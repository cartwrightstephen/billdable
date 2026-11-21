"""Fixes from the review of Duracash Round 2 (Nov 17): the Georgia form's sum once a waiver is
settled and its curly quotes and dashes, and the demo's Kirkwood job paid before its final pay app."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from unittest import mock

import domain
import waiver_form
from test_money import new_job, new_state, send_open
from test_round2_waivers import pdf_fields
from waiver_help import signed_waiver


class GeorgiaForm(unittest.TestCase):
    """Items 8 and 9."""

    def setUp(self):
        self.state = new_state()
        self.state["company"].update(name="Volt Electric LLC")
        self.job = new_job(self.state, contract=1_000_000, customer="Jones GC", name="O’Brien – clinic", role="GC")
        self.job["address"] = "12 Peachtree St NE, Atlanta, GA 30303"
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = send_open(self.state, self.job)

    def test_a_settled_waiver_prints_the_sum_that_was_sent(self):
        domain.match_payment(self.state, self.job["id"], 400_000, "c1", invoice_id=self.inv["id"])
        signed_waiver(self.state, self.job["id"], "unconditional_progress")
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"])
        self.assertEqual(waiver_form.prefill(self.state, self.job, self.inv["id"], "interim")["amount"], "4,000.00")

    def test_after_a_part_waiver_and_a_short_pay_it_prints_the_part(self):
        domain.match_payment(self.state, self.job["id"], 150_000, "c1", invoice_id=self.inv["id"])
        signed_waiver(self.state, self.job["id"], "unconditional_progress")
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"])
        domain.settle_short_pay(self.state, self.job["id"], self.inv["id"], "Backcharge", "Sam")
        self.assertEqual(waiver_form.prefill(self.state, self.job, self.inv["id"], "interim")["amount"], "1,500.00")

    def test_curly_quotes_and_dashes_read_as_typed(self):
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": "GA"}):
            pdf, _, refused = waiver_form.for_waiver(self.state, self.job["id"], self.inv["id"], "interim")
        self.assertEqual(refused, "")
        self.assertEqual(pdf_fields(pdf)["project"], "O’Brien – clinic")
        self.assertIn(b"/TU (\xfe\xff", pdf)
        # What is drawn stays cp1252 for the WinAnsi font.
        self.assertIn("O’Brien – clinic".encode("cp1252"), pdf)

    def test_a_value_with_parentheses_and_backslashes(self):
        raw = waiver_form._pdf_text("a (b) \\ c")
        self.assertTrue(raw.startswith(b"(\xfe\xff") and raw.endswith(b")"))
        body = raw[1:-1]
        self.assertNotRegex(body, rb"(?<!\\)[()]", "every parenthesis inside is escaped")
        # A character whose UTF-16 bytes hold a CR or LF byte is escaped, not left as a line end.
        self.assertNotIn(b"\n", waiver_form._pdf_text("਍ഊ"))
        self.assertNotIn(b"\r", waiver_form._pdf_text("਍ഊ"))


class DemoKirkwood(unittest.TestCase):
    """Item 10: the first pay app is paid before the final one goes."""

    def test_paid_before_the_final_pay_app_was_sent(self):
        from test_demo_seed import built
        state, _ = built()
        job = next(j for j in state["jobs"] if j["name"] == "Kirkwood Library lighting retrofit")
        first, final = job["invoices"][0], next(i for i in job["invoices"] if i.get("final"))
        paid_at = min(p["matched_at"] for p in job["payments"] if p["invoice_id"] == first["id"])
        self.assertLess(paid_at, final["sent_at"])
        self.assertNotIn("final_override", final)


if __name__ == "__main__":
    unittest.main()
