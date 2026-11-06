"""Round 1 of the Nov 5 audits, the pay app part, and the Nov 6 final review's money items: a signed
credit off retainage owes no second final waiver, and a refund paid back stays paid back once more
work is billed."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
from domain.jobinfo import default_retainage_pct
from test_blockers import GOOD, Case, pdf
from test_hunt_nov5_http import Base
from test_money import new_job, new_state, send_open


def signed_co(state, job, cents):
    domain.add_change_order(state, job["id"], "Change", cents)
    domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])


def finals_owed(job):
    return [x for x in domain._pending_waivers(job)
            if (domain.release_due_kind(job, x) if "reference" in x else domain.suggested_waiver_kind(job, x)).endswith("_final")]


class CreditWithTheFinalWaiverAlreadyDue(unittest.TestCase):
    """Nov 6 review B: the pay app's own waiver, due and not sent, becomes the final one."""

    def test_a_paid_final_pay_app_whose_waiver_is_unsent_covers_the_credit(self):
        s = new_state(); j = new_job(s, contract=1_000_000, retainage=10)
        domain.mark_done(s, j["id"]); inv = send_open(s, j)
        domain.match_payment(s, j["id"], inv["amount_cents"], "", invoice_id=inv["id"])
        self.assertTrue(inv["waiver_due"])
        signed_co(s, j, -100_000)
        domain.apply_credit_to_retainage(s, j["id"], "CO credit off retainage", "Sam")
        self.assertEqual(j.get("retainage_waivers") or [], [], "no second release to carry a final waiver")
        self.assertEqual([x["id"] for x in finals_owed(j)], [inv["id"]])

    def test_with_part_of_the_retainage_paid_one_final_waiver_is_owed(self):
        s = new_state(); j = new_job(s, contract=1_000_000, retainage=10)
        domain.mark_done(s, j["id"]); inv = send_open(s, j)
        domain.match_payment(s, j["id"], inv["amount_cents"], "", invoice_id=inv["id"])
        domain.record_retainage_payment(s, j["id"], 60_000, "part")
        signed_co(s, j, -40_000)
        domain.apply_credit_to_retainage(s, j["id"], "CO credit off retainage", "Sam")
        self.assertEqual(len(finals_owed(j)), 1)
        self.assertFalse(any(r.get("credit") for r in j.get("retainage_waivers", [])))


class RefundPaidBackThenMoreBilled(unittest.TestCase):
    """Nov 6 review C: the pay-back stays recorded; what is owed is the new pay app, not the refund."""

    def test_the_split_stays_on_the_pay_apps(self):
        s = new_state(); j = new_job(s, contract=1_000_000)
        domain.record_progress(s, j["id"], 600_000)
        inv = send_open(s, j)
        domain.match_payment(s, j["id"], 600_000, "", invoice_id=inv["id"])
        signed_co(s, j, -500_000)
        domain.settle_overbilling(s, j["id"], "refund")
        signed_co(s, j, 500_000)
        self.assertEqual(domain.job_money(j)["refund_owed_cents"], 100_000)
        domain.match_payment(s, j["id"], 100_000, "")
        self.assertEqual(domain.job_money(j)["refund_owed_cents"], 0)
        domain.record_progress(s, j["id"], 1_000_000)
        send_open(s, j)
        m = domain.job_money(j)
        self.assertEqual((m["outstanding_cents"], m["refund_owed_cents"]), (400_000, 0))
        self.assertEqual(domain._owed_text(m), "$4,000.00 is still owed on pay apps sent.")


class OneTapToConfirmAFile(Case):
    """Item 3: a photo the check can't read is in once it is added; a flagged mismatch still waits for
    the person's deliberate look, which names what didn't match."""

    def test_a_photo_added_to_an_item_is_in_with_who_added_it(self):
        # On a waiver item a photo waits for one tap (Round 1 acceptance, item 14); elsewhere it is in.
        self.attach(self.stored("Signed waiver", b"\xff\xd8 jpeg", "w.jpg"))
        self.assertFalse(self.waiver["present"])
        self.assertEqual(self.waiver["check"]["status"], "unchecked")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
        self.assertTrue(self.waiver["present"])
        self.assertEqual((self.waiver["confirmed_by"], self.waiver["looked_as"]), ("Sam", "conditional"))
        self.assertNotIn("unread", self.waiver)
        row = self.state["audit"][-1]
        self.assertEqual(row["action"], "packet_confirmed")
        self.assertIn("Sam looked at", row["detail"])
        self.assertIn("couldn't be read", row["detail"])

    def test_a_scanned_pdf_waits_for_the_tap_too(self):
        self.attach(self.stored("Scan", b"%PDF-1.4 /Subtype /Image stream", "s.pdf"))
        self.assertFalse(self.waiver["present"])
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
        self.assertTrue(self.waiver["present"])

    def test_a_file_that_was_read_and_matched_keeps_no_unread_mark(self):
        self.attach(self.stored("Waiver", pdf(GOOD)))
        self.assertTrue(self.waiver["present"])
        self.assertNotIn("unread", self.waiver)

    def test_a_flagged_mismatch_still_waits_for_the_look(self):
        self.attach(self.stored("w", pdf("Lien waiver for Oak street in the amount of $9.00")))
        self.assertFalse(self.waiver["present"])
        self.assertEqual(self.waiver["check"]["status"], "flagged")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", read_file=self.read)
        self.assertFalse(self.waiver["present"], "a confirm that didn't say they looked doesn't clear a mismatch")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
        self.assertTrue(self.waiver["present"])
        self.assertNotIn("unread", self.waiver)

    def test_a_photo_asked_to_be_looked_at_again_isnt_cleared_by_adding_it_again(self):
        rec = self.stored("Signed waiver", b"\xff\xd8 jpeg", "w.jpg")
        self.attach(rec)
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
        self.assertTrue(domain._ask_for_a_look(self.waiver, "The amount changed from $1.00 to $2.00. Look at it again."))
        self.attach(rec)
        self.assertFalse(self.waiver["present"])
        self.assertIn("The amount changed from $1.00 to $2.00. Look at it again.", self.waiver["check"]["problems"])
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
        self.assertTrue(self.waiver["present"])

    def test_a_suggested_photo_needs_the_persons_tap_but_no_second_one(self):
        self.stored("From the field", b"\xff\xd8 jpeg", "w.jpg")
        domain._refresh_draft_gate(self.job, self.state)
        self.assertFalse(self.waiver["present"], "a suggestion waits for a person")
        problems = domain.recheck_packet_item(self.state, self.job["id"], self.waiver["id"], read_file=self.read)
        self.assertTrue(problems)
        self.assertEqual(self.waiver["check"]["status"], "unchecked", "nothing for the person to answer: one tap")


class RetainageDefault(unittest.TestCase):
    """Item 1: 10% for GC, public and lower-tier clients, 0% for owners and homeowners, unless the
    client's playbook says its usual retainage; carried onto a new job; existing jobs unchanged."""

    def test_by_client_type_unless_the_playbook_says(self):
        for kind, pct in (("gc", 10), ("public", 10), ("sub", 10), ("owner", 0), ("homeowner", 0)):
            self.assertEqual(default_retainage_pct({"client_type": kind}), pct, kind)
        self.assertEqual(default_retainage_pct({"client_type": "homeowner", "retainage_pct": 5}), 5)
        self.assertEqual(default_retainage_pct({"client_type": "gc", "retainage_pct": 0}), 0)
        self.assertEqual(default_retainage_pct({"role": "GC"}), 10, "a client saved before types reads from its role")

    def test_a_new_job_with_no_retainage_given_takes_the_clients(self):
        s = new_state()
        gc = new_job(s, customer="Park GC", client_type="gc", retainage=None)
        self.assertEqual(gc["retainage_pct"], 10)
        home = new_job(s, name="Den", customer="Lee Home", client_type="homeowner", retainage=None)
        self.assertEqual(home["retainage_pct"], 0)
        self.assertEqual(new_job(s, name="Porch", customer="Park GC", retainage=0)["retainage_pct"], 0, "a 0 typed stays 0")
        domain.update_payer(s, gc["payer_id"], {"retainage_pct": "5"}, "Ann")
        self.assertEqual(new_job(s, name="Deck", customer="Park GC", retainage=None)["retainage_pct"], 5)
        self.assertEqual(gc["retainage_pct"], 10, "existing jobs unchanged")

    def test_the_playbook_field_is_checked_and_can_be_cleared(self):
        s = new_state(); j = new_job(s, customer="Park GC", client_type="gc")
        for bad in ("25", "abc", -1, True):
            with self.assertRaises(domain.GateError) as refused:
                domain.update_payer(s, j["payer_id"], {"retainage_pct": bad}, "Ann")
            self.assertIn("retainage_pct", refused.exception.fields)
        domain.update_payer(s, j["payer_id"], {"retainage_pct": 7}, "Ann")
        payer = domain._payer(s, j["payer_id"])
        self.assertEqual(payer["retainage_pct"], 7)
        domain.update_payer(s, j["payer_id"], {"retainage_pct": ""}, "Ann")
        self.assertNotIn("retainage_pct", payer)

    def test_the_page_reads_the_clients_usual_and_the_jobs_shortfall(self):
        s = new_state(); j = new_job(s, customer="Park GC", client_type="gc", retainage=0)
        self.assertEqual(domain.job_view(s, j)["retainage_usual"], 10)
        self.assertEqual(domain.light_snapshot(s)["payers"][0]["retainage_default"], 10)


class NewJobRetainageOverHttp(Base):
    def test_a_blank_retainage_takes_the_clients_and_a_typed_one_stays(self):
        status, snap = self.post("/api/jobs", {"name": "Oak wing", "customer": "Park GC", "address": "9 Oak Street", "contract_cents": 1_000_000, "retainage_pct": ""})
        self.assertEqual(status, 200, snap)
        made = next(j for j in snap["jobs"] if j["name"] == "Oak wing")
        self.assertEqual(made["retainage_pct"], 10)
        status, snap = self.post("/api/jobs", {"name": "Ash wing", "customer": "Park GC", "address": "9 Ash Street", "contract_cents": 1_000_000})
        self.assertEqual(next(j for j in snap["jobs"] if j["name"] == "Ash wing")["retainage_pct"], 10)
        self.assertEqual(self.job()["retainage_pct"], 0, "the job made with 0 keeps it")


def row(n, at, action, actor="Sam", job="j1", detail=""):
    return {"id": f"aud-{n}", "at": at, "job_id": job, "actor": actor, "action": action, "detail": detail or f"{action} {n}"}


class ActivityOnePerAction(unittest.TestCase):
    """Item 14: the job's Activity has one entry per person action; the app's own bookkeeping that
    came with it (attach, flag, confirm, suggested, filed) is under it; the audit trail keeps all."""

    def state(self, rows):
        return {"jobs": [{"id": "j1"}], "audit": rows, "email_log": []}

    def test_bookkeeping_right_after_an_action_goes_under_it(self):
        rows = [row(1, "2026-11-05T10:00:00+00:00", "add_record", detail="Sam added photo to the job's records: Porch."),
                row(2, "2026-11-05T10:00:00+00:00", "attach_record", detail="Sam attached Porch to Photos on PA-01."),
                row(3, "2026-11-05T10:00:01+00:00", "packet_confirmed", detail="Sam added Porch for Photos on PA-01."),
                row(4, "2026-11-05T10:00:01+00:00", "attach_record", actor="duracash", detail="Ticket suggested from the job records for Tickets on PA-01."),
                row(5, "2026-11-05T11:00:00+00:00", "match_payment", detail="Matched $10.00 (c) to PA-01.")]
        page = domain.timeline_page(self.state(rows), "j1", 0, 50)
        self.assertEqual(page["total"], 2)
        self.assertEqual([e["action"] for e in page["entries"]], ["match_payment", "add_record"])
        self.assertEqual([d["action"] for d in page["entries"][1]["details"]], ["attach_record", "packet_confirmed", "attach_record"])
        self.assertEqual(page["entries"][1]["details"][2]["text"], "Ticket suggested from the job records for Tickets on PA-01.")
        self.assertNotIn("details", page["entries"][0])

    def test_a_persons_own_confirm_later_is_its_own_entry(self):
        rows = [row(1, "2026-11-05T10:00:00+00:00", "add_record"), row(2, "2026-11-05T10:30:00+00:00", "packet_confirmed", detail="Sam checked w for Waiver on PA-01.")]
        page = domain.timeline_page(self.state(rows), "j1", 0, 50)
        self.assertEqual([e["action"] for e in page["entries"]], ["packet_confirmed", "add_record"])

    def test_the_apps_own_entry_with_no_action_before_it_stays_in_the_log_only(self):
        rows = [row(1, "2026-11-05T10:00:00+00:00", "add_record"),
                row(2, "2026-11-05T12:00:00+00:00", "document_flagged", actor="duracash", detail="Document check on Waiver: Amount not found.")]
        page = domain.timeline_page(self.state(rows), "j1", 0, 50)
        self.assertEqual([e["action"] for e in page["entries"]], ["add_record"])
        self.assertEqual(len(self.state(rows)["audit"]), 2)

    def test_other_jobs_and_emails_dont_take_the_bookkeeping(self):
        rows = [row(1, "2026-11-05T10:00:00+00:00", "add_record", job="j2"),
                row(2, "2026-11-05T10:00:01+00:00", "attach_record", detail="Sam attached x.")]
        page = domain.timeline_page(self.state(rows), "j1", 0, 50)
        self.assertEqual([e["action"] for e in page["entries"]], ["attach_record"])


class ActivityLogFilter(Base):
    def test_the_log_can_leave_out_the_apps_own_entries(self):
        res, _ = self.upload("waiver.txt", b"Conditional waiver Elm addition", kind="waiver")
        self.assertEqual(res.status, 200)
        line = self.job()["sov"]["lines"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        self.assertEqual(status, 200, data)
        _, everything = self.request("GET", "/api/audit?offset=0&limit=200", cookie=self.ann)
        _, people = self.request("GET", "/api/audit?offset=0&limit=200&who=people", cookie=self.ann)
        self.assertTrue(any(e["actor"] == "duracash" for e in everything["entries"]), "the trail keeps every row")
        self.assertFalse(any(e["actor"] == "duracash" for e in people["entries"]))
        self.assertLess(people["total"], everything["total"])


if __name__ == "__main__":
    unittest.main()
