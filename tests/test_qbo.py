"""QuickBooks Online, read-only (P2-1), against a local stand-in for Intuit's token and query
endpoints. The shapes here are what qbo.py expects; the first real sandbox connection checks them."""

import base64
import csv
import http.client
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import domain
import qbo
import server
import storage
from seed import seed_state
from test_money import new_job, new_state, send_open

CLIENT = ("qbo-client", "qbo-secret")
REALM = "4620816365"
INVOICES = [
    {"Id": "9", "DocNumber": "PA-BRK-01", "TotalAmt": 57950.00, "Balance": 0, "CustomerRef": {"name": "Ponce Property Co."}},
    {"Id": "10", "DocNumber": "PA-TRN-01", "TotalAmt": 119000.00, "Balance": 119000.00, "CustomerRef": {"name": "Northline GC"}},
]
PAYMENTS = [
    {"Id": "55", "TxnDate": "2026-10-09", "TotalAmt": 57950.00, "CustomerRef": {"name": "Ponce Property Co."},
     "Line": [{"LinkedTxn": [{"TxnId": "9", "TxnType": "Invoice"}]}]},
    {"Id": "56", "TxnDate": "2026-10-10", "TotalAmt": 119700.00, "PaymentRefNum": "10233", "CustomerRef": {"name": "Northline GC"}},
    {"Id": "57", "TxnDate": "2026-10-11", "TotalAmt": 1250.00, "CustomerRef": {"name": "Mara Ellison"}},
]


class FakeIntuit(BaseHTTPRequestHandler):
    tokens = {"access": None, "refresh": None, "n": 0}
    queries = []

    def log_message(self, *a):
        pass

    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _issue(self):
        t = FakeIntuit.tokens
        t["n"] += 1
        t["access"], t["refresh"] = f"A{t['n']}", f"R{t['n']}"
        return {"access_token": t["access"], "refresh_token": t["refresh"], "expires_in": 3600, "x_refresh_token_expires_in": 8726400}

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        if self.path == "/revoke":
            return self._reply(200, {})
        basic = "Basic " + base64.b64encode(f"{CLIENT[0]}:{CLIENT[1]}".encode()).decode()
        if self.headers.get("Authorization") != basic:
            return self._reply(401, {"error": "invalid_client"})
        grant = form.get("grant_type", [""])[0]
        if grant == "authorization_code" and form.get("code") == ["good-code"]:
            return self._reply(200, self._issue())
        if grant == "refresh_token" and form.get("refresh_token") == [FakeIntuit.tokens["refresh"]]:
            return self._reply(200, self._issue())
        self._reply(400, {"error": "invalid_grant"})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != f"/v3/company/{REALM}/query" or self.headers.get("Authorization") != f"Bearer {FakeIntuit.tokens['access']}":
            return self._reply(401, {"Fault": {"type": "AUTHENTICATION"}})
        sql = parse_qs(url.query)["query"][0]
        FakeIntuit.queries.append(sql)
        entity = sql.split()[3]
        rows = {"Payment": PAYMENTS, "Invoice": INVOICES}[entity]
        start = int(sql.split("STARTPOSITION ")[1].split()[0])
        size = int(sql.split("MAXRESULTS ")[1].split()[0])
        self._reply(200, {"QueryResponse": {entity: rows[start - 1:start - 1 + size]}})


class InvoiceCsvTests(unittest.TestCase):  # R4-11: Download for QuickBooks
    def rows(self, state, job, inv):
        return list(csv.DictReader(io.StringIO(qbo.invoice_csv(state, job, inv))))

    def test_columns_and_sums_equal_the_pay_app(self):
        state = new_state()
        job = new_job(state, contract=10_000_000, retainage=10)
        domain.update_payer(state, job["payer_id"], {"payment_terms_days": 30})
        domain.add_change_order(state, job["id"], "Extra outlet", 50_000)
        co = job["change_orders"][-1]
        domain.approve_change_order(state, job["id"], co["id"])
        domain.record_progress(state, job["id"], 4_000_000)
        inv = send_open(state, job)
        text = qbo.invoice_csv(state, job, inv)
        self.assertEqual(text.splitlines()[0], "InvoiceNo,Customer,InvoiceDate,DueDate,Item,ItemDescription,ItemAmount")
        rows = self.rows(state, job, inv)
        self.assertEqual(len(rows), len(inv["lines"]))
        self.assertEqual({r["InvoiceNo"] for r in rows}, {inv["number"]})
        self.assertEqual({r["Customer"] for r in rows}, {"Jones Family"})
        sent, due = inv["sent_at"][:10], inv["due_on"]
        self.assertEqual(rows[0]["InvoiceDate"], f"{sent[5:7]}/{sent[8:10]}/{sent[:4]}")
        self.assertEqual(rows[0]["DueDate"], f"{due[5:7]}/{due[8:10]}/{due[:4]}")
        cents = [round(float(r["ItemAmount"]) * 100) for r in rows]
        self.assertEqual(sum(cents), inv["amount_cents"], "the lines add up to what the pay app asks for")
        self.assertEqual(sum(c for c in cents if c > 0), inv["gross_cents"])
        ret = [r for r in rows if r["Item"] == "Retainage"]
        self.assertEqual([round(float(r["ItemAmount"]) * 100) for r in ret], [-inv["retainage_cents"]], "retainage as a negative line")
        self.assertTrue(all(r["ItemDescription"].startswith("Pine kitchen: ") for r in rows))

    def test_drafts_are_refused_and_formulas_are_defused(self):
        state = new_state()
        job = new_job(state, name="=HYPERLINK(1)")
        domain.record_progress(state, job["id"], 1_000_000)
        draft = job["invoices"][-1]
        with self.assertRaises(domain.GateError):
            qbo.invoice_csv(state, job, draft)
        inv = send_open(state, job)
        self.assertTrue(all(r["ItemDescription"].startswith("'=") for r in self.rows(state, job, inv)))


class QboTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = root / "auth.json", root / "companies", root
        cls.intuit = ThreadingHTTPServer(("127.0.0.1", 0), FakeIntuit)
        threading.Thread(target=cls.intuit.serve_forever, daemon=True).start()
        fake = f"http://127.0.0.1:{cls.intuit.server_address[1]}"
        cls.env = mock.patch.dict("os.environ", {"QBO_CLIENT_ID": CLIENT[0], "QBO_CLIENT_SECRET": CLIENT[1], "QBO_TOKEN_URL": fake + "/token",
                                                 "QBO_REVOKE_URL": fake + "/revoke", "QBO_API_BASE": fake, "DURACASH_URL": "http://localhost:8765"})
        cls.env.start()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        for s in (cls.httpd, cls.intuit):
            s.shutdown()
            s.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        headers = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})}
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            return res, json.loads(raw or b"{}")
        except ValueError:
            return res, raw.decode()

    def company(self, email):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": "correct horse battery",
                                                              "name": "Pat Pine", "company_name": "Hartline Remodeling"})
        cookie, cid = res.getheader("Set-Cookie").split(";")[0], data["user"]["company_id"]
        server.save_company(cid, seed_state())  # sent pay apps to match against
        return cookie, cid

    def connect(self, cookie):
        res, data = self.request("POST", "/api/qbo/connect", {}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        url = urlparse(data["url"])
        self.assertEqual(url.netloc, "appcenter.intuit.com")
        q = parse_qs(url.query)
        self.assertEqual(q["scope"], [qbo.SCOPE])
        self.assertEqual(q["redirect_uri"], ["http://localhost:8765/api/qbo/callback"])
        return q["state"][0]

    def test_connect_read_match_once_refresh_reconcile_disconnect(self):
        cookie, cid = self.company("qbo@example.com")
        state = self.connect(cookie)
        res, page = self.request("GET", f"/api/qbo/callback?code=good-code&state={state}&realmId={REALM}")
        self.assertEqual((res.status, res.getheader("Location")), (303, "/money/payments"))
        res, page = self.request("GET", f"/api/qbo/callback?code=good-code&state={state}&realmId={REALM}")
        self.assertEqual(res.status, 400, "a state works once")
        res, st = self.request("GET", "/api/qbo/status", cookie=cookie)
        self.assertTrue(st["connected"])
        self.assertNotIn("refresh_token", json.dumps(st), "tokens never reach the browser")

        with mock.patch.object(qbo, "PAGE", 2):  # paging: three payments over two pages
            res, data = self.request("POST", "/api/qbo/payments", {"days": 90}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        rows = {r["qbo_id"]: r for r in data["rows"]}
        self.assertEqual(rows["55"]["number"], "PA-BRK-01", "matched by the invoice number QuickBooks applied it to")
        self.assertEqual(rows["56"]["number"], "PA-TRN-01", "matched by customer and amount")
        self.assertEqual(rows["57"]["status"], "no match")
        picks = [{"job_id": r["job_id"], "invoice_id": r["invoice_id"], "amount_cents": r["amount_cents"], "reference": r["ref"],
                  "date": r["date"], "qbo_id": r["qbo_id"]} for r in data["rows"] if r["status"] == "match"]
        res, applied = self.request("POST", "/api/import/payments-apply", {"payments": picks}, cookie=cookie)
        self.assertEqual([x["ok"] for x in applied["import_results"]], [True, True])
        res, again = self.request("POST", "/api/import/payments-apply", {"payments": picks}, cookie=cookie)
        self.assertEqual([x["ok"] for x in again["import_results"]], [False, False], "the same QuickBooks payment is never recorded twice")

        # The access token expires after an hour; the next read refreshes it (and Intuit rotates the refresh token).
        conns = json.loads((storage.DATA_DIR / "qbo_connections.json").read_text())
        conns[cid]["access_expires_at"] = "2000-01-01T00:00:00+00:00"
        storage.write_json(storage.DATA_DIR / "qbo_connections.json", conns)
        res, data = self.request("POST", "/api/qbo/payments", {}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        self.assertEqual({r["qbo_id"]: r["status"] for r in data["rows"]}, {"55": "already recorded", "56": "already recorded", "57": "no match"})
        self.assertEqual(json.loads((storage.DATA_DIR / "qbo_connections.json").read_text())[cid]["refresh_token"], FakeIntuit.tokens["refresh"])

        res, rec = self.request("GET", "/api/qbo/reconcile", cookie=cookie)
        kinds = {(r["number"], r["kind"]) for r in rec["rows"]}
        self.assertIn(("PA-TRN-01", "amount"), kinds, "same number, different total")
        self.assertIn(("PA-TRN-01", "open_in_qbo"), kinds)
        self.assertIn(("PA-OAK-01", "missing"), kinds)
        self.assertNotIn("PA-BRK-01", {n for n, _ in kinds}, "paid on both sides, same amount")
        missing = next(r for r in rec["rows"] if r["kind"] == "missing")
        res, text = self.request("GET", f"/api/jobs/{missing['job_id']}/payapp/{missing['invoice_id']}.csv", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertTrue(res.getheader("Content-Type").startswith("text/csv"))
        self.assertIn("attachment", res.getheader("Content-Disposition"))
        self.assertTrue(text.startswith("InvoiceNo,Customer,"))
        self.assertIn(missing["number"], text)
        res, _ = self.request("GET", f"/api/jobs/{missing['job_id']}/payapp/nope.csv", cookie=cookie)
        self.assertEqual(res.status, 404)
        res, _ = self.request("GET", f"/api/jobs/{missing['job_id']}/payapp/{missing['invoice_id']}.csv")
        self.assertEqual(res.status, 401)

        res, st = self.request("POST", "/api/qbo/disconnect", {}, cookie=cookie)
        self.assertFalse(st["connected"])
        res, data = self.request("POST", "/api/qbo/payments", {}, cookie=cookie)
        self.assertEqual(res.status, 409)

    def test_refusals(self):
        cookie, cid = self.company("qbo2@example.com")
        res, page = self.request("GET", "/api/qbo/callback?code=good-code&state=made-up&realmId=1")
        self.assertEqual(res.status, 400)
        self.assertIn("expired", page)
        state = self.connect(cookie)
        res, page = self.request("GET", f"/api/qbo/callback?error=access_denied&state={state}")
        self.assertIn("You cancelled it", page)
        state = self.connect(cookie)
        res, page = self.request("GET", f"/api/qbo/callback?code=bad-code&state={state}&realmId={REALM}")
        self.assertEqual(res.status, 400, "Intuit refused the code")
        with mock.patch.dict("os.environ", {"QBO_CLIENT_ID": ""}):
            res, data = self.request("POST", "/api/qbo/connect", {}, cookie=cookie)
            self.assertEqual(res.status, 409)
            res, st = self.request("GET", "/api/qbo/status", cookie=cookie)
            self.assertFalse(st["configured"])


if __name__ == "__main__":
    unittest.main()
