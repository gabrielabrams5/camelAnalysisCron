#!/bin/bash
set -e

echo "Starting Event Analytics Cron Service..."
echo "Current time: $(date)"
echo "Cron schedule: Sundays at midnight UTC (0 0 * * 0)"

# Print environment check (without exposing secrets)
echo "Database host: ${PGHOST}"
echo "Database: ${PGDATABASE}"

# Start cron in the background
echo "Starting cron daemon..."
cron

# Verify cron is running
if ! pgrep cron > /dev/null; then
    echo "ERROR: Cron daemon failed to start"
    exit 1
fi

echo "Cron daemon started successfully"
echo "Crontab contents:"
crontab -l

# Run the analysis once at startup for verification
echo ""
echo "Running initial analysis for verification..."
python /app/analyze.py --outdir /app/analysis_outputs 2>&1 | tee -a /var/log/cron.log

echo ""
echo "Initial run complete. Service is now running."
echo "Logs will appear below:"
echo "---"

# Tail the log file to keep container alive
tail -f /var/log/cron.log
