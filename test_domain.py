import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
from seed import seed_state


class MoneyFileTests(unittest.TestCase):
    def test_oak_street_money_file_separates_unsigned_change_order(self):
        job = seed_state()["jobs"][0]
        money = domain.job_money(job)
        self.assertEqual(money["billed_cents"], 4000000)
        self.assertEqual(money["collected_cents"], 4000000)
        self.assertEqual(money["unbilled_completed_cents"], 4400000)
        self.assertEqual(money["pending_co_cents"], 640000)
        self.assertEqual(money["outstanding_cents"], 0)

    def test_status_names_the_blocker(self):
        job = seed_state()["jobs"][0]
        self.assertIn("Atlanta Millwork", domain.status_label(job))
        self.assertNotEqual(domain.status_label(job), "Pending")

    def test_send_is_gated_until_packet_and_signature_clear(self):
        state = seed_state()
        domain.draft_invoice(state, "job-oak")
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(state, "job-oak")
        self.assertGreaterEqual(len(raised.exception.blockers), 2)
        domain.resolve_packet_item(state, "job-oak", "pkt-oak-waiver")
        domain.resolve_packet_item(state, "job-oak", "pkt-oak-tickets")
        job = domain._job(state, "job-oak")
        job["change_orders"][0]["status"] = "approved"
        job["change_orders"][0]["included_in_billing"] = False
        domain._refresh_draft_gate(job)
        domain.approve_and_send(state, "job-oak", actor="owner")
        invoice = job["invoices"][-1]
        self.assertEqual(invoice["status"], "sent")
        self.assertEqual(invoice["approved_by"], "owner")

    def test_payment_match_opens_waiver(self):
        state = seed_state()
        domain.match_payment(state, "job-turner", 11970000, "QBO-20011")
        job = domain._job(state, "job-turner")
        self.assertEqual(job["invoices"][-1]["status"], "paid")
        self.assertTrue(job["waiver_due"])
        self.assertEqual(domain.status_label(job), "Payment received, waiver not sent")
        domain.send_waiver(state, "job-turner")
        self.assertTrue(job["waiver_sent"])

    def test_retainage_chase_requires_closeout_packet(self):
        state = seed_state()
        with self.assertRaises(domain.GateError):
            domain.chase_retainage(state, "job-decatur")
        domain.resolve_packet_item(state, "job-decatur", "pkt-dec-punch")
        domain.chase_retainage(state, "job-decatur")
        self.assertEqual(domain._job(state, "job-decatur")["retainage_chases"], 2)

    def test_partial_payment_does_not_mark_paid(self):
        state = seed_state()
        domain.match_payment(state, "job-turner", 5000000, "QBO-20012")
        job = domain._job(state, "job-turner")
        self.assertEqual(job["invoices"][-1]["status"], "partial")
        self.assertFalse(job["waiver_due"])
        self.assertEqual(domain.job_money(job)["outstanding_cents"], 6970000)


if __name__ == "__main__":
    unittest.main()
