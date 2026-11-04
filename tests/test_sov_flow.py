"""The schedule of values flow (Nov 3 overhaul): the schedule is on the job, field quantities add
up per period, the period gives a suggested pay app amount, and the person decides the final one.
Every figure here is worked out by hand to the cent."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import migrations
import payapp_pdf
import qty_import
import sov
from seed import seed_state
from test_money import clear_packet, new_job, new_state, send_open
from test_sov import LINES, line, sov_job


def open_draft(job):
    return next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)


def period(job, state=None):
    return domain._sov_view(job, state)["period"]


def row(view, line_id):
    return next(r for r in view["lines"] if r["line_id"] == line_id)


def daily(state, job, rows):
    domain.add_daily_record(state, job["id"], "Work today", details={"sections": {"quantities": rows}})


def audit_texts(state, action):
    return [e["detail"] if "detail" in e else e.get("text", "") for e in state["audit"] if e.get("action") == action]


def auto_job(state, contract=4_850_037, retainage=10):
    return new_job(state, contract=contract, retainage=retainage, auto_schedule=True)


class PeriodTotalTests(unittest.TestCase):
    def test_daily_quantities_add_up_to_the_suggested_value(self):
        state = new_state()
        job = sov_job(state)   # 400 ea x $45, lump sum $12,000, 15% O&P, 10% retainage
        rough, panel = line(job, "1")["id"], line(job, "2")["id"]
        daily(state, job, [{"line_id": rough, "qty": "12.5"}])
        daily(state, job, [{"line_id": rough, "qty": "7.5"}, {"line_id": panel, "qty": "12.5"}])
        p = period(job, state)
        r = row(p, rough)
        self.assertEqual((r["qty_this_milli"], r["from_records_milli"], r["record_count"]), (20_000, 20_000, 2))
        self.assertEqual((r["adds_up_cents"], r["suggested_cents"], r["final_cents"]), (90_000, 90_000, 0))
        r = row(p, panel)
        self.assertEqual((r["qty_this_milli"], r["from_records_milli"]), (125, 125))
        self.assertEqual(r["suggested_cents"], 150_000)
        # $900 + $1,500 = $2,400; 15% O&P $360; $2,760; 10% retainage $276; $2,484 due.
        self.assertEqual(p["op_suggested_cents"], 36_000)
        self.assertEqual((p["suggested_gross_cents"], p["suggested_retainage_cents"], p["suggested_net_cents"]), (276_000, 27_600, 248_400))
        self.assertEqual(p["draft"], None, "a job record makes no draft")
        self.assertEqual(job["invoices"], [])
        # Progress Pay App starts from the suggested values.
        domain.record_sov_progress(state, job["id"], {})
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (276_000, 27_600, 248_400))
        p = period(job, state)
        self.assertEqual(p["draft"]["number"], draft["number"])
        self.assertEqual(p["to"], draft["period_to"])
        self.assertEqual((row(p, rough)["final_cents"], p["final_net_cents"]), (90_000, 248_400))

    def test_the_period_starts_again_after_a_pay_app_is_sent(self):
        state = new_state()
        job = sov_job(state)
        rough, panel = line(job, "1")["id"], line(job, "2")["id"]
        daily(state, job, [{"line_id": rough, "qty": "20"}, {"line_id": panel, "qty": "12.5"}])
        domain.record_sov_progress(state, job["id"], {})
        sent = send_open(state, job)
        # Times are to the second: this much earlier, as on a real job.
        for rec in state["daily_records"]:
            rec["at"] = "2026-01-01T08:00:00Z"
        sent["created_at"] = "2026-01-02T08:00:00Z"
        p = period(job, state)
        self.assertEqual(p["from"], sent["period_to"])
        self.assertEqual(p["draft"], None)
        self.assertEqual((row(p, rough)["qty_this_milli"], row(p, rough)["record_count"], p["suggested_gross_cents"]), (0, 0, 0))
        daily(state, job, [{"line_id": rough, "qty": "33.333"}])
        p = period(job, state)
        r = row(p, rough)
        self.assertEqual((r["qty_this_milli"], r["from_records_milli"], r["record_count"]), (33_333, 33_333, 1))
        # 53.333 ea x $45 = $2,399.985, rounded half-up to $2,399.99; less $900 billed = $1,499.99.
        self.assertEqual(r["suggested_cents"], 149_999)
        # Base to date $3,899.99 x 15% = $585.00 (58,499.85 cents up); less $360 billed = $225.00.
        self.assertEqual(p["op_suggested_cents"], 22_500)
        # $1,724.99 gross; 10% is $172.499, half-up $172.50; $1,552.49 due.
        self.assertEqual((p["suggested_gross_cents"], p["suggested_retainage_cents"], p["suggested_net_cents"]), (172_499, 17_250, 155_249))
        domain.record_sov_progress(state, job["id"], {})
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (172_499, 17_250, 155_249))

    def test_records_and_imports_count_and_typed_figures_do_not_change_the_field_total(self):
        state = new_state()
        job = sov_job(state)
        rough = line(job, "1")["id"]
        daily(state, job, [{"line_id": rough, "qty": "5"}])
        qty_import.apply(state, job["id"], "Item,Quantity\n1,2.5\n", "Owner")
        r = row(period(job, state), rough)
        self.assertEqual((r["qty_this_milli"], r["from_records_milli"], r["record_count"]), (7_500, 7_500, 2))
        self.assertEqual(r["suggested_cents"], 33_750)   # 7.5 x $45
        # Typed 5 on the form: the pay app says 5, the suggestion stays 7.5, and the audit trail has both.
        domain.record_sov_progress(state, job["id"], {rough: "5"}, actor="Pat")
        self.assertEqual(open_draft(job)["sov_lines"][0]["amount_cents"], 22_500)
        r = row(period(job, state), rough)
        self.assertEqual((r["suggested_cents"], r["final_cents"]), (33_750, 22_500))
        self.assertIn("Pat changed the pay app quantity, 1. Rough-in wiring: suggested 7.5 ea ($337.50), final 5 ea ($225.00).", audit_texts(state, "payapp_amount_changed"))

    def test_late_records_and_the_field_stay_apart_from_what_is_billed(self):  # acceptance 25, 27, 28, 29, 30, 31
        state = new_state()
        job = new_job(state, contract=1_800_000, retainage=10)
        domain.save_sov(state, job["id"], [{"item": "1", "description": "Units", "quantity": "400", "unit": "ea", "rate": "45"}], "")
        the_line = sov.lines(job)[0]["id"]
        daily(state, job, [{"line_id": the_line, "qty": "120"}])
        domain.record_sov_progress(state, job["id"], {}, period_end="2026-10-31")
        sent = send_open(state, job)
        self.assertEqual((sent["gross_cents"], sent["retainage_cents"], sent["amount_cents"]), (540_000, 54_000, 486_000))
        daily(state, job, [{"line_id": the_line, "qty": "80"}])
        p = period(job, state)
        self.assertEqual((row(p, the_line)["qty_this_milli"], p["suggested_gross_cents"], p["suggested_net_cents"]), (80_000, 360_000, 324_000))
        # A record for an earlier day, entered late, still counts.
        domain.add_daily_record(state, job["id"], "Forgot this one", details={"today": "2026-10-30", "sections": {"quantities": [{"line_id": the_line, "qty": "10"}]}})
        p = period(job, state)
        self.assertEqual((row(p, the_line)["qty_this_milli"], p["suggested_gross_cents"]), (90_000, 405_000))

    def test_three_small_records_round_once(self):  # acceptance 30
        state = new_state()
        job = new_job(state, contract=833_000, retainage=0)
        domain.save_sov(state, job["id"], [{"item": "1", "description": "Pipe", "quantity": "1000", "unit": "LF", "rate": "8.33"}], "")
        the_line = sov.lines(job)[0]["id"]
        for _ in range(3):
            daily(state, job, [{"line_id": the_line, "qty": "0.5"}])
        self.assertEqual(row(period(job, state), the_line)["suggested_cents"], 1_250)

    def test_override_down_and_up_then_the_next_suggestion(self):  # acceptance 27, 28
        for typed, gross, net_next_units, next_cents in (("4500.00", 450_000, 100_000, 450_000), ("6750.00", 675_000, 50_000, 225_000)):
            state = new_state()
            job = new_job(state, contract=1_800_000, retainage=10)
            domain.save_sov(state, job["id"], [{"item": "1", "description": "Units", "quantity": "400", "unit": "ea", "rate": "45"}], "")
            the_line = sov.lines(job)[0]["id"]
            daily(state, job, [{"line_id": the_line, "qty": "120"}])
            domain.record_sov_progress(state, job["id"], {})
            domain.set_payapp_amounts(state, job["id"], {the_line: typed})
            self.assertEqual(open_draft(job)["gross_cents"], gross)
            send_open(state, job)
            daily(state, job, [{"line_id": the_line, "qty": "80"}])
            r = row(period(job, state), the_line)
            self.assertEqual((r["qty_this_milli"], r["suggested_cents"]), (net_next_units, next_cents))
            domain.record_sov_progress(state, job["id"], {})
            self.assertEqual(open_draft(job)["gross_cents"], next_cents)

    def test_a_record_while_with_the_client_is_suggested_after_their_answer(self):  # acceptance 38
        state = new_state()
        job = sov_job(state)
        rough = line(job, "1")["id"]
        domain.record_sov_progress(state, job["id"], {rough: "100"})
        domain.send_pencil(state, job["id"])
        daily(state, job, [{"line_id": rough, "qty": "50"}])
        domain.record_pencil_response(state, job["id"], True)
        send_open(state, job)
        self.assertEqual(row(period(job, state), rough)["suggested_cents"], 225_000)

    def test_final_then_undo_keeps_what_the_field_recorded(self):  # acceptance 39
        state = new_state()
        job = new_job(state, contract=1_800_000)
        domain.save_sov(state, job["id"], [{"item": "1", "description": "Units", "quantity": "400", "unit": "ea", "rate": "45"}], "")
        the_line = sov.lines(job)[0]["id"]
        daily(state, job, [{"line_id": the_line, "qty": "100"}])
        domain.record_sov_progress(state, job["id"], {})
        send_open(state, job)
        daily(state, job, [{"line_id": the_line, "qty": "50"}])
        domain.record_sov_progress(state, job["id"], {})
        domain.void_draft(state, job["id"])
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 225_000)
        domain.mark_done(state, job["id"], "Owner", "finished")
        self.assertEqual(open_draft(job)["gross_cents"], 1_800_000 - 450_000)
        domain.undo_mark_done(state, job["id"])
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 225_000)
        self.assertEqual(sov.field_total(sov.lines(job)[0]), 150_000)

    def test_over_the_schedule_on_one_line_still_adds_the_other(self):  # acceptance 35
        state = new_state()
        job = new_job(state, contract=1_900_000)
        domain.save_sov(state, job["id"], [{"item": "A", "description": "A", "quantity": "400", "unit": "ea", "rate": "45"},
                                           {"item": "B", "description": "B", "quantity": "100", "unit": "ea", "rate": "10"}], "")
        a, b = [l["id"] for l in sov.lines(job)]
        daily(state, job, [{"line_id": a, "qty": "380"}])
        daily(state, job, [{"line_id": a, "qty": "50"}, {"line_id": b, "qty": "5"}])
        self.assertIn("A. A: 30 ea is over the schedule", state["daily_records"][-1]["quantities_not_applied"])
        p = period(job, state)
        self.assertEqual((row(p, a)["suggested_cents"], row(p, b)["suggested_cents"]), (1_800_000, 5_000))


class OverrideTests(unittest.TestCase):
    def job_at_suggested(self, state):
        job = sov_job(state)
        rough, panel = line(job, "1")["id"], line(job, "2")["id"]
        domain.record_sov_progress(state, job["id"], {rough: "20", panel: "0.125"})
        return job, rough, panel

    def test_the_final_pay_app_uses_the_amount_entered(self):
        state = new_state()
        job, rough, panel = self.job_at_suggested(state)
        number = open_draft(job)["number"]
        domain.set_payapp_amounts(state, job["id"], {rough: "750.55"}, "Pat")
        draft = open_draft(job)
        self.assertEqual(draft["number"], number)
        # $750.55 + $1,500 = $2,250.55; 15% O&P $337.58 (33,758.25 down); $2,588.13 gross.
        self.assertEqual(draft["op_cents"], 33_758)
        self.assertEqual(draft["gross_cents"], 258_813)
        # 10% is $258.813, $258.81 held; $2,329.32 due.
        self.assertEqual((draft["retainage_cents"], draft["amount_cents"]), (25_881, 232_932))
        self.assertEqual(sum(l["amount_cents"] for l in draft["lines"]), 232_932)
        entry = next(e for e in draft["sov_lines"] if e["line_id"] == rough)
        self.assertEqual((entry["suggested_cents"], entry["amount_cents"], entry["to_date_cents"]), (90_000, 75_055, 75_055))
        self.assertIn("(amount adjusted)", next(l for l in draft["lines"] if l.get("sov_line_id") == rough)["label"])
        # The audit trail has who, suggested and final.
        self.assertIn("Pat changed " + number + ", 1. Rough-in wiring: suggested $900.00, final $750.55.", audit_texts(state, "payapp_amount_changed"))
        p = period(job, state)
        self.assertEqual((row(p, rough)["suggested_cents"], row(p, rough)["final_cents"], row(p, rough)["changed"]), (90_000, 75_055, True))
        self.assertEqual((p["suggested_gross_cents"], p["final_gross_cents"], p["final_net_cents"]), (276_000, 258_813, 232_932))
        # It goes out at that amount.
        sent = send_open(state, job)
        self.assertEqual((sent["status"], sent["amount_cents"]), ("sent", 232_932))

    def test_billing_short_is_suggested_again_and_the_job_adds_up(self):
        state = new_state()
        job, rough, panel = self.job_at_suggested(state)
        domain.set_payapp_amounts(state, job["id"], {rough: "750.55"})
        send_open(state, job)
        # Nothing new on the site: the $149.45 left, and the O&P on it, are still to bill.
        p = period(job, state)
        # $750.55 is 16.678 ea at $45: the rest of the 20 recorded is what is still to bill.
        self.assertEqual((row(p, rough)["qty_this_milli"], row(p, rough)["suggested_cents"]), (3_322, 14_945))
        self.assertEqual(p["op_suggested_cents"], 2_242)
        self.assertEqual(p["suggested_gross_cents"], 17_187)
        self.assertEqual(job["money"]["unbilled_completed_cents"] if "money" in job else domain.job_money(job)["unbilled_completed_cents"], 17_187)
        domain.record_sov_progress(state, job["id"], {})   # draft it again, unchanged
        draft = open_draft(job)
        self.assertEqual(draft["gross_cents"], 17_187)
        self.assertIn("(includes an earlier adjustment)", next(l for l in draft["lines"] if l.get("sov_line_id") == rough)["label"])
        # The two pay apps come to what no change at all would have billed: $2,760.
        self.assertEqual(sum(i["gross_cents"] for i in job["invoices"] if i["status"] != "void"), 276_000)

    def test_billing_ahead_stays_on_the_sheet_and_does_not_block_later_progress(self):
        state = new_state()
        job, rough, panel = self.job_at_suggested(state)
        domain.set_payapp_amounts(state, job["id"], {rough: "1000.10"})
        draft = open_draft(job)
        self.assertEqual(draft["gross_cents"], 100_010 + 150_000 + 37_502)   # 15% of $2,500.10 is $375.015, half-up $375.02
        send_open(state, job)
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)
        # The form sends the line's unchanged quantity with the new work on the other one.
        domain.record_sov_progress(state, job["id"], {rough: "22.224", panel: "0.25"})
        draft = open_draft(job)
        entry = next(e for e in draft["sov_sheet"] if e["line_id"] == rough)
        self.assertEqual((entry["amount_cents"], entry["to_date_cents"], entry["prev_work_cents"], entry["work_this_cents"]), (0, 100_010, 100_010, 0))
        # Panel 25% $3,000, less $1,500. O&P: 15% of $1,000.08 + $3,000 is $600.01; less $375.02 = $224.99.
        self.assertEqual(draft["op_cents"], 22_499)
        self.assertEqual(draft["gross_cents"], 150_000 + 22_499)
        # Going below what the earned work was is still refused.
        with self.assertRaises(domain.GateError):
            domain.record_sov_progress(state, job["id"], {rough: "10"})

    def test_the_continuation_sheet_and_pdf_summary_match_the_final_amounts(self):
        state = new_state()
        job, rough, panel = self.job_at_suggested(state)
        domain.record_sov_progress(state, job["id"], {rough: "20", panel: "0.125"}, stored={rough: "100.00"})
        domain.set_payapp_amounts(state, job["id"], {rough: "50.00", panel: "1234.56"})
        draft = open_draft(job)
        for e in draft["sov_sheet"]:
            c = sov.sheet_columns(e, 10)
            self.assertEqual(c["previous"] + c["this_period"] + c["stored"], c["to_date"], e["description"])
        # $50 goes on what is stored and the rest stays off: stored $50, work $0 on line 1.
        entry = next(e for e in draft["sov_sheet"] if e["line_id"] == rough)
        self.assertEqual((entry["stored_cents"], entry["work_this_cents"], entry["to_date_cents"]), (5_000, 0, 5_000))
        figures = payapp_pdf.summary(job, draft)
        self.assertEqual(figures["completed"], sum(e["to_date_cents"] for e in draft["sov_sheet"]) + draft["op_cents"])
        self.assertEqual(figures["completed"], draft["gross_cents"])
        self.assertEqual((figures["retainage"], figures["due"]), (draft["retainage_cents"], draft["amount_cents"]))
        pdf, _name = payapp_pdf.for_invoice(state, job["id"], draft["id"])
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_refusals(self):
        state = new_state()
        job, rough, panel = self.job_at_suggested(state)
        draft = open_draft(job)
        for amounts, words in (
            ({rough: "18000.01"}, "more than the $18,000.00 left"),
            ({rough: "-1"}, "can't be negative"),
            ({rough: "10.005"}, "two decimals"),
            ({"nope": "5"}, "not on this job's schedule"),
        ):
            with self.assertRaises(domain.GateError) as ctx:
                domain.set_payapp_amounts(state, job["id"], amounts)
            self.assertIn(words, " ".join(ctx.exception.blockers))
        with self.assertRaises(domain.GateError) as ctx:
            domain.set_payapp_amounts(state, job["id"], {rough: "900.00"})
        self.assertIn("Nothing changed", str(ctx.exception))
        with self.assertRaises(domain.GateError) as ctx:
            domain.set_payapp_amounts(state, job["id"], {rough: "0", panel: "0"})
        self.assertIn("nothing to bill", str(ctx.exception))
        self.assertIs(open_draft(job), draft, "a refusal leaves the draft as it was")
        domain.send_pencil(state, job["id"])
        with self.assertRaises(domain.GateError) as ctx:
            domain.set_payapp_amounts(state, job["id"], {rough: "1"})
        self.assertIn("with the client", str(ctx.exception))

    def test_a_blank_goes_back_to_the_suggested_amount(self):
        state = new_state()
        job, rough, panel = self.job_at_suggested(state)
        domain.set_payapp_amounts(state, job["id"], {rough: "750.55"})
        domain.set_payapp_amounts(state, job["id"], {rough: ""}, "Pat")
        draft = open_draft(job)
        self.assertEqual(draft["gross_cents"], 276_000)
        self.assertNotIn("sov_overrides", draft)
        self.assertIn(f"Pat changed {draft['number']}, 1. Rough-in wiring: suggested $900.00, final $900.00.", audit_texts(state, "payapp_amount_changed"))

    def test_new_field_quantities_keep_the_amount_and_show_the_new_suggestion(self):
        state = new_state()
        job, rough, panel = self.job_at_suggested(state)
        domain.set_payapp_amounts(state, job["id"], {rough: "750.55"})
        draft = open_draft(job)
        # The record moves the suggestion, and nothing on the draft (acceptance 33a).
        daily(state, job, [{"line_id": rough, "qty": "10"}])
        p = period(job, state)
        r = row(p, rough)
        self.assertEqual((r["suggested_cents"], r["final_cents"]), (135_000, 75_055))
        self.assertIs(open_draft(job), draft)
        self.assertEqual(draft["gross_cents"], 258_813)
        self.assertEqual(p["suggested_gross_cents"], (135_000 + 150_000) * 115 // 100)
        # Progress Pay App picks up what else was recorded and keeps the changed amount.
        domain.record_sov_progress(state, job["id"], {})
        self.assertEqual(open_draft(job)["gross_cents"], 258_813)
        self.assertTrue(any("suggested $1,350.00, final stays $750.55" in t for t in audit_texts(state, "payapp_amount_changed")))

    def test_marking_done_bills_everything_left(self):
        state = new_state()
        job, rough, panel = self.job_at_suggested(state)
        domain.set_payapp_amounts(state, job["id"], {rough: "750.55"})
        domain.mark_done(state, job["id"], "Owner", "finished")
        draft = open_draft(job)
        self.assertEqual(draft["gross_cents"], 3_450_000)
        self.assertTrue(draft["final"])

    def test_a_retainage_change_keeps_the_amounts(self):
        state = new_state()
        job, rough, panel = self.job_at_suggested(state)
        domain.set_payapp_amounts(state, job["id"], {rough: "750.55"})
        domain.edit_job(state, job["id"], {"retainage_pct": 5})
        draft = open_draft(job)
        # 5% of $2,588.13 is $129.4065, half-up $129.41.
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (258_813, 12_941, 245_872))


class AutoLumpSumTests(unittest.TestCase):
    def test_a_new_job_gets_one_line_for_the_contract_amount(self):
        state = new_state()
        job = auto_job(state)
        self.assertEqual(sov.lines(job), [{
            "id": sov.lines(job)[0]["id"], "item": "1", "description": "Contract amount", "unit": "LS", "qty_milli": 1000,
            "rate_cents": 4_850_037, "scheduled_cents": 4_850_037, "done_milli": 0, "stored_cents": 0, "co_id": None, "auto": True,
        }])
        view = domain._sov_view(job, state)
        self.assertTrue(view["present"] and view["auto"] and view["editable"] and view["contract_follows"])
        self.assertEqual(sov.totals(job)["contract_cents"], job["contract_cents"])

    def test_percent_progress_with_retainage(self):
        state = new_state()
        job = auto_job(state)
        the_line = sov.lines(job)[0]["id"]
        domain.record_sov_progress(state, job["id"], {the_line: "0.355"})
        draft = open_draft(job)
        # 35.5% of $48,500.37 is $17,217.63135: $17,217.63; 10% is $1,721.763, $1,721.76 held.
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (1_721_763, 172_176, 1_549_587))
        self.assertEqual(draft["lines"][0]["label"], "1. Contract amount (35% complete to date)")
        self.assertEqual(sum(l["amount_cents"] for l in draft["lines"]), 1_549_587)
        figures = payapp_pdf.summary(job, draft)
        self.assertEqual((figures["contract_to_date"], figures["completed"], figures["retainage"], figures["due"], figures["balance"]),
                         (4_850_037, 1_721_763, 172_176, 1_549_587, 4_850_037 - 1_549_587))
        # A % can't say every draw: the exact draw goes on as the amount.
        domain.set_payapp_amounts(state, job["id"], {the_line: "17500.00"})
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (1_750_000, 175_000, 1_575_000))
        sent = send_open(state, job)
        # The next 50%: 50% of the contract is $24,250.185, half-up $24,250.19; less $17,500.
        domain.record_sov_progress(state, job["id"], {the_line: "0.5"})
        draft = open_draft(job)
        self.assertEqual(draft["gross_cents"], 2_425_019 - 1_750_000)
        self.assertEqual(sent["status"], "sent")

    def test_a_change_order_adds_its_own_line_and_all_of_it_bills_to_the_cent(self):
        state = new_state()
        job = auto_job(state)
        the_line = sov.lines(job)[0]["id"]
        domain.add_change_order(state, job["id"], "Extra circuits", 123_456)
        co = job["change_orders"][-1]
        domain.approve_change_order(state, job["id"], co["id"])
        co_line = next(l for l in sov.lines(job) if l.get("co_id"))
        self.assertEqual((co_line["scheduled_cents"], co_line["done_milli"]), (123_456, 0))
        self.assertTrue(sov.is_auto(job), "the base schedule is still the one line")
        domain.record_sov_progress(state, job["id"], {the_line: "0.5", co_line["id"]: "1"})
        draft = open_draft(job)
        # $24,250.19 (half-up from .185) + $1,234.56 = $25,484.75; 10% is $2,548.475, $2,548.48 held.
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (2_548_475, 254_848, 2_293_627))
        send_open(state, job)
        domain.mark_done(state, job["id"], "Owner", "finished")
        draft = open_draft(job)
        self.assertEqual(draft["gross_cents"], 4_850_037 + 123_456 - 2_548_475)
        self.assertTrue(draft["final"])
        self.assertEqual(sum(i["gross_cents"] for i in job["invoices"] if i["status"] != "void"), 4_973_493)

    def test_the_line_follows_a_contract_edit_until_a_pay_app_is_sent(self):
        state = new_state()
        job = auto_job(state)
        the_line = sov.lines(job)[0]["id"]
        domain.edit_job(state, job["id"], {"contract_cents": 5_000_000})
        self.assertEqual((job["contract_cents"], sov.lines(job)[0]["scheduled_cents"], sov.lines(job)[0]["rate_cents"]), (5_000_000, 5_000_000, 5_000_000))
        domain.record_sov_progress(state, job["id"], {the_line: "0.25"})
        self.assertEqual(open_draft(job)["gross_cents"], 1_250_000)
        number = open_draft(job)["number"]
        # With a draft open, the draft follows too.
        domain.edit_job(state, job["id"], {"contract_cents": 4_000_001})
        draft = open_draft(job)
        # 25% of $40,000.01 is $10,000.0025: $10,000.00; 10% held.
        self.assertEqual((draft["number"], draft["gross_cents"], draft["retainage_cents"]), (number, 1_000_000, 100_000))
        self.assertEqual(sov.totals(job)["contract_cents"], job["contract_cents"])
        send_open(state, job)
        self.assertFalse(domain._sov_view(job, state)["contract_follows"])
        with self.assertRaises(domain.GateError) as ctx:
            domain.edit_job(state, job["id"], {"contract_cents": 5_000_000})
        self.assertIn("Use a change order", " ".join(ctx.exception.fields.values()))
        self.assertEqual(sov.lines(job)[0]["scheduled_cents"], 4_000_001)

    def test_a_job_with_real_lines_does_not_follow(self):
        state = new_state()
        job = sov_job(state)
        with self.assertRaises(domain.GateError):
            domain.edit_job(state, job["id"], {"contract_cents": 1_000_000})

    def test_real_lines_replace_it_and_taking_them_off_brings_it_back(self):
        state = new_state()
        job = auto_job(state, contract=3_450_000)
        domain.save_sov(state, job["id"], LINES, "15")
        self.assertFalse(sov.is_auto(job))
        self.assertEqual([l["description"] for l in sov.lines(job)], ["Rough-in wiring", "Panel and feeders"])
        domain.save_sov(state, job["id"], [], "")
        self.assertTrue(sov.is_auto(job))
        self.assertEqual((sov.lines(job)[0]["scheduled_cents"], sov.op_bp(job)), (3_450_000, 0))

    def test_the_paperwork_backup_is_not_taken_as_made_by_one_line(self):
        state = new_state()
        job = auto_job(state)
        self.assertFalse(domain._made_here("Pay app backup", job))
        real = sov_job(new_state())
        self.assertTrue(domain._made_here("Pay app backup", real))

    def test_quantities_from_a_file_and_a_daily_record_go_on_the_line(self):
        state = new_state()
        job = auto_job(state, retainage=0)
        the_line = sov.lines(job)[0]["id"]
        qty_import.apply(state, job["id"], "Description,Quantity\nContract amount,10\n", "Owner")
        self.assertEqual(sov.field_total(sov.lines(job)[0]), 100)
        daily(state, job, [{"line_id": the_line, "qty": "2.5"}])
        self.assertEqual(sov.field_total(sov.lines(job)[0]), 125)
        # 12.5% of $48,500.37 is $6,062.54625: $6,062.55.
        domain.record_sov_progress(state, job["id"], {})
        self.assertEqual(open_draft(job)["gross_cents"], 606_255)
        self.assertEqual(row(period(job, state), the_line)["record_count"], 2)

    def test_the_pdf_renders(self):
        state = new_state()
        job = auto_job(state)
        domain.record_sov_progress(state, job["id"], {sov.lines(job)[0]["id"]: "0.355"})
        draft = open_draft(job)
        text = domain.pay_app_text(state, job, draft)
        self.assertIn("Contract amount", text)
        self.assertIn("Amount due this application: $15,495.87", text)
        pdf, _name = payapp_pdf.for_invoice(state, job["id"], draft["id"])
        self.assertTrue(pdf.startswith(b"%PDF"))


class AcceptanceMoneyTests(unittest.TestCase):
    def test_exact_lump_sum_bill_then_final(self):  # acceptance 36
        state = new_state()
        job = auto_job(state, contract=25_000_000)
        the_line = sov.lines(job)[0]["id"]
        domain.record_sov_progress(state, job["id"], {the_line: "0.349"})
        domain.set_payapp_amounts(state, job["id"], {the_line: "87345.67"})
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (8_734_567, 873_457, 7_861_110))
        send_open(state, job)
        domain.mark_done(state, job["id"], "Owner", "finished")
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (16_265_433, 1_626_543, 14_638_890))
        live = [i for i in job["invoices"] if i["status"] != "void"]
        self.assertEqual(sum(i["gross_cents"] for i in live), 25_000_000)
        self.assertEqual(sum(i["retainage_cents"] for i in live), 2_500_000)

    def test_three_percents_then_final(self):  # acceptance 37
        state = new_state()
        job = auto_job(state, contract=10_000_001, retainage=0)
        the_line = sov.lines(job)[0]["id"]
        for _ in range(3):
            daily(state, job, [{"line_id": the_line, "qty": "33.3"}])
        self.assertEqual(domain._sov_view(job, state)["lines"][0]["to_date_cents"], 9_990_001)
        domain.record_sov_progress(state, job["id"], {})
        self.assertEqual(open_draft(job)["gross_cents"], 9_990_001)
        send_open(state, job)
        domain.mark_done(state, job["id"], "Owner", "finished")
        self.assertEqual(open_draft(job)["gross_cents"], 10_000)

    def test_override_past_the_balance_names_what_is_left(self):  # acceptance 34
        state = new_state()
        job = auto_job(state, contract=10_000_000, retainage=0)
        the_line = sov.lines(job)[0]["id"]
        domain.record_sov_progress(state, job["id"], {the_line: "0.6"})
        send_open(state, job)
        domain.record_sov_progress(state, job["id"], {the_line: "0.7"})
        with self.assertRaises(domain.GateError) as ctx:
            domain.set_payapp_amounts(state, job["id"], {the_line: "45000"})
        self.assertIn("$40,000.00 left", " ".join(ctx.exception.blockers))

    def test_a_dollar_draft_then_a_schedule_then_final(self):  # acceptance 41
        state = new_state()
        job = new_job(state, contract=10_000_000, retainage=10)
        domain.record_progress(state, job["id"], 5_000_000)
        domain.save_sov(state, job["id"], [{"item": "1", "description": "Work", "quantity": "1", "unit": "LS", "rate": "100000"}], "")
        domain.mark_done(state, job["id"], "Owner", "finished")
        live = [i for i in job["invoices"] if i["status"] != "void"]
        self.assertEqual(sum(i["gross_cents"] for i in live), 10_000_000)

    def test_replacing_the_lump_sum_over_a_draft(self):  # acceptance 40
        state = new_state()
        job = auto_job(state, contract=10_000_000)
        domain.add_change_order(state, job["id"], "Extra", 1_000_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        the_line = sov.lines(job)[0]["id"]
        domain.record_sov_progress(state, job["id"], {the_line: "0.5"})
        lines = [{"item": "1", "description": "A", "quantity": "1", "unit": "LS", "rate": "60000"}, {"item": "2", "description": "B", "quantity": "1", "unit": "LS", "rate": "40000"}]
        domain.save_sov(state, job["id"], lines, "")
        ids = {l["id"] for l in sov.lines(job)}
        self.assertEqual(sov.totals(job)["scheduled_cents"], 11_000_000)
        for inv in sov.live_invoices(job):
            self.assertTrue(all(e["line_id"] in ids for e in inv.get("sov_sheet", [])))
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "1"})
        self.assertEqual(open_draft(job)["gross_cents"], 6_000_000)

    def test_a_credit_change_order_on_a_lump_sum(self):  # acceptance 43
        state = new_state()
        job = auto_job(state, contract=10_000_000)
        the_line = sov.lines(job)[0]["id"]
        domain.record_sov_progress(state, job["id"], {the_line: "0.5"})
        send_open(state, job)
        domain.add_change_order(state, job["id"], "Less work", -500_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        self.assertFalse(any(i["status"] in domain.DRAFT_STATUSES and i["gross_cents"] <= 0 for i in job["invoices"]))
        domain.record_sov_progress(state, job["id"], {the_line: "0.75"})
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (2_000_000, 200_000, 1_800_000))
        send_open(state, job)
        domain.mark_done(state, job["id"], "Owner", "finished")
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (2_500_000, 250_000, 2_250_000))

    def test_an_unsigned_change_order_on_the_bill(self):  # acceptance 42
        state = new_state()
        job = auto_job(state, contract=10_000_000)
        the_line = sov.lines(job)[0]["id"]
        domain.add_change_order(state, job["id"], "Extra", 800_000)
        co = job["change_orders"][-1]
        domain.record_sov_progress(state, job["id"], {the_line: "0.5"})
        self.assertEqual(open_draft(job)["gross_cents"], 5_000_000, "not on the bill until the person says so")
        domain.include_change_order(state, job["id"], co["id"])
        draft = open_draft(job)
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (5_800_000, 580_000, 5_220_000))
        domain.approve_change_order(state, job["id"], co["id"])
        send_open(state, job)
        domain.mark_done(state, job["id"], "Owner", "finished")
        live = [i for i in job["invoices"] if i["status"] != "void"]
        self.assertEqual(sum(i["gross_cents"] for i in live), 10_800_000)


class NewJobScheduleTests(unittest.TestCase):
    TWO = [{"description": "Conduit", "quantity": "100", "unit": "LF", "rate": "50"}, {"description": "Fixtures", "quantity": "10", "unit": "EA", "rate": "500"}]

    def test_lines_at_new_job(self):  # acceptance 14
        state = new_state()
        job = new_job(state, contract=1_000_000, auto_schedule=True, schedule_lines=self.TWO)
        self.assertEqual([l["description"] for l in sov.lines(job)], ["Conduit", "Fixtures"])
        self.assertEqual(sov.totals(job)["contract_cents"], 1_000_000)
        self.assertFalse(sov.is_auto(job))

    def test_lines_that_dont_add_up_save_nothing(self):  # acceptance 15
        state = new_state()
        jobs, payers = len(state["jobs"]), len(state["payers"])
        with self.assertRaises(domain.GateError) as ctx:
            new_job(state, contract=1_500_000, customer="Brand New Client", auto_schedule=True, schedule_lines=self.TWO)
        self.assertEqual(ctx.exception.fields["sov"], "The schedule adds up to $10,000.00, $5,000.00 less than the $15,000.00 contract. Change a line or the contract amount.")
        self.assertEqual((len(state["jobs"]), len(state["payers"])), (jobs, payers))

    def test_a_bad_line_is_refused_under_the_schedule(self):
        state = new_state()
        with self.assertRaises(domain.GateError) as ctx:
            new_job(state, contract=1_000_000, auto_schedule=True, schedule_lines=[{"description": "Conduit", "quantity": "100", "unit": "LF", "rate": ""}])
        self.assertIn("needs a price", ctx.exception.fields["sov"])
        self.assertEqual(state["jobs"], [])


class ScheduleOnADraftTests(unittest.TestCase):
    def test_setting_lines_on_a_job_with_quantities_updates_the_draft(self):
        state = new_state()
        job = sov_job(state)
        rough = line(job, "1")["id"]
        domain.record_sov_progress(state, job["id"], {rough: "20"})
        draft = open_draft(job)
        number, paper = draft["number"], [i["label"] for i in draft["packet"]]
        self.assertEqual(draft["gross_cents"], 103_500)   # 20 x $45 = $900, 15% O&P $135
        # Rate down a dollar, a new line to keep the contract at $34,500: same id, same quantity.
        lines = [
            {"id": rough, "item": "1", "description": "Rough-in wiring", "quantity": "400", "unit": "ea", "rate": "44.00"},
            {"id": line(job, "2")["id"], "item": "2", "description": "Panel and feeders", "quantity": "1", "unit": "LS", "rate": "12000"},
            {"item": "3", "description": "Cleanup", "quantity": "1", "unit": "LS", "rate": "400"},
        ]
        domain.save_sov(state, job["id"], lines, "15")
        draft = open_draft(job)
        self.assertEqual((draft["number"], [i["label"] for i in draft["packet"]]), (number, paper))
        # 20 x $44 = $880; 15% O&P $132; $1,012; 10% retainage $101.20.
        self.assertEqual((draft["gross_cents"], draft["op_cents"], draft["retainage_cents"], draft["amount_cents"]), (101_200, 13_200, 10_120, 91_080))
        self.assertEqual(len([i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES]), 1)

    def test_a_dollar_draft_comes_off_until_there_are_quantities_and_keeps_its_number_and_paperwork(self):
        state = new_state()
        job = new_job(state, contract=3_450_000, retainage=10)
        domain.record_progress(state, job["id"], 100_000)
        draft = open_draft(job)
        number, packet_item = draft["number"], draft["packet"][0]
        domain.resolve_packet_item(state, job["id"], packet_item["id"], reason="in the truck")
        domain.save_sov(state, job["id"], LINES, "15")
        self.assertFalse(any(i["status"] in domain.DRAFT_STATUSES for i in job["invoices"]))
        self.assertEqual(domain.job_money(job)["draft_cents"], 0)
        self.assertTrue(any("comes back when they are entered" in t for t in audit_texts(state, "draft_replaced")))
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "100"})
        draft = open_draft(job)
        self.assertEqual(draft["number"], number)
        self.assertTrue(next(i for i in draft["packet"] if i["label"] == packet_item["label"])["present"])
        # 100 x $45 = $4,500; 15% O&P $675; $5,175; 10% held $517.50.
        self.assertEqual((draft["gross_cents"], draft["retainage_cents"], draft["amount_cents"]), (517_500, 51_750, 465_750))
        self.assertNotIn("sov_replaced_draft", job)

    def test_real_lines_over_a_percent_on_the_auto_line(self):
        state = new_state()
        job = auto_job(state, contract=3_450_000)
        domain.record_sov_progress(state, job["id"], {sov.lines(job)[0]["id"]: "0.1"})
        number = open_draft(job)["number"]
        domain.save_sov(state, job["id"], LINES, "15")
        self.assertFalse(any(i["status"] in domain.DRAFT_STATUSES for i in job["invoices"]))
        domain.record_sov_progress(state, job["id"], {line(job, "2")["id"]: "0.5"})
        self.assertEqual((open_draft(job)["number"], open_draft(job)["gross_cents"]), (number, 690_000))   # $6,000 + 15%

    def test_amounts_on_a_draft_stay_when_the_schedule_changes_and_are_cut_to_the_line(self):
        state = new_state()
        job = sov_job(state)
        rough = line(job, "1")["id"]
        domain.record_sov_progress(state, job["id"], {rough: "20"})
        domain.set_payapp_amounts(state, job["id"], {rough: "15000.25"})
        # The line drops to 300 x $45 = $13,500, and a new line keeps the total.
        lines = [
            {"id": rough, "item": "1", "description": "Rough-in wiring", "quantity": "300", "unit": "ea", "rate": "45.00"},
            {"id": line(job, "2")["id"], "item": "2", "description": "Panel and feeders", "quantity": "1", "unit": "LS", "rate": "16500"},
        ]
        domain.save_sov(state, job["id"], lines, "15")
        draft = open_draft(job)
        entry = next(e for e in draft["sov_lines"] if e["line_id"] == rough)
        self.assertEqual((entry["amount_cents"], entry["suggested_cents"]), (1_350_000, 90_000))
        self.assertTrue(any("more than the line's balance" in t for t in audit_texts(state, "payapp_amount_changed")))

    def test_a_draft_with_an_unsigned_change_order_stays_on_when_the_schedule_is_set(self):
        state = new_state()
        job = new_job(state, contract=3_450_000, retainage=0)
        domain.add_change_order(state, job["id"], "Extra", 12_345)
        domain.include_change_order(state, job["id"], job["change_orders"][-1]["id"])
        domain.draft_invoice(state, job["id"], strict=True)
        domain.save_sov(state, job["id"], LINES, "15")
        self.assertEqual(open_draft(job)["gross_cents"], 12_345)


class OldDataTests(unittest.TestCase):
    def test_old_company_files_still_load(self):
        state = seed_state()
        state.pop("schema_version")
        job = sov_job(state)
        # A job from before: schedule lines without the newer keys, a draft from before amounts.
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "20"})
        for l in job["sov"]["lines"]:
            l.pop("stored_cents", None)
        for key in ("qty_milli_to_date", "work_to_date_cents", "prev_work_cents", "work_this_cents"):
            for e in open_draft(job)["sov_lines"] + open_draft(job)["sov_sheet"]:
                e.pop(key, None)
        state = json.loads(json.dumps(state))
        migrations.migrate(state)
        again = copy.deepcopy(state)
        self.assertFalse(migrations.migrate(again))
        self.assertEqual(again, state)
        snap = domain.snapshot(state)
        for out in snap["jobs"]:
            self.assertIn("sov_view", out)
            self.assertFalse(out["sov_view"].get("auto"), "no job gets an automatic line it didn't have")
            json.dumps(out["sov_view"])
        # Jobs without a schedule keep dollar progress: none gained one.
        before = {j["id"]: bool(j.get("sov")) for j in seed_state()["jobs"]}
        for j in state["jobs"]:
            if j["id"] in before:
                self.assertEqual(bool(j.get("sov")), before[j["id"]], j["name"])

    def test_a_job_without_a_schedule_still_bills_in_dollars(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.record_progress(state, job["id"], 333_333)
        self.assertEqual(open_draft(job)["amount_cents"], 300_000)   # $3,333.33 less $333.33
        self.assertEqual(domain._sov_view(job, state)["present"], False)


if __name__ == "__main__":
    unittest.main()
