"""Company storage (P2-5): SQLite rows, the JSON fallback, the one-time import and backups."""

import io
import json
import sqlite3
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backup
import domain
import storage
import store
from seed import seed_state


class SqliteStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "companies"
        self.db = store.SqliteStore(self.folder)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_keeps_everything_including_empty_lists(self):
        state = seed_state()
        state["inbox"] = []
        self.db.save("co-1", state)
        fresh = store.SqliteStore(self.folder).load("co-1")  # another process, no cache
        self.assertEqual(fresh, state)
        self.assertEqual(domain.check_audit(fresh)["ok"], True)
        self.assertIsNone(self.db.load("co-nope"))
        self.assertEqual(self.db.ids(), ["co-1"])

    def test_a_save_writes_only_what_changed(self):
        self.db.save("co-1", seed_state())
        state = self.db.load("co-1")
        rows = sum(len(state[k]) for k in store.ROW_KINDS)
        job = state["jobs"][0]
        domain.audit(state, job["id"], "note", "A small change.", "Pat")
        job["done_notes"] = "changed"
        self.db.save("co-1", state)
        self.assertLessEqual(self.db.last_rows_written, 3, "the head, one job and one audit row")
        self.assertLess(self.db.last_rows_written, rows)
        state["jobs"].pop()  # a deleted job: the tail row goes
        self.db.save("co-1", state)
        self.assertEqual(store.SqliteStore(self.folder).load("co-1"), state)

    def test_a_write_from_elsewhere_is_not_overwritten_by_a_stale_cache(self):
        self.db.save("co-1", seed_state())
        state = self.db.load("co-1")
        other = store.SqliteStore(self.folder)
        theirs = other.load("co-1")
        theirs["company"]["name"] = "Renamed elsewhere"
        theirs["jobs"][0]["name"] = "Renamed job"
        other.save("co-1", theirs)
        state["jobs"][1]["done_notes"] = "mine"
        self.db.save("co-1", state)  # version moved on: written whole, so the result is exactly this state
        self.assertEqual(store.SqliteStore(self.folder).load("co-1"), state)

    def test_companies_save_at_the_same_time(self):
        errors = []

        def work(n):
            try:
                for i in range(10):
                    st = seed_state()
                    st["company"]["name"] = f"Co {n} v{i}"
                    self.db.save(f"co-{n}", st)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual([self.db.load(f"co-{n}")["company"]["name"] for n in range(6)], [f"Co {n} v9" for n in range(6)])

    def test_delete(self):
        self.db.save("co-1", seed_state())
        self.db.delete("co-1")
        self.assertFalse(self.db.exists("co-1"))
        self.assertEqual(sqlite3.connect(self.folder / store.DB_NAME).execute("SELECT COUNT(*) FROM company_rows").fetchone()[0], 0)

    def test_json_files_are_imported_once_and_moved_aside(self):
        self.folder.mkdir(parents=True)
        state = seed_state()
        storage.write_json(self.folder / "co-old.json", state)
        self.assertEqual(self.db.load("co-old"), state)
        self.assertFalse((self.folder / "co-old.json").exists())
        moved = list(self.folder.glob("imported-json-*/co-old.json"))
        self.assertEqual(len(moved), 1)
        self.assertEqual(json.loads(moved[0].read_text()), state)


class BackupWithSqliteTests(unittest.TestCase):
    def test_backup_carries_a_consistent_database_that_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "auth.json").write_text("{}")
            db = store.SqliteStore(root / "companies")
            db.save("co-1", seed_state())
            with mock.patch.object(storage, "DATA_DIR", root):
                path = backup.make_backup()
            with tarfile.open(path) as tar:
                self.assertIn("companies/billdable.db", tar.getnames())
                data = tar.extractfile("companies/billdable.db").read()
            copy = root / "restored" / "companies"
            copy.mkdir(parents=True)
            (copy / store.DB_NAME).write_bytes(data)
            self.assertEqual(store.SqliteStore(copy).load("co-1"), db.load("co-1"))


class RestoreDrillTests(unittest.TestCase):
    def make(self, root, tamper=False, orphan=False):
        users = [{"id": "u1", "company_id": "co-1"}] + ([{"id": "u2", "company_id": "co-gone"}] if orphan else [])
        (root / "auth.json").write_text(json.dumps({"users": users, "sessions": {}}))
        state = seed_state()
        if tamper:
            state["audit"][0]["detail"] = "changed after the fact"
        store.SqliteStore(root / "companies").save("co-1", state)
        with mock.patch.object(storage, "DATA_DIR", root), mock.patch.dict("os.environ", {"BACKUP_UPLOADS": "0"}):
            return backup.make_backup()

    def test_a_good_backup_passes_without_touching_the_live_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.make(root)
            before = sorted(p.name for p in root.rglob("*"))
            with mock.patch.dict("os.environ", {"BACKUP_UPLOADS": "0"}):
                report, problems = backup.drill(path)
            self.assertEqual(problems, [])
            self.assertTrue(any("co-1" in line and "audit ok" in line for line in report))
            self.assertEqual(sorted(p.name for p in root.rglob("*")), before)

    def test_problems_are_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make(Path(tmp), tamper=True, orphan=True)
            with mock.patch.dict("os.environ", {"BACKUP_UPLOADS": "0"}):
                _, problems = backup.drill(path)
            self.assertTrue(any("audit chain is broken" in p for p in problems))
            self.assertTrue(any("co-gone" in p for p in problems))


class BackendChoiceTests(unittest.TestCase):
    def test_sqlite_by_default_json_on_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"BILLDABLE_STORAGE": ""}):
                self.assertEqual(store.open_store(Path(tmp)).kind, "sqlite")
            with mock.patch.dict("os.environ", {"BILLDABLE_STORAGE": "json"}):
                self.assertEqual(store.open_store(Path(tmp)).kind, "json")


if __name__ == "__main__":
    unittest.main()
