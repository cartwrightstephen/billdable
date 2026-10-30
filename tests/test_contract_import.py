"""Import a contract (Stephen's Oct 29 review, G4 and S12): a Word file, a text PDF or pasted text
fills the New job and Add client forms; a scanned PDF says it can't be read; a broken file never
raises. The fixtures in tests/fixtures/contracts were made with the standard library."""

import io
import sys
import unittest
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import contract_import

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contracts"


def read(name, company="Hartline Tile"):
    return contract_import.read(name, (FIXTURES / name).read_bytes(), company_name=company)


class ContractFileTests(unittest.TestCase):
    def test_word_file_gives_the_sum_retainage_address_and_client(self):
        r = read("subcontract.docx")
        self.assertTrue(r["ok"], r)
        f = r["fields"]
        self.assertEqual((f["contract_cents"], f["retainage_pct"], f["contract_number"]), (4_850_000, 10, "SC-2026-114"))
        self.assertEqual((f["street"], f["city"], f["state"], f["zip"]), ("418 Oak Street", "Decatur", "GA", "30030"))
        self.assertEqual((f["name"], f["customer"], f["client_type"]), ("Oak Street Kitchen", "Ironleaf General Contractors, LLC", "gc"))
        self.assertEqual((f["started_on"], f["completion_on"]), ("2026-10-05", "2026-12-18"))
        self.assertEqual((f["payapp_due_day"], f["payment_terms_days"], f["ap_email"]), (25, 30, "ap@ironleaf.example"))
        # The table under "Schedule of values" comes through the SOV paste reader.
        self.assertEqual([l["description"] for l in r["sov"]["lines"]], ["Floor tile", "Wall tile"])
        self.assertEqual(r["sov"]["total_cents"], 4_850_000)
        self.assertIn("A schedule of values with 2 lines, $48,500.00 in all.", r["notes"])

    def test_text_pdf_gives_the_same(self):
        r = read("subcontract.pdf")
        self.assertTrue(r["ok"], r)
        f = r["fields"]
        # One line is drawn as a TJ array with kerning: it still reads as one amount.
        self.assertEqual((f["contract_cents"], f["retainage_pct"]), (4_850_000, 10))
        self.assertEqual((f["street"], f["city"], f["state"], f["zip"]), ("418 Oak Street", "Decatur", "GA", "30030"))
        self.assertEqual(f["customer"], "Ironleaf General Contractors, LLC")

    def test_the_users_own_company_is_never_the_client(self):
        text = b"Contractor: Hartline Tile Co.\nOwner: Ruiz Family Trust\nThe Contract Sum is $9,000.00 for the tile work described here."
        f = contract_import.read("c.txt", text, company_name="Hartline Tile")["fields"]
        self.assertEqual((f["customer"], f["client_type"]), ("Ruiz Family Trust", "owner"))
        f = contract_import.read("c.txt", b"Owner: Fulton County Schools\nContract amount $12,000 for the gym floor and more words.", "")["fields"]
        self.assertEqual(f["client_type"], "public")

    def test_scanned_pdf_says_it_has_no_text(self):
        self.assertEqual(read("scanned.pdf"), {"ok": False, "error": "Couldn't read text from this file; fill the fields in by hand."})

    def test_broken_files_never_raise(self):
        cases = [("a.docx", b"not a zip"), ("a.pdf", b"%PDF-1.4\n1 0 obj << /Filter /FlateDecode >> stream\n\x78\x9cgarbage\nendstream"),
                 ("a.pdf", b"not a pdf"), ("a.pdf", b"%PDF-1.4\nstream\n(unclosed \\9 <ab"), ("a.pdf", b""), ("a.txt", b"\xff\xfe\x00"),
                 ("a.exe", b"MZ")]
        # A zip without the document part, and a document with an entity in it.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("other.xml", "<x/>")
        cases.append(("b.docx", buf.getvalue()))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
        cases.append(("c.docx", buf.getvalue()))
        # Text operators fed nonsense, inflated from a real stream.
        cases.append(("d.pdf", b"%PDF-1.4\n4 0 obj << /Filter /FlateDecode >>\nstream\n" + zlib.compress(b"BT ( ) ] [ <zz> TJ Td T* (\\ ET") + b"\nendstream"))
        for name, data in cases:
            r = contract_import.read(name, data)
            self.assertFalse(r["ok"], name)
            self.assertTrue(r["error"], name)
        self.assertIn("Word file", contract_import.read("a.exe", b"MZ")["error"])

    def test_pasted_text_without_the_usual_words_finds_nothing_to_fill(self):
        r = contract_import.read("pasted.txt", b"Notes from the walk-through with the owner about the tile and the grout colour.")
        self.assertTrue(r["ok"])
        self.assertEqual(r["fields"], {})


if __name__ == "__main__":
    unittest.main()
