"""DocuSign eSignature (docusign.py, docusign_http.py), against a stand-in for DocuSign's account
server and eSignature REST API put in place of docusign.HTTP: no network. The shapes here are
what DocuSign's developer pages show (docs/setup-integrations.md); the first demo account
connected checks them."""

import base64
import hashlib
import hmac
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import docusign
import domain
import importer
import payapp_pdf
import server
import storage
import waiver_form
from test_accounts import ServerCase
from test_money import new_job, new_state
from test_team import JOB

CLIENT = ("ds-integration-key", "ds-secret")
CONNECT_KEY = "connect-hmac-secret"
AUTH = "https://account-d.docusign.com"
BASE = "https://demo.docusign.net"
ACCOUNT = "a1b2c3d4-0000-1111-2222-333344445555"
ENV = {"DOCUSIGN_CLIENT_ID": CLIENT[0], "DOCUSIGN_CLIENT_SECRET": CLIENT[1], "DOCUSIGN_CONNECT_KEY": CONNECT_KEY,
       "DURACASH_URL": "https://app.duracash.test"}
SIGNED_PDF = b"%PDF-1.4\n% signed in DocuSign\n%%EOF\n"
CERT_PDF = b"%PDF-1.4\n% certificate of completion\n%%EOF\n"


def sign_body(body: bytes, key: str = CONNECT_KEY) -> str:
    return base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()


class FakeDocuSign:
    """docusign.HTTP's stand-in: (method, url, headers, body) -> (status, headers, body)."""

    def __init__(self):
        self.n = 0
        self.access = self.refresh = None
        self.calls = []
        self.envelopes = {}   # id -> {"definition", "status", "signers"}
        self.gets = []        # envelope status reads

    def _issue(self):
        self.n += 1
        self.access, self.refresh = f"A{self.n}", f"R{self.n}"
        return {"access_token": self.access, "token_type": "Bearer", "refresh_token": self.refresh, "expires_in": 28800}

    def reply(self, code, payload):
        return code, {"Content-Type": "application/json"}, json.dumps(payload).encode()

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url))
        u = urlparse(url)
        if url.startswith(AUTH + "/oauth/token"):
            if headers.get("Authorization") != "Basic " + base64.b64encode(f"{CLIENT[0]}:{CLIENT[1]}".encode()).decode():
                return self.reply(401, {"error": "invalid_client"})
            form = parse_qs(body.decode())
            if form.get("grant_type") == ["authorization_code"] and form.get("code") == ["good-code"]:
                return self.reply(200, self._issue())
            if form.get("grant_type") == ["refresh_token"] and form.get("refresh_token") == [self.refresh]:
                return self.reply(200, self._issue())
            return self.reply(400, {"error": "invalid_grant"})
        if url == AUTH + "/oauth/userinfo":
            if headers.get("Authorization") != f"Bearer {self.access}":
                return self.reply(401, {})
            return self.reply(200, {"sub": "u-1", "name": "Pat Pine", "email": "pat@example.com", "accounts": [
                {"account_id": "other", "is_default": False, "account_name": "Old", "base_uri": "https://na2.docusign.net"},
                {"account_id": ACCOUNT, "is_default": True, "account_name": "Pine Mechanical", "base_uri": BASE}]})
        prefix = f"{BASE}/restapi/v2.1/accounts/{ACCOUNT}/envelopes"
        if not url.startswith(prefix):
            return self.reply(404, {"errorCode": "NOT_FOUND"})
        if headers.get("Authorization") != f"Bearer {self.access}":
            return self.reply(401, {"errorCode": "AUTHORIZATION_INVALID_TOKEN"})
        rest = u.path[len(urlparse(prefix).path):]
        if method == "POST" and rest == "":
            definition = json.loads(body)
            env_id = f"e{len(self.envelopes) + 1:07d}-aaaa-bbbb-cccc"
            self.envelopes[env_id] = {"definition": definition, "status": "sent"}
            return self.reply(201, {"envelopeId": env_id, "status": "sent"})
        parts = rest.strip("/").split("/")
        env = self.envelopes.get(parts[0])
        if not env:
            return self.reply(404, {"errorCode": "ENVELOPE_DOES_NOT_EXIST"})
        if method == "POST" and parts[1:] == ["views", "recipient"]:
            view = json.loads(body)
            first = env["definition"]["recipients"]["signers"][0]
            if (view["clientUserId"], view["email"], view["userName"]) != (first["clientUserId"], first["email"], first["name"]):
                return self.reply(400, {"errorCode": "UNKNOWN_ENVELOPE_RECIPIENT"})
            env["return_url"] = view["returnUrl"]
            return self.reply(201, {"url": f"{BASE}/Signing/MTRedeem/v1/{parts[0]}"})
        if method == "GET" and len(parts) == 1:
            self.gets.append(parts[0])
            return self.reply(200, self.envelope_json(parts[0]))
        if method == "GET" and parts[1:] == ["documents", "combined"]:
            return 200, {"Content-Type": "application/pdf"}, SIGNED_PDF
        if method == "GET" and parts[1:] == ["documents", "certificate"]:
            return 200, {"Content-Type": "application/pdf"}, CERT_PDF
        return self.reply(404, {})

    def envelope_json(self, env_id):
        env = self.envelopes[env_id]
        signers = []
        for s in env["definition"]["recipients"]["signers"]:
            done = env["status"] == "completed" or (env.get("first_done") and s["recipientId"] == "1")
            signers.append({"recipientId": s["recipientId"], "name": s["name"], "email": s["email"],
                            "status": "completed" if done else "sent", **({"signedDateTime": "2026-11-27T15:04:05Z"} if done else {})})
        return {"envelopeId": env_id, "status": env["status"],
                **({"completedDateTime": "2026-11-27T15:04:09Z"} if env["status"] == "completed" else {}),
                "recipients": {"signers": signers}}

    def complete(self, env_id):
        self.envelopes[env_id]["status"] = "completed"


class Pieces(unittest.TestCase):
    """The parts with no server: HMAC, the envelope's JSON, the anchors in our PDFs."""

    def test_hmac_good_bad_and_missing(self):
        body = b'{"event":"envelope-completed","data":{"envelopeId":"x"}}\r\n'
        good = sign_body(body)
        self.assertTrue(docusign.hmac_ok(body, {"X-DocuSign-Signature-1": good}, [CONNECT_KEY]))
        self.assertTrue(docusign.hmac_ok(body, {"X-DocuSign-Signature-1": "nope", "X-DocuSign-Signature-2": good}, [CONNECT_KEY]),
                        "any one header matching one key is enough")
        self.assertTrue(docusign.hmac_ok(body, {"X-DocuSign-Signature-1": good}, ["old-key", CONNECT_KEY]), "keys being replaced")
        self.assertFalse(docusign.hmac_ok(body, {"X-DocuSign-Signature-1": sign_body(body, "wrong")}, [CONNECT_KEY]))
        self.assertFalse(docusign.hmac_ok(body + b" ", {"X-DocuSign-Signature-1": good}, [CONNECT_KEY]), "the raw body, byte for byte")
        self.assertFalse(docusign.hmac_ok(body, {}, [CONNECT_KEY]), "no header")
        self.assertFalse(docusign.hmac_ok(body, {"X-DocuSign-Signature-1": good}, []), "no key on this server: nothing is trusted")
        self.assertFalse(docusign.hmac_ok(body, {"X-DocuSign-Signature-1": hmac.new(CONNECT_KEY.encode(), body, hashlib.sha256).hexdigest()},
                                          [CONNECT_KEY]), "base64, not hex")

    def test_envelope_definition(self):
        d = docusign.envelope_definition(b"%PDF-1.4 x", "PA-1.pdf", "Please sign PA-1", {"name": "Pat Pine", "email": "pat@example.com", "client_user_id": "u-1"},
                                         {"name": "Kim Park", "email": "ap@park.example"}, "https://app.duracash.test/api/docusign/webhook")
        self.assertEqual(d["status"], "sent")
        self.assertEqual(base64.b64decode(d["documents"][0]["documentBase64"]), b"%PDF-1.4 x")
        self.assertEqual((d["documents"][0]["fileExtension"], d["documents"][0]["documentId"]), ("pdf", "1"))
        first, second = d["recipients"]["signers"]
        self.assertEqual((first["clientUserId"], first["routingOrder"], first["recipientId"]), ("u-1", "1", "1"))
        self.assertNotIn("clientUserId", second, "the client is emailed by DocuSign, not embedded")
        self.assertEqual(second["routingOrder"], "2")
        self.assertEqual(first["tabs"]["signHereTabs"][0]["anchorString"], "/ds_sign1/")
        self.assertEqual(first["tabs"]["dateSignedTabs"][0]["anchorString"], "/ds_date1/")
        self.assertEqual(second["tabs"]["signHereTabs"][0]["anchorString"], "/ds_sign2/")
        self.assertEqual(first["tabs"]["signHereTabs"][0]["anchorIgnoreIfNotPresent"], "false")
        hook = d["eventNotification"]
        self.assertEqual((hook["includeHMAC"], hook["integratorManaged"], hook["deliveryMode"]), ("true", "true", "SIM"))
        self.assertIn("envelope-completed", hook["events"])
        self.assertEqual(hook["eventData"]["version"], "restv2.1")
        bare = docusign.envelope_definition(b"%PDF", "w.pdf", "s", {"name": "P", "email": "p@e.x", "client_user_id": "u"}, dates=(False, False))
        self.assertNotIn("eventNotification", bare)
        self.assertNotIn("dateSignedTabs", bare["recipients"]["signers"][0]["tabs"])

    def test_the_pay_app_pdf_has_its_anchors_only_for_docusign(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 300_000, "a")
        inv = job["invoices"][-1]
        plain, _ = payapp_pdf.for_invoice(state, job["id"], inv["id"])
        signing, _ = payapp_pdf.for_invoice(state, job["id"], inv["id"], anchors=True)
        self.assertNotIn(b"/ds_sign1/", plain)
        self.assertIn(b"1 1 1 rg BT /F1 10 Tf", signing, "white, so the page prints the same")
        self.assertIn(b"(/ds_sign1/)", signing)
        self.assertIn(b"(/ds_date1/)", signing)

    def test_the_change_order_pdf(self):
        snap = {"change_order": 2, "contractor": "Pine", "job": "Elm", "title": "Beam reroute", "description": "Move the duct.",
                "items": [{"label": "Labor", "amount_cents": 216000}], "amount_cents": 216000, "schedule_days": 2}
        one = docusign.co_pdf(snap, client_signs=False)
        two = docusign.co_pdf(snap, client_signs=True)
        self.assertTrue(one.startswith(b"%PDF"))
        self.assertIn(b"(/ds_sign1/)", one)
        self.assertNotIn(b"/ds_sign2/", one)
        self.assertIn(b"(/ds_sign2/)", two)
        self.assertIn(b"Beam reroute", two)

    def test_the_waiver_form_has_its_anchor_and_todays_date(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        job["address_parts"] = {"street": "12 Pine St", "city": "Atlanta", "state": "GA", "zip": "30303"}
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": "GA"}):
            plain, _, refused = waiver_form.for_waiver(state, job["id"])
            signing, _, _ = waiver_form.for_waiver(state, job["id"], for_docusign=True)
        self.assertEqual(refused, "")
        self.assertNotIn(b"/ds_sign1/", plain)
        self.assertIn(b"(/ds_sign1/)", signing)
        from datetime import date
        self.assertIn(b"(%d)" % date.today().year, signing.replace(b"\x00", b""), "the year blank is filled in")

    def test_connect_messages_are_read(self):
        msg = json.dumps({"event": "envelope-completed", "data": {"accountId": ACCOUNT, "envelopeId": "env-1", "envelopeSummary": {
            "status": "completed", "completedDateTime": "2026-11-27T15:04:09Z",
            "recipients": {"signers": [{"recipientId": "1", "name": "Pat", "email": "p@e.x", "status": "completed"}]}}}}).encode()
        env_id, account, info = docusign.parse_connect(msg)
        self.assertEqual((env_id, account, info["status"], info["signers"][0]["status"]), ("env-1", ACCOUNT, "completed", "completed"))
        _, _, bare = docusign.parse_connect(json.dumps({"event": "envelope-voided", "data": {"envelopeId": "e"}}).encode())
        self.assertEqual(bare["status"], "voided")


class DocusignHttp(ServerCase):
    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.fake = FakeDocuSign()
        self.http = mock.patch.object(docusign, "HTTP", self.fake)
        self.http.start()
        tag = self._testMethodName.replace("_", "")[-20:]
        self.cookie, data = self.signup(f"ds-{tag}@sign.example", "Pine Mechanical")
        self.user = data["user"]
        self.cid = self.user["company_id"]

    def tearDown(self):
        self.http.stop()
        self.env.stop()
        super().tearDown()

    def connect(self, cookie=None):
        res, data = self.request("POST", "/api/docusign/connect", {}, cookie=cookie or self.cookie)
        self.assertEqual(res.status, 200, data)
        url = urlparse(data["url"])
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "account-d.docusign.com", "/oauth/auth"))
        q = parse_qs(url.query)
        self.assertEqual(q["scope"], ["signature extended"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["client_id"], [CLIENT[0]])
        self.assertEqual(q["redirect_uri"], ["https://app.duracash.test/api/docusign/callback"])
        return q["state"][0]

    def connected(self):
        state = self.connect()
        res, _ = self.request("GET", f"/api/docusign/callback?code=good-code&state={state}", cookie=self.cookie)
        self.assertEqual((res.status, res.getheader("Location")), (303, "/settings/company"))

    def stored(self):
        with server.company_lock(self.cid):
            return server.load_company(self.cid)

    def ready_payapp(self):
        _, state = self.request("POST", "/api/jobs", JOB, cookie=self.cookie)
        job_id = state["jobs"][0]["id"]
        line = state["jobs"][0]["sov"]["lines"][0]
        _, out = self.request("POST", f"/api/jobs/{job_id}/sov-progress", {"done": {line["id"]: "0.3"}}, cookie=self.cookie)
        for item in out["jobs"][0]["invoices"][-1]["packet"]:
            self.request("POST", f"/api/jobs/{job_id}/resolve/{item['id']}", {"reason": "In hand"}, cookie=self.cookie)
        _, data = self.request("GET", f"/api/jobs/{job_id}", cookie=self.cookie)
        inv = next(i for i in data["job"]["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        return job_id, inv

    def webhook(self, env_id, status="completed", key=CONNECT_KEY, signers=None):
        body = json.dumps({"event": f"envelope-{status}", "apiVersion": "v2.1", "data": {"accountId": ACCOUNT, "envelopeId": env_id,
                           "envelopeSummary": {**self.fake.envelope_json(env_id), **({"recipients": {"signers": signers}} if signers is not None else {})}}}).encode()
        return self.request("POST", "/api/docusign/webhook", raw=body, headers={"X-DocuSign-Signature-1": sign_body(body, key)})

    # ----- connecting -----

    def test_not_set_up_or_not_connected_leaves_the_typed_signature(self):
        with mock.patch.dict(os.environ, {"DOCUSIGN_CLIENT_ID": "", "DOCUSIGN_CLIENT_SECRET": ""}):
            res, st = self.request("GET", "/api/docusign/status", cookie=self.cookie)
            self.assertEqual((st["configured"], st["connected"]), (False, False))
            res, data = self.request("POST", "/api/docusign/connect", {}, cookie=self.cookie)
            self.assertEqual(res.status, 409)
        res, st = self.request("GET", "/api/docusign/status", cookie=self.cookie)
        self.assertEqual((st["configured"], st["connected"]), (True, False))
        job_id, inv = self.ready_payapp()
        res, data = self.request("POST", "/api/docusign/sign", {"kind": "payapp", "job_id": job_id, "invoice_id": inv["id"],
                                                                "stamp": inv["approval"]["stamp"]}, cookie=self.cookie)
        self.assertEqual(res.status, 409, data)
        self.assertIn("typed name", data["error"])
        res, out = self.request("POST", f"/api/jobs/{job_id}/payapp-approve",
                                {"invoice_id": inv["id"], "stamp": inv["approval"]["stamp"], "signature": "Pat Pine"}, cookie=self.cookie)
        self.assertEqual(res.status, 200, "the typed signature works as before")

    def test_connect_with_userinfo_refresh_and_disconnect(self):
        state = self.connect()
        res, page = self.request("GET", f"/api/docusign/callback?code=good-code&state={state}", cookie=self.cookie)
        self.assertEqual(res.status, 303)
        res, _ = self.request("GET", f"/api/docusign/callback?code=good-code&state={state}", cookie=self.cookie)
        self.assertEqual(res.status, 400, "a state works once")
        conn = docusign.connection(self.cid)
        self.assertEqual((conn["account_id"], conn["base_uri"], conn["account_name"]), (ACCOUNT, BASE, "Pine Mechanical"), "the default account")
        self.assertEqual(conn["connected_by"], "Pat Pine")
        res, st = self.request("GET", "/api/docusign/status", cookie=self.cookie)
        self.assertTrue(st["connected"])
        self.assertNotIn(self.fake.refresh, json.dumps(st), "tokens never reach the browser")
        self.assertIn("docusign_connected", [a["action"] for a in self.stored()["audit"]])

        # An access token near its end is refreshed before the next call.
        data = json.loads((storage.DATA_DIR / "docusign_connections.json").read_text())
        data[self.cid]["access_expires_at"] = "2000-01-01T00:00:00+00:00"
        storage.write_json(storage.DATA_DIR / "docusign_connections.json", data)
        token, base, account = docusign._access(self.cid)
        self.assertEqual((token, base, account), (self.fake.access, BASE + "/restapi/v2.1", ACCOUNT))
        self.assertEqual(docusign.connection(self.cid)["refresh_token"], self.fake.refresh)

        res, st = self.request("POST", "/api/docusign/settings", {"co_client_signs": True}, cookie=self.cookie)
        self.assertTrue(st["co_client_signs"])
        res, st = self.request("POST", "/api/docusign/disconnect", {}, cookie=self.cookie)
        self.assertFalse(st["connected"])
        self.assertIsNone(docusign.connection(self.cid))

    def test_the_callback_finishes_only_for_who_pressed_connect(self):
        other, _ = self.signup("ds-other@sign.example", "Other Co")
        state = self.connect()
        res, page = self.request("GET", f"/api/docusign/callback?code=good-code&state={state}", cookie=other)
        self.assertEqual(res.status, 400)
        self.assertIn(b"Sign in", page)
        state = self.connect()
        res, _ = self.request("GET", f"/api/docusign/callback?code=good-code&state={state}")
        self.assertEqual(res.status, 400, "no session")
        self.assertIsNone(docusign.connection(self.cid))
        res, _ = self.request("GET", f"/api/docusign/callback?error=access_denied&state={self.connect()}", cookie=self.cookie)
        self.assertEqual(res.status, 400)

    # ----- the pay app -----

    def test_pay_app_signed_in_docusign_satisfies_the_send_gate(self):
        self.connected()
        job_id, inv = self.ready_payapp()
        res, out = self.request("POST", f"/api/jobs/{job_id}/send", {"reviewed": inv["email_preview"]["stamp"]}, cookie=self.cookie)
        self.assertEqual((res.status, out.get("approvals_missing")), (409, True), "unsigned, it doesn't go")
        res, data = self.request("POST", "/api/docusign/sign", {"kind": "payapp", "job_id": job_id, "invoice_id": inv["id"],
                                                                "stamp": "stale"}, cookie=self.cookie)
        self.assertEqual(res.status, 409, "the typed signature's checks: a stale version is refused")
        self.assertFalse(self.fake.envelopes)
        res, data = self.request("POST", "/api/docusign/sign", {"kind": "payapp", "job_id": job_id, "invoice_id": inv["id"],
                                                                "stamp": inv["approval"]["stamp"], "title": "Owner"}, cookie=self.cookie)
        self.assertEqual(res.status, 200, data)
        self.assertTrue(data["url"].startswith(BASE + "/Signing/"))
        env_id = data["envelope_id"]
        sent = self.fake.envelopes[env_id]
        pdf = base64.b64decode(sent["definition"]["documents"][0]["documentBase64"])
        self.assertIn(b"(/ds_sign1/)", pdf)
        signer = sent["definition"]["recipients"]["signers"][0]
        self.assertEqual((signer["clientUserId"], signer["email"]), (self.user["id"], self.user["email"]))
        self.assertEqual(sent["return_url"], f"https://app.duracash.test/api/docusign/return?e={env_id}")
        self.assertIn("eventNotification", sent["definition"])

        # Back before DocuSign has it completed: nothing is signed, and the page opens.
        res, _ = self.request("GET", f"/api/docusign/return?e={env_id}&event=cancel", cookie=self.cookie)
        self.assertEqual(res.getheader("Location"), f"/money/pay-app/{job_id}/{inv['id']}?docusign=cancel")
        self.assertEqual(self.fake.gets, [], "no status read for a cancel")

        # Signed: the return reads the status once and files it.
        self.fake.complete(env_id)
        res, _ = self.request("GET", f"/api/docusign/return?e={env_id}&event=signing_complete", cookie=self.cookie)
        self.assertEqual((res.status, res.getheader("Location")), (303, f"/money/pay-app/{job_id}/{inv['id']}?docusign=signing_complete"))
        self.assertEqual(self.fake.gets, [env_id])
        state = self.stored()
        job = next(j for j in state["jobs"] if j["id"] == job_id)
        draft = next(i for i in job["invoices"] if i["id"] == inv["id"])
        self.assertEqual(domain.approvals_missing(state, job, draft), [], "the sign gate is satisfied")
        approval = draft["approvals"][0]
        self.assertEqual((approval["method"], approval["envelope_id"], approval["signed_name"], approval["title"]),
                         ("docusign", env_id, self.user["name"], "Owner"))
        records = {r["id"]: r for r in job["records"]}
        self.assertEqual(len(approval["record_ids"]), 2)
        files = {f["id"]: f for f in state["files"]}
        stored = [importer.file_path(self.cid, files[records[rid]["file_ids"][0]]).read_bytes() for rid in approval["record_ids"]]
        self.assertEqual(stored, [SIGNED_PDF, CERT_PDF])
        self.assertIn("docusign_completed", [a["action"] for a in state["audit"]])
        self.assertEqual(docusign.envelope(env_id)["status"], "completed")

        # The webhook arriving after changes nothing; the same envelope is never filed twice.
        res, _ = self.webhook(env_id)
        self.assertEqual(res.status, 200)
        self.assertEqual(len(next(j for j in self.stored()["jobs"] if j["id"] == job_id)["records"]), len(records))

        _, data = self.request("GET", f"/api/jobs/{job_id}", cookie=self.cookie)
        inv = next(i for i in data["job"]["invoices"] if i["id"] == inv["id"])
        res, out = self.request("POST", f"/api/jobs/{job_id}/send", {"reviewed": inv["email_preview"]["stamp"]}, cookie=self.cookie)
        self.assertNotEqual(out.get("approvals_missing"), True, out)

    def test_webhook_hmac_and_a_changed_pay_app_isnt_marked_signed(self):
        self.connected()
        job_id, inv = self.ready_payapp()
        _, data = self.request("POST", "/api/docusign/sign", {"kind": "payapp", "job_id": job_id, "invoice_id": inv["id"],
                                                              "stamp": inv["approval"]["stamp"]}, cookie=self.cookie)
        env_id = data["envelope_id"]
        self.fake.complete(env_id)
        res, _ = self.webhook(env_id, key="wrong-key")
        self.assertEqual(res.status, 401, "a bad signature is refused")
        res, _ = self.request("POST", "/api/docusign/webhook", raw=b'{"data":{"envelopeId":"%s"}}' % env_id.encode())
        self.assertEqual(res.status, 401, "no signature")
        self.assertEqual(docusign.envelope(env_id)["status"], "sent")
        # The pay app changes while it is in DocuSign: the signature doesn't count for the new version.
        with server.company_lock(self.cid):
            state = server.load_company(self.cid)
            job = next(j for j in state["jobs"] if j["id"] == job_id)
            draft = next(i for i in job["invoices"] if i["id"] == inv["id"])
            draft["period_to"] = "2026-11-30"
            server.save_company(self.cid, state)
        res, _ = self.webhook(env_id)
        self.assertEqual(res.status, 200)
        state = self.stored()
        job = next(j for j in state["jobs"] if j["id"] == job_id)
        draft = next(i for i in job["invoices"] if i["id"] == inv["id"])
        self.assertTrue(domain.approvals_missing(state, job, draft), "nobody signed this version")
        self.assertEqual(len([r for r in job["records"] if "DocuSign" in r["label"]]), 2, "the signed files are kept all the same")
        self.assertIn("doesn't count", next(a for a in reversed(state["audit"]) if a["action"] == "docusign_completed")["detail"])

    def test_declined_marks_nothing_and_check_keeps_to_the_polling_limit(self):
        self.connected()
        job_id, inv = self.ready_payapp()
        _, data = self.request("POST", "/api/docusign/sign", {"kind": "payapp", "job_id": job_id, "invoice_id": inv["id"],
                                                              "stamp": inv["approval"]["stamp"]}, cookie=self.cookie)
        env_id = data["envelope_id"]
        res, out = self.request("POST", "/api/docusign/check", {"envelope_id": env_id}, cookie=self.cookie)
        self.assertEqual((res.status, out["status"]), (200, "sent"))
        res, out = self.request("POST", "/api/docusign/check", {"envelope_id": env_id}, cookie=self.cookie)
        self.assertEqual(res.status, 409, "once per 15 minutes per envelope")
        self.assertEqual(self.fake.gets, [env_id])
        res, _ = self.request("GET", f"/api/docusign/return?e={env_id}&event=signing_complete", cookie=self.cookie)
        self.assertEqual(self.fake.gets, [env_id], "the return keeps to the limit too")
        self.fake.envelopes[env_id]["status"] = "declined"
        res, _ = self.webhook(env_id, status="declined")
        self.assertEqual(docusign.envelope(env_id)["status"], "declined")
        state = self.stored()
        self.assertIn("docusign_ended", [a["action"] for a in state["audit"]])
        job = next(j for j in state["jobs"] if j["id"] == job_id)
        self.assertTrue(domain.approvals_missing(state, job, next(i for i in job["invoices"] if i["id"] == inv["id"])))
        other, _ = self.signup("ds-check-other@sign.example", "Other Co")
        res, _ = self.request("POST", "/api/docusign/check", {"envelope_id": env_id}, cookie=other)
        self.assertEqual(res.status, 404, "another company's envelope")

    # ----- change orders -----

    def add_co(self):
        _, state = self.request("POST", "/api/jobs", JOB, cookie=self.cookie)
        job_id = state["jobs"][0]["id"]
        _, out = self.request("POST", f"/api/jobs/{job_id}/add-co", {"title": "Beam reroute", "amount_cents": 216000,
                                                                       "description": "Move the duct around the beam.", "reason": "design_issue"}, cookie=self.cookie)
        co = next(j for j in out["jobs"] if j["id"] == job_id)["change_orders"][-1]
        self.assertEqual(co["status"], "pending", co)
        return job_id, co

    def test_change_order_with_the_client_as_second_signer_is_approved(self):
        self.connected()
        self.request("POST", "/api/docusign/settings", {"co_client_signs": True}, cookie=self.cookie)
        job_id, co = self.add_co()
        res, data = self.request("POST", "/api/docusign/sign", {"kind": "co", "job_id": job_id, "co_id": co["id"]}, cookie=self.cookie)
        self.assertEqual(res.status, 200, data)
        env_id = data["envelope_id"]
        first, second = self.fake.envelopes[env_id]["definition"]["recipients"]["signers"]
        self.assertEqual((second["name"], second["email"], second["routingOrder"]), (JOB["approver"], JOB["ap_email"], "2"))
        pdf = base64.b64decode(self.fake.envelopes[env_id]["definition"]["documents"][0]["documentBase64"])
        self.assertIn(b"(/ds_sign2/)", pdf)
        # The company has signed; the client hasn't yet: still pending.
        self.fake.envelopes[env_id]["first_done"] = True
        res, _ = self.request("GET", f"/api/docusign/return?e={env_id}&event=signing_complete", cookie=self.cookie)
        self.assertEqual(res.getheader("Location"), f"/money/change-order/{job_id}/{co['id']}?docusign=signing_complete")
        job = next(j for j in self.stored()["jobs"] if j["id"] == job_id)
        self.assertEqual(next(c for c in job["change_orders"] if c["id"] == co["id"])["status"], "pending")
        # The client signs: DocuSign's webhook files it and the change order is approved.
        self.fake.complete(env_id)
        res, _ = self.webhook(env_id)
        self.assertEqual(res.status, 200)
        job = next(j for j in self.stored()["jobs"] if j["id"] == job_id)
        done = next(c for c in job["change_orders"] if c["id"] == co["id"])
        self.assertEqual(done["status"], "approved")
        self.assertEqual(done["signed_evidence"]["signer_name"], JOB["approver"])
        self.assertEqual(done["signed_evidence"]["signed_on"], "2026-11-27")
        self.assertEqual(done["docusign"]["envelope_id"], env_id)
        self.assertEqual(next(r for r in job["records"] if r["id"] == done["signed_evidence"]["record_id"])["kind"], "change_order")

    def test_change_order_signed_by_the_company_alone_stays_pending(self):
        self.connected()
        job_id, co = self.add_co()
        _, data = self.request("POST", "/api/docusign/sign", {"kind": "co", "job_id": job_id, "co_id": co["id"]}, cookie=self.cookie)
        env_id = data["envelope_id"]
        self.assertEqual(len(self.fake.envelopes[env_id]["definition"]["recipients"]["signers"]), 1)
        self.fake.complete(env_id)
        self.webhook(env_id)
        job = next(j for j in self.stored()["jobs"] if j["id"] == job_id)
        c = next(c for c in job["change_orders"] if c["id"] == co["id"])
        self.assertEqual(c["status"], "pending", "the client's signature is what approves a change order")
        self.assertTrue(c["support_record_ids"])


class WaiverFiling(unittest.TestCase):
    """A signed waiver back from DocuSign goes on the job's records as a lien waiver of its type."""

    def test_filed_as_a_waiver_of_its_type(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = importer.UPLOADS
        importer.UPLOADS = Path(tmp.name)
        self.addCleanup(setattr, importer, "UPLOADS", orig)
        state = new_state()
        job = new_job(state, contract=1_000_000)
        metas = [importer.store_bytes("c1", "w signed.pdf", SIGNED_PDF, "DocuSign"), importer.store_bytes("c1", "w cert.pdf", CERT_PDF, "DocuSign")]
        env = {"id": "env-w", "kind": "waiver", "job_id": job["id"], "label": "Conditional waiver - Pine kitchen", "user_name": "Pat",
               "waiver_kind": "conditional_progress", "for_id": "", "signer": {"name": "Pat"}}
        out = docusign.file_signed(state, env, {"signers": [{"recipient_id": "1", "name": "Pat Pine", "status": "completed"}]}, metas)
        waiver = next(r for r in job["records"] if r["id"] == out["record_ids"][0])
        self.assertEqual(waiver["kind"], "waiver")
        self.assertEqual(domain.record_waiver_kind(waiver), "conditional_progress")
        self.assertEqual(len(state["files"]), 2)


if __name__ == "__main__":
    unittest.main()
