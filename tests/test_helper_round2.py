"""The helper's Round 2 actions (Stephen, 10 Nov, decision 12): open a page, start a task, answer from
live data, inside the rails: it never changes data, it keeps company money from anyone without "See
company financials", it reads only the signed-in company, and says so when it doesn't understand."""

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import financials
import helper
import helper_intents
from seed import seed_state
from test_accounts import ServerCase

OWNER = {"id": "u-own", "role": "owner"}
MEMBER = {"id": "u-mem", "role": "member"}   # the built-in Member: Record payments only


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


class OpenAPage(Base):
    def test_pages_by_name_and_mistyped(self):
        for q, view in (("take me to unpaid", "collections"), ("show financials", "financials"), ("Go to the client list", "playbook"),
                        ("open retainage", "retainage"), ("open the activity log", "audit"), ("show finacials", "financials"),
                        ("please take me to the calendar", "calendar"), ("open my jobs", "jobs")):
            r = self.ask(q)
            self.assertEqual((r["matched"], r["actions"][0], r.get("go")), ("open-page", helper_intents.nav_action(view), 0), q)

    def test_a_job_or_a_client_by_name_typos_allowed(self):
        r = self.ask("open the Oak Street job")
        self.assertEqual(r["actions"], [{"type": "job", "job_id": "job-oak", "label": "Open Oak Street kitchen"}])
        self.assertEqual(self.ask("open turnr office")["actions"][0]["job_id"], "job-turner")
        r = self.ask("open TRN")
        self.assertEqual((r["matched"], r.get("go"), r["actions"][0]["job_id"]), ("open-page", 0, "job-turner"))
        r = self.ask("open Northline")
        self.assertEqual((r["text"], r["actions"]), ("Opening Northline GC in the Client playbook.", [{"type": "client", "payer_id": "payer-north", "label": "Open Northline GC"}]))

    def test_two_jobs_with_the_name_asks_which(self):
        self.state["jobs"][1]["name"] = "Oak Street office"
        r = self.ask("open oak street")
        self.assertEqual(r["text"], "Which one?")
        self.assertEqual({a["job_id"] for a in r["actions"]}, {"job-oak", "job-turner"})
        self.assertNotIn("go", r)
        self.assertEqual(self.ask("open oak street office")["actions"][0]["job_id"], "job-turner", "the rest of the name settles it")

    def test_a_question_about_a_job_is_answered_not_opened(self):
        r = self.ask("show me the oak street status")
        self.assertNotEqual(r["matched"], "open-page")
        self.assertEqual(self.ask("show me the money")["matched"], "money")

    def test_no_prepared_question_is_taken_for_a_command(self):
        snap = domain.snapshot(self.state)
        taken = [(e["id"], q) for e in helper.load_kb() for q in e["questions"] if helper_intents.intent(q, snap, set(domain.profiles.PERM_KEYS))]
        self.assertEqual(taken, [])

    def test_financials_is_not_offered_to_someone_who_cant_open_it(self):
        r = self.ask("take me to financials", MEMBER)
        self.assertEqual(r["actions"], [])
        self.assertIn("permission", r["text"])


class StartATask(Base):
    def form(self, q, user=OWNER, **kw):
        r = self.ask(q, user, **kw)
        return r, next((a for a in r["actions"] if a["type"] == "form"), None)

    def test_each_form_opens_on_the_named_job(self):
        for q, form, job in (("new change order for oak street", "newco", "job-oak"), ("record a payment on turner", "match", "job-turner"),
                             ("add a job record for grant park", "daily", "job-grant"), ("start a progress pay app for grant park", "progress", "job-grant"),
                             ("new pay app for grant park", "progress", "job-grant"), ("Log a payment for brookhaven", "match", "job-brook")):
            r, a = self.form(q)
            self.assertEqual((r["matched"], a["form"], a["job_id"], r.get("go")), ("start-task", form, job, 0), q)
            self.assertIn("Nothing is saved until you save it.", r["text"])

    def test_new_job_fills_in_what_it_was_told(self):
        r, a = self.form("new job called Elm Street for Northline")
        self.assertEqual(a, {"type": "form", "form": "newjob", "label": "New job", "client": "Northline GC", "name": "Elm Street"})
        self.assertEqual(self.form("add a job")[1], {"type": "form", "form": "newjob", "label": "New job"})

    def test_without_a_job_it_asks_which_listing_only_jobs_the_form_fits(self):
        r = self.ask("record a payment")
        self.assertNotIn("go", r, "nothing opens until a job is picked")
        self.assertEqual(r["text"], "Which job?")
        self.assertEqual([x["label"] for x in r["actions"]], ["Turner office TI", "Brookhaven kitchen"], "only jobs with a pay app waiting")
        # Each choice opens the form on that job by its id (asking again by name could go round in a circle).
        self.assertEqual(r["actions"][0], {"type": "form", "form": "match", "label": "Turner office TI", "job_id": "job-turner"})

    def test_a_form_that_doesnt_fit_the_job_opens_the_job_instead(self):
        r, a = self.form("record a payment on oak street")
        self.assertIsNone(a)
        self.assertEqual(r["text"], "Oak Street kitchen has no pay app waiting for payment.")
        r, a = self.form("start a progress pay app for turner")
        self.assertIsNone(a)
        self.assertIn("marked done", r["text"])
        r, a = self.form("new change order for maple residence")
        self.assertEqual((a, r["text"]), (None, "Maple Residence (closed) is finished."))

    def test_permissions_hold(self):
        self.state["company"]["profiles"] = [{"id": "owner", "name": "Owner", "built_in": True, "locked": True},
                                             {"id": "member", "name": "Member", "built_in": True, "perms": []}]
        r, a = self.form("record a payment on turner", MEMBER)
        self.assertIsNone(a)
        self.assertIn("permissions don't cover Record a Payment", r["text"])
        self.assertEqual(self.form("new change order for oak", MEMBER)[1]["form"], "newco", "a change order is anyone's")

    def test_a_how_to_answer_offers_the_form(self):
        r = self.ask("how do I record a payment", job_id="job-turner")
        self.assertEqual(r["matched"], "match-payment")
        self.assertEqual(r["actions"][0], {"type": "form", "form": "match", "label": "Record a Payment: Turner office TI", "job_id": "job-turner"})
        self.assertEqual(self.ask("how do I create a job")["actions"][0]["form"], "newjob")
        self.assertFalse([a for a in self.ask("how do I record a payment")["actions"] if a["type"] == "form"], "no job, no form")

    def test_draws_are_called_draws(self):
        self.state["company"]["billing_style"] = "draws"
        r, a = self.form("start a progress pay app for grant park")
        self.assertEqual(a["label"], "Progress Draw: Grant Park addition")
        self.assertTrue(self.ask("which pay apps are unpaid")["text"].startswith("Unpaid draws, oldest first"))


class LiveData(Base):
    def setUp(self):
        super().setUp()
        self.f = financials.compute(self.state)

    def test_who_owes_is_financials_figures_by_client(self):
        r = self.ask("how much am I owed, and by whom")
        self.assertEqual(r["matched"], "who-owes")
        self.assertTrue(r["text"].startswith(f"Owed to you: {domain.money(self.f['receivable_cents'])}"))
        for c in self.f["clients"]:
            if c["owed_cents"]:
                self.assertIn(f"- {c['client']}: {domain.money(c['owed_cents'])}", r["text"])
        self.assertEqual(r["actions"][0], helper_intents.nav_action("collections"))

    def test_who_owes_for_one_client_or_one_job(self):
        r = self.ask("how much does Northline owe me")
        self.assertTrue(r["text"].startswith("Northline GC owes you $119,700.00"), r["text"])
        self.assertNotIn("Ponce", r["text"])
        r = self.ask("how much does Turner office owe")
        self.assertTrue(r["text"].startswith("Turner office TI (Northline GC) owes $119,700.00"), r["text"])
        r = self.ask("who owes me money", job_id="job-turner")
        self.assertTrue(r["text"].startswith("Owed to you:"), "on a job's page the question still means everyone")

    def test_unpaid_pay_apps_oldest_first_with_their_age(self):
        r = self.ask("which pay apps are unpaid")
        self.assertEqual(r["matched"], "unpaid-payapps")
        rows = sorted(self.f["aging_rows"], key=lambda x: -x["days"])
        lines = [l for l in r["text"].split("\n") if l.startswith("- ")]
        self.assertEqual(lines, [f"- {x['pay_app']}, {x['job']}: {domain.money(x['cents'])}, {x['days']} days" for x in rows])
        self.assertEqual(self.ask("how old are my unpaid pay apps")["matched"], "unpaid-payapps")

    def test_done_not_billed_retainage_and_backlog(self):
        snap = domain.snapshot(self.state)
        r = self.ask("what's done but not billed")
        self.assertEqual(r["matched"], "unbilled")
        self.assertTrue(all(a["type"] == "job" for a in r["actions"]))
        r = self.ask("how much retainage is held")
        self.assertTrue(r["text"].startswith(f"Retainage held: {domain.money(snap['totals']['retainage_cents'])}"))
        r = self.ask("what's my backlog")
        self.assertEqual(r["matched"], "backlog")
        self.assertTrue(r["text"].startswith(f"Backlog (signed work not done yet): {domain.money(self.f['backlog_cents'])}."))
        self.assertEqual(r["actions"][0], helper_intents.nav_action("financials"))

    def test_due_this_week(self):
        today = datetime.now(timezone.utc).date()
        self.state.setdefault("tasks", []).extend([
            {"id": "t1", "title": "Call the inspector", "job_id": "job-grant", "status": "open", "due": (today + timedelta(days=3)).isoformat()},
            {"id": "t2", "title": "Order the vanity", "job_id": "", "status": "open", "due": (today + timedelta(days=10)).isoformat()}])
        r = self.ask("what's due this week")
        self.assertEqual(r["matched"], "due-this-week")
        self.assertIn("Call the inspector (Grant Park addition)", r["text"])
        self.assertNotIn("Order the vanity", r["text"])
        self.assertNotIn("$", r["text"], "dates and tasks, no money")
        self.assertEqual(r["actions"][-1], helper_intents.nav_action("calendar"))

    def test_status_of_a_named_job_typos_allowed(self):
        r = self.ask("status of the oak stret job")
        self.assertEqual(r["matched"], "job-status")
        self.assertTrue(r["text"].startswith("Oak Street kitchen (Ellison Residential)"))
        self.assertEqual(r["actions"], [{"type": "job", "job_id": "job-oak", "label": "Open Oak Street kitchen"}])


class Rails(Base):
    QUESTIONS = ("who owes me money", "how much is owed", "which pay apps are unpaid", "what's done but not billed", "how much retainage is held",
                 "what's my backlog", "what's due this week", "status of oak street", "what should I do today", "which follow-ups are due",
                 "draft a follow-up for turner", "take me to unpaid", "new change order for oak street", "record a payment on turner",
                 "new job for northline", "show me the money", "what's blocking oak street", "opne finacials", "zzz qqq")

    def test_it_never_changes_data_and_only_offers_buttons_that_open_something(self):
        before = copy.deepcopy(self.state)
        for q in self.QUESTIONS:
            for user in (OWNER, MEMBER):
                r = self.ask(q, user)
                self.assertTrue(all(a["type"] in helper_intents.SAFE_ACTIONS for a in r["actions"]), (q, r["actions"]))
        self.assertEqual(self.state, before)
        self.assertNotIn("post", helper_intents.SAFE_ACTIONS)
        shipped = json.loads(helper.SHIPPED_KB.read_text(encoding="utf-8"))["answers"]
        self.assertEqual([a for e in shipped for a in e.get("actions", []) if a.get("type") not in helper_intents.SAFE_ACTIONS], [])

    def test_an_answer_added_in_the_app_with_a_post_button_loses_it(self):
        helper.LOCAL_KB.write_text(json.dumps({"answers": [{"id": "x", "questions": ["Send everything now"], "answer": "Sure.",
                                                            "actions": [{"type": "post", "path": "/api/jobs/job-turner/follow-up", "label": "Send"}]}]}))
        r = self.ask("send everything now")
        self.assertEqual((r["matched"], r["actions"]), ("x", []))

    def test_no_company_money_without_see_company_financials(self):
        for q in ("who owes me money", "how much is owed", "what's done but not billed", "how much retainage is held", "what's my backlog", "show me the money"):
            r = self.ask(q, MEMBER)
            self.assertEqual(r["text"], helper.NO_MONEY, q)
            self.assertEqual(r["actions"], [], q)
        r = self.ask("which pay apps are unpaid", MEMBER)
        self.assertIn("PA-TRN-01, Turner office TI: 60 days" if "60 days" in r["text"] else "PA-TRN-01, Turner office TI:", r["text"])
        self.assertNotIn("$", r["text"], "which and how old, without amounts")
        # Given the permission, the same person sees them.
        self.state["company"]["profiles"] = [{"id": "owner", "name": "Owner", "built_in": True, "locked": True},
                                             {"id": "member", "name": "Member", "built_in": True, "perms": ["record_payments", "financials"]}]
        self.assertIn("$", self.ask("who owes me money", MEMBER)["text"])

    def test_only_the_signed_in_company(self):
        other = seed_state()
        other["jobs"] = [j for j in other["jobs"] if j["id"] == "job-turner"]
        other["jobs"][0].update(id="job-elsewhere", name="Zephyr Tower", code="ZPH")
        mine = seed_state()
        ids = {j["id"] for j in mine["jobs"]}
        for q in ("open Zephyr Tower", "open ZPH", "status of Zephyr Tower", "new change order for zephyr"):
            r = self.ask(q, state=mine)
            self.assertNotIn("job-elsewhere", json.dumps(r), q)
            self.assertTrue(all(a.get("job_id", "job-oak") in ids for a in r["actions"]), q)
            self.assertNotEqual(r["matched"], "open-page", q)
        r = self.ask("what's the status of this job", state=mine, job_id="job-elsewhere")
        self.assertEqual(r["text"], "Which job do you mean?")

    def test_when_it_doesnt_understand_it_says_so_with_the_closest(self):
        r = self.ask("opne finacials")
        self.assertIsNone(r["matched"])
        self.assertTrue(r["text"].startswith("I don't know that yet."))
        self.assertIn(helper_intents.nav_action("financials"), r["actions"])
        r = self.ask("grnt park thing")
        self.assertIn({"type": "job", "job_id": "job-grant", "label": "Open Grant Park addition"}, r["actions"])


class HelperHttpTests(ServerCase):
    def join(self, owner, addr):
        res, out = self.request("POST", "/api/team/invite", {"email": addr, "profile_id": "member"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        res, joined = self.request("POST", "/api/auth/accept-invite", {"accept_terms": True, "token": out["invite_path"].split("#invite=")[1],
                                                                       "name": "Mo", "password": "member password 1"})
        self.assertEqual(res.status, 200, joined)
        return res.getheader("Set-Cookie").split(";")[0]

    def test_the_server_asks_as_the_signed_in_person(self):
        owner, _ = self.signup("owner@helper2.example", "Helper Co")
        member = self.join(owner, "mo@helper2.example")
        res, out = self.request("POST", "/api/helper/ask", {"q": "who owes me money"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertNotEqual(out["text"], helper.NO_MONEY)
        res, out = self.request("POST", "/api/helper/ask", {"q": "who owes me money"}, cookie=member)
        self.assertEqual((res.status, out["text"]), (200, helper.NO_MONEY))
        res, out = self.request("POST", "/api/helper/ask", {"q": "take me to unpaid"}, cookie=member)
        self.assertEqual((out["matched"], out["go"], out["actions"][0]["view"]), ("open-page", 0, "collections"))


if __name__ == "__main__":
    unittest.main()
