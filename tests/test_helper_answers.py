"""The helper's prepared answers use the app's words (the glossary, docs/improvement-plan.md section 4).

Each answer tells a contractor which button, tab or heading to tap, so it has to name them as
the app shows them today. This reads helper/answers.json as shipped and fails on:
- an old word in what the helper shows: the answer text, its button labels, its follow-up
  chips, or the first question (the helper's "Did you mean" lists it). The OLD list mirrors
  tests/js/glossary.mjs, written for prose. A trade term may stay in parentheses, as the Help
  page has it ("Paperwork (packet)"), and in the other questions and keywords, so a contractor
  who types "packet", "customer" or "Today" is still understood;
- a nav button to a page that has no address (VIEW_PATHS in static/js/routes.js);
- a follow-up chip that is not a question of any answer.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANSWERS = ROOT / "helper" / "answers.json"
ROUTES = ROOT / "static" / "js" / "routes.js"

# Old word -> what the app says now. Case-insensitive unless the pattern sets (?-i).
OLD = {
    r"\bpackets?\b": "Paperwork / Open paperwork",
    r"\bpayers?\b": "client",
    r"\bcustomers?('s)?\b": "client (Oct 29 review: Clients pay the Users)",
    r"\bcustomer settings\b": "Client playbook",
    r"(?<!client )(?<!client's )\bplaybooks?\b": "Client playbook",
    r"\bpencil (copy|draft)\b": "Send draft for their review",
    r"\bmatch(ed|ing)? (a |the )?payments?\b|\bpayments matched\b": "Record a payment / Payments received",
    r"\bmark(ed)? present\b": "I have this… / Marked as in hand",
    r"\bvoid(ed|s)?\b": "Delete draft (a sent one: Pull back this pay app…)",
    r"\bclosed? for (the )?record\b": "Finish job",
    r"\bmoney file\b": "Money",
    r"\b(approved|pending) (cos|change orders?)\b": "Signed / Unsigned change orders",
    r"\bput on next pay app\b": "Bill now (send waits for signature)",
    r"\bretainage open\b": "Held back, still owed",
    r"\bfile to job record\b|\bto file \(": "Add to job / Waiting (N)",
    r"\bcompleted[- ]to[- ]date\b": "Total done so far $",
    r"\bcompletion notes\b": "Anything to note? (optional)",
    r"\bnotes for the job record\b": "Notes (kept on the job record)",
    r"\bowners should turn this on\b": "Recommended for owners.",
    r"\bap (email|address|contact)\b": "Email for pay apps",
    r"\bapprover\b": "Who signs off on your bills",
    r"\bapprove send\b|\bdraft skipped\b": "Pay app sent / Draft replaced",
    r"pay app / invoice backup": "Backup for the pay app",
    r"\bcollections\b": "Unpaid (a Money tab)",
    r"(?-i:\bToday\b)(?=,| shows| and Settings| page| tab)|\bopen today\b|\bgo to today\b": "Dashboard (the page; today the day stays)",
    r"(?-i:\bHelp\b)(?= page| tab|,)|\bsettings, help\b": "(Help was emptied, Oct 29 review)",
    r"\baudit\b": "Activity log",
    r"\bblockers?\b": "Fix N things before sending / Before you can finish this job",
    r"\bchase retainage\b": "Ask for retainage",
    r"\bteach (the )?playbook\b": "Client sent a pay app back? Note why.",
    r"\bleft rail\b": "Settings, Company",
    r"(?-i:\bChange Orders\b)": "Change orders",
    r"(?-i:\bDaily Record\b)": "Daily record",
    r"\bwho pays you\?": "Client type (J2, Oct 29 review)",
    r"\breopen job\b": "Add more work (J9, Oct 29 review)",
    r"\bai assisted\b": "(never used)",
    r"(?-i:\bNeeds you\b)": "To Do",
}

PARENTHESES = re.compile(r"\([^()]*\)")


def shown_text(entry):
    """Everything of an entry the helper puts on screen, with its labels."""
    out = [("answer", entry.get("answer", ""))]
    out += [(f"action {a.get('label')!r}", a.get("label", "")) for a in entry.get("actions", [])]
    out += [(f"follow-up {f!r}", f) for f in entry.get("follow_ups", [])]
    if entry.get("questions"):
        out.append(("first question", entry["questions"][0]))
    return out


def old_words(text):
    """The old words in text, outside parentheses (where the glossary keeps the trade terms)."""
    bare = text
    while PARENTHESES.search(bare):
        bare = PARENTHESES.sub(" ", bare)
    return [(m.group(0), new) for pattern, new in OLD.items() for m in re.finditer(pattern, bare, re.I)]


def view_paths():
    src = ROUTES.read_text(encoding="utf-8")
    body = re.search(r"export const VIEW_PATHS = \{(.*?)\};", src, re.S).group(1)
    return set(re.findall(r'(?:^|[\s,{])"?([\w-]+)"?\s*:\s*"/', body))


class HelperAnswersWordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.answers = json.loads(ANSWERS.read_text(encoding="utf-8"))["answers"]

    def test_the_old_word_check_works(self):
        self.assertEqual(old_words("Open the packet and click Match payment."), [("packet", "Paperwork / Open paperwork"), ("Match payment", "Record a payment / Payments received")])
        self.assertEqual(old_words("Paperwork (packet) and a draft for their review (pencil copy)."), [])
        self.assertTrue(old_words("Void draft"))
        self.assertTrue(old_words("Open Change Orders"))
        self.assertFalse(old_words("Open Change orders"))

    def test_answers_use_the_apps_words(self):
        found = {}
        for e in self.answers:
            for where, text in shown_text(e):
                hits = old_words(text)
                if hits:
                    found.setdefault(e["id"], []).append(f"{where}: " + "; ".join(f"{w!r} -> {new}" for w, new in hits))
        self.assertEqual(found, {})

    def test_nav_buttons_open_a_page_that_exists(self):
        views = view_paths()
        self.assertIn("dashboard", views)
        self.assertIn("helper-admin", views)
        bad = [(e["id"], a) for e in self.answers for a in e.get("actions", []) if a.get("type") == "nav" and a.get("view") not in views]
        self.assertEqual(bad, [])

    def test_follow_ups_are_questions_the_helper_has(self):
        questions = {q for e in self.answers for q in e.get("questions", [])}
        missing = [(e["id"], f) for e in self.answers for f in e.get("follow_ups", []) if f not in questions]
        self.assertEqual(missing, [])


    def test_follow_ups_and_first_questions_open_their_own_answer(self):
        # A chip or a "Did you mean" line the helper shows must lead back to the entry it names.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import helper
        owners = {}
        for e in self.answers:
            for q in e["questions"]:
                owners.setdefault(helper.normalize(q), set()).add(e["id"])
        shown = [q for e in self.answers for q in e.get("follow_ups", [])] + [e["questions"][0] for e in self.answers]
        wrong = []
        for q in shown:
            score, top = helper.rank(q, self.answers)[0]
            if top["id"] not in owners.get(helper.normalize(q), set()) or score < helper.ANSWER_AT:
                wrong.append((q, top["id"]))
        self.assertEqual(wrong, [])


# Questions the Oct 30 review asked that went to the wrong answer or none (D3): each opens its own.
ROUTES_D3 = {
    "how do I add a client": "add-client", "add client": "add-client", "new client": "add-client", "add a new client": "add-client",
    "how do I print": "print", "print a message": "print", "print an email": "print", "print the outbox": "print",
    "print something I received": "print", "print the pay app": "what-payapp",
    "everything received": "everything-received", "where is everything received": "everything-received",
    "what came in": "everything-received",
    "automations": "automations", "what are automations": "automations",
    "where did my email go": "email-sent", "where is the daily record": "daily-record", "how do I add a new job": "create-job",
    # Asking about clients isn't asking to add one; profiles are an owner's to change.
    "who is my client": "playbook", "where are my clients": "playbook", "where do I see my clients": "playbook",
    "can a member edit profiles": "edit-profiles", "who can change profiles": "edit-profiles",
}


# The last three of Stephen's Oct 29 review: New pay app on Money (G10), quantities from the
# contractor's own records (M1) and the CSV lists on Export (I1).
ROUTES_NOT_DONES = {
    "new pay app": "new-payapp", "can I make a pay app from money": "new-payapp", "where do I start a new pay app": "new-payapp",
    "import quantities": "import-quantities", "import quantities from excel": "import-quantities",
    "upload my takeoff quantities": "import-quantities", "how do I import quantities from my own records": "import-quantities",
    "export my jobs to excel": "export", "download pay apps as csv": "export", "export payments to a spreadsheet": "export",
    "how do i bill progress on a job": "progress", "what is a daily record": "daily-record",
}


class HelperRoutingTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(ROOT))
        import helper
        self.helper = helper
        self.answers = json.loads(ANSWERS.read_text(encoding="utf-8"))["answers"]

    def test_review_questions_open_their_answer(self):
        wrong = {}
        for q, want in ROUTES_D3.items():
            score, top = self.helper.rank(q, self.answers)[0]
            got = top["id"] if score >= self.helper.ANSWER_AT else None
            if got != want:
                wrong[q] = (got, want, round(score, 2))
        self.assertEqual(wrong, {})

    def test_the_last_review_items_open_their_answer(self):
        wrong = {}
        for q, want in ROUTES_NOT_DONES.items():
            score, top = self.helper.rank(q, self.answers)[0]
            got = top["id"] if score >= self.helper.ANSWER_AT else None
            if got != want:
                wrong[q] = (got, want, round(score, 2))
        self.assertEqual(wrong, {})

    def test_the_profiles_answer_says_only_owners_change_them(self):
        by_id = {e["id"]: e for e in self.answers}
        self.assertTrue(by_id["edit-profiles"]["answer"].startswith("Only an owner can add, change or delete permission profiles."))
        self.assertNotIn("client", [k for k in by_id["add-client"]["keywords"]], "a bare 'client' sent every client question to Add client")

    def test_answers_name_where_things_are_now(self):
        by_id = {e["id"]: e for e in self.answers}
        # Settings, Company has no How you bill since S4; pasted notes go in on Import.
        self.assertNotIn("How you bill", by_id["what-payapp"]["answer"])
        self.assertNotIn("in the Inbox as notes", by_id["daily-record"]["answer"])
        self.assertIn("Import", by_id["daily-record"]["answer"])
        src = (ROOT / "helper.py").read_text(encoding="utf-8")
        self.assertNotIn("paste an email or notes in the Inbox", src)
        self.assertNotIn("what needs you", src)


if __name__ == "__main__":
    unittest.main()
