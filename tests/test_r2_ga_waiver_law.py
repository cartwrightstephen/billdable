"""Georgia waivers as the law reads from 1 January 2021 (SB 315; georgia-waivers.md, Nov 18): the
statutory form text, a conditional final waiver only with the request for the last money, and a
"Final lien release" nobody typed never taken as a progress waiver."""

import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import doc_check
import approve_help
import domain
import waiver_form
from test_money import clear_packet, new_job, new_state, send_open
from waiver_help import signed_waiver

NOTICE = ("NOTICE: WHEN YOU EXECUTE AND SUBMIT THIS DOCUMENT, YOU SHALL BE CONCLUSIVELY DEEMED TO HAVE WAIVED AND RELEASED ANY AND ALL LIENS "
          "AND CLAIMS OF LIENS UPON THE FOREGOING DESCRIBED PROPERTY AND ANY RIGHTS REGARDING ANY LABOR OR MATERIAL BOND REGARDING THE SAID PROPERTY "
          "TO THE EXTENT (AND ONLY TO THE EXTENT) SET FORTH ABOVE, EVEN IF YOU HAVE NOT ACTUALLY RECEIVED SUCH PAYMENT, 90 DAYS AFTER THE DATE STATED "
          "ABOVE UNLESS YOU FILE AN AFFIDAVIT OF NONPAYMENT PRIOR TO THE EXPIRATION OF SUCH 90 DAY PERIOD. THE FAILURE TO INCLUDE THIS NOTICE LANGUAGE "
          "ON THE FORM SHALL RENDER THE FORM UNENFORCEABLE AND INVALID AS A WAIVER AND RELEASE UNDER O.C.G.A. § 44-14-366.")


def ga():
    return domain.waiver_forms()["states"]["GA"]


class StatutoryText(unittest.TestCase):
    def test_both_forms_end_with_the_2021_notice(self):
        for which in ("interim", "final"):
            paras = ga()["statutory_forms"][which]["paragraphs"]
            self.assertEqual(paras[-1], NOTICE, which)
            text = " ".join(paras)
            self.assertNotIn("60 DAY", text)
            self.assertNotIn("CLAIM OF LIEN", text, "a lien filing no longer stops the waiver")
            self.assertNotIn("ON THE FACE OF THE FORM", text)

    def test_the_signature_block_is_the_statutes(self):
        for which in ("interim", "final"):
            paras = ga()["statutory_forms"][which]["paragraphs"]
            at = next(n for n, p in enumerate(paras) if p.startswith("GIVEN UNDER HAND AND SEAL"))
            self.assertEqual(paras[at + 1:at + 4], ["{signature} (SEAL)", "{witness} (WITNESS)", "{address} (ADDRESS)"], which)
            self.assertFalse([p for p in paras if p.startswith(("BY:", "ITS:", "(NAME OF MECHANIC"))], which)

    def test_the_notes_say_90_days_and_affidavit_only(self):
        notes = " ".join(ga()["notes"])
        self.assertIn("within 90 days", notes)
        self.assertIn("within 7 days", notes)
        self.assertIn("Filing a lien does not stop it.", notes)
        self.assertNotIn("60", notes)

    def test_attorney_questions_are_kept_off_the_page(self):
        self.assertTrue(ga()["attorney"])
        self.assertTrue(all(q.startswith("attorney: ") for q in ga()["attorney"]))
        self.assertNotIn("attorney", waiver_form.forms_for_page()["states"]["GA"])

    def test_signature_witness_and_address_print_blank(self):
        state = new_state()
        state["company"].update(legal_name="Volt Electric LLC")
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        values = waiver_form.prefill(state, job, job["invoices"][-1]["id"], "interim")
        self.assertEqual((values["signature"], values["witness"], values["address"]), ("", "", ""))
        form = ga()["statutory_forms"]["interim"]
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": "GA"}):
            pdf = waiver_form.render(form["paragraphs"], form["title"], values)
        self.assertEqual(pdf.count(b"/Type /Page "), 1, "the notice is on the form's one page")
        self.assertIn(rb"(\(WITNESS\)) Tj", pdf)


class FinalWaiverOnlyForTheLastMoney(unittest.TestCase):
    """The final form gives up the whole job once its sum is paid, retainage included: it goes only
    with the request for everything left."""

    def retainage_job(self, pay=True):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.record_progress(state, job["id"], 1_000_000)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        if pay:
            domain.match_payment(state, job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
        item = None
        for it in job["closeout_packet"]:
            if domain.is_waiver_item(it):
                item = it
            else:
                domain.resolve_packet_item(state, job["id"], it["id"], "Sam", reason="x")
        rec = signed_waiver(state, job["id"], "conditional_final", "Conditional final waiver")
        domain.attach_record_to_packet(state, job["id"], item["id"], rec["id"], "Sam")
        domain.confirm_packet_item(state, job["id"], item["id"], "Sam", answer="conditional")
        return state, job

    def test_it_goes_with_a_request_for_all_the_retainage(self):
        state, job = self.retainage_job()
        domain.chase_retainage(state, job["id"], "Owner")
        self.assertEqual(job["retainage_chases"], 1)

    def test_not_with_a_request_for_part_of_it(self):
        state, job = self.retainage_job()
        with self.assertRaises(domain.GateError) as refused:
            domain.chase_retainage(state, job["id"], "Owner", amount_cents=50_000)
        self.assertEqual(refused.exception.extra["final_waiver_held"], ["$500.00 of retainage is not asked for"])
        self.assertIn("Send a progress waiver with the retainage request", str(refused.exception))
        self.assertFalse(job.get("retainage_chases"))

    def test_not_while_a_pay_app_is_unpaid(self):
        state, job = self.retainage_job(pay=False)
        with self.assertRaises(domain.GateError) as refused:
            domain.chase_retainage(state, job["id"], "Owner")
        self.assertEqual(refused.exception.extra["final_waiver_held"], ["$9,000.00 is still owed"])
        self.assertFalse(job.get("retainage_chases"))

    def test_not_with_a_progress_pay_app(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        inv = clear_packet(state, job)
        rec = signed_waiver(state, job["id"], "conditional_final", "Conditional final waiver")
        domain.change_payapp_attachments(state, job["id"], inv["id"], "add_record", rec["id"])
        domain.answer_waiver_question(state, job["id"], rec["id"], "conditional", "Owner")
        with self.assertRaises(domain.GateError) as refused:
            approve_help.approve_and_send(state, job["id"], "Owner")
        self.assertIn("the job isn't finished", refused.exception.extra["final_waiver_held"])
        self.assertIn(inv["status"], domain.DRAFT_STATUSES)
        self.assertEqual(state["email_log"], [])

    def test_a_conditional_progress_waiver_still_goes(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        inv = clear_packet(state, job)
        rec = signed_waiver(state, job["id"], "conditional_progress", "Conditional waiver")
        domain.change_payapp_attachments(state, job["id"], inv["id"], "add_record", rec["id"])
        domain.answer_waiver_question(state, job["id"], rec["id"], "conditional", "Owner")
        approve_help.approve_and_send(state, job["id"], "Owner")
        self.assertEqual(inv["status"], "sent")


class ItemTurnsProgress(unittest.TestCase):
    """A form typed final only by the answer on the final pay app's item is the progress waiver once
    the item turns progress (more work billed, say); one that says final itself stays final."""

    def test_retyped_with_the_item(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 1_000_000)
        domain.mark_done(state, job["id"])
        inv = [i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES][-1]
        item = next(it for it in inv["packet"] if domain.is_waiver_item(it))
        self.assertEqual(item["waiver_kind"], "conditional_final")
        plain = signed_waiver(state, job["id"], None, "Signed lien waiver")
        named = signed_waiver(state, job["id"], "conditional_final", "Conditional final waiver")
        plain["waiver_kind"] = "conditional_final"   # as the answer on the final item typed it
        item["attachments"] = [{"name": r["label"], "record_id": r["id"]} for r in (plain, named)]
        job["work_status"] = "in_progress"
        domain._name_conditional_waiver(job, inv)
        self.assertEqual(item["waiver_kind"], "conditional_progress")
        self.assertEqual(plain["waiver_kind"], "conditional_progress")
        self.assertEqual(named["waiver_kind"], "conditional_final")


class UntypedFinalRelease(unittest.TestCase):
    """A "Final lien release" that says neither conditional nor unconditional."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.record_progress(self.state, self.job["id"], 400_000)

    def test_its_label_says_final_and_it_has_no_type(self):
        rec = signed_waiver(self.state, self.job["id"], None, "Final lien release")
        self.assertEqual(domain.record_waiver_kind(rec), "")
        self.assertTrue(domain.record_says_final(rec))
        self.assertFalse(domain.record_says_final({"label": "Interim waiver before final payment"}))
        self.assertFalse(domain.record_says_final({"label": "Lien waiver"}))

    def test_the_answer_on_a_progress_pay_app_types_it_final(self):
        inv = clear_packet(self.state, self.job)
        rec = signed_waiver(self.state, self.job["id"], None, "Final lien release")
        domain.change_payapp_attachments(self.state, self.job["id"], inv["id"], "add_record", rec["id"])
        self.assertIn(rec, domain.waiver_questions(self.job, inv["packet"], inv["extra_record_ids"]))
        domain.answer_waiver_question(self.state, self.job["id"], rec["id"], "unconditional", "Owner")
        self.assertEqual(rec["waiver_kind"], "unconditional_final")
        with self.assertRaises(domain.GateError) as refused:
            approve_help.approve_and_send(self.state, self.job["id"], "Owner", unconditional_override="GC asked")
        self.assertTrue(refused.exception.extra["final_waiver_held"])

    def test_files_that_read_final_count_too(self):
        rec = signed_waiver(self.state, self.job["id"], None, "Signed lien release")
        rec["waiver_read"] = {"guess": "", "form": True, "both": False, "final": True, "file_ids": list(rec["file_ids"])}
        self.assertTrue(domain.record_says_final(rec))
        self.assertEqual(domain._answer_stage(rec, "progress"), "final")

    def test_it_is_never_sent_as_a_progress_waiver(self):
        inv = send_open(self.state, self.job)
        domain.match_payment(self.state, self.job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
        rec = signed_waiver(self.state, self.job["id"], None, "Final lien release")
        with self.assertRaises(domain.GateError) as refused:
            domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=inv["id"])
        self.assertIn("Which waiver is this?", str(refused.exception))
        self.assertEqual(refused.exception.extra["waiver_record_id"], rec["id"])
        self.assertFalse(inv.get("waiver_sent"))
        domain.set_waiver_type(self.state, self.job["id"], rec["id"], "unconditional_progress", "Sam")
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=inv["id"])
        self.assertTrue(inv["waiver_sent"])

    def test_it_never_fills_a_progress_waiver_item(self):
        inv = [i for i in self.job["invoices"] if i["status"] in domain.DRAFT_STATUSES][-1]
        item = next(it for it in inv["packet"] if domain.is_waiver_item(it))
        rec = signed_waiver(self.state, self.job["id"], None, "Final lien release")
        self.assertFalse(domain._fits_waiver_item(rec, item, inv))
        self.assertTrue(domain._fits_waiver_item({**rec, "label": "Lien waiver"}, item, inv))


class ReadingFinal(unittest.TestCase):
    def test_a_final_heading_or_the_statutory_final_title_reads_final(self):
        self.assertTrue(doc_check.reading("FINAL LIEN RELEASE\nThe undersigned releases all liens.")["final"])
        self.assertTrue(doc_check.reading("Acme\n" + ga()["statutory_forms"]["final"]["title"].upper())["final"])
        self.assertFalse(doc_check.reading("Acme\n" + ga()["statutory_forms"]["interim"]["title"].upper())["final"])
        self.assertFalse(doc_check.reading("INTERIM LIEN WAIVER BEFORE FINAL PAYMENT\nThe undersigned waives.")["final"])
        self.assertFalse(doc_check.reading("Final grade invoice\nTotal $4,000")["final"])

    def test_waiver_reading_carries_it(self):
        got = doc_check.waiver_reading([("a.txt", ".txt", b"FINAL LIEN RELEASE\nThe undersigned releases all liens.")])
        self.assertTrue(got["final"])
        self.assertEqual(re.sub(r"\s", "", str(sorted(got))), str(sorted(["both", "final", "form", "guess"])).replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
