"""Money > Financials and the money strip (Stephen, Nov 13): the figures a bank asks for."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import financials
from domain import profiles
from seed import seed_state
from test_accounts import ServerCase

TODAY = date(2026, 11, 15)


def job(**kw):
    base = {"id": "j1", "name": "Elm", "payer_id": "p1", "contract_cents": 100_000, "change_orders": [], "invoices": [], "payments": []}
    base.update(kw)
    return base


def inv(id_, cents, sent, status="sent", **kw):
    return {"id": id_, "number": id_.upper(), "amount_cents": cents, "retainage_cents": 0, "status": status, "sent_at": f"{sent}T12:00:00+00:00", **kw}


class FiguresTests(unittest.TestCase):
    def test_the_aging_adds_up_to_what_the_jobs_show_as_owed(self):
        state = seed_state()
        f = financials.compute(state, today=TODAY)
        owed = sum(domain.job_money(j)["outstanding_cents"] for j in state["jobs"])
        self.assertEqual(f["receivable_cents"], owed)
        self.assertEqual(sum(a["cents"] for a in f["aging"]), owed)
        self.assertEqual(sum(r["cents"] for r in f["aging_rows"]), owed)
        self.assertEqual(sum(r["owed_cents"] for r in f["clients"]), owed)
        self.assertEqual(f["retainage_cents"], sum(domain.job_money(j)["retainage_open_cents"] for j in state["jobs"]))

    def test_pay_apps_age_by_days_since_sent_and_loose_payments_come_off_the_oldest(self):
        state = {"payers": [{"id": "p1", "name": "Park GC"}], "jobs": [job(
            contract_cents=1_000_000,
            invoices=[inv("a", 100_000, "2026-07-01"), inv("b", 50_000, "2026-10-01"), inv("c", 30_000, "2026-11-10")],
            payments=[{"amount_cents": 40_000, "invoice_id": None, "matched_at": "2026-10-20T12:00:00+00:00"},
                      {"amount_cents": 50_000, "invoice_id": "b", "matched_at": "2026-10-21T12:00:00+00:00"}])]}
        f = financials.compute(state, today=TODAY)
        aging = {a["key"]: a["cents"] for a in f["aging"]}
        self.assertEqual(aging, {"current": 30_000, "31_60": 0, "61_90": 0, "over_90": 60_000})
        self.assertEqual([(r["pay_app"], r["cents"]) for r in f["aging_rows"]], [("A", 60_000), ("C", 30_000)])
        self.assertEqual(f["collected_12m_cents"], 90_000)
        self.assertEqual(f["billed_12m_cents"], 180_000)

    def test_backlog_is_signed_work_not_done_yet(self):
        j = job(contract_cents=100_000, completed_value_cents=60_000,
                change_orders=[{"amount_cents": 20_000, "status": "approved"}, {"amount_cents": 5_000, "status": "pending"}],
                invoices=[inv("a", 40_000, "2026-11-01")])
        state = {"payers": [{"id": "p1", "name": "Park GC"}], "jobs": [j]}
        f = financials.compute(state, today=TODAY)
        # 120k signed; 40k billed; 20k done and not billed; 60k not done yet.
        self.assertEqual((f["backlog_cents"], f["unbilled_cents"], f["pending_co_cents"]), (60_000, 20_000, 5_000))
        self.assertEqual(f["backlog_jobs"][0]["left_cents"], 60_000)
        self.assertEqual(domain.backlog_cents(domain.job_money(j)), 60_000)
        closed = dict(j, closed=True)
        self.assertEqual(financials.compute({"payers": [], "jobs": [closed]}, today=TODAY)["backlog_cents"], 0, "a finished job has no backlog")

    def test_the_page_load_carries_the_backlog_for_the_strip(self):
        state = seed_state()
        view = domain.light_snapshot(state)
        self.assertEqual(view["totals"]["backlog_cents"], financials.compute(state, today=TODAY)["backlog_cents"])

    def test_months_run_twelve_back_from_this_one(self):
        f = financials.compute({"payers": [], "jobs": []}, today=TODAY)
        self.assertEqual([m["month"] for m in f["monthly"]][:2], ["2025-12", "2026-01"])
        self.assertEqual(f["monthly"][-1]["month"], "2026-11")
        self.assertEqual(len(f["monthly"]), 12)
        self.assertIsNone(f["top_client_share"])

    def test_the_csv_has_every_section(self):
        out = financials.to_csv(financials.compute(seed_state(), today=TODAY))
        for heading in ("Summary,Amount", "Receivables aging,Amount", "Month,Billed,Collected", "Client,Owed", "Backlog by job"):
            self.assertIn(heading, out)

    def test_only_owners_see_financials_until_a_profile_is_given_it(self):
        self.assertIn("financials", profiles.PERM_KEYS)
        self.assertNotIn("financials", profiles.MEMBER_DEFAULT)


class FinancialsHttpTests(ServerCase):
    def join(self, owner, addr):
        res, out = self.request("POST", "/api/team/invite", {"email": addr, "profile_id": "member"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        res, joined = self.request("POST", "/api/auth/accept-invite", {"accept_terms": True, "token": out["invite_path"].split("#invite=")[1],
                                                                       "name": "Mo", "password": "member password 1"})
        self.assertEqual(res.status, 200, joined)
        return res.getheader("Set-Cookie").split(";")[0], joined["user"]

    def test_owner_sees_it_member_does_not_until_given_it(self):
        owner, _ = self.signup("owner@fin.example", "Fin Co")
        res, out = self.request("GET", "/api/financials", cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(out["receivable_cents"], 0)
        res, body = self.request("GET", "/api/financials.csv", cookie=owner)
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "text/csv; charset=utf-8"))
        self.assertIn(b"Summary,Amount", body)
        member, _ = self.join(owner, "mo@fin.example")
        self.assertEqual(self.request("GET", "/api/financials", cookie=member)[0].status, 403)
        self.assertEqual(self.request("GET", "/api/financials.csv", cookie=member)[0].status, 403)
        res, out = self.request("POST", "/api/team/profiles/member", {"name": "Member", "perms": ["record_payments", "financials"]}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.request("GET", "/api/financials", cookie=member)[0].status, 200)


if __name__ == "__main__":
    unittest.main()
