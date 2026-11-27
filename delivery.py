"""What became of an email at each address it went to (Siteline comparison, item 5).

An Outbox row (state["email_log"]) is one email: one To address and up to five copy (Cc)
addresses. Each address gets its own line in entry["recipients"] when the email goes out:

    {"email": "ap@builder.com", "role": "to" | "cc", "status": "sent" | "delivered" | "opened"
     | "bounced" | "failed", "sent_at", "delivered_at"?, "opened_at"?, "bounced_at"?, "reason"?,
     "message_id"?, "token"?}

"sent" is our mail server accepting it; "delivered" and "bounced" come from the mail provider's
webhook (mail_events.py); "opened" from the provider's open event or our own tracking image
(the company's "open_tracking" setting, off unless an owner turns it on). A status only moves
forward (sent, delivered, opened), except that a bounce or failure is always kept.

"token" is the random key in the tracking image's address. It is a secret, so public() takes it
off every row that goes to a page.

No other module is imported here, so domain, outbox and mail_events can all use it.
"""

from __future__ import annotations

import html
import secrets
from typing import Any

ORDER = {"sent": 1, "delivered": 2, "opened": 3}
FAILED = {"bounced", "failed"}
# The 1x1 transparent GIF the tracking address answers with.
PIXEL = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
         b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


def _norm(address: Any) -> str:
    return str(address or "").strip().lower()


def addresses(entry: dict[str, Any]) -> list[tuple[str, str]]:
    """[(address, "to" | "cc")] the email is meant for, To first, without repeats."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for address, role in [(entry.get("to"), "to"), *[(a, "cc") for a in entry.get("cc") or []]]:
        a = str(address or "").strip()
        if a and _norm(a) not in seen:
            seen.add(_norm(a))
            out.append((a, role))
    return out


def new_token() -> str:
    return secrets.token_urlsafe(18)


def start(entry: dict[str, Any], at: str, refused: list[str] | None = None, tokens: dict[str, str] | None = None,
          message_ids: dict[str, str] | None = None) -> None:
    """The email just went out: a line per address, "sent", or "failed" for a copy address the
    mail server refused or held back (refused: "addr (why)" strings, as outbox keeps them)."""
    refused_by = {}
    for r in refused or []:
        address, _, why = str(r).partition(" (")
        refused_by[_norm(address)] = why.rstrip(")") or "refused by the mail server"
    rows = []
    for address, role in addresses(entry):
        row: dict[str, Any] = {"email": address, "role": role, "sent_at": at}
        if _norm(address) in refused_by:
            row.update(status="failed", reason=refused_by[_norm(address)][:200])
        else:
            row["status"] = "sent"
        if tokens and _norm(address) in tokens:
            row["token"] = tokens[_norm(address)]
        if message_ids and _norm(address) in message_ids:
            row["message_id"] = message_ids[_norm(address)]
        rows.append(row)
    entry["recipients"] = rows


def row_for(entry: dict[str, Any], address: str) -> dict[str, Any] | None:
    return next((r for r in entry.get("recipients") or [] if _norm(r.get("email")) == _norm(address)), None)


def mark(entry: dict[str, Any], address: str, status: str, at: str, reason: str = "") -> bool:
    """Move one address on: "delivered", "opened", "bounced" or "failed". Returns whether
    anything changed (an event sent twice changes nothing)."""
    row = row_for(entry, address)
    if row is None:
        if not any(_norm(a) == _norm(address) for a, _ in addresses(entry)):
            return False
        # An email sent before per-address lines were kept: its line starts now.
        role = "to" if _norm(entry.get("to")) == _norm(address) else "cc"
        row = {"email": address, "role": role, "status": "sent", "sent_at": entry.get("delivered_at") or entry.get("at") or ""}
        entry.setdefault("recipients", []).append(row)
    if status in FAILED:
        if row.get("status") in FAILED:
            return False
        row.update(status=status, bounced_at=at, reason=" ".join(str(reason).split())[:200])
        return True
    if status not in ORDER or row.get("status") in FAILED:
        return False
    changed = False
    key = f"{status}_at"
    if not row.get(key):
        row[key] = at
        changed = True
    if ORDER.get(row.get("status"), 0) < ORDER[status]:
        row["status"] = status
        changed = True
    if status == "opened" and not row.get("delivered_at"):
        row["delivered_at"] = at   # read means it arrived
    return changed


def find_token(state: dict[str, Any], token: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(Outbox row, address line) the tracking image's token belongs to, or (None, None)."""
    if not isinstance(token, str) or not token:
        return None, None
    for entry in reversed(state.get("email_log", [])):
        for row in entry.get("recipients") or []:
            stored = row.get("token")
            if stored and secrets.compare_digest(str(stored), token):
                return entry, row
    return None, None


def public(entry: dict[str, Any]) -> dict[str, Any]:
    """The row as a page may see it: the same dict, its tracking tokens taken off its lines."""
    if entry.get("recipients"):
        entry["recipients"] = [{k: v for k, v in r.items() if k != "token"} for r in entry["recipients"]]
    return entry


def html_body(text: str, pixel_url: str = "") -> str:
    """The plain-text email as simple HTML, for the tracking image to ride in. The text is the
    same; only the image is added."""
    paragraphs = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in str(text or "").split("\n\n") if p.strip())
    image = f'<img src="{html.escape(pixel_url, quote=True)}" width="1" height="1" alt="" style="display:block;border:0">' if pixel_url else ""
    return f'<!doctype html><html><body style="font-family:Arial,sans-serif;font-size:14px;line-height:1.45">{paragraphs}{image}</body></html>'
