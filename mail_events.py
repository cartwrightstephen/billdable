"""Bounces and complaints from the mail provider (P1-4).

A wrong AP address used to look exactly like a delivered pay app. The provider calls
POST /api/mail-events/<MAIL_EVENTS_TOKEN> when an email bounces or is marked as spam;
the Outbox row for that address turns "bounced: <reason>", and the job's status line
says so, so the contractor fixes the address in the Client playbook and sends again.

Accepted bodies (the formats are from the providers' documentation as remembered, not
checked against a live account; check them when the provider is chosen):
  - Postmark bounce or spam-complaint webhook: {"RecordType": "Bounce" | "SpamComplaint",
    "Email": ..., "Description": ..., "Type": ..., "Subject": ...}; out-of-office replies,
    mail still being retried and the like (NOT_BOUNCES) are ignored
  - SendGrid event webhook: a list of {"event": "bounce" | "dropped" | "spamreport",
    "email": ..., "reason": ..., "smtp-id": <the Message-ID>}; other events are ignored
  - Plain: {"email": ..., "reason": ..., "message_id": ..., "subject": ...}

An event marks one email: the one with its Message-ID (kept on the Outbox row when it was
sent), or else the newest delivered email to that address with that subject, or with no
subject the newest delivered email to that address in any company (the server picks it);
never one delivered after the bounce happened ("BouncedAt", "timestamp" or "at").
An event posted again finds the same email, already bounced, and changes nothing.
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


FAIL_EVENTS = {"bounce", "dropped", "spamreport", "spamcomplaint", "hardbounce", "softbounce"}
# Postmark sends these on its Bounce webhook too, but none means the address is wrong: an
# out-of-office reply, mail still being retried, or a notice about mail that was delivered.
NOT_BOUNCES = {"autoresponder", "transient", "addresschange", "subscribe", "unsubscribe", "challengeverification",
               "spamnotification", "virusnotification", "openrelaytest"}


def normalize(body: Any) -> list[dict[str, str]]:
    """[{"email", "reason", "message_id", "subject", "at"?}] for every failure in a webhook body;
    ignores everything else. "at" (when the bounce happened, UTC) is there only when it can be read."""
    items = body if isinstance(body, list) else [body]
    out = []
    for item in items[:500]:
        if not isinstance(item, dict):
            continue
        message_id, subject, at = "", "", ""
        if "RecordType" in item:  # Postmark
            kind = str(item.get("RecordType", "")).lower()
            if kind not in {"bounce", "spamcomplaint"} or str(item.get("Type", "")).lower() in NOT_BOUNCES:
                continue
            email, subject = item.get("Email"), item.get("Subject")
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
            message_id, subject, at = item.get("message_id"), item.get("subject"), item.get("at") or item.get("timestamp")
        if isinstance(email, str) and "@" in email:
            failure = {"email": email.strip().lower()[:254], "reason": " ".join(str(reason).split())[:200],
                       "message_id": _message_id(message_id), "subject": _subject(subject)}
            if _at(at):
                failure["at"] = _at(at)
            out.append(failure)
    return out


def _message_id(value: Any) -> str:
    return str(value).strip().strip("<>").strip()[:300] if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _subject(value: Any) -> str:
    return " ".join(value.split())[:250].lower() if isinstance(value, str) else ""


def find(state: dict[str, Any], email: str, message_id: str = "", subject: str = "", before: str = "") -> dict[str, Any] | None:
    """The delivered email (sent, or already bounced) this event is about, or None: the one with
    its Message-ID; else the newest to that address, with that subject when the event names one,
    delivered no later than the bounce (`before`, when the provider says when). An email that has
    a Message-ID of its own is never taken for an event naming another. An email sent before
    Message-IDs were kept is found by its address, as before."""
    delivered = [e for e in reversed(state.get("email_log", []))
                 if e.get("status") == "sent" or str(e.get("status", "")).startswith("bounced")]
    if message_id:
        exact = next((e for e in delivered if _message_id(e.get("message_id")) == message_id), None)
        if exact:
            return exact
    return next((e for e in delivered if not (message_id and e.get("message_id")) and str(e.get("to", "")).lower() == email
                 and (not subject or _subject(e.get("subject")) == subject)
                 and (not before or not when_delivered(e) or when_delivered(e) <= before)), None)


def when_delivered(entry: dict[str, Any]) -> str:
    return str(entry.get("delivered_at") or entry.get("at") or "")


def apply(state: dict[str, Any], email: str, reason: str, now: str, message_id: str = "", subject: str = "",
          before: str = "") -> dict[str, Any] | None:
    """Mark the email this event is about (find()) as bounced. Returns the Outbox row, or None
    when there is none or it is already marked (the provider sent the event again)."""
    entry = find(state, email, message_id, subject, before)
    if not entry or entry.get("status") != "sent":
        return None
    entry["status"] = f"bounced: {reason}"
    entry["bounced_at"] = now
    job = next((j for j in state.get("jobs", []) if j["id"] == entry.get("job_id")), None)
    if job and not job.get("closed"):
        job["external_status"] = f"Email to {email} bounced ({reason}). Fix the address in the Client playbook and send again."
    domain.audit(state, entry.get("job_id", ""), "email_bounced", f"The email \"{entry.get('subject', '')}\" to {email} bounced: {reason}.", "duracash")
    return entry
