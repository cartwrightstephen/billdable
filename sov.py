"""Schedule of values: quantity x unit rate lines, plus overhead and profit.

This is the way a trade sub bills (Stephen, Oct 12 call): each line is a
quantity at a unit rate, overhead and profit goes on top, and each pay app
bills the quantity completed since the last one, line by line, like an AIA
G703 continuation sheet.

Storage on the job:

    job["sov"] = {
        "op_bp": 1500,              # overhead and profit, basis points (15%)
        "lines": [{
            "id": "sov-1a2b3c", "item": "1", "description": "Rough-in",
            "unit": "ea", "qty_milli": 400000, "rate_cents": 4500,
            "scheduled_cents": 1800000, "done_milli": 120000,
            "stored_cents": 0,      # materials presently stored, not yet installed (G703 column F)
            "co_id": None,          # set on lines added by an approved CO
        }],
    }

Like a G703, the total to date on a line (column G) is work completed plus
materials stored, never more than the line's scheduled value. Each pay app
bills the change in that total, so stored materials are billed when they
arrive and not again when they are installed.

Quantities are stored in thousandths so 12.5 units is exact. Lump-sum lines
are unit "LS" with quantity 1; their progress is a fraction of 1 (0.35 = 35%).
Overhead and profit applies to the base contract lines only. Change-order
lines are added when a CO is approved, at the CO amount, which already
carries its markup.

Every amount is whole cents and every rounding is half-up on integers, so the
browser can repeat the arithmetic exactly.
"""

from __future__ import annotations

import csv
import io
import re
import secrets
from decimal import Decimal, DecimalException
from typing import Any

MAX_LINES = 300
MAX_OP_BP = 5000  # 50%
MAX_QTY_MILLI = 1_000_000_000_000  # one billion units
MAX_RATE_CENTS = 10_000_000_000  # $100,000,000
MAX_CELL = 60  # characters read from one pasted cell
MAX_CELLS = 30  # cells read from one pasted row
PLAIN_NUMBER_RE = re.compile(r"^-?(?:\d{1,15}(?:\.\d{0,6})?|\.\d{1,6})$")
LIVE_STATUSES = {"draft", "awaiting_approval", "sent", "approved", "partial", "paid"}


class SovError(ValueError):
    pass


# ---------- numbers ----------

def _decimal(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SovError(f"{what} is missing.")
    text = str(value)[:40].strip().replace(",", "").replace("$", "").replace(" ", "")
    if text.endswith("%"):
        text = text[:-1]
    if not text:
        raise SovError(f"{what} is missing.")
    # Plain decimals only: no exponents ("1e9999"), no infinities, nothing long.
    # Anything else is refused before Decimal sees it, so no input costs real CPU.
    if len(str(value)) > 40 or not PLAIN_NUMBER_RE.match(text):
        raise SovError(f"{what} must be a plain number, not {str(value)[:30]!r}.")
    try:
        return Decimal(text)
    except DecimalException:
        raise SovError(f"{what} must be a number.")


def to_milli(value: Any, what: str = "Quantity") -> int:
    """'12.5' -> 12500. Up to three decimals."""
    number = _decimal(value, what) * 1000
    if number != number.to_integral_value():
        raise SovError(f"{what} can have at most three decimals.")
    if abs(number) > MAX_QTY_MILLI:
        raise SovError(f"{what} is too large.")
    return int(number)


def to_cents(value: Any, what: str = "Rate") -> int:
    """'$1,234.50' -> 123450. Up to two decimals."""
    number = _decimal(value, what) * 100
    if number != number.to_integral_value():
        raise SovError(f"{what} can have at most two decimals (cents).")
    if abs(number) > MAX_RATE_CENTS:
        raise SovError(f"{what} is too large.")
    return int(number)


def to_bp(value: Any) -> int:
    """'15' or '15%' -> 1500 basis points. Up to two decimals."""
    if value in (None, ""):
        return 0
    number = _decimal(value, "Overhead and profit") * 100
    if number != number.to_integral_value():
        raise SovError("Overhead and profit can have at most two decimals.")
    bp = int(number)
    if bp < 0 or bp > MAX_OP_BP:
        raise SovError("Overhead and profit must be between 0% and 50%.")
    return bp


def extend(qty_milli: int, rate_cents: int) -> int:
    """Quantity x rate in cents, rounded half-up."""
    return (qty_milli * rate_cents + 500) // 1000


def markup(base_cents: int, op_bp: int) -> int:
    return (base_cents * op_bp + 5000) // 10000


def fmt_qty(milli: int) -> str:
    whole, frac = divmod(int(milli), 1000)
    return f"{whole:,}" if not frac else f"{whole:,}.{frac:03d}".rstrip("0")


# ---------- reading the schedule ----------

def lines(job: dict[str, Any]) -> list[dict[str, Any]]:
    return (job.get("sov") or {}).get("lines") or []


def has_sov(job: dict[str, Any]) -> bool:
    return any(not line.get("co_id") for line in lines(job))


def op_bp(job: dict[str, Any]) -> int:
    return int((job.get("sov") or {}).get("op_bp") or 0)


def line_completed(line: dict[str, Any]) -> int:
    """Value of the work completed on a line (G703 D + E)."""
    if line["done_milli"] >= line["qty_milli"]:
        return line["scheduled_cents"]
    return extend(line["done_milli"], line["rate_cents"])


def line_stored(line: dict[str, Any]) -> int:
    """Materials presently stored on a line (G703 F), within what the line has left."""
    return max(0, min(int(line.get("stored_cents") or 0), line["scheduled_cents"] - line_completed(line)))


def line_to_date(line: dict[str, Any]) -> int:
    """Total completed and stored to date (G703 G)."""
    return line_completed(line) + line_stored(line)


def retainage_on(cents: int, pct: int) -> int:
    """Retainage at a whole percent, half-up, for display beside a line."""
    return (cents * pct + 50) // 100


def sheet_columns(entry: dict[str, Any], retainage_pct: int = 0) -> dict[str, int]:
    """The G703 columns for one continuation-sheet entry. Entries from before stored
    materials have no work/stored split: all of their billing is work."""
    to_date = entry["to_date_cents"]
    stored = int(entry.get("stored_cents") or 0)
    work_this = entry.get("work_this_cents", entry["amount_cents"])
    previous = entry.get("prev_work_cents", to_date - entry["amount_cents"] - stored)
    return {
        "scheduled": entry["scheduled_cents"],
        "previous": previous,
        "this_period": work_this,
        "stored": stored,
        "to_date": to_date,
        "pct": (100 * to_date) // entry["scheduled_cents"] if entry["scheduled_cents"] else 0,
        "balance": entry["scheduled_cents"] - to_date,
        "retainage": retainage_on(to_date, retainage_pct),
    }


def totals(job: dict[str, Any]) -> dict[str, int]:
    base = [l for l in lines(job) if not l.get("co_id")]
    cos = [l for l in lines(job) if l.get("co_id")]
    bp = op_bp(job)
    base_scheduled = sum(l["scheduled_cents"] for l in base)
    base_to_date = sum(line_to_date(l) for l in base)
    co_scheduled = sum(l["scheduled_cents"] for l in cos)
    co_to_date = sum(line_to_date(l) for l in cos)
    op_scheduled = markup(base_scheduled, bp)
    op_to_date = markup(base_to_date, bp)
    return {
        "base_scheduled_cents": base_scheduled,
        "op_scheduled_cents": op_scheduled,
        "contract_cents": base_scheduled + op_scheduled,
        "co_scheduled_cents": co_scheduled,
        "scheduled_cents": base_scheduled + op_scheduled + co_scheduled,
        "base_to_date_cents": base_to_date,
        "op_to_date_cents": op_to_date,
        "co_to_date_cents": co_to_date,
        "to_date_cents": base_to_date + op_to_date + co_to_date,
    }


def live_invoices(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [inv for inv in job.get("invoices", []) if inv.get("status") in LIVE_STATUSES]


def billed(job: dict[str, Any]) -> dict[str, Any]:
    """What earlier pay apps (sent or drafted, not void) already billed, per line."""
    by_line: dict[str, int] = {}
    qty_by_line: dict[str, int] = {}
    work_by_line: dict[str, int] = {}
    op = 0
    co_line = {l["co_id"]: l["id"] for l in lines(job) if l.get("co_id")}
    for inv in live_invoices(job):
        for entry in inv.get("sov_lines", []):
            by_line[entry["line_id"]] = by_line.get(entry["line_id"], 0) + entry["amount_cents"]
        # Quantities come from the whole continuation sheet: a line can move on a $0 period
        # (stored materials installed) and still be on the pay app at its new quantity.
        for entry in inv.get("sov_sheet") or inv.get("sov_lines", []):
            qty_by_line[entry["line_id"]] = max(qty_by_line.get(entry["line_id"], 0), entry.get("qty_milli_to_date", 0))
            work = entry.get("work_to_date_cents", entry["to_date_cents"])
            work_by_line[entry["line_id"]] = max(work_by_line.get(entry["line_id"], 0), work)
        op += inv.get("op_cents", 0)
        # An unsigned CO billed before it was approved counts against its line.
        for entry in inv.get("lines", []):
            line_id = co_line.get(entry.get("co_id"))
            if line_id:
                by_line[line_id] = by_line.get(line_id, 0) + entry["amount_cents"]
    return {"by_line": by_line, "qty_by_line": qty_by_line, "work_by_line": work_by_line, "op_cents": op}


def this_period(job: dict[str, Any]) -> dict[str, Any]:
    """The pay-app lines for work completed since the last pay app."""
    already = billed(job)
    out, sheet = [], []
    for line in lines(job):
        to_date = line_to_date(line)
        amount = to_date - already["by_line"].get(line["id"], 0)
        credit = line["scheduled_cents"] < 0 and amount < 0
        previous_qty = already["qty_by_line"].get(line["id"], 0)
        completed = line_completed(line)
        prev_work = min(already["work_by_line"].get(line["id"], 0), completed)
        entry = {
            "line_id": line["id"],
            "item": line.get("item", ""),
            "description": line["description"],
            "unit": line["unit"],
            "rate_cents": line["rate_cents"],
            "scheduled_cents": line["scheduled_cents"],
            "qty_milli_this": max(0, line["done_milli"] - previous_qty),
            "qty_milli_to_date": line["done_milli"],
            "qty_milli": line["qty_milli"],
            # A credit change order's line (R3-06) bills negative, coming off this pay app.
            "amount_cents": amount if credit else max(0, amount),
            "to_date_cents": to_date,
            "work_to_date_cents": completed,
            "prev_work_cents": prev_work,
            "work_this_cents": completed - prev_work,
            "stored_cents": line_stored(line),
        }
        # The continuation sheet lists every line, like a G703; only lines with new work are billed.
        sheet.append(entry)
        if amount > 0 or credit:
            out.append(entry)
    op_this = max(0, totals(job)["op_to_date_cents"] - already["op_cents"])
    return {"lines": out, "sheet": sheet, "op_cents": op_this, "total_cents": sum(l["amount_cents"] for l in out) + op_this}


def unbilled(job: dict[str, Any]) -> int:
    return this_period(job)["total_cents"]


def line_label(entry: dict[str, Any]) -> str:
    item = f"{entry['item']}. " if entry.get("item") else ""
    pct = (100 * entry["qty_milli_to_date"]) // entry["qty_milli"] if entry["qty_milli"] else 0
    if entry["unit"].upper() == "LS" and entry["qty_milli"] == 1000:
        return f"{item}{entry['description']} ({pct}% complete to date)"
    return (
        f"{item}{entry['description']}: {fmt_qty(entry['qty_milli_this'])} {entry['unit']} this period"
        f" × ${entry['rate_cents'] / 100:,.2f} ({fmt_qty(entry['qty_milli_to_date'])} of {fmt_qty(entry['qty_milli'])}, {pct}% to date)"
    )


def continuation_sheet(job: dict[str, Any], invoice: dict[str, Any]) -> str:
    """A plain-text G703-style table of an invoice's schedule-of-values lines."""
    entries = invoice.get("sov_sheet") or invoice.get("sov_lines") or []
    if not entries:
        return ""
    pct_ret = int(invoice.get("retainage_pct", job.get("retainage_pct")) or 0)  # as held on this pay app (R4-07)
    rows = [("Item", "Description", "Scheduled", "Previous", "This period", "Stored", "To date", "%", "Balance", "Retainage")]
    for e in entries:
        c = sheet_columns(e, pct_ret)
        rows.append((
            e.get("item", ""),
            e["description"][:40],
            *(f"{c[k] / 100:,.2f}" for k in ("scheduled", "previous", "this_period", "stored", "to_date")),
            f"{c['pct']}%",
            f"{c['balance'] / 100:,.2f}",
            f"{c['retainage'] / 100:,.2f}",
        ))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    text = []
    for n, r in enumerate(rows):
        text.append("  ".join(c.ljust(widths[i]) if i < 2 else c.rjust(widths[i]) for i, c in enumerate(r)).rstrip())
        if n == 0:
            text.append("-" * len(text[0]))
    if invoice.get("op_cents"):
        text.append(f"Overhead and profit {op_bp(job) / 100:g}% on this period's base work: {invoice['op_cents'] / 100:,.2f}")
    return "\n".join(text)


# ---------- building lines from input ----------

def clean_lines(raw_lines: Any, existing: list[dict[str, Any]], new_id) -> list[dict[str, Any]]:
    """Validate lines sent by the editor. Keeps ids and progress of lines that already exist."""
    if not isinstance(raw_lines, list):
        raise SovError("Lines must be a list.")
    if len(raw_lines) > MAX_LINES:
        raise SovError(f"A schedule can have at most {MAX_LINES} lines.")
    old = {l["id"]: l for l in existing if not l.get("co_id")}
    # Ids must be unique: a repeated id would merge two lines' billing and under-bill the job.
    used = {l["id"] for l in existing if l.get("co_id")}
    out, errors = [], []
    for n, raw in enumerate(raw_lines, start=1):
        if not isinstance(raw, dict):
            errors.append(f"Line {n} is not a line.")
            continue
        try:
            description = str(raw.get("description") or "").strip()[:200]
            if not description:
                raise SovError("needs a description")
            unit = str(raw.get("unit") or "").strip()[:12]
            # A blank quantity (spaces too, as the editor reads it) is one: a lump sum.
            quantity = raw.get("quantity")
            qty = to_milli(1 if quantity is None or str(quantity).strip() == "" else quantity, "quantity")
            if str(raw.get("rate") if raw.get("rate") is not None else "").strip() == "":
                raise SovError("needs a price (unit rate)")
            rate = to_cents(raw.get("rate"), "rate")
            if qty <= 0:
                raise SovError("quantity must be more than zero")
            if rate <= 0:
                raise SovError("rate must be more than zero")
            if not unit:
                unit = "LS" if qty == 1000 else "ea"
            scheduled = extend(qty, rate)
            if scheduled > 10_000_000_000:
                raise SovError("is more than $100,000,000")
        except SovError as exc:
            errors.append(f"Line {n} ({str(raw.get('description') or '').strip()[:40] or 'no description'}): {str(exc).rstrip('.')}.")
            continue
        prior = old.get(raw.get("id")) if isinstance(raw.get("id"), str) else None
        if prior and prior["id"] in used:
            prior = None  # the same id sent twice: the second copy is a new line
        line_id = prior["id"] if prior else new_id()
        while line_id in used or (not prior and line_id in old):
            line_id = new_id()
        used.add(line_id)
        entry = {
            "id": line_id,
            "item": str(raw.get("item") or "").strip()[:12] or str(n),
            "description": description,
            "unit": unit,
            "qty_milli": qty,
            "rate_cents": rate,
            "scheduled_cents": scheduled,
            "done_milli": min(prior["done_milli"], qty) if prior else 0,
            "stored_cents": 0,
            "co_id": None,
        }
        if prior:  # materials stored on the line stay, within what the edited line has left
            entry["stored_cents"] = max(0, min(int(prior.get("stored_cents") or 0), scheduled - line_completed(entry)))
        out.append(entry)
    if errors:
        raise SovError(" ".join(errors[:10]) + (f" And {len(errors) - 10} more." if len(errors) > 10 else ""))
    return out


# ---------- pasting from a spreadsheet ----------

HEADER_WORDS = {
    "description": ("description", "desc", "item description", "scope", "work", "task", "line item", "name"),
    "item": ("item", "item no", "item #", "no", "#", "line", "code", "cost code"),
    "quantity": ("qty", "quantity", "quantities", "count", "units"),
    "unit": ("unit", "uom", "um", "u/m", "unit of measure"),
    "rate": ("rate", "unit rate", "unit price", "price", "unit cost", "cost", "each"),
    "amount": ("amount", "total", "value", "scheduled value", "extended", "ext", "line total", "sum"),
}
NUMBER_RE = re.compile(r"^\(?-?\$?(?:\d[\d,]{0,20})?(?:\.\d{1,6})?\)?%?$")
QTY_UNIT_RE = re.compile(r"^(\d[\d,]*(?:\.\d+)?)\s+([A-Za-z]{1,6}\.?)$")
# "Tile 1200 SF @ 8.50", "1,200 sf at $8.50/sf" or "300 LF x 4.25" in one cell: a description (optional),
# a quantity, a unit (optional) and a rate.
AT_RATE_RE = re.compile(
    r"^(?:(?P<desc>[^@]*?[A-Za-z][^@]*?)\s+)?(?P<qty>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>[A-Za-z]{1,6}\.?)?"
    r"\s*(?:@|\bat\b|\bx\b)\s*\$?(?P<rate>\d[\d,]*(?:\.\d{1,2})?)\s*(?:/\s*[A-Za-z]{1,6}\.?|\bper\s+[A-Za-z]{1,6}\.?)?$",
    re.I,
)
ITEM_RE = re.compile(r"^[A-Za-z]?\d+(\.\d+)*[A-Za-z]?$")
# Words that say "how many", not "how much": a lone number followed by one of these is a quantity
# with no price ("Site cleanup, 3 loads"), never a lump sum of $3. LS and "lot" are left out on purpose.
UNIT_WORDS = frozenset("""
    ea each pc pcs piece pieces unit units set sets
    load loads trip trips truck trucks
    hr hrs hour hours day days wk wks week weeks mo mos month months
    lf ft feet foot sf sqft sy cy cf yd yds m m2 m3 lm
    ton tons lb lbs gal gals bag bags roll rolls sheet sheets box boxes pallet pallets
    crew crews man men visit visits
""".split())
# "Concrete 2,400 SF" or "Haul 3 loads" in one cell: a description, a quantity and a unit word.
DESC_QTY_UNIT_RE = re.compile(r"^(?P<desc>.*?[A-Za-z].*?)\s+(?P<qty>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>[A-Za-z]{1,6}\.?)$")


def _desc_qty_unit(cell: str) -> "re.Match[str] | None":
    m = DESC_QTY_UNIT_RE.match(cell.strip())
    return m if m and m["unit"].lower().rstrip(".") in UNIT_WORDS else None


TOTAL_RE = re.compile(r"^(sub\s*-?\s*total|grand\s+total|total|totals|overhead|o\s*&\s*p|profit|markup|retainage)\b", re.I)


def _split_rows(text: str) -> list[list[str]]:
    # Bounded before any pattern runs: 2,000 lines of 2,000 characters, 30 cells of 200.
    lines_in = [line[:2000] for line in text.splitlines()[:2000]]
    rows = []
    if any("\t" in line for line in lines_in):
        # Copied from Excel or Google Sheets.
        rows = list(csv.reader(lines_in, delimiter="\t"))
    else:
        for line in lines_in:
            if re.search(r"\S {2,}\S", line.strip()):
                # Columns separated by runs of spaces (copied from a PDF); commas are thousands separators.
                rows.append(re.split(r" {2,}", line.strip()))
            elif ((r := AT_RATE_RE.match(line.strip()) or _desc_qty_unit(line)) and "," in line
                  and "," not in (r["desc"] or "")):
                # "Floor tile 1,200 sf at $8.50/sf" or "Concrete 2,400 SF": the comma is a thousands
                # separator, not a column.
                rows.append([line.strip()])
            else:
                rows.extend(_join_thousands(row, ", " in line) for row in csv.reader([line]))
    rows = [[c.strip()[:200] for c in row[:MAX_CELLS]] for row in rows]
    return [r for r in rows if any(c for c in r)]


def _join_thousands(row: list[str], spaced: bool) -> list[str]:
    """In a typed comma row, "$12,500.00" splits into "$12" and "500.00"; put it back together.
    Without a $ sign, "Demo, 3,500" is joined too when the row puts a space after its other commas
    (`spaced`): the comma with no space is then a thousands separator. A row with no ", " at all is
    a saved CSV file, where "Demo,3,500" means quantity 3 at $500, so it stays split. The joined part may
    run on into a unit and a rate ("Tile, 1,200 SF @ 8.50")."""
    out: list[str] = []
    for cell in row:
        groups = r"^\s*\$\d{1,3}(,\d{3})*$" if not spaced else r"^\s*\$?\d{1,3}(,\d{3})*$"
        if out and re.match(groups, out[-1]) and re.match(r"^\d{3}(\.\d{1,2})?(?=$|\s+\D)", cell):
            out[-1] = out[-1] + "," + cell
        else:
            out.append(cell)
    return out


def _counts_units(row: list[str], at: int, description_at: int) -> str:
    """The unit word next to the number at row[at] ("3 loads", "12 hrs", or "loads, 3" from a sheet with
    Unit before Qty), so it counts things rather than dollars; "" when there is none. The description
    cell is never the unit. A number with a $ sign is always money."""
    if "$" in row[at]:
        return ""
    for i in (at + 1, at - 1):
        if 0 <= i < len(row) and i != description_at and row[i].lower().rstrip(".") in UNIT_WORDS:
            return row[i]
    return ""


def _is_number(cell: str) -> bool:
    cell = cell.replace(" ", "")
    return 0 < len(cell) <= MAX_CELL and any(c.isdigit() for c in cell) and bool(NUMBER_RE.match(cell))


def _header_map(row: list[str]) -> dict[str, int] | None:
    found: dict[str, int] = {}
    for i, cell in enumerate(row):
        key = re.sub(r"[^a-z#/& ]", "", cell.lower()).strip()
        for field, words in HEADER_WORDS.items():
            if key in words and field not in found:
                found[field] = i
                break
    if "description" in found and ({"quantity", "rate", "amount"} & set(found)):
        return found
    return None


def parse_rows(text: str) -> dict[str, Any]:
    """Read schedule-of-values rows pasted from Excel, Google Sheets, a CSV or a PDF.

    With a header row (one of the first 5), columns are taken by name. Without one: the first text
    cell is the description, a short text cell is the unit, and the numbers are
    quantity and rate (two numbers), quantity, rate and amount (three), or a
    lump-sum amount (one). One number next to a unit word ("3 loads", or
    "loads, 3" when Unit comes before Qty) is a quantity with no price: the line
    comes back with a blank rate and needs_price set, and a warning says to enter
    the rate. Under a header, a quantity with the rate and amount blank comes back
    the same way. Total and markup rows are skipped and reported.
    """
    rows = _split_rows(text or "")
    lines_out: list[dict[str, str]] = []
    warnings: list[str] = []
    # The header can sit under a title line or two (a company name, "Schedule of values"):
    # look for it in the first 5 rows, the way qbo_csv looks in its first 20.
    header, at = None, 0
    for i, row in enumerate(rows[:5]):
        header = _header_map(row)
        if header:
            at = i
            break
    for i, row in enumerate(rows[:at]):
        title = next((c for c in row if c), "")[:80]
        warnings.append(f"Row {i + 1} ({title}) skipped: text above the header.")
    body = rows[at + 1:] if header else rows
    op_hint = ""
    for n, row in enumerate(body, start=at + 2 if header else 1):
        if header:
            cell = lambda f: row[header[f]].strip() if f in header and header[f] < len(row) else ""
            description, unit, item = cell("description"), cell("unit"), cell("item")
            qty, rate, amount = cell("quantity"), cell("rate"), cell("amount")
        else:
            # "1,200 lf" in one cell is a quantity and a unit; "Tile 1200 SF @ 8.50" is a description,
            # a quantity, a unit and a rate; "Haul 3 loads" a description, a quantity and a unit.
            split_row: list[str] = []
            for c in row:
                m = QTY_UNIT_RE.match(c)
                r = None if m else AT_RATE_RE.match(c.strip())
                d = None if m or r else _desc_qty_unit(c)
                if m:
                    split_row.extend([m.group(1), m.group(2)])
                elif r:
                    split_row.extend(x for x in (r["desc"], r["qty"], r["unit"], r["rate"]) if x)
                elif d:
                    split_row.extend([d["desc"], d["qty"], d["unit"]])
                else:
                    split_row.append(c)
            row = split_row
            texts = [(i, c) for i, c in enumerate(row) if c and not _is_number(c)]
            at_numbers = [i for i, c in enumerate(row) if _is_number(c)]
            item = ""
            if row and ITEM_RE.match(row[0]) and len(texts) >= 1 and texts[0][0] > 0:
                item = row[0]
                at_numbers = [i for i in at_numbers if i > 0]
            numbers = [row[i] for i in at_numbers]
            description = texts[0][1] if texts else ""
            unit = next((c for _, c in texts[1:] if len(c) <= 8), "")
            qty = rate = amount = ""
            count_unit = _counts_units(row, at_numbers[0], texts[0][0] if texts else -1) if len(numbers) == 1 else ""
            if count_unit:
                # "Site cleanup, 3 loads": how many, not how much. No price is made up.
                qty, unit = numbers[0], count_unit
            elif len(numbers) == 1:
                amount = numbers[0]
            elif len(numbers) == 2:
                qty, rate = numbers
            elif len(numbers) >= 3:
                qty, rate, amount = numbers[0], numbers[1], numbers[2]
        if not description:
            if any(row):
                warnings.append(f"Row {n} skipped: no description.")
            continue
        if TOTAL_RE.match(description):
            pct = re.search(r"(\d{1,3}(?:\.\d{1,2})?)\s*%", " ".join(row))
            if pct and re.match(r"^(overhead|o\s*&\s*p|profit|markup)", description, re.I):
                op_hint = pct.group(1)
                warnings.append(f"Row {n} ({description}) read as overhead and profit of {op_hint}%; check it below.")
            else:
                warnings.append(f"Row {n} ({description}) looks like a total or markup row and was skipped.")
            continue
        try:
            if qty and rate:
                q, r = to_milli(qty, "quantity"), to_cents(rate, "rate")
                if amount:
                    a = to_cents(amount, "amount")
                    if abs(extend(q, r) - a) > 100:
                        warnings.append(
                            f"Row {n} ({description}): quantity × rate is ${extend(q, r) / 100:,.2f}, "
                            f"but the row says ${a / 100:,.2f}. Kept quantity and rate; check it."
                        )
            elif qty and amount and to_milli(qty, "quantity") > 0 and (
                to_cents(amount, "amount") * 1000 % to_milli(qty, "quantity") == 0
            ):
                # Quantity and amount but no rate: the rate is amount / quantity when that is whole cents.
                q = to_milli(qty, "quantity")
                r = to_cents(amount, "amount") * 1000 // q
            elif amount or rate:
                q, r = 1000, to_cents(amount or rate, "amount")
                unit = unit or "LS"
                if qty:
                    warnings.append(f"Row {n} ({description}): amount doesn't divide evenly by the quantity, so it was read as a lump sum.")
            elif qty:
                # A quantity with no rate or amount, under a header or as "3 loads": the line comes in
                # with the rate blank and needs_price, never skipped and never priced.
                q, r = to_milli(qty, "quantity"), 0
                if q <= 0:
                    warnings.append(f"Row {n} ({description}) has a zero or negative number; skipped.")
                    continue
                warnings.append(f"Row {n} ({description}): {(fmt_qty(q) + ' ' + unit).strip()} has no price. Enter the unit rate before you save.")
                lines_out.append({
                    "item": item[:12],
                    "description": description[:200],
                    "unit": unit[:12],
                    "quantity": fmt_qty(q).replace(",", ""),
                    "rate": "",
                    "needs_price": True,
                })
                if len(lines_out) >= MAX_LINES:
                    warnings.append(f"Stopped at {MAX_LINES} lines.")
                    break
                continue
            else:
                warnings.append(f"Row {n} ({description}) has no numbers; skipped.")
                continue
        except SovError as exc:
            warnings.append(f"Row {n} ({description}): {exc}")
            continue
        if q <= 0 or r <= 0:
            warnings.append(f"Row {n} ({description}) has a zero or negative number; skipped.")
            continue
        lines_out.append({
            "item": item[:12],
            "description": description[:200],
            "unit": unit[:12] or ("LS" if q == 1000 else "ea"),
            "quantity": fmt_qty(q).replace(",", ""),
            "rate": f"{r / 100:.2f}",
        })
        if len(lines_out) >= MAX_LINES:
            warnings.append(f"Stopped at {MAX_LINES} lines.")
            break
    return {"lines": lines_out, "warnings": warnings, "op_pct": op_hint, "header": bool(header)}
