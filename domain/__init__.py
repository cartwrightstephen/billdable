"""Duracash domain: job money file, packet gate, invoice draft, payment match.

Source accounting data is treated as external. This module owns the operating
record Duracash builds on top of it: what is billable, what is blocking send,
who approved, and whether cash matched.

A package since R6-08: parts move out into their own modules one at a time, and every public
name stays importable from `domain` (the audit trail is in domain/trail.py).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import doc_check
import payapp_pdf
import sov
import trial
from domain.trail import AUDIT_FIELDS, _audit_hash, audit, check_audit, seal_audit  # noqa: F401  (re-exported)

BILLED_STATUSES = {"sent", "approved", "partial", "paid"}
DRAFT_STATUSES = {"draft", "awaiting_approval"}
OPEN_FOR_PAYMENT = {"sent", "approved", "partial"}
# Largest contract or change-order amount accepted: $100,000,000.00.
MAX_AMOUNT_CENTS = 10_000_000_000


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def sign_off(state: dict[str, Any], actor: str) -> str:
    """How every email to a customer ends (R4-05): who sent it, then the company and its phone,
    so a GC's accounts-payable desk knows who to call. (It used to read "Approved by {actor}.")"""
    company = state.get("company") or {}
    tail = " · ".join(x for x in (str(company.get("name") or "").strip(), str(company.get("phone") or "").strip()) if x)
    return f"{actor}\n{tail}" if tail else str(actor)


def pay_app_subject(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> str:
    """"Pay application PA-x — job — amount — company" (R4-05), "Final pay application" for the
    last one, and the period it covers at the end (R4-04): what an AP desk files it by."""
    company = str((state.get("company") or {}).get("name") or "").strip()
    parts = [f"{'Final pay application' if invoice.get('final') else 'Pay application'} {invoice['number']}",
             job["name"], money(invoice["amount_cents"])]
    if company:
        parts.append(company)
    if invoice.get("period_to"):
        parts.append(f"period to {invoice['period_to']}")
    return " — ".join(parts)


def _day(value: Any, field: str, message: str) -> date | None:
    """A YYYY-MM-DD date from a form; blank is None, anything else is refused under `field`."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        _refuse(message, {field: message})
    return None


def period_to(job: dict[str, Any], value: Any = "") -> str:
    """The last day of work a pay app covers (R4-04). Blank is today; it can't be before the
    previous pay app's, nor more than a month ahead. A draft about to be replaced doesn't count."""
    earlier = [inv["period_to"] for inv in job.get("invoices", [])
               if inv.get("period_to") and inv["status"] not in DRAFT_STATUSES and inv["status"] != "void"]
    last = max(earlier) if earlier else ""
    today = datetime.now(timezone.utc).date()
    day = _day(value, "period_to", "Enter the date this pay app runs to, like 2026-10-31.")
    if day is None:
        return max(today.isoformat(), last)
    if last and day.isoformat() < last:
        _refuse("", {"period_to": f"Enter {last} or later: the last pay app ran to {last}."})
    if day > today + timedelta(days=31):
        _refuse("", {"period_to": "Enter a date no more than a month from today."})
    return day.isoformat()


def is_final(job: dict[str, Any]) -> bool:
    """The job is marked done and nothing is left to bill: the pay app that gets it there is the
    final one (R4-04). Retainage still held is asked for separately."""
    return job.get("work_status") == "complete" and job_money(job)["unbilled_completed_cents"] == 0 \
        and not any(co["status"] == "pending" and not co.get("included_in_billing") for co in job["change_orders"])


class GateError(Exception):
    """A business refusal (the server's 409). `blockers` lists what stands in the way, for the
    card under the button that was tapped; `fields` maps a request field (its JSON key, such as
    "ap_email") to what to fix there, so a form shows it under that input. `extra` is anything
    else the page needs to answer it, such as the saved customer a new name nearly matches.
    `action` is the way out when it is on another page: {"label", "view", "job_id", "detail"},
    a link in that card. `keep_changes` says what was found on the way to the refusal is saved all
    the same (an item now waiting for a look), so the page can show the way forward."""

    def __init__(self, message: str, blockers: list[str] | None = None, fields: dict[str, str] | None = None,
                 extra: dict[str, Any] | None = None, action: dict[str, str] | None = None, keep_changes: bool = False):
        super().__init__(message)
        self.keep_changes = keep_changes
        self.fields = dict(fields or {})
        self.blockers = blockers or list(self.fields.values()) or [message]
        self.extra = dict(extra or {})
        self.action = dict(action or {})


def _refuse(message: str, fields: dict[str, str]) -> None:
    """Raise when any field has a problem: one message for one field, else `message` over them all."""
    if fields:
        raise GateError(next(iter(fields.values())) if len(fields) == 1 else message, fields=fields)


def parse_int(value: Any, label: str, field: str, default: int | None = None) -> int:
    """A whole number from a request: a JSON number or a string of digits. Blank gives `default`
    (or asks for the value when there is none); anything else is refused under the field.
    `label` names the field the way the form does, such as "the contract amount". Stored
    values are read with the lenient _int_or instead, which never refuses."""
    if value is None or value == "":
        if default is None:
            raise GateError(f"Enter {label}.", fields={field: f"Enter {label}."})
        return default
    if isinstance(value, float) and value == value and value not in (float("inf"), float("-inf")):
        return int(round(value))
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise GateError(f"Enter {label} as a number.", fields={field: f"Enter {label} as a number."})


def job_money(job: dict[str, Any]) -> dict[str, int]:
    approved = sum(co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved")
    pending = sum(co["amount_cents"] for co in job["change_orders"] if co["status"] == "pending")
    contract_total = job["contract_cents"] + approved
    billed = sum(inv["amount_cents"] for inv in job["invoices"] if inv["status"] in BILLED_STATUSES)
    payments = sum(p["amount_cents"] for p in job["payments"])
    # What the customer kept off a pay app that was settled short (R4-01): no longer owed.
    short_paid = sum(int(inv.get("short_pay_cents") or 0) for inv in job["invoices"] if inv["status"] in BILLED_STATUSES)
    draft = sum(inv["amount_cents"] for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES)
    # Pay app amounts are net of retainage. Retainage withheld on a pay app is
    # stored on the invoice; older records keep it in retainage_held_cents.
    retainage_on_billed = sum(inv.get("retainage_cents", 0) for inv in job["invoices"] if inv["status"] in BILLED_STATUSES)
    retainage_on_drafts = sum(inv.get("retainage_cents", 0) for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES)
    retainage_held = job.get("retainage_held_cents", 0) + retainage_on_billed
    retainage_released = job.get("retainage_released_cents", 0)
    if sov.has_sov(job):
        # Billed line by line from the schedule of values.
        unbilled_completed = max(0, sov.unbilled(job))  # a credit waits for work to come off
    else:
        unbilled_completed = max(
            0,
            job.get("completed_value_cents", contract_total) - billed - draft - retainage_held - retainage_on_drafts,
        )
    # Work billed past the contract and signed change orders (a credit signed after the bill went
    # out, say). An unsigned change order on a pay app counts until it is signed or withdrawn.
    allowed = contract_total + sum(co["amount_cents"] for co in job["change_orders"] if co["status"] == "pending" and co.get("included_in_billing"))
    billed_gross = job.get("retainage_held_cents", 0) + sum(
        int(inv.get("gross_cents", inv["amount_cents"] + inv.get("retainage_cents", 0))) for inv in job["invoices"] if inv["status"] in BILLED_STATUSES)
    came_in = payments + retainage_released
    entries = job.get("overbilling_settled") or []
    # A refund or credit is what really happened, whatever the contract does afterwards, so it stays
    # counted; only what never came in can't have gone back. returned is the money part (a credit
    # against a short pay moved none), and that credit stands only as long as the short pay does.
    returned = min(sum(int(s["returned_cents"]) for s in entries), came_in)
    settled = returned + min(sum(int(s["cents"]) - int(s["returned_cents"]) for s in entries), short_paid)
    # What came in on a pay app past what it billed is the client paying back a refund, and stays
    # that once more work is billed (Nov 6 review, C).
    paid_back = 0
    for inv in job["invoices"]:
        if inv["status"] in BILLED_STATUSES:
            paid_on = sum(p["amount_cents"] for p in job["payments"] if p.get("invoice_id") == inv["id"])
            paid_back += max(0, paid_on + int(inv.get("short_pay_cents") or 0) - inv["amount_cents"])
    paid_back = max(paid_back, payments + short_paid - billed)
    over = max(0, billed_gross + paid_back - allowed)
    # A signed credit taken off the retainage held (apply_credit_to_retainage), for as long as the
    # contract leaves it standing: a later change order brings the retainage back.
    credited = min(sum(int(c["cents"]) for c in job.get("retainage_credits") or []), max(0, over - settled), max(0, retainage_held - retainage_released))
    above = max(0, came_in + short_paid - allowed)
    # More went back than the job is now over its contract (a change order signed after a refund),
    # less what has come back since.
    refund_owed = max(0, returned - max(0, billed_gross - allowed) - paid_back)
    return {
        "contract_cents": job["contract_cents"],
        "approved_co_cents": approved,
        "pending_co_cents": pending,
        "contract_total_cents": contract_total,
        "billed_cents": billed,
        "draft_cents": draft,
        "payments_cents": payments,
        "collected_cents": max(0, payments - returned),
        "refunded_cents": returned,
        "short_paid_cents": short_paid,
        "outstanding_cents": max(0, billed - payments - short_paid + paid_back) + refund_owed,
        "refund_owed_cents": refund_owed,
        "unbilled_completed_cents": unbilled_completed,
        "billed_gross_cents": billed_gross,
        "overbilled_cents": max(0, over - settled - credited),
        "billing_allowed_cents": allowed,
        "overbilling_settled_cents": settled,
        "overbilling_settleable_cents": max(0, min(over - credited, above) - settled),
        "retainage_held_cents": retainage_held,
        "retainage_released_cents": retainage_released,
        "retainage_credited_cents": credited,
        "retainage_open_cents": max(0, retainage_held - retainage_released - credited),
    }


def _owed_text(m: dict[str, int]) -> str:
    """What is owed, in words: on pay apps, and what a refund left short of the contract."""
    owed = m["refund_owed_cents"]
    on_bills = m["outstanding_cents"] - owed
    parts = [f"{money(on_bills)} is still owed on pay apps sent."] if on_bills else []
    if owed:
        parts.append(f"{money(owed)} is owed back: {money(m['refunded_cents'])} was refunded or credited, and the contract total is now {money(m['billing_allowed_cents'])}. "
                     "Record it when it comes in, or sign a credit if the client keeps it.")
    return " ".join(parts)


def retainage_credit_room(job: dict[str, Any], m: dict[str, int] | None = None) -> int:
    """How much signed credit can still come off the retainage held: what is overbilled, what is
    held, and the credits signed less what was already taken off this way."""
    m = m or job_money(job)
    signed = sum(-co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved" and co["amount_cents"] < 0)
    used = sum(int(c["cents"]) for c in job.get("retainage_credits") or [])
    return max(0, min(m["overbilled_cents"], m["retainage_open_cents"], signed - used))


def close_blockers(job: dict[str, Any]) -> list[str]:
    """Everything that must be settled before a job can be finished, in words."""
    return [b["text"] for b in close_blocker_items(job)]


def close_blocker_items(job: dict[str, Any]) -> list[dict[str, Any]]:
    """What stands between the job and Finish job, one entry per thing to do (R3-08):
    {kind, text}, plus invoice_id/number for an open draft and co_id/number for an unsigned
    change order. The job page puts the buttons that settle each one beside it."""
    if job.get("closed"):
        return [{"kind": "closed", "text": "Job is already finished."}]
    m = job_money(job)
    out: list[dict[str, Any]] = []
    if job.get("work_status") != "complete":
        out.append({"kind": "not_done", "text": "The work isn't finished. Use Final pay app."})
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft:
        out.append({"kind": "draft", "text": f"{open_draft['number']} is still a draft.",
                    "invoice_id": open_draft["id"], "number": open_draft["number"]})
    for co in job["change_orders"]:
        if co["status"] == "pending":
            out.append({"kind": "co", "text": f"CO {co['number']} is not signed yet.", "co_id": co["id"], "number": co["number"]})
    if m["unbilled_completed_cents"] > 0 and not open_draft:
        out.append({"kind": "unbilled", "text": f"{money(m['unbilled_completed_cents'])} of finished work is not billed."})
    if m["overbilled_cents"] > 0:
        last = next((inv for inv in reversed(job["invoices"]) if inv["status"] in BILLED_STATUSES), None)
        already = f", with {money(m['overbilling_settled_cents'])} already refunded or credited" if m["overbilling_settled_cents"] else ""
        creditable = retainage_credit_room(job, m)
        out.append({"kind": "overbilled", "invoice_id": (last or {}).get("id", ""), "number": (last or {}).get("number", ""),
                    "settleable": m["overbilling_settleable_cents"] > 0, "creditable_cents": creditable, "text":
                    f"The client is overbilled by {money(m['overbilled_cents'])}: {money(m['billed_gross_cents'])} billed against a contract total of "
                    f"{money(m['billing_allowed_cents'])}{already}. Pull back {(last or {}).get('number', 'the last pay app')} and draft it again if nothing is paid on it. "
                    "If it is paid, refund or credit the difference and record that here."
                    + (f" If the signed credit comes out of the retainage held, take {money(creditable)} off the retainage." if creditable else "")})
    if m["outstanding_cents"] > 0:
        out.append({"kind": "outstanding", "refund_owed_cents": m["refund_owed_cents"], "text": _owed_text(m)})
    if m["retainage_open_cents"] > 0:
        out.append({"kind": "retainage", "text": f"{money(m['retainage_open_cents'])} held back (retainage) is still owed."})
    if job.get("waiver_due") and not job.get("waiver_sent"):
        out.append({"kind": "waiver", "text": "A lien waiver is still due."})
    return out


def active_packet(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The packet that gates send: the open pay app's packet, else the job packet."""
    open_invoice = next(
        (inv for inv in reversed(job.get("invoices", [])) if inv.get("status") in {"draft", "awaiting_approval"}),
        None,
    )
    if open_invoice and open_invoice.get("packet"):
        return open_invoice["packet"]
    return job.get("packet") or []


def missing_packet(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The required items not in. On a pay app not sent yet, an item in only on names is not in
    (acceptance 96), whatever the older code stored on it."""
    open_one = any(inv.get("status") in DRAFT_STATUSES for inv in job.get("invoices", []))
    return [item for item in active_packet(job) if item["required"] and (not item["present"] or (open_one and sov.name_only(job, item)))]


def closeout_missing(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The closeout items not in yet, which hold back asking for all the retainage: the closeout
    packet, or for older jobs without one, the paperwork still missing on the job."""
    if job.get("closeout_packet"):
        return [item for item in job["closeout_packet"] if item["required"] and (not item["present"] or sov.name_only(job, item))]
    return missing_packet(job)


def unsigned_cos_in_billing(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [co for co in job["change_orders"] if co["status"] == "pending" and co.get("included_in_billing")]


def _blocker_text(job: dict[str, Any], item: dict[str, Any]) -> str:
    """What holds an item back: a name with no file, a file waiting for a person's look, else it is missing."""
    if sov.name_only(job, item):
        return f"{item['label']}: No file was kept for it. Remove it and add the file."
    waiting = [a["name"] for a in item.get("attachments", []) if a.get("unconfirmed")]
    return f"{item['label']}: look at {', '.join(waiting)} and confirm it." if waiting else item["blocker"]


def blocker_items(job: dict[str, Any]) -> list[dict[str, Any]]:
    """What blocks send, each as {kind, text, co_id, number, item_id, waiting}: kind "paperwork" (a
    missing packet item: item_id is where its button lands, waiting that a file only needs a
    look; an item in only on names needs a file, so it is not waiting) or "co" (an unsigned
    change order on the bill, which the app links to)."""
    items = [{"kind": "paperwork", "text": _blocker_text(job, item), "co_id": "", "number": 0, "item_id": item.get("id", ""),
              "waiting": not sov.name_only(job, item) and any(a.get("unconfirmed") for a in item.get("attachments", []))}
             for item in missing_packet(job)]
    for co in unsigned_cos_in_billing(job):
        items.append({
            "kind": "co",
            "text": f"Change Order {co['number']} is included in the billing amount but has not been signed.",
            "co_id": co["id"],
            "number": co["number"],
        })
    return items


def blocker_lines(job: dict[str, Any]) -> list[str]:
    return [b["text"] for b in blocker_items(job)]


def is_blocked(job: dict[str, Any]) -> bool:
    """Blockers count once there is a pay app to block (a draft) or the work is done; before
    that they are what the next pay app will need. job_view sends it as `blocked`."""
    return bool(blocker_items(job)) and (
        job.get("work_status") == "complete" or any(i.get("status") in DRAFT_STATUSES for i in job.get("invoices", [])))


def needs_paperwork(job: dict[str, Any]) -> bool:
    """Blocked only by paperwork to attach: amber "Needs paperwork", not red "Blocked" (R1-08)."""
    return is_blocked(job) and len(missing_packet(job)) == len(blocker_items(job))


def blocker_button(job: dict[str, Any]) -> str:
    """The button that opens the paperwork, or the change order when that is all that blocks (R3-05)."""
    items = blocker_items(job)
    n = len(items)
    if needs_paperwork(job):
        waiting = sum(1 for b in items if b["waiting"])
        verb = "Check" if waiting == n else "Add or check" if waiting else "Add"
        return f"{verb} {n} {'' if waiting else 'missing '}document{'' if n == 1 else 's'}"
    if is_blocked(job) and items and all(b["kind"] == "co" for b in items):
        return f"Get CO {items[0]['number']} signed" if n == 1 else f"Get {n} change orders signed"
    return f"Fix {n} thing{'' if n == 1 else 's'} before sending"


def status_label(job: dict[str, Any], style: str = "pay_apps") -> str:
    """A status names the blocker. Never return a bare 'Pending'. style: the company's
    billing_style, for its word for a pay app in the fixed words (term_text, R6-01)."""
    if job.get("closed"):
        return "Closed"
    lines = blocker_lines(job)
    open_invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] != "void"), None)
    draft_open = open_invoice and open_invoice["status"] in {"draft", "awaiting_approval"}
    if draft_open and lines:
        return lines[0]
    if draft_open and open_invoice["status"] == "awaiting_approval":
        return term_text("Pay app ready for review", style)
    if draft_open:
        return term_text("Pay app draft in progress", style)
    # Still owed: the newest pay app, or an older one still unpaid when a newer one was paid (the
    # pill, jobs.js jobStatus, looks at every pay app too). The job's external status describes the
    # newest pay app's last event, so an older one gets the plain words.
    owed = open_invoice if open_invoice and open_invoice["status"] in OPEN_FOR_PAYMENT else \
        next((inv for inv in job["invoices"] if inv["status"] in OPEN_FOR_PAYMENT), None)
    external = job.get("external_status") if owed is open_invoice else None
    if owed and owed["status"] == "sent":
        return external or "Submitted, awaiting acknowledgment"
    if owed and owed["status"] == "approved":
        return external or "Approved, payment not received"
    if owed and owed["status"] == "partial":
        return "Partial payment received"
    if job.get("work_status") == "complete" and lines and job_money(job)["unbilled_completed_cents"] > 0:
        return lines[0]
    if job.get("waiver_due") and not job.get("waiver_sent"):
        return "Payment received, waiver not sent"
    if job.get("work_status") == "complete" and job_money(job)["retainage_open_cents"] > 0:
        return "Retainage outstanding"
    if not open_invoice:
        return term_text("Pay app not drafted", style) if job.get("work_status") == "complete" else "In progress"
    if open_invoice["status"] == "paid":
        return "Paid"
    return open_invoice["status"].replace("_", " ").capitalize()


def record_progress(
    state: dict[str, Any],
    job_id: str,
    completed_value_cents: int,
    notes: str = "",
    actor: str = "owner",
    period_cents: int | None = None,
    period_end: Any = "",
) -> dict[str, Any]:
    """Set completed-to-date and draft a progress pay app. Does not mark the job done.
    A correction while the draft is still only here replaces it at the new amount, keeping its
    number and its paperwork; a draft the customer is reviewing has to be answered or voided first.
    period_cents is "this draw" instead (R3-01): the work on this pay app, before retainage, on top
    of what is already billed; completed_value_cents is then ignored and refusals name period_cents.
    period_end is the date the pay app runs to (R4-04; blank is today)."""
    job = _open_job(state, job_id)
    if job.get("work_status") == "complete":
        raise GateError("Job is already marked done. Draft the remaining pay app instead.")
    if sov.has_sov(job):
        raise GateError("This job bills from its schedule of values. Enter quantities completed on each line instead.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft and open_draft.get("pencil_status") == "sent":
        raise GateError(f"{open_draft['number']} is with the client as a draft. Void it or record their answer first.")
    if not open_draft:
        _need_playbook(state, job)
    contract_total = job["contract_cents"] + sum(
        co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved"
    )
    billed = _billed_gross(job, skip=open_draft)
    runs_to = period_to(job, period_end)
    if period_cents is not None:
        if period_cents <= 0:
            _refuse("", {"period_cents": "Enter this draw, more than $0."})
        if billed + period_cents > contract_total:
            _refuse("", {"period_cents": f"Enter no more than the {money(contract_total - billed)} left on the contract. You entered {money(period_cents)}."})
        if open_draft and billed + period_cents == job.get("completed_value_cents", 0):
            _refuse("", {"period_cents": f"{open_draft['number']} is already drafted at that amount. Enter the corrected draw."})
        completed_value_cents = billed + period_cents
    if completed_value_cents <= 0:
        _refuse("", {"completed_cents": "Enter the total done so far, more than $0."})
    if completed_value_cents > contract_total:
        _refuse("", {"completed_cents": f"Enter no more than the contract total, {money(contract_total)}."})
    if completed_value_cents <= billed:
        _refuse("", {"completed_cents": f"Enter more than the {money(billed)} already billed. This is the total done so far, not this period."})
    if open_draft and completed_value_cents == job.get("completed_value_cents", 0):
        _refuse("", {"completed_cents": f"{open_draft['number']} is already drafted at that total. Enter the corrected total done so far."})
    job["completed_value_cents"] = int(completed_value_cents)
    notes = (notes or "").strip()[:2000]
    audit(
        state,
        job_id,
        "record_progress",
        f"{actor} recorded completed-to-date {money(completed_value_cents)}.{' ' + notes if notes else ''}",
        actor,
    )
    if open_draft:
        open_draft["status"] = "void"
        open_draft["voided_at"] = utcnow()
        open_draft["replaced"] = True
        audit(state, job_id, "draft_replaced", f"{open_draft['number']} ({money(open_draft['amount_cents'])}) replaced at the new total done so far.", "duracash")
        return draft_invoice(state, job_id, actor="duracash", replaces=open_draft, period_end=runs_to, notes=notes)
    return draft_invoice(state, job_id, actor="duracash", period_end=runs_to, notes=notes)


def _billed_gross(job: dict[str, Any], skip: dict[str, Any] | None = None) -> int:
    """Work on pay apps that still count (before retainage), leaving out `skip`."""
    return sum(
        int(inv.get("gross_cents", inv["amount_cents"] + inv.get("retainage_cents", 0)))
        for inv in job["invoices"] if inv["status"] != "void" and inv is not skip
    )


def _next_number(job: dict[str, Any]) -> str:
    """PA-<code>-NN, one past the pay apps that count. A voided draft's number is used again,
    unless the customer saw it: as a draft for review, or sent and then pulled back (R4-03)."""
    taken = {inv["number"] for inv in job["invoices"] if inv["status"] != "void" or inv.get("pencil_sent_at") or inv.get("sent_at")}
    n = len([inv for inv in job["invoices"] if inv["status"] != "void"]) + 1
    while f"PA-{job['code']}-{n:02d}" in taken:
        n += 1
    return f"PA-{job['code']}-{n:02d}"


def mark_done(state: dict[str, Any], job_id: str, actor: str = "owner", notes: str = "", evidence: list[str] | None = None,
              note_record: bool = True, period_end: Any = "") -> dict[str, Any]:
    job = _open_job(state, job_id)
    if job.get("work_status") == "complete":
        raise GateError("Job is already marked done.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if _with_client(open_draft):
        raise GateError(
            f"{open_draft['number']} is with the client for their review. Record their answer or delete the draft, "
            "then use Final pay app so it covers the rest."
        )
    if not open_draft:
        _need_playbook(state, job)
    runs_to = period_to(job, period_end)
    # A draft only here becomes the final pay app (B6 of the Oct 30 review): same number and paperwork.
    replaced = open_draft
    if replaced:
        replaced["status"] = "void"
        replaced["voided_at"] = utcnow()
        replaced["replaced"] = True
        # The final pay app bills everything left: amounts changed on the draft start over.
        if replaced.pop("sov_overrides", None):
            audit(state, job_id, "payapp_amount_changed", f"The amounts changed by hand on {replaced['number']} went back to the suggested ones: the final pay app bills all the work left.", "duracash")
    job["work_status"] = "complete"
    job["completed_at"] = job.get("completed_at") or utcnow()
    job["done_at_invoice"] = len(job["invoices"])   # pay apps from here on are the final one(s)
    job["done_notes"] = notes or job.get("done_notes", "")
    # The notes are kept on the job record (and in History, through the audit line below), not
    # on the job's header line (R2-05). A message filed from the Inbox is its own record already.
    if note_record and (notes or "").strip():
        job.setdefault("records", []).append({
            "id": "rec-" + secrets.token_hex(3), "kind": "note", "label": "Notes at Final pay app",
            "note": notes.strip(), "added_at": utcnow(), "added_by": actor,
        })
    if evidence:
        job.setdefault("evidence", []).extend(evidence)
    job["completed_value_cents"] = job["contract_cents"] + sum(
        co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved"
    )
    for line in sov.lines(job):
        line.setdefault("field_milli", line["done_milli"])   # what the field recorded stays, for undo
        line["done_milli"] = line["qty_milli"]
    audit(state, job_id, "job_done", f"{actor} marked {job['name']} done. Notes: {notes or 'none'}.", actor)
    _ensure_closeout_packet(state, job)
    if replaced:
        audit(state, job_id, "draft_replaced", f"{replaced['number']} ({money(replaced['amount_cents'])}) replaced by the final pay app.", "duracash")
        return draft_invoice(state, job_id, actor="duracash", replaces=replaced, period_end=runs_to, notes=notes)
    return draft_invoice(state, job_id, actor="duracash", period_end=runs_to, notes=notes)


def final_pay_app_preview(state: dict[str, Any], job_id: str) -> dict[str, Any]:
    """What Final Pay App (mark_done) would draft, worked out on a copy so nothing changes: its
    amount, whether it is the final one (is_final), and what is left pending if it isn't.
    Refuses as mark_done would."""
    trial_state = deepcopy(state)
    job = mark_done(trial_state, job_id, "preview")
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    pending = [{"number": co["number"], "amount_cents": co["amount_cents"]}
               for co in job["change_orders"] if co["status"] == "pending" and not co.get("included_in_billing")]
    if not draft:
        return {"drafted": False, "final": is_final(job), "pending_cos": pending}
    replaced = next((inv for inv in job["invoices"] if inv["id"] == draft.get("replaces")), None)
    return {"drafted": True, "final": bool(draft.get("final")) and is_final(job), "number": draft["number"], "amount_cents": draft["amount_cents"],
            "gross_cents": draft["gross_cents"], "retainage_cents": draft["retainage_cents"],
            "replaces": replaced["number"] if replaced else "", "pending_cos": pending}


def draft_invoice(
    state: dict[str, Any], job_id: str, actor: str = "duracash", strict: bool = False, replaces: dict[str, Any] | None = None,
    period_end: str = "", notes: str = "",
) -> dict[str, Any]:
    """Draft a pay app for unbilled completed work, withholding retainage.

    strict=True is for the explicit "Draft Pay App" button: a no-op raises
    instead of being logged silently, so the user is told why nothing happened.
    replaces is a draft just voided by a progress correction: the new one keeps its number
    (unless the customer saw it) and its paperwork.
    period_end is the date it runs to, already checked (period_to()); blank is today.
    notes are what the contractor wrote with the progress (R4-12): kept on the pay app and printed
    under its lines and on the PDF; a redraft without new notes keeps the replaced draft's.
    """
    job = _open_job(state, job_id)
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if job.get("work_status") == "complete":
        for line in sov.lines(job):   # every line is whole once the work is done (some were lowered before Nov 5)
            line["done_milli"] = line["qty_milli"]
    if open_draft:
        if strict:
            raise GateError(f"{open_draft['number']} is already an open draft. Send it before drafting another.")
        audit(state, job_id, "draft_skipped", "Open draft already exists.", actor)
        return job
    if not replaces:
        _need_playbook(state, job)
    money_file = job_money(job)
    work = money_file["unbilled_completed_cents"]
    if strict and job.get("work_status") != "complete":  # the person asked for it: the suggestion goes on
        billed_qty = sov.billed(job)["qty_by_line"]
        for line in sov.lines(job):
            line["done_milli"] = sov.suggested_done(line, billed_qty.get(line["id"], 0))
    # A draft set aside by a new schedule until it has quantities (save_sov) is what this one replaces.
    waiting = job.get("sov_replaced_draft")
    if waiting and not replaces:
        replaces = next((inv for inv in job["invoices"] if inv["id"] == waiting), None)
    period = sov.this_period(job) if sov.has_sov(job) else None
    pending_included = unsigned_cos_in_billing(job)
    pending_cents = sum(co["amount_cents"] for co in pending_included)
    # Amounts changed by hand on the draft this replaces stay: the person decides the pay app.
    chosen = (replaces or {}).get("sov_overrides") or {}
    if period and chosen:
        adjusted = sov.with_amounts(job, sov.this_period(job), chosen)
        if adjusted["total_cents"] + pending_cents > 0:
            period = adjusted
        else:
            audit(state, job_id, "payapp_amount_changed", f"The amounts changed by hand on {replaces['number']} would leave nothing to bill, so it went back to the suggested amounts.", "duracash")
    if period:
        work = period["total_cents"]
    gross = work + pending_cents
    if gross <= 0:
        if strict:
            raise GateError("Nothing to draft: there is no unbilled completed work on this job.")
        audit(state, job_id, "draft_skipped", "Nothing unbilled to draft.", actor)
        return job
    pct = int(job.get("retainage_pct") or 0)
    retained = (gross * pct + 50) // 100 if pct else 0
    amount = gross - retained
    number = _next_number(job)
    lines = []
    if period:
        for entry in period["lines"]:
            lines.append({"label": sov.line_label(entry), "amount_cents": entry["amount_cents"], "sov_line_id": entry["line_id"]})
        if period["op_cents"]:
            lines.append({"label": f"Overhead and profit {sov.op_bp(job) / 100:g}% on this period's base work", "amount_cents": period["op_cents"]})
    elif work:
        lines.append({"label": "Completed contract work not yet billed", "amount_cents": work})
    for co in pending_included:
        lines.append({"label": f"CO {co['number']} — {co['title']} (unsigned)", "amount_cents": co["amount_cents"], "co_id": co["id"]})
    if retained:
        lines.append({"label": f"Less retainage {pct}%", "amount_cents": -retained})
    invoice_id = f"inv-{job['id']}-{len(job['invoices']) + 1}"
    if replaces and replaces.get("packet") and replaces.get("packet_id") != "job-packet":
        packet = _carry_packet(replaces["packet"], invoice_id)
        _look_again_at_waivers(packet, replaces["amount_cents"], amount)
        _attach_from_record(state, job, packet, number)
    else:
        packet = _packet_for_pay_app(state, job, invoice_id, number)
    if retained:
        # Show the punch-list/closeout items from the first pay app that holds
        # retainage, so they can be collected while the job is still running.
        _ensure_closeout_packet(state, job)
    if period:
        # What the field stood at, beside the quantity drafted: they differ when a figure was typed.
        fields = {line["id"]: sov.field_total(line) for line in sov.lines(job)}
        for entry in period["sheet"]:
            entry["field_milli_at_draft"] = fields[entry["line_id"]]
    invoice = {
        "id": invoice_id,
        "number": number,
        "amount_cents": amount,
        "gross_cents": gross,
        "retainage_cents": retained,
        "status": "draft",
        "lines": lines,
        "created_at": utcnow(),
        "sent_at": None,
        "approved_by": None,
        "packet": packet,
        "packet_id": invoice_id if packet is not job.get("packet") else "job-packet",
        "sov_lines": period["lines"] if period else [],
        "sov_sheet": period["sheet"] if period else [],
        "op_cents": period["op_cents"] if period else 0,
        # The percent it was held at: a later change applies from the next pay app (R4-07).
        "retainage_pct": pct,
        "period_to": period_end or period_to(job),
        "progress_notes": (notes or "").strip()[:2000] or (replaces or {}).get("progress_notes", ""),
    }
    if replaces:
        invoice["replaces"] = replaces["id"]
        _carry_send_choices(state, job, replaces, invoice)
    job.pop("sov_replaced_draft", None)
    if period and period.get("kept"):
        invoice["sov_overrides"] = period["kept"]
        _audit_kept_amounts(state, job, invoice, replaces, period)
    job["invoices"].append(invoice)
    invoice["final"] = is_final(job)
    if invoice["packet_id"] != "job-packet":
        _name_conditional_waiver(job, invoice)
        if _add_ticket_item(job, invoice, _payer(state, job["payer_id"])):
            _attach_from_record(state, job, packet, number)
    blockers = blocker_lines(job)
    invoice["status"] = "draft" if blockers else "awaiting_approval"
    audit(
        state,
        job_id,
        "draft_invoice",
        f"Drafted {number} for {money(amount)}"
        + (f" ({money(gross)} less {money(retained)} retainage)" if retained else "")
        + f", with {len(packet)} supporting document{'' if len(packet) == 1 else 's'}. {len(blockers)} to fix before it can go.",
        actor,
    )
    return job


def _audit_kept_amounts(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any], old: dict[str, Any] | None,
                        period: dict[str, Any]) -> None:
    """A redraft that keeps amounts changed by hand says so when the suggested amount moved under
    one (new quantities came in) or the line's balance cut one down."""
    was = {e["line_id"]: e.get("suggested_cents", e["amount_cents"]) for e in (old or {}).get("sov_sheet") or []}
    for entry in period["sheet"]:
        if entry.get("suggested_cents") is None:
            continue
        name = sov_line_name(entry)
        if any(entry is c for c in period["cut"]):
            audit(state, job["id"], "payapp_amount_changed", f"{invoice['number']}, {name}: the amount changed by hand was more than the line's balance, so it is now {money(entry['amount_cents'])}.", "duracash")
        elif entry["line_id"] in was and was[entry["line_id"]] != entry["suggested_cents"]:
            audit(state, job["id"], "payapp_amount_changed", f"{invoice['number']}, {name}: suggested {money(entry['suggested_cents'])}, final stays {money(entry['amount_cents'])} as changed by hand.", "duracash")


def set_payapp_amounts(state: dict[str, Any], job_id: str, amounts: Any, actor: str = "owner", period_end: Any = None,
                       notes: Any = None) -> dict[str, Any]:
    """Change what the open draft bills on its schedule lines. amounts maps line id -> dollars; a
    blank goes back to the suggested amount. The draft is drafted again with them, keeping its
    number, paperwork and notes, and each change goes in the audit trail with the suggested and
    final amounts. An amount can be anything from $0 to what is left on the line. period_end and
    notes (Bill by dollars, which sends them with the amounts): the date it runs to and new notes,
    kept, and a change to them alone is a change too."""
    job = _open_job(state, job_id)
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if not draft:
        raise GateError("There is no draft pay app to change. Draft one first.")
    if _with_client(draft):
        raise GateError(f"{draft['number']} is with the client as a draft. Void it or record their answer first.")
    if not sov.has_sov(job):
        raise GateError("This job has no schedule of values. Enter the progress in dollars instead.")
    if not isinstance(amounts, dict) or not amounts:
        raise GateError("Enter an amount on at least one line.")
    drafted = {e["line_id"]: e["amount_cents"] for e in draft.get("sov_sheet") or []}
    # What the person sees as suggested: everything recorded and not yet billed.
    entries = {e["line_id"]: e for e in sov.this_period(job, skip=draft, field=True)["sheet"]}
    already = sov.billed(job, skip=draft)
    by_id = {line["id"]: line for line in sov.lines(job)}
    chosen = dict(draft.get("sov_overrides") or {})
    errors, changes, to_suggested = [], [], []
    for line_id, raw in amounts.items():
        line = by_id.get(line_id)
        if not line:
            errors.append(f"Line {str(line_id)[:20]} is not on this job's schedule.")
            continue
        name = sov_line_name(line)
        if line["scheduled_cents"] < 0:
            errors.append(f"{name}: a credit comes off the pay app as it is. Its amount can't be changed.")
            continue
        suggested = entries[line_id]["amount_cents"]
        was = drafted.get(line_id, 0)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            cents = suggested
        else:
            try:
                cents = sov.to_cents(raw, name)
            except sov.SovError as exc:
                errors.append(str(exc))
                continue
        room = sov.line_room(line, already["by_line"].get(line_id, 0))
        if cents < 0:
            errors.append(f"{name}: the amount can't be negative.")
        elif cents > room:
            errors.append(f"{name}: {money(cents)} is more than the {money(room)} left on the line. Extra scope is a change order.")
        elif cents != was:
            changes.append((name, suggested, cents))
            if cents == suggested:
                chosen.pop(line_id, None)
                to_suggested.append(line)
            else:
                chosen[line_id] = cents
        elif line_id in chosen and cents == suggested:
            chosen.pop(line_id, None)
    if errors:
        raise GateError("Amounts not changed.", errors)
    runs_to = period_to(job, period_end) if period_end is not None else ""
    notes = notes.strip()[:2000] if isinstance(notes, str) else ""
    new_date = bool(runs_to) and runs_to != draft.get("period_to")
    new_notes = bool(notes) and notes != (draft.get("progress_notes") or "")
    if not changes and not (new_date or new_notes):
        raise GateError("Nothing changed. Enter an amount that differs from the one on the draft.")
    # A line back at the suggested amount bills all that is recorded on it.
    moved = {line["id"]: line["done_milli"] for line in to_suggested}
    for line in to_suggested:
        line["done_milli"] = sov.anticipated(job, line)["done_milli"] if job.get("work_status") == "complete" \
            else sov.suggested_done(line, already["qty_by_line"].get(line["id"], 0))
    trial = sov.with_amounts(job, sov.this_period(job, skip=draft), chosen, skip=draft)
    if trial["total_cents"] + sum(co["amount_cents"] for co in unsigned_cos_in_billing(job)) <= 0:
        for line in to_suggested:
            line["done_milli"] = moved[line["id"]]
        raise GateError(f"That leaves nothing to bill. Leave an amount on a line, or delete {draft['number']}.")
    for name, suggested, cents in changes:
        audit(state, job_id, "payapp_amount_changed", f"{actor} changed {draft['number']}, {name}: suggested {money(suggested)}, final {money(cents)}.", actor)
    if new_date:
        audit(state, job_id, "payapp_period", f"{actor} changed the date {draft['number']} runs to: {draft.get('period_to') or 'not set'} → {runs_to}.", actor)
    draft["sov_overrides"] = chosen
    why = "with the amounts changed by hand" if changes else "with the date it runs to changed" if new_date else "with new notes"
    return _redraft(state, job, draft, why, period_end=runs_to if new_date else "", notes=notes if new_notes else "")


def _carry_send_choices(state: dict[str, Any], job: dict[str, Any], old: dict[str, Any], new: dict[str, Any]) -> None:
    """A redraft keeps what the person chose for the email (M4, B4 of the Oct 30 review): the job
    records added to the send and the files left out of it. Audited when there were any."""
    extra = [rid for rid in old.get("extra_record_ids") or [] if any(r["id"] == rid for r in job.get("records", []))]
    left_out = list(old.get("excluded_file_ids") or [])
    if not (extra or left_out):
        return
    new["extra_record_ids"] = extra
    new["excluded_file_ids"] = left_out
    names = [r["label"] for r in job.get("records", []) if r["id"] in extra]
    parts = ([f"{', '.join(names)} still added" if names else ""]
             + ([f"{len(left_out)} file{'' if len(left_out) == 1 else 's'} still left out"] if left_out else []))
    audit(state, job["id"], "payapp_attachments",
          f"{new['number']} keeps what was chosen for its email: {'; '.join(p for p in parts if p)}.", "duracash")


def _redraft(state: dict[str, Any], job: dict[str, Any], open_draft: dict[str, Any], why: str, period_end: str = "", notes: str = "") -> dict[str, Any]:
    """Replace a draft that has not gone to the client with a new one (B6 of the Oct 30 review):
    it keeps its number, paperwork, notes (unless new ones are given) and email choices."""
    open_draft["status"] = "void"
    open_draft["voided_at"] = utcnow()
    open_draft["replaced"] = True
    audit(state, job["id"], "draft_replaced", f"{open_draft['number']} ({money(open_draft['amount_cents'])}) replaced {why}.", "duracash")
    return draft_invoice(state, job["id"], actor="duracash", replaces=open_draft, period_end=period_end or open_draft.get("period_to") or "", notes=notes)


def _with_client(draft: dict[str, Any] | None) -> bool:
    """The draft went to the client for their review and they haven't answered."""
    return bool(draft and draft.get("pencil_status") == "sent")


def _pencil_email(state: dict[str, Any], job: dict[str, Any], draft: dict[str, Any], payer: dict[str, Any], actor: str) -> tuple[str, str]:
    """A pencil copy's subject and body; the review screen shows them before it goes (pencil_preview)."""
    subject = f"Pencil copy for review: {draft['number']} — {job['name']}"
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Here is the pencil copy of {draft['number']} for {job['name']}, attached as a PDF marked PENCIL COPY. "
        "Please confirm the quantities and change orders, or send corrections, before we submit the formal pay app.\n\n"
        f"{pay_app_text(state, job, draft)}\n\n{sign_off(state, actor)}"
    )
    return subject, body


def send_pencil(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    """Send the open draft to the payer as a pencil copy to agree quantities before the formal pay app."""
    job = _open_job(state, job_id)
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if not draft:
        raise GateError("Draft a pay app first. The draft is what goes for their review.")
    _need_playbook(state, job)
    payer = _payer(state, job["payer_id"])
    to = _send_to(payer)
    subject, body = _pencil_email(state, job, draft, payer, actor)
    _record_email(state, job_id, "pencil", to, subject, body, actor, payapp_invoice_id=draft["id"])
    draft["pencil_sent_at"] = utcnow()
    draft["pencil_status"] = "sent"
    job["external_status"] = f"Pencil copy with {payer.get('approver') or payer['name']} for review"
    audit(state, job_id, "pencil_sent", f"{actor} sent {draft['number']} to {payer['name']} as a pencil copy.", actor)
    return job


def record_pencil_response(state: dict[str, Any], job_id: str, agreed: bool, note: str = "", actor: str = "owner") -> dict[str, Any]:
    """Record what the payer said about the pencil copy. Corrections go in the note."""
    job = _open_job(state, job_id)
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if not draft or draft.get("pencil_status") != "sent":
        raise GateError("No draft is out for their review.")
    draft["pencil_status"] = "agreed" if agreed else "corrections"
    draft["pencil_note"] = (note or "").strip()[:2000]
    draft["pencil_answered_at"] = utcnow()
    job["external_status"] = None
    if not agreed and draft["pencil_note"]:
        suggest_packet_item(state, job["payer_id"], draft["pencil_note"], "pencil-copy changes", job["name"])
    what = "agreed to" if agreed else "asked for corrections on"
    audit(state, job_id, "pencil_response", f"{actor} recorded that the client {what} the draft of {draft['number']} sent for their review. {draft['pencil_note']}".strip(), actor)
    return job


def resolve_packet_item(state: dict[str, Any], job_id: str, item_id: str, actor: str = "owner", reason: str = "") -> dict[str, Any]:
    """"I have this" (Decision 5): an override, not a check of a file, so it needs a reason. Who,
    when and why are kept on the item for the pay app's page and Review & send, and audited; the
    client's PDF and page show only the tick, as they do for any item that is in."""
    job = _open_job(state, job_id)
    item, invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Supporting document not found.")
    _still_a_draft(invoice)
    reason = (reason or "").strip()[:500] if isinstance(reason, str) else ""
    if not reason:
        _refuse("", {"reason": f"Enter why {item['label']} can go without a checked file."})
    _drop_unconfirmed(item)
    if is_waiver_item(item):
        # An override doesn't replace the answer for a file that is going out: one nobody has said
        # which waiver it is stays off the item (it is still on the job records).
        records = {r["id"]: r for r in job.get("records", [])}
        unanswered = [a for a in item.get("attachments", []) if (records.get(a.get("record_id")) or {}).get("file_ids") and waiver_answer(records[a["record_id"]]) not in WAIVER_ANSWERS]
        if unanswered:
            item["attachments"] = [a for a in item["attachments"] if a not in unanswered]
            item.setdefault("detached_record_ids", []).extend(a["record_id"] for a in unanswered if a.get("auto"))
            audit(state, job_id, "paperwork_detached", f"{actor} took {', '.join(a['name'] for a in unanswered)} off {item['label']}: nobody had said which waiver it is.", actor)
    item["present"] = True
    item["resolved_at"] = utcnow()
    item["marked_in_hand"] = True
    item["present_reason"] = reason
    item["override"] = {"by": actor, "at": item["resolved_at"], "reason": reason}
    item.pop("check", None)
    which = f" on {invoice['number']}" if invoice else ""
    audit(state, job_id, "packet_resolved", f"{actor} overrode {item['label']}{which} with I have this: {reason}.", actor)
    _refresh_draft_gate(job, state)
    return job


def payer_link(invoice: dict[str, Any], link_base: str | None, company_id: str) -> str:
    """The pay app's private page for the payer (P2-4), made the first time it is needed."""
    if not link_base or not company_id:
        return ""
    if not invoice.get("payer_token"):
        invoice["payer_token"] = secrets.token_urlsafe(24)
    return f"{link_base.rstrip('/')}/p/{company_id}.{invoice['payer_token']}"


UNCONDITIONAL_ATTACHED = "An unconditional waiver is attached. It gives up your lien rights before you are paid."

# Which waiver a file is, is the person's answer (Nov 6 redesign). A file on a waiver item goes once
# someone says which waiver it is, Conditional or Unconditional, with the app's guess picked first
# when it read one (waiver_guess, doc_check.reading; none picked when it couldn't tell). Any other
# file going out that reads as a lien waiver form, wherever its text says so, or
# is filed as a waiver, is asked about once: Is this a lien waiver? No, Yes, conditional or Yes,
# unconditional (waiver_questions). The answer is kept on the record with who, when and the files it
# was given for, and types the record, and setting or changing one takes send_payapps (Answerer).
# The owner gate (M14) is for an unconditional answer, or a record typed unconditional, where a
# conditional waiver is needed.
WAIVER_ANSWERS = ("conditional", "unconditional")
QUESTION_ANSWERS = ("no",) + WAIVER_ANSWERS


@dataclass(frozen=True)
class Answerer:
    """Who is setting a waiver answer, as the server knows them from their profile: setting or
    changing one, by any route (the question, Confirm on a waiver item, attaching with an answer,
    the record's waiver type), takes send_payapps; on a retainage request, ask_retainage does too.
    An unconditional answer is taken back only by whoever gave it or an owner. None (scripts and
    tests of other rules) is anyone."""
    send_payapps: bool = True
    ask_retainage: bool = True
    owner: bool = True
    id: str = ""


NOT_YOURS_TO_ANSWER = "Someone who can send pay apps says which waiver this is."


def _retainage_packet(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The documents a retainage request attaches: the closeout list, or on a job from before it
    the older packet."""
    return job.get("closeout_packet") or job.get("packet") or []


def _on_retainage_request(job: dict[str, Any], item: dict[str, Any] | None = None, record: dict[str, Any] | None = None) -> bool:
    """An item on the closeout list (the retainage request's), or a record on one and on no pay app."""
    closeout = job.get("closeout_packet") or []
    if item is not None:
        return any(x is item for x in closeout)
    if record is None:
        return False
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if draft and (record["id"] in (draft.get("extra_record_ids") or []) or any(a.get("record_id") == record["id"] for x in draft.get("packet") or [] for a in x.get("attachments", []))):
        return False
    return any(a.get("record_id") == record["id"] for x in closeout for a in x.get("attachments", []))


def _may_answer(answerer: "Answerer | None", retainage: bool) -> bool:
    return answerer is None or answerer.send_payapps or (retainage and answerer.ask_retainage)


def is_waiver_item(item: dict[str, Any]) -> bool:
    return item.get("waiver_kind") in WAIVER_KINDS or (_record_kinds_for(item) or ("",))[0] in {"waiver", "final_waiver"}


def _item_word(item: dict[str, Any] | None) -> str:
    """The waiver an item needs: conditional, unless it asks for an unconditional one."""
    return "unconditional" if str((item or {}).get("waiver_kind") or "").startswith("unconditional") else "conditional"


def waiver_answer(record: dict[str, Any]) -> str:
    """The person's answer for the record's files as they are now: no, conditional or
    unconditional. Blank when nobody has answered for these files."""
    said = record.get("waiver_answer") or {}
    return str(said.get("kind") or "") if said and set(said.get("file_ids") or []) == set(record.get("file_ids") or []) else ""


def _read_now(record: dict[str, Any]) -> dict[str, Any]:
    """What the record's files read as (doc_check.waiver_reading), if they are the files that were read."""
    read = record.get("waiver_read") or {}
    return read if read and set(read.get("file_ids") or []) == set(record.get("file_ids") or []) else {}


def _read_waiver(state: dict[str, Any] | None, record: dict[str, Any], read_file: Any) -> dict[str, Any]:
    """Read a record's files for the waiver question, once for each set of files, kept on the record."""
    if read_file is not None and state is not None and record.get("file_ids") and not _read_now(record):
        ids = set(record["file_ids"])
        metas = [f for f in state.get("files", []) if f["id"] in ids]
        if metas:
            got = doc_check.waiver_reading([(m["name"], str(m.get("ext") or "").lower(), read_file(m)) for m in metas])
            record["waiver_read"] = {**got, "file_ids": sorted(ids)}
    return _read_now(record)


def waiver_guess(record: dict[str, Any]) -> str:
    """The answer picked first: what the files read as, and nothing else. A photo, a scan, a form
    that says neither word or both has no informed guess, so none is picked and the page says the
    app couldn't tell. The record's type and the waiver the item needs are not a reading."""
    return str(_read_now(record).get("guess") or "")


def _say_which(state: dict[str, Any], job: dict[str, Any], record: dict[str, Any], kind: str, actor: str, stage: str = "progress", where: str = "",
               answerer: "Answerer | None" = None) -> None:
    """Keep a person's answer on the record, with who, when and its files, and type the record.
    where: the audit's words for it, or blank when the caller's own audit row says it. An
    unconditional answer is changed to another only by whoever gave it or an owner; any change of
    an answer that stood is audited."""
    said = record.get("waiver_answer") or {}
    # An answer stands for the files it was given for, and for those and more: adding a file to the
    # record doesn't make it someone else's to take back.
    before = str(said.get("kind") or "") if set(said.get("file_ids") or []) & set(record.get("file_ids") or []) else ""
    if before == "unconditional" and kind != "unconditional" and answerer is not None and not answerer.owner:
        if not ((said.get("by_id") and said["by_id"] == answerer.id) or (not said.get("by_id") and said.get("by") == actor)):
            raise GateError(f"{record['label']} was marked unconditional by {said.get('by') or 'someone'}. Only {said.get('by') or 'they'} or an owner can change it.")
    if before and before != kind:
        audit(state, job["id"], "waiver_answer_changed", f"{actor} changed {record['label']} from {before} to {kind} (first said by {said.get('by') or 'someone'}).", actor)
    record["waiver_answer"] = {"kind": kind, "by": actor, "at": utcnow(), "file_ids": list(record.get("file_ids") or [])}
    if answerer is not None and answerer.id:
        record["waiver_answer"]["by_id"] = answerer.id
    if kind in WAIVER_ANSWERS:
        record["waiver_kind"] = f"{kind}_{stage}"
    if where:
        said = "not a lien waiver" if kind == "no" else f"the {kind} waiver"
        audit(state, job["id"], "waiver_answer", f"{actor} said {record['label']} is {said}{where}.", actor)


def _filed_records(job: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    """The records on an item that have files."""
    records = {r["id"]: r for r in job.get("records", [])}
    return [records[a["record_id"]] for a in item.get("attachments", []) if a.get("record_id") in records and records[a["record_id"]].get("file_ids")]


def item_question(job: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """What "Which waiver is this?" shows on a waiver item: the answer picked first, only when the
    files all read (unconditional when any of them reads so), blank when the app couldn't tell; what
    the files read as, for the warning beside it; and "both" when a file holds both forms."""
    filed = _filed_records(job, item)
    guesses = [waiver_guess(r) for r in filed]
    both = any(_read_now(r).get("both") for r in filed)
    reads = set(guesses) - {""}
    guess = "" if both or not filed else "unconditional" if "unconditional" in reads else "conditional" if all(guesses) else ""
    out = {"guess": guess, "reads_as": "" if both else "unconditional" if "unconditional" in reads else "conditional" if reads else ""}
    if both:
        out["both"] = True
    return out


def _ask_which_waiver(job: dict[str, Any], packet: list[dict[str, Any]], retainage: bool = False) -> list[dict[str, Any]]:
    """A waiver item that is in with a file nobody has said which waiver it is (in from before the
    question, or a file added to its record since) waits for the answer; an override ("I have this")
    doesn't replace the answer for a file that is going out. The final waiver that went out after
    payment stays on the retainage request (retainage), not on a later pay app. Returns the items
    now waiting."""
    out = []
    for item in packet or []:
        if not item.get("present") or not is_waiver_item(item):
            continue
        filed = _filed_records(job, item)
        if not any(waiver_answer(r) not in WAIVER_ANSWERS and not (retainage and _sent_for_this_job(job, r)) for r in filed):
            continue
        item["present"] = False
        for key in ("confirmed_by", "confirmed_at", "resolved_at", "looked_as", "unread", "override", "marked_in_hand", "present_reason"):
            item.pop(key, None)
        for a in item.get("attachments", []):
            a["unconfirmed"] = True
        out.append(item)
    return out


def _sent_for_this_job(job: dict[str, Any], rec: dict[str, Any]) -> bool:
    """The final waiver that went out after payment (send_waiver): already with the client, for a
    pay app or retainage release of this job. It fills the closeout's final waiver item and goes with
    the retainage request; added to a later pay app's email it is asked about like any other."""
    tie = _waiver_tie(rec)
    return bool(rec.get("sent_as_final")) and bool(tie) and (tie == EARLIER_PAYMENT or tie in {inv["id"] for inv in job["invoices"]} or tie in {r["id"] for r in job.get("retainage_waivers", [])})


def _going_records(job: dict[str, Any], packet: list[dict[str, Any]], extra_ids: Any = ()) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """The records going out with a packet, each with the item it is on (None when added to the
    send): an item's attachments that aren't waiting to be confirmed, then the ones added."""
    records = {r["id"]: r for r in job.get("records", [])}
    pairs = [(records.get(a.get("record_id")), item) for item in packet or [] for a in _going(item)]
    pairs += [(records.get(rid), None) for rid in extra_ids or []]
    return [(rec, item) for rec, item in pairs if rec]


def _records_going(job: dict[str, Any], invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """The records going out with this pay app: attached to a paperwork item or added to the send."""
    seen: dict[str, dict[str, Any]] = {}
    for rec, _item in _going_records(job, invoice.get("packet") or [], invoice.get("extra_record_ids") or []):
        seen.setdefault(rec["id"], rec)
    return list(seen.values())


def _left_out(rec: dict[str, Any], excluded: set[str]) -> bool:
    return bool(rec.get("file_ids")) and set(rec["file_ids"]) <= excluded


def waiver_questions(job: dict[str, Any], packet: list[dict[str, Any]], extra_ids: Any = (), excluded: Any = (),
                     state: dict[str, Any] | None = None, read_file: Any = None) -> list[dict[str, Any]]:
    """The records going out, not on a waiver item (which asks which waiver it is), that read as a
    lien waiver form or are filed as a waiver, and that nobody has answered "Is this a lien waiver?"
    for yet. With read_file, a record not read yet is read."""
    on_item = {a.get("record_id") for item in packet or [] if is_waiver_item(item) for a in item.get("attachments", [])}
    excluded = set(excluded or [])
    out: list[dict[str, Any]] = []
    for rec, _item in _going_records(job, packet, extra_ids):
        if rec["id"] in on_item or rec in out or not rec.get("file_ids") or _left_out(rec, excluded):
            continue
        read = _read_waiver(state, rec, read_file)
        if not waiver_answer(rec) and (rec.get("kind") == "waiver" or read.get("form")):
            out.append(rec)
    return out


def _is_unconditional(rec: dict[str, Any]) -> bool:
    """Someone said it is the unconditional waiver; with no answer for its files, it is typed so
    (filed as a waiver, or named one)."""
    said = waiver_answer(rec)
    if said:
        return said == "unconditional"
    return record_waiver_kind(rec).startswith("unconditional") and (rec.get("kind") == "waiver" or "waiver" in str(rec.get("label") or "").lower())


def _unconditional_going(job: dict[str, Any], packet: list[dict[str, Any]], extra_ids: Any = (), excluded: Any = (), retainage: bool = False) -> list[dict[str, Any]]:
    """Unconditional waivers going where a conditional one is needed: on any item but one asking for
    an unconditional waiver (for an earlier payment), or added to the send. The final waiver that
    went out after payment is already with the client, and goes with the retainage request (retainage)
    it fills the closeout for; on a later pay app it is gated like any other."""
    excluded = set(excluded or [])
    out: list[dict[str, Any]] = []
    for rec, item in _going_records(job, packet, extra_ids):
        if item is not None and is_waiver_item(item) and _item_word(item) == "unconditional":
            continue
        if rec in out or (retainage and _sent_for_this_job(job, rec)) or not _is_unconditional(rec) or _left_out(rec, excluded):
            continue
        out.append(rec)
    return out


def unconditional_waivers_attached(job: dict[str, Any], invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """The unconditional waivers going out with this pay app where it needs the conditional one
    (M14): sending them takes an owner and a reason."""
    return _unconditional_going(job, invoice.get("packet") or [], invoice.get("extra_record_ids") or [], invoice.get("excluded_file_ids") or [])


def _why_unconditional(rec: dict[str, Any]) -> str:
    said = rec.get("waiver_answer") or {}
    if waiver_answer(rec) == "unconditional":
        return f"{said.get('by') or 'someone'} said it is the unconditional waiver"
    kind = record_waiver_kind(rec)
    return WAIVER_KINDS[kind] if kind in WAIVER_KINDS else "unconditional"


def _record_rows(state: dict[str, Any], records: list[dict[str, Any]], extra: Any = ()) -> list[dict[str, Any]]:
    names = {f["id"]: f.get("name", "") for f in state.get("files", [])}
    extra = set(extra or [])
    return [{"record_id": r["id"], "label": r["label"], "extra": r["id"] in extra, "file_ids": list(r.get("file_ids") or []),
             "file_names": [names[f] for f in r.get("file_ids") or [] if names.get(f)]} for r in records]


def _unconditional_rows(state: dict[str, Any], records: list[dict[str, Any]], extra: Any = ()) -> list[dict[str, Any]]:
    """What a page warns about (M14), the same rule as the send: each record by name, why, and its
    files so a person can take it off."""
    return [{**row, "why": _why_unconditional(r)} for row, r in zip(_record_rows(state, records, extra), records)]


def _question_rows(state: dict[str, Any], records: list[dict[str, Any]], extra: Any = ()) -> list[dict[str, Any]]:
    """The records a page asks "Is this a lien waiver?" about, with the answer picked first."""
    return [{**row, "guess": waiver_guess(r), **({"both": True} if _read_now(r).get("both") else {})}
            for row, r in zip(_record_rows(state, records, extra), records)]


def answer_waiver_question(state: dict[str, Any], job_id: str, record_id: str, answer: str, actor: str = "owner",
                           shown: list[str] | None = None, answerer: "Answerer | None" = None) -> dict[str, Any]:
    """Is this a lien waiver? A person's answer for a record going out (waiver_questions): no, yes
    and conditional, or yes and unconditional, which then takes the owner where a conditional one
    is needed. shown is the files the page was showing: a file added since asks again."""
    job = _open_job(state, job_id)
    rec = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not rec:
        raise GateError("That record is not on this job.")
    if answer not in QUESTION_ANSWERS:
        _refuse("", {"answer": f"Say whether {rec['label']} is a lien waiver: no, yes and conditional, or yes and unconditional."})
    if not _may_answer(answerer, _on_retainage_request(job, record=rec)):
        raise GateError(f"Someone who can send pay apps says whether {rec['label']} is a lien waiver.")
    if shown is not None and set(shown) != set(rec.get("file_ids") or []):
        raise GateError(f"{rec['label']} has changed since this page was opened. Look at its files again.", extra={"review_changed": True})
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    stage = "final" if (draft or {}).get("final") or (not draft and job.get("work_status") == "complete") else "progress"
    _say_which(state, job, rec, answer, actor, stage, f" going with {draft['number']}" if draft else " going with the closeout documents", answerer)
    if not job.get("closed"):
        _refresh_draft_gate(job, state)
    return job


def _pay_app_email(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any], payer: dict[str, Any],
                   files: list[str], actor: str, link: str) -> str:
    """The email a pay app goes out with; Review & send shows it first (email_preview)."""
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Please find {'the final pay application' if invoice['final'] else 'pay application'} {invoice['number']} for {job['name']} attached as a PDF."
        + (f" {len(files)} supporting document{'s are' if len(files) != 1 else ' is'} attached too." if files else "")
        + (f" Payment due {invoice['due_on']}." if invoice.get("due_on") else "")
        + f"\n\n{pay_app_text(state, job, invoice)}"
    )
    if link:
        body += f"\n\nSee this pay app and its supporting documents, and tell us it reached you:\n{link}"
    return body + f"\n\n{sign_off(state, actor)}"


# Who sends it and the private link are known only when it goes; the page fills in the name.
PREVIEW_SENDER = "[Your name]"
PREVIEW_LINK = "[private link to this pay app]"


def email_preview(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> dict[str, str]:
    """A draft's email as it would go now, for Review & send. Changes nothing on the draft."""
    payer = _payer(state, job["payer_id"])
    going = {**invoice, "final": is_final(job)}
    terms = _int_or(payer.get("payment_terms_days"), None)
    if terms:
        going["due_on"] = (datetime.now(timezone.utc).date() + timedelta(days=terms)).isoformat()
    return {"subject": pay_app_subject(state, job, going), "stamp": review_stamp(state, job, invoice),
            "body": _pay_app_email(state, job, going, payer, _packet_file_ids(state, job, going), PREVIEW_SENDER, PREVIEW_LINK)}


def review_stamp(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> str:
    """What Review & send showed: the pay app, its amount and the files going. Sent back with
    Approve & send, so a change made since (in another session) asks for a fresh look."""
    what = [invoice["id"], str(invoice["amount_cents"]), *_packet_file_ids(state, job, invoice)]
    return hashlib.sha256("|".join(what).encode()).hexdigest()[:16]


def _need_waiver_answers(state: dict[str, Any], job: dict[str, Any], packet: list[dict[str, Any]], extra_ids: Any, excluded: Any, read_file: Any) -> None:
    """A file going out that reads as a lien waiver form, or is filed as a waiver, goes once a person
    has said whether it is one (waiver_questions). What was read stays for the page."""
    asked = waiver_questions(job, packet, extra_ids, excluded, state, read_file)
    if asked:
        names = ", ".join(r["label"] for r in asked)
        raise GateError(f"Say whether {names} {'is a lien waiver' if len(asked) == 1 else 'are lien waivers'}, then send.",
                        [f"{r['label']}: is this a lien waiver?" for r in asked], extra={"waiver_question": True}, keep_changes=True)


def approve_and_send(
    state: dict[str, Any], job_id: str, actor: str = "owner", link_base: str | None = None, company_id: str = "",
    unconditional_override: str = "", reviewed: str | None = None, read_file: Any = None,
) -> dict[str, Any]:
    """Send the open draft. A signed unconditional waiver going with it is refused (M14) unless
    unconditional_override gives the reason (the server lets only an owner give one); the
    override is kept on the pay app and in the audit. reviewed is the stamp of what Review & send
    showed (review_stamp): the server always passes it, so a send needs a look at what goes.
    None is for callers with no page (scripts, tests of other rules)."""
    job = _open_job(state, job_id)
    invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] in {"draft", "awaiting_approval"}), None)
    # A stale screen first: what it says stands in the way may not be what stands in the way now.
    if reviewed and invoice and reviewed != review_stamp(state, job, invoice):
        raise GateError(f"{invoice['number']} changed since you opened it. Look it over again, then send.",
                        extra={"review_changed": True, "invoice_id": invoice["id"]})
    if invoice and _ask_which_waiver(job, invoice.get("packet") or []):
        _refresh_draft_gate(job)
        raise GateError("Pay app blocked.", blocker_lines(job), extra={"waiver_question": True}, keep_changes=True)   # the page shows the question
    blockers = blocker_lines(job)
    if blockers:
        raise GateError("Pay app blocked.", blockers)
    if not invoice:
        raise GateError("No draft pay app to send.")
    _need_playbook(state, job)
    if reviewed is not None and reviewed != review_stamp(state, job, invoice):
        raise GateError(f"{invoice['number']} changed since you opened it. Look it over again, then send." if reviewed
                        else f"Open {invoice['number']} and look it over, then send.", extra={"review_changed": True, "invoice_id": invoice["id"]})
    payer = _payer(state, job["payer_id"])
    to = _send_to(payer)
    _need_waiver_answers(state, job, invoice.get("packet") or [], invoice.get("extra_record_ids") or [], invoice.get("excluded_file_ids") or [], read_file)
    unconditional = unconditional_waivers_attached(job, invoice)
    override = _reason(unconditional_override) if unconditional else ""
    if unconditional and not override:
        raise GateError(UNCONDITIONAL_ATTACHED, [f"{r['label']} ({_why_unconditional(r)})" for r in unconditional],
                        fields={"unconditional_override": "Enter why it goes with this pay app, or take it off."},
                        extra={"unconditional_waiver": True}, keep_changes=True)   # what was read goes on the page
    # The bill can stop being the final one under a confirmed waiver (a change order written since,
    # say): that waiver isn't sent as the other type without a look.
    invoice["final"] = is_final(job)
    if invoice.get("packet_id") != "job-packet":
        flipped = _name_conditional_waiver(job, invoice)
        if flipped:
            invoice["status"] = "draft"   # kept with the refusal: the page shows it waiting, with Confirm
            raise GateError(f"{invoice['number']} changed since you opened it. Look at {flipped[0]['label']} again, then send.",
                            [f"{item['label']}: {item['check']['problems'][-1]}" for item in flipped],
                            extra={"review_changed": True, "invoice_id": invoice["id"]}, keep_changes=True)
    invoice["status"] = "sent"
    invoice["sent_at"] = utcnow()
    invoice["approved_by"] = actor   # shown as "sent by" (R4-05)
    _settlements_follow(state, job, f"{invoice['number']} went out")
    # The contract as it went out: later change orders and releases don't change this pay app.
    invoice["contract_at_send"] = payapp_pdf.contract_terms(job)
    # The customer's payment terms set a due date from the day it goes out (R4-04).
    terms = _int_or(payer.get("payment_terms_days"), None)
    if terms:
        invoice["due_on"] = (datetime.now(timezone.utc).date() + timedelta(days=terms)).isoformat()
    # The first stage of the waiver (M13): the conditional type that went with the pay app.
    cond = next((item for item in invoice.get("packet") or [] if str(item.get("waiver_kind", "")).startswith("conditional")
                 and item.get("present")), None)
    if cond:
        invoice["conditional_waiver_kind"] = cond["waiver_kind"]
        records = {r["id"]: r for r in job.get("records", [])}
        for att in cond.get("attachments", []):
            rec = records.get(att.get("record_id"))
            if rec and rec.get("kind") == "waiver":
                if record_waiver_kind(rec):  # a type nobody said isn't assumed (a person confirmed it, or it stays blank)
                    rec.setdefault("waiver_kind", cond["waiver_kind"])
                if not _waiver_tie(rec):
                    rec["invoice_id"] = invoice["id"]
    if override:
        invoice["unconditional_override"] = {"by": actor, "at": utcnow(), "reason": override,
                                             "record_ids": [r["id"] for r in unconditional]}
        audit(state, job_id, "unconditional_override",
              f"{actor} sent {invoice['number']} with an unconditional waiver attached ({', '.join(r['label'] for r in unconditional)}): {override}.", actor)
    job["external_status"] = f"Submitted to {payer['name']}, awaiting {payer['approver']}"
    subject = pay_app_subject(state, job, invoice)
    files = _packet_file_ids(state, job, invoice)
    body = _pay_app_email(state, job, invoice, payer, files, actor, payer_link(invoice, link_base, company_id))
    _record_email(state, job_id, "pay_app", to, subject, body, actor, attachments=files, cc=_copy_list(payer),
                  payapp_invoice_id=invoice["id"])
    audit(
        state,
        job_id,
        "approve_send",
        f"{actor} approved and sent {invoice['number']} for {money(invoice['amount_cents'])} to {to}.",
        actor,
    )
    return job


def _aware(iso: str) -> datetime:
    when = datetime.fromisoformat(iso)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def follow_up_due(job: dict[str, Any], payer: dict[str, Any]) -> datetime | None:
    """When the next follow-up is due (R4-06): the customer's days between follow-ups after the
    last contact on the oldest unpaid pay app, or the date noted after a conversation (note_contact) when
    no follow-up has gone out since. None when nothing is out for payment."""
    unpaid = [inv for inv in job["invoices"] if inv["status"] in OPEN_FOR_PAYMENT]
    if not unpaid:
        return None
    last = unpaid[0].get("last_follow_up_at") or unpaid[0].get("sent_at")
    if not last:
        return None
    if job.get("expected_payment_on") and (job.get("contact_noted_at") or "") >= last:
        return datetime.combine(date.fromisoformat(job["expected_payment_on"]), datetime.min.time(), tzinfo=timezone.utc)
    due = _aware(last) + timedelta(days=_int_or(payer.get("follow_up_days"), 7))
    # Approved on the customer's page with a date to expect payment (R4-08): nothing to chase
    # until the day after it.
    promised = expected_payment(unpaid[0])
    if promised:
        due = max(due, datetime.combine(promised + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    return due


def expected_payment(invoice: dict[str, Any]) -> date | None:
    """The date the customer said payment would come when they approved the pay app (R4-08)."""
    if invoice.get("status") != "approved" or not invoice.get("expected_payment_on"):
        return None
    try:
        return date.fromisoformat(invoice["expected_payment_on"])
    except ValueError:
        return None


ORDINALS = {2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth", 6: "Sixth", 7: "Seventh", 8: "Eighth", 9: "Ninth"}


def _owed_on(job: dict[str, Any], invoice: dict[str, Any]) -> int:
    paid = sum(p["amount_cents"] for p in job["payments"] if p.get("invoice_id") == invoice["id"])
    return max(0, invoice["amount_cents"] - paid)


def record_follow_up(
    state: dict[str, Any], job_id: str, actor: str = "duracash", link_base: str | None = None, company_id: str = "",
    early: bool = False,
) -> dict[str, Any]:
    """One follow-up email for every unpaid pay app on the job, once the customer's window has
    passed (R4-06): "PA-02 $X sent 9/30; PA-03 $Y sent 10/31; total $Z". Each pay app counts its
    follow-ups ("Second follow-up: ..."); once the customer is past the days they usually take to
    pay the email is firmer, and from the third it copies their escalation email.
    `early`: the person chose "Follow up now" on the job page before then (R3-04)."""
    job = _open_job(state, job_id)
    unpaid = [inv for inv in job["invoices"] if inv["status"] in OPEN_FOR_PAYMENT]
    if not unpaid:
        raise GateError("No sent pay app to follow up.")
    oldest = unpaid[0]
    payer = _payer(state, job["payer_id"])
    to = _send_to(payer)
    days = _int_or(payer.get("follow_up_days"), 7)
    due = follow_up_due(job, payer)
    if due and not early and datetime.now(timezone.utc) < due:
        # Say what holds it: a date noted after a call, the customer's approval, or the days between contacts.
        last = oldest.get("last_follow_up_at") or oldest.get("sent_at") or ""
        promised = expected_payment(oldest)
        if job.get("expected_payment_on") and (job.get("contact_noted_at") or "") >= last:
            why = f"a conversation on {job['contact_noted_at'][:10]} noted to check again then"
        elif promised and due.date() == promised + timedelta(days=1):
            why = f"{oldest.get('approved_by_payer') or payer['name']} approved it for payment by {promised.isoformat()}"
        else:
            why = f"{payer['name']} expects {days} days between contacts"
        raise GateError(f"Follow-up on {oldest['number']} is not due until {due.date().isoformat()} ({why}).")
    now = utcnow()
    for inv in unpaid:
        inv["last_follow_up_at"] = now
        inv["follow_up_count"] = int(inv.get("follow_up_count") or 0) + 1
    count = oldest["follow_up_count"]
    job.pop("expected_payment_on", None)   # a date noted after a call is used up once a follow-up goes out
    who = payer.get("approver") or payer["name"]
    job["external_status"] = f"Follow-up sent to {who}"
    job["follow_ups"] = job.get("follow_ups", 0) + 1
    numbers = ", ".join(inv["number"] for inv in unpaid)
    lead = "Following up" if count == 1 else f"{ORDINALS[count]} follow-up" if count in ORDINALS else f"Follow-up {count}"
    subject = f"{lead}: {numbers} — {job['name']}"
    owed = [(inv, _owed_on(job, inv)) for inv in unpaid]
    listing = "; ".join(f"{inv['number']} {money(cents)} sent {(inv.get('sent_at') or '')[:10]}" for inv, cents in owed)
    if len(owed) > 1:
        listing += f"; total {money(sum(cents for _, cents in owed))}"
    usual = _int_or(payer.get("usual_approval_days"), 14)
    late = bool(oldest.get("sent_at")) and datetime.now(timezone.utc) >= _aware(oldest["sent_at"]) + timedelta(days=usual)
    if late:
        body = (
            f"{who},\n\nThis is our {lead.lower() if count > 1 else 'follow-up'} on {job['name']}, now past the {usual} days "
            f"you usually take to pay: {listing}. Please tell us the date payment will go out, or what is holding it up."
        )
    else:
        body = f"Hi {who}, checking on {job['name']}: {listing}. Is anything missing on your side?"
    link = payer_link(oldest, link_base, company_id)
    if link:
        body += f"\n\nThe pay app and its supporting documents: {link}"
    body += f"\n\n{sign_off(state, actor)}"
    escalate = str(payer.get("escalation_email") or "").strip()
    cc = [escalate] if count >= 3 and escalate and escalate.lower() != to.lower() else []
    _record_email(state, job_id, "follow_up", to, subject, body, actor, cc=cc)
    audit(
        state,
        job_id,
        "follow_up",
        f"{actor} followed up on {numbers} with {payer.get('approver', '')} at {payer['ap_email']}"
        + (f" ({lead.lower()}{', copied ' + escalate if cc else ''})" if count > 1 else "") + f". Cadence: {days} days.",
        actor,
    )
    return job


def _log_conversation(state: dict[str, Any], job: dict[str, Any], text: str, day: date | None, actor: str, now: str) -> None:
    """What makes a conversation with the client more than a note (R4-06): while a pay app waits
    for payment it counts as a follow-up for the timing, and with a date the next follow-up waits
    until then. It is in the Activity log and in Recent contact either way."""
    waiting = [inv for inv in job["invoices"] if inv["status"] in OPEN_FOR_PAYMENT]
    for inv in waiting:
        inv["last_follow_up_at"] = now
    if waiting:
        job["contact_noted_at"] = now
        if day:
            job["expected_payment_on"] = day.isoformat()
        else:
            job.pop("expected_payment_on", None)
    payer = _payer(state, job["payer_id"])
    who = payer.get("approver") or payer["name"]
    job["last_contact"] = {"at": now, "by": actor, "text": text, "next_on": day.isoformat() if day else ""}
    audit(state, job["id"], "contact_noted", f"Conversation with {who}: {text}" + (f" Next check {day.isoformat()}." if day else "") + f" ({actor})", actor)


def note_contact(state: dict[str, Any], job_id: str, text: str, next_on: Any = "", actor: str = "owner") -> dict[str, Any]:
    """Log a conversation (R4-06): what the client said, kept on the job's records as a record of
    kind "conversation". Older ones are notes labelled "Spoke to ..."; they stay as they are.
    A date to check again needs a pay app waiting for payment: that is the follow-up it holds."""
    job = _open_job(state, job_id)
    text = " ".join(str(text or "").split())[:500]
    problems = {}
    if not text:
        problems["text"] = "Enter what they said, like \"Check goes out Friday\"."
    day = _day(next_on, "next_on", "Enter the date to check again, like 2026-11-07.")
    if day and day < datetime.now(timezone.utc).date() - timedelta(days=1):
        problems["next_on"] = "Enter today or a later date to check again."
    elif day and not any(inv["status"] in OPEN_FOR_PAYMENT for inv in job["invoices"]):
        problems["next_on"] = "No sent pay app is waiting for payment, so there is no follow-up to hold."
    _refuse("Nothing was saved.", problems)
    payer = _payer(state, job["payer_id"])
    now = utcnow()
    job.setdefault("records", []).append({
        "id": "rec-" + secrets.token_hex(3), "kind": "conversation", "label": f"Conversation with {payer.get('approver') or payer['name']}",
        "note": text + (f" Check again {day.isoformat()}." if day else ""), "added_at": now, "added_by": actor,
    })
    _log_conversation(state, job, text, day, actor, now)
    return job


def match_payment(
    state: dict[str, Any],
    job_id: str,
    amount_cents: int,
    reference: str,
    actor: str = "duracash",
    invoice_id: str | None = None,
    received_on: str | None = None,
    qbo_payment_id: str | None = None,
    qbo_total_cents: int | None = None,
) -> dict[str, Any]:
    """Record money received against a pay app. `received_on` (YYYY-MM-DD) is the date the books
    show, for imports; without it the payment is dated now. `qbo_payment_id` is QuickBooks' id
    for the payment (qbo.py) and `qbo_total_cents` its whole amount: one QuickBooks payment is
    recorded no more than its amount in the whole company, in parts when it was split over pay
    apps (R5-03), and once when its amount isn't given."""
    job = _open_job(state, job_id)
    qbo_payment_id = str(qbo_payment_id or "").strip()[:40] or None
    if qbo_payment_id:
        parts = [p["amount_cents"] for j in state["jobs"] for p in j.get("payments", []) if p.get("qbo_payment_id") == qbo_payment_id]
        whole = qbo_total_cents if isinstance(qbo_total_cents, int) and not isinstance(qbo_total_cents, bool) else None
        amount = amount_cents if isinstance(amount_cents, int) and not isinstance(amount_cents, bool) else 0
        if parts and (whole is None or sum(parts) + amount > whole):
            raise GateError("This QuickBooks payment is already recorded.")
    open_invoices = [inv for inv in job["invoices"] if inv["status"] in OPEN_FOR_PAYMENT]
    # A pay app settled short still takes the rest if the customer pays it after all (R4-01):
    # named by id, or on its own when nothing else is open.
    short = [inv for inv in job["invoices"] if inv["status"] == "paid" and inv.get("short_pay_cents")]
    # A refund the contract no longer leaves room for (a change order signed after it): the client
    # pays it back, on the last pay app sent, which stays as it went.
    owed_back = job_money(job)["refund_owed_cents"]
    back_on = next((inv for inv in reversed(job["invoices"]) if inv["status"] in BILLED_STATUSES), None) if owed_back else None
    if not open_invoices and not short and not back_on:
        raise GateError("No open invoice to match.")
    if invoice_id:
        invoice = next((inv for inv in open_invoices + short + ([back_on] if back_on else []) if inv["id"] == invoice_id), None)
        if not invoice:
            _refuse("", {"invoice_id": "Pick a pay app that is waiting for payment."})
    elif len(open_invoices) == 1:
        invoice = open_invoices[0]
    elif not open_invoices and len(short) == 1:
        invoice = short[0]
    elif not open_invoices and back_on:
        invoice = back_on
    else:
        raise GateError(
            "More than one pay app is open. Choose which pay app this payment is for.",
            [f"{inv['number']} ({money(inv['amount_cents'])})" for inv in open_invoices + short],
            {"invoice_id": "Pick the pay app this payment is for."},
        )
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        _refuse("", {"amount_cents": "Enter the payment amount, more than $0."})
    already = sum(p["amount_cents"] for p in job["payments"] if p["invoice_id"] == invoice["id"])
    remaining = max(0, invoice["amount_cents"] - already) + (owed_back if invoice is back_on else 0)
    if amount_cents > remaining:
        _refuse("", {"amount_cents": f"Enter no more than the {money(remaining)} still owed on {invoice['number']}. You entered {money(amount_cents)}."})
    reference = reference.strip() if isinstance(reference, str) else ""
    # Two QuickBooks payments with different ids are two payments, even with one reference ("ACH").
    if reference and any(p.get("reference") == reference and not (qbo_payment_id and p.get("qbo_payment_id") and p["qbo_payment_id"] != qbo_payment_id)
                         for p in job["payments"]):
        _refuse("", {"reference": f"Use another reference: {reference} is already recorded on this job."})
    if not reference:
        used = {p.get("reference") for p in job["payments"]}
        n = len(job["payments"]) + 1
        while f"manual-{n}" in used:
            n += 1
        reference = f"manual-{n}"
    payment = {
        "id": f"pay-{len(job['payments']) + 1}",
        "amount_cents": amount_cents,
        "reference": reference,
        "invoice_id": invoice["id"],
        "matched_at": f"{received_on}T12:00:00+00:00" if received_on and re.fullmatch(r"\d{4}-\d{2}-\d{2}", received_on) else utcnow(),
    }
    if qbo_payment_id:
        payment["qbo_payment_id"] = qbo_payment_id
    job["payments"].append(payment)
    collected_for_invoice = sum(p["amount_cents"] for p in job["payments"] if p["invoice_id"] == invoice["id"])
    late = ""
    if invoice["status"] == "paid":
        # The rest of a short pay came in after all: less of it stays written off.
        was_short = int(invoice.get("short_pay_cents") or 0)
        invoice["short_pay_cents"] = max(0, invoice["amount_cents"] - collected_for_invoice)
        late = f" Short pay now {money(invoice['short_pay_cents'])}." if was_short else " Refund paid back."
    elif collected_for_invoice >= invoice["amount_cents"]:
        if _legacy_waiver_pending(job):
            job["legacy_waiver_pending"] = True
        invoice["status"] = "paid"
        due = _waiver_comes_due(state, job, invoice, invoice["number"])
        _sync_waiver(job)
        job["external_status"] = "Payment received, waiver not sent" if due else "Payment received"
    elif collected_for_invoice > 0:
        invoice["status"] = "partial"
        job["external_status"] = "Partial payment received"
    _refresh_draft_gate(job, state)   # what is left to collect decides the waiver on a final draft
    audit(
        state,
        job_id,
        "match_payment",
        f"Matched {money(amount_cents)} ({reference}) to {invoice['number']}.{late}",
        actor,
    )
    return job


def settle_short_pay(state: dict[str, Any], job_id: str, invoice_id: str, reason: str, actor: str = "owner") -> dict[str, Any]:
    """The customer paid part of a pay app and won't pay the rest (a backcharge, a disputed line):
    accept what came in, so the pay app reads paid and its lien waiver comes due (R4-01).
    The pay app keeps its amount and lines as they were sent (its PDF and the customer's page
    show what went out, and the next pay app's "previous certificates" count it in full); what
    was kept off it is stored beside it as short_pay_cents, which no longer counts as owed."""
    job = _open_job(state, job_id)
    invoice = next((inv for inv in job["invoices"] if inv["id"] == invoice_id), None)
    if not invoice or invoice["status"] not in OPEN_FOR_PAYMENT:
        raise GateError("Pick a pay app that is waiting for payment.")
    collected = sum(p["amount_cents"] for p in job["payments"] if p["invoice_id"] == invoice["id"])
    if collected <= 0:
        raise GateError(f"Record what came in on {invoice['number']} first. Short pay is accepted after a payment.")
    reason = _reason(reason)
    if not reason:
        _refuse("", {"reason": "Enter why it was paid short, like a backcharge for cleanup."})
    remaining = invoice["amount_cents"] - collected
    if remaining <= 0:
        raise GateError(f"{invoice['number']} is paid in full.")
    invoice["short_pay_cents"] = remaining
    invoice["short_pay_reason"] = reason
    invoice["short_pay_at"] = utcnow()
    invoice["short_pay_by"] = actor
    if _legacy_waiver_pending(job):
        job["legacy_waiver_pending"] = True
    invoice["status"] = "paid"
    due = _waiver_comes_due(state, job, invoice, invoice["number"])
    _sync_waiver(job)
    job["external_status"] = "Short pay accepted, waiver not sent" if due else "Short pay accepted"
    _refresh_draft_gate(job, state)
    audit(
        state,
        job_id,
        "short_pay",
        f"{actor} accepted {money(collected)} on {invoice['number']} ({money(invoice['amount_cents'])} billed); "
        f"{money(remaining)} short: {reason}.",
        actor,
    )
    return job


# The four lien waiver types. Duracash records which one went out; it never writes the
# waiver itself, because state law sets the forms (the contractor drops their signed form
# on the job record and sending attaches it).
WAIVER_KINDS = {
    "conditional_progress": "Conditional waiver on progress payment",
    "unconditional_progress": "Unconditional waiver on progress payment",
    "conditional_final": "Conditional waiver on final payment",
    "unconditional_final": "Unconditional waiver on final payment",
}


def suggested_waiver_kind(job: dict[str, Any], invoice: dict[str, Any] | None = None) -> str:
    """The waiver type that fits a pay app now (M17): conditional while it is unpaid, unconditional
    once its payment is matched; on final payment only when this pay app's money is the last there
    is (still_open). A final waiver gives up the right to anything unbilled or unpaid."""
    paid = invoice is None or invoice.get("status") == "paid"
    return f"{'unconditional' if paid else 'conditional'}_{'progress' if still_open(job, invoice) else 'final'}"


def still_open(job: dict[str, Any], invoice: dict[str, Any] | None = None) -> list[str]:
    """What a final waiver would give up, in words, besides this pay app's own money: the job not
    finished, work or a change order not billed, a draft, pay apps owed, retainage held. A change
    order on this draft has to be signed before it goes, so it isn't one."""
    m = job_money(job)
    own_owed = own_draft = own_held = 0
    drafting = False
    if invoice is not None and invoice.get("status") in DRAFT_STATUSES:
        own_draft, own_held, drafting = invoice["amount_cents"], int(invoice.get("retainage_cents") or 0), True
    elif invoice is not None and invoice.get("status") in OPEN_FOR_PAYMENT:
        own_owed = _owed_on(job, invoice)
    out = []
    if job.get("work_status") != "complete":
        out.append("the job isn't finished")
    if m["unbilled_completed_cents"] > 0:
        out.append(f"{money(m['unbilled_completed_cents'])} is not billed")
    if m["draft_cents"] > own_draft:
        out.append(f"{money(m['draft_cents'] - own_draft)} is on a draft")
    if m["outstanding_cents"] > own_owed:
        out.append(f"{money(m['outstanding_cents'] - own_owed)} is still owed")
    if m["retainage_open_cents"] > 0 or own_held:
        out.append(f"{money(m['retainage_open_cents'] + own_held)} of retainage is still held")
    for co in job["change_orders"]:
        if co["status"] == "pending" and not (drafting and co.get("included_in_billing")):
            out.append(f"CO {co['number']} is not signed")
    return out


def release_due_kind(job: dict[str, Any], release: dict[str, Any]) -> str:
    """The waiver a retainage release owes now. One marked final stays final only while nothing else
    is open (one recorded before Nov 5, or on a job undone or reopened since, owes a progress waiver)."""
    final = str(release.get("kind", "")).endswith("_final") and not still_open(job)
    return "unconditional_final" if final else "unconditional_progress"


WAIVER_TYPE_WORDS = {"conditional_progress": "conditional", "unconditional_progress": "unconditional",
                     "conditional_final": "conditional final", "unconditional_final": "unconditional final"}
_WAIVER_LABEL_RE = re.compile(r"\b(un)?conditional\b", re.I)


def record_waiver_kind(record: dict[str, Any]) -> str:
    """A waiver record's type (M14, M18): the one kept on it, else read from its label when the
    label says conditional or unconditional ("final" makes it final). Blank when unknown."""
    if record.get("waiver_kind") in WAIVER_KINDS:
        return record["waiver_kind"]
    label = str(record.get("label") or "")
    found = _WAIVER_LABEL_RE.search(label)
    if not found:
        return ""
    return f"{'unconditional' if found.group(1) else 'conditional'}_{'final' if re.search(r'final', label, re.I) else 'progress'}"


def set_waiver_type(state: dict[str, Any], job_id: str, record_id: str, kind: str, actor: str = "owner",
                    for_id: str = "", said: bool = True, answerer: "Answerer | None" = None) -> dict[str, Any]:
    """Say which of the four types a signed waiver on the job record is (M14), and with for_id
    which paid pay app or retainage release it is for (B1 of the Oct 30 review), or "job" for the
    older job-level waiver (Earlier payment). An unconditional one is tied as it is added when only
    one waiver is owed, so it can't go out for another; never one that was unclear while several
    were owed (waiver_needs_tie): a person says which that one is for. said: a person picked the
    type for this record, which is their answer to which waiver it is (False for an upload that
    takes the type of the item or page it was added on). Someone who can't answer (answerer) sets
    the type and nothing more, and changes no type that already has an answer."""
    job = _job(state, job_id)
    rec = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not rec or rec.get("kind") != "waiver":
        raise GateError("That lien waiver is not on this job's record.")
    if said and not _may_answer(answerer, _on_retainage_request(job, record=rec)):
        if waiver_answer(rec):
            raise GateError(f"{rec['label']} has been answered. {NOT_YOURS_TO_ANSWER}")
        said = False
    if kind not in WAIVER_KINDS:
        _refuse("", {"kind": "Pick the waiver type: conditional or unconditional, progress or final."})
    owed = _waiver_owed(job)
    target = None
    if for_id:
        target = next((o for o in owed if o["id"] == for_id), None)
        if not target:
            _refuse("", {"for_id": "Pick a paid pay app, retainage release or earlier payment that still owes its waiver."})
    elif kind.startswith("unconditional") and not _waiver_tie(rec) and not rec.get("waiver_needs_tie") and len(owed) == 1:
        target = owed[0]
    before = record_waiver_kind(rec)
    if said:
        _say_which(state, job, rec, kind.split("_")[0], actor, kind.split("_")[1], answerer=answerer)   # a person said it: their answer
    else:
        rec["waiver_kind"] = kind
    if before != kind:
        audit(state, job_id, "waiver_type", f"{actor} set the type of {rec['label']}: {WAIVER_KINDS[kind].lower()}.", actor)
    if target:
        rec.pop("waiver_needs_tie", None)
    if target and _waiver_tie(rec) != target["id"]:
        _tie_waiver(rec, target["id"])
        audit(state, job_id, "waiver_type", f"{actor} said {rec['label']} is the waiver for {target['number']}.", actor)
    if not job.get("closed"):
        _refresh_draft_gate(job)
    return job


def _sent_file_ids(state: dict[str, Any], job: dict[str, Any]) -> set[str]:
    """Every file that has gone out in any email on this job, the pay-app emails included."""
    return {fid for e in state.get("email_log", []) if e.get("job_id") == job["id"] for fid in e.get("attachments", [])}


# The older job-level waiver (records from before per-pay-app waivers), as a waiver_for choice.
EARLIER_PAYMENT = "job"


def _waiver_owed(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Every waiver owed now, as {id, number}: each paid pay app and retainage release not sent
    yet, and an older job-level one (id "job", Earlier payment)."""
    out = [{"id": x["id"], "number": x.get("number") or f"Retainage released {x['at'][:10]}"} for x in _pending_waivers(job)]
    if _legacy_waiver_pending(job):
        out.append({"id": EARLIER_PAYMENT, "number": "Earlier payment"})
    return out


def _waiver_tie(record: dict[str, Any]) -> str:
    """The pay app or retainage release a signed waiver was taken for ("job" for the earlier
    job-level payment), if any."""
    return record.get("invoice_id") or record.get("release_id") or (EARLIER_PAYMENT if record.get("waiver_legacy") else "")


def _tie_waiver(record: dict[str, Any], target: str) -> None:
    for key in ("invoice_id", "release_id", "waiver_legacy"):
        record.pop(key, None)
    if target == EARLIER_PAYMENT:
        record["waiver_legacy"] = True
    else:
        record["release_id" if target.startswith("rw-") else "invoice_id"] = target


def _hold_unclear_waivers(job: dict[str, Any]) -> None:
    """Before a waiver stops being owed (sent or marked not needed): while several are owed, a
    signed waiver not said to be for any is unclear (B1), and it stays unclear once fewer are owed,
    so it never quietly becomes the waiver for whichever one is left. A person ties it."""
    if len(_waiver_owed(job)) <= 1:
        return
    for record in job.get("records", []):
        if record.get("kind") == "waiver" and not record.get("number_only") and not _waiver_tie(record):
            record["waiver_needs_tie"] = True


def waiver_on_file(state: dict[str, Any], job: dict[str, Any], kind: str, invoice_id: str = "", release_id: str = "",
                   legacy: bool = False) -> dict[str, Any] | None:
    """The signed waiver that would go out for this type (M18): the newest waiver record of that
    type (or with no type said) whose files have not gone out in any email, taken for this pay
    app or release (invoice_id, release_id), else one taken for none. One taken for none is
    `ambiguous` when more than one waiver is owed (B1 of the Oct 30 review): the person says
    which it is for (set_waiver_type's for_id) before it is sent, and it stays so once fewer are
    owed (waiver_needs_tie). `legacy` asks for the older job-level waiver (Earlier payment)."""
    target = invoice_id or release_id or (EARLIER_PAYMENT if legacy else "")
    found = _waiver_on_file(state, job, kind, lambda tie: tie == target and bool(target)) \
        or _waiver_on_file(state, job, kind, lambda tie: not tie)
    if found:
        found["untied"] = not _waiver_tie(found["record"])
        found["ambiguous"] = found["untied"] and (len(_waiver_owed(job)) > 1 or bool(found["record"].get("waiver_needs_tie")))
    return found


def _waiver_on_file(state: dict[str, Any], job: dict[str, Any], kind: str, tied: Any) -> dict[str, Any] | None:
    sent = _sent_file_ids(state, job)
    # A waiver put on a conditional waiver item (a pay app's or the closeout one) is that
    # conditional waiver, even when its type was never said: never the one sent after payment.
    conditional = {a.get("record_id") for packet in [inv.get("packet") or [] for inv in job["invoices"] if inv.get("status") != "void"]
                   + [job.get("closeout_packet") or []] for item in packet
                   if str(item.get("waiver_kind", "")).startswith("conditional") for a in item.get("attachments", [])}
    for record in reversed(job.get("records", [])):
        if record.get("kind") != "waiver" or record.get("number_only") or waiver_answer(record) == "no":
            continue
        has = record_waiver_kind(record)
        if not has and record["id"] in conditional:
            continue
        if has and has != kind:
            continue
        if not tied(_waiver_tie(record)):
            continue
        fresh = [fid for fid in record.get("file_ids", []) if fid not in sent]
        if fresh:
            return {"record": record, "file_ids": fresh}
    return None


def _waiver_comes_due(state: dict[str, Any], job: dict[str, Any], what: dict[str, Any], which: str) -> bool:
    """A pay app paid or retainage released owes its unconditional waiver (M16, M20), unless the
    client was marked as not taking lien waivers (Not needed for this client on the conditional
    one, skip_packet_item; B2 of the Oct 30 review): then none is due and the pay app says why.
    Not needed on this job is about the conditional waiver only: the unconditional one stays owed,
    with its own audited way out (waiver_not_needed). Returns whether one is due."""
    payer = _payer(state, job["payer_id"])
    skip = payer.get("waiver_not_needed")
    what["waiver_sent"] = False
    if not skip:
        what["waiver_due"] = True
        return True
    reason = skip.get("reason") or "Not needed for this client"
    what["waiver_due"] = False
    what["waiver_not_needed"] = {"by": "duracash", "at": utcnow(), "reason": reason, "client": True}
    audit(state, job["id"], "waiver_not_needed", f"No lien waiver is due for {which}: {payer['name']} does not take them ({reason}).", "duracash")
    return False


def _pending_waivers(job: dict[str, Any]) -> list[dict[str, Any]]:
    """What still owes a lien waiver: each paid pay app and each retainage release not yet sent."""
    return [x for x in job["invoices"] + job.get("retainage_waivers", []) if x.get("waiver_due") and not x.get("waiver_sent")]


def send_waiver(
    state: dict[str, Any], job_id: str, actor: str = "owner", invoice_id: str | None = None, kind: str | None = None,
    release_id: str | None = None, confirm_final: bool = False,
    read_file: Any = None, looked_at: str = "", check_only: bool = False, answerer: "Answerer | None" = None,
) -> dict[str, Any]:
    """The second stage of the waiver (M16, M21): after payment, a person approves sending the
    signed form on the job record. One waiver per paid pay app and per retainage release
    (release_id, M20); older records keep a single job-level flag. Duracash never writes the form:
    it is refused until a signed waiver of the type being sent is on the job record. With read_file,
    the file is read whatever its label or type says (Decision 6, criterion 67): what doesn't match
    is named, and it goes only once a person says they opened that record (looked_at, its id).
    check_only: every check and warning, and nothing sent, so the page asks for the look first and
    then one confirm to send (Round 1 acceptance, item 17)."""
    job = _open_job(state, job_id)
    if kind is not None and kind not in WAIVER_KINDS:
        raise GateError("Pick the waiver type: conditional or unconditional, progress or final.")
    if invoice_id == EARLIER_PAYMENT:
        invoice_id = None  # the older job-level waiver, as the "Which pay app?" picker names it
    releases = [r for r in job.get("retainage_waivers", []) if r.get("waiver_due") and not r.get("waiver_sent")]
    pending = [inv for inv in job["invoices"] if inv.get("waiver_due") and not inv.get("waiver_sent")]
    if release_id:
        releases = [r for r in releases if r["id"] == release_id]
        pending = []
        if not releases:
            raise GateError("No waiver is due on that retainage release.")
    elif invoice_id:
        pending = [inv for inv in pending if inv["id"] == invoice_id]
        releases = []
    legacy = not invoice_id and not release_id and _legacy_waiver_pending(job)
    if not (legacy or pending or releases):
        raise GateError("No waiver is due. Payment has not been matched.")
    payer = _payer(state, job["payer_id"])
    to = _send_to(payer)
    invoice = release = None
    if legacy:
        kind = kind or suggested_waiver_kind(job)
    elif pending:
        invoice = pending[0]
        kind = kind or suggested_waiver_kind(job, invoice)
    else:
        release = releases[0]
        release_due = release_due_kind(job, release)
        kind = kind or release_due
    found = waiver_on_file(state, job, kind, invoice["id"] if invoice else "", release["id"] if release else "", legacy=legacy)
    if not found:
        message = f"Add the signed {WAIVER_TYPE_WORDS[kind]} waiver to the job records first."
        raise GateError(message, extra={"waiver_kind": kind})
    # A final waiver while the job still calls for a progress one gives up more than is being paid:
    # it warns and takes a person's confirm, before any email (never a hard block, Decision 6).
    due = suggested_waiver_kind(job, invoice) if invoice else release_due if release else suggested_waiver_kind(job)
    if kind.endswith("_final") and not due.endswith("_final") and not confirm_final:
        left = still_open(job, invoice)
        raise GateError(f"This is a final waiver, but the job isn't finished: the {WAIVER_KINDS[due].lower()} is the one due"
                        + (f", because {', and '.join(left)}" if left else "") + ". Send the final one only if you mean to.",
                        extra={"final_waiver_warning": True, "confirm_label": "Send the final waiver"})
    record, files = found["record"], found["file_ids"]
    looked_past = []
    if read_file is not None:
        amount_due = invoice["amount_cents"] - int(invoice.get("short_pay_cents") or 0) if invoice else release["amount_cents"] if release else None
        metas = [f for f in state.get("files", []) if f["id"] in set(files)]
        check = doc_check.check([(m["name"], str(m.get("ext") or "").lower(), read_file(m)) for m in metas], job["name"],
                                money(amount_due) if amount_due is not None else "", amount_due, kind, True)
        looked_past = check["problems"]
        if looked_past and looked_at != record["id"]:
            raise GateError(f"Look at {record['label']} before it goes as the {WAIVER_KINDS[kind].lower()}: {' '.join(looked_past)}",
                            extra={"look_warning": True, "record_id": record["id"], "confirm_label": "I opened it and it's right"})
    if found["ambiguous"]:
        # More than one waiver is owed and this one wasn't said to be for any (B1): ask, send nothing.
        why = "More than one waiver is due on this job." if len(_waiver_owed(job)) > 1 else "It was added while more than one waiver was due."
        raise GateError(f"Say which pay app {record['label']} is for first. {why}",
                        fields={"for_id": "Pick the pay app or retainage release this signed waiver is for."},
                        extra={"waiver_record_id": record["id"], "waiver_for": _waiver_owed(job)})
    if check_only:
        return job
    if looked_past:
        audit(state, job_id, "waiver_looked_at", f"{actor} opened {record['label']} and said it is right: {' '.join(looked_past)}", actor)
    _hold_unclear_waivers(job)
    now = utcnow()
    if legacy:
        which = ""
        detail = "the earlier matched payment"
        job["legacy_waiver_pending"] = False
        if not any("waiver_due" in inv for inv in job["invoices"]):
            job["waiver_sent"] = True
        amount, through, held = None, "", 0
    elif invoice:
        invoice["waiver_sent"] = True
        invoice["waiver_sent_at"] = now
        invoice["waiver_kind"] = kind
        invoice["waiver_record_id"] = record["id"]
        which = f" for {invoice['number']}"
        # After a short pay, what came in is the billed amount less what was kept off it.
        amount = invoice["amount_cents"] - int(invoice.get("short_pay_cents") or 0)
        detail = f"payment of {money(amount)} on {invoice['number']}"
        through = invoice.get("period_to") or ""
        # What stays held on the job to date, not only on this pay app (B-wording of the Oct 30 review).
        held = job_money(job)["retainage_open_cents"] if kind.endswith("progress") else 0
    else:
        release["waiver_sent"] = True
        release["waiver_sent_at"] = now
        release["kind"] = kind
        release["waiver_record_id"] = record["id"]
        amount = release["amount_cents"]
        which = f" for the retainage released {release['at'][:10]}"
        detail = f"release of {money(amount)} retainage"
        through, held = "", 0
    _say_which(state, job, record, kind.split("_")[0], actor, kind.split("_")[1], answerer=answerer)   # they chose the type and looked
    if not _waiver_tie(record):
        _tie_waiver(record, invoice["id"] if invoice else release["id"] if release else EARLIER_PAYMENT)
    if kind.endswith("_final"):
        _final_waiver_fills_closeout(state, job, record, actor)
    job["waiver_sent_at"] = now
    job["waiver_kind"] = WAIVER_KINDS[kind]
    job["external_status"] = "Waiver sent"
    subject = f"{WAIVER_KINDS[kind]}{which} — {job['name']}"
    covers = []
    if amount is not None:
        covers.append(f"Amount covered: {money(amount)}.")
    if through:
        covers.append(f"Work through {through}.")
    if held:
        covers.append(f"Not released by this waiver: the {money(held)} retainage held to date on this job.")
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Thank you for the {detail} on {job['name']}. Our signed {WAIVER_KINDS[kind].lower()} is attached."
        + ("\n\n" + "\n".join(covers) if covers else "")
        + f"\n\n{sign_off(state, actor)}"
    )
    _record_email(state, job_id, "waiver", to, subject, body, actor, attachments=files,
                  extra={"record_id": record["id"], "waiver_kind": kind, "invoice_id": invoice["id"] if invoice else None,
                         "release_id": release["id"] if release else None})
    _sync_waiver(job)
    audit(state, job_id, "send_waiver", f"{actor} approved sending the {WAIVER_KINDS[kind].lower()}{which} ({record['label']}).", actor)
    return job


def _final_waiver_fills_closeout(state: dict[str, Any], job: dict[str, Any], record: dict[str, Any], actor: str) -> None:
    """The final waiver that went out is the closeout's Final lien waivers: nobody files it twice."""
    record["sent_as_final"] = True
    for item in job.get("closeout_packet") or []:
        if not item.get("present") and (_record_kinds_for(item) or ("",))[0] == "final_waiver":
            now = utcnow()
            item["attachments"] = [{"name": record["label"], "record_id": record["id"]}]
            item.update(present=True, resolved_at=now, confirmed_by=actor, confirmed_at=now)
            item.pop("check", None)
            audit(state, job["id"], "packet_confirmed", f"{record['label']} went out as the final waiver, so it fills {item['label']}.", actor)


def waiver_not_needed(
    state: dict[str, Any], job_id: str, reason: str, invoice_id: str | None = None, release_id: str | None = None,
    actor: str = "owner",
) -> dict[str, Any]:
    """The way out of the after-payment waiver (B2 of the Oct 30 review): no waiver goes out
    through Duracash for this paid pay app, retainage release or older job-level payment, because
    it was handed over outside Duracash or the client doesn't need one. It takes a reason, kept on
    it and in the activity log, and the job's waiver page and Closeout show it as Not needed. The
    job can then be finished (M19 still holds for every waiver not settled this way)."""
    job = _open_job(state, job_id)
    if invoice_id == EARLIER_PAYMENT:
        invoice_id = None
    reason = _reason(reason)
    if not reason:
        _refuse("", {"reason": "Enter why no waiver goes out through Duracash, like handed to the owner on site."})
    releases = [r for r in job.get("retainage_waivers", []) if r.get("waiver_due") and not r.get("waiver_sent")]
    pending = [inv for inv in job["invoices"] if inv.get("waiver_due") and not inv.get("waiver_sent")]
    target: dict[str, Any] | None = None
    if release_id:
        target = next((r for r in releases if r["id"] == release_id), None)
    elif invoice_id:
        target = next((inv for inv in pending if inv["id"] == invoice_id), None)
    elif _legacy_waiver_pending(job):
        target = job
    elif len(pending) + len(releases) == 1:
        target = (pending + releases)[0]
    elif pending or releases:
        raise GateError("More than one waiver is due. Pick the one that isn't needed.",
                        [o["number"] for o in _waiver_owed(job)], {"invoice_id": "Pick the pay app or release."})
    if target is None:
        raise GateError("No waiver is due there.")
    _hold_unclear_waivers(job)
    mark = {"by": actor, "at": utcnow(), "reason": reason}
    if target is job:
        which = "the earlier payment"
        job["legacy_waiver_pending"] = False
        if not any("waiver_due" in inv for inv in job["invoices"]) and not job.get("retainage_waivers"):
            job["waiver_due"] = False
        job["waiver_not_needed"] = mark
    else:
        which = target.get("number") or f"the retainage released {target['at'][:10]}"
        target["waiver_due"] = False
        target["waiver_not_needed"] = mark
    _sync_waiver(job)
    if job.get("external_status") in {"Payment received, waiver not sent", "Short pay accepted, waiver not sent"}:
        job["external_status"] = None
    audit(state, job_id, "waiver_not_needed", f"{actor} marked the lien waiver for {which} as not going out through Duracash: {reason}.", actor)
    return job


def _sync_waiver(job: dict[str, Any]) -> None:
    """Keep the job-level waiver flags in step with the per-pay-app and per-release ones."""
    tracked = [inv for inv in job["invoices"] if "waiver_due" in inv] + job.get("retainage_waivers", [])
    if not tracked:
        return
    pending = bool(_pending_waivers(job)) or bool(job.get("legacy_waiver_pending"))
    job["waiver_due"] = True
    job["waiver_sent"] = not pending


def _legacy_waiver_pending(job: dict[str, Any]) -> bool:
    """An unsent waiver recorded only at job level (records from before per-pay-app waivers)."""
    if job.get("legacy_waiver_pending"):
        return True
    return bool(job.get("waiver_due") and not job.get("waiver_sent") and not any("waiver_due" in inv for inv in job["invoices"])
                and not job.get("retainage_waivers"))


def waiver_stages(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> dict[str, Any]:
    """Both waiver stages of a pay app (M17), for its page and Closeout: the conditional one that
    goes with it and the unconditional one after payment, each {kind, status}. Status is Needed,
    Attached, Sent, Not needed or (unconditional, before payment) After payment; a conditional
    item filled by an unconditional waiver adds ", but unconditional" (M14). After payment it may
    be "Which pay app?" (a signed waiver not said to be for one while several are owed, B1); with
    a waiver on file it names the record (record_label), and Not needed gives its reason."""
    cond_item = next((item for item in invoice.get("packet") or [] if str(item.get("waiver_kind", "")).startswith("conditional")), None)
    sent_out = invoice.get("status") not in DRAFT_STATUSES
    if cond_item is None:
        cond = {"kind": invoice.get("conditional_waiver_kind", ""), "status": ""}
    elif not cond_item.get("required") and not cond_item.get("present"):
        cond = {"kind": cond_item["waiver_kind"], "status": "Not needed"}
    elif cond_item.get("present"):
        records = {r["id"]: r for r in job.get("records", [])}
        wrong = any(record_waiver_kind(records.get(a.get("record_id")) or {}).startswith("unconditional")
                    for a in cond_item.get("attachments", []))
        cond = {"kind": invoice.get("conditional_waiver_kind") or cond_item["waiver_kind"],
                "status": ("Sent" if sent_out else "Attached") + (", but unconditional" if wrong else "")}
    else:
        cond = {"kind": cond_item["waiver_kind"], "status": "Needed"}
    if invoice.get("waiver_sent"):
        uncond = {"kind": invoice.get("waiver_kind") or "", "status": "Sent"}
    elif invoice.get("waiver_not_needed"):
        uncond = {"kind": "", "status": "Not needed", "reason": invoice["waiver_not_needed"].get("reason", "")}
    elif invoice.get("waiver_due"):
        kind = suggested_waiver_kind(job, invoice)
        found = waiver_on_file(state, job, kind, invoice["id"])
        # The exact record that would go out (B1), or which pay app it is for when that is unclear.
        uncond = {"kind": kind, "status": "Needed"}
        if found:
            uncond.update(status="Which pay app?" if found["ambiguous"] else "Attached", record_label=found["record"]["label"],
                          record_id=found["record"]["id"])
    else:
        uncond = {"kind": "", "status": "After payment"}
    return {"conditional": cond, "unconditional": uncond}


def chase_retainage(state: dict[str, Any], job_id: str, actor: str = "duracash", amount_cents: int | None = None,
                    unconditional_override: str = "", read_file: Any = None) -> dict[str, Any]:
    """Ask the client to release retainage (M24, M25): only once the job is marked done and the
    closeout paperwork is in. `amount_cents` below what is held asks for part of it. The request
    attaches the newest pay app sent and the closeout documents on file. Their waivers are asked
    about as a pay app's are: an unconditional one where the closeout needs the conditional final
    waiver takes unconditional_override, the reason (the server lets only an owner give one)."""
    job = _open_job(state, job_id)
    open_retainage = job_money(job)["retainage_open_cents"]
    if open_retainage <= 0:
        raise GateError("No open retainage.")
    if job.get("work_status") != "complete":
        raise GateError("Retainage is asked for once the work is finished. Use Final pay app first.")
    if amount_cents is not None:
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
            _refuse("", {"amount_cents": "Enter the retainage to ask for, more than $0."})
        if amount_cents > open_retainage:
            _refuse("", {"amount_cents": f"Enter no more than the {money(open_retainage)} held."})
    amount = amount_cents or open_retainage
    partial = amount < open_retainage
    closeout = _retainage_packet(job)   # what _packet_file_ids attaches below, on a job from before the closeout list too
    asked = _ask_which_waiver(job, closeout, retainage=True)
    missing = closeout_missing(job)
    if missing:
        raise GateError("The closeout documents aren't complete.", [_blocker_text(job, item) for item in missing], keep_changes=bool(asked))
    _need_waiver_answers(state, job, closeout, (), (), read_file)
    unconditional = _unconditional_going(job, closeout, retainage=True)
    override = _reason(unconditional_override) if unconditional else ""
    if unconditional and not override:
        raise GateError(UNCONDITIONAL_ATTACHED, [f"{r['label']} ({_why_unconditional(r)})" for r in unconditional],
                        fields={"unconditional_override": "Enter why it goes with the request, or take it off."},
                        extra={"unconditional_waiver": True}, keep_changes=True)
    payer = _payer(state, job["payer_id"])
    to = _send_to(payer)
    days = _int_or(payer.get("follow_up_days"), 7)
    last = job.get("last_retainage_chase_at")
    if last:
        due = _aware(last) + timedelta(days=days)
        if datetime.now(timezone.utc) < due:
            raise GateError(
                f"The last retainage request went out {last[:10]}. The next is due {due.date().isoformat()} "
                f"({payer['name']} expects {days} days between contacts)."
            )
    job["retainage_chases"] = job.get("retainage_chases", 0) + 1
    job["last_retainage_chase_at"] = utcnow()
    job["retainage_asked_cents"] = amount
    job["external_status"] = f"Retainage follow-up sent to {payer['approver']}"
    subject = f"Application for release of retainage — {money(amount)} — {job['name']}"
    files = _packet_file_ids(state, job, {"packet": closeout})
    newest = next((inv for inv in reversed(job["invoices"]) if inv["status"] in BILLED_STATUSES), None)
    if partial:
        ask = (f"The work on {job['name']} is complete. Please release {money(amount)} "
               f"of the {money(open_retainage)} retainage held on this job.")
    else:
        ask = (f"The work on {job['name']} is complete and the closeout items are done. "
               f"Please release the {money(amount)} retainage held on this job.")
    body = (
        f"{payer.get('approver') or payer['name']},\n\n{ask}"
        + (f" Our latest pay application, {newest['number']}, is attached." if newest else "")
        + (f" The closeout documents on file are attached ({len(files)})." if files else "")
        + f"\n\n{sign_off(state, actor)}"
    )
    _record_email(state, job_id, "retainage", to, subject, body, actor, attachments=files,
                  payapp_invoice_id=newest["id"] if newest else None)
    if override:
        job.setdefault("retainage_overrides", []).append({"by": actor, "at": utcnow(), "reason": override, "record_ids": [r["id"] for r in unconditional]})
        audit(state, job_id, "unconditional_override",
              f"{actor} asked for retainage with an unconditional waiver attached ({', '.join(r['label'] for r in unconditional)}): {override}.", actor)
    audit(
        state,
        job_id,
        "chase_retainage",
        f"Asked {payer['name']} to release {money(amount)}" + (f" of {money(open_retainage)}" if partial else "") + " retainage.",
        actor,
    )
    return job


def record_retainage_payment(
    state: dict[str, Any],
    job_id: str,
    amount_cents: int,
    reference: str = "",
    actor: str = "owner",
) -> dict[str, Any]:
    """Record retainage the payer released. Kept apart from pay-app payments. Each release owes
    its own lien waiver (M20): unconditional, on final payment only when the job is finished by it."""
    job = _open_job(state, job_id)
    open_retainage = job_money(job)["retainage_open_cents"]
    if open_retainage <= 0:
        raise GateError("No open retainage.")
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        raise GateError("Retainage payment must be greater than zero.")
    if amount_cents > open_retainage:
        raise GateError(f"{money(amount_cents)} is more than the {money(open_retainage)} retainage still open.")
    reference = reference.strip() if isinstance(reference, str) else ""
    job["retainage_released_cents"] = job.get("retainage_released_cents", 0) + amount_cents
    now = utcnow()
    legacy = _legacy_waiver_pending(job)
    job.setdefault("retainage_payments", []).append(
        {"amount_cents": amount_cents, "reference": reference or "manual", "at": now, "recorded_by": actor}
    )
    waivers = job.setdefault("retainage_waivers", [])
    release = {
        "id": f"rw-{len(waivers) + 1}", "amount_cents": amount_cents, "reference": reference or "manual", "at": now,
        # Final only when the job is finished (suggested_waiver_kind). Releasing all the retainage
        # on a job half done is a progress waiver: it gives up no rights to the work still to bill.
        "kind": "unconditional_final" if suggested_waiver_kind(job).endswith("_final") else "unconditional_progress",
    }
    waivers.append(release)
    due = _waiver_comes_due(state, job, release, f"the retainage released {now[:10]}")
    if legacy:
        job["legacy_waiver_pending"] = True
    _sync_waiver(job)
    _refresh_draft_gate(job, state)
    audit(state, job_id, "retainage_paid", f"{actor} recorded {money(amount_cents)} retainage released ({reference or 'manual'})."
          + (" Its lien waiver is due." if due else ""), actor)
    return job


# The secret part of each customer page (/p/) and signing page (/s/) address. They stay on the
# server (R6-05): what the app is sent, and the export, say only that the page exists, and
# GET /api/jobs/<id>/<payapp|co>/<id>/page opens it for someone signed in.
LINK_SECRETS = (("invoices", "payer_token", "customer_page"), ("change_orders", "sign_token", "signing_page"))


def hide_link_secrets(job: dict[str, Any]) -> dict[str, Any]:
    """A copy of a job (changed in place) without the page secrets, with a flag where one was."""
    for key, secret, flag in LINK_SECRETS:
        for item in job.get(key, []):
            if item.pop(secret, None):
                item[flag] = True
    return job


LINK_IN_TEXT = re.compile(r"/([ps])/[a-z0-9-]+\.[A-Za-z0-9_-]{20,64}")


def without_link_secrets(state: dict[str, Any]) -> dict[str, Any]:
    """The whole company, as the export writes it: the emails' copies of the links are cut too.
    (The Outbox in the app keeps each email as it went out.)"""
    out = deepcopy(state)
    for job in out.get("jobs", []):
        hide_link_secrets(job)
    for email in out.get("email_log", []):
        if isinstance(email.get("body"), str):
            email["body"] = LINK_IN_TEXT.sub(r"/\1/[link]", email["body"])
    return out


def job_view(state: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """One job as the app shows it: the stored job plus money, blockers and labels."""
    out = hide_link_secrets(deepcopy(job))
    out["money"] = job_money(job)
    out["blocker_items"] = blocker_items(job)
    out["blockers"] = [b["text"] for b in out["blocker_items"]]
    # The blockers that are paperwork to attach (the rest are unsigned change orders).
    out["missing_paperwork"] = [item["label"] for item in missing_packet(job)]
    out["status_label"] = status_label(job, billing_style(state))
    # Where the job stands, for its pills and buttons (R4-10: the app reads these, not its own rules).
    out["blocked"] = is_blocked(job)
    out["needs_paperwork"] = needs_paperwork(job)
    out["blocker_button"] = blocker_button(job) if out["blocked"] else ""
    out["payer"] = deepcopy(_payer(state, job["payer_id"]))
    out["retainage_usual"] = default_retainage_pct(out["payer"])   # the pay app warns at 0% (round 1, item 1)
    out["can_delete"] = can_delete_job(job)
    out["can_undo_done"] = can_undo_done(job)
    out["close_blocker_items"] = close_blocker_items(job)
    out["close_blockers"] = [b["text"] for b in out["close_blocker_items"]]
    # What still holds back asking for the retainage (chase_retainage's rule), for the job page,
    # Closeout and the helper.
    out["closeout_missing"] = [item["label"] for item in closeout_missing(job)]
    out.setdefault("closeout_packet", [])
    out.setdefault("evidence", [])
    out.setdefault("done_notes", "")
    out["sov_view"] = _sov_view(job, state)
    _paperwork_view(state, job, out)
    out["playbook_missing"] = playbook_missing(out["payer"])
    out["playbook_blocks"] = bool(job.get("playbook_required") and out["playbook_missing"])
    out["paperwork_view"] = _job_paperwork_view(state, job)
    # Where the job stands and the dates that matter (J7), worked out here so the page doesn't.
    out["started_on"] = job_started_on(state, job)
    out["stage"] = job_stage(job, out["money"])
    out["key_dates"] = job_key_dates(state, job, out["payer"])
    out["address_parts"] = job.get("address_parts") or parse_legacy_address(job.get("address", ""))
    # What is still owed on each pay app (R3-02): a part-paid one shows "$remaining of $amount".
    for inv in out["invoices"]:
        paid = sum(p["amount_cents"] for p in job["payments"] if p.get("invoice_id") == inv["id"])
        inv["remaining_cents"] = max(0, inv["amount_cents"] - paid) if inv["status"] in OPEN_FOR_PAYMENT else 0
    if out["money"]["refund_owed_cents"]:   # Record a Payment takes it on the last pay app sent
        last = next((inv for inv in reversed(out["invoices"]) if inv["status"] in BILLED_STATUSES), None)
        if last:
            last["remaining_cents"] += out["money"]["refund_owed_cents"]
            last["refund_owed_cents"] = out["money"]["refund_owed_cents"]
    _payapp_figures(job, out)
    _co_flags(out)
    # Money > Unpaid's Recent contact (M11), only on jobs with money owed, to keep the page light.
    if out["money"]["outstanding_cents"] > 0:
        out["recent_contact"], out["contact_total"] = contact_log(state, job["id"])
    return out


def payapp_figures(job: dict[str, Any], invoice: dict[str, Any]) -> dict[str, int]:
    """The pay app's summary lines 1-9, as its PDF prints them (payapp_pdf.summary), plus the
    payments received on the pay apps before it (Stephen's Oct 29 review, M2), for information."""
    figures = payapp_pdf.summary(job, invoice)
    earlier = set()
    for inv in job.get("invoices", []):
        if inv is invoice or inv.get("id") == invoice.get("id"):
            break
        earlier.add(inv.get("id"))
    received = sum(p["amount_cents"] for p in job.get("payments", []) if p.get("invoice_id") in earlier)
    # Less what went back by the time this one went out (a pay app not sent yet: by now).
    sent = invoice.get("sent_at")
    back = sum(int(s.get("returned_cents", s["cents"])) for s in job.get("overbilling_settled") or [] if not sent or str(s.get("at", "")) <= sent)
    figures["received_before"] = max(0, received - back)
    return figures


def _payapp_figures(job: dict[str, Any], out: dict[str, Any]) -> None:
    """Each pay app that isn't void gets its figures; a deleted draft's would only add weight."""
    for stored, inv in zip(job.get("invoices", []), out.get("invoices", [])):
        if stored.get("status") in sov.LIVE_STATUSES:
            inv["figures"] = payapp_figures(job, stored)


def _co_flags(out: dict[str, Any]) -> None:
    """What the app shows on each change order (M9): work started before it was signed."""
    for co in out.get("change_orders", []):
        co["work_started_unsigned"] = co_started_unsigned(co)


WAIVER_FORMS_PATH = Path(__file__).resolve().parents[1] / "helper" / "waiver_forms.json"
_WAIVER_FORMS: dict[str, Any] = {}


def waiver_forms() -> dict[str, Any]:
    """The lien waiver forms each state sets (M22), from helper/waiver_forms.json: {states:
    {"GA": entry}, other: entry}. Duracash only points to the form; it never writes one."""
    if not _WAIVER_FORMS:
        _WAIVER_FORMS.update(json.loads(WAIVER_FORMS_PATH.read_text(encoding="utf-8")))
    return _WAIVER_FORMS


def _item_questions(job: dict[str, Any], packet: list[dict[str, Any]]) -> None:
    """Each waiver item waiting for its files' answer, as the page draws it: item_question."""
    for item in packet:
        if is_waiver_item(item) and not item.get("present") and any(a.get("unconfirmed") for a in item.get("attachments", [])):
            item["waiver_question"] = item_question(job, item)


def _paperwork_view(state: dict[str, Any], job: dict[str, Any], out: dict[str, Any]) -> None:
    """What the paperwork, waiver and retainage parts of the app read (M3, M17, M20, M22): each
    item's accepted record kinds, both waiver stages of each pay app, the retainage releases'
    waivers and the waiver form for the job's state."""
    packets = [inv.get("packet") or [] for inv in out["invoices"]] + [out.get("packet") or [], out.get("closeout_packet") or []]
    for packet in packets:
        for item in packet:
            match = _record_kinds_for(item)
            item["accepts_kinds"] = list(match[1]) if match else []
    by_id = {inv["id"]: inv for inv in job["invoices"]}
    for inv in out["invoices"]:
        if inv["status"] == "void":
            continue
        inv["waiver_stages"] = waiver_stages(state, job, by_id[inv["id"]])
        inv["co_rows"] = sov.co_rows(job, by_id[inv["id"]])
        if inv["status"] in DRAFT_STATUSES:
            inv["email_preview"] = email_preview(state, job, by_id[inv["id"]])
            real = by_id[inv["id"]]
            extra = real.get("extra_record_ids") or []
            inv["unconditional_attached"] = _unconditional_rows(state, unconditional_waivers_attached(job, real), extra)
            inv["waiver_questions"] = _question_rows(state, waiver_questions(job, real.get("packet") or [], extra, real.get("excluded_file_ids") or []), extra)
            _item_questions(job, inv.get("packet") or [])
            for item in inv.get("packet") or []:
                if item.get("required") and sov.name_only(job, item):   # shown missing, with Remove and the ways to fill it
                    item.update(present=False, name_only=True)
            subject, body = _pencil_email(state, job, by_id[inv["id"]], _payer(state, job["payer_id"]), PREVIEW_SENDER)
            inv["pencil_preview"] = {"subject": subject, "body": body}
    if not job.get("closed"):
        for item in out.get("closeout_packet") or []:
            if item.get("required") and sov.name_only(job, item):
                item.update(present=False, name_only=True)
        closeout = _retainage_packet(job)
        _item_questions(job, out.get("closeout_packet") or [])
        out["closeout_unconditional"] = _unconditional_rows(state, _unconditional_going(job, closeout, retainage=True))
        out["closeout_waiver_questions"] = _question_rows(state, waiver_questions(job, closeout))
    if _legacy_waiver_pending(job):
        # The older job-level waiver (Earlier payment): the signed one that would go out, or that
        # it is unclear which payment one is for while several are owed (B1).
        found = waiver_on_file(state, job, suggested_waiver_kind(job), legacy=True)
        out["legacy_waiver"] = {"on_file": bool(found and not found["ambiguous"]), "which": bool(found and found["ambiguous"]),
                                "record_label": found["record"]["label"] if found else "",
                                "record_id": found["record"]["id"] if found else ""}
    for release in out.get("retainage_waivers", []):
        if release.get("waiver_due") and not release.get("waiver_sent"):
            release["kind"] = release_due_kind(job, release)
            found = waiver_on_file(state, job, release["kind"], release_id=release["id"])
            release["on_file"] = bool(found and not found["ambiguous"])
            release["waiver_record_label"] = found["record"]["label"] if found else ""
            release["waiver_record_id"] = found["record"]["id"] if found else ""
            release["waiver_which"] = bool(found and found["ambiguous"])
    out.setdefault("retainage_waivers", [])
    out.setdefault("retainage_payments", [])
    out["waiver_state"] = job_state(job)


def job_state(job: dict[str, Any]) -> str:
    """The two-letter state of the job's address, or blank when it doesn't say one (M22)."""
    from .validate import parse_legacy_address
    for key in ("state", "address_state"):  # a job saved with its address in parts
        if re.fullmatch(r"[A-Za-z]{2}", str(job.get(key) or "")):
            return str(job[key]).upper()
    saved = str((job.get("address_parts") or {}).get("state") or "")   # New job's four boxes (J1)
    if re.fullmatch(r"[A-Za-z]{2}", saved):
        return saved.upper()
    try:
        parts = parse_legacy_address(str(job.get("address") or ""))
    except Exception:  # an address the reader can't split names no state
        return ""
    found = str((parts or {}).get("state") or "").upper()
    return found if re.fullmatch(r"[A-Z]{2}", found) else ""


def _totals(views: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "unbilled_cents": sum(j["money"]["unbilled_completed_cents"] for j in views),
        "outstanding_cents": sum(j["money"]["outstanding_cents"] for j in views),
        "retainage_cents": sum(j["money"]["retainage_open_cents"] for j in views),
        # Blocked once there is a draft to block or the work is done; before that the
        # blockers are what the next pay app will need (the app shows them that way too).
        "blocked_jobs": sum(1 for j in views if j["blocked"] and not j.get("closed")),
    }


def snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Everything, every job in full (export, tests, the helper)."""
    out = deepcopy(state)
    out["jobs"] = [job_view(state, j) for j in state["jobs"]]
    out.setdefault("email_log", [])
    out["audit_chain"] = check_audit(state)
    out["trial"] = trial.status(state)
    out["totals"] = _totals(out["jobs"])
    return out


# The app's page load (P2-6). Open jobs in full; jobs closed recently as short summaries; older
# closed jobs, the Outbox and the Audit log are paged from their own endpoints.
RECENT_CLOSED_DAYS = 90
RECENT_EMAILS = 20
# Fields a closed job's summary leaves out; /api/jobs/<id> has them.
SUMMARY_DROPS = ("blockers", "close_blockers", "close_blocker_items", "closeout_packet", "evidence", "records", "daily_records", "follow_ups",
                 "retainage_chases", "sov", "sov_view", "contract_doc")


def job_summary(state: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """A closed job for lists: money, pay apps, payments and change orders, without the history."""
    out = hide_link_secrets({k: deepcopy(v) for k, v in job.items() if k not in SUMMARY_DROPS})
    out["money"] = job_money(job)
    out["status_label"] = status_label(job, billing_style(state))
    out["blockers"], out["close_blockers"], out["close_blocker_items"] = [], [], []
    out["blocked"], out["needs_paperwork"], out["blocker_button"] = False, False, ""
    out.setdefault("done_notes", "")
    for inv in out.get("invoices", []):
        inv.pop("lines", None)
    for co in out.get("change_orders", []):
        co.pop("snapshot", None)
    _co_flags(out)
    out["started_on"] = job_started_on(state, job)
    out["stage"] = job_stage(job, out["money"])
    out["summary"] = True
    return out


def _closed_recently(job: dict[str, Any], since: str) -> bool:
    return (job.get("closed_at") or job.get("completed_at") or "") >= since


def _email_failed(e: dict[str, Any]) -> bool:
    status = e.get("status") or ""
    return status.startswith("failed") or status.startswith("not sent")


def email_failures(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Emails that didn't go out and still matter, newest first: the latest email about an item
    (the same job, kind and pay app) failed, the job is open, the pay app is not void, and a pay
    app or follow-up is still unpaid.
    The demo's sample Outbox rows (seed.py) are not real sends and are left out."""
    jobs = {j["id"]: j for j in state.get("jobs", [])}
    latest: dict[tuple, dict[str, Any]] = {}
    for e in state.get("email_log", []):
        # A waiver or a signature request names its pay app or change order only in the subject.
        which = e.get("payapp_invoice_id") or (e.get("subject") if e.get("kind") in {"waiver", "co_signature"} else None)
        latest[(e.get("job_id"), e.get("kind"), which)] = e
    out = []
    for e in latest.values():
        job = jobs.get(e.get("job_id"))
        # Demo files seeded before the flag: their sample rows' ids are "eml-<pay app id>".
        sample = e.get("sample") or e.get("id") == f"eml-{e.get('payapp_invoice_id')}"
        if not _email_failed(e) or sample or not job or job.get("closed"):
            continue
        if email_moot(job, e):
            continue
        out.append(e)
    return sorted(out, key=lambda e: e.get("at", ""), reverse=True)


def mark_moot(state: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same email rows, each one that didn't go out (or is still "sending" or "queued", which
    may be stuck) and no longer needs to carrying "moot" (the reason), so the page shows why
    instead of a Send again that would be refused (outbox.retry_email)."""
    jobs = {j["id"]: j for j in state.get("jobs", [])}
    for r in rows:
        status = str(r.get("status") or "")
        job = jobs.get(r.get("job_id"))
        if job and (status.startswith("failed") or status.startswith("not sent") or status in {"sending", "queued"}):
            # Job history rows keep the email's kind in email_kind and its subject in text.
            reason = email_moot(job, {**r, "kind": r.get("email_kind") or r.get("kind"), "subject": r.get("subject") or r.get("text")})
            if reason:
                r["moot"] = reason
    return rows


def email_moot(job: dict[str, Any], e: dict[str, Any]) -> str:
    """Why an email that didn't go out no longer needs to, or "" when it still does. Today leaves
    these off its list, and Send again refuses them, so an old follow-up never goes out after the
    money is in."""
    inv = next((i for i in job.get("invoices", []) if i["id"] == e.get("payapp_invoice_id")), None)
    if inv and (inv.get("status") == "void" or inv.get("replaced")):
        number = inv.get("number", "That pay app")
        if inv.get("replaced"):
            return f"{number} was replaced."
        return f"{number} was pulled back." if inv.get("sent_at") else f"The draft {number} was deleted."
    # Once the money is in, a pay app or follow-up that didn't go out no longer matters.
    if e.get("kind") == "pay_app" and inv and inv.get("status") == "paid":
        return f"{inv.get('number', 'That pay app')} is paid."
    if e.get("kind") == "follow_up" and not any(i.get("status") in OPEN_FOR_PAYMENT for i in job.get("invoices", [])):
        return "Nothing on this job is waiting for payment now."
    # A draft for review is moot once the pay app itself went out; a signature request once
    # the change order is signed or withdrawn.
    if e.get("kind") == "pencil" and inv and inv.get("status") not in DRAFT_STATUSES:
        return f"{inv.get('number', 'That pay app')} has gone out already."
    if e.get("kind") == "co_signature" and not any(
        co.get("status") == "pending" and f"change order {co['number']} " in (e.get("subject") or "")
        for co in job.get("change_orders", [])
    ):
        return "That change order is signed or withdrawn."
    return ""


def light_snapshot(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """What /api/state sends: bounded by the open work, not by the company's whole history."""
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=RECENT_CLOSED_DAYS)).isoformat()
    heavy = {"jobs", "audit", "email_log", "inbox"}
    out = {k: deepcopy(v) for k, v in state.items() if k not in heavy}
    full, jobs = [], []
    closed_count, closed_collected = 0, 0
    for job in state["jobs"]:
        if job.get("closed"):
            closed_count += 1
            closed_collected += job_money(job)["collected_cents"]
            if _closed_recently(job, since):
                jobs.append(job_summary(state, job))
            continue
        view = job_view(state, job)
        view.pop("payer")  # the app joins payers by payer_id
        jobs.append(view)
        full.append(view)
    out["jobs"] = jobs
    # "First pay app to this payer" must not depend on which jobs were sent.
    sent = {j["payer_id"] for j in state["jobs"] if any(i.get("sent_at") for i in j.get("invoices", []))}
    timings = payer_timing(state)
    # Every job of each customer, finished ones too: one with none can be deleted (note 47).
    job_counts: dict[str, int] = {}
    for j in state["jobs"]:
        job_counts[j.get("payer_id", "")] = job_counts.get(j.get("payer_id", ""), 0) + 1
    for payer in out.get("payers", []):
        payer["sent_before"] = payer["id"] in sent
        payer["job_count"] = job_counts.get(payer["id"], 0)
        timing = timings.get(payer["id"])
        if timing:
            payer["paid_days_avg"], payer["paid_count"] = timing
        payer_extras(state, payer)   # its client type and paperwork items (S11)
        due = payapp_due(payer, (now or datetime.now(timezone.utc)).date())
        if due:
            payer["payapp_due_next"], payer["payapp_due_prev"] = due[0].isoformat(), due[1].isoformat()
    emails = state.get("email_log", [])
    # The Inbox: what is waiting and the last few done; Communications pages the rest (C1).
    inbox = state.get("inbox", [])
    out["inbox"] = deepcopy(inbox_for_page_load(inbox))
    out["inbox_total"] = len(inbox)
    out["email_log"] = mark_moot(state, deepcopy(emails[-RECENT_EMAILS:]))
    out["email_log_total"] = len(emails)
    # Today lists these with Send again, however old they are; the count is for the Today badge.
    failures = email_failures(state)
    out["email_failures"] = [{k: v for k, v in e.items() if k != "body"} for e in failures[:RECENT_EMAILS]]
    out["pending_email_failures"] = len(failures)
    out["audit_total"] = len(state.get("audit", []))
    # Today's queue, drawn from the same rows as the daily email (R4-10).
    out["today_rows"] = today_rows(state, now)
    # The To Do list's own tasks (G5): open ones, and those done in the last 30 days.
    out["tasks"] = visible_tasks(state, now)
    out["task_assignments"] = deepcopy(state.get("task_assignments") or {})
    out["closed_jobs_total"] = closed_count
    # The Client playbook's paperwork boxes (S11): the standard catalog, one list for both ends.
    out["paperwork_catalog"] = deepcopy(PAPERWORK_CATALOG)
    out["audit_chain"] = check_audit(state)
    out["trial"] = trial.status(state)
    out["totals"] = {**_totals(full), "closed_collected_cents": closed_collected}
    return out


def _day_words(value: Any) -> str:
    """"Oct 3, 2026" from an ISO date or time (the app's fmtDate); "" when there is none."""
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return ""
    return f"{d.strftime('%b')} {d.day}, {d.year}"


WENT_OUT = {"sent", "approved", "partial", "paid"}
# Where an Inbox item came from, as the app names it (jobs.js SOURCE_LABELS).
# The company's word for a pay app (R6-01): a company that bills homeowners in draws
# (billing_style "draws") reads "draw" on Today and in the daily email, as in the app
# (static/js/terms.js termText). keep: names someone typed (a job, a customer) that stay as typed.
_TERM_WORDS = [(re.compile(r"\bPay [Aa]pps\b"), "Draws"), (re.compile(r"\bpay apps\b"), "draws"),
               (re.compile(r"\bPay [Aa]pp\b"), "Draw"), (re.compile(r"\bpay app\b"), "draw")]


def billing_style(state: dict[str, Any]) -> str:
    return (state.get("company") or {}).get("billing_style") or "pay_apps"


def term_text(text: str, style: str, keep: tuple[str, ...] = ()) -> str:
    if style != "draws" or not text:
        return text
    kept = [k for k in keep if k]
    for n, k in enumerate(kept):
        text = text.replace(k, f"\x00{n}\x00")
    for pattern, word in _TERM_WORDS:
        text = pattern.sub(word, text)
    for n, k in enumerate(kept):
        text = text.replace(f"\x00{n}\x00", k)
    return text


SOURCE_LABELS = {"upload": "Upload", "paste": "Pasted", "email": "Email", "sms": "Text message", "notes": "Notes", "payer_page": "Client page"}


def _pay_app_list(invoices: list[dict[str, Any]]) -> str:
    """"PA-01" or "PA-01 and PA-02" or "PA-01, PA-02 and PA-03": the pay apps one follow-up names."""
    n = [i["number"] for i in invoices]
    return f"{', '.join(n[:-1])} and {n[-1]}" if len(n) > 1 else (n[0] if n else "the pay app")


def today_rows(state: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """What needs a person today, most urgent first (R4-10): the one rule set for Today's queue
    (light_snapshot sends it; static/js/today.js draws it) and the daily email.
    Each row: {rank, kind, job_id, job, payer, label, tone, text, note, button, path, invoice_id,
    co_id, email_id, item_id}. label and tone are the pill ("Ready to send", green); text the line
    under the job name; note a reason shown after it on Today only; button the main button's
    words (the app maps `kind` to what it does); path the app page that does it.
    Kinds: email_failed, says_done, blocked, ready, waiver, not_billed, due_soon, new_job,
    follow_up, approved, inbox. "approved" is a pay app the customer approved with a date to pay:
    waiting, not due, so it has no button.
    The To Do list (T3, G5) adds key (kind:job_id:item), due (YYYY-MM-DD: the client's next pay app
    cutoff for a pay app to send or bill, the follow-up day, the day the client said they'd pay,
    else today), ball_in_court ("us" or "client") and assignee_id/assignee_name
    (domain/tasks.py decorate_rows)."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    jobs = {j["id"]: j for j in state.get("jobs", []) if not j.get("closed")}
    payers = {p["id"]: p for p in state.get("payers", [])}
    style = billing_style(state)
    out: list[dict[str, Any]] = []

    def add(rank: float, kind: str, job: dict[str, Any], label: str, tone: str, text: str, button: str,
            path: str = "", **ids: str) -> None:
        payer = payers.get(job.get("payer_id"), {})
        # In the company's word for a pay app (R6-01). A failed email's subject and what came in
        # through the Inbox are shown as they are.
        if kind not in {"email_failed", "says_done", "inbox"}:
            keep = (job.get("name", ""), payer.get("name", ""), payer.get("approver", ""), payer.get("ap_email", ""))
            label, text, button = (term_text(x, style, keep) for x in (label, text, button))
        out.append({"rank": rank, "kind": kind, "job_id": job["id"], "job": job["name"], "payer": payer.get("name", ""),
                    "label": label, "tone": tone, "text": text, "note": ids.pop("note", ""), "button": button,
                    "path": path or f"/jobs/{job['id']}", "invoice_id": "", "co_id": "", "email_id": "", "item_id": "",
                    "due": ids.pop("due", ""), **ids})

    # An email that didn't go out: the latest per pay app or follow-up, open jobs only.
    for e in email_failures(state):
        job = jobs.get(e.get("job_id"))
        if job:
            add(0.5, "email_failed", job, "Didn't go out", "red", f"{e.get('subject', '')} to {e.get('to', '')}: {e.get('status', '')}",
                "Send again", "/dashboard", email_id=e.get("id", ""), invoice_id=e.get("payapp_invoice_id") or "")
    inbox = [i for i in state.get("inbox", []) if i.get("status") == "new"]
    for item in inbox:
        job = jobs.get(item.get("says_done_job_id"))
        if job:
            source = SOURCE_LABELS.get(item.get("source", ""), item.get("source", ""))
            first = (item.get("text") or item.get("subject") or "").split("\n")[0][:120]
            add(0, "says_done", job, "Job's done?", "copper", f"{source}{' from ' + item['from'] if item.get('from') else ''}: {first}",
                term_text("Final pay app", style), "/dashboard", item_id=item.get("id", ""))
    for job in jobs.values():
        payer = payers.get(job.get("payer_id"), {})
        invoices = job.get("invoices", [])
        draft = next((i for i in invoices if i["status"] in DRAFT_STATUSES), None)
        m = job_money(job)
        items = blocker_items(job)
        # The payer's monthly cutoff (playbook): within five days, and nothing billed since the
        # last one. Only a pay app that went out counts: a draft stuck on paperwork still leaves
        # the cutoff ahead.
        due = payapp_due(payer, today)
        billed_this_cycle = bool(due) and any(
            i["status"] in WENT_OUT and (i.get("sent_at") or i.get("created_at") or "")[:10] > due[1].isoformat() for i in invoices)
        may_bill = job.get("work_status") != "complete" or m["unbilled_completed_cents"] > 0
        due_soon = bool(due) and may_bill and not billed_this_cycle and (due[0] - today).days <= 5
        before = len(out)
        cutoff_day = due[0].isoformat() if due else ""
        # One next step per job, in the job page's order (jobActions): blockers first.
        if draft and draft.get("pencil_status") == "sent":
            since = f" since {_day_words(draft['pencil_sent_at'])}" if draft.get("pencil_sent_at") else ""
            add(4.5, "pencil_out", job, "With the client", "copper", f"{draft['number']} is with {payer.get('approver') or payer.get('name', 'the client')} for their review{since}. "
                "When they answer, tap Client agreed to the draft or Client wants changes.", "", invoice_id=draft["id"], due=cutoff_day)
        elif is_blocked(job):
            more = f" And {len(items) - 1} more." if len(items) > 1 else ""
            paperwork = needs_paperwork(job)
            cos = [b for b in items if b["kind"] == "co"]
            only_co = cos[0]["co_id"] if cos and len(cos) == len(items) else ""
            add(3, "blocked", job, "Needs supporting documents" if paperwork else "Blocked", "amber" if paperwork else "red", items[0]["text"] + more,
                blocker_button(job), f"/money/change-order/{job['id']}/{only_co}" if only_co else f"/money/packet/{job['id']}/{draft['id']}" if draft else f"/money/packet/{job['id']}",
                invoice_id=draft["id"] if draft else "", co_id=only_co, due=cutoff_day,
                packet_item_id=items[0].get("item_id", ""))
        elif draft:
            changes = draft.get("pencil_status") == "corrections"
            said = {"agreed": "The client agreed to it. ", "corrections": f"The client wants changes{': ' + draft['pencil_note'] if draft.get('pencil_note') else ''}. Fix it, then "}.get(draft.get("pencil_status"), "")
            email = "" if str(payer.get("ap_email") or "").strip() else f"{'add' if changes else 'Add'} the pay app email for {payer.get('name', 'the client')}, then "
            add(1, "ready", job, "Ready to send", "green", f"{draft['number']} for {money(draft['amount_cents'])} to {payer.get('approver') or payer.get('name', '')} is ready. "
                f"{said}{email}Review & send.", "Review & send", f"/money/pay-app/{job['id']}/{draft['id']}", invoice_id=draft["id"], due=cutoff_day)
        elif job.get("waiver_due") and not job.get("waiver_sent"):
            released = any(r.get("waiver_due") and not r.get("waiver_sent") for r in job.get("retainage_waivers", []))
            add(2, "waiver", job, "Waiver due", "amber", ("Retainage received" if released and not any(
                inv.get("waiver_due") and not inv.get("waiver_sent") for inv in job["invoices"]) else "Paid in full")
                + ": the client is waiting for the lien waiver. Choose waiver and send.", "Choose waiver and send",
                f"/money/waiver/{job['id']}")
        elif job.get("work_status") == "complete" and m["unbilled_completed_cents"] > 0:
            add(5, "not_billed", job, "Not billed", "copper",
                f"{money(m['unbilled_completed_cents'])} of finished work isn't billed. Draft pay app.", "Draft pay app", due=cutoff_day)
        elif due_soon:
            add(2.5, "due_soon", job, "Due soon", "amber", "Record this month's progress and draft the pay app.", "Progress pay app", due=cutoff_day)
        # A new job (R2-04): nothing billed on it yet, so Today says what comes next instead of nothing.
        elif not any(i["status"] not in {"void", "replaced"} for i in invoices):
            add(5, "new_job", job, "Not billed yet", "copper", f"{job['name']} has no pay app yet. Bill the work done so far with Progress pay app, or use Final pay app.",
                "Progress pay app", due=cutoff_day)
        # The work is finished and billed, and nothing is out with the client: closeout, then the
        # money held back, then Finish Job, in the job page's order and words (jobActions).
        elif m["refund_owed_cents"] and not any(i["status"] in OPEN_FOR_PAYMENT for i in invoices):
            add(4.5, "refund_owed", job, "Owed back", "amber", _owed_text(m), "Open job")
        elif job.get("work_status") == "complete" and m["outstanding_cents"] == 0:
            missing = closeout_missing(job)
            asked = job.get("last_retainage_chase_at")
            asked_due = _aware(asked) + timedelta(days=_int_or(payer.get("follow_up_days"), 7)) if asked else None
            if missing:
                add(5.5, "closeout", job, "Closeout", "amber", f"Closeout: missing {', '.join(i['label'] for i in missing)}. Open closeout documents.", "Open closeout documents")
            elif m["retainage_open_cents"] > 0:
                if not asked_due or now >= asked_due:
                    add(5.5, "retainage", job, "Retainage", "copper", f"{money(m['retainage_open_cents'])} held back. Closeout documents are in. Ask for retainage.", "Ask for retainage")
                else:   # asked already: waiting on the client until it can be asked again, so not in the email
                    add(5.6, "retainage_wait", job, "Retainage", "copper", f"Asked for the {money(m['retainage_open_cents'])} held back {_day_words(asked)}. "
                        f"Ask again from {_day_words(asked_due.isoformat())}, or Record retainage paid.", "Record retainage paid", due=asked_due.date().isoformat())
            elif close_blockers(job):
                add(6.5, "finish_held", job, "Before you finish", "amber", "Before you finish: " + close_blockers(job)[0], "Open job")
            else:
                add(7, "finish", job, "Ready to finish", "green", "Ready to finish the job. Finish job.", "Finish job")
        # A cutoff makes the job's row more urgent, and says so.
        if due_soon and len(out) > before:
            row = out[-1]
            cutoff = term_text("Pay app due to", style)
            if cutoff not in row["text"]:
                row["text"] += f" {cutoff} {payer.get('name', '')} by {_day_words(due[0].isoformat())}."
            row["rank"] = min(row["rank"], 1.5)
        # A pay app sent and unpaid past the payer's follow-up window is its own row. One follow-up
        # names every unpaid pay app; after a logged conversation the row waits for the date
        # noted (R4-06).
        follow = follow_up_due(job, payer)
        if follow:
            unpaid = [i for i in invoices if i["status"] in OPEN_FOR_PAYMENT]
            oldest = unpaid[0]
            last = oldest.get("last_follow_up_at") or oldest.get("sent_at") or ""
            noted = bool(job.get("expected_payment_on")) and (job.get("contact_noted_at") or "") >= last
            promised = None if noted else expected_payment(oldest)
            if now >= follow:
                names = f"{_pay_app_list(unpaid)} are unpaid; the oldest" if len(unpaid) > 1 else oldest["number"]
                since = f", last follow-up {_day_words(oldest['last_follow_up_at'])}" if oldest.get("last_follow_up_at") else ""
                add(4, "follow_up", job, "Follow up", "copper",
                    f"{names} was sent {_day_words(oldest.get('sent_at'))}{since}. Emails {payer.get('ap_email') or 'the client'}.",
                    "Send follow-up", invoice_id=oldest["id"], due=follow.date().isoformat())
            # Approved on the customer's page (R4-08): no follow-up until the day after the date they gave.
            elif promised:
                add(4.5, "approved", job, "Approved", "green",
                    f"Approved by {oldest.get('approved_by_payer') or payer.get('approver') or payer.get('name', '')}, "
                    f"expect payment {_day_words(promised.isoformat())} ({oldest['number']}).", "", invoice_id=oldest["id"], due=promised.isoformat())
    for item in inbox:
        job = jobs.get(item.get("suggested_job_id"))
        if job and not item.get("says_done_job_id"):
            # item_kind: the kind the Inbox guessed, which the row shows and lets you change before filing.
            add(6, "inbox", job, "Inbox", "", (item.get("subject") or item.get("text") or "An item").split("\n")[0][:100], "Add to This Job",
                "/communications", item_id=item.get("id", ""), note=item.get("match_reason") or "", item_kind=item.get("kind") or "note")
    return decorate_rows(state, sorted(out, key=lambda r: r["rank"]), today)


DIGEST_NOUNS = {
    "email_failed": ("email that didn't go out", "emails that didn't go out"), "says_done": ("job that may be done", "jobs that may be done"),
    "blocked": ("pay app missing supporting documents", "pay apps missing supporting documents"), "ready": ("pay app to send", "pay apps to send"),
    "waiver": ("waiver", "waivers"), "due_soon": ("pay app due soon", "pay apps due soon"), "follow_up": ("follow-up", "follow-ups"),
    "not_billed": ("job to bill", "jobs to bill"), "new_job": ("new job to bill", "new jobs to bill"), "inbox": ("Inbox item", "Inbox items"),
    "closeout": ("job missing closeout documents", "jobs missing closeout documents"), "retainage": ("retainage to ask for", "retainages to ask for"),
    "finish": ("job ready to finish", "jobs ready to finish"), "finish_held": ("job held up before finishing", "jobs held up before finishing"),
}
# Finishing is never urgent: these rows stay on the Dashboard, and the email names them once a week.
WEEKLY_DIGEST_KINDS = {"finish", "finish_held"}


def _digest_week_day(job: dict[str, Any] | None, today: date) -> bool:
    """Whether a job's finish row goes in today's email: every seventh day from the day the work
    was marked done (Mondays for an older job with no date). Worked out, not stored, so a restart
    and each person's copy of the email agree."""
    try:
        done = date.fromisoformat(str((job or {}).get("completed_at") or "")[:10])
    except ValueError:
        return today.weekday() == 0
    return (today - done).days % 7 == 0


def daily_digest(state: dict[str, Any], base: str = "", now: datetime | None = None, user_id: str | None = None,
                 owner: bool = True) -> tuple[str, str] | None:
    """The daily "due today" email (R4-10): (subject, body) listing every Today row that needs a
    person, each with its link, and the open tasks due today or before; None on a day with nothing
    due. A pay app the customer approved with a date to pay is waiting, not due, and is left out.
    With user_id, only that person's items (G5): those assigned to them and, for an owner, those
    assigned to nobody. A "due soon" row is listed as soon as it shows, so there is time to get
    the pay app ready before the client's cutoff; only a row someone dated by hand for a later day
    waits for that day (review C7). Work that is ready (a pay app to send, a block to clear, work
    not billed) is listed whatever its client's cutoff. A job to finish is listed once a week."""
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()

    def due_now(r: dict[str, Any]) -> bool:
        by_hand = (r.get("due") or "") != (r.get("own_due") or "")
        return not by_hand or (r.get("due") or day) <= day

    by_id = {j["id"]: j for j in state.get("jobs", [])}

    def this_week(r: dict[str, Any]) -> bool:
        return r["kind"] not in WEEKLY_DIGEST_KINDS or _digest_week_day(by_id.get(r["job_id"]), now.date())

    rows = rows_for_user([r for r in today_rows(state, now) if r["kind"] in DIGEST_NOUNS and due_now(r) and this_week(r)], user_id, owner)
    tasks = rows_for_user(tasks_due_by(state, now.date()), user_id, owner)
    if not rows and not tasks:
        return None
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    style = billing_style(state)
    parts = [term_text(f"{n} {DIGEST_NOUNS[k][0] if n == 1 else DIGEST_NOUNS[k][1]}", style) for k, n in counts.items()]
    if tasks:
        parts.append(f"{len(tasks)} task{'' if len(tasks) == 1 else 's'}")
    subject = f"Due today in Duracash: {', '.join(parts)}"
    lines = [f"- {r['job']}: {r['label']}. {r['text']}" + (f"\n  {base}{r['path']}" if base else "") for r in rows]
    jobs = {j["id"]: j for j in state.get("jobs", [])}
    for t in tasks:
        job = jobs.get(t.get("job_id") or "")
        late = f", due {_day_words(t['due'])}" if t.get("due") and t["due"] < now.date().isoformat() else ""
        lines.append(f"- Task: {t['title']}{' (' + job['name'] + ')' if job else ''}{late}" + (f"\n  {base}/dashboard" if base else ""))
    company = state.get("company", {}).get("name", "")
    body = (f"What needs you today{' at ' + company if company else ''}:\n\n" + "\n".join(lines)
            + "\n\nNothing goes out until you approve it in Duracash."
            + "\nTurn this email off in Settings, Company: Daily email of what's due.")
    return subject, body


def page(rows: list[dict[str, Any]], offset: int, limit: int, job_id: str = "") -> dict[str, Any]:
    """Newest first, optionally one job's rows: for the Outbox and Audit pages."""
    picked = [r for r in reversed(rows) if not job_id or r.get("job_id") == job_id]
    return {"entries": deepcopy(picked[offset:offset + limit]), "total": len(picked), "offset": offset, "limit": limit}


# The app's own bookkeeping that comes with a person's action (round 1, item 14): a file put on a
# supporting document, the check's warning, the confirm that came with it, a suggestion from the
# job records, an Inbox item filed. In a job's Activity it goes under the action it came with.
ACTIVITY_FOLLOW_ON = {"add_record", "attach_record", "packet_attachment", "document_flagged", "packet_confirmed", "import_filed", "record_filed", "add_evidence"}
FOLLOW_ON_SECONDS = 5
APP_ACTOR = "duracash"


def _seconds_apart(a: str, b: str) -> float:
    try:
        return abs((_aware(b) - _aware(a)).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


def _one_per_action(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Oldest first in, oldest first out: bookkeeping written within a few seconds of the action
    before it goes under that action as its details, when it is the same person's or the app's own.
    Another person's never goes under it, and what can't be grouped (the app's own later, a
    person's own confirm) is an entry of its own: nothing is left out (Round 1 hunt)."""
    out: list[dict[str, Any]] = []
    lead = None
    for r in rows:
        if r["kind"] == "action" and r["action"] in ACTIVITY_FOLLOW_ON:
            if lead is not None and _seconds_apart(lead["at"], r["at"]) <= FOLLOW_ON_SECONDS and r["who"] in (lead["who"], APP_ACTOR):
                lead.setdefault("details", []).append({k: r[k] for k in ("action", "text", "at", "who")})
                continue
        out.append(r)
        if r["kind"] == "action":
            lead = r
    return out


def timeline_page(state: dict[str, Any], job_id: str, offset: int, limit: int) -> dict[str, Any]:
    """One job's history, newest first (UI/UX Option B): one entry per action on the job, with the
    app's bookkeeping that came with it as its details (_one_per_action), and every email with its
    delivery status (bodies left out). The activity log keeps every row."""
    # One sequence for both: an action's place is its row number in the audit trail, an email's
    # is between the audit rows written before and after it (audit_seq). Older emails, from
    # before audit_seq, go after every audit row of the same second or earlier. Times are only
    # to the second, so they can't order things done in the same second.
    audit_rows = state.get("audit", [])
    stamps = [a.get("at", "") for a in audit_rows]
    keyed = [((n, 0), {"kind": "action", "at": a.get("at", ""), "action": a.get("action", ""), "text": a.get("detail", ""), "who": a.get("actor", "")})
             for n, a in enumerate(audit_rows) if a.get("job_id") == job_id]
    for n, e in enumerate(state.get("email_log", [])):
        if e.get("job_id") != job_id:
            continue
        seq = e.get("audit_seq")
        place = seq - 0.5 if isinstance(seq, int) else sum(1 for t in stamps if t <= e.get("at", "")) - 0.5
        keyed.append(((place, n), {**{k: v for k, v in e.items() if k not in {"body", "subject", "approved_by", "kind", "audit_seq"}},
                                   "kind": "email", "email_kind": e.get("kind", ""), "at": e.get("at", ""), "text": e.get("subject", ""),
                                   "who": e.get("approved_by", "")}))
    keyed.sort(key=lambda kr: kr[0])
    rows = list(reversed(_one_per_action([r for _, r in keyed])))
    return {"entries": mark_moot(state, deepcopy(rows[offset:offset + limit])), "total": len(rows), "offset": offset, "limit": limit}


def jobs_page(state: dict[str, Any], status: str, offset: int, limit: int, q: str = "") -> dict[str, Any]:
    """Jobs for a list, newest first: open in full, closed as summaries."""
    q = q.strip().lower()
    picked = [j for j in reversed(state["jobs"])
              if (status == "all" or bool(j.get("closed")) == (status == "closed"))
              and (not q or q in f"{j.get('name', '')} {j.get('code', '')} {j.get('address', '')}".lower())]
    if status == "closed":
        picked.sort(key=lambda j: j.get("closed_at") or j.get("completed_at") or "", reverse=True)
    rows = [job_summary(state, j) if j.get("closed") else job_view(state, j) for j in picked[offset:offset + limit]]
    for r in rows:
        r.pop("payer", None)
    return {"jobs": rows, "total": len(picked), "offset": offset, "limit": limit}


def _refresh_draft_gate(job: dict[str, Any], state: dict[str, Any] | None = None) -> None:
    """Recheck the open draft's blockers. With `state`, first fill missing paperwork from the job record."""
    invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] in {"draft", "awaiting_approval"}), None)
    if not invoice:
        return
    # Whether it is the final one can move under it (a change order written or signed, say).
    invoice["final"] = is_final(job)
    if invoice.get("packet_id") != "job-packet":
        _name_conditional_waiver(job, invoice)
    if state is not None:
        _attach_from_record(state, job, active_packet(job), invoice["number"])
    _ask_which_waiver(job, invoice.get("packet") or [])
    _items_follow_files(job, invoice)  # an item whose files are all left out of the email stays missing (M4)
    invoice["status"] = "draft" if blocker_lines(job) else "awaiting_approval"


def _send_to(payer: dict[str, Any]) -> str:
    """Where an email to this payer goes. A job can be saved without it (the email is asked for
    before the first send), so anything that emails the payer refuses until it is set."""
    to = str(payer.get("ap_email") or "").strip()
    if not to:
        message = f"Add the email pay apps go to for {payer['name']}."
        raise GateError(message, fields={"ap_email": message})
    return to


def _record_email(
    state: dict[str, Any],
    job_id: str,
    kind: str,
    to: str,
    subject: str,
    body: str,
    actor: str,
    attachments: list[str] | None = None,
    cc: list[str] | None = None,
    payapp_invoice_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write an approved email to the Outbox. The server delivers queued emails after the change is saved.
    `extra` is kept on the entry too (a waiver email names its record, type and pay app, M18)."""
    state.setdefault("email_log", []).append({**(extra or {}),
        "id": "eml-" + secrets.token_hex(5),
        "at": utcnow(),
        # Where the email falls among the audit rows: the job's history orders by it (timeline_page).
        "audit_seq": len(state.get("audit", [])),
        "job_id": job_id,
        "kind": kind,
        "to": to,
        "subject": subject,
        "body": body,
        "approved_by": actor,
        "attachments": list(attachments or []),
        "cc": list(cc or []),
        # The pay app PDF is made when the email is sent, from the pay app as it stands then.
        "payapp_invoice_id": payapp_invoice_id,
        "status": "queued",
    })


PAYER_DAY_FIELDS = {"follow_up_days": "the days between follow-ups", "usual_approval_days": "the days they usually take to pay",
                    "payment_terms_days": "the days they have to pay"}


def _payer_day(k: str, v: Any) -> tuple[Any, str]:
    """(the value to save, or None to clear it; an error) for a Client playbook day field."""
    if k == "payapp_due_day":
        # The payer's cutoff for this month's pay app (a contract date, not a legal one); blank clears it.
        if v in (None, ""):
            return None, ""
        try:
            day = parse_int(v, "the day pay apps are due", k)
        except GateError:
            day = 0
        if not 1 <= day <= 31:
            return None, "Enter a day of the month from 1 to 31 for when pay apps are due, or leave it blank."
        return day, ""
    if v == "":
        return None, ""  # blank goes back to the usual number
    try:
        days = parse_int(v, PAYER_DAY_FIELDS[k], k)
    except GateError:
        days = None
    if days is None or not 1 <= days <= 365:
        return None, f"Enter {PAYER_DAY_FIELDS[k]} as a whole number from 1 to 365."
    return days, ""


def update_payer(state: dict[str, Any], payer_id: str, updates: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    payer = _payer(state, payer_id)
    text_fields = {"approver": "who signs off on your bills", "ap_email": "the email pay apps go to",
                   "packet": "the supporting documents needed", "rejects_when": "when they send pay apps back", "copy": "who else to email",
                   "name": "the client's name", "phone": "the phone number", "escalation_email": "who to copy on late follow-ups"}
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for k, v in updates.items():
        if k == "approver_confirmed":
            continue  # read below: it says who signs off was answered, it isn't stored as sent
        if k in PAYER_DAY_FIELDS or k == "payapp_due_day":
            if v is None and k != "payapp_due_day":
                continue
            value, error = _payer_day(k, v)
            if error:
                errors[k] = error
            else:
                clean[k] = value
            continue
        if k == "retainage_pct":
            # The client playbook's "Usual retainage %" (round 1, item 1): blank goes back to its type's.
            if v in (None, "") or (isinstance(v, str) and not v.strip()):
                clean[k] = None
                continue
            pct = _int_or(v, None) if not isinstance(v, bool) else None
            if pct is None or not 0 <= pct <= 20 or (isinstance(v, str) and not v.strip().isdigit()):
                errors[k] = "Enter a retainage from 0 to 20 percent, or leave it blank."
            else:
                clean[k] = pct
            continue
        if v is None:
            continue
        if k in text_fields:
            if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                errors[k] = f"Enter {text_fields[k]} as text."
                continue
            clean[k] = str(v).strip()
        elif k == "role":
            if v not in ("Owner", "GC"):
                errors[k] = "Pick the client type."
                continue
            clean[k] = v
        elif k == "client_type":
            # Client type (J2): the finer word, which also sets the role's defaults.
            if not client_type_role(v):
                errors[k] = "Pick the client type."
                continue
            clean[k] = v
            clean["role"] = client_type_role(v)
    if clean.get("ap_email") and not EMAIL_RE.match(clean["ap_email"]):
        errors["ap_email"] = "Enter the email pay apps go to, like ap@builder.com."
    # A complete playbook stays complete (Decision 4): what is saved can be changed, not removed.
    # A client saved before the rule may stay without one until its next new job (Decision 3).
    for k in PLAYBOOK_FIELDS:
        if k in clean and not clean[k] and str(payer.get(k) or "").strip():
            errors[k] = PLAYBOOK_FIELDS[k]
    if clean.get("escalation_email") and not EMAIL_RE.match(clean["escalation_email"]):
        errors["escalation_email"] = "Enter one email to copy from the third follow-up, like owner@builder.com, or leave it blank."
    if clean.get("phone"):
        # G1: a US phone, saved as (404) 555-0123.
        phone = normalize_phone(clean["phone"])
        if phone:
            clean["phone"] = phone
        else:
            errors["phone"] = rule_message("phone")
    if "name" in clean:
        same = _payer_named(state, clean["name"], other_than=payer["id"]) if clean["name"] else None
        if not clean["name"]:
            errors["name"] = "Enter the client's name."
        elif check_field("name", clean["name"]):
            errors["name"] = rule_message("name")
        elif same:
            errors["name"] = f"Another client is already called {same['name']}."
        elif clean["name"] == payer["name"]:
            clean.pop("name")
    if "role" in clean and "client_type" not in clean and payer.get("role") != clean["role"]:
        # Only Owner or GC sent (an older page): the type follows it.
        clean["client_type"] = "gc" if clean["role"] == "GC" else "owner"
    if errors:
        raise GateError("Client playbook not saved.", fields=errors)
    changed = []
    was = payer["name"]
    signer_was = payer.get("approver")
    for k, v in clean.items():
        if v is None:
            payer.pop(k, None)
        else:
            payer[k] = v
        changed.append(k)
    if clean.get("approver") or updates.get("approver_confirmed") is True:
        if signer_answered(payer.get("approver"), payer["name"], updates.get("approver_confirmed") is True):
            payer["approver_confirmed"] = True
        elif clean.get("approver") != signer_was:
            payer.pop("approver_confirmed", None)   # what was confirmed was someone else
    if clean.get("packet"):
        payer.pop("no_paperwork", None)  # a list typed in again replaces "nothing needed"
    elif clean.get("packet") == "":
        payer["no_paperwork"] = True  # an emptied list means nothing is needed; empty alone would bring the default back
    if "role" in clean:
        # The usual paperwork for a job follows who pays it.
        for job in state["jobs"]:
            if job.get("payer_id") == payer["id"]:
                job["payer_role"] = clean["role"]
    renamed = f" (was {was})" if "name" in clean else ""
    audit(state, "", "playbook_update", f"{actor} updated {payer['name']}{renamed}: {', '.join(changed) or 'no change'}.", actor)
    return payer


# A complete Client playbook (Decision 4): its name and client type, and these. Box -> the reason.
PLAYBOOK_FIELDS = {"approver": "Enter who signs off on your bills.", "ap_email": "Enter the email pay apps go to, like ap@builder.com.",
                   "phone": "Enter the client's phone number."}
SIGNER_IS_CLIENT = "Confirm who signs off on your bills. It is set to the client's own name."
PLAYBOOK_WORDS = {"client_type": "the client type", "approver": "who signs off", "ap_email": "email for pay apps", "phone": "phone"}


def signer_answered(signer: str, client_name: str, said: bool) -> bool:
    """Whether a save answers who signs off: the request says so (the screens do, their box starts
    empty), or it names someone other than the client. Sending the client's own name back doesn't."""
    if not str(signer or "").strip():
        return False
    return said or str(signer).strip().lower() != str(client_name or "").strip().lower()


def signer_defaulted(payer: dict[str, Any]) -> bool:
    """Who signs off still reads as the client's own name, as a blank box used to be saved, and no
    one has said so since: asked at the client's next new job. Saying it once settles it."""
    signer = str(payer.get("approver") or "").strip().lower()
    return bool(signer) and signer == str(payer.get("name") or "").strip().lower() and not payer.get("approver_confirmed")


def playbook_missing(payer: dict[str, Any]) -> list[str]:
    """The boxes a saved client still has to answer: a blank one, a client type never picked (one
    read from an older role isn't an answer) or a signer that only defaulted to the client."""
    out = [] if payer.get("client_type") in CLIENT_TYPES else ["client_type"]
    return out + [k for k in PLAYBOOK_FIELDS if not str(payer.get(k) or "").strip() or (k == "approver" and signer_defaulted(payer))]


def fill_missing_client_details(state: dict[str, Any], payer_id: str, body: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    """The boxes a saved client still has to answer, from anyone on the team: the same rule as New
    job, where a blank is filled since nothing goes anywhere yet. What is already saved, and every
    other setting, is changed on the Client playbook with the company permission."""
    payer = _payer(state, payer_id)
    missing = playbook_missing(payer)
    typed = {k: str(body[k]).strip() for k in ("client_type", *PLAYBOOK_FIELDS) if isinstance(body.get(k), str) and body[k].strip()}
    if not typed:
        raise GateError("Enter what is missing.")
    errors = {k: "This is already saved." for k in typed if k not in missing}
    if "client_type" in typed and not client_type_role(typed["client_type"]):
        errors["client_type"] = "Pick the client type."
    if "ap_email" in typed and not EMAIL_RE.match(typed["ap_email"]):
        errors["ap_email"] = PLAYBOOK_FIELDS["ap_email"]
    if "phone" in typed:
        typed["phone"] = normalize_phone(typed["phone"]) or typed["phone"]
        errors.update(check_field("phone", typed["phone"]))
    if "approver" in typed and not signer_answered(typed["approver"], payer["name"], body.get("approver_confirmed") is True):
        errors["approver"] = SIGNER_IS_CLIENT
    _refuse("Client not saved.", errors)
    for k, value in typed.items():
        payer[k] = value
        audit(state, "", "playbook_update", f"{actor} added {PLAYBOOK_WORDS[k]} to {payer['name']}: {value}.", actor)
    if "approver" in typed:
        payer["approver_confirmed"] = True
    if "client_type" in typed:
        payer["role"] = client_type_role(typed["client_type"])
        for job in state["jobs"]:
            if job.get("payer_id") == payer["id"]:
                job["payer_role"] = payer["role"]
    return payer


def _need_playbook(state: dict[str, Any], job: dict[str, Any]) -> None:
    """A pay app doesn't start or go out while the client's playbook is incomplete. A job from
    before the rule isn't held up (Decision 3): sending still needs the email (_send_to)."""
    if not job.get("playbook_required"):
        return
    payer = _payer(state, job["payer_id"])
    missing = playbook_missing(payer)
    if missing:
        raise GateError(f"{payer['name']}'s Client playbook isn't complete.",
                        [f"Add {PLAYBOOK_WORDS[k]} in the Client playbook." for k in missing],
                        extra={"playbook_missing": missing}, action={"label": "Open Client playbook", "view": "playbook"})


def _payer_named(state: dict[str, Any], name: str, other_than: str = "") -> dict[str, Any] | None:
    """The customer with this name, ignoring case (one name, one customer)."""
    key = (name or "").strip().lower()
    return next((p for p in state["payers"] if p["name"].strip().lower() == key and p["id"] != other_than), None)


# Words at the end of a company name that don't make it another customer: "ABC Builders",
# "ABC Builders Inc" and "ABC Builders, LLC" are one (Oct 23 note 5).
NAME_SUFFIXES = {"inc", "incorporated", "llc", "co", "corp", "corporation", "company", "ltd", "limited",
                 "lp", "llp", "pllc", "pc", "and"}


def customer_key(name: str) -> str:
    """A customer's name boiled down to spot a near-duplicate: lower case, no punctuation, single
    spaces, no "The" in front and no Inc, LLC, Co, Corp, Company or Ltd at the end. "The ABC
    Builders Co., Inc." and "abc  builders" both give "abc builders". A name that is only such
    words ("Company") keeps its last one."""
    text = (name or "").casefold().replace("&", " and ")
    text = re.sub(r"[.'’]", "", text)  # L.L.C. is LLC; O'Neil is ONeil
    words = re.sub(r"[\W_]+", " ", text).split()
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in NAME_SUFFIXES:
        words.pop()
    return " ".join(words)


def payer_near(state: dict[str, Any], name: str, other_than: str = "") -> dict[str, Any] | None:
    """A saved customer whose name nearly matches this one (customer_key) without being the same
    name, so the person can be asked "Same as ABC Builders?" instead of getting a second
    customer by accident. Never merges anything by itself."""
    key, exact = customer_key(name), (name or "").strip().lower()
    if not key:
        return None
    return next((p for p in state["payers"] if p["id"] != other_than and p["name"].strip().lower() != exact
                 and customer_key(p["name"]) == key), None)


def job_near(state: dict[str, Any], name: str) -> dict[str, Any] | None:
    """An open job whose name is this one, ignoring case, spacing and punctuation (Round 1, item 20):
    two "Peachtree Clinic electrical" jobs can't be told apart in the Inbox or a job picker."""
    key = " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))
    if not key:
        return None
    return next((j for j in state["jobs"] if not j.get("closed") and " ".join(re.findall(r"[a-z0-9]+", j["name"].lower())) == key), None)


def _same_customer_question(field: str, typed: str, near: dict[str, Any]) -> GateError:
    """The refusal that asks "Same as ABC Builders?": the page offers the saved customer or
    keeping the new name (sent again with new_customer: true)."""
    question = f"Same as {near['name']}? Use the saved client, or keep {typed} as a new one."
    return GateError(f"Same as {near['name']}?", fields={field: question},
                     extra={"same_customer": {"id": near["id"], "name": near["name"], "typed": typed}})


def _new_payer(state: dict[str, Any], name: str, role: str, approver: str, ap_email: str, phone: str,
               client_type: str = "", approver_confirmed: bool = False) -> dict[str, Any]:
    """A customer with the usual settings for its role, added to the company."""
    # Catalog labels (PAPERWORK_ITEMS), so the Client playbook shows them as ticked boxes.
    packet_rule = ", ".join(DEFAULT_PAPERWORK["GC" if role == "GC" else "Owner"])
    payer = {
        "id": "payer-" + secrets.token_hex(3),
        "name": name,
        "role": role,
        "client_type": client_type if client_type_role(client_type) == role else ("gc" if role == "GC" else "owner"),
        "approver": approver,
        "ap_email": ap_email,
        "phone": phone,
        "packet": packet_rule,
        "follow_up_days": 5 if role == "GC" else 7,
        "usual_approval_days": 21 if role == "GC" else 14,
        "rejects_when": "Missing required supporting documents.",
        "copy": "",
        "approver_confirmed": signer_answered(approver, name, approver_confirmed),  # both callers refuse a blank one
    }
    state["payers"].append(payer)
    return payer


def add_payer(state: dict[str, Any], body: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    """A customer added from Customer settings, before any job (R3-07), with a complete playbook
    (Decision 4). The client type (J2) sets the role's defaults; a paperwork list (S11, S13) and
    the day fields a contract import found (S12) may come too. A name nearly matching a saved one
    is asked about before the rest."""
    def text(key: str) -> str:
        v = body.get(key)
        return str(v).strip() if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""
    name, approver, ap_email, phone = text("name"), text("approver"), text("ap_email"), text("phone")
    client_type = text("client_type")
    role = client_type_role(client_type) or (text("role") if text("role") in {"Owner", "GC"} else "Owner")
    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Enter the client's name."
    elif check_field("name", name):
        errors["name"] = rule_message("name")
    elif _payer_named(state, name):
        errors["name"] = f"Another client is already called {_payer_named(state, name)['name']}."
    near = None if body.get("new_customer") is True or errors else payer_near(state, name)
    if client_type and not client_type_role(client_type):
        errors["client_type"] = "Pick the client type."
    elif not near and not client_type and text("role") not in {"Owner", "GC"}:
        errors["client_type"] = "Pick the client type."
    if ap_email and not EMAIL_RE.match(ap_email):
        errors["ap_email"] = PLAYBOOK_FIELDS["ap_email"]
    if phone:
        phone = normalize_phone(phone) or phone
        errors.update(check_field("phone", phone))
    if not near:
        typed = {"approver": approver, "ap_email": ap_email, "phone": phone}
        errors.update({k: PLAYBOOK_FIELDS[k] for k in PLAYBOOK_FIELDS if not typed[k]})
        if approver and not signer_answered(approver, name, body.get("approver_confirmed") is True):
            errors["approver"] = SIGNER_IS_CLIENT
    days: dict[str, Any] = {}
    for k in (*PAYER_DAY_FIELDS, "payapp_due_day"):
        if body.get(k) not in (None, ""):
            value, error = _payer_day(k, body.get(k))
            if error:
                errors[k] = error
            else:
                days[k] = value
    _refuse("Client not added.", errors)
    if near:
        raise _same_customer_question("name", name, near)
    payer = _new_payer(state, name, role, approver, ap_email, phone, client_type, body.get("approver_confirmed") is True)
    payer.update(days)
    if body.get("no_paperwork") is True:
        payer["packet"], payer["no_paperwork"] = "", True
    elif text("packet"):
        payer["packet"] = text("packet")
    audit(state, "", "payer_add", f"{actor} added the client {name}.", actor)
    return payer


def merge_payer(state: dict[str, Any], payer_id: str, into_id: str, actor: str = "owner") -> dict[str, Any]:
    """Two customers that are one (a typo, or "Ironleaf" and "Ironleaf GC"): every job of the
    first moves to the second, which keeps its own settings; the first is removed (R3-07)."""
    payer = _payer(state, payer_id)
    into = _payer(state, into_id) if into_id else None
    if not into or into["id"] == payer["id"]:
        raise GateError("Pick the client to merge into.", fields={"into": "Pick the client to merge into."})
    moved = [j for j in state["jobs"] if j.get("payer_id") == payer["id"]]
    for job in moved:
        job["payer_id"] = into["id"]
        job["payer_role"] = into.get("role", job.get("payer_role"))
    # What this customer was asked for is still worth asking about on the one that stays.
    kept = {s["text"].lower() for s in into.get("packet_suggestions", [])}
    for sug in payer.get("packet_suggestions", []):
        if sug.get("status") == "open" and sug["text"].lower() not in kept:
            into.setdefault("packet_suggestions", []).append(sug)
    state["payers"].remove(payer)
    names = ", ".join(j["name"] for j in moved) or "no jobs"
    audit(state, "", "payer_merge", f"{actor} merged {payer['name']} into {into['name']} ({names}); kept {into['name']}'s settings.", actor)
    return into


def delete_payer(state: dict[str, Any], payer_id: str, actor: str = "owner") -> None:
    """Remove a customer no job uses, open or finished (Oct 23 note 47). One with jobs is kept:
    its jobs need it, so it can only be merged into another customer."""
    payer = _payer(state, payer_id)
    used = [j for j in state["jobs"] if j.get("payer_id") == payer["id"]]
    if used:
        jobs = "1 job uses" if len(used) == 1 else f"{len(used)} jobs use"
        finished = ", counting finished jobs" if any(j.get("closed") for j in used) else ""
        # Point at Merge only when there is another customer to merge into (the box is hidden otherwise).
        advice = ("To remove it, merge it into another client." if len(state["payers"]) > 1
                  else "Its jobs need it.")
        raise GateError(f"{payer['name']} can't be deleted: {jobs} this client{finished}. {advice}")
    state["payers"].remove(payer)
    audit(state, "", "payer_delete", f"{actor} deleted the client {payer['name']}.", actor)


def payapp_due(payer: dict[str, Any], today: date | None = None) -> tuple[date, date] | None:
    """(the next cutoff, the one before it) for a payer with "pay apps due by day N", or None.
    A day past the month's end means its last day (31 in April is April 30)."""
    day = _int_or(payer.get("payapp_due_day"), 0)
    if not 1 <= day <= 31:
        return None
    today = today or datetime.now(timezone.utc).date()

    def cutoff(year: int, month: int) -> date:
        last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
        return date(year, month, min(day, last))

    this = cutoff(today.year, today.month)
    if today <= this:
        before_m = today.month - 1 or 12
        return this, cutoff(today.year - (today.month == 1), before_m)
    nxt_m = today.month % 12 + 1
    return cutoff(today.year + (today.month == 12), nxt_m), this


# legal_name: the name on the contract and the lien waivers, when it isn't the company name (M15).
COMPANY_FIELDS = {"name": 120, "address": 200, "phone": 40, "license": 60, "legal_name": 160}
# The company address in four boxes (S3), with the longest each may be; "address" is composed.
ADDRESS_PARTS = {"street": 120, "city": 60, "state": 3, "zip": 10}
ADDRESS_WORDS = {"street": "street address", "city": "city", "state": "state", "zip": "ZIP code"}
# The G1 rule each company field is checked by (domain/validate.py) when it changes.
COMPANY_RULES = {"name": "name", "phone": "phone", "license": "licence", "street": "street", "city": "city", "state": "state", "zip": "zip"}
BILLING_STYLES = {"pay_apps", "draws"}
# On/off company settings on Settings, Company, with their default: owners get the daily "due
# today" email (R4-10). Who may send is a permission now (S6, domain/profiles.py), not a switch.
COMPANY_SWITCHES = {"daily_email": True}
SWITCH_WORDS = {"daily_email": "daily email of what's due"}


def payer_timing(state: dict[str, Any]) -> dict[str, tuple[float, int]]:
    """{payer id: (average days from sent to paid, pay apps paid)}, from the company's own history:
    what each payer actually does, for the playbook to compare with what it says (Results uses
    the same rule)."""
    out: dict[str, list[int]] = {}
    for job in state.get("jobs", []):
        for inv in job.get("invoices", []):
            if inv.get("status") != "paid" or not inv.get("sent_at"):
                continue
            paid = [p.get("matched_at", "") for p in job.get("payments", []) if p.get("invoice_id") == inv["id"]]
            try:
                days = (datetime.fromisoformat(max(paid)).date() - datetime.fromisoformat(inv["sent_at"]).date()).days if paid else None
            except ValueError:
                days = None
            if days is not None and days >= 0:
                out.setdefault(job.get("payer_id", ""), []).append(days)
    return {k: (round(sum(v) / len(v), 1), len(v)) for k, v in out.items()}


def update_company(state: dict[str, Any], updates: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    """The company's own details: its name, and the address, phone and licence number that go on
    every pay app. Blank clears a field; the name can't be blank. Also the on/off settings in
    COMPANY_SWITCHES.

    The address comes as its four parts (S3): street, city, state and zip, checked by
    domain/validate.py and kept as parts with the composed one-line "address" ("12 Peach St,
    Atlanta, GA 30303") that the pay app PDF, the client's page and the menu show. A bare
    "address" (pages from before S3) is split the same way. Each field is checked only when it
    changes, so a value saved before the rules (G1) isn't refused until someone edits it."""
    company = state.setdefault("company", {})
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    if "billing_style" in updates:
        # How the company bills: pay apps to GCs or owners, or draws from homeowners. It sets the
        # word the app uses (static/js/terms.js, term_text; R6-01) and the free trial's length
        # (trial.py: 90 days, or 45 for draws).
        if updates["billing_style"] not in BILLING_STYLES:
            errors["billing_style"] = "Pick how you bill: pay apps or draws."
        else:
            clean["billing_style"] = updates["billing_style"]
    labels = {"name": "the company name", "address": "the address", "phone": "the phone number", "license": "the licence number",
              "legal_name": "the legal name", **{k: f"the {w}" for k, w in ADDRESS_WORDS.items()}}
    texts: dict[str, str] = {}
    for k in (*COMPANY_FIELDS, *ADDRESS_PARTS):
        if k not in updates:
            continue
        v = updates[k]
        if not isinstance(v, str):
            errors[k] = f"Enter {labels[k]} as text."
            continue
        texts[k] = " ".join(v.split())
    if any(k in texts for k in ADDRESS_PARTS):
        parts = {k: texts.get(k, company.get(k, "")) for k in ADDRESS_PARTS}
        if parts["state"]:
            parts["state"] = normalize_state(parts["state"]) or parts["state"]
        if any(parts.values()):
            changed_parts = {k: v for k, v in parts.items() if v != company.get(k, "")}
            # The four boxes are one address: once any part is typed, every part is needed.
            missing = {k: v for k, v in check_address(parts).items() if k in changed_parts or not parts[k]}
            errors.update(missing)
        texts.update(parts)
        texts["address"] = compose_address(parts)
    elif "address" in texts:
        texts.update(parse_legacy_address(texts["address"]) if texts["address"] else {k: "" for k in ADDRESS_PARTS})
    for k, v in texts.items():
        if k in errors:
            continue
        limit = COMPANY_FIELDS.get(k) or ADDRESS_PARTS[k]
        if len(v) > limit:
            errors[k] = f"Shorten {labels[k]} to {limit} characters or fewer."
        elif k == "name" and not v:
            errors[k] = "Enter the company name."
        elif k in COMPANY_RULES and v and v != company.get(k, ""):
            rule = COMPANY_RULES[k]
            if rule == "phone":
                v = normalize_phone(v) or v
            errors.update(check_field(rule, v, k))
            clean[k] = v
        else:
            clean[k] = v
    for k in COMPANY_SWITCHES:
        if k in updates:
            if not isinstance(updates[k], bool):
                errors[k] = "Choose on or off."
            else:
                clean[k] = updates[k]
    if errors:
        raise GateError("Company details not saved.", fields=errors)
    changed = [k for k, v in clean.items()
               if (company.get(k, COMPANY_SWITCHES[k]) if k in COMPANY_SWITCHES else company.get(k) or ("pay_apps" if k == "billing_style" else "")) != v]
    for k in changed:
        if k in COMPANY_SWITCHES:
            company[k] = clean[k]
        elif clean[k]:
            company[k] = clean[k]
        else:
            company.pop(k, None)
    if changed:
        names = {"billing_style": f"billing style ({'draws' if clean.get('billing_style') == 'draws' else 'pay apps'})",
                 **{k: f"setting \"{SWITCH_WORDS[k]}\" ({'on' if clean.get(k) else 'off'})" for k in COMPANY_SWITCHES},
                 **ADDRESS_WORDS}
        shown = list(dict.fromkeys("address" if k in ADDRESS_PARTS else k for k in changed))
        audit(state, "", "company_update", f"{actor} updated the company's {', '.join(names.get(k, k) for k in shown)}.", actor)
    return company


# ---------- payer playbook learning (P3) ----------
MAX_SUGGESTIONS = 10


def suggest_packet_item(state: dict[str, Any], payer_id: str, text: str, source: str, job_name: str = "") -> dict | None:
    """Remember what a payer asked for, as a proposed addition to their packet. A person adds
    it or dismisses it on the Payer playbook; nothing changes the packet by itself."""
    text = " ".join(str(text or "").split())[:200]
    payer = next((p for p in state.get("payers", []) if p["id"] == payer_id), None)
    if not payer or len(text) < 4:
        return None
    if text.lower() in str(payer.get("packet", "")).lower():
        return None
    open_items = [s for s in payer.setdefault("packet_suggestions", []) if s.get("status") == "open"]
    if any(s["text"].lower() == text.lower() for s in open_items) or len(open_items) >= MAX_SUGGESTIONS:
        return None
    suggestion = {"id": "sug-" + secrets.token_hex(4), "text": text, "source": source, "job": job_name, "at": utcnow(), "status": "open"}
    payer["packet_suggestions"].append(suggestion)
    return suggestion


def answer_packet_suggestion(state: dict[str, Any], payer_id: str, suggestion_id: str, accept: bool, actor: str = "owner") -> dict[str, Any]:
    payer = _payer(state, payer_id)
    sug = next((s for s in payer.get("packet_suggestions", []) if s["id"] == suggestion_id and s.get("status") == "open"), None)
    if not sug:
        raise GateError("That suggestion was already answered.")
    sug["status"] = "added" if accept else "dismissed"
    sug["answered_by"], sug["answered_at"] = actor, utcnow()
    if accept:
        packet = str(payer.get("packet", "")).strip().rstrip(".")
        payer["packet"] = f"{packet}, {sug['text']}" if packet else sug["text"]
        audit(state, "", "playbook_learned", f"{actor} added \"{sug['text']}\" to {payer['name']}'s supporting documents list ({sug['source']}).", actor)
    return payer


MAX_SCHEDULE_DAYS = 365

# Why the scope changed (Stephen's Oct 29 review, M7). Older change orders have none.
CO_REASONS = {
    "owner_request": "Owner request",
    "unforeseen_condition": "Unforeseen condition",
    "design_issue": "Design issue",
    "value_engineering": "Value engineering",
    "other": "Other",
}
# How the change is priced (M8): one agreed price, or time and materials.
CO_PRICING = {"lump_sum": "Lump sum", "tm": "Time and materials"}
MAX_CO_ITEMS = 50
MAX_CO_TEXT = 200


def co_event(co: dict[str, Any], what: str, actor: str, at: str | None = None, note: str = "") -> str:
    """One row of a change order's own history (M10): what happened, who and when. Returns the time."""
    at = at or utcnow()
    row = {"at": at, "what": what, "by": actor}
    if note:
        row["note"] = note
    co.setdefault("history", []).append(row)
    return at


def _co_items(items: Any, errors: dict[str, str]) -> list[dict[str, Any]]:
    """The itemized cost breakdown (M8): [{label, amount_cents}], at most MAX_CO_ITEMS. Blank rows
    (no label and no amount) are dropped; the rest need both."""
    if items in (None, ""):
        return []
    if not isinstance(items, list):
        errors["items"] = "Enter each cost line with a label and an amount."
        return []
    out = []
    for n, item in enumerate(items):
        item = item if isinstance(item, dict) else {}
        label = " ".join(str(item.get("label") or "").split())[:120]
        amount = item.get("amount_cents")
        if not label and amount in (None, "", 0):
            continue
        if not label:
            errors[f"item_label_{n}"] = "Enter what this cost line is, like Labor."
        if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
            errors[f"item_amount_{n}"] = "Enter this line's amount, like 450."
        elif abs(amount) > MAX_AMOUNT_CENTS:
            errors[f"item_amount_{n}"] = f"Enter an amount up to {money(MAX_AMOUNT_CENTS)}."
        out.append({"label": label, "amount_cents": amount if isinstance(amount, int) and not isinstance(amount, bool) else 0})
    if len(out) > MAX_CO_ITEMS:
        errors["items"] = f"A change order can have up to {MAX_CO_ITEMS} cost lines."
    return out


def _past_day(value: Any, field: str, what: str) -> str:
    """A date that has already happened (one day of slack for time zones), as YYYY-MM-DD; blank is ""."""
    message = f"Enter {what}, like 2026-10-27."
    day = _day(value, field, message)
    if day is None:
        return ""
    if day > datetime.now(timezone.utc).date() + timedelta(days=1):
        _refuse(message, {field: f"Enter {what} that has already happened."})
    return day.isoformat()


def add_change_order(
    state: dict[str, Any], job_id: str, title: str, amount_cents: int, actor: str = "owner", description: str = "",
    schedule_days: int = 0, reason: str | None = None, pricing: str = "", items: Any = None, contract_ref: str = "",
    support_record_ids: Any = None, work_started_on: Any = "",
) -> dict[str, Any]:
    """A change order, pending until signed. A negative amount is a credit (R3-06): signed, it
    lowers the contract. `schedule_days` is how many days it adds to (or takes off) the schedule.

    Stephen's Oct 29 review (M7, M8): `reason` is one of CO_REASONS, and the description is
    required when it is "other". The server always passes one; None is for callers from before
    reasons (tests, scripts), which skip the check. `pricing` is lump sum (the default) or time and
    materials; `items` is the cost breakdown, whose sum is the amount (a typed amount that differs
    is refused; a blank one is set to the sum). `contract_ref` names the contract section or
    original scope it changes, and `support_record_ids` the job records that back it up (quotes,
    supplier invoices, T&M sheets). `work_started_on` (M9) is the day work began, if it already has."""
    job = _open_job(state, job_id)
    title = title.strip() if isinstance(title, str) else ""
    description = description.strip() if isinstance(description, str) else ""
    contract_ref = " ".join(str(contract_ref or "").split())[:MAX_CO_TEXT]
    errors: dict[str, str] = {}
    if not title:
        errors["title"] = "Enter a title for the change order."
    if reason is not None:
        reason = str(reason or "").strip()
        if reason not in CO_REASONS:
            errors["reason"] = "Pick why the scope changed."
        elif reason == "other" and not description:
            errors["description"] = "Enter a description: what changed and why."
    pricing = str(pricing or "lump_sum").strip()
    if pricing not in CO_PRICING:
        errors["pricing"] = "Pick lump sum or time and materials."
    lines = _co_items(items, errors)
    if lines and not any(k.startswith("item_") or k == "items" for k in errors):
        total = sum(l["amount_cents"] for l in lines)
        if isinstance(amount_cents, int) and not isinstance(amount_cents, bool) and amount_cents not in (0, total):
            errors["amount_cents"] = f"The cost lines add up to {money(total)}. Make the amount match, or leave it blank."
        else:
            amount_cents = total
    if "amount_cents" in errors:
        pass
    elif not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents == 0:
        errors["amount_cents"] = "Enter the change order amount, like 1,250, or -1,250 for a credit."
    elif abs(amount_cents) > MAX_AMOUNT_CENTS:
        errors["amount_cents"] = f"Enter a change order amount up to {money(MAX_AMOUNT_CENTS)}."
    elif amount_cents < 0 and job["contract_cents"] + sum(c["amount_cents"] for c in job["change_orders"] if c["status"] == "approved") + amount_cents < 0:
        errors["amount_cents"] = "Enter a credit no larger than the contract total."
    if not isinstance(schedule_days, int) or isinstance(schedule_days, bool) or abs(schedule_days) > MAX_SCHEDULE_DAYS:
        errors["schedule_days"] = f"Enter the schedule change in whole days, up to {MAX_SCHEDULE_DAYS}, or leave it blank."
    records = {r["id"] for r in job.get("records", [])}
    support = support_record_ids if isinstance(support_record_ids, list) else []
    support = list(dict.fromkeys(str(r) for r in support if r))
    if any(r not in records for r in support):
        errors["support_record_ids"] = "Pick backup from this job's record."
    try:
        started = _past_day(work_started_on, "work_started_on", "the day work started")
    except GateError as exc:
        errors.update(exc.fields)
        started = ""
    _refuse("Change order not saved.", errors)
    number = max((co["number"] for co in job["change_orders"]), default=0) + 1
    co = {
        "id": f"co-{job['code'].lower()}-{number}",
        "number": number,
        "title": title,
        "description": description,
        "amount_cents": amount_cents,
        "status": "pending",
        "included_in_billing": False,
        "pricing": pricing,
    }
    if reason:
        co["reason"] = reason
    if lines:
        co["items"] = lines
    if contract_ref:
        co["contract_ref"] = contract_ref
    if support:
        co["support_record_ids"] = support
    if schedule_days:
        co["schedule_days"] = schedule_days
    co["created_at"] = co_event(co, "created", actor)
    co["created_by"] = actor
    job["change_orders"].append(co)
    what = f"a credit of {money(-amount_cents)}" if amount_cents < 0 else money(amount_cents)
    days = f", {schedule_days:+d} day{'' if abs(schedule_days) == 1 else 's'} on the schedule" if schedule_days else ""
    audit(state, job_id, "add_co", f"{actor} added CO {number} — {title} for {what}{days}.", actor)
    if started:
        _set_work_started(state, job, co, started, actor)
    _refresh_draft_gate(job)   # a final draft isn't the final one with this still to come
    return job


def approve_change_order(state: dict[str, Any], job_id: str, co_id: str, actor: str = "owner") -> dict[str, Any]:
    job = _open_job(state, job_id)
    co = next((c for c in job["change_orders"] if c["id"] == co_id), None)
    if not co:
        raise GateError("Change order not found.")
    if co["status"] == "approved":
        raise GateError(f"CO {co['number']} is already approved.")
    co["status"] = "approved"
    co["included_in_billing"] = False  # no longer pending
    co["approved_at"] = co_event(co, "signed", actor)
    co["approved_by"] = actor
    if sov.has_sov(job):
        _add_co_line(job, co)
    if job.get("work_status") == "complete":
        # Work already marked done: completed work is the whole contract plus every
        # approved CO, so the new CO becomes billable. Recompute rather than add,
        # because older records can carry a stale completed value.
        contract_total = job["contract_cents"] + sum(c["amount_cents"] for c in job["change_orders"] if c["status"] == "approved")
        # A credit lowers the whole contract, so the work done comes down with it.
        job["completed_value_cents"] = contract_total if co["amount_cents"] < 0 else max(job.get("completed_value_cents", 0), contract_total)
    # An open draft that billed it unsigned no longer says so on its line (and so on the PDF).
    for inv in job["invoices"]:
        if inv["status"] in DRAFT_STATUSES:
            for line in inv.get("lines", []):
                if line.get("co_id") == co_id:
                    line["label"] = f"CO {co['number']} — {co['title']}"
    audit(state, job_id, "approve_co", f"{actor} approved CO {co['number']} — {co['title']}.", actor)
    # A final draft not sent yet bills what is left, so it follows the change. One with the client
    # stays as they have it; if it goes out over the new total, close_blocker_items says so.
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if job.get("work_status") == "complete" and draft and not _with_client(draft) and not any(l.get("co_id") == co_id for l in draft.get("lines", [])):
        _redraft(state, job, draft, f"to follow CO {co['number']}")
    _refresh_draft_gate(job, state)
    return job


# ---------- signed change orders (P2-3) ----------
# Draft consent wording, written to describe what the code does; counsel should check it
# against ESIGN and the state's rules before real customers rely on it. Stephen's Oct 29 review
# (M9): the signer agrees to the scope, price and schedule by name.
CO_CONSENT = (
    "I agree to the scope, price and schedule in this change order. I agree to sign it electronically, "
    "and my typed name below is my signature."
)
# What co_snapshot holds. Version 1 (no "version" key) had the title, description, amount and
# consent only; a stored signature keeps its own snapshot and hash, so older ones still verify.
CO_SNAPSHOT_VERSION = 2


def co_snapshot(state: dict[str, Any], job: dict[str, Any], co: dict[str, Any]) -> dict[str, Any]:
    """Exactly what the signer saw; its SHA-256 is kept with the signature. payer_page.render_co
    draws the signing page from it."""
    return {
        "version": CO_SNAPSHOT_VERSION,
        "contractor": state.get("company", {}).get("name", ""),
        "job": job["name"],
        "address": job.get("address", ""),
        "change_order": co["number"],
        "title": co["title"],
        "description": co.get("description", ""),
        "reason": CO_REASONS.get(co.get("reason", ""), ""),
        "contract_ref": co.get("contract_ref", ""),
        "pricing": CO_PRICING.get(co.get("pricing", "lump_sum"), CO_PRICING["lump_sum"]),
        "items": [{"label": i["label"], "amount_cents": i["amount_cents"]} for i in co.get("items", [])],
        "amount_cents": co["amount_cents"],
        "schedule_days": int(co.get("schedule_days") or 0),
        "consent": CO_CONSENT,
    }


def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _pending_co(state: dict[str, Any], job_id: str, co_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    job = _open_job(state, job_id)
    co = next((c for c in job["change_orders"] if c["id"] == co_id), None)
    if not co:
        raise GateError("Change order not found.")
    if co["status"] != "pending":
        raise GateError(f"CO {co['number']} is {co['status']}; only a pending change order can be signed.")
    return job, co


# What the contractor reads when the server has no site address for signing links (Oct 23 note 17).
NO_SIGNING_LINKS = ("Signing links aren't available on this server yet. Contact Duracash support. "
                    "Meanwhile, get it signed on paper and tap Signed on paper….")


def request_co_signature(
    state: dict[str, Any], job_id: str, co_id: str, actor: str = "owner", link_base: str | None = None, company_id: str = ""
) -> str:
    """Make the change order's signing link and, with a site address, email it to the payer. Returns the link.
    Without the site's address (DURACASH_URL, which the server logs) the contractor reads plain words
    and is pointed to signing on paper."""
    job, co = _pending_co(state, job_id, co_id)
    if not link_base or not company_id:
        raise GateError(NO_SIGNING_LINKS)
    co["sign_token"] = co.get("sign_token") or secrets.token_urlsafe(24)
    co["signature_requested_at"] = co_event(co, "signature_requested", actor)
    link = f"{link_base.rstrip('/')}/s/{company_id}.{co['sign_token']}"
    payer = _payer(state, job["payer_id"])
    if payer.get("ap_email"):
        days = int(co.get("schedule_days") or 0)
        body = (
            f"{payer.get('approver') or payer['name']},\n\nPlease review and sign change order {co['number']} for {job['name']}: "
            f"{co['title']}, {money(co['amount_cents'])}"
            + (f", {days:+d} day{'' if abs(days) == 1 else 's'} on the schedule" if days else "") + "."
            + (f"\n\n{co['description']}" if co.get("description") else "")
            + f"\n\nSign it here (type your name; nothing to print):\n{link}\n\nRequested by {actor}."
        )
        _record_email(state, job_id, "co_signature", payer["ap_email"], f"Please sign change order {co['number']} — {job['name']}",
                      body, actor, cc=_copy_list(payer))
    audit(state, job_id, "request_co_signature", f"{actor} asked {payer.get('ap_email') or payer['name']} to sign CO {co['number']}.", actor)
    return link


def sign_change_order(
    state: dict[str, Any], job: dict[str, Any], co: dict[str, Any], name: Any, agreed: bool, ip: str, user_agent: str
) -> dict[str, Any]:
    """The payer signs on the signing page: typed name, consent, time, IP and what they saw."""
    name = " ".join(str(name or "").split())[:120]
    if co.get("status") != "pending":
        raise GateError(f"CO {co['number']} is already {co['status']}.")
    if len(name) < 2:
        raise GateError("Type your full name to sign.")
    if not agreed:
        raise GateError("Tick the box to agree to the change order and to signing electronically.")
    snap = co_snapshot(state, job, co)
    co["signature"] = {
        "name": name, "at": utcnow(), "ip": str(ip)[:64], "user_agent": str(user_agent)[:200],
        "consent": CO_CONSENT, "snapshot": snap, "snapshot_sha256": snapshot_sha256(snap),
    }
    approve_change_order(state, job["id"], co["id"], actor=f"{name} (signed online)")
    _note_signed_after_start(state, job, co)
    return co


def mark_co_signed(
    state: dict[str, Any], job_id: str, co_id: str, record_id: str, actor: str = "owner",
    signer_name: Any = None, signed_on: Any = None,
) -> dict[str, Any]:
    """Approve a change order signed on paper: the job record must hold the signed copy. The
    server also asks who signed it and the date they signed (M9), no later than today; None is
    for callers from before that (tests, scripts)."""
    job, co = _pending_co(state, job_id, co_id)
    errors: dict[str, str] = {}
    record = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not record:
        raise GateError("Pick the record that holds the signed change order, or request an online signature.")
    signer = None if signer_name is None else " ".join(str(signer_name or "").split())[:120]
    if signer is not None and len(signer) < 2:
        errors["signer_name"] = "Enter the name of the person who signed it."
    day = ""
    if signed_on is not None:
        try:
            day = _past_day(signed_on, "signed_on", "the date it was signed")
            if not day:
                errors["signed_on"] = "Enter the date it was signed, like 2026-10-27."
        except GateError as exc:
            errors.update(exc.fields)
    _refuse("Not saved as signed.", errors)
    co["signed_evidence"] = {"record_id": record_id, "label": record.get("label", ""), "by": actor, "at": utcnow()}
    if signer:
        co["signed_evidence"]["signer_name"] = signer
    if day:
        co["signed_evidence"]["signed_on"] = day
    approve_change_order(state, job_id, co_id, actor)
    if signer:
        co["history"][-1]["note"] = f"signed on paper by {signer}" + (f" on {day}" if day else "")
    _note_signed_after_start(state, job, co)
    return job


def file_signed_co_copy(
    state: dict[str, Any], job_id: str, co_id: str, record_id: str, actor: str = "owner",
    signer_name: Any = None, signed_on: Any = None,
) -> str:
    """A signed copy taken by someone who can't approve change orders (Round 1, item 2): it goes
    on the change order as its backup, with who signed it and when if given, and the change order
    stays pending until someone who can marks it signed. Returns what the page says."""
    job, co = _pending_co(state, job_id, co_id)
    record = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not record:
        raise GateError("That record is not on this job.")
    if record_id not in (co.get("support_record_ids") or []):
        co.setdefault("support_record_ids", []).append(record_id)
    signer = " ".join(str(signer_name or "").split())[:120]
    try:
        day = _past_day(signed_on, "signed_on", "the date it was signed") if signed_on else ""
    except GateError:
        day = ""
    co["signed_copy"] = {"record_id": record_id, "label": record.get("label", ""), "by": actor, "at": utcnow(),
                         **({"signer_name": signer} if len(signer) >= 2 else {}), **({"signed_on": day} if day else {})}
    co_event(co, "signed_copy_filed", actor, note=record.get("label", ""))
    audit(state, job_id, "co_signed_copy", f"{actor} filed {record.get('label', 'a file')} as the signed copy of CO {co['number']}. "
          "It stays pending until someone who can approve change orders marks it signed.", actor)
    return f"Filed with CO {co['number']}. Someone who can approve change orders has to mark it signed."


def co_signed_on(co: dict[str, Any]) -> str:
    """The day the client signed (YYYY-MM-DD), or "" while unsigned."""
    if co.get("signature"):
        return str(co["signature"].get("at", ""))[:10]
    ev = co.get("signed_evidence") or {}
    if ev:
        return str(ev.get("signed_on") or ev.get("at") or co.get("approved_at") or "")[:10]
    return str(co.get("approved_at") or "")[:10] if co.get("status") == "approved" else ""


def co_started_unsigned(co: dict[str, Any]) -> bool:
    """Work began before the client signed (M9): pending with a start date, or signed after it.
    The flag stays once signed, as a record."""
    started = co.get("work_started_on")
    if not started:
        return False
    if co.get("status") == "pending":
        return True
    signed = co_signed_on(co)
    return co.get("status") == "approved" and bool(signed) and signed > started


def _set_work_started(state: dict[str, Any], job: dict[str, Any], co: dict[str, Any], day: str, actor: str) -> None:
    co["work_started_on"] = day
    co["work_started_by"] = actor
    co_event(co, "work_started", actor, note=day)
    flag = " before it was signed" if co_started_unsigned(co) else ""
    audit(state, job["id"], "co_work_started", f"{actor} recorded that work on CO {co['number']} started {day}{flag}.", actor)


def _note_signed_after_start(state: dict[str, Any], job: dict[str, Any], co: dict[str, Any]) -> None:
    if co_started_unsigned(co):
        audit(state, job["id"], "co_signed_after_start",
              f"CO {co['number']} was signed {co_signed_on(co)}, after work started {co['work_started_on']}.", "duracash")


def set_co_work_started(state: dict[str, Any], job_id: str, co_id: str, work_started_on: Any, actor: str = "owner") -> dict[str, Any]:
    """The day work on a change order started (M9), recorded before or after it is signed."""
    job = _open_job(state, job_id)
    co = next((c for c in job["change_orders"] if c["id"] == co_id), None)
    if not co:
        raise GateError("Change order not found.")
    if co["status"] == "withdrawn":
        raise GateError(f"CO {co['number']} is withdrawn.")
    day = _past_day(work_started_on, "work_started_on", "the day work started")
    if not day:
        _refuse("", {"work_started_on": "Enter the day work started, like 2026-10-27."})
    _set_work_started(state, job, co, day, actor)
    return job


def mark_co_logged(state: dict[str, Any], job_id: str, co_id: str, logged: bool, actor: str = "owner") -> dict[str, Any]:
    """"Client logged it on their end" (M8): after signing, the client has entered the change
    order in their own system. Ticking it again off takes it back; both are in the history."""
    job = _open_job(state, job_id)
    co = next((c for c in job["change_orders"] if c["id"] == co_id), None)
    if not co:
        raise GateError("Change order not found.")
    if co["status"] != "approved":
        raise GateError(f"CO {co['number']} isn't signed yet. Mark it logged once it is signed.")
    if logged == bool(co.get("client_logged_at")):
        return job
    if logged:
        co["client_logged_at"] = co_event(co, "client_logged", actor)
        co["client_logged_by"] = actor
        audit(state, job_id, "co_client_logged", f"{actor} marked CO {co['number']} as logged by the client on their end.", actor)
    else:
        co.pop("client_logged_at", None)
        co.pop("client_logged_by", None)
        co_event(co, "client_logged_undone", actor)
        audit(state, job_id, "co_client_logged", f"{actor} took off that the client logged CO {co['number']}.", actor)
    return job


def withdraw_change_order(state: dict[str, Any], job_id: str, co_id: str, actor: str = "owner") -> dict[str, Any]:
    """A pending CO the customer will not sign. Its dollars leave any open draft's blockers."""
    job = _open_job(state, job_id)
    co = next((c for c in job["change_orders"] if c["id"] == co_id), None)
    if not co:
        raise GateError("Change order not found.")
    if co["status"] != "pending":
        raise GateError(f"CO {co['number']} is {co['status']}; only a pending CO can be withdrawn.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if co.get("included_in_billing") and open_draft:
        raise GateError(f"CO {co['number']} is on {open_draft['number']}. Delete that draft first, then withdraw the CO.")
    co["status"] = "withdrawn"
    co["included_in_billing"] = False
    co["withdrawn_at"] = co_event(co, "withdrawn", actor)
    co["withdrawn_by"] = actor
    audit(state, job_id, "withdraw_co", f"{actor} withdrew CO {co['number']} — {co['title']}.", actor)
    _refresh_draft_gate(job, state)   # with it gone, a draft can be the final one
    return job


def void_draft(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    """Throw away the open draft pay app. On a job billed in dollars that is still running, the
    total done so far goes back to what is billed, so progress can be entered again at any
    amount; on a schedule of values the quantities stay and go back to unbilled. Change orders
    the draft carried come off it."""
    job = _open_job(state, job_id)
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if not draft:
        raise GateError("There is no open draft to void.")
    draft["status"] = "void"
    draft["voided_at"] = utcnow()
    audit(state, job_id, "void_draft", f"{actor} voided draft {draft['number']} ({money(draft['amount_cents'])}).", actor)
    if job.get("work_status") != "complete" and not sov.has_sov(job):
        job["completed_value_cents"] = _billed_gross(job)
    carried = {line.get("co_id") for line in draft.get("lines", []) if line.get("co_id")}
    for co in job["change_orders"]:
        if co["id"] in carried and co.get("included_in_billing"):
            co["included_in_billing"] = False
            co_event(co, "off_draft", actor)
            audit(state, job_id, "change_order_off_draft", f"CO {co['number']} taken off the voided draft.", actor)
    _back_to_suggested(job)
    _pencil_status_goes(job)
    return job


def _back_to_suggested(job: dict[str, Any]) -> None:
    """A pay app was deleted or pulled back: a quantity typed for it went with it, so each line goes
    back to what the card suggests and a blank box bills (a line with no field total has only the
    quantity entered on it, and keeps that). A finished job's lines stay whole."""
    if job.get("work_status") == "complete" or not sov.has_sov(job):
        return
    billed_qty = sov.billed(job)["qty_by_line"]
    for line in sov.lines(job):
        if "field_milli" in line:
            line["done_milli"] = sov.suggested_done(line, billed_qty.get(line["id"], 0))
    job["completed_value_cents"] = sov.totals(job)["to_date_cents"]


def _pencil_status_goes(job: dict[str, Any]) -> None:
    """The draft the client had for review is gone, so the job no longer says it is with them."""
    if str(job.get("external_status") or "").startswith("Pencil copy with"):
        job["external_status"] = None


def _reason(text: Any) -> str:
    return " ".join(str(text or "").split())[:200]


def void_sent_pay_app(state: dict[str, Any], job_id: str, invoice_id: str, reason: str, actor: str = "owner") -> dict[str, Any]:
    """Pull back a pay app that went out with a mistake, before anything was paid on it (R4-03).
    It is kept as void with the reason, its number is not used again, and the customer gets an
    email saying it is withdrawn (their page for it already says so). The work on it is unbilled
    again: on a dollar job still running, the total done so far goes back to what is billed, so
    progress can be entered again at a lower amount; on a schedule the quantities stay."""
    job = _open_job(state, job_id)
    invoice = next((inv for inv in job["invoices"] if inv["id"] == invoice_id), None)
    if not invoice or invoice["status"] not in {"sent", "approved", "partial", "paid"}:
        raise GateError("Pick a pay app that has gone out. A draft is deleted instead.")
    if invoice["status"] in {"partial", "paid"} or any(p["invoice_id"] == invoice["id"] for p in job["payments"]):
        raise GateError(f"A payment is recorded on {invoice['number']}, so it can't be pulled back. Correct the next pay app instead.")
    if _holds_paid_retainage(job, invoice):
        raise GateError(f"{money(job_money(job)['retainage_released_cents'])} of retainage is recorded as paid, and {invoice['number']} holds it, so it can't be pulled back. Correct the next pay app instead.")
    later = [inv for inv in job["invoices"][job["invoices"].index(invoice) + 1:] if inv["status"] != "void"]
    if later:
        nxt = later[0]
        if nxt["status"] in DRAFT_STATUSES:
            raise GateError(f"Delete the draft {nxt['number']} first, then pull back {invoice['number']}.")
        raise GateError(f"{nxt['number']} went out after it. Pull that one back first.")
    reason = _reason(reason)
    if not reason:
        _refuse("", {"reason": "Enter why you are pulling it back, like a wrong quantity on line 3."})
    payer = _payer(state, job["payer_id"])
    to = _send_to(payer)
    invoice["status"] = "void"
    invoice["voided_at"] = utcnow()
    invoice["voided_reason"] = reason
    invoice["voided_by"] = actor
    job["external_status"] = ""
    if job.get("work_status") != "complete" and not sov.has_sov(job):
        job["completed_value_cents"] = _billed_gross(job)
    carried = {line.get("co_id") for line in invoice.get("lines", []) if line.get("co_id")}
    for co in job["change_orders"]:
        if co["id"] in carried and co.get("included_in_billing"):
            co["included_in_billing"] = False
    _back_to_suggested(job)
    subject = f"Pay application {invoice['number']} withdrawn — {job['name']}"
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"We have withdrawn pay application {invoice['number']} for {job['name']} ({money(invoice['amount_cents'])}), "
        f"sent {(invoice.get('sent_at') or '')[:10]}. Nothing is due on it; please set it aside.\n\n"
        f"Why: {reason}\n\nIf a corrected pay app is due, it follows separately."
        f"\n\n{sign_off(state, actor)}"
    )
    _record_email(state, job_id, "pay_app_withdrawn", to, subject, body, actor, cc=_copy_list(payer))
    audit(state, job_id, "void_sent", f"{actor} pulled back {invoice['number']} ({money(invoice['amount_cents'])}): {reason}.", actor)
    _settlements_follow(state, job, f"{invoice['number']} was pulled back")
    return job


def can_undo_done(job: dict[str, Any]) -> bool:
    """Done can be undone while nothing billed since has gone out: the final pay app is still a
    draft (or there is none), so taking it back changes nothing the customer has (R4-02)."""
    if job.get("closed") or job.get("work_status") != "complete":
        return False
    if "done_at_invoice" in job:
        after = job["invoices"][job["done_at_invoice"]:]
    else:  # marked done before R4-02: by date
        after = [inv for inv in job["invoices"] if (inv.get("created_at") or "") > (job.get("completed_at") or "")]
    return not any(inv["status"] not in DRAFT_STATUSES and inv["status"] != "void" for inv in after)


def undo_mark_done(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    """Final Pay App tapped by mistake: delete the final draft and put the job back in progress, with
    the total done so far at what is billed (on a schedule, each line back to its billed quantity)."""
    job = _open_job(state, job_id)
    if job.get("work_status") != "complete":
        raise GateError(f"{job['name']} is not marked done.")
    if not can_undo_done(job):
        raise GateError("The final pay app has gone out. Pull it back first, then undo done.")
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if draft:
        draft["status"] = "void"
        draft["voided_at"] = utcnow()
        carried = {line.get("co_id") for line in draft.get("lines", []) if line.get("co_id")}
        for co in job["change_orders"]:
            if co["id"] in carried and co.get("included_in_billing"):
                co["included_in_billing"] = False
    _pencil_status_goes(job)
    job["work_status"] = "in_progress"
    job.pop("completed_at", None)
    job.pop("done_at_invoice", None)
    if sov.has_sov(job):
        billed_qty = sov.billed(job)["qty_by_line"]
        for line in sov.lines(job):
            if line["scheduled_cents"] >= 0:  # a credit's line stays taken off
                # A change order billed while unsigned is billed whole, before its line existed.
                billed_whole = line.get("co_id") and _co_billed_unsigned(job, line["co_id"])
                line["done_milli"] = line["qty_milli"] if billed_whole else billed_qty.get(line["id"], 0)
    job["completed_value_cents"] = _billed_gross(job)
    audit(state, job_id, "undo_done", f"{actor} undid done on {job['name']}"
          + (f"; deleted draft {draft['number']} ({money(draft['amount_cents'])})" if draft else "")
          + f". Total done so far back to {money(job['completed_value_cents'])}.", actor)
    return job


def include_change_order(state: dict[str, Any], job_id: str, co_id: str, actor: str = "owner") -> dict[str, Any]:
    """Put a pending CO's dollars on the next pay app. Send stays blocked until it is signed."""
    job = _open_job(state, job_id)
    co = next((c for c in job["change_orders"] if c["id"] == co_id), None)
    if not co:
        raise GateError("Change order not found.")
    if co["status"] != "pending":
        raise GateError(f"CO {co['number']} is already approved; it is billed as contract work.")
    if co["amount_cents"] < 0:
        raise GateError(f"CO {co['number']} is a credit. It comes off the contract once it is signed.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if _with_client(open_draft):
        raise GateError(f"{open_draft['number']} is with the client for their review. Record their answer or delete the draft, "
                        "then bill the CO.")
    co["included_in_billing"] = True
    co["included_at"] = co_event(co, "included", actor)
    co["included_by"] = actor
    if open_draft:
        # The draft is only here: it is drafted again with the CO on it (B6 of the Oct 30 review).
        audit(state, job_id, "include_co", f"{actor} put unsigned CO {co['number']} on {open_draft['number']}.", actor)
        return _redraft(state, job, open_draft, f"to bill CO {co['number']}")
    audit(state, job_id, "include_co", f"{actor} put unsigned CO {co['number']} on the next pay app.", actor)
    return job


def add_evidence(state: dict[str, Any], job_id: str, label: str, kind: str = "note", actor: str = "owner") -> dict[str, Any]:
    job = _open_job(state, job_id)
    item = {"id": f"ev-{len(job.get('evidence', [])) + 1}", "label": label, "kind": kind, "added_at": utcnow(), "added_by": actor}
    job.setdefault("evidence", []).append(item)
    audit(state, job_id, "add_evidence", f"{actor} added evidence: {label} ({kind}).", actor)
    return job


def learn_rejection(state: dict[str, Any], job_id: str, reason: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
    payer = _payer(state, job["payer_id"])
    current = payer.get("rejects_when", "")
    if reason and reason not in current:
        payer["rejects_when"] = (current + "; " + reason).strip("; ")
    suggest_packet_item(state, payer["id"], reason, "a rejection", job["name"])
    audit(state, job_id, "learn_rejection", f"{actor} saved to the client playbook: {payer['name']} sends pay apps back when {reason}.", actor)
    return job


def close_job(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
    blockers = close_blockers(job)
    if blockers:
        raise GateError("This job can't be finished yet.", blockers)
    job["closed"] = True
    job["closed_at"] = utcnow()
    audit(state, job_id, "close_job", f"{actor} finished {job['name']}.", actor)
    return job


def reopen_job(state: dict[str, Any], job_id: str, reason: str, actor: str = "owner") -> dict[str, Any]:
    """Bring a finished job back, for a late payment, a fix or more work (R4-02; Add more work in
    Stephen's Oct 29 review, J9): the same job opens again, so its record stays in one place. The
    reason goes in its activity log."""
    job = _job(state, job_id)
    if not job.get("closed"):
        raise GateError(f"{job['name']} is not finished.")
    reason = _reason(reason)
    if not reason:
        _refuse("", {"reason": "Enter the new work, like the owner added a backsplash."})
    job["closed"] = False
    job.pop("closed_at", None)
    job["reopened_at"] = utcnow()
    audit(state, job_id, "reopen_job", f"{actor} opened {job['name']} again to add more work: {reason}.", actor)
    return job


# add_daily_record is in domain/daily.py (T11, T12, M1).


def add_packet_attachment(state: dict[str, Any], job_id: str, item_id: str, name: str, actor: str = "owner", file_id: str = "",
                          read_file: Any = None, looked: bool = False, answer: str = "", answerer: "Answerer | None" = None) -> dict[str, Any]:
    """Attach a stored file by its id. A name alone is refused: it used to clear the item with no
    document behind it. The file goes on the job record if it isn't there yet."""
    job = _open_job(state, job_id)
    item, _invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Supporting document not found.")
    _still_a_draft(_invoice)
    meta = next((f for f in state.get("files", []) if file_id and f["id"] == file_id), None)
    if not meta:
        _refuse("", {"file_id": f"Choose the file for {item['label']}. A name alone isn't a document."})
    record = next((r for r in job.get("records", []) if file_id in r.get("file_ids", [])), None)
    if not record:
        add_job_record(state, job_id, str(name or meta["name"])[:200], (_record_kinds_for(item) or ("", ("other",)))[1][0], "", actor)
        record = job["records"][-1]
        record["file_ids"] = [file_id]
    return attach_record_to_packet(state, job_id, item_id, record["id"], actor, read_file=read_file, looked=looked, answer=answer, answerer=answerer)


def import_sample_qbo_payment(state: dict[str, Any], job_id: str, actor: str = "duracash") -> dict[str, Any]:
    """Stub QuickBooks read: match a realistic payment if an open invoice exists."""
    job = _job(state, job_id)
    invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] in {"sent", "approved", "partial"}), None)
    if not invoice:
        raise GateError("No open invoice to match from QBO sample.")
    # Sample amounts that look like real QBO pulls
    samples = { "job-oak": 4400000, "job-turner": 11970000, "job-ponce": 0, "job-decatur": 0 }
    remaining = invoice["amount_cents"] - sum(p["amount_cents"] for p in job["payments"] if p["invoice_id"] == invoice["id"])
    amount = samples.get(job_id, remaining)
    if amount <= 0 or amount > remaining:
        amount = remaining
    reference = f"QBO-sample-{len(job['payments']) + 1}"
    return match_payment(state, job_id, amount, reference, actor, invoice_id=invoice["id"])


def _job(state: dict[str, Any], job_id: str) -> dict[str, Any]:
    job = next((j for j in state["jobs"] if j["id"] == job_id), None)
    if not job:
        raise GateError("Job not found.")
    return job


def _open_job(state: dict[str, Any], job_id: str) -> dict[str, Any]:
    """A job that can still change. Closed jobs are read-only except for notes."""
    job = _job(state, job_id)
    if job.get("closed"):
        raise GateError(f"{job['name']} is closed. Closed jobs are read-only.")
    return job


def _int_or(value: Any, default: Any) -> Any:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _payer(state: dict[str, Any], payer_id: str) -> dict[str, Any]:
    payer = next((p for p in state["payers"] if p["id"] == payer_id), None)
    if not payer:
        raise GateError("Client not found.")
    return payer


def _find_packet_item(job: dict[str, Any], item_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for invoice in job.get("invoices", []):
        for item in invoice.get("packet") or []:
            if item["id"] == item_id:
                return item, invoice
    for item in (job.get("packet") or []) + (job.get("closeout_packet") or []):
        if item["id"] == item_id:
            return item, None
    return None, None


def parse_packet_text(text: str) -> list[str]:
    """Turn a playbook line like 'Invoice, photos and conditional waiver.' into item labels. A
    catalog label with "and" in it ("Daily tickets and time sheets") stays one label."""
    if not isinstance(text, str):
        return []
    for entry in PAPERWORK_ITEMS:
        if " and " in entry["label"]:
            text = re.sub(re.escape(entry["label"]), entry["label"].replace(" and ", " \x00 "), text, flags=re.I)
    parts = re.split(r",|;|\n|\.\s+|\.$|\band\b", text)
    labels = []
    for part in parts:
        label = part.strip(" .:-").replace("\x00", "and")
        if label and label.lower() not in {l.lower() for l in labels}:
            labels.append(label[0].upper() + label[1:])
    return labels


def _is_closeout(label: str) -> bool:
    """A closeout item (it gates the retainage request, not the pay apps): one the catalog keeps
    for closeout, or a label with closeout words."""
    lower = label.lower()
    if any(entry["closeout"] and entry["words"].search(label) for entry in PAPERWORK_ITEMS):
        return True
    return "closeout" in lower or "close-out" in lower or "retainage" in lower


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:24] or "item"


def _items_from_labels(prefix: str, labels: list[str]) -> list[dict[str, Any]]:
    items, seen = [], set()
    for label in labels:
        kind = _slug(label)
        n, base = 2, kind
        while kind in seen:
            kind = f"{base}-{n}"
            n += 1
        seen.add(kind)
        items.append(_packet_item(prefix, kind, label, f"{label} is missing."))
    return items


def _ensure_closeout_packet(state: dict[str, Any], job: dict[str, Any]) -> None:
    """Every job gets its closeout paperwork when it is marked done (M6), and a job holding
    retainage from its first pay app that holds some. The items are the closeout ones in the
    client's list, else the catalog's five closeout items. They gate the retainage request, not
    the pay apps; on a job with no retainage they are the closeout checklist."""
    if job.get("closeout_packet"):
        return
    if any(item.get("kind") == "closeout" for item in job.get("packet") or []):
        return  # older record: closeout already lives in the job packet
    payer = next((p for p in state["payers"] if p["id"] == job["payer_id"]), {})
    own = job.get("paperwork_labels")
    labels = [l for l in (own if own is not None else parse_packet_text(payer.get("packet", ""))) if _is_closeout(l)]
    prefix = f"closeout-{job['id']}"
    if labels:
        job["closeout_packet"] = _items_from_labels(prefix, labels)
    else:
        job["closeout_packet"] = [_packet_item(prefix, e["key"], e["label"], f"{e['label']} is missing.")
                                  for e in PAPERWORK_ITEMS if e["closeout"]]
    for item in job["closeout_packet"]:
        if _record_kinds_for(item) and _record_kinds_for(item)[0] == "final_waiver":
            item["waiver_kind"] = "conditional_final"


def _packet_for_pay_app(state: dict[str, Any], job: dict[str, Any], invoice_id: str, number: str = "") -> list[dict[str, Any]]:
    """Reuse an existing job packet (older records), else build one from the payer playbook.
    Paperwork Duracash makes is in from the start, and missing items are filled from the job record."""
    if job.get("packet"):
        _attach_from_record(state, job, job["packet"], number or "the job's supporting documents")
        return job["packet"]
    items = _new_packet(state, job, invoice_id)
    for item in items:
        if _made_here(item["label"], job):
            item.update(present=True, present_reason=MADE_HERE_REASON, resolved_at=utcnow())
    _attach_from_record(state, job, items, number or "the pay app")
    return items


def _new_packet(state: dict[str, Any], job: dict[str, Any], invoice_id: str) -> list[dict[str, Any]]:
    """A new pay app's paperwork: the job's own list when it has one (set_job_paperwork), else
    the client's, else the default one. Either way it has a conditional waiver (M16): the first
    stage of the two-stage waiver gate, so Approve & send waits for it unless the client was
    marked as not taking one (skip_packet_item, audited)."""
    payer = next((p for p in state["payers"] if p["id"] == job["payer_id"]), {})
    own = job.get("paperwork_labels")
    labels = [l for l in (own if own is not None else parse_packet_text(payer.get("packet", ""))) if not _is_closeout(l)]
    if labels:
        items = _items_from_labels(invoice_id, labels)
    elif own is not None or payer.get("no_paperwork"):
        items = []  # nothing needed on this job, or every item was marked not needed for this customer
    else:
        items = [
            _packet_item(invoice_id, "contract", "Signed contract", "Signed contract is missing."),
            _packet_item(invoice_id, "photo", "Progress photos", "Progress photos are missing."),
            _packet_item(invoice_id, "backup", "Pay app backup", "Pay app backup is missing."),
        ]
        if job.get("payer_role") == "GC":
            items.append(_packet_item(invoice_id, "sov", "Schedule of values", "Schedule of values is missing."))
            items.append(_packet_item(invoice_id, "insurance", "Insurance certificate", "Insurance certificate is missing."))
    waivers = [item for item in items if (_record_kinds_for(item) or ("",))[0] == "waiver"]
    for item in waivers:
        label = item["label"].lower()
        # A list item naming an unconditional waiver (for an earlier payment) is that type: a
        # conditional waiver never fills it, and it never stands in for the conditional one (B8).
        if "unconditional" in label:
            item["waiver_kind"] = f"unconditional_{'final' if 'final' in label else 'progress'}"
        else:
            item["waiver_kind"] = "conditional_progress"
    if not any(item["waiver_kind"].startswith("conditional") for item in waivers):
        kind = "conditional_progress"
        item = _packet_item(invoice_id, "waiver", WAIVER_KINDS[kind], f"{WAIVER_KINDS[kind]} is missing.")
        item.update(waiver_kind=kind, auto_waiver=True)
        skipped = payer.get("waiver_not_needed")
        if skipped:
            item.update(required=False, skipped_reason=skipped.get("reason") or "Not needed for this client", skipped_at=utcnow())
        items.insert(1 if items and items[0]["kind"] == "contract" else 0, item)
    job_skip = job.get("waiver_skipped")  # Not needed on this job (skip_packet_item): the client's list stays
    for item in items if job_skip else []:
        if str(item.get("waiver_kind", "")).startswith("conditional"):
            item.update(required=False, skipped_reason=job_skip["reason"], skipped_at=job_skip["at"], skipped_by=job_skip["by"], skipped_scope="job")
    return items


def _job_paperwork_view(state: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """The list a job's next pay app reads, for its card: {own, labels, none}. own: the job has its
    own list instead of the client's. A list that also has closeout items names them too."""
    payer = _payer(state, job["payer_id"])
    own = job.get("paperwork_labels")
    if own is not None:
        return {"own": True, "labels": list(own), "none": not own, "legacy": bool(job.get("packet")), "waiver_skipped": job.get("waiver_skipped") or None}
    none = bool(payer.get("no_paperwork"))
    labels = [] if none else parse_packet_text(payer.get("packet", "")) or list(DEFAULT_PAPERWORK["GC" if payer.get("role") == "GC" else "Owner"])
    return {"own": False, "labels": labels, "none": none, "legacy": bool(job.get("packet")), "waiver_skipped": job.get("waiver_skipped") or None}


def set_job_paperwork(state: dict[str, Any], job_id: str, body: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    """Change the paperwork this job needs: {packet: the list, no_paperwork: true for nothing, or
    use_client: true for the client's list again}. It applies from the next pay app and to a draft
    that hasn't been sent; a sent pay app stays as it went (B9). Audited."""
    job = _open_job(state, job_id)
    if job.get("packet"):
        raise GateError("This job's supporting documents were set up on an older pay app, so it can't have its own list.")
    before = _job_paperwork_view(state, job)
    took_back = False
    if body.get("use_client") is True:
        job.pop("paperwork_labels", None)
        # The client's list again is the client's list whole: a waiver marked not needed here comes back.
        took_back = bool(job.get("waiver_skipped"))
        if took_back:
            _waiver_needed_again(state, job, actor)
    else:
        text = body.get("packet") if isinstance(body.get("packet"), str) else ""
        labels = [] if body.get("no_paperwork") is True else parse_packet_text(text)
        if not labels and body.get("no_paperwork") is not True:
            _refuse("Supporting documents not saved.", {"packet": "Tick what this job needs with each pay app, or Nothing needed."})
        job["paperwork_labels"] = labels
    after = _job_paperwork_view(state, job)
    if before["own"] == after["own"] and before["labels"] == after["labels"]:
        if took_back:
            _refresh_draft_gate(job, state)
        return job
    was = {l.lower() for l in before["labels"]}
    now = {l.lower() for l in after["labels"]}
    parts = [f"added {', '.join(l for l in after['labels'] if l.lower() not in was)}" if now - was else "",
             f"took off {', '.join(l for l in before['labels'] if l.lower() not in now)}" if was - now else ""]
    change = "; ".join(p for p in parts if p)
    audit(state, job_id, "job_paperwork",
          f"{actor} set {job['name']}'s supporting documents back to the client's list" + (f": {change}." if change else ".") if not after["own"]
          else f"{actor} changed the supporting documents for {job['name']}: {change or 'nothing needed'}.", actor)
    for invoice in job["invoices"]:
        if invoice["status"] in DRAFT_STATUSES and invoice.get("packet_id") != "job-packet":
            _draft_follows_list(state, job, invoice)
    _refresh_draft_gate(job, state)
    return job


def _draft_follows_list(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> None:
    """A draft takes on the job's list: an item still listed keeps what is attached to it, a new
    one is added, and one taken off goes (its records stay on the job record). The conditional
    waiver (M16) and the tickets for work billed by ticket (M6) stay, whatever the list says."""
    packet = invoice["packet"]
    def same(old: dict[str, Any], new: dict[str, Any]) -> bool:
        if str(new.get("waiver_kind", "")).startswith("conditional"):
            return str(old.get("waiver_kind", "")).startswith("conditional")
        return old["label"].lower() == new["label"].lower()
    kept: list[dict[str, Any]] = []
    for new in _new_packet(state, job, invoice["id"]):
        old = next((o for o in packet if same(o, new) and not any(o is k for k in kept)), None)
        if old and not old.get("required") and old.get("skipped_reason") and new.get("required"):
            # Ticked again on the list: asked for again. (A waiver marked not needed reads as not
            # required on `new` too, so a change to the rest of the list leaves it off.)
            _item_needed_again(old)
            audit(state, job["id"], "paperwork_needed", f"{old['label']} is needed on {invoice['number']} again: it is back on the job's list.", "duracash")
        if not old and _made_here(new["label"], job):
            new.update(present=True, present_reason=MADE_HERE_REASON, resolved_at=utcnow())
        kept.append(old or new)
    kept += [o for o in packet if o.get("covers") and not any(o is k for k in kept)]
    packet[:] = kept
    _name_conditional_waiver(job, invoice)


def _item_needed_again(item: dict[str, Any]) -> None:
    item["required"] = True
    for key in ("skipped_reason", "skipped_at", "skipped_by", "skipped_scope", "detached_record_ids"):
        item.pop(key, None)


def _waiver_needed_again(state: dict[str, Any], job: dict[str, Any], actor: str) -> None:
    """Not needed on this job, taken back: the job's mark goes, and the open draft asks again."""
    skip = job.pop("waiver_skipped", None)
    for invoice in job["invoices"]:
        if invoice["status"] in DRAFT_STATUSES:
            for item in invoice.get("packet") or []:
                if item.get("skipped_scope") == "job" and str(item.get("waiver_kind", "")).startswith("conditional"):
                    _item_needed_again(item)
    audit(state, job["id"], "paperwork_needed", f"{actor} marked the conditional waiver needed after all on {job['name']}"
          + (f" (it was marked not needed: {skip['reason']})." if skip else "."), actor)


def waiver_needed_after_all(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    """The way back from Not needed on this job (skip_packet_item). What was marked for the whole
    client is changed on the client's playbook."""
    job = _open_job(state, job_id)
    if not job.get("waiver_skipped"):
        raise GateError("The conditional waiver isn't marked not needed on this job.")
    _waiver_needed_again(state, job, actor)
    _refresh_draft_gate(job, state)
    return job


def client_waiver_needed_again(state: dict[str, Any], payer_id: str, actor: str = "owner") -> dict[str, Any]:
    """The way back from Not needed for this client, on the Client playbook card: new pay apps ask
    for the conditional waiver again, and so do the client's open drafts, except on a job marked not
    needed on its own. After-payment waivers already settled as not due stay as they were."""
    payer = _payer(state, payer_id)
    skip = payer.pop("waiver_not_needed", None)
    if not skip:
        raise GateError(f"Lien waivers aren't marked not needed for {payer['name']}.")
    audit(state, "", "paperwork_needed", f"{actor} marked lien waivers needed again for {payer['name']} "
          f"(marked not needed by {skip.get('by', 'someone')} on {str(skip.get('at', ''))[:10]}: {skip.get('reason', 'no reason given')}).", actor)
    for job in state["jobs"]:
        if job.get("payer_id") != payer_id or job.get("closed") or job.get("waiver_skipped"):
            continue
        for invoice in job["invoices"]:
            if invoice["status"] in DRAFT_STATUSES:
                for item in invoice.get("packet") or []:
                    if not item.get("required") and item.get("skipped_scope") != "job" and str(item.get("waiver_kind", "")).startswith("conditional"):
                        _item_needed_again(item)
        _refresh_draft_gate(job, state)
    return payer


def _work_billed_by_ticket(job: dict[str, Any], invoice: dict[str, Any]) -> list[str]:
    """What a pay app bills that daily tickets back up (M6): its change orders and its unit-price
    (not lump-sum) schedule lines, by name."""
    lines = {line["id"]: line for line in sov.lines(job)}
    cos = {co["id"]: co for co in job.get("change_orders", [])}
    out = []
    for line in invoice.get("lines", []):
        if line.get("co_id") in cos:
            out.append(f"CO {cos[line['co_id']]['number']}")
        sov_line = lines.get(line.get("sov_line_id"))
        if sov_line and sov_line.get("co_id") in cos:
            out.append(f"CO {cos[sov_line['co_id']]['number']}")
        elif sov_line and not (str(sov_line.get("unit", "")).upper() == "LS" and sov_line.get("qty_milli") == 1000):
            out.append(sov_line.get("description") or line["label"])
    return list(dict.fromkeys(out))


def _add_ticket_item(job: dict[str, Any], invoice: dict[str, Any], payer: dict[str, Any] | None = None) -> bool:
    """A pay app billing change-order or unit-price work needs its daily tickets and time sheets
    (M6), when the client's list doesn't already ask for them. A client marked as not wanting
    them (skip_packet_item; B7 of the Oct 30 review) gets the item as not needed, with the reason.
    Returns whether a required one was added."""
    covers = _work_billed_by_ticket(job, invoice)
    packet = invoice.get("packet") or []
    if not covers:
        return False
    for item in packet:
        if (_record_kinds_for(item) or ("",))[0] == "ticket":
            item["covers"] = covers
            return False
    entry = next(e for e in PAPERWORK_ITEMS if e["key"] == "ticket")
    item = _packet_item(invoice["id"], "ticket", entry["label"], f"{entry['label']} are missing.")
    item["covers"] = covers
    packet.append(item)
    skipped = (payer or {}).get("tickets_not_needed")
    if skipped:
        item.update(required=False, skipped_reason=skipped.get("reason") or "Not needed for this client", skipped_at=utcnow())
        return False
    return True


def _kind_problem(has: str, needs: str) -> str:
    return f"It is a {WAIVER_KINDS[has].lower()}, and this pay app needs a {WAIVER_KINDS[needs].lower()}. Look at it again."


def _name_conditional_waiver(job: dict[str, Any], invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """The conditional waiver on a pay app is on final payment when the pay app is the final one
    (M13) and its money is the last there is (still_open, the rule of suggested_waiver_kind; B3 of
    the Oct 30 review): a waiver on final payment gives up what else is unpaid or held back.
    Its waiver items say so, and the type is kept on the pay app as it goes out. A waiver confirmed
    as one type isn't confirmed as the other: returns the items now waiting for another look."""
    final = bool(invoice.get("final")) and not still_open(job, invoice)
    kind = "conditional_final" if final else "conditional_progress"
    flipped = []
    for item in invoice.get("packet") or []:
        was = str(item.get("waiver_kind", ""))
        if not was.startswith("conditional"):
            continue
        item["waiver_kind"] = kind
        if item.get("auto_waiver"):
            item["label"] = WAIVER_KINDS[kind]
            item["blocker"] = f"{WAIVER_KINDS[kind]} is missing."
        if was == kind or was not in WAIVER_KINDS:
            continue
        if _ask_for_a_look(item, _kind_problem(was, kind)):
            flipped.append(item)
        elif not item.get("present") and (item.get("check") or {}).get("problems"):
            item["check"]["problems"].append(_kind_problem(was, kind))  # already waiting: all of it goes in one look
            item["check"]["status"] = "flagged"
    return flipped


# Paperwork Duracash makes itself, so a pay app never waits on it: the pay app and its schedule
# of values or continuation sheet. Backup counts only when the job bills from a schedule (the
# continuation sheet is the backup); otherwise it is tickets and invoices from the job record.
# The whole label has to name it: "Lien waiver for pay application" is a waiver, not the pay app.
MADE_HERE_RE = re.compile(r"(?:(?:the|aia|aia[\s-]style)\s+)?(?:pay[\s-]*app(?:lication)?s?|schedule of values|continuation sheet)(?:\s*\(.*\))?", re.I)
BACKUP_RE = re.compile(r"(?:pay[\s-]*app(?:lication)?\s+)?backup", re.I)
# A notarized pay application comes back from a notary, so it is not made here; it is its own item.
NOTARIZED_RE = re.compile(r"notari[sz]ed\s+(?:pay[\s-]*app|application)", re.I)
MADE_HERE_REASON = "Produced by Duracash"

# The standard paperwork (M6), for each pay app and for closeout: key (the item's kind), label,
# the job-record kinds that fill it, the words that name it in a client's list, per_pay_app (a
# record goes with one pay app only) and closeout (it belongs to the closeout paperwork). The
# client playbook's checkboxes (S11) are this list, in this order: PAPERWORK_CATALOG is the
# JSON-ready copy light_snapshot sends as paperwork_catalog, and a new client's paperwork
# (_new_payer) uses these labels so it shows as ticked boxes.
PAPERWORK_ITEMS = (
    {"key": "contract", "label": "Signed contract", "accepts_kinds": ("contract",), "words": re.compile(r"contract", re.I),
     "per_pay_app": False, "closeout": False},
    {"key": "waiver", "label": "Conditional waiver", "accepts_kinds": ("waiver",), "words": re.compile(r"waiver", re.I),
     "per_pay_app": True, "closeout": False},
    {"key": "photo", "label": "Progress photos", "accepts_kinds": ("photo",), "words": re.compile(r"photo", re.I),
     "per_pay_app": True, "closeout": False},
    {"key": "ticket", "label": "Daily tickets and time sheets", "accepts_kinds": ("ticket",),
     "words": re.compile(r"ticket|time ?sheet", re.I), "per_pay_app": True, "closeout": False},
    {"key": "supplier_invoice", "label": "Supplier invoices and receipts", "accepts_kinds": ("supplier_invoice",),
     "words": re.compile(r"invoice|receipt", re.I), "per_pay_app": True, "closeout": False},
    {"key": "insurance", "label": "Insurance certificate", "accepts_kinds": ("insurance",),
     "words": re.compile(r"insurance|\bcoi\b", re.I), "per_pay_app": False, "closeout": False},
    {"key": "stored", "label": "Stored materials documents", "accepts_kinds": ("stored",),
     "words": re.compile(r"stored material", re.I), "per_pay_app": True, "closeout": False},
    {"key": "backup", "label": "Pay app backup", "accepts_kinds": ("pay_app",), "words": re.compile(r"backup", re.I),
     "per_pay_app": True, "closeout": False},
    # Public jobs: each has its own record kind, since a time sheet or the app's backup isn't one.
    {"key": "payroll", "label": "Certified payroll", "accepts_kinds": ("payroll",), "words": re.compile(r"payroll", re.I),
     "per_pay_app": True, "closeout": False},
    {"key": "notarized", "label": "Notarized pay application", "accepts_kinds": ("notarized",), "words": NOTARIZED_RE,
     "per_pay_app": True, "closeout": False},
    {"key": "punch", "label": "Punch list sign-off", "accepts_kinds": ("punch",), "words": re.compile(r"punch", re.I),
     "per_pay_app": False, "closeout": True},
    {"key": "completion", "label": "Completion certificate", "accepts_kinds": ("completion",),
     "words": re.compile(r"completion cert|certificate of (?:substantial )?completion", re.I), "per_pay_app": False, "closeout": True},
    {"key": "final_waiver", "label": "Final lien waivers", "accepts_kinds": ("waiver",),
     "words": re.compile(r"final (?:lien )?waiver", re.I), "per_pay_app": False, "closeout": True},
    {"key": "asbuilt", "label": "As-builts", "accepts_kinds": ("asbuilt",), "words": re.compile(r"as[- ]?built", re.I),
     "per_pay_app": False, "closeout": True},
    {"key": "warranty", "label": "Warranties", "accepts_kinds": ("warranty",), "words": re.compile(r"warrant", re.I),
     "per_pay_app": False, "closeout": True},
)
# A new client's paperwork, by role (_new_payer). The pay app and its schedule of values are
# made here, so a GC's list leaves them off.
DEFAULT_PAPERWORK = {
    "Owner": ("Signed contract", "Conditional waiver", "Progress photos", "Pay app backup"),
    "GC": ("Conditional waiver", "Insurance certificate", "Daily tickets and time sheets"),
}
PAPERWORK_CATALOG = [{k: e[k] for k in ("key", "label", "per_pay_app", "closeout")} | {"accepts_kinds": list(e["accepts_kinds"])}
                     for e in PAPERWORK_ITEMS]

# Packet item -> the job-record kinds that fill it, and the words that name it in a customer's
# list (items from the list carry a slug of their label as their kind). First match wins, so
# "Contractor's insurance certificate" is insurance, not the contract. Items whose kind is a
# catalog key take the catalog's kinds; this table is the fallback for typed labels.
RECORD_FOR_ITEM = (
    ("notarized", ("notarized",), NOTARIZED_RE),
    ("payroll", ("payroll",), re.compile(r"payroll", re.I)),
    ("backup", ("pay_app",), re.compile(r"backup", re.I)),
    ("insurance", ("insurance",), re.compile(r"insurance|\bcoi\b", re.I)),
    ("co", ("change_order",), re.compile(r"change order", re.I)),
    ("final_waiver", ("waiver",), re.compile(r"final (?:lien )?waiver", re.I)),
    ("waiver", ("waiver",), re.compile(r"waiver", re.I)),
    ("photo", ("photo",), re.compile(r"photo", re.I)),
    ("supplier_invoice", ("supplier_invoice",), re.compile(r"invoice|receipt", re.I)),
    ("ticket", ("ticket",), re.compile(r"ticket|time ?sheet", re.I)),
    ("stored", ("stored",), re.compile(r"stored material", re.I)),
    ("inspection", ("inspection",), re.compile(r"inspection", re.I)),
    ("punch", ("punch",), re.compile(r"punch", re.I)),
    ("completion", ("completion",), re.compile(r"completion cert|certificate of (?:substantial )?completion", re.I)),
    ("asbuilt", ("asbuilt",), re.compile(r"as[- ]?built", re.I)),
    ("warranty", ("warranty",), re.compile(r"warrant", re.I)),
    ("contract", ("contract",), re.compile(r"contract", re.I)),
)
# Records that belong to one pay app (this period's waiver, photos, backup) are not reused on the
# next one; a signed contract or an insurance certificate is.
PER_PAY_APP = {"backup", "co", "waiver", "photo", "ticket", "supplier_invoice", "stored", "inspection", "payroll", "notarized"}
# Items several records can fill together, all suggested (acceptance 46). The rest take one document.
MANY_RECORDS = {"photo", "ticket", "supplier_invoice", "stored", "inspection", "payroll"}
TICKET_WORDS = re.compile(r"ticket|time ?sheet", re.I)
INVOICE_WORDS = re.compile(r"invoice|receipt", re.I)
AUTO_NOTE = "Attached from the job records"


def _made_here(label: str, job: dict[str, Any]) -> bool:
    label = label.strip()
    if BACKUP_RE.fullmatch(label):
        # The continuation sheet backs up a schedule with detail; one lump-sum line backs up nothing.
        base = [line for line in sov.lines(job) if not line.get("co_id")]
        return len(base) > 1 or any(line.get("qty_milli") and not (str(line.get("unit", "")).upper() == "LS" and line["qty_milli"] == 1000) for line in base)
    return bool(MADE_HERE_RE.fullmatch(label))


def _record_kinds_for(item: dict[str, Any]) -> tuple[str, tuple[str, ...]] | None:
    """(item name, the job-record kinds that fill it), or None when nothing on the record can. A
    supplier invoice fills a ticket item only when the item's label asks for invoices too."""
    label = str(item.get("label") or "")
    if TICKET_WORDS.search(label) and INVOICE_WORDS.search(label):
        return "ticket", ("ticket", "supplier_invoice")
    entry = next((e for e in PAPERWORK_ITEMS if e["key"] == item.get("kind")), None)
    if entry:
        return entry["key"], entry["accepts_kinds"]
    for name, kinds, words in RECORD_FOR_ITEM:
        if item.get("kind") == name or words.search(item.get("label", "")):
            return name, kinds
    return None


def _attach_from_record(state: dict[str, Any], job: dict[str, Any], packet: list[dict[str, Any]], where: str) -> None:
    """Suggest for each missing item the newest job record of the kind that fits it and has a file.
    A suggestion doesn't clear the item: a person confirms they checked it (confirm_packet_item)."""
    used = {
        a.get("record_id")
        for inv in job.get("invoices", []) if inv.get("status") != "void" and inv.get("packet") is not packet
        for p in inv.get("packet") or [] for a in p.get("attachments", [])
    }
    # A waiver that already went out in an email belongs to that payment, not this pay app (M18).
    sent = _sent_file_ids(state, job)
    used |= {r["id"] for r in job.get("records", []) if r.get("kind") == "waiver" and r.get("file_ids") and set(r["file_ids"]) <= sent}
    owner = next((inv for inv in job.get("invoices", []) if inv.get("packet") is packet), None)
    for item in packet:
        if item.get("present") or not item.get("required"):
            continue
        attached = item.get("attachments", [])
        if any(a.get("unconfirmed") and not a.get("auto") for a in attached):
            continue  # one a person attached is waiting for their look
        match = _record_kinds_for(item)
        if not match:
            continue
        name, kinds = match
        declined = set(item.get("detached_record_ids", []))
        fits = [
            r for r in reversed(job.get("records", []))
            if r.get("kind") in kinds and not r.get("number_only") and r["id"] not in declined
            and not ((name in PER_PAY_APP or name == "final_waiver") and r["id"] in used) and _fits_waiver_item(r, item, owner)
            and _fits_work(r, owner) and _can_fill(state, r, item)
        ]
        if name not in MANY_RECORDS:
            fits = fits[:1]  # a newer record takes over
        wanted = {r["id"] for r in fits}
        auto = {a.get("record_id") for a in attached if a.get("auto")}
        if wanted == auto:
            continue
        item["attachments"] = [a for a in attached if not a.get("auto") or a.get("record_id") in wanted]
        for record in fits:
            if record["id"] in auto:
                continue
            item["attachments"].append({
                "name": record["label"], "record_id": record["id"], "added_at": utcnow(), "added_by": "duracash", "auto": True, "unconfirmed": True,
            })
            audit(state, job["id"], "attach_record", f"{record['label']} suggested from the job records for {item['label']} on {where}.", "duracash")
        item.pop("check", None)


# What can fill an item (Stephen's Nov 1 notes): a stored file that isn't empty, of a type a person
# can open. A lien waiver or a contract is a document, not a spreadsheet.
DOCUMENT_TYPES = {".pdf", ".doc", ".docx", ".txt", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
SHEET_TYPES = {".csv", ".xls", ".xlsx"}
DOCUMENTS_ONLY = {"waiver", "final_waiver", "contract", "insurance", "completion"}
NOTE_ITEM_RE = sov.NOTE_ITEM_RE


def _takes_note(item: dict[str, Any]) -> bool:
    """The one kind of item a record with no file can fill: its label asks for a note."""
    return bool(NOTE_ITEM_RE.search(str(item.get("label") or "")))


def _record_files(state: dict[str, Any], record: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    """The record's stored files that can fill this item. A size is kept on every stored file; an
    older entry without one isn't taken for empty."""
    allowed = DOCUMENT_TYPES if (_record_kinds_for(item) or ("",))[0] in DOCUMENTS_ONLY else DOCUMENT_TYPES | SHEET_TYPES
    ids = set(record.get("file_ids") or [])
    return [f for f in state.get("files", []) if f["id"] in ids and f.get("size", 1) and str(f.get("ext") or "").lower() in allowed]


def _can_fill(state: dict[str, Any], record: dict[str, Any], item: dict[str, Any]) -> bool:
    return bool(_record_files(state, record, item)) or (_takes_note(item) and not record.get("file_ids"))


def _going(item: dict[str, Any]) -> list[dict[str, Any]]:
    """An item's attachments that go with the email: not one still waiting to be confirmed."""
    return [a for a in item.get("attachments", []) if not a.get("unconfirmed")]


def _drop_unconfirmed(item: dict[str, Any]) -> None:
    """Take off what nobody confirmed. A suggestion taken off this way isn't made again."""
    waiting = [a for a in item.get("attachments", []) if a.get("unconfirmed")]
    item.setdefault("detached_record_ids", []).extend(a["record_id"] for a in waiting if a.get("auto") and a.get("record_id"))
    item["attachments"] = _going(item)


FIELD_RECORD_KINDS = {"ticket", "photo", "inspection", "supplier_invoice"}


def _document_check(state: dict[str, Any], job: dict[str, Any], item: dict[str, Any], invoice: dict[str, Any] | None, read_file: Any) -> dict[str, Any] | None:
    """Compare the item's files with the job (Decision 6): a waiver with the job name, the amount
    and the conditional wording, any other document with the job name. Progress photos have
    nothing to compare. read_file(meta) gives a file's bytes; without it nothing is read."""
    name = (_record_kinds_for(item) or ("",))[0]
    records = {r["id"]: r for r in job.get("records", [])}
    attached = [records.get(a.get("record_id")) or {} for a in item.get("attachments", [])]
    check = None
    if read_file is not None and name != "photo":
        metas = [f for a in item.get("attachments", []) for f in _record_files(state, records.get(a.get("record_id")) or {}, item)]
        if metas:
            waiver = name in {"waiver", "final_waiver"} or bool(item.get("waiver_kind"))
            cents = invoice["amount_cents"] if waiver and invoice else None
            # Field paperwork often doesn't name the job, so that is no reason to stop anyone.
            field = not waiver and set((_record_kinds_for(item) or ("", ("-",)))[1]) <= FIELD_RECORD_KINDS
            check = doc_check.check([(m["name"], str(m.get("ext") or "").lower(), read_file(m)) for m in metas], "" if field else job["name"],
                                    money(cents) if cents is not None else "", cents, str(item.get("waiver_kind") or ""), waiver)
    # A record that says another type than the item needs (progress for final, say).
    if item.get("waiver_kind") in WAIVER_KINDS:
        for rec in attached:
            has = record_waiver_kind(rec) if rec.get("kind") == "waiver" else ""
            if has and has != item["waiver_kind"] and has.split("_")[0] == item["waiver_kind"].split("_")[0]:   # the other word is M14's gate at send
                check = check or {"status": "flagged", "problems": []}
                check["problems"].append(_kind_problem(has, item["waiver_kind"]))
                check["status"] = "flagged"
    return check


def _check_stamp(job: dict[str, Any], item: dict[str, Any], invoice: dict[str, Any] | None) -> dict[str, Any]:
    """What a check of an item was for: its files, and for a waiver, the amount."""
    records = {r["id"]: r for r in job.get("records", [])}
    files = sorted(f for a in item.get("attachments", []) for f in (records.get(a.get("record_id")) or {}).get("file_ids") or [])
    return {"file_ids": files, "cents": invoice["amount_cents"] if invoice and item.get("waiver_kind") else None}


def _clear_item(state: dict[str, Any], job: dict[str, Any], item: dict[str, Any], invoice: dict[str, Any] | None, actor: str,
                read_file: Any, looked: bool, confirming: bool = False, answer: str = "", answerer: "Answerer | None" = None) -> None:
    """The person's check (Stephen's Nov 1 notes, Decision 6): the item clears with who and when,
    unless the file doesn't match or can't be read. Then it waits, with the warning, until they
    say they opened it and it is right (looked), a separate step from the warning naming what
    didn't match, which clears it whatever the warning said. confirming: this is Confirm on the
    item (not an attach): one look clears the file at this amount, whatever else the check finds now.
    A waiver item clears once someone says which waiver its files are (answer, the Nov 6 redesign):
    that tap is the look at what the item shows, and it is kept on each record (_say_which)."""
    shown = set((item.get("check") or {}).get("problems") or [])
    stamp = _check_stamp(job, item, invoice)
    same_file = item.get("check_for") is not None and item["check_for"] == stamp
    records = {r["id"]: r for r in job.get("records", [])}
    for a in item.get("attachments", []):   # read once for the waiver question and the pay app's page
        if a.get("record_id") in records:
            _read_waiver(state, records[a["record_id"]], read_file)
    check = _document_check(state, job, item, invoice, read_file)
    if check is not None:
        item["check"] = check = _keep_asked_look(item, check, stamp)
        item["check_for"] = stamp
    else:
        check = item.get("check")
    which = invoice["number"] if invoice else "the job's supporting documents"
    problems = (check or {}).get("problems") or []
    waiver = is_waiver_item(item)
    filed = _filed_records(job, item)
    answer = answer if waiver and answer in WAIVER_ANSWERS else ""
    looked = looked or bool(answer)
    # Which waiver it is: answered now, or already for each record's files as they are.
    unanswered = waiver and bool(filed) and not answer and not all(waiver_answer(r) in WAIVER_ANSWERS for r in filed)
    # A photo or a scan the check can't read, added by the person (not a suggestion they confirm):
    # adding it is their check, and nothing was compared that could stand in the way (round 1, item 3).
    unread = bool(problems) and not confirming and (check or {}).get("status") != "flagged" and not waiver
    # Looked counts only for the warning the person was shown: a first attach never clears a flag.
    # A waiver's answer is all a file that couldn't be read needs: nothing was compared.
    told = (waiver and bool(answer) and (check or {}).get("status") != "flagged") or (looked and (set(problems) <= shown or (confirming and same_file)))
    if unanswered or (problems and not unread and not told):
        for a in item.get("attachments", []):
            a["unconfirmed"] = True
        if problems and not (waiver and (check or {}).get("status") == "unchecked"):
            audit(state, job["id"], "document_flagged", f"Document check on {item['label']} on {which}: {' '.join(problems)}", "duracash")
        item["present"] = False
        return
    for a in item.get("attachments", []):
        a.pop("unconfirmed", None)
        a.pop("auto", None)
    # A name with a real file beside it now has nothing to say.
    if any(_is_filed(records, a) for a in item.get("attachments", [])):
        item["attachments"] = [a for a in item["attachments"] if a.get("record_id") in records]
    item["present"] = True
    item["resolved_at"] = utcnow()
    item["confirmed_by"] = actor
    item["confirmed_at"] = item["resolved_at"]
    item.pop("files_left_out", None)
    names = ", ".join(a["name"] for a in item.get("attachments", []))
    if unread:
        files = [f for a in item.get("attachments", []) for f in _record_files(state, records.get(a.get("record_id")) or {}, item)]
        item["unread"] = "photo" if files and all(str(f.get("ext") or "").lower() in doc_check.PICTURES for f in files) else "file"
        audit(state, job["id"], "packet_confirmed", f"{actor} added {names} for {item['label']} on {which}. It couldn't be read, so nothing was compared.", actor)
        return
    item.pop("unread", None)
    warned = f" after the warning: {' '.join(problems)}" if problems and (check or {}).get("status") == "flagged" else ""
    if waiver and filed:
        stage = "final" if str(item.get("waiver_kind") or "").endswith("_final") else "progress"
        for rec in filed:
            if answer:
                _say_which(state, job, rec, answer, actor, stage, answerer=answerer)
        item["looked_as"] = answer or waiver_answer(filed[0])
        audit(state, job["id"], "packet_confirmed", f"{actor} said {names} is the {item['looked_as']} waiver for {item['label']} on {which}{warned}.", actor)
        return
    item.pop("looked_as", None)
    audit(state, job["id"], "packet_confirmed", f"{actor} checked {names} for {item['label']} on {which}{warned}.", actor)


def _keep_asked_look(item: dict[str, Any], check: dict[str, Any], stamp: dict[str, Any]) -> dict[str, Any]:
    """A look asked for on these same files (a new amount, the other type, a mismatch) stands until a
    person gives it: a check that could read nothing new doesn't answer it."""
    was = item.get("check") or {}
    if check.get("status") != "unchecked" or was.get("status") != "flagged":
        return check
    if (item.get("check_for") or {}).get("file_ids") not in (None, stamp["file_ids"]):
        return check
    asked = list(was.get("problems") or [])
    return {"status": "flagged", "problems": asked + [p for p in check.get("problems") or [] if p not in asked]}


def _is_filed(records: dict[str, Any], attachment: dict[str, Any]) -> bool:
    return bool((records.get(attachment.get("record_id")) or {}).get("file_ids"))


def _item_to_confirm(state: dict[str, Any], job: dict[str, Any], item_id: str, shown: list[str] | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    item, invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Supporting document not found.")
    _still_a_draft(invoice)
    records = {r["id"]: r for r in job.get("records", [])}
    if not any(_can_fill(state, records.get(a.get("record_id")) or {}, item) for a in item.get("attachments", [])):
        raise GateError(f"Attach the file for {item['label']} first.")
    waiting = [a for a in item.get("attachments", []) if a.get("unconfirmed")]
    # An attachment by name only has no record: the page sends a blank for it.
    if shown is not None and {r or "" for r in shown} != {a.get("record_id") or "" for a in waiting}:
        names = ", ".join(a["name"] for a in waiting) or "the file"
        raise GateError(f"{item['label']} changed since you opened it: {names} is on it now. Look at that, then confirm.",
                        extra={"review_changed": True, "invoice_id": (invoice or {}).get("id", "")})
    if invoice and invoice.get("packet_id") != "job-packet":
        # Saved before a change said so: what this confirm is for is the type the pay app needs now.
        invoice["final"] = is_final(job)
        _name_conditional_waiver(job, invoice)
    return item, invoice


def recheck_packet_item(state: dict[str, Any], job_id: str, item_id: str, read_file: Any = None, shown: list[str] | None = None) -> list[str]:
    """Read what is on an item as Confirm would, clearing nothing, and return every problem: what
    the prompt lists, so one look answers all of it."""
    job = _open_job(state, job_id)
    item, invoice = _item_to_confirm(state, job, item_id, shown)
    check = _document_check(state, job, item, invoice, read_file)
    if check is not None:
        stamp = _check_stamp(job, item, invoice)
        item["check"] = _keep_asked_look(item, check, stamp)
        item["check_for"] = stamp
    return list((item.get("check") or {}).get("problems") or [])


def recheck_reply(state: dict[str, Any], job_id: str, item_id: str, read_file: Any = None, shown: list[str] | None = None) -> dict[str, Any]:
    """What the page's Confirm reads before it confirms: every problem, and whether any of them is a
    mismatch for the person to answer ("flagged"), or only a file that couldn't be read."""
    problems = recheck_packet_item(state, job_id, item_id, read_file=read_file, shown=shown)
    item, _invoice = _find_packet_item(_open_job(state, job_id), item_id)
    return {"check_problems": problems, "check_status": ((item or {}).get("check") or {}).get("status", "")}


def confirm_packet_item(state: dict[str, Any], job_id: str, item_id: str, actor: str = "owner", looked: bool = False,
                        read_file: Any = None, shown: list[str] | None = None, answer: str = "", answerer: "Answerer | None" = None) -> dict[str, Any]:
    """A person confirms they checked what is attached to an item: a suggestion from the job
    record, or a file the document check flagged (looked says they opened it and it is right).
    On a waiver item the confirm is the answer to which waiver it is (answer: conditional or
    unconditional). shown is the records on their screen: a newer one may have taken over since,
    and that one hasn't been looked at."""
    job = _open_job(state, job_id)
    item, invoice = _item_to_confirm(state, job, item_id, shown)
    records = {r["id"]: r for r in job.get("records", [])}
    if is_waiver_item(item):
        unanswered = answer not in WAIVER_ANSWERS and not all(waiver_answer(r) in WAIVER_ANSWERS for r in _filed_records(job, item))
        if (answer in WAIVER_ANSWERS or unanswered) and not _may_answer(answerer, _on_retainage_request(job, item=item)):
            raise GateError(NOT_YOURS_TO_ANSWER, fields={"answer": NOT_YOURS_TO_ANSWER})
        if unanswered:
            _refuse("", {"answer": f"Which waiver is on {item['label']}? Say conditional or unconditional."})
    _clear_item(state, job, item, invoice, actor, read_file, looked, confirming=True, answer=answer, answerer=answerer)
    if item["present"] and invoice and invoice.get("excluded_file_ids"):
        # Confirmed with its file left out over a redraft (_ask_for_a_look): what clears goes with the email.
        back = {fid for a in item.get("attachments", []) for fid in records.get(a.get("record_id"), {}).get("file_ids", [])}
        invoice["excluded_file_ids"] = [fid for fid in invoice["excluded_file_ids"] if fid not in back]
    _refresh_draft_gate(job, state)
    return job


def _fits_waiver_item(record: dict[str, Any], item: dict[str, Any], invoice: dict[str, Any] | None) -> bool:
    """A waiver record fills a waiver item only when its type fits (an unconditional waiver never
    fills a conditional item, M14) and it was not taken for another pay app."""
    if record.get("kind") != "waiver" or not item.get("waiver_kind"):
        return True
    if record.get("sent_as_final") and (_record_kinds_for(item) or ("",))[0] == "final_waiver":
        return True   # the final waiver that went out, whichever type the closeout's item names
    has = record_waiver_kind(record)
    if has and has != item["waiver_kind"]:
        return False  # a progress waiver isn't suggested for a final payment, nor the other way
    if has and (_record_kinds_for(item) or ("",))[0] == "final_waiver" and not has.endswith("_final"):
        return False  # the closeout's final waiver is never a progress one
    # Taken for a pay app: it stays that pay app's, never another's or the closeout's; taken for
    # the earlier job-level payment, it is that payment's.
    if record.get("waiver_legacy"):
        return False
    return not (record.get("invoice_id") and (invoice is None or record["invoice_id"] != invoice["id"]))


def _fits_work(record: dict[str, Any], invoice: dict[str, Any] | None) -> bool:
    """A ticket tied to a change order or a schedule line (M6) goes with a pay app that bills it."""
    tied = record.get("co_id") or record.get("sov_line_id")
    if not tied or invoice is None:
        return True
    billed = {line.get("co_id") for line in invoice.get("lines", [])} | {line.get("sov_line_id") for line in invoice.get("lines", [])}
    return tied in billed


def detach_packet_item(state: dict[str, Any], job_id: str, item_id: str, actor: str = "owner") -> dict[str, Any]:
    """Remove (R3-05): take everything off an item, whether attached by hand, attached from the
    job record or marked as in hand, so it is missing again. The records stay on the job record,
    and one attached automatically is not attached again."""
    job = _open_job(state, job_id)
    item, _invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Supporting document not found.")
    if item.get("present_reason") == MADE_HERE_REASON:
        raise GateError(f"Duracash makes {item['label']} with the pay app; there is nothing to remove.")
    if _invoice and _invoice.get("status") not in DRAFT_STATUSES:
        # What went with a pay app stays as it was sent; the customer may already have it.
        raise GateError(f"{_invoice['number']} has gone to the client, so its supporting documents stay as they were sent.")
    taken = item.get("attachments", [])
    if not taken and not item.get("present"):
        raise GateError(f"Nothing is attached to {item['label']}.")
    item["attachments"] = []
    item.setdefault("detached_record_ids", []).extend(a["record_id"] for a in taken if a.get("auto") and a.get("record_id"))
    item["present"] = False
    item.pop("resolved_at", None)
    item.pop("present_reason", None)
    item.pop("marked_in_hand", None)
    for key in ("override", "confirmed_by", "confirmed_at", "check", "files_left_out"):
        item.pop(key, None)
    names = ", ".join(a["name"] for a in taken) or "the in-hand mark"
    audit(state, job_id, "paperwork_detached", f"{actor} took {names} off {item['label']}.", actor)
    _refresh_draft_gate(job)
    return job


def _still_a_draft(invoice: dict[str, Any] | None) -> None:
    """Paperwork that went with a pay app stays as it was sent (B9 of the Oct 30 review): the
    client may already have it. Closeout and older job paperwork have no pay app."""
    if invoice and invoice.get("status") not in DRAFT_STATUSES:
        raise GateError(f"{invoice['number']} has gone to the client, so its supporting documents stay as they were sent.")


def skip_packet_item(state: dict[str, Any], job_id: str, item_id: str, reason: str = "", actor: str = "owner",
                     whole_client: bool = False, on_client: bool = True) -> dict[str, Any]:
    """This customer does not need the item: it stops blocking, and it leaves the customer's
    paperwork list so later pay apps do not ask for it. The conditional waiver and the daily
    tickets Duracash adds itself are kept off on the client instead (waiver_not_needed,
    tickets_not_needed), since they are not on the list. A conditional waiver is the exception
    (accept. 10): it comes off this job only unless the person chose the whole client.
    on_client False: this person can't change the client's settings, so it comes off this pay app
    only, and the client's list stays as it is."""
    job = _open_job(state, job_id)
    item, _invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Supporting document not found.")
    _still_a_draft(_invoice)
    reason = (reason or "").strip()[:500] if isinstance(reason, str) else ""
    _drop_unconfirmed(item)
    item["required"] = False
    item["skipped_reason"] = reason or "Not needed for this client"
    item["skipped_at"] = utcnow()
    item["skipped_by"] = actor
    if str(item.get("waiver_kind", "")).startswith("conditional") and _invoice and not whole_client:
        if not reason:
            item["skipped_reason"] = "Not needed on this job"
        item["skipped_scope"] = "job"
        job["waiver_skipped"] = {"by": actor, "at": item["skipped_at"], "reason": item["skipped_reason"]}
        audit(state, job_id, "paperwork_not_needed", f"{actor} marked {item['label']} not needed on this job: {item['skipped_reason']}.", actor)
        _refresh_draft_gate(job)
        return job
    # A client's list and rules take the permission, on an older job-level list too (no pay app here).
    if not on_client and (_invoice or not any(x is item for x in job.get("closeout_packet") or [])):
        item["skipped_scope"] = "pay_app"
        audit(state, job_id, "paperwork_not_needed", f"{actor} marked {item['label']} not needed on {_invoice['number'] if _invoice else 'this job'}: {item['skipped_reason']}.", actor)
        _refresh_draft_gate(job)
        return job
    payer = _payer(state, job["payer_id"])
    if str(item.get("waiver_kind", "")).startswith("conditional") and _invoice:
        # The conditional waiver comes back on every pay app (M16) unless the client doesn't take one.
        payer["waiver_not_needed"] = {"by": actor, "at": utcnow(), "reason": item["skipped_reason"]}
    if item.get("kind") == "ticket" and _invoice:
        # Added by Duracash for work billed by ticket (M6), not from the client's list (B7).
        payer["tickets_not_needed"] = {"by": actor, "at": utcnow(), "reason": item["skipped_reason"]}
    # A closeout item (M6: five by default) is not needed on this job only; the client's list stays.
    closeout = any(x is item for x in job.get("closeout_packet") or [])
    own = job.get("paperwork_labels")
    labels = [] if closeout or own is not None else parse_packet_text(payer.get("packet", ""))
    if not labels and _invoice and own is None:
        # No list saved: this pay app used the default one, so save what it asked for, less this.
        labels = [p["label"] for p in _invoice.get("packet") or [] if p is item or not p.get("skipped_reason")]
    kept = [l for l in labels if l.lower() != item["label"].lower()]
    if own is not None and not closeout and item["label"].lower() in {l.lower() for l in own}:
        # The job has its own list: it comes off that one, and the client's stays.
        job["paperwork_labels"] = [l for l in own if l.lower() != item["label"].lower()]
        audit(state, job_id, "job_paperwork", f"{actor} took \"{item['label']}\" off the supporting documents for {job['name']}.", actor)
    if len(kept) != len(labels):
        payer["packet"] = _packet_text(kept)
        if kept:
            payer.pop("no_paperwork", None)
        else:
            payer["no_paperwork"] = True  # an empty list would bring the default one back
        audit(state, "", "playbook_update", f"{actor} took \"{item['label']}\" off {payer['name']}'s supporting documents list.", actor)
    audit(state, job_id, "paperwork_not_needed", f"{actor} marked {item['label']} not needed: {item['skipped_reason']}.", actor)
    _refresh_draft_gate(job)
    return job


def _packet_text(labels: list[str]) -> str:
    """Labels back into a paperwork list, as a person would write it."""
    words = [l if i == 0 or l[1:2].isupper() else l[0].lower() + l[1:] for i, l in enumerate(labels)]
    return (", ".join(words) + ".") if words else ""


def _ask_for_a_look(item: dict[str, Any], problem: str) -> bool:
    """A waiver someone confirmed (in now, or with its file left out of the email for the moment)
    waits for another look, saying why. An override stays, and an item in from before these rules
    stays. Returns whether it asked."""
    if not ((item.get("present") or item.get("files_left_out")) and item.get("confirmed_by") and item.get("waiver_kind") and not item.get("override")):
        return False
    item["present"] = False
    item.pop("files_left_out", None)
    item.pop("confirmed_by")
    item.pop("confirmed_at", None)
    item.pop("resolved_at", None)
    for a in item.get("attachments", []):
        a["unconfirmed"] = True
    item["check"] = {"status": "flagged", "problems": [problem]}
    return True


def _look_again_at_waivers(packet: list[dict[str, Any]], before: int, now: int) -> None:
    """A waiver confirmed for one amount isn't confirmed for another: a redraft at a new amount
    asks for another look."""
    if before == now:
        return
    for item in packet:
        _ask_for_a_look(item, f"The amount changed from {money(before)} to {money(now)}. Look at it again.")


def _carry_packet(packet: list[dict[str, Any]], invoice_id: str) -> list[dict[str, Any]]:
    """A replaced draft's paperwork, as it stood, under the new pay app's ids."""
    items = deepcopy(packet)
    for item in items:
        item["id"] = f"pkt-{invoice_id[-6:]}-{item['kind']}"
    return items


def _packet_item(job_id: str, kind: str, label: str, blocker: str, required: bool = True) -> dict[str, Any]:
    return {
        "id": f"pkt-{job_id[-6:]}-{kind}",
        "kind": kind,
        "label": label,
        "required": required,
        "present": False,
        "blocker": blocker,
        "attachments": [],
    }


# importer.KINDS: what a record on the job can be.
# The job record's kinds; static/js/jobs.js RECORD_KINDS and importer.KINDS have the same keys
# with their words. supplier_invoice is never a bare "invoice", so it isn't read as a pay app.
RECORD_KINDS = ("note", "conversation", "daily", "photo", "contract", "change_order", "waiver", "pay_app", "ticket",
                "supplier_invoice", "insurance", "inspection", "stored", "payroll", "notarized", "punch", "completion", "asbuilt", "warranty", "other")


RECORD_NOTE_MAX = 5000


def edit_record(state: dict[str, Any], job_id: str, record_id: str, label: str, kind: str, actor: str = "owner",
                note: str | None = None) -> dict[str, Any]:
    """Fix a record's label or kind (a supplier invoice filed as a pay app, a photo read as a note) (R5-07),
    or its note (what a client said on a conversation). note None leaves it as it is. The audit row
    keeps what the note said. Paperwork it is attached to keeps it; the draft's paperwork check runs again."""
    job = _job(state, job_id)
    rec = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not rec:
        raise GateError("That record is not on this job.")
    label = (label or "").strip()[:120]
    if not label:
        raise GateError("A record needs a label.", fields={"label": "Enter what this is."})
    if kind not in RECORD_KINDS:
        raise GateError("Pick what kind of record this is.", fields={"kind": "Pick a kind."})
    before = (rec["label"], rec["kind"])
    rec["label"], rec["kind"] = label, kind
    if kind != "ticket":
        rec.pop("ticket_type", None)  # only a ticket is a delivery ticket or a time sheet (domain/daily.py)
    if before != (label, kind):
        audit(state, job_id, "edit_record", f"{actor} changed the record '{before[0]}' ({before[1]}) to '{label}' ({kind}).", actor)
    if note is not None:
        was, note = rec.get("note", ""), note.strip()[:RECORD_NOTE_MAX]
        if note != was:
            rec["note"] = note
            audit(state, job_id, "edit_record", f"{actor} changed the note on '{label}' from '{was[:500]}' to '{note[:500]}'.", actor)
    if not job.get("closed"):
        _refresh_draft_gate(job, state)
    return job


def add_job_record(
    state: dict[str, Any],
    job_id: str,
    label: str,
    kind: str = "note",
    note: str = "",
    actor: str = "owner",
) -> dict[str, Any]:
    job = _job(state, job_id)
    label = (label or "").strip()
    if not label:
        raise GateError("A record needs a label.")
    rec = {
        "id": "rec-" + secrets.token_hex(3),
        "kind": kind or "note",
        "label": label,
        "note": (note or "").strip(),
        "added_at": utcnow(),
        "added_by": actor,
    }
    job.setdefault("records", []).append(rec)
    if rec["kind"] == "conversation":   # however it was added, it does what Log a conversation does
        _log_conversation(state, job, rec["note"] or label, None, actor, rec["added_at"])
    else:
        audit(state, job_id, "add_record", f"{actor} added {kind} to the job's records: {label}.", actor)
    if not job.get("closed"):
        _refresh_draft_gate(job, state)
    return job


def attach_record_to_packet(
    state: dict[str, Any],
    job_id: str,
    item_id: str,
    record_id: str,
    actor: str = "owner",
    read_file: Any = None,
    looked: bool = False,
    answer: str = "",
    answerer: "Answerer | None" = None,
) -> dict[str, Any]:
    """Attaching and confirming are one step for the person doing it: they chose this record for
    this item, so it clears in their name, unless the document check has something to say, or it
    is a waiver item and nobody has said which waiver it is (answer, _clear_item). A record with no
    real file can't fill a document item, whatever it is called. Someone who can't answer
    (answerer) attaches the file all the same, and it waits for an answer."""
    job = _open_job(state, job_id)
    item, invoice = _find_packet_item(job, item_id)
    record = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if item and not _may_answer(answerer, _on_retainage_request(job, item=item)):
        answer = ""
    if not item:
        raise GateError("Supporting document not found.")
    if not record:
        raise GateError("Job record not found.")
    _still_a_draft(invoice)
    if not _can_fill(state, record, item):
        if record.get("file_ids"):
            raise GateError(f"{record['label']}: its file is empty or isn't a type that can go with {item['label']}. Add a PDF, a Word file or a photo.")
        raise GateError(f"{record['label']} has no file. {item['label']} needs the document itself, not a note.")
    _drop_other_suggestions(item, record_id)
    already = next((a for a in item.get("attachments", []) if a.get("record_id") == record_id), None)
    if already:
        # Suggested from the job record, or added as a photo taken for this item: it is the
        # person's choice now, so keep one attachment and make it theirs.
        already.pop("auto", None)
        already["added_by"] = actor
    else:
        item.setdefault("attachments", []).append({
            "name": record["label"],
            "record_id": record_id,
            "added_at": utcnow(),
            "added_by": actor,
        })
        which = invoice["number"] if invoice else "the job's supporting documents"
        audit(state, job_id, "attach_record", f"{actor} attached {record['label']} to {item['label']} on {which}.", actor)
    _clear_item(state, job, item, invoice, actor, read_file, looked, answer=answer, answerer=answerer)
    _refresh_draft_gate(job, state)
    return job


def _drop_other_suggestions(item: dict[str, Any], record_id: str) -> None:
    item["attachments"] = [a for a in item.get("attachments", []) if not a.get("auto") or a.get("record_id") == record_id]


def create_job(
    state: dict[str, Any],
    name: str,
    customer_name: str,
    contract_cents: int,
    address: str = "",
    approver: str = "",
    ap_email: str = "",
    phone: str = "",
    role: str = "",
    retainage_pct: int | None = 0,
    note: str = "",
    contract_number: str = "",
    actor: str = "owner",
    new_customer: bool = False,
    address_parts: dict[str, Any] | None = None,
    client_type: str = "",
    started_on: Any = None,
    assignee_id: str = "",
    assignee_name: str = "",
    payer_days: dict[str, Any] | None = None,
    auto_schedule: bool = False,
    schedule_lines: Any = None,
    schedule_op: Any = "",
    today: Any = "",
    anticipated_end_on: Any = None,
    approver_confirmed: bool = False,
    ask_same_job: bool = False,
) -> dict[str, Any]:
    """Add a job and its record. With ask_same_job (New job), an open job of the same name is asked
    about first (job_near), the way a near-match client is. The packet is created later, with a pay app, from the payer's
    paperwork list (Customer settings); `note` is kept on the job record and never becomes it.
    The client's playbook has to be complete (Decisions 3 and 4): a new client comes with its
    type, who signs off, email and phone; a saved client missing one takes it from here, and
    anything still missing is refused under its box. A customer name that nearly matches a saved
    one ("ABC Builders Inc" for "ABC Builders") is asked about first, unless new_customer says to
    keep it as a new customer.

    The address comes in four parts (J1: street, city, state, ZIP), each checked, and is kept
    whole as address too ("Street, City, ST 12345") for the pay app PDF, the client's page and
    Inbox matching. A caller that sends only the one line (an older page, an import) is still
    taken. Phone, contract number and names go through the field rules (G1). started_on is the
    start date (J8, today when blank: `today`, the page's own date, through local_today);
    assignee_id is the team member the job's tasks go to.
    payer_days (the pay app due day and payment terms an imported contract found, S12) are saved
    on a new client only: a saved client's are changed in the Client playbook.
    auto_schedule (what the New job route asks for) gives every new job a schedule of values: the
    schedule_lines the form sent, which must add up to the contract amount or nothing is saved
    (field "sov"), or else one lump-sum line for the contract amount."""
    name = (name or "").strip()
    customer_name = (customer_name or "").strip()
    address = (address or "").strip()
    approver = (approver or "").strip()
    ap_email = (ap_email or "").strip()
    phone = (phone or "").strip()
    role = client_type_role(client_type) or (role if role in {"Owner", "GC"} else "")
    note = (note or "").strip()
    contract_number = (contract_number or "").strip()

    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Enter the job name."
    elif check_field("name", name):
        errors["name"] = rule_message("name")
    parts = None
    if address_parts is not None:
        parts = {k: str(address_parts.get(k) or "").strip() for k in ("street", "city", "state", "zip")}
        errors.update(check_address(parts))
        parts["state"] = normalize_state(parts["state"]) or parts["state"]
        address = compose_address(parts)
    elif not address:
        errors["address"] = "Enter the job address."
    if not customer_name:
        errors["customer"] = "Enter the client: who pays for this job."
    elif check_field("name", customer_name):
        errors["customer"] = rule_message("name")
    if client_type and not client_type_role(client_type):
        errors["client_type"] = "Pick the client type."
    if phone:
        phone = normalize_phone(phone) or phone
        errors.update(check_field("phone", phone))
    errors.update(check_field("contract_number", contract_number))
    payer = _payer_named(state, customer_name) if customer_name else None
    near = payer_near(state, customer_name) if customer_name and not payer and not new_customer else None
    if not near:
        # What this client still needs: everything when new, only its empty boxes when saved.
        typed = {"approver": approver, "ap_email": ap_email, "phone": phone}
        wanted = playbook_missing(payer) if payer else ["client_type", *PLAYBOOK_FIELDS]
        errors.update({k: PLAYBOOK_FIELDS[k] for k in wanted if k in typed and not typed[k]})
        if payer and "approver" in errors and signer_defaulted(payer):
            errors["approver"] = SIGNER_IS_CLIENT
        # The client's own name typed for who signs off counts when the page says a person answered.
        if "approver" in wanted and approver and not signer_answered(approver, payer["name"] if payer else customer_name, approver_confirmed):
            errors["approver"] = SIGNER_IS_CLIENT
        if "client_type" in wanted and not role and "client_type" not in errors:
            errors["client_type"] = "Pick the client type."
    start = ""
    if started_on not in (None, ""):
        try:
            start = parse_start_date(started_on)
        except GateError as exc:
            errors.update(exc.fields)
    end = ""
    if anticipated_end_on not in (None, ""):
        try:
            end = parse_end_date(anticipated_end_on, start or local_today(today))
        except GateError as exc:
            errors.update(exc.fields)
    if ap_email and not EMAIL_RE.match(ap_email):
        errors["ap_email"] = PLAYBOOK_FIELDS["ap_email"]
    days: dict[str, Any] = {}
    for k in ("payapp_due_day", "payment_terms_days"):
        if (payer_days or {}).get(k) not in (None, ""):
            value, error = _payer_day(k, payer_days[k])
            if error:
                errors[k] = error
            else:
                days[k] = value
    if not isinstance(contract_cents, int) or isinstance(contract_cents, bool) or contract_cents <= 0:
        errors["contract_cents"] = "Enter the contract amount, more than $0."
    elif contract_cents > MAX_AMOUNT_CENTS:
        errors["contract_cents"] = f"Enter a contract amount up to {money(MAX_AMOUNT_CENTS)}."
    if retainage_pct is not None and (not isinstance(retainage_pct, int) or isinstance(retainage_pct, bool) or not 0 <= retainage_pct <= 20):
        errors["retainage_pct"] = "Enter a retainage from 0 to 20 percent."
    # ---- schedule of values at New job (Nov 3 Decision 1) ----
    schedule = None
    if auto_schedule and schedule_lines:
        schedule, why = _new_job_schedule(schedule_lines, schedule_op, contract_cents)
        if why:
            errors["sov"] = why
    # ---- end schedule of values ----
    _refuse("Job not saved.", errors)

    if near:
        raise _same_customer_question("customer", customer_name, near)
    same_job = job_near(state, name) if ask_same_job else None
    if same_job:
        raise GateError(f"You already have a job called {same_job['name']}.",
                        fields={"name": f"You already have a job called {same_job['name']}. Open it, or keep this as a new job."},
                        extra={"same_job": {"id": same_job["id"], "name": same_job["name"], "typed": name}})
    if not payer:
        payer = _new_payer(state, customer_name, role, approver, ap_email, phone, client_type, approver_confirmed)
        payer.update(days)
    else:
        missing = playbook_missing(payer)
        if "client_type" in missing:
            # A type never picked is answered here (checked above); after that it is the client's.
            payer["client_type"] = client_type if client_type_role(client_type) else ("gc" if role == "GC" else "owner")
            payer["role"] = role
            audit(state, "", "playbook_update", f"{actor} set the client type of {payer['name']}: {payer['client_type']}.", actor)
        # A saved client keeps its type: the job follows it (the form doesn't ask, J2).
        role = payer.get("role") if payer.get("role") in {"Owner", "GC"} else role
        # Keep the payer's existing contact. A new job must not silently redirect
        # where every other job's pay apps go; changes belong in Customer settings.
        # A blank one is filled in, since nothing goes anywhere yet.
        for k, value in (("approver", approver), ("ap_email", ap_email), ("phone", phone)):
            if value and k in missing:
                payer[k] = value
                audit(state, "", "playbook_update", f"{actor} added {PLAYBOOK_WORDS[k]} to {payer['name']}: {value}.", actor)
        if "approver" in missing:
            payer["approver_confirmed"] = True
        differs = [
            label
            for label, new, old in (
                ("who signs off", approver if approver != customer_name else "", payer.get("approver", "")),
                ("email for pay apps", ap_email, payer.get("ap_email", "")),
            )
            if new and old and new.lower() != str(old).lower()
        ]
        if differs:
            audit(
                state,
                "",
                "payer_kept",
                f"{actor} entered a different {' and '.join(differs)} for {payer['name']}; kept the saved ones. Change them in the Client playbook.",
                actor,
            )

    if retainage_pct is None:
        retainage_pct = default_retainage_pct(payer)   # none given: what the client usually holds (round 1, item 1)

    base = "".join(ch for ch in name.upper() if ch.isalnum())[:4] or "JOB"
    code = base
    taken = {j["code"] for j in state["jobs"]}
    n = 2
    while code in taken:
        code = f"{base}{n}"
        n += 1

    job_id = "job-" + secrets.token_hex(3)
    # The contract number lives on the job (it prints on every pay app); it is not a record.
    # Jobs made before this kept a "Contract <number>" record flagged number_only.
    records = []
    if note:
        records.append({
            "id": "rec-" + secrets.token_hex(3),
            "kind": "note",
            "label": "Job notes",
            "note": note,
            "added_at": utcnow(),
            "added_by": actor,
        })

    job = {
        "id": job_id,
        "code": code,
        "name": name,
        "address": address,
        "payer_id": payer["id"],
        "started_on": start or local_today(today),
        "payer_role": role,
        "work_status": "in_progress",
        "completed_at": None,
        "contract_cents": int(contract_cents),
        "contract_number": contract_number,
        "completed_value_cents": 0,
        "external_status": None,
        "waiver_due": False,
        "waiver_sent": False,
        "follow_ups": 0,
        "retainage_pct": int(retainage_pct),
        "retainage_held_cents": 0,
        "retainage_released_cents": 0,
        "retainage_chases": 0,
        "change_orders": [],
        "packet": [],
        "invoices": [],
        "payments": [],
        "records": records,
        "closed": False,
        "evidence": [],
        "done_notes": "",
        "playbook_required": True,  # made under the rule, so a pay app waits for a complete playbook
    }
    if end:
        job["anticipated_end_on"] = end
    if parts is not None:
        job["address_parts"] = parts
    if assignee_id:
        job["assignee_id"], job["assignee_name"] = assignee_id, (assignee_name or "").strip()
    # ---- schedule of values: every new job has one (Nov 3 Decision 1) ----
    if auto_schedule:
        job["schedule_required"] = True
        job["sov"] = schedule or {"op_bp": 0, "lines": [sov.auto_line(job["contract_cents"], "sov-" + secrets.token_hex(8))]}
    # ---- end schedule of values ----
    state["jobs"].append(job)
    audit(
        state,
        job["id"],
        "create_job",
        f"{actor} created {job['name']} for {payer['name']} ({money(job['contract_cents'])}). The supporting documents list comes with the first pay app.",
        actor,
    )
    return job



def _new_job_schedule(raw_lines: Any, op_pct: Any, contract_cents: Any) -> tuple[dict[str, Any] | None, str]:
    """The lines typed at New job as a schedule, or why they can't be saved."""
    try:
        lines = sov.clean_lines(raw_lines, [], lambda: "sov-" + secrets.token_hex(8))
        schedule = {"op_bp": sov.to_bp(op_pct), "lines": lines}
    except sov.SovError as exc:
        return None, str(exc)
    if not lines:
        return None, ""
    total = sov.totals({"sov": schedule})["contract_cents"]
    if isinstance(contract_cents, int) and total != contract_cents:
        diff = abs(total - contract_cents)
        return None, (f"The schedule adds up to {money(total)}, {money(diff)} {'more' if total > contract_cents else 'less'} than the "
                      f"{money(contract_cents)} contract. Change a line or the contract amount.")
    return schedule, ""


def edit_job(state: dict[str, Any], job_id: str, updates: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    """Fix a job's name, address or contract number at any time. The contract amount can change
    only before the first pay app, and only without a schedule of values (change that schedule
    instead); a job on its one automatic lump-sum line can change it until a pay app is sent, and
    the line and any open draft follow. Retainage can change any time and applies from the next pay app (R4-07): pay apps
    already sent keep what they held, and an open draft is drafted again at the new percent. The job code never changes: emails
    and texts are filed by it. Payer contacts are edited in the Payer Playbook.

    The address comes as its four parts (J1), all required once any is sent; a one-line address
    from an older page is still taken. The start date (J8) can be corrected even on a finished
    job, and each change is written to the activity log. assignee_id (with assignee_name, which
    the server looks up) is the team member the job's tasks go to."""
    job = _job(state, job_id)
    if job.get("closed") and any(k != "started_on" for k in updates):
        raise GateError(f"{job['name']} is closed. Closed jobs are read-only.")
    has_pay_apps = any(i.get("status") != "void" for i in job.get("invoices", []))
    changes: list[str] = []
    problems: dict[str, str] = {}

    for key, label in (("name", "Job name"), ("address", "Job address"), ("contract_number", "Contract number")):
        if key not in updates or (key == "address" and any(k in updates for k in ("street", "city", "state", "zip"))):
            continue
        value = str(updates[key] or "").strip()
        if value == str(job.get(key, "") or "").strip():
            continue  # left as it was: a value saved before the rules doesn't block other edits (C4)
        if key != "contract_number" and not value:
            problems[key] = f"Enter the {label.lower()}."
        elif key == "name" and check_field("name", value):
            problems[key] = rule_message("name")
        elif key == "contract_number" and check_field("contract_number", value):
            problems[key] = rule_message("contract_number")
        elif value != job.get(key, ""):
            changes.append(f"{label.lower()} from \"{job.get(key, '')}\" to \"{value}\"")
            job[key] = value
            if key == "address":
                job.pop("address_parts", None)   # one line again: the parts are read from it

    if any(k in updates for k in ("street", "city", "state", "zip")):
        parts = {k: str(updates.get(k) or "").strip() for k in ("street", "city", "state", "zip")}
        # The address is checked only when a box changed from what the form showed (the saved
        # parts, or an older one-line address read into them); then all four are needed (C4).
        was = job.get("address_parts") or parse_legacy_address(job.get("address", ""))
        same = all(parts[k] == str(was.get(k) or "").strip() for k in parts)
        bad = {} if same else check_address(parts)
        if bad:
            problems.update(bad)
        elif not same:
            parts["state"] = normalize_state(parts["state"]) or parts["state"]
            value = compose_address(parts)
            if value != job.get("address", "") or parts != job.get("address_parts"):
                if value != job.get("address", ""):
                    changes.append(f"job address from \"{job.get('address', '')}\" to \"{value}\"")
                job["address"], job["address_parts"] = value, parts

    if "started_on" in updates and str(updates["started_on"] or "").strip():
        try:
            start = parse_start_date(updates["started_on"])
        except GateError as exc:
            problems.update(exc.fields)
        else:
            was = job_started_on(state, job)
            if start != was:
                changes.append(f"start date from {was or 'none'} to {start}")
                job["started_on"] = start

    start_now = job_started_on(state, job)
    if "anticipated_end_on" in updates:
        typed = str(updates["anticipated_end_on"] or "").strip()
        was = job.get("anticipated_end_on") or ""
        try:
            end = parse_end_date(typed, start_now, "The job was not changed.") if typed else ""
        except GateError as exc:
            problems.update(exc.fields)
        else:
            if end != was:
                changes.append(f"anticipated end date from {was or 'none'} to {end or 'none'}")
                if end:
                    job["anticipated_end_on"] = end
                else:
                    job.pop("anticipated_end_on", None)
    # A start date moved past an end date left as it was.
    if "started_on" in updates and "anticipated_end_on" not in problems and start_now > (job.get("anticipated_end_on") or start_now):
        problems["started_on"] = f"Enter a start date on or before the anticipated end date, {job['anticipated_end_on']}."

    if "assignee_id" in updates and (updates["assignee_id"] or "") != job.get("assignee_id", ""):
        who = str(updates.get("assignee_name") or "").strip() or "no one"
        changes.append(f"assigned to {who}" + (f" (was {job['assignee_name']})" if job.get("assignee_name") else ""))
        if updates["assignee_id"]:
            job["assignee_id"], job["assignee_name"] = updates["assignee_id"], who
        else:
            job.pop("assignee_id", None)
            job.pop("assignee_name", None)

    redraft = None
    if "contract_cents" in updates and updates["contract_cents"] != job["contract_cents"]:
        cents = updates["contract_cents"]
        follows = _contract_follows(job)
        if has_pay_apps and not follows:
            problems["contract_cents"] = "Use a change order: the contract amount is fixed once a pay app exists."
        elif sov.has_sov(job) and not follows:
            problems["contract_cents"] = "Change the contract from the schedule of values: this job bills from it."
        elif not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
            problems["contract_cents"] = "Enter the contract amount, more than $0."
        elif cents > MAX_AMOUNT_CENTS:
            problems["contract_cents"] = f"Enter a contract amount up to {money(MAX_AMOUNT_CENTS)}."
        elif cents + _signed_cos(job) <= 0:
            problems["contract_cents"] = f"Signed change orders take {money(-_signed_cos(job))} off the contract. Enter more than that."
        else:
            changes.append(f"contract from {money(job['contract_cents'])} to {money(cents)}")
            job["contract_cents"] = cents
            if job.get("work_status") == "complete":
                # Marked done: the work done is the whole contract, so it moves with the new amount.
                job["completed_value_cents"] = cents + sum(
                    co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved"
                )
            if follows:
                sov.follow_contract(job, cents)
                job["completed_value_cents"] = sov.totals(job)["to_date_cents"]
                redraft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)

    if "retainage_pct" in updates and updates["retainage_pct"] != job.get("retainage_pct", 0):
        pct = updates["retainage_pct"]
        open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
        if not isinstance(pct, int) or isinstance(pct, bool) or not 0 <= pct <= 20:
            problems["retainage_pct"] = "Enter a retainage from 0 to 20 percent."
        elif open_draft and open_draft.get("pencil_sent_at"):
            problems["retainage_pct"] = (f"{open_draft['number']} went to the client as a draft at {job.get('retainage_pct', 0)}%. "
                                         "Delete that draft or send it first, then change the retainage.")
        else:
            changes.append(f"retainage from {job.get('retainage_pct', 0)}% to {pct}%" + (" from the next pay app" if has_pay_apps else ""))
            job["retainage_pct"] = pct
            redraft = open_draft

    _refuse("The job was not changed.", problems)
    if redraft:
        # The open draft is the next pay app: drafted again at the new percent, keeping its number and paperwork.
        redraft["status"] = "void"
        redraft["voided_at"] = utcnow()
        redraft["replaced"] = True
        changes.append(f"{redraft['number']} drafted again at {job['retainage_pct']}%"
                       + (f" and the new contract amount" if "contract_cents" in updates and sov.is_auto(job) else ""))
        draft_invoice(state, job_id, actor="duracash", replaces=redraft, period_end=redraft.get("period_to") or "")
    if changes:
        audit(state, job_id, "edit_job", f"{actor} changed {job['name']}: " + "; ".join(changes) + ".", actor)
    _refresh_draft_gate(job)   # a new contract amount can change whether a draft is the final one
    return job


def _signed_cos(job: dict[str, Any]) -> int:
    return sum(co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved")


def delete_job(state: dict[str, Any], job_id: str, actor: str = "owner") -> None:
    """Delete a job created by mistake. A job can go while nothing was billed on it: no
    payments, and no pay apps other than drafts deleted before they went out (R3-09);
    anything billed stays on the record and the job is finished instead."""
    job = _job(state, job_id)
    if not can_delete_job(job):
        raise GateError(f"{job['name']} has pay apps or payments, so it can't be deleted. Finish it instead.")
    drafts = len(job.get("invoices") or [])
    state["jobs"] = [j for j in state["jobs"] if j["id"] != job_id]
    state["daily_records"] = [r for r in state.get("daily_records", []) if r.get("job_id") != job_id]
    had = f"{drafts} deleted draft{'s' if drafts != 1 else ''}" if drafts else "no pay apps"
    audit(state, job_id, "delete_job", f"{actor} deleted {job['name']} ({job['code']}), which had {had}.", actor)


def can_delete_job(job: dict[str, Any]) -> bool:
    """No payments, and every pay app a draft deleted before it was sent."""
    return not job.get("payments") and all(i.get("status") == "void" and not i.get("sent_at") for i in job.get("invoices") or [])


# ---------- schedule of values ----------

def _co_billed_unsigned(job: dict[str, Any], co_id: str) -> bool:
    """A pay app that isn't void billed this change order whole, as its own line, before it was signed."""
    return any(entry.get("co_id") == co_id for inv in sov.live_invoices(job) for entry in inv.get("lines", []))


def _add_co_line(job: dict[str, Any], co: dict[str, Any]) -> None:
    """An approved change order becomes its own schedule line, like a CO line on a G703."""
    if any(line.get("co_id") == co["id"] for line in sov.lines(job)):
        return
    billed_already = _co_billed_unsigned(job, co["id"])
    job["sov"]["lines"].append({
        "id": f"sov-co-{co['number']}",
        "item": f"CO{co['number']}",
        "description": f"CO {co['number']} — {co['title']}",
        "unit": "LS",
        "qty_milli": 1000,
        "rate_cents": co["amount_cents"],
        "scheduled_cents": co["amount_cents"],
        # Billed while unsigned, or the job is done: the work is complete. A credit (R3-06) is
        # "done" when signed, so it comes off the next pay app.
        "done_milli": 1000 if billed_already or job.get("work_status") == "complete" or co["amount_cents"] < 0 else 0,
        "co_id": co["id"],
    })


def save_sov(
    state: dict[str, Any],
    job_id: str,
    raw_lines: Any,
    op_pct: Any = 0,
    update_contract: bool = False,
    actor: str = "owner",
    may_change_contract: bool = True,
) -> dict[str, Any]:
    """Set the job's schedule of values. Allowed until the first pay app is sent (Nov 3 Decision
    2): an open draft doesn't lock it, it is drafted again from the new schedule. A draft that is
    with the client for review has to be answered or deleted first. may_change_contract: whether
    this person may make the schedule's total the contract (manage_jobs); the refusal says who can."""
    job = _open_job(state, job_id)
    sent = [inv for inv in sov.live_invoices(job) if inv["status"] not in DRAFT_STATUSES]
    if sent:
        raise GateError(
            f"The schedule of values is fixed once a pay app has been sent ({sent[0]['number']}). "
            "Scope changes go through change orders, which add their own lines."
        )
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if _with_client(draft):
        raise GateError(f"The schedule is fixed while {draft['number']} is with the client for review. Record their answer or delete the draft to change it.")
    if job.get("work_status") == "complete":
        raise GateError("The job is marked done. A schedule of values is set before billing starts.")
    try:
        new_lines = sov.clean_lines(raw_lines, sov.lines(job), lambda: "sov-" + secrets.token_hex(8))
        bp = sov.to_bp(op_pct)
    except sov.SovError as exc:
        raise GateError("Schedule of values not saved.", [str(exc)])
    if not new_lines and job.get("schedule_required"):
        # A new job always has a schedule: taking every line off leaves the one lump-sum line.
        new_lines, bp = [sov.auto_line(job["contract_cents"], "sov-" + secrets.token_hex(8))], 0
    if not new_lines:
        if job.get("sov"):
            job.pop("sov", None)
            job["completed_value_cents"] = 0
            audit(state, job_id, "sov_removed", f"{actor} removed the schedule of values; progress is entered in dollars again.", actor)
            _draft_follows_schedule(state, job, draft)
        return job
    candidate = {"op_bp": bp, "lines": new_lines}
    figures = sov.totals({"sov": candidate})
    if figures["contract_cents"] > MAX_AMOUNT_CENTS:
        raise GateError(f"The schedule totals more than {money(MAX_AMOUNT_CENTS)}.")
    contract = job["contract_cents"]
    if figures["contract_cents"] != contract:
        if not update_contract:
            raise GateError(
                f"The schedule totals {money(figures['contract_cents'])}, but the contract is {money(contract)}.",
                [
                    f"Lines {money(figures['base_scheduled_cents'])} + overhead and profit {money(figures['op_scheduled_cents'])} = {money(figures['contract_cents'])}.",
                    f"Difference: {money(figures['contract_cents'] - contract)}.",
                    "Adjust a line or the overhead and profit, or save it and change the contract amount to the schedule total." if may_change_contract
                    else "Adjust a line or the overhead and profit. Changing the contract amount takes someone who can change a job's contract.",
                ],
            )
        if figures["contract_cents"] + _signed_cos(job) <= 0:
            raise GateError(f"Signed change orders take {money(-_signed_cos(job))} off the contract, so the schedule has to total more than that.")
        job["contract_cents"] = figures["contract_cents"]
        audit(
            state,
            job_id,
            "contract_changed",
            f"{actor} changed the contract amount from {money(contract)} to {money(figures['contract_cents'])} to match the schedule of values.",
            actor,
        )
    earlier = job.get("completed_value_cents", 0)
    kept = {line["id"] for line in new_lines}
    discarded = [f"{line['description']} {_qty_words(line, sov.field_total(line))} ({money(sov.line_to_date(sov.anticipated(job, line)))})"
                 for line in sov.lines(job) if not line.get("co_id") and line["id"] not in kept and sov.line_to_date(sov.anticipated(job, line)) > 0]
    # Change-order lines aren't edited here: they keep their progress and stored materials.
    co_lines = {l["co_id"]: l for l in sov.lines(job) if l.get("co_id")}
    job["sov"] = candidate
    for co in job["change_orders"]:
        if co["status"] == "approved":
            if co["id"] in co_lines:
                candidate["lines"].append(co_lines[co["id"]])
            else:
                _add_co_line(job, co)
    job["completed_value_cents"] = sov.totals(job)["to_date_cents"]
    audit(
        state,
        job_id,
        "sov_saved",
        f"{actor} saved a schedule of values: {len(new_lines)} lines, {money(figures['base_scheduled_cents'])}"
        + (f" plus {bp / 100:g}% overhead and profit ({money(figures['op_scheduled_cents'])})" if bp else "")
        + "."
        + (f" Recorded on lines replaced, and discarded: {'; '.join(discarded)}." if discarded else
           f" The earlier completed-to-date of {money(earlier)} is replaced by quantities." if earlier and not job["completed_value_cents"] else ""),
        actor,
    )
    _draft_follows_schedule(state, job, draft)
    return job


def _draft_follows_schedule(state: dict[str, Any], job: dict[str, Any], draft: dict[str, Any] | None) -> None:
    """A schedule was saved on a job with an open draft: the draft is drafted again from it, keeping
    its number and paperwork. With nothing done on the new lines yet there is nothing to bill, so
    the draft comes off and the next one picks up its number and paperwork (draft_invoice)."""
    if not draft:
        return
    draft["status"] = "void"
    draft["voided_at"] = utcnow()
    draft["replaced"] = True
    audit(state, job["id"], "draft_replaced", f"{draft['number']} ({money(draft['amount_cents'])}) replaced from the new schedule of values.", "duracash")
    draft_invoice(state, job["id"], actor="duracash", replaces=draft, period_end=draft.get("period_to") or "")
    if not any(inv["status"] in DRAFT_STATUSES for inv in job["invoices"]):
        job["sov_replaced_draft"] = draft["id"]
        audit(state, job["id"], "draft_replaced", f"The new schedule has no quantities done yet, so {draft['number']} comes back when they are entered, with its number and supporting documents.", "duracash")


def record_sov_progress(
    state: dict[str, Any],
    job_id: str,
    done: Any,
    notes: str = "",
    actor: str = "owner",
    stored: Any = None,
    period_end: Any = "",
    keep_amounts: bool = False,
) -> dict[str, Any]:
    """Record quantities completed to date, and materials stored, on schedule lines; draft the
    progress pay app. `done` maps line id -> quantity to date; `stored` maps line id -> dollars of
    materials on site and not yet installed (G703 column F). period_end: the date it runs to (R4-04).
    The period's pay app is one draft that keeps up (M1): while it is only here, new progress
    replaces it, keeping its number, paperwork and notes, the way record_progress does for dollar
    jobs. Once it is with the client for review it has to be answered or voided first.
    This is the only way job records reach a draft (Nov 3): a line not in `done`, or blank in it,
    is at its suggestion (its field total, or what is billed when that is more), and a figure typed that differs from the field total is the person's own, audited
    with the suggested and final figures. It leaves the field total as it was. A quantity typed on a
    line whose amount was changed by hand on the open draft is the newer entry: the amount gives way
    to it, and the audit says so (Round 1 hunt), and so does a change to its stored materials; a
    line sent at the quantity and stored materials it stands at keeps its amount. keep_amounts: the dollar form, which sets them next."""
    job = _open_job(state, job_id)
    if not sov.has_sov(job):
        raise GateError("This job has no schedule of values. Enter completed-to-date in dollars, or add a schedule first.")
    if job.get("work_status") == "complete":
        raise GateError("Job is already marked done. Draft the remaining pay app instead.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft and open_draft.get("pencil_status") == "sent":
        raise GateError(f"{open_draft['number']} is with the client as a draft. Void it or record their answer first.")
    if not open_draft:
        _need_playbook(state, job)
    runs_to = period_to(job, period_end)
    stored = stored if isinstance(stored, dict) else {}
    # The open draft is about to be replaced: it doesn't count as billed while the new figures
    # are checked. Put back if they are refused.
    draft_status = open_draft["status"] if open_draft else ""
    if open_draft:
        open_draft["status"] = "void"
    # A blank box and a line left out both mean the suggestion: what the card shows and suggests.
    done = {k: v for k, v in done.items() if not (v is None or (isinstance(v, str) and not v.strip()))} if isinstance(done, dict) else {}
    by_hand = dict((open_draft or {}).get("sov_overrides") or {})
    # Only a line whose quantity or stored materials changed from the draft's: the form sends every box (Round 1 hunt).
    giving_way = [] if keep_amounts else [line for line in sov.lines(job) if line["id"] in by_hand
                                          and ((line["id"] in done and _qty_changed(line, done[line["id"]])) or _stored_changed(line, stored))]
    billed_qty = sov.billed(job)["qty_by_line"]
    for line in sov.lines(job):
        want = sov.suggested_done(line, billed_qty.get(line["id"], 0))
        if line["id"] not in done and want != line["done_milli"]:
            done[line["id"]] = f"{want // 1000}.{want % 1000:03d}"
    try:
        changes, rebill = _check_sov_progress(job, done, stored, replacing=open_draft, amounts_go=bool(giving_way))
    except GateError:
        if open_draft:
            open_draft["status"] = draft_status
        raise
    for line in giving_way:
        by_hand.pop(line["id"], None)
        qty_changed = line["id"] in done and _qty_changed(line, done[line["id"]])
        entered = (f"entered {_qty_words(line, sov.to_milli(done[line['id']], sov_line_name(line)))}" if qty_changed
                   else f"changed the stored materials to {money(sov.to_cents(stored[line['id']], 'stored materials'))}")
        audit(state, job_id, "payapp_amount_changed", f"{actor} {entered} on "
              f"{sov_line_name(line)}: the {money(open_draft['sov_overrides'][line['id']])} changed by hand gives way to it on {open_draft['number']}.", actor)
    if giving_way:
        open_draft["sov_overrides"] = by_hand
    summary = []
    for line, qty, store in changes:
        part = f"{line['description']} {sov.fmt_qty(line['done_milli'])} → {sov.fmt_qty(qty)} {line['unit']}"
        if store != int(line.get("stored_cents") or 0):
            part += f", stored {money(int(line.get('stored_cents') or 0))} → {money(store)}"
        summary.append(part)
        if "field_milli" in line and qty != line["field_milli"]:
            audit(state, job_id, "payapp_amount_changed", f"{actor} changed the pay app quantity, {sov_line_name(line)}: "
                  f"suggested {sov.fmt_qty(line['field_milli'])} {line['unit']} ({money(sov.extend(line['field_milli'], line['rate_cents']))}), "
                  f"final {sov.fmt_qty(qty)} {line['unit']} ({money(sov.extend(qty, line['rate_cents']))}).", actor)
            line.pop("from_daily_on", None)
        elif "field_milli" not in line and qty != line["done_milli"]:
            line.pop("from_daily_on", None)
        line["done_milli"] = qty
        line["stored_cents"] = store
    job["completed_value_cents"] = sov.totals(job)["to_date_cents"]
    notes = (notes or "").strip()[:2000]
    audit(
        state,
        job_id,
        "record_progress",
        (f"{actor} recorded progress to date: {'; '.join(summary)}." if summary
         else f"{actor} billed the work recorded before, unchanged.")
        + f" Completed and stored to date {money(job['completed_value_cents'])}."
        + (f" {notes}" if notes else ""),
        actor,
    )
    if open_draft:
        open_draft["voided_at"] = utcnow()
        open_draft["replaced"] = True
        audit(state, job_id, "draft_replaced", f"{open_draft['number']} ({money(open_draft['amount_cents'])}) replaced at the new quantities to date.", "duracash")
        return draft_invoice(state, job_id, actor="duracash", replaces=open_draft, period_end=runs_to, notes=notes)
    return draft_invoice(state, job_id, actor="duracash", period_end=runs_to, notes=notes)


def done_for_amounts(state: dict[str, Any], job_id: str, done: Any, amounts: Any) -> dict[str, Any]:
    """The quantities to go with "Bill by dollars" (Round 1 hunt): the page sends the line's % done
    rounded down to a tenth of a percent beside the dollars. A draw smaller than that step would
    round to no progress at all, so the line steps up to the next tenth that holds the dollars; the
    pay app then bills the dollars exactly (set_payapp_amounts)."""
    job = _job(state, job_id)
    out = dict(done) if isinstance(done, dict) else {}
    if not isinstance(amounts, dict):
        return out
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    already = sov.billed(job, skip=draft)
    for line in sov.lines(job):
        raw = amounts.get(line["id"])
        if raw is None or (isinstance(raw, str) and not raw.strip()) or line["scheduled_cents"] <= 0:
            continue
        try:
            cents = sov.to_cents(raw, sov_line_name(line))
            typed = sov.to_milli(out[line["id"]], sov_line_name(line)) if line["id"] in out else None
        except sov.SovError:
            continue
        before = already["qty_by_line"].get(line["id"], 0)
        if cents <= 0 or (typed is not None and typed > before):
            continue
        to_date = already["by_line"].get(line["id"], 0) + cents
        need = min(line["qty_milli"], max(before + 1, -(-to_date * line["qty_milli"] // line["scheduled_cents"])))
        out[line["id"]] = f"{need // 1000}.{need % 1000:03d}"
    return out


def drafted_at(state: dict[str, Any], job_id: str, done: Any) -> bool:
    """The open draft already stands at these quantities, and it isn't with the client: only its
    amounts can change (a new dollar amount within the same tenth of a percent)."""
    job = _job(state, job_id)
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if not draft or _with_client(draft) or not isinstance(done, dict) or not done:
        return False
    by_id = {line["id"]: line for line in sov.lines(job)}
    try:
        return all(k in by_id and sov.to_milli(v, sov_line_name(by_id[k])) == by_id[k]["done_milli"]
                   for k, v in done.items() if not (v is None or (isinstance(v, str) and not v.strip())))
    except sov.SovError:
        return False


def _stored_changed(line: dict[str, Any], stored: dict[str, Any]) -> bool:
    """Stored materials sent for a line differ from the open draft's. A blank box is no change; one
    that can't be read counts as changed, as for a quantity."""
    value = stored.get(line["id"])
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    try:
        return sov.to_cents(value, "stored materials") != int(line.get("stored_cents") or 0)
    except sov.SovError:
        return True


def _qty_changed(line: dict[str, Any], value: Any) -> bool:
    """A figure sent for a line differs from the quantity the open draft stands at. One that can't
    be read counts as changed: the check after says what is wrong with it."""
    try:
        return sov.to_milli(value, sov_line_name(line)) != line["done_milli"]
    except sov.SovError:
        return True


def _qty_words(line: dict[str, Any], milli: int) -> str:
    """A quantity as the line is measured: a lump sum in %, anything else in its unit."""
    if str(line.get("unit", "")).upper() == "LS" and line.get("qty_milli") == 1000:
        return f"{sov.fmt_qty(milli * 100)}%"
    return f"{sov.fmt_qty(milli)} {line['unit']}".strip()


def _field_totals_change(state: dict[str, Any], job: dict[str, Any], changes: list[tuple[dict[str, Any], int]]) -> str:
    """Set the field totals, (line, new total) each. What the card suggests follows; a draft drafted
    at the old field total follows too, unless it is with the client, and a figure typed on a draft
    stays until the person changes it. Returns what became of the draft, in words for the audit."""
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    billed_qty = sov.billed(job, skip=draft)["qty_by_line"]
    on_draft = {e["line_id"]: e for e in (draft or {}).get("sov_sheet") or []}
    follows = []
    for line, new in changes:
        # As the progress form reads it: typed when it differed from the field on the day it was drafted.
        at_draft = on_draft.get(line["id"], {}).get("field_milli_at_draft", sov.field_total(line))
        if line["done_milli"] == at_draft:
            follows.append(line)
        line["field_milli"] = new
        line.pop("from_daily_on", None)
    for line, _new in changes:
        if not draft:
            line["done_milli"] = sov.suggested_done(line, billed_qty.get(line["id"], 0))
    job["completed_value_cents"] = sov.totals({"sov": {"op_bp": sov.op_bp(job), "lines": [sov.anticipated(job, l) for l in sov.lines(job)]}})["to_date_cents"]
    if not draft:
        return ""
    if _with_client(draft):
        return f" {draft['number']} is with the client and still bills what they have."
    if not follows:
        _refresh_draft_gate(job, state)
        return f" {draft['number']} keeps the quantity typed on it."
    for line in follows:
        line["done_milli"] = sov.suggested_done(line, billed_qty.get(line["id"], 0))
    _redraft(state, job, draft, "at the corrected field total")
    return "" if any(inv["status"] in DRAFT_STATUSES for inv in job["invoices"]) else f" {draft['number']} came off: nothing is left to bill."


def correct_field_total(state: dict[str, Any], job_id: str, line_id: str, qty: Any, reason: Any, actor: str = "owner",
                        below_billed: bool = False) -> dict[str, Any]:
    """Change what the field has recorded on a line: a wrong quantity on a job record or an imported
    file. It takes a reason, and the audit keeps the old and new totals. Sent pay apps don't change;
    going below what they billed has to be said (below_billed)."""
    from domain.daily import quantities_blocked
    job = _open_job(state, job_id)
    blocked = quantities_blocked(job)
    if blocked:
        raise GateError(blocked)
    line = next((l for l in sov.lines(job) if l["id"] == line_id), None)
    if not line:
        _refuse("", {"line_id": "Pick a line of this job's schedule of values."})
    name = sov_line_name(line)
    if line["scheduled_cents"] < 0:
        raise GateError(f"{name}: a credit has no quantities to correct.")
    reason = _reason(reason)
    errors = {} if reason else {"reason": "Enter why the total is wrong, like the import had 90 and 9 was meant."}
    new = 0
    try:
        new = sov.to_milli(qty, name)
    except sov.SovError as exc:
        errors["qty"] = str(exc)
    else:
        if new < 0:
            errors["qty"] = f"{name}: the total can't be negative."
        elif new > line["qty_milli"]:
            errors["qty"] = over_schedule_text(line, new)
        elif sov.line_completed({**line, "done_milli": new}) + int(line.get("stored_cents") or 0) > line["scheduled_cents"]:
            errors["qty"] = f"{name}: work done plus the materials stored is more than the line's scheduled value. Lower the stored materials first."
    _refuse("Field total not corrected.", errors)
    old = sov.field_total(line)
    if new == old:
        _refuse("", {"qty": f"{name} is already at {_qty_words(line, old)}."})
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    sent_qty = sov.billed(job, skip=draft)["qty_by_line"].get(line_id, 0)
    if new < sent_qty and not below_billed:
        raise GateError(f"{name}: {_qty_words(line, sent_qty)} is already billed. Pay apps sent stay as they are, and nothing more is suggested until the field total passes that. Confirm to go below it.",
                        extra={"below_billed": True})
    what = _field_totals_change(state, job, [(line, new)])
    audit(state, job_id, "field_total_corrected",
          f"{actor} corrected the field total, {name}: {_qty_words(line, old)} to {_qty_words(line, new)}. {reason}."
          + (f" {_qty_words(line, sent_qty)} is already billed and stays billed." if new < sent_qty else "") + what, actor)
    return job


def undo_quantity_import(state: dict[str, Any], job_id: str, record_id: str, actor: str = "owner", below_billed: bool = False) -> dict[str, Any]:
    """Take off what an imported quantity file added (qty_import.apply), line by line, down to zero
    at most. The record stays, saying so. Going below what sent pay apps billed has to be said."""
    from domain.daily import quantities_blocked
    job = _open_job(state, job_id)
    blocked = quantities_blocked(job)
    if blocked:
        raise GateError(blocked)
    record = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not record:
        raise GateError("Job record not found.")
    rows = record.get("quantities") or []
    if not rows:
        raise GateError("That record added no quantities that can be taken off." if not record.get("quantities_undone") else "That import was already undone.")
    by_id = {line["id"]: line for line in sov.lines(job)}
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    sent = sov.billed(job, skip=draft)["qty_by_line"]
    changes, words, gone, below = [], [], [], []
    for row in rows:
        line = by_id.get(row["line_id"])
        if not line:
            gone.append(row["line_id"])
            continue
        old = sov.field_total(line)
        new = max(0, old - row["qty_milli"])
        changes.append((line, new))
        words.append(f"{sov_line_name(line)}: {_qty_words(line, old)} to {_qty_words(line, new)}")
        if new < sent.get(line["id"], 0):
            below.append(f"{sov_line_name(line)}: {_qty_words(line, sent[line['id']])} is already billed")
    if below and not below_billed:
        raise GateError("Pay apps sent stay as they are, and nothing more is suggested until the field total passes what they billed. Confirm to undo it.",
                        below, extra={"below_billed": True})
    what = _field_totals_change(state, job, changes)
    now = utcnow()
    record["quantities_undone"] = {"by": actor, "at": now, "rows": rows}
    record.pop("quantities")
    audit(state, job_id, "quantities_import_undone",
          f"{actor} undid the quantities from {record['label']}: {'; '.join(words) or 'nothing to take off'}."
          + (f" {len(gone)} line{'' if len(gone) == 1 else 's'} no longer on the schedule." if gone else "")
          + (f" Below what is billed: {'; '.join(below)}." if below else "") + what, actor)
    return job


def _holds_paid_retainage(job: dict[str, Any], invoice: dict[str, Any], m: dict[str, int] | None = None) -> bool:
    """Retainage the client paid must still have a pay app holding it, so a pay app whose retainage
    is part of what was paid stays. A credit off the retainage moved no money and only stands while
    something is held (job_money), so it doesn't hold a pay app."""
    m = m or job_money(job)
    return m["retainage_released_cents"] > m["retainage_held_cents"] - int(invoice.get("retainage_cents") or 0)


def _overbilling_way_out(job: dict[str, Any], m: dict[str, int]) -> str:
    """What can settle the overbilling when no refund can: the words for what works on this job now."""
    open_ones = [inv for inv in job["invoices"] if inv["status"] in OPEN_FOR_PAYMENT]
    part_paid = next((inv for inv in open_ones if any(p.get("invoice_id") == inv["id"] for p in job["payments"])), None)
    unpaid = next((inv for inv in reversed(open_ones) if not any(p.get("invoice_id") == inv["id"] for p in job["payments"]) and not _holds_paid_retainage(job, inv, m)), None)
    ways = []
    if retainage_credit_room(job, m):
        ways.append(f"take the signed credit off the {money(m['retainage_open_cents'])} retainage held")
    if part_paid:
        ways.append(f"accept what is left on {part_paid['number']} as short pay")
    if unpaid:
        ways.append(f"pull back {unpaid['number']} and draft it again")
    return (" Or ".join(w[0].upper() + w[1:] for w in ways) + ".") if ways else "Sign a change order for the difference."


def settle_overbilling(state: dict[str, Any], job_id: str, reason: Any, actor: str = "owner") -> dict[str, Any]:
    """The client paid past the contract and got the difference back outside Duracash (a refund, or
    a credit on other work): recorded with what was done, so the job can be finished. returned_cents
    is the money that went back, which comes off what the job has received: what covers only a short
    pay accepted (money that never came in) is a credit and moved nothing."""
    job = _open_job(state, job_id)
    m = job_money(job)
    if not m["overbilled_cents"]:
        raise GateError("This job isn't overbilled.")
    cents = m["overbilling_settleable_cents"]
    if not cents:
        received = m["payments_cents"] + m["short_paid_cents"] + m["retainage_released_cents"]
        last = next((inv for inv in reversed(job["invoices"]) if inv["status"] in BILLED_STATUSES), None)
        way_out = _overbilling_way_out(job, m)
        if not received:
            raise GateError(f"Nothing is paid on {(last or {}).get('number', 'this job')}, so there is nothing to refund. Pull it back and draft it again.")
        if m["overbilling_settled_cents"]:
            raise GateError(f"The {money(m['overbilling_settled_cents'])} paid above the contract total of {money(m['billing_allowed_cents'])} is already recorded as refunded or credited. {way_out}")
        raise GateError(f"{money(received)} is paid, which is not above the contract total of {money(m['billing_allowed_cents'])}, so there is nothing to refund. {way_out}")
    reason = _reason(reason)
    if not reason:
        _refuse("", {"reason": "Enter what was done, like refund check 1044."})
    money_above = max(0, m["payments_cents"] + m["retainage_released_cents"] - m["billing_allowed_cents"] - m["refunded_cents"])
    job.setdefault("overbilling_settled", []).append({"cents": cents, "returned_cents": min(cents, money_above), "by": actor, "at": utcnow(), "reason": reason})
    audit(state, job_id, "overbilling_settled", f"{actor} recorded {money(cents)} of overbilling as refunded or credited: {reason}."
          + (f" {money(m['overbilled_cents'] - cents)} stays overbilled: that much more was not collected." if cents < m["overbilled_cents"] else ""), actor)
    return job


def apply_credit_to_retainage(state: dict[str, Any], job_id: str, reason: Any, actor: str = "owner", amount_cents: Any = None) -> dict[str, Any]:
    """A signed credit taken out of the retainage held: the client doesn't pay that part of it, and
    nothing was overpaid to refund. It lowers what is held, with no money recorded and no waiver
    owed for that part. amount_cents blank is all that can go."""
    job = _open_job(state, job_id)
    m = job_money(job)
    room = retainage_credit_room(job, m)
    if not room:
        if not m["retainage_open_cents"]:
            raise GateError("No retainage is held to take a credit off.")
        if not m["overbilled_cents"]:
            raise GateError("Nothing is overbilled, so there is no credit to take off the retainage.")
        raise GateError("No signed credit is left to take off the retainage.")
    if amount_cents in (None, ""):
        amount_cents = room
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        _refuse("", {"amount_cents": "Enter the amount, more than $0."})
    if amount_cents > room:
        _refuse("", {"amount_cents": f"Enter no more than {money(room)}. You entered {money(amount_cents)}."})
    reason = _reason(reason)
    if not reason:
        _refuse("", {"reason": "Enter what was agreed, like CO 3's credit comes off retainage."})
    now = utcnow()
    job.setdefault("retainage_credits", []).append({"cents": amount_cents, "by": actor, "at": now, "reason": reason})
    # The last of what was held gone with nothing else open finishes the job, and no release is left
    # to carry its final waiver: it comes due here, unless one went out already.
    # A final waiver already due and not sent (the paid pay app's own, say) covers it too (Nov 6 review, B).
    owes_final = suggested_waiver_kind(job).endswith("_final") and not any(
        x.get("waiver_sent") and str(x.get("waiver_kind") or x.get("kind") or "").endswith("_final") for x in job["invoices"] + job.get("retainage_waivers", [])) \
        and not any((release_due_kind(job, x) if "reference" in x else suggested_waiver_kind(job, x)).endswith("_final") for x in _pending_waivers(job))
    due = False
    if owes_final:
        waivers = job.setdefault("retainage_waivers", [])
        release = {"id": f"rw-{len(waivers) + 1}", "amount_cents": amount_cents, "reference": "Signed credit", "at": now, "kind": "unconditional_final", "credit": True}
        waivers.append(release)
        due = _waiver_comes_due(state, job, release, "the signed credit that finishes the job")
    _sync_waiver(job)
    _refresh_draft_gate(job, state)
    audit(state, job_id, "retainage_credited",
          f"{actor} took {money(amount_cents)} of signed credit off the retainage held: {reason}. No money was received."
          + (" It finishes the job, so the final lien waiver is due." if due else " No waiver is owed for it."), actor)
    return job


def _settlements_follow(state: dict[str, Any], job: dict[str, Any], why: str) -> None:
    """The billed total changed: a refund or credit recorded stays only for money that came in. What
    no longer counts comes off (old data, settled on a pay app nobody paid), and the audit says so."""
    entries = job.get("overbilling_settled") or []
    recorded = sum(int(s["cents"]) for s in entries)
    keep = job_money(job)["overbilling_settled_cents"]
    if recorded <= keep:
        return
    kept = []
    for entry in entries:
        take = min(int(entry["cents"]), keep)
        keep -= take
        if take:
            kept.append({**entry, "cents": take, "returned_cents": min(take, int(entry.get("returned_cents", take)))})
    job["overbilling_settled"] = kept
    audit(state, job["id"], "overbilling_settled_dropped",
          f"{why}: {money(recorded - sum(s['cents'] for s in kept))} recorded as refunded or credited no longer counts, since that much never came in.", "duracash")


def _check_sov_progress(job: dict[str, Any], done: dict[str, Any], stored: dict[str, Any],
                        replacing: dict[str, Any] | None = None, amounts_go: bool = False) -> tuple[list[tuple[dict[str, Any], int, int]], bool]:
    """The lines whose quantity to date or stored materials change, each (line, quantity, stored),
    checked against the schedule and what is already billed; and whether work recorded earlier and
    not on a pay app is drafted again unchanged. Raises GateError with every problem."""
    # Work recorded and not on a pay app (one pulled back, or a draft deleted): the same
    # quantities draft it again. A draft being replaced is not that: it needs a change.
    rebill = not replacing and job_money(job)["unbilled_completed_cents"] > 0
    if not done and not stored and not rebill:
        raise GateError("Enter the quantity completed to date on at least one line.")
    by_id = {line["id"]: line for line in sov.lines(job)}
    already = sov.billed(job)
    billed_qty = already["qty_by_line"]
    changes, errors = [], []
    blank = lambda v: v is None or (isinstance(v, str) and not v.strip())
    for line_id in list(dict.fromkeys(list(done) + list(stored))):
        line = by_id.get(line_id)
        if not line:
            errors.append(f"Line {str(line_id)[:20]} is not on this job's schedule.")
            continue
        name = sov_line_name(line)
        qty, store = line["done_milli"], int(line.get("stored_cents") or 0)
        try:
            if not blank(done.get(line_id)):
                qty = sov.to_milli(done[line_id], name)
            if not blank(stored.get(line_id)):
                store = sov.to_cents(stored[line_id], f"{name}: stored materials")
        except sov.SovError as exc:
            errors.append(str(exc))
            continue
        trial_line = {**line, "done_milli": qty, "stored_cents": store}
        if qty < 0 or store < 0:
            errors.append(f"{name}: quantity and stored materials can't be negative.")
        elif qty > line["qty_milli"]:
            errors.append(over_schedule_text(line, qty))
        elif qty < billed_qty.get(line_id, 0):
            errors.append(f"{name}: {sov.fmt_qty(billed_qty[line_id])} {line['unit']} is already billed; the quantity to date can't go below that.")
        elif sov.line_completed(trial_line) + store > line["scheduled_cents"]:
            errors.append(f"{name}: work done plus {money(store)} stored is more than the line's {money(line['scheduled_cents'])}. Lower the stored materials as they are installed.")
        elif line["scheduled_cents"] >= 0 and sov.line_to_date(trial_line) < min(already["by_line"].get(line_id, 0), sov.line_to_date(line)):
            # A credit (a signed change order for less) goes further below nothing as its work is done.
            errors.append(f"{name}: {money(already['by_line'].get(line_id, 0))} is already billed on this line; work done plus stored materials can't go below that.")
        elif qty != line["done_milli"] or store != int(line.get("stored_cents") or 0):
            changes.append((line, qty, store))
    if errors:
        raise GateError("Progress not recorded.", errors)
    if not changes and replacing and not amounts_go:
        raise GateError(f"{replacing['number']} is already drafted at those quantities. Enter the corrected quantities to date.")
    if not changes and not rebill and not amounts_go:
        raise GateError("Nothing changed. Enter quantities to date or stored materials that differ from the last ones recorded.")
    return changes, rebill


def sov_line_name(line: dict[str, Any]) -> str:
    """"1. Rough-in wiring": how a refusal names a schedule line."""
    return f"{line.get('item', '')}. {line['description']}".strip(". ")


def over_schedule_text(line: dict[str, Any], qty: int) -> str:
    """The refusal for a quantity to date past the line's scheduled quantity (one wording for the
    job page and the daily record)."""
    if str(line.get("unit", "")).upper() == "LS" and line.get("qty_milli") == 1000:
        # A lump sum is entered and shown as % done (B5 of the Oct 30 review), so it is refused in %.
        return f"{sov_line_name(line)}: {sov.fmt_qty(qty * 100)}% done is more than 100%. Extra scope is a change order."
    return (f"{sov_line_name(line)}: {sov.fmt_qty(qty)} is more than the scheduled {sov.fmt_qty(line['qty_milli'])} {line['unit']}. "
            "Extra scope is a change order.")


def over_schedule_note(line: dict[str, Any], extra: int) -> str:
    """What didn't fit on a line, for a job record: the part past the scheduled quantity."""
    if str(line.get("unit", "")).upper() == "LS" and line.get("qty_milli") == 1000:
        return f"{sov_line_name(line)}: {sov.fmt_qty(extra * 100)}% is over 100% and wasn't added. Extra scope is a change order."
    return f"{sov_line_name(line)}: {sov.fmt_qty(extra)} {line['unit']} is over the schedule and wasn't added. Extra scope is a change order."


def pay_app_text(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> str:
    """The pay app as plain text, for the email body and the pencil copy."""
    out = [f"{'FINAL APPLICATION ' if invoice.get('final') else ''}{invoice['number']} — {job['name']}", job.get("address", "")]
    if invoice.get("period_to"):
        out.append(f"Period to {invoice['period_to']}")
    out.append("")
    for line in invoice.get("lines", []):
        out.append(f"  {line['label']}: {money(line['amount_cents'])}")
    out.append(f"  Amount due this application: {money(invoice['amount_cents'])}")
    sheet = sov.continuation_sheet(job, invoice)
    if sheet:
        out += ["", "Schedule of values (continuation sheet, dollars):", sheet]
    packet = [p for p in invoice.get("packet") or [] if p.get("required")]
    if packet:
        out += ["", "Supporting documents:"]
        # An overridden item has no checked file behind it: no tick, and its reason stays inside.
        out += [f"  Not attached: {p['label']}" if p.get("override") else f"  [{'x' if p.get('present') and not sov.name_only_open(job, invoice, p) else ' '}] {p['label']}" for p in packet]
    return "\n".join(out).strip()


def _packet_file_ids(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> list[str]:
    """Stored files the email carries (M4): those behind the records attached to this pay app's
    paperwork, then those of the records added to the send, less the files left out."""
    records = {r["id"]: r for r in job.get("records", [])}
    record_ids = [att.get("record_id") for item in invoice.get("packet") or [] for att in _going(item)]
    record_ids += list(invoice.get("extra_record_ids") or [])
    excluded = set(invoice.get("excluded_file_ids") or [])
    ids: list[str] = []
    for rid in record_ids:
        for file_id in (records.get(rid) or {}).get("file_ids", []):
            if file_id not in ids and file_id not in excluded:
                ids.append(file_id)
    return ids


PAYAPP_ATTACHMENT_ACTIONS = ("add_record", "remove_record", "exclude_file", "include_file")


def change_payapp_attachments(state: dict[str, Any], job_id: str, invoice_id: str, action: str, target: str,
                              actor: str = "owner", read_file: Any = None) -> dict[str, Any]:
    """Adjust what a draft pay app's email carries (M4): add a job record that is not a paperwork
    item (an extra photo, a supplier invoice), take one back off, or leave one file out and put it
    back. Only before it is sent. Leaving out the last file behind a paperwork item makes that
    item missing again, so the send still waits for it."""
    job = _open_job(state, job_id)
    invoice = next((inv for inv in job["invoices"] if inv["id"] == invoice_id), None)
    if not invoice:
        raise GateError("Pay app not found.")
    if invoice["status"] not in DRAFT_STATUSES:
        raise GateError(f"{invoice['number']} has gone to the client, so what went with it stays as it was sent.")
    if action not in PAYAPP_ATTACHMENT_ACTIONS:
        raise GateError("Pick what to change: add or remove a record, leave out or put back a file.")
    records = {r["id"]: r for r in job.get("records", [])}
    extra = invoice.setdefault("extra_record_ids", [])
    excluded = invoice.setdefault("excluded_file_ids", [])
    if action in ("add_record", "remove_record"):
        rec = records.get(target)
        if not rec:
            raise GateError("That record is not on this job.")
        if action == "add_record":
            if target in extra:
                raise GateError(f"{rec['label']} already goes with {invoice['number']}.")
            if not rec.get("file_ids"):
                raise GateError(f"{rec['label']} has no file to send.")
            extra.append(target)
            _read_waiver(state, rec, read_file)   # the page asks whether it is a lien waiver, when it reads as one
            words = f"added {rec['label']} to the email for {invoice['number']}"
        else:
            if target not in extra:
                raise GateError(f"{rec['label']} was not added to {invoice['number']}.")
            extra.remove(target)
            words = f"took {rec['label']} off the email for {invoice['number']}"
    else:
        going = _packet_file_ids(state, job, {**invoice, "excluded_file_ids": []})
        if target not in going and target not in excluded:
            raise GateError("That file does not go with this pay app.")
        meta = next((f for f in state.get("files", []) if f["id"] == target), {})
        name = meta.get("name") or "the file"
        if action == "exclude_file":
            if target in excluded:
                raise GateError(f"{name} is already left out.")
            excluded.append(target)
            words = f"left {name} out of the email for {invoice['number']}"
        else:
            if target not in excluded:
                raise GateError(f"{name} already goes with {invoice['number']}.")
            excluded.remove(target)
            words = f"put {name} back in the email for {invoice['number']}"
        _items_follow_files(job, invoice)
    audit(state, job_id, "payapp_attachments", f"{actor} {words}.", actor)
    _refresh_draft_gate(job)
    return job


def _items_follow_files(job: dict[str, Any], invoice: dict[str, Any]) -> None:
    """An item filled only by records whose files are all left out is missing again; putting one
    back fills it again."""
    records = {r["id"]: r for r in job.get("records", [])}
    excluded = set(invoice.get("excluded_file_ids") or [])
    for item in invoice.get("packet") or []:
        files = [fid for a in _going(item) for fid in (records.get(a.get("record_id")) or {}).get("file_ids", [])]
        if not files or item.get("marked_in_hand") or item.get("present_reason"):
            continue
        out = set(files) <= excluded
        if out and item.get("present"):
            item["present"] = False
            item["files_left_out"] = True
        elif not out and item.pop("files_left_out", False):
            item["present"] = True


def link_record_to_work(state: dict[str, Any], job_id: str, record_id: str, co_id: str = "", sov_line_id: str = "",
                        actor: str = "owner") -> dict[str, Any]:
    """Say which change order or schedule line a daily ticket or time sheet backs up (M6): it then
    goes with the pay app that bills that work. Both blank takes the link off."""
    job = _job(state, job_id)
    rec = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not rec:
        raise GateError("That record is not on this job.")
    if co_id and not any(co["id"] == co_id for co in job.get("change_orders", [])):
        _refuse("", {"co_id": "Pick a change order on this job."})
    if sov_line_id and not any(line["id"] == sov_line_id for line in sov.lines(job)):
        _refuse("", {"sov_line_id": "Pick a line of this job's schedule of values."})
    rec["co_id"] = co_id or None
    rec["sov_line_id"] = sov_line_id or None
    what = (f"CO {next(co['number'] for co in job['change_orders'] if co['id'] == co_id)}" if co_id
            else next((line["description"] for line in sov.lines(job) if line["id"] == sov_line_id), "") if sov_line_id else "nothing")
    audit(state, job_id, "record_work", f"{actor} tied {rec['label']} to {what}.", actor)
    return job


def _sov_draft_lock(job: dict[str, Any]) -> dict[str, Any] | None:
    """The draft that is with the client for review, if that is the only thing keeping the schedule
    fixed: recording their answer or deleting it opens the schedule again."""
    if job.get("closed") or job.get("work_status") == "complete":
        return None
    live = sov.live_invoices(job)
    if any(inv.get("status") not in DRAFT_STATUSES for inv in live):
        return None
    return next((inv for inv in live if _with_client(inv)), None)


def _contract_follows(job: dict[str, Any]) -> bool:
    """The contract amount can still change and take the automatic lump-sum line with it."""
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    return (sov.is_auto(job) and not job.get("closed") and not _with_client(draft)
            and not any(inv.get("status") not in DRAFT_STATUSES for inv in sov.live_invoices(job)))


def _quantities_from_records(state: dict[str, Any], job: dict[str, Any], since: str) -> dict[str, dict[str, int]]:
    """What the job records since the last sent pay app put on each line, {line id: {milli, count}}:
    daily records' quantities that were added (those kept on the record only don't count) and
    imported files'."""
    found: dict[str, dict[str, int]] = {}
    ids = set(job.get("daily_records") or [])
    daily = [r for r in state.get("daily_records", []) if r.get("id") in ids] if ids else []
    # Records from before quantities_added: all of a record's quantities went on, or none did.
    rows = [(r.get("at") or "", r.get("quantities_added", [] if r.get("quantities_not_applied") else (r.get("sections") or {}).get("quantities") or []))
            for r in daily]
    rows += [(r.get("added_at") or "", r["quantities"]) for r in job.get("records", []) if r.get("quantities")]
    for at, quantities in rows:
        if at > since:
            for row in quantities:
                entry = found.setdefault(row["line_id"], {"milli": 0, "count": 0})
                entry["milli"] += row["qty_milli"]
                entry["count"] += 1
    return found


def _sov_period(state: dict[str, Any] | None, job: dict[str, Any]) -> dict[str, Any]:
    """The current billing period on the job's card and progress form: everything recorded and not
    yet billed on a sent pay app, late records included. Per line, the quantity done this period
    (field total less what sent pay apps billed), how much of it job records added since the last
    pay app was drafted, what it adds up to, and the suggested amount; then the open draft's own
    figures beside them, which a later job record doesn't change. `from` and `to` are the period
    dates the pay apps carry (period_to, the person's local day)."""
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    suggested = sov.this_period(job, skip=draft, field=True)
    drafted = {e["line_id"]: e["amount_cents"] for e in (draft or {}).get("sov_sheet") or []}
    chosen = (draft or {}).get("sov_overrides") or {}
    already = sov.billed(job, skip=draft)
    sent = [inv for inv in sov.live_invoices(job) if inv.get("status") not in DRAFT_STATUSES]
    since = max((inv.get("created_at") or "" for inv in sent), default="")
    records = _quantities_from_records(state, job, since) if state else {}
    by_id = {line["id"]: line for line in sov.lines(job)}
    rows = []
    for entry in suggested["sheet"]:
        line = by_id[entry["line_id"]]
        found = records.get(entry["line_id"]) or {}
        rows.append({
            "line_id": entry["line_id"], "item": entry["item"], "description": entry["description"], "unit": entry["unit"],
            "lump_sum": entry["unit"].upper() == "LS" and entry["qty_milli"] == 1000,
            "qty_this_milli": entry["qty_milli_this"],
            # A later correction can leave less on the line than the records added.
            "from_records_milli": min(found.get("milli", 0), entry["qty_milli_this"]), "record_count": found.get("count", 0),
            "adds_up_cents": entry["work_this_cents"],
            "suggested_cents": entry["amount_cents"], "final_cents": drafted.get(entry["line_id"], 0),
            "changed": entry["line_id"] in chosen,
            "credit": line["scheduled_cents"] < 0,
            "room_cents": sov.line_room(line, already["by_line"].get(line["id"], 0)),
        })
    unsigned = sum(co["amount_cents"] for co in unsigned_cos_in_billing(job))
    pct = int((draft or {}).get("retainage_pct", job.get("retainage_pct")) or 0)
    gross = suggested["total_cents"] + unsigned
    # A signed credit with nothing else to bill suggests nothing; it waits to come off the next pay app.
    waiting = max(0, -gross)
    gross = max(0, gross)
    held = (gross * pct + 50) // 100 if pct and gross > 0 else 0
    return {
        "from": max((inv.get("period_to") or "" for inv in sent), default=""),
        "to": (draft or {}).get("period_to") or "",
        "draft": {"id": draft["id"], "number": draft["number"], "with_client": _with_client(draft)} if draft else None,
        "lines": rows,
        "op_suggested_cents": suggested["op_cents"], "op_final_cents": (draft or {}).get("op_cents", 0),
        "unsigned_cents": unsigned, "retainage_pct": pct,
        "suggested_gross_cents": gross, "suggested_retainage_cents": held, "suggested_net_cents": gross - held,
        "credit_waiting_cents": waiting, "credit_waiting_names": [r["description"] for r in rows if r["credit"] and r["suggested_cents"] < 0] if waiting else [],
        "final_gross_cents": (draft or {}).get("gross_cents", 0), "final_retainage_cents": (draft or {}).get("retainage_cents", 0),
        "final_net_cents": (draft or {}).get("amount_cents", 0),
        "changed": bool(chosen),
    }


def _progress_form_figure(line: dict[str, Any], entry: dict[str, Any] | None) -> int:
    """What the progress form starts from: the open draft's own figure when one was typed for it
    (it differed from the field then), else the field total, which keeps up with job records."""
    if not entry:
        return sov.field_total(line)
    at_draft = entry.get("field_milli_at_draft", sov.field_total(line))   # drafts from before this was kept
    return line["done_milli"] if line["done_milli"] != at_draft else sov.field_total(line)


def _sov_view(job: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the job page shows for the schedule of values: per-line figures, the current period and
    whether it can be edited (until the first pay app is sent, Nov 3 Decision 2). draft_lock names
    a draft that is with the client when answering or deleting it would open the schedule again;
    open_draft is a draft that saving the schedule would draft again; sent names the first pay app
    that went out; contract_follows says the Edit job form can still change the contract amount."""
    live = sov.live_invoices(job)
    first_sent = next((inv for inv in live if inv.get("status") not in DRAFT_STATUSES), None)
    draft = _sov_draft_lock(job)
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    editable = not job.get("closed") and job.get("work_status") != "complete" and not first_sent and not _with_client(open_draft)
    view = {
        "editable": editable,
        "draft_lock": {"id": draft["id"], "number": draft["number"]} if draft else None,
        "open_draft": {"id": open_draft["id"], "number": open_draft["number"], "amount_cents": open_draft["amount_cents"]} if open_draft and editable else None,
        "sent": first_sent["number"] if first_sent else "",
        "done": job.get("work_status") == "complete" or bool(job.get("closed")),
    }
    if not sov.has_sov(job):
        return {"present": False, **view}
    # The card reads the field: quantities recorded count as done whether or not they are billed yet.
    field_lines = [sov.anticipated(job, line) for line in sov.lines(job)]
    figures = sov.totals({"sov": {"op_bp": sov.op_bp(job), "lines": field_lines}})
    already = sov.billed(job)
    sent_before = sov.billed(job, skip=open_draft)
    sent_qty = sent_before["qty_by_line"]
    on_draft = {e["line_id"]: e for e in (open_draft or {}).get("sov_sheet") or []}
    rows = []
    for stored, line in zip(sov.lines(job), field_lines):
        to_date = sov.line_to_date(line)
        rows.append({
            **line,
            "to_date_cents": to_date,
            "billed_cents": already["by_line"].get(line["id"], 0),
            "billed_qty_milli": already["qty_by_line"].get(line["id"], 0),
            "sent_qty_milli": sent_qty.get(line["id"], 0),
            "sent_cents": sent_before["by_line"].get(line["id"], 0),   # billed on pay apps that went, the open draft left out
            "field_milli": sov.field_total(stored),
            "form_done_milli": _progress_form_figure(stored, on_draft.get(line["id"])),
            "pct": (100 * to_date) // line["scheduled_cents"] if line["scheduled_cents"] else 0,
            "lump_sum": line["unit"].upper() == "LS" and line["qty_milli"] == 1000,
        })
    return {
        "present": True,
        **view,
        "auto": sov.is_auto(job),
        "contract_follows": _contract_follows(job),
        "op_bp": sov.op_bp(job),
        "op_billed_cents": already["op_cents"],
        "op_sent_cents": sent_before["op_cents"],
        "totals": figures,
        "lines": rows,
        "period": None if job.get("closed") else _sov_period(state, job),
    }


def _copy_list(payer: dict[str, Any]) -> list[str]:
    """Email addresses in the payer's "copy" field (who to copy on pay apps). Names without an address are skipped."""
    found = re.findall(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", str(payer.get("copy") or ""))
    out: list[str] = []
    for addr in found:
        if addr.lower() != str(payer.get("ap_email", "")).lower() and addr.lower() not in [a.lower() for a in out]:
            out.append(addr)
    return out[:5]


# Parts of the domain kept in their own modules (each lists what it exports in __all__).
from .validate import *  # noqa: E402,F401,F403
from .tasks import *  # noqa: E402,F401,F403
from .daily import *  # noqa: E402,F401,F403
from .comms import *  # noqa: E402,F401,F403
from .contact import *  # noqa: E402,F401,F403
from .jobinfo import *  # noqa: E402,F401,F403
