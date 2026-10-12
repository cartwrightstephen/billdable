"""Billdable domain: job money file, packet gate, invoice draft, payment match.

Source accounting data is treated as external. This module owns the operating
record Billdable builds on top of it: what is billable, what is blocking send,
who approved, and whether cash matched.
"""

from __future__ import annotations

import secrets
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


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
    billed = sum(inv["amount_cents"] for inv in job["invoices"] if inv["status"] in {"sent", "approved", "partial", "paid"})
    collected = sum(p["amount_cents"] for p in job["payments"])
    draft = sum(inv["amount_cents"] for inv in job["invoices"] if inv["status"] in {"draft", "awaiting_approval"})
    retainage_held = job.get("retainage_held_cents", 0)
    retainage_released = job.get("retainage_released_cents", 0)
    unbilled_completed = max(0, job.get("completed_value_cents", contract_total) - billed - draft)
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
        "retainage_open_cents": max(0, retainage_held - retainage_released),
    }


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
    job = _job(state, job_id)
    if job.get("closed"):
        raise GateError("Job is closed.")
    if job.get("work_status") == "complete":
        raise GateError("Job is already marked done. Draft the remaining pay app instead.")
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
    job = _job(state, job_id)
    job["work_status"] = "complete"
    job["completed_at"] = job.get("completed_at") or utcnow()
    job["done_notes"] = notes or job.get("done_notes", "")
    if evidence:
        job.setdefault("evidence", []).extend(evidence)
    job["completed_value_cents"] = job["contract_cents"] + sum(
        co["amount_cents"] for co in job["change_orders"] if co["status"] == "approved"
    )
    audit(state, job_id, "job_done", f"{actor} marked {job['name']} done. Notes: {notes or 'none'}.", actor)
    return draft_invoice(state, job_id, actor="billdable")


def draft_invoice(state: dict[str, Any], job_id: str, actor: str = "billdable") -> dict[str, Any]:
    job = _job(state, job_id)
    if any(inv["status"] in {"draft", "awaiting_approval"} for inv in job["invoices"]):
        audit(state, job_id, "draft_skipped", "Open draft already exists.", actor)
        return job
    money_file = job_money(job)
    amount = money_file["unbilled_completed_cents"]
    pending_included = unsigned_cos_in_billing(job)
    amount += sum(co["amount_cents"] for co in pending_included)
    if amount <= 0:
        audit(state, job_id, "draft_skipped", "Nothing unbilled to draft.", actor)
        return job
    number = f"PA-{job['code']}-{len(job['invoices']) + 1:02d}"
    lines = [
        {
            "label": "Completed contract work not yet billed",
            "amount_cents": money_file["unbilled_completed_cents"],
        }
    ]
    for co in pending_included:
        lines.append({"label": f"CO {co['number']} — {co['title']} (unsigned)", "amount_cents": co["amount_cents"]})
    invoice_id = f"inv-{job['id']}-{len(job['invoices']) + 1}"
    packet = _packet_for_pay_app(job, invoice_id)
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
        "status": "draft" if blockers else "awaiting_approval",
        "lines": lines,
        "created_at": utcnow(),
        "sent_at": None,
        "approved_by": None,
        "packet": packet,
        "packet_id": invoice_id if packet is not job.get("packet") else "job-packet",
    }
    job["invoices"].append(invoice)
    audit(
        state,
        job_id,
        "draft_invoice",
        f"Drafted {number} for {money(amount)} with a {len(packet)}-item packet. Blockers: {len(blockers)}.",
        actor,
    )
    return job


def resolve_packet_item(state: dict[str, Any], job_id: str, item_id: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
    item, _invoice = _find_packet_item(job, item_id)
    if not item:
        raise GateError("Packet item not found.")
    item["present"] = True
    item["resolved_at"] = utcnow()
    audit(state, job_id, "packet_resolved", f"{item['label']} marked present.", actor)
    _refresh_draft_gate(job)
    return job


def approve_and_send(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
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
    body = f"Approved by {actor}. Amount {money(invoice['amount_cents'])}. Packet complete. Please process."
    _record_email(state, job_id, "pay_app", payer["ap_email"], subject, body, actor)
    audit(
        state,
        job_id,
        "approve_send",
        f"{actor} approved and sent {invoice['number']} for {money(invoice['amount_cents'])} to {payer['ap_email']}.",
        actor,
    )
    return job


def record_follow_up(state: dict[str, Any], job_id: str, actor: str = "billdable") -> dict[str, Any]:
    job = _job(state, job_id)
    invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] == "sent"), None)
    if not invoice:
        raise GateError("No sent pay app to follow up.")
    payer = _payer(state, job["payer_id"])
    job["external_status"] = f"Follow-up sent to {payer['approver']}"
    job["follow_ups"] = job.get("follow_ups", 0) + 1
    audit(
        state,
        job_id,
        "follow_up",
        f"Follow-up on {invoice['number']} to {payer['approver']} at {payer['ap_email']}. Cadence: {payer['follow_up_days']} days.",
        actor,
    )
    return job


def match_payment(
    state: dict[str, Any],
    job_id: str,
    amount_cents: int,
    reference: str,
    actor: str = "billdable",
) -> dict[str, Any]:
    job = _job(state, job_id)
    invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] in {"sent", "approved", "partial"}), None)
    if not invoice:
        raise GateError("No open invoice to match.")
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
        invoice["status"] = "paid"
        job["waiver_due"] = True
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


def send_waiver(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
    if not job.get("waiver_due"):
        raise GateError("No waiver is due. Payment has not been matched.")
    job["waiver_sent"] = True
    job["waiver_sent_at"] = utcnow()
    job["external_status"] = "Conditional waiver sent"
    payer = _payer(state, job["payer_id"])
    subject = f"Conditional waiver — {job['name']}"
    body = f"Approved by {actor}. Payment received; conditional waiver attached."
    _record_email(state, job_id, "waiver", payer["ap_email"], subject, body, actor)
    audit(state, job_id, "send_waiver", f"{actor} approved sending the conditional waiver.", actor)
    return job


def chase_retainage(state: dict[str, Any], job_id: str, actor: str = "billdable") -> dict[str, Any]:
    job = _job(state, job_id)
    open_retainage = job_money(job)["retainage_open_cents"]
    if open_retainage <= 0:
        raise GateError("No open retainage.")
    missing = missing_packet(job)
    if missing:
        raise GateError("Closeout packet incomplete.", [item["blocker"] for item in missing])
    payer = _payer(state, job["payer_id"])
    job["retainage_chases"] = job.get("retainage_chases", 0) + 1
    job["external_status"] = f"Retainage follow-up sent to {payer['approver']}"
    subject = f"Retainage release request — {job['name']}"
    body = f"Please release {money(open_retainage)} retainage. Closeout packet is complete."
    _record_email(state, job_id, "retainage", payer["ap_email"], subject, body, actor)
    audit(
        state,
        job_id,
        "chase_retainage",
        f"Asked {payer['name']} to release {money(open_retainage)} retainage.",
        actor,
    )
    return job


def snapshot(state: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(state)
    for job in out["jobs"]:
        job["money"] = job_money(job)
        job["blockers"] = blocker_lines(job)
        job["status_label"] = status_label(job)
        job["payer"] = _payer(out, job["payer_id"])
        job.setdefault("evidence", [])
        job.setdefault("done_notes", "")
    out.setdefault("email_log", [])
    out["totals"] = {
        "unbilled_cents": sum(j["money"]["unbilled_completed_cents"] for j in out["jobs"]),
        "outstanding_cents": sum(j["money"]["outstanding_cents"] for j in out["jobs"]),
        "retainage_cents": sum(j["money"]["retainage_open_cents"] for j in out["jobs"]),
        "blocked_jobs": sum(1 for j in out["jobs"] if j["blockers"] and j.get("work_status") == "complete"),
    }
    return out


def _refresh_draft_gate(job: dict[str, Any]) -> None:
    invoice = next((inv for inv in reversed(job["invoices"]) if inv["status"] in {"draft", "awaiting_approval"}), None)
    if not invoice:
        return
    invoice["status"] = "draft" if blocker_lines(job) else "awaiting_approval"


def _record_email(state: dict[str, Any], job_id: str, kind: str, to: str, subject: str, body: str, actor: str) -> None:
    state.setdefault("email_log", []).append({
        "id": f"eml-{len(state.get('email_log', [])) + 1}",
        "at": utcnow(),
        "job_id": job_id,
        "kind": kind,
        "to": to,
        "subject": subject,
        "body": body,
        "approved_by": actor,
        "status": "logged (stub — not delivered)",
    })


def update_payer(state: dict[str, Any], payer_id: str, updates: dict[str, Any], actor: str = "owner") -> dict[str, Any]:
    payer = _payer(state, payer_id)
    allowed = {"approver", "ap_email", "packet", "follow_up_days", "usual_approval_days", "rejects_when", "copy"}
    changed = []
    for k, v in updates.items():
        if k in allowed and v is not None:
            payer[k] = v
            changed.append(k)
    audit(state, "", "playbook_update", f"{actor} updated {payer['name']}: {', '.join(changed) or 'no change'}.", actor)
    return payer


def add_change_order(state: dict[str, Any], job_id: str, title: str, amount_cents: int, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
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
    job = _job(state, job_id)
    co = next((c for c in job["change_orders"] if c["id"] == co_id), None)
    if not co:
        raise GateError("Change order not found.")
    co["status"] = "approved"
    co["included_in_billing"] = False  # no longer pending
    audit(state, job_id, "approve_co", f"{actor} approved CO {co['number']} — {co['title']}.", actor)
    _refresh_draft_gate(job)
    return job


def add_evidence(state: dict[str, Any], job_id: str, label: str, kind: str = "note", actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
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
    money = job_money(job)
    if money["outstanding_cents"] > 0 or money["retainage_open_cents"] > 0 or (job.get("waiver_due") and not job.get("waiver_sent")):
        raise GateError("Cannot close: outstanding balance, open retainage, or waiver still due.")
    job["closed"] = True
    job["closed_at"] = utcnow()
    audit(state, job_id, "close_job", f"{actor} closed {job['name']} for the record.", actor)
    return job


def add_daily_record(state: dict[str, Any], job_id: str, notes: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
    rec = {
        "id": f"dr-{len(state.get('daily_records', [])) + 1}",
        "job_id": job_id,
        "notes": notes,
        "at": utcnow(),
        "actor": actor,
        "for_history": bool(job.get("closed")),
    }
    state.setdefault("daily_records", []).append(rec)
    job.setdefault("daily_records", []).append(rec["id"])
    add_job_record(state, job_id, f"Daily record {rec['at'][:10]}", "daily", notes, actor)
    audit(state, job_id, "daily_record", f"{actor} added a daily record to the job record.", actor)
    return job


def add_packet_attachment(state: dict[str, Any], job_id: str, item_id: str, name: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
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
    amount = samples.get(job_id, invoice["amount_cents"])
    if amount <= 0:
        amount = invoice["amount_cents"]
    return match_payment(state, job_id, amount, "QBO-sample-import", actor)


def _job(state: dict[str, Any], job_id: str) -> dict[str, Any]:
    job = next((j for j in state["jobs"] if j["id"] == job_id), None)
    if not job:
        raise GateError("Job not found.")
    return job


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
    for item in job.get("packet") or []:
        if item["id"] == item_id:
            return item, None
    return None, None


def _packet_for_pay_app(job: dict[str, Any], invoice_id: str) -> list[dict[str, Any]]:
    """Reuse an existing job packet, or build one for this pay app."""
    if job.get("packet"):
        return job["packet"]
    items = [
        _packet_item(invoice_id, "contract", "Signed contract", "Signed contract is missing."),
        _packet_item(invoice_id, "waiver", "Conditional lien waiver", "Conditional lien waiver is missing."),
        _packet_item(invoice_id, "photo", "Completion photos", "Completion photos are missing."),
        _packet_item(invoice_id, "backup", "Pay application backup", "Pay application backup is missing."),
    ]
    if job.get("payer_role") == "GC":
        items.append(_packet_item(invoice_id, "sov", "Schedule of values", "Schedule of values is missing."))
        items.append(_packet_item(invoice_id, "insurance", "Insurance certificate", "Insurance certificate is missing."))
    if job.get("retainage_pct"):
        items.append(_packet_item(invoice_id, "closeout", "Closeout / punch-list signoff", "Closeout packet is incomplete for retainage release."))
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
    job = _job(state, job_id)
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
    if not ap_email or "@" not in ap_email:
        missing.append("A real AP email is required.")
    if contract_cents <= 0:
        missing.append("Contract amount must be greater than zero.")
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
        payer["approver"] = approver or payer.get("approver", "")
        payer["ap_email"] = ap_email or payer.get("ap_email", "")
        payer["phone"] = phone or payer.get("phone", "")
        if packet_notes:
            payer["packet"] = packet_notes

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
