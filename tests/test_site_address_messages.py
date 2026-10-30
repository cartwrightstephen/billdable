"""Without the site's address (DURACASH_URL) a contractor reads plain words, never the setting's
name; the server log keeps the technical reason (Oct 23 review, note 17)."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import qbo
import server
from seed import seed_state
from test_accounts import ServerCase


def logged(calls) -> str:
    return " ".join(str(c.kwargs.get("msg", "")) for c in calls.call_args_list)


class NoSiteAddressTests(ServerCase):
    def setUp(self):
        super().setUp()
        os.environ.pop("DURACASH_URL", None)

    def test_signup_and_resend_say_email_isnt_set_up(self):
        with mock.patch.object(server, "log_json") as log:
            cookie, data = self.signup("plain-verify@pine.example")
            self.assertEqual(data["verification"], server.EMAIL_NOT_SET_UP)
            self.assertNotIn("DURACASH_URL", data["verification"])
            res, out = self.request("POST", "/api/auth/resend-verify", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("isn't set up on this server yet", out["error"])
        self.assertNotIn("DURACASH_URL", out["error"])
        self.assertIn("DURACASH_URL", logged(log), "the reason is in the server log")

    def test_invite_not_emailed_reason_is_plain(self):
        owner, _ = self.signup("plain-invite@pine.example", "Plain Co")
        with mock.patch.object(server, "log_json") as log:
            res, out = self.request("POST", "/api/team/invite", {"email": "helper@pine.example", "role": "member"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertFalse(out["emailed"])
        self.assertEqual(out["email_status"], server.EMAIL_NOT_SET_UP)
        self.assertIn("DURACASH_URL", logged(log))

    def test_request_signature_is_refused_in_plain_words(self):
        cookie, _ = self.signup("plain-co@pine.example", "Plain Signs")
        res, data = self.request("POST", "/api/jobs", {"name": "Elm", "customer": "Park GC", "address": "9 Elm", "approver": "Kim",
                                                       "ap_email": "ap@park.example", "contract_cents": 1_000_000}, cookie)
        job = data["jobs"][-1]
        res, data = self.request("POST", f"/api/jobs/{job['id']}/add-co", {"title": "Two outlets", "amount_cents": 45_000, "reason": "owner_request"}, cookie)
        co = data["jobs"][-1]["change_orders"][-1]
        with mock.patch.object(server, "log_json") as log:
            res, data = self.request("POST", f"/api/jobs/{job['id']}/request-co-signature/{co['id']}", {}, cookie)
        self.assertEqual(res.status, 409, data)
        self.assertEqual(data["error"], domain.NO_SIGNING_LINKS)
        self.assertNotIn("DURACASH_URL", data["error"])
        self.assertIn("Contact Duracash support", data["error"])
        self.assertIn("DURACASH_URL", logged(log))

    def test_subscribe_is_refused_in_plain_words(self):
        cookie, _ = self.signup("plain-bill@pine.example", "Plain Bills")
        with mock.patch.object(server, "log_json") as log:
            res, data = self.request("POST", "/api/billing/checkout", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertNotIn("DURACASH_URL", data["error"])
        self.assertIn("Contact Duracash support", data["error"])
        self.assertIn("DURACASH_URL", logged(log))

    def test_connect_quickbooks_is_refused_in_plain_words(self):
        cookie, _ = self.signup("plain-qbo@pine.example", "Plain Books")
        with mock.patch.dict(os.environ, {"QBO_CLIENT_ID": "id", "QBO_CLIENT_SECRET": "secret"}), \
                mock.patch.object(server, "log_json") as log:
            res, data = self.request("POST", "/api/qbo/connect", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertNotIn("DURACASH_URL", data["error"])
        self.assertIn("Contact Duracash support", data["error"])
        self.assertIn("DURACASH_URL", logged(log))


class NoSettingNamesShownTests(unittest.TestCase):
    def test_domain_refusal_has_no_setting_name(self):
        state = seed_state()
        job = next(j for j in state["jobs"] if any(c["status"] == "pending" for c in j["change_orders"]))
        co = next(c for c in job["change_orders"] if c["status"] == "pending")
        with self.assertRaises(domain.GateError) as caught:
            domain.request_co_signature(state, job["id"], co["id"], "owner")
        self.assertNotIn("DURACASH_URL", str(caught.exception))
        self.assertIn("Signed on paper…", str(caught.exception))

    def test_qbo_refusal_has_no_setting_name(self):
        with mock.patch.dict(os.environ, {"QBO_CLIENT_ID": "id", "QBO_CLIENT_SECRET": "secret"}):
            with self.assertRaises(qbo.QboError) as caught:
                qbo.authorize_url("co-x", "Pat", "/api/qbo/callback")
        self.assertNotIn("DURACASH_URL", str(caught.exception))
        self.assertTrue(qbo.redirect_ok("https://app.duracash.example/api/qbo/callback"))
        self.assertFalse(qbo.redirect_ok("/api/qbo/callback"))

    def test_no_user_facing_string_names_the_setting(self):
        """What the app and the server hand a contractor never names DURACASH_URL; comments,
        log lines and the startup check (read by whoever runs the server) may."""
        root = Path(__file__).resolve().parents[1]
        for path in (root / "static").rglob("*.js"):
            self.assertNotIn("DURACASH_URL", path.read_text(), path.name)
        for path in (root / "server.py", root / "domain" / "__init__.py", root / "qbo.py"):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                shown = any(k in line for k in ("Error(", '"error"', "email_status", 'return "', "message"))
                self.assertFalse("DURACASH_URL" in line and shown, f"{path.name}:{n}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
