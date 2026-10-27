"""A new contractor's first run over HTTP: sign up, add a job with the New job form's exact body,
mark it done, deal with the paperwork, send the first pay app (R1-12).

This pins the path as it works today. R1-04 (notes stay notes), R1-05 (email asked at send),
R1-07 (confirm your email first) and R1-08 (paperwork Duracash already has) each add their
assertions here when they land.
"""

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


def new_job_body(**over):
    """What the New job form posts (static/app.js, the create-job action), field for field."""
    body = {
        "name": "Porch rebuild", "address": "12 Elm St", "customer": "Dana Homeowner", "approver": "Dana Homeowner",
        "ap_email": "dana@example.com", "phone": "", "role": "Owner",
        "contract_number": "", "packet_notes": "",
        "contract_cents": 4850000, "retainage_pct": 0,
    }
    body.update(over)
    return body


class FirstRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._orig = (auth.AUTH_PATH, server.COMPANIES)
        auth.AUTH_PATH = Path(cls.tmp.name) / "auth.json"
        server.COMPANIES = Path(cls.tmp.name) / "companies"
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

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Content-Length": str(len(data))} if data is not None else {}
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        return res, json.loads(payload or b"{}")

    def signup(self, email):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": PASSWORD, "name": "Pat Pine", "company_name": "Pine Builders"})
        self.assertEqual(res.status, 200, data)
        return res.getheader("Set-Cookie").split(";")[0], data

    def job(self, data, job_id):
        return next(j for j in data["jobs"] if j["id"] == job_id)

    def test_sign_up_to_first_pay_app_sent(self):
        cookie, signed = self.signup("first-run@example.com")
        self.assertIn("verification", signed)

        res, data = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(data["jobs"], [], "a new company starts empty")

        res, data = self.request("POST", "/api/jobs", new_job_body(), cookie)
        self.assertEqual(res.status, 200, data)
        job = data["jobs"][-1]
        job_id = job["id"]
        self.assertEqual((job["name"], job["contract_cents"]), ("Porch rebuild", 4850000))
        self.assertEqual(job["invoices"], [])

        # Done: the whole contract is ready to bill and a draft pay app is made.
        res, data = self.request("POST", f"/api/jobs/{job_id}/done", {"notes": ""}, cookie)
        self.assertEqual(res.status, 200, data)
        job = self.job(data, job_id)
        self.assertEqual(job["work_status"], "complete")
        [draft] = job["invoices"]
        self.assertIn(draft["status"], ("draft", "awaiting_approval"))
        self.assertEqual(draft["gross_cents"], 4850000)

        # The paperwork the customer wants with each pay app stops the send until it's in.
        missing = [p for p in draft["packet"] if p["required"] and not p["present"]]
        res, data = self.request("POST", f"/api/jobs/{job_id}/send", {}, cookie)
        if missing:
            self.assertEqual(res.status, 409, data)
            self.assertTrue(data["blockers"], data)
            for item in missing:
                res, data = self.request("POST", f"/api/jobs/{job_id}/resolve/{item['id']}", {"reason": "Handed over on site"}, cookie)
                self.assertEqual(res.status, 200, data)
            self.assertEqual(self.job(data, job_id)["blockers"], [])
            res, data = self.request("POST", f"/api/jobs/{job_id}/send", {}, cookie)
        self.assertEqual(res.status, 200, data)

        job = self.job(data, job_id)
        [sent] = job["invoices"]
        self.assertEqual(sent["status"], "sent")
        self.assertTrue(sent["sent_at"])
        res, outbox = self.request("GET", "/api/outbox", cookie=cookie)
        self.assertEqual(res.status, 200, outbox)
        self.assertIn("dana@example.com", [e["to"] for e in outbox["entries"]], "the pay app went to the customer's email")

    def test_new_job_form_refusals_name_their_fields(self):
        cookie, _ = self.signup("first-run-fields@example.com")
        res, data = self.request("POST", "/api/jobs", new_job_body(name="", ap_email="not an email", contract_cents=0), cookie)
        self.assertEqual(res.status, 409, data)
        self.assertTrue({"name", "ap_email", "contract_cents"} <= set(data["fields"]), data)
        res, data = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(data["jobs"], [], "nothing is saved from a refused form")


if __name__ == "__main__":
    unittest.main()
