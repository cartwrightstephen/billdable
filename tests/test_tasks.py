"""The To Do list (G5, T3): ball in court, assignees and due dates on the rows the jobs make, tasks
added by hand, the daily email per person, and a removed team member's items moving to the owner."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import domain
import migrations
import monthly
import server
from test_accounts import ServerCase
from test_mail import parsed

NOW = datetime(2026, 10, 28, 13, 0, tzinfo=timezone.utc)
TEAM = {"u-owner": "Pat Pine", "u-mo": "Mo Member"}


def iso(when):
    return when.isoformat(timespec="seconds")


class LocalDayTests(unittest.TestCase):
    """Review C3: "today" is the person's own date, not the server's UTC one, which on a US
    evening is already tomorrow."""

    def test_an_undated_row_is_not_dated_with_the_utc_day(self):
        state = server.empty_state("Pine Builders")
        domain.create_job(state, "Porch rebuild", "Dana Lee", 1_000_000, address="1 Elm St")
        evening = datetime(2026, 10, 30, 0, 30, tzinfo=timezone.utc)  # Oct 29, 8:30 pm in New York
        self.assertEqual([r["due"] for r in domain.today_rows(state, evening)], [""])

    def test_the_pages_date_is_taken_within_a_day_of_utc(self):
        evening = datetime(2026, 10, 30, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(domain.local_today("2026-10-29", evening), "2026-10-29")
        self.assertEqual(domain.local_today("2026-10-31", evening), "2026-10-31")
        for wrong in ("", "2026-10-20", "soon", None, "2026-13-01"):
            self.assertEqual(domain.local_today(wrong, evening), "2026-10-30", wrong)

    def test_a_new_jobs_start_and_a_diary_entrys_day_are_the_pages_date(self):
        state = server.empty_state("Pine Builders")
        utc = datetime.now(timezone.utc).date()
        yesterday = (utc - timedelta(days=1)).isoformat()
        job = domain.create_job(state, "Porch rebuild", "Dana Lee", 1_000_000, address="1 Elm St", today=yesterday)
        self.assertEqual(job["started_on"], yesterday)
        other = domain.create_job(state, "Deck", "Dana Lee", 1_000_000, address="2 Elm St")
        self.assertEqual(other["started_on"], utc.isoformat())
        domain.add_daily_record(state, job["id"], "Framing", "Ana", details={"today": yesterday})
        self.assertEqual(job["records"][-1]["label"], f"Daily record {yesterday}")
        domain.add_daily_record(state, job["id"], "Framing", "Ana", details={"today": "1999-01-01"})
        self.assertEqual(job["records"][-1]["label"], f"Daily record {utc.isoformat()}")


class RowTests(unittest.TestCase):
    def setUp(self):
        self.state = server.empty_state("Pine Builders")
        self.job = domain.create_job(self.state, "Porch rebuild", "Dana Lee", 1_000_000, address="1 Elm St",
                                     approver="Dana", ap_email="dana@example.com")
        self.payer = self.state["payers"][0]

    def rows(self, now=NOW):
        return domain.today_rows(self.state, now)

    def sent(self, days_ago=10):
        domain.record_progress(self.state, self.job["id"], 400_000)
        inv = self.job["invoices"][-1]
        for item in inv["packet"]:
            item["present"] = True
        inv.update({"status": "sent", "sent_at": iso(NOW - timedelta(days=days_ago))})
        return inv

    def test_each_row_has_a_key_ball_in_court_due_date_and_no_assignee(self):
        r = self.rows()[0]
        self.assertEqual(r["key"], f"new_job:{self.job['id']}:")
        # No date of its own: blank, which the page reads as the person's own today (review C3).
        self.assertEqual((r["ball_in_court"], r["due"], r["assignee_id"], r["assignee_name"]), ("us", "", "", ""))

    def test_keys_stay_the_same_from_one_snapshot_to_the_next(self):
        domain.record_progress(self.state, self.job["id"], 400_000)
        first = [r["key"] for r in self.rows()]
        self.assertEqual(first, [r["key"] for r in self.rows(NOW + timedelta(hours=3))])
        self.assertEqual(first, [r["key"] for r in domain.light_snapshot(self.state, NOW)["today_rows"]])
        self.assertEqual(first, [f"blocked:{self.job['id']}:{self.job['invoices'][-1]['id']}"])

    def test_waiting_on_the_client_for_a_follow_up_and_an_approval(self):
        inv = self.sent(days_ago=10)
        r = self.rows()[0]
        self.assertEqual((r["kind"], r["ball_in_court"]), ("follow_up", "client"))
        self.assertEqual(r["due"], (datetime.fromisoformat(inv["sent_at"]) + timedelta(days=7)).date().isoformat())
        inv.update({"status": "approved", "approved_by_payer": "Dana", "expected_payment_on": "2026-11-05"})
        r = self.rows(NOW - timedelta(days=4))[0]
        self.assertEqual((r["kind"], r["ball_in_court"], r["due"]), ("approved", "client", "2026-11-05"))

    def test_a_pay_app_to_bill_is_due_on_the_clients_cutoff(self):
        self.payer["payapp_due_day"] = 31
        r = self.rows()[0]
        self.assertEqual((r["kind"], r["ball_in_court"], r["due"]), ("due_soon", "us", "2026-10-31"))

    def test_the_jobs_default_assignee_then_an_assignment(self):
        self.job["assignee_id"] = "u-mo"
        self.assertEqual(domain.today_rows(self.state, NOW)[0]["assignee_id"], "u-mo")
        key = self.rows()[0]["key"]
        domain.assign_row(self.state, key, {"assignee_id": "u-owner", "due": "2026-11-02"}, TEAM, "Pat Pine", now=NOW)
        r = self.rows()[0]
        self.assertEqual((r["assignee_id"], r["assignee_name"], r["due"]), ("u-owner", "Pat Pine", "2026-11-02"))
        self.assertEqual(self.state["audit"][-1]["action"], "task_assign")
        self.assertIn("assigned it to Pat Pine and set it due Nov 2, 2026", self.state["audit"][-1]["detail"])
        # Blank both: back to the job's default and its own date.
        domain.assign_row(self.state, key, {"assignee_id": "", "due": ""}, TEAM, now=NOW)
        self.assertEqual(self.state["task_assignments"], {})
        self.assertEqual((self.rows()[0]["assignee_id"], self.rows()[0]["due"]), ("u-mo", ""))

    def test_a_hand_set_due_date_does_not_stick_to_the_next_turn_of_a_row(self):
        """Review C5: a follow-up row comes back under the same key after a follow-up is sent;
        the date set for the last one doesn't carry over, and the stale entry is dropped."""
        inv = self.sent(days_ago=10)
        fu = [r for r in self.rows() if r["kind"] == "follow_up"][0]
        domain.assign_row(self.state, fu["key"], {"due": "2026-10-29", "assignee_id": "u-mo"}, TEAM, "Pat", now=NOW)
        self.assertEqual([r["due"] for r in self.rows() if r["key"] == fu["key"]], ["2026-10-29"])
        inv["last_follow_up_at"] = iso(NOW + timedelta(days=1))
        self.job["follow_ups"] = 1
        later = NOW + timedelta(days=20)
        again = [r for r in self.rows(later) if r["key"] == fu["key"]]
        self.assertEqual(len(again), 1)
        self.assertNotEqual(again[0]["due"], "2026-10-29")
        self.assertEqual(again[0]["due"], again[0]["own_due"])
        self.assertEqual(again[0]["assignee_id"], "u-mo", "who it is assigned to stays")
        self.assertTrue(domain.prune_assignments(self.state, later))
        self.assertNotIn("due_override", self.state["task_assignments"][fu["key"]])
        # Rows that are gone are dropped on the next save, not only on the next assignment.
        self.state["task_assignments"]["ready:job-gone:x"] = {"assignee_id": "u-mo"}
        domain.prune_assignments(self.state, later)
        self.assertNotIn("ready:job-gone:x", self.state["task_assignments"])

    def test_assigning_someone_not_on_the_team_or_a_row_that_is_gone_is_refused(self):
        key = self.rows()[0]["key"]
        with self.assertRaises(domain.GateError) as ctx:
            domain.assign_row(self.state, key, {"assignee_id": "u-stranger"}, TEAM, now=NOW)
        self.assertEqual(ctx.exception.fields, {"assignee_id": "Pick someone on your team."})
        with self.assertRaises(domain.GateError):
            domain.assign_row(self.state, "ready:nope:x", {"assignee_id": "u-mo"}, TEAM, now=NOW)
        with self.assertRaises(domain.GateError):
            domain.assign_row(self.state, key, {"due": "next week"}, TEAM, now=NOW)

    def test_assignments_of_rows_that_are_gone_are_dropped(self):
        domain.assign_row(self.state, self.rows()[0]["key"], {"assignee_id": "u-mo"}, TEAM, now=NOW)
        domain.record_progress(self.state, self.job["id"], 400_000)   # the new_job row becomes a blocked one
        domain.assign_row(self.state, self.rows()[0]["key"], {"assignee_id": "u-owner"}, TEAM, now=NOW)
        self.assertEqual(list(self.state["task_assignments"]), [self.rows()[0]["key"]])


class HandTaskTests(unittest.TestCase):
    def setUp(self):
        self.state = server.empty_state("Pine Builders")
        self.job = domain.create_job(self.state, "Porch rebuild", "Dana Lee", 1_000_000, address="1 Elm St",
                                     approver="Dana", ap_email="dana@example.com")

    def test_add_edit_and_tick_off_with_every_change_audited(self):
        t = domain.add_task(self.state, {"title": "  Order  the tile ", "job_id": self.job["id"], "assignee_id": "u-mo", "due": "2026-11-03"},
                            TEAM, "Pat Pine", "u-owner")
        self.assertEqual((t["title"], t["assignee_name"], t["ball_in_court"], t["status"]), ("Order the tile", "Mo Member", "us", "open"))
        domain.edit_task(self.state, t["id"], {"ball_in_court": "client", "due": ""}, TEAM, "Pat Pine")
        self.assertEqual((t["ball_in_court"], t["due"]), ("client", ""))
        domain.set_task_done(self.state, t["id"], True, "Mo Member")
        self.assertEqual((t["status"], t["done_by"]), ("done", "Mo Member"))
        with self.assertRaises(domain.GateError):
            domain.set_task_done(self.state, t["id"], True, "Mo Member")
        domain.set_task_done(self.state, t["id"], False, "Mo Member")
        self.assertEqual((t["status"], t["done_at"]), ("open", ""))
        self.assertEqual([a["action"] for a in self.state["audit"][-4:]], ["task_add", "task_edit", "task_done", "task_reopen"])

    def test_bad_fields_are_refused_under_the_field(self):
        for body, field in (({"title": ""}, "title"), ({"title": "x" * 201}, "title"), ({"title": "A", "assignee_id": "u-x"}, "assignee_id"),
                            ({"title": "A", "due": "31/10"}, "due"), ({"title": "A", "ball_in_court": "them"}, "ball_in_court"),
                            ({"title": "A", "job_id": "job-nope"}, "job_id")):
            with self.assertRaises(domain.GateError) as ctx:
                domain.add_task(self.state, body, TEAM)
            self.assertIn(field, ctx.exception.fields, body)
        self.assertEqual(self.state.get("tasks", []), [])

    def test_the_snapshot_sends_open_tasks_and_those_done_in_the_last_30_days(self):
        old = domain.add_task(self.state, {"title": "Old"}, TEAM)
        recent = domain.add_task(self.state, {"title": "Recent"}, TEAM)
        domain.add_task(self.state, {"title": "Open"}, TEAM)
        old.update(status="done", done_at=iso(NOW - timedelta(days=31)))
        recent.update(status="done", done_at=iso(NOW - timedelta(days=2)))
        self.assertEqual([t["title"] for t in domain.light_snapshot(self.state, NOW)["tasks"]], ["Recent", "Open"])

    def test_a_removed_members_open_items_move_to_the_owner(self):
        mine = domain.add_task(self.state, {"title": "Mine", "assignee_id": "u-mo"}, TEAM)
        done = domain.add_task(self.state, {"title": "Done", "assignee_id": "u-mo"}, TEAM)
        domain.set_task_done(self.state, done["id"], True)
        key = domain.today_rows(self.state, NOW)[0]["key"]
        domain.assign_row(self.state, key, {"assignee_id": "u-mo"}, TEAM, now=NOW)
        self.assertEqual(domain.reassign_open_tasks(self.state, "u-mo", "u-owner", "Pat Pine", "Pat Pine"), 2)
        self.assertEqual((mine["assignee_id"], mine["assignee_name"]), ("u-owner", "Pat Pine"))
        self.assertEqual(done["assignee_id"], "u-mo", "a done task keeps who did it")
        self.assertEqual(self.state["task_assignments"][key]["assignee_id"], "u-owner")

    def test_the_daily_email_leaves_out_rows_due_later(self):
        """Review C7: a row whose own due date is ahead (the client's pay app cutoff) waits for
        that day; on the day, and after, it is listed."""
        self.state["payers"][0]["payapp_due_day"] = 31
        rows = domain.today_rows(self.state, NOW)
        self.assertEqual([(r["kind"], r["due"]) for r in rows], [("due_soon", "2026-10-31")])
        self.assertIsNone(domain.daily_digest(self.state, "", NOW))
        on_the_day = NOW + timedelta(days=3)
        subject, body = domain.daily_digest(self.state, "", on_the_day)
        self.assertIn("Porch rebuild", body)
        # A due date set by hand to today brings it in early.
        key = rows[0]["key"]
        domain.assign_row(self.state, key, {"due": "2026-10-28"}, TEAM, now=NOW)
        self.assertIsNotNone(domain.daily_digest(self.state, "", NOW))

    def test_the_daily_email_keeps_ready_work_and_holds_a_row_dated_by_hand(self):
        """Ready work is listed whatever its client's cutoff; a row someone dated for a later day
        waits for that day (C7, as narrowed)."""
        rows = domain.today_rows(self.state, NOW)
        self.assertEqual([r["kind"] for r in rows], ["new_job"])
        self.assertIsNotNone(domain.daily_digest(self.state, "", NOW), "a new job to bill is listed today")
        domain.assign_row(self.state, rows[0]["key"], {"due": "2026-11-05"}, TEAM, now=NOW)
        self.assertIsNone(domain.daily_digest(self.state, "", NOW), "dated by hand for next week: not yet")
        self.assertIsNotNone(domain.daily_digest(self.state, "", NOW + timedelta(days=9)))

    def test_a_removed_members_jobs_move_to_the_owner(self):
        """Review C2: the job's default assignee moves too, so its rows still reach someone."""
        self.job.update(assignee_id="u-mo", assignee_name="Mo Member")
        self.assertEqual(domain.today_rows(self.state, NOW)[0]["assignee_id"], "u-mo")
        self.assertEqual(domain.reassign_open_tasks(self.state, "u-mo", "u-owner", "Pat Pine", "Pat Pine"), 1)
        self.assertEqual((self.job["assignee_id"], self.job["assignee_name"]), ("u-owner", "Pat Pine"))
        row = domain.today_rows(self.state, NOW)[0]
        self.assertEqual((row["assignee_id"], row["assignee_name"]), ("u-owner", "Pat Pine"))
        subject, _ = domain.daily_digest(self.state, "", NOW, user_id="u-owner", owner=True)
        self.assertIn("1 new job to bill", subject)

    def test_the_daily_email_lists_only_the_recipients_items(self):
        key = domain.today_rows(self.state, NOW)[0]["key"]
        domain.assign_row(self.state, key, {"assignee_id": "u-mo"}, TEAM, now=NOW)
        domain.add_task(self.state, {"title": "Call the inspector", "assignee_id": "u-owner", "due": "2026-10-27"}, TEAM)
        domain.add_task(self.state, {"title": "Next month", "assignee_id": "u-owner", "due": "2026-11-27"}, TEAM)
        domain.add_task(self.state, {"title": "Anyone"}, TEAM)
        subject, body = domain.daily_digest(self.state, "", NOW, user_id="u-owner")
        self.assertEqual(subject, "Due today in Duracash: 2 tasks")
        self.assertIn("- Task: Call the inspector, due Oct 27, 2026", body)
        self.assertIn("- Task: Anyone", body)
        self.assertNotIn("Porch rebuild", body)
        self.assertNotIn("Next month", body)
        subject, body = domain.daily_digest(self.state, "", NOW, user_id="u-mo", owner=False)
        self.assertEqual(subject, "Due today in Duracash: 1 new job to bill")
        self.assertNotIn("Anyone", body)
        self.assertIsNone(domain.daily_digest(self.state, "", NOW, user_id="u-nobody", owner=False))
        # Without a person: everything, as before.
        self.assertIn("1 new job to bill, 2 tasks", domain.daily_digest(self.state, "", NOW)[0])

    def test_a_company_file_from_before_gets_the_empty_lists(self):
        old = {"company": {}, "payers": [], "jobs": [], "audit": [], "email_log": [], "schema_version": 4}
        migrations.migrate(old)
        self.assertEqual((old["tasks"], old["task_assignments"]), ([], {}))


class TaskHttpTests(ServerCase):
    def member_of(self, owner, addr):
        res, out = self.request("POST", "/api/team/invite", {"email": addr, "role": "member"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        token = out["invite_path"].split("#invite=")[1]
        res, joined = self.request("POST", "/api/auth/accept-invite", {"accept_terms": True, "token": token, "name": "Mo Member", "password": "member password 1"})
        self.assertEqual(res.status, 200, joined)
        return res.getheader("Set-Cookie").split(";")[0], joined["user"]

    def test_add_assign_tick_off_and_remove_a_member(self):
        owner, data = self.signup("owner@tasks.example", "Task Co")
        auth.verify_email(auth.verify_email_token(data["user"]["id"]))
        member, mo = self.member_of(owner, "mo@tasks.example")
        res, out = self.request("POST", "/api/jobs", {"name": "Porch", "customer": "Dana Lee", "contract_cents": 1_000_000, "address": "1 Elm St",
                                                      "approver": "Dana", "ap_email": "dana@example.com"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        # A member adds a task for the owner; someone from another company can't be assigned.
        res, state = self.request("POST", "/api/tasks", {"title": "Order tile", "assignee_id": data["user"]["id"], "due": "2026-11-03"}, cookie=member)
        self.assertEqual(res.status, 200, state)
        task = state["tasks"][0]
        self.assertEqual((task["assignee_name"], task["created_by"]), ("Pat Pine", "Mo Member"))
        _, other = self.signup("other@tasks.example", "Other Co")
        res, out = self.request("POST", "/api/tasks", {"title": "Sneaky", "assignee_id": other["user"]["id"]}, cookie=owner)
        self.assertEqual((res.status, out["fields"]), (409, {"assignee_id": "Pick someone on your team."}))
        # The new job's row goes to the member.
        key = state["today_rows"][0]["key"]
        res, state = self.request("POST", "/api/tasks/assign", {"key": key, "assignee_id": mo["id"]}, cookie=owner)
        self.assertEqual(res.status, 200, state)
        self.assertEqual(state["today_rows"][0]["assignee_name"], "Mo Member")
        res, state = self.request("POST", f"/api/tasks/{task['id']}", {"assignee_id": mo["id"]}, cookie=owner)
        self.assertEqual(state["tasks"][0]["assignee_id"], mo["id"])
        res, state = self.request("POST", f"/api/tasks/{task['id']}/done", {}, cookie=member)
        self.assertEqual(state["tasks"][0]["status"], "done")
        res, state = self.request("POST", f"/api/tasks/{task['id']}/done", {"done": False}, cookie=member)
        self.assertEqual(state["tasks"][0]["status"], "open")
        res, _ = self.request("POST", "/api/tasks/task-00000000/done", {}, cookie=owner)
        self.assertEqual(res.status, 409)
        # Removing the member moves their items to the owner who removed them.
        res, _ = self.request("POST", f"/api/team/{mo['id']}/remove", {}, cookie=owner)
        self.assertEqual(res.status, 200)
        _, state = self.request("GET", "/api/state", cookie=owner)
        self.assertEqual(state["tasks"][0]["assignee_id"], data["user"]["id"])
        self.assertEqual(state["today_rows"][0]["assignee_id"], data["user"]["id"])
        _, page = self.request("GET", "/api/audit", cookie=owner)
        actions = [a["action"] for a in page["entries"]]
        for action in ("task_add", "task_assign", "task_edit", "task_done", "task_reopen"):
            self.assertIn(action, actions)
        self.assertTrue(any("moved to Pat Pine" in a["detail"] for a in page["entries"]))

    def test_each_person_gets_their_own_daily_email(self):
        self.mail_on()
        owner, data = self.signup("boss@digest.example", "Digest Co")
        auth.verify_email(auth.verify_email_token(data["user"]["id"]))
        member, mo = self.member_of(owner, "mo@digest.example")
        self.request("POST", "/api/jobs", {"name": "Porch", "customer": "Dana Lee", "contract_cents": 1_000_000, "address": "1 Elm St",
                                           "approver": "Dana", "ap_email": "dana@example.com"}, cookie=owner)
        _, state = self.request("GET", "/api/state", cookie=owner)
        self.request("POST", "/api/tasks/assign", {"key": state["today_rows"][0]["key"], "assignee_id": mo["id"]}, cookie=owner)
        self.request("POST", "/api/tasks", {"title": "Sign the contract", "assignee_id": data["user"]["id"]}, cookie=owner)
        self.sink.messages.clear()
        when = datetime.now(timezone.utc).replace(hour=13)
        with mock.patch.object(monthly, "daily_email_hour", return_value=0):
            monthly.send_daily_digests(server.SITE, when)
        body = {m["to"][0]: parsed(m).get_body(("plain",)).get_content() for m in self.sink.messages
                if m["to"][0] in ("boss@digest.example", "mo@digest.example")}
        self.assertEqual(set(body), {"boss@digest.example", "mo@digest.example"})
        self.assertIn("Sign the contract", body["boss@digest.example"])
        self.assertNotIn("Porch has no pay app yet", body["boss@digest.example"])
        self.assertIn("Porch has no pay app yet", body["mo@digest.example"])
        self.assertNotIn("Sign the contract", body["mo@digest.example"])


if __name__ == "__main__":
    unittest.main()
