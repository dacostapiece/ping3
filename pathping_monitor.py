"""Threshold-triggered pathping wrapper.

For each target in targets.txt (or targetsNN.txt per link when NUM_LINKS>1
in .env - see netstats.resolve_targets), runs a quick ping3 burst on an interval to
check loss/latency/jitter/MOS against the same thresholds ping.py uses. Only
when the final target itself breaches a threshold does this shell out to
`pathping` for a hop-by-hop snapshot, log it, and email an HTML report -
clean checks are discarded so results/ doesn't fill up with noise.
"""
import argparse
import json
import locale
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from html import escape
from threading import Event, Thread

import ping3
from ping3 import ping, errors as ping3_errors

import config
import netstats

# Without this, ping3 swallows a real ICMP error reply (Destination
# Unreachable, TTL Expired, etc.) and ping() just returns False for it -
# indistinguishable from a successful 0ms reply once misused as a number
# (False * 1000 == 0). EXCEPTIONS=True makes it raise a typed exception
# instead, so quick_check() below can tell "got an ICMP error" apart from
# "got a real Echo Reply" instead of miscounting the former as success.
ping3.EXCEPTIONS = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

BURST_SIZE = 20  # pings per quick check
BURST_INTERVAL = 0.2  # seconds between pings in the burst
# Safety cap on the pathping subprocess. Worst case (fully unresponsive
# destination, so pathping can't tell when it's "arrived" and walks the
# default max of 30 hops): ~90s discovery (30 hops x default 3s timeout per
# unanswered TTL) + ~150s query phase (30 hops x our -q 20 x default 250ms
# spacing) = ~240s. Set above that so a fully dark target still gets a
# complete 30-hop trace instead of being killed with no output - there's
# headroom under MONITOR_INTERVAL (default 300s) since each target runs in
# its own thread.
PATHPING_TIMEOUT = 280  # seconds
HOP_JUMP_MS = 30  # RTT delta from the previous hop that flags a hop as suspect

MONITOR_INTERVAL = config.MONITOR_INTERVAL
LOSS_THRESHOLD = config.LOSS_THRESHOLD
LATENCY_THRESHOLD = config.LATENCY_THRESHOLD
JITTER_THRESHOLD = config.JITTER_THRESHOLD
MOS_THRESHOLD = config.MOS_THRESHOLD
EMAIL_TO = config.EMAIL_TO
EMAIL_SUBJECT = "Pathping Alert "

stop_event = Event()

# Matches a pathping hop data row, e.g.:
#   1   18ms     0/ 100 =  0%     0/ 100 =  0%  10.35.60.1
# RTT is "---" instead of a number on hops with 100% loss - matched as an
# unnumbered alternative so those hops still get parsed instead of vanishing.
# Column shape is locale-independent (works whether headers say
# "Hop/Address" or "Salto/Endereço").
HOP_DATA_RE = re.compile(
    r"^\s*(\d+)\s+(?:(\d+)\s*ms|-+)\s+(\d+)/\s*(\d+)\s*=\s*(\d+)%\s+"
    r"(\d+)/\s*(\d+)\s*=\s*(\d+)%\s+(\S.*)$"
)
# Matches the hop-0 row, which has no timing columns yet, e.g.:
#   0                                           my-pc [192.168.0.10]
HOP_ADDR_ONLY_RE = re.compile(r"^\s*(\d+)\s+(\S.*)$")


def quick_check(target):
    """Run a short ping3 burst and return loss/latency/jitter/MOS for target."""
    latencies = []
    lost = 0
    icmp_errors = Counter()  # e.g. {"Destination unreachable: Host unreachable.": 3}
    for _ in range(BURST_SIZE):
        if stop_event.is_set():
            break
        try:
            latency = ping(target, timeout=1)
        except ping3_errors.Timeout:
            lost += 1
        except ping3_errors.PingError as err:
            # A real ICMP error reply (Destination Unreachable, TTL expired,
            # etc.), not a successful Echo Reply. Without EXCEPTIONS=True this
            # would come back as ping()'s bare False, which silently becomes
            # a fake 0ms "success" once multiplied - see the comment above
            # ping3.EXCEPTIONS = True.
            lost += 1
            icmp_errors[str(err)] += 1
        except Exception:
            lost += 1
        else:
            latencies.append(latency * 1000)
        time.sleep(BURST_INTERVAL)

    sent = len(latencies) + lost
    loss_pct = (lost / sent * 100) if sent else 100.0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    jitter = netstats.calculate_jitter(latencies)
    mos = netstats.calculate_mos(avg_latency, jitter, loss_pct)

    return {
        "sent": sent,
        "lost": lost,
        "loss_pct": loss_pct,
        "avg_latency_ms": avg_latency,
        "jitter_ms": jitter,
        "mos": mos,
        "mos_label": netstats.mos_label(mos),
        "icmp_errors": dict(icmp_errors),
    }


def breach_reasons(check):
    """Return a list of human-readable reasons check crossed a threshold (empty if clean)."""
    reasons = []
    if (check["loss_pct"] / 100.0) > LOSS_THRESHOLD:
        icmp_errors = check.get("icmp_errors")
        detail = (
            f' (ICMP errors received: {"; ".join(f"{n}x {msg}" for msg, n in icmp_errors.items())})'
            if icmp_errors else ""
        )
        reasons.append(f'loss {check["loss_pct"]:.1f}% > {LOSS_THRESHOLD * 100:.0f}%{detail}')
    if check["avg_latency_ms"] > LATENCY_THRESHOLD:
        reasons.append(f'latency {check["avg_latency_ms"]:.1f}ms > {LATENCY_THRESHOLD:.0f}ms')
    if check["jitter_ms"] > JITTER_THRESHOLD:
        reasons.append(f'jitter {check["jitter_ms"]:.1f}ms > {JITTER_THRESHOLD:.0f}ms')
    if check["mos"] < MOS_THRESHOLD:
        reasons.append(f'MOS {check["mos"]} < {MOS_THRESHOLD}')
    return reasons


def run_pathping(target):
    """Run pathping in fast mode (-n -q 20) and return decoded stdout, or None."""
    cmd = ["pathping", "-n", "-q", "20", target]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=PATHPING_TIMEOUT)
    except Exception as e:
        print(f"[{target}] pathping failed to run: {e}")
        return None

    encoding = locale.getpreferredencoding(False) or "cp850"
    try:
        return result.stdout.decode(encoding, errors="replace")
    except LookupError:
        return result.stdout.decode("utf-8", errors="replace")


def parse_pathping(output):
    """Parse pathping stdout into an ordered list of hop dicts.

    pathping prints the route twice: a bare tracert-style hop list first,
    then the real statistics table (hops 0..N again, this time with
    RTT/loss). Seeing hop 0 a second time marks the start of that table,
    so the first (data-less) pass is discarded in favor of it.
    """
    hops = []
    if not output:
        return hops

    seen_hops = set()
    for line in output.splitlines():
        m = HOP_DATA_RE.match(line)
        if m:
            hop_num = int(m.group(1))
            entry = {
                "hop": hop_num,
                "rtt_ms": int(m.group(2)) if m.group(2) is not None else None,
                "src_loss_pct": int(m.group(5)),
                "link_loss_pct": int(m.group(8)),
                "address": m.group(9).strip(),
            }
        else:
            m2 = HOP_ADDR_ONLY_RE.match(line)
            if not (m2 and "ms" not in line and "=" not in line):
                continue
            hop_num = int(m2.group(1))
            entry = {
                "hop": hop_num,
                "rtt_ms": None,
                "src_loss_pct": 0,
                "link_loss_pct": 0,
                "address": m2.group(2).strip(),
            }

        if hop_num == 0 and hops:
            hops = []
            seen_hops = set()

        if hop_num in seen_hops:
            continue
        hops.append(entry)
        seen_hops.add(hop_num)

    hops.sort(key=lambda h: h["hop"])
    return hops


def flag_suspects(hops):
    """Mutate hops with a 'suspect_reasons' list; return the flagged subset."""
    suspects = []
    prev_rtt = None
    for h in hops:
        reasons = []
        if h["rtt_ms"] is not None:
            if prev_rtt is not None:
                delta = h["rtt_ms"] - prev_rtt
                if delta >= HOP_JUMP_MS:
                    reasons.append(f"RTT jumped +{delta}ms vs previous hop")
            prev_rtt = h["rtt_ms"]
        if h["link_loss_pct"] > 0:
            reasons.append(f"{h['link_loss_pct']}% packet loss on this hop")
        h["suspect_reasons"] = reasons
        if reasons:
            suspects.append(h)
    return suspects


# Dark, email-safe palette shared with log_report.py (see netstats.py).
# Colors are inlined on every element (rather than in a <style> block)
# because Outlook's desktop renderer ignores most CSS that isn't inline,
# and bgcolor attributes are added alongside background styles for the
# same reason.
_BG = netstats.BG
_CARD = netstats.CARD
_BORDER = netstats.BORDER
_TEXT = netstats.TEXT
_MUTED = netstats.MUTED
_ACCENT = netstats.ACCENT
_HEAD_BG = netstats.HEAD_BG
_LOSS_BG = netstats.LOSS_BG
_WARN_BG = netstats.WARN_BG
_GOOD_BG = netstats.GOOD_BG
_MOS_COLOR = netstats.MOS_COLOR
_FONT = netstats.FONT

# Shown when pathping's trace reaches the target directly (its last hop's
# address is the target itself) but shows it clean, even though the quick
# check that triggered this run breached thresholds. A target that silently
# drops/deprioritizes its own ICMP replies (rate limiting, firewall policy,
# etc.) can look like a perfectly healthy path here - there's no hop beyond it
# left for pathping to pin the loss on. Expected pathping behavior, not a bug
# in this monitor, so the destination is called out explicitly instead of
# reporting a false all-clear.
DESTINATION_DENIAL_NOTE = (
    "quick check breached thresholds but pathping shows no loss on any hop - "
    "since this is the destination (the last hop), it is most likely dropping "
    "or deprioritizing ping traffic itself rather than a mid-path link failing"
)

# Shown when the target never shows up in the pathping trace at all - the
# last hop pathping printed responded fine, but nothing beyond it (including
# the target) ever replies. That last responding hop is NOT the failure - it's
# the last known-good point on the path - so a synthetic row for the target
# itself is appended and marked as the actual failure instead.
TARGET_UNREACHABLE_NOTE = (
    "target never appeared in the pathping trace - the path goes dark after "
    "the last hop shown, so the destination itself is most likely dropping "
    "all ICMP traffic (or is otherwise unreachable) rather than the last "
    "responding hop being at fault"
)


def render_html(target, check, hops, suspects, timestamp, reasons=None, note=None):
    """note: DESTINATION_DENIAL_NOTE or TARGET_UNREACHABLE_NOTE when the last
    hop in ``hops`` (the real destination or the appended target marker) is
    to be forced red and called out in a banner, regardless of its wording.
    """
    # Boundary hop: the last clean hop right before the first flagged one, so
    # the report visually shows where the path was still fine before failing.
    first_bad_hop = next((h["hop"] for h in hops if h.get("suspect_reasons")), None)
    last_good_hop = None
    if first_bad_hop is not None:
        for h in hops:
            if h["hop"] >= first_bad_hop:
                break
            last_good_hop = h["hop"]

    rows = []
    for h in hops:
        hop_reasons = h.get("suspect_reasons", [])
        is_last = bool(hops) and h["hop"] == hops[-1]["hop"]
        is_loss = any("loss" in r for r in hop_reasons) or (note and is_last)
        if is_loss:
            row_bg = _LOSS_BG
        elif hop_reasons:
            row_bg = _WARN_BG
        elif h["hop"] == last_good_hop:
            row_bg = _GOOD_BG
        else:
            row_bg = _CARD
        rtt_display = f'{h["rtt_ms"]}ms' if h["rtt_ms"] is not None else "-"
        cell = f'style="padding:6px 8px; border-bottom:1px solid {_BORDER}; color:{_TEXT};"'
        rows.append(
            f'<tr style="background-color:{row_bg};" bgcolor="{row_bg}">'
            f'<td {cell}>{h["hop"]}</td><td {cell}>{rtt_display}</td>'
            f'<td {cell}>{h["src_loss_pct"]}%</td><td {cell}>{h["link_loss_pct"]}%</td>'
            f'<td {cell}>{escape(h["address"])}</td>'
            f'<td {cell}>{escape("; ".join(hop_reasons))}</td></tr>'
        )

    if suspects:
        suspect_list = "".join(
            f'<li style="margin-bottom:4px;">Hop {h["hop"]} ({escape(h["address"])}): '
            f'{escape("; ".join(h["suspect_reasons"]))}</li>'
            for h in suspects
        )
    else:
        suspect_list = '<li>No single hop stood out - the path looks evenly degraded.</li>'

    reasons_html = (
        f'<p style="margin:0 0 16px; color:{_TEXT}; font-size:13px;">'
        f'<b>Triggered by:</b> {escape("; ".join(reasons))}</p>'
        if reasons else ""
    )
    if note:
        reasons_html += (
            f'<p style="margin:0 0 16px; padding:10px 12px; background-color:{_LOSS_BG}; '
            f'border-radius:6px; color:{_TEXT}; font-size:13px;">'
            f'<b>Note:</b> {escape(note)}.</p>'
        )

    mos_color = _MOS_COLOR.get(check["mos_label"], _TEXT)
    stat_label = f'style="padding:4px 12px 4px 0; color:{_MUTED}; font-size:13px;"'
    stat_value = f'style="padding:4px 0; color:{_TEXT}; font-size:13px; font-weight:600;"'
    icmp_errors = check.get("icmp_errors") or {}
    icmp_errors_row = (
        f'<tr><td {stat_label}>ICMP errors seen</td><td {stat_value}>'
        f'{escape("; ".join(f"{n}x {msg}" for msg, n in icmp_errors.items()))}</td></tr>'
        if icmp_errors else ""
    )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>Pathping alert - {escape(target)}</title>
</head>
<body style="margin:0; padding:0; background-color:{_BG};" bgcolor="{_BG}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{_BG};" bgcolor="{_BG}">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px; width:100%; background-color:{_CARD}; border:1px solid {_BORDER}; border-radius:8px;" bgcolor="{_CARD}">
<tr><td style="padding:24px 28px; font-family:{_FONT};">

<h2 style="margin:0 0 4px; color:{_ACCENT}; font-size:20px;">Pathping alert &mdash; {escape(target)}</h2>
<p style="margin:0 0 16px; color:{_MUTED}; font-size:13px;">Time: {escape(timestamp)}</p>
{reasons_html}

<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse; margin:0 0 20px;">
<tr><td {stat_label}>Packet loss</td><td {stat_value}>{check["loss_pct"]:.1f}%</td></tr>
<tr><td {stat_label}>Avg latency</td><td {stat_value}>{check["avg_latency_ms"]:.1f} ms</td></tr>
<tr><td {stat_label}>Jitter</td><td {stat_value}>{check["jitter_ms"]:.1f} ms</td></tr>
<tr><td {stat_label}>MOS</td><td {stat_value}><span style="color:{mos_color};">{check["mos"]} ({check["mos_label"]})</span></td></tr>
{icmp_errors_row}
</table>

<h3 style="margin:20px 0 8px; color:{_TEXT}; font-size:15px; border-bottom:1px solid {_BORDER}; padding-bottom:6px;">Suspect hop(s)</h3>
<ul style="margin:0 0 20px; padding-left:20px; color:{_TEXT}; font-size:13px;">{suspect_list}</ul>

<h3 style="margin:20px 0 8px; color:{_TEXT}; font-size:15px; border-bottom:1px solid {_BORDER}; padding-bottom:6px;">Hop-by-hop detail</h3>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse; font-size:13px;">
<tr style="background-color:{_HEAD_BG};" bgcolor="{_HEAD_BG}">
<th style="text-align:left; padding:8px; border-bottom:1px solid {_BORDER}; color:{_MUTED}; font-weight:600;">Hop</th>
<th style="text-align:left; padding:8px; border-bottom:1px solid {_BORDER}; color:{_MUTED}; font-weight:600;">RTT</th>
<th style="text-align:left; padding:8px; border-bottom:1px solid {_BORDER}; color:{_MUTED}; font-weight:600;">Loss to here</th>
<th style="text-align:left; padding:8px; border-bottom:1px solid {_BORDER}; color:{_MUTED}; font-weight:600;">Loss this hop</th>
<th style="text-align:left; padding:8px; border-bottom:1px solid {_BORDER}; color:{_MUTED}; font-weight:600;">Address</th>
<th style="text-align:left; padding:8px; border-bottom:1px solid {_BORDER}; color:{_MUTED}; font-weight:600;">Notes</th>
</tr>
{''.join(rows)}
</table>

{netstats.render_references_footer()}

</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def monitor_target(target, isp_name=None):
    sup = netstats.AlertSuppressor(target, config.SUPPRESS_STREAK)
    report_interval_s = config.REPORT_INTERVAL_HOURS * 3600

    while not stop_event.is_set():
        check = quick_check(target)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        # reasons = human-readable text for the email; res streaks on metric keys.
        reasons = breach_reasons(check)
        res = sup.evaluate(netstats.breached_metric_keys(check))

        if res["should_alert"]:
            print(f'[{target}] threshold breached ({"; ".join(reasons)}) - running pathping snapshot')
            output = run_pathping(target)
            hops = parse_pathping(output)
            suspects = flag_suspects(hops)

            # Did pathping's trace actually reach the target, or does it just
            # go dark after some earlier hop? These are two different
            # failures and need different reports - see the two NOTE
            # constants above.
            reached_target = bool(hops) and hops[-1]["address"] == target
            destination_denial = bool(reasons) and reached_target and not suspects
            target_unreachable = bool(reasons) and not reached_target

            note = None
            if destination_denial:
                # pathping reached the target directly but shows it clean -
                # the destination itself is denying/deprioritizing traffic.
                hops[-1]["suspect_reasons"] = hops[-1].get("suspect_reasons", []) + [DESTINATION_DENIAL_NOTE]
                suspects = [hops[-1]]
                note = DESTINATION_DENIAL_NOTE
            elif target_unreachable:
                # The trace never got to the target at all - don't blame the
                # last hop that DID respond, append a marker row for the
                # target instead and let it stand out as the real failure.
                marker_hop = (hops[-1]["hop"] + 1) if hops else 0
                target_hop = {
                    "hop": marker_hop,
                    "rtt_ms": None,
                    "src_loss_pct": 100,
                    "link_loss_pct": 100,
                    "address": f"* {target}",
                    "suspect_reasons": ["(target)"],
                }
                hops.append(target_hop)
                suspects = suspects + [target_hop]
                note = TARGET_UNREACHABLE_NOTE

            file_ts = time.strftime("%Y%m%d_%H%M%S")
            html = render_html(target, check, hops, suspects, timestamp, reasons, note)

            html_path = os.path.join(RESULTS_DIR, f"pathping_{target}_{file_ts}.html")
            json_path = os.path.join(RESULTS_DIR, f"pathping_{target}_{file_ts}.json")

            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)

            snapshot = {
                "target": target,
                "timestamp": timestamp,
                "check": check,
                "breach_reasons": reasons,
                "hops": hops,
                "suspects": [
                    {"hop": h["hop"], "address": h["address"], "reasons": h["suspect_reasons"]}
                    for h in suspects
                ],
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)

            try:
                subject = netstats.subject_with_host(EMAIL_SUBJECT + target, isp=isp_name)
                netstats.send_html_email(EMAIL_TO, subject, html, attachment_path=html_path)
                print(f"[{target}] alert email sent.")
            except Exception as e:
                print(f"[{target}] failed to send email: {e}")
        elif reasons and res["silenced"]:
            print(f'[{target}] still breaching ({"; ".join(reasons)}) - suppressed, testing silently.')
        else:
            print(
                f'[{target}] check OK - loss={check["loss_pct"]:.1f}% '
                f'avg={check["avg_latency_ms"]:.1f}ms jitter={check["jitter_ms"]:.1f}ms mos={check["mos"]}'
            )

        if res["newly_silenced"]:
            print(f'[{target}] same metric breaching {config.SUPPRESS_STREAK} cycles straight '
                  f'- going silent until it recovers.')

        if res["recovered"]:
            try:
                subject = netstats.subject_with_host(EMAIL_SUBJECT + target + " RECOVERED", isp=isp_name)
                body = (
                    f'<p style="font-family:{netstats.FONT}; color:{netstats.TEXT}; '
                    f'background:{netstats.BG}; padding:16px;">'
                    f'{escape(target)} is back within thresholds as of {escape(timestamp)}.<br>'
                    f'loss={check["loss_pct"]:.1f}% &nbsp; avg={check["avg_latency_ms"]:.1f}ms &nbsp; '
                    f'jitter={check["jitter_ms"]:.1f}ms &nbsp; mos={check["mos"]} ({escape(check["mos_label"])})'
                    f'</p>'
                )
                netstats.send_html_email(EMAIL_TO, subject, body)
                print(f"[{target}] recovered - alerting re-armed.")
            except Exception as e:
                print(f"[{target}] failed to send recovery email: {e}")

        # Daily report heartbeat - fires whether or not anything is failing.
        netstats.maybe_run_daily_report(RESULTS_DIR, report_interval_s)

        stop_event.wait(MONITOR_INTERVAL)


def signal_handler(sig, frame):
    print("Stopping pathping monitor...")
    stop_event.set()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Threshold-triggered pathping monitor")
    parser.add_argument("target", nargs="?", type=str, help="Single target to monitor")
    parser.add_argument(
        "targets_file", nargs="?", type=str, default=None,
        help="Targets file to use when no single target is given. Default: targets.txt, or "
             "targetsNN.txt per link when NUM_LINKS>1 in .env (see install_ping.bat).",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.target:
        targets = [(args.target, None)]
    else:
        try:
            targets = netstats.resolve_targets(args.targets_file)
        except FileNotFoundError as e:
            print(e)
            sys.exit(1)
        if not targets:
            print("No targets found - check targets.txt / targetsNN.txt and NUM_LINKS in .env.")
            sys.exit(1)

    print(f"Starting pathping monitor for {len(targets)} target(s):")
    for t, isp in targets:
        print(f"  - {t}{f' [{isp}]' if isp else ''}")
    threads = [Thread(target=monitor_target, args=(t, isp), daemon=True) for t, isp in targets]
    for th in threads:
        th.start()

    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
