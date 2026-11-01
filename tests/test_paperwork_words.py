"""Paperwork, not packet (Oct 23 note 21): no sentence the server writes for people says packet.

Reads every string in the server's Python (emails, refusals, the customer's page, PDFs, the
activity log, the demo company) and fails on prose like "the packet" or "Closeout packet". Code
names stay: JSON keys ("packet"), routes (/money/packet), function names and docstrings. A trade
term in parentheses, "Paperwork (packet)", is allowed, as on the Help page."""

import io
import re
import sys
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# "packet" as a word in a sentence (a string with a space in it): not part of a name like
# packet_items, /money/packet, job['packet'] or {packet}, and not "(packet)".
PROSE = re.compile(r"""(?i)(?<![/_\-'"\w.{])packets?(?![_\-'"\w)(}])""")
# The old words a migration looks for, so it can replace them.
ALLOWED = {("migrations.py", '"Missing required packet items."')}


def packet_prose(source: str) -> list[tuple[int, str]]:
    """(line, string) for each string literal, not a docstring, that says packet in prose."""
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.STRING:
            continue
        body = tok.string.lstrip("rbuRBUfF")
        if body.startswith(('"""', "'''")):
            continue  # docstrings explain the code, for the people who work on it
        body = body[1:-1]  # without its quotes
        if " " in body and PROSE.search(body):
            out.append((tok.start[0], tok.string))
    return out


class PaperworkWordsTests(unittest.TestCase):
    def test_the_pattern(self):
        self.assertTrue(packet_prose('x = "Closeout packet incomplete."\n'))
        self.assertTrue(packet_prose('x = number or "the job packet"\n'))
        self.assertTrue(packet_prose('x = "Missing required packet items."\n'), "capitalised or after a little word")
        self.assertTrue(packet_prose('x = "PACKET CHECKLIST"\n'))
        self.assertEqual(packet_prose('x = job["packet"]\ny = "/money/packet/1 x"\nz = "Paperwork (packet)"\n'
                                      'w = f"{packet}, {sug[\'text\']}"\nv = f"see {job[\'packet\']} now"\nu = "packet_items here"\n'), [])
        self.assertEqual(packet_prose('def f():\n    """Build the packet."""\n'), [])

    def test_no_server_text_says_packet(self):
        files = sorted(ROOT.glob("*.py")) + sorted((ROOT / "domain").glob("*.py"))
        left = [f"{p.name}:{line}: {text}" for p in files for line, text in packet_prose(p.read_text(encoding="utf-8"))
                if (p.name, text) not in ALLOWED]
        self.assertEqual(left, [])

    def test_old_default_is_replaced_on_saved_customers(self):
        import migrations
        from seed import seed_state
        state = seed_state()
        state["schema_version"] = 2
        state["payers"][0]["rejects_when"] = "Missing required packet items."
        state["payers"][1]["rejects_when"] = "They want the packet stapled."  # typed by the person: left alone
        self.assertTrue(migrations.migrate(state))
        self.assertEqual(state["payers"][0]["rejects_when"], "Missing required paperwork.")
        self.assertEqual(state["payers"][1]["rejects_when"], "They want the packet stapled.")
        self.assertEqual(state["schema_version"], migrations.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
