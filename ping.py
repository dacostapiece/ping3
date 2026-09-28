import time
import smtplib
from email.mime.text import MIMEText
import ping3
from ping3 import ping, errors as ping3_errors
from threading import Timer, Event, Thread, Lock
import os
import argparse
import signal
import sys

import config
import netstats

# Without this, ping3 swallows a real ICMP error reply (Destination
# Unreachable, TTL Expired, etc.) and ping() just returns False for it -
# indistinguishable from a successful 0ms reply once misused as a number
# (False * 1000 == 0). EXCEPTIONS=True makes it raise a typed exception
# instead, so ping_target() below can tell "got an ICMP error" apart from
# "got a real Echo Reply" instead of miscounting the former as success.
ping3.EXCEPTIONS = True

# Email configuration (loaded from .env via config.py - see .env.example)
SMTP_SERVER = config.SMTP_SERVER
SMTP_PORT = config.SMTP_PORT
SMTP_USERNAME = config.SMTP_USERNAME
SMTP_PASSWORD = config.SMTP_PASSWORD
EMAIL_FROM = config.EMAIL_FROM
EMAIL_TO = config.EMAIL_TO
EMAIL_SUBJECT = 'Ping Alert '

# Determine the home directory and results folder
HOME_DIR = os.getcwd()
RESULTS_DIR = os.path.join(HOME_DIR, "results")

# Create the results directory if it doesn't exist
if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

# Monitoring configuration
PING_INTERVAL = 1  # Interval between pings in seconds
MONITOR_INTERVAL = config.MONITOR_INTERVAL  # Interval between checks in seconds
LOSS_THRESHOLD = config.LOSS_THRESHOLD  # fraction, e.g. 0.05 = 5% packet loss
LATENCY_THRESHOLD = config.LATENCY_THRESHOLD  # ms
LOG_FILE = os.path.join(RESULTS_DIR,'ping_')
LOG_FILE_ENDING = ".txt"
LOG_INTERVAL = 3600  # Log to file every hour
MAX_LOG_SIZE = 100 * 1024 * 1024  # 100MB

# Shared state, written by ping_target() and consumed by monitor() (every
# MONITOR_INTERVAL) and log_to_file() (every LOG_INTERVAL) on independent
# schedules. state_lock guards all of it - without it, log_to_file()'s
# periodic ping_results.clear() can race with monitor() reading it for an
# alert email, landing an empty "Last 20 probes" section on a real breach.
#
# interval_* is consumed and reset by monitor(); hourly_* is consumed and
# reset by log_to_file(). They're kept separate (rather than one shared set
# of counters) because each used to reset the same globals on its own timer:
# monitor()'s 5-minute reset was wiping the data log_to_file() needed for a
# true hourly summary, so results/ping_<target>.txt's "Estatisticas" only
# ever reflected the last few minutes, not a full hour.
state_lock = Lock()
interval_latencies = []
interval_packet_loss_count = 0
interval_total_ping_count = 0
hourly_latencies = []
hourly_packet_loss_count = 0
hourly_total_ping_count = 0
ping_results = []
stop_event = Event()

def send_email(email_to, subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = email_to

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, email_to, msg.as_string())
        print('Email sent successfully.')
    except Exception as e:
        print(f'Failed to send email: {e}')

def rotate_log_file(PING_ADDRESS):
    """Rotate the log file if it exceeds the maximum size."""
    NEW_LOG_FILE_NAME = LOG_FILE+PING_ADDRESS+LOG_FILE_ENDING
    if os.path.exists(NEW_LOG_FILE_NAME) and os.path.getsize(NEW_LOG_FILE_NAME) > MAX_LOG_SIZE:
        os.rename(NEW_LOG_FILE_NAME, NEW_LOG_FILE_NAME + '.bak')
        open(NEW_LOG_FILE_NAME, 'w').close()  # Create a new log file

def log_to_file(PING_ADDRESS):
    global hourly_latencies, hourly_packet_loss_count, hourly_total_ping_count

    NEW_LOG_FILE_NAME = LOG_FILE+PING_ADDRESS+LOG_FILE_ENDING
    while not stop_event.is_set():
        rotate_log_file(PING_ADDRESS)  # Rotate log file if necessary

        # Snapshot and reset this consumer's own accumulators under the lock,
        # then do the (slower) file I/O outside it.
        with state_lock:
            results_snapshot = list(ping_results)
            ping_results.clear()
            packet_loss_count = hourly_packet_loss_count
            total_ping_count = hourly_total_ping_count
            latencies_snapshot = hourly_latencies
            hourly_latencies = []
            hourly_packet_loss_count = 0
            hourly_total_ping_count = 0

        with open(NEW_LOG_FILE_NAME, 'a') as log:
            log.write(f'\n=== {time.strftime("%Y-%m-%d %H:%M:%S")} ===\n')

            # Write all ping results
            for result in results_snapshot:
                log.write(result + '\n')

            # Simulate Ctrl+Break statistics
            packet_loss = packet_loss_count / total_ping_count if total_ping_count else 0
            avg_latency = sum(latencies_snapshot) / len(latencies_snapshot) if latencies_snapshot else 0
            summary = (f'\nEstatísticas do Ping para {PING_ADDRESS}:\n'
                       f'    Pacotes: Enviados = {total_ping_count}, Recebidos = {total_ping_count - packet_loss_count}, Perdidos = {packet_loss_count} ({packet_loss * 100:.2f}% de perda)\n')
            if latencies_snapshot:
                summary += (f'Aproximar um número redondo de vezes em milissegundos:\n'
                            f'    Mínimo = {min(latencies_snapshot):.2f}ms, Máximo = {max(latencies_snapshot):.2f}ms, Média = {avg_latency:.2f}ms\n')
            log.write(summary)

        time.sleep(LOG_INTERVAL)

def monitor(email_to):
    global interval_latencies, interval_packet_loss_count, interval_total_ping_count

    sup = netstats.AlertSuppressor(PING_ADDRESS, config.SUPPRESS_STREAK)
    report_interval_s = config.REPORT_INTERVAL_HOURS * 3600

    while not stop_event.is_set():
        # Snapshot and reset this consumer's own accumulators under the lock
        # (see the comment above their declaration) - log_to_file() has its
        # own separate hourly_* accumulators, so resetting these here can't
        # wipe data it still needs.
        with state_lock:
            packet_loss_count = interval_packet_loss_count
            total_ping_count = interval_total_ping_count
            latencies_snapshot = interval_latencies
            recent_probes = ping_results[-20:]
            interval_latencies = []
            interval_packet_loss_count = 0
            interval_total_ping_count = 0

        # Calculate packet loss and average latency
        packet_loss = packet_loss_count / total_ping_count if total_ping_count else 0
        avg_latency = sum(latencies_snapshot) / len(latencies_snapshot) if latencies_snapshot else 0
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        # Which metrics are over threshold this cycle (ping.py has no jitter/MOS).
        breached = []
        if total_ping_count > 0 and packet_loss > LOSS_THRESHOLD:
            breached.append("loss")
        if avg_latency > LATENCY_THRESHOLD:
            breached.append("latency")

        res = sup.evaluate(breached)

        if res["should_alert"]:
            subject = netstats.subject_with_host(EMAIL_SUBJECT + PING_ADDRESS, isp=ISP_NAME or None)
            body = (f'Packet loss: {packet_loss * 100:.2f}%\n'
                    f'Average latency: {avg_latency:.2f} ms\n'
                    f'Breached: {", ".join(breached)}\n'
                    f'Time: {timestamp}\n\n'
                    'Last 20 probes:\n' + '\n'.join(recent_probes))
            send_email(email_to, subject, body)
            print(f'Alert! Threshold exceeded ({", ".join(breached)}):\n{body}')
        elif breached and res["silenced"]:
            print(f'[{PING_ADDRESS}] still breaching ({", ".join(breached)}) - suppressed, testing silently.')

        if res["newly_silenced"]:
            print(f'[{PING_ADDRESS}] same metric breaching {config.SUPPRESS_STREAK} cycles straight '
                  f'- going silent until it recovers.')

        if res["recovered"]:
            subject = netstats.subject_with_host(EMAIL_SUBJECT + PING_ADDRESS + " RECOVERED", isp=ISP_NAME or None)
            body = (f'{PING_ADDRESS} is back within thresholds.\n'
                    f'Packet loss: {packet_loss * 100:.2f}%\n'
                    f'Average latency: {avg_latency:.2f} ms\n'
                    f'Time: {timestamp}\n')
            send_email(email_to, subject, body)
            print(f'[{PING_ADDRESS}] recovered - alerting re-armed.')

        # Daily report heartbeat - fires whether or not anything is failing.
        netstats.maybe_run_daily_report(RESULTS_DIR, report_interval_s)

        # Wait for next monitoring interval or stop event
        stop_event.wait(MONITOR_INTERVAL)

def ping_target(ping_address):
    global interval_latencies, interval_packet_loss_count, interval_total_ping_count
    global hourly_latencies, hourly_packet_loss_count, hourly_total_ping_count

    while not stop_event.is_set():
        try:
            latency = ping(ping_address, timeout=1)
        except ping3_errors.Timeout:
            msg = f'Resposta de {ping_address}: Tempo esgotado.'
            with state_lock:
                interval_packet_loss_count += 1
                hourly_packet_loss_count += 1
                ping_results.append(msg)
            print(msg)
        except ping3_errors.PingError as e:
            # A real ICMP error reply (Destination Unreachable, TTL expired,
            # etc.), not a successful Echo Reply - count as loss instead of
            # miscounting it as a fake 0ms success (see ping3.EXCEPTIONS above).
            msg = f'Resposta de {ping_address}: erro ICMP ({e}).'
            with state_lock:
                interval_packet_loss_count += 1
                hourly_packet_loss_count += 1
                ping_results.append(msg)
            print(msg)
        except Exception as e:
            msg = f'Resposta de {ping_address}: Erro no ping ({e}).'
            with state_lock:
                interval_packet_loss_count += 1
                hourly_packet_loss_count += 1
                ping_results.append(msg)
            print(msg)
        else:
            latency_ms = latency * 1000  # Convert to ms
            msg = f'Resposta de {ping_address}: bytes=32 tempo={latency_ms:.2f}ms TTL=116'
            with state_lock:
                interval_latencies.append(latency_ms)
                hourly_latencies.append(latency_ms)
                ping_results.append(msg)
            print(msg)

        with state_lock:
            interval_total_ping_count += 1
            hourly_total_ping_count += 1
        time.sleep(PING_INTERVAL)

def signal_handler(sig, frame):
    print('Stopping script...')
    stop_event.set()

if __name__ == '__main__':
    # Setup argument parser
    parser = argparse.ArgumentParser(description="Ping Monitoring Script")
    parser.add_argument("ping_address", nargs='?', type=str, help="The IP address to ping")
    parser.add_argument("isp_name", nargs='?', type=str, default="",
                         help="ISP/link name for this target's group (optional, tags alert email subjects)")
    args = parser.parse_args()

    if args.ping_address:
        PING_ADDRESS = args.ping_address
    else:
        PING_ADDRESS = input("Please enter the target address (e.g., 8.8.8.8): ")

    ISP_NAME = args.isp_name or ""

    # Request user input for email address
    #email_to = input("Please enter the recipient's email address: ")
    email_to = config.EMAIL_TO

    # Setup signal handler for graceful termination
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start monitoring, logging, and pinging
    print("Starting ping monitoring...")
    Timer(MONITOR_INTERVAL, monitor, [email_to]).start()
    
    # Start the log writing in a separate thread
    log_thread = Thread(target=log_to_file, args=(PING_ADDRESS,))
    log_thread.daemon = True
    log_thread.start()

    ping_target(PING_ADDRESS)
