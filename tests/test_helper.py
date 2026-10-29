"""The helper's prepared answers: matching, live-data answers, the learning loop.

The phrasing cases below guard the answers database: if an edit to
helper/answers.json breaks one, this test says which question moved.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import helper
from seed import seed_state

CASES = {
    "what should i do today": "today", "who owes me": "who-owes", "who hasn't paid me yet": "who-owes",
    "what's blocking oak": "blockers", "how do i get my retainage back": "release-retainage", "what is retainage": "what-retainage",
    "what's a pay app": "what-payapp", "can I sync with google calendar": "google-calendar", "forgot password": "password",
    "does it work with quickbooks": "quickbooks", "how much does it cost": "pricing", "customer paid half": "partial",
    "write a reminder email to turner": "draft-followup", "how do i import my apple notes": "import-notes",
    "change order not signed yet can i bill it": "unsigned-co", "how do i delete a job": "edit-job", "is my data safe": "security",
    "what is a pencil draft": "pencil", "can duracash call my customers": "voice-calls", "how to add a new job": "create-job",
    "can my bookkeeper log in": "team-users", "where do i see what i sent": "email-sent", "status of oak street": "job-status",
    "how do i delete my account": "delete-account", "is my data backed up": "backups",
    "how do i set up a schedule of values": "sov", "can i paste my estimate from excel": "sov", "bill by unit rates": "sov",
    "why did my email fail": "email-sent", "i forgot my password": "password", "how do i invite my office manager": "team-users",
    "i didn't get the confirmation email": "verify-email", "do you sell my data": "terms-privacy", "where are the terms of service": "terms-privacy", "when does my trial end": "pricing", "can i get a pdf of the pay app": "what-payapp", "did the customer get my pay app": "email-sent", "send the email again": "email-sent",
}


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (helper.LOCAL_KB, helper.LOG_PATH)
        helper.LOCAL_KB = Path(self.tmp.name) / "helper_answers.json"
        helper.LOG_PATH = Path(self.tmp.name) / "helper_log.json"

    def tearDown(self):
        helper.LOCAL_KB, helper.LOG_PATH = self._orig
        self.tmp.cleanup()

    def test_answers_database_is_valid(self):
        kb = helper.load_kb()
        ids = [e["id"] for e in kb]
        self.assertEqual(len(ids), len(set(ids)))
        handlers = set(helper.review()["handlers"])
        for e in kb:
            self.assertTrue(e["questions"], e["id"])
            self.assertTrue(e["answer"] or e.get("handler"), e["id"])
            if e.get("handler"):
                self.assertIn(e["handler"], handlers)

    def test_common_questions_match(self):
        wrong = {}
        for q, want in CASES.items():
            score, entry = helper.rank(q)[0]
            got = entry["id"] if score >= helper.ANSWER_AT else None
            if got != want:
                wrong[q] = (got, want, round(score, 2))
        self.assertEqual(wrong, {})

    def test_quickbooks_answer_names_what_the_app_has(self):
        # R5-09: the answer used to say QuickBooks was "not yet" and payments were matched by hand.
        reply = helper.answer("does it work with quickbooks", seed_state(), "co-x", "u-1")
        self.assertEqual(reply["matched"], "quickbooks")
        for words in ("Read payments", "CSV", "Connect QuickBooks", "Download for QuickBooks"):
            self.assertIn(words, reply["text"])
        self.assertNotIn("Not yet", reply["text"])
        self.assertNotIn("Match payment", reply["text"])

    def test_unknown_question_is_logged_for_review(self):
        reply = helper.answer("what's the weather in boise", seed_state(), "co-x", "u-1")
        self.assertIsNone(reply["matched"])
        self.assertIn("saved the question", reply["text"])
        self.assertEqual(helper.review()["open"][0]["examples"], ["what's the weather in boise"])

    def test_live_answer_uses_the_companys_jobs(self):
        reply = helper.answer("what's blocking oak street", seed_state(), "co-x", "u-1")
        self.assertIn("Oak Street", reply["text"])
        self.assertIn("Atlanta Millwork", reply["text"])
        self.assertTrue(any(a["type"] == "job" for a in reply["actions"]))

    def test_job_question_without_a_job_asks_which(self):
        reply = helper.answer("draft a follow-up", seed_state(), "co-x", "u-1")
        self.assertEqual(reply["text"], "Which job do you mean?")
        self.assertTrue(all(a["type"] == "ask" for a in reply["actions"]))

    def test_founder_answer_is_used_and_resolves_open_questions(self):
        helper.answer("do you have a referral program", seed_state(), "co-x", "u-1")
        self.assertEqual(len(helper.review()["open"]), 1)
        helper.save_answer({"questions": ["Do you have a referral program?"], "answer": "Yes: one free month per referral."}, "founder")
        self.assertEqual(helper.review()["open"], [])
        reply = helper.answer("is there a referral program", seed_state(), "co-x", "u-1")
        self.assertEqual(reply["text"], "Yes: one free month per referral.")

    def test_notes_and_matching_edge_cases(self):
        import importer
        chunks = importer.split_notes("10/1 Footings poured\n3/4 inch pipe ran to the panel\n1/2 bath rough-in\nMay 2 guys out sick\n10/2: framing", 2026)
        self.assertEqual([c["date"] for c in chunks], ["2026-10-01", "2026-10-02"])
        self.assertIn("3/4 inch pipe", chunks[0]["text"])
        self.assertIsNone(importer.leading_date("10/12/202 typo", 2026))
        self.assertIsNone(importer.leading_date("mon " * 1200, 2026))
        state = seed_state()
        m = importer.match_job(state, "Invoice dated DEC 12 for the Oak Street kitchen job")
        self.assertEqual(m["job_id"], "job-oak")

    def test_feedback_is_scoped_to_the_company(self):
        reply = helper.answer("who owes me", seed_state(), "co-x", "u-1")
        with self.assertRaises(domain.GateError):
            helper.feedback(reply["log_id"], False, "", "co-other")
        helper.feedback(reply["log_id"], False, "wrong list", "co-x")
        self.assertEqual(helper.review()["stats"]["unhelpful"], 1)


    def test_today_answer_is_todays_queue(self):
        # One rule set: the helper lists Today's rows (domain.today_rows), most urgent first.
        state = seed_state()
        rows = domain.today_rows(state)
        self.assertTrue(rows)
        reply = helper.answer("what should i do today", state, "co-x", "u-1")
        listed = [line for line in reply["text"].split("\n") if line.startswith("- ")]
        self.assertEqual(listed, [f"- {r['job']}: {r['label']}. {r['text']}" for r in rows[:8]])
        paths = {r["path"] for r in rows}
        for a in reply["actions"]:
            if a["type"] == "path":
                self.assertIn(a["path"], paths)
        self.assertNotIn("packet", " ".join(a["label"] for a in reply["actions"]).lower())

    def test_followups_answer_matches_today(self):
        state = seed_state()
        due = [r for r in domain.today_rows(state) if r["kind"] == "follow_up"]
        reply = helper.answer("which follow-ups are due", state, "co-x", "u-1")
        for r in due:
            self.assertIn(f"- {r['job']}: {r['text']}", reply["text"])
        if not due:
            self.assertIn("No follow-ups are due", reply["text"])

    def test_blocked_means_what_today_means(self):
        # Grant Park has paperwork missing but no draft yet: what the next pay app will need, not a block.
        state = seed_state()
        reply = helper.answer("which jobs are blocked", state, "co-x", "u-1")
        self.assertNotIn("Grant Park addition", reply["text"])
        self.assertIn("Oak Street kitchen", reply["text"])
        reply = helper.answer("what's blocking grant park", state, "co-x", "u-1")
        self.assertIn("isn't blocked yet", reply["text"])

    def test_follow_up_answer_covers_an_approved_pay_app(self):
        # A pay app the customer approved is still out for payment; the server follows up on it.
        state = seed_state()
        job = next(j for j in state["jobs"] if j["id"] == "job-turner")
        job["invoices"][0]["status"] = "approved"
        reply = helper.answer("draft a follow-up for turner", state, "co-x", "u-1")
        self.assertNotIn("has no sent, unpaid pay app", reply["text"])
        self.assertIn(job["invoices"][0]["number"], reply["text"])
        self.assertTrue(any(a.get("label") == "Send the follow-up" for a in reply["actions"]))

    def test_billed_this_month_leaves_out_a_pulled_back_pay_app(self):
        state = seed_state()
        month = domain.datetime.now(domain.timezone.utc).strftime("%Y-%m")
        job = next(j for j in state["jobs"] if j["id"] == "job-turner")
        inv = job["invoices"][0]
        inv["sent_at"] = month + "-01T12:00:00+00:00"
        counted = helper.answer("what are my totals", state, "co-x", "u-1")["text"]
        inv["status"] = "void"
        pulled = helper.answer("what are my totals", state, "co-x", "u-1")["text"]
        cents = lambda text: round(float(text.split("Billed this month: $")[1].split()[0].replace(",", "")) * 100)
        self.assertEqual(cents(counted) - cents(pulled), inv["amount_cents"])

if __name__ == "__main__":
    unittest.main()
