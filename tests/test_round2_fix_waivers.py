"""Round 2 review fixes, waivers: each part of a pay app's money settles its own waiver once (part
payment, short pay, late money after it), the final lien release is still owed before close, and a
form settled with one waiver isn't sent again for another (decisions 5 and 6)."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import approve_help  # noqa: E402  every pay app is signed before it goes (Stephen, 18 Nov)
import waiver_form
from test_money import clear_packet, new_job, new_state, send_open
from waiver_help import signed_waiver


def owed(job):
    return [inv["number"] for inv in job["invoices"] if inv.get("waiver_due") and not inv.get("waiver_sent")]


class PayAppWaivers(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = send_open(self.state, self.job)   # PA-01, $4,000

    def pay(self, cents, ref):
        domain.match_payment(self.state, self.job["id"], cents, ref, invoice_id=self.inv["id"])

    def send(self, label):
        signed_waiver(self.state, self.job["id"], "unconditional_progress", label)
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"])

    def test_late_money_after_a_part_waiver_and_a_short_pay_owes_its_own_waiver(self):  # R2 (a)
        self.pay(150_000, "c1")
        self.send("Part waiver")
        domain.settle_short_pay(self.state, self.job["id"], self.inv["id"], "Backcharge", "Sam")
        self.assertEqual(owed(self.job), [])
        self.pay(100_000, "c2")
        self.assertEqual(owed(self.job), [self.inv["number"]])
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 100_000)
        self.assertIn("waiver", [b["kind"] for b in domain.close_blocker_items(self.job)])

    def test_late_money_after_the_short_pay_waiver_went_owes_its_own(self):  # B22
        self.pay(150_000, "c1")
        domain.settle_short_pay(self.state, self.job["id"], self.inv["id"], "Backcharge", "Sam")
        self.send("Waiver for $1,500")
        self.assertEqual(self.inv["waiver_covered_cents"], 150_000)
        self.pay(250_000, "c2")
        self.assertEqual(owed(self.job), [self.inv["number"]])
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 250_000)
        self.assertEqual(self.job["external_status"], "Payment received, waiver not sent")
        self.assertEqual([w["cents"] for w in self.inv["partial_waivers"]], [150_000], "the $1,500 waiver stays listed")
        self.send("Waiver for $2,500")
        self.assertIn("Amount covered: $2,500.00.", self.state["email_log"][-1]["body"])
        self.assertEqual(owed(self.job), [])

    def test_a_part_waiver_settled_short_is_listed_once(self):  # B14
        self.pay(100_000, "c1")
        self.send("Part waiver")
        domain.settle_short_pay(self.state, self.job["id"], self.inv["id"], "Backcharge", "Sam")
        self.assertEqual(len(self.inv["partial_waivers"]), 1)
        self.assertFalse(self.inv["waiver_sent"] or self.inv["waiver_due"])
        for key in ("waiver_sent_at", "waiver_kind", "waiver_not_needed", "waiver_record_id"):
            self.assertNotIn(key, self.inv)
        self.assertNotIn("waiver", [b["kind"] for b in domain.close_blocker_items(self.job)])

    def test_a_part_waiver_marked_not_needed_then_settled_short_is_listed_once(self):  # B14
        self.pay(100_000, "c1")
        domain.waiver_not_needed(self.state, self.job["id"], "Handed over on site", invoice_id=self.inv["id"], actor="Sam")
        domain.settle_short_pay(self.state, self.job["id"], self.inv["id"], "Backcharge", "Sam")
        self.assertNotIn("waiver_not_needed", self.inv)
        self.assertEqual([w.get("not_needed") for w in self.inv["partial_waivers"]], [True])

    def test_a_client_mark_on_a_part_payment_goes_once_the_client_takes_waivers_again(self):  # B5, B16, B36
        payer = self.state["payers"][0]
        payer["waiver_not_needed"] = {"by": "Sam", "at": "2026-11-01T00:00:00+00:00", "reason": "GC doesn't take them"}
        self.pay(100_000, "c1")
        self.assertEqual(owed(self.job), [])
        domain.client_waiver_needed_again(self.state, payer["id"], "Sam")
        self.pay(300_000, "c2")
        self.assertEqual(owed(self.job), [self.inv["number"]])
        self.assertNotIn("waiver_not_needed", self.inv)
        self.assertEqual(domain.waiver_stages(self.state, self.job, self.inv)["unconditional"]["status"], "Needed")
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 300_000, "the part the client didn't take one for stays settled")
        self.send("Waiver for the rest")
        self.assertEqual(owed(self.job), [])

    def test_a_form_tied_to_a_part_marked_not_needed_is_not_sent_for_the_rest(self):  # B33
        self.pay(200_000, "c1")
        rec = signed_waiver(self.state, self.job["id"], "unconditional_progress", "Waiver for $2,000")
        domain.set_waiver_type(self.state, self.job["id"], rec["id"], "unconditional_progress", "Sam", for_id=self.inv["id"])
        domain.waiver_not_needed(self.state, self.job["id"], "Handed the signed form to the GC on site", invoice_id=self.inv["id"], actor="Sam")
        self.pay(200_000, "c2")
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 200_000)
        self.assertIsNone(domain.waiver_on_file(self.state, self.job, "unconditional_progress", self.inv["id"]))
        self.assertEqual(domain.waiver_stages(self.state, self.job, self.inv)["unconditional"]["status"], "Needed")
        with self.assertRaises(domain.GateError):
            domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"])

    def test_an_untied_form_for_a_part_marked_not_needed_is_not_sent_for_the_rest(self):  # B33 round 2
        for kind in ("unconditional_progress", None):   # typed, or as Choose file leaves it
            with self.subTest(kind=kind):
                self.setUp()
                self.pay(200_000, "c1")
                rec = signed_waiver(self.state, self.job["id"], kind, "Waiver for $2,000")
                self.assertEqual(domain.waiver_on_file(self.state, self.job, "unconditional_progress", self.inv["id"])["record"], rec)
                domain.waiver_not_needed(self.state, self.job["id"], "Handed the signed form to the GC on site", invoice_id=self.inv["id"], actor="Sam")
                self.pay(200_000, "c2")
                self.assertIsNone(domain.waiver_on_file(self.state, self.job, "unconditional_progress", self.inv["id"]))
                self.assertEqual(domain.waiver_stages(self.state, self.job, self.inv)["unconditional"]["status"], "Needed")
                with self.assertRaises(domain.GateError):
                    domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"])


class FinalWaiverAfterShortPay(unittest.TestCase):  # R2 (b)
    def test_a_final_pay_app_part_paid_then_settled_short_still_owes_the_final_waiver(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        self.assertTrue(inv["final"])
        domain.match_payment(state, job["id"], 600_000, "c1", invoice_id=inv["id"])
        signed_waiver(state, job["id"], "unconditional_progress", "Part waiver")
        domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"])
        domain.settle_short_pay(state, job["id"], inv["id"], "Backcharge", "Sam")
        self.assertEqual(owed(job), [inv["number"]])
        self.assertIn("A lien waiver is still due.", domain.close_blockers(job))
        self.assertEqual(domain.suggested_waiver_kind(job, inv), "unconditional_final")
        self.assertEqual(domain.waiver_cents(job, inv), 600_000)
        signed_waiver(state, job["id"], "unconditional_final", "Final waiver")
        domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"])
        self.assertEqual(inv["waiver_kind"], "unconditional_final")
        self.assertIn("Amount covered: $6,000.00.", state["email_log"][-1]["body"])
        self.assertEqual(owed(job), [])
        self.assertEqual(len(inv["partial_waivers"]), 1)

    def test_a_progress_pay_app_settled_short_owes_nothing_more(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], 150_000, "c1", invoice_id=inv["id"])
        signed_waiver(state, job["id"], "unconditional_progress")
        domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"])
        domain.settle_short_pay(state, job["id"], inv["id"], "Backcharge", "Sam")
        self.assertEqual(owed(job), [])


class NoFinalWaiverAttachedEarly(unittest.TestCase):  # B2, B32: decision 6 has no override
    def test_a_pay_app_cannot_carry_an_unconditional_final_waiver_with_a_reason(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        inv = clear_packet(state, job)
        rec = signed_waiver(state, job["id"], "unconditional_final", "Unconditional final waiver")
        domain.change_payapp_attachments(state, job["id"], inv["id"], "add_record", rec["id"])
        domain.answer_waiver_question(state, job["id"], rec["id"], "unconditional", "Owner")
        with self.assertRaises(domain.GateError) as refused:
            approve_help.approve_and_send(state, job["id"], "Owner", unconditional_override="GC asked")
        self.assertTrue(refused.exception.extra["final_waiver_held"])
        self.assertNotIn("unconditional_override", refused.exception.fields or {})
        self.assertEqual(inv["status"] in domain.DRAFT_STATUSES, True)
        self.assertEqual(state["email_log"], [])

    def test_the_owner_reason_still_covers_an_unconditional_progress_waiver(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        inv = clear_packet(state, job)
        rec = signed_waiver(state, job["id"], "unconditional_progress", "Unconditional waiver")
        domain.change_payapp_attachments(state, job["id"], inv["id"], "add_record", rec["id"])
        domain.answer_waiver_question(state, job["id"], rec["id"], "unconditional", "Owner")
        with self.assertRaises(domain.GateError) as refused:
            approve_help.approve_and_send(state, job["id"], "Owner")
        self.assertEqual(str(refused.exception), domain.UNCONDITIONAL_ATTACHED)
        approve_help.approve_and_send(state, job["id"], "Owner", unconditional_override="GC asked")
        self.assertEqual(inv["status"], "sent")

    def test_a_final_waiver_on_the_earlier_payment_item_waits_for_the_money(self):  # B2, B32 round 2
        # The client's list asks for an unconditional waiver for the earlier payment: a form typed
        # unconditional final on that item is still the final lien release.
        from test_packet import new_job as packet_job
        state = new_state()
        job = packet_job(state, contract=1_000_000, packet="Signed contract\nConditional waiver\nUnconditional waiver for previous payment")
        domain.record_progress(state, job["id"], 400_000)
        inv = next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        target = next(it for it in inv["packet"] if str(it.get("waiver_kind", "")).startswith("unconditional"))
        for it in inv["packet"]:
            if it is not target:
                domain.resolve_packet_item(state, job["id"], it["id"], reason="x")
        rec = signed_waiver(state, job["id"], "unconditional_final", "Unconditional final release")
        domain.attach_record_to_packet(state, job["id"], target["id"], rec["id"], "Sam")
        domain.confirm_packet_item(state, job["id"], target["id"], "Sam", answer="unconditional")
        with self.assertRaises(domain.GateError) as refused:
            approve_help.approve_and_send(state, job["id"], "Owner", unconditional_override="GC asked")
        self.assertTrue(refused.exception.extra["final_waiver_held"])
        self.assertIn(inv["status"], domain.DRAFT_STATUSES)
        self.assertEqual(state["email_log"], [])

    def retainage_job(self, kind):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.record_progress(state, job["id"], 1_000_000)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
        item = None
        for it in job["closeout_packet"]:
            if domain.is_waiver_item(it):
                item = it
            else:
                domain.resolve_packet_item(state, job["id"], it["id"], "Sam", reason="x")
        rec = signed_waiver(state, job["id"], kind, "Waiver")
        domain.attach_record_to_packet(state, job["id"], item["id"], rec["id"], "Sam")
        domain.confirm_packet_item(state, job["id"], item["id"], "Sam", answer="unconditional")
        return state, job, rec

    def test_a_retainage_request_cannot_carry_an_unconditional_final_waiver_with_a_reason(self):
        state, job, rec = self.retainage_job(None)
        self.assertEqual(rec["waiver_kind"], "unconditional_final")
        with self.assertRaises(domain.GateError) as refused:
            domain.chase_retainage(state, job["id"], "Owner", unconditional_override="Retainage paid by wire already")
        self.assertEqual(refused.exception.extra["final_waiver_held"], ["$1,000.00 of retainage is still held"])
        self.assertFalse(job.get("retainage_chases"))


class GeorgiaFormTypedText(unittest.TestCase):  # B35
    def test_a_value_reading_like_the_page_marker_prints_as_typed(self):
        form = domain.waiver_forms()["states"]["GA"]["statutory_forms"]["interim"]
        pdf = waiver_form.render(form["paragraphs"], form["title"], {"project": "Tower PAGEREF9 annex"})
        self.assertIn(b"(Tower PAGEREF9 annex) Tj", pdf)
        for head, body in re.findall(rb"/Length (\d+) >>\nstream\n(.*?)\nendstream", pdf, re.S):
            self.assertEqual(int(head), len(body))
        widgets = re.findall(rb"/Subtype /Widget .*? /P ([^>]*) >>", pdf)
        self.assertTrue(widgets)
        self.assertTrue(all(re.fullmatch(rb"\d+ 0 R", w) for w in widgets), widgets)


if __name__ == "__main__":
    unittest.main()
