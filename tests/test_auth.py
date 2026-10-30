"""Auth and login throttling. Uses a temporary auth file, never data/auth.json."""

import json
import secrets
import sys
import threading
import time
from unittest import mock
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
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        self.email = f"test-{secrets.token_hex(3)}@example.com"
        self.company = "co-" + secrets.token_hex(3)
        self.password = "correct horse battery"

    def tearDown(self):
        auth.AUTH_PATH = self._orig_path
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
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

    def test_stored_session_key_does_not_sign_in(self):  # P0-7
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        token = auth.create_session(user)
        stored = json.loads(auth.AUTH_PATH.read_text())["sessions"]
        self.assertNotIn(token, stored)
        self.assertEqual(len(stored), 1)
        self.assertIsNone(auth.session_user(next(iter(stored))))
        self.assertIsNotNone(auth.session_user(token))

    def test_raw_token_file_is_migrated(self):  # P0-7: files from before hashing keep working
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        data = json.loads(auth.AUTH_PATH.read_text())
        data.pop("sessions_hashed", None)
        data["sessions"] = {"old-raw-token": {"user_id": user["id"], "company_id": self.company, "expires_at": "2999-01-01T00:00:00+00:00",
                                            "last_seen_at": auth._iso(auth._utcnow())}}
        auth.AUTH_PATH.write_text(json.dumps(data))
        self.assertEqual(auth.session_user("old-raw-token")["id"], user["id"])
        auth.destroy_session("old-raw-token")
        self.assertNotIn("old-raw-token", json.loads(auth.AUTH_PATH.read_text())["sessions"])
        self.assertIsNone(auth.session_user("old-raw-token"))

    def test_upgrade_file_writes_hashed_sessions_once(self):
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        data = json.loads(auth.AUTH_PATH.read_text())
        data.pop("sessions_hashed", None)
        data["sessions"] = {"raw-token-2": {"user_id": user["id"], "company_id": self.company, "expires_at": "2999-01-01T00:00:00+00:00",
                                            "last_seen_at": auth._iso(auth._utcnow())}}
        auth.AUTH_PATH.write_text(json.dumps(data))
        auth.upgrade_file()
        stored = json.loads(auth.AUTH_PATH.read_text())
        self.assertTrue(stored["sessions_hashed"])
        self.assertNotIn("raw-token-2", stored["sessions"])
        self.assertEqual(auth.session_user("raw-token-2")["id"], user["id"])

    # ----- idle sign-out (G2) -----

    def _age_session(self, token, minutes):
        data = json.loads(auth.AUTH_PATH.read_text())
        entry = data["sessions"][auth._digest(token)]
        entry["last_seen_at"] = auth._iso(auth._utcnow() - auth.timedelta(minutes=minutes))
        auth.AUTH_PATH.write_text(json.dumps(data))
        return entry["last_seen_at"]

    def test_idle_session_is_refused_and_deleted(self):
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        token = auth.create_session(user)
        self._age_session(token, 61)
        self.assertEqual(auth.session_check(token), (None, "idle"))
        self.assertNotIn(auth._digest(token), json.loads(auth.AUTH_PATH.read_text())["sessions"])
        self.assertEqual(auth.session_check(token), (None, "none"), "gone, not idle twice")

    def test_idle_minutes_come_from_the_environment(self):
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        token = auth.create_session(user)
        self._age_session(token, 20)
        self.assertIsNotNone(auth.session_user(token), "60 minutes by default")
        with mock.patch.dict("os.environ", {"DURACASH_IDLE_MINUTES": "15"}):
            self.assertEqual(auth.idle_minutes(), 15)
            self.assertEqual(auth.idle_words(), "Signed out after 15 minutes without use.")
            self._age_session(token, 20)   # the check above counted as use
            self.assertEqual(auth.session_check(token)[1], "idle")
        with mock.patch.dict("os.environ", {"DURACASH_IDLE_MINUTES": "nonsense"}):
            self.assertEqual(auth.idle_minutes(), 60)
        self.assertEqual(auth.idle_words(60), "Signed out after an hour without use.")

    def test_use_refreshes_last_seen_at_most_once_a_minute(self):
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        token = auth.create_session(user)
        first = json.loads(auth.AUTH_PATH.read_text())["sessions"][auth._digest(token)]["last_seen_at"]
        with mock.patch.object(auth, "_save", wraps=auth._save) as save:
            for _ in range(5):
                self.assertIsNotNone(auth.session_user(token))
            self.assertEqual(save.call_count, 0, "used within the minute: nothing written")
        old = self._age_session(token, 30)
        self.assertIsNotNone(auth.session_user(token))
        seen = json.loads(auth.AUTH_PATH.read_text())["sessions"][auth._digest(token)]["last_seen_at"]
        self.assertGreater(seen, old)
        self.assertGreaterEqual(seen, first)

    def test_sessions_from_before_idle_sign_out_count_as_expired(self):
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        token = auth.create_session(user)
        data = json.loads(auth.AUTH_PATH.read_text())
        data["sessions"][auth._digest(token)].pop("last_seen_at")
        auth.AUTH_PATH.write_text(json.dumps(data))
        self.assertEqual(auth.session_check(token), (None, "idle"))
        # A new sign-in clears the old ones out of the file.
        auth.create_session(user)
        self.assertEqual(len(json.loads(auth.AUTH_PATH.read_text())["sessions"]), 1)

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
        for _ in range(auth.LOGIN_FREE_FAILURES):
            self.assertIsNone(auth.verify_login(self.email, "wrong password"))
        with self.assertRaises(auth.RateLimited):
            auth.verify_login(self.email, self.password)

    def test_wait_grows_and_then_lets_the_owner_in(self):  # P1-8: a delay, not a lockout
        auth.create_user(self.email, self.password, "Test Owner", self.company)
        clock = [1000.0]
        with mock.patch.object(auth.time, "monotonic", lambda: clock[0]):
            for _ in range(auth.LOGIN_FREE_FAILURES):
                self.assertIsNone(auth.verify_login(self.email, "wrong password"))
            with self.assertRaisesRegex(auth.RateLimited, "2 seconds"):
                auth.verify_login(self.email, "wrong password")
            clock[0] += 2
            self.assertIsNone(auth.verify_login(self.email, "wrong password"))
            with self.assertRaisesRegex(auth.RateLimited, "4 seconds"):
                auth.verify_login(self.email, self.password)
            clock[0] += 4
            self.assertEqual(auth.verify_login(self.email, self.password)["email"], self.email)

    def test_one_network_is_cut_off_after_many_failures(self):
        clock = [5000.0]
        with mock.patch.object(auth.time, "monotonic", lambda: clock[0]):
            for i in range(auth.IP_MAX_FAILURES):
                self.assertIsNone(auth.verify_login(f"guess{i}@example.com", "wrong password", ip="203.0.113.9"))
            with self.assertRaisesRegex(auth.RateLimited, "network"):
                auth.verify_login("another@example.com", "wrong password", ip="203.0.113.9")
            self.assertIsNone(auth.verify_login("another@example.com", "wrong password", ip="198.51.100.4"))
            clock[0] += auth.LOGIN_WINDOW_SECONDS
            self.assertIsNone(auth.verify_login("another@example.com", "wrong password", ip="203.0.113.9"))

    def _all_at_once(self, count, call):
        """Run call(i) on count threads that start together; return what each gave or 'limited'."""
        barrier, results = threading.Barrier(count), []

        def run(i):
            barrier.wait()
            try:
                results.append(call(i))
            except auth.RateLimited:
                results.append("limited")
            except auth.AuthError as exc:
                results.append("limited" if "Too many" in str(exc) else "wrong")
        threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_parallel_guesses_are_throttled_like_ones_in_a_row(self):
        auth.create_user(self.email, self.password, "Test Owner", self.company)
        real = auth._matches

        def slow(user, password):  # the hash takes long enough for all the requests to arrive
            time.sleep(0.3)
            return real(user, password)
        with mock.patch.object(auth, "_matches", slow):
            results = self._all_at_once(12, lambda i: auth.verify_login(self.email, f"wrong {i}", ip="203.0.113.7"))
        self.assertEqual(sum(1 for r in results if r != "limited"), auth.LOGIN_FREE_FAILURES)

    def test_parallel_wrong_current_passwords_are_throttled(self):
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        real = auth._matches

        def slow(u, password):
            time.sleep(0.3)
            return real(u, password)
        with mock.patch.object(auth, "_matches", slow):
            results = self._all_at_once(10, lambda i: auth.change_password(user["id"], f"wrong {i}", "a brand new password"))
        self.assertEqual(results.count("wrong"), auth.LOGIN_FREE_FAILURES)

    def test_right_passwords_count_as_no_failure(self):
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        for _ in range(auth.IP_MAX_FAILURES + 2):
            self.assertEqual(auth.verify_login(self.email, self.password, ip="203.0.113.8")["id"], user["id"])
        self.assertFalse(auth._FAILURES.get(self.email))
        self.assertFalse(auth._IP_FAILURES.get("203.0.113.8"))
        auth.change_password(user["id"], self.password, "a brand new password")
        self.assertFalse(auth._FAILURES.get(self.email))
        # With two-step sign-in a right password neither clears nor adds to the count.
        data = json.loads(auth.AUTH_PATH.read_text())
        data["users"][0]["totp_enabled"] = True
        auth.AUTH_PATH.write_text(json.dumps(data))
        self.assertIsNone(auth.verify_login(self.email, "wrong password", ip="203.0.113.8"))
        self.assertIsNotNone(auth.verify_login(self.email, "a brand new password", ip="203.0.113.8"))
        self.assertEqual(len(auth._FAILURES[self.email]), 1)
        self.assertEqual(len(auth._IP_FAILURES["203.0.113.8"]), 1)

    def test_user_ids_never_repeat(self):
        real, repeats = secrets.token_hex, [6]

        def token_hex(n=None):  # auth's first few short ids all come out the same
            if n == 4 and repeats[0] and sys._getframe(1).f_globals.get("__name__") == "auth":
                repeats[0] -= 1
                return "0badc0de"
            return real(n)
        with mock.patch.object(auth.secrets, "token_hex", token_hex):
            first = auth.create_user(self.email, self.password, "Test Owner", self.company)
            second = auth.create_user("second@example.com", self.password, "Second", "co-other")
            raw, _ = auth.create_invite(self.company, "third@example.com", "member", "Test Owner", first["id"])
            third = auth.accept_invite(raw, "Third", self.password)
        self.assertEqual(len({first["id"], second["id"], third["id"]}), 3)
        token = auth.create_session(second)
        self.assertEqual(auth.session_user(token)["email"], "second@example.com")

    def test_demo_sessions_are_capped(self):
        demo = auth.create_user(auth.DEMO_EMAIL, auth.DEMO_PASSWORD, "Demo", auth.DEMO_COMPANY_ID, enforce_policy=False)
        owner = auth.create_user(self.email, self.password, "Test Owner", self.company)
        mine = auth.create_session(owner)
        with mock.patch.object(auth, "DEMO_MAX_SESSIONS", 5):
            tokens = [auth.create_session(demo) for _ in range(9)]
        sessions = json.loads(auth.AUTH_PATH.read_text())["sessions"]
        self.assertEqual(sum(1 for v in sessions.values() if v["user_id"] == demo["id"]), 5)
        self.assertIsNone(auth.session_user(tokens[0]), "the oldest demo session gives way")
        self.assertIsNotNone(auth.session_user(tokens[-1]))
        self.assertIsNotNone(auth.session_user(mine), "real accounts' sessions are untouched")

    def test_old_hash_is_upgraded_at_sign_in(self):  # P1-15
        user = auth.create_user(self.email, self.password, "Test Owner", self.company)
        data = json.loads(auth.AUTH_PATH.read_text())
        stored = data["users"][0]
        self.assertEqual(stored["kdf_iterations"], auth.KDF_ITERATIONS)
        salt, digest = auth._hash(self.password, None, auth.LEGACY_ITERATIONS)
        stored.update(salt=salt, password_hash=digest)
        stored.pop("kdf_iterations")
        auth.AUTH_PATH.write_text(json.dumps(data))
        self.assertEqual(auth.verify_login(self.email, self.password)["id"], user["id"])
        upgraded = json.loads(auth.AUTH_PATH.read_text())["users"][0]
        self.assertEqual(upgraded["kdf_iterations"], auth.KDF_ITERATIONS)
        self.assertNotEqual(upgraded["password_hash"], digest)
        self.assertEqual(auth.verify_login(self.email, self.password)["id"], user["id"])

    def test_demo_account_skips_policy(self):
        user = auth.create_user("demo@example.com", "demo", "Demo", "co-demo", enforce_policy=False)
        self.assertEqual(auth.verify_login("demo@example.com", "demo")["id"], user["id"])

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        auth.create_user(self.email, self.password, "Test Owner", self.company)
        names = [p.name for p in Path(self.tmp.name).iterdir()]
        self.assertEqual(names, ["auth.json"])


if __name__ == "__main__":
    unittest.main()
