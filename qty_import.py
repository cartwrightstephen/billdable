"""Quantities from the contractor's own records onto a job's schedule of values (M1, Stephen's
Oct 29 review: the pay app is "populated as daily records are filled out ... or as information is
imported or retrieved from the contractor's current systems").

A CSV or Excel sheet (a takeoff, a foreman's log, an estimating or accounting export) lists
quantities put in place this period. Each row is matched to a line of the job's schedule by its
item number, else its description (case and punctuation don't matter), and read the way a daily
record's quantities are: added to the line's quantity done, a lump sum as the % done this period.
`preview` says what each row would do and changes nothing; `apply` adds the rows that can be
added, through record_sov_progress like a daily record (domain/daily.py), and says plainly which
couldn't and why. Nothing typed elsewhere is lost: the import is kept on the job record.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from decimal import Decimal
from typing import Any

import sov

MAX_ROWS = 500
MAX_TEXT = 2_000_000

# Header words, normalized as sov._header_map normalizes a cell.
HEADERS = {
    "item": ("item", "item no", "item #", "item number", "no", "#", "line", "line no", "line #", "line number", "code", "cost code"),
    "description": ("description", "desc", "item description", "scope", "work", "task", "line item", "name", "activity"),
    # This period's quantity only: a "to date" or "% complete" column is a running total, not what to add.
    # "Units" is the unit of measure, never the quantity.
    "quantity": ("qty this period", "quantity this period", "this period", "qty installed", "quantity installed", "installed",
                 "installed this period", "completed this period", "work completed this period", "% this period",
                 "qty", "quantity", "quantities", "count"),
}
# A sheet with both "Qty" and "Installed" lists the line's scheduled quantity and this period's:
# the named one wins over a bare "qty", "quantity" or "count".
GENERAL_QUANTITY = frozenset(("qty", "quantity", "quantities", "count"))
MAX_RECORD_LINES = 20
MIN_WORDS_MATCH = 4
# Unit words that mean the same thing ("ea" and "each"); a plural "s" never matters.
SAME_UNITS = (("ea", "each", "pc", "piece"), ("sf", "sqft", "sq ft"), ("lf", "ft", "feet", "foot", "lin ft"),
              ("sy", "sq yd", "sqyd"), ("cy", "cu yd", "cuyd"), ("hr", "hour"), ("wk", "week"), ("mo", "month"),
              ("yd", "yard"), ("gal", "gallon"), ("lb", "pound"))
QTY_RE = re.compile(r"^(-?(?:\d[\d,]*(?:\.\d+)?|\.\d+))\s*(%|[A-Za-z]{1,6}\.?)?$")


def _key(cell: str) -> str:
    return re.sub(r"[^a-z#/&% ]", "", cell.lower()).strip()


def _header(row: list[str]) -> dict[str, int] | None:
    found: dict[str, int] = {}
    general = False
    for i, cell in enumerate(row):
        key = _key(cell)
        for field, words in HEADERS.items():
            if key in words:
                if field not in found:
                    found[field] = i
                    general = key in GENERAL_QUANTITY
                elif field == "quantity" and general and key not in GENERAL_QUANTITY:
                    found[field] = i
                    general = False
                break
    if "quantity" in found and ({"item", "description"} & set(found)):
        return found
    return None


def norm_item(text: str) -> str:
    """"01." and "1" are the same item; "2.10" and "2.1" are not."""
    parts = re.sub(r"\s+", "", str(text or "")).lower().strip(".").split(".")
    return ".".join(p.lstrip("0") or "0" for p in parts if p != "")


def norm_desc(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def _rows(text: str) -> list[tuple[int, list[str]]]:
    """A CSV (as saved, or as qbo_csv.xlsx_to_csv writes an Excel sheet) or rows pasted from a
    spreadsheet (tabs), each row with the number of the line it starts on, bounded as
    sov._split_rows bounds a pasted schedule. A quoted cell may hold a line break ("Rough-in" on one
    line, "wiring" on the next) and stays one cell; a quote left open past MAX_RECORD_LINES lines is
    read as that one line, so it can't swallow the rest of the file. csv only: no pattern runs over
    a whole line, so a long one costs no more than reading it."""
    lines_in = [line[:2000].replace("\x00", "") for line in text.splitlines(keepends=True)[:2000]]
    rows: list[tuple[int, list[str]]] = []
    at = 0
    while at < len(lines_in):
        line = lines_in[at]
        delimiter = "\t" if "\t" in line else ","
        # A blank line after the window: a record still open when it is reached never closed.
        window = lines_in[at:at + MAX_RECORD_LINES] + ["\n"]
        reader = csv.reader(window, delimiter=delimiter)
        row = next(reader, [])
        used = reader.line_num
        if used >= len(window):
            row, used = next(csv.reader([line.rstrip("\r\n")], delimiter=delimiter), []), 1
        cells = [" ".join(c.split()) if "\n" in c or "\r" in c else c.strip() for c in row[:sov.MAX_CELLS]]
        rows.append((at + 1, [c[:200] for c in cells]))
        at += max(used, 1)
    return [(n, r) for n, r in rows if any(r)]


def _one_cell(cell: str) -> list[str]:
    """"Rough-in wiring 30" or "1 Rough-in wiring 30 ea" typed on one line, as its cells. Read word by
    word, so no pattern backtracks over a long line."""
    words = cell.split()
    for take in (1, 2):
        if len(words) > take and QTY_RE.match("".join(words[-take:])) and re.search(r"\d", words[-take]):
            if take == 2 and not re.fullmatch(r"[\d,.]+", words[-2]):
                continue
            rest, qty = words[:-take], " ".join(words[-take:])
            item = rest[0] if len(rest) > 1 and sov.ITEM_RE.match(rest[0]) else ""
            desc = " ".join(rest[1:] if item else rest)
            if re.search(r"[A-Za-z]", desc):
                return [x for x in (item, desc, qty) if x]
    return [cell]


def parse(text: str) -> dict[str, Any]:
    """The sheet's rows as {n, item, description, qty} (qty the cell as written), with warnings for
    what was skipped. A header (in the first 5 rows) names the columns; without one, a leading
    item number, the first words and the last number on the row are taken."""
    rows = _rows((text or "")[:MAX_TEXT])
    header, at = None, 0
    for i, (_, row) in enumerate(rows[:5]):
        header = _header(row)
        if header:
            at = i
            break
    warnings: list[str] = []
    out: list[dict[str, Any]] = []
    body = rows[at + 1:] if header else rows
    for n, row in body:
        if header:
            cell = lambda f: row[header[f]].strip() if f in header and header[f] < len(row) else ""
            item, description, qty = cell("item"), cell("description"), cell("quantity")
        else:
            cells = [c for c in row if c]
            if len(cells) == 1:
                cells = _one_cell(cells[0])
            numeric = [i for i, c in enumerate(cells) if QTY_RE.match(c.replace(" ", ""))]
            last = numeric[-1] if numeric else -1
            item = cells[0] if len(cells) > 1 and last > 0 and sov.ITEM_RE.match(cells[0]) else ""
            qty = cells[last] if last >= 0 and not (item and last == 0) else ""
            texts = [c for i, c in enumerate(cells) if i not in numeric and not (item and i == 0)]
            description = texts[0] if texts else ""
        label = (description or item)[:80]
        if not item and not description:
            if any(row):
                warnings.append(f"Row {n} skipped: no item number or description.")
            continue
        if sov.TOTAL_RE.match(description or item):
            warnings.append(f"Row {n} ({label}) looks like a total and was skipped.")
            continue
        m = QTY_RE.match(qty.replace(" ", ""))
        if not qty.strip() or qty.strip() == "-" or (m and not re.search(r"[1-9]", m.group(1))):
            warnings.append(f"Row {n} ({label}) has no quantity; skipped.")
            continue
        out.append({"n": n, "item": item[:20], "description": description[:200], "qty": qty[:40]})
        if len(out) >= MAX_ROWS:
            warnings.append(f"Stopped at {MAX_ROWS} rows.")
            break
    return {"rows": out, "warnings": warnings}


def _prepare(lines: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str, str]]:
    """Each schedule line with its item and description normalized, once per file."""
    return [(line, norm_item(line.get("item", "")), norm_desc(line["description"])) for line in lines]


def _by_description(prepared: list[tuple[dict[str, Any], str, str]], want: str) -> list[dict[str, Any]]:
    hits = [line for line, _, desc in prepared if desc == want]
    if not hits and len(want) >= MIN_WORDS_MATCH:
        # Tolerant, by whole words: "Rough-in" for "Rough-in wiring", or "Panel and feeders, main" for
        # "Panel and feeders"; never "Doorstops" for "Doors". The shorter side is at least MIN_WORDS_MATCH long.
        padded = f" {want} "
        hits = [line for line, _, desc in prepared
                if len(desc) >= MIN_WORDS_MATCH and (padded in f" {desc} " or f" {desc} " in padded)]
    return hits


def _match(lines: list[dict[str, Any]], item: str, description: str,
           prepared: list[tuple[dict[str, Any], str, str]] | None = None) -> tuple[dict[str, Any] | None, str]:
    """The schedule line a row names, or None and why. With both an item number and a description,
    they must not name two different lines."""
    prepared = _prepare(lines) if prepared is None else prepared
    want = norm_desc(description)
    by_desc = _by_description(prepared, want) if want else []
    if item:
        key = norm_item(item)
        hits = [line for line, it, _ in prepared if it == key]
        if len(hits) == 1:
            if len(by_desc) == 1 and by_desc[0] is not hits[0]:
                other = by_desc[0]
                return None, (f"Item {item} is {hits[0]['description']}, but the description fits "
                              + (f"item {other['item']}, {other['description']}" if other.get("item") else other["description"])
                              + ". Correct the row and read the file again.")
            return hits[0], ""
    if len(by_desc) == 1:
        return by_desc[0], ""
    if len(by_desc) > 1:
        names = ", ".join(_name(line) for line in by_desc[:4])
        return None, f"It fits more than one line ({names}). Put the item number on the row."
    named = " ".join(x for x in (f"item {item}" if item else "", f'"{description}"' if description else "") if x)
    return None, f"No line on this job's schedule of values matches {named}."


def _name(line: dict[str, Any]) -> str:
    return f"{line.get('item', '')}. {line['description']}".strip(". ")


def _lump(line: dict[str, Any]) -> bool:
    return str(line.get("unit", "")).upper() == "LS" and line.get("qty_milli") == 1000


def qty_text(line: dict[str, Any], milli: int) -> str:
    """"120 ea", or a lump sum as "35%"."""
    if _lump(line):
        return f"{sov.fmt_qty(milli * 100)}%"
    return f"{sov.fmt_qty(milli)} {line['unit']}".strip()


def _unit_key(unit: str) -> str:
    key = " ".join(str(unit or "").lower().replace(".", " ").split())
    key = key[:-1] if len(key) > 2 and key.endswith("s") else key
    return next((group[0] for group in SAME_UNITS if key in group), key)


def _amount(line: dict[str, Any], raw: str) -> tuple[int, str]:
    """The row's quantity in thousandths of the line's unit, or 0 and why not. A lump sum's is the %
    done this period (1000 thousandths is 100%), as on a daily record; any other line's is in its
    own unit, so a % or another unit word is refused."""
    m = QTY_RE.match(raw.replace(" ", ""))
    if not m:
        return 0, f"The quantity {raw[:30]!r} isn't a number."
    written = (m.group(2) or "").rstrip(".")
    unit = str(line.get("unit", "")).strip()
    if written == "%" and not _lump(line):
        return 0, f"{_name(line)} is measured in {unit or 'units'}, not %. Put the quantity installed this period on the row."
    if written and written != "%":
        if _lump(line) and _unit_key(written) != "ls":
            return 0, f"{_name(line)} is a lump sum. Put the % done this period on the row, like 25%."
        if not _lump(line) and unit and _unit_key(written) != _unit_key(unit):
            return 0, f"The quantity is in {written}, but {_name(line)} is measured in {unit}."
    try:
        milli = sov.to_milli(m.group(1), "The quantity")
    except sov.SovError as exc:
        return 0, str(exc)
    if milli <= 0:
        return 0, "The quantity can't be zero or less. Correct it on the job page."
    if _lump(line):
        if milli % 100:
            return 0, f"{_name(line)}: a % done can have at most one decimal, like 12.5."
        milli //= 100
    return milli, ""


def basis(job: dict[str, Any], text: str, rows: list[dict[str, Any]]) -> str:
    """What a preview was worked out from and what it showed: the file, the job's lines and pay apps
    as they were, and the line and amount each row was read as. Apply refuses another basis, so a
    preview tapped twice, or one the schedule moved on from (a daily record, another import, lines
    renumbered or renamed), never adds what the person didn't see."""
    lines = [(line["id"], line["done_milli"], line["qty_milli"], int(line.get("stored_cents") or 0)) for line in sov.lines(job)]
    pays = [(inv["id"], inv["status"], inv.get("amount_cents")) for inv in job.get("invoices", [])]
    shown = [(r["n"], r["line_id"], r["add_milli"]) for r in rows]
    raw = json.dumps([job["id"], text, lines, pays, job.get("work_status"), bool(job.get("closed")), shown], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _done_text(line: dict[str, Any], milli: int) -> str:
    """The line's quantity to date after adding `milli`, as record_sov_progress takes it."""
    return str(Decimal(line["done_milli"] + milli) / 1000)


def preview(state: dict[str, Any], job: dict[str, Any], parsed: dict[str, Any], text: str = "") -> dict[str, Any]:
    """What each row would add, and why a row can't be added. Changes nothing.
    {rows: [{n, item, description, qty, line_id, line, done, add, new_total, scheduled, add_milli, problem}],
     warnings, blocked (why nothing can be added to this job now, or ""), can_add (rows that can)}."""
    from domain.daily import quantities_blocked
    from domain import GateError, _check_sov_progress
    lines = sov.lines(job)
    prepared = _prepare(lines)
    seen: dict[str, int] = {}
    rows = []
    for raw in parsed["rows"]:
        out = {"n": raw["n"], "item": raw["item"], "description": raw["description"], "qty": raw["qty"],
               "line_id": "", "line": "", "done": "", "add": "", "new_total": "", "scheduled": "", "add_milli": 0, "problem": ""}
        line, why = _match(lines, raw["item"], raw["description"], prepared)
        if line:
            out.update(line_id=line["id"], line=_name(line), done=qty_text(line, line["done_milli"]), scheduled=qty_text(line, line["qty_milli"]))
            milli, why = _amount(line, raw["qty"])
            if not why and line["id"] in seen:
                why = f"{_name(line)} is also on row {seen[line['id']]}; only that row is added."
            if not why:
                seen[line["id"]] = raw["n"]
                total = line["done_milli"] + milli
                out.update(add=qty_text(line, milli), new_total=qty_text(line, total), add_milli=milli)
                try:
                    # The checks apply makes, line by line: the scheduled quantity, stored materials, what is billed.
                    _check_sov_progress(job, {line["id"]: _done_text(line, milli)}, {})
                except GateError as exc:
                    why = " ".join(exc.blockers) or str(exc)
        out["problem"] = why
        if why:
            out["add_milli"] = 0
        rows.append(out)
    blocked = quantities_blocked(job)
    return {"job_id": job["id"], "rows": rows, "warnings": parsed["warnings"], "blocked": blocked,
            "can_add": 0 if blocked else sum(1 for r in rows if not r["problem"]), "basis": basis(job, text, rows)}


def apply(state: dict[str, Any], job_id: str, text: str, actor: str, source: str = "", seen: str | None = None) -> dict[str, Any]:
    """Add the rows that can be added to the job's schedule lines and refresh the draft pay app,
    as a daily record's quantities are (record_sov_progress). The rows that can't are named with
    why, in the answer, the activity log and the job record's note; refused outright (GateError)
    when nothing can be added, and when `seen` (the preview's basis) isn't what the job and file
    give now. Returns {added: [...], not_added: [...], number}."""
    from domain import GateError, _job, add_job_record, audit, record_sov_progress
    job = _job(state, job_id)
    result = preview(state, job, parse(text), text)
    if seen is not None and seen != result["basis"]:
        raise GateError("The schedule of values changed since this file was read, or it was already added. Read the file again to see what it adds now.")
    if result["blocked"]:
        raise GateError(result["blocked"])
    good = [r for r in result["rows"] if not r["problem"]]
    bad = [r for r in result["rows"] if r["problem"]]
    if not good:
        raise GateError("Nothing was added: no row of the file can go on this job's schedule of values.",
                        [f"Row {r['n']}: {r['problem']}" for r in bad[:20]] or ["The file has no rows with a quantity."])
    by_id = {line["id"]: line for line in sov.lines(job)}
    done = {r["line_id"]: _done_text(by_id[r["line_id"]], r["add_milli"]) for r in good}
    record_sov_progress(state, job_id, done, actor=actor)
    name = (source or "a file").strip()[:120]
    added = [f"{r['line']}: +{r['add']} (now {r['new_total']})" for r in good]
    not_added = [f"Row {r['n']} ({(r['description'] or r['item'])[:60]}): {r['problem']}" for r in bad]
    note = "Added to the schedule of values:\n" + "\n".join(added)
    if not_added:
        note += "\n\nNot added:\n" + "\n".join(not_added)
    add_job_record(state, job_id, f"Quantities imported from {name}"[:200], "note", note, actor)
    audit(state, job_id, "quantities_imported",
          f"{actor} imported quantities from {name}: {'; '.join(added)}."
          + (f" Not added: {'; '.join(not_added)}" if not_added else ""), actor)
    draft = next((i for i in job["invoices"] if i["status"] in ("draft", "awaiting_approval")), None)
    number = draft["number"] if draft else ""
    return {"added": [{"n": r["n"], "line": r["line"], "add": r["add"], "new_total": r["new_total"]} for r in good],
            "not_added": [{"n": r["n"], "what": (r["description"] or r["item"])[:80], "problem": r["problem"]} for r in bad],
            "number": number}
