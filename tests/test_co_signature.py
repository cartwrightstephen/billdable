"""Signed change orders (P2-3): approval needs a signature online or a signed copy on record."""

import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import domain
import importer
import payapp_pdf
import server
import sov
import storage
from seed import seed_state

PASSWORD = "correct horse battery"
SITE = "https://app.duracash.example"


def pending(state):
    job = next(j for j in state["jobs"] if any(c["status"] == "pending" for c in j["change_orders"]))
    return job, next(c for c in job["change_orders"] if c["status"] == "pending")


class DomainTests(unittest.TestCase):
    def test_online_signature_keeps_what_was_signed(self):
        state = seed_state()
        job, co = pending(state)
        with self.assertRaises(domain.GateError):
            domain.request_co_signature(state, job["id"], co["id"], "owner")  # no site address
        link = domain.request_co_signature(state, job["id"], co["id"], "owner", link_base=SITE, company_id="co-x")
        self.assertTrue(link.startswith(f"{SITE}/s/co-x."))
        self.assertEqual(state["email_log"][-1]["kind"], "co_signature")
        self.assertIn(link, state["email_log"][-1]["body"])
        with self.assertRaisesRegex(domain.GateError, "full name"):
            domain.sign_change_order(state, job, co, " ", True, "203.0.113.5", "Safari")
        with self.assertRaisesRegex(domain.GateError, "agree"):
            domain.sign_change_order(state, job, co, "Mara Ellison", False, "203.0.113.5", "Safari")
        domain.sign_change_order(state, job, co, "  Mara   Ellison ", True, "203.0.113.5", "Safari")
        sig = co["signature"]
        self.assertEqual((co["status"], sig["name"], sig["ip"]), ("approved", "Mara Ellison", "203.0.113.5"))
        self.assertEqual(sig["snapshot"]["amount_cents"], co["amount_cents"])
        self.assertEqual(sig["snapshot_sha256"], domain.snapshot_sha256(sig["snapshot"]))
        self.assertEqual(sig["consent"], domain.CO_CONSENT)
        with self.assertRaises(domain.GateError):
            domain.sign_change_order(state, job, co, "Someone", True, "x", "y")

    def test_paper_signature_needs_the_signed_copy_on_record(self):
        state = seed_state()
        job, co = pending(state)
        with self.assertRaisesRegex(domain.GateError, "job record"):
            domain.mark_co_signed(state, job["id"], co["id"], "", "owner")
        domain.add_job_record(state, job["id"], "Signed CO scan", "change_order", "", "owner")
        rec = job["records"][-1]["id"]
        domain.mark_co_signed(state, job["id"], co["id"], rec, "owner")
        self.assertEqual(co["status"], "approved")
        self.assertEqual(co["signed_evidence"]["record_id"], rec)

    def test_signing_takes_unsigned_off_the_open_drafts_line(self):
        # Oak Street bills CO 7 unsigned on its draft; once signed, the line (and so the PDF) no
        # longer says "(unsigned)".
        state = seed_state()
        job = next(j for j in state["jobs"] if j["id"] == "job-oak")
        co = next(c for c in job["change_orders"] if c["status"] == "pending")
        domain.draft_invoice(state, job["id"], "owner")
        draft = job["invoices"][-1]
        line = next(l for l in draft["lines"] if l.get("co_id") == co["id"])
        self.assertIn("(unsigned)", line["label"])
        domain.add_job_record(state, job["id"], "Signed CO scan", "change_order", "", "owner")
        domain.mark_co_signed(state, job["id"], co["id"], job["records"][-1]["id"], "owner")
        self.assertEqual(line["label"], f"CO {co['number']} — {co['title']}")
        pdf = next(x for x in payapp_pdf.for_invoice(state, job["id"], draft["id"]) if isinstance(x, (bytes, bytearray)))
        self.assertNotIn(b"unsigned", pdf)


class CreditTests(unittest.TestCase):
    """A change order with a negative amount is a credit (R3-06)."""

    def sign(self, state, job, co):
        domain.add_job_record(state, job["id"], f"Signed CO {co['number']}", "change_order", "", "owner")
        domain.mark_co_signed(state, job["id"], co["id"], job["records"][-1]["id"], "owner")

    def test_credit_on_a_dollar_job_lowers_the_contract_and_line_2(self):
        from test_money import new_job, new_state, send_open
        import payapp_pdf
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        domain.add_change_order(state, job["id"], "Owner supplies the tile", -250_000, description="Tile by owner")
        co = job["change_orders"][-1]
        with self.assertRaisesRegex(domain.GateError, "credit"):
            domain.include_change_order(state, job["id"], co["id"])
        self.sign(state, job, co)
        self.assertEqual(domain.job_money(job)["contract_total_cents"], 750_000)
        inv = send_open(state, job)
        figures = payapp_pdf.summary(job, inv)
        self.assertEqual((figures["net_co"], figures["contract_to_date"]), (-250_000, 750_000))
        self.assertEqual(figures["balance"], 750_000 - 400_000)
        # Done: the work done is the lowered contract, so the last pay app bills what is left of it.
        domain.mark_done(state, job["id"])
        self.assertEqual(job["invoices"][-1]["amount_cents"], 350_000)

    def test_credit_on_a_schedule_of_values_comes_off_the_next_pay_app(self):
        from test_sov import sov_job, line
        from test_money import new_state, send_open
        import payapp_pdf
        state = new_state()
        job = sov_job(state, retainage=0)
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "100"}, "first floor")
        first = send_open(state, job)
        domain.add_change_order(state, job["id"], "Panel by owner", -250_000)
        co = job["change_orders"][-1]
        self.sign(state, job, co)
        self.assertEqual(domain.job_money(job)["contract_total_cents"], 3_450_000 - 250_000)
        credit_line = next(l for l in sov.lines(job) if l.get("co_id") == co["id"])
        self.assertEqual(credit_line["scheduled_cents"], -250_000)
        # Nothing new done: the credit waits, and nothing can be drafted from it alone.
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)
        domain.record_sov_progress(state, job["id"], {line(job, "1")["id"]: "300"})  # drafts the next pay app
        inv = job["invoices"][-1]
        self.assertIn(-250_000, [l["amount_cents"] for l in inv["lines"]])
        figures = payapp_pdf.summary(job, inv)
        self.assertEqual(figures["net_co"], -250_000)
        self.assertEqual(figures["contract_to_date"], 3_200_000)
        # 200 more ea at $45 plus 15% O&P, less the $2,500 credit.
        self.assertEqual(inv["gross_cents"], 200 * 4500 * 115 // 100 - 250_000)
        self.assertNotEqual(first["id"], inv["id"])


class SigningHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = root / "auth.json", root / "companies", root, root / "uploads"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.env = mock.patch.dict(os.environ, {"DURACASH_URL": SITE})
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        headers, data = {"User-Agent": "TestBrowser/1.0"}, None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if form is not None:
            data, headers["Content-Type"] = urlencode(form).encode(), "application/x-www-form-urlencoded"
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res, raw

    def test_request_sign_and_see_it_approved(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "cosign@example.com", "password": PASSWORD,
                                                           "name": "Pat", "company_name": "Pine"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, raw = self.request("POST", "/api/jobs", {"name": "Elm", "customer": "Park GC", "address": "9 Elm", "approver": "Kim",
                                                      "ap_email": "ap@park.example", "contract_cents": 1_000_000}, cookie)
        job = json.loads(raw)["jobs"][-1]
        res, raw = self.request("POST", f"/api/jobs/{job['id']}/add-co", {"title": "Two <b>outlets</b>", "amount_cents": 45_000}, cookie)
        co = json.loads(raw)["jobs"][-1]["change_orders"][-1]
        res, raw = self.request("POST", f"/api/jobs/{job['id']}/approve-co/{co['id']}", {}, cookie)
        self.assertEqual(res.status, 409, "no bare approval")
        res, raw = self.request("POST", f"/api/jobs/{job['id']}/request-co-signature/{co['id']}", {}, cookie)
        self.assertEqual(res.status, 200, raw)
        state = json.loads(raw)
        link = re.search(re.escape(SITE) + r"(/s/\S+)", state["email_log"][-1]["body"]).group(1)
        # The app is told only that the page exists, and opens it through the server (R6-05).
        sent_co = state["jobs"][-1]["change_orders"][-1]
        self.assertTrue(sent_co["signing_page"])
        self.assertNotIn("sign_token", sent_co)
        res, _ = self.request("GET", f"/api/jobs/{job['id']}/co/{co['id']}/page", cookie=cookie)
        self.assertEqual((res.status, res.getheader("Location")), (303, link))
        res, raw = self.request("GET", link)
        self.assertEqual(res.status, 200)
        self.assertIn("Two &lt;b&gt;outlets&lt;/b&gt;", raw.decode())
        res, raw = self.request("POST", link + "/sign", form={"name": "Kim Park"})
        self.assertEqual(res.status, 400)
        self.assertIn("Tick the box", raw.decode())
        res, _ = self.request("POST", link + "/sign", form={"name": "Kim Park", "agree": "yes"})
        self.assertEqual((res.status, res.getheader("Location")), (303, link))
        res, raw = self.request("GET", link)
        self.assertIn("Signed</strong> by Kim Park", raw.decode())
        res, raw = self.request("GET", "/api/state", cookie=cookie)
        signed = json.loads(raw)["jobs"][-1]["change_orders"][-1]
        self.assertEqual((signed["status"], signed["signature"]["user_agent"]), ("approved", "TestBrowser/1.0"))
        self.assertEqual(self.request("GET", link[:-4] + "zzzz")[0].status, 404)
        self.assertEqual(server.redact(link), re.sub(r"\.[^.]+$", ".[token]", link))


if __name__ == "__main__":
    unittest.main()
