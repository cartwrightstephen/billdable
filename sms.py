"""Outgoing internal texts through Twilio's REST API (Decision 7).

Texts are for a company's own team. Clients are never texted: the one way to send,
send_to_member, takes a user id, looks the number up on that person's account in that company,
and refuses anyone who hasn't agreed, or whose number is a client's. Nothing here takes a number
to send to from a caller. Agreeing takes effect only once a code texted to the number has come
back (send_code; auth.confirm_sms_code), so nobody can sign up someone else's phone.

Settings: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_NUMBER (the sender, +1...).
SMS_DAILY_LIMIT is texts per company per 24 hours, default 20. SMS_ALLOWLIST is comma-separated
numbers: when set, texts to any other number aren't sent. With MAIL_ALLOWLIST or
DURACASH_ENV=staging in force and no SMS_ALLOWLIST, no text goes out. TWILIO_API_BASE overrides
Twilio's address (tests use a local stand-in).

A failure is returned and logged, never raised, so it can't stop what triggered the text. What is
logged carries the company and the reason, never a phone number.

The count of texts is kept in <data dir>/sms_sends.json, and a text's place is taken before it is
sent. If the file can't be read or written, no text goes out: a copy is kept as
sms_sends.json.bad-<time>, the server alerts, and the file stays until a person removes it.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import alerts
import auth
import billing
import mailer
import storage

API_BASE = "https://api.twilio.com"
TIMEOUT_SECONDS = 15
BODY_MAX = 320
OPT_OUT_LINE = " Reply STOP to opt out."
NOT_SET_UP = "not sent: texting is not set up on this server"
DEMO = "not sent: demo account"
NOT_ALLOWED = "not sent: this number isn't on this server's allow-list"
READ_ONLY = "not sent: this company is read-only"
CLIENT_NUMBER = "not sent: this number belongs to a client"
COUNTER_BROKEN = "not sent: the count of texts sent can't be read"
UNSUBSCRIBED = 21610   # Twilio: this number replied STOP
# Twilio's default opt-out words; it answers them itself. Only a message of the one word counts.
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
OTHER_WORDS = {"START", "UNSTOP", "HELP", "INFO"}
_SENDS_LOCK = threading.Lock()
_REFUSED = "Twilio answered"
_SEP = r"[^0-9A-Za-z+]{0,3}"
# A 10-digit number anywhere in what was typed, so an extension or a note beside it changes nothing.
_NATIONAL = re.compile(rf"(?<![0-9])(?:\+?1{_SEP})?([0-9]{{3}}){_SEP}([0-9]{{3}}){_SEP}([0-9]{{4}})(?![0-9])")


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
    """A US number as +1XXXXXXXXXX, or "" when it isn't one (extensions, other countries and digits
    of other scripts aren't)."""
    m = re.fullmatch(r"\+?(?:1)?([2-9][0-9]{9})", re.sub(r"[\s().-]", "", str(text or "")))
    return "+1" + m.group(1) if m else ""


def _plain_digits(text: str) -> str:
    """Digits of any script as 0-9, for reading a client's number as it was stored."""
    return "".join(str(unicodedata.digit(c)) if c.isdigit() and unicodedata.digit(c, None) is not None else c for c in text)


def _numbers_in(text: Any) -> set[str]:
    """Each 10-digit number in what is held for a client, as +1XXXXXXXXXX, however it is written."""
    return {"+1" + "".join(m.groups()) for m in _NATIONAL.finditer(_plain_digits(str(text or "")))}


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
    """Every phone number held for a client, in the client's record or on a job, as +1XXXXXXXXXX:
    the 10-digit number decides, whatever the format or extension."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        nonlocal found
        if isinstance(value, dict):
            for key, inner in value.items():
                if "phone" in str(key).lower():
                    for text in inner if isinstance(inner, list) else [inner]:
                        if isinstance(text, str):
                            found |= _numbers_in(text)
                elif isinstance(inner, (dict, list)) and str(key) in {"contacts", "playbook", "client"}:
                    walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(state.get("payers") or [])
    for job in state.get("jobs") or []:
        walk({k: v for k, v in job.items() if "phone" in k.lower() or k in {"contacts", "client"}})
    return found


def is_client_number(state: dict[str, Any], text: Any) -> bool:
    return bool(_numbers_in(text) & client_numbers(state))


def _sends_path() -> Path:
    # the data directory can change in tests, so look it up each time
    return Path(storage.DATA_DIR) / "sms_sends.json"


class _CounterBroken(Exception):
    """The count of texts sent can't be trusted."""


def _counter_broken(why: str, company_id: str = "") -> None:
    _log("error", msg="the count of texts sent can't be read: no texts until it is fixed", reason=why, company=company_id)
    alerts.notify("sms-counter", f"sms_sends.json can't be used ({why}), so no texts are going out. A copy is kept beside it. Fix or remove the file to start again; removing it restarts the count.")


def _load_sends() -> dict[str, list[str]]:
    """{company: [when]}. No file is a count of nothing; anything unreadable raises _CounterBroken."""
    path = _sends_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise _CounterBroken(exc.__class__.__name__)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
        storage.set_aside(path)
        raise _CounterBroken("it is damaged")
    return data


def sent_in_last_day(company_id: str, now: datetime | None = None) -> int:
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=1)).isoformat(timespec="seconds")
    with _SENDS_LOCK:
        return sum(1 for when in _load_sends().get(company_id, []) if when >= cutoff)


def _reserve(company_id: str, limit: int) -> str | None:
    """Take a place in today's count before sending, so sends at the same moment can't pass the
    limit and a crash can't lose one. Returns the place, or None when the limit is reached."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=1)).isoformat(timespec="seconds")
    with _SENDS_LOCK:
        data = _load_sends()
        for key in list(data):
            data[key] = [w for w in data[key] if isinstance(w, str) and w >= cutoff]
            if not data[key]:
                del data[key]
        if len(data.get(company_id, [])) >= limit:
            return None
        stamp = now.isoformat(timespec="seconds")
        data.setdefault(company_id, []).append(stamp)
        storage.write_json(_sends_path(), data)
        return stamp


def _release(company_id: str, stamp: str) -> None:
    """Give a place back: the text certainly didn't go."""
    try:
        with _SENDS_LOCK:
            data = _load_sends()
            if stamp in data.get(company_id, []):
                data[company_id].remove(stamp)
                storage.write_json(_sends_path(), data)
    except (_CounterBroken, OSError) as exc:
        _log("warn", msg="a text's place in the count wasn't given back", reason=exc.__class__.__name__, company=company_id)


def forget_company(company_id: str) -> None:
    """Drop a deleted company's send counter."""
    try:
        with _SENDS_LOCK:
            data = _load_sends()
            if data.pop(company_id, None) is not None:
                storage.write_json(_sends_path(), data)
    except _CounterBroken as exc:
        _counter_broken(str(exc), company_id)


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
        return f"{_REFUSED} {exc.code}" + (f" (error {code})" if code else ""), code if isinstance(code, int) else None
    except (error.URLError, OSError, ValueError) as exc:
        return f"couldn't reach Twilio ({exc.__class__.__name__})", None


def _deliver(state: dict[str, Any], company_id: str, user_id: str, phone: str, body: str) -> str:
    """The checks every text passes, then the text. The number comes from the account or a live
    code, never from a caller's request."""
    if billing.read_only_why(state):
        return READ_ONLY
    if phone in client_numbers(state):
        _log("warn", msg="text refused: the number on this account is a client's", company=company_id, user=user_id)
        return CLIENT_NUMBER
    if not allowed(phone):
        _log("info", msg="text held: not on the allow-list", company=company_id, user=user_id)
        return NOT_ALLOWED
    cfg = settings()
    try:
        place = _reserve(company_id, cfg["daily_limit"])
    except (_CounterBroken, OSError) as exc:
        _counter_broken(str(exc) or exc.__class__.__name__, company_id)
        return COUNTER_BROKEN
    if place is None:
        return f"not sent: the daily limit of {cfg['daily_limit']} texts is reached"
    text = " ".join(str(body or "").split())[:BODY_MAX - len(OPT_OUT_LINE)] + OPT_OUT_LINE
    reason, code = _post(cfg, phone, text)
    if reason:
        _log("warn", msg="text failed", reason=reason, company=company_id, user=user_id)
        if code == UNSUBSCRIBED:
            auth.sms_stop(phone)   # they replied STOP and the post never reached us
        if reason.startswith(_REFUSED):   # a timeout may have gone through, so it keeps its place
            _release(company_id, place)
        return f"not sent: {reason}"
    return "sent"


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
        if not person["phone"]:
            return "not sent: no phone number on this account"
        if not person["opted_in"]:
            return "not sent: this person has not agreed to texts"
        return _deliver(state, company_id, user_id, person["phone"], body)
    except Exception as exc:  # a text must never stop what triggered it
        _log("error", msg="text failed", reason=exc.__class__.__name__, company=company_id, user=user_id)
        return f"not sent: unexpected error ({exc.__class__.__name__})"


def send_code(state: dict[str, Any], company_id: str, user_id: str, code: str) -> str:
    """Text a live code (auth.issue_sms_code) to the number it was issued for: the proof a number
    is this person's. The same checks and the same count as any other text. Returns "sent" or why
    not; never raises."""
    try:
        if company_id == auth.DEMO_COMPANY_ID:
            return DEMO
        if not configured():
            return NOT_SET_UP
        person = auth.sms_contact(company_id, user_id)
        phone = auth.sms_code_number(user_id, code) if person else ""
        if not phone:
            return "not sent: this code isn't live"
        return _deliver(state, company_id, user_id, phone, f"Duracash code: {code}. Enter it on My account to get work reminders at this number. Ignore this if it wasn't you.")
    except Exception as exc:
        _log("error", msg="code text failed", reason=exc.__class__.__name__, company=company_id, user=user_id)
        return f"not sent: unexpected error ({exc.__class__.__name__})"
