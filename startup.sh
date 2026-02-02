#!/bin/bash

echo "========================================="
echo "Event Analytics Cron Service Starting..."
echo "========================================="
echo "Current time: $(date)"
echo "Cron schedule: Sundays at midnight UTC (0 0 * * 0)"
echo ""

# Print environment check (without exposing full secrets)
echo "Environment Variables Check:"
echo "  PGHOST: ${PGHOST:-NOT_SET}"
echo "  PGDATABASE: ${PGDATABASE:-NOT_SET}"
echo "  PGUSER: ${PGUSER:-NOT_SET}"
echo "  PGPASSWORD: ${PGPASSWORD:+SET}"
echo "  PGPORT: ${PGPORT:-NOT_SET}"
echo ""

# Check if critical env vars are set
if [ -z "$PGHOST" ] || [ -z "$PGDATABASE" ] || [ -z "$PGUSER" ] || [ -z "$PGPASSWORD" ]; then
    echo "❌ ERROR: Missing required environment variables!"
    echo "Please set PGHOST, PGDATABASE, PGUSER, PGPASSWORD, and PGPORT in Railway"
    echo ""
    echo "Sleeping for 1 hour to keep container alive for debugging..."
    sleep 3600
    exit 1
fi

echo "✅ Environment variables configured"
echo ""

# Start cron in the background
echo "Starting cron daemon..."
cron

# Give cron a moment to start
sleep 2

# Verify cron is running
if ! pgrep cron > /dev/null; then
    echo "❌ ERROR: Cron daemon failed to start"
    echo "Sleeping for 1 hour to keep container alive for debugging..."
    sleep 3600
    exit 1
fi

echo "✅ Cron daemon started successfully"
echo ""
echo "Crontab contents:"
crontab -l
echo ""

# Run the analysis once at startup for verification
echo "========================================="
echo "Running Initial Analysis"
echo "========================================="
set +e  # Don't exit on error for the initial run
python /app/analyze.py --outdir /app/analysis_outputs 2>&1 | tee -a /var/log/cron.log
EXIT_CODE=$?
set -e

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Initial analysis completed successfully!"
else
    echo "⚠️  Initial analysis failed with exit code: $EXIT_CODE"
    echo "Check the output above for errors."
fi

echo ""
echo "========================================="
echo "Service is Running"
echo "========================================="
echo "Scheduled runs: Sundays at midnight UTC"
echo "Volume mount: /app/analysis_outputs"
echo ""
echo "Cron logs will appear below:"
echo "---"

# Tail the log file to keep container alive
tail -f /var/log/cron.log
