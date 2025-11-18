#!/usr/bin/env python3
"""
Enhanced restart wrapper for FastestMissiless.
Handles adaptive crash backoff, frequent memory checks,
incremental browser cleanup, and smart restart intervals.
"""

import subprocess
import sys
import signal
import time
import logging
import os
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RESTART-WRAPPER] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Config from environment with defaults
RESTART_INTERVAL_MINUTES = int(os.getenv("RESTART_INTERVAL_MINUTES", "15"))
RESTART_INTERVAL_SECONDS = RESTART_INTERVAL_MINUTES * 60
STARTUP_GRACE_PERIOD = 60  # Time in sec to consider crash 'fast failure'
MEMORY_CHECK_INTERVAL = 60  # Check memory every 60 seconds
MEMORY_THRESHOLD_PERCENT = int(os.getenv("MEMORY_THRESHOLD", "80"))
ORPHAN_CLEANUP_INTERVAL = 300  # 5 min interval to clean orphan browsers

PROCESS = None
consecutive_fast_failures = 0
last_orphan_cleanup = 0

def get_memory_usage():
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
        logging.warning("psutil not installed, memory monitoring disabled")
        return 0

def cleanup_process():
    global PROCESS
    if PROCESS and PROCESS.poll() is None:
        logging.info("Terminating final.py process...")
        PROCESS.terminate()
        try:
            PROCESS.wait(timeout=15)
            logging.info("Process terminated gracefully")
        except subprocess.TimeoutExpired:
            logging.warning("Process did not terminate after timeout, killing...")
            PROCESS.kill()
            PROCESS.wait()
            logging.info("Process killed")
    time.sleep(2)  # Wait for OS cleanup

    # Kill orphaned browser processes incrementally
    try:
        subprocess.run(["pkill", "-9", "chromium"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-9", "chrome"], stderr=subprocess.DEVNULL)
        logging.info("Cleaned up orphaned browser processes")
    except Exception as e:
        logging.warning(f"Could not kill browser processes: {e}")

def signal_handler(sig, frame):
    logging.info("Received termination signal, shutting down...")
    cleanup_process()
    sys.exit(0)

def main():
    global PROCESS, consecutive_fast_failures, last_orphan_cleanup

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    cmd = sys.argv[1:] if len(sys.argv) > 1 else [
        "python", "final.py", "--db", "--batch-size", "100"
    ]

    cycle = 1
    last_memory_check = 0
    start_time = 0

    while True:
        start_time = time.time()
        logging.info("="*80)
        logging.info(f"Starting cycle #{cycle} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"Command: {' '.join(cmd)}")
        logging.info(f"Will restart after {RESTART_INTERVAL_MINUTES} minutes")
        logging.info(f"Initial memory usage: {get_memory_usage():.1f}%")
        logging.info("="*80)

        env = os.environ.copy()
        env.update({
            'OPENBLAS_NUM_THREADS': '4',
            'OMP_NUM_THREADS': '4',
            'MKL_NUM_THREADS': '4',
            'NUMEXPR_NUM_THREADS': '4',
        })

        PROCESS = subprocess.Popen(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
            bufsize=1,
            universal_newlines=True,
            env=env
        )

        while True:
            elapsed = time.time() - start_time

            if PROCESS.poll() is not None:
                exit_code = PROCESS.returncode
                if elapsed < STARTUP_GRACE_PERIOD:
                    consecutive_fast_failures += 1
                    logging.error(f"Process crashed during startup, exit code {exit_code}, failure #{consecutive_fast_failures}")
                    backoff = min(60, 10 * (2 ** (consecutive_fast_failures - 1)))  # Exponential backoff: 10s, 20s, 40s, max 60s
                    logging.info(f"Sleeping for {backoff} seconds before restart")
                    time.sleep(backoff)
                else:
                    consecutive_fast_failures = 0
                    logging.info(f"Process exited with code {exit_code} after {elapsed:.1f} seconds. Restarting shortly.")
                    time.sleep(5)
                break

            current_time = time.time()

            # Periodic memory check
            if current_time - last_memory_check > MEMORY_CHECK_INTERVAL:
                mem = get_memory_usage()
                logging.info(f"Memory usage: {mem:.1f}% at {elapsed/60:.1f} min elapsed")
                last_memory_check = current_time
                if mem > MEMORY_THRESHOLD_PERCENT:
                    logging.warning(f"Memory usage exceeded threshold ({mem:.1f}%). Restarting early.")
                    cleanup_process()
                    break

            # Periodic orphan cleanup
            if current_time - last_orphan_cleanup > ORPHAN_CLEANUP_INTERVAL:
                logging.info("Running periodic orphan browser cleanup.")
                try:
                    subprocess.run(["pkill", "-9", "chromium"], stderr=subprocess.DEVNULL)
                    subprocess.run(["pkill", "-9", "chrome"], stderr=subprocess.DEVNULL)
                    logging.info("Orphaned browsers cleaned up")
                except Exception as e:
                    logging.warning(f"Failed to clean orphans: {e}")
                last_orphan_cleanup = current_time

            # Restart after timeout
            if elapsed > RESTART_INTERVAL_SECONDS:
                logging.info(f"Reached {RESTART_INTERVAL_MINUTES} minutes timeout (elapsed: {elapsed / 60:.1f} min)")
                cleanup_process()
                break

            time.sleep(5)

        logging.info(f"Cycle #{cycle} complete. Restarting in 5 seconds...")
        time.sleep(5)
        cycle += 1

if __name__ == "__main__":
    main()
