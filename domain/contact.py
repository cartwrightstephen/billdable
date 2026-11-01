"""Recent contact with a job's client (Stephen's Oct 29 review, M11, the payapps-cos stream): the
emails that went to them, the calls noted, what they did on the client page and the emails from
them filed to the job, newest first. Money > Unpaid shows the last few under each job.

Imported at the bottom of domain/__init__.py (from .contact import *). Helpers from domain
are imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["CONTACT_EMAIL_WORDS", "RECENT_CONTACT", "contact_log"]

# What each email to the client is called in the list.
CONTACT_EMAIL_WORDS = {
    "pay_app": "Pay app emailed", "follow_up": "Follow-up emailed", "waiver": "Lien waiver emailed",
    "retainage": "Retainage request emailed", "short_pay": "Short pay emailed", "pay_app_withdrawn": "Pulled-back notice emailed",
    "co_signature": "Change order sent to sign", "pencil": "Draft sent for their review",
}
# The client-page events in the audit trail.
CONTACT_PAGE_WORDS = {"payer_received": "Marked received on the client page", "payer_approved": "Approved on the client page",
                      "payer_question": "Asked a question on the client page"}
RECENT_CONTACT = 5
_ACTOR_TAIL = re.compile(r"\s*\(([^()]*)\)\s*$")
# The audit row says where it happened; the label already does.
_PAGE_WORDS = re.compile(r" on the (?:payer|customer|client) page", re.I)


def _email_status(entry: dict[str, Any]) -> str:
    """How the email went, in a word or two: Delivered once the mail service says so, else Sent."""
    status = str(entry.get("status") or "")
    if status == "sent":
        return "Delivered" if entry.get("delivered_at") else "Sent"
    if status.startswith("bounced"):
        return "Bounced"
    if status.startswith("failed"):
        return "Didn't go out"
    if status in ("queued", "sending"):
        return "Waiting to send"
    return "Not sent" if status else ""


def contact_log(state: dict[str, Any], job_id: str, limit: int | None = RECENT_CONTACT) -> tuple[list[dict[str, Any]], int]:
    """(the newest `limit` contacts, how many there are). Each is {at, kind, label, text, by, status}:
    kind is "email", "call", "client_page" or "email_in"."""
    from domain import billing_style, term_text
    style = billing_style(state)
    job = next((j for j in state.get("jobs", []) if j.get("id") == job_id), None)
    if not job:
        return [], 0
    out: list[dict[str, Any]] = []
    for e in state.get("email_log", []):
        if e.get("job_id") == job_id and e.get("kind") in CONTACT_EMAIL_WORDS:
            out.append({"at": e.get("at", ""), "kind": "email", "label": term_text(CONTACT_EMAIL_WORDS[e["kind"]], style),
                        "text": e.get("subject", ""), "by": e.get("approved_by", ""), "status": _email_status(e)})
    for row in state.get("audit", []):
        if row.get("job_id") != job_id:
            continue
        action = row.get("action")
        if action == "contact_noted":
            # "Spoke to Sam: Check goes out Friday Next check 2026-11-07. (Pat)"
            text = _ACTOR_TAIL.sub("", str(row.get("detail", "")))
            out.append({"at": row.get("at", ""), "kind": "call", "label": "Call", "text": text, "by": row.get("actor", ""), "status": ""})
        elif action in CONTACT_PAGE_WORDS:
            out.append({"at": row.get("at", ""), "kind": "client_page", "label": CONTACT_PAGE_WORDS[action],
                        "text": term_text(_PAGE_WORDS.sub("", str(row.get("detail", ""))), style), "by": "", "status": ""})
    payer = next((p for p in state.get("payers", []) if p.get("id") == job.get("payer_id")), {})
    client_email = str(payer.get("ap_email") or "").strip().lower()
    for item in state.get("inbox", []):
        if item.get("status") != "filed" or item.get("filed_job_id") != job_id:
            continue
        sender = str(item.get("from") or "").lower()
        if item.get("source") == "email" and client_email and client_email in sender:
            out.append({"at": item.get("received_at", ""), "kind": "email_in", "label": "Email from the client",
                        "text": item.get("subject", "") or str(item.get("text", ""))[:120], "by": item.get("from", ""), "status": ""})
    out.sort(key=lambda c: str(c["at"]), reverse=True)
    return (out if limit is None else out[:limit]), len(out)
