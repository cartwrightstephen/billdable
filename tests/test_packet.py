"""Paperwork with each pay app and at closeout (Stephen's Oct 29 review, M3-M6, M13, M16): the
standard items, the conditional waiver every new pay app asks for, and what the email carries."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import migrations
from seed import seed_state


def new_state():
    return {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": [], "email_log": [], "files": []}


def new_job(state, contract=1_000_000, retainage=0, packet=None):
    job = domain.create_job(state, "Pine kitchen", "Jones Family", contract, address="12 Pine St, Atlanta, GA 30307",
                            approver="Sam", ap_email="ap@jones.example", retainage_pct=retainage)
    if packet is not None:
        domain.update_payer(state, job["payer_id"], {"packet": packet})
    return job


def record(state, job, kind, label, files=1):
    ids = []
    for n in range(files):
        fid = f"file-{len(state['files']) + 1}"
        state["files"].append({"id": fid, "name": f"{label} {n + 1}.pdf", "ext": ".pdf"})
        ids.append(fid)
    domain.add_job_record(state, job["id"], label, kind)
    rec = job["records"][-1]
    rec["file_ids"] = ids
    return rec


def draft(job):
    return next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)


class CatalogTests(unittest.TestCase):
    def test_each_item_names_the_record_kinds_that_fill_it(self):
        keys = [e["key"] for e in domain.PAPERWORK_ITEMS]
        self.assertEqual(len(keys), len(set(keys)))
        for entry in domain.PAPERWORK_ITEMS:
            self.assertTrue(entry["accepts_kinds"], entry["key"])
            self.assertTrue(set(entry["accepts_kinds"]) <= set(domain.RECORD_KINDS), entry["key"])
            self.assertEqual(domain._record_kinds_for({"kind": entry["key"], "label": ""})[1], entry["accepts_kinds"])
        self.assertEqual([e["label"] for e in domain.PAPERWORK_ITEMS if e["closeout"]],
                         ["Punch list sign-off", "Completion certificate", "Final lien waivers", "As-builts", "Warranties"])
        # Typed labels still find their kinds.
        self.assertEqual(domain._record_kinds_for({"kind": "x", "label": "Contractor's COI"})[1], ("insurance",))
        self.assertEqual(domain._record_kinds_for({"kind": "x", "label": "Final lien waiver"})[0], "final_waiver")
        self.assertTrue(domain._is_closeout("As-built drawings"))
        self.assertFalse(domain._is_closeout("Conditional waiver"))

    def test_every_job_gets_closeout_paperwork_at_mark_done(self):  # M6: not only jobs with retainage
        state = new_state()
        job = new_job(state)
        self.assertFalse(job.get("closeout_packet"))
        domain.mark_done(state, job["id"])
        self.assertEqual([p["label"] for p in job["closeout_packet"]],
                         ["Punch list sign-off", "Completion certificate", "Final lien waivers", "As-builts", "Warranties"])
        final = next(p for p in job["closeout_packet"] if p["kind"] == "final_waiver")
        self.assertEqual(final["waiver_kind"], "conditional_final")
        view = domain.job_view(state, job)
        self.assertEqual(next(p for p in view["closeout_packet"] if p["kind"] == "punch")["accepts_kinds"], ["punch"])

    def test_the_clients_closeout_items_win(self):
        state = new_state()
        job = new_job(state, packet="Invoice, conditional waiver. Punch list and as-builts before retainage.")
        domain.mark_done(state, job["id"])
        self.assertEqual([p["label"] for p in job["closeout_packet"]], ["Punch list", "As-builts before retainage"])


class ConditionalWaiverTests(unittest.TestCase):  # M13, M16, M19
    def test_a_client_list_without_a_waiver_still_blocks_send(self):
        state = new_state()
        job = new_job(state, packet="Invoice, before photos.")
        domain.record_progress(state, job["id"], 400_000)
        inv = draft(job)
        waiver = next(p for p in inv["packet"] if p["kind"] == "waiver")
        self.assertEqual((waiver["label"], waiver["waiver_kind"], waiver["required"]),
                         ("Conditional waiver on progress payment", "conditional_progress", True))
        for p in inv["packet"]:
            if p is not waiver:
                domain.resolve_packet_item(state, job["id"], p["id"], reason="test")
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(state, job["id"])
        self.assertIn("Conditional waiver on progress payment is missing.", raised.exception.blockers)
        rec = record(state, job, "waiver", "Signed conditional waiver PA-1")
        self.assertTrue(waiver["present"], "the waiver on the job record fills it")
        domain.approve_and_send(state, job["id"])
        self.assertEqual(inv["conditional_waiver_kind"], "conditional_progress")
        self.assertEqual((rec["waiver_kind"], rec["invoice_id"]), ("conditional_progress", inv["id"]))

    def test_a_client_list_with_a_waiver_keeps_its_own_item(self):
        state = new_state()
        job = new_job(state, packet="Invoice, conditional lien waiver.")
        domain.record_progress(state, job["id"], 400_000)
        waivers = [p for p in draft(job)["packet"] if p["kind"] != "invoice" and "waiver" in p["label"].lower()]
        self.assertEqual([(p["label"], p["waiver_kind"]) for p in waivers], [("Conditional lien waiver", "conditional_progress")])

    def test_not_needed_is_kept_and_audited(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        domain.record_progress(state, job["id"], 400_000)
        waiver = next(p for p in draft(job)["packet"] if p["kind"] == "waiver")
        domain.skip_packet_item(state, job["id"], waiver["id"], "They don't take waivers")
        self.assertEqual(state["payers"][0]["waiver_not_needed"]["reason"], "They don't take waivers")
        self.assertIn("marked Conditional waiver on progress payment not needed", state["audit"][-1]["detail"])
        inv = draft(job)
        domain.resolve_packet_item(state, job["id"], next(p for p in inv["packet"] if p["kind"] != "waiver")["id"])
        domain.approve_and_send(state, job["id"])
        domain.record_progress(state, job["id"], 800_000)
        nxt = next(p for p in draft(job)["packet"] if p["kind"] == "waiver")
        self.assertEqual((nxt["required"], nxt["skipped_reason"]), (False, "They don't take waivers"))

    def test_older_pay_apps_keep_their_paperwork(self):  # the M16 default: only pay apps drafted from now on
        state = seed_state()
        inman = domain._job(state, "job-inman")
        before = copy.deepcopy(inman["packet"])
        self.assertTrue(domain.draft_invoice(state, "job-inman", strict=True))
        self.assertEqual(inman["invoices"][-1]["packet"], inman["packet"])
        self.assertEqual(inman["packet"][0]["label"], before[0]["label"])
        self.assertEqual(len(inman["packet"]), len(before))

    def test_the_final_pay_app_asks_for_a_conditional_final_waiver(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        domain.mark_done(state, job["id"])
        inv = draft(job)
        self.assertTrue(inv["final"])
        waiver = next(p for p in inv["packet"] if p["kind"] == "waiver")
        self.assertEqual((waiver["label"], waiver["waiver_kind"]), ("Conditional waiver on final payment", "conditional_final"))


class TicketTests(unittest.TestCase):  # M6: tickets tied to change orders and unit-price lines
    def test_a_billed_change_order_asks_for_its_tickets(self):
        state = new_state()
        job = new_job(state, packet="Invoice, conditional waiver.")
        co = domain.add_change_order(state, job["id"], "Extra outlet", 50_000)["change_orders"][-1]
        other = domain.add_change_order(state, job["id"], "Extra fan", 30_000)["change_orders"][-1]
        mine = record(state, job, "ticket", "T&M ticket outlet")
        theirs = record(state, job, "ticket", "T&M ticket fan")
        domain.link_record_to_work(state, job["id"], mine["id"], co_id=co["id"])
        domain.link_record_to_work(state, job["id"], theirs["id"], co_id=other["id"])
        self.assertIn("tied T&M ticket outlet to CO 1", state["audit"][-1 - 1]["detail"])
        domain.include_change_order(state, job["id"], co["id"])
        domain.draft_invoice(state, job["id"], strict=True)
        ticket = next(p for p in draft(job)["packet"] if p["kind"] == "ticket")
        self.assertEqual(ticket["covers"], ["CO 1"])
        self.assertEqual([a["record_id"] for a in ticket["attachments"]], [mine["id"]], "the ticket for the work billed")

    def test_no_ticket_item_for_lump_sum_work(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        domain.record_progress(state, job["id"], 400_000)
        self.assertFalse([p for p in draft(job)["packet"] if p["kind"] == "ticket"])

    def test_a_link_to_work_not_on_the_job_is_refused(self):
        state = new_state()
        job = new_job(state)
        rec = record(state, job, "ticket", "Ticket")
        with self.assertRaises(domain.GateError) as raised:
            domain.link_record_to_work(state, job["id"], rec["id"], co_id="co-nope")
        self.assertIn("co_id", raised.exception.fields)


class AttachmentTests(unittest.TestCase):  # M4
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Invoice, photos.")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.photo = record(self.state, self.job, "photo", "Kitchen photos", files=2)
        self.waiver = record(self.state, self.job, "waiver", "Signed conditional waiver")
        self.inv = draft(self.job)
        invoice_item = next(p for p in self.inv["packet"] if p["label"] == "Invoice")
        domain.resolve_packet_item(self.state, self.job["id"], invoice_item["id"], reason="Invoice in the PDF")

    def change(self, action, target):
        return domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], action, target, "Sam")

    def test_the_email_carries_what_the_draft_lists(self):
        extra = record(self.state, self.job, "supplier_invoice", "Lumber receipt")
        self.change("add_record", extra["id"])
        self.change("exclude_file", self.photo["file_ids"][0])
        files = domain._packet_file_ids(self.state, self.job, self.inv)
        self.assertEqual(files, [self.waiver["file_ids"][0], self.photo["file_ids"][1], extra["file_ids"][0]])
        self.assertIn("Sam left Kitchen photos 1.pdf out of the email", self.state["audit"][-1]["detail"])
        domain.approve_and_send(self.state, self.job["id"], "Sam")
        self.assertEqual(self.state["email_log"][-1]["attachments"], files)
        with self.assertRaises(domain.GateError) as raised:
            self.change("include_file", self.photo["file_ids"][0])
        self.assertIn("has gone to the client", str(raised.exception))

    def test_leaving_out_the_last_file_of_an_item_makes_it_missing(self):
        photos = next(p for p in self.inv["packet"] if p["kind"] != "waiver" and "photo" in p["label"].lower())
        self.assertTrue(photos["present"])
        for fid in self.photo["file_ids"]:
            self.change("exclude_file", fid)
        self.assertFalse(photos["present"])
        self.assertEqual(self.inv["status"], "draft")
        with self.assertRaises(domain.GateError):
            domain.approve_and_send(self.state, self.job["id"])
        self.change("include_file", self.photo["file_ids"][1])
        self.assertTrue(photos["present"])
        self.assertEqual(self.inv["status"], "awaiting_approval")

    def test_refusals(self):
        with self.assertRaises(domain.GateError):
            self.change("exclude_file", "file-not-going")
        with self.assertRaises(domain.GateError):
            self.change("explode", self.photo["id"])
        with self.assertRaises(domain.GateError):
            self.change("remove_record", self.photo["id"])
        note = domain.add_job_record(self.state, self.job["id"], "Gate code", "note")["records"][-1]
        with self.assertRaises(domain.GateError) as raised:
            self.change("add_record", note["id"])
        self.assertIn("no file", str(raised.exception))


class InsuranceMigrationTests(unittest.TestCase):
    def test_other_records_that_say_insurance_become_insurance(self):
        state = seed_state()
        job = state["jobs"][0]
        job.setdefault("records", []).extend([
            {"id": "r1", "kind": "other", "label": "Certificate of insurance 2026"},
            {"id": "r2", "kind": "other", "label": "Gate code"},
            {"id": "r3", "kind": "photo", "label": "Insurance photo"},
        ])
        state["schema_version"] = migrations.SCHEMA_VERSION - 1
        migrations._paperwork_insurance_records(state)
        self.assertEqual([r["kind"] for r in job["records"][-3:]], ["insurance", "other", "photo"])


if __name__ == "__main__":
    unittest.main()
