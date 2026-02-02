#!/usr/bin/env python3
"""
Entrypoint script for the cron service.
Handles startup, environment validation, and keeps container alive.
"""
import os
import sys
import subprocess
import time
from datetime import datetime

# Force unbuffered output
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)
sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)

# Print immediately to confirm Python is running
print("=" * 50, flush=True)
print("ENTRYPOINT STARTING - Python is running!", flush=True)
print("=" * 50, flush=True)

def log(message):
    """Print with timestamp"""
    print(f"[{datetime.now().isoformat()}] {message}", flush=True)

def main():
    log("=" * 50)
    log("Event Analytics Cron Service Starting...")
    log("=" * 50)
    log(f"Current time: {datetime.now()}")
    log("Cron schedule: Sundays at midnight UTC (0 0 * * 0)")
    log("")

    # Check environment variables
    log("Environment Variables Check:")
    env_vars = {
        'PGHOST': os.getenv('PGHOST'),
        'PGDATABASE': os.getenv('PGDATABASE'),
        'PGUSER': os.getenv('PGUSER'),
        'PGPASSWORD': os.getenv('PGPASSWORD'),
        'PGPORT': os.getenv('PGPORT'),
    }

    missing = []
    for key, value in env_vars.items():
        if key == 'PGPASSWORD':
            log(f"  {key}: {'SET' if value else 'NOT_SET'}")
        else:
            log(f"  {key}: {value if value else 'NOT_SET'}")

        if not value:
            missing.append(key)

    log("")

    if missing:
        log(f"❌ ERROR: Missing required environment variables: {', '.join(missing)}")
        log("Please set these variables in Railway's Variables tab")
        log("")
        log("Sleeping for 1 hour to keep container alive for debugging...")
        time.sleep(3600)
        sys.exit(1)

    log("✅ Environment variables configured")
    log("")

    # Start cron daemon
    log("Starting cron daemon...")
    try:
        subprocess.run(['cron'], check=True)
        time.sleep(2)  # Give cron time to start

        # Verify cron is running
        result = subprocess.run(['pgrep', 'cron'], capture_output=True)
        if result.returncode != 0:
            raise Exception("Cron process not found")

        log("✅ Cron daemon started successfully")
    except Exception as e:
        log(f"❌ ERROR: Failed to start cron daemon: {e}")
        log("Sleeping for 1 hour to keep container alive for debugging...")
        time.sleep(3600)
        sys.exit(1)

    log("")

    # Show crontab
    log("Crontab contents:")
    try:
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        log(result.stdout)
    except Exception as e:
        log(f"Could not read crontab: {e}")

    log("")

    # Run initial analysis
    log("=" * 50)
    log("Running Initial Analysis")
    log("=" * 50)

    try:
        result = subprocess.run(
            ['python', '/app/analyze.py', '--outdir', '/app/analysis_outputs'],
            capture_output=False,  # Show output in real-time
            text=True
        )

        log("")
        if result.returncode == 0:
            log("✅ Initial analysis completed successfully!")
        else:
            log(f"⚠️  Initial analysis failed with exit code: {result.returncode}")
            log("Check the output above for errors.")
    except Exception as e:
        log(f"⚠️  Initial analysis failed with exception: {e}")

    log("")
    log("=" * 50)
    log("Service is Running")
    log("=" * 50)
    log("Scheduled runs: Sundays at midnight UTC")
    log("Volume mount: /app/analysis_outputs")
    log("")
    log("Cron logs will appear below:")
    log("-" * 50)

    # Tail the log file to keep container alive
    try:
        subprocess.run(['tail', '-f', '/var/log/cron.log'])
    except KeyboardInterrupt:
        log("Shutting down...")
        sys.exit(0)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        log("Sleeping for 1 hour to allow debugging...")
        time.sleep(3600)
        sys.exit(1)
