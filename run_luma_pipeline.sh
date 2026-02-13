#!/bin/bash
# Luma Event Sync Pipeline Orchestrator
# Runs the full pipeline: sync events -> import attendance -> run analytics

set -e  # Exit on error

echo "========================================="
echo "Luma Event Sync Pipeline Started"
echo "Time: $(date)"
echo "========================================="

# Step 1: Sync events from Luma API
echo ""
echo "Step 1: Syncing events from Luma API..."
events_json=$(python3 /app/luma_sync.py)
sync_exit_code=$?

# Step 2: Import attendance if CSVs were downloaded
if [ $sync_exit_code -eq 0 ]; then
    echo ""
    echo "Step 2: Importing attendance data from Luma CSVs..."
    echo "$events_json" | python3 /app/import_luma_attendance.py

    if [ $? -eq 0 ]; then
        echo "Attendance import completed successfully"
    else
        echo "Warning: Attendance import encountered errors"
    fi
else
    echo ""
    echo "Step 2: No events require attendance import (skipping)"
fi

# Step 3: Run analytics (always run this)
echo ""
echo "Step 3: Running analytics..."
python3 /app/analyze.py --outdir /app/analysis_outputs

if [ $? -eq 0 ]; then
    echo "Analytics completed successfully"
else
    echo "Warning: Analytics encountered errors"
fi

echo ""
echo "========================================="
echo "Luma Event Sync Pipeline Completed"
echo "Time: $(date)"
echo "========================================="
