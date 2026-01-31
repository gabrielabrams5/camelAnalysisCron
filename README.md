# Event Analytics Cron Job

Automated weekly analytics for event attendance and RSVP data, running on Railway with persistent image storage.

## What This Does

This application connects to a PostgreSQL database to analyze event attendance patterns and generates:

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

## Schedule

The cron job runs **weekly on Sunday at midnight UTC (00:00)**.

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
   ```

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
   ```

3. **Run the script:**
   ```bash
   python analyze.py
   ```

   Or specify a custom output directory:
   ```bash
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
├── analyze.py           # Main analytics script
├── requirements.txt     # Python dependencies
├── Dockerfile          # Container configuration with cron
├── railway.toml        # Railway deployment config
├── .env                # Environment variables (not in git)
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `PGHOST` | PostgreSQL host | `yamabiko.proxy.rlwy.net` |
| `PGPORT` | PostgreSQL port | `58300` |
| `PGDATABASE` | Database name | `railway` |
| `PGUSER` | Database user | `postgres` |
| `PGPASSWORD` | Database password | `your-password-here` |

## Customizing the Schedule

To change the cron schedule, modify the cron expression in `Dockerfile`:

```dockerfile
# Current: Weekly on Sunday at midnight
RUN echo "0 0 * * 0 cd /app && ..." > /etc/cron.d/analytics-cron

# Daily at 2 AM:
RUN echo "0 2 * * * cd /app && ..." > /etc/cron.d/analytics-cron

# Every 6 hours:
RUN echo "0 */6 * * * cd /app && ..." > /etc/cron.d/analytics-cron
```

[Cron expression reference](https://crontab.guru/)

## Troubleshooting

### Job not running

1. Check Railway logs for errors
2. Verify environment variables are set correctly
3. Ensure the volume is properly mounted at `/app/analysis_outputs`

### Database connection errors

1. Verify all `PG*` environment variables are correct
2. Check that the database is accessible from Railway
3. Confirm database credentials have proper permissions

### Files not persisting

1. Verify the Railway volume is mounted at `/app/analysis_outputs`
2. Check that the volume has sufficient storage space
3. Ensure the cron job is writing to the correct path

## License

This project is for internal use.
