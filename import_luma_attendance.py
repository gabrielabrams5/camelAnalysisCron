#!/usr/bin/env python3
"""
Luma Attendance CSV Importer
Processes Luma attendance CSVs and imports to database
Mimics logic from raw_csv_to_sql.py for person matching and data import
"""

import os
import sys
import json
import logging
import argparse
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from difflib import SequenceMatcher
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Load environment variables
load_dotenv()

# Database connection parameters
DB_CONFIG = {
    'host': os.getenv('PGHOST'),
    'port': os.getenv('PGPORT'),
    'database': os.getenv('PGDATABASE'),
    'user': os.getenv('PGUSER'),
    'password': os.getenv('PGPASSWORD')
}

# CSV column name mappings (update based on actual Luma CSV format)
COLUMN_MAPPING = {
    'first_name': 'First Name',
    'last_name': 'Last Name',
    'email': 'Email',
    'school_email': 'What is your school email?',
    'phone': 'Phone Number',
    'approved': 'Order Status',
    'checked_in': 'Tickets Scanned',
    'rsvp_datetime': 'Order Date/Time',
    'tracking_link': 'Tracking Link',
    'gender': 'Detected Gender',
    'school': 'What school do you go to?',
    'class_year': 'What is your graduation year?',
}

# Fuzzy matching threshold
FUZZY_MATCH_THRESHOLD = 0.80

# Connection refresh interval (refresh every N rows to prevent timeout)
CONNECTION_REFRESH_INTERVAL = 50


def get_db_connection():
    """Create and return a database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logging.error(f"Failed to connect to database: {e}")
        sys.exit(1)


def ensure_connection(conn, force_refresh=False):
    """
    Test and refresh connection if needed. Returns valid connection.

    Args:
        conn: Current database connection
        force_refresh: If True, force a connection refresh

    Returns:
        Valid database connection
    """
    if force_refresh:
        logging.info("🔄 Refreshing connection...")
        try:
            # Commit any pending work before closing
            conn.commit()
            conn.close()
        except (psycopg2.Error, Exception) as e:
            # Connection already closed or in bad state
            pass
        new_conn = get_db_connection()
        logging.info("✓ Connection refreshed successfully")
        return new_conn

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        return conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        logging.warning("⚠️  Connection lost, reconnecting...")
        try:
            conn.close()
        except (psycopg2.Error, Exception) as e:
            # Connection already closed
            pass
        return get_db_connection()


def safe_get_column(df, column_name, default=pd.NA):
    """
    Safely get column from dataframe, return default if doesn't exist.

    Args:
        df: DataFrame to get column from
        column_name: Name of column to retrieve
        default: Default value if column doesn't exist (default: pd.NA)

    Returns:
        Series with column data or default values
    """
    if column_name in df.columns:
        return df[column_name]
    return pd.Series([default] * len(df))


def na_to_none(value):
    """Convert pandas NA/NaN to None"""
    if pd.isna(value):
        return None
    return value


def normalize_gender(gender_str):
    """
    Normalize gender string to M/F/None
    From raw_csv_to_sql.py logic
    """
    if pd.isna(gender_str):
        return None

    gender_lower = str(gender_str).lower().strip()

    if gender_lower in ['f', 'female', 'woman', 'girl']:
        return 'F'
    elif gender_lower in ['m', 'male', 'man', 'boy']:
        return 'M'
    else:
        return None


def normalize_school(school_str, email_str=None):
    """
    Normalize school string to harvard/mit/other/None
    From raw_csv_to_sql.py logic
    """
    # First check email domain (highest priority)
    if email_str and isinstance(email_str, str):
        email_lower = email_str.lower()
        if '@harvard.edu' in email_lower or '@college.harvard.edu' in email_lower:
            return 'harvard'
        elif '@mit.edu' in email_lower:
            return 'mit'
        elif '.edu' in email_lower:
            return 'other'

    # Then check school string
    if pd.isna(school_str):
        return None

    school_lower = str(school_str).lower()

    # Check for Harvard (but not business school)
    if 'harvard' in school_lower:
        if 'business' not in school_lower and 'hbs' not in school_lower:
            return 'harvard'
        else:
            return 'other'
    elif 'mit' in school_lower:
        return 'mit'
    elif school_lower:  # Non-empty string
        return 'other'

    return None


def normalize_class_year(year_str):
    """
    Parse class year from various formats
    From raw_csv_to_sql.py logic
    """
    if pd.isna(year_str):
        return None

    year_str = str(year_str).lower().strip()

    # Try 4-digit year
    if year_str.isdigit() and len(year_str) == 4:
        return int(year_str)

    # Try 2-digit with apostrophe (e.g., '27)
    if year_str.startswith("'") and year_str[1:].isdigit():
        return 2000 + int(year_str[1:])

    # Calculate current academic year
    now = datetime.now()
    current_year = now.year if now.month >= 9 else now.year - 1

    # Grade levels
    if any(x in year_str for x in ['freshman', 'first', '1st']):
        return current_year + 4
    elif any(x in year_str for x in ['sophomore', 'second', '2nd']):
        return current_year + 3
    elif any(x in year_str for x in ['junior', 'third', '3rd']):
        return current_year + 2
    elif any(x in year_str for x in ['senior', 'fourth', '4th']):
        return current_year + 1

    return None


def update_event_attendance_count(conn, event_id):
    """
    Update the attendance count for an event in the events table.

    Counts all attendance records where checked_in = TRUE for the given event
    and updates the events.attendance field with this count.

    Args:
        conn: Database connection
        event_id: ID of the event to update

    Returns:
        count: Number of checked-in attendees
    """
    cursor = conn.cursor()
    try:
        # Count checked-in attendees
        cursor.execute("""
            SELECT COUNT(*)
            FROM attendance
            WHERE event_id = %s AND checked_in = TRUE
        """, (event_id,))

        count = cursor.fetchone()[0]

        # Update events table
        cursor.execute("""
            UPDATE events
            SET attendance = %s
            WHERE id = %s
        """, (count, event_id))

        conn.commit()
        return count
    finally:
        cursor.close()


def update_person_attendance_counts(conn, event_id):
    """
    Update event_attendance_count for all people who attended a specific event.

    For each person who has an attendance record for the given event,
    recalculates their total checked-in attendance count and updates
    the people.event_attendance_count field.

    Args:
        conn: Database connection
        event_id: ID of the event whose attendees should be updated

    Returns:
        count: Number of people records updated
    """
    cursor = conn.cursor()
    try:
        # Update event_attendance_count for all people who have an attendance
        # record for this event (whether checked in or not, to handle updates)
        cursor.execute("""
            UPDATE people
            SET event_attendance_count = (
                SELECT COUNT(*)
                FROM attendance
                WHERE person_id = people.id
                  AND checked_in = TRUE
            )
            WHERE id IN (
                SELECT DISTINCT person_id
                FROM attendance
                WHERE event_id = %s
            )
        """, (event_id,))

        rows_updated = cursor.rowcount
        conn.commit()

        logging.info(f"Updated event_attendance_count for {rows_updated} people")
        return rows_updated
    finally:
        cursor.close()


def update_names_if_substring(conn, person_id, sheet_first, sheet_last, input_first, input_last):
    """
    Update person's name in database to the longer/more complete version.

    If one name is a substring of another (e.g., "Ben" vs "Benjamin"), this function
    updates the database to use the longer, more complete version. This helps maintain
    data quality by preferring full names over nicknames or abbreviated versions.

    Args:
        conn: Database connection
        person_id: ID of the person to update
        sheet_first: First name from database
        sheet_last: Last name from database
        input_first: First name from current input
        input_last: Last name from current input

    Returns:
        None
    """
    if pd.isna(sheet_first) or not sheet_first:
        sheet_first = ""
    if pd.isna(input_first) or not input_first:
        input_first = ""

    updates = {}

    if sheet_first.lower() in input_first.lower() or input_first.lower() in sheet_first.lower():
        longer_first = max(sheet_first, input_first, key=len)
        updates['first_name'] = longer_first

    if pd.notna(sheet_last) and pd.notna(input_last):
        if sheet_last.lower() in input_last.lower() or input_last.lower() in sheet_last.lower():
            longer_last = max(sheet_last, input_last, key=len)
            updates['last_name'] = longer_last

    if updates:
        cursor = conn.cursor()
        try:
            # Use explicit column names for security instead of dynamic SQL
            set_parts = []
            values = []
            for key, value in updates.items():
                if key in ['first_name', 'last_name']:  # Whitelist allowed columns
                    set_parts.append(f"{key} = %s")
                    values.append(value)

            if set_parts:
                set_clause = ', '.join(set_parts)
                values.append(person_id)
                cursor.execute(f"UPDATE people SET {set_clause} WHERE id = %s", values)
                conn.commit()
        finally:
            cursor.close()


def fuzzy_ratio(str_a, str_b):
    """Calculate fuzzy string similarity ratio using SequenceMatcher"""
    return SequenceMatcher(None, str_a, str_b).ratio()


def match_tracking_link_to_person(conn, link_value, fuzzy_threshold=0.8):
    """
    Match a tracking link value to a person in the database using fuzzy matching.

    Args:
        conn: Database connection
        link_value: The tracking link string (e.g., "doron", "[name]", "admlzr")
        fuzzy_threshold: Fuzzy matching threshold (default 0.8)

    Returns:
        person_id if match found, else None
    """
    if not link_value or pd.isna(link_value):
        return None

    # Clean the link value
    link_value = str(link_value).strip().lower()

    # Skip generic tracking codes that don't represent personal referrals
    generic_codes = {
        'default', 'emailreferral', 'email_first_button',
        'email_second_button', 'email', 'txt', 'insta',
        'maillist', 'lastname', '[name]'
    }
    if link_value in generic_codes:
        return None

    # Determine if this is a single word (no underscores or hyphens)
    is_single_word = '_' not in link_value and '-' not in link_value

    # Try to extract a name from the link value
    # Remove common prefixes/suffixes
    clean_name = link_value.replace('_', ' ').replace('-', ' ').strip()

    # Get all people from database
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT id, first_name, last_name FROM people")
        all_people = cursor.fetchall()
    finally:
        cursor.close()

    # Try exact match on first name (and last name only if multi-word)
    for person in all_people:
        first = person['first_name'].lower() if person['first_name'] else ''
        last = person['last_name'].lower() if person['last_name'] else ''

        # Always check first name
        if clean_name == first:
            return person['id']

        # Only check last name if this is a multi-word tracking link
        if not is_single_word and clean_name == last:
            return person['id']

    # Try fuzzy matching on first name (and last name only if multi-word)
    best_match = None
    best_ratio = 0

    for person in all_people:
        first = person['first_name'].lower() if person['first_name'] else ''
        last = person['last_name'].lower() if person['last_name'] else ''

        # Check fuzzy match against first name
        if first:
            ratio = fuzzy_ratio(clean_name, first)
            if ratio >= fuzzy_threshold and ratio > best_ratio:
                best_ratio = ratio
                best_match = person['id']

        # Check fuzzy match against last name only if multi-word
        if not is_single_word and last:
            ratio = fuzzy_ratio(clean_name, last)
            if ratio >= fuzzy_threshold and ratio > best_ratio:
                best_ratio = ratio
                best_match = person['id']

    return best_match


def find_person_by_email(cursor, email):
    """Find person by email (school or personal)"""
    if not email or pd.isna(email):
        return None

    email = email.lower().strip()

    cursor.execute("""
        SELECT id FROM people
        WHERE LOWER(school_email) = %s OR LOWER(personal_email) = %s
    """, (email, email))

    result = cursor.fetchone()
    return result[0] if result else None


def find_person_by_phone(cursor, phone):
    """Find person by phone number"""
    if not phone or pd.isna(phone):
        return None

    phone = str(phone).strip()

    cursor.execute("""
        SELECT id FROM people
        WHERE phone_number = %s
    """, (phone,))

    result = cursor.fetchone()
    return result[0] if result else None


def find_person_by_name(cursor, first_name, last_name):
    """Find person by exact name match"""
    if not first_name or not last_name or pd.isna(first_name) or pd.isna(last_name):
        return None

    first_name = str(first_name).strip().title()
    last_name = str(last_name).strip().title()

    cursor.execute("""
        SELECT id FROM people
        WHERE LOWER(first_name) = LOWER(%s)
        AND LOWER(last_name) = LOWER(%s)
    """, (first_name, last_name))

    results = cursor.fetchall()

    if len(results) == 1:
        return results[0][0]
    elif len(results) > 1:
        # Multiple matches - for automated import, use first one
        logging.warning(f"Multiple matches for {first_name} {last_name}, using first match")
        return results[0][0]

    return None


def fuzzy_match_name(cursor, first_name, last_name):
    """
    Fuzzy match person by name similarity
    From raw_csv_to_sql.py logic
    """
    if not first_name or not last_name:
        return None

    first_name = str(first_name).strip().title()
    last_name = str(last_name).strip().title()

    # Load all people for fuzzy matching
    cursor.execute("""
        SELECT id, first_name, last_name FROM people
    """)

    all_people = cursor.fetchall()
    matches = []

    for person_id, db_first, db_last in all_people:
        # Calculate similarity ratio
        first_ratio = SequenceMatcher(None, first_name.lower(), db_first.lower()).ratio()
        last_ratio = SequenceMatcher(None, last_name.lower(), db_last.lower()).ratio()

        # Average ratio
        avg_ratio = (first_ratio + last_ratio) / 2

        if avg_ratio >= FUZZY_MATCH_THRESHOLD:
            matches.append((person_id, avg_ratio, db_first, db_last))

    if matches:
        # Sort by ratio descending
        matches.sort(key=lambda x: x[1], reverse=True)
        best_match = matches[0]

        if best_match[1] >= 0.90:  # High confidence
            logging.info(f"Fuzzy matched {first_name} {last_name} to {best_match[2]} {best_match[3]} (confidence: {best_match[1]:.2f})")
            return best_match[0]

    return None


def create_person(cursor, row_data):
    """
    Create a new person record
    Args:
        cursor: Database cursor
        row_data: Dictionary with person data
    Returns: person_id
    """
    cursor.execute("""
        INSERT INTO people (
            first_name,
            last_name,
            gender,
            class_year,
            school,
            preferred_name
        ) VALUES (%s, %s, %s, %s, %s, NULL)
        RETURNING id
    """, (
        row_data['first_name'],
        row_data['last_name'],
        row_data['gender'],
        row_data['class_year'],
        row_data['school']
    ))

    person_id = cursor.fetchone()[0]
    logging.info(f"Created new person: {row_data['first_name']} {row_data['last_name']} (ID: {person_id})")
    return person_id


def update_contact_info(cursor, person_id, row_data):
    """
    Update person's contact information using COALESCE pattern
    Only updates fields that are currently NULL
    From raw_csv_to_sql.py logic

    Returns:
        bool: True if any contact info was updated, False otherwise
    """
    school_email = row_data.get('school_email')
    personal_email = row_data.get('personal_email')
    phone = row_data.get('phone')

    # Check current values before update
    cursor.execute("""
        SELECT school_email, personal_email, phone_number
        FROM people
        WHERE id = %s
    """, (person_id,))
    current = cursor.fetchone()

    if not current:
        return False

    current_school_email, current_personal_email, current_phone = current

    # Update contact info
    cursor.execute("""
        UPDATE people
        SET
            school_email = COALESCE(school_email, %s),
            personal_email = COALESCE(personal_email, %s),
            phone_number = COALESCE(phone_number, %s)
        WHERE id = %s
    """, (school_email, personal_email, phone, person_id))

    # Check if any field was updated (was NULL and now has a value)
    updated = False
    if current_school_email is None and school_email is not None:
        updated = True
    if current_personal_email is None and personal_email is not None:
        updated = True
    if current_phone is None and phone is not None:
        updated = True

    return updated


def find_or_create_person(conn, cursor, row):
    """
    Find existing person or create new one
    Multi-strategy matching: email -> phone -> exact name -> fuzzy name -> create
    From raw_csv_to_sql.py logic

    Args:
        conn: Database connection (needed for update_names_if_substring)
        cursor: Database cursor
        row: Row data from CSV
    """
    # Extract data from row
    first_name = na_to_none(row.get(COLUMN_MAPPING['first_name']))
    last_name = na_to_none(row.get(COLUMN_MAPPING['last_name']))
    email = na_to_none(row.get(COLUMN_MAPPING['email']))
    school_email = na_to_none(row.get(COLUMN_MAPPING.get('school_email', 'N/A')))
    phone = na_to_none(row.get(COLUMN_MAPPING.get('phone', 'N/A')))
    gender_raw = na_to_none(row.get(COLUMN_MAPPING.get('gender', 'N/A')))
    school_raw = na_to_none(row.get(COLUMN_MAPPING.get('school', 'N/A')))
    year_raw = na_to_none(row.get(COLUMN_MAPPING.get('class_year', 'N/A')))

    # Normalize data
    if first_name:
        first_name = first_name.strip().title()
    if last_name:
        last_name = last_name.strip().title()

    gender = normalize_gender(gender_raw)

    # Determine which email is school vs personal
    primary_email = school_email if school_email else email
    is_school_email = primary_email and '.edu' in str(primary_email).lower()

    if is_school_email:
        school_email_final = primary_email
        personal_email_final = email if email and email != school_email else None
    else:
        school_email_final = None
        personal_email_final = primary_email

    # Normalize school and year
    school = normalize_school(school_raw, primary_email)
    class_year = normalize_class_year(year_raw)

    # Prepare row data
    row_data = {
        'first_name': first_name,
        'last_name': last_name,
        'school_email': school_email_final,
        'personal_email': personal_email_final,
        'phone': str(phone).strip() if phone else None,
        'gender': gender,
        'school': school,
        'class_year': class_year
    }

    # Try matching strategies in order
    person_id = None
    was_created = False

    # 1. Try email match (prioritize school email)
    if school_email_final:
        person_id = find_person_by_email(cursor, school_email_final)
    if not person_id and personal_email_final:
        person_id = find_person_by_email(cursor, personal_email_final)

    # 2. Try phone match
    if not person_id and phone:
        person_id = find_person_by_phone(cursor, phone)

    # 3. Try exact name match
    if not person_id and first_name and last_name:
        person_id = find_person_by_name(cursor, first_name, last_name)

    # 4. Try fuzzy name match
    if not person_id and first_name and last_name:
        person_id = fuzzy_match_name(cursor, first_name, last_name)

    # 5. Create new person if no match
    if not person_id:
        person_id = create_person(cursor, row_data)
        was_created = True

    # Update contact info regardless (COALESCE will preserve existing)
    contact_updated = update_contact_info(cursor, person_id, row_data)

    # Update names if substring (only for existing persons, not newly created)
    if not was_created and person_id and first_name and last_name:
        # Fetch existing person's name from database
        cursor.execute("""
            SELECT first_name, last_name FROM people WHERE id = %s
        """, (person_id,))
        result = cursor.fetchone()
        if result:
            db_first_name, db_last_name = result
            update_names_if_substring(conn, person_id, db_first_name, db_last_name, first_name, last_name)

    return person_id, was_created, contact_updated


def find_or_create_invite_token(cursor, event_id, tracking_link):
    """
    Find or create invite token
    From raw_csv_to_sql.py logic
    """
    if not tracking_link or pd.isna(tracking_link):
        tracking_link = 'default'

    tracking_link = str(tracking_link).strip()[:100]  # Respect VARCHAR(100) limit

    # Skip generic codes
    generic_codes = ['default', 'emailreferral', 'email', 'instagram', 'facebook']
    if tracking_link.lower() in generic_codes:
        tracking_link = 'default'
        category = 'mailing list'
    else:
        category = 'personal outreach'

    # Check if token exists for this event
    cursor.execute("""
        SELECT id FROM invitetokens
        WHERE event_id = %s AND value = %s
    """, (event_id, tracking_link))

    result = cursor.fetchone()
    if result:
        return result[0]

    # Create new token
    cursor.execute("""
        INSERT INTO invitetokens (event_id, category, value)
        VALUES (%s, %s, %s)
        RETURNING id
    """, (event_id, category, tracking_link))

    token_id = cursor.fetchone()[0]
    return token_id


def create_attendance_record(cursor, person_id, event_id, row):
    """
    Create attendance record for person at event
    From raw_csv_to_sql.py logic

    Returns:
        tuple: (tracking_link, checked_in) for referral tracking
    """
    # Parse attendance fields
    approved_status = na_to_none(row.get(COLUMN_MAPPING['approved']))
    checked_in_value = na_to_none(row.get(COLUMN_MAPPING['checked_in']))
    rsvp_datetime_str = na_to_none(row.get(COLUMN_MAPPING.get('rsvp_datetime', 'N/A')))
    tracking_link = na_to_none(row.get(COLUMN_MAPPING.get('tracking_link', 'N/A')))

    # Determine rsvp, approved, checked_in booleans
    rsvp = approved_status is not None
    approved = str(approved_status).lower() == 'completed' if approved_status else False
    checked_in = str(checked_in_value).lower() in ['1', '1.0', 'true', 'yes'] if checked_in_value else False

    # Parse RSVP datetime
    rsvp_datetime = None
    if rsvp_datetime_str:
        try:
            # Try parsing common formats
            rsvp_datetime = pd.to_datetime(rsvp_datetime_str, errors='coerce')
            if pd.isna(rsvp_datetime):
                rsvp_datetime = None
        except:
            rsvp_datetime = None

    # Get or create invite token
    invite_token_id = find_or_create_invite_token(cursor, event_id, tracking_link)

    # Insert attendance record (ON CONFLICT DO NOTHING prevents duplicates)
    cursor.execute("""
        INSERT INTO attendance (
            person_id,
            event_id,
            rsvp,
            approved,
            checked_in,
            rsvp_datetime,
            is_first_event,
            invite_token_id
        ) VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s)
        ON CONFLICT (person_id, event_id) DO NOTHING
    """, (
        person_id,
        event_id,
        rsvp,
        approved,
        checked_in,
        rsvp_datetime,
        invite_token_id
    ))

    # Update is_first_event flag if this person checked in
    if checked_in:
        # Find the earliest checked-in event for this person
        cursor.execute("""
            SELECT a.event_id
            FROM attendance a
            JOIN events e ON a.event_id = e.id
            WHERE a.person_id = %s AND a.checked_in = TRUE
            ORDER BY e.start_datetime ASC
            LIMIT 1
        """, (person_id,))
        earliest_event = cursor.fetchone()

        if earliest_event:
            earliest_event_id = earliest_event[0]

            # Set is_first_event = TRUE for earliest event only
            cursor.execute("""
                UPDATE attendance
                SET is_first_event = TRUE
                WHERE person_id = %s AND event_id = %s
            """, (person_id, earliest_event_id))

            # Set is_first_event = FALSE for all other events
            cursor.execute("""
                UPDATE attendance
                SET is_first_event = FALSE
                WHERE person_id = %s AND event_id != %s
            """, (person_id, earliest_event_id))

    return tracking_link, checked_in


def process_event_csv(event_id, csv_path, event_name, log_people=False):
    """
    Process a single event CSV file
    Args:
        event_id: Database event ID
        csv_path: Path to CSV file
        event_name: Name of event for logging
        log_people: If True, print person information as each row is processed
    """
    logging.info(f"Processing CSV for event: {event_name} (ID: {event_id})")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Read CSV
        df = pd.read_csv(csv_path)
        logging.info(f"Read {len(df)} rows from CSV")

        # Detect referral column if it exists
        referral_column = None
        possible_referral_columns = [
            'How did you hear about this event?',
            'Who referred you?',
            'Referral',
            'Referred by'
        ]
        for col in possible_referral_columns:
            if col in df.columns:
                referral_column = col
                logging.info(f"Detected referral column: {referral_column}")
                break

        # Process each row
        attendance_count = 0
        processed_count = 0
        new_people_count = 0
        new_contacts_count = 0
        for idx, row in df.iterrows():
            # Find or create person
            person_id, was_created, contact_updated = find_or_create_person(conn, cursor, row)
            if was_created:
                new_people_count += 1
            if contact_updated:
                new_contacts_count += 1

            # Create attendance record
            tracking_link, checked_in = create_attendance_record(cursor, person_id, event_id, row)
            attendance_count += 1

            # Log person information if logging is enabled
            if log_people and person_id:
                # Get person info with email
                log_cursor = conn.cursor(cursor_factory=RealDictCursor)
                try:
                    log_cursor.execute("""
                        SELECT
                            first_name,
                            last_name,
                            COALESCE(school_email, personal_email) as email
                        FROM people
                        WHERE id = %s
                    """, (person_id,))
                    person_info = log_cursor.fetchone()
                finally:
                    log_cursor.close()

                # Determine referral code from invite token or referral column
                referral_code = "N/A"
                if tracking_link and not pd.isna(tracking_link) and str(tracking_link).lower() not in ['default', 'email', 'txt', 'insta', 'maillist']:
                    referral_code = str(tracking_link)
                elif referral_column and referral_column in row.index and not pd.isna(row[referral_column]):
                    referral_code = str(row[referral_column])

                # Attendance status
                attendance_status = "✓ Attended" if checked_in else "✗ No-show"

                if person_info:
                    logging.info(f"  📋 {person_info['first_name']} {person_info['last_name']} | {person_info['email'] or 'No email'} | {attendance_status} | Referral: {referral_code}")

            # Increment referral count if this person checked in and was referred
            if checked_in:
                referrer_id = None

                # 1. Check tracking link for referral
                if tracking_link and not pd.isna(tracking_link):
                    referrer_id = match_tracking_link_to_person(conn, tracking_link)
                    if referrer_id:
                        logging.info(f"  → Tracking link '{tracking_link}' matched to person ID {referrer_id}")

                # 2. Check referral column if it exists
                if referral_column and referral_column in row.index and not pd.isna(row[referral_column]):
                    referrer_name = str(row[referral_column]).strip()
                    # Try to match the referrer name to a person
                    # Use fuzzy matching on the referrer name
                    referrer_matches = fuzzy_match_name(cursor, referrer_name, "")
                    if referrer_matches:
                        referrer_id = referrer_matches
                        logging.info(f"  → Referral column '{referrer_name}' matched to person ID {referrer_id}")

                # Increment referral count
                if referrer_id and referrer_id != person_id:  # Don't count self-referrals
                    cursor.execute("""
                        UPDATE people
                        SET referral_count = referral_count + 1
                        WHERE id = %s
                    """, (referrer_id,))
                    logging.info(f"  ✓ Incremented referral_count for person ID {referrer_id}")

            processed_count += 1

            # Refresh connection periodically to prevent timeouts
            if processed_count % CONNECTION_REFRESH_INTERVAL == 0:
                logging.info(f"\n--- Processed {processed_count} rows, refreshing connection ---")
                conn = ensure_connection(conn, force_refresh=True)
                cursor.close()
                cursor = conn.cursor()
            elif processed_count % 10 == 0:
                conn.commit()
                logging.info(f"Processed {processed_count}/{len(df)} rows...")

        # Final commit
        conn.commit()

        # Update event attendance count using the new function
        final_attendance_count = update_event_attendance_count(conn, event_id)

        # Update person attendance counts for all people who attended this event
        people_updated = update_person_attendance_counts(conn, event_id)

        # Print enhanced statistics
        logging.info(f"\n=== Import Complete for {event_name} ===")
        logging.info(f"Processed: {processed_count} rows")
        logging.info(f"New people: {new_people_count}")
        logging.info(f"New contacts: {new_contacts_count}")
        logging.info(f"Attendance records: {attendance_count}")
        logging.info(f"Event total attendance: {final_attendance_count}")
        logging.info(f"People attendance counts updated: {people_updated}")

    except Exception as e:
        conn.rollback()
        logging.error(f"Error processing CSV for event {event_name}: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

        # Clean up CSV file
        try:
            os.unlink(csv_path)
            logging.info(f"Deleted temporary CSV file: {csv_path}")
        except:
            pass


def main():
    """Main entry point"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Import Luma attendance CSVs to database'
    )
    parser.add_argument(
        '--log-people',
        action='store_true',
        help='Print detailed person information as each row is processed'
    )
    args = parser.parse_args()

    # Read JSON from stdin (output from luma_sync.py)
    try:
        input_data = sys.stdin.read()
        if not input_data.strip():
            logging.info("No input data provided")
            sys.exit(0)

        events = json.loads(input_data)

        if not events:
            logging.info("No events to process")
            sys.exit(0)

        logging.info(f"Processing {len(events)} events")

        for event_data in events:
            event_id = event_data['event_id']
            csv_path = event_data['csv_path']
            event_name = event_data.get('event_name', f"Event {event_id}")

            process_event_csv(event_id, csv_path, event_name, log_people=args.log_people)

        logging.info("All events processed successfully")

    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON input: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
