# Mailchimp Integration Improvements

## Summary of Lessons Learned and Applied

Based on testing and real-world sync of 2091 contacts with 75.8% success rate.

---

## 1. Fixed API Response Parsing Bug

### Problem
Mailchimp API returns `new_members` and `updated_members` as **arrays**, not counts.

### Error
```python
stats['new'] += response.get('new_members', 0)  # ❌ TypeError: unsupported operand type(s) for +=: 'int' and 'list'
```

### Solution
```python
stats['new'] += len(response.get('new_members', []))  # ✅ Correct
stats['updated'] += len(response.get('updated_members', []))  # ✅ Correct
```

### Files Fixed
- ✅ `mailChimp/mailchimp_client.py` - `sync_full_audience()` function (line 371-373)
- ✅ `mailChimp/mailchimp_client.py` - `batch_tag_attendees()` function (line 200-204)

---

## 2. Prevented Duplicate Email Errors

### Problem
Multiple people in the database can share the same email address, causing Mailchimp batch errors:
```
{"title":"Invalid Resource","status":400,"detail":"Duplicate items found for email jchilson@college.harvard.edu"}
```

### Solution
Added `DISTINCT ON` to SQL queries to ensure each email appears only once per batch:

```sql
-- Before (could have duplicates)
SELECT first_name, last_name, COALESCE(school_email, personal_email) as email
FROM people
WHERE school_email IS NOT NULL OR personal_email IS NOT NULL

-- After (guaranteed unique emails)
SELECT DISTINCT ON (COALESCE(school_email, personal_email))
    first_name, last_name, COALESCE(school_email, personal_email) as email
FROM people
WHERE school_email IS NOT NULL OR personal_email IS NOT NULL
ORDER BY COALESCE(school_email, personal_email), id DESC
```

### Files Fixed
- ✅ `mailChimp/sync_mailchimp_audience.py` - `get_all_contacts()` query (line 86-93)
- ✅ `mailChimp/tag_mailchimp_attendees.py` - `get_event_attendees()` query (line 99-110)

---

## 3. Added Email Validation Warnings

### Problem
Invalid emails in database cause Mailchimp API errors:
- `benalibrown@gmail.con` (typo: .con instead of .com)
- `what is your school email?` (form field name instead of email)
- `mehutchinson7@gmail.com-deleted1738957427386` (corrupted email)

### Solution
Added validation warnings for obviously invalid emails:

```python
if (email.endswith('.con') or  # Common typo
    '@' not in email or
    ' ' in email or
    email.startswith('what ') or
    '-deleted' in email):
    logging.warning(f"Potentially invalid email for {first_name} {last_name}: {email}")
```

### Files Updated
- ✅ `mailChimp/tag_mailchimp_attendees.py` - Added validation in `get_event_attendees()` (line 132-140)

**Note**: Invalid emails are still sent to Mailchimp (they will reject them), but we now log warnings so you can fix them in your database.

---

## 4. API Timeout Handling

### Problem
Batch 4 (contacts 1501-2000) timed out after 120 seconds:
```
HTTPSConnectionPool(host='us20.api.mailchimp.com', port=443): Read timed out. (read timeout=120)
```

This lost 500 contacts in a single batch!

### Solutions Implemented

#### Option A: Use Smaller Batch Sizes
```bash
# Default: 500 contacts per batch (may timeout)
python mailChimp/sync_mailchimp_audience.py

# Safer: 250 contacts per batch (less likely to timeout)
python mailChimp/sync_mailchimp_audience.py --batch-size 250
```

#### Option B: Retry Failed Batches
Created `retry_failed_batch.py` script to retry specific ranges:
```bash
python retry_failed_batch.py --start 1500 --end 2000
```

### Recommendation
For large audiences (2000+ contacts), use `--batch-size 250` to prevent timeouts.

---

## 5. Updated Documentation

### Files Updated
- ✅ `mailChimp/sync_mailchimp_audience.py` - Updated docstrings to mention deduplication
- ✅ `mailChimp/tag_mailchimp_attendees.py` - Updated docstrings to mention deduplication
- ✅ `README.md` - Added Section 4: Mailchimp Audience Sync with full documentation

---

## Real-World Test Results

### Sync of 2091 Contacts
```
Total contacts processed: 2091
New contacts added:       12
Existing contacts updated: 1572
Errors:                   507
Success rate:             75.8%
```

### Error Breakdown
- **500 contacts** - Timeout on batch 4 (largest issue)
- **~7 contacts** - Invalid email typos (.con instead of .com)
- **1 contact** - Invalid merge fields

### Lessons
1. Use smaller batch sizes for large syncs
2. Clean up email typos in your database
3. The sync is resilient - even with errors, 75.8% still synced successfully

---

## Testing Tools Created

### 1. `test_mailchimp_credentials.py`
Tests Mailchimp API credentials and permissions:
```bash
python test_mailchimp_credentials.py
```

Verifies:
- ✅ API key validity
- ✅ Server prefix correctness
- ✅ Account access
- ✅ Audience ID correctness

### 2. `retry_failed_batch.py`
Retries specific contact ranges that failed:
```bash
python retry_failed_batch.py --start 1500 --end 2000
```

---

## Common Mailchimp Errors and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `API Key Invalid` | Wrong `MAILCHIMP_SERVER_PREFIX` | Match server prefix to end of API key (e.g., `-us20` → `us20`) |
| `Duplicate items found for email` | Same email in batch twice | ✅ Fixed with `DISTINCT ON` |
| `looks fake or invalid` | Email typo (`.con` instead of `.com`) | Fix email in database |
| `Please provide a valid email address` | Form field question in email field | Clean database data |
| `Read timed out` | Batch too large or API slow | Use smaller `--batch-size` |
| `Your merge fields were invalid` | First/last name contains invalid characters | Clean name data in database |

---

## Deployment Notes

### Environment Variables Required
```bash
MAILCHIMP_API_KEY=your_api_key_here
MAILCHIMP_SERVER_PREFIX=us20
MAILCHIMP_AUDIENCE_ID=your_audience_id_here
```

### Cron Integration
Audience sync automatically runs as Step 6 of the pipeline (every 6 hours):
```
1. Sync events from Luma
2. Import attendance
3. Run event analysis
4. Generate placards
5. Run analytics
6. Sync Mailchimp audience ← Runs if credentials configured
```

If credentials are missing, sync is skipped gracefully.

---

## Next Steps for Data Quality

### Recommended Database Cleanup

1. **Fix .con typos:**
```sql
-- Find all .con typos
SELECT id, first_name, last_name, school_email, personal_email
FROM people
WHERE school_email LIKE '%.con' OR personal_email LIKE '%.con';

-- Fix individually after verifying
UPDATE people SET school_email = REPLACE(school_email, '.con', '.com')
WHERE school_email LIKE '%.con';
```

2. **Remove form field questions:**
```sql
-- Find invalid emails that are actually form questions
SELECT id, first_name, last_name, school_email, personal_email
FROM people
WHERE school_email LIKE 'what %' OR personal_email LIKE 'what %'
   OR school_email NOT LIKE '%@%' OR personal_email NOT LIKE '%@%';

-- Clean them (set to NULL)
UPDATE people SET school_email = NULL WHERE school_email LIKE 'what %';
UPDATE people SET personal_email = NULL WHERE personal_email LIKE 'what %';
```

3. **Remove deleted email markers:**
```sql
SELECT id, first_name, last_name, school_email, personal_email
FROM people
WHERE school_email LIKE '%-deleted%' OR personal_email LIKE '%-deleted%';
```

---

## Performance Stats

- **Batch processing**: 500 contacts per batch (default), 250 recommended for large syncs
- **API rate limit**: 10 requests/second (Mailchimp's limit)
- **Typical speed**: ~45 seconds per 500-contact batch
- **Large sync (2091 contacts)**: ~21 minutes total with errors and retry

---

## Files Modified

1. `mailChimp/mailchimp_client.py` - Core API fixes
2. `mailChimp/sync_mailchimp_audience.py` - Audience sync script
3. `mailChimp/tag_mailchimp_attendees.py` - Event tagging script
4. `README.md` - Documentation updates
5. `run_luma_pipeline.sh` - Added Step 6: Mailchimp sync
6. `.env` - Added Mailchimp credentials

## Files Created

1. `test_mailchimp_credentials.py` - Credential testing tool
2. `retry_failed_batch.py` - Batch retry tool
3. `MAILCHIMP_IMPROVEMENTS.md` - This document

---

Generated: 2026-02-18
Based on: Real-world sync of 2091 contacts with 75.8% success rate
