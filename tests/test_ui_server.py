"""Page addresses, gzip and caching (UI A6, A8), the HTML 404, job edit and delete (P1-2),
waiver types (P1-6) and change-order descriptions."""

import gzip
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import domain
import importer
import server
import storage
from seed import seed_state

PASSWORD = "correct horse battery"


class UiServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS)
        root = Path(cls.tmp.name)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = root / "auth.json", root / "companies", root, root / "uploads"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = cls._orig
        cls.tmp.cleanup()

    def setUp(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers=headers or {})
        res = conn.getresponse()
        body = res.read()
        conn.close()
        return res, body

    def post(self, path, body, cookie):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode()
        conn.request("POST", path, body=data, headers={"Content-Type": "application/json", "Cookie": cookie})
        res = conn.getresponse()
        payload = json.loads(res.read() or b"{}")
        conn.close()
        return res, payload

    def audit(self, cookie):
        res, body = self.get("/api/audit?limit=200", {"Cookie": cookie})
        return list(reversed(json.loads(body)["entries"]))

    def company(self, email):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/auth/signup", body=json.dumps({"accept_terms": True, "email": email, "password": PASSWORD,
                     "name": "Pat", "company_name": "Pine"}).encode(), headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        res.read()
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, state = self.post("/api/jobs", {"name": "Oak kitchen", "address": "1 Oak St", "customer": "Ellison",
                                             "approver": "Mara", "ap_email": "ap@ellison.example", "contract_cents": 1_000_000,
                                             "retainage_pct": 10}, cookie)
        self.assertEqual(res.status, 200, state)
        return cookie, state["jobs"][-1]

    # ----- page addresses, caching, 404 -----

    def test_app_pages_serve_the_app(self):
        for path in ["/", "/today", "/today/calendar", "/jobs", "/jobs/job-abc123", "/money/pay-app/job-abc/inv-1",
                     "/settings/helper-answers", "/inbox"]:
            res, body = self.get(path)
            self.assertEqual(res.status, 200, path)
            self.assertIn(b'<div id="app">', body, path)
            self.assertEqual(res.getheader("Cache-Control"), "no-cache", path)

    def test_unknown_pages_get_an_html_404_and_api_a_json_one(self):
        for path in ["/nope", "/today/../../server.py", "/jobs/a/b/c/d/e", "/static/app.js"]:
            res, body = self.get(path)
            self.assertEqual(res.status, 404, path)
            self.assertTrue(res.getheader("Content-Type").startswith("text/html"), path)
            self.assertIn(b"Go to Duracash", body)
        res, body = self.get("/api/nope")
        self.assertEqual(res.status, 404)
        self.assertEqual(json.loads(body), {"error": "Not found"})

    def test_assets_are_hashed_gzipped_and_cached(self):
        _, page = self.get("/")
        version = page.decode().split('/app.js?v=')[1].split('"')[0]
        self.assertEqual(version, server.asset("app.js")["hash"])
        res, body = self.get(f"/app.js?v={version}", {"Accept-Encoding": "gzip"})
        self.assertEqual(res.getheader("Content-Encoding"), "gzip")
        self.assertIn("immutable", res.getheader("Cache-Control"))
        self.assertEqual(gzip.decompress(body), (server.STATIC / "app.js").read_bytes())
        res, body = self.get("/app.js")
        self.assertIsNone(res.getheader("Content-Encoding"))
        self.assertEqual(res.getheader("Cache-Control"), "no-cache")
        self.assertEqual(body, (server.STATIC / "app.js").read_bytes())
        res, _ = self.get("/styles.css", {"If-None-Match": f'"{server.asset("styles.css")["hash"]}"'})
        self.assertEqual(res.status, 304)

    def test_installable_app_files(self):  # P3: PWA
        res, body = self.get("/manifest.webmanifest")
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "application/manifest+json"))
        manifest = json.loads(body)
        self.assertEqual(manifest["share_target"]["action"], "/share")
        for icon in manifest["icons"]:
            res, png = self.get(icon["src"])
            self.assertEqual((res.status, png[:4]), (200, b"\x89PNG"), icon["src"])
        res, body = self.get("/sw.js", {"Accept-Encoding": "gzip"})
        self.assertEqual(res.getheader("Cache-Control"), "no-cache", "the worker must never be stuck in a cache")
        self.assertIn(b"fetch(event.request)", gzip.decompress(body))
        _, page = self.get("/")
        self.assertIn(b'rel="manifest"', page)

    def share(self, cookie=None):
        boundary = "----duracash"
        parts = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nOak tickets\r\n"
                 f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"ticket.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()
        body = parts + b"\xff\xd8fakejpeg" + f"\r\n--{boundary}--\r\n".encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))}
        if cookie:
            headers["Cookie"] = cookie
        conn.request("POST", "/share", body=body, headers=headers)
        res = conn.getresponse()
        res.read()
        conn.close()
        return res

    def test_share_from_the_phone_lands_in_the_inbox(self):  # P3: share target
        res = self.share()
        self.assertEqual((res.status, res.getheader("Location")), (303, "/inbox"), "signed out: sent to sign in, nothing stored")
        cookie, _ = self.company("share@example.com")
        res = self.share(cookie)
        self.assertEqual(res.status, 303)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/state", headers={"Cookie": cookie})
        state = json.loads(conn.getresponse().read())
        conn.close()
        item = state["inbox"][-1]
        self.assertEqual((item["subject"], item["status"], len(item["file_ids"])), ("Oak tickets", "new", 1))

    # ----- job edit and delete -----

    def test_edit_job_before_and_after_first_pay_app(self):
        cookie, job = self.company("edit@example.com")
        res, state = self.post(f"/api/jobs/{job['id']}/edit", {"name": "Oak St kitchen", "address": "1 Oak Street",
                                                                "contract_cents": 1_200_000, "retainage_pct": 5}, cookie)
        self.assertEqual(res.status, 200, state)
        edited = state["jobs"][-1]
        self.assertEqual((edited["name"], edited["address"], edited["contract_cents"], edited["retainage_pct"]),
                         ("Oak St kitchen", "1 Oak Street", 1_200_000, 5))
        self.assertEqual(edited["code"], job["code"], "the code never changes: inbound mail is filed by it")
        self.assertEqual(self.audit(cookie)[-1]["action"], "edit_job")
        res, body = self.post(f"/api/jobs/{job['id']}/edit", {"name": "  "}, cookie)
        self.assertEqual(res.status, 409)
        self.post(f"/api/jobs/{job['id']}/progress", {"completed_cents": 400_000}, cookie)
        res, body = self.post(f"/api/jobs/{job['id']}/edit", {"contract_cents": 2_000_000}, cookie)
        self.assertEqual(res.status, 409)
        self.assertIn("change order", " ".join(body["blockers"]))
        res, state = self.post(f"/api/jobs/{job['id']}/edit", {"name": "Oak kitchen, phase 1"}, cookie)
        self.assertEqual(res.status, 200)

    def test_delete_job_only_without_pay_apps(self):
        cookie, job = self.company("delete@example.com")
        res, state = self.post(f"/api/jobs/{job['id']}/delete", {}, cookie)
        self.assertEqual(res.status, 200)
        self.assertFalse(any(j["id"] == job["id"] for j in state["jobs"]))
        self.assertEqual(self.audit(cookie)[-1]["action"], "delete_job")
        cookie2, job2 = self.company("keep@example.com")
        self.post(f"/api/jobs/{job2['id']}/progress", {"completed_cents": 400_000}, cookie2)
        res, body = self.post(f"/api/jobs/{job2['id']}/delete", {}, cookie2)
        self.assertEqual(res.status, 409)
        self.assertIn("Finish it instead", body["error"])
        # A deleted draft is nothing billed (R3-09): the job can go.
        res, state = self.post(f"/api/jobs/{job2['id']}/void-draft", {}, cookie2)
        self.assertTrue(next(j for j in state["jobs"] if j["id"] == job2["id"])["can_delete"])
        res, state = self.post(f"/api/jobs/{job2['id']}/delete", {}, cookie2)
        self.assertEqual(res.status, 200, state)
        self.assertFalse(any(j["id"] == job2["id"] for j in state["jobs"]))

    def test_change_order_description_is_kept(self):
        cookie, job = self.company("co@example.com")
        res, state = self.post(f"/api/jobs/{job['id']}/add-co", {"title": "Two outlets", "amount_cents": 45_000,
                                                                 "description": "Island outlets per owner"}, cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(state["jobs"][-1]["change_orders"][-1]["description"], "Island outlets per owner")


class WaiverKindTests(unittest.TestCase):
    def paid_job(self):
        """A seed job with one paid pay app whose waiver is due."""
        state = seed_state()
        job = next(j for j in state["jobs"] if any(i.get("status") == "paid" for i in j["invoices"]) and not j.get("closed"))
        inv = next(i for i in job["invoices"] if i.get("status") == "paid")
        inv["waiver_due"], inv["waiver_sent"] = True, False
        job["legacy_waiver_pending"] = False
        job["waiver_due"], job["waiver_sent"] = True, False
        return state, job, inv

    def test_kind_is_recorded_on_the_pay_app_and_in_the_email(self):
        state, job, inv = self.paid_job()
        domain.send_waiver(state, job["id"], "owner", invoice_id=inv["id"], kind="conditional_final")
        self.assertEqual(inv["waiver_kind"], "conditional_final")
        self.assertEqual(job["waiver_kind"], "Conditional waiver on final payment")
        self.assertTrue(state["email_log"][-1]["subject"].startswith("Conditional waiver on final payment"))
        self.assertIn("conditional waiver on final payment", state["audit"][-1]["detail"])

    def test_default_and_unknown_kinds(self):
        state, job, inv = self.paid_job()
        with self.assertRaises(domain.GateError):
            domain.send_waiver(state, job["id"], "owner", invoice_id=inv["id"], kind="made_up")
        self.assertFalse(inv["waiver_sent"], "a refused kind sends nothing")
        domain.send_waiver(state, job["id"], "owner", invoice_id=inv["id"])
        self.assertEqual(inv["waiver_kind"], domain.suggested_waiver_kind(job))


if __name__ == "__main__":
    unittest.main()
