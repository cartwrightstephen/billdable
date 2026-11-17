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
        # A job record holds notes and photos, so "add a job record note" is that form (round 2, R11).
        for q in ("add a payment reminder for Turner", "new change order email for Turner", "add a change order note for Turner",
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


class LiveAnswers(Base):
    def test_b7_a_client_named_is_not_its_jobs_namesake(self):
        r = self.ask("What does Ponce Property owe")
        self.assertEqual(r["matched"], "who-owes")
        self.assertIn("Ponce Property Co. owes you $57,950.00", r["text"])
        r = self.ask("What does Ponce Property owe", job_id="job-turner")
        self.assertIn("Ponce Property Co. owes you $57,950.00", r["text"], "a client named beats the page's job")
        r = self.ask("What is blocking Ponce Property")
        self.assertNotIn("Ponce bath has nothing blocking", r["text"])
        # A job named still wins over its client.
        self.assertTrue(self.ask("how much does Turner owe")["text"].startswith("Turner office TI"))

    def test_b8_co_in_a_clients_name_is_not_a_change_order(self):
        for q in ("how much does Ponce Property Co owe me", "how much does Ponce Property Co. owe me"):
            r = self.ask(q)
            self.assertEqual(r["matched"], "who-owes", q)
            self.assertIn("$57,950.00", r["text"], q)
        self.assertEqual(self.ask("how do I add a co")["matched"], "change-orders")

    def test_b10_not_billed_is_the_unbilled_answer(self):
        st = self.demo()
        for q in ("What's not billed yet?", "what's not billed yet", "What work isn't billed", "which jobs have unbilled work",
                  "What hasn't been billed?"):
            self.assertEqual(self.ask(q, state=st)["matched"], "unbilled", q)
        self.assertEqual(self.ask("Who hasn't paid me yet?", state=st)["matched"], "who-owes")

    def test_b17_follow_up_note_quotes_whats_owed_on_every_unpaid_pay_app(self):
        st = self.demo()
        summit = self.job(st, "Summit")
        inv = next(i for i in summit["invoices"] if i["number"] == "PA-SUMM-01")
        domain.match_payment(st, summit["id"], 3_000_000, "chk 1", invoice_id=inv["id"])
        text = self.ask("Draft a follow-up for Summit Ridge", state=st)["text"]
        self.assertIn("PA-SUMM-01 $10,554.00", text)
        self.assertNotIn("$40,554.00", text)
        second = next(i for i in summit["invoices"] if i["number"] == "PA-SUMM-02")
        second.update(status="sent", sent_at=datetime.now(timezone.utc).isoformat())
        r = self.ask("Draft a follow-up for Summit Ridge", state=st)
        self.assertIn("PA-SUMM-02 $44,048.70", r["text"])
        self.assertIn("total $54,602.70", r["text"])
        self.assertIn("total $54,602.70", next(a for a in r["actions"] if a["type"] == "copy")["text"])

    def test_b18_overdue_lists_only_pay_apps_past_their_due_date(self):
        st = self.demo()
        today = datetime.now(timezone.utc).date().isoformat()
        late = [i["number"] for j in st["jobs"] for i in j["invoices"] if i["status"] in domain.OPEN_FOR_PAYMENT and str(i.get("due_on") or "9") < today]
        early = [i["number"] for j in st["jobs"] for i in j["invoices"] if i["status"] in domain.OPEN_FOR_PAYMENT and str(i.get("due_on") or "9") >= today]
        self.assertTrue(late and early, "the demo has one of each")
        for q in ("Is anything overdue?", "Which pay apps are past due?", "What's overdue?"):
            text = self.ask(q, state=st)["text"]
            for n in late:
                self.assertIn(n, text, q)
            for n in early:
                self.assertNotIn(n, text, q)
        # Every unpaid one when the question isn't about overdue.
        text = self.ask("Which pay apps are unpaid?", state=st)["text"]
        self.assertTrue(all(n in text for n in late + early))
        for j in st["jobs"]:
            for i in j["invoices"]:
                i["due_on"] = "2999-01-01"
        self.assertIn("Nothing is overdue", self.ask("Is anything overdue?", state=st)["text"])

    def test_b21_waivers_due_mentions_part_payments(self):
        for j in self.state["jobs"]:
            j["waiver_due"] = False
            for i in j["invoices"]:
                i["waiver_due"] = False
        text, _ = helper.handle("waivers_due", domain.snapshot(self.state), None, self.state)
        self.assertNotIn("paid in full.", text)
        self.assertIn("in part", text)

    def test_b26_how_to_text_comes_first_then_the_live_lines(self):
        st = self.demo()
        kb = {e["id"]: e for e in helper.load_kb()}
        westside = self.job(st, "Westside")
        for q, entry, job_id in (("How do I send a pay app?", "send", ""), ("How do I send a pay app?", "send", westside["id"]),
                                 ("How do I close a job?", "close-job", "")):
            r = self.ask(q, state=st, job_id=job_id)
            self.assertEqual(r["matched"], entry, q)
            self.assertTrue(r["text"].startswith(kb[entry]["answer"]), q)
            self.assertGreater(len(r["text"]), len(kb[entry]["answer"]), "the live lines follow")

    def test_b27_today_includes_the_dashboards_tasks(self):
        st = server.empty_state("E Co")
        today = datetime.now(timezone.utc).date().isoformat()
        domain_tasks.add_task(st, {"title": "Call the inspector", "due": today}, None)
        domain_tasks.add_task(st, {"title": "Order panels", "due": (date.fromisoformat(today) + timedelta(days=20)).isoformat()}, None)
        r = self.ask("What should I do today?", state=st)
        self.assertIn("Call the inspector", r["text"])
        self.assertNotIn("Nothing needs action", r["text"])
        self.assertLess(r["text"].index("Call the inspector"), r["text"].index("Order panels"))

    def test_b28_which_job_keyword_only_for_which_job(self):
        for q in ("How is this job doing?", "Where are we on this job?"):
            r = self.ask(q, job_id="job-turner")
            self.assertEqual(r["matched"], "job-status", q)
        self.assertEqual(self.ask("How does it know which job?")["matched"], "import-match")

    def test_b29_finishing_takes_the_permission_not_the_owner(self):
        for j in self.state["jobs"]:
            j["work_status"] = "in_progress"
        r = self.ask("How do I close a job?")
        self.assertNotIn("Only an owner", r["text"])
        text, _ = helper.handle("closeable", domain.snapshot(self.state), None, self.state)
        self.assertNotIn("Only an owner", text)
        self.assertIn("Finish and reopen jobs", text)

    def test_b31_no_open_jobs_doesnt_ask_which_job(self):
        st = server.empty_state("Acme")
        for q in ("Draft a follow-up", "Status of this job"):
            r = self.ask(q, state=st)
            self.assertEqual(r["text"], "There are no open jobs.", q)
            self.assertIn("newjob", [a.get("form") for a in r["actions"]], q)
        for j in self.state["jobs"]:
            j["closed"] = True
        r = self.ask("Status of this job")
        self.assertEqual(r["text"], "There are no open jobs.")
        self.assertIn(helper_intents.nav_action("history"), r["actions"])


if __name__ == "__main__":
    unittest.main()
