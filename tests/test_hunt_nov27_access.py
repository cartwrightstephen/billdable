"""Nov 27 hunt (access, security, accounts, integrations): what each fix promises."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_check
import auth
import backup
import server
import storage
import store
from test_accounts import ServerCase, PASSWORD

JOB = {"name": "Elm", "customer": "Park GC", "address": "9 Elm Street", "approver": "Kim", "ap_email": "ap@park.example",
       "contract_cents": 100000, "role": "GC", "phone": "(404) 555-0100"}


class AuditAnchorAfterResetAndRestore(ServerCase):
    def check(self, company_id):
        with server.company_lock(company_id):
            state = server.load_company(company_id)
        return dict(audit_check.check_all([(company_id, state)]))[company_id]

    def test_a_reset_starts_a_new_trail_without_an_alarm(self):
        cookie, data = self.signup("reset-anchor@pine.example")
        company_id = data["user"]["company_id"]
        for n in range(3):
            res, _ = self.request("POST", "/api/jobs", {**JOB, "name": f"Elm {n}"}, cookie=cookie)
            self.assertEqual(res.status, 200)
        self.assertTrue(self.check(company_id)["ok"], "anchored at today's trail")
        res, out = self.request("POST", "/api/reset", {"confirm": "Pine & Sons", "password": PASSWORD}, cookie=cookie)
        self.assertEqual(res.status, 200, out)
        result = self.check(company_id)
        self.assertTrue(result["ok"], f"a reset is not a changed trail: {result}")
        # A trail cut short after the reset is still caught.
        self.request("POST", "/api/jobs", JOB, cookie=cookie)
        self.assertTrue(self.check(company_id)["ok"])
        with server.company_lock(company_id):
            state = server.load_company(company_id)
            state["audit"].pop()
            server.save_company(company_id, state)
        self.assertFalse(self.check(company_id)["ok"])

    def test_a_deleted_company_leaves_no_anchor(self):
        cookie, data = self.signup("delete-anchor@pine.example")
        company_id = data["user"]["company_id"]
        self.assertTrue(self.check(company_id)["ok"])
        self.assertIn(company_id, json.loads(audit_check.anchors_path().read_text()))
        server.delete_company_data(company_id)
        self.assertNotIn(company_id, json.loads(audit_check.anchors_path().read_text()))

    def test_a_restored_backup_is_checked_afresh(self):
        cookie, data = self.signup("restore-anchor@pine.example")
        company_id = data["user"]["company_id"]
        made = backup.make_backup(server.BACKUP_LOCKS)
        for n in range(3):
            self.request("POST", "/api/jobs", {**JOB, "name": f"Oak {n}"}, cookie=cookie)
        self.assertTrue(self.check(company_id)["ok"])
        backup.restore(made)
        # As audit_check.py reads it after a restore (the server is stopped for one): from the disk.
        folder = storage.DATA_DIR / "companies"
        state = (store.SqliteStore(folder) if store.db_file(folder).exists() else store.JsonStore(folder)).load(company_id)
        self.assertEqual(state["jobs"], [], "the backup's trail, made before the jobs")
        result = dict(audit_check.check_all([(company_id, state)]))[company_id]
        self.assertTrue(result["ok"], f"an older trail put back by a restore is not a changed trail: {result}")


class TeamManagersCantHandOutMore(ServerCase):
    """Someone who manages the team but isn't an Owner can't give themselves, a colleague or an
    invite a profile that can do more than they can (A4's aim: "someone holding team can't give
    themselves more")."""

    def setUp(self):
        super().setUp()
        self.owner, data = self.signup(f"mgr{id(self)}@pine.example")
        self.company = data["user"]["company_id"]
        for name, perms in (("Office", ["team", "send_payapps"]), ("Co-owner", [k for k, _ in server.profiles.PERMISSIONS])):
            res, out = self.request("POST", "/api/team/profiles", {"name": name, "perms": perms}, cookie=self.owner)
            self.assertEqual(res.status, 200, out)
        team = self.request("GET", "/api/team", cookie=self.owner)[1]
        self.ids = {p["name"]: p["id"] for p in team["profiles"]}
        mgr = auth.create_user(f"office{id(self)}@pine.example", PASSWORD, "Olga Office", self.company, role="member")
        clerk = auth.create_user(f"clerk{id(self)}@pine.example", PASSWORD, "Cal Clerk", self.company, role="member")
        auth.set_role(self.company, mgr["id"], self.ids["Office"], actor_id=data["user"]["id"], profiles=list(self.ids.values()))
        self.mgr, self.clerk = mgr, clerk
        self.mgr_cookie = "duracash_session=" + auth.create_session(auth.user_by_email(mgr["email"]))

    def test_not_to_themselves_a_colleague_or_an_invite(self):
        for who in (self.mgr["id"], self.clerk["id"]):
            res, out = self.request("POST", f"/api/team/{who}/role", {"profile_id": self.ids["Co-owner"]}, cookie=self.mgr_cookie)
            self.assertEqual(res.status, 409, out)
            self.assertIn("more than you can", out["error"])
        res, out = self.request("POST", "/api/team/invite", {"email": "new@pine.example", "profile_id": self.ids["Co-owner"]}, cookie=self.mgr_cookie)
        self.assertEqual(res.status, 409, out)
        self.assertEqual(auth.user_by_email(self.mgr["email"])["profile_id"], self.ids["Office"])
        # What they hold themselves, or less, they can still hand out; an owner hands out anything.
        res, out = self.request("POST", f"/api/team/{self.clerk['id']}/role", {"profile_id": self.ids["Office"]}, cookie=self.mgr_cookie)
        self.assertEqual(res.status, 200, out)
        res, out = self.request("POST", f"/api/team/{self.clerk['id']}/role", {"profile_id": self.ids["Co-owner"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)


class SensitiveFilesStayClosedEverywhere(ServerCase):
    def test_the_payments_reader_doesnt_open_a_payroll_file_for_someone_without_the_permission(self):
        owner, data = self.signup("payroll-csv@pine.example")
        company_id = data["user"]["company_id"]
        res, snap = self.request("POST", "/api/jobs", JOB, cookie=owner)
        job_id = snap["jobs"][0]["id"]
        res, out = self.request("POST", f"/api/import/file?job={job_id}&file=1&kind=payroll", raw=b"Name,Total\nJo Welder,1234.56\n", cookie=owner,
                                headers={"Content-Type": "text/csv", "X-Filename": "certified-payroll.csv"})
        self.assertEqual(res.status, 200, out)
        file_id = next(f["id"] for f in out["files"] if f["name"] == "certified-payroll.csv")
        auth.create_user("crew@pine.example", PASSWORD, "Cy Crew", company_id, role="member")
        crew = "duracash_session=" + auth.create_session(auth.user_by_email("crew@pine.example"))
        res, out = self.request("POST", "/api/import/payments-csv", {"file_id": file_id}, cookie=crew)
        self.assertEqual(res.status, 403, out)
        self.assertNotIn("Jo Welder", json.dumps(out))
        res, out = self.request("POST", "/api/import/payments-csv", {"file_id": file_id}, cookie=owner)
        self.assertEqual(res.status, 200, out)


if __name__ == "__main__":
    unittest.main()
