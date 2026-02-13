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
import pandas as pd
import psycopg2
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


def get_db_connection():
    """Create and return a database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logging.error(f"Failed to connect to database: {e}")
        sys.exit(1)


def safe_get_column(df, column_name, default=''):
    """Safely get column from dataframe, return default if doesn't exist"""
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
    """
    school_email = row_data.get('school_email')
    personal_email = row_data.get('personal_email')
    phone = row_data.get('phone')

    cursor.execute("""
        UPDATE people
        SET
            school_email = COALESCE(school_email, %s),
            personal_email = COALESCE(personal_email, %s),
            phone_number = COALESCE(phone_number, %s)
        WHERE id = %s
    """, (school_email, personal_email, phone, person_id))


def find_or_create_person(cursor, row):
    """
    Find existing person or create new one
    Multi-strategy matching: email -> phone -> exact name -> fuzzy name -> create
    From raw_csv_to_sql.py logic
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

    # Update contact info regardless (COALESCE will preserve existing)
    update_contact_info(cursor, person_id, row_data)

    return person_id


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


def process_event_csv(event_id, csv_path, event_name):
    """
    Process a single event CSV file
    Args:
        event_id: Database event ID
        csv_path: Path to CSV file
        event_name: Name of event for logging
    """
    logging.info(f"Processing CSV for event: {event_name} (ID: {event_id})")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Read CSV
        df = pd.read_csv(csv_path)
        logging.info(f"Read {len(df)} rows from CSV")

        # Process each row
        attendance_count = 0
        for idx, row in df.iterrows():
            # Find or create person
            person_id = find_or_create_person(cursor, row)

            # Create attendance record
            create_attendance_record(cursor, person_id, event_id, row)
            attendance_count += 1

            # Commit periodically to prevent connection timeout
            if (idx + 1) % 50 == 0:
                conn.commit()
                logging.info(f"Processed {idx + 1}/{len(df)} rows")

        # Final commit
        conn.commit()

        # Update event attendance count
        cursor.execute("""
            UPDATE events
            SET attendance = (
                SELECT COUNT(*) FROM attendance
                WHERE event_id = %s AND checked_in = TRUE
            )
            WHERE id = %s
        """, (event_id, event_id))

        conn.commit()

        logging.info(f"Successfully processed {attendance_count} attendees for event {event_name}")

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

            process_event_csv(event_id, csv_path, event_name)

        logging.info("All events processed successfully")

    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON input: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
