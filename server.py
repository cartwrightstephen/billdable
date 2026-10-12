"""Local Billdable server with company isolation and cookie sessions.

    python3 server.py
    open http://127.0.0.1:8765

Each company has its own JSON file under <data dir>/companies/. Auth lives in
<data dir>/auth.json. The data dir is BILLDABLE_DATA_DIR, default data/ beside
this file. Only index.html, app.js and styles.css are served from static/.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import threading
import traceback
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import auth
import domain
import storage
from seed import seed_state

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
COMPANIES = storage.DATA_DIR / "companies"
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "0.0.0.0")
COOKIE = "billdable_session"
STATE_LOCK = threading.Lock()
MAX_BODY_BYTES = 1_000_000
MAX_TEXT_CHARS = 5_000

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


def save_company(company_id: str, state: dict) -> None:
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


def _text(body: dict, key: str, default: str = "") -> str:
    value = body.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise BadRequest(f"{key.replace('_', ' ').capitalize()} must be text.")
    if len(value) > MAX_TEXT_CHARS:
        raise BadRequest(f"{key.replace('_', ' ').capitalize()} is too long (limit {MAX_TEXT_CHARS} characters).")
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

    # One line per request goes to stderr (Render shows it in the service log).
    # The default formatter logs the request line only, never headers or cookies.

    def _send(self, code: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
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
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequest("Request body must be valid JSON.")
        if not isinstance(body, dict):
            raise BadRequest("Request body must be a JSON object.")
        return body

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
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _set_cookie(self, token: str | None) -> dict:
        secure = "; Secure" if self._secure_cookie() else ""
        if token:
            value = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={60 * 60 * 24 * auth.SESSION_DAYS}{secure}"
        else:
            value = f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}"
        return {"Set-Cookie": value}

    def _require_user(self) -> dict | None:
        user = auth.session_user(self._token())
        if not user:
            self._json(401, {"error": "Sign in required."})
            return None
        return user

    def do_GET(self) -> None:
        try:
            self._get()
        except Exception:
            traceback.print_exc()
            self._json(500, {"error": "Something went wrong on our side. It has been logged."})

    def do_POST(self) -> None:
        try:
            self._post()
        except BadRequest as exc:
            self._json(exc.code, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._json(500, {"error": "Something went wrong on our side. It has been logged."})

    def _get(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/auth/me":
            user = auth.session_user(self._token())
            if not user:
                self._json(401, {"error": "Not signed in."})
                return
            with STATE_LOCK:
                state = load_company(user["company_id"])
            self._json(200, {"user": user, "company": state.get("company", {})})
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
            if not user:
                self._json(401, {"error": "Email or password is wrong."})
                return
            token = auth.create_session(user)
            self._json(200, {"user": user}, self._set_cookie(token))
            return

        if path == "/api/auth/signup":
            body = self._read_json()
            try:
                company_id = "co-" + secrets.token_hex(4)
                user = auth.create_user(
                    _text(body, "email"),
                    _text(body, "password"),
                    _text(body, "name"),
                    company_id,
                )
                with STATE_LOCK:
                    save_company(company_id, empty_state(_text(body, "company_name").strip() or "New company"))
                token = auth.create_session(user)
                self._json(200, {"user": user}, self._set_cookie(token))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            return

        if path == "/api/auth/logout":
            auth.destroy_session(self._token())
            self._json(200, {"ok": True}, self._set_cookie(None))
            return

        # Everything below needs a signed-in user; check before reading the body.
        user = self._require_user()
        if not user:
            return
        body = self._read_json()
        company_id = user["company_id"]
        # The audit trail names the signed-in user. Any "actor" in the body is ignored.
        actor = user.get("name") or user.get("email") or "owner"

        try:
            with STATE_LOCK:
                code, payload = self._mutate(path, parts, body, company_id, actor)
        except domain.GateError as exc:
            self._json(409, {"error": str(exc), "blockers": exc.blockers})
            return
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            self._json(400, {"error": f"Bad request: {exc}"})
            return
        # The reply is written after the lock is released, so a client that
        # stops reading cannot stall every other company's requests.
        self._json(code, payload)

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
                state = empty_state(name)
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


def main() -> None:
    ensure_demo()
    server = Server((HOST, PORT), Handler)
    browse = "http://127.0.0.1" if HOST in {"0.0.0.0", ""} else f"http://{HOST}"
    print(f"Billdable running at {browse}:{PORT}")
    print(f"Data directory: {storage.DATA_DIR}")
    print(f"Demo login: {auth.DEMO_EMAIL} / {auth.DEMO_PASSWORD}")
    server.serve_forever()


if __name__ == "__main__":
    main()
