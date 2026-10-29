"""The payer's page for a pay app (P2-4)."""

import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import domain
import importer
import server
import storage

PASSWORD = "correct horse battery"
SITE = "https://app.duracash.example"


class PayerPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = root / "auth.json", root / "companies", root, root / "uploads"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.env = mock.patch.dict(os.environ, {"DURACASH_URL": SITE})
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()

    def request(self, method, path, body=None, cookie=None, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        headers, data = {}, None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if form is not None:
            data, headers["Content-Type"] = urlencode(form).encode(), "application/x-www-form-urlencoded"
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res, raw

    def sent_pay_app(self, email):
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": PASSWORD,
                                                           "name": "Pat", "company_name": "Pine <Builders>"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, raw = self.request("POST", "/api/jobs", {"name": "Elm <b>addition</b>", "customer": "Park GC", "address": "9 Elm",
                                                      "approver": "Kim", "ap_email": "ap@park.example", "contract_cents": 1_000_000,
                                                      "retainage_pct": 0, "note": "Invoice only"}, cookie)
        job = json.loads(raw)["jobs"][-1]
        self.request("POST", f"/api/jobs/{job['id']}/progress", {"completed_cents": 400_000}, cookie)
        res, raw = self.request("GET", "/api/state", cookie=cookie)
        state = json.loads(raw)
        for item in state["jobs"][-1]["invoices"][-1].get("packet") or []:
            if not item["present"]:
                self.request("POST", f"/api/jobs/{job['id']}/resolve/{item['id']}", {"reason": "Attached to the email"}, cookie)
        res, raw = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie)
        self.assertEqual(res.status, 200, raw)
        state = json.loads(raw)
        body = state["email_log"][-1]["body"]
        link = re.search(re.escape(SITE) + r"(/p/\S+)", body).group(1)
        return cookie, state, link

    def test_page_secrets_stay_on_the_server(self):  # R6-05
        cookie, state, link = self.sent_pay_app("payer-secret@example.com")
        secret = link.rsplit(".", 1)[1]
        res, raw = self.request("GET", "/api/state", cookie=cookie)
        self.assertNotIn("payer_token", raw.decode())
        # The Outbox keeps the email as it went out, link and all; the jobs carry no secret.
        self.assertNotIn(secret, json.dumps(json.loads(raw)["jobs"]))
        job = json.loads(raw)["jobs"][-1]
        inv = job["invoices"][-1]
        self.assertTrue(inv["customer_page"])
        res, raw = self.request("GET", f"/api/jobs/{job['id']}", cookie=cookie)
        self.assertNotIn(secret, raw.decode())
        res, raw = self.request("GET", "/api/export", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertNotIn(secret, raw.decode(), "nor the file that leaves the app, emails included")
        self.assertIn("/p/[link]", raw.decode())
        # Signed in, "open it" goes through the server to the page.
        res, _ = self.request("GET", f"/api/jobs/{job['id']}/payapp/{inv['id']}/page", cookie=cookie)
        self.assertEqual((res.status, res.getheader("Location")), (303, link))
        self.assertEqual(self.request("GET", f"/api/jobs/{job['id']}/payapp/{inv['id']}/page")[0].status, 401)
        self.assertEqual(self.request("GET", f"/api/jobs/{job['id']}/payapp/inv-nope/page", cookie=cookie)[0].status, 404)

    def test_link_in_email_opens_an_escaped_page(self):
        cookie, state, link = self.sent_pay_app("payer1@example.com")
        res, raw = self.request("GET", link)
        page = raw.decode()
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader("Cache-Control"), "no-store")
        self.assertIn("Mark as received", page)
        self.assertIn("Pine &lt;Builders&gt;", page)
        self.assertNotIn("<b>addition</b>", page, "job names are escaped")
        res, raw = self.request("GET", link + "/pdf")
        self.assertEqual((res.status, raw[:4]), (200, b"%PDF"))
        self.assertEqual(self.request("GET", link + "/file/not-in-this-packet")[0].status, 404)
        self.assertEqual(self.request("GET", link[:-3] + "xyz")[0].status, 404, "a wrong token finds nothing")
        self.assertEqual(server.redact(link), re.sub(r"\.[^.]+$", ".[token]", link))

    def test_mark_received_records_once_and_shows_on_the_job(self):
        cookie, state, link = self.sent_pay_app("payer2@example.com")
        res, _ = self.request("POST", link + "/received", form={"name": "Kim <script>"})
        self.assertEqual((res.status, res.getheader("Location")), (303, link))
        res, raw = self.request("GET", link)
        self.assertIn("Received</strong> by Kim &lt;script&gt;", raw.decode())
        res, raw = self.request("GET", "/api/state", cookie=cookie)
        st = json.loads(raw)
        inv = st["jobs"][-1]["invoices"][-1]
        self.assertEqual(inv["received_by"], "Kim <script>")
        self.assertIn("confirmed receipt", st["jobs"][-1]["status_label"])
        first = inv["received_at"]
        self.request("POST", link + "/received", form={"name": "Someone else"})
        res, raw = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(json.loads(raw)["jobs"][-1]["invoices"][-1]["received_at"], first, "only the first receipt counts")

    def test_approve_sets_status_and_date_and_holds_the_follow_up(self):  # R4-08
        cookie, state, link = self.sent_pay_app("payer3@example.com")
        res, raw = self.request("GET", link)
        page = raw.decode()
        for words in ("Approve for payment", "This job", "Contract to date", "Balance to finish", "Send a question"):
            self.assertIn(words, page)
        # A refused approval shows the page again with why, and changes nothing.
        res, raw = self.request("POST", link + "/approve", form={"name": "", "expected_on": "2020-01-01"})
        self.assertEqual(res.status, 400)
        self.assertIn("Enter your name.", raw.decode())
        self.assertIn("Enter today or a later date.", raw.decode())
        day = (datetime.now(timezone.utc).date() + timedelta(days=20)).isoformat()
        res, _ = self.request("POST", link + "/approve", form={"name": "Kim Park", "expected_on": day})
        self.assertEqual((res.status, res.getheader("Location")), (303, link))
        res, raw = self.request("GET", link)
        self.assertIn("Approved for payment</strong> by Kim Park", raw.decode())
        self.assertNotIn('action="' + link + '/approve"', raw.decode(), "approved once")
        _, raw = self.request("GET", "/api/state", cookie=cookie)
        st = json.loads(raw)
        job, inv = st["jobs"][-1], st["jobs"][-1]["invoices"][-1]
        self.assertEqual((inv["status"], inv["approved_by_payer"], inv["expected_payment_on"]), ("approved", "Kim Park", day))
        self.assertTrue(inv["approved_at"])
        self.assertTrue(job["status_label"].startswith("Approved by Kim Park, expect payment"))
        # No follow-up is due until the day after the date they gave.
        due = domain.follow_up_due(job, job.get("payer") or {"follow_up_days": 7})
        self.assertEqual(due.date().isoformat(), (date.fromisoformat(day) + timedelta(days=1)).isoformat())
        res, raw = self.request("POST", f"/api/jobs/{job['id']}/follow-up", {}, cookie)
        self.assertEqual(res.status, 409, raw)
        self.assertIn(f"Kim Park approved it for payment by {day}", json.loads(raw)["error"])   # says what holds it
        res, _ = self.request("POST", link + "/approve", form={"name": "Someone", "expected_on": day})
        self.assertEqual(res.status, 400, "a second approval is refused")

    def test_question_lands_in_the_inbox_on_the_job(self):  # R4-08
        cookie, state, link = self.sent_pay_app("payer4@example.com")
        res, raw = self.request("POST", link + "/question", form={"question": "", "email": "nope"})
        self.assertEqual(res.status, 400)
        self.assertIn("Enter your question.", raw.decode())
        res, _ = self.request("POST", link + "/question", form={"name": "Kim", "email": "kim@park.example",
                                                                 "question": "Line 3 looks doubled. Can you check?"})
        self.assertEqual((res.status, res.getheader("Location")), (303, link + "?asked=1"))
        res, raw = self.request("GET", link + "?asked=1")
        self.assertIn("Question sent.", raw.decode())
        _, raw = self.request("GET", "/api/state", cookie=cookie)
        st = json.loads(raw)
        item = st["inbox"][-1]
        self.assertEqual((item["source"], item["suggested_job_id"], item["from"]), ("payer_page", st["jobs"][-1]["id"], "Kim <kim@park.example>"))
        self.assertIn("Line 3 looks doubled", item["text"])
        self.assertIn("Question on PA-", item["subject"])

    def test_no_link_without_a_site_address(self):
        with mock.patch.dict(os.environ, {"DURACASH_URL": ""}):
            res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "nolink@example.com", "password": PASSWORD,
                                                               "name": "Pat", "company_name": "Pine"})
            cookie = res.getheader("Set-Cookie").split(";")[0]
            res, raw = self.request("POST", "/api/jobs", {"name": "Oak", "customer": "Park GC", "address": "1 Oak", "approver": "Kim",
                                                          "ap_email": "ap@park.example", "contract_cents": 500_000}, cookie)
            job = json.loads(raw)["jobs"][-1]
            self.request("POST", f"/api/jobs/{job['id']}/progress", {"completed_cents": 100_000}, cookie)
            res, raw = self.request("GET", "/api/state", cookie=cookie)
            for item in json.loads(raw)["jobs"][-1]["invoices"][-1].get("packet") or []:
                self.request("POST", f"/api/jobs/{job['id']}/resolve/{item['id']}", {"reason": "ok"}, cookie)
            res, raw = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie)
            self.assertNotIn("/p/", json.loads(raw)["email_log"][-1]["body"])


if __name__ == "__main__":
    unittest.main()
