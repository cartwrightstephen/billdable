"""The narrow re-check of the waiver answers (Round 1 hunt, r1d): a same-kind answer from someone
else never makes them the giver, a record can't be edited out of the waiver question by someone who
can't answer it, a retainage holder doesn't answer for a file that also goes on a pay app, the older
packet's retainage gate, the helper's answer to why it asks, claim waivers read as lien waivers, and
a waiver sent by someone who can't say which it is. The text only sets the default and a question;
a person's answer carries the decision."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import doc_check
import domain
import helper
import server
import unconditional_corpus as c
from test_blockers import GOOD, Case, pdf
from test_hunt_r1c import HttpBase, OTHER_SENDER, OWNER, PLAIN, RETAINER, SENDER, UNC
from test_money import send_open
from test_packet import draft, new_job, new_state
from waiver_help import signed_waiver

JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 200 + b"\xff\xd9"
UNC_FORM = b"UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\nElm addition\nAmount $5,000.00\nThe undersigned has been paid and waives all lien rights.\n"


class SameAnswerKeepsTheGiver(Case):
    """Blocker 1: an owner's Unconditional answer is not taken over by a second person saying the same
    thing, who could then take it back. The giver, and the audit, stay the first person's."""

    def setUp(self):
        super().setUp()
        self.rec = self.stored("Signed waiver", pdf(UNC), typed="")

    def attach_by(self, who, answer):
        for _ in range(2):
            domain.attach_record_to_packet(self.state, self.job["id"], self.waiver["id"], self.rec["id"], who.id, read_file=self.read, answer=answer,
                                           looked=True, answerer=who)

    def say(self, route, who, answer):
        if route == "attach":
            return self.attach_by(who, answer)
        if route == "confirm":
            self.waiver["attachments"][0]["unconfirmed"] = True
            self.waiver["present"] = False
            return domain.confirm_packet_item(self.state, self.job["id"], self.waiver["id"], who.id, looked=True, read_file=self.read,
                                              shown=[self.rec["id"]], answer=answer, answerer=who)
        if route == "type":
            return domain.set_waiver_type(self.state, self.job["id"], self.rec["id"], f"{answer}_progress", who.id, answerer=who)
        return domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], answer, who.id, shown=self.rec["file_ids"], answerer=who)

    def test_saying_the_same_thing_never_changes_the_giver_and_the_other_cannot_take_it_back(self):
        for route in ("attach", "confirm", "type", "question"):
            with self.subTest(route):
                self.setUp()
                self.attach_by(OWNER, "unconditional")
                first = dict(self.rec["waiver_answer"])
                self.say(route, OTHER_SENDER, "unconditional")
                said = self.rec["waiver_answer"]
                self.assertEqual((said["kind"], said["by"], said["by_id"], said["at"]), (first["kind"], first["by"], first["by_id"], first["at"]))
                with self.assertRaises(domain.GateError):
                    self.say(route, OTHER_SENDER, "conditional")
                self.assertEqual(self.rec["waiver_answer"]["kind"], "unconditional")
                self.assertEqual([r["label"] for r in domain.unconditional_waivers_attached(self.job, self.inv)], [self.rec["label"]], "the owner's gate still holds it")
                self.say(route, OWNER, "conditional")   # who gave it still can
                self.assertEqual(self.rec["waiver_answer"]["kind"], "conditional")

    def test_the_audit_keeps_the_first_author_and_notes_the_second(self):
        self.attach_by(OWNER, "unconditional")
        before = len(self.state["audit"])
        domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "unconditional", "Lee", shown=self.rec["file_ids"], answerer=OTHER_SENDER)
        rows = [a["detail"] for a in self.state["audit"][before:]]
        self.assertTrue(any("Lee" in r and "u-pat" in r for r in rows), rows)
        self.assertFalse(any("Lee said" in r for r in rows), rows)
        self.assertEqual(self.rec["waiver_answer"]["by"], OWNER.id)
        self.assertEqual(self.rec["waiver_answer"]["confirmed_by"], "Lee")

    def test_a_file_added_since_is_still_the_givers_answer(self):
        self.attach_by(OWNER, "unconditional")
        self.state["files"].append({"id": "file-x", "name": "x.jpg", "ext": ".jpg", "size": 3})
        self.rec["file_ids"].append("file-x")
        domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "unconditional", "Lee", shown=self.rec["file_ids"], answerer=OTHER_SENDER)
        self.assertEqual(domain.waiver_answer(self.rec), "unconditional", "answered for the files as they are now")
        self.assertEqual(self.rec["waiver_answer"]["by_id"], OWNER.id)


class EditingAWaiverOutOfTheQuestion(Case):
    """Blocker 2: the question keys on the record's history, and a kind change that would drop a
    waiver off a pay app is for someone who can answer it."""

    def setUp(self):
        super().setUp()
        self.attach(self.stored("Our waiver", pdf(GOOD)))   # the waiver item is filled, so a scan goes on as an extra
        for item in self.inv["packet"]:
            if item is not self.waiver:
                domain.resolve_packet_item(self.state, self.job["id"], item["id"], "Sam", reason="In hand")
        self.rec = self.stored("scan.jpg", JPG, "scan.jpg", kind="waiver", typed="unconditional_progress")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", self.rec["id"], "Sam", read_file=self.read)

    def asked(self):
        return domain.waiver_questions(self.job, self.inv["packet"], self.inv["extra_record_ids"], (), self.state, self.read)

    def edit(self, who, kind="photo", label="IMG_2231"):
        return domain.edit_record(self.state, self.job["id"], self.rec["id"], label, kind, who.id if who else "owner", answerer=who)

    def test_the_setup_asks(self):
        self.assertEqual(self.asked(), [self.rec])
        self.assertEqual(domain.unconditional_waivers_attached(self.job, self.inv), [self.rec])

    def test_someone_who_cannot_answer_does_not_change_the_kind_of_a_waiver_going_out(self):
        with self.assertRaises(domain.GateError):
            self.edit(PLAIN)
        self.assertEqual(self.rec["kind"], "waiver")
        self.edit(PLAIN, kind="waiver", label="Acme waiver")   # a new label alone is fine
        self.assertEqual(self.rec["label"], "Acme waiver")

    def test_whatever_it_is_called_it_is_still_asked_about_and_still_takes_the_owner(self):
        self.edit(SENDER)
        self.assertEqual(self.rec["kind"], "photo")
        self.assertEqual(self.asked(), [self.rec], "ever a waiver, asked until a person answers")
        self.assertEqual(domain.unconditional_waivers_attached(self.job, self.inv), [self.rec])
        with self.assertRaises(domain.GateError) as refused:
            domain.approve_and_send(self.state, self.job["id"], "Sam", read_file=self.read)
        self.assertTrue(refused.exception.extra.get("waiver_question"))
        domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "no", "Sam", shown=self.rec["file_ids"], answerer=SENDER)
        self.assertEqual(self.asked(), [])
        self.assertEqual(domain.unconditional_waivers_attached(self.job, self.inv), [], "a person said it is not a waiver")

    def test_an_unconditional_answer_is_changed_away_only_by_who_gave_it_or_an_owner_and_it_asks_again(self):
        domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "unconditional", "Sam", shown=self.rec["file_ids"], answerer=SENDER)
        for who in (PLAIN, OTHER_SENDER):
            with self.subTest(who.id):
                with self.assertRaises(domain.GateError):
                    self.edit(who)
                self.assertEqual((self.rec["kind"], domain.waiver_answer(self.rec)), ("waiver", "unconditional"))
        self.edit(SENDER)
        self.assertEqual(domain.waiver_answer(self.rec), "", "asks again")
        self.assertEqual(self.asked(), [self.rec])
        domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "unconditional", "Sam", shown=self.rec["file_ids"], answerer=SENDER)
        self.edit(OWNER, kind="ticket", label="Ticket 4411")
        self.assertEqual(self.asked(), [self.rec])

    def test_a_record_not_going_out_is_edited_freely_but_is_asked_when_it_is_added(self):
        free = self.stored("scan2.jpg", JPG, "scan2.jpg", kind="waiver", typed="unconditional_progress")
        domain.edit_record(self.state, self.job["id"], free["id"], "IMG_9", "photo", "Mo", answerer=PLAIN)
        self.assertEqual(free["kind"], "photo")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", free["id"], "Sam", read_file=self.read)
        self.assertIn(free, self.asked())

    def test_a_record_somebody_said_is_not_a_waiver_is_edited_freely(self):
        domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "no", "Sam", shown=self.rec["file_ids"], answerer=SENDER)
        self.edit(PLAIN)
        self.assertEqual((self.rec["kind"], self.asked()), ("photo", []))

    def test_a_ticket_was_never_a_waiver(self):
        ticket = self.stored("Ticket", b"DELIVERY TICKET 5501\nElm addition\n", "t.txt", kind="ticket", typed="")
        domain.change_payapp_attachments(self.state, self.job["id"], self.inv["id"], "add_record", ticket["id"], "Sam", read_file=self.read)
        domain.edit_record(self.state, self.job["id"], ticket["id"], "Ticket 5501", "photo", "Mo", answerer=PLAIN)
        self.assertNotIn(ticket, self.asked())


def retainage_job_with_an_open_draft():
    """A closeout waiver item whose record is also an extra on the next pay app's draft (the hunter's a11_ret.py)."""
    state = new_state()
    job = new_job(state, contract=1_000_000, retainage=10)
    domain.record_progress(state, job["id"], 500_000)
    inv1 = send_open(state, job)
    domain.match_payment(state, job["id"], inv1["amount_cents"], "c", invoice_id=inv1["id"])
    domain.record_progress(state, job["id"], 1_000_000)
    d = draft(job)
    rec = signed_waiver(state, job["id"], None, "Waiver scan")
    item = next(i for i in job["closeout_packet"] if domain.is_waiver_item(i))
    domain.attach_record_to_packet(state, job["id"], item["id"], rec["id"], "Rita", answerer=RETAINER)
    domain.change_payapp_attachments(state, job["id"], d["id"], "add_record", rec["id"], "Rita")
    return state, job, d, rec, item


class RetainageHolderAndAPayApp(unittest.TestCase):
    """Blocker 3: a file that also goes on the open pay app is the pay app's, whichever item it is on."""

    def setUp(self):
        self.state, self.job, self.draft, self.rec, self.item = retainage_job_with_an_open_draft()

    def test_neither_the_item_nor_the_record_is_the_retainage_requests_alone(self):
        self.assertFalse(domain._on_retainage_request(self.job, record=self.rec))
        self.assertFalse(domain._on_retainage_request(self.job, item=self.item))

    def test_no_route_lets_a_retainage_holder_answer_for_it(self):
        j = self.job["id"]
        with self.assertRaises(domain.GateError):
            domain.confirm_packet_item(self.state, j, self.item["id"], "Rita", answer="conditional", looked=True, answerer=RETAINER)
        domain.attach_record_to_packet(self.state, j, self.item["id"], self.rec["id"], "Rita", answer="conditional", looked=True, answerer=RETAINER)
        with self.assertRaises(domain.GateError):
            domain.answer_waiver_question(self.state, j, self.rec["id"], "conditional", "Rita", answerer=RETAINER)
        self.assertNotIn("waiver_answer", self.rec)
        self.assertFalse(self.item["present"], "it waits for someone who can send pay apps")
        domain.confirm_packet_item(self.state, j, self.item["id"], "Sam", answer="conditional", looked=True, answerer=SENDER)
        self.assertEqual(self.rec["waiver_answer"]["by"], "Sam")

    def test_a_different_record_attached_to_the_item_while_one_is_on_the_draft(self):
        j = self.job["id"]
        other = signed_waiver(self.state, j, None, "Other waiver")
        domain.attach_record_to_packet(self.state, j, self.item["id"], other["id"], "Rita", answer="conditional", looked=True, answerer=RETAINER)
        self.assertNotIn("waiver_answer", other, "the item also holds a file that goes on the pay app")

    def test_an_item_with_only_closeout_files_is_still_theirs(self):
        j = self.job["id"]
        for x in self.draft["packet"]:
            x["attachments"] = []   # as if it had not been suggested to the pay app's own waiver item
        self.draft["extra_record_ids"] = []
        self.assertTrue(domain._on_retainage_request(self.job, item=self.item))
        domain.confirm_packet_item(self.state, j, self.item["id"], "Rita", answer="conditional", looked=True, answerer=RETAINER)
        self.assertEqual(self.rec["waiver_answer"]["by"], "Rita")


class RetainageOnAJobWithoutACloseoutList(unittest.TestCase):
    """Minor 4: the older packet is the retainage request's, and its holder is no more refused than the page says."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000, retainage=10)
        domain.record_progress(self.state, self.job["id"], 1_000_000)
        domain.mark_done(self.state, self.job["id"])
        inv = send_open(self.state, self.job)
        domain.match_payment(self.state, self.job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
        self.rec = signed_waiver(self.state, self.job["id"], "unconditional_final", "Final waiver")
        self.job.pop("closeout_packet", None)
        self.item = {"id": "pk-old", "kind": "closeout", "label": "Final lien waivers", "required": True, "present": False, "blocker": "x",
                     "waiver_kind": "conditional_final", "attachments": [{"name": "Final waiver", "record_id": self.rec["id"], "unconfirmed": True}]}
        self.job["packet"] = [self.item]

    def test_the_holder_of_ask_retainage_answers_there(self):
        self.assertTrue(domain._on_retainage_request(self.job, item=self.item))
        self.assertTrue(domain._on_retainage_request(self.job, record=self.rec))
        domain.confirm_packet_item(self.state, self.job["id"], self.item["id"], "Rita", answer="conditional", looked=True, answerer=RETAINER)
        self.assertEqual(self.rec["waiver_answer"]["kind"], "conditional")
        domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "unconditional", "Rita", answerer=RETAINER)

    def test_someone_with_neither_is_still_refused(self):
        with self.assertRaises(domain.GateError):
            domain.confirm_packet_item(self.state, self.job["id"], self.item["id"], "Mo", answer="conditional", looked=True, answerer=PLAIN)


class SendingAWaiverTakesAnAnswerer(unittest.TestCase):
    """Minor 7: sending the signed waiver isn't the answer to which waiver it is, for someone who can't give one."""

    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000, retainage=0)
        domain.record_progress(self.state, self.job["id"], 500_000)
        self.inv = send_open(self.state, self.job)
        domain.match_payment(self.state, self.job["id"], self.inv["amount_cents"], "c", invoice_id=self.inv["id"])
        self.rec = signed_waiver(self.state, self.job["id"], None, "Waiver")
        self.sender = domain.Answerer(send_payapps=False, ask_retainage=False, owner=False, id="u-w")

    def send(self, who):
        return domain.send_waiver(self.state, self.job["id"], "Wendy", invoice_id=self.inv["id"], kind="unconditional_progress", answerer=who)

    def test_a_waiver_nobody_has_answered_waits_for_someone_who_can(self):
        with self.assertRaises(domain.GateError) as refused:
            self.send(self.sender)
        self.assertIn("can send pay apps", str(refused.exception))
        self.assertNotIn("waiver_answer", self.rec)
        self.assertFalse(self.inv.get("waiver_sent"))
        self.send(SENDER)
        self.assertEqual(self.rec["waiver_answer"]["kind"], "unconditional")
        self.assertTrue(self.inv["waiver_sent"])

    def test_one_that_was_answered_goes(self):
        domain.answer_waiver_question(self.state, self.job["id"], self.rec["id"], "unconditional", "Sam", answerer=SENDER)
        self.send(self.sender)
        self.assertEqual(self.rec["waiver_answer"]["by"], "Sam", "the answer is still the one who gave it")
        self.assertTrue(self.inv["waiver_sent"])


class ThroughTheServer(HttpBase):
    """The hunter's routes (a1_steal.py, a8_edit.py), as the profiles are: Ann is the owner, Cat and Lee
    send pay apps, Bob is a plain member."""

    def setUp(self):
        super().setUp()
        line = self.job()["sov"]["lines"][0]
        self.assertEqual(self.P("sov-progress", {"done": {line["id"]: "0.25"}})[0], 200)
        cond = self.up("conditional.txt", UNC_FORM.replace(b"UNCONDITIONAL", b"CONDITIONAL"))   # the pay app's own waiver item is filled
        for _ in range(2):
            self.P(f"attach-record/{self.waiver_item()['id']}", {"record_id": cond, "answer": "conditional", "looked": True}, cookie=self.cat)
        self.assertTrue(self.waiver_item()["present"])

    def add_extra(self, rid, cookie=None):
        status, data = self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "add_record", "record_id": rid}, cookie=cookie)
        self.assertEqual(status, 200, data)

    def test_a_second_person_saying_unconditional_does_not_become_the_giver(self):
        for route in ("answer", "type"):
            with self.subTest(route):
                rid = self.up(f"w-{route}.txt", UNC_FORM)
                files = self.rec(rid)["file_ids"]
                self.add_extra(rid, cookie=self.cat)
                self.assertEqual(self.P(f"waiver-answer/{rid}", {"answer": "unconditional", "shown": files})[0], 200)
                self.assertEqual(self.rec(rid)["waiver_answer"]["by"], "Ann Owner")
                if route == "answer":
                    self.assertEqual(self.P(f"waiver-answer/{rid}", {"answer": "unconditional", "shown": files}, cookie=self.lee)[0], 200)
                else:
                    self.assertEqual(self.P(f"waiver-type/{rid}", {"kind": "unconditional_progress"}, cookie=self.lee)[0], 200)
                self.assertEqual(self.rec(rid)["waiver_answer"]["by"], "Ann Owner", "the giver stays")
                status, _ = self.P(f"waiver-type/{rid}", {"kind": "conditional_progress"}, cookie=self.lee)
                self.assertEqual(status, 409)
                status, _ = self.P(f"waiver-answer/{rid}", {"answer": "conditional", "shown": files}, cookie=self.lee)
                self.assertEqual(status, 409)
                self.assertEqual(self.rec(rid)["waiver_answer"]["kind"], "unconditional")
                self.assertIn(rid, [u["record_id"] for u in self.draft()["unconditional_attached"]])

    def test_a_member_who_cannot_answer_does_not_edit_a_scanned_waiver_out_of_the_question(self):
        rid = self.up("scan.jpg", JPG, kind="waiver", waiver_kind="unconditional_progress")
        self.add_extra(rid, cookie=self.cat)
        self.assertEqual([q["record_id"] for q in self.draft()["waiver_questions"]], [rid])
        self.assertEqual([u["record_id"] for u in self.draft()["unconditional_attached"]], [rid])
        status, data = self.P(f"edit-record/{rid}", {"label": "IMG_2231", "kind": "photo"}, cookie=self.bob)
        self.assertEqual(status, 409, data)
        self.assertEqual(self.rec(rid)["kind"], "waiver")
        # Cat can send: the edit goes through and the question stays, by its history.
        status, data = self.P(f"edit-record/{rid}", {"label": "IMG_2231", "kind": "photo"}, cookie=self.cat)
        self.assertEqual(status, 200, data)
        self.assertEqual([q["record_id"] for q in self.draft()["waiver_questions"]], [rid])
        self.assertEqual([u["record_id"] for u in self.draft()["unconditional_attached"]], [rid])
        for item in self.draft()["packet"]:
            if item["required"] and not item["present"]:
                self.assertEqual(self.P(f"resolve/{item['id']}", {"reason": "In hand"})[0], 200)
        status, data = self.P("send", {"reviewed": self.draft()["email_preview"]["stamp"]}, cookie=self.cat)
        self.assertEqual((status, bool(data.get("waiver_question"))), (409, True))


class WhyItAsksAboutALienWaiver(unittest.TestCase):
    """Minor 5: the helper answers why it asks; its other answers stay."""

    @classmethod
    def setUpClass(cls):
        cls.kb = [{**e, "origin": "shipped"} for e in helper._read(helper.SHIPPED_KB)]

    def top(self, q):
        score, entry = helper.rank(q, self.kb)[0]
        return entry["id"], score

    def test_the_questions_about_the_lien_waiver_question(self):
        for q in ("Why does it ask whether a file is a lien waiver?", "Why does Duracash ask is this a lien waiver", "Why is it asking if this is a lien waiver",
                  "Is this a lien waiver? what do I tap", "Which waiver do I tap?"):
            with self.subTest(q):
                eid, score = self.top(q)
                self.assertEqual(eid, "which-waiver")
                self.assertGreaterEqual(score, helper.ANSWER_AT)

    def test_the_others_are_as_they_were(self):
        for q, eid in (("my client is asking for retainage, how do I ask", "release-retainage"), ("What is a lien waiver?", "what-waiver"),
                       ("Why is my GC asking for a lien waiver?", "what-waiver"), ("How do I send a waiver?", "send-waiver")):
            with self.subTest(q):
                self.assertEqual(self.top(q)[0], eid)

    def test_every_phrasing_of_an_answer_that_lists_the_words_it_needs_gets_that_answer(self):
        for e in self.kb:
            if e.get("needs"):
                for q in e["questions"]:
                    with self.subTest(q):
                        score, top = helper.rank(q, self.kb)[0]
                        self.assertEqual((top["id"], score >= helper.ANSWER_AT), (e["id"], True))


class ClaimWaiversAreLienWaivers(unittest.TestCase):
    """Minor 6: a waiver of claims and the right to recover, with the type or a payment beside it, is one; insurance waivers are not."""

    def test_the_real_claim_waivers_are_asked_about_in_every_form(self):
        missed = [name for name, text in c.CLAIM_WAIVERS.items() if not doc_check.reading(text)["form"]]
        self.assertEqual(missed, [])
        for name, text in c.CLAIM_WAIVERS.items():
            self.assertEqual(doc_check.reading(text)["guess"], "unconditional", name)

    def test_insurance_waivers_still_are_not(self):
        asked = [name for name, text in c.INSURANCE.items() if doc_check.reading(text)["form"]]
        self.assertEqual(asked, [])
        for text in ("WAIVER OF CLAIMS AND SUBROGATION ENDORSEMENT\nThe insurer waives its rights of subrogation against Park GC.",
                     "CERTIFICATE OF LIABILITY INSURANCE\nWaiver of subrogation applies. Claims made basis. Right to recover from others is waived.",
                     "POLICY WC 00 03 13\nWAIVER OF OUR RIGHT TO RECOVER FROM OTHERS ENDORSEMENT\nWe waive our right of recovery for claims."):
            self.assertFalse(doc_check.reading(text)["form"], text)

    def test_a_claim_waiver_far_from_a_type_or_a_payment_is_not_a_form(self):
        text = ("SUBCONTRACT AGREEMENT\n" + "".join(f"Article {n}. Work item {n} is described in Exhibit A.\n" for n in range(1, 15))
                + "Waiver of claims for delay is covered in Article 9.\n" + "Warranty applies for one year.\n" * 20)
        self.assertFalse(doc_check.reading(text)["form"])


if __name__ == "__main__":
    unittest.main()
