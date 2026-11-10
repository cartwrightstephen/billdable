"""OCR with the real tesseract and pdftoppm (ocr.py): the words come back from a scanned waiver, a
scanned contract and a phone photo. Skipped where the programs aren't installed (the main CI jobs,
most laptops) and where Pillow isn't: Pillow only draws these tests' documents
(scripts/ocr_measure.py), and the app never imports it. CI's `ocr` job installs all three and runs these."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import contract_import
import doc_check
import importer
import ocr
import storage
from ocr_help import clean_env

try:
    import PIL  # noqa: F401
    PILLOW = True
except ImportError:
    PILLOW = False
PROGRAMS = bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


@unittest.skipUnless(PROGRAMS, "tesseract and pdftoppm aren't installed")
@unittest.skipUnless(PILLOW, "Pillow isn't installed (it only draws these tests' documents)")
class RealOcr(unittest.TestCase):
    settings: dict = {}

    def setUp(self):
        import ocr_measure
        self.draw = ocr_measure
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for patch in (mock.patch.object(importer, "UPLOADS", self.root / "uploads"), mock.patch.object(storage, "DATA_DIR", self.root),
                      mock.patch.dict(os.environ, {**clean_env(), "OCR_ENABLED": "1", **self.settings}, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)
        hook = ocr._AFTER
        ocr.after_read(None)
        ocr._PENDING.clear()
        self.addCleanup(ocr.after_read, hook)
        self.addCleanup(ocr._PENDING.clear)
        self.assertEqual(ocr.status(), "ready")

    def read(self, name, data):
        meta = importer.store_bytes("co-a", name, data, "test")
        self.assertTrue(ocr.enqueue("co-a", meta), name)
        self.assertEqual(ocr.run_waiting(), 1)
        entry = next(j for j in ocr.queue("co-a") if j["file_id"] == meta["id"])
        return entry, ocr.text_for("co-a", meta["id"])

    def test_a_scanned_conditional_waiver(self):
        pdf = self.draw.scan_pdf([self.draw.page_image(self.draw.WAIVER)])
        self.assertEqual(doc_check.prepare(".pdf", pdf).text, "", "today's reader gets nothing from it")
        entry, text = self.read("Signed waiver.pdf", pdf)
        self.assertEqual((entry["state"], entry["pages"], entry["pages_read"], entry["reason"]), ("read", 1, 1, ""))
        self.assertIn("CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT", text)
        self.assertIn("Elm addition", text)
        self.assertEqual(doc_check.reading(text), {"guess": "conditional", "form": True})
        read = doc_check.with_stored_text(doc_check.prepare(".pdf", pdf), ".pdf", lambda: text)
        self.assertTrue(read.ocr)
        self.assertEqual(doc_check.check([("Signed waiver.pdf", ".pdf", read)], "Elm addition", "$4,500.00", 450_000, "conditional_progress", True),
                         {"status": "matched", "problems": []})
        self.assertEqual(doc_check.check([("Signed waiver.pdf", ".pdf", read)], "Oak Street", "$9,999.00", 999_900, "unconditional_progress", True)["problems"],
                         ["Job name not found: Oak Street.", "Amount not found: $9,999.00.", "This reads as conditional."])

    def test_a_scanned_contract_of_several_pages(self):
        pdf = self.draw.scan_pdf(self.draw.contract_pages(3))
        self.assertEqual(contract_import.read("Subcontract.pdf", pdf), {"ok": False, "error": contract_import.NO_TEXT})
        entry, text = self.read("Subcontract.pdf", pdf)
        self.assertEqual((entry["state"], entry["pages"], entry["pages_read"], entry["reason"]), ("read", 3, 3, ""))
        self.assertIn("Page 3 of 3", text)
        got = contract_import.read("Subcontract.pdf", pdf, ocr_text=lambda: text)
        self.assertTrue(got["ok"])
        self.assertEqual({k: got["fields"].get(k) for k in ("contract_cents", "retainage_pct", "name")},
                         {"contract_cents": 12_500_000, "retainage_pct": 10, "name": "Elm addition"})

    def test_only_the_first_pages_are_read_when_there_are_more_than_the_cap(self):
        with mock.patch.dict(os.environ, {"OCR_MAX_PAGES": "2"}):
            entry, text = self.read("Subcontract.pdf", self.draw.scan_pdf(self.draw.contract_pages(3)))
        self.assertEqual((entry["state"], entry["pages_read"], entry["reason"]), ("read", 2, "Only the first 2 pages are read."))
        self.assertIn("Page 2 of 3", text)
        self.assertNotIn("Page 3 of 3", text)

    def test_a_phone_photo_stored_sideways_and_bigger_than_the_cap(self):
        photo = self.draw.phone_photo(self.draw.WAIVER)
        self.assertEqual(ocr._jpeg_info(photo), (4032, 3024, 3, 90))
        entry, text = self.read("IMG_0412.jpg", photo)
        self.assertEqual((entry["state"], entry["pages_read"]), ("read", 1))
        self.assertIn("CONDITIONAL WAIVER AND RELEASE", text)
        self.assertIn("Elm addition", text)

    def test_a_png_photo_within_the_cap(self):
        import io
        out = io.BytesIO()
        self.draw.page_image(self.draw.WAIVER).save(out, "PNG")
        entry, text = self.read("IMG_0413.png", out.getvalue())
        self.assertEqual(entry["state"], "read")
        self.assertIn("CONDITIONAL WAIVER AND RELEASE", text)

    def test_a_page_far_bigger_than_the_cap_is_read_scaled_down_not_at_full_size(self):
        # 2,000 x 2,000 dots saved as 50 an inch: a 40-inch page, 64 megapixels at 200 dpi.
        pdf = self.draw.scan_pdf([self.draw.page_image(self.draw.WAIVER, 2000, 2000, 40)], dpi=50)
        entry, text = self.read("Plan sheet.pdf", pdf)
        self.assertEqual((entry["state"], entry["pages_read"]), ("read", 1))
        self.assertIn("CONDITIONAL WAIVER", text)

    def test_a_file_that_is_not_what_it_says_is_unread_and_raises_nothing(self):
        for name, data in (("broken.pdf", b"%PDF-1.4\n1 0 obj\n<< /Subtype /Image >>\nstream\nnothing\nendstream\n"), ("broken.jpg", b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x10\x00\x10\x03" + b"\x00" * 30),
                           ("broken.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x08\x00\x00\x00\x00junk")):
            entry, text = self.read(name, data)
            self.assertIn(entry["state"], ("read", "unread"), name)   # a blank page is "read"; either way there is no text
            self.assertEqual(text, "", name)

    @unittest.skipUnless(sys.platform.startswith("linux"), "the address-space ceiling is enforced on Linux only")
    def test_the_memory_ceiling_stops_a_program_and_the_file_is_unread(self):
        with mock.patch.dict(os.environ, {"OCR_MEMORY_MB": "20"}):
            entry, text = self.read("Signed waiver.pdf", self.draw.scan_pdf([self.draw.page_image(self.draw.WAIVER)]))
        self.assertEqual((entry["state"], text), ("unread", ""))


if __name__ == "__main__":
    unittest.main()
