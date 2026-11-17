"""Round 1, after the three parts were merged: a Member sets up a new job's contract from an
imported schedule (the contract gate is for an existing job's), a lump sum's % done to date on a
New job record records the step up from the field total, to the cent, and the client card's Usual
retainage % takes the company permission."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import server
import sov
from test_accounts import ServerCase
from test_money import new_job, new_state
import test_team

# Two lines as the contract reader hands them over (contract_import._sov), $50,000 in all.
IMPORTED = [{"item": "1", "description": "Framing", "quantity": "1", "unit": "LS", "rate": "30000"},
            {"item": "2", "description": "Drywall", "quantity": "1", "unit": "LS", "rate": "20000"}]
NEW_JOB = {"name": "Oak kitchen", "customer": "Northline GC", "street": "9 Oak St", "city": "Atlanta", "state": "GA", "zip": "30307",
           "approver": "Sam", "ap_email": "ap@northline.example", "phone": "(404) 555-0100", "client_type": "gc", "approver_confirmed": True}


class NewJobContractHttpTests(ServerCase):
    join = test_team.TeamHttpTests.join

    def setUp(self):
        super().setUp()
        tag = self._testMethodName.replace("_", "")[-20:]
        self.owner, _ = self.signup(f"owner-{tag}@merge.example", "Merge Co")
        self.member, _ = self.join(self.owner, f"fred-{tag}@merge.example")

    def test_a_member_creates_a_job_whose_contract_is_the_imported_schedules_total(self):
        res, out = self.request("POST", "/api/jobs", {**NEW_JOB, "contract_cents": 5_000_000, "retainage_pct": "", "sov_lines": IMPORTED}, cookie=self.member)
        self.assertEqual(res.status, 200, out)
        job = next(j for j in out["jobs"] if j["name"] == "Oak kitchen")
        self.assertEqual(job["money"]["contract_cents"], 5_000_000)
        self.assertEqual([l["description"] for l in job["sov_view"]["lines"]], ["Framing", "Drywall"])
        self.assertFalse(job["sov_view"]["auto"], "the contract's lines, not the lump-sum line")
        self.assertEqual(job["retainage_pct"], 10, "blank retainage: a general contractor's usual")
        # Changing that job's contract afterwards is still the gate's.
        res, out = self.request("POST", f"/api/jobs/{job['id']}/sov", {"lines": IMPORTED[:1], "update_contract": True}, cookie=self.member)
        self.assertEqual((res.status, out.get("error")), (403, server.NO_PERMISSION))

    def test_a_schedule_that_misses_the_contract_saves_nothing(self):
        res, out = self.request("POST", "/api/jobs", {**NEW_JOB, "contract_cents": 4_850_000, "sov_lines": IMPORTED}, cookie=self.member)
        self.assertEqual(res.status, 409, out)
        self.assertIn("sov", out["fields"])
        res, state = self.request("GET", "/api/state", cookie=self.owner)
        self.assertFalse([j for j in state["jobs"] if j["name"] == "Oak kitchen"])

    def test_usual_retainage_takes_the_company_permission(self):
        _, out = self.request("POST", "/api/jobs", {**NEW_JOB, "contract_cents": 1_000_000}, cookie=self.owner)
        payer = out["payers"][0]
        res, out = self.request("POST", f"/api/payers/{payer['id']}/update", {"retainage_pct": "5"}, cookie=self.member)
        self.assertEqual(res.status, 403, out)
        res, out = self.request("POST", f"/api/payers/{payer['id']}/update", {"retainage_pct": "5"}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(out["payers"][0]["retainage_default"], 5)
        res, out = self.request("POST", "/api/jobs", {**NEW_JOB, "name": "Deck", "contract_cents": 1_000_000, "retainage_pct": ""}, cookie=self.member)
        self.assertEqual(next(j for j in out["jobs"] if j["name"] == "Deck")["retainage_pct"], 5)
        res, out = self.request("POST", "/api/jobs", {**NEW_JOB, "name": "Porch", "contract_cents": 1_000_000, "retainage_pct": 0}, cookie=self.member)
        self.assertEqual(next(j for j in out["jobs"] if j["name"] == "Porch")["retainage_pct"], 0, "a typed 0 stays 0")


def to_date(pct, line):
    return {"sections": {"quantities": [{"line_id": line["id"], "qty": pct, "to_date": True}]}}


class LumpSumToDateTests(unittest.TestCase):
    """New job record asks a lump sum's % done to date; what it adds is the step up from the field total."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=4_850_000, auto_schedule=True)
        self.line = sov.lines(self.job)[0]

    def view_line(self):
        return domain.job_view(self.state, self.job)["sov_view"]["lines"][0]

    def test_each_record_adds_the_step_up_to_the_cent(self):
        domain.add_daily_record(self.state, self.job["id"], "Framing", details=to_date("25", self.line))
        self.assertEqual(sov.field_total(self.line), 250)
        self.assertEqual(self.view_line()["to_date_cents"], 1_212_500)
        domain.add_daily_record(self.state, self.job["id"], "Drywall", details=to_date("60", self.line))
        self.assertEqual(sov.field_total(self.line), 600)
        self.assertEqual(self.view_line()["to_date_cents"], 2_910_000)
        rec = self.state["daily_records"][-1]
        self.assertEqual(rec["quantities_added"], [{"line_id": self.line["id"], "qty_milli": 350}], "35% more, not 60%")
        self.assertIn("Contract amount 60% done to date (+35%)", domain.daily.daily_record_note(self.job, rec))
        domain.add_daily_record(self.state, self.job["id"], "Trim", details=to_date("93.3", self.line))
        self.assertEqual(self.view_line()["to_date_cents"], 4_525_050, "93.3% of $48,500.00 is $45,250.50")
        # Progress pay app bills what the field recorded, to the cent.
        domain.record_sov_progress(self.state, self.job["id"], {})
        draft = next(i for i in self.job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        self.assertEqual(draft["amount_cents"], 4_525_050, "no retainage on this job")

    def test_below_the_field_total_is_refused_toward_correct_field_total(self):
        domain.add_daily_record(self.state, self.job["id"], "Framing", details=to_date("40", self.line))
        before = len(self.state["daily_records"])
        with self.assertRaises(domain.GateError) as refused:
            domain.add_daily_record(self.state, self.job["id"], "Oops", details=to_date("30", self.line))
        message = refused.exception.fields[f"qty_{self.line['id']}"]
        self.assertEqual(message, "Contract amount is at 40% done already. Enter that or more, or lower it with Correct field total on the job's Schedule of values card.")
        self.assertEqual(len(self.state["daily_records"]), before, "nothing saved")
        self.assertEqual(sov.field_total(self.line), 400)

    def test_the_same_figure_again_adds_nothing_and_still_saves_the_diary(self):
        domain.add_daily_record(self.state, self.job["id"], "Framing", details=to_date("40", self.line))
        domain.add_daily_record(self.state, self.job["id"], "Rain day", details=to_date("40", self.line))
        self.assertEqual(sov.field_total(self.line), 400)
        self.assertNotIn("quantities", self.state["daily_records"][-1].get("sections") or {})

    def test_past_100_percent_is_kept_on_the_record_not_added(self):
        domain.add_daily_record(self.state, self.job["id"], "Framing", details=to_date("90", self.line))
        domain.add_daily_record(self.state, self.job["id"], "Over", details=to_date("120", self.line))
        self.assertEqual(sov.field_total(self.line), 1000)
        self.assertEqual(self.view_line()["to_date_cents"], 4_850_000)
        self.assertTrue(self.state["daily_records"][-1]["quantities_not_applied"])

    def test_unit_lines_stay_quantities_done_today_and_old_pages_keep_percent_today(self):
        job = new_job(self.state, name="SOV job", contract=3_450_000)
        domain.save_sov(self.state, job["id"], [{"item": "1", "description": "Rough-in wiring", "quantity": "400", "unit": "ea", "rate": "45.00"},
                                                 {"item": "2", "description": "Panel and feeders", "quantity": "1", "unit": "LS", "rate": "12000"}], "15")
        wiring, panel = sov.lines(job)
        for _ in range(2):   # to_date on a unit line is ignored: it is today's quantity
            domain.add_daily_record(self.state, job["id"], "Wire", details={"sections": {"quantities": [{"line_id": wiring["id"], "qty": "120", "to_date": True}]}})
        self.assertEqual(sov.field_total(wiring), 240_000)
        # A page from before sends a lump sum's % done today, without to_date: it adds as it did.
        for _ in range(2):
            domain.add_daily_record(self.state, job["id"], "Panel", details={"sections": {"quantities": [{"line_id": panel["id"], "qty": "25"}]}})
        self.assertEqual(sov.field_total(panel), 500)


if __name__ == "__main__":
    unittest.main()
