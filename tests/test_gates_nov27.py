"""Hard and soft gates on a pay app's supporting documents (Stephen, Nov 26). Per job, each document
is Required (a hard stop: the pay app doesn't go without it unless someone who can send pay apps
overrides it with a reason, logged) or Check (soft: whoever sends says it was checked or isn't
needed this time, and it goes). Signed contract and conditional waiver default to hard; progress
photos, daily tickets, time sheets and stored materials to soft. Daily tickets and time sheets are
two items now."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import approve_help
import domain
from test_money import new_job, new_state


def draft(job):
    return [i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES][-1]


def item(job, word):
    return next(p for p in draft(job)["packet"] if word.lower() in p["label"].lower())


class GateTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state)
        self.id = self.job["id"]
        payer = self.state["payers"][0]
        payer["packet"] = "Signed contract, conditional waiver, progress photos, daily tickets and time sheets, stored materials documents, insurance certificate."
        domain.record_progress(self.state, self.id, 1_000_000)

    def labels(self):
        return [p["label"] for p in draft(self.job)["packet"]]

    def test_the_defaults(self):
        gates = {p["label"]: domain.item_gate(self.job, p) for p in draft(self.job)["packet"]}
        self.assertEqual(gates["Signed contract"], "hard")
        self.assertEqual(next(g for l, g in gates.items() if "waiver" in l.lower()), "hard")
        self.assertEqual((gates["Progress photos"], gates["Daily tickets"], gates["Time sheets"], gates["Stored materials documents"]), ("soft",) * 4)
        self.assertEqual(gates["Insurance certificate"], "hard", "anything else keeps its hard stop")

    def test_daily_tickets_and_time_sheets_are_two_items(self):
        self.assertIn("Daily tickets", self.labels())
        self.assertIn("Time sheets", self.labels())
        self.assertNotIn("Daily tickets and time sheets", self.labels())
        self.assertEqual(domain.parse_packet_text("Daily tickets and time sheets, photos"), ["Daily tickets", "Time sheets", "Photos"])
        self.assertEqual([e["label"] for e in domain.PAPERWORK_CATALOG][3:5], ["Daily tickets", "Time sheets"])

    def test_a_soft_item_missing_doesnt_block_but_is_asked_at_send(self):
        for word in ("contract", "waiver", "insurance"):
            domain.resolve_packet_item(self.state, self.id, item(self.job, word)["id"], reason="Handed over")
        self.assertEqual(domain.blocker_lines(self.job), [], "photos, tickets, time sheets and stored materials don't stop it")
        inv = draft(self.job)
        with self.assertRaises(domain.GateError) as ctx:
            approve_help.approve_and_send(self.state, self.id, reviewed=domain.review_stamp(self.state, self.job, inv))
        asked = ctx.exception.extra["soft_checks"]
        self.assertEqual(len(asked), 4)
        self.assertIn("Any stored materials received this period to bill?", [a["question"] for a in asked])
        answers = {a["item_id"]: "not_needed" for a in asked}
        photo = item(self.job, "photo")
        answers[photo["id"]] = "checked"
        approve_help.approve_and_send(self.state, self.id, reviewed=domain.review_stamp(self.state, self.job, inv), soft_checks=answers)
        self.assertEqual(inv["status"], "sent")
        self.assertEqual(photo["soft_check"]["answer"], "checked")
        said = [a["detail"] for a in self.state["audit"] if a["action"] == "packet_soft_check"]
        self.assertTrue(any("stored materials: none this period" in d for d in said), said)

    def test_a_soft_check_can_be_answered_before_and_taken_back(self):
        photo = item(self.job, "photo")
        domain.soft_check_packet_item(self.state, self.id, photo["id"], "checked", "Ana")
        self.assertEqual(photo["soft_check"]["by"], "Ana")
        domain.soft_check_packet_item(self.state, self.id, photo["id"], "", "Ana")
        self.assertNotIn("soft_check", photo)
        with self.assertRaises(domain.GateError):
            domain.soft_check_packet_item(self.state, self.id, item(self.job, "contract")["id"], "checked", "Ana")

    def test_the_job_sets_each_one(self):
        domain.set_job_paperwork(self.state, self.id, {"gates": {"photo": "hard", "contract": "soft"}}, "Owner")
        self.assertEqual(domain.item_gate(self.job, item(self.job, "photo")), "hard")
        self.assertEqual(domain.item_gate(self.job, item(self.job, "contract")), "soft")
        self.assertIn("Progress photos is missing.", " ".join(domain.blocker_lines(self.job)))
        self.assertNotIn("Signed contract", " ".join(domain.blocker_lines(self.job)))
        view = domain._job_paperwork_view(self.state, self.job)
        rows = {g["key"]: g for g in view["gates"]}
        self.assertEqual((rows["photo"]["gate"], rows["photo"]["default"]), ("hard", "soft"))
        self.assertEqual(rows["waiver"]["gate"], "hard")
        self.assertTrue(any(a["action"] == "job_paperwork_gates" for a in self.state["audit"]))
        with self.assertRaises(domain.GateError):
            domain.set_job_paperwork(self.state, self.id, {"gates": {"photo": "maybe"}}, "Owner")

    def test_a_hard_stop_is_overridden_only_with_the_permission(self):
        contract = item(self.job, "contract")
        with self.assertRaises(domain.GateError) as ctx:
            domain.resolve_packet_item(self.state, self.id, contract["id"], "Sam", reason="Have it", may_override=False)
        self.assertIn("can send pay apps", str(ctx.exception))
        with self.assertRaises(domain.GateError):
            domain.skip_packet_item(self.state, self.id, contract["id"], "No", "Sam", may_override=False)
        domain.resolve_packet_item(self.state, self.id, item(self.job, "photo")["id"], "Sam", reason="On my phone", may_override=False)
        domain.resolve_packet_item(self.state, self.id, contract["id"], "Owner", reason="Signed at the kitchen table")
        self.assertEqual(contract["override"]["reason"], "Signed at the kitchen table")

    def test_time_sheets_and_tickets_are_suggested_to_their_own_items(self):
        for label, ticket_type in (("sheet.pdf", "time_sheet"), ("ticket.pdf", "delivery")):
            domain.add_job_record(self.state, self.id, label, "ticket", "", "Ana")
            rec = self.job["records"][-1]
            rec.update(file_ids=["f-" + label], ticket_type=ticket_type)
            self.state.setdefault("files", []).append({"id": "f-" + label, "name": label, "ext": ".pdf", "size": 10})
        domain._refresh_draft_gate(self.job, self.state)
        names = lambda word: [a["name"] for a in item(self.job, word).get("attachments", [])]
        self.assertEqual(names("time sheets"), ["sheet.pdf"])
        self.assertEqual(names("daily tickets"), ["ticket.pdf"])

    def test_work_billed_by_ticket_keeps_its_hard_stop(self):
        packet = draft(self.job)["packet"]
        sheet = next(p for p in packet if p["label"] == "Daily tickets")
        sheet["gate_fixed"] = "hard"
        self.assertEqual(domain.item_gate(self.job, sheet), "hard")


if __name__ == "__main__":
    unittest.main()
