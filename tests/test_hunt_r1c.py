"""The last gaps in the waiver redesign (Round 1 hunt, r1c): a lien waiver filed under another kind
whose title is not read as a heading is still asked about; a waiver answer is set by someone who can
send, and an unconditional one is not taken back by a bare confirm; and the smaller gaps around
them. The text only sets the default and a question; a person's answer carries the decision."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import doc_check
import domain
import unconditional_corpus as c
from test_blockers import GOOD, Case, pdf
from test_packet import draft, new_job, new_state


class WaiverAnywhereReadsAsAForm(unittest.TestCase):
    """A title is not the only place a lien waiver says what it is."""

    def test_every_real_unconditional_waiver_reads_as_a_form_wherever_its_title_is(self):
        missed = [name[:70] for name, text in c.TITLES_ANYWHERE.items() if not doc_check.reading(text)["form"]]
        self.assertEqual(missed, [])

    def test_the_guess_is_unconditional_when_the_text_says_so(self):
        for name, text in c.TITLES_ANYWHERE.items():
            if name in ("Partial Release of Lien", "line 12: Partial Release of Lien", "no title, only the words"):
                continue   # says neither word
            with self.subTest(name[:70]):
                self.assertEqual(doc_check.reading(text)["guess"], "unconditional")

    def test_insurance_waivers_are_no_lien_waiver(self):
        for name, text in c.INSURANCE.items():
            with self.subTest(name):
                self.assertFalse(doc_check.reading(text)["form"])

    def test_a_lien_waiver_that_also_talks_about_subrogation_is_still_one(self):
        text = "WAIVER OF LIEN\nElm addition\nThe undersigned waives lien rights and, by waiver of subrogation, any right to recover from the owner.\n"
        self.assertTrue(doc_check.reading(text)["form"])

    def test_a_job_with_tickets_invoices_a_coi_and_a_contract_asks_at_most_once(self):
        asked = [name for name, text in {**c.REALISTIC, "contract": c.CONTRACT}.items() if doc_check.reading(text)["form"]]
        self.assertLessEqual(len(asked), 1, asked)
        self.assertEqual([n for n in asked if n != "contract"], [])

    def test_a_long_file_is_read_in_a_blink(self):
        import time
        big = ("waives " + "x " * 30) * 6000 + ("not a b c d e f " * 5000)
        start = time.time()
        doc_check.reading(big[:contract_limit()])
        self.assertLess(time.time() - start, 2.0)


def contract_limit():
    import contract_import
    return contract_import.MAX_TEXT


class FiledUnderAnotherKind(Case):
    """The route that was open: add_record of a record filed as other, a note, insurance or a PDF
    named scan-0042.pdf, then send. Now it is asked about, then the owner."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Insurance certificate.")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = draft(self.job)
        self.waiver = next(p for p in self.inv["packet"] if p["kind"] == "waiver")
        self.cert = next(p for p in self.inv["packet"] if p["kind"].startswith("insurance"))
        self.data = {}
        self.attach(self.stored("Our waiver", pdf(GOOD)))
        self.assertTrue(self.waiver["present"])
        domain.resolve_packet_item(self.state, self.job["id"], self.cert["id"], "Sam", reason="In hand")

    def send(self, **kw):
        return domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read, **kw)

    def test_each_kind_and_a_scan_named_file_asks_then_takes_the_owner(self):
        title = c.LONG_TITLES[2]   # "Acme Electric - Unconditional Waiver and Release of Lien - Elm addition - Pay App 3": 14 words
        for kind, name in (("other", "w.txt"), ("note", "w.txt"), ("insurance", "w.txt"), ("other", "scan-0042.pdf")):
            with self.subTest(kind=kind, name=name):
                self.setUp()
                text = (title + c.LONG_BODY).replace("\n", " ") if name.endswith("pdf") else title + c.LONG_BODY
                data = pdf(text.replace("(", "").replace(")", "")) if name.endswith("pdf") else text.encode()
                rec = self.stored("Paperwork", data, name, kind=kind, typed="")
                domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", rec["id"], "Sam", read_file=self.read)
                with self.assertRaises(domain.GateError) as asked:
                    self.send()
                self.assertTrue(asked.exception.extra.get("waiver_question"), "no file leaves without a person's answer")
                self.assertEqual(domain.waiver_guess(rec), "unconditional")
                domain.answer_waiver_question(self.state, self.job["id"], rec["id"], "unconditional", "Sam", shown=rec["file_ids"])
                with self.assertRaises(domain.GateError) as refused:
                    self.send()
                self.assertEqual(str(refused.exception), domain.UNCONDITIONAL_ATTACHED)
                self.send(unconditional_override="Paid by wire, owner checked")
                self.assertEqual(self.inv["status"], "sent")

    def test_a_letterhead_then_the_title_is_asked_about(self):
        rec = self.stored("Paperwork", (c.LETTERHEAD + "UNCONDITIONAL LIEN RELEASE" + c.LONG_BODY).encode(), "w.txt", kind="other", typed="")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", rec["id"], "Sam", read_file=self.read)
        self.assertEqual(domain.waiver_questions(self.job, self.inv["packet"], self.inv["extra_record_ids"], (), self.state, self.read), [rec])

    def test_a_certificate_with_a_waiver_of_subrogation_goes_with_no_question(self):
        rec = self.stored("COI", c.INSURANCE["ACORD"].encode(), "coi.txt", kind="insurance", typed="")
        domain.detach_packet_item(self.state, self.job["id"], self.cert["id"], "Sam")
        self.attach(rec, item=self.cert)
        if not self.cert["present"]:
            domain.confirm_packet_item(self.state, self.job["id"], self.cert["id"], "Sam", looked=True, read_file=self.read, shown=[rec["id"]])
        self.send()
        self.assertEqual(self.inv["status"], "sent")


if __name__ == "__main__":
    unittest.main()
