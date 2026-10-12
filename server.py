"""Local Billdable server with company isolation and cookie sessions.

    python3 server.py
    open http://127.0.0.1:8765

Each company has its own JSON file under data/companies/. Auth lives in
data/auth.json. Domain rules are unchanged.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import auth
import domain
from seed import seed_state

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
COMPANIES = ROOT / "data" / "companies"
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "0.0.0.0")
COOKIE = "billdable_session"
STATE_LOCK = threading.Lock()


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
    # Missing file: give a blank company rather than crash.
    return empty_state("New company")


def save_company(company_id: str, state: dict) -> None:
    COMPANIES.mkdir(parents=True, exist_ok=True)
    company_path(company_id).write_text(json.dumps(state, indent=2))


def ensure_demo() -> None:
    if not auth.user_by_email(auth.DEMO_EMAIL):
        auth.create_user(
            auth.DEMO_EMAIL,
            auth.DEMO_PASSWORD,
            "Alex Hartline",
            auth.DEMO_COMPANY_ID,
        )
    if not company_path(auth.DEMO_COMPANY_ID).exists():
        save_company(auth.DEMO_COMPANY_ID, seed_state())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict, headers: dict | None = None) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json", headers)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode() or "{}")

    def _token(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        jar = SimpleCookie()
        jar.load(raw)
        morsel = jar.get(COOKIE)
        return morsel.value if morsel else None

    def _set_cookie(self, token: str | None) -> dict:
        if token:
            value = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={60 * 60 * 24 * auth.SESSION_DAYS}"
        else:
            value = f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        return {"Set-Cookie": value}

    def _require_user(self) -> dict | None:
        user = auth.session_user(self._token())
        if not user:
            self._json(401, {"error": "Sign in required."})
            return None
        return user

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/auth/me":
            user = auth.session_user(self._token())
            if not user:
                self._json(401, {"error": "Not signed in."})
                return
            state = load_company(user["company_id"])
            self._json(200, {"user": user, "company": state.get("company", {})})
            return
        if path == "/api/state":
            user = self._require_user()
            if not user:
                return
            self._json(200, domain.snapshot(load_company(user["company_id"])))
            return
        if path in {"/", "/index.html"}:
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        target = STATIC / path.lstrip("/")
        if target.is_file():
            kind = "text/css" if target.suffix == ".css" else "text/javascript"
            self._send(200, target.read_bytes(), kind)
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        body = self._read_json()

        if path == "/api/auth/login":
            user = auth.verify_login(body.get("email", ""), body.get("password", ""))
            if not user:
                self._json(401, {"error": "Email or password is wrong."})
                return
            token = auth.create_session(user)
            self._json(200, {"user": user}, self._set_cookie(token))
            return

        if path == "/api/auth/signup":
            try:
                company_id = "co-" + secrets.token_hex(4)
                user = auth.create_user(
                    body.get("email", ""),
                    body.get("password", ""),
                    body.get("name", ""),
                    company_id,
                )
                with STATE_LOCK:
                    save_company(company_id, empty_state(body.get("company_name") or "New company"))
                token = auth.create_session(user)
                self._json(200, {"user": user}, self._set_cookie(token))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            return

        if path == "/api/auth/logout":
            auth.destroy_session(self._token())
            self._json(200, {"ok": True}, self._set_cookie(None))
            return

        user = self._require_user()
        if not user:
            return
        company_id = user["company_id"]

        try:
            with STATE_LOCK:
                state = load_company(company_id)
                if path == "/api/reset":
                    if company_id == auth.DEMO_COMPANY_ID:
                        state = seed_state()
                    else:
                        state = empty_state(state.get("company", {}).get("name", "New company"))
                    save_company(company_id, state)
                    self._json(200, domain.snapshot(state))
                    return
                if path == "/api/jobs" and len(parts) == 2:
                    domain.create_job(
                        state,
                        body.get("name", ""),
                        body.get("customer", ""),
                        int(body.get("contract_cents") or 0),
                        address=body.get("address", ""),
                        approver=body.get("approver", ""),
                        ap_email=body.get("ap_email", ""),
                        phone=body.get("phone", ""),
                        role=body.get("role", "Owner"),
                        retainage_pct=int(body.get("retainage_pct") or 0),
                        packet_notes=body.get("packet_notes", ""),
                        contract_number=body.get("contract_number", ""),
                        actor=body.get("actor") or user.get("name") or "owner",
                    )
                    save_company(company_id, state)
                    self._json(200, domain.snapshot(state))
                    return
                if len(parts) >= 4 and parts[0] == "api" and parts[1] == "jobs":
                    job_id = parts[2]
                    action = parts[3]
                    actor = body.get("actor") or user.get("name") or "owner"
                    if action == "done":
                        domain.mark_done(state, job_id, actor, body.get("notes", ""), body.get("evidence"))
                    elif action == "progress":
                        domain.record_progress(state, job_id, int(body.get("completed_cents") or 0), body.get("notes", ""), actor)
                    elif action == "resolve" and len(parts) == 5:
                        domain.resolve_packet_item(state, job_id, parts[4], actor)
                    elif action == "draft":
                        domain.draft_invoice(state, job_id)
                    elif action == "send":
                        domain.approve_and_send(state, job_id, actor)
                    elif action == "follow-up":
                        domain.record_follow_up(state, job_id)
                    elif action == "match-payment":
                        domain.match_payment(
                            state,
                            job_id,
                            int(body["amount_cents"]),
                            body.get("reference", "QBO-manual"),
                        )
                    elif action == "waiver":
                        domain.send_waiver(state, job_id, actor)
                    elif action == "retainage":
                        domain.chase_retainage(state, job_id)
                    elif action == "add-co":
                        domain.add_change_order(state, job_id, body["title"], int(body["amount_cents"]), actor)
                    elif action == "approve-co" and len(parts) == 5:
                        domain.approve_change_order(state, job_id, parts[4], actor)
                    elif action == "add-evidence":
                        domain.add_evidence(state, job_id, body["label"], body.get("kind", "note"), actor)
                    elif action == "add-record":
                        domain.add_job_record(state, job_id, body.get("label", ""), body.get("kind", "note"), body.get("note", ""), actor)
                    elif action == "attach-record" and len(parts) == 5:
                        domain.attach_record_to_packet(state, job_id, parts[4], body["record_id"], actor)
                    elif action == "learn-rejection":
                        domain.learn_rejection(state, job_id, body["reason"], actor)
                    elif action == "import-qbo":
                        domain.import_sample_qbo_payment(state, job_id)
                    elif action == "close":
                        domain.close_job(state, job_id, actor)
                    elif action == "daily-record":
                        domain.add_daily_record(state, job_id, body.get("notes", ""), actor)
                    elif action == "attach" and len(parts) == 5:
                        domain.add_packet_attachment(state, job_id, parts[4], body.get("name", "attachment"), actor)
                    else:
                        self._json(404, {"error": "Unknown action"})
                        return
                    save_company(company_id, state)
                    self._json(200, domain.snapshot(state))
                    return
                if len(parts) >= 4 and parts[0] == "api" and parts[1] == "payers" and parts[3] == "update":
                    domain.update_payer(state, parts[2], body, body.get("actor") or user.get("name") or "owner")
                    save_company(company_id, state)
                    self._json(200, domain.snapshot(state))
                    return
        except domain.GateError as exc:
            self._json(409, {"error": str(exc), "blockers": exc.blockers})
            return
        except (KeyError, ValueError, TypeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(404, {"error": "Not found"})


def main() -> None:
    ensure_demo()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    browse = "http://127.0.0.1" if HOST in {"0.0.0.0", ""} else f"http://{HOST}"
    print(f"Billdable running at {browse}:{PORT}")
    print(f"Demo login: {auth.DEMO_EMAIL} / {auth.DEMO_PASSWORD}")
    server.serve_forever()


if __name__ == "__main__":
    main()
