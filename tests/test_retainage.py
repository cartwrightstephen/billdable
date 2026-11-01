"""Retainage (Stephen's Oct 29 review, M20, M24-M26): asked for only once the job is done, each
release owes its own lien waiver, and the job finishes only once that waiver has gone."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
from test_packet import new_state, new_job, draft
from waiver_help import signed_waiver


def done_and_paid(state, job):
    domain.mark_done(state, job["id"])
    inv = draft(job)
    for item in inv["packet"]:
        if not item["present"]:
            domain.resolve_packet_item(state, job["id"], item["id"], reason="test")
    domain.approve_and_send(state, job["id"], "Owner")
    domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
    signed_waiver(state, job["id"])
    domain.send_waiver(state, job["id"], "Owner")
    for item in job["closeout_packet"]:
        domain.resolve_packet_item(state, job["id"], item["id"], reason="In the binder")
    return inv


class AskTests(unittest.TestCase):
    def test_no_ask_before_done_even_for_part(self):  # M24
        state = new_state()
        job = new_job(state, retainage=10, packet="Invoice.")
        domain.record_progress(state, job["id"], 900_000)
        inv = draft(job)
        for item in inv["packet"]:
            domain.resolve_packet_item(state, job["id"], item["id"])
        domain.approve_and_send(state, job["id"], "Owner")
        with self.assertRaises(domain.GateError) as raised:
            domain.chase_retainage(state, job["id"], "Owner", amount_cents=10_000)
        self.assertIn("Mark the job done first", str(raised.exception))

    def test_part_after_done_and_times_asked(self):  # M24, M25
        state = new_state()
        job = new_job(state, retainage=10, packet="Invoice.")
        done_and_paid(state, job)
        domain.chase_retainage(state, job["id"], "Owner", amount_cents=50_000)
        self.assertEqual((job["retainage_chases"], job["retainage_asked_cents"]), (1, 50_000))
        self.assertIn("release $500.00 of the $1,000.00 retainage", state["email_log"][-1]["body"])
        with self.assertRaises(domain.GateError):
            domain.chase_retainage(state, job["id"], "Owner")  # the client's days between contacts


class ReleaseWaiverTests(unittest.TestCase):  # M20
    def test_each_release_owes_a_waiver_and_close_waits_for_it(self):
        state = new_state()
        job = new_job(state, retainage=10, packet="Invoice.")
        done_and_paid(state, job)
        domain.record_retainage_payment(state, job["id"], 40_000, "CHK-2")
        first = job["retainage_waivers"][-1]
        self.assertEqual((first["kind"], first["waiver_due"], first["waiver_sent"]), ("unconditional_progress", True, False))
        self.assertTrue(job["waiver_due"] and not job["waiver_sent"])
        domain.record_retainage_payment(state, job["id"], 60_000, "CHK-3")
        last = job["retainage_waivers"][-1]
        self.assertEqual(last["kind"], "unconditional_final")
        with self.assertRaises(domain.GateError) as raised:
            domain.close_job(state, job["id"])
        self.assertEqual(raised.exception.blockers, ["A lien waiver is still due."])
        rec = signed_waiver(state, job["id"], "unconditional_progress")
        with self.assertRaises(domain.GateError) as which:
            domain.send_waiver(state, job["id"], "Owner", release_id=first["id"])  # two are owed: which is it for? (B1)
        self.assertIn("for_id", which.exception.fields)
        domain.set_waiver_type(state, job["id"], rec["id"], "unconditional_progress", for_id=first["id"])
        domain.send_waiver(state, job["id"], "Owner", release_id=first["id"])
        self.assertTrue(first["waiver_sent"])
        self.assertEqual(state["email_log"][-1]["release_id"], first["id"])
        with self.assertRaises(domain.GateError):
            domain.close_job(state, job["id"])
        signed_waiver(state, job["id"], "unconditional_final")
        domain.send_waiver(state, job["id"], "Owner")  # the next one due
        self.assertTrue(last["waiver_sent"])
        domain.close_job(state, job["id"])
        self.assertTrue(job["closed"])

    def test_view_says_whether_the_signed_form_is_on_file(self):
        state = new_state()
        job = new_job(state, retainage=10, packet="Invoice.")
        done_and_paid(state, job)
        domain.record_retainage_payment(state, job["id"], 100_000, "CHK-2")
        self.assertFalse(domain.job_view(state, job)["retainage_waivers"][0]["on_file"])
        signed_waiver(state, job["id"], "unconditional_final")
        self.assertTrue(domain.job_view(state, job)["retainage_waivers"][0]["on_file"])
        with self.assertRaises(domain.GateError):
            domain.send_waiver(state, job["id"], "Owner", release_id="rw-9")


if __name__ == "__main__":
    unittest.main()
