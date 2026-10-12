"""Auth and company isolation. Domain rules are tested separately."""

import secrets
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth


class AuthTests(unittest.TestCase):
    def setUp(self):
        # Tests touch the real auth file only through the module API.
        # We use unique emails so they do not collide with the demo account.
        self.email = f"test-{secrets.token_hex(3)}@example.com"
        self.company = "co-" + secrets.token_hex(3)

    def test_create_and_verify(self):
        user = auth.create_user(self.email, "secret", "Test Owner", self.company)
        self.assertEqual(user["company_id"], self.company)
        self.assertIsNone(auth.verify_login(self.email, "wrong"))
        self.assertEqual(auth.verify_login(self.email, "secret")["id"], user["id"])

    def test_duplicate_email_rejected(self):
        auth.create_user(self.email, "secret", "Test Owner", self.company)
        with self.assertRaises(ValueError):
            auth.create_user(self.email, "other", "Someone", "co-other")

    def test_session_round_trip(self):
        user = auth.create_user(self.email, "secret", "Test Owner", self.company)
        token = auth.create_session(user)
        loaded = auth.session_user(token)
        self.assertEqual(loaded["company_id"], self.company)
        auth.destroy_session(token)
        self.assertIsNone(auth.session_user(token))


if __name__ == "__main__":
    unittest.main()
