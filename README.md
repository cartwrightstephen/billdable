# Billdable

Rule-based get-paid tool for small contractors (2–12 people, roughly $750k–$2.5M). It works beside QuickBooks; a direct QuickBooks connection is planned, not built. It does not replace Jobber, Housecall Pro, or the books. There is no AI in the product yet.

## Run locally

```bash
python3 server.py
```

Then open http://127.0.0.1:8765

The public landing page is the front door. Create a company there, or sign in.

- Demo: `owner@hartline.example` / `demo` (Hartline Remodeling sample data). Anyone can sign in to the demo and reset it.
- Create account: a blank company space. Passwords need at least 10 characters. Add jobs from the Jobs tab; they persist with that company.

## Data

All data lives in one directory, set by the `BILLDABLE_DATA_DIR` environment variable. The default is `data/` beside `server.py`.

- `companies/<company_id>.json`: one file per company
- `companies/snapshots/`: a copy of a company file taken before each confirmed reset
- `auth.json`: users, password hashes, sessions

Writes go to a temp file and are renamed into place, so a crash never leaves half a file. There are no automatic backups. `GET /api/export` downloads the signed-in company's file.

On Render, the default filesystem does not survive a deploy or restart. Attach a persistent disk and set `BILLDABLE_DATA_DIR` to its mount path, or every deploy starts with no accounts. When a signed-in user's company file is missing, the server logs a `WARNING: company file missing` line.

Set `COOKIE_SECURE=1` in production to force the `Secure` flag on the session cookie. Without it, the flag is set when the request arrives with `X-Forwarded-Proto: https`.

Also in the data directory: `uploads/<company_id>/` (imported files), `inbound_tokens.json` (the secret parts of each company's inbound email, SMS and calendar addresses), `helper_answers.json` (helper answers added in the app) and `helper_log.json` (questions asked of the helper).

### Settings

| Variable | What it does |
| --- | --- |
| `BILLDABLE_DATA_DIR` | Where all data lives (see above). |
| `COOKIE_SECURE` | `1` forces the Secure cookie flag. |
| `HELPER_ADMIN_USER_IDS` | Comma-separated account ids of the people who may review helper questions and edit answers (the founders). Nobody can, if unset. Ids, not emails, because sign-up doesn't verify email. Each person's id is at the bottom of the Help page. |
| `TWILIO_AUTH_TOKEN` | When set, inbound texts must carry a valid `X-Twilio-Signature`. Set it before giving out the SMS address. |
| `TWILIO_NUMBER` | The texting number shown to users in the Inbox. Display only. |
| `INBOUND_EMAIL_ADDRESS_TEMPLATE` | The forwarding address shown in the Inbox, with `{token}` where the company's secret goes, e.g. `jobs+{token}@in.example.com`. Display only; the email service does the routing. |

## Importing

Everything comes into the Inbox, gets a suggested job (job code, address, job name, customer name, or the payer's AP email as sender), a kind (change order, waiver, pay app backup, ticket, photo, contract, daily record, note), and any dollar amounts and dates found. A person files it to the job record with one click; Undo takes it back off unless a packet uses it. No AI: the rules are in `importer.py`.

- Files: drop anywhere, paste a screenshot, or choose files. On a job page they go straight onto that job. PDF, images (JPG, PNG, GIF, WEBP, HEIC), Word, Excel, CSV, TXT and `.eml`, up to 15 MB. HTML, SVG and scripts are refused. Files are served back only to the same company, with `Content-Security-Policy: sandbox`, except PDFs, which are served without it so the browser's PDF viewer opens them.
- Saved emails (`.eml`): sender, subject, date, body and attachments are read with Python's email parser.
- Pasted text: an email (with or without headers), a text thread, or notes. Notes split at every line that starts with a date (`10/12`, `Oct 12, 2026`, `2026-10-12`) and file as daily records with that date.
- Inbound email: `POST /api/inbound/email/<token>` accepts raw MIME, a multipart form post, or JSON. The field names handled are the common ones for SendGrid Inbound Parse, Mailgun routes and Postmark inbound, written from their documentation; none has been tested against the live service.
- Inbound SMS: `POST /api/inbound/sms/<token>` accepts Twilio's form post and answers with empty TwiML. Photos sent by text are kept as links, not downloaded (Twilio media can require Twilio credentials).

## Calendar feed

`GET /api/calendar/<token>.ics` is an iCalendar feed of follow-ups due, expected approvals, sends, payments, waivers and closeouts. Google Calendar, Apple Calendar and Outlook can subscribe to it; the URL carries a secret instead of a login. Calendar apps refresh subscribed feeds on their own schedule.

## Helper

The ? badge opens the helper. It answers from a database of prepared answers, not an AI model:

- `helper/answers.json` ships with the code. Edit it in Git; `tests/test_helper.py` checks that common questions still land on the right answer.
- Accounts listed in `HELPER_ADMIN_USER_IDS` get a Helper answers page: questions with no good answer (unmatched, or marked not helpful), grouped and counted, with a form to answer them. Saved answers go to `helper_answers.json` in the data directory and work immediately for every company; the same id replaces a shipped answer.
- Matching is word overlap weighted by rarity, with synonyms (pay app = invoice = requisition, retainage = retention, ...), in `helper.py`.
- Some answers read the company's own jobs: what to do today, who owes you, follow-ups due, what is blocking a job, a drafted follow-up note, unbilled work, retainage, waivers, the Inbox, unsigned change orders, jobs ready to close. Their buttons call the normal endpoints, so every gate and confirmation still applies.
- Every question is logged with what it matched. The panel tells users that questions are saved.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests write to a temporary directory, never to `data/`.

## Project layout

- `domain.py`: money rules, packet gates, status labels, audit, playbook, email log
- `auth.py`: users, password hashes, sessions, login throttling
- `storage.py`: data directory and atomic JSON writes
- `importer.py`: files, email parsing, notes splitting, job matching, the Inbox
- `calendar_feed.py`: the .ics feed
- `helper.py` and `helper/answers.json`: the helper and its answers
- `seed.py`: Hartline Remodeling sample data
- `server.py`: stdlib HTTP server, cookie sessions, per-company JSON
- `static/`: `index.html`, `app.js`, `styles.css` (the only files served)
- `tests/`: domain, auth and HTTP tests

## Money rules

- Pay apps are net of retainage. With a retainage percent on the job, each pay app withholds that percent and shows it as a line. Held retainage is not counted as unbilled work.
- A job with retainage gets a closeout packet when it is marked done. The closeout packet gates the retainage release request, not the pay apps.
- Pay-app packet items come from the payer's playbook packet text.
- "Mark present" needs an attached job record or a written reason.
- A change order approved after the job is marked done becomes billable on the next pay app.
- A payment goes to a pay app the user picks, must be above zero and at most the balance owed, and a reference can be used once per job.
- One waiver per paid pay app. The waiver document comes from the contractor; Billdable does not generate it.
- Follow-ups are refused before the payer's follow-up window, and each one writes an Outbox row.
- A job closes only when work is done, nothing is drafted, unbilled, outstanding or pending, retainage is released, and waivers are sent. Closed jobs are read-only except for notes.
- A new job for an existing payer keeps the payer's approver and AP email. Change them in the Payer Playbook.
- Email delivery is not built. Every "send" is recorded in the Outbox as `logged (stub — not delivered)`.

## API

Auth (cookie session):

- `POST /api/auth/login`: `{email, password}`. After 5 wrong passwords for one email in 15 minutes, returns 429.
- `POST /api/auth/signup`: `{email, password, name, company_name}`
- `POST /api/auth/logout`
- `GET /api/auth/me`

Company data (requires a session; scoped to the signed-in company). The audit trail always names the signed-in user; an `actor` field in a request body is ignored.

- `GET /api/state`: full snapshot
- `GET /api/export`: download the company's data file
- `POST /api/reset`: demo company returns to the Hartline seed. Any other company needs `{confirm: "<exact company name>"}` and gets a snapshot first.
- `POST /api/jobs`: create a job. Required: `name`, `address`, `customer`, `approver`, `ap_email`, `contract_cents`. Optional: `phone`, `role` (`Owner` or `GC`), `retainage_pct` (0–20), `packet_notes`, `contract_number`.
- `POST /api/jobs/{id}/done`: `{notes}`
- `POST /api/jobs/{id}/progress`: `{completed_cents, notes}`
- `POST /api/jobs/{id}/draft`: returns 409 when there is nothing to draft or a draft is already open
- `POST /api/jobs/{id}/send`
- `POST /api/jobs/{id}/follow-up`
- `POST /api/jobs/{id}/match-payment`: `{amount_cents, reference, invoice_id}`. `invoice_id` is required when more than one pay app is open.
- `POST /api/jobs/{id}/waiver`: `{invoice_id}` optional
- `POST /api/jobs/{id}/retainage`: request retainage release
- `POST /api/jobs/{id}/retainage-paid`: `{amount_cents, reference}`
- `POST /api/jobs/{id}/resolve/{item_id}`: `{reason}`, required unless a record is attached
- `POST /api/jobs/{id}/attach/{item_id}`: `{name}`
- `POST /api/jobs/{id}/attach-record/{item_id}`: `{record_id}`
- `POST /api/jobs/{id}/add-co`: `{title, amount_cents}`
- `POST /api/jobs/{id}/approve-co/{co_id}`
- `POST /api/jobs/{id}/include-co/{co_id}`: put a pending CO on the next pay app; send stays blocked until it is signed
- `POST /api/jobs/{id}/add-evidence`: `{label, kind}`
- `POST /api/jobs/{id}/add-record`: `{label, kind, note}`
- `POST /api/jobs/{id}/daily-record`: `{notes}`
- `POST /api/jobs/{id}/learn-rejection`: `{reason}`
- `POST /api/jobs/{id}/close`
- `POST /api/jobs/{id}/import-qbo`: demo company only; a hard-coded sample payment
- `POST /api/payers/{id}/update`: playbook fields. Follow-up and approval days must be 1–365.
- `POST /api/jobs/{id}/pencil`: send the open draft as a pencil copy; `POST /api/jobs/{id}/pencil-response`: `{agreed, note}`
- `POST /api/jobs/{id}/void-draft`; `POST /api/jobs/{id}/withdraw-co/{co_id}`
- `POST /api/import/file?job=<id>&file=1`: raw file body, `X-Filename` header (URL-encoded). Without `file=1` it goes to the Inbox.
- `POST /api/import/text?file=1`: `{text, source: email|sms|notes|paste, job_id}`
- `POST /api/inbox/{item_id}/file` `{job_id, kind}`, `/dismiss`, `/undo`
- `GET /api/import/settings`: the company's inbound and calendar addresses; `POST /api/import/rotate` `{kind}` makes a new one
- `GET /api/files/{file_id}`
- `POST /api/helper/ask` `{q, view, job_id}`; `POST /api/helper/feedback` `{log_id, helpful, note}`
- Team only: `GET /api/helper/review`, `POST /api/helper/answers`, `POST /api/helper/resolve`

Gates return 409 with a list of blockers. Bad input returns 400 with a sentence. Unauthenticated calls return 401. Server errors return a JSON 500 and are logged.

## Optional: Docker

```bash
docker build -t billdable .
docker run -p 8765:8765 billdable
```

Local `python3 server.py` is the primary path. No external services or API keys required.
