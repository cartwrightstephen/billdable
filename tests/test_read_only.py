"""Read-only mode: the public demo in production, and a company a founder has put on hold
(P2-2's "past due: read-only, never deletion")."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import admin
import auth
import helper
import importer
import server
import storage

PASSWORD = "correct horse battery"


class DemoModeTests(unittest.TestCase):
    def test_modes(self):
        cases = [({}, "on"), ({"DURACASH_ENV": "production"}, "off"),
                 ({"DURACASH_ENV": "production", "DURACASH_DEMO": "readonly"}, "readonly"),
                 ({"DURACASH_ENV": "production", "DURACASH_DEMO": "on"}, "off"),
                 ({"DURACASH_DEMO": "off"}, "off"), ({"DURACASH_DEMO": "readonly"}, "readonly")]
        for env, want in cases:
            with mock.patch.dict("os.environ", {"DURACASH_ENV": "", "DURACASH_DEMO": "", **env}):
                self.assertEqual(server.demo_mode(), want, env)


class ReadOnlyHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, helper.LOG_PATH, helper.LOCAL_KB)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = root / "auth.json", root / "companies", root
        helper.LOG_PATH, helper.LOCAL_KB = root / "helper_log.json", root / "helper_answers.json"
        server.ensure_demo()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, helper.LOG_PATH, helper.LOCAL_KB = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        headers = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})}
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            return res, json.loads(raw or b"{}")
        except ValueError:
            return res, None

    def signup(self, email):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": PASSWORD,
                                                              "name": "Pat Pine", "company_name": "Pine Builders"})
        self.assertEqual(res.status, 200, data)
        return res.getheader("Set-Cookie").split(";")[0], data["user"]

    def test_a_held_company_keeps_its_data_but_takes_no_changes(self):
        cookie, user = self.signup("hold@example.com")
        job = {"name": "Ash", "customer": "Ash GC", "contract_cents": 100000, "address": "1 Ash St", "approver": "Al Ash", "ap_email": "ap@ashgc.com"}
        res, _ = self.request("POST", "/api/jobs", job, cookie=cookie)
        self.assertEqual(res.status, 200)
        with mock.patch("builtins.print"):
            self.assertEqual(admin.main(["admin.py", "read-only", user["company_id"], "payment", "is", "past", "due"]), 0)

        res, data = self.request("POST", "/api/jobs", job, cookie=cookie)
        self.assertEqual(res.status, 403)
        self.assertTrue(data["read_only"])
        self.assertIn("payment is past due", data["error"])
        res, data = self.request("POST", "/api/import/text", {"text": "a note"}, cookie=cookie)
        self.assertEqual(res.status, 403)
        res, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual((res.status, len(state["jobs"])), (200, 1), "nothing is deleted")
        res, _ = self.request("GET", "/api/export", cookie=cookie)
        self.assertEqual(res.status, 200)
        res, data = self.request("POST", "/api/auth/password", {"current": "wrong password!", "new": "another password 1"}, cookie=cookie)
        self.assertEqual(res.status, 400, "sign-in security still works; this fails only on the wrong password")
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertIn("payment is past due", me["read_only"])
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        body = b"--b\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\nshared note\r\n--b--\r\n"
        conn.request("POST", "/share", body=body, headers={"Cookie": cookie, "Content-Type": "multipart/form-data; boundary=b"})
        res = conn.getresponse(); res.read(); conn.close()
        self.assertEqual(res.status, 303)
        self.assertEqual(server.load_company(user["company_id"]).get("inbox", []), [], "sharing to a held company stores nothing")

        with mock.patch("builtins.print"):
            self.assertEqual(admin.main(["admin.py", "writable", user["company_id"]]), 0)
        res, _ = self.request("POST", "/api/jobs", job, cookie=cookie)
        self.assertEqual(res.status, 200)
        res, page = self.request("GET", "/api/audit?limit=3", cookie=cookie)
        self.assertEqual([e["action"] for e in page["entries"][1:3]], ["writable", "read_only"])
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertEqual(me["read_only"], "")

    def test_the_production_demo_is_read_only(self):
        env = {"DURACASH_ENV": "production", "DURACASH_DEMO": "readonly"}
        with mock.patch.dict("os.environ", env):
            res, data = self.request("POST", "/api/auth/login", {"email": auth.DEMO_EMAIL, "password": auth.DEMO_PASSWORD})
            self.assertEqual(res.status, 200, data)
            cookie = res.getheader("Set-Cookie").split(";")[0]
            res, state = self.request("GET", "/api/state", cookie=cookie)
            self.assertEqual(res.status, 200)
            job_id = state["jobs"][0]["id"]
            for path, body in [("/api/reset", {}), (f"/api/jobs/{job_id}/done", {}), ("/api/auth/password", {"current": auth.DEMO_PASSWORD, "new": "taken over 123"}),
                               ("/api/auth/2fa/begin", {}), ("/api/team/invite", {"email": "x@example.com", "role": "owner"}),
                               ("/api/account/delete", {"password": auth.DEMO_PASSWORD, "confirm": "Hartline Remodeling"})]:
                res, data = self.request("POST", path, body, cookie=cookie)
                self.assertEqual(res.status, 403, path)
            res, data = self.request("POST", "/api/helper/ask", {"q": "What should I do today?"}, cookie=cookie)
            self.assertEqual(res.status, 200, "the helper still answers")
            res, me = self.request("GET", "/api/auth/me", cookie=cookie)
            self.assertIn("read-only demo", me["read_only"])
            # Its inbound addresses are visible to every visitor: what arrives there is ignored.
            tokens = importer.company_tokens(auth.DEMO_COMPANY_ID)
            inbox_before = len(server.load_company(auth.DEMO_COMPANY_ID).get("inbox", []))
            for path in (f"/api/inbound/email/{tokens['email']}", f"/api/inbound/sms/{tokens['sms']}"):
                res, data = self.request("POST", path, {"from": "x@example.com", "text": "spam"})
                self.assertEqual((res.status, data.get("ignored")), (200, "the demo is read-only"), path)
            self.assertEqual(len(server.load_company(auth.DEMO_COMPANY_ID).get("inbox", [])), inbox_before)
        with mock.patch.dict("os.environ", {"DURACASH_ENV": "production", "DURACASH_DEMO": ""}):
            res, _ = self.request("GET", "/api/state", cookie=cookie)
            self.assertEqual(res.status, 401, "with the demo off, its sessions stop working")


if __name__ == "__main__":
    unittest.main()
