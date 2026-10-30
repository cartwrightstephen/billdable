"""The route table (P2-7): every route is reachable, and the edges answer as before."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import server
import storage


class RouteTableTests(unittest.TestCase):
    def test_every_route_names_a_handler_and_a_known_access(self):
        for route in server.ROUTES:
            self.assertTrue(callable(getattr(server.Handler, route.handler, None)), route.handler)
            self.assertIn(route.access, {"public", "user", "body", "mutate"}, route.pattern)

    def test_no_route_shadows_another(self):
        seen = set()
        for route in server.ROUTES:
            self.assertNotIn((route.method, route.pattern), seen)
            seen.add((route.method, route.pattern))


class RouteEdgeTests(unittest.TestCase):
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

    def signup(self, email):
        res, data = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": email, "password": "correct horse battery",
                                                              "name": "Pat Pine", "company_name": "Pine Builders"})
        self.assertEqual(res.status, 200, data)
        return res.getheader("Set-Cookie").split(";")[0], data["user"]

    def test_unknown_or_malformed_actions_are_404_and_sign_in_comes_first(self):
        res, data = self.request("POST", "/api/jobs/x/done", {})
        self.assertEqual(res.status, 401)
        res, data = self.request("POST", "/api/nope", {})
        self.assertEqual(res.status, 401)
        cookie, _ = self.signup("routes@example.com")
        res, data = self.request("POST", "/api/jobs", {"name": "Elm", "customer": "Elm GC", "contract_cents": 100000, "address": "9 Elm St", "approver": "Dana Elm", "ap_email": "ap@elmgc.com"}, cookie=cookie)
        self.assertEqual(res.status, 200, data)
        job_id = data["jobs"][0]["id"]
        for path in [f"/api/jobs/{job_id}/nope", f"/api/jobs/{job_id}/approve-co", f"/api/jobs/{job_id}/done/extra", "/api/nope"]:
            res, data = self.request("POST", path, {}, cookie=cookie)
            self.assertEqual(res.status, 404, path)
        res, data = self.request("GET", "/api/nope", cookie=cookie)
        self.assertEqual((res.status, data), (404, {"error": "Not found"}))
        res, data = self.request("POST", "/api/jobs/no-such-job/done", {}, cookie=cookie)
        self.assertIn(res.status, {400, 409})


    def test_the_team_routes_need_manage_the_team(self):  # R6-08, S6
        team = {r.handler: r for r in server.ROUTES if r.method == "POST" and r.pattern.startswith(("/api/team", "/api/account"))}
        self.assertEqual(set(team), {"_p_team_invite", "_p_team_revoke", "_p_team_role", "_p_team_remove", "_p_account_delete",
                                     "_p_profile_save", "_p_profile_delete", "_p_admin_hand_over"})
        self.assertTrue(all(r.access == "body" for r in team.values()))
        # Handing over and deleting are the Company Admin's, checked in their handlers (S8).
        self.assertTrue(all(r.perm == "team" for h, r in team.items() if h not in {"_p_account_delete", "_p_admin_hand_over"}))
        owner, me = self.signup("team-routes@example.com")
        res, out = self.request("POST", "/api/team/invite", {"email": "team-member@example.com", "role": "member"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        res, joined = self.request("POST", "/api/auth/accept-invite", {"accept_terms": True, "token": out["invite_path"].split("#invite=")[1],
                                                                       "name": "Mo Member", "password": "member password 1"})
        self.assertEqual(res.status, 200, joined)
        member = res.getheader("Set-Cookie").split(";")[0]
        res, out = self.request("POST", "/api/team/invite", {"email": "late@example.com"}, cookie=owner)
        invite_id = self.request("GET", "/api/team", cookie=owner)[1]["invites"][0]["id"]
        for path, body in (("/api/team/invite", {"email": "x@example.com"}), (f"/api/team/invites/{invite_id}/revoke", {}),
                           (f"/api/team/{me['id']}/role", {"role": "member"}), (f"/api/team/{me['id']}/remove", {}),
                           ("/api/account/delete", {"password": "member password 1", "confirm": "Pine Builders"})):
            res, data = self.request("POST", path, body, cookie=member)
            self.assertEqual(res.status, 403, path)
            self.assertIn(data.get("error"), (server.NO_PERMISSION, "Only the Company Admin can delete the company."), path)
        res, data = self.request("POST", f"/api/team/invites/{invite_id}/revoke", {}, cookie=owner)
        self.assertEqual((res.status, data["invites"]), (200, []))
        res, data = self.request("POST", f"/api/team/{joined['user']['id']}/role", {"role": "owner"}, cookie=owner)
        self.assertEqual(res.status, 200)
        self.assertEqual(next(u for u in data["users"] if u["id"] == joined["user"]["id"])["role"], "owner")
        res, data = self.request("POST", f"/api/team/{joined['user']['id']}/remove", {}, cookie=owner)
        self.assertEqual((res.status, data["rotated"]), (200, True))
        res, _ = self.request("POST", f"/api/team/{me['id']}/nope", {}, cookie=owner)
        self.assertEqual(res.status, 404, "an unknown team action is not found")
        _, page = self.request("GET", "/api/audit", cookie=owner)
        self.assertTrue(any("revoked an invite" in e["detail"] for e in page["entries"] if e["action"] == "team"))

    def test_per_view_endpoints(self):
        cookie, _ = self.signup("views@example.com")
        job = {"name": "Ash", "customer": "Ash GC", "contract_cents": 100000, "address": "1 Ash St", "approver": "Al Ash", "ap_email": "ap@ashgc.com"}
        res, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        job_id = state["jobs"][0]["id"]
        self.assertNotIn("payer", state["jobs"][0], "payers are sent once, not per job")
        res, data = self.request("GET", f"/api/jobs/{job_id}", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(data["job"]["payer"]["name"], "Ash GC")
        res, data = self.request("GET", "/api/jobs/job-nope", cookie=cookie)
        self.assertEqual(res.status, 404)
        res, data = self.request("GET", "/api/jobs?status=open&limit=1", cookie=cookie)
        self.assertEqual((data["total"], len(data["jobs"])), (1, 1))
        res, data = self.request("GET", "/api/jobs?status=bogus", cookie=cookie)
        self.assertEqual(res.status, 400)
        res, data = self.request("GET", "/api/jobs?limit=x", cookie=cookie)
        self.assertEqual(res.status, 400)
        res, data = self.request("GET", f"/api/audit?job={job_id}", cookie=cookie)
        self.assertEqual(data["entries"][0]["job_id"], job_id)
        res, data = self.request("GET", "/api/outbox", cookie=cookie)
        self.assertEqual((res.status, data["total"]), (200, 0))
        res, _ = self.request("GET", "/api/outbox")
        self.assertEqual(res.status, 401)
        res, data = self.request("GET", f"/api/timeline?job={job_id}", cookie=cookie)
        self.assertEqual((res.status, data["entries"][0]["kind"]), (200, "action"))
        res, _ = self.request("GET", "/api/timeline?job=job-nope", cookie=cookie)
        self.assertEqual(res.status, 404)


    def test_styles_only_from_the_site_or_the_page_s_own_style_block(self):
        import base64, hashlib, re
        for path in ["/", "/terms", "/nope"]:
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
            conn.request("GET", path, headers={"Accept-Encoding": "gzip"} if path == "/" else {})
            res = conn.getresponse()
            body = res.read()
            conn.close()
            csp = res.getheader("Content-Security-Policy")
            self.assertNotIn("unsafe-inline", csp, path)
            if path != "/":
                for block in re.findall(rb"<style>(.*?)</style>", body, re.S):
                    self.assertIn(base64.b64encode(hashlib.sha256(block).digest()).decode(), csp, path)
                self.assertNotIn(b'style="', body, path)
        static = Path(server.__file__).parent / "static"
        for page in [static / "app.js", *sorted((static / "js" / "views").glob("*.js"))]:
            self.assertNotIn('style="', page.read_text(), f"{page.name}: style attributes are blocked by the CSP; use a class")


    def test_app_is_a_module_and_its_imports_are_served(self):
        import re
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request("GET", "/")
        page = conn.getresponse().read().decode()
        conn.close()
        self.assertIn('<script type="module" src="/app.js?v=', page)
        static = Path(server.__file__).parent / "static"
        # Every module the page loads, followed from app.js through each import (js/views/ too).
        imports, todo = set(), [static / "app.js"]
        while todo:
            module = todo.pop()
            for rel in re.findall(r'^import [^;]*? from "(\.{1,2}/[a-z/-]+\.js)";$', module.read_text(), re.M):
                target = (module.parent / rel).resolve()
                name = target.relative_to(static.resolve()).as_posix()
                if name not in imports:
                    imports.add(name)
                    todo.append(target)
        self.assertGreaterEqual(len(imports), 20)
        self.assertTrue({"js/app-core.js", "js/views/job.js", "js/views/money.js"} <= imports)
        for rel in sorted(imports):
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
            conn.request("GET", "/" + rel)
            res = conn.getresponse()
            res.read()
            conn.close()
            self.assertEqual(res.status, 200, rel)
            self.assertTrue(res.getheader("Content-Type").startswith("text/javascript"), rel)


if __name__ == "__main__":
    unittest.main()
