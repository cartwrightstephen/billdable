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

    def test_edit_record_kind(self):  # R5-07
        state = seed_state()
        domain.add_job_record(state, "job-ponce", "ABC Supply invoice 4471", "pay_app", "", "owner")
        job = domain._job(state, "job-ponce")
        rec = job["records"][-1]
        domain.edit_record(state, "job-ponce", rec["id"], "ABC Supply tile delivery", "ticket", "owner")
        self.assertEqual((rec["label"], rec["kind"]), ("ABC Supply tile delivery", "ticket"))
        self.assertEqual(state["audit"][-1]["action"], "edit_record")
        with self.assertRaises(domain.GateError):
            domain.edit_record(state, "job-ponce", rec["id"], "x", "invoice", "owner")
        with self.assertRaises(domain.GateError):
            domain.edit_record(state, "job-ponce", rec["id"], "  ", "ticket", "owner")
        with self.assertRaises(domain.GateError):
            domain.edit_record(state, "job-ponce", "rec-nope", "x", "ticket", "owner")



class PaperworkTests(unittest.TestCase):  # R3-05
    def test_i_have_this_needs_no_reason_and_remove_restores_the_blocker(self):
        state = seed_state()
        domain.draft_invoice(state, "job-oak")
        job = domain._job(state, "job-oak")
        item = next(p for p in domain.active_packet(job) if p["id"] == "pkt-oak-waiver")
        domain.resolve_packet_item(state, "job-oak", item["id"])
        self.assertTrue(item["present"])
        self.assertNotIn(item["blocker"], domain.blocker_lines(job))
        self.assertTrue(state["audit"][-1]["detail"].endswith(f"marked {item['label']} as in hand."))
        domain.detach_packet_item(state, "job-oak", item["id"])
        self.assertFalse(item["present"])
        self.assertIn(item["blocker"], domain.blocker_lines(job))
        # Where it is, when given, is kept and in the activity log.
        domain.resolve_packet_item(state, "job-oak", item["id"], reason="Emailed 10/12")
        self.assertEqual(item["present_reason"], "Emailed 10/12")
        self.assertIn("Emailed 10/12", state["audit"][-1]["detail"])
        domain.detach_packet_item(state, "job-oak", item["id"])
        self.assertNotIn("present_reason", item)
        # A record attached by hand comes off too, and nothing on an empty item is refused.
        domain.add_job_record(state, "job-oak", "Waiver scan", "other")
        domain.attach_record_to_packet(state, "job-oak", item["id"], job["records"][-1]["id"])
        domain.detach_packet_item(state, "job-oak", item["id"])
        self.assertEqual((item["present"], item["attachments"]), (False, []))
        with self.assertRaises(domain.GateError):
            domain.detach_packet_item(state, "job-oak", item["id"])

    def test_remove_refused_once_the_pay_app_went_out(self):
        # What went with a sent pay app stays as it was sent (review of R3-05).
        state = seed_state()
        domain.draft_invoice(state, "job-oak")
        job = domain._job(state, "job-oak")
        item = next(p for p in domain.active_packet(job) if p["id"] == "pkt-oak-waiver")
        domain.resolve_packet_item(state, "job-oak", item["id"], reason="Emailed 10/12")
        sent = next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        sent["status"], sent["sent_at"] = "sent", domain.utcnow()
        with self.assertRaises(domain.GateError) as raised:
            domain.detach_packet_item(state, "job-oak", item["id"])
        self.assertIn(f"{sent['number']} has gone to the customer", str(raised.exception))
        self.assertTrue(item["present"])

    def test_blocker_items_link_unsigned_change_orders(self):
        state = seed_state()
        domain.draft_invoice(state, "job-oak")
        view = domain.job_view(state, domain._job(state, "job-oak"))
        cos = [b for b in view["blocker_items"] if b["kind"] == "co"]
        self.assertTrue(cos)
        self.assertTrue(all(b["co_id"] and b["number"] for b in cos))
        self.assertEqual(view["blockers"], [b["text"] for b in view["blocker_items"]])


class FollowUpNowTests(unittest.TestCase):  # R3-04
    def test_follow_up_now_goes_before_the_window(self):
        state = seed_state()
        job = next(j for j in state["jobs"] if any(i["status"] == "sent" for i in j["invoices"]))
        inv = next(i for i in job["invoices"] if i["status"] == "sent")
        inv["sent_at"] = domain.utcnow()
        inv.pop("last_follow_up_at", None)
        with self.assertRaisesRegex(domain.GateError, "not due"):
            domain.record_follow_up(state, job["id"], "owner")
        domain.record_follow_up(state, job["id"], "owner", early=True)
        self.assertTrue(inv["last_follow_up_at"])



class CloseBlockerTests(unittest.TestCase):  # R3-08
    def blank(self):
        return {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}

    def test_close_blocker_items_shape(self):
        state = self.blank()
        job = domain.create_job(state, "Pine deck", "Lee Family", 1_000_000, address="1 Pine St", ap_email="lee@example.com", retainage_pct=10)
        self.assertEqual([b["kind"] for b in domain.close_blocker_items(job)], ["not_done"])
        domain.add_change_order(state, job["id"], "Extra stairs", 50_000)
        domain.mark_done(state, job["id"], "owner")
        items = domain.close_blocker_items(job)
        self.assertEqual([b["kind"] for b in items], ["draft", "co"])
        draft, co = items
        self.assertEqual(set(draft), {"kind", "text", "invoice_id", "number"})
        self.assertEqual(draft["invoice_id"], job["invoices"][-1]["id"])
        self.assertEqual(draft["text"], f"{draft['number']} is still a draft.")
        self.assertEqual((set(co), co["number"], co["text"]), ({"kind", "text", "co_id", "number"}, 1, "CO 1 is not signed yet."))
        self.assertEqual(domain.close_blockers(job), [b["text"] for b in items])
        view = domain.job_view(state, job)
        self.assertEqual(view["close_blocker_items"], items)
        with self.assertRaises(domain.GateError) as raised:
            domain.close_job(state, job["id"])
        self.assertEqual(raised.exception.blockers, [b["text"] for b in items])

    def test_owed_and_held_back(self):
        state = seed_state()
        for job in state["jobs"]:
            kinds = {b["kind"] for b in domain.close_blocker_items(job)}
            m = domain.job_money(job)
            self.assertEqual("retainage" in kinds, m["retainage_open_cents"] > 0)
            self.assertEqual("outstanding" in kinds, m["outstanding_cents"] > 0)


class DeleteJobTests(unittest.TestCase):  # R3-09
    def test_delete_after_void_succeeds_and_after_a_payment_is_refused(self):
        state = {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = domain.create_job(state, "Pine deck", "Lee Family", 1_000_000, address="1 Pine St", ap_email="lee@example.com")
        domain.record_progress(state, job["id"], 400_000, "", "owner")
        self.assertFalse(domain.can_delete_job(job))
        with self.assertRaises(domain.GateError):
            domain.delete_job(state, job["id"])
        domain.void_draft(state, job["id"])
        self.assertTrue(domain.can_delete_job(job))
        domain.delete_job(state, job["id"])
        self.assertEqual(state["jobs"], [])
        self.assertIn("1 deleted draft", state["audit"][-1]["detail"])

        paid = domain.create_job(state, "Oak porch", "Lee Family", 1_000_000, address="2 Oak St", ap_email="lee@example.com")
        domain.update_payer(state, paid["payer_id"], {"packet": ""})  # nothing needed with each pay app
        domain.record_progress(state, paid["id"], 400_000, "", "owner")
        domain.approve_and_send(state, paid["id"], "owner")
        domain.match_payment(state, paid["id"], 100_000, "1001", "owner")
        with self.assertRaises(domain.GateError) as raised:
            domain.delete_job(state, paid["id"])
        self.assertIn("Finish it instead", str(raised.exception))
        self.assertEqual(len(state["jobs"]), 1)

class ProgressNotesTests(unittest.TestCase):  # R4-12
    def new_job(self, state, **kw):
        return domain.create_job(state, "Pine kitchen", "Jones Family", 4_500_000, address="12 Pine St",
                                 approver="Sam Jones", ap_email="ap@jones.example", **kw)

    def test_the_drafted_pay_app_carries_the_notes(self):
        state = {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = self.new_job(state)
        domain.record_progress(state, job["id"], 1_000_000, "  Cabinets set, counters templated.  ")
        first = job["invoices"][-1]
        self.assertEqual(first["progress_notes"], "Cabinets set, counters templated.")
        self.assertNotIn("progress_notes", job, "kept on the pay app, not overwritten on the job")
        # A correction without new notes keeps them; with new ones, takes those.
        domain.record_progress(state, job["id"], 1_200_000)
        self.assertEqual(job["invoices"][-1]["progress_notes"], "Cabinets set, counters templated.")
        domain.record_progress(state, job["id"], 1_300_000, "Counters in too.")
        self.assertEqual(job["invoices"][-1]["progress_notes"], "Counters in too.")
        self.assertEqual(first["progress_notes"], "Cabinets set, counters templated.", "the replaced draft keeps its own")

    def test_mark_done_notes_go_on_the_final_pay_app(self):
        state = {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = self.new_job(state)
        domain.mark_done(state, job["id"], notes="Punch list walked with Sam.")
        self.assertEqual(job["invoices"][-1]["progress_notes"], "Punch list walked with Sam.")


if __name__ == "__main__":
    unittest.main()
