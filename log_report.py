"""Standalone log ingestion + HTML dashboard.

Scans a results/ directory for ping.py's hourly-summary .txt logs and
pathping_monitor.py's structured .json incident snapshots, and renders one
self-contained HTML report. Doesn't need either monitor script to be running
- it only reads files already on disk.
"""
import argparse
import glob
import json
import os
import re
import time
from html import escape

import config
import netstats

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EMAIL_SUBJECT = "Ping Monitoring Report"

TIMESTAMP_RE = re.compile(r"=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ===")
STATS_RE = re.compile(
    r"Pacotes: Enviados = (\d+), Recebidos = (\d+), Perdidos = (\d+) \(([\d.]+)% de perda\)"
)
LATENCY_RE = re.compile(
    r"M[ií]nimo = ([\d.]+)ms, M[aá]ximo = ([\d.]+)ms, M[ée]dia = ([\d.]+)ms"
)


def parse_ping_log(path):
    """Parse a ping.py hourly-summary log into a list of timestamped blocks.

    Relies on the '=== <timestamp> ===' marker ping.py writes before each
    hourly summary - files written before that change won't have blocks.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    blocks = []
    parts = TIMESTAMP_RE.split(content)
    for i in range(1, len(parts), 2):
        timestamp = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        stats_m = STATS_RE.search(body)
        if not stats_m:
            continue
        entry = {
            "timestamp": timestamp,
            "sent": int(stats_m.group(1)),
            "received": int(stats_m.group(2)),
            "lost": int(stats_m.group(3)),
            "loss_pct": float(stats_m.group(4)),
        }
        lat_m = LATENCY_RE.search(body)
        if lat_m:
            entry["min_ms"] = float(lat_m.group(1))
            entry["max_ms"] = float(lat_m.group(2))
            entry["avg_ms"] = float(lat_m.group(3))
        blocks.append(entry)
    return blocks


def collect_data(results_dir, only_targets=None):
    """Gather ping blocks and pathping incidents per target from results_dir.

    only_targets (a set) limits the report to those targets, so logs left
    behind by targets since removed from targets*.txt don't show up.
    None means include every log found.
    """
    targets_data = {}

    for path in glob.glob(os.path.join(results_dir, "ping_*.txt")):
        basename = os.path.basename(path)
        target = basename[len("ping_"):-len(".txt")]
        if only_targets is not None and target not in only_targets:
            continue
        targets_data.setdefault(target, {"blocks": [], "incidents": []})
        targets_data[target]["blocks"].extend(parse_ping_log(path))

    for path in glob.glob(os.path.join(results_dir, "pathping_*.json")):
        with open(path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        target = snapshot.get("target", "unknown")
        if only_targets is not None and target not in only_targets:
            continue
        targets_data.setdefault(target, {"blocks": [], "incidents": []})
        targets_data[target]["incidents"].append(snapshot)

    for data in targets_data.values():
        data["blocks"].sort(key=lambda b: b["timestamp"])
        data["incidents"].sort(key=lambda s: s.get("timestamp", ""))

    return targets_data


def render_svg_chart(blocks, width=600, height=160, pad=30):
    values = [b["avg_ms"] for b in blocks if "avg_ms" in b]
    if not values:
        return f'<p style="color:{netstats.MUTED};"><i>No latency history yet.</i></p>'

    min_v, max_v = min(values), max(values)
    if max_v == min_v:
        max_v = min_v + 1
    n = len(values)

    def x(i):
        return pad + (i / max(n - 1, 1)) * (width - 2 * pad)

    def y(v):
        return height - pad - ((v - min_v) / (max_v - min_v)) * (height - 2 * pad)

    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="background:{netstats.CARD};border:1px solid {netstats.BORDER};border-radius:6px;">'
        f'<polyline fill="none" stroke="{netstats.LINK}" stroke-width="2" points="{points}" />'
        f'<text x="4" y="14" font-size="11" fill="{netstats.MUTED}">{max_v:.0f}ms</text>'
        f'<text x="4" y="{height - 6}" font-size="11" fill="{netstats.MUTED}">{min_v:.0f}ms</text>'
        f"</svg>"
    )


def render_target_section(target, data):
    blocks = data["blocks"]
    incidents = data["incidents"]

    total_sent = sum(b["sent"] for b in blocks)
    total_lost = sum(b["lost"] for b in blocks)
    overall_loss_pct = (total_lost / total_sent * 100) if total_sent else 0.0
    avg_values = [b["avg_ms"] for b in blocks if "avg_ms" in b]
    overall_avg = sum(avg_values) / len(avg_values) if avg_values else 0.0

    cell = f'style="padding:6px 8px; border-bottom:1px solid {netstats.BORDER}; color:{netstats.TEXT};"'
    head_cell = (
        f'style="text-align:left; padding:8px; border-bottom:1px solid {netstats.BORDER}; '
        f'color:{netstats.MUTED}; font-weight:600;"'
    )

    incident_rows = []
    for inc in incidents:
        check = inc.get("check", {})
        suspects = inc.get("suspects", [])
        suspect_text = "; ".join(f'hop {s["hop"]} ({s["address"]})' for s in suspects) or "none flagged"
        incident_rows.append(
            f'<tr><td {cell}>{escape(inc.get("timestamp", ""))}</td>'
            f'<td {cell}>{check.get("loss_pct", 0):.1f}%</td>'
            f'<td {cell}>{check.get("avg_latency_ms", 0):.1f}ms</td>'
            f'<td {cell}>{check.get("jitter_ms", 0):.1f}ms</td>'
            f'<td {cell}>{check.get("mos", "-")}</td>'
            f"<td {cell}>{escape(suspect_text)}</td></tr>"
        )
    incidents_table = (
        f'<table cellpadding="0" cellspacing="0" style="border-collapse:collapse; width:100%;">'
        f'<tr style="background-color:{netstats.HEAD_BG};" bgcolor="{netstats.HEAD_BG}">'
        f'<th {head_cell}>Time</th><th {head_cell}>Loss</th><th {head_cell}>Avg latency</th>'
        f"<th {head_cell}>Jitter</th><th {head_cell}>MOS</th><th {head_cell}>Suspect hop(s)</th></tr>"
        + "".join(incident_rows) + "</table>"
    ) if incident_rows else f'<p style="color:{netstats.MUTED};"><i>No pathping incidents recorded.</i></p>'

    stat_label = f'style="padding:4px 12px 4px 0; color:{netstats.MUTED}; font-size:13px;"'
    stat_value = f'style="padding:4px 0; color:{netstats.TEXT}; font-size:13px; font-weight:600;"'

    return f"""
<section style="margin-bottom:40px; padding:20px 24px; background-color:{netstats.CARD}; border:1px solid {netstats.BORDER}; border-radius:8px;">
<h2 style="margin:0 0 12px; color:{netstats.ACCENT}; font-size:18px;">{escape(target)}</h2>
<table cellpadding="0" cellspacing="0" style="border-collapse:collapse; margin:0 0 16px;">
<tr><td {stat_label}>Hourly summaries parsed</td><td {stat_value}>{len(blocks)}</td></tr>
<tr><td {stat_label}>Overall packet loss</td><td {stat_value}>{overall_loss_pct:.2f}%</td></tr>
<tr><td {stat_label}>Overall avg latency</td><td {stat_value}>{overall_avg:.1f} ms</td></tr>
<tr><td {stat_label}>Pathping incidents</td><td {stat_value}>{len(incidents)}</td></tr>
</table>
<h3 style="margin:16px 0 8px; color:{netstats.TEXT}; font-size:14px;">Latency over time</h3>
{render_svg_chart(blocks)}
<h3 style="margin:16px 0 8px; color:{netstats.TEXT}; font-size:14px;">Incidents</h3>
{incidents_table}
</section>"""


def render_report(targets_data):
    sections = "".join(render_target_section(t, d) for t, d in sorted(targets_data.items()))
    generated = time.strftime("%Y-%m-%d %H:%M:%S")
    no_data = f'<p style="color:{netstats.MUTED};"><i>No log files found.</i></p>'
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>Ping monitoring report</title>
</head>
<body style="font-family:{netstats.FONT}; max-width:900px; margin:20px auto; padding:0 16px; background-color:{netstats.BG}; color:{netstats.TEXT};">
<h1 style="color:{netstats.ACCENT};">Ping monitoring report</h1>
<p style="color:{netstats.MUTED};">Generated {generated}</p>
{sections if sections else no_data}
{netstats.render_references_footer()}
</body></html>"""


def main():
    parser = argparse.ArgumentParser(description="Build an HTML report from ping/pathping log files")
    parser.add_argument("--results-dir", default=os.path.join(SCRIPT_DIR, "results"))
    parser.add_argument("--output", default=None)
    parser.add_argument("--no-email", action="store_true", help="Skip emailing the report - just write it to disk")
    parser.add_argument("--all", action="store_true",
                        help="Include every log in results-dir, not just targets in the active targets*.txt")
    args = parser.parse_args()

    output = args.output or os.path.join(args.results_dir, "report.html")

    only_targets = None
    if not args.all:
        only_targets = {t for t, _ in netstats.resolve_targets(None)}
        if not only_targets:
            print("No active targets found (check targets*.txt / NUM_LINKS) - reporting on every log instead.")
            only_targets = None

    targets_data = collect_data(args.results_dir, only_targets)
    html = render_report(targets_data)

    out_dir = os.path.dirname(output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Report written to {output}")

    if not args.no_email:
        try:
            subject = netstats.subject_with_host(EMAIL_SUBJECT)
            netstats.send_html_email(config.EMAIL_TO, subject, html, attachment_path=output)
            print(f"Report emailed to {config.EMAIL_TO}")
        except Exception as e:
            print(f"Failed to email report: {e}")


if __name__ == "__main__":
    main()
