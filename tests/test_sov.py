"""Schedule of values: quantity x unit rate lines plus overhead and profit, billed line by line."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import sov
from test_money import clear_packet, new_job, new_state, send_open

# Rough-in 400 ea x $45 = $18,000; panel lump sum $12,000; base $30,000; 15% O&P $4,500; total $34,500.
LINES = [
    {"item": "1", "description": "Rough-in wiring", "quantity": "400", "unit": "ea", "rate": "45.00"},
    {"item": "2", "description": "Panel and feeders", "quantity": "1", "unit": "LS", "rate": "12000"},
]


def sov_job(state, retainage=10, contract=3_450_000):
    job = new_job(state, contract=contract, retainage=retainage)
    domain.save_sov(state, job["id"], LINES, "15")
    return job


def line(job, item):
    return next(l for l in sov.lines(job) if l["item"] == item)


def gross_billed(job):
    return sum(inv["gross_cents"] for inv in job["invoices"] if inv["status"] != "void")


class ParseTests(unittest.TestCase):
    def test_excel_rows_with_header_skip_totals_and_read_markup(self):
        text = (
            "Item\tDescription\tQty\tUnit\tUnit price\tTotal\n"
            "1\tRough-in wiring\t400\tea\t$45.00\t$18,000.00\n"
            "2\tPanel\t1\tLS\t12,500\t12,500\n"
            "\tSubtotal\t\t\t\t30,500\n"
            "\tOverhead & profit 15%\t\t\t\t4,575\n"
        )
        out = sov.parse_rows(text)
        self.assertTrue(out["header"])
        self.assertEqual([l["description"] for l in out["lines"]], ["Rough-in wiring", "Panel"])
        self.assertEqual(out["lines"][0]["quantity"], "400")
        self.assertEqual(out["lines"][0]["rate"], "45.00")
        self.assertEqual(out["op_pct"], "15")
        self.assertTrue(any("Subtotal" in w for w in out["warnings"]))

    def test_a_title_line_above_the_header_still_maps_columns_by_name(self):
        text = (
            "Brightwater Tile & Stone - Schedule of values\n"
            "Mill Creek Clubhouse\n"
            "Description\tAmount\tQty\tUnit\tRate\n"
            "Wall tile\t2,000.00\t100\tsf\t20\n"
            "Thresholds\t500\t\t\t\n"
        )
        out = sov.parse_rows(text)
        self.assertTrue(out["header"])
        got = [(l["description"], l["quantity"], l["unit"], l["rate"]) for l in out["lines"]]
        self.assertEqual(got, [("Wall tile", "100", "sf", "20.00"), ("Thresholds", "1", "LS", "500.00")])
        self.assertEqual(out["warnings"][:2], [
            "Row 1 (Brightwater Tile & Stone - Schedule of values) skipped: text above the header.",
            "Row 2 (Mill Creek Clubhouse) skipped: text above the header.",
        ])

    def test_a_header_past_the_fifth_row_is_not_looked_for(self):
        text = "a\nb\nc\nd\ne\nDescription,Amount\nWall tile,2000\n"
        out = sov.parse_rows(text)
        self.assertFalse(out["header"])
        self.assertFalse(any("above the header" in w for w in out["warnings"]))

    def test_rows_without_header_csv_and_pdf_spacing(self):
        out = sov.parse_rows(
            'Rough-in wiring, 400, ea, 45\nPanel and feeders, 12500\n"Wire, THHN #12","2,500",ft,0.85\n'
            "3  Conduit  1,200 lf  4.25\nTotal  43,000"
        )
        got = [(l["item"], l["description"], l["quantity"], l["unit"], l["rate"]) for l in out["lines"]]
        self.assertEqual(got, [
            ("", "Rough-in wiring", "400", "ea", "45.00"),
            ("", "Panel and feeders", "1", "LS", "12500.00"),
            ("", "Wire, THHN #12", "2500", "ft", "0.85"),
            ("3", "Conduit", "1200", "lf", "4.25"),
        ])
        self.assertTrue(any("Total" in w for w in out["warnings"]))

    def test_amount_that_disagrees_with_qty_times_rate_is_flagged(self):
        out = sov.parse_rows("Trim out, 120, ea, 30, 3700")
        self.assertEqual(out["lines"][0]["rate"], "30.00")
        self.assertTrue(any("check it" in w for w in out["warnings"]))

    def test_quantity_and_amount_without_rate(self):
        out = sov.parse_rows("Description,Qty,Amount\nFixtures,10,1500\nOdd,3,100")
        self.assertEqual((out["lines"][0]["quantity"], out["lines"][0]["rate"]), ("10", "150.00"))
        self.assertEqual((out["lines"][1]["quantity"], out["lines"][1]["unit"]), ("1", "LS"))

    def test_a_count_with_no_price_is_a_quantity_not_a_lump_sum(self):  # Oct 23 note 9
        for text in ("Site cleanup, 3 loads", "Site cleanup\t3\tloads", "Site cleanup  3 loads", "Site cleanup, 3, loads"):
            out = sov.parse_rows(text)
            self.assertEqual(out["lines"], [{
                "item": "", "description": "Site cleanup", "unit": "loads", "quantity": "3", "rate": "", "needs_price": True,
            }], text)
            self.assertEqual(out["warnings"], ["Row 1 (Site cleanup): 3 loads has no price. Enter the unit rate before you save."], text)
        for text, unit in (("Labor, 12 hrs", "hrs"), ("Rental, 4 days", "days"), ("Curb, 1200 LF", "LF"),
                           ("Fill, 30 CY", "CY"), ("Stone, 8 tons", "tons"), ("Doors, 6 ea", "ea"), ("Sod, 900 SF", "SF")):
            line = sov.parse_rows(text)["lines"][0]
            self.assertEqual((line["unit"], line["rate"], line.get("needs_price")), (unit, "", True), text)
        out = sov.parse_rows("4, Site cleanup, 3 loads")
        self.assertEqual((out["lines"][0]["item"], out["lines"][0]["quantity"]), ("4", "3"))
        self.assertEqual(sov.parse_rows("Haul, 0 loads")["lines"], [])

    def test_a_count_with_the_unit_first_or_under_a_header_needs_a_price(self):  # Oct 23 note 9
        # A sheet with Unit before Qty: the unit word sits just before the number.
        for text in ("Site cleanup, loads, 3", "Site cleanup\tloads\t3", "Site cleanup  loads  3"):
            out = sov.parse_rows(text)
            self.assertEqual(out["lines"], [{
                "item": "", "description": "Site cleanup", "unit": "loads", "quantity": "3", "rate": "", "needs_price": True,
            }], text)
            self.assertEqual(out["warnings"], ["Row 1 (Site cleanup): 3 loads has no price. Enter the unit rate before you save."], text)
        # The description is never taken as the unit, and a $ amount is money either way.
        self.assertEqual(sov.parse_rows("Loads, 3")["lines"][0]["rate"], "3.00")
        self.assertEqual(sov.parse_rows("Haul, loads, $300")["lines"][0]["rate"], "300.00")
        # Under a header, a quantity with the rate left blank comes in the same way, not skipped.
        for text in ("Description\tQty\tUnit\tRate\nSite cleanup\t3\tloads\t\nTile\t1200\tSF\t8.50",
                     "Description,Unit,Qty,Rate\nSite cleanup,loads,3,\nTile,SF,1200,8.50"):
            out = sov.parse_rows(text)
            self.assertEqual(out["lines"], [
                {"item": "", "description": "Site cleanup", "unit": "loads", "quantity": "3", "rate": "", "needs_price": True},
                {"item": "", "description": "Tile", "unit": "SF", "quantity": "1200", "rate": "8.50"},
            ], text)
            self.assertEqual(out["warnings"], ["Row 2 (Site cleanup): 3 loads has no price. Enter the unit rate before you save."], text)
        out = sov.parse_rows("Description,Qty\nPrep,40")
        self.assertEqual(out["warnings"], ["Row 2 (Prep): 40 has no price. Enter the unit rate before you save."])

    def test_money_rows_still_read_as_before(self):  # Oct 23 note 9
        def got(text):
            return [(l["description"], l["quantity"], l["unit"], l["rate"], l.get("needs_price")) for l in sov.parse_rows(text)["lines"]]
        self.assertEqual(got("Demo, $3,500"), [("Demo", "1", "LS", "3500.00", None)])
        self.assertEqual(got("Panel, 12500"), [("Panel", "1", "LS", "12500.00", None)])
        self.assertEqual(got("Panel, 12500 LS"), [("Panel", "1", "LS", "12500.00", None)])
        self.assertEqual(got("Haul, $300, loads"), [("Haul", "1", "loads", "300.00", None)])
        self.assertEqual(got("Tile, 1200 SF, 8.50"), [("Tile", "1200", "SF", "8.50", None)])
        self.assertEqual(got("Tile\t1200\tSF\t8.50"), [("Tile", "1200", "SF", "8.50", None)])
        self.assertEqual(got("Paint, 3 ea, 45"), [("Paint", "3", "ea", "45.00", None)])
        self.assertEqual(got("Demo\t3,500"), [("Demo", "1", "LS", "3500.00", None)])

    def test_typed_thousands_comma_is_one_number(self):  # Oct 23 note 9
        def got(text):
            return [(l["description"], l["quantity"], l["unit"], l["rate"], l.get("needs_price")) for l in sov.parse_rows(text)["lines"]]
        # Typed with ", " between cells, a comma with no space is a thousands separator.
        self.assertEqual(got("Demo, 3,500"), [("Demo", "1", "LS", "3500.00", None)])
        self.assertEqual(got("Demo, 3,500.75"), [("Demo", "1", "LS", "3500.75", None)])
        self.assertEqual(got("Site work, 1,234,567"), [("Site work", "1", "LS", "1234567.00", None)])
        self.assertEqual(got("Tile, 1,200, 8.50"), [("Tile", "1200", "ea", "8.50", None)])
        self.assertEqual(got("Footings, 4,500, LF"), [("Footings", "4500", "LF", "", True)])
        # Commas with spaces after them are still separate cells.
        self.assertEqual(got("Demo, 3, 500"), [("Demo", "3", "ea", "500.00", None)])
        # A saved CSV file (no ", " anywhere) keeps its columns: quantity 3 at $500.
        self.assertEqual(got("Demo,3,500"), [("Demo", "3", "ea", "500.00", None)])
        self.assertEqual(got("Description,Qty,Rate\nDemo,3,500"), [("Demo", "3", "ea", "500.00", None)])

    def test_a_quoted_description_with_a_comma_keeps_a_saved_csv_split(self):
        # Excel quotes "Doors, solid"; the ", " inside the quotes is not how the cells are separated.
        def got(text):
            out = sov.parse_rows(text)
            return [(l["description"], l["quantity"], l["unit"], l["rate"]) for l in out["lines"]], out["warnings"]
        self.assertEqual(got('Description,Qty,Rate,Amount\n"Doors, solid",12,450,5400'),
                         ([("Doors, solid", "12", "ea", "450.00")], []))
        self.assertEqual(got('"Paint, primer",3,500'), ([("Paint, primer", "3", "ea", "500.00")], []))
        self.assertEqual(got('Description,Qty,Rate\n"Paint, primer",3,500'), ([("Paint, primer", "3", "ea", "500.00")], []))
        # Typed with ", " between the cells, a comma with no space is still a thousands separator.
        self.assertEqual(got('"Doors, solid", 3,500')[0], [("Doors, solid", "1", "LS", "3500.00")])

    def test_a_thousands_comma_after_a_number_ending_a_text_cell_is_one_number(self):
        def got(text):
            out = sov.parse_rows(text)
            return [(l["description"], l["quantity"], l["unit"], l["rate"]) for l in out["lines"]], out["warnings"]
        self.assertEqual(got("Mobilization 12,500"), ([("Mobilization", "1", "LS", "12500.00")], []))
        self.assertEqual(got("Demo $3,500"), ([("Demo", "1", "LS", "3500.00")], []))
        self.assertEqual(got("Site work 1,234,567.50"), ([("Site work", "1", "LS", "1234567.50")], []))
        self.assertEqual(got("Gen conditions, 6 mo at 2,500/mo"), ([("Gen conditions", "6", "mo", "2500.00")], []))
        self.assertEqual(got("Doors, 12 ea @ 1,450"), ([("Doors", "12", "ea", "1450.00")], []))
        self.assertEqual(got("Mobilization 12,500, LS"), ([("Mobilization", "1", "LS", "12500.00")], []))
        # The description keeps its whole number; next to another cell it stays a name, as "Tile 1200 SF, 8.50" does.
        self.assertEqual(got("Concrete 2,400 SF, 8.50")[0], [("Concrete 2,400 SF", "1", "LS", "8.50")])
        # A $ amount at the end of a cell in a saved CSV is one number too; a plain one is left split.
        self.assertEqual(got("1,Demo $3,500")[0], [("Demo", "1", "LS", "3500.00")])
        self.assertEqual(got("Description,Qty,Rate\nLevel 2,3,500")[0], [("Level 2", "3", "ea", "500.00")])

    def test_quantity_at_rate_in_one_cell(self):  # Oct 23 note 9
        def got(text):
            return [(l["description"], l["quantity"], l["unit"], l["rate"]) for l in sov.parse_rows(text)["lines"]]
        self.assertEqual(got("Tile 1200 SF @ 8.50"), [("Tile", "1200", "SF", "8.50")])
        self.assertEqual(got("Tile, 1200 SF @ $8.50"), [("Tile", "1200", "SF", "8.50")])
        self.assertEqual(got("Floor tile 1,200 sf at $8.50/sf"), [("Floor tile", "1200", "sf", "8.50")])
        self.assertEqual(got("Trim 300 LF x 4.25"), [("Trim", "300", "LF", "4.25")])
        # "x" between two plain numbers is a size, not quantity times rate.
        self.assertEqual(got("Ceiling grid 2 x 2"), [])
        self.assertEqual(got("Ceiling grid 2 x 2, 1,800"), [("Ceiling grid 2 x 2", "1", "LS", "1800.00")])
        self.assertEqual(got("Shelving 4 x $8.50"), [("Shelving", "4", "ea", "8.50")])
        self.assertEqual(got("Labor 40 hrs @ 65 per hr"), [("Labor", "40", "hrs", "65.00")])
        self.assertEqual(got("Grout 12 @ 15"), [("Grout", "12", "ea", "15.00")])
        self.assertEqual(got("Tile\t1200 SF @ 8.50"), [("Tile", "1200", "SF", "8.50")])
        self.assertEqual(got("Tile, 1,200 SF @ 8.50"), [("Tile", "1200", "SF", "8.50")])
        self.assertEqual(got("Tile,1200 SF @ 8.50"), [("Tile", "1200", "SF", "8.50")])
        # A description, a quantity and a unit word in one cell: a quantity that needs a price.
        self.assertEqual(got("Concrete 2,400 SF"), [("Concrete", "2400", "SF", "")])
        self.assertEqual(got("Haul 3 loads"), [("Haul", "3", "loads", "")])
        # With other cells on the row, a quantity at the end of the description stays in it
        # ("General conditions 16 wks<TAB>24000" is a lump sum), so this is $8.50, not 1200 SF.
        self.assertEqual(got("Tile 1200 SF, 8.50"), [("Tile 1200 SF", "1", "LS", "8.50")])
        self.assertEqual(got("Level 2 tile, 500"), [("Level 2 tile", "1", "LS", "500.00")])
        # A number inside the description stays in the description.
        self.assertEqual(got("Level 2 tile 1200 SF @ 8.50"), [("Level 2 tile", "1200", "SF", "8.50")])
        # Rows without "@", "at" or "x" read as before.
        self.assertEqual(got("Paint, 3 ea, 45"), [("Paint", "3", "ea", "45.00")])
        self.assertEqual(got("Demo, $3,500"), [("Demo", "1", "LS", "3500.00")])

    def test_a_number_in_the_description_cell_stays_there_on_a_row_with_columns(self):
        # Before the one-cell readings, these were read the same way; the description's own
        # "16 wks" or "2 x 2" is part of the name, not a quantity or a rate.
        def got(text):
            out = sov.parse_rows(text)
            return [(l["description"], l["quantity"], l["unit"], l["rate"]) for l in out["lines"]], out["warnings"]
        self.assertEqual(got("General conditions 16 wks\t24000"), ([("General conditions 16 wks", "1", "LS", "24000.00")], []))
        self.assertEqual(got("Supervision 12 weeks\t18,000"), ([("Supervision 12 weeks", "1", "LS", "18000.00")], []))
        self.assertEqual(got("Electrical rough-in 40 hrs\t2500"), ([("Electrical rough-in 40 hrs", "1", "LS", "2500.00")], []))
        self.assertEqual(got("General conditions 16 wks, 24000"), ([("General conditions 16 wks", "1", "LS", "24000.00")], []))
        self.assertEqual(got("Ceiling grid 2 x 2\t1\tLS\t5000"), ([("Ceiling grid 2 x 2", "1", "LS", "5000.00")], []))
        self.assertEqual(got("Retaining wall 4 ft\t120\tLF\t85"), ([("Retaining wall 4 ft", "120", "LF", "85.00")], []))
        self.assertEqual(got("Skylight 2 x 4\t3\tea\t850"), ([("Skylight 2 x 4", "3", "ea", "850.00")], []))
        self.assertEqual(got("1\tSkylight 2 x 4\t3\tea\t850"), ([("Skylight 2 x 4", "3", "ea", "850.00")], []))
        # Empty cells around one cell still leave it the only cell on the row.
        self.assertEqual(got("Concrete 2,400 SF\t\t")[0], [("Concrete", "2400", "SF", "")])
        self.assertEqual(got("Tile 1200 SF @ 8.50\t")[0], [("Tile", "1200", "SF", "8.50")])

    def test_garbage_is_reported_not_crashed(self):
        out = sov.parse_rows("hello\n\n,,,\n$$$, abc\n")
        self.assertEqual(out["lines"], [])
        self.assertTrue(out["warnings"])


class SaveTests(unittest.TestCase):
    def test_schedule_must_match_contract(self):
        state = new_state()
        job = new_job(state, contract=3_000_000)
        with self.assertRaises(domain.GateError) as ctx:
            domain.save_sov(state, job["id"], LINES, "15")
        self.assertIn("$34,500.00", str(ctx.exception))
        self.assertIn("$30,000.00", str(ctx.exception))
        self.assertNotIn("sov", job)
        domain.save_sov(state, job["id"], LINES, "15", update_contract=True)
        self.assertEqual(job["contract_cents"], 3_450_000)
        self.assertTrue(any(a["action"] == "contract_changed" for a in state["audit"]))

    def test_bad_lines_are_named(self):
        state = new_state()
        job = new_job(state)
        with self.assertRaises(domain.GateError) as ctx:
            domain.save_sov(state, job["id"], [{"description": "", "quantity": "1", "rate": "5"}, {"description": "X", "quantity": "-2", "rate": "5"}, {"description": "Y", "quantity": "1.23456", "rate": "5"}], "0")
        text = " ".join(ctx.exception.blockers)
        self.assertIn("Line 1", text)
        self.assertIn("Line 2", text)
        self.assertIn("three decimals", text)
        with self.assertRaises(domain.GateError):
            domain.save_sov(state, job["id"], LINES, "75")

    def test_a_line_with_no_price_is_refused(self):  # Oct 23 note 9: the server refuses what the editor flags
        state = new_state()
        job = new_job(state)
        for rate in ("", None, "  "):
            with self.assertRaises(domain.GateError) as ctx:
                domain.save_sov(state, job["id"], [{"description": "Site cleanup", "quantity": "3", "unit": "loads", "rate": rate}], "0")
            self.assertEqual(ctx.exception.blockers, ["Line 1 (Site cleanup): needs a price (unit rate)."])
        self.assertNotIn("sov", job)

    def test_a_draft_alone_says_delete_the_draft(self):  # Oct 23 note 8: wording only, the lock is unchanged
        state = new_state()
        job = sov_job(state)
        self.assertEqual(domain._sov_view(job)["draft_lock"], None)
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "100"})
        draft = job["invoices"][-1]
        view = domain._sov_view(job)
        self.assertFalse(view["editable"])
        self.assertEqual(view["draft_lock"], {"id": draft["id"], "number": draft["number"]})
        with self.assertRaises(domain.GateError) as ctx:
            domain.save_sov(state, job["id"], LINES, "15")
        self.assertEqual(str(ctx.exception), f"The schedule is fixed while {draft['number']} is a draft. Delete the draft to change it.")
        # Deleting the draft opens it again, as before.
        domain.void_draft(state, job["id"])
        self.assertTrue(domain._sov_view(job)["editable"])
        domain.save_sov(state, job["id"], LINES, "15")
        # Once a pay app is sent, the draft hint goes and the old message stays.
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "100"})
        send_open(state, job)
        self.assertEqual(domain._sov_view(job)["draft_lock"], None)
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "200"})
        self.assertEqual(domain._sov_view(job)["draft_lock"], None, "a sent pay app keeps it fixed even with a draft")
        with self.assertRaises(domain.GateError) as ctx:
            domain.save_sov(state, job["id"], LINES, "15")
        self.assertIn("fixed once a pay app exists", str(ctx.exception))

    def test_a_draft_on_a_job_without_a_schedule_says_so(self):  # Oct 23 note 8
        state = new_state()
        job = new_job(state, contract=3_450_000)
        domain.record_progress(state, job["id"], 100_000)
        view = domain._sov_view(job)
        self.assertEqual((view["present"], view["editable"]), (False, False))
        self.assertEqual(view["draft_lock"]["number"], job["invoices"][-1]["number"])
        # The server refuses what the card holds back, with the same reason.
        with self.assertRaises(domain.GateError) as ctx:
            domain.save_sov(state, job["id"], LINES, "15")
        self.assertEqual(str(ctx.exception), f"The schedule is fixed while {view['draft_lock']['number']} is a draft. Delete the draft to change it.")

    def test_dollar_progress_refused_and_removal_restores_it(self):
        state = new_state()
        job = sov_job(state)
        with self.assertRaises(domain.GateError):
            domain.record_progress(state, job["id"], 100_000)
        domain.save_sov(state, job["id"], [], "0")
        self.assertNotIn("sov", job)
        domain.record_progress(state, job["id"], 100_000)
        self.assertEqual(len(job["invoices"]), 1)


class BillingTests(unittest.TestCase):
    def test_progress_bills_line_by_line_with_markup_and_retainage(self):
        state = new_state()
        job = sov_job(state)
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "100"}, "first floor")
        inv = job["invoices"][-1]
        # 100 x $45 = $4,500; 15% O&P = $675; gross $5,175; 10% retainage $517.50; due $4,657.50
        self.assertEqual(inv["gross_cents"], 517_500)
        self.assertEqual(inv["op_cents"], 67_500)
        self.assertEqual(inv["retainage_cents"], 51_750)
        self.assertEqual(inv["amount_cents"], 465_750)
        self.assertEqual([e["amount_cents"] for e in inv["sov_lines"]], [450_000])
        # The continuation sheet lists every line, billed this period or not.
        self.assertEqual([(e["description"], e["amount_cents"]) for e in inv["sov_sheet"]], [("Rough-in wiring", 450_000), ("Panel and feeders", 0)])
        self.assertIn("100 ea this period", inv["lines"][0]["label"])
        self.assertIn("25% to date", inv["lines"][0]["label"])
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)
        send_open(state, job)

        # Second period: rough-in to 250, panel to 50%.
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "250", line(job, "2")["id"]: "0.5"})
        inv2 = job["invoices"][-1]
        self.assertEqual([e["amount_cents"] for e in inv2["sov_lines"]], [675_000, 600_000])
        # O&P to date on $17,250 is $2,587.50; $675 already billed.
        self.assertEqual(inv2["op_cents"], 258_750 - 67_500)
        self.assertEqual(inv2["sov_lines"][0]["qty_milli_this"], 150_000)
        self.assertIn("50% complete to date", inv2["lines"][1]["label"])

    def test_stored_materials_are_billed_once(self):  # P1-5: G703 column F
        state = new_state()
        job = sov_job(state)
        rough = line(job, "1")["id"]
        # Wire for the whole floor delivered: $3,000 stored, 100 ea installed.
        domain.record_sov_progress(state, job["id"], {rough: "100"}, stored={rough: "3000"})
        inv = job["invoices"][-1]
        entry = inv["sov_lines"][0]
        self.assertEqual((entry["work_this_cents"], entry["stored_cents"], entry["amount_cents"]), (450_000, 300_000, 750_000))
        self.assertEqual(inv["op_cents"], 112_500, "O&P applies to stored materials on base lines too")
        cols = sov.sheet_columns(entry, job["retainage_pct"])
        self.assertEqual((cols["previous"], cols["this_period"], cols["stored"], cols["to_date"]), (0, 450_000, 300_000, 750_000))
        self.assertEqual(cols["retainage"], 75_000)
        send_open(state, job)
        # 50 more installed from what was stored ($2,250 of it): the total to date is unchanged,
        # so nothing new is billed and no pay app is drafted.
        count = len(job["invoices"])
        domain.record_sov_progress(state, job["id"], {rough: "150"}, stored={rough: "750"})
        self.assertEqual(len(job["invoices"]), count, "installing stored materials bills nothing new")
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)
        # Then the rest of the stored wire goes in, plus 33 ea more: only the new work is billed.
        domain.record_sov_progress(state, job["id"], {rough: "200"}, stored={rough: "0"})
        entry3 = job["invoices"][-1]["sov_lines"][0]
        cols = sov.sheet_columns(entry3, job["retainage_pct"])
        self.assertEqual(entry3["amount_cents"], 900_000 - 750_000)
        self.assertEqual((cols["previous"], cols["this_period"], cols["stored"], cols["to_date"]), (450_000, 450_000, 0, 900_000))
        self.assertEqual(cols["previous"] + cols["this_period"] + cols["stored"], cols["to_date"])

    def test_stored_materials_are_checked(self):
        state = new_state()
        job = sov_job(state)
        rough = line(job, "1")["id"]
        with self.assertRaises(domain.GateError) as err:
            domain.record_sov_progress(state, job["id"], {rough: "300"}, stored={rough: "5000"})
        self.assertIn("more than the line", " ".join(err.exception.blockers))
        with self.assertRaises(domain.GateError):
            domain.record_sov_progress(state, job["id"], {}, stored={rough: "-5"})
        domain.record_sov_progress(state, job["id"], {}, stored={rough: "2000"})
        send_open(state, job)
        with self.assertRaises(domain.GateError) as err:
            domain.record_sov_progress(state, job["id"], {}, stored={rough: "0"})
        self.assertIn("already billed", " ".join(err.exception.blockers))

    def test_quantities_are_checked(self):
        state = new_state()
        job = sov_job(state)
        rough = line(job, "1")["id"]
        with self.assertRaises(domain.GateError) as ctx:
            domain.record_sov_progress(state, job["id"], {rough: "401"})
        self.assertIn("change order", " ".join(ctx.exception.blockers))
        domain.record_sov_progress(state, job["id"], {rough: "100"})
        with self.assertRaises(domain.GateError) as ctx:
            domain.record_sov_progress(state, job["id"], {rough: "150"})
        self.assertIn("still a draft", str(ctx.exception))
        send_open(state, job)
        with self.assertRaises(domain.GateError) as ctx:
            domain.record_sov_progress(state, job["id"], {rough: "90"})
        self.assertIn("already billed", " ".join(ctx.exception.blockers))
        with self.assertRaises(domain.GateError):
            domain.record_sov_progress(state, job["id"], {"sov-nope": "1"})
        with self.assertRaises(domain.GateError):
            domain.record_sov_progress(state, job["id"], {rough: "100"})  # nothing changed

    def test_schedule_is_fixed_after_first_pay_app(self):
        state = new_state()
        job = sov_job(state)
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "10"})
        with self.assertRaises(domain.GateError) as ctx:
            domain.save_sov(state, job["id"], LINES, "10")
        self.assertIn("fixed", str(ctx.exception))
        # Voiding the only pay app makes it editable again.
        domain.void_draft(state, job["id"])
        domain.save_sov(state, job["id"], LINES, "15")

    def test_void_returns_quantities_to_unbilled(self):
        state = new_state()
        job = sov_job(state, retainage=0)
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "100"})
        domain.void_draft(state, job["id"])
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 517_500)
        domain.draft_invoice(state, job["id"], strict=True)
        self.assertEqual(job["invoices"][-1]["gross_cents"], 517_500)

    def test_whole_job_bills_exactly_the_contract_plus_change_orders(self):
        state = new_state()
        job = sov_job(state)
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "133.333"})
        send_open(state, job)
        # Approved CO mid-job becomes its own line, billed when done; no markup on it.
        domain.add_change_order(state, job["id"], "Extra circuits", 250_000)
        co1 = job["change_orders"][-1]
        domain.approve_change_order(state, job["id"], co1["id"])
        co_line = next(l for l in sov.lines(job) if l.get("co_id") == co1["id"])
        self.assertEqual((co_line["scheduled_cents"], co_line["done_milli"]), (250_000, 0))
        domain.record_sov_progress(state, job["id"], {co_line["id"]: "1", line(job, "2")["id"]: "0.333"})
        inv = job["invoices"][-1]
        self.assertIn(co_line["id"], [e["line_id"] for e in inv["sov_lines"]])
        send_open(state, job)
        # A second CO billed while unsigned, then approved: not billed twice.
        domain.add_change_order(state, job["id"], "Relocate panel", 80_000)
        co2 = job["change_orders"][-1]
        domain.include_change_order(state, job["id"], co2["id"])
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "300"})
        draft = job["invoices"][-1]
        self.assertTrue(any(l.get("co_id") == co2["id"] for l in draft["lines"]))
        domain.approve_change_order(state, job["id"], co2["id"])
        self.assertEqual(next(l for l in sov.lines(job) if l.get("co_id") == co2["id"])["done_milli"], 1000)
        send_open(state, job)
        domain.mark_done(state, job["id"], "Owner", "finished")
        send_open(state, job)
        self.assertEqual(gross_billed(job), 3_450_000 + 250_000 + 80_000)
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)
        self.assertTrue(all(l["done_milli"] == l["qty_milli"] for l in sov.lines(job)))

    def test_pay_app_text_has_continuation_sheet(self):
        state = new_state()
        job = sov_job(state)
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "100"})
        inv = job["invoices"][-1]
        text = domain.pay_app_text(state, job, inv)
        self.assertIn("Schedule of values (continuation sheet", text)
        self.assertIn("Rough-in wiring", text)
        self.assertIn("4,500.00", text)
        self.assertIn("Panel and feeders", text)
        self.assertIn("Amount due this application: $4,657.50", text)
        domain.send_pencil(state, job["id"], "Owner")
        self.assertIn("Rough-in wiring", state["email_log"][-1]["body"])

    def test_snapshot_view(self):
        state = new_state()
        job = sov_job(state)
        view = domain.snapshot(state)["jobs"][0]["sov_view"]
        self.assertTrue(view["present"])
        self.assertTrue(view["editable"])
        self.assertEqual(view["totals"]["scheduled_cents"], 3_450_000)
        self.assertTrue(view["lines"][1]["lump_sum"])
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "100"})
        view = domain.snapshot(state)["jobs"][0]["sov_view"]
        self.assertFalse(view["editable"])
        self.assertEqual(view["lines"][0]["billed_cents"], 450_000)


if __name__ == "__main__":
    unittest.main()


class ReviewFindingTests(unittest.TestCase):
    """Regressions for the third independent review."""

    def test_exponents_and_huge_numbers_are_refused_fast(self):  # findings 1 and 3
        import time
        state = new_state()
        job = new_job(state, contract=3_450_000)
        for bad in ("1e999990", "1e999999999", "Infinity", "NaN", "9" * 40):
            start = time.monotonic()
            with self.assertRaises(domain.GateError):
                domain.save_sov(state, job["id"], [{"description": "A", "quantity": bad, "rate": "1"}], "0")
            with self.assertRaises(domain.GateError):
                domain.save_sov(state, job["id"], LINES, bad)
            self.assertLess(time.monotonic() - start, 0.5)
        state2 = new_state()
        job2 = sov_job(state2)
        with self.assertRaises(domain.GateError):
            domain.record_sov_progress(state2, job2["id"], {line(job2, "1")["id"]: "1e999999999"})

    def test_paste_reader_is_linear(self):  # finding 2
        import time
        for text in ("Desc," + "1" * 200_000 + "x", "Overhead " + "1" * 190_000 + "x %", ("A," + "9" * 30 + ",5\n") * 5000):
            start = time.monotonic()
            sov.parse_rows(text)
            self.assertLess(time.monotonic() - start, 1.0)
        out = sov.parse_rows("Description,Qty,Rate\nA,1e5000,5")
        self.assertEqual(out["lines"], [])
        self.assertIn("plain number", out["warnings"][0])

    def test_repeated_line_ids_become_separate_lines(self):  # finding 4
        state = new_state()
        job = new_job(state, contract=30_000, retainage=0)
        domain.save_sov(state, job["id"], [{"description": "A", "quantity": "1", "rate": "100"}, {"description": "B", "quantity": "1", "rate": "200"}], "0")
        a_id = sov.lines(job)[0]["id"]
        domain.save_sov(state, job["id"], [{"id": a_id, "description": "A", "quantity": "1", "rate": "100"}, {"id": a_id, "description": "B", "quantity": "1", "rate": "200"}], "0")
        ids = [l["id"] for l in sov.lines(job)]
        self.assertEqual(len(set(ids)), 2)
        domain.record_sov_progress(state, job["id"], {a_id: "1"})
        send_open(state, job)
        domain.mark_done(state, job["id"], "Owner")
        send_open(state, job)
        self.assertEqual(gross_billed(job), 30_000)

    def test_co_line_billed_unsigned_cannot_be_lowered(self):  # finding 10
        state = new_state()
        job = sov_job(state, retainage=0)
        domain.add_change_order(state, job["id"], "Extra", 100_000)
        co = job["change_orders"][-1]
        domain.include_change_order(state, job["id"], co["id"])
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "10"})
        domain.approve_change_order(state, job["id"], co["id"])
        send_open(state, job)
        co_line = next(l for l in sov.lines(job) if l.get("co_id"))
        with self.assertRaises(domain.GateError) as ctx:
            domain.record_sov_progress(state, job["id"], {co_line["id"]: "0"})
        self.assertIn("already billed", " ".join(ctx.exception.blockers))

    def test_waiver_email_attaches_the_waiver_on_record(self):  # finding 9
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 500_000)
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk 1", "Owner", invoice_id=inv["id"])
        domain.send_waiver(state, job["id"], "Owner")
        self.assertIn("will follow separately", state["email_log"][-1]["body"])
        self.assertEqual(state["email_log"][-1]["attachments"], [])
        # With a waiver file on the record, the next waiver email carries it.
        job["records"].append({"id": "rec-w", "kind": "waiver", "label": "Conditional waiver", "file_ids": ["file-w1"]})
        domain.record_progress(state, job["id"], 1_000_000)
        inv2 = send_open(state, job)
        domain.match_payment(state, job["id"], inv2["amount_cents"], "chk 2", "Owner", invoice_id=inv2["id"])
        domain.send_waiver(state, job["id"], "Owner")
        self.assertEqual(state["email_log"][-1]["attachments"], ["file-w1"])
        self.assertIn("is attached", state["email_log"][-1]["body"])

    def test_retainage_requests_are_spaced(self):  # finding 9
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.mark_done(state, job["id"], "Owner")
        send_open(state, job)
        for item in job["closeout_packet"]:
            domain.resolve_packet_item(state, job["id"], item["id"], reason="done")
        domain.chase_retainage(state, job["id"], "Owner")
        with self.assertRaises(domain.GateError) as ctx:
            domain.chase_retainage(state, job["id"], "Owner")
        self.assertIn("next is due", str(ctx.exception))

    def test_payer_copy_field_becomes_cc(self):  # finding 11
        state = new_state()
        job = new_job(state, contract=1_000_000)
        state["payers"][0]["copy"] = "Dana (PM) <dana@park.example>, the bookkeeper, ap@jones.example"
        domain.record_progress(state, job["id"], 500_000)
        send_open(state, job)
        self.assertEqual(state["email_log"][-1]["cc"], ["dana@park.example"])


class StoredAndQuantityTests(unittest.TestCase):
    """Stored materials and quantities kept through a schedule edit and an undo of done."""

    def test_resaving_the_schedule_keeps_stored_materials(self):
        state = new_state()
        job = sov_job(state)
        rough = line(job, "1")["id"]
        domain.record_sov_progress(state, job["id"], {rough: "100"}, stored={rough: "3000"})
        domain.void_draft(state, job["id"])
        before = sov.unbilled(job)
        self.assertEqual(before, 862_500)  # $4,500 work + $3,000 stored + 15% O&P
        edited = [{**raw, "id": l["id"]} for raw, l in zip(LINES, sov.lines(job))]
        edited[0]["description"] = "Rough-in wiring, all floors"
        domain.save_sov(state, job["id"], edited, "15")
        self.assertEqual(line(job, "1")["done_milli"], 100_000)
        self.assertEqual(line(job, "1")["stored_cents"], 300_000)
        self.assertEqual(sov.unbilled(job), before)

    def test_resaving_caps_stored_materials_to_what_the_line_has_left(self):
        state = new_state()
        job = sov_job(state)
        rough = line(job, "1")["id"]
        domain.record_sov_progress(state, job["id"], {rough: "100"}, stored={rough: "3000"})
        domain.void_draft(state, job["id"])
        # Rough-in cut to 150 ea ($6,750): 100 done ($4,500) leaves $2,250 for stored materials.
        edited = [{**raw, "id": l["id"]} for raw, l in zip(LINES, sov.lines(job))]
        edited[0]["quantity"] = "150"
        edited[1]["rate"] = "23250"
        domain.save_sov(state, job["id"], edited, "15")
        self.assertEqual(line(job, "1")["stored_cents"], 225_000)
        self.assertEqual(sov.line_to_date(line(job, "1")), 675_000)

    def test_undo_done_keeps_quantity_billed_on_a_zero_dollar_period(self):
        state = new_state()
        job = sov_job(state)
        rough, panel = line(job, "1")["id"], line(job, "2")["id"]
        domain.record_sov_progress(state, job["id"], {rough: "100"}, stored={rough: "3000"})
        send_open(state, job)
        # 50 more installed from what was stored: rough-in bills $0 this period, the panel bills.
        domain.record_sov_progress(state, job["id"], {rough: "150", panel: "0.5"}, stored={rough: "750"})
        inv2 = job["invoices"][-1]
        self.assertNotIn(rough, [e["line_id"] for e in inv2["sov_lines"]])
        send_open(state, job)
        self.assertEqual(sov.billed(job)["qty_by_line"][rough], 150_000)
        domain.mark_done(state, job["id"])
        domain.undo_mark_done(state, job["id"])
        self.assertEqual(line(job, "1")["done_milli"], 150_000)
        self.assertEqual(sov.line_to_date(line(job, "1")), 750_000)
        with self.assertRaises(domain.GateError) as err:
            domain.record_sov_progress(state, job["id"], {rough: "120"}, stored={rough: "2000"})
        self.assertIn("150 ea is already billed", " ".join(err.exception.blockers))
