"""Round 2 job rules (Stephen, 10 Nov 2026): a change order saved without a price (decision 2),
the override Finish job needs while closeout documents are missing (decision 4), and named
people who approve a pay app before it goes (decision 8)."""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import server
from domain import profiles
from test_accounts import ServerCase
from test_money import clear_packet, new_job, new_state, send_open
from test_team import JOB, TeamHttpTests
from waiver_help import signed_waiver


def kinds(state, perms=None):
    return [r["kind"] for r in domain.rows_with_perms(domain.today_rows(state), perms)]


class UnpricedChangeOrderTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.add_change_order(self.state, self.job["id"], "Extra outlets", 0, reason="owner_request")
        self.co = self.job["change_orders"][-1]

    def test_saved_without_a_price_and_counts_for_nothing(self):
        self.assertTrue(self.co["unpriced"])
        self.assertEqual((self.co["amount_cents"], self.co["status"]), (0, "pending"))
        self.assertIn("no price yet", self.state["audit"][-1]["detail"])
        m = domain.job_money(self.job)
        self.assertEqual((m["contract_total_cents"], m["pending_co_cents"]), (1_000_000, 0))

    def test_cost_lines_that_add_to_nothing_are_still_refused(self):
        with self.assertRaises(domain.GateError) as raised:
            domain.add_change_order(self.state, self.job["id"], "Swap", 0, reason="owner_request",
                                    items=[{"label": "Add", "amount_cents": 100}, {"label": "Take off", "amount_cents": -100}])
        self.assertIn("amount_cents", raised.exception.fields)

    def test_it_cannot_be_signed_or_billed_until_priced(self):
        jid, cid = self.job["id"], self.co["id"]
        for attempt in (lambda: domain.request_co_signature(self.state, jid, cid, link_base="https://x.example", company_id="c1"),
                        lambda: domain.mark_co_signed(self.state, jid, cid, "rec-1"),
                        lambda: domain.include_change_order(self.state, jid, cid),
                        lambda: domain.sign_change_order(self.state, self.job, self.co, "Dana Lee", True, "1.2.3.4", "ua")):
            with self.assertRaises(domain.GateError) as raised:
                attempt()
            self.assertIn("has no price yet", str(raised.exception))
        self.assertEqual(self.co["status"], "pending")

    def test_it_keeps_collecting_hours_from_job_records(self):
        domain.add_daily_record(self.state, self.job["id"], "Ran the kitchen circuit", details={
            "sections": {"co_work": [{"co_id": self.co["id"], "description": "Two outlets roughed in", "hours": "3.5"}]}})
        domain.add_job_record(self.state, self.job["id"], "Time sheet Nov 12", "ticket")
        domain.link_record_to_work(self.state, self.job["id"], self.job["records"][-1]["id"], co_id=self.co["id"])
        view = domain.job_view(self.state, self.job)
        co = view["change_orders"][-1]
        self.assertEqual(co["needs"], ["a price"])
        self.assertEqual(co["accrued"]["hours_milli"], 3500)
        self.assertEqual(co["accrued"]["entries"][0]["description"], "Two outlets roughed in")
        self.assertEqual(co["accrued"]["record_ids"], [self.job["records"][-1]["id"]])

    def test_a_to_do_row_for_those_who_manage_jobs_only(self):
        self.assertIn("co_info", kinds(self.state))
        row = next(r for r in domain.today_rows(self.state) if r["kind"] == "co_info")
        self.assertEqual((row["perm"], row["co_id"], row["button"]), ("manage_jobs", self.co["id"], "Add the price to CO 1"))
        self.assertEqual(row["key"], f"co_info:{self.job['id']}:{self.co['id']}")
        self.assertIn("co_info", kinds(self.state, ["manage_jobs"]))
        self.assertNotIn("co_info", kinds(self.state, ["record_payments"]))
        snap = domain.light_snapshot(self.state, perms=["record_payments"])
        self.assertNotIn("co_info", [r["kind"] for r in snap["today_rows"]])
        # The daily email lists it for those who can price it.
        subject, _ = domain.daily_digest(self.state, perms=["manage_jobs"])
        self.assertIn("1 change order to price", subject)
        member = domain.daily_digest(self.state, perms=["record_payments"])
        self.assertTrue(member is None or "change order to price" not in member[0])

    def test_priced_it_can_be_signed_and_the_row_goes(self):
        with self.assertRaises(domain.GateError) as raised:
            domain.price_change_order(self.state, self.job["id"], self.co["id"], 0)
        self.assertIn("amount_cents", raised.exception.fields)
        domain.price_change_order(self.state, self.job["id"], self.co["id"], 125_000, actor="Ana")
        self.assertNotIn("unpriced", self.co)
        self.assertEqual((self.co["amount_cents"], self.co["priced_by"], self.co["history"][-1]["what"]), (125_000, "Ana", "priced"))
        self.assertEqual(self.state["audit"][-1]["action"], "price_co")
        self.assertNotIn("co_info", kinds(self.state))
        domain.include_change_order(self.state, self.job["id"], self.co["id"])
        with self.assertRaises(domain.GateError):
            domain.price_change_order(self.state, self.job["id"], self.co["id"], 1)

    def test_cost_lines_price_it(self):
        domain.price_change_order(self.state, self.job["id"], self.co["id"], 0, [{"label": "Labor", "amount_cents": 40_000}, {"label": "Material", "amount_cents": 10_000}])
        self.assertEqual(self.co["amount_cents"], 50_000)
        self.assertEqual(len(self.co["items"]), 2)

    @unittest.skipUnless(shutil.which("node"), "needs Node")
    def test_the_row_and_the_job_page_task_say_the_same(self):
        row = next(r for r in domain.today_rows(self.state) if r["kind"] == "co_info")
        js = Path(__file__).resolve().parents[1] / "static/js/jobs.js"
        script = "const { coPriceLabel } = await import(process.argv[1]); console.log(coPriceLabel({ number: 1 }));"
        out = subprocess.run(["node", "--input-type=module", "-e", script, js.as_uri()], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, row["button"])

    def test_the_permission(self):
        self.assertEqual(profiles.JOB_ACTION_PERMS["co-price"], "manage_jobs")
        js = (Path(__file__).resolve().parents[1] / "static/js/jobs.js").read_text()
        self.assertIn('"co-price": "manage_jobs"', js)


class CloseoutOverrideTests(unittest.TestCase):
    def finished_and_paid(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=0)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        signed_waiver(state, job["id"], "unconditional_final")
        domain.send_waiver(state, job["id"], "Owner")
        self.assertEqual(domain.close_blockers(job), [])
        return state, job

    def test_missing_closeout_documents_need_a_reason(self):
        state, job = self.finished_and_paid()
        missing = [i["label"] for i in domain.closeout_missing(job)]
        self.assertTrue(missing)
        with self.assertRaises(domain.GateError) as raised:
            domain.close_job(state, job["id"], "Sam")
        self.assertEqual(raised.exception.fields, {"closeout_override": "Enter why the job can finish without them."})
        self.assertEqual(raised.exception.blockers, missing)
        self.assertFalse(job.get("closed"))
        domain.close_job(state, job["id"], "Sam", closeout_override="  Owner has the binder already ")
        self.assertTrue(job["closed"])
        o = job["closeout_override"]
        self.assertEqual((o["by"], o["reason"], o["missing"]), ("Sam", "Owner has the binder already", missing))
        self.assertTrue(o["at"])
        audits = [a for a in state["audit"] if a["action"] == "closeout_override"]
        self.assertEqual(len(audits), 1)
        self.assertIn("Owner has the binder already", audits[0]["detail"])
        self.assertEqual(audits[0]["actor"], "Sam")
        # Shown on the job file of a finished job.
        self.assertEqual(domain.job_summary(state, job)["closeout_override"]["reason"], "Owner has the binder already")

    def test_nothing_missing_needs_no_reason(self):
        state, job = self.finished_and_paid()
        for item in job["closeout_packet"]:
            domain.resolve_packet_item(state, job["id"], item["id"], reason="In the binder")
        domain.close_job(state, job["id"], "Sam")
        self.assertTrue(job["closed"])
        self.assertNotIn("closeout_override", job)


class ApproverTests(unittest.TestCase):
    POOL = {"u-ana": "Ana Ruiz", "u-ben": "Ben Ode"}

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.record_progress(self.state, self.job["id"], 300_000, "a")
        self.inv = clear_packet(self.state, self.job)
        self.assertEqual(self.inv["status"], "awaiting_approval")

    def test_named_people_approve_before_it_goes(self):
        domain.set_payapp_approvers(self.state, ["u-ana", "u-ben"], self.POOL, actor="Owner")
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), ["Ana Ruiz", "Ben Ode"])
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(self.state, self.job["id"], "Owner")
        self.assertTrue(raised.exception.extra.get("approvals_missing"))
        self.assertIn("Ana Ruiz, Ben Ode", str(raised.exception))
        with self.assertRaises(domain.GateError):
            domain.approve_payapp(self.state, self.job["id"], "Cat", "u-cat")   # not named
        domain.approve_payapp(self.state, self.job["id"], "Ana Ruiz", "u-ana")
        with self.assertRaises(domain.GateError):
            domain.approve_payapp(self.state, self.job["id"], "Ana Ruiz", "u-ana")   # already
        view = domain.job_view(self.state, self.job)["invoices"][-1]["approval"]
        self.assertEqual([(p["name"], bool(p["approved_at"])) for p in view["required"]], [("Ana Ruiz", True), ("Ben Ode", False)])
        self.assertEqual(view["waiting"], ["Ben Ode"])
        domain.approve_payapp(self.state, self.job["id"], "Ben Ode", "u-ben")
        domain.approve_and_send(self.state, self.job["id"], "Owner")
        self.assertEqual(self.inv["status"], "sent")

    def test_an_approval_is_cleared_when_the_pay_app_changes(self):
        domain.set_payapp_approvers(self.state, ["u-ana"], self.POOL)
        domain.approve_payapp(self.state, self.job["id"], "Ana Ruiz", "u-ana")
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), [])
        self.inv["period_to"] = "2026-10-31" if self.inv.get("period_to") != "2026-10-31" else "2026-09-30"
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), ["Ana Ruiz"])
        domain._refresh_draft_gate(self.job, self.state)
        self.assertEqual(self.inv["approvals"], [])
        self.assertEqual(self.state["audit"][-1]["action"], "payapp_approval_cleared")
        # A new amount drafts it again: nothing carries over.
        domain.approve_payapp(self.state, self.job["id"], "Ana Ruiz", "u-ana")
        domain.record_progress(self.state, self.job["id"], 400_000, "b")
        draft = next(i for i in self.job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        self.assertEqual(domain.approvals_missing(self.state, self.job, draft), ["Ana Ruiz"])

    def test_only_once_it_is_ready_to_send(self):
        domain.set_payapp_approvers(self.state, ["u-ana"], self.POOL)
        self.inv["status"] = "draft"
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_payapp(self.state, self.job["id"], "Ana Ruiz", "u-ana")
        self.assertIn("supporting documents", str(raised.exception))

    def test_a_client_list_replaces_the_companys(self):
        domain.set_payapp_approvers(self.state, ["u-ana"], self.POOL)
        payer_id = self.job["payer_id"]
        domain.set_payapp_approvers(self.state, ["u-ben"], self.POOL, payer_id=payer_id)
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), ["Ben Ode"])
        domain.set_payapp_approvers(self.state, [], self.POOL, payer_id=payer_id)
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), [])
        domain.set_payapp_approvers(self.state, [], self.POOL, payer_id=payer_id, same_as_company=True)
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), ["Ana Ruiz"])

    def test_only_people_who_hold_the_permission_and_leaving_takes_them_off(self):
        with self.assertRaises(domain.GateError) as raised:
            domain.set_payapp_approvers(self.state, ["u-zed"], self.POOL)
        self.assertIn("approvers", raised.exception.fields)
        domain.set_payapp_approvers(self.state, ["u-ana", "u-ben"], self.POOL)
        domain.set_payapp_approvers(self.state, ["u-ana"], self.POOL, payer_id=self.job["payer_id"])
        domain.drop_approver(self.state, "u-ana")
        # The client's list she emptied comes off, so the company's applies (not "nobody").
        self.assertNotIn("payapp_approvers", self.state["payers"][0])
        self.assertEqual(domain.approvals_missing(self.state, self.job, self.inv), ["Ben Ode"])
        self.assertEqual([p["id"] for p in self.state["company"]["payapp_approvers"]], ["u-ben"])

    def test_the_permission(self):
        self.assertIn("approve_payapps", profiles.PERM_KEYS)
        self.assertNotIn("approve_payapps", profiles.MEMBER_DEFAULT + profiles.MEMBER_SENDS)
        self.assertEqual(profiles.JOB_ACTION_PERMS["payapp-approve"], "approve_payapps")
        js = (Path(__file__).resolve().parents[1] / "static/js/jobs.js").read_text()
        self.assertIn('"payapp-approve": "approve_payapps"', js)


class Round2RulesHttpTests(ServerCase):
    join = TeamHttpTests.join

    def setUp(self):
        super().setUp()
        tag = self._testMethodName.replace("_", "")[-20:]
        self.tag = tag
        self.owner, self.owner_user = self.signup(f"owner-{tag}@rules.example", "Rules Co")
        self.member, self.member_user = self.join(self.owner, f"fred-{tag}@rules.example")
        _, state = self.request("POST", "/api/jobs", JOB, cookie=self.owner)
        self.job_id = state["jobs"][0]["id"]

    def job(self, state):
        return next(j for j in state["jobs"] if j["id"] == self.job_id)

    def test_a_change_order_without_a_price(self):
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/add-co", {"title": "Gate", "reason": "owner_request"}, cookie=self.member)
        self.assertEqual(res.status, 200, out)
        co = self.job(out)["change_orders"][0]
        self.assertTrue(co["unpriced"])
        self.assertNotIn("co_info", [r["kind"] for r in out["today_rows"]], "a member doesn't price change orders")
        _, owner_state = self.request("GET", "/api/state", cookie=self.owner)
        self.assertIn("co_info", [r["kind"] for r in owner_state["today_rows"]])
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/co-price/{co['id']}", {"amount_cents": 20_000}, cookie=self.member)
        self.assertEqual((res.status, out.get("error")), (403, server.NO_PERMISSION))
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/co-price/{co['id']}", {"amount_cents": 20_000}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.job(out)["change_orders"][0]["amount_cents"], 20_000)
        self.assertNotIn("co_info", [r["kind"] for r in out["today_rows"]])

    def test_named_approvers(self):
        res, out = self.request("POST", "/api/team/profiles", {"name": "Project manager", "perms": ["approve_payapps"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        pm, pm_user = self.join(self.owner, f"pam-{self.tag}@rules.example", out["profile"]["id"])
        res, out = self.request("POST", "/api/approvers", {"user_ids": [self.member_user["id"]]}, cookie=self.owner)
        self.assertEqual((res.status, list(out.get("fields", {}))), (409, ["approvers"]), out)
        res, out = self.request("POST", "/api/approvers", {"user_ids": [pm_user["id"]]}, cookie=self.member)
        self.assertEqual(res.status, 403, out)
        res, out = self.request("POST", "/api/approvers", {"user_ids": [pm_user["id"]]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual([p["id"] for p in out["company"]["payapp_approvers"]], [pm_user["id"]])
        line = self.job(out)["sov"]["lines"][0]
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.3"}}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        for item in self.job(out)["invoices"][-1]["packet"]:
            res, out = self.request("POST", f"/api/jobs/{self.job_id}/resolve/{item['id']}", {"reason": "In hand"}, cookie=self.owner)
            self.assertEqual(res.status, 200, out)
        inv = self.job(out)["invoices"][-1]
        self.assertEqual(inv["status"], "awaiting_approval")
        self.assertEqual(inv["approval"]["waiting"], [pm_user["name"]])
        stamp = inv["email_preview"]["stamp"]
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/send", {"reviewed": stamp}, cookie=self.owner)
        self.assertEqual(res.status, 409, out)
        self.assertTrue(out.get("approvals_missing"), out)
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/payapp-approve", {}, cookie=self.member)
        self.assertEqual((res.status, out.get("error")), (403, server.NO_PERMISSION))
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/payapp-approve", {}, cookie=pm)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.job(out)["invoices"][-1]["approval"]["waiting"], [])
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/send", {"reviewed": stamp}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.job(out)["invoices"][-1]["status"], "sent")
        # A client's own list replaces it; an empty one means nobody for that client.
        payer_id = self.job(out)["payer_id"]
        res, out = self.request("POST", f"/api/payers/{payer_id}/approvers", {"user_ids": []}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(next(p for p in out["payers"] if p["id"] == payer_id)["payapp_approvers"], [])
        # Someone who leaves comes off the lists.
        res, out = self.request("POST", f"/api/team/{pm_user['id']}/remove", {}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        _, state = self.request("GET", "/api/state", cookie=self.owner)
        self.assertEqual(state["company"]["payapp_approvers"], [])


if __name__ == "__main__":
    unittest.main()
