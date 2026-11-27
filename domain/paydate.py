"""When an open pay app should be paid, from what each client has done (Siteline comparison, item 6).

client_stats(state): for each client, from its pay apps paid in full, the days from sent to paid
(median and average) and how many were paid by their due date. predicted_pay(): the day to expect
the money on one open pay app, and why that day:

  1. "promised": the client approved it on its page and said when payment goes out
     (domain.expected_payment, the existing expected-date rule, kept as it is);
  2. "history":  sent + the client's median days to paid, from its paid pay apps;
  3. "terms":    the contract's payment terms (the due date set when it went out, or the
                 client's payment terms in days);
  4. "usual":    the client's "days they usually take to pay" from the Client playbook, which
                 starts at the company's default (14 days, 21 for a GC).

Days are calendar days, as the rest of Duracash counts them (the due date, follow-ups and the
Results figures); there is no business-day calendar to follow.

Imported at the bottom of domain/__init__.py (from .paydate import *). Helpers from domain are
imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

__all__ = ["client_stats", "predicted_pay", "add_pay_predictions", "PAY_DEFAULT_DAYS"]

# The playbook's default when a client has none saved (domain._new_payer sets 14, or 21 for a GC).
PAY_DEFAULT_DAYS = 14


def _day(value: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(value)).date() if "T" in str(value) else date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _paid_on(job: dict[str, Any], invoice: dict[str, Any]) -> date | None:
    """The day the last payment on a paid pay app was recorded (domain.payer_timing's rule)."""
    days = [d for d in (_day(p.get("matched_at")) for p in job.get("payments", []) if p.get("invoice_id") == invoice.get("id")) if d]
    return max(days) if days else None


def client_stats(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{client id: {"paid", "median_days", "average_days", "on_time", "judged", "on_time_pct"}}
    from the pay apps paid in full. On time: paid by its due date (payment terms), or else by the
    date the client gave when approving it; one with neither isn't judged."""
    days: dict[str, list[int]] = {}
    judged: dict[str, list[bool]] = {}
    for job in state.get("jobs", []):
        payer_id = job.get("payer_id", "")
        for inv in job.get("invoices", []):
            sent = _day(inv.get("sent_at")) if inv.get("sent_at") else None
            if inv.get("status") != "paid" or not sent:
                continue
            paid = _paid_on(job, inv)
            if not paid or paid < sent:
                continue
            days.setdefault(payer_id, []).append((paid - sent).days)
            deadline = _day(inv.get("due_on")) if inv.get("due_on") else _day(inv.get("expected_payment_on")) if inv.get("expected_payment_on") else None
            if deadline:
                judged.setdefault(payer_id, []).append(paid <= deadline)
    out = {}
    for payer_id, values in days.items():
        marks = judged.get(payer_id, [])
        out[payer_id] = {
            "paid": len(values),
            "median_days": int(round(median(values))),
            "average_days": round(sum(values) / len(values), 1),
            "on_time": sum(marks),
            "judged": len(marks),
            "on_time_pct": round(100 * sum(marks) / len(marks)) if marks else None,
        }
    return out


def _words(d: date, today: date) -> str:
    return f"{d.strftime('%b')} {d.day}" + (f", {d.year}" if d.year != today.year else "")


def _plural(n: int) -> str:
    return f"{n} day{'' if n == 1 else 's'}"


def predicted_pay(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any],
                  stats: dict[str, dict[str, Any]] | None = None, today: date | None = None) -> dict[str, Any] | None:
    """{"on": ISO date, "basis", "days" (from sent), "text", "overdue_days"} for a pay app out for
    payment, or None for one that isn't."""
    import domain
    if invoice.get("status") not in domain.OPEN_FOR_PAYMENT or not invoice.get("sent_at"):
        return None
    sent = _day(invoice["sent_at"])
    if not sent:
        return None
    today = today or datetime.now(timezone.utc).date()
    payer = next((p for p in state.get("payers", []) if p.get("id") == job.get("payer_id")), {})
    history = (client_stats(state) if stats is None else stats).get(job.get("payer_id", ""))
    promised = domain.expected_payment(invoice)
    terms = domain._int_or(payer.get("payment_terms_days"), None)
    if promised:
        on, basis = promised, "promised"
        why = f"{invoice.get('approved_by_payer') or 'the client'} said so when approving it"
    elif history:
        on, basis = sent + timedelta(days=history["median_days"]), "history"
        why = f"this client usually pays in {_plural(history['median_days'])}" + (
            "" if history["paid"] > 2 else f" (from {history['paid']} paid)")
    elif invoice.get("due_on") and _day(invoice["due_on"]):
        on, basis = _day(invoice["due_on"]), "terms"
        why = "its payment due date; no paid pay apps from this client yet"
    elif terms:
        on, basis = sent + timedelta(days=terms), "terms"
        why = f"{_plural(terms)} payment terms; no paid pay apps from this client yet"
    else:
        usual = domain._int_or(payer.get("usual_approval_days"), None) or PAY_DEFAULT_DAYS
        on, basis = sent + timedelta(days=usual), "usual"
        why = f"the {_plural(usual)} this client usually takes, from the Client playbook"
    late = (today - on).days
    return {"on": on.isoformat(), "basis": basis, "days": (on - sent).days,
            "text": f"Expected {'' if basis == 'promised' else 'about '}{_words(on, today)} · {why}",
            "overdue_days": late if late > 0 else 0}


def add_pay_predictions(state: dict[str, Any], job: dict[str, Any], view: dict[str, Any],
                        stats: dict[str, dict[str, Any]] | None = None, today: date | None = None) -> None:
    """Put "predicted_pay" on each open pay app of a job as the app is sent it (view)."""
    stats = client_stats(state) if stats is None else stats
    stored = {inv.get("id"): inv for inv in job.get("invoices", [])}
    for inv in view.get("invoices", []):
        guess = predicted_pay(state, job, stored.get(inv.get("id"), inv), stats, today)
        if guess:
            inv["predicted_pay"] = guess
