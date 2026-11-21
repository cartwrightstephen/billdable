"""Fixes from the review of Duracash Round 2 (Nov 17), the helper: its To Do and money answers
follow the person's permissions, and its commands open a form only when the form is what the
command makes, read job codes, ask "Which job?" by id, and use the job whose page is open."""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import financials
import helper
import helper_intents
from seed import seed_state

OWNER = {"id": "u-own", "role": "owner"}
MEMBER = {"id": "u-mem", "role": "member"}   # the built-in Member: Record payments only


class HelperPermissions(unittest.TestCase):
    """Items 7, 15, 16, 17c."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (helper.LOCAL_KB, helper.LOG_PATH)
        helper.LOCAL_KB = Path(self.tmp.name) / "helper_answers.json"
        helper.LOG_PATH = Path(self.tmp.name) / "helper_log.json"
        self.state = seed_state()
        self.state["company"]["profiles"] = [{"id": "owner", "name": "Owner", "built_in": True, "locked": True},
                                             {"id": "member", "name": "Member", "built_in": True, "perms": ["record_payments"]}]

    def tearDown(self):
        helper.LOCAL_KB, helper.LOG_PATH = self._orig
        self.tmp.cleanup()

    def ask(self, q, user=OWNER, job_id=""):
        return helper.answer(q, self.state, "co-x", user["id"], "", job_id, user=user)

    def add_unpriced_co(self):
        job = next(j for j in self.state["jobs"] if j["id"] == "job-oak")
        domain.add_change_order(self.state, job["id"], "Extra outlets", 0, reason="owner_request")
        return job

    def test_today_and_due_this_week_show_only_the_rows_the_dashboard_shows(self):
        self.add_unpriced_co()
        rows = domain.today_rows(self.state)
        priced = [r for r in rows if r.get("perm")]
        self.assertTrue(priced, "a change order to price is kept for those who may price it")
        mine = domain.rows_with_perms(rows, ["record_payments"])
        for q in ("what should I do today", "what's due this week"):
            text = self.ask(q, MEMBER)["text"]
            for r in priced:
                self.assertNotIn(f"{r['job']}: {r['label']}", text, q)
            self.assertIn(f"{mine[0]['job']}: {mine[0]['label']}", text, q)
        self.assertIn(f"{priced[0]['job']}: {priced[0]['label']}", self.ask("what should I do today")["text"])

    def test_no_money_in_today_or_change_orders_without_the_permission(self):
        self.state["jobs"][0]["change_orders"].append({"id": "co-x", "number": 9, "title": "Gate", "status": "pending", "amount_cents": 123_400})
        for q in ("what should I do today", "which change orders are waiting"):
            r = self.ask(q, MEMBER)
            self.assertNotIn("$", r["text"], q)
        self.assertIn("$1,234.00", self.ask("which change orders are waiting")["text"])

    def test_billed_this_month_is_financials_figure(self):
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        f = financials.compute(self.state)
        expected = next(m["billed_cents"] for m in f["monthly"] if m["month"] == month)
        inv = next(i for j in self.state["jobs"] for i in j["invoices"] if i["status"] in domain.BILLED_STATUSES)
        inv["sent_at"] = datetime.now(timezone.utc).isoformat()
        inv["retainage_cents"] = 10_000
        inv["gross_cents"] = inv["amount_cents"] + 10_000
        expected = next(m["billed_cents"] for m in financials.compute(self.state)["monthly"] if m["month"] == month)
        self.assertIn(f"- Billed this month: {domain.money(expected)}", self.ask("show me the money")["text"])

    def test_no_financials_button_for_someone_who_cant_open_it(self):
        for q in ("opne finacials", "zzz finances qqq"):
            r = self.ask(q, MEMBER)
            self.assertNotIn(helper_intents.nav_action("financials"), r["actions"], q)
        self.assertIn(helper_intents.nav_action("financials"), self.ask("opne finacials")["actions"])
        # A prepared answer's own button too.
        r = self.ask("which pay apps are unpaid", MEMBER)
        self.assertFalse([a for a in r["actions"] if a.get("view") == "financials"])
        self.assertTrue(all(helper_intents.may_open(a, {"record_payments"}) for a in self.ask("what's my backlog", MEMBER)["actions"]))


class HelperCommands(unittest.TestCase):
    """Items 11-14 and 17a-b."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (helper.LOCAL_KB, helper.LOG_PATH)
        helper.LOCAL_KB = Path(self.tmp.name) / "helper_answers.json"
        helper.LOG_PATH = Path(self.tmp.name) / "helper_log.json"
        self.state = seed_state()
        westside = next(j for j in self.state["jobs"] if j["id"] == "job-grant")
        westside["name"] = "Westside Lofts"

    def tearDown(self):
        helper.LOCAL_KB, helper.LOG_PATH = self._orig
        self.tmp.cleanup()

    def ask(self, q, job_id=""):
        return helper.answer(q, self.state, "co-x", OWNER["id"], "", job_id, user=OWNER)

    def test_a_form_word_that_isnt_the_verbs_object_opens_no_form(self):
        for q in ("Draft a follow-up for the Turner pay app", "Write a reminder for the unpaid invoice on Turner",
                  "new final pay app for Turner", "log progress on Westside", "add a payment reminder for Westside",
                  "Draft an email about the change order for Westside"):
            r = self.ask(q)
            self.assertNotEqual(r["matched"], "start-task", q)
            self.assertIsNone(helper_intents.intent(q, domain.snapshot(self.state), set(domain.profiles.PERM_KEYS)), q)
            self.assertNotIn("go", r, q)
        self.assertEqual(self.ask("new final pay app for Turner")["matched"], "mark-done", "the prepared answer about the final pay app")

    def test_the_forms_still_open_when_named_as_the_object(self):
        for q, form in (("new change order for Westside", "newco"), ("record a payment on turner", "match"),
                        ("add a job record for Westside", "daily"), ("new pay app for Westside", "progress")):
            r = self.ask(q)
            self.assertEqual((r["matched"], r["actions"][0]["form"]), ("start-task", form), q)

    def test_a_job_code_in_a_command(self):
        r = self.ask("open TRN")
        self.assertEqual((r["matched"], r.get("go"), r["actions"][0]["job_id"]), ("open-page", 0, "job-turner"))
        r = self.ask("new change order for OAK")
        self.assertEqual((r["matched"], r["actions"][0]["job_id"]), ("start-task", "job-oak"))

    def test_which_job_buttons_carry_the_job(self):
        r = self.ask("record a payment")
        self.assertEqual(r["text"], "Which job?")
        self.assertEqual([(a["type"], a["form"], a["job_id"]) for a in r["actions"]], [("form", "match", "job-turner"), ("form", "match", "job-brook")])

    def test_a_name_of_common_words_only(self):
        oak = next(j for j in self.state["jobs"] if j["id"] == "job-oak")
        oak["name"] = "Kitchen remodel"
        r = self.ask("new change order for kitchen remodel")
        self.assertEqual((r["matched"], r["actions"][0]["job_id"]), ("start-task", "job-oak"))
        r = self.ask("status of kitchen remodel")
        self.assertEqual(r["matched"], "job-status")
        self.assertTrue(r["text"].startswith("Kitchen remodel"), r["text"])

    def test_two_jobs_with_one_name_ask_by_id(self):
        for jid in ("job-oak", "job-brook"):
            next(j for j in self.state["jobs"] if j["id"] == jid)["name"] = "Kitchen remodel"
        r = self.ask("status of kitchen remodel")
        self.assertEqual(r["text"], "Which job do you mean?")
        self.assertEqual({a.get("job_id") for a in r["actions"]}, {"job-oak", "job-brook"})
        again = self.ask(r["actions"][0]["q"], job_id=r["actions"][0]["job_id"])
        self.assertEqual(again["matched"], "job-status")
        r = self.ask("new change order for kitchen remodel")
        self.assertEqual({(a["type"], a["job_id"]) for a in r["actions"]}, {("form", "job-oak"), ("form", "job-brook")})

    def test_a_command_on_a_jobs_page_means_that_job(self):
        r = self.ask("new change order", job_id="job-oak")
        self.assertEqual((r["matched"], r["actions"][0]["form"], r["actions"][0]["job_id"], r.get("go")), ("start-task", "newco", "job-oak", 0))
        r = self.ask("record a payment", job_id="job-turner")
        self.assertEqual((r["actions"][0]["form"], r["actions"][0]["job_id"]), ("match", "job-turner"))
        r = self.ask("new change order for Westside", job_id="job-oak")
        self.assertEqual(r["actions"][0]["job_id"], "job-grant", "a job named wins")

    def test_is_anything_overdue(self):
        self.assertIn(self.ask("Is anything overdue?")["matched"], {"unpaid-payapps", "followups-due"})

    def test_a_new_jobs_own_name_isnt_its_client(self):
        self.state["payers"].append({"id": "payer-granite", "name": "Granite Row"})
        r = self.ask("new job called Granite Tower")
        self.assertEqual(r["actions"][0], {"type": "form", "form": "newjob", "label": "New job", "name": "Granite Tower"})
        r = self.ask("new job called Granite Tower for Granite Row")
        self.assertEqual((r["actions"][0]["client"], r["actions"][0]["name"]), ("Granite Row", "Granite Tower"))


if __name__ == "__main__":
    unittest.main()
