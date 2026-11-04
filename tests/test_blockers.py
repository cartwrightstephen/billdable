"""What clears a pay app's supporting document (Stephen's Nov 1 notes, Decisions 5, 6 and 9): a real
file, filed to the item by a person's choice or by kind, never by its title, and a named person's
check. I have this is an override with a reason. A mismatch warns and never blocks for good."""

import copy
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import doc_check
import domain
import payapp_pdf
import payer_page
from test_packet import new_state, new_job, record, draft
from waiver_help import confirm_suggested


def pdf(text):
    """A one-stream text PDF, as contract_import reads them."""
    body = "BT /F1 12 Tf 72 720 Td (" + text + ") Tj ET"
    return f"%PDF-1.4\n1 0 obj\n<< /Length {len(body)} >>\nstream\n{body}\nendstream\nendobj\n".encode()


def docx(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


GOOD = "Conditional lien waiver for Pine kitchen in the amount of $4,000.00 upon payment"


class Case(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Invoice.")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = draft(self.job)
        self.waiver = next(p for p in self.inv["packet"] if p["kind"] == "waiver")
        self.data = {}

    def read(self, meta):
        return self.data.get(meta["id"])

    def stored(self, label, data, name="file.pdf", kind="waiver", typed="conditional_progress"):
        """A record with a stored file whose bytes the check can read, typed as the item's Choose file does."""
        rec = record(self.state, self.job, kind, label)
        if typed:
            rec["waiver_kind"] = typed
        meta = self.state["files"][-1]
        meta.update(name=name, ext=Path(name).suffix, size=len(data))
        self.data[meta["id"]] = data
        return rec

    def attach(self, rec, looked=False, item=None):
        return domain.attach_record_to_packet(self.state, self.job["id"], (item or self.waiver)["id"], rec["id"], "Sam",
                                              read_file=self.read, looked=looked)


class StephensReportTests(Case):
    def test_a_blank_file_with_any_title_does_not_clear_the_waiver(self):
        for title in ("asdf", "Signed conditional lien waiver"):
            with self.subTest(title):
                self.setUp()
                rec = self.stored(title, b"%PDF-1.4\n")
                self.assertFalse(self.waiver["present"], "filing it clears nothing")
                self.attach(rec)
                self.assertFalse(self.waiver["present"], "a person attaching it still gets the warning")
                self.assertIn("looks blank", " ".join(self.waiver["check"]["problems"]))
                self.assertIn(f"Conditional waiver on progress payment: look at {title} and confirm it.", domain.blocker_lines(self.job))
                self.assertEqual(self.inv["status"], "draft")
                with self.assertRaises(domain.GateError):
                    domain.approve_and_send(self.state, self.job["id"], "Sam")

    def test_the_title_no_longer_matters(self):
        good = self.stored("zzz", pdf(GOOD))
        self.attach(good)
        self.assertTrue(self.waiver["present"], "an odd title on the right document clears it")
        self.setUp()
        wrong = self.stored("Signed conditional lien waiver Pine kitchen $4,000.00", pdf("A lunch menu with soup and salad"))
        self.attach(wrong)
        self.assertFalse(self.waiver["present"], "a perfect title on the wrong document does not")

    def test_a_list_item_is_not_made_here_because_its_label_says_pay_app(self):
        state = new_state()
        job = new_job(state, packet="Lien waiver for pay application, Pay application, Schedule of values, AIA-style pay app.")
        domain.record_progress(state, job["id"], 400_000)
        items = {p["label"]: p for p in draft(job)["packet"]}
        self.assertFalse(items["Lien waiver for pay application"]["present"])
        for label in ("Pay application", "Schedule of values", "AIA-style pay app"):
            self.assertEqual(items[label]["present_reason"], domain.MADE_HERE_REASON, label)


class AcceptanceTests(Case):  # acceptance.md 66-70, 74, 76, 80
    def test_a_flag_is_not_cleared_on_the_first_try_even_when_it_says_looked(self):
        rec = self.stored("w", pdf("Lien waiver for Oak street in the amount of $9.00"))
        self.attach(rec, looked=True)
        self.assertFalse(self.waiver["present"], "the warning has to be shown first")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", read_file=self.read)
        self.assertFalse(self.waiver["present"], "the normal confirm doesn't clear a flag")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
        self.assertTrue(self.waiver["present"])

    def test_interim_and_final_wording_is_read(self):
        for kind, text, clean in (("conditional_progress", "Interim Waiver and Release Pine kitchen $4,000.00", True),
                                  ("conditional_final", "Waiver and Release Upon Final Payment Pine kitchen $4,000.00", True),
                                  ("conditional_progress", "Waiver and Release Final Pine kitchen $4,000.00", False),
                                  ("conditional_final", "Interim Waiver and Release Pine kitchen $4,000.00", False),
                                  ("unconditional_progress", "Interim Waiver and Release Pine kitchen $4,000.00", True)):
            check = doc_check.check([("w.pdf", ".pdf", pdf(text))], "Pine kitchen", "$4,000.00", 400_000, kind, True)
            self.assertEqual(check["status"] == "matched", clean, (kind, text, check))

    def test_a_waiver_with_no_type_is_asked_about_and_never_stamped(self):
        rec = self.stored("waiver.pdf", pdf(GOOD), typed="")
        self.attach(rec)
        self.assertIn("doesn't say its type", " ".join(self.waiver["check"]["problems"]))
        self.assertFalse(self.waiver["present"])
        self.assertNotIn("waiver_kind", rec)
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
        self.assertEqual(rec["waiver_kind"], "conditional_progress", "the person's word for it")

    def test_an_untyped_waiver_is_not_stamped_at_send(self):
        rec = self.stored("waiver.pdf", pdf(GOOD), typed="")
        confirm_suggested(self.state, self.job)  # no reader: as a draft from before these rules
        domain.resolve_packet_item(self.state, self.job["id"], next(p for p in self.inv["packet"] if p["label"] == "Invoice")["id"], reason="x")
        domain.approve_and_send(self.state, self.job["id"], "Sam")
        self.assertNotIn("waiver_kind", rec)

    def test_an_unconditional_waiver_filed_as_other_still_warns(self):
        rec = self.stored("Unconditional lien waiver", pdf(GOOD), kind="other", typed="")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", rec["id"])
        self.assertEqual(domain.unconditional_waivers_attached(self.job, self.inv), [rec])

    def test_a_new_amount_asks_for_another_look(self):
        self.attach(self.stored("Waiver", pdf(GOOD)))
        item = next(p for p in self.inv["packet"] if p["label"] == "Invoice")
        domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="In the PDF")
        domain.record_progress(self.state, self.job["id"], 500_000)
        new = draft(self.job)
        self.assertNotEqual(new["id"], self.inv["id"])
        waiver = next(p for p in new["packet"] if p["kind"] == "waiver")
        self.assertFalse(waiver["present"])
        self.assertNotIn("confirmed_by", waiver)
        self.assertIn("The amount changed from $4,000.00 to $5,000.00", waiver["check"]["problems"][0])
        kept = next(p for p in new["packet"] if p["label"] == "Invoice")
        self.assertEqual(kept["override"]["reason"], "In the PDF")

    def test_an_override_prints_no_tick_for_the_client(self):
        for item in self.inv["packet"]:
            domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="with PM on site")
        domain.approve_and_send(self.state, self.job["id"], "Sam")
        body = self.state["email_log"][-1]["body"]
        self.assertNotIn("[x]", body)
        self.assertNotIn("accounted for", body)
        self.assertIn("Not attached: Conditional waiver on progress payment", body)
        self.assertNotIn("✓", payer_page.render(self.state, self.job, self.inv, "/p/x").split("<h2>Supporting documents</h2>")[1].split("</div>")[0])

    def test_a_final_waiver_while_retainage_is_held_needs_a_confirm(self):
        state = new_state()
        job = new_job(state, retainage=10, packet="Invoice.")
        domain.record_progress(state, job["id"], 500_000)
        inv = draft(job)
        for item in inv["packet"]:
            domain.resolve_packet_item(state, job["id"], item["id"], "Sam", reason="x")
        domain.approve_and_send(state, job["id"], "Sam")
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk")
        from waiver_help import signed_waiver
        signed_waiver(state, job["id"], "unconditional_final", "Final waiver")
        sent = len(state["email_log"])
        with self.assertRaises(domain.GateError) as raised:
            domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"], kind="unconditional_final")
        self.assertTrue(raised.exception.extra["final_waiver_warning"])
        self.assertEqual(len(state["email_log"]), sent)
        domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"], kind="unconditional_final", confirm_final=True)
        self.assertEqual(len(state["email_log"]), sent + 1)

    def test_not_needed_on_a_waiver_is_this_job_only_unless_said(self):  # accept. 10
        state = new_state()
        a = new_job(state, packet="Invoice, conditional waiver.")
        b = domain.create_job(state, "Oak porch", "Jones Family", 1_000_000, address="2 Oak St, Atlanta, GA 30307", approver="Sam", ap_email="ap@jones.example")
        domain.record_progress(state, a["id"], 400_000)
        item = next(p for p in draft(a)["packet"] if p.get("waiver_kind"))
        domain.skip_packet_item(state, a["id"], item["id"], "", "Sam")
        self.assertEqual((item["required"], item["skipped_by"], item["skipped_scope"]), (False, "Sam", "job"))
        self.assertIn("Sam marked", state["audit"][-1]["detail"])
        self.assertEqual(state["audit"][-1]["action"], "paperwork_not_needed")
        payer = state["payers"][0]
        self.assertNotIn("waiver_not_needed", payer)
        self.assertIn("conditional waiver", payer["packet"].lower(), "the playbook still asks")
        domain.record_progress(state, b["id"], 400_000)
        other = next(p for p in draft(b)["packet"] if p.get("waiver_kind"))
        self.assertTrue(other["required"], "job B still needs it")
        domain.skip_packet_item(state, b["id"], other["id"], "Client doesn't take them", "Sam", whole_client=True)
        self.assertEqual(payer["waiver_not_needed"]["by"], "Sam")

    def test_a_partial_payment_owes_no_waiver(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        domain.record_progress(state, job["id"], 500_000)
        inv = draft(job)
        for item in inv["packet"]:
            domain.resolve_packet_item(state, job["id"], item["id"], "Sam", reason="x")
        domain.approve_and_send(state, job["id"], "Sam")
        domain.match_payment(state, job["id"], inv["amount_cents"] - 1, "chk")
        with self.assertRaises(domain.GateError):
            domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"])

    def test_send_from_an_old_review_asks_for_a_fresh_one(self):
        self.attach(self.stored("Waiver", pdf(GOOD)))
        item = next(p for p in self.inv["packet"] if p["label"] == "Invoice")
        domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="In the PDF")
        stamp = domain.review_stamp(self.state, self.job, self.inv)
        extra = self.stored("Lumber receipt", pdf("x" * 20), kind="other", typed="")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", extra["id"])
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(self.state, self.job["id"], "Sam", reviewed=stamp)
        self.assertTrue(raised.exception.extra["review_changed"])
        self.assertFalse(self.state["email_log"])
        domain.approve_and_send(self.state, self.job["id"], "Sam", reviewed=domain.review_stamp(self.state, self.job, self.inv))
        self.assertEqual(self.inv["status"], "sent")

    def test_a_lump_sum_schedule_is_no_backup(self):
        state = new_state()
        job = new_job(state, packet="Pay app backup.")
        domain.save_sov(state, job["id"], [{"item": "1", "description": "Contract", "unit": "LS", "quantity": "1", "rate": "10000.00"}], "0")
        domain.record_sov_progress(state, job["id"], {job["sov"]["lines"][0]["id"]: "1"})
        item = next(p for p in draft(job)["packet"] if p["label"] == "Pay app backup")
        self.assertFalse(item["present"])
        self.assertNotIn("Daily tickets and time sheets is missing.", domain.blocker_lines(job))


class RealFileTests(Case):
    def test_a_nameless_attach_is_refused(self):
        for name in ("", "lien waiver.pdf"):
            with self.assertRaises(domain.GateError) as raised:
                domain.add_packet_attachment(self.state, self.job["id"], self.waiver["id"], name, "Sam")
            self.assertIn("file_id", raised.exception.fields)
        self.assertFalse(self.waiver["present"])
        self.assertEqual(self.waiver["attachments"], [])

    def test_an_attach_with_a_stored_file_goes_through_the_record(self):
        self.state["files"].append({"id": "f9", "name": "w.pdf", "ext": ".pdf", "size": len(pdf(GOOD))})
        self.data["f9"] = pdf(GOOD)
        domain.add_packet_attachment(self.state, self.job["id"], self.waiver["id"], "", "Sam", file_id="f9", read_file=self.read)
        self.assertIn("doesn't say its type", self.waiver["check"]["problems"][0], "a new record has no type yet")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
        self.assertTrue(self.waiver["present"])
        self.assertEqual(self.job["records"][-1]["file_ids"], ["f9"])

    def test_a_record_with_no_file_cannot_fill_a_document_item(self):
        domain.add_job_record(self.state, self.job["id"], "Waiver is in the truck", "waiver")
        self.assertEqual(self.waiver["attachments"], [], "not even suggested")
        with self.assertRaises(domain.GateError) as raised:
            domain.attach_record_to_packet(self.state, self.job["id"], self.waiver["id"], self.job["records"][-1]["id"], "Sam")
        self.assertIn("has no file", str(raised.exception))

    def test_a_record_with_typed_fields_and_no_file_is_not_suggested(self):  # from ov-records
        state = new_state()
        job = new_job(state, packet="Daily tickets and time sheets.")
        domain.record_progress(state, job["id"], 400_000)
        item = next(p for p in draft(job)["packet"] if p["kind"] != "waiver")
        for ticket_type in ("delivery", "time_sheet"):
            domain.add_job_record(state, job["id"], f"Typed {ticket_type}", "ticket", "12 loads, 8 hours", "Sam")
            job["records"][-1]["ticket_type"] = ticket_type
            domain._refresh_draft_gate(job, state)
            self.assertEqual(item["attachments"], [], "typed fields aren't a document")
            with self.assertRaises(domain.GateError):
                domain.attach_record_to_packet(state, job["id"], item["id"], job["records"][-1]["id"], "Sam")
        for ticket_type in ("delivery", "time_sheet"):  # with a file, either type fills the ticket item
            rec = record(state, job, "ticket", f"Signed {ticket_type}")
            rec["ticket_type"] = ticket_type
            domain.attach_record_to_packet(state, job["id"], item["id"], rec["id"], "Sam")
            self.assertTrue(item["present"], ticket_type)
            domain.detach_packet_item(state, job["id"], item["id"])

    def test_an_item_that_asks_for_a_note_takes_one(self):
        state = new_state()
        job = new_job(state, packet="Foreman notes.")
        domain.record_progress(state, job["id"], 400_000)
        item = next(p for p in draft(job)["packet"] if p["label"] == "Foreman notes")
        domain.add_job_record(state, job["id"], "Week 3", "note")
        domain.attach_record_to_packet(state, job["id"], item["id"], job["records"][-1]["id"], "Sam")
        self.assertTrue(item["present"])

    def test_an_empty_file_or_a_spreadsheet_is_not_a_waiver(self):
        for name, size in (("w.pdf", 0), ("w.xlsx", 50)):
            with self.subTest(name):
                self.setUp()
                rec = self.stored("w", b"x")
                self.state["files"][-1].update(name=name, ext=Path(name).suffix, size=size)
                with self.assertRaises(domain.GateError):
                    self.attach(rec)
                self.assertFalse(self.waiver["present"])


class ConfirmTests(Case):
    def test_a_suggestion_waits_for_a_named_person_and_is_not_sent(self):
        rec = self.stored("Waiver from the field", pdf(GOOD))
        self.assertFalse(self.waiver["present"])
        self.assertEqual([(a["record_id"], a["auto"], a["unconfirmed"]) for a in self.waiver["attachments"]], [(rec["id"], True, True)])
        self.assertEqual(domain._packet_file_ids(self.state, self.job, self.inv), [], "not in the email until confirmed")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", read_file=self.read)
        self.assertTrue(self.waiver["present"])
        self.assertEqual(self.waiver["confirmed_by"], "Sam")
        self.assertTrue(self.waiver["confirmed_at"])
        self.assertEqual(domain._packet_file_ids(self.state, self.job, self.inv), rec["file_ids"])
        row = next(a for a in self.state["audit"] if a["action"] == "packet_confirmed")
        self.assertIn("Sam checked", row["detail"])

    def test_attaching_is_the_one_step(self):
        self.attach(self.stored("Waiver", pdf(GOOD)))
        self.assertTrue(self.waiver["present"])
        self.assertEqual(self.waiver["confirmed_by"], "Sam")
        self.assertEqual(self.waiver["check"], {"status": "matched", "problems": []})

    def test_a_suggestion_is_not_made_twice_and_a_newer_record_takes_over(self):
        self.stored("One", pdf(GOOD))
        domain._refresh_draft_gate(self.job, self.state)
        self.assertEqual(len(self.waiver["attachments"]), 1)
        newer = self.stored("Two", pdf(GOOD))
        self.assertEqual([a["record_id"] for a in self.waiver["attachments"]], [newer["id"]])

    def test_a_confirm_with_nothing_attached_is_refused(self):
        with self.assertRaises(domain.GateError):
            domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam")


class OverrideTests(Case):
    def test_it_needs_a_reason_and_keeps_who_when_why(self):
        for blank in ("", "   "):
            with self.assertRaises(domain.GateError) as raised:
                domain.resolve_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", reason=blank)
            self.assertIn("reason", raised.exception.fields)
        self.assertFalse(self.waiver["present"])
        domain.resolve_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", reason="Original at the GC's office")
        self.assertTrue(self.waiver["present"])
        self.assertEqual((self.waiver["override"]["by"], self.waiver["override"]["reason"]), ("Sam", "Original at the GC's office"))
        self.assertTrue(self.waiver["override"]["at"])
        self.assertIn("Sam overrode", self.state["audit"][-1]["detail"])
        self.assertIn("Original at the GC's office", self.state["audit"][-1]["detail"])
        view = domain.job_view(self.state, self.job)
        self.assertEqual(view["invoices"][-1]["packet"][self.inv["packet"].index(self.waiver)]["override"]["by"], "Sam")

    def test_it_does_not_reach_the_client(self):
        reason = "Original at the GC office"
        for item in self.inv["packet"]:
            domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason=reason)
        domain.approve_and_send(self.state, self.job["id"], "Sam")
        self.assertNotIn(reason, self.state["email_log"][-1]["body"])
        self.assertNotIn(reason, payer_page.render(self.state, self.job, self.inv, "/p/x"))
        self.assertNotIn(reason.encode("latin-1"), payapp_pdf.for_invoice(self.state, self.job["id"], self.inv["id"])[0])

    def test_it_takes_off_what_nobody_confirmed(self):
        rec = self.stored("Maybe the waiver", pdf(GOOD))
        domain.resolve_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", reason="Have the signed one")
        self.assertEqual(self.waiver["attachments"], [])
        self.assertEqual(domain._packet_file_ids(self.state, self.job, self.inv), [])
        self.assertIn(rec["id"], self.waiver["detached_record_ids"])

    def test_not_needed_stays_its_own_audited_path(self):
        domain.skip_packet_item(self.state, self.job["id"], self.waiver["id"], "They don't take waivers", "Sam")
        self.assertNotIn("override", self.waiver)
        self.assertFalse(self.waiver["required"])
        self.assertEqual(self.state["audit"][-1]["action"], "paperwork_not_needed")


class DocumentCheckTests(Case):
    def problems(self, data, name="w.pdf"):
        rec = self.stored("w", data, name)
        self.attach(rec)
        return " ".join(self.waiver["check"]["problems"])

    def test_a_mismatch_names_what_did_not_match_and_a_look_clears_it(self):
        words = self.problems(pdf("Lien waiver for Oak street in the amount of $9.00"))
        self.assertIn("Job name not found: Pine kitchen.", words)
        self.assertIn("Amount not found: $4,000.00.", words)
        self.assertIn("Conditional wording not found.", words)
        self.assertFalse(self.waiver["present"])
        self.assertEqual(self.inv["status"], "draft")
        domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
        self.assertTrue(self.waiver["present"], "never a hard block")
        self.assertEqual(self.waiver["confirmed_by"], "Sam")
        self.assertIn("after the warning", self.state["audit"][-1]["detail"] + self.state["audit"][-2]["detail"])

    def test_unconditional_wording_on_a_conditional_item(self):
        self.assertIn("It says unconditional", self.problems(pdf("Unconditional waiver Pine kitchen $4,000.00")))

    def test_word_files_are_read_too(self):
        self.attach(self.stored("w", docx(GOOD), "w.docx"))
        self.assertTrue(self.waiver["present"])

    def test_the_georgia_wording_counts_as_conditional(self):
        self.attach(self.stored("w", pdf("Interim Waiver and Release Upon Payment, Pine kitchen, $4,000.00")))
        self.assertTrue(self.waiver["present"])

    def test_a_scan_says_it_could_not_be_checked(self):
        for data, name in ((b"%PDF-1.4 /Subtype /Image stream", "s.pdf"), (b"\xff\xd8 jpeg", "s.jpg")):
            with self.subTest(name):
                self.setUp()
                words = self.problems(data, name)
                self.assertIn("couldn't be checked", words)
                self.assertEqual(self.waiver["check"]["status"], "unchecked")
                self.assertFalse(self.waiver["present"])
                domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", looked=True, read_file=self.read)
                self.assertTrue(self.waiver["present"])

    def test_a_file_gone_from_the_disk_warns(self):
        rec = self.stored("w", pdf(GOOD))
        self.data.clear()
        self.attach(rec)
        self.assertIn("couldn't be opened", self.waiver["check"]["problems"][0])
        self.assertFalse(self.waiver["present"])

    def test_another_document_is_compared_with_the_job_name_only(self):
        state = new_state()
        job = new_job(state, packet="Signed contract.")
        domain.record_progress(state, job["id"], 400_000)
        check = doc_check.check([("c.pdf", ".pdf", pdf("Contract for the Pine kitchen job, no amount here"))], job["name"])
        self.assertEqual(check, {"status": "matched", "problems": []})
        check = doc_check.check([("c.pdf", ".pdf", pdf("Contract for a different job altogether"))], job["name"])
        self.assertEqual(check["problems"], ["Job name not found: Pine kitchen."])

    def test_progress_photos_have_nothing_to_compare(self):
        state = new_state()
        job = new_job(state, packet="Progress photos.")
        domain.record_progress(state, job["id"], 400_000)
        item = next(p for p in draft(job)["packet"] if p["kind"] != "waiver")
        rec = record(state, job, "photo", "Porch")
        domain.attach_record_to_packet(state, job["id"], item["id"], rec["id"], "Sam", read_file=lambda m: b"\xff\xd8")
        self.assertTrue(item["present"])
        self.assertNotIn("check", item)


class ExistingDataTests(unittest.TestCase):
    def test_an_item_already_in_on_a_draft_stays_in(self):
        """Marked present before these rules, with no file, or attached automatically: no re-block."""
        state = new_state()
        job = new_job(state, packet="Invoice.")
        domain.record_progress(state, job["id"], 400_000)
        inv = draft(job)
        rec = record(state, job, "waiver", "Old auto")
        for item in inv["packet"]:
            item.update(present=True, resolved_at="2026-10-01T00:00:00+00:00")
            for a in item.get("attachments", []):
                a.pop("unconfirmed", None)
        item = inv["packet"][0]
        item.update(marked_in_hand=True, present_reason="old reason")
        domain._refresh_draft_gate(job, state)
        self.assertEqual(domain.blocker_lines(job), [])
        self.assertEqual(inv["status"], "awaiting_approval")
        self.assertTrue(all(p["present"] for p in inv["packet"]))
        domain.approve_and_send(state, job["id"], "Sam")
        self.assertIn(rec["file_ids"][0], state["email_log"][-1]["attachments"], "an old automatic attachment still goes")

    def test_a_sent_pay_app_does_not_change(self):
        state = new_state()
        job = new_job(state, packet="Invoice.")
        domain.record_progress(state, job["id"], 400_000)
        inv = draft(job)
        rec = record(state, job, "waiver", "Waiver PA-1")
        confirm_suggested(state, job)
        for item in inv["packet"]:
            if not item["present"]:
                domain.resolve_packet_item(state, job["id"], item["id"], reason="test")
        domain.approve_and_send(state, job["id"], "Sam")
        before = copy.deepcopy(inv["packet"])
        item = next(p for p in inv["packet"] if p["kind"] == "waiver")
        for call in (lambda: domain.resolve_packet_item(state, job["id"], item["id"], "Sam", reason="late"),
                     lambda: domain.confirm_packet_item(state, job["id"], item["id"], "Sam", looked=True),
                     lambda: domain.attach_record_to_packet(state, job["id"], item["id"], rec["id"], "Sam"),
                     lambda: domain.detach_packet_item(state, job["id"], item["id"]),
                     lambda: domain.skip_packet_item(state, job["id"], item["id"], "x", "Sam")):
            with self.assertRaises(domain.GateError) as raised:
                call()
            self.assertIn("has gone to the client", str(raised.exception))
        record(state, job, "waiver", "A later waiver")
        domain._attach_from_record(state, job, inv["packet"], "x")
        self.assertEqual(inv["packet"], before)


class ReviewTests(Case):
    def test_the_email_preview_is_the_email_sent(self):
        self.attach(self.stored("Waiver", pdf(GOOD)))
        item = next(p for p in self.inv["packet"] if p["label"] == "Invoice")
        domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="In the PDF")
        before = copy.deepcopy(self.inv)
        view = next(i for i in domain.job_view(self.state, self.job)["invoices"] if i["id"] == self.inv["id"])
        self.assertEqual(self.inv, before, "showing it changes nothing")
        preview = view["email_preview"]
        domain.approve_and_send(self.state, self.job["id"], "Sam")
        mail = self.state["email_log"][-1]
        self.assertEqual(preview["subject"], mail["subject"])
        self.assertEqual(preview["body"].replace(domain.PREVIEW_SENDER, "Sam").replace("\n\n" + "See this pay app and its supporting documents, and tell us it reached you:\n" + domain.PREVIEW_LINK, ""), mail["body"])
        after = next(i for i in domain.job_view(self.state, self.job)["invoices"] if i["id"] == self.inv["id"])
        self.assertNotIn("email_preview", after)


if __name__ == "__main__":
    unittest.main()
