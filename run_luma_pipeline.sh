#!/bin/bash
# Luma Event Sync Pipeline Orchestrator
# Runs the full pipeline: sync events -> auto-approve RSVPs -> import attendance
#                         -> analytics -> placards -> Mailchimp sync/tagging
#
# Error handling: every step is isolated. A failing step logs a warning, gets
# recorded, and the pipeline moves on so later steps (attendance import,
# Mailchimp tagging, ...) still run. At the end the script exits non-zero if
# any step failed, so Railway shows the cron run as failed and the log gets
# looked at. Do NOT add `set -e` here: it would abort the whole run on the
# first failing step and silently skip everything after it.

# Get script directory for relative paths
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Overlap guard: a slow/hung run must not overlap with the next scheduled run
# (duplicate imports, port collisions in placard generation, DB contention).
# flock is available in the Railway container (util-linux); skipped on macOS.
if command -v flock >/dev/null 2>&1; then
    exec 200>/tmp/luma_pipeline.lock
    if ! flock -n 200; then
        echo "Another pipeline run is already in progress - exiting."
        exit 0
    fi
fi

# Per-step timeout wrapper so no single step can hang the pipeline forever.
# Uses coreutils timeout when available (Railway container); runs bare on macOS.
run_step() {
    local duration="$1"; shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$duration" "$@"
    else
        "$@"
    fi
}

# Failure bookkeeping
FAILED_STEPS=()
record_failure() {
    # $1 = step label, $2 = exit code
    local detail="exit $2"
    if [ "$2" -eq 124 ]; then
        detail="timed out"
    fi
    FAILED_STEPS+=("$1 ($detail)")
    echo "  ⚠️  $1 failed ($detail)"
}

# Load environment variables from .env file (local runs only; Railway injects env)
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
#   exit 0 -> new past events need attendance import (JSON list on stdout)
#   exit 1 -> nothing to import (normal)
#   exit 2 -> error
echo ""
echo "Step 1: Syncing events from Luma API..."
events_json=$(run_step 30m python3 "$SCRIPT_DIR/luma_sync.py")
sync_exit_code=$?
case $sync_exit_code in
    0) echo "Event sync completed - new events need attendance import" ;;
    1) echo "Event sync completed - no events need attendance import" ;;
    *) record_failure "Step 1 (luma_sync)" $sync_exit_code ;;
esac

# Step 2: Auto-approve pending RSVPs for upcoming events.
# Currently PAUSED inside auto_approve_rsvps.py: the script exits 0 without
# approving anything unless LUMA_AUTO_APPROVE_ENABLED=true is set.
echo ""
echo "Step 2: Auto-approving pending RSVPs for upcoming events..."
run_step 15m python3 "$SCRIPT_DIR/luma/auto_approve_rsvps.py"
rc=$?
if [ $rc -eq 0 ]; then
    echo "RSVP auto-approval completed successfully"
else
    record_failure "Step 2 (auto_approve_rsvps)" $rc
fi

# Steps 3-7 only run when Step 1 found newly finished events
if [ $sync_exit_code -eq 0 ]; then
    # Step 3: Import attendance from the downloaded Luma JSON files
    echo ""
    echo "Step 3: Importing attendance data from Luma JSON..."
    echo "$events_json" | run_step 30m python3 "$SCRIPT_DIR/import_luma_attendance.py"
    rc=${PIPESTATUS[1]}
    if [ $rc -eq 0 ]; then
        echo "Attendance import completed successfully"
    else
        record_failure "Step 3 (import_luma_attendance)" $rc
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
except Exception:
    pass
")

    if [ -n "$event_ids" ]; then
        for event_id in $event_ids; do
            echo "  Analyzing event ID: $event_id"
            run_step 15m python3 "$SCRIPT_DIR/event_analysis_single.py" --event-id "$event_id" --outdir "$SCRIPT_DIR/analysis_outputs"
            rc=$?
            if [ $rc -eq 0 ]; then
                echo "  ✅ Event $event_id analysis completed"
            else
                record_failure "Step 4 (event_analysis_single, event $event_id)" $rc
            fi
        done
    else
        echo "  No event IDs found in sync output"
    fi

    # Step 5: Generate placard PDFs for all analyzed events
    echo ""
    echo "Step 5: Generating placard PDFs for analyzed events..."
    run_step 60m python3 "$SCRIPT_DIR/generate_all_placards.py" --input-csv "$SCRIPT_DIR/analysis_outputs/event_analysis_all.csv" --placard-dir "$SCRIPT_DIR/placard_generation"
    rc=$?
    if [ $rc -eq 0 ]; then
        echo "Placard generation completed successfully"
    else
        record_failure "Step 5 (generate_all_placards)" $rc
    fi

    # Step 6: Run comprehensive analytics
    echo ""
    echo "Step 6: Running comprehensive analytics..."
    run_step 30m python3 "$SCRIPT_DIR/analyze.py" --outdir "$SCRIPT_DIR/analysis_outputs"
    rc=$?
    if [ $rc -eq 0 ]; then
        echo "Analytics completed successfully"
    else
        record_failure "Step 6 (analyze)" $rc
    fi

    # Step 7: Sync Mailchimp audience (if credentials are configured)
    echo ""
    echo "Step 7: Syncing Mailchimp audience..."
    if [ -n "$MAILCHIMP_API_KEY" ] && [ -n "$MAILCHIMP_AUDIENCE_ID" ]; then
        run_step 30m python3 "$SCRIPT_DIR/mailChimp/sync_mailchimp_audience.py"
        rc=$?
        if [ $rc -eq 0 ]; then
            echo "Mailchimp audience sync completed successfully"
        else
            record_failure "Step 7 (sync_mailchimp_audience)" $rc
        fi
    else
        echo "Mailchimp credentials not configured - skipping audience sync"
        echo "(Set MAILCHIMP_API_KEY, MAILCHIMP_SERVER_PREFIX, and MAILCHIMP_AUDIENCE_ID to enable)"
    fi
else
    echo ""
    echo "Steps 3-7: No events require attendance import (skipping)"
fi

# Step 8: Tag attendees and RSVP no-shows in Mailchimp for any untagged past events.
# Runs on EVERY pipeline run (self-healing): an event whose tagging failed in a
# previous run is retried here until it succeeds, and newly imported events are
# tagged in the same run. Gated on events.mailchimp_tagged_at in the database.
echo ""
echo "Step 8: Tagging Mailchimp attendees for untagged past events..."
if [ -n "$MAILCHIMP_API_KEY" ] && [ -n "$MAILCHIMP_AUDIENCE_ID" ]; then
    untagged_event_ids=$(run_step 5m python3 "$SCRIPT_DIR/mailChimp/tag_mailchimp_attendees.py" --list-untagged)
    rc=$?
    if [ $rc -ne 0 ]; then
        record_failure "Step 8 (list untagged events)" $rc
    elif [ -n "$untagged_event_ids" ]; then
        for event_id in $untagged_event_ids; do
            echo "  Tagging attendees and RSVP no-shows in Mailchimp for event $event_id..."
            run_step 15m python3 "$SCRIPT_DIR/mailChimp/tag_mailchimp_attendees.py" --event-id "$event_id"
            rc=$?
            if [ $rc -eq 0 ]; then
                echo "  ✅ Event $event_id Mailchimp tagging completed"
            else
                record_failure "Step 8 (tag event $event_id, will retry next run)" $rc
            fi
        done
    else
        echo "  No untagged events - nothing to do"
    fi
else
    echo "Mailchimp credentials not configured - skipping event tagging"
fi

echo ""
echo "========================================="
if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
    echo "Luma Event Sync Pipeline Completed - all steps succeeded"
    echo "Time: $(date)"
    echo "========================================="
    exit 0
else
    echo "Luma Event Sync Pipeline Completed WITH FAILURES:"
    for step in "${FAILED_STEPS[@]}"; do
        echo "  - $step"
    done
    echo "Time: $(date)"
    echo "========================================="
    exit 1
fi
