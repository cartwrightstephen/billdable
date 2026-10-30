"""The Daily record (T11, T12): crew, weather, materials and deliveries, the ticked sections and
files, checked on the server and read out on the job record. Quantities feeding the pay app (M1)
are in tests/test_sov.py LivingDraftTests."""

import sys
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
from test_accounts import ServerCase
from test_money import new_job, new_state
from test_sov import LINES


class DailyRecordTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state)

    def add(self, notes="Framing on the east wall", **details):
        return domain.add_daily_record(self.state, self.job["id"], notes, "Ana", at="2026-10-30T15:00:00+00:00", details=details)

    def refused(self, field, **details):
        with self.assertRaises(domain.GateError) as ctx:
            self.add(**details)
        self.assertIn(field, ctx.exception.fields, details)
        self.assertEqual(self.state.get("daily_records", []), [], "nothing saved")
        return ctx.exception

    def test_crew_weather_materials_and_deliveries_are_kept_and_read_out_on_the_job_record(self):
        self.add(crew_count="4", crew_names="Ana, Ben\nCruz", weather="Rain", weather_note="Light, morning only",
                 materials="12 sheets of drywall", deliveries="Tile from Floor & Decor")
        rec = self.state["daily_records"][-1]
        self.assertEqual((rec["crew_count"], rec["crew_names"], rec["weather"]), (4, ["Ana", "Ben", "Cruz"], "Rain"))
        record = self.job["records"][-1]
        self.assertEqual((record["kind"], record["label"]), ("daily", "Daily record 2026-10-30"))
        self.assertEqual(record["note"], "Framing on the east wall\nCrew: 4 (Ana, Ben, Cruz)\nWeather: Rain, Light, morning only\n"
                                         "Materials: 12 sheets of drywall\nDeliveries: Tile from Floor & Decor")

    def test_a_diary_alone_is_saved_as_before(self):
        domain.add_daily_record(self.state, self.job["id"], "Just a note")
        self.assertEqual(self.job["records"][-1]["note"], "Just a note")
        self.assertNotIn("crew_count", self.state["daily_records"][-1])

    def test_fields_are_checked(self):
        self.refused("crew_count", crew_count="501")
        self.refused("crew_count", crew_count="-1")
        self.refused("crew_count", crew_count="four")
        self.refused("weather", weather="Hail")
        self.refused("materials", materials="x" * 2001)
        self.refused("crew_names", crew_names=[f"n{i}" for i in range(51)])
        self.refused("weather_note", weather_note={"a": 1})

    def test_sections_are_checked(self):
        self.refused("sections", sections={"selfies": {}})
        self.refused("sections", sections=["photos"])
        self.refused("quantities", sections={"quantities": [{"line_id": "sov-x", "qty": "1"}]})   # no schedule on this job
        self.refused("co_work", sections={"co_work": []})
        self.refused("co_0", sections={"co_work": [{"co_id": "co-elsewhere", "description": "Extra outlet"}]})
        self.refused("co_hours_0", sections={"co_work": [{"description": "Extra outlet", "hours": "-2"}]})
        self.refused("insp_type_0", sections={"inspections": [{"type": "party", "who": "City"}]})
        self.refused("inspections", sections={"inspections": [{"type": "inspection"}]})
        self.refused("punch", sections={"punch": "\n  \n"})

    def test_ticked_sections_are_kept_and_read_out(self):
        domain.add_change_order(self.state, self.job["id"], "Extra outlets", 50_000)
        co = self.job["change_orders"][-1]
        self.add(sections={"photos": {"note": "East wall"}, "co_work": [{"co_id": co["id"], "description": "Ran two circuits", "hours": "3.5"},
                                                                         {"co_id": "", "description": "Moved a vent"}],
                           "inspections": [{"type": "inspection", "who": "City electrical", "outcome": "Passed"}],
                           "punch": "Patch hallway\nTouch up trim"})
        rec = self.state["daily_records"][-1]
        self.assertEqual(rec["sections"]["co_work"][0], {"co_id": co["id"], "description": "Ran two circuits", "hours_milli": 3500})
        self.assertEqual(rec["sections"]["punch"], ["Patch hallway", "Touch up trim"])
        note = self.job["records"][-1]["note"]
        self.assertIn(f"Change order work (CO {co['number']}): Ran two circuits, 3.5 h", note)
        self.assertIn("Change order work (new, not priced yet): Moved a vent", note)
        self.assertIn("Inspection with City electrical: Passed", note)
        self.assertIn("New punchlist items: Patch hallway; Touch up trim", note)
        self.assertIn("Progress photos: East wall", note)

    def test_quantities_name_lines_of_this_jobs_schedule_only(self):
        job = new_job(self.state, contract=3_450_000, name="SOV job")
        domain.save_sov(self.state, job["id"], LINES, "15")
        line = domain.sov.lines(job)[0]
        with self.assertRaises(domain.GateError) as ctx:   # a line of another job's schedule
            domain.add_daily_record(self.state, self.job["id"], "x", details={"sections": {"quantities": [{"line_id": line["id"], "qty": "1"}]}})
        self.assertIn("quantities", ctx.exception.fields)
        for qty, field in (("-1", f"qty_{line['id']}"), ("lots", f"qty_{line['id']}"), ("", "quantities")):
            with self.assertRaises(domain.GateError) as ctx:
                domain.add_daily_record(self.state, job["id"], "x", details={"sections": {"quantities": [{"line_id": line["id"], "qty": qty}]}})
            self.assertIn(field, ctx.exception.fields, qty)
        lump = domain.sov.lines(job)[1]
        with self.assertRaises(domain.GateError) as ctx:
            domain.add_daily_record(self.state, job["id"], "x", details={"sections": {"quantities": [{"line_id": lump["id"], "qty": "12.25"}]}})
        self.assertIn("at most one decimal", str(ctx.exception))

    def test_files_must_be_on_this_jobs_record_and_are_linked(self):
        domain.add_job_record(self.state, self.job["id"], "photo.jpg", "photo")
        bare = self.job["records"][-1]
        self.refused("attachments", attachments=[bare["id"]])   # a record with no file
        self.refused("attachments", attachments=["rec-nope"])
        bare["file_ids"] = ["f-1"]
        self.add(attachments=[bare["id"], bare["id"]], sections={"photos": {}})
        rec = self.state["daily_records"][-1]
        self.assertEqual(rec["attachments"], [bare["id"]])
        self.assertEqual(bare["daily_record_id"], rec["id"])
        self.assertIn("1 file attached", self.job["records"][-1]["note"])


class DailyRecordHttpTests(ServerCase):
    def test_a_photo_uploaded_first_is_linked_to_the_daily_record(self):
        owner, _ = self.signup("daily@site.example", "Site Co")
        _, state = self.request("POST", "/api/jobs", {"name": "Porch", "customer": "Dana Lee", "contract_cents": 1_000_000, "address": "1 Elm St",
                                                      "approver": "Dana", "ap_email": "dana@example.com"}, cookie=owner)
        job = state["jobs"][0]
        res, state = self.request("POST", f"/api/import/file?job={job['id']}&file=1&kind=photo", raw=b"\xff\xd8\xff\xe0 photo", cookie=owner,
                                  headers={"Content-Type": "image/jpeg", "X-Filename": quote("east wall.jpg")})
        self.assertEqual(res.status, 200, state)
        photo = state["jobs"][0]["records"][-1]
        self.assertEqual(photo["kind"], "photo")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/daily-record",
                                  {"notes": "Framing", "weather": "Clear", "crew_count": 3, "attachments": [photo["id"]], "sections": {"photos": {}}}, cookie=owner)
        self.assertEqual(res.status, 200, state)
        rec = state["daily_records"][-1]
        self.assertEqual((rec["weather"], rec["crew_count"], rec["attachments"]), ("Clear", 3, [photo["id"]]))
        res, out = self.request("POST", f"/api/jobs/{job['id']}/daily-record", {"notes": "x", "weather": "Fog"}, cookie=owner)
        self.assertEqual((res.status, out["fields"]), (409, {"weather": "Pick the weather from the list."}))
        res, out = self.request("POST", f"/api/jobs/{job['id']}/daily-record", {"weather": "Clear"}, cookie=owner)
        self.assertEqual((res.status, out["error"]), (409, "Enter the diary entry."))


if __name__ == "__main__":
    unittest.main()
