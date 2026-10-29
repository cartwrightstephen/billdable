"""Importer, inbound email/SMS, files, calendar feed, pencil copy and helper over HTTP."""

import base64
import hashlib
import hmac
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import helper
import importer
import server
import storage

PASSWORD = "correct horse battery"
EML = (b"From: Kim Park <ap@park.example>\r\nTo: jobs@example.com\r\nSubject: CO 2 signed for Elm addition\r\n"
       b"Date: Mon, 12 Oct 2026 09:00:00 -0400\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=XYZ\r\n\r\n"
       b"--XYZ\r\nContent-Type: text/plain\r\n\r\nSigned change order attached. Total $2,500.00, due 10/30/2026.\r\n"
       b"--XYZ\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"co2.pdf\"\r\n\r\n%PDF-1.4 fake\r\n"
       b"--XYZ\r\nContent-Type: text/html\r\nContent-Disposition: attachment; filename=\"evil.html\"\r\n\r\n<script>x</script>\r\n--XYZ--\r\n")


ROOT = Path(__file__).resolve().parents[1]


def xlsx_bytes(rows):
    """A minimal Excel file: shared strings for text, numbers as numbers, the first column of later rows as a date."""
    import io
    import zipfile
    strings, sheet = [], []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(value, str):
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                style = ' s="1"' if c == 0 else ""
                cells.append(f'<c r="{ref}"{style}><v>{value}</v></c>')
        sheet.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(sheet)}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>{"".join(f"<si><t>{s}</t></si>" for s in strings)}</sst>')
        z.writestr("xl/styles.xml", f'<styleSheet {ns}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
    return buf.getvalue()


class ImportHttpTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH) = cls._orig
        os.environ.pop("TWILIO_AUTH_TOKEN", None)
        os.environ.pop("HELPER_ADMIN_USER_IDS", None)
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
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
        try:
            parsed = json.loads(payload or b"{}")
        except ValueError:
            parsed = payload
        return res, parsed

    def company(self, email):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": PASSWORD, "name": "Pat Pine", "company_name": "Pine Co"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        job = {"name": "Elm addition", "customer": "Park GC", "address": "9 Elm Street", "approver": "Kim", "ap_email": "ap@park.example",
               "contract_cents": 10_000_000, "retainage_pct": 10, "role": "GC"}
        res, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        return cookie, state["jobs"][0]

    def test_upload_eml_matches_job_and_refuses_html(self):
        cookie, job = self.company("eml@example.com")
        res, state = self.request("POST", "/api/import/file", raw=EML, cookie=cookie,
                                  headers={"Content-Type": "message/rfc822", "X-Filename": quote("co2 email.eml")})
        self.assertEqual(res.status, 200, state)
        item = state["inbox"][-1]
        self.assertEqual(item["suggested_job_id"], job["id"])
        self.assertEqual(item["kind"], "change_order")
        self.assertEqual(item["amounts"], [250_000])
        self.assertIn("2026-10-30", item["dates"])
        names = sorted(f["name"] for f in state["files"])
        self.assertEqual(names, ["co2 email.eml", "co2.pdf"])
        self.assertTrue(any("evil.html" in s for s in item["skipped_attachments"]))
        res, state = self.request("POST", f"/api/inbox/{item['id']}/file", {"job_id": job["id"], "kind": "change_order"}, cookie=cookie)
        rec = state["jobs"][0]["records"][-1]
        self.assertEqual(len(rec["file_ids"]), 2)
        pdf = next(f for f in state["files"] if f["name"] == "co2.pdf")
        res, body = self.request("GET", f"/api/files/{pdf['id']}", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader("Content-Type"), "application/pdf")
        self.assertIsNone(res.getheader("Content-Security-Policy"))
        eml = next(f for f in state["files"] if f["name"] == "co2 email.eml")
        res, _ = self.request("GET", f"/api/files/{eml['id']}", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertIn("sandbox", res.getheader("Content-Security-Policy") or "")
        other, _ = self.company("other-eml@example.com")
        res, _ = self.request("GET", f"/api/files/{pdf['id']}", cookie=other)
        self.assertEqual(res.status, 404)
        res, state = self.request("POST", f"/api/inbox/{item['id']}/undo", {}, cookie=cookie)
        self.assertNotIn(rec["id"], [r["id"] for r in state["jobs"][0]["records"]])

    def test_a_tie_comes_with_candidates_and_a_forward_keeps_its_sender(self):  # R5-05, R5-06
        cookie, job = self.company("tie@example.com")
        second = {"name": "Birch garage", "customer": "Park GC", "address": "4 Birch Road", "approver": "Kim", "ap_email": "ap@park.example",
                  "contract_cents": 5_000_000, "retainage_pct": 10, "role": "GC"}
        res, state = self.request("POST", "/api/jobs", second, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        self.assertEqual(len({j["payer_id"] for j in state["jobs"]}), 1, "both jobs are Park GC's")
        forward = ("Can you look at this one?\n\n---------- Forwarded message ---------\nFrom: Kim Park <ap@park.example>\n"
                   "Date: Mon, Oct 12, 2026 at 9:00 AM\nSubject: Backup for October\nTo: Pat Pine <pat@pine.example>\n\n"
                   "Please send the delivery tickets with the next one. 12,500.00 is held until then.")
        res, state = self.request("POST", "/api/import/text", {"text": forward, "source": "paste"}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        item = state["inbox"][-1]
        self.assertEqual((item["from"], item["subject"]), ("Kim Park <ap@park.example>", "Backup for October"))
        self.assertTrue(item["text"].startswith("Can you look at this one?"))
        self.assertNotIn("Forwarded message", item["text"])
        self.assertEqual(item["amounts"], [1_250_000])
        # The AP email is both jobs' customer: a tie, with both jobs offered.
        self.assertIsNone(item["suggested_job_id"])
        self.assertEqual(sorted(item["candidates"]), sorted(j["id"] for j in state["jobs"]))
        self.assertEqual(item["tied"], 2)
        # One of them by name: suggested, for the AP-email reason only when the name is not there.
        res, state = self.request("POST", "/api/import/text", {"text": forward.replace("Backup for October", "Birch garage backup"), "source": "email"}, cookie=cookie)
        item = state["inbox"][-1]
        self.assertEqual(item["suggested_job_id"], next(j["id"] for j in state["jobs"] if j["name"] == "Birch garage"))
        res, state = self.request("POST", f"/api/jobs/{job['id']}/delete", {}, cookie=cookie)
        res, state = self.request("POST", "/api/import/text", {"text": forward, "source": "email"}, cookie=cookie)
        item = state["inbox"][-1]
        self.assertEqual((item["from"], item["match_reason"]), ("Kim Park <ap@park.example>", "sent from Park GC's AP email"))

    def test_upload_from_the_inbox_with_a_job_picked(self):  # R5-07
        cookie, job = self.company("pick-job@example.com")
        res, state = self.request("POST", f"/api/import/file?job={job['id']}", raw=b"\x89PNG fake", cookie=cookie,
                                  headers={"Content-Type": "image/png", "X-Filename": "IMG_0002.png"})
        self.assertEqual(res.status, 200, state)
        item = state["inbox"][-1]
        self.assertEqual((item["status"], item["suggested_job_id"], item["kind"]), ("new", job["id"], "photo"))
        self.assertEqual(item["match_reason"], "was uploaded for this job")
        res, state = self.request("POST", f"/api/inbox/{item['id']}/file", {"job_id": job["id"], "kind": ""}, cookie=cookie)
        rec = state["jobs"][0]["records"][-1]
        res, state = self.request("POST", f"/api/jobs/{job['id']}/edit-record/{rec['id']}", {"label": "Lobby floor", "kind": "ticket"}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        rec = next(r for r in state["jobs"][0]["records"] if r["id"] == rec["id"])
        self.assertEqual((rec["label"], rec["kind"]), ("Lobby floor", "ticket"))

    def test_refused_file_types(self):
        cookie, _ = self.company("types@example.com")
        for name in ["page.html", "logo.svg", "run.js"]:
            res, data = self.request("POST", "/api/import/file", raw=b"<x>", cookie=cookie, headers={"Content-Type": "text/html", "X-Filename": name})
            self.assertEqual(res.status, 409, name)

    def test_drop_on_job_files_immediately(self):
        cookie, job = self.company("drop@example.com")
        res, state = self.request("POST", f"/api/import/file?job={job['id']}&file=1", raw=b"\x89PNG fake", cookie=cookie,
                                  headers={"Content-Type": "image/png", "X-Filename": "IMG_0001.png"})
        self.assertEqual(res.status, 200, state)
        self.assertEqual(state["inbox"][-1]["status"], "filed")
        self.assertEqual(state["jobs"][0]["records"][-1]["kind"], "photo")

    def test_photo_for_a_missing_item_attaches_in_one_step(self):  # R3-05
        cookie, job = self.company("paperwork-photo@example.com")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/progress", {"completed_cents": 4_000_000}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        packet = state["jobs"][0]["invoices"][-1]["packet"]
        item = next(p for p in packet if not p["present"])
        res, state = self.request("POST", f"/api/import/file?job={job['id']}&file=1&kind=waiver&attach={item['id']}", raw=b"\x89PNG fake",
                                  cookie=cookie, headers={"Content-Type": "image/png", "X-Filename": "IMG_0002.png"})
        self.assertEqual(res.status, 200, state)
        rec = state["jobs"][0]["records"][-1]
        item = next(p for p in state["jobs"][0]["invoices"][-1]["packet"] if p["id"] == item["id"])
        self.assertTrue(item["present"])
        self.assertEqual([a["record_id"] for a in item["attachments"]], [rec["id"]])
        # The Inbox's Undo was refused while the paperwork used the record; Remove frees it.
        inbox_id = state["inbox"][-1]["id"]
        res, data = self.request("POST", f"/api/inbox/{inbox_id}/undo", {}, cookie=cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("Remove", data["error"])
        res, state = self.request("POST", f"/api/jobs/{job['id']}/detach/{item['id']}", {}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        self.assertIn(item["blocker"], state["jobs"][0]["blockers"])
        res, state = self.request("POST", f"/api/inbox/{inbox_id}/undo", {}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        # A wrong item refuses the upload and keeps nothing.
        records = len(state["jobs"][0]["records"])
        res, data = self.request("POST", f"/api/import/file?job={job['id']}&file=1&attach=pkt-nope", raw=b"\x89PNG fake",
                                 cookie=cookie, headers={"Content-Type": "image/png", "X-Filename": "IMG_0003.png"})
        self.assertEqual(res.status, 409)
        res, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(len(state["jobs"][0]["records"]), records)

    def test_photo_of_a_signed_change_order_signs_it(self):  # R3-06
        cookie, job = self.company("co-photo@example.com")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/add-co", {"title": "Owner buys fixtures", "amount_cents": -150_000, "schedule_days": "2"}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        co = state["jobs"][0]["change_orders"][-1]
        self.assertEqual((co["amount_cents"], co["schedule_days"]), (-150_000, 2))
        res, state = self.request("POST", f"/api/import/file?job={job['id']}&file=1&kind=change_order&co={co['id']}", raw=b"\x89PNG fake",
                                  cookie=cookie, headers={"Content-Type": "image/png", "X-Filename": "IMG_0004.png"})
        self.assertEqual(res.status, 200, state)
        j = state["jobs"][0]
        co = j["change_orders"][-1]
        self.assertEqual(co["status"], "approved")
        self.assertEqual(co["signed_evidence"]["record_id"], j["records"][-1]["id"])
        self.assertEqual(j["records"][-1]["kind"], "change_order")
        self.assertEqual(j["money"]["contract_total_cents"], 10_000_000 - 150_000)

    def test_notes_split_into_daily_records(self):
        cookie, job = self.company("notes@example.com")
        notes = "10/1 Footings poured\ncrew of 4\n10/2 Framing started\nOct 3, 2026: rough-in inspection passed"
        res, state = self.request("POST", "/api/import/text?file=1", {"text": notes, "source": "notes", "job_id": job["id"]}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        days = sorted(d["at"][:10] for d in state["daily_records"])
        self.assertEqual(len(days), 3)
        self.assertTrue(all(d.endswith(("-01", "-02", "-03")) for d in days))

    def test_quickbooks_csv_import_is_safe_to_repeat(self):  # P2-1
        cookie, job = self.company("qbo@example.com")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/progress", {"completed_cents": 4_000_000}, cookie=cookie)
        inv = state["jobs"][0]["invoices"][-1]
        for item in inv.get("packet") or []:
            self.request("POST", f"/api/jobs/{job['id']}/resolve/{item['id']}", {"reason": "sent with the email"}, cookie=cookie)
        res, state = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        amount = state["jobs"][0]["invoices"][-1]["amount_cents"]
        csv_text = f"Date,Num,Customer,Amount,Memo\n10/09/2026,5521,Park GC,{amount / 100:.2f},\n"
        res, read = self.request("POST", "/api/import/payments-csv", {"csv": csv_text}, cookie=cookie)
        row = read["rows"][0]
        self.assertEqual((row["status"], row["invoice_id"]), ("match", inv["id"]))
        pick = {"job_id": row["job_id"], "invoice_id": row["invoice_id"], "amount_cents": row["amount_cents"], "reference": row["ref"], "date": row["date"]}
        res, state = self.request("POST", "/api/import/payments-apply", {"payments": [pick]}, cookie=cookie)
        self.assertEqual(state["import_results"], [{"key": None, "reference": "5521", "ok": True}])
        self.assertEqual(state["jobs"][0]["invoices"][-1]["status"], "paid")
        res, state = self.request("POST", "/api/import/payments-apply", {"payments": [pick]}, cookie=cookie)
        self.assertFalse(state["import_results"][0]["ok"], "the same check twice is refused")
        res, read = self.request("POST", "/api/import/payments-csv", {"csv": csv_text}, cookie=cookie)
        self.assertEqual(read["rows"][0]["status"], "already recorded")

    def test_a_dropped_payments_file_reads_on_payments(self):  # R5-04
        cookie, job = self.company("dropcsv@example.com")
        path = ROOT / "testdata/test-contractor/import/01_qbo-payments_partial-nomatch-odd-formats.csv"
        res, state = self.request("POST", "/api/import/file", raw=path.read_bytes(), cookie=cookie,
                                  headers={"Content-Type": "text/csv", "X-Filename": quote(path.name)})
        self.assertEqual(res.status, 200, state)
        item = state["inbox"][-1]
        self.assertTrue(item["payments_csv"])
        self.assertEqual(item["payment_rows"], 6)
        res, read = self.request("POST", "/api/import/payments-csv", {"file_id": item["file_ids"][0]}, cookie=cookie)
        self.assertEqual(res.status, 200, read)
        self.assertEqual(len(read["rows"]), 6)
        # A CSV that isn't payments is an ordinary Inbox item.
        res, state = self.request("POST", "/api/import/file", raw=b"Item,Qty\nTile,4\n", cookie=cookie,
                                  headers={"Content-Type": "text/csv", "X-Filename": "takeoff.csv"})
        self.assertNotIn("payments_csv", state["inbox"][-1])
        # Another company can't read the file by its id.
        other, _ = self.company("dropcsv-other@example.com")
        res, read = self.request("POST", "/api/import/payments-csv", {"file_id": item["file_ids"][0]}, cookie=other)
        self.assertEqual(res.status, 409)

    def test_an_excel_payments_export(self):  # R5-04
        cookie, _ = self.company("xlsx@example.com")
        data = xlsx_bytes([["Date", "Num", "Customer", "Amount"], [46309, "4471", "Park GC", 1250.5]])
        res, state = self.request("POST", "/api/import/file", raw=data, cookie=cookie,
                                  headers={"Content-Type": "application/octet-stream", "X-Filename": "payments.xlsx"})
        self.assertEqual(res.status, 200, state)
        self.assertEqual(state["inbox"][-1]["payment_rows"], 1)
        res, read = self.request("POST", "/api/import/payments-csv", {"xlsx": base64.b64encode(data).decode()}, cookie=cookie)
        row = read["rows"][0]
        self.assertEqual((row["date"], row["ref"], row["customer"], row["amount_cents"]), ("2026-10-14", "4471", "Park GC", 125_050))
        res, read = self.request("POST", "/api/import/payments-csv", {"xlsx": base64.b64encode(b"not a zip").decode()}, cookie=cookie)
        self.assertEqual(res.status, 409)

    def test_reply_to_a_sent_pay_app_files_itself(self):  # P1-3
        cookie, job = self.company("reply@example.com")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/progress", {"completed_cents": 4_000_000}, cookie=cookie)
        number = state["jobs"][0]["invoices"][-1]["number"]
        company_id = state["company_id"] if "company_id" in state else next(
            c for c in server.company_ids() if any(j["id"] == job["id"] for j in server.load_company(c)["jobs"]))
        with server.company_lock(company_id):  # as if the pay app email had gone out
            st = server.load_company(company_id)
            st.setdefault("email_log", []).append({"id": "eml-t", "job_id": job["id"], "kind": "pay_app", "to": "ap@park.example",
                                                   "cc": [], "subject": f"Pay application {number} — Elm addition", "status": "sent"})
            server.save_company(company_id, st)
        res, settings = self.request("GET", "/api/import/settings", cookie=cookie)
        email_path = "/api/inbound/email/" + settings["email_webhook"].rsplit("/", 1)[-1]
        res, _ = self.request("POST", email_path, {"from": "Kim <ap@park.example>", "subject": f"RE: Pay application {number} — Elm addition",
                                                    "text": "Approved, check goes out Friday."})
        self.assertEqual(res.status, 200)
        res, state = self.request("GET", "/api/state", cookie=cookie)
        item = state["inbox"][-1]
        self.assertEqual((item["status"], item["filed_job_id"], item["auto_filed"]), ("filed", job["id"], True))
        # Shown as the status line once the pay app is sent (here it is still a draft in the test).
        self.assertTrue(state["jobs"][0]["external_status"].startswith("Park GC replied "))
        self.assertIn("Approved, check goes out Friday.", state["jobs"][0]["external_status"])

    def test_inbound_email_json_and_sms_with_signature(self):
        cookie, job = self.company("inbound@example.com")
        res, settings = self.request("GET", "/api/import/settings", cookie=cookie)
        email_path = "/api/inbound/email/" + settings["email_webhook"].rsplit("/", 1)[-1]
        sms_path = "/api/inbound/sms/" + settings["sms_webhook"].rsplit("/", 1)[-1]
        res, _ = self.request("POST", "/api/inbound/email/not-a-token", {"from": "x", "text": "hi"})
        self.assertEqual(res.status, 404)
        payload = {"From": "ap@park.example", "Subject": "Pay app question", "TextBody": "About 9 Elm Street",
                   "Attachments": [{"Name": "photo.jpg", "Content": base64.b64encode(b"\xff\xd8fake").decode(), "ContentType": "image/jpeg"}]}
        res, data = self.request("POST", email_path, payload)
        self.assertEqual(res.status, 200, data)
        os.environ["TWILIO_AUTH_TOKEN"] = "test-secret"
        form = {"From": "+15555550123", "Body": "Elm addition: drywall delivered", "NumMedia": "0"}
        raw = urlencode(form).encode()
        url = f"http://127.0.0.1:{self.port}{sms_path}"
        good = base64.b64encode(hmac.new(b"test-secret", (url + "".join(k + form[k] for k in sorted(form))).encode(), hashlib.sha1).digest()).decode()
        hdr = {"Content-Type": "application/x-www-form-urlencoded", "Host": f"127.0.0.1:{self.port}"}
        res, _ = self.request("POST", sms_path, raw=raw, headers={**hdr, "X-Twilio-Signature": "wrong"})
        self.assertEqual(res.status, 403)
        res, body = self.request("POST", sms_path, raw=raw, headers={**hdr, "X-Twilio-Signature": good})
        self.assertEqual(res.status, 200)
        self.assertIn(b"<Response>", body)
        os.environ.pop("TWILIO_AUTH_TOKEN")
        res, state = self.request("GET", "/api/state", cookie=cookie)
        sources = [(i["source"], i["suggested_job_id"]) for i in state["inbox"]]
        self.assertIn(("email", job["id"]), sources)
        self.assertIn(("sms", job["id"]), sources)

    def inbound_paths(self, email):
        cookie, job = self.company(email)
        _, settings = self.request("GET", "/api/import/settings", cookie=cookie)
        return cookie, ("/api/inbound/email/" + settings["email_webhook"].rsplit("/", 1)[-1],
                        "/api/inbound/sms/" + settings["sms_webhook"].rsplit("/", 1)[-1])

    def test_inbound_email_with_a_password(self):  # R6-07
        cookie, (email_path, _) = self.inbound_paths("inbound-pw@example.com")
        payload = {"From": "ap@park.example", "Subject": "Lien waiver", "TextBody": "About 9 Elm Street"}
        basic = lambda pw: {"Authorization": "Basic " + base64.b64encode(f"duracash:{pw}".encode()).decode()}
        with unittest.mock.patch.dict(os.environ, {"INBOUND_WEBHOOK_PASSWORD": "long inbound password"}):
            res, data = self.request("POST", email_path, payload)
            self.assertEqual(res.status, 401, "no password: refused")
            self.assertIn("Basic", res.getheader("WWW-Authenticate"))
            self.assertEqual(self.request("POST", email_path, payload, headers=basic("wrong"))[0].status, 401)
            res, data = self.request("POST", email_path, payload, headers=basic("long inbound password"))
            self.assertEqual(res.status, 200, data)
            _, settings = self.request("GET", "/api/import/settings", cookie=cookie)
            self.assertEqual(settings["email_check"], "password")
        res, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual([i["subject"] for i in state["inbox"] if i["source"] == "email"], ["Lien waiver"], "only the signed-in post came in")

    def test_inbound_email_signed_and_unsigned(self):  # R6-07
        cookie, (email_path, _) = self.inbound_paths("inbound-sig@example.com")
        key = "key-3ax6xnjp29jd6fds4gc373sgvjxteol0"
        stamp = str(int(time.time()))
        good = {"timestamp": stamp, "token": "a8ce0edb2dd8301dee6c2405235584e45aa91d1e9f979f3de0",
                "signature": hmac.new(key.encode(), (stamp + "a8ce0edb2dd8301dee6c2405235584e45aa91d1e9f979f3de0").encode(), hashlib.sha256).hexdigest(),
                "sender": "ap@park.example", "subject": "Signed form", "body-plain": "About 9 Elm Street"}
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        with unittest.mock.patch.dict(os.environ, {"INBOUND_SIGNING_KEY": key}):
            unsigned = {k: v for k, v in good.items() if k not in {"timestamp", "token", "signature"}}
            self.assertEqual(self.request("POST", email_path, raw=urlencode(unsigned).encode(), headers=form)[0].status, 401)
            forged = {**good, "signature": "0" * 64}
            self.assertEqual(self.request("POST", email_path, raw=urlencode(forged).encode(), headers=form)[0].status, 401)
            old = {**good, "timestamp": str(int(stamp) - 3600)}
            old["signature"] = hmac.new(key.encode(), (old["timestamp"] + old["token"]).encode(), hashlib.sha256).hexdigest()
            self.assertEqual(self.request("POST", email_path, raw=urlencode(old).encode(), headers=form)[0].status, 401, "too old")
            res, data = self.request("POST", email_path, raw=urlencode(good).encode(), headers=form)
            self.assertEqual(res.status, 200, data)
            # Mailgun's JSON webhooks carry the three in a "signature" object.
            nested = {"signature": {k: good[k] for k in ("timestamp", "token", "signature")}, "From": "ap@park.example",
                      "Subject": "Signed JSON", "TextBody": "About 9 Elm Street"}
            self.assertEqual(self.request("POST", email_path, nested)[0].status, 200)
        res, state = self.request("GET", "/api/state", cookie=cookie)
        got = [(i["subject"], i["from"], i["suggested_job_id"] is not None) for i in state["inbox"] if i["source"] == "email"]
        self.assertEqual(got, [("Signed form", "ap@park.example", True), ("Signed JSON", "ap@park.example", True)])

    def test_inbound_addresses_are_rate_limited(self):  # R6-07
        _, (email_path, sms_path) = self.inbound_paths("inbound-rate@example.com")
        with unittest.mock.patch.object(server, "INBOUND_PER_HOUR", 2):
            statuses = [self.request("POST", email_path, {"From": "x@example.com", "TextBody": f"note {n}"})[0].status for n in range(3)]
            self.assertEqual(statuses, [200, 200, 429])
            form = {"Content-Type": "application/x-www-form-urlencoded"}
            statuses = [self.request("POST", sms_path, raw=urlencode({"From": "+1555", "Body": "hi"}).encode(), headers=form)[0].status for _ in range(3)]
            self.assertEqual(statuses, [200, 200, 429])

    def test_webhook_addresses_owners_only_and_never_from_the_host_header(self):  # R6-07
        cookie, _ = self.company("inbound-owner@example.com")
        me = auth.session_user(cookie.split("=", 1)[1])
        auth.create_user("inbound-member@example.com", PASSWORD, "Mo", me["company_id"], role="member")
        res, _ = self.request("POST", "/api/auth/login", {"email": "inbound-member@example.com", "password": PASSWORD})
        member = res.getheader("Set-Cookie").split(";")[0]
        _, settings = self.request("GET", "/api/import/settings", cookie=member)
        self.assertEqual((settings["email_webhook"], settings["sms_webhook"]), ("", ""))
        self.assertTrue(settings["calendar_url"], "the calendar address is for everyone")
        spoof = {"Host": "evil.example", "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http"}
        with unittest.mock.patch.dict(os.environ, {"DURACASH_URL": "https://app.duracash.example"}):
            _, settings = self.request("GET", "/api/import/settings", cookie=cookie, headers=spoof)
            for key in ("email_webhook", "sms_webhook", "calendar_url"):
                self.assertTrue(settings[key].startswith("https://app.duracash.example/api/"), settings[key])
            # The Twilio signature is checked against the same address Twilio was given.
            sms_path = "/api/inbound/sms/" + settings["sms_webhook"].rsplit("/", 1)[-1]
            form = {"From": "+15555550123", "Body": "Elm addition: tile in", "NumMedia": "0"}
            signed = lambda base: base64.b64encode(hmac.new(b"test-secret", (base + sms_path + "".join(k + form[k] for k in sorted(form))).encode(), hashlib.sha1).digest()).decode()
            hdr = {"Content-Type": "application/x-www-form-urlencoded", **spoof}
            with unittest.mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "test-secret"}):
                res, _ = self.request("POST", sms_path, raw=urlencode(form).encode(), headers={**hdr, "X-Twilio-Signature": signed("http://evil.example")})
                self.assertEqual(res.status, 403, "a signature for the spoofed host fails")
                res, _ = self.request("POST", sms_path, raw=urlencode(form).encode(), headers={**hdr, "X-Twilio-Signature": signed("https://app.duracash.example")})
                self.assertEqual(res.status, 200)
        _, settings = self.request("GET", "/api/import/settings", cookie=cookie, headers={"Host": "laptop.local:8765"})
        self.assertTrue(settings["email_webhook"].startswith("http://laptop.local:8765/"), "without DURACASH_URL, the request's own address")

    def test_calendar_feed_and_pencil(self):
        cookie, job = self.company("cal@example.com")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/progress", {"completed_cents": 4_000_000}, cookie=cookie)
        self.assertTrue(state["jobs"][0]["closeout_packet"], "punch list visible from the first retainage pay app")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/pencil", {}, cookie=cookie)
        self.assertEqual(state["email_log"][-1]["kind"], "pencil")
        res, state = self.request("POST", f"/api/jobs/{job['id']}/pencil-response", {"agreed": False, "note": "Qty on line 3 is 900 sf"}, cookie=cookie)
        self.assertEqual(state["jobs"][0]["invoices"][-1]["pencil_status"], "corrections")
        res, settings = self.request("GET", "/api/import/settings", cookie=cookie)
        token = settings["calendar_url"].rsplit("/", 1)[-1]
        res, body = self.request("GET", f"/api/calendar/{token}")
        self.assertEqual(res.status, 200)
        self.assertTrue(body.startswith(b"BEGIN:VCALENDAR"))
        unfolded = body.decode().replace("\r\n ", "")
        self.assertIn("X-WR-CALDESC:These are your contract and follow-up dates\\, not legal deadlines.", unfolded)  # P0-6
        self.assertTrue(all("not legal deadlines" in ev for ev in unfolded.split("BEGIN:VEVENT")[1:]))
        res, _ = self.request("GET", "/api/calendar/wrong.ics")
        self.assertEqual(res.status, 404)

    def test_helper_endpoints_and_admin_gate(self):
        cookie, job = self.company("helper@example.com")
        res, reply = self.request("POST", "/api/helper/ask", {"q": "what should I do today?", "view": "dashboard"}, cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(reply["matched"], "today")
        res, reply = self.request("POST", "/api/helper/ask", {"q": "zzz qqq unknown thing"}, cookie=cookie)
        self.assertIsNone(reply["matched"])
        res, _ = self.request("GET", "/api/helper/review", cookie=cookie)
        self.assertEqual(res.status, 403)
        res, _ = self.request("POST", "/api/helper/answers", {"questions": ["x"], "answer": "y"}, cookie=cookie)
        self.assertEqual(res.status, 403)
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        os.environ["HELPER_ADMIN_USER_IDS"] = me["user"]["id"]
        res, review = self.request("GET", "/api/helper/review", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertTrue(any("zzz" in g["examples"][0] for g in review["open"]))
        res, data = self.request("POST", "/api/helper/answers", {"questions": "not a list", "answer": "y"}, cookie=cookie)
        self.assertEqual(res.status, 409)
        # An email that matches nothing in the setting gets nothing, whatever the address.
        other, _ = self.company("founder-lookalike@example.com")
        res, _ = self.request("GET", "/api/helper/review", cookie=other)
        self.assertEqual(res.status, 403)
        os.environ.pop("HELPER_ADMIN_USER_IDS")

    def test_review_findings(self):
        cookie, job = self.company("findings@example.com")
        # Multipart inbound email with a mixed-case boundary (SendGrid style) keeps its fields.
        res, settings = self.request("GET", "/api/import/settings", cookie=cookie)
        email_path = "/api/inbound/email/" + settings["email_webhook"].rsplit("/", 1)[-1]
        body = (b"--xYzZY\r\nContent-Disposition: form-data; name=\"from\"\r\n\r\nap@park.example\r\n"
                b"--xYzZY\r\nContent-Disposition: form-data; name=\"subject\"\r\n\r\nElm addition question\r\n"
                b"--xYzZY\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\nHello\r\n--xYzZY--\r\n")
        res, _ = self.request("POST", email_path, raw=body, headers={"Content-Type": "multipart/form-data; boundary=xYzZY"})
        self.assertEqual(res.status, 200)
        res, state = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(state["inbox"][-1]["subject"], "Elm addition question")
        # Malformed inbound JSON is handled, not a 500.
        for payload in [{"attachments": 5}, {"attachments": [{"name": 5, "content": "aGk="}]}]:
            res, _ = self.request("POST", email_path, payload)
            self.assertIn(res.status, (200, 409), payload)
        # Long pasted notes are accepted.
        notes = "\n".join(f"10/{d} Elm addition crew of 4, framing and blocking, " + "x" * 200 for d in range(1, 29))
        res, state = self.request("POST", "/api/import/text", {"text": notes, "source": "notes"}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        # A refused import leaves no file on disk.
        before = sorted(p.name for p in (importer.UPLOADS / job["id"].split("-")[0]).glob("*")) if False else None
        res, state = self.request("GET", "/api/state", cookie=cookie)
        company_dir = next(importer.UPLOADS.iterdir()) if importer.UPLOADS.exists() else None
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        folder = importer.UPLOADS / me["user"]["company_id"]
        count = len(list(folder.glob("*"))) if folder.exists() else 0
        res, _ = self.request("POST", "/api/import/file?job=job-nope&file=1", raw=b"%PDF-1.4", cookie=cookie,
                              headers={"Content-Type": "application/pdf", "X-Filename": "a.pdf"})
        self.assertEqual(res.status, 409)
        self.assertEqual(len(list(folder.glob("*"))) if folder.exists() else 0, count)
        # PDFs are served without the sandbox header so the browser's viewer opens them.
        res, state = self.request("POST", "/api/import/file", raw=b"%PDF-1.4 x", cookie=cookie,
                                  headers={"Content-Type": "application/pdf", "X-Filename": "ok.pdf"})
        pdf = next(f for f in state["files"] if f["name"] == "ok.pdf")
        res, _ = self.request("GET", f"/api/files/{pdf['id']}", cookie=cookie)
        self.assertIsNone(res.getheader("Content-Security-Policy"))

    def test_tokens_survive_concurrent_companies(self):
        cookies = [self.company(f"race{i}@example.com")[0] for i in range(10)]
        results = [None] * len(cookies)

        def fetch(i):
            res, data = self.request("GET", "/api/import/settings", cookie=cookies[i])
            results[i] = data["email_webhook"].rsplit("/", 1)[-1]

        threads = [threading.Thread(target=fetch, args=(i,)) for i in range(len(cookies))]
        for t in threads: t.start()
        for t in threads: t.join()
        stored = importer.load_tokens()
        self.assertTrue(all(tok in stored for tok in results))


if __name__ == "__main__":
    unittest.main()
