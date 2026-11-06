"""A small file can't cost a lot (the Nov 5 security hunt): reading a Word file or a .txt stays small
whatever they inflate to, and it happens outside the company's lock. Real contracts still read."""

import http.client
import io
import json
import sys
import tempfile
import threading
import tracemalloc
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import contract_import
import doc_check
import importer
import server
import storage

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
MB = 1024 * 1024


def docx(xml, name="word/document.xml"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, xml)
    return buf.getvalue()


def bomb(size=29 * MB):
    return docx(f"<w:document {NS}><w:body>".encode() + b"<w:p/>" * (size // 6) + b"</w:body></w:document>")


def peak(fn):
    tracemalloc.start()
    try:
        result = fn()
        return result, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def run_of(text):
    return f'<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'


def contract(pages=300):
    """A long contract as Word writes one: formatted runs, a page break and a schedule table."""
    body = [f"<w:p>{run_of('SUBCONTRACT AGREEMENT')}</w:p>",
            f"<w:p>{run_of('Contract No. SC-2026-114 between Ironleaf General Contractors, LLC and Hartline Tile.')}</w:p>",
            f"<w:p>{run_of('The Contract Sum is $48,500.00. Retainage shall be ten percent (10%).')}</w:p>",
            "<w:tbl>" + "".join(f"<w:tr><w:tc><w:p>{run_of(f'Line {n}')}</w:p></w:tc><w:tc><w:p>{run_of(f'Item')}</w:p><w:p>{run_of('two')}</w:p></w:tc></w:tr>" for n in range(3)) + "</w:tbl>"]
    for page in range(pages):
        for line in range(40):
            body.append(f"<w:p><w:pPr><w:spacing w:after=\"120\"/></w:pPr>{run_of(f'Section {page}.{line}: The Subcontractor shall perform')}{run_of(' the Work &amp; keep it clean.')}</w:p>")
        body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    body.append(f"<w:p>{run_of('The last page of this contract.')}</w:p>")
    return f"<w:document {NS}><w:body>{''.join(body)}</w:body></w:document>".encode()


class WordFiles(unittest.TestCase):
    def test_a_long_real_contract_still_reads(self):
        xml = contract(pages=120)
        text = contract_import.docx_text(docx(xml))
        self.assertIn("The last page of this contract.", text)
        self.assertIn("Section 119.39: The Subcontractor shall perform the Work & keep it clean.", text)
        self.assertIn("Line 2\tItem two", text.split("\n"))
        found = contract_import.read("c.docx", docx(xml))
        self.assertTrue(found["ok"], found)
        self.assertEqual(found["fields"]["contract_cents"], 4_850_000)
        self.assertEqual(found["fields"]["retainage_pct"], 10)

    def test_a_very_long_contract_is_inside_the_limits_and_reads_up_to_the_text_limit(self):
        xml = contract(pages=500)
        self.assertLess(len(xml), contract_import.MAX_PART, "500 pages")
        text = contract_import.docx_text(docx(xml))
        self.assertIn("SC-2026-114", text)
        self.assertGreaterEqual(len(text), contract_import.MAX_TEXT)

    def test_the_lines_come_out_as_before(self):
        xml = (f'<w:document {NS}><w:body><w:p><w:r><w:t>One</w:t><w:tab/><w:t>two</w:t><w:br/><w:t>three</w:t></w:r></w:p>'
               '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>a</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>b</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
               '<w:p><w:r><w:t>end &amp; done</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(contract_import.docx_text(docx(xml)), "One\ttwo\nthree\na\tb\nend & done")

    def test_a_bomb_is_refused_with_little_memory(self):
        data = bomb()
        self.assertLess(len(data), 100_000)
        _, used = peak(lambda: self.assertRaises(ValueError, contract_import.docx_text, data))
        self.assertLess(used, 5 * MB)
        result, used = peak(lambda: doc_check.check([("w.docx", ".docx", data)], "Oak"))
        self.assertLess(used, 5 * MB)
        self.assertIn("couldn't be opened", " ".join(result["problems"]))
        self.assertFalse(peak(lambda: contract_import.read("w.docx", data))[0]["ok"])

    def test_empty_elements_inside_the_size_cap_are_cheap(self):
        size = contract_import.MAX_PART - 100
        data = bomb(size)
        result, used = peak(lambda: doc_check.check([("w.docx", ".docx", data)], "Oak"))
        self.assertEqual(result["status"], "flagged")
        self.assertLess(used, 5 * MB)

    def test_deep_nesting_is_refused(self):
        depth = 500_000
        data = docx(b"<a>" * depth + b"</a>" * depth)
        _, used = peak(lambda: self.assertRaises(ValueError, contract_import.docx_text, data))
        self.assertLess(used, 5 * MB)

    def test_text_past_the_limit_is_left_unread(self):
        words = "word " * 1000
        para = f"<w:p><w:r><w:t>{words}</w:t></w:r></w:p>"
        data = docx(f"<w:document {NS}><w:body>{para * 1000}</w:body></w:document>")
        text, used = peak(lambda: contract_import.docx_text(data))
        self.assertLessEqual(len(text), contract_import.MAX_TEXT + 2000)
        self.assertLess(used, 15 * MB)

    def test_a_zip_directory_stuffed_with_parts_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            z.writestr("word/document.xml", f"<w:document {NS}/>")
            for n in range(40_000):
                z.writestr(f"{n}", b"")
        data = buf.getvalue()
        _, used = peak(lambda: self.assertRaises(ValueError, contract_import.docx_text, data))
        self.assertLess(used, 5 * MB)

    def test_entities_are_refused_in_any_encoding(self):
        xml = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
        with self.assertRaises(ValueError):
            contract_import.docx_text(docx(xml.encode("utf-16")))


class TextFiles(unittest.TestCase):
    def test_a_big_txt_stays_small(self):
        data = (b"12,345.67 Oak word " * 800_000)[:15 * MB]
        result, used = peak(lambda: doc_check.check([("x.txt", ".txt", data)], "Oak", "$1.00", 100, is_waiver=True))
        self.assertLess(used, 40 * MB)
        self.assertEqual(result["status"], "flagged")
        _, used = peak(lambda: contract_import.read("x.txt", data))
        self.assertLess(used, 80 * MB)

    def test_a_normal_txt_is_read_whole(self):
        result = doc_check.check([("x.txt", ".txt", b"Oak job, amount $1.00 paid")], "Oak", "$1.00", 100, is_waiver=True)
        self.assertEqual(result["problems"], [])


class OutsideTheLock(unittest.TestCase):
    """The document check reads before the company's lock is taken: others wait for nothing."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = root / "auth.json", root / "companies", root, root / "uploads"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=20)
        h = dict(headers or {})
        data = raw
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        if cookie:
            h["Cookie"] = cookie
        conn.request(method, path, body=data, headers=h)
        res = conn.getresponse()
        out = res.read()
        conn.close()
        return res, json.loads(out or b"{}")

    def setUp(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": f"lock{id(self)}@example.com", "password": "correct horse battery", "name": "Pat", "company_name": "Pine"})
        self.cookie = res.getheader("Set-Cookie").split(";")[0]
        _, state = self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "4045550177",
                                                      "ap_email": "ap@northline.example", "contract_cents": 2000000}, cookie=self.cookie)
        self.job = state["jobs"][-1]
        res, state = self.request("POST", f"/api/jobs/{self.job['id']}/sov-progress", {"done": {self.job["sov"]["lines"][0]["id"]: "0.5"}}, cookie=self.cookie)
        self.assertEqual(res.status, 200, state)
        self.job = state["jobs"][-1]
        self.item = next(p for inv in self.job["invoices"] for p in inv["packet"] if not p["present"])

    def upload(self, query):
        return self.request("POST", f"/api/import/file?job={self.job['id']}&file=1&kind=waiver{query}", raw=docx(f"<w:document {NS}><w:body><w:p><w:r><w:t>Birch deck waiver</w:t></w:r></w:p></w:body></w:document>"),
                            cookie=self.cookie, headers={"Content-Type": "application/octet-stream", "X-Filename": quote("w.docx")})

    def others_are_not_held_up_during(self, send):
        entered, go = threading.Event(), threading.Event()
        real = doc_check.prepare

        def slow(ext, data):
            entered.set()
            go.wait(10)
            return real(ext, data)

        answer = {}

        def run():
            answer["res"] = send()

        with mock.patch.object(doc_check, "prepare", slow):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(entered.wait(10), "the file was read")
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=3)
                conn.request("GET", "/api/state", headers={"Cookie": self.cookie})
                res = conn.getresponse()
                res.read()
                conn.close()
                self.assertEqual(res.status, 200)
            finally:
                go.set()
            worker.join(15)
        return answer["res"]

    def test_uploading_for_an_item(self):
        res, state = self.others_are_not_held_up_during(lambda: self.upload(f"&attach={self.item['id']}"))
        self.assertEqual(res.status, 200, state)
        item = next(p for inv in state["jobs"][-1]["invoices"] for p in inv["packet"] if p["id"] == self.item["id"])
        self.assertEqual(item["check"]["status"] in {"flagged", "matched"}, True)
        self.assertEqual(len(item["attachments"]), 1)

    def test_adding_a_record_to_the_email(self):
        _, state = self.upload("")
        rec = state["jobs"][-1]["records"][-1]
        inv = state["jobs"][-1]["invoices"][-1]
        res, state = self.others_are_not_held_up_during(lambda: self.request("POST", f"/api/jobs/{self.job['id']}/payapp/{inv['id']}/attachments", {"action": "add_record", "record_id": rec["id"]}, cookie=self.cookie))
        self.assertEqual(res.status, 200, state)

    def test_sending_reads_a_waiver_with_no_type_first(self):
        _, state = self.upload("")
        rec = state["jobs"][-1]["records"][-1]
        inv = state["jobs"][-1]["invoices"][-1]
        self.request("POST", f"/api/jobs/{self.job['id']}/payapp/{inv['id']}/attachments", {"action": "add_record", "record_id": rec["id"]}, cookie=self.cookie)
        self.others_are_not_held_up_during(lambda: self.request("POST", f"/api/jobs/{self.job['id']}/send", {}, cookie=self.cookie))

    def test_the_waiver_step_reads_a_waiver_with_no_type_first(self):
        self.upload("")
        self.others_are_not_held_up_during(lambda: self.request("POST", f"/api/jobs/{self.job['id']}/waiver", {}, cookie=self.cookie))

    def test_attaching_a_record_and_confirming(self):
        res, state = self.upload("")
        rec = state["jobs"][-1]["records"][-1]
        res, state = self.others_are_not_held_up_during(lambda: self.request("POST", f"/api/jobs/{self.job['id']}/attach-record/{self.item['id']}", {"record_id": rec["id"]}, cookie=self.cookie))
        self.assertEqual(res.status, 200, state)
        res, state = self.others_are_not_held_up_during(lambda: self.request("POST", f"/api/jobs/{self.job['id']}/packet-confirm/{self.item['id']}", {"looked": True}, cookie=self.cookie))
        self.assertEqual(res.status, 200, state)

    def test_a_long_number_in_a_waiver_gives_a_warning_not_a_refusal(self):
        data = docx(f"<w:document {NS}><w:body><w:p><w:r><w:t>Birch deck conditional waiver {'9' * 400}</w:t></w:r></w:p></w:body></w:document>")
        res, state = self.request("POST", f"/api/import/file?job={self.job['id']}&file=1&kind=waiver&attach={self.item['id']}", raw=data, cookie=self.cookie,
                                  headers={"Content-Type": "application/octet-stream", "X-Filename": quote("w.docx")})
        self.assertEqual(res.status, 200, state)


class WaiverWording(unittest.TestCase):
    def problems(self, text, kind="conditional_progress", job="Oak Street"):
        return doc_check.check([("w.txt", ".txt", f"{job} $1,000.00 {text}".encode())], job, "$1,000.00", 100000, kind, True)["problems"]

    def test_unconditional_anywhere_flags_a_conditional_item(self):
        for text in ("UNCONDITIONAL WAIVER AND RELEASE UPON PAYMENT", "UNCONDITIONAL WAIVER AND RELEASE upon receipt", "UNCONDITIONAL WAIVER subject to payment",
                     "UNCONDITIONAL interim WAIVER", "upon payment, an UNCONDITIONAL waiver", "interim\nunconditional\nwaiver", "UN-CONDITIONAL waiver upon payment",
                     "un conditional waiver upon payment", "UN\nCONDITIONAL waiver interim", "Un\u2010conditional  waiver interim", "U N C O N D I T I O N A L waiver upon payment",
                     ):
            self.assertIn("It says unconditional, not conditional.", self.problems(text), text)

    def test_a_conditional_waiver_mentioning_it_gets_a_look(self):
        # Nov 6 review, A: one that talks about an unconditional waiver is still a look, said as a mention.
        for text in ("conditional waiver upon payment; this is not an unconditional release", "conditional waiver and also an unconditional one"):
            self.assertIn(doc_check.MENTIONS_UNCONDITIONAL, self.problems(text), text)

    def test_a_truly_conditional_waiver_passes(self):
        for text in ("Conditional waiver upon payment", "CONDITIONAL waiver", "interim waiver upon payment"):
            self.assertEqual(self.problems(text), [], text)

    def test_other_words_are_not_it(self):
        self.assertEqual(self.problems("run conditional waiver upon payment"), [])

    def test_a_filed_unconditional_waiver_still_passes(self):
        self.assertEqual(self.problems("Unconditional waiver", "unconditional_progress"), [])

    def test_conditional_on_an_unconditional_item_warns(self):
        for text in ("Conditional waiver and release", "CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT", "con-ditional waiver",
                     "Unconditional waiver, not a conditional one", "conditional waiver upon payment"):
            self.assertIn("It says conditional, not unconditional.", self.problems(text, "unconditional_progress"), text)
        got = doc_check.check([("w.txt", ".txt", b"Oak Street $1,000.00 Conditional waiver and release")], "Oak Street", "$1,000.00", 100000, "unconditional_progress", True)
        self.assertEqual(got["status"], "flagged", "a prompt to look; only send_waiver and attach decide what that does")

    def test_georgias_upon_payment_alone_stays_accepted_on_an_unconditional_item(self):
        for text in ("Interim waiver and release upon payment", "Waiver and release upon final payment", "Unconditional waiver and release upon payment"):
            problems = self.problems(text, "unconditional_progress") + self.problems(text, "unconditional_final")
            self.assertNotIn("It says conditional, not unconditional.", problems, text)
            self.assertNotIn("Unconditional wording not found.", problems, text)
        self.assertIn("It says conditional, not unconditional.", self.problems("Conditional waiver and release upon payment", "unconditional_progress"))

    def test_the_jobs_own_name_doesnt_count_on_an_unconditional_item(self):
        self.assertEqual(self.problems("Unconditional waiver", "unconditional_progress", job="Conditional Plaza"), [])

    def test_the_jobs_own_name_says_nothing_about_the_type(self):
        self.assertEqual(self.problems("Conditional waiver", job="Final Grade"), [])
        self.assertEqual(self.problems("Conditional waiver upon payment", job="Unconditional Plaza"), [])
        self.assertIn("It says final, not interim or progress.", self.problems("Conditional waiver upon final payment", job="Final Grade"), "its own word still counts")
        got = doc_check.check([("w.txt", ".txt", b"final\ngrade $1,000.00 conditional waiver")], "Final Grade", "$1,000.00", 100000, "conditional_progress", True)
        self.assertEqual(got["problems"], [], "the name split across lines")


class ALongNumber(unittest.TestCase):
    def test_a_long_number_is_just_not_the_amount(self):
        text = "Oak conditional waiver " + "9" * 400 + " and 1,000.00"
        got = doc_check.check([("w.txt", ".txt", text.encode())], "Oak", "$1,000.00", 100000, "conditional_progress", True)
        self.assertEqual(got["status"], "matched", got)
        text = "Oak conditional waiver " + ",".join("9" * 3 for _ in range(200))
        got = doc_check.check([("w.txt", ".txt", text.encode())], "Oak", "$1,000.00", 100000, "conditional_progress", True)
        self.assertIn("Amount not found: $1,000.00.", got["problems"])

    def test_anything_unexpected_is_a_warning_a_person_can_clear(self):
        with mock.patch.object(doc_check, "_has_job_name", side_effect=RuntimeError("no")):
            got = doc_check.check([("w.txt", ".txt", b"Oak conditional waiver text")], "Oak")
        self.assertEqual(got["status"], "flagged")
        self.assertIn("couldn't be checked", got["problems"][0])


if __name__ == "__main__":
    unittest.main()
