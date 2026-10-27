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


if __name__ == "__main__":
    unittest.main()
