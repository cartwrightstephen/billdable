"""Money hunt, Nov 27: what the pay-app core, Revise, the predicted pay date, the PDF and the
QuickBooks push got wrong when exercised together."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain  # noqa: E402
import metrics  # noqa: E402
from test_payapp_core import DAY, DAY2, schedule_job, send  # noqa: E402


class UndatedEarlierPaymentsTests(unittest.TestCase):
    def test_paid_with_no_date_isnt_paid_in_zero_days(self):
        """Earlier pay apps entered as paid with no date were paid "on" the day their period ended,
        so the client "usually pays in 0 days" and the next pay app was expected the day it went."""
        state, job, labor, _materials = schedule_job(retainage=10)
        domain.import_earlier_payapps(state, job["id"], {"payapps": [
            {"number": "1", "period_to": DAY, "lines": {labor: {"pct": "20"}}, "paid": "1800"},
            {"number": "2", "period_to": DAY2, "lines": {labor: {"pct": "40"}}, "paid": "1800"},
        ]})
        self.assertTrue(all(p.get("undated") for p in job["payments"]))
        self.assertEqual(domain.client_stats(state), {})
        self.assertEqual(domain.payer_timing(state), {})
        self.assertIsNone(metrics.compute(state)["sent_to_paid_median_days"])
        domain.record_sov_progress(state, job["id"], {labor: "0.5"})
        inv = send(state, job)
        guess = domain.predicted_pay(state, job, inv)
        self.assertNotEqual(guess["basis"], "history")
        self.assertGreater(guess["days"], 0)

    def test_paid_with_a_date_still_counts(self):
        state, job, labor, _materials = schedule_job(retainage=10)
        domain.import_earlier_payapps(state, job["id"], {"payapps": [
            {"number": "1", "period_to": DAY, "lines": {labor: {"pct": "20"}}, "paid": "1800", "paid_on": DAY2},
        ]})
        self.assertFalse(job["payments"][0].get("undated"))
        stats = domain.client_stats(state)[job["payer_id"]]
        self.assertEqual(stats["median_days"], (date.fromisoformat(DAY2) - date.fromisoformat(DAY)).days)

    def test_the_month_email_counts_only_what_went_from_here(self):
        state, job, labor, _materials = schedule_job(retainage=10)
        domain.import_earlier_payapps(state, job["id"], {"payapps": [
            {"number": "1", "period_to": DAY, "lines": {labor: {"pct": "20"}}, "paid": "1800", "paid_on": DAY},
        ]})
        d = date.fromisoformat(DAY)
        _subject, body = metrics.month_summary(state, d.year, d.month)
        self.assertIn("Pay apps sent: 0,", body)
        self.assertIn("Payments received: 0,", body)


if __name__ == "__main__":
    unittest.main()
