"""The New job record form: what happened on site, with crew, weather, materials and deliveries
(the tasks-daily stream: T11, T12 and the daily side of M1), and the day's documents, each a job
record of its own kind (save_job_record, the Nov 3 overhaul). Stored names keep the word "daily".

A daily record is state["daily_records"] = [{id, job_id, notes, at, actor, for_history, crew_count,
crew_names, weather, weather_note, materials, deliveries, sections, attachments, quantities}],
with its id on job["daily_records"], and one "daily" job record whose note reads it out, so
paperwork and History show it. Everything but the Diary (notes) is optional and checked here:
known section keys only, schedule lines and change orders of this job only, numbers 0 or more,
text capped. Quantities put in place today add to each line's field total, up to the scheduled
quantity, and move the suggested pay app value (Nov 3). A record never creates or changes a draft:
only Progress Pay App does. What can't be added stays on the record, with quantities_not_applied
saying why.

Imported at the bottom of domain/__init__.py (from .daily import *). Helpers from domain
are imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

__all__ = [
    "WEATHER", "DAILY_SECTIONS", "INSPECTION_TYPES", "CREW_MAX", "RECORD_SECTIONS", "add_daily_record", "clean_daily_details",
    "clean_record_entries", "daily_record_note", "daily_record_for", "hold_for_job_record", "save_job_record", "update_job_record",
]

_HOLD_HOURS = 24   # how long an uploaded file waits for its New job record
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
# The form's document sections (static/js/views/daily.js RECORD_SECTIONS): the record kind each
# makes and the word its label starts with. Delivery tickets and time sheets share the kind
# "ticket", which the paperwork rules read (_record_kinds_for, _add_ticket_item); ticket_type
# tells them apart on the page. "other" takes its kind from the row.
RECORD_SECTIONS = {
    "delivery": {"kind": "ticket", "ticket_type": "delivery", "word": "Delivery ticket"},
    "photos": {"kind": "photo", "word": "Progress photo"},
    "time_sheets": {"kind": "ticket", "ticket_type": "time_sheet", "word": "Time sheet"},
    "invoices": {"kind": "supplier_invoice", "word": "Invoice"},
    "inspections": {"kind": "inspection", "word": "Inspection"},
    # Conversation / note with client (Stephen, Nov 26): what Log a conversation did, now on New job
    # record, so everything that happens on a job goes through the one form.
    "conversation": {"kind": "conversation", "word": ""},
    "other": {"kind": "", "word": ""},
}
CREW_MAX = 500
# A time sheet's rows (Nov 26): crew member and hours, a team member or anyone typed. A row's hours
# are a day's or a week's on a sheet that covers one.
CREW_HOURS_MAX = 168
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


def clean_daily_details(state: dict[str, Any], job: dict[str, Any], body: dict[str, Any],
                        credit: dict[str, int] | None = None, gate_over: bool = False,
                        own: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The daily record's fields beyond the Diary, checked: crew, weather, materials, deliveries,
    the ticked sections and the files already on the job record. Raises GateError under the field.
    A quantity more than what is left on its line is a soft gate (Nov 26: 2 sinks on a 1-each line
    went through): refused with over_schedule saying which, unless body["over_ok"] says to record
    it anyway; gate_over is the form's save (an import or a script records what it is given).
    credit: what this record already added to each line, on Edit (it is left again). own: the
    record's own quantity rows by line, on Edit: a lump sum's % done to date typed as it was saved
    is that row again, not a new step up (Nov 27 hunt: saving an Edit wiped the % it had recorded)."""
    from domain import GateError, _refuse, parse_int
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
        _refuse("", {"sections": f"{str(unknown[0])[:30]} isn't a section of this form."})
    sections: dict[str, Any] = {}
    if "photos" in raw:
        photos = raw["photos"] if isinstance(raw["photos"], dict) else {}
        sections["photos"] = {"note": _text(photos.get("note"), _SHORT_MAX, "photos_note", "The photo note")}
    if "quantities" in raw:
        if not sov.has_sov(job):
            _refuse("", {"quantities": "This job has no schedule of values to add quantities to."})
        by_id = {line["id"]: line for line in sov.lines(job)}
        rows, seen, unchanged, over = [], set(), False, []
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
            to_date = lump and entry.get("to_date") is True
            amount = _amount(entry.get("qty"), field, f"{line['description']}: {'% done to date' if to_date else '% done today' if lump else 'quantity'}")
            if lump:
                # A lump sum is entered as % of the line: 1000 thousandths is 100%.
                if amount % 100:
                    _refuse("", {field: f"{line['description']}: a % done can have at most one decimal, like 12.5."})
                amount //= 100
            if to_date:
                # New job record asks a lump sum's % done to date, the figure a foreman knows (Round 1):
                # what is recorded is the step up from the field total. Lower is a correction, not a record.
                prev = (own or {}).get(line["id"])
                if prev is not None and prev.get("to_date_milli") == amount:
                    rows.append(dict(prev))   # Edit: the % this record set, unchanged
                    continue
                current = _field_floor(job, line)
                if prev is not None and prev.get("to_date_milli") == current:
                    # Edit of the record that set the line where it is now: its own step up comes off
                    # first, so it can be changed either way. A later record's % stays the floor.
                    current = max(0, current - int((credit or {}).get(line["id"], 0)))
                if amount < current:
                    _refuse("", {field: f"{line['description']} is at {sov.fmt_qty(current * 100)}% done already. Enter that or more, "
                                        "or lower it with Correct field total on the job's Schedule of values card."})
                if amount > current or ("field_milli" not in line and sov.field_total(line) != amount):
                    # Nothing recorded in the field yet and a draft's figure on the line: the field
                    # total becomes what is typed, even with no step up (Round 1 hunt).
                    rows.append({"line_id": line["id"], "qty_milli": amount - current, "to_date_milli": amount})
                else:
                    unchanged = True
                continue
            if amount:
                left = max(0, line["qty_milli"] - sov.field_total(line) + int((credit or {}).get(line["id"], 0)))
                if amount > left:
                    over.append({"line_id": line["id"], "field": field, "description": line["description"], "unit": line["unit"],
                                 "lump_sum": lump, "typed_milli": amount, "left_milli": left})
                rows.append({"line_id": line["id"], "qty_milli": amount})
        if over and gate_over and body.get("over_ok") is not True:
            def words(o: dict[str, Any], milli: int) -> str:
                return f"{sov.fmt_qty(milli * 100)}%" if o["lump_sum"] else f"{sov.fmt_qty(milli)} {o['unit']}"
            said = {o["field"]: f"{words(o, o['typed_milli'])} is more than the {words(o, o['left_milli'])} left on {o['description']}." for o in over}
            raise GateError("More than the schedule of values has left. Check the quantities, or record them anyway.",
                            list(said.values()), fields=said, extra={"over_schedule": over})
        if rows:
            sections["quantities"] = rows
        elif not unchanged:   # a lump sum typed at its own total adds nothing, and isn't a mistake
            _refuse("", {"quantities": "Enter a quantity on at least one line, or untick Quantities."})
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
        if line["unit"].upper() == "LS" and line["qty_milli"] == 1000 and "to_date_milli" in row:
            parts.append(f"{line['description']} {sov.fmt_qty(row['to_date_milli'] * 100)}% done to date (+{sov.fmt_qty(row['qty_milli'] * 100)}%)")
        elif line["unit"].upper() == "LS" and line["qty_milli"] == 1000:
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
                     details: dict[str, Any] | None = None, gate_over: bool = False) -> dict[str, Any]:
    """A dated note on the job: the Diary, and optionally crew, weather, materials, deliveries,
    ticked sections and files already on the job record (details, the request's body). `at` lets
    an import keep the day the note was written; otherwise the day is the page's own date
    (details["today"], through local_today; review C3). Quantities put in place today add to the
    lines' field totals before the record is saved; when they can't, the record is saved anyway
    with quantities_not_applied saying why (_apply_quantities)."""
    from domain import _job, add_job_record, audit, utcnow, local_today
    job = _job(state, job_id)
    extra = clean_daily_details(state, job, details or {}, gate_over=gate_over) if details is not None else {}
    when = at or utcnow()
    day = when[:10] if at else local_today((details or {}).get("today"))
    quantities = (extra.get("sections") or {}).get("quantities") or []
    added, not_applied = _apply_quantities(job, quantities, day) if quantities else ([], "")
    rec = {
        "id": "dr-" + secrets.token_hex(4),
        "job_id": job_id,
        "notes": notes,
        "at": when,
        "actor": actor,
        "for_history": bool(job.get("closed")),
        **extra,
    }
    if quantities:
        rec["quantities_added"] = added
    if not_applied:
        rec["quantities_not_applied"] = not_applied
    state.setdefault("daily_records", []).append(rec)
    job.setdefault("daily_records", []).append(rec["id"])
    label = f"Diary entry {day}"
    if quantities:
        label += f": {_qty_words(job, quantities)}"[:160]
    add_job_record(state, job_id, label[:200], "daily", daily_record_note(job, rec), actor)
    job["records"][-1]["daily_record_id"] = rec["id"]   # Edit opens this record's form again (Nov 26)
    for record in job.get("records", []):
        if record["id"] in rec.get("attachments", []):
            record["daily_record_id"] = rec["id"]
    audit(state, job_id, "daily_record", f"{actor} added a diary entry.", actor)
    if not_applied:
        audit(state, job_id, "daily_quantities_not_applied",
              f"The quantities on the diary entry of {day} were kept on it and not added to the schedule of values: {not_applied}", "duracash")
    return job


def _joined(name: str, number: str) -> str:
    """"ABC Supply #1042"."""
    return " ".join(x for x in (name, f"#{number}" if number else "") if x)


def hold_for_job_record(state: dict[str, Any], job_id: str, record_ids: list[str], actor: str) -> None:
    """These files came up for a New job record that isn't saved yet (?for_job_record=1): that save,
    by this person, may put its fields on them. Nothing else on the job can be rewritten that way."""
    from domain import _job, utcnow
    wanted = set(record_ids)
    for rec in _job(state, job_id).get("records", []):
        if rec["id"] in wanted:
            rec["awaiting_record"] = {"by": actor, "at": utcnow()}


def _may_file(job: dict[str, Any], rec: dict[str, Any], actor: str) -> bool:
    """A file this person uploaded for this form in the last day, that nobody has filed since: not
    yet saved by a New job record, and not chosen for a pay app's supporting documents (one only
    suggested there is still free)."""
    from domain import utcnow
    held = rec.get("awaiting_record")
    if not isinstance(held, dict) or held.get("by") != actor or not rec.get("file_ids"):
        return False
    if str(held.get("at") or "") < (datetime.fromisoformat(utcnow()) - timedelta(hours=_HOLD_HOURS)).isoformat(timespec="seconds"):
        return False
    packets = [job.get("packet"), job.get("closeout_packet")] + [inv.get("packet") for inv in job.get("invoices", [])]
    return not any(a.get("record_id") == rec["id"] and not a.get("auto")
                   for packet in packets for item in packet or [] for a in item.get("attachments") or [])


def clean_record_entries(job: dict[str, Any], raw: Any, actor: str = "", report: dict[str, list] | None = None) -> list[dict[str, Any]]:
    """The form's document sections, checked: one entry per filled row, each with what its records
    will hold. Errors land under the row's own box (invoices_amount_0). Changes nothing. With report,
    a file the form can't file doesn't stop the save: this person's own already filed goes in
    report["already_saved"], any other in report["not_added"]; without it, it is refused."""
    from domain import RECORD_KINDS, _refuse
    import sov
    records = {r["id"]: r for r in job.get("records", [])}
    out = []
    for entry in _list(raw, "entries", "Sections"):
        entry = entry if isinstance(entry, dict) else {}
        section = str(entry.get("section") or "")
        if section not in RECORD_SECTIONS:
            _refuse("", {"entries": "A section of the form wasn't understood."})
        row = entry.get("row") if isinstance(entry.get("row"), int) and not isinstance(entry.get("row"), bool) else 0
        name = lambda box: f"{section}_{box}_{row}"
        text = lambda box, label, limit=_SHORT_MAX: _text(entry.get(box), limit, name(box), label)
        attachments: list[str] = []
        for rid in _list(entry.get("attachments"), "attachments", "Files"):
            rid = str(rid or "")
            rec = records.get(rid)
            if rec and _may_file(job, rec, actor):
                if rid not in attachments:
                    attachments.append(rid)
            elif report is not None and rec and (rec.get("filed_record") or {}).get("by") == actor:
                report["already_saved"].append(rid)   # this person's own, from a save whose answer was lost
            elif report is not None:
                report["not_added"].append({"record_id": rid, "label": rec["label"] if rec else ""})
            else:
                _refuse("", {"attachments": "A file isn't waiting to be saved on this job. Add it again."})
        spec = RECORD_SECTIONS[section]
        clean = {"kind": spec["kind"], "ticket_type": spec.get("ticket_type", ""), "word": spec["word"],
                 "title": "", "note": "", "extra": {}, "attachments": attachments, "section": section,
                 # On Edit (update_job_record): the record this row was saved as.
                 "record_id": str(entry.get("record_id") or "")[:40]}
        if section == "delivery":
            clean["title"] = _joined(text("supplier", "The supplier"), text("number", "The ticket number"))
            clean["note"] = text("what", "What arrived", _TEXT_MAX)
        elif section == "photos":
            clean["title"] = text("note", "What they show")
        elif section == "time_sheets" and entry.get("crew") not in (None, "", []):
            # Rows of crew member and hours (Nov 26): a team member picked, or anyone typed.
            crew = []
            for n, person in enumerate(_list(entry.get("crew"), name("crew"), "Crew rows")):
                person = person if isinstance(person, dict) else {}
                who = " ".join(_text(person.get("name"), _NAME_MAX, f"time_sheets_crew_name_{n}", "The name").split())
                raw_hours = person.get("hours")
                if not who and raw_hours in (None, ""):
                    continue
                if not who:
                    _refuse("", {f"time_sheets_crew_name_{n}": "Pick who worked, or type their name."})
                if raw_hours in (None, ""):
                    _refuse("", {f"time_sheets_crew_hours_{n}": f"Enter the hours {who} worked, like 8."})
                hours = _amount(raw_hours, f"time_sheets_crew_hours_{n}", "Hours")
                if hours <= 0 or hours > CREW_HOURS_MAX * 1000:
                    _refuse("", {f"time_sheets_crew_hours_{n}": f"Enter hours from 0.25 to {CREW_HOURS_MAX}."})
                member = _text(person.get("member_id"), 60, f"time_sheets_crew_name_{n}", "The team member")
                crew.append({"name": who, "hours_milli": hours, **({"member_id": member} if member else {})})
            if crew:
                total = sum(p["hours_milli"] for p in crew)
                clean["title"] = f"{len(crew)} crew, {sov.fmt_qty(total)} h"
                clean["note"] = "; ".join(f"{p['name']} {sov.fmt_qty(p['hours_milli'])} h" for p in crew)
                clean["extra"].update(crew=crew, hours_milli=total)
                clean["crew"] = crew
        elif section == "time_sheets":
            clean["title"] = text("who", "Who")
            if entry.get("hours") not in (None, ""):
                hours = _amount(entry.get("hours"), name("hours"), "Hours")
                clean["extra"]["hours_milli"] = hours
                clean["note"] = f"Hours: {sov.fmt_qty(hours)}"
        elif section == "conversation":
            said = " ".join(_text(entry.get("text"), 500, name("text"), "What was said").split())
            if not said and not attachments:
                continue
            if not said:
                _refuse("", {name("text"): "Enter what was said, like \"Check goes out Friday\"."})
            # On Edit, a conversation saved before keeps its date as it was, even one gone by.
            day = _next_on(job, entry.get("next_on"), name("next_on"), saved=bool(entry.get("record_id")))
            clean["note"] = said + (f" Check again {day.isoformat()}." if day else "")
            clean["conversation"] = {"text": said, "day": day}
        elif section == "invoices":
            clean["title"] = _joined(text("supplier", "The supplier"), text("number", "The invoice number"))
            if entry.get("amount") not in (None, ""):
                try:
                    cents = sov.to_cents(entry.get("amount"), "The amount")
                except sov.SovError as exc:
                    _refuse("", {name("amount"): str(exc)})
                if cents < 0:
                    _refuse("", {name("amount"): "The amount can't be negative."})
                clean["extra"]["amount_cents"] = cents
                clean["note"] = f"Amount: ${cents / 100:,.2f}"
        elif section == "inspections":
            clean["title"] = text("who", "Who inspected")
            clean["note"] = text("outcome", "The result", 500)
        else:
            clean["kind"] = str(entry.get("kind") or "note")
            if clean["kind"] not in RECORD_KINDS or clean["kind"] == "daily":
                _refuse("", {name("kind"): "Pick what kind of document this is."})
            clean["title"] = text("label", "What it is", 120)
            clean["note"] = text("note", "The note", _TEXT_MAX)
            if not clean["title"] and not attachments:
                if not clean["note"]:
                    continue
                _refuse("", {name("label"): "Enter what this is, like \"Rough-in inspection passed\"."})
        if clean["title"] or clean["note"] or attachments:
            out.append(clean)
    return out


def _next_on(job: dict[str, Any], value: Any, field: str, saved: bool = False) -> "date | None":
    """A conversation's Check again on (as Log a conversation took it): today or later, and only
    while a pay app waits for payment, since that is the follow-up it holds. saved: a row saved
    before, on Edit, whose date is only read back."""
    from domain import OPEN_FOR_PAYMENT, _day, _refuse
    day = _day(value, field, "Enter the date to check again, like 2026-11-07.")
    if saved:
        return day
    if day and day < datetime.now(timezone.utc).date() - timedelta(days=1):
        _refuse("", {field: "Enter today or a later date to check again."})
    if day and not any(inv["status"] in OPEN_FOR_PAYMENT for inv in job["invoices"]):
        _refuse("", {field: "No sent pay app is waiting for payment, so there is no follow-up to hold."})
    return day


def _record_day(body: dict[str, Any]) -> str:
    """The day the form says, YYYY-MM-DD: today where the person is, or a day gone by."""
    from domain import START_BACK_DAYS, _refuse, local_today
    today = local_today(body.get("today"))
    raw = _text(body.get("date"), 10, "date", "The date")
    if not raw:
        return today
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        _refuse("", {"date": "Enter the date as a day, month and year."})
    if raw > today:
        _refuse("", {"date": "The date can't be in the future."})
    if day < date.fromisoformat(today) - timedelta(days=START_BACK_DAYS):
        _refuse("", {"date": "That date is too long ago."})
    return raw


def _apply_entry(state: dict[str, Any], job: dict[str, Any], entry: dict[str, Any], day: str, actor: str, daily_id: str) -> None:
    """Make one section's records: its fields on each file it uploaded, one record per file, or
    one record of what was typed when no file came with it."""
    from domain import _log_conversation, _payer, add_job_record, audit, utcnow
    head = lambda rest: f"{entry['word']}: {rest}" if entry["word"] else rest
    files = [next(r for r in job["records"] if r["id"] == rid) for rid in entry["attachments"]]
    if entry.get("conversation"):
        # As Log a conversation did (R4-06): a record of kind conversation that counts as a follow-up
        # while a pay app waits, and with Check again on holds the next one until then.
        payer = _payer(state, job["payer_id"])
        now = utcnow()
        job.setdefault("records", []).append({"id": "rec-" + secrets.token_hex(3), "kind": "conversation",
                                              "label": (entry["title"] or f"Conversation with {payer.get('approver') or payer['name']}")[:200],
                                              "note": entry["note"], "added_at": now, "added_by": actor})
        _log_conversation(state, job, entry["conversation"]["text"], entry["conversation"]["day"], actor, now)
        files = [job["records"][-1]]
    elif files:
        for n, rec in enumerate(files, 1):
            if entry["title"]:
                many = f" ({n} of {len(files)})" if len(files) > 1 else ""
                rec["label"] = head(entry["title"])[:190] + many
            else:
                rec["label"] = head(rec["label"])[:200]
            if rec.get("kind") == "waiver" and entry["kind"] != "waiver":
                rec["was_waiver"] = True   # still a waiver going out for the waiver question (edit_record)
            rec["kind"] = entry["kind"]
            if entry["note"]:
                rec["note"] = entry["note"]
            rec.pop("awaiting_record", None)
            rec["filed_record"] = {"by": actor}
            audit(state, job["id"], "record_filed", f"{actor} filed {rec['label']} as a job record.", actor)
    else:
        add_job_record(state, job["id"], head(entry["title"] or entry["note"][:_NAME_MAX])[:200], entry["kind"], entry["note"], actor)
        files = [job["records"][-1]]
    for rec in files:
        rec["on_date"] = day
        rec.update(entry["extra"])
        if entry["ticket_type"]:
            rec["ticket_type"] = entry["ticket_type"]
        if daily_id:
            rec["daily_record_id"] = daily_id


def _crew_from_sheet(details: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    """The crew count and names come from the time sheet's rows when it has them (Nov 26): the
    count box is gone from the form. A count typed by a script or an older page stays as given."""
    sheet = next((e["crew"] for e in entries if e.get("crew")), None)
    if sheet and details.get("crew_count") in (None, "") and not details.get("crew_names"):
        names = list(dict.fromkeys(p["name"] for p in sheet))
        details["crew_count"], details["crew_names"] = len(names), names


def save_job_record(state: dict[str, Any], job_id: str, notes: str, actor: str, body: dict[str, Any],
                    report: dict[str, list] | None = None, gate_over: bool = False) -> dict[str, Any]:
    """What the New job record form saves: the Diary and the day's details as a daily record, when
    there are any, and one job record per file or filled section. A document alone needs no Diary;
    the day's details do. Nothing is written if any of it is refused. A file the form lists that it
    can't file never holds the rest up: report says what became of it. Its own file filed already
    means the answer to the same save was lost, so what that save wrote isn't written twice."""
    from domain import GateError, _open_job, _refresh_draft_gate, _refuse, local_today
    job = _open_job(state, job_id)
    notes = (notes or "").strip()
    report = report if report is not None else {}
    report.setdefault("already_saved", [])
    report.setdefault("not_added", [])
    entries = clean_record_entries(job, body.get("entries"), actor, report)
    day = _record_day(body)
    if report["already_saved"]:
        entries = [e for e in entries if e["attachments"]]
        said = {d.get("notes") for d in state.get("daily_records", [])
                if d.get("id") in {r.get("daily_record_id") for r in job.get("records", []) if r["id"] in report["already_saved"]}}
        if not notes or notes in said:
            notes = ""
            if not entries:
                return job
            body = {k: body[k] for k in ("today", "date") if k in body}
    if report["not_added"] and not notes and not entries and not any(body.get(k) not in (None, "", [], {}) for k in
                                                                     ("crew_count", "crew_names", "weather", "weather_note", "materials", "deliveries", "sections", "attachments")):
        message = "That file is someone else's, or was filed already, so it stays on the job records as it is. Add your own file, or write the diary entry."
        raise GateError(message, fields={"attachments": message}, extra={"stale_record_ids": [x["record_id"] for x in report["not_added"]]})
    details = {k: v for k, v in body.items() if k != "entries"}
    has_day = any(body.get(k) not in (None, "", [], {}) for k in
                  ("crew_count", "crew_names", "weather", "weather_note", "materials", "deliveries", "sections", "attachments"))
    if not notes and (has_day or not entries):
        message = "Enter the diary entry." if has_day else "Enter the diary entry, or add a document below."
        _refuse("", {"notes": message})
    daily_id = ""
    if notes:
        details["attachments"] = list(body.get("attachments") or []) + [rid for e in entries for rid in e["attachments"]]
        _crew_from_sheet(details, entries)
        at = None if day == local_today(body.get("today")) else f"{day}T12:00:00+00:00"
        add_daily_record(state, job_id, notes, actor, at=at, details=details, gate_over=gate_over)
        daily_id = state["daily_records"][-1]["id"]
    for entry in entries:
        _apply_entry(state, job, entry, day, actor, daily_id)
    if entries and not job.get("closed"):
        _refresh_draft_gate(job, state)
    return job


def _added_rows(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """What a daily record put on the field totals: quantities_added, or for a record from before it,
    all of its quantities or none (as _quantities_from_records reads them)."""
    if "quantities_added" in rec:
        return list(rec.get("quantities_added") or [])
    return [] if rec.get("quantities_not_applied") else list((rec.get("sections") or {}).get("quantities") or [])


def _same_quantities(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    key = lambda rows: sorted((r["line_id"], r["qty_milli"], r.get("to_date_milli")) for r in rows)
    return key(a) == key(b)


def _daily_job_record(job: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any] | None:
    """The "daily" job record that reads a daily record out: linked by id, or for one saved before
    the link, the one whose note is that record's note."""
    daily = [r for r in job.get("records", []) if r.get("kind") == "daily"]
    linked = next((r for r in daily if r.get("daily_record_id") == rec["id"]), None)
    if linked:
        return linked
    said = daily_record_note(job, rec)
    return next((r for r in daily if not r.get("daily_record_id") and r.get("note") == said), None)


def daily_record_for(state: dict[str, Any], job: dict[str, Any], record_id: str) -> dict[str, Any] | None:
    """The daily record a job record of kind "daily" reads out, for its Edit."""
    rec = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not rec or rec.get("kind") != "daily":
        return None
    ids = set(job.get("daily_records") or [])
    for d in state.get("daily_records", []):
        if d.get("id") in ids and _daily_job_record(job, d) is rec:
            return d
    return None


def _drop_typed_record(state: dict[str, Any], job: dict[str, Any], rec: dict[str, Any], actor: str) -> bool:
    """A row taken off a saved job record on Edit: a record with no file goes, and comes off any
    draft's supporting documents; one with a file, or one that went out with a pay app, stays on the
    job records, no longer part of this job record. Returns whether it went."""
    from domain import DRAFT_STATUSES, audit
    rec.pop("daily_record_id", None)
    if rec.get("file_ids"):
        return False
    packets = [(inv, inv.get("packet") or []) for inv in job.get("invoices", [])] + [(None, job.get("packet") or []), (None, job.get("closeout_packet") or [])]
    if any(inv is not None and inv.get("status") not in DRAFT_STATUSES and a.get("record_id") == rec["id"]
           for inv, packet in packets for item in packet for a in item.get("attachments") or []):
        return False
    for inv, packet in packets:
        if inv is not None and inv.get("status") not in DRAFT_STATUSES:
            continue
        for item in packet:
            if any(a.get("record_id") == rec["id"] for a in item.get("attachments") or []):
                item["attachments"] = [a for a in item["attachments"] if a.get("record_id") != rec["id"]]
                if not item["attachments"] and item.get("present") and not item.get("override"):
                    item["present"] = False
    job["records"] = [r for r in job["records"] if r is not rec]
    audit(state, job["id"], "edit_record", f"{actor} took {rec['label']} off a job record.", actor)
    return True


def update_job_record(state: dict[str, Any], job_id: str, daily_id: str, notes: str, actor: str, body: dict[str, Any],
                      report: dict[str, list] | None = None) -> dict[str, Any]:
    """Edit on a saved job record (Stephen, Nov 26): the same form again, with all its parts. The
    body is what New job record sends; rows that came from the saved record carry their record_id.
    The Diary and the day's details are replaced; quantities changed come off the field totals and
    the new ones go on (refused once a sent pay app may have billed them: Correct field total on the
    schedule card changes those); each row saved before is changed in place, a row taken off goes
    (_drop_typed_record) and a new row is added, as on a new record. Audited."""
    from domain import GateError, DRAFT_STATUSES, _open_job, _refresh_draft_gate, _refuse, audit, utcnow
    import sov
    job = _open_job(state, job_id)
    rec = next((d for d in state.get("daily_records", []) if d.get("id") == daily_id and d.get("job_id") == job_id), None)
    if not rec:
        raise GateError("That job record is not on this job.")
    notes = (notes or "").strip()
    if not notes:
        _refuse("", {"notes": "Enter the diary entry."})
    report = report if report is not None else {}
    report.setdefault("already_saved", [])
    report.setdefault("not_added", [])
    entries = clean_record_entries(job, body.get("entries"), actor, report)
    linked = {r["id"]: r for r in job.get("records", []) if r.get("daily_record_id") == daily_id and r.get("kind") != "daily"}
    for entry in entries:
        if entry["record_id"] and entry["record_id"] not in linked:
            raise GateError("Part of this job record was changed or taken off since you opened it. Open it again.")
    day = _record_day({**body, "today": body.get("today")}) if body.get("date") else str(rec.get("at") or "")[:10]
    shown = _daily_job_record(job, rec)
    old_rows = _added_rows(rec)
    details = {k: v for k, v in body.items() if k != "entries"}
    _crew_from_sheet(details, entries)
    credit: dict[str, int] = {}
    for row in old_rows:
        credit[row["line_id"]] = credit.get(row["line_id"], 0) + int(row["qty_milli"])
    before_q = (rec.get("sections") or {}).get("quantities") or []
    extra = clean_daily_details(state, job, details, credit=credit, gate_over=True, own={r["line_id"]: r for r in before_q})
    new_q = (extra.get("sections") or {}).get("quantities") or []
    if not _same_quantities(before_q, new_q):
        sent = [inv for inv in sov.live_invoices(job) if inv.get("status") not in DRAFT_STATUSES and str(inv.get("created_at") or "") >= str(rec.get("at") or "")]
        if old_rows and sent:
            raise GateError(f"The quantities on this job record may be billed on {sent[0]['number']}, which has gone to the client. "
                            "Leave them as they are here, and change the field total with Correct field total on the Schedule of values card.",
                            fields={"quantities": "These quantities may already be billed."})
        by_id = {line["id"]: line for line in sov.lines(job)}
        for row in old_rows:
            line = by_id.get(row["line_id"])
            if line:
                line["field_milli"] = max(0, sov.field_total(line) - int(row["qty_milli"]))
        job["completed_value_cents"] = sov.totals({"sov": {"op_bp": sov.op_bp(job), "lines": [sov.anticipated(job, l) for l in sov.lines(job)]}})["to_date_cents"]
        added, not_applied = _apply_quantities(job, new_q, day) if new_q else ([], "")
        rec["quantities_added"] = added
        if not_applied:
            rec["quantities_not_applied"] = not_applied
        else:
            rec.pop("quantities_not_applied", None)
    rec["notes"] = notes
    keep_attached = [rid for rid in rec.get("attachments", []) if rid in linked]
    for k in ("crew_count",):
        rec.pop(k, None)
    rec.update({k: v for k, v in extra.items() if k != "attachments"})
    if day != str(rec.get("at") or "")[:10]:
        rec["at"] = f"{day}T12:00:00+00:00"
    kept: set[str] = set()
    for entry in entries:
        if entry["record_id"]:
            old = linked[entry["record_id"]]
            kept.add(old["id"])
            head = f"{entry['word']}: {entry['title']}" if entry["word"] and entry["title"] else entry["title"]
            if head:
                old["label"] = head[:200]
            if entry["note"] or entry["section"] != "photos":
                old["note"] = entry["note"]
            for k in ("crew", "hours_milli", "amount_cents"):
                old.pop(k, None)
            old.update(entry["extra"])
            old["on_date"] = day
            if entry["attachments"]:
                _apply_entry(state, job, {**entry, "record_id": ""}, day, actor, daily_id)
        else:
            _apply_entry(state, job, entry, day, actor, daily_id)
    gone = [r for rid, r in linked.items() if rid not in kept and _drop_typed_record(state, job, r, actor)]
    rec["attachments"] = [rid for rid in keep_attached if rid in kept] + [r["id"] for r in job.get("records", [])
                          if r.get("daily_record_id") == daily_id and r.get("file_ids") and r["id"] not in keep_attached]
    label = f"Diary entry {day}" + (f": {_qty_words(job, new_q)}"[:160] if new_q else "")
    if shown is not None:
        shown["label"], shown["note"], shown["daily_record_id"] = label[:200], daily_record_note(job, rec), daily_id
        shown["edited"] = {"by": actor, "at": utcnow()}
    audit(state, job_id, "daily_record_edit", f"{actor} changed the job record of {day}" + (f", taking off {', '.join(r['label'] for r in gone)}" if gone else "") + ".", actor)
    if not job.get("closed"):
        _refresh_draft_gate(job, state)
    return job


def quantities_blocked(job: dict[str, Any]) -> str:
    """Why quantities can't be added to this job's schedule of values right now, in plain words
    (a daily record's and an imported file's alike); blank when they can. A draft with the client
    doesn't stop them: its lines and rates are fixed, but the field keeps recording (Nov 3)."""
    import sov
    if job.get("closed"):
        return "The job is finished, so the schedule of values doesn't change."
    if not sov.has_sov(job):
        return "This job has no schedule of values to add quantities to."
    if job.get("work_status") == "complete":
        return "The work is finished, so every line is already counted as done."
    return ""


def _field_floor(job: dict[str, Any], line: dict[str, Any]) -> int:
    """What a lump sum's % done to date can't go below (Round 1 hunt): what the field recorded, and on a
    line with nothing recorded yet, what pay apps that went out billed. A figure typed on a draft is
    the pay app's own, never the floor, even after the draft is deleted."""
    import sov
    if "field_milli" in line:
        return int(line["field_milli"])
    draft = next((inv for inv in job.get("invoices", []) if inv.get("status") in ("draft", "awaiting_approval")), None)
    return int(sov.billed(job, skip=draft)["qty_by_line"].get(line["id"], 0))


def add_field_quantities(job: dict[str, Any], rows: list[dict[str, Any]], day: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """Add quantities to the lines' field totals, each up to its scheduled quantity. Pay apps and
    drafts aren't touched: the card's suggested value moves, and Progress Pay App bills it. Returns
    the rows as added, and what wasn't added on each line, in words."""
    from domain import over_schedule_note
    import sov
    by_id = {line["id"]: line for line in sov.lines(job)}
    added, over = [], []
    for row in rows:
        line = by_id[row["line_id"]]
        # A % done to date is the step up from its own floor (_field_floor), not from a draft's figure.
        total = row["to_date_milli"] - row["qty_milli"] if "to_date_milli" in row else sov.field_total(line)
        fits = min(row["qty_milli"], max(0, line["qty_milli"] - total))
        if fits < row["qty_milli"]:
            over.append(over_schedule_note(line, row["qty_milli"] - fits))
        if fits or ("to_date_milli" in row and "field_milli" not in line):
            line["field_milli"] = total + fits
            if day:
                line["from_daily_on"] = day
            added.append({"line_id": line["id"], "qty_milli": fits})
    if added:
        job["completed_value_cents"] = sov.totals({"sov": {"op_bp": sov.op_bp(job), "lines": [sov.anticipated(job, l) for l in sov.lines(job)]}})["to_date_cents"]
    return added, over


def _apply_quantities(job: dict[str, Any], rows: list[dict[str, Any]], day: str) -> tuple[list[dict[str, Any]], str]:
    """Add the quantities put in place today to the field totals (add_field_quantities). A diary
    entry is never lost because they can't be added (B5 of the Oct 30 review): it is saved anyway
    with them on it. Returns the rows added and why any weren't, in plain words, for the record,
    its note and the page; blank when all were."""
    blocked = quantities_blocked(job)
    if blocked:
        return [], blocked + " They are kept on this diary entry."
    added, over = add_field_quantities(job, rows, day)
    return added, " ".join(over) + (" The rest is kept on this diary entry." if over else "")
