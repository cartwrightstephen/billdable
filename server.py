"""Local Billdable server with company isolation and cookie sessions.

    python3 server.py
    open http://127.0.0.1:8765

Each company has its own JSON file under <data dir>/companies/. Auth lives in
<data dir>/auth.json. The data dir is BILLDABLE_DATA_DIR, default data/ beside
this file. Only index.html, app.js and styles.css are served from static/.
"""

from __future__ import annotations

import base64
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
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlparse

import auth
import backup
import calendar_feed
import domain
import helper
import importer
import legal
import mailer
import payapp_pdf
import sov
import storage
import trial
from seed import seed_state

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
COMPANIES = storage.DATA_DIR / "companies"
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "0.0.0.0")
COOKIE = "billdable_session"
STATE_LOCK = threading.Lock()
STUCK_MINUTES = 30  # an email still "sending" after this long died mid-send and can be retried
MAX_BODY_BYTES = 1_000_000
MAX_INBOUND_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 5_000
BODY_SECONDS = 60  # a request body must arrive within this long in total, however slowly it trickles

# The only files served from static/. Anything else is a 404.
STATIC_FILES = {
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
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
    url = (os.environ.get("BILLDABLE_URL") or "").strip().rstrip("/")
    return url if re.match(r"^https?://[^\s/]+", url) else None


def production() -> bool:
    """BILLDABLE_ENV=production: no demo tenant, and startup refuses a half-set configuration."""
    return (os.environ.get("BILLDABLE_ENV") or "").strip().lower() == "production"


def demo_enabled() -> bool:
    # The demo account's password is public, so it never exists in production.
    return not production()


def data_notice() -> bool:
    """Show "data may be erased" on the landing page until someone has checked that a
    deploy kept the data (BILLDABLE_DATA_PERSISTENT=1)."""
    return os.environ.get("BILLDABLE_DATA_PERSISTENT") != "1"


def config_problems() -> list[str]:
    """What stops a production start. Empty outside production."""
    if not production():
        return []
    problems = []
    if not os.environ.get("BILLDABLE_DATA_DIR"):
        problems.append("BILLDABLE_DATA_DIR is not set. Point it at the persistent disk.")
    elif not storage.DATA_DIR.is_dir():
        problems.append(f"BILLDABLE_DATA_DIR ({storage.DATA_DIR}) does not exist. Is the disk mounted?")
    if os.environ.get("COOKIE_SECURE") != "1":
        problems.append("COOKIE_SECURE must be 1.")
    url = app_url()
    if not url or not url.startswith("https://"):
        problems.append("BILLDABLE_URL must be the site's https:// address, so emailed links work.")
    return problems


SECRET_PATHS = (
    (re.compile(r"(/api/inbound/(?:email|sms)/)[^/?\s]+"), r"\1[token]"),
    (re.compile(r"(/api/calendar/)[^/?\s]+?(\.ics)"), r"\1[token]\2"),
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
            "books": "QuickBooks Online",
        },
        "payers": [],
        "jobs": [],
        "audit": [],
        "email_log": [],
    }


def company_path(company_id: str) -> Path:
    return COMPANIES / f"{company_id}.json"


def load_company(company_id: str) -> dict:
    path = company_path(company_id)
    if path.exists():
        return json.loads(path.read_text())
    # A signed-in user whose company file is gone means data was lost
    # (for example a deploy without a persistent disk). Say so in the log.
    print(f"WARNING: company file missing for {company_id} at {path}; serving a blank company.", file=sys.stderr)
    return empty_state("New company")


def _deleted_path() -> Path:
    return storage.DATA_DIR / "deleted_companies.json"


def deleted_companies() -> set:
    try:
        return set(json.loads(_deleted_path().read_text()))
    except (OSError, ValueError):
        return set()


def save_company(company_id: str, state: dict) -> None:
    # A request that was already running when its company was deleted must not write it back.
    if company_id in deleted_companies():
        log_json("warn", msg="write to a deleted company refused", company=company_id)
        return
    storage.write_json(company_path(company_id), state)


def ensure_demo() -> None:
    if not auth.user_by_email(auth.DEMO_EMAIL):
        auth.create_user(
            auth.DEMO_EMAIL,
            auth.DEMO_PASSWORD,
            "Alex Hartline",
            auth.DEMO_COMPANY_ID,
            enforce_policy=False,
        )
    if not company_path(auth.DEMO_COMPANY_ID).exists():
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


def _whole_number(body: dict, key: str, label: str, default: int | None = None) -> int:
    value = body.get(key, default)
    if value is None or value == "":
        if default is None:
            raise BadRequest(f"{label} is required.")
        return default
    if isinstance(value, bool):
        raise BadRequest(f"{label} must be a number.")
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise BadRequest(f"{label} must be a number.")
        return int(round(value))
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise BadRequest(f"{label} must be a number.")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


class Handler(BaseHTTPRequestHandler):
    server_version = "Billdable"
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
        log_json("error", rid=self._rid, path=redact(urlparse(self.path).path), trace=traceback.format_exc())
        self._json(500, {"error": "Something went wrong on our side. It has been logged.", "request_id": self._rid})

    def _send(self, code: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
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
            with STATE_LOCK:
                state = load_company(company_id)
                fn(state)
                save_company(company_id, state)
                return 200, domain.snapshot(state)
        except domain.GateError as exc:
            return 409, {"error": str(exc), "blockers": exc.blockers}

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
            self._json(exc.code, {"error": str(exc)})
        except Exception:
            self._crash()

    def do_POST(self) -> None:
        self._rid, self._t0 = secrets.token_hex(6), time.monotonic()
        try:
            self._post()
        except BadRequest as exc:
            self._json(exc.code, {"error": str(exc)})
        except Exception:
            self._crash()

    def _get(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
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
                "env": "production" if production() else "development",
            })
            return
        if path == "/api/team":
            user = self._require_user()
            if not user:
                return
            data = auth.team(user["company_id"])
            if user["role"] != "owner":
                data["invites"] = []
            self._json(200, data)
            return
        if path == "/api/admin/companies":
            user = self._require_user()
            if not user:
                return
            if not is_helper_admin(user):
                self._json(403, {"error": "Only the Billdable team can see this."})
                return
            self._json(200, {"companies": self._company_overview()})
            return
        m = re.match(r"^/api/jobs/([^/]+)/payapp/([^/]+)\.pdf$", path)
        if m:
            user = self._require_user()
            if not user:
                return
            with STATE_LOCK:
                state = load_company(user["company_id"])
            pdf, name = self._payapp_pdf(state, m.group(1), m.group(2))
            if not pdf:
                self._json(404, {"error": "Pay app not found."})
                return
            self._send(200, pdf, "application/pdf", {"Content-Disposition": f'inline; filename="{name}"'})
            return
        if path == "/api/auth/me":
            user = self._session_user()
            if not user:
                self._json(401, {"error": "Not signed in.", "data_notice": data_notice()})
                return
            with STATE_LOCK:
                state = load_company(user["company_id"])
            self._json(200, {"user": user, "company": state.get("company", {}), "helper_admin": is_helper_admin(user),
                             "mail_ready": bool(mailer.configured() and app_url())})
            return
        if path == "/api/helper/review":
            user = self._require_user()
            if not user:
                return
            if not is_helper_admin(user):
                self._json(403, {"error": "Only the Billdable team can review helper questions."})
                return
            self._json(200, helper.review())
            return
        if path == "/api/state":
            user = self._require_user()
            if not user:
                return
            with STATE_LOCK:
                state = load_company(user["company_id"])
            self._json(200, domain.snapshot(state))
            return
        if path == "/api/export":
            user = self._require_user()
            if not user:
                return
            with STATE_LOCK:
                state = load_company(user["company_id"])
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
            self._send(
                200,
                json.dumps(state, indent=2).encode(),
                "application/json",
                {"Content-Disposition": f'attachment; filename="billdable-export-{stamp}.json"'},
            )
            return
        if path.startswith("/api/files/"):
            user = self._require_user()
            if not user:
                return
            file_id = path.rsplit("/", 1)[-1]
            with STATE_LOCK:
                state = load_company(user["company_id"])
            meta = next((f for f in state.get("files", []) if f["id"] == file_id), None)
            target = importer.file_path(user["company_id"], meta) if meta else None
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
            return
        if path == "/api/import/settings":
            user = self._require_user()
            if not user:
                return
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
            return
        if path.startswith("/api/calendar/") and path.endswith(".ics"):
            token = path[len("/api/calendar/"):-len(".ics")]
            company_id = importer.company_for_token(token, "calendar")
            if not company_id:
                self._json(404, {"error": "Not found"})
                return
            with STATE_LOCK:
                state = load_company(company_id)
            self._send(200, calendar_feed.render(state).encode("utf-8"), "text/calendar; charset=utf-8",
                       {"Content-Disposition": 'inline; filename="billdable.ics"'})
            return
        if path in {"/terms", "/privacy"}:
            self._send(200, legal.page(path.strip("/")).encode("utf-8"), "text/html; charset=utf-8")
            return
        if path in {"/", "/index.html"}:
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if path in STATIC_FILES:
            name, kind = STATIC_FILES[path]
            self._send(200, (STATIC / name).read_bytes(), kind)
            return
        self._json(404, {"error": "Not found"})

    def _post(self) -> None:
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]

        if path == "/api/auth/login":
            body = self._read_json()
            try:
                user = auth.verify_login(_text(body, "email"), _text(body, "password"))
            except auth.RateLimited as exc:
                self._json(429, {"error": str(exc)})
                return
            if not user or (not demo_enabled() and user["company_id"] == auth.DEMO_COMPANY_ID):
                self._json(401, {"error": "Email or password is wrong."})
                return
            token = auth.create_session(user)
            self._json(200, {"user": user}, self._set_cookie(token))
            return

        if path == "/api/auth/signup":
            body = self._read_json()
            if body.get("accept_terms") is not True:
                self._json(400, {"error": "To create an account, accept the Terms of Service and the Privacy Policy."})
                return
            try:
                company_id = "co-" + secrets.token_hex(4)
                user = auth.create_user(
                    _text(body, "email"),
                    _text(body, "password"),
                    _text(body, "name"),
                    company_id,
                    terms_version=legal.TERMS_VERSION,
                )
                fresh = empty_state(_text(body, "company_name").strip() or "New company")
                trial.start(fresh["company"])
                with STATE_LOCK:
                    save_company(company_id, fresh)
                token = auth.create_session(user)
                verification = self._send_verification(user)
                self._json(200, {"user": user, "verification": verification}, self._set_cookie(token))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            return

        if path == "/api/auth/logout":
            auth.destroy_session(self._token())
            self._json(200, {"ok": True}, self._set_cookie(None))
            return

        if path == "/api/auth/forgot":
            body = self._read_json()
            url = app_url()
            if not (mailer.configured() and url):
                self._json(200, {"message": "Password reset by email isn't set up on this server yet. Ask the Billdable team to reset it."})
                return
            requested = _text(body, "email", limit=300)

            def send_reset() -> None:
                found = auth.password_reset_token(requested)
                if found:
                    raw, account = found
                    self._system_email(
                        account["email"], "Reset your Billdable password",
                        f"Someone asked to reset the password for {account['email']} on Billdable.\n\n"
                        f"Choose a new password here (the link works once, for one hour):\n{url}/#reset={raw}\n\n"
                        "If it wasn't you, ignore this email; your password stays the same.",
                        company_id=account["company_id"],
                    )

            # All the work happens in the background, so the answer takes the same time
            # whether or not the account exists.
            threading.Thread(target=send_reset, daemon=True).start()
            self._json(200, {"message": "If an account exists for that email, a reset link is on its way. It works for one hour."})
            return

        if path == "/api/auth/reset":
            body = self._read_json()
            try:
                user = auth.reset_password(body.get("token"), body.get("password"))
            except auth.AuthError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, {"user": user}, self._set_cookie(auth.create_session(user)))
            return

        if path == "/api/auth/verify":
            body = self._read_json()
            try:
                user = auth.verify_email(body.get("token"))
            except auth.AuthError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, {"user": user})
            return

        if path == "/api/auth/invite-info":
            body = self._read_json()
            info = auth.invite_info(body.get("token"))
            if not info:
                self._json(404, {"error": "This invite is invalid or has expired. Ask for a new one."})
                return
            with STATE_LOCK:
                company = load_company(info["company_id"]).get("company", {})
            self._json(200, {"email": info["email"], "role": info["role"], "company_name": company.get("name", ""),
                             "invited_by": info["invited_by"]})
            return

        if path == "/api/auth/accept-invite":
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
            return

        if path.startswith("/api/inbound/email/"):
            self._inbound_email(path.rsplit("/", 1)[-1])
            return
        if path.startswith("/api/inbound/sms/"):
            self._inbound_sms(path.rsplit("/", 1)[-1])
            return

        # Everything below needs a signed-in user; check before reading the body.
        user = self._require_user()
        if not user:
            return
        actor = user.get("name") or user.get("email") or "owner"
        if path == "/api/import/file":
            self._import_file(user, actor)
            return
        body = self._read_json()
        # Check the session again now the body is here: someone removed, demoted or signed out
        # while their request was arriving must not finish it with their old rights.
        user = self._session_user()
        if not user:
            self._json(401, {"error": "Sign in required."})
            return
        actor = user.get("name") or user.get("email") or "owner"
        company_id = user["company_id"]
        # The audit trail names the signed-in user. Any "actor" in the body is ignored.
        if path == "/api/helper/ask":
            with STATE_LOCK:
                state = load_company(company_id)
            try:
                reply = helper.answer(_text(body, "q"), state, company_id, user["id"], _text(body, "view"), _text(body, "job_id"))
            except domain.GateError as exc:
                self._json(409, {"error": str(exc)})
                return
            self._json(200, reply)
            return
        if path == "/api/helper/feedback":
            try:
                helper.feedback(_text(body, "log_id"), body.get("helpful") is True, _text(body, "note"), company_id)
            except domain.GateError as exc:
                self._json(409, {"error": str(exc)})
                return
            self._json(200, {"ok": True})
            return
        if path in {"/api/helper/answers", "/api/helper/resolve"}:
            if not is_helper_admin(user):
                self._json(403, {"error": "Only the Billdable team can edit helper answers."})
                return
            try:
                if path == "/api/helper/answers":
                    saved = helper.save_answer(body, user.get("email", ""))
                    self._json(200, {"saved": saved, **helper.review()})
                else:
                    helper.resolve(_text(body, "normalized"))
                    self._json(200, helper.review())
            except domain.GateError as exc:
                self._json(409, {"error": str(exc)})
            return
        if path in {"/api/reset", "/api/import/rotate"} and user["role"] != "owner":
            self._json(403, {"error": "Only an owner can do that."})
            return
        if path == "/api/auth/resend-verify":
            if user.get("email_verified"):
                self._json(409, {"error": "Your email is already verified."})
                return
            result = self._send_verification(user)
            if result != "sent":
                self._json(409, {"error": f"No verification email was sent: {result}."})
                return
            self._json(200, {"ok": True, "message": f"A verification link is on its way to {user['email']}."})
            return
        if path == "/api/auth/password":
            try:
                auth.change_password(user["id"], body.get("current"), body.get("new"), keep_session=self._token())
            except auth.RateLimited as exc:
                self._json(429, {"error": str(exc)})
                return
            except auth.AuthError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, {"ok": True, "message": "Password changed. Every other device is signed out."})
            return
        if path == "/api/onboarding/dismiss":
            self._json(*self._change(company_id, lambda st: st["company"].__setitem__("onboarding_dismissed", True)))
            return
        if path.startswith("/api/team/") or path == "/api/account/delete":
            self._team_action(path, parts, body, user)
            return
        if path == "/api/import/text":
            file_now = (parse_qs(urlparse(self.path).query).get("file") or [""])[0] == "1"
            self._json(*self._change(company_id, lambda st: self._import_text(st, body, actor if file_now else None)))
            return
        if path.startswith("/api/inbox/") and len(parts) == 4:
            item_id, verb = parts[2], parts[3]
            if verb == "file":
                fn = lambda st: importer.file_item(st, item_id, _text(body, "job_id"), _text(body, "kind"), actor)
            elif verb == "dismiss":
                fn = lambda st: importer.dismiss_item(st, item_id, actor)
            elif verb == "undo":
                fn = lambda st: importer.undo_item(st, item_id, actor)
            else:
                self._json(404, {"error": "Unknown action"})
                return
            self._json(*self._change(company_id, fn))
            return
        if path == "/api/sov/parse":
            # Read pasted rows; nothing is saved until the schedule itself is saved.
            self._json(200, sov.parse_rows(_text(body, "text", limit=200_000)))
            return
        if path.startswith("/api/outbox/") and len(parts) == 4 and parts[3] == "retry":
            self._retry_email(company_id, user, parts[2])
            return
        if path == "/api/import/rotate":
            kind = _text(body, "kind")
            if kind not in {"email", "sms", "calendar"}:
                raise BadRequest("Kind must be email, sms or calendar.")
            importer.rotate_token(company_id, kind)
            self._json(200, {"ok": True})
            return

        try:
            with STATE_LOCK:
                code, payload = self._mutate(path, parts, body, company_id, actor)
        except domain.GateError as exc:
            self._json(409, {"error": str(exc), "blockers": exc.blockers})
            return
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            self._json(400, {"error": f"Bad request: {exc}"})
            return
        if code == 200:
            # Approved emails are delivered after the change is saved and the lock is released.
            payload = self._deliver_queued(company_id, user) or payload
        # The reply is written after the lock is released, so a client that
        # stops reading cannot stall every other company's requests.
        self._json(code, payload)

    # ----- accounts and team -----

    def _system_email(self, to: str, subject: str, body: str, company_id: str = "") -> str:
        """An email from Billdable itself (reset, verification, invite). Returns "sent" or the reason it wasn't.
        It counts against the company's daily limit like any other email."""
        if not mailer.configured():
            return "email delivery is not set up on this server"
        cfg = mailer.settings()
        if company_id and mailer.sent_in_last_day(company_id) >= cfg["daily_limit"]:
            return f"the daily limit of {cfg['daily_limit']} emails is reached; try tomorrow"
        try:
            msg, _ = mailer.build_message({"to": to, "subject": subject, "body": body}, "", "", [], cfg, sender_name="Billdable")
            error = mailer.send([msg], cfg)[0]
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
        if error:
            log_json("warn", msg="system email failed", reason=str(error)[:200], rid=self._rid)
        elif company_id:
            mailer.record_sent(company_id, 1)
        return "sent" if not error else str(error)

    def _send_verification(self, user: dict) -> str:
        url = app_url()
        if user["company_id"] == auth.DEMO_COMPANY_ID:
            return "the demo account isn't verified"
        if not (mailer.configured() and url):
            return "email delivery or BILLDABLE_URL is not set up on this server"
        if not _within_limit(f"verify:{user['id']}", 3, 3600):
            return "three links were already sent in the last hour; use the newest, or try later"
        raw = auth.verify_email_token(user["id"])
        return self._system_email(
            user["email"], "Confirm your email for Billdable",
            f"Hi {user['name']},\n\nConfirm that this is your email address (the link works for 3 days):\n{url}/#verify={raw}\n\n"
            "Billdable emails your pay apps from this address's account, with replies coming back to it, "
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
                        "email_status": "BILLDABLE_URL is not set on this server" if not url else "not sent"}
                if url:
                    with STATE_LOCK:
                        company_name = load_company(company_id).get("company", {}).get("name", "")
                    # Names typed by users go in the body only, flattened and shortened; the subject is fixed.
                    flat = lambda text, n: " ".join(str(text).split())[:n]
                    status = self._system_email(
                        invite["email"], "You're invited to a team on Billdable",
                        f"{flat(user['name'], 80)} ({user['email']}) invited you to join the company "
                        f"\"{flat(company_name, 80)}\" on Billdable as {invite['role']}.\n\n"
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
        with STATE_LOCK:
            name = load_company(company_id).get("company", {}).get("name", "")
        if _text(body, "confirm").strip() != name:
            self._json(409, {"error": "Type your company name exactly to confirm. Nothing was deleted."})
            return
        removed = auth.delete_company_users(company_id)  # every session of the company ends here
        with STATE_LOCK:
            gone = sorted(deleted_companies() | {company_id})
            storage.write_json(_deleted_path(), gone)
            for path in [company_path(company_id), *(COMPANIES / "snapshots").glob(f"{company_id}-*.json")]:
                try:
                    path.unlink()
                except OSError:
                    pass
        shutil.rmtree(importer.UPLOADS / company_id, ignore_errors=True)
        importer.forget_company(company_id)
        helper.forget_company(company_id)
        mailer.forget_company(company_id)
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
        return payapp_pdf.render(state.get("company", {}).get("name", ""), job, payer, invoice), name

    def _company_overview(self) -> list:
        users = auth.all_users()
        out = []
        for path in sorted(COMPANIES.glob("*.json")) if COMPANIES.exists() else []:
            company_id = path.stem
            with STATE_LOCK:
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
                "demo": company_id == auth.DEMO_COMPANY_ID,
            })
        return out

    # ----- email delivery -----

    def _deliver_queued(self, company_id: str, user: dict, only: set | None = None) -> dict | None:
        """Send this company's queued emails. Returns the new snapshot, or None if nothing was queued."""
        cfg = mailer.settings()
        now = datetime.now(timezone.utc)
        stale = (now - timedelta(minutes=STUCK_MINUTES)).isoformat(timespec="seconds")
        with STATE_LOCK:
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
                return domain.snapshot(state)
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
            save_company(company_id, state)
            if not work:
                return domain.snapshot(state)
        # Outside the lock: read attachments and talk to the mail server.
        outcome: dict = {}
        try:
            messages = []
            for entry, attach in work:
                try:
                    msg, left_off = mailer.build_message(entry, company_name, entry.get("reply_to", ""), attach, cfg)
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
        with STATE_LOCK:
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
            return domain.snapshot(state)

    def _retry_email(self, company_id: str, user: dict, email_id: str) -> None:
        stale = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_MINUTES)).isoformat(timespec="seconds")
        code, payload = 200, None
        with STATE_LOCK:
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
                    payload = domain.snapshot(state)
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
            self._json(409, {"error": str(exc), "blockers": exc.blockers})
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
                importer.file_item(state, item["id"], job_id, item["kind"], actor)

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
        # Keep the header as sent: a multipart boundary is case-sensitive.
        parsed = _parse_inbound_email(data, self.headers.get("Content-Type") or "")
        stored, skipped = self._store_email_files(company_id, parsed, "inbound email")

        def add(state: dict) -> None:
            for meta in stored:
                importer.register_file(state, meta)
            importer.add_item(
                state, "email", text=parsed.get("text", ""), sender=parsed.get("from", ""), subject=parsed.get("subject", ""),
                file_ids=[m["id"] for m in stored], sent_at=parsed.get("sent_at", ""),
                extra={"skipped_attachments": skipped} if skipped else None)

        status, payload = self._change(company_id, add)
        if status != 200:
            importer.discard(company_id, stored)
            self._json(status, payload)
            return
        self._json(200, {"ok": True})

    def _inbound_sms(self, token: str) -> None:
        company_id = importer.company_for_token(token, "sms")
        if not company_id:
            self._json(404, {"error": "Not found"})
            return
        data = self._read_raw(MAX_BODY_BYTES)
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
        if media:
            text += "\n\n" + "\n".join(f"Photo or file sent by text (not downloaded): {m['url']}" for m in media)

        def add(state: dict) -> None:
            importer.add_item(state, "sms", text=text, sender=params.get("From", ""), extra={"media": media} if media else None)

        status, _ = self._change(company_id, add)
        body = b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
        self._send(200 if status == 200 else 409, body, "text/xml; charset=utf-8")

    def _mutate(self, path: str, parts: list, body: dict, company_id: str, actor: str) -> tuple:
        """Apply one change under the state lock and return (status, payload)."""
        state = load_company(company_id)
        if path == "/api/reset":
            if company_id == auth.DEMO_COMPANY_ID:
                state = seed_state()
            else:
                name = state.get("company", {}).get("name", "New company")
                if _text(body, "confirm").strip() != name:
                    raise domain.GateError(
                        "Reset not done. Type your company name exactly to confirm.",
                        ["Reset deletes every job, payer and record in this company."],
                    )
                if company_path(company_id).exists():
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    snapshots = COMPANIES / "snapshots"
                    snapshots.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(company_path(company_id), snapshots / f"{company_id}-before-reset-{stamp}.json")
                kept = {k: v for k, v in state.get("company", {}).items() if k in ("trial", "onboarding_dismissed")}
                state = empty_state(name)
                state["company"].update(kept)  # a reset doesn't restart the trial
            save_company(company_id, state)
            return 200, domain.snapshot(state)
        if path == "/api/jobs" and len(parts) == 2:
            domain.create_job(
                state,
                _text(body, "name"),
                _text(body, "customer"),
                _whole_number(body, "contract_cents", "Contract amount", 0),
                address=_text(body, "address"),
                approver=_text(body, "approver"),
                ap_email=_text(body, "ap_email"),
                phone=_text(body, "phone"),
                role=_text(body, "role", "Owner"),
                retainage_pct=_whole_number(body, "retainage_pct", "Retainage", 0),
                packet_notes=_text(body, "packet_notes"),
                contract_number=_text(body, "contract_number"),
                actor=actor,
            )
            save_company(company_id, state)
            return 200, domain.snapshot(state)
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "jobs":
            job_id = parts[2]
            action = parts[3]
            if action == "done":
                domain.mark_done(state, job_id, actor, _text(body, "notes"))
            elif action == "progress":
                domain.record_progress(
                    state, job_id, _whole_number(body, "completed_cents", "Completed-to-date", 0), _text(body, "notes"), actor
                )
            elif action == "sov":
                domain.save_sov(
                    state, job_id, body.get("lines"), body.get("op_pct"), body.get("update_contract") is True, actor
                )
            elif action == "sov-progress":
                domain.record_sov_progress(state, job_id, body.get("done"), _text(body, "notes"), actor)
            elif action == "resolve" and len(parts) == 5:
                domain.resolve_packet_item(state, job_id, parts[4], actor, reason=_text(body, "reason"))
            elif action == "draft":
                domain.draft_invoice(state, job_id, actor=actor, strict=True)
            elif action == "send":
                domain.approve_and_send(state, job_id, actor)
            elif action == "follow-up":
                domain.record_follow_up(state, job_id, actor)
            elif action == "match-payment":
                domain.match_payment(
                    state,
                    job_id,
                    _whole_number(body, "amount_cents", "Payment amount"),
                    _text(body, "reference"),
                    actor,
                    invoice_id=_text(body, "invoice_id") or None,
                )
            elif action == "waiver":
                domain.send_waiver(state, job_id, actor, invoice_id=_text(body, "invoice_id") or None)
            elif action == "retainage":
                domain.chase_retainage(state, job_id, actor)
            elif action == "retainage-paid":
                domain.record_retainage_payment(
                    state, job_id, _whole_number(body, "amount_cents", "Retainage amount"), _text(body, "reference"), actor
                )
            elif action == "add-co":
                domain.add_change_order(
                    state, job_id, _text(body, "title"), _whole_number(body, "amount_cents", "Change order amount", 0), actor
                )
            elif action == "approve-co" and len(parts) == 5:
                domain.approve_change_order(state, job_id, parts[4], actor)
            elif action == "include-co" and len(parts) == 5:
                domain.include_change_order(state, job_id, parts[4], actor)
            elif action == "withdraw-co" and len(parts) == 5:
                domain.withdraw_change_order(state, job_id, parts[4], actor)
            elif action == "void-draft":
                domain.void_draft(state, job_id, actor)
            elif action == "pencil":
                domain.send_pencil(state, job_id, actor)
            elif action == "pencil-response":
                domain.record_pencil_response(state, job_id, body.get("agreed") is True, _text(body, "note"), actor)
            elif action == "add-evidence":
                domain.add_evidence(state, job_id, _text(body, "label"), _text(body, "kind", "note"), actor)
            elif action == "add-record":
                domain.add_job_record(
                    state, job_id, _text(body, "label"), _text(body, "kind", "note"), _text(body, "note"), actor
                )
            elif action == "attach-record" and len(parts) == 5:
                domain.attach_record_to_packet(state, job_id, parts[4], _text(body, "record_id"), actor)
            elif action == "learn-rejection":
                domain.learn_rejection(state, job_id, _text(body, "reason"), actor)
            elif action == "import-qbo":
                if company_id != auth.DEMO_COMPANY_ID:
                    raise domain.GateError(
                        "QuickBooks import is not built yet. The sample import only runs on the demo account."
                    )
                domain.import_sample_qbo_payment(state, job_id, actor)
            elif action == "close":
                domain.close_job(state, job_id, actor)
            elif action == "daily-record":
                domain.add_daily_record(state, job_id, _text(body, "notes"), actor)
            elif action == "attach" and len(parts) == 5:
                domain.add_packet_attachment(state, job_id, parts[4], _text(body, "name", "attachment"), actor)
            else:
                return 404, {"error": "Unknown action"}
            save_company(company_id, state)
            return 200, domain.snapshot(state)
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "payers" and parts[3] == "update":
            domain.update_payer(state, parts[2], body, actor)
            save_company(company_id, state)
            return 200, domain.snapshot(state)
        return 404, {"error": "Not found"}


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
    if demo_enabled():
        ensure_demo()
    server = Server((HOST, PORT), Handler)
    browse = "http://127.0.0.1" if HOST in {"0.0.0.0", ""} else f"http://{HOST}"
    print(f"Billdable running at {browse}:{PORT}{' (production)' if production() else ''}")
    print(f"Data directory: {storage.DATA_DIR}")
    if demo_enabled():
        print(f"Demo login: {auth.DEMO_EMAIL} / {auth.DEMO_PASSWORD}")
    backup.start_scheduler(
        {"companies": STATE_LOCK, "auth": auth.LOCK, "tokens": importer.TOKENS_LOCK},
        log=lambda msg: log_json("info", msg=msg),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
