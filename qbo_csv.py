"""Payments from a QuickBooks CSV export (P2-1, the CSV half of the QuickBooks import).

Until the QuickBooks Online connection is built, the bookkeeper exports received payments
(Reports, e.g. "Transaction List by Date", Export to CSV; rows whose Transaction Type is not
money in, such as invoices and refunds, are skipped) and pastes
them in. Duracash proposes a pay app for each row and records nothing until someone
applies the matches. Matching is keyed on the payment's reference (check number or QuickBooks
number) together with the customer, since two customers can send the same check number; a
reference can be recorded once per job (the amount must agree too: one check can pay two jobs,
and a word like ACH can be every payment's reference), and a payment with no number is known by
its customer, amount and day, so importing the same file twice is harmless. One recorded a few
days from the row's date, keyed in by hand say, leaves the row unticked with the reason.

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
# A payments export has a dozen columns. Cells past column IV (256) are left out, so a cell
# addressed far to the right can't make a row of millions of empty cells.
MAX_COLS = 256


def _col(ref: str) -> int:
    """The column number of a cell reference ("A1" is 0), or MAX_COLS for one past the limit."""
    n = 0
    for ch in re.match(r"[A-Z]*", ref or "").group(0):
        n = n * 26 + ord(ch) - 64
        if n > MAX_COLS:
            return MAX_COLS
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
                        try:
                            value = (datetime(1899, 12, 30) + timedelta(days=float(v.text))).strftime("%m/%d/%Y")
                        except OverflowError:  # past the year 9999: not a date, keep the number
                            value = v.text
                    else:
                        value = v.text
                    col = _col(c.get("r", ""))
                    if col < MAX_COLS:
                        cells[col] = value.strip()
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
    unreadable = {"rows": [], "warnings": ["The file couldn't be read. Save it again as CSV (UTF-8) and add it again."]}
    # A NUL byte means a UTF-16 or binary file. Newer Pythons' csv reads it without complaint, so
    # it is checked here rather than left to csv.Error, the same on every version.
    if "\x00" in (text or ""):
        return unreadable
    try:
        rows = list(csv.reader(io.StringIO(text or "")))[: MAX_ROWS + 20]
    except csv.Error:  # one cell over 128 KB
        return unreadable
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
    """A report's customer names this payer: one normalized name holds the other's whole words
    ("Northline GC Inc" and "Northline GC"; not "Grace Roofing" and "Ace Roofing")."""
    return bool(cust and payer) and (f" {payer} " in f" {cust} " or f" {cust} " in f" {payer} ")


NEAR_DAYS = 7   # a payment with no number keyed in this many days from the books' date is the same one


def _day(payment: dict[str, Any]) -> str:
    return str(payment.get("matched_at") or "")[:10]


def _apart(a: str, b: str) -> int | None:
    try:
        return abs((datetime.strptime(a, "%Y-%m-%d") - datetime.strptime(b, "%Y-%m-%d")).days)
    except ValueError:
        return None


def _made_up(payment: dict[str, Any]) -> bool:
    """Recorded with no number: Duracash's manual-N, or a QuickBooks payment's "QBO <id>"."""
    ref, qid = payment["reference"], payment.get("qbo_payment_id")
    return ref.startswith("manual-") or bool(qid and ref.split(" (rest)")[0] == f"QBO {qid}")


def _already(row: dict[str, Any], recorded: list, used: set[int], stage: str) -> tuple[dict[str, Any], str] | None:
    """(job, day) of the recorded payment (or the parts of a split one) this row already is, or
    None; what it finds is marked used. `day` is "" when it is surely this row. Stage "one" looks
    for a single payment, "split" also for the parts of a split one, and "near" for a payment of
    the same amount dated a few days apart, which may be this one, and `day` is its date."""
    near = stage == "near"
    cust, qid = _norm(row["customer"]), row.get("qbo_id") or ""
    amount, day = row["amount_cents"], row["date"]

    def free(p: dict[str, Any]) -> bool:
        # A QuickBooks payment's own parts are counted by its id (qbo.propose), another's are not this one.
        return id(p) not in used and not (qid and p.get("qbo_payment_id"))

    def take(found: list, when: str = "") -> tuple[dict[str, Any], str]:
        used.update(id(p) for _, p in found)
        return found[0][0], when

    def nearest(found: list) -> tuple[dict[str, Any], str] | None:
        close = sorted(((gap, n, j, p) for n, (j, p) in enumerate(found) if p["amount_cents"] == amount
                        for gap in [_apart(_day(p), day)] if gap is not None and gap <= NEAR_DAYS), key=lambda t: t[:2])
        return take([close[0][2:]], _day(close[0][3])) if close else None

    number = "" if row.get("no_number") else row["ref"]
    if number:
        # A check number is only unique per customer: two customers can both send check 1001.
        # Without a customer column the number alone decides, to stay on the safe side. One check
        # can pay two jobs, so the amount must agree too (the parts of a split add up to it, its
        # rest on any job), and a word used for every payment, like ACH, needs the same day.
        word = not any(c.isdigit() for c in number)
        same = [(j, p) for j, p, name in recorded if free(p) and p["reference"] == number and (not cust or _same(cust, name))]
        if near:
            return nearest(same) if word and day else None
        if word and day:
            same = [(j, p) for j, p in same if _day(p) == day]
        one = next(((j, p) for j, p in same if p["amount_cents"] == amount), None)
        if one:
            return take([one])
        if stage == "one":
            return None
        rest = [(j, p) for j, p, _ in recorded if free(p) and p["reference"].startswith(f"{number} (rest)")
                and (not word or not day or _day(p) == day)]
        parts = same + rest
        return take(parts) if same and sum(p["amount_cents"] for _, p in parts) == amount else None
    if not day:
        return None
    # No number (an ACH, say): recorded with a made-up reference, so the same customer, amount and
    # day stand in for it, and the same file read again stays harmless.
    loose = [(j, p, name) for j, p, name in recorded if free(p) and _made_up(p)]
    mine = [(j, p) for j, p, name in loose if not cust or _same(cust, name)]
    if near:
        return nearest(mine)
    today = [(j, p) for j, p in mine if _day(p) == day]
    one = next(((j, p) for j, p in today if p["amount_cents"] == amount), None)
    if one:
        return take([one])
    if stage == "one":
        return None
    # Split over two pay apps: the rest may be on another customer's job, recorded the same day.
    rest = [(j, p) for j, p, _ in loose if _day(p) == day]
    pair = next(([a, b] for a in today for b in rest if b[1] is not a[1] and a[1]["amount_cents"] + b[1]["amount_cents"] == amount), None)
    return take(pair) if pair else None


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

    # Which recorded payment each row already is: each one stands for one row only, the same day
    # first and then a few days apart, so one payment keyed in by hand doesn't hide a second.
    # Every row takes its own payment before any row takes two as a split, whatever the row order.
    used: set[int] = set()
    seen_as: list[tuple[dict[str, Any], str] | None] = [None] * len(rows)
    for stage in ("one", "split", "near"):
        for n, row in enumerate(rows):
            if seen_as[n] is None:
                seen_as[n] = _already(row, recorded, used, stage)

    out = []
    for n, row in enumerate(rows):
        result = {**row, "status": "no match", "reason": "No open pay app has this number, or this customer and amount.",
                  "job_id": None, "invoice_id": None, "number": None, "candidates": []}
        cust = _norm(row["customer"])
        seen, maybe = seen_as[n] or (None, "")
        who = f" from {row['customer']}" if row["customer"] else ""
        if seen and not maybe:
            if row["ref"] and not row.get("no_number"):
                what = "Check" if row["ref"].isdigit() else "Payment"
                reason = f"{what} {row['ref']}{who} for {domain.money(row['amount_cents'])} is already recorded on {seen['name']}."
            else:
                reason = f"A payment{who} for {domain.money(row['amount_cents'])} on {row['date']} is already recorded on {seen['name']}."
            result.update(status="already recorded", reason=reason)
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
        if maybe:
            # Recorded a few days from this date (keyed in by hand, say): likely this payment, so
            # nothing is ticked, and the pay apps stay to pick from if it is another one.
            hit, same = None, []
            result.update(status="no match", reason=f"A payment{who} for {domain.money(row['amount_cents'])} dated {maybe} is already "
                                                    f"recorded on {seen['name']}. If this is a different payment, pick its pay app.")
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
