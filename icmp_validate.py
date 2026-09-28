"""icmp_validate.py - live diagnostic for validating ping3's ICMP handling
against a target where you're manually crafting specific ICMP error replies
(e.g. via a firewall's custom-packet testing feature).

For every ping attempt, prints exactly what ping3 saw: a real Echo Reply
(with latency), a timeout (no reply at all), or a specific ICMP error type/
code - so you can toggle the target's crafted response (Host/Net/Port
Unreachable, TTL Exceeded, etc.) and confirm it's classified correctly in
real time, using the same ping3.EXCEPTIONS=True + typed-exception handling
that ping.py and pathping_monitor.py use.

Usage: python icmp_validate.py <target> [interval_seconds]
"""
import sys
import time

import ping3
from ping3 import ping, errors as ping3_errors

ping3.EXCEPTIONS = True


def describe(err):
    icmp_header = getattr(err, "icmp_header", None)
    if icmp_header:
        return f"{type(err).__name__} - ICMP type={icmp_header['type']} code={icmp_header['code']} ({err})"
    return f"{type(err).__name__} ({err})"


def main():
    if len(sys.argv) < 2:
        print("Usage: python icmp_validate.py <target> [interval_seconds]")
        sys.exit(1)
    target = sys.argv[1]
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

    print(f"Pinging {target} every {interval}s - Ctrl+C to stop.\n")
    seq = 0
    try:
        while True:
            seq += 1
            ts = time.strftime("%H:%M:%S")
            try:
                latency = ping(target, timeout=1)
            except ping3_errors.Timeout:
                print(f"[{ts}] #{seq}: TIMEOUT - no reply at all")
            except ping3_errors.PingError as err:
                print(f"[{ts}] #{seq}: ICMP ERROR - {describe(err)}")
            except Exception as err:
                print(f"[{ts}] #{seq}: UNEXPECTED - {type(err).__name__}: {err}")
            else:
                print(f"[{ts}] #{seq}: OK - {latency * 1000:.1f}ms (real Echo Reply)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
