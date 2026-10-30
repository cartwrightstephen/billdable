"""End-to-end over HTTP against a temporary data directory."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import server
import trial

PASSWORD = "correct horse battery"


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._orig = (auth.AUTH_PATH, server.COMPANIES)
        auth.AUTH_PATH = Path(cls.tmp.name) / "auth.json"
        server.COMPANIES = Path(cls.tmp.name) / "companies"
        server.ensure_demo()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()

    def request(self, method, path, body=None, cookie=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
            hdrs.setdefault("Content-Length", str(len(data)))
        if cookie:
            hdrs["Cookie"] = cookie
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        try:
            parsed = json.loads(payload or b"{}")
        except ValueError:
            parsed = None
        return res, parsed

    def signup(self, email, company="Pine Builders"):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": PASSWORD, "name": "Pat Pine", "company_name": company})
        self.assertEqual(res.status, 200, data)
        return res.getheader("Set-Cookie").split(";")[0], data["user"]

    def test_static_traversal_refused(self):
        for path in ["/../data/auth.json", "/../server.py", "/../auth.json", "/static/../server.py", "/%2e%2e/server.py"]:
            res, _ = self.request("GET", path)
            self.assertEqual(res.status, 404, path)
        res, _ = self.request("GET", "/app.js")
        self.assertEqual(res.status, 200)
        self.assertTrue(res.getheader("Content-Type").startswith("text/javascript"))
        res, _ = self.request("GET", "/styles.css")
        self.assertTrue(res.getheader("Content-Type").startswith("text/css"))
        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
        csp = res.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self';", csp)
        self.assertNotIn("unsafe-eval", csp)
        res, _ = self.request("GET", "/favicon.ico")
        self.assertEqual(res.status, 204)

    def test_malformed_input_returns_400(self):
        cookie, _ = self.signup("bad-input@example.com")
        res, data = self.request("POST", "/api/jobs", raw=b"{not json", cookie=cookie)
        self.assertEqual(res.status, 400)
        self.assertIn("JSON", data["error"])
        res, data = self.request("POST", "/api/jobs", raw=b"[1,2]", cookie=cookie)
        self.assertEqual(res.status, 400)
        res, data = self.request("POST", "/api/jobs", raw=b"{}", cookie=cookie, headers={"Content-Length": "abc"})
        self.assertEqual(res.status, 400)
        res, data = self.request("POST", "/api/auth/login", {"email": "x@example.com", "password": 12345})
        self.assertEqual(res.status, 400)
        # A number that isn't one is refused under its field, so the form can show it there (R1-02).
        res, data = self.request("POST", "/api/jobs", {"name": "A", "contract_cents": 1e400}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("contract_cents", data["fields"])

    def test_bad_request_says_nothing_about_python(self):
        cookie, _ = self.signup("not-understood@example.com")
        job = {"name": "Ash deck", "customer": "Ash GC", "address": "1 Ash", "approver": "Kim", "ap_email": "ap@ash.example", "contract_cents": 500_000}
        res, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        jid = state["jobs"][0]["id"]
        res, data = self.request("POST", f"/api/jobs/{jid}/sov", {"lines": "x"}, cookie=cookie)
        self.assertEqual(res.status, 400)
        for leak in ("'", '"', "str", "list", "dict", "NoneType", "Error", "Traceback"):
            self.assertNotIn(leak, data["error"])
        self.assertIn("request_id", data)

    def test_refusals_name_the_field(self):
        cookie, _ = self.signup("fields@example.com")
        res, data = self.request("POST", "/api/jobs", {"name": "Oak", "customer": "Oak GC", "address": "1 Oak", "approver": "Kim",
                                                       "ap_email": "not an email", "contract_cents": 100_000}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertEqual(list(data["fields"]), ["ap_email"])
        self.assertEqual(data["error"], data["fields"]["ap_email"], "one bad field: the message is that field's")
        res, data = self.request("POST", "/api/jobs", {"name": "", "customer": "", "contract_cents": "lots"}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertEqual(data["fields"], {"contract_cents": "Enter the contract amount as a number."})

    def test_signup_password_min_length(self):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "short@example.com", "password": "123456789", "name": "Pat", "company_name": "P"})
        self.assertEqual(res.status, 400)
        self.assertIn("10 characters", data["error"])

    def test_look_around_the_sample_company(self):  # R2-06
        with mock.patch.dict("os.environ", {"DURACASH_ENV": "", "DURACASH_DEMO": "readonly"}):
            res, data = self.request("GET", "/api/auth/demo-available")
            self.assertEqual((res.status, data), (200, {"available": True}))
            res, data = self.request("POST", "/api/auth/demo", {})
            self.assertEqual(res.status, 200, data)
            self.assertEqual(data["user"]["company_id"], auth.DEMO_COMPANY_ID)
            cookie = res.getheader("Set-Cookie").split(";")[0]
            self.assertTrue(cookie.split("=", 1)[1], "a session cookie is set")
            res, me = self.request("GET", "/api/auth/me", cookie=cookie)
            self.assertEqual(res.status, 200, me)
            self.assertEqual(me["user"]["email"], auth.DEMO_EMAIL)
            self.assertTrue(me["read_only"], "the read-only demo stays read-only")
        with mock.patch.dict("os.environ", {"DURACASH_ENV": "", "DURACASH_DEMO": "off"}):
            res, data = self.request("GET", "/api/auth/demo-available")
            self.assertEqual(data, {"available": False})
            res, data = self.request("POST", "/api/auth/demo", {})
            self.assertEqual(res.status, 404, data)
            self.assertNotIn("Set-Cookie", dict(res.getheaders()))

    def test_signup_without_company_name_uses_the_persons_name(self):  # R2-02
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "noco@example.com", "password": PASSWORD, "name": "Pat Pine", "company_name": ""})
        self.assertEqual(res.status, 200, data)
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(state["company"]["name"], "Pat Pine")
        # The same email again: the page is told to offer "Sign in instead".
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "noco@example.com", "password": PASSWORD, "name": "Pat Pine", "company_name": ""})
        self.assertEqual(res.status, 400)
        self.assertTrue(data.get("exists"), data)

    def test_login_rate_limited(self):
        self.signup("rate@example.com")
        for _ in range(auth.LOGIN_FREE_FAILURES):
            res, _ = self.request("POST", "/api/auth/login", {"email": "rate@example.com", "password": "wrong password"})
            self.assertEqual(res.status, 401)
        res, _ = self.request("POST", "/api/auth/login", {"email": "rate@example.com", "password": PASSWORD})
        self.assertEqual(res.status, 429)

    def test_secure_cookie_behind_https_proxy(self):
        res, _ = self.request("POST", "/api/auth/login", {"email": auth.DEMO_EMAIL, "password": auth.DEMO_PASSWORD}, headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(res.status, 200)
        self.assertIn("Secure", res.getheader("Set-Cookie"))
        self.assertIsNone(res.getheader("Strict-Transport-Security"), "only once the site says it is https-only")
        self.assertIn("microphone=()", res.getheader("Permissions-Policy"))
        # With COOKIE_SECURE=1 (production) every answer tells the browser to stay on https (R6-05).
        with mock.patch.dict("os.environ", {"COOKIE_SECURE": "1"}):
            for path in ("/", "/api/auth/me", "/healthz"):
                res, _ = self.request("GET", path, headers={"X-Forwarded-Proto": "https"})
                self.assertEqual(res.getheader("Strict-Transport-Security"), "max-age=31536000; includeSubDomains", path)

    def test_signup_is_rate_limited_per_network(self):  # R6-05
        server._LIMITS.clear()
        signup = lambda n, ip: self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": f"net{n}-{ip}@example.com", "password": PASSWORD,
                                                                       "name": "Pat", "company_name": "Net Co"}, headers={"X-Forwarded-For": ip})
        statuses = [signup(n, "203.0.113.7")[0].status for n in range(6)]
        self.assertEqual(statuses, [200] * 5 + [429])
        res, data = signup(9, "203.0.113.7")
        self.assertEqual(data["error"], "Too many new accounts from this network. Try again in an hour.")
        self.assertEqual(signup(0, "198.51.100.4")[0].status, 200, "another network is not held up")
        # Only the address the proxy added counts: an address the caller makes up in front of it doesn't.
        self.assertEqual(signup(1, "10.9.9.9, 203.0.113.7")[0].status, 429)
        server._LIMITS.clear()

    def test_client_address_follows_the_trusted_proxies(self):  # R6-05
        handler = server.Handler.__new__(server.Handler)
        handler.client_address = ("10.0.0.2", 5555)
        handler.headers = {"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 3.3.3.3"}
        for hops, ip in (("1", "3.3.3.3"), ("2", "2.2.2.2"), ("0", "10.0.0.2"), ("5", "10.0.0.2"), ("x", "3.3.3.3")):
            with mock.patch.dict("os.environ", {"TRUSTED_PROXY_HOPS": hops}):
                self.assertEqual(handler._client_ip(), ip, hops)
        handler.headers = {}
        self.assertEqual(handler._client_ip(), "10.0.0.2")

    def test_system_email_has_an_hourly_ceiling(self):  # R6-05
        server._LIMITS.clear()
        with mock.patch.object(server.mailer, "configured", lambda: True), \
             mock.patch.object(server.mailer, "build_message", lambda *a, **k: (object(), [])), \
             mock.patch.object(server.mailer, "send", lambda msgs, cfg: [None] * len(msgs)):
            results = [server.send_system_email(f"p{n}@example.com", "Hi", "Body") for n in range(301)]
        self.assertEqual(results[:300], ["sent"] * 300)
        self.assertIn("hourly email limit", results[300])
        server._LIMITS.clear()

    def test_deeply_nested_json_is_a_bad_request(self):
        for path in ("/api/auth/login", "/api/auth/signup", "/api/auth/forgot"):
            res, data = self.request("POST", path, raw=b"[" * 100_000)
            self.assertEqual(res.status, 400, path)
            self.assertEqual(data["error"], "Request body must be valid JSON.")

    def test_a_refused_company_name_leaves_no_half_made_account(self):
        body = {"accept_terms": True, "email": "half@example.com", "password": PASSWORD, "name": "Pat Pine"}
        for bad in ("x" * 5_001, 42):
            res, data = self.request("POST", "/api/auth/signup", {**body, "company_name": bad})
            self.assertEqual(res.status, 400, data)
            self.assertIsNone(auth.user_by_email("half@example.com"))
        cookie, user = self.signup("half@example.com", company="Half Co")
        res, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(res.status, 200, state)
        self.assertEqual(state["company"]["name"], "Half Co")

    def test_reset_keeps_the_subscription_and_how_the_trial_ended(self):
        cookie, user = self.signup("reset-billing@example.com", company="Billing Co")
        company_id = user["company_id"]
        with server.company_lock(company_id):
            st = server.load_company(company_id)
            st["company"]["billing"] = {"customer_id": "cus_1", "subscription_id": "sub_1", "status": "active", "partner": True}
            st["jobs"] = [{"id": "job-x", "name": "Oak deck", "invoices": [
                {"id": "inv-x", "number": "PA-OAK-01", "status": "paid", "sent_at": "2026-10-01T12:00:00+00:00"}],
                "payments": [{"invoice_id": "inv-x", "amount_cents": 100, "matched_at": "2026-10-05T12:00:00+00:00"}]}]
            st["company"]["trial"] = {"started_at": "2026-09-01T12:00:00+00:00"}
            server.save_company(company_id, st)
        res, state = self.request("POST", "/api/reset", {"confirm": "Billing Co", "password": PASSWORD}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        after = server.load_company(company_id)
        self.assertEqual(after["jobs"], [])
        self.assertEqual(after["company"]["billing"], {"customer_id": "cus_1", "subscription_id": "sub_1", "status": "active", "partner": True})
        t = trial.status(after)
        self.assertEqual((t["state"], t["reason"], t["ended_at"], t["first_paid"], t["first_paid_job"]),
                         ("ended", "paid", "2026-10-05T12:00:00+00:00", "PA-OAK-01", "Oak deck"))

    def test_actor_comes_from_session_and_reset_needs_confirmation(self):
        cookie, user = self.signup("actor@example.com", company="Actor Co")
        job = {"name": "Oak deck", "customer": "Lee", "address": "1 Oak St", "approver": "Lee", "ap_email": "ap@lee.example", "contract_cents": 500000, "actor": "The CEO"}
        res, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        _, page = self.request("GET", "/api/audit?limit=1", cookie=cookie)
        self.assertEqual(page["entries"][0]["actor"], "Pat Pine")
        res, data = self.request("POST", "/api/reset", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        res, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(len(state["jobs"]), 1)
        # The name alone is not enough: the password too, as Delete asks (R6-05).
        res, data = self.request("POST", "/api/reset", {"confirm": "Actor Co", "password": "not my password"}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("password", data["fields"])
        res, state = self.request("POST", "/api/reset", {"confirm": "Actor Co", "password": PASSWORD}, cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(state["jobs"], [])
        snaps = list((server.COMPANIES / "snapshots").glob(f"{user['company_id']}-before-reset-*.json"))
        self.assertEqual(len(snaps), 1)

    def test_qbo_sample_demo_only_and_isolation(self):
        cookie, _ = self.signup("iso@example.com")
        res, data = self.request("POST", "/api/jobs/job-oak/import-qbo", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        res, data = self.request("POST", "/api/jobs/job-oak/add-co", {"title": "x", "amount_cents": 100}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("Job not found", data["error"])

    def test_export(self):
        cookie, _ = self.signup("export@example.com", company="Export Co")
        res, data = self.request("GET", "/api/export", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(data["company"]["name"], "Export Co")
        self.assertIn("attachment", res.getheader("Content-Disposition"))

    def test_export_is_owner_only(self):  # R6-05
        cookie, owner = self.signup("export-owner@example.com", company="Export Two")
        auth.create_user("export-member@example.com", PASSWORD, "Mo Member", owner["company_id"], role="member")
        res, data = self.request("POST", "/api/auth/login", {"email": "export-member@example.com", "password": PASSWORD})
        member = res.getheader("Set-Cookie").split(";")[0]
        res, data = self.request("GET", "/api/export", cookie=member)
        self.assertEqual(res.status, 403)
        self.assertEqual(data["error"], "Only an owner can export the company's data.")
        self.assertEqual(self.request("GET", "/api/export", cookie=cookie)[0].status, 200)

    def test_full_loop_over_http(self):
        cookie, _ = self.signup("loop@example.com")
        job = {"name": "Elm addition", "customer": "Park GC", "address": "9 Elm", "approver": "Kim", "ap_email": "ap@park.example",
               "contract_cents": 10_000_000, "retainage_pct": 10, "role": "GC", "note": "Site contact: Kim, gate code 1234."}
        res, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        jid = state["jobs"][0]["id"]
        self.assertEqual([r["label"] for r in state["jobs"][0]["records"]], ["Job notes"])
        # The paperwork list lives in the customer's settings, not in the job's notes (R1-04).
        res, state = self.request("POST", f"/api/payers/{state['jobs'][0]['payer_id']}/update", {"packet": "Pay app, lien waiver. Punch list before retainage."}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        res, state = self.request("POST", f"/api/jobs/{jid}/progress", {"completed_cents": 4_000_000}, cookie=cookie)
        inv = state["jobs"][0]["invoices"][0]
        self.assertEqual(inv["amount_cents"], 3_600_000)
        self.assertEqual([p["label"] for p in inv["packet"]], ["Pay app", "Lien waiver"])
        res, data = self.request("POST", f"/api/jobs/{jid}/send", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        for item in inv["packet"]:
            # Where it is is optional now (R3-05); a bad item id is still refused.
            res, data = self.request("POST", f"/api/jobs/{jid}/resolve/pkt-nope", {}, cookie=cookie)
            self.assertEqual(res.status, 409)
            res, state = self.request("POST", f"/api/jobs/{jid}/resolve/{item['id']}", {"reason": "Sent by email"}, cookie=cookie)
            self.assertEqual(res.status, 200)
        res, state = self.request("POST", f"/api/jobs/{jid}/send", {}, cookie=cookie)
        self.assertEqual(res.status, 200)
        res, data = self.request("POST", f"/api/jobs/{jid}/match-payment", {"amount_cents": -5, "reference": ""}, cookie=cookie)
        self.assertEqual(res.status, 409)
        res, state = self.request("POST", f"/api/jobs/{jid}/match-payment", {"amount_cents": 3_600_000, "reference": "CHK-1"}, cookie=cookie)
        self.assertEqual(res.status, 200)
        # The signed waiver goes on the job record first (M21); the type picked on the waiver page comes with it.
        res, data = self.request("POST", f"/api/jobs/{jid}/waiver", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        res, state = self.request("POST", f"/api/import/file?job={jid}&file=1&kind=waiver&waiver_kind=unconditional_progress", raw=b"%PDF-1.4 signed",
                                  cookie=cookie, headers={"Content-Type": "application/pdf", "X-Filename": "signed-waiver.pdf"})
        self.assertEqual(res.status, 200, state)
        self.assertEqual(state["jobs"][0]["records"][-1]["waiver_kind"], "unconditional_progress")
        res, state = self.request("POST", f"/api/jobs/{jid}/waiver", {}, cookie=cookie)
        self.assertEqual(res.status, 200)
        res, data = self.request("POST", f"/api/jobs/{jid}/close", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        res, data = self.request("POST", f"/api/jobs/{jid}/draft", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("Nothing to draft", data["error"])
        res, state = self.request("POST", f"/api/jobs/{jid}/done", {"notes": "Finished"}, cookie=cookie)
        self.assertEqual(res.status, 200)
        j = state["jobs"][0]
        self.assertEqual(j["invoices"][-1]["amount_cents"], 5_400_000)
        self.assertEqual([p["label"] for p in j["closeout_packet"]], ["Punch list before retainage"])


if __name__ == "__main__":
    unittest.main()
