"""OCR through the server (ocr.py, with the stand-in programs of ocr_help.py): a scan uploaded to a
waiver item is read in the background and sets only the answer picked first, the person's answer
still decides and the owner's gate is as it was; text that comes after an answer re-asks nothing;
the other upload paths queue too; the demo's files and another company's text stay out of reach."""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import contract_import
import domain
import ocr
import server
from domain import profiles
from ocr_help import SCAN, WAIVER_TEXT, StandIns, clean_env, png_header, text_pdf
from test_hunt_nov5_http import PASSWORD, Base

UNCONDITIONAL_TEXT = "UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\nProject: Elm addition\nThe undersigned has been paid and waives and releases its lien rights.\n"
CONTRACT_TEXT = "SUBCONTRACT AGREEMENT\nProject: Elm addition\nThe Contract Sum is $125,000.00. Retainage of 10% shall be withheld from each payment.\n"


class WithOcr(Base):
    """Base's company and job, a draft pay app, OCR on and the stand-ins as its programs. Nothing
    reads in the background here: a test calls ocr.run_waiting() where the worker would."""

    def setUp(self):
        super().setUp()
        self.stand = StandIns(Path(self.tmp.name) / f"bin-{self.n}")
        patch = mock.patch.dict(os.environ, {**clean_env(), **self.stand.env()}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        hook = ocr._AFTER
        ocr.after_read(server._ocr_text_arrived)
        ocr._PENDING.clear()
        self.addCleanup(ocr.after_read, hook)
        self.addCleanup(ocr._PENDING.clear)
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            state["company"]["profiles"] = [dict(p, perms=["record_payments", "send_payapps", "send_waivers"]) if p["id"] == "member" else p for p in profiles.profiles(state)]
            server.save_company(self.company, state)
        line = self.job()["sov"]["lines"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        self.assertEqual(status, 200, data)

    def waiver_item(self):
        return next(p for p in self.draft()["packet"] if p.get("waiver_kind"))

    def record(self, rid):
        with server.company_lock(self.company):
            state = server.load_company(self.company)
        return next(r for j in state["jobs"] for r in j["records"] if r["id"] == rid)

    def scan_on_the_waiver_item(self, name="Signed waiver scan.pdf", data=SCAN):
        item = self.waiver_item()
        res, got = self.upload(name, data, attach=item["id"], kind="waiver")
        self.assertEqual(res.status, 200, got)
        return got["uploaded_record_ids"][0], item

    def clear_the_rest(self):
        for item in self.draft()["packet"]:
            if item["required"] and not item["present"]:
                status, data = self.post(f"/api/jobs/{self.job_id}/resolve/{item['id']}", {"reason": "In hand"})
                self.assertEqual(status, 200, data)

    def send(self, cookie=None, **body):
        return self.post(f"/api/jobs/{self.job_id}/send", {"reviewed": self.draft()["email_preview"]["stamp"], **body}, cookie=cookie)

    def health(self):
        return self.request("GET", "/healthz")[1]


class AScannedWaiver(WithOcr):
    def test_before_the_read_it_couldnt_be_checked_and_after_it_the_text_sets_only_the_answer_picked_first(self):
        amount = domain.money(self.draft()["amount_cents"])
        self.stand.say(WAIVER_TEXT + f"Amount of this payment: {amount}\n")
        rid, item = self.scan_on_the_waiver_item()

        # Before the read: as it always was for a scan.
        item = self.waiver_item()
        self.assertFalse(item["present"])
        self.assertEqual(item["check"], {"status": "unchecked", "problems": ["Signed waiver scan.pdf is a scan, so it couldn't be checked."]})
        self.assertEqual(item["waiver_question"], {"guess": "", "reads_as": ""})
        self.assertEqual((self.health()["ocr"], self.health()["ocr_queue"]), ("ready", 1))
        self.assertEqual([(j["state"], j["company_id"]) for j in ocr.queue(self.company)], [("waiting", self.company)])

        self.assertEqual(ocr.run_waiting(), 1)   # the worker

        # After it: the question's first answer is what the scan reads as. Nothing else moved.
        after = self.waiver_item()
        self.assertEqual(after["waiver_question"], {"guess": "conditional", "reads_as": "conditional"})
        self.assertFalse(after["present"], "it still waits for a person")
        self.assertEqual(after["check"], item["check"], "the item's check is not run again by the text arriving")
        self.assertEqual(self.health()["ocr_queue"], 0)
        self.assertNotIn("waiver_answer", self.record(rid))

        # What Confirm reads first now compares the scan's text with the job and the amount.
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-recheck/{item['id']}", {"shown": [rid]})
        self.assertEqual((status, data["check_status"], data["check_problems"]), (200, "matched", []))

        # The person's answer decides, whatever the text reads as: the other one, here, by a member.
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"shown": [rid], "answer": "unconditional"}, cookie=self.bob)
        self.assertEqual(status, 200, data)
        rec = self.record(rid)
        self.assertEqual((rec["waiver_answer"]["kind"], rec["waiver_answer"]["by"], rec["waiver_kind"]), ("unconditional", "Bob Field", "unconditional_progress"))
        self.assertTrue(self.waiver_item()["present"])
        # And the owner's gate is as it was: an unconditional waiver on a pay app takes an owner and a reason.
        self.assertIn(rid, [r["record_id"] for r in self.draft()["unconditional_attached"]])
        self.clear_the_rest()
        status, data = self.send(cookie=self.bob)
        self.assertEqual(status, 409, data)
        status, data = self.send(cookie=self.ann)
        self.assertEqual(status, 409, data)
        self.assertTrue(data.get("unconditional_waiver"), data)

    def test_text_that_comes_after_the_answer_asks_nothing_again(self):
        self.stand.say(UNCONDITIONAL_TEXT)
        rid, item = self.scan_on_the_waiver_item()
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"shown": [rid], "answer": "conditional"})
        self.assertEqual(status, 200, data)
        before = self.waiver_item()
        self.assertTrue(before["present"])
        answered = self.record(rid)["waiver_answer"]

        self.assertEqual(ocr.run_waiting(), 1)

        self.assertEqual(self.record(rid)["waiver_read"]["guess"], "unconditional", "the text is read")
        after = self.waiver_item()
        self.assertEqual({k: after.get(k) for k in ("present", "confirmed_by", "confirmed_at", "looked_as", "check", "attachments")},
                         {k: before.get(k) for k in ("present", "confirmed_by", "confirmed_at", "looked_as", "check", "attachments")})
        self.assertNotIn("waiver_question", after)
        self.assertEqual(self.record(rid)["waiver_answer"], answered)
        draft = self.draft()
        self.assertEqual((draft["waiver_questions"], draft["unconditional_attached"]), ([], []))
        self.clear_the_rest()
        status, data = self.send()
        self.assertEqual(status, 200, data)

    def test_with_ocr_off_a_scan_is_as_it_was(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            rid, item = self.scan_on_the_waiver_item()
            self.assertEqual((self.health()["ocr"], self.health()["ocr_queue"]), ("off", 0))
            self.assertEqual(ocr.run_waiting(), 0)
        self.assertEqual(ocr.queue(self.company), [])
        self.assertEqual(self.waiver_item()["waiver_question"], {"guess": "", "reads_as": ""})

    def test_a_scan_that_cant_be_read_stays_for_a_person(self):
        self.stand.set("mode", "fail")
        rid, item = self.scan_on_the_waiver_item()
        self.assertEqual(ocr.run_waiting(), 1)
        self.assertEqual([j["state"] for j in ocr.queue(self.company)], ["unread"])
        after = self.waiver_item()
        self.assertEqual((after["waiver_question"], after["check"]["status"]), ({"guess": "", "reads_as": ""}, "unchecked"))
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"shown": [rid], "answer": "conditional"})
        self.assertEqual((status, self.waiver_item()["present"]), (200, True))

    def test_a_photo_on_the_waiver_item_is_read_too(self):
        rid, item = self.scan_on_the_waiver_item("IMG_0412.png", png_header(1200, 1600))
        self.assertEqual(self.waiver_item()["check"]["problems"], ["IMG_0412.png is a photo or a scan, so it couldn't be checked."])
        ocr.run_waiting()
        self.assertEqual(self.waiver_item()["waiver_question"]["guess"], "conditional")

    def test_text_stored_between_the_read_and_the_lock_is_not_lost(self):
        # The file was read (no text) before the company's lock; its text arrives; the change then
        # runs with that earlier reading in hand. The reader looks again under the lock.
        res, got = self.upload("scan.pdf", SCAN, kind="waiver")
        rid = got["uploaded_record_ids"][0]
        file_id = self.record(rid)["file_ids"][0]
        ahead = server._read_ahead(self.company, self.job_id, self.waiver_item()["id"], rid)
        self.assertTrue(ahead[file_id].text == "" and not ahead[file_id].ocr)
        ocr.run_waiting()
        read = server._file_reader(self.company, ahead)({"id": file_id, "ext": ".pdf", "name": "scan.pdf"})
        self.assertEqual((read.text, read.ocr), (WAIVER_TEXT.strip(), True))
        typed = server._read_stored(self.company, {"id": file_id, "ext": ".pdf"})
        self.assertIs(server._file_reader(self.company, {file_id: typed})({"id": file_id}), typed, "a reading with text is used as it is")


class AFileGoingOut(WithOcr):
    def test_a_scan_filed_as_something_else_is_asked_about_once_its_text_reads_as_a_waiver(self):
        res, got = self.upload("scan-0042.pdf", SCAN, kind="insurance")
        self.assertEqual(res.status, 200, got)
        rid = got["uploaded_record_ids"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "add_record", "record_id": rid})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.draft()["waiver_questions"], [], "nothing to read yet, so nothing is asked")
        ocr.run_waiting()
        asked = self.draft()["waiver_questions"]
        self.assertEqual([(q["record_id"], q["guess"]) for q in asked], [(rid, "conditional")])
        self.clear_the_rest()
        waiver = self.waiver_item()
        if not waiver["present"]:
            self.post(f"/api/jobs/{self.job_id}/resolve/{waiver['id']}", {"reason": "In hand"})
        status, data = self.send()
        self.assertEqual(status, 409, data)
        self.assertTrue(data.get("waiver_question"), data)
        status, data = self.post(f"/api/jobs/{self.job_id}/waiver-answer/{rid}", {"answer": "no", "shown": asked[0]["file_ids"]})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.draft()["waiver_questions"], [])

    def test_a_scan_read_before_it_is_added_is_asked_about_straight_away(self):
        res, got = self.upload("scan-0043.pdf", SCAN, kind="note")
        rid = got["uploaded_record_ids"][0]
        ocr.run_waiting()
        status, data = self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "add_record", "record_id": rid})
        self.assertEqual(status, 200, data)
        self.assertEqual([q["record_id"] for q in self.draft()["waiver_questions"]], [rid])

    def test_a_read_only_company_is_left_alone_when_text_arrives(self):
        res, got = self.upload("scan-0044.pdf", SCAN, kind="note")
        rid = got["uploaded_record_ids"][0]
        self.post(f"/api/jobs/{self.job_id}/payapp/{self.draft()['id']}/attachments", {"action": "add_record", "record_id": rid})
        with mock.patch.object(server, "read_only_reason", return_value="This company is read-only."):
            ocr.run_waiting()
        self.assertEqual(self.record(rid)["waiver_read"]["form"], False)
        self.assertEqual([j["state"] for j in ocr.queue(self.company)], ["read"], "the text is kept all the same")


class TheInbox(WithOcr):
    def inbox(self):
        return self.request("GET", "/api/state", cookie=self.ann)[1]["inbox"]

    def to_inbox(self, name, data=SCAN):
        res, state = self.request("POST", "/api/import/file", raw=data, cookie=self.ann, headers={"X-Filename": name, "Content-Type": "application/octet-stream"})
        self.assertEqual(res.status, 200, state)
        return state["inbox"][-1]

    def test_a_scan_with_no_kind_in_its_name_is_suggested_by_its_text(self):
        self.stand.say("CERTIFICATE OF LIABILITY INSURANCE\nProducer: Acme Insurance Agency\n")
        item = self.to_inbox("scan-0042.pdf")
        named = self.to_inbox("Elm contract signed.pdf")
        self.assertEqual((item["kind"], named["kind"]), ("note", "contract"))
        self.assertEqual(ocr.run_waiting(), 2)
        kinds = {i["id"]: (i["kind"], i["status"]) for i in self.inbox()}
        self.assertEqual((kinds[item["id"]], kinds[named["id"]]), (("insurance", "new"), ("contract", "new")), "a kind the name gave stands")

    def test_what_a_person_filed_is_not_touched(self):
        self.stand.say("CERTIFICATE OF LIABILITY INSURANCE\n")
        item = self.to_inbox("scan-0050.pdf")
        status, data = self.post(f"/api/inbox/{item['id']}/file", {"job_id": self.job_id, "kind": "note"})
        self.assertEqual(status, 200, data)
        ocr.run_waiting()
        filed = next(i for i in self.inbox() if i["id"] == item["id"])
        self.assertEqual((filed["kind"], filed["filed_kind"], filed["status"]), ("note", "note", "filed"))
        self.assertEqual(self.record(filed["record_ids"][0])["kind"], "note")

    def test_a_file_with_text_is_not_queued(self):
        self.to_inbox("typed.pdf", text_pdf())
        self.to_inbox("notes.txt", b"Call the GC about the invoice.")
        self.assertEqual((ocr.queue(self.company), self.health()["ocr_queue"]), ([], 0))

    def test_what_a_phone_shares_and_what_comes_by_email_are_queued(self):
        boundary = "----duracash"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nFrom the truck\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"IMG_1.png\"\r\nContent-Type: image/png\r\n\r\n").encode() \
            + png_header(640, 480) + f"\r\n--{boundary}--\r\n".encode()
        res, _ = self.request("POST", "/share", raw=body, cookie=self.ann, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(res.status, 303)
        _, settings = self.request("GET", "/api/import/settings", cookie=self.ann)
        eml = (b"From: Kim Park <ap@park.example>\r\nSubject: Signed paperwork\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=XYZ\r\n\r\n"
               b"--XYZ\r\nContent-Type: text/plain\r\n\r\nAttached.\r\n"
               b"--XYZ\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"scan.pdf\"\r\n\r\n" + SCAN + b"\r\n--XYZ--\r\n")
        res, data = self.request("POST", "/api/inbound/email/" + settings["email_webhook"].rsplit("/", 1)[-1], raw=eml, headers={"Content-Type": "message/rfc822"})
        self.assertEqual(res.status, 200, data)
        self.assertEqual(sorted(j["ext"] for j in ocr.queue(self.company) if j["state"] == "waiting"), [".pdf", ".png"])

    def test_a_refused_upload_queues_nothing(self):
        res, data = self.upload("scan.pdf", SCAN, attach="pkt-nope", kind="waiver")
        self.assertEqual(res.status, 409, data)
        self.assertEqual(ocr.queue(self.company), [])


class AScannedContract(WithOcr):
    def read_contract(self, cookie, data=SCAN):
        res, got = self.request("POST", "/api/contract/read", raw=data, cookie=cookie, headers={"X-Filename": "Subcontract.pdf", "Content-Type": "application/pdf"})
        self.assertEqual(res.status, 200, got)
        return got

    def test_the_form_is_filled_from_the_text_of_the_same_file_on_the_companys_records(self):
        self.stand.say(CONTRACT_TEXT)
        self.assertEqual(self.read_contract(self.ann), {"ok": False, "error": contract_import.NO_TEXT})
        res, got = self.upload("Subcontract.pdf", SCAN, kind="contract")
        self.assertEqual(res.status, 200, got)
        self.assertEqual(self.read_contract(self.ann), {"ok": False, "error": contract_import.NO_TEXT}, "not read yet: as before")
        ocr.run_waiting()
        got = self.read_contract(self.ann)
        self.assertEqual((got["ok"], got["fields"]["contract_cents"], got["fields"]["retainage_pct"]), (True, 12_500_000, 10))
        self.assertEqual(self.read_contract(self.ann, SCAN + b"\n")["ok"], False, "another file's bytes are another file")

        # Another company sending the very same bytes gets nothing of this company's text.
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": f"eve{self.n}@example.com", "password": PASSWORD, "name": "Eve Other", "company_name": "Other Co"})
        eve = res.getheader("Set-Cookie").split(";")[0]
        self.assertEqual(self.read_contract(eve), {"ok": False, "error": contract_import.NO_TEXT})
        with mock.patch.dict(os.environ, {"OCR_ENABLED": "0"}):
            self.assertEqual(self.read_contract(self.ann), {"ok": False, "error": contract_import.NO_TEXT})


class OutOfReach(WithOcr):
    def test_the_demo_companys_uploads_are_never_queued(self):
        res, _ = self.request("POST", "/api/auth/login", {"email": auth.DEMO_EMAIL, "password": auth.DEMO_PASSWORD})
        demo = res.getheader("Set-Cookie").split(";")[0]
        res, state = self.request("POST", "/api/import/file", raw=SCAN, cookie=demo, headers={"X-Filename": "scan.pdf", "Content-Type": "application/pdf"})
        self.assertEqual(res.status, 200, state)
        self.assertEqual((ocr.queue(auth.DEMO_COMPANY_ID), ocr.run_waiting(), self.health()["ocr_queue"]), ([], 0, 0))
        self.assertEqual(self.stand.calls("pdftoppm"), [])

    def test_no_route_serves_the_text_or_the_queue(self):
        res, got = self.upload("scan.pdf", SCAN, kind="note")
        file_id = self.record(got["uploaded_record_ids"][0])["file_ids"][0]
        ocr.run_waiting()
        self.assertTrue(ocr.text_for(self.company, file_id))
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": f"mal{self.n}@example.com", "password": PASSWORD, "name": "Mal Other", "company_name": "Other Co"})
        mal = res.getheader("Set-Cookie").split(";")[0]
        other = next(u["company_id"] for u in auth._load()["users"] if u["email"] == f"mal{self.n}@example.com")
        for cookie in (self.ann, mal):
            for name in (f"{file_id}.ocr.json", "ocr-queue.json", f"{file_id}.ocr", f"..%2F{self.company}%2F{file_id}.ocr.json"):
                res, _ = self.request("GET", f"/api/files/{name}", cookie=cookie)
                self.assertEqual(res.status, 404, name)
        res, _ = self.request("GET", f"/api/files/{file_id}", cookie=mal)
        self.assertEqual(res.status, 404)
        self.assertEqual(ocr.text_for(other, file_id), "")
        state = self.request("GET", "/api/state", cookie=self.ann)[1]
        self.assertNotIn(WAIVER_TEXT.splitlines()[0], str(state), "the text is beside the file, not in the company's state")

    def test_healthz_says_off_not_installed_or_ready(self):
        self.assertEqual((self.health()["ocr"], self.health()["ocr_queue"]), ("ready", 0))
        with mock.patch.dict(os.environ, {"OCR_PDFTOPPM": "/nowhere/pdftoppm"}):
            self.assertEqual(self.health()["ocr"], "not installed")
        with mock.patch.dict(os.environ, {"OCR_ENABLED": ""}):
            self.assertEqual((self.health()["ocr"], self.health()["ok"]), ("off", True))

    def test_a_deleted_company_is_left_alone_when_its_text_arrives(self):
        res, got = self.upload("scan.pdf", SCAN, kind="note")
        self.assertEqual(len(ocr.queue(self.company)), 1)
        server.delete_company_data(self.company)
        self.assertEqual(ocr.run_waiting(), 0)
        self.assertFalse((Path(self.tmp.name) / "uploads" / self.company).exists())
        server._ocr_text_arrived(self.company, "file-" + "0" * 16)   # nothing to do, and no error


class TheCommand(WithOcr):
    def run_command(self, *args):
        lines = []
        with mock.patch("builtins.print", lambda *a, **k: lines.append(" ".join(str(x) for x in a))):
            code = ocr.main(["ocr.py", *args])
        return code, "\n".join(lines)

    def test_backfill_queues_a_companys_older_scans(self):
        with mock.patch.dict(os.environ, {"OCR_ENABLED": ""}):
            for name, data in (("old-scan.pdf", SCAN), ("old-photo.png", png_header(640, 480)), ("typed.pdf", text_pdf()), ("notes.txt", b"words")):
                self.assertEqual(self.upload(name, data, kind="note")[0].status, 200)
            self.assertEqual(self.run_command("backfill", "--company", self.company)[0], 1, "off: it says so and queues nothing")
        self.assertEqual(ocr.queue(self.company), [])
        code, said = self.run_command("backfill", "--company", self.company)
        self.assertEqual((code, said.split(".")[0]), (0, "2 of 4 files queued"))
        self.assertEqual(self.run_command("backfill", "--company", self.company)[1].split(".")[0], "0 of 4 files queued", "a second run queues nothing twice")
        self.assertEqual(self.run_command("backfill", "--company", auth.DEMO_COMPANY_ID)[0], 1)
        self.assertEqual(self.run_command("backfill", "--company", "co-nobody")[0], 1)
        self.assertEqual(self.run_command()[0], 2)
        code, said = self.run_command("status")
        self.assertEqual((code, said.splitlines()), (0, ["OCR: ready", f"{self.company}: 2 waiting"]))
        self.assertEqual(ocr.run_waiting(), 2)


if __name__ == "__main__":
    unittest.main()
