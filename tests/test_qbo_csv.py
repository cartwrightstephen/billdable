"""Payments from a QuickBooks CSV export (P2-1, CSV half)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import qbo_csv
from seed import seed_state

EXPORT = """Hartline Remodeling
Transaction List by Date
October 1-13, 2026

Date,Transaction Type,Num,Name,Memo/Description,Amount
10/09/2026,Payment,4417,Northline GC,PA-TRN-01 approved,"$119,700.00"
10/10/2026,Payment,88213,Ponce Property Co.,,"57,950.00"
10/11/2026,Payment,9001,Someone Else,,"1,000.00"
10/11/2026,Refund,77,Ponce Property Co.,,(500.00)
,,,,TOTAL,"$178,650.00"
"""


class ParseTests(unittest.TestCase):
    def test_reads_a_report_with_title_lines(self):
        out = qbo_csv.parse(EXPORT)
        self.assertEqual([(r["date"], r["ref"], r["customer"], r["amount_cents"]) for r in out["rows"]], [
            ("2026-10-09", "4417", "Northline GC", 11_970_000),
            ("2026-10-10", "88213", "Ponce Property Co.", 5_795_000),
            ("2026-10-11", "9001", "Someone Else", 100_000),
        ])
        self.assertTrue(any("not money received" in w for w in out["warnings"]))

    def test_no_header_is_reported(self):
        self.assertIn("No header row", qbo_csv.parse("a,b\n1,2")["warnings"][0])


class ProposeAndApplyTests(unittest.TestCase):
    def test_proposals(self):
        state = seed_state()
        rows = qbo_csv.propose(state, qbo_csv.parse(EXPORT)["rows"])
        self.assertEqual([(r["status"], r["number"]) for r in rows], [("match", "PA-TRN-01"), ("match", "PA-BRK-01"), ("no match", None)])
        self.assertIn("names PA-TRN-01", rows[0]["reason"])
        self.assertIn("Same customer", rows[1]["reason"])

    def test_already_recorded_and_too_much(self):
        state = seed_state()
        trn = next(j for j in state["jobs"] if j["code"] == "TRN")
        domain.match_payment(state, trn["id"], 100_000, "4417", "owner", received_on="2026-10-09")
        self.assertEqual(trn["payments"][-1]["matched_at"], "2026-10-09T12:00:00+00:00", "dated from the books")
        rows = qbo_csv.propose(state, qbo_csv.parse(EXPORT)["rows"])
        self.assertEqual(rows[0]["status"], "already recorded")
        big = qbo_csv.propose(state, [{"line": 2, "date": None, "ref": "5", "customer": "Northline GC", "amount_cents": 20_000_000, "memo": "PA-TRN-01"}])
        self.assertEqual(big[0]["status"], "too much")


class UnfilteredReportTests(unittest.TestCase):
    """R1-10: a Transaction List that was not filtered to payments, and check numbers two customers share."""

    def test_invoice_row_is_skipped_and_payment_proposed(self):
        state = seed_state()
        trn = next(j for j in state["jobs"] if j["code"] == "TRN")
        owed = next(i for i in trn["invoices"] if i["status"] in domain.OPEN_FOR_PAYMENT)["amount_cents"]
        text = ("Date,Transaction Type,Num,Name,Memo/Description,Amount\n"
                f"10/01/2026,Invoice,1043,Northline GC,,{owed / 100:.2f}\n"
                f"10/09/2026,Payment,4417,Northline GC,,{owed / 100:.2f}\n"
                "10/09/2026,Sales Receipt,4418,Someone Else,,10.00\n"
                "10/09/2026,Deposit,4419,Someone Else,,10.00\n")
        parsed = qbo_csv.parse(text)
        self.assertEqual([r["ref"] for r in parsed["rows"]], ["4417", "4418", "4419"])
        self.assertEqual(parsed["warnings"], ["Line 2: skipped, an Invoice is not money received."])
        rows = qbo_csv.propose(state, parsed["rows"])
        self.assertEqual((rows[0]["status"], rows[0]["number"]), ("match", "PA-TRN-01"))

    def test_same_check_number_from_two_customers(self):
        state = seed_state()
        trn = next(j for j in state["jobs"] if j["code"] == "TRN")
        brk = next(j for j in state["jobs"] if j["code"] == "BRK")
        payer = {p["id"]: p["name"] for p in state["payers"]}
        domain.match_payment(state, trn["id"], 100, "1001", "owner")
        rows = qbo_csv.propose(state, [
            {"line": 2, "date": None, "ref": "1001", "customer": payer[trn["payer_id"]], "amount_cents": 100, "memo": ""},
            {"line": 3, "date": None, "ref": "1001", "customer": payer[brk["payer_id"]], "amount_cents": 100, "memo": ""},
            {"line": 4, "date": None, "ref": "1001", "customer": "Someone Else", "amount_cents": 100, "memo": ""},
        ])
        self.assertEqual(rows[0]["status"], "already recorded")
        self.assertEqual(rows[0]["reason"], f"Check 1001 from {payer[trn['payer_id']]} for $1.00 is already recorded on {trn['name']}.")
        self.assertNotEqual(rows[1]["status"], "already recorded")
        self.assertNotEqual(rows[2]["status"], "already recorded")

    def test_two_payers_sharing_check_1001_are_both_proposed(self):
        state = seed_state()
        trn = next(j for j in state["jobs"] if j["code"] == "TRN")
        brk = next(j for j in state["jobs"] if j["code"] == "BRK")
        payer = {p["id"]: p["name"] for p in state["payers"]}
        owed = {j["code"]: next(i for i in j["invoices"] if i["status"] in domain.OPEN_FOR_PAYMENT) for j in (trn, brk)}
        rows = qbo_csv.propose(state, [
            {"line": 2, "date": None, "ref": "1001", "customer": payer[trn["payer_id"]], "amount_cents": owed["TRN"]["amount_cents"], "memo": ""},
            {"line": 3, "date": None, "ref": "1001", "customer": payer[brk["payer_id"]], "amount_cents": owed["BRK"]["amount_cents"], "memo": ""},
        ])
        self.assertEqual([(r["status"], r["number"]) for r in rows], [("match", owed["TRN"]["number"]), ("match", owed["BRK"]["number"])])


ROOT = Path(__file__).resolve().parents[1]


class FinishTheImportTests(unittest.TestCase):
    """R5-03: a pay app to pick from on every row that needs one, more header spellings, and dates that warn."""

    def test_a_tie_returns_both_pay_apps_first(self):
        state = seed_state()
        brk = next(j for j in state["jobs"] if j["code"] == "BRK")
        twin = {**brk["invoices"][0], "id": "inv-brk-2", "number": "PA-BRK-02"}
        brk["invoices"].append(twin)
        payer = next(p["name"] for p in state["payers"] if p["id"] == brk["payer_id"])
        row = qbo_csv.propose(state, [{"line": 2, "date": None, "ref": "77", "customer": payer, "amount_cents": twin["amount_cents"], "memo": ""}])[0]
        self.assertEqual(row["status"], "pick one")
        self.assertIsNone(row["job_id"])
        self.assertEqual([c["number"] for c in row["candidates"][:2]], ["PA-BRK-01", "PA-BRK-02"])
        self.assertEqual(row["candidates"][0]["owed_cents"], twin["amount_cents"])
        self.assertEqual(len({c["invoice_id"] for c in row["candidates"]}), len(row["candidates"]), "each pay app once")

    def test_no_match_still_lists_open_pay_apps_and_too_much_offers_the_split(self):
        state = seed_state()
        rows = qbo_csv.propose(state, [
            {"line": 2, "date": None, "ref": "9001", "customer": "Someone Else", "amount_cents": 100_000, "memo": ""},
            {"line": 3, "date": None, "ref": "5", "customer": "Northline GC", "amount_cents": 20_000_000, "memo": "PA-TRN-01"},
        ])
        self.assertEqual(rows[0]["status"], "no match")
        self.assertTrue(rows[0]["candidates"])
        self.assertEqual(rows[1]["status"], "too much")
        self.assertIsNone(rows[1]["job_id"])
        self.assertEqual(rows[1]["split"]["number"], "PA-TRN-01")
        self.assertLess(rows[1]["split"]["owed_cents"], 20_000_000)

    def test_other_header_spellings(self):
        out = qbo_csv.parse("Client,Payment Date,Amount Paid,Check\nNorthline GC,10/09/2026,\"1,000.00\",4417\n")
        self.assertEqual([(r["customer"], r["ref"], r["amount_cents"]) for r in out["rows"]], [("Northline GC", "4417", 100_000)])
        out = qbo_csv.parse("Payer Name,Ref,Amt\nNorthline GC,X1,10\n")
        self.assertEqual(out["rows"][0]["amount_cents"], 1_000)

    def test_the_customer_date_paid_check_number_file_now_reads(self):
        out = qbo_csv.parse((ROOT / "testdata/test-contractor/bad/bad_02_payments-wrong-headers.csv").read_text())
        self.assertEqual([(r["customer"], r["ref"]) for r in out["rows"]],
                         [("Ironleaf General Contractors", "4471"), ("Corbel Street Properties", "ACH-30211")])

    def test_no_header_names_the_columns_seen(self):
        warning = qbo_csv.parse("Customer,Date,Paid out,Cheque\nIronleaf,10/15/2026,10.00,4471\n")["warnings"][0]
        self.assertTrue(warning.startswith("No header row"))
        self.assertIn("The columns seen: Customer, Date, Paid out, Cheque.", warning)

    def test_more_date_formats_and_a_warning_for_one_not_read(self):
        for raw, iso in (("10-14-2026", "2026-10-14"), ("October 14, 2026", "2026-10-14"),
                         ("Wednesday, October 14, 2026", "2026-10-14"), ("14.10.2026", "2026-10-14")):
            self.assertEqual(qbo_csv._date(raw), iso, raw)
        out = qbo_csv.parse("Date,Name,Num,Amount\n14/10/2026,Northline GC,1,10.00\n")
        self.assertIsNone(out["rows"][0]["date"])
        self.assertEqual(out["warnings"], ["Line 2: the date '14/10/2026' wasn't read; this payment will be dated today unless you fix the cell."])


REAL = Path(__file__).resolve().parents[1] / "testdata" / "quickbooks"


class RealExportTests(unittest.TestCase):  # R5-10: real exports, once someone drops them in testdata/quickbooks
    def exports(self):
        files = sorted(p for p in REAL.glob("*") if p.suffix.lower() in {".csv", ".xlsx"})
        if not files:
            self.skipTest("No real QuickBooks export in testdata/quickbooks yet (see its README).")
        return files

    def test_each_real_export_reads_as_payments(self):
        for path in self.exports():
            with self.subTest(path.name):
                text = qbo_csv.xlsx_to_csv(path.read_bytes()) if path.suffix.lower() == ".xlsx" else path.read_text(encoding="utf-8-sig")
                out = qbo_csv.parse(text)
                self.assertTrue(out["rows"], out["warnings"])
                self.assertFalse(any("No header row" in w for w in out["warnings"]), out["warnings"])
                for row in out["rows"]:
                    self.assertGreater(row["amount_cents"], 0)
                    self.assertTrue(row["date"], row)
                    self.assertTrue(row["ref"] or row["customer"], row)
                proposed = qbo_csv.propose(seed_state(), out["rows"])
                self.assertEqual(len(proposed), len(out["rows"]))


if __name__ == "__main__":
    unittest.main()
