#!/usr/bin/env python3
"""
Event Analytics Script - SQL Version

Creates a single master dataset by merging attendance, events, and people data
from a PostgreSQL database (Railway), then performs all analysis on this unified
dataset. Tracks both first-time attendees and first-time RSVPs (who didn't attend)
to understand conversion patterns.

This is the database version of event_analysis.py
"""

import argparse
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import psycopg2
import os
from dotenv import load_dotenv
import logging
from datetime import datetime
import json

def load_data_from_db():
    """Load data from PostgreSQL database instead of CSV files."""

    # Load environment variables
    load_dotenv()

    # Connect to Railway database
    conn = psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=os.getenv('PGPORT'),
        database=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD')
    )

    print("Connected to Railway database")

    # Load attendance data
    print("Loading attendance data...")
    attendance_query = """
        SELECT
            id,
            person_id,
            event_id,
            rsvp,
            approved,
            checked_in,
            rsvp_datetime,
            is_first_event,
            invite_token_id
        FROM attendance
        ORDER BY rsvp_datetime
    """
    attendance = pd.read_sql(attendance_query, conn)

    # Load events data
    print("Loading events data...")
    events_query = """
        SELECT
            id,
            event_name,
            category,
            location,
            start_datetime,
            description
        FROM events
        ORDER BY start_datetime
    """
    events = pd.read_sql(events_query, conn)

    # Load people data
    print("Loading people data...")
    people_query = """
        SELECT
            id,
            first_name,
            last_name,
            preferred_name,
            gender,
            class_year,
            is_jewish,
            school,
            additional_info
        FROM people
    """
    people = pd.read_sql(people_query, conn)

    conn.close()
    print("Database connection closed")

    return attendance, events, people

def create_master_dataset_from_db():
    """Load data from database and merge into a single master dataset."""

    # Load data from database
    attendance, events, people = load_data_from_db()

    # Parse datetime columns
    attendance['rsvp_datetime'] = pd.to_datetime(attendance['rsvp_datetime'], errors='coerce')
    events['start_datetime'] = pd.to_datetime(events['start_datetime'], errors='coerce')

    # Convert boolean columns (they should already be boolean from postgres, but ensure)
    attendance['rsvp'] = attendance['rsvp'].astype(bool)
    attendance['checked_in'] = attendance['checked_in'].astype(bool)
    attendance['approved'] = attendance['approved'].astype(int)

    # For is_jewish, convert from boolean to 'J'/'N' format for compatibility
    # The CSV version uses 'J'/'N', but database uses boolean
    people['is_jewish'] = people['is_jewish'].apply(lambda x: 'J' if x == True else ('N' if x == False else None))

    # Merge everything into master dataset
    master = attendance.merge(events, left_on='event_id', right_on='id', suffixes=('', '_event'))
    master = master.merge(people, left_on='person_id', right_on='id', suffixes=('', '_person'))

    # Calculate first attendance (checked_in = True) per person
    first_attendance = master[master['checked_in']].groupby('person_id').agg({
        'event_id': 'first',
        'start_datetime': 'min'
    }).reset_index()
    first_attendance.columns = ['person_id', 'first_attendance_event_id', 'first_attendance_datetime']

    # Calculate first RSVP per person
    first_rsvp = master[master['rsvp']].groupby('person_id').agg({
        'event_id': 'first',
        'start_datetime': 'min'
    }).reset_index()
    first_rsvp.columns = ['person_id', 'first_rsvp_event_id', 'first_rsvp_datetime']

    # Add first attendance and RSVP info to master
    master = master.merge(first_attendance, on='person_id', how='left')
    master = master.merge(first_rsvp, on='person_id', how='left')

    # Add flags
    master['is_first_attendance'] = (master['event_id'] == master['first_attendance_event_id'])
    master['is_first_rsvp'] = (master['event_id'] == master['first_rsvp_event_id'])

    return master, events

def retention_analysis(master, events, outdir):
    """Analyze retention by event including RSVPs."""

    # Get attendees and RSVPs per event and who returned later
    retention_data = []

    for event_id in events['id'].unique():
        event_rsvps = master[(master['event_id'] == event_id) & (master['rsvp'])]['person_id'].unique()
        event_attendees = master[(master['event_id'] == event_id) & (master['checked_in'])]['person_id'].unique()
        event_time = events[events['id'] == event_id]['start_datetime'].iloc[0]
        event_name = events[events['id'] == event_id]['event_name'].iloc[0]

        # Count who returned to ANY later event (attendees)
        later_attendances = master[
            (master['person_id'].isin(event_attendees)) &
            (master['checked_in']) &
            (master['start_datetime'] > event_time)
        ]['person_id'].unique()

        # Count RSVPs who attended a later event
        rsvp_later_attended = master[
            (master['person_id'].isin(event_rsvps)) &
            (master['checked_in']) &
            (master['start_datetime'] > event_time)
        ]['person_id'].unique()

        retention_data.append({
            'event_id': event_id,
            'event_name': event_name,
            'event_time': event_time,
            'total_rsvps': len(event_rsvps),
            'total_attendees': len(event_attendees),
            'attendees_returned_later': len(later_attendances),
            'rsvps_attended_later': len(rsvp_later_attended),
            'retention_rate': len(later_attendances) / len(event_attendees) if len(event_attendees) > 0 else 0
        })

    retention_df = pd.DataFrame(retention_data).sort_values('event_time')

    # Plot grouped bar chart with 4 bars
    fig, ax = plt.subplots(figsize=(16, 8))
    x = np.arange(len(retention_df))
    width = 0.2

    bars1 = ax.bar(x - 1.5*width, retention_df['total_rsvps'], width, label='Total RSVPs', color='lightcoral')
    bars2 = ax.bar(x - 0.5*width, retention_df['total_attendees'], width, label='Total Attendees', color='steelblue')
    bars3 = ax.bar(x + 0.5*width, retention_df['attendees_returned_later'], width, label='Attendees Returned Later', color='darkgreen')
    bars4 = ax.bar(x + 1.5*width, retention_df['rsvps_attended_later'], width, label='RSVPs Attended Later', color='darkorange')

    ax.set_xlabel('Event')
    ax.set_ylabel('Number of People')
    ax.set_title('Event Retention: RSVPs, Attendance, and Return Patterns')
    ax.set_xticks(x)
    ax.set_xticklabels(retention_df['event_name'], rotation=45, ha='right')
    ax.legend()

    # Add value labels on bars
    for bars in [bars1, bars2, bars3, bars4]:
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{int(height)}', ha='center', va='bottom', fontsize=7)

    plt.tight_layout()
    plt.savefig(outdir / 'retention_by_event.png', dpi=150, bbox_inches='tight')
    plt.close()

    return retention_df

def new_members_analysis(master, events, outdir):
    """Analyze new attendees by event and category, including first-time RSVP tracking."""

    # New attendees by event (first attendances)
    new_by_event = master[master['is_first_attendance']].groupby(['event_id', 'event_name']).size().reset_index(name='new_members')

    # Total unique RSVPs by event
    total_rsvps_by_event = master[master['rsvp']].groupby(['event_id', 'event_name'])['person_id'].nunique().reset_index(name='total_rsvps')

    # Total unique attendees by event
    total_by_event = master[master['checked_in']].groupby(['event_id', 'event_name'])['person_id'].nunique().reset_index(name='total_attendees')

    # First-time RSVPs (who didn't attend) by event
    first_rsvp_no_attend = master[(master['is_first_rsvp']) & (~master['checked_in'])].groupby(['event_id', 'event_name']).agg({
        'person_id': lambda x: x.unique().tolist()
    }).reset_index()
    first_rsvp_no_attend.columns = ['event_id', 'event_name', 'first_rsvp_people']

    # Check which first-time RSVPers returned later
    first_rsvp_returns = []
    for _, row in first_rsvp_no_attend.iterrows():
        event_id = row['event_id']
        event_time = events[events['id'] == event_id]['start_datetime'].iloc[0]
        people = row['first_rsvp_people']

        returned_later = master[
            (master['person_id'].isin(people)) &
            (master['checked_in']) &
            (master['start_datetime'] > event_time)
        ]['person_id'].unique()

        first_rsvp_returns.append({
            'event_id': event_id,
            'event_name': row['event_name'],
            'first_rsvp_no_attend': len(people),
            'first_rsvp_returned': len(returned_later)
        })

    first_rsvp_df = pd.DataFrame(first_rsvp_returns)

    # Merge all data
    new_members_df = new_by_event.merge(total_rsvps_by_event, on=['event_id', 'event_name'])
    new_members_df = new_members_df.merge(total_by_event, on=['event_id', 'event_name'])
    new_members_df = new_members_df.merge(first_rsvp_df, on=['event_id', 'event_name'], how='left')
    new_members_df = new_members_df.fillna(0)
    new_members_df[['first_rsvp_no_attend', 'first_rsvp_returned']] = new_members_df[['first_rsvp_no_attend', 'first_rsvp_returned']].astype(int)

    # Sort by event time to show chronologically
    event_times = events[['id', 'start_datetime']].rename(columns={'id': 'event_id'})
    new_members_df = new_members_df.merge(event_times, on='event_id')
    new_members_df = new_members_df.sort_values('start_datetime')

    # Plot ALL events with 5 bars
    fig, ax = plt.subplots(figsize=(18, 8))
    x = np.arange(len(new_members_df))
    width = 0.16

    bars1 = ax.bar(x - 2*width, new_members_df['total_rsvps'], width, label='Total RSVPs', color='lightcoral')
    bars2 = ax.bar(x - width, new_members_df['total_attendees'], width, label='Total Attendees', color='steelblue')
    bars3 = ax.bar(x, new_members_df['new_members'], width, label='New Attendees (First-time)', color='lightgreen')
    bars4 = ax.bar(x + width, new_members_df['first_rsvp_no_attend'], width, label='First RSVP (No Show)', color='lightblue')
    bars5 = ax.bar(x + 2*width, new_members_df['first_rsvp_returned'], width, label='First RSVP → Later Attended', color='lightsalmon')

    ax.set_xlabel('Event')
    ax.set_ylabel('Number of People')
    ax.set_title('All Events: RSVP, Attendance & First-Timer Patterns')
    ax.set_xticks(x)
    ax.set_xticklabels(new_members_df['event_name'], rotation=75, ha='right', fontsize=8)
    ax.legend()

    # Add value labels (only for bars > 0 to avoid clutter)
    for bars in [bars1, bars2, bars3, bars4, bars5]:
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{int(height)}', ha='center', va='bottom', fontsize=6)

    plt.tight_layout()
    plt.savefig(outdir / 'new_members_by_event.png', dpi=150, bbox_inches='tight')
    plt.close()

    # New attendees by category with RSVP tracking
    category_stats = master[master['is_first_attendance']].groupby('category').agg({
        'person_id': 'count'
    }).reset_index()
    category_stats.columns = ['category', 'new_members']  # Keep column name for consistency

    # First RSVPs (no attend) by category
    first_rsvp_cat = master[(master['is_first_rsvp']) & (~master['checked_in'])].copy()

    category_data = []
    for category in master['category'].unique():
        # First-time attendees
        cat_first_timers = master[(master['is_first_attendance']) & (master['category'] == category)]['person_id'].unique()

        # First-time RSVPs (no show)
        cat_first_rsvp = first_rsvp_cat[first_rsvp_cat['category'] == category]['person_id'].unique()

        # Count returns for attendees
        attendee_returns = []
        for person in cat_first_timers:
            first_time = master[
                (master['person_id'] == person) &
                (master['is_first_attendance'])
            ]['start_datetime'].iloc[0]

            returns = master[
                (master['person_id'] == person) &
                (master['checked_in']) &
                (master['start_datetime'] > first_time)
            ].shape[0]

            attendee_returns.append(returns)

        # Count if RSVPers ever attended later
        rsvp_returned_count = 0
        for person in cat_first_rsvp:
            first_rsvp_time = master[
                (master['person_id'] == person) &
                (master['is_first_rsvp'])
            ]['start_datetime'].iloc[0]

            later_attended = master[
                (master['person_id'] == person) &
                (master['checked_in']) &
                (master['start_datetime'] > first_rsvp_time)
            ].shape[0]

            if later_attended > 0:
                rsvp_returned_count += 1

        category_data.append({
            'category': category,
            'new_members': len(cat_first_timers),  # Keep as new_members for consistency in variable names
            'returned_1x': sum(1 for r in attendee_returns if r == 1),
            'returned_2x': sum(1 for r in attendee_returns if r == 2),
            'returned_3plus': sum(1 for r in attendee_returns if r >= 3),
            'first_rsvp_no_show': len(cat_first_rsvp),
            'first_rsvp_returned': rsvp_returned_count
        })

    category_returns_df = pd.DataFrame(category_data).sort_values('new_members', ascending=False)

    # Plot category returns with 6 bars
    fig, ax = plt.subplots(figsize=(14, 7))
    categories = category_returns_df['category']
    x = np.arange(len(categories))
    width = 0.14

    bars1 = ax.bar(x - 2.5*width, category_returns_df['new_members'], width, label='New Attendees', color='steelblue')
    bars2 = ax.bar(x - 1.5*width, category_returns_df['returned_1x'], width, label='Returned 1x', color='lightgreen')
    bars3 = ax.bar(x - 0.5*width, category_returns_df['returned_2x'], width, label='Returned 2x', color='gold')
    bars4 = ax.bar(x + 0.5*width, category_returns_df['returned_3plus'], width, label='Returned 3+', color='coral')
    bars5 = ax.bar(x + 1.5*width, category_returns_df['first_rsvp_no_show'], width, label='First RSVP (No Show)', color='lightblue')
    bars6 = ax.bar(x + 2.5*width, category_returns_df['first_rsvp_returned'], width, label='First RSVP → Later Attended', color='lightsalmon')

    ax.set_xlabel('Event Category')
    ax.set_ylabel('Number of People')
    ax.set_title('New Attendees by Category: Attendance & RSVP Patterns')
    ax.set_xticks(x)
    ax.set_xticklabels(categories, rotation=45, ha='right')
    ax.legend(loc='upper right', ncol=2)

    plt.tight_layout()
    plt.savefig(outdir / 'new_members_by_category.png', dpi=150, bbox_inches='tight')
    plt.close()

    return new_members_df, category_returns_df

def party_analysis(master, events, outdir):
    """Analyze the big parties specifically, including first-time RSVP patterns."""

    # Identify parties (case-insensitive matching)
    party_names = ['launch', 'sababa nights', 'bsmnt', 'fall 2025']
    party_events = []

    for _, event in events.iterrows():
        event_name_lower = str(event['event_name']).lower()
        for party_name in party_names:
            if party_name in event_name_lower:
                party_events.append(event['id'])
                break

    party_data = []
    for event_id in party_events:
        event_info = events[events['id'] == event_id].iloc[0]
        event_time = event_info['start_datetime']

        # Total RSVPs
        total_rsvps = master[(master['event_id'] == event_id) & (master['rsvp'])]['person_id'].nunique()

        # Total attendees
        total = master[(master['event_id'] == event_id) & (master['checked_in'])]['person_id'].nunique()

        # First-timers (attended)
        first_timers = master[
            (master['event_id'] == event_id) &
            (master['checked_in']) &
            (master['is_first_attendance'])
        ]['person_id'].unique()

        # First-timers who returned
        returned = master[
            (master['person_id'].isin(first_timers)) &
            (master['checked_in']) &
            (master['start_datetime'] > event_time)
        ]['person_id'].nunique()

        # First RSVPs (no show)
        first_rsvp_no_show = master[
            (master['event_id'] == event_id) &
            (master['is_first_rsvp']) &
            (~master['checked_in'])
        ]['person_id'].unique()

        # First RSVPs who later attended
        first_rsvp_returned = master[
            (master['person_id'].isin(first_rsvp_no_show)) &
            (master['checked_in']) &
            (master['start_datetime'] > event_time)
        ]['person_id'].nunique()

        party_data.append({
            'event_name': event_info['event_name'],
            'event_time': event_time,
            'total_rsvps': total_rsvps,
            'total_attendees': total,
            'first_timers': len(first_timers),
            'first_timers_returned': returned,
            'first_rsvp_no_show': len(first_rsvp_no_show),
            'first_rsvp_returned': first_rsvp_returned,
            'retention_rate': returned / len(first_timers) if len(first_timers) > 0 else 0
        })

    party_df = pd.DataFrame(party_data).sort_values('event_time')

    # Plot party funnel with 6 bars
    fig, ax = plt.subplots(figsize=(14, 7))
    parties = party_df['event_name']
    x = np.arange(len(parties))
    width = 0.14

    bars1 = ax.bar(x - 2.5*width, party_df['total_rsvps'], width, label='Total RSVPs', color='lightcoral')
    bars2 = ax.bar(x - 1.5*width, party_df['total_attendees'], width, label='Total Attendees', color='steelblue')
    bars3 = ax.bar(x - 0.5*width, party_df['first_timers'], width, label='First-Time Attendees', color='lightgreen')
    bars4 = ax.bar(x + 0.5*width, party_df['first_timers_returned'], width, label='First-Timers Who Returned', color='darkgreen')
    bars5 = ax.bar(x + 1.5*width, party_df['first_rsvp_no_show'], width, label='First RSVP (No Show)', color='lightblue')
    bars6 = ax.bar(x + 2.5*width, party_df['first_rsvp_returned'], width, label='First RSVP → Later Attended', color='lightsalmon')

    ax.set_xlabel('Party')
    ax.set_ylabel('Number of People')
    ax.set_title('Party Funnel: RSVP, Attendance & First-Timer Patterns')
    ax.set_xticks(x)
    ax.set_xticklabels(parties, rotation=45, ha='right')
    ax.legend(loc='upper left', ncol=2)

    # Add value labels
    for bars in [bars1, bars2, bars3, bars4, bars5, bars6]:
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{int(height)}', ha='center', va='bottom', fontsize=8)

    plt.tight_layout()
    plt.savefig(outdir / 'party_funnel.png', dpi=150, bbox_inches='tight')
    plt.close()

    return party_df

def rsvp_conversion_analysis(master, outdir):
    """Analyze RSVP to attendance conversion - both overall and excluding parties."""

    # Version 1: All events
    # Get all people who ever RSVPed
    rsvp_people = master[master['rsvp']]['person_id'].unique()

    # Count how many events each person attended
    attendance_counts = []
    for person in rsvp_people:
        n_attended = master[
            (master['person_id'] == person) &
            (master['checked_in'])
        ]['event_id'].nunique()
        attendance_counts.append(n_attended)

    # Create histogram data
    max_count = max(attendance_counts) if attendance_counts else 0
    hist_data = pd.Series(attendance_counts).value_counts().sort_index()

    # Plot histogram - ALL events
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    # First subplot: All events
    x = list(range(0, max_count + 1))
    y = [hist_data.get(i, 0) for i in x]

    bars1 = ax1.bar(x, y, color='steelblue', edgecolor='black')

    # Add value labels on bars
    for bar, val in zip(bars1, y):
        if val > 0:
            ax1.text(bar.get_x() + bar.get_width()/2., bar.get_height(),
                   f'{int(val)}', ha='center', va='bottom')

    ax1.set_xlabel('Number of Events Attended')
    ax1.set_ylabel('Number of People')
    ax1.set_title(f'RSVP to Attendance (All Events)\n{len(rsvp_people):,} Unique RSVPs | {sum(1 for c in attendance_counts if c > 0):,} Unique Attendees')
    ax1.set_xticks(x)

    # Add explanatory text
    ax1.text(0.02, 0.95,
           '0 = RSVPed but never attended\n1+ = Number of events attended',
           transform=ax1.transAxes,
           verticalalignment='top',
           bbox=dict(boxstyle='round', facecolor='lightgray', alpha=0.8))

    # Version 2: Excluding party category
    # Get all people who RSVPed to non-party events
    non_party_rsvps = master[(master['rsvp']) & (master['category'] != 'party')]
    rsvp_people_no_party = non_party_rsvps['person_id'].unique()

    # Count how many non-party events each person attended
    attendance_counts_no_party = []
    for person in rsvp_people_no_party:
        n_attended = master[
            (master['person_id'] == person) &
            (master['checked_in']) &
            (master['category'] != 'party')
        ]['event_id'].nunique()
        attendance_counts_no_party.append(n_attended)

    # Create histogram data for non-party
    max_count_no_party = max(attendance_counts_no_party) if attendance_counts_no_party else 0
    hist_data_no_party = pd.Series(attendance_counts_no_party).value_counts().sort_index()

    # Second subplot: Excluding parties
    x2 = list(range(0, max_count_no_party + 1))
    y2 = [hist_data_no_party.get(i, 0) for i in x2]

    bars2 = ax2.bar(x2, y2, color='darkgreen', edgecolor='black')

    # Add value labels on bars
    for bar, val in zip(bars2, y2):
        if val > 0:
            ax2.text(bar.get_x() + bar.get_width()/2., bar.get_height(),
                   f'{int(val)}', ha='center', va='bottom')

    ax2.set_xlabel('Number of Events Attended')
    ax2.set_ylabel('Number of People')
    ax2.set_title(f'RSVP to Attendance (Excluding Parties)\n{len(rsvp_people_no_party):,} Unique RSVPs | {sum(1 for c in attendance_counts_no_party if c > 0):,} Unique Attendees')
    ax2.set_xticks(x2)

    # Add explanatory text
    ax2.text(0.02, 0.95,
           '0 = RSVPed but never attended\n1+ = Number of events attended\n(Party events excluded)',
           transform=ax2.transAxes,
           verticalalignment='top',
           bbox=dict(boxstyle='round', facecolor='lightgray', alpha=0.8))

    plt.tight_layout()
    plt.savefig(outdir / 'rsvp_conversion.png', dpi=150, bbox_inches='tight')
    plt.close()

    # Return summary stats for all events
    conversion_stats = pd.DataFrame({
        'events_attended': x,
        'people_count': y
    })

    return conversion_stats

def save_attendance_to_db(master, events):
    """Save attendance statistics back to the events table in the database."""

    # Load environment variables
    load_dotenv()

    # Connect to Railway database
    conn = psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=os.getenv('PGPORT'),
        database=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD')
    )

    cursor = conn.cursor()

    print("\nSaving attendance data to events table...")
    logging.info("Updating events table with attendance statistics...")

    # Calculate attendance stats for each event
    for event_id in events['id'].unique():
        # Total RSVPs
        total_rsvps = master[(master['event_id'] == event_id) & (master['rsvp'])]['person_id'].nunique()

        # Total attendees
        total_attendees = master[(master['event_id'] == event_id) & (master['checked_in'])]['person_id'].nunique()

        # First-time attendees
        first_time_attendees = master[
            (master['event_id'] == event_id) &
            (master['checked_in']) &
            (master['is_first_attendance'])
        ]['person_id'].nunique()

        # Create attendance data object (convert numpy types to Python types for JSON)
        attendance_data = {
            'total_rsvps': int(total_rsvps),
            'total_attendees': int(total_attendees),
            'first_time_attendees': int(first_time_attendees),
            'conversion_rate': float(round((total_attendees / total_rsvps * 100), 2)) if total_rsvps > 0 else 0.0
        }

        # Update the events table with attendance data as JSON
        update_query = """
            UPDATE events
            SET attendance = %s
            WHERE id = %s
        """

        cursor.execute(update_query, (json.dumps(attendance_data), int(event_id)))

    conn.commit()
    cursor.close()
    conn.close()

    print(f"✅ Attendance data saved to database for {len(events)} events")
    logging.info(f"Successfully updated attendance_data for {len(events)} events")

def generate_summary_stats(master, outdir):
    """Generate overall summary statistics."""

    stats = {
        'Total Unique People': master['person_id'].nunique(),
        'Total Events': master['event_id'].nunique(),
        'Total RSVPs': master[master['rsvp']].shape[0],
        'Total Attendances': master[master['checked_in']].shape[0],
        'Unique People Who RSVPed': master[master['rsvp']]['person_id'].nunique(),
        'Unique People Who Attended': master[master['checked_in']]['person_id'].nunique(),
        'Overall RSVP→Attendance Rate': master[master['checked_in']].shape[0] / master[master['rsvp']].shape[0] if master[master['rsvp']].shape[0] > 0 else 0,
        'Avg Events per Attendee': master[master['checked_in']].groupby('person_id')['event_id'].nunique().mean()
    }

    # Demographics of attendees
    attendees = master[master['checked_in']].drop_duplicates('person_id')
    stats['% Jewish Attendees'] = (attendees['is_jewish'] == 'J').mean() * 100 if 'is_jewish' in attendees.columns else None
    stats['% Female Attendees'] = (attendees['gender'] == 'F').mean() * 100 if 'gender' in attendees.columns else None

    # Save stats
    stats_df = pd.DataFrame([stats]).T
    stats_df.columns = ['Value']
    stats_df.to_csv(outdir / 'summary_stats.csv')

    print("\n=== SUMMARY STATISTICS ===")
    for key, value in stats.items():
        if value is not None:
            if '%' in key or 'Rate' in key:
                print(f"{key}: {value:.1f}%")
            elif 'Avg' in key:
                print(f"{key}: {value:.2f}")
            else:
                print(f"{key}: {int(value):,}")

    return stats_df

def main():
    parser = argparse.ArgumentParser(description='Event Analytics Script (SQL Version)')
    parser.add_argument('--outdir', default='analysis_outputs', help='Output directory')
    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logging.info("=" * 60)
    logging.info("Event Analytics Cron Job Started")
    logging.info("=" * 60)

    # Create output directory
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("Loading and merging data from Railway database...")
    logging.info("Loading data from Railway database...")
    master, events = create_master_dataset_from_db()

    print(f"Master dataset created: {len(master)} rows, {master['person_id'].nunique()} unique people, {master['event_id'].nunique()} events")

    print("\nRunning analyses...")

    # 1. Retention analysis
    print("1. Analyzing retention by event...")
    retention_df = retention_analysis(master, events, outdir)

    # 2. New attendees analysis
    print("2. Analyzing new attendees...")
    new_by_event, new_by_category = new_members_analysis(master, events, outdir)

    # 3. Party analysis
    print("3. Analyzing big parties...")
    party_df = party_analysis(master, events, outdir)

    # 4. RSVP conversion (two versions)
    print("4. Analyzing RSVP conversion...")
    conversion_stats = rsvp_conversion_analysis(master, outdir)

    # 5. Generate summary stats
    print("5. Generating summary statistics...")
    summary_stats = generate_summary_stats(master, outdir)

    # 6. Save attendance data back to database
    print("6. Saving attendance data to database...")
    save_attendance_to_db(master, events)

    print(f"\n✅ Analysis complete! All outputs saved to: {outdir.resolve()}")
    print("\nGenerated files:")
    for file in sorted(outdir.glob('*')):
        print(f"  - {file.name}")

    logging.info("=" * 60)
    logging.info("Event Analytics Cron Job Completed Successfully")
    logging.info(f"Output directory: {outdir.resolve()}")
    logging.info(f"Files generated: {len(list(outdir.glob('*')))}")
    logging.info("=" * 60)

    # Notification for monitoring
    print(f"\n[NOTIFICATION] Analytics job completed successfully at {datetime.now().isoformat()}")

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logging.error("=" * 60)
        logging.error(f"Event Analytics Cron Job Failed: {str(e)}")
        logging.error("=" * 60)
        print(f"\n[NOTIFICATION] Analytics job FAILED at {datetime.now().isoformat()}: {str(e)}")
        raise
