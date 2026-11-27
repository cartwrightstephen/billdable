"""QuickBooks Online both ways (qbo_sync.py): a pay app sent as an invoice, and payments recorded in
QuickBooks against it read back. Against a local stand-in for Intuit's API (no network); the shapes
are what qbo_sync.py expects, and the first real sandbox connection checks them."""

import base64
import http.client
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import domain
import monthly
import qbo
import qbo_sync
import server
import storage
from seed import seed_state
from test_money import new_job, new_state, send_open

CLIENT = ("qbo-client", "qbo-secret")
REALM = "4620816365"


class Books:
    """What the stand-in QuickBooks company holds, and what it was asked."""

    def __init__(self):
        self.next_id = 100
        self.items = {"1": {"Id": "1", "Name": "Construction billing", "Type": "Service", "Active": True, "IncomeAccountRef": {"name": "Construction income"}},
                      "2": {"Id": "2", "Name": "Retainage", "Type": "Service", "Active": True, "IncomeAccountRef": {"name": "Retainage Receivable"}},
                      "3": {"Id": "3", "Name": "Lumber", "Type": "Inventory", "Active": True}}
        self.customers = {"58": {"Id": "58", "DisplayName": "Northline GC", "Active": True, "SyncToken": "0",
                                 "PrimaryEmailAddr": {"Address": "dpatel@northlinegc.example"}}}
        self.invoices, self.payments = {}, {}
        self.log, self.faults, self.request_ids = [], [], {}
        self.access = "A1"

    def new_id(self):
        self.next_id += 1
        return str(self.next_id)


BOOKS = Books()


def fault(code, message, detail=""):
    return {"Fault": {"Error": [{"Message": message, "Detail": detail, "code": code}], "type": "ValidationFault"}, "time": "2026-11-27T10:00:00.000-08:00"}


class FakeQbo(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, payload, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method):
        b = BOOKS
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if url.path == "/token":
            form = parse_qs(raw)
            basic = "Basic " + base64.b64encode(f"{CLIENT[0]}:{CLIENT[1]}".encode()).decode()
            if self.headers.get("Authorization") != basic or form.get("grant_type") != ["refresh_token"]:
                return self._reply(400, {"error": "invalid_grant"})
            b.access = f"A{int(b.access[1:]) + 1}"
            b.log.append(("REFRESH", "", {}, {"refresh_token": (form.get("refresh_token") or [""])[0]}))
            return self._reply(200, {"access_token": b.access, "refresh_token": "R" + b.access[1:], "expires_in": 3600, "x_refresh_token_expires_in": 8726400})
        body = json.loads(raw) if raw else None
        path = url.path.split(f"/v3/company/{REALM}/", 1)[-1]
        b.log.append((method, path, q, body))
        for i, (where, code, payload, headers) in enumerate(b.faults):
            if where in f"{method} {path}":
                b.faults.pop(i)
                return self._reply(code, payload, headers)
        if self.headers.get("Authorization") != f"Bearer {b.access}":
            return self._reply(401, {"Fault": {"Error": [{"Message": "AuthenticationFailed", "code": "3200"}], "type": "AUTHENTICATION"}})
        if q.get("minorversion") != qbo.MINOR_VERSION:
            return self._reply(400, fault("2010", "Unsupported minor version"))
        if method == "POST" and q.get("requestid") in b.request_ids:
            return self._reply(200, b.request_ids[q["requestid"]])
        reply = self._route(method, path, q, body)
        if isinstance(reply, tuple):
            return self._reply(*reply)
        if method == "POST" and q.get("requestid"):
            b.request_ids[q["requestid"]] = reply
        self._reply(200, reply)

    def _route(self, method, path, q, body):
        b = BOOKS
        if method == "GET" and path == "query":
            sql = q["query"]
            entity = sql.split()[3]
            rows = {"Item": b.items, "Customer": b.customers, "Invoice": b.invoices, "Payment": b.payments}[entity].values()
            if "Active = true" in sql:
                rows = [r for r in rows if r.get("Active", True)]
            if "DocNumber = '" in sql:
                want = sql.split("DocNumber = '")[1].split("'")[0]
                rows = [r for r in rows if r.get("DocNumber") == want]
            if "TxnDate >= '" in sql:
                since = sql.split("TxnDate >= '")[1].split("'")[0]
                rows = [r for r in rows if r.get("TxnDate", "") >= since]
            return {"QueryResponse": {entity: list(rows)}}
        kind, _, oid = path.partition("/")
        store = {"customer": b.customers, "invoice": b.invoices}.get(kind)
        if store is None:
            return 400, fault("2500", "Invalid Reference Id")
        if method == "GET":
            return {kind.title(): store[oid]} if oid in store else (400, fault("610", "Object Not Found", "Something you're trying to use has been made inactive."))
        if kind == "customer":
            if any(c["DisplayName"].lower() == body["DisplayName"].lower() for c in b.customers.values()):
                return 400, fault("6240", "Duplicate Name Exists Error", "The name supplied already exists. : Another customer, vendor or employee is already using this name.")
            made = {**body, "Id": b.new_id(), "SyncToken": "0", "Active": True}
            b.customers[made["Id"]] = made
            return {"Customer": made}
        # invoice
        for line in body["Line"]:
            item = line["SalesItemLineDetail"]["ItemRef"]["value"]
            if item not in b.items:
                return 400, fault("2500", "Invalid Reference Id", f"Item {item} not found")
            if round(line["SalesItemLineDetail"]["Qty"] * line["SalesItemLineDetail"]["UnitPrice"], 2) != line["Amount"]:
                return 400, fault("6070", "Amount is not equal to UnitPrice * Qty")
        if body["CustomerRef"]["value"] not in b.customers:
            return 400, fault("2500", "Invalid Reference Id", "Customer not found")
        total = round(sum(l["Amount"] for l in body["Line"]), 2)
        if "Id" in body:
            have = b.invoices.get(body["Id"])
            if not have:
                return 400, fault("610", "Object Not Found")
            if body.get("SyncToken") != have["SyncToken"]:
                return 400, fault("5010", "Stale Object Error", "You and root were working on this at the same time.")
            paid = have["TotalAmt"] - have["Balance"]
            have.update({k: v for k, v in body.items() if k not in ("sparse",)}, SyncToken=str(int(have["SyncToken"]) + 1),
                        TotalAmt=total, Balance=round(total - paid, 2))
            return {"Invoice": have}
        made = {**body, "Id": b.new_id(), "SyncToken": "0", "TotalAmt": total, "Balance": total}
        b.invoices[made["Id"]] = made
        return {"Invoice": made}

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def pay_in_qbo(qbo_invoice_id, dollars, ref="", txn_date="2026-11-20", total=None, more=()):
    """A payment recorded in QuickBooks against one or more invoices; bumps the invoice's SyncToken as QuickBooks does."""
    pid = BOOKS.new_id()
    lines = [{"Amount": dollars, "LinkedTxn": [{"TxnId": qbo_invoice_id, "TxnType": "Invoice"}]}]
    lines += [{"Amount": d, "LinkedTxn": [{"TxnId": t, "TxnType": "Invoice"}]} for t, d in more]
    BOOKS.payments[pid] = {"Id": pid, "TxnDate": txn_date, "TotalAmt": total if total is not None else round(sum(l["Amount"] for l in lines), 2),
                           "CustomerRef": {"value": "58"}, "Line": lines, **({"PaymentRefNum": ref} if ref else {})}
    for line in lines:
        inv = BOOKS.invoices[line["LinkedTxn"][0]["TxnId"]]
        inv["Balance"] = round(inv["Balance"] - line["Amount"], 2)
        inv["SyncToken"] = str(int(inv["SyncToken"]) + 1)
    return pid


class FakeCase(unittest.TestCase):
    """The stand-in QuickBooks on a local port, a temporary data dir, and one connected company."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = root / "auth.json", root / "companies", root
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeQbo)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        cls.env = mock.patch.dict("os.environ", {"QBO_CLIENT_ID": CLIENT[0], "QBO_CLIENT_SECRET": CLIENT[1], "QBO_TOKEN_URL": base + "/token",
                                                 "QBO_REVOKE_URL": base + "/revoke", "QBO_API_BASE": base, "DURACASH_URL": "http://localhost:8765"})
        cls.env.start()
        cls.backoff = mock.patch.object(qbo_sync, "BACKOFF", 0)
        cls.backoff.start()

    @classmethod
    def tearDownClass(cls):
        cls.backoff.stop()
        cls.env.stop()
        cls.fake.shutdown()
        cls.fake.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        global BOOKS
        BOOKS = Books()
        self.cid = "co-qbo-sync"
        qbo._store(self.cid, {"access_token": "A1", "refresh_token": "R1", "realm_id": REALM,
                              "access_expires_at": "2999-01-01T00:00:00+00:00", "refresh_expires_at": "2999-01-01T00:00:00+00:00",
                              "connected_at": "2026-11-27T00:00:00+00:00", "connected_by": "Pat"})

    def map_items(self, state, retainage=True):
        qbo_sync.save_mapping(state, {"id": "1", "name": "Construction billing"}, {"id": "2", "name": "Retainage"} if retainage else None, "Pat")

    def job_with_retainage(self, customer="Jones Family"):
        state = new_state()
        job = new_job(state, contract=10_000_000, retainage=10, customer=customer)
        domain.add_change_order(state, job["id"], "Extra outlet", 50_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        domain.record_progress(state, job["id"], 4_000_000)
        inv = send_open(state, job)
        self.map_items(state)
        return state, job, inv

    def push(self, state, job, inv, **kw):
        result = qbo_sync.push(self.cid, state, job["id"], inv["id"], **kw)
        qbo_sync.apply_push(state, result, "Pat")
        return result


class PushTests(FakeCase):
    def test_new_customer_and_invoice_lines_mirror_the_pay_app_with_retainage_negative(self):
        state, job, inv = self.job_with_retainage()
        result = self.push(state, job, inv)
        self.assertEqual((result["action"], result["customer"]["how"]), ("created", "created"))
        made = BOOKS.customers[result["customer"]["id"]]
        self.assertEqual((made["DisplayName"], made["PrimaryEmailAddr"]["Address"]), ("Jones Family", "ap@jones.example"))
        payer = next(p for p in state["payers"] if p["id"] == job["payer_id"])
        self.assertEqual(payer["qbo_customer_id"], made["Id"], "the customer is kept on the client")
        sent = BOOKS.invoices[inv["qbo"]["invoice_id"]]
        self.assertEqual(sent["DocNumber"], inv["number"])
        self.assertEqual(sent["TxnDate"], inv["sent_at"][:10])
        if inv.get("due_on"):
            self.assertEqual(sent["DueDate"], inv["due_on"])
        self.assertEqual(round(sent["TotalAmt"] * 100), inv["amount_cents"], "QuickBooks' total is what the pay app asks for")
        self.assertEqual(len(sent["Line"]), len(inv["lines"]), "one QuickBooks line per pay app line")
        held = [l for l in sent["Line"] if l["SalesItemLineDetail"]["ItemRef"]["value"] == "2"]
        self.assertEqual([round(l["Amount"] * 100) for l in held], [-inv["retainage_cents"]], "retainage as one negative line on the retainage item")
        work = [l for l in sent["Line"] if l["SalesItemLineDetail"]["ItemRef"]["value"] == "1"]
        self.assertEqual(sum(round(l["Amount"] * 100) for l in work), inv["gross_cents"])
        self.assertTrue(all(l["Description"].startswith("Pine kitchen: ") for l in sent["Line"]))
        self.assertEqual((inv["qbo"]["sync_token"], inv["qbo"]["pushed_by"]), ("0", "Pat"))
        self.assertEqual(state["audit"][-1]["action"], "qbo_pushed")
        self.assertTrue(all(q.get("minorversion") == qbo.MINOR_VERSION for _, _, q, _ in BOOKS.log), "minorversion on every call")
        self.assertTrue(all(q.get("requestid") for m, _, q, _ in BOOKS.log if m == "POST"), "a requestid on every write")

    def test_sending_again_updates_the_same_invoice_with_its_sync_token(self):
        state, job, inv = self.job_with_retainage()
        self.push(state, job, inv)
        first = inv["qbo"]["invoice_id"]
        again = self.push(state, job, inv)
        self.assertEqual((again["action"], again["customer"]["how"]), ("updated", "linked"))
        self.assertEqual(inv["qbo"]["invoice_id"], first, "no second invoice")
        self.assertEqual(len(BOOKS.invoices), 1)
        self.assertEqual(len(BOOKS.customers), 2, "no second customer")
        self.assertEqual(inv["qbo"]["sync_token"], "1", "the new SyncToken is kept")
        update = [b for m, p, q, b in BOOKS.log if m == "POST" and p == "invoice"][-1]
        self.assertEqual((update["Id"], update["SyncToken"], update["sparse"]), (first, "0", True))

    def test_a_revised_pay_app_sent_again_updates_the_same_invoice(self):
        # Integration (Nov 27): delivery's Revise puts a sent pay app back to a draft with its number;
        # corrected (record_progress replaces the draft) and sent again, it keeps its QuickBooks
        # invoice, so Send to QuickBooks updates that one with its SyncToken instead of making another.
        state, job, inv = self.job_with_retainage()
        self.push(state, job, inv)
        first, old_total = inv["qbo"]["invoice_id"], inv["amount_cents"]
        domain.revise_sent_pay_app(state, job["id"], inv["id"], "Wrong amount", "Pat")
        self.assertIn(f"QuickBooks invoice {inv['qbo']['doc_number']} still shows {domain.money(old_total)}", state["audit"][-1]["detail"])
        domain.record_progress(state, job["id"], 4_500_000)
        draft = next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        self.assertEqual((draft["number"], draft["qbo"]["invoice_id"]), (inv["number"], first), "the QuickBooks link moves to the corrected draft")
        self.assertNotIn("qbo", inv, "the replaced draft no longer claims it")
        with self.assertRaises(domain.GateError):
            qbo_sync.push(self.cid, state, job["id"], draft["id"])   # not until it has gone again
        again = send_open(state, job)
        self.assertNotEqual(again["amount_cents"], old_total)
        result = self.push(state, job, again)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(len(BOOKS.invoices), 1, "no second invoice")
        update = [b for m, p, q, b in BOOKS.log if m == "POST" and p == "invoice"][-1]
        self.assertEqual((update["Id"], update["SyncToken"]), (first, "0"))
        self.assertEqual(round(BOOKS.invoices[first]["TotalAmt"] * 100), again["amount_cents"])
        self.assertEqual(qbo_sync.pushed(state)[first][1]["id"], again["id"])

    def test_an_invoice_changed_in_quickbooks_is_replaced_only_when_asked(self):
        state, job, inv = self.job_with_retainage()
        self.push(state, job, inv)
        sent = BOOKS.invoices[inv["qbo"]["invoice_id"]]
        sent.update(TotalAmt=1.0, Balance=1.0, SyncToken="3")   # someone edited it in QuickBooks
        with self.assertRaises(qbo_sync.QboApiError) as caught:
            qbo_sync.push(self.cid, state, job["id"], inv["id"])
        self.assertEqual(caught.exception.code, "changed")
        self.assertIn("changed in QuickBooks", str(caught.exception))
        self.push(state, job, inv, overwrite=True)
        self.assertEqual(round(sent["TotalAmt"] * 100), inv["amount_cents"])
        self.assertEqual(sent["SyncToken"], "4", "the update used QuickBooks' current SyncToken")

    def test_a_stale_sync_token_says_so_in_plain_words(self):
        state, job, inv = self.job_with_retainage()
        self.push(state, job, inv)
        BOOKS.faults.append(("POST invoice", 400, fault("5010", "Stale Object Error", "You and root were working on this at the same time."), {}))
        with self.assertRaises(qbo_sync.QboApiError) as caught:
            qbo_sync.push(self.cid, state, job["id"], inv["id"])
        self.assertEqual(caught.exception.code, "5010")
        self.assertIn("changed this in QuickBooks", str(caught.exception))

    def test_an_existing_customer_is_found_by_name_then_by_email(self):
        state = seed_state()
        qbo_sync.save_mapping(state, {"id": "1", "name": "Construction billing"}, {"id": "2", "name": "Retainage"}, "Pat")
        job = next(j for j in state["jobs"] if j["id"] == "job-turner")
        result = self.push(state, job, job["invoices"][0])
        self.assertEqual((result["customer"]["id"], result["customer"]["how"]), ("58", "matched"), "same display name")
        BOOKS.customers["58"]["DisplayName"] = "Northline General Contractors"
        state = seed_state()
        self.map_items(state)
        job = next(j for j in state["jobs"] if j["id"] == "job-turner")
        BOOKS.invoices.clear()
        result = self.push(state, job, job["invoices"][0])
        self.assertEqual((result["customer"]["id"], result["customer"]["how"]), ("58", "matched"), "same email")

    def test_a_csv_imported_invoice_with_the_same_number_is_updated_not_duplicated(self):
        state = seed_state()
        self.map_items(state)
        job = next(j for j in state["jobs"] if j["id"] == "job-turner")
        BOOKS.invoices["77"] = {"Id": "77", "DocNumber": "PA-TRN-01", "CustomerRef": {"value": "58"}, "TotalAmt": 119700.0, "Balance": 119700.0,
                                "SyncToken": "2", "Line": []}
        result = self.push(state, job, job["invoices"][0])
        self.assertEqual((result["action"], result["qbo"]["invoice_id"]), ("updated", "77"))
        self.assertEqual(len(BOOKS.invoices), 1)

    def test_refusals_before_anything_is_sent(self):
        state = new_state()
        job = new_job(state, retainage=10)
        domain.record_progress(state, job["id"], 1_000_000)
        draft = job["invoices"][-1]
        with self.assertRaises(domain.GateError):
            qbo_sync.push(self.cid, state, job["id"], draft["id"])
        inv = send_open(state, job)
        with self.assertRaisesRegex(domain.GateError, "item pay apps are billed on"):
            qbo_sync.push(self.cid, state, job["id"], inv["id"])
        self.map_items(state, retainage=False)
        with self.assertRaisesRegex(domain.GateError, "retainage item"):
            qbo_sync.push(self.cid, state, job["id"], inv["id"])
        self.assertEqual([x for x in BOOKS.log if x[0] == "POST"], [], "nothing was written to QuickBooks")
        with self.assertRaises(domain.GateError):
            qbo_sync.save_mapping(state, {"id": "1"}, {"id": "1"}, "Pat")

    def test_quickbooks_validation_errors_reach_the_person_in_plain_words(self):
        state, job, inv = self.job_with_retainage()
        BOOKS.faults.append(("POST customer", 400, fault("6240", "Duplicate Name Exists Error", "The name supplied already exists."), {}))
        with self.assertRaises(qbo_sync.QboApiError) as caught:
            qbo_sync.push(self.cid, state, job["id"], inv["id"])
        self.assertIn("already uses that name for a client, vendor or employee", str(caught.exception))
        BOOKS.faults.append(("POST invoice", 400, fault("6000", "A business validation error has occurred while processing your request",
                                                         "Business Validation Error: Transaction date is in a closed period."), {}))
        with self.assertRaises(qbo_sync.QboApiError) as caught:
            qbo_sync.push(self.cid, state, job["id"], inv["id"])
        self.assertIn("QuickBooks refused invoice", str(caught.exception))
        self.assertIn("closed period", str(caught.exception))
        del BOOKS.items["2"]   # the retainage item was deleted in QuickBooks
        with self.assertRaises(qbo_sync.QboApiError) as caught:
            qbo_sync.push(self.cid, state, job["id"], inv["id"])
        self.assertIn("Choose the items again", str(caught.exception))


class TransportTests(FakeCase):
    def test_429_and_5xx_are_retried_with_backoff_then_given_up(self):
        BOOKS.faults += [("GET query", 429, {"Fault": {"Error": [{"Message": "ThrottleExceeded", "code": "003001"}]}}, {"Retry-After": "0"}),
                         ("GET query", 503, {}, {})]
        with mock.patch.object(qbo_sync.time, "sleep") as slept:
            self.assertEqual(len(qbo_sync.items(self.cid)), 2, "Service items only; inventory is left out")
        self.assertEqual(slept.call_count, 2)
        BOOKS.faults += [("GET query", 429, {}, {})] * qbo_sync.TRIES
        with mock.patch.object(qbo_sync.time, "sleep"), self.assertRaisesRegex(qbo.QboError, "busy"):
            qbo_sync.items(self.cid)

    def test_a_retried_create_does_not_make_two_invoices(self):
        state, job, inv = self.job_with_retainage()
        real = qbo_sync.request.urlopen
        calls = []

        def drop_first_invoice_answer(req, timeout):
            res = real(req, timeout=timeout)
            if req.get_method() == "POST" and "/invoice?" in req.full_url and not calls:
                calls.append(req.full_url)
                res.close()
                raise TimeoutError("timed out")   # QuickBooks made it, the answer was lost
            return res

        with mock.patch.object(qbo_sync.request, "urlopen", side_effect=drop_first_invoice_answer):
            self.push(state, job, inv)
        self.assertEqual(len(BOOKS.invoices), 1, "the same requestid: QuickBooks answers with the first invoice")

    def test_a_401_refreshes_the_token_once_and_tries_again(self):
        BOOKS.access = "A7"   # QuickBooks retired the token Duracash holds
        self.assertEqual(len(qbo_sync.customers(self.cid)), 1)
        self.assertEqual(qbo.connection(self.cid)["access_token"], "A8")
        self.assertEqual([x[0] for x in BOOKS.log].count("REFRESH"), 1)
        BOOKS.faults += [("GET query", 401, {}, {}), ("GET query", 401, {}, {})]
        with self.assertRaisesRegex(qbo.QboError, "connect it again"):
            qbo_sync.customers(self.cid)

    def test_tokens_are_read_decrypted_and_a_refresh_is_stored_encrypted(self):  # sec2a H3
        raw = json.loads((storage.DATA_DIR / "qbo_connections.json").read_text())[self.cid]
        self.assertTrue(raw["access_token"].startswith("enc1:") and raw["refresh_token"].startswith("enc1:"))
        BOOKS.access = "A7"
        self.assertEqual(len(qbo_sync.customers(self.cid)), 1)
        refresh = next(x for x in BOOKS.log if x[0] == "REFRESH")
        self.assertEqual(refresh[3]["refresh_token"], "R1", "Intuit gets the plain refresh token, not the sealed one")
        raw = json.loads((storage.DATA_DIR / "qbo_connections.json").read_text())[self.cid]
        self.assertTrue(raw["access_token"].startswith("enc1:") and raw["refresh_token"].startswith("enc1:"))
        self.assertEqual((qbo.connection(self.cid)["access_token"], qbo.connection(self.cid)["refresh_token"]), ("A8", "R8"))

    def test_environment_setting(self):
        for env, want in (({"QBO_ENVIRONMENT": "production"}, "production"), ({"QBO_ENVIRONMENT": "sandbox", "DURACASH_ENV": "production"}, "sandbox"),
                          ({"DURACASH_ENV": "production"}, "production"), ({}, "sandbox"), ({"QBO_ENV": "production"}, "production")):
            clean = {"QBO_ENVIRONMENT": "", "QBO_ENV": "", "DURACASH_ENV": "", "QBO_API_BASE": "", **env}
            with mock.patch.dict("os.environ", clean):
                s = qbo.settings()
                self.assertEqual(s["env"], want, env)
                self.assertEqual(s["api_base"], qbo.API_BASES[want])

    def test_query_values_are_escaped(self):
        self.assertEqual(qbo_sync.quote("O'Brien's"), "O\\'Brien\\'s")


class PaymentSyncTests(FakeCase):
    def test_a_payment_recorded_in_quickbooks_marks_the_pay_app_paid_once(self):
        state, job, inv = self.job_with_retainage()
        self.push(state, job, inv)
        pid = pay_in_qbo(inv["qbo"]["invoice_id"], inv["amount_cents"] / 100, ref="10233", txn_date=inv["sent_at"][:10])
        BOOKS.payments["9"] = {"Id": "9", "TxnDate": inv["sent_at"][:10], "TotalAmt": 50.0, "Line": []}   # not for a pay app sent from here
        report = qbo_sync.apply_payments(state, qbo_sync.fetch_payments(self.cid, state), "Pat")
        self.assertEqual([(r["number"], r["amount_cents"], r["reference"]) for r in report["recorded"]], [(inv["number"], inv["amount_cents"], "10233")])
        self.assertEqual(inv["status"], "paid")
        self.assertEqual(job["payments"][-1]["qbo_payment_id"], pid)
        self.assertEqual(job["payments"][-1]["matched_at"][:10], inv["sent_at"][:10], "dated as QuickBooks has it")
        kinds = [a["action"] for a in state["audit"][-2:]]
        self.assertEqual(kinds, ["match_payment", "qbo_synced"], "the Record payment path and its audit trail")
        again = qbo_sync.apply_payments(state, qbo_sync.fetch_payments(self.cid, state), "Pat")
        self.assertEqual((again["recorded"], again["problems"]), ([], []), "nothing recorded twice; nothing open is not asked for")

    def test_partial_and_split_payments(self):
        state = seed_state()
        self.map_items(state)
        turner = next(j for j in state["jobs"] if j["id"] == "job-turner")
        brook = next(j for j in state["jobs"] if j["id"] == "job-brook")
        for j in (turner, brook):
            self.push(state, j, j["invoices"][0])
        t_inv, b_inv = turner["invoices"][0], brook["invoices"][0]
        pay_in_qbo(t_inv["qbo"]["invoice_id"], 10_000.0, ref="ACH", txn_date="2026-10-20")
        report = qbo_sync.apply_payments(state, qbo_sync.fetch_payments(self.cid, state))
        self.assertEqual(len(report["recorded"]), 1)
        self.assertEqual(t_inv["status"], "partial")
        # One QuickBooks payment over both pay apps: two parts, each with its pay app's number.
        rest = (t_inv["amount_cents"] - 1_000_000) / 100
        pay_in_qbo(t_inv["qbo"]["invoice_id"], rest, ref="4471", txn_date="2026-10-21", more=[(b_inv["qbo"]["invoice_id"], b_inv["amount_cents"] / 100)])
        report = qbo_sync.apply_payments(state, qbo_sync.fetch_payments(self.cid, state))
        self.assertEqual(sorted(r["reference"] for r in report["recorded"]), ["4471 PA-BRK-01", "4471 PA-TRN-01"])
        self.assertEqual((t_inv["status"], b_inv["status"]), ("paid", "paid"))

    def test_too_much_applied_in_quickbooks_is_reported_not_recorded(self):
        state, job, inv = self.job_with_retainage()
        self.push(state, job, inv)
        pay_in_qbo(inv["qbo"]["invoice_id"], inv["amount_cents"] / 100 + 500, txn_date=inv["sent_at"][:10])
        report = qbo_sync.apply_payments(state, qbo_sync.fetch_payments(self.cid, state))
        self.assertEqual(report["recorded"], [])
        self.assertIn("no more than", report["problems"][0]["error"])
        self.assertEqual(inv["status"], "sent")

    def test_daily_sync_from_the_hourly_loop(self):
        state, job, inv = self.job_with_retainage()
        self.push(state, job, inv)
        server.save_company(self.cid, state)
        pay_in_qbo(inv["qbo"]["invoice_id"], inv["amount_cents"] / 100, txn_date=inv["sent_at"][:10])
        site = server.SITE
        with mock.patch.object(server, "company_ids", return_value=[self.cid]):
            now = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
            lines = qbo_sync.run_daily(site, now)
            self.assertEqual(lines, [f"{self.cid}  QuickBooks sync: 1 recorded, 0 not"])
            self.assertEqual(server.load_company(self.cid)["jobs"][0]["invoices"][-1]["status"], "paid")
            asked = len(BOOKS.log)
            self.assertEqual(qbo_sync.run_daily(site, now), [], "once a day")
            self.assertEqual(len(BOOKS.log), asked)
            with mock.patch.object(qbo_sync, "run_daily", return_value=["x"]) as daily:
                monthly.run_once(mock.Mock(log=lambda *a, **k: None, company_ids=lambda: [], deleted=set, app_url=lambda: ""), now)
            daily.assert_called_once()


class QboSyncHttpTests(FakeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        super().tearDownClass()

    def setUp(self):
        global BOOKS
        BOOKS = Books()

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
        server.save_company(cid, seed_state())
        qbo._store(cid, {"access_token": "A1", "refresh_token": "R1", "realm_id": REALM, "access_expires_at": "2999-01-01T00:00:00+00:00",
                         "refresh_expires_at": "2999-01-01T00:00:00+00:00", "connected_at": "2026-11-27T00:00:00+00:00", "connected_by": "Pat"})
        return cookie, cid

    def member(self, owner, addr):
        res, out = self.request("POST", "/api/team/invite", {"email": addr, "profile_id": "member"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        res, joined = self.request("POST", "/api/auth/accept-invite", {"accept_terms": True, "token": out["invite_path"].split("#invite=")[1],
                                                                       "name": "Mo Member", "password": "member password 1"})
        self.assertEqual(res.status, 200, joined)
        return res.getheader("Set-Cookie").split(";")[0]

    def test_settings_link_push_and_sync_through_the_server(self):
        owner, cid = self.company("qbo-push@example.com")
        res, data = self.request("GET", "/api/qbo/items", cookie=owner)
        self.assertEqual(res.status, 200, data)
        self.assertEqual([i["name"] for i in data["items"]], ["Construction billing", "Retainage"])
        push = {"job_id": "job-turner", "invoice_id": "inv-trn-1"}
        res, data = self.request("POST", "/api/qbo/push", push, cookie=owner)
        self.assertEqual(res.status, 409)
        self.assertIn("item pay apps are billed on", data["error"])
        res, data = self.request("POST", "/api/qbo/settings", {"billing_item": {"id": "1", "name": "made up"}, "retainage_item": {"id": "2"}}, cookie=owner)
        self.assertEqual(res.status, 200, data)
        self.assertEqual(data["company"]["qbo"]["billing_item"], {"id": "1", "name": "Construction billing"}, "QuickBooks' own name is kept")
        res, data = self.request("POST", "/api/qbo/settings", {"billing_item": {"id": "404"}}, cookie=owner)
        self.assertEqual(res.status, 409)

        res, data = self.request("GET", "/api/qbo/customers", cookie=owner)
        self.assertEqual((res.status, data["suggest"]), (200, {"payer-north": "58"}))
        res, data = self.request("POST", "/api/qbo/link", {"payer_id": "payer-north", "customer_id": "58"}, cookie=owner)
        self.assertEqual(res.status, 200, data)
        self.assertEqual(next(p for p in data["payers"] if p["id"] == "payer-north")["qbo_customer_id"], "58")

        res, data = self.request("POST", "/api/qbo/push", push, cookie=owner)
        self.assertEqual(res.status, 200, data)
        self.assertEqual((data["qbo_push"]["action"], data["qbo_push"]["customer"]["how"]), ("created", "linked"))
        qid = data["qbo_push"]["invoice_id"]
        res, data = self.request("POST", "/api/qbo/push", push, cookie=owner)
        self.assertEqual((res.status, data["qbo_push"]["action"], data["qbo_push"]["invoice_id"]), (200, "updated", qid))

        mo = self.member(owner, "mo@qbo-push.example")
        for method, path, body in (("POST", "/api/qbo/push", push), ("POST", "/api/qbo/settings", {"billing_item": {"id": "1"}}),
                                   ("GET", "/api/qbo/items", None), ("POST", "/api/qbo/link", {"payer_id": "payer-north"})):
            res, data = self.request(method, path, body, cookie=mo)
            self.assertEqual(res.status, 403, f"a Member can't {method} {path}")

        pay_in_qbo(qid, 119700.0, ref="10233", txn_date="2026-10-10")
        res, data = self.request("POST", "/api/qbo/sync", {}, cookie=mo)   # Members record payments
        self.assertEqual(res.status, 200, data)
        self.assertEqual([r["number"] for r in data["qbo_sync"]["recorded"]], ["PA-TRN-01"])
        turner = next(j for j in server.load_company(cid)["jobs"] if j["id"] == "job-turner")
        self.assertEqual(turner["invoices"][0]["status"], "paid")

        BOOKS.faults.append(("GET query", 400, fault("4000", "Error parsing query", "QueryParserError: Encountered bad value"), {}))
        res, data = self.request("POST", "/api/qbo/sync", {}, cookie=owner)
        self.assertEqual(res.status, 200, "nothing open was sent to QuickBooks: no call")
        res, data = self.request("POST", "/api/qbo/push", {"job_id": "job-brook", "invoice_id": "inv-brk-1"}, cookie=owner)
        self.assertEqual(res.status, 409)
        self.assertIn("QuickBooks says: QueryParserError", data["error"])


if __name__ == "__main__":
    unittest.main()


class PayappCoreTests(FakeCase):
    """Integration (Nov 27): QuickBooks with nov27-payapp-core's earlier pay apps, retainage by line
    and the retainage-only release pay app."""

    def schedule(self):
        import test_payapp_core as core
        state, job, labor, materials = core.schedule_job(retainage=10)
        self.map_items(state)
        return core, state, job, labor, materials

    def test_an_earlier_pay_app_is_never_pushed_and_takes_no_payments_from_quickbooks(self):
        core, state, job, labor, materials = self.schedule()
        domain.import_earlier_payapps(state, job["id"], {"payapps": [{"number": "1", "period_to": core.DAY, "lines": {labor: {"pct": "20"}}, "paid": "0"}]})
        [earlier] = domain.imported_invoices(job)
        self.assertEqual(earlier["status"], "sent")
        with self.assertRaises(domain.GateError) as caught:
            qbo_sync.push(self.cid, state, job["id"], earlier["id"])
        self.assertIn("billed before Duracash", str(caught.exception))
        self.assertEqual([m for m, *_ in BOOKS.log if m == "POST"], [], "nothing written to QuickBooks")
        # Linked somehow anyway (an old copy of the data): payments in QuickBooks don't touch it.
        BOOKS.invoices["77"] = {"Id": "77", "DocNumber": earlier["number"], "TotalAmt": earlier["amount_cents"] / 100,
                                "Balance": earlier["amount_cents"] / 100, "SyncToken": "0", "CustomerRef": {"value": "58"}}
        earlier["qbo"] = {"invoice_id": "77", "sync_token": "0", "doc_number": earlier["number"], "total_cents": earlier["amount_cents"]}
        self.assertNotIn("77", qbo_sync.pushed(state))
        pay_in_qbo("77", earlier["amount_cents"] / 100, ref="OLD-1")
        report = qbo_sync.apply_payments(state, qbo_sync.fetch_payments(self.cid, state), "Pat")
        self.assertEqual(report["recorded"], [])
        self.assertEqual((earlier["status"], [p for p in job["payments"] if p.get("qbo_payment_id")]), ("sent", []))

    def test_retainage_by_line_is_still_one_negative_retainage_line(self):
        core, state, job, labor, materials = self.schedule()
        domain.set_line_retainage(state, job["id"], {materials: "5"})
        domain.record_sov_progress(state, job["id"], {labor: "0.5", materials: "40"})
        inv = core.send(state, job)
        self.assertEqual(inv["lines"][-1]["label"], "Less retainage (10% and 5% by line)")
        self.push(state, job, inv)
        sent = BOOKS.invoices[inv["qbo"]["invoice_id"]]
        held = [l for l in sent["Line"] if l["SalesItemLineDetail"]["ItemRef"]["value"] == "2"]
        self.assertEqual([round(l["Amount"] * 100) for l in held], [-inv["retainage_cents"]])
        self.assertEqual(round(sent["TotalAmt"] * 100), inv["amount_cents"])

    def test_a_retainage_release_is_a_positive_line_on_the_retainage_item(self):
        core, state, job, labor, materials = self.schedule()
        domain.record_sov_progress(state, job["id"], {labor: "1", materials: "50"})
        first = core.send(state, job)
        domain.match_payment(state, job["id"], first["amount_cents"], "CHK-1")
        domain.draft_retainage_release(state, job["id"], 50_000, "substantial")
        rel = core.send(state, job)
        self.assertTrue(rel.get("retainage_release"))
        self.push(state, job, rel)
        sent = BOOKS.invoices[rel["qbo"]["invoice_id"]]
        self.assertEqual([(l["SalesItemLineDetail"]["ItemRef"]["value"], round(l["Amount"] * 100)) for l in sent["Line"]], [("2", 50_000)])
        # Without a retainage item chosen it says so instead of putting it on the billing item.
        qbo_sync.save_mapping(state, {"id": "1", "name": "Construction billing"}, None, "Pat")
        with self.assertRaises(domain.GateError) as caught:
            qbo_sync.invoice_lines(rel, job, "1", None)
        self.assertIn("bills retainage held", str(caught.exception))
