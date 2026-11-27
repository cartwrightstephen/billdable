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


if __name__ == "__main__":
    unittest.main()
