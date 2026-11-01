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


if __name__ == "__main__":
    unittest.main()
