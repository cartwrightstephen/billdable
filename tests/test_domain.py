import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
from waiver_help import signed_waiver
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
        signed_waiver(state, "job-turner")
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
        self.assertIn(f"{sent['number']} has gone to the client", str(raised.exception))
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
        waiver = next(p for p in paid["invoices"][-1]["packet"] if p["kind"] == "waiver")
        domain.skip_packet_item(state, paid["id"], waiver["id"], "They take no waiver")
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


def blank_company():
    return {"company": {"name": "Hartline Tile"}, "payers": [], "jobs": [], "audit": [], "email_log": []}


PARTS = {"street": "418 Oak Street", "city": "Decatur", "state": "ga", "zip": "30030"}


class JobFieldsTests(unittest.TestCase):
    """Stephen's Oct 29 review: the job's address in four checked parts (J1), the field rules on
    the job and client forms (G1), Client type (J2) and the start date (J8)."""

    def test_each_address_box_is_checked_and_the_address_is_composed(self):
        state = blank_company()
        with self.assertRaises(domain.GateError) as raised:
            domain.create_job(state, "Oak kitchen", "Ironleaf GC", 100_000,
                              address_parts={"street": "", "city": " ", "state": "ZZ", "zip": "3003"})
        self.assertEqual(set(raised.exception.fields), {"street", "city", "state", "zip"})
        self.assertIn("ZIP", raised.exception.fields["zip"])
        job = domain.create_job(state, "Oak kitchen", "Ironleaf GC", 100_000, address_parts=PARTS)
        self.assertEqual(job["address"], "418 Oak Street, Decatur, GA 30030")
        self.assertEqual(job["address_parts"]["state"], "GA")
        view = domain.job_view(state, job)
        self.assertEqual(view["address_parts"], {"street": "418 Oak Street", "city": "Decatur", "state": "GA", "zip": "30030"})

    def test_an_older_job_keeps_its_one_line_address_in_street_until_edited(self):
        state = blank_company()
        job = domain.create_job(state, "Porch", "Dana Homeowner", 100_000, address="2 Elm St")
        self.assertNotIn("address_parts", job)
        self.assertEqual(domain.job_view(state, job)["address_parts"], {"street": "2 Elm St", "city": "", "state": "", "zip": ""})
        # Sent back as the form showed it, with another change: not checked, kept as it was (C4).
        domain.edit_job(state, job["id"], {"name": "Back porch", "street": "2 Elm St", "city": "", "state": "", "zip": ""})
        self.assertEqual((job["name"], job["address"]), ("Back porch", "2 Elm St"))
        with self.assertRaises(domain.GateError) as raised:
            domain.edit_job(state, job["id"], {"street": "2 Elm Street", "city": "", "state": "", "zip": ""})
        self.assertEqual(set(raised.exception.fields), {"city", "state", "zip"}, "once edited, all four are needed")
        domain.edit_job(state, job["id"], {"street": "2 Elm St", "city": "Decatur", "state": "GA", "zip": "30030-1234"})
        self.assertEqual(job["address"], "2 Elm St, Decatur, GA 30030-1234")
        self.assertIn('job address from "2 Elm St" to "2 Elm St, Decatur, GA 30030-1234"', state["audit"][-1]["detail"])

    def test_edit_job_checks_only_what_changed(self):
        """Review C4: a contract number or address saved before the field rules doesn't block an
        unrelated edit; a changed one is checked."""
        state = blank_company()
        job = domain.create_job(state, "Porch", "Dana Lee", 1_000_000, address="1 Elm St, Atlanta, GA 30303")
        job["contract_number"] = "PO#4500-12; Rev 2"          # any text was taken before G1
        long_street = "Lot 4, behind the old mill on the county road past the second bridge, " * 2
        job.pop("address_parts", None)
        job["address"] = long_street.strip()                  # longer than the 120 the street box takes
        body = {"name": job["name"], "contract_number": job["contract_number"],
                **domain.parse_legacy_address(job["address"]), "retainage_pct": 5}
        domain.edit_job(state, job["id"], body)
        self.assertEqual(job["retainage_pct"], 5)
        self.assertEqual(job["contract_number"], "PO#4500-12; Rev 2")
        with self.assertRaises(domain.GateError) as raised:
            domain.edit_job(state, job["id"], {"contract_number": "PO#4500-12; Rev 3"})
        self.assertIn("contract_number", raised.exception.fields)
        for number in ("PO# 4500-12", "SC_2026_014", "2026-014 (Rev 1)", "A&B-12"):
            domain.edit_job(state, job["id"], {"contract_number": number})
            self.assertEqual(job["contract_number"], number)

    def test_a_new_clients_pay_app_day_and_terms_come_with_the_job_a_saved_ones_stay(self):  # S12
        state = blank_company()
        with self.assertRaises(domain.GateError) as raised:
            domain.create_job(state, "Oak kitchen", "Ironleaf GC", 100_000, address_parts=PARTS,
                              payer_days={"payapp_due_day": 40, "payment_terms_days": "soon"})
        self.assertEqual(set(raised.exception.fields), {"payapp_due_day", "payment_terms_days"})
        domain.create_job(state, "Oak kitchen", "Ironleaf GC", 100_000, address_parts=PARTS,
                          payer_days={"payapp_due_day": 25, "payment_terms_days": "30"})
        payer = state["payers"][0]
        self.assertEqual((payer["payapp_due_day"], payer["payment_terms_days"]), (25, 30))
        domain.create_job(state, "Bath", "Ironleaf GC", 100_000, address_parts=PARTS, payer_days={"payapp_due_day": 10})
        self.assertEqual(payer["payapp_due_day"], 25, "a saved client's day is changed in the Client playbook")

    def test_phone_contract_number_and_names_follow_the_field_rules(self):
        state = blank_company()
        with self.assertRaises(domain.GateError) as raised:
            domain.create_job(state, "!!!", "...", 100_000, address_parts=PARTS, phone="555", contract_number="C*100")
        self.assertEqual(set(raised.exception.fields), {"name", "customer", "phone", "contract_number"})
        job = domain.create_job(state, "Oak kitchen", "Ironleaf GC", 100_000, address_parts=PARTS, phone="404.555.0142", contract_number="SC-114")
        self.assertEqual(state["payers"][0]["phone"], "(404) 555-0142")
        with self.assertRaises(domain.GateError) as raised:
            domain.edit_job(state, job["id"], {"contract_number": "SC 114 / *2"})
        self.assertIn("contract_number", raised.exception.fields)
        payer = state["payers"][0]
        with self.assertRaises(domain.GateError) as raised:
            domain.update_payer(state, payer["id"], {"phone": "12", "name": "--"})
        self.assertEqual(set(raised.exception.fields), {"phone", "name"})
        with self.assertRaises(domain.GateError) as raised:
            domain.add_payer(state, {"name": "Elm Co", "phone": "1234"})
        self.assertIn("phone", raised.exception.fields)

    def test_client_type_maps_onto_the_two_sets_of_defaults(self):
        state = blank_company()
        for n, (kind, role, follow) in enumerate([("gc", "GC", 5), ("sub", "GC", 5), ("owner", "Owner", 7), ("homeowner", "Owner", 7), ("public", "Owner", 7)]):
            payer = domain.add_payer(state, {"name": f"Client {n}", "client_type": kind})
            self.assertEqual((payer["client_type"], payer["role"], payer["follow_up_days"]), (kind, role, follow), kind)
        with self.assertRaises(domain.GateError) as raised:
            domain.add_payer(state, {"name": "Odd one", "client_type": "landlord"})
        self.assertIn("client_type", raised.exception.fields)
        # New job: a new client takes the type asked; a saved client keeps its own.
        job = domain.create_job(state, "School gym", "Fulton County", 100_000, address_parts=PARTS, client_type="public")
        self.assertEqual((state["payers"][-1]["client_type"], job["payer_role"]), ("public", "Owner"))
        again = domain.create_job(state, "Gym two", "Client 0", 100_000, address_parts=PARTS, client_type="homeowner", role="Owner")
        self.assertEqual(again["payer_role"], "GC", "the saved client's type decides")
        # Changing the type moves the role and every job's defaults with it; an older page's role sets the type.
        payer = state["payers"][0]
        domain.update_payer(state, payer["id"], {"client_type": "owner"})
        self.assertEqual((payer["role"], again["payer_role"]), ("Owner", "Owner"))
        domain.update_payer(state, payer["id"], {"role": "GC"})
        self.assertEqual(payer["client_type"], "gc")
        self.assertEqual(domain.client_type_of({"role": "GC"}), "gc")
        self.assertEqual(domain.client_type_of({"role": "Owner"}), "owner")

    def test_start_date_defaults_to_today_is_bounded_and_audited(self):
        from datetime import date, timedelta
        state = blank_company()
        job = domain.create_job(state, "Oak kitchen", "Ironleaf GC", 100_000, address_parts=PARTS)
        today = date.today()
        self.assertIn(job["started_on"], {(today + timedelta(days=d)).isoformat() for d in (-1, 0, 1)})
        for bad in ("2026-13-01", "soon", (today - timedelta(days=4000)).isoformat(), (today + timedelta(days=800)).isoformat()):
            with self.assertRaises(domain.GateError) as raised:
                domain.edit_job(state, job["id"], {"started_on": bad})
            self.assertIn("started_on", raised.exception.fields, bad)
        start = (today - timedelta(days=30)).isoformat()
        was = job["started_on"]
        domain.edit_job(state, job["id"], {"started_on": start})
        self.assertEqual(job["started_on"], start)
        self.assertIn(f"start date from {was} to {start}", state["audit"][-1]["detail"])
        # An older job shows the day it was created until someone sets one.
        job.pop("started_on")
        created = next(a for a in state["audit"] if a["action"] == "create_job")["at"][:10]
        self.assertEqual(domain.job_view(state, job)["started_on"], created)
        n = len(state["audit"])
        domain.edit_job(state, job["id"], {"started_on": created})
        self.assertEqual(len(state["audit"]), n, "the date it already shows is no change")
        # A finished job's start date can still be corrected; nothing else can.
        job["closed"] = True
        domain.edit_job(state, job["id"], {"started_on": start})
        with self.assertRaises(domain.GateError):
            domain.edit_job(state, job["id"], {"name": "Renamed"})


class ClientJobsTests(unittest.TestCase):
    def test_a_clients_jobs_include_finished_ones_with_the_newest_contract(self):  # S10
        state = blank_company()
        a = domain.create_job(state, "Porch", "Ruiz Family", 100_000, address="1 Elm St")
        b = domain.create_job(state, "Deck", "Ruiz Family", 200_000, address="2 Elm St", contract_number="C-7")
        domain.create_job(state, "Other", "Someone Else", 100_000, address="3 Elm St")
        b["closed"] = True
        domain.add_job_record(state, a["id"], "Old contract", "contract")
        a["records"][-1]["file_ids"] = ["file-1"]
        domain.add_job_record(state, a["id"], "Signed contract", "contract")
        a["records"][-1]["file_ids"] = ["file-2"]
        rows = domain.payer_jobs(state, a["payer_id"])
        self.assertEqual([(r["name"], r["closed"]) for r in rows], [("Porch", False), ("Deck", True)])
        self.assertEqual((rows[0]["contract_file_id"], rows[1]["contract_file_id"]), ("file-2", ""))
        self.assertEqual((rows[1]["contract_number"], rows[1]["contract_total_cents"]), ("C-7", 200_000))
        snap = domain.light_snapshot(state)
        payer = next(p for p in snap["payers"] if p["id"] == a["payer_id"])
        self.assertEqual(payer["packet_items"], domain.parse_packet_text(payer["packet"]))
        self.assertNotIn("jobs", payer, "loaded when the card opens, so the page load stays small")


class AddressMigrationTests(unittest.TestCase):
    def test_older_jobs_get_address_parts_and_clients_a_type_without_losing_anything(self):
        import migrations
        state = {"schema_version": 4, "company": {}, "audit": [], "jobs": [
            {"id": "j1", "address": "12 Peach St, Atlanta, GA 30303"}, {"id": "j2", "address": "418 Oak Street, Decatur"}, {"id": "j3", "address": ""}],
            "payers": [{"id": "p1", "role": "GC"}, {"id": "p2", "role": "Owner"}, {"id": "p3", "role": "GC", "client_type": "sub"}]}
        migrations.migrate(state)
        jobs = {j["id"]: j for j in state["jobs"]}
        self.assertEqual(jobs["j1"]["address_parts"], {"street": "12 Peach St", "city": "Atlanta", "state": "GA", "zip": "30303"})
        self.assertEqual(jobs["j2"]["address_parts"], {"street": "418 Oak Street, Decatur", "city": "", "state": "", "zip": ""})
        self.assertEqual(jobs["j2"]["address"], "418 Oak Street, Decatur", "the one line is untouched")
        self.assertNotIn("address_parts", jobs["j3"])
        self.assertEqual([p["client_type"] for p in state["payers"]], ["gc", "owner", "sub"])
        self.assertEqual(state["schema_version"], migrations.SCHEMA_VERSION)


class JobStandingTests(unittest.TestCase):
    """J7: where the job is, and its dates, worked out on the server."""

    def test_stage_moves_with_the_job(self):
        state = blank_company()
        job = domain.create_job(state, "Oak kitchen", "Ironleaf GC", 1_000_000, address_parts=PARTS, retainage_pct=10)
        stage = lambda: domain.job_view(state, job)["stage"]
        self.assertEqual(stage()["key"], "started")
        domain.record_progress(state, job["id"], 400_000)
        self.assertEqual((stage()["key"], stage()["pct_billed"]), ("in_progress", 0))
        job["invoices"][-1]["status"] = "sent"
        job["invoices"][-1]["sent_at"] = "2026-10-01T12:00:00+00:00"
        self.assertEqual((stage()["key"], stage()["pct_billed"]), ("payapp_out", 40))
        steps = stage()["steps"]
        self.assertEqual([s["state"] for s in steps[:4]], ["done", "done", "current", "next"])
        self.assertEqual([s["label"] for s in steps], ["Started", "Work in progress", "Pay app out", "Approved", "Paid",
                                                       "Work done", "Retainage released", "Finished"])
        job["closed"] = True
        self.assertEqual(stage()["key"], "finished")

    def test_key_dates(self):
        from datetime import date
        state = blank_company()
        job = domain.create_job(state, "Oak kitchen", "Ironleaf GC", 1_000_000, address_parts=PARTS, started_on="2026-10-05")
        payer = state["payers"][0]
        payer["payapp_due_day"] = 31
        domain.record_progress(state, job["id"], 400_000)
        inv = job["invoices"][-1]
        inv.update(status="sent", sent_at="2026-10-20T15:00:00+00:00", due_on="2026-11-19")
        dates = {d["key"]: d["date"] for d in domain.job_key_dates(state, job, payer, today=date(2026, 11, 3))}
        self.assertEqual(dates["started"], "2026-10-05")
        self.assertEqual((dates["cutoff_this"], dates["cutoff_next"]), ("2026-11-30", "2026-12-31"))
        self.assertEqual((dates["last_sent"], dates["payment_due"]), ("2026-10-20", "2026-11-19"))
        self.assertEqual(dates["follow_up"], "2026-10-27", "an owner client's 7 days after it was sent")
        self.assertNotIn("finished", dates)
        labels = [d["label"] for d in domain.job_key_dates(state, job, payer, today=date(2026, 11, 3))]
        self.assertEqual(labels[:3], ["Started", "Pay app due this month", "Pay app due next month"])


if __name__ == "__main__":
    unittest.main()
