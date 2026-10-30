"""The Daily record: what happened on site, with crew, weather, materials and deliveries (the
tasks-daily stream: T11, T12 and the daily side of M1).

A daily record is state["daily_records"] = [{id, job_id, notes, at, actor, for_history, crew_count,
crew_names, weather, weather_note, materials, deliveries, sections, attachments, quantities}],
with its id on job["daily_records"], and one "daily" job record whose note reads it out, so
paperwork and History show it. Everything but the Diary (notes) is optional and checked here:
known section keys only, schedule lines and change orders of this job only, numbers 0 or more,
text capped. Quantities put in place today add to each line's quantity done (M1, capped at the
scheduled quantity) and refresh the period's draft pay app through record_sov_progress. When
they can't be added the record is still saved, with quantities_not_applied saying why.

Imported at the bottom of domain/__init__.py (from .daily import *). Helpers from domain
are imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

import secrets
from decimal import Decimal
from typing import Any

__all__ = [
    "WEATHER", "DAILY_SECTIONS", "INSPECTION_TYPES", "CREW_MAX", "add_daily_record", "clean_daily_details", "daily_record_note",
]

WEATHER = ("Clear", "Cloudy", "Rain", "Snow", "Wind", "Heat", "Cold")
# The ticked boxes under the Diary, each opening its own fields (T11).
DAILY_SECTIONS = {
    "photos": "Progress photos",
    "quantities": "Quantities",
    "co_work": "Change order work",
    "inspections": "Inspections or major discussions",
    "punch": "New punchlist items",
}
INSPECTION_TYPES = {"inspection": "Inspection", "discussion": "Discussion"}
CREW_MAX = 500
_LIST_MAX = 50          # rows in any one list (crew names, punch items, inspections...)
_ATTACH_MAX = 30
_NAME_MAX = 80
_SHORT_MAX = 200
_TEXT_MAX = 2000


def _text(value: Any, limit: int, field: str, label: str) -> str:
    from domain import _refuse
    if value is None:
        return ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        _refuse("", {field: f"{label} must be text."})
    text = str(value).strip()
    if len(text) > limit:
        _refuse("", {field: f"Keep {label.lower()} under {limit:,} characters."})
    return text


def _list(value: Any, field: str, label: str) -> list:
    from domain import _refuse
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        _refuse("", {field: f"{label} wasn't understood."})
    if len(value) > _LIST_MAX:
        _refuse("", {field: f"No more than {_LIST_MAX} {label.lower()} on one record."})
    return value


def _names(value: Any) -> list[str]:
    """Crew names: a list, or text with one name per line or comma."""
    from domain import _refuse
    if isinstance(value, str):
        value = [p for chunk in value.splitlines() for p in chunk.split(",")]
    names = [_text(n, _NAME_MAX, "crew_names", "Each name") for n in _list(value, "crew_names", "Crew names")]
    names = [n for n in names if n]
    if len(names) > _LIST_MAX:
        _refuse("", {"crew_names": f"No more than {_LIST_MAX} names on one record."})
    return names


def _amount(value: Any, field: str, label: str) -> int:
    """A number of 0 or more with up to three decimals, in thousandths ("2.5" -> 2500)."""
    import sov
    from domain import _refuse
    try:
        milli = sov.to_milli(value, label)
    except sov.SovError as exc:
        _refuse("", {field: str(exc)})
    if milli < 0:
        _refuse("", {field: f"{label} can't be negative."})
    return milli


def clean_daily_details(state: dict[str, Any], job: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """The daily record's fields beyond the Diary, checked: crew, weather, materials, deliveries,
    the ticked sections and the files already on the job record. Raises GateError under the field."""
    from domain import _refuse, parse_int
    import sov
    out: dict[str, Any] = {}
    if body.get("crew_count") not in (None, ""):
        count = parse_int(body.get("crew_count"), "the crew count", "crew_count")
        if count < 0 or count > CREW_MAX:
            _refuse("", {"crew_count": f"Enter a crew count from 0 to {CREW_MAX}."})
        out["crew_count"] = count
    out["crew_names"] = _names(body.get("crew_names"))
    weather = _text(body.get("weather"), 20, "weather", "Weather")
    if weather and weather not in WEATHER:
        _refuse("", {"weather": "Pick the weather from the list."})
    out["weather"] = weather
    out["weather_note"] = _text(body.get("weather_note"), _SHORT_MAX, "weather_note", "The weather note")
    out["materials"] = _text(body.get("materials"), _TEXT_MAX, "materials", "Materials")
    out["deliveries"] = _text(body.get("deliveries"), _TEXT_MAX, "deliveries", "Deliveries")

    raw = body.get("sections") or {}
    if not isinstance(raw, dict):
        _refuse("", {"sections": "The ticked sections weren't understood."})
    unknown = [k for k in raw if k not in DAILY_SECTIONS]
    if unknown:
        _refuse("", {"sections": f"{str(unknown[0])[:30]} isn't a section of the daily record."})
    sections: dict[str, Any] = {}
    if "photos" in raw:
        photos = raw["photos"] if isinstance(raw["photos"], dict) else {}
        sections["photos"] = {"note": _text(photos.get("note"), _SHORT_MAX, "photos_note", "The photo note")}
    if "quantities" in raw:
        if not sov.has_sov(job):
            _refuse("", {"quantities": "This job has no schedule of values to add quantities to."})
        by_id = {line["id"]: line for line in sov.lines(job)}
        rows, seen = [], set()
        for entry in _list(raw["quantities"], "quantities", "Quantities"):
            entry = entry if isinstance(entry, dict) else {}
            line = by_id.get(str(entry.get("line_id") or ""))
            if not line:
                _refuse("", {"quantities": "Pick a line from this job's schedule of values."})
            if line["id"] in seen:
                _refuse("", {"quantities": f"{line['description']} is listed twice."})
            seen.add(line["id"])
            lump = line["unit"].upper() == "LS" and line["qty_milli"] == 1000
            field = f"qty_{line['id']}"
            if entry.get("qty") in (None, ""):
                continue
            amount = _amount(entry.get("qty"), field, f"{line['description']}: {'% done today' if lump else 'quantity'}")
            if lump:
                # A lump sum is entered as % of the line done today: 1000 thousandths is 100%.
                if amount % 100:
                    _refuse("", {field: f"{line['description']}: a % done can have at most one decimal, like 12.5."})
                amount //= 100
            if amount:
                rows.append({"line_id": line["id"], "qty_milli": amount})
        if not rows:
            _refuse("", {"quantities": "Enter a quantity on at least one line, or untick Quantities."})
        sections["quantities"] = rows
    if "co_work" in raw:
        cos = {co["id"]: co for co in job.get("change_orders", []) if co.get("status") != "withdrawn"}
        rows = []
        for n, entry in enumerate(_list(raw["co_work"], "co_work", "Change order rows")):
            entry = entry if isinstance(entry, dict) else {}
            co_id = str(entry.get("co_id") or "")
            if co_id and co_id not in cos:
                _refuse("", {f"co_{n}": "Pick a change order on this job, or New work, not priced yet."})
            text = _text(entry.get("description"), 500, f"co_desc_{n}", "The description")
            hours = _amount(entry.get("hours"), f"co_hours_{n}", "Hours") if entry.get("hours") not in (None, "") else 0
            if not (co_id or text or hours):
                continue
            if not text:
                _refuse("", {f"co_desc_{n}": "Say what change order work was done."})
            rows.append({"co_id": co_id, "description": text, "hours_milli": hours})
        if not rows:
            _refuse("", {"co_work": "Describe the change order work, or untick Change order work."})
        sections["co_work"] = rows
    if "inspections" in raw:
        rows = []
        for n, entry in enumerate(_list(raw["inspections"], "inspections", "Inspections")):
            entry = entry if isinstance(entry, dict) else {}
            kind = str(entry.get("type") or "inspection")
            if kind not in INSPECTION_TYPES:
                _refuse("", {f"insp_type_{n}": "Pick Inspection or Discussion."})
            who = _text(entry.get("who"), 120, f"insp_who_{n}", "Who")
            outcome = _text(entry.get("outcome"), 500, f"insp_outcome_{n}", "The outcome")
            if who or outcome:
                rows.append({"type": kind, "who": who, "outcome": outcome})
        if not rows:
            _refuse("", {"inspections": "Say who it was with and what came of it, or untick Inspections."})
        sections["inspections"] = rows
    if "punch" in raw:
        value = raw["punch"]
        if isinstance(value, str):
            value = value.splitlines()
        items = [t for t in (_text(t, _SHORT_MAX, "punch", "Each punchlist item") for t in _list(value, "punch", "Punchlist items")) if t]
        if not items:
            _refuse("", {"punch": "Enter at least one punchlist item, or untick New punchlist items."})
        sections["punch"] = items
    out["sections"] = sections

    records = {r["id"]: r for r in job.get("records", [])}
    attachments: list[str] = []
    for rid in _list(body.get("attachments"), "attachments", "Files"):
        rid = str(rid or "")
        if rid not in records or not records[rid].get("file_ids"):
            _refuse("", {"attachments": "A file isn't on this job's record. Add it again."})
        if rid not in attachments:
            attachments.append(rid)
    if len(attachments) > _ATTACH_MAX:
        _refuse("", {"attachments": f"No more than {_ATTACH_MAX} files on one record."})
    out["attachments"] = attachments
    return out


def _qty_words(job: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """"120 LF Rough-in wiring, 25% Panel and feeders"."""
    import sov
    by_id = {line["id"]: line for line in sov.lines(job)}
    parts = []
    for row in rows:
        line = by_id.get(row["line_id"])
        if not line:
            continue
        if line["unit"].upper() == "LS" and line["qty_milli"] == 1000:
            parts.append(f"{sov.fmt_qty(row['qty_milli'] * 100)}% {line['description']}")
        else:
            parts.append(f"{sov.fmt_qty(row['qty_milli'])} {line['unit']} {line['description']}")
    return ", ".join(parts)


def daily_record_note(job: dict[str, Any], rec: dict[str, Any]) -> str:
    """The job record's note for a daily record: the Diary, then one line for each thing filled in."""
    import sov
    lines = [rec.get("notes", "")]
    if rec.get("crew_count") is not None or rec.get("crew_names"):
        count = rec.get("crew_count")
        names = ", ".join(rec.get("crew_names") or [])
        lines.append("Crew: " + (f"{count}" if count is not None else "") + (f" ({names})" if names and count is not None else names))
    if rec.get("weather") or rec.get("weather_note"):
        lines.append("Weather: " + ", ".join(x for x in (rec.get("weather"), rec.get("weather_note")) if x))
    if rec.get("materials"):
        lines.append(f"Materials: {rec['materials']}")
    if rec.get("deliveries"):
        lines.append(f"Deliveries: {rec['deliveries']}")
    sections = rec.get("sections") or {}
    if "photos" in sections:
        lines.append("Progress photos" + (f": {sections['photos']['note']}" if sections["photos"].get("note") else ""))
    if sections.get("quantities"):
        lines.append(f"Quantities: {_qty_words(job, sections['quantities'])}")
        if rec.get("quantities_not_applied"):
            lines.append(f"Not added to the schedule of values: {rec['quantities_not_applied']}")
    cos = {co["id"]: co for co in job.get("change_orders", [])}
    for row in sections.get("co_work", []):
        co = cos.get(row["co_id"])
        hours = f", {sov.fmt_qty(row['hours_milli'])} h" if row.get("hours_milli") else ""
        lines.append(f"Change order work ({'CO ' + str(co['number']) if co else 'new, not priced yet'}): {row['description']}{hours}")
    for row in sections.get("inspections", []):
        lines.append(f"{INSPECTION_TYPES[row['type']]}" + (f" with {row['who']}" if row.get("who") else "") + (f": {row['outcome']}" if row.get("outcome") else ""))
    if sections.get("punch"):
        lines.append("New punchlist items: " + "; ".join(sections["punch"]))
    if rec.get("attachments"):
        n = len(rec["attachments"])
        lines.append(f"{n} file{'' if n == 1 else 's'} attached")
    return "\n".join(x for x in lines if x)


def add_daily_record(state: dict[str, Any], job_id: str, notes: str, actor: str = "owner", at: str | None = None,
                     details: dict[str, Any] | None = None) -> dict[str, Any]:
    """A dated note on the job: the Diary, and optionally crew, weather, materials, deliveries,
    ticked sections and files already on the job record (details, the request's body). `at` lets
    an import keep the day the note was written; otherwise the day is the page's own date
    (details["today"], through local_today; review C3). Quantities put in place today add to the
    schedule lines and refresh the draft pay app (M1) before the record is saved; when they
    can't, the record is saved anyway with quantities_not_applied saying why (_apply_quantities)."""
    from domain import _job, add_job_record, audit, utcnow, local_today
    job = _job(state, job_id)
    extra = clean_daily_details(state, job, details or {}) if details is not None else {}
    when = at or utcnow()
    day = when[:10] if at else local_today((details or {}).get("today"))
    quantities = (extra.get("sections") or {}).get("quantities") or []
    not_applied = _apply_quantities(state, job, quantities, actor, day) if quantities else ""
    rec = {
        "id": "dr-" + secrets.token_hex(4),
        "job_id": job_id,
        "notes": notes,
        "at": when,
        "actor": actor,
        "for_history": bool(job.get("closed")),
        **extra,
    }
    if not_applied:
        rec["quantities_not_applied"] = not_applied
    state.setdefault("daily_records", []).append(rec)
    job.setdefault("daily_records", []).append(rec["id"])
    label = f"Daily record {day}"
    if quantities:
        label += f": {_qty_words(job, quantities)}"[:160]
    add_job_record(state, job_id, label[:200], "daily", daily_record_note(job, rec), actor)
    for record in job.get("records", []):
        if record["id"] in rec.get("attachments", []):
            record["daily_record_id"] = rec["id"]
    audit(state, job_id, "daily_record", f"{actor} added a daily record to the job record.", actor)
    if not_applied:
        audit(state, job_id, "daily_quantities_not_applied",
              f"The quantities on the daily record of {day} were kept on it and not added to the schedule of values: {not_applied}", "duracash")
    return job


def _apply_quantities(state: dict[str, Any], job: dict[str, Any], rows: list[dict[str, Any]], actor: str, day: str) -> str:
    """Add the quantities put in place today to the schedule lines and refresh the draft pay app
    (M1). A daily record is never lost because they can't be added (B5 of the Oct 30 review): it
    is saved anyway with them on it, and this returns why they weren't added, in plain words, for
    the record, its note and the page. Blank when they were added."""
    from domain import DRAFT_STATUSES, GateError, over_schedule_text, record_sov_progress
    import sov
    keep = " They are kept on this daily record."
    if job.get("closed"):
        return "The job is finished, so the schedule of values doesn't change." + keep
    if job.get("work_status") == "complete":
        return "The job is marked done, so every line is already counted as done." + keep
    draft = next((inv for inv in job["invoices"] if inv["status"] in DRAFT_STATUSES), None)
    if draft and draft.get("pencil_status") == "sent":
        return (f"{draft['number']} is with the client for their review. Record their answer or delete the draft, "
                "then enter these quantities on the job page." + keep)
    by_id = {line["id"]: line for line in sov.lines(job)}
    done, errors = {}, []
    for row in rows:
        line = by_id[row["line_id"]]
        total = line["done_milli"] + row["qty_milli"]
        if total > line["qty_milli"]:
            errors.append(over_schedule_text(line, total))
        done[line["id"]] = str(Decimal(total) / 1000)
    if errors:
        return " ".join(errors) + keep
    try:
        record_sov_progress(state, job["id"], done, actor=actor, from_daily=day)
    except GateError as exc:
        return " ".join(dict.fromkeys(exc.blockers or [str(exc)])) + keep
    return ""
