"""A new contractor's first run over HTTP: sign up, add a job with the New job form's exact body,
mark it done, deal with the paperwork, send the first pay app (R1-12).

R1-04 (notes stay notes), R1-05 (email asked at send), R1-07 (confirm your email first) and
R1-08 (paperwork Duracash makes or already has) and R2-01 (a contract number is not a record)
are pinned here.
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
from test_accounts import ServerCase

PASSWORD = "correct horse battery"


def new_job_body(**over):
    """What the New job form posts (static/app.js, the create-job action), field for field."""
    body = {
        "name": "Porch rebuild", "address": "12 Elm St", "customer": "Dana Homeowner", "approver": "Dana Homeowner",
        "ap_email": "dana@example.com", "phone": "", "role": "Owner",
        "contract_number": "", "note": "",
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

    def test_contract_number_makes_no_record(self):  # R2-01
        cookie, _ = self.signup("first-run-contract@example.com")
        res, data = self.request("POST", "/api/jobs", new_job_body(contract_number="C-100"), cookie)
        self.assertEqual(res.status, 200, data)
        job = data["jobs"][-1]
        self.assertEqual(job["contract_number"], "C-100", "the number is kept on the job and prints from there")
        self.assertEqual(job["records"], [], "no placeholder contract record")

    def test_notes_stay_on_the_job_record(self):  # R1-04
        cookie, _ = self.signup("first-run-notes@example.com")
        res, data = self.request("POST", "/api/jobs", new_job_body(name="Plain deck", customer="Lee Family", approver="", ap_email=""), cookie)
        self.assertEqual(res.status, 200, data)
        res, data = self.request("POST", "/api/jobs", new_job_body(note="Gate code 1234, call before 8am"), cookie)
        self.assertEqual(res.status, 200, data)
        job = data["jobs"][-1]
        self.assertIn({"kind": "note", "label": "Job notes", "note": "Gate code 1234, call before 8am"},
                      [{k: r[k] for k in ("kind", "label", "note")} for r in job["records"]])
        res, data = self.request("POST", f"/api/jobs/{job['id']}/done", {"notes": ""}, cookie)
        packets = {j["name"]: [p["label"] for p in j["invoices"][-1]["packet"]] for j in data["jobs"] if j["invoices"]}
        res, data = self.request("POST", f"/api/jobs/{data['jobs'][0]['id']}/done", {"notes": ""}, cookie)
        default = [p["label"] for p in data["jobs"][0]["invoices"][-1]["packet"]]
        self.assertEqual(packets["Porch rebuild"], default, "the notes did not become the paperwork list")
        self.assertNotIn("Gate code 1234", default)

    def test_email_asked_for_at_send(self):  # R1-05
        cookie, _ = self.signup("first-run-email@example.com")
        res, data = self.request("POST", "/api/jobs", new_job_body(approver="", ap_email=""), cookie)
        self.assertEqual(res.status, 200, data)
        job = data["jobs"][-1]
        payer = next(p for p in data["payers"] if p["id"] == job["payer_id"])
        self.assertEqual((payer["approver"], payer["ap_email"]), ("Dana Homeowner", ""))
        res, data = self.request("POST", f"/api/jobs/{job['id']}/done", {"notes": ""}, cookie)
        for item in self.job(data, job["id"])["invoices"][-1]["packet"]:
            self.request("POST", f"/api/jobs/{job['id']}/resolve/{item['id']}", {"reason": "Handed over on site"}, cookie)
        res, data = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie)
        self.assertEqual(res.status, 409, data)
        self.assertIn("Add the email pay apps go to", data["error"])
        self.assertIn("ap_email", data["fields"])
        res, data = self.request("POST", f"/api/payers/{payer['id']}/update", {"ap_email": "dana@example.com"}, cookie)
        self.assertEqual(res.status, 200, data)
        res, data = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie)
        self.assertEqual(res.status, 200, data)
        self.assertEqual(self.job(data, job["id"])["invoices"][-1]["status"], "sent")

    def test_paperwork_on_the_job_and_items_not_needed(self):  # R1-08
        cookie, _ = self.signup("first-run-paperwork@example.com")
        res, data = self.request("POST", "/api/jobs", new_job_body(), cookie)
        job_id = data["jobs"][-1]["id"]
        self.request("POST", f"/api/jobs/{job_id}/add-record", {"label": "Signed contract PDF", "kind": "contract", "note": ""}, cookie)
        res, data = self.request("POST", f"/api/jobs/{job_id}/progress", {"completed_cents": 2000000, "notes": ""}, cookie)
        self.assertEqual(res.status, 200, data)
        job = self.job(data, job_id)
        packet = {p["label"]: p for p in job["invoices"][-1]["packet"]}
        self.assertTrue(packet["Signed contract"]["present"], "the contract on the job record is attached")
        self.assertNotIn("Signed contract", job["missing_paperwork"])
        self.assertEqual(len(job["missing_paperwork"]), len(job["blockers"]), "only paperwork blocks it")

        # "Not needed for this customer": it stops blocking and the next pay app does not ask.
        photos = packet["Completion photos"]
        res, data = self.request("POST", f"/api/jobs/{job_id}/packet-skip/{photos['id']}", {}, cookie)
        self.assertEqual(res.status, 200, data)
        job = self.job(data, job_id)
        self.assertNotIn("Completion photos", job["missing_paperwork"])
        payer = next(p for p in data["payers"] if p["id"] == job["payer_id"])
        self.assertNotIn("photos", payer["packet"].lower())
        for label in job["missing_paperwork"]:
            item = next(p for p in job["invoices"][-1]["packet"] if p["label"] == label)
            self.request("POST", f"/api/jobs/{job_id}/resolve/{item['id']}", {"reason": "Handed over on site"}, cookie)
        res, data = self.request("POST", f"/api/jobs/{job_id}/send", {}, cookie)
        self.assertEqual(res.status, 200, data)
        res, data = self.request("POST", f"/api/jobs/{job_id}/progress", {"completed_cents": 3000000, "notes": ""}, cookie)
        self.assertEqual(res.status, 200, data)
        second = self.job(data, job_id)["invoices"][-1]
        self.assertEqual(second["number"][-2:], "02")
        self.assertNotIn("Completion photos", [p["label"] for p in second["packet"]])

    def test_every_item_not_needed_stays_that_way(self):  # R1-08, review: an emptied list must not bring the default back
        cookie, _ = self.signup("first-run-no-paperwork@example.com")
        res, data = self.request("POST", "/api/jobs", new_job_body(), cookie)
        job_id = data["jobs"][-1]["id"]
        res, data = self.request("POST", f"/api/jobs/{job_id}/progress", {"completed_cents": 2000000, "notes": ""}, cookie)
        for item in self.job(data, job_id)["invoices"][-1]["packet"]:
            if not item["present"]:
                res, data = self.request("POST", f"/api/jobs/{job_id}/packet-skip/{item['id']}", {}, cookie)
                self.assertEqual(res.status, 200, data)
        job = self.job(data, job_id)
        self.assertEqual(job["missing_paperwork"], [])
        res, data = self.request("POST", f"/api/jobs/{job_id}/send", {}, cookie)
        self.assertEqual(res.status, 200, data)
        res, data = self.request("POST", f"/api/jobs/{job_id}/progress", {"completed_cents": 3000000, "notes": ""}, cookie)
        self.assertEqual(res.status, 200, data)
        job = self.job(data, job_id)
        self.assertEqual([p for p in job["invoices"][-1]["packet"] if p["required"] and not p["present"]], [],
                         "the second pay app asks for nothing")
        # Typing a list into Customer settings again brings paperwork back.
        res, data = self.request("POST", f"/api/payers/{job['payer_id']}/update", {"packet": "Lien waiver."}, cookie)
        self.assertEqual(res.status, 200, data)
        payer = next(p for p in data["payers"] if p["id"] == job["payer_id"])
        self.assertNotIn("no_paperwork", payer)


class FirstRunWithMailTests(ServerCase):
    """On a server that sends mail: an unconfirmed email can't send, and says so (R1-07)."""

    def test_confirm_your_email_before_the_first_send(self):
        self.mail_on()
        cookie, signed = self.signup("first-run-mail@example.com")
        self.assertEqual(signed["verification"], "sent")
        self.wait_for_mail(1)
        _, token = self.link_token("verify")
        res, data = self.request("POST", "/api/jobs", new_job_body(), cookie)
        job_id = data["jobs"][-1]["id"]
        res, data = self.request("POST", f"/api/jobs/{job_id}/done", {"notes": ""}, cookie)
        for item in next(j for j in data["jobs"] if j["id"] == job_id)["invoices"][-1]["packet"]:
            self.request("POST", f"/api/jobs/{job_id}/resolve/{item['id']}", {"reason": "Handed over on site"}, cookie)
        res, data = self.request("POST", f"/api/jobs/{job_id}/send", {}, cookie)
        self.assertEqual(res.status, 409, data)
        self.assertIn("Confirm your email first so the pay app goes out from you", data["error"])
        _, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertIn(next(j for j in state["jobs"] if j["id"] == job_id)["invoices"][-1]["status"], ("draft", "awaiting_approval"))
        res, data = self.request("POST", "/api/auth/verify", {"token": token})
        self.assertEqual(res.status, 200, data)
        res, data = self.request("POST", f"/api/jobs/{job_id}/send", {}, cookie)
        self.assertEqual(res.status, 200, data)


if __name__ == "__main__":
    unittest.main()
