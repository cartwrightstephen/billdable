"""The payer's page for a pay app (P2-4)."""

import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
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
