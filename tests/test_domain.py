import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
from seed import seed_state


class MoneyFileTests(unittest.TestCase):
    def test_create_job_starts_a_record_not_a_packet(self):
        state = {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = domain.create_job(
            state,
            "Pine kitchen",
            "Jones Family",
            4500000,
            address="12 Pine St",
            approver="Sam Jones",
            ap_email="ap@jones.example",
            role="Owner",
            retainage_pct=10,
            contract_number="C-100",
        )
        self.assertEqual(job["contract_cents"], 4500000)
        self.assertEqual(job["packet"], [])
        self.assertEqual(job["contract_number"], "C-100")
        self.assertFalse(job["records"], "the contract number is not a job record")
        self.assertEqual(state["payers"][0]["ap_email"], "ap@jones.example")
        domain.mark_done(state, job["id"], notes="Done")
        invoice = job["invoices"][-1]
        self.assertEqual(invoice["packet_id"], invoice["id"])
        # Packet items come from the payer playbook; the closeout item gates the
        # retainage release, not the pay app (Build 2 item 1).
        self.assertEqual(len(invoice["packet"]), 4)
        self.assertNotIn("Closeout", " ".join(item["label"] for item in invoice["packet"]))
        self.assertTrue(job["closeout_packet"])

    def test_progress_pay_app_does_not_mark_done(self):
        state = {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = domain.create_job(
            state, "Pine kitchen", "Jones Family", 10000000,
            address="12 Pine St", approver="Sam Jones", ap_email="ap@jones.example",
        )
        domain.record_progress(state, job["id"], 4000000, "Framing complete")
        self.assertEqual(job["work_status"], "in_progress")
        self.assertEqual(job["invoices"][-1]["amount_cents"], 4000000)
        self.assertTrue(job["invoices"][-1]["packet"])

    def test_create_job_rejects_missing_required_fields(self):
        state = {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": []}
        with self.assertRaises(domain.GateError) as raised:
            domain.create_job(state, "", "", 0)
        self.assertGreaterEqual(len(raised.exception.blockers), 4)

    def test_create_job_names_the_bad_field(self):
        state = {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": []}
        with self.assertRaises(domain.GateError) as raised:
            domain.create_job(state, "Oak", "Oak GC", 100_000, address="1 Oak", approver="Kim", ap_email="kim at oak")
        self.assertEqual(set(raised.exception.fields), {"ap_email"})
        self.assertIn("ap@builder.com", raised.exception.fields["ap_email"])
        self.assertEqual(str(raised.exception), raised.exception.fields["ap_email"])

    def test_parse_int_is_strict_at_the_request(self):
        self.assertEqual(domain.parse_int("12", "the days", "days"), 12)
        self.assertEqual(domain.parse_int(4000000.0, "the amount", "amount_cents"), 4000000)
        self.assertEqual(domain.parse_int("", "the days", "days", 7), 7)
        for bad in ("x", "1.5", True, [3], float("inf")):
            with self.assertRaises(domain.GateError) as raised:
                domain.parse_int(bad, "the days", "days")
            self.assertEqual(list(raised.exception.fields), ["days"])
        with self.assertRaises(domain.GateError):
            domain.parse_int(None, "the days", "days")
        self.assertEqual(domain._int_or("x", 7), 7, "reading stored values stays lenient")

    def test_playbook_errors_are_keyed_by_field(self):
        state = seed_state()
        with self.assertRaises(domain.GateError) as raised:
            domain.update_payer(state, "payer-ellison", {"follow_up_days": "soon", "ap_email": "nope", "payapp_due_day": 40})
        self.assertEqual(set(raised.exception.fields), {"follow_up_days", "ap_email", "payapp_due_day"})

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
        domain.resolve_packet_item(state, "job-oak", "pkt-oak-waiver", reason="Received by email")
        domain.resolve_packet_item(state, "job-oak", "pkt-oak-tickets", reason="Received by email")
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
        domain.resolve_packet_item(state, "job-decatur", "pkt-dec-punch", reason="Signed at walkthrough")
        domain.chase_retainage(state, "job-decatur")
        self.assertEqual(domain._job(state, "job-decatur")["retainage_chases"], 2)

    def test_partial_payment_does_not_mark_paid(self):
        state = seed_state()
        domain.match_payment(state, "job-turner", 5000000, "QBO-20012")
        job = domain._job(state, "job-turner")
        self.assertEqual(job["invoices"][-1]["status"], "partial")
        self.assertFalse(job["waiver_due"])
        self.assertEqual(domain.job_money(job)["outstanding_cents"], 6970000)

    def test_playbook_update_and_learn_rejection(self):
        state = seed_state()
        domain.update_payer(state, "payer-ellison", {"follow_up_days": 5, "rejects_when": "Missing photos"})
        payer = domain._payer(state, "payer-ellison")
        self.assertEqual(payer["follow_up_days"], 5)
        domain.learn_rejection(state, "job-oak", "Unsigned millwork waiver")
        self.assertIn("Unsigned millwork waiver", domain._payer(state, "payer-ellison")["rejects_when"])

    def test_email_log_on_send(self):
        state = seed_state()
        domain.resolve_packet_item(state, "job-oak", "pkt-oak-waiver", reason="Received by email")
        domain.resolve_packet_item(state, "job-oak", "pkt-oak-tickets", reason="Received by email")
        job = domain._job(state, "job-oak")
        job["change_orders"][0]["status"] = "approved"
        job["change_orders"][0]["included_in_billing"] = False
        domain._refresh_draft_gate(job)
        domain.draft_invoice(state, "job-oak")
        domain.approve_and_send(state, "job-oak", actor="owner")
        self.assertGreater(len(state.get("email_log", [])), 0)
        self.assertEqual(state["email_log"][-1]["kind"], "pay_app")

    def test_add_and_approve_change_order(self):
        state = seed_state()
        domain.add_change_order(state, "job-ponce", "Tile upgrade", 120000)
        job = domain._job(state, "job-ponce")
        self.assertEqual(len(job["change_orders"]), 1)
        co_id = job["change_orders"][0]["id"]
        domain.approve_change_order(state, "job-ponce", co_id)
        self.assertEqual(job["change_orders"][0]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
