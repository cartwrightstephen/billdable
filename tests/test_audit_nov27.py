"""What the Nov 27 build-list audit found and fixed: the first pay app moved to the business day
before can't land on or before the job's start; a finished job isn't asked to record work for a
change order; and making a hard stop a check takes the permission an override takes."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
from test_money import new_job, new_state
from test_accounts import ServerCase
from test_team import JOB, TeamHttpTests


class FirstDueAfterStartTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, started_on="2026-11-14")   # a Saturday
        self.state["payers"][0]["payapp_due_day"] = 15            # Nov 15, 2026 is a Sunday

    def test_the_friday_before_a_weekend_cutoff_isnt_before_the_start(self):
        due = domain.next_payapp_due(self.state, self.job, today=date(2026, 11, 14))
        self.assertEqual(due, date(2026, 12, 15), "not Fri Nov 13, the day before the job began")
        dates = domain.job_key_dates(self.state, self.job, today=date(2026, 11, 14))
        self.assertFalse([d for d in dates if (d["key"].startswith("cutoff") or d["key"] == "first_payapp") and d["date"] <= "2026-11-14"], dates)
        self.assertEqual(next(d for d in dates if d["label"] == "First pay app due")["date"], "2026-12-15")

    def test_a_job_without_a_start_still_counts_from_today(self):
        self.job.pop("started_on", None)
        self.job.pop("start_date", None)
        got = domain.next_payapp_due(self.state, self.job, today=date(2026, 11, 2))
        self.assertGreaterEqual(got, date(2026, 11, 2))


class FinishedJobChangeOrderTests(unittest.TestCase):
    def test_a_finished_job_isnt_asked_to_record_change_order_work(self):
        state = new_state()
        job = new_job(state)
        job.setdefault("change_orders", []).append({"id": "co-1", "number": 1, "title": "Foundation repair", "amount_cents": 100_000,
                                                    "status": "approved", "included_in_billing": False})
        self.assertEqual([c["id"] for c in domain.cos_without_work(state, job)], ["co-1"], "open: asked")
        job["work_status"] = "complete"
        self.assertEqual(domain.cos_without_work(state, job), [], "finished: not asked")
        job["work_status"] = "in_progress"
        job["closed"] = True
        self.assertEqual(domain.cos_without_work(state, job), [])


class GateChangeTakesThePermissionTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state)
        self.state["payers"][0]["packet"] = "Signed contract, conditional waiver, progress photos."
        domain.record_progress(self.state, self.job["id"], 1_000_000)

    def test_hard_to_soft_needs_the_send_permission_soft_to_hard_doesnt(self):
        with self.assertRaises(domain.GateError) as ctx:
            domain.set_job_paperwork(self.state, self.job["id"], {"gates": {"contract": "soft"}}, "Sam", may_override=False)
        self.assertIn("can send pay apps", str(ctx.exception))
        self.assertNotIn("contract", self.job.get("paperwork_gates") or {}, "nothing changed")
        with self.assertRaises(domain.GateError):
            domain.set_job_paperwork(self.state, self.job["id"], {"gates": {"waiver": "soft", "photo": "hard"}}, "Sam", may_override=False)
        self.assertEqual(self.job.get("paperwork_gates") or {}, {}, "all or nothing")
        domain.set_job_paperwork(self.state, self.job["id"], {"gates": {"photo": "hard"}}, "Sam", may_override=False)
        self.assertEqual(self.job["paperwork_gates"], {"photo": "hard"}, "stricter needs no permission")
        with self.assertRaises(domain.GateError, msg="once required on this job, only the permission lets it go back to a check"):
            domain.set_job_paperwork(self.state, self.job["id"], {"gates": {"photo": "soft"}}, "Sam", may_override=False)
        domain.set_job_paperwork(self.state, self.job["id"], {"gates": {"photo": "soft", "contract": "soft"}}, "Owner", may_override=True)
        self.assertEqual(self.job["paperwork_gates"], {"contract": "soft"})


class GateChangeHttpTests(ServerCase):
    """The server passes the send permission through to the gate change."""
    join = TeamHttpTests.join

    def test_a_profile_without_send_cant_make_the_contract_a_check(self):
        owner, _ = self.signup("owner@gates.example", "Gates Co")
        res, out = self.request("POST", "/api/team/profiles", {"name": "Job runner", "perms": ["manage_jobs"]}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        cookie, _ = self.join(owner, "jo@gates.example", out["profile"]["id"])
        _, state = self.request("POST", "/api/jobs", JOB, cookie=owner)
        job_id = state["jobs"][0]["id"]
        res, out = self.request("POST", f"/api/jobs/{job_id}/paperwork", {"gates": {"contract": "soft"}}, cookie=cookie)
        self.assertEqual(res.status, 409, out)
        self.assertIn("can send pay apps", out["error"])
        res, out = self.request("POST", f"/api/jobs/{job_id}/paperwork", {"gates": {"contract": "soft"}}, cookie=owner)
        self.assertEqual(res.status, 200, out)


if __name__ == "__main__":
    unittest.main()
