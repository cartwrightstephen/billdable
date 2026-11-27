"""The security review of Nov 26 (findings H1, H2, H4, H6, H7, M1, M2, M3, M9, M10, M11, L1 and
part of L4): what each fix promises, through the server where it can be."""

import base64
import hashlib
import hmac
import http.client
import io
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import admin
import alerts
import auth
import backup
import domain
import helper
import importer
import migrations
import qbo
import server
import storage
from domain import profiles
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hartline_demo import hartline_demo

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery"
GC = {"address": "9 Elm Street", "contract_cents": 2_000_000, "retainage_pct": 0, "role": "GC", "client_type": "gc",
      "approver": "Kim", "ap_email": "ap@park.example", "phone": "(404) 555-0100"}


@hartline_demo
class SecurityHttpTests(unittest.TestCase):
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
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = {"Content-Type": "application/json"} if body is not None else {}
        hdrs.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
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

    def signin(self, email):
        res, data = self.request("POST", "/api/auth/login", {"email": email, "password": PASSWORD})
        self.assertEqual(res.status, 200, data)
        return res.getheader("Set-Cookie").split(";")[0]

    def setUp(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        type(self).n += 1
        n = self.n
        email = f"sec{n}@example.com"
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": PASSWORD,
                                                             "name": "Ann Owner", "company_name": f"Pine {n}"})
        self.assertEqual(res.status, 200, data)
        self.owner = res.getheader("Set-Cookie").split(";")[0]
        self.company = data["user"]["company_id"]
        auth.create_user(f"field{n}@example.com", PASSWORD, "Bob Field", self.company, role="member")
        self.member = self.signin(f"field{n}@example.com")
        res, snap = self.request("POST", "/api/jobs", {**GC, "name": "Elm addition", "customer": "Park GC"}, cookie=self.owner)
        self.assertEqual(res.status, 200, snap)
        self.job_id = snap["jobs"][0]["id"]

    def add_file(self, kind, name, ticket_type=""):
        meta = importer.store_bytes(self.company, name, b"%PDF-1.4 test" if name.endswith(".pdf") else b"\xff\xd8\xff photo", "Ann")
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            importer.register_file(state, meta)
            rec = domain.add_job_record(state, self.job_id, name, kind, "", "Ann")["records"][-1]
            rec["file_ids"] = [meta["id"]]
            if ticket_type:
                rec["ticket_type"] = ticket_type
            server.save_company(self.company, state)
        return meta["id"]

    # ----- H2: payroll and sensitive documents -----

    def test_payroll_waivers_contracts_and_time_sheets_take_the_permission(self):
        payroll = self.add_file("payroll", "wh-347.pdf")
        sheet = self.add_file("ticket", "week-12.pdf", ticket_type="time_sheet")
        waiver = self.add_file("waiver", "waiver.pdf")
        photo = self.add_file("photo", "wall.jpg")
        for fid in (payroll, sheet, waiver):
            res, data = self.request("GET", f"/api/files/{fid}", cookie=self.member)
            self.assertEqual(res.status, 403, fid)
            self.assertIn("See payroll and sensitive documents", data["error"])
            res, _ = self.request("GET", f"/api/files/{fid}", cookie=self.owner)
            self.assertEqual(res.status, 200, "an owner holds every permission")
        res, _ = self.request("GET", f"/api/files/{photo}", cookie=self.member)
        self.assertEqual(res.status, 200, "a photo stays open to the team")
        # The job file zip holds them all.
        res, _ = self.request("GET", f"/api/jobs/{self.job_id}/archive/job-file.zip", cookie=self.member)
        self.assertEqual(res.status, 403)
        res, _ = self.request("GET", f"/api/jobs/{self.job_id}/archive/job-file.zip", cookie=self.owner)
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "application/zip"))
        # The page is told which files to show without a link.
        _, state = self.request("GET", "/api/state", cookie=self.member)
        marked = {f["id"] for f in state["files"] if f.get("sensitive")}
        self.assertEqual(marked, {payroll, sheet, waiver})
        _, me = self.request("GET", "/api/auth/me", cookie=self.member)
        self.assertNotIn("sensitive_docs", me["user"]["perms"])
        _, me = self.request("GET", "/api/auth/me", cookie=self.owner)
        self.assertIn("sensitive_docs", me["user"]["perms"])

    def test_a_profile_given_the_permission_opens_them(self):
        payroll = self.add_file("payroll", "wh-347.pdf")
        res, saved = self.request("POST", "/api/team/profiles", {"name": "Office", "perms": ["send_payapps", "sensitive_docs"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, saved)
        office = next(p for p in saved["profiles"] if p["name"] == "Office") if "profiles" in saved else saved["profile"]
        user = auth.user_by_email(f"field{self.n}@example.com")
        res, out = self.request("POST", f"/api/team/{user['id']}/role", {"profile_id": office["id"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        member = self.signin(f"field{self.n}@example.com")
        res, _ = self.request("GET", f"/api/files/{payroll}", cookie=member)
        self.assertEqual(res.status, 200)
        _, team = self.request("GET", "/api/team", cookie=self.owner)
        self.assertIn({"key": "sensitive_docs", "words": "See payroll and sensitive documents"}, team["permissions"])

    # ----- L1: routes their neighbours' permission -----

    def test_the_export_with_files_leaves_sensitive_files_out_without_the_permission(self):
        # sec2b's export with files (L4) is gated by "data"; its payroll and other sensitive files
        # also take "See payroll and sensitive documents" (H2), like /api/files/<id>.
        import zipfile
        payroll = self.add_file("payroll", "wh-347.pdf")
        sheet = self.add_file("ticket", "week-12.pdf", ticket_type="time_sheet")
        photo = self.add_file("photo", "wall.jpg")
        res, saved = self.request("POST", "/api/team/profiles", {"name": "Bookkeeper", "perms": ["data"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, saved)
        keeper = next(p for p in saved["profiles"] if p["name"] == "Bookkeeper") if "profiles" in saved else saved["profile"]
        user = auth.user_by_email(f"field{self.n}@example.com")
        res, out = self.request("POST", f"/api/team/{user['id']}/role", {"profile_id": keeper["id"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        member = self.signin(f"field{self.n}@example.com")

        def names(cookie):
            res, raw = self.request("GET", "/api/export/with-files.zip", cookie=cookie)
            self.assertEqual(res.status, 200)
            zf = zipfile.ZipFile(io.BytesIO(raw))
            return zf, zf.namelist()

        def has(got, fid):
            return any(n.startswith(f"files/{fid}-") for n in got)

        zf, got = names(member)
        self.assertTrue(has(got, photo))
        self.assertFalse(has(got, payroll))
        self.assertFalse(has(got, sheet))
        listed = zf.read("left-out-files.txt").decode()
        self.assertIn(payroll, listed)
        self.assertIn("See payroll and sensitive documents", listed)
        _, got = names(self.owner)
        self.assertTrue(all(has(got, f) for f in (photo, payroll, sheet)), "an owner holds every permission")
        self.assertNotIn("left-out-files.txt", got)

    def test_metrics_reconcile_audit_and_retry_take_their_permission(self):
        for path in ("/api/metrics", "/api/qbo/reconcile", "/api/audit"):
            res, data = self.request("GET", path, cookie=self.member)
            self.assertEqual(res.status, 403, path)
        res, _ = self.request("GET", "/api/metrics", cookie=self.owner)
        self.assertEqual(res.status, 200)
        res, _ = self.request("GET", "/api/audit", cookie=self.owner)
        self.assertEqual(res.status, 200)
        res, _ = self.request("GET", "/api/outbox", cookie=self.member)
        self.assertEqual(res.status, 200, "the Outbox itself stays open")
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            state.setdefault("email_log", []).append({"id": "eml-paypay", "kind": "pay_app", "job_id": self.job_id, "status": "failed: test", "at": "2026-01-01T00:00:00+00:00"})
            state["email_log"].append({"id": "eml-follow", "kind": "follow_up", "job_id": self.job_id, "status": "sent", "at": "2026-01-01T00:00:00+00:00"})
            server.save_company(self.company, state)
        res, data = self.request("POST", "/api/outbox/eml-paypay/retry", {}, cookie=self.member)
        self.assertEqual(res.status, 403, "sending a pay app again takes Send pay apps")
        res, data = self.request("POST", "/api/outbox/eml-follow/retry", {}, cookie=self.member)
        self.assertEqual(res.status, 409, "a follow-up is anyone's; this one was sent, so it is refused for that")
        # Integration (Nov 27): Resend to this recipient (delivery) takes the same permission, by the
        # same helper (profiles.may_send_again), and says so in the same words.
        res, again = self.request("POST", "/api/outbox/eml-paypay/resend", {"email": "ap@park.example"}, cookie=self.member)
        self.assertEqual(res.status, 403, again)
        res, data = self.request("POST", "/api/outbox/eml-paypay/retry", {}, cookie=self.member)
        self.assertEqual(data, again, "one refusal for both buttons")
        res, data = self.request("POST", "/api/outbox/eml-follow/resend", {"email": "nobody@else.example"}, cookie=self.member)
        self.assertEqual(res.status, 409, "a follow-up is anyone's to resend; that address isn't on it")
        member = auth.session_public(auth.user_by_email(f"field{self.n}@example.com")["id"]) or auth.user_by_email(f"field{self.n}@example.com")
        state = server.load_company(self.company)
        self.assertEqual([profiles.may_send_again(member, {"kind": k}, state) for k in ("pay_app", "waiver", "retainage", "pay_app_withdrawn", "follow_up", "")],
                         [False, False, False, False, True, True])

    # ----- M1: cross-site posts -----

    def test_posts_from_another_site_are_refused(self):
        host = f"127.0.0.1:{self.port}"
        evil = {"Origin": "https://evil.example"}
        for path, body in (("/api/auth/login", {"email": f"sec{self.n}@example.com", "password": PASSWORD}), ("/api/auth/logout", {}),
                           ("/api/auth/demo", {}), ("/api/jobs", {**GC, "name": "X", "customer": "Y"})):
            res, data = self.request("POST", path, body, cookie=self.owner, headers=evil)
            self.assertEqual(res.status, 403, path)
            self.assertIn("another site", data["error"])
        res, _ = self.request("POST", "/api/auth/logout", {}, cookie=self.owner, headers={"Origin": "null"})
        self.assertEqual(res.status, 403)
        res, _ = self.request("POST", "/api/auth/logout", {}, cookie=self.owner, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(res.status, 403, "no Origin, but the browser says cross-site")
        # Still signed in after all that.
        res, _ = self.request("GET", "/api/state", cookie=self.owner)
        self.assertEqual(res.status, 200)
        # The site's own pages post as before.
        res, _ = self.request("POST", "/api/jobs", {**GC, "name": "Own page", "customer": "Park GC"}, cookie=self.owner,
                              headers={"Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(res.status, 200)
        with mock.patch.dict(os.environ, {"DURACASH_URL": "https://app.duracash.example"}):
            res, _ = self.request("POST", "/api/auth/ping", {}, cookie=self.owner, headers={"Origin": "https://app.duracash.example"})
            self.assertEqual(res.status, 200, "DURACASH_URL's own address")
        # A JSON route refuses the bodies an HTML form can send without asking.
        for ctype in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data; boundary=x"):
            res, data = self.request("POST", "/api/auth/login", raw=b"{}", headers={"Content-Type": ctype})
            self.assertEqual(res.status, 415, ctype)
        # Services' webhooks keep their own checks.
        res, _ = self.request("POST", "/api/mail-events/whatever", {}, headers=evil)
        self.assertEqual(res.status, 404, "the token check answers, not the Origin check")

    def test_the_nov27_branches_routes_keep_the_cross_site_rules(self):
        # Integration (Nov 27): the posts records, delivery and compliance added are refused from
        # another site like every other; the open-tracking image (GET) needs no sign-in and says
        # nothing; provider delivery and open events still come in through the bounce webhook.
        import delivery
        host = f"127.0.0.1:{self.port}"
        evil, own = {"Origin": "https://evil.example"}, {"Origin": f"http://{host}"}
        posts = [(f"/api/jobs/{self.job_id}/daily-record-edit/rec-x", {"notes": "x"}), (f"/api/jobs/{self.job_id}/dismiss-row", {"key": "k"}),
                 (f"/api/jobs/{self.job_id}/revise/inv-x", {"reason": "x"}), ("/api/outbox/eml-x/resend", {"email": "a@b.example"}),
                 (f"/api/jobs/{self.job_id}/packet-soft-check/item-x", {"answer": "checked"}),
                 ("/api/compliance/docs", {"type": "coi"}), ("/api/compliance/settings", {"remind_days": [30]}),
                 (f"/api/compliance/liens/{self.job_id}", {"tier": "lower"}), ("/api/compliance/requirements", {"type": "coi"})]
        for path, body in posts:
            res, data = self.request("POST", path, body, cookie=self.owner, headers=evil)
            self.assertEqual(res.status, 403, path)
            self.assertIn("another site", data["error"], path)
            res, data = self.request("POST", path, body, cookie=self.owner, headers=own)
            self.assertNotIn("another site", str(data.get("error", "")) if isinstance(data, dict) else "", path)
        res, data = self.request("POST", "/api/compliance/docs/cdoc-00000000/file", raw=b"%PDF-1.4", cookie=self.owner,
                                 headers={**evil, "Content-Type": "application/pdf", "X-Filename": "coi.pdf"})
        self.assertEqual(res.status, 403, "the raw upload too")
        # The image: no cookie, any token, the same answer; a real one records an open only when the
        # company turned tracking on, and nothing about the email comes back.
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            entry = {"id": "eml-track", "kind": "pay_app", "job_id": self.job_id, "status": "sent", "to": "ap@park.example", "cc": [],
                     "at": "2026-11-27T00:00:00+00:00", "subject": "Pay app"}
            delivery.start(entry, "2026-11-27T00:00:01+00:00", [], {"ap@park.example": "t" * 24}, {})
            state.setdefault("email_log", []).append(entry)
            server.save_company(self.company, state)
        answers = []
        for path in (f"/o/{self.company}.{'t' * 24}.gif", f"/o/{self.company}.{'u' * 24}.gif", f"/o/co-nobody.{'t' * 24}.gif"):
            res, body = self.request("GET", path)
            answers.append((res.status, res.getheader("Content-Type"), body, res.getheader("Set-Cookie")))
        self.assertEqual(answers, [(200, "image/gif", delivery.PIXEL, None)] * 3)
        stored = next(e for e in server.load_company(self.company)["email_log"] if e["id"] == "eml-track")
        self.assertEqual(delivery.row_for(stored, "ap@park.example")["status"], "sent", "tracking is off by default: nothing recorded")
        self.assertEqual(self.request("POST", "/api/company", {"open_tracking": True}, cookie=self.owner)[0].status, 200)
        self.request("GET", f"/o/{self.company}.{'t' * 24}.gif", headers=evil)
        stored = next(e for e in server.load_company(self.company)["email_log"] if e["id"] == "eml-track")
        self.assertEqual(delivery.row_for(stored, "ap@park.example")["status"], "opened")
        # Delivery events through the bounce webhook, with its password.
        token = "t" * 20
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": token, "MAIL_EVENTS_PASSWORD": "pw"}):
            event = [{"event": "delivered", "email": "ap@park.example", "subject": "Pay app", "timestamp": 1890000000}]
            res, _ = self.request("POST", f"/api/mail-events/{token}", event, headers=evil)
            self.assertEqual(res.status, 401, "no password: refused by its own check, not the Origin one")
            res, out = self.request("POST", f"/api/mail-events/{token}", event,
                                    headers={**evil, "Authorization": "Basic " + base64.b64encode(b"x:pw").decode()})
            self.assertEqual(res.status, 200, out)

    def test_the_payapp_core_and_client_forms_posts_keep_the_cross_site_rules(self):
        # Integration (Nov 27, second pass): payapp-core's job actions and gc-forms' routes, the
        # raw-body uploads among them, are refused from another site and not from the app.
        host = f"127.0.0.1:{self.port}"
        evil, own = {"Origin": "https://evil.example"}, {"Origin": f"http://{host}"}
        _, snap = self.request("GET", "/api/state", cookie=self.owner)
        payer = snap["jobs"][0]["payer_id"]
        posts = [(f"/api/jobs/{self.job_id}/earlier-payapps", {"payapps": []}), (f"/api/jobs/{self.job_id}/earlier-payapps-remove", {}),
                 (f"/api/jobs/{self.job_id}/line-retainage", {"pcts": {}}), (f"/api/jobs/{self.job_id}/payapp-retainage", {"pct": "5"}),
                 (f"/api/jobs/{self.job_id}/retainage-release", {"stage": "final"}),
                 (f"/api/payers/{payer}/client-forms/gcf-0123456789/match", {"mapping": []}),
                 (f"/api/payers/{payer}/client-forms/gcf-0123456789/note", {"note": "x"}),
                 (f"/api/payers/{payer}/client-forms/gcf-0123456789/retire", {}), (f"/api/payers/{payer}/client-forms/send", {"send": "ours"})]
        for path, body in posts:
            res, data = self.request("POST", path, body, cookie=self.owner, headers=evil)
            self.assertEqual(res.status, 403, path)
            self.assertIn("another site", data["error"], path)
            res, data = self.request("POST", path, body, cookie=self.owner, headers=own)
            self.assertNotIn("another site", str(data.get("error", "")) if isinstance(data, dict) else "", path)
        for path, ctype, name in ((f"/api/payers/{payer}/client-forms/upload/payapp", "application/pdf", "form.pdf"),
                                  (f"/api/import/file?job={self.job_id}&file=1&kind=other&history=inv-x", "application/pdf", "old-pa-1.pdf")):
            res, data = self.request("POST", path, raw=b"%PDF-1.4 x", cookie=self.owner, headers={**evil, "Content-Type": ctype, "X-Filename": name})
            self.assertEqual(res.status, 403, path)
            self.assertIn("another site", data["error"], path)
        self.assertEqual(server.load_company(self.company).get("gc_forms", {}).get("templates", []), [], "nothing was stored from another site")

    # ----- M2: password asked again, and throttled -----

    def test_password_checks_in_a_session_are_throttled(self):
        for _ in range(auth.LOGIN_FREE_FAILURES):
            res, _ = self.request("POST", "/api/account/delete", {"password": "wrong one", "confirm": "x"}, cookie=self.owner)
            self.assertEqual(res.status, 409)
        res, data = self.request("POST", "/api/account/delete", {"password": "wrong one", "confirm": "x"}, cookie=self.owner)
        self.assertEqual(res.status, 429, data)
        res, data = self.request("POST", "/api/reset", {"password": "wrong", "confirm": f"Pine {self.n}"}, cookie=self.owner)
        self.assertEqual(res.status, 409)
        self.assertIn("Try again in", data["error"])
        res, data = self.request("POST", "/api/auth/2fa/begin", {"password": PASSWORD}, cookie=self.owner)
        self.assertEqual(res.status, 429, "the right password waits too, so guessing can't go faster")

    def test_admin_turns_the_app_off_for_a_lost_phone(self):
        email = f"sec{self.n}@example.com"
        uid = auth.user_by_email(email)["id"]
        secret = auth.totp_begin(uid, PASSWORD)["secret"]
        import totp
        auth.totp_enable(uid, totp.code_at(secret, int(time.time() // 30)))
        self.assertTrue(auth.user_by_email(email)["two_step"])
        with mock.patch("builtins.input", lambda _: email), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(admin.main(["admin.py", "two-step-off", email]), 0)
        self.assertIn("is off", out.getvalue())
        self.assertFalse(auth.user_by_email(email)["two_step"])
        res, _ = self.request("GET", "/api/state", cookie=self.owner)
        self.assertEqual(res.status, 401, "every session ended")
        line = json.loads(admin.log_path().read_text().splitlines()[-1])
        self.assertEqual((line["command"], line["result"]), ("two-step-off", "ok"))
        self.assertNotIn(email, admin.log_path().read_text(), "an email is kept as a hash")
        self.assertEqual(line["target"], "email:" + hashlib.sha256(email.encode()).hexdigest()[:12])

    # ----- M3: inbound email -----

    def test_inbound_email_needs_a_check_in_production_and_a_repeat_is_ignored(self):
        token = importer.company_tokens(self.company)["email"]
        post = {"from": "pm@park.example", "subject": "Daily log", "text": "Poured footings today."}
        with mock.patch.dict(os.environ, {"DURACASH_ENV": "production", "INBOUND_WEBHOOK_PASSWORD": "", "INBOUND_SIGNING_KEY": ""}):
            res, data = self.request("POST", f"/api/inbound/email/{token}", post)
            self.assertEqual(res.status, 503, "the address alone is not enough in production")
        basic = {"Authorization": "Basic " + base64.b64encode(b"duracash:pw").decode()}
        with mock.patch.dict(os.environ, {"DURACASH_ENV": "production", "INBOUND_WEBHOOK_PASSWORD": "pw"}):
            res, data = self.request("POST", f"/api/inbound/email/{token}", post, headers=basic)
            self.assertEqual(res.status, 200, data)
            res, data = self.request("POST", f"/api/inbound/email/{token}", post, headers=basic)
            self.assertEqual((res.status, data.get("ignored")), (200, "already received"))
        inbox = server.load_company(self.company).get("inbox", [])
        self.assertEqual(sum(1 for i in inbox if i.get("subject") == "Daily log"), 1)

    def test_a_replayed_mailgun_token_is_ignored(self):
        token = importer.company_tokens(self.company)["email"]
        stamp, nonce = str(int(time.time())), "nonce-" + str(self.n)
        sig = hmac.new(b"key", (stamp + nonce).encode(), hashlib.sha256).hexdigest()
        form = (f"sender=pm%40park.example&subject=Hi&body-plain=Hello&timestamp={stamp}&token={nonce}&signature={sig}").encode()
        ctype = {"Content-Type": "application/x-www-form-urlencoded"}
        with mock.patch.dict(os.environ, {"INBOUND_SIGNING_KEY": "key"}):
            res, data = self.request("POST", f"/api/inbound/email/{token}", raw=form, headers=ctype)
            self.assertEqual(res.status, 200, data)
            self.assertNotIn("ignored", data)
            res, data = self.request("POST", f"/api/inbound/email/{token}", raw=form, headers=ctype)
            self.assertEqual(data.get("ignored"), "already received", "the same signing token twice")

    # ----- L4 -----

    def test_a_very_long_number_is_refused_not_a_crash(self):
        digits = "9" * 5000
        res, data = self.request("POST", f"/api/jobs/{self.job_id}/progress", raw=('{"completed_cents": ' + digits + "}").encode(),
                                 cookie=self.owner, headers={"Content-Type": "application/json"})
        # Refused either way: 400 where the JSON reader itself won't take it (Python 3.11 and later), 409 under the field.
        self.assertIn(res.status, (400, 409), data)
        res, data = self.request("POST", f"/api/jobs/{self.job_id}/progress", {"completed_cents": "9" * 4000}, cookie=self.owner)
        self.assertEqual(res.status, 409, data)
        self.assertIn("too large", data["error"])
        res, data = self.request("POST", "/api/qbo/payments", {"days": "9" * 40}, cookie=self.owner)
        self.assertEqual(res.status, 409, data)

    def test_bounce_webhook_takes_a_password_when_set(self):
        token = "t" * 20
        body = {"email": "ap@park.example", "reason": "no such user"}
        with mock.patch.dict(os.environ, {"MAIL_EVENTS_TOKEN": token, "MAIL_EVENTS_PASSWORD": "pw"}):
            res, _ = self.request("POST", f"/api/mail-events/{token}", body)
            self.assertEqual(res.status, 401)
            res, _ = self.request("POST", f"/api/mail-events/{token}", body,
                                  headers={"Authorization": "Basic " + base64.b64encode(b"x:pw").decode()})
            self.assertEqual(res.status, 200)
            res, _ = self.request("POST", f"/api/mail-events/{'u' * 20}", body)
            self.assertEqual(res.status, 404)

    # ----- M10 -----

    def test_the_forwarded_header_shape_is_logged_once_without_addresses(self):
        server._FORWARDED_LOGGED.clear()
        lines = []
        with mock.patch.object(server, "log_json", lambda level, **f: lines.append(f)):
            self.request("GET", "/api/auth/me", headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7"})
            self.request("GET", "/api/auth/me", headers={"X-Forwarded-For": "203.0.113.10"})
        shapes = [f for f in lines if f.get("msg") == "forwarded header shape"]
        self.assertEqual(len(shapes), 1)
        self.assertEqual((shapes[0]["entries"], shapes[0]["trusted_proxy_hops"]), (2, 1))
        self.assertNotIn("203.0.113", json.dumps(lines))
        with mock.patch.dict(os.environ, {"TRUSTED_PROXY_HOPS": "2"}):
            self.assertEqual(server.trusted_proxy_hops(), 2)


@hartline_demo
class DeletedCompanyQuickBooksTests(unittest.TestCase):
    """H4: a deleted company's QuickBooks tokens go with it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        patches = [mock.patch.object(auth, "AUTH_PATH", root / "auth.json"), mock.patch.object(server, "COMPANIES", root / "companies"),
                   mock.patch.object(importer, "UPLOADS", root / "uploads"), mock.patch.object(storage, "DATA_DIR", root)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_deleting_a_company_drops_and_revokes_its_tokens(self):
        with server.company_lock("co-gone"):
            server.save_company("co-gone", server.empty_state("Gone Co"))
        qbo._store("co-gone", {"refresh_token": "r1", "access_token": "a1"})
        qbo._store("co-kept", {"refresh_token": "r2", "access_token": "a2"})
        qbo._PENDING["state-x"] = {"company_id": "co-gone", "user": "Ann", "user_id": "u", "until": time.time() + 60}
        revoked = []
        with mock.patch.object(qbo, "_revoke", lambda conn: revoked.append(conn["refresh_token"])):
            server.delete_company_data("co-gone")
            for t in threading.enumerate():
                if t.name == "qbo-revoke":
                    t.join(5)
        self.assertIsNone(qbo.connection("co-gone"))
        self.assertIsNotNone(qbo.connection("co-kept"))
        self.assertNotIn("state-x", qbo._PENDING, "a Connect in progress can't finish")
        self.assertEqual(revoked, ["r1"])
        # The file is keyed by company; "r1" alone could turn up inside the other company's random ciphertext.
        self.assertEqual(list(json.loads((Path(self.tmp.name) / "qbo_connections.json").read_text())), ["co-kept"])


class UpgradeBackupTests(unittest.TestCase):
    """H7: everything is backed up before this code upgrades data it can't roll back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patches = [mock.patch.object(auth, "AUTH_PATH", self.root / "auth.json"), mock.patch.object(server, "COMPANIES", self.root / "companies"),
                   mock.patch.object(importer, "UPLOADS", self.root / "uploads"), mock.patch.object(storage, "DATA_DIR", self.root),
                   mock.patch.object(server, "log_json")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_backup_before_an_upgrade_once(self):
        self.assertIsNone(server.backup_before_upgrade(log=lambda m: None), "no data yet: nothing to keep")
        (self.root / server.SCHEMA_MARKER).unlink()
        auth.create_user("up@example.com", PASSWORD, "Pat", "co-up")
        made = server.backup_before_upgrade(log=lambda m: None)
        self.assertRegex(made.name, rf"-pre-upgrade-schema{migrations.SCHEMA_VERSION}\.tar\.gz(\.enc)?$")
        self.assertIn(made, backup.backups(), "with the other backups, so backup.py list and restore see it")
        self.assertIsNone(server.backup_before_upgrade(log=lambda m: None), "once per schema")
        storage.write_json(self.root / server.SCHEMA_MARKER, {"schema": migrations.SCHEMA_VERSION - 1, "auth_steps": len(migrations.AUTH_STEPS)})
        self.assertIsNotNone(server.backup_before_upgrade(log=lambda m: None), "data from older code")

    def test_older_code_on_newer_data_says_so(self):
        auth.create_user("up@example.com", PASSWORD, "Pat", "co-up")
        storage.write_json(self.root / server.SCHEMA_MARKER, {"schema": migrations.SCHEMA_VERSION + 1, "auth_steps": 9})
        with mock.patch.object(alerts, "notify") as notify:
            self.assertIsNone(server.backup_before_upgrade())
        notify.assert_called_once()
        self.assertIn("restore the pre-upgrade backup", notify.call_args[0][1])
        self.assertEqual(json.loads((self.root / server.SCHEMA_MARKER).read_text())["schema"], migrations.SCHEMA_VERSION + 1,
                         "the marker isn't moved back: the next newer deploy needs no second backup")

    @unittest.skipIf(storage.fcntl is None, "flock is POSIX only")
    def test_a_second_copy_is_refused_before_it_backs_up_or_marks_the_schema(self):  # Nov 27 hunt
        auth.create_user("up@example.com", PASSWORD, "Pat", "co-up")
        held = open(self.root / ".duracash-server.lock", "a+")   # another server holds the data directory
        self.addCleanup(held.close)
        storage.fcntl.flock(held.fileno(), storage.fcntl.LOCK_EX | storage.fcntl.LOCK_NB)
        with mock.patch.object(backup, "make_backup") as make, mock.patch.dict(os.environ, {"DURACASH_ENV": ""}), self.assertRaises(SystemExit):
            server.main()
        make.assert_not_called()
        self.assertFalse((self.root / server.SCHEMA_MARKER).exists(), "the running server's marker is left as it is")

    def test_a_failed_backup_stops_the_start(self):
        auth.create_user("up@example.com", PASSWORD, "Pat", "co-up")
        with mock.patch.object(backup, "make_backup", side_effect=OSError("disk full")), mock.patch.object(alerts, "notify"), \
                mock.patch.dict(os.environ, {"DURACASH_ENV": ""}), self.assertRaises(SystemExit):
            server.main()
        self.assertFalse((self.root / server.SCHEMA_MARKER).exists())


class ShutdownTests(unittest.TestCase):
    """L4: SIGTERM lets the requests being answered finish."""

    def test_drain_waits_for_requests_in_progress(self):
        srv = server.Server(("127.0.0.1", 0), server.Handler)
        self.addCleanup(srv.server_close)
        srv.busy(1)
        threading.Timer(0.2, lambda: srv.busy(-1)).start()
        started = time.monotonic()
        self.assertEqual(srv.drain(5), 0)
        self.assertLess(time.monotonic() - started, 3)
        srv.busy(1)
        self.assertEqual(srv.drain(0.1), 1, "gives up after the time allowed")

    def test_sigterm_stops_serving(self):
        srv = server.Server(("127.0.0.1", 0), server.Handler)
        self.addCleanup(srv.server_close)
        previous = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, previous)
        with mock.patch.object(server, "log_json"):
            server.stop_on_sigterm(srv)
            runner = threading.Thread(target=srv.serve_forever, daemon=True)
            runner.start()
            time.sleep(0.1)
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            runner.join(5)
        self.assertFalse(runner.is_alive())


class AdminLogTests(unittest.TestCase):
    """M11: every admin.py command leaves a line, and a change alerts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(storage, "DATA_DIR", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)

    def test_each_command_is_appended_and_changes_alert(self):
        sent = []
        with mock.patch.object(alerts, "notify", lambda key, text, send=None: sent.append(text)), \
                mock.patch.object(admin, "_who", lambda: "stephen"):
            admin.record("find", ["Pat@Example.com"], 0)
            admin.record("delete-company", ["co-1"], 0)
            admin.record("delete-company", ["co-2"], 1)
        lines = [json.loads(x) for x in admin.log_path().read_text().splitlines()]
        self.assertEqual([(x["command"], x["target"], x["by"], x["result"]) for x in lines],
                         [("find", "email:" + hashlib.sha256(b"pat@example.com").hexdigest()[:12], "stephen", "ok"),
                          ("delete-company", "co-1", "stephen", "ok"), ("delete-company", "co-2", "stephen", "exit 1")])
        self.assertEqual(sent, ["admin.py delete-company co-1 run by stephen"], "only a change that went through alerts")
        admin.record("companies", [], 0)
        self.assertEqual(len(admin.log_path().read_text().splitlines()), 4, "appended, never rewritten")


class ProfileMigrationTests(unittest.TestCase):
    def test_office_profiles_get_the_permission_and_field_ones_dont(self):  # H2
        state = {"company": {"profiles": [
            {"id": "owner", "name": "Owner", "built_in": True, "locked": True},
            {"id": "member", "name": "Member", "built_in": True, "perms": ["record_payments", "send_payapps"]},
            {"id": "prof-1", "name": "Office", "perms": ["send_payapps", "record_payments"]},
            {"id": "prof-2", "name": "Bookkeeper", "perms": ["financials"]},
            {"id": "prof-3", "name": "Field", "perms": []},
            {"id": "prof-4", "name": "PM", "perms": ["manage_jobs", "close_jobs"]}]}, "schema_version": migrations.SCHEMA_VERSION - 1}
        migrations.migrate(state)
        holders = profiles.holders(state, "sensitive_docs")
        self.assertEqual(holders, {"owner", "prof-1", "prof-2"})
        migrations._sec_sensitive_docs(state)
        self.assertEqual(state["company"]["profiles"][2]["perms"].count("sensitive_docs"), 1, "safe to run again")


class RenderBlueprintTests(unittest.TestCase):
    def test_settings_stephen_fills_in(self):  # H1, M9
        text = (ROOT / "render.yaml").read_text()
        production, staging = text.split("name: duracash-staging")
        for key in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_REGION", "BACKUP_S3_KEY_ID", "BACKUP_S3_SECRET",
                    "BACKUP_AGE_RECIPIENT", "ALERT_WEBHOOK_URL", "DURACASH_ALLOW_NO_OFFSITE"):
            self.assertIn(f"- key: {key}\n        sync: false", production, key)
        self.assertIn("- key: DURACASH_DEMO\n        value: readonly", staging, "the staging demo's password is public")


if __name__ == "__main__":
    unittest.main()
