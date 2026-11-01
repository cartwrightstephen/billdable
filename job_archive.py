"""The job file (Stephen's Oct 29 review, J9): one zip of everything kept on a job, for a finished
job's archive (Download job file on its page, GET /api/jobs/<id>/archive/job-file.zip).

In it: summary.txt (the contract, change orders, billed, received, retainage and dates; each pay
app, payment, change order and waiver sent; what is on the job record), activity.txt (the job's
activity log), each sent pay app's PDF under pay-apps/, and the job record's files under
record/, by kind. Built with the standard library's zipfile, in memory.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any, Callable

import domain
import payapp_pdf

RECORD_WORDS = {
    "note": "Notes", "daily": "Daily records", "photo": "Photos", "contract": "Contract", "change_order": "Change orders",
    "waiver": "Lien waivers", "pay_app": "Pay app backup", "ticket": "Tickets and time sheets",
    "supplier_invoice": "Supplier invoices", "insurance": "Insurance", "inspection": "Inspections", "stored": "Stored materials",
    "punch": "Punch list", "completion": "Completion certificate", "asbuilt": "As-builts", "warranty": "Warranties", "other": "Other",
}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "")).strip("-.")[:80] or "file"


def _day(iso: Any) -> str:
    return str(iso or "")[:10] or "-"


def summary_text(state: dict[str, Any], job: dict[str, Any]) -> str:
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    m = domain.job_money(job)
    money = domain.money
    lines = [
        f"{job.get('name', '')} ({job.get('code', '')})",
        f"Address: {job.get('address', '')}",
        f"Client: {payer.get('name', '')}",
        f"Contract number: {job.get('contract_number') or '-'}",
        f"Started: {_day(domain.job_started_on(state, job))}",
        f"Marked done: {_day(job.get('completed_at'))}",
        f"Finished: {_day(job.get('closed_at'))}",
        "",
        f"Contract: {money(m['contract_cents'])}",
        f"Signed change orders: {money(m['approved_co_cents'])}",
        f"Contract total: {money(m['contract_total_cents'])}",
        f"Billed: {money(m['billed_cents'])}",
        f"Received: {money(m['collected_cents'])}",
        f"Retainage held: {money(m['retainage_held_cents'])}",
        f"Retainage released: {money(m['retainage_released_cents'])}",
        "",
        "Pay apps:",
    ]
    for inv in job.get("invoices", []):
        if inv.get("status") == "void" and not inv.get("sent_at"):
            continue
        lines.append(f"  {inv['number']}  {money(inv['amount_cents'])}  {inv.get('status', '')}  sent {_day(inv.get('sent_at'))}")
    lines += ["", "Payments:"]
    lines += [f"  {_day(p.get('matched_at'))}  {money(p['amount_cents'])}  {p.get('reference') or ''}".rstrip()
              for p in job.get("payments", [])] or ["  None"]
    lines += ["", "Change orders:"]
    lines += [f"  CO {co.get('number')}  {co.get('title', '')}  {money(co['amount_cents'])}  {co.get('status', '')}"
              for co in job.get("change_orders", [])] or ["  None"]
    waivers = [e for e in state.get("email_log", []) if e.get("job_id") == job["id"] and e.get("kind") == "waiver"]
    lines += ["", "Waivers sent:"]
    lines += [f"  {_day(e.get('at'))}  {e.get('subject', '')}" for e in waivers] or ["  None"]
    lines += ["", "Job record:"]
    lines += [f"  {_day(r.get('added_at'))}  {RECORD_WORDS.get(r.get('kind'), r.get('kind', ''))}: {r.get('label', '')}"
              for r in job.get("records", [])] or ["  None"]
    return "\n".join(lines) + "\n"


def activity_text(state: dict[str, Any], job: dict[str, Any]) -> str:
    rows = [a for a in state.get("audit", []) if a.get("job_id") == job["id"]]
    return "\n".join(f"{a.get('at', '')}  {a.get('actor', '')}  {a.get('detail', '')}" for a in rows) + "\n"


def build(state: dict[str, Any], job: dict[str, Any], path_for: Callable[[dict[str, Any]], Path]) -> tuple[bytes, str]:
    """(zip bytes, file name) of the job file. path_for(file meta) is where an upload is on disk;
    a file no longer there is listed in summary.txt as missing instead."""
    buf = io.BytesIO()
    missing: list[str] = []
    used: set[str] = set()

    def unique(path: str) -> str:
        stem, dot, ext = path.rpartition(".")
        out, n = path, 2
        while out in used:
            out = f"{stem}-{n}.{ext}" if dot else f"{path}-{n}"
            n += 1
        used.add(out)
        return out

    files = {f["id"]: f for f in state.get("files", [])}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for inv in job.get("invoices", []):
            if not inv.get("sent_at") or inv.get("status") == "void":
                continue
            try:
                pdf, name = payapp_pdf.for_invoice(state, job["id"], inv["id"])
            except Exception:  # one pay app that can't be drawn doesn't stop the rest
                pdf, name = None, None
            if pdf:
                z.writestr(unique(f"pay-apps/{name}"), pdf)
            else:
                missing.append(f"{inv['number']} PDF")
        for rec in job.get("records", []):
            folder = _slug(RECORD_WORDS.get(rec.get("kind"), rec.get("kind") or "Other"))
            for fid in rec.get("file_ids", []):
                meta = files.get(fid)
                try:
                    data = path_for(meta).read_bytes() if meta else None
                except OSError:
                    data = None
                if data is None:
                    missing.append(meta["name"] if meta else rec.get("label", fid))
                    continue
                z.writestr(unique(f"record/{folder}/{_slug(meta['name'])}"), data)
        summary = summary_text(state, job)
        if missing:
            summary += "\nNot in this file (no longer on the server):\n" + "".join(f"  {x}\n" for x in missing)
        z.writestr("summary.txt", summary)
        z.writestr("activity.txt", activity_text(state, job))
    return buf.getvalue(), f"{_slug(job.get('code') or job.get('name') or 'job')}-job-file.zip"
