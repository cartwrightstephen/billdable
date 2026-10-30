"""The company's own details on its pay apps (Stephen's feature 2), and the payer's monthly
pay-app cutoff on the calendar and Today (feature 15, the part that is a contract date)."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import calendar_feed
import domain
import payapp_pdf
import server
import storage
from seed import seed_state


class CompanyDetailsTests(unittest.TestCase):
    def test_edit_clear_and_refuse(self):
        state = seed_state()
        domain.update_company(state, {"name": "  Hartline  Remodeling LLC ", "address": "12 Peach St, Atlanta, GA 30303",
                                      "phone": "404-555-0100", "license": "RBQA001234"}, "Alex")
        c = state["company"]
        self.assertEqual((c["name"], c["address"], c["license"]), ("Hartline Remodeling LLC", "12 Peach St, Atlanta, GA 30303", "RBQA001234"))
        self.assertEqual(state["audit"][-1]["action"], "company_update")
        rows = len(state["audit"])
        domain.update_company(state, {"phone": "404-555-0100"}, "Alex")
        self.assertEqual(len(state["audit"]), rows, "no change, no audit row")
        domain.update_company(state, {"license": ""}, "Alex")
        self.assertNotIn("license", c)
        for bad in [{"name": "  "}, {"address": "x" * 201}, {"phone": 5}]:
            with self.assertRaises(domain.GateError):
                domain.update_company(state, bad, "Alex")
        self.assertEqual(c["name"], "Hartline Remodeling LLC")

    def test_address_in_four_boxes(self):  # S3
        state = seed_state()
        c = domain.update_company(state, {"street": " 12  Peach St ", "city": "Atlanta", "state": "ga", "zip": "30303-1234"}, "Alex")
        self.assertEqual((c["street"], c["city"], c["state"], c["zip"]), ("12 Peach St", "Atlanta", "GA", "30303-1234"))
        self.assertEqual(c["address"], "12 Peach St, Atlanta, GA 30303-1234", "the PDF and the menu read the composed line")
        with self.assertRaises(domain.GateError) as ctx:
            domain.update_company(state, {"street": "12 Peach St", "city": "", "state": "ZZ", "zip": "3030"}, "Alex")
        self.assertEqual(set(ctx.exception.fields), {"city", "state", "zip"})
        with self.assertRaises(domain.GateError) as ctx:
            domain.update_company(state, {"zip": "abcde"}, "Alex")
        self.assertEqual(set(ctx.exception.fields), {"zip"}, "one box changed: the others are kept")
        domain.update_company(state, {"city": "Decatur", "zip": "30030"}, "Alex")
        self.assertEqual(c["address"], "12 Peach St, Decatur, GA 30030")
        domain.update_company(state, {"street": "", "city": "", "state": "", "zip": ""}, "Alex")
        self.assertFalse(any(k in c for k in ("street", "city", "state", "zip", "address")), "all blank clears the address")
        # A page from before the four boxes sends one line: it is split the same way.
        domain.update_company(state, {"address": "9 Elm Ave, Macon, GA 31201"}, "Alex")
        self.assertEqual((c["street"], c["city"], c["state"], c["zip"]), ("9 Elm Ave", "Macon", "GA", "31201"))

    def test_rules_apply_only_to_what_changes(self):  # G1
        state = seed_state()
        state["company"].update(phone="555-0101", license="LIC #12 (old)")
        domain.update_company(state, {"name": "Hartline", "phone": "555-0101", "license": "LIC #12 (old)"}, "Alex")
        self.assertEqual(state["company"]["phone"], "555-0101", "saved before the rules: kept until edited")
        with self.assertRaises(domain.GateError) as ctx:
            domain.update_company(state, {"phone": "555-0102", "license": "LIC #13"}, "Alex")
        self.assertEqual(set(ctx.exception.fields), {"phone", "license"})

    def test_an_old_one_line_address_is_split_by_the_upgrade(self):
        import migrations
        for line, parts in (("12 Peach St, Atlanta, GA 30303", ("12 Peach St", "Atlanta", "GA", "30303")),
                            ("Unit 4, 88 Mill Rd", ("Unit 4, 88 Mill Rd", None, None, None))):
            state = seed_state()
            state["company"] = {"name": "Old Co", "address": line}
            state["schema_version"] = migrations.SCHEMA_VERSION - 1
            migrations.migrate(state)
            c = state["company"]
            self.assertEqual(tuple(c.get(k) for k in ("street", "city", "state", "zip")), parts)
            self.assertEqual(c["address"], line, "the line itself is kept")

    def test_the_pay_app_pdf_carries_them(self):
        state = seed_state()
        domain.update_company(state, {"address": "12 Peach St, Atlanta, GA 30303", "phone": "404-555-0100", "license": "RBQA001234"}, "Alex")
        self.assertEqual(state["company"]["phone"], "(404) 555-0100")
        job = next(j for j in state["jobs"] if j["code"] == "TRN")
        payer = next(p for p in state["payers"] if p["id"] == job["payer_id"])
        pdf = payapp_pdf.render(state["company"], job, payer, job["invoices"][-1])
        self.assertIn(b"(12 Peach St, Atlanta, GA 30303)", pdf)
        self.assertIn(b"(404) 555-0100", pdf.replace(b"\\(", b"(").replace(b"\\)", b")"))
        self.assertIn(b"Lic. RBQA001234", pdf)
        self.assertIn(b"(Hartline Remodeling)", payapp_pdf.render("Hartline Remodeling", job, payer, job["invoices"][-1]), "a name still works")


class PayAppCutoffTests(unittest.TestCase):
    def test_next_and_previous_cutoff(self):
        due = lambda day, today: domain.payapp_due({"payapp_due_day": day}, today)
        self.assertEqual(due(25, date(2026, 10, 13)), (date(2026, 10, 25), date(2026, 9, 25)))
        self.assertEqual(due(25, date(2026, 10, 25)), (date(2026, 10, 25), date(2026, 9, 25)), "the day itself is still this cycle")
        self.assertEqual(due(25, date(2026, 10, 26)), (date(2026, 11, 25), date(2026, 10, 25)))
        self.assertEqual(due(31, date(2027, 2, 10)), (date(2027, 2, 28), date(2027, 1, 31)), "past the month's end: its last day")
        self.assertEqual(due(5, date(2026, 12, 20)), (date(2027, 1, 5), date(2026, 12, 5)))
        self.assertIsNone(domain.payapp_due({}))

    def test_playbook_field(self):
        state = seed_state()
        payer = state["payers"][0]
        domain.update_payer(state, payer["id"], {"payapp_due_day": 25}, "Alex")
        self.assertEqual(payer["payapp_due_day"], 25)
        with self.assertRaises(domain.GateError):
            domain.update_payer(state, payer["id"], {"payapp_due_day": 40}, "Alex")
        domain.update_payer(state, payer["id"], {"payapp_due_day": ""}, "Alex")
        self.assertNotIn("payapp_due_day", payer, "blank clears it")

    def test_on_the_calendar_feed_and_in_the_page_data(self):
        state = seed_state()
        north = next(p for p in state["payers"] if p["name"] == "Northline GC")
        domain.update_payer(state, north["id"], {"payapp_due_day": 25}, "Alex")
        titles = [e["title"] for e in calendar_feed.events(state)]
        due = [t for t in titles if t.startswith("Pay app due to Northline GC")]
        open_northline = [j for j in state["jobs"] if j["payer_id"] == north["id"] and not j.get("closed")
                          and (j.get("work_status") != "complete" or domain.job_money(j)["unbilled_completed_cents"] > 0)]
        self.assertEqual(len(due), 3 * len(open_northline), "this cycle and the next two, per job that may still bill")
        self.assertIn("not legal deadlines", calendar_feed.render(state))
        snap = domain.light_snapshot(state)
        p = next(p for p in snap["payers"] if p["id"] == north["id"])
        nxt, prev = date.fromisoformat(p["payapp_due_next"]), date.fromisoformat(p["payapp_due_prev"])
        self.assertTrue(prev < datetime.now(timezone.utc).date() <= nxt)


class CompanyHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = root / "auth.json", root / "companies", root
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        headers = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})}
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        res = conn.getresponse()
        data = json.loads(res.read() or b"{}")
        conn.close()
        return res, data

    def sign_in(self, email, password):
        res, data = self.request("POST", "/api/auth/login", {"email": email, "password": password})
        self.assertEqual(res.status, 200, data)
        return res.getheader("Set-Cookie").split(";")[0]

    def test_owners_edit_the_company_and_everyone_their_own_name(self):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "own@example.com", "password": "correct horse battery",
                                                              "name": "Pat Pine", "company_name": "Pine Builders"})
        owner = res.getheader("Set-Cookie").split(";")[0]
        cid = data["user"]["company_id"]
        auth.create_user("mem@example.com", "member password 12", "Mo Member", cid, role="member")
        member = self.sign_in("mem@example.com", "member password 12")

        res, state = self.request("POST", "/api/company", {"name": "Pine Builders Inc", "street": "1 Pine Rd", "city": "Macon", "state": "GA",
                                                           "zip": "31201", "phone": "478-555-0101"}, cookie=owner)
        self.assertEqual(res.status, 200, state)
        self.assertEqual((state["company"]["name"], state["company"]["address"], state["company"]["phone"]),
                         ("Pine Builders Inc", "1 Pine Rd, Macon, GA 31201", "(478) 555-0101"))
        res, data = self.request("POST", "/api/company", {"phone": "555-0101", "zip": "312"}, cookie=owner)
        self.assertEqual(res.status, 409)
        self.assertEqual(set(data["fields"]), {"phone", "zip"}, "each box is named")
        res, _ = self.request("POST", "/api/company", {"name": "Taken over"}, cookie=member)
        self.assertEqual(res.status, 403)

        res, data = self.request("POST", "/api/auth/name", {"name": "  Mo  Builder "}, cookie=member)
        self.assertEqual((res.status, data["user"]["name"]), (200, "Mo Builder"))
        for bad in (" ", "--"):
            res, data = self.request("POST", "/api/auth/name", {"name": bad}, cookie=member)
            self.assertEqual(res.status, 409)
            self.assertIn("name", data["fields"])
        res, me = self.request("GET", "/api/auth/me", cookie=member)
        self.assertEqual(me["user"]["name"], "Mo Builder")


if __name__ == "__main__":
    unittest.main()
