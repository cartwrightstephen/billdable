"""The waiver question against documents subs really attach (tests/unconditional_corpus.py, the Nov 5
and Nov 6 reviews and the Round 1 hunt), under the Nov 6 redesign: the person's answer carries the
decision, the text only picks the answer shown first. For each case: the guess, and that after the
person's answer it goes out, or takes the owner, as it should. No case leaves without the answer or
the owner: filed as a waiver every file is asked which waiver it is; filed as anything else, every
real waiver reads as one and is asked about."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import doc_check
import domain
import unconditional_corpus as c
from test_blockers import Case
from test_packet import draft, new_job, new_state

STATUTORY = {"CA 8132": (c.CA_8132, "conditional"), "CA 8136": (c.CA_8136, "conditional"), "CA 8134": (c.CA_8134, "unconditional"),
             "CA 8138": (c.CA_8138, "unconditional"), "GA interim": (c.GA_INTERIM, "conditional"), "GA final": (c.GA_FINAL, "conditional"),
             "TX conditional": (c.TX_COND, "conditional"), "TX unconditional": (c.TX_UNCOND, "unconditional")}
# Georgia's forms say neither word: the guess is blank and the item's type is picked first.
SAYS_NEITHER = {"GA interim", "GA final"}
REAL = {**c.REAL_ELSEWHERE, **c.NUMBERED, **{t: t + c.TITLE_BODY for t in c.TITLES}}
# Real unconditional waivers whose text puts a conditional title, or both words, first: the guess
# is wrong and the person's answer corrects it.
GUESSED_WRONG = {"packet, second form numbered", "Unconditional Waiver and Release Conditional Builders LLC", c.TITLES[46]}   # [46]: a conditional form, then the unconditional one 30 lines down
CONDITIONAL_FORMS = {**c.GC_FORMS, "type unticked": c.NOT_NUMBERED["type unticked"]}
NOT_WAIVERS = {**c.NOT_WAIVERS, **{k: v for k, v in c.NOT_NUMBERED.items() if k != "type unticked"}}
# Non-waivers with a short line naming a waiver near the top: asked once, and No clears them.
ASKED = {"contract-excerpt-heading", "daily-log-ends-waiver", "daily-log-short-line", "invoice-enclosed", "invoice-to-follow",
         "invoice-will-be-provided", "supplier-invoice", "lettered plural", "clause with prose"}


class Filed(Case):
    """One document through the app: filed on the pay app's conditional waiver item, or under the
    insurance certificate, then the person's answer, then Approve & send."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Insurance certificate.")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = draft(self.job)
        self.waiver = next(p for p in self.inv["packet"] if p["kind"] == "waiver")
        self.cert = next(p for p in self.inv["packet"] if p["kind"].startswith("insurance"))
        self.data = {}

    def send(self, **kw):
        return domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read, **kw)

    def a_good_waiver(self):
        self.attach(self.stored("Our waiver", b"CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\nPine kitchen $4,000.00\n", "w.txt"), answer="conditional")
        self.assertTrue(self.waiver["present"])

    def a_certificate(self):
        self.attach(self.stored("COI", b"CERTIFICATE OF LIABILITY INSURANCE\nPine kitchen\n", "coi.txt", kind="insurance", typed=""), item=self.cert)
        self.assertTrue(self.cert["present"])

    def on_the_waiver_item(self, text, guess, truth):
        """Guessed `guess`, answered `truth`: an unconditional answer takes the owner."""
        self.setUp()
        self.a_certificate()
        rec = self.stored("Signed waiver", text.encode(), "w.txt", typed="")
        self.attach(rec, answer="")
        self.assertFalse(self.waiver["present"], "nothing goes before the answer")
        self.assertEqual(domain.item_question(self.job, self.waiver)["guess"], guess)
        with self.assertRaises(domain.GateError):
            self.send()
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read,
                                   shown=[rec["id"]], answer=truth)
        self.assertTrue(self.waiver["present"])
        self.assertEqual(rec["waiver_kind"], f"{truth}_progress", "the answer types the record")
        self.goes(truth == "unconditional")

    def elsewhere(self, text, asked, truth):
        """Filed under the insurance certificate: asked "Is this a lien waiver?" when it reads as
        one, answered `truth` (no, conditional, unconditional)."""
        self.setUp()
        self.a_good_waiver()
        rec = self.stored("Paperwork", text.encode(), "p.txt", kind="insurance", typed="")
        self.attach(rec, item=self.cert)
        if not self.cert["present"]:
            domain.confirm_packet_item(self.state, self.job["id"], self.cert["id"], "Sam", looked=True, read_file=self.read, shown=[rec["id"]])
        if not asked:
            self.goes(False)
            return
        with self.assertRaises(domain.GateError) as refused:
            self.send()
        self.assertTrue(refused.exception.extra.get("waiver_question"))
        self.assertEqual(self.inv["status"] in domain.DRAFT_STATUSES, True)
        domain.answer_waiver_question(self.state, self.job["id"], rec["id"], truth, "Sam", shown=rec["file_ids"])
        self.goes(truth == "unconditional")

    def goes(self, owner):
        if owner:
            with self.assertRaises(domain.GateError) as refused:
                self.send()
            self.assertEqual(str(refused.exception), domain.UNCONDITIONAL_ATTACHED)
            self.send(unconditional_override="Paid by wire, owner checked")
        else:
            self.send()
        self.assertEqual(self.inv["status"], "sent")


class StatutoryForms(Filed):
    def test_the_guess_and_the_answer(self):
        for name, (text, truth) in STATUTORY.items():
            with self.subTest(name):
                self.assertEqual(doc_check.reading(text)["guess"], "" if name in SAYS_NEITHER else truth)
                self.assertTrue(doc_check.reading(text)["form"])
                self.on_the_waiver_item(text, truth, truth)
                self.elsewhere(text, True, truth)


class GcConditionalForms(Filed):
    def test_boilerplate_about_an_unconditional_waiver_reads_conditional_and_goes(self):
        for name, text in CONDITIONAL_FORMS.items():
            with self.subTest(name):
                self.assertEqual(doc_check.reading(text), {"guess": "conditional", "form": True})
                self.on_the_waiver_item(text, "conditional", "conditional")
                self.elsewhere(text, True, "conditional")


class RealUnconditionalWaivers(Filed):
    def test_wherever_it_is_filed_it_takes_the_answer_then_the_owner(self):
        for name, text in REAL.items():
            with self.subTest(name[:60]):
                read = doc_check.reading(text)
                self.assertTrue(read["form"], "filed as anything else it is asked about")
                guess = "conditional" if name in GUESSED_WRONG else "unconditional"
                self.assertEqual(read["guess"] or "conditional", guess)
                self.on_the_waiver_item(text, guess, "unconditional")
                self.elsewhere(text, True, "unconditional")

    def test_a_wrong_answer_is_the_persons_and_says_so(self):
        """The person's answer carries it: the guess and the warning beside the answer are all the text does."""
        self.setUp()
        rec = self.stored("Signed waiver", c.CA_8134.encode(), "w.txt", typed="")
        self.attach(rec, answer="")
        self.assertEqual(domain.item_question(self.job, self.waiver), {"guess": "unconditional", "reads_as": "unconditional"})
        self.assertIn("This reads as unconditional.", self.waiver["check"]["problems"])


class RecordsThatAreNotWaivers(Filed):
    def test_asked_only_when_a_line_near_the_top_names_one_and_no_clears_it(self):
        for name, text in NOT_WAIVERS.items():
            with self.subTest(name):
                self.assertEqual(doc_check.reading(text)["form"], name in ASKED)
                self.elsewhere(text, name in ASKED, "no")

    def test_a_contract_never_gates_by_itself(self):
        for name in ("contract-excerpt-heading", "contract-excerpt-shall-deliver", "plural heading then prose", "clause with prose"):
            with self.subTest(name):
                self.setUp()
                self.a_good_waiver()
                rec = self.stored("Subcontract", NOT_WAIVERS[name].encode(), "s.txt", kind="contract", typed="")
                domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", rec["id"], read_file=self.read)
                self.assertEqual(domain.unconditional_waivers_attached(self.job, self.inv), [])


if __name__ == "__main__":
    unittest.main()
