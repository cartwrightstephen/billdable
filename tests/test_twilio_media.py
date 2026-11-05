"""Photos and PDFs texted to the Duracash number are downloaded (P3)."""

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
from unittest import mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import importer
import server
import storage
import twilio_media

GOOD = "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1"


def agree(user_id, phone):
    """A person who has answered the code texted to this number."""
    auth.confirm_sms_code(user_id, auth.issue_sms_code(user_id, phone))


class DownloadTests(unittest.TestCase):
    def test_nothing_without_signature_checking(self):
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": ""}):
            self.assertEqual(twilio_media.download([{"url": GOOD, "type": "image/jpeg"}], 100, get=lambda u, l: b"x"), ([], []))

    def test_only_photos_and_pdfs_from_twilio(self):
        media = [{"url": GOOD, "type": "image/jpeg"}, {"url": "https://evil.example/x.jpg", "type": "image/jpeg"},
                 {"url": GOOD, "type": "video/mp4"}, {"url": "http://api.twilio.com/x", "type": "application/pdf"}]
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "t"}):
            files, problems = twilio_media.download(media, 100, get=lambda u, l: b"\xff\xd8jpeg")
        self.assertEqual([f["filename"] for f in files], ["Text photo 1.jpg"])
        self.assertEqual(len(problems), 3)

    def test_a_failed_download_is_reported(self):
        def boom(url, limit):
            raise TimeoutError()
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "t"}):
            files, problems = twilio_media.download([{"url": GOOD, "type": "image/png"}], 100, get=boom)
        self.assertEqual(files, [])
        self.assertIn("TimeoutError", problems[0])

    def test_redirects_only_to_twilio_storage(self):
        handler = twilio_media._Redirects()
        with self.assertRaises(Exception):
            handler.redirect_request(None, None, 302, "Found", {}, "https://169.254.169.254/latest/meta-data")


class InboundSmsMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = root / "auth.json", root / "companies", root, root / "uploads"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, form=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        h, data = dict(headers or {}), None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        if form is not None:
            data, h["Content-Type"] = urlencode(form).encode(), "application/x-www-form-urlencoded"
        if cookie:
            h["Cookie"] = cookie
        conn.request(method, path, body=data, headers=h)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res, raw

    def test_texted_photo_is_stored_on_the_inbox_item(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "sms@example.com", "password": "correct horse battery",
                                                           "name": "Pat", "company_name": "Pine"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, raw = self.request("GET", "/api/import/settings", cookie=cookie)
        path = "/api/inbound/sms/" + json.loads(raw)["sms_webhook"].rsplit("/", 1)[-1]
        form = {"From": "+15555550123", "Body": "Oak tickets", "NumMedia": "1", "MediaUrl0": GOOD, "MediaContentType0": "image/jpeg"}
        port = self.httpd.server_address[1]
        signed = f"http://127.0.0.1:{port}{path}" + "".join(k + form[k] for k in sorted(form))
        sig = base64.b64encode(hmac.new(b"secret", signed.encode(), hashlib.sha1).digest()).decode()
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "secret"}), \
                mock.patch.object(twilio_media, "fetch", lambda url, limit: b"\xff\xd8realjpeg"):
            res, _ = self.request("POST", path, form=form, headers={"X-Twilio-Signature": sig, "Host": f"127.0.0.1:{port}"})
        self.assertEqual(res.status, 200)
        res, raw = self.request("GET", "/api/state", cookie=cookie)
        state = json.loads(raw)
        item = state["inbox"][-1]
        self.assertEqual(len(item["file_ids"]), 1)
        self.assertNotIn("not downloaded", item["text"])
        f = next(x for x in state["files"] if x["id"] == item["file_ids"][0])
        self.assertTrue(f["name"].endswith(".jpg"))

    def signup(self, email="stephen-idea@example.com"):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": "correct horse battery",
                                                           "name": "Pat", "company_name": "Pine"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, raw = self.request("GET", "/api/import/settings", cookie=cookie)
        return cookie, "/api/inbound/sms/" + json.loads(raw)["sms_webhook"].rsplit("/", 1)[-1]

    def post_signed(self, path, form, url=None, secret="secret", headers=None):
        port = self.httpd.server_address[1]
        sig = twilio_media.signature(url or f"http://127.0.0.1:{port}{path}", form, secret)
        return self.request("POST", path, form=form, headers={"X-Twilio-Signature": sig, **(headers or {})})

    def test_signature_is_twilios_own_published_example(self):
        """The vector from twilio-python's request-validator tests, not one worked out here."""
        params = {"CallSid": "CA1234567890ABCDE", "Caller": "+14158675309", "Digits": "1234", "From": "+14158675309", "To": "+18005551212"}
        self.assertEqual(twilio_media.signature("https://mycompany.com/myapp.php?foo=1&bar=2", params, "12345"), "RSOYDt4T1cUTdK1PDd93/VVr8B8=")

    def test_production_refuses_texts_without_the_auth_token(self):
        _, path = self.signup("prod-refuse@example.com")
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "", "DURACASH_ENV": "production", "DURACASH_URL": "https://app.duracash.example"}):
            res, _ = self.request("POST", path, form={"From": "+15555550123", "Body": "hello"})
        self.assertEqual(res.status, 403)

    def test_a_wrong_or_odd_signature_is_refused_not_a_crash(self):
        _, path = self.signup("odd-sig@example.com")
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "secret"}):
            res, _ = self.request("POST", path, form={"Body": "x"}, headers={"X-Twilio-Signature": "wrong"})
            self.assertEqual(res.status, 403)
            res, _ = self.request("POST", path, form={"Body": "x"}, headers={"X-Twilio-Signature": "caf\xe9".encode("latin-1").decode("latin-1")})
            self.assertEqual(res.status, 403)

    def test_signed_url_behind_renders_proxy(self):
        """Render ends TLS: the server sees http, with X-Forwarded-Proto and the public Host."""
        cookie, path = self.signup("proxy@example.com")
        form = {"From": "+15555550123", "Body": "behind the proxy"}
        proxy = {"X-Forwarded-Proto": "https", "Host": "duracash-staging.onrender.com"}
        env = {k: v for k, v in os.environ.items() if k != "DURACASH_URL"}
        with mock.patch.dict(os.environ, {**env, "TWILIO_AUTH_TOKEN": "secret"}, clear=True):
            res, _ = self.post_signed(path, form, url=f"https://duracash-staging.onrender.com{path}", headers=proxy)
            self.assertEqual(res.status, 200, "no DURACASH_URL: the forwarded address")
            res, _ = self.post_signed(path, form, url=f"http://duracash-staging.onrender.com{path}", headers=proxy)
            self.assertEqual(res.status, 403, "Twilio signs the https address it was given")
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "secret", "DURACASH_URL": "https://app.duracash.example/"}):
            res, _ = self.post_signed(path, form, url=f"https://app.duracash.example{path}", headers={"X-Forwarded-Proto": "http", "Host": "evil.example", "X-Forwarded-Host": "evil.example"})
            self.assertEqual(res.status, 200, "DURACASH_URL wins over whatever the request says")
            res, _ = self.post_signed(path, form, url=f"https://evil.example{path}", headers={"Host": "evil.example"})
            self.assertEqual(res.status, 403)

    def test_whole_path_photo_and_pdf_to_a_filed_job_record(self):
        """Stephen's idea: a text with attachments waits in the Inbox, says who sent it, and a manager files it."""
        cookie, path = self.signup("whole-path@example.com")
        res, raw = self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "4045550177",
                                                     "ap_email": "ap@northline.example", "contract_cents": 2000000}, cookie=cookie)
        self.assertEqual(res.status, 200, raw)
        job_id = json.loads(raw)["jobs"][-1]["id"]
        me = next(u for u in auth.all_users() if u["email"] == "whole-path@example.com")
        auth.set_sms(me["id"], "+15555550123", False)
        auth.update_name(me["id"], "Pat Lee")
        pdf = GOOD.replace("ME1", "ME2")
        form = {"From": "+15555550123", "Body": "", "NumMedia": "2", "MediaUrl0": GOOD, "MediaContentType0": "image/jpeg",
                "MediaUrl1": pdf, "MediaContentType1": "application/pdf", "MessageSid": "SM1"}
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "secret"}), \
                mock.patch.object(twilio_media, "fetch", lambda url, limit: b"%PDF-1.4 x" if url == pdf else b"\xff\xd8realjpeg"):
            res, raw = self.post_signed(path, form)
        self.assertEqual(res.status, 200)
        self.assertIn(b"<Response>", raw)
        state = json.loads(self.request("GET", "/api/state", cookie=cookie)[1])
        item = state["inbox"][-1]
        self.assertEqual(item["status"], "new")
        self.assertEqual(item["source"], "sms")
        self.assertIn("Pat Lee", item["from"])
        self.assertIn("+15555550123", item["from"])
        names = [next(f for f in state["files"] if f["id"] == fid)["name"] for fid in item["file_ids"]]
        self.assertEqual(names, ["Text photo 1.jpg", "Text file 2.pdf"])
        for fid, start in zip(item["file_ids"], (b"\xff\xd8", b"%PDF")):
            res, data = self.request("GET", f"/api/files/{fid}", cookie=cookie)
            self.assertEqual(res.status, 200)
            self.assertTrue(data.startswith(start))
        res, raw = self.request("POST", f"/api/inbox/{item['id']}/file", {"job_id": job_id, "kind": "ticket"}, cookie=cookie)
        self.assertEqual(res.status, 200, raw)
        res, raw = self.request("GET", f"/api/jobs/{job_id}", cookie=cookie)
        rec = [r for r in json.loads(raw)["job"]["records"] if r.get("inbox_id") == item["id"]][0]
        self.assertEqual(rec["kind"], "ticket")
        self.assertEqual(rec["file_ids"], item["file_ids"])
        self.assertIn("Pat Lee", rec["source"])

    def test_a_pdf_alone_is_not_called_a_photo(self):
        cookie, path = self.signup("pdf-kind@example.com")
        form = {"From": "+15555550124", "NumMedia": "1", "MediaUrl0": GOOD, "MediaContentType0": "application/pdf"}
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "secret"}), mock.patch.object(twilio_media, "fetch", lambda url, limit: b"%PDF-1.4"):
            self.post_signed(path, form)
        item = json.loads(self.request("GET", "/api/state", cookie=cookie)[1])["inbox"][-1]
        self.assertEqual(item["kind"], "note")
        self.assertEqual(item["from"], "Unknown sender (+15555550124)", "nobody on the team has this number")
        self.assertTrue(item["unknown_sender"])
        self.assertEqual(item["status"], "new")

    def test_a_text_naming_a_job_and_a_waiver_still_waits_for_a_person(self):
        cookie, path = self.signup("no-auto@example.com")
        self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "4045550177",
                                           "ap_email": "ap@northline.example", "contract_cents": 2000000}, cookie=cookie)
        form = {"From": "+15555550126", "Body": "Birch deck conditional waiver", "NumMedia": "1", "MediaUrl0": GOOD, "MediaContentType0": "application/pdf"}
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "secret"}), mock.patch.object(twilio_media, "fetch", lambda url, limit: b"%PDF-1.4"):
            res, raw = self.post_signed(path, form)
        self.assertEqual(raw, b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>', "nothing goes back")
        state = json.loads(self.request("GET", "/api/state", cookie=cookie)[1])
        item = state["inbox"][-1]
        self.assertEqual(item["status"], "new")
        self.assertFalse(item.get("auto_filed"))
        self.assertIn("Unknown sender", item["from"])

    def test_filing_a_text_needs_the_company_permission(self):
        cookie, path = self.signup("perm-owner@example.com")
        res, raw = self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "4045550177",
                                                     "ap_email": "ap@northline.example", "contract_cents": 2000000}, cookie=cookie)
        job_id = json.loads(raw)["jobs"][-1]["id"]
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "secret"}):
            self.post_signed(path, {"From": "+15555550127", "Body": "tickets"})
        item = json.loads(self.request("GET", "/api/state", cookie=cookie)[1])["inbox"][-1]
        with mock.patch.object(server, "has_perm", lambda user, perm, state=None: perm != "company"):
            res, raw = self.request("POST", f"/api/inbox/{item['id']}/file", {"job_id": job_id, "kind": "note"}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("permission", json.loads(raw)["error"])
        res, _ = self.request("POST", f"/api/inbox/{item['id']}/file", {"job_id": job_id, "kind": "note"}, cookie=cookie)
        self.assertEqual(res.status, 200)

    def test_stop_turns_texts_off_and_stays_out_of_the_inbox(self):
        cookie, path = self.signup("stop@example.com")
        me = next(u for u in auth.all_users() if u["email"] == "stop@example.com")
        agree(me["id"], "+15555550125")
        before = len(json.loads(self.request("GET", "/api/state", cookie=cookie)[1])["inbox"])
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "secret"}):
            res, _ = self.post_signed(path, {"From": "+15555550125", "Body": "STOP"})
            self.assertEqual(res.status, 200)
            self.post_signed(path, {"From": "+15555550125", "Body": "START"})
            self.post_signed(path, {"From": "+15555550125", "Body": "help"})
        state = json.loads(self.request("GET", "/api/state", cookie=cookie)[1])
        self.assertEqual(len(state["inbox"]), before)
        me = next(u for u in auth.all_users() if u["email"] == "stop@example.com")
        self.assertFalse(me.get("sms_opt_in"), "START at Twilio doesn't turn reminders back on; My account does")

    def test_sample_quickbooks_import_never_runs_for_a_real_company(self):
        cookie, _ = self.signup("sample-qbo@example.com")
        res, raw = self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "4045550177",
                                                     "ap_email": "ap@northline.example", "contract_cents": 2000000}, cookie=cookie)
        job_id = json.loads(raw)["jobs"][-1]["id"]
        res, raw = self.request("POST", f"/api/jobs/{job_id}/import-qbo", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("demo", json.loads(raw)["error"])
        self.assertNotIn("not built", json.loads(raw)["error"])


if __name__ == "__main__":
    unittest.main()
