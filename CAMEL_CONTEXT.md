# CAMEL — Context Base (source of truth for Claude sessions)

_Last updated: 2026-09-02. Maintained by Claude across chat / Cowork / Claude Code. Read this first in any Camel session._

## 1. What The Camel is
- Community org connecting top students at elite universities (Harvard + MIT focus) with prominent Israeli and Jewish business leaders, founders, and investors.
- Tagline: "Loud minds. Low lighting." (Luma bio: "Loud minds. Low lighting. Join the ride.")
- Formats: private dinners, fireside chats, parties/happy hours, CamelHack (annual hackathon: TechTrek NYC → hackathon in Cambridge → Demo Day), Tel Aviv founder gatherings.
- Scale (per website): 3,000+ Harvard & MIT students reached, 30+ events over two years, Boston / NYC / Tel Aviv. Luma description copy says "2,500+ students across Harvard and MIT."
- Website: https://www.thecamel.club/ — mailing-list signup is a Google Form; board contact listed: doronbenhaim@college.harvard.edu. Related: camelhack.com, LinkedIn @camelusa, CamelX (Israeli intelligence-alumni community).
- Team seen as Luma hosts: Colin Kamhi, Gabriel Abrams, Doron Ben Haim, Mikhal Shvartsman, Logan Hennes, Logan Goodman, Jacob Gross, Daniella Biblin, Zachary Sardi-Santos, Adrian Maydanich, AJ Sachwitz, Ben Shaer, Charlie Covit, Ronen Betser, Elad Cohen, Bill Schnoor, Adam Elitzur, Ohad Nargassi.

## 2. Systems map
| System | What it is | IDs / links | Access from Claude |
|---|---|---|---|
| **Luma** (Luma Plus) | Event pages + RSVPs | Calendar `cal-Gp6LlufvXcKd7H0`, public page https://luma.com/thecamel, manage https://luma.com/calendar/manage/cal-Gp6LlufvXcKd7H0 | Browser (Colin is logged in; internal API `api.luma.com` works from a luma.com tab). Public API via Supabase bridge once `luma_api_key` is in Vault (create keys at calendar Settings → Developer; format `secret-…`; Gabriel's existing key is the Railway one). Create Event endpoint exists: `POST /v1/events/create` (name, start_at, timezone). |
| **Mailchimp** | All email pubs (invites, reminders, follow-ups). Luma Newsletters are unused. | Audience id lives in Railway env `MAILCHIMP_AUDIENCE_ID`; server prefix e.g. `us20` | Claude connector = AI campaign planner only (cannot read past campaigns, refuses single-email drafts). Real work needs Marketing API key → Supabase bridge `mailchimp_call()`. |
| **GitHub** `gabrielabrams5/camelAnalysisCron` | Railway-hosted Python cron (every 6h): Luma event sync, attendance import, analytics PNG/CSV, placard PDFs, Mailchimp audience sync + tagging (`{event}_attended`, `{event}_first_attended`). Read-only toward Luma; no campaign sending. | Attached to this Project as a GitHub sync source (read-only snapshot; click Sync in project settings to refresh). | Reference only. Don't duplicate; don't modify without Gabriel. |
| **Railway Postgres** (the repo's DB) | `events`, `people` (~school/email/class year), `attendance`, `event_feedback`, `invitetokens`, `opportunities`, `promos`, `partner_codes`, `subscribers` | Env: PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD | Not accessible from Claude yet. Could be linked via `postgres_fdw` from camel-hub later. |
| **Supabase `camel-hub`** (NEW, built 2026-09-02) | Claude's searchable archive + API bridge | project id `wzdhumppbntlpvxfmkjr`, region us-east-1, $0 tier | Supabase MCP connector (`execute_sql`). |

## 3. Supabase `camel-hub` — schema
- `events` — one row per Luma event. Key cols: `luma_event_id`, `name`, `start_at`, `end_at`, `luma_slug` → generated `luma_url`, `status` (upcoming/past/canceled/draft), `visibility`, `category` (fireside chat / party / happy hour / camelhack), `city`, `address`, `speaker_name`, `speaker_org`, `hosts text[]`, `cover_url`, `description` (plain text), `registration_questions jsonb`, `guest_count`, `raw jsonb`, `search tsvector`.
- `event_sections` — every paragraph/section of each Luma description (`event_id`, `position`, `heading`, `body`, `search`).
- `email_campaigns` — one row per Mailchimp campaign: `mailchimp_campaign_id`, `event_id` (link to event), `campaign_type` (invite/reminder/followup/newsletter/announcement/other), `subject_line`, `preview_text`, `status`, `send_time`, `archive_url`, `html_content`, `plain_text`, `emails_sent`, `open_rate`, `click_rate`, `raw`, `search`.
- `campaign_event_rules` — editable ILIKE patterns that link an email to its event (e.g. `%blackrock%` → The Camel x BlackRock). Subject match beats title match; lower `priority` wins.
- `sync_log` — one row per auto-sync run (job, started_at, result, error). Check this first if data looks stale.
- `app_settings` — non-secret config (luma_calendar_id, luma_calendar_slug, website, github_repo).
- Events also carry `source` (`luma` or `mailchimp-inferred`) and `notes`; `email_campaigns` carry `is_resend`, `audience_segment` (all / first_timers / returners), `last_synced_at`.
- Secrets: Supabase **Vault** names `luma_api_key`, `mailchimp_api_key`, `mailchimp_dc` (= `us20`). Read via `camel_secret(name)` (service-only).
- RLS is enabled on all tables; nothing is exposed through the public anon API. Claude uses the MCP service connection.

### Functions (call with `select ...`)
- `search_all('query text', 20)` — unified full-text search across events, sections, and emails. Returns kind, id, title, date, snippet, rank, url.
- `sync_luma_events()` — pulls every event from Luma public API into `events` (needs `luma_api_key`).
- `sync_mailchimp_campaigns(p_full boolean default false)` — incremental: 1 list call, then content + report only for new/changed/recent campaigns. `select sync_mailchimp_campaigns(true)` forces a full refresh (~60–90s).
- `link_campaigns_to_events()` — classifies `campaign_type` (invite / reminder / followup) from subject+title and links each campaign to an event via `campaign_event_rules`, falling back to "sent within 14 days before the event". Runs automatically at the end of every Mailchimp sync.
- `run_sync_all()` — what the cron job calls: Luma sync + Mailchimp sync, logged to `sync_log`.
- Low-level: `luma_get(path, query)`, `luma_post(path, body)`, `mailchimp_call(method, path, body)`, `http_json(method, url, headers, body)`.

### Auto-sync (live since 2026-09-02)
`pg_cron` job **`camel_sync_all`** runs `run_sync_all()` **every 15 minutes** inside Supabase. So any new Luma event, edited description, new Mailchimp send, or draft shows up in the database within 15 minutes with no one doing anything. Nothing runs outside Supabase. To check: `select * from sync_log order by id desc limit 5;` To change cadence: `select cron.schedule('camel_sync_all','*/15 * * * *',$$select public.run_sync_all();$$);`

### Adding a secret (Colin does this once per key)
Supabase dashboard → project camel-hub → Project Settings → Vault → Add new secret. Name exactly as above. Or via SQL: `select vault.create_secret('<value>', 'luma_api_key');`

### Handy queries
```sql
select * from search_all('bridgewater');                       -- anything mentioning Bridgewater
select name, start_at, city, guest_count from events order by start_at desc;   -- event list
select subject_line, send_time, open_rate from email_campaigns order by send_time desc;  -- email pubs
select e.name, c.subject_line, c.campaign_type from email_campaigns c join events e on e.id=c.event_id;  -- emails per event
```

## 4. Event inventory (Luma calendar, as of 2026-09-02) — 15 events, ~1,900 registrations
| Date | Event | City / Venue | Guests | Speaker |
|---|---|---|---|---|
| Sep 9 2026 (upcoming) | Camel Fall Welcome Party | Boston · Studio B | — | party |
| Aug 6 | The Camel x BlackRock | NYC · Spygold | 127 | Gary Shedlin (BlackRock) |
| Jul 23 | The Camel x Christie's | NYC · Pebble Bar | 60 | Sara Friedlander (Christie's) |
| Jul 9 | The Camel x Rakesh Loonkar | NYC · Goodwin Procter | 77 | Rakesh Loonkar (Picture Capital / Transmit Security) |
| Jun 25 | The Camel x Josh Wolfe | NYC · Barlume | 98 | Josh Wolfe (Lux Capital) |
| Jun 11 | The Camel NYC Summer Kickoff | NYC · 230 Fifth | 184 | happy hour |
| Apr 30 | Instagram x CAMEL | Cambridge · Lou's | 325 | Adam Mosseri (Instagram) |
| Apr 9 | General Catalyst x Camel | Cambridge · Harvest | 163 | David Fialkow (General Catalyst) |
| Mar 11 | Elliott Management x CAMEL | Cambridge · Bar Enza | 222 | Dan Senor (Elliott) |
| Mar 8 | CamelHack 2026 Demo Day (canceled) | Cambridge | — | Udi Mokady keynote (CyberArk) |
| Mar 7 | CamelHack Kick Off | Cambridge | — | — |
| Mar 5 | CamelHack TechTrek | NYC (Battery, Wix, Boxgroup, Lux, Cognition, Crosby AI, a16z) | — | — |
| Mar 4 | LionTree x Take-Two x CAMEL | Cambridge · Charles Hotel | 108 | Strauss Zelnick & Aryeh Bourkoff |
| Feb 25 | Bridgewater x CAMEL | Cambridge · Charles Hotel | 337 | Nir Bar Dea (Bridgewater CEO) |
| Feb 17 | Jon Hirschtick x CAMEL | Cambridge · Sulmona | 198 | Jon Hirschtick (SolidWorks/Onshape/PTC) |

Plus 3 events inferred from Mailchimp history that are **not** on this Luma calendar (dates approximate, flagged `source='mailchimp-inferred'`): Jefferies x CAMEL (~Nov 2025), Camel Oasis party (~Feb 7 2026), Rakefet x CAMEL (Feb 11 2026).

Gap: 2024–2025 events (the other ~15 of the "30+") are not on this calendar — likely on an older calendar or personal host accounts. Ask Gabriel/Doron, or pull from the Railway `events` table.

## 4b. Email inventory (Mailchimp, synced 2026-09-02) — 56 campaigns: 45 sent (Nov 2025 → Aug 2026, ~76K emails, 54.5% avg open rate) + 11 drafts
Every campaign is in `email_campaigns` with full HTML + plain text, open/click rates, and a link to its event. The standard sequence per event, in Colin's vocabulary:
- **pub / pub1** — main invite to the full list (~3,000 sends). Title pattern: `<Event> pub`, `GCpub`, `3/4 pub1 email`.
- **pub2 / "this Wednesday!" / "TONIGHT"** — reminder 1–3 days before. Often a **Resend:** to non-openers.
- **Feedback / Photos email** — follow-up 1–4 days after, split **first timers** ("Welcome to the Camel!") vs **returners** ("Photos from … + feedback"). ~20–160 sends.
- Non-event sends: CamelHack application pubs (live / last day / extended), Camel Oasis photos.
- Merge tags used: `*|FNAME|*`. Opening: "Hey *|FNAME|*," Signed by Colin in follow-ups.

## 5. House style for event pages (from the 15 descriptions)
- Title pattern: `<Partner/Speaker> x CAMEL` (Cambridge era) or `The Camel x <Speaker>` (NYC summer). Parties: `Camel <Season> <Thing>`.
- Opening line: "Join the Camel for an exclusive fireside chat with <Name>, <title> of <Org>." or "The Camel is excited to announce…"
- Body: 1–2 paragraphs of speaker bio with concrete numbers ($ AUM, exits, roles), then 1 line on format (intimate / buffet / open bar / drinks on us), close with "RSVP now — spots are limited!"
- Registration questions (standard): School email (.edu), Gender, Grad year, Interests, College (+ summer: "Where are you working this summer?"; + spring: "What was the primary source that influenced your decision…", "What are you most looking forward to…").
- Visibility: private (link-only) for almost everything; approval required for Demo Day.

## 6. Automation plan (agreed with Colin, 2026-09-02)
- Runs in Claude + Supabase. No n8n. No new servers.
- Event + follow-up creation: on demand in a Claude session → Claude pulls the closest past event/email from camel-hub as the template → drafts Luma page + Mailchimp email → **Colin approves** → Claude publishes via `luma_post('/events/create', …)` and Mailchimp API → rows saved back to camel-hub.
- No Telegram (dropped 2026-09-02). Everything runs inside the Claude Project + Supabase (auto-sync via pg_cron every 15 min).
- Existing Railway cron keeps doing attendance/analytics/audience-tag sync untouched.

## 7. Open items
1. ~~Luma API key~~ done. ~~Mailchimp API key~~ done. Both synced; auto-sync every 15 min.
2. Confirm the 3 inferred events (Jefferies, Oasis, Rakefet) — real dates/venues — or add them to Luma.
3. Locate 2024–2025 events (older calendar? Railway DB?) and backfill.
4. Optional: `postgres_fdw` link from camel-hub to the Railway DB so `people`/`attendance` are queryable from Claude.
