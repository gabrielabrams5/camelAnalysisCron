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

