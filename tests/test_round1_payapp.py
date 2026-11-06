"""Round 1 of the Nov 5 audits, the pay app part, and the Nov 6 final review's money items: a signed
credit off retainage owes no second final waiver, and a refund paid back stays paid back once more
work is billed."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
from test_money import new_job, new_state, send_open


def signed_co(state, job, cents):
    domain.add_change_order(state, job["id"], "Change", cents)
    domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])


def finals_owed(job):
    return [x for x in domain._pending_waivers(job)
            if (domain.release_due_kind(job, x) if "reference" in x else domain.suggested_waiver_kind(job, x)).endswith("_final")]


class CreditWithTheFinalWaiverAlreadyDue(unittest.TestCase):
    """Nov 6 review B: the pay app's own waiver, due and not sent, becomes the final one."""

    def test_a_paid_final_pay_app_whose_waiver_is_unsent_covers_the_credit(self):
        s = new_state(); j = new_job(s, contract=1_000_000, retainage=10)
        domain.mark_done(s, j["id"]); inv = send_open(s, j)
        domain.match_payment(s, j["id"], inv["amount_cents"], "", invoice_id=inv["id"])
        self.assertTrue(inv["waiver_due"])
        signed_co(s, j, -100_000)
        domain.apply_credit_to_retainage(s, j["id"], "CO credit off retainage", "Sam")
        self.assertEqual(j.get("retainage_waivers") or [], [], "no second release to carry a final waiver")
        self.assertEqual([x["id"] for x in finals_owed(j)], [inv["id"]])

    def test_with_part_of_the_retainage_paid_one_final_waiver_is_owed(self):
        s = new_state(); j = new_job(s, contract=1_000_000, retainage=10)
        domain.mark_done(s, j["id"]); inv = send_open(s, j)
        domain.match_payment(s, j["id"], inv["amount_cents"], "", invoice_id=inv["id"])
        domain.record_retainage_payment(s, j["id"], 60_000, "part")
        signed_co(s, j, -40_000)
        domain.apply_credit_to_retainage(s, j["id"], "CO credit off retainage", "Sam")
        self.assertEqual(len(finals_owed(j)), 1)
        self.assertFalse(any(r.get("credit") for r in j.get("retainage_waivers", [])))


class RefundPaidBackThenMoreBilled(unittest.TestCase):
    """Nov 6 review C: the pay-back stays recorded; what is owed is the new pay app, not the refund."""

    def test_the_split_stays_on_the_pay_apps(self):
        s = new_state(); j = new_job(s, contract=1_000_000)
        domain.record_progress(s, j["id"], 600_000)
        inv = send_open(s, j)
        domain.match_payment(s, j["id"], 600_000, "", invoice_id=inv["id"])
        signed_co(s, j, -500_000)
        domain.settle_overbilling(s, j["id"], "refund")
        signed_co(s, j, 500_000)
        self.assertEqual(domain.job_money(j)["refund_owed_cents"], 100_000)
        domain.match_payment(s, j["id"], 100_000, "")
        self.assertEqual(domain.job_money(j)["refund_owed_cents"], 0)
        domain.record_progress(s, j["id"], 1_000_000)
        send_open(s, j)
        m = domain.job_money(j)
        self.assertEqual((m["outstanding_cents"], m["refund_owed_cents"]), (400_000, 0))
        self.assertEqual(domain._owed_text(m), "$4,000.00 is still owed on pay apps sent.")


if __name__ == "__main__":
    unittest.main()
