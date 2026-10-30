"""Customers (Oct 23 review): a near-match name is asked about, never merged (note 5); a customer
no job uses can be deleted by an owner (note 47)."""

import http.client
import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import domain
import server
import storage
from seed import seed_state


def empty_state():
    return {"company": {"name": "Pine Builders"}, "payers": [], "jobs": [], "audit": [], "email_log": [],
            "daily_records": [], "inbox": [], "files": []}


class CustomerKeyTests(unittest.TestCase):
    def test_suffixes_punctuation_spacing_and_case(self):
        key = domain.customer_key
        for name in ["ABC Builders", "ABC Builders Inc", "ABC Builders, Inc.", "ABC Builders, LLC", "ABC Builders L.L.C.",
                     "abc builders co", "ABC Builders Corp.", "ABC Builders Company", "ABC Builders Ltd", "  ABC   Builders  ",
                     "ABC-Builders", "The ABC Builders", "ABC Builders Co., Inc.", "ABC BUILDERS INCORPORATED"]:
            self.assertEqual(key(name), "abc builders", name)

    def test_what_stays_apart(self):
        key = domain.customer_key
        self.assertNotEqual(key("ABC Builders"), key("ABD Builders"))
        self.assertNotEqual(key("ABC Builders"), key("ABC Builder Group"))
        self.assertNotEqual(key("Ironleaf"), key("Ironleaf GC"), "GC is part of a name, not a company suffix")
        self.assertEqual(key("Company"), "company", "a name that is only a suffix keeps it")
        self.assertEqual(key("The Company"), "company")
        self.assertEqual(key(""), "")
        self.assertEqual(key(None), "")
        self.assertEqual(key("Smith & Sons"), "smith and sons")
        self.assertEqual(key("Peña Builders"), "peña builders", "letters beyond a-z are kept")
        self.assertEqual(key("O'Neil Homes"), key("ONeil Homes"))

    def test_near_is_never_the_same_name(self):
        state = empty_state()
        saved = domain.add_payer(state, {"name": "ABC Builders"}, "Pat")
        self.assertIs(domain.payer_near(state, "ABC Builders Inc"), saved)
        self.assertIs(domain.payer_near(state, "abc  builders"), saved)
        self.assertIsNone(domain.payer_near(state, "abc builders"), "the same name, ignoring case, is a match, not a near one")
        self.assertIsNone(domain.payer_near(state, "ABC Builders Inc", other_than=saved["id"]))
        self.assertIsNone(domain.payer_near(state, "Dana Lee"))
        self.assertIsNone(domain.payer_near(state, "  "))


class SameCustomerTests(unittest.TestCase):
    def test_new_job_asks_then_keeps_or_uses(self):
        state = empty_state()
        saved = domain.add_payer(state, {"name": "ABC Builders", "ap_email": "ap@abc.example"}, "Pat")
        with self.assertRaises(domain.GateError) as ctx:
            domain.create_job(state, "Porch", "ABC Builders, LLC", 100_000, address="1 Elm St")
        err = ctx.exception
        self.assertEqual(str(err), "Same as ABC Builders?")
        self.assertEqual(err.fields, {"customer": "Same as ABC Builders? Use the saved client, or keep ABC Builders, LLC as a new one."})
        self.assertEqual(err.extra["same_customer"], {"id": saved["id"], "name": "ABC Builders", "typed": "ABC Builders, LLC"})
        self.assertEqual((len(state["payers"]), len(state["jobs"])), (1, 0), "nothing saved while it asks")

        used = domain.create_job(state, "Porch", "abc builders", 100_000, address="1 Elm St")
        self.assertEqual(used["payer_id"], saved["id"], "the same name, ignoring case, is the saved customer")
        kept = domain.create_job(state, "Deck", "ABC Builders, LLC", 100_000, address="2 Elm St", new_customer=True)
        self.assertNotEqual(kept["payer_id"], saved["id"])
        self.assertEqual([p["name"] for p in state["payers"]], ["ABC Builders", "ABC Builders, LLC"])
        # Now both names are saved, each is an exact match: no more asking.
        again = domain.create_job(state, "Fence", "ABC Builders, LLC", 100_000, address="3 Elm St")
        self.assertEqual(again["payer_id"], kept["payer_id"])

    def test_other_errors_come_first(self):
        state = empty_state()
        domain.add_payer(state, {"name": "ABC Builders"}, "Pat")
        with self.assertRaises(domain.GateError) as ctx:
            domain.create_job(state, "", "ABC Builders Inc", 100_000, address="1 Elm St")
        self.assertIn("name", ctx.exception.fields)
        self.assertNotIn("same_customer", ctx.exception.extra)

    def test_add_customer_asks_then_keeps(self):
        state = empty_state()
        domain.add_payer(state, {"name": "ABC Builders"}, "Pat")
        with self.assertRaises(domain.GateError) as ctx:
            domain.add_payer(state, {"name": "ABC Builders Inc."}, "Pat")
        self.assertEqual(list(ctx.exception.fields), ["name"])
        self.assertEqual(ctx.exception.extra["same_customer"]["name"], "ABC Builders")
        self.assertEqual(len(state["payers"]), 1)
        domain.add_payer(state, {"name": "ABC Builders Inc.", "new_customer": True}, "Pat")
        self.assertEqual(len(state["payers"]), 2)
        with self.assertRaises(domain.GateError):
            domain.add_payer(state, {"name": "ABC Builders Corp", "new_customer": "yes"}, "Pat")  # only true keeps it


class DeleteCustomerTests(unittest.TestCase):
    def test_only_a_customer_no_job_uses(self):
        state = empty_state()
        spare = domain.add_payer(state, {"name": "Spare Customer"}, "Pat")
        job = domain.create_job(state, "Porch", "Dana Lee", 100_000, address="1 Elm St")
        used = next(p for p in state["payers"] if p["id"] == job["payer_id"])
        with self.assertRaises(domain.GateError) as ctx:
            domain.delete_payer(state, used["id"], "Pat")
        self.assertEqual(str(ctx.exception), "Dana Lee can't be deleted: 1 job uses this client. "
                                             "To remove it, merge it into another client.")
        job["closed"] = True  # a finished job still needs its client
        with self.assertRaises(domain.GateError) as ctx:
            domain.delete_payer(state, used["id"], "Pat")
        self.assertEqual(str(ctx.exception), "Dana Lee can't be deleted: 1 job uses this client, counting finished jobs. "
                                             "To remove it, merge it into another client.")
        self.assertIn(used, state["payers"])

        domain.delete_payer(state, spare["id"], "Pat")
        self.assertNotIn(spare, state["payers"])
        self.assertEqual((state["audit"][-1]["action"], state["audit"][-1]["detail"]), ("payer_delete", "Pat deleted the client Spare Customer."))
        with self.assertRaises(domain.GateError):
            domain.delete_payer(state, spare["id"], "Pat")

    def test_no_merge_advice_without_another_customer(self):
        state = empty_state()
        job = domain.create_job(state, "Porch", "Dana Lee", 100_000, address="1 Elm St")
        domain.create_job(state, "Deck", "Dana Lee", 50_000, address="1 Elm St")
        self.assertEqual(len(state["payers"]), 1)
        with self.assertRaises(domain.GateError) as ctx:
            domain.delete_payer(state, job["payer_id"], "Pat")
        self.assertEqual(str(ctx.exception), "Dana Lee can't be deleted: 2 jobs use this client. Its jobs need it.")

    def test_the_page_data_counts_every_job(self):
        state = seed_state()
        snap = domain.light_snapshot(state)
        counts = {p["id"]: p["job_count"] for p in snap["payers"]}
        for payer_id, n in counts.items():
            self.assertEqual(n, sum(1 for j in state["jobs"] if j["payer_id"] == payer_id))
        self.assertNotIn("job_count", state["payers"][0], "counted for the page, never stored")


class CustomerHttpTests(unittest.TestCase):
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

    def test_same_as_and_delete_over_http(self):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "own@example.com", "password": "correct horse battery",
                                                              "name": "Pat Pine", "company_name": "Pine Builders"})
        self.assertEqual(res.status, 200, data)
        owner = res.getheader("Set-Cookie").split(";")[0]
        auth.create_user("mem@example.com", "member password 12", "Mo Member", data["user"]["company_id"], role="member")
        res, data = self.request("POST", "/api/auth/login", {"email": "mem@example.com", "password": "member password 12"})
        member = res.getheader("Set-Cookie").split(";")[0]

        job = {"name": "Porch", "address": "1 Elm St", "contract_cents": 100_000}
        res, state = self.request("POST", "/api/jobs", {**job, "customer": "ABC Builders"}, owner)
        self.assertEqual(res.status, 200, state)
        saved = state["payers"][0]
        self.assertEqual(saved["job_count"], 1)
        res, data = self.request("POST", "/api/jobs", {**job, "name": "Deck", "customer": "ABC Builders Inc"}, owner)
        self.assertEqual(res.status, 409)
        self.assertEqual(data["same_customer"], {"id": saved["id"], "name": "ABC Builders", "typed": "ABC Builders Inc"})
        self.assertIn("customer", data["fields"])
        res, state = self.request("POST", "/api/jobs", {**job, "name": "Deck", "customer": "ABC Builders Inc", "new_customer": True}, owner)
        self.assertEqual(res.status, 200, state)
        self.assertEqual(len(state["payers"]), 2)

        res, data = self.request("POST", "/api/payers", {"name": "ABC Builders Co"}, owner)
        self.assertEqual((res.status, data["same_customer"]["name"]), (409, "ABC Builders"))
        res, state = self.request("POST", "/api/payers", {"name": "Spare Customer"}, owner)
        spare = next(p for p in state["payers"] if p["name"] == "Spare Customer")
        self.assertEqual(spare["job_count"], 0)

        res, data = self.request("POST", f"/api/payers/{spare['id']}/delete", {}, member)
        self.assertEqual((res.status, data["error"]), (403, "Only an owner can do that."))
        res, data = self.request("POST", f"/api/payers/{saved['id']}/delete", {}, owner)
        self.assertEqual(res.status, 409)
        self.assertIn("can't be deleted", data["error"])
        res, state = self.request("POST", f"/api/payers/{spare['id']}/delete", {}, owner)
        self.assertEqual(res.status, 200, state)
        self.assertNotIn(spare["id"], {p["id"] for p in state["payers"]})

    def signup(self, email, company):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": "correct horse battery",
                                                              "name": "Pat " + company, "company_name": company})
        self.assertEqual(res.status, 200, data)
        return res.getheader("Set-Cookie").split(";")[0], data["user"]

    def raw(self, method, path, body=b"", cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request(method, path, body=body, headers={**(headers or {}), **({"Cookie": cookie} if cookie else {})})
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def test_new_job_takes_the_four_address_boxes_start_date_and_assignee(self):  # J1, J8, G1
        owner, user = self.signup("parts@example.com", "Parts Builders")
        job = {"name": "Oak kitchen", "customer": "Ironleaf GC", "client_type": "gc", "contract_cents": 4_850_000,
               "street": "418 Oak Street", "city": "Decatur", "state": "GA", "zip": "300", "started_on": "2026-10-05"}
        res, data = self.request("POST", "/api/jobs", job, owner)
        self.assertEqual(res.status, 409)
        self.assertEqual(set(data["fields"]), {"zip"})
        res, data = self.request("POST", "/api/jobs", {**job, "zip": "30030", "assignee_id": "user-nobody"}, owner)
        self.assertEqual((res.status, set(data["fields"])), (409, {"assignee_id"}))
        res, state = self.request("POST", "/api/jobs", {**job, "zip": "30030"}, owner)
        self.assertEqual(res.status, 200, state)
        made = state["jobs"][-1]
        self.assertEqual((made["address"], made["started_on"]), ("418 Oak Street, Decatur, GA 30030", "2026-10-05"))
        self.assertEqual((made["assignee_id"], made["assignee_name"]), (user["id"], "Pat Parts Builders"), "the one who made it")
        self.assertEqual(state["payers"][0]["client_type"], "gc")
        res, state = self.request("POST", f"/api/jobs/{made['id']}/edit", {"street": "420 Oak Street", "city": "Decatur", "state": "GA",
                                                                          "zip": "30030", "started_on": "2026-10-06"}, owner)
        self.assertEqual(res.status, 200, state)
        self.assertEqual((state["jobs"][-1]["address"], state["jobs"][-1]["started_on"]), ("420 Oak Street, Decatur, GA 30030", "2026-10-06"))

    def test_client_jobs_and_the_job_file(self):  # S10, J9
        owner, _ = self.signup("file@example.com", "File Builders")
        other, _ = self.signup("other@example.com", "Other Builders")
        self.request("POST", "/api/payers", {"name": "Ruiz Family", "ap_email": "ruiz@example.com", "no_paperwork": True}, owner)
        res, state = self.request("POST", "/api/jobs", {"name": "Porch", "address": "1 Elm St", "customer": "Ruiz Family", "contract_cents": 100_000}, owner)
        job, payer = state["jobs"][-1], state["payers"][0]
        res, data = self.raw("POST", f"/api/import/file?job={job['id']}&file=1&kind=contract", b"Signed subcontract", owner,
                             {"Content-Type": "text/plain", "X-Filename": "contract.txt"})
        self.assertEqual(res.status, 200, data)
        res, state = self.request("POST", f"/api/jobs/{job['id']}/done", {}, owner)
        res, state = self.request("POST", f"/api/jobs/{job['id']}/send", {}, owner)
        self.assertEqual(res.status, 200, state)
        res, data = self.request("GET", f"/api/payers/{payer['id']}/jobs", cookie=owner)
        self.assertEqual(res.status, 200, data)
        row = data["jobs"][0]
        self.assertEqual((row["name"], row["closed"], row["contract_total_cents"]), ("Porch", False, 100_000))
        self.assertTrue(row["contract_file_id"], "the contract record's file, for the card's Contract link")
        res, _ = self.request("GET", f"/api/payers/{payer['id']}/jobs", cookie=other)
        self.assertEqual(res.status, 404, "another company's client")

        res, data = self.raw("GET", f"/api/jobs/{job['id']}/archive.zip", cookie=owner)
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "application/zip"))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            self.assertIn("summary.txt", names)
            self.assertIn("activity.txt", names)
            self.assertIn("record/Contract/contract.txt", names)
            self.assertTrue(any(n.startswith("pay-apps/") and n.endswith(".pdf") for n in names), names)
            summary = z.read("summary.txt").decode()
        self.assertIn("Porch (PORC)", summary)
        self.assertIn("Contract total: $1,000.00", summary)
        res, _ = self.raw("GET", f"/api/jobs/{job['id']}/archive.zip")
        self.assertEqual(res.status, 401, "signed in only")
        res, _ = self.raw("GET", f"/api/jobs/{job['id']}/archive.zip", cookie=other)
        self.assertEqual(res.status, 404, "another company can't open it")

    def test_contract_read_fills_without_saving(self):  # G4, S12
        owner, _ = self.signup("read@example.com", "Hartline Tile")
        data = (Path(__file__).resolve().parent / "fixtures" / "contracts" / "subcontract.docx").read_bytes()
        res, body = self.raw("POST", "/api/contract/read", data, owner, {"Content-Type": "application/octet-stream", "X-Filename": "subcontract.docx"})
        self.assertEqual(res.status, 200)
        found = json.loads(body)
        self.assertEqual(found["fields"]["contract_cents"], 4_850_000)
        self.assertEqual(found["fields"]["customer"], "Ironleaf General Contractors, LLC")
        res, state = self.request("GET", "/api/state", cookie=owner)
        self.assertEqual((state["jobs"], state["payers"]), ([], []), "nothing saved")
        scan = (Path(__file__).resolve().parent / "fixtures" / "contracts" / "scanned.pdf").read_bytes()
        res, body = self.raw("POST", "/api/contract/read", scan, owner, {"X-Filename": "scan.pdf"})
        self.assertEqual(json.loads(body)["error"], "Couldn't read text from this file; fill the fields in by hand.")
        res, _ = self.raw("POST", "/api/contract/read", data, None, {"X-Filename": "subcontract.docx"})
        self.assertEqual(res.status, 401)

    def test_add_client_with_its_type_paperwork_and_days(self):  # J2, S11, S13
        owner, _ = self.signup("add@example.com", "Add Builders")
        res, state = self.request("POST", "/api/payers", {"name": "Fulton County", "client_type": "public", "phone": "404-555-0100",
                                                          "packet": "Signed contract, Insurance certificate, Punch list sign-off (closeout)",
                                                          "payapp_due_day": 25, "payment_terms_days": "30"}, owner)
        self.assertEqual(res.status, 200, state)
        p = state["payers"][0]
        self.assertEqual((p["client_type"], p["role"], p["phone"], p["payapp_due_day"], p["payment_terms_days"]),
                         ("public", "Owner", "(404) 555-0100", 25, 30))
        self.assertEqual(p["packet_items"], ["Signed contract", "Insurance certificate", "Punch list sign-off (closeout)"])
        res, state = self.request("POST", "/api/payers", {"name": "Nothing Co", "no_paperwork": True}, owner)
        q = next(x for x in state["payers"] if x["name"] == "Nothing Co")
        self.assertEqual((q["packet"], q["no_paperwork"], q["packet_items"]), ("", True, []))
        res, data = self.request("POST", "/api/payers", {"name": "Bad Days", "payapp_due_day": 40}, owner)
        self.assertEqual((res.status, set(data["fields"])), (409, {"payapp_due_day"}))


if __name__ == "__main__":
    unittest.main()
