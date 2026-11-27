"""Security review L2, L3 and L4: common passwords refused, a sign-up reply that doesn't say
whether an email is registered, the session list and Sign out everywhere else, customer and
signing links that expire, the helper log kept bounded, and the export with the files."""

import io
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import domain
import helper
import passwords
import payer_page
import server
from test_accounts import PASSWORD, ServerCase

CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


class PasswordRuleTests(ServerCase):
    def test_common_and_predictable_passwords_are_refused_everywhere(self):
        for pw in ("password123", "P@ssw0rd2026!", "1234567890", "qwertyuiop", "aaaaaaaaaaaa", "Contractor2026", "iloveyou12"):
            res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "weak@pine.example", "password": pw,
                                                                 "name": "Pat Pine", "company_name": "Pine"})
            self.assertEqual(res.status, 400, pw)
            self.assertNotIn("Set-Cookie", dict(res.getheaders()))
        self.assertIsNone(auth.user_by_email("weak@pine.example"))
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "sam.oak@pine.example", "password": "samoak2026!",
                                                             "name": "Sam Oak", "company_name": "Oak"})
        self.assertEqual(res.status, 400)
        self.assertIn("email address or name", data["error"])
        cookie, _ = self.signup("strong@pine.example")
        res, data = self.request("POST", "/api/auth/password", {"current": PASSWORD, "new": "letmein2026"}, cookie=cookie)
        self.assertIn("most common", data.get("error", ""))

    def test_the_rules(self):
        self.assertTrue(passwords.problem("abababababab"))
        self.assertTrue(passwords.problem("zxcvbnm123"))
        self.assertTrue(passwords.problem("Welcome!!2025"))
        self.assertEqual(passwords.problem("correct horse battery"), "")
        self.assertEqual(passwords.problem("pine roof ladder 7"), "")
        self.assertGreater(len(passwords.common()), 250)


class UniformSignupTests(ServerCase):
    def test_signup_answers_the_same_for_a_new_and_a_registered_email(self):
        self.mail_on()
        first, _ = self.signup("taken@pine.example")       # follows the link: signed in
        self.assertTrue(first.startswith("duracash_session="))
        self.sink.messages.clear()
        body = {"accept_terms": True, "password": PASSWORD, "name": "Someone", "company_name": "X"}
        res_new, new = self.request("POST", "/api/auth/signup", {**body, "email": "fresh@pine.example"})
        res_old, old = self.request("POST", "/api/auth/signup", {**body, "email": "taken@pine.example"})
        for res in (res_new, res_old):
            self.assertEqual(res.status, 200)
            self.assertIsNone(res.getheader("Set-Cookie"), "nobody is signed in before the link is opened")
        self.assertEqual(new["message"].replace("fresh@", "X@"), old["message"].replace("taken@", "X@"))
        self.assertEqual(set(new), set(old))
        self.assertNotIn("exists", old)
        # The registered address is told it already has an account; the new one gets its link.
        self.assertIsNotNone(self.mail_to("taken@pine.example", "already has one"))
        self.assertIsNotNone(self.mail_to("fresh@pine.example", "sign in to your new account"))
        users = json.loads(auth.AUTH_PATH.read_text())["users"]
        self.assertEqual(sum(1 for u in users if u["email"] == "taken@pine.example"), 1)

    def test_the_sign_up_link_signs_in_once_and_a_resent_link_only_confirms(self):
        self.mail_on()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "once@pine.example", "password": PASSWORD,
                                                          "name": "Pat", "company_name": "Once"})
        token = self.verify_token_for("once@pine.example")
        res, data = self.request("POST", "/api/auth/verify", {"token": token})
        self.assertTrue(data["signed_in"])
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, _ = self.request("POST", "/api/auth/verify", {"token": token})
        self.assertEqual(res.status, 400, "used once")
        user = auth.user_by_email("once@pine.example")
        raw = auth.verify_email_token(user["id"])          # Send the link again, from My account
        res, data = self.request("POST", "/api/auth/verify", {"token": raw})
        self.assertEqual(res.status, 200)
        self.assertNotIn("signed_in", data)
        self.assertIsNone(res.getheader("Set-Cookie"))
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=cookie)[0].status, 200)

    def test_without_mail_sign_up_still_signs_in_at_once(self):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "local@pine.example", "password": PASSWORD,
                                                             "name": "Pat", "company_name": "Local"})
        self.assertEqual(res.status, 200)
        self.assertIn("user", data)
        self.assertTrue(res.getheader("Set-Cookie"))


class SessionListTests(ServerCase):
    def test_sessions_are_listed_and_the_others_can_be_signed_out(self):
        here, data = self.signup("many@pine.example")
        user = data["user"]
        phone = "duracash_session=" + auth.create_session(user, SAFARI_IPHONE)
        laptop = "duracash_session=" + auth.create_session(user, CHROME_MAC)
        res, listing = self.request("GET", "/api/auth/sessions", cookie=laptop)
        rows = listing["sessions"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0]["current"])
        self.assertEqual(rows[0]["browser"], "Chrome on Mac")
        self.assertIn("Safari on iPhone", [r["browser"] for r in rows])
        self.assertTrue(all(len(r["id"]) == 16 for r in rows))
        stored = json.loads(auth.AUTH_PATH.read_text())["sessions"]
        self.assertFalse(any(r["id"] in stored for r in rows), "the list never shows a stored session key")
        self.assertNotIn(SAFARI_IPHONE, auth.AUTH_PATH.read_text(), "only the summary is kept")
        # One other session ends, then all the others; this one stays.
        phone_id = next(r["id"] for r in rows if r["browser"] == "Safari on iPhone")
        res, out = self.request("POST", "/api/auth/sessions/end", {"id": phone_id}, cookie=laptop)
        self.assertEqual(out["ended"], 1)
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=phone)[0].status, 401)
        res, out = self.request("POST", "/api/auth/sessions/end", {}, cookie=laptop)
        self.assertEqual(out["ended"], 1)
        self.assertEqual([r["current"] for r in out["sessions"]], [True])
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=here)[0].status, 401)
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=laptop)[0].status, 200)

    def test_browser_words(self):
        self.assertEqual(auth.browser_words(CHROME_MAC), "Chrome on Mac")
        self.assertEqual(auth.browser_words(SAFARI_IPHONE), "Safari on iPhone")
        self.assertEqual(auth.browser_words(""), "Another browser or app")


class LinkExpiryTests(ServerCase):
    def test_links_expire_after_the_set_days_and_sending_again_renews(self):
        old = (datetime.now(timezone.utc) - timedelta(days=61)).isoformat(timespec="seconds")
        recent = (datetime.now(timezone.utc) - timedelta(days=59)).isoformat(timespec="seconds")
        self.assertTrue(payer_page.link_expired({"payer_link_at": old}, "invoice"))
        self.assertFalse(payer_page.link_expired({"payer_link_at": recent}, "invoice"))
        self.assertTrue(payer_page.link_expired({"sent_at": old}, "invoice"), "an older link counts from when it was sent")
        self.assertTrue(payer_page.link_expired({"signature_requested_at": old}, "co"))
        self.assertFalse(payer_page.link_expired({}, "co"))
        with mock.patch.dict(os.environ, {"DURACASH_LINK_DAYS": "90"}):
            self.assertFalse(payer_page.link_expired({"sign_link_at": old}, "co"))
        invoice = {"payer_link_at": old}
        domain.payer_link(invoice, "https://x.test", "co-1")
        self.assertFalse(payer_page.link_expired(invoice, "invoice"), "a new email with the link renews it")

    def test_an_expired_customer_page_says_so(self):
        cookie, data = self.signup("links@pine.example")
        company_id = data["user"]["company_id"]
        with server.company_lock(company_id):
            st = server.load_company(company_id)
            st["company"]["name"] = "Pine Links"
            st["jobs"] = [{"id": "job-1", "name": "Oak St", "invoices": [{"id": "inv-1", "number": "PA-1", "amount_cents": 100, "status": "sent",
                                                                       "payer_token": "T" * 32, "payer_link_at": "2020-01-01T00:00:00+00:00"}],
                           "change_orders": [{"id": "co-1", "sign_token": "S" * 32, "sign_link_at": "2020-01-01T00:00:00+00:00"}]}]
            server.save_company(company_id, st)
        for path in (f"/p/{company_id}." + "T" * 32, f"/s/{company_id}." + "S" * 32):
            res, page = self.request("GET", path)
            self.assertEqual(res.status, 410, path)
            self.assertIn(b"This link has expired", page)
            self.assertIn(b"Pine Links", page)
        res, _ = self.request("POST", f"/p/{company_id}." + "T" * 32 + "/received", raw=b"name=Al", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(res.status, 410)
        self.assertEqual(self.request("GET", f"/p/{company_id}." + "N" * 32)[0].status, 404)


class HelperLogTests(ServerCase):
    def test_the_log_drops_old_entries_and_stays_under_its_size(self):
        now = datetime(2026, 11, 27, tzinfo=timezone.utc)
        rows = [{"id": "old", "at": "2025-01-01T00:00:00+00:00"}, {"id": "new", "at": "2026-11-01T00:00:00+00:00"}]
        self.assertEqual([e["id"] for e in helper.trim_log(rows, now)], ["new"])
        big = [{"id": str(n), "at": "2026-11-01T00:00:00+00:00", "question": "x" * 1000} for n in range(200)]
        with mock.patch.object(helper, "LOG_MAX_BYTES", 50_000):
            kept = helper.trim_log(big, now)
        self.assertLess(len(json.dumps(kept)), 50_000)
        self.assertEqual(kept[-1]["id"], "199", "the newest are kept")


class ExportWithFilesTests(ServerCase):
    def test_the_export_with_files_holds_every_uploaded_file(self):
        cookie, data = self.signup("export@pine.example")
        for name, body in (("site.jpg", b"\xff\xd8 photo"), ("certified payroll.pdf", b"%PDF payroll")):
            res, _ = self.request("POST", "/api/import/file", raw=body, cookie=cookie,
                                  headers={"Content-Type": "application/octet-stream", "X-Filename": name})
            self.assertEqual(res.status, 200)
        res, raw = self.request("GET", "/api/export/with-files.zip", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader("Content-Type"), "application/zip")
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = zf.namelist()
        self.assertIn("company.json", names)
        company = json.loads(zf.read("company.json"))
        for meta in company["files"]:
            entry = next(n for n in names if n.startswith(f"files/{meta['id']}-"))
            self.assertEqual(len(zf.read(entry)), meta["size"])
        self.assertTrue(any(n.endswith("certified payroll.pdf") for n in names))
        self.assertNotIn("payer_token", zf.read("company.json").decode())
