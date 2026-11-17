"""Periodic internal reminders (My account > Notifications): by email and text only once a person
turns them on, at most once a day, by the channel that person picked."""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import domain
import importer
import monthly
import reminders
import server
import sms
import storage

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)


def job(**extra):
    return {"id": "j1", "name": "Oak", "code": "OAK", "payer_id": "p1", "invoices": [], "payments": [], "change_orders": [], "records": [],
            "contract_cents": 100, "daily_records": [], "assignee_id": "", **extra}


def company(**extra):
    return {"company": {"name": "Pine"}, "jobs": [job()], "payers": [{"id": "p1", "name": "Northline", "ap_email": "ap@n.example", "phone": "(404) 555-0100", "approver": "Sam"}],
            "daily_records": [], "inbox": [], "tasks": [], "task_assignments": {}, **extra}


class Items(unittest.TestCase):
    def co_state(self, co, rows, day="2026-11-01"):
        state = company()
        state["jobs"][0]["change_orders"] = [co]
        state["daily_records"] = [{"id": "d1", "job_id": "j1", "at": f"{day}T12:00:00+00:00", "sections": {"co_work": rows}}]
        return state

    def co_texts(self, state):
        return [i["text"] for i in reminders.build(state, NOW) if i["kind"] == "co_records"]

    def test_stephens_example_unsigned_change_order_with_field_work(self):
        co = {"amount_cents": 100, "id": "c1", "title": "Extra outlets", "number": 1, "status": "pending", "pricing": "lump_sum", "created_at": "2026-10-20T00:00:00+00:00"}
        texts = self.co_texts(self.co_state(co, [{"co_id": "c1", "description": "Ran conduit", "hours_milli": 4000}]))
        self.assertEqual(len(texts), 1)
        self.assertIn("CO-1", texts[0])
        self.assertIn("Extra outlets", texts[0])
        self.assertIn("signature", texts[0])

    def test_time_and_materials_needs_hours_and_backup(self):
        co = {"amount_cents": 100, "id": "c1", "title": "Extra outlets", "status": "approved", "pricing": "tm", "support_record_ids": []}
        text = self.co_texts(self.co_state(co, [{"co_id": "c1", "description": "Ran conduit", "hours_milli": 0}]))[0]
        self.assertIn("hours", text)
        self.assertIn("backup", text)

    def test_a_signed_lump_sum_change_order_needs_nothing(self):
        co = {"amount_cents": 100, "id": "c1", "title": "Extra outlets", "status": "approved", "pricing": "lump_sum"}
        self.assertEqual(self.co_texts(self.co_state(co, [{"co_id": "c1", "description": "Ran conduit", "hours_milli": 0}])), [])

    def test_new_work_with_no_change_order(self):
        state = self.co_state({"amount_cents": 100, "id": "c9", "title": "Old", "status": "approved", "pricing": "lump_sum", "created_at": "2026-01-01T00:00:00+00:00"},
                              [{"co_id": "", "description": "Moved the panel", "hours_milli": 0}])
        self.assertIn("Moved the panel", self.co_texts(state)[0])
        state["jobs"][0]["change_orders"].append({"amount_cents": 100, "id": "c2", "title": "Panel", "status": "pending", "pricing": "lump_sum", "created_at": "2026-11-02T00:00:00+00:00"})
        self.assertFalse(any("Moved the panel" in t for t in self.co_texts(state)), "a change order written since answers it")

    def test_old_records_and_closed_jobs_drop_off(self):
        co = {"amount_cents": 100, "id": "c1", "title": "Extra", "status": "pending", "pricing": "lump_sum"}
        rows = [{"co_id": "c1", "description": "x", "hours_milli": 1000}]
        self.assertEqual(self.co_texts(self.co_state(co, rows, day="2026-08-01")), [])
        state = self.co_state(co, rows)
        state["jobs"][0]["closed"] = True
        self.assertEqual(self.co_texts(state), [])

    def test_missing_client_details(self):
        state = company()
        state["payers"][0]["phone"] = ""
        state["payers"][0]["ap_email"] = ""
        text = [i for i in reminders.build(state, NOW) if i["kind"] == "missing"][0]["text"]
        self.assertIn("Northline", text)
        self.assertIn("phone", text)
        self.assertIn("email", text)

    def test_important_dates_within_a_week(self):
        state = company()
        state["jobs"][0]["invoices"] = [{"id": "i1", "number": "PA-OAK-01", "status": "sent", "sent_at": "2026-11-01T00:00:00+00:00", "due_on": "2026-11-06", "amount_cents": 100}]
        dates = [i for i in reminders.build(state, NOW) if i["kind"] == "dates"]
        self.assertTrue(any("PA-OAK-01 payment due" in i["text"] for i in dates))
        self.assertTrue(all(i["job_id"] == "j1" for i in dates))
        state["jobs"][0]["invoices"][0]["due_on"] = "2026-12-20"
        self.assertFalse(any("payment due" in i["text"] for i in reminders.build(state, NOW)))

    def test_an_owner_gets_unassigned_and_a_member_only_their_own(self):
        items = [{"kind": "missing", "assignee_id": "", "text": "a"}, {"kind": "missing", "assignee_id": "u2", "text": "b"}]
        self.assertEqual([i["text"] for i in reminders.for_person(items, {"id": "u1", "role": "owner"})], ["a"])
        self.assertEqual([i["text"] for i in reminders.for_person(items, {"id": "u2", "role": "member"})], ["b"])

    def test_anticipated_end_date_when_a_job_has_one(self):
        state = company()
        self.assertFalse(any("end date" in i["text"] for i in reminders.build(state, NOW)), "absent is fine")
        state["jobs"][0]["anticipated_end_on"] = "not a date"
        self.assertFalse(any("end date" in i["text"] for i in reminders.build(state, NOW)))
        state["jobs"][0]["anticipated_end_on"] = "2026-11-06"
        self.assertTrue(any("Anticipated end date, Nov 6" in i["text"] for i in reminders.build(state, NOW)))
        state["jobs"][0]["work_status"] = "complete"
        self.assertFalse(any("end date" in i["text"] for i in reminders.build(state, NOW)))

    def test_a_job_made_with_an_end_date_next_week_gets_a_reminder(self):  # the field New job saves
        state = {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        end = (NOW.date() + timedelta(days=5)).isoformat()
        # The start is given, tied to NOW like the end. Left blank it is the real day (`today` is the
        # page's date and only counts within a day of the server's), and an end before it is refused.
        made = domain.create_job(state, "Pine deck", "Northline GC", 2_000_000, address="5 Birch Rd", approver="Nia", ap_email="ap@northline.example",
                                 phone="(404) 555-0177", role="GC", anticipated_end_on=end, started_on=NOW.date().isoformat())
        self.assertEqual(made["started_on"], NOW.date().isoformat())
        self.assertEqual(made["anticipated_end_on"], end)
        dates = [i for i in reminders.build(state, NOW) if i["kind"] == "dates"]
        self.assertTrue(any(i["job_id"] == made["id"] and "Anticipated end date" in i["text"] for i in dates), dates)
        made["anticipated_end_on"] = (NOW.date() + timedelta(days=8)).isoformat()
        self.assertFalse(any("Anticipated end date" in i["text"] for i in reminders.build(state, NOW)), "past the next 7 days")

    def test_a_follow_up_stays_until_dealt_with_and_a_past_due_day_does_not(self):
        state = company()
        state["jobs"][0]["invoices"] = [{"id": "i1", "number": "PA-OAK-01", "status": "sent", "sent_at": "2026-09-01T00:00:00+00:00", "due_on": "2026-10-01", "amount_cents": 100}]
        state["payers"][0]["usual_approval_days"] = 14
        texts = [i["text"] for i in reminders.build(state, NOW) if i["kind"] == "dates"]
        self.assertTrue(any("Follow up on PA-OAK-01" in t for t in texts), texts)
        self.assertTrue(any("PA-OAK-01 payment due" in t for t in texts), texts)

    def test_missing_supporting_documents(self):
        state = company()
        with mock.patch.object(domain, "is_blocked", return_value=True), mock.patch.object(domain, "blocker_lines", return_value=["Add the lien waiver."]):
            items = [i for i in reminders.build(state, NOW) if i["key"] == "missing:blocked:j1"]
        self.assertIn("Add the lien waiver.", items[0]["text"])

    def test_clean_prefs(self):
        self.assertEqual(reminders.clean_prefs({"dates": ["text", "email"], "missing": []}), {"dates": ["email", "text"], "missing": []})
        with self.assertRaises(ValueError):
            reminders.clean_prefs({"dates": ["pigeon"]})
        with self.assertRaises(ValueError):
            reminders.clean_prefs({"nope": ["email"]})


class LoopBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for target, name, value in ((auth, "AUTH_PATH", root / "auth.json"), (storage, "DATA_DIR", root)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.owner = auth.create_user("pat@example.com", "correct horse battery", "Pat", "co-x")
        self.sent = []
        self.state = company()
        self.state["payers"][0]["phone"] = ""
        site = mock.Mock()
        site.company_ids.return_value = ["co-x"]
        site.deleted.return_value = set()
        site.read.return_value = self.state
        site.app_url.return_value = "https://app.example"
        site.send.side_effect = lambda to, subject, body, company_id: (self.sent.append((to, subject, body)), "sent")[1]
        self.site = site

    def verified(self):
        data = auth._load()
        data["users"][0]["email_verified"] = True
        auth._save(data)


class Loop(LoopBase):
    def test_off_by_default_nothing_goes_out(self):
        self.verified()
        self.assertEqual(reminders.run(self.site, NOW), [])
        self.assertEqual(self.sent, [])
        self.site.read.assert_not_called()

    def test_one_item_comes_round_each_interval_until_it_is_dealt_with(self):
        self.verified()
        auth.set_reminders(self.owner["id"], {"missing": ["email"]})
        self.assertEqual(reminders.run(self.site, NOW.replace(hour=2)), [])
        self.assertEqual(self.sent, [])
        reminders.run(self.site, NOW)
        reminders.run(self.site, NOW + timedelta(hours=1))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Northline", self.sent[0][2])
        for days in (1, 2):
            reminders.run(self.site, NOW + timedelta(days=days))
        self.assertEqual(len(self.sent), 1, "inside the interval")
        reminders.run(self.site, NOW + timedelta(days=reminders.DEFAULT_REPEAT_DAYS))
        self.assertEqual(len(self.sent), 2, "the interval has passed")
        self.state["payers"][0]["phone"] = "(404) 555-0100"
        reminders.run(self.site, NOW + timedelta(days=2 * reminders.DEFAULT_REPEAT_DAYS))
        self.assertEqual(len(self.sent), 2, "dealt with")

    def test_app_only_sends_nothing_and_shows_with_an_unread_count(self):
        auth.set_reminders(self.owner["id"], {"missing": ["app"]})
        reminders.run(self.site, NOW)
        self.assertEqual(self.sent, [])
        person = auth.session_public(self.owner["id"])
        page = reminders.page(self.state, person, NOW)
        self.assertEqual(page["unread"], 1)
        self.assertIn("Northline", page["notifications"][0]["text"])
        self.assertEqual(reminders.page(self.state, person, NOW + timedelta(days=1))["unread"], 1, "no repeat inside the interval")
        reminders.mark_read("co-x", self.owner["id"], page["notifications"][0]["id"])
        self.assertEqual(reminders.page(self.state, person, NOW + timedelta(days=1))["unread"], 0)
        page = reminders.page(self.state, person, NOW + timedelta(days=reminders.DEFAULT_REPEAT_DAYS))
        self.assertEqual((page["unread"], len(page["notifications"])), (1, 2), "the interval has passed")
        self.state["payers"][0]["phone"] = "(404) 555-0100"
        page = reminders.page(self.state, person, NOW + timedelta(days=30))
        self.assertEqual(page["unread"], 0, "dealt with: it stops counting")
        self.assertTrue(all(n["done"] for n in page["notifications"]))

    def test_one_person_is_not_shown_anothers_notifications(self):
        other = auth.create_user("sam@example.com", "correct horse battery", "Sam", "co-x")
        auth.set_reminders(self.owner["id"], {"missing": ["app"]})
        reminders.page(self.state, auth.session_public(self.owner["id"]), NOW)
        self.assertEqual(reminders.page(self.state, auth.session_public(other["id"]), NOW)["notifications"], [])

    def test_the_same_item_goes_by_every_channel_picked(self):
        self.verified()
        auth.set_sms(self.owner["id"], "+14045550123", True)
        auth.set_reminders(self.owner["id"], {"missing": ["app", "email", "text"]})
        with mock.patch.object(sms, "send_to_member", return_value="sent") as send:
            reminders.run(self.site, NOW)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Northline", send.call_args[0][3])
        self.assertEqual(reminders.page(self.state, auth.session_public(self.owner["id"]), NOW)["unread"], 1)

    def test_text_goes_through_sms_and_a_failure_blocks_nothing(self):
        self.verified()
        auth.set_sms(self.owner["id"], "+14045550123", True)
        auth.set_reminders(self.owner["id"], {"missing": ["text"], "dates": ["email"]})
        self.state["jobs"][0]["invoices"] = [{"id": "i1", "number": "PA-OAK-01", "status": "sent", "sent_at": "2026-11-01T00:00:00+00:00", "due_on": "2026-11-06", "amount_cents": 100}]
        with mock.patch.object(sms, "send_to_member", side_effect=RuntimeError("boom")) as send:
            lines = reminders.run(self.site, NOW)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][2], self.owner["id"])
        self.assertEqual(len(self.sent), 1, "the email still went")
        self.assertTrue(lines)
        self.assertIn("not sent", reminders.last_result("co-x", self.owner["id"])["results"]["text"])

    def test_unverified_email_is_held_and_says_so(self):
        auth.set_reminders(self.owner["id"], {"missing": ["email"]})
        reminders.run(self.site, NOW)
        self.assertEqual(self.sent, [])
        self.assertIn("confirm", reminders.last_result("co-x", self.owner["id"])["results"]["email"])

    def test_demo_never(self):
        self.site.company_ids.return_value = [auth.DEMO_COMPANY_ID]
        auth.set_reminders(self.owner["id"], {"missing": ["email"]})
        self.assertEqual(reminders.run(self.site, NOW), [])

    def test_hourly_loop_runs_them(self):
        with mock.patch.object(monthly, "run_monthly_jobs", return_value=[]), mock.patch.object(monthly, "send_daily_digests", return_value=[]), \
                mock.patch.object(reminders, "run", return_value=["x"]) as run:
            monthly.run_once(self.site, NOW)
        run.assert_called_once()

    def test_the_text_says_who_and_is_short(self):
        items = [{"kind": "missing", "job": "Oak", "text": "word " * 200, "assignee_id": ""}] * 3
        text = reminders.text_message(items)
        self.assertTrue(text.startswith("Duracash: 3 reminders."))
        self.assertLessEqual(len(text), reminders.TEXT_MAX)
        self.assertIn("And 2 more", text)
        both = reminders.text_message([{"job": "Oak", "text": "Follow up."}, {"job": "Elm", "text": "Ends Nov 6."}])
        self.assertIn("Oak: Follow up.", both)
        self.assertIn("Elm: Ends Nov 6.", both)


class BootSendsNothing(unittest.TestCase):
    """Criterion 84: starting up, even past the send hour with reminders due, sends nothing."""

    def test_the_first_run_comes_after_the_wait_not_at_start_up(self):
        calls, waits = [], []

        def sleep(seconds):
            waits.append(seconds)
            if len(waits) == 3:
                raise SystemExit

        class Inline:
            def __init__(self, target, **kw):
                self.target = target

            def start(self):
                try:
                    self.target()
                except SystemExit:
                    pass

        with mock.patch.object(monthly, "run_once", side_effect=lambda site: calls.append(len(waits))), mock.patch.object(monthly.time, "sleep", sleep), mock.patch.object(monthly.threading, "Thread", Inline):
            monthly.start(mock.Mock())
        self.assertEqual(calls, [1, 2], "a sleep comes before every run")
        self.assertGreaterEqual(waits[0], monthly.BOOT_QUIET_SECONDS)

    def test_the_first_run_is_five_minutes_after_start_up(self):
        at = lambda h, m=0: datetime(2026, 11, 3, h, m, tzinfo=timezone.utc)
        for now in (at(15), at(22, 58), at(23, 10), at(23, 58)):
            self.assertEqual(monthly.first_wait(now), 300)

    def test_a_process_restarted_hourly_still_runs_and_sends_a_day_once(self):
        calls = []

        class Stop(BaseException):
            pass

        def sleep(seconds):
            if len(sleeps) == 1:
                raise Stop
            sleeps.append(seconds)

        class Inline:
            def __init__(self, target, **kw):
                self.target = target

            def start(self):
                try:
                    self.target()
                except Stop:
                    pass

        for _restart in range(3):
            sleeps = []
            with mock.patch.object(monthly, "run_once", side_effect=lambda site: calls.append(sleeps[0])), mock.patch.object(monthly.time, "sleep", sleep), mock.patch.object(monthly.threading, "Thread", Inline):
                monthly.start(mock.Mock())
        self.assertEqual(calls, [300, 300, 300], "each start-up runs once its short wait is over")

    def test_a_deploy_at_the_send_hour_keeps_that_days_digest(self):
        hour = monthly.daily_email_hour()
        start = datetime(2026, 11, 3, hour, 10, tzinfo=timezone.utc)
        first = start + timedelta(seconds=monthly.first_wait(start))
        self.assertEqual(first.date(), start.date(), "the first run is still that day")
        self.assertGreaterEqual(first.hour, hour)
        late = datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc)
        self.assertEqual((late + timedelta(seconds=monthly.first_wait(late))).date(), late.date())


class StateFile(LoopBase):
    """reminder_jobs.json going wrong never sends twice and never loses it silently."""

    def setUp(self):
        super().setUp()
        self.verified()
        auth.set_reminders(self.owner["id"], {"missing": ["email", "app"]})
        self.path = storage.DATA_DIR / reminders.JOBS_FILE
        self.me = auth.session_public(self.owner["id"])

    def test_a_file_that_can_not_be_written_means_no_send(self):
        with mock.patch.object(storage, "write_json", side_effect=OSError("disk")), mock.patch("alerts.notify") as alert:
            for hour in (15, 16, 17):
                reminders.run(self.site, NOW.replace(hour=hour))
        self.assertEqual(self.sent, [], "no record of it, so nothing goes out")
        self.assertTrue(alert.called)

    def test_a_crash_while_sending_does_not_send_again(self):
        self.site.send.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            reminders.run(self.site, NOW)
        self.site.send.side_effect = lambda to, subject, body, company_id: (self.sent.append(to), "sent")[1]
        reminders.run(self.site, NOW.replace(hour=16))
        self.assertEqual(self.sent, [], "its place was taken before sending")

    def test_a_failed_send_comes_back_the_next_day(self):
        self.site.send.side_effect = lambda to, subject, body, company_id: "not sent: the mail server said no"
        reminders.run(self.site, NOW)
        self.assertEqual(reminders.last_result("co-x", self.owner["id"])["results"]["email"], "not sent: the mail server said no")
        self.site.send.side_effect = lambda to, subject, body, company_id: (self.sent.append(to), "sent")[1]
        reminders.run(self.site, NOW.replace(hour=16))
        self.assertEqual(self.sent, [], "once a day")
        reminders.run(self.site, NOW + timedelta(days=1))
        self.assertEqual(len(self.sent), 1)

    def damage(self, content='{"co-x": {"u'):
        self.path.write_text(content)
        return content

    def test_a_damaged_file_sends_nothing_alerts_and_is_kept(self):
        for content in ('{"co-x": {"u', "[]"):
            self.damage(content)
            with mock.patch("sys.stderr") as log, mock.patch("alerts.notify") as alert:
                reminders.run(self.site, NOW)
            self.assertEqual(self.sent, [])
            self.assertTrue(alert.called)
            self.assertIn(reminders.JOBS_FILE, "".join(str(c) for c in log.write.call_args_list))
            self.assertEqual(self.path.read_text(), content, "not overwritten")
            self.assertTrue([f for f in self.path.parent.glob(reminders.JOBS_FILE + ".bad-*") if f.read_text() == content])

    def test_the_page_still_opens_on_a_damaged_file_and_writes_nothing(self):
        content = self.damage()
        with mock.patch("sys.stderr"), mock.patch("alerts.notify"):
            page = reminders.page(self.state, {**self.me, "company_id": "co-x"}, NOW)
            reminders.mark_read("co-x", self.owner["id"])
            reminders.forget_company("co-x")
            self.assertIsNone(reminders.last_result("co-x", self.owner["id"]))
        self.assertEqual(page["notifications"], [])
        self.assertEqual(self.path.read_text(), content)

    def test_the_page_still_opens_when_the_file_can_not_be_written(self):
        with mock.patch.object(storage, "write_json", side_effect=OSError("disk")), mock.patch("sys.stderr"), mock.patch("alerts.notify") as alert:
            page = reminders.page(self.state, {**self.me, "company_id": "co-x"}, NOW)
        self.assertEqual(len(page["notifications"]), 1)
        self.assertTrue(alert.called)

    def test_a_half_written_save_leaves_the_last_good_file(self):
        reminders.run(self.site, NOW)
        good = self.path.read_text()
        with mock.patch("json.dump", side_effect=OSError("disk")), mock.patch("alerts.notify"):
            reminders.run(self.site, NOW + timedelta(days=4))
        self.assertEqual(self.path.read_text(), good)


class WrongShapes(LoopBase):
    def person(self):
        return {**auth.session_public(self.owner["id"]), "company_id": "co-x", "reminders": {"missing": ["app"]}}

    def test_stored_files_of_the_wrong_shape_are_tolerated(self):
        path = storage.DATA_DIR / reminders.JOBS_FILE
        uid = self.owner["id"]
        for stored in ({"co-x": []}, {"co-x": {uid: []}}, {"co-x": {uid: {"sent": []}}}, {"co-x": {uid: {"sent": {"app": []}}}}, {"co-x": {uid: {"notes": "x"}}},
                       {"co-x": {uid: {"notes": ["x", {"id": "n1"}, {"key": "k"}]}}}, {"co-x": {uid: {"results": []}}}):
            path.write_text(json.dumps(stored))
            page = reminders.page(self.state, self.person(), NOW)
            self.assertTrue(all(isinstance(n, dict) and n["id"].startswith("n-") or n["id"] == "n1" for n in page["notifications"]), stored)
            reminders.mark_read("co-x", uid)
            self.assertEqual(reminders.run(self.site, NOW), [], stored)
            auth.set_reminders(uid, {"missing": ["email"]})
            reminders.run(self.site, NOW)
            auth.set_reminders(uid, {})

    def test_records_of_the_wrong_shape_are_skipped(self):
        for rec in ({"id": "d", "job_id": "j1", "at": "2026-11-01T00:00:00+00:00", "sections": {"co_work": ["x"]}},
                    {"id": "d", "job_id": "j1", "at": "2026-11-01T00:00:00+00:00", "sections": ["x"]},
                    {"id": "d", "job_id": "j1", "at": "2026-11-01T00:00:00+00:00", "sections": {"co_work": "x"}}, "x"):
            self.state["daily_records"] = [rec]
            self.assertEqual([i for i in reminders.build(self.state, NOW) if i["kind"] == "co_records"], [], rec)

    def test_something_unforeseen_in_one_kind_leaves_the_others(self):
        with mock.patch.object(reminders, "_co_items", side_effect=RuntimeError("no")), mock.patch("sys.stderr"), mock.patch("alerts.notify") as alert:
            kinds = {i["kind"] for i in reminders.build(self.state, NOW)}
        self.assertIn("missing", kinds)
        self.assertTrue(alert.called)


class WhoStillSees(LoopBase):
    def member(self):
        return {"id": "u2", "role": "member", "company_id": "co-x", "reminders": {"missing": ["app"]}}

    def test_someone_taken_off_a_job_no_longer_sees_its_notifications(self):
        self.state["jobs"][0]["assignee_id"] = "u2"
        notes = reminders.page(self.state, self.member(), NOW)["notifications"]
        self.assertEqual([n["job"] for n in notes], ["Oak"])
        self.state["jobs"][0]["assignee_id"] = "u3"
        page = reminders.page(self.state, self.member(), NOW)
        self.assertEqual((page["notifications"], page["unread"]), ([], 0))
        self.state["jobs"][0]["assignee_id"] = "u2"
        self.assertEqual(len(reminders.page(self.state, self.member(), NOW)["notifications"]), 1, "hidden, not lost")

    def test_a_note_from_before_this_rule_goes_by_the_job_name(self):
        data = {"co-x": {"u2": {"notes": [{"id": "n1", "key": "k", "kind": "missing", "job": "Oak", "text": "t", "path": "/", "at": "x", "read": False}]}}}
        (storage.DATA_DIR / reminders.JOBS_FILE).write_text(json.dumps(data))
        self.assertEqual(reminders.page(self.state, self.member(), NOW)["notifications"], [])
        self.state["jobs"][0]["assignee_id"] = "u2"
        self.assertIn("n1", [n["id"] for n in reminders.page(self.state, self.member(), NOW)["notifications"]])

    def test_an_owner_keeps_them_all(self):
        owner = {**self.member(), "role": "owner"}
        self.assertEqual(len(reminders.page(self.state, owner, NOW)["notifications"]), 1)
        self.state["jobs"][0]["assignee_id"] = "u3"
        self.assertEqual(len(reminders.page(self.state, owner, NOW)["notifications"]), 1)


class ReadOnly(LoopBase):
    def test_a_read_only_company_gets_no_reminders(self):
        self.verified()
        auth.set_reminders(self.owner["id"], {"missing": ["email"]})
        self.state["company"]["read_only"] = "the account is closed"
        reminders.run(self.site, NOW)
        self.assertEqual(self.sent, [])
        del self.state["company"]["read_only"]
        reminders.run(self.site, NOW)
        self.assertEqual(len(self.sent), 1, "nothing counted while it was read-only")


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = root / "auth.json", root / "companies", root, root / "uploads"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        h, data = {}, None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        if cookie:
            h["Cookie"] = cookie
        conn.request(method, path, body=data, headers=h)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res, json.loads(raw or b"{}")

    def signup(self, email):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": "correct horse battery", "name": "Pat", "company_name": "Pine"})
        return res.getheader("Set-Cookie").split(";")[0]

    def test_reminders_start_in_the_app_and_the_person_changes_them(self):
        cookie = self.signup("rem1@example.com")
        res, data = self.request("GET", "/api/reminders", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual({k["key"] for k in data["kinds"]}, {"dates", "missing", "co_records"})
        # Someone new starts with each kind in the app, nothing by email or text (round 1, item 19).
        self.assertTrue(all(v == ["app"] for v in data["prefs"].values()))
        self.assertEqual((data["notifications"], data["unread"]), ([], 0))
        res, data = self.request("POST", "/api/reminders/prefs", {"prefs": {"dates": [], "missing": [], "co_records": []}}, cookie=cookie)
        self.assertTrue(all(v == [] for v in data["prefs"].values()))
        # A new job's client is complete, so what is missing here is the pay app's supporting documents.
        _, saved = self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "4045550177",
                                                      "ap_email": "ap@northline.example", "contract_cents": 2000000}, cookie=cookie)
        made = saved["jobs"][-1]
        res, data = self.request("POST", f"/api/jobs/{made['id']}/sov-progress", {"done": {made["sov"]["lines"][0]["id"]: "0.5"}}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        res, data = self.request("POST", "/api/reminders/prefs", {"prefs": {"missing": ["app"]}}, cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(data["prefs"]["missing"], ["app"])
        self.assertEqual(data["unread"], 1)
        note = next(n for n in data["notifications"] if "pay app needs" in n["text"])
        res, data = self.request("POST", "/api/reminders/read", {"id": note["id"]}, cookie=cookie)
        self.assertEqual((res.status, data["unread"]), (200, 0))

    def test_text_needs_a_phone_and_agreement_first(self):
        cookie = self.signup("rem2@example.com")
        res, data = self.request("POST", "/api/reminders/prefs", {"prefs": {"missing": ["text"]}}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("missing", data["fields"])
        user = next(u for u in auth.all_users() if u["email"] == "rem2@example.com")
        auth.confirm_sms_code(user["id"], auth.issue_sms_code(user["id"], "+14045550123"))
        res, data = self.request("POST", "/api/auth/sms", {"phone": "(404) 555-0123", "sms_opt_in": True}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        self.assertEqual(data["user"]["phone"], "+14045550123")
        self.assertTrue(data["user"]["sms_opt_in"])
        self.assertNotIn("code_sent", data, "the number is already proven")
        res, data = self.request("POST", "/api/reminders/prefs", {"prefs": {"missing": ["text"]}}, cookie=cookie)
        self.assertEqual(res.status, 200)

    def test_my_account_says_when_a_number_can_not_be_confirmed(self):
        cookie = self.signup("rem5@example.com")
        res, data = self.request("POST", "/api/auth/sms", {"phone": "(404) 555-0155", "sms_opt_in": True}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("set up", data["fields"]["phone"])
        res, data = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertFalse(data.get("user", data).get("phone"), "nothing is saved")

    def test_a_clients_number_with_an_extension_is_refused_on_my_account(self):
        cookie = self.signup("rem6@example.com")
        res, data = self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "(404) 555-0142 x12",
                                                       "ap_email": "ap@northline.example", "contract_cents": 2000000}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        self.assertEqual(data["payers"][0]["phone"], "(404) 555-0142 x12")
        for typed in ("404-555-0142", "+1 (404) 555 0142"):
            res, data = self.request("POST", "/api/auth/sms", {"phone": typed, "sms_opt_in": True}, cookie=cookie)
            self.assertEqual(res.status, 409, typed)
            self.assertIn("client", data["fields"]["phone"])
        res, data = self.request("POST", "/api/auth/sms", {"phone": "404555\u0660\u0661\u0664\u0662", "sms_opt_in": True}, cookie=cookie)
        self.assertEqual(res.status, 409)

    def test_my_account_refuses_a_bad_number_and_a_clients_number(self):
        cookie = self.signup("rem3@example.com")
        res, data = self.request("POST", "/api/auth/sms", {"phone": "555-0123", "sms_opt_in": False}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("phone", data["fields"])
        res, data = self.request("POST", "/api/auth/sms", {"phone": "", "sms_opt_in": True}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "(404) 555-0188",
                                          "ap_email": "ap@northline.example", "contract_cents": 2000000}, cookie=cookie)
        res, data = self.request("POST", "/api/auth/sms", {"phone": "404-555-0188", "sms_opt_in": True}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("client", data["fields"]["phone"])

    def test_prefs_of_the_wrong_shape_are_a_plain_400(self):
        cookie = self.signup("rem7@example.com")
        for prefs in ({"dates": [[]]}, {"dates": [{}]}, {"dates": "text"}, {"nope": []}, [], "x"):
            res, data = self.request("POST", "/api/reminders/prefs", {"prefs": prefs}, cookie=cookie)
            self.assertEqual(res.status, 400, prefs)
            self.assertTrue(data["error"])

    def test_wrong_shapes_on_disk_do_not_stop_the_page(self):
        cookie = self.signup("rem8@example.com")
        me = next(u for u in auth.all_users() if u["email"] == "rem8@example.com")
        path = storage.DATA_DIR / reminders.JOBS_FILE
        path.write_text(json.dumps({me["company_id"]: []}))
        res, data = self.request("GET", "/api/reminders", cookie=cookie)
        self.assertEqual(res.status, 200, data)
        path.unlink()

    def test_the_demo_account_can_not_save_a_phone(self):
        res, data = self.request("POST", "/api/reminders/prefs", {"prefs": {}})
        self.assertEqual(res.status, 401)


if __name__ == "__main__":
    unittest.main()
