"""Round 2 review fixes, Money > Financials: backlog counts signed work with a draft's retainage
in and unsigned change orders out, Collected is money that stayed, and the CSV's names can't run
as spreadsheet formulas."""

import csv
import io
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import financials
from seed import seed_state
from test_money import new_job, new_state, send_open

TODAY = date(2026, 11, 17)


class Backlog(unittest.TestCase):
    def test_a_drafts_retainage_is_not_backlog(self):  # B0
        state = new_state()
        job = new_job(state, contract=10_000_000, retainage=10)
        self.assertEqual(domain.backlog_cents(domain.job_money(job)), 10_000_000)
        domain.record_progress(state, job["id"], 5_000_000)
        draft = job["invoices"][-1]
        self.assertEqual((draft["amount_cents"], draft["retainage_cents"]), (4_500_000, 500_000))
        self.assertEqual(domain.backlog_cents(domain.job_money(job)), 5_000_000)
        send_open(state, job)
        self.assertEqual(domain.backlog_cents(domain.job_money(job)), 5_000_000)

    def test_an_unsigned_change_order_on_the_draft_leaves_backlog_alone(self):  # B12
        state = seed_state()
        job = next(j for j in state["jobs"] if j["id"] == "job-grant")
        co = next(c for c in job["change_orders"] if c["status"] == "pending")
        before = domain.backlog_cents(domain.job_money(job))
        domain.include_change_order(state, job["id"], co["id"])
        domain.draft_invoice(state, job["id"])
        self.assertTrue(any(line.get("co_id") == co["id"] for line in job["invoices"][-1]["lines"]))
        self.assertEqual(domain.backlog_cents(domain.job_money(job)), before)


def money_job(**kw):
    base = {"id": "j1", "name": "Elm", "payer_id": "p1", "contract_cents": 10_000_000, "change_orders": [], "payments": [],
            "invoices": [{"id": "a", "number": "A", "amount_cents": 9_000_000, "retainage_cents": 1_000_000, "gross_cents": 10_000_000,
                          "status": "paid", "sent_at": "2026-09-01T12:00:00+00:00"}]}
    base.update(kw)
    return base


class Collected(unittest.TestCase):
    def test_retainage_released_is_collected_in_its_month(self):  # B1
        j = money_job(payments=[{"amount_cents": 9_000_000, "invoice_id": "a", "matched_at": "2026-09-20T12:00:00+00:00"}],
                      retainage_released_cents=1_000_000,
                      retainage_payments=[{"amount_cents": 1_000_000, "reference": "r", "at": "2026-10-05T12:00:00+00:00"}])
        f = financials.compute({"payers": [{"id": "p1", "name": "Park GC"}], "jobs": [j]}, today=TODAY)
        self.assertEqual((f["billed_12m_cents"], f["collected_12m_cents"]), (10_000_000, 10_000_000))
        months = {r["month"]: r["collected_cents"] for r in f["monthly"]}
        self.assertEqual((months["2026-09"], months["2026-10"]), (9_000_000, 1_000_000))

    def test_a_refund_comes_off_collected_in_its_month(self):  # B11
        j = money_job(payments=[{"amount_cents": 9_000_000, "invoice_id": "a", "matched_at": "2026-09-20T12:00:00+00:00"}],
                      overbilling_settled=[{"cents": 200_000, "returned_cents": 200_000, "at": "2026-11-02T12:00:00+00:00", "reason": "check 1044"}])
        f = financials.compute({"payers": [{"id": "p1", "name": "Park GC"}], "jobs": [j]}, today=TODAY)
        self.assertEqual(f["collected_12m_cents"], 8_800_000)
        months = {r["month"]: r["collected_cents"] for r in f["monthly"]}
        self.assertEqual((months["2026-09"], months["2026-11"]), (9_000_000, -200_000))

    def test_no_more_goes_back_than_came_in(self):  # B11, as job_money caps it
        j = money_job(payments=[{"amount_cents": 100_000, "invoice_id": "a", "matched_at": "2026-09-20T12:00:00+00:00"}],
                      overbilling_settled=[{"cents": 300_000, "returned_cents": 300_000, "at": "2026-11-02T12:00:00+00:00", "reason": "bad"}])
        f = financials.compute({"payers": [{"id": "p1", "name": "Park GC"}], "jobs": [j]}, today=TODAY)
        self.assertEqual(f["collected_12m_cents"], 0)
        self.assertEqual(f["collected_12m_cents"], domain.job_money(j)["collected_cents"])


class CsvNames(unittest.TestCase):  # B3
    def test_names_go_as_text(self):
        state = seed_state()
        formula = '=HYPERLINK("http://x","click")'
        state["company"]["name"] = "@Evil"
        p = state["payers"][0]
        domain.update_payer(state, p["id"], {"name": formula})
        for j in state["jobs"]:
            j["name"] = "+Job"
        rows = list(csv.reader(io.StringIO(financials.to_csv(financials.compute(state, today=TODAY)))))
        cells = [c for r in rows for c in r]
        self.assertFalse([c for c in cells if c[:1] in ("=", "+", "@")], "no cell starts a formula")
        self.assertIn("'" + formula, cells)
        self.assertIn("'+Job", cells)


if __name__ == "__main__":
    unittest.main()
