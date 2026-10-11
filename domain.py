"""Billdable domain: job money file, packet gate, invoice draft, payment match.

Source accounting data is treated as external. This module owns the operating
record Billdable builds on top of it: what is billable, what is blocking send,
who approved, and whether cash matched.
"""

from __future__ import annotations

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


def missing_packet(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in job["packet"] if item["required"] and not item["present"]]


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


def mark_done(state: dict[str, Any], job_id: str, actor: str = "owner", notes: str = "", evidence: list[str] | None = None) -> dict[str, Any]:
    job = _job(state, job_id)
    job["work_status"] = "complete"
    job["completed_at"] = job.get("completed_at") or utcnow()
    job["done_notes"] = notes or job.get("done_notes", "")
    if evidence:
        job.setdefault("evidence", []).extend(evidence)
    if job.get("completed_value_cents") is None:
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
    blockers = blocker_lines(job)
    invoice = {
        "id": f"inv-{job['id']}-{len(job['invoices']) + 1}",
        "number": number,
        "amount_cents": amount,
        "status": "draft" if blockers else "awaiting_approval",
        "lines": lines,
        "created_at": utcnow(),
        "sent_at": None,
        "approved_by": None,
    }
    job["invoices"].append(invoice)
    audit(
        state,
        job_id,
        "draft_invoice",
        f"Drafted {number} for {money(amount)}. Blockers: {len(blockers)}.",
        actor,
    )
    return job


def resolve_packet_item(state: dict[str, Any], job_id: str, item_id: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
    item = next((i for i in job["packet"] if i["id"] == item_id), None)
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
    # also attach as packet evidence
    job.setdefault("packet", []).append({
        "id": f"pkt-dr-{rec['id']}",
        "kind": "daily",
        "label": f"Daily record {rec['at'][:10]}",
        "required": False,
        "present": True,
        "blocker": "",
        "attachments": [{"name": "daily-note.txt", "note": notes}],
    })
    audit(state, job_id, "daily_record", f"{actor} added a daily record.", actor)
    return job


def add_packet_attachment(state: dict[str, Any], job_id: str, item_id: str, name: str, actor: str = "owner") -> dict[str, Any]:
    job = _job(state, job_id)
    item = next((i for i in job["packet"] if i["id"] == item_id), None)
    if not item:
        raise GateError("Packet item not found.")
    item.setdefault("attachments", []).append({"name": name, "added_at": utcnow(), "added_by": actor})
    item["present"] = True
    audit(state, job_id, "packet_attachment", f"{actor} attached {name} to {item['label']}.", actor)
    _refresh_draft_gate(job)
    return job
    job = _job(state, job_id)
    money = job_money(job)
    if money["outstanding_cents"] > 0 or money["retainage_open_cents"] > 0 or (job.get("waiver_due") and not job.get("waiver_sent")):
        raise GateError("Cannot close: outstanding balance, open retainage, or waiver still due.")
    job["closed"] = True
    job["closed_at"] = utcnow()
    audit(state, job_id, "close_job", f"{actor} closed {job['name']} for the record.", actor)
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
