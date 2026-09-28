import sys
import os
import threading
import subprocess
from queue import Queue
from datetime import datetime

# -------------------------------
# SETTINGS
# -------------------------------
# 1 = hidden / background (no console)
# 0 = visible console for each ping : )
hidden = 1

# Windows flags
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010

# Determine script directory (where targets.txt and ping.py reside)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Log file for background mode
LOG_FILE = os.path.join(SCRIPT_DIR, "many_ping.log")

# -------------------------------
# Function to run ping.py for a single target
# -------------------------------
def ping_target(target, isp_name, queue):
    ping_script = os.path.join(SCRIPT_DIR, "ping.py")

    # Ensure ping.py exists
    if not os.path.exists(ping_script):
        queue.put((target, f"Error: ping.py not found in {SCRIPT_DIR}"))
        return

    # Prepare command. -u: unbuffered child stdout so LOG_FILE fills in real
    # time in hidden mode instead of sitting in a block buffer for ages.
    cmd = [sys.executable, "-u", ping_script, target]
    if isp_name:
        cmd.append(isp_name)

    try:
        if hidden == 1:
            # Run fully detached in background, no console
            with open(LOG_FILE, "a") as log_file:
                subprocess.Popen(
                    cmd,
                    creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                    cwd=SCRIPT_DIR,
                    stdout=log_file,
                    stderr=subprocess.STDOUT
                )
            queue.put((target, "Started in background"))
        else:
            # Run in visible console
            process = subprocess.Popen(
                cmd,
                creationflags=CREATE_NEW_CONSOLE,
                cwd=SCRIPT_DIR
            )
            process.wait()
            if process.returncode == 0:
                queue.put((target, "OK"))
            else:
                queue.put((target, "Not OK"))
    except Exception as e:
        queue.put((target, f"Error: {str(e)}"))

# -------------------------------
# Read targets from file
# -------------------------------
def read_targets(file_name):
    path = os.path.join(SCRIPT_DIR, file_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{file_name} not found in {SCRIPT_DIR}")
    
    targets = []
    with open(path, 'r') as file:
        for line in file:
            line = line.split('#')[0].strip()  # Remove comments
            if line:
                targets.append(line)
    return targets

# -------------------------------
# Main function
# -------------------------------
def main():
    # Input file from argument or default
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    else:
        input_file = input("Please enter the input file name: ")

    # Optional ISP/link name (multi-link setups): tags this batch of targets'
    # alert email subjects so it's clear which ISP link they belong to.
    isp_name = sys.argv[2] if len(sys.argv) > 2 else ""

    try:
        targets = read_targets(input_file)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)

    queue = Queue()
    threads = []

    # Launch threads for each target
    for target in targets:
        thread = threading.Thread(target=ping_target, args=(target, isp_name, queue))
        thread.start()
        threads.append(thread)

    # Wait for threads to finish (only matters in visible console mode)
    for thread in threads:
        thread.join()

    # Collect results
    results = []
    while not queue.empty():
        results.append(queue.get())

    # Print/log results
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for target, status in results:
        print(f"{timestamp} - {target}: {status}")

    # Also append to log file
    with open(LOG_FILE, "a") as f:
        f.write(f"\nResults at {timestamp}:\n")
        for target, status in results:
            f.write(f"{target}: {status}\n")

if __name__ == "__main__":
    main()
