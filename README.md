# Billdable

AI-assisted get-paid tool for small general contractors and remodelers (2–12 people, roughly $750k–$2.5M). Sits on top of QuickBooks; does not replace Jobber, Housecall Pro, or the books.

## Project layout

- `domain.py` — money rules, packet gates, status labeling, audit, playbook, evidence, email log
- `seed.py` — Hartline Remodeling sample data
- `server.py` — stdlib HTTP server + JSON API
- `static/` — frontend matching the warm paper/copper prototype
- `tests/` — domain tests (money math + send gates + new playbook/CO/email coverage)

## API

- `GET /api/state` — full snapshot
- `POST /api/reset`
- `POST /api/jobs/{id}/done` — body `{notes, evidence}`
- `POST /api/jobs/{id}/draft` / `send` / `follow-up` / `match-payment` / `waiver` / `retainage`
- `POST /api/jobs/{id}/resolve/{item_id}`
- `POST /api/jobs/{id}/add-co` — `{title, amount_cents}`
- `POST /api/jobs/{id}/approve-co/{co_id}`
- `POST /api/jobs/{id}/add-evidence` — `{label, kind}`
- `POST /api/jobs/{id}/learn-rejection` — `{reason}`
- `POST /api/jobs/{id}/import-qbo` — sample QuickBooks payment read
- `POST /api/payers/{id}/update` — playbook fields

Gates return 409 with specific blocker list.

## Pricing context

## Optional: Docker

```bash
docker build -t billdable .
docker run -p 8765:8765 billdable
```

