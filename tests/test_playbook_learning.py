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



class CustomerFixTests(unittest.TestCase):
    """R3-07: customers can be added, renamed and merged from Customer settings."""

    def test_rename_collision_refused_ignoring_case(self):
        state = seed_state()
        _, ellison = payer_of(state, "OAK")
        north = next(p for p in state["payers"] if p["id"] == "payer-north")
        with self.assertRaises(domain.GateError) as raised:
            domain.update_payer(state, ellison["id"], {"name": f"  {north['name'].upper()} "})
        # The refusal names the customer as it is saved, not as it was typed.
        self.assertEqual(raised.exception.fields["name"], f"Another client is already called {north['name']}.")
        domain.update_payer(state, ellison["id"], {"name": "Ellison Family Trust"})
        self.assertEqual(ellison["name"], "Ellison Family Trust")
        self.assertIn("(was Ellison Residential)", state["audit"][-1]["detail"])

    def test_role_phone_and_blanks_clear(self):
        state = seed_state()
        job, payer = payer_of(state, "OAK")
        domain.update_payer(state, payer["id"], {"role": "GC", "phone": "0404 555 0100", "copy": "", "rejects_when": "",
                                                 "follow_up_days": "", "packet": ""})
        self.assertEqual((payer["role"], payer["phone"], payer["copy"], payer["rejects_when"]), ("GC", "0404 555 0100", "", ""))
        self.assertNotIn("follow_up_days", payer, "blank goes back to the usual number")
        self.assertTrue(payer["no_paperwork"], "an emptied paperwork list means nothing is needed")
        self.assertEqual(job["payer_role"], "GC")
        with self.assertRaises(domain.GateError):
            domain.update_payer(state, payer["id"], {"role": "Landlord"})
        with self.assertRaises(domain.GateError):
            domain.update_payer(state, payer["id"], {"name": " "})

    def test_add_customer(self):
        state = seed_state()
        payer = domain.add_payer(state, {"name": "Ruiz Residence", "ap_email": "ruiz@example.com"}, "owner")
        self.assertEqual((payer["role"], payer["approver"], payer["ap_email"]), ("Owner", "Ruiz Residence", "ruiz@example.com"))
        with self.assertRaises(domain.GateError) as raised:
            domain.add_payer(state, {"name": "ruiz residence"}, "owner")
        self.assertIn("already called", raised.exception.fields["name"])
        with self.assertRaises(domain.GateError):
            domain.add_payer(state, {"name": "Lee", "ap_email": "nope"}, "owner")
        job = domain.create_job(state, "Ruiz porch", "RUIZ RESIDENCE", 500_000, address="9 Elm St")
        self.assertEqual(job["payer_id"], payer["id"], "a new job finds the customer added first")

    def test_merge_moves_two_jobs_and_keeps_one_playbook(self):
        state = seed_state()
        target = domain.add_payer(state, {"name": "Ironleaf GC", "role": "GC", "ap_email": "ap@ironleaf.example"}, "owner")
        domain.update_payer(state, target["id"], {"packet": "Lien waiver", "follow_up_days": 5})
        typo = domain.add_payer(state, {"name": "Ironleaf", "ap_email": "wrong@ironleaf.example"}, "owner")
        a = domain.create_job(state, "Ironleaf A", "Ironleaf", 100_000, address="1 A St")
        b = domain.create_job(state, "Ironleaf B", "Ironleaf", 200_000, address="2 B St")
        domain.merge_payer(state, typo["id"], target["id"], "owner")
        self.assertEqual({a["payer_id"], b["payer_id"]}, {target["id"]})
        self.assertEqual((a["payer_role"], b["payer_role"]), ("GC", "GC"))
        self.assertNotIn(typo["id"], {p["id"] for p in state["payers"]})
        self.assertEqual(sum(1 for p in state["payers"] if p["name"].startswith("Ironleaf")), 1)
        self.assertEqual((target["ap_email"], target["packet"], target["follow_up_days"]), ("ap@ironleaf.example", "Lien waiver", 5))
        self.assertEqual(state["audit"][-1]["action"], "payer_merge")
        with self.assertRaises(domain.GateError):
            domain.merge_payer(state, target["id"], target["id"], "owner")


if __name__ == "__main__":
    unittest.main()
