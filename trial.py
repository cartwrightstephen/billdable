"""The free trial, timed to the payment cycle (business plan, Oct 12).

A contractor pays nothing until the first pay app sent through Duracash is
paid in full, or for 90 days, whichever comes first. If a pay app has been
sent but is still unpaid on day 90, the trial runs to day 120. A remodeler who
bills homeowners in draws (company billing_style "draws") gets 45 days or the
first paid draw, with no extension.

This module only works out where a company stands; billing.py charges for it (Stripe) and,
with BILLING_ENFORCE=1, holds a company read-only after the trial and a grace period.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

TRIAL_DAYS = 90
EXTENDED_DAYS = 120
DRAW_DAYS = 45
OPEN = {"sent", "approved", "partial"}


def _when(text: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def start(company: dict[str, Any], now: datetime | None = None) -> None:
    company["trial"] = {"started_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}


def status(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    begun = _when(((state.get("company") or {}).get("trial") or {}).get("started_at", ""))
    if not begun:
        return {"tracked": False}
    sent = [
        (job, inv) for job in state.get("jobs", []) for inv in job.get("invoices", [])
        if inv.get("sent_at") and (_when(inv["sent_at"]) or begun) >= begun
    ]
    paid = []
    for job, inv in sent:
        if inv.get("status") == "paid":
            times = [p.get("matched_at", "") for p in job.get("payments", []) if p.get("invoice_id") == inv["id"]]
            when = _when(max(times)) if times else None
            if when:
                paid.append((when, inv["number"], job["name"]))
    base = {"tracked": True, "started_at": begun.isoformat(timespec="seconds"), "pay_apps_sent": len(sent)}
    if paid:
        when, number, job_name = min(paid)
        return {**base, "state": "ended", "reason": "paid", "ended_at": when.isoformat(timespec="seconds"),
                "first_paid": number, "first_paid_job": job_name}
    if (state.get("company") or {}).get("billing_style") == "draws":
        end = begun + timedelta(days=DRAW_DAYS)
        if now < end:
            return {**base, "state": "active", "ends_by": end.isoformat(timespec="seconds"), "extended": False, "draws": True}
        return {**base, "state": "ended", "reason": "time", "ended_at": end.isoformat(timespec="seconds"), "draws": True}
    day90 = begun + timedelta(days=TRIAL_DAYS)
    day120 = begun + timedelta(days=EXTENDED_DAYS)
    unpaid = any(inv.get("status") in OPEN for _, inv in sent)
    if now < day90:
        return {**base, "state": "active", "ends_by": day90.isoformat(timespec="seconds"), "extended": False}
    if unpaid and now < day120:
        return {**base, "state": "active", "ends_by": day120.isoformat(timespec="seconds"), "extended": True}
    end = day120 if unpaid else day90
    return {**base, "state": "ended", "reason": "time", "ended_at": end.isoformat(timespec="seconds")}
