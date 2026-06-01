#!/usr/bin/env python3
"""
Luma Attendance JSON Importer
Processes Luma attendance JSON data and imports to database
Mimics logic from raw_csv_to_sql.py for person matching and data import
"""

import os
import sys
import json
import logging
import argparse
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

# JSON field mappings for Luma API response
# These map our internal field names to the Luma API JSON field names
# NOTE: Custom fields (gender, school, class_year) are in registration_answers, not top-level
JSON_FIELD_MAPPING = {
    'first_name': 'user_first_name',
    'last_name': 'user_last_name',
    'name': 'user_name',
    'email': 'email',
    'phone': 'phone_number',
    'approved': 'approval_status',
    'checked_in': 'checked_in_at',  # Luma uses timestamp, not boolean
    'rsvp_datetime': 'created_at',
    'tracking_link': 'custom_source',  # Luma's field for referral/invite token
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


def safe_get_field(data, field_name, default=None):
    """
    Safely get field from JSON data dict, return default if doesn't exist.

    Args:
        data: Dictionary to get field from
        field_name: Name of field to retrieve
        default: Default value if field doesn't exist (default: None)

    Returns:
        Field value or default
    """
    return data.get(field_name, default)


def na_to_none(value):
    """Convert None, empty string, or whitespace-only string to None"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


def get_registration_answer(guest_data, question_label, case_sensitive=False):
    """
    Extract answer from registration_answers array by question label.

    Custom fields in Luma API are stored in a registration_answers array like:
    [
        {"label": "School email (.edu)", "value": "student@harvard.edu"},
        {"label": "What brings you to Camel?", "value": "..."}
    ]

    Args:
        guest_data: Guest data dictionary from Luma API
        question_label: The question label to search for
        case_sensitive: If True, use exact case matching (default: False)

    Returns:
        The answer value if found, None otherwise
    """
    registration_answers = guest_data.get('registration_answers', [])

    for answer in registration_answers:
        answer_label = answer.get('label', '')

        if case_sensitive:
            if answer_label == question_label:
                return na_to_none(answer.get('value'))
        else:
            if answer_label.lower() == question_label.lower():
                return na_to_none(answer.get('value'))

    return None


def get_all_registration_answers(guest_data):
    """
    Extract all registration answers as a dictionary for storage in additional_info.

    Args:
        guest_data: Guest data dictionary from Luma API

    Returns:
        Dictionary mapping question labels to answer values
    """
    registration_answers = guest_data.get('registration_answers', [])
    answers_dict = {}

    for answer in registration_answers:
        label = answer.get('label')
        value = na_to_none(answer.get('value'))

        if label and value is not None:
            answers_dict[label] = value

    return answers_dict if answers_dict else None


def split_full_name(full_name):
    """
    Split a full name into first_name and last_name.
    Uses "first word = first_name, rest = last_name" approach.

    Args:
        full_name: Full name string (e.g., "John Paul Smith")

    Returns:
        tuple: (first_name, last_name)

    Examples:
        "John Smith" -> ("John", "Smith")
        "John Paul Smith" -> ("John", "Paul Smith")
        "John" -> ("John", None)
        "" -> (None, None)
    """
    if not full_name or not isinstance(full_name, str):
        return None, None

    full_name = full_name.strip()
    if not full_name:
        return None, None

    # Split on whitespace
    parts = full_name.split(None, 1)  # Split on first whitespace only

    if len(parts) == 0:
        return None, None
    elif len(parts) == 1:
        # Single name - use as first name
        return parts[0].strip().title(), None
    else:
        # Multiple parts - first word is first_name, rest is last_name
        return parts[0].strip().title(), parts[1].strip().title()



def normalize_gender(gender_str):
    """
    Normalize gender string to M/F/None
    From raw_csv_to_sql.py logic
    """
    if not gender_str:
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
    if not school_str:
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
    if not year_str:
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
    if not sheet_first:
        sheet_first = ""
    if not input_first:
        input_first = ""

    updates = {}

    if sheet_first.lower() in input_first.lower() or input_first.lower() in sheet_first.lower():
        longer_first = max(sheet_first, input_first, key=len)
        updates['first_name'] = longer_first

    if sheet_last and input_last:
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
    if not link_value:
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
    if not email:
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
    if not phone:
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
    if not first_name or not last_name:
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
    import json

    # Convert additional_info dict to JSON string if present
    additional_info_json = None
    if row_data.get('additional_info'):
        additional_info_json = json.dumps(row_data['additional_info'])

    cursor.execute("""
        INSERT INTO people (
            first_name,
            last_name,
            gender,
            class_year,
            school,
            preferred_name,
            additional_info
        ) VALUES (%s, %s, %s, %s, %s, NULL, %s)
        RETURNING id
    """, (
        row_data['first_name'],
        row_data['last_name'],
        row_data['gender'],
        row_data['class_year'],
        row_data['school'],
        additional_info_json
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
    import json

    school_email = row_data.get('school_email')
    personal_email = row_data.get('personal_email')
    phone = row_data.get('phone')
    additional_info = row_data.get('additional_info')

    # Convert additional_info dict to JSON string if present
    additional_info_json = None
    if additional_info:
        additional_info_json = json.dumps(additional_info)

    # Check current values before update
    cursor.execute("""
        SELECT school_email, personal_email, phone_number, additional_info
        FROM people
        WHERE id = %s
    """, (person_id,))
    current = cursor.fetchone()

    if not current:
        return False

    current_school_email, current_personal_email, current_phone, current_additional_info = current

    # Update contact info
    cursor.execute("""
        UPDATE people
        SET
            school_email = COALESCE(school_email, %s),
            personal_email = COALESCE(personal_email, %s),
            phone_number = COALESCE(phone_number, %s),
            additional_info = COALESCE(additional_info, %s)
        WHERE id = %s
    """, (school_email, personal_email, phone, additional_info_json, person_id))

    # Check if any field was updated (was NULL and now has a value)
    updated = False
    if current_school_email is None and school_email is not None:
        updated = True
    if current_personal_email is None and personal_email is not None:
        updated = True
    if current_phone is None and phone is not None:
        updated = True
    if current_additional_info is None and additional_info_json is not None:
        updated = True

    return updated


def find_or_create_person(conn, cursor, guest_data):
    """
    Find existing person or create new one
    Multi-strategy matching: email -> phone -> exact name -> fuzzy name -> create
    From raw_csv_to_sql.py logic

    Args:
        conn: Database connection (needed for update_names_if_substring)
        cursor: Database cursor
        guest_data: Guest data dictionary from JSON
    """
    # Extract data from guest_data using JSON field mappings
    first_name = na_to_none(guest_data.get(JSON_FIELD_MAPPING['first_name']))
    last_name = na_to_none(guest_data.get(JSON_FIELD_MAPPING['last_name']))
    full_name = na_to_none(guest_data.get(JSON_FIELD_MAPPING.get('name')))
    email = na_to_none(guest_data.get(JSON_FIELD_MAPPING['email']))
    phone = na_to_none(guest_data.get(JSON_FIELD_MAPPING.get('phone')))

    # If first_name and last_name are blank, try splitting the full name
    if not first_name and not last_name and full_name:
        first_name, last_name = split_full_name(full_name)
        logging.info(f"Split name '{full_name}' into first='{first_name}' last='{last_name}'")

    # Extract school email from registration_answers (prioritize this over main email)
    school_email_from_reg = get_registration_answer(guest_data, 'School email (.edu)')

    # Extract custom fields from registration_answers
    gender_raw = get_registration_answer(guest_data, 'Gender')
    school_raw = get_registration_answer(guest_data, 'School')
    if not school_raw:
        # Try alternate question labels
        school_raw = get_registration_answer(guest_data, 'What school do you go to?')

    year_raw = get_registration_answer(guest_data, 'Grad year')
    if not year_raw:
        # Try alternate question labels
        year_raw = get_registration_answer(guest_data, 'Graduation Year')
        if not year_raw:
            year_raw = get_registration_answer(guest_data, 'Class Year')

    # Skip guests with no name data - database requires NOT NULL for names
    if not first_name and not last_name:
        logging.warning(f"Skipping guest with no name data (email: {email or 'N/A'}, phone: {phone or 'N/A'})")
        return None, False, False

    # Normalize data
    if first_name:
        first_name = first_name.strip().title()
    if last_name:
        last_name = last_name.strip().title()

    # If only one name field is missing, we still can't proceed (both are NOT NULL)
    if not first_name or not last_name:
        logging.warning(f"Skipping guest with incomplete name (first: {first_name or 'N/A'}, last: {last_name or 'N/A'}, email: {email or 'N/A'})")
        return None, False, False

    gender = normalize_gender(gender_raw)

    # Determine which email is school vs personal
    # Priority: 1) school_email from registration_answers, 2) email field if .edu
    if school_email_from_reg:
        school_email_final = school_email_from_reg
        # If main email is different from school email, treat it as personal email
        personal_email_final = email if email and email.lower() != school_email_from_reg.lower() else None
    else:
        # Fall back to checking if main email is a .edu address
        is_school_email = email and '.edu' in str(email).lower()
        if is_school_email:
            school_email_final = email
            personal_email_final = None
        else:
            school_email_final = None
            personal_email_final = email

    # Normalize school and year (use school_email_final for normalization)
    school = normalize_school(school_raw, school_email_final)
    class_year = normalize_class_year(year_raw)

    # Extract all registration answers for storage in additional_info
    additional_info = get_all_registration_answers(guest_data)

    # Prepare row data
    row_data = {
        'first_name': first_name,
        'last_name': last_name,
        'school_email': school_email_final,
        'personal_email': personal_email_final,
        'phone': str(phone).strip() if phone else None,
        'gender': gender,
        'school': school,
        'class_year': class_year,
        'additional_info': additional_info
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
    if not tracking_link:
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


def create_attendance_record(cursor, person_id, event_id, guest_data):
    """
    Create attendance record for person at event
    From raw_csv_to_sql.py logic

    Returns:
        tuple: (tracking_link, checked_in) for referral tracking
    """
    # Parse attendance fields from JSON
    approved_status = na_to_none(guest_data.get(JSON_FIELD_MAPPING['approved']))
    checked_in_value = na_to_none(guest_data.get(JSON_FIELD_MAPPING['checked_in']))
    rsvp_datetime_str = na_to_none(guest_data.get(JSON_FIELD_MAPPING.get('rsvp_datetime')))
    tracking_link = na_to_none(guest_data.get(JSON_FIELD_MAPPING.get('tracking_link')))

    # Determine rsvp, approved, checked_in booleans
    rsvp = approved_status is not None
    # Luma API uses 'approved' status for completed registrations
    approved = str(approved_status).lower() == 'approved' if approved_status else False
    # checked_in_at is a timestamp - if present and non-empty, person is checked in
    checked_in = bool(checked_in_value) if checked_in_value else False

    # Parse RSVP datetime
    rsvp_datetime = None
    if rsvp_datetime_str:
        try:
            # Parse ISO format datetime
            rsvp_datetime = datetime.fromisoformat(rsvp_datetime_str.replace('Z', '+00:00'))
        except:
            rsvp_datetime = None

    # Get or create invite token
    invite_token_id = find_or_create_invite_token(cursor, event_id, tracking_link)

    # Insert or update attendance record
    # ON CONFLICT DO UPDATE allows re-importing to update checked_in and other status changes
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
        ON CONFLICT (person_id, event_id) DO UPDATE SET
            rsvp = EXCLUDED.rsvp,
            approved = EXCLUDED.approved,
            checked_in = EXCLUDED.checked_in,
            rsvp_datetime = EXCLUDED.rsvp_datetime,
            invite_token_id = EXCLUDED.invite_token_id
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


def process_event_json(event_id, json_path, event_name, log_people=False):
    """
    Process a single event JSON file
    Args:
        event_id: Database event ID
        json_path: Path to JSON file
        event_name: Name of event for logging
        log_people: If True, print person information as each guest is processed
    """
    logging.info(f"Processing JSON for event: {event_name} (ID: {event_id})")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Read JSON file
        with open(json_path, 'r') as f:
            response_data = json.load(f)

        # Extract entries array from response
        # The Luma API returns an 'entries' array where each entry contains a nested 'guest' object
        guests = response_data.get('entries', [])
        logging.info(f"Read {len(guests)} guest entries from JSON")

        # Process each guest
        attendance_count = 0
        processed_count = 0
        new_people_count = 0
        new_contacts_count = 0
        for entry in guests:
            # Extract nested guest object from entry
            # Luma API structure: entries[i].guest contains the actual guest data
            guest_data = entry.get('guest', {})

            # Skip entries that don't have a guest object
            if not guest_data:
                logging.warning(f"Entry missing 'guest' object: {entry.get('api_id', 'unknown')}")
                processed_count += 1
                continue

            # Find or create person
            person_id, was_created, contact_updated = find_or_create_person(conn, cursor, guest_data)

            # Skip if person couldn't be created (missing required data like name)
            if person_id is None:
                processed_count += 1
                continue

            if was_created:
                new_people_count += 1
            if contact_updated:
                new_contacts_count += 1

            # Create attendance record
            tracking_link, checked_in = create_attendance_record(cursor, person_id, event_id, guest_data)
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

                # Determine referral code from invite token
                referral_code = "N/A"
                if tracking_link and str(tracking_link).lower() not in ['default', 'email', 'txt', 'insta', 'maillist']:
                    referral_code = str(tracking_link)

                # Attendance status
                attendance_status = "✓ Attended" if checked_in else "✗ No-show"

                if person_info:
                    logging.info(f"  📋 {person_info['first_name']} {person_info['last_name']} | {person_info['email'] or 'No email'} | {attendance_status} | Referral: {referral_code}")

            # Increment referral count if this person checked in and was referred
            if checked_in:
                referrer_id = None

                # Check tracking link for referral
                if tracking_link:
                    referrer_id = match_tracking_link_to_person(conn, tracking_link)
                    if referrer_id:
                        logging.info(f"  → Tracking link '{tracking_link}' matched to person ID {referrer_id}")

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
                logging.info(f"\n--- Processed {processed_count} guests, refreshing connection ---")
                conn = ensure_connection(conn, force_refresh=True)
                cursor.close()
                cursor = conn.cursor()
            elif processed_count % 10 == 0:
                conn.commit()
                logging.info(f"Processed {processed_count}/{len(guests)} guests...")

        # Final commit
        conn.commit()

        # Update event attendance count using the new function
        final_attendance_count = update_event_attendance_count(conn, event_id)

        # Update person attendance counts for all people who attended this event
        people_updated = update_person_attendance_counts(conn, event_id)

        # Print enhanced statistics
        logging.info(f"\n=== Import Complete for {event_name} ===")
        logging.info(f"Processed: {processed_count} guests")
        logging.info(f"New people: {new_people_count}")
        logging.info(f"New contacts: {new_contacts_count}")
        logging.info(f"Attendance records: {attendance_count}")
        logging.info(f"Event total attendance: {final_attendance_count}")
        logging.info(f"People attendance counts updated: {people_updated}")

    except Exception as e:
        conn.rollback()
        logging.error(f"Error processing JSON for event {event_name}: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

        # Clean up JSON file
        try:
            os.unlink(json_path)
            logging.info(f"Deleted temporary JSON file: {json_path}")
        except:
            pass


def main():
    """Main entry point"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Import Luma attendance JSON data to database'
    )
    parser.add_argument(
        '--log-people',
        action='store_true',
        help='Print detailed person information as each guest is processed'
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
            json_path = event_data['json_path']
            event_name = event_data.get('event_name', f"Event {event_id}")

            process_event_json(event_id, json_path, event_name, log_people=args.log_people)

        logging.info("All events processed successfully")

    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON input: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
