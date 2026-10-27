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
  - Plain: {"email": ..., "reason": ...}
"""

from __future__ import annotations

from typing import Any

import domain

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
        elif "event" in item:  # SendGrid
            if str(item.get("event", "")).lower() not in FAIL_EVENTS:
                continue
            email = item.get("email")
            reason = "marked as spam" if item.get("event") == "spamreport" else (item.get("reason") or item.get("event"))
        else:
            email, reason = item.get("email"), item.get("reason") or "bounced"
        if isinstance(email, str) and "@" in email:
            out.append({"email": email.strip().lower()[:254], "reason": " ".join(str(reason).split())[:200]})
    return out


def apply(state: dict[str, Any], email: str, reason: str, now: str) -> dict[str, Any] | None:
    """Mark this company's most recent sent email to `email` as bounced. Returns the Outbox row, or None."""
    entry = next((e for e in reversed(state.get("email_log", []))
                  if e.get("status") == "sent" and str(e.get("to", "")).lower() == email), None)
    if not entry:
        return None
    entry["status"] = f"bounced: {reason}"
    entry["bounced_at"] = now
    job = next((j for j in state.get("jobs", []) if j["id"] == entry.get("job_id")), None)
    if job and not job.get("closed"):
        job["external_status"] = f"Email to {email} bounced ({reason}). Fix the address in Customer settings and send again."
    domain.audit(state, entry.get("job_id", ""), "email_bounced", f"The email \"{entry.get('subject', '')}\" to {email} bounced: {reason}.", "duracash")
    return entry
