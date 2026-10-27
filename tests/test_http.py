"""End-to-end over HTTP against a temporary data directory."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import server

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
        res, state = self.request("POST", "/api/reset", {"confirm": "Actor Co"}, cookie=cookie)
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
            res, data = self.request("POST", f"/api/jobs/{jid}/resolve/{item['id']}", {}, cookie=cookie)
            self.assertEqual(res.status, 409)
            res, state = self.request("POST", f"/api/jobs/{jid}/resolve/{item['id']}", {"reason": "Sent by email"}, cookie=cookie)
            self.assertEqual(res.status, 200)
        res, state = self.request("POST", f"/api/jobs/{jid}/send", {}, cookie=cookie)
        self.assertEqual(res.status, 200)
        res, data = self.request("POST", f"/api/jobs/{jid}/match-payment", {"amount_cents": -5, "reference": ""}, cookie=cookie)
        self.assertEqual(res.status, 409)
        res, state = self.request("POST", f"/api/jobs/{jid}/match-payment", {"amount_cents": 3_600_000, "reference": "CHK-1"}, cookie=cookie)
        self.assertEqual(res.status, 200)
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
