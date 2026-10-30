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
from datetime import date, datetime, timedelta, timezone
from typing import Any

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
    a link in that card."""

    def __init__(self, message: str, blockers: list[str] | None = None, fields: dict[str, str] | None = None,
                 extra: dict[str, Any] | None = None, action: dict[str, str] | None = None):
        super().__init__(message)
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
    collected = sum(p["amount_cents"] for p in job["payments"])
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
    outstanding = max(0, billed - collected - short_paid)
    return {
        "contract_cents": job["contract_cents"],
        "approved_co_cents": approved,
        "pending_co_cents": pending,
        "contract_total_cents": contract_total,
        "billed_cents": billed,
        "draft_cents": draft,
        "collected_cents": collected,
        "short_paid_cents": short_paid,
        "outstanding_cents": outstanding,
        "unbilled_completed_cents": unbilled_completed,
        "retainage_held_cents": retainage_held,
        "retainage_released_cents": retainage_released,
        "retainage_open_cents": max(0, retainage_held - retainage_released),
    }


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
        out.append({"kind": "not_done", "text": "Work is not marked done."})
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft:
        out.append({"kind": "draft", "text": f"{open_draft['number']} is still a draft.",
                    "invoice_id": open_draft["id"], "number": open_draft["number"]})
    for co in job["change_orders"]:
        if co["status"] == "pending":
            out.append({"kind": "co", "text": f"CO {co['number']} is not signed yet.", "co_id": co["id"], "number": co["number"]})
    if m["unbilled_completed_cents"] > 0 and not open_draft:
        out.append({"kind": "unbilled", "text": f"{money(m['unbilled_completed_cents'])} of finished work is not billed."})
    if m["outstanding_cents"] > 0:
        out.append({"kind": "outstanding", "text": f"{money(m['outstanding_cents'])} is still owed on pay apps sent."})
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
    return [item for item in active_packet(job) if item["required"] and not item["present"]]


def closeout_missing(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The closeout items not in yet, which hold back asking for all the retainage: the closeout
    packet, or for older jobs without one, the paperwork still missing on the job."""
    if job.get("closeout_packet"):
        return [item for item in job["closeout_packet"] if item["required"] and not item["present"]]
    return missing_packet(job)


def unsigned_cos_in_billing(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [co for co in job["change_orders"] if co["status"] == "pending" and co.get("included_in_billing")]


def blocker_items(job: dict[str, Any]) -> list[dict[str, Any]]:
    """What blocks send, each as {kind, text, co_id, number}: kind "paperwork" (a missing packet
    item) or "co" (an unsigned change order on the bill, which the app links to)."""
    items = [{"kind": "paperwork", "text": item["blocker"], "co_id": "", "number": 0} for item in missing_packet(job)]
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
        return f"Add {n} missing document{'' if n == 1 else 's'}"
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
        raise GateError(f"{open_draft['number']} is with the customer as a draft. Void it or record their answer first.")
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
    if open_draft:
        raise GateError(
            f"{open_draft['number']} is still a draft. Send it first, then mark the job done so the final pay app covers the rest."
        )
    runs_to = period_to(job, period_end)
    job["work_status"] = "complete"
    job["completed_at"] = job.get("completed_at") or utcnow()
    job["done_at_invoice"] = len(job["invoices"])   # pay apps from here on are the final one(s)
    job["done_notes"] = notes or job.get("done_notes", "")
    # The notes are kept on the job record (and in History, through the audit line below), not
    # on the job's header line (R2-05). A message filed from the Inbox is its own record already.
    if note_record and (notes or "").strip():
        job.setdefault("records", []).append({
            "id": "rec-" + secrets.token_hex(3), "kind": "note", "label": "Notes when marked done",
            "note": notes.strip(), "added_at": utcnow(), "added_by": actor,
        })
    if evidence:
        job.setdefault("evidence", []).extend(evidence)
    job["completed_value_cents"] = job["contract_cents"] + sum(
        co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved"
    )
    for line in sov.lines(job):
        line["done_milli"] = line["qty_milli"]
    audit(state, job_id, "job_done", f"{actor} marked {job['name']} done. Notes: {notes or 'none'}.", actor)
    _ensure_closeout_packet(state, job)
    return draft_invoice(state, job_id, actor="duracash", period_end=runs_to, notes=notes)


def draft_invoice(
    state: dict[str, Any], job_id: str, actor: str = "duracash", strict: bool = False, replaces: dict[str, Any] | None = None,
    period_end: str = "", notes: str = "",
) -> dict[str, Any]:
    """Draft a pay app for unbilled completed work, withholding retainage.

    strict=True is for the explicit "Draft pay app" button: a no-op raises
    instead of being logged silently, so the user is told why nothing happened.
    replaces is a draft just voided by a progress correction: the new one keeps its number
    (unless the customer saw it) and its paperwork.
    period_end is the date it runs to, already checked (period_to()); blank is today.
    notes are what the contractor wrote with the progress (R4-12): kept on the pay app and printed
    under its lines and on the PDF; a redraft without new notes keeps the replaced draft's.
    """
    job = _open_job(state, job_id)
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft:
        if strict:
            raise GateError(f"{open_draft['number']} is already an open draft. Send it before drafting another.")
        audit(state, job_id, "draft_skipped", "Open draft already exists.", actor)
        return job
    money_file = job_money(job)
    work = money_file["unbilled_completed_cents"]
    period = sov.this_period(job) if sov.has_sov(job) else None
    if period:
        work = period["total_cents"]
    pending_included = unsigned_cos_in_billing(job)
    gross = work + sum(co["amount_cents"] for co in pending_included)
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
        _attach_from_record(state, job, packet, number)
    else:
        packet = _packet_for_pay_app(state, job, invoice_id, number)
    if retained:
        # Show the punch-list/closeout items from the first pay app that holds
        # retainage, so they can be collected while the job is still running.
        _ensure_closeout_packet(state, job)
    packet_missing = [item["blocker"] for item in packet if item["required"] and not item["present"]]
    co_blockers = [
        f"Change Order {co['number']} is included in the billing amount but has not been signed."
        for co in pending_included
    ]
    blockers = packet_missing + co_blockers
    invoice = {
        "id": invoice_id,
        "number": number,
        "amount_cents": amount,
        "gross_cents": gross,
        "retainage_cents": retained,
        "status": "draft" if blockers else "awaiting_approval",
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
    job["invoices"].append(invoice)
    invoice["final"] = is_final(job)
    audit(
        state,
        job_id,
        "draft_invoice",
        f"Drafted {number} for {money(amount)}"
        + (f" ({money(gross)} less {money(retained)} retainage)" if retained else "")
        + f", with {len(packet)} paperwork item{'' if len(packet) == 1 else 's'}. {len(blockers)} to fix before it can go.",
        actor,
    )
    return job


def send_pencil(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    """Send the open draft to the payer as a pencil copy to agree quantities before the formal pay app."""
    job = _open_job(state, job_id)
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if not draft:
        raise GateError("Draft a pay app first. The draft is what goes for their review.")
    payer = _payer(state, job["payer_id"])
    to = _send_to(payer)
    subject = f"Pencil copy for review: {draft['number']} — {job['name']}"
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Here is the pencil copy of {draft['number']} for {job['name']}, attached as a PDF marked PENCIL COPY. "
        "Please confirm the quantities and change orders, or send corrections, before we submit the formal pay app.\n\n"
        f"{pay_app_text(state, job, draft)}\n\n{sign_off(state, actor)}"
    )
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
    audit(state, job_id, "pencil_response", f"{actor} recorded that the payer {what} the pencil copy of {draft['number']}. {draft['pencil_note']}".strip(), actor)
    return job


def resolve_packet_item(state: dict[str, Any], job_id: str, item_id: str, actor: str = "owner", reason: str = "") -> dict[str, Any]:
    job = _open_job(state, job_id)
    item, _invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Paperwork item not found.")
    # "I have this" (R3-05): where it is is optional; when given it is kept and audited.
    reason = (reason or "").strip()[:500] if isinstance(reason, str) else ""
    item["present"] = True
    item["resolved_at"] = utcnow()
    item["marked_in_hand"] = True
    if reason:
        item["present_reason"] = reason
    audit(state, job_id, "packet_resolved", f"{actor} marked {item['label']} as in hand{': ' + reason if reason else ''}.", actor)
    _refresh_draft_gate(job, state)
    return job


def payer_link(invoice: dict[str, Any], link_base: str | None, company_id: str) -> str:
    """The pay app's private page for the payer (P2-4), made the first time it is needed."""
    if not link_base or not company_id:
        return ""
    if not invoice.get("payer_token"):
        invoice["payer_token"] = secrets.token_urlsafe(24)
    return f"{link_base.rstrip('/')}/p/{company_id}.{invoice['payer_token']}"


def approve_and_send(
    state: dict[str, Any], job_id: str, actor: str = "owner", link_base: str | None = None, company_id: str = ""
) -> dict[str, Any]:
    job = _open_job(state, job_id)
    blockers = blocker_lines(job)
    if blockers:
        raise GateError("Pay app blocked.", blockers)
    invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] in {"draft", "awaiting_approval"}), None)
    if not invoice:
        raise GateError("No draft pay app to send.")
    payer = _payer(state, job["payer_id"])
    to = _send_to(payer)
    invoice["status"] = "sent"
    invoice["sent_at"] = utcnow()
    invoice["approved_by"] = actor   # shown as "sent by" (R4-05)
    # The contract as it went out: later change orders and releases don't change this pay app.
    invoice["contract_at_send"] = payapp_pdf.contract_terms(job)
    # The customer's payment terms set a due date from the day it goes out (R4-04).
    terms = _int_or(payer.get("payment_terms_days"), None)
    if terms:
        invoice["due_on"] = (datetime.now(timezone.utc).date() + timedelta(days=terms)).isoformat()
    invoice["final"] = is_final(job)
    job["external_status"] = f"Submitted to {payer['name']}, awaiting {payer['approver']}"
    subject = pay_app_subject(state, job, invoice)
    files = _packet_file_ids(state, job, invoice)
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Please find {'the final pay application' if invoice['final'] else 'pay application'} {invoice['number']} for {job['name']} attached as a PDF. Every item of paperwork is accounted for"
        + (f"; {len(files)} paperwork document{'s are' if len(files) != 1 else ' is'} attached too." if files else ".")
        + (f" Payment due {invoice['due_on']}." if invoice.get("due_on") else "")
        + f"\n\n{pay_app_text(state, job, invoice)}"
    )
    link = payer_link(invoice, link_base, company_id)
    if link:
        body += f"\n\nSee this pay app, its paperwork and documents, and tell us it reached you:\n{link}"
    body += f"\n\n{sign_off(state, actor)}"
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
    last contact on the oldest unpaid pay app, or the date noted after a call (note_contact) when
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
            why = f"you spoke to them {job['contact_noted_at'][:10]} and noted to check again then"
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
        body += f"\n\nThe pay app, its paperwork and documents: {link}"
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


def note_contact(state: dict[str, Any], job_id: str, text: str, next_on: Any = "", actor: str = "owner") -> dict[str, Any]:
    """"Spoke to them" (R4-06): what the customer said on the phone, kept on the job record and in
    the Activity log. It counts as a follow-up for the timing, and with a date the next follow-up
    waits until then."""
    job = _open_job(state, job_id)
    if not any(inv["status"] in OPEN_FOR_PAYMENT for inv in job["invoices"]):
        raise GateError("No sent pay app is waiting for payment.")
    text = " ".join(str(text or "").split())[:500]
    problems = {}
    if not text:
        problems["text"] = "Enter what they said, like \"Check goes out Friday\"."
    day = _day(next_on, "next_on", "Enter the date to check again, like 2026-11-07.")
    if day and day < datetime.now(timezone.utc).date() - timedelta(days=1):
        problems["next_on"] = "Enter today or a later date to check again."
    _refuse("Nothing was saved.", problems)
    payer = _payer(state, job["payer_id"])
    who = payer.get("approver") or payer["name"]
    now = utcnow()
    for inv in job["invoices"]:
        if inv["status"] in OPEN_FOR_PAYMENT:
            inv["last_follow_up_at"] = now
    job["contact_noted_at"] = now
    if day:
        job["expected_payment_on"] = day.isoformat()
    else:
        job.pop("expected_payment_on", None)
    job["last_contact"] = {"at": now, "by": actor, "text": text, "next_on": day.isoformat() if day else ""}
    job.setdefault("records", []).append({
        "id": "rec-" + secrets.token_hex(3), "kind": "note", "label": f"Spoke to {who}",
        "note": text + (f" Check again {day.isoformat()}." if day else ""), "added_at": now, "added_by": actor,
    })
    audit(state, job_id, "contact_noted", f"Spoke to {who}: {text}" + (f" Next check {day.isoformat()}." if day else "") + f" ({actor})", actor)
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
    if not open_invoices and not short:
        raise GateError("No open invoice to match.")
    if invoice_id:
        invoice = next((inv for inv in open_invoices + short if inv["id"] == invoice_id), None)
        if not invoice:
            _refuse("", {"invoice_id": "Pick a pay app that is waiting for payment."})
    elif len(open_invoices) == 1:
        invoice = open_invoices[0]
    elif not open_invoices and len(short) == 1:
        invoice = short[0]
    else:
        raise GateError(
            "More than one pay app is open. Choose which pay app this payment is for.",
            [f"{inv['number']} ({money(inv['amount_cents'])})" for inv in open_invoices + short],
            {"invoice_id": "Pick the pay app this payment is for."},
        )
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        _refuse("", {"amount_cents": "Enter the payment amount, more than $0."})
    already = sum(p["amount_cents"] for p in job["payments"] if p["invoice_id"] == invoice["id"])
    remaining = invoice["amount_cents"] - already
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
        invoice["short_pay_cents"] = max(0, invoice["amount_cents"] - collected_for_invoice)
        late = f" Short pay now {money(invoice['short_pay_cents'])}."
    elif collected_for_invoice >= invoice["amount_cents"]:
        if _legacy_waiver_pending(job):
            job["legacy_waiver_pending"] = True
        invoice["status"] = "paid"
        invoice["waiver_due"] = True
        invoice["waiver_sent"] = False
        _sync_waiver(job)
        job["external_status"] = "Payment received, waiver not sent"
    elif collected_for_invoice > 0:
        invoice["status"] = "partial"
        job["external_status"] = "Partial payment received"
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
    invoice["waiver_due"] = True
    invoice["waiver_sent"] = False
    _sync_waiver(job)
    job["external_status"] = "Short pay accepted, waiver not sent"
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
    """Payment has been matched, so unconditional; final for the final pay app (R4-04), or once
    the job is done and fully paid."""
    if invoice is not None and invoice.get("final"):
        return "unconditional_final"
    m = job_money(job)
    final = job.get("work_status") == "complete" and m["unbilled_completed_cents"] == 0 and m["outstanding_cents"] == 0
    return "unconditional_final" if final else "unconditional_progress"


def send_waiver(
    state: dict[str, Any], job_id: str, actor: str = "owner", invoice_id: str | None = None, kind: str | None = None
) -> dict[str, Any]:
    """One waiver per paid pay app. Older records keep a single job-level flag."""
    job = _open_job(state, job_id)
    if kind is not None and kind not in WAIVER_KINDS:
        raise GateError("Pick the waiver type: conditional or unconditional, progress or final.")
    pending = [inv for inv in job["invoices"] if inv.get("waiver_due") and not inv.get("waiver_sent")]
    if invoice_id:
        pending = [inv for inv in pending if inv["id"] == invoice_id]
    kind = kind or suggested_waiver_kind(job, pending[0] if pending else None)
    payer = _payer(state, job["payer_id"])
    if pending or (not invoice_id and _legacy_waiver_pending(job)):
        to = _send_to(payer)
    now = utcnow()
    if not invoice_id and _legacy_waiver_pending(job):
        # The older job-level waiver goes first; it predates per-pay-app tracking.
        which = ""
        detail = "the earlier matched payment"
        job["legacy_waiver_pending"] = False
        if not any("waiver_due" in inv for inv in job["invoices"]):
            job["waiver_sent"] = True
    elif pending:
        invoice = pending[0]
        invoice["waiver_sent"] = True
        invoice["waiver_sent_at"] = now
        invoice["waiver_kind"] = kind
        which = f" for {invoice['number']}"
        # After a short pay, what came in is the billed amount less what was kept off it.
        received = invoice["amount_cents"] - int(invoice.get("short_pay_cents") or 0)
        detail = f"payment of {money(received)} on {invoice['number']}"
    else:
        raise GateError("No waiver is due. Payment has not been matched.")
    job["waiver_sent_at"] = now
    job["waiver_kind"] = WAIVER_KINDS[kind]
    job["external_status"] = "Waiver sent"
    subject = f"{WAIVER_KINDS[kind]}{which} — {job['name']}"
    files = _waiver_file_ids(state, job)
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Thank you for the {detail} on {job['name']}. "
        + ("Our lien waiver for it is attached." if files else "Our signed lien waiver for it will follow separately.")
        + f"\n\n{sign_off(state, actor)}"
    )
    _record_email(state, job_id, "waiver", to, subject, body, actor, attachments=files)
    _sync_waiver(job)
    audit(state, job_id, "send_waiver", f"{actor} approved sending the {WAIVER_KINDS[kind].lower()}{which}.", actor)
    return job


def _sync_waiver(job: dict[str, Any]) -> None:
    """Keep the job-level waiver flags in step with the per-pay-app ones."""
    tracked = [inv for inv in job["invoices"] if "waiver_due" in inv]
    if not tracked:
        return
    pending = any(inv.get("waiver_due") and not inv.get("waiver_sent") for inv in tracked) or bool(job.get("legacy_waiver_pending"))
    job["waiver_due"] = True
    job["waiver_sent"] = not pending


def _legacy_waiver_pending(job: dict[str, Any]) -> bool:
    """An unsent waiver recorded only at job level (records from before per-pay-app waivers)."""
    if job.get("legacy_waiver_pending"):
        return True
    return bool(job.get("waiver_due") and not job.get("waiver_sent") and not any("waiver_due" in inv for inv in job["invoices"]))


def chase_retainage(state: dict[str, Any], job_id: str, actor: str = "duracash", amount_cents: int | None = None) -> dict[str, Any]:
    """Ask the customer to release retainage. All of it once the work is done and the closeout
    paperwork is in; `amount_cents` below what is held asks for part of it (R4-07), for example
    at substantial completion, before the job is marked done (once it is done, the closeout
    paperwork still has to be in). The request attaches the newest pay app sent."""
    job = _open_job(state, job_id)
    open_retainage = job_money(job)["retainage_open_cents"]
    if open_retainage <= 0:
        raise GateError("No open retainage.")
    if amount_cents is not None:
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
            _refuse("", {"amount_cents": "Enter the retainage to ask for, more than $0."})
        if amount_cents > open_retainage:
            _refuse("", {"amount_cents": f"Enter no more than the {money(open_retainage)} held."})
    amount = amount_cents or open_retainage
    partial = amount < open_retainage
    if job.get("work_status") != "complete" and not partial:
        if amount_cents is not None:
            _refuse("", {"amount_cents": f"Enter less than the {money(open_retainage)} held. All of it waits until the job is done."})
        raise GateError("Retainage is released at closeout. Mark the job done first, or ask for part of it now.")
    if job.get("work_status") == "complete":
        missing = closeout_missing(job)
        if missing:
            raise GateError("The closeout paperwork isn't complete.", [item["blocker"] for item in missing])
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
    files = _packet_file_ids(state, job, {"packet": job.get("closeout_packet") or job.get("packet") or []}) \
        if job.get("work_status") == "complete" else []
    newest = next((inv for inv in reversed(job["invoices"]) if inv["status"] in BILLED_STATUSES), None)
    if partial:
        ask = (f"The work on {job['name']} is substantially complete. Please release {money(amount)} "
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
    """Record retainage the payer released. Kept apart from pay-app payments."""
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
    job.setdefault("retainage_payments", []).append(
        {"amount_cents": amount_cents, "reference": reference or "manual", "at": utcnow(), "recorded_by": actor}
    )
    audit(state, job_id, "retainage_paid", f"{actor} recorded {money(amount_cents)} retainage released ({reference or 'manual'}).", actor)
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
    out["sov_view"] = _sov_view(job)
    # What is still owed on each pay app (R3-02): a part-paid one shows "$remaining of $amount".
    for inv in out["invoices"]:
        paid = sum(p["amount_cents"] for p in job["payments"] if p.get("invoice_id") == inv["id"])
        inv["remaining_cents"] = max(0, inv["amount_cents"] - paid) if inv["status"] in OPEN_FOR_PAYMENT else 0
    return out


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
    """The same email rows, each one that didn't go out and no longer needs to carrying "moot" (the
    reason), so the page shows why instead of a Send again that would be refused."""
    jobs = {j["id"]: j for j in state.get("jobs", [])}
    for r in rows:
        status = str(r.get("status") or "")
        job = jobs.get(r.get("job_id"))
        if job and (status.startswith("failed") or status.startswith("not sent")):
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
    heavy = {"jobs", "audit", "email_log"}
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
        due = payapp_due(payer, (now or datetime.now(timezone.utc)).date())
        if due:
            payer["payapp_due_next"], payer["payapp_due_prev"] = due[0].isoformat(), due[1].isoformat()
    emails = state.get("email_log", [])
    out["email_log"] = mark_moot(state, deepcopy(emails[-RECENT_EMAILS:]))
    out["email_log_total"] = len(emails)
    # Today lists these with Send again, however old they are; the count is for the Today badge.
    failures = email_failures(state)
    out["email_failures"] = [{k: v for k, v in e.items() if k != "body"} for e in failures[:RECENT_EMAILS]]
    out["pending_email_failures"] = len(failures)
    out["audit_total"] = len(state.get("audit", []))
    # Today's queue, drawn from the same rows as the daily email (R4-10).
    out["today_rows"] = today_rows(state, now)
    out["closed_jobs_total"] = closed_count
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
_TERM_WORDS = [(re.compile(r"\bPay apps\b"), "Draws"), (re.compile(r"\bpay apps\b"), "draws"),
               (re.compile(r"\bPay app\b"), "Draw"), (re.compile(r"\bpay app\b"), "draw")]


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


SOURCE_LABELS = {"upload": "Upload", "paste": "Pasted", "email": "Email", "sms": "Text message", "notes": "Notes", "payer_page": "Customer page"}


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
    waiting, not due, so it has no button."""
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
                    "path": path or f"/jobs/{job['id']}", "invoice_id": "", "co_id": "", "email_id": "", "item_id": "", **ids})

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
                "Mark done and draft", "/dashboard", item_id=item.get("id", ""))
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
        # One next step per job, in the job page's order (jobActions): blockers first.
        if is_blocked(job):
            more = f" And {len(items) - 1} more." if len(items) > 1 else ""
            paperwork = needs_paperwork(job)
            cos = [b for b in items if b["kind"] == "co"]
            add(3, "blocked", job, "Needs paperwork" if paperwork else "Blocked", "amber" if paperwork else "red", items[0]["text"] + more,
                blocker_button(job), f"/money/packet/{job['id']}/{draft['id']}" if draft else f"/money/packet/{job['id']}",
                invoice_id=draft["id"] if draft else "", co_id=cos[0]["co_id"] if cos and len(cos) == len(items) else "")
        elif draft:
            add(1, "ready", job, "Ready to send", "green", f"{draft['number']} for {money(draft['amount_cents'])} to {payer.get('approver') or payer.get('name', '')}. "
                "Check what goes out, then approve.", "Review & send", f"/money/pay-app/{job['id']}/{draft['id']}", invoice_id=draft["id"])
        elif job.get("waiver_due") and not job.get("waiver_sent"):
            add(2, "waiver", job, "Waiver due", "amber", "Paid in full: the customer is waiting for the lien waiver.", "Review waiver",
                f"/money/waiver/{job['id']}")
        elif job.get("work_status") == "complete" and m["unbilled_completed_cents"] > 0:
            add(5, "not_billed", job, "Not billed", "copper",
                f"{money(m['unbilled_completed_cents'])} of finished work has no pay app yet. Drafting sends nothing.", "Draft pay app")
        elif due_soon:
            add(2.5, "due_soon", job, "Due soon", "amber", "Record this month's progress and draft the pay app.", "Bill progress")
        # A new job (R2-04): nothing billed on it yet, so Today says what comes next instead of nothing.
        elif not any(i["status"] not in {"void", "replaced"} for i in invoices):
            add(5, "new_job", job, "Not billed yet", "copper", f"{job['name']} has no pay app yet. Bill the work done so far, or mark it done.",
                "Bill progress")
        # A cutoff makes the job's row more urgent, and says so.
        if due_soon and len(out) > before:
            row = out[-1]
            cutoff = term_text("Pay app due to", style)
            if cutoff not in row["text"]:
                row["text"] += f" {cutoff} {payer.get('name', '')} by {_day_words(due[0].isoformat())}."
            row["rank"] = min(row["rank"], 1.5)
        # A pay app sent and unpaid past the payer's follow-up window is its own row. One follow-up
        # names every unpaid pay app; after a call ("Spoke to them…") the row waits for the date
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
                    f"{names} was sent {_day_words(oldest.get('sent_at'))}{since}. Emails {payer.get('ap_email') or 'the customer'}.",
                    "Send follow-up", invoice_id=oldest["id"])
            # Approved on the customer's page (R4-08): no follow-up until the day after the date they gave.
            elif promised:
                add(4.5, "approved", job, "Approved", "green",
                    f"Approved by {oldest.get('approved_by_payer') or payer.get('approver') or payer.get('name', '')}, "
                    f"expect payment {_day_words(promised.isoformat())} ({oldest['number']}).", "", invoice_id=oldest["id"])
    for item in inbox:
        job = jobs.get(item.get("suggested_job_id"))
        if job and not item.get("says_done_job_id"):
            # item_kind: the kind the Inbox guessed, which the row shows and lets you change before filing.
            add(6, "inbox", job, "Inbox", "", (item.get("subject") or item.get("text") or "An item").split("\n")[0][:100], "Add to this job",
                "/communications", item_id=item.get("id", ""), note=item.get("match_reason") or "", item_kind=item.get("kind") or "note")
    return sorted(out, key=lambda r: r["rank"])


DIGEST_NOUNS = {
    "email_failed": ("email that didn't go out", "emails that didn't go out"), "says_done": ("job that may be done", "jobs that may be done"),
    "blocked": ("pay app missing paperwork", "pay apps missing paperwork"), "ready": ("pay app to send", "pay apps to send"),
    "waiver": ("waiver", "waivers"), "due_soon": ("pay app due soon", "pay apps due soon"), "follow_up": ("follow-up", "follow-ups"),
    "not_billed": ("job to bill", "jobs to bill"), "new_job": ("new job to bill", "new jobs to bill"), "inbox": ("Inbox item", "Inbox items"),
}


def daily_digest(state: dict[str, Any], base: str = "", now: datetime | None = None) -> tuple[str, str] | None:
    """The daily "due today" email (R4-10): (subject, body) listing every Today row that needs a
    person, each with its link; None on a day with nothing due. A pay app the customer approved
    with a date to pay is waiting, not due, and is left out."""
    rows = [r for r in today_rows(state, now) if r["kind"] in DIGEST_NOUNS]
    if not rows:
        return None
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    style = billing_style(state)
    parts = [term_text(f"{n} {DIGEST_NOUNS[k][0] if n == 1 else DIGEST_NOUNS[k][1]}", style) for k, n in counts.items()]
    subject = f"Due today in Duracash: {', '.join(parts)}"
    lines = [f"- {r['job']}: {r['label']}. {r['text']}" + (f"\n  {base}{r['path']}" if base else "") for r in rows]
    company = state.get("company", {}).get("name", "")
    body = (f"What needs you today{' at ' + company if company else ''}:\n\n" + "\n".join(lines)
            + "\n\nNothing goes out until you approve it in Duracash."
            + "\nTurn this email off in Settings, Company: Daily email of what's due.")
    return subject, body


def page(rows: list[dict[str, Any]], offset: int, limit: int, job_id: str = "") -> dict[str, Any]:
    """Newest first, optionally one job's rows: for the Outbox and Audit pages."""
    picked = [r for r in reversed(rows) if not job_id or r.get("job_id") == job_id]
    return {"entries": deepcopy(picked[offset:offset + limit]), "total": len(picked), "offset": offset, "limit": limit}


def timeline_page(state: dict[str, Any], job_id: str, offset: int, limit: int) -> dict[str, Any]:
    """One job's history, newest first (UI/UX Option B): every audit row on the job, and every
    email with its delivery status (bodies left out)."""
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
    keyed.sort(key=lambda kr: kr[0], reverse=True)
    rows = [r for _, r in keyed]
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
    if state is not None:
        _attach_from_record(state, job, active_packet(job), invoice["number"])
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
) -> None:
    """Write an approved email to the Outbox. The server delivers queued emails after the change is saved."""
    state.setdefault("email_log", []).append({
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


def update_payer(state: dict[str, Any], payer_id: str, updates: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    payer = _payer(state, payer_id)
    text_fields = {"approver": "who signs off on your bills", "ap_email": "the email pay apps go to",
                   "packet": "the paperwork needed", "rejects_when": "when they send pay apps back", "copy": "who else to email",
                   "name": "the customer's name", "phone": "the phone number", "escalation_email": "who to copy on late follow-ups"}
    day_fields = {"follow_up_days": "the days between follow-ups", "usual_approval_days": "the days they usually take to pay",
                  "payment_terms_days": "the days they have to pay"}
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for k, v in updates.items():
        if k == "payapp_due_day":
            # The payer's cutoff for this month's pay app (a contract date, not a legal one); blank clears it.
            day = None
            if v not in (None, ""):
                try:
                    day = parse_int(v, "the day pay apps are due", k)
                except GateError:
                    day = 0
            if day is not None and not 1 <= day <= 31:
                errors[k] = "Enter a day of the month from 1 to 31 for when pay apps are due, or leave it blank."
                continue
            clean[k] = day
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
                errors[k] = "Pick who pays you: the property owner or a general contractor."
                continue
            clean[k] = v
        elif k in day_fields:
            if v == "":
                clean[k] = None  # blank goes back to the usual number
                continue
            try:
                days = parse_int(v, day_fields[k], k)
            except GateError:
                days = None
            if days is None or not 1 <= days <= 365:
                errors[k] = f"Enter {day_fields[k]} as a whole number from 1 to 365."
                continue
            clean[k] = days
    if "ap_email" in clean and not EMAIL_RE.match(clean["ap_email"]):
        errors["ap_email"] = "Enter the email pay apps go to, like ap@builder.com."
    if clean.get("escalation_email") and not EMAIL_RE.match(clean["escalation_email"]):
        errors["escalation_email"] = "Enter one email to copy from the third follow-up, like owner@builder.com, or leave it blank."
    if "approver" in clean and not clean["approver"]:
        errors["approver"] = "Enter who signs off on your bills."
    if "name" in clean:
        same = _payer_named(state, clean["name"], other_than=payer["id"]) if clean["name"] else None
        if not clean["name"]:
            errors["name"] = "Enter the customer's name."
        elif same:
            errors["name"] = f"Another customer is already called {same['name']}."
        elif clean["name"] == payer["name"]:
            clean.pop("name")
    if errors:
        raise GateError("Customer settings not saved.", fields=errors)
    changed = []
    was = payer["name"]
    for k, v in clean.items():
        if v is None:
            payer.pop(k, None)
        else:
            payer[k] = v
        changed.append(k)
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


def _same_customer_question(field: str, typed: str, near: dict[str, Any]) -> GateError:
    """The refusal that asks "Same as ABC Builders?": the page offers the saved customer or
    keeping the new name (sent again with new_customer: true)."""
    question = f"Same as {near['name']}? Use the saved customer, or keep {typed} as a new one."
    return GateError(f"Same as {near['name']}?", fields={field: question},
                     extra={"same_customer": {"id": near["id"], "name": near["name"], "typed": typed}})


def _new_payer(state: dict[str, Any], name: str, role: str, approver: str, ap_email: str, phone: str) -> dict[str, Any]:
    """A customer with the usual settings for its role, added to the company."""
    packet_rule = "Signed contract, conditional waiver, completion photos, and pay-app backup."
    if role == "GC":
        # The pay app and its schedule of values are made here, so they are not on the list.
        packet_rule = "Lien waiver, insurance certificate, daily tickets."
    payer = {
        "id": "payer-" + secrets.token_hex(3),
        "name": name,
        "role": role,
        "approver": approver,
        "ap_email": ap_email,
        "phone": phone,
        "packet": packet_rule,
        "follow_up_days": 5 if role == "GC" else 7,
        "usual_approval_days": 21 if role == "GC" else 14,
        "rejects_when": "Missing required paperwork.",
        "copy": "",
    }
    state["payers"].append(payer)
    return payer


def add_payer(state: dict[str, Any], body: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    """A customer added from Customer settings, before any job (R3-07). Only the name is needed;
    who signs off defaults to the customer, and the email can wait until the first send."""
    def text(key: str) -> str:
        v = body.get(key)
        return str(v).strip() if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""
    name, approver, ap_email, phone = text("name"), text("approver"), text("ap_email"), text("phone")
    role = text("role") if text("role") in {"Owner", "GC"} else "Owner"
    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Enter the customer's name."
    elif _payer_named(state, name):
        errors["name"] = f"Another customer is already called {_payer_named(state, name)['name']}."
    if ap_email and not EMAIL_RE.match(ap_email):
        errors["ap_email"] = "Enter the email pay apps go to, like ap@builder.com, or leave it blank for now."
    _refuse("Customer not added.", errors)
    near = None if body.get("new_customer") is True else payer_near(state, name)
    if near:
        raise _same_customer_question("name", name, near)
    payer = _new_payer(state, name, role, approver or name, ap_email, phone)
    audit(state, "", "payer_add", f"{actor} added the customer {name}.", actor)
    return payer


def merge_payer(state: dict[str, Any], payer_id: str, into_id: str, actor: str = "owner") -> dict[str, Any]:
    """Two customers that are one (a typo, or "Ironleaf" and "Ironleaf GC"): every job of the
    first moves to the second, which keeps its own settings; the first is removed (R3-07)."""
    payer = _payer(state, payer_id)
    into = _payer(state, into_id) if into_id else None
    if not into or into["id"] == payer["id"]:
        raise GateError("Pick the customer to merge into.", fields={"into": "Pick the customer to merge into."})
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
        advice = ("To remove it, merge it into another customer." if len(state["payers"]) > 1
                  else "Its jobs need it.")
        raise GateError(f"{payer['name']} can't be deleted: {jobs} this customer{finished}. {advice}")
    state["payers"].remove(payer)
    audit(state, "", "payer_delete", f"{actor} deleted the customer {payer['name']}.", actor)


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


COMPANY_FIELDS = {"name": 120, "address": 200, "phone": 40, "license": 60}
BILLING_STYLES = {"pay_apps", "draws"}
# On/off company settings on Settings, Company, with their default: members may send pay apps,
# waivers and retainage requests (R4-09); owners get the daily "due today" email (R4-10).
COMPANY_SWITCHES = {"members_can_send": False, "daily_email": True}
SWITCH_WORDS = {"members_can_send": "members can send", "daily_email": "daily email of what's due"}


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
    COMPANY_SWITCHES."""
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
    labels = {"name": "the company name", "address": "the address", "phone": "the phone number", "license": "the licence number"}
    for k, limit in COMPANY_FIELDS.items():
        if k not in updates:
            continue
        v = updates[k]
        if not isinstance(v, str):
            errors[k] = f"Enter {labels[k]} as text."
            continue
        v = " ".join(v.split())
        if len(v) > limit:
            errors[k] = f"Shorten {labels[k]} to {limit} characters or fewer."
        elif k == "name" and not v:
            errors[k] = "Enter the company name."
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
                 **{k: f"setting \"{SWITCH_WORDS[k]}\" ({'on' if clean.get(k) else 'off'})" for k in COMPANY_SWITCHES}}
        audit(state, "", "company_update", f"{actor} updated the company's {', '.join(names.get(k, k) for k in changed)}.", actor)
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
        audit(state, "", "playbook_learned", f"{actor} added \"{sug['text']}\" to {payer['name']}'s paperwork list ({sug['source']}).", actor)
    return payer


MAX_SCHEDULE_DAYS = 365


def add_change_order(
    state: dict[str, Any], job_id: str, title: str, amount_cents: int, actor: str = "owner", description: str = "",
    schedule_days: int = 0,
) -> dict[str, Any]:
    """A change order, pending until signed. A negative amount is a credit (R3-06): signed, it
    lowers the contract. `schedule_days` is how many days it adds to (or takes off) the schedule."""
    job = _open_job(state, job_id)
    title = title.strip() if isinstance(title, str) else ""
    description = description.strip() if isinstance(description, str) else ""
    errors: dict[str, str] = {}
    if not title:
        errors["title"] = "Enter a title for the change order."
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents == 0:
        errors["amount_cents"] = "Enter the change order amount, like 1,250, or -1,250 for a credit."
    elif abs(amount_cents) > MAX_AMOUNT_CENTS:
        errors["amount_cents"] = f"Enter a change order amount up to {money(MAX_AMOUNT_CENTS)}."
    elif amount_cents < 0 and job["contract_cents"] + sum(c["amount_cents"] for c in job["change_orders"] if c["status"] == "approved") + amount_cents < 0:
        errors["amount_cents"] = "Enter a credit no larger than the contract total."
    if not isinstance(schedule_days, int) or isinstance(schedule_days, bool) or abs(schedule_days) > MAX_SCHEDULE_DAYS:
        errors["schedule_days"] = f"Enter the schedule change in whole days, up to {MAX_SCHEDULE_DAYS}, or leave it blank."
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
    }
    if schedule_days:
        co["schedule_days"] = schedule_days
    job["change_orders"].append(co)
    what = f"a credit of {money(-amount_cents)}" if amount_cents < 0 else money(amount_cents)
    days = f", {schedule_days:+d} day{'' if abs(schedule_days) == 1 else 's'} on the schedule" if schedule_days else ""
    audit(state, job_id, "add_co", f"{actor} added CO {number} — {title} for {what}{days}.", actor)
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
    co["approved_at"] = utcnow()
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
    _refresh_draft_gate(job, state)
    return job


# ---------- signed change orders (P2-3) ----------
# Draft consent wording, written to describe what the code does; counsel should check it
# against ESIGN and the state's rules before real customers rely on it.
CO_CONSENT = (
    "I agree to this change order. I agree to sign it electronically, and my typed name below is my signature."
)


def co_snapshot(state: dict[str, Any], job: dict[str, Any], co: dict[str, Any]) -> dict[str, Any]:
    """Exactly what the signer saw; its SHA-256 is kept with the signature."""
    return {
        "contractor": state.get("company", {}).get("name", ""),
        "job": job["name"],
        "address": job.get("address", ""),
        "change_order": co["number"],
        "title": co["title"],
        "description": co.get("description", ""),
        "amount_cents": co["amount_cents"],
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
    co["signature_requested_at"] = utcnow()
    link = f"{link_base.rstrip('/')}/s/{company_id}.{co['sign_token']}"
    payer = _payer(state, job["payer_id"])
    if payer.get("ap_email"):
        body = (
            f"{payer.get('approver') or payer['name']},\n\nPlease review and sign change order {co['number']} for {job['name']}: "
            f"{co['title']}, {money(co['amount_cents'])}."
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
    return co


def mark_co_signed(state: dict[str, Any], job_id: str, co_id: str, record_id: str, actor: str = "owner") -> dict[str, Any]:
    """Approve a change order signed on paper: the job record must hold the signed copy."""
    job, co = _pending_co(state, job_id, co_id)
    record = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not record:
        raise GateError("Pick the job record that holds the signed change order, or request an online signature.")
    co["signed_evidence"] = {"record_id": record_id, "label": record.get("label", ""), "by": actor, "at": utcnow()}
    approve_change_order(state, job_id, co_id, actor)
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
    co["withdrawn_at"] = utcnow()
    audit(state, job_id, "withdraw_co", f"{actor} withdrew CO {co['number']} — {co['title']}.", actor)
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
            audit(state, job_id, "change_order_off_draft", f"CO {co['number']} taken off the voided draft.", actor)
    return job


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
    """Mark done tapped by mistake: delete the final draft and put the job back in progress, with
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
    if open_draft:
        raise GateError(f"Send or finish {open_draft['number']} first. The CO goes on the next pay app.")
    co["included_in_billing"] = True
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
    audit(state, job_id, "learn_rejection", f"{actor} saved to customer settings: {payer['name']} sends pay apps back when {reason}.", actor)
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
    """Bring a finished job back, for a late payment or a fix (R4-02). The reason goes in History."""
    job = _job(state, job_id)
    if not job.get("closed"):
        raise GateError(f"{job['name']} is not finished.")
    reason = _reason(reason)
    if not reason:
        _refuse("", {"reason": "Enter why you are reopening it, like a late payment came in."})
    job["closed"] = False
    job.pop("closed_at", None)
    job["reopened_at"] = utcnow()
    audit(state, job_id, "reopen_job", f"{actor} reopened {job['name']}: {reason}.", actor)
    return job


def add_daily_record(state: dict[str, Any], job_id: str, notes: str, actor: str = "owner", at: str | None = None) -> dict[str, Any]:
    """A dated note on the job. `at` lets an import keep the day the note was written."""
    job = _job(state, job_id)
    rec = {
        "id": "dr-" + secrets.token_hex(4),
        "job_id": job_id,
        "notes": notes,
        "at": at or utcnow(),
        "actor": actor,
        "for_history": bool(job.get("closed")),
    }
    state.setdefault("daily_records", []).append(rec)
    job.setdefault("daily_records", []).append(rec["id"])
    add_job_record(state, job_id, f"Daily record {rec['at'][:10]}", "daily", notes, actor)
    audit(state, job_id, "daily_record", f"{actor} added a daily record to the job record.", actor)
    return job


def add_packet_attachment(state: dict[str, Any], job_id: str, item_id: str, name: str, actor: str = "owner") -> dict[str, Any]:
    job = _open_job(state, job_id)
    item, _invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Paperwork item not found.")
    item.setdefault("attachments", []).append({"name": name, "added_at": utcnow(), "added_by": actor})
    item["present"] = True
    audit(state, job_id, "packet_attachment", f"{actor} attached {name} to {item['label']}.", actor)
    _refresh_draft_gate(job, state)
    return job


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
        raise GateError("Customer not found.")
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
    """Turn a playbook line like 'Invoice, photos and conditional waiver.' into item labels."""
    if not isinstance(text, str):
        return []
    parts = re.split(r",|;|\n|\.\s+|\.$|\band\b", text)
    labels = []
    for part in parts:
        label = part.strip(" .:-")
        if label and label.lower() not in {l.lower() for l in labels}:
            labels.append(label[0].upper() + label[1:])
    return labels


def _is_closeout(label: str) -> bool:
    lower = label.lower()
    return "closeout" in lower or "close-out" in lower or "retainage" in lower or "punch" in lower


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
    """Jobs with retainage get a closeout packet that gates the release request, not the pay apps."""
    if not job.get("retainage_pct") or job.get("closeout_packet"):
        return
    if any(item.get("kind") == "closeout" for item in job.get("packet") or []):
        return  # older record: closeout already lives in the job packet
    payer = next((p for p in state["payers"] if p["id"] == job["payer_id"]), {})
    labels = [l for l in parse_packet_text(payer.get("packet", "")) if _is_closeout(l)]
    labels = labels or ["Closeout / punch-list signoff"]
    job["closeout_packet"] = _items_from_labels(f"closeout-{job['id']}", labels)


def _packet_for_pay_app(state: dict[str, Any], job: dict[str, Any], invoice_id: str, number: str = "") -> list[dict[str, Any]]:
    """Reuse an existing job packet (older records), else build one from the payer playbook.
    Paperwork Duracash makes is in from the start, and missing items are filled from the job record."""
    if job.get("packet"):
        _attach_from_record(state, job, job["packet"], number or "the job's paperwork")
        return job["packet"]
    items = _new_packet(state, job, invoice_id)
    for item in items:
        if _made_here(item["label"], job):
            item.update(present=True, present_reason=MADE_HERE_REASON, resolved_at=utcnow())
    _attach_from_record(state, job, items, number or "the pay app")
    return items


def _new_packet(state: dict[str, Any], job: dict[str, Any], invoice_id: str) -> list[dict[str, Any]]:
    payer = next((p for p in state["payers"] if p["id"] == job["payer_id"]), {})
    labels = [l for l in parse_packet_text(payer.get("packet", "")) if not _is_closeout(l)]
    if labels:
        return _items_from_labels(invoice_id, labels)
    if payer.get("no_paperwork"):
        return []  # every item was marked not needed for this customer
    items = [
        _packet_item(invoice_id, "contract", "Signed contract", "Signed contract is missing."),
        _packet_item(invoice_id, "waiver", "Conditional lien waiver", "Conditional lien waiver is missing."),
        _packet_item(invoice_id, "photo", "Completion photos", "Completion photos are missing."),
        _packet_item(invoice_id, "backup", "Pay application backup", "Pay application backup is missing."),
    ]
    if job.get("payer_role") == "GC":
        items.append(_packet_item(invoice_id, "sov", "Schedule of values", "Schedule of values is missing."))
        items.append(_packet_item(invoice_id, "insurance", "Insurance certificate", "Insurance certificate is missing."))
    return items


# Paperwork Duracash makes itself, so a pay app never waits on it: the pay app and its schedule
# of values or continuation sheet. Backup counts only when the job bills from a schedule (the
# continuation sheet is the backup); otherwise it is tickets and invoices from the job record.
MADE_HERE_RE = re.compile(r"pay[\s-]*app(?:lication)?s?\b|schedule of values|continuation sheet", re.I)
MADE_HERE_REASON = "Produced by Duracash"

# Packet item -> the job-record kinds that fill it, and the words that name it in a customer's
# list (items from the list carry a slug of their label as their kind). First match wins, so
# "Contractor's insurance certificate" is insurance, not the contract.
RECORD_FOR_ITEM = (
    ("backup", ("pay_app",), re.compile(r"backup", re.I)),
    ("insurance", ("insurance",), re.compile(r"insurance|\bcoi\b", re.I)),
    ("co", ("change_order",), re.compile(r"change order", re.I)),
    ("waiver", ("waiver",), re.compile(r"waiver", re.I)),
    ("photo", ("photo",), re.compile(r"photo", re.I)),
    ("supplier_invoice", ("supplier_invoice",), re.compile(r"invoice|receipt", re.I)),
    ("ticket", ("ticket", "supplier_invoice"), re.compile(r"ticket|time ?sheet", re.I)),
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
PER_PAY_APP = {"backup", "co", "waiver", "photo", "ticket", "supplier_invoice", "stored", "inspection"}
AUTO_NOTE = "Attached from the job record"


def _made_here(label: str, job: dict[str, Any]) -> bool:
    if "backup" in label.lower():
        return sov.has_sov(job)
    return bool(MADE_HERE_RE.search(label))


def _record_kinds_for(item: dict[str, Any]) -> tuple[str, tuple[str, ...]] | None:
    for name, kinds, words in RECORD_FOR_ITEM:
        if item.get("kind") == name or words.search(item.get("label", "")):
            return name, kinds
    return None


def _attach_from_record(state: dict[str, Any], job: dict[str, Any], packet: list[dict[str, Any]], where: str) -> None:
    """Fill each missing item with the newest job record of the kind that matches it."""
    used = {
        a.get("record_id")
        for inv in job.get("invoices", []) if inv.get("status") != "void" and inv.get("packet") is not packet
        for p in inv.get("packet") or [] for a in p.get("attachments", [])
    }
    # A waiver that already went out in a waiver email belongs to that payment, not this pay app.
    sent = {fid for e in state.get("email_log", []) if e.get("job_id") == job["id"] and e.get("kind") == "waiver" for fid in e.get("attachments", [])}
    used |= {r["id"] for r in job.get("records", []) if r.get("kind") == "waiver" and r.get("file_ids") and set(r["file_ids"]) <= sent}
    for item in packet:
        if item.get("present") or not item.get("required"):
            continue
        match = _record_kinds_for(item)
        if not match:
            continue
        name, kinds = match
        declined = set(item.get("detached_record_ids", []))
        record = next((
            r for r in reversed(job.get("records", []))
            if r.get("kind") in kinds and not r.get("number_only") and r["id"] not in declined
            and not (name in PER_PAY_APP and r["id"] in used)
        ), None)
        if not record:
            continue
        item.setdefault("attachments", []).append({
            "name": record["label"], "record_id": record["id"], "added_at": utcnow(), "added_by": "duracash", "auto": True,
        })
        item["present"] = True
        item["resolved_at"] = utcnow()
        audit(state, job["id"], "attach_record", f"{record['label']} attached automatically from the job record to {item['label']} on {where}.", "duracash")


def detach_packet_item(state: dict[str, Any], job_id: str, item_id: str, actor: str = "owner") -> dict[str, Any]:
    """Remove (R3-05): take everything off an item, whether attached by hand, attached from the
    job record or marked as in hand, so it is missing again. The records stay on the job record,
    and one attached automatically is not attached again."""
    job = _open_job(state, job_id)
    item, _invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Paperwork item not found.")
    if item.get("present_reason") == MADE_HERE_REASON:
        raise GateError(f"Duracash makes {item['label']} with the pay app; there is nothing to remove.")
    if _invoice and _invoice.get("status") not in DRAFT_STATUSES:
        # What went with a pay app stays as it was sent; the customer may already have it.
        raise GateError(f"{_invoice['number']} has gone to the customer, so its paperwork stays as it was sent.")
    taken = item.get("attachments", [])
    if not taken and not item.get("present"):
        raise GateError(f"Nothing is attached to {item['label']}.")
    item["attachments"] = []
    item.setdefault("detached_record_ids", []).extend(a["record_id"] for a in taken if a.get("auto") and a.get("record_id"))
    item["present"] = False
    item.pop("resolved_at", None)
    item.pop("present_reason", None)
    item.pop("marked_in_hand", None)
    names = ", ".join(a["name"] for a in taken) or "the in-hand mark"
    audit(state, job_id, "paperwork_detached", f"{actor} took {names} off {item['label']}.", actor)
    _refresh_draft_gate(job)
    return job


def skip_packet_item(state: dict[str, Any], job_id: str, item_id: str, reason: str = "", actor: str = "owner") -> dict[str, Any]:
    """This customer does not need the item: it stops blocking, and it leaves the customer's
    paperwork list so later pay apps do not ask for it."""
    job = _open_job(state, job_id)
    item, _invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Paperwork item not found.")
    reason = (reason or "").strip()[:500] if isinstance(reason, str) else ""
    item["required"] = False
    item["skipped_reason"] = reason or "Not needed for this customer"
    item["skipped_at"] = utcnow()
    payer = _payer(state, job["payer_id"])
    labels = parse_packet_text(payer.get("packet", ""))
    if not labels and _invoice:
        # No list saved: this pay app used the default one, so save what it asked for, less this.
        labels = [p["label"] for p in _invoice.get("packet") or [] if p is item or not p.get("skipped_reason")]
    kept = [l for l in labels if l.lower() != item["label"].lower()]
    if len(kept) != len(labels):
        payer["packet"] = _packet_text(kept)
        if kept:
            payer.pop("no_paperwork", None)
        else:
            payer["no_paperwork"] = True  # an empty list would bring the default one back
        audit(state, "", "playbook_update", f"{actor} took \"{item['label']}\" off {payer['name']}'s paperwork list.", actor)
    audit(state, job_id, "paperwork_not_needed", f"{actor} marked {item['label']} not needed: {item['skipped_reason']}.", actor)
    _refresh_draft_gate(job)
    return job


def _packet_text(labels: list[str]) -> str:
    """Labels back into a paperwork list, as a person would write it."""
    words = [l if i == 0 or l[1:2].isupper() else l[0].lower() + l[1:] for i, l in enumerate(labels)]
    return (", ".join(words) + ".") if words else ""


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
RECORD_KINDS = ("note", "daily", "photo", "contract", "change_order", "waiver", "pay_app", "ticket",
                "supplier_invoice", "insurance", "inspection", "stored", "punch", "completion", "asbuilt", "warranty", "other")


def edit_record(state: dict[str, Any], job_id: str, record_id: str, label: str, kind: str, actor: str = "owner") -> dict[str, Any]:
    """Fix a record's label or kind (a supplier invoice filed as a pay app, a photo read as a note) (R5-07).
    Paperwork it is attached to keeps it; the draft's paperwork check runs again."""
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
    if before != (label, kind):
        audit(state, job_id, "edit_record", f"{actor} changed the record '{before[0]}' ({before[1]}) to '{label}' ({kind}).", actor)
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
    audit(state, job_id, "add_record", f"{actor} added {kind} to the job record: {label}.", actor)
    if not job.get("closed"):
        _refresh_draft_gate(job, state)
    return job


def attach_record_to_packet(
    state: dict[str, Any],
    job_id: str,
    item_id: str,
    record_id: str,
    actor: str = "owner",
) -> dict[str, Any]:
    job = _open_job(state, job_id)
    item, invoice = _find_packet_item(job, item_id)
    record = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not item:
        raise GateError("Paperwork item not found.")
    if not record:
        raise GateError("Job record not found.")
    already = next((a for a in item.get("attachments", []) if a.get("record_id") == record_id), None)
    if already:
        # Attached from the job record as it was added (a photo taken for this item): it is the
        # person's choice now, so keep one attachment and make it theirs.
        already.pop("auto", None)
        already["added_by"] = actor
        _refresh_draft_gate(job, state)
        return job
    item.setdefault("attachments", []).append({
        "name": record["label"],
        "record_id": record_id,
        "added_at": utcnow(),
        "added_by": actor,
    })
    item["present"] = True
    item["resolved_at"] = utcnow()
    which = invoice["number"] if invoice else "the job's paperwork"
    audit(state, job_id, "attach_record", f"{actor} attached {record['label']} to {item['label']} on {which}.", actor)
    _refresh_draft_gate(job, state)
    return job


def create_job(
    state: dict[str, Any],
    name: str,
    customer_name: str,
    contract_cents: int,
    address: str = "",
    approver: str = "",
    ap_email: str = "",
    phone: str = "",
    role: str = "Owner",
    retainage_pct: int = 0,
    note: str = "",
    contract_number: str = "",
    actor: str = "owner",
    new_customer: bool = False,
) -> dict[str, Any]:
    """Add a job and its record. The packet is created later, with a pay app, from the payer's
    paperwork list (Customer settings); `note` is kept on the job record and never becomes it.
    Who signs off and the email are optional: the email is asked for before the first send.
    A customer name that nearly matches a saved one ("ABC Builders Inc" for "ABC Builders") is
    asked about first, unless new_customer says to keep it as a new customer."""
    name = (name or "").strip()
    customer_name = (customer_name or "").strip()
    address = (address or "").strip()
    approver = (approver or "").strip()
    ap_email = (ap_email or "").strip()
    phone = (phone or "").strip()
    role = role if role in {"Owner", "GC"} else "Owner"
    note = (note or "").strip()
    contract_number = (contract_number or "").strip()

    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Enter the job name."
    if not address:
        errors["address"] = "Enter the job address."
    if not customer_name:
        errors["customer"] = "Enter the customer: who pays for this job."
    if ap_email and not EMAIL_RE.match(ap_email):
        errors["ap_email"] = "Enter the email pay apps go to, like ap@builder.com, or leave it blank for now."
    if not isinstance(contract_cents, int) or isinstance(contract_cents, bool) or contract_cents <= 0:
        errors["contract_cents"] = "Enter the contract amount, more than $0."
    elif contract_cents > MAX_AMOUNT_CENTS:
        errors["contract_cents"] = f"Enter a contract amount up to {money(MAX_AMOUNT_CENTS)}."
    if not isinstance(retainage_pct, int) or isinstance(retainage_pct, bool) or not 0 <= retainage_pct <= 20:
        errors["retainage_pct"] = "Enter a retainage from 0 to 20 percent."
    _refuse("Job not saved.", errors)
    approver = approver or customer_name  # a homeowner signs off on their own bills

    payer = _payer_named(state, customer_name)
    near = None if payer or new_customer else payer_near(state, customer_name)
    if near:
        raise _same_customer_question("customer", customer_name, near)
    if not payer:
        payer = _new_payer(state, customer_name, role, approver, ap_email, phone)
    else:
        # Keep the payer's existing contact. A new job must not silently redirect
        # where every other job's pay apps go; changes belong in Customer settings.
        # A blank one is filled in, since nothing goes anywhere yet.
        if ap_email and not str(payer.get("ap_email") or "").strip():
            payer["ap_email"] = ap_email
            audit(state, "", "playbook_update", f"{actor} added the email for pay apps to {payer['name']}: {ap_email}.", actor)
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
                f"{actor} entered a different {' and '.join(differs)} for {payer['name']}; kept the saved ones. Change them in Customer settings.",
                actor,
            )

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
    }
    state["jobs"].append(job)
    audit(
        state,
        job["id"],
        "create_job",
        f"{actor} created {job['name']} for {payer['name']} ({money(job['contract_cents'])}). The paperwork list comes with the first pay app.",
        actor,
    )
    return job



def edit_job(state: dict[str, Any], job_id: str, updates: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    """Fix a job's name, address or contract number at any time. The contract amount can change
    only before the first pay app, and only without a schedule of values (change that schedule
    instead). Retainage can change any time and applies from the next pay app (R4-07): pay apps
    already sent keep what they held, and an open draft is drafted again at the new percent. The job code never changes: emails
    and texts are filed by it. Payer contacts are edited in the Payer Playbook."""
    job = _open_job(state, job_id)
    has_pay_apps = any(i.get("status") != "void" for i in job.get("invoices", []))
    changes: list[str] = []
    problems: dict[str, str] = {}

    for key, label in (("name", "Job name"), ("address", "Job address"), ("contract_number", "Contract number")):
        if key not in updates:
            continue
        value = str(updates[key] or "").strip()
        if key != "contract_number" and not value:
            problems[key] = f"Enter the {label.lower()}."
        elif value != job.get(key, ""):
            changes.append(f"{label.lower()} from \"{job.get(key, '')}\" to \"{value}\"")
            job[key] = value

    if "contract_cents" in updates and updates["contract_cents"] != job["contract_cents"]:
        cents = updates["contract_cents"]
        if has_pay_apps:
            problems["contract_cents"] = "Use a change order: the contract amount is fixed once a pay app exists."
        elif sov.has_sov(job):
            problems["contract_cents"] = "Change the contract from the schedule of values: this job bills from it."
        elif not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
            problems["contract_cents"] = "Enter the contract amount, more than $0."
        elif cents > MAX_AMOUNT_CENTS:
            problems["contract_cents"] = f"Enter a contract amount up to {money(MAX_AMOUNT_CENTS)}."
        else:
            changes.append(f"contract from {money(job['contract_cents'])} to {money(cents)}")
            job["contract_cents"] = cents
            if job.get("work_status") == "complete":
                # Marked done: the work done is the whole contract, so it moves with the new amount.
                job["completed_value_cents"] = cents + sum(
                    co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved"
                )

    redraft = None
    if "retainage_pct" in updates and updates["retainage_pct"] != job.get("retainage_pct", 0):
        pct = updates["retainage_pct"]
        open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
        if not isinstance(pct, int) or isinstance(pct, bool) or not 0 <= pct <= 20:
            problems["retainage_pct"] = "Enter a retainage from 0 to 20 percent."
        elif open_draft and open_draft.get("pencil_sent_at"):
            problems["retainage_pct"] = (f"{open_draft['number']} went to the customer as a draft at {job.get('retainage_pct', 0)}%. "
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
        changes.append(f"{redraft['number']} drafted again at {job['retainage_pct']}%")
        draft_invoice(state, job_id, actor="duracash", replaces=redraft, period_end=redraft.get("period_to") or "")
    if changes:
        audit(state, job_id, "edit_job", f"{actor} changed {job['name']}: " + "; ".join(changes) + ".", actor)
    return job


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
) -> dict[str, Any]:
    """Set the job's schedule of values. Allowed until the first pay app is drafted."""
    job = _open_job(state, job_id)
    live = sov.live_invoices(job)
    if live:
        draft = _sov_draft_lock(job)
        if draft:
            raise GateError(f"The schedule is fixed while {draft['number']} is a draft. Delete the draft to change it.")
        raise GateError(
            f"The schedule of values is fixed once a pay app exists ({live[0]['number']}). "
            "Scope changes go through change orders, which add their own lines."
        )
    if job.get("work_status") == "complete":
        raise GateError("The job is marked done. A schedule of values is set before billing starts.")
    try:
        new_lines = sov.clean_lines(raw_lines, sov.lines(job), lambda: "sov-" + secrets.token_hex(8))
        bp = sov.to_bp(op_pct)
    except sov.SovError as exc:
        raise GateError("Schedule of values not saved.", [str(exc)])
    if not new_lines:
        if job.get("sov"):
            job.pop("sov", None)
            job["completed_value_cents"] = 0
            audit(state, job_id, "sov_removed", f"{actor} removed the schedule of values; progress is entered in dollars again.", actor)
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
                    "Adjust a line or the overhead and profit, or save it and change the contract amount to the schedule total.",
                ],
            )
        job["contract_cents"] = figures["contract_cents"]
        audit(
            state,
            job_id,
            "contract_changed",
            f"{actor} changed the contract amount from {money(contract)} to {money(figures['contract_cents'])} to match the schedule of values.",
            actor,
        )
    earlier = job.get("completed_value_cents", 0)
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
        + (f" The earlier completed-to-date of {money(earlier)} is replaced by quantities." if earlier and not job["completed_value_cents"] else ""),
        actor,
    )
    return job


def record_sov_progress(
    state: dict[str, Any],
    job_id: str,
    done: Any,
    notes: str = "",
    actor: str = "owner",
    stored: Any = None,
    period_end: Any = "",
) -> dict[str, Any]:
    """Record quantities completed to date, and materials stored, on schedule lines; draft the
    progress pay app. `done` maps line id -> quantity to date; `stored` maps line id -> dollars of
    materials on site and not yet installed (G703 column F). period_end: the date it runs to (R4-04)."""
    job = _open_job(state, job_id)
    if not sov.has_sov(job):
        raise GateError("This job has no schedule of values. Enter completed-to-date in dollars, or add a schedule first.")
    if job.get("work_status") == "complete":
        raise GateError("Job is already marked done. Draft the remaining pay app instead.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft:
        raise GateError(f"{open_draft['number']} is still a draft. Send or void it, then record more progress.")
    runs_to = period_to(job, period_end)
    done = done if isinstance(done, dict) else {}
    stored = stored if isinstance(stored, dict) else {}
    # Work recorded and not on a pay app (one pulled back, or a draft deleted): the same
    # quantities draft it again.
    rebill = job_money(job)["unbilled_completed_cents"] > 0
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
        name = f"{line.get('item', '')}. {line['description']}".strip(". ")
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
            errors.append(f"{name}: {sov.fmt_qty(qty)} is more than the scheduled {sov.fmt_qty(line['qty_milli'])} {line['unit']}. Extra scope is a change order.")
        elif qty < billed_qty.get(line_id, 0):
            errors.append(f"{name}: {sov.fmt_qty(billed_qty[line_id])} {line['unit']} is already billed; the quantity to date can't go below that.")
        elif sov.line_completed(trial_line) + store > line["scheduled_cents"]:
            errors.append(f"{name}: work done plus {money(store)} stored is more than the line's {money(line['scheduled_cents'])}. Lower the stored materials as they are installed.")
        elif sov.line_to_date(trial_line) < already["by_line"].get(line_id, 0):
            errors.append(f"{name}: {money(already['by_line'][line_id])} is already billed on this line; work done plus stored materials can't go below that.")
        elif qty != line["done_milli"] or store != int(line.get("stored_cents") or 0):
            changes.append((line, qty, store))
    if errors:
        raise GateError("Progress not recorded.", errors)
    if not changes and not rebill:
        raise GateError("Nothing changed. Enter quantities to date or stored materials that differ from the last ones recorded.")
    summary = []
    for line, qty, store in changes:
        part = f"{line['description']} {sov.fmt_qty(line['done_milli'])} → {sov.fmt_qty(qty)} {line['unit']}"
        if store != int(line.get("stored_cents") or 0):
            part += f", stored {money(int(line.get('stored_cents') or 0))} → {money(store)}"
        summary.append(part)
        line["done_milli"] = qty
        line["stored_cents"] = store
    job["completed_value_cents"] = sov.totals(job)["to_date_cents"]
    notes = (notes or "").strip()[:2000]
    audit(
        state,
        job_id,
        "record_progress",
        (f"{actor} recorded progress to date: {'; '.join(summary)}." if summary else f"{actor} billed the work recorded before, unchanged.")
        + f" Completed and stored to date {money(job['completed_value_cents'])}."
        + (f" {notes}" if notes else ""),
        actor,
    )
    return draft_invoice(state, job_id, actor="duracash", period_end=runs_to, notes=notes)


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
        out += ["", "Paperwork:"]
        out += [f"  [{'x' if p.get('present') else ' '}] {p['label']}" for p in packet]
    return "\n".join(out).strip()


def _packet_file_ids(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> list[str]:
    """Stored files behind the records attached to this pay app's packet."""
    records = {r["id"]: r for r in job.get("records", [])}
    ids: list[str] = []
    for item in invoice.get("packet") or []:
        for att in item.get("attachments", []):
            for file_id in (records.get(att.get("record_id")) or {}).get("file_ids", []):
                if file_id not in ids:
                    ids.append(file_id)
    return ids


def _sov_draft_lock(job: dict[str, Any]) -> dict[str, Any] | None:
    """The unsent draft pay app that is the only thing keeping the schedule fixed, if there is one.
    Wording only: the rule (no edits while any pay app exists) is the same either way."""
    live = sov.live_invoices(job)
    if job.get("closed") or job.get("work_status") == "complete" or not live:
        return None
    if any(inv.get("status") not in DRAFT_STATUSES for inv in live):
        return None
    return live[0]


def _sov_view(job: dict[str, Any]) -> dict[str, Any]:
    """What the job page shows for the schedule of values: per-line figures and whether it can be edited.
    draft_lock names the draft pay app when deleting it would make the schedule editable again."""
    editable = not job.get("closed") and job.get("work_status") != "complete" and not sov.live_invoices(job)
    draft = _sov_draft_lock(job)
    draft_lock = {"id": draft["id"], "number": draft["number"]} if draft else None
    if not sov.has_sov(job):
        return {"present": False, "editable": editable, "draft_lock": draft_lock}
    figures = sov.totals(job)
    already = sov.billed(job)
    rows = []
    for line in sov.lines(job):
        to_date = sov.line_to_date(line)
        rows.append({
            **line,
            "to_date_cents": to_date,
            "billed_cents": already["by_line"].get(line["id"], 0),
            "billed_qty_milli": already["qty_by_line"].get(line["id"], 0),
            "pct": (100 * to_date) // line["scheduled_cents"] if line["scheduled_cents"] else 0,
            "lump_sum": line["unit"].upper() == "LS" and line["qty_milli"] == 1000,
        })
    return {
        "present": True,
        "editable": editable,
        "draft_lock": draft_lock,
        "op_bp": sov.op_bp(job),
        "op_billed_cents": already["op_cents"],
        "totals": figures,
        "lines": rows,
    }


def _copy_list(payer: dict[str, Any]) -> list[str]:
    """Email addresses in the payer's "copy" field (who to copy on pay apps). Names without an address are skipped."""
    found = re.findall(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", str(payer.get("copy") or ""))
    out: list[str] = []
    for addr in found:
        if addr.lower() != str(payer.get("ap_email", "")).lower() and addr.lower() not in [a.lower() for a in out]:
            out.append(addr)
    return out[:5]


def _waiver_file_ids(state: dict[str, Any], job: dict[str, Any]) -> list[str]:
    """The newest waiver on the job record that hasn't gone out in an earlier waiver email."""
    sent = {fid for e in state.get("email_log", []) if e.get("job_id") == job["id"] and e.get("kind") == "waiver" for fid in e.get("attachments", [])}
    for record in reversed(job.get("records", [])):
        if record.get("kind") == "waiver":
            fresh = [fid for fid in record.get("file_ids", []) if fid not in sent]
            if fresh:
                return fresh
    return []


# Parts of the domain kept in their own modules (each lists what it exports in __all__).
from .validate import *  # noqa: E402,F401,F403
from .tasks import *  # noqa: E402,F401,F403
from .daily import *  # noqa: E402,F401,F403
