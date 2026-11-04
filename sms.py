"""Outgoing internal texts through Twilio's REST API (Decision 7).

Texts are for a company's own team. Clients are never texted: the one way to send,
send_to_member, takes a user id, looks the number up on that person's account in that company,
and refuses anyone who hasn't agreed, or whose number is a client's. Nothing here takes a number
to send to from a caller.

Settings: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_NUMBER (the sender, +1...).
SMS_DAILY_LIMIT is texts per company per 24 hours, default 20. SMS_ALLOWLIST is comma-separated
numbers: when set, texts to any other number aren't sent. With MAIL_ALLOWLIST or
DURACASH_ENV=staging in force and no SMS_ALLOWLIST, no text goes out. TWILIO_API_BASE overrides
Twilio's address (tests use a local stand-in).

A failure is returned and logged, never raised, so it can't stop what triggered the text. What is
logged carries the company and the reason, never a phone number.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import auth
import mailer

API_BASE = "https://api.twilio.com"
TIMEOUT_SECONDS = 15
BODY_MAX = 320
OPT_OUT_LINE = " Reply STOP to opt out."
NOT_SET_UP = "not sent: texting is not set up on this server"
DEMO = "not sent: demo account"
NOT_ALLOWED = "not sent: this number isn't on this server's allow-list"
UNSUBSCRIBED = 21610   # Twilio: this number replied STOP
# Twilio's default opt-out words; it answers them itself. Only a message of the one word counts.
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
OTHER_WORDS = {"START", "UNSTOP", "HELP", "INFO"}
_SENDS_LOCK = threading.Lock()


def _log(level: str, **fields: Any) -> None:
    record = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": level, **fields}
    sys.stderr.write(json.dumps(record, default=str) + "\n")


def settings() -> dict[str, Any]:
    try:
        limit = int(os.environ.get("SMS_DAILY_LIMIT") or "20")
    except ValueError:
        limit = 20
    return {
        "sid": (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip(),
        "token": (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip(),
        "from": e164(os.environ.get("TWILIO_NUMBER")),
        "base": (os.environ.get("TWILIO_API_BASE") or API_BASE).strip().rstrip("/"),
        "daily_limit": max(0, limit),
    }


def configured() -> bool:
    cfg = settings()
    return bool(cfg["sid"] and cfg["token"] and cfg["from"])


def e164(text: Any) -> str:
    """A US number as +1XXXXXXXXXX, or "" when it isn't one (extensions and other countries aren't)."""
    m = re.fullmatch(r"\+?(?:1)?([2-9]\d{9})", re.sub(r"[\s().-]", "", str(text or "")))
    return "+1" + m.group(1) if m else ""


def allowed(phone: str) -> bool:
    """May a text go to this number? See SMS_ALLOWLIST above."""
    raw = (os.environ.get("SMS_ALLOWLIST") or "").strip()
    if raw:
        return bool(phone) and phone in {e164(entry) for entry in raw.split(",")}
    return not mailer.list_in_force()


def is_stop(body: Any) -> bool:
    return str(body or "").strip().upper() in STOP_WORDS


def is_keyword(body: Any) -> bool:
    return str(body or "").strip().upper() in OTHER_WORDS


def opt_out(phone: Any) -> int:
    """STOP from this number: returns how many accounts had texts turned off."""
    return auth.sms_stop(e164(phone))


def client_numbers(state: dict[str, Any]) -> set[str]:
    """Every phone number held for a client, in the client's record or on a job."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if "phone" in str(key).lower() and isinstance(inner, str):
                    found.add(e164(inner))
                elif isinstance(inner, (dict, list)) and str(key) in {"contacts", "playbook", "client"}:
                    walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(state.get("payers") or [])
    for job in state.get("jobs") or []:
        walk({k: v for k, v in job.items() if "phone" in k.lower() or k in {"contacts", "client"}})
    found.discard("")
    return found


def _sends_path() -> Path:
    import storage  # the data directory can change in tests, so look it up each time
    return Path(storage.DATA_DIR) / "sms_sends.json"


def _load_sends() -> dict[str, list[str]]:
    try:
        data = json.loads(_sends_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def sent_in_last_day(company_id: str, now: datetime | None = None) -> int:
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=1)).isoformat(timespec="seconds")
    with _SENDS_LOCK:
        return sum(1 for when in _load_sends().get(company_id, []) if when >= cutoff)


def _record_sent(company_id: str) -> None:
    import storage
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=1)).isoformat(timespec="seconds")
    with _SENDS_LOCK:
        data = _load_sends()
        for key in list(data):
            data[key] = [w for w in data[key] if isinstance(w, str) and w >= cutoff]
            if not data[key]:
                del data[key]
        data.setdefault(company_id, []).append(now.isoformat(timespec="seconds"))
        storage.write_json(_sends_path(), data)


def forget_company(company_id: str) -> None:
    """Drop a deleted company's send counter."""
    import storage
    with _SENDS_LOCK:
        data = _load_sends()
        if data.pop(company_id, None) is not None:
            storage.write_json(_sends_path(), data)


def _post(cfg: dict[str, Any], to: str, body: str) -> tuple[str, int | None]:
    """One POST to Twilio's Messages resource. Returns ("", None) when accepted, else (reason, code)."""
    url = f"{cfg['base']}/2010-04-01/Accounts/{parse.quote(cfg['sid'])}/Messages.json"
    login = base64.b64encode(f"{cfg['sid']}:{cfg['token']}".encode()).decode()
    req = request.Request(url, data=parse.urlencode({"To": to, "From": cfg["from"], "Body": body}).encode(), method="POST",
                          headers={"Authorization": f"Basic {login}", "Accept": "application/json",
                                   "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as res:
            res.read()
        return "", None
    except error.HTTPError as exc:
        try:
            reply = json.loads(exc.read() or b"{}")
        except ValueError:
            reply = {}
        code = reply.get("code") if isinstance(reply, dict) else None
        return f"Twilio answered {exc.code}" + (f" (error {code})" if code else ""), code if isinstance(code, int) else None
    except (error.URLError, OSError, ValueError) as exc:
        return f"couldn't reach Twilio ({exc.__class__.__name__})", None


def send_to_member(state: dict[str, Any], company_id: str, user_id: str, body: str) -> str:
    """Text one team member of this company. Returns "sent" or why not; never raises."""
    try:
        if company_id == auth.DEMO_COMPANY_ID:
            return DEMO
        if not configured():
            return NOT_SET_UP
        person = auth.sms_contact(company_id, user_id)
        if not person:
            return "not sent: this person is not on this team"
        phone = person["phone"]
        if not phone:
            return "not sent: no phone number on this account"
        if not person["opted_in"]:
            return "not sent: this person has not agreed to texts"
        if phone in client_numbers(state):
            _log("warn", msg="text refused: the number on this account is a client's", company=company_id, user=user_id)
            return "not sent: this number belongs to a client"
        if not allowed(phone):
            _log("info", msg="text held: not on the allow-list", company=company_id, user=user_id)
            return NOT_ALLOWED
        cfg = settings()
        if sent_in_last_day(company_id) >= cfg["daily_limit"]:
            return f"not sent: the daily limit of {cfg['daily_limit']} texts is reached"
        text = " ".join(str(body or "").split())[:BODY_MAX - len(OPT_OUT_LINE)] + OPT_OUT_LINE
        reason, code = _post(cfg, phone, text)
        if reason:
            _log("warn", msg="text failed", reason=reason, company=company_id, user=user_id)
            if code == UNSUBSCRIBED:
                auth.sms_stop(phone)   # they replied STOP and the post never reached us
            return f"not sent: {reason}"
        _record_sent(company_id)
        return "sent"
    except Exception as exc:  # a text must never stop what triggered it
        _log("error", msg="text failed", reason=exc.__class__.__name__, company=company_id, user=user_id)
        return f"not sent: unexpected error ({exc.__class__.__name__})"
