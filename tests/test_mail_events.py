"""Bounce and complaint webhooks (P1-4)."""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import mail_events
import server
import storage
from seed import seed_state

TOKEN = "t" * 24


class NormalizeTests(unittest.TestCase):
    def test_postmark_sendgrid_and_plain(self):
        self.assertEqual(mail_events.normalize({"RecordType": "Bounce", "Email": "AP@Payer.example", "Description": "Mailbox does not exist"}),
                         [{"email": "ap@payer.example", "reason": "Mailbox does not exist"}])
        self.assertEqual(mail_events.normalize({"RecordType": "SpamComplaint", "Email": "ap@payer.example"})[0]["reason"], "marked as spam")
        self.assertEqual(mail_events.normalize({"RecordType": "Delivery", "Email": "ap@payer.example"}), [])
        events = [{"event": "delivered", "email": "a@x.example"}, {"event": "bounce", "email": "b@x.example", "reason": "550 no such user"},
                  {"event": "dropped", "email": "c@x.example", "reason": "Bounced Address"}]
        self.assertEqual([e["email"] for e in mail_events.normalize(events)], ["b@x.example", "c@x.example"])
        self.assertEqual(mail_events.normalize({"email": "d@x.example"}), [{"email": "d@x.example", "reason": "bounced"}])
        self.assertEqual(mail_events.normalize(["junk", {"email": "not-an-address"}, None]), [])

    def test_apply_marks_the_latest_sent_email_and_the_job(self):
        state = seed_state()
        for e in state["email_log"]:
            e["status"] = "sent"
        entry = mail_events.apply(state, "payables@ponceproperty.example", "Mailbox full", "2026-10-13T08:00:00+00:00")
        self.assertIn("PA-BRK-01", entry["subject"], "the most recent email to that address")
        self.assertEqual(entry["status"], "bounced: Mailbox full")
        job = next(j for j in state["jobs"] if j["id"] == entry["job_id"])
        self.assertIn("bounced", job["external_status"])
        self.assertEqual(state["audit"][-1]["action"], "email_bounced")
        self.assertIsNone(mail_events.apply(state, "nobody@else.example", "x", "now"))


class MailEventsHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = root / "auth.json", root / "companies", root
        state = seed_state()
        for e in state["email_log"]:
            e["status"] = "sent"
        server.save_company("co-test", state)
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = cls._orig
        cls.tmp.cleanup()

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request("POST", path, body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        data = json.loads(res.read() or b"{}")
        conn.close()
        return res, data

    def test_token_is_required(self):
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": ""}):
            self.assertEqual(self.post(f"/api/mail-events/{TOKEN}", {"email": "x@y.example"})[0].status, 404)
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": TOKEN}):
            self.assertEqual(self.post("/api/mail-events/wrong-token-wrong-token", {"email": "x@y.example"})[0].status, 404)

    def test_bounce_marks_the_outbox_row(self):
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": TOKEN}):
            res, data = self.post(f"/api/mail-events/{TOKEN}", {"RecordType": "Bounce", "Email": "dpatel@northlinegc.example",
                                                                 "Description": "The server was unable to deliver your message"})
        self.assertEqual((res.status, data["marked"]), (200, 1))
        state = server.load_company("co-test")
        row = next(e for e in state["email_log"] if e["to"] == "dpatel@northlinegc.example")
        self.assertTrue(row["status"].startswith("bounced: The server was unable"))
        self.assertEqual(server.redact(f"/api/mail-events/{TOKEN}"), "/api/mail-events/[token]")


if __name__ == "__main__":
    unittest.main()
