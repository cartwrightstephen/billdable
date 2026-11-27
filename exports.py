"""Import/Export, Export: the company's jobs, pay apps, payments and change orders as CSV files
for a spreadsheet or the books (I1, Stephen's Oct 29 review). Finished jobs are in them too.

Every text cell goes through `text`, so a name or note typed as "=HYPERLINK(...)" is read by a
spreadsheet as text, never run as a formula; dollar amounts are written by `dollars` from whole
cents, so they stay numbers. csv.writer quotes commas, quotes and line breaks.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Callable

import domain
from domain.jobinfo import job_started_on

KINDS = ("jobs", "payapps", "payments", "change-orders")
FILE_NAMES = {"jobs": "jobs", "payapps": "pay-apps", "payments": "payments", "change-orders": "change-orders"}

PAYAPP_WORDS = {"draft": "Draft", "awaiting_approval": "Ready to send", "sent": "Sent", "approved": "Approved",
                "partial": "Partly paid", "paid": "Paid", "void": "Cancelled"}
CO_WORDS = {"pending": "Unsigned", "approved": "Signed", "withdrawn": "Withdrawn"}
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def text(value: Any) -> str:
    """A text cell a spreadsheet won't run as a formula: one line, and an apostrophe in front of
    = + - @ (and a tab or carriage return), as qbo._cell does for the QuickBooks file."""
    cell = " ".join(str(value if value is not None else "").split())
    return "'" + cell if cell.startswith(FORMULA_START) else cell


def dollars(cents: Any) -> str:
    """Whole cents as dollars with two decimals, from integers only: -50 -> "-0.50"."""
    c = int(cents or 0)
    return f"{'-' if c < 0 else ''}{abs(c) // 100}.{abs(c) % 100:02d}"


def _day(iso: Any) -> str:
    return str(iso or "")[:10]


def _client(state: dict[str, Any], job: dict[str, Any]) -> str:
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    return payer.get("name", "")


def _status(job: dict[str, Any]) -> str:
    if job.get("closed"):
        return "Finished"
    return "Done" if job.get("work_status") == "complete" else "In progress"


def jobs_rows(state: dict[str, Any]) -> list[list[str]]:
    style = (state.get("company") or {}).get("billing_style", "pay_apps")
    rows = [["Job", "Code", "Client", "Address", "Contract", "Signed change orders", "Contract total", "Billed", "Received",
             "Outstanding", "Retainage held", "Retainage still owed", "Status", "Where it stands", "Started", "Anticipated end", "Done", "Finished"]]
    for job in state.get("jobs", []):
        m = domain.job_money(job)
        rows.append([text(job.get("name")), text(job.get("code")), text(_client(state, job)), text(job.get("address")),
                     dollars(m["contract_cents"]), dollars(m["approved_co_cents"]), dollars(m["contract_total_cents"]),
                     dollars(m["billed_cents"]), dollars(m["collected_cents"]), dollars(m["outstanding_cents"]),
                     dollars(m["retainage_held_cents"]), dollars(m["retainage_open_cents"]), _status(job),
                     text(domain.status_label(job, style)), _day(job_started_on(state, job)), _day(job.get("anticipated_end_on")),
                     _day(job.get("completed_at")) if job.get("work_status") == "complete" else "", _day(job.get("closed_at"))])
    return rows


def _received_by_pay_app(job: dict[str, Any]) -> dict[str, int]:
    """What each pay app received, with money refunded taken off the newest first, so the column
    adds up to the job's Received."""
    net: dict[str, int] = {}
    for p in job.get("payments", []):
        net[p.get("invoice_id")] = net.get(p.get("invoice_id"), 0) + int(p.get("amount_cents") or 0)
    back = domain.job_money(job)["refunded_cents"]
    for inv in reversed(job.get("invoices", [])):
        take = min(back, net.get(inv["id"], 0))
        if take:
            net[inv["id"]] -= take
            back -= take
    return net


def payapps_rows(state: dict[str, Any]) -> list[list[str]]:
    """Every pay app but a deleted draft or one pulled back (Cancelled), drafts included."""
    rows = [["Number", "Job", "Client", "Period to", "Amount", "Retainage", "Status", "Final", "Sent", "Received", "Paid on"]]
    for job in state.get("jobs", []):
        net = _received_by_pay_app(job)
        for inv in job.get("invoices", []):
            if inv.get("status") == "void":
                continue
            pays = [p for p in job.get("payments", []) if p.get("invoice_id") == inv["id"]]
            paid_on = max((_day(p.get("matched_at")) for p in pays), default="") if inv.get("status") == "paid" else ""
            rows.append([text(inv.get("number")), text(job.get("name")), text(_client(state, job)), _day(inv.get("period_to")),
                         dollars(inv.get("amount_cents")), dollars(inv.get("retainage_cents")),
                         PAYAPP_WORDS.get(inv.get("status"), text(inv.get("status"))), "Yes" if inv.get("final") else "",
                         _day(inv.get("sent_at")), dollars(net.get(inv["id"], 0)), paid_on])
    return rows


def payments_rows(state: dict[str, Any]) -> list[list[str]]:
    """Payments on pay apps, retainage the client released, and money refunded (as a minus, so the
    Amount column adds up to what the job has really received), oldest first. The payment records
    themselves stay as they were."""
    rows = []
    for job in state.get("jobs", []):
        numbers = {inv["id"]: inv.get("number", "") for inv in job.get("invoices", [])}
        for p in job.get("payments", []):
            rows.append([_day(p.get("matched_at")), text(job.get("name")), text(_client(state, job)), text(numbers.get(p.get("invoice_id"), "")),
                         "Pay app", dollars(p.get("amount_cents")), text(p.get("reference"))])
        for p in job.get("retainage_payments", []):
            rows.append([_day(p.get("at")), text(job.get("name")), text(_client(state, job)), "", "Retainage",
                         dollars(p.get("amount_cents")), text(p.get("reference"))])
        for s in job.get("overbilling_settled") or []:
            back = int(s.get("returned_cents", s.get("cents") or 0))
            if back:
                rows.append([_day(s.get("at")), text(job.get("name")), text(_client(state, job)), "", "Refund or credit", dollars(-back), text(s.get("reason"))])
    rows.sort(key=lambda r: r[0])
    return [["Date", "Job", "Client", "Pay app", "For", "Amount", "Reference"]] + rows


def change_orders_rows(state: dict[str, Any]) -> list[list[str]]:
    rows = [["Number", "Job", "Title", "Reason", "Amount", "Status", "Signed on", "On a pay app"]]
    for job in state.get("jobs", []):
        for co in job.get("change_orders", []):
            reason = co.get("reason") or ""
            rows.append([f"CO {co.get('number', '')}", text(job.get("name")), text(co.get("title")),
                         text(domain.CO_REASONS.get(reason, reason)), ("No price yet" if co.get("unpriced") else dollars(co.get("amount_cents"))),   # Round 2 decision 2: not $0
                         CO_WORDS.get(co.get("status"), text(co.get("status"))), domain.co_signed_on(co),
                         "Yes" if co.get("included_in_billing") else "No"])
    return rows


BUILDERS = {"jobs": jobs_rows, "payapps": payapps_rows, "payments": payments_rows, "change-orders": change_orders_rows}


def csv_text(state: dict[str, Any], kind: str) -> str:
    """The file for one kind (KINDS), with a byte-order mark so Excel reads names in any alphabet."""
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(BUILDERS[kind](state))
    return "﻿" + out.getvalue()


# ---------- everything, with the uploaded files (security review L4) ----------

def _file_name(meta: dict[str, Any]) -> str:
    name = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", str(meta.get("name") or "file")).strip(" .")[:100] or "file"
    return f"files/{meta.get('id', 'file')}-{name}"


def write_company_archive(state: dict[str, Any], path_of: Callable[[dict[str, Any]], Path], out: BinaryIO,
                          leave_out: set[str] | frozenset[str] = frozenset()) -> dict[str, int]:
    """A zip of the company's export (company.json, as GET /api/export gives it) and every file
    it holds, under files/<file id>-<name>; company.json's "files" list names each one. Files
    are stored as they are (photos and PDFs are compressed already), read one at a time, so a
    large company never sits in memory. A file missing on disk is listed in missing-files.txt.
    `leave_out`: file ids whose contents stay out (payroll and other sensitive documents for someone
    without "See payroll and sensitive documents", H2); they are listed in left-out-files.txt.
    Returns {"files", "missing", "left_out"}."""
    missing: list[str] = []
    skipped: list[str] = []
    count = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        zf.writestr(zipfile.ZipInfo("company.json"), json.dumps(domain.without_link_secrets(state), indent=2),
                    compress_type=zipfile.ZIP_DEFLATED)
        for meta in state.get("files", []):
            if str(meta.get("id")) in leave_out:
                skipped.append(f"{meta.get('id')}  {meta.get('name')}")
                continue
            source = path_of(meta)
            if not source.is_file():
                missing.append(f"{meta.get('id')}  {meta.get('name')}")
                continue
            zf.write(source, _file_name(meta))
            count += 1
        if missing:
            zf.writestr("missing-files.txt", "These files are listed in company.json but were not found on the server:\n" + "\n".join(missing) + "\n")
        if skipped:
            zf.writestr("left-out-files.txt", "These files are payroll, time sheets, lien waivers or contracts. Opening them needs the "
                        "permission \"See payroll and sensitive documents\", so they were left out of this export:\n" + "\n".join(skipped) + "\n")
    return {"files": count, "missing": len(missing), "left_out": len(skipped)}

