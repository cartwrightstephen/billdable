"""The pay app PDF (R4-04): the period it covers, when payment is due, and the final mark.
R4-13 adds the line arithmetic across several pay apps here."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import payapp_pdf
from test_money import new_state, new_job, send_open


def pdf_text(state, job, invoice) -> bytes:
    payer = domain._payer(state, job["payer_id"])
    return payapp_pdf.render(state["company"], job, payer, invoice)


class PeriodAndDueTests(unittest.TestCase):
    def test_period_and_due_date_on_the_pdf(self):
        state = new_state()
        job = new_job(state)
        domain.update_payer(state, job["payer_id"], {"payment_terms_days": 30})
        today = datetime.now(timezone.utc).date()
        domain.record_progress(state, job["id"], 4_000_000, period_end=today.isoformat())
        inv = send_open(state, job)
        self.assertEqual(inv["period_to"], today.isoformat())
        self.assertEqual(inv["due_on"], (today + timedelta(days=30)).isoformat())
        pdf = pdf_text(state, job, inv)
        self.assertIn(f"Period to {today.isoformat()}".encode(), pdf)
        self.assertIn(f"Payment due {inv['due_on']}".encode(), pdf)
        self.assertNotIn(b"FINAL APPLICATION", pdf)
        mail = state["email_log"][-1]
        self.assertIn(f"Payment due {inv['due_on']}.", mail["body"])
        self.assertTrue(mail["subject"].endswith(f"period to {today.isoformat()}"))

    def test_period_defaults_to_today_and_cannot_go_back(self):
        state = new_state()
        job = new_job(state)
        today = datetime.now(timezone.utc).date()
        domain.record_progress(state, job["id"], 4_000_000, period_end=(today + timedelta(days=3)).isoformat())
        send_open(state, job)
        with self.assertRaises(domain.GateError) as raised:
            domain.record_progress(state, job["id"], 5_000_000, period_end=today.isoformat())
        self.assertIn("period_to", raised.exception.fields)
        with self.assertRaises(domain.GateError):
            domain.record_progress(state, job["id"], 5_000_000, period_end="10/31/2026")
        domain.record_progress(state, job["id"], 5_000_000)
        self.assertEqual(job["invoices"][-1]["period_to"], (today + timedelta(days=3)).isoformat(), "blank is today, or the last one's if later")

    def test_final_mark(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        first = send_open(state, job)
        self.assertFalse(first["final"])
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        self.assertTrue(inv["final"])
        self.assertIn(b"FINAL APPLICATION", pdf_text(state, job, inv))
        self.assertTrue(state["email_log"][-1]["subject"].startswith(f"Final pay application {inv['number']}"))
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-9", invoice_id=inv["id"])
        self.assertEqual(domain.suggested_waiver_kind(job, inv), "unconditional_final")
        self.assertEqual(domain.suggested_waiver_kind(job, first), "unconditional_progress", "the first pay app is still owed")


if __name__ == "__main__":
    unittest.main()
