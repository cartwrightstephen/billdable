"""What the Nov 27 build-list audit found and fixed: the first pay app moved to the business day
before can't land on or before the job's start; a finished job isn't asked to record work for a
change order; and making a hard stop a check takes the permission an override takes."""

import sys
import unittest
from datetime import date
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
from test_money import new_job, new_state
from test_accounts import ServerCase
import test_team  # a module, so its tests aren't run again here


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

    def test_taking_a_hard_stop_off_the_list_needs_the_send_permission(self):
        with self.assertRaises(domain.GateError) as ctx:
            domain.set_job_paperwork(self.state, self.job["id"], {"packet": "Conditional waiver, progress photos."}, "Sam", may_override=False)
        self.assertEqual(str(ctx.exception), domain.OVERRIDE_PERM)
        self.assertNotIn("paperwork_labels", self.job, "nothing changed")
        with self.assertRaises(domain.GateError):
            domain.set_job_paperwork(self.state, self.job["id"], {"no_paperwork": True}, "Sam", may_override=False)
        # Adding, or taking off a check, needs none.
        domain.set_job_paperwork(self.state, self.job["id"], {"packet": "Signed contract, conditional waiver, insurance certificate."}, "Sam", may_override=False)
        self.assertIn("Insurance certificate", self.job["paperwork_labels"])
        # Back to the client's list would drop the insurance certificate (a hard stop): refused too.
        with self.assertRaises(domain.GateError):
            domain.set_job_paperwork(self.state, self.job["id"], {"use_client": True}, "Sam", may_override=False)
        domain.set_job_paperwork(self.state, self.job["id"], {"packet": "Conditional waiver, progress photos."}, "Owner", may_override=True)
        self.assertNotIn("Signed contract", self.job["paperwork_labels"])


class TicketItemsSplitTests(unittest.TestCase):
    """Item 16: the item added for change-order or unit-price work is two, daily tickets and time
    sheets, each with the gates' defaults (checks) and the change-order hard stop."""
    def run_rule(self, covers, packet=None, payer=None):
        inv = {"id": "inv-abc123", "packet": list(packet or [])}
        with mock.patch.object(domain, "_work_billed_by_ticket", return_value=covers):
            added = domain._add_ticket_item({"id": "j1", "paperwork_gates": {}}, inv, payer)
        return added, inv["packet"]

    def test_two_items_checks_for_unit_price_work(self):
        added, packet = self.run_rule(["Pavers"])
        self.assertTrue(added)
        self.assertEqual([(p["kind"], p["label"]) for p in packet], [("ticket", "Daily tickets"), ("time_sheet", "Time sheets")])
        self.assertEqual({domain.item_gate({}, p) for p in packet}, {"soft"})
        self.assertEqual({tuple(p["covers"]) for p in packet}, {("Pavers",)})
        self.assertEqual(len({p["id"] for p in packet}), 2)

    def test_both_hard_stops_for_change_order_work(self):
        _, packet = self.run_rule(["Pavers", "CO 1"])
        self.assertEqual([domain.item_gate({}, p) for p in packet], ["hard", "hard"])

    def test_one_the_list_has_takes_the_work_and_the_other_is_added(self):
        tickets = {"id": "pkt-1", "kind": "ticket", "label": "Daily tickets", "required": True, "present": False, "attachments": []}
        added, packet = self.run_rule(["CO 2"], [tickets])
        self.assertEqual([p["label"] for p in packet], ["Daily tickets", "Time sheets"])
        self.assertEqual(tickets["gate_fixed"], "hard")
        combined = {"id": "pkt-2", "kind": "ticket", "label": "Daily tickets and time sheets", "required": True, "present": False, "attachments": []}
        added, packet = self.run_rule(["CO 2"], [combined])
        self.assertFalse(added)
        self.assertEqual([p["label"] for p in packet], ["Daily tickets and time sheets"], "an older combined item covers both")

    def test_a_client_that_doesnt_want_them_gets_both_as_not_needed(self):
        added, packet = self.run_rule(["Pavers"], payer={"tickets_not_needed": {"reason": "Lump sum"}})
        self.assertFalse(added)
        self.assertEqual([(p["required"], p["skipped_reason"]) for p in packet], [(False, "Lump sum"), (False, "Lump sum")])


class PredictedPayTermsTests(unittest.TestCase):
    """Items 24/31: a predicted pay date from payment terms moves off weekends and holidays, as the due date does."""
    def test_terms_land_on_a_business_day(self):
        state = {"payers": [{"id": "p-new", "name": "New", "usual_approval_days": 14, "payment_terms_days": 30}], "jobs": []}
        for sent, want in (("2026-11-06", "2026-12-07"),    # Dec 6, 2026 is a Sunday
                           ("2026-11-25", "2026-12-28"),    # Dec 25 is Christmas, a Friday
                           ("2026-11-10", "2026-12-10")):   # a Thursday stays
            inv = {"id": "inv-1", "number": "PA-1", "status": "sent", "sent_at": f"{sent}T12:00:00+00:00", "amount_cents": 100}
            job = {"id": "j", "payer_id": "p-new", "invoices": [inv], "payments": []}
            p = domain.predicted_pay(state, job, inv, today=date(2026, 11, 27))
            self.assertEqual((p["on"], p["basis"]), (want, "terms"), sent)


class LumpSumEarlierPayAppsTests(unittest.TestCase):
    """Item 27: a lump-sum job with no schedule of values starts mid-project too: the contract
    amount becomes its one line, as a new job's is."""
    def test_a_job_without_a_schedule_takes_its_earlier_pay_apps(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        job.pop("sov", None)
        domain.import_earlier_payapps(state, job["id"], {"summary": {"number": "3", "period_to": "2026-10-31",
                                                                      "lines": {"contract": {"to_date": "4000"}}, "paid": "3600", "paid_on": "2026-11-10"}})
        import sov
        self.assertTrue(sov.has_sov(job))
        line = sov.lines(job)[0]
        self.assertEqual((line["scheduled_cents"], line["description"], line.get("auto")), (1_000_000, "Contract amount", True))
        (earlier,) = domain.imported_invoices(job)
        self.assertEqual(earlier["gross_cents"], 400_000)
        self.assertTrue(earlier["number"].endswith("03"))


class ZipStateTests(unittest.TestCase):
    """Item 4: the server asks the page's question about a ZIP from another state; never refuses."""
    def test_the_question_matches_the_page(self):
        from domain.validate import zip_state_question
        self.assertEqual(zip_state_question("33909", "GA"), "ZIP 33909 is in Florida, not Georgia. Check the state or the ZIP.")
        self.assertEqual(zip_state_question("30303", "ga"), "")
        self.assertEqual(zip_state_question("", "GA"), "")
        self.assertEqual(zip_state_question("33909", ""), "")
        self.assertEqual(zip_state_question("09012", "GA"), "", "military mail isn't questioned")
        self.assertEqual(zip_state_question("96910-1234", "GU"), "")

    def test_a_contract_with_a_zip_from_another_state_says_so(self):
        import contract_import
        out = contract_import.suggest("Project: Oak kitchen\nProject address: 12 Peach St, Atlanta, GA 33909\n", "Pine Co")
        self.assertEqual(out["fields"]["zip"], "33909")
        self.assertIn("Florida, not Georgia", out["warnings"]["zip"])
        self.assertNotIn("warnings", contract_import.suggest("Project address: 12 Peach St, Atlanta, GA 30303\n", "Pine Co"))


class ZipStateHttpTests(ServerCase):
    def test_job_and_company_saves_carry_the_warning_and_still_save(self):
        owner, _ = self.signup("owner@zip.example", "Zip Co")
        res, out = self.request("POST", "/api/jobs", {**test_team.JOB, "street": "1 Main St", "city": "Atlanta", "state": "GA", "zip": "33909"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(out["warnings"]["zip"], "ZIP 33909 is in Florida, not Georgia. Check the state or the ZIP.")
        job_id = out["jobs"][0]["id"]
        res, out = self.request("POST", f"/api/jobs/{job_id}/edit", {"street": "1 Main St", "city": "Atlanta", "state": "GA", "zip": "30303"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertNotIn("warnings", out)
        res, out = self.request("POST", f"/api/jobs/{job_id}/edit", {"street": "1 Main St", "city": "Atlanta", "state": "GA", "zip": "10001"}, cookie=owner)
        self.assertIn("New York, not Georgia", out["warnings"]["zip"])
        res, out = self.request("POST", "/api/company", {"name": "Zip Co", "street": "1 Main St", "city": "Atlanta", "state": "GA", "zip": "33909"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(out["company"]["zip"], "33909", "saved: a warning, not a refusal")
        self.assertIn("Florida, not Georgia", out["warnings"]["zip"])


class GateChangeHttpTests(ServerCase):
    """The server passes the send permission through to the gate change."""
    join = test_team.TeamHttpTests.join

    def test_a_profile_without_send_cant_make_the_contract_a_check(self):
        owner, _ = self.signup("owner@gates.example", "Gates Co")
        res, out = self.request("POST", "/api/team/profiles", {"name": "Job runner", "perms": ["manage_jobs"]}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        cookie, _ = self.join(owner, "jo@gates.example", out["profile"]["id"])
        _, state = self.request("POST", "/api/jobs", test_team.JOB, cookie=owner)
        job_id = state["jobs"][0]["id"]
        res, out = self.request("POST", f"/api/jobs/{job_id}/paperwork", {"gates": {"contract": "soft"}}, cookie=cookie)
        self.assertEqual(res.status, 409, out)
        self.assertIn("can send pay apps", out["error"])
        res, out = self.request("POST", f"/api/jobs/{job_id}/paperwork", {"gates": {"contract": "soft"}}, cookie=owner)
        self.assertEqual(res.status, 200, out)


if __name__ == "__main__":
    unittest.main()
