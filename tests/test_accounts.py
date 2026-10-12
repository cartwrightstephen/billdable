"""Accounts and operations: password reset, email verification, team and roles, account deletion,
health check, logs, request ids, the pay-app PDF, trials, onboarding and backups."""

import email
import email.policy
import http.client
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import backup
import helper
import importer
import mailer
import server
import storage
import trial
from test_mail import Sink

PASSWORD = "correct horse battery"
ENV = ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "MAIL_FROM", "BILLDABLE_URL", "HELPER_ADMIN_USER_IDS", "BACKUP_KEEP")


class AccountHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH)
        auth.AUTH_PATH = root / "auth.json"
        server.COMPANIES = root / "companies"
        importer.UPLOADS = root / "uploads"
        storage.DATA_DIR = root
        helper.LOCAL_KB = root / "helper_answers.json"
        helper.LOG_PATH = root / "helper_log.json"
        server.ensure_demo()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.sink = Sink()
        threading.Thread(target=cls.sink.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.sink.shutdown()
        cls.sink.server_close()
        (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH) = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        self.sink.messages.clear()
        auth._RESETS.clear()

    def tearDown(self):
        for key in ENV:
            os.environ.pop(key, None)

    def mail_on(self):
        os.environ.update({"SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(self.sink.server_address[1]), "SMTP_SECURITY": "none",
                           "MAIL_FROM": "billing@billdable.test", "BILLDABLE_URL": "https://app.billdable.test"})

    def request(self, method, path, body=None, cookie=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
            hdrs["Content-Length"] = str(len(data))
        if cookie:
            hdrs["Cookie"] = cookie
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        try:
            return res, json.loads(payload or b"{}")
        except ValueError:
            return res, payload

    def signup(self, email_addr, company="Pine & Sons"):
        res, data = self.request("POST", "/api/auth/signup", {"email": email_addr, "password": PASSWORD, "name": "Pat Pine", "company_name": company})
        self.assertEqual(res.status, 200, data)
        return res.getheader("Set-Cookie").split(";")[0], data

    def login(self, email_addr, password=PASSWORD):
        res, data = self.request("POST", "/api/auth/login", {"email": email_addr, "password": password})
        return res, (res.getheader("Set-Cookie") or "").split(";")[0]

    def link_token(self, kind, message=None):
        msg = message or self.sink.messages[-1]
        body = email.message_from_bytes(msg["data"], policy=email.policy.default).get_body(("plain",)).get_content()
        m = re.search(rf"(https?://\S+?)/#{kind}=([A-Za-z0-9_-]+)", body)
        self.assertIsNotNone(m, body)
        return m.group(1), m.group(2)

    def wait_for_mail(self, count):
        for _ in range(50):
            if len(self.sink.messages) >= count:
                return
            time.sleep(0.05)

    # ----- password reset -----

    def test_forgot_without_mail_says_so(self):
        self.signup("nomail@pine.example")
        res, data = self.request("POST", "/api/auth/forgot", {"email": "nomail@pine.example"})
        self.assertEqual(res.status, 200)
        self.assertIn("isn't set up", data["message"])
        self.assertEqual(self.sink.messages, [])

    def test_password_reset_end_to_end(self):
        self.mail_on()
        old_cookie, _ = self.signup("reset@pine.example")
        self.wait_for_mail(1)  # the verification email
        self.sink.messages.clear()
        # The link is built from BILLDABLE_URL, never from the Host header a stranger controls.
        res, data = self.request("POST", "/api/auth/forgot", {"email": "Reset@Pine.example"}, headers={"Host": "evil.example"})
        same, data2 = self.request("POST", "/api/auth/forgot", {"email": "nobody@pine.example"})
        self.assertEqual(data["message"], data2["message"])  # no hint which emails exist
        self.wait_for_mail(1)
        self.assertEqual(len(self.sink.messages), 1)
        base, token = self.link_token("reset")
        self.assertEqual(base, "https://app.billdable.test")
        res, data = self.request("POST", "/api/auth/reset", {"token": token, "password": "short"})
        self.assertEqual(res.status, 400)
        res, data = self.request("POST", "/api/auth/reset", {"token": token, "password": "a new long password"})
        self.assertEqual(res.status, 200, data)
        self.assertTrue(data["user"]["email_verified"])
        res, _ = self.request("GET", "/api/state", cookie=old_cookie)
        self.assertEqual(res.status, 401)  # other sessions are signed out
        res, _ = self.login("reset@pine.example", "a new long password")
        self.assertEqual(res.status, 200)
        res, _ = self.request("POST", "/api/auth/reset", {"token": token, "password": "another long password"})
        self.assertEqual(res.status, 400)  # single use

    def test_reset_requests_are_limited(self):
        self.mail_on()
        self.signup("flood@pine.example")
        self.wait_for_mail(1)
        self.sink.messages.clear()
        for _ in range(5):
            self.request("POST", "/api/auth/forgot", {"email": "flood@pine.example"})
        time.sleep(0.5)
        self.assertEqual(len(self.sink.messages), auth.RESET_MAX_PER_HOUR)

    # ----- email verification -----

    def test_verification_and_the_send_rule(self):
        self.mail_on()
        cookie, data = self.signup("verify@pine.example")
        self.assertEqual(data["verification"], "sent")
        self.wait_for_mail(1)
        _, token = self.link_token("verify")
        _, me = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertFalse(me["user"]["email_verified"])
        self.assertTrue(me["mail_ready"])
        # Unverified: an approved email is held, not sent.
        job = {"name": "Elm", "customer": "Park GC", "address": "9 Elm", "approver": "Kim", "ap_email": "ap@park.example",
               "contract_cents": 1_000_000, "retainage_pct": 0, "role": "Owner"}
        _, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        job_id = state["jobs"][0]["id"]
        self.request("POST", f"/api/jobs/{job_id}/progress", {"completed_cents": 500_000}, cookie=cookie)
        _, state = self.request("POST", f"/api/jobs/{job_id}/pencil", {}, cookie=cookie)
        self.assertEqual(state["email_log"][-1]["status"], mailer.UNVERIFIED)
        before = len(self.sink.messages)
        res, data = self.request("POST", "/api/auth/verify", {"token": token})
        self.assertEqual(res.status, 200, data)
        self.assertTrue(data["user"]["email_verified"])
        res, state = self.request("POST", f"/api/outbox/{state['email_log'][-1]['id']}/retry", {}, cookie=cookie)
        self.assertEqual(state["email_log"][-1]["status"], "sent")
        self.assertEqual(len(self.sink.messages), before + 1)
        res, _ = self.request("POST", "/api/auth/resend-verify", {}, cookie=cookie)
        self.assertEqual(res.status, 409)  # already verified
        res, _ = self.request("POST", "/api/auth/verify", {"token": token})
        self.assertEqual(res.status, 400)  # single use

    def test_change_password(self):
        cookie, _ = self.signup("change@pine.example")
        res, _ = self.request("POST", "/api/auth/password", {"current": "wrong one here", "new": "brand new password"}, cookie=cookie)
        self.assertEqual(res.status, 400)
        res, _ = self.request("POST", "/api/auth/password", {"current": PASSWORD, "new": "brand new password"}, cookie=cookie)
        self.assertEqual(res.status, 200)
        res, _ = self.login("change@pine.example", "brand new password")
        self.assertEqual(res.status, 200)

    # ----- team -----

    def test_team_invites_roles_and_removal(self):
        owner, data = self.signup("owner@team.example", "Team Co")
        res, out = self.request("POST", "/api/team/invite", {"email": "member@team.example", "role": "member"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertFalse(out["emailed"])  # no mail on this server: the owner gets the link to pass on
        token = out["invite_link"].split("#invite=")[1]
        res, info = self.request("POST", "/api/auth/invite-info", {"token": token})
        self.assertEqual((info["email"], info["company_name"], info["role"]), ("member@team.example", "Team Co", "member"))
        res, joined = self.request("POST", "/api/auth/accept-invite", {"token": token, "name": "Mo Member", "password": "member password 1"})
        self.assertEqual(res.status, 200, joined)
        member = res.getheader("Set-Cookie").split(";")[0]
        self.assertEqual(joined["user"]["company_id"], data["user"]["company_id"])
        _, state = self.request("GET", "/api/state", cookie=member)
        self.assertEqual(state["company"]["name"], "Team Co")
        self.assertTrue(any(a["action"] == "team_join" for a in state["audit"]))
        res, _ = self.request("POST", "/api/auth/accept-invite", {"token": token, "name": "Again", "password": "member password 1"})
        self.assertEqual(res.status, 400)
        # Members can't manage the team, reset the company or rotate addresses.
        for path, body in (("/api/team/invite", {"email": "x@team.example"}), ("/api/reset", {"confirm": "Team Co"}),
                           ("/api/import/rotate", {"kind": "email"}), ("/api/account/delete", {"password": "member password 1", "confirm": "Team Co"})):
            res, _ = self.request("POST", path, body, cookie=member)
            self.assertEqual(res.status, 403, path)
        _, team = self.request("GET", "/api/team", cookie=member)
        self.assertEqual(team["invites"], [])
        self.assertEqual(len(team["users"]), 2)
        # The last owner can't step down; promoting someone first makes it possible.
        res, _ = self.request("POST", f"/api/team/{data['user']['id']}/role", {"role": "member"}, cookie=owner)
        self.assertEqual(res.status, 409)
        res, _ = self.request("POST", f"/api/team/{joined['user']['id']}/role", {"role": "owner"}, cookie=owner)
        self.assertEqual(res.status, 200)
        res, _ = self.request("POST", f"/api/team/{joined['user']['id']}/role", {"role": "member"}, cookie=owner)
        res, _ = self.request("POST", f"/api/team/{data['user']['id']}/remove", {}, cookie=owner)
        self.assertEqual(res.status, 409)  # not yourself
        res, _ = self.request("POST", f"/api/team/{joined['user']['id']}/remove", {}, cookie=owner)
        self.assertEqual(res.status, 200)
        res, _ = self.request("GET", "/api/state", cookie=member)
        self.assertEqual(res.status, 401)  # removed: signed out at once
        # Another company's owner can't touch this team.
        other, _ = self.signup("other@team.example", "Other Co")
        res, _ = self.request("POST", f"/api/team/{data['user']['id']}/remove", {}, cookie=other)
        self.assertEqual(res.status, 409)
        # Invites can be revoked; an existing account can't be invited.
        _, out = self.request("POST", "/api/team/invite", {"email": "late@team.example"}, cookie=owner)
        _, team = self.request("GET", "/api/team", cookie=owner)
        res, _ = self.request("POST", f"/api/team/invites/{team['invites'][0]['id']}/revoke", {}, cookie=owner)
        self.assertEqual(res.status, 200)
        res, _ = self.request("POST", "/api/auth/invite-info", {"token": out["invite_link"].split("#invite=")[1]})
        self.assertEqual(res.status, 404)
        res, _ = self.request("POST", "/api/team/invite", {"email": "other@team.example"}, cookie=owner)
        self.assertEqual(res.status, 409)

    def test_invite_is_emailed_when_mail_is_ready(self):
        self.mail_on()
        owner, _ = self.signup("owner2@team.example", "Mail Team")
        self.wait_for_mail(1)
        self.sink.messages.clear()
        _, out = self.request("POST", "/api/team/invite", {"email": "new@team.example", "role": "owner"}, cookie=owner)
        self.assertTrue(out["emailed"])
        base, token = self.link_token("invite")
        self.assertEqual(base, "https://app.billdable.test")
        self.assertEqual(self.sink.messages[-1]["to"], ["new@team.example"])

    # ----- deleting the company -----

    def test_delete_company(self):
        cookie, data = self.signup("delete@pine.example", "Gone Co")
        company_id = data["user"]["company_id"]
        res, _ = self.request("POST", "/api/import/file", raw=b"%PDF-1.4 x", cookie=cookie,
                              headers={"Content-Type": "application/pdf", "X-Filename": "a.pdf"})
        self.request("GET", "/api/import/settings", cookie=cookie)  # creates inbound tokens
        tokens = importer.company_tokens(company_id, create=False)
        self.assertTrue(tokens)
        res, _ = self.request("POST", "/api/account/delete", {"password": "wrong password!", "confirm": "Gone Co"}, cookie=cookie)
        self.assertEqual(res.status, 409)
        res, _ = self.request("POST", "/api/account/delete", {"password": PASSWORD, "confirm": "Gone"}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertTrue(server.company_path(company_id).exists())
        res, data = self.request("POST", "/api/account/delete", {"password": PASSWORD, "confirm": "Gone Co"}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        self.assertFalse(server.company_path(company_id).exists())
        self.assertFalse((importer.UPLOADS / company_id).exists())
        self.assertEqual(importer.company_tokens(company_id, create=False), {})
        res, _ = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(res.status, 401)
        res, _ = self.login("delete@pine.example")
        self.assertEqual(res.status, 401)
        res, _ = self.request("GET", f"/api/inbound/email/{tokens['email']}")  # the old address is dead
        res, _ = self.request("POST", f"/api/inbound/email/{tokens['email']}", raw=b"Subject: hi\r\n\r\nhello",
                              headers={"Content-Type": "message/rfc822"})
        self.assertEqual(res.status, 404)

    def test_demo_account_cannot_be_deleted_or_teamed(self):
        res, cookie = self.login(auth.DEMO_EMAIL, auth.DEMO_PASSWORD)
        res, _ = self.request("POST", "/api/account/delete", {"password": auth.DEMO_PASSWORD, "confirm": "Hartline Remodeling"}, cookie=cookie)
        self.assertEqual(res.status, 403)
        res, _ = self.request("POST", "/api/team/invite", {"email": "x@y.example"}, cookie=cookie)
        self.assertEqual(res.status, 403)
        res, data = self.request("POST", "/api/auth/forgot", {"email": auth.DEMO_EMAIL})
        self.assertEqual(res.status, 200)

    # ----- operations -----

    def test_health_check(self):
        res, data = self.request("GET", "/healthz")
        self.assertEqual(res.status, 200)
        self.assertEqual(set(data), {"ok", "storage", "email", "last_backup"})
        self.assertTrue(data["ok"])
        self.assertTrue(res.getheader("X-Request-Id"))

    def test_crash_gives_a_request_id(self):
        cookie, _ = self.signup("crash@pine.example")
        original = helper.answer
        helper.answer = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            res, data = self.request("POST", "/api/helper/ask", {"q": "hello"}, cookie=cookie)
        finally:
            helper.answer = original
        self.assertEqual(res.status, 500)
        self.assertEqual(data["request_id"], res.getheader("X-Request-Id"))

    def test_log_lines_hide_secrets(self):
        self.assertEqual(server.redact("/api/inbound/email/abcDEF123_-xyz"), "/api/inbound/email/[token]")
        self.assertEqual(server.redact("/api/calendar/abc123.ics"), "/api/calendar/[token].ics")
        self.assertEqual(server.redact('"GET /api/files/f1?x=secret HTTP/1.1"'), '"GET /api/files/f1 HTTP/1.1"')

    def test_pay_app_pdf_download(self):
        cookie, _ = self.signup("pdf@pine.example")
        job = {"name": "Elm", "customer": "Park GC", "address": "9 Elm", "approver": "Kim", "ap_email": "ap@park.example",
               "contract_cents": 1_000_000, "retainage_pct": 10, "role": "Owner"}
        _, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        job_id = state["jobs"][0]["id"]
        _, state = self.request("POST", f"/api/jobs/{job_id}/progress", {"completed_cents": 400_000}, cookie=cookie)
        inv = state["jobs"][0]["invoices"][-1]
        res, body = self.request("GET", f"/api/jobs/{job_id}/payapp/{inv['id']}.pdf", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader("Content-Type"), "application/pdf")
        self.assertTrue(body.startswith(b"%PDF-1.4"))
        other, _ = self.signup("pdf-other@pine.example")
        res, _ = self.request("GET", f"/api/jobs/{job_id}/payapp/{inv['id']}.pdf", cookie=other)
        self.assertEqual(res.status, 404)

    def test_trial_onboarding_and_founders_overview(self):
        cookie, data = self.signup("trial@pine.example", "Trial Co")
        _, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(state["trial"]["state"], "active")
        ends = datetime.fromisoformat(state["trial"]["ends_by"])
        self.assertAlmostEqual((ends - datetime.now(timezone.utc)).days, 89, delta=1)
        self.assertFalse(state["company"].get("onboarding_dismissed", False))
        _, state = self.request("POST", "/api/onboarding/dismiss", {}, cookie=cookie)
        self.assertTrue(state["company"]["onboarding_dismissed"])
        res, _ = self.request("GET", "/api/admin/companies", cookie=cookie)
        self.assertEqual(res.status, 403)
        os.environ["HELPER_ADMIN_USER_IDS"] = data["user"]["id"]
        res, out = self.request("GET", "/api/admin/companies", cookie=cookie)
        self.assertEqual(res.status, 200)
        mine = next(c for c in out["companies"] if c["company_id"] == data["user"]["company_id"])
        self.assertEqual((mine["name"], mine["users"], mine["trial"]["state"]), ("Trial Co", 1, "active"))


class TrialRuleTests(unittest.TestCase):
    def state(self, started, invoices=(), payments=()):
        return {"company": {"trial": {"started_at": started.isoformat()}},
                "jobs": [{"name": "Elm", "invoices": list(invoices), "payments": list(payments)}]}

    def test_paid_pay_app_ends_the_trial(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        inv = {"id": "i1", "number": "PA-1", "status": "paid", "sent_at": "2026-01-10T00:00:00+00:00"}
        pay = {"invoice_id": "i1", "matched_at": "2026-02-20T00:00:00+00:00"}
        out = trial.status(self.state(start, [inv], [pay]), now=start + timedelta(days=60))
        self.assertEqual((out["state"], out["reason"], out["ended_at"][:10], out["first_paid"]), ("ended", "paid", "2026-02-20", "PA-1"))

    def test_ninety_days_then_extension_for_an_unpaid_pay_app(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(trial.status(self.state(start), now=start + timedelta(days=30))["state"], "active")
        out = trial.status(self.state(start), now=start + timedelta(days=91))
        self.assertEqual((out["state"], out["reason"]), ("ended", "time"))
        sent = {"id": "i1", "number": "PA-1", "status": "sent", "sent_at": "2026-03-01T00:00:00+00:00"}
        out = trial.status(self.state(start, [sent]), now=start + timedelta(days=100))
        self.assertEqual((out["state"], out["extended"]), ("active", True))
        out = trial.status(self.state(start, [sent]), now=start + timedelta(days=121))
        self.assertEqual((out["state"], out["ended_at"][:10]), ("ended", "2026-05-01"))

    def test_untracked_company(self):
        self.assertEqual(trial.status({"company": {}, "jobs": []}), {"tracked": False})


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = storage.DATA_DIR
        storage.DATA_DIR = Path(self.tmp.name)
        root = storage.DATA_DIR
        (root / "companies").mkdir()
        (root / "companies" / "co-1.json").write_text('{"company": {"name": "One"}}')
        (root / "auth.json").write_text('{"users": []}')
        (root / "uploads" / "co-1").mkdir(parents=True)
        (root / "uploads" / "co-1" / "file-1.pdf").write_bytes(b"%PDF")

    def tearDown(self):
        storage.DATA_DIR = self._orig
        os.environ.pop("BACKUP_KEEP", None)
        self.tmp.cleanup()

    def test_backup_rotate_restore(self):
        os.environ["BACKUP_KEEP"] = "2"
        lock = threading.Lock()
        made = [backup.make_backup({"companies": lock, "auth": lock}) for _ in range(3)]
        self.assertEqual(len(backup.backups()), 2)  # oldest rotated away
        self.assertFalse(made[0].exists())
        with tarfile.open(made[-1]) as tar:
            names = sorted(tar.getnames())
        self.assertEqual(names, ["auth.json", "companies/co-1.json", "uploads/co-1/file-1.pdf"])
        self.assertIsNotNone(backup.newest_time())
        # Change the data, then restore: the old data comes back and the changed data is kept aside.
        (storage.DATA_DIR / "companies" / "co-1.json").write_text('{"company": {"name": "Changed"}}')
        aside = backup.restore(made[-1])
        self.assertIn("One", (storage.DATA_DIR / "companies" / "co-1.json").read_text())
        self.assertIn("Changed", (aside / "companies" / "co-1.json").read_text())
        self.assertTrue((storage.DATA_DIR / "uploads" / "co-1" / "file-1.pdf").exists())

    def test_restore_refuses_unsafe_archives(self):
        bad = storage.DATA_DIR / "bad.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            info = tarfile.TarInfo("../outside.txt")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(ValueError):
            backup.restore(bad)
        self.assertFalse((storage.DATA_DIR.parent / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
