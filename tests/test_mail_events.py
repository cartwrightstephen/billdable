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


def sent(entry_id, to, delivered_at, message_id="", job_id=""):
    return {"id": entry_id, "at": delivered_at, "delivered_at": delivered_at, "job_id": job_id, "kind": "pay_app", "to": to,
            "subject": f"Pay application {entry_id}", "status": "sent", **({"message_id": message_id} if message_id else {})}


class WhichEmailTests(unittest.TestCase):
    def test_message_id_and_time_are_read(self):
        [f] = mail_events.normalize([{"event": "bounce", "email": "ap@gc.example", "reason": "550", "smtp-id": "<abc.1@duracash.test>",
                                      "timestamp": 1760000000}])
        self.assertEqual((f["message_id"], f["at"]), ("abc.1@duracash.test", "2025-10-09T08:53:20+00:00"))
        [f] = mail_events.normalize({"RecordType": "Bounce", "Email": "ap@gc.example", "BouncedAt": "2026-10-13T08:00:00.1234567Z",
                                     "MessageID": "883953f4-6105-42a2-a16a-77a8eac79483"})
        self.assertEqual((f["message_id"], f["at"]), ("883953f4-6105-42a2-a16a-77a8eac79483", "2026-10-13T08:00:00+00:00"))
        [f] = mail_events.normalize({"email": "ap@gc.example", "at": "2026-10-13T04:00:00-04:00", "message_id": "x@y"})
        self.assertEqual(f["at"], "2026-10-13T08:00:00+00:00")
        self.assertEqual(mail_events.normalize({"email": "ap@gc.example", "timestamp": "not a time"}),
                         [{"email": "ap@gc.example", "reason": "bounced"}])

    def test_the_message_id_names_the_email(self):
        state = {"email_log": [sent("eml-1", "ap@gc.example", "2026-10-01T12:00:00+00:00", "<one@duracash.test>"),
                               sent("eml-2", "ap@gc.example", "2026-10-08T12:00:00+00:00", "<two@duracash.test>")], "jobs": [], "audit": []}
        [f] = mail_events.normalize({"event": "bounce", "email": "ap@gc.example", "smtp-id": "<one@duracash.test>"})
        self.assertEqual(mail_events.find(state, f)[1]["id"], "eml-1")

    def test_an_old_bounce_marks_the_email_sent_before_it_and_a_replay_marks_nothing(self):
        state = {"email_log": [sent("eml-1", "ap@gc.example", "2026-10-01T12:00:00+00:00"),
                               sent("eml-2", "ap@gc.example", "2026-10-08T12:00:00+00:00")], "jobs": [], "audit": []}
        [f] = mail_events.normalize({"email": "ap@gc.example", "reason": "Mailbox full", "at": "2026-10-01T12:05:00+00:00"})
        _, entry = mail_events.find(state, f)
        self.assertEqual(entry["id"], "eml-1")
        self.assertTrue(mail_events.apply(state, f["email"], f["reason"], "now", entry_id=entry["id"]))
        self.assertEqual([e["status"] for e in state["email_log"]], ["bounced: Mailbox full", "sent"])
        # The provider sends the same bounce again: eml-1 is already marked, and eml-2 went out after it.
        _, entry = mail_events.find(state, f)
        self.assertIsNone(mail_events.apply(state, f["email"], f["reason"], "now", entry_id=entry["id"]))
        self.assertEqual(state["email_log"][1]["status"], "sent")


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


    def test_one_companys_bounce_does_not_mark_another_companys_email(self):
        for company_id, entry_id, when, mid in (("co-a", "eml-a", "2026-10-01T12:00:00+00:00", "<a1@duracash.test>"),
                                                ("co-b", "eml-b", "2026-10-02T12:00:00+00:00", "<b1@duracash.test>")):
            state = seed_state()
            state["email_log"] = [sent(entry_id, "ap@biggc.example", when, mid)]
            server.save_company(company_id, state)
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": TOKEN}):
            # The message id names Company A's email.
            res, data = self.post(f"/api/mail-events/{TOKEN}", [{"event": "spamreport", "email": "ap@biggc.example", "smtp-id": "<a1@duracash.test>"}])
            self.assertEqual((res.status, data["marked"]), (200, 1))
            self.assertEqual(server.load_company("co-a")["email_log"][0]["status"], "bounced: marked as spam")
            self.assertEqual(server.load_company("co-b")["email_log"][0]["status"], "sent")
            self.assertEqual(server.load_company("co-b")["audit"], seed_state()["audit"])
            # No message id: only the one email sent last before the bounce is marked, not one in each company.
            server.save_company("co-a", {**server.load_company("co-a"), "email_log": [sent("eml-a", "ap@biggc.example", "2026-10-01T12:00:00+00:00")]})
            res, data = self.post(f"/api/mail-events/{TOKEN}", {"email": "ap@biggc.example", "reason": "Mailbox full", "at": "2026-10-01T13:00:00Z"})
            self.assertEqual((res.status, data["marked"]), (200, 1))
            self.assertEqual(server.load_company("co-a")["email_log"][0]["status"], "bounced: Mailbox full")
            self.assertEqual(server.load_company("co-b")["email_log"][0]["status"], "sent")

    def test_deeply_nested_json_is_a_bad_request(self):
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": TOKEN}):
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
            conn.request("POST", f"/api/mail-events/{TOKEN}", body=b"[" * 100_000, headers={"Content-Type": "application/json"})
            res = conn.getresponse()
            res.read()
            conn.close()
        self.assertEqual(res.status, 400)

if __name__ == "__main__":
    unittest.main()
