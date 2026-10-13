"""Results: what Billdable did for a company, in numbers (P2-10)."""

import datetime as dt
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import metrics
from seed import seed_state

TODAY = dt.date(2026, 10, 13)


class MetricsTests(unittest.TestCase):
    def test_all_time_numbers_from_the_demo(self):
        m = metrics.compute(seed_state(), today=TODAY)
        # Done -> sent: TRN 0, PON 1, DEC 2, BRK 1, MPL 1, CDR 1, WLS 1 days.
        self.assertEqual((m["done_to_sent_median_days"], m["done_to_sent_jobs"]), (1, 7))
        self.assertEqual([(w["job"], w["days"]) for w in m["waiting_to_bill"]], [("Oak Street kitchen", 11), ("Inman Park bath", 5)])
        # Sent -> paid: 16, 21, 24, 16, 5, 7 days.
        self.assertEqual((m["sent_to_paid_median_days"], m["sent_to_paid_average_days"], m["pay_apps_paid"]), (16.0, 14.8, 6))
        self.assertEqual(m["by_payer"], [{"payer": "Ellison Residential", "average_days": 15.8, "paid": 4},
                                         {"payer": "Ponce Property Co.", "average_days": 13.0, "paid": 2}])
        self.assertEqual((m["pay_apps_sent"], m["change_orders_billed_cents"], m["sent_back"], m["follow_ups"]), (8, 1_800_000, 0, 0))

    def test_period_counts_only_pay_apps_sent_since(self):
        m = metrics.compute(seed_state(), today=TODAY, since=dt.date(2026, 9, 1))
        self.assertEqual((m["pay_apps_sent"], m["billed_cents"]), (3, 2_860_000 + 11_970_000 + 5_795_000))
        self.assertEqual(m["pay_apps_paid"], 1)

    def test_rejections_count_as_sent_back(self):
        state = seed_state()
        import domain
        domain.learn_rejection(state, state["jobs"][0]["id"], "needs lien waiver from supplier", "owner")
        self.assertEqual(metrics.compute(state, today=TODAY)["sent_back"], 1)

    def test_month_summary(self):
        subject, body = metrics.month_summary(seed_state(), 2026, 9)
        self.assertEqual(subject, "September 2026: what Billdable did for Hartline Remodeling")
        self.assertIn("Pay apps sent: 2, for $148,300.00.", body)
        self.assertIn("Payments received: 1, for $28,600.00; on average 21.0 days", body)
        self.assertNotIn("-1 days", body, "jobs finished after the month don't appear")

    def test_admin_sends_to_verified_owners_only(self):
        import admin
        users = [{"company_id": "co-pine", "role": "owner", "email_verified": True, "email": "own@pine.example"},
                 {"company_id": "co-pine", "role": "member", "email_verified": True, "email": "mem@pine.example"},
                 {"company_id": "co-pine", "role": "owner", "email_verified": False, "email": "new@pine.example"}]
        sent = []
        with mock.patch.object(admin, "_companies", return_value=[{"id": "co-pine", "name": "Pine", "users": 3, "jobs": 0}]), \
                mock.patch.object(admin.auth, "all_users", return_value=users), \
                mock.patch.object(admin.server, "load_company", return_value=seed_state()), \
                mock.patch.object(admin.server, "send_system_email", side_effect=lambda to, s, b, c: sent.append(to) or "sent"), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(admin.main(["admin.py", "monthly-summaries", "2026-09"]), 0)
        self.assertEqual(sent, ["own@pine.example"])


if __name__ == "__main__":
    unittest.main()
