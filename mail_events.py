"""Bounces and complaints from the mail provider (P1-4).

A wrong AP address used to look exactly like a delivered pay app. The provider calls
POST /api/mail-events/<MAIL_EVENTS_TOKEN> when an email bounces or is marked as spam;
the Outbox row for that address turns "bounced: <reason>", and the job's status line
says so, so the contractor fixes the address in Customer settings and sends again.

Accepted bodies (the formats are from the providers' documentation as remembered, not
checked against a live account; check them when the provider is chosen):
  - Postmark bounce or spam-complaint webhook: {"RecordType": "Bounce" | "SpamComplaint",
    "Email": ..., "Description": ..., "Type": ...}
  - SendGrid event webhook: a list of {"event": "bounce" | "dropped" | "spamreport",
    "email": ..., "reason": ...}; other events are ignored
  - Plain: {"email": ..., "reason": ..., "message_id": ..., "at": ...}

Each failure marks one email, never one in every company that wrote to the address: the email
whose Message-ID the event names (SendGrid's "smtp-id"), or else the one delivered last to that
address before the event happened ("BouncedAt", "timestamp" or "at", when the provider sends it).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import domain


def _at(value: Any) -> str:
    """The event's time as UTC ISO seconds (as delivered_at is written), or "" when it can't be read."""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool) or (isinstance(value, str) and value.strip().isdigit()):
            when = datetime.fromtimestamp(int(value), timezone.utc)
        elif isinstance(value, str):
            m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)?", value.strip())
            if not m:
                return ""
            zone = m.group(2) if m.group(2) and m.group(2) != "Z" else "+00:00"
            when = datetime.fromisoformat(m.group(1) + zone).astimezone(timezone.utc)
        else:
            return ""
    except (ValueError, OverflowError, OSError):
        return ""
    return when.isoformat(timespec="seconds")


def _message_id(value: Any) -> str:
    return str(value).strip().strip("<>").strip()[:300] if isinstance(value, (str, int)) and not isinstance(value, bool) else ""

FAIL_EVENTS = {"bounce", "dropped", "spamreport", "spamcomplaint", "hardbounce", "softbounce"}


def normalize(body: Any) -> list[dict[str, str]]:
    """[{"email", "reason"}] for every failure in a webhook body; ignores everything else."""
    items = body if isinstance(body, list) else [body]
    out = []
    for item in items[:500]:
        if not isinstance(item, dict):
            continue
        if "RecordType" in item:  # Postmark
            kind = str(item.get("RecordType", "")).lower()
            if kind not in {"bounce", "spamcomplaint"}:
                continue
            email = item.get("Email")
            reason = "marked as spam" if kind == "spamcomplaint" else (item.get("Description") or item.get("Type") or "bounced")
            message_id, at = item.get("MessageID"), item.get("BouncedAt")
        elif "event" in item:  # SendGrid
            if str(item.get("event", "")).lower() not in FAIL_EVENTS:
                continue
            email = item.get("email")
            reason = "marked as spam" if item.get("event") == "spamreport" else (item.get("reason") or item.get("event"))
            message_id, at = item.get("smtp-id"), item.get("timestamp")
        else:
            email, reason = item.get("email"), item.get("reason") or "bounced"
            message_id, at = item.get("message_id"), item.get("at") or item.get("timestamp")
        if isinstance(email, str) and "@" in email:
            failure = {"email": email.strip().lower()[:254], "reason": " ".join(str(reason).split())[:200]}
            if _message_id(message_id):
                failure["message_id"] = _message_id(message_id)
            if _at(at):
                failure["at"] = _at(at)
            out.append(failure)
    return out


def find(state: dict[str, Any], failure: dict[str, str]) -> tuple[tuple, dict[str, Any]] | None:
    """(rank, Outbox row) of the email in this company the failure is about, or None. The server
    marks only the best-ranked row across all companies: a Message-ID match first, then the latest
    delivered. The row may be bounced already (a replayed event), which then marks nothing."""
    email, mid, before = failure["email"], failure.get("message_id", ""), failure.get("at", "")
    rows = [e for e in state.get("email_log", [])
            if str(e.get("to", "")).lower() == email and (e.get("status") == "sent" or str(e.get("status", "")).startswith("bounced"))]
    if mid:
        named = [e for e in rows if _message_id(e.get("message_id")) == mid]
        if named:
            return (1, named[-1].get("delivered_at") or named[-1].get("at") or ""), named[-1]
    best = None
    for e in rows:
        when = e.get("delivered_at") or e.get("at") or ""
        if before and when > before:
            continue  # sent after the bounce happened: not the one that bounced
        if best is None or when >= best[0][1]:
            best = ((0, when), e)
    return best


def apply(state: dict[str, Any], email: str, reason: str, now: str, entry_id: str | None = None) -> dict[str, Any] | None:
    """Mark the Outbox row `entry_id` (by default the most recent sent email to `email`) as bounced.
    Returns the row, or None when it isn't a sent email to that address."""
    entry = next((e for e in reversed(state.get("email_log", []))
                  if e.get("status") == "sent" and str(e.get("to", "")).lower() == email
                  and (entry_id is None or e.get("id") == entry_id)), None)
    if not entry:
        return None
    entry["status"] = f"bounced: {reason}"
    entry["bounced_at"] = now
    job = next((j for j in state.get("jobs", []) if j["id"] == entry.get("job_id")), None)
    if job and not job.get("closed"):
        job["external_status"] = f"Email to {email} bounced ({reason}). Fix the address in Customer settings and send again."
    domain.audit(state, entry.get("job_id", ""), "email_bounced", f"The email \"{entry.get('subject', '')}\" to {email} bounced: {reason}.", "duracash")
    return entry
