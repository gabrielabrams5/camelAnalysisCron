#!/usr/bin/env python3
"""
Transform event analysis CSV to placard-compatible key-value format.

This script reads a single event row from event_analysis_all.csv and transforms
it into the key-value CSV format expected by the placard generation system.
"""

import argparse
import csv
import sys
from datetime import datetime
import pandas as pd


def format_date_short(date_str):
    """
    Convert ISO datetime to short format (e.g., '2/11/26' or '2/5').

    Args:
        date_str: ISO datetime string or None

    Returns:
        Formatted short date string or empty string
    """
    if not date_str or pd.isna(date_str):
        return ""

    try:
        # Parse the datetime string
        dt = pd.to_datetime(date_str)
        # Format as M/D/YY or M/D depending on context
        # For event date, include year
        if '/' in date_str or '-' in date_str:
            return dt.strftime("%-m/%-d/%y")
        return dt.strftime("%-m/%-d")
    except Exception as e:
        print(f"Warning: Could not parse date '{date_str}': {e}", file=sys.stderr)
        return str(date_str)


def format_previous_event_date(date_str):
    """Format previous event date to short format without year (e.g., '2/5')."""
    if not date_str or pd.isna(date_str):
        return ""

    try:
        dt = pd.to_datetime(date_str)
        return dt.strftime("%-m/%-d")
    except Exception:
        return str(date_str)


def transform_event_to_placard_format(event_row):
    """
    Transform a single event row to placard key-value format.

    Args:
        event_row: pandas Series containing event analysis data

    Returns:
        Dictionary mapping placard keys to values
    """
    # Helper function to safely get values
    def get_val(key, default=""):
        val = event_row.get(key, default)
        return "" if pd.isna(val) else val

    # Helper to format percentages (remove decimal if whole number)
    def fmt_pct(val):
        if pd.isna(val) or val == "":
            return "0"
        try:
            num = float(val)
            # Round to whole number for display
            return str(int(round(num)))
        except (ValueError, TypeError):
            return "0"

    # Build the placard data dictionary
    placard_data = {
        # Event identification
        "eventName": get_val("event_name"),
        "venue": get_val("venue", ""),
        "date": format_date_short(get_val("event_date")),

        # Previous event info
        "lastEvent": get_val("previous_event_name"),
        "lastEventDate": format_previous_event_date(get_val("previous_event_date")),

        # Main metrics
        "rsvps": fmt_pct(get_val("rsvps")),
        "attendees": fmt_pct(get_val("attendees")),
        "firstTimers": fmt_pct(get_val("first_timers")),

        # Deltas (percent changes)
        "rsvpsDelta": fmt_pct(get_val("rsvps_pct_change")),
        "attendeesDelta": fmt_pct(get_val("attendees_pct_change")),
        "firstTimersDelta": fmt_pct(get_val("first_timers_pct_change")),

        # Financials (conditional - only if cost data exists)
        "hasCostData": "true" if (not pd.isna(get_val("cost")) and get_val("cost") != "") else "false",
        "totalCost": fmt_pct(get_val("cost")) if (not pd.isna(get_val("cost")) and get_val("cost") != "") else "N/A",
        "perAttendee": f"{float(get_val('per_attendee_cost')):.2f}" if (not pd.isna(get_val("per_attendee_cost")) and get_val("per_attendee_cost") != "") else "N/A",
        "perFirstTimer": f"{float(get_val('per_first_timer_cost')):.2f}" if (not pd.isna(get_val("per_first_timer_cost")) and get_val("per_first_timer_cost") != "") else "N/A",

        # Demographics - Gender
        "malePct": fmt_pct(get_val("male_pct")),
        "femalePct": fmt_pct(get_val("female_pct")),

        # Demographics - School
        "mitPct": fmt_pct(get_val("mit_pct")),
        "harvardPct": fmt_pct(get_val("harvard_pct")),

        # Demographics - Class Year
        "underclassmenPct": fmt_pct(get_val("underclassmen_pct")),
        "upperclassmenPct": fmt_pct(get_val("upperclassmen_pct")),

        # Attendance History
        "firstEventPct": fmt_pct(get_val("first_event_pct")),
        "twoThreeEventsPct": fmt_pct(get_val("events_2_3_pct")),
        "fourPlusEventsPct": fmt_pct(get_val("events_4_plus_pct")),
    }

    # Retention metrics (i-1 through i-4)
    # Format: "EventName, Date" for event, then total and new percentages
    for i in range(1, 5):
        # Get event name and date from the CSV (now included from enhanced analysis script)
        event_name = get_val(f"event_name_i_minus_{i}")
        event_date = format_previous_event_date(get_val(f"event_date_i_minus_{i}"))

        # Fallback to previous_event fields for i=1 if the new columns don't exist
        if (not event_name or event_name == "") and i == 1:
            event_name = get_val("previous_event_name")
            event_date = format_previous_event_date(get_val("previous_event_date"))

        retention_label = f"{event_name}, {event_date}" if event_date else event_name

        placard_data[f"retention_event_{i}"] = retention_label
        placard_data[f"retention_total_{i}"] = fmt_pct(get_val(f"return_rate_i_minus_{i}"))
        placard_data[f"retention_new_{i}"] = fmt_pct(get_val(f"first_timer_return_rate_i_minus_{i}"))

    return placard_data


def main():
    parser = argparse.ArgumentParser(
        description="Transform event analysis CSV to placard key-value format"
    )
    parser.add_argument(
        "--event-id",
        type=int,
        required=True,
        help="Event ID to transform"
    )
    parser.add_argument(
        "--input-csv",
        default="./event_analysis_all.csv",
        help="Input CSV file path (default: ./event_analysis_all.csv)"
    )
    parser.add_argument(
        "--output-csv",
        default="./placard_generation/public/event_data.csv",
        help="Output CSV file path (default: ./placard_generation/public/event_data.csv)"
    )

    args = parser.parse_args()

    # Read the input CSV
    try:
        df = pd.read_csv(args.input_csv)
    except FileNotFoundError:
        print(f"Error: Input file not found: {args.input_csv}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading CSV: {e}", file=sys.stderr)
        sys.exit(1)

    # Find the row for the specified event_id
    event_rows = df[df['event_id'] == args.event_id]

    if event_rows.empty:
        print(f"Error: Event ID {args.event_id} not found in {args.input_csv}", file=sys.stderr)
        sys.exit(1)

    # Get the first matching row (should only be one)
    event_row = event_rows.iloc[0]

    # Transform to placard format
    placard_data = transform_event_to_placard_format(event_row)

    # Write to output CSV in key-value format
    try:
        with open(args.output_csv, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['key', 'value'])
            for key, value in placard_data.items():
                writer.writerow([key, value])

        print(f"Successfully wrote placard data for event {args.event_id} to {args.output_csv}")
    except Exception as e:
        print(f"Error writing output CSV: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
