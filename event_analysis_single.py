#!/usr/bin/env python3
"""
Single Event Analysis Script

Analyzes a specific event and compares it to the previous event (event_id - 1).
Outputs a CSV with comprehensive metrics including:
- RSVPs, attendees, first timers (with % change from previous event)
- Demographics (gender, school, class year)
- Attendance patterns (1st event, 2-3 events, 4+ events)
- Retention rates from previous 4 events
"""

import psycopg2
import pandas as pd
import os
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path


def connect_to_db():
    """Connect to Railway PostgreSQL database."""
    load_dotenv()

    conn = psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=os.getenv('PGPORT'),
        database=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD')
    )

    return conn


def display_events(conn):
    """Display all events and let user choose one."""
    query = """
    SELECT id, event_name, start_datetime, category, attendance
    FROM events
    ORDER BY start_datetime DESC
    """

    events_df = pd.read_sql(query, conn)

    print("\n=== AVAILABLE EVENTS ===")
    print(f"{'ID':<5} {'Name':<50} {'Date':<20} {'Category':<15} {'Attendance':<10}")
    print("=" * 105)

    for _, row in events_df.iterrows():
        date_str = row['start_datetime'].strftime('%Y-%m-%d %H:%M') if pd.notna(row['start_datetime']) else 'N/A'
        attendance = row['attendance'] if pd.notna(row['attendance']) else 0
        category = row['category'] if pd.notna(row['category']) else 'N/A'
        print(f"{row['id']:<5} {row['event_name'][:48]:<50} {date_str:<20} {category:<15} {int(attendance):<10}")

    print("\n")
    return events_df


def calculate_academic_year_cutoff(event_date):
    """
    Calculate the class year cutoff for underclassmen.
    Underclassmen = freshmen + sophomores

    The cutoff is: closest year that has had August + 2
    - If we're past August, it's current_year + 2
    - If we haven't reached August yet, it's (current_year - 1) + 2
    """
    if event_date.month >= 8:  # August or later
        return event_date.year + 2
    else:  # Before August
        return (event_date.year - 1) + 2


def get_event_metrics(conn, event_id):
    """Get comprehensive metrics for a specific event."""

    # Get event details
    event_query = """
    SELECT id, event_name, start_datetime, category, cost, location
    FROM events
    WHERE id = %s
    """
    event_info = pd.read_sql(event_query, conn, params=(event_id,))

    if event_info.empty:
        return None

    event_info = event_info.iloc[0]
    event_date = event_info['start_datetime']

    # Get attendance data with people info
    attendance_query = """
    SELECT
        a.person_id,
        a.rsvp,
        a.checked_in,
        a.is_first_event,
        p.gender,
        p.school,
        p.class_year,
        p.event_attendance_count
    FROM attendance a
    JOIN people p ON a.person_id = p.id
    WHERE a.event_id = %s
    """
    attendance_df = pd.read_sql(attendance_query, conn, params=(event_id,))

    metrics = {
        'event_id': event_id,
        'event_name': event_info['event_name'],
        'event_date': event_date,
        'category': event_info['category'],
        'cost': event_info['cost'] if pd.notna(event_info['cost']) else None,
        'location': event_info['location'] if pd.notna(event_info['location']) else ''
    }

    # Core metrics
    metrics['rsvps'] = attendance_df['rsvp'].sum()
    metrics['attendees'] = attendance_df['checked_in'].sum()
    metrics['first_timers'] = attendance_df[attendance_df['checked_in'] & attendance_df['is_first_event']].shape[0]

    # Financial metrics (if cost is available)
    if metrics['cost'] is not None:
        metrics['per_attendee_cost'] = (metrics['cost'] / metrics['attendees']) if metrics['attendees'] > 0 else None
        metrics['per_first_timer_cost'] = (metrics['cost'] / metrics['first_timers']) if metrics['first_timers'] > 0 else None
    else:
        metrics['per_attendee_cost'] = None
        metrics['per_first_timer_cost'] = None

    # Get only attendees for demographic analysis
    attendees = attendance_df[attendance_df['checked_in']]

    if len(attendees) > 0:
        # Gender breakdown
        gender_counts = attendees['gender'].value_counts()
        total_attendees = len(attendees)
        raw_male_pct = (gender_counts.get('M', 0) / total_attendees * 100) if total_attendees > 0 else 0
        raw_female_pct = (gender_counts.get('F', 0) / total_attendees * 100) if total_attendees > 0 else 0

        # Calculate unaccounted gender percentage
        gender_sum = raw_male_pct + raw_female_pct
        metrics['gender_unaccounted_pct'] = 100 - gender_sum if gender_sum > 0 else 0

        # Normalize to 100%
        if gender_sum > 0:
            metrics['male_pct'] = (raw_male_pct / gender_sum) * 100
            metrics['female_pct'] = (raw_female_pct / gender_sum) * 100
        else:
            metrics['male_pct'] = 0
            metrics['female_pct'] = 0

        # School breakdown
        school_counts = attendees['school'].value_counts()
        raw_mit_pct = (school_counts.get('mit', 0) / total_attendees * 100) if total_attendees > 0 else 0
        raw_harvard_pct = (school_counts.get('harvard', 0) / total_attendees * 100) if total_attendees > 0 else 0

        # Calculate unaccounted school percentage
        school_sum = raw_mit_pct + raw_harvard_pct
        metrics['school_unaccounted_pct'] = 100 - school_sum if school_sum > 0 else 0

        # Normalize to 100%
        if school_sum > 0:
            metrics['mit_pct'] = (raw_mit_pct / school_sum) * 100
            metrics['harvard_pct'] = (raw_harvard_pct / school_sum) * 100
        else:
            metrics['mit_pct'] = 0
            metrics['harvard_pct'] = 0

        # Class year breakdown (underclassmen vs upperclassmen)
        cutoff_year = calculate_academic_year_cutoff(event_date)
        # Filter out NULL/NaN class_year values
        attendees_with_class_year = attendees[attendees['class_year'].notna()]
        underclassmen = attendees_with_class_year[attendees_with_class_year['class_year'] > cutoff_year].shape[0]
        upperclassmen = attendees_with_class_year[attendees_with_class_year['class_year'] <= cutoff_year].shape[0]

        # Calculate raw percentages
        raw_underclassmen_pct = (underclassmen / total_attendees * 100) if total_attendees > 0 else 0
        raw_upperclassmen_pct = (upperclassmen / total_attendees * 100) if total_attendees > 0 else 0

        # Calculate unaccounted class year percentage
        class_year_sum = raw_underclassmen_pct + raw_upperclassmen_pct
        metrics['class_year_unaccounted_pct'] = 100 - class_year_sum if class_year_sum > 0 else 0

        # Normalize to 100%
        if class_year_sum > 0:
            metrics['underclassmen_pct'] = (raw_underclassmen_pct / class_year_sum) * 100
            metrics['upperclassmen_pct'] = (raw_upperclassmen_pct / class_year_sum) * 100
        else:
            metrics['underclassmen_pct'] = 0
            metrics['upperclassmen_pct'] = 0

        # Attendance frequency breakdown (using lifetime event_attendance_count)
        first_event = attendees[attendees['event_attendance_count'] == 1].shape[0]
        events_2_3 = attendees[(attendees['event_attendance_count'] >= 2) & (attendees['event_attendance_count'] <= 3)].shape[0]
        events_4_plus = attendees[attendees['event_attendance_count'] >= 4].shape[0]

        metrics['first_event_pct'] = (first_event / total_attendees * 100) if total_attendees > 0 else 0
        metrics['events_2_3_pct'] = (events_2_3 / total_attendees * 100) if total_attendees > 0 else 0
        metrics['events_4_plus_pct'] = (events_4_plus / total_attendees * 100) if total_attendees > 0 else 0
    else:
        # No attendees
        metrics['male_pct'] = 0
        metrics['female_pct'] = 0
        metrics['gender_unaccounted_pct'] = 0
        metrics['mit_pct'] = 0
        metrics['harvard_pct'] = 0
        metrics['school_unaccounted_pct'] = 0
        metrics['underclassmen_pct'] = 0
        metrics['upperclassmen_pct'] = 0
        metrics['class_year_unaccounted_pct'] = 0
        metrics['first_event_pct'] = 0
        metrics['events_2_3_pct'] = 0
        metrics['events_4_plus_pct'] = 0

    return metrics


def calculate_retention_rates(conn, current_event_id):
    """
    Calculate retention rates from previous 4 events.

    For each of events i-1, i-2, i-3, i-4:
    - % of attendees who also attended event i
    - % of first timers who also attended event i
    - Event name and date
    """
    retention = {}

    # Get current event attendees
    current_attendees_query = """
    SELECT person_id
    FROM attendance
    WHERE event_id = %s AND checked_in = TRUE
    """
    current_attendees = pd.read_sql(current_attendees_query, conn, params=(current_event_id,))
    current_attendee_ids = set(current_attendees['person_id'].tolist())

    for offset in range(1, 5):  # i-1, i-2, i-3, i-4
        prev_event_id = current_event_id - offset

        if prev_event_id < 1:
            # Event doesn't exist
            retention[f'return_rate_i_minus_{offset}'] = None
            retention[f'first_timer_return_rate_i_minus_{offset}'] = None
            retention[f'event_name_i_minus_{offset}'] = None
            retention[f'event_date_i_minus_{offset}'] = None
            continue

        # Get previous event info
        prev_event_query = """
        SELECT event_name, start_datetime
        FROM events
        WHERE id = %s
        """
        prev_event_info = pd.read_sql(prev_event_query, conn, params=(prev_event_id,))

        if prev_event_info.empty:
            retention[f'return_rate_i_minus_{offset}'] = None
            retention[f'first_timer_return_rate_i_minus_{offset}'] = None
            retention[f'event_name_i_minus_{offset}'] = None
            retention[f'event_date_i_minus_{offset}'] = None
            continue

        # Store event name and date
        retention[f'event_name_i_minus_{offset}'] = prev_event_info.iloc[0]['event_name']
        retention[f'event_date_i_minus_{offset}'] = prev_event_info.iloc[0]['start_datetime']

        # Get previous event attendees
        prev_attendees_query = """
        SELECT person_id, is_first_event
        FROM attendance
        WHERE event_id = %s AND checked_in = TRUE
        """
        prev_attendees = pd.read_sql(prev_attendees_query, conn, params=(prev_event_id,))

        if prev_attendees.empty:
            retention[f'return_rate_i_minus_{offset}'] = None
            retention[f'first_timer_return_rate_i_minus_{offset}'] = None
            continue

        # Calculate return rate for all attendees
        prev_attendee_ids = set(prev_attendees['person_id'].tolist())
        returned = len(current_attendee_ids & prev_attendee_ids)
        total_prev = len(prev_attendee_ids)
        retention[f'return_rate_i_minus_{offset}'] = (returned / total_prev * 100) if total_prev > 0 else 0

        # Calculate return rate for first timers only
        first_timers = prev_attendees[prev_attendees['is_first_event'] == True]
        first_timer_ids = set(first_timers['person_id'].tolist())
        first_timers_returned = len(current_attendee_ids & first_timer_ids)
        total_first_timers = len(first_timer_ids)
        retention[f'first_timer_return_rate_i_minus_{offset}'] = (first_timers_returned / total_first_timers * 100) if total_first_timers > 0 else 0

    return retention


def calculate_percent_change(current, previous):
    """Calculate percentage change between two values."""
    if previous is None or previous == 0:
        return None
    return ((current - previous) / previous * 100)


def main():
    """Main function."""
    import argparse

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='Single Event Analysis')
    parser.add_argument('--event-id', type=int, help='Event ID to analyze (for automated mode)')
    parser.add_argument('--outdir', type=str, default='.', help='Output directory for CSV file')
    parser.add_argument('--output-file', type=str, default='event_analysis_all.csv', help='Output CSV filename (default: event_analysis_all.csv)')
    args = parser.parse_args()

    print("=== Single Event Analysis ===\n")

    # Connect to database
    print("Connecting to Railway database...")
    conn = connect_to_db()
    print("Connected!\n")

    # Get event ID - either from args or interactively
    if args.event_id:
        # Automated mode
        event_id = args.event_id
        print(f"Analyzing event ID: {event_id} (automated mode)\n")
    else:
        # Interactive mode
        # Display events
        events_df = display_events(conn)

        # Get event ID from user
        while True:
            try:
                event_id = int(input("Enter event ID to analyze: "))
                if event_id in events_df['id'].values:
                    break
                else:
                    print(f"Error: Event ID {event_id} not found. Please try again.")
            except ValueError:
                print("Error: Please enter a valid integer.")

        print(f"\nAnalyzing event {event_id}...\n")

    # Get metrics for current event
    current_metrics = get_event_metrics(conn, event_id)

    if current_metrics is None:
        print(f"Error: Could not find event {event_id}")
        conn.close()
        return

    # Get metrics for previous event (event_id - 1)
    prev_event_id = event_id - 1
    prev_metrics = get_event_metrics(conn, prev_event_id)

    # Calculate retention rates
    retention = calculate_retention_rates(conn, event_id)

    # Build output row
    output = {
        'event_id': current_metrics['event_id'],
        'event_name': current_metrics['event_name'],
        'event_date': current_metrics['event_date'],
        'category': current_metrics['category'],
        'venue': current_metrics['location'],

        # RSVPs
        'rsvps': current_metrics['rsvps'],
        'rsvps_pct_change': calculate_percent_change(current_metrics['rsvps'], prev_metrics['rsvps']) if prev_metrics else None,

        # Attendees
        'attendees': current_metrics['attendees'],
        'attendees_pct_change': calculate_percent_change(current_metrics['attendees'], prev_metrics['attendees']) if prev_metrics else None,

        # First timers
        'first_timers': current_metrics['first_timers'],
        'first_timers_pct_change': calculate_percent_change(current_metrics['first_timers'], prev_metrics['first_timers']) if prev_metrics else None,

        # Financials
        'cost': current_metrics['cost'],
        'per_attendee_cost': current_metrics['per_attendee_cost'],
        'per_first_timer_cost': current_metrics['per_first_timer_cost'],

        # Demographics
        'male_pct': current_metrics['male_pct'],
        'female_pct': current_metrics['female_pct'],
        'gender_unaccounted_pct': current_metrics['gender_unaccounted_pct'],
        'mit_pct': current_metrics['mit_pct'],
        'harvard_pct': current_metrics['harvard_pct'],
        'school_unaccounted_pct': current_metrics['school_unaccounted_pct'],
        'underclassmen_pct': current_metrics['underclassmen_pct'],
        'upperclassmen_pct': current_metrics['upperclassmen_pct'],
        'class_year_unaccounted_pct': current_metrics['class_year_unaccounted_pct'],

        # Attendance patterns
        'first_event_pct': current_metrics['first_event_pct'],
        'events_2_3_pct': current_metrics['events_2_3_pct'],
        'events_4_plus_pct': current_metrics['events_4_plus_pct'],

        # Previous event info
        'previous_event_id': prev_event_id if prev_metrics else None,
        'previous_event_name': prev_metrics['event_name'] if prev_metrics else None,
        'previous_event_date': prev_metrics['event_date'] if prev_metrics else None,
    }

    # Add retention rates
    output.update(retention)

    # Create DataFrame and save to CSV
    output_df = pd.DataFrame([output])

    # Format the output filename with outdir
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    output_path = outdir / args.output_file

    # Check if file exists to determine append mode
    file_exists = output_path.exists()

    # Append if file exists, otherwise create new file
    output_df.to_csv(
        output_path,
        mode='a' if file_exists else 'w',
        header=not file_exists,
        index=False
    )

    print(f"✅ Analysis complete!")
    if file_exists:
        print(f"📊 Output appended to: {output_path.resolve()}")
    else:
        print(f"📊 Output saved to: {output_path.resolve()}")

    # Display summary
    print("\n=== SUMMARY ===")
    print(f"Event: {current_metrics['event_name']} (ID: {event_id})")
    print(f"Date: {current_metrics['event_date']}")
    print(f"RSVPs: {current_metrics['rsvps']}")
    print(f"Attendees: {current_metrics['attendees']}")
    print(f"First Timers: {current_metrics['first_timers']}")
    print(f"Male: {current_metrics['male_pct']:.1f}% | Female: {current_metrics['female_pct']:.1f}% (Unaccounted: {current_metrics['gender_unaccounted_pct']:.1f}%)")
    print(f"MIT: {current_metrics['mit_pct']:.1f}% | Harvard: {current_metrics['harvard_pct']:.1f}% (Unaccounted: {current_metrics['school_unaccounted_pct']:.1f}%)")
    print(f"Underclassmen: {current_metrics['underclassmen_pct']:.1f}% | Upperclassmen: {current_metrics['upperclassmen_pct']:.1f}% (Unaccounted: {current_metrics['class_year_unaccounted_pct']:.1f}%)")

    if prev_metrics:
        print(f"\nCompared to previous event: {prev_metrics['event_name']} (ID: {prev_event_id})")
        if output['rsvps_pct_change'] is not None:
            print(f"  RSVPs change: {output['rsvps_pct_change']:+.1f}%")
        if output['attendees_pct_change'] is not None:
            print(f"  Attendees change: {output['attendees_pct_change']:+.1f}%")
        if output['first_timers_pct_change'] is not None:
            print(f"  First timers change: {output['first_timers_pct_change']:+.1f}%")

    conn.close()


if __name__ == '__main__':
    main()
