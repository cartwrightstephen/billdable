"""Two-step sign-in (P2-9): TOTP codes and recovery codes."""

import base64
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import server
import storage
import totp

PASSWORD = "correct horse battery"


class TotpTests(unittest.TestCase):
    def test_rfc6238_vectors(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual([totp.code_at(secret, t // 30, 8) for t in (59, 1111111109, 1234567890, 2000000000)],
                         ["94287082", "07081804", "89005924", "69279037"])

    def test_window_and_no_reuse(self):
        secret = totp.new_secret()
        now = 1_800_000_000
        code = totp.code_at(secret, now // 30)
        step = totp.verify(secret, code, now=now)
        self.assertEqual(step, now // 30)
        self.assertIsNone(totp.verify(secret, code, last_counter=step, now=now), "a code works once")
        self.assertIsNotNone(totp.verify(secret, totp.code_at(secret, now // 30 - 1), now=now), "30 seconds of clock drift")
        self.assertIsNone(totp.verify(secret, totp.code_at(secret, now // 30 - 3), now=now))
        self.assertIsNone(totp.verify(secret, "12345", now=now))
        self.assertTrue(totp.uri(secret, "pat@pine.example").startswith("otpauth://totp/Duracash:pat%40pine.example?secret="))


class TwoStepHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = root / "auth.json", root / "companies", root
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()

    def request(self, path, body, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        headers = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})}
        conn.request("POST", path, body=json.dumps(body).encode(), headers=headers)
        res = conn.getresponse()
        data = json.loads(res.read() or b"{}")
        conn.close()
        return res, data

    def test_turn_on_sign_in_with_code_and_recovery_then_turn_off(self):
        res, data = self.request("/api/auth/signup", {"accept_terms": True, "email": "two@example.com", "password": PASSWORD,
                                                      "name": "Pat", "company_name": "Pine"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, setup = self.request("/api/auth/2fa/begin", {}, cookie)
        self.assertEqual(res.status, 200)
        secret = setup["secret"]
        res, data = self.request("/api/auth/2fa/enable", {"code": "000000"}, cookie)
        self.assertEqual(res.status, 400)
        res, data = self.request("/api/auth/2fa/enable", {"code": totp.code_at(secret, int(time.time() // 30))}, cookie)
        self.assertEqual(res.status, 200, data)
        recovery = data["recovery_codes"]
        self.assertEqual(len(recovery), auth.RECOVERY_CODES)
        self.assertNotIn(recovery[0], auth.AUTH_PATH.read_text(), "recovery codes are stored hashed")

        login = {"email": "two@example.com", "password": PASSWORD}
        res, data = self.request("/api/auth/login", login)
        self.assertEqual((res.status, data.get("needs_code")), (401, True))
        self.assertIsNone(res.getheader("Set-Cookie"))
        res, data = self.request("/api/auth/login", {**login, "code": "999999"})
        self.assertEqual(res.status, 401)
        # The enable step used this time step's code; take the next one, as the next sign-in would.
        later = time.time() + 30
        with mock.patch.object(totp.time, "time", lambda: later):
            res, data = self.request("/api/auth/login", {**login, "code": totp.code_at(secret, int(later // 30))})
        self.assertEqual(res.status, 200, data)
        self.assertTrue(data["user"]["two_step"])
        auth._FAILURES.clear()
        res, data = self.request("/api/auth/login", {**login, "code": recovery[0]})
        self.assertEqual(res.status, 200, "a recovery code signs in")
        res, data = self.request("/api/auth/login", {**login, "code": recovery[0]})
        self.assertEqual(res.status, 401, "once")
        auth._FAILURES.clear()
        res, data = self.request("/api/auth/2fa/disable", {"password": "wrong password", "code": recovery[1]}, cookie)
        self.assertEqual(res.status, 400)
        res, data = self.request("/api/auth/2fa/disable", {"password": PASSWORD, "code": recovery[1]}, cookie)
        self.assertEqual(res.status, 200, data)
        res, data = self.request("/api/auth/login", login)
        self.assertEqual(res.status, 200, "back to password only")

    def test_wrong_codes_are_throttled(self):
        auth.create_user("thr@example.com", PASSWORD, "Pat", "co-thr")
        uid = auth.user_by_email("thr@example.com")["id"]
        secret = auth.totp_begin(uid)["secret"]
        auth.totp_enable(uid, totp.code_at(secret, int(time.time() // 30)))
        login = {"email": "thr@example.com", "password": PASSWORD}
        statuses = [self.request("/api/auth/login", {**login, "code": "111111"})[0].status for _ in range(auth.LOGIN_FREE_FAILURES + 1)]
        self.assertEqual(statuses[-1], 429)


if __name__ == "__main__":
    unittest.main()
