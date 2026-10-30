"""Import/Export (I1, M1 of Stephen's Oct 29 review): quantities from the contractor's own
records onto a job's schedule of values, and the company's jobs, pay apps, payments and change
orders as CSV files."""

import base64
import csv
import io
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import exports
import qty_import
from domain import profiles
from test_accounts import ServerCase
from test_import_http import xlsx_bytes
from test_money import clear_packet, new_job, new_state
from test_sov import LINES

LOG = "Item,Description,Qty this period\n1,Rough-in wiring,30\n2,Panel and feeders,25%\n9,Gutters,4\n"


def sov_job(state, **kw):
    job = new_job(state, contract=3_450_000, retainage=10, **kw)
    domain.save_sov(state, job["id"], LINES, "15")
    return job


class QuantityReadTests(unittest.TestCase):
    def test_a_header_names_the_columns_and_zero_and_total_rows_are_skipped(self):
        parsed = qty_import.parse("Weekly log\nLine #,Scope,Installed\n1,Rough-in wiring,30 ea\n2,Panel and feeders,0\nTotal,,30\n")
        self.assertEqual([(r["item"], r["description"], r["qty"]) for r in parsed["rows"]], [("1", "Rough-in wiring", "30 ea")])
        self.assertEqual(len(parsed["warnings"]), 2)

    def test_a_running_total_column_is_not_read_as_this_periods_quantity(self):
        self.assertEqual(qty_import.parse("Item,Description,Completed to date\n1,Rough-in wiring,120\n")["rows"][0]["qty"], "120",
                         "no header found: the last number is taken, and the preview shows the new total before anything is added")
        self.assertIsNone(qty_import._header(["Item", "Description", "% complete"]))
        self.assertIsNone(qty_import.QTY_RE.match("(30)"), "an accounting negative is not 30")

    def test_without_a_header_the_item_words_and_last_number_are_taken(self):
        parsed = qty_import.parse("1, Rough-in wiring, 30\nPanel and feeders\t25%\nRough-in wiring 12\n")
        self.assertEqual([(r["item"], r["description"], r["qty"]) for r in parsed["rows"]],
                         [("1", "Rough-in wiring", "30"), ("", "Panel and feeders", "25%"), ("", "Rough-in wiring", "12")])

    def test_a_long_crafted_line_is_read_in_linear_time(self):
        import time
        start = time.monotonic()
        qty_import.parse(("a " * 1000 + "\n") * 2000)
        qty_import.parse(("1," * 1000 + "\n") * 2000)
        self.assertLess(time.monotonic() - start, 3)

    def test_items_and_descriptions_match_tolerantly(self):
        lines = [{"id": "a", "item": "1", "description": "Rough-in wiring"}, {"id": "b", "item": "2.10", "description": "Panel and feeders"},
                 {"id": "c", "item": "3", "description": "Panel trim"}]
        self.assertEqual(qty_import._match(lines, "01.", "")[0]["id"], "a")
        self.assertEqual(qty_import._match(lines, "2.10", "")[0]["id"], "b")
        self.assertIsNone(qty_import._match(lines, "2.1", "")[0], "2.1 and 2.10 are different items")
        self.assertEqual(qty_import._match(lines, "", "ROUGH IN wiring!")[0]["id"], "a")
        self.assertEqual(qty_import._match(lines, "", "rough-in")[0]["id"], "a")
        line, why = qty_import._match(lines, "", "panel")
        self.assertIsNone(line)
        self.assertIn("more than one line", why)
        line, why = qty_import._match(lines, "7", "Gutters")
        self.assertIn('item 7 "Gutters"', why)


class QuantityPreviewTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)

    def preview(self, text):
        return qty_import.preview(self.state, self.job, qty_import.parse(text))

    def test_preview_shows_each_row_and_changes_nothing(self):
        before = [dict(line) for line in domain.sov.lines(self.job)]
        p = self.preview(LOG + "1,Rough-in wiring,5\n")
        self.assertEqual(domain.sov.lines(self.job), before)
        self.assertEqual(self.job["invoices"], [])
        rows = {r["n"]: r for r in p["rows"]}
        self.assertEqual((rows[2]["line"], rows[2]["done"], rows[2]["add"], rows[2]["new_total"], rows[2]["scheduled"], rows[2]["problem"]),
                         ("1. Rough-in wiring", "0 ea", "30 ea", "30 ea", "400 ea", ""))
        self.assertEqual((rows[3]["add"], rows[3]["new_total"], rows[3]["add_milli"]), ("25%", "25%", 250), "a lump sum is % done this period")
        self.assertIn("No line on this job's schedule of values matches", rows[4]["problem"])
        self.assertIn("also on row 2", rows[5]["problem"])
        self.assertEqual((p["blocked"], p["can_add"]), ("", 2))

    def test_over_the_scheduled_quantity_and_bad_numbers_are_problems(self):
        domain.record_sov_progress(self.state, self.job["id"], {domain.sov.lines(self.job)[0]["id"]: "390"})
        p = self.preview("Item,Qty\n1,20\n2,12.25\n2,abc\n")
        self.assertIn("is more than the scheduled 400 ea", p["rows"][0]["problem"])
        self.assertEqual((p["rows"][0]["done"], p["rows"][0]["add_milli"]), ("390 ea", 0))
        self.assertIn("at most one decimal", p["rows"][1]["problem"])
        self.assertEqual(p["can_add"], 0)

    def test_a_job_that_cant_take_quantities_says_why(self):
        domain.mark_done(self.state, self.job["id"])
        p = self.preview(LOG)
        self.assertEqual(p["blocked"], "The job is marked done, so every line is already counted as done.")
        self.assertEqual(p["can_add"], 0)
        plain = new_job(self.state, name="No schedule")
        self.assertIn("no schedule of values", qty_import.preview(self.state, plain, qty_import.parse(LOG))["blocked"])


class QuantityApplyTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)

    def test_apply_adds_like_a_daily_record_and_keeps_what_it_couldnt(self):
        out = qty_import.apply(self.state, self.job["id"], LOG, "Pat", "foreman-log.csv")
        lines = domain.sov.lines(self.job)
        self.assertEqual((lines[0]["done_milli"], lines[1]["done_milli"]), (30_000, 250))
        draft = self.job["invoices"][-1]
        self.assertEqual((draft["status"], out["number"]), ("draft", draft["number"]))
        self.assertEqual([a["line"] for a in out["added"]], ["1. Rough-in wiring", "2. Panel and feeders"])
        self.assertEqual(out["not_added"][0]["what"], "Gutters")
        record = self.job["records"][-1]
        self.assertEqual(record["label"], "Quantities imported from foreman-log.csv")
        self.assertIn("Not added:\nRow 4 (Gutters): No line", record["note"])
        self.assertIn("quantities_imported", [a["action"] for a in self.state["audit"]])
        # A second file adds to the done-to-date and keeps the same draft number (the living draft).
        qty_import.apply(self.state, self.job["id"], "Item,Qty\n1,10\n", "Pat", "day2.csv")
        self.assertEqual(domain.sov.lines(self.job)[0]["done_milli"], 40_000)
        live = [i for i in self.job["invoices"] if i["status"] in domain.DRAFT_STATUSES]
        self.assertEqual([i["number"] for i in live], [draft["number"]])

    def test_amounts_follow_the_schedule_in_whole_cents(self):
        qty_import.apply(self.state, self.job["id"], "Item,Qty\n1,30\n", "Pat")
        draft = self.job["invoices"][-1]
        gross = 30 * 4500 + domain.sov.markup(30 * 4500, 1500)
        self.assertEqual(draft["amount_cents"] + draft["retainage_cents"], gross)
        self.assertIsInstance(draft["amount_cents"], int)

    def test_nothing_is_added_when_no_row_can_be(self):
        with self.assertRaises(domain.GateError) as ctx:
            qty_import.apply(self.state, self.job["id"], "Item,Qty\n9,4\n", "Pat")
        self.assertIn("Row 2: No line", ctx.exception.blockers[0])
        self.assertEqual(self.job["invoices"], [])
        domain.mark_done(self.state, self.job["id"])
        with self.assertRaises(domain.GateError):
            qty_import.apply(self.state, self.job["id"], LOG, "Pat")

    def test_a_preview_the_schedule_moved_on_from_is_refused(self):
        p = qty_import.preview(self.state, self.job, qty_import.parse(LOG), LOG)
        domain.record_sov_progress(self.state, self.job["id"], {domain.sov.lines(self.job)[0]["id"]: "5"})
        with self.assertRaises(domain.GateError) as ctx:
            qty_import.apply(self.state, self.job["id"], LOG, "Pat", seen=p["basis"])
        self.assertIn("changed since this file was read", str(ctx.exception))
        p = qty_import.preview(self.state, self.job, qty_import.parse(LOG), LOG)
        qty_import.apply(self.state, self.job["id"], LOG, "Pat", seen=p["basis"])
        self.assertEqual(domain.sov.lines(self.job)[0]["done_milli"], 35_000)

    def test_a_draft_with_the_client_for_review_is_left_alone(self):
        qty_import.apply(self.state, self.job["id"], "Item,Qty\n1,30\n", "Pat")
        self.job["invoices"][-1]["pencil_status"] = "sent"
        with self.assertRaises(domain.GateError) as ctx:
            qty_import.apply(self.state, self.job["id"], "Item,Qty\n1,10\n", "Pat")
        self.assertIn("with the client for their review", str(ctx.exception))
        self.assertEqual(domain.sov.lines(self.job)[0]["done_milli"], 30_000)


class QuantityHuntTests(unittest.TestCase):
    """Rows the Oct 30 bug hunt found read wrongly."""

    def setUp(self):
        self.state = new_state()
        self.job = sov_job(self.state)

    def rows(self, text, job=None):
        p = qty_import.preview(self.state, job or self.job, qty_import.parse(text), text)
        return [(r["line"], r["add"], r["problem"]) for r in p["rows"]], p

    def test_a_this_period_column_wins_over_a_bare_quantity_column(self):
        got, _ = self.rows("Item,Description,Quantity,Unit,Installed\n1,Rough-in wiring,400,ea,30\n2,Panel and feeders,1,LS,25%\n")
        self.assertEqual([(l, a) for l, a, _ in got], [("1. Rough-in wiring", "30 ea"), ("2. Panel and feeders", "25%")])
        got, _ = self.rows("Item,Description,Qty,Qty this period\n1,Rough-in wiring,400,30\n")
        self.assertEqual(got[0][1], "30 ea")

    def test_a_units_column_is_the_unit_not_the_quantity(self):
        got, _ = self.rows("Item,Description,Units,Qty\n1,Rough-in wiring,ea,30\n")
        self.assertEqual(got, [("1. Rough-in wiring", "30 ea", "")])
        got, _ = self.rows("Item,Description,Units,Installed\n1,Rough-in wiring,ea,30\n")
        self.assertEqual(got, [("1. Rough-in wiring", "30 ea", "")])

    def test_a_percent_or_another_unit_on_a_measured_line_is_refused(self):
        got, p = self.rows("Item,Description,% this period\n1,Rough-in wiring,25%\n2,Panel and feeders,25%\n")
        self.assertIn("%", got[0][2])
        self.assertEqual(got[0][1], "")
        self.assertEqual((got[1][1], got[1][2]), ("25%", ""))
        self.assertEqual(p["can_add"], 1)
        got, _ = self.rows("Item,Qty\n1,5 LF\n1,5 EA\n")
        self.assertIn("LF", got[0][2])
        lines = [{"item": "1", "description": "Tile", "quantity": "900", "unit": "SF", "rate": "10"}]
        job = new_job(self.state, contract=9000_00, retainage=10)
        domain.save_sov(self.state, job["id"], lines, "0")
        got, _ = self.rows("Item,Qty\n1,5 LF\n", job)
        self.assertIn("LF", got[0][2])
        got, _ = self.rows("Item,Qty\n1,5 sf\n", job)
        self.assertEqual(got[0][1:], ("5 SF", ""))

    def test_a_preview_the_schedule_was_renumbered_since_is_refused(self):
        text = "Item,Description,Qty\n1,,30\n"
        p = qty_import.preview(self.state, self.job, qty_import.parse(text), text)
        ls = domain.sov.lines(self.job)
        edited = [{"id": l["id"], "item": {"1": "2", "2": "1"}[l["item"]], "description": l["description"], "unit": l["unit"],
                   "quantity": domain.sov.fmt_qty(l["qty_milli"]).replace(",", ""), "rate": f"{l['rate_cents'] / 100:.2f}"} for l in ls]
        domain.save_sov(self.state, self.job["id"], edited, "15")
        with self.assertRaises(domain.GateError):
            qty_import.apply(self.state, self.job["id"], text, "Pat", seen=p["basis"])
        self.assertEqual([l["done_milli"] for l in domain.sov.lines(self.job)], [0, 0])

    def test_an_item_and_a_description_naming_different_lines_is_refused(self):
        got, p = self.rows("Item,Description,Qty\n1,Panel and feeders,25\n")
        self.assertEqual(got[0][1], "")
        self.assertIn("Panel and feeders", got[0][2])
        self.assertEqual(p["can_add"], 0)
        # A description in other words still goes by the item number.
        got, _ = self.rows("Item,Description,Qty\n1,Wiring for level 2,25\n")
        self.assertEqual(got[0][:2], ("1. Rough-in wiring", "25 ea"))

    def test_a_description_matches_whole_words_only(self):
        lines = [{"id": "a", "item": "1", "description": "Doors"}, {"id": "b", "item": "2", "description": "Base"},
                 {"id": "c", "item": "3", "description": "Paint"}]
        self.assertIsNone(qty_import._match(lines, "", "Doorstops")[0])
        self.assertIsNone(qty_import._match(lines, "", "Baseboard heaters")[0])
        self.assertEqual(qty_import._match(lines, "", "Doors, level 2")[0]["id"], "a")
        self.assertIsNone(qty_import._match([{"id": "x", "item": "1", "description": "Paint and tile"}], "", "and")[0])

    def test_many_rows_against_a_long_schedule_are_quick(self):
        import time
        job = new_job(self.state, contract=300_000_00, retainage=10)
        lines = [{"item": str(i), "description": ("Line description number %d " % i) * 7, "quantity": "100", "unit": "ea", "rate": "10"}
                 for i in range(300)]
        domain.save_sov(self.state, job["id"], lines, "0", update_contract=True)
        for text in ("Description,Qty\n" + "\n".join(("zz%dqq " % i) * 30 + ",5" for i in range(600)),
                     "Description,Qty\n" + "\n".join("Line description number,5" for _ in range(600))):
            start = time.monotonic()
            qty_import.preview(self.state, job, qty_import.parse(text), text)
            self.assertLess(time.monotonic() - start, 0.8)

    def test_preview_refuses_what_apply_would_and_apply_adds_the_rest(self):
        l1 = domain.sov.lines(self.job)[0]
        domain.record_sov_progress(self.state, self.job["id"], {}, stored={l1["id"]: "15000"})
        text = "Item,Description,Qty\n1,Rough-in wiring,100\n2,Panel and feeders,10%\n"
        got, p = self.rows(text)
        self.assertIn("stored", got[0][2])
        self.assertEqual((got[1][1], got[1][2], p["can_add"]), ("10%", "", 1))
        out = qty_import.apply(self.state, self.job["id"], text, "Pat", seen=p["basis"])
        self.assertEqual([a["line"] for a in out["added"]], ["2. Panel and feeders"])
        self.assertEqual([n["n"] for n in out["not_added"]], [2])
        self.assertEqual([l["done_milli"] for l in domain.sov.lines(self.job)], [0, 100])

    def test_a_quoted_cell_with_a_line_break_is_one_row(self):
        text = 'Item,Description,Qty\n1,"Rough-in\nwiring",30\n2,Panel and feeders,10\n'
        parsed = qty_import.parse(text)
        self.assertEqual([(r["n"], r["item"], r["description"], r["qty"]) for r in parsed["rows"]],
                         [(2, "1", "Rough-in wiring", "30"), (4, "2", "Panel and feeders", "10")])
        self.assertEqual(parsed["warnings"], [])
        # A quote left open doesn't swallow the rest of the file.
        parsed = qty_import.parse('Item,Description,Qty\n1,"Rough-in wiring,30\n2,Panel and feeders,10\n')
        self.assertIn(("2", "Panel and feeders", "10"), [(r["item"], r["description"], r["qty"]) for r in parsed["rows"]])

    def test_excel_numbers_are_rounded_to_three_decimals(self):
        import server
        data = xlsx_bytes([["Item", "Description", "Qty"], ["1", "Rough-in wiring", 0.1 * 3 * 100], ["2", "Panel and feeders", 12.5]])
        text = server._sheet_text({"xlsx": base64.b64encode(data).decode()})
        self.assertEqual(text, "Item,Description,Qty\n1,Rough-in wiring,30\n2,Panel and feeders,12.5\n")


class ExportFileTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, name="=HYPERLINK(\"http://x\")", retainage=10, customer="+Evil Client")
        domain.add_change_order(self.state, self.job["id"], "@Extra outlets", -50_000, reason="other", description="A credit")
        domain.record_progress(self.state, self.job["id"], 4_000_000)
        clear_packet(self.state, self.job)
        domain.approve_and_send(self.state, self.job["id"])
        inv = self.job["invoices"][-1]
        domain.match_payment(self.state, self.job["id"], inv["amount_cents"], "-1 chk", received_on="2026-10-20")
        self.done = new_job(self.state, name="Finished porch", contract=100_000)
        self.done["closed"] = True
        self.done["closed_at"] = "2026-09-30T12:00:00+00:00"

    def rows(self, kind):
        body = exports.csv_text(self.state, kind)
        self.assertTrue(body.startswith("﻿"))
        return list(csv.reader(io.StringIO(body[1:])))

    def test_cells_cant_run_as_formulas_and_money_is_whole_cents(self):
        self.assertEqual(exports.text("=1+1"), "'=1+1")
        self.assertEqual(exports.text("\t=cmd"), "'=cmd", "spaces and tabs are collapsed first")
        self.assertEqual(exports.text("-2"), "'-2")
        self.assertEqual(exports.text("Plain, \"quoted\""), "Plain, \"quoted\"")
        self.assertEqual((exports.dollars(-50), exports.dollars(123456789), exports.dollars(None)), ("-0.50", "1234567.89", "0.00"))

    def test_jobs_include_finished_ones_with_their_money(self):
        rows = self.rows("jobs")
        head = rows[0]
        job = dict(zip(head, rows[1]))
        self.assertEqual(job["Job"], "'=HYPERLINK(\"http://x\")")
        self.assertEqual(job["Client"], "'+Evil Client")
        m = domain.job_money(self.job)
        self.assertEqual((job["Contract"], job["Billed"], job["Received"], job["Outstanding"], job["Retainage held"]),
                         tuple(exports.dollars(m[k]) for k in ("contract_cents", "billed_cents", "collected_cents", "outstanding_cents", "retainage_held_cents")))
        self.assertEqual(job["Status"], "In progress")
        self.assertRegex(job["Started"], r"^\d{4}-\d{2}-\d{2}$")
        done = dict(zip(head, rows[2]))
        self.assertEqual((done["Job"], done["Status"], done["Finished"]), ("Finished porch", "Finished", "2026-09-30"))

    def test_pay_apps_payments_and_change_orders(self):
        inv = self.job["invoices"][-1]
        pa = dict(zip(*self.rows("payapps")[:2]))
        self.assertEqual((pa["Number"], pa["Status"], pa["Amount"], pa["Retainage"], pa["Paid on"]),
                         (inv["number"], "Paid", exports.dollars(inv["amount_cents"]), exports.dollars(inv["retainage_cents"]), "2026-10-20"))
        pay = dict(zip(*self.rows("payments")[:2]))
        self.assertEqual((pay["Date"], pay["Pay app"], pay["Reference"], pay["For"]), ("2026-10-20", inv["number"], "'-1 chk", "Pay app"))
        co = dict(zip(*self.rows("change-orders")[:2]))
        self.assertEqual((co["Number"], co["Title"], co["Reason"], co["Amount"], co["Status"], co["Signed on"]),
                         ("CO 1", "'@Extra outlets", "Other", "-500.00", "Unsigned", ""))


class ImportExportHttpTests(ServerCase):
    def owner_with_job(self, addr):
        cookie, _ = self.signup(addr, "Qty Co")
        res, st = self.request("POST", "/api/jobs", {"name": "Elm wiring", "customer": "Park GC", "address": "9 Elm St", "role": "GC",
                                                     "ap_email": "ap@park.example", "contract_cents": 3_450_000, "retainage_pct": 10}, cookie=cookie)
        job = st["jobs"][0]
        res, st = self.request("POST", f"/api/jobs/{job['id']}/sov", {"lines": LINES, "op_pct": "15"}, cookie=cookie)
        self.assertEqual(res.status, 200, st)
        return cookie, job["id"]

    def join(self, owner, addr):
        res, out = self.request("POST", "/api/team/invite", {"email": addr, "profile_id": "member"}, cookie=owner)
        res, joined = self.request("POST", "/api/auth/accept-invite", {"accept_terms": True, "token": out["invite_path"].split("#invite=")[1],
                                                                       "name": "Max", "password": "member password 1"})
        self.assertEqual(res.status, 200, joined)
        return res.getheader("Set-Cookie").split(";")[0]

    def test_preview_then_apply_a_csv_and_an_excel_file(self):
        cookie, job_id = self.owner_with_job("qty@import.example")
        res, p = self.request("POST", "/api/import/quantities/preview", {"job_id": job_id, "csv": LOG}, cookie=cookie)
        self.assertEqual(res.status, 200, p)
        self.assertEqual(p["can_add"], 2)
        res, st = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(st["jobs"][0]["invoices"], [], "preview changes nothing")
        res, out = self.request("POST", "/api/import/quantities/apply", {"job_id": job_id, "csv": LOG, "name": "log.csv"}, cookie=cookie)
        self.assertEqual(res.status, 409, "the preview's basis is needed")
        body = {"job_id": job_id, "csv": LOG, "name": "log.csv", "basis": p["basis"]}
        res, st = self.request("POST", "/api/import/quantities/apply", body, cookie=cookie)
        self.assertEqual(res.status, 200, st)
        self.assertEqual(len(st["quantity_import"]["added"]), 2)
        self.assertEqual(st["quantity_import"]["not_added"][0]["n"], 4)
        res, out = self.request("POST", "/api/import/quantities/apply", body, cookie=cookie)
        self.assertEqual(res.status, 409, "the same preview can't be added twice")
        self.assertIn("Read the file again", out["error"])
        res, st = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual([l["done_milli"] for l in st["jobs"][0]["sov"]["lines"]], [30_000, 250])
        data = xlsx_bytes([["Item", "Qty"], ["1", 10]])
        res, p = self.request("POST", "/api/import/quantities/preview", {"job_id": job_id, "xlsx": base64.b64encode(data).decode()}, cookie=cookie)
        self.assertEqual((res.status, p["rows"][0]["new_total"]), (200, "40 ea"), p)
        res, p = self.request("POST", "/api/import/quantities/preview", {"job_id": job_id, "xlsx": base64.b64encode(b"not a zip").decode()}, cookie=cookie)
        self.assertEqual(res.status, 409)
        res, p = self.request("POST", "/api/import/quantities/preview", {"job_id": job_id, "csv": " "}, cookie=cookie)
        self.assertEqual(res.status, 409)
        # Another company's job can't be read or changed.
        other, _ = self.signup("qty-other@import.example", "Other Co")
        res, _ = self.request("POST", "/api/import/quantities/apply", {"job_id": job_id, "csv": LOG, "basis": body["basis"]}, cookie=other)
        self.assertEqual(res.status, 409)

    def test_whoever_may_record_progress_may_import_and_the_server_enforces_it(self):
        owner, job_id = self.owner_with_job("qty-perm@import.example")
        member = self.join(owner, "qty-member@import.example")
        res, p = self.request("POST", "/api/import/quantities/preview", {"job_id": job_id, "csv": LOG}, cookie=member)
        self.assertEqual(res.status, 200, "recording progress is everyone's")
        with unittest.mock.patch.dict(profiles.JOB_ACTION_PERMS, {"sov-progress": "send_payapps"}):
            for path in ("/api/import/quantities/preview", "/api/import/quantities/apply"):
                res, out = self.request("POST", path, {"job_id": job_id, "csv": LOG, "basis": p["basis"]}, cookie=member)
                self.assertEqual(res.status, 403, (path, out))
        res, st = self.request("GET", "/api/state", cookie=owner)
        self.assertEqual(st["jobs"][0]["invoices"], [])

    def test_csv_exports_download_for_everyone_signed_in(self):
        owner, job_id = self.owner_with_job("export-csv@import.example")
        member = self.join(owner, "export-csv-member@import.example")
        for kind, first in (("jobs", "Job"), ("payapps", "Number"), ("payments", "Date"), ("change-orders", "Number")):
            for cookie in (owner, member):
                conn_res, body = self.request("GET", f"/api/export/{kind}.csv", cookie=cookie)
                self.assertEqual(conn_res.status, 200, kind)
                self.assertTrue(conn_res.getheader("Content-Type").startswith("text/csv"))
                self.assertIn("attachment; filename=\"duracash-", conn_res.getheader("Content-Disposition"))
                text = body.decode("utf-8-sig") if isinstance(body, bytes) else ""
                self.assertTrue(text.startswith(first + ","), (kind, text[:40]))
        res, _ = self.request("GET", "/api/export/jobs.csv")
        self.assertEqual(res.status, 401)
        res, _ = self.request("GET", "/api/export/secrets.csv", cookie=owner)
        self.assertEqual(res.status, 404)


if __name__ == "__main__":
    unittest.main()
