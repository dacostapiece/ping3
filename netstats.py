"""Shared helpers for ping.py, pathping_monitor.py and log_report.py.

Jitter/MOS math, target-list parsing, HTML email sending, the per-target alert
suppressor and the daily-report trigger in one place so they aren't duplicated
across scripts.
"""
import os
import subprocess
import sys
import smtplib
import socket
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

import config

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_computer_name():
    """Local machine name, for tagging email subjects when monitoring runs on multiple hosts."""
    return (
        os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
        or socket.gethostname()
    )


def subject_with_host(subject, isp=None):
    """Append this machine's name (and, for multi-link setups, the ISP name) to an
    email subject, e.g. 'Ping Alert 8.8.8.8 [Vivo Fibra] [DESKTOP-ABC123]'."""
    isp_tag = f" [{isp}]" if isp else ""
    return f"{subject}{isp_tag} [{get_computer_name()}]"

# Shared dark, email-safe color palette for pathping_monitor.py's alert
# emails and log_report.py's dashboard, so both render identically.
# Colors get inlined on every element rather than left in a <style> block
# because Outlook's desktop renderer ignores most non-inline CSS.
BG = "#0d1117"
CARD = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#f85149"
LINK = "#58a6ff"
HEAD_BG = "#21262d"
LOSS_BG = "#3d1418"
WARN_BG = "#3d2f05"
GOOD_BG = "#12301c"
MOS_COLOR = {
    "Excellent": "#3fb950", "Good": "#3fb950",
    "Fair": "#d29922", "Poor": "#f85149", "Bad": "#f85149",
}
FONT = "Segoe UI, Arial, sans-serif"

# (category, label, url) - documents the real-world standards and tools
# behind this project's numbers: netstats.calculate_jitter/calculate_mos,
# ping.py/pathping_monitor.py's use of ICMP and ping3, and config.py's
# threshold defaults.
REFERENCES = [
    ("Jitter", "RFC 3550 (RTP) - interarrival jitter formula used by calculate_jitter", "https://www.rfc-editor.org/rfc/rfc3550"),
    ("MOS", "ITU-T G.107 - the E-model calculate_mos approximates", "https://www.itu.int/rec/T-REC-G.107"),
    ("MOS", "ITU-T P.800 - Mean Opinion Score definition and scale", "https://www.itu.int/rec/T-REC-P.800"),
    ("ICMP", "RFC 792 - Internet Control Message Protocol (what ping/pathping send)", "https://www.rfc-editor.org/rfc/rfc792"),
    ("Tooling", "ping3 (PyPI) - library used by ping.py, many_ping.py, pathping_monitor.py", "https://pypi.org/project/ping3/"),
    ("Tooling", "Microsoft Learn - pathping command reference", "https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/pathping"),
    ("Thresholds", "Cisco - latency/jitter/loss guidance for voice quality (context for config.py defaults)", "https://www.cisco.com/c/en/us/support/docs/voice/voice-quality/18902-jitterintro.html"),
]


def render_references_footer():
    """Small dark-styled footer of standards/tooling links behind this project's metrics."""
    items = "".join(
        f'<li style="margin-bottom:6px;"><span style="color:{MUTED};">[{escape(cat)}]</span> '
        f'<a href="{url}" style="color:{LINK};" target="_blank" rel="noopener">{escape(label)}</a></li>'
        for cat, label, url in REFERENCES
    )
    return f"""<footer style="margin-top:32px; padding-top:16px; border-top:1px solid {BORDER};">
<p style="margin:0 0 8px; color:{MUTED}; font-size:11px; text-transform:uppercase; letter-spacing:.04em;">References</p>
<ul style="margin:0; padding:0; list-style:none; color:{TEXT}; font-size:12.5px;">{items}</ul>
</footer>"""


def calculate_jitter(latencies_ms):
    """Mean absolute difference between consecutive RTT samples (RFC 3550-style)."""
    if len(latencies_ms) < 2:
        return 0.0
    diffs = [abs(latencies_ms[i] - latencies_ms[i - 1]) for i in range(1, len(latencies_ms))]
    return sum(diffs) / len(diffs)


def calculate_mos(latency_ms, jitter_ms, loss_pct):
    """Simplified E-model approximation (not full ITU-T G.107).

    loss_pct is a percentage (0-100), not a fraction.
    """
    effective_latency = latency_ms + (jitter_ms * 2) + 10

    if effective_latency < 160:
        r_factor = 93.2 - (effective_latency / 40)
    else:
        r_factor = 93.2 - ((effective_latency - 120) / 10)

    r_factor -= loss_pct * 2.5
    r_factor = max(0.0, min(100.0, r_factor))

    mos = 1 + (0.035 * r_factor) + (7e-6 * r_factor * (r_factor - 60) * (100 - r_factor))
    return round(max(1.0, min(4.5, mos)), 2)


def mos_label(mos):
    if mos >= 4.0:
        return "Excellent"
    if mos >= 3.5:
        return "Good"
    if mos >= 3.0:
        return "Fair"
    if mos >= 2.0:
        return "Poor"
    return "Bad"


def read_targets(path):
    targets = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if line:
                targets.append(line)
    return targets


def resolve_targets(explicit_file):
    """Return a list of (target, isp_name) pairs to monitor.

    Used by pathping_monitor.py (what to monitor) and log_report.py (what to
    report on), so both match what bat_many_ping.bat hands to many_ping.py.
    An explicit targets_file argument always wins and is read as-is, no ISP
    tag (same as always). Otherwise this follows NUM_LINKS/ISPnn from .env
    (written by install_ping.bat): NUM_LINKS
    <= 1 keeps reading targets.txt; NUM_LINKS > 1 reads targets01.txt,
    targets02.txt, ... instead and tags each target with that link's ISP
    name so alert subjects show which ISP it breached on.
    """
    if explicit_file:
        path = os.path.join(SCRIPT_DIR, explicit_file)
        return [(t, None) for t in read_targets(path)]

    try:
        num_links = int(os.environ.get("NUM_LINKS", "1") or "1")
    except ValueError:
        num_links = 1
    if num_links <= 1:
        path = os.path.join(SCRIPT_DIR, "targets.txt")
        return [(t, os.environ.get("ISP01") or None) for t in read_targets(path)]

    pairs = []
    for link in range(1, num_links + 1):
        lnum2 = f"{link:02d}"
        path = os.path.join(SCRIPT_DIR, f"targets{lnum2}.txt")
        isp = os.environ.get(f"ISP{lnum2}") or None
        if not os.path.exists(path):
            print(f"Warning: {path} not found - skipping link {lnum2} ({isp or 'no ISP name'}).")
            continue
        pairs.extend((t, isp) for t in read_targets(path))
    return pairs


def breached_metric_keys(check):
    """Metric keys currently over threshold for a pathping-style ``check`` dict.

    Mirrors pathping_monitor.breach_reasons() but returns short stable keys
    ("loss", "latency", "jitter", "mos") for AlertSuppressor to streak on.
    """
    keys = []
    if (check["loss_pct"] / 100.0) > config.LOSS_THRESHOLD:
        keys.append("loss")
    if check["avg_latency_ms"] > config.LATENCY_THRESHOLD:
        keys.append("latency")
    if check["jitter_ms"] > config.JITTER_THRESHOLD:
        keys.append("jitter")
    if check["mos"] < config.MOS_THRESHOLD:
        keys.append("mos")
    return keys


class AlertSuppressor:
    """Per-target alert state machine shared by ping.py and pathping_monitor.py.

    Feed evaluate() the list of metric keys breaching this cycle. Once the SAME
    metric has breached ``streak_limit`` cycles in a row the target goes silent;
    the cycle that reaches the limit still alerts once (matching the old
    100%-loss behavior this generalizes). A fully clean cycle while silenced
    re-arms alerting and reports ``recovered`` so the caller can send one
    "back to normal" notice.
    """

    def __init__(self, name, streak_limit=3):
        self.name = name
        self.streak_limit = max(1, int(streak_limit))
        self._streaks = {}       # metric key -> consecutive breach count
        self.silenced = False

    def evaluate(self, breached_metrics):
        breached = set(breached_metrics or ())

        for metric in breached:
            self._streaks[metric] = self._streaks.get(metric, 0) + 1
        for metric in list(self._streaks):
            if metric not in breached:
                self._streaks[metric] = 0

        recovered = False
        if self.silenced and not breached:
            self.silenced = False
            self._streaks.clear()
            recovered = True

        # Decide the alert before this cycle can newly trip the silence, so the
        # cycle that first hits streak_limit still emits one last alert.
        should_alert = bool(breached) and not self.silenced

        newly_silenced = False
        if not self.silenced and any(c >= self.streak_limit for c in self._streaks.values()):
            self.silenced = True
            newly_silenced = True

        return {
            "should_alert": should_alert,
            "newly_silenced": newly_silenced,
            "recovered": recovered,
            "silenced": self.silenced,
            "streaks": dict(self._streaks),
        }


FIRST_REPORT_DELAY_S = 900  # 15 min grace period before the very first report


def maybe_run_daily_report(results_dir, interval_s=86400):
    """Spawn log_report.py at most once per ``interval_s``.

    Cadence is tracked by a ``.last_report`` stamp file in ``results_dir``, so
    calling this every monitor cycle (and from more than one monitor process)
    still yields one report per day. The stamp is rewritten before the spawn so
    a second caller in the same window backs off. Returns True if it launched.

    When the stamp doesn't exist yet (fresh install / fresh results dir), the
    first report waits FIRST_REPORT_DELAY_S instead of firing on the very next
    cycle, so it isn't built from a handful of seconds of data.
    """
    stamp = os.path.join(results_dir, ".last_report")
    now = time.time()
    try:
        last = os.path.getmtime(stamp)
    except OSError:
        # First run ever: seed the stamp on disk (mtime, not just this call's
        # local var) so the delay is measured against real elapsed time across
        # later calls, instead of recomputing "now - interval_s + delay" fresh
        # every cycle and never actually reaching the threshold.
        last = now - interval_s + FIRST_REPORT_DELAY_S
        try:
            with open(stamp, "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
            os.utime(stamp, (last, last))
        except OSError:
            pass
    if now - last < interval_s:
        return False

    try:
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except OSError:
        pass

    script = os.path.join(SCRIPT_DIR, "log_report.py")
    try:
        subprocess.Popen([sys.executable, script], cwd=SCRIPT_DIR)
        print(f"Daily report triggered ({script}).")
        return True
    except Exception as e:
        print(f"Failed to spawn daily report: {e}")
        return False


def send_html_email(to, subject, html_body, attachment_path=None):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html"))

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(attachment_path)}"'
        msg.attach(part)

    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        server.sendmail(config.EMAIL_FROM, to, msg.as_string())
