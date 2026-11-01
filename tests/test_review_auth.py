"""Oct 30 review, section A: sign-in, settings and permissions (A1-A6).

Built from the reviewer's reproduction (stephen-review-2026-10-29/rev-scripts/rev-auth/test_rev.py).
"""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import server
from domain import profiles
from test_accounts import ServerCase, PASSWORD
import test_team
from test_team import JOB


class ReviewAuthTests(ServerCase):
    join = test_team.TeamHttpTests.join

    def setUp(self):
        super().setUp()
        auth._FAILURES.clear(); auth._IP_FAILURES.clear(); auth._CODES.clear(); auth._CODE_WRONG.clear()
        server._LIMITS.clear()

    def tearDown(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear(); auth._CODES.clear(); auth._CODE_WRONG.clear()
        server._LIMITS.clear()
        super().tearDown()

    # ----- A1: no session from the password alone when mail is set up -----

    def test_a1_code_that_cant_be_sent_gives_no_session(self):
        self.mail_on()
        auth.create_user("a1@rev.example", PASSWORD, "Pat", "co-a1")
        with mock.patch.object(server, "send_system_email", return_value="SMTPServerDisconnected: down"):
            res, data = self.request("POST", "/api/auth/login", {"email": "a1@rev.example", "password": PASSWORD})
        self.assertEqual(res.status, 503, data)
        self.assertIsNone(res.getheader("Set-Cookie"), "no session from the password alone")
        self.assertEqual(data["error"], "We couldn't send your sign-in code. Try again shortly.")
        self.assertNotIn("needs_code", data)

    def test_a1_sign_in_codes_are_off_the_shared_hourly_limit(self):
        self.mail_on()
        auth.create_user("a1b@rev.example", PASSWORD, "Pat", "co-a1b")
        server._LIMITS["system-email"] = [time.monotonic()] * 300   # reset requests and sign-ups used the hour up
        res, data = self.request("POST", "/api/auth/login", {"email": "a1b@rev.example", "password": PASSWORD})
        self.assertEqual((res.status, data.get("needs_code"), data.get("method")), (401, True, "email"), data)
        self.assertIsNone(res.getheader("Set-Cookie"))
        res, data = self.request("POST", "/api/auth/login", {"email": "a1b@rev.example", "password": PASSWORD, "code": self.last_code()})
        self.assertEqual(res.status, 200, data)

    def test_a1_password_change_code_is_off_the_shared_hourly_limit(self):
        self.mail_on()
        cookie, _ = self.signup("a1c@rev.example")
        server._LIMITS["system-email"] = [time.monotonic()] * 300
        res, data = self.request("POST", "/api/auth/password/code", {}, cookie=cookie)
        self.assertEqual(res.status, 200, data)

    def test_a1_without_mail_the_password_still_signs_in(self):
        auth.create_user("a1d@rev.example", PASSWORD, "Pat", "co-a1d")
        res, data = self.request("POST", "/api/auth/login", {"email": "a1d@rev.example", "password": PASSWORD})
        self.assertEqual(res.status, 200, data)
        self.assertTrue(data["user"]["two_step_setup_due"], "and the page asks to set up the app")

    # ----- A2: wrong emailed codes are throttled per account -----

    def test_a2_right_password_does_not_reset_the_code_throttle(self):
        self.mail_on()
        auth.create_user("a2@rev.example", PASSWORD, "Pat", "co-a2")
        login = {"email": "a2@rev.example", "password": PASSWORD}
        res, data = self.request("POST", "/api/auth/login", login)
        self.assertEqual(data.get("method"), "email", data)
        code = self.last_code()
        wrong = "000000" if code != "000000" else "111111"
        statuses = [self.request("POST", "/api/auth/login", {**login, "code": wrong})[0].status
                    for _ in range(auth.LOGIN_FREE_FAILURES + 1)]
        self.assertEqual(statuses[-1], 429, statuses)

    def test_a2_five_wrong_codes_drop_the_live_sign_in_codes(self):
        self.mail_on()
        auth.create_user("a2b@rev.example", PASSWORD, "Pat", "co-a2b")
        login = {"email": "a2b@rev.example", "password": PASSWORD}
        self.request("POST", "/api/auth/login", login)
        code = self.last_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(auth.CODE_MAX_WRONG):
            auth._FAILURES.clear()   # past the waits: only the per-code count is left
            res, _ = self.request("POST", "/api/auth/login", {**login, "code": wrong})
            self.assertEqual(res.status, 401)
        auth._FAILURES.clear()
        res, data = self.request("POST", "/api/auth/login", {**login, "code": code})
        self.assertEqual(res.status, 401, "the code sent before is gone")
        self.assertIsNone(res.getheader("Set-Cookie"))

    def test_a2_a_right_code_signs_in_and_clears_the_count(self):
        self.mail_on()
        auth.create_user("a2c@rev.example", PASSWORD, "Pat", "co-a2c")
        login = {"email": "a2c@rev.example", "password": PASSWORD}
        self.request("POST", "/api/auth/login", login)
        code = self.last_code()
        wrong = "000000" if code != "000000" else "111111"
        self.request("POST", "/api/auth/login", {**login, "code": wrong})
        res, data = self.request("POST", "/api/auth/login", {**login, "code": code})
        self.assertEqual(res.status, 200, data)
        self.assertFalse(auth._FAILURES.get("a2c@rev.example"))
        self.assertFalse(auth._CODE_WRONG)

    def test_a2_five_wrong_password_change_codes_drop_the_code(self):
        auth.create_user("a2d@rev.example", PASSWORD, "Pat", "co-a2d")
        uid = auth.user_by_email("a2d@rev.example")["id"]
        code = auth.issue_code(uid, "pwchange")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(auth.CODE_MAX_WRONG):
            auth._FAILURES.clear()
            with self.assertRaises(auth.AuthError):
                auth.change_password(uid, PASSWORD, "a brand new password", code=wrong, need_code=True)
        auth._FAILURES.clear()
        with self.assertRaises(auth.AuthError):
            auth.change_password(uid, PASSWORD, "a brand new password", code=code, need_code=True)

    # ----- A3: recording payments from the import needs "Record payments" -----

    def test_a3_payments_apply_needs_record_payments(self):
        owner, _ = self.signup("a3@rev.example", "Rev A3")
        res, out = self.request("POST", "/api/team/profiles/member", {"name": "Member", "perms": []}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        cookie, _ = self.join(owner, "mo@reva3.example", "member")
        _, state = self.request("POST", "/api/jobs", JOB, cookie=owner)
        job_id = state["jobs"][0]["id"]
        _, state = self.request("POST", f"/api/jobs/{job_id}/progress", {"completed_cents": 400_000}, cookie=owner)
        inv = state["jobs"][0]["invoices"][-1]
        for item in inv.get("packet") or []:
            self.request("POST", f"/api/jobs/{job_id}/resolve/{item['id']}", {"reason": "sent with the email"}, cookie=owner)
        res, state = self.request("POST", f"/api/jobs/{job_id}/send", {}, cookie=owner)
        self.assertEqual(res.status, 200, state)
        inv = state["jobs"][0]["invoices"][-1]
        pick = {"job_id": job_id, "invoice_id": inv["id"], "amount_cents": inv["amount_cents"], "reference": "77"}
        res, out = self.request("POST", "/api/import/payments-apply", {"payments": [pick]}, cookie=cookie)
        self.assertEqual(res.status, 403, out)
        res, state = self.request("GET", "/api/state", cookie=owner)
        self.assertNotEqual(state["jobs"][0]["invoices"][-1]["status"], "paid")
        res, out = self.request("POST", "/api/import/payments-apply", {"payments": [pick]}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertTrue(out["import_results"][0]["ok"], out)

    # ----- A4: only the Owner profile creates, changes or deletes profiles -----

    def test_a4_only_owners_manage_profiles(self):
        owner, _ = self.signup("a4@rev.example", "Rev A4")
        res, out = self.request("POST", "/api/team/profiles", {"name": "Office", "perms": ["team"]}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        office = out["profile"]
        cookie, _ = self.join(owner, "olive@reva4.example", office["id"])
        res, out = self.request("POST", f"/api/team/profiles/{office['id']}", {"name": "Office", "perms": list(profiles.PERM_KEYS)}, cookie=cookie)
        self.assertEqual(res.status, 403, out)
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertEqual(me["user"]["perms"], ["team"], "nothing granted")
        res, out = self.request("POST", "/api/team/profiles", {"name": "Boss", "perms": list(profiles.PERM_KEYS)}, cookie=cookie)
        self.assertEqual(res.status, 403, out)
        res, out = self.request("POST", "/api/team/profiles/member", {"name": "Member", "perms": list(profiles.PERM_KEYS)}, cookie=cookie)
        self.assertEqual(res.status, 403, out)
        res, out = self.request("POST", f"/api/team/profiles/{office['id']}/delete", {}, cookie=cookie)
        self.assertEqual(res.status, 403, out)
        # Managing the team (invites) still works for them.
        res, out = self.request("POST", "/api/team/invite", {"email": "new@reva4.example", "profile_id": "member"}, cookie=cookie)
        self.assertEqual(res.status, 200, out)

    # ----- A5: a reset keeps the company's profiles -----

    def test_a5_reset_keeps_the_profiles(self):
        owner, _ = self.signup("a5@rev.example", "Rev A5")
        res, out = self.request("POST", "/api/team/profiles", {"name": "Office", "perms": ["record_payments"]}, cookie=owner)
        office = out["profile"]
        cookie, _ = self.join(owner, "olive@reva5.example", office["id"])
        res, out = self.request("POST", "/api/reset", {"confirm": "Rev A5", "password": PASSWORD}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        res, team = self.request("GET", "/api/team", cookie=owner)
        self.assertIn(office["id"], [p["id"] for p in team["profiles"]])
        olive = next(u for u in team["users"] if u["email"] == "olive@reva5.example")
        self.assertEqual(olive["profile_name"], "Office")

    # ----- A6: a password reset forgets remembered devices -----

    def test_a6_reset_forgets_remembered_devices(self):
        self.mail_on()
        self.signup("a6@rev.example", "Rev A6")
        login = {"email": "a6@rev.example", "password": PASSWORD}
        self.request("POST", "/api/auth/login", login)
        res, data = self.request("POST", "/api/auth/login", {**login, "code": self.last_code()})
        self.assertEqual(res.status, 200, data)
        device = next(c.split(";")[0] for c in res.msg.get_all("Set-Cookie") if c.startswith(server.DEVICE_COOKIE + "="))
        self.sink.messages.clear()
        self.request("POST", "/api/auth/forgot", {"email": "a6@rev.example"})
        self.wait_for_mail(1)
        _, raw = self.link_token("reset")
        res, data = self.request("POST", "/api/auth/reset", {"token": raw, "password": "brand new password 9"})
        self.assertEqual(res.status, 200, data)
        res, data = self.request("POST", "/api/auth/login", {"email": "a6@rev.example", "password": "brand new password 9"}, cookie=device)
        self.assertEqual((res.status, data.get("method")), (401, "email"), "the old device asks for a code again")


if __name__ == "__main__":
    unittest.main()
