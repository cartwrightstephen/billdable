"""Auth and login throttling. Uses a temporary auth file, never data/auth.json."""

import secrets
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_path = auth.AUTH_PATH
        auth.AUTH_PATH = Path(self.tmp.name) / "auth.json"
        auth._FAILURES.clear()
        self.email = f"test-{secrets.token_hex(3)}@example.com"
        self.company = "co-" + secrets.token_hex(3)
        self.password = "correct horse battery"

    def tearDown(self):
        auth.AUTH_PATH = self._orig_path
        auth._FAILURES.clear()
        self.tmp.cleanup()

    def test_create_and_verify(self):
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        self.assertEqual(user["company_id"], self.company)
        self.assertIsNone(auth.verify_login(self.email, "wrong password"))
        self.assertEqual(auth.verify_login(self.email, self.password)["id"], user["id"])

    def test_duplicate_email_rejected(self):
        auth.create_user(self.email, self.password, "Test Owner", self.company)
        with self.assertRaises(ValueError):
            auth.create_user(self.email, "another long one", "Someone", "co-other")

    def test_session_round_trip(self):
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        token = auth.create_session(user)
        loaded = auth.session_user(token)
        self.assertEqual(loaded["company_id"], self.company)
        auth.destroy_session(token)
        self.assertIsNone(auth.session_user(token))

    def test_password_min_length(self):
        with self.assertRaises(ValueError):
            auth.create_user(self.email, "123456789", "Test Owner", self.company)

    def test_email_must_be_real(self):
        for bad in ["@", "a@b", "no-at-sign.com"]:
            with self.assertRaises(ValueError):
                auth.create_user(bad, self.password, "Test Owner", self.company)

    def test_non_text_password_rejected(self):
        with self.assertRaises(ValueError):
            auth.create_user(self.email, 12345678901, "Test Owner", self.company)
        self.assertIsNone(auth.verify_login(self.email, 12345678901))

    def test_login_rate_limited(self):
        auth.create_user(self.email, self.password, "Test Owner", self.company)
        for _ in range(auth.LOGIN_MAX_FAILURES):
            self.assertIsNone(auth.verify_login(self.email, "wrong password"))
        with self.assertRaises(auth.RateLimited):
            auth.verify_login(self.email, self.password)

    def test_demo_account_skips_policy(self):
        user = auth.create_user("demo@example.com", "demo", "Demo", "co-demo", enforce_policy=False)
        self.assertEqual(auth.verify_login("demo@example.com", "demo")["id"], user["id"])

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        auth.create_user(self.email, self.password, "Test Owner", self.company)
        names = [p.name for p in Path(self.tmp.name).iterdir()]
        self.assertEqual(names, ["auth.json"])


if __name__ == "__main__":
    unittest.main()
