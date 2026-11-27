"""Nov 27 hunt (access, security, accounts, integrations): what each fix promises."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_check
import auth
import backup
import server
import storage
import store
from test_accounts import ServerCase, PASSWORD

JOB = {"name": "Elm", "customer": "Park GC", "address": "9 Elm Street", "approver": "Kim", "ap_email": "ap@park.example",
       "contract_cents": 100000, "role": "GC", "phone": "(404) 555-0100"}


class AuditAnchorAfterResetAndRestore(ServerCase):
    def check(self, company_id):
        with server.company_lock(company_id):
            state = server.load_company(company_id)
        return dict(audit_check.check_all([(company_id, state)]))[company_id]

    def test_a_reset_starts_a_new_trail_without_an_alarm(self):
        cookie, data = self.signup("reset-anchor@pine.example")
        company_id = data["user"]["company_id"]
        for n in range(3):
            res, _ = self.request("POST", "/api/jobs", {**JOB, "name": f"Elm {n}"}, cookie=cookie)
            self.assertEqual(res.status, 200)
        self.assertTrue(self.check(company_id)["ok"], "anchored at today's trail")
        res, out = self.request("POST", "/api/reset", {"confirm": "Pine & Sons", "password": PASSWORD}, cookie=cookie)
        self.assertEqual(res.status, 200, out)
        result = self.check(company_id)
        self.assertTrue(result["ok"], f"a reset is not a changed trail: {result}")
        # A trail cut short after the reset is still caught.
        self.request("POST", "/api/jobs", JOB, cookie=cookie)
        self.assertTrue(self.check(company_id)["ok"])
        with server.company_lock(company_id):
            state = server.load_company(company_id)
            state["audit"].pop()
            server.save_company(company_id, state)
        self.assertFalse(self.check(company_id)["ok"])

    def test_a_deleted_company_leaves_no_anchor(self):
        cookie, data = self.signup("delete-anchor@pine.example")
        company_id = data["user"]["company_id"]
        self.assertTrue(self.check(company_id)["ok"])
        self.assertIn(company_id, json.loads(audit_check.anchors_path().read_text()))
        server.delete_company_data(company_id)
        self.assertNotIn(company_id, json.loads(audit_check.anchors_path().read_text()))

    def test_a_restored_backup_is_checked_afresh(self):
        cookie, data = self.signup("restore-anchor@pine.example")
        company_id = data["user"]["company_id"]
        made = backup.make_backup(server.BACKUP_LOCKS)
        for n in range(3):
            self.request("POST", "/api/jobs", {**JOB, "name": f"Oak {n}"}, cookie=cookie)
        self.assertTrue(self.check(company_id)["ok"])
        backup.restore(made)
        # As audit_check.py reads it after a restore (the server is stopped for one): from the disk.
        folder = storage.DATA_DIR / "companies"
        state = (store.SqliteStore(folder) if store.db_file(folder).exists() else store.JsonStore(folder)).load(company_id)
        self.assertEqual(state["jobs"], [], "the backup's trail, made before the jobs")
        result = dict(audit_check.check_all([(company_id, state)]))[company_id]
        self.assertTrue(result["ok"], f"an older trail put back by a restore is not a changed trail: {result}")


class TeamManagersCantHandOutMore(ServerCase):
    """Someone who manages the team but isn't an Owner can't give themselves, a colleague or an
    invite a profile that can do more than they can (A4's aim: "someone holding team can't give
    themselves more")."""

    def setUp(self):
        super().setUp()
        self.owner, data = self.signup(f"mgr{id(self)}@pine.example")
        self.company = data["user"]["company_id"]
        for name, perms in (("Office", ["team", "send_payapps"]), ("Co-owner", [k for k, _ in server.profiles.PERMISSIONS])):
            res, out = self.request("POST", "/api/team/profiles", {"name": name, "perms": perms}, cookie=self.owner)
            self.assertEqual(res.status, 200, out)
        team = self.request("GET", "/api/team", cookie=self.owner)[1]
        self.ids = {p["name"]: p["id"] for p in team["profiles"]}
        mgr = auth.create_user(f"office{id(self)}@pine.example", PASSWORD, "Olga Office", self.company, role="member")
        clerk = auth.create_user(f"clerk{id(self)}@pine.example", PASSWORD, "Cal Clerk", self.company, role="member")
        auth.set_role(self.company, mgr["id"], self.ids["Office"], actor_id=data["user"]["id"], profiles=list(self.ids.values()))
        self.mgr, self.clerk = mgr, clerk
        self.mgr_cookie = "duracash_session=" + auth.create_session(auth.user_by_email(mgr["email"]))

    def test_not_to_themselves_a_colleague_or_an_invite(self):
        for who in (self.mgr["id"], self.clerk["id"]):
            res, out = self.request("POST", f"/api/team/{who}/role", {"profile_id": self.ids["Co-owner"]}, cookie=self.mgr_cookie)
            self.assertEqual(res.status, 409, out)
            self.assertIn("more than you can", out["error"])
        res, out = self.request("POST", "/api/team/invite", {"email": "new@pine.example", "profile_id": self.ids["Co-owner"]}, cookie=self.mgr_cookie)
        self.assertEqual(res.status, 409, out)
        self.assertEqual(auth.user_by_email(self.mgr["email"])["profile_id"], self.ids["Office"])
        # What they hold themselves, or less, they can still hand out; an owner hands out anything.
        res, out = self.request("POST", f"/api/team/{self.clerk['id']}/role", {"profile_id": self.ids["Office"]}, cookie=self.mgr_cookie)
        self.assertEqual(res.status, 200, out)
        res, out = self.request("POST", f"/api/team/{self.clerk['id']}/role", {"profile_id": self.ids["Co-owner"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)


class SensitiveFilesStayClosedEverywhere(ServerCase):
    def test_the_payments_reader_doesnt_open_a_payroll_file_for_someone_without_the_permission(self):
        owner, data = self.signup("payroll-csv@pine.example")
        company_id = data["user"]["company_id"]
        res, snap = self.request("POST", "/api/jobs", JOB, cookie=owner)
        job_id = snap["jobs"][0]["id"]
        res, out = self.request("POST", f"/api/import/file?job={job_id}&file=1&kind=payroll", raw=b"Name,Total\nJo Welder,1234.56\n", cookie=owner,
                                headers={"Content-Type": "text/csv", "X-Filename": "certified-payroll.csv"})
        self.assertEqual(res.status, 200, out)
        file_id = next(f["id"] for f in out["files"] if f["name"] == "certified-payroll.csv")
        auth.create_user("crew@pine.example", PASSWORD, "Cy Crew", company_id, role="member")
        crew = "duracash_session=" + auth.create_session(auth.user_by_email("crew@pine.example"))
        res, out = self.request("POST", "/api/import/payments-csv", {"file_id": file_id}, cookie=crew)
        self.assertEqual(res.status, 403, out)
        self.assertNotIn("Jo Welder", json.dumps(out))
        res, out = self.request("POST", "/api/import/payments-csv", {"file_id": file_id}, cookie=owner)
        self.assertEqual(res.status, 200, out)

    def test_a_payroll_files_text_is_hidden_as_the_file_is(self):
        owner, data = self.signup("payroll-text@pine.example")
        company_id = data["user"]["company_id"]
        res, snap = self.request("POST", "/api/jobs", JOB, cookie=owner)
        job_id = snap["jobs"][0]["id"]
        res, out = self.request("POST", f"/api/import/file?job={job_id}&file=1&kind=payroll", raw=b"Name,Total\nJo Welder,1234.56\n", cookie=owner,
                                headers={"Content-Type": "text/csv", "X-Filename": "certified-payroll.csv"})
        self.assertEqual(res.status, 200, out)
        self.assertIn("Jo Welder", json.dumps(out), "the owner sees it")
        res, out = self.request("POST", "/api/import/file", raw=b"Week of Nov 2: poured slab", cookie=owner,
                                headers={"Content-Type": "text/plain", "X-Filename": "site-note.txt"})
        auth.create_user("crew2@pine.example", PASSWORD, "Cy Crew", company_id, role="member")
        crew = "duracash_session=" + auth.create_session(auth.user_by_email("crew2@pine.example"))
        for path in ("/api/state", f"/api/jobs/{job_id}", "/api/inbox"):
            res, out = self.request("GET", path, cookie=crew)
            self.assertEqual(res.status, 200, path)
            self.assertNotIn("Jo Welder", json.dumps(out), path)
            if "inbox" in out or "entries" in out:
                self.assertIn("poured slab", json.dumps(out), f"{path}: other uploads keep their text")
        res, out = self.request("POST", "/api/import/file", raw=b"another note", cookie=crew,
                                headers={"Content-Type": "text/plain", "X-Filename": "n.txt"})
        self.assertEqual(res.status, 200, out)
        self.assertNotIn("Jo Welder", json.dumps(out), "nor in the answer to a change")
        res, out = self.request("GET", "/api/state", cookie=owner)
        self.assertIn("Jo Welder", json.dumps(out), "the owner still does")


# Emails Python's email package trips on (found by fuzzing, Nov 27): a broken address across two
# lines, a Content-Type with a stray ";", a NUL in a charset.
BROKEN_EMAILS = [
    b"From: A <a@\r\nb.example>\r\nTo: c@d.example<\r\nSubject: Pay app\r\n\r\nHello\r\n",
    b"From: A <a@b.example>\r\nTo: c@d.example\r\nSubject: x\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; b/undary=XX\r\n\r\n"
    b"--XX\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n--XX\r\nContent-Type: application/p;df; name=\"a.pdf\"\r\n"
    b"Content-Disposition: attachment; filename=\"a.pdf\"\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERi0xLjQK\r\n--XX--\r\n",
    b"From: A <a@b.example>\r\nSubject: x\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=u\x00tf-8\r\n\r\nHello\r\n",
]
BROKEN_SHARE = (b"--Btion\xff\xfe: form-data; name=\"title\"\xb0\n\r\n-Disposition: form-data; name=\"files\"; filename=\"p.jpg\"\r\n"
                b"Content-Type: image/jpeg\r\n\r\n\xff\xd8\xff\r\n--BB--\r\n")


class BrokenEmailsAreReadNotCrashed(ServerCase):
    def test_upload_paste_and_share(self):
        cookie, _ = self.signup("broken-mail@pine.example")
        for n, raw in enumerate(BROKEN_EMAILS):
            res, out = self.request("POST", "/api/import/file", raw=raw, cookie=cookie,
                                    headers={"Content-Type": "message/rfc822", "X-Filename": f"m{n}.eml"})
            self.assertEqual(res.status, 200, (n, out))
            res, out = self.request("POST", "/api/import/text", {"text": raw.decode("utf-8", "replace"), "source": "email"}, cookie=cookie)
            self.assertEqual(res.status, 200, (n, out))
        for who in (cookie, None):
            res, _ = self.request("POST", "/share", raw=BROKEN_SHARE, cookie=who,
                                  headers={"Content-Type": "multipart/form-data; boundary=BB", "Sec-Fetch-Site": "same-origin"})
            self.assertEqual(res.status, 303)


class InvitesDontRevealAccounts(ServerCase):
    """An invite answers the same whether or not the address has an account (as sign-up, L3)."""

    def test_the_same_reply_and_the_person_is_told_what_to_do(self):
        self.mail_on()
        owner, _ = self.signup("inv-owner@pine.example")
        self.signup("inv-taken@elsewhere.example", "Elsewhere Co")
        replies = {}
        for addr in ("inv-taken@elsewhere.example", "inv-new@pine.example"):
            res, out = self.request("POST", "/api/team/invite", {"email": addr, "profile_id": "member"}, cookie=owner)
            self.assertEqual(res.status, 200, out)
            replies[addr] = {k: out[k] for k in ("emailed", "email_status", "invite_path", "invite_link")}
            self.assertIn(addr, [i["email"] for i in out["invites"]], "listed with the open invites, as any invite")
        self.assertEqual(replies["inv-taken@elsewhere.example"], replies["inv-new@pine.example"])
        self.assertEqual(replies["inv-new@pine.example"], {"emailed": True, "email_status": "sent", "invite_path": None, "invite_link": None})
        text = self.mail_to("inv-taken@elsewhere.example", "already has a Duracash account")
        self.assertIsNotNone(text)
        self.assertNotIn("#invite=", text, "no link that can't work")
        self.assertIsNotNone(self.mail_to("inv-new@pine.example", "#invite="))
        # Someone already on this team is said to be, as the team list shows anyway.
        res, out = self.request("POST", "/api/team/invite", {"email": "inv-owner@pine.example"}, cookie=owner)
        self.assertEqual((res.status, out["error"]), (409, "That person is already on this team."))


class RateLimitTableKeepsLiveCounts(unittest.TestCase):
    def test_a_full_table_drops_expired_keys_not_live_limits(self):
        saved = (dict(server._LIMITS), dict(server._LIMIT_WINDOWS))
        self.addCleanup(lambda: (server._LIMITS.clear(), server._LIMITS.update(saved[0]),
                                 server._LIMIT_WINDOWS.clear(), server._LIMIT_WINDOWS.update(saved[1])))
        server._LIMITS.clear()
        server._LIMIT_WINDOWS.clear()
        with mock.patch.object(server, "LIMIT_KEYS", 50):
            for n in range(40):
                server._within_limit(f"short:{n}", 5, 1)
            for _ in range(5):
                self.assertTrue(server._within_limit("signup:203.0.113.9", 5, 3600))
            self.assertFalse(server._within_limit("signup:203.0.113.9", 5, 3600), "used up")
            import time
            time.sleep(1.1)
            for n in range(20):
                server._within_limit(f"spray:{n}", 5, 3600)
            self.assertFalse(any(k.startswith("short:") for k in server._LIMITS), "the expired keys went")
            self.assertFalse(server._within_limit("signup:203.0.113.9", 5, 3600), "a live count is kept, not cleared with the rest")
            # Past the cap with nothing expired, the longest idle go, down to nine tenths of it.
            for n in range(100):
                server._within_limit(f"more:{n}", 5, 3600)
            self.assertLessEqual(len(server._LIMITS), 51)
            self.assertIn("more:99", server._LIMITS)


if __name__ == "__main__":
    unittest.main()
