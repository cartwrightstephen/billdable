"""Inbound reply capture (P1-3): replies to a sent pay app file themselves to the job."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importer
import mailer
from seed import seed_state


def state_with_sent_payapp():
    state = seed_state()
    for e in state["email_log"]:
        if "PA-BRK-01" in e["subject"]:
            e["status"] = "sent"
    return state


def reply(state, sender, subject="Re: Pay application PA-BRK-01 — Brookhaven kitchen",
          text="Needs the punch-list signoff before we can approve.\n\nOn Oct 6, 2026, Hartline wrote:\n> Attached is PA-BRK-01"):
    return importer.add_item(state, "email", text=text, sender=sender, subject=subject)


class ReplyCaptureTests(unittest.TestCase):
    def test_reply_from_the_payer_is_filed_and_named_in_the_status(self):
        state = state_with_sent_payapp()
        item = reply(state, "Accounts Payable <payables@ponceproperty.example>")
        job = importer.capture_reply(state, item)
        self.assertEqual(job["code"], "BRK")
        self.assertEqual(item["status"], "filed")
        self.assertTrue(item["auto_filed"])
        self.assertTrue(job["external_status"].startswith("Ponce Property Co. replied "))
        self.assertIn("Needs the punch-list signoff before we can approve.", job["external_status"])
        self.assertNotIn("wrote:", job["external_status"], "the quoted original is not the summary")
        self.assertEqual(job["records"][-1]["kind"], "note")
        self.assertEqual(state["audit"][-1]["action"], "reply_captured")

    def test_same_domain_counts_other_senders_do_not(self):
        state = state_with_sent_payapp()
        self.assertIsNotNone(importer.capture_reply(state, reply(state, "pm@ponceproperty.example")))
        state = state_with_sent_payapp()
        item = reply(state, "someone@elsewhere.example")
        self.assertIsNone(importer.capture_reply(state, item))
        self.assertEqual(item["status"], "new", "an unknown sender stays in the Inbox for a person")

    def test_needs_a_pay_app_that_was_actually_emailed(self):
        state = seed_state()  # the demo's pay apps were never emailed
        item = reply(state, "payables@ponceproperty.example")
        self.assertIsNone(importer.capture_reply(state, item))
        state = state_with_sent_payapp()
        item = reply(state, "payables@ponceproperty.example", subject="Question about the bath")
        self.assertIsNone(importer.capture_reply(state, item))

    def test_undo_restores_the_status_line(self):
        state = state_with_sent_payapp()
        job = next(j for j in state["jobs"] if j["code"] == "BRK")
        before = job.get("external_status")
        item = reply(state, "payables@ponceproperty.example")
        importer.capture_reply(state, item)
        importer.undo_item(state, item["id"], "owner")
        self.assertEqual(job.get("external_status"), before)
        self.assertNotIn("last_reply", job)
        self.assertEqual(item["status"], "new")

    def test_undo_keeps_a_newer_status_line(self):
        state = state_with_sent_payapp()
        job = next(j for j in state["jobs"] if j["code"] == "BRK")
        item = reply(state, "payables@ponceproperty.example")
        importer.capture_reply(state, item)
        job["external_status"] = "Approved by Kim, expect payment Nov 14"  # the customer approved it the next day
        importer.undo_item(state, item["id"], "owner")
        self.assertEqual(job["external_status"], "Approved by Kim, expect payment Nov 14")
        self.assertNotIn("last_reply", job)
        self.assertEqual(item["status"], "new")

    def test_undoing_two_replies_puts_the_first_status_line_back(self):
        # Newest first, then oldest first: either way the line ends as it was before both replies.
        for newest_first in (True, False):
            state = state_with_sent_payapp()
            job = next(j for j in state["jobs"] if j["code"] == "BRK")
            before = job.get("external_status")
            first = reply(state, "payables@ponceproperty.example", text="We need the lien waiver first.")
            importer.capture_reply(state, first)
            second = reply(state, "payables@ponceproperty.example", text="Approved, paying Friday.")
            importer.capture_reply(state, second)
            for item in ((second, first) if newest_first else (first, second)):
                importer.undo_item(state, item["id"], "owner")
                if item is second and newest_first:
                    self.assertIn("We need the lien waiver first.", job["external_status"])
            self.assertEqual(job.get("external_status"), before, f"newest first: {newest_first}")
            self.assertNotIn("last_reply", job)


class ReplyToHeaderTests(unittest.TestCase):
    CFG = {"from": "billing@duracash.example"}

    def test_reply_to_lists_the_approver_and_the_inbound_address(self):
        msg, _ = mailer.build_message({"to": "ap@payer.example", "subject": "Pay application PA-X-1", "body": "Hi"},
                                      "Pine", "pat@pine.example", [], self.CFG, inbound="jobs+abc123@in.duracash.example")
        self.assertEqual(msg["Reply-To"], "pat@pine.example, jobs+abc123@in.duracash.example")

    def test_without_an_inbound_address_only_the_approver(self):
        msg, _ = mailer.build_message({"to": "ap@payer.example", "subject": "s", "body": "b"}, "Pine", "pat@pine.example", [], self.CFG)
        self.assertEqual(msg["Reply-To"], "pat@pine.example")


if __name__ == "__main__":
    unittest.main()
