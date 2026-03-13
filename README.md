# Luma Event Sync & Analytics Cron Service

Automated event synchronization from Luma API, attendance data import, analytics generation, and Mailchimp audience sync, running on Railway.

## What This Does

This application provides a complete pipeline for managing events from Luma:

### 1. **Luma Event Sync** (`luma_sync.py`)
- Fetches all events from Luma API
- **Future events**: Creates new events or syncs changed fields with Luma API
  - Tracks: Event name, start datetime, description (speaker bio), location, signup URL, cover image
  - **Smart Change Detection**: Only updates database when Luma data actually changes (prevents false "updated" logs and unnecessary database writes)
  - Compares all 6 fields before updating, logs specific changed fields
- **Past events** (>1 day old): Downloads attendance JSON data for events that haven't been processed yet

### 2. **Attendance Import** (`import_luma_attendance.py`)
- Processes Luma JSON data with sophisticated person matching:
  - Matches by email → phone → exact name → fuzzy name matching
  - Creates new person records if no match found
  - Updates person fields: gender, school, class year, contact info
- **Smart Name Handling**: If first/last names are blank, automatically splits full name field
- **School Email Priority**: Extracts "School email (.edu)" from custom registration fields
- **Custom Fields Storage**: Stores all registration answers (major, interests, clubs, etc.) in `additional_info` JSON column
- Creates attendance records with RSVP/approval/check-in status
- Handles invite token tracking for referral analysis

### 3. **Analytics Generation** (`analyze.py`)
- Connects to PostgreSQL database to analyze event attendance patterns and generates:

- **5 PNG visualization charts:**
  - `retention_by_event.png` - Event retention analysis
  - `new_members_by_event.png` - First-time attendee tracking
  - `new_members_by_category.png` - New members by event category
  - `party_funnel.png` - Large party event analysis
  - `rsvp_conversion.png` - RSVP to attendance conversion

- **1 CSV data file:**
  - `summary_stats.csv` - Overall summary statistics

- **Database updates:**
  - Saves attendance statistics back to the `events` table in the `attendance` column
  - Includes: `total_rsvps`, `total_attendees`, `first_time_attendees`, `conversion_rate`

All files are saved to a persistent Railway volume for long-term storage.

### 4. **Mailchimp Audience Sync** (`mailChimp/sync_mailchimp_audience.py`)
- Automatically syncs your entire mailing list from the database to Mailchimp:
  - Queries all people with email addresses from the database
  - Deduplicates contacts (one per person, prefers school email over personal email)
  - Batch syncs to Mailchimp audience using their API (500 contacts per batch)
- **One-way sync**: Only adds/updates contacts in Mailchimp, never removes them
- **Smart deduplication**: Uses `COALESCE(school_email, personal_email)` to prevent duplicates
- **Error resilient**: Continues processing even if individual contacts fail
- **Automatic**: Runs every 6 hours as part of the cron pipeline (if Mailchimp credentials are configured)
- **Manual**: Can also be run standalone anytime for immediate sync

## Pipeline Execution Flow

```
CRON TRIGGER (every 6 hours)
  ↓
Step 1: luma_sync.py
  ├─ Fetch all Luma events via API
  ├─ Future events: CREATE/UPDATE in database
  ├─ Past events with attendance==0: Download JSON attendance data
  └─ Output: List of JSON files to process
  ↓
Step 2: auto_approve_rsvps.py
  └─ Auto-approve Harvard/MIT RSVPs for upcoming events
  ↓
Step 3: import_luma_attendance.py (only if JSON files downloaded)
  ├─ Parse registration_answers for custom fields
  ├─ Extract school email (.edu) from custom fields
  ├─ Split full name if first/last names are blank
  ├─ Match/create people (email→phone→name→fuzzy)
  ├─ Update person data (gender, school, year, contact info, additional_info)
  └─ Create attendance records
  ↓
Step 4: For each newly imported event:
  ├─ event_analysis_single.py
  │   └─ Generate comprehensive event analysis CSV
  └─ tag_mailchimp_attendees.py (if Mailchimp configured)
      ├─ Tag first-time attendees with {event}_first_attended
      ├─ Tag returning attendees with {event}_attended
      └─ Tag RSVP no-shows with {event}_rsvp_no_show
  ↓
Step 5: generate_all_placards.py
  └─ Generate PDF placards and store in database
  ↓
Step 6: analyze.py (always runs)
  ├─ Generate analytics graphs
  └─ Save statistics to database
  ↓
Step 7: sync_mailchimp_audience.py (if Mailchimp configured)
  ├─ Query all people with emails from database
  ├─ Deduplicate (prefer school_email over personal_email)
  └─ Batch sync to Mailchimp audience (add/update contacts)
```

## Schedule

The cron job runs **every 6 hours** to keep events synchronized with Luma.

## Recent Changes

### 2026-02-27: Luma Sync Smart Change Detection
- **Fixed**: `luma_sync.py` no longer reports false "updated" messages on every run
- **Improved**: Implemented proper change detection - compares all 6 event fields before updating
- **Added**: Location field now synced from Luma `geo_address_json.city`
- **Enhanced Logging**: Shows specific changed fields when updates occur (e.g., `"Updated event ID 24: start_datetime, location (2 fields)"`)
- **Performance**: Reduced unnecessary database writes by only updating when Luma data actually changes

## Railway Deployment

### Prerequisites

1. A Railway account
2. A PostgreSQL database on Railway (or accessible from Railway)
3. A Railway volume for persistent storage

### Deployment Steps

1. **Push this repository to GitHub**

2. **Create a new Railway project:**
   - Go to [railway.app](https://railway.app)
   - Click "New Project" → "Deploy from GitHub repo"
   - Select your repository

3. **Set up environment variables:**

   Add the following variables in Railway's Variables section:

   ```
   # Required
   PGHOST=your-database-host
   PGPORT=your-database-port
   PGDATABASE=your-database-name
   PGUSER=your-database-user
   PGPASSWORD=your-database-password
   LUMA_API_KEY=your-luma-api-key

   # Optional - For Mailchimp integration
   MAILCHIMP_API_KEY=your-mailchimp-api-key
   MAILCHIMP_SERVER_PREFIX=us21
   MAILCHIMP_AUDIENCE_ID=your-audience-id
   ```

   **Note:** Replace placeholder values with your actual credentials.

4. **Create and mount a Railway volume:**

   - In your Railway service settings, go to "Volumes"
   - Click "New Volume"
   - Name: `analytics-outputs` (or any name)
   - Mount path: `/app/analysis_outputs`
   - Click "Add"

5. **Deploy:**

   Railway will automatically build and deploy using the Dockerfile.

### Viewing Generated Images

To access the generated images and CSV files:

1. **Option A - Railway CLI:**
   ```bash
   # Install Railway CLI
   npm i -g @railway/cli

   # Login
   railway login

   # Link to your project
   railway link

   # View files in the volume
   railway run ls /app/analysis_outputs

   # Download a specific file
   railway run cat /app/analysis_outputs/retention_by_event.png > retention_by_event.png
   ```

2. **Option B - Add a file server:**

   Modify the Dockerfile to include a simple HTTP server to browse files via the web.

3. **Option C - Connect to another service:**

   Create a separate Railway service that reads from the same volume and serves the files.

### Monitoring

View logs in Railway's dashboard to monitor cron job execution:

- Look for `[NOTIFICATION]` messages indicating success or failure
- Check timestamped log entries for each analytics run

## Local Development

### Running Locally

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create a `.env` file:**
   ```
   PGHOST=your-database-host
   PGPORT=your-database-port
   PGDATABASE=your-database-name
   PGUSER=your-database-user
   PGPASSWORD=your-database-password
   LUMA_API_KEY=your-luma-api-key
   ```

3. **Run the full pipeline:**
   ```bash
   bash run_luma_pipeline.sh
   ```

   Or run individual components:
   ```bash
   # Sync events from Luma
   python luma_sync.py

   # Import attendance (requires output from luma_sync.py)
   # The script reads JSON from stdin with event data
   python luma_sync.py | python import_luma_attendance.py

   # Import with detailed person logging
   python3 luma_sync.py | python3 import_luma_attendance.py --log-people

   # Run analytics only
   python analyze.py --outdir custom_output_folder
   ```

### `import_luma_attendance.py` Usage

The attendance import script reads JSON data from stdin (typically piped from `luma_sync.py`):

```bash
# Standard usage (piped from luma_sync.py)
python luma_sync.py | python import_luma_attendance.py

# With detailed person logging (shows each person processed with email and attendance status)
python luma_sync.py | python import_luma_attendance.py --log-people
```

**Input Format**: The script expects JSON from stdin in this format:
```json
[
  {
    "event_id": 123,
    "json_path": "/tmp/event_123_attendance.json",
    "event_name": "Spring 2024 Mixer"
  }
]
```

**Command-line Arguments:**
| Argument | Description | Required |
|----------|-------------|----------|
| `--log-people` | Print detailed person information as each guest is processed (name, email, attendance status, referral code) | No |

**What It Does:**
1. Reads event data from stdin (list of events with JSON file paths)
2. For each event, processes the Luma attendance JSON file
3. Extracts custom fields from `registration_answers`
4. Matches guests to existing people or creates new person records
5. Creates/updates attendance records
6. Updates event and person attendance counts
7. Handles referral tracking via invite tokens

### Testing with Docker

Build and run the Docker container locally:

```bash
# Build the image
docker build -t event-analytics .

# Run with volume mount
docker run -v $(pwd)/analysis_outputs:/app/analysis_outputs event-analytics
```

## File Structure

```
.
├── luma_sync.py                 # Luma API event sync script
├── import_luma_attendance.py    # Attendance CSV import script
├── analyze.py                   # Analytics generation script
├── run_luma_pipeline.sh         # Pipeline orchestrator script
├── entrypoint.py                # Docker entrypoint with cron
├── luma/                        # Luma integration scripts
│   ├── auto_approve_rsvps.py    # Auto-approve RSVPs based on attendance/email
│   └── README.md                # Luma scripts documentation
├── mailChimp/                   # Mailchimp integration module
│   ├── __init__.py              # Package initialization
│   ├── mailchimp_client.py      # Mailchimp API client
│   ├── tag_mailchimp_attendees.py  # Event attendee tagging script
│   └── sync_mailchimp_audience.py  # Full audience sync script
├── feedback/                    # Event feedback analysis scripts
│   └── event24_additional_questions.py  # Event 24 additional questions analysis
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Container configuration
├── railway.toml                 # Railway deployment config
├── schema.sql                   # Database schema
├── .env                         # Environment variables (not in git)
├── .gitignore                   # Git ignore rules
└── README.md                    # This file
```

## Environment Variables

| Variable | Description | Example | Required |
|----------|-------------|---------|----------|
| `PGHOST` | PostgreSQL host | `yamabiko.proxy.rlwy.net` | Yes |
| `PGPORT` | PostgreSQL port | `58300` | Yes |
| `PGDATABASE` | Database name | `railway` | Yes |
| `PGUSER` | Database user | `postgres` | Yes |
| `PGPASSWORD` | Database password | `your-password-here` | Yes |
| `LUMA_API_KEY` | Luma API authentication key | `lu_api_xxx...` | Yes |
| `LUMA_CALENDAR_ID` | Optional Luma calendar ID filter | `cal-abc123` | No |
| `MAILCHIMP_API_KEY` | Mailchimp API key | `abc123...` | No* |
| `MAILCHIMP_SERVER_PREFIX` | Mailchimp server prefix from API key | `us21` | No* |
| `MAILCHIMP_AUDIENCE_ID` | Mailchimp audience/list ID | `a1b2c3d4e5` | No* |

\* Required only if using Mailchimp integration (`mailChimp/tag_mailchimp_attendees.py`)

## Database Migration

Before deploying, ensure your database has the required columns:

```sql
-- Connect to your database and run:

-- Events table
ALTER TABLE events ADD COLUMN IF NOT EXISTS luma_event_id VARCHAR(100);
ALTER TABLE events ADD COLUMN IF NOT EXISTS attendance_data JSONB;

-- People table (for storing custom registration answers)
ALTER TABLE people ADD COLUMN IF NOT EXISTS additional_info JSON;

-- Optional: Add indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_events_luma_id ON events(luma_event_id);
```

The `additional_info` JSON column in the `people` table stores all custom registration answers from Luma events, including:
- "What brings you to Camel?"
- "What major are you?"
- "What school clubs are you involved in?"
- Any other custom registration questions

## Luma API Configuration

### Getting Your Luma API Key

1. Log into your Luma account
2. Navigate to Settings → API or Developer Settings
3. Generate an API key
4. Add it to your Railway environment variables as `LUMA_API_KEY`

### Customizing Luma API Endpoints

The Luma API endpoints are configured in `luma_sync.py`. You may need to update:

- `LUMA_API_BASE_URL` - Base URL for Luma API
- Event listing endpoint
- CSV download endpoint

Check the [Luma API documentation](https://docs.lu.ma/reference/api-overview) for the latest endpoint information.

### JSON Field Mapping & Custom Fields

The `import_luma_attendance.py` script processes JSON data from the Luma API. The script handles:

#### **Top-Level Fields**
These are mapped in the `JSON_FIELD_MAPPING` dictionary:

```python
JSON_FIELD_MAPPING = {
    'first_name': 'user_first_name',    # Primary first name
    'last_name': 'user_last_name',      # Primary last name
    'name': 'user_name',                # Full name (fallback if first/last blank)
    'email': 'email',                   # Primary email
    'phone': 'phone_number',            # Phone number
    'approved': 'approval_status',      # Registration status
    'checked_in': 'checked_in',         # Check-in status
    'rsvp_datetime': 'created_at',      # Registration timestamp
    'tracking_link': 'referral_code',   # Referral tracking
}
```

#### **Custom Registration Fields** (`registration_answers`)
Custom fields are stored in a `registration_answers` array within the JSON:

```json
{
  "registration_answers": [
    {"label": "School email (.edu)", "value": "student@harvard.edu"},
    {"label": "Gender", "value": "Male"},
    {"label": "What brings you to Camel?", "value": "..."},
    {"label": "What major are you?", "value": "Computer Science"}
  ]
}
```

The script automatically:
- **Extracts school email** from "School email (.edu)" field (takes priority over main email)
- **Handles variable question labels** with case-insensitive matching and fallbacks:
  - Gender: "Gender"
  - School: "School" or "What school do you go to?"
  - Graduation Year: "Grad year", "Graduation Year", or "Class Year"
- **Stores all registration answers** in the `people.additional_info` JSON column for custom questions like:
  - "What brings you to Camel?"
  - "What major are you?"
  - "What school clubs are you involved in?"
  - Any other custom registration questions

#### **Name Handling**
The script intelligently handles names:
1. Uses `user_first_name` and `user_last_name` if available
2. If both are blank, splits `user_name` using "first word = first name, rest = last name" logic
   - Example: "John Paul Smith" → first="John", last="Paul Smith"

## Customizing the Schedule

To change the cron schedule, modify the cron expression in `Dockerfile`:

```dockerfile
# Current: Every 6 hours
RUN echo "0 */6 * * * cd /app && ..." > /etc/cron.d/analytics-cron

# Daily at 2 AM:
RUN echo "0 2 * * * cd /app && ..." > /etc/cron.d/analytics-cron

# Every 12 hours:
RUN echo "0 */12 * * * cd /app && ..." > /etc/cron.d/analytics-cron
```

[Cron expression reference](https://crontab.guru/)

## Troubleshooting

### Job not running

1. Check Railway logs for errors
2. Verify environment variables are set correctly
3. Ensure the volume is properly mounted at `/app/analysis_outputs`

### Luma API connection errors

1. Verify `LUMA_API_KEY` is set correctly in Railway variables
2. Check that the API key has proper permissions in Luma
3. Review Luma API rate limits - you may need to adjust sync frequency
4. Update API endpoints in `luma_sync.py` if Luma has changed their API

### Database connection errors

1. Verify all `PG*` environment variables are correct
2. Check that the database is accessible from Railway
3. Confirm database credentials have proper permissions
4. Ensure the database migration has been run (luma_event_id column exists)

### Events not syncing from Luma

1. Check Railway logs for API errors
2. Verify Luma API endpoints are correct in `luma_sync.py`
3. Ensure event datetime parsing is working (check for timezone issues)
4. Verify events have the required fields (name, start_at)

**Note on Update Logging:**
- As of 2026-02-27, `luma_sync.py` uses smart change detection
- **Only logs updates when data actually changes**: `"Updated event ID 24: start_datetime, location (2 fields)"`
- **Silent when no changes detected**: No false "updated" messages on every run
- If you see update logs, it means Luma API data genuinely changed for that event

### Attendance JSON import errors

1. Check that JSON field names match `JSON_FIELD_MAPPING` in `import_luma_attendance.py`
2. Verify Luma API JSON response structure matches expected format
3. Check `registration_answers` array for custom fields
4. Review logs for person matching issues or name splitting errors
5. Verify custom field question labels match your Luma registration form

### Duplicate person records

1. The fuzzy matching threshold may be too low - adjust in `import_luma_attendance.py`
2. Verify email matching is working (check for typos in CSV)
3. Consider adding manual cleanup SQL queries for known duplicates

### Files not persisting

1. Verify the Railway volume is mounted at `/app/analysis_outputs`
2. Check that the volume has sufficient storage space
3. Ensure the cron job is writing to the correct path

## Manual Analysis Scripts

In addition to the automated pipeline, there are two manual scripts for generating detailed event analysis and placards.

### Running Single Event Analysis

The `event_analysis_single.py` script analyzes a specific event and compares it to the previous event, generating comprehensive metrics.

**Usage:**

```bash
# Interactive mode - select event from a list
python event_analysis_single.py

# Automated mode - analyze specific event ID
python event_analysis_single.py --event-id 123

# Custom output directory
python event_analysis_single.py --event-id 123 --outdir ./reports

# Custom output filename
python event_analysis_single.py --event-id 123 --output-file custom_analysis.csv

# Manual retention calculation - choose 4 past events for retention metrics
python event_analysis_single.py --event-id 45 --choose-past
```

**Command-line Arguments:**

| Argument | Description | Default | Required |
|----------|-------------|---------|----------|
| `--event-id` | Event ID to analyze (omit for interactive mode) | None | No |
| `--choose-past` | Manually select 4 past events for retention calculation (requires `--event-id`) | Disabled | No |
| `--outdir` | Output directory for CSV file | `.` (current directory) | No |
| `--output-file` | Output CSV filename | `event_analysis_all.csv` | No |

**Manual Retention Selection (`--choose-past`):**

The `--choose-past` flag allows you to manually select which 4 events to use for retention calculation instead of automatically using the most recent events by datetime. This is useful when you want to compare a current event to specific past events.

**How it works:**
1. Displays the past 15 events (most recent first)
2. Prompts you to enter up to 4 event IDs (comma-separated, e.g., `42,39,37,35`)
3. Analyzes the event specified by `--event-id`
4. Uses your selected events for retention metrics (i-1, i-2, i-3, i-4)
5. Uses the **first event ID** in your list for comparison metrics (RSVPs/attendees/first timers % change)

**Example:**
```bash
# Analyze event 45, manually choosing retention events
python event_analysis_single.py --event-id 45 --choose-past

# You'll see:
# === SELECT PAST EVENTS TO ANALYZE ===
# (Select up to 4 events by entering event IDs separated by commas, e.g., 42,41,38)
#
# ID    Name                                              Date                 Category        Attendance
# ======================================================================================================
# 42    Winter Formal Dance                               2024-12-15 19:00     party           120
# 41    Tech Panel Discussion                             2024-12-10 18:30     talk            45
# ...
#
# Enter event IDs (max 4, comma-separated): 42,39,37,35
#
# Selected 4 event(s) for retention calculation.
# Analyzing event ID: 45
```

**Benefits:**
- Compare to specific event types (e.g., only compare parties to parties)
- Exclude outlier events from retention calculations
- Manual control over which events matter for analysis
- Consistent comparison across different event analyses

**Notes:**
- Event names in retention columns are automatically truncated to 3 words to prevent display issues
- The 4 selected events are automatically sorted by datetime to determine i-1, i-2, i-3, i-4 order
- Only 1 row is added to the CSV (for the event specified by `--event-id`)

**What It Does:**

1. Connects to the database using environment variables
2. Retrieves comprehensive metrics for the specified event:
   - RSVPs, attendees, and first-timers
   - Demographics (gender, school, class year)
   - Attendance patterns (1st event, 2-3 events, 4+ events)
   - Retention rates from previous 4 events
3. Compares metrics to the previous event (event_id - 1)
4. Calculates percentage changes for key metrics
5. Outputs results to a CSV file (appends if file exists)
6. Displays a summary in the terminal

**Output:**

Creates or appends to `event_analysis_all.csv` with columns including:
- Event details (ID, name, date, category, venue)
- Core metrics with % change (RSVPs, attendees, first timers)
- Financial metrics (cost per attendee/first timer)
- Demographics (gender, school, class year percentages)
- Attendance patterns
- Retention rates from previous 4 events

**Prerequisites:**
- PostgreSQL database connection configured in `.env`
- Python dependencies installed (`psycopg2`, `pandas`)

---

### Generating Event Placards (PDFs)

The `generate_all_placards.py` script generates PDF placards for all events listed in the analysis CSV and stores them in the database.

**Usage:**

```bash
# Basic usage - store PDFs only in database
python generate_all_placards.py

# With custom input CSV path
python generate_all_placards.py --input-csv ./reports/event_analysis_all.csv

# Save PDF copies to a directory (in addition to database)
python3 generate_all_placards.py --save-pdfs-dir ./generated_pdfs

# Customize title and location for each event interactively
python3 generate_all_placards.py --customize

# Full custom configuration with customization
python3 generate_all_placards.py \
  --input-csv ./reports/event_analysis_all.csv \
  --placard-dir ./placard_generation \
  --save-pdfs-dir ./generated_pdfs \
  --customize
```

**Command-line Arguments:**

| Argument | Description | Default | Required |
|----------|-------------|---------|----------|
| `--input-csv` | Path to input CSV file with event data | `./event_analysis_all.csv` | No |
| `--placard-dir` | Path to placard_generation directory | `./placard_generation` | No |
| `--save-pdfs-dir` | Directory to save PDF copies (optional) | None | No |
| `--customize` | Enable interactive customization of title and location for each event | Disabled | No |

**What It Does:**

For each event in the input CSV:
1. Transforms event data to placard format using `transform_to_placard_csv.py`
2. **If `--customize` is set**: Prompts you to customize the title and location
   - Shows current title and location
   - Allows you to enter custom values (or press Enter to keep current)
   - Updates the placard data before PDF generation
3. Generates a PDF using Node.js React app in `placard_generation/`
4. Stores the PDF binary in the database (`events.placard_pdf` column)
5. Optionally saves a copy with event-specific name (e.g., `event_123_Event_Name.pdf`)
6. Deletes the processed row from the CSV
7. Continues processing remaining events even if errors occur

**Customize Mode Example:**

When using `--customize`, you'll see prompts like this:

```
[1/5] Processing event 123: Spring Networking Night
  ✓ Transformed event 123 to placard format

  Current title: Spring Networking Night
  Current location: Cambridge, MA

  Enter custom title (or press Enter to keep current): CAMEL Spring Mixer
  Enter custom location (or press Enter to keep current): Charles Hotel

  ✓ Customized placard data
  ✓ Generated PDF
  ...
```

**Output:**

- PDFs stored in the `events` table `placard_pdf` column (BYTEA)
- Optional: PDF files saved to `--save-pdfs-dir` with naming: `event_{id}_{name}.pdf`
- Terminal output showing progress and summary statistics

**Prerequisites:**
- PostgreSQL database connection configured in `.env`
- Python dependencies installed (`psycopg2`, `pandas`)
- Node.js installed with `placard_generation/` directory set up
- CSV file generated by `event_analysis_single.py`

**Important Notes:**
- The script processes events sequentially and removes them from the CSV after successful processing
- If the script is interrupted, it can be re-run and will continue from remaining events
- Failed events are skipped but remain in the CSV for retry
- Use `--save-pdfs-dir` if you want to keep local copies of generated PDFs

---

### Downloading Placards from Database

The `extra/download_placards.py` script downloads all placard PDFs stored in the Railway database and saves them to local files.

**Usage:**

```bash
# Download all placards from database
python3 extra/download_placards.py
```

**What It Does:**

1. Connects to the Railway PostgreSQL database
2. Queries all events where `placard_pdf IS NOT NULL`
3. Downloads each PDF from the database (BYTEA column)
4. Saves PDFs to `extra/placards/` directory
5. Uses filename format: `event_{id}_{sanitized_event_name}.pdf`
6. Shows progress with file sizes and summary count

**Output:**

- **Location**: `extra/placards/`
- **Filename format**: `event_123_Spring_Networking_Night.pdf`
- **Git-ignored**: PDFs in this directory are automatically excluded from version control via `.gitignore`

**Example Output:**

```
Downloading 15 placard(s) to /path/to/extra/placards/

✓ Downloaded: event_120_CamelHack_2026_Demo_Day.pdf (234.5 KB)
✓ Downloaded: event_121_Jon_Hirschtick_x_CAMEL.pdf (198.3 KB)
✓ Downloaded: event_122_Spring_Networking_Night.pdf (215.7 KB)
...

Successfully downloaded 15/15 placard(s)
```

**Prerequisites:**
- PostgreSQL database connection configured in `.env`
- Python dependencies installed (`psycopg2`)
- Placards must be generated first using `generate_all_placards.py`

**Features:**
- **Automatic directory creation**: Creates `extra/placards/` if it doesn't exist
- **Safe filenames**: Sanitizes event names for filesystem compatibility
- **Progress tracking**: Shows each download with file size
- **Error handling**: Continues downloading even if individual files fail
- **Git protection**: PDFs are automatically excluded from commits via `.gitignore`

**Use Cases:**
- **Local backup**: Keep local copies of all generated placards
- **Offline access**: Access placards without database connection
- **Distribution**: Share placard PDFs with team members
- **Archive**: Maintain historical records of event placards

---

### Event 24 Additional Questions Analysis

The `feedback/event24_additional_questions.py` script analyzes custom registration questions for Event 24 (Jon Hirschtick x CAMEL / Solidworks event), providing detailed insights into attendee responses with attendance cross-reference and demographics breakdown.

**Usage:**

```bash
# Basic usage - saves CSV to current directory
python3 feedback/event24_additional_questions.py

# Specify output directory
python3 feedback/event24_additional_questions.py --outdir feedback

# Custom CSV filename
python3 feedback/event24_additional_questions.py --csv-filename my_report.csv
```

**Command-line Arguments:**

| Argument | Description | Default | Required |
|----------|-------------|---------|----------|
| `--outdir` | Output directory for CSV file | `.` (current directory) | No |
| `--csv-filename` | Output CSV filename | `event24_additional_questions.csv` | No |

**What It Does:**

1. Connects to the Railway PostgreSQL database
2. Queries Event 24 (ID: 24) attendee data including the `additional_info` JSON field
3. **Dynamically discovers** all questions stored in the `additional_info` field (no hardcoding required)
4. Expands each question into a separate column in the output CSV
5. Analyzes responses with two key breakdowns:
   - **Attendance Cross-Reference**: Compares responses between attendees who checked in vs those who only RSVP'd
   - **Demographics Breakdown**: Analyzes responses by gender, school, and class year
6. Exports detailed CSV with all individual responses plus metadata
7. Displays comprehensive terminal summary with statistics and distributions

**Questions Discovered in Event 24:**

The script automatically detected 6 questions in the `additional_info` field:
- Gender
- Grad year
- School email (.edu)
- What brings you to Camel?
- What major are you? (no numbers)
- What school clubs are you involved in?

**Output:**

- **CSV File** (38KB): One row per attendee with columns for:
  - Event details (id, name, date)
  - Person details (id, name, demographics)
  - Attendance status (rsvp, checked_in, approved)
  - All discovered questions as separate columns (prefixed with "Q: ")

- **Terminal Summary**: Displays for each question:
  - Total responses count
  - Attendance breakdown (attended vs didn't attend)
  - Response distributions with counts and percentages
  - Demographics breakdown (by gender, school, class year)
  - Overall event statistics

**Example Output (Terminal):**

```
================================================================================
EVENT 24 (SOLIDWORKS) - ADDITIONAL QUESTIONS ANALYSIS
================================================================================

Event: Jon Hirschtick x CAMEL
Date: 2026-02-17 19:45:00
Total RSVPs: 190
Total Attended: 101
Attendance Rate: 53.2%

Found 6 unique questions:
  1. Gender
  2. Grad year
  3. What brings you to Camel?
  ...

================================================================================
ATTENDANCE CROSS-REFERENCE ANALYSIS
================================================================================

Question: What major are you? (no numbers)
Total Responses: 190
  - Attended: 101
  - Didn't Attend: 89

Response                                 Attended        Not Attended
----------------------------------------------------------------------
Mechanical Engineering                   25 (24.8%)      18 (20.2%)
Computer Science                         30 (29.7%)      25 (28.1%)
...

================================================================================
DEMOGRAPHICS BREAKDOWN: What major are you? (no numbers)
================================================================================

By Gender:
----------------------------------------------------------------------
  M:
    - Computer Science: 45
    - Mechanical Engineering: 28
  F:
    - Computer Science: 10
    - Biological Engineering: 8
...
```

**Features:**

- **Hardcoded for Event 24**: Specifically designed for analyzing the Solidworks event
- **Dynamic Question Discovery**: No need to update code when questions change
- **Handles Complex Data Types**: Automatically converts lists/arrays to comma-separated strings
- **Attendance Insights**: Compare what attendees said vs no-shows
- **Demographics Deep Dive**: Understand response patterns by gender, school, and class year
- **Dual Output**: Both machine-readable CSV and human-readable terminal summary

**Prerequisites:**
- PostgreSQL database connection configured in `.env`
- Python dependencies installed (`psycopg2`, `pandas`)
- Event 24 data in the database with `people.additional_info` populated

**Use Cases:**

- Understanding what motivated attendees to come (vs those who didn't show)
- Analyzing major/club distributions by attendance status
- Demographic analysis of registration responses
- Event planning insights for future similar events
- Identifying patterns in attendee interests and engagement

---

### Complete Workflow Example

Here's how to analyze multiple events and generate placards for them:

```bash
# Step 1: Analyze multiple events (run once per event)
python event_analysis_single.py --event-id 120 --output-file my_events.csv
python event_analysis_single.py --event-id 121 --output-file my_events.csv
python event_analysis_single.py --event-id 122 --output-file my_events.csv

# Step 2: Generate placards for all analyzed events
python generate_all_placards.py \
  --input-csv ./my_events.csv \
  --save-pdfs-dir ./event_placards

# The CSV will be empty after all events are processed
```

**Setting Up Placard Generation:**

Before running `generate_all_placards.py`, ensure the React app is built:

```bash
cd placard_generation
npm install
cd ..
```

The script uses Node.js to build and generate PDFs from the React app automatically.

## Mailchimp Integration

The `mailChimp/tag_mailchimp_attendees.py` script automatically tags event attendees and RSVP no-shows in Mailchimp for email marketing campaigns.

### Setup

1. **Get your Mailchimp credentials:**
   - **API Key**: Mailchimp → Account → Extras → API keys
   - **Server Prefix**: Found in your API key (e.g., if key ends in `-us21`, use `us21`)
   - **Audience ID**: Mailchimp → Audience → Settings → Audience name and defaults → Audience ID

2. **Update `.env` file:**
   ```bash
   MAILCHIMP_API_KEY=your_mailchimp_api_key_here
   MAILCHIMP_SERVER_PREFIX=us21
   MAILCHIMP_AUDIENCE_ID=your_audience_list_id_here
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   # This will install mailchimp-marketing SDK
   ```

### Usage

Tag attendees and RSVP no-shows for a specific event:

```bash
# Tag both attendees AND RSVP no-shows (default behavior)
python mailChimp/tag_mailchimp_attendees.py --event-id 123

# Tag ONLY attendees (skip RSVP no-shows)
python mailChimp/tag_mailchimp_attendees.py --event-id 123 --only-attendees

# Test without actually calling Mailchimp API
python3 mailChimp/tag_mailchimp_attendees.py --event-id 123 --dry-run

# Enable verbose logging
python mailChimp/tag_mailchimp_attendees.py --event-id 123 --verbose
```

### What It Does

**By default**, the script tags **two groups** with **differentiation for first-time vs returning attendees**:

1. **Attendees** (people who checked in):
   - Queries the Railway PostgreSQL database where `checked_in = TRUE`
   - Extracts first_name, last_name, email, and `is_first_event` flag for each attendee
   - Batch upserts contacts to Mailchimp (creates new or updates existing)
   - **First-time attendees**: Tagged with `{sanitized_event_name}_first_attended`
   - **Returning attendees**: Tagged with `{sanitized_event_name}_attended`

2. **RSVP No-Shows** (people who registered but didn't attend):
   - Queries the database where `checked_in = FALSE`
   - Extracts contact information for each RSVP
   - Batch upserts contacts to Mailchimp
   - Tags all contacts with `{sanitized_event_name}_rsvp_no_show` (no differentiation)

**Tag Format Examples:**
- "Spring 2024 Mixer" → `spring_2024_mixer_first_attended`, `spring_2024_mixer_attended`, and `spring_2024_mixer_rsvp_no_show`
- "Coffee & Coding!" → `coffee_coding_first_attended`, `coffee_coding_attended`, and `coffee_coding_rsvp_no_show`
- "Speaker Series: AI" → `speaker_series_ai_first_attended`, `speaker_series_ai_attended`, and `speaker_series_ai_rsvp_no_show`

### Example Output

```
Event: Spring 2024 Mixer
Event ID: 123

Groups to tag:
  Checked-in attendees: 45
    First-time attendees: 12 (tag: spring_2024_mixer_first_attended)
    Returning attendees: 33 (tag: spring_2024_mixer_attended)
  RSVP no-shows: 23
    Tag: spring_2024_mixer_rsvp_no_show

============================================================
MAILCHIMP TAGGING RESULTS
============================================================

Attendees (tagged with '_first_attended' or '_attended'):
  Total:               45
  Successfully upserted: 45
  Successfully tagged:   45
  Errors:               0

RSVP No-Shows (tagged with '_rsvp_no_show'):
  Total:               23
  Successfully upserted: 23
  Successfully tagged:   23
  Errors:               0

Overall Summary:
  Total people processed: 68
  Total errors:          0
============================================================
```

### Command-line Arguments

| Argument | Description | Required |
|----------|-------------|----------|
| `--event-id` | Database ID of the event to tag attendees for | Yes |
| `--only-attendees` | Only tag attendees (skip RSVP no-shows) | No |
| `--dry-run` | Query database but skip Mailchimp API calls (for testing) | No |
| `--verbose` | Enable verbose logging for debugging | No |

### Features

- **First-Time vs Returning Differentiation**: Automatically tags first-time attendees separately from returning attendees using the `is_first_event` database field
- **Dual Group Tagging**: Tags both attendees and no-shows by default for comprehensive email segmentation
- **Flexible Control**: Use `--only-attendees` to maintain original behavior (attendees only)
- **Automatic Contact Management**: Creates new Mailchimp contacts or updates existing ones
- **Batch Processing**: Uses Mailchimp's batch API for efficient operations
- **Safe Tag Names**: Automatically sanitizes event names (removes special characters, converts to lowercase)
- **Error Handling**: Logs warnings for individual failures without stopping the entire process
- **Dry Run Mode**: Test database queries and see what would be tagged without making API calls
- **Separate Statistics**: Shows detailed stats for both attendees and RSVP no-shows

### Use Cases for First-Time vs Returning Attendee Tags

- **Personalized Welcome Emails**: Send targeted onboarding emails to first-time attendees
- **Engagement Tracking**: Measure which events attract new members vs retain existing ones
- **Different Follow-Ups**: Send "Welcome!" emails to first-timers and "Thanks for coming back!" to returners
- **Segmentation in Mailchimp**: Create audience segments for all first-time attendees across events
- **Analytics**: Compare first-time vs returning attendance rates per event type

### Use Cases for RSVP No-Show Tagging

- **Follow-up Campaigns**: Send "We missed you!" emails to RSVPs who didn't attend
- **Event Improvement**: Survey no-shows to understand barriers to attendance
- **Re-engagement**: Invite no-shows to future similar events
- **Conversion Analysis**: Track which marketing channels lead to actual attendance vs just RSVPs
- **Segmentation**: Create separate email campaigns for engaged attendees vs one-time registrants

### Automatic Pipeline Integration

**The tagging script is automatically integrated into the pipeline** and runs as part of **Step 4** every 6 hours:

When the cron job detects a new event with attendance data:
1. Event is imported and analyzed
2. **Mailchimp tagging runs automatically** for that event (if credentials configured)
3. Attendees are tagged with `{event}_attended`
4. RSVP no-shows are tagged with `{event}_rsvp_no_show`
5. Each event is tagged **exactly once** - no duplicates

**How it prevents duplicate tagging:**
- The pipeline only processes events that have new attendance data
- Once an event is processed, it's never processed again
- Tagging happens immediately after the event is analyzed
- No manual intervention required

**Log output example:**
```
Step 4: Running single event analysis for newly imported events...
  Analyzing event ID: 123
  ✅ Event 123 analysis completed
  Tagging attendees (first-time/returning) and RSVP no-shows in Mailchimp for event 123...
  ✅ Event 123 Mailchimp tagging completed (12 first-time, 33 returning, 23 no-shows)
```

### Manual Usage

You can also run the script manually for specific events:

```bash
# Tag both attendees AND RSVP no-shows
python mailChimp/tag_mailchimp_attendees.py --event-id 123

# Tag only attendees if you prefer
python mailChimp/tag_mailchimp_attendees.py --event-id 123 --only-attendees
```

### Troubleshooting

**API Key Invalid:**
- Verify `MAILCHIMP_API_KEY` is correct in `.env`
- Ensure API key has proper permissions in Mailchimp

**Server Prefix Mismatch:**
- Check that `MAILCHIMP_SERVER_PREFIX` matches your API key (e.g., `us21`, `us12`)
- The server prefix is the part after the last dash in your API key

**No Attendees or RSVPs Found:**
- Verify the event ID exists in the database
- Check that attendees have `checked_in = TRUE` and RSVPs have `checked_in = FALSE` in the attendance table
- Use `--dry-run` to see the query results without calling the API

**Tagging Errors:**
- Some contacts may fail if they've previously unsubscribed
- Check Mailchimp logs for specific error messages
- The script will continue tagging other people even if some fail

**For More Details:**
See [MAILCHIMP_IMPROVEMENTS.md](MAILCHIMP_IMPROVEMENTS.md) for comprehensive documentation on:
- How the first-time vs returning attendee differentiation works
- Technical implementation details
- Complete feature changelog and improvements
- Troubleshooting common issues

---

### Audience Sync

The `mailChimp/sync_mailchimp_audience.py` script syncs your entire mailing list from the Railway database to Mailchimp.

#### What It Does

1. Queries all people in the database who have email addresses
2. Deduplicates using `COALESCE(school_email, personal_email)` - prefers school emails
3. Batch syncs all contacts to your Mailchimp audience
4. Creates new contacts and updates existing ones with latest names
5. **Does NOT** remove contacts from Mailchimp if they're removed from database (one-way sync)

#### Usage

Sync the entire mailing list manually:

```bash
# Sync entire audience to Mailchimp
python mailChimp/sync_mailchimp_audience.py

# Test without actually calling Mailchimp API
python mailChimp/sync_mailchimp_audience.py --dry-run

# Enable verbose logging
python mailChimp/sync_mailchimp_audience.py --verbose

# Custom batch size (default: 500, max: 500)
python mailChimp/sync_mailchimp_audience.py --batch-size 250
```

#### Example Output

```
Mailing List Sync
============================================================
Total contacts found: 1,234

============================================================
MAILCHIMP AUDIENCE SYNC RESULTS
============================================================
Total contacts processed: 1,234
New contacts added:       156
Existing contacts updated:1,078
Errors:                   0
============================================================
Success rate: 100.0%
```

#### Command-line Arguments

| Argument | Description | Required |
|----------|-------------|----------|
| `--dry-run` | Query database but skip Mailchimp API calls (for testing) | No |
| `--verbose` | Enable verbose logging for debugging | No |
| `--batch-size` | Number of contacts per batch (default: 500, max: 500) | No |

#### Automated Sync (Cron)

The audience sync automatically runs as **Step 7** of the pipeline cron job (every 6 hours):

```
CRON TRIGGER (every 6 hours)
  ↓
1. Sync events from Luma
2. Auto-approve RSVPs
3. Import attendance
4. Run event analysis + Tag attendees/no-shows in Mailchimp
5. Generate placards
6. Run analytics
7. Sync Mailchimp audience
```

The sync only runs if Mailchimp credentials are configured. If credentials are missing, it will skip gracefully with a message.

#### How Deduplication Works

The script uses the people table with email preference:

```sql
COALESCE(school_email, personal_email) as email
```

This means:
- If a person has a school email, that's used
- If no school email, personal email is used
- If both exist, school email is preferred
- **Result**: One contact per person in Mailchimp

#### Features

- **One-way Sync**: Database → Mailchimp only (never deletes from Mailchimp)
- **Batch Processing**: Handles large audiences efficiently (500 contacts per batch)
- **Deduplication**: One email per person (school email preferred)
- **Error Resilient**: Continues processing even if some contacts fail
- **Automatic**: Runs every 6 hours as part of cron pipeline
- **Manual**: Can be run standalone anytime
- **Dry Run Mode**: Test the sync without making API calls

#### Troubleshooting

**Sync Skipped in Cron:**
- Check that `MAILCHIMP_API_KEY`, `MAILCHIMP_SERVER_PREFIX`, and `MAILCHIMP_AUDIENCE_ID` are set in Railway
- Review cron logs to see skip message

**Duplicate Contacts:**
- The script should prevent duplicates via email deduplication
- If duplicates exist in Mailchimp from before, you may need to manually clean them
- Consider archiving old duplicates in Mailchimp dashboard

**Batch Errors:**
- Review logs for specific error messages
- Some contacts may fail due to invalid email formats
- Previously unsubscribed contacts may fail to update
- Mailchimp rate limits: max 10 requests/second (script respects this)

**Sync Takes Too Long:**
- For large audiences (10,000+ contacts), the sync may take several minutes
- Reduce `--batch-size` if you encounter timeout issues
- Check Mailchimp API status if consistent failures occur

## RSVP Management

The `extra/approve_rsvps.py` script provides an interactive tool for managing pending RSVPs for Luma events. It auto-approves RSVPs from Harvard/MIT students and prompts for manual review of others.

### Features

- **Auto-Approval**: Automatically approves RSVPs with `@college.harvard.edu` or `@mit.edu` emails
- **Dual Email Check**: Checks both main email field and "School Email (.edu)" custom field
- **Time Filtering**: Only auto-approve RSVPs from users who registered X hours ago
- **Manual Review**: Prompts for approve/decline decision on non-approved emails
- **Interactive Event Selection**: Choose from the 10 most recent events
- **Luma API Integration**: Updates RSVP status directly in Luma via API

### Setup

Ensure your `.env` file includes:
```bash
# Database credentials (required)
PGHOST=your-database-host
PGPORT=5432
PGDATABASE=railway
PGUSER=postgres
PGPASSWORD=your-password

# Luma API credentials (required)
LUMA_API_KEY=luma_sk_xxxxx
```

### Usage

Run the script interactively:

```bash
python extra/approve_rsvps.py
```

**Step 1: Select Time Filter Mode**

Choose how far back to auto-approve Harvard/MIT RSVPs:

```
1. any   - No time filter (approve all)
2. 1hr   - RSVPed 1+ hours ago
3. 12hr  - RSVPed 12+ hours ago
4. 24hr  - RSVPed 24+ hours ago
5. 48hr  - RSVPed 48+ hours ago
```

**Step 2: Select Event**

Choose from the 10 most recent events by number.

**Step 3: Review Pending RSVPs**

The script will:
1. **Auto-approve** Harvard/MIT RSVPs that meet the time filter criteria
2. **Skip** Harvard/MIT RSVPs that are too recent (based on time filter)
3. **Show for manual review** all non-Harvard/MIT RSVPs (regardless of time)

For manual reviews, you'll see:
```
────────────────────────────────────────────────────────────────────────────────
Name: John Doe
Email: john@example.com
School Email: john.doe@bu.edu
RSVP Time: 2026-02-23 10:30 AM UTC

Decline this RSVP? (y/n):
```

- Type `y` to decline the RSVP
- Type `n` to approve the RSVP

**Step 4: View Summary**

After processing, see a summary:
```
================================================================================
SUMMARY
================================================================================
Auto-approved (Harvard/MIT, old enough): 15
Skipped (Harvard/MIT, too recent): 3
Manually approved: 5
Declined: 2
Total processed: 22
```

### How It Works

#### Email Checking Logic

The script checks for approved domains in two places:
1. **Main email field** (`guest.email`)
2. **School Email (.edu)** custom registration field

If either contains `@college.harvard.edu` or `@mit.edu`, the RSVP qualifies for auto-approval.

#### Time Filter Logic

When you select a time filter (e.g., "24hr"):
- **Cutoff time** = Current time - 24 hours
- **Auto-approve**: RSVPs created before the cutoff time (≥ 24 hours ago)
- **Skip**: RSVPs created after the cutoff time (< 24 hours ago)

**Why Use Time Filters?**
- Prevent auto-approving last-minute RSVPs that might be spam
- Give preference to students who registered early
- Allow manual review of recent sign-ups

#### Processing Rules

| Scenario | Time Filter Met? | Action |
|----------|------------------|--------|
| Harvard/MIT email | Yes (old enough) | Auto-approve ✓ |
| Harvard/MIT email | No (too recent) | Skip ⊘ (stays pending) |
| Non-Harvard/MIT email | N/A (ignored) | Manual review → Approve or Decline |

**Important**: Non-approved emails are ALWAYS shown for manual review, regardless of when they RSVPed.

### Luma API Operations

The script uses two Luma API endpoints:

**Approve Guest:**
```python
POST https://public-api.luma.com/v1/event/update-guest-status
{
  "event_identifier": "evt-xxxxx",
  "guest_identifier": "gst-xxxxx",
  "status": "going"
}
```

**Decline Guest:**
```python
POST https://public-api.luma.com/v1/event/update-guest-status
{
  "event_identifier": "evt-xxxxx",
  "guest_identifier": "gst-xxxxx",
  "status": "not_going"
}
```

### Example Workflow

```bash
# Run the script
python extra/approve_rsvps.py

# Select time filter: 24hr
# Select event: Spring Networking Night
# Script processes RSVPs:
#   ✓ AUTO-APPROVED: Sarah Chen (sarah@college.harvard.edu)
#   ✓ AUTO-APPROVED: Michael Park (michael@mit.edu)
#   ⊘ SKIPPED (too recent): Emma Li (emma@college.harvard.edu)
#
#   ────────────────────────────────────────────────────────────────
#   Name: Alex Johnson
#   Email: alex@bu.edu
#   School Email: alex.johnson@bu.edu
#   RSVP Time: 2026-02-22 03:15 PM UTC
#
#   Decline this RSVP? (y/n): n
#   ✓ APPROVED: Alex Johnson
#
# Summary:
#   Auto-approved: 2
#   Skipped: 1
#   Manually approved: 1
#   Declined: 0
```

### Troubleshooting

**No Pending RSVPs Found:**
- Verify the event has pending RSVPs in Luma
- Check that the event has "Require Approval" enabled
- Ensure `luma_event_id` is correctly stored in the database

**API Authentication Errors:**
- Verify `LUMA_API_KEY` is correct in `.env`
- Check API key has permissions to update guest status
- Ensure you're using a valid Luma API key (starts with `luma_sk_`)

**Email Not Detected:**
- Check that custom field is named exactly "School email (.edu)" in Luma
- Verify the email addresses contain the exact domain strings
- Domains are case-insensitive (`@MIT.EDU` works the same as `@mit.edu`)

**Time Filter Not Working:**
- Ensure `created_at` field exists in Luma API response
- Check that RSVPs have valid timestamps
- The script uses UTC time for all comparisons

### Use Cases

- **Pre-Event Cleanup**: Review and approve RSVPs 24 hours before an event
- **Harvard/MIT Only Events**: Auto-approve students, manually review others
- **Spam Prevention**: Use 1hr+ filter to prevent last-minute spam RSVPs
- **Selective Approval**: Manually review non-students for exclusive events

## Luma RSVP Auto-Approval

The `luma/auto_approve_rsvps.py` script automatically approves pending Luma RSVPs based on attendance history and event timing. It runs as **Step 2** in the automated pipeline (every 6 hours) and can also be run manually.

### Auto-Approval Rules

The script processes events happening in the **next 2 weeks** and applies these rules to pending RSVPs:

**Auto-approve if:**
1. **Returning attendee**: Person has attended 2 or more events (based on `event_attendance_count` in database)
2. **Last-minute + verified email**: Event starts in ≤24 hours AND person has a Harvard/MIT email address

**Email verification checks:**
- Primary: Main RSVP email field
- Secondary: "School email (.edu)" custom registration field
- Database: Cross-reference with `school_email` or `personal_email` in people table

**Approved email domains:**
- `@college.harvard.edu`
- `@mit.edu`
- `@harvard.edu`

### Person Matching Strategy

The script matches Luma RSVPs to database records using this priority order:

1. **Email match**: Match by main email address
2. **School email match**: Match by "School email (.edu)" from registration form
3. **Name match**: Exact first + last name match (case-insensitive)

Once matched, the same approval rules apply regardless of matching method.

### Usage

**Preview without executing (recommended first run):**
```bash
python3 luma/auto_approve_rsvps.py --dry-run --verbose
```

**Execute approvals:**
```bash
python3 luma/auto_approve_rsvps.py
```

**Command-line options:**
- `--dry-run` - Preview what would be approved without making any API calls
- `--verbose` - Enable detailed debug logging (shows each RSVP decision)

### Example Output

```
2024-02-27 10:30:15 - INFO - Starting Luma RSVP auto-approval script
2024-02-27 10:30:16 - INFO - Fetched 15 events from Luma
2024-02-27 10:30:16 - INFO - Found 3 events in next 2 weeks

============================================================
Processing: Camel Coffee Chat
Start time: 2024-02-28 04:00 PM EST
Event ID: evt-abc123
2024-02-27 10:30:17 - INFO - Found 12 pending RSVPs
2024-02-27 10:30:18 - INFO - Approved: student1@college.harvard.edu
2024-02-27 10:30:18 - INFO - Approved: student2@mit.edu (returning attendee, 5 events)
2024-02-27 10:30:18 - INFO - Event summary: 8 approved, 4 skipped, 0 errors

============================================================
FINAL SUMMARY
Events processed: 3
Total approved: 24
Total skipped: 8
Total errors: 0
```

### Pipeline Integration

This script is automatically run as part of `run_luma_pipeline.sh` (Step 2) to ensure timely RSVP approvals:

```
CRON TRIGGER (every 6 hours)
  ↓
1. Sync events from Luma
2. Auto-approve RSVPs ← This script runs here
3. Import attendance
4. Run event analysis
...
```

### Troubleshooting

**No RSVPs being approved:**
- Run with `--verbose` to see decision reasons for each RSVP
- Check that persons exist in database with correct emails
- Verify `event_attendance_count` is updated (run `import_luma_attendance.py`)

**API authentication errors:**
- Verify `LUMA_API_KEY` is correct in `.env`
- Check API key hasn't expired or been rotated

**Timezone issues:**
- Script uses event timezone from Luma API
- 24-hour calculation accounts for timezone differences

### Customization

To adjust auto-approval criteria, modify these constants in the script:

```python
# Minimum attendance count for auto-approval
if person.get('attendance_count', 0) >= 2:  # Change 2 to desired threshold

# Hours before event for last-minute approvals
if hours_until_event <= 24:  # Change 24 to desired hours

# Approved email domains
APPROVED_DOMAINS = ['@college.harvard.edu', '@mit.edu', '@harvard.edu']

# Look-ahead window for events
upcoming_events = filter_upcoming_events(all_events, weeks=2)  # Change weeks=2
```

---

## Guest List Export

The `extra/export_guest_list.py` script exports approved guest lists directly from the Luma API to CSV format, including all registration form answers.

### Features

- **Direct Luma API Integration**: Queries Luma directly (no database required)
- **Interactive Event Selection**: Choose from 3-5 most recent events
- **Approved Guests Only**: Filters for `approval_status == 'approved'`
- **Complete Registration Data**: Exports all standard fields plus custom form answers
- **Automatic Pagination**: Handles events with any number of guests
- **Dynamic Columns**: Automatically discovers and includes all registration questions

### Setup

Ensure your `.env` file includes:
```bash
LUMA_API_KEY=luma_sk_xxxxx
LUMA_CALENDAR_ID=cal-xxxxx
```

### Usage

Run the script interactively:

```bash
python3 extra/export_guest_list.py
```

**Step 1: View Recent Events**

The script displays 3-5 most recent events:
```
============================================================
LUMA GUEST LIST EXPORTER
============================================================

Found 5 recent events:

  1. CamelHack 2026 Demo Day
     Date: 2026-03-08

  2. CamelHack Kick Off
     Date: 2026-03-07

  3. Jon Hirschtick x CAMEL
     Date: 2026-02-18

Select an event (1-5):
```

**Step 2: Select Event**

Enter the event number to export its guest list.

**Step 3: Automatic Export**

The script will:
1. Fetch all guests from Luma API (with pagination)
2. Filter for approved guests only
3. Extract all registration form answers
4. Export to CSV in `test_output/` directory

### Output

**CSV Location**: `test_output/{event_name}_guest_list.csv`

**Columns Exported**:
- `user_first_name` - Guest's first name
- `user_last_name` - Guest's last name
- `user_name` - Full name
- `email` - Email address
- `phone_number` - Phone number
- **All registration form questions** (dynamically discovered)
  - Example: "School email (.edu)"
  - Example: "Gender"
  - Example: "Grad year"
  - Example: "What brings you to Camel?"
  - Example: "What major are you?"
  - Example: "What school clubs are you involved in?"

### Example Output

```
Selected: Jon Hirschtick x CAMEL

Fetching guests...
  Page 1: 50 guests (total: 50)
  Page 2: 50 guests (total: 100)
  Page 3: 42 guests (total: 142)

Total guests fetched: 142
Approved guests: 135

Extracting guest data...

============================================================
EXPORT COMPLETE
============================================================
Event: Jon Hirschtick x CAMEL
Approved guests exported: 135
Output file: test_output/jon_hirschtick_x_camel_guest_list.csv
Columns: 11

Columns included:
  - user_first_name
  - user_last_name
  - user_name
  - email
  - phone_number
  - Gender
  - Grad year
  - School email (.edu)
  - What brings you to Camel?
  - What major are you?
  - What school clubs are you involved in?
============================================================
```

### Features

- **No Database Required**: Queries Luma API directly
- **Pagination Handling**: Automatically handles events with 50+ guests
- **Safe Filenames**: Sanitizes event names for filesystem compatibility
- **Dynamic Discovery**: Automatically includes all registration form questions
- **Organized Output**: All CSVs saved to `test_output/` directory
- **Status Summary**: Shows total guests, approved count, and column list

### Use Cases

- **Event Check-In**: Print guest lists for registration tables
- **Email Campaigns**: Export approved guests for targeted emails
- **Data Analysis**: Analyze registration form responses in Excel/Sheets
- **Attendee Coordination**: Share guest lists with event staff
- **Marketing**: Create mailing lists for specific events

### Troubleshooting

**No Events Found:**
- Verify `LUMA_CALENDAR_ID` is set correctly in `.env`
- Check that your calendar has events
- Ensure `LUMA_API_KEY` has access to the calendar

**API Authentication Errors:**
- Verify `LUMA_API_KEY` is correct in `.env`
- Ensure API key has permissions to read events and guests
- Check that the API key is valid and not expired

**No Approved Guests:**
- Verify the event has approved RSVPs in Luma
- Check that guests have `approval_status == 'approved'`
- Some events may have pending RSVPs that need manual approval

**Missing Registration Columns:**
- Verify the event has a registration form with custom questions
- Check that guests filled out the registration form
- Empty/unanswered questions will appear as blank cells in the CSV

---

## Attendee List Export

The `extra/export_attendee_list.py` script exports checked-in attendee lists from the PostgreSQL database to CSV format, including all registration form answers. This is similar to `export_guest_list.py`, but exports actual attendees (who checked in) rather than approved RSVPs from Luma.

### Features

- **Database-Driven**: Queries PostgreSQL database directly (not Luma API)
- **Interactive Event Selection**: Choose from 5 most recent events
- **Checked-In Attendees Only**: Filters for `checked_in = TRUE` in attendance table
- **Complete Registration Data**: Exports all standard fields plus custom form answers from `additional_info` JSON
- **Dynamic Columns**: Automatically discovers and includes all registration questions stored in database
- **Same Format as Guest List**: Maintains consistency with `export_guest_list.py` output format

### Setup

Ensure your `.env` file includes database credentials:
```bash
PGHOST=your-database-host
PGPORT=5432
PGDATABASE=railway
PGUSER=postgres
PGPASSWORD=your-password
```

### Usage

Run the script interactively:

```bash
python3 extra/export_attendee_list.py
```

**Step 1: View Recent Events**

The script displays 5 most recent events from the database:
```
============================================================
DATABASE ATTENDEE LIST EXPORTER
============================================================

Found 5 recent events:

  1. CamelHack 2026 Demo Day
     Date: 2026-03-08

  2. CamelHack Kick Off
     Date: 2026-03-07

  3. Jon Hirschtick x CAMEL
     Date: 2026-02-18

Select an event (1-5):
```

**Step 2: Select Event**

Enter the event number to export its attendee list.

**Step 3: Automatic Export**

The script will:
1. Query all attendees from database where `checked_in = TRUE`
2. Parse `additional_info` JSON for each attendee
3. Extract all registration form answers
4. Export to CSV in `test_output/` directory

### Output

**CSV Location**: `test_output/{event_name}_attendee_list.csv`

**Columns Exported**:
- `first_name` - Attendee's first name
- `last_name` - Attendee's last name
- `email` - Email address (coalesced from school_email/personal_email)
- `phone_number` - Phone number
- **All registration form questions** (from `additional_info` JSON)
  - Example: "Gender"
  - Example: "Grad year"
  - Example: "School email (.edu)"
  - Example: "What brings you to Camel?"
  - Example: "What major are you?"
  - Example: "What school clubs are you involved in?"

### Example Output

```
Selected: Jon Hirschtick x CAMEL

Fetching attendees...
  Found 101 checked-in attendees

Extracting attendee data...

============================================================
EXPORT COMPLETE
============================================================
Event: Jon Hirschtick x CAMEL
Checked-in attendees exported: 101
Output file: test_output/jon_hirschtick_x_camel_attendee_list.csv
Columns: 10

Columns included:
  - first_name
  - last_name
  - email
  - phone_number
  - Gender
  - Grad year
  - School email (.edu)
  - What brings you to Camel?
  - What major are you?
  - What school clubs are you involved in?
============================================================
```

### Comparison: Guest List vs Attendee List

| Feature | `export_guest_list.py` | `export_attendee_list.py` |
|---------|------------------------|---------------------------|
| **Data Source** | Luma API | PostgreSQL Database |
| **Filter** | `approval_status == 'approved'` | `checked_in = TRUE` |
| **People Included** | Approved RSVPs | Actual attendees who checked in |
| **Custom Answers Source** | `registration_answers` array (Luma) | `additional_info` JSON (database) |
| **Requires Database** | No | Yes |
| **Requires Luma API** | Yes | No |
| **Best For** | Pre-event planning, check-in lists | Post-event analysis, actual attendance |

### Use Cases

- **Post-Event Analysis**: Export actual attendees for follow-up surveys
- **Attendance Records**: Create historical records of who actually attended
- **Email Campaigns**: Target only people who showed up (higher engagement)
- **Data Analysis**: Analyze demographics of actual attendees vs RSVPs
- **Certificates/Recognition**: Generate certificates for attendees
- **Event Reporting**: Report actual attendance with full registration data

### Troubleshooting

**No Events Found:**
- Verify database connection is working
- Check that events exist in the `events` table
- Ensure `.env` has correct database credentials

**Database Connection Errors:**
- Verify all `PG*` environment variables are correct in `.env`
- Check that the database is accessible
- Ensure database credentials have SELECT permissions

**No Attendees Found:**
- Verify the event has attendees with `checked_in = TRUE`
- Check that attendance data has been imported from Luma
- Run `import_luma_attendance.py` if attendance data is missing

**Missing Registration Columns:**
- Verify `people.additional_info` JSON field is populated
- Check that `import_luma_attendance.py` has been run to sync data
- Some people may have empty `additional_info` if they registered before custom fields were added

**Column Differences from Guest List:**
- Attendee list uses `first_name`/`last_name` (database fields)
- Guest list uses `user_first_name`/`user_last_name` (Luma fields)
- Attendee list doesn't include `user_name` field (not stored in database)
- Both formats include all custom registration questions

## Add Person to Database

The `extra/add_person.py` script provides an interactive tool for manually adding people to the database. It's useful for quickly adding contacts who may not have attended events yet.

### Features

- **Interactive Prompts**: Prompts for email (required), first name (optional), and last name (optional)
- **Auto-detect Email Field**: `.edu` emails are stored in `school_email`, others in `personal_email`
- **Duplicate Detection**: Checks all email fields before inserting to prevent duplicates
- **Flexible Name Input**: First and last names can be skipped (uses empty strings for database NOT NULL constraints)
- **Clear Feedback**: Shows success message with person ID and details, or error if duplicate found

### Setup

Ensure your `.env` file includes database credentials:
```bash
PGHOST=your-database-host
PGPORT=5432
PGDATABASE=railway
PGUSER=postgres
PGPASSWORD=your-password
```

### Usage

Run the script interactively:

```bash
python3 extra/add_person.py
```

**Interactive Prompts:**

```
================================================================================
ADD PERSON TO DATABASE
================================================================================

Email (required): student@harvard.edu
First name (press Enter to skip): John
Last name (press Enter to skip): Doe

✓ Successfully added person!
   Person ID: 1234
   Name: John Doe
   Email: student@harvard.edu
   Email Field: school_email
```

**Skipping Names:**

```bash
Email (required): contact@example.com
First name (press Enter to skip): [Enter]
Last name (press Enter to skip): [Enter]

✓ Successfully added person!
   Person ID: 1235
   Name: (empty) (empty)
   Email: contact@example.com
   Email Field: personal_email
```

### How It Works

#### Email Field Auto-Detection

The script automatically determines which email field to use:

| Email Domain | Database Field | Example |
|--------------|----------------|---------|
| Ends with `.edu` | `school_email` | `student@harvard.edu` → `school_email` |
| Other domains | `personal_email` | `john@gmail.com` → `personal_email` |

#### Duplicate Detection

Before inserting, the script checks if the email already exists in any of these fields:
- `school_email`
- `personal_email`
- `preferred_email`

If found, it displays the existing person's information and exits without creating a duplicate.

**Example - Duplicate Found:**

```
Email (required): existing@mit.edu
First name (press Enter to skip): Jane
Last name (press Enter to skip): Smith

❌ Duplicate email found!
   Person ID: 456
   Name: Jane Smith
   School Email: existing@mit.edu
   Personal Email: N/A
```

#### Name Handling

- **First and last names are optional** - you can press Enter to skip either or both
- **Empty strings** are stored in the database to satisfy NOT NULL constraints
- **Required field**: Only email is required; the script will keep prompting until provided

### Use Cases

- **Manual Contact Entry**: Add people who expressed interest but haven't RSVPed yet
- **Team Member Addition**: Add staff or volunteers who need database records
- **Quick Data Entry**: Faster than SQL INSERT statements for one-off additions
- **Email List Building**: Add contacts from business cards or sign-up sheets
- **Testing**: Create test records with specific email formats

### Troubleshooting

**Database Connection Errors:**
- Verify all `PG*` environment variables are correct in `.env`
- Check that the database is accessible
- Ensure database credentials have INSERT permissions on the `people` table

**Email Validation:**
- The script doesn't validate email format - ensure you enter valid emails
- Case-insensitive duplicate checking (e.g., `JOHN@MIT.EDU` matches `john@mit.edu`)

**Empty Name Fields:**
- Empty names are allowed and stored as empty strings (`""`)
- You can update names later through the database or other scripts

**Duplicate Email:**
- If duplicate is found, no insertion occurs
- You can update the existing person record through the database if needed

## Merge Duplicate People

The `extra/merge_duplicate_people.py` script finds and merges duplicate people records in the database based on matching identifiers (email addresses or phone numbers). This is useful for cleaning up the database when the same person has been registered multiple times with slight variations in their information.

### Features

- **Smart Duplicate Detection**: Identifies people who share the same `school_email`, `personal_email`, or `phone_number`
- **Connected Component Analysis**: Groups together all people connected through any shared identifier (e.g., if Person A shares email with Person B, and Person B shares phone with Person C, all three are grouped together)
- **Interactive Merging**: Shows detailed information for each duplicate group and prompts for confirmation before merging
- **Data Preservation**: Combines the best non-NULL values from all duplicate records
- **Relationship Updates**: Automatically reassigns all related records (attendance, promo codes, event feedback) to the primary person
- **Safe Operations**: Uses database transactions with rollback on error
- **Dry Run Mode**: Preview what would be merged without making any changes

### Setup

Ensure your `.env` file includes database credentials:
```bash
PGHOST=your-database-host
PGPORT=5432
PGDATABASE=railway
PGUSER=postgres
PGPASSWORD=your-password
```

### Usage

**Preview duplicates without making changes (recommended first run):**
```bash
python3 extra/merge_duplicate_people.py --dry-run
```

**Interactive merge with confirmation prompts:**
```bash
python3 extra/merge_duplicate_people.py
```

### How It Works

#### Duplicate Detection Strategy

The script finds groups of people who share any of these identifiers:
- **School email**: Same `school_email` value (case-insensitive)
- **Personal email**: Same `personal_email` value (case-insensitive)
- **Phone number**: Same `phone_number` value

**Connected components**: If Person A shares an email with Person B, and Person B shares a phone number with Person C, all three are considered duplicates and grouped together.

#### Merging Strategy

For each duplicate group:
1. **Primary record**: Keeps the person with the lowest ID (oldest record)
2. **Data merging**: For each field, uses the first non-NULL value found across all duplicates
3. **Related records**: Updates all references in these tables to point to the primary record:
   - `attendance.person_id`
   - `promo_codes.person_id`
   - `event_feedback.person_id`
4. **Deletion**: Removes duplicate person records after reassigning their data
5. **Count recalculation**: Updates `event_attendance_count` for the merged person

### Interactive Example

```
================================================================================
DUPLICATE GROUP FOUND (2 records)
================================================================================
  Record 1 [PRIMARY - lowest ID]:
    ID: 1928
    Name: Gabriel Barnes
    Class Year: None
    School Email: gabrielabrams@college.harvard.edu
    Personal Email: gabrielabrams99@gmail.com
    Preferred Email: gabrielabrams@college.harvard.edu
    Phone: None
    School: None
    Gender: None
    Is Jewish: None
    Event Attendance: 0
    Event RSVPs: 0
    Related records: 4 attendance, 0 promo codes, 0 feedback

  Record 2:
    ID: 2123
    Name: Gabriel Abrams
    Class Year: None
    School Email: None
    Personal Email: gabrielabrams100@gmail.com
    Preferred Email: gabrielabrams100@gmail.com
    Phone: None
    School: None
    Gender: None
    Is Jewish: None
    Event Attendance: 0
    Event RSVPs: 0
    Related records: 0 attendance, 0 promo codes, 0 feedback

--------------------------------------------------------------------------------
MERGED RECORD (combining non-NULL values):
    ID: 1928
    Name: Gabriel Barnes
    Class Year: None
    School Email: gabrielabrams@college.harvard.edu
    Personal Email: gabrielabrams99@gmail.com
    Preferred Email: gabrielabrams@college.harvard.edu
    Phone: None
    School: None
    Gender: None
    Is Jewish: None
    Event Attendance: 0
    Event RSVPs: 0
--------------------------------------------------------------------------------

Total related records to merge:
  - 4 attendance records
  - 0 promo code records
  - 0 event feedback records

Duplicate records to delete: 1

Merge these records? [y/N]: y

✓ Successfully merged 2 records into ID 1928
```

### Command-line Arguments

| Argument | Description | Required |
|----------|-------------|----------|
| `--dry-run` | Show what would be merged without making database changes | No |

### Output Summary

After processing all duplicate groups, you'll see:
```
================================================================================
SUMMARY
================================================================================
Total duplicate groups found: 5
Merged: 3
Skipped: 2
================================================================================
```

### Use Cases

- **Name Typos**: Merge records where names were spelled differently ("Gabriel Barnes" vs "Gabriel Abrams")
- **Multiple Registrations**: Combine records when someone registered for different events with different emails
- **Data Quality**: Clean up the database by removing duplicate entries
- **Contact Consolidation**: Ensure each person has only one record with complete information
- **Pre-Analysis Cleanup**: Run before generating analytics to ensure accurate attendance counts

### Common Scenarios

**Scenario 1: Same person, different emails**
- Person registered for Event 1 with `student@college.harvard.edu`
- Same person registered for Event 2 with `student.name@gmail.com`
- Script merges both records, preserving both email addresses

**Scenario 2: Phone number match**
- Two records with different names but same phone number
- Script identifies them as potential duplicates
- You review and confirm if they're the same person

**Scenario 3: Connected duplicates**
- Person A and B share email `john@harvard.edu`
- Person B and C share phone `(555) 123-4567`
- All three are grouped together for merging

### Troubleshooting

**No Duplicates Found:**
- This is good! Your database is clean
- Double-check by looking for similar names or email patterns manually

**Too Many False Positives:**
- The script may group people who genuinely share phones (e.g., family members)
- Use interactive mode and carefully review each group before confirming
- Choose "N" to skip merging groups that aren't truly duplicates

**Merge Failed:**
- Check database logs for specific error messages
- Ensure database credentials have UPDATE and DELETE permissions
- The transaction will roll back automatically, leaving the database unchanged

**Data Loss Concerns:**
- The script only keeps non-NULL values, so no data is lost
- Primary record (lowest ID) is always preserved
- All related records (attendance, promo codes, feedback) are reassigned, not deleted
- Use `--dry-run` first to preview changes without risk

**Database Connection Errors:**
- Verify all `PG*` environment variables are correct in `.env`
- Check that the database is accessible
- Ensure database credentials have proper permissions

### Important Notes

- **Backup first**: Consider backing up your database before running merge operations
- **Review carefully**: Always review the merged record preview before confirming
- **Irreversible**: Once merged and confirmed, the duplicate records are deleted (though their data lives on in the primary record)
- **Attendance preserved**: All attendance records are moved to the primary person, so event counts remain accurate

## License

This project is for internal use.
