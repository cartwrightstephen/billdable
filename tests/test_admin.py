"""admin.py: the founders' shell commands (P1-14)."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import admin
import auth
import helper
import importer
import server
import storage


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        patches = [mock.patch.object(auth, "AUTH_PATH", root / "auth.json"), mock.patch.object(server, "COMPANIES", root / "companies"),
                   mock.patch.object(server, "_deleted_path", lambda: root / "deleted_companies.json"),
                   # Deletion also cleans inbound tokens, uploads, the helper log and the mail counter.
                   mock.patch.object(storage, "DATA_DIR", root), mock.patch.object(importer, "UPLOADS", root / "uploads"),
                   mock.patch.object(helper, "LOG_PATH", root / "helper_log.json")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        auth.create_user("owner@pine.example", "correct horse battery", "Pat", "co-pine")
        server.save_company("co-pine", server.empty_state("Pine Builders"))

    def run_admin(self, *args, typed=""):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("builtins.input", lambda _: typed):
            code = admin.main(["admin.py", *args])
        return code, out.getvalue()

    def test_companies_and_find(self):
        code, out = self.run_admin("companies")
        self.assertEqual(code, 0)
        self.assertIn("co-pine  Pine Builders  users=1  jobs=0", out)
        self.assertIn("owner of co-pine", self.run_admin("find", "owner@pine.example")[1])

    def test_delete_company_needs_the_id_typed(self):
        code, out = self.run_admin("delete-company", "co-pine", typed="yes")
        self.assertEqual(code, 1)
        self.assertTrue(server.company_path("co-pine").exists())
        code, out = self.run_admin("delete-company", "co-pine", typed="co-pine")
        self.assertEqual(code, 0)
        self.assertFalse(server.company_path("co-pine").exists())
        self.assertIsNone(auth.user_by_email("owner@pine.example"))


if __name__ == "__main__":
    unittest.main()
