"""Fixes from the independent hunt of Round 1 (Nov 6): every way to mark work done or approve a
change order takes manage_jobs, not only the job page's buttons (h1_manage_jobs.py)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import server
from domain import profiles
from test_accounts import ServerCase
from test_team import JOB, TeamHttpTests


class OtherRoutesToManageJobs(ServerCase):
    join = TeamHttpTests.join

    def setUp(self):
        super().setUp()
        tag = self._testMethodName.replace("_", "")[-20:]
        self.owner, _ = self.signup(f"o-{tag}@hunt.example", "Hunt Co")
        self.member, _ = self.join(self.owner, f"m-{tag}@hunt.example")
        _, st = self.request("POST", "/api/jobs", JOB, cookie=self.owner)
        self.job_id = st["jobs"][0]["id"]

    def job(self):
        _, st = self.request("GET", "/api/state", cookie=self.owner)
        return next(j for j in st["jobs"] if j["id"] == self.job_id)

    def says_done_item(self, cookie):
        res, out = self.request("POST", "/api/import/text", {"text": "Elm job is done, all finished", "source": "sms", "job_id": self.job_id}, cookie=cookie)
        self.assertEqual(res.status, 200, out)
        return next(i for i in out["inbox"] if i.get("says_done_job_id"))

    def test_a_member_cannot_mark_work_done_through_the_inbox(self):
        item = self.says_done_item(self.member)
        res, out = self.request("POST", f"/api/inbox/{item['id']}/mark-done", {}, cookie=self.member)
        self.assertEqual((res.status, out.get("error")), (403, server.NO_PERMISSION))
        job = self.job()
        self.assertNotEqual(job["work_status"], "complete")
        self.assertEqual([i for i in job["invoices"] if i["status"] != "void"], [])
        # Filing it is still theirs.
        res, out = self.request("POST", f"/api/inbox/{item['id']}/file", {"job_id": self.job_id, "kind": item["kind"]}, cookie=self.member)
        self.assertEqual(res.status, 200, out)

    def test_the_owner_still_can(self):
        item = self.says_done_item(self.owner)
        res, out = self.request("POST", f"/api/inbox/{item['id']}/mark-done", {}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.job()["work_status"], "complete")

    def add_co(self):
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/add-co", {"title": "Rail", "description": "Add a rail", "amount_cents": 50_000,
                                                                           "reason": "owner_request"}, cookie=self.member)
        self.assertEqual(res.status, 200, out)
        return next(j for j in out["jobs"] if j["id"] == self.job_id)["change_orders"][0]

    def upload_signed(self, co, cookie):
        return self.request("POST", f"/api/import/file?job={self.job_id}&file=1&kind=change_order&co={co['id']}&signer=Kim&signed_on=2026-11-01",
                            raw=b"%PDF-1.4 signed change order", headers={"X-Filename": "signed-co.pdf", "Content-Type": "application/octet-stream"},
                            cookie=cookie)

    def test_a_members_signed_copy_is_filed_with_the_change_order_but_does_not_approve_it(self):
        co = self.add_co()
        contract = self.job()["money"]["contract_total_cents"]
        res, out = self.upload_signed(co, self.member)
        self.assertEqual(res.status, 200, out)
        self.assertIn("Someone who can approve change orders", out.get("message", ""))
        job = self.job()
        now = job["change_orders"][0]
        self.assertEqual(now["status"], "pending")
        self.assertEqual(job["money"]["contract_total_cents"], contract)
        record = next(r for r in job["records"] if r["id"] in out["uploaded_record_ids"])
        self.assertIn(record["id"], now["support_record_ids"], "filed to the change order as its backup")
        self.assertEqual((now.get("signed_copy") or {}).get("record_id"), record["id"])
        self.assertIn("Kim", now["signed_copy"].get("signer_name", ""))

    def test_the_owners_signed_copy_still_approves_it(self):
        co = self.add_co()
        res, out = self.upload_signed(co, self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.job()["change_orders"][0]["status"], "approved")


DOLLAR_JOB = {"name": "Elm", "customer": "Park GC", "address": "9 Elm", "approver": "Kim", "ap_email": "ap@park.example",
              "contract_cents": 1_000_000, "retainage_pct": 10, "role": "Owner", "phone": "(404) 555-0100"}


class BillByDollars(ServerCase):
    """Bill by dollars on a one-line schedule (h2_dollars.py, h2b.py): the dollars typed are what the
    pay app bills, however small, and a new amount on the open draft updates it."""
    CONTRACT = 1_000_000

    def setUp(self):
        super().setUp()
        self.owner, _ = self.signup(f"o-{self._testMethodName[-20:]}-{self.CONTRACT}@dollars.example", "Dollar Co")
        res, st = self.request("POST", "/api/jobs", {**DOLLAR_JOB, "contract_cents": self.CONTRACT}, cookie=self.owner)
        self.assertEqual(res.status, 200, st)
        self.job_id = st["jobs"][0]["id"]

    def job(self):
        _, st = self.request("GET", "/api/state", cookie=self.owner)
        return next(j for j in st["jobs"] if j["id"] == self.job_id)

    def draft(self):
        return next((i for i in self.job()["invoices"] if i["status"] in ("draft", "awaiting_approval")), None)

    def bill(self, cents):
        """As static/js/jobs.js lumpDollars sends this draw: the % done to date rounded down to a
        tenth of a percent, and the exact dollars."""
        j = self.job()
        line = j["sov_view"]["lines"][0]
        base = sum(int(i.get("gross_cents") or 0) for i in j["invoices"] if i["status"] not in ("void", "draft", "awaiting_approval"))
        milli = (base + cents) * 1000 // line["scheduled_cents"]
        return self.request("POST", f"/api/jobs/{self.job_id}/sov-progress",
                            {"done": {line["id"]: f"{milli / 1000:.3f}"}, "amounts": {line["id"]: f"{cents / 100:.2f}"}, "notes": "", "period_to": ""},
                            cookie=self.owner)

    def test_exact_cents(self):
        res, out = self.bill(333_333)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.draft()["gross_cents"], 333_333)

    def test_a_draw_under_a_tenth_of_a_percent_bills_what_was_typed(self):
        res, out = self.bill(500)
        self.assertEqual(res.status, 200, out)
        d = self.draft()
        self.assertEqual((d["gross_cents"], d["retainage_cents"]), (500, 50))

    def test_a_new_amount_in_the_same_tenth_of_a_percent_updates_the_draft(self):
        self.assertEqual(self.bill(333_333)[0].status, 200)
        number = self.draft()["number"]
        res, out = self.bill(333_500)
        self.assertEqual(res.status, 200, out.get("error"))
        d = self.draft()
        self.assertEqual((d["number"], d["gross_cents"]), (number, 333_500))
        res, out = self.bill(340_000)
        self.assertEqual(res.status, 200, out.get("error"))
        self.assertEqual(self.draft()["gross_cents"], 340_000)
        res, out = self.bill(500)
        self.assertEqual(res.status, 200, out.get("error"))
        self.assertEqual(self.draft()["gross_cents"], 500)

    def test_a_percent_typed_after_dollars_replaces_the_dollars_on_that_line(self):
        self.assertEqual(self.bill(333_333)[0].status, 200)
        lid = self.job()["sov_view"]["lines"][0]["id"]
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/sov-progress", {"done": {lid: "0.600"}, "notes": ""}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.draft()["gross_cents"], 600_000)
        _, trail = self.request("GET", f"/api/timeline?job={self.job_id}&offset=0&limit=50", cookie=self.owner)
        said = " ".join(e.get("text", "") + " ".join(d.get("text", "") for d in e.get("details", [])) for e in trail.get("entries", []))
        self.assertIn("$3,333.33 changed by hand gives way to it", said)

    def test_the_percent_the_dollars_stood_at_typed_on_the_percent_form_bills_that_percent(self):
        self.assertEqual(self.bill(333_333)[0].status, 200)
        lid = self.job()["sov_view"]["lines"][0]["id"]
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/sov-progress", {"done": {lid: "0.333"}, "notes": ""}, cookie=self.owner)
        self.assertEqual(res.status, 200, out.get("error"))
        self.assertEqual(self.draft()["gross_cents"], 333_000)


class BillByDollarsOnABigLine(BillByDollars):
    CONTRACT = 120_000_000   # $1.2M: a $1,000 draw is 0.083%
    test_a_percent_typed_after_dollars_replaces_the_dollars_on_that_line = None
    test_the_percent_the_dollars_stood_at_typed_on_the_percent_form_bills_that_percent = None

    def test_a_draw_under_a_tenth_of_a_percent_bills_what_was_typed(self):
        res, out = self.bill(100_000)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(self.draft()["gross_cents"], 100_000)


class PercentToDateFloor(ServerCase):
    """New job record's % done to date (h3_todate.py): what a pay app typed is the pay app's, never
    the floor for the field. The floor is the field total only."""
    join = TeamHttpTests.join

    def setUp(self):
        super().setUp()
        tag = self._testMethodName.replace("_", "")[-20:]
        self.owner, _ = self.signup(f"o-{tag}@todate.example", "To Date Co")
        self.fore, _ = self.join(self.owner, f"f-{tag}@todate.example")
        _, st = self.request("POST", "/api/jobs", DOLLAR_JOB, cookie=self.owner)
        self.job_id = st["jobs"][0]["id"]
        self.line = self.view()["sov_view"]["lines"][0]["id"]

    def view(self):
        _, st = self.request("GET", "/api/state", cookie=self.owner)
        return next(j for j in st["jobs"] if j["id"] == self.job_id)

    def record(self, pct):
        return self.request("POST", f"/api/jobs/{self.job_id}/daily-record",
                            {"notes": "x", "sections": {"quantities": [{"line_id": self.line, "qty": pct, "to_date": True}]}}, cookie=self.fore)

    def test_a_pay_app_typed_before_any_record_is_not_the_floor(self):
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/sov-progress", {"done": {self.line: "0.500"}}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        res, out = self.record("40")
        self.assertEqual(res.status, 200, out.get("fields") or out.get("error"))
        line = self.view()["sov_view"]["lines"][0]
        self.assertEqual(line["field_milli"], 400)
        self.assertEqual(line["form_done_milli"], 500, "the draft keeps the figure typed on it")
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/void-draft", {}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        line = self.view()["sov_view"]["lines"][0]
        self.assertEqual((line["field_milli"], line["form_done_milli"]), (400, 400), "back to the field total once the draft is gone")
        res, out = self.record("45")
        self.assertEqual(res.status, 200, out.get("fields") or out.get("error"))
        self.assertEqual(self.view()["sov_view"]["lines"][0]["field_milli"], 450)


class MemberScheduleRefusal(ServerCase):
    """Round 1 acceptance, item 19: a schedule that doesn't total the contract tells a Member who
    can change the contract, not to do it themselves."""
    join = TeamHttpTests.join

    def test_the_refusal_says_who_can(self):
        owner, _ = self.signup("o-sched@hunt.example", "Sched Co")
        member, _ = self.join(owner, "m-sched@hunt.example")
        _, st = self.request("POST", "/api/jobs", JOB, cookie=owner)
        job_id = st["jobs"][0]["id"]
        lines = [{"description": "Framing", "quantity": "1", "unit": "LS", "rate": "12,000"}]
        res, out = self.request("POST", f"/api/jobs/{job_id}/sov", {"lines": lines}, cookie=member)
        self.assertEqual(res.status, 409, out)
        said = " ".join(out.get("blockers") or out.get("problems") or [])
        self.assertIn("takes someone who can change a job's contract", said)
        self.assertNotIn("save it and change the contract amount", said)
        res, out = self.request("POST", f"/api/jobs/{job_id}/sov", {"lines": lines}, cookie=owner)
        self.assertIn("save it and change the contract amount", " ".join(out.get("blockers") or out.get("problems") or []))


class EveryCallerIsGated(unittest.TestCase):
    """Each call that marks work done, undoes it, approves, withdraws or bills an unsigned change
    order, or changes the contract sits behind manage_jobs: grepped from the code, not the table."""

    GATED = ("mark_done(", "undo_mark_done(", "approve_change_order(", "withdraw_change_order(", "include_change_order(",
             "mark_co_signed(", "edit_job(", "file_and_mark_done(")

    def test_server_calls(self):
        import re
        src = (Path(__file__).resolve().parents[1] / "server.py").read_text()
        lines = src.splitlines()
        table = {m.group(1): m.group(2) for m in re.finditer(r'^    "([a-z-]+)": \((?:True|False), (\w+|lambda)', src, re.M)}
        for n, line in enumerate(lines):
            if not any(f"domain.{g}" in line or f"importer.{g}" in line for g in self.GATED):
                continue
            entry = re.match(r'^    "([a-z-]+)": \(', line)
            enclosing = next((m.group(1) for m in (re.match(r"\s*def (\w+)", l) for l in reversed(lines[:n])) if m), "")
            gated_by_table = (entry and profiles.JOB_ACTION_PERMS.get(entry.group(1)) == "manage_jobs") or any(
                fn == enclosing and profiles.JOB_ACTION_PERMS.get(action) == "manage_jobs" for action, fn in table.items())
            checked = re.search(r'may_take\([^)]*"(done|undo-done|approve-co|withdraw-co|include-co|edit)"', "\n".join(lines[max(0, n - 8):n + 1]))
            with self.subTest(line=n + 1, code=line.strip()[:80]):
                self.assertTrue(gated_by_table or checked, f"server.py:{n + 1} reaches a manage_jobs action without a check")

if __name__ == "__main__":
    unittest.main()
