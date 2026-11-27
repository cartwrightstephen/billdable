# Duracash

Rule-based get-paid tool for small contractors (2–12 people, roughly $750k–$2.5M). It works beside QuickBooks and reads from it (read-only): payments from a CSV export, or through the QuickBooks Online connection once the Intuit app's keys are set. It does not replace Jobber, Housecall Pro, or the books. There is no AI in the product yet.

## Run locally

```bash
python3 server.py
```

Then open http://127.0.0.1:8765

The public landing page is the front door. Create a company there, or sign in.

- Demo: **Look around a sample company** on the landing page opens Redbud Electric Co., a small electrical subcontractor (demo_seed.py), with no account, or sign in as `owner@redbudelectric.example` / `demo`. Anyone can look around and reset it (see `DURACASH_DEMO` for production).
- Create account: a blank company space. Your name, email and a password of 10+ characters; the company name can wait (the company is named after you until it is typed in Settings › Company). At most 5 sign-ups an hour from one network. The Dashboard's setup checklist then leads to the company details, the first job, its supporting documents and the first send.

## Data

The product was called Billdable until October 2026. A server set up with the old `BILLDABLE_*` variable names, an existing `companies/billdable.db` and older `billdable-*` backups all keep working.

All data lives in one directory, set by the `DURACASH_DATA_DIR` environment variable. The default is `data/` beside `server.py`.

- `companies/duracash.db`: every company, in one SQLite database (WAL mode). Each job, audit row, email, inbox item, daily record and file is its own row, so a save writes only what changed, and companies don't wait on each other (each has its own lock). Schema migrations are in `store.py`.
- `companies/<company_id>.json`: the older one-file-per-company layout, used when `DURACASH_STORAGE=json`. On its first start the SQLite store imports any such files and moves them to `companies/imported-json-<time>/`; it never deletes them.
- `companies/snapshots/`: a copy of a company taken before each confirmed reset
- `auth.json`: users, password hashes, and the SHA-256 of each session token (a copy of the file signs nobody in); two-step sign-in keys are encrypted with the server key (`DURACASH_SECRET_KEY`)

JSON files are written to a temp file and renamed into place, and SQLite commits each save as one transaction, so a crash never leaves half a write.

One writer at a time (security review M7): the server holds an exclusive lock on `<data dir>/.duracash-server.lock` while it runs, so a second server started on the same data directory refuses to start. The company, accounts, inbound-address, QuickBooks and deleted-companies locks hold across processes (`storage.ProcessLock`, flock on small `.lock` files beside the data; companies' are in `companies/.locks/`), so `admin.py` run from the Shell while the server is up waits its turn. And a save is refused, with nothing written, when the company was written by another program after this one read it (SQLite: its version moved on; JSON: the file was replaced): the request answers 409 "Another change to your company was saved at the same moment… reload and try again". Backups are described under "Backups, health and logs". `GET /api/export` downloads the signed-in company's data as JSON, whichever layout is in use; `GET /api/export/with-files.zip` (Export › Everything with files) is the same JSON as `company.json` plus every uploaded file under `files/<file id>-<name>`, built in a temporary file and streamed, six an hour per company.

On Render, the default filesystem does not survive a deploy or restart. `render.yaml` builds the Dockerfile (which installs `age` for off-site backups), attaches a 10 GB persistent disk at `/var/data` (this needs a paid instance) and sets `DURACASH_DATA_DIR` to it. It also defines `duracash-staging`, a free service that deploys `dev` with throwaway data, which emails and texts only the people on its allow-lists (`docs/setup-integrations.md`); without a disk every deploy starts with no accounts. After the first deploy with the disk, deploy a second time, check that `/healthz` shows the same `last_backup` and that an account still signs in, then set `DURACASH_DATA_PERSISTENT=1` to remove the landing page's "data may be erased" notice. When a signed-in user's company is missing, the server never serves or saves a blank one: the request answers 503 ("Your company's data isn't available right now. Nothing was changed; we've been alerted."), and a `company data missing` error line and an alert go out.

Set `COOKIE_SECURE=1` in production to force the `Secure` flag on the session cookie and send `Strict-Transport-Security` (one year, subdomains included) with every answer. Without it, the cookie flag is set when the request arrives with `X-Forwarded-Proto: https`.

Also in the data directory: `uploads/<company_id>/` (imported files; each company's files are capped, see `DURACASH_STORAGE_CAP_MB`), `inbound_tokens.json` (the secret parts of each company's inbound email, SMS and calendar addresses, looked up by a keyed hash and kept encrypted), `qbo_connections.json` and `docusign_connections.json` (QuickBooks and DocuSign tokens, encrypted), `audit_anchors.json` (the daily audit check's anchors), `secret.key` (the server key on a laptop, when `DURACASH_SECRET_KEY` isn't set), `helper_answers.json` (helper answers added in the app) and `helper_log.json` (questions asked of the helper).

### Settings

| Variable | What it does |
| --- | --- |
| `DURACASH_DATA_DIR` | Where all data lives (see above). |
| `DURACASH_DEMO` | `readonly` keeps the demo for prospects: anyone can sign in and look around, and nothing can be changed (no edits, reset, password or two-step changes). `off` removes it anywhere. The default is a writable demo in development and a read-only one in production; `render.yaml` sets `readonly` on staging too, since the demo's password is public. |
| `DURACASH_DEMO_SEED` | `hartline` makes the demo company the Hartline Remodeling data the tests are written against (seed.py) instead of Redbud Electric (demo_seed.py). The browser suite's server sets it. A demo company from an older seed is made again from the current one once, on start. |
| `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET` | Billing (billing.py). With them, Settings › Billing shows everyone the subscription, and owners get Subscribe (Stripe Checkout, the free trial carried over) and Manage billing (Stripe's Customer Portal). Point a Stripe webhook at `<DURACASH_URL>/api/stripe/webhook`; see runbook 7. |
| `BILLING_DAY`, `STRIPE_PARTNER_PRICE_ID` | Charges land on `BILLING_DAY` of the month (default 5, after the contractor's own 1st-of-the-month pay app is out): a trial ends on it, and a later subscription is anchored to it with the first part-month prorated. Design partners (`admin.py partner COMPANY_ID on`) pay `STRIPE_PARTNER_PRICE_ID`. |
| `MONTHLY_SUMMARY_EMAILS` | `1`: the server emails each owner last month's summary on the 1st to the 3rd, once. The monthly restore drill runs by itself whenever backups exist, with an alert if it fails. |
| `DAILY_EMAIL_HOUR_UTC` | The hour (UTC, default `11`) from which each owner with a confirmed email gets the daily "due today" email, once a day, when email is set up, and a member what is assigned to them. Nothing is sent on a day with nothing due; a job to finish is named once a week; a company turns it off on Settings › Company. Emailed and texted reminders go from this hour too. |
| `BILLING_ENFORCE`, `BILLING_GRACE_DAYS` | `BILLING_ENFORCE=1` makes a company read-only (never deleted) when its subscription is unpaid or cancelled, or when the trial ended more than `BILLING_GRACE_DAYS` (14) ago with no subscription. A declined card that Stripe is still retrying only shows a warning. Off by default. |
| `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET` | The Intuit Developer app's keys; with them, owners can connect QuickBooks, send pay apps to it as invoices and bring its payments back. Add `<DURACASH_URL>/api/qbo/callback` as a redirect URI in the Intuit app. `QBO_ENVIRONMENT` is `sandbox` or `production` and must match the keys; unset, it is `production` when `DURACASH_ENV=production` and `sandbox` elsewhere (`QBO_ENV` still works). Setup: docs/setup-integrations.md. Tokens live in `qbo_connections.json`, encrypted with `DURACASH_SECRET_KEY` and left out of the backups (a company restored onto a new disk connects again). |
| `DOCUSIGN_CLIENT_ID`, `DOCUSIGN_CLIENT_SECRET`, `DOCUSIGN_ENV`, `DOCUSIGN_CONNECT_KEY` | DocuSign (docusign.py). With the integration key and its secret, owners can connect the company's DocuSign account on Settings › Company, and the Sign step of a pay app, a change order and a state waiver form offers Sign with DocuSign beside the typed name. `DOCUSIGN_ENV=production` after DocuSign's go-live review; the default is the demo environment. `DOCUSIGN_CONNECT_KEY` is the Connect HMAC secret: without it no webhook is asked for or trusted, and a signature is filed when the signer comes back or on Check DocuSign. Redirect URI `<DURACASH_URL>/api/docusign/callback`; see docs/setup-integrations.md. Tokens live in `docusign_connections.json`, encrypted with `DURACASH_SECRET_KEY` and left out of the backups like QuickBooks's (a company restored onto a new disk connects again); envelopes in flight are in `docusign_envelopes.json`, which the backups hold. Deleting a company removes its tokens and its envelopes in flight. |
| `DURACASH_STORAGE` | `json` keeps the one-file-per-company layout. Unset (the default) uses SQLite. |
| `DURACASH_SECRET_KEY` | The server key, at least 32 characters: make one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. It encrypts two-step sign-in keys, QuickBooks and DocuSign tokens, the secret part of each inbound address and the local backups, and keys the audit trail (`vault.py`). Production and staging refuse to start without it; elsewhere one is made once in `<data dir>/secret.key`. Keep a copy in both password managers: without it the local backups can't be read, two-step sign-in has to be set up again and QuickBooks and DocuSign connected again. No backup holds it. |
| `DURACASH_ENV` | `production`, `staging` or `development`; nothing else starts (a typo such as `prod` is refused at start-up). Unset means `development`, but only off Render: on Render (which sets `RENDER`) an unset value is refused too, so a missing setting never brings the lenient development rules to a live server. `production` makes the demo read-only (`DURACASH_DEMO`) and refuses to start unless `DURACASH_DATA_DIR` is an existing directory, `COOKIE_SECURE=1`, `DURACASH_URL` is an `https://` address, the off-site backup settings (`BACKUP_S3_*` and `BACKUP_AGE_RECIPIENT`) are filled in and `ALERT_WEBHOOK_URL` is set; production and staging also need `DURACASH_SECRET_KEY`. `staging` limits email and texts to the allow-lists. `/healthz` reports which one is running. |
| `DURACASH_ALLOW_NO_OFFSITE` | `1` lets production start without the off-site backup and alert settings, with a warning in the log on every start. Only for a deploy that has to come up before they are filled in; remove it once they work. |
| `DURACASH_DATA_PERSISTENT` | `1` once a deploy has been seen to keep the data. Until then the landing page says "Demo: data may be erased". |
| `COOKIE_SECURE` | `1` forces the Secure cookie flag and sends `Strict-Transport-Security`. |
| `DURACASH_STORAGE_CAP_MB`, `DURACASH_TRIAL_STORAGE_CAP_MB` | How much a company may keep in uploaded files (H5): 2048 MB once it has a subscription (or is a design partner), 1024 MB before. An upload, email attachment or shared file that would go past it is refused with "Your company's file storage is full: … used. Nothing was saved." Settings › Company › Your data shows what is used (`GET /api/storage`). |
| `DURACASH_UPLOADS_PER_HOUR_USER`, `DURACASH_UPLOADS_PER_HOUR_COMPANY` | Uploads an hour (default 120 a person, 400 a company), counted before the file is read; past them the upload answers 429. |
| `DURACASH_MAX_CONNECTIONS` | Connections served at once (default 200, M8). One more gets a short 503 "The server is busy" and is closed. The request line and headers must arrive within 20 seconds (`HEADER_SECONDS`), the body within 60. |
| `DURACASH_LINK_DAYS` | Days a customer page (`/p/…`) or change-order signing link (`/s/…`) works after it was last emailed (default 60, L2). Sending the pay app or a reminder again, or asking for the signature again, renews it; an old link shows "This link has expired. Ask … to send it again" (410). |
| `HELPER_LOG_DAYS` | Days helper questions are kept in `helper_log.json` (default 365); the file also keeps at most 5,000 entries and 5 MB, oldest dropped first. |
| `TRUSTED_PROXY_HOPS` | How many proxies in front of the server add to `X-Forwarded-For` (default `1`, Render's). The caller's address, used by the sign-in and sign-up limits, is that many entries from the end. `0` when nothing sits in front: the header is ignored. The first request with the header logs a `forwarded header shape` line with how many entries it had (never the addresses); a warning there means the setting is higher than what arrives, and every caller shares one limit. |
| `HELPER_ADMIN_USER_IDS` | Comma-separated account ids of the people who may review helper questions and edit answers (the founders). Nobody can, if unset. Ids, not emails, because sign-up doesn't verify email. Each person's id is at the bottom of the Help page. |
| `INBOUND_EMAIL_ADDRESS_TEMPLATE` | The forwarding address shown in the Inbox, with `{token}` where the company's secret goes, e.g. `jobs+{token}@in.example.com`. Display only; the email service does the routing. |
| `INBOUND_WEBHOOK_PASSWORD` | When set, the inbound email web address needs HTTP Basic with this password (any user name): give the email service `https://duracash:PASSWORD@<site>/api/inbound/email/<token>`. Anything else is refused (401). In production, inbound email is refused (503) unless this or `INBOUND_SIGNING_KEY` is set: the address alone is not enough. The same post twice in 24 hours (a replay, or the provider trying again) is answered 200 and filed once. |
| `INBOUND_SIGNING_KEY` | Mailgun's webhook signing key: when set, each inbound email post must carry Mailgun's `timestamp`, `token` and `signature` (HMAC-SHA256), no more than 15 minutes old, or it is refused (401). Each inbound email or text address takes at most 120 posts an hour either way (429 after that). The provider web addresses are shown to owners only, built from `DURACASH_URL` when it is set. |
| `DURACASH_URL` | The public address, e.g. `https://duracash.onrender.com`. Password-reset, verification and invite emails use it for their links; they are not sent without it (a link built from the request's Host header could point a reset token at someone else's site). |
| `LEGAL_ENTITY`, `LEGAL_ADDRESS`, `LEGAL_EMAIL`, `LEGAL_STATE`, `DATA_REGION` | Company details shown in the Terms of Service and Privacy Policy (`/terms`, `/privacy`). Until all are set and `LEGAL_REVIEWED=1`, both pages say they are a draft. |
| `LEGAL_REVIEWED` | `1` once a lawyer has reviewed the text in `legal/` and the details above are filled in. |
| `BACKUP_HOURS` | Hours between automatic backups, default 24; `0` turns them off. |
| `BACKUP_KEEP` | At most this many local backups are kept, default 14. |
| `BACKUP_KEEP_DAYS` | Local backups, what a restore moved aside (`pre-restore-<time>/`) and copies of files that couldn't be read (`*.bad-*`) are removed once this many days old, default 14. The privacy policy quotes it. |
| `BACKUP_OFFSITE_DAYS` | How long the off-site bucket keeps a copy (its lifecycle rule, set at the provider), default 35. Only the privacy policy uses it; keep the two the same. |
| `BACKUP_UPLOADS` | `0` leaves uploaded files out of the local backups (they can be large; each backup holds them all). The off-site copy still has them, and a restore of such a backup leaves the uploads on the disk in place. Default: included. |
| `BACKUP_S3_ENDPOINT`, `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`, `BACKUP_S3_KEY_ID`, `BACKUP_S3_SECRET` | S3-compatible storage for off-site copies, e.g. Backblaze B2 (`https://s3.us-west-004.backblazeb2.com`, region `us-west-004`). Use a write-only application key and a bucket with Object Lock. All five set turns the upload on. |
| `BACKUP_S3_PREFIX` | Folder in the bucket, default `duracash/`. |
| `BACKUP_AGE_RECIPIENT` | The age public key (`age1…`) backups are encrypted to before upload. Keep the private key off the server, in both founders' password managers. Without it nothing is uploaded. |
| `BACKUP_AGE_BINARY` | Path to the `age` program, default `age` on the PATH. The Dockerfile installs it, and `render.yaml` deploys with the Dockerfile. |
| `ALERT_WEBHOOK_URL` | An `https://` incoming-webhook address (Slack, Discord, Better Stack, ...). Each crash and failed backup posts one line with the request id; rate limited; no client data. |
| `SMTP_HOST` | Mail server for outgoing email. Unset: approved emails stay in the Outbox as "not sent: email delivery is not set up". |
| `SMTP_PORT` | Default 587 (465 with `SMTP_SECURITY=ssl`). |
| `SMTP_USER`, `SMTP_PASSWORD` | Login for the mail provider, if it needs one. |
| `SMTP_SECURITY` | `starttls` (default), `ssl`, or `none` (local testing only). |
| `MAIL_FROM` | Sender address on a domain the provider has verified (SPF and DKIM), e.g. `billing@duracash.com`. Required with `SMTP_HOST`. |
| `MAIL_EVENTS_TOKEN` | A random secret of 16+ characters. Give the mail provider `https://<site>/api/mail-events/<this token>` as its bounce (and spam-complaint) webhook; a bounced email's Outbox row turns "bounced" and its job's status line says so. Formats: `mail_events.py`. The token is compared in constant time, and the address takes at most 120 posts an hour. |
| `MAIL_EVENTS_PASSWORD` | When set, the bounce webhook also needs HTTP Basic with this password (any user name): `https://duracash:PASSWORD@<site>/api/mail-events/<token>`. Postmark and SendGrid both take credentials in the webhook address; neither signs its webhooks in a way the standard library can check (SendGrid's signature is ECDSA). |
| `MAIL_ALLOWLIST` | Comma-separated addresses and `@domains`. When set, mail to anyone else isn't sent; an Outbox email stays there marked why. With `DURACASH_ENV=staging`, a blank list sends to nobody. Sign-in codes aren't asked of someone off the list, outside production. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER` | Twilio, for texts in and out. Inbound texts must carry a valid `X-Twilio-Signature`; production and staging refuse them without the token, and only local development takes them unsigned. Photos and PDFs sent by text are downloaded into the Inbox item (`twilio_media.py`). Texts only go to team members who confirmed their number on My account; clients are never texted. |
| `SMS_ALLOWLIST` | Comma-separated numbers. When set, texts to any other number aren't sent. With `MAIL_ALLOWLIST` or `DURACASH_ENV=staging` in force and this blank, no text goes out. |
| `SMS_DAILY_LIMIT` | Texts per company per 24 hours, default 20. Counted in `sms_sends.json`. |
| `REMINDER_REPEAT_DAYS` | Days before the same reminder comes round again, default 3. |
| `MAIL_DAILY_LIMIT` | Emails per company per 24 hours, default 50. Sign-up doesn't verify anyone, so this keeps one account from using us to send spam. Counted in `mail_sends.json`. |

## Importing

Everything comes into the Inbox, gets a suggested job (job code, address, job name, client name, or the client's email for pay apps as sender), a kind (change order, lien waiver, backup for the pay app, delivery ticket, time sheet, photo, contract, diary entry, note), and any dollar amounts and dates found. A person adds it to the job record with one tap (**Add to <job>**); Undo takes it back off unless a pay app's supporting documents use it. A payments export dropped in the Inbox offers **Read as payments**. No AI: the rules are in `importer.py`.

- Files: drop anywhere, paste a screenshot, or choose files on Import/Export › Import, where **Which job?** starts on **Not sure, file later** unless you came from a job's Import link. On a job page they go straight onto that job. PDF, images (JPG, PNG, GIF, WEBP, HEIC), Word, Excel, CSV, TXT and `.eml`, up to 15 MB. HTML, SVG and scripts are refused. Files are served back only to the same company, with `Content-Security-Policy: sandbox`, except PDFs, which are served without it so the browser's PDF viewer opens them.
- Saved emails (`.eml`): sender, subject, date, body and attachments are read with Python's email parser.
- Pasted text: an email (with or without headers), a text thread, or notes. Notes split at every line that starts with a date (`10/12`, `Oct 12, 2026`, `2026-10-12`) and file as diary entries with that date.
- Inbound email: `POST /api/inbound/email/<token>` accepts raw MIME, a multipart form post, or JSON. The field names handled are the common ones for SendGrid Inbound Parse, Mailgun routes and Postmark inbound, written from their documentation; none has been tested against the live service.
- Inbound SMS: `POST /api/inbound/sms/<token>` accepts Twilio's form post and answers with empty TwiML. With `TWILIO_AUTH_TOKEN` set, photos and PDFs sent by text are downloaded into the Inbox item (`twilio_media.py`); when a download fails, they are kept as links. The item reads from the team member whose confirmed number sent it, or **Unknown sender**. The job is only suggested: filing a text needs the permission to edit company details. A one-word STOP turns texts off for that number and isn't filed.

What happened on site goes on **New job record** (Jobs): the Diary, crew, weather and materials; tick boxes for Quantities, Change order work and New punchlist items; and sections for Delivery tickets, Progress photos, Time sheets, Invoices, Inspections and Other documents and notes. Each file makes one job record of its kind, and the diary one labelled "Diary entry <date>" (`domain/daily.py`).

## Sending email

Approved pay apps, drafts sent for the client's review, follow-ups, waivers and retainage requests are emailed over SMTP (`mailer.py`). Postmark, SendGrid, Mailgun, Amazon SES and Google Workspace all accept SMTP, so pick one and set the variables above. Tested only against a local test server in `tests/test_mail.py`, not against a real provider.

- From is "<company> via Duracash" at `MAIL_FROM`; Reply-To is the person who approved the send (kept if someone else retries it), so the client's answer goes to the contractor. Addresses in the client's **Also email (CC)** are copied on pay apps, and from the third follow-up on a pay app the **Also email from the third follow-up** address is too. Subjects read "Pay application PA-KIRK-01 — <job> — <amount> — <company>" so an accounts-payable desk can file them.
- A pay app goes with its full text, the schedule-of-values continuation sheet when the job has one, the supporting documents checklist, and the files behind the records attached to its supporting documents (up to 15 MB in total; anything left off is named in the email). A waiver email attaches the newest lien waiver on the job record not sent before, and says the waiver will follow if there is none. A retainage request attaches the closeout documents and is spaced by the client's days between follow-ups, like follow-ups. One follow-up names every unpaid pay app on the job.
- Delivery happens after the change is saved and the state lock is released, so a slow mail server never holds up other requests. Each email shows sending, sent (with the time), failed (with the reason) or not sent in the Outbox. Failed and unsent emails have Send again (`POST /api/outbox/{id}/retry`) where the server sends mail, and a pay app marked sent whose email didn't go out says so under its Sent pill. The approval itself stands either way.
- The person approving must have confirmed their email address first (when email is set up), because replies go to it; until then emails are held as "not sent" with Send again. A pay app, or a draft sent for review, also carries the pay app as a PDF (`payapp_pdf.py`), made from the pay app when the email goes out.
- The demo account never sends. Header fields are stripped of line breaks and control characters, addresses must be plain (`name@domain.tld`), and the mail server is given the recipients directly rather than reading them back from the headers.
- The daily limit is counted in `mail_sends.json` in the data directory, outside the company's own data, so resetting a company doesn't reset it. An email still "sending" after 30 minutes (the server stopped mid-send) can be sent again.

## Accounts and team

- A company has owners and members. Owners invite people (Settings › Team), change roles, remove people, rotate the inbound addresses, reset the company and delete it. Owners also take every final or emailed job step (`OWNER_ACTIONS` in `server.py`): sending pay apps, lien waivers and retainage requests, deleting drafts and jobs, pulling back a sent pay app, accepting a short pay, and finishing or reopening a job. A company can let members send pay apps, waivers and retainage requests (**Members can send…** on Settings › Company). Members do everything else. Permission profiles on Settings › Team now set what each person may do; **Change a job's contract, change orders and mark work done** (`manage_jobs`: Edit job, Final pay app, Undo done, signing a change order on paper, withdrawing one, billing one unsigned, a schedule saved as a new contract amount) is the Owner's and not the Member profile's, and a custom profile that could delete drafts or finish jobs got it when it was added. **See payroll and sensitive documents** (`sensitive_docs`) opens the files on certified payroll, time sheet, lien waiver and contract records, a job's contract and Inbox items read as one, and downloads the job file zip (`/api/files/<id>` and `/api/jobs/<id>/archive/job-file.zip` answer 403 without it; the page shows those files' names without a link). Owners hold it; when it was added, custom profiles that send pay apps, waivers or retainage requests or see the financials got it, and Member did not. **See company financials** also covers the Dashboard's Results and Check against QuickBooks; the company's whole Activity log takes **Export and reset data** (each job's history stays on its page); Send again on an Outbox email takes the permission that sent it (a pay app, Send pay apps; a waiver, Send lien waivers). The last owner can't step down or be removed. Removing someone signs them out at once.
- Password reset: "Forgot your password?" emails a link that works once, for an hour, and signs the account out everywhere else. At most 3 requests per email per hour; the answer is the same whether or not the account exists. Signed in, the password can be changed on Settings › My account.
- Email verification: sign-up sends a link (valid 3 days). An unconfirmed person can use everything but can't have emails sent under their name.
- Sign-up on a server that sends mail (L3) answers the same whatever the email: "Check your email: we sent a link…". A new account is signed in by the link in its confirmation email (once; a link sent again from My account only confirms). An email that already has an account is sent "You already have a Duracash account" with how to sign in or reset instead, and nothing is created. Without mail (a laptop, staging without SMTP) sign-up signs in at once and says when the email is taken, since no link could reach anyone.
- Passwords: 10+ characters, and not one of the common ones in `common_passwords.txt` (with digits, symbols or look-alike letters added), one or two characters repeated, a run of keys or letters (`1234567890`, `qwertyuiop`), or the person's own email or name (`passwords.py`). Checked at sign-up, invite, reset and change.
- Settings › My account › Where you're signed in lists each live session (the browser as "Chrome on Mac", when it signed in and was last used), with Sign out for one and **Sign out everywhere else** (`GET /api/auth/sessions`, `POST /api/auth/sessions/end`). Only that summary of the browser is kept, not the header.
- Invites last 7 days. Without email set up, the owner gets the link to pass on themselves.
- Only a SHA-256 of each reset, verification and invite token is stored. Tokens travel after `#` in links, so they never reach server logs.
- Deleting the company (owner, password plus the typed company name) removes its users, sessions, data, uploads and inbound addresses. Backups already made still hold it until they rotate out.
- Every team change is checked against the acting person's current role at the moment it is made, and every request re-checks the session once its body has arrived (bodies must arrive within 60 seconds), so someone removed or demoted mid-request can't finish with their old rights.
- Removing someone also changes the company's inbound email, text and calendar addresses (they knew them), and cancels the invites they sent; demoting an owner cancels their invites.
- Invites need a confirmed email when email is set up, and are limited to 20 a day per company; confirmation links to 3 an hour per person. These system emails count against the company's daily email limit. Invite and reset subjects are fixed text; names typed by users appear only in the body, flattened.
- Changing a password signs out every other session and is throttled like sign-in. The demo account's password can't be changed.
- Older unexpired reset links stay valid when another is requested (so a stranger's requests can't cancel yours); using one uses them all up. The reset request does its work in the background, so the answer takes the same time for any email; sign-in also does the same work for unknown emails.
- Without `DURACASH_URL`, an invite returns only the path (`/#invite=…`); the owner's browser adds its own address.
- Deleting a company also records it in `deleted_companies.json`, so a request that was already running can't write it back, and removes its helper questions and mail counter.
- Two-step sign-in (an authenticator app, with recovery codes shown once) is on Settings › My account; setting it up asks for the password again. A password reset for a two-step account changes the password and still asks for a code at the next sign-in. Someone who lost the phone and the recovery codes: `python3 admin.py two-step-off EMAIL` (runbook 3a).
- The password asked again inside a session (delete, reset, two-step) is throttled like sign-in: after 3 wrong ones, a growing wait (429).
- Every POST from a browser page must come from this site: one whose `Origin` (or, without one, `Sec-Fetch-Site: cross-site`) names another site is refused (403), sign-in, sign-up, the demo and sign-out included; the webhooks (`/api/inbound/`, `/api/mail-events/`, `/api/stripe/webhook`) and the share menu keep their own checks. JSON routes refuse the bodies an HTML form can send (`application/x-www-form-urlencoded`, `multipart/form-data`, `text/plain`) with 415.
- Sign-up is limited to 5 an hour per network, and the server sends at most 300 system emails (reset, verification, invite, monthly summary) an hour in all.
- Not built: single sign-on, per-job permissions.

## Terms and privacy

`legal/terms.html` and `legal/privacy.html`, served at `/terms` and `/privacy` with the company details from the settings above (HTML-escaped). Sign-up and accepting an invite need the box ticked; the server records the version (`legal.TERMS_VERSION`) and time on the account. The text is a draft written to match what the code does (data collected, sharing, retention, deletion, backups); **have a lawyer review it before relying on it**, then set `LEGAL_REVIEWED=1`. Change `TERMS_VERSION` when the text changes.

## Pay app PDF

`GET /api/jobs/{id}/payapp/{invoice_id}.pdf`, and attached to pay-app emails and to drafts sent for review (never for a pay app voided after the email was approved). Older jobs' job-level held retainage counts in lines 4 and 5; unsigned change orders billed on the pay app show as line 2a and count in the contract sum; a voided pay app is marked VOID and keeps its own figures. Page 1 is an application summary (lines 1–9: contract, change orders, completed to date, retainage, previous pay apps, current payment due, balance); then a continuation sheet of every schedule line (or the pay app's lines; change orders billed unsigned get their own row, so the sheet's totals match line 4), the notes typed with Progress Pay App or Final Pay App, and the supporting documents checklist. The layout follows the G702/G703 pattern but it is Duracash's own document and says it is not an AIA form. Drafts, and drafts sent for the client's review, are marked. A final pay app is marked FINAL APPLICATION. Standard library only; built-in fonts. Lien waivers are not generated: their forms are set by state law.

## Trial

`trial.py` works out where each company stands against the agreed trial: nothing to pay until the first pay app sent through Duracash is paid, or 90 days, extended to 120 if a pay app is still unpaid on day 90. A company set to bill homeowners in draws (a setting from before Oct 29, `billing_style`) gets 45 days or the first paid draw, with no extension. Today shows it, and the founders' page lists every company with its trial. Without the Stripe settings nothing is charged or locked when a trial ends; with them, Stripe's trial ends when the first pay app is paid (retried on the next change if Stripe is down), and `BILLING_ENFORCE` decides whether an unpaid company turns read-only. Companies that signed up before the trial was tracked aren't tracked.

## Backups, health and logs

- `backup.py`: one backup on start-up if the newest is older than `BACKUP_HOURS`, then every `BACKUP_HOURS`, as `.tar.gz.enc` files in `<data dir>/backups` (accounts, companies, inbound tokens, helper files, the mail counter and uploads), encrypted with the server key (`DURACASH_SECRET_KEY`; older plain `.tar.gz` backups still restore). An hourly sweep removes backups older than `BACKUP_KEEP_DAYS` or beyond the newest `BACKUP_KEEP`, and restore leftovers and `*.bad-*` copies older than `BACKUP_KEEP_DAYS`. They are on the same disk as the data, so when the `BACKUP_S3_*` settings are filled in, each new backup is also encrypted with age (from the plain archive, so the age key alone opens it) and uploaded (`offsite.py`); `python3 backup.py upload [FILE]` does one by hand. To restore an off-site copy: download the `.age` file, `age -d -i key.txt FILE.age > backup.tar.gz`, then `python3 backup.py restore backup.tar.gz`. `python3 backup.py now|list|restore FILE|drill [FILE]` (drill: restore into a temporary folder and check it reads, without touching the live data; runbook 2); restore (server stopped) first unpacks into a staging folder and checks the archive is complete (it must hold `auth.json` and `companies/`, with nothing outside the data directory); only then does it move the current data aside to `pre-restore-<time>/` and swap the backup in. Company snapshots taken before a reset are included.
- `GET /healthz`: no sign-in; `{"ok": …, "env": …}` only: `ok` is whether the data directory is writable and has 200 MB free (503 if not), and `env` is `production`, `staging` or `development`. The details (free space, email, the last backup and off-site upload, alerts) are for the founders: `python3 admin.py health` in the Shell tab, and a `health check failing` log line when it fails. Point an uptime monitor (for example Better Stack's free tier) at it: it is the only thing that notices the server being down.
- Before this code upgrades data from older code (a new schema, `migrations.py`), it backs everything up once, as `duracash-<time>-pre-upgrade-schema<N>.tar.gz.enc` among the other backups; `<data dir>/schema_version.json` says which schema the data was last opened with. If that backup fails the server doesn't start. Older code started on newer data logs and alerts that the upgraded companies won't open (runbook 1).
- On SIGTERM (every deploy and restart) the server stops taking connections, lets the requests being answered finish (up to 20 seconds; Render waits 30), then exits.
- QuickBooks tokens are encrypted with the server key and never in a backup (`qbo_connections.json` is left out, and read past in older backups): after a restore, connected companies stay connected, and a company restored onto a new disk connects again. Deleting a company removes its tokens and asks Intuit to revoke them.
- Logs: one JSON line per request on stderr (time, method, path, status, milliseconds, request id). Query strings and the secret parts of inbound and calendar addresses are removed. A crash logs its traceback with the request id, and the user sees the same id to quote. With `ALERT_WEBHOOK_URL` set, each crash also posts a one-line alert (`alerts.py`).

## Schedule of values

Every new job has one (`sov.py`): the lines typed at New job, or one lump-sum line for the contract amount when they are left blank. Jobs from before Nov 3 with no schedule keep dollar progress. Each line is a quantity at a unit rate (a lump sum is quantity 1, unit LS); overhead and profit is a percentage on top of the base lines. The schedule total must equal the contract; the editor shows the difference and offers to change the contract amount instead.

- A schedule can change until the first pay app is sent. A draft doesn't lock it: saving drafts it again. A draft with the client for review does, until it is answered or deleted. After a send, or once the work is finished, the card says why it is fixed.
- Rows can be pasted from Excel or Google Sheets (tabs), a CSV, or a PDF (columns separated by runs of spaces). A header row is optional. Total and markup rows are skipped and reported; an "Overhead & profit 15%" row fills in the percentage.
- Field totals are kept apart from what is billed. A New job record's quantities (a lump sum asked as % done to date, recorded as the step up from the field total) and the schedule card's **Import quantities** add to each line's field total and never touch a draft. **Correct field total** (with a reason) and **Undo import** change it; an open draft drafted at the old total follows unless it is with the client, a figure typed on a draft stays, and sent pay apps stay as they are.
- **Progress pay app** is the one way quantities reach a draft (**Final pay app** sets every line to 100%). A job on one lump-sum line can **Bill by dollars instead**: the pay app bills exactly the dollars typed. Each line starts at its total to date. A blank box bills the suggestion. A changed figure bills that, audited, and the field total stays. On the draft, any line's amount can be changed from $0 to what is left on it; a cleared box goes back to the suggestion. Each pay app bills the difference since the last one, line by line, plus overhead and profit on that period's base work, less retainage, and stores the lines as a G703-style continuation sheet.
- Quantities can't exceed the line (extra scope is a change order) or drop below what was billed, in quantity or in dollars. Numbers must be plain decimals (no exponents), and pasted text is read in bounded pieces.
- An approved change order adds its own lump-sum line at the CO amount, without markup. A CO billed while unsigned is not billed again when approved.
- All amounts are whole cents with half-up rounding on integers; the browser repeats the same arithmetic for the live totals.
- Stored materials (G703 column F) are entered per line on the Progress pay app page and billed when they arrive; total to date is previous + this period + stored. When later work passes where a line stood when its materials were stored, the page asks to move them into work done, so they are never billed twice. A pay app with new stored materials asks for each line's supplier invoice, receipt or photo (a check by default; Required per job), uploaded on the pay app's **Stored materials backup** card.
- Retainage can be set per line (**Retainage by line**, blank for the job's percent) and per pay app for one period (**Retainage this period %** on a draft). The G702 summary splits line 5 into 5a (completed work) and 5b (stored materials).
- **Bill retainage release** drafts a pay app with no new work that bills retainage held, all or part, at substantial completion or final (final: the work finished and billed, the closeout documents in). It is sent and paid like any pay app, and its payment brings its lien waiver due.
- A job started mid-project takes its earlier pay apps (New job's **This job already has pay apps**, or Edit job): pay app by pay app (each line in dollars or % complete, retainage held, paid and when) or as totals to date, with the old PDFs kept on the job records. The next pay app's number and Previous column follow them; they are never emailed or put in QuickBooks.

## Reminders and texts

Internal only: clients get email, never texts (`sms.py`, `reminders.py`, page: My account, Notifications).

- Each person picks, for Important dates, Missing information and Change order work, any of in the app, email and text. Someone new starts with every kind in the app; email and text stay off until picked. The same item comes round every `REMINDER_REPEAT_DAYS` until it is dealt with, then stops.
- Email and text go from the hourly loop, at most one message per person per channel per day, from `DAILY_EMAIL_HOUR_UTC`. In the app they are Reminder rows on the Dashboard's To do. Nothing goes at start-up: the first run is an hour later. A read-only company is skipped.
- A number takes a texted code on My account before texts turn on. A client's number is refused in any format, when saved, when a code goes and at every send.
- `MAIL_ALLOWLIST` and `SMS_ALLOWLIST` limit who gets anything; on `duracash-staging` (`DURACASH_ENV=staging`) a blank list sends to nobody. Accounts and live tests: `docs/setup-integrations.md`.
- Not built: a Twilio number shared by companies for inbound texts. One number files to one company.

## Calendar feed

`GET /api/calendar/<token>.ics` is an iCalendar feed of follow-ups due, expected approvals, sends, payments, waivers, closeouts and open jobs' anticipated end dates. Google Calendar, Apple Calendar and Outlook can subscribe to it; the URL carries a secret instead of a login. Calendar apps refresh subscribed feeds on their own schedule. The feed and the Calendar page say these are contract and follow-up dates, not legal deadlines. Lien deadlines are on Jobs, Compliance (below), not in the feed; Duracash computes no prompt-pay dates.

## Compliance documents and lien deadlines

Jobs, Compliance (`/jobs/compliance`; `compliance.py`, `liens.py`, `static/js/views/compliance.js`). Data in `state["compliance"]`, apart from the jobs.

- **Company documents**: certificate of insurance (COI), W-9, contractor license, bond, DBE certification, certified payroll, other. Each has a holder, issuer, number, effective and expiry dates, an optional client (and job) it is for, "names the client as additional insured" on a COI, and one file, stored through the importer's file store with its kind (insurance, payroll, other). A later document of the same kind for the same client takes over from the earlier one (Replaced).
- **No tax IDs**: a W-9 and certified payroll keep no number, only the file; a number shaped like a Social Security or employer ID, or a field naming one, is refused. W-9 and certified payroll files are private: they open (on `/api/compliance/docs/<id>/file` or `/api/files/<id>`) only for people with the "See payroll and sensitive documents" permission, as payroll on a job record does, and the export with files leaves them out for anyone without it. Deleting a document deletes its file.
- **What clients need**: a client (or one of its jobs) needs a kind of document, a COI optionally naming them as additional insured (then only that client's own COI with the box ticked meets it). Status: on file, expired, missing.
- **Soft gate**: when a job's client needs a COI and the one on file has expired, or there is none, the pay app page shows a warning above Approve & send. It never stops the send; it is separate from the supporting document gates.
- **Reminders**: kind "Compliance and lien dates" (`reminders.py`), in the app for everyone unless they turn it off on My account: a document 30, 14 and 3 days before it expires (the days are set on the tab) and once expired; a client's need with nothing current on file; a lien deadline within the first reminder day on an open job (the claim of lien only while money is owed; not the optional preliminary notice). In the app they are rows on the Dashboard's To do.
- **Lien deadlines**: per open job, from the rules of the job's state (`liens.STATE_RULES`, one dict per state; only Georgia is filled). Inputs: the state (from the job's address unless typed), who hired the company (from the client's type unless picked: owner, general contractor, lower tier, public), first and last furnishing (the job's start and the latest job record unless typed), the Notice of Commencement's filing date, a notice of contest received, a waiver signed outside Duracash, and each step marked done or not being done. Every row cites its section, and the page carries a "Not legal advice; confirm with your attorney" note.
- **Georgia** (O.C.G.A. § 44-14-360 et seq., read Nov 27, 2026): preliminary notice of lien rights, optional, 30 days after first furnishing (§ 44-14-361.3); notice to contractor, lower tier only and only when a Notice of Commencement was filed, 30 days from its filing or first furnishing, whichever is later (§ 44-14-361.5); claim of lien, 90 days after last furnishing, with a copy to the owner within 2 business days (§ 44-14-361.1(a)(2)); suit within 365 days of filing and the notice of commencement of lien action within 30 days of suing (§ 44-14-361.1(a)(3), § 44-14-367); suit within 60 days of a notice of contest (§ 44-14-368); affidavit of nonpayment before 90 days after an unpaid waiver was signed (§ 44-14-366(g), 60 days before SB 315 took effect on Jan 1, 2021; the waivers Duracash sent are already on the Dashboard, `domain/gawaivers.py`). Time is counted under § 44-14-369 and § 1-3-1(d)(3): the first day not counted, a last day on a weekend or a § 1-4-1 holiday (the federal holidays; the Governor's added days aren't known) moved to the next business day. The page shows the statute's last day and, when it was moved, a safer business day before it to file by.
- **Notarization**: Georgia's statutory waiver forms (§ 44-14-366) have signature, seal and witness lines and no notary block, so a waiver needs no notary; the affidavit of nonpayment is sworn before one. Duracash adds no notary feature.
- API: `GET /api/compliance`; `POST /api/compliance/docs` (and `/docs/<id>`, `/docs/<id>/delete`, `/docs/<id>/file` with the file as the body and its name in `X-Filename`), `POST /api/compliance/requirements`, `/requirements/<id>/delete`, `/api/compliance/settings` `{remind_days}`, `/api/compliance/liens/<job id>`. Changes answer with the state and `compliance`, the page's data.

## Helper

The ? badge opens the helper. It answers from a database of prepared answers, not an AI model:

- `helper/answers.json` ships with the code. Edit it in Git; `tests/test_helper.py` checks that common questions still land on the right answer.
- Accounts listed in `HELPER_ADMIN_USER_IDS` get a Helper answers page: questions with no good answer (unmatched, or marked not helpful), grouped and counted, with a form to answer them. Saved answers go to `helper_answers.json` in the data directory and work immediately for every company; the same id replaces a shipped answer.
- Matching is word overlap weighted by rarity, with synonyms (pay app = invoice = requisition, retainage = retention, ...), in `helper.py`.
- Some answers read the company's own jobs: what to do today, who owes you, follow-ups due, what is blocking a job, a drafted follow-up note, unbilled work, retainage, waivers, the Inbox, unsigned change orders, jobs ready to close. Their buttons call the normal endpoints, so every gate and confirmation still applies.
- Every question is logged with what it matched. The panel tells users that questions are saved. The log keeps a year (`HELPER_LOG_DAYS`), at most 5,000 entries and 5 MB.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Browser test: `DURACASH_DEMO_SEED=hartline DURACASH_DATA_DIR=$(mktemp -d) BACKUP_HOURS=0 HOST=127.0.0.1 PORT=8873 python3 server.py`, then `DURACASH_URL_UNDER_TEST=http://127.0.0.1:8873 node tests/e2e/smoke.mjs`. The steps are written against the Hartline Remodeling data (seed.py), which `DURACASH_DEMO_SEED=hartline` makes the demo company. The live demo's own steps (86-demo.mjs) need a second server started without it, named by `DURACASH_DEMO_URL_UNDER_TEST`; `DURACASH_STEPS=86` runs only those. Keep a Mac awake for it (run both under `caffeinate -s -i -m`): when it sleeps, the browser's waits run out and steps fail with timeouts that have nothing to do with the app. `pmset -g log` shows whether it slept.

Tests write to a temporary directory, never to `data/`. GitHub Actions (`.github/workflows/tests.yml`) runs them on every push and pull request; protect `main` so a red run blocks the merge (docs/runbooks.md, section 8, lists every setting). Actions are pinned to commit ids, `.github/dependabot.yml` proposes updates, and `.github/workflows/codeql.yml` scans the code.

## Project layout

- `domain/`: money rules, supporting document gates, status labels, client settings, the Dashboard's rows and the daily email's, email log (`domain/__init__.py`), and the audit chain (`domain/trail.py`); every name imports from `domain`
- `auth.py`: users and roles, password hashes, sessions, login throttling, reset/verify/invite tokens
- `storage.py`: data directory and atomic JSON writes
- `importer.py`: files, email parsing, notes splitting, job matching, the Inbox
- `calendar_feed.py`: the .ics feed
- `helper.py` and `helper/answers.json`: the helper and its answers
- `sov.py`: schedule-of-values arithmetic and the paste reader
- `qty_import.py`: quantities from a contractor's own file, added to the field totals
- `contract_import.py`: reads a contract's Word, PDF or text, for New job and Add client
- `doc_check.py`: reads a file attached as a supporting document: blank, job name, a waiver's amount and wording. Warnings only; no OCR
- `sms.py`, `twilio_media.py`: internal texts out, and files from texts in
- `reminders.py`: reminders by app, email and text
- `compliance.py`, `liens.py`: company documents, what clients need on file, and lien deadlines by state (Jobs, Compliance)
- `exports.py`, `job_archive.py`: the CSV lists and a finished job's zip
- `payapp_pdf.py`: the pay app PDF
- `docusign.py`, `docusign_http.py`: DocuSign eSignature: the connection, envelopes, the Connect webhook and filing what comes back signed
- `delivery.py`: what became of each email at each address (sent, delivered, opened, bounced) and the optional open-tracking image; `mail_events.py` reads the provider's webhooks into it
- `domain/payapp_core.py`: earlier pay apps for a job started mid-project, retainage by line and for one pay app, the retainage-only pay app, and the backup each stored-materials line needs (Nov 27)
- `trial.py`: trial status
- `domain/paydate.py`: each client's days to paid and the predicted pay date of an open pay app; `domain/revise.py`: Revise, a sent pay app back to a draft with a snapshot of what went
- `backup.py`: backups and restore
- `offsite.py`: encrypted off-site copies of backups (age, S3-compatible upload)
- `alerts.py`: crash and backup-failure alerts to a webhook
- `totp.py`: two-step sign-in codes (RFC 6238)
- `qr.py`: the QR code shown when two-step sign-in is set up (SVG, no library)
- `outbox.py`: sends the emails a change approved, and Send again from the Outbox
- `monthly.py`: the daily and monthly emails, the restore drill and the hourly loop that runs them
- `billing.py`: Stripe Checkout and Customer Portal, the signed webhook, ending Stripe's trial when the first pay app is paid, and whether a company is held read-only. Stripe's names are from their documentation as remembered, checked only against a stand-in (`tests/test_billing.py`)
- `qbo_csv.py`: payments from a QuickBooks CSV export, proposed against open pay apps
- `qbo_sync.py`: QuickBooks Online both ways: a sent pay app as an invoice (customer found or made, lines and retainage on the items chosen in settings, updated rather than duplicated), payments recorded in QuickBooks against those invoices recorded here (Sync now and daily), retries, token refresh and Intuit's errors in plain words. Tested against a stand-in (`tests/test_qbo_sync.py`)
- `qbo.py`: the QuickBooks Online connection: OAuth connect and token refresh, payments proposed with the same matcher (each recorded once, by its QuickBooks id), and a reconciliation report against QuickBooks invoices. Intuit's endpoints and field names are from their documentation as remembered, checked only against a stand-in (`tests/test_qbo.py`) until the first sandbox connection
- `metrics.py`: the Results numbers (a fold on the Dashboard) and the monthly summary email (`python3 admin.py monthly-summaries`)
- `payer_page.py`: the client's page for a pay app (`/p/...`: Mark as received, Approve for payment, Send a question) and the change-order signing page (`/s/...`)
- `mail_events.py`: bounce and spam-complaint webhooks from the mail provider
- `migrations.py`: the company-file schema version and the steps between versions, run on load
- `audit_check.py`: checks every company's audit hash chain (the server runs it daily from its hourly loop and alerts on a broken one; by hand: `python3 audit_check.py`)
- `admin.py`: founder shell commands: list companies, find an email, delete a company on request, end sessions, turn two-step off for a lost phone, the health details; each run is appended to `<data dir>/admin_log.jsonl` (an email as a short hash), and a change alerts `ALERT_WEBHOOK_URL`
- `docs/runbooks.md`: deploy, restore, rotate a secret, delete a company, incident
- `legal.py`, `legal/`: the Terms of Service and Privacy Policy
- `mailer.py`: outgoing email over SMTP
- `demo_seed.py`: the live demo, Redbud Electric Co., built through the domain functions with its PDFs
- `seed.py`: Hartline Remodeling sample data, the tests' fixture
- `server.py`: stdlib HTTP server and cookie sessions. Every URL is one line in its `ROUTES` table, naming the handler and who may call it (public, signed in, or a change to the company's data, which runs under that company's lock and returns the updated state)
- `store.py`: company storage, SQLite or JSON files (see Data)
- `static/`: `index.html`, `styles.css` and the app, an ES module (`app.js`, which holds the views, imports the helpers in `static/js/`: formatting, job status, schedule of values, routes, help text, network and DOM helpers; no bundler). The only files served. The page's Content-Security-Policy allows scripts and styles only from these files: no inline scripts, `onclick=` or `style=""`; server-written pages allow their own `<style>` block by its hash.
- `tests/`: domain, auth and HTTP tests

## Money rules

- Pay apps are net of retainage. With a retainage percent on the job, each pay app withholds that percent and shows it as a line. Held retainage is not counted as unbilled work.
- Every job gets closeout documents at Final pay app. They gate asking for all the retainage, not the pay apps. Finish job warns when they are missing and doesn't stop.
- A new job needs a complete client playbook: name, client type, who signs off, billing email and phone. A saved email, signer or phone can be changed, not cleared. A job made under the rule waits for a complete playbook before a pay app starts or goes out; a job from before it isn't held up, and sending still needs the email.
- Each pay app's supporting documents list comes from the job's own list when it has one (**Supporting documents for this job**), else the client's **Supporting documents needed with each pay app** (Client playbook), plus a conditional waiver; an item named like the pay app, schedule of values or continuation sheet is in from the start.
- An item clears on a real file a person checked: a stored, non-empty file of a type a person can open. A record that fits is only suggested, until someone taps **I checked this file**. `doc_check.py` warns on a blank file, a missing job name, or a waiver's amount or wording; a person clears it with **I opened it and it's right**. A misread never blocks for good. An item in only on a name, from before files were required, counts as missing on a draft.
- **I have this…** is an override with a required reason: who, when and why show on the pay app page and Review & send, and the client's PDF and page say **Not attached**. **Not needed for this client** takes an item off the client's list, or off the job's own; a conditional waiver comes off this job unless **For this whole client** is tapped, and **Needed after all** brings it back. Supporting documents that went with a sent pay app stay as they were sent.
- A send needs a look: Approve & send carries what Review & send showed, and a change since is refused with a request to look again. A bill that stops being the final one under a confirmed waiver waits for another look too.
- A change order approved after the job is marked done becomes billable on the next pay app.
- A payment goes to a pay app the user picks, must be above zero and at most the balance owed, and a reference can be used once per job.
- One waiver per paid pay app and per retainage release (unconditional; final only when the job is finished by it, and a final waiver sent early warns first). The waiver document comes from the contractor; Duracash does not generate it.
- A follow-up before the client's days between follow-ups have passed needs **Follow up now** and a confirm; one sent after a conversation on New job record with a check-again date or the client's **Approve for payment** waits for the date they gave. Each one writes an Outbox row.
- A job closes only when work is done, nothing is drafted, unbilled, outstanding or pending, nothing is overbilled (or a refund or credit was recorded), retainage is released, and waivers are sent. Closed jobs are read-only except for notes.
- A new job for an existing client keeps what the client has and asks only for what it is missing. Change the rest in the Client playbook.
- A sent pay app with nothing paid can be pulled back with a reason (the client is told nothing is due on it); a part-paid one can be settled short with a reason; a finished job can be reopened by an owner with a reason. Each is in the Activity log.
- Every approved send is written to the Outbox and emailed when email is set up (see Sending email).

## API

Auth (cookie session):

- `POST /api/auth/login`: `{email, password}`. After 5 wrong passwords for one email in 15 minutes, returns 429.
- `POST /api/auth/signup`: `{email, password, name, company_name, accept_terms: true}`. Without `accept_terms: true` (the Terms of Service and Privacy Policy) it returns 400.
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/auth/sessions`: your live sessions; `POST /api/auth/sessions/end` `{id?}`: sign out that one, or every other

Company data (requires a session; scoped to the signed-in company). The audit trail always names the signed-in user; an `actor` field in a request body is ignored.

- `GET /api/state`: what the app needs on load: open jobs in full, jobs closed in the last 90 days as summaries, the clients (`payers`), the Dashboard's rows (`today_rows`), the latest 20 emails, and counts (`closed_jobs_total`, `email_log_total`, `audit_total`). It doesn't grow with the company's history.
- `GET /api/jobs?status=open|closed|all&offset=&limit=&q=`: jobs, newest first (closed ones as summaries, by closing date); `limit` up to 200
- `GET /api/jobs/{id}`: one job in full, with its client
- `GET /api/outbox?offset=&limit=&job=` and `GET /api/audit?…`: the Outbox and the audit trail, newest first
- `GET /api/timeline?job=&offset=&limit=`: one job's history for its page: audit rows and emails (with delivery status, without bodies), newest first
- `GET /api/export`: download the company's data file; `GET /api/export/with-files.zip`: the same with every uploaded file
- `GET /api/storage`: the company's file storage used and its cap
- `POST /api/reset`: demo company returns to the Redbud Electric seed (Hartline with `DURACASH_DEMO_SEED=hartline`). Any other company needs `{confirm: "<exact company name>"}` and gets a snapshot first.
- `POST /api/jobs`: create a job. Required: `name`, the address (`street`, `city`, `state`, `zip`), `customer`, `contract_cents`, and for a new client or what a saved one lacks: `client_type`, `approver`, `ap_email`, `phone`. Optional: `approver_confirmed` (the signer was typed, even as the client's own name), `sov_lines` (blank makes one lump-sum line; New job sends an imported contract's lines here, with its total as `contract_cents` when asked, so whoever may create the job sets its contract), `anticipated_end_on`, `retainage_pct` (0–20; blank or left out: the client's Usual retainage %, else 10 for a general contractor, public agency or another subcontractor and 0 for an owner or homeowner), `new_job` (keep it even though an open job has the same name), `note` (kept on the job record as "Job notes"; the old `packet_notes` key is read the same way), `contract_number`. The paperwork list for each pay app comes from the customer's settings, not from the job.
- `GET /api/jobs/{id}/final-preview`: what Final pay app would bill; `POST /api/jobs/{id}/done`: `{notes, period_to}`
- `POST /api/jobs/{id}/progress`: `{completed_cents, notes}` (jobs without a schedule of values)
- `POST /api/jobs` and `/api/jobs/{id}/edit` take `period_end_day` (1 to 31): the day the job's pay period ends, from its contract
- `POST /api/sov/parse`: `{text}` → `{lines, warnings, op_pct}`; reads pasted rows, saves nothing
- `POST /api/jobs/{id}/sov`: `{lines: [{id?, item, description, quantity, unit, rate}], op_pct, update_contract}`
- `POST /api/jobs/{id}/sov-progress`: `{done: {line_id: quantity_to_date}, stored, notes, period_to}`; lump sums take a fraction (0.35 = 35%); a line left out is at its suggestion
- `POST /api/jobs/{id}/sov-amounts`: `{amounts: {line_id: dollars}}`, blank for the suggestion; `/sov-correct`: `{line_id, qty, reason, below_billed}`; `/quantities-undo/{record_id}`: `{below_billed}`
- `POST /api/import/quantities/preview` and `/apply`: `{job_id, name, csv | xlsx}` and, on apply, the preview's `basis`
- `POST /api/outbox/{id}/retry`: send a failed or unsent email again
- `GET /api/jobs/{id}/payapp/{invoice_id}.pdf`: the pay app as a PDF
- `GET /api/jobs/{id}/payapp/{invoice_id}.csv`: a sent pay app in QuickBooks Online's invoice-import layout (one row per line, retainage as a negative line), for **Download for QuickBooks**
- `POST /api/auth/forgot` `{email}`; `POST /api/auth/reset` `{token, password}`; `POST /api/auth/verify` `{token}`; `POST /api/auth/resend-verify`; `POST /api/auth/password` `{current, new}`
- `POST /api/auth/invite-info` `{token}`; `POST /api/auth/accept-invite` `{token, name, password}`
- `POST /api/auth/name` `{name}`: your own name; owners: `POST /api/company` `{name, address, phone, license}` (on every pay app's From block)
- `GET /api/team`; owners: `POST /api/team/invite` `{email, role}`, `/api/team/{user_id}/role` `{role}`, `/api/team/{user_id}/remove`, `/api/team/invites/{id}/revoke`, `/api/account/delete` `{password, confirm}`
- `POST /api/onboarding/dismiss`; team only: `GET /api/admin/companies`
- `GET /healthz`
- `POST /api/jobs/{id}/draft`: returns 409 when there is nothing to draft or a draft is already open
- `POST /api/jobs/{id}/send`: `{reviewed, unconditional_override, soft_checks, today}`; `reviewed` is the draft's `email_preview.stamp`; `soft_checks` `{item_id: "checked" | "not_needed"}` answers the soft gates (refused with `soft_checks` while one is unanswered); `today` is the sender's own date, which the payment terms count from (the due date moves to the next business day)
- `POST /api/jobs/{id}/packet-soft-check/{item_id}`: `{answer: "checked" | "not_needed" | ""}`, a soft gate's answer at Review & send
- `POST /api/jobs/{id}/daily-record`: New job record (`{notes, date, today, weather, materials, sections, entries, over_ok}`); a `time_sheets` entry takes `crew: [{name, hours, member_id}]`, a `conversation` entry `{text, next_on}`; a quantity past what a line has left is refused with `over_schedule` unless `over_ok: true`. `POST /api/jobs/{id}/daily-record-edit/{daily_record_id}`: the same body, to change a saved one (rows saved before carry `record_id`)
- `POST /api/jobs/{id}/dismiss-row`: `{key}`, a Dashboard reminder (`no_record`, `co_no_work`) put away
- `POST /api/jobs/{id}/follow-up`
- `POST /api/jobs/{id}/match-payment`: `{amount_cents, reference, invoice_id, received_on}`. `invoice_id` is required when more than one pay app is open.
- Owners only: `POST /api/jobs/{id}/accept-short-pay/{invoice_id}` `{reason}`, `/void-sent/{invoice_id}` `{reason}`, `/reopen` `{reason}`. Anyone on the team: `/undo-done`, `/note-contact` `{text, next_on}` (older pages; New job record's conversation section does this now), `/request-co-signature/{co_id}`, `/packet-skip/{item_id}`, `/detach/{item_id}`
- `POST /api/auth/demo`: a session in the sample company, when the server has one (`GET /api/auth/demo-available`)
- `POST /api/jobs/{id}/waiver`: `{invoice_id, release_id, kind, confirm_final}`, all optional
- `POST /api/jobs/{id}/retainage`: request retainage release
- `POST /api/jobs/{id}/retainage-paid`: `{amount_cents, reference}`
- `POST /api/jobs/{id}/resolve/{item_id}`: `{reason}` (required): the I have this override; on a hard stop (Required) it takes `send_payapps`, as does `/packet-skip` on one
- `POST /api/jobs/{id}/attach/{item_id}`: `{file_id}`; a name alone is refused
- `POST /api/jobs/{id}/attach-record/{item_id}`: `{record_id, looked}`; `POST /api/jobs/{id}/packet-confirm/{item_id}`: `{looked}`
- `POST /api/jobs/{id}/packet-skip/{item_id}`: `{reason, whole_client}`; `/waiver-needed`; `/paperwork`: `{packet}`, `{no_paperwork: true}` or `{use_client: true}`, and/or `{gates: {key: "hard" | "soft"}}` (the job's hard stops and checks); `/overbilling-settled`: `{reason}`
- `POST /api/jobs/{id}/add-co`: `{title, reason, amount_cents}`
- `POST /api/jobs/{id}/approve-co/{co_id}`
- `POST /api/jobs/{id}/include-co/{co_id}`: put a pending CO on the next pay app; send stays blocked until it is signed
- `POST /api/jobs/{id}/add-evidence`: `{label, kind}`
- `POST /api/jobs/{id}/add-record`: `{label, kind, note}`
- `POST /api/jobs/{id}/daily-record`: `{notes, date, today, sections, entries}`: the New job record form
- `POST /api/jobs/{id}/learn-rejection`: `{reason}`
- `POST /api/jobs/{id}/close`
- `POST /api/jobs/{id}/import-qbo`: demo company only; a hard-coded sample payment
- Billing: `GET /api/billing`; owners: `POST /api/billing/checkout` and `/api/billing/portal` (each returns a Stripe address); `POST /api/stripe/webhook` (Stripe-Signature checked)
- QuickBooks: `GET /api/qbo/status`; owners: `POST /api/qbo/connect` (returns Intuit's sign-in address) and `/api/qbo/disconnect`; `GET /api/qbo/callback` (Intuit sends the owner back here); `POST /api/qbo/payments` `{days}` (proposals, records nothing); `GET /api/qbo/reconcile`
- `POST /api/payers` `{name, client_type, approver, approver_confirmed, ap_email, phone}`: add a client; `POST /api/payers/{id}/missing` `{client_type, approver, approver_confirmed, ap_email, phone}`: fill only what the client has blank, from anyone on the team; `POST /api/payers/{id}/update` (needs `company`, as do merge, delete, waiver-needed and answering a suggestion): client playbook fields (follow-up and approval days 1–365, due day 1–31); `POST /api/payers/{id}/merge` `{into}`
- `POST /api/jobs/{id}/pencil`: send the open draft for the client's review; `POST /api/jobs/{id}/pencil-response`: `{agreed, note}`
- `POST /api/jobs/{id}/void-draft`; `POST /api/jobs/{id}/withdraw-co/{co_id}`
- `POST /api/import/file?job=<id>&file=1`: raw file body, `X-Filename` header (URL-encoded). Without `file=1` it goes to the Inbox.
- `POST /api/import/text?file=1`: `{text, source: email|sms|notes|paste, job_id}`
- `POST /api/inbox/{item_id}/file` `{job_id, kind}`, `/dismiss`, `/undo`
- `GET /api/import/settings`: the company's inbound and calendar addresses; `POST /api/import/rotate` `{kind}` makes a new one
- `GET /api/files/{file_id}`
- `POST /api/auth/sms` `{phone, sms_opt_in}`, `/api/auth/sms/confirm` `{code}`; `GET /api/reminders`, `POST /api/reminders/prefs` `{prefs}`, `/api/reminders/read` `{id}`
- `POST /api/helper/ask` `{q, view, job_id}`; `POST /api/helper/feedback` `{log_id, helpful, note}`
- Team only: `GET /api/helper/review`, `POST /api/helper/answers`, `POST /api/helper/resolve`

Gates return 409 with a list of blockers. Bad input returns 400 with a sentence. Unauthenticated calls return 401. Server errors return a JSON 500 and are logged.

## Optional: Docker

```bash
docker build -t duracash .
docker run -p 8765:8765 duracash
```

Local `python3 server.py` is the primary path. No external services or API keys are required; email, texting and inbound email are off until their settings are filled in.
