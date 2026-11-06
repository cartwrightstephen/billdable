"""The owner gate on an unconditional waiver (M14) against documents subs really attach (Nov 6
review, A). The gate is for a document that is one: typed unconditional, titled as one, or saying
it waives now without condition. A form or a record that only talks about one (it comes later, it
is deemed so on payment, one was sent or received) is a look that anyone who can send clears."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import doc_check
import unconditional_corpus as c


def read(text, near_only):
    return doc_check.unconditional_reading([("f.txt", ".txt", text.encode())], near_only)


class StatutoryForms(unittest.TestCase):
    def test_filed_as_a_waiver_only_the_unconditional_ones_gate(self):
        for name, text, gate in (("CA 8132", c.CA_8132, False), ("CA 8136", c.CA_8136, False), ("CA 8134", c.CA_8134, True),
                                 ("CA 8138", c.CA_8138, True), ("GA interim", c.GA_INTERIM, False), ("GA final", c.GA_FINAL, False),
                                 ("TX conditional", c.TX_COND, False), ("TX unconditional", c.TX_UNCOND, True)):
            with self.subTest(name):
                self.assertEqual(read(text, False)["waiver"], gate)


class GcConditionalForms(unittest.TestCase):
    def test_boilerplate_about_an_unconditional_waiver_is_a_look_not_the_gate(self):
        for name, text in c.GC_FORMS.items():
            for near_only in (False, True):
                with self.subTest(name, near_only=near_only):
                    self.assertFalse(read(text, near_only)["waiver"])

    def test_filed_as_a_waiver_the_mention_still_asks_for_a_look(self):
        for name, text in c.GC_FORMS.items():
            if "unconditional" not in doc_check._NEGATED_UNCONDITIONAL.sub(" ", text).lower():
                continue   # "not an unconditional waiver" is taken back at once
            with self.subTest(name):
                self.assertTrue(read(text, False)["mention"])

    def test_the_item_warning_says_it_mentions_the_word_rather_than_that_it_is_one(self):
        problems = doc_check._type_problems(c.GC_FORMS["GC-becomes"], "conditional_progress", "Elm addition")
        self.assertNotIn("It says unconditional, not conditional.", problems)
        self.assertTrue(any("unconditional" in p.lower() for p in problems), problems)
        self.assertEqual(doc_check._type_problems("Unconditional waiver Pine kitchen $4,000.00", "conditional_progress"),
                         ["It says unconditional, not conditional."])


class RecordsThatAreNotWaivers(unittest.TestCase):
    def test_logs_invoices_contracts_and_emails_filed_as_other_records_never_gate(self):
        for name, text in c.NOT_WAIVERS.items():
            with self.subTest(name):
                self.assertFalse(read(text, True)["waiver"])


class RealWaiversFiledElsewhere(unittest.TestCase):
    def test_a_real_unconditional_waiver_gates_wherever_it_is_filed(self):
        for name, text in c.REAL_ELSEWHERE.items():
            for near_only in (False, True):
                with self.subTest(name, near_only=near_only):
                    self.assertTrue(read(text, near_only)["waiver"])


class Titles(unittest.TestCase):
    def test_every_title_shape_stays_at_the_gate_filed_as_a_waiver_or_anything_else(self):
        for title in c.TITLES:
            # Filed as something else, the word alone names no waiver: a look at most.
            for near_only in (False, True) if title != "UNCONDITIONAL" else (False,):
                with self.subTest(title[:60], near_only=near_only):
                    self.assertTrue(read(title + c.TITLE_BODY, near_only)["waiver"])


if __name__ == "__main__":
    unittest.main()
