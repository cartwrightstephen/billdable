"""Fixes to the helper from the second look at Round 2 (Nov 17): commands that name a job or client
with a word like "final" or "notes" still open the form, a client named beats a job sharing a word
and the open page's job, prepared how-to text is never replaced by live lines, and the live answers
say what the Dashboard and Unpaid say. Each test names the bug it guards (B<n>, R<n>)."""

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_seed
import domain
import helper
import helper_intents
import server
from domain import tasks as domain_tasks
from seed import seed_state

OWNER = {"id": "u-own", "role": "owner"}
MEMBER = {"id": "u-mem", "role": "member"}   # the built-in Member: Record payments only
ALL = set(domain.profiles.PERM_KEYS)


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

    def demo(self):
        st, _ = demo_seed.build()
        return st

    @staticmethod
    def job(st, start):
        return next(j for j in st["jobs"] if j["name"].startswith(start))


class FormCommands(Base):
    def test_r11_a_name_with_final_notes_email_photo_still_opens_the_form(self):
        st = self.demo()
        summit, westside = self.job(st, "Summit"), self.job(st, "Westside")
        for q, form, job in (("new job called Final Touch", "newjob", None), ("new job for Notes Inc", "newjob", None),
                             ("new job for Final Phase Builders", "newjob", None),
                             ("new change order for the final punch at Summit", "newco", summit),
                             ("new change order to add photo cells at Summit", "newco", summit),
                             ("new change order for the Westside email room", "newco", westside),
                             ("new change order for SUMM: replace notes panel", "newco", summit),
                             ("record a payment for the Summit final billing", "match", summit),
                             ("add another change order for Summit", "newco", summit)):
            r = self.ask(q, state=st)
            self.assertEqual((r["matched"], r["actions"][0]["form"]), ("start-task", form), q)
            if job:
                self.assertEqual(r["actions"][0]["job_id"], job["id"], q)
        self.assertEqual(self.ask("new job called Final Touch", state=st)["actions"][0]["name"], "Final Touch")

    def test_r11_still_no_form_when_the_thing_made_is_a_reminder_or_a_note(self):
        snap = domain.snapshot(self.state)
        for q in ("add a payment reminder for Turner", "new change order email for Turner", "add a job record note for Turner",
                  "Draft a follow-up for the Turner pay app", "new final pay app for Turner"):
            self.assertIsNone(helper_intents.intent(q, snap, ALL), q)

    def test_r14_a_client_named_beats_the_open_pages_job(self):
        st = self.demo()
        westside = self.job(st, "Westside")
        halcyon = {j["id"] for j in st["jobs"] if j["payer_id"] == next(p["id"] for p in st["payers"] if p["name"].startswith("Halcyon"))}
        r = self.ask("record a payment from Halcyon", state=st, job_id=westside["id"])
        ids = {a.get("job_id") for a in r["actions"] if a.get("job_id")}
        self.assertNotIn(westside["id"], ids, r)
        self.assertTrue(ids and ids <= halcyon, r)
        # The page's job still counts when nothing else is named.
        self.assertEqual(self.ask("record a payment", state=st, job_id=westside["id"])["actions"][0]["job_id"], westside["id"])

    def test_b7_a_task_for_a_client_lists_the_clients_jobs(self):
        r = self.ask("record a payment for Ponce Property Co")
        self.assertEqual(r["matched"], "start-task")
        self.assertIn(("form", "match", "job-brook"), [(a["type"], a.get("form"), a.get("job_id")) for a in r["actions"]], r)
        self.assertNotIn("Ponce bath", r["text"])

    def test_b20_new_job_name_without_trailing_punctuation_or_please(self):
        snap = domain.snapshot(self.state)
        for q in ("new job called Tower B.", "new job called Tower B?", "new job called Tower B please", 'New job named "Tower B".'):
            kind, reply = helper_intents.intent(q, snap, ALL)
            self.assertEqual(reply["actions"][0]["name"], "Tower B", q)

    def test_b30_no_open_jobs_says_so_and_offers_new_job(self):
        st = server.empty_state("Acme")
        for q in ("new progress pay app", "start a progress pay app", "record payment"):
            r = self.ask(q, state=st)
            self.assertEqual(r["text"], "There are no open jobs.", q)
            self.assertEqual([a.get("form") for a in r["actions"]], ["newjob"], q)

    def test_b38_retainage_received_is_not_the_pay_apps_record_a_payment(self):
        for q, jid in (("Record a payment for the retainage on Turner", "job-turner"), ("record a payment for the retainage on Decatur", "job-decatur")):
            r = self.ask(q)
            self.assertFalse([a for a in r["actions"] if a.get("form") == "match"], q)
            self.assertIn("Record retainage paid", r["text"], q)
            self.assertIn(helper_intents.job_action(next(j for j in self.state["jobs"] if j["id"] == jid)), r["actions"], q)


class Pages(Base):
    def test_b9_team_and_billing_open_for_everyone(self):
        for q, view in (("open team", "team"), ("take me to billing", "billing")):
            r = self.ask(q, MEMBER)
            self.assertEqual(r["actions"], [helper_intents.nav_action(view)], q)
        self.assertTrue(helper_intents.may_open(helper_intents.nav_action("team"), {"record_payments"}))
        self.assertFalse(helper_intents.may_open(helper_intents.nav_action("financials"), {"record_payments"}))

    def test_b19_to_do_list_opens_the_dashboard(self):
        for phrase in ("to do", "to do list", "the to do list", "to-do list"):
            self.assertEqual(helper_intents.page_for(phrase), "dashboard", phrase)
        for q in ("go to the to do list", "open to do", "take me to to do"):
            r = self.ask(q)
            self.assertEqual((r["matched"], r["actions"]), ("open-page", [helper_intents.nav_action("dashboard")]), q)

    def test_b37_money_opens_the_money_section(self):
        st = self.demo()
        for user in (OWNER, MEMBER):
            for q in ("open money", "go to money", "take me to the money tab", "open the money page"):
                r = self.ask(q, user, state=st)
                self.assertEqual((r["matched"], r["actions"]), ("open-page", [helper_intents.nav_action("payapps")]), q)
        self.assertEqual(self.ask("show me the money", state=st)["matched"], "money")


if __name__ == "__main__":
    unittest.main()
