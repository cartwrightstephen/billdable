"""Random sequences of what people do to a job, with the money checked after every step: exact
cents, the job never billed past its contract and signed change orders without saying so, each pay
app's lines adding up, a final pay app leaving nothing unbilled, and sent pay apps never changing.
It takes payments too: refunds of overbilling, retainage paid, short pay, pay in full and waivers
sent, with a final waiver never fitting while anything is left, and a draft never left out of step
with whether it is the final one, which is what refused a send with no way forward.
A fixed seed, so a failure repeats. MONEY_FUZZ_RUNS=5000 runs far more of them."""

import copy
import os
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import payapp_pdf
import qty_import
import sov
from test_money import new_job, new_state
from waiver_help import signed_waiver

RUNS = int(os.environ.get("MONEY_FUZZ_RUNS", "1000"))
SEED = int(os.environ.get("MONEY_FUZZ_SEED", "20261105"))
STEPS = 30
SENT_KEYS = ("number", "amount_cents", "gross_cents", "retainage_cents", "lines", "sov_lines", "sov_sheet", "op_cents", "packet", "sent_at", "final", "contract_at_send")
SHAPES = (
    [{"item": "1", "description": "Rough", "quantity": "100", "unit": "ea", "rate": "100"}, {"item": "2", "description": "Panel", "quantity": "1", "unit": "LS", "rate": "10000"}],
    [{"item": "1", "description": "Rough-in", "quantity": "400", "unit": "ea", "rate": "45"}],
    [{"item": "1", "description": "Tile", "quantity": "1000", "unit": "LF", "rate": "8.33"}, {"item": "2", "description": "Grout", "quantity": "33.5", "unit": "sf", "rate": "19.99"}, {"item": "3", "description": "Trim", "quantity": "1", "unit": "LS", "rate": "1234.56"}],
)


def draft_of(job):
    return next((i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES), None)


class Sequence:
    def __init__(self, rng):
        self.rng = rng
        self.state = new_state()
        self.log = []
        self.sent = {}
        shape = rng.choice(SHAPES + (None,))
        retainage = rng.choice((0, 5, 10))
        if shape is None:
            self.job = new_job(self.state, contract=rng.choice((1_000_000, 4_850_037, 10_000_001)), retainage=retainage, auto_schedule=True)
        else:
            self.job = new_job(self.state, contract=1, retainage=retainage)
            bp = rng.choice(("0", "15", "12.5"))
            domain.save_sov(self.state, self.job["id"], shape, bp, update_contract=True)
        self.id = self.job["id"]
        self.credit_signed = False

    def line(self):
        return self.rng.choice([l for l in sov.lines(self.job)])

    def qty(self, line, whole=False):
        if line["unit"].upper() == "LS" and line["qty_milli"] == 1000:
            return f"{self.rng.randint(0, 1000 if whole else 400) / 1000:.3f}"
        return f"{self.rng.randint(0, line['qty_milli'] if whole else line['qty_milli'] // 2) / 1000:.3f}"

    # ---------- what people do ----------
    def daily(self):
        line = self.line()
        if line["unit"].upper() == "LS" and line["qty_milli"] == 1000:
            qty = f"{self.rng.randint(1, 40)}%"
        else:
            qty = f"{self.rng.randint(1, max(1, line['qty_milli'] // 4)) / 1000:.3f}"
        domain.add_daily_record(self.state, self.id, "Work", details={"sections": {"quantities": [{"line_id": line["id"], "qty": qty}]}})

    def import_file(self):
        line = self.line()
        if line.get("co_id"):
            return
        qty = f"{self.rng.randint(1, 40)}%" if line["unit"].upper() == "LS" and line["qty_milli"] == 1000 else f"{self.rng.randint(1, max(1, line['qty_milli'] // 4000))}"
        qty_import.apply(self.state, self.id, f"{line['item']},{line['description']},{qty}", "Sam", "q.csv")

    def undo_import(self):
        records = [r for r in self.job.get("records", []) if r.get("quantities")]
        if records:
            domain.undo_quantity_import(self.state, self.id, self.rng.choice(records)["id"], "Sam", below_billed=self.rng.random() < 0.7)

    def correct(self):
        line = self.line()
        domain.correct_field_total(self.state, self.id, line["id"], self.qty(line, True), "Wrong", "Sam", below_billed=self.rng.random() < 0.5)

    def progress_blank(self):
        blank = draft_of(self.job) is None
        view = domain._sov_view(self.job, self.state)
        want = (view.get("period") or {}).get("suggested_gross_cents")
        domain.record_sov_progress(self.state, self.id, {})
        made = draft_of(self.job)
        if blank and made and not self.job.get("sov_replaced_draft"):
            assert made["gross_cents"] == want, f"blank bills {made['gross_cents']}, the card said {want}"

    def progress_typed(self):
        lines = sov.lines(self.job)
        line = self.rng.choice(lines)
        domain.record_sov_progress(self.state, self.id, {line["id"]: self.qty(line, True)})

    def amounts(self):
        line = self.line()
        domain.set_payapp_amounts(self.state, self.id, {line["id"]: f"{self.rng.randint(0, max(0, line['scheduled_cents'])) / 100:.2f}"})

    def reset(self):
        period = domain._sov_view(self.job, self.state).get("period") or {}
        changed = [l["line_id"] for l in period.get("lines", []) if l["changed"]]
        if changed:
            domain.set_payapp_amounts(self.state, self.id, {k: "" for k in changed})

    def send(self):
        draft = draft_of(self.job)
        if not draft:
            return
        for item in draft["packet"]:
            if item["required"] and not item["present"]:
                domain.resolve_packet_item(self.state, self.id, item["id"], "Sam", reason="fuzz")
        ready = not domain.blocker_lines(self.job)
        try:
            domain.approve_and_send(self.state, self.id, "Sam", reviewed=domain.review_stamp(self.state, self.job, draft))
        except domain.GateError as exc:
            assert not ready, f"{draft['number']} showed nothing in the way and was refused: {exc} {exc.blockers}"
            raise
        if draft["final"]:
            assert domain.job_money(self.job)["unbilled_completed_cents"] == 0, "final, with work left to bill"

    def waiver_confirmed(self):
        """A person looks at a signed conditional waiver and confirms it: what a change to whether
        the draft is the final one has to ask them about again."""
        draft = draft_of(self.job)
        item = next((i for i in (draft or {}).get("packet") or [] if str(i.get("waiver_kind", "")).startswith("conditional") and i["required"] and not i["present"]), None)
        if item and not domain.unconditional_waivers_attached(self.job, draft):
            rec = signed_waiver(self.state, self.id, item["waiver_kind"])
            domain.attach_record_to_packet(self.state, self.id, item["id"], rec["id"], "Sam")
            if not item["present"]:
                domain.confirm_packet_item(self.state, self.id, item["id"], "Sam", looked=True)

    def stored(self):
        line = self.line()
        if line["scheduled_cents"] > 0:
            domain.record_sov_progress(self.state, self.id, {}, stored={line["id"]: f"{self.rng.randint(0, line['scheduled_cents']) / 100:.2f}"})

    def settle(self):
        domain.settle_overbilling(self.state, self.id, "Refund")

    def retainage_credit(self):
        domain.apply_credit_to_retainage(self.state, self.id, "Credit comes off retainage", "Sam",
                                         amount_cents=None if self.rng.random() < 0.5 else max(1, domain.retainage_credit_room(self.job) // 2))

    def pay_back(self):
        owed = domain.job_money(self.job)["refund_owed_cents"]
        if owed and not self.open_to_pay():
            domain.match_payment(self.state, self.id, self.rng.choice((owed, max(1, owed // 2))), "")

    def retainage_paid(self):
        left = domain.job_money(self.job)["retainage_open_cents"]
        if left:
            domain.record_retainage_payment(self.state, self.id, self.rng.choice((left, max(1, left // 2))), "Check")

    def open_to_pay(self):
        return [i for i in self.job["invoices"] if i["status"] in domain.OPEN_FOR_PAYMENT]

    def pay_in_full(self):
        for inv in self.open_to_pay():
            domain.match_payment(self.state, self.id, domain._owed_on(self.job, inv), "", invoice_id=inv["id"])

    def short_pay(self):
        partial = [i for i in self.open_to_pay() if i["status"] == "partial"]
        if partial:
            domain.settle_short_pay(self.state, self.id, partial[0]["id"], "Backcharge")

    def send_waiver(self):
        owed = [i for i in self.job["invoices"] if i.get("waiver_due") and not i.get("waiver_sent")]
        releases = [r for r in self.job.get("retainage_waivers", []) if r.get("waiver_due") and not r.get("waiver_sent")]
        if owed:
            kind = domain.suggested_waiver_kind(self.job, owed[0])
            signed_waiver(self.state, self.id, kind)["invoice_id"] = owed[0]["id"]
            domain.send_waiver(self.state, self.id, "Sam", invoice_id=owed[0]["id"])
        elif releases:
            kind = domain.release_due_kind(self.job, releases[0])
            signed_waiver(self.state, self.id, kind)["release_id"] = releases[0]["id"]
            domain.send_waiver(self.state, self.id, "Sam", release_id=releases[0]["id"])

    def delete(self):
        domain.void_draft(self.state, self.id)

    def final(self):
        domain.mark_done(self.state, self.id)

    def undo(self):
        domain.undo_mark_done(self.state, self.id)

    def pencil(self):
        domain.send_pencil(self.state, self.id)

    def pencil_answer(self):
        domain.record_pencil_response(self.state, self.id, self.rng.random() < 0.5, "note")

    def add_co(self):
        cents = self.rng.choice((50_000, 123_456, 1_000, -5_000, -50_000))
        domain.add_change_order(self.state, self.id, "Change", cents)

    def co(self, status="pending"):
        found = [c for c in self.job["change_orders"] if c["status"] == status]
        return self.rng.choice(found) if found else None

    def sign_co(self):
        co = self.co()
        if co:
            domain.approve_change_order(self.state, self.id, co["id"])
            self.credit_signed = self.credit_signed or co["amount_cents"] < 0

    def include_co(self):
        co = self.co()
        if co:
            domain.include_change_order(self.state, self.id, co["id"])

    def withdraw_co(self):
        co = self.co()
        if co:
            domain.withdraw_change_order(self.state, self.id, co["id"])

    def retainage(self):
        domain.edit_job(self.state, self.id, {"retainage_pct": self.rng.choice((0, 5, 10))})

    def contract(self):
        domain.edit_job(self.state, self.id, {"contract_cents": self.rng.choice((500_000, 1_000_000, 7_777_777))})

    def replace_lines(self):
        shape = self.rng.choice(SHAPES)
        domain.save_sov(self.state, self.id, shape, "0", update_contract=True)

    def partial_pay(self):
        open_ones = [i for i in self.job["invoices"] if i["status"] in {"sent", "approved", "partial"}]
        if open_ones:
            inv = self.rng.choice(open_ones)
            domain.match_payment(self.state, self.id, self.rng.randint(1, max(1, inv["amount_cents"])), "chk")

    def pull_back(self):
        sent = [i for i in self.job["invoices"] if i["status"] in {"sent", "approved"}]
        if sent:
            domain.void_sent_pay_app(self.state, self.id, sent[-1]["id"], "Wrong quantity")

    ACTIONS = ("daily", "daily", "daily", "import_file", "undo_import", "correct", "progress_blank", "progress_blank", "progress_typed", "amounts", "reset", "send", "send", "delete",
               "final", "undo", "pencil", "pencil_answer", "add_co", "sign_co", "include_co", "withdraw_co", "retainage", "contract", "replace_lines", "partial_pay", "pull_back",
               "waiver_confirmed", "waiver_confirmed", "stored", "settle", "settle", "retainage_paid", "pay_in_full", "pay_in_full", "short_pay", "send_waiver", "send_waiver", "retainage_credit", "pay_back")

    # ---------- what must hold ----------
    def check(self):
        job = self.job
        m = domain.job_money(job)
        for key, value in m.items():
            assert isinstance(value, int), f"{key} is {value!r}"
        live = sov.live_invoices(job)
        sov_lines = {l["id"]: l for l in sov.lines(job)} if sov.has_sov(job) else {}
        for inv in live:
            what = inv["number"]
            assert inv["gross_cents"] - inv["retainage_cents"] == inv["amount_cents"], f"{what}: gross less retainage"
            assert sum(l["amount_cents"] for l in inv["lines"]) == inv["amount_cents"], f"{what}: its lines don't add up to it"
            assert inv["amount_cents"] > 0 or inv["gross_cents"] > 0, f"{what}: nothing billed"
            if inv.get("sov_sheet"):
                billed = sum(e["amount_cents"] for e in inv["sov_lines"]) + inv.get("op_cents", 0) + sum(l["amount_cents"] for l in inv["lines"] if l.get("co_id"))
                assert billed == inv["gross_cents"], f"{what}: schedule lines {billed}, gross {inv['gross_cents']}"
                for e in inv["sov_sheet"]:
                    assert isinstance(e["to_date_cents"], int) and isinstance(e["amount_cents"], int), f"{what}: not whole cents"
                # G703: previous + this period + stored = total to date, line by line. What a pay app
                # bills is the work this period and the change in materials stored (stored to date
                # is a balance: what an earlier pay app stored is in its gross, not this one's). The
                # previous column starts from the work the last sheet showed, and it can only be
                # less when that was billed ahead of the quantities on the line (a typed amount, a
                # field total corrected down): nothing new is called work until they catch up.
                stored_before, work_before = {}, {}
                for earlier in live[:live.index(inv)]:
                    for e in earlier.get("sov_sheet") or []:
                        stored_before[e["line_id"]] = int(e.get("stored_cents") or 0)
                        work_before[e["line_id"]] = int(e.get("work_to_date_cents", e["to_date_cents"]))
                this = inv.get("op_cents", 0) + sum(r["this_cents"] for r in sov.co_rows(job, inv))
                for e in inv["sov_sheet"]:
                    row = sov.sheet_columns(e)
                    behind = 0
                    if e["scheduled_cents"] >= 0:
                        assert row["previous"] + row["this_period"] + row["stored"] == row["to_date"], f"{what}, {e['description']}: {row}"
                        # A change order billed on its own row before it was signed has no sheet line to look back to.
                        carried_co = e["line_id"] not in work_before and row["previous"] and sov_lines.get(e["line_id"], {}).get("co_id")
                        behind = 0 if carried_co else work_before.get(e["line_id"], 0) - row["previous"]
                        assert behind <= 0 or row["this_period"] == 0, f"{what}, {e['description']}: previous is {behind} under the last sheet, with new work on it"
                    this += row["this_period"] + row["stored"] - stored_before.get(e["line_id"], 0) - behind
                assert this == inv["gross_cents"], f"{what}: the sheet's This period and change in stored is {this}, gross {inv['gross_cents']}"
        self.check_waivers_and_refunds(m)
        by_line = sov.billed(job)["by_line"]
        for line in sov.lines(job):
            if line["scheduled_cents"] >= 0:
                assert by_line.get(line["id"], 0) <= line["scheduled_cents"], f"{line['description']} billed past its line"
        total = m["contract_total_cents"] + m["pending_co_cents"] * 0 + sum(c["amount_cents"] for c in job["change_orders"] if c["status"] == "pending" and c.get("included_in_billing"))
        if not self.credit_signed:
            assert m["billed_gross_cents"] <= total, f"billed {m['billed_gross_cents']} on {total} with no credit"
            draft = draft_of(job)
            if draft and not domain._with_client(draft):
                assert m["billed_gross_cents"] + draft["gross_cents"] <= total, f"with the draft, billed past {total}"
        if m["billed_gross_cents"] > total:
            assert any(b["kind"] == "overbilled" for b in domain.close_blocker_items(job)) or m["overbilled_cents"] == 0, "overbilled and no word of it"
        draft = draft_of(job)
        if draft and draft.get("final") and not domain._with_client(draft):
            assert m["unbilled_completed_cents"] == 0, f"{draft['number']} says final with {m['unbilled_completed_cents']} left to bill"
        if job.get("work_status") == "complete" and not draft and m["unbilled_completed_cents"] == 0 and not self.credit_signed:
            whole = sum(int(i["gross_cents"]) for i in job["invoices"] if i["status"] in domain.BILLED_STATUSES)
            pending = any(c["status"] == "pending" and c.get("included_in_billing") for c in job["change_orders"])
            assert pending or whole == m["contract_total_cents"], f"finished with {whole} billed on {m['contract_total_cents']}"
        for inv in job["invoices"]:
            if inv["status"] in domain.BILLED_STATUSES:
                now = {k: copy.deepcopy(inv.get(k)) for k in SENT_KEYS}
                if inv["id"] not in self.sent:
                    self.sent[inv["id"]] = now
                assert now == self.sent[inv["id"]], f"{inv['number']} changed after it was sent"

    def check_waivers_and_refunds(self, m):
        job = self.job
        # The open draft is in step with the job: a send can't find out only then that it changed.
        draft = draft_of(job)
        if draft and draft.get("packet_id") != "job-packet":
            assert draft["final"] == domain.is_final(job), f"{draft['number']} says final {draft['final']}, the job says {domain.is_final(job)}"
            trial = copy.deepcopy(draft)
            flipped = domain._name_conditional_waiver(job, trial)
            assert not flipped, f"{draft['number']}: {flipped[0]['label']} would flip only at send"
        # A final waiver only with nothing else to bill or collect.
        for inv in job["invoices"]:
            if inv["status"] not in domain.BILLED_STATUSES or not domain.suggested_waiver_kind(job, inv).endswith("_final"):
                continue
            own = domain._owed_on(job, inv) if inv["status"] in domain.OPEN_FOR_PAYMENT else 0
            assert job.get("work_status") == "complete" and m["unbilled_completed_cents"] == 0 and m["draft_cents"] == 0 \
                and m["outstanding_cents"] <= own and m["retainage_open_cents"] == 0 \
                and not any(c["status"] == "pending" for c in job["change_orders"]), f"final waiver for {inv['number']} with {m}"
        # A refund or credit counts only for what came in, and it stays money returned: whatever the
        # contract does afterwards, what is left to bill, collect or refund adds up to it.
        came_in = m["payments_cents"] + m["short_paid_cents"] + m["retainage_released_cents"]
        allowed = m["billing_allowed_cents"]
        assert m["overbilling_settled_cents"] <= came_in, f"settled {m['overbilling_settled_cents']} of {came_in}"
        assert m["collected_cents"] == m["payments_cents"] - min(m["payments_cents"], m["refunded_cents"]), f"received {m}"
        assert m["overbilled_cents"] == max(0, m["billed_gross_cents"] - allowed - m["overbilling_settled_cents"] - m["retainage_credited_cents"]), f"overbilled {m}"
        if not domain.close_blocker_items(job):
            assert m["payments_cents"] + m["retainage_released_cents"] + m["short_paid_cents"] - m["refunded_cents"] >= allowed, f"nothing in the way of finishing, and money is still due: {m}"
        if m["overbilled_cents"]:
            assert any(b["kind"] == "overbilled" for b in domain.close_blocker_items(job)), "overbilled and no word of it"
        # A credit never suggests less than nothing.
        if sov.has_sov(job):
            assert (domain._sov_view(job, self.state).get("period") or {}).get("suggested_gross_cents", 0) >= 0, "a negative suggestion"

    # ---------- getting it to Finish Job from wherever it ended up ----------
    def finish(self):
        """Only what a person can do on the job page: whatever state the job is in, it reaches closed."""
        for _ in range(60):
            items = domain.close_blocker_items(self.job)
            if not items:
                domain.close_job(self.state, self.id, "Sam")
                self.check()
                return
            kind = items[0]["kind"]
            self.log.append(f"finish:{kind}")
            draft = draft_of(self.job)
            if draft and draft.get("pencil_status") == "sent":
                domain.record_pencil_response(self.state, self.id, True, "Fine")
            getattr(self, f"clear_{kind}")(items[0])
            self.check()
        raise AssertionError(f"not finished: {domain.close_blockers(self.job)}")

    def clear_not_done(self, item):
        domain.mark_done(self.state, self.id)

    def clear_draft(self, item):
        for co in domain.unsigned_cos_in_billing(self.job):
            domain.approve_change_order(self.state, self.id, co["id"])
            self.credit_signed = self.credit_signed or co["amount_cents"] < 0
        self.send()

    def clear_co(self, item):
        if self.rng.random() < 0.5:
            domain.withdraw_change_order(self.state, self.id, item["co_id"])
        else:
            domain.approve_change_order(self.state, self.id, item["co_id"])
            self.credit_signed = self.credit_signed or next(c for c in self.job["change_orders"] if c["id"] == item["co_id"])["amount_cents"] < 0

    def clear_unbilled(self, item):
        domain.draft_invoice(self.state, self.id, strict=True)

    def clear_outstanding(self, item):
        if self.open_to_pay():
            if self.rng.random() < 0.5:
                self.pay_in_full()
            else:
                inv = self.open_to_pay()[0]
                domain.match_payment(self.state, self.id, max(1, domain._owed_on(self.job, inv) // 2), "", invoice_id=inv["id"])
                self.short_pay() if self.rng.random() < 0.3 else None
        else:
            self.pay_back()

    def clear_retainage(self, item):
        left = domain.job_money(self.job)["retainage_open_cents"]
        domain.record_retainage_payment(self.state, self.id, self.rng.choice((left, max(1, left // 2))), "Check")

    def clear_waiver(self, item):
        self.send_waiver()

    def clear_overbilled(self, item):
        m = domain.job_money(self.job)
        if m["overbilling_settleable_cents"]:
            return self.settle()
        if domain.retainage_credit_room(self.job, m):
            return self.retainage_credit()
        open_ones = self.open_to_pay()
        part = next((i for i in open_ones if i["status"] == "partial"), None)
        if part:
            return domain.settle_short_pay(self.state, self.id, part["id"], "Credit")
        unpaid = next((i for i in reversed(open_ones)), None)
        if unpaid:
            return domain.void_sent_pay_app(self.state, self.id, unpaid["id"], "Over the contract")
        raise AssertionError(f"overbilled with no way out: {m}")

    def step(self):
        name = self.rng.choice(self.ACTIONS)
        self.log.append(name)
        try:
            getattr(self, name)()
        except domain.GateError:
            self.log[-1] += " (refused)"
        self.check()


class FuzzTests(unittest.TestCase):
    def test_the_money_holds(self):
        master = random.Random(SEED)
        for run in range(RUNS):
            seed = master.randrange(1 << 30)
            seq = Sequence(random.Random(seed))
            try:
                for _ in range(STEPS):
                    seq.step()
                domain.job_view(seq.state, seq.job)
                for inv in sov.live_invoices(seq.job):
                    payapp_pdf.for_invoice(seq.state, seq.id, inv["id"])
            except Exception as exc:
                self.fail(f"run {run}, seed {seed}: {type(exc).__name__}: {exc}\nsteps: {', '.join(seq.log)}")

    def test_every_job_can_be_finished(self):
        """Whatever a run left, retainage part-released and credits included, it reaches closed."""
        master = random.Random(SEED + 1)
        for run in range(RUNS):
            seed = master.randrange(1 << 30)
            seq = Sequence(random.Random(seed))
            try:
                for _ in range(seq.rng.randint(0, STEPS)):
                    seq.step()
                seq.finish()
                assert seq.job["closed"]
            except Exception as exc:
                self.fail(f"run {run}, seed {seed}: {type(exc).__name__}: {exc}\nsteps: {', '.join(seq.log)}")


if __name__ == "__main__":
    unittest.main()
