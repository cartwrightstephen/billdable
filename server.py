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
import threading
import traceback
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlparse

import auth
import calendar_feed
import domain
import helper
import importer
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
MAX_INBOUND_BYTES = 25 * 1024 * 1024
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


def is_helper_admin(user: dict) -> bool:
    """Founders who may answer helper questions: emails listed in HELPER_ADMIN_EMAILS (comma-separated)."""
    allowed = {e.strip().lower() for e in os.environ.get("HELPER_ADMIN_EMAILS", "").split(",") if e.strip()}
    return bool(user) and user.get("email", "").lower() in allowed


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

    def _read_raw(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            raise BadRequest("Content-Length must be a number.")
        if length < 0:
            raise BadRequest("Content-Length must be a number.")
        if length > limit:
            raise BadRequest(f"Upload is larger than {limit // (1024 * 1024)} MB.", 413)
        return self.rfile.read(length) if length else b""

    def _base_url(self) -> str:
        proto = self.headers.get("X-Forwarded-Proto", "").lower() or ("https" if os.environ.get("COOKIE_SECURE") == "1" else "http")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or f"127.0.0.1:{PORT}"
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
            self._json(200, {"user": user, "company": state.get("company", {}), "helper_admin": is_helper_admin(user)})
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
            self._send(200, target.read_bytes(), kind, {
                "Content-Disposition": f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(meta["name"])}',
                "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
                "Cache-Control": "private, max-age=300",
            })
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
        # The reply is written after the lock is released, so a client that
        # stops reading cannot stall every other company's requests.
        self._json(code, payload)

    # ----- importing -----

    def _import_file(self, user: dict, actor: str) -> None:
        query = parse_qs(urlparse(self.path).query)
        job_id = (query.get("job") or [""])[0] or None
        file_now = bool(job_id) and (query.get("file") or [""])[0] == "1"
        filename = unquote(self.headers.get("X-Filename", "") or "upload")
        data = self._read_raw(importer.MAX_FILE_BYTES)

        def add(state: dict) -> None:
            if filename.lower().endswith(".eml"):
                item = self._add_email(state, user["company_id"], importer.parse_email(data), "upload", actor, job_id, raw=data, raw_name=filename)
            else:
                meta = importer.save_file(state, user["company_id"], filename, data, actor)
                text = data.decode("utf-8", "replace") if meta["ext"] in {".txt", ".csv"} else ""
                item = importer.add_item(state, "upload", text=text, subject=meta["name"], file_ids=[meta["id"]], job_id=job_id)
            if file_now:
                # Dropped on a job page: the person already chose the job.
                importer.file_item(state, item["id"], job_id, item["kind"], actor)

        self._json(*self._change(user["company_id"], add))

    def _import_text(self, state: dict, body: dict, file_as: str | None = None) -> None:
        """Add pasted text to the Inbox; with file_as (a person) and a job, file it straight away."""
        before = {i["id"] for i in state.get("inbox", [])}
        self._import_text_items(state, body)
        job_id = _text(body, "job_id") or None
        if file_as and job_id:
            for item in [i for i in state.get("inbox", []) if i["id"] not in before]:
                importer.file_item(state, item["id"], job_id, item["kind"], file_as)

    def _import_text_items(self, state: dict, body: dict) -> None:
        text = _text(body, "text").strip()
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

    def _add_email(self, state: dict, company_id: str, parsed: dict, source: str, actor: str,
                   job_id: str | None = None, raw: bytes | None = None, raw_name: str = "") -> dict:
        file_ids, skipped = [], []
        if raw is not None and raw_name:
            file_ids.append(importer.save_file(state, company_id, raw_name, raw, actor)["id"])
        for att in parsed.get("attachments", []):
            try:
                file_ids.append(importer.save_file(state, company_id, att["filename"], att["data"], actor)["id"])
            except domain.GateError as exc:
                skipped.append(str(exc))
        return importer.add_item(
            state, source, text=parsed.get("text", ""), sender=parsed.get("from", ""), subject=parsed.get("subject", ""),
            file_ids=file_ids, job_id=job_id, sent_at=parsed.get("sent_at", ""),
            extra={"skipped_attachments": skipped} if skipped else None,
        )

    def _inbound_email(self, token: str) -> None:
        company_id = importer.company_for_token(token, "email")
        if not company_id:
            self._json(404, {"error": "Not found"})
            return
        data = self._read_raw(MAX_INBOUND_BYTES)
        ctype = (self.headers.get("Content-Type") or "").lower()
        parsed = _parse_inbound_email(data, ctype)
        status, payload = self._change(company_id, lambda st: self._add_email(st, company_id, parsed, "email", "inbound email"))
        self._json(200 if status == 200 else status, {"ok": status == 200} if status == 200 else payload)

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


def _parse_inbound_email(data: bytes, ctype: str) -> dict:
    """Accept raw MIME, a JSON payload, or a multipart form post from an email provider.

    Field names below are the common ones for SendGrid Inbound Parse, Mailgun
    routes and Postmark inbound, from their documentation as I remember it;
    they have not been tested against the live services.
    """
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
        for att in low.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            a = {k.lower(): v for k, v in att.items()}
            content = a.get("content") or a.get("content_base64") or ""
            try:
                blob = base64.b64decode(content, validate=False)
            except (ValueError, TypeError):
                continue
            attachments.append({"filename": a.get("name") or a.get("filename") or "attachment", "data": blob})
        return {"from": str(low.get("from") or low.get("sender") or ""), "subject": str(low.get("subject") or ""),
                "text": str(text), "sent_at": "", "attachments": attachments}
    if "multipart/form-data" in ctype:
        import email as _email
        import email.policy as _policy
        msg = _email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + data, policy=_policy.default)
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
    ensure_demo()
    server = Server((HOST, PORT), Handler)
    browse = "http://127.0.0.1" if HOST in {"0.0.0.0", ""} else f"http://{HOST}"
    print(f"Billdable running at {browse}:{PORT}")
    print(f"Data directory: {storage.DATA_DIR}")
    print(f"Demo login: {auth.DEMO_EMAIL} / {auth.DEMO_PASSWORD}")
    server.serve_forever()


if __name__ == "__main__":
    main()
