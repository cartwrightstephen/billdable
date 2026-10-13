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

import json
import os
import re
import smtplib
import ssl
import threading
from datetime import datetime, timedelta, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable

# Plain addresses only. Characters such as ( : ] change how a header is parsed, so the
# address shown in the Outbox could differ from the one the mail server is given.
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
SENDS_LOCK = threading.Lock()
MAX_ATTACH_BYTES = 15 * 1024 * 1024
TIMEOUT_SECONDS = 20
NOT_SET_UP = "not sent: email delivery is not set up"
DEMO = "not sent: demo account"
UNVERIFIED = "not sent: verify your email address first (Team & account page), then Send again"


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
    """No line breaks or control characters in headers, including Unicode line separators."""
    return re.sub(r"[\x00-\x1f\x7f\x85\u2028\u2029]+", " ", str(value or "")).strip()[:limit]


def _sends_path() -> Path:
    import storage  # the data directory can change in tests, so look it up each time
    return Path(storage.DATA_DIR) / "mail_sends.json"


def _load_sends() -> dict[str, list[str]]:
    try:
        data = json.loads(_sends_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def sent_in_last_day(company_id: str, now: datetime | None = None) -> int:
    """Emails this company sent in the last 24 hours. Kept outside the company's data,
    so resetting the company doesn't reset its sending allowance."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=1)).isoformat(timespec="seconds")
    with SENDS_LOCK:
        return sum(1 for when in _load_sends().get(company_id, []) if when >= cutoff)


def record_sent(company_id: str, count: int, now: datetime | None = None) -> None:
    if count <= 0:
        return
    import storage
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=1)).isoformat(timespec="seconds")
    stamp = now.isoformat(timespec="seconds")
    with SENDS_LOCK:
        data = _load_sends()
        for key in list(data):
            data[key] = [w for w in data[key] if isinstance(w, str) and w >= cutoff]
            if not data[key]:
                del data[key]
        data.setdefault(company_id, []).extend([stamp] * count)
        storage.write_json(_sends_path(), data)


def build_message(
    entry: dict[str, Any],
    company_name: str,
    reply_to: str,
    files: list[tuple[str, Any]],
    cfg: dict[str, Any],
    sender_name: str | None = None,
) -> tuple[EmailMessage, list[str]]:
    """Return the message and a list of attachments left off (with the reason)."""
    to = _clean_header(entry.get("to", ""), 254)
    if not EMAIL_RE.match(to):
        raise ValueError(f"the address {to!r} is not a valid email address")
    msg = EmailMessage()
    sender_name = _clean_header(sender_name or f"{company_name or 'Billdable'} via Billdable", 120)
    local, _, domain = cfg["from"].partition("@")
    msg["From"] = Address(display_name=sender_name, username=local, domain=domain)
    msg["To"] = to
    reply_to = _clean_header(reply_to, 254)
    if reply_to and EMAIL_RE.match(reply_to):
        msg["Reply-To"] = reply_to
    cc = [_clean_header(a, 254) for a in entry.get("cc") or []]
    cc = [a for a in cc if EMAIL_RE.match(a) and a.lower() != to.lower()][:5]
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = _clean_header(entry.get("subject", ""), 250)
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=domain or None)
    left_off: list[str] = []
    attached: list[tuple[str, bytes]] = []
    total = 0
    for name, path in files:
        try:
            # A file on disk, or bytes made for this email (the pay app PDF).
            data = bytes(path) if isinstance(path, (bytes, bytearray)) else path.read_bytes()
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
    # The envelope recipients are given explicitly, never re-read from the headers.
    msg.recipients = [to] + cc  # type: ignore[attr-defined]
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
                refused = server.send_message(msg, to_addrs=getattr(msg, "recipients", None))
                results[i] = ("refused: " + ", ".join(f"{k} ({v[0]})" for k, v in refused.items())) if refused else None
            except smtplib.SMTPRecipientsRefused as exc:
                results[i] = "recipient refused: " + ", ".join(exc.recipients.keys())
            except Exception as exc:  # one bad message must not stop the rest
                results[i] = _short(exc)
    except Exception as exc:
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


def forget_company(company_id: str) -> None:
    """Drop a deleted company's send counter."""
    import storage
    with SENDS_LOCK:
        data = _load_sends()
        if data.pop(company_id, None) is not None:
            storage.write_json(_sends_path(), data)
