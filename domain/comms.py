"""Communications and Import/Export (the comms-data stream): every item that came into the
Inbox, paged, filtered and sorted for its "Everything received" list (C1); the page load's share
of the Inbox; and the sent pay apps the Export page lists (I1).

Imported at the bottom of domain/__init__.py (from .comms import *). Helpers from domain
are imported inside each function, so the two modules never import each other at load time.
"""
from __future__ import annotations

from typing import Any

__all__ = ["INBOX_STATUSES", "INBOX_SORTS", "RECENT_INBOX_DONE", "inbox_for_page_load", "inbox_page", "sent_payapps_page"]

# What the Status filter offers: waiting, filed by a person, filed automatically (a reply to a
# follow-up, importer.auto_file_reply), dismissed.
INBOX_STATUSES = ("new", "filed", "auto", "dismissed")
INBOX_SORTS = ("newest", "oldest", "from", "job")
# The page load carries every waiting item and only this many of the rest: the Waiting card and
# Today's rows read the waiting ones, the Undo after a dismiss reads the last few.
RECENT_INBOX_DONE = 20
# The text kept on a row of the list: enough to read and print, not a whole pasted month of notes.
ROW_TEXT_CHARS = 4000


def inbox_for_page_load(inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every waiting item and the last RECENT_INBOX_DONE others, in the order they came in."""
    done = [n for n, i in enumerate(inbox) if i.get("status") != "new"]
    keep = set(done[-RECENT_INBOX_DONE:])
    return [i for n, i in enumerate(inbox) if i.get("status") == "new" or n in keep]


def _status(item: dict[str, Any]) -> str:
    status = item.get("status") or "new"
    return "auto" if status == "filed" and item.get("auto_filed") else status


def _job_of(item: dict[str, Any]) -> str:
    """The job a row belongs to: where it was filed, else (still waiting) the suggested one."""
    if item.get("status") == "filed":
        return item.get("filed_job_id") or ""
    if item.get("status") == "new":
        return item.get("suggested_job_id") or ""
    return ""


def _why(item: dict[str, Any]) -> str:
    """How Duracash matched it, in a sentence: a matched job's reason is a phrase ("mentions job
    code OAK"), anything else is already a sentence ("More than one job fits. Pick one.")."""
    if item.get("auto_filed"):
        return "A reply to a pay app emailed to this sender, so it was filed to that job."
    reason = (item.get("match_reason") or "").strip()
    return f"Suggested because it {reason}." if reason[:1].islower() else reason


def _row(item: dict[str, Any], files: dict[str, dict[str, Any]], names: dict[str, str]) -> dict[str, Any]:
    text = item.get("text") or ""
    own = [files[fid] for fid in item.get("file_ids", []) if fid in files]
    title = item.get("subject") or (own[0]["name"] if own else "") or text.split("\n")[0][:80] or "Untitled"
    job_id = _job_of(item)
    return {
        "id": item["id"],
        "source": item.get("source", ""),
        "from": item.get("from", ""),
        "title": title,
        "received_at": item.get("received_at", ""),
        "sent_at": item.get("sent_at", ""),
        "status": _status(item),
        "kind": item.get("filed_kind") or item.get("kind") or "",
        "job_id": job_id,
        "job_name": names.get(job_id, ""),
        "why": _why(item),
        "text": text[:ROW_TEXT_CHARS],
        "files": [{"id": f["id"], "name": f.get("name", "")} for f in own],
        "files_gone": len(item.get("file_ids", [])) - len(own),
    }


def inbox_page(state: dict[str, Any], offset: int, limit: int, source: str = "", status: str = "",
               job_id: str = "", sort: str = "newest") -> dict[str, Any]:
    """Everything that came into the Inbox, for Communications > Inbox (C1): one page of rows,
    filtered by where it came in, its status and its job, and sorted. Newest first by default."""
    names = {j["id"]: j.get("name", "") for j in state.get("jobs", [])}
    files = {f["id"]: f for f in state.get("files", [])}
    picked = [i for i in reversed(state.get("inbox", []))
              if (not source or i.get("source") == source)
              and (not status or _status(i) == status)
              and (not job_id or _job_of(i) == job_id)]
    if sort == "oldest":
        picked.reverse()
    elif sort == "from":
        # Blank senders (uploads, pastes) last; newest first within one sender (the sort is stable).
        picked.sort(key=lambda i: (not (i.get("from") or "").strip(), (i.get("from") or "").strip().lower()))
    elif sort == "job":
        picked.sort(key=lambda i: (not names.get(_job_of(i)), names.get(_job_of(i), "").lower()))
    rows = [_row(i, files, names) for i in picked[offset:offset + limit]]
    return {"entries": rows, "total": len(picked), "offset": offset, "limit": limit}


def sent_payapps_page(state: dict[str, Any], offset: int, limit: int, job_id: str = "") -> dict[str, Any]:
    """Every pay app that went out, newest first, for the Export page: each downloads as its PDF
    and as the QuickBooks invoice file, closed jobs' too (I1)."""
    from . import WENT_OUT
    rows = []
    for job in state.get("jobs", []):
        if job_id and job["id"] != job_id:
            continue
        for inv in job.get("invoices", []):
            if inv.get("status") in WENT_OUT and inv.get("sent_at"):
                rows.append({"job_id": job["id"], "job": job.get("name", ""), "closed": bool(job.get("closed")),
                             "invoice_id": inv["id"], "number": inv.get("number", ""), "status": inv.get("status", ""),
                             "amount_cents": inv.get("amount_cents", 0), "sent_at": inv.get("sent_at", "")})
    rows.sort(key=lambda r: r["sent_at"], reverse=True)
    return {"entries": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit}
