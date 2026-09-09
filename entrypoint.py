#!/usr/bin/env python3
"""
Entrypoint script for the Railway cron service.

Railway starts this container on its native cron schedule (service settings ->
Cron Schedule) and expects it to run the job and EXIT. Runs the job once, then
exits with its status code. Do not add keep-alive loops here: a container that
never exits shows up in Railway as a cron run stuck "running" forever, and
blocks subsequent scheduled runs.

Modes (PIPELINE_MODE env var):
  full          (default) run the whole pipeline via run_luma_pipeline.sh
  auto_approve  run ONLY luma/auto_approve_rsvps.py. Meant for a second Railway
                cron service on this same repo with an hourly schedule, so that
                RSVPs arriving on the day of an event get approved instead of
                waiting for the nightly full run (which fires after the event
                has already started).
"""
import os
import sys
import subprocess
from datetime import datetime

# Force unbuffered output
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)
sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)

MODES = {
    # mode: (command, timeout in seconds)
    'full': (['/bin/bash', '/app/run_luma_pipeline.sh'], 3 * 3600),
    'auto_approve': (['python3', '/app/luma/auto_approve_rsvps.py'], 20 * 60),
}

REQUIRED_ENV = ['PGHOST', 'PGDATABASE', 'PGUSER', 'PGPASSWORD', 'PGPORT',
                'LUMA_API_KEY', 'LUMA_CALENDAR_ID']
SECRET_ENV = {'PGPASSWORD', 'LUMA_API_KEY'}


def log(message):
    """Print with timestamp"""
    print(f"[{datetime.now().isoformat()}] {message}", flush=True)


def main():
    mode = (os.getenv('PIPELINE_MODE') or 'full').strip().lower()

    log("=" * 50)
    log(f"Luma Event Sync & Analytics - run starting (mode: {mode})")
    log("=" * 50)
    log(f"Current time: {datetime.now()}")
    log("")

    if mode not in MODES:
        log(f"❌ ERROR: Unknown PIPELINE_MODE '{mode}'. Valid values: {', '.join(MODES)}")
        return 1

    # Check environment variables
    log("Environment Variables Check:")
    missing = []
    for key in REQUIRED_ENV:
        value = os.getenv(key)
        if key in SECRET_ENV:
            log(f"  {key}: {'SET' if value else 'NOT_SET'}")
        else:
            log(f"  {key}: {value if value else 'NOT_SET'}")
        if not value:
            missing.append(key)

    mailchimp_ready = bool(os.getenv('MAILCHIMP_API_KEY') and os.getenv('MAILCHIMP_AUDIENCE_ID'))
    log(f"  MAILCHIMP_*: {'SET' if mailchimp_ready else 'NOT_SET (Mailchimp steps will be skipped)'}")
    log("")

    if missing:
        log(f"❌ ERROR: Missing required environment variables: {', '.join(missing)}")
        log("Please set these variables in Railway's Variables tab")
        return 1

    log("✅ Environment variables configured")
    log("")

    command, timeout_seconds = MODES[mode]
    try:
        result = subprocess.run(
            command,
            capture_output=False,  # Show output in real-time
            text=True,
            timeout=timeout_seconds
        )

        log("")
        if result.returncode == 0:
            log("✅ Run completed successfully!")
        else:
            log(f"⚠️  Run failed with exit code: {result.returncode}")
            log("Check the output above for errors.")
        return result.returncode
    except subprocess.TimeoutExpired:
        log(f"⚠️  Run timed out after {timeout_seconds // 60} minutes and was killed.")
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
