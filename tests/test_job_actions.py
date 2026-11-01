"""Every job action the page calls exists on the server, and none of them answers an empty
request with a server error (R1-03)."""

import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import auth
import server

PASSWORD = "correct horse battery"
# `/api/jobs/${id}/send`, `/api/jobs/${id}/resolve/${item}`: the action is the word after the id.
CALL = re.compile(r"\/api\/jobs\/\$\{[^}]+\}\/([a-z-]+)")
# An empty body that is a complete request: these may succeed. Marking done takes optional
# notes, an edit with no fields changes nothing, and a job with no pay apps can be deleted.
EMPTY_OK = {"delete", "done", "edit"}


class JobActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._orig = (auth.AUTH_PATH, server.COMPANIES)
        auth.AUTH_PATH = Path(cls.tmp.name) / "auth.json"
        server.COMPANIES = Path(cls.tmp.name) / "companies"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        res, data = cls.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "actions@example.com", "password": PASSWORD, "name": "Pat Pine", "company_name": "Pine Builders"})
        assert res.status == 200, data
        cls.cookie = res.getheader("Set-Cookie").split(";")[0]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES = cls._orig
        cls.tmp.cleanup()

    @classmethod
    def request(cls, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Content-Length": str(len(data))} if data is not None else {}
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        try:
            return res, json.loads(payload or b"{}")
        except ValueError:
            return res, None

    def new_job(self, name):
        res, data = self.request("POST", "/api/jobs", {
            "name": name, "address": "1 Main St", "customer": "Ada Owner", "approver": "Ada Owner",
            "ap_email": "ada@example.com", "role": "Owner", "contract_cents": 1000000, "retainage_pct": 0,
        }, self.cookie)
        self.assertEqual(res.status, 200, data)
        return next(j["id"] for j in data["jobs"] if j["name"] == name)

    def test_every_action_the_page_calls_exists(self):
        called = set()
        for path in (ROOT / "static").rglob("*.js"):
            called |= set(CALL.findall(path.read_text(encoding="utf-8")))
        self.assertTrue(called, "the pattern found no calls: has the URL style changed?")
        # Links to pages the server serves (the pay-app PDF) are GET routes, not actions.
        served = {r.method + " " + r.pattern for r in server.ROUTES}
        links = {w for w in called if any(p.startswith(f"GET /api/jobs/([^/]+)/{w}/") for p in served)}
        self.assertEqual(sorted(called - links - set(server.JOB_ACTIONS)), [], "the page calls job actions the server does not have")

    def test_an_empty_request_is_refused_plainly_never_a_server_error(self):
        for action, (takes_item, _) in sorted(server.JOB_ACTIONS.items()):
            with self.subTest(action=action):
                job_id = self.new_job(f"Empty {action}")
                path = f"/api/jobs/{job_id}/{action}" + ("/no-such-item" if takes_item else "")
                res, data = self.request("POST", path, {}, self.cookie)
                self.assertLess(res.status, 500, data)
                if action in EMPTY_OK:
                    continue
                self.assertIn(res.status, (400, 404, 409), data)
                self.assertIsInstance(data, dict)
                self.assertTrue(isinstance(data.get("error"), str) and data["error"], data)

    def test_blank_text_is_refused_under_its_field(self):
        job_id = self.new_job("Blank text")
        for action, key in (("daily-record", "notes"), ("learn-rejection", "reason"), ("add-evidence", "label")):
            with self.subTest(action=action):
                res, data = self.request("POST", f"/api/jobs/{job_id}/{action}", {key: "   "}, self.cookie)
                self.assertEqual(res.status, 409, data)
                self.assertIn(key, data["fields"])

    def test_item_and_no_item_paths_do_not_mix(self):
        job_id = self.new_job("Path shapes")
        res, data = self.request("POST", f"/api/jobs/{job_id}/send/extra", {}, self.cookie)
        self.assertEqual(res.status, 404, data)
        res, data = self.request("POST", f"/api/jobs/{job_id}/resolve", {}, self.cookie)
        self.assertEqual(res.status, 404, data)
        res, data = self.request("POST", f"/api/jobs/{job_id}/no-such-action", {}, self.cookie)
        self.assertEqual(res.status, 404, data)
        self.assertEqual(data["error"], "Unknown action")


if __name__ == "__main__":
    unittest.main()
