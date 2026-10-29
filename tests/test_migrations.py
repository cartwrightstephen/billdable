"""Schema versions (P1-12) and the hash-chained audit trail (P1-11)."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import migrations
from seed import seed_state


def version_1_file():
    """A company file as code before schema versions wrote it: no version, no chain, missing lists."""
    state = seed_state()
    state.pop("schema_version")
    state.pop("email_log")
    for row in state["audit"]:
        row.pop("prev", None)
        row.pop("hash", None)
    return state


class MigrationTests(unittest.TestCase):
    def test_version_1_file_is_brought_up_to_date(self):
        state = version_1_file()
        self.assertTrue(migrations.migrate(state))
        self.assertEqual(state["schema_version"], migrations.SCHEMA_VERSION)
        self.assertEqual(state["email_log"], [])
        self.assertTrue(domain.check_audit(state)["ok"])
        again = copy.deepcopy(state)
        self.assertFalse(migrations.migrate(again), "a current file is left alone")
        self.assertEqual(again, state)

    def test_newer_file_is_refused(self):
        state = seed_state()
        state["schema_version"] = migrations.SCHEMA_VERSION + 1
        with self.assertRaises(ValueError):
            migrations.migrate(state)


class WriteBackTests(unittest.TestCase):
    def test_a_version_1_file_is_current_on_disk_after_one_read(self):  # R6-08
        import tempfile
        from unittest import mock
        import server
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(server, "COMPANIES", Path(tmp) / "companies"), \
                mock.patch.object(server, "_deleted_path", lambda: Path(tmp) / "deleted_companies.json"):
            server.company_store().save("co-old", version_1_file())
            self.assertNotIn("schema_version", server.company_store().load("co-old"))
            state = server.load_company("co-old")
            self.assertEqual(state["schema_version"], migrations.SCHEMA_VERSION)
            on_disk = server.company_store().load("co-old")
            self.assertEqual(on_disk["schema_version"], migrations.SCHEMA_VERSION, "saved without waiting for a change")
            self.assertTrue(domain.check_audit(on_disk)["ok"])
            self.assertEqual(on_disk["email_log"], [])


class AuditChainTests(unittest.TestCase):
    def state_with_rows(self):
        state = seed_state()
        job = state["jobs"][0]["id"]
        for n in range(3):
            domain.audit(state, job, "note", f"row {n}", "owner")
        return state

    def test_new_rows_extend_the_chain(self):
        state = self.state_with_rows()
        rows = state["audit"]
        self.assertEqual(rows[-1]["prev"], rows[-2]["hash"])
        self.assertEqual(domain.check_audit(state), {"ok": True, "rows": len(rows), "broken_at": None})

    def test_an_edited_row_breaks_the_chain_there(self):
        state = self.state_with_rows()
        state["audit"][2]["detail"] = "something else"
        self.assertEqual(domain.check_audit(state)["broken_at"], state["audit"][2]["id"])

    def test_a_removed_row_breaks_the_chain(self):
        state = self.state_with_rows()
        removed = state["audit"].pop(1)
        result = domain.check_audit(state)
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["broken_at"], removed["id"])

    def test_snapshot_reports_the_chain(self):
        self.assertTrue(domain.snapshot(self.state_with_rows())["audit_chain"]["ok"])


if __name__ == "__main__":
    unittest.main()
