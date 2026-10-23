"""What Duracash did for a company, in numbers (P2-10).

The strategy doc's renewal argument and the pilot's four measures, from what the app
already records. Each number says how it is counted; none is estimated.

  - days from work done to the pay app sent (and jobs done but not billed yet)
  - days from a pay app sent to it being paid, overall and by payer
  - follow-ups per pay app sent
  - pay apps sent back by the payer (rejections taught to the playbook, pencil changes)
  - change orders billed and retainage collected
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from typing import Any

import domain

LIVE = {"sent", "approved", "partial", "paid"}


def _day(iso: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(iso)).date()
    except (TypeError, ValueError):
        return None


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def compute(state: dict[str, Any], today: date | None = None, since: date | None = None) -> dict[str, Any]:
    """Numbers for pay apps sent on or after `since` (all time when None)."""
    today = today or datetime.now(timezone.utc).date()
    payers = {p["id"]: p.get("name", "") for p in state.get("payers", [])}
    in_range = lambda d: d is not None and (since is None or d >= since)

    done_to_sent, waiting = [], []
    sent_to_paid, by_payer = [], {}
    sent_count = sent_cents = paid_count = paid_cents = 0
    for job in state.get("jobs", []):
        done = _day(job.get("completed_at"))
        live = [i for i in job.get("invoices", []) if i.get("status") in LIVE and i.get("sent_at")]
        if done:
            after = sorted(d for d in (_day(i["sent_at"]) for i in live) if d and d >= done)
            if after and in_range(after[0]):
                done_to_sent.append((after[0] - done).days)
            elif not after and domain.job_money(job)["unbilled_completed_cents"] > 0 and not job.get("closed"):
                waiting.append({"job": job["name"], "job_id": job["id"], "days": (today - done).days})
        for inv in live:
            sent = _day(inv["sent_at"])
            if not in_range(sent):
                continue
            sent_count += 1
            sent_cents += inv["amount_cents"]
            if inv["status"] == "paid":
                paid_on = max((d for d in (_day(p.get("matched_at")) for p in job.get("payments", []) if p.get("invoice_id") == inv["id"]) if d),
                              default=None)
                if paid_on:
                    days = (paid_on - sent).days
                    sent_to_paid.append(days)
                    by_payer.setdefault(payers.get(job.get("payer_id"), "Unknown"), []).append(days)
                    paid_count += 1
                    paid_cents += inv["amount_cents"]

    since_iso = since.isoformat() if since else ""
    follow_ups = sum(1 for e in state.get("email_log", []) if e.get("kind") == "follow_up" and (e.get("at") or "") >= since_iso)
    rejections = sum(1 for a in state.get("audit", []) if a.get("action") == "learn_rejection" and (a.get("at") or "") >= since_iso)
    rejections += sum(1 for j in state.get("jobs", []) for i in j.get("invoices", []) if i.get("pencil_status") == "corrections")
    cos_billed = sum(c["amount_cents"] for j in state.get("jobs", []) for c in j.get("change_orders", []) if c.get("status") == "approved")
    retainage = sum(int(j.get("retainage_released_cents") or 0) for j in state.get("jobs", []))
    return {
        "since": since_iso or None,
        "pay_apps_sent": sent_count,
        "billed_cents": sent_cents,
        "pay_apps_paid": paid_count,
        "paid_cents": paid_cents,
        "done_to_sent_median_days": _round(median(done_to_sent)) if done_to_sent else None,
        "done_to_sent_jobs": len(done_to_sent),
        "waiting_to_bill": sorted(waiting, key=lambda w: -w["days"]),
        "sent_to_paid_median_days": _round(median(sent_to_paid)) if sent_to_paid else None,
        "sent_to_paid_average_days": _round(sum(sent_to_paid) / len(sent_to_paid)) if sent_to_paid else None,
        "by_payer": sorted(({"payer": k, "average_days": _round(sum(v) / len(v)), "paid": len(v)} for k, v in by_payer.items()),
                           key=lambda r: -r["average_days"]),
        "follow_ups": follow_ups,
        "follow_ups_per_pay_app": _round(follow_ups / sent_count) if sent_count else None,
        "sent_back": rejections,
        "change_orders_billed_cents": cos_billed,
        "retainage_collected_cents": retainage,
    }


def month_summary(state: dict[str, Any], year: int, month: int) -> tuple[str, str]:
    """Subject and body of the monthly "what Duracash did for you" email, for one calendar month."""
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    name = state.get("company", {}).get("name", "") or "your company"
    label = first.strftime("%B %Y")
    sent, paid, days, follow = [], [], [], 0
    for job in state.get("jobs", []):
        for inv in job.get("invoices", []):
            d = _day(inv.get("sent_at"))
            if inv.get("status") in LIVE and d and first <= d < nxt:
                sent.append(inv)
        for p in job.get("payments", []):
            d = _day(p.get("matched_at"))
            if d and first <= d < nxt:
                paid.append(p)
                inv = next((i for i in job.get("invoices", []) if i["id"] == p.get("invoice_id")), None)
                sd = _day(inv.get("sent_at")) if inv else None
                if sd:
                    days.append((d - sd).days)
    follow = sum(1 for e in state.get("email_log", []) if e.get("kind") == "follow_up" and first <= (_day(e.get("at")) or date.min) < nxt)
    # As of the month's end: jobs finished after it belong to next month's email.
    waiting = [w for w in compute(state, today=nxt)["waiting_to_bill"] if w["days"] > 0]
    lines = [
        f"{name}: {label} in Duracash",
        "",
        f"Pay apps sent: {len(sent)}, for {domain.money(sum(i['amount_cents'] for i in sent))}.",
        f"Payments received: {len(paid)}, for {domain.money(sum(p['amount_cents'] for p in paid))}"
        + (f"; on average {round(sum(days) / len(days), 1)} days from sending to payment." if days else "."),
        f"Follow-ups sent: {follow}.",
    ]
    if waiting:
        lines += ["", "Done but not billed yet:"] + [f"- {w['job']}: {w['days']} days since the work was marked done" for w in waiting[:10]]
    lines += ["", "Open Duracash to act on any of these. Owners of the company get this summary once a month."]
    return f"{label}: what Duracash did for {name}", "\n".join(lines)
