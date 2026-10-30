"""The test contractor's import files, one test each (R5-10).

testdata/test-contractor/MANUAL-TEST.md part 3 walks a person through every file in import/
and bad/. These tests do the same through the HTTP API, against Brightwater Tile & Stone as
scripts/load_test_contractor.py enters it (its setup() runs once, in-process). The tests are
numbered after the MANUAL-TEST steps and run in that order, because step 3.18 records payments
that 3.19 and 3.25 then see as already recorded.
"""

import contextlib
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import auth
import helper
import importer
import load_test_contractor as loader
import server
import sov
import storage

IMPORT, BAD = loader.IMPORT, loader.BAD


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH)
        auth.AUTH_PATH = root / "auth.json"
        server.COMPANIES = root / "companies"
        importer.UPLOADS = root / "uploads"
        storage.DATA_DIR = root
        helper.LOCAL_KB = root / "helper_answers.json"
        helper.LOG_PATH = root / "helper_log.json"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.run_ = loader.Run(loader.Api(f"http://127.0.0.1:{cls.httpd.server_address[1]}"))
        # Signed up here rather than with loader.signup, which writes testdata/test-contractor/.login.
        status, _ = cls.run_.api.call("POST", "/api/auth/signup", {
            "name": loader.OWNER_NAME, "company_name": loader.COMPANY["name"], "email": "nadia@brightwatertile.example",
            "password": "fixture test password", "accept_terms": True})
        assert status == 200, status
        with contextlib.redirect_stdout(io.StringIO()):
            loader.setup(cls.run_)
        failed = [r for r in cls.run_.results if not r[1]]
        assert not failed, failed

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH) = cls._orig
        cls.tmp.cleanup()

    # ----- helpers -----

    def code(self, job_id):
        return self.run_.code_of(job_id)

    def upload(self, path, query=""):
        before = {i["id"] for i in self.run_.state().get("inbox", [])}
        status, payload = loader.upload(self.run_, path, query)
        return status, payload, (loader.new_items(before, payload) if status == 200 else [])

    def upload_one(self, name):
        status, payload, items = self.upload(IMPORT / name)
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(items), 1)
        return items[0], payload

    def file_names(self, item, state):
        names = {f["id"]: f["name"] for f in state.get("files", [])}
        return sorted(names.get(i, "?") for i in item.get("file_ids", []))

    def paste(self, name, source):
        before = {i["id"] for i in self.run_.state().get("inbox", [])}
        state = self.run_.post(name, "/api/import/text", {"text": (IMPORT / name).read_text(), "source": source}, quiet=True)
        return loader.new_items(before, state)

    def call(self, method, path, body=None):
        return self.run_.api.call(method, path, body)

    def sov_total(self, parsed):
        base = sum(sov.extend(sov.to_milli(l["quantity"]), sov.to_cents(l["rate"])) for l in parsed["lines"])
        return base + sov.markup(base, sov.to_bp(parsed["op_pct"] or "0"))

    # ----- 3a. Files into the Inbox -----

    def test_3_01_change_order_email_from_the_gc_super(self):
        item, _ = self.upload_one("05_email_co-request-from-gc-super.eml")
        self.assertEqual((self.code(item["suggested_job_id"]), item["kind"]), ("BROO", "change_order"))
        self.assertIn("job name", item["match_reason"])
        self.assertIn("Travis Mendez", item["from"])
        self.assertIn(234_000, item["amounts"])

    def test_3_02_signed_waiver_with_its_pdf(self):
        item, state = self.upload_one("06_email_signed-waiver-pdf-attached.eml")
        self.assertEqual((self.code(item["suggested_job_id"]), item["kind"]), ("DECA", "waiver"))
        self.assertIn("PA-DECA", item["match_reason"])
        self.assertEqual(self.file_names(item, state), ["06_email_signed-waiver-pdf-attached.eml", "Conditional-Waiver-PA-DECA-01-signed.pdf"])

    def test_3_03_payer_says_backup_is_missing(self):
        item, _ = self.upload_one("07_email_payer-says-backup-missing.eml")
        self.assertEqual((self.code(item["suggested_job_id"]), item["kind"]), ("VEST", "pay_app"))
        self.assertIn("PA-VEST", item["match_reason"])

    def test_3_04_foreman_photo_email(self):
        item, state = self.upload_one("08_email_photo-attachment-from-foreman.eml")
        self.assertEqual((self.code(item["suggested_job_id"]), item["kind"]), ("MILL", "photo"))
        self.assertIn("MILL", item["match_reason"])
        self.assertEqual(self.file_names(item, state), ["08_email_photo-attachment-from-foreman.eml", "IMG_2240.jpg"])

    def test_3_05_email_that_matches_no_job(self):
        item, _ = self.upload_one("09_email_matches-no-job.eml")
        self.assertIsNone(item["suggested_job_id"])
        self.assertEqual(item["kind"], "note")
        self.assertEqual(item["match_reason"], "No job name, code, address or client found.")
        self.assertEqual(sorted(item["amounts"]), [1875, 2410])

    def test_3_06_portal_notice_in_html_only(self):
        item, _ = self.upload_one("10_email_portal-notice-html-only.eml")
        self.assertEqual((self.code(item["suggested_job_id"]), item["kind"]), ("ASHB", "pay_app"))
        self.assertIn("PA-ASHB", item["match_reason"])
        self.assertTrue(item["text"].strip())
        self.assertNotIn("<", item["text"])

    def test_3_07_scanned_subcontract_named_by_the_scanner(self):
        item, _ = self.upload_one("13_Scan_kenn_subcontract.pdf")
        self.assertEqual((self.code(item["suggested_job_id"]), item["kind"]), ("KENN", "contract"))
        self.assertIn("KENN", item["match_reason"])

    def test_3_08_phone_photo_with_the_code_in_its_name(self):
        item, _ = self.upload_one("14_IMG_2231_MILL_lobby-floor.jpg")
        self.assertEqual((self.code(item["suggested_job_id"]), item["kind"]), ("MILL", "photo"))
        self.assertIn("MILL", item["match_reason"])

    def test_3_08a_payments_export_dropped_in_the_inbox(self):
        item, _ = self.upload_one("01_qbo-payments_partial-nomatch-odd-formats.csv")
        self.assertIsNone(item["suggested_job_id"])
        # Its rows name pay apps (PA-VEST-01), so the kind is pay app backup; it is read on Payments, not filed.
        self.assertEqual((item["kind"], item.get("payments_csv"), item.get("payment_rows")), ("pay_app", True, 6))
        status, read = self.call("POST", "/api/import/payments-csv", {"file_id": item["file_ids"][0]})
        self.assertEqual((status, len(read["rows"])), (200, 6))
        self.run_.post("dismiss", f"/api/inbox/{item['id']}/dismiss", {}, quiet=True)

    def test_3_08b_uploaded_for_a_job_picked_first(self):
        status, _, items = self.upload(IMPORT / "14_IMG_2231_MILL_lobby-floor.jpg", f"?job={self.run_.jobs['GLEN']}")
        self.assertEqual(status, 200)
        self.assertEqual(self.code(items[0]["suggested_job_id"]), "GLEN")
        self.assertEqual(items[0]["status"], "new")
        self.run_.post("dismiss", f"/api/inbox/{items[0]['id']}/dismiss", {}, quiet=True)

    def test_3_11_subcontract_dropped_on_the_job_record(self):
        status, payload, items = self.upload(IMPORT / "13_Scan_kenn_subcontract.pdf", f"?job={self.run_.jobs['KENN']}&file=1")
        self.assertEqual(status, 200, payload)
        self.assertEqual([i["status"] for i in items], ["filed"])
        records = self.run_.job("KENN")["records"]
        self.assertTrue(any(r["kind"] == "contract" and r.get("file_ids") for r in records))

    # ----- 3b. Pasted text -----

    def test_3_12_text_thread_says_whitfield_is_done(self):
        items = self.paste("11_text-thread_whitfield-done.txt", "sms")
        self.assertEqual(len(items), 1)
        self.assertEqual(self.code(items[0]["suggested_job_id"]), "WHIT")
        self.assertEqual(self.code(items[0].get("says_done_job_id")), "WHIT")

    def test_3_13_field_notes_split_into_daily_records(self):
        items = self.paste("12_field-notes_split-into-daily-records.txt", "notes")
        got = [(i.get("note_date"), self.code(i.get("suggested_job_id"))) for i in items]
        self.assertEqual(got, loader.EXPECTED_NOTES)
        self.assertTrue(all(i["kind"] == "daily" for i in items))
        # "3/4 inch" and "May 2 guys" stay inside their day's record.
        self.assertIn("3/4 inch", items[1]["text"])
        self.assertIn("May 2 guys", items[4]["text"])

    def test_3_15_pasted_email_from_wrenfield(self):
        items = self.paste("15_pasted-email_wrenfield-punch-list.txt", "email")
        self.assertEqual(len(items), 1)
        self.assertEqual(self.code(items[0]["suggested_job_id"]), "DECA")
        self.assertIn("ap@wrenfieldcg.example", items[0]["from"])
        self.assertEqual(items[0]["subject"], "Decatur Library Annex - punch list before retainage release")

    def test_3_16_inbound_email_says_glenwood_is_finished(self):
        _, settings = self.call("GET", "/api/import/settings")
        path = settings["email_webhook"].split("://", 1)[-1]
        path = path[path.index("/"):]
        before = {i["id"] for i in self.run_.state()["inbox"]}
        cookie, self.run_.api.cookie = self.run_.api.cookie, ""  # the mail provider has no session
        try:
            status, _ = self.run_.api.call("POST", path, raw=(IMPORT / "16_inbound-webhook_crew-says-glenwood-finished.eml").read_bytes(),
                                           headers={"Content-Type": "message/rfc822"})
        finally:
            self.run_.api.cookie = cookie
        self.assertEqual(status, 200)
        items = loader.new_items(before, self.run_.state())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "email")
        self.assertIn("Luis Ortega", items[0]["from"])
        self.assertEqual(self.code(items[0]["suggested_job_id"]), "GLEN")
        self.assertEqual(self.code(items[0].get("says_done_job_id")), "GLEN")

    # ----- 3c. QuickBooks payments CSV -----

    def test_3_17_to_3_19_payments_export_read_recorded_and_read_again(self):
        text = (IMPORT / "01_qbo-payments_partial-nomatch-odd-formats.csv").read_text()
        status, proposal = self.call("POST", "/api/import/payments-csv", {"csv": text})
        self.assertEqual(status, 200)
        self.assertEqual(proposal["warnings"], [
            "Line 12: skipped, a Refund is not money received.",
            "Line 13: skipped, the amount 'pending' isn't a number.",
            "Line 14: skipped, an Invoice is not money received.",
        ])
        rows = {r["ref"]: r for r in proposal["rows"]}
        self.assertEqual(list(rows), list(loader.EXPECTED_CSV))
        for ref, (word, code) in loader.EXPECTED_CSV.items():
            self.assertEqual((rows[ref]["status"], self.code(rows[ref].get("job_id"))), (word, code), ref)
        self.assertEqual([rows[r]["date"] for r in rows],
                         ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17", "2026-10-20", "2026-10-21"])
        self.assertEqual(rows["9902"]["split"]["owed_cents"], 3_780_000)
        self.assertTrue(rows["2210"]["candidates"])
        # 3.18: record the three matched rows.
        picks = [{"job_id": r["job_id"], "invoice_id": r["invoice_id"], "amount_cents": r["amount_cents"], "reference": r["ref"],
                  "date": r["date"] or ""} for r in proposal["rows"] if r["status"] == "match"]
        status, applied = self.call("POST", "/api/import/payments-apply", {"payments": picks})
        self.assertEqual(status, 200)
        self.assertEqual([r["ok"] for r in applied["import_results"]], [True, True, True])
        for code, word in (("ASHB", "paid"), ("WHIT", "paid"), ("GLEN", "partial")):
            live = [i for i in self.run_.job(code)["invoices"] if i["status"] != "void"]
            self.assertEqual(live[-1]["status"], word, code)
        self.assertEqual(self.run_.job("GLEN")["money"]["outstanding_cents"], 125_000)
        # 3.19: read again.
        _, again = self.call("POST", "/api/import/payments-csv", {"csv": text})
        recorded = {r["ref"] for r in again["rows"] if r["status"] == "already recorded"}
        self.assertEqual(recorded, {"ACH-30211", "4471", "1187", "CHK 40871"})

    # ----- 3d. Schedule of values paste -----

    def sov_parse(self, path):
        status, parsed = self.call("POST", "/api/sov/parse", {"text": path.read_text()})
        self.assertEqual(status, 200)
        return parsed

    def test_3_20_excel_rows_with_a_header(self):
        parsed = self.sov_parse(IMPORT / "02_sov_MILL_excel-tab-with-header.txt")
        self.assertEqual((len(parsed["lines"]), parsed["op_pct"]), (5, "10"))
        self.assertEqual(parsed["warnings"], [
            "Row 7 (Overhead and profit 10%) read as overhead and profit of 10%; check it below.",
            "Row 8 (Total) looks like a total or markup row and was skipped.",
        ])
        self.assertEqual(self.sov_total(parsed), 8_030_000)

    def test_3_21_a_schedule_that_differs_from_the_contract_is_refused(self):
        parsed = self.sov_parse(IMPORT / "02_sov_MILL_excel-tab-with-header.txt")
        status, out = self.call("POST", f"/api/jobs/{self.run_.jobs['KENN']}/sov",
                                {"lines": parsed["lines"], "op_pct": parsed["op_pct"], "update_contract": False})
        self.assertEqual(status, 409)
        self.assertIn("The schedule totals $80,300.00, but the contract is $92,400.00.", str(out))

    def test_3_22_pdf_copy_and_typed_commas(self):
        ashb = self.sov_parse(IMPORT / "03_sov_ASHB_pdf-copy-spaces.txt")
        self.assertEqual(len(ashb["lines"]), 4)
        self.assertTrue(ashb["warnings"][0].endswith("has no numbers; skipped."))
        self.assertTrue(any("(Total) looks like a total" in w for w in ashb["warnings"]))
        self.assertEqual(self.sov_total(ashb), 7_485_000)
        broo = self.sov_parse(IMPORT / "04_sov_BROO_typed-commas-headerless.txt")
        self.assertEqual(len(broo["lines"]), 4)
        tubs = next(l for l in broo["lines"] if l["description"] == "Tub surrounds")
        self.assertEqual((tubs["quantity"], tubs["unit"], tubs["rate"]), ("18", "ea", "1150.00"))
        self.assertEqual((broo["op_pct"], self.sov_total(broo)), ("10", 4_606_800))

    def test_3_22a_a_title_above_the_header(self):  # R5-08
        text = "Brightwater Tile & Stone - Schedule of values\n" + (IMPORT / "02_sov_MILL_excel-tab-with-header.txt").read_text()
        _, parsed = self.call("POST", "/api/sov/parse", {"text": text})
        self.assertTrue(parsed["header"])
        self.assertEqual(self.sov_total(parsed), 8_030_000)
        self.assertEqual(parsed["warnings"][0], "Row 1 (Brightwater Tile & Stone - Schedule of values) skipped: text above the header.")

    # ----- 3e. Bad files -----

    def test_3_23_an_html_page_is_refused(self):
        status, payload, _ = self.upload(BAD / "bad_01_page.html")
        self.assertEqual(status, 409)
        self.assertTrue(payload["error"].startswith("bad_01_page.html: this file type can't be imported. Allowed: csv, doc, docx, eml,"))

    def test_3_24_an_empty_file_is_refused(self):
        status, payload, _ = self.upload(BAD / "bad_03_empty-file.txt")
        self.assertEqual((status, payload["error"]), (409, "bad_03_empty-file.txt is empty."))

    def test_3_25_payments_file_with_other_headers(self):
        text = (BAD / "bad_02_payments-wrong-headers.csv").read_text()
        _, out = self.call("POST", "/api/import/payments-csv", {"csv": text})
        self.assertEqual([(r["ref"], r["status"]) for r in out["rows"]], [("4471", "already recorded"), ("ACH-30211", "already recorded")])
        first, rest = text.split("\n", 1)
        _, out = self.call("POST", "/api/import/payments-csv", {"csv": "Customer,Date,Paid out,Cheque\n" + rest})
        self.assertEqual(out["rows"], [])
        self.assertIn("The columns seen: Customer, Date, Paid out, Cheque.", " ".join(out["warnings"]))

    def test_3_26_sov_rows_with_a_quantity_and_no_rate(self):
        # The rows come in with the rate blank and needs_price; nothing is priced for them, and the
        # server refuses to save until each has a rate (Oct 23 note 9, the same with or without a header).
        parsed = self.sov_parse(BAD / "bad_04_sov-quantity-without-rate.txt")
        self.assertEqual([(l["description"], l["quantity"], l["rate"], l["needs_price"]) for l in parsed["lines"]],
                         [("Rough-in prep", "40", "", True), ("Wall tile", "1450", "", True), ("Thresholds", "12", "", True)])
        self.assertEqual(parsed["warnings"], [
            "Row 2 (Rough-in prep): 40 has no price. Enter the unit rate before you save.",
            "Row 3 (Wall tile): 1,450 has no price. Enter the unit rate before you save.",
            "Row 4 (Thresholds): 12 has no price. Enter the unit rate before you save.",
        ])

    def test_3_27_email_with_a_zip_keeps_the_pdf(self):
        status, payload, items = self.upload(BAD / "bad_05_email-with-zip-attachment.eml")
        self.assertEqual(status, 200, payload)
        item = items[0]
        self.assertEqual(self.code(item["suggested_job_id"]), "ASHB")
        self.assertEqual(len(item["file_ids"]), 2)
        self.assertTrue(any(s.startswith("Ashby-paver-drawings-rev3.zip: this file type can't be imported.") for s in item["skipped_attachments"]))


if __name__ == "__main__":
    unittest.main()
