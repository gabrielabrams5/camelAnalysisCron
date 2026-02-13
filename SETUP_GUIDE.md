# Luma Event Sync Setup Guide

## What Was Created

Your Luma event synchronization system is now complete! Here's what was built:

### New Files Created

1. **luma_sync.py** - Fetches events from Luma API and downloads attendance CSVs
2. **import_luma_attendance.py** - Processes CSVs and imports attendance data
3. **run_luma_pipeline.sh** - Orchestrates the full pipeline
4. **SETUP_GUIDE.md** - This file

### Files Modified

1. **requirements.txt** - Added `requests` library for API calls
2. **schema.sql** - Added `luma_event_id` and `attendance_data` columns
3. **Dockerfile** - Updated to include new scripts and run pipeline
4. **entrypoint.py** - Updated to run full pipeline instead of just analytics
5. **.env** - Added `LUMA_API_KEY` placeholder
6. **README.md** - Comprehensive documentation for the new system

## Quick Start Checklist

### 1. Database Migration ✓ Required

Run this SQL on your Railway PostgreSQL database:

```sql
ALTER TABLE events ADD COLUMN luma_event_id VARCHAR(100);
ALTER TABLE events ADD COLUMN attendance_data JSONB;
CREATE INDEX idx_events_luma_id ON events(luma_event_id);
```

### 2. Get Your Luma API Key ✓ Required

1. Log into Luma
2. Go to Settings → API or Developer Settings
3. Generate an API key
4. Copy the key (it will look like `lu_api_...`)

### 3. Configure Luma API Endpoints ⚠️ Important

The Luma API endpoints in `luma_sync.py` are currently **placeholders**. You need to update them:

**Open `luma_sync.py` and update:**

- Line 35: `LUMA_API_BASE_URL` - Set to actual Luma API base URL
- Line 62: Event listing endpoint URL
- Line 95: CSV download endpoint URL

**Example (update with real endpoints):**
```python
LUMA_API_BASE_URL = 'https://api.lu.ma/public/v1'  # Real URL needed

# In get_luma_events():
url = f'{LUMA_API_BASE_URL}/calendar/list-events'  # Real endpoint needed

# In download_event_csv():
url = f'{LUMA_API_BASE_URL}/event/get-guests-export'  # Real endpoint needed
```

Refer to the [Luma API docs](https://docs.lu.ma/reference/api-overview) for correct endpoints.

### 4. Configure CSV Column Mapping ⚠️ Important

The CSV import expects specific column names. If your Luma CSV has different columns:

**Open `import_luma_attendance.py` and update the `COLUMN_MAPPING` (lines 41-53):**

```python
COLUMN_MAPPING = {
    'first_name': 'First Name',          # Update if different
    'last_name': 'Last Name',            # Update if different
    'email': 'Email',                    # Update if different
    'school_email': 'What is your school email?',
    'phone': 'Phone Number',
    # ... etc
}
```

Download a sample CSV from Luma and match the column names.

### 5. Update Environment Variables

#### Local Development (.env file):
```bash
# Already in your .env:
PGHOST=yamabiko.proxy.rlwy.net
PGPORT=58300
PGDATABASE=railway
PGUSER=postgres
PGPASSWORD=MURyJhuWJvkGJbbhVLInwSimZeanilKF

# ADD THIS - replace with your real API key:
LUMA_API_KEY=your_actual_luma_api_key_here
```

#### Railway Deployment:

1. Go to your Railway project
2. Click on Variables
3. Add new variable:
   - Name: `LUMA_API_KEY`
   - Value: Your actual Luma API key

### 6. Test Locally (Recommended)

Before deploying to Railway, test locally:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Update .env with your LUMA_API_KEY

# 3. Run the sync script
python luma_sync.py

# 4. If successful, run the full pipeline
bash run_luma_pipeline.sh
```

**Expected behavior:**
- Syncs future events to database
- Downloads CSVs for past events (if any)
- Imports attendance data
- Runs analytics

### 7. Deploy to Railway

Once local testing works:

```bash
# Commit all changes
git add .
git commit -m "Add Luma event sync pipeline"
git push

# Railway will auto-deploy
```

Monitor the Railway logs to ensure:
- Environment variables are loaded
- Cron job is created successfully
- Initial pipeline run completes

## How It Works

### Pipeline Flow

```
Every 6 hours, the cron job runs:

1. luma_sync.py
   ├─ Fetches all events from Luma API
   ├─ For future events:
   │  └─ Creates new events OR updates missing fields in DB
   ├─ For past events (>1 day old):
   │  ├─ Checks if attendance == 0 (not processed yet)
   │  └─ Downloads CSV if not processed
   └─ Outputs JSON list of CSVs to process

2. import_luma_attendance.py (conditional)
   ├─ Reads CSV files from Step 1
   ├─ For each attendee:
   │  ├─ Tries to match to existing person:
   │  │  ├─ By email (school or personal)
   │  │  ├─ By phone number
   │  │  ├─ By exact name match
   │  │  └─ By fuzzy name match
   │  ├─ Creates new person if no match
   │  ├─ Updates person fields:
   │  │  ├─ Gender, school, class year
   │  │  ├─ Email addresses, phone number
   │  │  └─ Only updates NULL fields (preserves existing data)
   │  └─ Creates attendance record
   └─ Updates event.attendance count

3. analyze.py (always runs)
   ├─ Generates analytics graphs
   └─ Saves statistics to database
```

## Data Synced from Luma

### Event Metadata (from Luma API)
- Event name → `event_name`
- Start datetime → `start_datetime`
- Description → `speaker_bio_short`
- Signup URL → `posh_url`
- Luma event ID → `luma_event_id` (for tracking)

### Person Data (from Luma CSV)
- First name, last name
- Email addresses (school and personal)
- Phone number
- Gender (normalized to M/F/None)
- School (harvard/mit/other)
- Class year

### Attendance Data (from Luma CSV)
- RSVP status
- Approval status
- Check-in status
- RSVP datetime
- Invite token (for referral tracking)

## Customization Options

### Change Sync Frequency

Edit `Dockerfile` line 32:

```dockerfile
# Every 6 hours (current)
RUN echo "0 */6 * * * cd /app && ..."

# Daily at 2 AM
RUN echo "0 2 * * * cd /app && ..."

# Every hour
RUN echo "0 * * * * cd /app && ..."
```

### Adjust Person Matching Sensitivity

Edit `import_luma_attendance.py` line 47:

```python
FUZZY_MATCH_THRESHOLD = 0.80  # Increase to 0.85 or 0.90 for stricter matching
```

### Add More CSV Fields

1. Update `COLUMN_MAPPING` in `import_luma_attendance.py`
2. Modify `find_or_create_person()` to extract the new field
3. Update database schema if needed

## Common Issues & Solutions

### Issue: "LUMA_API_KEY not found"
**Solution:** Add `LUMA_API_KEY` to Railway variables or .env file

### Issue: API calls fail with 404
**Solution:** Update the Luma API endpoints in `luma_sync.py` with correct URLs from Luma API documentation

### Issue: CSV column not found
**Solution:** Download a sample CSV from Luma, check column names, update `COLUMN_MAPPING` in `import_luma_attendance.py`

### Issue: Events created but no attendance data
**Solution:** Check that:
1. CSV download is working
2. CSV has the expected columns
3. Events have `attendance == 0` (so they get processed)

### Issue: Duplicate person records
**Solution:** Fuzzy matching may need adjustment. Increase `FUZZY_MATCH_THRESHOLD` to be more strict

## Next Steps

1. ✅ Run database migration
2. ✅ Get Luma API key
3. ✅ Update Luma API endpoints in `luma_sync.py`
4. ✅ Update CSV column mapping in `import_luma_attendance.py`
5. ✅ Test locally
6. ✅ Add `LUMA_API_KEY` to Railway
7. ✅ Deploy to Railway
8. ✅ Monitor first sync in Railway logs

## Support

- Check `README.md` for detailed documentation
- Review Railway logs for error messages
- Test components individually (luma_sync.py, import_luma_attendance.py, analyze.py)
- Verify database schema matches expected structure

---

**Ready to go!** Once you complete the setup checklist, your Luma events will automatically sync every 6 hours. 🎉
