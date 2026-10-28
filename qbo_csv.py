"""Payments from a QuickBooks CSV export (P2-1, the CSV half of the QuickBooks import).

Until the QuickBooks Online connection is built, the bookkeeper exports received payments
(Reports, e.g. "Transaction List by Date", Export to CSV; rows whose Transaction Type is not
money in, such as invoices and refunds, are skipped) and pastes
them in. Duracash proposes a pay app for each row and records nothing until someone
applies the matches. Matching is keyed on the payment's reference (check number or QuickBooks
number) together with the customer, since two customers can send the same check number; a
reference can be recorded once per job, so importing the same file twice is harmless.

The column names QuickBooks uses are from memory, not from a real export, so the reader
accepts several spellings and finds the header row below any report-title lines.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree
from typing import Any

import domain

COLUMNS = {
    "date": {"date", "payment date", "txn date", "transaction date"},
    "ref": {"num", "no.", "no", "ref no", "ref no.", "ref #", "reference", "reference no.", "check no", "check no.", "check #", "payment ref",
            "check number", "check", "ref"},
    "customer": {"customer", "name", "received from", "customer:job", "customer/job", "payer", "customer name", "client", "payer name"},
    "amount": {"amount", "payment", "payment amount", "amount received", "total", "total amount", "paid", "amt", "amount paid"},
    "memo": {"memo", "memo/description", "description", "notes"},
    "type": {"transaction type", "type", "txn type"},
}
# Transaction types that are money in. An unfiltered report also lists invoices, bills and refunds.
MONEY_IN = {"payment", "sales receipt", "deposit", "receive payment"}
PAYAPP_RE = re.compile(r"\bPA-[A-Z0-9]{1,12}-\d{1,4}\b")
MAX_ROWS = 2_000
DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y", "%A, %B %d, %Y", "%m-%d-%Y", "%d.%m.%Y")
MAX_CANDIDATES = 20


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
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


_XL = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_XL_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_XLSX_XML = 20 * 1024 * 1024


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref or "").group(0):
        n = n * 26 + ord(ch) - 64
    return max(n - 1, 0)


def xlsx_to_csv(data: bytes) -> str:
    """The first sheet of an Excel file as CSV text, read with the standard library (R5-04).
    Numbers stay as written; a cell in a date format becomes MM/DD/YYYY. Anything unreadable gives ""."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            def xml(name: str):
                info = z.getinfo(name)
                if info.file_size > MAX_XLSX_XML:
                    raise ValueError("too large")
                return ElementTree.fromstring(z.read(name))
            names = set(z.namelist())
            shared = []
            if "xl/sharedStrings.xml" in names:
                shared = ["".join(t.text or "" for t in si.iter(f"{_XL}t")) for si in xml("xl/sharedStrings.xml").iter(f"{_XL}si")]
            # Cell styles that are dates (built-in formats 14-22, or a custom format with d, m and y).
            date_styles: set[int] = set()
            if "xl/styles.xml" in names:
                styles = xml("xl/styles.xml")
                custom = {int(f.get("numFmtId", 0)): (f.get("formatCode") or "").lower() for f in styles.iter(f"{_XL}numFmt")}
                xfs = styles.find(f"{_XL}cellXfs")
                for k, xf in enumerate(xfs if xfs is not None else []):
                    fmt = int(xf.get("numFmtId", 0))
                    code = custom.get(fmt, "")
                    if 14 <= fmt <= 22 or ("d" in code and "y" in code):
                        date_styles.add(k)
            sheet = "xl/worksheets/sheet1.xml"
            if sheet not in names:
                sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
                if not sheets:
                    return ""
                sheet = sheets[0]
            out = io.StringIO()
            writer = csv.writer(out, lineterminator="\n")
            for n, row in enumerate(xml(sheet).iter(f"{_XL}row")):
                if n > MAX_ROWS + 20:
                    break
                cells: dict[int, str] = {}
                for c in row.iter(f"{_XL}c"):
                    kind, v = c.get("t"), c.find(f"{_XL}v")
                    if kind == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter(f"{_XL}t"))
                    elif v is None or v.text is None:
                        value = ""
                    elif kind == "s":
                        value = shared[int(v.text)] if int(v.text) < len(shared) else ""
                    elif int(c.get("s", 0) or 0) in date_styles and re.fullmatch(r"\d+(\.\d+)?", v.text):
                        value = (datetime(1899, 12, 30) + timedelta(days=float(v.text))).strftime("%m/%d/%Y")
                    else:
                        value = v.text
                    cells[_col(c.get("r", ""))] = value.strip()
                if cells:
                    writer.writerow([cells.get(i, "") for i in range(max(cells) + 1)])
            return out.getvalue()
    except (zipfile.BadZipFile, KeyError, ValueError, ElementTree.ParseError, OSError):
        return ""


def text_of(ext: str, data: bytes) -> str:
    """A payments file's text: CSV as written, an Excel file's first sheet as CSV."""
    if ext == ".xlsx":
        return xlsx_to_csv(data)
    return data.decode("utf-8-sig", "replace") if ext in {".csv", ".txt"} else ""


def looks_like_payments(text: str) -> int:
    """How many payment rows a file has, when it reads as a payments export; 0 otherwise."""
    return len(parse(text)["rows"]) if text.strip() else 0


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
        warning = "No header row with an Amount column and a Customer or Num column was found."
        # Name what the file does have, so the person can rename a column or export another report.
        seen = next(([c.strip() for c in row if c.strip()] for row in rows[:20] if sum(1 for c in row if c.strip()) >= 2), [])
        if seen:
            warning += f" The columns seen: {', '.join(seen[:12])}."
        return {"rows": [], "warnings": [warning]}
    out, warnings = [], []
    for n, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        get = lambda name: row[cols[name]].strip() if name in cols and cols[name] < len(row) else ""
        if not any(c.strip() for c in row):
            continue
        kind = get("type")
        if kind and _norm(kind) not in MONEY_IN and not any(_norm(c).startswith("total") for c in row):
            warnings.append(f"Line {n}: skipped, {'an' if kind[:1].lower() in 'aeiou' else 'a'} {kind} is not money received.")
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
        when = _date(get("date"))
        if get("date") and not when:
            warnings.append(f"Line {n}: the date '{get('date')[:40]}' wasn't read; this payment will be dated today unless you fix the cell.")
        out.append({"line": n, "date": when, "ref": get("ref")[:60], "customer": get("customer")[:120],
                    "amount_cents": amount, "memo": get("memo")[:200]})
    return {"rows": out[:MAX_ROWS], "warnings": warnings[:50]}


def _same(cust: str, payer: str) -> bool:
    """A report's customer names this payer: one normalized name contains the other."""
    return bool(cust and payer) and (payer in cust or cust in payer)


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
    recorded = [(j, p, _norm(payers.get(j.get("payer_id"), {}).get("name", "")))
                for j in state.get("jobs", []) for p in j.get("payments", []) if p.get("reference")]
    def choice(job: dict[str, Any], inv: dict[str, Any], owed: int) -> dict[str, Any]:
        return {"job_id": job["id"], "job": job["name"], "invoice_id": inv["id"], "number": inv["number"], "owed_cents": owed}

    out = []
    for row in rows:
        result = {**row, "status": "no match", "reason": "No open pay app has this number, or this customer and amount.",
                  "job_id": None, "invoice_id": None, "number": None, "candidates": []}
        # A check number is only unique per customer: two customers can both send check 1001.
        # Without a customer column the number alone decides, to stay on the safe side.
        cust = _norm(row["customer"])
        seen = next((j for j, p, name in recorded if p["reference"] == row["ref"] and (not cust or _same(cust, name))), None) if row["ref"] else None
        if seen:
            what = "Check" if row["ref"].isdigit() else "Payment"
            who = f" from {row['customer']}" if row["customer"] else ""
            result.update(status="already recorded",
                          reason=f"{what} {row['ref']}{who} for {domain.money(row['amount_cents'])} is already recorded on {seen['name']}.")
            out.append(result)
            continue
        named = set(PAYAPP_RE.findall(f"{row['memo']} {row['ref']}"))
        hit = next(((j, i, r) for j, i, r in open_apps if i["number"] in named), None)
        reason, same = None, []
        if hit:
            reason = f"The memo or number names {hit[1]['number']}."
        else:
            same = [(j, i, r) for j, i, r in open_apps
                    if cust and _same(cust, _norm(payers.get(j.get("payer_id"), {}).get("name", ""))) and r == row["amount_cents"]]
            if len(same) == 1:
                hit, reason = same[0], f"Same customer and exactly the {domain.money(row['amount_cents'])} still owed."
            elif len(same) > 1:
                result.update(status="pick one", reason=f"{len(same)} open pay apps for this customer are for this amount. Pick one.")
        # The pay apps a person can pick from: the ones that fit, then this customer's, then the rest.
        mine = [(j, i, r) for j, i, r in open_apps if cust and _same(cust, _norm(payers.get(j.get("payer_id"), {}).get("name", "")))]
        ordered = (same if len(same) > 1 else []) + mine + open_apps
        seen_ids: set[str] = set()
        for j, i, r in ordered:
            if i["id"] not in seen_ids and len(seen_ids) < MAX_CANDIDATES:
                seen_ids.add(i["id"])
                result["candidates"].append(choice(j, i, r))
        if hit:
            job, inv, remaining = hit
            if row["amount_cents"] > remaining:
                # Nothing is picked: the person can record what is owed and place the rest (R5-03).
                result.update(status="too much", split=choice(job, inv, remaining),
                              reason=f"{domain.money(row['amount_cents'])} is more than the {domain.money(remaining)} owed on {inv['number']}.")
            else:
                result.update(status="match", reason=reason, job_id=job["id"], job=job["name"], invoice_id=inv["id"], number=inv["number"])
        out.append(result)
    return out
