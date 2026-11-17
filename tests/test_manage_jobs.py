"""Round 1, item 2: changing a job's contract, its change orders and marking work done take the
manage_jobs permission. Owner holds it; Member doesn't; a custom profile from before it existed
gets it when it held void or close_jobs. The server refuses what the page hides."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrations
import server
from domain import profiles
from seed import seed_state
from test_accounts import ServerCase
from test_team import JOB, TeamHttpTests


class ManageJobsRuleTests(unittest.TestCase):
    def test_the_permission_and_the_actions_it_gates(self):
        self.assertIn("manage_jobs", profiles.PERM_KEYS)
        self.assertEqual(profiles.PERM_WORDS["manage_jobs"], "Change a job's contract, change orders and mark work done")
        for action in ("done", "undo-done", "approve-co", "withdraw-co", "include-co", "edit"):
            self.assertEqual(profiles.JOB_ACTION_PERMS[action], "manage_jobs", action)
        # Members keep records, conversations, drafts, progress and documents.
        for action in ("progress", "sov-progress", "draft", "add-co", "note-contact", "daily-record", "attach", "packet-confirm", "add-record"):
            self.assertIsNone(profiles.JOB_ACTION_PERMS[action], action)

    def test_owner_has_it_and_member_does_not(self):
        state = seed_state()
        self.assertIn("manage_jobs", profiles.perms_of({"role": "owner"}, state))
        self.assertNotIn("manage_jobs", profiles.perms_of({"role": "member"}, state))
        state["company"].pop("profiles", None)
        self.assertNotIn("manage_jobs", profiles.perms_of({"role": "member"}, state))

    def test_the_upgrade_gives_it_to_custom_profiles_that_void_or_close_jobs(self):
        state = seed_state()
        state["company"]["profiles"] = [
            {"id": "owner", "name": "Owner", "built_in": True, "locked": True},
            {"id": "member", "name": "Member", "built_in": True, "perms": ["record_payments", "void"]},
            {"id": "prof-a", "name": "Office", "perms": ["void"]},
            {"id": "prof-b", "name": "PM", "perms": ["close_jobs", "record_payments"]},
            {"id": "prof-c", "name": "Bookkeeper", "perms": ["record_payments"]},
        ]
        state["schema_version"] = migrations.STEPS.index(migrations._round1_manage_jobs) + 1
        migrations.migrate(state)
        perms = {p["id"]: p.get("perms") for p in state["company"]["profiles"]}
        self.assertEqual(perms["prof-a"], ["void", "manage_jobs"])
        self.assertEqual(perms["prof-b"], ["close_jobs", "record_payments", "manage_jobs"])
        self.assertEqual(perms["prof-c"], ["record_payments"])
        self.assertNotIn("manage_jobs", perms["member"], "Member is built in: an owner gives it on purpose")
        self.assertIsNone(perms["owner"], "Owner's permissions aren't stored")
        # Running it again changes nothing.
        before = [dict(p) for p in state["company"]["profiles"]]
        migrations._round1_manage_jobs(state)
        self.assertEqual(state["company"]["profiles"], before)

    def test_the_upgrade_leaves_a_company_without_profiles_alone(self):
        state = seed_state()
        state["company"].pop("profiles", None)
        migrations._round1_manage_jobs(state)
        self.assertNotIn("profiles", state["company"])

    def test_the_page_hides_what_a_member_cannot_do(self):
        js = (Path(__file__).resolve().parents[1] / "static/js/jobs.js").read_text()
        for action in ("done", "undo-done", "approve-co", "withdraw-co", "include-co", "edit"):
            self.assertRegex(js, rf'"?{action}"?:\s*"manage_jobs"')


class ManageJobsHttpTests(ServerCase):
    join = TeamHttpTests.join

    def setUp(self):
        super().setUp()
        tag = self._testMethodName.replace("_", "")[-20:]
        self.tag = tag
        self.owner, _ = self.signup(f"owner-{tag}@manage.example", "Manage Co")
        self.member, _ = self.join(self.owner, f"fred-{tag}@manage.example")
        _, state = self.request("POST", "/api/jobs", JOB, cookie=self.owner)
        self.job_id = state["jobs"][0]["id"]
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/add-co", {"title": "Rail", "description": "Add a rail", "amount_cents": 50_000,
                                                                           "reason": "owner_request"}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.co_id = next(j for j in out["jobs"] if j["id"] == self.job_id)["change_orders"][0]["id"]

    def refused(self, path, body=None):
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/{path}", body or {}, cookie=self.member)
        self.assertEqual((res.status, out.get("error")), (403, server.NO_PERMISSION), path)

    def test_a_member_is_refused_each_one(self):
        self.refused("done", {"notes": ""})
        self.refused(f"approve-co/{self.co_id}", {"record_id": "x", "signer_name": "Kim", "signed_on": "2026-11-01"})
        self.refused(f"withdraw-co/{self.co_id}")
        self.refused(f"include-co/{self.co_id}")
        self.refused("edit", {"name": "Elm renamed"})
        self.refused("undo-done")
        # What a member keeps: progress, a conversation, a change order of their own.
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/note-contact", {"text": "Check Friday"}, cookie=self.member)
        self.assertEqual(res.status, 200, out)
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/add-co", {"title": "Gate", "description": "Add a gate", "amount_cents": 20_000,
                                                                           "reason": "owner_request"}, cookie=self.member)
        self.assertEqual(res.status, 200, out)

    def test_a_member_cannot_change_the_contract_through_the_schedule(self):
        lines = [{"description": "Framing", "quantity": "1", "unit": "LS", "rate": "12,000"}]
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/sov", {"lines": lines, "update_contract": True}, cookie=self.member)
        self.assertEqual((res.status, out.get("error")), (403, server.NO_PERMISSION))

    def test_the_owner_may(self):
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/include-co/{self.co_id}", {}, cookie=self.owner)
        self.assertNotEqual(res.status, 403, out)
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/edit", {"name": "Elm renamed"}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)

    def test_a_profile_with_it_may(self):
        res, out = self.request("POST", "/api/team/profiles", {"name": "Project manager", "perms": ["manage_jobs"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        pm, _ = self.join(self.owner, f"pam-{self.tag}@manage.example", out["profile"]["id"])
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/withdraw-co/{self.co_id}", {}, cookie=pm)
        self.assertEqual(res.status, 200, out)


if __name__ == "__main__":
    unittest.main()
