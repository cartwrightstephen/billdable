"""The old name, Billdable, survives only in the compatibility shims (R2-09): old BILLDABLE_*
settings, an old billdable.db, older billdable-* backups, and the notes that say so."""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = re.compile("billdable", re.I)

# File -> lines allowed to name it. The plan names the shims too, and this test names the word.
ALLOWED = {
    "backup.py": 1,        # OLD_PREFIX for older backups
    "store.py": 2,         # OLD_DB_NAME, and the docstring that reads it
    "storage.py": 3,       # BILLDABLE_* settings still work
    "render.yaml": 1,      # the services' old names
    "README.md": 1,        # the note on the rename
    "docs/improvement-plan.md": None,
    "tests/test_old_name.py": None,
}


class OldNameTests(unittest.TestCase):
    def test_old_name_only_in_the_shims(self):
        try:
            listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        found = {}
        for name in filter(None, listed.decode().split("\0")):
            path = ROOT / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # images, fonts
            lines = [n for n, line in enumerate(text.splitlines(), 1) if OLD.search(line)]
            if lines:
                found[name] = lines
        for name, lines in found.items():
            with self.subTest(file=name):
                self.assertIn(name, ALLOWED, f"{name} names the old product on lines {lines}")
                if ALLOWED[name] is not None:
                    self.assertLessEqual(len(lines), ALLOWED[name], f"{name} names the old product on lines {lines}")
        self.assertIn("storage.py", found, "the BILLDABLE_* shim is kept")


if __name__ == "__main__":
    unittest.main()
