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
