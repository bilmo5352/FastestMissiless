#!/usr/bin/env python3
"""
Production restart wrapper for FastestMissiless.
Handles adaptive crash recovery, memory monitoring, and resource cleanup.
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

# Configuration
RESTART_INTERVAL_MINUTES = int(os.getenv("RESTART_INTERVAL_MINUTES", "15"))
RESTART_INTERVAL_SECONDS = RESTART_INTERVAL_MINUTES * 60
STARTUP_GRACE_PERIOD = 60
MEMORY_CHECK_INTERVAL = 60
MEMORY_THRESHOLD = int(os.getenv("MEMORY_THRESHOLD", "75"))
ORPHAN_CLEANUP_INTERVAL = 300  # 5 minutes

PROCESS = None
consecutive_fast_failures = 0
last_orphan_cleanup = 0

def get_memory_usage():
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
        logging.warning("psutil not installed")
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
            logging.warning("Force killing process...")
            PROCESS.kill()
            PROCESS.wait()
            logging.info("Process killed")
    
    time.sleep(2)  # Let OS cleanup
    
    # Kill orphaned browsers
    try:
        subprocess.run(["pkill", "-9", "chromium"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-9", "chrome"], stderr=subprocess.DEVNULL)
        logging.info("Cleaned up orphaned browsers")
    except Exception as e:
        logging.warning(f"Could not kill browsers: {e}")

def signal_handler(sig, frame):
    logging.info("Received termination signal")
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
    
    while True:
        start_time = time.time()
        
        logging.info("=" * 80)
        logging.info(f"Cycle #{cycle} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"Command: {' '.join(cmd)}")
        logging.info(f"Restart interval: {RESTART_INTERVAL_MINUTES} minutes")
        logging.info(f"Memory: {get_memory_usage():.1f}%")
        logging.info("=" * 80)
        
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
            
            # Check if process exited
            if PROCESS.poll() is not None:
                exit_code = PROCESS.returncode
                
                if elapsed < STARTUP_GRACE_PERIOD:
                    consecutive_fast_failures += 1
                    logging.error(f"Fast failure #{consecutive_fast_failures} (exit {exit_code}, {elapsed:.1f}s)")
                    
                    # Exponential backoff
                    backoff = min(60, 10 * (2 ** (consecutive_fast_failures - 1)))
                    logging.info(f"Backing off for {backoff}s")
                    time.sleep(backoff)
                else:
                    consecutive_fast_failures = 0
                    logging.info(f"Process exited {exit_code} after {elapsed:.1f}s")
                    time.sleep(5)
                
                break
            
            current_time = time.time()
            
            # Memory check
            if current_time - last_memory_check > MEMORY_CHECK_INTERVAL:
                mem = get_memory_usage()
                logging.info(f"Memory: {mem:.1f}% at {elapsed/60:.1f}min")
                last_memory_check = current_time
                
                if mem > MEMORY_THRESHOLD:
                    logging.warning(f"Memory {mem:.1f}% > {MEMORY_THRESHOLD}%, restarting early")
                    cleanup_process()
                    break
            
            # Periodic orphan cleanup
            if current_time - last_orphan_cleanup > ORPHAN_CLEANUP_INTERVAL:
                logging.info("Running periodic orphan cleanup")
                try:
                    subprocess.run(["pkill", "-9", "chromium"], stderr=subprocess.DEVNULL)
                    subprocess.run(["pkill", "-9", "chrome"], stderr=subprocess.DEVNULL)
                except:
                    pass
                last_orphan_cleanup = current_time
            
            # Timeout check
            if elapsed > RESTART_INTERVAL_SECONDS:
                logging.info(f"Reached {RESTART_INTERVAL_MINUTES}min timeout")
                cleanup_process()
                break
            
            time.sleep(5)
        
        if elapsed >= STARTUP_GRACE_PERIOD:
            consecutive_fast_failures = 0
        
        logging.info(f"Cycle #{cycle} complete. Restarting in 5s...")
        time.sleep(5)
        cycle += 1

if __name__ == "__main__":
    main()
