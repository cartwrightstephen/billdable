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



class ClientFormValuesTests(unittest.TestCase):
    def test_application_number_is_the_pay_apps_own(self):
        """After earlier pay apps entered as one summary through PA-04, the next pay app is PA-05;
        the client's form said Application No. 2."""
        import gcforms
        state, job, labor, _materials = schedule_job(retainage=10)
        domain.import_earlier_payapps(state, job["id"], {"summary": {
            "number": "4", "period_to": DAY, "lines": {labor: {"to_date": "6000"}}, "retainage": "600", "paid": "5400", "paid_on": DAY}})
        domain.record_sov_progress(state, job["id"], {labor: "0.7"})
        inv = send(state, job)
        values, _rows = gcforms.payapp_values(state, job, inv)
        self.assertEqual((inv["number"], values["application_count"]), ("PA-PINE-05", 5))

    def test_change_orders_as_of_the_pay_app(self):
        """Additions and deductions counted every change order signed now, so a pay app's form
        filled after a later change order showed it, against a net change of $0."""
        import gcforms
        state, job, labor, _materials = schedule_job(retainage=10)
        domain.add_change_order(state, job["id"], "Credit", -20_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        domain.record_sov_progress(state, job["id"], {labor: "0.5"})
        domain.add_change_order(state, job["id"], "Unsigned extra", 30_000)
        domain.include_change_order(state, job["id"], job["change_orders"][-1]["id"])
        approve_unsigned = job["change_orders"][-1]
        inv = next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        values, _rows = gcforms.payapp_values(state, job, inv)
        self.assertEqual((values["co_additions"], values["co_deductions"], values["net_change_orders"]), (30_000, 20_000, 10_000))
        inv["status"], inv["sent_at"] = "sent", domain.utcnow()
        inv["contract_at_send"] = __import__("payapp_pdf").contract_terms(job)
        domain.add_change_order(state, job["id"], "Later", 100_000)
        later = job["change_orders"][-1]
        later["status"], later["approved_at"] = "approved", "2999-01-01T10:00:00+00:00"
        values, _rows = gcforms.payapp_values(state, job, inv)
        self.assertEqual((values["co_additions"], values["co_deductions"], values["net_change_orders"]), (30_000, 20_000, 10_000))
        self.assertEqual(values["co_additions"] - values["co_deductions"], values["net_change_orders"])
        self.assertEqual(approve_unsigned["status"], "pending")



class ReleasePreviewTests(unittest.TestCase):
    def test_a_part_release_on_a_finished_job_previews_as_it_goes(self):
        """Review & send's email for a retainage release asked is_final(job), not the release's own
        rule: a part release on a finished job previewed as "the final pay application", and went
        as a plain one."""
        import approve_help
        state, job, labor, materials = schedule_job(retainage=10)
        domain.record_sov_progress(state, job["id"], {labor: "1", materials: "100"})
        inv = send(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1", invoice_id=inv["id"])
        domain.mark_done(state, job["id"])
        self.assertTrue(domain.is_final(job))
        domain.draft_retainage_release(state, job["id"], 50_000, "substantial")
        rel = next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        self.assertFalse(domain.final_for(job, rel))
        preview = domain.email_preview(state, job, rel)
        self.assertNotIn("final pay application", preview["body"])
        self.assertTrue(preview["subject"].startswith("Pay application"))



class DeleteRevisedDraftTests(unittest.TestCase):
    def test_a_revised_pay_app_deleted_is_withdrawn_and_its_number_kept(self):
        """Sent, put back to a draft to revise, then deleted: the client still held it with no word
        that it was withdrawn, and the next pay app took its number for another amount."""
        state, job, labor, _materials = schedule_job(retainage=10)
        domain.record_sov_progress(state, job["id"], {labor: "0.5"})
        inv = send(state, job)
        domain.revise_sent_pay_app(state, job["id"], inv["id"], "Wrong quantity")
        domain.record_sov_progress(state, job["id"], {labor: "0.4"})
        again = next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        self.assertEqual(again["number"], "PA-PINE-01")   # corrected, it keeps its number
        domain.void_draft(state, job["id"])
        last = state["email_log"][-1]
        self.assertEqual(last["kind"], "pay_app_withdrawn")
        self.assertIn("PA-PINE-01", last["subject"])
        self.assertIn("$4,500.00", last["body"])
        domain.record_sov_progress(state, job["id"], {labor: "0.3"})
        nxt = next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        self.assertEqual(nxt["number"], "PA-PINE-02")

    def test_a_plain_draft_deleted_sends_nothing_and_its_number_comes_back(self):
        state, job, labor, _materials = schedule_job(retainage=10)
        domain.record_sov_progress(state, job["id"], {labor: "0.5"})
        domain.void_draft(state, job["id"])
        self.assertEqual(state["email_log"], [])
        domain.record_sov_progress(state, job["id"], {labor: "0.3"})
        self.assertEqual(next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)["number"], "PA-PINE-01")



class WithdrawnQuickBooksInvoiceTests(unittest.TestCase):
    def test_pull_back_says_the_quickbooks_invoice_is_still_open(self):
        """A pay app sent to QuickBooks and then pulled back left its invoice open there, with nothing
        saying so: the books showed it owed and the corrected pay app went in beside it."""
        state, job, labor, _materials = schedule_job(retainage=10)
        domain.record_sov_progress(state, job["id"], {labor: "0.5"})
        inv = send(state, job)
        inv["qbo"] = {"invoice_id": "130", "doc_number": "PA-PINE-01", "total_cents": inv["amount_cents"]}
        domain.void_sent_pay_app(state, job["id"], inv["id"], "Wrong quantity")
        self.assertIn("QuickBooks invoice PA-PINE-01 still shows $4,500.00", " ".join(str(a) for a in state["audit"][-3:]))



class LargeFiguresOnTheSheetTests(unittest.TestCase):
    def test_ten_million_dollars_stays_in_its_column_and_inside_the_margin(self):
        """Figures of $10 million and more ran one character past the 12 the money columns held:
        each such row shifted right of its headings and past the right margin."""
        import re
        import payapp_pdf
        import sov
        import test_packet
        state = test_packet.new_state()
        job = test_packet.new_job(state, contract=2_500_000_000, retainage=10, packet="Invoice.")
        domain.save_sov(state, job["id"], [{"item": "1", "description": "Structure", "quantity": "1", "unit": "LS", "rate": "15000000"},
                                           {"item": "2", "description": "Finishes", "quantity": "1", "unit": "LS", "rate": "10000000"}], "")
        a, b = (line["id"] for line in sov.lines(job))
        domain.record_sov_progress(state, job["id"], {a: "0.8", b: "0.5"})
        inv = send(state, job)
        pdf = payapp_pdf.render({"name": "Co"}, job, {}, inv)
        rows = [(float(size), float(x), text.decode()) for size, x, text in re.findall(rb"BT /F3 ([\d.]+) Tf ([\d.]+) [\d.]+ Td \((.*?)\) Tj", pdf)
                if float(size) < 7.5]
        self.assertTrue(any("Totals" in t and "25,000,000.00" in t for _s, _x, t in rows))
        lengths = {len(t) for _s, _x, t in rows if t.startswith(("Item", "1 ", "2 ", "  "))}
        self.assertEqual(len(lengths), 1, "every row as long as the headings")
        for size, x, text in rows:
            self.assertLessEqual(x + len(text) * 0.6 * size, payapp_pdf.PAGE_W - payapp_pdf.MARGIN + 0.01, text)
        # A small job keeps its 7 pt type.
        state, job, labor, _materials = schedule_job(retainage=10)
        domain.record_sov_progress(state, job["id"], {labor: "0.5"})
        pdf = payapp_pdf.render({"name": "Co"}, job, {}, send(state, job))
        self.assertIn(b"/F3 7 Tf", pdf)



class PayerPageRetainageColumnTests(unittest.TestCase):
    def test_a_line_held_at_its_own_percent_shows_on_a_job_at_0(self):
        """The client's page showed the retainage column only when the pay app's own percent was
        more than 0, so a job at 0% with a line held at 5% hid what that line held."""
        import payer_page
        state, job, labor, materials = schedule_job(retainage=0)
        domain.set_line_retainage(state, job["id"], {materials: "5"})
        domain.record_sov_progress(state, job["id"], {labor: "0.5", materials: "40"})
        inv = send(state, job)
        self.assertEqual(inv["retainage_cents"], 10_000)
        html = payer_page._sheet(job, inv)
        self.assertIn("<th>Retainage</th>", html)
        self.assertIn("$100.00", html)


if __name__ == "__main__":
    unittest.main()
