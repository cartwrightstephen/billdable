"""Import/Export, Export: the company's jobs, pay apps, payments and change orders as CSV files
for a spreadsheet or the books (I1, Stephen's Oct 29 review). Finished jobs are in them too.

Every text cell goes through `text`, so a name or note typed as "=HYPERLINK(...)" is read by a
spreadsheet as text, never run as a formula; dollar amounts are written by `dollars` from whole
cents, so they stay numbers. csv.writer quotes commas, quotes and line breaks.
"""

from __future__ import annotations

import csv
import io
from typing import Any

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
             "Outstanding", "Retainage held", "Retainage still owed", "Status", "Where it stands", "Started", "Done", "Finished"]]
    for job in state.get("jobs", []):
        m = domain.job_money(job)
        rows.append([text(job.get("name")), text(job.get("code")), text(_client(state, job)), text(job.get("address")),
                     dollars(m["contract_cents"]), dollars(m["approved_co_cents"]), dollars(m["contract_total_cents"]),
                     dollars(m["billed_cents"]), dollars(m["collected_cents"]), dollars(m["outstanding_cents"]),
                     dollars(m["retainage_held_cents"]), dollars(m["retainage_open_cents"]), _status(job),
                     text(domain.status_label(job, style)), _day(job_started_on(state, job)),
                     _day(job.get("completed_at")) if job.get("work_status") == "complete" else "", _day(job.get("closed_at"))])
    return rows


def payapps_rows(state: dict[str, Any]) -> list[list[str]]:
    """Every pay app but a deleted draft or one pulled back (Cancelled), drafts included."""
    rows = [["Number", "Job", "Client", "Period to", "Amount", "Retainage", "Status", "Final", "Sent", "Received", "Paid on"]]
    for job in state.get("jobs", []):
        for inv in job.get("invoices", []):
            if inv.get("status") == "void":
                continue
            pays = [p for p in job.get("payments", []) if p.get("invoice_id") == inv["id"]]
            paid_on = max((_day(p.get("matched_at")) for p in pays), default="") if inv.get("status") == "paid" else ""
            rows.append([text(inv.get("number")), text(job.get("name")), text(_client(state, job)), _day(inv.get("period_to")),
                         dollars(inv.get("amount_cents")), dollars(inv.get("retainage_cents")),
                         PAYAPP_WORDS.get(inv.get("status"), text(inv.get("status"))), "Yes" if inv.get("final") else "",
                         _day(inv.get("sent_at")), dollars(sum(int(p.get("amount_cents") or 0) for p in pays)), paid_on])
    return rows


def payments_rows(state: dict[str, Any]) -> list[list[str]]:
    """Payments on pay apps, then retainage the client released, oldest first."""
    rows = []
    for job in state.get("jobs", []):
        numbers = {inv["id"]: inv.get("number", "") for inv in job.get("invoices", [])}
        for p in job.get("payments", []):
            rows.append([_day(p.get("matched_at")), text(job.get("name")), text(_client(state, job)), text(numbers.get(p.get("invoice_id"), "")),
                         "Pay app", dollars(p.get("amount_cents")), text(p.get("reference"))])
        for p in job.get("retainage_payments", []):
            rows.append([_day(p.get("at")), text(job.get("name")), text(_client(state, job)), "", "Retainage",
                         dollars(p.get("amount_cents")), text(p.get("reference"))])
    rows.sort(key=lambda r: r[0])
    return [["Date", "Job", "Client", "Pay app", "For", "Amount", "Reference"]] + rows


def change_orders_rows(state: dict[str, Any]) -> list[list[str]]:
    rows = [["Number", "Job", "Title", "Reason", "Amount", "Status", "Signed on", "On a pay app"]]
    for job in state.get("jobs", []):
        for co in job.get("change_orders", []):
            reason = co.get("reason") or ""
            rows.append([f"CO {co.get('number', '')}", text(job.get("name")), text(co.get("title")),
                         text(domain.CO_REASONS.get(reason, reason)), dollars(co.get("amount_cents")),
                         CO_WORDS.get(co.get("status"), text(co.get("status"))), domain.co_signed_on(co),
                         "Yes" if co.get("included_in_billing") else "No"])
    return rows


BUILDERS = {"jobs": jobs_rows, "payapps": payapps_rows, "payments": payments_rows, "change-orders": change_orders_rows}


def csv_text(state: dict[str, Any], kind: str) -> str:
    """The file for one kind (KINDS), with a byte-order mark so Excel reads names in any alphabet."""
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(BUILDERS[kind](state))
    return "﻿" + out.getvalue()
