"""Production flags (P0-4) and the landing-page data notice (P0-8)."""

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
import server
import storage

GOOD = {
    "DURACASH_ENV": "production",
    "COOKIE_SECURE": "1",
    "DURACASH_URL": "https://app.duracash.example",
}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def env(self, **values):
        clean = {k: v for k, v in os.environ.items() if k not in {*GOOD, "DURACASH_DATA_DIR", "DURACASH_DATA_PERSISTENT"}}
        return mock.patch.dict(os.environ, {**clean, **values}, clear=True)

    def test_development_has_no_requirements(self):
        with self.env():
            self.assertFalse(server.production())
            self.assertTrue(server.demo_enabled())
            self.assertEqual(server.config_problems(), [])

    def test_production_complete_config_starts(self):
        with self.env(**GOOD, DURACASH_DATA_DIR=self.tmp.name), mock.patch.object(storage, "DATA_DIR", Path(self.tmp.name)):
            self.assertTrue(server.production())
            self.assertFalse(server.demo_enabled())
            self.assertEqual(server.config_problems(), [])

    def test_production_refuses_each_missing_setting(self):
        with self.env(DURACASH_ENV="production"):
            problems = " ".join(server.config_problems())
        for name in ("DURACASH_DATA_DIR", "COOKIE_SECURE", "DURACASH_URL"):
            self.assertIn(name, problems)
        with self.env(**{**GOOD, "DURACASH_URL": "http://app.duracash.example"}, DURACASH_DATA_DIR=self.tmp.name), \
                mock.patch.object(storage, "DATA_DIR", Path(self.tmp.name)):
            self.assertEqual(len(server.config_problems()), 1)

    def test_production_refuses_unmounted_disk(self):
        missing = Path(self.tmp.name) / "not-mounted"
        with self.env(**GOOD, DURACASH_DATA_DIR=str(missing)), mock.patch.object(storage, "DATA_DIR", missing):
            self.assertIn("does not exist", " ".join(server.config_problems()))

    def test_main_exits_when_misconfigured(self):
        with self.env(DURACASH_ENV="production"), mock.patch.object(server, "ensure_demo") as demo, \
                mock.patch.object(server, "log_json"), self.assertRaises(SystemExit):
            server.main()
        demo.assert_not_called()

    def test_data_notice_flag(self):
        with self.env():
            self.assertTrue(server.data_notice())
        with self.env(DURACASH_DATA_PERSISTENT="1"):
            self.assertFalse(server.data_notice())


class ProductionHttpTests(unittest.TestCase):
    """A demo account left on the disk can't be used once DURACASH_ENV=production."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR)
        auth.AUTH_PATH = Path(cls.tmp.name) / "auth.json"
        server.COMPANIES = Path(cls.tmp.name) / "companies"
        storage.DATA_DIR = Path(cls.tmp.name)  # /healthz writes a probe file here
        server.ensure_demo()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        res = conn.getresponse()
        data = json.loads(res.read() or b"{}")
        conn.close()
        return res, data

    def demo_login(self):
        return self.request("POST", "/api/auth/login", {"email": auth.DEMO_EMAIL, "password": auth.DEMO_PASSWORD})

    def test_demo_refused_in_production(self):
        res, _ = self.demo_login()
        self.assertEqual(res.status, 200)
        cookie = res.getheader("Set-Cookie").split(";")[0]
        with mock.patch.dict(os.environ, {"DURACASH_ENV": "production"}):
            res, _ = self.request("GET", "/api/state", cookie=cookie)
            self.assertEqual(res.status, 401, "an existing demo session stops working")
            res, _ = self.request("GET", "/api/auth/me", cookie=cookie)
            self.assertEqual(res.status, 401)
            res, body = self.demo_login()
            self.assertEqual(res.status, 401)
            self.assertEqual(body["error"], "Email or password is wrong.")
            res, health = self.request("GET", "/healthz")
            self.assertEqual(health["env"], "production")

    def test_signed_out_me_carries_data_notice(self):
        with mock.patch.dict(os.environ, {"DURACASH_DATA_PERSISTENT": ""}):
            res, body = self.request("GET", "/api/auth/me")
            self.assertEqual(res.status, 401)
            self.assertIs(body["data_notice"], True)
        with mock.patch.dict(os.environ, {"DURACASH_DATA_PERSISTENT": "1"}):
            _, body = self.request("GET", "/api/auth/me")
            self.assertIs(body["data_notice"], False)


if __name__ == "__main__":
    unittest.main()
