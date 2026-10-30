"""Clients, not customers or payers (Stephen's Oct 29 review, G7): Users are the people who use
Duracash; Clients are who pay them. No sentence the server writes for people (emails, refusals,
the client's page, the calendar feed, the helper, the activity log) calls a client a customer or
a payer. Code names stay: keys ("payer_id"), routes, function names, docstrings, log messages and
the patterns that read other people's files (a QuickBooks "Customer" column, a contract's
"Customer:" line)."""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORD = re.compile(r"""(?<![\w./\-{'"])(?:[Cc]ustomers?|[Pp]ayers?)(?:'s)?(?![\w\-'"(}])""")
# Strings that read what others wrote, or go only to the server log.
ALLOWED = {
    "contract_import.py": ("(?im)",),
    "contact.py": (" on the (?:payer",),
    "qbo_csv.py": ("customer name", "payer name", "No header row with an Amount column and a Customer or Num column"),
    "server.py": ("payer question emailed",),
}


def client_words(source: str, name: str = "") -> list[tuple[int, str]]:
    """(line, string) for each string, not a docstring, that says customer or payer in a sentence."""
    tree = ast.parse(source)
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            docs.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            text = node.value
            if " " in text.strip() and WORD.search(text) and not text.startswith(ALLOWED.get(name, ())):
                out.append((node.lineno, text))
    return out


class ClientWordsTests(unittest.TestCase):
    def test_the_pattern(self):
        self.assertTrue(client_words('x = "Emails the customer."\n'))
        self.assertTrue(client_words('x = f"{who} marked it on the payer page."\n'))
        self.assertTrue(client_words('x = "The Customer\'s page"\n'))
        self.assertEqual(client_words('x = p["payer_id"]\ny = "/api/payers/1 x"\nz = f"{payer} paid"\n'
                                      'def f():\n    """The payer\'s page."""\n'), [])

    def test_no_server_text_says_customer_or_payer(self):
        files = sorted(ROOT.glob("*.py")) + sorted((ROOT / "domain").glob("*.py"))
        left = [f"{p.name}:{line}: {text[:100]}" for p in files for line, text in client_words(p.read_text(encoding="utf-8"), p.name)]
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
