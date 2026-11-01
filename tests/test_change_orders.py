"""Change orders after Stephen's Oct 29 review: the reason (M7), pricing, cost lines, contract
reference, backup and "client logged it" (M8), what the signer agrees to, the paper signer and
date, and work started before signing (M9), and each change order's own history (M10)."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import migrations
import payer_page
import server
from test_money import new_state, new_job

TODAY = datetime.now(timezone.utc).date()


def route(state, job, action, body, item=None):
    """A job action the way the page calls it."""
    ctx = server.Ctx(user={"email_verified": True}, body=body, company_id="co-test", actor="Pat")
    return server.JOB_ACTIONS[action][1](None, state, job["id"], item, ctx)


def refused(test, fn, *fields):
    with test.assertRaises(domain.GateError) as raised:
        fn()
    for f in fields:
        test.assertIn(f, raised.exception.fields)
    return raised.exception


def paper_sign(state, job, co, name="Kim Park", day=None):
    domain.add_job_record(state, job["id"], "Signed CO scan", "change_order", "", "owner")
    return domain.mark_co_signed(state, job["id"], co["id"], job["records"][-1]["id"], "Pat",
                                 signer_name=name, signed_on=day or TODAY.isoformat())


class ReasonTests(unittest.TestCase):  # M7
    def test_the_page_must_say_why(self):
        state = new_state()
        job = new_job(state)
        e = refused(self, lambda: route(state, job, "add-co", {"title": "Outlets", "amount_cents": 45_000}), "reason")
        self.assertEqual(str(e), "Pick why the scope changed.")
        refused(self, lambda: route(state, job, "add-co", {"title": "Outlets", "amount_cents": 45_000, "reason": "whim"}), "reason")
        refused(self, lambda: route(state, job, "add-co", {"title": "Outlets", "amount_cents": 45_000, "reason": "other"}), "description")
        route(state, job, "add-co", {"title": "Outlets", "amount_cents": 45_000, "reason": "other", "description": "Owner moved the island"})
        route(state, job, "add-co", {"title": "Rock", "amount_cents": 90_000, "reason": "unforeseen_condition"})
        self.assertEqual([c["reason"] for c in job["change_orders"]], ["other", "unforeseen_condition"])
        self.assertEqual(job["change_orders"][-1]["pricing"], "lump_sum")

    def test_callers_from_before_reasons_still_work(self):
        state = new_state()
        job = new_job(state)
        domain.add_change_order(state, job["id"], "Outlets", 45_000)
        self.assertNotIn("reason", job["change_orders"][-1])


class PricingTests(unittest.TestCase):  # M8
    def test_cost_lines_make_the_amount(self):
        state = new_state()
        job = new_job(state)
        items = [{"label": "Labor", "amount_cents": 30_000}, {"label": "Material", "amount_cents": 15_000}, {"label": "", "amount_cents": ""}]
        route(state, job, "add-co", {"title": "Tile", "reason": "owner_request", "pricing": "tm", "items": items, "contract_ref": "Section 09 30 00"})
        co = job["change_orders"][-1]
        self.assertEqual((co["amount_cents"], co["pricing"], co["contract_ref"]), (45_000, "tm", "Section 09 30 00"))
        self.assertEqual(co["items"], [{"label": "Labor", "amount_cents": 30_000}, {"label": "Material", "amount_cents": 15_000}], "the blank row is dropped")
        e = refused(self, lambda: route(state, job, "add-co", {"title": "Tile", "reason": "owner_request", "amount_cents": 50_000, "items": items}), "amount_cents")
        self.assertIn("$450.00", str(e))
        route(state, job, "add-co", {"title": "Tile", "reason": "owner_request", "amount_cents": 45_000, "items": items})

    def test_cost_line_rules(self):
        state = new_state()
        job = new_job(state)
        many = [{"label": f"Line {n}", "amount_cents": 100} for n in range(51)]
        refused(self, lambda: domain.add_change_order(state, job["id"], "Big", 0, items=many), "items")
        domain.add_change_order(state, job["id"], "Fifty", 0, items=many[:50])
        self.assertEqual(job["change_orders"][-1]["amount_cents"], 5_000)
        refused(self, lambda: domain.add_change_order(state, job["id"], "No label", 0, items=[{"label": "", "amount_cents": 100}]), "item_label_0")
        refused(self, lambda: domain.add_change_order(state, job["id"], "No amount", 0, items=[{"label": "Labor", "amount_cents": 0}]), "item_amount_0")
        refused(self, lambda: domain.add_change_order(state, job["id"], "Huge", 0, items=[{"label": "Labor", "amount_cents": domain.MAX_AMOUNT_CENTS + 1}]), "item_amount_0")
        refused(self, lambda: domain.add_change_order(state, job["id"], "Odd", 100, pricing="cost_plus"), "pricing")

    def test_credit_rules_are_unchanged(self):
        state = new_state()
        job = new_job(state, contract=100_000)
        refused(self, lambda: domain.add_change_order(state, job["id"], "Too much", 0, items=[{"label": "Owner buys fixtures", "amount_cents": -100_001}]), "amount_cents")
        domain.add_change_order(state, job["id"], "Credit", 0, items=[{"label": "Owner buys fixtures", "amount_cents": -50_000}])
        self.assertEqual(job["change_orders"][-1]["amount_cents"], -50_000)

    def test_backup_must_be_on_the_job_record(self):
        state = new_state()
        job = new_job(state)
        domain.add_job_record(state, job["id"], "Tile quote", "supplier_invoice", "", "owner")
        rec = job["records"][-1]["id"]
        refused(self, lambda: domain.add_change_order(state, job["id"], "Tile", 100, support_record_ids=["rec-nope"]), "support_record_ids")
        domain.add_change_order(state, job["id"], "Tile", 100, support_record_ids=[rec, rec])
        self.assertEqual(job["change_orders"][-1]["support_record_ids"], [rec])

    def test_client_logged_it_after_signing(self):
        state = new_state()
        job = new_job(state)
        domain.add_change_order(state, job["id"], "Tile", 100)
        co = job["change_orders"][-1]
        self.assertIn("isn't signed", str(refused(self, lambda: route(state, job, "co-logged", {}, co["id"]))))
        paper_sign(state, job, co)
        route(state, job, "co-logged", {"logged": True}, co["id"])
        self.assertEqual(co["client_logged_by"], "Pat")
        self.assertTrue(co["client_logged_at"])
        route(state, job, "co-logged", {"logged": False}, co["id"])
        self.assertNotIn("client_logged_at", co)
        self.assertEqual([r["action"] for r in state["audit"]].count("co_client_logged"), 2)


class SignatureTests(unittest.TestCase):  # M9
    def test_the_signer_agrees_to_scope_price_and_schedule(self):
        state = new_state()
        job = new_job(state)
        domain.add_change_order(state, job["id"], "Tile", 0, reason="design_issue", pricing="tm", schedule_days=3,
                                items=[{"label": "Labor", "amount_cents": 20_000}], contract_ref="Bath 2")
        co = job["change_orders"][-1]
        snap = domain.co_snapshot(state, job, co)
        self.assertEqual(snap["version"], 2)
        self.assertEqual((snap["schedule_days"], snap["reason"], snap["pricing"], snap["contract_ref"]), (3, "Design issue", "Time and materials", "Bath 2"))
        self.assertEqual(snap["items"], [{"label": "Labor", "amount_cents": 20_000}])
        self.assertIn("scope, price and schedule", domain.CO_CONSENT)
        page = payer_page.render_co(state, job, co, "/s/x.y")
        for words in ("<h2>Scope</h2>", "<h2>Price</h2>", "<h2>Schedule</h2>", "+3 days on the schedule", "Design issue", "Labor", "Time and materials", "scope, price and schedule"):
            self.assertIn(words, page)
        domain.sign_change_order(state, job, co, "Kim Park", True, "203.0.113.5", "Safari")
        self.assertEqual(co["signature"]["snapshot"], snap)
        self.assertEqual(co["signature"]["snapshot_sha256"], domain.snapshot_sha256(snap))
        self.assertEqual(co["approved_by"], "Kim Park (signed online)")

    def test_an_older_signature_still_verifies_and_shows_what_was_signed(self):
        state = new_state()
        job = new_job(state)
        domain.add_change_order(state, job["id"], "Outlets", 45_000, schedule_days=2)
        co = job["change_orders"][-1]
        old = {"contractor": "Test", "job": job["name"], "address": job["address"], "change_order": 1, "title": "Outlets",
               "description": "", "amount_cents": 45_000, "consent": "I agree to this change order. I agree to sign it electronically, and my typed name below is my signature."}
        co["signature"] = {"name": "Kim", "at": "2026-09-01T10:00:00+00:00", "snapshot": old, "snapshot_sha256": domain.snapshot_sha256(old)}
        co["status"] = "approved"
        self.assertEqual(co["signature"]["snapshot_sha256"], domain.snapshot_sha256(co["signature"]["snapshot"]))
        page = payer_page.render_co(state, job, co, "/s/x.y")
        self.assertIn("Signed</strong> by Kim", page)
        self.assertNotIn("<h2>Schedule</h2>", page, "the older signature didn't show a schedule, so neither does its page")

    def test_paper_signer_and_date(self):
        state = new_state()
        job = new_job(state)
        domain.add_change_order(state, job["id"], "Tile", 100)
        co = job["change_orders"][-1]
        domain.add_job_record(state, job["id"], "Signed CO scan", "change_order", "", "owner")
        rec = job["records"][-1]["id"]
        refused(self, lambda: route(state, job, "approve-co", {"record_id": rec}, co["id"]), "signer_name", "signed_on")
        later = (TODAY + timedelta(days=3)).isoformat()
        refused(self, lambda: route(state, job, "approve-co", {"record_id": rec, "signer_name": "Kim Park", "signed_on": later}, co["id"]), "signed_on")
        refused(self, lambda: route(state, job, "approve-co", {"record_id": rec, "signer_name": "Kim Park", "signed_on": "10/01/2026"}, co["id"]), "signed_on")
        self.assertEqual(co["status"], "pending")
        route(state, job, "approve-co", {"record_id": rec, "signer_name": " Kim  Park ", "signed_on": "2026-10-01"}, co["id"])
        ev = co["signed_evidence"]
        self.assertEqual((co["status"], ev["signer_name"], ev["signed_on"], ev["by"]), ("approved", "Kim Park", "2026-10-01", "Pat"))
        self.assertEqual(co["history"][-1]["note"], "signed on paper by Kim Park on 2026-10-01")

    def test_work_started_before_signing_is_flagged(self):
        state = new_state()
        job = new_job(state)
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        route(state, job, "add-co", {"title": "Rock", "amount_cents": 90_000, "reason": "unforeseen_condition", "work_started_on": yesterday})
        co = job["change_orders"][-1]
        self.assertTrue(domain.co_started_unsigned(co))
        self.assertTrue(domain.job_view(state, job)["change_orders"][-1]["work_started_unsigned"])
        self.assertIn("before it was signed", state["audit"][-1]["detail"])
        paper_sign(state, job, co, day=TODAY.isoformat())
        self.assertTrue(domain.co_started_unsigned(co), "signed after work started: the flag stays as a record")
        self.assertEqual(state["audit"][-1]["action"], "co_signed_after_start")

        domain.add_change_order(state, job["id"], "Signed first", 100)
        first = job["change_orders"][-1]
        paper_sign(state, job, first, day=yesterday)
        route(state, job, "co-work-started", {"work_started_on": TODAY.isoformat()}, first["id"])
        self.assertFalse(domain.co_started_unsigned(first))
        self.assertEqual(first["work_started_on"], TODAY.isoformat())
        refused(self, lambda: route(state, job, "co-work-started", {"work_started_on": (TODAY + timedelta(days=5)).isoformat()}, first["id"]), "work_started_on")
        refused(self, lambda: route(state, job, "co-work-started", {}, first["id"]), "work_started_on")


class HistoryTests(unittest.TestCase):  # M10
    def test_every_transition_stamps_who_and_when(self):
        state = new_state()
        job = new_job(state)
        domain.record_progress(state, job["id"], 1_000_000)
        domain.void_draft(state, job["id"], "Pat")
        domain.add_change_order(state, job["id"], "Tile", 45_000, actor="Pat")
        co = job["change_orders"][-1]
        self.assertTrue(co["created_at"])
        self.assertEqual(co["created_by"], "Pat")
        domain.include_change_order(state, job["id"], co["id"], "Lee")
        self.assertEqual(co["included_by"], "Lee")
        self.assertTrue(co["included_at"])
        domain.draft_invoice(state, job["id"], "Lee")
        domain.void_draft(state, job["id"], "Lee")
        domain.request_co_signature(state, job["id"], co["id"], "Lee", link_base="https://x.example", company_id="co-x")
        paper_sign(state, job, co)
        self.assertEqual(co["approved_by"], "Pat")
        self.assertEqual([h["what"] for h in co["history"]], ["created", "included", "off_draft", "signature_requested", "signed"])
        self.assertTrue(all(h["at"] and h["by"] for h in co["history"]))

        domain.add_change_order(state, job["id"], "Dropped", 100, actor="Pat")
        gone = job["change_orders"][-1]
        domain.withdraw_change_order(state, job["id"], gone["id"], "Lee")
        self.assertEqual((gone["withdrawn_by"], gone["history"][-1]["what"]), ("Lee", "withdrawn"))
        self.assertTrue(gone["withdrawn_at"])

    def test_older_change_orders_get_their_history_from_the_audit(self):
        state = new_state()
        job = new_job(state)
        for n in range(10):
            domain.add_change_order(state, job["id"], f"CO {n}", 100, actor="Pat")
        first, tenth = job["change_orders"][0], job["change_orders"][9]
        domain.include_change_order(state, job["id"], first["id"], "Lee")
        paper_sign(state, job, tenth)
        for co in job["change_orders"]:
            for key in ("history", "created_at", "created_by", "included_at", "included_by", "approved_by"):
                co.pop(key, None)
        state["schema_version"] = 4
        migrations.migrate(state)
        self.assertEqual([h["what"] for h in first["history"]], ["created", "included"], "CO 10's rows aren't CO 1's")
        self.assertEqual((first["created_by"], first["included_by"]), ("Pat", "Lee"))
        self.assertEqual([h["what"] for h in tenth["history"]], ["created", "signed"])
        self.assertEqual(tenth["approved_by"], "Pat")
        migrations._payapps_cos_co_history(state)
        self.assertEqual(len(first["history"]), 2, "safe to run again")


if __name__ == "__main__":
    unittest.main()
