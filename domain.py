"""Duracash domain: job money file, packet gate, invoice draft, payment match.

Source accounting data is treated as external. This module owns the operating
record Duracash builds on top of it: what is billable, what is blocking send,
who approved, and whether cash matched.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
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
    """A business refusal (the server's 409). `blockers` lists what stands in the way, for the
    card under the button that was tapped; `fields` maps a request field (its JSON key, such as
    "ap_email") to what to fix there, so a form shows it under that input."""

    def __init__(self, message: str, blockers: list[str] | None = None, fields: dict[str, str] | None = None):
        super().__init__(message)
        self.fields = dict(fields or {})
        self.blockers = blockers or list(self.fields.values()) or [message]


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


# ---------- audit trail: a hash chain (P1-11) ----------
# Each row carries hash = sha256(previous row's hash + this row's fields). Changing, removing
# or reordering any row breaks every hash after it, which check_audit() reports. The chain
# shows the file was not edited after the fact; it is not a signature, so someone with the
# file and this code could rebuild it. The nightly check and off-site backups are what make
# a quiet rebuild visible.
AUDIT_FIELDS = ("id", "at", "job_id", "actor", "action", "detail")


def _audit_hash(prev: str, row: dict[str, Any]) -> str:
    body = json.dumps({k: row.get(k, "") for k in AUDIT_FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev + "\n" + body).encode("utf-8")).hexdigest()


def seal_audit(state: dict[str, Any]) -> None:
    """Chain rows that have no hash yet (rows written before the chain existed)."""
    prev = ""
    for row in state.get("audit", []):
        if not row.get("hash"):
            row["prev"] = prev
            row["hash"] = _audit_hash(prev, row)
        prev = row["hash"]


def check_audit(state: dict[str, Any]) -> dict[str, Any]:
    """{"ok", "rows", "broken_at"}: broken_at is the id of the first row that doesn't match."""
    prev = ""
    rows = state.get("audit", [])
    for row in rows:
        if row.get("prev") != prev or row.get("hash") != _audit_hash(prev, row):
            return {"ok": False, "rows": len(rows), "broken_at": row.get("id", "?")}
        prev = row["hash"]
    return {"ok": True, "rows": len(rows), "broken_at": None}


def audit(state: dict[str, Any], job_id: str, action: str, detail: str, actor: str = "duracash") -> None:
    rows = state.setdefault("audit", [])
    prev = rows[-1].get("hash", "") if rows else ""
    row = {
        "id": f"aud-{len(rows) + 1}",
        "at": utcnow(),
        "job_id": job_id,
        "actor": actor,
        "action": action,
        "detail": detail,
        "prev": prev,
    }
    row["hash"] = _audit_hash(prev, row)
    rows.append(row)


def record_progress(
    state: dict[str, Any],
    job_id: str,
    completed_value_cents: int,
    notes: str = "",
    actor: str = "owner",
    period_cents: int | None = None,
) -> dict[str, Any]:
    """Set completed-to-date and draft a progress pay app. Does not mark the job done.
    A correction while the draft is still only here replaces it at the new amount, keeping its
    number and its paperwork; a draft the customer is reviewing has to be answered or voided first.
    period_cents is "this draw" instead (R3-01): the work on this pay app, before retainage, on top
    of what is already billed; completed_value_cents is then ignored and refusals name period_cents."""
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
    if notes:
        job["progress_notes"] = notes
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
        return draft_invoice(state, job_id, actor="duracash", replaces=open_draft)
    return draft_invoice(state, job_id, actor="duracash")


def _billed_gross(job: dict[str, Any], skip: dict[str, Any] | None = None) -> int:
    """Work on pay apps that still count (before retainage), leaving out `skip`."""
    return sum(
        int(inv.get("gross_cents", inv["amount_cents"] + inv.get("retainage_cents", 0)))
        for inv in job["invoices"] if inv["status"] != "void" and inv is not skip
    )


def _next_number(job: dict[str, Any]) -> str:
    """PA-<code>-NN, one past the pay apps that count. A voided draft's number is used again,
    unless the customer saw it as a draft for review."""
    taken = {inv["number"] for inv in job["invoices"] if inv["status"] != "void" or inv.get("pencil_sent_at")}
    n = len([inv for inv in job["invoices"] if inv["status"] != "void"]) + 1
    while f"PA-{job['code']}-{n:02d}" in taken:
        n += 1
    return f"PA-{job['code']}-{n:02d}"


def mark_done(state: dict[str, Any], job_id: str, actor: str = "owner", notes: str = "", evidence: list[str] | None = None,
              note_record: bool = True) -> dict[str, Any]:
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
    return draft_invoice(state, job_id, actor="duracash")


def draft_invoice(
    state: dict[str, Any], job_id: str, actor: str = "duracash", strict: bool = False, replaces: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Draft a pay app for unbilled completed work, withholding retainage.

    strict=True is for the explicit "Draft pay app" button: a no-op raises
    instead of being logged silently, so the user is told why nothing happened.
    replaces is a draft just voided by a progress correction: the new one keeps its number
    (unless the customer saw it) and its paperwork.
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
    }
    if replaces:
        invoice["replaces"] = replaces["id"]
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
    to = _send_to(payer)
    subject = f"Pencil copy for review: {draft['number']} — {job['name']}"
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Here is the pencil copy of {draft['number']} for {job['name']}, attached as a PDF marked PENCIL COPY. "
        "Please confirm the quantities and change orders, or send corrections, before we submit the formal pay app.\n\n"
        f"{pay_app_text(state, job, draft)}\n\nApproved by {actor}."
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
        raise GateError("No pencil copy is waiting for a response.")
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
        raise GateError("Packet item not found.")
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
    invoice["approved_by"] = actor
    job["external_status"] = f"Submitted to {payer['name']}, awaiting {payer['approver']}"
    subject = f"Pay application {invoice['number']} — {job['name']}"
    files = _packet_file_ids(state, job, invoice)
    body = (
        f"{payer.get('approver') or payer['name']},\n\n"
        f"Please find pay application {invoice['number']} for {job['name']} attached as a PDF. Every packet item is accounted for"
        + (f"; {len(files)} packet document{'s are' if len(files) != 1 else ' is'} attached too." if files else ".")
        + f"\n\n{pay_app_text(state, job, invoice)}"
    )
    link = payer_link(invoice, link_base, company_id)
    if link:
        body += f"\n\nSee this pay app, its packet and documents, and tell us it reached you:\n{link}"
    body += f"\n\nApproved by {actor}."
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


def record_follow_up(
    state: dict[str, Any], job_id: str, actor: str = "duracash", link_base: str | None = None, company_id: str = "",
    early: bool = False,
) -> dict[str, Any]:
    """Draft a follow-up on the oldest unpaid pay app once the payer's window has passed.
    `early`: the person chose "Follow up now" on the job page before then (R3-04)."""
    job = _open_job(state, job_id)
    invoice = next((inv for inv in job["invoices"] if inv["status"] in {"sent", "partial"}), None)
    if not invoice:
        raise GateError("No sent pay app to follow up.")
    payer = _payer(state, job["payer_id"])
    to = _send_to(payer)
    days = _int_or(payer.get("follow_up_days"), 7)
    last = invoice.get("last_follow_up_at") or invoice.get("sent_at")
    if last and not early:
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
        f"Is anything missing on your side?"
    )
    link = payer_link(invoice, link_base, company_id)
    if link:
        body += f"\n\nThe pay app, its packet and documents: {link}"
    body += f"\n\nApproved by {actor}."
    _record_email(state, job_id, "follow_up", to, subject, body, actor)
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
    actor: str = "duracash",
    invoice_id: str | None = None,
    received_on: str | None = None,
    qbo_payment_id: str | None = None,
) -> dict[str, Any]:
    """Record money received against a pay app. `received_on` (YYYY-MM-DD) is the date the books
    show, for imports; without it the payment is dated now. `qbo_payment_id` is QuickBooks' id
    for the payment (qbo.py): one QuickBooks payment is recorded once in the whole company."""
    job = _open_job(state, job_id)
    qbo_payment_id = str(qbo_payment_id or "").strip()[:40] or None
    if qbo_payment_id and any(p.get("qbo_payment_id") == qbo_payment_id for j in state["jobs"] for p in j.get("payments", [])):
        raise GateError("This QuickBooks payment is already recorded.")
    open_invoices = [inv for inv in job["invoices"] if inv["status"] in OPEN_FOR_PAYMENT]
    if not open_invoices:
        raise GateError("No open invoice to match.")
    if invoice_id:
        invoice = next((inv for inv in open_invoices if inv["id"] == invoice_id), None)
        if not invoice:
            _refuse("", {"invoice_id": "Pick a pay app that is waiting for payment."})
    elif len(open_invoices) == 1:
        invoice = open_invoices[0]
    else:
        raise GateError(
            "More than one pay app is open. Choose which pay app this payment is for.",
            [f"{inv['number']} ({money(inv['amount_cents'])})" for inv in open_invoices],
            {"invoice_id": "Pick the pay app this payment is for."},
        )
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        _refuse("", {"amount_cents": "Enter the payment amount, more than $0."})
    already = sum(p["amount_cents"] for p in job["payments"] if p["invoice_id"] == invoice["id"])
    remaining = invoice["amount_cents"] - already
    if amount_cents > remaining:
        _refuse("", {"amount_cents": f"Enter no more than the {money(remaining)} still owed on {invoice['number']}. You entered {money(amount_cents)}."})
    reference = reference.strip() if isinstance(reference, str) else ""
    if reference and any(p.get("reference") == reference for p in job["payments"]):
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


# The four lien waiver types. Duracash records which one went out; it never writes the
# waiver itself, because state law sets the forms (the contractor drops their signed form
# on the job record and sending attaches it).
WAIVER_KINDS = {
    "conditional_progress": "Conditional waiver on progress payment",
    "unconditional_progress": "Unconditional waiver on progress payment",
    "conditional_final": "Conditional waiver on final payment",
    "unconditional_final": "Unconditional waiver on final payment",
}


def suggested_waiver_kind(job: dict[str, Any]) -> str:
    """Payment has been matched, so unconditional; final once the job is done and fully paid."""
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
    kind = kind or suggested_waiver_kind(job)
    pending = [inv for inv in job["invoices"] if inv.get("waiver_due") and not inv.get("waiver_sent")]
    if invoice_id:
        pending = [inv for inv in pending if inv["id"] == invoice_id]
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
        detail = f"payment of {money(invoice['amount_cents'])} on {invoice['number']}"
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
        + f"\n\nApproved by {actor}."
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


def chase_retainage(state: dict[str, Any], job_id: str, actor: str = "duracash") -> dict[str, Any]:
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
    to = _send_to(payer)
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
    _record_email(state, job_id, "retainage", to, subject, body, actor, attachments=files)
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


def job_view(state: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """One job as the app shows it: the stored job plus money, blockers and labels."""
    out = deepcopy(job)
    out["money"] = job_money(job)
    out["blocker_items"] = blocker_items(job)
    out["blockers"] = [b["text"] for b in out["blocker_items"]]
    # The blockers that are paperwork to attach (the rest are unsigned change orders).
    out["missing_paperwork"] = [item["label"] for item in missing_packet(job)]
    out["status_label"] = status_label(job)
    out["payer"] = deepcopy(_payer(state, job["payer_id"]))
    out["can_delete"] = can_delete_job(job)
    out["close_blocker_items"] = close_blocker_items(job)
    out["close_blockers"] = [b["text"] for b in out["close_blocker_items"]]
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
        "blocked_jobs": sum(
            1 for j in views
            if j["blockers"] and not j.get("closed")
            and (j.get("work_status") == "complete" or any(i.get("status") in {"draft", "awaiting_approval"} for i in j["invoices"]))
        ),
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
    out = {k: deepcopy(v) for k, v in job.items() if k not in SUMMARY_DROPS}
    out["money"] = job_money(job)
    out["status_label"] = status_label(job)
    out["blockers"], out["close_blockers"], out["close_blocker_items"] = [], [], []
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
        inv = next((i for i in job.get("invoices", []) if i["id"] == e.get("payapp_invoice_id")), None)
        if inv and (inv.get("status") == "void" or inv.get("replaced")):
            continue
        # Once the money is in, a pay app or follow-up that didn't go out no longer matters.
        if e.get("kind") == "pay_app" and inv and inv.get("status") == "paid":
            continue
        if e.get("kind") == "follow_up" and not any(i.get("status") in {"sent", "partial"} for i in job.get("invoices", [])):
            continue
        # A draft for review is moot once the pay app itself went out; a signature request once
        # the change order is signed or withdrawn.
        if e.get("kind") == "pencil" and inv and inv.get("status") not in DRAFT_STATUSES:
            continue
        if e.get("kind") == "co_signature" and not any(
            co.get("status") == "pending" and f"change order {co['number']} " in (e.get("subject") or "")
            for co in job.get("change_orders", [])
        ):
            continue
        out.append(e)
    return sorted(out, key=lambda e: e.get("at", ""), reverse=True)


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
    for payer in out.get("payers", []):
        payer["sent_before"] = payer["id"] in sent
        timing = timings.get(payer["id"])
        if timing:
            payer["paid_days_avg"], payer["paid_count"] = timing
        due = payapp_due(payer, (now or datetime.now(timezone.utc)).date())
        if due:
            payer["payapp_due_next"], payer["payapp_due_prev"] = due[0].isoformat(), due[1].isoformat()
    emails = state.get("email_log", [])
    out["email_log"] = deepcopy(emails[-RECENT_EMAILS:])
    out["email_log_total"] = len(emails)
    # Today lists these with Send again, however old they are; the count is for the Today badge.
    failures = email_failures(state)
    out["email_failures"] = [{k: v for k, v in e.items() if k != "body"} for e in failures[:RECENT_EMAILS]]
    out["pending_email_failures"] = len(failures)
    out["audit_total"] = len(state.get("audit", []))
    out["closed_jobs_total"] = closed_count
    out["audit_chain"] = check_audit(state)
    out["trial"] = trial.status(state)
    out["totals"] = {**_totals(full), "closed_collected_cents": closed_collected}
    return out


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
    return {"entries": deepcopy(rows[offset:offset + limit]), "total": len(rows), "offset": offset, "limit": limit}


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
                   "name": "the customer's name", "phone": "the phone number"}
    day_fields = {"follow_up_days": "the days between follow-ups", "usual_approval_days": "the days they usually take to pay"}
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
        "rejects_when": "Missing required packet items.",
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
    every pay app. Blank clears a field; the name can't be blank."""
    company = state.setdefault("company", {})
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    if "billing_style" in updates:
        # How the company bills: pay apps to GCs or owners, or draws from homeowners. It sets the
        # free trial's length (trial.py: 90 days, or 45 for draws).
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
    if errors:
        raise GateError("Company details not saved.", fields=errors)
    changed = [k for k, v in clean.items() if (company.get(k) or ("pay_apps" if k == "billing_style" else "")) != v]
    for k in changed:
        if clean[k]:
            company[k] = clean[k]
        else:
            company.pop(k, None)
    if changed:
        names = {"billing_style": f"billing style ({'draws' if clean.get('billing_style') == 'draws' else 'pay apps'})"}
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
        audit(state, "", "playbook_learned", f"{actor} added \"{sug['text']}\" to {payer['name']}'s packet ({sug['source']}).", actor)
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


def request_co_signature(
    state: dict[str, Any], job_id: str, co_id: str, actor: str = "owner", link_base: str | None = None, company_id: str = ""
) -> str:
    """Make the change order's signing link and, with a site address, email it to the payer. Returns the link."""
    job, co = _pending_co(state, job_id, co_id)
    if not link_base or not company_id:
        raise GateError("Signing links need the site's address (DURACASH_URL) set on the server.")
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
        raise GateError(f"CO {co['number']} is on {open_draft['number']}. Void that draft first, then withdraw the CO.")
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
    audit(state, job_id, "learn_rejection", f"{actor} taught playbook: {payer['name']} rejects when {reason}.", actor)
    return job


def close_job(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
    blockers = close_blockers(job)
    if blockers:
        raise GateError("This job can't be finished yet.", blockers)
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


def _packet_for_pay_app(state: dict[str, Any], job: dict[str, Any], invoice_id: str, number: str = "") -> list[dict[str, Any]]:
    """Reuse an existing job packet (older records), else build one from the payer playbook.
    Paperwork Duracash makes is in from the start, and missing items are filled from the job record."""
    if job.get("packet"):
        _attach_from_record(state, job, job["packet"], number or "the job packet")
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
    ("ticket", ("ticket",), re.compile(r"ticket", re.I)),
    ("contract", ("contract",), re.compile(r"contract", re.I)),
)
# Records that belong to one pay app (this period's waiver, photos, backup) are not reused on the
# next one; a signed contract or an insurance certificate is.
PER_PAY_APP = {"backup", "co", "waiver", "photo", "ticket"}
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
        raise GateError("Packet item not found.")
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
        raise GateError("Packet item not found.")
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
        raise GateError("Packet item not found.")
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
    which = invoice["number"] if invoice else "the job packet"
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
) -> dict[str, Any]:
    """Add a job and its record. The packet is created later, with a pay app, from the payer's
    paperwork list (Customer settings); `note` is kept on the job record and never becomes it.
    Who signs off and the email are optional: the email is asked for before the first send."""
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
        f"{actor} created {job['name']} for {payer['name']} ({money(job['contract_cents'])}). Packet is created with the first pay app.",
        actor,
    )
    return job



def edit_job(state: dict[str, Any], job_id: str, updates: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    """Fix a job's name, address or contract number at any time. The contract amount and
    retainage can change only before the first pay app, and the contract only without a
    schedule of values (change that schedule instead). The job code never changes: emails
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

    if "retainage_pct" in updates and updates["retainage_pct"] != job.get("retainage_pct", 0):
        pct = updates["retainage_pct"]
        if has_pay_apps:
            problems["retainage_pct"] = "Keep the retainage as it is: it is fixed once a pay app exists."
        elif not isinstance(pct, int) or isinstance(pct, bool) or not 0 <= pct <= 20:
            problems["retainage_pct"] = "Enter a retainage from 0 to 20 percent."
        else:
            changes.append(f"retainage from {job.get('retainage_pct', 0)}% to {pct}%")
            job["retainage_pct"] = pct

    _refuse("The job was not changed.", problems)
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
    stored: Any = None,
) -> dict[str, Any]:
    """Record quantities completed to date, and materials stored, on schedule lines; draft the
    progress pay app. `done` maps line id -> quantity to date; `stored` maps line id -> dollars of
    materials on site and not yet installed (G703 column F)."""
    job = _open_job(state, job_id)
    if not sov.has_sov(job):
        raise GateError("This job has no schedule of values. Enter completed-to-date in dollars, or add a schedule first.")
    if job.get("work_status") == "complete":
        raise GateError("Job is already marked done. Draft the remaining pay app instead.")
    open_draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if open_draft:
        raise GateError(f"{open_draft['number']} is still a draft. Send or void it, then record more progress.")
    done = done if isinstance(done, dict) else {}
    stored = stored if isinstance(stored, dict) else {}
    if not done and not stored:
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
    if not changes:
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
    if notes:
        job["progress_notes"] = notes
    audit(
        state,
        job_id,
        "record_progress",
        f"{actor} recorded progress to date: {'; '.join(summary)}. Completed and stored to date {money(job['completed_value_cents'])}."
        + (f" {notes}" if notes else ""),
        actor,
    )
    return draft_invoice(state, job_id, actor="duracash")


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
