"""Bounces and complaints from the mail provider (P1-4).

A wrong AP address used to look exactly like a delivered pay app. The provider calls
POST /api/mail-events/<MAIL_EVENTS_TOKEN> when an email bounces or is marked as spam;
the Outbox row for that address turns "bounced: <reason>", and the job's status line
says so, so the contractor fixes the address in the Client playbook and sends again.

Accepted bodies (the formats are from the providers' documentation as remembered, not
checked against a live account; check them when the provider is chosen):
  - Postmark bounce or spam-complaint webhook: {"RecordType": "Bounce" | "SpamComplaint",
    "Email": ..., "Description": ..., "Type": ..., "Subject": ...}; out-of-office replies,
    mail still being retried and the like (NOT_BOUNCES) are ignored. Its "MessageID" is
    Postmark's own ID, not the Message-ID header we sent, so it is not used
  - SendGrid event webhook: a list of {"event": "bounce" | "dropped" | "spamreport",
    "email": ..., "reason": ..., "smtp-id": <the Message-ID>}; other events are ignored
  - Plain: {"email": ..., "reason": ..., "message_id": ..., "subject": ...}

An event marks one email: the one with its Message-ID (kept on the Outbox row when it was
sent), or else the newest delivered email to that address with that subject, or with no
subject the newest delivered email to that address in any company (the server picks it);
never one that started going out after the bounce happened ("BouncedAt", "timestamp" or
"at"), allowing CLOCK_SLACK for the provider's clock.
An event posted again finds the same email, already bounced, and changes nothing.

Delivered and opened (Siteline comparison, item 5), from the providers' documentation, checked
Nov 27, 2026 (docs/setup-integrations.md says how to turn them on):
  - Postmark Delivery webhook: {"RecordType": "Delivery", "Recipient", "DeliveredAt",
    "MessageID", "Metadata", ...}; Open webhook: {"RecordType": "Open", "Recipient",
    "ReceivedAt", "FirstOpen", "MessageID", "Metadata", ...}
    (postmarkapp.com/developer/webhooks/delivery-webhook, .../open-tracking-webhook). Our own
    Message-ID comes back in Metadata["duracash-message-id"] (mailer._provider_headers).
  - SendGrid event webhook: {"event": "delivered" | "open", "email", "timestamp", "smtp-id"
    (delivered only), "duracash_message_id" (our unique_args), ...}
    (twilio.com/docs/sendgrid/for-developers/tracking-events/event)
  - Plain: {"email", "status": "delivered" | "opened", "message_id", "subject", "at"}
Each marks that address's line on the email (delivery.py): an event for a copy (Cc) address
finds the email through its Cc list. A bounce for a copy address marks only that line; the email
stays "sent", since its To address got it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import delivery
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
            at = item.get("BouncedAt")  # its "MessageID" is Postmark's own, never our Message-ID header
            message_id = _ours(item)    # ours comes back in its Metadata (mailer._provider_headers)
        elif "event" in item:  # SendGrid
            if str(item.get("event", "")).lower() not in FAIL_EVENTS:
                continue
            email = item.get("email")
            reason = "marked as spam" if item.get("event") == "spamreport" else (item.get("reason") or item.get("event"))
            message_id, at = _ours(item) or item.get("smtp-id"), item.get("timestamp")
        else:
            if str(item.get("status", "")).lower() in STATUS_EVENTS:
                continue   # a plain delivered or opened event (normalize_status)
            email, reason = item.get("email"), item.get("reason") or "bounced"
            message_id, subject, at = item.get("message_id"), item.get("subject"), item.get("at") or item.get("timestamp")
        if isinstance(email, str) and "@" in email:
            failure = {"email": email.strip().lower()[:254], "reason": " ".join(str(reason).split())[:200],
                       "message_id": _message_id(message_id), "subject": _subject(subject)}
            if _at(at):
                failure["at"] = _at(at)
            out.append(failure)
    return out


STATUS_EVENTS = {"delivery": "delivered", "delivered": "delivered", "open": "opened", "opened": "opened"}


def _ours(item: dict[str, Any]) -> Any:
    """Our Message-ID as the provider handed it back (mailer._provider_headers), or None."""
    meta = item.get("Metadata")
    if isinstance(meta, dict) and meta.get("duracash-message-id"):
        return meta["duracash-message-id"]
    return item.get("duracash_message_id")


def normalize_status(body: Any) -> list[dict[str, str]]:
    """[{"status": "delivered" | "opened", "email", "message_id", "subject", "at"?}] for every
    delivery or open event in a webhook body; ignores everything else (normalize() has the
    failures)."""
    items = body if isinstance(body, list) else [body]
    out = []
    for item in items[:500]:
        if not isinstance(item, dict):
            continue
        message_id, subject, at = "", "", ""
        if "RecordType" in item:  # Postmark
            status = STATUS_EVENTS.get(str(item.get("RecordType", "")).lower())
            if status not in {"delivered", "opened"} or str(item.get("RecordType")) not in {"Delivery", "Open"}:
                continue
            email, message_id = item.get("Recipient"), _ours(item)
            at = item.get("DeliveredAt") if status == "delivered" else item.get("ReceivedAt")
        elif "event" in item:  # SendGrid
            status = {"delivered": "delivered", "open": "opened"}.get(str(item.get("event", "")).lower())
            if not status:
                continue
            email, message_id, at = item.get("email"), _ours(item) or item.get("smtp-id"), item.get("timestamp")
        else:
            status = STATUS_EVENTS.get(str(item.get("status", "")).lower())
            if not status:
                continue
            email, message_id, subject, at = item.get("email"), item.get("message_id"), item.get("subject"), item.get("at") or item.get("timestamp")
        if isinstance(email, str) and "@" in email:
            event = {"status": status, "email": email.strip().lower()[:254], "message_id": _message_id(message_id), "subject": _subject(subject)}
            if _at(at):
                event["at"] = _at(at)
            out.append(event)
    return out


def _addressed(entry: dict[str, Any], email: str) -> bool:
    return any(a.lower() == email for a, _ in delivery.addresses(entry))


def find_any(state: dict[str, Any], email: str, message_id: str = "", subject: str = "", before: str = "") -> dict[str, Any] | None:
    """As find(), for an event about any address the email went to (To or Cc): the email with
    that Message-ID (its own, or the one its copy to that address had); else the newest sent to
    that address, with that subject when the event names one, sent no later than the event."""
    delivered = [e for e in reversed(state.get("email_log", []))
                 if e.get("status") == "sent" or str(e.get("status", "")).startswith("bounced")]
    if message_id:
        for e in delivered:
            ids = {_message_id(e.get("message_id"))} | {_message_id(r.get("message_id")) for r in e.get("recipients") or []}
            if message_id in ids - {""} and _addressed(e, email):
                return e
    return next((e for e in delivered if not (message_id and e.get("message_id")) and _addressed(e, email)
                 and (not subject or _subject(e.get("subject")) == subject)
                 and (not before or not _went_out(e) or _went_out(e) <= _later(before, CLOCK_SLACK))), None)


def record_status(state: dict[str, Any], email: str, status: str, at: str, message_id: str = "", subject: str = "",
                  before: str = "") -> dict[str, Any] | None:
    """Mark one address of the email this event is about delivered or opened. Returns the
    Outbox row, or None when there is none or nothing changed. Nothing goes in the activity
    log: these are frequent, and the Outbox and the pay app's page show them."""
    entry = find_any(state, email, message_id, subject, before)
    if not entry or not delivery.mark(entry, email, status, at or ""):
        return None
    return entry


def _message_id(value: Any) -> str:
    return str(value).strip().strip("<>").strip()[:300] if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _subject(value: Any) -> str:
    return " ".join(value.split())[:250].lower() if isinstance(value, str) else ""


def find(state: dict[str, Any], email: str, message_id: str = "", subject: str = "", before: str = "") -> dict[str, Any] | None:
    """The delivered email (sent, or already bounced) this event is about, or None: the one with
    its Message-ID; else the newest to that address, with that subject when the event names one,
    that started going out no later than the bounce (`before`, when the provider says when). An email that has
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
                 and (not before or not _went_out(e) or _went_out(e) <= _later(before, CLOCK_SLACK))), None)


def when_delivered(entry: dict[str, Any]) -> str:
    return str(entry.get("delivered_at") or entry.get("at") or "")


# The provider's clock may be a little behind ours.
CLOCK_SLACK = timedelta(minutes=2)


def _went_out(entry: dict[str, Any]) -> str:
    """When the email started going out. delivered_at is written after the whole batch is sent,
    so an email can bounce before its own delivered_at; it can't bounce before sending_at."""
    return str(entry.get("sending_at") or when_delivered(entry))


def _later(at: str, by: timedelta) -> str:
    try:
        return (datetime.fromisoformat(at) + by).isoformat(timespec="seconds")
    except ValueError:
        return at


def apply(state: dict[str, Any], email: str, reason: str, now: str, message_id: str = "", subject: str = "",
          before: str = "") -> dict[str, Any] | None:
    """Mark the email this event is about (find()) as bounced. Returns the Outbox row, or None
    when there is none or it is already marked (the provider sent the event again)."""
    entry = find(state, email, message_id, subject, before)
    if not entry or str(entry.get("to", "")).strip().lower() != email:
        copy = entry if entry and _addressed(entry, email) else find_any(state, email, message_id, subject, before)
        if copy and str(copy.get("to", "")).strip().lower() != email and delivery.mark(copy, email, "bounced", now, reason):
            domain.audit(state, copy.get("job_id", ""), "email_bounced",
                         f"The copy of \"{copy.get('subject', '')}\" to {email} bounced: {reason}. {copy.get('to', '')} got it.", "duracash")
            return copy
        return None
    if entry.get("status") != "sent":
        return None
    delivery.mark(entry, email, "bounced", now, reason)
    entry["status"] = f"bounced: {reason}"
    entry["bounced_at"] = now
    job = next((j for j in state.get("jobs", []) if j["id"] == entry.get("job_id")), None)
    if job and not job.get("closed"):
        job["external_status"] = f"Email to {email} bounced ({reason}). Fix the address in the Client playbook and send again."
    domain.audit(state, entry.get("job_id", ""), "email_bounced", f"The email \"{entry.get('subject', '')}\" to {email} bounced: {reason}.", "duracash")
    return entry
