"""Payments from a QuickBooks CSV export (P2-1, the CSV half of the QuickBooks import).

Until the QuickBooks Online connection is built, the bookkeeper exports received payments
(Reports, e.g. "Transaction List by Date" filtered to payments, Export to CSV) and pastes
them in. Billdable proposes a pay app for each row and records nothing until someone
applies the matches. Matching is keyed on the payment's reference (check number or QuickBooks
number), which can be recorded once per job, so importing the same file twice is harmless.

The column names QuickBooks uses are from memory, not from a real export, so the reader
accepts several spellings and finds the header row below any report-title lines.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any

import domain

COLUMNS = {
    "date": {"date", "payment date", "txn date", "transaction date"},
    "ref": {"num", "no.", "no", "ref no", "ref no.", "ref #", "reference", "reference no.", "check no", "check no.", "check #", "payment ref"},
    "customer": {"customer", "name", "received from", "customer:job", "customer/job", "payer"},
    "amount": {"amount", "payment", "payment amount", "amount received", "total", "total amount"},
    "memo": {"memo", "memo/description", "description", "notes"},
}
PAYAPP_RE = re.compile(r"\bPA-[A-Z0-9]{1,12}-\d{1,4}\b")
MAX_ROWS = 2_000


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _cents(raw: str) -> int | None:
    t = (raw or "").strip().replace("$", "").replace(",", "")
    negative = t.startswith("(") and t.endswith(")") or t.startswith("-")
    t = t.strip("()-")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", t):
        return None
    cents = int(round(float(t) * 100))
    return -cents if negative else cents


def _date(raw: str) -> str | None:
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse(text: str) -> dict[str, Any]:
    """{"rows": [{"line", "date", "ref", "customer", "amount_cents", "memo"}], "warnings": [...]}"""
    rows = list(csv.reader(io.StringIO(text or "")))[: MAX_ROWS + 20]
    header_at, cols = None, {}
    for n, row in enumerate(rows[:20]):
        found = {}
        for i, cell in enumerate(row):
            key = cell.strip().lower()
            for name, spellings in COLUMNS.items():
                if key in spellings and name not in found:
                    found[name] = i
        if "amount" in found and ("customer" in found or "ref" in found):
            header_at, cols = n, found
            break
    if header_at is None:
        return {"rows": [], "warnings": ["No header row with an Amount column and a Customer or Num column was found."]}
    out, warnings = [], []
    for n, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        get = lambda name: row[cols[name]].strip() if name in cols and cols[name] < len(row) else ""
        if not any(c.strip() for c in row):
            continue
        amount = _cents(get("amount"))
        if amount is None:
            if get("customer") or get("ref"):
                warnings.append(f"Line {n}: skipped, the amount {get('amount')!r} isn't a number.")
            continue
        if amount <= 0:
            warnings.append(f"Line {n}: skipped, {get('amount')} is not money received.")
            continue
        # Report totals: a "Total" cell anywhere, or no date, customer or number at all.
        if any(_norm(c).startswith("total") for c in row) or not (get("date") or get("customer") or get("ref")):
            continue
        out.append({"line": n, "date": _date(get("date")), "ref": get("ref")[:60], "customer": get("customer")[:120],
                    "amount_cents": amount, "memo": get("memo")[:200]})
    return {"rows": out[:MAX_ROWS], "warnings": warnings[:50]}


def propose(state: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A proposed pay app for each row, and why. Changes nothing."""
    payers = {p["id"]: p for p in state.get("payers", [])}
    open_apps = []
    for job in state.get("jobs", []):
        if job.get("closed"):
            continue
        for inv in job.get("invoices", []):
            if inv.get("status") in domain.OPEN_FOR_PAYMENT:
                paid = sum(p["amount_cents"] for p in job.get("payments", []) if p.get("invoice_id") == inv["id"])
                open_apps.append((job, inv, inv["amount_cents"] - paid))
    refs = {p.get("reference") for j in state.get("jobs", []) for p in j.get("payments", [])}
    out = []
    for row in rows:
        result = {**row, "status": "no match", "reason": "No open pay app has this number, or this customer and amount.",
                  "job_id": None, "invoice_id": None, "number": None}
        if row["ref"] and row["ref"] in refs:
            result.update(status="already recorded", reason=f"Payment {row['ref']} is already in Billdable.")
            out.append(result)
            continue
        named = set(PAYAPP_RE.findall(f"{row['memo']} {row['ref']}"))
        hit = next(((j, i, r) for j, i, r in open_apps if i["number"] in named), None)
        reason = None
        if hit:
            reason = f"The memo or number names {hit[1]['number']}."
        else:
            cust = _norm(row["customer"])
            same = [(j, i, r) for j, i, r in open_apps
                    if cust and (_norm(payers.get(j.get("payer_id"), {}).get("name", "")) in cust or cust in _norm(payers.get(j.get("payer_id"), {}).get("name", "")))
                    and r == row["amount_cents"]]
            if len(same) == 1:
                hit, reason = same[0], f"Same customer and exactly the {domain.money(row['amount_cents'])} still owed."
            elif len(same) > 1:
                result.update(status="pick one", reason=f"{len(same)} open pay apps for this customer are for this amount.")
        if hit:
            job, inv, remaining = hit
            if row["amount_cents"] > remaining:
                result.update(status="too much", reason=f"{domain.money(row['amount_cents'])} is more than the {domain.money(remaining)} owed on {inv['number']}.")
            else:
                result.update(status="match", reason=reason, job_id=job["id"], job=job["name"], invoice_id=inv["id"], number=inv["number"])
        out.append(result)
    return out
