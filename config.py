"""Shared configuration for ping.py, pathping_monitor.py and log_report.py.

Reads SMTP credentials and monitoring thresholds from a local .env file
(see .env.example) instead of hardcoding secrets in every script.
"""
import os

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _load_env_file(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_env_file(_ENV_PATH)

SMTP_SERVER = os.environ.get("SMTP_SERVER", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USERNAME)
EMAIL_TO = os.environ.get("EMAIL_TO", "")

# Shared thresholds so ping.py and pathping_monitor.py agree on what a "breach" is.
LOSS_THRESHOLD = float(os.environ.get("LOSS_THRESHOLD", "0.01"))  # 5% packet loss
LATENCY_THRESHOLD = float(os.environ.get("LATENCY_THRESHOLD", "100"))  # ms
JITTER_THRESHOLD = float(os.environ.get("JITTER_THRESHOLD", "30"))  # ms
MOS_THRESHOLD = float(os.environ.get("MOS_THRESHOLD", "3.5"))  # breach if MOS drops below this
MONITOR_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "300"))  # seconds

# Alert suppression: after SUPPRESS_STREAK consecutive cycles breaching the same
# metric (loss/latency/jitter/mos), a target stops emailing until it recovers.
SUPPRESS_STREAK = int(os.environ.get("SUPPRESS_STREAK", "3"))
# log_report.py is auto-triggered by the running monitors every this many hours,
# regardless of whether anything is currently failing.
REPORT_INTERVAL_HOURS = float(os.environ.get("REPORT_INTERVAL_HOURS", "24"))
