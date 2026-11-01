"""The independent review of Oct 30, section B (money: pay apps, change orders, paperwork,
waivers, retainage). Each test is one of the reviewer's reproduction scripts, kept as a check
that the fix holds. Stephen's rules from his Oct 29 review stay: a conditional waiver goes with
every pay app (M16, its audited way out is Not needed for this client), the unconditional one is
sent by a person after payment (M21), and a job does not close while one is still owed (M19)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import migrations
import sov
from seed import seed_state


def new_state():
    return {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": [], "email_log": [], "files": [], "daily_records": []}


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
    domain.add_job_record(state, job["id"], label, "note")
    rec = job["records"][-1]
    rec["kind"] = kind
    rec["file_ids"] = ids
    domain._refresh_draft_gate(job, state)
    return rec


def draft(job):
    return next((i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES), None)


def sov_job(state, retainage=0, packet=None):
    job = new_job(state, retainage=retainage, packet=packet)
    domain.save_sov(state, job["id"], [
        {"item": "1", "description": "Rough-in wiring", "unit": "LF", "quantity": "1000", "rate": "5.00"},
        {"item": "2", "description": "Panel", "unit": "LS", "quantity": "1", "rate": "5000.00"},
    ])
    return job


def send_one(state, job, total, tag):
    domain.record_progress(state, job["id"], total)
    inv = draft(job)
    record(state, job, "photo", f"Photos {tag}")
    record(state, job, "waiver", f"Signed conditional waiver {tag}")
    domain.approve_and_send(state, job["id"])
    return inv


class UnconditionalWaiverTiedToItsPayAppTests(unittest.TestCase):  # B1 (r8_two_pending.py)
    def setUp(self):
        self.s = new_state()
        self.job = new_job(self.s, packet="Progress photos")
        self.a = send_one(self.s, self.job, 300_000, "PA-1")
        self.b = send_one(self.s, self.job, 600_000, "PA-2")
        domain.match_payment(self.s, self.job["id"], self.a["amount_cents"], "chk1", invoice_id=self.a["id"])
        domain.match_payment(self.s, self.job["id"], self.b["amount_cents"], "chk2", invoice_id=self.b["id"])
        self.rec = record(self.s, self.job, "waiver", "Lien waiver PA-1 signed")
        domain.set_waiver_type(self.s, self.job["id"], self.rec["id"], "unconditional_progress")

    def test_with_two_paid_pay_apps_an_untied_waiver_asks_which(self):
        for inv in (self.a, self.b):
            stage = domain.waiver_stages(self.s, self.job, inv)["unconditional"]
            self.assertEqual(stage["status"], "Which pay app?", inv["number"])
            self.assertEqual(stage["record_label"], "Lien waiver PA-1 signed")
        sent = len(self.s["email_log"])
        with self.assertRaises(domain.GateError) as raised:
            domain.send_waiver(self.s, self.job["id"], invoice_id=self.b["id"])
        self.assertIn("Lien waiver PA-1 signed", str(raised.exception))
        self.assertIn("for_id", raised.exception.fields)
        self.assertEqual(len(self.s["email_log"]), sent, "nothing goes out while it is unclear")
        self.assertFalse(self.b["waiver_sent"])

    def test_once_tied_it_goes_only_with_its_pay_app(self):
        domain.set_waiver_type(self.s, self.job["id"], self.rec["id"], "unconditional_progress", for_id=self.a["id"])
        self.assertEqual(self.rec["invoice_id"], self.a["id"])
        self.assertTrue(any(r["action"] == "waiver_type" and "PA-PINE-01" in r["detail"] for r in self.s["audit"]))
        stage_a = domain.waiver_stages(self.s, self.job, self.a)["unconditional"]
        self.assertEqual((stage_a["status"], stage_a["record_label"]), ("Attached", "Lien waiver PA-1 signed"))
        self.assertEqual(domain.waiver_stages(self.s, self.job, self.b)["unconditional"]["status"], "Needed")
        with self.assertRaises(domain.GateError):
            domain.send_waiver(self.s, self.job["id"], invoice_id=self.b["id"])
        domain.send_waiver(self.s, self.job["id"], invoice_id=self.a["id"])
        self.assertEqual(self.s["email_log"][-1]["record_id"], self.rec["id"])
        self.assertTrue(self.a["waiver_sent"])

    def test_tying_to_a_pay_app_that_owes_no_waiver_is_refused(self):
        with self.assertRaises(domain.GateError) as raised:
            domain.set_waiver_type(self.s, self.job["id"], self.rec["id"], "unconditional_progress", for_id="inv-nope")
        self.assertIn("for_id", raised.exception.fields)

    def test_with_one_paid_pay_app_it_is_tied_as_it_is_added(self):
        s = new_state()
        job = new_job(s, packet="Progress photos")
        inv = send_one(s, job, 300_000, "PA-1")
        domain.match_payment(s, job["id"], inv["amount_cents"], "chk1")
        rec = record(s, job, "waiver", "Lien waiver signed")
        domain.set_waiver_type(s, job["id"], rec["id"], "unconditional_progress")
        self.assertEqual(rec["invoice_id"], inv["id"])
        self.assertEqual(domain.waiver_stages(s, job, inv)["unconditional"]["record_label"], "Lien waiver signed")

    def test_an_unclear_waiver_stays_unclear_after_one_is_marked_not_needed(self):
        """h12_notneeded_shift.py: marking PA-1's waiver not needed left one owed, and the waiver
        signed for PA-1 quietly became PA-2's. It stays unclear until a person ties it."""
        domain.waiver_not_needed(self.s, self.job["id"], "Handed the PA-1 waiver to Sam on site", invoice_id=self.a["id"])
        self.assertEqual(domain.waiver_stages(self.s, self.job, self.b)["unconditional"]["status"], "Which pay app?")
        sent = len(self.s["email_log"])
        with self.assertRaises(domain.GateError) as raised:
            domain.send_waiver(self.s, self.job["id"], invoice_id=self.b["id"])
        self.assertIn("more than one waiver was due", str(raised.exception))
        self.assertEqual(len(self.s["email_log"]), sent)
        self.assertEqual(domain._waiver_tie(self.rec), "")
        domain.set_waiver_type(self.s, self.job["id"], self.rec["id"], "unconditional_progress")  # retyped: still not tied
        self.assertEqual(domain._waiver_tie(self.rec), "")
        domain.set_waiver_type(self.s, self.job["id"], self.rec["id"], "unconditional_progress", for_id=self.b["id"])
        self.assertEqual(domain.waiver_stages(self.s, self.job, self.b)["unconditional"]["status"], "Attached")
        domain.send_waiver(self.s, self.job["id"], invoice_id=self.b["id"])
        self.assertTrue(self.b["waiver_sent"])

    def test_an_unclear_waiver_stays_unclear_after_the_other_is_sent(self):
        other = record(self.s, self.job, "waiver", "Lien waiver PA-2 signed")
        domain.set_waiver_type(self.s, self.job["id"], other["id"], "unconditional_progress", for_id=self.b["id"])
        domain.send_waiver(self.s, self.job["id"], invoice_id=self.b["id"])
        self.assertEqual(self.s["email_log"][-1]["record_id"], other["id"])
        self.assertEqual(domain.waiver_stages(self.s, self.job, self.a)["unconditional"]["status"], "Which pay app?")


class EarlierPaymentWaiverTests(unittest.TestCase):  # h1_legacy_ambig.py
    """An older job-level waiver (Earlier payment) owed while a pay app owes one too: its signed
    waiver can be said to be for the earlier payment, and then sent."""
    def setUp(self):
        self.s = new_state()
        self.job = new_job(self.s, packet="Progress photos")
        self.job["waiver_due"], self.job["waiver_sent"] = True, False
        self.inv = send_one(self.s, self.job, 300_000, "PA-1")
        domain.match_payment(self.s, self.job["id"], self.inv["amount_cents"], "chk1", invoice_id=self.inv["id"])
        self.rec = record(self.s, self.job, "waiver", "Unconditional waiver for earlier payment signed")
        domain.set_waiver_type(self.s, self.job["id"], self.rec["id"], "unconditional_progress")

    def test_the_choices_name_the_earlier_payment(self):
        with self.assertRaises(domain.GateError) as raised:
            domain.send_waiver(self.s, self.job["id"])
        self.assertEqual([o["number"] for o in raised.exception.extra["waiver_for"]], ["PA-PINE-01", "Earlier payment"])
        self.assertIn(domain.EARLIER_PAYMENT, [o["id"] for o in raised.exception.extra["waiver_for"]])

    def test_tied_to_the_earlier_payment_it_goes_out_for_it_only(self):
        domain.set_waiver_type(self.s, self.job["id"], self.rec["id"], "unconditional_progress", for_id=domain.EARLIER_PAYMENT)
        self.assertEqual(domain._waiver_tie(self.rec), domain.EARLIER_PAYMENT)
        self.assertNotIn("invoice_id", self.rec)
        self.assertEqual(domain.waiver_stages(self.s, self.job, self.inv)["unconditional"]["status"], "Needed")
        view = {"invoices": [], "retainage_waivers": []}
        domain._paperwork_view(self.s, self.job, view)
        self.assertEqual(view["legacy_waiver"]["on_file"], True)
        with self.assertRaises(domain.GateError):
            domain.send_waiver(self.s, self.job["id"], invoice_id=self.inv["id"])
        domain.send_waiver(self.s, self.job["id"])
        self.assertEqual(self.s["email_log"][-1]["record_id"], self.rec["id"])
        self.assertFalse(domain._legacy_waiver_pending(self.job))
        self.assertFalse(self.inv["waiver_sent"])

    def test_the_picker_choice_sends_the_earlier_payment_waiver_too(self):
        domain.set_waiver_type(self.s, self.job["id"], self.rec["id"], "unconditional_progress", for_id=domain.EARLIER_PAYMENT)
        domain.send_waiver(self.s, self.job["id"], invoice_id=domain.EARLIER_PAYMENT)
        self.assertFalse(domain._legacy_waiver_pending(self.job))
        self.assertTrue(self.inv["waiver_due"] and not self.inv["waiver_sent"])


class NoWaiverClientTests(unittest.TestCase):  # B2 (r10_no_waiver_client.py, r11_old_flows.py)
    def test_a_client_who_takes_no_waivers_can_close(self):
        s = new_state()
        job = new_job(s, packet="Progress photos")
        domain.record_progress(s, job["id"], 1_000_000)
        inv = draft(job)
        w = next(p for p in inv["packet"] if p.get("waiver_kind"))
        domain.skip_packet_item(s, job["id"], w["id"], "Homeowner, doesn't take waivers")
        record(s, job, "photo", "Photos")
        domain.approve_and_send(s, job["id"])
        domain.match_payment(s, job["id"], inv["amount_cents"], "chk")
        self.assertFalse(inv["waiver_due"])
        self.assertEqual(inv["waiver_not_needed"]["reason"], "Homeowner, doesn't take waivers")
        self.assertEqual(domain.waiver_stages(s, job, inv)["unconditional"]["status"], "Not needed")
        self.assertNotEqual(domain.status_label(job), "Payment received, waiver not sent")
        domain.mark_done(s, job["id"])
        self.assertNotIn("A lien waiver is still due.", domain.close_blockers(job))
        domain.close_job(s, job["id"])
        self.assertTrue(job["closed"])

    def test_no_waiver_due_on_a_retainage_release_for_that_client(self):
        s = new_state()
        job = new_job(s, retainage=10, packet="Progress photos")
        domain.record_progress(s, job["id"], 1_000_000)
        inv = draft(job)
        domain.skip_packet_item(s, job["id"], next(p for p in inv["packet"] if p.get("waiver_kind"))["id"], "Doesn't take them")
        record(s, job, "photo", "Photos")
        domain.approve_and_send(s, job["id"])
        domain.match_payment(s, job["id"], inv["amount_cents"], "chk")
        domain.record_retainage_payment(s, job["id"], inv["retainage_cents"], "ret")
        release = job["retainage_waivers"][-1]
        self.assertFalse(release["waiver_due"])
        self.assertTrue(release["waiver_not_needed"])
        self.assertFalse(domain._pending_waivers(job))

    def test_handed_over_outside_duracash_is_audited_and_shown(self):
        s = new_state()
        job = new_job(s, packet="Progress photos")
        inv = send_one(s, job, 1_000_000, "PA-1")
        domain.match_payment(s, job["id"], inv["amount_cents"], "chk")
        self.assertTrue(inv["waiver_due"])
        with self.assertRaises(domain.GateError) as raised:
            domain.waiver_not_needed(s, job["id"], "", invoice_id=inv["id"])
        self.assertIn("reason", raised.exception.fields)
        domain.waiver_not_needed(s, job["id"], "Handed the signed form to Sam on site", invoice_id=inv["id"], actor="Owner")
        self.assertFalse(inv["waiver_due"])
        self.assertEqual((inv["waiver_not_needed"]["reason"], inv["waiver_not_needed"]["by"]), ("Handed the signed form to Sam on site", "Owner"))
        stage = domain.waiver_stages(s, job, inv)["unconditional"]
        self.assertEqual((stage["status"], stage["reason"]), ("Not needed", "Handed the signed form to Sam on site"))
        row = next(a for a in s["audit"] if a["action"] == "waiver_not_needed")
        self.assertIn("Handed the signed form to Sam on site", row["detail"])
        self.assertIn("PA-PINE-01", row["detail"])
        domain.mark_done(s, job["id"])
        domain.close_job(s, job["id"])

    def test_an_older_job_level_waiver_can_be_settled_the_same_way(self):
        s = seed_state()
        migrations.migrate(s)
        job = next(j for j in s["jobs"] if j["name"] == "Ponce bath")
        self.assertIn("A lien waiver is still due.", domain.close_blockers(job))
        domain.waiver_not_needed(s, job["id"], "Given to the owner at the walkthrough")
        self.assertFalse(domain._legacy_waiver_pending(job))
        self.assertNotIn("A lien waiver is still due.", domain.close_blockers(job))
        self.assertEqual(job["waiver_not_needed"]["reason"], "Given to the owner at the walkthrough")

    def test_nothing_due_is_refused(self):
        s = new_state()
        job = new_job(s)
        with self.assertRaises(domain.GateError):
            domain.waiver_not_needed(s, job["id"], "No idea")

    def test_the_route_needs_the_waiver_permission(self):
        from domain import profiles
        self.assertEqual(profiles.JOB_ACTION_PERMS["waiver-not-needed"], profiles.JOB_ACTION_PERMS["waiver"])


class FinalWaiverWithRetainageTests(unittest.TestCase):  # B3 (r5_final_ret.py)
    def test_the_final_pay_app_asks_for_a_progress_waiver_while_retainage_is_held(self):
        s = new_state()
        job = new_job(s, retainage=10, packet="Progress photos")
        inv1 = send_one(s, job, 500_000, "PA-1")
        domain.match_payment(s, job["id"], inv1["amount_cents"], "chk1")
        record(s, job, "waiver", "Unconditional waiver PA-1")
        domain.send_waiver(s, job["id"])
        domain.mark_done(s, job["id"])
        inv2 = draft(job)
        self.assertTrue(inv2["final"])
        item = next(i for i in inv2["packet"] if i.get("waiver_kind"))
        self.assertEqual(item["waiver_kind"], "conditional_progress")
        self.assertEqual(item["label"], "Conditional waiver on progress payment")
        record(s, job, "photo", "Photos Oct")
        record(s, job, "waiver", "Signed conditional waiver PA-2")
        domain.approve_and_send(s, job["id"])
        self.assertEqual(inv2["conditional_waiver_kind"], "conditional_progress")
        domain.match_payment(s, job["id"], inv2["amount_cents"], "chk2")
        self.assertEqual(domain.waiver_stages(s, job, inv2)["unconditional"]["kind"], "unconditional_progress")

    def test_without_retainage_the_final_pay_app_still_asks_for_a_final_waiver(self):
        s = new_state()
        job = new_job(s, packet="Progress photos")
        send_one(s, job, 500_000, "PA-1")
        domain.mark_done(s, job["id"])
        item = next(i for i in draft(job)["packet"] if i.get("waiver_kind"))
        self.assertEqual(item["waiver_kind"], "conditional_final")


class DailyRecordKeepsSendChoicesTests(unittest.TestCase):  # B4 (r1_attach_lost.py)
    def test_a_redraft_keeps_records_added_and_files_left_out(self):
        s = new_state()
        job = sov_job(s)
        ids = [l["id"] for l in sov.lines(job)]
        domain.record_sov_progress(s, job["id"], {ids[0]: "100"})
        photo = record(s, job, "photo", "Progress photo", files=2)
        extra = record(s, job, "other", "Site letter")
        inv = draft(job)
        domain.change_payapp_attachments(s, job["id"], inv["id"], "add_record", extra["id"])
        domain.change_payapp_attachments(s, job["id"], inv["id"], "exclude_file", photo["file_ids"][1])
        before = domain._packet_file_ids(s, job, inv)
        domain.add_daily_record(s, job["id"], "Pulled wire", details={"sections": {"quantities": [{"line_id": ids[0], "qty": "50"}]}})
        inv2 = draft(job)
        self.assertIsNot(inv2, inv)
        self.assertEqual(inv2["number"], inv["number"])
        self.assertEqual(domain._packet_file_ids(s, job, inv2), before)
        self.assertEqual(inv2["extra_record_ids"], [extra["id"]])
        self.assertEqual(inv2["excluded_file_ids"], [photo["file_ids"][1]])
        self.assertTrue(any(a["action"] == "payapp_attachments" and "Site letter still added" in a["detail"]
                            and "1 file still left out" in a["detail"] for a in s["audit"]))


class DailyRecordNeverLostTests(unittest.TestCase):  # B5 (r2_daily_blocked.py, r9_ls.py)
    def setUp(self):
        self.s = new_state()
        self.job = sov_job(self.s)
        self.ids = [l["id"] for l in sov.lines(self.job)]

    def saved(self):
        return [r for r in self.s["daily_records"] if r["job_id"] == self.job["id"]]

    def test_saved_while_the_draft_is_with_the_client(self):
        domain.record_sov_progress(self.s, self.job["id"], {self.ids[0]: "100"})
        domain.send_pencil(self.s, self.job["id"])
        before = sov.lines(self.job)[0]["done_milli"]
        domain.add_daily_record(self.s, self.job["id"], "Pulled wire, 3 crew, rain at noon",
                                details={"crew_count": 3, "weather": "Rain", "sections": {"quantities": [{"line_id": self.ids[0], "qty": "50"}]}})
        rec = self.saved()[-1]
        self.assertEqual((rec["notes"], rec["crew_count"], rec["weather"]), ("Pulled wire, 3 crew, rain at noon", 3, "Rain"))
        self.assertEqual(rec["sections"]["quantities"], [{"line_id": self.ids[0], "qty_milli": 50_000}])
        self.assertIn("with the client for their review", rec["quantities_not_applied"])
        self.assertNotIn("pencil", rec["quantities_not_applied"].lower())
        self.assertEqual(sov.lines(self.job)[0]["done_milli"], before, "the quantities were not added")
        note = self.job["records"][-1]["note"]
        self.assertIn("Not added to the schedule of values", note)
        self.assertTrue(any(a["action"] == "daily_quantities_not_applied" for a in self.s["audit"]))

    def test_saved_after_mark_done(self):
        domain.mark_done(self.s, self.job["id"])
        domain.add_daily_record(self.s, self.job["id"], "Cleanup", details={"sections": {"quantities": [{"line_id": self.ids[0], "qty": "5"}]}})
        rec = self.saved()[-1]
        self.assertIn("marked done", rec["quantities_not_applied"])

    def test_a_lump_sum_past_100_percent_is_saved_and_worded_in_percent(self):
        domain.add_daily_record(self.s, self.job["id"], "Panel set", details={"sections": {"quantities": [{"line_id": self.ids[1], "qty": "60"}]}})
        self.assertNotIn("quantities_not_applied", self.saved()[-1])
        domain.add_daily_record(self.s, self.job["id"], "Panel trim, crew of 2",
                                details={"crew_count": 2, "sections": {"quantities": [{"line_id": self.ids[1], "qty": "60"}]}})
        self.assertEqual(len(self.saved()), 2)
        reason = self.saved()[-1]["quantities_not_applied"]
        self.assertIn("120%", reason)
        self.assertIn("100%", reason)
        self.assertNotIn("LS", reason)
        self.assertEqual(sov.lines(self.job)[1]["done_milli"], 600)

    def test_the_job_page_words_a_lump_sum_in_percent_too(self):
        line = sov.lines(self.job)[1]
        self.assertEqual(domain.over_schedule_text(line, 1200), "2. Panel: 120% done is more than 100%. Extra scope is a change order.")


class LiveDraftRedraftsTests(unittest.TestCase):  # B6 (r7_include_co.py)
    def setUp(self):
        self.s = new_state()
        self.job = sov_job(self.s)
        ids = [l["id"] for l in sov.lines(self.job)]
        domain.add_daily_record(self.s, self.job["id"], "Day 1", details={"sections": {"quantities": [{"line_id": ids[0], "qty": "50"}]}})
        self.first = draft(self.job)
        domain.add_change_order(self.s, self.job["id"], "Extra outlets", 120_000, reason=next(iter(domain.CO_REASONS)))
        self.co = self.job["change_orders"][-1]

    def test_bill_now_redrafts_the_draft_with_the_change_order(self):
        domain.include_change_order(self.s, self.job["id"], self.co["id"])
        now = draft(self.job)
        self.assertIsNot(now, self.first)
        self.assertEqual(now["number"], self.first["number"])
        self.assertTrue(any(line.get("co_id") == self.co["id"] for line in now["lines"]))
        self.assertEqual(now["gross_cents"], self.first["gross_cents"] + 120_000)
        self.assertTrue(self.co["included_in_billing"])

    def test_mark_done_turns_the_draft_into_the_final_pay_app(self):
        domain.withdraw_change_order(self.s, self.job["id"], self.co["id"])
        domain.mark_done(self.s, self.job["id"])
        now = draft(self.job)
        self.assertIsNot(now, self.first)
        self.assertEqual(now["number"], self.first["number"])
        self.assertTrue(now["final"])
        self.assertEqual(self.job["work_status"], "complete")

    def test_still_refused_while_the_draft_is_with_the_client(self):
        domain.send_pencil(self.s, self.job["id"])
        with self.assertRaises(domain.GateError):
            domain.include_change_order(self.s, self.job["id"], self.co["id"])
        with self.assertRaises(domain.GateError):
            domain.mark_done(self.s, self.job["id"])


class TicketSkipSticksTests(unittest.TestCase):  # B7 (r12_ticket_skip.py)
    def test_not_needed_for_this_client_is_kept_for_the_next_pay_app(self):
        s = new_state()
        job = sov_job(s, packet="Progress photos")
        ids = [l["id"] for l in sov.lines(job)]
        domain.record_sov_progress(s, job["id"], {ids[0]: "100"})
        inv = draft(job)
        t = next(p for p in inv["packet"] if p["kind"] == "ticket")
        domain.skip_packet_item(s, job["id"], t["id"], "GC doesn't want tickets")
        self.assertEqual(s["payers"][0]["tickets_not_needed"]["reason"], "GC doesn't want tickets")
        record(s, job, "photo", "Photos 1")
        record(s, job, "waiver", "Signed conditional waiver 1")
        domain.approve_and_send(s, job["id"])
        domain.record_sov_progress(s, job["id"], {ids[0]: "300"})
        t2 = next(p for p in draft(job)["packet"] if p["kind"] == "ticket")
        self.assertFalse(t2["required"])
        self.assertEqual(t2["skipped_reason"], "GC doesn't want tickets")


class WaiverListsTests(unittest.TestCase):  # B8 (r4_waiver_lists.py)
    def test_an_unconditional_item_is_unconditional_and_the_conditional_one_is_still_asked(self):
        s = new_state()
        job = new_job(s, packet="Progress photos, Unconditional waiver for previous payment")
        domain.record_progress(s, job["id"], 400_000)
        inv = draft(job)
        kinds = {i["label"]: i.get("waiver_kind") for i in inv["packet"]}
        self.assertEqual(kinds["Unconditional waiver for previous payment"], "unconditional_progress")
        self.assertEqual(kinds["Conditional waiver on progress payment"], "conditional_progress")
        record(s, job, "photo", "Photos Oct")
        record(s, job, "waiver", "Signed conditional waiver PA-1")
        by_label = {i["label"]: i for i in inv["packet"]}
        self.assertEqual([a["name"] for a in by_label["Conditional waiver on progress payment"]["attachments"]], ["Signed conditional waiver PA-1"])
        self.assertFalse(by_label["Unconditional waiver for previous payment"]["attachments"],
                         "a conditional waiver never fills an unconditional item")


class SentPayAppPaperworkStaysTests(unittest.TestCase):  # B9 (r6_attach_sent.py)
    def setUp(self):
        self.s = new_state()
        self.job = new_job(self.s, packet="Progress photos")
        self.inv = send_one(self.s, self.job, 400_000, "PA-1")
        self.item = next(i for i in self.inv["packet"] if i.get("waiver_kind"))
        self.photos = next(i for i in self.inv["packet"] if i["label"] == "Progress photos")

    def test_attach_skip_and_add_are_refused_once_it_went_out(self):
        u = record(self.s, self.job, "waiver", "Unconditional waiver PA-1")
        before = [a["name"] for a in self.item["attachments"]]
        with self.assertRaises(domain.GateError):
            domain.attach_record_to_packet(self.s, self.job["id"], self.item["id"], u["id"])
        with self.assertRaises(domain.GateError):
            domain.skip_packet_item(self.s, self.job["id"], self.photos["id"], "late")
        with self.assertRaises(domain.GateError):
            domain.add_packet_attachment(self.s, self.job["id"], self.photos["id"], "late.pdf")
        self.assertEqual([a["name"] for a in self.item["attachments"]], before)
        self.assertTrue(self.photos["required"])

    def test_closeout_paperwork_still_takes_them(self):
        domain.mark_done(self.s, self.job["id"])
        item = self.job["closeout_packet"][0]
        domain.skip_packet_item(self.s, self.job["id"], item["id"], "Not on this job")
        self.assertFalse(item["required"])


class WaiverWordsTests(unittest.TestCase):  # B-wording
    def test_the_unconditional_progress_email_names_the_retainage_open_to_date(self):
        s = new_state()
        job = new_job(s, retainage=10, packet="Progress photos")
        a = send_one(s, job, 300_000, "PA-1")
        domain.match_payment(s, job["id"], a["amount_cents"], "chk1")
        r1 = record(s, job, "waiver", "Unconditional waiver PA-1")
        domain.set_waiver_type(s, job["id"], r1["id"], "unconditional_progress")
        domain.send_waiver(s, job["id"], invoice_id=a["id"])
        b = send_one(s, job, 600_000, "PA-2")
        domain.match_payment(s, job["id"], b["amount_cents"], "chk2")
        r2 = record(s, job, "waiver", "Unconditional waiver PA-2")
        domain.set_waiver_type(s, job["id"], r2["id"], "unconditional_progress")
        domain.send_waiver(s, job["id"], invoice_id=b["id"])
        body = s["email_log"][-1]["body"]
        self.assertIn("Not released by this waiver: the $600.00 retainage held to date on this job.", body)


class StoredDataTests(unittest.TestCase):  # migrations._review_oct30_money_waivers
    def test_what_is_stored_is_brought_in_line(self):
        s = new_state()
        job = new_job(s, packet="Progress photos")
        inv = send_one(s, job, 300_000, "PA-1")
        domain.match_payment(s, job["id"], inv["amount_cents"], "chk")
        self.assertTrue(inv["waiver_due"])
        s["payers"][0]["waiver_not_needed"] = {"by": "Owner", "at": "2026-10-01T12:00:00+00:00", "reason": "Homeowner"}
        other = new_job(s, retainage=10, packet="Progress photos, Unconditional waiver for previous payment")
        other["payer_id"] = domain.add_payer(s, {"name": "Ellison GC", "role": "GC", "approver": "Kim", "ap_email": "ap@ellison.example",
                                                 "packet": "Progress photos, Unconditional waiver for previous payment"})["id"]
        domain.record_progress(s, other["id"], 300_000)
        d = draft(other)
        # As an older version stored it: no type on the unconditional item and no conditional one.
        d["packet"] = [i for i in d["packet"] if not i.get("auto_waiver")]
        for i in d["packet"]:
            i.pop("waiver_kind", None)
        s["schema_version"] = migrations.STEPS.index(migrations._review_oct30_money_waivers) + 1
        migrations.migrate(s)
        self.assertFalse(inv["waiver_due"])
        self.assertEqual(inv["waiver_not_needed"]["reason"], "Homeowner")
        self.assertNotIn("A lien waiver is still due.", domain.close_blockers(job))
        kinds = {i["label"]: i.get("waiver_kind") for i in d["packet"]}
        self.assertEqual(kinds["Unconditional waiver for previous payment"], "unconditional_progress")
        self.assertEqual(kinds["Conditional waiver on progress payment"], "conditional_progress")
        self.assertEqual(d["status"], "draft")

    def old_draft(self, label="Conditional lien waiver"):
        """A draft as the code on main saved it: its waiver item has no type (h7_migrate_olddraft.py)."""
        s = new_state()
        job = new_job(s, packet="Progress photos")
        domain.record_progress(s, job["id"], 300_000)
        d = draft(job)
        for p in d["packet"]:
            if p.get("waiver_kind"):
                p.pop("waiver_kind")
                p.pop("auto_waiver", None)
                p["label"], p["blocker"] = label, f"{label} is missing."
        record(s, job, "photo", "Photos")
        record(s, job, "waiver", "Signed conditional waiver")
        return s, job, d

    def test_an_old_drafts_waiver_item_is_read_as_the_conditional_one(self):
        for label in ("Conditional lien waiver", "conditional waiver on payment", "Lien waiver"):
            s, job, d = self.old_draft(label)
            self.assertEqual(d["status"], "awaiting_approval", label)
            migrations._review_oct30_money_waivers(s)
            waivers = [p for p in d["packet"] if p["kind"] == "waiver"]
            self.assertEqual(len(waivers), 1, label)
            self.assertEqual(waivers[0]["waiver_kind"], "conditional_progress", label)
            self.assertTrue(waivers[0]["present"], label)
            self.assertEqual(d["status"], "awaiting_approval", label)
            ids = [p["id"] for p in d["packet"]]
            self.assertEqual(len(ids), len(set(ids)), label)

    def test_an_added_conditional_item_never_takes_an_id_already_used(self):
        s, job, d = self.old_draft("Unconditional waiver for previous payment")
        migrations._review_oct30_money_waivers(s)
        ids = [p["id"] for p in d["packet"]]
        self.assertEqual(len(ids), len(set(ids)))
        kinds = sorted(p.get("waiver_kind") for p in d["packet"] if p["kind"] == "waiver")
        self.assertEqual(kinds, ["conditional_progress", "unconditional_progress"])

    def test_a_file_the_earlier_step_doubled_is_repaired(self):
        """What the step did before this fix: a second, empty conditional item with the same id in
        front of the old one, and the draft sent back from awaiting approval to draft."""
        s, job, d = self.old_draft()
        old = next(p for p in d["packet"] if p["kind"] == "waiver")
        extra = domain._packet_item(d["id"], "waiver", "Conditional waiver on progress payment", "Conditional waiver on progress payment is missing.")
        extra.update(waiver_kind="conditional_progress", auto_waiver=True)
        self.assertEqual(extra["id"], old["id"])
        d["packet"].insert(0, extra)
        d["status"] = "draft"
        s["schema_version"] = migrations.STEPS.index(migrations._review_oct30_money_waivers) + 2
        migrations.migrate(s)
        waivers = [p for p in d["packet"] if p["kind"] == "waiver"]
        self.assertEqual(len(waivers), 1)
        self.assertIs(waivers[0], old)
        self.assertEqual(old["waiver_kind"], "conditional_progress")
        self.assertEqual(domain.blocker_lines(job), [])
        self.assertEqual(d["status"], "awaiting_approval")
        found, _ = domain._find_packet_item(job, old["id"])
        self.assertIs(found, old)


if __name__ == "__main__":
    unittest.main()
