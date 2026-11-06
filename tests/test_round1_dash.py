"""Round 1 flow fixes (Nov 5 audits, round1.md items 19 and 20): reminders in the app on for new
people, under My account as Notifications; and the helper saying it doesn't know rather than
answering a different question."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import importer
import reminders
import server
import storage


class NewPeopleGetReminders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = auth.AUTH_PATH
        auth.AUTH_PATH = Path(self.tmp.name) / "auth.json"

    def tearDown(self):
        auth.AUTH_PATH = self._orig
        self.tmp.cleanup()

    def test_the_default_is_in_the_app_for_every_kind(self):
        self.assertEqual(set(auth.NEW_USER_REMINDERS), set(reminders.KINDS))
        self.assertTrue(all(v == ["app"] for v in auth.NEW_USER_REMINDERS.values()))

    def test_someone_who_joins_by_invite_gets_them_in_the_app(self):
        owner = auth.create_user("own@example.com", "correct horse battery", "Own", "co-x")
        raw, _ = auth.create_invite("co-x", "mem@example.com", "member", "Own", owner["id"])
        member = auth.accept_invite(raw, "Mem", "correct horse battery")
        self.assertEqual(member["reminders"], auth.NEW_USER_REMINDERS)

    def test_an_existing_persons_choices_are_kept(self):
        # Accounts from before: nothing ticked stays nothing ticked; what was ticked stays.
        old = auth.create_user("old@example.com", "correct horse battery", "Old", "co-x")
        self.assertEqual(old["reminders"], {})
        kept = auth.set_reminders(old["id"], {"dates": ["email"]})
        self.assertEqual(kept["reminders"], {"dates": ["email"]})

    def test_the_email_and_text_point_to_my_account(self):
        item = {"job": "Oak", "text": "Due soon.", "path": "/jobs/j1"}
        _, body = reminders.email_message([item], "Pine", "")
        self.assertIn("My account", body)
        self.assertNotIn("Automations", body)
        many = [dict(item, text="x" * 120) for _ in range(5)]
        self.assertIn("My account", reminders.text_message(many))
        self.assertNotIn("Automations", reminders.text_message(many))


class SignUp(unittest.TestCase):
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

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        h, data = {}, None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        if cookie:
            h["Cookie"] = cookie
        conn.request(method, path, body=data, headers=h)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res, json.loads(raw or b"{}")

    def test_a_new_account_has_reminders_in_the_app_and_they_show(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "r1@example.com", "password": "correct horse battery", "name": "Pat", "company_name": "Pine"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, data = self.request("GET", "/api/reminders", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(data["prefs"], {"dates": ["app"], "missing": ["app"], "co_records": ["app"]})
        _, saved = self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "4045550177",
                                                      "ap_email": "ap@northline.example", "contract_cents": 2000000}, cookie=cookie)
        made = saved["jobs"][-1]
        self.request("POST", f"/api/jobs/{made['id']}/sov-progress", {"done": {made["sov"]["lines"][0]["id"]: "0.5"}}, cookie=cookie)
        _, data = self.request("GET", "/api/reminders", cookie=cookie)
        self.assertTrue(any("pay app needs" in n["text"] for n in data["notifications"]), data["notifications"])

    def test_the_old_automations_address_still_opens_the_app(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request("GET", "/automations")
        res = conn.getresponse()
        self.assertEqual((res.status, res.getheader("Content-Type").split(";")[0]), (200, "text/html"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
