# Where the improvement plan stands (Oct 13, 2026)

The plan is the full-scope build plan of Oct 12 (P0–P3), with the UI/UX plan (Options A–C) and
the retest's open rows. "Built" means in this branch (`ui-option-a-and-p1`, pull request #2),
with tests; nothing here is live until it is merged and deployed.

**In short: the code is complete for everything that code alone can do.** What remains needs
an account, a decision or a person: the Render disk, the Intuit and Stripe keys, a Backblaze
bucket, an alert channel, a lawyer, and the buyer interviews.

## P0: before any real contractor data

| # | Item | Code | Still needed to be live |
|---|---|---|---|
| P0-1 | Persistent disk on Render | Built: `render.yaml` (Docker, starter plan, 1 GB disk at /var/data) | Stephen: attach the disk or switch the service to the blueprint; deploy twice and check `/healthz`. Unchecked from here: the live site didn't answer within 60 s today |
| P0-2 | Off-site encrypted backup | Built: `offsite.py` (age, then S3 API to B2) | A B2 bucket with Object Lock, a write-only key, an age key pair |
| P0-3 | CI on every push | Built and running: unit tests on SQLite and JSON, 13-step browser test | Stephen: protect `main`, require `unittest` and `smoke` |
| P0-4 | Production flags | Built (`BILLDABLE_ENV=production`) | Set on Render with `BILLDABLE_URL` |
| P0-5 | Error alerts and uptime | Built: `alerts.py` (crash and backup alerts to a webhook), `/healthz` | A webhook (Slack or Better Stack) and an uptime monitor |
| P0-6 | "Not legal deadlines" label | Built | — |
| P0-7 | Session tokens hashed at rest | Built | — |
| P0-8 | "Data may be erased" notice | Built | Set `BILLDABLE_DATA_PERSISTENT=1` once the disk is checked |

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
| P2-4 | Payer-facing page | Built | — |
| P2-5 | SQLite storage | Built (`store.py`; JSON import on first start; `BILLDABLE_STORAGE=json` keeps files) | Back up before the first start of this code |
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
| Payer playbook learning | Built |
| AI: classify documents, extract amounts and dates | **Not built, on purpose.** Needs an API key and the founders' go-ahead. Rule to keep when it is built: the model proposes and never writes a money field; every proposal goes through the same approval as a person's |
| Lien and prompt-pay dates for one state | **Not built, on purpose.** Needs counsel to review the table first |
| Postgres and a second instance | **Not needed.** Measured on Oct 13 (`docs/benchmark.md`): far beyond a pilot on one box |

## Stephen's 15 features (from his messages and the Oct 12 calls)

| # | Feature | Status |
|---|---|---|
| 1 | Job record | Built: manual entry, edit and delete, and the Inbox (files, emails, texts, notes, photos) |
| 2 | User / client profile | Built: your own name; the company's name, address, phone and licence on every pay app; payers in the playbook |
| 3 | Daily records: manual, mass import, AI | Built except AI: manual, and pasted notes split into one record per date |
| 4 | Packet check | Built, from the payer's playbook, which learns from rejections |
| 5 | Invoice draft; unsigned CO refused unless flagged; upload and AI-verify | Built except the AI check: draft with retainage, unsigned COs block send, uploads on the record |
| 6 | Approval and send | Built (needs a mail provider to send for real) |
| 7 | Status that names the blocker | Built |
| 8 | Payment match from QuickBooks; partial stays partial | Built: by hand, CSV, or the QuickBooks connection (needs the Intuit keys) |
| 9 | Waiver guard after payment | Built, with waiver types; Billdable never writes the waiver form |
| 10 | Follow-up by the payer's window | Built, and on Today in one tap |
| 11 | Change order to billable; signature | Built: signing link or signed copy |
| 12 | Payer playbook read by packet and follow-up | Built, and it learns |
| 13 | Evidence binder | Built |
| 14 | Retainage and closeout | Built |
| 15 | Deadline calendar | Built for contract dates: follow-ups, approvals, the payer's monthly pay-app cutoff (playbook), with a Google Calendar feed. Lien and prompt-pay deadlines not built: they are state law and need counsel |

From the calls, not built: voice follow-up agents (deferred until counsel; consent rules), AI
filing (shelved for cost, as both founders agreed on the call). Decisions still open: 50/50 terms,
the free period, competitor sizes.

## UI/UX plan

- Option A (A1–A8): built.
- Option B: built. Today is one action list with one-tap actions, each job has a history, mobile
  capture, and the payer page.
- Option C (a framework redesign): not now, as the plan says.

## Retest rows that were still open

- TE-2 "no restore drill": `python3 backup.py drill`, monthly in runbook 2.
- TE-5 "demo tenant in production": the demo is off in production, or read-only with
  `BILLDABLE_DEMO=readonly`.
- TE-6 tamper-evident audit, TE-7 idempotent matching, TE-10 CI, LC-2 calendar label, LC-4 send
  confirmation, LC-5 CO signature: built (see above).
- No pen-test checklist yet; the security work so far is the review findings fixed with tests.

## Decisions that are the founders'

Host and disk; Stripe, Intuit and Backblaze accounts (and whether before the entity); whether the
live site runs the read-only demo; when to turn on `BILLING_ENFORCE`; the segment and state;
whether to start the AI work.
