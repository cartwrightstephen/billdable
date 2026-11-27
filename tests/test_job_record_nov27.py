"""New job record after the Nov 26 walkthrough: a conversation goes through the form, a time sheet
is rows of crew and hours, a quantity past what a line has left asks first (a soft gate), and Edit
on a saved record changes it through the same form."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import sov
from test_money import new_job, new_state
from test_sov import line, sov_job


class FormTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)
        self.id = self.job["id"]
        self.rough = line(self.job, "1")

    def save(self, notes="", **body):
        return domain.save_job_record(self.state, self.id, notes, "Ana", {"today": domain.local_today(), **body}, gate_over=True)

    def test_a_conversation_is_a_section_of_the_form(self):
        self.save("", entries=[{"section": "conversation", "row": 0, "text": "Owner wants the vanity moved 6 inches"}])
        rec = self.job["records"][-1]
        self.assertEqual((rec["kind"], rec["label"]), ("conversation", "Conversation with Sam Jones"))
        self.assertEqual(rec["note"], "Owner wants the vanity moved 6 inches")
        self.assertEqual(self.job["last_contact"]["text"], "Owner wants the vanity moved 6 inches")

    def test_a_conversation_with_a_date_needs_a_pay_app_waiting(self):
        with self.assertRaises(domain.GateError) as ctx:
            self.save("", entries=[{"section": "conversation", "row": 0, "text": "Pays Friday", "next_on": "2099-01-01"}])
        self.assertIn("conversation_next_on_0", ctx.exception.fields)

    def test_a_time_sheet_is_rows_of_crew_and_hours(self):
        self.save("Set the toilet", entries=[{"section": "time_sheets", "row": 0, "crew": [
            {"name": "Stephen", "hours": "8", "member_id": "u-1"}, {"name": "Jose (day labor)", "hours": "4"}, {"name": "", "hours": ""}]}])
        sheet = next(r for r in self.job["records"] if r.get("ticket_type") == "time_sheet")
        self.assertEqual(sheet["label"], "Time sheet: 2 crew, 12 h")
        self.assertEqual(sheet["hours_milli"], 12_000)
        self.assertEqual([p["name"] for p in sheet["crew"]], ["Stephen", "Jose (day labor)"])
        self.assertEqual(sheet["crew"][0]["member_id"], "u-1")
        daily = self.state["daily_records"][-1]
        self.assertEqual((daily["crew_count"], daily["crew_names"]), (2, ["Stephen", "Jose (day labor)"]), "the count comes from the rows")

    def test_time_sheet_hours_are_numbers_and_a_name_needs_them(self):
        for row, field in (({"name": "Ben", "hours": "eight"}, "time_sheets_crew_hours_0"), ({"name": "Ben", "hours": ""}, "time_sheets_crew_hours_0"),
                           ({"name": "", "hours": "8"}, "time_sheets_crew_name_0"), ({"name": "Ben", "hours": "200"}, "time_sheets_crew_hours_0")):
            with self.assertRaises(domain.GateError, msg=row) as ctx:
                self.save("x", entries=[{"section": "time_sheets", "row": 0, "crew": [row]}])
            self.assertIn(field, ctx.exception.fields)

    def test_a_quantity_past_what_the_line_has_left_asks_first(self):
        with self.assertRaises(domain.GateError) as ctx:
            self.save("Wire", sections={"quantities": [{"line_id": self.rough["id"], "qty": "401"}]})
        self.assertEqual(ctx.exception.extra["over_schedule"][0]["left_milli"], 400_000)
        self.assertIn(f"qty_{self.rough['id']}", ctx.exception.fields)
        self.assertEqual(self.state.get("daily_records", []), [], "nothing saved until they say so")
        self.save("Wire", sections={"quantities": [{"line_id": self.rough["id"], "qty": "401"}]}, over_ok=True)
        self.assertEqual(sov.field_total(self.rough), 400_000, "recorded anyway, up to the line")
        self.assertIn("over the schedule", self.state["daily_records"][-1]["quantities_not_applied"])

    def test_what_is_left_counts_what_is_recorded_already(self):
        self.save("Wire", sections={"quantities": [{"line_id": self.rough["id"], "qty": "399"}]})
        with self.assertRaises(domain.GateError):
            self.save("More", sections={"quantities": [{"line_id": self.rough["id"], "qty": "2"}]})
        self.save("More", sections={"quantities": [{"line_id": self.rough["id"], "qty": "1"}]})

    def test_an_import_still_records_what_it_is_given(self):
        domain.add_daily_record(self.state, self.id, "Import", details={"sections": {"quantities": [{"line_id": self.rough["id"], "qty": "500"}]}})
        self.assertEqual(sov.field_total(self.rough), 400_000)


class EditTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)
        self.id = self.job["id"]
        self.rough = line(self.job, "1")
        domain.save_job_record(self.state, self.id, "Day one", "Ana", {"today": domain.local_today(), "materials": "Caulking",
                               "sections": {"quantities": [{"line_id": self.rough["id"], "qty": "10"}]},
                               "entries": [{"section": "delivery", "row": 0, "supplier": "ABC", "number": "7", "what": "Toilet"},
                                           {"section": "time_sheets", "row": 0, "crew": [{"name": "Stephen", "hours": "8"}]}]})
        self.daily = self.state["daily_records"][-1]
        self.ticket = next(r for r in self.job["records"] if r.get("ticket_type") == "delivery")
        self.sheet = next(r for r in self.job["records"] if r.get("ticket_type") == "time_sheet")

    def edit(self, notes, **body):
        return domain.update_job_record(self.state, self.id, self.daily["id"], notes, "Ben", {"today": domain.local_today(), **body})

    def test_the_diary_record_points_at_its_form(self):
        shown = next(r for r in self.job["records"] if r["kind"] == "daily")
        self.assertEqual(shown["daily_record_id"], self.daily["id"])
        view = domain.job_view(self.state, self.job)
        self.assertEqual(next(r for r in view["records"] if r["kind"] == "daily")["daily_record_id"], self.daily["id"])

    def test_edit_changes_every_part_in_place(self):
        self.edit("Day one, corrected", materials="Caulking, wax ring",
                  sections={"quantities": [{"line_id": self.rough["id"], "qty": "12"}]},
                  entries=[{"section": "delivery", "row": 0, "record_id": self.ticket["id"], "supplier": "ABC", "number": "8", "what": "Toilet and seat"},
                           {"section": "time_sheets", "row": 0, "record_id": self.sheet["id"], "crew": [{"name": "Stephen", "hours": "8"}, {"name": "Jose", "hours": "4"}]}])
        self.assertEqual((self.daily["notes"], self.daily["materials"]), ("Day one, corrected", "Caulking, wax ring"))
        self.assertEqual(sov.field_total(self.rough), 12_000, "the old 10 came off and the new 12 went on")
        self.assertEqual((self.ticket["label"], self.ticket["note"]), ("Delivery ticket: ABC #8", "Toilet and seat"))
        self.assertEqual((self.sheet["label"], self.sheet["hours_milli"]), ("Time sheet: 2 crew, 12 h", 12_000))
        self.assertEqual(self.daily["crew_count"], 2)
        shown = next(r for r in self.job["records"] if r["kind"] == "daily")
        self.assertIn("Day one, corrected", shown["note"])
        self.assertIn("12 ea Rough-in wiring", shown["label"])

    def test_a_row_taken_off_goes_and_a_new_row_is_added(self):
        self.edit("Day one", entries=[{"section": "time_sheets", "row": 0, "record_id": self.sheet["id"], "crew": [{"name": "Stephen", "hours": "8"}]},
                                      {"section": "invoices", "row": 0, "supplier": "Home Depot", "number": "55", "amount": "40"}])
        labels = [r["label"] for r in self.job["records"]]
        self.assertNotIn(self.ticket["label"], labels)
        self.assertIn("Invoice: Home Depot #55", labels)
        self.assertEqual(sov.field_total(self.rough), 0, "no quantities sent: what it added comes off")

    def test_edit_is_refused_for_a_row_from_another_record(self):
        other = domain.add_job_record(self.state, self.id, "Loose note", "note", "", "Ana")["records"][-1]
        with self.assertRaises(domain.GateError):
            self.edit("Day one", entries=[{"section": "other", "row": 0, "record_id": other["id"], "kind": "note", "label": "Hijack"}])

    def test_billed_quantities_are_not_changed_here(self):
        domain.record_sov_progress(self.state, self.id, {})
        inv = [i for i in self.job["invoices"] if i["status"] in domain.DRAFT_STATUSES][-1]
        inv["status"] = "sent"
        with self.assertRaises(domain.GateError) as ctx:
            self.edit("Day one", sections={"quantities": [{"line_id": self.rough["id"], "qty": "11"}]})
        self.assertIn("Correct field total", str(ctx.exception))
        self.edit("Typo fixed", sections={"quantities": [{"line_id": self.rough["id"], "qty": "10"}]},
                  entries=[{"section": "delivery", "row": 0, "record_id": self.ticket["id"], "supplier": "ABC", "number": "7", "what": "Toilet"},
                           {"section": "time_sheets", "row": 0, "record_id": self.sheet["id"], "crew": [{"name": "Stephen", "hours": "8"}]}])
        self.assertEqual(self.daily["notes"], "Typo fixed")


if __name__ == "__main__":
    unittest.main()
