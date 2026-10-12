# Billdable

AI-assisted get-paid tool for small general contractors and remodelers (2–12 people, roughly $750k–$2.5M). Sits on top of QuickBooks; does not replace Jobber, Housecall Pro, or the books.

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
- `POST /api/jobs/{id}/progress` — body `{completed_cents, notes}`
- `POST /api/jobs/{id}/draft` / `send` / `follow-up` / `match-payment` / `waiver` / `retainage`
- `POST /api/jobs/{id}/resolve/{item_id}`
- `POST /api/jobs/{id}/add-co` — `{title, amount_cents}`
- `POST /api/jobs/{id}/approve-co/{co_id}`
- `POST /api/jobs/{id}/add-evidence` — `{label, kind}`
- `POST /api/jobs/{id}/add-record` — `{label, kind, note}`
- `POST /api/jobs/{id}/attach-record/{item_id}` — `{record_id}`
- `POST /api/jobs/{id}/learn-rejection` — `{reason}`
- `POST /api/jobs/{id}/import-qbo` — sample QuickBooks payment read
- `POST /api/payers/{id}/update` — playbook fields

Gates return 409 with specific blocker list. Unauthenticated API calls return 401.

## Pricing context

## Optional: Docker

```bash
docker build -t billdable .
docker run -p 8765:8765 billdable
```

Local `python3 server.py` is the primary path. No external services or API keys required.
