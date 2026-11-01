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

    def test_a_contract_number_with_underscores_is_found(self):
        """Review C4: "SC_2026_014" is a contract number as GCs write them."""
        text = ("Subcontract No. SC_2026_014\nThis agreement between Acme Builders and Hartline Tile covers the tile work "
                "for the lobby. The contract sum is $48,500.00.")
        r = contract_import.read("pasted.txt", text.encode())
        self.assertEqual(r["fields"].get("contract_number"), "SC_2026_014")

    def test_a_small_crafted_pdf_is_read_quickly(self):
        """A few KB that inflate to megabytes of operators, thousands of empty streams, or one
        string of escapes: each is read in well under 2 s (review C1: the reader was quadratic)."""
        import time

        def flate(content):
            return b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n" + zlib.compress(content, 9) + b"\nendstream\nendobj\n"

        cases = [("ops", flate(b"1 0 0 1 72 720 Tm " * 90_000)),
                 ("streams", b"%PDF-1.4\n" + b"stream\nendstream" * 60_000),
                 ("escapes", flate(b"(" + b"\\0" * 3_000_000 + b") Tj")),
                 ("names", flate(b"/N " * 2_000_000))]
        self.assertLess(len(cases[0][1]), 5000)
        for label, data in cases:
            start = time.monotonic()
            r = contract_import.read("crafted.pdf", data)
            self.assertLess(time.monotonic() - start, 2.0, label)
            self.assertIn("ok", r, label)
        # The real text after a crafted part is still read when time allows.
        good = flate(b"BT (This subcontract between Acme Builders and the tile contractor sets the work, the price and the schedule.) Tj ET")
        self.assertTrue(contract_import.read("ok.pdf", good)["ok"])

    def test_a_long_run_of_gaps_is_read_quickly(self):
        """A 2 KB PDF whose one TJ array is hundreds of thousands of wide gaps (no ET): each gap
        is a space, and trimming the line ends must stay linear and inside the time limit."""
        import time
        for n in (60_000, 240_000):
            content = b"BT /F1 12 Tf 72 700 Td [" + b"-200 " * n + b"] TJ"
            data = b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n" + zlib.compress(content, 9) + b"\nendstream\nendobj\n"
            start = time.monotonic()
            r = contract_import.read("gaps.pdf", data)
            self.assertLess(time.monotonic() - start, 2.0, n)
            self.assertFalse(r["ok"])

    def test_the_text_stops_at_its_limit_inside_one_array(self):
        text = contract_import._content_text(b"[" + b"(ab) " * 5000 + b"] TJ", None, limit=100)
        self.assertLessEqual(len(text), 200)

    def test_line_ends_are_trimmed_as_before(self):
        text = contract_import._content_text(b"BT (Line one   ) Tj T* (\tLine two\t) Tj T* T* T* T* (three) Tj ET")
        self.assertEqual(text, "Line one\n\tLine two\n\nthree\n")

    def test_adversarial_pasted_text_is_read_quickly(self):
        """Every pattern the reader looks with stays linear: long runs of spaces, line ends,
        letters, digits or repeated key words in pasted text never keep the server busy."""
        import time
        n = 200_000
        cases = {
            "spaces": "Contractor: AB" + " " * n,
            "newlines": "\n" * n + "x",
            "tabs": "Project:" + "\t" * n,
            "letters": "invoices " + "a" * n,
            "keywords": "invoices" * (n // 8),
            "keywords spaced": "invoices " * (n // 9),
            "capitals": "AB " + " " * n + "x",
            "capital newlines": ("A\n" * (n // 2)),
            "digits": "1" * n,
            "digit spaces": "1 " * (n // 2),
            "commas": "A," * (n // 2),
            "dollars": "contract sum " + "$1" * (n // 2),
            "emails": "billing " + "a." * (n // 2),
            "schedule": "Schedule of values\n" + "a " * n,
        }
        for label, text in cases.items():
            start = time.monotonic()
            contract_import.read("pasted.txt", text.encode())
            self.assertLess(time.monotonic() - start, 2.0, label)

    def test_pasted_text_without_the_usual_words_finds_nothing_to_fill(self):
        r = contract_import.read("pasted.txt", b"Notes from the walk-through with the owner about the tile and the grout colour.")
        self.assertTrue(r["ok"])
        self.assertEqual(r["fields"], {})


if __name__ == "__main__":
    unittest.main()
