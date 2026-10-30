"""Tasks: what someone on the team has to do, who it is assigned to and when it is due (the
tasks-daily stream, G5 and T3).

Two kinds of item make the To Do list:
- the rows domain.today_rows works out from the jobs (a pay app to send, a follow-up due...),
  which are not stored. Each has a stable `key` (kind:job_id:item). Who it is assigned to, and a
  due date someone set by hand, are kept in state["task_assignments"][key] =
  {assignee_id, assignee_name, due_override, row_due, set_by, set_at}. row_due is the row's own
  due date when the override was set: a row that comes back under the same key (the next
  follow-up, next month's cutoff) has a new one, and the old override no longer applies.
- tasks a person adds by hand: state["tasks"] = [{id, title, job_id, assignee_id, assignee_name,
  due, ball_in_court, status open/done, created_by, created_at, done_by, done_at}].

Ball in court is "us" (someone here has to act) or "client" (we are waiting on the client).
The assignee is a user of the company: the server passes `members` ({user id: name}) from
auth.team, and an id that isn't in it is refused.

Imported at the bottom of domain/__init__.py (from .tasks import *). Helpers from domain
are imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

__all__ = [
    "BALL_SIDES", "CLIENT_KINDS", "TASK_TITLE_MAX", "DONE_TASKS_SHOWN_DAYS", "row_key", "decorate_rows",
    "add_task", "edit_task", "set_task_done", "assign_row", "reassign_open_tasks", "visible_tasks",
    "tasks_due_by", "rows_for_user", "prune_assignments",
]

BALL_SIDES = ("us", "client")
# Rows that wait on the client: a pay app they approved with a date to pay, and one sent and
# unpaid past their follow-up window (the follow-up is a nudge; the payment is theirs to make).
CLIENT_KINDS = {"approved", "follow_up"}
TASK_TITLE_MAX = 200
# Done tasks stay on the list (struck through) this long; older ones are kept but not sent.
DONE_TASKS_SHOWN_DAYS = 30


def row_key(row: dict[str, Any]) -> str:
    """kind:job_id:item, the same for the same row from one snapshot to the next."""
    item = row.get("invoice_id") or row.get("co_id") or row.get("item_id") or row.get("email_id") or ""
    return f"{row.get('kind', '')}:{row.get('job_id', '')}:{item}"


def decorate_rows(state: dict[str, Any], rows: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Give each of today_rows its key, ball in court, assignee and due date (today_rows calls this).
    A row's own due date (row["due"], from the job's dates) gives way to one set by hand; with
    neither it is blank, which means today where the person is (static/js/tasks.js todoItems;
    the daily email counts it as due), so a row isn't dated with the server's UTC day, which is
    already tomorrow on a US evening (review C3). The assignee is the one set on the row, else the job's default
    assignee (job["assignee_id"], when the job has one), else nobody."""
    jobs = {j["id"]: j for j in state.get("jobs", [])}
    assigned = state.get("task_assignments") or {}
    for row in rows:
        row["key"] = row_key(row)
        row["ball_in_court"] = "client" if row["kind"] in CLIENT_KINDS else "us"
        a = assigned.get(row["key"]) or {}
        job = jobs.get(row.get("job_id")) or {}
        if a.get("assignee_id"):
            row["assignee_id"], row["assignee_name"] = a["assignee_id"], a.get("assignee_name", "")
        else:
            row["assignee_id"] = str(job.get("assignee_id") or "")
            row["assignee_name"] = str(job.get("assignee_name") or "") if row["assignee_id"] else ""
        own = row.get("due") or ""
        row["own_due"] = own
        row["due"] = (a.get("due_override") if _override_holds(a, own) else "") or own
    return rows


def _override_holds(entry: dict[str, Any], own_due: str) -> bool:
    """Whether a hand-set due date still belongs to the row: set while the row had the same own
    due date (review C5). One saved before row_due was kept holds."""
    return bool(entry.get("due_override")) and entry.get("row_due", own_due) == own_due


def _prune(store: dict[str, Any], rows: dict[str, dict[str, Any]]) -> bool:
    """Drop assignments of rows that are gone (sent, paid, filed) and due dates set for an earlier
    turn of a row that came back. True when anything changed."""
    changed = False
    for key in list(store):
        row = rows.get(key)
        entry = store[key]
        if row is None:
            del store[key]
            changed = True
        elif entry.get("due_override") and not _override_holds(entry, row.get("own_due", "")):
            entry.pop("due_override", None)
            entry.pop("row_due", None)
            if not entry.get("assignee_id"):
                del store[key]
            changed = True
    return changed


def prune_assignments(state: dict[str, Any], now: datetime | None = None) -> bool:
    """_prune against the rows as they are now; run before every save (server._mutate) so
    assignments of rows that are gone don't pile up."""
    from domain import today_rows
    store = state.get("task_assignments")
    if not store:
        return False
    return _prune(store, {r["key"]: r for r in today_rows(state, now)})


def _members(members: dict[str, str] | None) -> dict[str, str]:
    return {str(k): str(v or "") for k, v in (members or {}).items()}


def _assignee(members: dict[str, str] | None, value: Any) -> tuple[str, str]:
    """(id, name) for an assignee from a form: blank is nobody; someone not on the team is refused."""
    from domain import _refuse
    uid = str(value or "").strip()
    if not uid:
        return "", ""
    team = _members(members)
    if uid not in team:
        _refuse("", {"assignee_id": "Pick someone on your team."})
    return uid, team[uid]


def _due(value: Any) -> str:
    from domain import _day
    day = _day(value, "due", "Enter the due date, like 2026-11-03.")
    return day.isoformat() if day else ""


def _title(value: Any) -> str:
    from domain import _refuse
    title = " ".join(str(value or "").split())
    if not title:
        _refuse("", {"title": "Enter what needs doing."})
    if len(title) > TASK_TITLE_MAX:
        _refuse("", {"title": f"Keep it under {TASK_TITLE_MAX} characters."})
    return title


def _ball(value: Any) -> str:
    from domain import _refuse
    side = str(value or "us").strip().lower()
    if side not in BALL_SIDES:
        _refuse("", {"ball_in_court": "Pick Us or Client."})
    return side


def _task_job(state: dict[str, Any], value: Any) -> str:
    from domain import _refuse
    job_id = str(value or "").strip()
    if not job_id:
        return ""
    job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
    if not job or job.get("closed"):
        _refuse("", {"job_id": "Pick an open job, or none."})
    return job_id


def _find_task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    from domain import GateError
    task = next((t for t in state.get("tasks", []) if t.get("id") == task_id), None)
    if not task:
        raise GateError("That task isn't on the list any more.")
    return task


def _who(name: str) -> str:
    return name or "nobody"


def _when(due: str) -> str:
    from domain import _day_words
    return _day_words(due) if due else "no date"


def add_task(state: dict[str, Any], body: dict[str, Any], members: dict[str, str] | None, actor: str = "owner",
             actor_id: str = "") -> dict[str, Any]:
    """A task added by hand: title, and optionally a job, an assignee, a due date and whose court
    it is in (us by default)."""
    from domain import audit, utcnow
    title = _title(body.get("title"))
    job_id = _task_job(state, body.get("job_id"))
    uid, name = _assignee(members, body.get("assignee_id"))
    due = _due(body.get("due"))
    side = _ball(body.get("ball_in_court"))
    task = {"id": "task-" + secrets.token_hex(4), "title": title, "job_id": job_id, "assignee_id": uid, "assignee_name": name,
            "due": due, "ball_in_court": side, "status": "open", "created_by": actor, "created_by_id": actor_id,
            "created_at": utcnow(), "done_by": "", "done_at": ""}
    state.setdefault("tasks", []).append(task)
    audit(state, job_id, "task_add", f"{actor} added a task: {title}. Assigned to {_who(name)}, due {_when(due)}"
          + (", waiting on the client." if side == "client" else "."), actor)
    return task


def edit_task(state: dict[str, Any], task_id: str, body: dict[str, Any], members: dict[str, str] | None,
              actor: str = "owner") -> dict[str, Any]:
    """Change a task's title, job, assignee, due date or ball in court (only the fields sent)."""
    from domain import audit, GateError
    task = _find_task(state, task_id)
    changes = []
    if "title" in body:
        title = _title(body.get("title"))
        if title != task["title"]:
            changes.append(f"renamed it {title}")
            task["title"] = title
    if "job_id" in body:
        job_id = _task_job(state, body.get("job_id"))
        if job_id != task.get("job_id", ""):
            changes.append("moved it to another job" if job_id else "took it off its job")
            task["job_id"] = job_id
    if "assignee_id" in body:
        uid, name = _assignee(members, body.get("assignee_id"))
        if uid != task.get("assignee_id", ""):
            changes.append(f"assigned it to {_who(name)}")
            task["assignee_id"], task["assignee_name"] = uid, name
    if "due" in body:
        due = _due(body.get("due"))
        if due != task.get("due", ""):
            changes.append(f"set it due {_when(due)}")
            task["due"] = due
    if "ball_in_court" in body:
        side = _ball(body.get("ball_in_court"))
        if side != task.get("ball_in_court", "us"):
            changes.append("put it in the client's court" if side == "client" else "put it in our court")
            task["ball_in_court"] = side
    if not changes:
        raise GateError("Nothing changed.")
    audit(state, task.get("job_id", ""), "task_edit", f"{actor} changed the task {task['title']}: {', '.join(changes)}.", actor)
    return task


def set_task_done(state: dict[str, Any], task_id: str, done: bool, actor: str = "owner") -> dict[str, Any]:
    from domain import audit, utcnow, GateError
    task = _find_task(state, task_id)
    if (task.get("status") == "done") == done:
        raise GateError("That task is already done." if done else "That task is already open.")
    task["status"] = "done" if done else "open"
    task["done_by"], task["done_at"] = (actor, utcnow()) if done else ("", "")
    audit(state, task.get("job_id", ""), "task_done" if done else "task_reopen",
          f"{actor} {'ticked off' if done else 'reopened'} the task {task['title']}.", actor)
    return task


def assign_row(state: dict[str, Any], key: Any, body: dict[str, Any], members: dict[str, str] | None, actor: str = "owner",
               now: datetime | None = None) -> dict[str, Any] | None:
    """Assign one of the To Do rows the jobs make (by its key), and optionally set its due date.
    Blank assignee and blank due take the row back to its defaults. Keys of rows that are gone
    (sent, paid, filed) are dropped at the same time."""
    from domain import audit, utcnow, today_rows, GateError
    rows = {r["key"]: r for r in today_rows(state, now)}
    key = str(key or "")
    row = rows.get(key)
    if not row:
        raise GateError("That item is no longer on the To Do list.")
    store = state.setdefault("task_assignments", {})
    _prune(store, rows)
    entry = dict(store.get(key) or {})
    said = []
    if "assignee_id" in body:
        uid, name = _assignee(members, body.get("assignee_id"))
        if uid != entry.get("assignee_id", ""):
            entry["assignee_id"], entry["assignee_name"] = uid, name
            said.append(f"assigned it to {_who(name)}")
    if "due" in body:
        due = _due(body.get("due"))
        if due != entry.get("due_override", ""):
            entry["due_override"] = due
            entry["row_due"] = row.get("own_due", "")
            if not due:
                entry.pop("row_due", None)
            said.append(f"set it due {_when(due)}" if due else "put its due date back")
    if not said:
        raise GateError("Nothing changed.")
    entry.update(set_by=actor, set_at=utcnow())
    if entry.get("assignee_id") or entry.get("due_override"):
        store[key] = entry
    else:
        store.pop(key, None)
    audit(state, row["job_id"], "task_assign", f"{actor} {' and '.join(said)}: {row['label']}, {row['job']}.", actor)
    return store.get(key)


def reassign_open_tasks(state: dict[str, Any], from_user: str, to_user: str, to_name: str, actor: str = "owner") -> int:
    """Someone left the team: their open tasks, their To Do rows and the jobs they were the default
    assignee of (job["assignee_id"], whose rows would otherwise reach nobody) go to `to_user` (the
    owner who removed them). Returns how many moved."""
    from domain import audit
    if not from_user:
        return 0
    moved = 0
    for task in state.get("tasks", []):
        if task.get("assignee_id") == from_user and task.get("status") != "done":
            task["assignee_id"], task["assignee_name"] = to_user, to_name
            moved += 1
    for entry in (state.get("task_assignments") or {}).values():
        if entry.get("assignee_id") == from_user:
            entry["assignee_id"], entry["assignee_name"] = to_user, to_name
            moved += 1
    for job in state.get("jobs", []):
        if job.get("assignee_id") == from_user:
            job["assignee_id"], job["assignee_name"] = to_user, to_name
            moved += 1
    if moved:
        audit(state, "", "task_assign", f"{moved} open item{'' if moved == 1 else 's'} moved to {to_name or 'the owner'}.", actor)
    return moved


def visible_tasks(state: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """What /api/state sends: every open task, and those done in the last 30 days."""
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=DONE_TASKS_SHOWN_DAYS)).isoformat()
    return [dict(t) for t in state.get("tasks", []) if t.get("status") != "done" or (t.get("done_at") or "") >= since]


def tasks_due_by(state: dict[str, Any], day: date) -> list[dict[str, Any]]:
    """Open tasks due on or before `day`, or with no date: for the daily email."""
    return [t for t in state.get("tasks", []) if t.get("status") != "done" and (not t.get("due") or t["due"] <= day.isoformat())]


def rows_for_user(items: list[dict[str, Any]], user_id: str | None, owner: bool = True) -> list[dict[str, Any]]:
    """The items one person's daily email lists: theirs, and, for an owner, those nobody has."""
    if user_id is None:
        return items
    return [i for i in items if i.get("assignee_id") == user_id or (owner and not i.get("assignee_id"))]
