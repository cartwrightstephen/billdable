"""The last Round 1 blockers (the Nov 6 hunt, hunt-r1b) and the waiver rule that replaces the text
heuristics. Which waiver a file is, is the person's answer: one tap on a waiver item, kept on the
record with who and when, and the owner gate for an unconditional answer where a conditional waiver
is needed. Then: a hand-set dollar amount on one line survives the progress form sent for another
(d1_giveway.py), retainage released early can be recorded and its waiver sent, and billing by
dollars keeps the date and notes (d2_dollars.py)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import server
import sov
from test_blockers import GOOD, Case, pdf
from test_money import new_job, new_state, send_open
from test_packet import draft
from waiver_help import signed_waiver
import unconditional_corpus as corpus


class WhichWaiverIsThis(Case):
    def test_every_file_on_a_waiver_item_waits_for_the_answer_kept_with_who_and_when(self):
        rec = self.stored("Waiver", pdf(GOOD), typed="")
        self.attach(rec, answer="")
        self.assertEqual(self.waiver["check"]["status"], "matched", "it matched everything the check reads")
        self.assertFalse(self.waiver["present"], "and still waits for the answer")
        self.assertEqual(domain.item_question(self.job, self.waiver), {"guess": "conditional", "reads_as": "conditional"})
        with self.assertRaises(domain.GateError):
            domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read)
        with self.assertRaises(domain.GateError) as raised:
            domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", read_file=self.read, answer="maybe")
        self.assertIn("answer", raised.exception.fields)
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", read_file=self.read, shown=[rec["id"]], answer="conditional")
        self.assertTrue(self.waiver["present"])
        said = rec["waiver_answer"]
        self.assertEqual((said["kind"], said["by"], said["file_ids"]), ("conditional", "Sam", rec["file_ids"]))
        self.assertTrue(said["at"])
        self.assertEqual(rec["waiver_kind"], "conditional_progress")
        self.assertEqual(self.waiver["looked_as"], "conditional")

    def test_the_guess_that_disagrees_with_the_item_says_so(self):
        rec = self.stored("Waiver", corpus.CA_8134.encode(), "w.txt", typed="")
        self.attach(rec, answer="")
        self.assertEqual(domain.item_question(self.job, self.waiver), {"guess": "unconditional", "reads_as": "unconditional"})
        self.assertIn("This reads as unconditional.", self.waiver["check"]["problems"])

    def test_an_unconditional_answer_takes_the_owner_whatever_the_text_says(self):
        rec = self.stored("Waiver", pdf(GOOD), typed="")
        self.attach(rec, answer="unconditional")
        self.assertTrue(self.waiver["present"])
        for item in self.inv["packet"]:
            if not item["present"]:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="x")
        with self.assertRaises(domain.GateError) as refused:
            domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read)
        self.assertEqual(str(refused.exception), domain.UNCONDITIONAL_ATTACHED)
        self.assertIn("Sam said it is the unconditional waiver", " ".join(refused.exception.blockers))
        domain.approve_and_send(self.state, self.job["id"], "Owner", read_file=self.read, unconditional_override="Paid by wire")
        self.assertEqual(self.inv["unconditional_override"]["record_ids"], [rec["id"]])

    def test_a_record_typed_before_keeps_its_type_and_its_type_is_picked_first(self):
        rec = self.stored("Waiver", b"\xff\xd8 jpeg", "w.jpg", typed="unconditional_progress")
        self.attach(rec, answer="")
        self.assertEqual(rec["waiver_kind"], "unconditional_progress", "nothing retypes it")
        self.assertEqual(domain.item_question(self.job, self.waiver)["guess"], "unconditional")
        self.assertFalse(self.waiver["present"], "a type from before is not the answer")

    def test_typing_a_record_is_the_answer_and_retyping_it_moves_the_gate(self):
        rec = self.stored("Waiver", pdf(GOOD), typed="")
        domain.set_waiver_type(self.state, self.job["id"], rec["id"], "unconditional_progress", "Ann")
        self.assertEqual((rec["waiver_answer"]["kind"], rec["waiver_answer"]["by"]), ("unconditional", "Ann"))
        self.attach(rec, answer="")
        self.assertTrue(self.waiver["present"], "already answered for these files")
        self.assertEqual(domain.unconditional_waivers_attached(self.job, self.inv), [rec])
        domain.set_waiver_type(self.state, self.job["id"], rec["id"], "conditional_progress", "Ann")
        self.assertEqual(domain.unconditional_waivers_attached(self.job, self.inv), [])

    def test_a_file_added_to_an_answered_record_asks_again(self):
        rec = self.stored("Waiver", pdf(GOOD), typed="")
        self.attach(rec)
        self.assertTrue(self.waiver["present"])
        self.state["files"].append({"id": "file-x", "name": "x.jpg", "ext": ".jpg", "size": 3})
        rec["file_ids"].append("file-x")
        domain._refresh_draft_gate(self.job, self.state)
        self.assertFalse(self.waiver["present"])
        self.assertEqual(domain.waiver_answer(rec), "", "the answer was for the files then")

    def test_an_old_draft_in_from_before_the_question_asks_it_at_send(self):
        rec = self.stored("Waiver", pdf(GOOD))
        self.attach(rec)
        rec.pop("waiver_answer")   # confirmed before the question
        for item in self.inv["packet"]:
            if not item["present"]:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="x")
        self.waiver["present"] = True   # as it was saved
        with self.assertRaises(domain.GateError) as refused:
            domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read)
        self.assertTrue(refused.exception.keep_changes and refused.exception.extra["waiver_question"])
        self.assertFalse(self.waiver["present"])
        self.assertEqual(self.inv["status"], "draft")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", read_file=self.read, answer="conditional")
        domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read)
        self.assertEqual(self.inv["status"], "sent")

    def test_a_sent_pay_app_never_changes(self):
        rec = self.stored("Waiver", pdf(GOOD))
        self.attach(rec)
        for item in self.inv["packet"]:
            if not item["present"]:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="x")
        domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read)
        before = [dict(p) for p in self.inv["packet"]]
        rec.pop("waiver_answer")
        domain.record_progress(self.state, self.job["id"], 500_000)
        domain._refresh_draft_gate(self.job, self.state)
        self.assertEqual([dict(p) for p in self.inv["packet"]], before)

    def test_a_record_said_to_be_no_waiver_is_not_sent_as_one_after_payment(self):
        rec = self.stored("Not one", pdf(GOOD), typed="conditional_progress")
        rec["waiver_answer"] = {"kind": "no", "by": "Sam", "at": "x", "file_ids": list(rec["file_ids"])}
        self.assertIsNone(domain.waiver_on_file(self.state, self.job, "conditional_progress"))


class ClosingOut(unittest.TestCase):
    """The closeout's final waiver takes the same tap, and asking for retainage with an
    unconditional one in the closeout documents takes an owner and a reason."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000, retainage=10)
        domain.record_progress(self.state, self.job["id"], 1_000_000)
        domain.mark_done(self.state, self.job["id"])
        inv = send_open(self.state, self.job)
        domain.match_payment(self.state, self.job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
        for item in self.job["closeout_packet"]:
            if domain.is_waiver_item(item):
                self.item = item
            else:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="x")

    def test_an_unconditional_final_waiver_on_the_closeout_takes_the_owner(self):
        rec = signed_waiver(self.state, self.job["id"], None, "Final waiver")
        domain.attach_record_to_packet(self.state, self.job["id"], self.item["id"], rec["id"], "Sam")
        self.assertFalse(self.item["present"], "the closeout's waiver waits for the answer too")
        domain.confirm_packet_item(self.state, self.job["id"], self.item["id"], "Sam", answer="unconditional")
        self.assertEqual(rec["waiver_kind"], "unconditional_final")
        with self.assertRaises(domain.GateError) as refused:
            domain.chase_retainage(self.state, self.job["id"], "Sam")
        self.assertEqual(str(refused.exception), domain.UNCONDITIONAL_ATTACHED)
        view = domain.job_view(self.state, self.job)
        self.assertEqual([r["record_id"] for r in view["closeout_unconditional"]], [rec["id"]])
        member = server.Ctx(user={"role": "member"}, body={"unconditional_override": "Because"}, company_id="co", actor="Mo")
        with self.assertRaises(domain.GateError):
            server._retainage(None, self.state, self.job["id"], None, member)
        domain.chase_retainage(self.state, self.job["id"], "Owner", unconditional_override="Retainage paid by wire already")
        self.assertEqual(self.job["retainage_overrides"][0]["reason"], "Retainage paid by wire already")
        self.assertTrue(any(a["action"] == "unconditional_override" for a in self.state["audit"]))

    def test_a_conditional_one_goes_with_the_request(self):
        rec = signed_waiver(self.state, self.job["id"], None, "Final waiver")
        domain.attach_record_to_packet(self.state, self.job["id"], self.item["id"], rec["id"], "Sam", answer="conditional")
        self.assertTrue(self.item["present"])
        domain.chase_retainage(self.state, self.job["id"], "Sam")
        self.assertEqual(self.job["retainage_chases"], 1)


class HandSetDollarsSurviveTheForm(unittest.TestCase):
    """d1_giveway.py: the progress form sends every box, so only a line whose quantity changed gives
    way to it; a hand-set amount on another line stays."""

    def test_changing_one_line_keeps_the_others_dollars(self):
        st = new_state()
        job = new_job(st, contract=1, retainage=10)
        domain.save_sov(st, job["id"], [{"item": "1", "description": "Rough", "quantity": "100", "unit": "ea", "rate": "100"},
                                       {"item": "2", "description": "Panel", "quantity": "1", "unit": "LS", "rate": "10000"}], "0", update_contract=True)
        a, b = sov.lines(job)
        domain.record_sov_progress(st, job["id"], {a["id"]: "20", b["id"]: "0.400"}, "", "Sam")
        domain.set_payapp_amounts(st, job["id"], {b["id"]: "3500.00"}, "Sam")
        domain.record_sov_progress(st, job["id"], {a["id"]: "30", b["id"]: "0.400"}, "", "Sam")
        d = draft(job)
        self.assertEqual({e["line_id"]: e["amount_cents"] for e in d["sov_sheet"]}, {a["id"]: 300_000, b["id"]: 350_000})
        self.assertEqual(d["gross_cents"], 650_000)
        self.assertFalse([x for x in st["audit"] if "gives way" in x["detail"]])
        domain.record_sov_progress(st, job["id"], {a["id"]: "30", b["id"]: "0.500"}, "", "Sam")
        self.assertEqual(draft(job)["gross_cents"], 800_000, "a new % on that line replaces its dollars")
        self.assertTrue([x for x in st["audit"] if "gives way" in x["detail"]])


class RetainageReleasedEarly(unittest.TestCase):
    """Released mid-job on a paid pay app: recorded, and its own waiver can be sent."""

    def test_recorded_mid_job_and_its_waiver_sent(self):
        st = new_state()
        job = new_job(st, contract=1_000_000, retainage=10)
        domain.record_progress(st, job["id"], 400_000)
        inv = send_open(st, job)
        domain.match_payment(st, job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
        held = domain.job_money(job)["retainage_open_cents"]
        self.assertEqual(held, 40_000)
        domain.record_retainage_payment(st, job["id"], held, "CHK 12", "Sam")
        release = job["retainage_waivers"][-1]
        self.assertEqual((release["kind"], release["waiver_due"]), ("unconditional_progress", True))
        rec = signed_waiver(st, job["id"], None, "Waiver for the retainage")
        domain.set_waiver_type(st, job["id"], rec["id"], "unconditional_progress", "Sam", for_id=release["id"])   # the pay app's own is owed too
        domain.send_waiver(st, job["id"], "Sam", release_id=release["id"], kind="unconditional_progress")
        self.assertTrue(release["waiver_sent"])


class BillByDollarsKeepsTheDateAndNotes(unittest.TestCase):
    """d2_dollars.py: a new amount at the same tenth of a percent keeps the date and the notes sent
    with it, and a new date alone is a change."""

    def setUp(self):
        self.st = new_state()
        self.job = new_job(self.st, contract=1_000_000, retainage=10, auto_schedule=True)
        self.line = sov.lines(self.job)[0]

    def bill(self, cents, **kw):
        milli = cents * 1000 // self.line["scheduled_cents"]
        body = {"done": {self.line["id"]: f"{milli / 1000:.3f}"}, "amounts": {self.line["id"]: f"{cents / 100:.2f}"}, "notes": "", "period_to": "", **kw}
        return server._sov_progress(None, self.st, self.job["id"], None, server.Ctx(user={"role": "owner"}, body=body, company_id="co", actor="Sam"))

    def test_the_date_and_notes_go_with_a_new_amount_and_a_date_alone_is_a_change(self):
        last = domain.period_to(self.job)
        self.bill(333_333)
        self.bill(333_399, period_to=last, notes="Second floor")
        d = draft(self.job)
        self.assertEqual((d["gross_cents"], d["period_to"], d["progress_notes"]), (333_399, last, "Second floor"))
        earlier = "2026-10-31" if last > "2026-10-31" else last
        self.bill(333_399, period_to=earlier)
        d = draft(self.job)
        self.assertEqual((d["gross_cents"], d["period_to"], d["progress_notes"]), (333_399, earlier, "Second floor"), "the date alone changed it")
        with self.assertRaises(domain.GateError) as raised:
            self.bill(333_399, period_to=earlier)
        self.assertIn("Nothing changed", str(raised.exception))
        self.bill(333_399, period_to=earlier, notes="Corrected note")
        self.assertEqual(draft(self.job)["progress_notes"], "Corrected note")


class CreditLineProgress(unittest.TestCase):
    """The money fuzz billing by dollars (seed 873339740 of 77123): progress on a job with a signed
    credit change order's line raised KeyError where the line's credit grew. A credit goes further
    below nothing as its work is done; that is no refusal."""

    def test_the_seed_runs_and_the_job_finishes(self):
        import random
        import test_money_fuzz
        seq = test_money_fuzz.Sequence(random.Random(873339740))
        for _ in range(test_money_fuzz.STEPS):
            seq.step()
        seq.finish()
        self.assertTrue(seq.job["closed"])


if __name__ == "__main__":
    unittest.main()
