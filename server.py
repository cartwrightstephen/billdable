"""Local Billdable server. No external services required.

    python3 server.py
    open http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import domain
from seed import seed_state

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
STATE_PATH = ROOT / "data" / "state.json"
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "0.0.0.0")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    state = seed_state()
    save_state(state)
    return state


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode() or "{}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(200, domain.snapshot(load_state()))
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
        state = load_state()
        try:
            if path == "/api/reset":
                state = seed_state()
                save_state(state)
                self._json(200, domain.snapshot(state))
                return
            if len(parts) >= 4 and parts[0] == "api" and parts[1] == "jobs":
                job_id = parts[2]
                action = parts[3]
                if action == "done":
                    domain.mark_done(state, job_id, body.get("actor", "owner"), body.get("notes", ""), body.get("evidence"))
                elif action == "resolve" and len(parts) == 5:
                    domain.resolve_packet_item(state, job_id, parts[4], body.get("actor", "owner"))
                elif action == "draft":
                    domain.draft_invoice(state, job_id)
                elif action == "send":
                    domain.approve_and_send(state, job_id, body.get("actor", "owner"))
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
                    domain.send_waiver(state, job_id, body.get("actor", "owner"))
                elif action == "retainage":
                    domain.chase_retainage(state, job_id)
                elif action == "add-co":
                    domain.add_change_order(state, job_id, body["title"], int(body["amount_cents"]), body.get("actor", "owner"))
                elif action == "approve-co" and len(parts) == 5:
                    domain.approve_change_order(state, job_id, parts[4], body.get("actor", "owner"))
                elif action == "add-evidence":
                    domain.add_evidence(state, job_id, body["label"], body.get("kind", "note"), body.get("actor", "owner"))
                elif action == "learn-rejection":
                    domain.learn_rejection(state, job_id, body["reason"], body.get("actor", "owner"))
                elif action == "import-qbo":
                    domain.import_sample_qbo_payment(state, job_id)
                elif action == "close":
                    domain.close_job(state, job_id, body.get("actor", "owner"))
                elif action == "daily-record":
                    domain.add_daily_record(state, job_id, body.get("notes", ""), body.get("actor", "owner"))
                elif action == "attach" and len(parts) == 5:
                    domain.add_packet_attachment(state, job_id, parts[4], body.get("name", "attachment"), body.get("actor", "owner"))
                else:
                    self._json(404, {"error": "Unknown action"})
                    return
                save_state(state)
                self._json(200, domain.snapshot(state))
                return
            if len(parts) >= 4 and parts[0] == "api" and parts[1] == "payers" and parts[3] == "update":
                domain.update_payer(state, parts[2], body, body.get("actor", "owner"))
                save_state(state)
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
    load_state()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Billdable running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
