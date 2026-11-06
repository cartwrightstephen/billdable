"""The final review of Nov 5: what goes to the owner's gate, what a refund's pay-back owes, the card
and a blank box agreeing, and the smaller items, each through the code a person reaches."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import alerts
import doc_check
import domain
import mailer
import monthly
import server
import sov
import storage
from domain import profiles
from test_hunt_nov5_http import Base
from test_money import clear_packet, new_job, new_state, send_open
from waiver_help import signed_waiver

BODY = "\nThe undersigned has been paid and hereby waives and releases its lien and payment bond rights. Elm addition %s\nSigned ____\n"
LETTERHEAD = "Acme Electric, LLC\n123 Main Street\nAtlanta, GA 30303\n(404) 555-0100\nwww.acme-electric.com\n"
REAL_ONES = ("CONTRACTOR'S UNCONDITIONAL FINAL LIEN WAIVER", "WAIVER AND RELEASE ON PROGRESS PAYMENT - UNCONDITIONAL",
             LETTERHEAD + "UNCONDITIONAL WAIVER AND RELEASE", "CONDITIONAL WAIVER AND RELEASE\nl\nl\nl\nl\nl\nUNCONDITIONAL WAIVER AND RELEASE ON FINAL PAYMENT",
             "WAIVER AND RELEASE - UNCONDITIONAL", "RELEASE OF LIEN - FINAL - UNCONDITIONAL", "Subcontractor Unconditional Waiver", "UNCONDITIONAL SUBCONTRACTOR LIEN WAIVER",
             "State of Georgia County of Fulton Unconditional Waiver and Release", "Conditional Builders LLC\nUNCONDITIONAL WAIVER AND RELEASE",
             "Pay application #4\nCover page\nJob: Pine\nDate: Nov 5\nAmount: $50,000\nAttached: lien waiver\nUNCONDITIONAL WAIVER AND RELEASE",
             "A\nB\nC\nD\nE\nF\nG\nH\nI\nUNCONDITIONAL WAIVER AND RELEASE", "CONDITIONAL WAIVER AND RELEASE\n\n\n" + "pad\n" * 30 + "UNCONDITIONAL WAIVER AND RELEASE",
             "UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT", "WAIVER AND RELEASE UPON FINAL PAYMENT (UNCONDITIONAL)", "PROGRESS PAYMENT LIEN WAIVER: UNCONDITIONAL",
             "Georgia Statutory Unconditional Lien Waiver", "UNCONDITIONAL AFFIDAVIT AND WAIVER OF LIEN", "UNCONDITIONAL LIEN WAIVER AND AFFIDAVIT", "Partial Waiver of Lien (Unconditional Upon Receipt)",
             "Unconditional Waiver and Release Conditional Builders LLC", "Form: Conditional and Unconditional Waivers\nUNCONDITIONAL WAIVER AND RELEASE", "UNCONDITIONAL WAIVER: received in full",
             "UN-CONDITIONAL LIEN WAIVER", "U N C O N D I T I O N A L  L I E N  W A I V E R", "UNCONDI-\nTIONAL LIEN WAIVER", "UNCONDITIONAL\nLIEN WAIVER", "Exhibit B - Unconditional Waiver",
             "Not a conditional waiver. Unconditional lien waiver", "Waiver of lien. Upon receipt the undersigned waives. This is an unconditional waiver and release.")
ONLY_FILED_AS_A_WAIVER = ("NOTICE: THIS DOCUMENT WAIVES RIGHTS UNCONDITIONALLY AND STATES THAT YOU HAVE BEEN PAID.\nWAIVER AND RELEASE ON PROGRESS PAYMENT",
                          "Lien Waiver\nThis waiver is unconditional and effective immediately.", "Lien Waiver\nThis release is unconditional.",
                          "Lien Waiver\nThe undersigned gives this unconditional release of all lien rights.", "Lien Waiver\nThis waiver is hereby made unconditional.", "UNCONDITIONAL")
FORWARD = ("Upon receipt of payment the undersigned will deliver an unconditional waiver and release.", "This waiver becomes unconditional on receipt of payment.",
           "Do not sign an unconditional waiver: if you have not been paid, use a conditional waiver form for this payment.",
           "The contractor shall furnish an unconditional lien waiver after each payment clears at the bank.")


def reading(text, near_only=False):
    return doc_check.unconditional_reading([("f.txt", ".txt", text.encode())], near_only)


class TheOwnersGate(unittest.TestCase):
    def test_filed_as_a_waiver_a_real_one_is_the_gate_wherever_its_title_is(self):
        for title in REAL_ONES + ONLY_FILED_AS_A_WAIVER:
            self.assertTrue(reading(title + BODY % "$5,000.00")["waiver"], title)

    def test_not_filed_as_a_waiver_a_heading_or_a_phrase_is_the_gate(self):
        for title in REAL_ONES:
            self.assertTrue(reading(title + BODY % "$5,000.00", near_only=True)["waiver"], title)

    def test_filed_as_a_waiver_only_the_forward_looking_sentences_are_a_look(self):
        for sentence in FORWARD:
            for near_only in (False, True):
                got = reading("CONDITIONAL WAIVER AND RELEASE\nElm addition\n" + sentence, near_only)
                self.assertFalse(got["waiver"], sentence)
                self.assertTrue(got["mention"], sentence)

    def test_the_forward_sentence_doesnt_hide_the_title(self):
        self.assertTrue(reading("UNCONDITIONAL WAIVER AND RELEASE\nThis waiver becomes unconditional on receipt of payment.")["waiver"])
        self.assertTrue(reading("This waiver becomes unconditional on receipt of payment.\nl\nl\nUNCONDITIONAL WAIVER AND RELEASE")["waiver"])

    def test_a_sentence_about_one_is_a_look_wherever_it_is_filed(self):
        # Nov 6 review, A: a line reporting one received is a look, filed as a waiver too.
        text = "Sub's unconditional lien waiver received from plumber for last month, and the crew moved on to the next floor."
        for near_only in (False, True):
            got = reading(text, near_only=near_only)
            self.assertEqual((got["waiver"], got["mention"]), (False, True))

    def test_taken_back_stays_neither(self):
        got = reading("Conditional waiver and release. This is not an unconditional release.")
        self.assertEqual((got["waiver"], got["mention"]), (False, False))

    def test_more_forward_sentences_than_are_read_is_the_gate(self):
        text = "Conditional waiver.\n" + "The contractor will deliver an unconditional waiver.\n" * (doc_check.MAX_MATCHES + 1) + "UNCONDITIONAL WAIVER AND RELEASE"
        self.assertTrue(reading(text)["waiver"])


class ThroughTheServer(Base):
    def setUp(self):
        super().setUp()
        self.member_perms(["record_payments", "send_payapps", "send_waivers"])
        line = self.job()["sov"]["lines"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        self.assertEqual(status, 200, data)

    def member_perms(self, perms):
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            state["company"]["profiles"] = [dict(p, perms=perms) if p["id"] == "member" else p for p in profiles.profiles(state)]
            server.save_company(self.company, state)

    def waiver_item(self):
        return next(p for p in self.draft()["packet"] if p.get("waiver_kind"))

    def attach(self, text):
        item = self.waiver_item()
        res, data = self.upload("w.txt", (text % domain.money(self.draft()["amount_cents"])).encode(), attach=item["id"], kind="waiver")
        self.assertEqual(res.status, 200, data)
        return data["uploaded_record_ids"][0], item

    def clear_the_rest(self):
        for item in self.draft()["packet"]:
            if item["required"] and not item["present"]:
                status, data = self.post(f"/api/jobs/{self.job_id}/resolve/{item['id']}", {"reason": "In hand"})
                self.assertEqual(status, 200, data)

    def send(self, cookie=None, **body):
        return self.post(f"/api/jobs/{self.job_id}/send", {"reviewed": self.draft()["email_preview"]["stamp"], **body}, cookie=cookie)

    def record(self, rid):
        return next(r for r in self.job()["records"] if r["id"] == rid)

    def test_a_member_cant_send_a_real_one_with_a_look(self):
        for title in ("CONTRACTOR'S UNCONDITIONAL FINAL LIEN WAIVER\n", LETTERHEAD + "UNCONDITIONAL WAIVER AND RELEASE\n"):
            with self.subTest(title):
                rid, item = self.attach(title + "Elm addition %s\n")
                self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": [rid]}, cookie=self.bob)
                self.assertEqual(self.record(rid).get("waiver_kind", ""), "", "a look doesn't retype it")
                self.assertIn(rid, [r["record_id"] for r in self.draft()["unconditional_attached"]])
                self.clear_the_rest()
                status, data = self.send(cookie=self.bob)
                self.assertNotEqual(status, 200, data)
                self.assertFalse(any(a["action"] == "unconditional_mention_confirmed" for a in server.load_company(self.company)["audit"]))
                self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "remove_record", "record_id": rid})
                self.post(f"/api/jobs/{self.job_id}/detach/{item['id']}", {})

    def test_a_confirm_without_send_permission_leaves_the_mention(self):
        self.member_perms(["record_payments"])
        rid, item = self.attach("CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\nElm addition %s. Upon receipt the undersigned will deliver an unconditional waiver and release.\n")
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": [rid]}, cookie=self.bob)
        self.assertEqual(status, 200, data)
        self.assertNotIn("unconditional_looked", self.record(rid))
        self.assertIn(rid, [r["record_id"] for r in self.draft()["unconditional_mentions"]])
        shown = self.draft()["unconditional_mentions"][0]["file_ids"]
        self.assertEqual(self.post(f"/api/jobs/{self.job_id}/unconditional-look/{rid}", {"shown": shown}, cookie=self.bob)[0], 403)
        status, data = self.post(f"/api/jobs/{self.job_id}/unconditional-look/{rid}", {"shown": shown})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.draft()["unconditional_mentions"], [], "someone who can send clears it")

    def test_a_confirm_by_someone_who_can_send_clears_the_mention(self):
        rid, item = self.attach("CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\nElm addition %s. Upon receipt the undersigned will deliver an unconditional waiver and release.\n")
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": [rid]}, cookie=self.bob)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.draft()["unconditional_mentions"], [])

    def test_a_look_counts_only_for_the_files_on_screen(self):
        res, data = self.upload("log.txt", b"Sub's unconditional lien waiver received from plumber.", kind="note")
        self.assertEqual(res.status, 200, data)
        rid = data["uploaded_record_ids"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "add_record", "record_id": rid})
        self.assertEqual(status, 200, data)
        shown = self.draft()["unconditional_mentions"][0]["file_ids"]
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            rec = next(r for j in state["jobs"] for r in j["records"] if r["id"] == rid)
            state["files"].append({"id": "file-new", "name": "other.txt", "ext": ".txt", "size": 10, "stored": "gone"})
            rec["file_ids"].append("file-new")
            server.save_company(self.company, state)
        for body in ({"shown": shown}, {}):
            status, data = self.post(f"/api/jobs/{self.job_id}/unconditional-look/{rid}", body)
            self.assertEqual(status, 409, data)
            self.assertNotIn("unconditional_looked", self.record(rid))
        status, data = self.post(f"/api/jobs/{self.job_id}/unconditional-look/{rid}", {"shown": shown + ["file-new"]})
        self.assertEqual(status, 200, data)
        self.assertEqual(sorted(self.record(rid)["unconditional_looked"]["file_ids"]), sorted(shown + ["file-new"]))


def signed_co(state, job, cents):
    domain.add_change_order(state, job["id"], "Change", cents)
    domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])


class PayingBackARefund(unittest.TestCase):
    def test_paid_back_while_the_contract_has_room_to_bill(self):
        s = new_state(); j = new_job(s, contract=10_000_000); i = j["id"]
        domain.record_progress(s, i, 5_000_000); clear_packet(s, j); domain.approve_and_send(s, i, "Owner"); inv = j["invoices"][-1]
        domain.match_payment(s, i, 5_000_000, "c", invoice_id=inv["id"])
        signed_co(s, j, -6_000_000); domain.settle_overbilling(s, i, "Refund")
        signed_co(s, j, 6_000_000)
        m = domain.job_money(j)
        self.assertEqual((m["refund_owed_cents"], m["outstanding_cents"]), (1_000_000, 1_000_000))
        domain.match_payment(s, i, 1_000_000, "back", invoice_id=inv["id"])
        m = domain.job_money(j)
        self.assertEqual((m["refund_owed_cents"], m["outstanding_cents"], m["collected_cents"]), (0, 0, 5_000_000))
        with self.assertRaises(domain.GateError):
            domain.match_payment(s, i, 1_000_000, "again", invoice_id=inv["id"])

    def test_part_of_a_raise_is_the_refund_and_the_rest_is_unbilled(self):
        s = new_state(); j = new_job(s, contract=10_000_000); i = j["id"]
        domain.mark_done(s, i); inv = send_open(s, j); domain.match_payment(s, i, 10_000_000, "c", invoice_id=inv["id"])
        signed_co(s, j, -1_000_000); domain.settle_overbilling(s, i, "Refund")
        signed_co(s, j, 1_500_000)
        domain.match_payment(s, i, 1_000_000, "back", invoice_id=inv["id"])
        m = domain.job_money(j)
        self.assertEqual((m["refund_owed_cents"], m["outstanding_cents"], m["collected_cents"]), (0, 0, 10_000_000))
        with self.assertRaises(domain.GateError):
            domain.match_payment(s, i, 500_000, "more", invoice_id=inv["id"])

    def test_a_credit_on_a_short_pay_doesnt_hide_a_later_real_overpayment(self):
        s = new_state(); j = new_job(s, contract=10_000_000); i = j["id"]
        domain.mark_done(s, i); inv = send_open(s, j); domain.match_payment(s, i, 9_000_000, "c", invoice_id=inv["id"])
        signed_co(s, j, -1_000_000); domain.settle_short_pay(s, i, inv["id"], "Credit"); domain.settle_overbilling(s, i, "Credit CO")
        self.assertEqual(domain.job_money(j)["overbilled_cents"], 0)
        domain.match_payment(s, i, 1_000_000, "late", invoice_id=inv["id"])
        m = domain.job_money(j)
        self.assertEqual((m["overbilled_cents"], m["collected_cents"]), (1_000_000, 10_000_000))
        self.assertIn("overbilled", [b["kind"] for b in domain.close_blocker_items(j)])
        domain.settle_overbilling(s, i, "Refund check")
        m = domain.job_money(j)
        self.assertEqual((m["overbilled_cents"], m["collected_cents"]), (0, 9_000_000))


class TheCardAndABlankBox(unittest.TestCase):
    def test_a_blank_box_bills_what_the_card_said_when_stored_sits_on_a_line_billed_ahead(self):
        s = new_state(); j = new_job(s, contract=1, retainage=0); i = j["id"]
        domain.save_sov(s, i, [{"item": "1", "description": "Panel", "quantity": "1", "unit": "LS", "rate": "10000"}], "0", update_contract=True)
        line = sov.lines(j)[0]
        domain.add_daily_record(s, i, "Work", details={"sections": {"quantities": [{"line_id": line["id"], "qty": "10%"}]}})
        domain.record_sov_progress(s, i, {line["id"]: "0.507"})
        clear_packet(s, j); domain.approve_and_send(s, i, "Owner")
        domain.record_sov_progress(s, i, {}, stored={line["id"]: "1000"})
        draft = j["invoices"][-1]
        domain.void_draft(s, i, "Sam")
        want = domain._sov_view(j, s)["period"]["suggested_gross_cents"]
        self.assertEqual(want, 100_000, "the stored materials, with no work taken off them")
        domain.record_sov_progress(s, i, {})
        self.assertEqual(j["invoices"][-1]["gross_cents"], want)


class AnAmountTypedAhead(unittest.TestCase):
    def test_the_next_sheets_previous_column_is_what_the_last_one_showed(self):
        s = new_state(); j = new_job(s, contract=1, retainage=0); i = j["id"]
        domain.save_sov(s, i, [{"item": "1", "description": "Panel", "quantity": "1", "unit": "LS", "rate": "10000"}], "0", update_contract=True)
        line = sov.lines(j)[0]
        domain.record_sov_progress(s, i, {line["id"]: "0.245"})
        domain.set_payapp_amounts(s, i, {line["id"]: "2450.81"})   # 81 cents past the quantity
        clear_packet(s, j); domain.approve_and_send(s, i, "Owner")
        domain.record_sov_progress(s, i, {}, stored={line["id"]: "1000"})
        domain.set_payapp_amounts(s, i, {line["id"]: "1519.61"})   # more than the stored materials suggested
        entry = j["invoices"][-1]["sov_sheet"][0]
        cols = sov.sheet_columns(entry)
        self.assertEqual(cols["previous"], 245_081, "what the last sheet showed as work to date")
        self.assertEqual(cols["previous"] + cols["this_period"] + cols["stored"], cols["to_date"])
        self.assertEqual((cols["stored"], cols["this_period"]), (100_000, 51_961))


class RetainageAndPullingBack(unittest.TestCase):
    def test_the_way_out_of_overbilling_doesnt_offer_a_pull_back_that_is_refused(self):
        s = new_state(); j = new_job(s, retainage=10); i = j["id"]
        domain.mark_done(s, i); inv = send_open(s, j)
        domain.record_retainage_payment(s, i, inv["retainage_cents"], "Check")
        signed_co(s, j, -10_000)
        words = domain._overbilling_way_out(j, domain.job_money(j))
        self.assertNotIn("pull back", words.lower())
        self.assertIn("change order", words.lower())

    def test_a_pay_app_whose_retainage_was_paid_cant_be_pulled_back(self):
        s = new_state(); j = new_job(s, retainage=10); i = j["id"]
        domain.mark_done(s, i); inv = send_open(s, j)
        domain.record_retainage_payment(s, i, inv["retainage_cents"], "Check")
        with self.assertRaises(domain.GateError) as refused:
            domain.void_sent_pay_app(s, i, inv["id"], "Wrong")
        self.assertIn("retainage", str(refused.exception).lower())
        self.assertEqual(inv["status"], "sent")


class CreditOffRetainageAndTheFinalWaiver(unittest.TestCase):
    def setUp(self):
        self.s = new_state(); self.j = new_job(self.s, retainage=10); self.i = self.j["id"]
        domain.mark_done(self.s, self.i); self.inv = send_open(self.s, self.j)
        domain.match_payment(self.s, self.i, self.inv["amount_cents"], "c", invoice_id=self.inv["id"])
        kind = domain.suggested_waiver_kind(self.j, self.inv)
        self.assertEqual(kind, "unconditional_progress")
        signed_waiver(self.s, self.i, kind)["invoice_id"] = self.inv["id"]
        domain.send_waiver(self.s, self.i, "Sam", invoice_id=self.inv["id"])
        signed_co(self.s, self.j, -1_000_000)

    def test_a_credit_that_finishes_the_job_owes_the_final_waiver(self):
        domain.apply_credit_to_retainage(self.s, self.i, "CO 1", "Sam")
        self.assertIn("waiver", [b["kind"] for b in domain.close_blocker_items(self.j)])
        with self.assertRaises(domain.GateError):
            domain.close_job(self.s, self.i, "Sam")
        release = self.j["retainage_waivers"][-1]
        self.assertEqual(release["kind"], "unconditional_final")
        self.assertTrue(release["waiver_due"])
        signed_waiver(self.s, self.i, "unconditional_final")
        domain.send_waiver(self.s, self.i, "Sam", release_id=release["id"])
        domain.close_job(self.s, self.i, "Sam")
        self.assertTrue(self.j["closed"])

    def test_a_final_waiver_already_sent_isnt_owed_twice(self):
        self.inv["waiver_kind"] = "unconditional_final"
        domain.apply_credit_to_retainage(self.s, self.i, "CO 1", "Sam")
        self.assertEqual(self.j.get("retainage_waivers") or [], [])

    def test_a_credit_for_part_of_it_owes_nothing_yet(self):
        domain.apply_credit_to_retainage(self.s, self.i, "CO 1", "Sam", amount_cents=500_000)
        self.assertEqual(self.j.get("retainage_waivers") or [], [])

    def test_a_client_that_doesnt_take_waivers_owes_none(self):
        self.s["payers"][0]["waiver_not_needed"] = {"by": "Ann", "at": "2026-11-01", "reason": "Their rule"}
        domain.apply_credit_to_retainage(self.s, self.i, "CO 1", "Sam")
        self.assertNotIn("waiver", [b["kind"] for b in domain.close_blocker_items(self.j)])


class OlderJobLevelPackets(unittest.TestCase):
    def test_a_skip_without_the_permission_leaves_the_clients_list(self):
        s = new_state(); j = new_job(s); i = j["id"]; domain.record_progress(s, i, 5_000_000)
        inv = j["invoices"][-1]
        j["packet"] = inv.pop("packet"); inv["packet_id"] = "job-packet"
        payer = s["payers"][0]
        payer["packet"] = ", ".join(p["label"] for p in j["packet"]) + "."
        was = payer["packet"]
        item = next(p for p in j["packet"] if not str(p.get("waiver_kind", "")).startswith("conditional"))
        domain.skip_packet_item(s, i, item["id"], "x", "Bob", on_client=False)
        self.assertEqual(payer["packet"], was)
        self.assertFalse(item["required"])


class Site:
    def __init__(self):
        self.sent = []

    def company_ids(self): return ["c1"]
    def deleted(self): return set()
    def app_url(self): return "http://x"
    def read(self, company_id): return {"company": {}}

    def send(self, to, subject, body, company_id):
        self.sent.append(to)
        return "sent"


class ADailyFileThatCantBeRead(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(storage, "DATA_DIR", Path(tmp.name)); patch.start(); self.addCleanup(patch.stop)
        self.path = Path(tmp.name) / monthly.DAILY_PATH_NAME

    def run_it(self):
        site = Site()
        when = monthly.datetime(2026, 11, 5, 20, tzinfo=monthly.timezone.utc)
        with mock.patch.object(mailer, "configured", lambda: True), mock.patch.object(monthly, "daily_email_hour", lambda: 0), \
                mock.patch.object(monthly, "_digests", lambda *a: [("a@c1.example", ("s", "b"))]), mock.patch.object(alerts, "notify") as notify:
            lines = monthly.send_daily_digests(site, when)
        return site, lines, notify

    def test_each_bad_shape_alerts_keeps_a_copy_and_sends_nothing(self):
        for bad in ("[]", "3", "null", '"x"', '{"c1": "2026-11', '{"sending": []}', '{"sending": {"c1": 4}}'):
            with self.subTest(bad):
                self.path.write_text(bad)
                site, lines, notify = self.run_it()
                self.assertEqual((site.sent, lines), ([], []))
                notify.assert_called()
                self.assertEqual(self.path.read_text(), bad, "nothing is written over it")
                self.assertTrue(list(self.path.parent.glob(self.path.name + ".bad-*")))

    def test_no_file_yet_is_the_first_day(self):
        site, lines, notify = self.run_it()
        self.assertEqual(site.sent, ["a@c1.example"])
        notify.assert_not_called()
        self.assertEqual(self.run_it()[0].sent, [], "and once a day")


if __name__ == "__main__":
    unittest.main()
