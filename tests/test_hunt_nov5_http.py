"""The independent hunt of Nov 5, through the server: the override on a file that says unconditional,
New job record's uploads, and the way back from a client's waiver skip."""

import contextlib
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import doc_check
import domain
import helper
import importer
import server
import sov
import storage

PASSWORD = "correct horse battery"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH)
        auth.AUTH_PATH = root / "auth.json"
        server.COMPANIES = root / "companies"
        importer.UPLOADS = root / "uploads"
        storage.DATA_DIR = root
        helper.LOCAL_KB = root / "helper_answers.json"
        helper.LOG_PATH = root / "helper_log.json"
        server.ensure_demo()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH) = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
            hdrs["Content-Length"] = str(len(data))
        if cookie:
            hdrs["Cookie"] = cookie
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        return res, json.loads(payload or b"{}")

    def post(self, path, body=None, cookie=None):
        res, data = self.request("POST", path, body if body is not None else {}, cookie=cookie or self.ann)
        return res.status, data

    def setUp(self):
        type(self).n += 1
        email = f"ann{self.n}@example.com"
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": PASSWORD, "name": "Ann Owner", "company_name": f"Pine {self.n}"})
        self.assertEqual(res.status, 200, data)
        self.ann = res.getheader("Set-Cookie").split(";")[0]
        store = auth._load()
        user = next(u for u in store["users"] if u["email"] == email)
        user["email_verified"] = True
        auth._save(store)
        self.company = user["company_id"]
        bob = f"bob{self.n}@example.com"
        auth.create_user(bob, PASSWORD, "Bob Field", self.company, role="member")
        res, _ = self.request("POST", "/api/auth/login", {"email": bob, "password": PASSWORD})
        self.bob = res.getheader("Set-Cookie").split(";")[0]
        status, snap = self.post("/api/jobs", {"name": "Elm addition", "customer": "Park GC", "address": "9 Elm Street", "approver": "Kim", "ap_email": "ap@park.example",
                                               "contract_cents": 2_000_000, "retainage_pct": 0, "role": "GC", "client_type": "gc", "phone": "(404) 555-0100"})
        self.assertEqual(status, 200, snap)
        self.job_id = snap["jobs"][0]["id"]

    def job(self):
        _, data = self.request("GET", f"/api/jobs/{self.job_id}", cookie=self.ann)
        return data["job"]

    def upload(self, name, data, cookie=None, **query):
        qs = urlencode({"job": self.job_id, "file": "1", **query})
        return self.request("POST", f"/api/import/file?{qs}", raw=data, cookie=cookie or self.ann, headers={"X-Filename": quote(name), "Content-Type": "application/octet-stream"})

    def draft(self, job=None):
        return next((i for i in (job or self.job())["invoices"] if i["status"] in ("draft", "awaiting_approval")), None)


class SaysUnconditional(unittest.TestCase):
    def says(self, text, **kw):
        return doc_check.says_unconditional([("a.txt", ".txt", text.encode())], **kw)

    def test_negated_wording_does_not_trigger(self):
        for text in ("Conditional waiver and release. This is not an unconditional release.",
                     "This document does not constitute an unconditional waiver.",
                     "It is not to be construed as an unconditional release of lien rights.",
                     "This isn't an UNCONDITIONAL waiver."):
            self.assertFalse(self.says(text), text)

    def test_the_plain_word_still_does(self):
        for text in ("UNCONDITIONAL WAIVER AND RELEASE", "Un-conditional waiver",
                     "This release is not conditional and is an unconditional waiver.",
                     "Not an unconditional release. Addendum: unconditional waiver of all claims."):
            self.assertTrue(self.says(text), text)

    def test_filed_as_anything_else_the_word_has_to_be_near_waiver_release_or_lien(self):
        self.assertFalse(self.says("Unconditional acceptance of the change in scope, with no conditions at all on this waiver.", near_only=True))
        for text in ("Unconditional waiver of lien.", "Waiver and release, unconditional.", "UNCONDITIONAL\nWAIVER AND RELEASE"):
            self.assertTrue(self.says(text, near_only=True), text)

    def test_a_prompt_to_look_at_confirm_stays(self):
        got = doc_check.check([("a.txt", ".txt", b"Conditional waiver. This is not an unconditional release.")], "", "", None, "conditional_progress", True)
        self.assertIn("It says unconditional, not conditional.", got["problems"])


class FileThatSaysUnconditional(Base):
    """Item 5: a Send 409 with nothing on the page to get past it."""

    def setUp(self):
        super().setUp()
        job = self.job()
        line = job["sov"]["lines"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        self.assertEqual(status, 200, data)
        for item in self.draft()["packet"]:
            if item["required"] and not item["present"]:
                status, data = self.post(f"/api/jobs/{self.job_id}/resolve/{item['id']}", {"reason": "In hand"})
                self.assertEqual(status, 200, data)

    def add(self, text, label="release.txt", kind="waiver"):
        res, data = self.upload(label, text, kind=kind)
        self.assertEqual(res.status, 200, data)
        rid = data["uploaded_record_ids"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "add_record", "record_id": rid})
        self.assertEqual(status, 200, data)
        return rid

    def send(self, cookie=None, **body):
        draft = self.draft()
        return self.post(f"/api/jobs/{self.job_id}/send", {"reviewed": draft["email_preview"]["stamp"], **body}, cookie=cookie)

    def test_a_negated_form_sends(self):
        self.add(b"Conditional waiver and release upon progress payment. This is not an unconditional release.\n")
        status, data = self.send()
        self.assertEqual(status, 200, data)

    def test_the_page_names_the_file_and_an_owner_sends_with_a_reason(self):
        rid = self.add(b"UNCONDITIONAL WAIVER AND RELEASE\n", label="signed-form.txt")
        rows = self.draft()["unconditional_attached"]
        self.assertEqual([r["record_id"] for r in rows], [rid])
        self.assertIn("signed-form.txt", rows[0]["file_names"])
        self.assertTrue(rows[0]["extra"])
        status, data = self.send()
        self.assertEqual(status, 409)
        self.assertTrue(data["unconditional_waiver"])
        status, data = self.send(unconditional_override="Paid by wire")
        self.assertEqual(status, 200, data)
        sent = next(i for i in self.job()["invoices"] if i["status"] == "sent")
        self.assertEqual(sent["unconditional_override"]["reason"], "Paid by wire")
        self.assertEqual(sent["unconditional_override"]["by"], "Ann Owner")

    def test_a_member_is_told_who_to_ask_and_can_take_it_off(self):
        rid = self.add(b"UNCONDITIONAL WAIVER AND RELEASE\n")
        status, data = self.send(cookie=self.bob, unconditional_override="Because")
        self.assertIn(status, (403, 409), "a member doesn't send it this way")
        _, me = self.request("GET", "/api/auth/me", cookie=self.bob)
        self.assertIn("Ann Owner", (me.get("user") or me)["owner_names"])
        status, data = self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "remove_record", "record_id": rid}, cookie=self.bob)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.draft()["unconditional_attached"], [])

    def test_a_file_only_read_at_send_is_on_the_page_after_the_refusal(self):
        rid = self.add(b"UNCONDITIONAL WAIVER AND RELEASE\n")
        state = server.load_company(self.company)
        job = next(j for j in state["jobs"] if j["id"] == self.job_id)
        next(r for r in job["records"] if r["id"] == rid).pop("says_unconditional", None)   # added before this was read
        server.save_company(self.company, state)
        self.assertEqual(self.draft()["unconditional_attached"], [])
        status, _ = self.send()
        self.assertEqual(status, 409)
        self.assertEqual([r["record_id"] for r in self.draft()["unconditional_attached"]], [rid])

    def test_what_a_refusal_keeps_can_not_overwrite_another_request(self):
        """Another request gets the lock the moment the refused send lets go of it: its change stands."""
        self.add(b"UNCONDITIONAL WAIVER AND RELEASE\n")
        real, company = server.company_lock, self.company

        @contextlib.contextmanager
        def lock(company_id):
            try:
                with real(company_id):
                    yield
            except domain.GateError:
                with real(company):
                    state = server.load_company(company)
                    state["company"]["name"] = "Changed in the gap"
                    server.save_company(company, state)
                raise

        with mock.patch.object(server, "company_lock", lock):
            status, _ = self.send()
        self.assertEqual(status, 409)
        state = server.load_company(company)
        self.assertEqual(state["company"]["name"], "Changed in the gap", "the other request's change is still there")
        job = next(j for j in state["jobs"] if j["id"] == self.job_id)
        self.assertTrue(any(r.get("says_unconditional") for r in job["records"]), "what the refusal read is kept too")

    def test_an_addendum_filed_as_other_is_not_a_false_alarm(self):
        self.add(b"Addendum: unconditional acceptance of the revised scope.\n", label="addendum.txt", kind="other")
        status, data = self.send()
        self.assertEqual(status, 200, data)


class NewJobRecordUploads(Base):
    """Item 7: the form tracks the record its upload made, and a stale id never sticks."""

    def entries(self, ids, **kw):
        return {"notes": "", "today": "2026-11-05", "entries": [{"section": "delivery", "row": 0, "supplier": "Acme", "attachments": ids}], **kw}

    def save(self, body):
        return self.post(f"/api/jobs/{self.job_id}/daily-record", body)

    def test_the_answer_names_this_upload_not_bobs(self):
        res, bob = self.upload("site.jpg", b"\xff\xd8\xff bob", cookie=self.bob, kind="photo")
        self.assertEqual(res.status, 200, bob)
        res, ann = self.upload("ticket.pdf", b"%PDF ann", for_job_record="1", kind="ticket")
        self.assertEqual(res.status, 200, ann)
        self.assertEqual(len(ann["uploaded_record_ids"]), 1)
        self.assertNotIn(bob["uploaded_record_ids"][0], ann["uploaded_record_ids"])
        status, data = self.save(self.entries(ann["uploaded_record_ids"]))
        self.assertEqual(status, 200, data)
        self.assertEqual(data["not_added"], [])

    def test_another_persons_record_comes_off_and_the_rest_saves(self):
        _, bob = self.upload("site.jpg", b"\xff\xd8\xff bob", cookie=self.bob, kind="photo")
        _, ann = self.upload("ticket.pdf", b"%PDF ann", for_job_record="1", kind="ticket")
        status, data = self.save(self.entries(bob["uploaded_record_ids"] + ann["uploaded_record_ids"]))
        self.assertEqual(status, 200, data)
        self.assertEqual([x["record_id"] for x in data["not_added"]], bob["uploaded_record_ids"])
        rec = {r["id"]: r for r in self.job()["records"]}
        self.assertTrue(rec[ann["uploaded_record_ids"][0]]["label"].startswith("Delivery ticket"), rec[ann["uploaded_record_ids"][0]]["label"])
        self.assertEqual(rec[bob["uploaded_record_ids"][0]]["label"], "site.jpg", "Bob's file stays as he left it")

    def test_only_anothers_record_says_so_and_names_it_for_the_form(self):
        _, bob = self.upload("site.jpg", b"\xff\xd8\xff bob", cookie=self.bob, kind="photo")
        status, data = self.save({"notes": "", "today": "2026-11-05", "entries": [{"section": "photos", "row": 0, "attachments": bob["uploaded_record_ids"]}]})
        self.assertEqual(status, 409)
        self.assertEqual(data["stale_record_ids"], bob["uploaded_record_ids"])
        self.assertIn("stays on the job records", data["fields"]["attachments"])

    def test_a_retry_after_a_lost_answer_saves_nothing_twice(self):
        _, ann = self.upload("t2.pdf", b"%PDF t2", for_job_record="1", kind="ticket")
        body = self.entries(ann["uploaded_record_ids"], notes="Poured the slab")
        status, data = self.save(body)
        self.assertEqual(status, 200, data)
        before = (len(self.job()["records"]), len(data.get("daily_records") or []))
        status, data = self.save(body)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["already_saved"], ann["uploaded_record_ids"])
        self.assertEqual(len(self.job()["records"]), before[0], "nothing is written twice")
        state = server.load_company(self.company)
        self.assertEqual(sum(1 for d in state["daily_records"] if d["notes"] == "Poured the slab"), 1)

    def test_a_page_from_before_this_deploy_is_not_stuck(self):
        _, up = self.upload("t3.pdf", b"%PDF t3", kind="ticket")   # no for_job_record
        status, data = self.save(self.entries(up["uploaded_record_ids"]))
        self.assertEqual(status, 200, data)
        self.assertEqual([x["record_id"] for x in data["not_added"]], up["uploaded_record_ids"])
        self.assertTrue(any(r["id"] == up["uploaded_record_ids"][0] for r in self.job()["records"]), "the file stays on the job records")

    def test_a_record_that_is_gone_comes_off(self):
        _, ann = self.upload("t4.pdf", b"%PDF t4", for_job_record="1", kind="ticket")
        status, data = self.save(self.entries(ann["uploaded_record_ids"] + ["rec-gone"]))
        self.assertEqual(status, 200, data)
        self.assertEqual([x["record_id"] for x in data["not_added"]], ["rec-gone"])


class EveryWaiverFileIsRead(Base):
    """Item 15: what goes through the waiver step is read, whatever its label or type says."""

    RIGHT = b"Elm addition. Unconditional waiver and release on progress payment. Amount $5,000.00."

    def setUp(self):
        super().setUp()
        job = self.job()
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {job["sov"]["lines"][0]["id"]: "0.25"}})
        self.assertEqual(status, 200, data)
        draft = self.draft()
        for item in draft["packet"]:
            if item["required"] and not item["present"]:
                self.post(f"/api/jobs/{self.job_id}/resolve/{item['id']}", {"reason": "In hand"})
        status, data = self.post(f"/api/jobs/{self.job_id}/send", {"reviewed": self.draft()["email_preview"]["stamp"]})
        self.assertEqual(status, 200, data)
        self.invoice = self.job()["invoices"][-1]
        status, data = self.post(f"/api/jobs/{self.job_id}/match-payment", {"amount_cents": self.invoice["amount_cents"], "reference": "c", "invoice_id": self.invoice["id"]})
        self.assertEqual(status, 200, data)

    def choose(self, name, data):
        res, up = self.upload(name, data, kind="waiver", waiver_kind="unconditional_progress", waiver_for=self.invoice["id"])
        self.assertEqual(res.status, 200, up)
        return up["uploaded_record_ids"][0]

    def waiver(self, **extra):
        return self.post(f"/api/jobs/{self.job_id}/waiver", {"invoice_id": self.invoice["id"], "kind": "unconditional_progress", **extra})

    def test_the_wrong_words_warn_and_a_look_sends_it(self):
        rid = self.choose("conditional.txt", b"Elm addition $5,000.00 CONDITIONAL WAIVER AND RELEASE")
        emails = len(server.load_company(self.company)["email_log"])
        status, data = self.waiver()
        self.assertEqual(status, 409)
        self.assertTrue(data["look_warning"])
        self.assertIn("It says conditional, not unconditional.", data["error"])
        self.assertEqual(data["confirm_label"], "I opened it and it's right")
        self.assertEqual(len(server.load_company(self.company)["email_log"]), emails, "no email yet")
        status, data = self.waiver(looked_at=rid)
        self.assertEqual(status, 200, data)
        state = server.load_company(self.company)
        row = next(a for a in state["audit"] if a["action"] == "waiver_looked_at")
        self.assertEqual(row["actor"], "Ann Owner")
        self.assertIn("It says conditional", row["detail"])

    def test_blank_a_letter_and_a_title_all_warn(self):
        for name, data, says in (("blank.txt", b" ", "looks blank"), ("lunch.txt", b"Thanks for lunch on Tuesday at the Oak Grill, see you soon.", "Job name not found"),
                                 ("Unconditional waiver.txt", b"", "looks blank")):
            rid = self.choose(name, data or b"\n")
            status, reply = self.waiver()
            self.assertEqual(status, 409, name)
            self.assertIn(says, reply["error"], name)
            self.assertEqual(reply["record_id"], rid)
            state = server.load_company(self.company)
            job = next(j for j in state["jobs"] if j["id"] == self.job_id)
            job["records"] = [r for r in job["records"] if r["id"] != rid]
            server.save_company(self.company, state)

    def test_a_scan_asks_for_a_look(self):
        rid = self.choose("scan.jpg", b"\xff\xd8\xff\xe0 scan")
        status, data = self.waiver()
        self.assertEqual(status, 409)
        self.assertIn("couldn't be checked", data["error"])
        status, data = self.waiver(looked_at=rid)
        self.assertEqual(status, 200, data)

    def test_a_look_at_another_file_does_not_count(self):
        self.choose("conditional.txt", b"Elm addition $5,000.00 CONDITIONAL WAIVER AND RELEASE")
        status, data = self.waiver(looked_at="rec-other")
        self.assertEqual(status, 409)
        self.assertTrue(data["look_warning"])

    def test_the_right_file_goes_with_no_question(self):
        self.choose("signed.txt", self.RIGHT)
        status, data = self.waiver()
        self.assertEqual(status, 200, data)


class UnconditionalInAnyKind(FileThatSaysUnconditional):
    """Item 16: a form filed as a note, a ticket or a photo, added to the email, still warns."""

    def test_every_kind_but_the_contract_papers(self):
        for kind in ("note", "pay_app", "ticket", "supplier_invoice"):
            rid = self.add(b"UNCONDITIONAL WAIVER AND RELEASE\n", label=f"{kind}.txt", kind=kind)
            status, data = self.send()
            self.assertEqual(status, 409, kind)
            self.assertTrue(data["unconditional_waiver"], kind)
            self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "remove_record", "record_id": rid})

    def test_a_contract_is_not_read(self):
        self.add(b"Sub agrees to an unconditional release of lien on final payment.\n", label="contract.txt", kind="contract")
        status, data = self.send()
        self.assertEqual(status, 200, data)

    def test_a_file_labelled_conditional_that_reads_unconditional_warns(self):
        self.add(b"UNCONDITIONAL WAIVER AND RELEASE\n", label="Conditional waiver.txt")
        status, data = self.send()
        self.assertEqual(status, 409)
        self.assertTrue(data["unconditional_waiver"])


class StaleReviewWithAWaiver(Base):
    """Item 17: a stale screen hears the pay app changed, not the blockers of the new draft."""

    def test_the_stamp_comes_first(self):
        job = self.job()
        line = job["sov"]["lines"][0]
        self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        for item in self.draft()["packet"]:
            if item["required"] and not item["present"] and not item.get("waiver_kind"):
                self.post(f"/api/jobs/{self.job_id}/resolve/{item['id']}", {"reason": "In hand"})
        res, up = self.upload("signed.txt", b"Elm addition $5,000.00 Conditional waiver on progress payment", kind="waiver")
        item = next(p for p in self.draft()["packet"] if p.get("waiver_kind"))
        status, data = self.post(f"/api/jobs/{self.job_id}/attach-record/{item['id']}", {"record_id": up["uploaded_record_ids"][0], "looked": True})
        self.assertEqual(status, 200, data)
        stale = self.draft()["email_preview"]["stamp"]
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.3"}})
        self.assertEqual(status, 200, data)
        self.assertNotEqual(self.draft()["email_preview"]["stamp"], stale)
        self.assertEqual(self.draft()["status"], "draft", "the new amount asks for another look at the waiver")
        status, data = self.post(f"/api/jobs/{self.job_id}/send", {"reviewed": stale})
        self.assertEqual(status, 409)
        self.assertTrue(data["review_changed"], data)
        self.assertEqual(data["invoice_id"], self.draft()["id"])


class TooLarge(Base):
    """Item 21: a body over the limit gets its plain answer, not a broken connection."""

    def test_the_answer_arrives(self):
        res, data = self.request("POST", "/api/import/quantities/apply", {"job_id": self.job_id, "xlsx": "A" * 1_500_000}, cookie=self.ann)
        self.assertEqual(res.status, 413)
        self.assertIn("too large", data["error"])
        self.assertIn("CSV", data["error"])


class QuantitiesReadBeforeTheLock(Base):
    """Item 14: the sheet is read before the company's lock, and a bad one still gets its answer."""

    def test_a_bad_sheet_is_refused_and_a_good_one_applies(self):
        import base64
        status, data = self.post("/api/import/quantities/apply", {"job_id": self.job_id, "xlsx": base64.b64encode(b"not excel").decode(), "basis": ""})
        self.assertEqual(status, 409)
        self.assertIn("Save it as CSV", data["error"])
        status, data = self.post("/api/import/quantities/apply", {"job_id": self.job_id, "csv": "", "basis": ""})
        self.assertEqual(status, 409)
        self.assertIn("Choose a CSV", data["error"])


class ClientWaiverSkip(Base):
    """Item 10: Not needed for this client has a way back on the client's card, audited."""

    def setUp(self):
        super().setUp()
        job = self.job()
        self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {job["sov"]["lines"][0]["id"]: "0.25"}})
        self.item = next(p for p in self.draft()["packet"] if p.get("waiver_kind"))
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-skip/{self.item['id']}", {"reason": "They never ask", "whole_client": True})
        self.assertEqual(status, 200, data)
        self.payer_id = job["payer_id"]

    def payer(self):
        state = server.load_company(self.company)
        return next(p for p in state["payers"] if p["id"] == self.payer_id), state

    def test_turned_back_on(self):
        self.assertEqual(self.payer()[0]["waiver_not_needed"]["reason"], "They never ask")
        status, data = self.post(f"/api/payers/{self.payer_id}/waiver-needed")
        self.assertEqual(status, 200, data)
        payer, state = self.payer()
        self.assertNotIn("waiver_not_needed", payer)
        row = next(a for a in state["audit"] if a["action"] == "paperwork_needed")
        self.assertEqual(row["actor"], "Ann Owner")
        self.assertIn("They never ask", row["detail"])
        item = next(p for p in self.draft()["packet"] if p["id"] == self.item["id"])
        self.assertTrue(item["required"], "the open draft asks again")
        self.assertFalse(item["present"])

    def test_not_marked_is_refused(self):
        self.post(f"/api/payers/{self.payer_id}/waiver-needed")
        status, data = self.post(f"/api/payers/{self.payer_id}/waiver-needed")
        self.assertEqual(status, 409)
        self.assertIn("aren't marked", data["error"])


class ClientsOwnNameAsSigner(Base):
    """Item 13: the client's own name typed over a real signer is said to need confirming."""

    def test_the_reply_says_so_and_the_same_save_can_confirm(self):
        payer_id = self.job()["payer_id"]
        status, data = self.post(f"/api/payers/{payer_id}/update", {"approver": "Park GC"})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["signer_needs_confirming"])
        status, data = self.post(f"/api/payers/{payer_id}/update", {"approver": "Park GC", "approver_confirmed": True})
        self.assertEqual(status, 200, data)
        self.assertNotIn("signer_needs_confirming", data)


if __name__ == "__main__":
    unittest.main()
