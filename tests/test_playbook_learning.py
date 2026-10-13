"""Payer playbook learning (P3): what payers ask for becomes a proposed packet item."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import importer
from seed import seed_state


def payer_of(state, code):
    job = next(j for j in state["jobs"] if j["code"] == code)
    return job, next(p for p in state["payers"] if p["id"] == job["payer_id"])


class PlaybookLearningTests(unittest.TestCase):
    def test_rejection_proposes_an_item_and_adding_it_changes_the_packet(self):
        state = seed_state()
        job, payer = payer_of(state, "OAK")
        domain.learn_rejection(state, job["id"], "updated insurance certificate", "owner")
        sug = payer["packet_suggestions"][-1]
        self.assertEqual((sug["text"], sug["source"], sug["status"]), ("updated insurance certificate", "a rejection", "open"))
        domain.learn_rejection(state, job["id"], "updated insurance certificate", "owner")
        self.assertEqual(sum(1 for s in payer["packet_suggestions"] if s["status"] == "open"), 1, "no duplicates")
        domain.answer_packet_suggestion(state, payer["id"], sug["id"], True, "owner")
        self.assertTrue(payer["packet"].endswith(", updated insurance certificate"))
        with self.assertRaises(domain.GateError):
            domain.answer_packet_suggestion(state, payer["id"], sug["id"], True, "owner")
        domain.learn_rejection(state, job["id"], "updated insurance certificate", "owner")
        self.assertEqual(sum(1 for s in payer["packet_suggestions"] if s["status"] == "open"), 0, "already in the packet")

    def test_dismiss_and_reply_suggestions(self):
        state = seed_state()
        job, payer = payer_of(state, "BRK")
        for e in state["email_log"]:
            if "PA-BRK-01" in e["subject"]:
                e["status"] = "sent"
        item = importer.add_item(state, "email", text="We need the signed punch list before approval.",
                                 sender="payables@ponceproperty.example", subject="Re: Pay application PA-BRK-01 — Brookhaven kitchen")
        importer.capture_reply(state, item)
        sug = payer["packet_suggestions"][-1]
        self.assertEqual(sug["source"], "a reply")
        domain.answer_packet_suggestion(state, payer["id"], sug["id"], False, "owner")
        self.assertEqual(sug["status"], "dismissed")
        self.assertNotIn("punch list before", payer["packet"])


if __name__ == "__main__":
    unittest.main()
