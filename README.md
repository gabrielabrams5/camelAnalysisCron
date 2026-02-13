# Luma Event Sync & Analytics Cron Service

Automated event synchronization from Luma API, attendance data import, and analytics generation, running on Railway.

## What This Does

This application provides a complete pipeline for managing events from Luma:

### 1. **Luma Event Sync** (`luma_sync.py`)
- Fetches all events from Luma API
- **Future events**: Creates new events or updates missing metadata in the database
  - Event name, start datetime, description (speaker bio), signup URL
- **Past events** (>1 day old): Downloads attendance CSV files for events that haven't been processed yet

### 2. **Attendance Import** (`import_luma_attendance.py`)
- Processes Luma CSV files with sophisticated person matching:
  - Matches by email → phone → exact name → fuzzy name matching
  - Creates new person records if no match found
  - Updates person fields: gender, school, class year, contact info
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

## Pipeline Execution Flow

```
CRON TRIGGER (every 6 hours)
  ↓
luma_sync.py
  ├─ Fetch all Luma events via API
  ├─ Future events: CREATE/UPDATE in database
  ├─ Past events with attendance==0: Download CSV
  └─ Output: List of CSVs to process
  ↓
import_luma_attendance.py (only if CSVs downloaded)
  ├─ Match/create people (email→phone→name→fuzzy)
  ├─ Update person data (gender, school, year, contact info)
  └─ Create attendance records
  ↓
analyze.py (always runs)
  ├─ Generate analytics graphs
  └─ Save statistics to database
```

## Schedule

The cron job runs **every 6 hours** to keep events synchronized with Luma.

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
   PGHOST=your-database-host
   PGPORT=your-database-port
   PGDATABASE=your-database-name
   PGUSER=your-database-user
   PGPASSWORD=your-database-password
   LUMA_API_KEY=your-luma-api-key
   ```

   **Note:** Replace `your-luma-api-key` with your actual Luma API key.

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

   # Import attendance (requires event CSVs)
   python import_luma_attendance.py

   # Run analytics only
   python analyze.py --outdir custom_output_folder
   ```

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

## Database Migration

Before deploying, add the new `luma_event_id` column to your events table:

```sql
-- Connect to your database and run:
ALTER TABLE events ADD COLUMN luma_event_id VARCHAR(100);
ALTER TABLE events ADD COLUMN attendance_data JSONB;

-- Optional: Add index for faster lookups
CREATE INDEX idx_events_luma_id ON events(luma_event_id);
```

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

### CSV Column Mapping

The `import_luma_attendance.py` script expects specific CSV columns from Luma. If your CSV format differs, update the `COLUMN_MAPPING` dictionary in the script:

```python
COLUMN_MAPPING = {
    'first_name': 'First Name',
    'last_name': 'Last Name',
    'email': 'Email',
    # ... add your custom mappings
}
```

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

### Attendance CSV import errors

1. Check that CSV column names match `COLUMN_MAPPING` in `import_luma_attendance.py`
2. Verify Luma CSV download endpoint is correct
3. Check for missing required columns (First Name, Last Name, Email)
4. Review logs for person matching issues

### Duplicate person records

1. The fuzzy matching threshold may be too low - adjust in `import_luma_attendance.py`
2. Verify email matching is working (check for typos in CSV)
3. Consider adding manual cleanup SQL queries for known duplicates

### Files not persisting

1. Verify the Railway volume is mounted at `/app/analysis_outputs`
2. Check that the volume has sufficient storage space
3. Ensure the cron job is writing to the correct path

## License

This project is for internal use.
