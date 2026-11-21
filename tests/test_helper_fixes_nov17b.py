"""Second round of fixes to the helper (Nov 17), after verification of the first: items that still
failed by another route, and regressions the first fixes brought in. Each test names its item.

The main guard is HelperAnswersDontRegress: every example question in helper/answers.json and the
paraphrases from the verification, with the answer each gets on the seed and the Redbud demo, as
the owner and the Member, with no page open and on a job's page
(tests/fixtures/helper_expected_answers.json). A row that differs from dev 198422a says why."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_seed
import domain
import helper
import helper_intents
from seed import seed_state

OWNER = {"id": "u-own", "role": "owner"}
MEMBER = {"id": "u-mem", "role": "member"}   # the built-in Member: Record payments only
ALL = set(domain.profiles.PERM_KEYS)
EXPECTED = Path(__file__).resolve().parent / "fixtures" / "helper_expected_answers.json"


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
    def demo():
        return demo_seed.build()[0]

    @staticmethod
    def job(st, start):
        return next(j for j in st["jobs"] if j["name"].startswith(start))


class HelperAnswersDontRegress(Base):
    # The four contexts of each row (tests/fixtures/helper_expected_answers.json): page|user.
    CONTEXTS = (("-", "owner"), ("-", "member"), ("page", "owner"), ("page", "member"))

    def test_every_example_and_paraphrase_gets_its_expected_answer(self):
        """Every row, on the seed and the demo, as the owner and the Member, with no page open and on
        a job's page: the answer id, and for a how-to with live lines which comes first. A later
        change that moves one answers worse silently no more (Round 2 review, third pass)."""
        rows = json.loads(EXPECTED.read_text())["questions"]
        kb = {e["id"]: e for e in helper.load_kb()}
        wrong = []
        # The log is no part of what is answered, and the state is not changed by answering: each
        # state's snapshot and each question's ranking are worked out once, which keeps this quick.
        snaps, ranks = {}, {}
        real = (helper._log, domain.snapshot, helper.rank)
        helper._log = lambda entry: None
        def snapshot(st):
            if id(st) not in snaps:
                snaps[id(st)] = real[1](st)
            return snaps[id(st)]

        def rank(q, kb=None):
            if kb is not None:
                return real[2](q, kb)
            if q not in ranks:
                ranks[q] = real[2](q)
            return ranks[q]
        domain.snapshot, helper.rank = snapshot, rank
        try:
            for name, st, page in (("seed", self.state, "Turner"), ("demo", self.demo(), "Westside")):
                page_id = self.job(st, page)["id"]
                for row in rows:
                    for where, who in self.CONTEXTS:
                        ctx = f"{where}|{who}"
                        r = self.ask(row["q"], user=OWNER if who == "owner" else MEMBER, state=st,
                                     job_id=page_id if where == "page" else "")
                        want = row[name] if not isinstance(row[name], dict) else row[name][ctx]
                        if r["matched"] != want:
                            wrong.append(f"{name} {ctx}: {row['q']!r} gave {r['matched']}, expected {want}")
                            continue
                        lead = (row.get("lead") or {}).get(name)
                        lead = lead[ctx] if isinstance(lead, dict) else lead
                        prepared = (kb.get(want) or {}).get("answer", "").strip()
                        if lead and prepared:
                            got = "prepared" if r["text"].startswith(prepared[:60]) else "live"
                            if got != lead:
                                wrong.append(f"{name} {ctx}: {row['q']!r} leads with the {got} lines, expected {lead}")
        finally:
            helper._log, domain.snapshot, helper.rank = real
        self.assertEqual(wrong, [])

    def test_every_example_question_is_in_the_table(self):
        listed = {row["q"] for row in json.loads(EXPECTED.read_text())["questions"]}
        missing = [q for e in json.loads(helper.SHIPPED_KB.read_text())["answers"]
                   for q in e.get("questions", []) if q not in listed]
        self.assertEqual(missing, [])

    def test_every_change_since_198422a_says_why(self):
        for row in json.loads(EXPECTED.read_text())["questions"]:
            if "at_198422a" in row:
                self.assertRegex(row.get("why", ""), r"^[BR]\d+: ", row["q"])

    def test_every_example_question_gets_its_own_answer(self):
        kb = helper.load_kb()
        missed = [(q, e["id"]) for e in kb for q in e["questions"] if helper.rank(q, kb)[0][1]["id"] != e["id"]]
        self.assertEqual(missed, [])


class FormCommands(Base):
    def test_r11_a_dash_or_a_name_after_the_form_words_still_opens_the_form(self):
        st = self.demo()
        summit, westside = self.job(st, "Summit"), self.job(st, "Westside")
        for q, form, job in (("new change order - notes panel for Summit", "newco", summit),
                             ("new change order: notes panel for Summit", "newco", summit),
                             ("new change order email room at Westside", "newco", westside),
                             ("new job Notes Inc", "newjob", None),
                             ("new job Photo Lab Remodel for Halcyon", "newjob", None),
                             ("new job record photos for Summit", "daily", summit)):
            r = self.ask(q, state=st)
            self.assertEqual((r["matched"], r["actions"][0]["form"]), ("start-task", form), q)
            if job:
                self.assertEqual(r["actions"][0]["job_id"], job["id"], q)
        self.assertEqual(self.ask("new job Photo Lab Remodel for Halcyon", state=st)["actions"][0]["client"],
                         next(p["name"] for p in st["payers"] if p["name"].startswith("Halcyon")))

    def test_r11_a_reminder_email_or_note_made_is_still_no_form(self):
        snap = domain.snapshot(self.state)
        for q in ("add a payment reminder", "add a change order note", "new change order emails", "new change order email to Turner",
                  "add a payment reminder, Turner", "new final pay app"):
            self.assertIsNone(helper_intents.intent(q, snap, ALL), q)

    def test_b38_a_payment_less_retainage_is_the_pay_apps_payment(self):
        for st, name in ((self.state, "Turner"), (self.demo(), "Summit")):
            job = self.job(st, name)
            for q in (f"record a payment for {name} less retainage", f"record a payment on {name} net of retainage",
                      f"record a payment for {name} minus 10% retainage", f"record a payment for {name}, retention withheld",
                      f"record a payment for {name} after retainage"):
                r = self.ask(q, state=st)
                self.assertEqual(r["matched"], "start-task", q)
                self.assertEqual((r["actions"][0]["type"], r["actions"][0].get("form"), r["actions"][0].get("job_id")), ("form", "match", job["id"]), q)

    def test_b38_retainage_that_came_in_opens_the_job_where_the_button_is(self):
        for q, name in (("record retainage paid for Turner", "Turner"), ("retainage came in for Decatur", "Decatur"),
                        ("record a payment for the retainage on Turner", "Turner")):
            r = self.ask(q)
            self.assertEqual(r["matched"], "start-task", q)
            self.assertIn("Record retainage paid", r["text"], q)
            self.assertEqual(r["actions"], [helper_intents.job_action(self.job(self.state, name))], q)

    def test_b38_only_where_record_retainage_paid_is_offered(self):
        # Summit Ridge: retainage held, work in progress, nothing asked, no paid pay app holding any,
        # so the job page shows no Record retainage paid (static/js/jobs.js retainageReleasable).
        st = self.demo()
        snap = domain.snapshot(st)
        summit = self.job(snap, "Summit")
        self.assertGreater(summit["money"]["retainage_open_cents"], 0)
        self.assertFalse(helper_intents.retainage_releasable(summit))
        r = self.ask("record a payment for the retainage on Summit", state=st)
        self.assertNotIn("is recorded with Record retainage paid", r["text"])
        self.assertIn("once its work is marked done", r["text"])
        # With no job named, only jobs whose page offers it.
        r = self.ask("record retainage paid", state=st)
        offered = {a["job_id"] for a in r["actions"]}
        self.assertTrue(offered)
        self.assertNotIn(summit["id"], offered)
        self.assertTrue(all(helper_intents.retainage_releasable(j) for j in snap["jobs"] if j["id"] in offered))


class Navigation(Base):
    def test_b19_no_page_for_words_near_do_list(self):
        for phrase in ("docs list", "doc list", "done list", "go list", "to go list"):
            self.assertIsNone(helper_intents.page_for(phrase), phrase)
        for phrase in ("to do list", "the to do list", "to do", "todo"):
            self.assertEqual(helper_intents.page_for(phrase), "dashboard", phrase)
        for q in ("open the docs list", "go to done list"):
            self.assertNotEqual(self.ask(q)["text"], "Opening Dashboard.", q)

    def test_b37_money_opens_pay_apps_for_everyone(self):
        st = self.demo()
        for user in (OWNER, MEMBER):
            for q in ("open money", "go to money", "show money", "show me the money tab", "show the money section",
                      "show me the money page", "money tab", "the money tab", "take me to the money tab", "open money page thanks"):
                r = self.ask(q, user, st)
                self.assertEqual((r["matched"], r["actions"]), ("open-page", [helper_intents.nav_action("payapps")]), (user["id"], q))
        self.assertEqual(self.ask("show me the money", OWNER, st)["matched"], "money")
        self.assertEqual(self.ask("money", OWNER, st)["matched"], "money")


class Answers(Base):
    def test_b7_a_needs_job_answer_for_a_client_is_about_the_clients_jobs(self):
        brk, turner, ponce_bath = (self.job(self.state, n)["id"] for n in ("Brookhaven", "Turner", "Ponce bath"))
        for page in ("", brk, turner):
            r = self.ask("draft a follow-up for Ponce Property", job_id=page)
            self.assertTrue(r["text"].startswith("Here is a follow-up for Brookhaven kitchen"), (page, r["text"]))
        r = self.ask("what is the status of Ponce Property", job_id=turner)
        self.assertEqual(r["text"], "Which job do you mean?")
        self.assertEqual({a["job_id"] for a in r["actions"]}, {brk, ponce_bath})
        self.assertTrue(self.ask("what is the status of Ponce Property", job_id=brk)["text"].startswith("Brookhaven kitchen"))

    def test_b7_unsigned_change_orders_for_a_client_are_theirs(self):
        r = self.ask("what change orders are unsigned for Ponce Property")
        self.assertEqual(r["matched"], "pending-cos")
        self.assertNotIn("Oak Street", r["text"])
        self.assertNotIn("Grant Park", r["text"])
        r = self.ask("what change orders are unsigned for Oak Street")
        self.assertIn("Oak Street", r["text"])
        self.assertNotIn("Grant Park", r["text"])

    def test_b10_not_billed_about_retainage_or_drafts_reads_as_before(self):
        for q, want in (("retainage that hasn't been billed", "what-retainage"), ("how do I bill retainage that is not billed", "what-retainage"),
                        ("is retainage billed separately or not billed", "what-retainage"),
                        ("which pay apps are drafted but not billed", "nothing-to-draft"),
                        ("what havent I billed yet", "unbilled"), ("What's not billed yet?", "unbilled")):
            self.assertEqual(self.ask(q)["matched"], want, q)

    def test_b18_overdue_however_asked_lists_only_pay_apps_past_due(self):
        st = self.demo()
        for q in ("Which pay apps are past-due?", "Show overdue pay apps", "Which invoices are overdue?", "Which pay apps are late?",
                  "Any late payments?", "overdue pay apps for Summit", "Is anything overdue?"):
            r = self.ask(q, state=st)
            self.assertEqual(r["matched"], "unpaid-payapps", q)
            self.assertIn("PA-SUMM-01", r["text"], q)
            self.assertNotIn("PA-WEST-03", r["text"], q)
        r = self.ask("Is Westside overdue?", state=st)
        self.assertIn("Nothing is overdue for Westside Lofts", r["text"])
        self.assertNotIn("PA-SUMM-01", r["text"])
        r = self.ask("What's not overdue yet?", state=st)
        self.assertIn("PA-WEST-03", r["text"])
        self.assertNotIn("PA-SUMM-01", r["text"])

    def test_b21_no_answer_says_a_waiver_waits_for_payment_in_full(self):
        kb = {e["id"]: e for e in helper.load_kb()}
        for entry in ("partial", "match-payment", "send-waiver"):
            self.assertIn("part", kb[entry]["answer"].split("Choose waiver")[0] if entry == "send-waiver" else kb[entry]["answer"], entry)
        self.assertNotIn("no waiver is due until", kb["partial"]["answer"])
        self.assertNotIn("paid in full opens the waiver step", kb["match-payment"]["answer"])
        self.assertFalse(kb["send-waiver"]["answer"].startswith("When a pay app is paid in full"))
        self.assertEqual(self.ask("Is a waiver due after a partial payment?")["matched"], "partial")

    def test_b26_what_did_i_send_is_the_outbox_answer(self):
        r = self.ask("What did I send?")
        self.assertEqual(r["matched"], "email-sent")
        self.assertNotIn("Review & send", r["text"])

    def test_b26_joined_replies_say_nothing_twice(self):
        demo = self.demo()
        for st in (self.state, demo):
            for page in ("", self.job(st, "Turner" if st is self.state else "Westside")["id"]):
                for q in ("How do I close a job?", "How do I finish a job?", "Why can't I close this job?", "How do I send a pay app?"):
                    text = self.ask(q, state=st, job_id=page)["text"]
                    said = [re.sub(r"\W+", " ", s).strip().lower() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
                    self.assertEqual(len(said), len(set(said)), (q, page, text))
                    if "send" not in q:
                        self.assertEqual(text.count("Finish and reopen jobs permission"), 1, (q, page))

    def test_b26_on_a_job_page_finishing_is_about_that_job(self):
        turner = self.job(self.state, "Turner")
        r = self.ask("How do I finish a job?", job_id=turner["id"])
        self.assertIn("Before Turner office TI can be finished:", r["text"])
        self.assertNotIn("No job is ready to finish yet", r["text"])

    def test_b26_live_answers_start_with_their_figures_as_before(self):
        self.assertTrue(self.ask("How old are my unpaid pay apps?")["text"].startswith("Unpaid pay apps, oldest first"))
        self.assertTrue(self.ask("What's my backlog?")["text"].startswith("Backlog (signed work not done yet)"))
        self.assertTrue(self.ask("What's due this week?")["text"].startswith("Due this week"))

    def test_b28_how_does_it_work_is_getting_started(self):
        for q in ("How does it work?", "how does this work"):
            self.assertEqual(self.ask(q)["matched"], "getting-started", q)
        # A quoted keyword still matches only as written (the first fix).
        self.assertEqual(self.ask("How is this job doing?", job_id="job-turner")["matched"], "job-status")
        self.assertEqual(self.ask("How does it know which job?")["matched"], "import-match")


if __name__ == "__main__":
    unittest.main()
