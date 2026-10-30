"""Money > Unpaid's Recent contact (Stephen's Oct 29 review, M11): the emails to the client, the
calls noted, the client page events and the client's emails filed to the job, newest first."""

import itertools
import sys
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import importer
import payer_page
from test_money import new_state, new_job, send_open


class ContactLogTests(unittest.TestCase):
    def setUp(self):
        self.state = state = new_state()
        self.job = job = new_job(state)
        start = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
        self.clock = ((start + timedelta(minutes=n)).isoformat(timespec="seconds") for n in itertools.count())
        tick = lambda: next(self.clock)
        self.patches = [mock.patch.object(domain, "utcnow", tick), mock.patch.object(importer, "utcnow", tick)]
        for p in self.patches:
            p.start()
        domain.record_progress(state, job["id"], 4_000_000)
        self.inv = send_open(state, job)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_newest_first_from_every_source(self):
        state, job = self.state, self.job
        payer_page.mark_received(state, job, self.inv, "Sam Jones", domain.utcnow())
        domain.note_contact(state, job["id"], "Check goes out Friday", "", "Pat")
        domain.record_follow_up(state, job["id"], "Pat", early=True)
        state["email_log"][-1]["status"] = "bounced: mailbox full"
        item = importer.add_item(state, "email", "We'll pay next week.", sender="Sam Jones <ap@jones.example>", subject="Re: pay app", job_id=job["id"])
        importer.file_item(state, item["id"], job["id"], "note", "Pat")
        other = importer.add_item(state, "email", "Spam", sender="someone@else.example", subject="Offer", job_id=job["id"])
        importer.file_item(state, other["id"], job["id"], "note", "Pat")
        rows, total = domain.contact_log(state, job["id"], limit=None)
        self.assertEqual([r["kind"] for r in rows], ["email_in", "email", "call", "client_page", "email"])
        self.assertEqual(total, 5, "an email from someone else isn't the client's")
        self.assertEqual(rows == sorted(rows, key=lambda r: r["at"], reverse=True), True)
        self.assertEqual((rows[1]["label"], rows[1]["status"]), ("Follow-up emailed", "Bounced"))
        self.assertEqual(rows[2]["text"], "Spoke to Sam Jones: Check goes out Friday")
        self.assertEqual(rows[2]["by"], "Pat")
        self.assertEqual(rows[3]["label"], "Marked received on the client page")
        self.assertNotIn("page", rows[3]["text"])
        self.assertEqual(rows[4]["label"], "Pay app emailed")
        self.assertEqual(domain.contact_log(state, job["id"])[0], rows[:5])
        self.assertEqual(len(domain.contact_log(state, job["id"], limit=2)[0]), 2)

    def test_only_jobs_with_money_owed_carry_it(self):
        view = domain.job_view(self.state, self.job)
        self.assertEqual(view["contact_total"], 1)
        self.assertEqual(view["recent_contact"][0]["label"], "Pay app emailed")
        domain.match_payment(self.state, self.job["id"], self.inv["amount_cents"], "CHK-1", invoice_id=self.inv["id"])
        view = domain.job_view(self.state, self.job)
        self.assertNotIn("recent_contact", view)

    def test_draws_say_draw(self):
        self.state["company"]["billing_style"] = "draws"
        rows, _ = domain.contact_log(self.state, self.job["id"])
        self.assertEqual(rows[0]["label"], "Draw emailed")


if __name__ == "__main__":
    unittest.main()
