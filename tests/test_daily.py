"""The New job record form (T11, T12, the Nov 3 overhaul): crew, weather, materials and deliveries, the ticked sections and
files, checked on the server and read out on the job record. Quantities feeding the pay app (M1)
are in tests/test_sov.py LivingDraftTests."""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
from test_accounts import ServerCase
from test_money import new_job, new_state
from test_sov import LINES


class NewJobRecordTests(unittest.TestCase):
    """One form, many records (the Nov 3 overhaul): tickets, photos, time sheets, invoices,
    inspections and other documents each become a record of their own kind."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state)
        self.id = self.job["id"]

    def save(self, notes="", **body):
        return domain.save_job_record(self.state, self.id, notes, "Ana", {"today": domain.local_today(), **body})

    def upload(self, kind, name):
        """A file already on the job, as the form's upload leaves it (the importer's file_item)."""
        domain.add_job_record(self.state, self.id, name, kind, "", "Ana")
        rec = self.job["records"][-1]
        rec["file_ids"] = ["f-" + name]
        self.state.setdefault("files", []).append({"id": "f-" + name, "name": name, "ext": "." + name.rsplit(".", 1)[-1], "size": 10})
        return rec

    def refused(self, field, notes="", **body):
        before = len(self.job.get("records", []))
        with self.assertRaises(domain.GateError) as ctx:
            self.save(notes, **body)
        self.assertIn(field, ctx.exception.fields)
        self.assertEqual(len(self.job.get("records", [])), before, "nothing saved")
        self.assertEqual(self.state.get("daily_records", []), [], "nothing saved")

    def test_each_section_makes_a_record_of_its_own_kind(self):
        self.save("Framing", entries=[
            {"section": "delivery", "row": 0, "supplier": "Floor & Decor", "number": "T-88", "what": "Tile"},
            {"section": "photos", "row": 0, "note": "East wall"},
            {"section": "time_sheets", "row": 0, "who": "Ana's crew", "hours": "32"},
            {"section": "invoices", "row": 0, "supplier": "ABC Supply", "number": "1042", "amount": "1,240.50"},
            {"section": "inspections", "row": 0, "who": "City inspector", "outcome": "Passed rough-in"},
            {"section": "other", "row": 0, "kind": "insurance", "label": "COI 2026", "note": "Brightwater"},
        ])
        recs = {r["label"]: r for r in self.job["records"]}
        ticket = recs["Delivery ticket: Floor & Decor #T-88"]
        self.assertEqual((ticket["kind"], ticket["ticket_type"], ticket["note"]), ("ticket", "delivery", "Tile"))
        self.assertEqual(recs["Progress photo: East wall"]["kind"], "photo")
        sheet = recs["Time sheet: Ana's crew"]
        self.assertEqual((sheet["kind"], sheet["ticket_type"], sheet["note"]), ("ticket", "time_sheet", "Hours: 32"))
        invoice = recs["Invoice: ABC Supply #1042"]
        self.assertEqual((invoice["kind"], invoice["amount_cents"], invoice["note"]), ("supplier_invoice", 124050, "Amount: $1,240.50"))
        inspection = recs["Inspection: City inspector"]
        self.assertEqual((inspection["kind"], inspection["note"]), ("inspection", "Passed rough-in"))
        self.assertEqual((recs["COI 2026"]["kind"], recs["COI 2026"]["note"]), ("insurance", "Brightwater"))
        self.assertEqual(len(self.state["daily_records"]), 1, "the diary is one record of its own")

    def test_each_file_is_a_record_with_the_sections_fields_and_kind(self):
        one, two = self.upload("note", "a.jpg"), self.upload("note", "b.jpg")
        self.save(entries=[{"section": "delivery", "row": 0, "supplier": "ABC", "attachments": [one["id"], two["id"]]}])
        self.assertEqual((one["kind"], one["ticket_type"], one["label"]), ("ticket", "delivery", "Delivery ticket: ABC (1 of 2)"))
        self.assertEqual(two["label"], "Delivery ticket: ABC (2 of 2)")
        self.assertEqual(len(self.job["records"]), 2, "no extra record for the section")

    def test_a_file_with_nothing_typed_keeps_its_name(self):
        one = self.upload("photo", "deck.jpg")
        self.save(entries=[{"section": "photos", "row": 0, "attachments": [one["id"]]}])
        self.assertEqual(one["label"], "Progress photo: deck.jpg")
        other = self.upload("note", "plan.pdf")
        self.save(entries=[{"section": "other", "row": 0, "kind": "asbuilt", "attachments": [other["id"]]}])
        self.assertEqual((other["kind"], other["label"]), ("asbuilt", "plan.pdf"))

    def test_no_diary_is_needed_for_a_document(self):
        self.save(entries=[{"section": "invoices", "row": 0, "supplier": "ABC"}])
        self.assertEqual(self.state.get("daily_records", []), [], "no empty diary entry")
        self.assertEqual(self.job["records"][-1]["kind"], "supplier_invoice")

    def test_nothing_at_all_and_details_without_a_diary_are_refused(self):
        self.refused("notes")
        self.refused("notes", crew_count="3")

    def test_entries_are_checked(self):
        self.refused("entries", entries="x")
        self.refused("entries", entries=[{"section": "selfies", "row": 0, "note": "x"}])
        self.refused("invoices_amount_0", entries=[{"section": "invoices", "row": 0, "amount": "lots"}])
        self.refused("time_sheets_hours_1", entries=[{"section": "time_sheets", "row": 1, "hours": "-2"}])
        self.refused("other_label_0", entries=[{"section": "other", "row": 0, "kind": "note", "note": "No label"}])
        self.refused("other_kind_0", entries=[{"section": "other", "row": 0, "kind": "mystery", "label": "x"}])
        self.refused("other_kind_0", entries=[{"section": "other", "row": 0, "kind": "daily", "label": "x"}])
        self.refused("attachments", entries=[{"section": "photos", "row": 0, "attachments": ["rec-nope"]}])

    def test_the_date_is_kept_and_a_future_one_refused(self):
        day = (date.fromisoformat(domain.local_today()) - timedelta(days=3)).isoformat()
        self.save("Backfilled", date=day, entries=[{"section": "delivery", "row": 0, "supplier": "ABC"}])
        self.assertEqual(self.state["daily_records"][-1]["at"][:10], day)
        self.assertIn(f"Diary entry {day}", [r["label"] for r in self.job["records"]])
        self.assertEqual(self.job["records"][-1]["on_date"], day)
        self.assertEqual(self.job["records"][-1]["added_at"][:10], domain.utcnow()[:10], "goes with the pay app it was entered for")
        future = (date.fromisoformat(domain.local_today()) + timedelta(days=5)).isoformat()
        for bad in (future, "soon"):
            with self.assertRaises(domain.GateError) as ctx:
                self.save("Later", date=bad)
            self.assertIn("date", ctx.exception.fields)

    def test_records_are_still_suggested_for_their_items(self):
        """The packet rules read the kind: a time sheet or a delivery ticket is suggested for the ticket
        item, an invoice for the invoice item, a photo for the photo item."""
        for label, item_kind, section in (("Daily tickets and time sheets", "ticket", "time_sheets"),
                                          ("Daily tickets and time sheets", "ticket", "delivery"),
                                          ("Supplier invoices and receipts", "supplier_invoice", "invoices"),
                                          ("Progress photos", "photo", "photos")):
            self.job = new_job(self.state)
            self.id = self.job["id"]
            packet = [{"id": "p1", "kind": item_kind, "label": label, "required": True, "present": False}]
            rec = self.upload("note", f"{section}.jpg")
            self.save(entries=[{"section": section, "row": 0, "attachments": [rec["id"]]}])
            domain._attach_from_record(self.state, self.job, packet, "the test")
            self.assertFalse(packet[0].get("present"), "suggested for a person to check, not cleared")
            self.assertEqual(packet[0]["attachments"][0]["record_id"], rec["id"], section)
            self.assertTrue(packet[0]["attachments"][0]["unconfirmed"])

    def test_the_job_file_says_neither_old_name(self):
        import job_archive
        self.save("Framing", entries=[{"section": "delivery", "row": 0, "supplier": "ABC"}])
        text = (job_archive.summary_text(self.state, self.job) + job_archive.activity_text(self.state, self.job)).lower()
        self.assertIn("diary entry", text)
        self.assertNotIn("daily record", text)
        self.assertNotIn("job record", text)

    def test_changing_a_tickets_kind_drops_its_type(self):
        self.save(entries=[{"section": "time_sheets", "row": 0, "who": "Ana"}])
        rec = self.job["records"][-1]
        domain.edit_record(self.state, self.id, rec["id"], rec["label"], "ticket", "Ana")
        self.assertEqual(rec["ticket_type"], "time_sheet")
        domain.edit_record(self.state, self.id, rec["id"], rec["label"], "note", "Ana")
        self.assertNotIn("ticket_type", rec)


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
        self.assertEqual((record["kind"], record["label"]), ("daily", "Diary entry 2026-10-30"))
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
                                                      "approver": "Dana", "ap_email": "dana@example.com", "phone": "(404) 555-0100", "role": "Owner"}, cookie=owner)
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
