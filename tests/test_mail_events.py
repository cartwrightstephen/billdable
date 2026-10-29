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
                         [{"email": "ap@payer.example", "reason": "Mailbox does not exist", "message_id": "", "subject": ""}])
        self.assertEqual(mail_events.normalize({"RecordType": "SpamComplaint", "Email": "ap@payer.example"})[0]["reason"], "marked as spam")
        self.assertEqual(mail_events.normalize({"RecordType": "Delivery", "Email": "ap@payer.example"}), [])
        events = [{"event": "delivered", "email": "a@x.example"}, {"event": "bounce", "email": "b@x.example", "reason": "550 no such user"},
                  {"event": "dropped", "email": "c@x.example", "reason": "Bounced Address"}]
        self.assertEqual([e["email"] for e in mail_events.normalize(events)], ["b@x.example", "c@x.example"])
        self.assertEqual(mail_events.normalize({"email": "d@x.example"}), [{"email": "d@x.example", "reason": "bounced", "message_id": "", "subject": ""}])
        self.assertEqual(mail_events.normalize(["junk", {"email": "not-an-address"}, None]), [])

    def test_postmark_out_of_office_and_delayed_mail_are_not_bounces(self):
        for kind in ("AutoResponder", "Transient", "AddressChange", "Unsubscribe", "Subscribe", "ChallengeVerification",
                     "SpamNotification", "VirusNotification", "OpenRelayTest"):
            self.assertEqual(mail_events.normalize({"RecordType": "Bounce", "Type": kind, "Email": "ap@payer.example",
                                                    "Description": "Out of the office until Monday"}), [], kind)
        for kind in ("HardBounce", "BadEmailAddress", "ManuallyDeactivated", "Blocked", "SoftBounce", "DnsError"):
            self.assertEqual(len(mail_events.normalize({"RecordType": "Bounce", "Type": kind, "Email": "ap@payer.example"})), 1, kind)

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
                         [{"email": "ap@gc.example", "reason": "bounced", "message_id": "", "subject": ""}])

    def test_the_message_id_names_the_email(self):
        state = {"email_log": [sent("eml-1", "ap@gc.example", "2026-10-01T12:00:00+00:00", "<one@duracash.test>"),
                               sent("eml-2", "ap@gc.example", "2026-10-08T12:00:00+00:00", "<two@duracash.test>")], "jobs": [], "audit": []}
        [f] = mail_events.normalize({"event": "bounce", "email": "ap@gc.example", "smtp-id": "<one@duracash.test>"})
        self.assertEqual(mail_events.find(state, f["email"], f["message_id"], f["subject"])["id"], "eml-1")

    def test_an_old_bounce_marks_the_email_sent_before_it_and_a_replay_marks_nothing(self):
        state = {"email_log": [sent("eml-1", "ap@gc.example", "2026-10-01T12:00:00+00:00"),
                               sent("eml-2", "ap@gc.example", "2026-10-08T12:00:00+00:00")], "jobs": [], "audit": []}
        [f] = mail_events.normalize({"email": "ap@gc.example", "reason": "Mailbox full", "at": "2026-10-01T12:05:00+00:00"})
        args = (f["email"], f["message_id"], f["subject"], f["at"])
        self.assertEqual(mail_events.find(state, *args)["id"], "eml-1")
        self.assertTrue(mail_events.apply(state, f["email"], f["reason"], "now", *args[1:]))
        self.assertEqual([e["status"] for e in state["email_log"]], ["bounced: Mailbox full", "sent"])
        # The provider sends the same bounce again: eml-1 is already marked, and eml-2 went out after it.
        self.assertEqual(mail_events.find(state, *args)["id"], "eml-1")
        self.assertIsNone(mail_events.apply(state, f["email"], f["reason"], "now", *args[1:]))
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


def two_companies():
    """Company A and company B both bill the same GC at ap@gc.example."""
    states = {}
    for name in ("a", "b"):
        state = seed_state()
        log = [e for e in state["email_log"] if e["job_id"] == "job-oak"][:1]
        older = dict(log[0], id="eml-old", subject=f"Pay application PA-{name.upper()}-01", at="2026-09-01T15:00:00+00:00",
                     delivered_at="2026-09-01T15:00:10+00:00", message_id=f"<old.{name}@duracash.test>")
        newer = dict(log[0], id="eml-new", subject=f"Pay application PA-{name.upper()}-02", at="2026-10-01T15:00:00+00:00",
                     delivered_at="2026-10-01T15:00:10+00:00", message_id=f"<new.{name}@duracash.test>")
        for e in (older, newer):
            e.update(to="ap@gc.example", status="sent")
        state["email_log"] = [older, newer]
        next(j for j in state["jobs"] if j["id"] == "job-oak")["external_status"] = f"Submitted by {name}"
        states[name] = state
    # B's newest pay app went out after A's.
    states["b"]["email_log"][1].update(at="2026-10-08T15:00:00+00:00", delivered_at="2026-10-08T15:00:10+00:00")
    return states


class OneEventOneEmailTests(unittest.TestCase):
    """A bounce marks the one email it is about: not other companies' emails, not older ones."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = root / "auth.json", root / "companies", root
        for name, state in two_companies().items():
            server.save_company(f"co-{name}", state)
        self.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = self._orig
        self.tmp.cleanup()

    post = MailEventsHttpTests.post

    def statuses(self, name):
        state = server.load_company(f"co-{name}")
        job = next(j for j in state["jobs"] if j["id"] == "job-oak")
        return [e["status"].split(":")[0] for e in state["email_log"]], job["external_status"]

    def test_postmark_subject_picks_the_company_and_the_email(self):
        event = {"RecordType": "SpamComplaint", "Email": "ap@gc.example", "Subject": "Pay application PA-A-02"}
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": TOKEN}):
            self.assertEqual(self.post(f"/api/mail-events/{TOKEN}", event)[1]["marked"], 1)
            # The provider posts the same event again: nothing more is marked.
            self.assertEqual(self.post(f"/api/mail-events/{TOKEN}", event)[1]["marked"], 0)
        self.assertEqual(self.statuses("a")[0], ["sent", "bounced"])
        self.assertIn("bounced", self.statuses("a")[1])
        self.assertEqual(self.statuses("b"), (["sent", "sent"], "Submitted by b"), "company B's email was delivered")

    def test_sendgrid_message_id_picks_the_email(self):
        events = [{"event": "bounce", "email": "ap@gc.example", "reason": "550 no such user", "smtp-id": "<old.a@duracash.test>"}]
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": TOKEN}):
            self.assertEqual(self.post(f"/api/mail-events/{TOKEN}", events)[1]["marked"], 1)
            self.assertEqual(self.post(f"/api/mail-events/{TOKEN}", events)[1]["marked"], 0)
        self.assertEqual(self.statuses("a")[0], ["bounced", "sent"])
        self.assertEqual(self.statuses("b")[0], ["sent", "sent"])

    def test_an_email_sent_before_message_ids_were_kept_is_found_by_address(self):
        state = two_companies()["a"]
        for e in state["email_log"]:
            e.pop("message_id")
        found = mail_events.find(state, "ap@gc.example", "unknown@duracash.test")
        self.assertEqual(found["id"], "eml-new")
        state["email_log"][1]["message_id"] = "<new.a@duracash.test>"
        self.assertEqual(mail_events.find(state, "ap@gc.example", "unknown@duracash.test")["id"], "eml-old",
                         "an email with its own Message-ID is never taken for another")

    def test_without_a_message_identity_only_the_newest_email_anywhere(self):
        event = {"email": "ap@gc.example", "reason": "Mailbox full"}
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": TOKEN}):
            self.assertEqual(self.post(f"/api/mail-events/{TOKEN}", event)[1]["marked"], 1)
            self.assertEqual(self.post(f"/api/mail-events/{TOKEN}", event)[1]["marked"], 0, "a repeat doesn't reach older emails")
        self.assertEqual(self.statuses("b")[0], ["sent", "bounced"])
        self.assertEqual(self.statuses("a"), (["sent", "sent"], "Submitted by a"))


if __name__ == "__main__":
    unittest.main()
