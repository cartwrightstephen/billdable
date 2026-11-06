"""The second independent hunt of Nov 5, under the Nov 6 redesign: a file going out that reads as a
lien waiver is asked about, and the person's answer decides; said to be the unconditional one where
a conditional one is needed, it is the owner's gate at send."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import doc_check
import server
from domain import profiles
from test_hunt_nov5_http import Base

CALIFORNIA = """UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT
NOTICE: THIS DOCUMENT WAIVES THE CLAIMANT'S LIEN RIGHTS UNCONDITIONALLY AND STATES THAT YOU HAVE BEEN PAID FOR GIVING UP THOSE RIGHTS.
THIS DOCUMENT IS ENFORCEABLE AGAINST YOU IF YOU SIGN IT, EVEN IF YOU HAVE NOT BEEN PAID. IF YOU HAVE NOT BEEN PAID, USE A CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT FORM.
Name of Claimant: Elm addition $5,000.00
"""
LOG = "Daily log Elm addition. Sub's unconditional lien waiver received from plumber for last month. Crew of 4."


class Reading(unittest.TestCase):
    def read(self, text):
        return doc_check.reading(text)

    def test_reads_as_an_unconditional_waiver(self):
        for text in ("UNCONDITIONAL WAIVER AND RELEASE", "UNCONDITIONAL WAIVER AND RELEASE UPON PAYMENT", "Unconditional waiver of lien.", "Waiver and release, unconditional.",
                     "UNCONDITIONAL\nWAIVER AND RELEASE", "Un-conditional waiver", "Georgia Statutory Form\nUNCONDITIONAL FINAL LIEN WAIVER", "UNCONDITIONAL LIEN WAIVER\nJob: Elm",
                     "Project: Elm\nOwner: Park\nUNCONDITIONAL RELEASE", CALIFORNIA, "This release is not conditional and is an unconditional waiver.",
                     "Not an unconditional release. Addendum: unconditional waiver of all claims.", "UNCONDITIONAL"):
            self.assertEqual(self.read(text), {"guess": "unconditional", "form": True}, text)

    def test_a_conditional_title_first_reads_conditional_and_the_answer_decides(self):
        self.assertEqual(self.read("Conditional waiver and release\nline\nline\nUNCONDITIONAL WAIVER AND RELEASE"), {"guess": "conditional", "form": True})

    def test_a_line_about_one_near_the_top_is_asked_about(self):
        for text in (LOG, "Unconditional lien waiver received from plumber for last month.",
                     "CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT. Elm addition. Upon receipt of payment the undersigned will deliver an unconditional waiver and release."):
            self.assertTrue(self.read(text)["form"], text)

    def test_taken_back_or_about_something_else(self):
        self.assertEqual(self.read("This is not an unconditional release.")["guess"], "")
        self.assertFalse(self.read("Addendum: unconditional acceptance of the revised scope.")["form"])

    def test_california_on_an_unconditional_item(self):
        got = doc_check.check([("a.txt", ".txt", CALIFORNIA.encode())], "Elm addition", "$5,000.00", 500_000, "unconditional_progress", True)
        self.assertEqual(got["problems"], [])

    def test_a_conditional_form_on_an_unconditional_item_still_says_so(self):
        got = doc_check.check([("a.txt", ".txt", b"CONDITIONAL WAIVER AND RELEASE. Elm addition $5,000.00")], "Elm addition", "$5,000.00", 500_000, "unconditional_progress", True)
        self.assertIn("This reads as conditional.", got["problems"])


class SendGates(Base):
    def setUp(self):
        super().setUp()
        self.member_perms(["record_payments", "send_payapps", "send_waivers"])
        line = self.job()["sov"]["lines"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        self.assertEqual(status, 200, data)
        self.amount = self.draft()["amount_cents"] / 100

    def member_perms(self, perms):
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            state["company"]["profiles"] = [dict(p, perms=perms) if p["id"] == "member" else p for p in profiles.profiles(state)]
            server.save_company(self.company, state)

    def item(self, words):
        return next(p for p in self.draft()["packet"] if words in p["label"].lower() or (words == "waiver" and p.get("waiver_kind")))

    def attach(self, words, name, text, **query):
        item = self.item(words)
        res, data = self.upload(name, text.encode(), attach=item["id"], **query)
        self.assertEqual(res.status, 200, data)
        return data["uploaded_record_ids"][0], self.item(words)

    def clear_the_rest(self):
        for item in self.draft()["packet"]:
            if item["required"] and not item["present"]:
                status, data = self.post(f"/api/jobs/{self.job_id}/resolve/{item['id']}", {"reason": "In hand"})
                self.assertEqual(status, 200, data)

    def send(self, cookie=None, **body):
        return self.post(f"/api/jobs/{self.job_id}/send", {"reviewed": self.draft()["email_preview"]["stamp"], **body}, cookie=cookie)

    def audit(self):
        return server.load_company(self.company)["audit"]

    def answer(self, rid, kind, cookie=None):
        shown = next(r for r in self.job()["records"] if r["id"] == rid).get("file_ids")
        return self.post(f"/api/jobs/{self.job_id}/waiver-answer/{rid}", {"answer": kind, "shown": shown}, cookie=cookie)

    def extra(self, name, text, kind):
        res, data = self.upload(name, text.encode(), kind=kind)
        self.assertEqual(res.status, 200, data)
        rid = data["uploaded_record_ids"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "add_record", "record_id": rid})
        self.assertEqual(status, 200, data)
        return rid

    def asked(self):
        return [r["record_id"] for r in self.draft()["waiver_questions"]]

    def test_a_daily_log_that_mentions_one_a_member_answers_no_and_sends(self):
        rid = self.extra("log.txt", LOG, "ticket")
        self.clear_the_rest()
        self.assertEqual(self.draft()["unconditional_attached"], [])
        self.assertEqual(self.asked(), [rid])
        status, data = self.send(cookie=self.bob)
        self.assertEqual(status, 409, data)
        self.assertTrue(data["waiver_question"])
        self.assertFalse(data.get("unconditional_waiver"))
        self.assertEqual(self.answer(rid, "no", cookie=self.bob)[0], 200)
        self.assertEqual(self.asked(), [])
        status, data = self.send(cookie=self.bob)
        self.assertEqual(status, 200, data)
        rows = [a for a in self.audit() if a["action"] == "waiver_answer"]
        self.assertIn("Bob Field said log.txt is not a lien waiver", rows[0]["detail"])
        self.assertFalse(any(a["action"] == "unconditional_override" for a in self.audit()))

    def test_the_waiver_items_tap_is_the_answer(self):
        rid, item = self.attach("waiver", "cond.txt", f"CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT. Elm addition. Amount {self.amount:,.2f}. Upon receipt of payment the undersigned will deliver an unconditional waiver and release.",
                                kind="waiver", waiver_kind=self.item("waiver")["waiver_kind"])
        self.assertFalse(item["present"], "nothing goes before someone says which waiver it is")
        self.assertEqual(item["waiver_question"], {"guess": "conditional", "reads_as": "conditional"})
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": [rid], "answer": "conditional"}, cookie=self.bob)
        self.assertEqual(status, 200, data)
        self.clear_the_rest()
        status, data = self.send(cookie=self.bob)
        self.assertEqual(status, 200, data)
        rec = next(r for r in self.job()["records"] if r["id"] == rid)
        self.assertEqual((rec["waiver_answer"]["kind"], rec["waiver_answer"]["by"], rec["waiver_kind"]), ("conditional", "Bob Field", "conditional_progress"))

    def test_a_member_without_send_permission_cant_answer(self):
        rid = self.extra("log.txt", "Sub's unconditional lien waiver received from plumber.", "note")
        self.member_perms(["record_payments"])
        self.assertEqual(self.answer(rid, "no", cookie=self.bob)[0], 403)

    def test_another_file_on_the_record_asks_again(self):
        rid = self.extra("log.txt", "Sub's unconditional lien waiver received from plumber.", "note")
        self.assertEqual(self.answer(rid, "no")[0], 200)
        self.assertEqual(self.asked(), [])
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            rec = next(r for j in state["jobs"] for r in j["records"] if r["id"] == rid)
            rec["file_ids"] = rec["file_ids"] + ["f-other"]
            state["files"].append({"id": "f-other", "name": "other.txt", "ext": ".txt", "stored": "gone"})
            server.save_company(self.company, state)
        self.clear_the_rest()
        status, data = self.send()
        self.assertEqual(status, 409, data)
        self.assertTrue(data["waiver_question"])
        self.assertEqual(self.asked(), [rid])

    def test_a_members_unconditional_answer_takes_the_owner(self):
        rid = self.extra("form.txt", "UNCONDITIONAL WAIVER AND RELEASE\n", "other")
        self.assertEqual(self.answer(rid, "unconditional", cookie=self.bob)[0], 200)
        self.clear_the_rest()
        status, data = self.send(cookie=self.bob)
        self.assertEqual(status, 409, data)
        self.assertTrue(data["unconditional_waiver"])
        status, data = self.send(cookie=self.bob, unconditional_override="Paid")
        self.assertNotEqual(status, 200, data)
        status, data = self.send(unconditional_override="Paid by wire")
        self.assertEqual(status, 200, data)
        row = next(a for a in self.audit() if a["action"] == "unconditional_override")
        self.assertIn("with an unconditional waiver attached", row["detail"])

    def test_every_kind_is_asked_and_the_waiver_item_said_unconditional_is_the_gate(self):
        rids = [self.extra(f"f{n}.txt", "UNCONDITIONAL WAIVER AND RELEASE\n", kind) for n, kind in enumerate(("note", "photo", "pay_app", "ticket", "supplier_invoice"))]
        self.assertEqual(sorted(self.asked()), sorted(rids))
        rid2, item = self.attach("waiver", "w.txt", f"UNCONDITIONAL WAIVER AND RELEASE UPON PAYMENT. Elm addition {self.amount:,.2f}", kind="waiver", waiver_kind=self.item("waiver")["waiver_kind"])
        self.assertFalse(item["present"])
        self.assertEqual(item["waiver_question"]["guess"], "unconditional", "picked first")
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": [rid2], "answer": "unconditional"})
        self.assertEqual(status, 200, data)
        self.assertIn(rid2, [r["record_id"] for r in self.draft()["unconditional_attached"]])

    def test_an_override_doesnt_pass_a_question_without_the_answer(self):
        rid = self.extra("log.txt", "Sub's unconditional lien waiver received from plumber.", "note")
        self.clear_the_rest()
        status, data = self.send(unconditional_override="Reason")
        self.assertEqual(status, 409, data)
        self.assertTrue(data["waiver_question"])
        self.assertEqual(self.asked(), [rid])


class OneLookClearsAFile(Base):
    """Item 12: the prompt lists everything, and one confirm clears it for that file at that amount."""

    def setUp(self):
        super().setUp()
        line = self.job()["sov"]["lines"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "1"}})
        self.assertEqual(status, 200, data)

    def item(self, words="waiver"):
        return next(p for p in self.draft()["packet"] if p.get("waiver_kind") if words == "waiver")

    def attach_waiver(self):
        item = self.item()
        res, data = self.upload("w.txt", b"Conditional waiver for somewhere else", kind="waiver", attach=item["id"], waiver_kind=item["waiver_kind"])
        self.assertEqual(res.status, 200, data)
        return data["uploaded_record_ids"][0], self.item()

    def test_a_problem_that_turned_up_since_is_cleared_by_the_one_look(self):
        rid, item = self.attach_waiver()
        self.assertFalse(item["present"])
        before = list(item["check"]["problems"])
        # The job is marked done, so the draft is the final one and the waiver's wording is asked for again.
        status, data = self.post(f"/api/jobs/{self.job_id}/done", {})
        self.assertEqual(status, 200, data)
        draft = self.draft()
        item = next(p for p in draft["packet"] if p.get("waiver_kind"))
        shown = [a["record_id"] for a in item["attachments"] if a.get("unconfirmed")]
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": shown, "answer": "conditional"})
        self.assertEqual(status, 200, data)
        item = next(p for p in self.draft()["packet"] if p["id"] == item["id"])
        self.assertTrue(item["present"], "one look clears it")
        self.assertTrue(before)

    def test_the_prompt_has_everything_before_the_look(self):
        rid, item = self.attach_waiver()
        status, data = self.post(f"/api/jobs/{self.job_id}/done", {})
        self.assertEqual(status, 200, data)
        item = next(p for p in self.draft()["packet"] if p.get("waiver_kind"))
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-recheck/{item['id']}", {"shown": [a["record_id"] for a in item["attachments"] if a.get("unconfirmed")]})
        self.assertEqual(status, 200, data)
        fresh = next(p for p in self.draft()["packet"] if p["id"] == item["id"])
        self.assertFalse(fresh["present"], "a recheck clears nothing")
        self.assertIn("Final wording not found.", fresh["check"]["problems"])

    def test_a_different_amount_asks_again(self):
        rid, item = self.attach_waiver()
        line = self.job()["sov"]["lines"][0]
        draft_id = self.draft()["id"]
        with server.company_lock(self.company):   # the amount changed since that look
            state = server.load_company(self.company)
            job = next(j for j in state["jobs"] if j["id"] == self.job_id)
            stored = next(p for i in job["invoices"] if i["id"] == draft_id for p in i["packet"] if p["id"] == item["id"])
            stored["check_for"]["cents"] = 1
            server.save_company(self.company, state)
        shown = [a["record_id"] for a in item["attachments"] if a.get("unconfirmed")]
        status, data = self.post(f"/api/jobs/{self.job_id}/done", {})
        self.assertEqual(status, 200, data)
        item = next(p for p in self.draft()["packet"] if p.get("waiver_kind"))
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": shown, "answer": "conditional"})
        item = next(p for p in self.draft()["packet"] if p["id"] == item["id"])
        self.assertFalse(item["present"], "a new problem at another amount gets its own look")
        self.assertIn("Final wording not found.", item["check"]["problems"])

    def test_field_documents_dont_need_the_job_name(self):
        for label, kind in (("Daily tickets", "ticket"), ("Inspection report", "inspection"), ("Supplier invoices", "supplier_invoice")):
            job = next(j for j in server.load_company(self.company)["jobs"] if j["id"] == self.job_id)
            with server.company_lock(self.company):
                state = server.load_company(self.company)
                job = next(j for j in state["jobs"] if j["id"] == self.job_id)
                draft = next(i for i in job["invoices"] if i["status"] in ("draft", "awaiting_approval"))
                draft["packet"].append({"id": f"it-{kind}", "kind": kind, "label": label, "required": True, "present": False, "blocker": f"{label} is missing.", "attachments": []})
                server.save_company(self.company, state)
            res, data = self.upload(f"{kind}.txt", b"Crew of four, six hours, framing on the second floor.", kind=kind, attach=f"it-{kind}")
            self.assertEqual(res.status, 200, data)
            item = next(p for p in self.draft()["packet"] if p["id"] == f"it-{kind}")
            self.assertEqual((item.get("check") or {}).get("problems") or [], [], label)
            self.assertTrue(item["present"], label)

    def test_a_contract_still_does(self):
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            job = next(j for j in state["jobs"] if j["id"] == self.job_id)
            draft = next(i for i in job["invoices"] if i["status"] in ("draft", "awaiting_approval"))
            draft["packet"].append({"id": "it-c", "kind": "contract", "label": "Signed contract", "required": True, "present": False, "blocker": "Signed contract is missing.", "attachments": []})
            server.save_company(self.company, state)
        res, data = self.upload("c.txt", b"Contract for a different job altogether.", kind="contract", attach="it-c")
        self.assertEqual(res.status, 200, data)
        item = next(p for p in self.draft()["packet"] if p["id"] == "it-c")
        self.assertIn("Job name not found: Elm addition.", item["check"]["problems"])


if __name__ == "__main__":
    unittest.main()
