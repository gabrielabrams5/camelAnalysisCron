#!/usr/bin/env python3
"""
Import Event Data to PostgreSQL Database

This script imports new event attendee data from CSV files into the PostgreSQL database.

Usage:
    python raw_csv_to_sql.py <csv_file_path>
"""

import sys
import argparse
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
from difflib import SequenceMatcher
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Load environment variables
load_dotenv()

# Helper function to convert pandas NA to Python None for psycopg2
def na_to_none(val):
    """Convert pandas NA values to Python None for database compatibility."""
    if pd.isna(val):
        return None
    return val

def safe_get_column(df, column_name, default=pd.NA):
    """Safely get a column from DataFrame, returning default if column doesn't exist."""
    if column_name in df.columns:
        return df[column_name]
    return pd.Series([default] * len(df), index=df.index)

# Validate required environment variables
def validate_env_vars():
    """Validate that all required environment variables are set."""
    required_vars = ['PGHOST', 'PGPASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing_vars)}. "
            f"Please set them in your .env file or environment."
        )

# Validate environment variables at module load
validate_env_vars()

# Database connection parameters
DB_CONFIG = {
    'host': os.getenv('PGHOST'),
    'port': os.getenv('PGPORT', 58300),
    'database': os.getenv('PGDATABASE', 'postgres'),
    'user': os.getenv('PGUSER', 'postgres'),
    'password': os.getenv('PGPASSWORD'),
    'connect_timeout': 10  # 10 second timeout for connection attempts
}

def get_db_connection():
    """Create a new database connection."""
    return psycopg2.connect(**DB_CONFIG)

def ensure_connection(conn, force_refresh=False):
    """Test and refresh connection if needed. Returns valid connection."""
    if force_refresh:
        print("🔄 Refreshing connection...")
        try:
            # Commit any pending work before closing
            conn.commit()
            conn.close()
        except (psycopg2.Error, Exception) as e:
            # Connection already closed or in bad state
            pass
        new_conn = get_db_connection()
        print("✓ Connection refreshed successfully")
        return new_conn

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        print("⚠️  Connection lost, reconnecting...")
        try:
            conn.close()
        except (psycopg2.Error, Exception) as e:
            # Connection already closed
            pass
        return get_db_connection()

# Import all helper functions from notebook
def fuzzy_ratio(str_a, str_b):
    return SequenceMatcher(None, str_a, str_b).ratio()

def is_initial(name):
    cleaned = name.strip().lower()
    if len(cleaned) == 1 and cleaned.isalpha():
        return True
    if len(cleaned) == 2 and cleaned[0].isalpha() and cleaned[1] == ".":
        return True
    return False

def compare_names(fn_ta, ln_ta, fn_sheet, ln_sheet, fuzzy_threshold):
    """Compare two name pairs and return matching verdict."""
    fn_ta = str(fn_ta) if pd.notna(fn_ta) else ""
    ln_ta = str(ln_ta) if pd.notna(ln_ta) else ""
    fn_sheet = str(fn_sheet) if pd.notna(fn_sheet) else ""
    ln_sheet = str(ln_sheet) if pd.notna(ln_sheet) else ""

    if fn_ta == fn_sheet and ln_ta == ln_sheet:
        return "auto_accept"
    if (fn_ta in fn_sheet and ln_sheet == ln_ta) or (fn_sheet in fn_ta and ln_ta == ln_sheet):
        return "auto_accept"
    if (fn_ta == fn_sheet and ln_ta in ln_sheet) or (fn_sheet == fn_ta and ln_sheet in ln_ta):
        return "auto_accept"

    fn_ta_is_initial = is_initial(fn_ta)
    ln_ta_is_initial = is_initial(ln_ta)

    if fn_ta_is_initial or ln_ta_is_initial:
        if fn_ta_is_initial:
            letter = fn_ta[0].lower()
            if not fn_sheet.startswith(letter):
                return "reject_now"
        if ln_ta_is_initial:
            letter = ln_ta[0].lower()
            if not ln_sheet.startswith(letter):
                return "reject_now"

        if fn_ta_is_initial and not ln_ta_is_initial:
            ratio_last = fuzzy_ratio(ln_ta, ln_sheet)
            if ratio_last >= fuzzy_threshold:
                return "manual_review"
            else:
                return "reject_now"
        elif ln_ta_is_initial and not fn_ta_is_initial:
            ratio_first = fuzzy_ratio(fn_ta, fn_sheet)
            if ratio_first >= fuzzy_threshold:
                return "manual_review"
            else:
                return "reject_now"
        else:
            return "manual_review"

    exact_first = (fn_ta == fn_sheet)
    exact_last = (ln_ta == ln_sheet)
    ratio_first = fuzzy_ratio(fn_ta, fn_sheet)
    ratio_last = fuzzy_ratio(ln_ta, ln_sheet)

    if exact_first and ratio_last >= fuzzy_threshold:
        print(f"Matching {fn_ta} {ln_ta} to {fn_sheet} {ln_sheet}")
        return "auto_accept"
    if exact_last and ratio_first >= fuzzy_threshold:
        print(f"Matching {fn_ta} {ln_ta} to {fn_sheet} {ln_sheet}")
        return "auto_accept"

    if ratio_first >= fuzzy_threshold and ratio_last >= fuzzy_threshold:
        return "manual_review"

    return "reject_now"

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
        with conn.cursor() as cur:
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
                cur.execute(f"UPDATE People SET {set_clause} WHERE id = %s", values)
        conn.commit()

def find_person_id(row, conn, email_col=None, phone_col=None, handle_indices_list=None, fuzzy_threshold=0.80):
    """
    Find a person's ID in the database using multiple matching strategies.

    Matching is attempted in order of confidence:
    1. Email match (exact, case-insensitive)
    2. Phone number match (exact)
    3. Exact name match (first + last name)
    4. Fuzzy name match using Sequence Matcher algorithm

    For fuzzy matches, the user may be prompted to manually select from
    multiple candidates or confirm uncertain matches.

    Args:
        row: Dictionary-like object with keys: first_name, last_name, and optionally email/phone
        conn: Database connection
        email_col: Column name for email in the row (optional)
        phone_col: Column name for phone in the row (optional)
        handle_indices_list: List to append unmatched names to (optional)
        fuzzy_threshold: Similarity threshold for fuzzy matching (0.0-1.0, default 0.80)

    Returns:
        int: Person ID if match found, None otherwise
    """

    first_name = row["first_name"].strip().lower() if not pd.isna(row["first_name"]) else None
    last_name = row.get("last_name")
    last_name = last_name.strip().lower() if pd.notna(last_name) else None

    # Email matching
    if email_col and email_col in row and pd.notna(row[email_col]):
        email = row[email_col]
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, first_name, last_name FROM People WHERE LOWER(school_email) = LOWER(%s) OR LOWER(personal_email) = LOWER(%s)",
                (email, email)
            )
            person = cur.fetchone()
            if person:
                person_id = person['id']
                update_names_if_substring(conn, person_id, person['first_name'], person['last_name'], first_name, last_name)
                return person_id
        print(f"Could not find person with email: {email}")
    else:
        email = None

    # Phone matching
    if phone_col and phone_col in row and pd.notna(row[phone_col]):
        phone = row[phone_col]
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, first_name, last_name FROM People WHERE LOWER(phone_number) = LOWER(%s)",
                (phone,)
            )
            person = cur.fetchone()
            if person:
                person_id = person['id']
                update_names_if_substring(conn, person_id, person['first_name'], person['last_name'], first_name, last_name)
                return person_id
        print(f"Could not find person with phone: {phone}")

    if not first_name:
        return None

    # Exact name matching
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if last_name:
            cur.execute(
                "SELECT * FROM People WHERE LOWER(first_name) = %s AND LOWER(last_name) = %s",
                (first_name, last_name)
            )
        else:
            cur.execute(
                "SELECT * FROM People WHERE LOWER(first_name) = %s",
                (first_name,)
            )
        potentials = cur.fetchall()

    if len(potentials) == 1:
        person = potentials[0]
        update_names_if_substring(conn, person['id'], person['first_name'], person['last_name'], first_name, last_name)
        return person['id']

    elif len(potentials) > 1:
        options = [
            f"{i} => {p['first_name']} {p['last_name']} (gender={p['gender']}, jewish={p['is_jewish']})"
            for i, p in enumerate(potentials)
        ]
        options_str = "\n".join(options)
        choice = input(f"Multiple exact matches for '{first_name} {last_name or ''}' and email {email}. Choose one:\n\n{options_str}\n\nSelect index or 'n' to skip: ")
        if choice.lower() == "n":
            handle_indices_list.append((first_name, last_name))
            return None
        try:
            selected = potentials[int(choice)]
            person_id = selected['id']
            update_names_if_substring(conn, person_id, selected['first_name'], selected['last_name'], first_name, last_name)
            return person_id
        except (ValueError, IndexError, KeyError) as e:
            print(f"Invalid choice: {e}. Skipping.")
            handle_indices_list.append((first_name, last_name))
            return None

    # Fuzzy matching
    # NOTE: This query loads all people for fuzzy name matching. For databases with
    # 100K+ people, consider implementing PostgreSQL pg_trgm extension for faster
    # similarity search, or implementing pagination. For typical university org size
    # (<10K people), this approach is acceptable.
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Only select columns needed for matching (performance optimization)
        cur.execute("""
            SELECT id, first_name, last_name, gender, is_jewish
            FROM People
        """)
        all_people = cur.fetchall()

    auto_accepts, manual_reviews = [], []
    for candidate in all_people:
        verdict = compare_names(
            first_name,
            (last_name or ""),
            candidate["first_name"],
            candidate["last_name"] if candidate["last_name"] else "",
            fuzzy_threshold,
        )
        if verdict == "auto_accept":
            auto_accepts.append(candidate)
        elif verdict == "manual_review":
            manual_reviews.append(candidate)

    if len(auto_accepts) == 1:
        person = auto_accepts[0]
        update_names_if_substring(conn, person['id'], person['first_name'], person['last_name'], first_name, last_name)
        return person['id']

    if len(auto_accepts) > 1:
        options = [
            f"{i} => {p['first_name']} {p['last_name']} (gender={p['gender']}, jewish={p['is_jewish']})"
            for i, p in enumerate(auto_accepts)
        ]
        options_str = "\n".join(options)
        choice = input(f"Multiple 'auto_accept' matches for '{first_name} {last_name or ''}' and email {email}. Choose one:\n\n{options_str}\n\nSelect index or 'n' to skip: ")
        if choice.lower() == "n":
            handle_indices_list.append((first_name, last_name))
            return None
        try:
            person = auto_accepts[int(choice)]
            update_names_if_substring(conn, person['id'], person['first_name'], person['last_name'], first_name, last_name)
            return person['id']
        except (ValueError, IndexError, KeyError) as e:
            print(f"Invalid choice: {e}. Skipping.")
            handle_indices_list.append((first_name, last_name))
            return None

    if manual_reviews:
        options = [
            f"{i} => {p['first_name']} {p['last_name']} (gender={p['gender']}, jewish={p['is_jewish']})"
            for i, p in enumerate(manual_reviews)
        ]
        options_str = "\n".join(options)
        choice = input(f"No auto-accept found for '{first_name} {last_name or ''}', but possible matches:\n\n{options_str}\n\nSelect index or 'n' to skip: ")
        if choice.lower() == "n":
            handle_indices_list.append((first_name, last_name))
            return None
        try:
            person = manual_reviews[int(choice)]
            update_names_if_substring(conn, person['id'], person['first_name'], person['last_name'], first_name, last_name)
            return person['id']
        except (ValueError, IndexError, KeyError) as e:
            print(f"Invalid choice: {e}. Skipping.")
            handle_indices_list.append((first_name, last_name))
            return None

    print(f"No match found for '{first_name} {last_name or ''}'.")
    handle_indices_list.append((first_name, last_name))
    return None

def update_contact_info(conn, person_id, school_email, personal_email, phone):
    """
    Update contact information for a person in the People table.

    Args:
        conn: Database connection
        person_id: ID of the person to update
        school_email: School email address (or empty string)
        personal_email: Personal email address (or empty string)
        phone: Phone number (or empty string)

    Returns:
        Number of contact fields updated
    """
    with conn.cursor() as cur:
        # Build update query with explicit parameterization
        set_parts = []
        params = []

        if school_email:
            set_parts.append("school_email = COALESCE(school_email, %s)")
            params.append(school_email)

        if personal_email and personal_email != school_email:
            if ".edu" in personal_email:
                set_parts.append("school_email = COALESCE(school_email, %s)")
                params.append(personal_email)
            else:
                set_parts.append("personal_email = COALESCE(personal_email, %s)")
                params.append(personal_email)

        if phone:
            set_parts.append("phone_number = COALESCE(phone_number, %s)")
            params.append(phone)

        if set_parts:
            params.append(person_id)
            query = f"UPDATE People SET {', '.join(set_parts)} WHERE id = %s"
            cur.execute(query, params)

    return len(set_parts)

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
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, first_name, last_name FROM People")
        all_people = cur.fetchall()

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

def normalize_gender(val):
    if pd.isna(val): return None
    s = str(val).strip().lower()
    if s in {"f","female","woman","girl"}: return "F"
    if s in {"m","male","man","boy"}: return "M"
    return None

def normalize_is_jewish(val):
    if pd.isna(val): return None
    s = str(val).strip().upper()
    if s == 'J': return True
    if s == 'N': return False
    return None

def normalize_school_with_email(school_response, general_email, school_email):
    if pd.notna(school_email) and school_email.strip():
        school_email_clean = str(school_email).strip().lower()

        if any(domain in school_email_clean for domain in [
            "@harvard.edu", "@college.harvard.edu"
        ]):
            return "harvard"
        elif any(domain in school_email_clean for domain in [
            "@hbs.edu", "@hms.harvard.edu", "@hsph.harvard.edu",
            "@fas.harvard.edu", "@hillel.harvard.edu"
        ]):
            return "other"
        elif "@mit.edu" in school_email_clean:
            return "mit"
        else:
            return "other"

    if pd.notna(general_email) and general_email.strip():
        general_email_clean = str(general_email).strip().lower()

        if any(domain in general_email_clean for domain in [
            "@harvard.edu", "@college.harvard.edu"
        ]):
            return "harvard"
        elif any(domain in general_email_clean for domain in [
            "@hbs.edu", "@hms.harvard.edu", "@hsph.harvard.edu",
            "@fas.harvard.edu"
        ]):
            return "other"
        elif "@mit.edu" in general_email_clean:
            return "mit"
        elif ".edu" in general_email_clean:
            return "other"

    if pd.notna(school_response) and school_response.strip():
        s = str(school_response).strip().lower()
        if "harvard" in s and "business" not in s:
            return "harvard"
        elif "mit" in s:
            return "mit"
        else:
            return "other"

    return None

def get_grade_to_year_mapping():
    """
    Generate dynamic grade-to-year mapping based on current academic year.
    Academic year starts in September (month 9).
    """
    now = datetime.now()
    current_year = now.year

    # If we're between January and August, use current year as reference
    # If we're between September and December, use next year as reference
    if now.month >= 9:
        # Fall semester - students are in the academic year that ends next year
        base_year = current_year + 1
    else:
        # Spring semester - students are in the academic year that ends this year
        base_year = current_year

    # Calculate graduation years for each class level
    # Freshman graduate in 4 years, Sophomore in 3, Junior in 2, Senior in 1
    return {
        "freshman": base_year + 3, "first": base_year + 3, "first year": base_year + 3,
        "1": base_year + 3, "1st": base_year + 3,
        "sophomore": base_year + 2, "second": base_year + 2,
        "2": base_year + 2, "2nd": base_year + 2,
        "junior": base_year + 1, "third": base_year + 1,
        "3": base_year + 1, "3rd": base_year + 1,
        "senior": base_year, "fourth": base_year,
        "4": base_year, "4th": base_year,
    }

def parse_class_year(val):
    if pd.isna(val): return None
    s = str(val).strip()

    m = re.search(r"(20\d{2})", s)
    if m:
        return int(m.group(1))

    m = re.search(r"['\']\s*(\d{2})", s)
    if m:
        short = int(m.group(1))
        return 2000 + short

    # Get dynamic grade-to-year mapping
    grade_to_year = get_grade_to_year_mapping()

    t = s.lower()
    t = t.replace("year", "").strip()
    t = t.replace("st year", "").replace("nd year", "").replace("rd year", "").replace("th year", "").strip()

    if t in grade_to_year:
        return grade_to_year[t]

    words = re.findall(r"[a-z]+|\d+(?:st|nd|rd|th)?", t)
    for w in words:
        yr = grade_to_year.get(w)
        if yr:
            return yr

    return None

def create_event(conn):
    """Create new event in database. Returns new_event_id or None."""
    with conn.cursor() as cur:
        cur.execute('SELECT COALESCE(MAX(id), 0) + 1 FROM Events')
        new_event_id = cur.fetchone()[0]

    print(f"New event will have ID: {new_event_id}")

    category_valid = False
    while not category_valid:
        category = input('Enter category (speaker, party, speaker dinner, community dinner): ')
        if category.lower() in ['speaker', 'party', 'speaker dinner', 'community dinner']:
            category = category.lower()
            category_valid = True
        else:
            print('Invalid category. Please enter a valid category.')

    name = input('Enter name: ')
    start_datetime_input = input('Enter start date and time (YYYY-MM-DD HH:MM): ')

    # Parse datetime with Eastern timezone (handles DST automatically)
    try:
        start_datetime_parsed = pd.to_datetime(start_datetime_input + ":00").tz_localize(ZoneInfo('America/New_York'))
    except Exception as e:
        print(f"Error parsing datetime: {e}")
        print("Please use format: YYYY-MM-DD HH:MM")
        return None

    location = input('Enter location: ').lower()
    description = input('Enter description: ')

    with conn.cursor() as cur:
        cur.execute('SELECT id FROM Events WHERE event_name = %s', (name,))
        existing = cur.fetchone()

        if existing:
            print('Event already exists.')
            return None
        else:
            cur.execute("""
                INSERT INTO Events (id, event_name, category, location, start_datetime, description)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (new_event_id, name, category, location, start_datetime_parsed, description))
            conn.commit()
            print('Event added successfully.')
            print(f"Event ID: {new_event_id}")
            print(f"Name: {name}")
            print(f"Category: {category}")
            print(f"Location: {location}")
            print(f"Start: {start_datetime_parsed}")
            return new_event_id

def select_existing_event(conn):
    """Select an existing event from the database. Returns event_id or None."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, event_name, category, location, start_datetime
            FROM Events
            ORDER BY start_datetime DESC
        """)
        events = cur.fetchall()

    if not events:
        print("No existing events found in database.")
        return None

    print("\n=== Existing Events ===")
    for i, event in enumerate(events):
        start_dt = event['start_datetime'].strftime('%Y-%m-%d %H:%M') if event['start_datetime'] else 'N/A'
        print(f"{i}: ID={event['id']} | {event['event_name']} | {event['category']} | {start_dt} | {event['location']}")

    print("\n")
    choice = input("Enter the number of the event to add attendees to (or 'q' to quit): ")

    if choice.lower() == 'q':
        return None

    try:
        selected_index = int(choice)
        if 0 <= selected_index < len(events):
            selected_event = events[selected_index]
            print(f"\nSelected: {selected_event['event_name']} (ID: {selected_event['id']})")
            return selected_event['id']
        else:
            print("Invalid selection. Index out of range.")
            return None
    except ValueError:
        print("Invalid input. Please enter a number.")
        return None

def import_csv(csv_path, new_event_id, log_people=False):
    """
    Import event attendee data from CSV file into PostgreSQL database.

    This function performs the following operations:
    1. Loads and normalizes CSV data (names, emails, phone numbers, demographics)
    2. Processes invite tokens and creates database records
    3. Matches attendees to existing people using email, phone, or fuzzy name matching
    4. Creates new person records for unmatched attendees
    5. Updates contact information (email, phone) for all people
    6. Creates attendance records linking people to events
    7. Processes referral tracking and increments referral counts

    Args:
        csv_path: Path to the CSV file to import (from Posh ticketing system)
        new_event_id: ID of the event to associate attendance records with
        log_people: If True, print person information as each row is processed

    Returns:
        None

    Raises:
        Exception: If import fails (rolls back all changes)
    """
    # Configuration - hardcoded column mappings (can be parameterized later if needed)
    approved_column = "Order Status"
    rsvp_approved_values = ["Completed"]
    rsvp_datetime_column = "Order Date/Time"
    first_name_column = "First Name"
    last_name_column = "Last Name"
    email_column = "Email"
    school_email_column = "What is your school email?"
    phone_column = "Phone Number"
    attendance_column = "Tickets Scanned"
    invite_token_column = "Tracking Link"
    gender_column_raw = "Detected Gender"
    school_column_raw = "What school do you go to?"
    year_column_raw = "What is your graduation year?"

    # Detect referral column (any column with "referral" in the name)
    referral_column = None

    # Load CSV - first peek at columns to build dtype dict safely
    df_peek = pd.read_csv(csv_path, nrows=0)
    available_columns = set(df_peek.columns)

    # Build dtype dict only for columns that exist
    dtype_dict = {}
    if phone_column in available_columns:
        dtype_dict[phone_column] = str
    if email_column in available_columns:
        dtype_dict[email_column] = str
    if school_email_column in available_columns:
        dtype_dict[school_email_column] = str

    # Load CSV with explicit dtype for phone/email columns to prevent float conversion
    df_current = pd.read_csv(csv_path, dtype=dtype_dict, header=0)

    # Filter out any rows that appear to be duplicate headers
    # This happens when the header row is incorrectly treated as data
    df_current = df_current[df_current[first_name_column] != first_name_column]
    df_current = df_current.reset_index(drop=True)

    print(f"Loaded {len(df_current)} rows from CSV")

    # Check for referral column after CSV is loaded
    for col in df_current.columns:
        if 'referral' in col.lower():
            referral_column = col
            print(f"Found referral column: {referral_column}")
            break

    # Normalize data - use safe column access
    df_current["_norm_gender"] = safe_get_column(df_current, gender_column_raw).apply(normalize_gender)
    df_current["_norm_school"] = df_current.apply(
        lambda r: normalize_school_with_email(
            r.get(school_column_raw, pd.NA),
            r.get(email_column, ""),
            r.get(school_email_column, pd.NA)
        ),
        axis=1
    )
    df_current["_norm_class_year"] = safe_get_column(df_current, year_column_raw).apply(parse_class_year)
    if "is_jewish" in df_current.columns:
        df_current["_norm_is_jewish"] = df_current["is_jewish"].apply(normalize_is_jewish)
    else:
        df_current["_norm_is_jewish"] = None

    # Handle invite token column if it exists
    if invite_token_column in df_current.columns:
        df_current[invite_token_column] = df_current[invite_token_column].apply(
            lambda x: pd.NA if x == "email" else x
        )

    print("Data normalized successfully.")

    conn = get_db_connection()
    CONNECTION_REFRESH_INTERVAL = 50  # Refresh connection every N rows to prevent timeout

    try:
        # Process invite tokens
        if invite_token_column in df_current.columns:
            df_current[invite_token_column] = df_current[invite_token_column].fillna("default")
            unique_tokens = df_current[invite_token_column].unique()

            invite_token_map = {}

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, value FROM InviteTokens WHERE event_id = %s",
                    (new_event_id,)
                )
                existing_tokens = cur.fetchall()
                for token in existing_tokens:
                    invite_token_map[token['value']] = token['id']

            with conn.cursor() as cur:
                for token in unique_tokens:
                    token_str = str(token)
                    # Truncate to 100 characters to match database constraint
                    if len(token_str) > 100:
                        print(f"⚠️  Warning: Truncating tracking link from {len(token_str)} to 100 chars: '{token_str[:50]}...'")
                        token_str_truncated = token_str[:100]
                    else:
                        token_str_truncated = token_str

                    if token_str_truncated not in invite_token_map:
                        category = "personal outreach" if token_str_truncated != "default" else "mailing list"
                        cur.execute("""
                            INSERT INTO InviteTokens (event_id, category, value, description)
                            VALUES (%s, %s, %s, %s)
                            RETURNING id
                        """, (new_event_id, category, token_str_truncated, ""))
                        new_id = cur.fetchone()[0]
                        invite_token_map[token_str_truncated] = new_id
                    # Map original token to the truncated version's ID
                    if token_str != token_str_truncated:
                        invite_token_map[token_str] = invite_token_map[token_str_truncated]
            conn.commit()
            print(f"Processed {len(invite_token_map)} invite tokens.")
        else:
            df_current[invite_token_column] = "default"
            invite_token_map = {"default": 1}

        # Process each row
        handle_indices_list = []
        processed_count = 0
        new_people_count = 0
        new_contacts_count = 0
        new_attendance_count = 0

        for idx, row in df_current.iterrows():
            raw_first = row.get(first_name_column, "")
            raw_last = row.get(last_name_column, "")
            raw_email = row.get(email_column, "")
            raw_school_email = row.get(school_email_column, "")
            raw_phone = row.get(phone_column, "")
            raw_invite_token = row.get(invite_token_column, "default")
            raw_rsvp_status = row.get(approved_column, pd.NA)
            raw_rsvp_datetime = row.get(rsvp_datetime_column, None)
            raw_attended = row.get(attendance_column, None)

            # Clean names - handle NaN values from pandas
            first_name_clean = str(raw_first).strip().title() if pd.notna(raw_first) and str(raw_first).strip() else ""
            last_name_clean = str(raw_last).strip().title() if pd.notna(raw_last) and str(raw_last).strip() else ""

            email_clean = str(raw_email).strip().lower() if pd.notna(raw_email) else ""
            school_email_clean = str(raw_school_email).strip().lower() if pd.notna(raw_school_email) else ""
            phone_clean = str(raw_phone).strip() if pd.notna(raw_phone) else ""

            primary_email_for_matching = school_email_clean if school_email_clean else email_clean

            row_dict_for_matching = {
                "first_name": first_name_clean,
                "last_name": last_name_clean,
                "email": primary_email_for_matching,
                "phone": phone_clean
            }

            matched_person_id = find_person_id(
                row_dict_for_matching,
                conn,
                email_col="email",
                phone_col="phone",
                handle_indices_list=handle_indices_list,
                fuzzy_threshold=0.80
            )

            # Create new person if no match
            if not matched_person_id and matched_person_id != 0:
                norm_gender = na_to_none(row.get("_norm_gender", None))
                norm_school = na_to_none(row.get("_norm_school", None))
                norm_class_year = na_to_none(row.get("_norm_class_year", None))
                norm_is_jewish = na_to_none(row.get("_norm_is_jewish", None))

                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO People (first_name, last_name, gender, class_year, is_jewish, school, preferred_name)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        first_name_clean,
                        last_name_clean,
                        norm_gender,
                        norm_class_year,
                        norm_is_jewish,
                        norm_school,
                        None
                    ))
                    matched_person_id = cur.fetchone()[0]
                conn.commit()
                new_people_count += 1

                # Update contact information in People table
                contacts_updated = update_contact_info(
                    conn, matched_person_id, school_email_clean, email_clean, phone_clean
                )
                new_contacts_count += contacts_updated
                conn.commit()

            else:
                # Update contact information in People table for existing person
                update_contact_info(
                    conn, matched_person_id, school_email_clean, email_clean, phone_clean
                )
                conn.commit()

            # Create attendance record
            approved_val = raw_rsvp_status in rsvp_approved_values
            checked_in_val = str(raw_attended).strip().lower() in ["1", "1.0", "true", "yes"]
            rsvp_val = not pd.isna(raw_rsvp_status) or raw_rsvp_status == ""

            token_str = str(raw_invite_token)
            invite_token_id = invite_token_map.get(token_str, invite_token_map.get("default"))

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO Attendance
                    (person_id, event_id, rsvp, approved, checked_in, rsvp_datetime, is_first_event, invite_token_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (person_id, event_id) DO NOTHING
                """, (
                    matched_person_id,
                    new_event_id,
                    rsvp_val,
                    approved_val,
                    checked_in_val,
                    na_to_none(raw_rsvp_datetime),
                    False,
                    invite_token_id
                ))

            # Update is_first_event flag if this person checked in
            if checked_in_val:
                with conn.cursor() as cur:
                    # Find the earliest checked-in event for this person
                    cur.execute("""
                        SELECT a.event_id
                        FROM Attendance a
                        JOIN Events e ON a.event_id = e.id
                        WHERE a.person_id = %s AND a.checked_in = TRUE
                        ORDER BY e.start_datetime ASC
                        LIMIT 1
                    """, (matched_person_id,))
                    earliest_event = cur.fetchone()

                    if earliest_event:
                        earliest_event_id = earliest_event[0]

                        # Set is_first_event = TRUE for earliest event only
                        cur.execute("""
                            UPDATE Attendance
                            SET is_first_event = TRUE
                            WHERE person_id = %s AND event_id = %s
                        """, (matched_person_id, earliest_event_id))

                        # Set is_first_event = FALSE for all other events
                        cur.execute("""
                            UPDATE Attendance
                            SET is_first_event = FALSE
                            WHERE person_id = %s AND event_id != %s
                        """, (matched_person_id, earliest_event_id))

            conn.commit()
            new_attendance_count += 1

            # Log person information if logging is enabled
            if log_people and matched_person_id:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # Get person info with email
                    cur.execute("""
                        SELECT
                            p.first_name,
                            p.last_name,
                            COALESCE(
                                (SELECT contact_value FROM Contacts WHERE person_id = p.id AND contact_type = 'school email' LIMIT 1),
                                (SELECT contact_value FROM Contacts WHERE person_id = p.id AND contact_type = 'personal email' LIMIT 1)
                            ) as email
                        FROM People p
                        WHERE p.id = %s
                    """, (matched_person_id,))
                    person_info = cur.fetchone()

                # Determine referral code from invite token or referral column
                referral_code = "N/A"
                if raw_invite_token and not pd.isna(raw_invite_token) and str(raw_invite_token).lower() not in ['default', 'email', 'txt', 'insta', 'maillist']:
                    referral_code = str(raw_invite_token)
                elif referral_column and referral_column in row.index and not pd.isna(row[referral_column]):
                    referral_code = str(row[referral_column])

                # Attendance status
                attendance_status = "✓ Attended" if checked_in_val else "✗ No-show"

                if person_info:
                    print(f"  📋 {person_info['first_name']} {person_info['last_name']} | {person_info['email'] or 'No email'} | {attendance_status} | Referral: {referral_code}")

            # Increment referral count if this person checked in and was referred
            if checked_in_val:
                referrer_id = None

                # 1. Check tracking link for referral
                if raw_invite_token and not pd.isna(raw_invite_token):
                    referrer_id = match_tracking_link_to_person(conn, raw_invite_token)
                    if referrer_id:
                        print(f"  → Tracking link '{raw_invite_token}' matched to person ID {referrer_id}")

                # 2. Check referral column if it exists
                if referral_column and referral_column in row.index and not pd.isna(row[referral_column]):
                    referrer_name = str(row[referral_column]).strip()
                    # Create a fake row for find_person_id
                    referrer_row = pd.Series({
                        "first_name": referrer_name,
                        "last_name": ""
                    })
                    referrer_match = find_person_id(referrer_row, conn, fuzzy_threshold=0.8, handle_indices_list=[])
                    if referrer_match:
                        referrer_id = referrer_match
                        print(f"  → Referral column '{referrer_name}' matched to person ID {referrer_id}")

                # Increment referral count
                if referrer_id and referrer_id != matched_person_id:  # Don't count self-referrals
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE People
                            SET referral_count = referral_count + 1
                            WHERE id = %s
                        """, (referrer_id,))
                    conn.commit()
                    print(f"  ✓ Incremented referral_count for person ID {referrer_id}")

            processed_count += 1

            # Refresh connection periodically to prevent timeouts
            if processed_count % CONNECTION_REFRESH_INTERVAL == 0:
                print(f"\n--- Processed {processed_count} rows, refreshing connection ---")
                conn = ensure_connection(conn, force_refresh=True)

            elif processed_count % 10 == 0:
                print(f"Processed {processed_count}/{len(df_current)} rows...")

        # Final commit to save all remaining work
        conn.commit()
        print("\n✓ Final commit successful")

        print(f"\n=== Import Complete ===")
        print(f"Processed: {processed_count} rows")
        print(f"New people: {new_people_count}")
        print(f"New contacts: {new_contacts_count}")
        print(f"New attendance records: {new_attendance_count}")

        if handle_indices_list:
            print(f"\nUnmatched names: {len(handle_indices_list)}")
            for fn, ln in handle_indices_list:
                print(f"  - {fn} {ln}")

    except Exception as e:
        conn.rollback()
        print(f"Error during import: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description='Import event data from CSV to PostgreSQL')
    parser.add_argument('csv_file', help='Path to the CSV file to import')
    parser.add_argument('--log-people', action='store_true',
                        help='Log person information as each row is processed during import')
    args = parser.parse_args()

    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"Error: CSV file not found: {csv_path}")
        sys.exit(1)

    print(f"Importing from: {csv_path}\n")

    # Test connection
    try:
        conn = get_db_connection()
        print("✓ Connected to database successfully")

        # Mode selection: new event or add to existing
        print("\n=== Event Mode Selection ===")
        mode = input("Create new event or add to existing event? (new/existing): ").strip().lower()

        event_id = None

        if mode == "existing":
            # Select existing event
            event_id = select_existing_event(conn)
            if not event_id:
                print("Error: Could not select event")
                conn.close()
                sys.exit(1)
        elif mode == "new":
            # Create new event
            event_id = create_event(conn)
            if not event_id:
                print("Error: Could not create event")
                conn.close()
                sys.exit(1)
        else:
            print("Invalid mode. Please enter 'new' or 'existing'.")
            conn.close()
            sys.exit(1)

        conn.close()

        # Import CSV
        import_csv(csv_path, event_id, log_people=args.log_people)

        print("\n✓ All done!")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
