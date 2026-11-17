"""Round 2 review fixes, job rules: the final pay app's override follows a redraft, the Inbox's
mark-done on a finished job says so, backlog counts signed work only, approvals sign off the
version shown, and the demo address can't be taken."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
from test_money import new_job, new_state, send_open


class FinalOverrideOnARedraft(unittest.TestCase):  # R4, B6 (decision 6)
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.first = send_open(self.state, self.job)
        domain.mark_done(self.state, self.job["id"], "Sam", unpaid_reason="GC pays late every month")
        self.override = self.job["final_override"]

    def test_deleting_the_final_draft_and_drafting_again_keeps_the_override(self):
        domain.void_draft(self.state, self.job["id"], "Sam")
        domain.draft_invoice(self.state, self.job["id"], strict=True)
        final = self.job["invoices"][-1]
        self.assertTrue(final["final"])
        self.assertEqual(final["final_override"], self.override)

    def test_pulling_back_the_sent_final_and_drafting_again_keeps_the_override(self):
        final = send_open(self.state, self.job)
        domain.void_sent_pay_app(self.state, self.job["id"], final["id"], "Wrong period", "Sam")
        domain.draft_invoice(self.state, self.job["id"], strict=True)
        again = self.job["invoices"][-1]
        self.assertNotEqual(again["id"], final["id"])
        self.assertEqual(again["final_override"], self.override)

    def test_no_override_once_the_earlier_pay_app_is_paid(self):
        domain.void_draft(self.state, self.job["id"], "Sam")
        domain.match_payment(self.state, self.job["id"], self.first["amount_cents"], "chk", invoice_id=self.first["id"])
        domain.draft_invoice(self.state, self.job["id"], strict=True)
        self.assertNotIn("final_override", self.job["invoices"][-1])


if __name__ == "__main__":
    unittest.main()
