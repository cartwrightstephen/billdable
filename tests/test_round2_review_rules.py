"""Fixes from the review of Duracash Round 2 (Nov 17), money and job rules: "Mark job done" from a
message with an earlier pay app unpaid, no $0 waiver, approvers who can't approve, overrides that
belong to their own finish, and an unpriced change order in the Final pay app preview."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import importer
from test_accounts import ServerCase
from test_money import clear_packet, new_job, new_state, send_open
from test_team import TeamHttpTests
from waiver_help import signed_waiver


def says_done_item(state, job):
    item = {"id": "inb-done", "status": "new", "kind": "note", "text": "All done on site", "from": "GC", "says_done_job_id": job["id"],
            "source": "email", "subject": "Done", "file_ids": [], "received_at": "2026-11-17T10:00:00+00:00"}
    state.setdefault("inbox", []).append(item)
    return item


class InboxMarkDoneWithAnUnpaidPayApp(unittest.TestCase):
    """Item 1: "Mark job done and draft the pay app" with an earlier pay app unpaid."""

    def test_the_message_is_filed_and_the_job_waits_for_the_reason(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000, "a")
        send_open(state, job)
        item = says_done_item(state, job)
        out = importer.file_and_mark_done(state, item["id"], "Sam")
        self.assertEqual(out, {"needs_reason": [job["invoices"][0]["number"]], "job_id": job["id"]})
        self.assertEqual(item["status"], "filed")
        self.assertEqual(item["filed_job_id"], job["id"])
        self.assertNotEqual(job["work_status"], "complete", "the Final pay app form asks for the reason")
        # With the reason, from the job's form, it goes as before.
        domain.mark_done(state, job["id"], "Sam", unpaid_reason="GC asked for it")
        self.assertEqual(job["work_status"], "complete")

    def test_with_nothing_unpaid_it_still_marks_done_in_one_click(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        item = says_done_item(state, job)
        importer.file_and_mark_done(state, item["id"], "Sam")
        self.assertEqual(job["work_status"], "complete")


class NoZeroWaiver(unittest.TestCase):
    """Item 2: a part payment's waiver covers all that came in, and the rest is settled short."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = send_open(self.state, self.job)
        domain.match_payment(self.state, self.job["id"], 150_000, "c1", invoice_id=self.inv["id"])
        signed_waiver(self.state, self.job["id"], "unconditional_progress")
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"])

    def test_settled_short_owes_no_waiver(self):
        domain.settle_short_pay(self.state, self.job["id"], self.inv["id"], "Backcharge", "Sam")
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 0)
        self.assertFalse(self.inv["waiver_due"])
        self.assertEqual(self.job["external_status"], "Short pay accepted")
        self.assertNotIn("A lien waiver is still due.", domain.close_blockers(self.job))
        self.assertEqual([r for r in domain.today_rows(self.state) if r["kind"] == "waiver"], [])

    def test_the_rest_paid_still_owes_its_waiver(self):
        domain.match_payment(self.state, self.job["id"], 250_000, "c2", invoice_id=self.inv["id"])
        self.assertTrue(self.inv["waiver_due"])
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 250_000)


class Approvers(unittest.TestCase):
    """Item 3b: removing someone never leaves a client's own list empty by accident."""
    POOL = {"u-ana": "Ana Ruiz", "u-ben": "Ben Ode"}

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.record_progress(self.state, self.job["id"], 300_000, "a")
        self.inv = clear_packet(self.state, self.job)

    def test_a_client_list_emptied_by_a_removal_falls_back_to_the_company(self):
        domain.set_payapp_approvers(self.state, ["u-ana"], self.POOL)
        domain.set_payapp_approvers(self.state, ["u-ben"], self.POOL, payer_id=self.job["payer_id"])
        domain.drop_approver(self.state, "u-ben")
        self.assertNotIn("payapp_approvers", self.state["payers"][0])
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), ["Ana Ruiz"])
        self.assertIn("company's approvers apply", self.state["audit"][-1]["detail"])
        with self.assertRaises(domain.GateError):
            domain.approve_and_send(self.state, self.job["id"], "Owner")

    def test_a_list_saved_empty_on_purpose_stays_nobody(self):
        domain.set_payapp_approvers(self.state, ["u-ana"], self.POOL)
        domain.set_payapp_approvers(self.state, [], self.POOL, payer_id=self.job["payer_id"])
        domain.drop_approver(self.state, "u-ana")
        self.assertEqual(self.state["payers"][0]["payapp_approvers"], [])
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), [])

    def test_someone_without_the_permission_comes_off(self):
        domain.set_payapp_approvers(self.state, ["u-ana", "u-ben"], self.POOL)
        domain.drop_approvers_without(self.state, {"u-ben": "Ben Ode"})
        self.assertEqual([p["id"] for p in self.state["company"]["payapp_approvers"]], ["u-ben"])
        self.assertIn("Ana Ruiz no longer holds Approve pay apps", self.state["audit"][-1]["detail"])


class ApproversHttp(ServerCase):
    """Item 3a: a profile change or a permissions edit takes the approver off the lists."""
    join = TeamHttpTests.join

    def setUp(self):
        super().setUp()
        tag = self._testMethodName.replace("_", "")[-16:]
        self.tag = tag
        self.owner, _ = self.signup(f"owner-{tag}@appr.example", "Approve Co")
        res, out = self.request("POST", "/api/team/profiles", {"name": "Project manager", "perms": ["approve_payapps"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.profile = out["profile"]
        _, self.pm = self.join(self.owner, f"pam-{tag}@appr.example", self.profile["id"])
        res, out = self.request("POST", "/api/approvers", {"user_ids": [self.pm["id"]]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)

    def approvers(self):
        _, state = self.request("GET", "/api/state", cookie=self.owner)
        return [p["id"] for p in state["company"].get("payapp_approvers", [])]

    def test_a_new_profile_without_it(self):
        res, out = self.request("POST", f"/api/team/{self.pm['id']}/role", {"profile_id": "member"}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.approvers(), [])

    def test_the_permission_taken_off_the_profile(self):
        res, out = self.request("POST", f"/api/team/profiles/{self.profile['id']}", {"name": "Project manager", "perms": ["record_payments"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.approvers(), [])

    def test_a_change_that_keeps_it_keeps_them(self):
        res, out = self.request("POST", f"/api/team/profiles/{self.profile['id']}", {"name": "PM", "perms": ["approve_payapps", "record_payments"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.approvers(), [self.pm["id"]])


class OverridesBelongToTheirFinish(unittest.TestCase):
    """Items 4 and 5: the final-pay-app and closeout overrides don't outlive the finish they were for."""

    def finished_with_overrides(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000, "a")
        p1 = send_open(state, job)
        domain.mark_done(state, job["id"], "Sam", unpaid_reason="Client asked for it")
        fin = send_open(state, job)
        self.assertTrue(fin.get("final_override"), "the final pay app shows the override")
        for inv in (p1, fin):
            domain.match_payment(state, job["id"], inv["amount_cents"], "r" + inv["id"], invoice_id=inv["id"])
            signed_waiver(state, job["id"], "unconditional_final" if inv is fin else "unconditional_progress", "w" + inv["id"])
            domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"])
        self.assertTrue(domain.closeout_override_needed(job))
        domain.close_job(state, job["id"], "Sam", closeout_override="Owner waived")
        self.assertTrue(job.get("closeout_override"))
        return state, job

    def test_a_pay_app_after_a_reopen_carries_no_final_override(self):
        state, job = self.finished_with_overrides()
        domain.reopen_job(state, job["id"], "More work: backsplash", "Sam")
        self.assertNotIn("final_override", job)
        domain.add_change_order(state, job["id"], "Backsplash", 50_000, reason=None)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"], "Sam")
        domain.draft_invoice(state, job["id"], actor="Sam")
        self.assertNotIn("final_override", job["invoices"][-1])
        self.assertTrue(any(a["action"] == "final_override" for a in state["audit"]), "the audit log keeps it")

    def test_a_clean_finish_after_a_reopen_has_no_closeout_override(self):
        state, job = self.finished_with_overrides()
        domain.reopen_job(state, job["id"], "Late warranty paperwork", "Sam")
        self.assertNotIn("closeout_override", job)
        for item in job["closeout_packet"]:
            item["present"] = True
        domain.close_job(state, job["id"], "Sam")
        self.assertTrue(job["closed"])
        self.assertNotIn("closeout_override", job)
        self.assertTrue(any(a["action"] == "closeout_override" for a in state["audit"]), "the audit log keeps it")

    def test_a_clean_close_clears_an_old_override(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.mark_done(state, job["id"], "Sam")
        for item in job["closeout_packet"]:
            item["present"] = True
        job["closeout_override"] = {"by": "Sam", "at": "2026-11-01T00:00:00+00:00", "reason": "old", "missing": ["x"]}
        domain.record_closeout_override(state, job, "", "Sam")   # nothing missing now
        self.assertNotIn("closeout_override", job)

    def test_undo_done_then_done_again_without_the_reason_carries_none(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000, "a")
        p1 = send_open(state, job)
        domain.mark_done(state, job["id"], "Sam", unpaid_reason="Client asked for it")
        domain.undo_mark_done(state, job["id"], "Sam")
        self.assertNotIn("final_override", job)
        domain.match_payment(state, job["id"], p1["amount_cents"], "c", invoice_id=p1["id"])
        domain.mark_done(state, job["id"], "Sam")
        self.assertNotIn("final_override", job["invoices"][-1])


class UnpricedChangeOrderInThePreview(unittest.TestCase):
    """Item 6."""

    def test_the_preview_says_it_has_no_price(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.add_change_order(state, job["id"], "Extra outlets", 0, reason="owner_request")
        number = job["change_orders"][0]["number"]
        self.assertEqual(domain.final_pay_app_preview(state, job["id"])["pending_cos"], [{"number": number, "amount_cents": 0, "unpriced": True}])


if __name__ == "__main__":
    unittest.main()
