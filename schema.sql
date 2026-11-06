-- Railway Database Schema
-- Exported on: 2026-02-01 17:07:11
-- Database: railway
-- Host: yamabiko.proxy.rlwy.net
--
-- This file tracks the current state of the database schema.
-- Re-run export_schema.py to update this file after making changes.


-- ============================================
-- TABLE: analytics_graphs
-- ============================================

CREATE TABLE IF NOT EXISTS analytics_graphs (
    id SERIAL PRIMARY KEY,
    graph_name VARCHAR(100) UNIQUE NOT NULL,
    image_data BYTEA NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);


-- ============================================
-- VIEW: allmailing
-- ============================================
-- Generates a denormalized list of all emails (one row per email address)

CREATE OR REPLACE VIEW allmailing AS
SELECT
    row_number() OVER () AS id,
    first_name,
    last_name,
    school,
    email AS contact_value,
    COALESCE(event_attendance_count, 0) AS event_count
FROM (
    SELECT first_name, last_name, school, school_email AS email, event_attendance_count
    FROM people
    WHERE school_email IS NOT NULL
    UNION ALL
    SELECT first_name, last_name, school, personal_email AS email, event_attendance_count
    FROM people
    WHERE personal_email IS NOT NULL
) AS all_emails;


-- ============================================
-- TABLE: attendance
-- ============================================

CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
    person_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    rsvp BOOLEAN NOT NULL,
    approved BOOLEAN NOT NULL DEFAULT false,
    checked_in BOOLEAN NOT NULL DEFAULT false,
    rsvp_datetime TIMESTAMP,
    is_first_event BOOLEAN NOT NULL DEFAULT false,
    invite_token_id INTEGER NOT NULL,
    additional_info JSONB,
    UNIQUE (person_id, event_id),
    CONSTRAINT fk_attendance_person
        FOREIGN KEY (person_id)
        REFERENCES people(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_attendance_event
        FOREIGN KEY (event_id)
        REFERENCES events(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_attendance_invite_token
        FOREIGN KEY (invite_token_id)
        REFERENCES invitetokens(id)
        ON DELETE CASCADE
);


-- ============================================
-- TABLE: camel_fiscal_years
-- ============================================
-- Opening balance of each Hillel fiscal year (July-June, named for the year
-- it ends: FY27 = 2027). Recorded with finance/track_balance.py --set-opening,
-- never committed (public repo). Created by add_camel_finance_migration.py.

CREATE TABLE IF NOT EXISTS camel_fiscal_years (
    fiscal_year SMALLINT PRIMARY KEY,
    opening_balance NUMERIC(12,2) NOT NULL,
    source TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================
-- TABLE: camel_pl_reports
-- ============================================
-- One row per QuickBooks report email (source_id = Gmail message id) or
-- manually imported workbook (source_id = 'file:<sha256>'). 'imported' rows
-- hold a weekly P&L's fiscal-year-to-date totals and per-account figures;
-- 'ledger' rows are Transaction Detail reports covering period_start -
-- as_of_date, whose lines are in camel_transactions (generated_at: when
-- QuickBooks ran it); 'skipped' rows are other attachments, kept so they are
-- not fetched again. emailed_at is set once a report is covered by the weekly
-- summary email. Written by finance/track_balance.py (pipeline Step 9).

CREATE TABLE IF NOT EXISTS camel_pl_reports (
    id SERIAL PRIMARY KEY,
    source_id VARCHAR(100) UNIQUE NOT NULL,
    status VARCHAR(10) NOT NULL,
    subject TEXT,
    received_at TIMESTAMPTZ,
    filename TEXT,
    period_start DATE,
    as_of_date DATE,
    fiscal_year SMALLINT,
    total_income NUMERIC(12,2),
    total_expenses NUMERIC(12,2),
    net_income NUMERIC(12,2),
    accounts JSONB,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    emailed_at TIMESTAMPTZ,
    generated_at TIMESTAMPTZ,
    CONSTRAINT camel_pl_reports_status_check
        CHECK (status IN ('imported', 'skipped', 'ledger')),
    CONSTRAINT camel_pl_reports_imported_check
        CHECK (status <> 'imported' OR (as_of_date IS NOT NULL
                                        AND fiscal_year IS NOT NULL
                                        AND net_income IS NOT NULL)),
    CONSTRAINT camel_pl_reports_ledger_check
        CHECK (status <> 'ledger' OR (period_start IS NOT NULL
                                      AND as_of_date IS NOT NULL))
);

CREATE INDEX idx_camel_pl_reports_as_of ON public.camel_pl_reports USING btree (fiscal_year, as_of_date);


-- ============================================
-- TABLE: camel_transactions
-- ============================================
-- Every line of every 'ledger' report: QuickBooks' weekly / YTD Transaction
-- Detail, Hillel's roll-forward detail, and camelManagement's pre-July-2025
-- expenses (category set, account NULL). section is 'income', 'expense' or
-- 'balance' (balance-sheet accounts such as prepaid expenses). Created by
-- add_camel_transactions_migration.py.

CREATE TABLE IF NOT EXISTS camel_transactions (
    id SERIAL PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES camel_pl_reports(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    txn_date DATE NOT NULL,
    account TEXT,
    section VARCHAR(10) NOT NULL,
    txn_type TEXT,
    num TEXT,
    name TEXT,
    description TEXT,
    amount NUMERIC(12,2) NOT NULL,
    ap_paid TEXT,
    category TEXT,
    UNIQUE (report_id, line_no),
    CHECK (section IN ('income', 'expense', 'balance'))
);

CREATE INDEX idx_camel_transactions_date ON public.camel_transactions USING btree (txn_date);


-- ============================================
-- VIEW: camel_ledger
-- ============================================
-- The current ledger: each line comes from the newest 'ledger' report covering
-- its date (a report restates its whole period). Reports with no balance-sheet
-- lines (Hillel's roll-forward lists P&L accounts only) do not override those.

CREATE OR REPLACE VIEW camel_ledger AS
SELECT
    t.txn_date,
    CASE WHEN EXTRACT(MONTH FROM t.txn_date) >= 7
         THEN EXTRACT(YEAR FROM t.txn_date)::int + 1
         ELSE EXTRACT(YEAR FROM t.txn_date)::int END AS fiscal_year,
    t.section, t.account, t.category, t.txn_type, t.num, t.name, t.description,
    t.amount, t.ap_paid, t.id, r.source_id
FROM camel_transactions t
JOIN camel_pl_reports r ON r.id = t.report_id
WHERE t.report_id = (
    SELECT newest.id FROM camel_pl_reports newest
    WHERE newest.status = 'ledger'
      AND t.txn_date BETWEEN newest.period_start AND newest.as_of_date
      AND (t.section <> 'balance' OR EXISTS (
          SELECT 1 FROM camel_transactions b
          WHERE b.report_id = newest.id AND b.section = 'balance'))
    ORDER BY newest.generated_at DESC NULLS LAST, newest.received_at DESC NULLS LAST, newest.id DESC
    LIMIT 1
);


-- ============================================
-- TABLE: camel_pledges
-- ============================================
-- Donations logged in camelManagement (status verbal / unconfirmed /
-- confirmed / not_received), copied by finance/import_camel_management.py.

CREATE TABLE IF NOT EXISTS camel_pledges (
    id SERIAL PRIMARY KEY,
    mgmt_id TEXT UNIQUE NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    pledged_at DATE NOT NULL,
    expected_at DATE,
    status VARCHAR(20) NOT NULL,
    confirmed_at TIMESTAMPTZ,
    campaign TEXT,
    camelhack_edition SMALLINT,
    donors TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ,
    migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================
-- VIEW: camel_balance_history
-- ============================================
-- The Camel's balance after every imported P&L:
-- opening balance of its fiscal year + net income so far that year.

CREATE OR REPLACE VIEW camel_balance_history AS
SELECT
    r.as_of_date,
    r.fiscal_year,
    f.opening_balance,
    r.total_income,
    r.total_expenses,
    r.net_income,
    f.opening_balance + r.net_income AS balance,
    r.source_id
FROM camel_pl_reports r
JOIN camel_fiscal_years f ON f.fiscal_year = r.fiscal_year
WHERE r.status = 'imported';


-- ============================================
-- TABLE: events
-- ============================================

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    event_name VARCHAR(100) NOT NULL,
    category VARCHAR(50),
    location VARCHAR(100) NOT NULL,
    start_datetime TIMESTAMP NOT NULL,
    description TEXT,
    speaker_name VARCHAR(100),
    speaker_org VARCHAR(100),
    speaker_bio_short TEXT,
    speaker_headshot_url TEXT,
    speaker_links TEXT,
    rsvp_link TEXT,
    attendance INTEGER DEFAULT 0,
    photos_dropbox_url TEXT,
    posh_url TEXT,
    dropbox_path TEXT,
    luma_event_id VARCHAR(100),
    attendance_data JSONB
);


-- ============================================
-- TABLE: event_feedback
-- ============================================

CREATE TABLE IF NOT EXISTS event_feedback (
    id SERIAL PRIMARY KEY,
    person_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    rating INTEGER,
    venue_rating INTEGER,
    text_feedback TEXT,
    from_where TEXT,
    interests TEXT,
    reason TEXT,
    additional_responses JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (person_id, event_id),
    CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
    CHECK (venue_rating IS NULL OR (venue_rating >= 1 AND venue_rating <= 5)),
    CONSTRAINT fk_event_feedback_person
        FOREIGN KEY (person_id)
        REFERENCES people(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_event_feedback_event
        FOREIGN KEY (event_id)
        REFERENCES events(id)
        ON DELETE CASCADE
);


-- ============================================
-- TABLE: harvard_students
-- ============================================
-- Verified Harvard student directory used by luma/auto_approve_rsvps.py:
-- a Harvard-domain RSVP email only counts as a verified student if it is in
-- this table. Loaded from mailChimp/harvard_students_with_emails.csv (which
-- is gitignored - the repo is public) by mailChimp/load_harvard_students.py.

CREATE TABLE IF NOT EXISTS harvard_students (
    email VARCHAR(255) PRIMARY KEY,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    full_name VARCHAR(200),
    profile_url TEXT,
    source_file VARCHAR(255),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================
-- TABLE: invitetokens
-- ============================================

CREATE TABLE IF NOT EXISTS invitetokens (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL,
    category VARCHAR(50) NOT NULL,
    description TEXT,
    value VARCHAR(100),
    CHECK ((category)::text = ANY ((ARRAY['personal outreach'::character varying, 'mailing list'::character varying, 'club collaboration'::character varying])::text[])),
    CONSTRAINT fk_invite_tokens_event
        FOREIGN KEY (event_id)
        REFERENCES events(id)
        ON DELETE CASCADE
);


-- ============================================
-- VIEW: mailinglist
-- ============================================
-- Simple pass-through view of people table for mailing list exports

CREATE OR REPLACE VIEW mailinglist AS
SELECT
    id,
    first_name,
    last_name,
    gender,
    class_year,
    is_jewish,
    school,
    event_attendance_count,
    event_rsvp_count,
    school_email,
    personal_email,
    preferred_email,
    phone_number
FROM people;


-- ============================================
-- TABLE: opportunities
-- ============================================

CREATE TABLE IF NOT EXISTS opportunities (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    organization VARCHAR(200),
    contact_email VARCHAR(100),
    application_link TEXT,
    deadline TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true
);


-- ============================================
-- TABLE: partner_codes
-- ============================================

CREATE TABLE IF NOT EXISTS partner_codes (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) NOT NULL,
    partner_name VARCHAR(255),
    organization VARCHAR(255),
    email VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    used_count INTEGER DEFAULT 0,
    max_uses INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT now(),
    last_used_at TIMESTAMP,
    UNIQUE (code)
);

CREATE INDEX idx_partner_codes_code ON public.partner_codes USING btree (code);


-- ============================================
-- TABLE: partner_sessions
-- ============================================

CREATE TABLE IF NOT EXISTS partner_sessions (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    partner_code VARCHAR(50) NOT NULL,
    partner_name VARCHAR(255),
    organization VARCHAR(255),
    last_login TIMESTAMP DEFAULT now(),
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE (email)
);

CREATE INDEX idx_partner_sessions_email ON public.partner_sessions USING btree (email);


-- ============================================
-- TABLE: promo_codes
-- ============================================

CREATE TABLE IF NOT EXISTS promo_codes (
    id SERIAL PRIMARY KEY,
    promo_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    code VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    CONSTRAINT fk_promo_codes_promo
        FOREIGN KEY (promo_id)
        REFERENCES promos(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_promo_codes_person
        FOREIGN KEY (person_id)
        REFERENCES people(id)
        ON DELETE CASCADE,
    CONSTRAINT unique_person_promo UNIQUE (person_id, promo_id),
    UNIQUE (code)
);

CREATE INDEX idx_promo_codes_promo_id ON public.promo_codes USING btree (promo_id);

CREATE INDEX idx_promo_codes_person_id ON public.promo_codes USING btree (person_id);

CREATE INDEX idx_promo_codes_code ON public.promo_codes USING btree (code);


-- ============================================
-- TABLE: people
-- ============================================

CREATE TABLE IF NOT EXISTS people (
    id SERIAL PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    preferred_name VARCHAR(50),
    gender CHAR(1),
    class_year SMALLINT,
    is_jewish BOOLEAN,
    school VARCHAR(20),
    school_email VARCHAR(100),
    personal_email VARCHAR(100),
    preferred_email VARCHAR(100),
    phone_number VARCHAR(15),
    event_attendance_count INTEGER DEFAULT 0,
    event_rsvp_count INTEGER DEFAULT 0,
    additional_info JSON,
    referral_count INTEGER DEFAULT 0,
    CHECK ((gender IS NULL) OR (gender = ANY (ARRAY['M'::bpchar, 'F'::bpchar, 'O'::bpchar]))),
    CHECK ((school IS NULL) OR ((school)::text = ANY ((ARRAY['harvard'::character varying, 'mit'::character varying, 'other'::character varying])::text[])))
);


-- ============================================
-- TABLE: promos
-- ============================================

CREATE TABLE IF NOT EXISTS promos (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    image_url TEXT,
    signup_link TEXT,
    end_date TIMESTAMP,
    referrals INTEGER DEFAULT 0
);


-- ============================================
-- TABLE: subscribers
-- ============================================

CREATE TABLE IF NOT EXISTS subscribers (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    first_name VARCHAR(50),
    last_name VARCHAR(50),
    UNIQUE (email)
);


-- ============================================
-- TABLE: verification_codes
-- ============================================

CREATE TABLE IF NOT EXISTS verification_codes (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    code VARCHAR(6) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE (email)
);

