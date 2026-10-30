"""Where a job stands and the facts around it (Stephen's Oct 29 review, jobs-clients stream):
the client's type (J2), the job's start date (J8), its stage and key monthly dates (J7), and
each client's jobs for the Client playbook (S10, S11).

Imported at the bottom of domain/__init__.py (from .jobinfo import *). Helpers from domain are
imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

__all__ = [
    "CLIENT_TYPES", "client_type_role", "client_type_of", "START_BACK_DAYS", "START_AHEAD_DAYS", "parse_start_date",
    "job_started_on", "JOB_STAGES", "job_stage", "job_key_dates", "payer_jobs", "payer_extras",
]

# Client type (J2): five kinds of client, each mapped onto the two sets of defaults the app
# already has (the paperwork a new client starts with, follow-up and approval days). The payer
# keeps role "GC" or "Owner" as before; client_type is the finer word shown on the page.
CLIENT_TYPES: dict[str, tuple[str, str]] = {
    "gc": ("General contractor", "GC"),
    "owner": ("Owner or developer", "Owner"),
    "homeowner": ("Homeowner", "Owner"),
    "sub": ("Another subcontractor (you are a lower tier)", "GC"),
    "public": ("Public agency", "Owner"),
}


def client_type_role(client_type: Any) -> str | None:
    """"GC" or "Owner" for a client type, or None when it isn't one."""
    entry = CLIENT_TYPES.get(str(client_type or ""))
    return entry[1] if entry else None


def client_type_of(payer: dict[str, Any]) -> str:
    """The payer's client type; one saved before there were types reads from its role."""
    saved = payer.get("client_type")
    if saved in CLIENT_TYPES:
        return saved
    return "gc" if payer.get("role") == "GC" else "owner"


# The job's start date (J8): a real day, no more than about 10 years back or 2 years ahead.
START_BACK_DAYS = 3653
START_AHEAD_DAYS = 731
START_MESSAGE = "Enter a start date in the last 10 years or the next 2, like 2026-10-30."


def parse_start_date(value: Any, today: date | None = None) -> str:
    """The start date as YYYY-MM-DD, or a GateError on the started_on box."""
    from domain import GateError
    text = str(value or "").strip()
    try:
        day = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise GateError("Job not saved.", fields={"started_on": START_MESSAGE})
    today = today or datetime.now(timezone.utc).date()
    if not today - timedelta(days=START_BACK_DAYS) <= day <= today + timedelta(days=START_AHEAD_DAYS):
        raise GateError("Job not saved.", fields={"started_on": START_MESSAGE})
    return day.isoformat()


def job_started_on(state: dict[str, Any], job: dict[str, Any]) -> str:
    """The saved start date, or for a job made before there was one, the day it was created
    (its create_job row in the activity log). Shown, not saved, until someone edits it."""
    if job.get("started_on"):
        return str(job["started_on"])
    row = next((a for a in state.get("audit", []) if a.get("job_id") == job.get("id") and a.get("action") == "create_job"), None)
    return str(row.get("at", ""))[:10] if row else ""


# J7: the stages a job moves through, in order, as the job page's line shows them.
JOB_STAGES = (
    ("started", "Started"),
    ("in_progress", "Work in progress"),
    ("payapp_out", "Pay app out"),
    ("approved", "Approved"),
    ("paid", "Paid"),
    ("work_done", "Work done"),
    ("retainage_released", "Retainage released"),
    ("finished", "Finished"),
)


def job_stage(job: dict[str, Any], money: dict[str, int] | None = None) -> dict[str, Any]:
    """{key, label, pct_billed, steps: [{key, label, state: done|current|next}]}: where the job is,
    from its work status, its pay apps' statuses and the retainage still open."""
    from domain import job_money
    m = money or job_money(job)
    live = [i for i in job.get("invoices", []) if i.get("status") != "void"]
    statuses = {i.get("status") for i in live}
    if job.get("closed"):
        key = "finished"
    elif job.get("work_status") == "complete":
        settled = m["outstanding_cents"] == 0 and m["unbilled_completed_cents"] == 0 and not statuses & {"draft", "awaiting_approval"}
        key = "retainage_released" if settled and m["retainage_held_cents"] > 0 and m["retainage_open_cents"] == 0 else "work_done"
    elif statuses & {"sent", "partial"}:
        key = "payapp_out"
    elif "approved" in statuses:
        key = "approved"
    elif "paid" in statuses:
        key = "paid"
    elif live or job.get("completed_value_cents"):
        key = "in_progress"
    else:
        key = "started"
    total = m["contract_total_cents"]
    billed = m["billed_cents"] + m["retainage_held_cents"]
    pct = round(billed * 100 / total) if total > 0 else 0
    at = [k for k, _ in JOB_STAGES].index(key)
    steps = [{"key": k, "label": label, "state": "done" if n < at else "current" if n == at else "next"}
             for n, (k, label) in enumerate(JOB_STAGES)]
    return {"key": key, "label": dict(JOB_STAGES)[key], "pct_billed": max(0, min(100, pct)), "steps": steps}


def _cutoff(day: int, year: int, month: int) -> date:
    last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day, last))


def _day(value: Any) -> str:
    return str(value or "")[:10]


def job_key_dates(state: dict[str, Any], job: dict[str, Any], payer: dict[str, Any] | None = None,
                  today: date | None = None) -> list[dict[str, str]]:
    """The job's dates that matter, oldest rule first ([{key, label, date}], dates as YYYY-MM-DD):
    its start, the client's pay app cutoffs this month and next (payapp_due_day), the last pay app
    sent, when payment is due and the next follow-up, the day it was marked done, the retainage
    asks, and the day it was finished. Only the ones that have a date are listed."""
    from domain import _int_or, follow_up_due, OPEN_FOR_PAYMENT, job_money
    today = today or datetime.now(timezone.utc).date()
    payer = payer if payer is not None else next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    out: list[dict[str, str]] = []

    def add(key: str, label: str, value: Any) -> None:
        if value:
            out.append({"key": key, "label": label, "date": _day(value)})

    add("started", "Started", job_started_on(state, job))
    day = _int_or(payer.get("payapp_due_day"), 0)
    if 1 <= day <= 31 and not job.get("closed") and job.get("work_status") != "complete":
        add("cutoff_this", "Pay app due this month", _cutoff(day, today.year, today.month).isoformat())
        nxt = today.month % 12 + 1
        add("cutoff_next", "Pay app due next month", _cutoff(day, today.year + (today.month == 12), nxt).isoformat())
    live = [i for i in job.get("invoices", []) if i.get("status") != "void"]
    sent = sorted(_day(i.get("sent_at")) for i in live if i.get("sent_at"))
    add("last_sent", "Last pay app sent", sent[-1] if sent else "")
    unpaid = [i for i in live if i.get("status") in OPEN_FOR_PAYMENT]
    due = sorted(_day(i.get("due_on")) for i in unpaid if i.get("due_on"))
    add("payment_due", "Payment due", due[0] if due else "")
    follow = follow_up_due(job, payer) if unpaid and not job.get("closed") else None
    add("follow_up", "Next follow-up", follow.date().isoformat() if follow else "")
    add("done", "Marked done", job.get("completed_at"))
    asked = job.get("last_retainage_chase_at")
    add("retainage_asked", "Retainage asked", asked)
    if asked and not job.get("closed") and job_money(job)["retainage_open_cents"] > 0:
        try:
            again = datetime.fromisoformat(str(asked)).date() + timedelta(days=_int_or(payer.get("follow_up_days"), 7))
            add("retainage_next", "Ask for retainage again from", again.isoformat())
        except ValueError:
            pass
    add("finished", "Finished", job.get("closed_at"))
    return out


def payer_jobs(state: dict[str, Any], payer_id: str) -> list[dict[str, Any]]:
    """Every job of a client, finished ones too (S10), for the Client playbook: the newest
    contract record's file, if any, so the card can link to it."""
    from domain import job_money
    out = []
    for job in state.get("jobs", []):
        if job.get("payer_id") != payer_id:
            continue
        contract = next((r for r in reversed(job.get("records", [])) if r.get("kind") == "contract" and r.get("file_ids")), None)
        out.append({
            "id": job["id"], "name": job.get("name", ""), "code": job.get("code", ""), "closed": bool(job.get("closed")),
            "contract_number": job.get("contract_number", ""), "contract_total_cents": job_money(job)["contract_total_cents"],
            "contract_record_id": contract["id"] if contract else "",
            "contract_file_id": contract["file_ids"][-1] if contract else "",
        })
    return out


def payer_extras(state: dict[str, Any], payer: dict[str, Any]) -> None:
    """What the Client playbook reads on each client besides its saved fields: its client type,
    and its paperwork list read the way the server reads it (S11). Its jobs, finished ones too,
    come from GET /api/payers/<id>/jobs when its card is opened, so the page load stays the
    size of the open work however many jobs a client had."""
    from domain import parse_packet_text
    payer["client_type"] = client_type_of(payer)
    payer["packet_items"] = [] if payer.get("no_paperwork") else parse_packet_text(payer.get("packet") or "")
