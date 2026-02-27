# Luma Integration Scripts

This directory contains scripts for managing Luma event RSVPs and automation.

## Scripts

### `auto_approve_rsvps.py`

Automatically approves pending Luma RSVPs based on attendance history and event timing.

#### Auto-Approval Rules

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

#### Person Matching Strategy

The script matches Luma RSVPs to database records using this priority order:

1. **Email match**: Match by main email address
2. **School email match**: Match by "School email (.edu)" from registration form
3. **Name match**: Exact first + last name match (case-insensitive)

Once matched, the same approval rules apply regardless of matching method.

#### Usage

**Preview without executing (recommended first run):**
```bash
python3 luma/auto_approve_rsvps.py --dry-run
```

**Execute approvals:**
```bash
python3 luma/auto_approve_rsvps.py
```

**Detailed logging (for debugging):**
```bash
python3 luma/auto_approve_rsvps.py --verbose
```

**Dry run with detailed logging:**
```bash
python3 luma/auto_approve_rsvps.py --dry-run --verbose
```

#### Command-Line Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview what would be approved without making any API calls |
| `--verbose` | Enable detailed debug logging (shows each RSVP decision) |

#### Output

The script provides:
- Per-event summaries showing approved/skipped/error counts
- Final summary across all processed events
- Detailed decision logging in verbose mode

**Example output:**
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
2024-02-27 10:30:18 - INFO - Approved: student2@mit.edu
2024-02-27 10:30:18 - INFO - Event summary: 8 approved, 4 skipped, 0 errors

============================================================
FINAL SUMMARY
Events processed: 3
Total approved: 24
Total skipped: 8
Total errors: 0
```

#### Integration with Pipeline

This script is automatically run as part of `run_luma_pipeline.sh` to ensure timely RSVP approvals.

In automated mode (cron):
- Runs without `--verbose` flag for concise logging
- Executes approvals automatically (no dry-run)
- Logs are captured in cron output

#### Requirements

**Environment variables** (in `.env`):
- `LUMA_API_KEY`: Luma API authentication key
- `LUMA_CALENDAR_ID`: Luma calendar ID (format: `cal-xxx`)
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`: Railway database credentials

**Python dependencies** (in `requirements.txt`):
- `psycopg2-binary`: PostgreSQL database adapter
- `requests`: HTTP library for Luma API calls
- `python-dotenv`: Environment variable management
- Standard library: `argparse`, `logging`, `datetime`, `zoneinfo`

#### How It Works

1. **Fetch events**: Retrieves all events from Luma calendar API
2. **Filter upcoming**: Selects events starting in next 2 weeks
3. **Get pending RSVPs**: For each event, fetches all RSVPs with `approval_status: pending_approval`
4. **Match to database**: Attempts to match each RSVP to a person record (email → school email → name)
5. **Apply rules**: Checks if person meets auto-approval criteria
6. **Approve**: Calls Luma API `/event/update-guest-status` to approve qualifying RSVPs
7. **Report**: Logs summary statistics

#### Error Handling

- Continues processing even if individual approvals fail
- Logs all errors with details for debugging
- Database connection is safely closed even if errors occur
- Pagination is handled automatically for events with many RSVPs

#### Customization

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

#### Troubleshooting

**No RSVPs being approved:**
- Run with `--verbose` to see decision reasons for each RSVP
- Check that persons exist in database with correct emails
- Verify `event_attendance_count` is updated (run `import_luma_attendance.py`)

**API authentication errors:**
- Verify `LUMA_API_KEY` is correct in `.env`
- Check API key hasn't expired or been rotated

**Database connection errors:**
- Verify Railway credentials in `.env`
- Check database is accessible (network/firewall)

**Timezone issues:**
- Script uses event timezone from Luma API
- 24-hour calculation accounts for timezone differences

#### Future Enhancements

Possible improvements:
- Email notification of auto-approvals
- Slack integration for approval summaries
- Configurable rules via config file
- Web dashboard for approval history
- A/B testing different approval thresholds
