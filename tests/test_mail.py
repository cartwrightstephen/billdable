"""Email delivery: SMTP against a local test server, the Outbox statuses, retry, limits, and the lock."""

import email
import email.policy
import http.client
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import helper
import importer
import mailer
import server
import storage

PASSWORD = "correct horse battery"
MAIL_ENV = ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_DAILY_LIMIT")


class SinkHandler(socketserver.StreamRequestHandler):
    """Just enough SMTP to receive a message, refuse an address, or be slow."""

    def handle(self):
        def say(text):
            self.wfile.write((text + "\r\n").encode())

        say("220 sink ESMTP")
        mail_from, rcpts = None, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            cmd = raw.decode("utf-8", "replace").strip()
            up = cmd.upper()
            if up.startswith(("EHLO", "HELO")):
                say("250-sink")
                say("250-AUTH PLAIN LOGIN")
                say("250 8BITMIME")
            elif up.startswith("AUTH"):
                say("535 bad credentials" if self.server.refuse_login else "235 ok")
            elif up.startswith("MAIL FROM"):
                mail_from, rcpts = cmd[10:].strip(), []
                say("250 OK")
            elif up.startswith("RCPT TO"):
                addr = cmd[8:].strip().strip("<>")
                if addr in self.server.reject:
                    say("550 no such user")
                else:
                    rcpts.append(addr)
                    say("250 OK")
            elif up == "DATA":
                say("354 go ahead")
                lines = []
                while True:
                    line = self.rfile.readline()
                    if not line or line in (b".\r\n", b".\n"):
                        break
                    lines.append(line[1:] if line.startswith(b"..") else line)
                time.sleep(self.server.delay)
                self.server.messages.append({"from": mail_from, "to": rcpts, "data": b"".join(lines)})
                say("250 queued")
            elif up in ("RSET", "NOOP"):
                say("250 OK")
            elif up == "QUIT":
                say("221 bye")
                return
            else:
                say("502 not implemented")


class Sink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), SinkHandler)
        self.messages, self.reject, self.delay, self.refuse_login = [], set(), 0, False


def parsed(message):
    return email.message_from_bytes(message["data"], policy=email.policy.default)


class MailerUnitTests(unittest.TestCase):
    cfg = {"host": "127.0.0.1", "port": 0, "user": "", "password": "", "security": "none", "from": "billing@billdable.test", "daily_limit": 50}

    def test_headers_cannot_be_injected(self):
        entry = {"to": "ap@park.example", "subject": "Pay app\r\nBcc: victim@example.com", "body": "Hello"}
        msg, _ = mailer.build_message(entry, "Pine\nCo", "pat@pine.example", [], self.cfg)
        self.assertNotIn("Bcc", msg.keys())
        self.assertEqual(msg["Subject"], "Pay app Bcc: victim@example.com")
        self.assertIn("Pine Co via Billdable", str(msg["From"]))
        self.assertEqual(msg["Reply-To"], "pat@pine.example")
        with self.assertRaises(ValueError):
            mailer.build_message({"to": "a@b.example, c@d.example", "subject": "x", "body": ""}, "Pine", "", [], self.cfg)

    def test_attachments_over_the_limit_are_listed_not_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            small, big = Path(tmp) / "a.pdf", Path(tmp) / "b.pdf"
            small.write_bytes(b"%PDF small")
            big.write_bytes(b"0" * (mailer.MAX_ATTACH_BYTES + 1))
            msg, left = mailer.build_message(
                {"to": "ap@park.example", "subject": "s", "body": "b"}, "Pine", "",
                [("a.pdf", small), ("b.pdf", big), ("gone.pdf", Path(tmp) / "nope.pdf")], self.cfg,
            )
        names = [p.get_filename() for p in msg.iter_attachments()]
        self.assertEqual(names, ["a.pdf"])
        self.assertEqual(len(left), 2)
        self.assertIn("Not attached", msg.get_body(("plain",)).get_content())

    def test_send_reports_each_outcome(self):
        sink = Sink()
        threading.Thread(target=sink.serve_forever, daemon=True).start()
        try:
            cfg = dict(self.cfg, port=sink.server_address[1])
            sink.reject.add("nobody@park.example")
            good, _ = mailer.build_message({"to": "ap@park.example", "subject": "s", "body": "b"}, "Pine", "", [], cfg)
            bad, _ = mailer.build_message({"to": "nobody@park.example", "subject": "s", "body": "b"}, "Pine", "", [], cfg)
            results = mailer.send([good, bad], cfg)
            self.assertIsNone(results[0])
            self.assertIn("refused", results[1])
            self.assertEqual(len(sink.messages), 1)
            # Wrong login: nothing is sent and the reason says so.
            sink.refuse_login = True
            results = mailer.send([good], dict(cfg, user="u", password="p"))
            self.assertIn("refused the login", results[0])
        finally:
            sink.shutdown()
            sink.server_close()
        closed = mailer.send([good], dict(self.cfg, port=sink.server_address[1]))
        self.assertIn("could not connect", closed[0])


class MailHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH)
        auth.AUTH_PATH = root / "auth.json"
        server.COMPANIES = root / "companies"
        importer.UPLOADS = root / "uploads"
        storage.DATA_DIR = root
        helper.LOCAL_KB = root / "helper_answers.json"
        helper.LOG_PATH = root / "helper_log.json"
        server.ensure_demo()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.sink = Sink()
        threading.Thread(target=cls.sink.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.sink.shutdown()
        cls.sink.server_close()
        (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH) = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        self.sink.messages.clear()
        self.sink.reject.clear()
        self.sink.delay = 0
        self.sink.refuse_login = False
        self.mail_on()

    def tearDown(self):
        for key in MAIL_ENV:
            os.environ.pop(key, None)

    def mail_on(self, **extra):
        os.environ.update({
            "SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(self.sink.server_address[1]),
            "SMTP_SECURITY": "none", "MAIL_FROM": "billing@billdable.test", **extra,
        })

    def request(self, method, path, body=None, cookie=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
            hdrs["Content-Length"] = str(len(data))
        if cookie:
            hdrs["Cookie"] = cookie
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        return res, json.loads(payload or b"{}")

    def company(self, email_addr, ap="ap@park.example"):
        res, _ = self.request("POST", "/api/auth/signup", {"email": email_addr, "password": PASSWORD, "name": "Pat Pine", "company_name": "Pine & Sons"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        job = {"name": "Elm addition", "customer": "Park GC", "address": "9 Elm Street", "approver": "Kim", "ap_email": ap,
               "contract_cents": 3_450_000, "retainage_pct": 10, "role": "Owner"}
        _, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        return cookie, state["jobs"][0]

    def ready_to_send(self, cookie, job, attach=False):
        _, state = self.request("POST", f"/api/jobs/{job['id']}/progress", {"completed_cents": 1_000_000}, cookie=cookie)
        inv = state["jobs"][0]["invoices"][-1]
        rec_id = None
        if attach:
            res, state = self.request("POST", f"/api/import/file?job={job['id']}&file=1", raw=b"%PDF-1.4 signed contract", cookie=cookie,
                                      headers={"Content-Type": "application/pdf", "X-Filename": quote("contract.pdf")})
            self.assertEqual(res.status, 200, state)
            rec_id = state["jobs"][0]["records"][-1]["id"]
        for n, item in enumerate(inv["packet"]):
            if attach and n == 0:
                self.request("POST", f"/api/jobs/{job['id']}/attach-record/{item['id']}", {"record_id": rec_id}, cookie=cookie)
            else:
                self.request("POST", f"/api/jobs/{job['id']}/resolve/{item['id']}", {"reason": "on file"}, cookie=cookie)

    def test_pay_app_is_emailed_with_packet_files(self):
        cookie, job = self.company("mail1@pine.example")
        self.ready_to_send(cookie, job, attach=True)
        res, state = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        entry = state["email_log"][-1]
        self.assertEqual(entry["status"], "sent", entry)
        self.assertTrue(entry["delivered_at"])
        self.assertEqual(len(self.sink.messages), 1)
        msg = parsed(self.sink.messages[0])
        self.assertEqual(self.sink.messages[0]["to"], ["ap@park.example"])
        self.assertEqual(msg["To"], "ap@park.example")
        self.assertEqual(msg["Reply-To"], "mail1@pine.example")
        self.assertIn("Pine & Sons via Billdable", str(msg["From"]))
        self.assertIn("billing@billdable.test", str(msg["From"]))
        self.assertIn("Pay application PA-", msg["Subject"])
        self.assertIn("Amount due this application", msg.get_body(("plain",)).get_content())
        self.assertIn("1 document is attached", msg.get_body(("plain",)).get_content())
        self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ["contract.pdf"])

    def test_not_set_up_then_retry(self):
        for key in MAIL_ENV:
            os.environ.pop(key, None)
        cookie, job = self.company("mail2@pine.example")
        self.ready_to_send(cookie, job)
        _, state = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie=cookie)
        entry = state["email_log"][-1]
        self.assertEqual(entry["status"], mailer.NOT_SET_UP)
        self.assertEqual(self.sink.messages, [])
        _, settings = self.request("GET", "/api/import/settings", cookie=cookie)
        self.assertFalse(settings["mail_configured"])
        self.mail_on()
        res, state = self.request("POST", f"/api/outbox/{entry['id']}/retry", {}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        self.assertEqual(state["email_log"][-1]["status"], "sent")
        self.assertEqual(state["email_log"][-1]["retried"], 1)
        self.assertEqual(len(self.sink.messages), 1)
        res, _ = self.request("POST", f"/api/outbox/{entry['id']}/retry", {}, cookie=cookie)
        self.assertEqual(res.status, 409)  # already sent
        other, _ = self.company("mail2b@pine.example")
        res, _ = self.request("POST", f"/api/outbox/{entry['id']}/retry", {}, cookie=other)
        self.assertEqual(res.status, 404)  # another company's email

    def test_refused_address_fails_and_can_be_retried(self):
        self.sink.reject.add("bounce@park.example")
        cookie, job = self.company("mail3@pine.example", ap="bounce@park.example")
        self.ready_to_send(cookie, job)
        _, state = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie=cookie)
        entry = state["email_log"][-1]
        self.assertTrue(entry["status"].startswith("failed: recipient refused"), entry["status"])
        self.assertNotIn("attached", entry["body"])  # nothing was attached, so the email doesn't say so
        self.assertEqual(state["jobs"][0]["invoices"][-1]["status"], "sent")  # the approval stands
        self.sink.reject.clear()
        _, state = self.request("POST", f"/api/outbox/{entry['id']}/retry", {}, cookie=cookie)
        self.assertEqual(state["email_log"][-1]["status"], "sent")

    def test_demo_account_never_sends(self):
        res, _ = self.request("POST", "/api/auth/login", {"email": auth.DEMO_EMAIL, "password": auth.DEMO_PASSWORD})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        job = {"name": "Demo mail check", "customer": "Park GC", "address": "1 Demo St", "approver": "Kim",
               "ap_email": "ap@park.example", "contract_cents": 1_000_000, "retainage_pct": 0, "role": "Owner"}
        _, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        job_id = next(j["id"] for j in state["jobs"] if j["name"] == "Demo mail check")
        self.request("POST", f"/api/jobs/{job_id}/progress", {"completed_cents": 500_000}, cookie=cookie)
        res, state = self.request("POST", f"/api/jobs/{job_id}/pencil", {}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        self.assertEqual(state["email_log"][-1]["status"], mailer.DEMO)
        self.assertEqual(self.sink.messages, [])
        res, state = self.request("POST", f"/api/outbox/{state['email_log'][-1]['id']}/retry", {}, cookie=cookie)
        self.assertEqual(state["email_log"][-1]["status"], mailer.DEMO)
        self.assertEqual(self.sink.messages, [])
        self.request("POST", "/api/reset", {}, cookie=cookie)

    def test_daily_limit(self):
        self.mail_on(MAIL_DAILY_LIMIT="1")
        cookie, job = self.company("mail4@pine.example")
        self.ready_to_send(cookie, job)
        _, state = self.request("POST", f"/api/jobs/{job['id']}/pencil", {}, cookie=cookie)
        self.assertEqual(state["email_log"][-1]["status"], "sent")
        self.request("POST", f"/api/jobs/{job['id']}/pencil-response", {"agreed": True}, cookie=cookie)
        _, state = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie=cookie)
        self.assertIn("daily limit of 1", state["email_log"][-1]["status"])
        self.assertEqual(len(self.sink.messages), 1)

    def test_slow_mail_server_does_not_block_other_requests(self):
        self.sink.delay = 2.0
        cookie, job = self.company("mail5@pine.example")
        self.ready_to_send(cookie, job)
        other, _ = self.company("mail5b@pine.example")
        result = {}
        sender = threading.Thread(target=lambda: result.update(r=self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie=cookie)))
        sender.start()
        time.sleep(0.4)
        start = time.monotonic()
        res, _ = self.request("GET", "/api/state", cookie=other)
        elapsed = time.monotonic() - start
        sender.join(10)
        self.assertEqual(res.status, 200)
        self.assertLess(elapsed, 1.0, f"state read waited {elapsed:.2f}s behind a slow send")
        self.assertEqual(result["r"][1]["email_log"][-1]["status"], "sent")
        # While it was sending, the entry showed "sending", so a second request could not send it again.
        self.assertEqual(len(self.sink.messages), 1)

    def test_sov_over_http(self):
        cookie, job = self.company("mail6@pine.example")
        res, parsed_rows = self.request("POST", "/api/sov/parse", {"text": "Rough-in wiring\t400\tea\t45\nPanel and feeders\t12000"}, cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(len(parsed_rows["lines"]), 2)
        res, body = self.request("POST", f"/api/jobs/{job['id']}/sov", {"lines": parsed_rows["lines"], "op_pct": "10"}, cookie=cookie)
        self.assertEqual(res.status, 409)  # $33,000 vs $34,500
        res, state = self.request("POST", f"/api/jobs/{job['id']}/sov", {"lines": parsed_rows["lines"], "op_pct": "15"}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        line_id = state["jobs"][0]["sov_view"]["lines"][0]["id"]
        res, state = self.request("POST", f"/api/jobs/{job['id']}/sov-progress", {"done": {line_id: "100"}}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        self.assertEqual(state["jobs"][0]["invoices"][-1]["gross_cents"], 517_500)
        res, _ = self.request("POST", f"/api/jobs/{job['id']}/sov-progress", {"done": {line_id: {"x": 1}}}, cookie=cookie)
        self.assertEqual(res.status, 409)
        res, _ = self.request("POST", f"/api/jobs/{job['id']}/sov", {"lines": "nope"}, cookie=cookie)
        self.assertEqual(res.status, 409)


if __name__ == "__main__":
    unittest.main()
