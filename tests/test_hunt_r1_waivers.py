"""The owner gate on an unconditional waiver and the look at a photo, from the Round 1 acceptance
test (Nov 6, items 13 and 14): every file going out is read for the gate, whatever it was filed
as; on a waiver item a photo or a scan takes one person's tap that says which waiver it is."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
from test_blockers import GOOD, Case, pdf
from test_packet import new_job, new_state, draft


class GateReadsEveryKind(Case):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Insurance certificate.")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = draft(self.job)
        self.waiver = next(p for p in self.inv["packet"] if p["kind"] == "waiver")
        self.cert = next(p for p in self.inv["packet"] if p["kind"].startswith("insurance"))
        self.data = {}
        self.attach(self.stored("Waiver", pdf(GOOD)))
        self.assertTrue(self.waiver["present"])

    def send(self, **kw):
        return domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read, **kw)

    def test_a_real_unconditional_waiver_under_the_insurance_item_needs_the_owner(self):
        for text in ("UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT Pine kitchen $4,000.00",
                     "Pine kitchen. The undersigned does hereby unconditionally waive and release all lien rights."):
            with self.subTest(text[:40]):
                self.setUp()
                rec = self.stored("COI", pdf(text), kind="insurance", typed="")
                self.attach(rec, item=self.cert)
                if not self.cert["present"]:
                    domain.confirm_packet_item(self.state, self.job["id"], self.cert["id"], "Sam", looked=True, read_file=self.read,
                                               shown=[rec["id"]])
                self.assertTrue(self.cert["present"])
                with self.assertRaises(domain.GateError) as refused:
                    self.send()
                self.assertIn("unconditional", str(refused.exception).lower())
                self.send(unconditional_override="Paid already, owner checked")
                self.assertEqual(self.inv["status"], "sent")

    def test_a_contract_that_only_talks_about_waivers_asks_for_no_look(self):
        rec = self.stored("Contract", b"SUBCONTRACT\nPine kitchen\n9.5 Unconditional Waivers\nSubcontractor shall deliver an unconditional waiver after payment.\n",
                          "c.txt", kind="insurance", typed="")
        self.attach(rec, item=self.cert)
        self.assertEqual(domain.unconditional_mentions(self.job, self.inv, self.state, self.read), [])
        self.assertEqual(domain.unconditional_waivers_attached(self.job, self.inv, self.state, self.read), [])


class PhotoOnAWaiverItem(Case):
    def test_a_photo_on_the_conditional_waiver_waits_for_one_tap_that_says_which_waiver(self):
        rec = self.stored("Signed waiver", b"\xff\xd8 jpeg", "w.jpg")
        self.attach(rec)
        self.assertFalse(self.waiver["present"], "nobody has looked at it yet")
        self.assertEqual(self.waiver["check"]["status"], "unchecked")
        self.assertEqual(domain.waiver_look_words(self.waiver), "I looked at it: it's the conditional waiver for this pay app")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read, shown=[rec["id"]])
        self.assertTrue(self.waiver["present"])
        self.assertEqual((self.waiver["confirmed_by"], self.waiver.get("looked_as")), ("Sam", "conditional"))
        row = self.state["audit"][-1]
        self.assertIn("Sam looked at", row["detail"])
        self.assertIn("conditional waiver", row["detail"])

    def test_a_photo_typed_unconditional_still_needs_the_owner_at_send(self):
        self.setUp()
        unc = next(p for p in self.inv["packet"] if p["kind"] == "waiver")
        unc["waiver_kind"] = "unconditional_progress"
        rec = self.stored("Signed waiver", b"\xff\xd8 jpeg", "w.jpg", typed="unconditional_progress")
        self.attach(rec, item=unc)
        self.assertEqual(domain.waiver_look_words(unc), "I looked at it: it's the unconditional waiver for this pay app")
        domain.confirm_packet_item(self.state, self.job["id"], unc["id"], "Sam", looked=True, read_file=self.read, shown=[rec["id"]])
        self.assertTrue(unc["present"])
        self.assertTrue(domain.unconditional_waivers_attached(self.job, self.inv, self.state, self.read))

    def test_a_photo_on_another_item_is_in_once_added(self):
        self.setUp()
        self.job["packet"] = None
        state, job = self.state, new_job(self.state, packet="Progress photos.")
        domain.record_progress(state, job["id"], 400_000)
        inv = draft(job)
        item = next(p for p in inv["packet"] if p["kind"].startswith("progress-photo") or p["kind"] == "photo")
        self.job = job
        rec = self.stored("Site", b"\xff\xd8 jpeg", "s.jpg", kind="photo", typed="")
        self.attach(rec, item=item)
        self.assertTrue(item["present"])
        self.assertEqual(item["confirmed_by"], "Sam")


class WaiverSendOrder(unittest.TestCase):
    """Round 1 acceptance, item 17: the waiver step's look comes before its send confirm. A check
    reads everything and warns as the send would, and sends nothing."""

    def test_check_only_warns_as_the_send_would_and_sends_nothing(self):
        from test_money_fixes import UnreadWaiverTests
        t = UnreadWaiverTests("test_the_waiver_step_reads_a_record_with_no_type")
        t.setUp()
        domain.approve_and_send(t.state, t.job["id"], "Owner")
        domain.match_payment(t.state, t.job["id"], t.inv["amount_cents"], "chk")
        rec = t.record("Signed form", "waiver", "Conditional waiver upon payment for another job")
        emails = len(t.state["email_log"])
        with self.assertRaises(domain.GateError) as raised:
            domain.send_waiver(t.state, t.job["id"], "Sam", invoice_id=t.inv["id"], read_file=t.read, check_only=True)
        self.assertTrue(raised.exception.extra["look_warning"])
        domain.send_waiver(t.state, t.job["id"], "Sam", invoice_id=t.inv["id"], looked_at=rec["id"], read_file=t.read, check_only=True)
        self.assertFalse(t.inv.get("waiver_sent"))
        self.assertEqual(len(t.state["email_log"]), emails)
        domain.send_waiver(t.state, t.job["id"], "Sam", invoice_id=t.inv["id"], looked_at=rec["id"], read_file=t.read)
        self.assertTrue(t.inv["waiver_sent"])


class WaiverPageState(unittest.TestCase):
    def test_a_job_saved_with_its_address_in_boxes_names_its_state(self):  # acceptance item 18
        self.assertEqual(domain.job_state({"address": "9 Elm St", "address_parts": {"street": "9 Elm St", "city": "Atlanta", "state": "GA", "zip": "30307"}}), "GA")
        self.assertEqual(domain.job_state({"address": "9 Elm St, Atlanta, GA 30307"}), "GA")
        self.assertEqual(domain.job_state({"address": "9 Elm"}), "")


if __name__ == "__main__":
    unittest.main()
