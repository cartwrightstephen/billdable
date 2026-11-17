"""The last gaps in the waiver redesign (Round 1 hunt, r1c): a lien waiver filed under another kind
whose title is not read as a heading is still asked about; a waiver answer is set by someone who can
send, and an unconditional one is not taken back by a bare confirm; and the smaller gaps around
them. The text only sets the default and a question; a person's answer carries the decision."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import doc_check
import domain
import server
import sov
import unconditional_corpus as c
from test_blockers import GOOD, Case, pdf
from test_hunt_nov5_http import PASSWORD, Base
from test_money import send_open
from test_packet import draft, new_job, new_state
from waiver_help import signed_waiver

# Who is answering, as the server knows them from their profile.
OWNER = domain.Answerer(send_payapps=True, ask_retainage=True, owner=True, id="u-pat")
SENDER = domain.Answerer(send_payapps=True, ask_retainage=False, owner=False, id="u-sam")
OTHER_SENDER = domain.Answerer(send_payapps=True, ask_retainage=False, owner=False, id="u-lee")
PLAIN = domain.Answerer(send_payapps=False, ask_retainage=False, owner=False, id="u-mo")
RETAINER = domain.Answerer(send_payapps=False, ask_retainage=True, owner=False, id="u-ret")
UNC = "UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT Pine kitchen $4,000.00 The undersigned has been paid."


class WaiverAnywhereReadsAsAForm(unittest.TestCase):
    """A title is not the only place a lien waiver says what it is."""

    def test_every_real_unconditional_waiver_reads_as_a_form_wherever_its_title_is(self):
        missed = [name[:70] for name, text in c.TITLES_ANYWHERE.items() if not doc_check.reading(text)["form"]]
        self.assertEqual(missed, [])

    def test_the_guess_is_unconditional_when_the_text_says_so(self):
        for name, text in c.TITLES_ANYWHERE.items():
            if name in ("Partial Release of Lien", "line 12: Partial Release of Lien", "no title, only the words"):
                continue   # says neither word
            with self.subTest(name[:70]):
                self.assertEqual(doc_check.reading(text)["guess"], "unconditional")

    def test_insurance_waivers_are_no_lien_waiver(self):
        for name, text in c.INSURANCE.items():
            with self.subTest(name):
                self.assertFalse(doc_check.reading(text)["form"])

    def test_a_lien_waiver_that_also_talks_about_subrogation_is_still_one(self):
        text = "WAIVER OF LIEN\nElm addition\nThe undersigned waives lien rights and, by waiver of subrogation, any right to recover from the owner.\n"
        self.assertTrue(doc_check.reading(text)["form"])

    def test_a_job_with_tickets_invoices_a_coi_and_a_contract_asks_at_most_once(self):
        asked = [name for name, text in {**c.REALISTIC, "contract": c.CONTRACT}.items() if doc_check.reading(text)["form"]]
        self.assertLessEqual(len(asked), 1, asked)
        self.assertEqual([n for n in asked if n != "contract"], [])

    def test_a_long_file_is_read_in_a_blink(self):
        import time
        big = ("waives " + "x " * 30) * 6000 + ("not a b c d e f " * 5000)
        start = time.time()
        doc_check.reading(big[:contract_limit()])
        self.assertLess(time.time() - start, 2.0)


def contract_limit():
    import contract_import
    return contract_import.MAX_TEXT


class FiledUnderAnotherKind(Case):
    """The route that was open: add_record of a record filed as other, a note, insurance or a PDF
    named scan-0042.pdf, then send. Now it is asked about, then the owner."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, packet="Insurance certificate.")
        domain.record_progress(self.state, self.job["id"], 400_000)
        self.inv = draft(self.job)
        self.waiver = next(p for p in self.inv["packet"] if p["kind"] == "waiver")
        self.cert = next(p for p in self.inv["packet"] if p["kind"].startswith("insurance"))
        self.data = {}
        self.attach(self.stored("Our waiver", pdf(GOOD)))
        self.assertTrue(self.waiver["present"])
        domain.resolve_packet_item(self.state, self.job["id"], self.cert["id"], "Sam", reason="In hand")

    def send(self, **kw):
        return domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read, **kw)

    def test_each_kind_and_a_scan_named_file_asks_then_takes_the_owner(self):
        title = c.LONG_TITLES[2]   # "Acme Electric - Unconditional Waiver and Release of Lien - Elm addition - Pay App 3": 14 words
        for kind, name in (("other", "w.txt"), ("note", "w.txt"), ("insurance", "w.txt"), ("other", "scan-0042.pdf")):
            with self.subTest(kind=kind, name=name):
                self.setUp()
                text = (title + c.LONG_BODY).replace("\n", " ") if name.endswith("pdf") else title + c.LONG_BODY
                data = pdf(text.replace("(", "").replace(")", "")) if name.endswith("pdf") else text.encode()
                rec = self.stored("Paperwork", data, name, kind=kind, typed="")
                domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", rec["id"], "Sam", read_file=self.read)
                with self.assertRaises(domain.GateError) as asked:
                    self.send()
                self.assertTrue(asked.exception.extra.get("waiver_question"), "no file leaves without a person's answer")
                self.assertEqual(domain.waiver_guess(rec), "unconditional")
                domain.answer_waiver_question(self.state, self.job["id"], rec["id"], "unconditional", "Sam", shown=rec["file_ids"])
                with self.assertRaises(domain.GateError) as refused:
                    self.send()
                self.assertEqual(str(refused.exception), domain.UNCONDITIONAL_ATTACHED)
                self.send(unconditional_override="Paid by wire, owner checked")
                self.assertEqual(self.inv["status"], "sent")

    def test_a_letterhead_then_the_title_is_asked_about(self):
        rec = self.stored("Paperwork", (c.LETTERHEAD + "UNCONDITIONAL LIEN RELEASE" + c.LONG_BODY).encode(), "w.txt", kind="other", typed="")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", rec["id"], "Sam", read_file=self.read)
        self.assertEqual(domain.waiver_questions(self.job, self.inv["packet"], self.inv["extra_record_ids"], (), self.state, self.read), [rec])

    def test_a_certificate_with_a_waiver_of_subrogation_goes_with_no_question(self):
        rec = self.stored("COI", c.INSURANCE["ACORD"].encode(), "coi.txt", kind="insurance", typed="")
        domain.detach_packet_item(self.state, self.job["id"], self.cert["id"], "Sam")
        self.attach(rec, item=self.cert)
        if not self.cert["present"]:
            domain.confirm_packet_item(self.state, self.job["id"], self.cert["id"], "Sam", looked=True, read_file=self.read, shown=[rec["id"]])
        self.send()
        self.assertEqual(self.inv["status"], "sent")


class WhoSetsAnAnswer(Case):
    """Fix 2: setting or changing a waiver answer, by any route, takes send_payapps. Files and
    records from anyone still work; they wait for an answer."""

    def setUp(self):
        super().setUp()
        self.rec = self.stored("Signed waiver", pdf(UNC), typed="")

    def attach_by(self, who, answer):
        """As the page does: attach (the warning is shown), then attach again with the look."""
        for _ in range(2):
            domain.attach_record_to_packet(self.state, self.job["id"], self.waiver["id"], self.rec["id"], who.id, read_file=self.read, answer=answer,
                                           looked=True, answerer=who)

    def test_attaching_with_an_answer_without_send_payapps_attaches_but_waits(self):
        self.attach_by(PLAIN, "conditional")
        self.assertEqual([a["record_id"] for a in self.waiver["attachments"]], [self.rec["id"]])
        self.assertFalse(self.waiver["present"], "nobody who can send has said which waiver it is")
        self.assertNotIn("waiver_answer", self.rec)
        self.attach_by(SENDER, "conditional")
        self.assertTrue(self.waiver["present"])
        self.assertEqual(self.rec["waiver_answer"]["by"], "u-sam")

    def test_confirming_with_an_answer_without_send_payapps_is_refused_and_says_who_can(self):
        self.attach_by(PLAIN, "")
        with self.assertRaises(domain.GateError) as refused:
            domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Mo", looked=True, read_file=self.read, shown=[self.rec["id"]],
                                       answer="conditional", answerer=PLAIN)
        self.assertIn("can send pay apps", str(refused.exception))
        self.assertFalse(self.waiver["present"])
        with self.assertRaises(domain.GateError):   # a bare confirm has no answer to give
            domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], "Mo", looked=True, read_file=self.read, shown=[self.rec["id"]], answerer=PLAIN)

    def test_the_question_on_another_file_is_answered_by_someone_who_can_send(self):
        other = self.stored("Paperwork", pdf(UNC), kind="other", typed="")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", other["id"], "Mo", read_file=self.read)
        with self.assertRaises(domain.GateError):
            domain.answer_waiver_question(self.state, self.job["id"], other["id"], "no", "Mo", shown=other["file_ids"], answerer=PLAIN)
        self.assertNotIn("waiver_answer", other)
        domain.answer_waiver_question(self.state, self.job["id"], other["id"], "no", "Sam", shown=other["file_ids"], answerer=SENDER)
        self.assertEqual(other["waiver_answer"]["kind"], "no")

    def test_the_type_chosen_on_the_record_is_not_an_answer_without_send_payapps(self):
        domain.set_waiver_type(self.state, self.job["id"], self.rec["id"], "conditional_progress", "Mo", answerer=PLAIN)
        self.assertEqual(self.rec["waiver_kind"], "conditional_progress")
        self.assertNotIn("waiver_answer", self.rec, "the type alone answers nothing")
        self.attach_by(SENDER, "")
        self.assertFalse(self.waiver["present"], "still waiting for an answer")
        domain.set_waiver_type(self.state, self.job["id"], self.rec["id"], "conditional_progress", "Sam", answerer=SENDER)
        self.assertEqual(self.rec["waiver_answer"]["kind"], "conditional", "someone who can send said it")
        # An answer that is there stays: a type change by someone who can't send is refused.
        with self.assertRaises(domain.GateError):
            domain.set_waiver_type(self.state, self.job["id"], self.rec["id"], "unconditional_progress", "Mo", answerer=PLAIN)
        self.assertEqual(self.rec["waiver_answer"]["kind"], "conditional")

    def test_an_unconditional_answer_is_taken_back_only_by_who_gave_it_or_an_owner_and_it_is_audited(self):
        for route in ("attach", "confirm", "type", "question"):
            with self.subTest(route):
                self.setUp()
                self.attach_by(SENDER, "unconditional")
                self.assertEqual(self.rec["waiver_answer"]["kind"], "unconditional")

                def back(who, name="X"):
                    if route == "attach":
                        return domain.attach_record_to_packet(self.state, self.job["id"], self.waiver["id"], self.rec["id"], name, read_file=self.read, answer="conditional", looked=True, answerer=who)
                        return domain.attach_record_to_packet(self.state, self.job["id"], self.waiver["id"], self.rec["id"], name, read_file=self.read, answer="conditional", looked=True, answerer=who)
                    if route == "confirm":
                        self.waiver["attachments"][0]["unconfirmed"] = True
                        self.waiver["present"] = False
                        return domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], name, looked=True, read_file=self.read,
                                                          shown=[self.rec["id"]], answer="conditional", answerer=who)
                    if route == "type":
                        return domain.set_waiver_type(self.state, self.job["id"], self.rec["id"], "conditional_progress", name, answerer=who)
                    return domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "conditional", name, shown=self.rec["file_ids"], answerer=who)

                with self.assertRaises(domain.GateError) as refused:
                    back(OTHER_SENDER, "Lee")
                self.assertIn("owner", str(refused.exception))
                self.assertEqual(self.rec["waiver_answer"]["kind"], "unconditional", "the answer stands")
                with self.assertRaises(domain.GateError):   # to "not a lien waiver" too
                    domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "no", "Lee", shown=self.rec["file_ids"], answerer=OTHER_SENDER)
                before = len(self.state["audit"])
                back(SENDER, "Sam")   # the person who gave it
                self.assertEqual(self.rec["waiver_answer"]["kind"], "conditional")
                self.assertTrue([a for a in self.state["audit"][before:] if "changed" in a["detail"] and "unconditional" in a["detail"]], "the change is audited")
                self.setUp()
                self.attach_by(SENDER, "unconditional")
                back(OWNER, "Pat")   # an owner
                self.assertEqual(self.rec["waiver_answer"]["kind"], "conditional")

    def test_adding_a_file_to_the_record_does_not_hand_the_answer_to_someone_else(self):
        self.attach_by(SENDER, "unconditional")
        self.state["files"].append({"id": "file-x", "name": "x.jpg", "ext": ".jpg", "size": 3})
        self.rec["file_ids"].append("file-x")
        domain._refresh_draft_gate(self.job, self.state)
        self.assertFalse(self.waiver["present"], "asked again for the files as they are")
        with self.assertRaises(domain.GateError):
            domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "conditional", "Lee", shown=self.rec["file_ids"], answerer=OTHER_SENDER)
        domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "conditional", "Pat", shown=self.rec["file_ids"], answerer=OWNER)
        self.assertEqual(self.rec["waiver_answer"]["kind"], "conditional")

    def test_a_conditional_answer_is_changed_to_unconditional_by_a_sender(self):
        self.attach_by(SENDER, "conditional")
        self.attach_by(OTHER_SENDER, "unconditional")
        self.assertEqual(self.rec["waiver_answer"]["kind"], "unconditional")
        with self.assertRaises(domain.GateError):   # and now it is the gate's: the send needs the owner
            domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read)


class AskingForRetainage(unittest.TestCase):
    """Fix 8: someone with ask_retainage answers the questions on a retainage request, and only there."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000, retainage=10)
        domain.record_progress(self.state, self.job["id"], 1_000_000)
        domain.mark_done(self.state, self.job["id"])
        inv = send_open(self.state, self.job)
        domain.match_payment(self.state, self.job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
        self.item = None
        for item in self.job["closeout_packet"]:
            if domain.is_waiver_item(item):
                self.item = item
            else:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="x")
        self.data = {}

    def test_a_retainage_holder_answers_the_question_and_the_which_waiver_on_the_request(self):
        rec = signed_waiver(self.state, self.job["id"], None, "Final waiver")
        domain.attach_record_to_packet(self.state, self.job["id"], self.item["id"], rec["id"], "Ret", answer="conditional", answerer=RETAINER)
        self.assertTrue(self.item["present"], "which waiver it is, on the request")
        other = signed_waiver(self.state, self.job["id"], None, "Other paperwork")
        other["kind"] = "other"
        domain.attach_record_to_packet(self.state, self.job["id"], self.item["id"], other["id"], "Ret", answerer=RETAINER)
        domain.answer_waiver_question(self.state, self.job["id"], other["id"], "no", "Ret", shown=other["file_ids"], answerer=RETAINER)
        self.assertEqual(other["waiver_answer"]["kind"], "no")

    def test_not_for_a_file_going_with_a_pay_app(self):
        job = new_job(self.state, contract=1_000_000, retainage=10, packet="Insurance certificate.")
        domain.record_progress(self.state, job["id"], 100_000)
        rec = signed_waiver(self.state, job["id"], None, "Paperwork")
        rec["kind"] = "other"
        d = draft(job)
        domain.change_payapp_attachments(self.state, job["id"], d["id"], "add_record", rec["id"], "Sam", read_file=lambda m: None)
        with self.assertRaises(domain.GateError):
            domain.answer_waiver_question(self.state, job["id"], rec["id"], "no", "Ret", shown=rec["file_ids"], answerer=RETAINER)
        domain.answer_waiver_question(self.state, job["id"], rec["id"], "no", "Sam", shown=rec["file_ids"], answerer=SENDER)

    def test_not_for_a_waiver_item_on_a_pay_app(self):
        job = new_job(self.state, contract=1_000_000, retainage=10)
        domain.record_progress(self.state, job["id"], 100_000)
        item = next(p for p in draft(job)["packet"] if domain.is_waiver_item(p))
        rec = signed_waiver(self.state, job["id"], None, "Waiver")
        domain.attach_record_to_packet(self.state, job["id"], item["id"], rec["id"], "Ret", answer="conditional", answerer=RETAINER)
        self.assertFalse(item["present"], "a pay app's waiver is answered by someone who can send pay apps")


class ASentFinalWaiverIsExemptOnlyWhereItWasSent(Case):
    """M1: the final waiver that went out after payment fills the closeout item and goes with the
    retainage request; added to a later pay app's email it is asked about like any other."""

    def test_added_to_a_later_pay_app_it_takes_the_owner(self):
        self.attach(self.stored("Waiver", pdf(GOOD)))
        for item in self.inv["packet"]:
            if not item["present"]:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="x")
        domain.approve_and_send(self.state, self.job["id"], "Sam")
        domain.match_payment(self.state, self.job["id"], self.inv["amount_cents"], "chk", invoice_id=self.inv["id"])
        sent = self.stored("Final", pdf("UNCONDITIONAL WAIVER AND RELEASE ON FINAL PAYMENT Pine kitchen $4,000.00"), typed="unconditional_final")
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"], kind="unconditional_final", confirm_final=True,
                           read_file=self.read, looked_at=sent["id"])
        self.assertTrue(sent.get("sent_as_final"))
        domain.record_progress(self.state, self.job["id"], 700_000)
        later = draft(self.job)
        domain.change_payapp_attachments(self.state, self.job["id"], later["id"], "add_record", sent["id"], "Mo", read_file=self.read)
        self.assertEqual([r["label"] for r in domain.unconditional_waivers_attached(self.job, later)], ["Final"])


class RetainageRequestOnAnOlderJob(unittest.TestCase):
    """M3: a job from before the closeout list attaches its older packet's files to the request;
    they are asked about and gated like the closeout's."""

    def test_an_unconditional_waiver_in_the_older_packet_takes_an_owner_reason(self):
        st = new_state()
        job = new_job(st, contract=1_000_000, retainage=10)
        domain.record_progress(st, job["id"], 1_000_000)
        domain.mark_done(st, job["id"])
        inv = send_open(st, job)
        domain.match_payment(st, job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
        rec = signed_waiver(st, job["id"], "unconditional_final", "Final waiver")
        rec["waiver_answer"] = {"kind": "unconditional", "by": "Sam", "at": "x", "file_ids": list(rec["file_ids"])}
        job.pop("closeout_packet", None)
        job["packet"] = [{"id": "pk-old", "kind": "waiver", "label": "Final lien waivers", "required": True, "present": True, "blocker": "x",
                          "waiver_kind": None, "attachments": [{"name": "Final waiver", "record_id": rec["id"]}], "confirmed_by": "Sam"}]
        with self.assertRaises(domain.GateError) as refused:
            domain.chase_retainage(st, job["id"], "Sam")
        self.assertEqual(str(refused.exception), domain.UNCONDITIONAL_ATTACHED)
        domain.chase_retainage(st, job["id"], "Pat", unconditional_override="Paid by wire")
        self.assertEqual(job["retainage_chases"], 1)

    def test_a_file_with_no_answer_in_the_older_packet_is_asked_first(self):
        st = new_state()
        job = new_job(st, contract=1_000_000, retainage=10)
        domain.record_progress(st, job["id"], 1_000_000)
        domain.mark_done(st, job["id"])
        inv = send_open(st, job)
        domain.match_payment(st, job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
        rec = signed_waiver(st, job["id"], None, "Other paperwork")
        rec["kind"] = "other"
        job.pop("closeout_packet", None)
        job["packet"] = [{"id": "pk-old", "kind": "insurance", "label": "Insurance certificate", "required": True, "present": True, "blocker": "x",
                          "waiver_kind": None, "attachments": [{"name": "Other paperwork", "record_id": rec["id"]}], "confirmed_by": "Sam"}]
        rec["waiver_read"] = {"guess": "", "form": True, "file_ids": sorted(rec["file_ids"])}
        with self.assertRaises(domain.GateError) as refused:
            domain.chase_retainage(st, job["id"], "Sam")
        self.assertTrue(refused.exception.extra.get("waiver_question"))


class IHaveThisDoesNotReplaceAnAnswer(Case):
    """M7: an override on a waiver item whose attached file nobody has answered for takes that file
    off the item; it does not keep going with the pay app."""

    def test_the_old_attachment_without_an_answer_does_not_go(self):
        rec = self.stored("W1", pdf(UNC), typed="")
        self.attach(rec, answer="conditional")
        self.attach(rec, answer="conditional", looked=True)
        rec.pop("waiver_answer", None)   # in from before the question
        self.waiver["present"] = True
        domain.resolve_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", reason="have it")
        self.assertEqual(self.waiver["attachments"], [], "the file nobody answered for is off the item")
        self.assertTrue(self.waiver["present"])
        self.assertEqual(self.waiver["override"]["by"], "Sam")
        self.assertEqual(domain._records_going(self.job, self.inv), [])
        for item in self.inv["packet"]:
            if not item["present"]:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="x")
        domain.approve_and_send(self.state, self.job["id"], "Sam")
        self.assertEqual(self.state["email_log"][-1]["attachments"], [])

    def test_an_answered_file_stays_after_the_override(self):
        rec = self.stored("W1", pdf(GOOD))
        self.attach(rec, answer="conditional")
        domain.resolve_packet_item(self.state, self.job["id"], self.waiver["id"], "Sam", reason="have it")
        self.assertEqual([a["record_id"] for a in self.waiver["attachments"]], [rec["id"]])


class TheAppSaysWhenItCouldntTell(Case):
    """M4: no choice is picked unless the app read one. A scan, a photo, a form that says neither
    word or both: "couldn't tell". Two forms in one file: says so."""

    TWO = "CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\nElm addition. Amount $5,000.00\nSigned: ____\n\f\nUNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\nElm addition. Amount $3,000.00\nSigned ____"

    def question(self, data, name="w.txt"):
        rec = self.stored("Signed waiver", data, name, typed="")
        self.attach(rec, answer="")
        return rec, domain.item_question(self.job, self.waiver)

    def test_separate_forms_are_lines_of_their_own_not_sentences_in_a_notice(self):
        self.assertTrue(doc_check.separate_forms(self.TWO))
        self.assertTrue(doc_check.separate_forms("Contents:\n2. Conditional waiver current payment\n3. Unconditional waiver previous payment\n"))
        for one in (c.TX_UNCOND, c.CA_8134, c.CA_8132, c.GC_FORMS["GC-two-forms-note"], c.GC_FORMS["GC-paragraph-heading"], c.TX_COND):
            self.assertFalse(doc_check.separate_forms(one), one[:60])

    def test_a_scan_a_photo_and_a_form_saying_neither_word_have_no_pick(self):
        for data, name in ((b"\xff\xd8 jpeg", "w.jpg"), (c.GA_INTERIM.encode(), "w.txt"), (b"%PDF-1.4\n", "w.pdf")):
            with self.subTest(name):
                self.setUp()
                _rec, asked = self.question(data, name)
                self.assertEqual(asked, {"guess": "", "reads_as": ""})

    def test_two_forms_in_one_file_say_so_and_pick_nothing(self):
        _rec, asked = self.question(self.TWO.encode())
        self.assertEqual(asked, {"guess": "", "reads_as": "", "both": True})

    def test_a_file_that_reads_gets_its_pick_and_one_that_does_not_beside_it_takes_it_away(self):
        rec, asked = self.question(pdf(GOOD))
        self.assertEqual(asked, {"guess": "conditional", "reads_as": "conditional"})
        other = self.stored("Photo", b"\xff\xd8 jpeg", "p.jpg", typed="")
        self.attach(other, answer="")
        self.assertEqual(domain.item_question(self.job, self.waiver)["guess"], "", "one of the two files was not read")

    def test_the_other_question_has_no_pick_either_when_the_app_couldnt_tell(self):
        rec = self.stored("Paperwork", c.TITLES_ANYWHERE["no title, only the words"].encode(), "p.txt", kind="other", typed="")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", rec["id"], "Sam", read_file=self.read)
        domain.waiver_questions(self.job, self.inv["packet"], self.inv["extra_record_ids"], (), self.state, self.read)
        rows = domain._question_rows(self.state, [rec])
        self.assertEqual([r["guess"] for r in rows], [""])
        two = self.stored("Both", self.TWO.encode(), "b.txt", kind="other", typed="")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", two["id"], "Sam", read_file=self.read)
        domain.waiver_questions(self.job, self.inv["packet"], self.inv["extra_record_ids"], (), self.state, self.read)
        self.assertEqual([(r["guess"], r.get("both")) for r in domain._question_rows(self.state, [two])], [("", True)])


class StoredMaterialsGiveWayToo(unittest.TestCase):
    """M9: a hand-set amount on a line gives way to a change of its stored materials, as it does to
    a new quantity, and the audit says so."""

    def setUp(self):
        self.st = new_state()
        self.job = new_job(self.st, contract=1, retainage=10)
        domain.save_sov(self.st, self.job["id"], [{"item": "1", "description": "Rough", "quantity": "100", "unit": "ea", "rate": "100"},
                                                  {"item": "2", "description": "Panel", "quantity": "1", "unit": "LS", "rate": "10000"}], "0", update_contract=True)
        self.a, self.b = sov.lines(self.job)
        domain.record_sov_progress(self.st, self.job["id"], {self.a["id"]: "20", self.b["id"]: "0.400"}, "", "Sam")
        domain.set_payapp_amounts(self.st, self.job["id"], {self.b["id"]: "3500.00"}, "Sam")

    def amounts(self):
        return {e["line_id"]: e["amount_cents"] for e in draft(self.job)["sov_sheet"]}

    def test_stored_materials_alone_replace_the_hand_amount(self):
        self.assertEqual(self.amounts()[self.b["id"]], 350_000)
        domain.record_sov_progress(self.st, self.job["id"], {self.a["id"]: "20", self.b["id"]: "0.400"}, "", "Sam", stored={self.b["id"]: "2000"})
        d = draft(self.job)
        self.assertEqual(self.amounts()[self.b["id"]], 400_000 + 200_000, "the stored $2,000 is billed")
        self.assertFalse(d.get("sov_overrides"))
        rows = [x["detail"] for x in self.st["audit"] if "gives way" in x["detail"]]
        self.assertEqual(len(rows), 1)
        self.assertIn("stored materials", rows[0])

    def test_the_other_lines_hand_amounts_and_an_unchanged_stored_figure_stay(self):
        domain.record_sov_progress(self.st, self.job["id"], {self.a["id"]: "20", self.b["id"]: "0.400"}, "", "Sam", stored={self.b["id"]: "2000"})
        domain.set_payapp_amounts(self.st, self.job["id"], {self.b["id"]: "5500.00"}, "Sam")
        domain.record_sov_progress(self.st, self.job["id"], {self.a["id"]: "30", self.b["id"]: "0.400"}, "", "Sam", stored={self.b["id"]: "2000"})
        self.assertEqual(self.amounts()[self.b["id"]], 550_000, "the form sends the same stored figure again: it is no change")


class HttpBase(Base):
    """Real profiles: Bob is a plain Member (record_payments only); Cat and Lee send pay apps; Dee
    asks for retainage; Ann is the owner."""

    FORM = b"UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\nElm addition\nAmount $5,000.00\nThe undersigned has been paid and waives all lien rights.\n"

    def setUp(self):
        super().setUp()
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            state["company"]["profiles"] = server.profiles.profiles(state) + [
                {"id": "sender", "name": "Sender", "perms": ["record_payments", "send_payapps"]},
                {"id": "sender2", "name": "Sender2", "perms": ["record_payments", "send_payapps"]},
                {"id": "retainer", "name": "Retainer", "perms": ["record_payments", "ask_retainage"]}]
            server.save_company(self.company, state)
        self.cat, self.lee, self.dee = (self.member(n, pid) for n, pid in (("cat", "sender"), ("lee", "sender2"), ("dee", "retainer")))

    def member(self, name, profile):
        email = f"{name}{self.n}@example.com"
        auth.create_user(email, PASSWORD, name.title() + " Member", self.company, role="member")
        store = auth._load()
        user = next(u for u in store["users"] if u["email"] == email)
        user["profile_id"] = profile
        auth._save(store)
        res, _ = self.request("POST", "/api/auth/login", {"email": email, "password": PASSWORD})
        return res.getheader("Set-Cookie").split(";")[0]

    def P(self, path, body=None, cookie=None):
        return self.post(f"/api/jobs/{self.job_id}/{path}", body or {}, cookie=cookie)

    def waiver_item(self):
        return next(p for p in self.draft()["packet"] if p.get("waiver_kind"))

    def up(self, name, data, kind="waiver", cookie=None, **q):
        res, d = self.upload(name, data, kind=kind, cookie=cookie, **q)
        self.assertEqual(res.status, 200, d)
        return d["uploaded_record_ids"][0]

    def rec(self, rid):
        return next(r for r in self.job()["records"] if r["id"] == rid)


class Through(HttpBase):
    """The server: who may set an answer, by each route."""

    def setUp(self):
        super().setUp()
        line = self.job()["sov"]["lines"][0]
        self.assertEqual(self.P("sov-progress", {"done": {line["id"]: "0.25"}})[0], 200)

    def test_no_route_lets_a_plain_member_set_an_answer(self):
        item, rid = self.waiver_item(), self.up("w.txt", self.FORM)
        files = self.rec(rid)["file_ids"]
        status, _ = self.P(f"waiver-answer/{rid}", {"answer": "conditional", "shown": files}, cookie=self.bob)
        self.assertEqual(status, 403)
        status, _ = self.P(f"attach-record/{item['id']}", {"record_id": rid, "answer": "conditional", "looked": True}, cookie=self.bob)
        self.assertEqual(status, 200, "the attachment works")
        self.assertFalse(self.waiver_item()["present"], "and waits for an answer")
        status, data = self.P(f"packet-confirm/{item['id']}", {"answer": "conditional", "looked": True, "shown": [rid]}, cookie=self.bob)
        self.assertEqual(status, 409, data)
        self.assertEqual(self.P(f"waiver-type/{rid}", {"kind": "conditional_progress"}, cookie=self.bob)[0], 200)
        self.assertNotIn("waiver_answer", self.rec(rid), "the type is no answer")
        self.assertFalse(self.waiver_item()["present"])
        status, data = self.P(f"packet-confirm/{item['id']}", {"answer": "unconditional", "looked": True, "shown": [rid]}, cookie=self.cat)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.rec(rid)["waiver_answer"]["by"], "Cat Member")

    def test_the_hunters_route_a_long_title_filed_as_a_note_is_asked_then_takes_the_owner(self):
        """h3_http: add_record of a note whose title isn't read as a heading, then Send as a Member who can."""
        item = self.waiver_item()
        cond = self.up("conditional.txt", b"CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\nElm addition\nAmount $5,000.00\nThe undersigned waives lien rights upon receipt of payment.\n")
        for _ in range(2):
            self.P(f"attach-record/{item['id']}", {"record_id": cond, "answer": "conditional", "looked": True}, cookie=self.cat)
        for other in self.draft()["packet"]:
            if other["required"] and not other["present"]:
                self.assertEqual(self.P(f"resolve/{other['id']}", {"reason": "In hand"})[0], 200)
        title = c.LONG_TITLES[2]
        for kind, name in (("other", "acme-oct.txt"), ("note", "acme-oct.txt"), ("insurance", "acme-oct.txt")):
            rid = self.up(name, (title + c.LONG_BODY).encode(), kind=kind)
            status, data = self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "add_record", "record_id": rid}, cookie=self.cat)
            self.assertEqual(status, 200, data)
            self.assertEqual([q["record_id"] for q in self.draft()["waiver_questions"]], [rid])
            self.assertEqual(self.draft()["waiver_questions"][0]["guess"], "unconditional")
            status, data = self.P("send", {"reviewed": self.draft()["email_preview"]["stamp"]}, cookie=self.cat)
            self.assertEqual((status, bool(data.get("waiver_question"))), (409, True), "no question, no answer, no send")
            self.assertEqual(self.P(f"waiver-answer/{rid}", {"answer": "unconditional", "shown": self.rec(rid)["file_ids"]}, cookie=self.cat)[0], 200)
            status, data = self.P("send", {"reviewed": self.draft()["email_preview"]["stamp"]}, cookie=self.cat)
            self.assertEqual((status, bool(data.get("unconditional_waiver"))), (409, True), "and then the owner")
            status, _ = self.P("send", {"reviewed": self.draft()["email_preview"]["stamp"], "unconditional_override": "Paid by wire"}, cookie=self.cat)
            self.assertEqual(status, 409, "a member can't give the reason")
            if kind != "insurance":
                self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "remove_record", "record_id": rid}, cookie=self.cat)
        status, data = self.P("send", {"reviewed": self.draft()["email_preview"]["stamp"], "unconditional_override": "Paid by wire"})
        self.assertEqual(status, 200, data)

    def test_a_bare_confirm_or_a_member_does_not_take_back_an_unconditional_answer(self):
        item, rid = self.waiver_item(), self.up("w.txt", self.FORM)
        for _ in range(2):
            self.P(f"attach-record/{item['id']}", {"record_id": rid, "answer": "unconditional", "looked": True}, cookie=self.cat)
        self.assertEqual(self.rec(rid)["waiver_answer"]["kind"], "unconditional")
        self.assertTrue(self.waiver_item()["present"])
        for who in (self.lee, self.bob):
            status, _ = self.P(f"packet-confirm/{item['id']}", {"answer": "conditional", "looked": True, "shown": []}, cookie=who)
            self.assertIn(status, (409,), "Lee can send but didn't say it")
            status, _ = self.P(f"waiver-answer/{rid}", {"answer": "conditional", "shown": self.rec(rid)["file_ids"]}, cookie=who)
            self.assertIn(status, (403, 409))
            status, _ = self.P(f"waiver-type/{rid}", {"kind": "conditional_progress"}, cookie=who)
            self.assertIn(status, (403, 409))
        self.assertEqual(self.rec(rid)["waiver_answer"]["kind"], "unconditional")
        status, _ = self.P(f"waiver-answer/{rid}", {"answer": "conditional", "shown": self.rec(rid)["file_ids"]}, cookie=self.cat)
        self.assertEqual(status, 200, "who said it can change it")
        self.assertTrue([a for a in server.load_company(self.company)["audit"] if "from unconditional to conditional" in a["detail"]], "audited")


class ThroughRetainage(HttpBase):
    """A finished job asking for its retainage: Dee (ask_retainage, not send_payapps) answers the
    questions on the request."""

    def setUp(self):
        super().setUp()
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            next(j for j in state["jobs"] if j["id"] == self.job_id)["retainage_pct"] = 10
            server.save_company(self.company, state)
        line = self.job()["sov"]["lines"][0]
        self.assertEqual(self.P("sov-progress", {"done": {line["id"]: "1.000"}})[0], 200)
        self.assertEqual(self.P("done", {})[0], 200)
        for item in self.draft()["packet"]:
            if item["required"] and not item["present"]:
                self.assertEqual(self.P(f"resolve/{item['id']}", {"reason": "In hand"})[0], 200)
        status, data = self.P("send", {"reviewed": self.draft()["email_preview"]["stamp"]})
        self.assertEqual(status, 200, data)
        sent = next(i for i in self.job()["invoices"] if i["status"] == "sent")
        self.assertEqual(self.P("match-payment", {"amount_cents": sent["amount_cents"], "reference": "c", "invoice_id": sent["id"]})[0], 200)
        self.items = self.job()["closeout_packet"]
        self.waiver = next(p for p in self.items if domain.is_waiver_item(p))
        for p in self.items:
            if p is not self.waiver and not p["present"]:
                self.assertEqual(self.P(f"resolve/{p['id']}", {"reason": "x"})[0], 200)

    def test_a_retainage_holder_answers_both_questions_on_the_request_and_asks(self):
        rid = self.up("final.txt", self.FORM.replace(b"PROGRESS", b"FINAL"))
        other = self.up("paper.txt", self.FORM, kind="other")
        for _ in range(2):
            status, data = self.P(f"attach-record/{self.waiver['id']}", {"record_id": rid, "answer": "unconditional", "looked": True}, cookie=self.dee)
            self.assertEqual(status, 200, data)
        self.assertTrue(next(p for p in self.job()["closeout_packet"] if p["id"] == self.waiver["id"])["present"])
        self.assertEqual(self.rec(rid)["waiver_answer"]["by"], "Dee Member")
        status, data = self.P("retainage", {}, cookie=self.dee)
        self.assertEqual(status, 409)
        self.assertTrue(data.get("unconditional_waiver"), data)
        status, _ = self.P(f"waiver-answer/{other}", {"answer": "no", "shown": self.rec(other)["file_ids"]}, cookie=self.dee)
        self.assertEqual(status, 409, "not on the request: only someone who can send pay apps answers it")
        self.assertEqual(self.P(f"waiver-answer/{other}", {"answer": "no", "shown": self.rec(other)["file_ids"]}, cookie=self.bob)[0], 403)

    def test_the_question_on_a_closeout_file_is_answered_by_the_retainage_holder(self):
        paper = self.up("paper.txt", self.FORM, kind="other")
        item = next(p for p in self.job()["closeout_packet"] if p["id"] != self.waiver["id"])
        self.assertEqual(self.P(f"detach/{item['id']}")[0], 200)
        status, data = self.P(f"attach-record/{item['id']}", {"record_id": paper, "looked": True}, cookie=self.dee)
        self.assertEqual(status, 200, data)
        self.assertTrue([q for q in self.job()["closeout_waiver_questions"] if q["record_id"] == paper])
        status, data = self.P(f"waiver-answer/{paper}", {"answer": "no", "shown": self.rec(paper)["file_ids"]}, cookie=self.dee)
        self.assertEqual(status, 200, data)


if __name__ == "__main__":
    unittest.main()
