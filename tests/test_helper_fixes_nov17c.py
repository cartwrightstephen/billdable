"""Third pass on the helper (Nov 17), after the second verification: example questions that answered
worse than at dev 198422a answer at least as well again, payment commands that mention retainage
not paid yet open Record a Payment (B38), and a late follow-up is the follow-ups answer (B18).

The table of every example question is tests/fixtures/helper_expected_answers.json, checked in
tests/test_helper_fixes_nov17b.py; these tests check the text and buttons behind a few of its rows."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_seed
import helper
from seed import seed_state

OWNER = {"id": "u-own", "role": "owner"}
MEMBER = {"id": "u-mem", "role": "member"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (helper.LOCAL_KB, helper.LOG_PATH)
        helper.LOCAL_KB = Path(self.tmp.name) / "helper_answers.json"
        helper.LOG_PATH = Path(self.tmp.name) / "helper_log.json"
        self.state = seed_state()

    def tearDown(self):
        helper.LOCAL_KB, helper.LOG_PATH = self._orig
        self.tmp.cleanup()

    def ask(self, q, user=OWNER, state=None, job_id=""):
        return helper.answer(q, state or self.state, "co-x", user["id"], "", job_id, user=user)

    @staticmethod
    def job(st, start):
        return next(j for j in st["jobs"] if j["name"].startswith(start))

    def contexts(self):
        turner = self.job(self.state, "Turner")["id"]
        return [(u, page) for u in (OWNER, MEMBER) for page in ("", turner)]


class ExamplesAnswerAsWellAsBefore(Base):
    def test_waiver_due_is_the_live_list(self):
        for user, page in self.contexts():
            r = self.ask("Waiver due", user=user, job_id=page)
            self.assertEqual(r["matched"], "waivers-due")
            self.assertEqual(r["text"], "Waivers due:\n- Ponce bath")
            self.assertIn("Open Ponce bath", [a["label"] for a in r["actions"]])

    def test_what_is_blocked_jobs_is_the_blocked_list(self):
        r = self.ask("What is blocked jobs?")
        self.assertEqual(r["matched"], "blockers")
        self.assertTrue(r["text"].startswith("Blocked jobs:\n- Oak Street kitchen"), r["text"])
        r = self.ask("What is blocked jobs?", job_id=self.job(self.state, "Turner")["id"])
        self.assertTrue(r["text"].startswith("Turner office TI has nothing blocking it."), r["text"])

    def test_send_questions_lead_with_whats_blocked_and_how_to_with_the_how_to(self):
        how = next(e for e in helper.load_kb() if e["id"] == "send")["answer"]
        turner = self.job(self.state, "Turner")["id"]
        for q in ("Why can't I send?", "Approve and send", "Send the bill", "What does Fix things before sending mean?"):
            r = self.ask(q)
            self.assertEqual(r["matched"], "send", q)
            self.assertTrue(r["text"].startswith("Blocked jobs:\n- Oak Street kitchen"), (q, r["text"][:80]))
            self.assertIn(how, r["text"])
            r = self.ask(q, job_id=turner)
            self.assertTrue(r["text"].startswith("Turner office TI has nothing blocking it."), (q, r["text"][:80]))
        r = self.ask("How do I send a pay app?")
        self.assertTrue(r["text"].startswith(how), r["text"][:80])
        self.assertIn("Blocked jobs:", r["text"])

    def test_overdue_with_no_due_date_set_lists_those_pay_apps(self):
        # The seed's pay apps have no due date: not "nothing is overdue" with a 60-day-old pay app.
        for q in ("What's overdue?", "Is anything overdue?", "Which pay apps are past due?"):
            r = self.ask(q)
            self.assertEqual(r["matched"], "unpaid-payapps", q)
            self.assertTrue(r["text"].startswith("Nothing is past its due date, but these unpaid pay apps have no due date set"), r["text"])
            self.assertIn("- PA-TRN-01, Turner office TI: $119,700.00, ", r["text"])
            self.assertIn("- PA-BRK-01, Brookhaven kitchen: $57,950.00, ", r["text"])
            self.assertIn("Open Turner office TI", [a["label"] for a in r["actions"]])
        # A dated pay app past its date is still the only one listed (B18).
        demo = demo_seed.build()[0]
        r = self.ask("What's overdue?", state=demo)
        self.assertTrue(r["text"].startswith("Overdue pay apps"), r["text"])
        self.assertIn("PA-SUMM-01", r["text"])
        self.assertNotIn("PA-WEST-03", r["text"])

    def test_follow_up_draft_is_a_letter_again(self):
        r = self.ask("Draft a follow-up for a job", job_id=self.job(self.state, "Turner")["id"])
        self.assertEqual(r["text"], "Here is a follow-up for Turner office TI:\n\nHi Dev Patel,\n\nChecking on PA-TRN-01 for Turner office TI "
                                    "($119,700.00), sent 2026-09-18. Is anything missing on your side?\n\nThanks,\nHartline Remodeling")


class B38PaymentsWithRetainageNotPaidYet(Base):
    def test_a_payment_with_the_retainage_still_to_come_opens_record_a_payment(self):
        for q in ("record a payment for Turner, no retainage paid yet",
                  "record a payment for Turner, retainage paid separately later",
                  "record a payment for Turner, the retainage check comes later",
                  "record a payment for Turner, retainage payment comes at closeout",
                  "record a payment for Turner; retainage release later",
                  "record a payment for Turner but not for the retainage",
                  "record retainage held on Turner's payment"):
            r = self.ask(q)
            self.assertEqual(r["matched"], "start-task", q)
            self.assertEqual(r["text"], "Here is Record a Payment for Turner office TI. Nothing is saved until you save it.", q)
            self.assertEqual(r["actions"][0]["form"], "match", q)

    def test_retainage_that_came_in_is_still_record_retainage_paid(self):
        for q in ("record a payment for the retainage on Turner", "record retainage paid for Turner", "the retainage was paid on Turner"):
            r = self.ask(q)
            self.assertEqual(r["text"], "Retainage received is recorded with Record retainage paid on Turner office TI.", q)


class B18LateFollowUps(Base):
    def test_a_late_follow_up_or_send_is_the_follow_ups_answer(self):
        for q in ("is a follow up overdue", "am I late on a follow up", "is my pay app late to send?", "I'm late sending a pay app"):
            for user, page in self.contexts():
                r = self.ask(q, user=user, job_id=page)
                self.assertEqual(r["matched"], "followups-due", (q, user["role"], page))
                self.assertTrue(r["text"].startswith("These follow-ups are due now:"), r["text"])

    def test_late_payments_are_still_the_money_answers(self):
        self.assertEqual(self.ask("Who is late paying?")["matched"], "who-owes")
        self.assertEqual(self.ask("Which pay apps are late?")["matched"], "unpaid-payapps")
        self.assertEqual(self.ask("Any overdue pay apps?")["matched"], "unpaid-payapps")


if __name__ == "__main__":
    unittest.main()
