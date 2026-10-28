"""Reading dates, kinds and jobs from imported notes and file names (R5-01, R5-02)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importer

ROOT = Path(__file__).resolve().parents[1]


def jobs_state():
    return {"jobs": [
        {"id": "j1", "code": "KENN", "name": "Kennesaw Ortho Clinic", "address": "3895 Cherokee St NW, Kennesaw"},
        {"id": "j2", "code": "MILL", "name": "Mill Creek Clubhouse", "address": "2150 Mill Creek Pkwy, Marietta"},
    ], "payers": []}


class DatesAreDatesTests(unittest.TestCase):  # R5-01
    def test_a_job_code_after_the_date_keeps_it_a_date(self):
        self.assertEqual(importer.leading_date("10/16 VEST corridor tile done", 2026).isoformat(), "2026-10-16")
        self.assertEqual(importer.leading_date("1/2 MILL started wall tile", 2026).isoformat(), "2026-01-02")
        self.assertEqual(importer.leading_date("9/16 crew of 4 on site", 2026).isoformat(), "2026-09-16")

    def test_quantities_with_a_unit_are_not_dates(self):
        self.assertIsNone(importer.leading_date("3/4 inch pipe ran to the panel", 2026))
        self.assertIsNone(importer.leading_date("1/2 bath demo", 2026))
        self.assertIsNone(importer.leading_date('5/8" drywall hung', 2026))
        self.assertIsNone(importer.leading_date("15/16 of the tile set", 2026))

    def test_the_fraction_notes_file_splits_on_every_date(self):
        text = (ROOT / "testdata/test-contractor/app-issues/notes_date-read-as-fraction.txt").read_text()
        self.assertEqual([c["date"] for c in importer.split_notes(text, 2026)],
                         [None, "2026-10-15", "2026-10-16", "2026-01-02"])


class FileNamesTests(unittest.TestCase):  # R5-02
    def test_underscored_name_finds_the_code_and_the_kind(self):
        m = importer.match_job(jobs_state(), "", names="KENN_subcontract.pdf")
        self.assertEqual((m["job_id"], m["reason"]), ("j1", "mentions job code KENN"))
        self.assertEqual(importer.guess_kind("KENN_subcontract.pdf", "", [".pdf"]), "contract")

    def test_a_code_in_lower_case_counts_only_in_a_file_name(self):
        self.assertEqual(importer.match_job(jobs_state(), "", names="13_Scan_kenn_subcontract.pdf")["job_id"], "j1")
        self.assertIsNone(importer.match_job(jobs_state(), "the kenn crew and the mill shop")["job_id"])
        self.assertIsNone(importer.match_job(jobs_state(), "", names="IMG_2231.jpg")["job_id"])

    def test_street_number_and_name_match_the_address(self):
        m = importer.match_job(jobs_state(), "Tile delivered to 3895 Cherokee this morning")
        self.assertEqual(m["job_id"], "j1")
        self.assertIn("3895 Cherokee", m["reason"])

    def test_an_email_whose_only_attachment_is_a_picture_is_a_photo(self):
        self.assertEqual(importer.guess_kind("08_email_photo-attachment-from-foreman.eml IMG_2240.jpg",
                                             "Layout is snapped for the lobby.", [".eml", ".jpg"]), "photo")
        self.assertEqual(importer.guess_kind("note.eml scan.pdf IMG_1.jpg", "See attached.", [".eml", ".pdf", ".jpg"]), "note")
        self.assertEqual(importer.guess_kind("note.eml", "Call me.", [".eml"]), "note")
        self.assertEqual(importer.guess_kind("IMG_1.heic", "", ".heic"), "photo")

    def test_the_foreman_email_upload_is_a_photo_for_mill(self):
        raw = (ROOT / "testdata/test-contractor/import/08_email_photo-attachment-from-foreman.eml").read_bytes()
        parsed = importer.parse_email(raw)
        state = jobs_state()
        state["files"] = [{"id": "f1", "name": "08_email_photo-attachment-from-foreman.eml", "ext": ".eml"},
                          {"id": "f2", "name": parsed["attachments"][0]["filename"], "ext": ".jpg"}]
        item = importer.add_item(state, "upload", text=parsed["text"], sender=parsed["from"], subject=parsed["subject"],
                                 file_ids=["f1", "f2"])
        self.assertEqual((item["suggested_job_id"], item["kind"]), ("j2", "photo"))


if __name__ == "__main__":
    unittest.main()
