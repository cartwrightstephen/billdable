"""Round 2 review fixes, job rules: the final pay app's override follows a redraft, the Inbox's
mark-done on a finished job says so, backlog counts signed work only, approvals sign off the
version shown, and the demo address can't be taken."""

import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import domain
import importer
import outbox
from domain import profiles
from seed import seed_state
from test_accounts import ServerCase
import test_team
from test_money import clear_packet, new_job, new_state, send_open
from test_round2_review_rules import says_done_item


class FinalOverrideOnARedraft(unittest.TestCase):  # R4, B6 (decision 6)
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.first = send_open(self.state, self.job)
        domain.mark_done(self.state, self.job["id"], "Sam", unpaid_reason="GC pays late every month")
        self.override = self.job["final_override"]

    def test_deleting_the_final_draft_and_drafting_again_keeps_the_override(self):
        domain.void_draft(self.state, self.job["id"], "Sam")
        domain.draft_invoice(self.state, self.job["id"], strict=True)
        final = self.job["invoices"][-1]
        self.assertTrue(final["final"])
        self.assertEqual(final["final_override"], self.override)

    def test_pulling_back_the_sent_final_and_drafting_again_keeps_the_override(self):
        final = send_open(self.state, self.job)
        domain.void_sent_pay_app(self.state, self.job["id"], final["id"], "Wrong period", "Sam")
        domain.draft_invoice(self.state, self.job["id"], strict=True)
        again = self.job["invoices"][-1]
        self.assertNotEqual(again["id"], final["id"])
        self.assertEqual(again["final_override"], self.override)

    def test_no_override_once_the_earlier_pay_app_is_paid(self):
        domain.void_draft(self.state, self.job["id"], "Sam")
        domain.match_payment(self.state, self.job["id"], self.first["amount_cents"], "chk", invoice_id=self.first["id"])
        domain.draft_invoice(self.state, self.job["id"], strict=True)
        self.assertNotIn("final_override", self.job["invoices"][-1])

    def test_a_later_final_pay_app_skips_an_override_whose_pay_apps_are_paid(self):  # R4 round 2
        # The final PA-02 is unpaid, but the PA-01 the override named is paid: the next pay app
        # (a change order after the final one) was never overridden.
        send_open(self.state, self.job)
        domain.match_payment(self.state, self.job["id"], self.first["amount_cents"], "c1", invoice_id=self.first["id"])
        domain.add_change_order(self.state, self.job["id"], "Extra", 50_000)
        co = self.job["change_orders"][-1]
        domain.approve_change_order(self.state, self.job["id"], co["id"], "Owner")
        self.assertTrue(domain.unpaid_pay_apps(self.job))
        domain.draft_invoice(self.state, self.job["id"], strict=True)
        later = self.job["invoices"][-1]
        self.assertTrue(later["final"])
        self.assertNotIn("final_override", later)


class InboxMarkDoneOnAFinishedJob(unittest.TestCase):  # R1
    def test_a_job_marked_done_since_says_so_and_keeps_the_message(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000, "a")
        send_open(state, job)
        item = says_done_item(state, job)
        domain.mark_done(state, job["id"], "Sam", unpaid_reason="GC asked for it")
        with self.assertRaises(domain.GateError) as refused:
            importer.file_and_mark_done(state, item["id"], "Sam")
        self.assertEqual(str(refused.exception), "Job is already marked done.")
        self.assertEqual(item["status"], "new")


def unpriced_co(state):
    job = next(j for j in state["jobs"] if j["id"] == "job-grant")
    domain.add_change_order(state, job["id"], "Extra outlets", None)
    return job


class PermissionRowsInReplies(unittest.TestCase):  # B4, B24
    def test_the_reply_after_an_email_is_queued_keeps_rows_the_person_cannot_act_on_out(self):
        state = seed_state()
        unpriced_co(state)
        state.setdefault("email_log", []).append({"id": "e-q", "to": "a@b.example", "subject": "s", "body": "b", "status": "queued"})
        site = mock.Mock()
        site.lock.return_value = threading.Lock()
        site.load.return_value = state
        member = {"id": "u-member", "role": "member", "email_verified": False, "email": "m@team.example"}
        self.assertNotIn("manage_jobs", profiles.perms_of(member, state))
        reply = outbox.deliver_queued(site, "co-x", member)
        self.assertEqual([r for r in reply["today_rows"] if r["kind"] == "co_info"], [])
        owner = {"id": "u-owner", "role": "owner", "email_verified": False}
        state["email_log"][-1]["status"] = "queued"
        reply = outbox.deliver_queued(site, "co-x", owner)
        self.assertTrue([r for r in reply["today_rows"] if r["kind"] == "co_info"])


class DigestRowsForSomeoneWhoCanAct(unittest.TestCase):  # B23
    def test_a_change_order_to_price_assigned_to_a_member_goes_to_the_owner(self):
        state = seed_state()
        job = unpriced_co(state)
        job["assignee_id"] = "u-member"
        team = {"u-owner": list(profiles.PERM_KEYS), "u-member": ["record_payments"]}
        now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        mail = domain.daily_digest(state, "", now, user_id="u-owner", perms=list(profiles.PERM_KEYS), team_perms=team)
        self.assertIn("Extra outlets needs a price", mail[1])
        member_mail = domain.daily_digest(state, "", now, user_id="u-member", owner=False, perms=["record_payments"], team_perms=team)
        self.assertNotIn("Extra outlets", (member_mail or ("", ""))[1])


class ApprovalOfTheVersionShown(unittest.TestCase):  # B13
    POOL = {"u-ana": "Ana Ruiz"}

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.record_progress(self.state, self.job["id"], 300_000, "a")
        self.inv = clear_packet(self.state, self.job)
        domain.set_payapp_approvers(self.state, ["u-ana"], self.POOL)
        self.shown = domain.job_view(self.state, self.job)["invoices"][-1]["approval"]["stamp"]

    def test_a_draft_changed_since_it_was_shown_is_not_approved(self):
        domain.record_progress(self.state, self.job["id"], 450_000, "b")
        inv = clear_packet(self.state, self.job)
        with self.assertRaises(domain.GateError) as refused:
            domain.approve_payapp(self.state, self.job["id"], "Ana Ruiz", "u-ana", invoice_id=inv["id"], seen=self.shown)
        self.assertTrue(refused.exception.extra["review_changed"])
        self.assertEqual(domain.approvals_missing(self.state, self.job, inv), ["Ana Ruiz"])
        with self.assertRaises(domain.GateError):
            domain.approve_and_send(self.state, self.job["id"], "Owner")

    def test_a_click_from_an_older_page_is_refused(self):
        with self.assertRaises(domain.GateError):
            domain.approve_payapp(self.state, self.job["id"], "Ana Ruiz", "u-ana", invoice_id=self.inv["id"], seen="")
        with self.assertRaises(domain.GateError):
            domain.approve_payapp(self.state, self.job["id"], "Ana Ruiz", "u-ana", invoice_id="inv-other", seen=self.shown)

    def test_the_version_shown_is_approved(self):
        domain.approve_payapp(self.state, self.job["id"], "Ana Ruiz", "u-ana", invoice_id=self.inv["id"], seen=self.shown)
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), [])


class ApproverRenamed(unittest.TestCase):  # B25
    def test_the_lists_show_the_new_name(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        payer = state["payers"][0]
        domain.set_payapp_approvers(state, ["u-ana"], {"u-ana": "Ana Ruiz"})
        domain.set_payapp_approvers(state, ["u-ana"], {"u-ana": "Ana Ruiz"}, payer_id=payer["id"])
        self.assertTrue(domain.rename_approver(state, "u-ana", "Ana Ortiz"))
        self.assertEqual(state["company"]["payapp_approvers"][0]["name"], "Ana Ortiz")
        self.assertEqual(payer["payapp_approvers"][0]["name"], "Ana Ortiz")
        domain.record_progress(state, job["id"], 300_000, "a")
        inv = clear_packet(state, job)
        self.assertEqual(domain.approvals_missing(state, job, inv), ["Ana Ortiz"])
        self.assertFalse(domain.rename_approver(state, "u-ana", "Ana Ortiz"))


class ApproverRenamedHttp(ServerCase):  # B25, through My account
    join = test_team.TeamHttpTests.join

    def test_my_name_renames_me_on_the_approvers_list(self):
        owner, _ = self.signup("owner-rename@appr.example", "Rename Co")
        res, out = self.request("POST", "/api/team/profiles", {"name": "Project manager", "perms": ["approve_payapps"]}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        pm_cookie, pm = self.join(owner, "pam-rename@appr.example", out["profile"]["id"])
        res, out = self.request("POST", "/api/approvers", {"user_ids": [pm["id"]]}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        res, out = self.request("POST", "/api/auth/name", {"name": "Pam Ortiz"}, cookie=pm_cookie)
        self.assertEqual(res.status, 200, out)
        _, state = self.request("GET", "/api/state", cookie=owner)
        self.assertEqual([p["name"] for p in state["company"]["payapp_approvers"]], ["Pam Ortiz"])


class DemoAddressKept(ServerCase):  # B15
    join = test_team.TeamHttpTests.join

    def test_nobody_signs_up_with_or_is_invited_to_a_demo_address(self):
        for addr in (auth.DEMO_EMAIL, "Owner@Hartline.example"):
            res, out = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": addr, "password": "correct horse battery",
                                                                  "name": "Mallory", "company_name": "Squat Co"})
            self.assertEqual(res.status, 400, out)
            self.assertEqual(out["error"], auth.RESERVED_WORDS)
        self.assertIsNone(auth.user_by_email("owner@hartline.example"))
        owner, _ = self.signup("owner-demo-squat@pine.example")
        res, out = self.request("POST", "/api/team/invite", {"email": auth.DEMO_EMAIL, "profile_id": "member"}, cookie=owner)
        self.assertEqual(res.status, 409, out)

    def test_the_demo_account_itself_is_still_made(self):
        user = auth.user_by_email(auth.DEMO_EMAIL) or auth.create_user(auth.DEMO_EMAIL, "demo", "Dana", auth.DEMO_COMPANY_ID, enforce_policy=False)
        self.assertEqual(user["company_id"], auth.DEMO_COMPANY_ID)


if __name__ == "__main__":
    unittest.main()
