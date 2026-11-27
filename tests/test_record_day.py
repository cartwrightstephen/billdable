"""The day a job record is for (Nov 27 hunt). A record saved on a US evening has a UTC time on the
next day; everything that asks which day it was for reads the person's own day (domain.record_day):
the Dashboard's "No job record yesterday", what the pay period leaves for the next pay app, the lien
deadlines' last day of work, and the date Edit opens with."""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compliance
import domain
from test_money import new_state
from test_sov import line, sov_job


class RecordDayTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)
        self.job["started_on"] = "2026-10-01"

    def evening_record(self, day, notes="Framing"):
        """A record saved at 8:30 pm in Atlanta on `day`: 01:30 UTC the day after."""
        domain.add_daily_record(self.state, self.job["id"], notes, "Ana", details={})
        rec = self.state["daily_records"][-1]
        rec["at"] = f"{(date.fromisoformat(day) + timedelta(days=1)).isoformat()}T01:30:00+00:00"
        rec["day"] = day
        shown = next(r for r in self.job["records"] if r.get("daily_record_id") == rec["id"])
        shown["added_at"], shown["on_date"] = rec["at"], day
        return rec

    def test_a_record_keeps_the_day_the_page_sent(self):
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        domain.add_daily_record(self.state, self.job["id"], "Evening in Atlanta", "Ana", details={"today": yesterday})
        rec = self.state["daily_records"][-1]
        self.assertEqual(domain.record_day(rec), yesterday)
        self.assertEqual(next(r for r in self.job["records"] if r.get("daily_record_id") == rec["id"])["on_date"], yesterday)

    def test_an_evening_record_counts_for_its_own_day_on_the_dashboard(self):
        rec = self.evening_record("2026-11-23")   # a Monday
        next(r for r in self.job["records"] if r.get("daily_record_id") == rec["id"]).pop("on_date")   # one saved before on_date
        self.assertEqual(domain.record_missed_on(self.state, self.job, date(2026, 11, 24)), "", "Monday had its record")

    def test_an_evening_record_on_the_last_day_of_the_period_is_in_it(self):
        period = domain.pay_period(self.state, self.job, today=date(2026, 10, 15))
        rough = line(self.job, "1")
        rec = self.evening_record(period["to"])
        rec["quantities_added"] = [{"line_id": rough["id"], "qty_milli": 5000}]
        view = domain.job_view(self.state, self.job)
        self.assertEqual(view["pay_period"]["to"], period["to"])
        self.assertEqual(view["pay_period"]["after_milli"], {}, "recorded on the period's last day, where the crew is")

    def test_the_lien_deadlines_count_from_the_day_of_the_last_record(self):
        self.evening_record("2026-11-23")
        self.assertEqual(compliance._furnishing(self.state, self.job)[1], "2026-11-23")

    def test_edit_keeps_the_day_when_none_is_sent(self):
        rec = self.evening_record("2026-11-23")
        domain.update_job_record(self.state, self.job["id"], rec["id"], "Framing, corrected", "Ana", {})
        self.assertEqual(domain.record_day(rec), "2026-11-23")
        self.assertEqual(rec["at"], "2026-11-24T01:30:00+00:00", "its time is left as it was")


if __name__ == "__main__":
    unittest.main()
