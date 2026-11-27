"""Delivery status per recipient, Resend to this recipient, Revise with a snapshot, and the
predicted pay date (Siteline comparison, items 5 and 6)."""

import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import delivery
import domain
import helper
import importer
import mail_events
import mailer
import server
import storage
import test_mail   # its SMTP sink and HTTP helpers; imported as a module so its tests don't run twice here
from dollar_jobs import dollar_jobs
from hartline_demo import hartline_demo
from review_help import reviewed

CFG = {"host": "127.0.0.1", "port": 0, "user": "", "password": "", "security": "none", "from": "billing@duracash.test", "daily_limit": 50}
TOKEN = "t" * 24


def entry(**extra):
    return {"id": "eml-1", "at": "2026-11-02T12:00:00+00:00", "delivered_at": "2026-11-02T12:00:05+00:00", "job_id": "", "kind": "pay_app",
            "to": "ap@gc.example", "cc": ["pm@gc.example"], "subject": "Pay application PA-1", "status": "sent",
            "message_id": "<one@duracash.test>", **extra}


class EventTests(unittest.TestCase):
    def test_provider_delivery_and_open_events_are_read(self):
        # Postmark's Delivery and Open webhooks (postmarkapp.com/developer/webhooks/...): our Message-ID in Metadata.
        [d] = mail_events.normalize_status({"RecordType": "Delivery", "Recipient": "AP@gc.example", "DeliveredAt": "2026-11-05T16:33:54.9070259Z",
                                            "MessageID": "883953f4", "Metadata": {"duracash-message-id": "one@duracash.test"}})
        self.assertEqual(d, {"status": "delivered", "email": "ap@gc.example", "message_id": "one@duracash.test", "subject": "",
                             "at": "2026-11-05T16:33:54+00:00"})
        [o] = mail_events.normalize_status({"RecordType": "Open", "Recipient": "ap@gc.example", "ReceivedAt": "2026-11-06T10:00:00Z", "FirstOpen": True})
        self.assertEqual((o["status"], o["at"]), ("opened", "2026-11-06T10:00:00+00:00"))
        # SendGrid's event webhook: delivered carries smtp-id; open only our unique_args.
        events = mail_events.normalize_status([
            {"event": "delivered", "email": "ap@gc.example", "timestamp": 1762360434, "smtp-id": "<one@duracash.test>"},
            {"event": "open", "email": "pm@gc.example", "timestamp": 1762360500, "duracash_message_id": "one@duracash.test"},
            {"event": "processed", "email": "ap@gc.example"}, {"event": "bounce", "email": "x@gc.example"}])
        self.assertEqual([(e["status"], e["email"], e["message_id"]) for e in events],
                         [("delivered", "ap@gc.example", "one@duracash.test"), ("opened", "pm@gc.example", "one@duracash.test")])
        self.assertEqual(mail_events.normalize_status({"email": "ap@gc.example", "status": "delivered"})[0]["status"], "delivered")
        # None of these is a bounce, and a bounce isn't a delivery.
        self.assertEqual(mail_events.normalize([{"RecordType": "Open", "Recipient": "ap@gc.example"}, {"email": "ap@gc.example", "status": "opened"}]), [])
        self.assertEqual(mail_events.normalize_status({"RecordType": "Bounce", "Email": "ap@gc.example"}), [])
        # A Postmark bounce names our email by its Metadata now.
        [b] = mail_events.normalize({"RecordType": "Bounce", "Email": "ap@gc.example", "Metadata": {"duracash-message-id": "one@duracash.test"}})
        self.assertEqual(b["message_id"], "one@duracash.test")

    def test_status_only_moves_forward_and_a_bounce_stays(self):
        e = entry()
        delivery.start(e, "2026-11-02T12:00:05+00:00", ["pm@gc.example (550 no such user)"])
        self.assertEqual([(r["email"], r["role"], r["status"]) for r in e["recipients"]],
                         [("ap@gc.example", "to", "sent"), ("pm@gc.example", "cc", "failed")])
        self.assertTrue(delivery.mark(e, "AP@gc.example", "opened", "2026-11-03T09:00:00+00:00"))
        self.assertFalse(delivery.mark(e, "ap@gc.example", "opened", "2026-11-03T10:00:00+00:00"), "an event sent twice")
        self.assertFalse(delivery.mark(e, "ap@gc.example", "delivered", "2026-11-02T12:00:09+00:00"), "read means it arrived")
        row = delivery.row_for(e, "ap@gc.example")
        self.assertEqual((row["status"], row["opened_at"]), ("opened", "2026-11-03T09:00:00+00:00"), "delivered doesn't undo opened")
        self.assertFalse(delivery.mark(e, "pm@gc.example", "delivered", "x"), "a failed copy stays failed")
        self.assertEqual(delivery.row_for(e, "pm@gc.example")["status"], "failed")
        self.assertFalse(delivery.mark(e, "stranger@x.example", "opened", "x"))

    def test_events_find_the_email_through_its_copy_address(self):
        state = {"email_log": [entry()], "jobs": [], "audit": []}
        self.assertEqual(mail_events.record_status(state, "pm@gc.example", "delivered", "2026-11-02T12:01:00+00:00")["id"], "eml-1")
        self.assertEqual(delivery.row_for(state["email_log"][0], "pm@gc.example")["status"], "delivered",
                         "an email from before per-address lines gets its line when the event comes")
        # A copy address bouncing marks only its line; the To address got it.
        hit = mail_events.apply(state, "pm@gc.example", "Mailbox full", "2026-11-02T12:02:00+00:00", "one@duracash.test")
        self.assertEqual(hit["status"], "sent")
        self.assertEqual(delivery.row_for(hit, "pm@gc.example")["status"], "bounced")
        self.assertEqual(state["audit"][-1]["action"], "email_bounced")
        self.assertIsNone(mail_events.apply(state, "pm@gc.example", "Mailbox full", "later", "one@duracash.test"), "sent again: nothing")
        # The To address bouncing marks the email, as before, and its line.
        hit = mail_events.apply(state, "ap@gc.example", "No such user", "2026-11-02T12:03:00+00:00")
        self.assertEqual((hit["status"], delivery.row_for(hit, "ap@gc.example")["status"]), ("bounced: No such user", "bounced"))

    def test_tokens_never_go_to_the_page(self):
        e = entry()
        delivery.start(e, "now", tokens={"ap@gc.example": "secret-token-secret-token"})
        state = {"email_log": [e], "jobs": [], "audit": []}
        self.assertEqual(delivery.find_token(state, "secret-token-secret-token")[1]["email"], "ap@gc.example")
        self.assertEqual(delivery.find_token(state, "other"), (None, None))
        rows = domain.mark_moot(state, [dict(e, recipients=[dict(r) for r in e["recipients"]])])
        self.assertNotIn("token", str(rows))
        self.assertIn("token", str(state["email_log"]), "kept on the server")


class MessageTests(unittest.TestCase):
    def test_tracking_image_and_one_address_per_copy(self):
        e = {"to": "ap@gc.example", "cc": ["pm@gc.example"], "subject": "s", "body": "Hello <there>\n\nAmount due"}
        msg, _ = mailer.build_message(e, "Pine", "", [("a.pdf", b"%PDF-1.4 x")], CFG, only_to="pm@gc.example",
                                      pixel_url="https://app.example/o/co-1.tok.gif")
        self.assertEqual(msg.recipients, ["pm@gc.example"])
        self.assertEqual((msg["To"], msg["Cc"]), ("ap@gc.example", "pm@gc.example"), "the headers still say who else got it")
        html = msg.get_body(("html",)).get_content()
        self.assertIn('<img src="https://app.example/o/co-1.tok.gif" width="1" height="1"', html)
        self.assertIn("Hello &lt;there&gt;", html)
        self.assertEqual(msg.get_body(("plain",)).get_content().strip(), "Hello <there>\n\nAmount due")
        self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ["a.pdf"])
        plain, _ = mailer.build_message(e, "Pine", "", [], CFG)
        self.assertIsNone(plain.get_body(("html",)), "no tracking: plain text only, as before")
        self.assertEqual(plain.recipients, ["ap@gc.example", "pm@gc.example"])

    def test_provider_headers_carry_our_message_id(self):
        e = {"to": "ap@gc.example", "subject": "s", "body": "b"}
        pm, _ = mailer.build_message(e, "Pine", "", [], dict(CFG, host="smtp.postmarkapp.com"))
        self.assertEqual(pm["X-PM-Metadata-duracash-message-id"], pm["Message-ID"].strip("<>"))
        sg, _ = mailer.build_message(e, "Pine", "", [], dict(CFG, host="smtp.sendgrid.net"))
        self.assertIn(sg["Message-ID"].strip("<>"), sg["X-SMTPAPI"])
        other, _ = mailer.build_message(e, "Pine", "", [], CFG)
        self.assertIsNone(other["X-SMTPAPI"])
        self.assertIsNone(other["X-PM-Metadata-duracash-message-id"])


def paid_job(payer_id, sent, paid, due_on=None, number="PA-1"):
    inv = {"id": f"inv-{number}", "number": number, "status": "paid", "sent_at": f"{sent}T12:00:00+00:00", "amount_cents": 100}
    if due_on:
        inv["due_on"] = due_on
    return {"id": f"job-{number}", "payer_id": payer_id, "invoices": [inv],
            "payments": [{"invoice_id": inv["id"], "amount_cents": 100, "matched_at": f"{paid}T15:00:00+00:00"}]}


class PayDateTests(unittest.TestCase):
    def state(self):
        jobs = [paid_job("p-gc", "2026-08-01", "2026-09-08", "2026-08-31", "A"),     # 38 days, late
                paid_job("p-gc", "2026-09-01", "2026-10-01", "2026-10-01", "B"),     # 30 days, on time
                paid_job("p-gc", "2026-10-01", "2026-11-10", None, "C")]             # 40 days, not judged
        return {"payers": [{"id": "p-gc", "name": "GC", "usual_approval_days": 21, "payment_terms_days": 30},
                           {"id": "p-new", "name": "New", "usual_approval_days": 14}], "jobs": jobs}

    def test_client_stats(self):
        st = domain.client_stats(self.state())["p-gc"]
        self.assertEqual((st["paid"], st["median_days"], st["average_days"]), (3, 38, 36.0))
        self.assertEqual((st["on_time"], st["judged"], st["on_time_pct"]), (1, 2, 50))

    def test_history_then_terms_then_the_usual_days(self):
        state = self.state()
        today = date(2026, 11, 27)
        open_inv = {"id": "inv-D", "number": "PA-D", "status": "sent", "sent_at": "2026-11-06T12:00:00+00:00", "amount_cents": 100, "due_on": "2026-12-06"}
        job = {"id": "job-D", "payer_id": "p-gc", "invoices": [open_inv], "payments": []}
        p = domain.predicted_pay(state, job, open_inv, today=today)
        self.assertEqual((p["on"], p["basis"], p["days"], p["overdue_days"]), ("2026-12-14", "history", 38, 0))
        self.assertEqual(p["text"], "Expected about Dec 14 · this client usually pays in 38 days")
        late = domain.predicted_pay(state, job, open_inv, today=date(2026, 12, 20))
        self.assertEqual(late["overdue_days"], 6)
        # A new client: the due date from its terms, then its days in days, then the playbook's usual days.
        job["payer_id"] = "p-new"
        self.assertEqual(domain.predicted_pay(state, job, open_inv, today=today)["basis"], "terms")
        del open_inv["due_on"]
        state["payers"][1]["payment_terms_days"] = 45
        p = domain.predicted_pay(state, job, open_inv, today=today)
        self.assertEqual((p["on"], p["basis"]), ("2026-12-21", "terms"))
        del state["payers"][1]["payment_terms_days"]
        p = domain.predicted_pay(state, job, open_inv, today=today)
        self.assertEqual((p["on"], p["basis"]), ("2026-11-20", "usual"))
        self.assertEqual(p["overdue_days"], 7)
        # The client said a date when approving it: that wins (domain.expected_payment).
        open_inv.update(status="approved", expected_payment_on="2026-12-01", approved_by_payer="Kim")
        p = domain.predicted_pay(state, job, open_inv, today=today)
        self.assertEqual((p["on"], p["basis"], p["text"]), ("2026-12-01", "promised", "Expected Dec 1 · Kim said so when approving it"))
        open_inv["status"] = "paid"
        self.assertIsNone(domain.predicted_pay(state, job, open_inv, today=today))


@hartline_demo
@dollar_jobs
class DeliveryHttpTests(unittest.TestCase):
    """Sending with open tracking, the image, provider events, Resend to this recipient and Revise."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH)
        auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR = root / "auth.json", root / "companies", root / "uploads", root
        helper.LOCAL_KB, helper.LOG_PATH = root / "helper_answers.json", root / "helper_log.json"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.sink = test_mail.Sink()
        threading.Thread(target=cls.sink.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.sink.shutdown()
        cls.sink.server_close()
        (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH) = cls._orig
        cls.tmp.cleanup()

    setUp = test_mail.MailHttpTests.setUp
    tearDown = test_mail.MailHttpTests.tearDown
    mail_on = test_mail.MailHttpTests.mail_on
    request = test_mail.MailHttpTests.request
    company = test_mail.MailHttpTests.company
    ready_to_send = test_mail.MailHttpTests.ready_to_send

    def get(self, path):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path)
        res = conn.getresponse()
        body = res.read()
        conn.close()
        return res, body

    def sent_pay_app(self, who, copy="pm@park.example", tracking=False, env=None):
        cookie, job = self.company(who)
        with mock.patch.dict(os.environ, env or {}):
            return self._send_it(cookie, job, copy, tracking)

    def _send_it(self, cookie, job, copy, tracking):
        if tracking:
            res, data = self.request("POST", "/api/company", {"open_tracking": True}, cookie=cookie)
            self.assertEqual(res.status, 200, data)
        self.request("POST", f"/api/payers/{job['payer_id']}/update", {"copy": copy}, cookie=cookie)
        self.ready_to_send(cookie, job)
        res, state = self.request("POST", f"/api/jobs/{job['id']}/send", reviewed(self, job["id"], cookie), cookie=cookie)
        self.assertEqual(res.status, 200, state)
        return cookie, job, state

    def test_open_tracking_gives_each_address_its_own_copy_and_image(self):
        self.sink.messages.clear()
        # Signed up first: with DURACASH_URL set, sign-up answers alike for every address and
        # signs nobody in until the email is confirmed (dev's uniform sign-up).
        cookie, job, state = self.sent_pay_app("track1@pine.example", tracking=True, env={"DURACASH_URL": "https://app.duracash.example"})
        e = state["email_log"][-1]
        self.assertEqual(e["status"], "sent")
        self.assertEqual(sorted(m["to"][0] for m in self.sink.messages if m["to"][0].endswith("park.example")),
                         ["ap@park.example", "pm@park.example"], "one copy per address")
        self.assertEqual([r["status"] for r in e["recipients"]], ["sent", "sent"])
        self.assertNotIn("token", str(state), "the image's keys stay on the server")
        company_id = auth.user_by_email("track1@pine.example")["company_id"]
        stored = next(x for x in server.load_company(company_id)["email_log"] if x["id"] == e["id"])
        pm = delivery.row_for(stored, "pm@park.example")
        html = test_mail.parsed(next(m for m in self.sink.messages if m["to"] == ["pm@park.example"])).get_body(("html",)).get_content()
        self.assertIn(f"https://app.duracash.example/o/{company_id}.{pm['token']}.gif", html)
        self.assertNotEqual(pm["token"], delivery.row_for(stored, "ap@park.example")["token"])
        res, body = self.get(f"/o/{company_id}.{pm['token']}.gif")
        self.assertEqual((res.status, res.getheader("Content-Type"), body), (200, "image/gif", delivery.PIXEL))
        stored = next(x for x in server.load_company(company_id)["email_log"] if x["id"] == e["id"])
        self.assertEqual((delivery.row_for(stored, "pm@park.example")["status"], delivery.row_for(stored, "ap@park.example")["status"]),
                         ("opened", "sent"))
        # A made-up token gets the same image and changes nothing.
        self.assertEqual(self.get(f"/o/{company_id}.{'x' * 24}.gif")[1], delivery.PIXEL)
        self.assertEqual(server.redact(f"/o/{company_id}.{pm['token']}.gif"), f"/o/{company_id}.[token].gif")
        # Turned off since: nothing more is recorded.
        self.request("POST", "/api/company", {"open_tracking": False}, cookie=cookie)
        self.get(f"/o/{company_id}.{delivery.row_for(stored, 'ap@park.example')['token']}.gif")
        stored = next(x for x in server.load_company(company_id)["email_log"] if x["id"] == e["id"])
        self.assertEqual(delivery.row_for(stored, "ap@park.example")["status"], "sent")

    def test_without_tracking_one_message_and_provider_events_mark_each_address(self):
        self.sink.messages.clear()
        cookie, job, state = self.sent_pay_app("track2@pine.example")
        e = state["email_log"][-1]
        self.assertEqual(len(self.sink.messages), 1)
        self.assertEqual(sorted(self.sink.messages[0]["to"]), ["ap@park.example", "pm@park.example"])
        self.assertIsNone(test_mail.parsed(self.sink.messages[0]).get_body(("html",)), "plain text, no image")
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": TOKEN}):
            res, data = self.request("POST", f"/api/mail-events/{TOKEN}", [
                {"event": "delivered", "email": "ap@park.example", "smtp-id": e["message_id"], "timestamp": 1893456000},
                {"event": "bounce", "email": "pm@park.example", "smtp-id": e["message_id"], "reason": "550 no such user"}])
        self.assertEqual((res.status, data["marked"]), (200, 2))
        _, state = self.request("GET", "/api/state", cookie=cookie)
        inv = next(i for i in state["jobs"][0]["invoices"] if i["status"] == "sent")
        mine = next(x for x in inv["emails"] if x["id"] == e["id"])
        self.assertEqual(mine["status"], "sent", "the To address got it")
        self.assertEqual([(r["email"], r["status"]) for r in mine["recipients"]], [("ap@park.example", "delivered"), ("pm@park.example", "bounced")])
        self.assertIn("predicted_pay", inv)
        self.assertEqual(inv["predicted_pay"]["basis"], "usual", "a new client, no terms: the playbook's usual days")
        # Resend to this recipient: a new Outbox row, to that address only.
        self.sink.messages.clear()
        res, state = self.request("POST", f"/api/outbox/{e['id']}/resend", {"email": "pm@park.example"}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        again = state["email_log"][-1]
        self.assertEqual((again["to"], again["cc"], again["status"], again["resend_of"]), ("pm@park.example", [], "sent", e["id"]))
        self.assertEqual([m["to"] for m in self.sink.messages], [["pm@park.example"]])
        self.assertEqual(next(iter(test_mail.parsed(self.sink.messages[0]).iter_attachments())).get_content()[:8], b"%PDF-1.4")
        res, data = self.request("POST", f"/api/outbox/{e['id']}/resend", {"email": "stranger@x.example"}, cookie=cookie)
        self.assertEqual(res.status, 409, data)

    def test_resend_is_for_those_who_may_send_it(self):
        cookie, job, state = self.sent_pay_app("track3@pine.example")
        e = state["email_log"][-1]
        company_id = auth.user_by_email("track3@pine.example")["company_id"]
        user = auth.user_by_email("track3@pine.example")
        with mock.patch.object(server.profiles, "has_perm", lambda u, perm, st: perm != "send_payapps"):
            import outbox
            status, data = outbox.resend_to(server.SITE, company_id, user, e["id"], "ap@park.example", "Pat")
        self.assertEqual(status, 403, data)

    def test_revise_keeps_what_went_and_sends_again_under_the_same_number(self):
        cookie, job, state = self.sent_pay_app("revise1@pine.example", copy="")
        sent = next(i for i in state["jobs"][0]["invoices"] if i["status"] == "sent")
        res, data = self.request("POST", f"/api/jobs/{job['id']}/revise/{sent['id']}", {"reason": ""}, cookie=cookie)
        self.assertEqual(res.status, 409, data)
        self.assertIn("reason", data.get("fields", {}))
        res, state = self.request("POST", f"/api/jobs/{job['id']}/revise/{sent['id']}", {"reason": "Wrong quantity on line 1"}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        inv = next(i for i in state["jobs"][0]["invoices"] if i["id"] == sent["id"])
        self.assertIn(inv["status"], domain.DRAFT_STATUSES)
        self.assertEqual(inv["number"], sent["number"])
        [rev] = inv["revisions"]
        self.assertEqual((rev["n"], rev["reason"], rev["amount_cents"], rev["sent_at"]), (1, "Wrong quantity on line 1", sent["amount_cents"], sent["sent_at"]))
        self.assertEqual(rev["figures"]["due"], sent["figures"]["due"])
        res, pdf = self.get(f"/api/files/{rev['file_id']}")
        self.assertEqual(res.status, 401, "the saved PDF is the company's, behind sign-in")
        company_id = auth.user_by_email("revise1@pine.example")["company_id"]
        stored = server.load_company(company_id)
        meta = next(f for f in stored["files"] if f["id"] == rev["file_id"])
        self.assertTrue(importer.file_path(company_id, meta).read_bytes().startswith(b"%PDF-1.4"))
        self.assertEqual(meta["name"], f"{sent['number']} as sent {sent['sent_at'][:10]}.pdf")
        self.assertEqual(stored["audit"][-1]["action"], "payapp_revised")
        self.assertFalse(inv.get("sent_at"))
        self.assertFalse(any(e.get("payapp_invoice_id") == inv["id"] and e.get("moot") is None and e["status"] == "queued" for e in state["email_log"]))
        # Refused again while it is a draft; sent again, it keeps its number and its history.
        res, data = self.request("POST", f"/api/jobs/{job['id']}/revise/{sent['id']}", {"reason": "again"}, cookie=cookie)
        self.assertEqual(res.status, 409, data)
        res, state = self.request("POST", f"/api/jobs/{job['id']}/send", reviewed(self, job["id"], cookie), cookie=cookie)
        self.assertEqual(res.status, 200, state)
        again = next(i for i in state["jobs"][0]["invoices"] if i["status"] == "sent")
        self.assertEqual((again["number"], len(again["revisions"])), (sent["number"], 1))
        self.assertEqual(state["email_log"][-1]["status"], "sent")


if __name__ == "__main__":
    unittest.main()
