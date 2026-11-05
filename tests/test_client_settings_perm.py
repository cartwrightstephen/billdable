"""Who may change a client's settings (the Team page's "Edit company details, client settings and
connections"): by profile, through the server, and what stays open to everyone on the team."""

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
import helper
import importer
import server
import storage

PASSWORD = "correct horse battery"
GC = {"address": "9 Elm Street", "contract_cents": 2_000_000, "retainage_pct": 0, "role": "GC", "client_type": "gc",
      "approver": "Kim", "ap_email": "ap@park.example", "phone": "(404) 555-0100"}


class ClientSettingsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH)
        auth.AUTH_PATH = root / "auth.json"
        server.COMPANIES = root / "companies"
        importer.UPLOADS = root / "uploads"
        storage.DATA_DIR = root
        helper.LOCAL_KB = root / "helper_answers.json"
        helper.LOG_PATH = root / "helper_log.json"
        server.ensure_demo()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        (auth.AUTH_PATH, server.COMPANIES, importer.UPLOADS, storage.DATA_DIR, helper.LOCAL_KB, helper.LOG_PATH) = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = {}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            hdrs["Content-Type"] = "application/json"
            hdrs["Content-Length"] = str(len(data))
        if cookie:
            hdrs["Cookie"] = cookie
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        return res, json.loads(payload or b"{}")

    def post(self, path, body=None, cookie=None):
        res, data = self.request("POST", path, body if body is not None else {}, cookie=cookie or self.owner)
        return res.status, data

    def signin(self, email):
        res, _ = self.request("POST", "/api/auth/login", {"email": email, "password": PASSWORD})
        return res.getheader("Set-Cookie").split(";")[0]

    def setUp(self):
        type(self).n += 1
        n = self.n
        email = f"ann{n}@example.com"
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": PASSWORD, "name": "Ann Owner", "company_name": f"Pine {n}"})
        self.assertEqual(res.status, 200, data)
        self.owner = res.getheader("Set-Cookie").split(";")[0]
        store = auth._load()
        user = next(u for u in store["users"] if u["email"] == email)
        user["email_verified"] = True
        auth._save(store)
        self.company = user["company_id"]
        auth.create_user(f"bob{n}@example.com", PASSWORD, "Bob Field", self.company, role="member")
        self.member = self.signin(f"bob{n}@example.com")
        status, snap = self.post("/api/jobs", {**GC, "name": "Elm addition", "customer": "Park GC"})
        self.assertEqual(status, 200, snap)
        self.job_id = snap["jobs"][0]["id"]
        self.payer_id = snap["jobs"][0]["payer_id"]

    def payer(self, payer_id=None):
        state = server.load_company(self.company)
        return next(p for p in state["payers"] if p["id"] == (payer_id or self.payer_id))

    def blank(self, *fields, payer_id=None):
        """A client saved before these were needed (Decision 3)."""
        state = server.load_company(self.company)
        payer = next(p for p in state["payers"] if p["id"] == (payer_id or self.payer_id))
        for k in fields:
            payer.pop(k, None)
        server.save_company(self.company, state)

    def spare(self, name="Spare Co"):
        status, snap = self.post("/api/payers", {"name": name, "client_type": "owner", "approver": "Lee", "ap_email": "lee@spare.example", "phone": "(404) 555-0111", "new_customer": True})
        self.assertEqual(status, 200, snap)
        return next(p for p in snap["payers"] if p["name"] == name)["id"]

    def suggestion(self):
        state = server.load_company(self.company)
        sug = domain.suggest_packet_item(state, self.payer_id, "Notarized affidavit", "a rejection", "Elm addition")
        server.save_company(self.company, state)
        return sug["id"]

    def profile_user(self, perms, label):
        """Someone on a profile an owner made, with exactly these permissions."""
        status, data = self.post("/api/team/profiles", {"name": label, "perms": perms})
        self.assertEqual(status, 200, data)
        pid = next(p["id"] for p in data["profiles"] if p["name"] == label)
        addr = f"{label.lower()}{self.n}@example.com"
        made = auth.create_user(addr, PASSWORD, label, self.company, role="member")
        status, data = self.post(f"/api/team/{made['id']}/role", {"profile_id": pid})
        self.assertEqual(status, 200, data)
        return self.signin(addr)


class TheRoutes(unittest.TestCase):
    def test_the_routes_that_change_a_saved_client_need_the_permission(self):
        perms = {r.handler: r.perm for r in server.ROUTES}
        for handler in ("_m_payer_update", "_m_payer_merge", "_m_payer_suggestion", "_m_payer_waiver_needed", "_m_payer_delete"):
            self.assertEqual(perms[handler], "company", handler)
        # A new client, or a blank on a saved one, changes nothing already set.
        self.assertFalse(perms["_m_payer_add"])
        self.assertFalse(perms["_m_payer_missing"])


class WhoChangesAClient(ClientSettingsBase):
    def test_a_member_is_refused_and_nothing_changes(self):
        other = self.spare()
        sug = self.suggestion()
        before = self.payer()
        for path, body in ((f"/api/payers/{self.payer_id}/update", {"no_paperwork": True, "packet": "", "rejects_when": "Always"}),
                           (f"/api/payers/{self.payer_id}/update", {"usual_approval_days": 3}),
                           (f"/api/payers/{self.payer_id}/merge", {"into": other}),
                           (f"/api/payers/{self.payer_id}/suggestion/{sug}/add", {}),
                           (f"/api/payers/{self.payer_id}/suggestion/{sug}/dismiss", {})):
            status, data = self.post(path, body, cookie=self.member)
            self.assertEqual((status, data.get("error")), (403, server.NO_PERMISSION), path)
        self.assertEqual(self.payer(), before)
        self.assertEqual(self.payer(other)["name"], "Spare Co", "not merged")

    def test_an_owner_changes_all_of_it(self):
        other = self.spare()
        sug = self.suggestion()
        status, data = self.post(f"/api/payers/{self.payer_id}/update", {"usual_approval_days": 3})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.payer()["usual_approval_days"], 3)
        status, data = self.post(f"/api/payers/{self.payer_id}/suggestion/{sug}/add")
        self.assertEqual(status, 200, data)
        self.assertIn("Notarized affidavit", self.payer()["packet"])
        status, data = self.post(f"/api/payers/{other}/merge", {"into": self.payer_id})
        self.assertEqual(status, 200, data)
        self.assertEqual([p["id"] for p in data["payers"]], [self.payer_id])

    def test_it_follows_the_profile_not_the_word_owner(self):
        office = self.profile_user(["company"], "Office")
        field_lead = self.profile_user(["send_payapps", "record_payments"], "Lead")
        status, data = self.post(f"/api/payers/{self.payer_id}/update", {"usual_approval_days": 9}, cookie=office)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.payer()["usual_approval_days"], 9)
        status, data = self.post(f"/api/payers/{self.payer_id}/update", {"usual_approval_days": 11}, cookie=field_lead)
        self.assertEqual((status, data.get("error")), (403, server.NO_PERMISSION))
        self.assertEqual(self.payer()["usual_approval_days"], 9)

    def test_member_profile_given_the_permission_may(self):
        status, data = self.post("/api/team/profiles/member", {"name": "Member", "perms": ["record_payments", "company"]})
        self.assertEqual(status, 200, data)
        status, data = self.post(f"/api/payers/{self.payer_id}/update", {"usual_approval_days": 5}, cookie=self.member)
        self.assertEqual(status, 200, data)


class WhatStaysOpen(ClientSettingsBase):
    def test_a_member_at_new_job_fills_what_a_saved_client_is_missing(self):
        self.blank("phone", "ap_email")
        status, data = self.post("/api/jobs", {**GC, "name": "Deck", "customer": "Park GC", "ap_email": "new@park.example", "phone": "4045550177"}, cookie=self.member)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.payer()["ap_email"], "new@park.example")
        self.assertEqual(self.payer()["phone"], "(404) 555-0177")

    def test_a_member_at_new_job_does_not_change_what_is_saved(self):
        status, data = self.post("/api/jobs", {**GC, "name": "Deck", "customer": "Park GC", "ap_email": "other@park.example", "approver": "Someone Else"}, cookie=self.member)
        self.assertEqual(status, 200, data)
        self.assertEqual((self.payer()["ap_email"], self.payer()["approver"]), ("ap@park.example", "Kim"))

    def test_a_member_at_new_job_answers_the_near_match_either_way(self):
        res, data = self.request("POST", "/api/jobs", {**GC, "name": "Deck", "customer": "Park GC Inc"}, cookie=self.member)
        self.assertEqual((res.status, data["same_customer"]["id"]), (409, self.payer_id))
        status, data = self.post("/api/jobs", {**GC, "name": "Deck", "customer": "Park GC"}, cookie=self.member)
        self.assertEqual(status, 200, data)
        status, data = self.post("/api/jobs", {**GC, "name": "Shed", "customer": "Park GC Inc", "new_customer": True}, cookie=self.member)
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data["payers"]), 2)

    def test_a_member_adds_a_new_client_and_a_near_match_is_only_asked(self):
        before = self.payer()
        body = {"name": "Park GC Co", "client_type": "gc", "approver": "Rae", "ap_email": "rae@park.example", "phone": "(404) 555-0122"}
        res, data = self.request("POST", "/api/payers", body, cookie=self.member)
        self.assertEqual((res.status, data["same_customer"]["id"]), (409, self.payer_id))
        self.assertEqual(self.payer(), before)
        status, data = self.post("/api/payers", {**body, "new_customer": True}, cookie=self.member)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.payer(), before)

    def test_a_member_notes_why_a_client_sent_it_back(self):
        status, data = self.post(f"/api/jobs/{self.job_id}/learn-rejection", {"reason": "Unsigned waiver"}, cookie=self.member)
        self.assertEqual(status, 200, data)
        self.assertIn("Unsigned waiver", self.payer()["rejects_when"])


class FillingWhatIsMissing(ClientSettingsBase):
    def fill(self, body, cookie=None):
        return self.post(f"/api/payers/{self.payer_id}/missing", body, cookie=cookie or self.member)

    def test_a_member_saves_the_missing_email_and_the_log_says_so(self):
        self.blank("ap_email")
        status, data = self.fill({"ap_email": "new@park.example"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.payer()["ap_email"], "new@park.example")
        state = server.load_company(self.company)
        self.assertTrue(any(a["action"] == "playbook_update" and "Bob Field added email for pay apps to Park GC" in a["detail"] for a in state["audit"]), state["audit"][-3:])

    def test_what_is_saved_is_not_changed_this_way(self):
        status, data = self.fill({"ap_email": "stranger@evil.example"})
        self.assertEqual(status, 409, data)
        self.assertIn("ap_email", data["fields"])
        self.assertEqual(self.payer()["ap_email"], "ap@park.example")

    def test_one_saved_box_refuses_the_whole_save(self):
        self.blank("phone")
        status, data = self.fill({"phone": "4045550177", "ap_email": "stranger@evil.example"})
        self.assertEqual(status, 409, data)
        self.assertNotIn("phone", self.payer())

    def test_rules_and_other_settings_are_not_taken(self):
        self.blank("ap_email")
        before = self.payer()
        status, data = self.fill({"ap_email": "new@park.example", "packet": "", "no_paperwork": True, "rejects_when": "Always", "name": "Renamed", "usual_approval_days": 2, "waiver_not_needed": {"by": "x"}})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.payer(), {**before, "ap_email": "new@park.example"})

    def test_nothing_typed_is_refused(self):
        self.blank("ap_email")
        status, data = self.fill({})
        self.assertEqual(status, 409, data)

    def test_the_boxes_are_checked(self):
        self.blank("ap_email", "phone", "client_type")
        for body, key in (({"ap_email": "not an email"}, "ap_email"), ({"phone": "12"}, "phone"), ({"client_type": "landlord"}, "client_type")):
            status, data = self.fill(body)
            self.assertEqual(status, 409, data)
            self.assertIn(key, data["fields"])
        status, data = self.fill({"phone": "4045550177", "client_type": "owner"})
        self.assertEqual(status, 200, data)
        self.assertEqual((self.payer()["phone"], self.payer()["client_type"], self.payer()["role"]), ("(404) 555-0177", "owner", "Owner"))
        state = server.load_company(self.company)
        self.assertEqual(next(j for j in state["jobs"] if j["id"] == self.job_id)["payer_role"], "Owner", "the job follows the client's type")

    def test_a_signer_that_only_defaulted_is_answered_once(self):
        state = server.load_company(self.company)
        payer = next(p for p in state["payers"] if p["id"] == self.payer_id)
        payer["approver"] = payer["name"]
        payer.pop("approver_confirmed", None)
        server.save_company(self.company, state)
        status, data = self.fill({"approver": "Park GC"})
        self.assertEqual(status, 409, data)  # the client's own name, unsaid
        status, data = self.fill({"approver": "Park GC", "approver_confirmed": True})
        self.assertEqual(status, 200, data)
        self.assertEqual(domain.playbook_missing(self.payer()), [])

    def test_an_unknown_client_is_not_found(self):
        status, _ = self.post("/api/payers/payer-nope/missing", {"ap_email": "a@b.example"}, cookie=self.member)
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
