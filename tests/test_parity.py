"""The server's rules and the app's copies of them say the same thing (bug hunt, parity)."""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calendar_feed
import domain
from waiver_help import signed_waiver
import helper
import sov
from seed import seed_state


def _state():
    return {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": [], "email_log": []}


def _clear(job):
    for inv in job["invoices"]:
        for p in inv.get("packet", []):
            p["present"] = True
    for p in job.get("packet", []):
        p["present"] = True


def _two_sent(state):
    """PA-01 $10,000 and PA-02 $5,000, both sent."""
    job = domain.create_job(state, "Pine", "Jones GC", 10000000, address="12 Pine", approver="Sam", ap_email="ap@j.example")
    domain.record_progress(state, job["id"], 1000000, "a")
    _clear(job)
    domain.approve_and_send(state, job["id"])
    domain.record_progress(state, job["id"], 1500000, "b")
    _clear(job)
    domain.approve_and_send(state, job["id"])
    return job


class StatusLabelTests(unittest.TestCase):
    def test_an_older_unpaid_pay_app_is_not_hidden_by_a_paid_newer_one(self):
        state = _state()
        job = _two_sent(state)
        pa1, pa2 = job["invoices"]
        domain.match_payment(state, job["id"], pa2["amount_cents"], "chk1", invoice_id=pa2["id"])
        self.assertEqual(pa1["status"], "sent")
        self.assertEqual(domain.status_label(job), "Submitted, awaiting acknowledgment")
        signed_waiver(state, job["id"])
        domain.send_waiver(state, job["id"], invoice_id=pa2["id"])
        self.assertEqual(domain.status_label(job), "Submitted, awaiting acknowledgment")
        snap = domain.snapshot(state)
        text, _ = helper.handle("job_status", snap, next(j for j in snap["jobs"] if j["id"] == job["id"]))
        self.assertIn("Pine (Jones GC): Submitted, awaiting acknowledgment.", text)

    def test_part_paid_older_pay_app(self):
        state = _state()
        job = _two_sent(state)
        pa1, pa2 = job["invoices"]
        domain.match_payment(state, job["id"], 400000, "chk0", invoice_id=pa1["id"])
        domain.match_payment(state, job["id"], pa2["amount_cents"], "chk1", invoice_id=pa2["id"])
        self.assertEqual(pa1["status"], "partial")
        self.assertEqual(domain.status_label(job), "Partial payment received")

    def test_newest_open_pay_app_still_speaks_first(self):
        state = _state()
        job = _two_sent(state)
        self.assertEqual(domain.status_label(job), "Submitted to Jones GC, awaiting Sam")
        pa1, pa2 = job["invoices"]
        domain.match_payment(state, job["id"], pa1["amount_cents"], "chk1", invoice_id=pa1["id"])
        domain.match_payment(state, job["id"], pa2["amount_cents"], "chk2", invoice_id=pa2["id"])
        signed_waiver(state, job["id"])
        domain.send_waiver(state, job["id"], invoice_id=pa1["id"])
        signed_waiver(state, job["id"])
        domain.send_waiver(state, job["id"], invoice_id=pa2["id"])
        self.assertEqual(domain.status_label(job), "Paid")


class CalendarFeedTests(unittest.TestCase):
    def _sent(self):
        state = _state()
        job = domain.create_job(state, "Pine", "Jones GC", 10000000, address="12 Pine", approver="Sam", ap_email="ap@j.example")
        domain.record_progress(state, job["id"], 1000000, "a")
        _clear(job)
        domain.approve_and_send(state, job["id"])
        return state, job

    def _follow_ups(self, state):
        return [(e["date"], e["title"]) for e in calendar_feed.events(state) if e["title"].startswith("Follow up")]

    def test_follow_up_event_is_the_servers_due_date(self):
        state, job = self._sent()
        payer = state["payers"][0]
        job["invoices"][0]["sent_at"] = (datetime.now(timezone.utc) - timedelta(days=28)).replace(microsecond=0).isoformat()
        nxt = (datetime.now(timezone.utc).date() + timedelta(days=14)).isoformat()
        domain.note_contact(state, job["id"], "check goes out", nxt)
        due = domain.follow_up_due(job, payer).date()
        self.assertEqual(due.isoformat(), nxt)
        self.assertEqual(self._follow_ups(state), [(due, f"Follow up on {job['invoices'][0]['number']} (Pine)")])

    def test_approved_pay_app_gets_its_follow_up(self):
        state, job = self._sent()
        payer = state["payers"][0]
        inv = job["invoices"][0]
        inv["status"] = "approved"
        inv["expected_payment_on"] = (datetime.now(timezone.utc).date() + timedelta(days=20)).isoformat()
        due = domain.follow_up_due(job, payer).date()
        self.assertEqual([d for d, _ in self._follow_ups(state)], [due])

    def test_one_follow_up_per_job_on_the_oldest(self):
        state = _state()
        job = _two_sent(state)
        due = domain.follow_up_due(job, state["payers"][0]).date()
        self.assertEqual(self._follow_ups(state), [(due, f"Follow up on {job['invoices'][0]['number']} (Pine)")])


class DrawsWordTests(unittest.TestCase):
    """A company that bills in draws (billing_style "draws", R6-01) reads "draw" in the job's
    status line, the calendar feed and the helper's live answers, as on Today and in the app."""

    def _draws(self):
        state = _state()
        state["company"]["billing_style"] = "draws"
        job = domain.create_job(state, "Pay app house", "Jones GC", 10000000, address="12 Pine", approver="Sam", ap_email="ap@j.example")
        return state, job

    def test_status_line(self):
        state, job = self._draws()
        self.assertEqual(domain.job_view(state, job)["status_label"], "In progress")
        domain.record_progress(state, job["id"], 1000000, "a")
        _clear(job)
        self.assertEqual(domain.job_view(state, job)["status_label"], "Draw draft in progress")
        job["invoices"][-1]["status"] = "awaiting_approval"
        self.assertEqual(domain.job_view(state, job)["status_label"], "Draw ready for review")
        job["invoices"][-1]["status"] = "void"
        domain.mark_done(state, job["id"])
        job["invoices"] = [i for i in job["invoices"] if i["status"] == "void"]
        self.assertEqual(domain.status_label(job, "draws"), "Draw not drafted")
        self.assertEqual(domain.job_summary(state, job)["status_label"], "Draw not drafted")
        self.assertEqual(domain.status_label(job), "Pay app not drafted", "pay apps by default")

    def test_calendar_feed(self):
        state, job = self._draws()
        state["payers"][0]["payapp_due_day"] = 25
        titles = [e["title"] for e in calendar_feed.events(state) if "due to" in e["title"]]
        self.assertTrue(titles)
        self.assertTrue(all(t.startswith("Draw due to Jones GC") for t in titles), titles)
        self.assertNotIn("pay app", calendar_feed.render(state).lower().replace("pay app house", ""))

    def test_helper_live_answers(self):
        state, job = self._draws()
        snap = domain.snapshot(state)
        self.assertEqual(helper.handle("who_owes", snap, None, state)[0], "Nobody owes you on a sent draw right now.")
        self.assertEqual(helper.handle("unbilled", snap, None, state)[0], "All finished work is on a draw.")
        view = next(j for j in snap["jobs"] if j["id"] == job["id"])
        self.assertEqual(helper.handle("draft_followup", snap, view, state)[0], "Pay app house has no sent, unpaid draw to follow up on.")


class CloseoutMissingTests(unittest.TestCase):
    def test_job_view_names_the_closeout_items_the_server_checks(self):
        state = seed_state()
        for jid in ("job-decatur", "job-brook"):
            job = domain._job(state, jid)
            self.assertFalse(job.get("closeout_packet"), "an older job, its closeout items in the job paperwork")
            self.assertEqual(job.get("work_status"), "complete")
            view = domain.job_view(state, job)
            with self.assertRaises(domain.GateError) as raised:
                domain.chase_retainage(state, jid)
            self.assertEqual(len(view["closeout_missing"]), len(raised.exception.blockers))
            self.assertTrue(view["closeout_missing"])

    def test_closeout_packet_when_there_is_one(self):
        state = _state()
        job = domain.create_job(state, "Pine", "Jones GC", 4500000, address="12 Pine", approver="Sam",
                                ap_email="ap@j.example", retainage_pct=10)
        domain.mark_done(state, job["id"], notes="Done")
        view = domain.job_view(state, job)
        self.assertEqual(view["closeout_missing"],
                         [p["label"] for p in job["closeout_packet"] if p["required"] and not p["present"]])


class SovWhitespaceTests(unittest.TestCase):
    def test_blank_quantity_with_spaces_is_one(self):
        lines = sov.clean_lines([{"description": "Demo", "quantity": "  ", "unit": "LS", "rate": "3500"}], [], lambda: "l1")
        self.assertEqual(lines[0]["qty_milli"], 1000)


if __name__ == "__main__":
    unittest.main()
