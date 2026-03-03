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

def generate_crontab():
    """Generate crontab file with environment variables from Railway"""
    log("Generating crontab with environment variables...")

    # Collect all environment variables needed for the cron job
    env_vars_to_export = [
        'SHELL',
        'PATH',
        'PGHOST',
        'PGDATABASE',
        'PGUSER',
        'PGPASSWORD',
        'PGPORT',
        'LUMA_API_KEY',
        'LUMA_CALENDAR_ID',
        'MAILCHIMP_API_KEY',
        'MAILCHIMP_AUDIENCE_ID',
        'MAILCHIMP_SERVER_PREFIX',
    ]

    crontab_lines = []

    # Add SHELL and PATH
    crontab_lines.append('SHELL=/bin/bash')
    crontab_lines.append('PATH=/usr/local/bin:/usr/bin:/bin')

    # Add all environment variables
    for var in env_vars_to_export:
        if var in ['SHELL', 'PATH']:
            continue  # Already added above

        value = os.getenv(var)
        if value:
            # Escape any special characters in the value
            escaped_value = value.replace('$', '\\$').replace('"', '\\"')
            crontab_lines.append(f'{var}="{escaped_value}"')

    # Add the cron schedule
    crontab_lines.append('0 */6 * * * cd /app && /bin/bash /app/run_luma_pipeline.sh >> /var/log/cron.log 2>&1')

    # Write to crontab file
    crontab_content = '\n'.join(crontab_lines) + '\n'

    try:
        with open('/etc/cron.d/analytics-cron', 'w') as f:
            f.write(crontab_content)

        # Set proper permissions
        os.chmod('/etc/cron.d/analytics-cron', 0o644)

        # Load the crontab
        subprocess.run(['crontab', '/etc/cron.d/analytics-cron'], check=True)

        log("✅ Crontab generated successfully")
        return True
    except Exception as e:
        log(f"❌ ERROR: Failed to generate crontab: {e}")
        return False

def main():
    log("=" * 50)
    log("Luma Event Sync & Analytics Cron Service Starting...")
    log("=" * 50)
    log(f"Current time: {datetime.now()}")
    log("Cron schedule: Every 6 hours (0 */6 * * *)")
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
        log("")
        log("Sleeping for 1 hour to keep container alive for debugging...")
        time.sleep(3600)
        sys.exit(1)

    log("✅ Environment variables configured")
    log("")

    # Generate crontab with environment variables
    if not generate_crontab():
        log("Sleeping for 1 hour to keep container alive for debugging...")
        time.sleep(3600)
        sys.exit(1)

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

    # Run initial pipeline
    log("=" * 50)
    log("Running Initial Luma Sync & Analytics Pipeline")
    log("=" * 50)

    try:
        result = subprocess.run(
            ['/bin/bash', '/app/run_luma_pipeline.sh'],
            capture_output=False,  # Show output in real-time
            text=True
        )

        log("")
        if result.returncode == 0:
            log("✅ Initial pipeline run completed successfully!")
        else:
            log(f"⚠️  Initial pipeline run failed with exit code: {result.returncode}")
            log("Check the output above for errors.")
    except Exception as e:
        log(f"⚠️  Initial pipeline run failed with exception: {e}")

    log("")
    log("=" * 50)
    log("Service is Running")
    log("=" * 50)
    log("Scheduled runs: Every 6 hours")
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
