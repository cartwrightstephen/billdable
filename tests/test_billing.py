"""Billing with Stripe (P2-2), against a local stand-in for Stripe's API. The shapes here are
what billing.py sends and expects; the first test-mode checkout is the real check."""

import hashlib
import hmac
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import billing
import server
import storage
from seed import seed_state

WHSEC = "whsec_test_secret"


class FakeStripe(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        FakeStripe.calls.append((self.path, {k: v[0] for k, v in form.items()}, self.headers.get("Authorization")))
        reply = {"/v1/checkout/sessions": {"id": "cs_1", "url": "https://checkout.stripe.com/c/pay/cs_1"},
                 "/v1/billing_portal/sessions": {"id": "bps_1", "url": "https://billing.stripe.com/p/session/bps_1"}}.get(self.path, {"id": "sub_1"})
        body = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def signed(event, secret=WHSEC, at=None):
    payload = json.dumps(event).encode()
    t = int(at or time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, f"t={t},v1={sig}"


def event(kind, obj, created=None, eid=None):
    return {"id": eid or f"evt_{kind}_{created}", "type": kind, "created": created or int(time.time()), "data": {"object": obj}}


class BillingUnitTests(unittest.TestCase):
    def test_form_encoding_nests_like_stripe(self):
        pairs = billing._form({"line_items": [{"price": "price_1", "quantity": 1}], "subscription_data": {"metadata": {"company_id": "co-1"}}})
        self.assertEqual(pairs, [("line_items[0][price]", "price_1"), ("line_items[0][quantity]", "1"),
                                 ("subscription_data[metadata][company_id]", "co-1")])

    def test_signature(self):
        with mock.patch.dict("os.environ", {"STRIPE_WEBHOOK_SECRET": WHSEC}):
            payload, header = signed(event("invoice.paid", {"customer": "cus_1"}))
            self.assertEqual(billing.verify(payload, header)["type"], "invoice.paid")
            for bad in [header.replace("v1=", "v1=0"), "t=abc,v1=x", ""]:
                with self.assertRaises(billing.BillingError):
                    billing.verify(payload, bad)
            old_payload, old_header = signed(event("invoice.paid", {}), at=time.time() - 3600)
            with self.assertRaises(billing.BillingError):
                billing.verify(old_payload, old_header)
            with self.assertRaises(billing.BillingError):
                billing.verify(payload + b" ", header)

    def test_events_apply_once_and_in_order(self):
        state = {"company": {}}
        billing.apply(state, event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_1", "status": "active"}, 200, "e2"))
        self.assertEqual(billing.apply(state, event("customer.subscription.updated", {"id": "sub_1", "status": "past_due"}, 100, "e1")), "",
                         "an older event never overwrites a newer status")
        self.assertEqual(billing.apply(state, event("customer.subscription.updated", {"id": "sub_1", "status": "active"}, 200, "e2")), "", "a replay")
        self.assertEqual(state["company"]["billing"]["status"], "active")
        billing.apply(state, event("customer.subscription.deleted", {"id": "sub_1", "status": "active"}, 300, "e3"))
        self.assertEqual(state["company"]["billing"]["status"], "canceled")

    def test_standing_holds_only_when_enforced_and_never_for_past_due(self):
        now = datetime.now(timezone.utc)
        env = {"STRIPE_SECRET_KEY": "sk_test", "STRIPE_PRICE_ID": "price_1", "BILLING_ENFORCE": "1"}
        with mock.patch.dict("os.environ", env):
            self.assertTrue(billing.standing({"company": {"billing": {"status": "unpaid"}}})["hold"])
            past_due = billing.standing({"company": {"billing": {"status": "past_due"}}})
            self.assertEqual(past_due["hold"], "")
            self.assertTrue(past_due["warning"])
            ended = {"company": {"trial": {"started_at": (now - timedelta(days=200)).isoformat()}}, "jobs": []}
            self.assertTrue(billing.standing(ended)["hold"], "trial over plus 14 days, no subscription")
            recent = {"company": {"trial": {"started_at": (now - timedelta(days=95)).isoformat()}}, "jobs": []}
            self.assertEqual(billing.standing(recent)["hold"], "")
            self.assertIn("Subscribe by", billing.standing(recent)["warning"])
        with mock.patch.dict("os.environ", {**env, "BILLING_ENFORCE": ""}):
            self.assertEqual(billing.standing({"company": {"billing": {"status": "unpaid"}}})["hold"], "", "not enforced")
        self.assertFalse(billing.standing({"company": {"billing": {"status": "unpaid"}}})["configured"])


class BillingHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = root / "auth.json", root / "companies", root
        cls.stripe = ThreadingHTTPServer(("127.0.0.1", 0), FakeStripe)
        threading.Thread(target=cls.stripe.serve_forever, daemon=True).start()
        cls.env = mock.patch.dict("os.environ", {"STRIPE_SECRET_KEY": "sk_test_1", "STRIPE_PRICE_ID": "price_1", "STRIPE_WEBHOOK_SECRET": WHSEC,
                                                 "STRIPE_API_BASE": f"http://127.0.0.1:{cls.stripe.server_address[1]}",
                                                 "BILLING_ENFORCE": "1", "BILLDABLE_URL": "https://billdable.example"})
        cls.env.start()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        for s in (cls.httpd, cls.stripe):
            s.shutdown()
            s.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        FakeStripe.calls.clear()
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()

    def request(self, method, path, body=None, cookie=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        hdrs = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {}), **(headers or {})}
        conn.request(method, path, body=raw if raw is not None else (json.dumps(body).encode() if body is not None else None), headers=hdrs)
        res = conn.getresponse()
        data = json.loads(res.read() or b"{}")
        conn.close()
        return res, data

    def signup(self, email):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": "correct horse battery",
                                                              "name": "Pat Pine", "company_name": "Pine Builders"})
        return res.getheader("Set-Cookie").split(";")[0], data["user"]["company_id"]

    def webhook(self, ev):
        payload, header = signed(ev)
        return self.request("POST", "/api/stripe/webhook", raw=payload, headers={"Stripe-Signature": header})

    def test_subscribe_get_held_when_unpaid_and_fix_it(self):
        cookie, cid = self.signup("bill@example.com")
        res, data = self.request("POST", "/api/billing/checkout", {}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        self.assertTrue(data["url"].startswith("https://checkout.stripe.com/"))
        path, form, authz = FakeStripe.calls[-1]
        self.assertEqual(path, "/v1/checkout/sessions")
        self.assertEqual(authz, "Bearer sk_test_1")
        self.assertEqual((form["mode"], form["line_items[0][price]"], form["client_reference_id"]), ("subscription", "price_1", cid))
        self.assertEqual(form["customer_email"], "bill@example.com")
        trial_end = int(form["subscription_data[trial_end]"])
        self.assertGreater(trial_end - time.time(), 85 * 86400, "Stripe's trial runs to the free trial's last day")

        res, _ = self.webhook(event("checkout.session.completed", {"client_reference_id": cid, "customer": "cus_9", "subscription": "sub_9"}))
        self.assertEqual(res.status, 200)
        self.webhook(event("customer.subscription.updated", {"id": "sub_9", "customer": "cus_9", "status": "unpaid"}))
        res, data = self.request("POST", "/api/onboarding/dismiss", {}, cookie=cookie)
        self.assertEqual(res.status, 403)
        self.assertIn("unpaid", data["error"])
        res, data = self.request("POST", "/api/billing/portal", {}, cookie=cookie)
        self.assertEqual(res.status, 200, "Billing still opens while read-only")
        self.assertEqual(FakeStripe.calls[-1][1]["customer"], "cus_9")
        res, data = self.request("POST", "/api/billing/checkout", {}, cookie=cookie)
        self.assertEqual(res.status, 200, "an unpaid subscription can be replaced")

        self.webhook(event("customer.subscription.updated", {"id": "sub_9", "customer": "cus_9", "status": "active"}, int(time.time()) + 5))
        res, _ = self.request("POST", "/api/onboarding/dismiss", {}, cookie=cookie)
        self.assertEqual(res.status, 200)
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertEqual(me["billing"]["status"], "active")
        res, page = self.request("GET", "/api/audit", cookie=cookie)
        self.assertTrue(any(e["action"] == "billing" and "unpaid" in e["detail"] for e in page["entries"]))

    def test_a_bad_signature_changes_nothing(self):
        cookie, cid = self.signup("bill2@example.com")
        payload, header = signed(event("customer.subscription.updated", {"id": "sub_x", "status": "unpaid", "metadata": {"company_id": cid}}), secret="whsec_wrong")
        res, _ = self.request("POST", "/api/stripe/webhook", raw=payload, headers={"Stripe-Signature": header})
        self.assertEqual(res.status, 400)
        res, _ = self.request("POST", "/api/onboarding/dismiss", {}, cookie=cookie)
        self.assertEqual(res.status, 200)

    def test_the_first_paid_pay_app_ends_the_stripe_trial(self):
        cookie, cid = self.signup("bill3@example.com")
        now = datetime.now(timezone.utc)
        state = seed_state()
        state["company"]["trial"] = {"started_at": (now - timedelta(days=2)).isoformat(timespec="seconds")}
        state["company"]["billing"] = {"status": "trialing", "subscription_id": "sub_7", "customer_id": "cus_7"}
        job = next(j for j in state["jobs"] if j["code"] == "BRK")
        inv = next(i for i in job["invoices"] if i["status"] == "sent")
        inv["sent_at"] = (now - timedelta(days=1)).isoformat(timespec="seconds")
        server.save_company(cid, state)
        res, data = self.request("POST", f"/api/jobs/{job['id']}/match-payment", {"amount_cents": inv["amount_cents"], "reference": "4471",
                                                                                  "invoice_id": inv["id"]}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        for _ in range(50):
            if any(c[0] == "/v1/subscriptions/sub_7" for c in FakeStripe.calls):
                break
            time.sleep(0.05)
        call = next(c for c in FakeStripe.calls if c[0] == "/v1/subscriptions/sub_7")
        ends = datetime.fromtimestamp(int(call[1]["trial_end"]), timezone.utc)
        self.assertEqual((ends.day, ends.hour), (5, 12), "the first charge lands on the billing day (the 5th), not at once")
        self.assertTrue(now < ends <= now + timedelta(days=32))
        self.assertTrue(server.load_company(cid)["company"]["billing"]["trial_end_requested_at"], "sent once")


if __name__ == "__main__":
    unittest.main()
