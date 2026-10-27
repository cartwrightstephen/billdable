"""Local Duracash server with company isolation and cookie sessions.

    python3 server.py
    open http://127.0.0.1:8765

Each company has its own JSON file under <data dir>/companies/. Auth lives in
<data dir>/auth.json. The data dir is DURACASH_DATA_DIR, default data/ beside
this file. Only index.html, app.js and styles.css are served from static/; the
page addresses the app uses (/today, /jobs/..., /money/...) all get index.html.
"""

from __future__ import annotations

from dataclasses import dataclass

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
import offsite
import payapp_pdf
import qbo
import qbo_csv
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
_COMPANY_LOCKS: dict = {}
_COMPANY_LOCKS_GUARD = threading.Lock()
_DELETED_LOCK = threading.Lock()


def company_lock(company_id: str) -> threading.Lock:
    """One lock per company: a change loads, edits and saves under it. Companies don't wait on each other."""
    with _COMPANY_LOCKS_GUARD:
        return _COMPANY_LOCKS.setdefault(company_id, threading.Lock())


STUCK_MINUTES = 30  # an email still "sending" after this long died mid-send and can be retried
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
# The app's ES modules (P2-8): app.js imports them from /js/; no bundler.
STATIC_FILES.update({f"/js/{p.name}": (f"js/{p.name}", "text/javascript; charset=utf-8")
                     for p in sorted((STATIC / "js").glob("*.js"))})
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
COMPANY_READ_ONLY_OK = DEMO_READ_ONLY_OK | {"_p_password", "_p_two_step", "_p_my_name", "_p_resend_verify", "_p_team", "_p_helper_admin",
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


def load_company(company_id: str) -> dict:
    state = company_store().load(company_id)
    if state is not None:
        migrations.migrate(state)  # the next save writes the current shape back
        return state
    # A signed-in user whose company is gone means data was lost
    # (for example a deploy without a persistent disk). Say so in the log.
    print(f"WARNING: company data missing for {company_id} in {COMPANIES}; serving a blank company.", file=sys.stderr)
    return empty_state("New company")


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
    # A request that was already running when its company was deleted must not write it back.
    if company_id in deleted_companies():
        log_json("warn", msg="write to a deleted company refused", company=company_id)
        return
    company_store().save(company_id, state)


def activation_days(state: dict) -> int | None:
    """The business plan's activation measure: days from signup to the first paid pay app sent
    through Duracash (None until then)."""
    t = trial.status(state)
    if t.get("reason") != "paid":
        return None
    return (datetime.fromisoformat(t["ended_at"]).date() - datetime.fromisoformat(t["started_at"]).date()).days


def send_monthly_summaries(year: int, month: int) -> list:
    """Email each company's owners (confirmed addresses) their summary of that month. Returns
    one line per email for the log: "company  address: result"."""
    lines = []
    users = auth.all_users()
    for company_id in company_ids():
        if company_id == auth.DEMO_COMPANY_ID:
            continue
        with company_lock(company_id):
            state = load_company(company_id)
        subject, body = metrics.month_summary(state, year, month)
        for u in users:
            if u["company_id"] == company_id and u.get("role") == "owner" and u.get("email_verified"):
                lines.append(f"{company_id}  {u['email']}: {send_system_email(u['email'], subject, body, company_id)}")
    return lines


MONTHLY_PATH_NAME = "monthly_jobs.json"


def run_monthly_jobs(now: datetime | None = None) -> list:
    """Once a month, recorded in <data dir>/monthly_jobs.json so a restart doesn't repeat them:
    the restore drill on the newest backup (an alert when it fails), and, with
    MONTHLY_SUMMARY_EMAILS=1 and email set up, last month's summary to each owner (on the 1st
    to the 3rd). Returns what it did, for the log."""
    now = now or datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    path = storage.DATA_DIR / MONTHLY_PATH_NAME
    try:
        done = json.loads(path.read_text())
    except (OSError, ValueError):
        done = {}
    did = []
    newest = (backup.backups() or [None])[0]
    if done.get("drill") != month and newest is not None:
        report, problems = backup.drill(newest)
        did.append(f"restore drill of {newest.name}: " + ("PASS" if not problems else f"FAIL ({len(problems)} problems)"))
        if problems:
            alerts.notify("drill", f"the monthly restore drill of {newest.name} failed: {problems[0]}")
        done["drill"] = month
    if (os.environ.get("MONTHLY_SUMMARY_EMAILS") == "1" and mailer.configured() and now.day <= 3
            and done.get("summaries") != month):
        last = now.date().replace(day=1) - timedelta(days=1)
        lines = send_monthly_summaries(last.year, last.month)
        did.append(f"monthly summaries for {last:%Y-%m}: {len(lines)} sent or tried")
        done["summaries"] = month
    if did:
        storage.write_json(path, done)
    return did


def start_monthly_jobs() -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                for line in run_monthly_jobs():
                    log_json("info", msg=line)
            except Exception as exc:  # a failure is logged and alerted; the loop keeps going
                log_json("error", msg=f"monthly jobs failed: {exc.__class__.__name__}: {exc}")
                alerts.notify("monthly-jobs", f"monthly jobs failed ({exc.__class__.__name__}); see the server log")
            time.sleep(3600)

    thread = threading.Thread(target=loop, name="monthly-jobs", daemon=True)
    thread.start()
    return thread


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
        proto = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() or ("https" if os.environ.get("COOKIE_SECURE") == "1" else "http")
        host = (self.headers.get("X-Forwarded-Host") or "").split(",")[0].strip() or self.headers.get("Host") or f"127.0.0.1:{PORT}"
        return f"{proto}://{host}"

    def _change(self, company_id: str, fn) -> tuple:
        """Load, change and save one company under the lock; return (status, snapshot)."""
        try:
            with company_lock(company_id):
                state = load_company(company_id)
                fn(state)
                save_company(company_id, state)
                return 200, domain.light_snapshot(state)
        except domain.GateError as exc:
            return 409, _gate(exc)

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
        """The caller's address. Behind Render's proxy it is the last X-Forwarded-For entry,
        the one the proxy added; earlier entries come from the client and can be made up."""
        forwarded = [p.strip() for p in (self.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
        return forwarded[-1] if forwarded else self.client_address[0]

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
        try:
            with company_lock(ctx.company_id):
                state = load_company(ctx.company_id)
                state = handler(state, m, ctx) or state
                # The first paid pay app ends the free trial (trial.py): tell Stripe, once.
                end_trial = billing.trial_end_due(state) if billing.configured() else None
                save_company(ctx.company_id, state)
                payload = domain.light_snapshot(state)
                # Most changes queue no email: don't load the company again just to find that out.
                queued = any(e.get("status") == "queued" for e in state.get("email_log", []))
        except UnknownAction:
            self._json(404, {"error": "Unknown action"})
            return
        except domain.GateError as exc:
            self._json(409, _gate(exc))
            return
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            log_json("warn", msg="request not understood", rid=self._rid, path=redact(urlparse(self.path).path),
                     error=redact(f"{type(exc).__name__}: {exc}")[:300])
            self._json(400, {"error": NOT_UNDERSTOOD, "request_id": self._rid})
            return
        if end_trial:
            threading.Thread(target=self._end_stripe_trial, args=(end_trial,), daemon=True).start()
        # Approved emails are delivered after the change is saved and the lock is released;
        # the reply is written after that too, so a slow client can't stall other companies.
        self._json(200, (self._deliver_queued(ctx.company_id, ctx.user) if queued else None) or payload)

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
        # For an uptime monitor: no sign-in, no secrets, 503 when data can't be written.
        writable = True
        try:
            probe = storage.DATA_DIR / f".health-{secrets.token_hex(4)}"
            storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok")
            probe.unlink()
        except OSError:
            writable = False
        self._json(200 if writable else 503, {
            "ok": writable,
            "storage": "ok" if writable else "not writable",
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
        pdf, name = self._payapp_pdf(state, m.group(1), m.group(2))
        if not pdf:
            self._json(404, {"error": "Pay app not found."})
            return
        self._send(200, pdf, "application/pdf", {"Content-Disposition": f'inline; filename="{name}"'})

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
                         "billing": billing.standing(state)})

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

    def _end_stripe_trial(self, subscription_id: str) -> None:
        try:
            billing.end_trial(subscription_id)
        except billing.BillingError as exc:
            log_json("warn", msg="ending the Stripe trial failed", reason=str(exc)[:200])
            alerts.notify("billing:trial-end", f"Couldn't end a Stripe trial ({subscription_id}): {exc}")

    def _g_billing(self, m, user) -> None:
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        self._json(200, {**billing.standing(state), "trial": trial.status(state)})

    def _p_billing_checkout(self, m, ctx: "Ctx") -> None:
        if ctx.company_id == auth.DEMO_COMPANY_ID or not app_url():
            self._json(409, {"error": "The demo account can't subscribe." if ctx.company_id == auth.DEMO_COMPANY_ID
                             else "Set DURACASH_URL first; Stripe sends people back to it."})
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

        self._change(company_id, apply)
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
        self._change(company_id, lambda st: domain.audit(st, "", "qbo_connected", "QuickBooks was connected (read-only).", "QuickBooks"))
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
            self._json(200, {"url": qbo.authorize_url(ctx.company_id, ctx.actor, self._qbo_redirect_uri())})
        except qbo.QboError as exc:
            self._json(409, {"error": str(exc)})

    def _p_qbo_disconnect(self, m, ctx: "Ctx") -> None:
        qbo.disconnect(ctx.company_id)
        self._change(ctx.company_id, lambda st: domain.audit(st, "", "qbo_disconnected", f"{ctx.actor} disconnected QuickBooks.", ctx.actor))
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
        with company_lock(user["company_id"]):
            state = load_company(user["company_id"])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        self._send(200, json.dumps(state, indent=2).encode(), "application/json",
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
        self._json(200, {
            "email_webhook": f"{base}/api/inbound/email/{tokens['email']}",
            "email_address": template.replace("{token}", tokens["email"]) if "{token}" in template else "",
            "sms_webhook": f"{base}/api/inbound/sms/{tokens['sms']}",
            "sms_number": os.environ.get("TWILIO_NUMBER", ""),
            "sms_signature_check": bool(os.environ.get("TWILIO_AUTH_TOKEN")),
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
        self._change(user["company_id"], lambda st: domain.audit(
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
        # Read a QuickBooks payments export and propose matches; nothing is recorded (P2-1).
        parsed = qbo_csv.parse(_text(ctx.body, "csv", limit=2_000_000))
        with company_lock(ctx.company_id):
            state = load_company(ctx.company_id)
        self._json(200, {"rows": qbo_csv.propose(state, parsed["rows"]), "warnings": parsed["warnings"]})

    def _p_payments_apply(self, m, ctx: "Ctx") -> None:
        picks = ctx.body.get("payments")
        if not isinstance(picks, list) or not picks or len(picks) > 500:
            raise BadRequest("Send the payments to record.")
        results = []

        def apply(st: dict) -> None:
            for pick in picks:
                if not isinstance(pick, dict):
                    continue
                ref = str(pick.get("reference") or "")[:60]
                try:
                    domain.match_payment(st, str(pick.get("job_id") or ""), pick.get("amount_cents"), ref,
                                         ctx.actor, invoice_id=str(pick.get("invoice_id") or "") or None,
                                         received_on=str(pick.get("date") or "") or None,
                                         qbo_payment_id=str(pick.get("qbo_id") or "") or None)
                    results.append({"reference": ref, "ok": True})
                except domain.GateError as exc:
                    results.append({"reference": ref, "ok": False, "error": str(exc)})

        status, payload = self._change(ctx.company_id, apply)
        if status == 200:
            payload["import_results"] = results
        self._json(status, payload)

    def _p_summary_email(self, m, ctx: "Ctx") -> None:
        if ctx.company_id == auth.DEMO_COMPANY_ID or not ctx.user.get("email_verified"):
            self._json(409, {"error": "Confirm your email address first (Team & account)." if ctx.company_id != auth.DEMO_COMPANY_ID
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
                self._json(200, auth.totp_begin(ctx.user["id"]))
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

    def _p_team(self, m, ctx: "Ctx") -> None:
        path = urlparse(self.path).path
        self._team_action(path, [p for p in path.split("/") if p], ctx.body, ctx.user)

    def _p_import_text(self, m, ctx: "Ctx") -> None:
        file_now = (parse_qs(urlparse(self.path).query).get("file") or [""])[0] == "1"
        self._json(*self._change(ctx.company_id, lambda st: self._import_text(st, ctx.body, ctx.actor if file_now else None)))

    def _p_inbox(self, m, ctx: "Ctx") -> None:
        item_id, verb, body, actor = m.group(1), m.group(2), ctx.body, ctx.actor
        actions = {
            "file": lambda st: importer.file_item(st, item_id, _text(body, "job_id"), _text(body, "kind"), actor),
            "dismiss": lambda st: importer.dismiss_item(st, item_id, actor),
            "undo": lambda st: importer.undo_item(st, item_id, actor),
            "mark-done": lambda st: importer.file_and_mark_done(st, item_id, actor),
        }
        if verb not in actions:
            self._json(404, {"error": "Unknown action"})
            return
        self._json(*self._change(ctx.company_id, actions[verb]))

    def _p_sov_parse(self, m, ctx: "Ctx") -> None:
        # Read pasted rows; nothing is saved until the schedule itself is saved.
        self._json(200, sov.parse_rows(_text(ctx.body, "text", limit=200_000)))

    def _p_outbox_retry(self, m, ctx: "Ctx") -> None:
        self._retry_email(ctx.company_id, ctx.user, m.group(1))

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
            return "email delivery or DURACASH_URL is not set up on this server"
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

    def _team_action(self, path: str, parts: list, body: dict, user: dict) -> None:
        company_id = user["company_id"]
        if user["role"] != "owner":
            self._json(403, {"error": "Only an owner can manage the team or the account."})
            return
        if company_id == auth.DEMO_COMPANY_ID:
            self._json(403, {"error": "The demo account's team can't be changed."})
            return
        note = None
        try:
            if path == "/api/team/invite":
                if mailer.configured() and app_url() and not user.get("email_verified"):
                    raise auth.AuthError("Confirm your own email address before inviting people.")
                if not _within_limit(f"invite:{company_id}", 20, 24 * 3600):
                    raise auth.AuthError("20 invites a day is the limit. Try again tomorrow.")
                raw, invite = auth.create_invite(company_id, body.get("email"), body.get("role") or "member", user["name"], actor_id=user["id"])
                url = app_url()
                note = {"invite_path": f"/#invite={raw}", "invite_link": f"{url}/#invite={raw}" if url else None, "emailed": False,
                        "email_status": "DURACASH_URL is not set on this server" if not url else "not sent"}
                if url:
                    with company_lock(company_id):
                        company_name = load_company(company_id).get("company", {}).get("name", "")
                    # Names typed by users go in the body only, flattened and shortened; the subject is fixed.
                    flat = lambda text, n: " ".join(str(text).split())[:n]
                    status = self._system_email(
                        invite["email"], "You're invited to a team on Duracash",
                        f"{flat(user['name'], 80)} ({user['email']}) invited you to join the company "
                        f"\"{flat(company_name, 80)}\" on Duracash as {invite['role']}.\n\n"
                        f"Accept here (the link works for 7 days):\n{url}/#invite={raw}\n\n"
                        "If you don't know this person, ignore this email.",
                        company_id=company_id,
                    )
                    note.update({"emailed": status == "sent", "email_status": status})
                detail = f"{user['name']} invited {invite['email']} as {invite['role']}."
            elif len(parts) == 5 and parts[2] == "invites" and parts[4] == "revoke":
                auth.revoke_invite(company_id, parts[3], actor_id=user["id"])
                detail = f"{user['name']} revoked an invite."
            elif len(parts) == 4 and parts[3] == "role":
                changed = auth.set_role(company_id, parts[2], body.get("role"), actor_id=user["id"])
                detail = f"{user['name']} made {changed['name']} {changed['role']}."
            elif len(parts) == 4 and parts[3] == "remove":
                if parts[2] == user["id"]:
                    raise auth.AuthError("You can't remove yourself. Ask another owner.")
                removed = auth.remove_user(company_id, parts[2], actor_id=user["id"])
                # They knew the inbound and calendar addresses, so those change too.
                for kind in ("email", "sms", "calendar"):
                    importer.rotate_token(company_id, kind)
                note = {"rotated": True}
                detail = (f"{user['name']} removed {removed['name']} ({removed['email']}) from the team. "
                          "The inbound email, text and calendar addresses were changed.")
            elif path == "/api/account/delete":
                auth.require_owner(company_id, user["id"])
                self._delete_account(body, user)
                return
            else:
                self._json(404, {"error": "Not found"})
                return
        except auth.AuthError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._change(company_id, lambda st: domain.audit(st, "", "team", detail, user["name"]))
        self._json(200, {**auth.team(company_id), **(note or {})})

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

    def _payapp_pdf(self, state: dict, job_id: str, invoice_id: str) -> tuple:
        job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
        invoice = next((i for i in (job or {}).get("invoices", []) if i["id"] == invoice_id), None)
        if not invoice:
            return None, None
        payer = next((p for p in state.get("payers", []) if p["id"] == job["payer_id"]), {})
        name = re.sub(r"[^A-Za-z0-9._-]", "-", invoice["number"]) + ".pdf"
        return payapp_pdf.render(state.get("company", {}), job, payer, invoice), name

    def _company_overview(self) -> list:
        users = auth.all_users()
        out = []
        for company_id in company_ids():
            with company_lock(company_id):
                try:
                    state = load_company(company_id)
                except (OSError, ValueError):
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

    def _deliver_queued(self, company_id: str, user: dict, only: set | None = None) -> dict | None:
        """Send this company's queued emails. Returns the new snapshot, or None if nothing was queued."""
        cfg = mailer.settings()
        now = datetime.now(timezone.utc)
        stale = (now - timedelta(minutes=STUCK_MINUTES)).isoformat(timespec="seconds")
        with company_lock(company_id):
            state = load_company(company_id)
            log = state.get("email_log", [])
            queued = [e for e in log if e.get("status") == "queued" and (only is None or e["id"] in only)]
            if not queued:
                return None
            if company_id == auth.DEMO_COMPANY_ID or not mailer.configured() or not user.get("email_verified"):
                if company_id == auth.DEMO_COMPANY_ID:
                    reason = mailer.DEMO
                elif not mailer.configured():
                    reason = mailer.NOT_SET_UP
                else:
                    # Replies go to the approver's address, so it must be theirs before anything goes out.
                    reason = mailer.UNVERIFIED
                for e in queued:
                    e["status"] = reason
                save_company(company_id, state)
                return domain.light_snapshot(state)
            # Only recent "sending" entries are in flight; an older one died mid-send and can be retried.
            in_flight = sum(1 for e in log if e.get("status") == "sending" and (e.get("sending_at") or "") >= stale)
            room = max(0, cfg["daily_limit"] - mailer.sent_in_last_day(company_id) - in_flight)
            batch, over = queued[:room], queued[room:]
            for e in over:
                e["status"] = f"not sent: daily limit of {cfg['daily_limit']} emails reached; retry tomorrow"
            files = {f["id"]: f for f in state.get("files", [])}
            work = []
            for e in batch:
                e["status"] = "sending"
                e["sending_at"] = now.isoformat(timespec="seconds")
                # Reply-To is the person who approved the send, kept even if someone else retries it.
                e["reply_to"] = e.get("reply_to") or user.get("email", "")
                attach = [
                    (files[fid]["name"], importer.file_path(company_id, files[fid]))
                    for fid in e.get("attachments", [])
                    if fid in files
                ]
                job_now = next((j for j in state.get("jobs", []) if j["id"] == e.get("job_id")), {})
                inv_now = next((i for i in job_now.get("invoices", []) if i["id"] == e.get("payapp_invoice_id")), None)
                if e.get("payapp_invoice_id") and inv_now and inv_now.get("status") == "void":
                    # A pay app voided after this email was approved is never sent.
                    e["status"] = "failed: the pay app was voided after this email was approved"
                    e["failed_at"] = now.isoformat(timespec="seconds")
                    continue
                if e.get("payapp_invoice_id"):
                    try:
                        pdf, name = self._payapp_pdf(state, e["job_id"], e["payapp_invoice_id"])
                    except Exception:
                        log_json("error", msg="pay app PDF failed", rid=self._rid, trace=traceback.format_exc())
                        pdf, name = None, None
                    if pdf:
                        attach.insert(0, (name, pdf))
                    else:
                        e["status"] = "failed: the pay app PDF could not be made; see the server log"
                        e["failed_at"] = now.isoformat(timespec="seconds")
                        continue
                work.append((dict(e), attach))
            company_name = state.get("company", {}).get("name", "")
            inbound = inbound_address(company_id)
            save_company(company_id, state)
            if not work:
                return domain.light_snapshot(state)
        # Outside the lock: read attachments and talk to the mail server.
        outcome: dict = {}
        try:
            messages = []
            for entry, attach in work:
                try:
                    msg, left_off = mailer.build_message(entry, company_name, entry.get("reply_to", ""), attach, cfg, inbound=inbound)
                    messages.append((entry["id"], msg, left_off))
                except Exception as exc:  # a bad address or file fails this email only
                    outcome[entry["id"]] = (f"failed: {exc}"[:300], [])
            results = mailer.send([m for _, m, _ in messages], cfg)
            for (entry_id, _, left_off), error in zip(messages, results):
                outcome[entry_id] = (f"failed: {error}" if error else "sent", left_off)
        except Exception as exc:
            traceback.print_exc()
            for entry, _ in work:
                outcome.setdefault(entry["id"], (f"failed: unexpected error ({exc.__class__.__name__}); see the server log", []))
        sent = sum(1 for status, _ in outcome.values() if status == "sent")
        mailer.record_sent(company_id, sent)
        done_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with company_lock(company_id):
            state = load_company(company_id)
            for e in state.get("email_log", []):
                if e["id"] in outcome and e.get("status") == "sending":
                    status, left_off = outcome[e["id"]]
                    e["status"] = status
                    if status == "sent":
                        e["delivered_at"] = done_at
                        if left_off:
                            e["not_attached"] = left_off
                    else:
                        e["failed_at"] = done_at
            save_company(company_id, state)
            return domain.light_snapshot(state)

    def _retry_email(self, company_id: str, user: dict, email_id: str) -> None:
        stale = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_MINUTES)).isoformat(timespec="seconds")
        code, payload = 200, None
        with company_lock(company_id):
            state = load_company(company_id)
            entry = next((e for e in state.get("email_log", []) if e["id"] == email_id), None)
            if not entry:
                code, payload = 404, {"error": "Not found"}
            else:
                status = entry.get("status", "")
                stuck = status in {"sending", "queued"} and (entry.get("sending_at") or entry.get("at") or "") < stale
                if not (status.startswith("failed") or status.startswith("not sent") or stuck):
                    code, payload = 409, {"error": f"This email is {status}; only a failed or unsent email can be sent again."}
                else:
                    entry["status"] = "queued"
                    entry["retried"] = entry.get("retried", 0) + 1
                    save_company(company_id, state)
                    payload = domain.light_snapshot(state)
        # Replies are written after the lock is released.
        if code != 200:
            self._json(code, payload)
            return
        self._json(200, self._deliver_queued(company_id, user, {email_id}) or payload)

    # ----- importing -----

    def _import_file(self, user: dict, actor: str) -> None:
        query = parse_qs(urlparse(self.path).query)
        job_id = (query.get("job") or [""])[0] or None
        file_now = bool(job_id) and (query.get("file") or [""])[0] == "1"
        # What the person said it is (the waiver page's "Take a photo" files a lien waiver); else guessed.
        kind = (query.get("kind") or [""])[0]
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

        def add(state: dict) -> None:
            for meta in stored:
                importer.register_file(state, meta)
            ids = [m["id"] for m in stored]
            if parsed is not None:
                item = importer.add_item(
                    state, "upload", text=parsed.get("text", ""), sender=parsed.get("from", ""), subject=parsed.get("subject", ""),
                    file_ids=ids, job_id=job_id, sent_at=parsed.get("sent_at", ""),
                    extra={"skipped_attachments": skipped} if skipped else None)
            else:
                meta = stored[0]
                text = data.decode("utf-8", "replace") if meta["ext"] in {".txt", ".csv"} else ""
                item = importer.add_item(state, "upload", text=text, subject=meta["name"], file_ids=ids, job_id=job_id)
            if file_now:
                # Dropped on a job page: the person already chose the job.
                importer.file_item(state, item["id"], job_id, kind if kind in importer.KINDS else item["kind"], actor)

        status, payload = self._change(company_id, add)
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
        if source == "email" and text.lower().startswith(("from:", "subject:", "to:", "date:")):
            parsed = importer.parse_email(text.encode("utf-8"))
            sender, subject, text = parsed["from"], parsed["subject"], parsed["text"] or text
        importer.add_item(state, source, text=text, sender=sender, subject=subject, job_id=job_id)

    def _inbound_email(self, token: str) -> None:
        company_id = importer.company_for_token(token, "email")
        if not company_id:
            self._json(404, {"error": "Not found"})
            return
        data = self._read_raw(MAX_INBOUND_BYTES)
        if demo_locked(company_id):
            self._json(200, {"ok": True, "ignored": "the demo is read-only"})
            return
        # Keep the header as sent: a multipart boundary is case-sensitive.
        parsed = _parse_inbound_email(data, self.headers.get("Content-Type") or "")
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

        status, payload = self._change(company_id, add)
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
            pdf, name = self._payapp_pdf(state, job["id"], invoice["id"])
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
            page = payer_page.render(state, job, invoice, f"/p/{company_id}.{token}")
            self._send_compressible(200, page.encode("utf-8"), "text/html; charset=utf-8", private)

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

        self._change(company_id, mark)
        self._send(303, b"", "text/plain", {"Location": f"/p/{company_id}.{token}"})

    def _share_target(self) -> None:
        """Photos, PDFs and text shared to the installed app from the phone's share menu (P3).
        They land in the Inbox like any upload; nothing is filed without a person."""
        user = self._session_user()
        if not user:
            self._send(303, b"", "text/plain", {"Location": "/inbox"})
            return
        company_id = user["company_id"]
        data = self._read_raw(MAX_INBOUND_BYTES)
        if read_only_reason(company_id):
            # The Inbox page shows why; nothing is stored.
            self._send(303, b"", "text/plain", {"Location": "/inbox"})
            return
        parsed = _parse_share(data, self.headers.get("Content-Type") or "")
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

        status, _ = self._change(company_id, add)
        if status != 200:
            importer.discard(company_id, stored)
        self._send(303, b"", "text/plain", {"Location": "/inbox"})

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

        status, payload = self._change(company_id, sign)
        if status != 200:
            self._co_sign_page(company_id, token, payload.get("error", "Not signed."))
            return
        self._send(303, b"", "text/plain", {"Location": f"/s/{company_id}.{token}"})

    def _inbound_sms(self, token: str) -> None:
        company_id = importer.company_for_token(token, "sms")
        if not company_id:
            self._json(404, {"error": "Not found"})
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

        status, _ = self._change(company_id, add)
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
                                   ["Reset deletes every job, payer and record in this company."])
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



class UnknownAction(Exception):
    """A job action the route table doesn't know: answered with 404."""


@dataclass
class Ctx:
    """A signed-in request with its JSON body."""
    user: dict
    body: dict
    company_id: str
    actor: str


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
    Route("POST", r"/api/import/payments-apply", "_p_payments_apply", "body"),
    Route("POST", r"/api/import/text", "_p_import_text", "body"),
    Route("POST", r"/api/import/rotate", "_p_rotate", "body", owner=True),
    Route("POST", r"/api/metrics/summary-email", "_p_summary_email", "body"),
    Route("POST", r"/api/qbo/connect", "_p_qbo_connect", "body", owner=True),
    Route("POST", r"/api/billing/checkout", "_p_billing_checkout", "body", owner=True),
    Route("POST", r"/api/billing/portal", "_p_billing_portal", "body", owner=True),
    Route("POST", r"/api/qbo/disconnect", "_p_qbo_disconnect", "body", owner=True),
    Route("POST", r"/api/qbo/payments", "_p_qbo_payments", "body"),
    Route("POST", r"/api/onboarding/dismiss", "_p_onboarding_dismiss", "body"),
    Route("POST", r"/api/team/.+|/api/account/delete", "_p_team", "body"),
    Route("POST", r"/api/inbox/([^/]+)/([a-z-]+)", "_p_inbox", "body"),
    Route("POST", r"/api/sov/parse", "_p_sov_parse", "body"),
    Route("POST", r"/api/outbox/([^/]+)/retry", "_p_outbox_retry", "body"),
    # changes to the company's state
    Route("POST", r"/api/reset", "_m_reset", "mutate", owner=True),
    Route("POST", r"/api/jobs", "_m_create_job", "mutate"),
    Route("POST", r"/api/jobs/([^/]+)/([a-z-]+)(?:/([^/]+))?", "_m_job_action", "mutate"),
    Route("POST", r"/api/payers/([^/]+)/suggestion/([^/]+)/(add|dismiss)", "_m_payer_suggestion", "mutate"),
    Route("POST", r"/api/payers/([^/]+)/update", "_m_payer_update", "mutate"),
    Route("POST", r"/api/company", "_m_company_update", "mutate", owner=True),
    Route("POST", r"/api/auth/name", "_p_my_name", "body"),
]


def _co_args(ctx: Ctx) -> dict:
    return {"link_base": app_url(), "company_id": ctx.company_id}


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
    if body.get("period_cents") not in (None, ""):
        return domain.record_progress(st, j, 0, _text(body, "notes"), c.actor, period_cents=_number(body, "period_cents", "this draw"))
    return domain.record_progress(st, j, _number(body, "completed_cents", "the total done so far", 0), _text(body, "notes"), c.actor)


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


# /api/jobs/<job>/<action>[/<item>]: action -> (takes an item id, handler(handler, state, job_id, item, ctx)).
JOB_ACTIONS = {
    "done": (False, lambda h, st, j, i, c: domain.mark_done(st, j, c.actor, _text(c.body, "notes"))),
    "progress": (False, _progress),
    "sov": (False, lambda h, st, j, i, c: domain.save_sov(st, j, _list(c.body, "lines"), c.body.get("op_pct"), c.body.get("update_contract") is True, c.actor)),
    "sov-progress": (False, lambda h, st, j, i, c: domain.record_sov_progress(st, j, c.body.get("done"), _text(c.body, "notes"), c.actor, stored=c.body.get("stored"))),
    "resolve": (True, lambda h, st, j, i, c: domain.resolve_packet_item(st, j, i, c.actor, reason=_text(c.body, "reason"))),
    "draft": (False, lambda h, st, j, i, c: domain.draft_invoice(st, j, actor=c.actor, strict=True)),
    "send": (False, _sends("the pay app", lambda h, st, j, i, c: domain.approve_and_send(st, j, c.actor, **_co_args(c)))),
    "follow-up": (False, _sends("the follow-up", lambda h, st, j, i, c: domain.record_follow_up(st, j, c.actor, **_co_args(c)))),
    "match-payment": (False, lambda h, st, j, i, c: domain.match_payment(st, j, _number(c.body, "amount_cents", "the payment amount"), _text(c.body, "reference"), c.actor,
                                                                         invoice_id=_text(c.body, "invoice_id") or None, received_on=_received_on(c.body))),
    "waiver": (False, _sends("the waiver", lambda h, st, j, i, c: domain.send_waiver(st, j, c.actor, invoice_id=_text(c.body, "invoice_id") or None, kind=_text(c.body, "kind") or None))),
    "retainage": (False, _sends("the request", lambda h, st, j, i, c: domain.chase_retainage(st, j, c.actor))),
    "retainage-paid": (False, lambda h, st, j, i, c: domain.record_retainage_payment(st, j, _number(c.body, "amount_cents", "the retainage received"), _text(c.body, "reference"), c.actor)),
    "add-co": (False, lambda h, st, j, i, c: domain.add_change_order(st, j, _text(c.body, "title"), _number(c.body, "amount_cents", "the change order amount", 0), c.actor, description=_text(c.body, "description"))),
    # A change order is approved by a signature: online (request-co-signature) or a signed
    # copy on the job record. A bare status flip is not accepted (P2-3).
    "approve-co": (True, lambda h, st, j, i, c: domain.mark_co_signed(st, j, i, _text(c.body, "record_id"), c.actor)),
    "request-co-signature": (True, _sends("the request", lambda h, st, j, i, c: domain.request_co_signature(st, j, i, c.actor, **_co_args(c)))),
    "include-co": (True, lambda h, st, j, i, c: domain.include_change_order(st, j, i, c.actor)),
    "withdraw-co": (True, lambda h, st, j, i, c: domain.withdraw_change_order(st, j, i, c.actor)),
    "void-draft": (False, lambda h, st, j, i, c: domain.void_draft(st, j, c.actor)),
    "pencil": (False, _sends("the draft", lambda h, st, j, i, c: domain.send_pencil(st, j, c.actor))),
    "pencil-response": (False, lambda h, st, j, i, c: domain.record_pencil_response(st, j, c.body.get("agreed") is True, _text(c.body, "note"), c.actor)),
    "add-evidence": (False, lambda h, st, j, i, c: domain.add_evidence(st, j, _filled(c.body, "label", "Enter what this evidence is."), _text(c.body, "kind", "note"), c.actor)),
    "add-record": (False, lambda h, st, j, i, c: domain.add_job_record(st, j, _text(c.body, "label"), _text(c.body, "kind", "note"), _text(c.body, "note"), c.actor)),
    "attach-record": (True, lambda h, st, j, i, c: domain.attach_record_to_packet(st, j, i, _text(c.body, "record_id"), c.actor)),
    "detach": (True, lambda h, st, j, i, c: domain.detach_packet_item(st, j, i, c.actor)),
    "packet-skip": (True, lambda h, st, j, i, c: domain.skip_packet_item(st, j, i, _text(c.body, "reason"), c.actor)),
    "learn-rejection": (False, lambda h, st, j, i, c: domain.learn_rejection(st, j, _filled(c.body, "reason", "Enter why the customer sent the pay app back."), c.actor)),
    "import-qbo": (False, _import_qbo),
    "close": (False, lambda h, st, j, i, c: domain.close_job(st, j, c.actor)),
    "edit": (False, _edit_job),
    "delete": (False, lambda h, st, j, i, c: domain.delete_job(st, j, c.actor)),
    "daily-record": (False, lambda h, st, j, i, c: domain.add_daily_record(st, j, _filled(c.body, "notes", "Enter what happened on the job."), c.actor)),
    "attach": (True, lambda h, st, j, i, c: domain.add_packet_attachment(st, j, i, _text(c.body, "name", "attachment"), c.actor)),
}

def _parse_share(data: bytes, raw_ctype: str) -> dict:
    """The multipart form a phone's share menu posts to /share: title, text, url and files."""
    out = {"title": "", "text": "", "url": "", "files": []}
    if "multipart/form-data" not in raw_ctype.lower():
        return out
    import email as _email
    import email.policy as _policy
    msg = _email.message_from_bytes(b"Content-Type: " + raw_ctype.encode("latin-1", "replace") + b"\r\n\r\n" + data, policy=_policy.default)
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename or name == "files":
            if payload:
                out["files"].append({"filename": filename or "shared file", "data": payload})
        elif name in {"title", "text", "url"}:
            out[name] = payload.decode("utf-8", "replace")[:5000]
    out["files"] = out["files"][:20]
    return out


def _parse_inbound_email(data: bytes, raw_ctype: str) -> dict:
    """Accept raw MIME, a JSON payload, or a multipart form post from an email provider.

    Field names below are the common ones for SendGrid Inbound Parse, Mailgun
    routes and Postmark inbound, from their documentation as I remember it;
    they have not been tested against the live services.
    """
    ctype = raw_ctype.lower()
    if "application/json" in ctype:
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequest("Request body must be valid JSON.")
        if not isinstance(raw, dict):
            raise BadRequest("Request body must be a JSON object.")
        low = {k.lower(): v for k, v in raw.items()}
        if isinstance(low.get("email"), str):
            return importer.parse_email(low["email"].encode("utf-8"))
        text = low.get("text") or low.get("textbody") or low.get("body-plain") or low.get("stripped-text") or ""
        if not text and (low.get("html") or low.get("htmlbody")):
            text = importer.html_to_text(str(low.get("html") or low.get("htmlbody")))
        attachments = []
        raw_atts = low.get("attachments")
        for att in raw_atts if isinstance(raw_atts, list) else []:
            if not isinstance(att, dict):
                continue
            a = {str(k).lower(): v for k, v in att.items()}
            content = a.get("content") or a.get("content_base64") or ""
            if not isinstance(content, str):
                continue
            try:
                blob = base64.b64decode(content, validate=False)
            except (ValueError, TypeError):
                continue
            name = a.get("name") or a.get("filename")
            attachments.append({"filename": name if isinstance(name, str) else "attachment", "data": blob})
        return {"from": str(low.get("from") or low.get("sender") or "")[:300], "subject": str(low.get("subject") or "")[:300],
                "text": str(text), "sent_at": "", "attachments": attachments}
    if "multipart/form-data" in ctype:
        import email as _email
        import email.policy as _policy
        msg = _email.message_from_bytes(b"Content-Type: " + raw_ctype.encode("latin-1", "replace") + b"\r\n\r\n" + data, policy=_policy.default)
        fields, attachments = {}, []
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition") or ""
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                attachments.append({"filename": filename, "data": payload})
            else:
                fields[name.lower()] = payload.decode("utf-8", "replace")
        if fields.get("email"):
            parsed = importer.parse_email(fields["email"].encode("utf-8"))
            parsed["attachments"] += attachments
            return parsed
        text = fields.get("text") or fields.get("body-plain") or fields.get("stripped-text") or ""
        if not text and fields.get("html"):
            text = importer.html_to_text(fields["html"])
        return {"from": fields.get("from") or fields.get("sender") or "", "subject": fields.get("subject", ""),
                "text": text, "sent_at": "", "attachments": attachments}
    return importer.parse_email(data)


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
        {"auth": auth.LOCK, "tokens": importer.TOKENS_LOCK},
        log=lambda msg: log_json("info", msg=msg),
    )
    start_monthly_jobs()
    server.serve_forever()


if __name__ == "__main__":
    main()
