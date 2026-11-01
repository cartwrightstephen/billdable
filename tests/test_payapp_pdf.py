"""The pay app PDF (R4-04): the period it covers, when payment is due, and the final mark;
the notes typed with the progress (R4-12); and the line arithmetic across several pay apps (R4-13)."""

import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import payapp_pdf
import sov
from test_money import new_state, new_job, send_open
from test_sov import line, sov_job


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
        self.assertEqual(domain.suggested_waiver_kind(job, first), "conditional_progress", "the first pay app is still owed (M17)")


class NotesTests(unittest.TestCase):  # R4-12
    def test_notes_in_the_pdf_text(self):
        state = new_state()
        job = new_job(state)
        domain.record_progress(state, job["id"], 4_000_000, "Framing done on level 2. Inspection passed (10/20).")
        inv = send_open(state, job)
        pdf = pdf_text(state, job, inv)
        self.assertIn(b"Notes", pdf)
        self.assertIn(b"Framing done on level 2. Inspection passed \\(10/20\\).", pdf)

    def test_long_notes_wrap_and_a_schedule_job_prints_them_too(self):
        state = new_state()
        job = sov_job(state)
        notes = "Rough-in on the first floor. " * 30
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "100"}, notes)
        inv = job["invoices"][-1]
        pdf = pdf_text(state, job, inv)
        self.assertIn(b"Rough-in on the first floor.", pdf)
        self.assertGreater(pdf.count(b"Rough-in on the first floor."), 3, "wrapped over several lines")

    def test_no_notes_no_block(self):
        state = new_state()
        job = new_job(state)
        domain.record_progress(state, job["id"], 4_000_000)
        self.assertNotIn(b"(Notes)", pdf_text(state, job, job["invoices"][-1]))


def money_text(cents):
    return f"{cents / 100:,.2f}".encode()


class MonthTwoTests(unittest.TestCase):  # R4-13
    """A schedule-of-values job at 10% retainage across three pay apps: PA-01 sent with stored
    materials, PA-02 sent and pulled back, PA-03 drafted with an unsigned change order."""

    def setUp(self):
        self.state = state = new_state()
        self.job = job = sov_job(state, retainage=10)   # $34,500 with 15% overhead and profit
        self.rough, self.panel = line(job, "1")["id"], line(job, "2")["id"]
        # PA-01: 100 ea installed ($4,500) and $3,000 of wire stored on site.
        domain.record_sov_progress(state, job["id"], {self.rough: "100"}, stored={self.rough: "3000"})
        self.first = send_open(state, job)
        # PA-02: sent, then pulled back with the wrong quantity.
        domain.record_sov_progress(state, job["id"], {self.rough: "200"})
        self.second = send_open(state, job)
        domain.void_sent_pay_app(state, job["id"], self.second["id"], "Wrong quantity")
        # PA-03: 180 ea and half the panel, plus a change order billed before it is signed.
        domain.add_change_order(state, job["id"], "Extra circuit", 100_000)
        self.co = job["change_orders"][-1]
        domain.include_change_order(state, job["id"], self.co["id"])
        domain.record_sov_progress(state, job["id"], {self.rough: "180", self.panel: "0.5"})
        self.third = job["invoices"][-1]

    def test_numbers_and_statuses(self):
        self.assertEqual([i["number"] for i in self.job["invoices"]], ["PA-PINE-01", "PA-PINE-02", "PA-PINE-03"])
        self.assertEqual([i["status"] for i in self.job["invoices"]], ["sent", "void", "draft"])

    def test_summary_lines_add_up(self):
        for inv in (self.first, self.third):
            f = payapp_pdf.summary(self.job, inv)
            self.assertEqual(f["contract_to_date"], f["original"] + f["net_co"] + f["unsigned_co"], "3 = 1 + 2 + 2a")
            self.assertEqual(f["earned"], f["completed"] - f["retainage"], "6 = 4 - 5")
            self.assertEqual(f["due"], f["earned"] - f["previous"], "8 = 6 - 7")
            self.assertEqual(f["balance"], f["contract_to_date"] - f["earned"], "9 = 3 - 6")
        f = payapp_pdf.summary(self.job, self.third)
        self.assertEqual(f["unsigned_co"], 100_000)
        self.assertEqual(f["previous"], self.first["amount_cents"], "the pulled-back pay app is not a previous certificate")
        self.assertEqual(f["completed"], self.first["gross_cents"] + self.third["gross_cents"])
        self.assertEqual(f["retainage"], self.first["retainage_cents"] + self.third["retainage_cents"])
        self.assertEqual(self.third["retainage_cents"], (self.third["gross_cents"] * 10 + 50) // 100)
        pdf = pdf_text(self.state, self.job, self.third)
        for key in ("contract_to_date", "completed", "retainage", "earned", "previous", "due", "balance"):
            self.assertIn(("$" + money_text(f[key]).decode()).encode(), pdf, key)
        self.assertIn(b"2a. Change orders billed here, not yet signed", pdf)

    def totals_row(self, inv):
        pdf = pdf_text(self.state, self.job, inv)
        found = re.search(rb"\(\s+Totals\s+([^)]*)\) Tj", pdf)
        self.assertTrue(found, "the continuation sheet has a Totals row")
        return found.group(1).split()

    def test_sheet_totals_equal_line_4(self):
        for inv in (self.first, self.third):
            scheduled, previous, this_period, stored, to_date = self.totals_row(inv)[:5]
            self.assertEqual(to_date, money_text(payapp_pdf.summary(self.job, inv)["completed"]), inv["number"])
        # The unsigned change order has its own row on the sheet, and is in the scheduled total.
        self.assertIn(b"CO 1 \\(unsigned\\)", pdf_text(self.state, self.job, self.third))
        self.assertEqual(self.totals_row(self.third)[0], money_text(3_450_000 + 100_000))

    # Read as it was when deleted: a change order signed or withdrawn after that does not change it.
    def test_unsigned_change_order_signed_after_the_draft_was_deleted_stays_unsigned_there(self):
        before = payapp_pdf.summary(self.job, self.third)
        totals = self.totals_row(self.third)
        domain.void_draft(self.state, self.job["id"])
        self.third["voided_at"] = _earlier(self.third["voided_at"])
        domain.approve_change_order(self.state, self.job["id"], self.co["id"])
        self.assertEqual(payapp_pdf.summary(self.job, self.third), before)
        self.assertEqual(before["unsigned_co"], 100_000)
        self.assertIn(b"CO 1 \\(unsigned\\)", pdf_text(self.state, self.job, self.third))
        self.assertEqual(self.totals_row(self.third), totals)

    def test_withdrawn_after_the_draft_was_deleted_still_reads_as_it_was(self):
        before = payapp_pdf.summary(self.job, self.third)
        domain.void_draft(self.state, self.job["id"])
        self.third["voided_at"] = _earlier(self.third["voided_at"])
        domain.withdraw_change_order(self.state, self.job["id"], self.co["id"])
        self.assertEqual(payapp_pdf.summary(self.job, self.third)["unsigned_co"], before["unsigned_co"])
        self.assertEqual(payapp_pdf.summary(self.job, self.third)["contract_to_date"], before["contract_to_date"])

    def test_stored_materials_feed_to_date_not_this_period(self):
        entry = next(e for e in self.first["sov_sheet"] if e["line_id"] == self.rough)
        cols = sov.sheet_columns(entry, 10)
        self.assertEqual((cols["this_period"], cols["stored"], cols["to_date"]), (450_000, 300_000, 750_000))
        scheduled, previous, this_period, stored, to_date = self.totals_row(self.first)[:5]
        self.assertEqual(stored, money_text(300_000))
        # The overhead and profit row has no stored column: its 15% on the wire ($450) is in its This period.
        self.assertEqual(this_period, money_text(450_000 + 112_500), "this period: the work and the overhead row, no stored wire")
        self.assertEqual(to_date, money_text(450_000 + 300_000 + 112_500))
        # PA-03: the wire is still stored, so it stays in To date and out of This period again.
        entry = next(e for e in self.third["sov_sheet"] if e["line_id"] == self.rough)
        cols = sov.sheet_columns(entry, 10)
        self.assertEqual((cols["previous"], cols["this_period"], cols["stored"]), (450_000, 360_000, 300_000))
        self.assertEqual(cols["to_date"], cols["previous"] + cols["this_period"] + cols["stored"])


def _earlier(iso, days=1):
    return (datetime.fromisoformat(iso) - timedelta(days=days)).isoformat()


class FiguresAsSentTests(unittest.TestCase):
    """An old pay app's lines 1-9 read as they were when it went out: change orders signed, and
    retainage released, after that do not change it (its PDF, its customer page, a resend)."""

    def test_change_order_signed_after_sending_is_not_on_the_old_pay_app(self):
        import payer_page
        state = new_state()
        job = new_job(state, contract=10_000_000)
        domain.record_progress(state, job["id"], 4_500_000)
        first = send_open(state, job)
        before = payapp_pdf.summary(job, first)
        first["sent_at"] = _earlier(first["sent_at"])
        domain.add_change_order(state, job["id"], "Extra outlets", 1_000_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        after = payapp_pdf.summary(job, first)
        self.assertEqual(after, before)
        self.assertEqual(after["net_co"], 0)
        self.assertEqual(after["contract_to_date"], 10_000_000)
        self.assertIn("$100,000.00", payer_page._job_card(job, first))
        self.assertNotIn("$110,000.00", payer_page._job_card(job, first))
        # The next pay app is drafted now, so it has the change order.
        domain.record_progress(state, job["id"], 6_000_000)
        self.assertEqual(payapp_pdf.summary(job, job["invoices"][-1])["contract_to_date"], 11_000_000)

    def test_retainage_released_after_sending_is_not_on_the_old_pay_app(self):
        state = new_state()
        job = new_job(state, contract=10_000_000, retainage=10)
        domain.record_progress(state, job["id"], 10_000_000)
        first = send_open(state, job)
        first["sent_at"] = _earlier(first["sent_at"])
        domain.record_retainage_payment(state, job["id"], 500_000)
        self.assertEqual(payapp_pdf.summary(job, first)["released"], 0)

if __name__ == "__main__":
    unittest.main()


class LaterChangesTests(unittest.TestCase):
    """A pay app already sent reads as it went out: a change order approved or retainage released
    afterwards is on the next pay app, not this one's PDF or customer page."""

    def setUp(self):
        self.state = state = new_state()
        self.job = job = new_job(state, contract=10_000_000, retainage=10)
        domain.record_progress(state, job["id"], 4_000_000)
        self.first = send_open(state, job)
        self.before = payapp_pdf.summary(job, self.first)

    def change_things(self):
        domain.add_change_order(self.state, self.job["id"], "Extra outlets", 1_000_000)
        domain.approve_change_order(self.state, self.job["id"], self.job["change_orders"][-1]["id"])
        domain.record_retainage_payment(self.state, self.job["id"], 100_000)

    def test_lines_2_3_and_9_stay_as_sent(self):
        self.assertEqual((self.before["net_co"], self.before["contract_to_date"], self.before["released"]), (0, 10_000_000, 0))
        pdf = pdf_text(self.state, self.job, self.first)
        self.change_things()
        self.assertEqual(payapp_pdf.summary(self.job, self.first), self.before)
        self.assertEqual(pdf_text(self.state, self.job, self.first), pdf, "the PDF is the one they received")
        import payer_page
        self.assertIn("$100,000.00", payer_page._job_card(self.job, self.first))
        self.assertNotIn("$110,000.00", payer_page._job_card(self.job, self.first))
        # The next pay app has them.
        domain.record_progress(self.state, self.job["id"], 6_000_000)
        second = send_open(self.state, self.job)
        after = payapp_pdf.summary(self.job, second)
        self.assertEqual((after["net_co"], after["contract_to_date"], after["released"]), (1_000_000, 11_000_000, 100_000))

    def test_an_older_file_is_given_the_contract_as_it_was_sent(self):
        self.first.pop("contract_at_send")
        self.first["sent_at"] = "2026-09-01T15:00:00+00:00"
        self.change_things()
        self.job["change_orders"][-1]["approved_at"] = "2026-10-01T15:00:00+00:00"
        self.job["retainage_payments"][-1]["at"] = "2026-10-02T15:00:00+00:00"
        self.assertEqual(payapp_pdf.summary(self.job, self.first), self.before, "figured from the dates")
        import migrations
        self.state["schema_version"] = 3
        migrations.migrate(self.state)
        self.assertEqual(self.first["contract_at_send"]["approved_co_cents"], 0)
        self.assertEqual(self.first["contract_at_send"]["released_cents"], 0)
        self.assertEqual(payapp_pdf.summary(self.job, self.first), self.before)


class SheetQuantityTests(unittest.TestCase):  # Stephen's Oct 29 review, M2
    """The quantity completed to date on every sheet, and the app's pay app summary equal to the PDF's."""

    setUp = MonthTwoTests.setUp

    def test_job_view_figures_equal_the_pdf_summary(self):
        view = domain.job_view(self.state, self.job)
        for stored, inv in zip(self.job["invoices"], view["invoices"]):
            if stored["status"] == "void":
                self.assertNotIn("figures", inv, "a pulled-back pay app carries no figures")
                continue
            want = payapp_pdf.summary(self.job, stored)
            self.assertEqual({k: inv["figures"][k] for k in want}, want, stored["number"])
        domain.match_payment(self.state, self.job["id"], 50_000, "CHK-1", invoice_id=self.first["id"])
        view = domain.job_view(self.state, self.job)
        self.assertEqual(view["invoices"][0]["figures"]["received_before"], 0)
        self.assertEqual(view["invoices"][2]["figures"]["received_before"], 50_000, "payments on earlier pay apps, for information")

    def sheet_rows(self, inv):
        pdf = pdf_text(self.state, self.job, inv)
        return [r.decode("latin-1").replace("\\(", "(").replace("\\)", ")") for r in re.findall(rb"/F3 8 Tf [\d.]+ [\d.]+ Td \((.*?)\) Tj", pdf)]

    def test_the_pdf_sheet_has_qty_to_date_and_nothing_runs_off_the_page(self):
        rows = self.sheet_rows(self.third)
        self.assertIn("Qty to date", rows[0])
        self.assertLess(rows[0].index("Stored"), rows[0].index("Qty to date"), "in the order of sov.sheet_columns")
        self.assertLess(rows[0].index("Qty to date"), rows[0].index("To date", rows[0].index("Qty to date") + 3))
        rough = next(r for r in rows if r.startswith("1 "))
        self.assertIn("180 ea", rough)
        panel = next(r for r in rows if r.startswith("2 "))
        self.assertNotIn(" LS", panel, "a lump sum has no quantity")
        usable = (payapp_pdf.PAGE_W - 2 * payapp_pdf.MARGIN) / (0.6 * 8)
        self.assertTrue(all(len(r) <= usable for r in rows), max(len(r) for r in rows))

    def test_the_client_page_and_the_email_text_have_it_too(self):
        import payer_page
        page = payer_page.render(self.state, self.job, self.third, "/p/x.y")
        self.assertIn("<h2>Continuation sheet</h2>", page)
        self.assertIn("<th>Qty to date</th>", page)
        self.assertIn("<td>180 ea</td>", page)
        for words in ("Contract sum", "Approved change orders", "Completed and stored to date", "Prior payments received", "Current amount due"):
            self.assertIn(words, page)
        self.assertIn("Qty to date", sov.continuation_sheet(self.job, self.third))
