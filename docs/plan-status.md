# Where the plans stand (Oct 29, 2026)

Two plans. The full-scope build plan of Oct 12 (P0–P3), with the UI/UX plan (Options A–C) and
the retest's open rows, was built by Oct 13 (pull request #2). The improvement plan of Oct 27
(`docs/improvement-plan.md`, six rounds to make Duracash easy for a contractor to start and use
alone) was built on `dev` by Oct 29; its section below says what changed and what is left.
"Built" means in the code with tests; nothing here is live until it is merged to `main` and
deployed. The product words below are the app's own (customer, paperwork, customer settings);
the plan rows keep the words they were written with.

**In short: the code is complete for everything that code alone can do.** What remains needs
an account, a decision or a person: the Render disk, the Intuit and Stripe keys, a Backblaze
bucket, an alert channel, a lawyer, and the buyer interviews.

## The improvement plan of Oct 27 (six rounds, built Oct 27–29)

Each item's line is in the Progress section of `docs/improvement-plan.md`; the flows are in
`docs/client-flows.md` and the screens in `docs/wireframes/`. What changed for a contractor:

- **First run.** Sign-up asks for name, email and password (the company name can wait); a "Check
  your email" panel says where the link went; Today's setup checklist leads to the company details,
  the first job, its paperwork and the first send. **Look around a sample company** opens the demo
  with no account. New job is five boxes, with the rest under More details.
- **Forms.** A wrong box turns red in place with the reason, and nothing typed is lost. Money boxes
  take "$48,500". Every label has an (i) with the trade term. Plain words everywhere (customer,
  paperwork, Customer settings, held back, Record a payment, Finish job), and "draw" for companies
  that bill homeowners in draws.
- **The job page.** One Next step with the next two beside it and the rest under More; the progress
  card bills a draw, a total or a percent with the figures shown; change orders, credits and their
  signatures on the page; paperwork by camera from a phone; a list of what stands before Finish job.
- **The money loop.** Every send goes through Before you send. Payments carry their date; a short
  pay can be accepted; a sent pay app can be pulled back; a finished job can be reopened (owners).
  Pay apps carry Period to, a payment-due date and an AP-friendly subject, and download for
  QuickBooks. Follow-ups name every unpaid pay app and get firmer; a call can stand for one. The
  customer can approve a pay app for payment with a date. Owners get a daily email of what is due.
  Only owners send unless the company lets members send.
- **Import and the Inbox.** Waiting first, one tap to add an item to its suggested job, photos
  that come in with a job, pastes read as the email, text thread or notes they are, a dropped
  payments file read as payments, and a payments import that finishes (pick a pay app, split a
  payment that is too much).
- **Phone.** Tables fit the screen, schedules scroll in their own box, Take a photo opens the
  camera, Money has six tabs, and Settings' long account page is now Company, Team, My account and Billing.
- **Reliability and security.** One save path for every change; a server that runs without root;
  backups that also hold the deleted-company list, QuickBooks connections and the email schedules,
  so a restore brings back a company deleted after the backup, ready to use; a missing company never served blank;
  a disk check on `/healthz`; inbound email addresses that check who posts and are rate-limited;
  sign-up limited per network; a two-step account's password reset still asks for the code.

Left, and why (details in the Progress lines):

- R1-03: the views still live in `static/app.js` (4,300 lines); the action map, partial redraw and
  shared state are in, and views move out a few at a time as they are touched.
- R5-10: the real QuickBooks export files and JSON need someone signed in to an Intuit sandbox
  company; the tests read them from `testdata/quickbooks/` as soon as they land.
- R6-06: local backups still hold every upload (`BACKUP_UPLOADS=0` leaves them out and relies on the
  off-site copy), because a backup that holds only new files would not restore on its own.
- R6-08: the rest of `domain/__init__.py` moves out a module at a time, each its own commit, with no
  change a contractor sees.
- Deliberately not done now (plan section 5): a provisioned inbound address per company, a waiver
  fill sheet and user-entered lien dates, a change-order PDF, AR aging, a daily QuickBooks read, the
  escape-by-default template, and removing the old-name shims.

## P0: before any real contractor data

| # | Item | Code | Still needed to be live |
|---|---|---|---|
| P0-1 | Persistent disk on Render | Built: `render.yaml` (Docker, starter plan, 10 GB disk at /var/data) | Stephen: attach the disk or switch the service to the blueprint; deploy twice and check `/healthz`. Unchecked from here: the live site didn't answer within 60 s today |
| P0-2 | Off-site encrypted backup | Built: `offsite.py` (age, then S3 API to B2) | A B2 bucket with Object Lock, a write-only key, an age key pair |
| P0-3 | CI on every push | Built and running: unit tests on SQLite and JSON, 13-step browser test | Stephen: protect `main`, require `unittest` and `smoke` |
| P0-4 | Production flags | Built (`DURACASH_ENV=production`) | Set on Render with `DURACASH_URL` |
| P0-5 | Error alerts and uptime | Built: `alerts.py` (crash and backup alerts to a webhook), `/healthz` | A webhook (Slack or Better Stack) and an uptime monitor |
| P0-6 | "Not legal deadlines" label | Built | — |
| P0-7 | Session tokens hashed at rest | Built | — |
| P0-8 | "Data may be erased" notice | Built | Set `DURACASH_DATA_PERSISTENT=1` once the disk is checked |

## P1: the pilot runs without a founder in the loop

All fifteen are built: page URLs, job edit and delete, payer replies filed to the job, bounce
webhooks, stored materials and per-line retainage, waiver types, "Before you send", a fair
sign-in throttle, gzip and cache headers, the browser test in CI, the hash-chained audit, schema
migrations, the staging service (in `render.yaml`; it exists once the blueprint is applied),
runbooks, and PBKDF2 at 600,000 rounds.

Still needed: a mail provider and a verified domain for real email; the bounce webhook formats
checked against the chosen provider.

## P2: ready for paying customers and for QuickBooks

| # | Item | Code | Still needed to be live |
|---|---|---|---|
| P2-1 | QuickBooks read-only import, CSV fallback, reconciliation, idempotent matching | Built: `qbo_csv.py` (CSV), `qbo.py` (OAuth, payments keyed on the QuickBooks id, reconciliation) | Intuit Developer app keys; the first sandbox connection checks Intuit's field names |
| P2-2 | Stripe billing, portal, webhooks, read-only when past due | Built: `billing.py` (Checkout, Customer Portal, signed webhook, trial carried over, read-only never deletion with `BILLING_ENFORCE=1`) | Stripe account (after the entity); the first test-mode checkout checks Stripe's field names |
| P2-3 | Change-order signature | Built (signing link, or a signed copy on the record) | Counsel to check the consent wording (`domain.CO_CONSENT`) |
| P2-4 | Payer-facing page | Built: the customer's page, with Mark as received, Approve for payment and Send a question | — |
| P2-5 | SQLite storage | Built (`store.py`; JSON import on first start; `DURACASH_STORAGE=json` keeps files) | Back up before the first start of this code |
| P2-6 | Per-view endpoints | Built | — |
| P2-7 | Route table | Built | — |
| P2-8 | ES modules, no inline onclick, strict CSP | Built | — |
| P2-9 | Two-step sign-in | Built | — |
| P2-10 | Metrics and monthly summary | Built (Results tab, `admin.py monthly-summaries`) | A monthly cron on Render once email works |

## P3: after product-market signal

| Item | Status |
|---|---|
| Mobile capture (PWA, camera, Twilio media) | Built: installable app, share target, "take a photo", texted photos downloaded |
| "Job's done" by text or email | Built |
| Payer playbook learning | Built: Customer settings suggests paperwork items from pay apps sent back and drafts the customer wanted changed |
| AI: classify documents, extract amounts and dates | **Not built, on purpose.** Needs an API key and the founders' go-ahead. Rule to keep when it is built: the model proposes and never writes a money field; every proposal goes through the same approval as a person's |
| Lien and prompt-pay dates for one state | **Not built, on purpose.** Needs counsel to review the table first |
| Postgres and a second instance | **Not needed.** Measured on Oct 13 (`docs/benchmark.md`): far beyond a pilot on one box |

## Stephen's 15 features (from his messages and the Oct 12 calls)

| # | Feature | Status |
|---|---|---|
| 1 | Job record | Built: manual entry, edit and delete, and the Inbox (files, emails, texts, notes, photos) |
| 2 | User / client profile | Built: your own name; the company's name, address, phone and licence on every pay app; customers in Customer settings |
| 3 | Daily records: manual, mass import, AI | Built except AI: manual, and pasted notes split into one record per date |
| 4 | Packet check | Built: each pay app's paperwork list, from Customer settings, which learns from pay apps sent back |
| 5 | Invoice draft; unsigned CO refused unless flagged; upload and AI-verify | Built except the AI check: draft with retainage, unsigned COs block send, uploads on the record |
| 6 | Approval and send | Built (needs a mail provider to send for real) |
| 7 | Status that names the blocker | Built |
| 8 | Payment match from QuickBooks; partial stays partial | Built: by hand, CSV, or the QuickBooks connection (needs the Intuit keys) |
| 9 | Waiver guard after payment | Built, with waiver types; Duracash never writes the waiver form |
| 10 | Follow-up by the payer's window | Built: one email for every unpaid pay app, firmer as it goes on, on Today in one tap, and in the owners' daily email |
| 11 | Change order to billable; signature | Built: signing link or signed copy |
| 12 | Payer playbook read by packet and follow-up | Built (Customer settings), and it learns |
| 13 | Evidence binder | Built |
| 14 | Retainage and closeout | Built |
| 15 | Deadline calendar | Built for contract dates: follow-ups, approvals, the customer's monthly pay-app cutoff (Customer settings), with a Google Calendar feed. Lien and prompt-pay deadlines not built: they are state law and need counsel |

From the calls, not built: voice follow-up agents (deferred until counsel; consent rules), AI
filing (shelved for cost, as both founders agreed on the call). Decisions still open: 50/50 terms,
the free period, competitor sizes.

## The business plan's billing rules

- Trial: one paid pay app or 90 days (120 if a sent pay app is unpaid at day 90); 45 days or the
  first paid draw for companies that bill homeowners in draws. Built (`trial.py`).
- Charge after the cutoff, on the 5th; prorate the first month. Built (`billing.py`, `BILLING_DAY`).
- Up to 3 design partners at $99 a month. Built (`STRIPE_PARTNER_PRICE_ID`, `admin.py partner`).
- Activation measured as days from signup to the first paid pay app. Built (founders' Companies page).
- Annual plan after the first paid cycle, priced as 10 months: a second Stripe price offered in the
  Customer Portal; no code needed. The money-back promise is a founders' process.

## UI/UX plan

- Option A (A1–A8): built.
- Option B: built. Today is one action list with one-tap actions, each job has a history, mobile
  capture, and the customer's page.
- Option C (a framework redesign): not now, as the plan says.

## Retest rows that were still open

- TE-2 "no restore drill": `python3 backup.py drill`, monthly in runbook 2.
- TE-5 "demo tenant in production": the demo is off in production, or read-only with
  `DURACASH_DEMO=readonly`.
- TE-6 tamper-evident audit, TE-7 idempotent matching, TE-10 CI, LC-2 calendar label, LC-4 send
  confirmation, LC-5 CO signature: built (see above).
- No pen-test checklist yet; the security work so far is the review findings fixed with tests.

## Decisions that are the founders'

Host and disk; Stripe, Intuit and Backblaze accounts (and whether before the entity); whether the
live site runs the read-only demo; when to turn on `BILLING_ENFORCE`; the segment and state;
whether to start the AI work.
