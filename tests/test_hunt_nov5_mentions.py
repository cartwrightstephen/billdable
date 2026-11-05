"""The second independent hunt of Nov 5: a file that reads as an unconditional waiver is the owner's
gate at send; a file that only mentions one is a look anyone who can send clears."""

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


class Reading(unittest.TestCase):
    def read(self, text, near_only=False):
        return doc_check.unconditional_reading([("a.txt", ".txt", text.encode())], near_only)

    def test_reads_as_an_unconditional_waiver_in_itself(self):
        for text in ("UNCONDITIONAL WAIVER AND RELEASE", "UNCONDITIONAL WAIVER AND RELEASE UPON PAYMENT", "Unconditional waiver of lien.", "Waiver and release, unconditional.",
                     "UNCONDITIONAL\nWAIVER AND RELEASE", "Un-conditional waiver", "Georgia Statutory Form\nUNCONDITIONAL FINAL LIEN WAIVER", "UNCONDITIONAL LIEN WAIVER\nJob: Elm",
                     "Project: Elm\nOwner: Park\nUNCONDITIONAL RELEASE", CALIFORNIA, "This release is not conditional and is an unconditional waiver.",
                     "Not an unconditional release. Addendum: unconditional waiver of all claims."):
            for near_only in (False, True):
                self.assertTrue(self.read(text, near_only)["waiver"], text)

    def test_only_mentions_one(self):
        for text in ("CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT. Elm addition. Upon receipt of payment the undersigned will deliver an unconditional waiver and release.",
                     "Conditional waiver and release\nThis waiver becomes unconditional on receipt of payment.",
                     "Waiver and release for Elm.\nThis waiver becomes unconditional on receipt of payment.",
                     "Daily log Elm addition. Sub's unconditional lien waiver received from plumber for last month. Crew of 4.",
                     "Sub's unconditional lien waiver received from plumber for last month.",
                     "Unconditional lien waiver received from plumber for last month.",
                     "Daily log\nCrew of four framed the second floor.\nSix hours.\nInspector came.\nCleaned up.\nWe still owe them an unconditional waiver for last month.",
                     "Conditional waiver and release\nline\nline\nUNCONDITIONAL WAIVER AND RELEASE"):
            got = self.read(text, near_only=True)
            self.assertFalse(got["waiver"], text)
            self.assertTrue(got["mention"], text)
            self.assertIn("unconditional", got["found"].lower())

    def test_filed_as_a_waiver_the_word_alone_is_the_type(self):
        self.assertTrue(self.read("UNCONDITIONAL")["waiver"])
        self.assertFalse(self.read("UNCONDITIONAL", near_only=True)["waiver"])
        self.assertFalse(self.read("UNCONDITIONAL", near_only=True)["mention"])

    def test_taken_back_or_about_something_else_is_neither(self):
        got = self.read("This is not an unconditional release.")
        self.assertEqual((got["waiver"], got["mention"]), (False, False))
        got = self.read("Addendum: unconditional acceptance of the revised scope.", near_only=True)
        self.assertEqual((got["waiver"], got["mention"]), (False, False))

    def test_california_on_an_unconditional_item(self):
        got = doc_check.check([("a.txt", ".txt", CALIFORNIA.encode())], "Elm addition", "$5,000.00", 500_000, "unconditional_progress", True)
        self.assertEqual(got["problems"], [])

    def test_a_conditional_form_on_an_unconditional_item_still_says_so(self):
        got = doc_check.check([("a.txt", ".txt", b"CONDITIONAL WAIVER AND RELEASE. Elm addition $5,000.00")], "Elm addition", "$5,000.00", 500_000, "unconditional_progress", True)
        self.assertIn("It says conditional, not unconditional.", got["problems"])


class SendGates(Base):
    def setUp(self):
        super().setUp()
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            state["company"]["profiles"] = [dict(p, perms=["record_payments", "send_payapps", "send_waivers"]) if p["id"] == "member" else p for p in profiles.profiles(state)]
            server.save_company(self.company, state)
        line = self.job()["sov"]["lines"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        self.assertEqual(status, 200, data)
        self.amount = self.draft()["amount_cents"] / 100

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

    def look(self, rid, cookie=None):
        return self.post(f"/api/jobs/{self.job_id}/unconditional-look/{rid}", {}, cookie=cookie)

    def extra(self, name, text, kind):
        res, data = self.upload(name, text.encode(), kind=kind)
        self.assertEqual(res.status, 200, data)
        rid = data["uploaded_record_ids"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "add_record", "record_id": rid})
        self.assertEqual(status, 200, data)
        return rid

    def test_a_daily_log_that_mentions_one_a_member_looks_and_sends(self):
        rid, item = self.attach("ticket", "log.txt", "Daily log Elm addition. Sub's unconditional lien waiver received from plumber for last month. Crew of 4.", kind="ticket") \
            if any("ticket" in p["label"].lower() for p in self.draft()["packet"]) else (self.extra("log.txt", "Daily log Elm addition. Sub's unconditional lien waiver received from plumber for last month. Crew of 4.", "ticket"), None)
        self.clear_the_rest()
        draft = self.draft()
        self.assertEqual(draft["unconditional_attached"], [])
        row = draft["unconditional_mentions"][0]
        self.assertEqual(row["record_id"], rid)
        self.assertIn("unconditional lien waiver received", row["found"])
        status, data = self.send(cookie=self.bob)
        self.assertEqual(status, 409, data)
        self.assertTrue(data["unconditional_mention"])
        self.assertFalse(data.get("unconditional_waiver"))
        self.assertIn("mentions an unconditional waiver", data["error"])
        self.assertEqual(self.look(rid, cookie=self.bob)[0], 200)
        self.assertEqual(self.draft()["unconditional_mentions"], [])
        status, data = self.send(cookie=self.bob)
        self.assertEqual(status, 200, data)
        rows = [a for a in self.audit() if a["action"] == "unconditional_mention_confirmed"]
        self.assertIn("confirmed a file that mentions an unconditional waiver", rows[0]["detail"])
        self.assertIn("Bob Field", rows[0]["detail"])
        self.assertFalse(any(a["action"] == "unconditional_override" for a in self.audit()))
        self.assertFalse(any("with an unconditional waiver attached" in a["detail"] for a in self.audit()))

    def test_it_right_on_the_waiver_item_counts_for_that_file(self):
        rid, item = self.attach("waiver", "cond.txt", f"CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT. Elm addition. Amount {self.amount:,.2f}. Upon receipt of payment the undersigned will deliver an unconditional waiver and release.",
                                kind="waiver", waiver_kind=self.item("waiver")["waiver_kind"])
        self.assertFalse(item["present"])
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": [rid]}, cookie=self.bob)
        self.assertEqual(status, 200, data)
        self.clear_the_rest()
        status, data = self.send(cookie=self.bob)
        self.assertEqual(status, 200, data)
        self.assertTrue(any(a["action"] == "unconditional_mention_confirmed" for a in self.audit()))

    def test_a_member_without_send_permission_cant_clear_it(self):
        rid = self.extra("log.txt", "Sub's unconditional lien waiver received from plumber.", "note")
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            state["company"]["profiles"] = [dict(p, perms=["record_payments"]) if p["id"] == "member" else p for p in profiles.profiles(state)]
            server.save_company(self.company, state)
        self.assertEqual(self.look(rid, cookie=self.bob)[0], 403)

    def test_another_file_on_the_record_asks_again(self):
        rid = self.extra("log.txt", "Sub's unconditional lien waiver received from plumber.", "note")
        self.assertEqual(self.look(rid)[0], 200)
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            rec = next(r for j in state["jobs"] for r in j["records"] if r["id"] == rid)
            rec["file_ids"] = rec["file_ids"] + ["f-other"]
            state["files"].append({"id": "f-other", "name": "other.txt", "ext": ".txt", "stored": "gone"})
            server.save_company(self.company, state)
        self.assertEqual([r["record_id"] for r in self.draft()["unconditional_mentions"]], [rid])

    def test_a_look_cant_clear_a_real_one(self):
        rid = self.extra("form.txt", "UNCONDITIONAL WAIVER AND RELEASE\n", "other")
        status, data = self.look(rid)
        self.assertEqual(status, 409, data)
        self.clear_the_rest()
        status, data = self.send()
        self.assertEqual(status, 409, data)
        self.assertTrue(data["unconditional_waiver"])

    def test_what_the_last_test_required_still_flags(self):
        for n, kind in enumerate(("note", "photo", "pay_app", "ticket", "supplier_invoice")):
            rid = self.extra(f"f{n}.txt", "UNCONDITIONAL WAIVER AND RELEASE\n", kind)
            self.assertIn(rid, [r["record_id"] for r in self.draft()["unconditional_attached"]], kind)
        rid2, item = self.attach("waiver", "w.txt", f"UNCONDITIONAL WAIVER AND RELEASE UPON PAYMENT. Elm addition {self.amount:,.2f}", kind="waiver", waiver_kind=self.item("waiver")["waiver_kind"])
        self.assertFalse(item["present"], "it waits for a look first")
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": [rid2]})
        self.assertEqual(status, 200, data)
        self.assertIn(rid2, [r["record_id"] for r in self.draft()["unconditional_attached"]], "a person's look doesn't clear the gate")

    def test_the_owners_override_still_says_what_happened(self):
        self.extra("form.txt", "UNCONDITIONAL WAIVER AND RELEASE\n", "other")
        self.clear_the_rest()
        status, data = self.send(unconditional_override="Paid by wire")
        self.assertEqual(status, 200, data)
        row = next(a for a in self.audit() if a["action"] == "unconditional_override")
        self.assertIn("with an unconditional waiver attached", row["detail"])

    def test_an_override_doesnt_pass_a_mention_without_the_look(self):
        rid = self.extra("log.txt", "Sub's unconditional lien waiver received from plumber.", "note")
        self.clear_the_rest()
        status, data = self.send(unconditional_override="Reason")
        self.assertEqual(status, 409, data)
        self.assertTrue(data["unconditional_mention"])
        self.assertEqual(self.draft()["unconditional_mentions"][0]["record_id"], rid)


if __name__ == "__main__":
    unittest.main()
