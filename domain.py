"""Billdable domain: job money file, packet gate, invoice draft, payment match.

Source accounting data is treated as external. This module owns the operating
record Billdable builds on top of it: what is billable, what is blocking send,
who approved, and whether cash matched.
"""

from __future__ import annotations

import re
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import sov
import trial

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


class GateError(Exception):
    def __init__(self, message: str, blockers: list[str] | None = None):
        super().__init__(message)
        self.blockers = blockers or [message]


def job_money(job: dict[str, Any]) -> dict[str, int]:
    approved = sum(co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved")
    pending = sum(co["amount_cents"] for co in job["change_orders"] if co["status"] == "pending")
    contract_total = job["contract_cents"] + approved
    billed = sum(inv["amount_cents"] for inv in job["invoices"] if inv["status"] in BILLED_STATUSES)
    collected = sum(p["amount_cents"] for p in job["payments"])
    draft = sum(inv["amount_cents"] for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES)
    # Pay app amounts are net of retainage. Retainage withheld on a pay app is
    # stored on the invoice; older records keep it in retainage_held_cents.
    retainage_on_billed = sum(inv.get("retainage_cents", 0) for inv in job["invoices"] if inv["status"] in BILLED_STATUSES)
    retainage_on_drafts = sum(inv.get("retainage_cents", 0) for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES)
    retainage_held = job.get("retainage_held_cents", 0) + retainage_on_billed
    retainage_released = job.get("retainage_released_cents", 0)
    if sov.has_sov(job):
        # Billed line by line from the schedule of values.
        unbilled_completed = sov.unbilled(job)
    else:
        unbilled_completed = max(
            0,
            job.get("completed_value_cents", contract_total) - billed - draft - retainage_held - retainage_on_drafts,
        )
    outstanding = max(0, billed - collected)
    return {
        "contract_cents": job["contract_cents"],
        "approved_co_cents": approved,
        "pending_co_cents": pending,
        "contract_total_cents": contract_total,
        "billed_cents": billed,
        "draft_cents": draft,
        "collected_cents": collected,
        "outstanding_cents": outstanding,
        "unbilled_completed_cents": unbilled_completed,
        "retainage_held_cents": retainage_held,
        "retainage_released_cents": retainage_released,
        "retainage_open_cents": max(0, retainage_held - retainage_released),
    }


def close_blockers(job: dict[str, Any]) -> list[str]:
    """Everything that must be settled before a job can be closed for the record."""
    if job.get("closed"):
        return ["Job is already closed."]
    m = job_money(job)
    out = []
    if job.get("work_status") != "complete":
        out.append("Work is not marked done.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft:
        out.append(f"{open_draft['number']} is still an open draft.")
    if any(co["status"] == "pending" for co in job["change_orders"]):
        out.append("A change order is still pending.")
    if m["unbilled_completed_cents"] > 0:
        out.append(f"{money(m['unbilled_completed_cents'])} of completed work is not billed.")
    if m["outstanding_cents"] > 0:
        out.append(f"{money(m['outstanding_cents'])} is still outstanding.")
    if m["retainage_open_cents"] > 0:
        out.append(f"{money(m['retainage_open_cents'])} of retainage is still open.")
    if job.get("waiver_due") and not job.get("waiver_sent"):
        out.append("A waiver is still due.")
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


def unsigned_cos_in_billing(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [co for co in job["change_orders"] if co["status"] == "pending" and co.get("included_in_billing")]


def blocker_lines(job: dict[str, Any]) -> list[str]:
    lines = [item["blocker"] for item in missing_packet(job)]
    for co in unsigned_cos_in_billing(job):
        lines.append(
            f"Change Order {co['number']} is included in the billing amount but has not been signed."
        )
    return lines


def status_label(job: dict[str, Any]) -> str:
    """A status names the blocker. Never return a bare 'Pending'."""
    if job.get("closed"):
        return "Closed"
    lines = blocker_lines(job)
    open_invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] != "void"), None)
    draft_open = open_invoice and open_invoice["status"] in {"draft", "awaiting_approval"}
    if draft_open and lines:
        return lines[0]
    if draft_open and open_invoice["status"] == "awaiting_approval":
        return "Pay app ready for review"
    if draft_open:
        return "Pay app draft in progress"
    if open_invoice and open_invoice["status"] == "sent":
        return job.get("external_status") or "Submitted, awaiting acknowledgment"
    if open_invoice and open_invoice["status"] == "approved":
        return "Invoice approved, payment not received"
    if open_invoice and open_invoice["status"] == "partial":
        return "Partial payment received"
    if job.get("work_status") == "complete" and lines and job_money(job)["unbilled_completed_cents"] > 0:
        return lines[0]
    if job.get("waiver_due") and not job.get("waiver_sent"):
        return "Payment received, waiver not sent"
    if job.get("work_status") == "complete" and job_money(job)["retainage_open_cents"] > 0:
        return "Retainage outstanding"
    if not open_invoice:
        return "Pay app not drafted" if job.get("work_status") == "complete" else "In progress"
    if open_invoice["status"] == "paid":
        return "Paid"
    return open_invoice["status"].replace("_", " ").capitalize()


def audit(state: dict[str, Any], job_id: str, action: str, detail: str, actor: str = "billdable") -> None:
    state["audit"].append(
        {
            "id": f"aud-{len(state['audit']) + 1}",
            "at": utcnow(),
            "job_id": job_id,
            "actor": actor,
            "action": action,
            "detail": detail,
        }
    )


def record_progress(
    state: dict[str, Any],
    job_id: str,
    completed_value_cents: int,
    notes: str = "",
    actor: str = "owner",
) -> dict[str, Any]:
    """Set completed-to-date and draft a progress pay app. Does not mark the job done."""
    job = _open_job(state, job_id)
    if job.get("work_status") == "complete":
        raise GateError("Job is already marked done. Draft the remaining pay app instead.")
    if sov.has_sov(job):
        raise GateError("This job bills from its schedule of values. Enter quantities completed on each line instead.")
    contract_total = job["contract_cents"] + sum(
        co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved"
    )
    if completed_value_cents <= 0:
        raise GateError("Completed-to-date must be greater than zero.")
    if completed_value_cents > contract_total:
        raise GateError("Completed-to-date cannot exceed the contract total.")
    if completed_value_cents <= job.get("completed_value_cents", 0):
        raise GateError("Completed-to-date must be higher than the last recorded amount.")
    job["completed_value_cents"] = int(completed_value_cents)
    if notes:
        job["progress_notes"] = notes
    audit(
        state,
        job_id,
        "record_progress",
        f"{actor} recorded completed-to-date {money(completed_value_cents)}.{' ' + notes if notes else ''}",
        actor,
    )
    return draft_invoice(state, job_id, actor="billdable")


def mark_done(state: dict[str, Any], job_id: str, actor: str = "owner", notes: str = "", evidence: list[str] | None = None) -> dict[str, Any]:
    job = _open_job(state, job_id)
    if job.get("work_status") == "complete":
        raise GateError("Job is already marked done.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft:
        raise GateError(
            f"{open_draft['number']} is still a draft. Send it first, then mark the job done so the final pay app covers the rest."
        )
    job["work_status"] = "complete"
    job["completed_at"] = job.get("completed_at") or utcnow()
    job["done_notes"] = notes or job.get("done_notes", "")
    if evidence:
        job.setdefault("evidence", []).extend(evidence)
    job["completed_value_cents"] = job["contract_cents"] + sum(
        co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved"
    )
    for line in sov.lines(job):
        line["done_milli"] = line["qty_milli"]
    audit(state, job_id, "job_done", f"{actor} marked {job['name']} done. Notes: {notes or 'none'}.", actor)
    _ensure_closeout_packet(state, job)
    return draft_invoice(state, job_id, actor="billdable")


def draft_invoice(state: dict[str, Any], job_id: str, actor: str = "billdable", strict: bool = False) -> dict[str, Any]:
    """Draft a pay app for unbilled completed work, withholding retainage.

    strict=True is for the explicit "Draft pay app" button: a no-op raises
    instead of being logged silently, so the user is told why nothing happened.
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
    number = f"PA-{job['code']}-{len(job['invoices']) + 1:02d}"
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
    packet = _packet_for_pay_app(state, job, invoice_id)
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
    }
    job["invoices"].append(invoice)
    audit(
        state,
        job_id,
        "draft_invoice",
        f"Drafted {number} for {money(amount)}"
        + (f" ({money(gross)} less {money(retained)} retainage)" if retained else "")
        + f" with a {len(packet)}-item packet. Blockers: {len(blockers)}.",
        actor,
    )
    return job


def send_pencil(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    """Send the open draft to the payer as a pencil copy to agree quantities before the formal pay app."""
    job = _open_job(state, job_id)
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if not draft:
        raise GateError("Draft a pay app first. The pencil copy is the draft sent for review.")
    payer = _payer(state, job["payer_id"])
    subject = f"Pencil copy for review: {draft['number']} — {job['name']}"
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Here is the pencil copy of {draft['number']} for {job['name']}, attached as a PDF marked PENCIL COPY. "
        "Please confirm the quantities and change orders, or send corrections, before we submit the formal pay app.\n\n"
        f"{pay_app_text(state, job, draft)}\n\nApproved by {actor}."
    )
    _record_email(state, job_id, "pencil", payer["ap_email"], subject, body, actor, payapp_invoice_id=draft["id"])
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
        raise GateError("No pencil copy is waiting for a response.")
    draft["pencil_status"] = "agreed" if agreed else "corrections"
    draft["pencil_note"] = (note or "").strip()[:2000]
    draft["pencil_answered_at"] = utcnow()
    job["external_status"] = None
    what = "agreed to" if agreed else "asked for corrections on"
    audit(state, job_id, "pencil_response", f"{actor} recorded that the payer {what} the pencil copy of {draft['number']}. {draft['pencil_note']}".strip(), actor)
    return job


def resolve_packet_item(state: dict[str, Any], job_id: str, item_id: str, actor: str = "owner", reason: str = "") -> dict[str, Any]:
    job = _open_job(state, job_id)
    item, _invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Packet item not found.")
    reason = (reason or "").strip() if isinstance(reason, str) else ""
    if not item.get("attachments") and not reason:
        raise GateError(f"Attach a job record to {item['label']}, or say why it is present, before marking it present.")
    item["present"] = True
    item["resolved_at"] = utcnow()
    if reason:
        item["present_reason"] = reason
    audit(state, job_id, "packet_resolved", f"{item['label']} marked present{': ' + reason if reason else ''}.", actor)
    _refresh_draft_gate(job)
    return job


def approve_and_send(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    job = _open_job(state, job_id)
    blockers = blocker_lines(job)
    if blockers:
        raise GateError("Pay app blocked.", blockers)
    invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] in {"draft", "awaiting_approval"}), None)
    if not invoice:
        raise GateError("No draft pay app to send.")
    invoice["status"] = "sent"
    invoice["sent_at"] = utcnow()
    invoice["approved_by"] = actor
    payer = _payer(state, job["payer_id"])
    job["external_status"] = f"Submitted to {payer['name']}, awaiting {payer['approver']}"
    subject = f"Pay application {invoice['number']} — {job['name']}"
    files = _packet_file_ids(state, job, invoice)
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Please find pay application {invoice['number']} for {job['name']} attached as a PDF. Every packet item is accounted for"
        + (f"; {len(files)} packet document{'s are' if len(files) != 1 else ' is'} attached too." if files else ".")
        + f"\n\n{pay_app_text(state, job, invoice)}\n\nApproved by {actor}."
    )
    _record_email(state, job_id, "pay_app", payer["ap_email"], subject, body, actor, attachments=files, cc=_copy_list(payer),
                  payapp_invoice_id=invoice["id"])
    audit(
        state,
        job_id,
        "approve_send",
        f"{actor} approved and sent {invoice['number']} for {money(invoice['amount_cents'])} to {payer['ap_email']}.",
        actor,
    )
    return job


def record_follow_up(state: dict[str, Any], job_id: str, actor: str = "billdable") -> dict[str, Any]:
    """Draft a follow-up on the oldest unpaid pay app once the payer's window has passed."""
    job = _open_job(state, job_id)
    invoice = next((inv for inv in job["invoices"] if inv["status"] in {"sent", "partial"}), None)
    if not invoice:
        raise GateError("No sent pay app to follow up.")
    payer = _payer(state, job["payer_id"])
    days = _int_or(payer.get("follow_up_days"), 7)
    last = invoice.get("last_follow_up_at") or invoice.get("sent_at")
    if last:
        due = datetime.fromisoformat(last) + timedelta(days=days)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < due:
            raise GateError(
                f"Follow-up on {invoice['number']} is not due until {due.date().isoformat()} "
                f"({payer['name']} expects {days} days between contacts)."
            )
    now = utcnow()
    invoice["last_follow_up_at"] = now
    job["external_status"] = f"Follow-up sent to {payer.get('approver') or payer['name']}"
    job["follow_ups"] = job.get("follow_ups", 0) + 1
    subject = f"Following up: {invoice['number']} — {job['name']}"
    body = (
        f"Hi {payer.get('approver') or payer['name']}, checking on {invoice['number']} for {job['name']} "
        f"({money(invoice['amount_cents'])}), sent {(invoice.get('sent_at') or '')[:10]}. "
        f"Is anything missing on your side? Approved by {actor}."
    )
    _record_email(state, job_id, "follow_up", payer["ap_email"], subject, body, actor)
    audit(
        state,
        job_id,
        "follow_up",
        f"{actor} followed up on {invoice['number']} with {payer.get('approver', '')} at {payer['ap_email']}. Cadence: {days} days.",
        actor,
    )
    return job


def match_payment(
    state: dict[str, Any],
    job_id: str,
    amount_cents: int,
    reference: str,
    actor: str = "billdable",
    invoice_id: str | None = None,
) -> dict[str, Any]:
    job = _open_job(state, job_id)
    open_invoices = [inv for inv in job["invoices"] if inv["status"] in OPEN_FOR_PAYMENT]
    if not open_invoices:
        raise GateError("No open invoice to match.")
    if invoice_id:
        invoice = next((inv for inv in open_invoices if inv["id"] == invoice_id), None)
        if not invoice:
            raise GateError("That pay app is not open for payment.")
    elif len(open_invoices) == 1:
        invoice = open_invoices[0]
    else:
        raise GateError(
            "More than one pay app is open. Choose which pay app this payment is for.",
            [f"{inv['number']} ({money(inv['amount_cents'])})" for inv in open_invoices],
        )
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        raise GateError("Payment amount must be greater than zero.")
    already = sum(p["amount_cents"] for p in job["payments"] if p["invoice_id"] == invoice["id"])
    remaining = invoice["amount_cents"] - already
    if amount_cents > remaining:
        raise GateError(f"Payment of {money(amount_cents)} is more than the {money(remaining)} still owed on {invoice['number']}.")
    reference = reference.strip() if isinstance(reference, str) else ""
    if reference and any(p.get("reference") == reference for p in job["payments"]):
        raise GateError(f"Payment {reference} is already recorded on this job.")
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
        "matched_at": utcnow(),
    }
    job["payments"].append(payment)
    collected_for_invoice = sum(p["amount_cents"] for p in job["payments"] if p["invoice_id"] == invoice["id"])
    if collected_for_invoice >= invoice["amount_cents"]:
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
        f"Matched {money(amount_cents)} ({reference}) to {invoice['number']}.",
        actor,
    )
    return job


def send_waiver(state: dict[str, Any], job_id: str, actor: str = "owner", invoice_id: str | None = None) -> dict[str, Any]:
    """One waiver per paid pay app. Older records keep a single job-level flag."""
    job = _open_job(state, job_id)
    pending = [inv for inv in job["invoices"] if inv.get("waiver_due") and not inv.get("waiver_sent")]
    if invoice_id:
        pending = [inv for inv in pending if inv["id"] == invoice_id]
    payer = _payer(state, job["payer_id"])
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
        which = f" for {invoice['number']}"
        detail = f"payment of {money(invoice['amount_cents'])} on {invoice['number']}"
    else:
        raise GateError("No waiver is due. Payment has not been matched.")
    job["waiver_sent_at"] = now
    job["external_status"] = "Waiver sent"
    subject = f"Lien waiver{which} — {job['name']}"
    files = _waiver_file_ids(state, job)
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Thank you for the {detail} on {job['name']}. "
        + ("Our lien waiver for it is attached." if files else "Our signed lien waiver for it will follow separately.")
        + f"\n\nApproved by {actor}."
    )
    _record_email(state, job_id, "waiver", payer["ap_email"], subject, body, actor, attachments=files)
    _sync_waiver(job)
    audit(state, job_id, "send_waiver", f"{actor} approved sending the waiver{which}.", actor)
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


def chase_retainage(state: dict[str, Any], job_id: str, actor: str = "billdable") -> dict[str, Any]:
    job = _open_job(state, job_id)
    open_retainage = job_money(job)["retainage_open_cents"]
    if open_retainage <= 0:
        raise GateError("No open retainage.")
    if job.get("work_status") != "complete":
        raise GateError("Retainage is released at closeout. Mark the job done first.")
    if job.get("closeout_packet"):
        missing = [item for item in job["closeout_packet"] if item["required"] and not item["present"]]
    else:
        missing = missing_packet(job)
    if missing:
        raise GateError("Closeout packet incomplete.", [item["blocker"] for item in missing])
    payer = _payer(state, job["payer_id"])
    days = _int_or(payer.get("follow_up_days"), 7)
    last = job.get("last_retainage_chase_at")
    if last:
        due = datetime.fromisoformat(last) + timedelta(days=days)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < due:
            raise GateError(
                f"The last retainage request went out {last[:10]}. The next is due {due.date().isoformat()} "
                f"({payer['name']} expects {days} days between contacts)."
            )
    job["retainage_chases"] = job.get("retainage_chases", 0) + 1
    job["last_retainage_chase_at"] = utcnow()
    job["external_status"] = f"Retainage follow-up sent to {payer['approver']}"
    subject = f"Retainage release request — {job['name']}"
    files = _packet_file_ids(state, job, {"packet": job.get("closeout_packet") or job.get("packet") or []})
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"The work on {job['name']} is complete and the closeout items are done. "
        f"Please release the {money(open_retainage)} retainage held on this job."
        + (f" The closeout documents on file are attached ({len(files)})." if files else "")
        + f"\n\nApproved by {actor}."
    )
    _record_email(state, job_id, "retainage", payer["ap_email"], subject, body, actor, attachments=files)
    audit(
        state,
        job_id,
        "chase_retainage",
        f"Asked {payer['name']} to release {money(open_retainage)} retainage.",
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


def snapshot(state: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(state)
    for job in out["jobs"]:
        job["money"] = job_money(job)
        job["blockers"] = blocker_lines(job)
        job["status_label"] = status_label(job)
        job["payer"] = _payer(out, job["payer_id"])
        job["close_blockers"] = close_blockers(job)
        job.setdefault("closeout_packet", [])
        job.setdefault("evidence", [])
        job.setdefault("done_notes", "")
        job["sov_view"] = _sov_view(job)
    out.setdefault("email_log", [])
    out["trial"] = trial.status(state)
    out["totals"] = {
        "unbilled_cents": sum(j["money"]["unbilled_completed_cents"] for j in out["jobs"]),
        "outstanding_cents": sum(j["money"]["outstanding_cents"] for j in out["jobs"]),
        "retainage_cents": sum(j["money"]["retainage_open_cents"] for j in out["jobs"]),
        "blocked_jobs": sum(1 for j in out["jobs"] if j["blockers"] and not j.get("closed")),
    }
    return out


def _refresh_draft_gate(job: dict[str, Any]) -> None:
    invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] in {"draft", "awaiting_approval"}), None)
    if not invoice:
        return
    invoice["status"] = "draft" if blocker_lines(job) else "awaiting_approval"


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
    text_fields = {"approver", "ap_email", "packet", "rejects_when", "copy"}
    day_fields = {"follow_up_days", "usual_approval_days"}
    clean: dict[str, Any] = {}
    errors = []
    for k, v in updates.items():
        if v is None:
            continue
        if k in text_fields:
            if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                errors.append(f"{k.replace('_', ' ').capitalize()} must be text.")
                continue
            clean[k] = str(v).strip()
        elif k in day_fields:
            days = _int_or(v, None)
            if days is None or not 1 <= days <= 365:
                errors.append(f"{k.replace('_', ' ').capitalize()} must be a whole number from 1 to 365.")
                continue
            clean[k] = days
    if "ap_email" in clean and not EMAIL_RE.match(clean["ap_email"]):
        errors.append("AP email must be a real email address.")
    if "approver" in clean and not clean["approver"]:
        errors.append("Approver cannot be empty.")
    if errors:
        raise GateError("Playbook not saved.", errors)
    changed = []
    for k, v in clean.items():
        payer[k] = v
        changed.append(k)
    audit(state, "", "playbook_update", f"{actor} updated {payer['name']}: {', '.join(changed) or 'no change'}.", actor)
    return payer


def add_change_order(state: dict[str, Any], job_id: str, title: str, amount_cents: int, actor: str = "owner") -> dict[str, Any]:
    job = _open_job(state, job_id)
    title = title.strip() if isinstance(title, str) else ""
    errors = []
    if not title:
        errors.append("Change order title is required.")
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        errors.append("Change order amount must be greater than zero.")
    elif amount_cents > MAX_AMOUNT_CENTS:
        errors.append(f"Change order amount cannot exceed {money(MAX_AMOUNT_CENTS)}.")
    if errors:
        raise GateError("Cannot add the change order.", errors)
    number = max((co["number"] for co in job["change_orders"]), default=0) + 1
    co = {
        "id": f"co-{job['code'].lower()}-{number}",
        "number": number,
        "title": title,
        "amount_cents": amount_cents,
        "status": "pending",
        "included_in_billing": False,
    }
    job["change_orders"].append(co)
    audit(state, job_id, "add_co", f"{actor} added CO {number} — {title} for {money(amount_cents)}.", actor)
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
        job["completed_value_cents"] = max(job.get("completed_value_cents", 0), contract_total)
    audit(state, job_id, "approve_co", f"{actor} approved CO {co['number']} — {co['title']}.", actor)
    _refresh_draft_gate(job)
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
        raise GateError(f"CO {co['number']} is on {open_draft['number']}. Void that draft first, then withdraw the CO.")
    co["status"] = "withdrawn"
    co["included_in_billing"] = False
    co["withdrawn_at"] = utcnow()
    audit(state, job_id, "withdraw_co", f"{actor} withdrew CO {co['number']} — {co['title']}.", actor)
    return job


def void_draft(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    """Throw away the open draft pay app. Its work goes back to unbilled."""
    job = _open_job(state, job_id)
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if not draft:
        raise GateError("There is no open draft to void.")
    draft["status"] = "void"
    draft["voided_at"] = utcnow()
    audit(state, job_id, "void_draft", f"{actor} voided draft {draft['number']} ({money(draft['amount_cents'])}).", actor)
    return job


def include_change_order(state: dict[str, Any], job_id: str, co_id: str, actor: str = "owner") -> dict[str, Any]:
    """Put a pending CO's dollars on the next pay app. Send stays blocked until it is signed."""
    job = _open_job(state, job_id)
    co = next((c for c in job["change_orders"] if c["id"] == co_id), None)
    if not co:
        raise GateError("Change order not found.")
    if co["status"] != "pending":
        raise GateError(f"CO {co['number']} is already approved; it is billed as contract work.")
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
    audit(state, job_id, "learn_rejection", f"{actor} taught playbook: {payer['name']} rejects when {reason}.", actor)
    return job


def close_job(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
    blockers = close_blockers(job)
    if blockers:
        raise GateError("Cannot close this job yet.", blockers)
    job["closed"] = True
    job["closed_at"] = utcnow()
    audit(state, job_id, "close_job", f"{actor} closed {job['name']} for the record.", actor)
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
        raise GateError("Packet item not found.")
    item.setdefault("attachments", []).append({"name": name, "added_at": utcnow(), "added_by": actor})
    item["present"] = True
    audit(state, job_id, "packet_attachment", f"{actor} attached {name} to {item['label']}.", actor)
    _refresh_draft_gate(job)
    return job


def import_sample_qbo_payment(state: dict[str, Any], job_id: str, actor: str = "billdable") -> dict[str, Any]:
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
        raise GateError("Payer not found.")
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


def _packet_for_pay_app(state: dict[str, Any], job: dict[str, Any], invoice_id: str) -> list[dict[str, Any]]:
    """Reuse an existing job packet (older records), else build one from the payer playbook."""
    if job.get("packet"):
        return job["packet"]
    payer = next((p for p in state["payers"] if p["id"] == job["payer_id"]), {})
    labels = [l for l in parse_packet_text(payer.get("packet", "")) if not _is_closeout(l)]
    if labels:
        return _items_from_labels(invoice_id, labels)
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
        raise GateError("Packet item not found.")
    if not record:
        raise GateError("Job record not found.")
    item.setdefault("attachments", []).append({
        "name": record["label"],
        "record_id": record_id,
        "added_at": utcnow(),
        "added_by": actor,
    })
    item["present"] = True
    item["resolved_at"] = utcnow()
    which = invoice["number"] if invoice else "the job packet"
    audit(state, job_id, "attach_record", f"{actor} attached {record['label']} to {item['label']} on {which}.", actor)
    _refresh_draft_gate(job)
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
    packet_notes: str = "",
    contract_number: str = "",
    actor: str = "owner",
) -> dict[str, Any]:
    """Add a job and its record. The packet is created later, with a pay app."""
    name = (name or "").strip()
    customer_name = (customer_name or "").strip()
    address = (address or "").strip()
    approver = (approver or "").strip()
    ap_email = (ap_email or "").strip()
    phone = (phone or "").strip()
    role = role if role in {"Owner", "GC"} else "Owner"
    packet_notes = (packet_notes or "").strip()
    contract_number = (contract_number or "").strip()

    missing = []
    if not name:
        missing.append("Job name is required.")
    if not address:
        missing.append("Job address is required.")
    if not customer_name:
        missing.append("Customer name is required.")
    if not approver:
        missing.append("Approver is required.")
    if not ap_email or not EMAIL_RE.match(ap_email):
        missing.append("A real AP email is required.")
    if not isinstance(contract_cents, int) or isinstance(contract_cents, bool) or contract_cents <= 0:
        missing.append("Contract amount must be greater than zero.")
    elif contract_cents > MAX_AMOUNT_CENTS:
        missing.append(f"Contract amount cannot exceed {money(MAX_AMOUNT_CENTS)}.")
    if retainage_pct < 0 or retainage_pct > 20:
        missing.append("Retainage must be between 0 and 20 percent.")
    if missing:
        raise GateError("Cannot create the job.", missing)

    payer = next((p for p in state["payers"] if p["name"].lower() == customer_name.lower()), None)
    packet_rule = packet_notes or "Signed contract, conditional waiver, completion photos, and pay-app backup."
    if role == "GC":
        packet_rule = packet_notes or "AIA-style pay app, schedule of values, lien waiver, insurance certificate, daily tickets."
    if not payer:
        payer = {
            "id": "payer-" + secrets.token_hex(3),
            "name": customer_name,
            "role": role,
            "approver": approver,
            "ap_email": ap_email,
            "phone": phone,
            "packet": packet_rule,
            "follow_up_days": 5 if role == "GC" else 7,
            "usual_approval_days": 21 if role == "GC" else 14,
            "rejects_when": "Missing required packet items.",
            "copy": "",
        }
        state["payers"].append(payer)
    else:
        # Keep the payer's existing contact. A new job must not silently redirect
        # where every other job's pay apps go; changes belong in the playbook.
        differs = [
            label
            for label, new, old in (
                ("approver", approver, payer.get("approver", "")),
                ("AP email", ap_email, payer.get("ap_email", "")),
            )
            if new and new.lower() != str(old).lower()
        ]
        if differs:
            audit(
                state,
                "",
                "payer_kept",
                f"{actor} entered a different {' and '.join(differs)} for {payer['name']}; kept the playbook values. Change them in the Payer Playbook.",
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
    records = []
    if contract_number:
        records.append({
            "id": "rec-" + secrets.token_hex(3),
            "kind": "contract",
            "label": f"Contract {contract_number}",
            "note": "",
            "added_at": utcnow(),
            "added_by": actor,
        })
    if packet_notes:
        records.append({
            "id": "rec-" + secrets.token_hex(3),
            "kind": "note",
            "label": "Packet requirements",
            "note": packet_notes,
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
        f"{actor} created {job['name']} for {payer['name']} ({money(job['contract_cents'])}). Packet is created with the first pay app.",
        actor,
    )
    return job


# ---------- schedule of values ----------

def _add_co_line(job: dict[str, Any], co: dict[str, Any]) -> None:
    """An approved change order becomes its own schedule line, like a CO line on a G703."""
    if any(line.get("co_id") == co["id"] for line in sov.lines(job)):
        return
    billed_already = any(
        entry.get("co_id") == co["id"] for inv in sov.live_invoices(job) for entry in inv.get("lines", [])
    )
    job["sov"]["lines"].append({
        "id": f"sov-co-{co['number']}",
        "item": f"CO{co['number']}",
        "description": f"CO {co['number']} — {co['title']}",
        "unit": "LS",
        "qty_milli": 1000,
        "rate_cents": co["amount_cents"],
        "scheduled_cents": co["amount_cents"],
        # Billed while unsigned, or the job is done: the work is complete.
        "done_milli": 1000 if billed_already or job.get("work_status") == "complete" else 0,
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
    job["sov"] = candidate
    for co in job["change_orders"]:
        if co["status"] == "approved":
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
) -> dict[str, Any]:
    """Record quantities completed to date on schedule lines and draft the progress pay app."""
    job = _open_job(state, job_id)
    if not sov.has_sov(job):
        raise GateError("This job has no schedule of values. Enter completed-to-date in dollars, or add a schedule first.")
    if job.get("work_status") == "complete":
        raise GateError("Job is already marked done. Draft the remaining pay app instead.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft:
        raise GateError(f"{open_draft['number']} is still a draft. Send or void it, then record more progress.")
    if not isinstance(done, dict) or not done:
        raise GateError("Enter the quantity completed to date on at least one line.")
    by_id = {line["id"]: line for line in sov.lines(job)}
    already = sov.billed(job)
    billed_qty = already["qty_by_line"]
    changes, errors = [], []
    for line_id, value in done.items():
        line = by_id.get(line_id)
        if not line:
            errors.append(f"Line {str(line_id)[:20]} is not on this job's schedule.")
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        name = f"{line.get('item', '')}. {line['description']}".strip(". ")
        try:
            qty = sov.to_milli(value, name)
        except sov.SovError as exc:
            errors.append(str(exc))
            continue
        if qty < 0:
            errors.append(f"{name}: quantity can't be negative.")
        elif qty > line["qty_milli"]:
            errors.append(f"{name}: {sov.fmt_qty(qty)} is more than the scheduled {sov.fmt_qty(line['qty_milli'])} {line['unit']}. Extra scope is a change order.")
        elif qty < billed_qty.get(line_id, 0):
            errors.append(f"{name}: {sov.fmt_qty(billed_qty[line_id])} {line['unit']} is already billed; the quantity to date can't go below that.")
        elif sov.line_to_date({**line, "done_milli": qty}) < already["by_line"].get(line_id, 0):
            errors.append(f"{name}: {money(already['by_line'][line_id])} is already billed on this line; the amount to date can't go below that.")
        elif qty != line["done_milli"]:
            changes.append((line, qty))
    if errors:
        raise GateError("Progress not recorded.", errors)
    if not changes:
        raise GateError("Nothing changed. Enter quantities to date that differ from the last ones recorded.")
    summary = []
    for line, qty in changes:
        summary.append(f"{line['description']} {sov.fmt_qty(line['done_milli'])} → {sov.fmt_qty(qty)} {line['unit']}")
        line["done_milli"] = qty
    job["completed_value_cents"] = sov.totals(job)["to_date_cents"]
    notes = (notes or "").strip()[:2000]
    if notes:
        job["progress_notes"] = notes
    audit(
        state,
        job_id,
        "record_progress",
        f"{actor} recorded quantities to date: {'; '.join(summary)}. Completed to date {money(job['completed_value_cents'])}."
        + (f" {notes}" if notes else ""),
        actor,
    )
    return draft_invoice(state, job_id, actor="billdable")


def pay_app_text(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> str:
    """The pay app as plain text, for the email body and the pencil copy."""
    out = [f"{invoice['number']} — {job['name']}", job.get("address", ""), ""]
    for line in invoice.get("lines", []):
        out.append(f"  {line['label']}: {money(line['amount_cents'])}")
    out.append(f"  Amount due this application: {money(invoice['amount_cents'])}")
    sheet = sov.continuation_sheet(job, invoice)
    if sheet:
        out += ["", "Schedule of values (continuation sheet, dollars):", sheet]
    packet = [p for p in invoice.get("packet") or [] if p.get("required")]
    if packet:
        out += ["", "Packet:"]
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


def _sov_view(job: dict[str, Any]) -> dict[str, Any]:
    """What the job page shows for the schedule of values: per-line figures and whether it can be edited."""
    editable = not job.get("closed") and job.get("work_status") != "complete" and not sov.live_invoices(job)
    if not sov.has_sov(job):
        return {"present": False, "editable": editable}
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
