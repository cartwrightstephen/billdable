"""The company's financial picture, as a bank asks for it (Stephen, Nov 13: Money > Financials).

Every figure comes from what the app already records; none is estimated:

  - receivables: what clients owe on pay apps sent, aged by days since sending
  - retainage held and not yet released
  - backlog: signed contract value (with signed change orders) not done yet, on open jobs
  - work done and not billed yet, and work billed ahead of the contract
  - billed and collected, month by month
  - days to get paid
  - how much of what is owed, and of what was billed, sits with the largest clients
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any

import domain
import metrics
from exports import text

BILLED = domain.BILLED_STATUSES
BUCKETS = [("current", "0–30 days", 0, 30), ("31_60", "31–60 days", 31, 60), ("61_90", "61–90 days", 61, 90), ("over_90", "Over 90 days", 91, None)]
MONTHS = 12


def _day(iso: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(iso)).date()
    except (TypeError, ValueError):
        return None


def _bucket(days: int) -> str:
    for key, _, lo, hi in BUCKETS:
        if days >= lo and (hi is None or days <= hi):
            return key
    return "current"


def _month_keys(today: date, n: int = MONTHS) -> list[str]:
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def _job_aging(job: dict[str, Any], owed: int, today: date) -> list[tuple[str, int, int]]:
    """What is still owed on each pay app sent: (invoice number, days since sent, cents).

    Payments recorded against a pay app come off it; payments recorded without one come off the
    oldest first. Whatever the job owes past that (a refund owed back, say) is listed as "Other",
    so the aging always adds up to what the job shows as outstanding."""
    sent = sorted((i for i in job.get("invoices", []) if i.get("status") in BILLED), key=lambda i: str(i.get("sent_at") or ""))
    rows = []
    for inv in sent:
        paid = sum(p["amount_cents"] for p in job.get("payments", []) if p.get("invoice_id") == inv["id"])
        left = inv["amount_cents"] - paid - int(inv.get("short_pay_cents") or 0)
        d = _day(inv.get("sent_at"))
        rows.append([inv.get("number", ""), (today - d).days if d else 0, max(0, left)])
    loose = sum(p["amount_cents"] for p in job.get("payments", []) if not p.get("invoice_id"))
    for r in rows:
        take = min(loose, r[2])
        r[2] -= take
        loose -= take
    rows = [(n, max(0, d), c) for n, d, c in rows if c > 0]
    aged = sum(c for _, _, c in rows)
    if owed > aged:
        rows.append(("Other", 0, owed - aged))
    elif aged > owed:
        # More came in than the pay apps show as paid (paid back, settled): trim the newest first.
        extra = aged - owed
        trimmed = []
        for n, d, c in sorted(rows, key=lambda r: r[1]):
            take = min(extra, c)
            extra -= take
            if c - take:
                trimmed.append((n, d, c - take))
        rows = trimmed
    return rows


def compute(state: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    payers = {p["id"]: p.get("name", "") for p in state.get("payers", [])}
    months = _month_keys(today)
    year_ago = today - timedelta(days=365)
    first_month = date(int(months[0][:4]), int(months[0][5:]), 1)
    monthly = {k: {"month": k, "billed_cents": 0, "collected_cents": 0} for k in months}
    aging = {k: 0 for k, *_ in BUCKETS}
    by_client: dict[str, dict[str, Any]] = {}
    aging_rows = []
    backlog = unbilled = overbilled = pending_co = retainage = open_contract = unpriced_cos = 0
    open_jobs = completed_12 = 0
    backlog_jobs = []

    def client(job: dict[str, Any]) -> dict[str, Any]:
        name = payers.get(job.get("payer_id")) or "No client"
        return by_client.setdefault(name, {"client": name, "owed_cents": 0, "retainage_cents": 0, "billed_12m_cents": 0})

    for job in state.get("jobs", []):
        m = domain.job_money(job)
        c = client(job)
        owed = m["outstanding_cents"]
        for number, days, cents in _job_aging(job, owed, today):
            aging[_bucket(days)] += cents
            aging_rows.append({"job": job["name"], "job_id": job["id"], "client": c["client"], "pay_app": number, "days": days, "cents": cents})
        c["owed_cents"] += owed
        c["retainage_cents"] += m["retainage_open_cents"]
        retainage += m["retainage_open_cents"]
        done = _day(job.get("completed_at"))
        if done and done > year_ago:
            completed_12 += 1
        if not job.get("closed"):
            open_jobs += 1
            open_contract += m["contract_total_cents"]
            # Backlog is signed work not done yet: what is left to bill, less what is done and waiting to be billed.
            left = domain.backlog_cents(m)
            backlog += left
            if left or m["unbilled_completed_cents"]:
                backlog_jobs.append({"job": job["name"], "job_id": job["id"], "client": c["client"], "contract_cents": m["contract_total_cents"],
                                     "billed_cents": m["billed_gross_cents"], "unbilled_cents": m["unbilled_completed_cents"], "left_cents": left})
            unbilled += m["unbilled_completed_cents"]
            overbilled += m["overbilled_cents"]
            pending_co += m["pending_co_cents"]
            # Round 2 decision 2: a change order saved before it has a price counts nowhere in the
            # money, so it is listed by number rather than as $0.
            unpriced_cos += sum(1 for co in job.get("change_orders", []) if co.get("status") == "pending" and co.get("unpriced"))
        for inv in job.get("invoices", []):
            d = _day(inv.get("sent_at"))
            if inv.get("status") in BILLED and d and d >= first_month:
                gross = int(inv.get("gross_cents", inv["amount_cents"] + inv.get("retainage_cents", 0)))
                key = f"{d.year:04d}-{d.month:02d}"
                if key in monthly:
                    monthly[key]["billed_cents"] += gross
                    c["billed_12m_cents"] += gross
        # Collected is money that stayed: payments and retainage released, less refunds, each in
        # the month it happened, as the payments CSV adds up (B1, B11).
        moves = [(p.get("matched_at"), p["amount_cents"]) for p in job.get("payments", [])]
        moves += [(p.get("at"), int(p.get("amount_cents") or 0)) for p in job.get("retainage_payments", [])]
        # Only money that came in can go back, as job_money caps it.
        came_in = sum(cents for _at, cents in moves)
        for s in job.get("overbilling_settled") or []:
            back = max(0, min(int(s.get("returned_cents", s.get("cents") or 0)), came_in))
            came_in -= back
            moves.append((s.get("at"), -back))
        for at, cents in moves:
            d = _day(at)
            key = d and f"{d.year:04d}-{d.month:02d}"
            if key in monthly:
                monthly[key]["collected_cents"] += cents

    receivable = sum(aging.values())
    billed_12 = sum(r["billed_cents"] for r in monthly.values())
    collected_12 = sum(r["collected_cents"] for r in monthly.values())
    clients = [r for r in by_client.values() if r["owed_cents"] or r["retainage_cents"] or r["billed_12m_cents"]]
    for r in clients:
        r["owed_share"] = round(100 * (r["owed_cents"] + r["retainage_cents"]) / (receivable + retainage), 1) if receivable + retainage else None
        r["billed_share"] = round(100 * r["billed_12m_cents"] / billed_12, 1) if billed_12 else None
    clients.sort(key=lambda r: (-(r["owed_cents"] + r["retainage_cents"]), -r["billed_12m_cents"], r["client"]))
    paid = metrics.compute(state, today=today, since=year_ago)
    company = state.get("company", {})
    return {
        "as_of": today.isoformat(),
        "company": company.get("name", ""),
        "receivable_cents": receivable,
        "aging": [{"key": k, "label": label, "cents": aging[k]} for k, label, *_ in BUCKETS],
        "aging_rows": sorted(aging_rows, key=lambda r: -r["days"]),
        "retainage_cents": retainage,
        "backlog_cents": backlog,
        "backlog_jobs": sorted(backlog_jobs, key=lambda r: (-r["left_cents"], -r["unbilled_cents"])),
        "unbilled_cents": unbilled,
        "overbilled_cents": overbilled,
        "pending_co_cents": pending_co,
        "unpriced_cos": unpriced_cos,
        "open_jobs": open_jobs,
        "open_contract_cents": open_contract,
        "completed_jobs_12m": completed_12,
        "monthly": list(monthly.values()),
        "billed_12m_cents": billed_12,
        "collected_12m_cents": collected_12,
        "paid_median_days": paid["sent_to_paid_median_days"],
        "paid_average_days": paid["sent_to_paid_average_days"],
        "paid_count": paid["pay_apps_paid"],
        "clients": clients,
        "top_client_share": clients[0]["owed_share"] if clients and clients[0]["owed_share"] is not None else None,
    }


def to_csv(f: dict[str, Any]) -> str:
    """The same figures as one CSV file, a section at a time, for a bank or an accountant."""
    out = io.StringIO()
    w = csv.writer(out)
    d = lambda cents: f"{cents / 100:.2f}"
    # Names are typed by people: each goes as text a spreadsheet won't run as a formula (B3).
    w.writerow([text(f"{f['company']} financial summary"), f"as of {f['as_of']}"])
    w.writerow([])
    w.writerow(["Summary", "Amount"])
    for label, key in [("Owed by clients (receivables)", "receivable_cents"), ("Retainage held", "retainage_cents"), ("Backlog (signed work not done yet)", "backlog_cents"),
                       ("Done, not billed", "unbilled_cents"), ("Billed ahead of the contract", "overbilled_cents"), ("Change orders waiting for signature", "pending_co_cents"),
                       ("Open contract value", "open_contract_cents"), ("Billed, last 12 months", "billed_12m_cents"), ("Collected, last 12 months", "collected_12m_cents")]:
        w.writerow([label, d(f[key])])
    w.writerow(["Change orders without a price yet", f["unpriced_cos"]])
    w.writerow(["Open jobs", f["open_jobs"]])
    w.writerow(["Jobs completed, last 12 months", f["completed_jobs_12m"]])
    w.writerow(["Days from sent to paid, median", "" if f["paid_median_days"] is None else f["paid_median_days"]])
    w.writerow([])
    w.writerow(["Receivables aging", "Amount"])
    for a in f["aging"]:
        w.writerow([a["label"], d(a["cents"])])
    w.writerow([])
    w.writerow(["Job", "Client", "Pay app", "Days since sent", "Owed"])
    for r in f["aging_rows"]:
        w.writerow([text(r["job"]), text(r["client"]), text(r["pay_app"]), r["days"], d(r["cents"])])
    w.writerow([])
    w.writerow(["Month", "Billed", "Collected"])
    for r in f["monthly"]:
        w.writerow([r["month"], d(r["billed_cents"]), d(r["collected_cents"])])
    w.writerow([])
    w.writerow(["Client", "Owed", "Retainage held", "Share of receivables %", "Billed, last 12 months", "Share of billings %"])
    for r in f["clients"]:
        w.writerow([text(r["client"]), d(r["owed_cents"]), d(r["retainage_cents"]), "" if r["owed_share"] is None else r["owed_share"],
                    d(r["billed_12m_cents"]), "" if r["billed_share"] is None else r["billed_share"]])
    w.writerow([])
    w.writerow(["Backlog by job", "Client", "Contract with change orders", "Billed", "Done, not billed", "Work not done yet"])
    for r in f["backlog_jobs"]:
        w.writerow([text(r["job"]), text(r["client"]), d(r["contract_cents"]), d(r["billed_cents"]), d(r["unbilled_cents"]), d(r["left_cents"])])
    return out.getvalue()
