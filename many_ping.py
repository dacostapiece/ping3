import os
import sys
import threading
import subprocess
from queue import Queue
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREATE_NEW_CONSOLE = 0x00000010
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Spawn a persistent ping.py monitor for target in its own visible console.
def ping_target(target, isp_name, queue):
    ping_script = os.path.join(SCRIPT_DIR, "ping.py")

    if not os.path.exists(ping_script):
        queue.put((target, f"Error: ping.py not found in {SCRIPT_DIR}"))
        return

    # -u: unbuffered child stdout so a hidden run's log fills in real time.
    cmd = [sys.executable, "-u", ping_script, target]
    if isp_name:
        cmd.append(isp_name)

    try:
        subprocess.Popen(
            cmd,
            creationflags=CREATE_NEW_CONSOLE | CREATE_BREAKAWAY_FROM_JOB,
            cwd=SCRIPT_DIR
        )
        queue.put((target, "Started"))
    except Exception as e:
        queue.put((target, f"Error: {str(e)}"))

def read_targets(file_name):
    with open(file_name, 'r') as file:
        targets = []
        for line in file:
            # Remove comments and strip whitespace
            line = line.split('#')[0].strip()
            if line:  # Only add non-empty lines
                targets.append(line)
    return targets

def main():
    # Check if file name is provided as an argument
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    else:
        input_file = input("Please enter the input file name: ")

    # Optional ISP/link name (multi-link setups): tags this batch of targets'
    # alert email subjects so it's clear which ISP link they belong to.
    isp_name = sys.argv[2] if len(sys.argv) > 2 else ""

    try:
        targets = read_targets(input_file)
    except FileNotFoundError:
        print(f"File {input_file} not found.")
        sys.exit(1)

    queue = Queue()
    threads = []

    # Create and start threads for each target
    for target in targets:
        if target:  # Ensure the target is not empty after stripping comments
            thread = threading.Thread(target=ping_target, args=(target, isp_name, queue))
            thread.start()
            threads.append(thread)

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    # Collect results from the queue
    results = []
    while not queue.empty():
        results.append(queue.get())

    # Determine whether every monitor process launched
    all_started = all(result[1] == "Started" for result in results)

    # Print results to the console
    for target, status in results:
        print(f"{target} {status}")

    if all_started:
        print("All target monitors were started.")
    else:
        print("Some target monitors failed to start.")

    # Write results to the output file
    with open('many_ping.txt', 'a') as output_file:
        output_file.write("\n\n")
        output_file.write(f"Results at {datetime.now()}:\n")
        for target, status in results:
            output_file.write(f"{target} {status}\n")

if __name__ == "__main__":
    main()
