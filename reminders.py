"""Periodic internal reminders and in-app notifications (Automations; Stephen's texting note of Nov 1).

Each person picks, for each kind of reminder, any of in the app, email and text. Everything is off
until they turn it on. The same item comes round again every REPEAT_DAYS (REMINDER_REPEAT_DAYS)
until what it asks for is done, and then stops.

Email and text: the hourly loop (monthly.run_once) calls run(). From the daily email's hour, at most
one message per person per channel per day, listing the items due on that channel.
In the app: sync_notifications() adds what is due to the person's list whenever they load the app,
with an unread count; nothing is sent. A notification whose item has been dealt with is marked
done and stops counting.
All of this is kept in <data dir>/reminder_jobs.json, outside the company's data, so a restart
doesn't repeat anything. An email or text's place is written there before it goes, so a file that
can't be written means nothing is sent, not the same thing again each hour. If the file can't be
read, nothing goes out and nothing is written: a copy is kept as reminder_jobs.json.bad-<time>,
the server alerts, and the file stays until a person fixes or removes it (removing it starts the
reminders and the in-app list afresh).

The kinds:
- dates: a follow-up or payment date in the next week or already past, a pay app due day in the
  next week, and a job's anticipated end date in the next week;
- missing: a client on an open job with no billing email or phone, and a pay app held up by
  supporting documents or a signature;
- co_records: a job record in the last 30 days with change order work whose change order lacks
  what it needs (not signed; time and materials with no hours or backup), or with work no change
  order covers yet.
Who gets what follows the daily email: what is assigned to you and, for an owner, what nobody has.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Any

import alerts
import auth
import billing
import calendar_feed
import domain
import mailer
import sms
import storage

JOBS_FILE = "reminder_jobs.json"
KINDS = {
    "dates": ("Important dates", "Pay app cutoffs, payment dates and follow-ups in the next 7 days."),
    "missing": ("Missing information", "A client with no email or phone, and pay apps held up by what they need."),
    "co_records": ("Change order work", "A job record about a change order that doesn't have what it needs."),
}
CHANNELS = {"app": "In the app", "email": "Email", "text": "Text"}
DATE_DAYS = 7
CO_RECORD_DAYS = 30
DEFAULT_REPEAT_DAYS = 3
TEXT_MAX = 280
NOTES_MAX = 100        # notifications kept per person
SENT_KEEP_DAYS = 60    # how long what was sent is remembered
_DATE_UID = re.compile(r"^(.+)-(follow|due(?:-\d{6})?)$")
_LOCK = threading.RLock()


class StateUnreadable(Exception):
    """reminder_jobs.json is there and can't be read: what was sent isn't known."""


def _log(level: str, **fields: Any) -> None:
    record = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": level, **fields}
    sys.stderr.write(json.dumps(record, default=str) + "\n")


def repeat_days() -> int:
    try:
        return max(1, int(os.environ.get("REMINDER_REPEAT_DAYS") or DEFAULT_REPEAT_DAYS))
    except ValueError:
        return DEFAULT_REPEAT_DAYS


def clean_prefs(raw: dict[str, Any]) -> dict[str, list[str]]:
    """{kind: [channels]}. Raises ValueError on a kind or channel that doesn't exist."""
    out = {}
    for kind, channels in raw.items():
        if kind not in KINDS:
            raise ValueError("That reminder doesn't exist.")
        if not isinstance(channels, list) or any(not isinstance(c, str) or c not in CHANNELS for c in channels):
            raise ValueError("Pick in the app, email or text.")
        out[kind] = [c for c in CHANNELS if c in channels]
    return out


def _item(kind: str, job: dict[str, Any], key: str, text: str, path: str = "") -> dict[str, Any]:
    return {"kind": kind, "key": f"{kind}:{key}", "job_id": job["id"], "job": job.get("name", ""), "text": text,
            "path": path or f"/jobs/{job['id']}", "assignee_id": job.get("assignee_id") or ""}


def _date_items(state: dict[str, Any], today: date, jobs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The calendar feed's follow-ups, payment dates and pay app due days, and the anticipated end date."""
    owner_of = {inv["id"]: j for j in jobs.values() for inv in j.get("invoices", [])}
    last = today + timedelta(days=DATE_DAYS)
    out = []
    for event in calendar_feed.events(state):
        m = _DATE_UID.match(event["uid"])
        if not m or event["date"] > last:
            continue
        # A pay app due day that has gone by gives way to the next; a follow-up or payment date stays until it is dealt with.
        if m.group(2).startswith("due-") and event["date"] < today:
            continue
        job = jobs.get(m.group(1)) or owner_of.get(m.group(1))
        if job:
            out.append(_item("dates", job, event["uid"], f"{event['title']}, {event['date']:%b} {event['date'].day}."))
    for job in jobs.values():
        try:
            end = date.fromisoformat(str(job.get("anticipated_end_on") or "")[:10])
        except ValueError:
            continue
        if today <= end <= last and job.get("work_status") != "complete":
            out.append(_item("dates", job, f"end:{job['id']}:{end.isoformat()}", f"Anticipated end date, {end:%b} {end.day}."))
    return out


def _missing_items(state: dict[str, Any], jobs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    payers = {p["id"]: p for p in state.get("payers", [])}
    out, seen = [], set()
    for job in jobs.values():
        payer = payers.get(job.get("payer_id"))
        if payer and payer["id"] not in seen:
            seen.add(payer["id"])
            lacks = [word for word, field in (("billing email", "ap_email"), ("phone", "phone")) if not str(payer.get(field) or "").strip()]
            if lacks:
                out.append(_item("missing", job, f"client:{payer['id']}", f"{payer.get('name', 'A client')} has no {' or '.join(lacks)} on file.", "/jobs/clients"))
        if domain.is_blocked(job):
            out.append(_item("missing", job, f"blocked:{job['id']}", "The pay app needs: " + " ".join(domain.blocker_lines(job))))
    return out


def _co_items(state: dict[str, Any], now: datetime, jobs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    since = (now - timedelta(days=CO_RECORD_DAYS)).isoformat(timespec="seconds")
    needs: dict[tuple[str, str], tuple[dict[str, Any], set[str]]] = {}
    out = []
    for rec in state.get("daily_records", []):
        if not isinstance(rec, dict):
            continue
        job = jobs.get(rec.get("job_id"))
        if not job or (rec.get("at") or "") < since:
            continue
        sections = rec.get("sections") if isinstance(rec.get("sections"), dict) else {}
        rows = sections.get("co_work") if isinstance(sections.get("co_work"), list) else []
        for n, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            co = next((c for c in job.get("change_orders", []) if c["id"] == row.get("co_id")), None)
            if not row.get("co_id"):
                # Answered once a change order has been written since the record.
                if not any((c.get("created_at") or "") >= rec["at"] for c in job.get("change_orders", [])):
                    out.append(_item("co_records", job, f"new:{rec['id']}:{n}", f"The job record of {rec['at'][:10]} has work no change order covers: {row.get('description', '')}."))
                continue
            if not co or co.get("status") == "withdrawn":
                continue
            _, lacks = needs.setdefault((job["id"], co["id"]), (co, set()))
            if co.get("status") == "pending":
                lacks.add("a signature")
            if co.get("pricing") == "tm":
                if not row.get("hours_milli"):
                    lacks.add("hours")
                if not co.get("support_record_ids"):
                    lacks.add("backup")
    for (job_id, co_id), (co, lacks) in needs.items():
        if lacks:
            name = f"CO-{co['number']}" if co.get("number") else "A change order"
            title = f" ({co['title']})" if co.get("title") else ""
            out.append(_item("co_records", jobs[job_id], f"co:{co_id}", f"{name}{title} has work on a job record and needs {', '.join(sorted(lacks))}."))
    return out


def build(state: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Everything worth a reminder today, for the whole company."""
    jobs = {j["id"]: j for j in state.get("jobs", []) if not j.get("closed")}
    return (_safely("dates", _date_items, state, now.date(), jobs) + _safely("missing", _missing_items, state, jobs)
            + _safely("co_records", _co_items, state, now, jobs))


def _safely(kind: str, read: Any, *args: Any) -> list[dict[str, Any]]:
    """One kind of reminder, whatever stored data it meets: the others and the page go on."""
    try:
        return read(*args)
    except Exception as exc:
        _log("error", msg=f"reminders: {kind} couldn't be worked out", reason=exc.__class__.__name__, trace=traceback.format_exc())
        alerts.notify(f"reminders-build:{kind}", f"the {kind} reminders couldn't be worked out ({exc.__class__.__name__}); see the server log")
        return []


def for_person(items: list[dict[str, Any]], person: dict[str, Any]) -> list[dict[str, Any]]:
    return domain.rows_for_user(items, person["id"], person.get("role") == "owner")


def _on_channel(items: list[dict[str, Any]], prefs: dict[str, list[str]], channel: str) -> list[dict[str, Any]]:
    return [i for i in items if channel in (prefs.get(i["kind"]) or [])]


def email_message(items: list[dict[str, Any]], company: str, base: str) -> tuple[str, str]:
    lines = [f"- {i['job']}: {i['text']}" + (f"\n  {base}{i['path']}" if base else "") for i in items]
    return (f"Duracash reminders: {len(items)}",
            f"Reminders{' at ' + company if company else ''}:\n\n" + "\n".join(lines)
            + "\n\nChange or turn these off on the Automations page.")


def text_message(items: list[dict[str, Any]]) -> str:
    """Short, and says who it is from: as many items as fit, and how many more."""
    head = f"Duracash: {len(items)} reminder{'' if len(items) == 1 else 's'}. "
    parts = [f"{i['job']}: {i['text']}" for i in items]
    body = ""
    for n, part in enumerate(parts):
        tail = f" And {len(parts) - n - 1} more on Automations." if n < len(parts) - 1 else ""
        joined = f"{body} {part}".strip()
        if len(head) + len(joined) + len(tail) > TEXT_MAX:
            if body:
                return f"{head}{body} And {len(parts) - n} more on Automations."
            room = TEXT_MAX - len(head) - len(tail) - 1
            return f"{head}{part[:room].rstrip()}…{tail}"
        body = joined
    return head + body


def _path():
    return storage.DATA_DIR / JOBS_FILE


def _unreadable(why: str) -> StateUnreadable:
    _log("error", msg=f"{JOBS_FILE} can't be read: no reminders go out until it is fixed", reason=why)
    alerts.notify("reminders-state", f"{JOBS_FILE} can't be read ({why}), so no reminders are going out. A copy is kept beside it. Fix or remove the file to start again.")
    return StateUnreadable(why)


def _clean_person(rec: dict[str, Any]) -> dict[str, Any]:
    """One person's record with only the parts of the shape they should have: a stray value is
    dropped, and never stops the page or the loop."""
    sent = rec.get("sent") if isinstance(rec.get("sent"), dict) else {}
    rec["sent"] = {c: {k: d for k, d in days.items() if isinstance(d, str)} for c, days in sent.items() if isinstance(days, dict)}
    notes = rec.get("notes") if isinstance(rec.get("notes"), list) else []
    rec["notes"] = [{"kind": "", "job": "", "text": "", "path": "", "at": "", "read": False, **n} for n in notes
                    if isinstance(n, dict) and isinstance(n.get("id"), str) and isinstance(n.get("key"), str)]
    if not isinstance(rec.get("results"), dict):
        rec.pop("results", None)
    return rec


def _load() -> dict[str, Any]:
    """Everything kept, {company: {person: record}}. No file is nothing kept yet. A file that can't
    be read raises StateUnreadable (and is copied aside, logged and alerted); a company or person
    of the wrong shape is left out."""
    try:
        raw = _path().read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise _unreadable(exc.__class__.__name__)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        storage.set_aside(_path())
        raise _unreadable("it is damaged")
    return {company: {uid: _clean_person(rec) for uid, rec in people.items() if isinstance(rec, dict)}
            for company, people in data.items() if isinstance(people, dict)}


def _person(data: dict[str, Any], company_id: str, user_id: str) -> dict[str, Any]:
    return data.setdefault(company_id, {}).setdefault(user_id, {})


def last_result(company_id: str, user_id: str) -> dict[str, Any] | None:
    """{day, results: {email, text}} of this person's last reminder."""
    try:
        with _LOCK:
            rec = _load().get(company_id, {}).get(user_id) or {}
    except StateUnreadable:
        return None
    return {"day": rec.get("day"), "results": rec.get("results") or {}} if rec.get("day") else None


def forget_company(company_id: str) -> None:
    try:
        with _LOCK:
            data = _load()
            if data.pop(company_id, None) is not None:
                storage.write_json(_path(), data)
    except StateUnreadable:
        pass   # logged and alerted; a person deals with the file


def _due(items: list[dict[str, Any]], sent: dict[str, str], today: date) -> list[dict[str, Any]]:
    """The items not sent on this channel in the last REPEAT_DAYS."""
    since = (today - timedelta(days=repeat_days() - 1)).isoformat()
    return [i for i in items if sent.get(i["key"], "") < since]


def _mark_sent(rec: dict[str, Any], channel: str, items: list[dict[str, Any]], today: date) -> None:
    sent = rec.setdefault("sent", {}).setdefault(channel, {})
    keep = (today - timedelta(days=SENT_KEEP_DAYS)).isoformat()
    for key in [k for k, day in sent.items() if day < keep]:
        del sent[key]
    sent.update({i["key"]: today.isoformat() for i in items})


def _send_email(site, state: dict[str, Any], company_id: str, person: dict[str, Any], items: list[dict[str, Any]]) -> str:
    if not person.get("email_verified"):
        return "not sent: confirm your email address first (My account)"
    try:
        subject, body = email_message(items, state.get("company", {}).get("name", ""), site.app_url() or "")
        return site.send(person["email"], subject, body, company_id)
    except Exception as exc:
        site.log("error", msg="reminder email failed", company=company_id, trace=traceback.format_exc())
        return f"not sent: unexpected error ({exc.__class__.__name__})"


def _send_text(site, state: dict[str, Any], company_id: str, person: dict[str, Any], items: list[dict[str, Any]]) -> str:
    try:
        return sms.send_to_member(state, company_id, person["id"], text_message(items))
    except Exception as exc:
        site.log("error", msg="reminder text failed", company=company_id, trace=traceback.format_exc())
        return f"not sent: unexpected error ({exc.__class__.__name__})"


def run(site, now: datetime | None = None) -> list[str]:
    """One turn of the hourly loop. Each channel apart: one failing never stops the other. Returns
    one line per company reminded, for the log (counts and company ids, never an address)."""
    from monthly import daily_email_hour   # monthly imports this module
    now = now or datetime.now(timezone.utc)
    if now.hour < daily_email_hour():
        return []
    today, lines, deleted = now.date(), [], site.deleted()
    day = today.isoformat()
    senders = {"email": _send_email, "text": _send_text}
    for company_id in site.company_ids():
        if company_id == auth.DEMO_COMPANY_ID or company_id in deleted:
            continue
        try:
            with _LOCK:
                done = _load().get(company_id, {})
            people = [p for p in auth.reminder_people(company_id) if (done.get(p["id"]) or {}).get("day") != day
                      and any(c in senders for chosen in (p.get("reminders") or {}).values() for c in chosen)]
            if not people:
                continue
            state = site.read(company_id)
            if state is None or billing.read_only_why(state):
                continue
            items, sent = build(state, now), 0
            for person in people:
                mine = for_person(items, person)
                chosen = person.get("reminders") or {}
                with _LOCK:   # each send's place is taken and written first; the day counts with nothing to say too
                    data = _load()
                    rec = _person(data, company_id, person["id"])
                    due = {channel: _due(_on_channel(mine, chosen, channel), (rec.get("sent") or {}).get(channel) or {}, today) for channel in senders}
                    due = {channel: rows for channel, rows in due.items() if rows}
                    before = {channel: dict((rec.get("sent") or {}).get(channel) or {}) for channel in due}
                    rec["day"] = day
                    for channel, rows in due.items():
                        _mark_sent(rec, channel, rows, today)
                    storage.write_json(_path(), data)
                results = {channel: senders[channel](site, state, company_id, person, rows) for channel, rows in due.items()}
                delivered = [channel for channel, result in results.items() if result == "sent"]
                if results:
                    with _LOCK:   # the last thing sent stays on show; what didn't go gets its place back
                        data = _load()
                        rec = _person(data, company_id, person["id"])
                        rec["results"] = results
                        for channel, rows in due.items():
                            if channel not in delivered:
                                sent_days = rec.setdefault("sent", {}).setdefault(channel, {})
                                for row in rows:
                                    if row["key"] in before[channel]:
                                        sent_days[row["key"]] = before[channel][row["key"]]
                                    else:
                                        sent_days.pop(row["key"], None)
                        storage.write_json(_path(), data)
                sent += len(delivered)
            if sent:
                lines.append(f"{company_id}  reminders: {sent} sent")
        except Exception as exc:  # one company's failure leaves the others theirs
            site.log("error", msg=f"{company_id}: reminders failed: {exc.__class__.__name__}: {exc}")
            alerts.notify(f"reminders:{company_id}", f"the reminders for {company_id} failed ({exc.__class__.__name__}); see the server log")
    return lines


# ----- in the app -----

def sync_notifications(state: dict[str, Any], person: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Add what is due to this person's list, and return the list, newest first. An item already
    dealt with reads done, and no longer counts as unread."""
    items = for_person(build(state, now), person)
    live = {i["key"] for i in items}
    prefs = person.get("reminders") or {}
    today = now.date()
    with _LOCK:
        try:
            data = _load()
        except StateUnreadable:
            return []
        rec = _person(data, person.get("company_id", ""), person["id"])
        due = _due(_on_channel(items, prefs, "app"), (rec.get("sent") or {}).get("app") or {}, today)
        if due:
            notes = rec.setdefault("notes", [])
            stamp = now.isoformat(timespec="seconds")
            notes.extend({"id": "n-" + secrets.token_hex(4), "key": i["key"], "kind": i["kind"], "job_id": i["job_id"], "job": i["job"], "text": i["text"],
                          "path": i["path"], "at": stamp, "read": False} for i in due)
            del notes[:-NOTES_MAX]
            _mark_sent(rec, "app", due, today)
            try:
                storage.write_json(_path(), data)
            except OSError as exc:   # shown this once; they come round again when it can be kept
                _log("error", msg=f"{JOBS_FILE} couldn't be written", reason=exc.__class__.__name__)
                alerts.notify("reminders-state-write", f"{JOBS_FILE} couldn't be written ({exc.__class__.__name__}); see the server log")
        notes = rec.get("notes") or []
    return [{**n, "done": n["key"] not in live} for n in reversed(notes) if _still_theirs(state, person, n)]


def _still_theirs(state: dict[str, Any], person: dict[str, Any], note: dict[str, Any]) -> bool:
    """A member sees a notification while the job is theirs; taken off it, it is hidden, job name
    and all, and back if they are put on it again. An owner sees all. A note from before job_id was
    kept goes by the job's name."""
    if person.get("role") == "owner":
        return True
    field, value = ("id", note["job_id"]) if note.get("job_id") else ("name", note.get("job"))
    return any(j.get(field) == value and j.get("assignee_id") == person["id"] for j in state.get("jobs", []))


def mark_read(company_id: str, user_id: str, note_id: str = "") -> None:
    """One notification read, or all of them with no id."""
    with _LOCK:
        try:
            data = _load()
        except StateUnreadable:
            return
        rec = data.get(company_id, {}).get(user_id) or {}
        changed = False
        for note in rec.get("notes") or []:
            if not note["read"] and (not note_id or note["id"] == note_id):
                note["read"] = changed = True
        if changed:
            storage.write_json(_path(), data)


def page(state: dict[str, Any], person: dict[str, Any], now: datetime) -> dict[str, Any]:
    """What the Automations page shows this person."""
    prefs = {kind: list((person.get("reminders") or {}).get(kind) or []) for kind in KINDS}
    notes = sync_notifications(state, person, now)
    return {"kinds": [{"key": k, "label": v[0], "help": v[1]} for k, v in KINDS.items()],
            "channels": [[k, v] for k, v in CHANNELS.items()], "prefs": prefs, "repeat_days": repeat_days(),
            "notifications": notes, "unread": sum(1 for n in notes if not n["read"] and not n["done"]),
            "can_text": bool(person.get("sms_opt_in")), "texts_stopped": bool(person.get("sms_stopped")),
            "email_ready": mailer.configured(), "texts_ready": sms.configured(),
            "last": last_result(person.get("company_id", ""), person["id"])}
