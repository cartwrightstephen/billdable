"""The independent hunt of Nov 5 (money, waivers and paperwork, with the domain only): each test is
one of the hunter's reproductions, kept as a check that the fix holds."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import qty_import
import sov
from test_money import new_job, new_state, send_open
from waiver_help import signed_waiver

TWO_LINES = [{"item": "1", "description": "Rough", "quantity": "100", "unit": "ea", "rate": "100"},
             {"item": "2", "description": "Panel", "quantity": "1", "unit": "LS", "rate": "10000"}]
ONE_LINE = [{"item": "1", "description": "Rough", "quantity": "1000", "unit": "ea", "rate": "100"}]


def sov_job(state, lines=None, retainage=0):
    job = new_job(state, contract=1, retainage=retainage)
    domain.save_sov(state, job["id"], lines or TWO_LINES, "0", update_contract=True)
    return job


def draft_of(job):
    return next((i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES), None)


def tried(state, fn, *args, **kw):
    """As the server runs a change: a refusal saves nothing, unless the refusal says it saves."""
    before = copy.deepcopy(state)
    try:
        fn(state, *args, **kw)
    except domain.GateError as exc:
        if not getattr(exc, "keep_changes", False):
            state.clear()
            state.update(before)
        return exc
    return None


def job_in(state, job_id):
    return next(j for j in state["jobs"] if j["id"] == job_id)


def waiver_item(invoice):
    return next(p for p in invoice["packet"] if p.get("waiver_kind"))


def others_in_hand(state, job, invoice, skip):
    for item in invoice["packet"]:
        if item is not skip and item["required"] and not item["present"]:
            domain.resolve_packet_item(state, job["id"], item["id"], reason="test")


class FieldTotalsAndTheDraft(unittest.TestCase):
    """Item 1: a draft drafted at the old field total follows the correction, even after a later job record."""

    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state, ONE_LINE)
        self.id = self.job["id"]
        self.line = sov.lines(self.job)[0]

    def later_record(self, qty="5"):
        domain.add_daily_record(self.state, self.id, "Work", details={"sections": {"quantities": [{"line_id": self.line["id"], "qty": qty}]}})

    def test_undo_import_redrafts_after_a_later_record(self):
        qty_import.apply(self.state, self.id, "1,Rough,90", "Sam", "q.csv")
        domain.record_sov_progress(self.state, self.id, {})
        self.assertEqual(draft_of(self.job)["gross_cents"], 900_000)
        self.later_record()
        record = next(r for r in self.job["records"] if r.get("quantities") and r.get("kind") != "daily")
        domain.undo_quantity_import(self.state, self.id, record["id"], "Sam")
        self.assertEqual(sov.field_total(self.line), 5000)
        self.assertEqual(draft_of(self.job)["gross_cents"], 50_000, "the draft bills what the field now says")
        self.assertNotIn("keeps the quantity typed", self.state["audit"][-1]["detail"])

    def test_correct_field_total_redrafts_after_a_later_record(self):
        self.later_record("90")
        domain.record_sov_progress(self.state, self.id, {})
        self.later_record("1")
        domain.correct_field_total(self.state, self.id, self.line["id"], "10", "Typo", "Sam")
        self.assertEqual(draft_of(self.job)["gross_cents"], 100_000)
        self.assertNotIn("keeps the quantity typed", self.state["audit"][-1]["detail"])

    def test_a_typed_figure_still_stays(self):
        self.later_record("90")
        domain.record_sov_progress(self.state, self.id, {self.line["id"]: "70"})
        self.later_record("1")
        domain.correct_field_total(self.state, self.id, self.line["id"], "10", "Typo", "Sam")
        self.assertEqual(draft_of(self.job)["gross_cents"], 700_000)
        self.assertIn("keeps the quantity typed", self.state["audit"][-1]["detail"])


class FinalWaiverOnlyWhenNothingIsLeft(unittest.TestCase):
    """Item 2: a final waiver gives up the right to what is still unbilled or unpaid."""

    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)
        self.id = self.job["id"]
        domain.mark_done(self.state, self.id)
        self.first = send_open(self.state, self.job)

    def signed_co(self, cents=100_000):
        domain.add_change_order(self.state, self.id, "Extra", cents)
        domain.approve_change_order(self.state, self.id, self.job["change_orders"][-1]["id"])

    def test_progress_while_a_signed_co_is_unbilled(self):
        self.signed_co()
        domain.match_payment(self.state, self.id, self.first["amount_cents"], "c", invoice_id=self.first["id"])
        self.assertEqual(domain.suggested_waiver_kind(self.job, self.first), "unconditional_progress")

    def test_progress_while_a_later_pay_app_is_owed(self):
        self.signed_co()
        domain.draft_invoice(self.state, self.id, strict=True)
        second = send_open(self.state, self.job)
        self.assertNotEqual(second["id"], self.first["id"])
        domain.match_payment(self.state, self.id, self.first["amount_cents"], "c", invoice_id=self.first["id"])
        self.assertEqual(domain.suggested_waiver_kind(self.job, self.first), "unconditional_progress")
        domain.match_payment(self.state, self.id, second["amount_cents"], "c2", invoice_id=second["id"])
        self.assertEqual(domain.suggested_waiver_kind(self.job, second), "unconditional_final")

    def test_sending_it_anyway_warns(self):
        self.signed_co()
        domain.match_payment(self.state, self.id, self.first["amount_cents"], "c", invoice_id=self.first["id"])
        signed_waiver(self.state, self.id, "unconditional_final")
        sent = len(self.state["email_log"])
        with self.assertRaises(domain.GateError) as refused:
            domain.send_waiver(self.state, self.id, "Sam", invoice_id=self.first["id"], kind="unconditional_final")
        self.assertTrue(refused.exception.extra.get("final_waiver_warning"))
        self.assertIn("$1,000.00", str(refused.exception), "it says what is left")
        self.assertEqual(len(self.state["email_log"]), sent)

    def test_the_final_one_still_fits_when_it_is_all_paid(self):
        domain.match_payment(self.state, self.id, self.first["amount_cents"], "c", invoice_id=self.first["id"])
        self.assertEqual(domain.suggested_waiver_kind(self.job, self.first), "unconditional_final")

    def test_a_final_pay_app_on_a_job_undone_is_progress(self):
        self.first["final"] = True
        self.job["work_status"] = "in_progress"
        domain.match_payment(self.state, self.id, self.first["amount_cents"], "c", invoice_id=self.first["id"])
        self.assertEqual(domain.suggested_waiver_kind(self.job, self.first), "unconditional_progress")


class ConditionalWaiverOnAFinalDraft(unittest.TestCase):
    """Item 2's audit: the conditional waiver on a final draft, with an earlier pay app still owed."""

    def test_progress_until_the_earlier_one_is_paid(self):
        state = new_state()
        job = sov_job(state)
        line = sov.lines(job)[0]
        domain.record_sov_progress(state, job["id"], {line["id"]: "50"})
        first = send_open(state, job)
        domain.mark_done(state, job["id"])
        draft = draft_of(job)
        self.assertTrue(draft["final"])
        self.assertEqual(waiver_item(draft)["waiver_kind"], "conditional_progress", "$5,000.00 is still owed on PA-01")
        domain.match_payment(state, job["id"], first["amount_cents"], "c", invoice_id=first["id"])
        self.assertEqual(waiver_item(draft)["waiver_kind"], "conditional_final")

    def test_an_unpaid_final_pay_app_on_its_own_is_conditional_final(self):
        state = new_state()
        job = sov_job(state)
        domain.mark_done(state, job["id"])
        invoice = send_open(state, job)
        self.assertEqual(domain.suggested_waiver_kind(job, invoice), "conditional_final")


class OldRetainageReleases(unittest.TestCase):
    """Item 3: a release stored as final on a job not finished, which owes a progress waiver."""

    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state, retainage=10)
        self.id = self.job["id"]
        line = sov.lines(self.job)[0]
        domain.record_sov_progress(self.state, self.id, {line["id"]: "50"})
        invoice = send_open(self.state, self.job)
        domain.match_payment(self.state, self.id, invoice["amount_cents"], "c", invoice_id=invoice["id"])
        domain.waiver_not_needed(self.state, self.id, "Paid by wire", invoice_id=invoice["id"], actor="Sam")
        domain.record_retainage_payment(self.state, self.id, domain.job_money(self.job)["retainage_open_cents"], "r")
        self.release = self.job["retainage_waivers"][-1]
        self.assertEqual(self.release["kind"], "unconditional_progress")

    def view_release(self):
        return domain.job_view(self.state, self.job)["retainage_waivers"][-1]

    def test_the_page_asks_for_the_progress_one(self):
        self.release["kind"] = "unconditional_final"   # as recorded before Nov 5
        self.assertEqual(self.view_release()["kind"], "unconditional_progress")
        signed_waiver(self.state, self.id, "unconditional_progress")
        shown = self.view_release()
        self.assertTrue(shown["on_file"], "the progress one on file is the one this release sends")
        domain.send_waiver(self.state, self.id, "Sam", release_id=self.release["id"])
        self.assertEqual(self.release["kind"], "unconditional_progress")
        self.assertTrue(self.release["waiver_sent"])

    def test_a_release_final_on_a_job_undone(self):
        self.release["kind"] = "unconditional_final"
        self.job["work_status"] = "complete"
        self.job["work_status"] = "in_progress"
        self.assertEqual(domain.release_due_kind(self.job, self.release), "unconditional_progress")

    def test_a_release_on_a_finished_job_stays_final(self):
        self.release["kind"] = "unconditional_final"
        domain.edit_job(self.state, self.id, {"retainage_pct": 0})
        domain.mark_done(self.state, self.id)
        send_open(self.state, self.job)
        final = self.job["invoices"][-1]
        domain.match_payment(self.state, self.id, final["amount_cents"], "c2", invoice_id=final["id"])
        self.assertEqual(domain.release_due_kind(self.job, self.release), "unconditional_final")
        self.assertEqual(self.view_release()["kind"], "unconditional_final")

    def test_a_waiver_sent_keeps_what_it_was_sent_as(self):
        signed_waiver(self.state, self.id, "unconditional_progress")
        domain.send_waiver(self.state, self.id, "Sam", release_id=self.release["id"])
        self.release["kind"] = "unconditional_final"
        self.assertEqual(self.view_release()["kind"], "unconditional_final")


class NoDeadEndAtSend(unittest.TestCase):
    """Item 4: when a draft stops or starts being the final one, an item waiting shows at once."""

    def confirmed(self, state, job):
        draft = draft_of(job)
        item = waiver_item(draft)
        others_in_hand(state, job, draft, item)
        record = signed_waiver(state, job["id"], "conditional_progress")
        domain.attach_record_to_packet(state, job["id"], item["id"], record["id"], answer="conditional")
        self.assertTrue(item["present"], item)
        return item

    def assert_way_forward(self, state, job):
        """The item waits for a look, as blockers and on the page, and Confirm clears it; then it sends."""
        draft = draft_of(job)
        item = waiver_item(draft)
        self.assertFalse(item["present"], "it waits as soon as it flips")
        self.assertEqual(draft["status"], "draft")
        blocker = next(b for b in domain.blocker_items(job) if b.get("item_id") == item["id"])
        self.assertTrue(blocker["waiting"])
        domain.confirm_packet_item(state, job["id"], item["id"], "Sam", looked=True, answer="conditional")
        self.assertTrue(item["present"])
        self.assertEqual(domain.blocker_lines(job), [])
        self.assertIsNone(tried(state, domain.approve_and_send, job["id"], "Sam", reviewed=domain.review_stamp(state, job, draft)))

    def test_withdrawing_the_change_order(self):
        state = new_state()
        job = sov_job(state)
        domain.add_change_order(state, job["id"], "Extra", 100_000)
        domain.mark_done(state, job["id"])
        self.assertFalse(draft_of(job)["final"])
        self.confirmed(state, job)
        domain.withdraw_change_order(state, job["id"], job["change_orders"][0]["id"])
        self.assertTrue(draft_of(job)["final"])
        self.assert_way_forward(state, job)

    def test_recording_the_retainage_paid(self):
        state = new_state()
        job = sov_job(state, retainage=10)
        line = sov.lines(job)[0]
        domain.record_sov_progress(state, job["id"], {line["id"]: "50"})
        invoice = send_open(state, job)
        domain.match_payment(state, job["id"], invoice["amount_cents"], "c", invoice_id=invoice["id"])
        domain.edit_job(state, job["id"], {"retainage_pct": 0})
        domain.mark_done(state, job["id"])
        self.confirmed(state, job)
        domain.record_retainage_payment(state, job["id"], domain.job_money(job)["retainage_open_cents"], "r")
        self.assert_way_forward(state, job)

    def test_a_flip_a_refusal_finds_is_kept_and_shown(self):
        """A draft saved in the dead end before this fix: the refused send saves the flip and redraws."""
        state = new_state()
        job = sov_job(state)
        domain.add_change_order(state, job["id"], "Extra", 100_000)
        domain.mark_done(state, job["id"])
        self.confirmed(state, job)
        job["change_orders"][0]["status"] = "withdrawn"   # as withdraw_change_order left it before
        refused = tried(state, domain.approve_and_send, job["id"], "Sam", reviewed=domain.review_stamp(state, job, draft_of(job)))
        self.assertIsNotNone(refused)
        self.assertTrue(refused.extra.get("review_changed"))
        job = job_in(state, job["id"])
        self.assert_way_forward(state, job)


class Overbilling(unittest.TestCase):
    """Item 6: a refund or credit is for what was collected above the contract, not forever."""

    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state, [{"item": "1", "description": "Rough", "quantity": "200", "unit": "ea", "rate": "100"}])
        self.id = self.job["id"]
        domain.mark_done(self.state, self.id)
        self.invoice = send_open(self.state, self.job)

    def credit(self, cents):
        domain.add_change_order(self.state, self.id, "Credit", -cents)
        domain.approve_change_order(self.state, self.id, self.job["change_orders"][-1]["id"])

    def overbilled(self):
        return domain.job_money(self.job)["overbilled_cents"]

    def test_unpaid_is_refused_in_plain_words(self):
        self.credit(500_000)
        with self.assertRaises(domain.GateError) as refused:
            domain.settle_overbilling(self.state, self.id, "refund")
        self.assertIn("Nothing is paid", str(refused.exception))
        self.assertIn("Pull it back", str(refused.exception))
        self.assertEqual(self.overbilled(), 500_000)

    def test_paid_settles_and_a_later_credit_shows(self):
        domain.match_payment(self.state, self.id, self.invoice["amount_cents"], "c")
        self.credit(500_000)
        domain.settle_overbilling(self.state, self.id, "refund 1")
        self.assertEqual(self.overbilled(), 0)
        self.credit(200_000)
        self.assertEqual(self.overbilled(), 200_000)
        text = next(b["text"] for b in domain.close_blocker_items(self.job) if b["kind"] == "overbilled")
        self.assertIn("contract total of $13,000.00", text)
        self.assertIn("$5,000.00", text, "what was already refunded is said")

    def test_only_what_was_collected_above_the_contract(self):
        domain.match_payment(self.state, self.id, 1_600_000, "c")
        self.credit(500_000)
        domain.settle_overbilling(self.state, self.id, "refund")
        self.assertEqual(self.job["overbilling_settled"][-1]["cents"], 100_000)
        self.assertEqual(self.overbilled(), 400_000, "$16,000 paid on a $15,000 contract: $1,000 can be refunded")
        with self.assertRaises(domain.GateError):
            domain.settle_overbilling(self.state, self.id, "again")

    def test_not_above_the_contract_is_refused(self):
        domain.match_payment(self.state, self.id, 1_000_000, "c")
        self.credit(500_000)
        with self.assertRaises(domain.GateError) as refused:
            domain.settle_overbilling(self.state, self.id, "refund")
        self.assertIn("$10,000.00", str(refused.exception))
        self.assertIn("$15,000.00", str(refused.exception))

    def test_a_settlement_from_before_is_dropped_on_pull_back(self):
        """Old data: settled on a pay app nobody paid. The hunter's sequence, with the audit."""
        self.credit(500_000)
        self.job["overbilling_settled"] = [{"cents": 500_000, "returned_cents": 500_000, "by": "Sam", "at": "2026-11-01T00:00:00+00:00", "reason": "refund"}]
        domain.void_sent_pay_app(self.state, self.id, self.invoice["id"], "Wrong")
        self.assertEqual(self.job.get("overbilling_settled") or [], [])
        self.assertTrue(any(a["action"] == "overbilling_settled_dropped" for a in self.state["audit"]))
        domain.draft_invoice(self.state, self.id, strict=True) if not draft_of(self.job) else None
        send_open(self.state, self.job)
        self.assertEqual(domain.job_money(self.job)["billed_gross_cents"], 1_500_000)
        self.credit(200_000)
        self.assertEqual(self.overbilled(), 200_000)

    def test_the_blocker_says_the_real_contract_total(self):
        self.credit(500_000)
        item = next(b for b in domain.close_blocker_items(self.job) if b["kind"] == "overbilled")
        self.assertIn("$20,000.00 billed against a contract total of $15,000.00", item["text"])
        self.assertFalse(item["settleable"], "nothing paid: the page offers Pull back, not Record refund")


class ConfirmWhatWasShown(unittest.TestCase):
    """Item 8: Confirm clears the file on the screen, or says it changed."""

    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)
        self.id = self.job["id"]
        self.old = signed_waiver(self.state, self.id, "conditional_progress", label="Conditional waiver OLD")
        domain.record_sov_progress(self.state, self.id, {sov.lines(self.job)[0]["id"]: "10"})
        self.item = waiver_item(draft_of(self.job))

    def swap(self):
        new = signed_waiver(self.state, self.id, "conditional_progress", label="Conditional waiver NEW")
        domain.edit_record(self.state, self.id, new["id"], new["label"], "waiver", "Bob")
        self.assertEqual([a["record_id"] for a in self.item["attachments"]], [new["id"]])
        return new

    def test_a_file_swapped_under_the_page_is_refused(self):
        self.swap()
        with self.assertRaises(domain.GateError) as refused:
            domain.confirm_packet_item(self.state, self.id, self.item["id"], "Ann", shown=[self.old["id"]])
        self.assertTrue(refused.exception.extra.get("review_changed"))
        self.assertIn("Conditional waiver NEW", str(refused.exception))
        self.assertFalse(self.item["present"])

    def test_the_file_on_the_screen_clears(self):
        domain.confirm_packet_item(self.state, self.id, self.item["id"], "Ann", shown=[self.old["id"]], answer="conditional")
        self.assertTrue(self.item["present"])
        self.assertEqual(self.item["confirmed_by"], "Ann")


class NameOnlyCloseout(unittest.TestCase):
    """Item 9: closeout items in only on names are not in, as on a pay app."""

    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state, retainage=10)
        self.id = self.job["id"]
        domain.mark_done(self.state, self.id)
        self.invoice = send_open(self.state, self.job)
        self.item = self.job["closeout_packet"][0]
        for item in self.job["closeout_packet"]:
            item["required"] = True
            item["present"] = True
            item["attachments"] = [{"name": "on paper"}]
            for key in ("override", "marked_in_hand", "present_reason"):
                item.pop(key, None)

    def test_it_counts_as_missing_and_says_why(self):
        missing = domain.closeout_missing(self.job)
        self.assertEqual(len(missing), len(self.job["closeout_packet"]))
        shown = domain.job_view(self.state, self.job)
        self.assertTrue(shown["closeout_packet"][0]["name_only"])
        self.assertFalse(shown["closeout_packet"][0]["present"])
        self.assertIn("No file was kept for it", domain._blocker_text(self.job, self.item))

    def test_asking_for_the_retainage_waits(self):
        domain.match_payment(self.state, self.id, self.invoice["amount_cents"], "c", invoice_id=self.invoice["id"])
        with self.assertRaises(domain.GateError) as refused:
            domain.chase_retainage(self.state, self.id)
        self.assertIn("No file was kept for it", " ".join(refused.exception.blockers))

    def test_a_real_file_or_an_override_still_counts(self):
        rec = signed_waiver(self.state, self.id, None, label="Warranty")
        self.item["attachments"] = [{"name": "Warranty", "record_id": rec["id"]}]
        self.assertNotIn(self.item, domain.closeout_missing(self.job))


class CreditWithNothingElseToBill(unittest.TestCase):
    """Item 13: a signed credit with nothing else to bill suggests $0, and names the credit."""

    def test_the_card_shows_zero_and_the_credit(self):
        state = new_state()
        job = sov_job(state)
        domain.add_change_order(state, job["id"], "Deduct tile", -200_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        period = domain.job_view(state, job)["sov_view"]["period"]
        self.assertEqual(period["suggested_gross_cents"], 0)
        self.assertEqual(period["suggested_net_cents"], 0)
        self.assertEqual(period["credit_waiting_cents"], 200_000)
        self.assertIn("Deduct tile", " ".join(period["credit_waiting_names"]))


if __name__ == "__main__":
    unittest.main()
