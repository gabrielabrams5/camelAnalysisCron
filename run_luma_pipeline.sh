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

    # Step 3: Run single event analysis for each newly imported event
    echo ""
    echo "Step 3: Running single event analysis for newly imported events..."

    # Parse JSON to extract event IDs using Python
    event_ids=$(echo "$events_json" | python3 -c "
import json
import sys
try:
    events = json.load(sys.stdin)
    if events:
        print(' '.join(str(e['event_id']) for e in events))
except:
    pass
")

    if [ -n "$event_ids" ]; then
        for event_id in $event_ids; do
            echo "  Analyzing event ID: $event_id"
            python3 /app/event_analysis_single.py --event-id "$event_id" --outdir /app/analysis_outputs

            if [ $? -eq 0 ]; then
                echo "  ✅ Event $event_id analysis completed"
            else
                echo "  ⚠️  Event $event_id analysis encountered errors"
            fi
        done
    else
        echo "  No event IDs found in sync output"
    fi

    # Step 4: Generate placard PDFs for all analyzed events
    echo ""
    echo "Step 4: Generating placard PDFs for analyzed events..."
    python3 /app/generate_all_placards.py --input-csv /app/analysis_outputs/event_analysis_all.csv --placard-dir /app/placard_generation

    if [ $? -eq 0 ]; then
        echo "Placard generation completed successfully"
    else
        echo "Warning: Placard generation encountered errors"
    fi
else
    echo ""
    echo "Step 2: No events require attendance import (skipping)"
fi

# Step 5: Run comprehensive analytics (always run this)
echo ""
echo "Step 5: Running comprehensive analytics..."
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
