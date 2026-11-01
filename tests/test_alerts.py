"""Crash and backup alerts (P0-5)."""

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

import alerts
import auth
import server

HOOK = {"ALERT_WEBHOOK_URL": "https://hooks.example.com/T000/B000/xyz"}


def reset():
    alerts._sent.clear()
    alerts._last.clear()


class AlertTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.sent = []
        self.send = lambda url, text: self.sent.append((url, text))

    def test_off_without_https_webhook(self):
        for value in ("", "http://hooks.example.com/x"):
            with mock.patch.dict(os.environ, {"ALERT_WEBHOOK_URL": value}):
                self.assertFalse(alerts.notify("k", "boom", send=self.send))
        self.assertEqual(self.sent, [])

    def test_repeats_and_hourly_limit(self):
        with mock.patch.dict(os.environ, HOOK):
            self.assertTrue(alerts.notify("crash:/api/state", "one", send=self.send))
            self.assertFalse(alerts.notify("crash:/api/state", "same again", send=self.send))
            for i in range(alerts.HOURLY_LIMIT + 5):
                alerts.notify(f"crash:/api/{i}", "other", send=self.send)
        self.assertEqual(len(self.sent), alerts.HOURLY_LIMIT)
        self.assertEqual(self.sent[0][0], HOOK["ALERT_WEBHOOK_URL"])
        self.assertTrue(self.sent[0][1].startswith("Duracash ("))


class CrashAlertHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._orig = (auth.AUTH_PATH, server.COMPANIES)
        auth.AUTH_PATH = Path(cls.tmp.name) / "auth.json"
        server.COMPANIES = Path(cls.tmp.name) / "companies"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES = cls._orig
        cls.tmp.cleanup()

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

    def test_induced_500_alerts_without_customer_data(self):
        reset()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "crash@example.com",
                                                           "password": "correct horse battery", "name": "Pat", "company_name": "Pine"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        calls = []
        with mock.patch.dict(os.environ, HOOK), \
                mock.patch.object(server, "load_company", side_effect=RuntimeError("secret customer detail")), \
                mock.patch.object(alerts, "_post", side_effect=lambda url, text: calls.append(text)), \
                mock.patch.object(server, "log_json"):
            res, body = self.request("GET", "/api/state", cookie=cookie)
            for t in [t for t in threading.enumerate() if t.name == "alert"]:
                t.join(5)
        self.assertEqual(res.status, 500)
        self.assertEqual(len(calls), 1)
        self.assertIn("RuntimeError on GET /api/state", calls[0])
        self.assertIn(body["request_id"], calls[0])
        self.assertNotIn("secret customer detail", calls[0])


if __name__ == "__main__":
    unittest.main()
