"""Importer, inbound email/SMS, files, calendar feed, pencil copy and helper over HTTP."""

import base64
import hashlib
import hmac
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import helper
import importer
import server
import storage

PASSWORD = "correct horse battery"
EML = (b"From: Kim Park <ap@park.example>\r\nTo: jobs@example.com\r\nSubject: CO 2 signed for Elm addition\r\n"
       b"Date: Mon, 12 Oct 2026 09:00:00 -0400\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=XYZ\r\n\r\n"
       b"--XYZ\r\nContent-Type: text/plain\r\n\r\nSigned change order attached. Total $2,500.00, due 10/30/2026.\r\n"
       b"--XYZ\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"co2.pdf\"\r\n\r\n%PDF-1.4 fake\r\n"
       b"--XYZ\r\nContent-Type: text/html\r\nContent-Disposition: attachment; filename=\"evil.html\"\r\n\r\n<script>x</script>\r\n--XYZ--\r\n")


class ImportHttpTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH) = cls._orig
        os.environ.pop("TWILIO_AUTH_TOKEN", None)
        os.environ.pop("HELPER_ADMIN_USER_IDS", None)
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
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
        try:
            parsed = json.loads(payload or b"{}")
        except ValueError:
            parsed = payload
        return res, parsed

    def company(self, email):
        res, data = self.request("POST", "/api/auth/signup", {"email": email, "password": PASSWORD, "name": "Pat Pine", "company_name": "Pine Co"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        job = {"name": "Elm addition", "customer": "Park GC", "address": "9 Elm Street", "approver": "Kim", "ap_email": "ap@park.example",
               "contract_cents": 10_000_000, "retainage_pct": 10, "role": "GC"}
        res, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        return cookie, state["jobs"][0]

    def test_upload_eml_matches_job_and_refuses_html(self):
        cookie, job = self.company("eml@example.com")
        res, state = self.request("POST", "/api/import/file", raw=EML, cookie=cookie,
                                  headers={"Content-Type": "message/rfc822", "X-Filename": quote("co2 email.eml")})
        self.assertEqual(res.status, 200, state)
        item = state["inbox"][-1]
        self.assertEqual(item["suggested_job_id"], job["id"])
        self.assertEqual(item["kind"], "change_order")
        self.assertEqual(item["amounts"], [250_000])
        self.assertIn("2026-10-30", item["dates"])
        names = sorted(f["name"] for f in state["files"])
        self.assertEqual(names, ["co2 email.eml", "co2.pdf"])
        self.assertTrue(any("evil.html" in s for s in item["skipped_attachments"]))
        res, state = self.request("POST", f"/api/inbox/{item['id']}/file", {"job_id": job["id"], "kind": "change_order"}, cookie=cookie)
        rec = state["jobs"][0]["records"][-1]
        self.assertEqual(len(rec["file_ids"]), 2)
        pdf = next(f for f in state["files"] if f["name"] == "co2.pdf")
        res, body = self.request("GET", f"/api/files/{pdf['id']}", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader("Content-Type"), "application/pdf")
        self.assertIsNone(res.getheader("Content-Security-Policy"))
        eml = next(f for f in state["files"] if f["name"] == "co2 email.eml")
        res, _ = self.request("GET", f"/api/files/{eml['id']}", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertIn("sandbox", res.getheader("Content-Security-Policy") or "")
        other, _ = self.company("other-eml@example.com")
        res, _ = self.request("GET", f"/api/files/{pdf['id']}", cookie=other)
        self.assertEqual(res.status, 404)
        res, state = self.request("POST", f"/api/inbox/{item['id']}/undo", {}, cookie=cookie)
        self.assertNotIn(rec["id"], [r["id"] for r in state["jobs"][0]["records"]])

    def test_refused_file_types(self):
        cookie, _ = self.company("types@example.com")
        for name in ["page.html", "logo.svg", "run.js"]:
            res, data = self.request("POST", "/api/import/file", raw=b"<x>", cookie=cookie, headers={"Content-Type": "text/html", "X-Filename": name})
            self.assertEqual(res.status, 409, name)

    def test_drop_on_job_files_immediately(self):
        cookie, job = self.company("drop@example.com")
        res, state = self.request("POST", f"/api/import/file?job={job['id']}&file=1", raw=b"\x89PNG fake", cookie=cookie,
                                  headers={"Content-Type": "image/png", "X-Filename": "IMG_0001.png"})
        self.assertEqual(res.status, 200, state)
        self.assertEqual(state["inbox"][-1]["status"], "filed")
        self.assertEqual(state["jobs"][0]["records"][-1]["kind"], "photo")

    def test_notes_split_into_daily_records(self):
        cookie, job = self.company("notes@example.com")
        notes = "10/1 Footings poured\ncrew of 4\n10/2 Framing started\nOct 3, 2026: rough-in inspection passed"
        res, state = self.request("POST", "/api/import/text?file=1", {"text": notes, "source": "notes", "job_id": job["id"]}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        days = sorted(d["at"][:10] for d in state["daily_records"])
        self.assertEqual(len(days), 3)
        self.assertTrue(all(d.endswith(("-01", "-02", "-03")) for d in days))

    def test_inbound_email_json_and_sms_with_signature(self):
        cookie, job = self.company("inbound@example.com")
        res, settings = self.request("GET", "/api/import/settings", cookie=cookie)
        email_path = "/api/inbound/email/" + settings["email_webhook"].rsplit("/", 1)[-1]
        sms_path = "/api/inbound/sms/" + settings["sms_webhook"].rsplit("/", 1)[-1]
        res, _ = self.request("POST", "/api/inbound/email/not-a-token", {"from": "x", "text": "hi"})
        self.assertEqual(res.status, 404)
        payload = {"From": "ap@park.example", "Subject": "Pay app question", "TextBody": "About 9 Elm Street",
                   "Attachments": [{"Name": "photo.jpg", "Content": base64.b64encode(b"\xff\xd8fake").decode(), "ContentType": "image/jpeg"}]}
        res, data = self.request("POST", email_path, payload)
        self.assertEqual(res.status, 200, data)
        os.environ["TWILIO_AUTH_TOKEN"] = "test-secret"
        form = {"From": "+15555550123", "Body": "Elm addition: drywall delivered", "NumMedia": "0"}
        raw = urlencode(form).encode()
        url = f"http://127.0.0.1:{self.port}{sms_path}"
        good = base64.b64encode(hmac.new(b"test-secret", (url + "".join(k + form[k] for k in sorted(form))).encode(), hashlib.sha1).digest()).decode()
        hdr = {"Content-Type": "application/x-www-form-urlencoded", "Host": f"127.0.0.1:{self.port}"}
        res, _ = self.request("POST", sms_path, raw=raw, headers={**hdr, "X-Twilio-Signature": "wrong"})
        self.assertEqual(res.status, 403)
        res, body = self.request("POST", sms_path, raw=raw, headers={**hdr, "X-Twilio-Signature": good})
        self.assertEqual(res.status, 200)
        self.assertIn(b"<Response>", body)
        os.environ.pop("TWILIO_AUTH_TOKEN")
        res, state = self.request("GET", "/api/state", cookie=cookie)
        sources = [(i["source"], i["suggested_job_id"]) for i in state["inbox"]]
        self.assertIn(("email", job["id"]), sources)
        self.assertIn(("sms", job["id"]), sources)

    def test_calendar_feed_and_pencil(self):
        cookie, job = self.company("cal@example.com")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/progress", {"completed_cents": 4_000_000}, cookie=cookie)
        self.assertTrue(state["jobs"][0]["closeout_packet"], "punch list visible from the first retainage pay app")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/pencil", {}, cookie=cookie)
        self.assertEqual(state["email_log"][-1]["kind"], "pencil")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/pencil-response", {"agreed": False, "note": "Qty on line 3 is 900 sf"}, cookie=cookie)
        self.assertEqual(state["jobs"][0]["invoices"][-1]["pencil_status"], "corrections")
        res, settings = self.request("GET", "/api/import/settings", cookie=cookie)
        token = settings["calendar_url"].rsplit("/", 1)[-1]
        res, body = self.request("GET", f"/api/calendar/{token}")
        self.assertEqual(res.status, 200)
        self.assertTrue(body.startswith(b"BEGIN:VCALENDAR"))
        res, _ = self.request("GET", "/api/calendar/wrong.ics")
        self.assertEqual(res.status, 404)

    def test_helper_endpoints_and_admin_gate(self):
        cookie, job = self.company("helper@example.com")
        res, reply = self.request("POST", "/api/helper/ask", {"q": "what should I do today?", "view": "dashboard"}, cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(reply["matched"], "today")
        res, reply = self.request("POST", "/api/helper/ask", {"q": "zzz qqq unknown thing"}, cookie=cookie)
        self.assertIsNone(reply["matched"])
        res, _ = self.request("GET", "/api/helper/review", cookie=cookie)
        self.assertEqual(res.status, 403)
        res, _ = self.request("POST", "/api/helper/answers", {"questions": ["x"], "answer": "y"}, cookie=cookie)
        self.assertEqual(res.status, 403)
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        os.environ["HELPER_ADMIN_USER_IDS"] = me["user"]["id"]
        res, review = self.request("GET", "/api/helper/review", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertTrue(any("zzz" in g["examples"][0] for g in review["open"]))
        res, data = self.request("POST", "/api/helper/answers", {"questions": "not a list", "answer": "y"}, cookie=cookie)
        self.assertEqual(res.status, 409)
        # An email that matches nothing in the setting gets nothing, whatever the address.
        other, _ = self.company("founder-lookalike@example.com")
        res, _ = self.request("GET", "/api/helper/review", cookie=other)
        self.assertEqual(res.status, 403)
        os.environ.pop("HELPER_ADMIN_USER_IDS")

    def test_review_findings(self):
        cookie, job = self.company("findings@example.com")
        # Multipart inbound email with a mixed-case boundary (SendGrid style) keeps its fields.
        res, settings = self.request("GET", "/api/import/settings", cookie=cookie)
        email_path = "/api/inbound/email/" + settings["email_webhook"].rsplit("/", 1)[-1]
        body = (b"--xYzZY\r\nContent-Disposition: form-data; name=\"from\"\r\n\r\nap@park.example\r\n"
                b"--xYzZY\r\nContent-Disposition: form-data; name=\"subject\"\r\n\r\nElm addition question\r\n"
                b"--xYzZY\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\nHello\r\n--xYzZY--\r\n")
        res, _ = self.request("POST", email_path, raw=body, headers={"Content-Type": "multipart/form-data; boundary=xYzZY"})
        self.assertEqual(res.status, 200)
        res, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(state["inbox"][-1]["subject"], "Elm addition question")
        # Malformed inbound JSON is handled, not a 500.
        for payload in [{"attachments": 5}, {"attachments": [{"name": 5, "content": "aGk="}]}]:
            res, _ = self.request("POST", email_path, payload)
            self.assertIn(res.status, (200, 409), payload)
        # Long pasted notes are accepted.
        notes = "\n".join(f"10/{d} Elm addition crew of 4, framing and blocking, " + "x" * 200 for d in range(1, 29))
        res, state = self.request("POST", "/api/import/text", {"text": notes, "source": "notes"}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        # A refused import leaves no file on disk.
        before = sorted(p.name for p in (importer.UPLOADS / job["id"].split("-")[0]).glob("*")) if False else None
        res, state = self.request("GET", "/api/state", cookie=cookie)
        company_dir = next(importer.UPLOADS.iterdir()) if importer.UPLOADS.exists() else None
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        folder = importer.UPLOADS / me["user"]["company_id"]
        count = len(list(folder.glob("*"))) if folder.exists() else 0
        res, _ = self.request("POST", "/api/import/file?job=job-nope&file=1", raw=b"%PDF-1.4", cookie=cookie,
                              headers={"Content-Type": "application/pdf", "X-Filename": "a.pdf"})
        self.assertEqual(res.status, 409)
        self.assertEqual(len(list(folder.glob("*"))) if folder.exists() else 0, count)
        # PDFs are served without the sandbox header so the browser's viewer opens them.
        res, state = self.request("POST", "/api/import/file", raw=b"%PDF-1.4 x", cookie=cookie,
                                  headers={"Content-Type": "application/pdf", "X-Filename": "ok.pdf"})
        pdf = next(f for f in state["files"] if f["name"] == "ok.pdf")
        res, _ = self.request("GET", f"/api/files/{pdf['id']}", cookie=cookie)
        self.assertIsNone(res.getheader("Content-Security-Policy"))

    def test_tokens_survive_concurrent_companies(self):
        cookies = [self.company(f"race{i}@example.com")[0] for i in range(10)]
        results = [None] * len(cookies)

        def fetch(i):
            res, data = self.request("GET", "/api/import/settings", cookie=cookies[i])
            results[i] = data["email_webhook"].rsplit("/", 1)[-1]

        threads = [threading.Thread(target=fetch, args=(i,)) for i in range(len(cookies))]
        for t in threads: t.start()
        for t in threads: t.join()
        stored = importer.load_tokens()
        self.assertTrue(all(tok in stored for tok in results))


if __name__ == "__main__":
    unittest.main()
