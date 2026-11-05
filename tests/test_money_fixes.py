"""Bugs independent testers found after the Nov 3 overhaul (fix stream ov-fixmoney). Every figure is
worked out by hand to the cent; each test names the tester's item."""

import copy
import json
import re
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import migrations
import payapp_pdf
import qty_import
import sov
from test_money import clear_packet, new_job as plain_job, new_state, send_open
from waiver_help import confirm_suggested, signed_waiver

OLD = Path(__file__).parent / "fixtures" / "old_company_cd935f5.json"
ROUGH = [{"item": "1", "description": "Rough", "quantity": "100", "unit": "ea", "rate": "100"},
         {"item": "2", "description": "Panel", "quantity": "1", "unit": "LS", "rate": "10000"}]


def new_job(state, packet=None, **kw):
    job = plain_job(state, **kw)
    if packet:
        domain.update_payer(state, job["payer_id"], {"packet": packet})
    return job


def open_draft(job):
    return next((i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES), None)


def line(job, item):
    return next(l for l in sov.lines(job) if l["item"] == item)


def daily(state, job, rows):
    domain.add_daily_record(state, job["id"], "Work today", details={"sections": {"quantities": rows}})


def rough_job(state, retainage=10):
    """$20,000: line 1 100 ea x $100, line 2 lump sum $10,000."""
    job = new_job(state, contract=2_000_000, retainage=retainage, packet="Invoice.")
    domain.save_sov(state, job["id"], ROUGH, "0")
    return job


def wire_job(state, retainage=10):
    """One line, 400 ea x $45, on $18,000."""
    job = new_job(state, contract=1_800_000, retainage=retainage, packet="Invoice.")
    domain.save_sov(state, job["id"], [{"item": "1", "description": "Rough-in", "quantity": "400", "unit": "ea", "rate": "45"}], "0")
    return job


def auto_job(state, contract=1_000_000, retainage=0):
    return new_job(state, contract=contract, retainage=retainage, auto_schedule=True, packet="Invoice.")


def actions(state, name):
    return [e["detail"] for e in state["audit"] if e["action"] == name]


def waiver_item(inv):
    return next(p for p in inv["packet"] if str(p.get("waiver_kind", "")).startswith("conditional"))


class FinalDraftResetTests(unittest.TestCase):  # item 1
    def final_draft(self, state, job):
        domain.mark_done(state, job["id"])
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["final"]), (2_000_000, True))
        return draft

    def check_whole(self, job):
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["final"]), (2_000_000, True))
        self.assertEqual(line(job, "1")["done_milli"], 100_000)
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)

    def test_clearing_the_box_goes_back_to_the_whole_job(self):
        state = new_state()
        job = rough_job(state)
        daily(state, job, [{"line_id": line(job, "1")["id"], "qty": "40"}])
        self.final_draft(state, job)
        domain.set_payapp_amounts(state, job["id"], {line(job, "1")["id"]: "1000"})
        self.assertEqual(open_draft(job)["gross_cents"], 1_100_000)
        self.assertFalse(open_draft(job)["final"], "it bills less than what is left, so it isn't the final one")
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 900_000)
        domain.set_payapp_amounts(state, job["id"], {line(job, "1")["id"]: ""})
        self.check_whole(job)

    def test_typing_the_suggested_amount_back_does_the_same(self):
        state = new_state()
        job = rough_job(state)
        daily(state, job, [{"line_id": line(job, "1")["id"], "qty": "40"}])
        self.final_draft(state, job)
        domain.set_payapp_amounts(state, job["id"], {line(job, "1")["id"]: "1000"})
        domain.set_payapp_amounts(state, job["id"], {line(job, "1")["id"]: "10000"})
        self.check_whole(job)

    def test_with_no_field_records(self):
        state = new_state()
        job = rough_job(state)
        self.final_draft(state, job)
        domain.set_payapp_amounts(state, job["id"], {line(job, "1")["id"]: "1000"})
        domain.set_payapp_amounts(state, job["id"], {line(job, "1")["id"]: ""})
        self.check_whole(job)

    def test_a_short_final_draft_is_not_final_when_it_goes(self):
        state = new_state()
        job = rough_job(state)
        self.final_draft(state, job)
        domain.set_payapp_amounts(state, job["id"], {line(job, "1")["id"]: "1000"})
        inv = send_open(state, job)
        self.assertFalse(inv["final"])
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 900_000)
        self.assertTrue(any(b["kind"] == "unbilled" for b in domain.close_blocker_items(job)))

    def test_a_job_lowered_before_this_fix_still_owes_the_rest(self):
        state = new_state()
        job = rough_job(state)
        self.final_draft(state, job)
        line(job, "1")["done_milli"] = 40_000   # what the old reset left behind
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0, "the draft has it all")
        domain.void_draft(state, job["id"])
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 2_000_000)
        domain.draft_invoice(state, job["id"], strict=True)
        self.assertEqual(open_draft(job)["gross_cents"], 2_000_000)


class BlankBoxTests(unittest.TestCase):  # item 2
    def up_to_150(self, state):
        job = wire_job(state)
        rid = line(job, "1")["id"]
        daily(state, job, [{"line_id": rid, "qty": "120"}])
        domain.record_sov_progress(state, job["id"], {rid: "150"})
        self.assertEqual(open_draft(job)["gross_cents"], 675_000)
        return job, rid

    def check_suggested(self, state, job, rid, done):
        view = domain._sov_view(job, state)
        self.assertEqual(view["period"]["suggested_gross_cents"], 540_000)
        domain.record_sov_progress(state, job["id"], done)
        self.assertEqual(open_draft(job)["gross_cents"], 540_000)
        self.assertEqual(line(job, "1")["done_milli"], 120_000)

    def test_after_deleting_the_draft_a_blank_bills_the_suggestion(self):
        state = new_state()
        job, rid = self.up_to_150(state)
        domain.void_draft(state, job["id"])
        self.check_suggested(state, job, rid, {})

    def test_a_blank_string_is_the_same_as_no_box(self):
        state = new_state()
        job, rid = self.up_to_150(state)
        domain.void_draft(state, job["id"])
        self.check_suggested(state, job, rid, {rid: "  "})

    def test_after_pulling_it_back_too(self):
        state = new_state()
        job, rid = self.up_to_150(state)
        send_open(state, job)
        domain.void_sent_pay_app(state, job["id"], job["invoices"][-1]["id"], "Wrong quantity")
        self.check_suggested(state, job, rid, {})

    def test_the_draft_button_bills_the_suggestion_on_a_job_voided_before_this_fix(self):
        state = new_state()
        job, rid = self.up_to_150(state)
        open_draft(job)["status"] = "void"   # done_milli stays at 150, as the old void left it
        domain.draft_invoice(state, job["id"], strict=True)
        self.assertEqual(open_draft(job)["gross_cents"], 540_000)

    def test_a_blank_box_over_an_open_draft_goes_to_the_suggestion(self):
        state = new_state()
        job, rid = self.up_to_150(state)
        domain.record_sov_progress(state, job["id"], {})
        self.assertEqual(open_draft(job)["gross_cents"], 540_000)

    def test_typed_30_over_field_10_with_the_draft_deleted(self):
        state = new_state()
        job = rough_job(state)
        rid = line(job, "1")["id"]
        daily(state, job, [{"line_id": rid, "qty": "10"}])
        domain.record_sov_progress(state, job["id"], {rid: "30"})
        domain.void_draft(state, job["id"])
        self.assertEqual(domain._sov_view(job, state)["period"]["suggested_gross_cents"], 100_000)
        domain.record_sov_progress(state, job["id"], {})
        self.assertEqual(open_draft(job)["gross_cents"], 100_000)

    def test_a_typed_figure_still_bills(self):
        state = new_state()
        job, rid = self.up_to_150(state)
        domain.void_draft(state, job["id"])
        domain.record_sov_progress(state, job["id"], {rid: "150"})
        self.assertEqual(open_draft(job)["gross_cents"], 675_000)

    def test_the_figure_on_a_sent_pay_app_stays_under_a_blank(self):
        state = new_state()
        job, rid = self.up_to_150(state)
        send_open(state, job)    # 150 billed, field 120
        daily(state, job, [{"line_id": rid, "qty": "80"}])
        domain.record_sov_progress(state, job["id"], {})
        self.assertEqual(open_draft(job)["gross_cents"], 225_000, "50 more, $2,250 (acceptance 28)")


class ProgressFormTests(unittest.TestCase):  # item 5
    def setUp(self):
        self.state = new_state()
        self.job = rough_job(self.state)
        self.l1, self.l2 = line(self.job, "1")["id"], line(self.job, "2")["id"]
        daily(self.state, self.job, [{"line_id": self.l1, "qty": "50"}])
        domain.record_sov_progress(self.state, self.job["id"], {self.l1: "30", self.l2: "0"})
        self.assertEqual(open_draft(self.job)["gross_cents"], 300_000)

    def row(self, line_id):
        return next(r for r in domain._sov_view(self.job, self.state)["lines"] if r["id"] == line_id)

    def test_the_form_starts_from_the_drafts_own_quantity(self):
        self.assertEqual(self.row(self.l1)["form_done_milli"], 30_000)
        self.assertEqual(self.row(self.l1)["done_milli"], 50_000, "the card still reads the field")
        # What the form sends when only line 2 changes.
        domain.record_sov_progress(self.state, self.job["id"], {self.l1: "30", self.l2: "0.1"})
        self.assertEqual(open_draft(self.job)["gross_cents"], 400_000)
        self.assertEqual(line(self.job, "1")["done_milli"], 30_000)

    def test_a_draft_at_the_field_total_keeps_up_with_the_field(self):
        state = new_state()
        job = rough_job(state)
        rid = line(job, "1")["id"]
        daily(state, job, [{"line_id": rid, "qty": "50"}])
        domain.record_sov_progress(state, job["id"], {})
        daily(state, job, [{"line_id": rid, "qty": "5"}])
        row = next(r for r in domain._sov_view(job, state)["lines"] if r["id"] == rid)
        self.assertEqual(row["form_done_milli"], 55_000)

    def test_with_no_draft_it_is_the_field_total(self):
        domain.void_draft(self.state, self.job["id"])
        self.assertEqual(self.row(self.l1)["form_done_milli"], 50_000)


class WaiverFiles(unittest.TestCase):
    """A progress draft on a one-item list with a waiver confirmed for it."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000, retainage=0, packet="Invoice.", auto_schedule=True)
        self.rid = sov.lines(self.job)[0]["id"]
        domain.record_sov_progress(self.state, self.job["id"], {self.rid: "0.45"})
        self.inv = open_draft(self.job)
        self.rec = signed_waiver(self.state, self.job["id"], "conditional_progress", "Signed waiver")
        domain.attach_record_to_packet(self.state, self.job["id"], waiver_item(self.inv)["id"], self.rec["id"], "Sam")
        self.assertTrue(waiver_item(self.inv)["present"])


class WaiverAmountTests(WaiverFiles):  # item 6
    def test_a_file_left_out_over_a_redraft_asks_for_another_look(self):
        file_id = self.rec["file_ids"][0]
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "exclude_file", file_id)
        self.assertFalse(waiver_item(self.inv)["present"])
        domain.record_sov_progress(self.state, self.job["id"], {self.rid: "0.72"})
        new = open_draft(self.job)
        self.assertEqual(new["amount_cents"], 720_000)
        domain.change_payapp_attachments(self.state, self.job["id"], new["id"], "include_file", file_id)
        item = waiver_item(new)
        self.assertFalse(item["present"])
        self.assertNotIn("confirmed_by", item)
        self.assertIn("$4,500.00", item["check"]["problems"][0])
        self.assertIn("$7,200.00", item["check"]["problems"][0])
        self.assertEqual(new["status"], "draft")
        domain.confirm_packet_item(self.state, self.job["id"], item["id"], "Pat", looked=True)
        self.assertEqual((item["present"], item["confirmed_by"]), (True, "Pat"))
        self.assertEqual(domain._packet_file_ids(self.state, self.job, new), [file_id])

    def test_confirming_with_the_file_still_left_out_puts_it_back(self):
        file_id = self.rec["file_ids"][0]
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "exclude_file", file_id)
        domain.record_sov_progress(self.state, self.job["id"], {self.rid: "0.72"})
        new = open_draft(self.job)
        domain.confirm_packet_item(self.state, self.job["id"], waiver_item(new)["id"], "Pat", looked=True)
        self.assertEqual(domain._packet_file_ids(self.state, self.job, new), [file_id], "a cleared item's file goes with the email")

    def test_left_out_and_back_at_the_same_amount_needs_no_look(self):
        file_id = self.rec["file_ids"][0]
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "exclude_file", file_id)
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "include_file", file_id)
        self.assertTrue(waiver_item(self.inv)["present"])
        self.assertEqual(waiver_item(self.inv)["confirmed_by"], "Sam")


class WaiverKindTests(WaiverFiles):  # item 8
    def test_final_pay_app_at_a_new_amount(self):
        domain.mark_done(self.state, self.job["id"])
        item = waiver_item(open_draft(self.job))
        self.assertEqual(item["waiver_kind"], "conditional_final")
        self.assertFalse(item["present"])

    def test_final_pay_app_at_the_same_amount_still_asks(self):
        domain.record_sov_progress(self.state, self.job["id"], {self.rid: "1"})
        item = waiver_item(open_draft(self.job))
        domain.confirm_packet_item(self.state, self.job["id"], item["id"], "Sam", looked=True)
        self.assertTrue(item["present"])
        domain.mark_done(self.state, self.job["id"])
        item = waiver_item(open_draft(self.job))
        self.assertEqual(item["waiver_kind"], "conditional_final")
        self.assertFalse(item["present"], "the amount didn't move, the kind did")
        self.assertNotIn("confirmed_by", item)
        words = item["check"]["problems"][0]
        self.assertIn("progress", words)
        self.assertIn("final", words)

    def test_a_look_clears_it_and_never_retypes_the_record(self):
        domain.record_sov_progress(self.state, self.job["id"], {self.rid: "1"})
        domain.confirm_packet_item(self.state, self.job["id"], waiver_item(open_draft(self.job))["id"], "Sam", looked=True)
        domain.mark_done(self.state, self.job["id"])
        item = waiver_item(open_draft(self.job))
        domain.confirm_packet_item(self.state, self.job["id"], item["id"], "Pat", looked=True)
        self.assertEqual((item["present"], item["confirmed_by"]), (True, "Pat"))
        self.assertEqual(self.rec["waiver_kind"], "conditional_progress")

    def final_confirmed(self):
        domain.mark_done(self.state, self.job["id"])
        draft = open_draft(self.job)
        item = waiver_item(draft)
        domain.confirm_packet_item(self.state, self.job["id"], item["id"], "Sam", looked=True)
        self.assertEqual((item["waiver_kind"], item["present"], draft["final"]), ("conditional_final", True, True))
        return draft

    def test_a_new_pending_change_order_flips_it_back_with_a_look(self):
        draft = self.final_confirmed()
        domain.add_change_order(self.state, self.job["id"], "Extra", 50_000)
        item = waiver_item(open_draft(self.job))
        self.assertFalse(draft["final"])
        self.assertEqual(item["waiver_kind"], "conditional_progress")
        self.assertFalse(item["present"])
        with self.assertRaises(domain.GateError):
            domain.approve_and_send(self.state, self.job["id"])
        self.assertEqual(draft["status"], "draft")

    def test_send_refuses_a_flip_no_change_caught(self):
        draft = self.final_confirmed()
        for item in draft["packet"]:
            if not item["present"]:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="test")
        # A change order written straight on the job, as a path that doesn't refresh the draft would.
        self.job["change_orders"].append({"id": "co-x", "number": 9, "title": "Extra", "amount_cents": 50_000, "status": "pending", "included_in_billing": False})
        before = len(self.state["email_log"])
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(self.state, self.job["id"])
        self.assertIn("progress", " ".join(raised.exception.blockers))
        self.assertEqual(draft["status"] in domain.DRAFT_STATUSES, True)
        self.assertEqual(len(self.state["email_log"]), before)

    def test_a_progress_record_isnt_suggested_for_a_final_item(self):
        domain.mark_done(self.state, self.job["id"])
        item = waiver_item(open_draft(self.job))
        domain.detach_packet_item(self.state, self.job["id"], item["id"])
        item["detached_record_ids"] = []
        domain._refresh_draft_gate(self.job, self.state)
        self.assertEqual(item["attachments"], [])
        final = signed_waiver(self.state, self.job["id"], "conditional_final", "Signed final")
        domain._refresh_draft_gate(self.job, self.state)
        self.assertEqual([a["record_id"] for a in item["attachments"]], [final["id"]])

    def test_attaching_the_wrong_kind_by_hand_warns_and_a_look_clears_it(self):
        domain.mark_done(self.state, self.job["id"])
        item = waiver_item(open_draft(self.job))
        domain.detach_packet_item(self.state, self.job["id"], item["id"])
        domain.attach_record_to_packet(self.state, self.job["id"], item["id"], self.rec["id"], "Sam")
        self.assertFalse(item["present"])
        self.assertTrue(item["check"]["problems"])
        domain.confirm_packet_item(self.state, self.job["id"], item["id"], "Sam", looked=True)
        self.assertTrue(item["present"], "never a hard block (Decision 6)")


class WaiverNotNeededTests(unittest.TestCase):  # item 7
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Invoice.")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.item = waiver_item(open_draft(self.job))
        domain.skip_packet_item(self.state, self.job["id"], self.item["id"], "mis-tap", "Sam")
        self.assertIn("waiver_skipped", self.job)

    def test_needed_after_all_on_the_open_draft(self):
        self.assertFalse(self.item["required"])
        domain.waiver_needed_after_all(self.state, self.job["id"], "Pat")
        self.assertNotIn("waiver_skipped", self.job)
        self.assertTrue(self.item["required"])
        for key in ("skipped_reason", "skipped_at", "skipped_by", "skipped_scope"):
            self.assertNotIn(key, self.item)
        self.assertEqual(open_draft(self.job)["status"], "draft")
        said = actions(self.state, "paperwork_needed")[-1]
        self.assertIn("Pat", said)
        self.assertIn("mis-tap", said)

    def test_needed_after_all_with_no_draft(self):
        domain.void_draft(self.state, self.job["id"])
        self.assertEqual(domain.job_view(self.state, self.job)["paperwork_view"]["waiver_skipped"]["reason"], "mis-tap")
        domain.waiver_needed_after_all(self.state, self.job["id"], "Pat")
        self.assertIsNone(domain.job_view(self.state, self.job)["paperwork_view"]["waiver_skipped"])
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.assertTrue(waiver_item(open_draft(self.job))["required"])

    def test_nothing_to_take_back(self):
        domain.waiver_needed_after_all(self.state, self.job["id"], "Pat")
        with self.assertRaises(domain.GateError):
            domain.waiver_needed_after_all(self.state, self.job["id"], "Pat")

    def test_going_back_to_the_clients_list_takes_it_back(self):
        domain.set_job_paperwork(self.state, self.job["id"], {"packet": "Invoice, photos."})
        domain.void_draft(self.state, self.job["id"])
        domain.set_job_paperwork(self.state, self.job["id"], {"use_client": True})
        self.assertNotIn("waiver_skipped", self.job)
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.assertTrue(waiver_item(open_draft(self.job))["required"])

    def test_a_job_skip_doesnt_cancel_the_waiver_after_payment(self):
        inv = open_draft(self.job)
        clear_packet(self.state, self.job)
        domain.approve_and_send(self.state, self.job["id"])
        domain.match_payment(self.state, self.job["id"], inv["amount_cents"], "chk")
        self.assertTrue(inv["waiver_due"])
        self.assertNotIn("waiver_not_needed", inv)
        self.assertTrue(any(b["kind"] == "waiver" for b in domain.close_blocker_items(self.job)))
        # It has its own way out, with its own reason.
        domain.waiver_not_needed(self.state, self.job["id"], "Handed over on site", invoice_id=inv["id"], actor="Pat")
        self.assertEqual(inv["waiver_not_needed"]["reason"], "Handed over on site")

    def test_the_whole_client_keeps_its_meaning(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        domain.record_progress(state, job["id"], 400_000)
        inv = open_draft(job)
        domain.skip_packet_item(state, job["id"], waiver_item(inv)["id"], "Homeowner", "Sam", whole_client=True)
        clear_packet(state, job)
        domain.approve_and_send(state, job["id"])
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk")
        self.assertFalse(inv["waiver_due"])
        self.assertEqual(inv["waiver_not_needed"]["reason"], "Homeowner")


class ListTickTests(unittest.TestCase):  # item 12
    def test_ticking_a_skipped_item_again_restores_it_on_the_draft(self):
        state = new_state()
        job = new_job(state, packet="Invoice, progress photos.")
        domain.set_job_paperwork(state, job["id"], {"packet": "Invoice, progress photos, insurance certificate."})
        domain.record_progress(state, job["id"], 400_000)
        draft = open_draft(job)
        photos = next(p for p in draft["packet"] if "photos" in p["label"].lower())
        domain.skip_packet_item(state, job["id"], photos["id"], "Not on this job", "Sam")
        self.assertFalse(photos["required"])
        domain.set_job_paperwork(state, job["id"], {"packet": "Invoice, progress photos, insurance certificate."})
        photos = next(p for p in draft["packet"] if "photos" in p["label"].lower())
        self.assertTrue(photos["required"])
        self.assertNotIn("skipped_reason", photos)
        self.assertEqual(draft["status"], "draft")

    def test_changing_something_else_leaves_a_skipped_item_off(self):
        state = new_state()
        job = new_job(state, packet="Invoice, progress photos.")
        domain.set_job_paperwork(state, job["id"], {"packet": "Invoice, progress photos."})
        domain.record_progress(state, job["id"], 400_000)
        draft = open_draft(job)
        photos = next(p for p in draft["packet"] if "photos" in p["label"].lower())
        domain.skip_packet_item(state, job["id"], photos["id"], "Not on this job", "Sam")
        domain.set_job_paperwork(state, job["id"], {"packet": "Invoice, insurance certificate."})
        self.assertFalse(any("photos" in p["label"].lower() and p["required"] for p in draft["packet"]))

    def test_a_job_skipped_waiver_stays_skipped_through_a_list_change(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        domain.record_progress(state, job["id"], 400_000)
        draft = open_draft(job)
        domain.skip_packet_item(state, job["id"], waiver_item(draft)["id"], "On site", "Sam")
        domain.set_job_paperwork(state, job["id"], {"packet": "Invoice, progress photos."})
        self.assertFalse(waiver_item(draft)["required"])


class OldNameOnlyTests(unittest.TestCase):  # item 3, acceptance 96
    def setUp(self):
        self.raw = json.loads(OLD.read_text(encoding="utf-8"))
        self.state = copy.deepcopy(self.raw)
        migrations.migrate(self.state)
        self.job = self.state["jobs"][0]
        self.sent, self.draft = self.job["invoices"]
        self.named = next(p for p in self.draft["packet"] if p["label"] == "Conditional waiver")
        self.state["payers"][0].update(phone="(404) 555-0100")

    def fill_the_rest(self):
        for item in self.draft["packet"]:
            if not item["present"]:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="test")

    def test_the_old_item_counts_as_missing_with_the_message(self):
        self.assertTrue(self.named["present"], "as the old code left it")
        self.assertIn(self.named, domain.missing_packet(self.job))
        words = " ".join(domain.blocker_lines(self.job))
        self.assertIn("Conditional waiver: No file was kept for it. Remove it and add the file.", words)

    def test_send_is_refused(self):
        self.fill_the_rest()
        sent = len(self.state["email_log"])
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(self.state, self.job["id"], "Sam")
        self.assertIn("No file was kept", " ".join(raised.exception.blockers))
        self.assertEqual(len(self.state["email_log"]), sent)

    def test_the_page_shows_it_missing_with_a_way_forward(self):
        view = domain.job_view(self.state, self.job)
        item = next(p for p in next(i for i in view["invoices"] if i["id"] == self.draft["id"])["packet"] if p["label"] == "Conditional waiver")
        self.assertEqual((item["present"], item["name_only"]), (False, True))
        self.assertTrue(self.named["present"], "the view only; the stored item isn't touched")
        domain.detach_packet_item(self.state, self.job["id"], self.named["id"])
        self.assertFalse(self.named["present"])

    def test_a_real_file_clears_it_and_the_name_goes(self):
        rec = signed_waiver(self.state, self.job["id"], "conditional_progress", "Signed waiver")
        domain.attach_record_to_packet(self.state, self.job["id"], self.named["id"], rec["id"], "Sam")
        self.assertEqual([a.get("record_id") for a in self.named["attachments"]], [rec["id"]])
        self.assertNotIn(self.named, domain.missing_packet(self.job))

    def test_the_pencil_copy_doesnt_tick_it(self):
        self.assertIn("[ ] Conditional waiver", domain.pay_app_text(self.state, self.job, self.draft))

    def test_sent_pay_apps_stay_as_they_were(self):
        was = copy.deepcopy(self.sent)
        domain.job_view(self.state, self.job)
        self.assertEqual(self.sent, was)
        self.assertEqual(self.sent, self.raw["jobs"][0]["invoices"][0])
        self.assertIn("[x] Conditional waiver", domain.pay_app_text(self.state, self.job, self.sent))

    def test_a_sent_pay_app_with_a_name_only_item_stays_ticked(self):
        self.sent["packet"][0]["attachments"] = [{"name": "Old name", "added_by": "Olive"}]
        self.sent["packet"][0].pop("marked_in_hand")
        self.assertIn("[x] Conditional waiver", domain.pay_app_text(self.state, self.job, self.sent))
        domain.void_draft(self.state, self.job["id"])
        self.assertEqual(domain.missing_packet(self.job), [], "no open draft, no new blocker")

    def test_an_old_item_with_a_real_file_and_no_confirmation_stays_valid(self):
        rec = signed_waiver(self.state, self.job["id"], "conditional_progress", "Signed waiver")
        self.named["attachments"] = [{"name": "Signed waiver", "record_id": rec["id"], "added_by": "Olive"}]
        self.assertNotIn(self.named, domain.missing_packet(self.job))

    def test_the_old_in_hand_item_stays_valid(self):
        hand = next(p for p in self.draft["packet"] if p["label"] == "Insurance certificate")
        self.assertNotIn(hand, domain.missing_packet(self.job))

    def test_not_required_doesnt_block(self):
        self.named["required"] = False
        self.assertNotIn(self.named, domain.missing_packet(self.job))

    def test_a_note_item_can_be_filled_by_a_note(self):
        job = self.job
        job["records"].append({"id": "rec-n", "kind": "note", "label": "Site note", "file_ids": []})
        self.named.update(label="Site notes", attachments=[{"name": "Site note", "record_id": "rec-n"}])
        self.assertNotIn(self.named, domain.missing_packet(job))


class CreditAfterFinalTests(unittest.TestCase):  # item 9
    def credit(self, state, job, cents=-50_000):
        domain.add_change_order(state, job["id"], "Credit", cents)
        co = job["change_orders"][-1]
        domain.approve_change_order(state, job["id"], co["id"])
        return co

    def test_an_unsent_final_draft_follows_the_credit(self):
        state = new_state()
        job = auto_job(state)
        domain.mark_done(state, job["id"])
        was = open_draft(job)
        self.credit(state, job)
        draft = open_draft(job)
        self.assertEqual((draft["number"], draft["gross_cents"], draft["final"]), (was["number"], 950_000, True))
        self.assertEqual(sum(l["amount_cents"] for l in draft["lines"]), 950_000)
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)

    def test_on_a_dollar_job_too(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, packet="Invoice.")
        domain.mark_done(state, job["id"])
        self.credit(state, job)
        self.assertEqual(open_draft(job)["gross_cents"], 950_000)

    def test_a_draft_with_the_client_stays_and_the_close_says_so_after_it_is_sent(self):
        state = new_state()
        job = auto_job(state)
        domain.mark_done(state, job["id"])
        domain.send_pencil(state, job["id"])
        self.credit(state, job)
        self.assertEqual(open_draft(job)["gross_cents"], 1_000_000)
        domain.record_pencil_response(state, job["id"], True)
        send_open(state, job)
        self.assertEqual(domain.job_money(job)["overbilled_cents"], 50_000)

    def sent_then_credit(self, state, job):
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        self.credit(state, job)
        return inv

    def test_after_it_was_sent_finish_job_is_blocked_in_plain_words(self):
        state = new_state()
        job = auto_job(state)
        inv = self.sent_then_credit(state, job)
        self.assertEqual(inv["amount_cents"], 1_000_000, "a sent pay app doesn't change")
        self.assertEqual(domain.job_money(job)["overbilled_cents"], 50_000)
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk")
        domain.waiver_not_needed(state, job["id"], "On site", invoice_id=inv["id"])
        items = domain.close_blocker_items(job)
        self.assertEqual([b["kind"] for b in items], ["overbilled"])
        self.assertIn("overbilled by $500.00", items[0]["text"])
        self.assertIn(inv["number"], items[0]["text"])
        with self.assertRaises(domain.GateError):
            domain.close_job(state, job["id"])

    def test_recording_the_refund_lets_it_finish(self):
        state = new_state()
        job = auto_job(state)
        inv = self.sent_then_credit(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk")
        domain.waiver_not_needed(state, job["id"], "On site", invoice_id=inv["id"])
        with self.assertRaises(domain.GateError):
            domain.settle_overbilling(state, job["id"], "  ", "Pat")
        domain.settle_overbilling(state, job["id"], "Refund check 1044", "Pat")
        self.assertEqual(domain.job_money(job)["overbilled_cents"], 0)
        self.assertIn("Refund check 1044", actions(state, "overbilling_settled")[-1])
        self.assertIn("$500.00", actions(state, "overbilling_settled")[-1])
        domain.close_job(state, job["id"])
        self.assertTrue(job["closed"])

    def test_pulling_it_back_and_drafting_again_clears_it(self):
        state = new_state()
        job = auto_job(state)
        inv = self.sent_then_credit(state, job)
        domain.void_sent_pay_app(state, job["id"], inv["id"], "Credit signed")
        self.assertEqual(domain.job_money(job)["overbilled_cents"], 0)
        domain.draft_invoice(state, job["id"], strict=True)
        self.assertEqual(open_draft(job)["gross_cents"], 950_000)

    def test_nothing_to_settle_is_refused(self):
        state = new_state()
        job = auto_job(state)
        with self.assertRaises(domain.GateError):
            domain.settle_overbilling(state, job["id"], "Refund", "Pat")

    def test_a_dollar_job_billed_over_contract(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, packet="Invoice.")
        domain.mark_done(state, job["id"])
        send_open(state, job)
        self.credit(state, job, -10_000)
        self.assertEqual(domain.job_money(job)["overbilled_cents"], 10_000)
        self.assertTrue(any(b["kind"] == "overbilled" for b in domain.close_blocker_items(job)))

    def test_retainage_counts_as_billed_and_an_unsigned_change_order_on_the_bill_isnt_over(self):
        state = new_state()
        job = auto_job(state, retainage=10)
        domain.mark_done(state, job["id"])
        send_open(state, job)
        self.assertEqual(domain.job_money(job)["overbilled_cents"], 0)
        self.credit(state, job)
        self.assertEqual(domain.job_money(job)["overbilled_cents"], 50_000)

    def test_a_job_within_its_contract_has_no_overbilling(self):
        state = new_state()
        job = auto_job(state)
        domain.record_sov_progress(state, job["id"], {sov.lines(job)[0]["id"]: "0.5"})
        send_open(state, job)
        self.credit(state, job, -10_000)
        self.assertEqual(domain.job_money(job)["overbilled_cents"], 0)


class NegativeContractTests(unittest.TestCase):  # item 14
    def test_a_contract_edit_that_takes_the_total_below_zero_is_refused(self):
        state = new_state()
        job = auto_job(state, contract=100_000)
        domain.add_change_order(state, job["id"], "Credit", -90_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        with self.assertRaises(domain.GateError) as raised:
            domain.edit_job(state, job["id"], {"contract_cents": 50_000})
        self.assertIn("$900.00", raised.exception.fields["contract_cents"])
        self.assertEqual(job["contract_cents"], 100_000)
        domain.edit_job(state, job["id"], {"contract_cents": 90_001})
        self.assertEqual(job["contract_cents"], 90_001)

    def test_saving_a_schedule_that_does_the_same_is_refused(self):
        state = new_state()
        job = auto_job(state, contract=100_000)
        domain.add_change_order(state, job["id"], "Credit", -90_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        with self.assertRaises(domain.GateError):
            domain.save_sov(state, job["id"], [{"description": "Small", "quantity": "1", "unit": "LS", "rate": "500"}], "0", update_contract=True)
        self.assertEqual(job["contract_cents"], 100_000)


def sheet_this_period(data: bytes):
    """The continuation sheet's rows from the PDF's decompressed text: [(description, [amounts])]."""
    text = b""
    for stream in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            text += zlib.decompress(stream) + b"\n"
        except zlib.error:
            text += stream + b"\n"
    rows = []
    for raw in re.findall(rb"\((.*?)\) Tj", text):
        row = raw.decode("latin-1")
        rows.append(row)
    return rows


def amounts(row):
    return [float(x.replace(",", "")) for x in re.findall(r"-?[\d,]+\.\d\d", row)]


class SheetWithUnsignedChangeOrderTests(unittest.TestCase):  # item 10
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000, packet="Invoice.")
        domain.save_sov(self.state, self.job["id"], [
            {"item": "1", "description": "Line one", "quantity": "1", "unit": "LS", "rate": "2000"},
            {"item": "2", "description": "Line two", "quantity": "1", "unit": "LS", "rate": "8000"}], "0")
        self.l1 = line(self.job, "1")["id"]
        domain.add_change_order(self.state, self.job["id"], "Extra", 100_000)
        self.co = self.job["change_orders"][-1]
        domain.include_change_order(self.state, self.job["id"], self.co["id"])
        domain.record_sov_progress(self.state, self.job["id"], {self.l1: "0.5"})
        self.pa1 = open_draft(self.job)
        self.assertEqual(self.pa1["gross_cents"], 200_000)

    def rows(self, inv):
        return sheet_this_period(payapp_pdf.for_invoice(self.state, self.job["id"], inv["id"])[0])

    def totals_row(self, inv):
        return amounts(next(r for r in self.rows(inv) if "Totals" in r))

    def test_pa_01_shows_the_change_order_once_signed_while_a_draft(self):
        domain.approve_change_order(self.state, self.job["id"], self.co["id"])
        self.assertIs(open_draft(self.job), self.pa1)
        self.assertEqual(self.pa1["gross_cents"], 200_000)
        row = next(r for r in self.rows(self.pa1) if "CO 1" in r and "1,000.00" in r)
        self.assertNotIn("unsigned", row)
        # scheduled, previous, this period, stored, to date
        self.assertEqual(self.totals_row(self.pa1)[2], 2000.00, "This period adds up to the pay app")
        self.assertIn("CO 1", sov.continuation_sheet(self.job, self.pa1))
        view = domain.job_view(self.state, self.job)
        rows = next(i for i in view["invoices"] if i["id"] == self.pa1["id"])["co_rows"]
        self.assertEqual([(r["this_cents"], r["previous_cents"], r["scheduled_cents"]) for r in rows], [(100_000, 0, 100_000)])

    def test_pa_02_shows_it_as_billed_before(self):
        domain.approve_change_order(self.state, self.job["id"], self.co["id"])
        send_open(self.state, self.job)
        domain.record_sov_progress(self.state, self.job["id"], {self.l1: "1"})
        pa2 = open_draft(self.job)
        self.assertEqual(pa2["gross_cents"], 100_000)
        entry = next(e for e in pa2["sov_sheet"] if e["description"].startswith("CO 1"))
        self.assertEqual((entry["prev_work_cents"], entry["work_this_cents"], entry["amount_cents"], entry["to_date_cents"]),
                         (100_000, 0, 0, 100_000))
        self.assertEqual(entry["qty_milli_this"], 0)
        self.assertEqual(sum(e["work_this_cents"] for e in pa2["sov_sheet"]), pa2["gross_cents"])
        total = self.totals_row(pa2)
        self.assertEqual(total[2], 1000.00, "This period is the $1,000 pay app, not $2,000")
        self.assertEqual(total[1], 2000.00, "Previous")
        self.assertEqual(domain.sov.co_rows(self.job, pa2), [], "it has its own row now")

    def test_the_sheet_on_the_first_pay_app_stays_when_it_is_sent(self):
        domain.approve_change_order(self.state, self.job["id"], self.co["id"])
        send_open(self.state, self.job)
        self.assertEqual(self.totals_row(self.pa1)[2], 2000.00)

    def test_while_still_unsigned_it_says_so(self):
        row = next(r for r in self.rows(self.pa1) if "CO 1" in r and "unsigned" in r)
        self.assertIn("1,000.00", row)
        self.assertEqual(self.totals_row(self.pa1)[2], 2000.00)


class FiveSectionsTests(unittest.TestCase):  # item 11, acceptance 46
    def record(self, state, job, kind, label, ticket_type=None):
        rec = signed_waiver(state, job["id"], None, label)
        rec["kind"] = kind
        if ticket_type:
            rec["ticket_type"] = ticket_type
        return rec

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Daily tickets and time sheets, progress photos, supplier invoices and receipts.")
        self.delivery = self.record(self.state, self.job, "ticket", "Delivery 1", "delivery")
        self.photo = self.record(self.state, self.job, "photo", "Photo 1")
        self.time = self.record(self.state, self.job, "ticket", "Time sheet 1", "time_sheet")
        self.invoice = self.record(self.state, self.job, "supplier_invoice", "Invoice 1")
        self.inspection = self.record(self.state, self.job, "inspection", "Inspection 1")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.packet = {p["label"]: p for p in open_draft(self.job)["packet"]}

    def suggested(self, label):
        return [a["record_id"] for a in self.packet[label]["attachments"]]

    def test_the_ticket_item_suggests_all_that_fit_and_no_invoice(self):
        self.assertEqual(sorted(self.suggested("Daily tickets and time sheets")), sorted([self.delivery["id"], self.time["id"]]))

    def test_the_other_items_get_theirs(self):
        self.assertEqual(self.suggested("Progress photos"), [self.photo["id"]])
        self.assertEqual(self.suggested("Supplier invoices and receipts"), [self.invoice["id"]])

    def test_two_photos_are_both_suggested(self):
        second = self.record(self.state, self.job, "photo", "Photo 2")
        domain._refresh_draft_gate(self.job, self.state)
        self.assertEqual(sorted(self.suggested("Progress photos")), sorted([self.photo["id"], second["id"]]))

    def test_confirming_the_item_sends_them_all(self):
        item = self.packet["Daily tickets and time sheets"]
        domain.confirm_packet_item(self.state, self.job["id"], item["id"], "Sam")
        self.assertTrue(item["present"])
        ids = domain._packet_file_ids(self.state, self.job, open_draft(self.job))
        self.assertIn(self.delivery["file_ids"][0], ids)
        self.assertIn(self.time["file_ids"][0], ids)

    def test_a_label_that_asks_for_invoices_takes_them(self):
        kinds = domain._record_kinds_for({"kind": "tickets-and-invoices", "label": "Tickets and invoices"})[1]
        self.assertEqual(set(kinds), {"ticket", "supplier_invoice"})
        kinds = domain._record_kinds_for({"kind": "ticket", "label": "Daily tickets and invoices"})[1]
        self.assertEqual(set(kinds), {"ticket", "supplier_invoice"})
        kinds = domain._record_kinds_for({"kind": "ticket", "label": "Daily tickets and time sheets"})[1]
        self.assertEqual(kinds, ("ticket",))

    def test_a_waiver_still_gets_one_suggestion(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        signed_waiver(state, job["id"], "conditional_progress", "Old waiver")
        newest = signed_waiver(state, job["id"], "conditional_progress", "New waiver")
        domain.record_progress(state, job["id"], 400_000)
        self.assertEqual([a["record_id"] for a in waiver_item(open_draft(job))["attachments"]], [newest["id"]])


class ExternalStatusTests(unittest.TestCase):  # item 13
    def test_delete_draft_clears_the_pencil_status(self):
        state = new_state()
        job = auto_job(state)
        domain.record_sov_progress(state, job["id"], {sov.lines(job)[0]["id"]: "0.5"})
        domain.send_pencil(state, job["id"])
        self.assertIn("Pencil copy", job["external_status"])
        domain.void_draft(state, job["id"])
        self.assertFalse(job["external_status"])

    def test_undo_done_clears_it(self):
        state = new_state()
        job = auto_job(state)
        domain.mark_done(state, job["id"])
        domain.send_pencil(state, job["id"])
        domain.undo_mark_done(state, job["id"])
        self.assertFalse(job["external_status"])

    def test_another_status_stays(self):
        state = new_state()
        job = auto_job(state)
        domain.record_sov_progress(state, job["id"], {sov.lines(job)[0]["id"]: "0.5"})
        job["external_status"] = "Submitted to Jones Family, awaiting Sam"
        domain.void_draft(state, job["id"])
        self.assertEqual(job["external_status"], "Submitted to Jones Family, awaiting Sam")


class ReplaceLinesTests(unittest.TestCase):  # item 15
    def test_the_audit_names_what_was_recorded(self):
        state = new_state()
        job = auto_job(state)
        daily(state, job, [{"line_id": sov.lines(job)[0]["id"], "qty": "50"}])
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 500_000)
        domain.save_sov(state, job["id"], [{"description": "Real", "quantity": "1", "unit": "LS", "rate": "10000"}], "0")
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)
        said = actions(state, "sov_saved")[-1]
        self.assertIn("Contract amount", said)
        self.assertIn("50%", said)
        self.assertIn("$5,000.00", said)

    def test_a_line_kept_isnt_named(self):
        state = new_state()
        job = rough_job(state)
        rid = line(job, "1")["id"]
        daily(state, job, [{"line_id": rid, "qty": "5"}])
        lines = [{"id": rid, **ROUGH[0]}, ROUGH[1]]
        domain.save_sov(state, job["id"], lines, "0")
        self.assertNotIn("discard", actions(state, "sov_saved")[-1].lower())
        self.assertEqual(sov.field_total(line(job, "1")), 5_000)


class CorrectFieldTotalTests(unittest.TestCase):  # item 4
    def setUp(self):
        self.state = new_state()
        self.job = rough_job(self.state)
        self.rid = line(self.job, "1")["id"]

    def imported(self, text="1,Rough,90"):
        made = qty_import.apply(self.state, self.job["id"], text, "Sam", "q.csv")
        self.assertTrue(made["added"])
        return self.job["records"][-1]

    def view_row(self):
        return next(r for r in domain._sov_view(self.job, self.state)["lines"] if r["id"] == self.rid)

    def test_a_wrong_import_can_be_corrected_with_a_reason(self):
        self.imported()
        domain.correct_field_total(self.state, self.job["id"], self.rid, "9", "Meant 9", "Pat")
        self.assertEqual(sov.field_total(line(self.job, "1")), 9_000)
        self.assertEqual(domain._sov_view(self.job, self.state)["period"]["suggested_gross_cents"], 90_000)
        self.assertEqual(self.view_row()["form_done_milli"], 9_000)
        said = actions(self.state, "field_total_corrected")[-1]
        for words in ("Pat", "90", "9 ea", "Meant 9"):
            self.assertIn(words, said)

    def test_it_needs_a_reason_and_a_real_figure(self):
        self.imported()
        for qty, reason in (("9", " "), ("-1", "x"), ("101", "x"), ("abc", "x"), ("90", "x")):
            with self.subTest(qty=qty):
                with self.assertRaises(domain.GateError):
                    domain.correct_field_total(self.state, self.job["id"], self.rid, qty, reason, "Pat")
        self.assertEqual(sov.field_total(line(self.job, "1")), 90_000)

    def test_below_what_is_billed_has_to_say_so(self):
        self.imported()
        domain.record_sov_progress(self.state, self.job["id"], {})
        send_open(self.state, self.job)
        with self.assertRaises(domain.GateError) as raised:
            domain.correct_field_total(self.state, self.job["id"], self.rid, "9", "Meant 9", "Pat")
        self.assertIn("90", str(raised.exception))
        self.assertEqual(raised.exception.extra.get("below_billed"), True)
        sent = copy.deepcopy(self.job["invoices"][-1])
        domain.correct_field_total(self.state, self.job["id"], self.rid, "9", "Meant 9", "Pat", below_billed=True)
        self.assertEqual(sov.field_total(line(self.job, "1")), 9_000)
        self.assertEqual(self.job["invoices"][-1], sent)
        self.assertIn("already billed", actions(self.state, "field_total_corrected")[-1])
        self.assertEqual(domain.job_money(self.job)["unbilled_completed_cents"], 0, "nothing more until the field passes the bill")
        daily(self.state, self.job, [{"line_id": self.rid, "qty": "85"}])
        self.assertEqual(domain.job_money(self.job)["unbilled_completed_cents"], 40_000)   # field 94, billed 90: 4 ea at $100

    def test_an_open_draft_at_the_field_total_follows(self):
        self.imported()
        domain.record_sov_progress(self.state, self.job["id"], {})
        number = open_draft(self.job)["number"]
        self.assertEqual(open_draft(self.job)["gross_cents"], 900_000)
        domain.correct_field_total(self.state, self.job["id"], self.rid, "9", "Meant 9", "Pat")
        self.assertEqual((open_draft(self.job)["number"], open_draft(self.job)["gross_cents"]), (number, 90_000))

    def test_a_typed_figure_on_a_draft_stays(self):
        self.imported()
        domain.record_sov_progress(self.state, self.job["id"], {self.rid: "20"})
        domain.correct_field_total(self.state, self.job["id"], self.rid, "9", "Meant 9", "Pat")
        self.assertEqual(open_draft(self.job)["gross_cents"], 200_000)

    def test_a_draft_with_the_client_stays_and_the_audit_says_so(self):
        self.imported()
        domain.record_sov_progress(self.state, self.job["id"], {})
        domain.send_pencil(self.state, self.job["id"])
        domain.correct_field_total(self.state, self.job["id"], self.rid, "9", "Meant 9", "Pat")
        self.assertEqual(open_draft(self.job)["gross_cents"], 900_000)
        self.assertIn(open_draft(self.job)["number"], actions(self.state, "field_total_corrected")[-1])

    def test_a_lump_sum_is_in_percent(self):
        lump = line(self.job, "2")["id"]
        daily(self.state, self.job, [{"line_id": lump, "qty": "50%"}])
        domain.correct_field_total(self.state, self.job["id"], lump, "0.25", "Half that", "Pat")
        self.assertEqual(sov.field_total(line(self.job, "2")), 250)
        self.assertIn("25%", actions(self.state, "field_total_corrected")[-1])

    def test_not_on_a_finished_job(self):
        domain.mark_done(self.state, self.job["id"])
        with self.assertRaises(domain.GateError):
            domain.correct_field_total(self.state, self.job["id"], self.rid, "9", "x", "Pat")

    def test_undo_import_takes_off_what_it_added(self):
        daily(self.state, self.job, [{"line_id": self.rid, "qty": "5"}])
        rec = self.imported()
        self.assertEqual(sov.field_total(line(self.job, "1")), 95_000)
        domain.undo_quantity_import(self.state, self.job["id"], rec["id"], "Pat")
        self.assertEqual(sov.field_total(line(self.job, "1")), 5_000)
        self.assertEqual(domain._sov_view(self.job, self.state)["period"]["suggested_gross_cents"], 50_000)
        self.assertEqual(domain._sov_view(self.job, self.state)["period"]["lines"][0]["from_records_milli"], 5_000)
        self.assertNotIn("quantities", rec)
        self.assertEqual(rec["quantities_undone"]["by"], "Pat")
        self.assertIn("Pat", actions(self.state, "quantities_import_undone")[-1])
        with self.assertRaises(domain.GateError):
            domain.undo_quantity_import(self.state, self.job["id"], rec["id"], "Pat")

    def test_undo_below_what_is_billed_has_to_say_so(self):
        rec = self.imported()
        domain.record_sov_progress(self.state, self.job["id"], {})
        send_open(self.state, self.job)
        with self.assertRaises(domain.GateError) as raised:
            domain.undo_quantity_import(self.state, self.job["id"], rec["id"], "Pat")
        self.assertTrue(raised.exception.extra.get("below_billed"))
        domain.undo_quantity_import(self.state, self.job["id"], rec["id"], "Pat", below_billed=True)
        self.assertEqual(sov.field_total(line(self.job, "1")), 0)

    def test_undo_doesnt_go_below_zero_after_a_correction(self):
        rec = self.imported()
        domain.correct_field_total(self.state, self.job["id"], self.rid, "9", "Meant 9", "Pat")
        domain.undo_quantity_import(self.state, self.job["id"], rec["id"], "Pat")
        self.assertEqual(sov.field_total(line(self.job, "1")), 0)

    def test_a_record_that_isnt_an_import_is_refused(self):
        domain.add_job_record(self.state, self.job["id"], "A note", "note", "x", "Pat")
        with self.assertRaises(domain.GateError):
            domain.undo_quantity_import(self.state, self.job["id"], self.job["records"][-1]["id"], "Pat")

    def test_a_line_gone_from_the_schedule_is_said(self):
        rec = self.imported()
        domain.save_sov(self.state, self.job["id"], ROUGH, "0")
        domain.undo_quantity_import(self.state, self.job["id"], rec["id"], "Pat")
        self.assertIn("no longer on the schedule", actions(self.state, "quantities_import_undone")[-1])


def pdf_bytes(text):
    body = "BT /F1 12 Tf 72 720 Td (" + text + ") Tj ET"
    return f"%PDF-1.4\n1 0 obj\n<< /Length {len(body)} >>\nstream\n{body}\nendstream\nendobj\n".encode()


class FinalWaiverOnAJobNotFinishedTests(unittest.TestCase):  # items 16, 17, acceptance 75
    def half_done(self, retainage=10):
        state = new_state()
        job = auto_job(state, contract=10_000_000, retainage=retainage)
        domain.record_sov_progress(state, job["id"], {sov.lines(job)[0]["id"]: "0.5"})
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk")
        domain.waiver_not_needed(state, job["id"], "On site", invoice_id=inv["id"])
        return state, job, inv

    def test_all_the_retainage_on_a_job_half_done_is_a_progress_release(self):
        state, job, inv = self.half_done()
        self.assertEqual(inv["gross_cents"], 5_000_000)
        domain.record_retainage_payment(state, job["id"], 500_000, "ret")
        release = job["retainage_waivers"][-1]
        self.assertEqual(release["kind"], "unconditional_progress")
        signed_waiver(state, job["id"], None, "Signed release waiver")
        domain.send_waiver(state, job["id"], "Sam", release_id=release["id"])
        mail = state["email_log"][-1]
        self.assertIn("Unconditional waiver on progress payment", mail["subject"])
        self.assertNotIn("final payment", mail["subject"] + mail["body"])

    def test_a_pending_change_order_keeps_it_from_final(self):
        state = new_state()
        job = auto_job(state, contract=10_000_000, retainage=10)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk")
        domain.waiver_not_needed(state, job["id"], "On site", invoice_id=inv["id"])
        domain.add_change_order(state, job["id"], "Extra", 1_000_000)
        domain.record_retainage_payment(state, job["id"], 1_000_000, "ret")
        self.assertEqual(job["retainage_waivers"][-1]["kind"], "unconditional_progress")

    def test_a_pay_app_unpaid_keeps_it_from_final(self):
        state, job, _ = self.half_done()
        domain.mark_done(state, job["id"])
        send_open(state, job)
        domain.record_retainage_payment(state, job["id"], 1_000_000, "ret")
        self.assertEqual(job["retainage_waivers"][-1]["kind"], "unconditional_progress")

    def test_the_last_money_on_a_finished_job_is_final(self):
        state = new_state()
        job = auto_job(state, contract=10_000_000, retainage=10)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk")
        domain.record_retainage_payment(state, job["id"], 400_000, "ret")
        self.assertEqual(job["retainage_waivers"][-1]["kind"], "unconditional_progress")
        domain.record_retainage_payment(state, job["id"], 600_000, "ret")
        self.assertEqual(job["retainage_waivers"][-1]["kind"], "unconditional_final")

    def test_a_release_marked_final_by_the_old_code_goes_as_progress(self):
        state, job, _ = self.half_done()
        domain.record_retainage_payment(state, job["id"], 500_000, "ret")
        release = job["retainage_waivers"][-1]
        release["kind"] = "unconditional_final"
        signed_waiver(state, job["id"], None, "Signed release waiver")
        domain.send_waiver(state, job["id"], "Sam", release_id=release["id"])
        self.assertEqual(release["kind"], "unconditional_progress")

    def test_a_final_waiver_asked_for_on_a_job_half_done_with_no_retainage_warns(self):
        state = new_state()
        job = auto_job(state, contract=10_000_000)
        domain.record_sov_progress(state, job["id"], {sov.lines(job)[0]["id"]: "0.5"})
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk")
        signed_waiver(state, job["id"], "unconditional_final", "Final")
        sent = len(state["email_log"])
        with self.assertRaises(domain.GateError) as raised:
            domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"], kind="unconditional_final")
        self.assertTrue(raised.exception.extra["final_waiver_warning"])
        self.assertIn("isn't finished", str(raised.exception))
        self.assertEqual(len(state["email_log"]), sent)
        domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"], kind="unconditional_final", confirm_final=True)
        self.assertEqual(inv["waiver_kind"], "unconditional_final")


class UnreadWaiverTests(unittest.TestCase):  # item 18
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Invoice.")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = open_draft(self.job)
        for item in self.inv["packet"]:
            domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="test")
        self.data = {}

    def read(self, meta):
        return self.data.get(meta["id"])

    def record(self, label, kind, text, name="Release.pdf"):
        rec = signed_waiver(self.state, self.job["id"], None, label)
        rec["kind"] = kind
        meta = next(f for f in self.state["files"] if f["id"] == rec["file_ids"][0])
        meta["name"] = name
        self.data[meta["id"]] = pdf_bytes(text)
        return rec

    def add(self, rec):
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", rec["id"], "Sam", read_file=self.read)

    def check_gate(self, rec):
        self.add(rec)
        self.assertEqual(domain.unconditional_waivers_attached(self.job, self.inv), [rec], "the page's warning")
        sent = len(self.state["email_log"])
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(self.state, self.job["id"], "Owner", read_file=self.read)
        self.assertEqual(str(raised.exception), domain.UNCONDITIONAL_ATTACHED)
        self.assertEqual(len(self.state["email_log"]), sent)
        domain.approve_and_send(self.state, self.job["id"], "Owner", unconditional_override="Paid by wire", read_file=self.read)
        self.assertEqual(self.inv["status"], "sent")

    def test_a_file_filed_as_other(self):
        self.check_gate(self.record("Release", "other", "UNCONDITIONAL WAIVER AND RELEASE"))

    def test_a_waiver_record_with_no_type(self):
        self.check_gate(self.record("Signed form", "waiver", "UNCONDITIONAL"))

    def test_send_reads_one_added_before_this_fix(self):
        rec = self.record("Release", "other", "UNCONDITIONAL WAIVER AND RELEASE")
        self.inv["extra_record_ids"] = [rec["id"]]
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(self.state, self.job["id"], "Owner", read_file=self.read)
        self.assertEqual(str(raised.exception), domain.UNCONDITIONAL_ATTACHED)

    def test_a_conditional_waiver_and_a_photo_go_through(self):
        self.add(self.record("Signed form", "waiver", "Conditional waiver upon payment, Pine kitchen"))
        photo = self.record("Site", "photo", "UNCONDITIONAL")
        self.add(photo)
        domain.approve_and_send(self.state, self.job["id"], "Owner", read_file=self.read)
        self.assertEqual(self.inv["status"], "sent")

    def test_the_waiver_step_reads_a_record_with_no_type(self):
        domain.approve_and_send(self.state, self.job["id"], "Owner")
        domain.match_payment(self.state, self.job["id"], self.inv["amount_cents"], "chk")
        rec = self.record("Signed form", "waiver", "Conditional waiver upon payment for another job")
        with self.assertRaises(domain.GateError) as raised:
            domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"], read_file=self.read)
        self.assertTrue(raised.exception.extra["look_warning"])
        self.assertIn(rec["label"], str(raised.exception))
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"], looked_at=rec["id"], read_file=self.read)
        self.assertTrue(self.inv["waiver_sent"])


class ReviewGateTests(unittest.TestCase):  # item 19, acceptance 80
    def setUp(self):
        self.state = new_state()
        self.job = auto_job(self.state)
        domain.record_sov_progress(self.state, self.job["id"], {sov.lines(self.job)[0]["id"]: "0.5"})
        clear_packet(self.state, self.job)
        self.inv = open_draft(self.job)

    def test_no_stamp_is_refused_and_says_where_to_look(self):
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(self.state, self.job["id"], "Sam", reviewed="")
        self.assertTrue(raised.exception.extra["review_changed"])
        self.assertEqual(raised.exception.extra["invoice_id"], self.inv["id"])
        self.assertEqual(self.inv["status"], "awaiting_approval")

    def test_a_stale_stamp_names_the_draft_that_goes_now(self):
        stamp = domain.review_stamp(self.state, self.job, self.inv)
        domain.record_sov_progress(self.state, self.job["id"], {sov.lines(self.job)[0]["id"]: "0.6"})
        new = open_draft(self.job)
        clear_packet(self.state, self.job)
        self.assertNotEqual(new["id"], self.inv["id"])
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(self.state, self.job["id"], "Sam", reviewed=stamp)
        self.assertEqual(raised.exception.extra["invoice_id"], new["id"])

    def test_the_stamp_the_page_showed_sends(self):
        domain.approve_and_send(self.state, self.job["id"], "Sam", reviewed=domain.review_stamp(self.state, self.job, self.inv))
        self.assertEqual(self.inv["status"], "sent")


class FoundByTheFuzzTest(unittest.TestCase):
    def test_a_credit_comes_off_in_this_period_on_the_sheet(self):
        state = new_state()
        job = auto_job(state)
        domain.add_change_order(state, job["id"], "Credit", -50_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        domain.record_sov_progress(state, job["id"], {sov.lines(job)[0]["id"]: "0.5"})
        draft = open_draft(job)
        self.assertEqual(draft["gross_cents"], 450_000)
        credit = next(e for e in draft["sov_sheet"] if e["scheduled_cents"] < 0)
        self.assertEqual((credit["prev_work_cents"], credit["work_this_cents"]), (0, -50_000))
        send_open(state, job)
        domain.record_sov_progress(state, job["id"], {sov.lines(job)[0]["id"]: "0.75"})
        credit = next(e for e in open_draft(job)["sov_sheet"] if e["scheduled_cents"] < 0)
        self.assertEqual((credit["prev_work_cents"], credit["work_this_cents"], credit["amount_cents"]), (-50_000, 0, 0))

    def test_the_card_counts_overhead_and_profit_on_a_line_corrected_below_the_bill(self):
        state = new_state()
        job = new_job(state, contract=1_150_000)
        domain.save_sov(state, job["id"], [{"item": "1", "description": "A", "quantity": "100", "unit": "ea", "rate": "50"},
                                           {"item": "2", "description": "B", "quantity": "100", "unit": "ea", "rate": "50"}], "15")
        a, b = line(job, "1")["id"], line(job, "2")["id"]
        daily(state, job, [{"line_id": a, "qty": "50"}])
        domain.record_sov_progress(state, job["id"], {})
        send_open(state, job)
        domain.correct_field_total(state, job["id"], a, "10", "Wrong", "Pat", below_billed=True)
        daily(state, job, [{"line_id": b, "qty": "10"}])
        said = domain._sov_view(job, state)["period"]["suggested_gross_cents"]
        self.assertEqual(said, 57_500, "10 at $50 is $500, plus 15% $75")
        domain.record_sov_progress(state, job["id"], {})
        self.assertEqual(open_draft(job)["gross_cents"], said)


if __name__ == "__main__":
    unittest.main()
