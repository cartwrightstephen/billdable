"""Email delivery over SMTP.

Postmark, SendGrid, Mailgun, Amazon SES and Google Workspace all accept SMTP,
so one block of settings covers any of them and no extra package is needed.

Settings (environment):

    SMTP_HOST         required to send; when unset, approved emails stay in
                      the Outbox marked "not sent: email delivery is not set up"
    SMTP_PORT         default 587 (465 with SMTP_SECURITY=ssl)
    SMTP_USER         login for the provider, if it needs one
    SMTP_PASSWORD
    SMTP_SECURITY     starttls (default), ssl, or none (none is for local testing)
    MAIL_FROM         sender address on a domain the provider has verified,
                      e.g. billing@billdable.com; required with SMTP_HOST
    MAIL_DAILY_LIMIT  emails per company per 24 hours, default 50

The From name is "<company> via Billdable" and Reply-To is the signed-in user,
so the payer's answer goes to the contractor, not to us.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable

EMAIL_RE = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")
MAX_ATTACH_BYTES = 15 * 1024 * 1024
TIMEOUT_SECONDS = 20
NOT_SET_UP = "not sent: email delivery is not set up"
DEMO = "not sent: demo account"


def settings() -> dict[str, Any]:
    security = (os.environ.get("SMTP_SECURITY") or "starttls").strip().lower()
    default_port = "465" if security == "ssl" else "587"
    try:
        port = int(os.environ.get("SMTP_PORT") or default_port)
    except ValueError:
        port = int(default_port)
    try:
        limit = int(os.environ.get("MAIL_DAILY_LIMIT") or "50")
    except ValueError:
        limit = 50
    return {
        "host": (os.environ.get("SMTP_HOST") or "").strip(),
        "port": port,
        "user": os.environ.get("SMTP_USER") or "",
        "password": os.environ.get("SMTP_PASSWORD") or "",
        "security": security if security in {"starttls", "ssl", "none"} else "starttls",
        "from": (os.environ.get("MAIL_FROM") or "").strip(),
        "daily_limit": max(0, limit),
    }


def configured() -> bool:
    cfg = settings()
    return bool(cfg["host"] and EMAIL_RE.match(cfg["from"]))


def _clean_header(value: str, limit: int = 300) -> str:
    """No line breaks in headers, ever."""
    return re.sub(r"[\r\n\x00]+", " ", str(value or "")).strip()[:limit]


def sent_in_last_day(email_log: list[dict[str, Any]], now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=1)
    count = 0
    for entry in email_log:
        if entry.get("status") != "sent" or not entry.get("delivered_at"):
            continue
        try:
            when = datetime.fromisoformat(entry["delivered_at"])
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            count += 1
    return count


def build_message(
    entry: dict[str, Any],
    company_name: str,
    reply_to: str,
    files: list[tuple[str, Path]],
    cfg: dict[str, Any],
) -> tuple[EmailMessage, list[str]]:
    """Return the message and a list of attachments left off (with the reason)."""
    to = _clean_header(entry.get("to", ""), 254)
    if not EMAIL_RE.match(to):
        raise ValueError(f"the address {to!r} is not a valid email address")
    msg = EmailMessage()
    sender_name = _clean_header(f"{company_name or 'Billdable'} via Billdable", 120)
    local, _, domain = cfg["from"].partition("@")
    msg["From"] = Address(display_name=sender_name, username=local, domain=domain)
    msg["To"] = to
    reply_to = _clean_header(reply_to, 254)
    if reply_to and EMAIL_RE.match(reply_to):
        msg["Reply-To"] = reply_to
    msg["Subject"] = _clean_header(entry.get("subject", ""), 250)
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=domain or None)
    left_off: list[str] = []
    attached: list[tuple[str, bytes]] = []
    total = 0
    for name, path in files:
        try:
            data = path.read_bytes()
        except OSError:
            left_off.append(f"{name} (file missing)")
            continue
        if total + len(data) > MAX_ATTACH_BYTES:
            left_off.append(f"{name} (over the {MAX_ATTACH_BYTES // (1024 * 1024)} MB attachment limit)")
            continue
        total += len(data)
        attached.append((name, data))
    body = str(entry.get("body") or "")
    if left_off:
        body += "\n\nNot attached: " + "; ".join(left_off) + ". Ask us for these if you need them."
    msg.set_content(body)
    for name, data in attached:
        maintype, subtype = _mime(name)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=_clean_header(name, 120))
    return msg, left_off


def _mime(name: str) -> tuple[str, str]:
    ext = Path(name).suffix.lower()
    table = {
        ".pdf": ("application", "pdf"),
        ".jpg": ("image", "jpeg"), ".jpeg": ("image", "jpeg"), ".png": ("image", "png"),
        ".gif": ("image", "gif"), ".webp": ("image", "webp"), ".heic": ("image", "heic"),
        ".csv": ("text", "csv"), ".txt": ("text", "plain"), ".eml": ("message", "rfc822"),
        ".doc": ("application", "msword"),
        ".docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ".xls": ("application", "vnd.ms-excel"),
        ".xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    }
    # .eml is sent as a plain file so it is never re-parsed as a nested message.
    if ext == ".eml":
        return "application", "octet-stream"
    return table.get(ext, ("application", "octet-stream"))


def send(messages: list[EmailMessage], cfg: dict[str, Any], connect: Callable[..., smtplib.SMTP] | None = None) -> list[str | None]:
    """Send over one connection. Returns None per message on success, or the error text."""
    pending = "not attempted"
    results: list[str | None] = [pending] * len(messages)
    if not messages:
        return results
    try:
        if cfg["security"] == "ssl":
            server = (connect or smtplib.SMTP_SSL)(cfg["host"], cfg["port"], timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
        else:
            server = (connect or smtplib.SMTP)(cfg["host"], cfg["port"], timeout=TIMEOUT_SECONDS)
    except (OSError, smtplib.SMTPException) as exc:
        return [f"could not connect to {cfg['host']}:{cfg['port']} ({_short(exc)})"] * len(messages)
    try:
        server.ehlo()
        if cfg["security"] == "starttls":
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        for i, msg in enumerate(messages):
            try:
                refused = server.send_message(msg)
                results[i] = ("refused: " + ", ".join(f"{k} ({v[0]})" for k, v in refused.items())) if refused else None
            except smtplib.SMTPRecipientsRefused as exc:
                results[i] = "recipient refused: " + ", ".join(exc.recipients.keys())
            except (smtplib.SMTPException, OSError) as exc:
                results[i] = _short(exc)
    except (smtplib.SMTPException, OSError) as exc:
        # Hello, STARTTLS or login failed: nothing after that point was sent.
        reason = _short(exc)
        results = [reason if r == pending else r for r in results]
    finally:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass
    return results


def _short(exc: BaseException) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "the email provider refused the login (check SMTP_USER and SMTP_PASSWORD)"
    if isinstance(exc, smtplib.SMTPResponseException):
        text = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        return f"{exc.smtp_code} {text}"[:200]
    return (str(exc) or exc.__class__.__name__)[:200]
