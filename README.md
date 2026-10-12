# Billdable

AI-assisted get-paid tool for small general contractors and remodelers (2–12 people, roughly $750k–$2.5M). Sits on top of QuickBooks; does not replace Jobber, Housecall Pro, or the books.

## Phase 1 readiness

This build supports the full core loop on the sample company with explicit human gates:

- Job’s done intake (notes + evidence binder)
- Same-week pay-app draft with packet completeness check
- Lien-waiver guard and change-order into the bill
- Approve-and-send (logged to Outbox; real delivery stubbed)
- Payment match (manual or sample QBO import)
- Retainage/closeout chase
- Payer playbook edits that persist, plus “teach playbook” from a rejection
- Job history, Calendar (derived from playbook cadence + dates), Audit, and Outbox

Domain tests stay green. Customer source data stays external; Billdable stores operating context.

## Run locally

```bash
cd billdable-web
python3 server.py
```

Then open http://127.0.0.1:8765

The public landing page is the front door. Create a company there, or sign in.

- Demo: `owner@hartline.example` / `demo` (Hartline Remodeling sample data)
- Create account: a blank company space. Add jobs from the Jobs tab; they persist with that company.

Each company is a separate JSON file under `data/companies/`. Users and sessions live in `data/auth.json`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Project layout

- `domain.py` — money rules, packet gates, status labeling, audit, playbook, evidence, email log
- `auth.py` — users, password hashes, session tokens
- `seed.py` — Hartline Remodeling sample data
- `server.py` — stdlib HTTP server, cookie sessions, per-company JSON
- `static/` — frontend matching the warm paper/copper prototype
- `tests/` — domain tests (money math + send gates + new playbook/CO/email coverage)

## API

Auth (cookie session):

- `POST /api/auth/login` — `{email, password}`
- `POST /api/auth/signup` — `{email, password, name, company_name}`
- `POST /api/auth/logout`
- `GET /api/auth/me`

Company data (requires session; scoped to the signed-in company):

- `GET /api/state` — full snapshot
- `POST /api/reset` — restore Hartline seed for the demo company, empty state for others
- `POST /api/jobs` — create a job `{name, customer, address, contract_cents}`
- `POST /api/jobs/{id}/done` — body `{notes, evidence}`
- `POST /api/jobs/{id}/draft` / `send` / `follow-up` / `match-payment` / `waiver` / `retainage`
- `POST /api/jobs/{id}/resolve/{item_id}`
- `POST /api/jobs/{id}/add-co` — `{title, amount_cents}`
- `POST /api/jobs/{id}/approve-co/{co_id}`
- `POST /api/jobs/{id}/add-evidence` — `{label, kind}`
- `POST /api/jobs/{id}/learn-rejection` — `{reason}`
- `POST /api/jobs/{id}/import-qbo` — sample QuickBooks payment read
- `POST /api/payers/{id}/update` — playbook fields

Gates return 409 with specific blocker list. Unauthenticated API calls return 401.

## Pricing context

Starter $149/mo (<$750k) · default $299/mo ($750k–$2.5M, $249 annual) · Crew $499/mo · first month $99 · unlimited users · no % of collections.

## Optional: Docker

```bash
docker build -t billdable .
docker run -p 8765:8765 billdable
```

Local `python3 server.py` is the primary path. No external services or API keys required.

## Non-goals

Real QuickBooks OAuth, live email delivery, full lien filing, estimating, payroll, or autonomous money/lien actions. Adapters are usable local stubs. Auth is cookie-based and single-user-per-company for now; roles and invites come later.