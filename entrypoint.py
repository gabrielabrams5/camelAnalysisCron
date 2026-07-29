#!/usr/bin/env python3
"""
Entrypoint script for the Railway cron service.

Railway starts this container on its native cron schedule (service settings ->
Cron Schedule) and expects it to run the job and EXIT. Runs the Luma pipeline
once, then exits with the pipeline's status code. Do not add keep-alive loops
here: a container that never exits shows up in Railway as a cron run stuck
"running" forever, and blocks subsequent scheduled runs.
"""
import os
import sys
import subprocess
from datetime import datetime

# Force unbuffered output
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)
sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)

# Hard ceiling on a single pipeline run
PIPELINE_TIMEOUT_SECONDS = 3 * 3600


def log(message):
    """Print with timestamp"""
    print(f"[{datetime.now().isoformat()}] {message}", flush=True)


def main():
    log("=" * 50)
    log("Luma Event Sync & Analytics Pipeline Run Starting...")
    log("=" * 50)
    log(f"Current time: {datetime.now()}")
    log("")

    # Check environment variables
    log("Environment Variables Check:")
    env_vars = {
        'PGHOST': os.getenv('PGHOST'),
        'PGDATABASE': os.getenv('PGDATABASE'),
        'PGUSER': os.getenv('PGUSER'),
        'PGPASSWORD': os.getenv('PGPASSWORD'),
        'PGPORT': os.getenv('PGPORT'),
        'LUMA_API_KEY': os.getenv('LUMA_API_KEY'),
    }

    missing = []
    for key, value in env_vars.items():
        if key in ['PGPASSWORD', 'LUMA_API_KEY']:
            log(f"  {key}: {'SET' if value else 'NOT_SET'}")
        else:
            log(f"  {key}: {value if value else 'NOT_SET'}")

        if not value:
            missing.append(key)

    log("")

    if missing:
        log(f"❌ ERROR: Missing required environment variables: {', '.join(missing)}")
        log("Please set these variables in Railway's Variables tab")
        return 1

    log("✅ Environment variables configured")
    log("")

    try:
        result = subprocess.run(
            ['/bin/bash', '/app/run_luma_pipeline.sh'],
            capture_output=False,  # Show output in real-time
            text=True,
            timeout=PIPELINE_TIMEOUT_SECONDS
        )

        log("")
        if result.returncode == 0:
            log("✅ Pipeline run completed successfully!")
        else:
            log(f"⚠️  Pipeline run failed with exit code: {result.returncode}")
            log("Check the output above for errors.")
        return result.returncode
    except subprocess.TimeoutExpired:
        log(f"⚠️  Pipeline run timed out after {PIPELINE_TIMEOUT_SECONDS // 3600} hours and was killed.")
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
