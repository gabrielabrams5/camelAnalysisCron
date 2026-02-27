#!/bin/bash
# Luma Event Sync Pipeline Orchestrator
# Runs the full pipeline: sync events -> import attendance -> run analytics

set -e  # Exit on error

# Get script directory for relative paths
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Load environment variables from .env file
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

echo "========================================="
echo "Luma Event Sync Pipeline Started"
echo "Time: $(date)"
echo "========================================="

# Step 1: Sync events from Luma API
echo ""
echo "Step 1: Syncing events from Luma API..."
set +e  # Temporarily allow non-zero exit codes
events_json=$(python3 "$SCRIPT_DIR/luma_sync.py")
sync_exit_code=$?
set -e  # Re-enable exit on error

# Step 2: Auto-approve pending RSVPs for upcoming events
echo ""
echo "Step 2: Auto-approving pending RSVPs for upcoming events..."
python3 "$SCRIPT_DIR/luma/auto_approve_rsvps.py"

if [ $? -eq 0 ]; then
    echo "RSVP auto-approval completed successfully"
else
    echo "Warning: RSVP auto-approval encountered errors"
fi

# Step 3: Import attendance if CSVs were downloaded
if [ $sync_exit_code -eq 0 ]; then
    echo ""
    echo "Step 3: Importing attendance data from Luma CSVs..."
    echo "$events_json" | python3 "$SCRIPT_DIR/import_luma_attendance.py"

    if [ $? -eq 0 ]; then
        echo "Attendance import completed successfully"
    else
        echo "Warning: Attendance import encountered errors"
    fi

    # Step 4: Run single event analysis for each newly imported event
    echo ""
    echo "Step 4: Running single event analysis for newly imported events..."

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
            python3 "$SCRIPT_DIR/event_analysis_single.py" --event-id "$event_id" --outdir "$SCRIPT_DIR/analysis_outputs"

            if [ $? -eq 0 ]; then
                echo "  ✅ Event $event_id analysis completed"
            else
                echo "  ⚠️  Event $event_id analysis encountered errors"
            fi

            # Tag attendees and RSVP no-shows in Mailchimp (if credentials configured)
            if [ -n "$MAILCHIMP_API_KEY" ] && [ -n "$MAILCHIMP_AUDIENCE_ID" ]; then
                echo "  Tagging attendees and RSVP no-shows in Mailchimp for event $event_id..."
                python3 "$SCRIPT_DIR/mailChimp/tag_mailchimp_attendees.py" --event-id "$event_id"

                if [ $? -eq 0 ]; then
                    echo "  ✅ Event $event_id Mailchimp tagging completed"
                else
                    echo "  ⚠️  Event $event_id Mailchimp tagging encountered errors"
                fi
            else
                echo "  Mailchimp credentials not configured - skipping event tagging"
            fi
        done
    else
        echo "  No event IDs found in sync output"
    fi

    # Step 5: Generate placard PDFs for all analyzed events
    echo ""
    echo "Step 5: Generating placard PDFs for analyzed events..."
    python3 "$SCRIPT_DIR/generate_all_placards.py" --input-csv "$SCRIPT_DIR/analysis_outputs/event_analysis_all.csv" --placard-dir "$SCRIPT_DIR/placard_generation"

    if [ $? -eq 0 ]; then
        echo "Placard generation completed successfully"
    else
        echo "Warning: Placard generation encountered errors"
    fi
else
    echo ""
    echo "Step 3: No events require attendance import (skipping)"
fi

# Step 6: Run comprehensive analytics (always run this)
echo ""
echo "Step 6: Running comprehensive analytics..."
python3 "$SCRIPT_DIR/analyze.py" --outdir "$SCRIPT_DIR/analysis_outputs"

if [ $? -eq 0 ]; then
    echo "Analytics completed successfully"
else
    echo "Warning: Analytics encountered errors"
fi

# Step 7: Sync Mailchimp audience (if credentials are configured)
echo ""
echo "Step 7: Syncing Mailchimp audience..."

# Check if Mailchimp credentials are configured
if [ -n "$MAILCHIMP_API_KEY" ] && [ -n "$MAILCHIMP_AUDIENCE_ID" ]; then
    set +e  # Temporarily allow non-zero exit codes
    python3 "$SCRIPT_DIR/mailChimp/sync_mailchimp_audience.py"
    mailchimp_exit_code=$?
    set -e  # Re-enable exit on error

    if [ $mailchimp_exit_code -eq 0 ]; then
        echo "Mailchimp audience sync completed successfully"
    else
        echo "Warning: Mailchimp audience sync encountered errors"
    fi
else
    echo "Mailchimp credentials not configured - skipping audience sync"
    echo "(Set MAILCHIMP_API_KEY, MAILCHIMP_SERVER_PREFIX, and MAILCHIMP_AUDIENCE_ID to enable)"
fi

echo ""
echo "========================================="
echo "Luma Event Sync Pipeline Completed"
echo "Time: $(date)"
echo "========================================="
