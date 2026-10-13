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


if __name__ == "__main__":
    unittest.main()
