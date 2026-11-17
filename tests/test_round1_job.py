"""Round 1 (Nov 5 audits): billing a one-line schedule in dollars (item 10) and the job near-match
check at New job (item 20)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_accounts import ServerCase

JOB = {"name": "Peachtree Clinic electrical", "customer": "Northline GC", "street": "1 Elm St", "city": "Atlanta", "state": "GA", "zip": "30303",
       "approver": "Sam", "ap_email": "ap@northline.example", "contract_cents": 4_850_000, "retainage_pct": 10, "client_type": "gc",
       "phone": "(404) 555-0100"}


class Round1JobHttpTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.owner, _ = self.signup(f"owner-{self._testMethodName[-24:]}@r1.example", "R1 Co")

    def new_job(self, **over):
        res, state = self.request("POST", "/api/jobs", {**JOB, **over}, cookie=self.owner)
        self.assertEqual(res.status, 200, state)
        return state

    def job_in(self, job_id):
        _, state = self.request("GET", "/api/state", cookie=self.owner)
        return next(j for j in state["jobs"] if j["id"] == job_id)

    def test_dollars_on_a_one_line_schedule_bill_exactly_what_was_typed(self):
        state = self.new_job()
        job = state["jobs"][0]
        line = job["sov_view"]["lines"][0]
        # $10,000 of $48,500 is 20.6185...%: the line goes to 20.6% and the pay app bills $10,000.00.
        res, out = self.request("POST", f"/api/jobs/{job['id']}/sov-progress", {"done": {line["id"]: "0.206"}, "amounts": {line["id"]: "10000.00"}},
                                cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        view = self.job_in(job["id"])
        draft = next(i for i in view["invoices"] if i["status"] in ("draft", "awaiting_approval"))
        self.assertEqual(draft.get("gross_cents", draft["amount_cents"] + draft.get("retainage_cents", 0)), 1_000_000)
        self.assertEqual(draft["retainage_cents"], 100_000)
        # Typed again at the figure the quantity already bills: no amount is changed by hand.
        res, out = self.request("POST", f"/api/jobs/{job['id']}/sov-progress", {"done": {line["id"]: "0.500"}, "amounts": {line["id"]: "24250.00"}},
                                cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        view = self.job_in(job["id"])
        drafts = [i for i in view["invoices"] if i["status"] in ("draft", "awaiting_approval")]
        self.assertEqual([d["number"] for d in drafts], [draft["number"]], "the same draft, redrafted")
        self.assertEqual(drafts[0].get("gross_cents"), 2_425_000)

    def test_dollars_past_the_line_are_refused_and_nothing_is_saved(self):
        state = self.new_job()
        job = state["jobs"][0]
        line = job["sov_view"]["lines"][0]
        res, out = self.request("POST", f"/api/jobs/{job['id']}/sov-progress", {"done": {line["id"]: "0.500"}, "amounts": {line["id"]: "60000.00"}},
                                cookie=self.owner)
        self.assertEqual(res.status, 409, out.get("error"))
        self.assertEqual(self.job_in(job["id"])["invoices"], [], "no draft was left behind")

    def test_a_second_job_with_the_same_name_is_asked_about(self):
        state = self.new_job()
        first = state["jobs"][0]
        for typed in ("Peachtree Clinic electrical", "peachtree clinic  Electrical."):
            res, out = self.request("POST", "/api/jobs", {**JOB, "name": typed}, cookie=self.owner)
            self.assertEqual(res.status, 409, out.get("error"))
            self.assertEqual(out["same_job"], {"id": first["id"], "name": "Peachtree Clinic electrical", "typed": typed})
            self.assertIn("You already have a job called Peachtree Clinic electrical", out["fields"]["name"])
        res, out = self.request("POST", "/api/jobs", {**JOB, "new_job": True}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(sorted(j["code"] for j in out["jobs"]), ["PEAC", "PEAC2"])
        # A different name is not asked about.
        res, out = self.request("POST", "/api/jobs", {**JOB, "name": "Peachtree Clinic electrical, phase 2"}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)


if __name__ == "__main__":
    unittest.main()
