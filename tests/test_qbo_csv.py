"""Payments from a QuickBooks CSV export (P2-1, CSV half)."""

import sys
import io
import tracemalloc
import zipfile
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
        self.assertIn("Same client", rows[1]["reason"])

    def test_already_recorded_and_too_much(self):
        state = seed_state()
        trn = next(j for j in state["jobs"] if j["code"] == "TRN")
        domain.match_payment(state, trn["id"], 100_000, "4417", "owner", received_on="2026-10-09")
        self.assertEqual(trn["payments"][-1]["matched_at"], "2026-10-09T12:00:00+00:00", "dated from the books")
        rows = qbo_csv.propose(state, [{"line": 7, "date": "2026-10-09", "ref": "4417", "customer": "Northline GC", "amount_cents": 100_000, "memo": ""}])
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



class BlankNumberTests(unittest.TestCase):
    """A payment with no Num (an ACH, say) imported twice is recorded once."""

    TEXT = 'Date,Transaction Type,Num,Customer,Memo,Amount\n10/09/2026,Payment,,Northline GC,PA-TRN-01 part,"1,000.00"\n'

    def record(self, state):
        rows = qbo_csv.propose(state, qbo_csv.parse(self.TEXT)["rows"])
        for r in rows:
            if r["status"] == "match":
                domain.match_payment(state, r["job_id"], r["amount_cents"], r["ref"], "owner", invoice_id=r["invoice_id"], received_on=r["date"])
        return rows

    def test_importing_the_same_file_twice_records_it_once(self):
        state = seed_state()
        self.assertEqual(self.record(state)[0]["status"], "match")
        again = self.record(state)
        self.assertEqual(again[0]["status"], "already recorded")
        self.assertIn("Northline GC", again[0]["reason"])
        trn = next(j for j in state["jobs"] if j["id"] == "job-turner")
        self.assertEqual([p["amount_cents"] for p in trn["payments"]], [100_000])

    def test_another_day_or_amount_is_a_new_payment(self):
        state = seed_state()
        self.record(state)
        # A day apart may be the same payment keyed in by hand: not ticked, but it can still be picked.
        other = qbo_csv.propose(state, qbo_csv.parse(self.TEXT.replace("10/09/2026", "10/10/2026"))["rows"])
        self.assertEqual(other[0]["status"], "no match")
        self.assertIn("PA-TRN-01", [c["number"] for c in other[0]["candidates"]])
        other = qbo_csv.propose(state, qbo_csv.parse(self.TEXT.replace("10/09/2026", "10/30/2026"))["rows"])
        self.assertEqual(other[0]["status"], "match")
        other = qbo_csv.propose(state, qbo_csv.parse(self.TEXT.replace('"1,000.00"', '"1,000.01"'))["rows"])
        self.assertEqual(other[0]["status"], "match")


class CustomerNameTests(unittest.TestCase):
    """A customer is the same one when its whole words are in the other name, not any run of letters."""

    def test_whole_words_only(self):
        self.assertFalse(qbo_csv._same("grace roofing", "ace roofing"))
        self.assertFalse(qbo_csv._same("slab co", "ab"))
        self.assertTrue(qbo_csv._same("northline gc inc", "northline gc"))
        self.assertTrue(qbo_csv._same("northline gc", "northline gc"))

    def test_another_customer_with_the_same_check_number_is_not_already_recorded(self):
        state = seed_state()
        state["payers"].append({"id": "payer-ace", "name": "Ace Roofing"})
        trn = next(j for j in state["jobs"] if j["id"] == "job-turner")
        trn["payer_id"] = "payer-ace"
        domain.match_payment(state, trn["id"], 100, "1001", "owner")
        owed = 11_970_000 - 100
        rows = qbo_csv.propose(state, [{"line": 2, "date": None, "ref": "1001", "customer": "Grace Roofing", "amount_cents": 420_000, "memo": ""},
                                       {"line": 3, "date": None, "ref": "77", "customer": "Grace Roofing", "amount_cents": owed, "memo": ""}])
        self.assertNotEqual(rows[0]["status"], "already recorded")
        self.assertEqual(rows[1]["status"], "no match", "Ace's pay app isn't proposed for Grace's payment")

    def test_reconcile_pairs_by_whole_words(self):
        import qbo
        state = seed_state()
        brk = next(j for j in state["jobs"] if j["id"] == "job-brook")
        inv = brk["invoices"][0]
        inv["number"] = "PA-X-01"   # no number in common: customer and amount decide
        state["payers"] = [p if p["id"] != brk["payer_id"] else {**p, "name": "Ace Roofing"} for p in state["payers"]]
        qb = [{"Id": "1", "DocNumber": "G-9", "TotalAmt": inv["amount_cents"] / 100, "Balance": 0, "CustomerRef": {"name": "Grace Roofing"}}]
        rows = qbo.reconcile(state, qb)
        self.assertIn(("PA-X-01", "missing"), {(r["number"], r["kind"]) for r in rows})


def _sheet_zip(cells_xml, styles=False):
    """A minimal Excel file with this sheetData, and a date style s="1" when asked."""
    import io
    import zipfile
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{cells_xml}</sheetData></worksheet>')
        if styles:
            z.writestr("xl/styles.xml", f'<styleSheet {ns}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
    return buf.getvalue()


def _peak(fn):
    tracemalloc.start()
    try:
        out = fn()
        return out, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _xl_zip(parts):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, xml in parts.items():
            z.writestr(name, xml)
    return buf.getvalue()


NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
MB = 1024 * 1024


class XlsxLimitsTests(unittest.TestCase):
    """A small file that inflates to a great deal: no tree is built, whatever is inside."""

    def small_memory(self, data, most=8):
        text, used = _peak(lambda: qbo_csv.xlsx_to_csv(data))
        self.assertLess(used, most * MB)
        return text

    def test_empty_cells_stay_small(self):
        cells = "<c/>" * ((qbo_csv.MAX_XLSX_XML - 200) // 4)
        data = _xl_zip({"xl/worksheets/sheet1.xml": f"<worksheet {NS}><sheetData><row>{cells}</row></sheetData></worksheet>"})
        self.assertLess(len(data), 40_000)
        self.assertEqual(self.small_memory(data), "")

    def test_each_part_is_bounded(self):
        for part in ("xl/sharedStrings.xml", "xl/styles.xml"):
            xml = f"<x {NS}>" + "<si/>" * ((qbo_csv.MAX_XLSX_XML - 200) // 5) + "</x>"
            data = _xl_zip({part: xml, "xl/worksheets/sheet1.xml": f'<worksheet {NS}><row><c r="A1"><v>1</v></c></row></worksheet>'})
            self.assertEqual(self.small_memory(data), "", part)

    def test_past_the_size_cap_is_refused_unread(self):
        data = _xl_zip({"xl/worksheets/sheet1.xml": f"<worksheet {NS}>" + " " * (qbo_csv.MAX_XLSX_XML + 1) + "</worksheet>"})
        self.assertEqual(self.small_memory(data), "")

    def test_deep_nesting_is_refused(self):
        depth = qbo_csv.MAX_XLSX_DEPTH + 5
        data = _xl_zip({"xl/worksheets/sheet1.xml": f"<worksheet {NS}>" + "<a>" * depth + "</a>" * depth + "</worksheet>"})
        self.assertEqual(self.small_memory(data), "")

    def test_one_long_string_in_every_cell_stops_at_the_text_limit(self):
        long = "x" * 30_000
        cells = "".join(f'<c r="{chr(65 + c)}{{r}}" t="s"><v>0</v></c>' for c in range(26))
        rows = "".join(f'<row r="{r}">{cells.format(r=r)}</row>' for r in range(1, 2000))
        data = _xl_zip({"xl/sharedStrings.xml": f"<sst {NS}><si><t>{long}</t></si></sst>", "xl/worksheets/sheet1.xml": f"<worksheet {NS}>{rows}</worksheet>"})
        self.assertEqual(self.small_memory(data, most=30), "")

    def test_a_doctype_is_refused(self):
        for xml in (f'<!DOCTYPE w [<!ENTITY a "aaaa">]><worksheet {NS}><row><c r="A1" t="inlineStr"><is><t>&a;</t></is></c></row></worksheet>',):
            self.assertEqual(self.small_memory(_xl_zip({"xl/worksheets/sheet1.xml": xml})), "")

    def test_a_stuffed_zip_directory_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {NS}><row><c r="A1"><v>1</v></c></row></worksheet>')
            for n in range(25_000):
                z.writestr(f"x/{'y' * 20}{n}", "")
        self.assertEqual(self.small_memory(buf.getvalue(), most=20), "")

    def test_damaged_files_give_nothing_and_no_exception(self):
        good = _sheet_zip('<row r="1"><c r="A1"><v>1</v></c></row>')
        self.assertEqual(qbo_csv.xlsx_to_csv(good), "1\n")
        self.assertEqual(qbo_csv.xlsx_to_csv(good[:len(good) // 2]), "")
        self.assertEqual(qbo_csv.xlsx_to_csv(_xl_zip({"xl/worksheets/sheet1.xml": "<not xml"})), "")
        at = good.find(b"PK\x01\x02")
        self.assertEqual(qbo_csv.xlsx_to_csv(good[:40] + bytes(b ^ 0xFF for b in good[40:at]) + good[at:]), "")

    def test_a_normal_sheet_at_the_row_limit_reads_as_before(self):
        strings = "".join(f"<si><t>Customer {n} &amp; Sons</t></si>" for n in range(3000))
        rows = "".join(f'<row r="{r}"><c r="A{r}" s="1"><v>46309</v></c><c r="B{r}" t="s"><v>{r}</v></c><c r="C{r}"><v>{r}.5</v></c>'
                       f'<c r="E{r}" t="inlineStr"><is><r><t>rich </t></r><r><t>text</t></r></is></c></row>' for r in range(1, 3000))
        data = _xl_zip({"xl/sharedStrings.xml": f"<sst {NS}>{strings}</sst>", "xl/worksheets/sheet1.xml": f"<worksheet {NS}><sheetData>{rows}</sheetData></worksheet>",
                        "xl/styles.xml": f'<styleSheet {NS}><numFmts><numFmt numFmtId="170" formatCode="d/m/yy"/></numFmts><cellFormats><xf numFmtId="14"/></cellFormats>'
                                         f'<cellXfs><xf numFmtId="0"><alignment/></xf><xf numFmtId="170"/></cellXfs></styleSheet>'})
        lines = self.small_memory(data).splitlines()
        self.assertEqual(lines[0], '10/14/2026,Customer 1 & Sons,1.5,,rich text')
        self.assertEqual(len(lines), qbo_csv.MAX_ROWS + 21, "the rows past the limit are left, as before")

    def test_rounding_and_the_first_sheet_by_name_still_work(self):
        data = _xl_zip({"xl/worksheets/sheet2.xml": f'<worksheet {NS}><row><c r="B1"><v>0.30000000000000004</v></c></row></worksheet>'})
        self.assertEqual(qbo_csv.xlsx_to_csv(data, digits=3), ",0.3\n")


class UnsafeFilesTests(unittest.TestCase):
    def test_a_cell_far_to_the_right_does_not_blow_up_the_row(self):
        # One cell at column ZZZZ used to make a 475,000-character row (ZZZZZZZZ: billions, out of memory).
        for ref in ("ZZZZ", "ZZZZZZZZ"):
            data = _sheet_zip(f'<row r="1"><c r="A1" t="inlineStr"><is><t>Amount</t></is></c>'
                              f'<c r="{ref}1" t="inlineStr"><is><t>x</t></is></c></row>')
            text = qbo_csv.xlsx_to_csv(data)
            self.assertLess(len(text), 2_000, ref)
            self.assertTrue(text.startswith("Amount"), ref)

    def test_a_date_cell_out_of_range_keeps_its_number(self):
        data = _sheet_zip('<row r="1"><c r="A1" s="1"><v>46309</v></c><c r="B1" s="1"><v>' + "9" * 400 + '</v></c>'
                          '<c r="C1" s="1"><v>99999999999</v></c></row>', styles=True)
        self.assertEqual(qbo_csv.xlsx_to_csv(data), f"10/14/2026,{'9' * 400},99999999999\n")

    def test_a_nul_byte_or_a_huge_cell_is_a_warning_not_a_crash(self):
        for text in ("Date,Num,Customer,Amount\n10/09/2026,4417,Park\x00 GC,100.00\n",
                     'Date,Num,Customer,Amount\n10/09/2026,4417,"' + "x" * 140_000 + '",100.00\n'):
            parsed = qbo_csv.parse(text)
            self.assertEqual(parsed["rows"], [])
            self.assertIn("couldn't be read", parsed["warnings"][0])
            self.assertEqual(qbo_csv.looks_like_payments(text), 0)


def _row(ref, amount, day, customer="Northline GC", memo="", **more):
    return {"line": 2, "date": day, "ref": ref, "customer": customer, "amount_cents": amount, "memo": memo, **more}


def _qbo_row(state, pid, amount, day, ref=None, linked="PA-TRN-01"):
    import qbo
    payment = {"Id": pid, "TxnDate": day, "TotalAmt": amount / 100, "CustomerRef": {"name": "Northline GC"},
               "Line": [{"LinkedTxn": [{"TxnId": "10", "TxnType": "Invoice"}]}]}
    if ref:
        payment["PaymentRefNum"] = ref
    return qbo.payment_rows([payment], [{"Id": "10", "DocNumber": linked}])[0]


class NoNumberAlreadyRecordedTests(unittest.TestCase):
    """A payment recorded with no number (by hand, or from a blank-Num row) is not offered again
    when QuickBooks is read, or when the CSV dates it a few days apart."""

    def test_a_quickbooks_payment_with_no_number_is_already_recorded_by_hand(self):
        import qbo
        state = seed_state()
        domain.match_payment(state, "job-turner", 100_000, "", "owner", received_on="2026-10-09")
        row = _qbo_row(state, "55", 100_000, "2026-10-09")
        self.assertEqual(qbo.propose(state, [row])[0]["status"], "already recorded")
        # Keyed in two days after the books date: likely the same payment, so it is not ticked,
        # and the pay apps stay to pick from in case it is another one.
        state = seed_state()
        domain.match_payment(state, "job-turner", 100_000, "", "owner", received_on="2026-10-11")
        for got in (qbo.propose(state, [row])[0], qbo_csv.propose(state, [_row("", 100_000, "2026-10-09", memo="PA-TRN-01")])[0]):
            self.assertEqual((got["status"], got["job_id"]), ("no match", None))
            self.assertIn("dated 2026-10-11 is already recorded on", got["reason"])
            self.assertIn("PA-TRN-01", [c["number"] for c in got["candidates"]])
        self.assertEqual(qbo_csv.propose(state, [_row("", 100_000, "2026-10-30", memo="PA-TRN-01")])[0]["status"], "match", "weeks apart")

    def test_a_paid_draw_is_not_matched_to_the_next_one_of_the_same_amount(self):
        import qbo
        state = seed_state()
        trn = next(j for j in state["jobs"] if j["id"] == "job-turner")
        trn["invoices"].append({**trn["invoices"][0], "id": "inv-trn-2", "number": "PA-TRN-02"})
        domain.match_payment(state, "job-turner", 11_970_000, "", "owner", invoice_id=trn["invoices"][0]["id"], received_on="2026-10-09")
        october = _qbo_row(state, "55", 11_970_000, "2026-10-09", linked="")
        november = _qbo_row(state, "56", 11_970_000, "2026-11-09", linked="")
        rows = qbo.propose(state, [october, november])
        self.assertEqual(rows[0]["status"], "already recorded")
        self.assertEqual((rows[1]["status"], rows[1]["number"]), ("match", "PA-TRN-02"))

    def test_one_recorded_payment_stands_for_one_row(self):
        state = seed_state()
        domain.match_payment(state, "job-turner", 100_000, "", "owner", received_on="2026-10-09")
        rows = qbo_csv.propose(state, [_row("", 100_000, "2026-10-09", memo="PA-TRN-01"), _row("", 100_000, "2026-10-09", memo="PA-TRN-01")])
        self.assertEqual([r["status"] for r in rows], ["already recorded", "match"])

    def test_a_split_payment_with_no_number_is_not_offered_again(self):
        state = seed_state()
        owed = 11_970_000
        row = _row("", owed + 50_000, "2026-10-09", memo="PA-TRN-01")
        self.assertEqual(qbo_csv.propose(state, [row])[0]["status"], "too much")
        domain.match_payment(state, "job-turner", owed, "", "owner", received_on="2026-10-09")
        domain.match_payment(state, "job-brook", 50_000, "", "owner", received_on="2026-10-09")
        self.assertEqual(qbo_csv.propose(state, [row])[0]["status"], "already recorded")

    def test_a_split_payment_with_a_number_is_not_offered_again(self):
        state = seed_state()
        owed = 11_970_000
        row = _row("1001", owed + 50_000, "2026-10-09", memo="PA-TRN-01")
        domain.match_payment(state, "job-turner", owed, "1001", "owner", received_on="2026-10-09")
        domain.match_payment(state, "job-brook", 50_000, "1001 (rest)", "owner", received_on="2026-10-09")
        self.assertEqual(qbo_csv.propose(state, [row])[0]["status"], "already recorded")


    def test_rows_recorded_one_by_one_are_not_taken_as_a_split(self):
        # $1,000 from Northline and $500 from Ponce were recorded by hand. A $1,500 Northline row
        # read first must not take both as a split and leave the two real rows to be recorded again.
        state = seed_state()
        domain.match_payment(state, "job-turner", 100_000, "", "owner", received_on="2026-10-09")
        domain.match_payment(state, "job-brook", 50_000, "", "owner", received_on="2026-10-09")
        rows = [_row("", 150_000, "2026-10-09"), _row("", 100_000, "2026-10-09"), _row("", 50_000, "2026-10-09", customer="Ponce Property Co.")]
        for order in (rows, rows[::-1]):
            got = {r["amount_cents"]: r["status"] for r in qbo_csv.propose(state, order)}
            self.assertEqual(got, {150_000: "no match", 100_000: "already recorded", 50_000: "already recorded"})


class ReusedReferenceTests(unittest.TestCase):
    """'ACH' typed for every electronic payment, or one check paying two jobs, doesn't hide the next payment."""

    def test_ach_every_month(self):
        state = seed_state()
        domain.match_payment(state, "job-turner", 100_000, "ACH", "owner", received_on="2026-10-09")
        rows = qbo_csv.propose(state, [_row("ACH", 100_000, "2026-10-09"), _row("ACH", 200_000, "2026-11-09"), _row("ACH", 100_000, "2026-11-09")])
        self.assertEqual([r["status"] == "already recorded" for r in rows], [True, False, False])

    def test_ach_from_quickbooks_is_known_by_its_id_and_can_be_recorded(self):
        import qbo
        state = seed_state()
        trn = next(j for j in state["jobs"] if j["id"] == "job-turner")
        domain.match_payment(state, "job-turner", 100_000, "ACH", "owner", received_on="2026-10-09", qbo_payment_id="76", qbo_total_cents=100_000)
        row = _qbo_row(state, "77", 100_000, "2026-10-09", ref="ACH")
        got = qbo.propose(state, [row])[0]
        self.assertEqual((got["status"], got["number"]), ("match", "PA-TRN-01"))
        domain.match_payment(state, "job-turner", 100_000, "ACH", "owner", invoice_id=got["invoice_id"], received_on="2026-10-09",
                             qbo_payment_id="77", qbo_total_cents=100_000)
        self.assertEqual(len(trn["payments"]), 2)
        self.assertEqual(qbo.propose(state, [row])[0]["status"], "already recorded")

    def test_one_check_paying_two_jobs(self):
        state = seed_state()
        domain.match_payment(state, "job-turner", 600_000, "1001", "owner", received_on="2026-10-09")
        rows = qbo_csv.propose(state, [_row("1001", 600_000, "2026-10-09"), _row("1001", 400_000, "2026-10-09")])
        self.assertEqual([r["status"] == "already recorded" for r in rows], [True, False])
        self.assertNotEqual(qbo_csv.propose(state, [_row("1001", 400_000, "2026-10-09")])[0]["status"], "already recorded")


if __name__ == "__main__":
    unittest.main()
