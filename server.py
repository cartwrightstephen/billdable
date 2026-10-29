"""Local Duracash server with company isolation and cookie sessions.

    python3 server.py
    open http://127.0.0.1:8765

Each company has its own JSON file under <data dir>/companies/. Auth lives in
<data dir>/auth.json. The data dir is DURACASH_DATA_DIR, default data/ beside
this file. Only index.html, app.js and styles.css are served from static/; the
page addresses the app uses (/today, /jobs/..., /money/...) all get index.html.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import base64
import gzip
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sys
import re
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from html import escape as html_escape
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlparse

import alerts
import auth
import backup
import billing
import calendar_feed
import domain
import helper
import importer
import legal
import mail_events
import mailer
import metrics
import migrations
import monthly
import offsite
import outbox
import payapp_pdf
import qbo
import qbo_csv
import qr
import payer_page
import sov
import storage
import store
import trial
import twilio_media
from seed import seed_state

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
COMPANIES = storage.DATA_DIR / "companies"
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "0.0.0.0")
COOKIE = "duracash_session"
SHARE_COOKIE = "duracash_share"  # the key of what this browser shared while signed out (R6-10)
SHARE_HOLDS_PER_HOUR = 10  # holds an hour from one network
_COMPANY_LOCKS: dict = {}
_COMPANY_LOCKS_GUARD = threading.Lock()
_DELETED_LOCK = threading.Lock()
DISK_FREE_MIN_MB = 200   # /healthz answers 503 below this (R6-06)
INBOUND_PER_HOUR = 120   # posts an hour to one inbound email or text address (R6-07)


def company_lock(company_id: str) -> threading.RLock:
    """One lock per company: a change loads, edits and saves under it. Companies don't wait on each other.
    The same thread may take it again: a read that brings an old file up to date saves it under it."""
    with _COMPANY_LOCKS_GUARD:
        return _COMPANY_LOCKS.setdefault(company_id, threading.RLock())


MAX_BODY_BYTES = 1_000_000
MAX_INBOUND_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 5_000
BODY_SECONDS = 60  # a request body must arrive within this long in total, however slowly it trickles

# The only files served from static/. Anything else is a 404.
STATIC_FILES = {
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    # Installable app (P3): manifest, icons, and a service worker that caches nothing.
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
# The app's ES modules (P2-8): app.js imports them from /js/, and each screen's views from
# /js/views/ (R1-03); no bundler.
STATIC_FILES.update({f"/{p.relative_to(STATIC).as_posix()}": (p.relative_to(STATIC).as_posix(), "text/javascript; charset=utf-8")
                     for p in sorted([*(STATIC / "js").glob("*.js"), *(STATIC / "js" / "views").glob("*.js")])})
# First path segments that belong to the browser app; each one serves index.html so
# refresh, Back and links in emails land on the same page (the app reads the address).
APP_SECTIONS = {"today", "jobs", "inbox", "money", "settings"}
APP_PATH_RE = re.compile(r"^/(?:[a-z]+)(?:/[A-Za-z0-9_-]{1,64}){0,3}/?$")
_ASSETS: dict = {}


def asset(name: str) -> dict:
    """A static file with its gzip copy and a short content hash, rebuilt when the file changes."""
    path = STATIC / name
    mtime = path.stat().st_mtime_ns
    cached = _ASSETS.get(name)
    if not cached or cached["mtime"] != mtime:
        raw = path.read_bytes()
        cached = {"mtime": mtime, "raw": raw, "gz": gzip.compress(raw, 9, mtime=0),
                  "hash": hashlib.sha256(raw).hexdigest()[:12]}
        _ASSETS[name] = cached
    return cached


def index_html() -> bytes:
    """index.html with each script and stylesheet address carrying its content hash, so a
    browser can keep them for a year and still gets a new copy the moment one changes."""
    page = asset("index.html")["raw"].decode("utf-8")
    for url, (name, _) in STATIC_FILES.items():
        page = page.replace(f'"{url}"', f'"{url}?v={asset(name)["hash"]}"')
    return page.encode("utf-8")


NOT_FOUND_PAGE = b"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Page not found - Duracash</title>
<style>body{font:16px/1.6 system-ui,sans-serif;background:#f6f3ee;color:#1c1915;margin:0;display:grid;place-items:center;min-height:100vh}main{max-width:420px;padding:24px}h1{font:500 32px Georgia,serif;margin:0 0 8px}a{color:#8c3f1d}</style></head>
<body><main><h1>Page not found</h1><p>There is nothing at this address.</p><p><a href="/">Go to Duracash</a></p></main></body></html>"""

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # No page here uses the microphone, location, payment or USB; the camera is reached through a
    # file input (R6-04), which the browser hands to the phone's camera app.
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=(), payment=(), usb=()",
    # Scripts and styles only from this site's own files: an injected <script>, onerror= or
    # style="" never applies. Pages the server writes with a <style> block get that block's
    # hash added (_csp). P2-8.
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                               "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
                               "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
}


def is_helper_admin(user: dict) -> bool:
    """Founders who may answer helper questions: account ids in HELPER_ADMIN_USER_IDS (comma-separated).

    Account ids, not emails: sign-up doesn't verify email, so anyone could register a
    founder's address before the founder does. An id is assigned by the server.
    """
    allowed = {e.strip() for e in os.environ.get("HELPER_ADMIN_USER_IDS", "").split(",") if e.strip()}
    return bool(user) and user.get("id", "") in allowed


# What a contractor reads when no email can go out from this server (Oct 23 note 17): plain
# words, with the missing setting (DURACASH_URL or the mail service) in the server log only.
EMAIL_NOT_SET_UP = "email isn't set up on this server yet; contact Duracash support"


def app_url() -> str | None:
    """The public address used in emailed links (password reset, verification, invites).
    Never taken from the request's Host header, which a stranger controls."""
    url = (os.environ.get("DURACASH_URL") or "").strip().rstrip("/")
    return url if re.match(r"^https?://[^\s/]+", url) else None


def production() -> bool:
    """DURACASH_ENV=production: no demo tenant, and startup refuses a half-set configuration."""
    return (os.environ.get("DURACASH_ENV") or "").strip().lower() == "production"


def demo_mode() -> str:
    """DURACASH_DEMO: "on" (the default outside production), "readonly" or "off" (the default
    in production). The demo's password is public, so production allows it only read-only:
    visitors can look around, and nothing can be changed, reset or locked."""
    value = (os.environ.get("DURACASH_DEMO") or "").strip().lower()
    if value in {"off", "readonly"}:
        return value
    return "off" if production() else "on"


def demo_enabled() -> bool:
    return demo_mode() != "off"


# What a read-only company may still do (P2-2's "past due: read-only, never deletion"). The
# read-only demo allows less: no password, two-step or team changes, or the demo locks.
DEMO_READ_ONLY_OK = {"_p_helper_ask", "_p_helper_feedback", "_p_sov_parse", "_p_payments_csv"}
COMPANY_READ_ONLY_OK = DEMO_READ_ONLY_OK | {"_p_password", "_p_two_step", "_p_my_name", "_p_resend_verify", "_p_helper_admin",
                                             "_p_team_invite", "_p_team_revoke", "_p_team_role", "_p_team_remove", "_p_account_delete",
                                             "_p_qbo_payments", "_p_qbo_disconnect", "_p_billing_checkout", "_p_billing_portal"}


def demo_user() -> dict | None:
    """The demo owner, when the demo is on or read-only and its company exists. Someone who turned
    on two-step sign-in for it (possible when the demo is "on") keeps the password route only."""
    if not demo_enabled() or not company_exists(auth.DEMO_COMPANY_ID):
        return None
    user = auth.user_by_email(auth.DEMO_EMAIL)
    if not user or user.get("company_id") != auth.DEMO_COMPANY_ID or user.get("two_step"):
        return None
    return user


def demo_locked(company_id: str) -> bool:
    """The read-only demo also ignores writes from outside: its inbound addresses are visible to
    every visitor, so mail, texts, "Mark as received" and signatures sent to it change nothing."""
    return company_id == auth.DEMO_COMPANY_ID and demo_mode() == "readonly"


def read_only_reason(company_id: str, state: dict | None = None) -> str:
    """Why this company can't be changed right now, or "" when it can."""
    if company_id == auth.DEMO_COMPANY_ID and demo_mode() == "readonly":
        return "This is the read-only demo. Create your own company to try changes."
    if state is None:
        with company_lock(company_id):
            state = load_company(company_id)
    why = str(state.get("company", {}).get("read_only") or "").strip() or billing.standing(state)["hold"]
    return f"This company is read-only: {why}. Nothing was changed; your data is kept and can be exported from Settings." if why else ""


def data_notice() -> bool:
    """Show "data may be erased" on the landing page until someone has checked that a
    deploy kept the data (DURACASH_DATA_PERSISTENT=1)."""
    return os.environ.get("DURACASH_DATA_PERSISTENT") != "1"


def config_problems() -> list[str]:
    """What stops a production start. Empty outside production."""
    if not production():
        return []
    problems = []
    if not os.environ.get("DURACASH_DATA_DIR"):
        problems.append("DURACASH_DATA_DIR is not set. Point it at the persistent disk.")
    elif not storage.DATA_DIR.is_dir():
        problems.append(f"DURACASH_DATA_DIR ({storage.DATA_DIR}) does not exist. Is the disk mounted?")
    if os.environ.get("COOKIE_SECURE") != "1":
        problems.append("COOKIE_SECURE must be 1.")
    url = app_url()
    if not url or not url.startswith("https://"):
        problems.append("DURACASH_URL must be the site's https:// address, so emailed links work.")
    return problems


SECRET_PATHS = (
    (re.compile(r"(/api/inbound/(?:email|sms)/)[^/?\s]+"), r"\1[token]"),
    (re.compile(r"(/api/calendar/)[^/?\s]+?(\.ics)"), r"\1[token]\2"),
    (re.compile(r"(/api/mail-events/)[^/?\s]+"), r"\1[token]"),
    (re.compile(r"(/[ps]/[a-z0-9-]+\.)[A-Za-z0-9_-]+"), r"\1[token]"),
)


def redact(text: str) -> str:
    """Paths in the log never carry the secret parts of inbound or calendar addresses, or query strings."""
    text = str(text)
    for pattern, repl in SECRET_PATHS:
        text = pattern.sub(repl, text)
    return re.sub(r"\?[^\s\"]*", "", text)


def log_json(level: str, **fields) -> None:
    record = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": level, **fields}
    sys.stderr.write(json.dumps(record, default=str) + "\n")


_LIMITS: dict = {}
_LIMITS_LOCK = threading.Lock()


def trusted_proxy_hops() -> int:
    """How many proxies in front of the server add to X-Forwarded-For (R6-05). Render has one."""
    try:
        return max(0, int(os.environ.get("TRUSTED_PROXY_HOPS", "1")))
    except ValueError:
        return 1


def _local(ip: str) -> bool:
    """A caller on this machine: the tests, the test-contractor loader, a person trying it out."""
    return ip in {"127.0.0.1", "::1", "localhost"}


def _within_limit(key: str, count: int, seconds: int) -> bool:
    """True, and counted, when fewer than `count` events happened for `key` in the last `seconds`."""
    now = time.monotonic()
    with _LIMITS_LOCK:
        if len(_LIMITS) > 20_000:
            _LIMITS.clear()
        recent = [t for t in _LIMITS.get(key, []) if now - t < seconds]
        if len(recent) >= count:
            _LIMITS[key] = recent
            return False
        _LIMITS[key] = recent + [now]
        return True


class BadRequest(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def empty_state(company_name: str) -> dict:
    return {
        "company": {
            "name": company_name,
            "city": "",
            "crew": 0,
            "revenue_band": "",
            "books": "",
        },
        "payers": [],
        "jobs": [],
        "audit": [],
        "email_log": [],
        "schema_version": migrations.SCHEMA_VERSION,
    }


STYLE_BLOCK_RE = re.compile(rb"<style>(.*?)</style>", re.S)


def _csp(policy: str, body: bytes) -> str:
    """Allow exactly the <style> blocks this page carries, by their SHA-256."""
    hashes = sorted({"'sha256-" + base64.b64encode(hashlib.sha256(m.group(1)).digest()).decode() + "'"
                     for m in STYLE_BLOCK_RE.finditer(body)})
    return policy.replace("style-src 'self'", "style-src 'self' " + " ".join(hashes), 1) if hashes else policy


def company_store():
    """SQLite by default, the JSON files with DURACASH_STORAGE=json (store.py, P2-5)."""
    return store.open_store(COMPANIES)


def company_path(company_id: str) -> Path:
    """The JSON layout's file for a company (DURACASH_STORAGE=json)."""
    return COMPANIES / f"{company_id}.json"


def company_exists(company_id: str) -> bool:
    return company_store().exists(company_id)


def company_ids() -> list:
    return company_store().ids() if COMPANIES.exists() else []


class CompanyMissing(Exception):
    """A signed-in company whose data is gone: data was lost (a deploy without the persistent
    disk, a bad restore). The request answers 503 and nothing is saved over it (R6-06): a blank
    company served and saved back would hide the loss and overwrite whatever comes back."""

    MESSAGE = "Your company's data isn't available right now. Nothing was changed; we've been alerted."


def load_company(company_id: str) -> dict:
    state = company_store().load(company_id)
    if state is not None:
        if migrations.migrate(state):
            _write_back_migrated(company_id)
        return state
    log_json("error", msg="company data missing", company=company_id, folder=str(COMPANIES))
    alerts.notify(f"company-missing:{company_id}", f"company data missing for {company_id}; requests answer 503 until it is restored")
    raise CompanyMissing(company_id)


def _write_back_migrated(company_id: str) -> None:
    """A file from older code is saved in the current shape the first time it is read (R6-08),
    so the disk never waits on the company's next change. Read again under the lock and migrated
    afresh, so a change saved since the first read is kept."""
    with company_lock(company_id):
        fresh = company_store().load(company_id)
        if fresh is not None and company_id not in deleted_companies() and migrations.migrate(fresh):
            company_store().save(company_id, fresh)
            log_json("info", msg="company file brought up to date", company=company_id, schema=migrations.SCHEMA_VERSION)


def _deleted_path() -> Path:
    return storage.DATA_DIR / "deleted_companies.json"


def deleted_companies() -> set:
    try:
        return set(json.loads(_deleted_path().read_text()))
    except (OSError, ValueError):
        return set()


def send_system_email(to: str, subject: str, body: str, company_id: str = "", rid: str = "") -> str:
    """An email from Duracash itself (reset, verification, invite, monthly summary). Returns "sent"
    or the reason it wasn't. It counts against the company's daily limit like any other email."""
    if not mailer.configured():
        return "email delivery is not set up on this server"
    # However many companies or reset requests there are, the server sends at most 300 of
    # these an hour, so a flood of sign-ups or reset requests can't burn the mail account (R6-05).
    if not _within_limit("system-email", 300, 3600):
        log_json("warn", msg="system email held: hourly limit", rid=rid)
        return "the server's hourly email limit is reached; try again in an hour"
    cfg = mailer.settings()
    if company_id and mailer.sent_in_last_day(company_id) >= cfg["daily_limit"]:
        return f"the daily limit of {cfg['daily_limit']} emails is reached; try tomorrow"
    try:
        msg, _ = mailer.build_message({"to": to, "subject": subject, "body": body}, "", "", [], cfg, sender_name="Duracash")
        error = mailer.send([msg], cfg)[0]
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
    if error:
        log_json("warn", msg="system email failed", reason=str(error)[:200], rid=rid)
    elif company_id:
        mailer.record_sent(company_id, 1)
    return "sent" if not error else str(error)


def inbound_address(company_id: str) -> str:
    """The company's inbound email address, when INBOUND_EMAIL_ADDRESS_TEMPLATE is set; else ""."""
    template = os.environ.get("INBOUND_EMAIL_ADDRESS_TEMPLATE", "")
    if "{token}" not in template:
        return ""
    return template.replace("{token}", importer.company_tokens(company_id)["email"])


def delete_company_data(company_id: str) -> int:
    """Remove a company: its users and sessions, company file, reset snapshots, uploads, inbound
    and calendar addresses, helper and mail records. Backups that hold it expire on their own.
    Used by the owner's Delete the company and by admin.py for a deletion request. Returns how
    many users were removed."""
    removed = auth.delete_company_users(company_id)  # every session of the company ends here
    with company_lock(company_id):
        with _DELETED_LOCK:
            gone = sorted(deleted_companies() | {company_id})
            storage.write_json(_deleted_path(), gone)
        company_store().delete(company_id)
        for path in (COMPANIES / "snapshots").glob(f"{company_id}-*.json"):
            try:
                path.unlink()
            except OSError:
                pass
    shutil.rmtree(importer.UPLOADS / company_id, ignore_errors=True)
    importer.forget_company(company_id)
    helper.forget_company(company_id)
    mailer.forget_company(company_id)
    return removed


def save_company(company_id: str, state: dict) -> None:
    # A request that was already running when its company was deleted must not write it back,
    # and must not look as if it did (R6-06): it is refused like any other change that can't be made.
    if company_id in deleted_companies():
        log_json("warn", msg="write to a deleted company refused", company=company_id)
        raise domain.GateError("This company was deleted and can't be changed. Contact support if that is wrong.")
    company_store().save(company_id, state)


def activation_days(state: dict) -> int | None:
    """The business plan's activation measure: days from signup to the first paid pay app sent
    through Duracash (None until then)."""
    t = trial.status(state)
    if t.get("reason") != "paid":
        return None
    return (datetime.fromisoformat(t["ended_at"]).date() - datetime.fromisoformat(t["started_at"]).date()).days


def email_owners_question(company_id: str, asked: dict) -> list:
    """A question sent from the customer's page (R4-08) goes to the owners, with a link to the
    job; it is in the Inbox as well. Returns "address: result" lines for the log."""
    if company_id == auth.DEMO_COMPANY_ID:
        return []
    subject = f"Question on {asked['number']} — {asked['job_name']}"
    link = app_url()
    body = (f"{asked.get('from') or 'The customer'} asked on the customer page for {asked['number']}:\n\n{asked.get('text', '')}\n\n"
            + (f"Open the job: {link}/jobs/{asked['job_id']}\n" if link else "")
            + "It is in your Inbox too. Reply to the customer by email or phone.")
    lines = [f"{to}: {send_system_email(to, subject, body, company_id)}" for to in auth.owner_emails(company_id)]
    for line in lines:
        log_json("info", msg="payer question emailed", company=company_id, result=line.split(": ", 1)[-1][:120])
    return lines


class Site:
    """What outbox.py and monthly.py need from the server (R6-08). Each is looked up when it is
    called, so a test that points COMPANIES somewhere else, or patches load_company, still applies."""

    @staticmethod
    def lock(company_id: str):
        return company_lock(company_id)

    @staticmethod
    def load(company_id: str) -> dict:
        return load_company(company_id)

    @staticmethod
    def save(company_id: str, state: dict) -> None:
        save_company(company_id, state)

    @staticmethod
    def read(company_id: str) -> dict | None:
        """The company's state, or None when its data is missing (that is alerted on its own)."""
        try:
            with company_lock(company_id):
                return load_company(company_id)
        except CompanyMissing:
            return None

    @staticmethod
    def company_ids() -> list:
        return company_ids()

    @staticmethod
    def deleted() -> set:
        return deleted_companies()

    @staticmethod
    def inbound(company_id: str) -> str:
        return inbound_address(company_id)

    @staticmethod
    def send(to: str, subject: str, body: str, company_id: str = "") -> str:
        return send_system_email(to, subject, body, company_id)

    @staticmethod
    def app_url() -> str | None:
        return app_url()

    @staticmethod
    def log(level: str, **fields) -> None:
        log_json(level, **fields)


SITE = Site()


def ensure_demo() -> None:
    if not auth.user_by_email(auth.DEMO_EMAIL):
        auth.create_user(
            auth.DEMO_EMAIL,
            auth.DEMO_PASSWORD,
            "Alex Hartline",
            auth.DEMO_COMPANY_ID,
            enforce_policy=False,
        )
    if not company_exists(auth.DEMO_COMPANY_ID):
        save_company(auth.DEMO_COMPANY_ID, seed_state())


def _text(body: dict, key: str, default: str = "", limit: int = MAX_TEXT_CHARS) -> str:
    value = body.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise BadRequest(f"{key.replace('_', ' ').capitalize()} must be text.")
    if len(value) > limit:
        raise BadRequest(f"{key.replace('_', ' ').capitalize()} is too long (limit {limit:,} characters).")
    return value


def _filled(body: dict, key: str, message: str) -> str:
    """A text field the action can't do without: blank is refused under that field."""
    value = _text(body, key).strip()
    if not value:
        raise domain.GateError(message, fields={key: message})
    return value


def _list(body: dict, key: str) -> list | None:
    """A list from the request body, or None when absent; anything else was not sent by the app."""
    value = body.get(key)
    if value is not None and not isinstance(value, list):
        raise BadRequest(NOT_UNDERSTOOD)
    return value


def _number(body: dict, key: str, label: str, default: int | None = None) -> int:
    """A whole number from the request body; a bad one is refused under its field (409)."""
    return domain.parse_int(body.get(key, default), label, key, default)


# What a 400 says: the request's own wording (a Python exception's text) stays in the log.
NOT_UNDERSTOOD = "Something in the form was not understood. Check the fields and try again."


def _gate(exc: "domain.GateError") -> dict:
    """The 409 reply for a business refusal: the message, what stands in the way, and the
    fields to fix, each under its request key."""
    out = {"error": str(exc), "blockers": exc.blockers}
    if exc.fields:
        out["fields"] = exc.fields
    return out


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


class Handler(BaseHTTPRequestHandler):
    server_version = "Duracash"
    sys_version = ""
    # A client that stops sending or reading is dropped after this many seconds.
    timeout = 30

    # One JSON line per request goes to stderr (Render shows it in the service log): method,
    # path with secrets removed, status, milliseconds and a request id. Never headers or cookies.
    _rid = ""
    _t0 = 0.0

    def log_request(self, code="-", size="-") -> None:
        log_json(
            "info",
            method=getattr(self, "command", ""),
            path=redact(urlparse(getattr(self, "path", "")).path),
            status=int(code) if str(code).isdigit() else str(code),
            ms=round((time.monotonic() - self._t0) * 1000) if self._t0 else None,
            rid=self._rid,
        )

    def log_error(self, format: str, *args) -> None:
        log_json("warn", msg=redact(format % args), rid=self._rid)

    def _crash(self) -> None:
        path = redact(urlparse(self.path).path)
        log_json("error", rid=self._rid, path=path, trace=traceback.format_exc())
        kind = sys.exc_info()[0].__name__ if sys.exc_info()[0] else "Error"
        # Ids in paths (job-1a2b) would make every crash look new; group by the path's shape.
        shape = re.sub(r"/[a-z]+-[0-9a-f]{4,}", "/{id}", path)
        alerts.notify(f"crash:{shape}:{kind}", f"server error {kind} on {self.command} {shape}, request id {self._rid}")
        self._json(500, {"error": "Something went wrong on our side. It has been logged.", "request_id": self._rid})

    def _send(self, code: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            # PDFs go without the page policy (it stops browsers' PDF viewers), and a response
            # that sets its own policy (an uploaded file's sandbox) keeps only that one.
            if k == "Content-Security-Policy" and (content_type == "application/pdf" or k in (headers or {})):
                continue
            if k == "Content-Security-Policy" and content_type.startswith("text/html"):
                v = _csp(v, body)
            self.send_header(k, v)
        if os.environ.get("COOKIE_SECURE") == "1":
            # The site is served over https only: browsers must never try http for a year (R6-05).
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if self._rid:
            self.send_header("X-Request-Id", self._rid)
        if self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code: int, payload: dict, headers: dict | None = None) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json", headers)

    def _read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(raw_length)
        except ValueError:
            raise BadRequest("Content-Length must be a number.")
        if length < 0:
            raise BadRequest("Content-Length must be a number.")
        if length > MAX_BODY_BYTES:
            raise BadRequest("Request body is too large.", 413)
        if not length:
            return {}
        raw = self._read_body(length)
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequest("Request body must be valid JSON.")
        if not isinstance(body, dict):
            raise BadRequest("Request body must be a JSON object.")
        return body

    def _read_raw(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            raise BadRequest("Content-Length must be a number.")
        if length < 0:
            raise BadRequest("Content-Length must be a number.")
        if length > limit:
            raise BadRequest(f"Upload is larger than {limit // (1024 * 1024)} MB.", 413)
        return self._read_body(length) if length else b""

    def _read_body(self, length: int) -> bytes:
        """Read exactly `length` bytes within BODY_SECONDS in total. The socket timeout alone is per read,
        so a client sending one byte every few seconds could otherwise hold a request open for hours."""
        deadline = time.monotonic() + BODY_SECONDS
        chunks, left = [], length
        try:
            while left > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BadRequest("The request took too long to arrive.", 408)
                self.connection.settimeout(min(self.timeout, remaining))
                chunk = self.rfile.read1(min(left, 65536))
                if not chunk:
                    raise BadRequest("The request body ended early.")
                chunks.append(chunk)
                left -= len(chunk)
        except (TimeoutError, OSError):
            raise BadRequest("The request took too long to arrive.", 408)
        finally:
            try:
                self.connection.settimeout(self.timeout)
            except OSError:
                pass
        return b"".join(chunks)

    def _base_url(self) -> str:
        """The site's address for addresses shown in Settings and the Twilio signature check:
        DURACASH_URL when set (R6-07), since Host and X-Forwarded-Host are whatever the caller
        sends; the request's own address only on a server without it (a laptop, a test)."""
        url = app_url()
        if url:
            return url
        proto = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() or ("https" if os.environ.get("COOKIE_SECURE") == "1" else "http")
        host = (self.headers.get("X-Forwarded-Host") or "").split(",")[0].strip() or self.headers.get("Host") or f"127.0.0.1:{PORT}"
        return f"{proto}://{host}"

    def _inbound_refused(self, token: str) -> bool:
        """Checks on an inbound webhook before its body is read (R6-07): at most 120 posts an
        hour to one address, and HTTP Basic with INBOUND_WEBHOOK_PASSWORD when that is set (put
        it in the address given to the email service: https://any:PASSWORD@host/...).
        Answers and returns True when refused."""
        if not _within_limit(f"inbound:{token}", INBOUND_PER_HOUR, 3600):
            self._json(429, {"error": "Too many messages to this address this hour. Try again later."})
            return True
        password = os.environ.get("INBOUND_WEBHOOK_PASSWORD", "")
        if password:
            given = ""
            auth_header = self.headers.get("Authorization") or ""
            if auth_header[:6].lower() == "basic ":
                try:
                    given = base64.b64decode(auth_header[6:].strip(), validate=True).decode("utf-8").split(":", 1)[-1]
                except (ValueError, UnicodeDecodeError):
                    given = ""
            if not hmac.compare_digest(given.encode(), password.encode()):
                self._json(401, {"error": "Sign-in required for this address."}, {"WWW-Authenticate": 'Basic realm="Duracash inbound"'})
                return True
        return False

    def _mutate(self, company_id: str, fn, user: dict | None = None, deliver: bool = True, end_trial: bool = True) -> tuple:
        """The one way a company's state is changed (R6-08). Under the company's lock: load, run
        fn(state), which changes it in place, see whether the first paid pay app ends the Stripe
        trial, save, take the snapshot. Then, with the lock released: ask Stripe to end the trial,
        and, with deliver and the signed-in user, send the emails the change approved. A refusal
        becomes its answer (409, 403, 404, or 400 for a body that wasn't understood).
        Returns (status, reply); the caller writes it."""
        try:
            with company_lock(company_id):
                state = load_company(company_id)
                fn(state)
                # The first paid pay app ends the free trial (trial.py): tell Stripe, once.
                trial_sub = billing.trial_end_due(state) if end_trial and billing.configured() else None
                save_company(company_id, state)
                payload = domain.light_snapshot(state)
                # Most changes queue no email: don't load the company again just to find that out.
                queued = deliver and user is not None and any(e.get("status") == "queued" for e in state.get("email_log", []))
        except UnknownAction:
            return 404, {"error": "Unknown action"}
        except OwnerOnly:
            return 403, {"error": "Only an owner can do that."}
        except domain.GateError as exc:
            return 409, _gate(exc)
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            log_json("warn", msg="request not understood", rid=self._rid, path=redact(urlparse(self.path).path),
                     error=redact(f"{type(exc).__name__}: {exc}")[:300])
            return 400, {"error": NOT_UNDERSTOOD, "request_id": self._rid}
        if trial_sub:
            threading.Thread(target=self._end_stripe_trial, args=(company_id, trial_sub), daemon=True).start()
        if queued:
            # Approved emails go out after the change is saved and the lock is released; the reply
            # is written after that too, so a slow client can't stall other companies.
            payload = outbox.deliver_queued(SITE, company_id, user, rid=self._rid) or payload
        return 200, payload

    def _token(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(COOKIE)
        return morsel.value if morsel else None

    def _secure_cookie(self) -> bool:
        if os.environ.get("COOKIE_SECURE") == "1":
            return True
        return self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() == "https"

    def _set_cookie(self, token: str | None) -> dict:
        secure = "; Secure" if self._secure_cookie() else ""
        if token:
            value = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={60 * 60 * 24 * auth.SESSION_DAYS}{secure}"
        else:
            value = f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}"
        return {"Set-Cookie": value}

    def _client_ip(self) -> str:
        """The caller's address. Each proxy in front adds the address it saw to the end of
        X-Forwarded-For, so with TRUSTED_PROXY_HOPS proxies (1, Render's, by default) the caller
        is that many entries from the end; earlier entries come from the client and can be made
        up. 0 means no proxy: the socket's address, and the header is ignored."""
        hops = trusted_proxy_hops()
        forwarded = [p.strip() for p in (self.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
        return forwarded[-hops] if hops and len(forwarded) >= hops else self.client_address[0]

    def _session_user(self) -> dict | None:
        user = auth.session_user(self._token())
        if user and not demo_enabled() and user["company_id"] == auth.DEMO_COMPANY_ID:
            return None
        return user

    def _require_user(self) -> dict | None:
        user = self._session_user()
        if not user:
            self._json(401, {"error": "Sign in required."})
            return None
        return user

    def do_GET(self) -> None:
        self._rid, self._t0 = secrets.token_hex(6), time.monotonic()
        try:
            self._get()
        except BadRequest as exc:
            self._json(exc.code, {"error": str(exc), "request_id": self._rid})
        except domain.GateError as exc:
            self._json(409, _gate(exc))
        except CompanyMissing:
            self._json(503, {"error": CompanyMissing.MESSAGE, "request_id": self._rid})
        except Exception:
            self._crash()

    def do_POST(self) -> None:
        self._rid, self._t0 = secrets.token_hex(6), time.monotonic()
        try:
            self._post()
        except BadRequest as exc:
            self._json(exc.code, {"error": str(exc), "request_id": self._rid})
        except domain.GateError as exc:
            self._json(409, _gate(exc))
        except CompanyMissing:
            self._json(503, {"error": CompanyMissing.MESSAGE, "request_id": self._rid})
        except Exception:
            self._crash()

    # ----- routing (P2-7): one table, one handler per route -----
    # Access levels: "public" (no sign-in), "user" (signed in; the handler reads its own body,
    # if any), "body" (signed in, JSON body read, session checked again after the body
    # arrives), "mutate" (as body, then the handler changes the company's state under the
    # lock; it is saved, approved emails are delivered and the snapshot returned).

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path
        for route in ROUTES:
            if route.method != method:
                continue
            m = route.regex.fullmatch(path)
            if m:
                self._run(route, m)
                return
        if method == "GET":
            self._get_fallback(path)
        elif path.startswith("/api/"):
            # Unknown API calls answer like before: sign in first, then "Not found".
            if self._require_user():
                self._json(404, {"error": "Not found"})
        else:
            self._send(404, NOT_FOUND_PAGE, "text/html; charset=utf-8")

    def _run(self, route: "Route", m: "re.Match") -> None:
        handler = getattr(self, route.handler)
        if route.access == "public":
            handler(m)
            return
        user = self._require_user()
        if not user:
            return
        if route.method == "POST":
            allowed = DEMO_READ_ONLY_OK if user["company_id"] == auth.DEMO_COMPANY_ID and demo_mode() == "readonly" else COMPANY_READ_ONLY_OK
            if route.handler not in allowed:
                why = read_only_reason(user["company_id"])
                if why:
                    self._json(403, {"error": why, "read_only": True})
                    return
        if route.access == "user":
            handler(m, user)
            return
        body = self._read_json()
        # Check the session again now the body is here: someone removed, demoted or signed out
        # while their request was arriving must not finish it with their old rights.
        user = self._session_user()
        if not user:
            self._json(401, {"error": "Sign in required."})
            return
        if route.owner and user["role"] != "owner":
            self._json(403, {"error": "Only an owner can do that."})
            return
        # The audit trail names the signed-in user. Any "actor" in the body is ignored.
        ctx = Ctx(user=user, body=body, company_id=user["company_id"], actor=user.get("name") or user.get("email") or "owner")
        if route.access == "body":
            handler(m, ctx)
            return

        def change(state: dict) -> None:
            fresh = handler(state, m, ctx)
            if fresh is not None and fresh is not state:  # a reset hands back a new company
                state.clear()
                state.update(fresh)

        status, payload = self._mutate(ctx.company_id, change, ctx.user)
        # What a handler adds to its answer (the payments import's row results) rides on the snapshot.
        self._json(status, {**payload, **ctx.reply} if status == 200 and ctx.reply else payload)

    def _get(self) -> None:
        self._dispatch("GET")

    def _post(self) -> None:
        self._dispatch("POST")

    def _get_fallback(self, path: str) -> None:
        if path in {"/", "/index.html"} or (APP_PATH_RE.match(path) and path.split("/")[1] in APP_SECTIONS):
            self._send_compressible(200, index_html(), "text/html; charset=utf-8", {"Cache-Control": "no-cache"})
        elif path in STATIC_FILES:
            self._static_asset(*STATIC_FILES[path])
        elif path.startswith("/api/"):
            self._json(404, {"error": "Not found"})
        else:
            self._send(404, NOT_FOUND_PAGE, "text/html; charset=utf-8")

    # ----- GET handlers -----

    def _g_healthz(self, m) -> None:
        # For an uptime monitor: no sign-in, no secrets, 503 when data can't be written or the
        # disk is nearly full (R6-06: a full disk fails every save and every backup).
        writable = True
        try:
            probe = storage.DATA_DIR / f".health-{secrets.token_hex(4)}"
            storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok")
            probe.unlink()
        except OSError:
            writable = False
        try:
            free_mb = shutil.disk_usage(storage.DATA_DIR).free // (1024 * 1024)
        except OSError:
            free_mb = None
        roomy = free_mb is None or free_mb >= DISK_FREE_MIN_MB
        ok = writable and roomy
        self._json(200 if ok else 503, {
            "ok": ok,
            "storage": "ok" if ok else "not writable" if not writable else f"under {DISK_FREE_MIN_MB} MB free",
            "disk_free_mb": free_mb,
            "email": "configured" if mailer.configured() else "not set up",
            "last_backup": backup.newest_time(),
            "offsite_backup": offsite.last_upload() if offsite.configured() else "not set up",
            "env": "production" if production() else "development",
        })

    def _g_team(self, m, user) -> None:
        data = auth.team(user["company_id"])
        if user["role"] != "owner":
            data["invites"] = []
        self._json(200, data)

    def _g_admin_companies(self, m, user) -> None:
        if not is_helper_admin(user):
            self._json(403, {"error": "Only the Duracash team can see this."})
            return
        self._json(200, {"companies": self._company_overview()})

    def _g_payapp_pdf(self, m, user) -> None:
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        pdf, name = payapp_pdf.for_invoice(state, m.group(1), m.group(2))
        if not pdf:
            self._json(404, {"error": "Pay app not found."})
            return
        self._send(200, pdf, "application/pdf", {"Content-Disposition": f'inline; filename="{name}"'})

    def _g_payapp_csv(self, m, user) -> None:
        """The pay app in QuickBooks Online's invoice-import layout (R4-11)."""
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        job = next((j for j in state.get("jobs", []) if j["id"] == m.group(1)), None)
        invoice = next((i for i in (job or {}).get("invoices", []) if i["id"] == m.group(2)), None)
        if not invoice:
            self._json(404, {"error": "Pay app not found."})
            return
        try:
            body = qbo.invoice_csv(state, job, invoice)
        except domain.GateError as exc:
            self._json(409, _gate(exc))
            return
        name = re.sub(r"[^A-Za-z0-9._-]", "-", invoice["number"]) + "-quickbooks.csv"
        self._send(200, body.encode("utf-8"), "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="{name}"'})

    def _g_payapp_page(self, m, user) -> None:
        self._link_page(user, m.group(1), m.group(2), "invoices", "payer_token", "p")

    def _g_co_page(self, m, user) -> None:
        self._link_page(user, m.group(1), m.group(2), "change_orders", "sign_token", "s")

    def _link_page(self, user: dict, job_id: str, item_id: str, key: str, secret: str, prefix: str) -> None:
        """Open a pay app's customer page or a change order's signing page. Their secret
        addresses are never in what the app loads (R6-05); this sends a signed-in person on."""
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
        item = next((i for i in (job or {}).get(key, []) if i["id"] == item_id), None)
        if not item or not item.get(secret):
            self._json(404, {"error": "There is no page for that yet. It is made when the email goes out."})
            return
        self._send(303, b"", "text/plain", {"Location": f"/{prefix}/{user['company_id']}.{item[secret]}"})

    def _g_me(self, m) -> None:
        user = self._session_user()
        if not user:
            self._json(401, {"error": "Not signed in.", "data_notice": data_notice()})
            return
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        self._json(200, {"user": user, "company": state.get("company", {}), "helper_admin": is_helper_admin(user),
                         "mail_ready": bool(mailer.configured() and app_url()),
                         "read_only": read_only_reason(user["company_id"], state),
                         "billing": billing.standing(state),
                         # Shared to the app from this browser before signing in (R6-10).
                         "held_share": importer.held_count(self._held_key())})

    def _g_demo_available(self, m) -> None:
        self._json(200, {"available": demo_user() is not None})

    def _g_helper_review(self, m, user) -> None:
        if not is_helper_admin(user):
            self._json(403, {"error": "Only the Duracash team can review helper questions."})
            return
        self._json(200, helper.review())

    def _g_state(self, m, user) -> None:
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        self._json(200, domain.light_snapshot(state))

    def _paging(self, default_limit: int = 50) -> tuple:
        q = parse_qs(urlparse(self.path).query)
        try:
            offset = max(0, int((q.get("offset") or ["0"])[0]))
            limit = min(200, max(1, int((q.get("limit") or [str(default_limit)])[0])))
        except ValueError:
            raise BadRequest("Offset and limit must be whole numbers.")
        return offset, limit, q

    def _g_jobs(self, m, user) -> None:
        offset, limit, q = self._paging()
        status = (q.get("status") or ["all"])[0]
        if status not in {"open", "closed", "all"}:
            raise BadRequest("Status must be open, closed or all.")
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
            self._json(200, domain.jobs_page(state, status, offset, limit, (q.get("q") or [""])[0][:100]))

    def _g_job(self, m, user) -> None:
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
            job = next((j for j in state["jobs"] if j["id"] == m.group(1)), None)
            if not job:
                self._json(404, {"error": "Job not found."})
                return
            view = domain.job_view(state, job)
        self._json(200, {"job": view})

    def _g_log_page(self, m, user) -> None:
        offset, limit, q = self._paging()
        key = "email_log" if m.group(1) == "outbox" else "audit"
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
            self._json(200, domain.page(state.get(key, []), offset, limit, (q.get("job") or [""])[0]))

    # ----- Billing with Stripe (billing.py) -----

    def _end_stripe_trial(self, company_id: str, subscription_id: str) -> None:
        try:
            billing.end_trial(subscription_id)
        except billing.BillingError as exc:
            # Unmark it so the company's next change asks Stripe again, up to TRIAL_END_TRIES (R6-06).
            tries = []
            self._mutate(company_id, lambda st: tries.append(billing.trial_end_failed(st)), end_trial=False)
            final = bool(tries) and tries[0] >= billing.TRIAL_END_TRIES
            log_json("warn", msg="ending the Stripe trial failed", reason=str(exc)[:200], tries=tries[0] if tries else None)
            alerts.notify("billing:trial-end", f"Couldn't end a Stripe trial ({subscription_id}): {exc}. "
                          + ("Gave up after that many tries; end it in Stripe by hand." if final else "It is tried again on the company's next change."))

    def _g_billing(self, m, user) -> None:
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        self._json(200, {**billing.standing(state), "trial": trial.status(state)})

    def _p_billing_checkout(self, m, ctx: "Ctx") -> None:
        if ctx.company_id == auth.DEMO_COMPANY_ID or not app_url():
            if ctx.company_id != auth.DEMO_COMPANY_ID:
                log_json("warn", msg="Subscribe needs DURACASH_URL set on the server; Stripe sends people back to it",
                         company=ctx.company_id)
            self._json(409, {"error": "The demo account can't subscribe." if ctx.company_id == auth.DEMO_COMPANY_ID
                             else "Subscribing isn't available on this server yet. Contact Duracash support."})
            return
        with company_lock(ctx.company_id):
            state = load_company(ctx.company_id)
        try:
            self._json(200, {"url": billing.checkout_url(state, ctx.company_id, ctx.user.get("email", ""), app_url())})
        except billing.BillingError as exc:
            self._json(409, {"error": str(exc)})

    def _p_billing_portal(self, m, ctx: "Ctx") -> None:
        with company_lock(ctx.company_id):
            state = load_company(ctx.company_id)
        try:
            self._json(200, {"url": billing.portal_url(state, app_url() or "")})
        except billing.BillingError as exc:
            self._json(409, {"error": str(exc)})

    def _p_stripe_webhook(self, m) -> None:
        payload = self._read_raw(MAX_BODY_BYTES)
        try:
            event = billing.verify(payload, self.headers.get("Stripe-Signature", ""))
        except billing.BillingError as exc:
            log_json("warn", msg="stripe webhook refused", reason=str(exc), rid=self._rid)
            self._json(400, {"error": str(exc)})
            return
        company_id, customer = billing.company_hint(event)
        known = company_ids()
        if company_id not in known:
            company_id = ""
            for cid in known:
                with company_lock(cid):
                    if customer and (load_company(cid).get("company", {}).get("billing") or {}).get("customer_id") == customer:
                        company_id = cid
                        break
        if not company_id:
            # Answer 200 anyway: Stripe would retry an error for days, and nothing here can use it.
            log_json("info", msg="stripe event for no known company", type=event.get("type"), rid=self._rid)
            self._json(200, {"received": True})
            return

        def apply(st: dict) -> None:
            line = billing.apply(st, event)
            if line:
                domain.audit(st, "", "billing", line, "Stripe")

        self._mutate(company_id, apply)
        self._json(200, {"received": True})

    # ----- QuickBooks Online, read-only (qbo.py) -----

    def _qbo_redirect_uri(self) -> str:
        return f"{app_url() or ''}/api/qbo/callback"

    def _g_qbo_status(self, m, user) -> None:
        self._json(200, qbo.status(user["company_id"]))

    def _g_qbo_callback(self, m) -> None:
        q = parse_qs(urlparse(self.path).query)
        arg = lambda k: (q.get(k) or [""])[0][:200]
        try:
            if arg("error"):
                raise qbo.QboError("QuickBooks wasn't connected." + (" You cancelled it." if arg("error") == "access_denied" else ""))
            company_id = qbo.finish(arg("state"), arg("code"), arg("realmId"), self._qbo_redirect_uri())
        except qbo.QboError as exc:
            page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                    f'<title>QuickBooks - Duracash</title></head><body><main><h1>QuickBooks</h1><p>{html_escape(str(exc))}</p>'
                    f'<p><a href="/money/payments">Back to Payments</a></p></main></body></html>')
            self._send(400, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        self._mutate(company_id, lambda st: domain.audit(st, "", "qbo_connected", "QuickBooks was connected (read-only).", "QuickBooks"))
        self._send(303, b"", "text/plain", {"Location": "/money/payments"})

    def _g_qbo_reconcile(self, m, user) -> None:
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        try:
            invoices = qbo.query(user["company_id"], "Invoice")
        except qbo.QboError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._json(200, {"rows": qbo.reconcile(state, invoices), "invoices": len(invoices)})

    def _p_qbo_connect(self, m, ctx: "Ctx") -> None:
        if ctx.company_id == auth.DEMO_COMPANY_ID:
            self._json(409, {"error": "The demo account can't connect QuickBooks. Use the sample import on a job instead."})
            return
        try:
            if qbo.configured() and not qbo.redirect_ok(self._qbo_redirect_uri()):
                log_json("warn", msg="Connect QuickBooks needs DURACASH_URL set to the site's https address", company=ctx.company_id)
            self._json(200, {"url": qbo.authorize_url(ctx.company_id, ctx.actor, self._qbo_redirect_uri())})
        except qbo.QboError as exc:
            self._json(409, {"error": str(exc)})

    def _p_qbo_disconnect(self, m, ctx: "Ctx") -> None:
        qbo.disconnect(ctx.company_id)
        self._mutate(ctx.company_id, lambda st: domain.audit(st, "", "qbo_disconnected", f"{ctx.actor} disconnected QuickBooks.", ctx.actor))
        self._json(200, qbo.status(ctx.company_id))

    def _p_qbo_payments(self, m, ctx: "Ctx") -> None:
        days = _number(ctx.body, "days", "the number of days", 1) if "days" in ctx.body else 90
        with company_lock(ctx.company_id):
            state = load_company(ctx.company_id)
        try:
            self._json(200, qbo.read_payments(ctx.company_id, state, days))
        except qbo.QboError as exc:
            self._json(409, {"error": str(exc)})

    def _g_timeline(self, m, user) -> None:
        offset, limit, q = self._paging()
        job_id = (q.get("job") or [""])[0]
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        if not any(j["id"] == job_id for j in state["jobs"]):
            self._json(404, {"error": "Job not found."})
            return
        self._json(200, domain.timeline_page(state, job_id, offset, limit))

    def _g_export(self, m, user) -> None:
        # Everything the company holds, so owners only (R6-05); without the customer-page and
        # signing-link secrets, which would open those pages to whoever holds the file.
        if user["role"] != "owner":
            self._json(403, {"error": "Only an owner can export the company's data."})
            return
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        self._send(200, json.dumps(domain.without_link_secrets(state), indent=2).encode(), "application/json",
                   {"Content-Disposition": f'attachment; filename="duracash-export-{stamp}.json"'})

    def _g_file(self, m, user) -> None:
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        self._serve_file(user["company_id"], state, m.group(1))

    def _g_payer_page(self, m) -> None:
        self._payer_page_get(m.group(1), m.group(2), m.group(3) or "")

    def _g_co_sign(self, m) -> None:
        self._co_sign_page(m.group(1), m.group(2), "")

    def _g_metrics(self, m, user) -> None:
        period = (parse_qs(urlparse(self.path).query).get("period") or ["90"])[0]
        since = None if period == "all" else datetime.now(timezone.utc).date() - timedelta(days=90)
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        self._json(200, metrics.compute(state, since=since))

    def _g_import_settings(self, m, user) -> None:
        tokens = importer.company_tokens(user["company_id"])
        base = self._base_url()
        template = os.environ.get("INBOUND_EMAIL_ADDRESS_TEMPLATE", "")
        # The web addresses the email service and Twilio post to are set up by an owner, and
        # anyone holding one can fill the Inbox: members get the forwarding address and the
        # number, not these (R6-07).
        owner = user["role"] == "owner"
        self._json(200, {
            "email_webhook": f"{base}/api/inbound/email/{tokens['email']}" if owner else "",
            "email_address": template.replace("{token}", tokens["email"]) if "{token}" in template else "",
            "sms_webhook": f"{base}/api/inbound/sms/{tokens['sms']}" if owner else "",
            "sms_number": os.environ.get("TWILIO_NUMBER", ""),
            "sms_signature_check": bool(os.environ.get("TWILIO_AUTH_TOKEN")),
            "email_check": "password" if os.environ.get("INBOUND_WEBHOOK_PASSWORD") else "signature" if os.environ.get("INBOUND_SIGNING_KEY") else "",
            "calendar_url": f"{base}/api/calendar/{tokens['calendar']}.ics",
            "max_file_mb": importer.MAX_FILE_BYTES // (1024 * 1024),
            "mail_configured": mailer.configured(),
            "mail_from": mailer.settings()["from"] if mailer.configured() else "",
            "mail_daily_limit": mailer.settings()["daily_limit"],
            "file_types": sorted(e.lstrip(".") for e in importer.ALLOWED_FILES),
        })

    def _g_calendar(self, m) -> None:
        company_id = importer.company_for_token(m.group(1), "calendar")
        if not company_id:
            self._json(404, {"error": "Not found"})
            return
        with company_lock(company_id):
            state = load_company(company_id)
        self._send(200, calendar_feed.render(state).encode("utf-8"), "text/calendar; charset=utf-8",
                   {"Content-Disposition": 'inline; filename="duracash.ics"'})

    def _g_legal(self, m) -> None:
        self._send(200, legal.page(m.group(1)).encode("utf-8"), "text/html; charset=utf-8")

    def _g_favicon(self, m) -> None:
        self._send(204, b"", "image/x-icon")

    def _accepts_gzip(self) -> bool:
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def _send_compressible(self, code: int, body: bytes, kind: str, headers: dict) -> None:
        headers = {**headers, "Vary": "Accept-Encoding"}
        if kind.startswith("text/html") and "Content-Security-Policy" not in headers:
            headers["Content-Security-Policy"] = _csp(SECURITY_HEADERS["Content-Security-Policy"], body)  # before gzip hides the <style>
        if self._accepts_gzip():
            body, headers["Content-Encoding"] = gzip.compress(body, 6, mtime=0), "gzip"
        self._send(code, body, kind, headers)

    def _static_asset(self, name: str, kind: str) -> None:
        """app.js and styles.css: gzip when the browser accepts it; cached for a year when
        asked for by the current content hash (?v=), otherwise revalidated by ETag."""
        a = asset(name)
        etag = '"' + a["hash"] + '"'
        versioned = parse_qs(urlparse(self.path).query).get("v", [""])[0] == a["hash"]
        headers = {"ETag": etag, "Vary": "Accept-Encoding",
                   "Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-cache"}
        if self.headers.get("If-None-Match") == etag:
            self._send(304, b"", kind, headers)
            return
        body = a["raw"]
        if self._accepts_gzip() and not kind.startswith("image/"):  # PNGs are compressed already
            body, headers["Content-Encoding"] = a["gz"], "gzip"
        self._send(200, body, kind, headers)

    # ----- POST handlers: signing in and public links -----

    def _p_login(self, m) -> None:
        body = self._read_json()
        try:
            user = auth.verify_login(_text(body, "email"), _text(body, "password"), ip=self._client_ip())
        except auth.RateLimited as exc:
            self._json(429, {"error": str(exc)})
            return
        if not user or (not demo_enabled() and user["company_id"] == auth.DEMO_COMPANY_ID):
            self._json(401, {"error": "Email or password is wrong."})
            return
        if user.get("two_step"):
            code = _text(body, "code", limit=40)
            if not code:
                self._json(401, {"error": "Enter the 6-digit code from your authenticator app.", "needs_code": True})
                return
            try:
                ok = auth.totp_check(user["id"], code, self._client_ip())
            except auth.RateLimited as exc:
                self._json(429, {"error": str(exc), "needs_code": True})
                return
            if not ok:
                self._json(401, {"error": "That code didn't work. Codes change every 30 seconds; a recovery code works once.", "needs_code": True})
                return
        self._json(200, {"user": user}, self._set_cookie(auth.create_session(user)))

    def _p_signup(self, m) -> None:
        body = self._read_json()
        if body.get("accept_terms") is not True:
            self._json(400, {"error": "To create an account, accept the Terms of Service and the Privacy Policy."})
            return
        # Five new accounts an hour from one network (R6-05). Someone on this machine isn't counted.
        ip = self._client_ip()
        if not _local(ip) and not _within_limit(f"signup:{ip}", 5, 3600):
            self._json(429, {"error": "Too many new accounts from this network. Try again in an hour."})
            return
        try:
            company_id = "co-" + secrets.token_hex(4)
            user = auth.create_user(_text(body, "email"), _text(body, "password"), _text(body, "name"), company_id,
                                    terms_version=legal.TERMS_VERSION)
            # The company name can wait (R2-02): until it is typed in Account, it is the person's name.
            fresh = empty_state(_text(body, "company_name").strip() or str(user.get("name") or "").strip() or "New company")
            trial.start(fresh["company"])
            with company_lock(company_id):
                save_company(company_id, fresh)
            token = auth.create_session(user)
            verification = self._send_verification(user)
            self._json(200, {"user": user, "verification": verification}, self._set_cookie(token))
        except ValueError as exc:
            # An email that already has an account: the page offers "Sign in instead".
            self._json(400, {"error": str(exc), **({"exists": True} if "already exists" in str(exc) else {})})

    def _p_demo(self, m) -> None:
        """Look around before signing up (R2-06): a session as the demo owner, no password typed."""
        user = demo_user()
        if not user:
            self._json(404, {"error": "There is no sample company on this server."})
            return
        self._json(200, {"user": user}, self._set_cookie(auth.create_session(user)))

    def _p_logout(self, m) -> None:
        auth.destroy_session(self._token())
        self._json(200, {"ok": True}, self._set_cookie(None))

    def _p_forgot(self, m) -> None:
        body = self._read_json()
        url = app_url()
        if not (mailer.configured() and url):
            contact = (os.environ.get("LEGAL_EMAIL") or "").strip()
            self._json(200, {"message": "Password reset by email isn't set up on this server yet. "
                             + (f"Email {contact} to reset it." if contact else "Ask the Duracash team to reset it.")})
            return
        requested = _text(body, "email", limit=300)

        def send_reset() -> None:
            found = auth.password_reset_token(requested)
            if found:
                raw, account = found
                self._system_email(
                    account["email"], "Reset your Duracash password",
                    f"Someone asked to reset the password for {account['email']} on Duracash.\n\n"
                    f"Choose a new password here (the link works once, for one hour):\n{url}/#reset={raw}\n\n"
                    "If it wasn't you, ignore this email; your password stays the same.",
                    company_id=account["company_id"],
                )

        # All the work happens in the background, so the answer takes the same time
        # whether or not the account exists.
        threading.Thread(target=send_reset, daemon=True).start()
        self._json(200, {"message": "If an account exists for that email, a reset link is on its way. It works for one hour."})

    def _p_reset_password(self, m) -> None:
        body = self._read_json()
        try:
            user = auth.reset_password(body.get("token"), body.get("password"))
        except auth.AuthError as exc:
            self._json(400, {"error": str(exc)})
            return
        if user.get("two_step"):
            # The emailed link proves the inbox, not the phone: two-step accounts still sign in
            # with the code (R6-05).
            self._json(200, {"ok": True, "needs_code": True, "email": user["email"],
                             "message": "Password changed. Sign in with your new password and your authenticator code."})
            return
        self._json(200, {"user": user}, self._set_cookie(auth.create_session(user)))

    def _p_verify(self, m) -> None:
        body = self._read_json()
        try:
            user = auth.verify_email(body.get("token"))
        except auth.AuthError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"user": user})

    def _p_invite_info(self, m) -> None:
        body = self._read_json()
        info = auth.invite_info(body.get("token"))
        if not info:
            self._json(404, {"error": "This invite is invalid or has expired. Ask for a new one."})
            return
        with company_lock(info["company_id"]):
            company = load_company(info["company_id"]).get("company", {})
        self._json(200, {"email": info["email"], "role": info["role"], "company_name": company.get("name", ""),
                         "invited_by": info["invited_by"]})

    def _p_accept_invite(self, m) -> None:
        body = self._read_json()
        if body.get("accept_terms") is not True:
            self._json(400, {"error": "To join, accept the Terms of Service and the Privacy Policy."})
            return
        try:
            user = auth.accept_invite(body.get("token"), body.get("name"), body.get("password"), terms_version=legal.TERMS_VERSION)
        except auth.AuthError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._mutate(user["company_id"], lambda st: domain.audit(
            st, "", "team_join", f"{user['name']} ({user['email']}) joined the team as {user['role']}.", user["name"]))
        self._json(200, {"user": user}, self._set_cookie(auth.create_session(user)))

    def _p_inbound_email(self, m) -> None:
        self._inbound_email(m.group(1))

    def _p_inbound_sms(self, m) -> None:
        self._inbound_sms(m.group(1))

    def _p_mail_events(self, m) -> None:
        self._mail_events(m.group(1))

    def _p_payer_received(self, m) -> None:
        self._payer_received(m.group(1), m.group(2))

    def _p_payer_approve(self, m) -> None:
        self._payer_form(m.group(1), m.group(2), "approve")

    def _p_payer_question(self, m) -> None:
        self._payer_form(m.group(1), m.group(2), "question")

    def _p_co_sign(self, m) -> None:
        self._co_sign_post(m.group(1), m.group(2))

    def _p_share(self, m) -> None:
        self._share_target()

    # ----- POST handlers: signed in -----

    def _p_import_file(self, m, user) -> None:
        self._import_file(user, user.get("name") or user.get("email") or "owner")

    def _p_helper_ask(self, m, ctx: "Ctx") -> None:
        with company_lock(ctx.company_id):
            state = load_company(ctx.company_id)
        try:
            reply = helper.answer(_text(ctx.body, "q"), state, ctx.company_id, ctx.user["id"], _text(ctx.body, "view"), _text(ctx.body, "job_id"))
        except domain.GateError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._json(200, reply)

    def _p_helper_feedback(self, m, ctx: "Ctx") -> None:
        try:
            helper.feedback(_text(ctx.body, "log_id"), ctx.body.get("helpful") is True, _text(ctx.body, "note"), ctx.company_id)
        except domain.GateError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._json(200, {"ok": True})

    def _p_helper_admin(self, m, ctx: "Ctx") -> None:
        if not is_helper_admin(ctx.user):
            self._json(403, {"error": "Only the Duracash team can edit helper answers."})
            return
        try:
            if m.group(1) == "answers":
                saved = helper.save_answer(ctx.body, ctx.user.get("email", ""))
                self._json(200, {"saved": saved, **helper.review()})
            else:
                helper.resolve(_text(ctx.body, "normalized"))
                self._json(200, helper.review())
        except domain.GateError as exc:
            self._json(409, {"error": str(exc)})

    def _p_resend_verify(self, m, ctx: "Ctx") -> None:
        user = ctx.user
        if user.get("email_verified"):
            self._json(409, {"error": "Your email is already verified."})
            return
        result = self._send_verification(user)
        if result != "sent":
            self._json(409, {"error": f"No verification email was sent: {result}."})
            return
        self._json(200, {"ok": True, "message": f"A verification link is on its way to {user['email']}."})

    def _p_password(self, m, ctx: "Ctx") -> None:
        try:
            auth.change_password(ctx.user["id"], ctx.body.get("current"), ctx.body.get("new"), keep_session=self._token())
        except auth.RateLimited as exc:
            self._json(429, {"error": str(exc)})
            return
        except auth.AuthError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"ok": True, "message": "Password changed. Every other device is signed out."})

    def _p_payments_csv(self, m, ctx: "Ctx") -> None:
        # Read a QuickBooks payments export and propose matches; nothing is recorded (P2-1). The
        # export comes pasted (csv), chosen on Payments as an Excel file (xlsx, base64), or as a
        # file already in the Inbox (file_id, R5-04).
        file_id = _text(ctx.body, "file_id")
        xlsx = _text(ctx.body, "xlsx", limit=4_000_000)
        with company_lock(ctx.company_id):
            state = load_company(ctx.company_id)
        if file_id:
            meta = next((f for f in state.get("files", []) if f["id"] == file_id), None)
            if not meta or meta["ext"] not in {".csv", ".xlsx", ".txt"}:
                raise domain.GateError("That file isn't a payments export.")
            try:
                text = qbo_csv.text_of(meta["ext"], importer.file_path(ctx.company_id, meta).read_bytes())
            except OSError:
                raise domain.GateError("That file is no longer on the server. Upload it again.")
        elif xlsx:
            try:
                raw = base64.b64decode(xlsx, validate=True)
            except ValueError:
                raise BadRequest("Send the Excel file again.")
            text = qbo_csv.xlsx_to_csv(raw)
            if not text:
                raise domain.GateError("That Excel file couldn't be read. In QuickBooks, export the report to CSV instead.")
        else:
            text = _text(ctx.body, "csv", limit=2_000_000)
        parsed = qbo_csv.parse(text)
        self._json(200, {"rows": qbo_csv.propose(state, parsed["rows"]), "warnings": parsed["warnings"]})

    def _m_payments_apply(self, state: dict, m, ctx: "Ctx") -> None:
        picks = ctx.body.get("payments")
        if not isinstance(picks, list) or not picks or len(picks) > 500:
            raise BadRequest("Send the payments to record.")
        results = []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            ref = str(pick.get("reference") or "")[:60]
            # The page's row number comes back with its result, so a refused row stays on the page (R5-03).
            key = pick.get("key") if isinstance(pick.get("key"), (int, str)) else None
            try:
                domain.match_payment(state, str(pick.get("job_id") or ""), pick.get("amount_cents"), ref,
                                     ctx.actor, invoice_id=str(pick.get("invoice_id") or "") or None,
                                     received_on=str(pick.get("date") or "") or None,
                                     qbo_payment_id=str(pick.get("qbo_id") or "") or None)
                results.append({"key": key, "reference": ref, "ok": True})
            except domain.GateError as exc:
                results.append({"key": key, "reference": ref, "ok": False, "error": str(exc)})
        ctx.reply["import_results"] = results

    def _p_summary_email(self, m, ctx: "Ctx") -> None:
        if ctx.company_id == auth.DEMO_COMPANY_ID or not ctx.user.get("email_verified"):
            self._json(409, {"error": "Confirm your email address first (Settings, My account)." if ctx.company_id != auth.DEMO_COMPANY_ID
                             else "The demo account doesn't send email."})
            return
        first = datetime.now(timezone.utc).date().replace(day=1) - timedelta(days=1)
        with company_lock(ctx.company_id):
            state = load_company(ctx.company_id)
        subject, text = metrics.month_summary(state, first.year, first.month)
        result = self._system_email(ctx.user["email"], subject, text, ctx.company_id)
        if result != "sent":
            self._json(409, {"error": f"Not sent: {result}."})
            return
        self._json(200, {"ok": True, "message": f"Last month's summary is on its way to {ctx.user['email']}."})

    def _p_two_step(self, m, ctx: "Ctx") -> None:
        step = m.group(1)
        try:
            if step == "begin":
                setup = auth.totp_begin(ctx.user["id"])
                # A QR code of the same key, for the phone's authenticator app to scan (R6-05).
                self._json(200, {**setup, "qr": qr.data_uri(setup["uri"])})
            elif step == "enable":
                codes = auth.totp_enable(ctx.user["id"], _text(ctx.body, "code", limit=40))
                self._json(200, {"ok": True, "recovery_codes": codes,
                                 "message": "Two-step sign-in is on. Keep the recovery codes somewhere safe; each works once."})
            else:
                auth.totp_disable(ctx.user["id"], ctx.body.get("password"), _text(ctx.body, "code", limit=40))
                self._json(200, {"ok": True, "message": "Two-step sign-in is off."})
        except auth.RateLimited as exc:
            self._json(429, {"error": str(exc)})
        except auth.AuthError as exc:
            self._json(400, {"error": str(exc)})

    def _p_onboarding_dismiss(self, m, ctx: "Ctx") -> None:
        """Hide the setup checklist for the signed-in person, or show it again with {"show": true}.
        Kept on their own account, not the company (an old company-wide flag is ignored)."""
        try:
            user = auth.set_onboarding_dismissed(ctx.user["id"], ctx.body.get("show") is not True)
        except auth.AuthError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"user": user})

    def _m_import_text(self, state: dict, m, ctx: "Ctx") -> None:
        file_now = (parse_qs(urlparse(self.path).query).get("file") or [""])[0] == "1"
        self._import_text(state, ctx.body, ctx.actor if file_now else None)

    def _m_inbox(self, state: dict, m, ctx: "Ctx") -> None:
        item_id, verb, body, actor = m.group(1), m.group(2), ctx.body, ctx.actor
        if verb == "file":
            importer.file_item(state, item_id, _text(body, "job_id"), _text(body, "kind"), actor)
        elif verb == "dismiss":
            importer.dismiss_item(state, item_id, actor)
        elif verb == "undo":
            importer.undo_item(state, item_id, actor)
        elif verb == "mark-done":
            importer.file_and_mark_done(state, item_id, actor)
        else:
            raise UnknownAction()

    def _p_sov_parse(self, m, ctx: "Ctx") -> None:
        # Read pasted rows; nothing is saved until the schedule itself is saved.
        self._json(200, sov.parse_rows(_text(ctx.body, "text", limit=200_000)))

    def _p_outbox_retry(self, m, ctx: "Ctx") -> None:
        self._json(*outbox.retry_email(SITE, ctx.company_id, ctx.user, m.group(1), self._rid))

    def _p_rotate(self, m, ctx: "Ctx") -> None:
        kind = _text(ctx.body, "kind")
        if kind not in {"email", "sms", "calendar"}:
            raise BadRequest("Kind must be email, sms or calendar.")
        importer.rotate_token(ctx.company_id, kind)
        self._json(200, {"ok": True})

    # ----- accounts and team -----

    def _system_email(self, to: str, subject: str, body: str, company_id: str = "") -> str:
        return send_system_email(to, subject, body, company_id, self._rid)

    def _send_verification(self, user: dict) -> str:
        url = app_url()
        if user["company_id"] == auth.DEMO_COMPANY_ID:
            return "the demo account isn't verified"
        if not (mailer.configured() and url):
            if not url:
                log_json("warn", msg="no confirmation email: DURACASH_URL is not set on the server", company=user["company_id"])
            return EMAIL_NOT_SET_UP
        if not _within_limit(f"verify:{user['id']}", 3, 3600):
            return "three links were already sent in the last hour; use the newest, or try later"
        raw = auth.verify_email_token(user["id"])
        return self._system_email(
            user["email"], "Confirm your email for Duracash",
            f"Hi {user['name']},\n\nConfirm that this is your email address (the link works for 3 days):\n{url}/#verify={raw}\n\n"
            "Duracash emails your pay apps from this address's account, with replies coming back to it, "
            "so we check it first. If you didn't sign up, ignore this email.",
            company_id=user["company_id"],
        )

    # The five team routes are owners only by the route table (R6-08); each answers with the team.

    def _team_change(self, ctx: "Ctx", run) -> None:
        """run(user) makes the change and returns (what the Activity log says, extra reply fields)."""
        if ctx.company_id == auth.DEMO_COMPANY_ID:
            self._json(403, {"error": "The demo account's team can't be changed."})
            return
        try:
            detail, note = run(ctx.user)
        except auth.AuthError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._mutate(ctx.company_id, lambda st: domain.audit(st, "", "team", detail, ctx.user["name"]))
        self._json(200, {**auth.team(ctx.company_id), **(note or {})})

    def _p_team_invite(self, m, ctx: "Ctx") -> None:
        def invite(user: dict) -> tuple:
            company_id = user["company_id"]
            if mailer.configured() and app_url() and not user.get("email_verified"):
                raise auth.AuthError("Confirm your own email address before inviting people.")
            if not _within_limit(f"invite:{company_id}", 20, 24 * 3600):
                raise auth.AuthError("20 invites a day is the limit. Try again tomorrow.")
            raw, invite = auth.create_invite(company_id, ctx.body.get("email"), ctx.body.get("role") or "member", user["name"], actor_id=user["id"])
            url = app_url()
            note = {"invite_path": f"/#invite={raw}", "invite_link": f"{url}/#invite={raw}" if url else None, "emailed": False,
                    "email_status": EMAIL_NOT_SET_UP if not url else "not sent"}
            if not url:
                log_json("warn", msg="invite not emailed: DURACASH_URL is not set on the server", company=company_id)
            if url:
                with company_lock(company_id):
                    company_name = load_company(company_id).get("company", {}).get("name", "")
                subject, text = mailer.invite_email(user["name"], user["email"], company_name, invite["role"], f"{url}/#invite={raw}")
                status = self._system_email(invite["email"], subject, text, company_id=company_id)
                note.update({"emailed": status == "sent", "email_status": status})
            return f"{user['name']} invited {invite['email']} as {invite['role']}.", note

        self._team_change(ctx, invite)

    def _p_team_revoke(self, m, ctx: "Ctx") -> None:
        def revoke(user: dict) -> tuple:
            auth.revoke_invite(user["company_id"], m.group(1), actor_id=user["id"])
            return f"{user['name']} revoked an invite.", None

        self._team_change(ctx, revoke)

    def _p_team_role(self, m, ctx: "Ctx") -> None:
        def role(user: dict) -> tuple:
            changed = auth.set_role(user["company_id"], m.group(1), ctx.body.get("role"), actor_id=user["id"])
            return f"{user['name']} made {changed['name']} {changed['role']}.", None

        self._team_change(ctx, role)

    def _p_team_remove(self, m, ctx: "Ctx") -> None:
        def remove(user: dict) -> tuple:
            if m.group(1) == user["id"]:
                raise auth.AuthError("You can't remove yourself. Ask another owner.")
            removed = auth.remove_user(user["company_id"], m.group(1), actor_id=user["id"])
            # They knew the inbound and calendar addresses, so those change too.
            for kind in ("email", "sms", "calendar"):
                importer.rotate_token(user["company_id"], kind)
            return (f"{user['name']} removed {removed['name']} ({removed['email']}) from the team. "
                    "The inbound email, text and calendar addresses were changed."), {"rotated": True}

        self._team_change(ctx, remove)

    def _p_account_delete(self, m, ctx: "Ctx") -> None:
        if ctx.company_id == auth.DEMO_COMPANY_ID:
            self._json(403, {"error": "The demo account's team can't be changed."})
            return
        try:
            auth.require_owner(ctx.company_id, ctx.user["id"])
        except auth.AuthError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._delete_account(ctx.body, ctx.user)

    def _delete_account(self, body: dict, user: dict) -> None:
        company_id = user["company_id"]
        if not auth.check_password(user["id"], body.get("password")):
            self._json(409, {"error": "Password is wrong. Nothing was deleted."})
            return
        with company_lock(company_id):
            name = load_company(company_id).get("company", {}).get("name", "")
        if _text(body, "confirm").strip() != name:
            self._json(409, {"error": "Type your company name exactly to confirm. Nothing was deleted."})
            return
        removed = delete_company_data(company_id)
        log_json("info", msg="company deleted", company=company_id, users=removed, rid=self._rid)
        self._json(200, {"ok": True, "message": "Your company and its users were deleted. Backups that still hold it expire on their own."},
                   self._set_cookie(None))

    def _company_overview(self) -> list:
        users = auth.all_users()
        out = []
        for company_id in company_ids():
            with company_lock(company_id):
                try:
                    state = load_company(company_id)
                except (OSError, ValueError, CompanyMissing):
                    continue
            members = [u for u in users if u["company_id"] == company_id]
            if not members:
                continue
            out.append({
                "company_id": company_id,
                "name": state.get("company", {}).get("name", ""),
                "users": len(members),
                "signed_up": min((u.get("created_at", "") for u in members), default=""),
                "jobs": len(state.get("jobs", [])),
                "pay_apps_sent": sum(1 for j in state.get("jobs", []) for i in j.get("invoices", []) if i.get("sent_at")),
                "trial": trial.status(state),
                "billing": billing.standing(state)["status"] or ("none" if billing.configured() else ""),
                "activation_days": activation_days(state),
                "demo": company_id == auth.DEMO_COMPANY_ID,
            })
        return out

    # ----- email delivery -----

    # ----- importing -----

    def _import_file(self, user: dict, actor: str) -> None:
        query = parse_qs(urlparse(self.path).query)
        job_id = (query.get("job") or [""])[0] or None
        file_now = bool(job_id) and (query.get("file") or [""])[0] == "1"
        # What the person said it is (the waiver page's "Take a photo" files a lien waiver); else guessed.
        kind = (query.get("kind") or [""])[0]
        # A photo taken for a missing paperwork item is attached to it, and one taken of a signed
        # change order marks it signed, in the same step as the upload (R3-05, R3-06).
        attach_to = (query.get("attach") or [""])[0] if file_now else ""
        signed_co = (query.get("co") or [""])[0] if file_now else ""
        filename = unquote(self.headers.get("X-Filename", "") or "upload")
        data = self._read_raw(importer.MAX_FILE_BYTES)
        user = self._session_user()  # still signed in now the upload has arrived?
        if not user:
            self._json(401, {"error": "Sign in required."})
            return
        actor = user.get("name") or user.get("email") or "owner"
        company_id = user["company_id"]
        # Check and write the file before taking the state lock, so a big upload
        # never holds up other companies; delete it again if the import is refused.
        try:
            if filename.lower().endswith(".eml"):
                parsed = importer.parse_email(data)
                stored, skipped = self._store_email_files(company_id, parsed, actor, raw=data, raw_name=filename)
            else:
                parsed, skipped = None, []
                stored = [importer.store_bytes(company_id, filename, data, actor)]
        except domain.GateError as exc:
            self._json(409, _gate(exc))
            return
        # A QuickBooks payments export dropped in the Inbox: the card offers to read it on Payments (R5-04).
        payment_rows = 0
        if parsed is None and stored[0]["ext"] in {".csv", ".xlsx"} and not file_now:
            payment_rows = qbo_csv.looks_like_payments(qbo_csv.text_of(stored[0]["ext"], data))

        # Uploaded in the Inbox with "Which job?" answered: suggested there, filed by a person (R5-07).
        reason = "Added from the job page." if file_now else "was uploaded for this job"

        def add(state: dict) -> None:
            for meta in stored:
                importer.register_file(state, meta)
            ids = [m["id"] for m in stored]
            if parsed is not None:
                item = importer.add_item(
                    state, "upload", text=parsed.get("text", ""), sender=parsed.get("from", ""), subject=parsed.get("subject", ""),
                    file_ids=ids, job_id=job_id, sent_at=parsed.get("sent_at", ""),
                    extra={"skipped_attachments": skipped} if skipped else None, job_reason=reason)
            else:
                meta = stored[0]
                text = data.decode("utf-8", "replace") if meta["ext"] in {".txt", ".csv"} else ""
                item = importer.add_item(state, "upload", text=text, subject=meta["name"], file_ids=ids, job_id=job_id,
                                         extra={"payments_csv": True, "payment_rows": payment_rows} if payment_rows else None,
                                         job_reason=reason)
            if file_now:
                # Dropped on a job page: the person already chose the job.
                filed = importer.file_item(state, item["id"], job_id, kind if kind in importer.KINDS else item["kind"], actor)
                if attach_to:
                    domain.attach_record_to_packet(state, job_id, attach_to, filed["record_ids"][0], actor)
                if signed_co:
                    domain.mark_co_signed(state, job_id, signed_co, filed["record_ids"][0], actor)

        status, payload = self._mutate(company_id, add)
        if status != 200:
            importer.discard(company_id, stored)
        self._json(status, payload)

    def _store_email_files(self, company_id: str, parsed: dict, actor: str, raw: bytes | None = None, raw_name: str = "") -> tuple:
        """Write an email's own file and its attachments to disk; refused attachment types are listed, not fatal."""
        stored, skipped = [], []
        if raw is not None and raw_name:
            stored.append(importer.store_bytes(company_id, raw_name, raw, actor))
        for att in parsed.get("attachments", []):
            try:
                stored.append(importer.store_bytes(company_id, att.get("filename") or "attachment", att.get("data") or b"", actor))
            except domain.GateError as exc:
                skipped.append(str(exc))
        return stored, skipped

    def _import_text(self, state: dict, body: dict, file_as: str | None = None) -> None:
        """Add pasted text to the Inbox; with file_as (a person) and a job, file it straight away."""
        before = {i["id"] for i in state.get("inbox", [])}
        self._import_text_items(state, body)
        job_id = _text(body, "job_id") or None
        if file_as and job_id:
            for item in [i for i in state.get("inbox", []) if i["id"] not in before]:
                importer.file_item(state, item["id"], job_id, item["kind"], file_as)

    def _import_text_items(self, state: dict, body: dict) -> None:
        # Pasted notes or a long email thread can be long; the 1 MB body cap still applies.
        text = _text(body, "text", limit=200_000).strip()
        source = _text(body, "source", "paste")
        if source not in {"paste", "email", "sms", "notes"}:
            source = "paste"
        if not text:
            raise domain.GateError("Paste some text first.")
        job_id = _text(body, "job_id") or None
        if source == "notes":
            chunks = importer.split_notes(text)
            if not chunks:
                raise domain.GateError("Paste some text first.")
            for chunk in chunks:
                importer.add_item(state, "notes", text=chunk["text"], job_id=job_id, note_date=chunk["date"])
            return
        sender = ""
        subject = ""
        if source in {"email", "paste"}:
            header = importer.pasted_headers(text, strict=source == "paste")
            if header is not None:
                above, block = header
                parsed = importer.parse_email(block.encode("utf-8"))
                sender, subject = parsed["from"], parsed["subject"]
                text = "\n\n".join(t for t in (above, parsed["text"]) if t) or text
        importer.add_item(state, source, text=text, sender=sender, subject=subject, job_id=job_id)

    def _inbound_email(self, token: str) -> None:
        company_id = importer.company_for_token(token, "email")
        if not company_id:
            self._json(404, {"error": "Not found"})
            return
        if self._inbound_refused(token):
            return
        data = self._read_raw(MAX_INBOUND_BYTES)
        key = os.environ.get("INBOUND_SIGNING_KEY", "")
        if key and not _signed_by_provider(data, self.headers.get("Content-Type") or "", key):
            self._json(401, {"error": "Signature check failed."})
            return
        if demo_locked(company_id):
            self._json(200, {"ok": True, "ignored": "the demo is read-only"})
            return
        # Keep the header as sent: a multipart boundary is case-sensitive.
        try:
            parsed = importer.parse_inbound_email(data, self.headers.get("Content-Type") or "")
        except importer.BadPayload as exc:
            raise BadRequest(str(exc))
        stored, skipped = self._store_email_files(company_id, parsed, "inbound email")

        def add(state: dict) -> None:
            for meta in stored:
                importer.register_file(state, meta)
            item = importer.add_item(
                state, "email", text=parsed.get("text", ""), sender=parsed.get("from", ""), subject=parsed.get("subject", ""),
                file_ids=[m["id"] for m in stored], sent_at=parsed.get("sent_at", ""),
                extra={"skipped_attachments": skipped} if skipped else None)
            # A payer answering a pay app we sent files itself to that job (P1-3).
            importer.capture_reply(state, item)

        status, payload = self._mutate(company_id, add)
        if status != 200:
            importer.discard(company_id, stored)
            self._json(status, payload)
            return
        self._json(200, {"ok": True})

    def _mail_events(self, token: str) -> None:
        """Bounce and complaint webhook from the mail provider (P1-4); see mail_events.py."""
        secret = os.environ.get("MAIL_EVENTS_TOKEN", "")
        if len(secret) < 16 or not hmac.compare_digest(token, secret):
            self._json(404, {"error": "Not found"})
            return
        try:
            body = json.loads(self._read_raw(MAX_BODY_BYTES) or b"null")
        except ValueError:
            raise BadRequest("Send JSON.")
        failures = mail_events.normalize(body)
        marked = 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if failures:
            # The provider doesn't know our companies: find each address's latest sent email.
            for company_id in company_ids():
                with company_lock(company_id):
                    state = load_company(company_id)
                    hit = [f for f in failures if mail_events.apply(state, f["email"], f["reason"], now)]
                    if hit:
                        save_company(company_id, state)
                        marked += len(hit)
        log_json("info", msg="mail events", received=len(failures), marked=marked, rid=self._rid)
        self._json(200, {"ok": True, "marked": marked})

    def _serve_file(self, company_id: str, state: dict, file_id: str) -> None:
        meta = next((f for f in state.get("files", []) if f["id"] == file_id), None)
        target = importer.file_path(company_id, meta) if meta else None
        if not meta or not target.is_file():
            self._json(404, {"error": "File not found."})
            return
        kind, inline = importer.ALLOWED_FILES.get(meta["ext"], ("application/octet-stream", False))
        disposition = "inline" if inline else "attachment"
        ascii_name = meta["name"].encode("ascii", "replace").decode().replace('"', "")
        headers = {
            "Content-Disposition": f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(meta["name"])}',
            "Cache-Control": "private, max-age=300",
        }
        if meta["ext"] != ".pdf":
            # Browsers' PDF viewers won't open inside a sandboxed document, so PDFs go without it;
            # every other type is an image or a download, where the sandbox costs nothing.
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
        self._send(200, target.read_bytes(), kind, headers)

    def _payer_lookup(self, company_id: str, token: str) -> tuple:
        """(state, job, invoice) for a payer link, or Nones. Deleted companies have no links."""
        if company_id in deleted_companies() or not company_exists(company_id):
            return None, None, None
        with company_lock(company_id):
            state = load_company(company_id)
        job, invoice = payer_page.find(state, token)
        return (state, job, invoice) if invoice else (None, None, None)

    def _payer_page_get(self, company_id: str, token: str, rest: str) -> None:
        """The payer's page for one pay app (P2-4); no account, the link is the key."""
        state, job, invoice = self._payer_lookup(company_id, token)
        if not invoice:
            self._send(404, NOT_FOUND_PAGE, "text/html; charset=utf-8")
            return
        private = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
        if rest == "/pdf" and invoice.get("status") != "void":
            pdf, name = payapp_pdf.for_invoice(state, job["id"], invoice["id"])
            self._send(200, pdf, "application/pdf", {**private, "Content-Disposition": f'inline; filename="{name}"'})
        elif rest.startswith("/file/") and invoice.get("status") != "void":
            file_id = rest.rsplit("/", 1)[-1]
            if file_id not in payer_page.file_ids(state, job, invoice):
                self._send(404, NOT_FOUND_PAGE, "text/html; charset=utf-8")
                return
            self._serve_file(company_id, state, file_id)
        elif rest:
            self._send(404, NOT_FOUND_PAGE, "text/html; charset=utf-8")
        else:
            asked = (parse_qs(urlparse(self.path).query).get("asked") or [""])[0] == "1"
            page = payer_page.render(state, job, invoice, f"/p/{company_id}.{token}", asked=asked)
            self._send_compressible(200, page.encode("utf-8"), "text/html; charset=utf-8", private)

    def _payer_form(self, company_id: str, token: str, form: str) -> None:
        """"Approve for payment" and "Send a question" on the customer's page (R4-08). A refusal
        shows the page again with why; a question is emailed to the company's owners."""
        state, job, invoice = self._payer_lookup(company_id, token)
        if not invoice:
            self._send(404, NOT_FOUND_PAGE, "text/html; charset=utf-8")
            return
        fields = dict(parse_qsl(self._read_raw(8_000).decode("utf-8", "replace")))
        link = f"/p/{company_id}.{token}"
        if demo_locked(company_id):
            self._send(303, b"", "text/plain", {"Location": link})
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        asked: dict = {}

        def act(st: dict) -> None:
            j, inv = payer_page.find(st, token)
            if form == "approve":
                payer_page.approve(st, j, inv, fields.get("name", ""), fields.get("expected_on", ""), now)
            else:
                asked.update(payer_page.ask(st, j, inv, fields.get("name", ""), fields.get("email", ""), fields.get("question", "")),
                             job_id=j["id"], job_name=j["name"], number=inv["number"])

        status, payload = self._mutate(company_id, act)
        if status != 200:
            state, job, invoice = self._payer_lookup(company_id, token)
            errors = payload.get("fields") or {"form": payload.get("error", "That didn't go through.")}
            page = payer_page.render(state, job, invoice, link, errors=errors, form=form)
            self._send_compressible(400, page.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
            return
        if asked:
            threading.Thread(target=email_owners_question, args=(company_id, asked), daemon=True).start()
        self._send(303, b"", "text/plain", {"Location": link + ("?asked=1" if asked else "")})

    def _payer_received(self, company_id: str, token: str) -> None:
        state, job, invoice = self._payer_lookup(company_id, token)
        if not invoice:
            self._send(404, NOT_FOUND_PAGE, "text/html; charset=utf-8")
            return
        raw = self._read_raw(4_000)
        if demo_locked(company_id):
            self._send(303, b"", "text/plain", {"Location": f"/p/{company_id}.{token}"})
            return
        name = dict(parse_qsl(raw.decode("utf-8", "replace"))).get("name", "")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        def mark(st: dict) -> None:
            j, inv = payer_page.find(st, token)
            if inv:
                payer_page.mark_received(st, j, inv, name, now)

        self._mutate(company_id, mark)
        self._send(303, b"", "text/plain", {"Location": f"/p/{company_id}.{token}"})

    def _share_target(self) -> None:
        """Photos, PDFs and text shared to the installed app from the phone's share menu (P3).
        They land in the Inbox like any upload; nothing is filed without a person. Shared while
        signed out, they are held for this browser until someone signs in (R6-10)."""
        user = self._session_user()
        data = self._read_raw(MAX_INBOUND_BYTES)
        parsed = importer.parse_share(data, self.headers.get("Content-Type") or "")
        if not user:
            key = None
            if _within_limit(f"share-hold:{self._client_ip()}", SHARE_HOLDS_PER_HOUR, 3600):
                key = importer.hold_share(parsed)
            headers = {"Location": "/inbox"}
            if key:
                headers.update(self._share_cookie(key))
            self._send(303, b"", "text/plain", headers)
            return
        if not read_only_reason(user["company_id"]):
            self._add_shared(user["company_id"], parsed)
        # Read-only: the Inbox page shows why; nothing is stored.
        self._send(303, b"", "text/plain", {"Location": "/inbox"})

    def _share_cookie(self, key: str | None) -> dict:
        secure = "; Secure" if self._secure_cookie() else ""
        age = importer.HOLD_MINUTES * 60 if key else 0
        return {"Set-Cookie": f"{SHARE_COOKIE}={key or ''}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{secure}"}

    def _held_key(self) -> str | None:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = jar.get(SHARE_COOKIE)
        return morsel.value if morsel else None

    def _add_shared(self, company_id: str, parsed: dict) -> tuple:
        """Store what a phone shared and put it in the company's Inbox: one item per file, or one
        for the text alone. Returns (status, reply)."""
        stored, skipped = [], []
        for f in parsed["files"]:
            try:
                stored.append(importer.store_bytes(company_id, f["filename"] or "shared file", f["data"], "share"))
            except domain.GateError as exc:
                skipped.append(str(exc))
        text = "\n".join(x for x in (parsed["text"], parsed["url"]) if x).strip()

        def add(state: dict) -> None:
            for meta in stored:
                importer.register_file(state, meta)
                importer.add_item(state, "upload", subject=parsed["title"] or meta["name"], text=text, file_ids=[meta["id"]],
                                  extra={"skipped_attachments": skipped} if skipped else None)
            if not stored and text:
                importer.add_item(state, "paste", subject=parsed["title"], text=text)

        status, payload = self._mutate(company_id, add)
        if status != 200:
            importer.discard(company_id, stored)
        return status, payload

    def _p_share_claim(self, m, ctx: "Ctx") -> None:
        """Finish adding what was shared before sign-in to this company's Inbox, or {"discard": true}."""
        key = self._held_key()
        held = importer.held_share(key)
        if not held:
            self._json(404, {"error": "Nothing shared is waiting. Share it again."}, self._share_cookie(None))
            return
        if ctx.body.get("discard") is True:
            importer.drop_share(key)
            self._json(200, {"ok": True}, self._share_cookie(None))
            return
        status, payload = self._add_shared(ctx.company_id, held)
        if status == 200:
            importer.drop_share(key)
        self._json(status, payload, self._share_cookie(None) if status == 200 else None)

    def _co_lookup(self, company_id: str, token: str) -> tuple:
        if company_id in deleted_companies() or not company_exists(company_id):
            return None, None, None
        with company_lock(company_id):
            state = load_company(company_id)
        job, co = payer_page.find_co(state, token)
        return (state, job, co) if co else (None, None, None)

    def _co_sign_page(self, company_id: str, token: str, error: str) -> None:
        """The change-order signing page (P2-3); the link is the key."""
        state, job, co = self._co_lookup(company_id, token)
        if not co:
            self._send(404, NOT_FOUND_PAGE, "text/html; charset=utf-8")
            return
        page = payer_page.render_co(state, job, co, f"/s/{company_id}.{token}", error)
        self._send_compressible(200 if not error else 400, page.encode("utf-8"), "text/html; charset=utf-8",
                                {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

    def _co_sign_post(self, company_id: str, token: str) -> None:
        state, job, co = self._co_lookup(company_id, token)
        if not co:
            self._send(404, NOT_FOUND_PAGE, "text/html; charset=utf-8")
            return
        form = dict(parse_qsl(self._read_raw(4_000).decode("utf-8", "replace")))
        if demo_locked(company_id):
            self._send(303, b"", "text/plain", {"Location": f"/s/{company_id}.{token}"})
            return
        ip, agent = self._client_ip(), self.headers.get("User-Agent", "")

        def sign(st: dict) -> None:
            j, c = payer_page.find_co(st, token)
            domain.sign_change_order(st, j, c, form.get("name", ""), form.get("agree") == "yes", ip, agent)

        status, payload = self._mutate(company_id, sign)
        if status != 200:
            self._co_sign_page(company_id, token, payload.get("error", "Not signed."))
            return
        self._send(303, b"", "text/plain", {"Location": f"/s/{company_id}.{token}"})

    def _inbound_sms(self, token: str) -> None:
        company_id = importer.company_for_token(token, "sms")
        if not company_id:
            self._json(404, {"error": "Not found"})
            return
        if not _within_limit(f"inbound:{token}", INBOUND_PER_HOUR, 3600):
            self._json(429, {"error": "Too many messages to this address this hour. Try again later."})
            return
        data = self._read_raw(MAX_BODY_BYTES)
        if demo_locked(company_id):
            self._json(200, {"ok": True, "ignored": "the demo is read-only"})
            return
        params = dict(parse_qsl(data.decode("utf-8", "replace"), keep_blank_values=True))
        secret = os.environ.get("TWILIO_AUTH_TOKEN")
        if secret:
            url = self._base_url() + self.path
            signed = url + "".join(k + params[k] for k in sorted(params))
            expected = base64.b64encode(hmac.new(secret.encode(), signed.encode(), hashlib.sha1).digest()).decode()
            if not hmac.compare_digest(expected, self.headers.get("X-Twilio-Signature", "")):
                self._json(403, {"error": "Signature check failed."})
                return
        media = []
        try:
            count = int(params.get("NumMedia", "0") or 0)
        except ValueError:
            count = 0
        for i in range(min(count, 10)):
            if params.get(f"MediaUrl{i}"):
                media.append({"url": params[f"MediaUrl{i}"], "type": params.get(f"MediaContentType{i}", "")})
        text = params.get("Body", "")
        # Download the pictures (outside the lock: it talks to Twilio); what can't be fetched stays a link.
        downloaded, problems = twilio_media.download(media, importer.MAX_FILE_BYTES)
        stored, skipped = self._store_email_files(company_id, {"attachments": downloaded}, "text message")
        got = {f["url"] for f in downloaded}
        links = [m for m in media if m["url"] not in got] if stored or downloaded else media
        if links:
            text += "\n\n" + "\n".join(f"Photo or file sent by text (not downloaded): {m['url']}" for m in links)
        notes = problems + skipped

        def add(state: dict) -> None:
            for meta in stored:
                importer.register_file(state, meta)
            extra = {"media": media} if media else {}
            if notes:
                extra["skipped_attachments"] = notes
            importer.add_item(state, "sms", text=text, sender=params.get("From", ""), file_ids=[m["id"] for m in stored],
                              extra=extra or None)

        status, _ = self._mutate(company_id, add)
        if status != 200:
            importer.discard(company_id, stored)
        body = b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
        self._send(200 if status == 200 else 409, body, "text/xml; charset=utf-8")

    # ----- state-changing handlers ("mutate": run under the lock, saved, snapshot returned) -----

    def _m_reset(self, state: dict, m, ctx: "Ctx") -> dict:
        company_id = ctx.company_id
        if company_id == auth.DEMO_COMPANY_ID:
            return seed_state()
        name = state.get("company", {}).get("name", "New company")
        if _text(ctx.body, "confirm").strip() != name:
            raise domain.GateError("Reset not done. Type your company name exactly to confirm.",
                                   ["Reset deletes every job, customer and record in this company."])
        # As Delete asks for it: a session left open on a shared computer can't wipe the company (R6-05).
        if not auth.check_password(ctx.user["id"], ctx.body.get("password")):
            raise domain.GateError("Reset not done. Your password is wrong.", fields={"password": "Enter your password to confirm."})
        if company_exists(company_id):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            storage.write_json(COMPANIES / "snapshots" / f"{company_id}-before-reset-{stamp}.json", state)
        kept = {k: v for k, v in state.get("company", {}).items() if k == "trial"}
        fresh = empty_state(name)
        fresh["company"].update(kept)  # a reset doesn't restart the trial
        return fresh

    def _m_create_job(self, state: dict, m, ctx: "Ctx") -> None:
        body = ctx.body
        domain.create_job(
            state, _text(body, "name"), _text(body, "customer"), _number(body, "contract_cents", "the contract amount", 0),
            address=_text(body, "address"), approver=_text(body, "approver"), ap_email=_text(body, "ap_email"),
            phone=_text(body, "phone"), role=_text(body, "role", "Owner"),
            retainage_pct=_number(body, "retainage_pct", "the retainage percent", 0),
            # "packet_notes" is what pages from before R1-04 send; it is a note on the job record now.
            note=_text(body, "note") or _text(body, "packet_notes"),
            contract_number=_text(body, "contract_number"), actor=ctx.actor,
        )

    def _m_job_action(self, state: dict, m, ctx: "Ctx") -> None:
        job_id, action, item = m.group(1), m.group(2), m.group(3)
        entry = JOB_ACTIONS.get(action)
        if not entry or entry[0] != (item is not None):
            raise UnknownAction()
        if action in OWNER_ACTIONS and not may_take(ctx.user, action, state):
            raise OwnerOnly()
        entry[1](self, state, job_id, item, ctx)

    def _m_payer_suggestion(self, state: dict, m, ctx: "Ctx") -> None:
        domain.answer_packet_suggestion(state, m.group(1), m.group(2), m.group(3) == "add", ctx.actor)

    def _m_company_update(self, state: dict, m, ctx: "Ctx") -> None:
        domain.update_company(state, ctx.body, ctx.actor)

    def _p_my_name(self, m, ctx: "Ctx") -> None:
        try:
            user = auth.update_name(ctx.user["id"], ctx.body.get("name"))
        except auth.AuthError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"user": user})

    def _m_payer_update(self, state: dict, m, ctx: "Ctx") -> None:
        domain.update_payer(state, m.group(1), ctx.body, ctx.actor)

    def _m_payer_add(self, state: dict, m, ctx: "Ctx") -> None:
        domain.add_payer(state, ctx.body, ctx.actor)

    def _m_payer_merge(self, state: dict, m, ctx: "Ctx") -> None:
        domain.merge_payer(state, m.group(1), _text(ctx.body, "into"), ctx.actor)



class UnknownAction(Exception):
    """A job action the route table doesn't know: answered with 404."""


class OwnerOnly(Exception):
    """A job action only an owner may take: answered with 403."""


@dataclass
class Ctx:
    """A signed-in request with its JSON body."""
    user: dict
    body: dict
    company_id: str
    actor: str
    # Fields a "mutate" handler adds to the snapshot it answers with.
    reply: dict = field(default_factory=dict)


@dataclass
class Route:
    method: str
    pattern: str
    handler: str
    access: str = "public"
    owner: bool = False

    def __post_init__(self) -> None:
        self.regex = re.compile(self.pattern)


TOKEN_LINK = r"([a-z0-9-]{1,40})\.([A-Za-z0-9_-]{20,64})"
ROUTES = [
    # pages and feeds
    Route("GET", r"/healthz", "_g_healthz"),
    Route("GET", r"/(terms|privacy)", "_g_legal"),
    Route("GET", r"/favicon\.ico", "_g_favicon"),
    Route("GET", r"/api/calendar/([^/]+?)\.ics", "_g_calendar"),
    Route("GET", r"/p/" + TOKEN_LINK + r"(/pdf|/file/[A-Za-z0-9_-]{1,64})?", "_g_payer_page"),
    Route("GET", r"/s/" + TOKEN_LINK, "_g_co_sign"),
    # signed-in reads
    Route("GET", r"/api/auth/me", "_g_me"),
    Route("GET", r"/api/auth/demo-available", "_g_demo_available"),
    Route("GET", r"/api/state", "_g_state", "user"),
    Route("GET", r"/api/jobs", "_g_jobs", "user"),
    Route("GET", r"/api/jobs/([^/]+)", "_g_job", "user"),
    Route("GET", r"/api/(outbox|audit)", "_g_log_page", "user"),
    Route("GET", r"/api/timeline", "_g_timeline", "user"),
    Route("GET", r"/api/export", "_g_export", "user"),
    Route("GET", r"/api/team", "_g_team", "user"),
    Route("GET", r"/api/metrics", "_g_metrics", "user"),
    Route("GET", r"/api/qbo/status", "_g_qbo_status", "user"),
    Route("GET", r"/api/billing", "_g_billing", "user"),
    Route("GET", r"/api/qbo/reconcile", "_g_qbo_reconcile", "user"),
    Route("GET", r"/api/qbo/callback", "_g_qbo_callback"),
    Route("GET", r"/api/import/settings", "_g_import_settings", "user"),
    Route("GET", r"/api/files/([^/]+)", "_g_file", "user"),
    Route("GET", r"/api/jobs/([^/]+)/payapp/([^/]+)\.pdf", "_g_payapp_pdf", "user"),
    Route("GET", r"/api/jobs/([^/]+)/payapp/([^/]+)\.csv", "_g_payapp_csv", "user"),
    Route("GET", r"/api/jobs/([^/]+)/payapp/([^/]+)/page", "_g_payapp_page", "user"),
    Route("GET", r"/api/jobs/([^/]+)/co/([^/]+)/page", "_g_co_page", "user"),
    Route("GET", r"/api/helper/review", "_g_helper_review", "user"),
    Route("GET", r"/api/admin/companies", "_g_admin_companies", "user"),
    # signing in, and links people outside the company use
    Route("POST", r"/api/auth/login", "_p_login"),
    Route("POST", r"/api/auth/signup", "_p_signup"),
    Route("POST", r"/api/auth/demo", "_p_demo"),
    Route("POST", r"/api/auth/logout", "_p_logout"),
    Route("POST", r"/api/auth/forgot", "_p_forgot"),
    Route("POST", r"/api/auth/reset", "_p_reset_password"),
    Route("POST", r"/api/auth/verify", "_p_verify"),
    Route("POST", r"/api/auth/invite-info", "_p_invite_info"),
    Route("POST", r"/api/auth/accept-invite", "_p_accept_invite"),
    Route("POST", r"/api/inbound/email/([^/]+)", "_p_inbound_email"),
    Route("POST", r"/api/inbound/sms/([^/]+)", "_p_inbound_sms"),
    Route("POST", r"/api/mail-events/([^/]+)", "_p_mail_events"),
    Route("POST", r"/p/" + TOKEN_LINK + r"/received", "_p_payer_received"),
    Route("POST", r"/p/" + TOKEN_LINK + r"/approve", "_p_payer_approve"),
    Route("POST", r"/p/" + TOKEN_LINK + r"/question", "_p_payer_question"),
    Route("POST", r"/s/" + TOKEN_LINK + r"/sign", "_p_co_sign"),
    Route("POST", r"/share", "_p_share"),
    Route("POST", r"/api/stripe/webhook", "_p_stripe_webhook"),
    # signed in
    Route("POST", r"/api/import/file", "_p_import_file", "user"),
    Route("POST", r"/api/helper/ask", "_p_helper_ask", "body"),
    Route("POST", r"/api/helper/feedback", "_p_helper_feedback", "body"),
    Route("POST", r"/api/helper/(answers|resolve)", "_p_helper_admin", "body"),
    Route("POST", r"/api/auth/resend-verify", "_p_resend_verify", "body"),
    Route("POST", r"/api/auth/password", "_p_password", "body"),
    Route("POST", r"/api/auth/2fa/(begin|enable|disable)", "_p_two_step", "body"),
    Route("POST", r"/api/import/payments-csv", "_p_payments_csv", "body"),
    Route("POST", r"/api/import/rotate", "_p_rotate", "body", owner=True),
    Route("POST", r"/api/metrics/summary-email", "_p_summary_email", "body"),
    Route("POST", r"/api/qbo/connect", "_p_qbo_connect", "body", owner=True),
    Route("POST", r"/api/billing/checkout", "_p_billing_checkout", "body", owner=True),
    Route("POST", r"/api/billing/portal", "_p_billing_portal", "body", owner=True),
    Route("POST", r"/api/qbo/disconnect", "_p_qbo_disconnect", "body", owner=True),
    Route("POST", r"/api/qbo/payments", "_p_qbo_payments", "body"),
    Route("POST", r"/api/onboarding/dismiss", "_p_onboarding_dismiss", "body"),
    Route("POST", r"/api/team/invite", "_p_team_invite", "body", owner=True),
    Route("POST", r"/api/team/invites/([^/]+)/revoke", "_p_team_revoke", "body", owner=True),
    Route("POST", r"/api/team/([^/]+)/role", "_p_team_role", "body", owner=True),
    Route("POST", r"/api/team/([^/]+)/remove", "_p_team_remove", "body", owner=True),
    Route("POST", r"/api/account/delete", "_p_account_delete", "body", owner=True),
    Route("POST", r"/api/sov/parse", "_p_sov_parse", "body"),
    Route("POST", r"/api/outbox/([^/]+)/retry", "_p_outbox_retry", "body"),
    Route("POST", r"/api/share/claim", "_p_share_claim", "body"),
    # changes to the company's state
    Route("POST", r"/api/reset", "_m_reset", "mutate", owner=True),
    Route("POST", r"/api/jobs", "_m_create_job", "mutate"),
    Route("POST", r"/api/jobs/([^/]+)/([a-z-]+)(?:/([^/]+))?", "_m_job_action", "mutate"),
    Route("POST", r"/api/payers/([^/]+)/suggestion/([^/]+)/(add|dismiss)", "_m_payer_suggestion", "mutate"),
    Route("POST", r"/api/payers", "_m_payer_add", "mutate"),
    Route("POST", r"/api/payers/([^/]+)/update", "_m_payer_update", "mutate"),
    Route("POST", r"/api/payers/([^/]+)/merge", "_m_payer_merge", "mutate"),
    Route("POST", r"/api/import/payments-apply", "_m_payments_apply", "mutate"),
    Route("POST", r"/api/import/text", "_m_import_text", "mutate"),
    Route("POST", r"/api/inbox/([^/]+)/([a-z-]+)", "_m_inbox", "mutate"),
    Route("POST", r"/api/company", "_m_company_update", "mutate", owner=True),
    Route("POST", r"/api/auth/name", "_p_my_name", "body"),
]


def _co_args(ctx: Ctx) -> dict:
    return {"link_base": app_url(), "company_id": ctx.company_id}


def _request_co_signature(st: dict, job_id: str, co_id: str, c: Ctx) -> str:
    """The contractor reads plain words when signing links can't be made; the reason is logged here."""
    if not app_url():
        log_json("warn", msg="no signing link: DURACASH_URL is not set on the server", company=c.company_id)
    return domain.request_co_signature(st, job_id, co_id, c.actor, **_co_args(c))


def _confirmed(c: Ctx, what: str = "the pay app") -> None:
    """Emails go out from the signed-in person's address, so on a server that sends mail it must
    be confirmed first. The Outbox used to hold them without a word (mailer.UNVERIFIED); now the
    tap that would send is refused with the reason, before anything changes (R1-07)."""
    if c.company_id != auth.DEMO_COMPANY_ID and mailer.configured() and not c.user.get("email_verified"):
        raise domain.GateError(f"Confirm your email first so {what} goes out from you. Open the link we emailed you, or send it again.")


def _sends(what: str, run):
    """A JOB_ACTIONS handler that emails the payer: refused until the sender's email is confirmed."""
    def handler(h, st, j, i, c):
        _confirmed(c, what)
        return run(h, st, j, i, c)
    return handler


def _import_qbo(h, st, j, i, c):
    if c.company_id != auth.DEMO_COMPANY_ID:
        raise domain.GateError("QuickBooks import is not built yet. The sample import only runs on the demo account.")
    domain.import_sample_qbo_payment(st, j, c.actor)


def _progress(h, st, j, i, c):
    """Progress in dollars: the total done so far, or this draw (period_cents), which the
    domain adds to what is already billed (R3-01)."""
    body = c.body
    runs_to = _text(body, "period_to")   # the date the pay app runs to (R4-04); blank is today
    if body.get("period_cents") not in (None, ""):
        return domain.record_progress(st, j, 0, _text(body, "notes"), c.actor, period_cents=_number(body, "period_cents", "this draw"), period_end=runs_to)
    return domain.record_progress(st, j, _number(body, "completed_cents", "the total done so far", 0), _text(body, "notes"), c.actor, period_end=runs_to)


def _retainage(h, st, j, i, c):
    """Ask for retainage: all of it, or amount_cents of it (R4-07)."""
    amount = c.body.get("amount_cents")
    return domain.chase_retainage(st, j, c.actor, amount_cents=None if amount in (None, "") else _number(c.body, "amount_cents", "the retainage to ask for"))


def _received_on(body: dict) -> str | None:
    """The date a payment came in (YYYY-MM-DD, R3-02); blank is today. Not a date, or a day
    still to come, is refused under the field."""
    text = _text(body, "received_on").strip()
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise domain.GateError("Enter the date received, like 2026-10-27.", fields={"received_on": "Enter the date received, like 2026-10-27."})
    # One day past today in UTC is allowed: where the person is, it may already be tomorrow.
    if day > (datetime.now(timezone.utc) + timedelta(days=1)).date():
        raise domain.GateError("Enter a date that has already happened.", fields={"received_on": "Enter a date that has already happened."})
    return text


def _edit_job(h, st, j, i, c):
    body = c.body
    updates = {k: _text(body, k) for k in ("name", "address", "contract_number") if k in body}
    if "contract_cents" in body:
        updates["contract_cents"] = _number(body, "contract_cents", "the contract amount", 0)
    if "retainage_pct" in body:
        updates["retainage_pct"] = _number(body, "retainage_pct", "the retainage percent", 0)
    domain.edit_job(st, j, updates, c.actor)


# Job actions only an owner may take (R4-02, R4-09): every final or emailed step. "You approve
# every send": a member drafts, an owner sends, unless the company lets members send pay apps,
# waivers and retainage requests (members_can_send, on Settings, Company).
OWNER_ACTIONS = {"send", "waiver", "retainage", "void-draft", "void-sent", "close", "delete", "accept-short-pay", "reopen"}
MEMBER_SEND_ACTIONS = {"send", "waiver", "retainage"}


def may_take(user: dict, action: str, state: dict) -> bool:
    """Whether this person may take this job action (mirrored by mayTake in static/js/jobs.js)."""
    if action not in OWNER_ACTIONS or user.get("role", "owner") == "owner":
        return True
    return action in MEMBER_SEND_ACTIONS and bool(state.get("company", {}).get("members_can_send"))


# /api/jobs/<job>/<action>[/<item>]: action -> (takes an item id, handler(handler, state, job_id, item, ctx)).
JOB_ACTIONS = {
    "done": (False, lambda h, st, j, i, c: domain.mark_done(st, j, c.actor, _text(c.body, "notes"), period_end=_text(c.body, "period_to"))),
    "progress": (False, _progress),
    "sov": (False, lambda h, st, j, i, c: domain.save_sov(st, j, _list(c.body, "lines"), c.body.get("op_pct"), c.body.get("update_contract") is True, c.actor)),
    "sov-progress": (False, lambda h, st, j, i, c: domain.record_sov_progress(st, j, c.body.get("done"), _text(c.body, "notes"), c.actor, stored=c.body.get("stored"),
                                                                                        period_end=_text(c.body, "period_to"))),
    "resolve": (True, lambda h, st, j, i, c: domain.resolve_packet_item(st, j, i, c.actor, reason=_text(c.body, "reason"))),
    "draft": (False, lambda h, st, j, i, c: domain.draft_invoice(st, j, actor=c.actor, strict=True)),
    "send": (False, _sends("the pay app", lambda h, st, j, i, c: domain.approve_and_send(st, j, c.actor, **_co_args(c)))),
    "follow-up": (False, _sends("the follow-up", lambda h, st, j, i, c: domain.record_follow_up(st, j, c.actor, early=c.body.get("early") is True, **_co_args(c)))),
    "match-payment": (False, lambda h, st, j, i, c: domain.match_payment(st, j, _number(c.body, "amount_cents", "the payment amount"), _text(c.body, "reference"), c.actor,
                                                                         invoice_id=_text(c.body, "invoice_id") or None, received_on=_received_on(c.body))),
    "waiver": (False, _sends("the waiver", lambda h, st, j, i, c: domain.send_waiver(st, j, c.actor, invoice_id=_text(c.body, "invoice_id") or None, kind=_text(c.body, "kind") or None))),
    "retainage": (False, _sends("the request", _retainage)),
    "note-contact": (False, lambda h, st, j, i, c: domain.note_contact(st, j, _text(c.body, "text"), _text(c.body, "next_on"), c.actor)),
    "retainage-paid": (False, lambda h, st, j, i, c: domain.record_retainage_payment(st, j, _number(c.body, "amount_cents", "the retainage received"), _text(c.body, "reference"), c.actor)),
    "add-co": (False, lambda h, st, j, i, c: domain.add_change_order(st, j, _text(c.body, "title"), _number(c.body, "amount_cents", "the change order amount", 0), c.actor, description=_text(c.body, "description"),
                                                                    schedule_days=_number(c.body, "schedule_days", "the schedule change in days", 0))),
    # A change order is approved by a signature: online (request-co-signature) or a signed
    # copy on the job record. A bare status flip is not accepted (P2-3).
    "approve-co": (True, lambda h, st, j, i, c: domain.mark_co_signed(st, j, i, _text(c.body, "record_id"), c.actor)),
    "request-co-signature": (True, _sends("the request", lambda h, st, j, i, c: _request_co_signature(st, j, i, c))),
    "include-co": (True, lambda h, st, j, i, c: domain.include_change_order(st, j, i, c.actor)),
    "withdraw-co": (True, lambda h, st, j, i, c: domain.withdraw_change_order(st, j, i, c.actor)),
    "void-draft": (False, lambda h, st, j, i, c: domain.void_draft(st, j, c.actor)),
    "pencil": (False, _sends("the draft", lambda h, st, j, i, c: domain.send_pencil(st, j, c.actor))),
    "pencil-response": (False, lambda h, st, j, i, c: domain.record_pencil_response(st, j, c.body.get("agreed") is True, _text(c.body, "note"), c.actor)),
    "add-evidence": (False, lambda h, st, j, i, c: domain.add_evidence(st, j, _filled(c.body, "label", "Enter what this evidence is."), _text(c.body, "kind", "note"), c.actor)),
    "add-record": (False, lambda h, st, j, i, c: domain.add_job_record(st, j, _text(c.body, "label"), _text(c.body, "kind", "note"), _text(c.body, "note"), c.actor)),
    "edit-record": (True, lambda h, st, j, i, c: domain.edit_record(st, j, i, _text(c.body, "label"), _text(c.body, "kind"), c.actor)),
    "attach-record": (True, lambda h, st, j, i, c: domain.attach_record_to_packet(st, j, i, _text(c.body, "record_id"), c.actor)),
    "detach": (True, lambda h, st, j, i, c: domain.detach_packet_item(st, j, i, c.actor)),
    "packet-skip": (True, lambda h, st, j, i, c: domain.skip_packet_item(st, j, i, _text(c.body, "reason"), c.actor)),
    "learn-rejection": (False, lambda h, st, j, i, c: domain.learn_rejection(st, j, _filled(c.body, "reason", "Enter why the customer sent the pay app back."), c.actor)),
    "import-qbo": (False, _import_qbo),
    "close": (False, lambda h, st, j, i, c: domain.close_job(st, j, c.actor)),
    "reopen": (False, lambda h, st, j, i, c: domain.reopen_job(st, j, _text(c.body, "reason"), c.actor)),
    "undo-done": (False, lambda h, st, j, i, c: domain.undo_mark_done(st, j, c.actor)),
    "accept-short-pay": (True, lambda h, st, j, i, c: domain.settle_short_pay(st, j, i, _text(c.body, "reason"), c.actor)),
    "void-sent": (True, _sends("the email", lambda h, st, j, i, c: domain.void_sent_pay_app(st, j, i, _text(c.body, "reason"), c.actor))),
    "edit": (False, _edit_job),
    "delete": (False, lambda h, st, j, i, c: domain.delete_job(st, j, c.actor)),
    "daily-record": (False, lambda h, st, j, i, c: domain.add_daily_record(st, j, _filled(c.body, "notes", "Enter what happened on the job."), c.actor)),
    "attach": (True, lambda h, st, j, i, c: domain.add_packet_attachment(st, j, i, _text(c.body, "name", "attachment"), c.actor)),
}

def _signed_by_provider(data: bytes, raw_ctype: str, key: str, now: float | None = None) -> bool:
    """Mailgun's signature (R6-07): `signature` is the hex HMAC-SHA256 of `timestamp` + `token`
    under the signing key, and the timestamp is within 15 minutes."""
    fields = importer.provider_fields(data, raw_ctype)
    stamp, nonce, signature = (str(fields.get(k) or "") for k in ("timestamp", "token", "signature"))
    try:
        fresh = abs((now or time.time()) - int(stamp)) <= 15 * 60
    except ValueError:
        return False
    expected = hmac.new(key.encode(), (stamp + nonce).encode(), hashlib.sha256).hexdigest()
    return fresh and bool(nonce) and hmac.compare_digest(expected, signature)


def main() -> None:
    problems = config_problems()
    if problems:
        for problem in problems:
            log_json("error", msg=f"Refusing to start in production: {problem}")
        sys.exit(1)
    auth.upgrade_file()
    if demo_enabled():
        ensure_demo()
    server = Server((HOST, PORT), Handler)
    browse = "http://127.0.0.1" if HOST in {"0.0.0.0", ""} else f"http://{HOST}"
    print(f"Duracash running at {browse}:{PORT}{' (production)' if production() else ''}")
    print(f"Data directory: {storage.DATA_DIR}")
    if demo_enabled():
        print(f"Demo login: {auth.DEMO_EMAIL} / {auth.DEMO_PASSWORD}")
    backup.start_scheduler(
        {"auth": auth.LOCK, "tokens": importer.TOKENS_LOCK, "deleted": _DELETED_LOCK, "qbo": qbo.LOCK},
        log=lambda msg: log_json("info", msg=msg),
    )
    monthly.start(SITE)
    server.serve_forever()


if __name__ == "__main__":
    main()
