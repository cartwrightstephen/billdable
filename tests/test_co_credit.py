"""A negative change order (Stephen's mother, Nov 21): the page sends a credit as a minus amount
(the form's Credit (deducts) choice), the server takes it with cost lines too, and the client's
signing page says "credit of" rather than showing a bare minus sign."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import payer_page
from test_money import new_state, new_job


class CreditChangeOrder(unittest.TestCase):
    def test_a_credit_with_cost_lines_is_saved_as_a_credit(self):
        state = new_state()
        job = new_job(state)
        domain.add_change_order(state, job["id"], "Owner buys fixtures", None, reason="owner_request",
                                items=[{"label": "Fixtures", "amount_cents": -80_000}, {"label": "Labor", "amount_cents": -20_000}])
        co = job["change_orders"][-1]
        self.assertEqual(co["amount_cents"], -100_000)
        self.assertFalse(co.get("unpriced"))

    def test_the_signing_page_calls_it_a_credit(self):
        state = new_state()
        job = new_job(state)
        domain.add_change_order(state, job["id"], "Drop the panel upgrade", -125_000, reason="owner_request")
        page = payer_page.render_co(state, job, job["change_orders"][-1], "/s/x.y")
        self.assertIn("Change order 1: credit of $1,250.00</h1>", page)
        domain.add_change_order(state, job["id"], "Two outlets", 50_000, reason="owner_request")
        page = payer_page.render_co(state, job, job["change_orders"][-1], "/s/x.y")
        self.assertIn("Change order 2: $500.00</h1>", page)

    def test_signed_it_lowers_the_contract(self):
        state = new_state()
        job = new_job(state)
        before = domain.job_money(job)["contract_total_cents"]
        domain.add_change_order(state, job["id"], "Drop the panel upgrade", -125_000, reason="owner_request")
        co = job["change_orders"][-1]
        domain.sign_change_order(state, job, co, "Kim Park", True, "203.0.113.5", "Safari")
        self.assertEqual(co["status"], "approved")
        self.assertEqual(domain.job_money(job)["contract_total_cents"], before - 125_000)


if __name__ == "__main__":
    unittest.main()
