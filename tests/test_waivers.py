"""Lien waivers, two stages per pay app (Stephen's Oct 29 review, M13-M21): a conditional waiver
goes with the pay app, an unconditional one is sent by a person after payment. Duracash never
writes the form; it sends the signed one on the job record and says which type it was."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import server
from test_packet import new_state, new_job, record, draft
from waiver_help import signed_waiver


def sent_pay_app(state, job, cents=400_000):
    domain.record_progress(state, job["id"], cents)
    inv = draft(job)
    for item in inv["packet"]:
        if not item["present"] and item["required"] and item["kind"] != "waiver":
            domain.resolve_packet_item(state, job["id"], item["id"], reason="test")
    if any(not p["present"] and p["required"] for p in inv["packet"]):
        record(state, job, "waiver", f"Signed conditional waiver {inv['number']}")
    domain.approve_and_send(state, job["id"], "Owner")
    return inv


class UnconditionalWithPayAppTests(unittest.TestCase):  # M14
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Invoice.")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = draft(self.job)
        domain.resolve_packet_item(self.state, self.job["id"], next(p for p in self.inv["packet"] if p["label"] == "Invoice")["id"])
        self.rec = signed_waiver(self.state, self.job["id"], "unconditional_progress", "Unconditional waiver PA-1")
        waiver = next(p for p in self.inv["packet"] if p["kind"] == "waiver")
        self.assertFalse(waiver["present"], "an unconditional waiver never fills the conditional item by itself")
        domain.attach_record_to_packet(self.state, self.job["id"], waiver["id"], self.rec["id"], "Sam")

    def test_refused_without_a_reason(self):
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(self.state, self.job["id"], "Owner")
        self.assertEqual(str(raised.exception), domain.UNCONDITIONAL_ATTACHED)
        self.assertIn("unconditional_override", raised.exception.fields)
        self.assertEqual(self.inv["status"], "awaiting_approval")
        self.assertFalse(self.state["email_log"])

    def test_an_owner_override_is_kept_and_audited(self):
        domain.approve_and_send(self.state, self.job["id"], "Owner", unconditional_override="Client already paid by wire")
        self.assertEqual(self.inv["status"], "sent")
        self.assertEqual((self.inv["unconditional_override"]["reason"], self.inv["unconditional_override"]["by"]),
                         ("Client already paid by wire", "Owner"))
        row = next(a for a in self.state["audit"] if a["action"] == "unconditional_override")
        self.assertIn("Client already paid by wire", row["detail"])

    def test_left_out_of_the_email_it_no_longer_blocks(self):
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "exclude_file", self.rec["file_ids"][0])
        with self.assertRaises(domain.GateError) as raised:  # the item is missing again instead
            domain.approve_and_send(self.state, self.job["id"], "Owner")
        self.assertEqual(str(raised.exception), "Pay app blocked.")

    def test_a_member_cannot_override(self):
        ctx = server.Ctx(user={"id": "u2", "role": "member"}, body={"unconditional_override": "Because"}, company_id="co-x", actor="Pat")
        with self.assertRaises(domain.GateError) as raised:
            server._send_payapp(None, self.state, self.job["id"], None, ctx)
        self.assertIn("Only an owner", str(raised.exception))

    def test_a_waiver_label_says_its_type(self):
        self.assertEqual(domain.record_waiver_kind({"label": "Unconditional final waiver"}), "unconditional_final")
        self.assertEqual(domain.record_waiver_kind({"label": "Conditional waiver"}), "conditional_progress")
        self.assertEqual(domain.record_waiver_kind({"label": "Signed waiver"}), "")


class SecondStageTests(unittest.TestCase):  # M18, M19, M21
    def test_send_is_refused_without_a_signed_form(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        inv = sent_pay_app(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        self.assertEqual(domain.status_label(job), "Payment received, waiver not sent")
        with self.assertRaises(domain.GateError) as raised:
            domain.send_waiver(state, job["id"], "Owner")
        self.assertEqual(str(raised.exception), "Add the signed unconditional waiver to the job record first.")
        with self.assertRaises(domain.GateError) as raised:
            domain.close_job(state, job["id"])
        self.assertIn("A lien waiver is still due.", raised.exception.blockers)

    def test_the_conditional_waiver_that_went_with_the_pay_app_is_not_sent_again(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        inv = sent_pay_app(state, job)
        cond = next(r for r in job["records"] if r["kind"] == "waiver")
        self.assertIn(cond["file_ids"][0], state["email_log"][-1]["attachments"])
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        with self.assertRaises(domain.GateError):
            domain.send_waiver(state, job["id"], "Owner")
        uncond = signed_waiver(state, job["id"], label="Signed waiver after payment")
        domain.send_waiver(state, job["id"], "Owner", invoice_id=inv["id"])
        mail = state["email_log"][-1]
        self.assertEqual(mail["attachments"], uncond["file_ids"])
        self.assertEqual((mail["record_id"], mail["waiver_kind"], mail["invoice_id"]), (uncond["id"], "unconditional_progress", inv["id"]))
        self.assertNotIn("follow separately", mail["body"])
        self.assertIn(f"Amount covered: {domain.money(inv['amount_cents'])}.", mail["body"])
        self.assertEqual(uncond["waiver_kind"], "unconditional_progress")
        self.assertIn("(Signed waiver after payment)", state["audit"][-1]["detail"])

    def test_a_waiver_of_another_type_is_not_sent(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        inv = sent_pay_app(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        signed_waiver(state, job["id"], "unconditional_final")
        with self.assertRaises(domain.GateError):
            domain.send_waiver(state, job["id"], "Owner", kind="unconditional_progress")
        domain.send_waiver(state, job["id"], "Owner", kind="unconditional_final")

    def test_the_retainage_on_a_progress_waiver_is_named(self):  # M15
        state = new_state()
        job = new_job(state, retainage=10, packet="Invoice.")
        inv = sent_pay_app(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        signed_waiver(state, job["id"], "unconditional_progress")
        domain.send_waiver(state, job["id"], "Owner")
        self.assertIn("Not released by this waiver: the $400.00 retainage held on this pay app.", state["email_log"][-1]["body"])


class SuggestedTypeTests(unittest.TestCase):  # M17
    def test_conditional_before_payment_unconditional_after(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        inv = sent_pay_app(state, job)
        self.assertEqual(domain.suggested_waiver_kind(job, inv), "conditional_progress")
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        self.assertEqual(domain.suggested_waiver_kind(job, inv), "unconditional_progress")

    def test_never_final_while_retainage_is_held(self):
        state = new_state()
        job = new_job(state, retainage=10, packet="Invoice.")
        domain.mark_done(state, job["id"])
        inv = draft(job)
        for item in inv["packet"]:
            if not item["present"]:
                domain.resolve_packet_item(state, job["id"], item["id"])
        domain.approve_and_send(state, job["id"], "Owner")
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        self.assertTrue(inv["final"])
        self.assertEqual(domain.suggested_waiver_kind(job, inv), "unconditional_progress")
        stages = domain.waiver_stages(state, job, inv)
        self.assertEqual(stages["conditional"], {"kind": "conditional_final", "status": "Sent"})
        self.assertEqual(stages["unconditional"], {"kind": "unconditional_progress", "status": "Needed"})
        signed_waiver(state, job["id"])
        self.assertEqual(domain.waiver_stages(state, job, inv)["unconditional"]["status"], "Attached")


class StagesTests(unittest.TestCase):
    def test_stages_follow_the_pay_app(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        domain.record_progress(state, job["id"], 400_000)
        inv = draft(job)
        self.assertEqual(domain.waiver_stages(state, job, inv),
                         {"conditional": {"kind": "conditional_progress", "status": "Needed"},
                          "unconditional": {"kind": "", "status": "After payment"}})
        view = domain.job_view(state, job)
        self.assertEqual(view["invoices"][-1]["waiver_stages"]["conditional"]["status"], "Needed")
        self.assertEqual(view["waiver_state"], "GA")

    def test_set_waiver_type_is_audited(self):
        state = new_state()
        job = new_job(state)
        rec = record(state, job, "waiver", "Signed waiver")
        domain.set_waiver_type(state, job["id"], rec["id"], "unconditional_final", "Sam")
        self.assertEqual(rec["waiver_kind"], "unconditional_final")
        self.assertIn("set the type of Signed waiver: unconditional waiver on final payment", state["audit"][-1]["detail"])
        with self.assertRaises(domain.GateError):
            domain.set_waiver_type(state, job["id"], rec["id"], "made_up")


if __name__ == "__main__":
    unittest.main()
