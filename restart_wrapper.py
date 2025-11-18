#!/usr/bin/env python3
"""
Wrapper script that runs final.py and automatically restarts every 15 minutes.
This ensures Railway restarts the service even if final.py silently stops.
"""
import subprocess
import sys
import signal
import time
import logging
import os
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RESTART-WRAPPER] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Configuration from environment
RESTART_INTERVAL_SECONDS = int(os.getenv("RESTART_INTERVAL_MINUTES", "15")) * 60
STARTUP_GRACE_PERIOD = 60  # Allow 60 seconds for imports
PROCESS = None
consecutive_fast_failures = 0

def get_memory_usage():
    """Get current memory usage percentage."""
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
        logging.warning("psutil not installed, cannot monitor memory")
        return 0

def signal_handler(sig, frame):
    """Handle termination signals gracefully."""
    logging.info("Received termination signal, shutting down...")
    cleanup_process()
    sys.exit(0)

def cleanup_process():
    """Terminate and cleanup the subprocess."""
    global PROCESS
    if PROCESS and PROCESS.poll() is None:
        logging.info("Terminating final.py process...")
        PROCESS.terminate()
        try:
            PROCESS.wait(timeout=15)
            logging.info("Process terminated gracefully")
        except subprocess.TimeoutExpired:
            logging.warning("Process didn't terminate, killing...")
            PROCESS.kill()
            PROCESS.wait()
            logging.info("Process killed")
    
    # Kill any orphaned browser processes
    try:
        subprocess.run(["pkill", "-9", "chromium"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-9", "chrome"], stderr=subprocess.DEVNULL)
        logging.info("Cleaned up orphaned browser processes")
    except Exception as e:
        logging.warning(f"Could not kill browser processes: {e}")

def main():
    """Main wrapper function."""
    global PROCESS, consecutive_fast_failures
    
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Get command to run
    cmd = sys.argv[1:] if len(sys.argv) > 1 else [
        "python", "final.py", "--db", "--batch-size", "100"
    ]
    
    cycle = 1
    
    while True:
        start_time = time.time()
        
        logging.info("=" * 80)
        logging.info(f"Starting cycle #{cycle} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"Command: {' '.join(cmd)}")
        logging.info(f"Will restart after {RESTART_INTERVAL_SECONDS / 60:.0f} minutes")
        logging.info(f"Memory usage: {get_memory_usage():.1f}%")
        logging.info("=" * 80)
        
        try:
            # Set environment variables for this process
            env = os.environ.copy()
            env.update({
                'OPENBLAS_NUM_THREADS': '4',
                'OMP_NUM_THREADS': '4',
                'MKL_NUM_THREADS': '4',
                'NUMEXPR_NUM_THREADS': '4',
            })
            
            # Start the process
            PROCESS = subprocess.Popen(
                cmd,
                stdout=sys.stdout,
                stderr=sys.stderr,
                bufsize=1,
                universal_newlines=True,
                env=env
            )
            
            # Monitor the process
            elapsed = 0
            last_log_time = 0
            
            while elapsed < RESTART_INTERVAL_SECONDS:
                # Check if process is still running
                if PROCESS.poll() is not None:
                    # Process exited
                    exit_code = PROCESS.returncode
                    elapsed = time.time() - start_time
                    
                    # Check if this was a fast failure (within startup grace period)
                    if elapsed < STARTUP_GRACE_PERIOD:
                        consecutive_fast_failures += 1
                        logging.error(
                            f"Process crashed during startup with code {exit_code} "
                            f"after {elapsed:.1f} seconds "
                            f"(failure #{consecutive_fast_failures})"
                        )
                        
                        # If too many fast failures, wait longer before retry
                        if consecutive_fast_failures >= 3:
                            wait_time = min(60, consecutive_fast_failures * 10)
                            logging.warning(
                                f"Multiple fast failures detected. "
                                f"Waiting {wait_time} seconds before retry..."
                            )
                            time.sleep(wait_time)
                        else:
                            time.sleep(5)
                    else:
                        # Process ran for a while before exiting
                        consecutive_fast_failures = 0
                        logging.info(
                            f"Process exited with code {exit_code} "
                            f"after {elapsed:.1f} seconds"
                        )
                        time.sleep(5)
                    
                    break
                
                # Sleep in small increments
                time.sleep(5)
                elapsed = time.time() - start_time
                
                # Log progress every 5 minutes
                if elapsed - last_log_time >= 300:
                    remaining = RESTART_INTERVAL_SECONDS - elapsed
                    mem = get_memory_usage()
                    logging.info(
                        f"Running... {elapsed/60:.1f} min elapsed, "
                        f"{remaining/60:.1f} min until restart, "
                        f"memory: {mem:.1f}%"
                    )
                    last_log_time = elapsed
                    
                    # Check memory threshold
                    if mem > 85:
                        logging.warning(
                            f"Memory usage high ({mem:.1f}%), "
                            "triggering early restart..."
                        )
                        break
            
            # If we reached the timeout, terminate the process
            if PROCESS.poll() is None:
                elapsed = time.time() - start_time
                logging.info(
                    f"Reached {RESTART_INTERVAL_SECONDS / 60:.0f} minute timeout "
                    f"(elapsed: {elapsed/60:.1f} min)"
                )
                cleanup_process()
            
            # Reset consecutive failures if we ran successfully
            if elapsed >= STARTUP_GRACE_PERIOD:
                consecutive_fast_failures = 0
            
            # Log cycle completion
            logging.info(f"Cycle #{cycle} completed. Restarting in 5 seconds...")
            time.sleep(5)
            cycle += 1
            
        except KeyboardInterrupt:
            logging.info("Received keyboard interrupt, shutting down...")
            cleanup_process()
            sys.exit(0)
            
        except Exception as e:
            logging.error(f"Error in wrapper: {e}", exc_info=True)
            cleanup_process()
            logging.info("Restarting in 10 seconds...")
            time.sleep(10)
            cycle += 1

if __name__ == "__main__":
    main()