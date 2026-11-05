"""The second independent hunt of Nov 5: a refund is money returned, whatever the contract does
afterwards; a signed credit can come off the retainage held."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import exports
from domain import profiles
from test_hunt_nov5_http import Base
from test_money import new_job, new_state, send_open
from waiver_help import signed_waiver


def base(retainage=0, contract=10_000_000):
    state = new_state()
    job = new_job(state, contract=contract, retainage=retainage)
    domain.mark_done(state, job["id"])
    return state, job, send_open(state, job)


def co(state, job, cents):
    domain.add_change_order(state, job["id"], "Change", cents)
    domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])


def waivers_out(state, job):
    for inv in job["invoices"]:
        if inv.get("waiver_due") and not inv.get("waiver_sent"):
            signed_waiver(state, job["id"], domain.suggested_waiver_kind(job, inv))["invoice_id"] = inv["id"]
            domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"])
    for rel in job.get("retainage_waivers", []):
        if rel.get("waiver_due") and not rel.get("waiver_sent"):
            signed_waiver(state, job["id"], domain.release_due_kind(job, rel))["release_id"] = rel["id"]
            domain.send_waiver(state, job["id"], "Sam", release_id=rel["id"])


class RefundIsMoneyReturned(unittest.TestCase):
    def setUp(self):
        self.state, self.job, self.inv = base()
        self.id = self.job["id"]
        domain.match_payment(self.state, self.id, 10_000_000, "c", invoice_id=self.inv["id"])
        co(self.state, self.job, -1_000_000)
        domain.settle_overbilling(self.state, self.id, "refund check 1044")

    def money(self):
        return domain.job_money(self.job)

    def test_received_is_net_and_the_payment_stays(self):
        self.assertEqual(self.money()["collected_cents"], 9_000_000)
        self.assertEqual(self.money()["refunded_cents"], 1_000_000)
        self.assertEqual(sum(p["amount_cents"] for p in self.job["payments"]), 10_000_000)
        self.assertEqual(domain.close_blockers(self.job), [k for k in domain.close_blockers(self.job) if "waiver" in k.lower()])

    def test_a_later_change_order_leaves_it_owed(self):
        co(self.state, self.job, 500_000)
        m = self.money()
        self.assertEqual((m["contract_total_cents"], m["collected_cents"], m["outstanding_cents"], m["refund_owed_cents"], m["overbilled_cents"]),
                         (9_500_000, 9_000_000, 500_000, 500_000, 0))
        item = next(b for b in domain.close_blocker_items(self.job) if b["kind"] == "outstanding")
        self.assertIn("$5,000.00", item["text"])
        self.assertIn("refunded", item["text"])
        with self.assertRaises(domain.GateError):
            domain.close_job(self.state, self.id)
        self.assertFalse(domain.suggested_waiver_kind(self.job, self.inv).endswith("_final"))

    def test_it_can_be_paid_back_and_the_job_finished(self):
        co(self.state, self.job, 500_000)
        with self.assertRaises(domain.GateError):
            domain.match_payment(self.state, self.id, 500_001, "back")
        domain.match_payment(self.state, self.id, 200_000, "back 1")
        self.assertEqual(self.money()["outstanding_cents"], 300_000)
        view = domain.job_view(self.state, self.job)
        self.assertEqual(view["invoices"][-1]["remaining_cents"], 300_000)
        domain.match_payment(self.state, self.id, 300_000, "back 2", invoice_id=self.inv["id"])
        m = self.money()
        self.assertEqual((m["outstanding_cents"], m["overbilled_cents"], m["collected_cents"]), (0, 0, 9_500_000))
        waivers_out(self.state, self.job)
        domain.close_job(self.state, self.id)
        self.assertTrue(self.job["closed"])

    def test_or_the_client_keeps_it_with_a_signed_credit(self):
        co(self.state, self.job, 500_000)
        co(self.state, self.job, -500_000)
        m = self.money()
        self.assertEqual((m["outstanding_cents"], m["overbilled_cents"], m["contract_total_cents"]), (0, 0, 9_000_000))

    def test_a_further_credit_after_paying_back_is_overbilled_again(self):
        co(self.state, self.job, 500_000)
        domain.match_payment(self.state, self.id, 500_000, "back")
        co(self.state, self.job, -500_000)
        self.assertEqual(self.money()["overbilled_cents"], 500_000)
        domain.settle_overbilling(self.state, self.id, "refund 2")
        m = self.money()
        self.assertEqual((m["overbilled_cents"], m["collected_cents"], m["contract_total_cents"]), (0, 9_000_000, 9_000_000))

    def test_csv_and_the_next_pay_app_agree(self):
        co(self.state, self.job, 1_500_000)   # $105,000: $5,000 more to bill and $10,000 owed back
        m = self.money()
        self.assertEqual((m["collected_cents"], m["refund_owed_cents"], m["contract_total_cents"]), (9_000_000, 1_000_000, 10_500_000))
        draft = domain.draft_invoice(self.state, self.id, strict=True)["invoices"][-1]
        figures = domain.payapp_figures(self.job, draft)
        self.assertEqual(figures["received_before"], 9_000_000, "the prior payments the client page and the pay app show")
        state = self.state
        row = next(r for r in exports.jobs_rows(state)[1:] if r[0] == self.job["name"])
        head = exports.jobs_rows(state)[0]
        self.assertEqual(row[head.index("Received")], exports.dollars(9_000_000))
        rows = exports.payments_rows(state)
        amounts = [float(r[5].replace(",", "")) for r in rows[1:]]
        self.assertEqual(round(sum(amounts) * 100), 9_000_000)
        self.assertTrue(any(r[4] == "Refund or credit" for r in rows))
        pay_rows = exports.payapps_rows(state)
        at = pay_rows[0].index("Received")
        self.assertEqual(round(sum(float(r[at].replace(",", "") or 0) for r in pay_rows[1:]) * 100), 9_000_000)

    def test_a_pay_app_sent_before_the_refund_keeps_what_it_showed(self):
        state, job, first = base(contract=20_000_000)
        self.assertEqual(domain.payapp_figures(job, first)["received_before"], 0)

    def test_a_credit_on_a_short_pay_moved_no_money(self):
        state, job, inv = base()
        domain.match_payment(state, job["id"], 9_000_000, "c", invoice_id=inv["id"])
        co(state, job, -1_000_000)
        domain.settle_short_pay(state, job["id"], inv["id"], "credit CO")
        domain.settle_overbilling(state, job["id"], "credit CO")
        m = domain.job_money(job)
        self.assertEqual((m["collected_cents"], m["refunded_cents"], m["overbilled_cents"], m["outstanding_cents"]), (9_000_000, 0, 0, 0))


class CreditOffRetainage(unittest.TestCase):
    def setUp(self):
        self.state, self.job, self.inv = base(retainage=10)
        self.id = self.job["id"]
        domain.match_payment(self.state, self.id, self.inv["amount_cents"], "c", invoice_id=self.inv["id"])
        co(self.state, self.job, -500_000)

    def test_the_hunters_job_reaches_closed(self):
        domain.record_retainage_payment(self.state, self.id, 500_000, "chk")
        with self.assertRaises(domain.GateError) as refused:
            domain.settle_overbilling(self.state, self.id, "credit")
        self.assertIn("retainage", str(refused.exception))
        self.assertNotIn("short pay", str(refused.exception), "there is nothing to short pay on this job")
        self.assertEqual(sorted(b["kind"] for b in domain.close_blocker_items(self.job)), ["overbilled", "retainage", "waiver"])
        before = (len(self.job["payments"]), domain.job_money(self.job)["collected_cents"], domain.job_money(self.job)["retainage_released_cents"], len(self.job.get("retainage_waivers", [])))
        domain.apply_credit_to_retainage(self.state, self.id, "CO 1 comes off retainage", "Sam")
        m = domain.job_money(self.job)
        self.assertEqual((m["retainage_open_cents"], m["overbilled_cents"], m["retainage_credited_cents"]), (0, 0, 500_000))
        self.assertEqual((len(self.job["payments"]), m["collected_cents"], m["retainage_released_cents"], len(self.job.get("retainage_waivers", []))), before, "no money and no waiver")
        row = next(a for a in self.state["audit"] if a["action"] == "retainage_credited")
        self.assertIn("No money was received", row["detail"])
        self.assertIn("CO 1 comes off retainage", row["detail"])
        waivers_out(self.state, self.job)
        domain.close_job(self.state, self.id)

    def test_a_reason_is_required(self):
        with self.assertRaises(domain.GateError) as refused:
            domain.apply_credit_to_retainage(self.state, self.id, " ", "Sam")
        self.assertIn("reason", refused.exception.fields)

    def test_only_up_to_the_credit_and_the_retainage(self):
        with self.assertRaises(domain.GateError) as refused:
            domain.apply_credit_to_retainage(self.state, self.id, "x", "Sam", amount_cents=500_001)
        self.assertIn("amount_cents", refused.exception.fields)
        domain.apply_credit_to_retainage(self.state, self.id, "x", "Sam", amount_cents=200_000)
        self.assertEqual(domain.job_money(self.job)["retainage_open_cents"], 800_000)
        self.assertEqual(domain.retainage_credit_room(self.job), 300_000)

    def test_a_credit_bigger_than_the_retainage(self):
        co(self.state, self.job, -1_000_000)   # $15,000 of credit on $10,000 held; $5,000 came in above
        domain.apply_credit_to_retainage(self.state, self.id, "x", "Sam")
        domain.settle_overbilling(self.state, self.id, "refund")
        m = domain.job_money(self.job)
        self.assertEqual((m["retainage_open_cents"], m["overbilled_cents"], m["collected_cents"]), (0, 0, 8_500_000))

    def test_nothing_overbilled_nothing_to_take_off(self):
        co(self.state, self.job, 500_000)
        with self.assertRaises(domain.GateError):
            domain.apply_credit_to_retainage(self.state, self.id, "x", "Sam")

    def test_a_later_change_order_brings_the_retainage_back(self):
        domain.apply_credit_to_retainage(self.state, self.id, "x", "Sam")
        co(self.state, self.job, 500_000)
        m = domain.job_money(self.job)
        self.assertEqual((m["retainage_open_cents"], m["overbilled_cents"]), (1_000_000, 0))


class CreditOffRetainageByProfile(Base):
    def test_it_takes_record_payments(self):
        self.assertEqual(profiles.JOB_ACTION_PERMS["retainage-credit"], "record_payments")
        status, data = self.post(f"/api/jobs/{self.job_id}/retainage-credit", {"reason": "x"}, cookie=self.bob)
        self.assertEqual(status, 409, data)   # Member holds it by default: it gets as far as "none held"
        self.assertIn("retainage", data["error"])


if __name__ == "__main__":
    unittest.main()
