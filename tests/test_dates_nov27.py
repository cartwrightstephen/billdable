"""Dates after the Nov 26 walkthrough: the first pay app falls in the billing cycle after the job
starts, on a business day; the pay period comes from the job's contract and the progress form
counts only what was recorded in it; payment due dates count from the sender's own day and land
on a business day; and the Dashboard's two new reminders (no job record yesterday, a signed
change order with no work)."""

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import payer_page
from test_money import new_job, new_state
from test_sov import line, sov_job


class BusinessDayTests(unittest.TestCase):
    def test_federal_holidays_as_observed(self):
        h = domain.us_federal_holidays(2026)
        for d in (date(2026, 1, 1), date(2026, 1, 19), date(2026, 5, 25), date(2026, 7, 3), date(2026, 11, 26), date(2026, 12, 25)):
            self.assertIn(d, h)
        self.assertNotIn(date(2026, 7, 4), h, "a Saturday's holiday is kept the Friday before")
        self.assertIn(date(2027, 12, 31), domain.us_federal_holidays(2027), "New Year's Day 2028 is a Saturday")

    def test_moving_to_a_business_day(self):
        self.assertEqual(domain.business_day_on_or_before(date(2026, 11, 15)), date(2026, 11, 13), "Sunday to Friday")
        self.assertEqual(domain.business_day_on_or_after(date(2026, 11, 26)), date(2026, 11, 27), "Thanksgiving to Friday")
        self.assertEqual(domain.business_day_on_or_after(date(2027, 1, 25)), date(2027, 1, 25))


class FirstPayAppTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, started_on="2026-11-26")
        self.payer = self.state["payers"][0]
        self.payer["payapp_due_day"] = 15

    def test_a_job_started_today_is_first_due_next_cycle(self):   # Stephen's job: started Nov 26, pay apps due the 15th
        self.assertEqual(domain.next_payapp_due(self.state, self.job, today=date(2026, 11, 26)), date(2026, 12, 15))
        dates = domain.job_key_dates(self.state, self.job, today=date(2026, 11, 26))
        self.assertFalse(any(d["date"] < "2026-11-26" for d in dates if d["key"].startswith("cutoff") or d["key"] == "first_payapp"))
        first = next(d for d in dates if d["label"] == "First pay app due")
        self.assertEqual(first["date"], "2026-12-15")

    def test_a_cutoff_on_a_weekend_is_the_friday_before(self):
        self.job["started_on"] = "2026-10-20"
        self.assertEqual(domain.next_payapp_due(self.state, self.job, today=date(2026, 11, 2)), date(2026, 11, 13), "Nov 15, 2026 is a Sunday")

    def test_no_cutoff_no_due_date(self):
        self.payer.pop("payapp_due_day")
        self.assertIsNone(domain.next_payapp_due(self.state, self.job, today=date(2026, 11, 26)))


class PayPeriodTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)
        self.job["started_on"] = "2026-10-01"
        self.rough = line(self.job, "1")

    def test_the_period_comes_from_the_contract(self):
        domain.edit_job(self.state, self.job["id"], {"period_end_day": "15"}, "Owner")
        self.assertEqual(self.job["period_end_day"], 15)
        p = domain.pay_period(self.state, self.job, today=date(2026, 11, 27))
        self.assertEqual((p["from"], p["to"]), ("2026-10-01", "2026-10-15"), "the first period after the start, still not billed")
        for inv in self.job["invoices"]:
            inv["status"] = "void"
        self.job["invoices"].append({"id": "x", "status": "sent", "period_to": "2026-11-15", "lines": []})
        p = domain.pay_period(self.state, self.job, today=date(2026, 11, 27))
        self.assertEqual((p["from"], p["to"]), ("2026-11-16", "2026-12-15"), "the 16th to the 15th")
        with self.assertRaises(domain.GateError):
            domain.edit_job(self.state, self.job["id"], {"period_end_day": "40"}, "Owner")

    def test_without_a_setting_the_client_day_then_the_month_end(self):
        self.assertEqual(domain.period_end_day(self.state, self.job), 31)
        self.state["payers"][0]["payapp_due_day"] = 25
        self.assertEqual(domain.period_end_day(self.state, self.job), 25)

    def test_the_progress_form_leaves_what_came_after_the_period(self):
        self.job["period_end_day"] = 15
        self.job["invoices"].append({"id": "x", "number": "PA-00", "status": "sent", "period_to": "2026-10-15", "lines": [], "amount_cents": 0,
                                     "retainage_cents": 0, "gross_cents": 0, "created_at": "2026-10-15T12:00:00+00:00", "sent_at": "2026-10-15T12:00:00+00:00"})
        domain.add_daily_record(self.state, self.job["id"], "In the period", at="2026-11-10T12:00:00+00:00",
                                details={"sections": {"quantities": [{"line_id": self.rough["id"], "qty": "10"}]}})
        domain.add_daily_record(self.state, self.job["id"], "After it", at="2026-11-20T12:00:00+00:00",
                                details={"sections": {"quantities": [{"line_id": self.rough["id"], "qty": "4"}]}})
        view = domain.job_view(self.state, self.job)
        self.assertEqual((view["pay_period"]["from"], view["pay_period"]["to"]), ("2026-10-16", "2026-11-15"))
        self.assertEqual(view["pay_period"]["after_milli"], {self.rough["id"]: 4000})

    def test_a_new_job_takes_the_setting(self):
        job = new_job(self.state, name="Bath", period_end_day="15")
        self.assertEqual(job["period_end_day"], 15)
        with self.assertRaises(domain.GateError) as ctx:
            new_job(self.state, name="Den", period_end_day="0")
        self.assertIn("period_end_day", ctx.exception.fields)


class PaymentDueTests(unittest.TestCase):
    def test_net_60_from_nov_26_is_jan_25(self):   # the walkthrough's email: right date, shown as 2027-01-25
        payer = {"payment_terms_days": 60}
        self.assertEqual(domain.payment_due_on(payer, domain.local_today()), domain.payment_due_on(payer))
        self.assertEqual(domain._long_day("2027-01-25"), "January 25, 2027")

    def test_a_due_date_on_a_weekend_or_holiday_moves_to_the_next_business_day(self):
        now = datetime(2026, 11, 27, 15, tzinfo=timezone.utc)
        sent = domain.local_today("2026-11-27", now)
        self.assertEqual(sent, "2026-11-27")
        due = domain.business_day_on_or_after(date(2026, 11, 27) + __import__("datetime").timedelta(days=30))
        self.assertEqual(due, date(2026, 12, 28), "Dec 27, 2026 is a Sunday")

    def test_the_email_and_the_send_use_words_and_the_senders_day(self):
        state = new_state()
        job = new_job(state)
        state["payers"][0]["payment_terms_days"] = 60
        domain.record_progress(state, job["id"], 1_000_000)
        inv = [i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES][-1]
        preview = domain.email_preview(state, job, inv)
        self.assertRegex(preview["body"], r"Payment due [A-Z][a-z]+ \d{1,2}, \d{4}\.")
        self.assertNotRegex(preview["body"], r"Payment due \d{4}-")
        self.assertEqual(preview["due_on"], domain.payment_due_on(state["payers"][0]))


class PayerApproveTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state)
        domain.record_progress(self.state, self.job["id"], 1_000_000)
        self.inv = self.job["invoices"][-1]
        self.inv.update(status="sent", sent_at="2026-11-26T20:00:00+00:00", due_on="2027-01-25")

    def test_the_payers_evening_is_still_today(self):
        # 9 pm in Atlanta on Nov 26 is Nov 27 in UTC: the payer's "today" is accepted.
        payer_page.approve(self.state, self.job, self.inv, "Yusuf", "2026-11-26", "2026-11-27T02:00:00+00:00")
        self.assertEqual(self.inv["expected_payment_on"], "2026-11-26")

    def test_a_date_long_gone_is_refused(self):
        with self.assertRaises(domain.GateError):
            payer_page.approve(self.state, self.job, self.inv, "Yusuf", "2018-02-10", "2026-11-27T02:00:00+00:00")

    def test_the_form_starts_at_the_due_date(self):
        html = payer_page._approval("Pine", {**self.inv, "due_on": "2099-01-25"}, "/p/x", {})
        self.assertIn('value="2099-01-25"', html)
        self.assertIn("due by Jan 25, 2099", html)
        self.assertIn(' min="', html)


class ReminderRowTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)
        self.job["started_on"] = "2026-11-02"
        self.id = self.job["id"]

    def rows(self, kind, today):
        now = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).replace(hour=14)
        return [r for r in domain.today_rows(self.state, now) if r["kind"] == kind]

    def test_no_job_record_yesterday(self):
        domain.add_daily_record(self.state, self.id, "Day one", at="2026-11-24T12:00:00+00:00")
        rows = self.rows("no_record", date(2026, 11, 26))
        self.assertEqual(len(rows), 1)
        self.assertIn("No job record yesterday on Pine kitchen", rows[0]["text"])
        self.assertEqual(rows[0]["key"], f"no_record:{self.id}:2026-11-25")
        domain.dismiss_row(self.state, self.id, rows[0]["key"], "Ana")
        self.assertEqual(self.rows("no_record", date(2026, 11, 26)), [], "dismissed for that day")
        domain.add_daily_record(self.state, self.id, "Day two", at="2026-11-25T12:00:00+00:00")
        self.assertEqual(self.rows("no_record", date(2026, 11, 26)), [])

    def test_business_days_only(self):
        domain.add_daily_record(self.state, self.id, "Friday", at="2026-11-20T12:00:00+00:00")
        self.assertEqual(self.rows("no_record", date(2026, 11, 23)), [], "Monday: Friday had one; the weekend doesn't count")
        rows = self.rows("no_record", date(2026, 11, 27))
        self.assertEqual(rows[0]["item_id"], "2026-11-25", "the day after Thanksgiving looks back to Wednesday")
        self.assertIn("on Nov 25, 2026", rows[0]["text"])

    def test_a_job_without_records_or_not_started_is_left_alone(self):
        self.assertEqual(self.rows("no_record", date(2026, 11, 26)), [])
        self.job["started_on"] = "2026-12-01"
        domain.add_daily_record(self.state, self.id, "Planning", at="2026-11-20T12:00:00+00:00")
        self.assertEqual(self.rows("no_record", date(2026, 11, 26)), [])

    def test_a_signed_change_order_with_no_work(self):
        co = {"id": "co-1", "number": 2, "title": "Extra outlets", "status": "approved", "amount_cents": 50000}
        self.job["change_orders"].append(co)
        rows = self.rows("co_no_work", date(2026, 11, 26))
        self.assertEqual(len(rows), 1)
        self.assertIn("Change order 2 (Extra outlets) approved but no work recorded against it", rows[0]["text"])
        self.assertEqual(rows[0]["tone"], "amber")
        self.state["payers"][0]["payapp_due_day"] = 27
        soon = self.rows("co_no_work", date(2026, 11, 25))[0]
        self.assertEqual(soon["tone"], "red")
        self.assertIn("The pay app is due Nov 27, 2026", soon["text"])
        domain.add_daily_record(self.state, self.id, "Outlets", details={"sections": {"co_work": [{"co_id": "co-1", "description": "Two circuits"}]}})
        self.assertEqual(self.rows("co_no_work", date(2026, 11, 26)), [])

    def test_only_reminders_are_dismissed(self):
        with self.assertRaises(domain.GateError):
            domain.dismiss_row(self.state, self.id, f"ready:{self.id}:x", "Ana")


if __name__ == "__main__":
    unittest.main()
