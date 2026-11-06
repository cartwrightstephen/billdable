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
