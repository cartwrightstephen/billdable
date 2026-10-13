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
- `auth.json`: users, password hashes, and the SHA-256 of each session token (a copy of the file signs nobody in)

Writes go to a temp file and are renamed into place, so a crash never leaves half a file. Backups are described under "Backups, health and logs". `GET /api/export` downloads the signed-in company's file.

On Render, the default filesystem does not survive a deploy or restart. `render.yaml` builds the Dockerfile (which installs `age` for off-site backups), attaches a 1 GB persistent disk at `/var/data` (this needs a paid instance) and sets `BILLDABLE_DATA_DIR` to it. It also defines `billdable-staging`, a free service that deploys `dev` with throwaway data and no email; without a disk every deploy starts with no accounts. After the first deploy with the disk, deploy a second time, check that `/healthz` shows the same `last_backup` and that an account still signs in, then set `BILLDABLE_DATA_PERSISTENT=1` to remove the landing page's "data may be erased" notice. When a signed-in user's company file is missing, the server logs a `WARNING: company file missing` line.

Set `COOKIE_SECURE=1` in production to force the `Secure` flag on the session cookie. Without it, the flag is set when the request arrives with `X-Forwarded-Proto: https`.

Also in the data directory: `uploads/<company_id>/` (imported files), `inbound_tokens.json` (the secret parts of each company's inbound email, SMS and calendar addresses), `helper_answers.json` (helper answers added in the app) and `helper_log.json` (questions asked of the helper).

### Settings

| Variable | What it does |
| --- | --- |
| `BILLDABLE_DATA_DIR` | Where all data lives (see above). |
| `BILLDABLE_ENV` | `production` turns the demo account off (not created, can't sign in, old demo sessions end) and refuses to start unless `BILLDABLE_DATA_DIR` is an existing directory, `COOKIE_SECURE=1` and `BILLDABLE_URL` is an `https://` address. Unset everywhere else. |
| `BILLDABLE_DATA_PERSISTENT` | `1` once a deploy has been seen to keep the data. Until then the landing page says "Demo: data may be erased". |
| `COOKIE_SECURE` | `1` forces the Secure cookie flag. |
| `HELPER_ADMIN_USER_IDS` | Comma-separated account ids of the people who may review helper questions and edit answers (the founders). Nobody can, if unset. Ids, not emails, because sign-up doesn't verify email. Each person's id is at the bottom of the Help page. |
| `TWILIO_AUTH_TOKEN` | When set, inbound texts must carry a valid `X-Twilio-Signature`. Set it before giving out the SMS address. |
| `TWILIO_NUMBER` | The texting number shown to users in the Inbox. Display only. |
| `INBOUND_EMAIL_ADDRESS_TEMPLATE` | The forwarding address shown in the Inbox, with `{token}` where the company's secret goes, e.g. `jobs+{token}@in.example.com`. Display only; the email service does the routing. |
| `BILLDABLE_URL` | The public address, e.g. `https://billdable.onrender.com`. Password-reset, verification and invite emails use it for their links; they are not sent without it (a link built from the request's Host header could point a reset token at someone else's site). |
| `LEGAL_ENTITY`, `LEGAL_ADDRESS`, `LEGAL_EMAIL`, `LEGAL_STATE`, `DATA_REGION` | Company details shown in the Terms of Service and Privacy Policy (`/terms`, `/privacy`). Until all are set and `LEGAL_REVIEWED=1`, both pages say they are a draft. |
| `LEGAL_REVIEWED` | `1` once a lawyer has reviewed the text in `legal/` and the details above are filled in. |
| `BACKUP_HOURS` | Hours between automatic backups, default 24; `0` turns them off. |
| `BACKUP_KEEP` | How many backups to keep, default 14. |
| `BACKUP_UPLOADS` | `0` leaves uploaded files out of backups (they can be large). Default: included. |
| `BACKUP_S3_ENDPOINT`, `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`, `BACKUP_S3_KEY_ID`, `BACKUP_S3_SECRET` | S3-compatible storage for off-site copies, e.g. Backblaze B2 (`https://s3.us-west-004.backblazeb2.com`, region `us-west-004`). Use a write-only application key and a bucket with Object Lock. All five set turns the upload on. |
| `BACKUP_S3_PREFIX` | Folder in the bucket, default `billdable/`. |
| `BACKUP_AGE_RECIPIENT` | The age public key (`age1…`) backups are encrypted to before upload. Keep the private key off the server, in both founders' password managers. Without it nothing is uploaded. |
| `BACKUP_AGE_BINARY` | Path to the `age` program, default `age` on the PATH. The Dockerfile installs it, and `render.yaml` deploys with the Dockerfile. |
| `ALERT_WEBHOOK_URL` | An `https://` incoming-webhook address (Slack, Discord, Better Stack, ...). Each crash and failed backup posts one line with the request id; rate limited; no customer data. |
| `SMTP_HOST` | Mail server for outgoing email. Unset: approved emails stay in the Outbox as "not sent: email delivery is not set up". |
| `SMTP_PORT` | Default 587 (465 with `SMTP_SECURITY=ssl`). |
| `SMTP_USER`, `SMTP_PASSWORD` | Login for the mail provider, if it needs one. |
| `SMTP_SECURITY` | `starttls` (default), `ssl`, or `none` (local testing only). |
| `MAIL_FROM` | Sender address on a domain the provider has verified (SPF and DKIM), e.g. `billing@billdable.com`. Required with `SMTP_HOST`. |
| `MAIL_EVENTS_TOKEN` | A random secret of 16+ characters. Give the mail provider `https://<site>/api/mail-events/<this token>` as its bounce (and spam-complaint) webhook; a bounced email's Outbox row turns "bounced" and its job's status line says so. Formats: `mail_events.py`. |
| `MAIL_DAILY_LIMIT` | Emails per company per 24 hours, default 50. Sign-up doesn't verify anyone, so this keeps one account from using us to send spam. Counted in `mail_sends.json`. |

## Importing

Everything comes into the Inbox, gets a suggested job (job code, address, job name, customer name, or the payer's AP email as sender), a kind (change order, waiver, pay app backup, ticket, photo, contract, daily record, note), and any dollar amounts and dates found. A person files it to the job record with one click; Undo takes it back off unless a packet uses it. No AI: the rules are in `importer.py`.

- Files: drop anywhere, paste a screenshot, or choose files. On a job page they go straight onto that job. PDF, images (JPG, PNG, GIF, WEBP, HEIC), Word, Excel, CSV, TXT and `.eml`, up to 15 MB. HTML, SVG and scripts are refused. Files are served back only to the same company, with `Content-Security-Policy: sandbox`, except PDFs, which are served without it so the browser's PDF viewer opens them.
- Saved emails (`.eml`): sender, subject, date, body and attachments are read with Python's email parser.
- Pasted text: an email (with or without headers), a text thread, or notes. Notes split at every line that starts with a date (`10/12`, `Oct 12, 2026`, `2026-10-12`) and file as daily records with that date.
- Inbound email: `POST /api/inbound/email/<token>` accepts raw MIME, a multipart form post, or JSON. The field names handled are the common ones for SendGrid Inbound Parse, Mailgun routes and Postmark inbound, written from their documentation; none has been tested against the live service.
- Inbound SMS: `POST /api/inbound/sms/<token>` accepts Twilio's form post and answers with empty TwiML. Photos sent by text are kept as links, not downloaded (Twilio media can require Twilio credentials).

## Sending email

Approved pay apps, pencil copies, follow-ups, waivers and retainage requests are emailed over SMTP (`mailer.py`). Postmark, SendGrid, Mailgun, Amazon SES and Google Workspace all accept SMTP, so pick one and set the variables above. Tested only against a local test server in `tests/test_mail.py`, not against a real provider.

- From is "<company> via Billdable" at `MAIL_FROM`; Reply-To is the person who approved the send (kept if someone else retries it), so the payer's answer goes to the contractor. Addresses in the payer's Copy field are copied on pay apps.
- A pay app goes with its full text, the schedule-of-values continuation sheet when the job has one, the packet checklist, and the files behind the records attached to its packet (up to 15 MB in total; anything left off is named in the email). A waiver email attaches the newest lien waiver on the job record not sent before, and says the waiver will follow if there is none. A retainage request attaches the closeout documents and is spaced by the payer's follow-up days, like follow-ups.
- Delivery happens after the change is saved and the state lock is released, so a slow mail server never holds up other requests. Each email shows sending, sent (with the time), failed (with the reason) or not sent in the Outbox. Failed and unsent emails have Send again (`POST /api/outbox/{id}/retry`). The approval itself stands either way.
- The person approving must have confirmed their email address first (when email is set up), because replies go to it; until then emails are held as "not sent" with Send again. A pay app or pencil copy also carries the pay app as a PDF (`payapp_pdf.py`), made from the pay app when the email goes out.
- The demo account never sends. Header fields are stripped of line breaks and control characters, addresses must be plain (`name@domain.tld`), and the mail server is given the recipients directly rather than reading them back from the headers.
- The daily limit is counted in `mail_sends.json` in the data directory, outside the company's own data, so resetting a company doesn't reset it. An email still "sending" after 30 minutes (the server stopped mid-send) can be sent again.

## Accounts and team

- A company has owners and members. Owners invite people (Team & account), change roles, remove people, rotate the inbound addresses, reset the company and delete it; members do everything else. The last owner can't step down or be removed. Removing someone signs them out at once.
- Password reset: "Forgot your password?" emails a link that works once, for an hour, and signs the account out everywhere else. At most 3 requests per email per hour; the answer is the same whether or not the account exists. Signed in, the password can be changed on Team & account.
- Email verification: sign-up sends a link (valid 3 days). An unconfirmed person can use everything but can't have emails sent under their name.
- Invites last 7 days. Without email set up, the owner gets the link to pass on themselves.
- Only a SHA-256 of each reset, verification and invite token is stored. Tokens travel after `#` in links, so they never reach server logs.
- Deleting the company (owner, password plus the typed company name) removes its users, sessions, data, uploads and inbound addresses. Backups already made still hold it until they rotate out.
- Every team change is checked against the acting person's current role at the moment it is made, and every request re-checks the session once its body has arrived (bodies must arrive within 60 seconds), so someone removed or demoted mid-request can't finish with their old rights.
- Removing someone also changes the company's inbound email, text and calendar addresses (they knew them), and cancels the invites they sent; demoting an owner cancels their invites.
- Invites need a confirmed email when email is set up, and are limited to 20 a day per company; confirmation links to 3 an hour per person. These system emails count against the company's daily email limit. Invite and reset subjects are fixed text; names typed by users appear only in the body, flattened.
- Changing a password signs out every other session and is throttled like sign-in. The demo account's password can't be changed.
- Older unexpired reset links stay valid when another is requested (so a stranger's requests can't cancel yours); using one uses them all up. The reset request does its work in the background, so the answer takes the same time for any email; sign-in also does the same work for unknown emails.
- Without `BILLDABLE_URL`, an invite returns only the path (`/#invite=…`); the owner's browser adds its own address.
- Deleting a company also records it in `deleted_companies.json`, so a request that was already running can't write it back, and removes its helper questions and mail counter.
- Not built: two-factor sign-in, single sign-on, per-job permissions.

## Terms and privacy

`legal/terms.html` and `legal/privacy.html`, served at `/terms` and `/privacy` with the company details from the settings above (HTML-escaped). Sign-up and accepting an invite need the box ticked; the server records the version (`legal.TERMS_VERSION`) and time on the account. The text is a draft written to match what the code does (data collected, sharing, retention, deletion, backups); **have a lawyer review it before relying on it**, then set `LEGAL_REVIEWED=1`. Change `TERMS_VERSION` when the text changes.

## Pay app PDF

`GET /api/jobs/{id}/payapp/{invoice_id}.pdf`, and attached to pay-app and pencil emails (never for a pay app voided after the email was approved). Older jobs' job-level held retainage counts in lines 4 and 5; unsigned change orders billed on the pay app show as line 2a and count in the contract sum; a voided pay app is marked VOID and keeps its own figures. Page 1 is an application summary (lines 1–9: contract, change orders, completed to date, retainage, previous pay apps, current payment due, balance); then a continuation sheet of every schedule line (or the pay app's lines) and the packet checklist. The layout follows the G702/G703 pattern but it is Billdable's own document and says it is not an AIA form. Drafts and pencil copies are marked. Standard library only; built-in fonts. Lien waivers are not generated: their forms are set by state law.

## Trial

`trial.py` works out where each company stands against the agreed trial: nothing to pay until the first pay app sent through Billdable is paid, or 90 days, extended to 120 if a pay app is still unpaid on day 90. The dashboard shows it, and the founders' page lists every company with its trial. Billing isn't live, so nothing is charged or locked when a trial ends. Companies that signed up before this change aren't tracked. The 45-day variant for remodelers isn't automated.

## Backups, health and logs

- `backup.py`: one backup on start-up if the newest is older than `BACKUP_HOURS`, then every `BACKUP_HOURS`, as `.tar.gz` files in `<data dir>/backups` (accounts, companies, inbound tokens, helper files, the mail counter and uploads), keeping `BACKUP_KEEP`. They are on the same disk as the data, so when the `BACKUP_S3_*` settings are filled in, each new backup is also encrypted with age and uploaded (`offsite.py`); `python3 backup.py upload [FILE]` does one by hand. To restore an off-site copy: download the `.age` file, `age -d -i key.txt FILE.age > backup.tar.gz`, then `python3 backup.py restore backup.tar.gz`. `python3 backup.py now|list|restore FILE`; restore (server stopped) first unpacks into a staging folder and checks the archive is complete (it must hold `auth.json` and `companies/`, with nothing outside the data directory); only then does it move the current data aside to `pre-restore-<time>/` and swap the backup in. Company snapshots taken before a reset are included.
- `GET /healthz`: no sign-in; `ok`, whether the data directory is writable (503 if not), whether email is set up, the time of the last backup and of the last off-site upload, and the env. Point an uptime monitor (for example Better Stack's free tier) at it: it is the only thing that notices the server being down.
- Logs: one JSON line per request on stderr (time, method, path, status, milliseconds, request id). Query strings and the secret parts of inbound and calendar addresses are removed. A crash logs its traceback with the request id, and the user sees the same id to quote. With `ALERT_WEBHOOK_URL` set, each crash also posts a one-line alert (`alerts.py`).

## Schedule of values

Optional per job, set before the first pay app (`sov.py`). Each line is a quantity at a unit rate (a lump sum is quantity 1, unit LS); overhead and profit is a percentage on top of the base lines. The schedule total must equal the contract; the editor shows the difference and offers to change the contract amount instead.

- Rows can be pasted from Excel or Google Sheets (tabs), a CSV, or a PDF (columns separated by runs of spaces). A header row is optional. Total and markup rows are skipped and reported; an "Overhead & profit 15%" row fills in the percentage.
- Progress is entered as quantity done to date per line (percent for lump sums). Each pay app bills the difference since the last one, line by line, plus overhead and profit on that period's base work, less retainage, and stores the lines as a G703-style continuation sheet.
- Quantities can't exceed the line (extra scope is a change order) or drop below what was billed, in quantity or in dollars. Numbers must be plain decimals (no exponents), and pasted text is read in bounded pieces. The schedule is fixed once a pay app exists; voiding the only pay app unlocks it.
- An approved change order adds its own lump-sum line at the CO amount, without markup. A CO billed while unsigned is not billed again when approved.
- All amounts are whole cents with half-up rounding on integers; the browser repeats the same arithmetic for the live totals.

## Calendar feed

`GET /api/calendar/<token>.ics` is an iCalendar feed of follow-ups due, expected approvals, sends, payments, waivers and closeouts. Google Calendar, Apple Calendar and Outlook can subscribe to it; the URL carries a secret instead of a login. Calendar apps refresh subscribed feeds on their own schedule. The feed and the Calendar page say these are contract and follow-up dates, not legal deadlines; Billdable computes no lien or prompt-pay dates.

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

Tests write to a temporary directory, never to `data/`. GitHub Actions (`.github/workflows/tests.yml`) runs them on every push and pull request; protect `main` so a red run blocks the merge (Settings, Branches, require the `unittest` check).

## Project layout

- `domain.py`: money rules, packet gates, status labels, audit, playbook, email log
- `auth.py`: users and roles, password hashes, sessions, login throttling, reset/verify/invite tokens
- `storage.py`: data directory and atomic JSON writes
- `importer.py`: files, email parsing, notes splitting, job matching, the Inbox
- `calendar_feed.py`: the .ics feed
- `helper.py` and `helper/answers.json`: the helper and its answers
- `sov.py`: schedule-of-values arithmetic and the paste reader
- `payapp_pdf.py`: the pay app PDF
- `trial.py`: trial status
- `backup.py`: backups and restore
- `offsite.py`: encrypted off-site copies of backups (age, S3-compatible upload)
- `alerts.py`: crash and backup-failure alerts to a webhook
- `totp.py`: two-step sign-in codes (RFC 6238)
- `qbo_csv.py`: payments from a QuickBooks CSV export, proposed against open pay apps
- `metrics.py`: the Results numbers and the monthly summary email (`python3 admin.py monthly-summaries`)
- `payer_page.py`: the payer's page for a pay app (`/p/...`) and the change-order signing page (`/s/...`)
- `mail_events.py`: bounce and spam-complaint webhooks from the mail provider
- `migrations.py`: the company-file schema version and the steps between versions, run on load
- `audit_check.py`: checks every company's audit hash chain (run nightly)
- `admin.py`: founder shell commands: list companies, find an email, delete a company on request, end sessions
- `docs/runbooks.md`: deploy, restore, rotate a secret, delete a company, incident
- `legal.py`, `legal/`: the Terms of Service and Privacy Policy
- `mailer.py`: outgoing email over SMTP
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
- Every approved send is written to the Outbox and emailed when email is set up (see Sending email).

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
- `POST /api/jobs/{id}/progress`: `{completed_cents, notes}` (jobs without a schedule of values)
- `POST /api/sov/parse`: `{text}` → `{lines, warnings, op_pct}`; reads pasted rows, saves nothing
- `POST /api/jobs/{id}/sov`: `{lines: [{id?, item, description, quantity, unit, rate}], op_pct, update_contract}`; an empty list removes the schedule
- `POST /api/jobs/{id}/sov-progress`: `{done: {line_id: quantity_to_date}, notes}`; lump sums take a fraction (0.35 = 35%)
- `POST /api/outbox/{id}/retry`: send a failed or unsent email again
- `GET /api/jobs/{id}/payapp/{invoice_id}.pdf`: the pay app as a PDF
- `POST /api/auth/forgot` `{email}`; `POST /api/auth/reset` `{token, password}`; `POST /api/auth/verify` `{token}`; `POST /api/auth/resend-verify`; `POST /api/auth/password` `{current, new}`
- `POST /api/auth/invite-info` `{token}`; `POST /api/auth/accept-invite` `{token, name, password}`
- `GET /api/team`; owners: `POST /api/team/invite` `{email, role}`, `/api/team/{user_id}/role` `{role}`, `/api/team/{user_id}/remove`, `/api/team/invites/{id}/revoke`, `/api/account/delete` `{password, confirm}`
- `POST /api/onboarding/dismiss`; team only: `GET /api/admin/companies`
- `GET /healthz`
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

Local `python3 server.py` is the primary path. No external services or API keys are required; email, texting and inbound email are off until their settings are filled in.
