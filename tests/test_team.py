"""Permission profiles (S6), the Company Admin (S8) and each person's appearance (G9)."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import migrations
import server
from domain import profiles
from test_accounts import PASSWORD, ServerCase
from seed import seed_state

JOB = {"name": "Elm", "customer": "Park GC", "address": "9 Elm", "approver": "Kim", "ap_email": "ap@park.example",
       "contract_cents": 1_000_000, "retainage_pct": 0, "role": "Owner"}


class ProfileRulesTests(unittest.TestCase):
    def test_every_job_action_has_a_permission_or_is_anyones(self):
        missing = sorted(set(server.JOB_ACTIONS) - set(profiles.JOB_ACTION_PERMS))
        self.assertEqual(missing, [], "map each new job action in domain/profiles.py JOB_ACTION_PERMS")
        for need in profiles.JOB_ACTION_PERMS.values():
            self.assertTrue(need is None or need in profiles.PERM_WORDS, need)

    def test_routes_name_real_permissions(self):
        for route in server.ROUTES:
            self.assertTrue(not route.perm or route.perm in profiles.PERM_WORDS, route.pattern)

    def test_the_page_mirrors_the_servers_job_action_permissions(self):
        js = (Path(__file__).resolve().parents[1] / "static/js/jobs.js").read_text()
        block = js[js.index("export const ACTION_PERM"):js.index("};", js.index("export const ACTION_PERM"))]
        pairs = dict((a.strip('"'), p) for a, p in __import__("re").findall(r'"?([a-z-]+)"?:\s*"([a-z_]+)"', block))
        self.assertEqual(pairs, {k: v for k, v in profiles.JOB_ACTION_PERMS.items() if v})

    def test_owner_holds_everything_and_member_starts_with_payments(self):
        state = seed_state()
        state["company"].pop("profiles", None)
        self.assertEqual(profiles.perms_of({"role": "owner"}, state), profiles.PERM_KEYS)
        self.assertEqual(profiles.perms_of({"role": "member"}, state), ["record_payments"])
        self.assertEqual(profiles.perms_of({"profile_id": "prof-gone"}, state), ["record_payments"], "a deleted profile reads as Member")
        with self.assertRaises(Exception):
            profiles.save_profile(state, "owner", "Owner", [], "Pat")

    def test_the_upgrade_turns_members_can_send_into_member_permissions(self):
        for on, perms in ((True, ["send_payapps", "send_waivers", "ask_retainage", "record_payments"]), (False, ["record_payments"])):
            state = seed_state()
            state["company"] = {"name": "Old Co", "members_can_send": on}
            state["schema_version"] = migrations.STEPS.index(migrations._settings_auth_permission_profiles) + 1
            migrations.migrate(state)
            self.assertNotIn("members_can_send", state["company"], "the switch is retired")
            member = next(p for p in state["company"]["profiles"] if p["id"] == "member")
            self.assertEqual(sorted(member["perms"]), sorted(perms))
            self.assertNotIn("perms", state["company"]["profiles"][0], "Owner's permissions aren't stored")

    def test_the_accounts_upgrade_makes_the_earliest_owner_company_admin(self):
        data = {"users": [
            {"id": "u2", "company_id": "co-a", "role": "owner", "created_at": "2026-02-01T00:00:00+00:00"},
            {"id": "u1", "company_id": "co-a", "role": "owner", "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "u0", "company_id": "co-a", "role": "member", "created_at": "2025-01-01T00:00:00+00:00"},
            {"id": "u3", "company_id": "co-b", "role": "owner", "created_at": "2026-03-01T00:00:00+00:00"},
            {"id": "u4", "company_id": "co-b", "role": "owner", "created_at": "2026-01-01T00:00:00+00:00", "company_admin": True},
        ]}
        self.assertTrue(migrations._settings_auth_company_admins(data))
        self.assertEqual({u["id"] for u in data["users"] if u.get("company_admin")}, {"u1", "u4"})
        self.assertFalse(migrations._settings_auth_company_admins(data), "running it again changes nothing")


class TeamHttpTests(ServerCase):
    def join(self, owner, addr, profile="member"):
        res, out = self.request("POST", "/api/team/invite", {"email": addr, "profile_id": profile}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        res, joined = self.request("POST", "/api/auth/accept-invite", {"accept_terms": True, "token": out["invite_path"].split("#invite=")[1],
                                                                       "name": addr.split("@")[0].title(), "password": "member password 1"})
        self.assertEqual(res.status, 200, joined)
        return res.getheader("Set-Cookie").split(";")[0], joined["user"]

    def test_a_custom_profile_is_enforced_by_the_server(self):
        owner, _ = self.signup("owner@prof.example", "Prof Co")
        res, out = self.request("POST", "/api/team/profiles", {"name": "Office manager", "perms": ["record_payments", "team"]}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        office = out["profile"]
        self.assertEqual((office["name"], office["perms"]), ("Office manager", ["record_payments", "team"]))
        res, out = self.request("POST", "/api/team/profiles", {"name": "office MANAGER", "perms": []}, cookie=owner)
        self.assertEqual((res.status, out["fields"]["name"]), (409, "Another profile has that name."))
        cookie, user = self.join(owner, "olive@prof.example", office["id"])
        self.assertEqual(user["profile_id"], office["id"])
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertEqual((me["user"]["perms"], me["user"]["profile_name"]), (["record_payments", "team"], "Office manager"))
        _, state = self.request("POST", "/api/jobs", JOB, cookie=owner)
        job_id = state["jobs"][0]["id"]
        self.request("POST", f"/api/jobs/{job_id}/progress", {"completed_cents": 400_000}, cookie=cookie)
        # The page hides Send for this profile; a request sent anyway is refused on the server.
        res, out = self.request("POST", f"/api/jobs/{job_id}/send", {}, cookie=cookie)
        self.assertEqual((res.status, out["error"]), (403, server.NO_PERMISSION))
        res, out = self.request("POST", "/api/company", {"name": "Renamed"}, cookie=cookie)
        self.assertEqual(res.status, 403)
        res, _ = self.request("GET", "/api/export", cookie=cookie)
        self.assertEqual(res.status, 403)
        # "Manage the team" lets them invite, with the profiles that exist.
        res, out = self.request("POST", "/api/team/invite", {"email": "new@prof.example", "profile_id": "prof-nope"}, cookie=cookie)
        self.assertEqual((res.status, out["error"]), (409, "Pick a profile from the list."))
        res, out = self.request("POST", "/api/team/invite", {"email": "new@prof.example", "profile_id": "owner"}, cookie=cookie)
        self.assertEqual((res.status, out["error"]), (409, auth.ONLY_OWNERS_MAKE_OWNERS), "only an owner makes owners")
        owner_id = next(u["id"] for u in out.get("users", []) or self.request("GET", "/api/team", cookie=owner)[1]["users"] if u["profile_id"] == "owner")
        res, out = self.request("POST", f"/api/team/{owner_id}/remove", {}, cookie=cookie)
        self.assertEqual((res.status, out["error"]), (409, auth.ONLY_OWNERS_MAKE_OWNERS))
        res, out = self.request("POST", "/api/team/invite", {"email": "new@prof.example", "profile_id": "member"}, cookie=cookie)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(len(out["invites"]), 1)
        # Taking "manage the team" off the profile ends that right at once, and their invites.
        res, out = self.request("POST", f"/api/team/profiles/{office['id']}", {"name": "Office manager", "perms": ["record_payments"]}, cookie=owner)
        self.assertEqual((res.status, out["invites"]), (200, []))
        res, _ = self.request("POST", "/api/team/invite", {"email": "late@prof.example"}, cookie=cookie)
        self.assertEqual(res.status, 403)
        _, page = self.request("GET", "/api/audit", cookie=owner)
        self.assertTrue(any("changed the Office manager profile: can no longer: manage the team and permissions" in e["detail"] for e in page["entries"]))
        # A profile someone holds can't be deleted; once no one does, it can.
        res, out = self.request("POST", f"/api/team/profiles/{office['id']}/delete", {}, cookie=owner)
        self.assertEqual(res.status, 409, out)
        res, out = self.request("POST", f"/api/team/{user['id']}/role", {"profile_id": "member"}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        res, out = self.request("POST", f"/api/team/profiles/{office['id']}/delete", {}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual([p["id"] for p in out["profiles"]], ["owner", "member"])
        res, out = self.request("POST", "/api/team/profiles/owner", {"name": "Boss", "perms": []}, cookie=owner)
        self.assertEqual(res.status, 409)

    def test_the_company_keeps_an_owner_and_its_admin(self):
        owner, me = self.signup("first@admin.example", "Admin Co")
        self.assertTrue(me["user"]["company_admin"], "the account that creates the company is its Company Admin")
        second, second_user = self.join(owner, "second@admin.example", "owner")
        res, out = self.request("POST", f"/api/team/{me['user']['id']}/role", {"profile_id": "member"}, cookie=second)
        self.assertEqual(res.status, 409)
        self.assertIn("Company Admin", out["error"])
        res, out = self.request("POST", f"/api/team/{me['user']['id']}/remove", {}, cookie=second)
        self.assertEqual(res.status, 409)
        # Only the Admin deletes the company, even another owner is refused.
        res, out = self.request("POST", "/api/account/delete", {"password": "member password 1", "confirm": "Admin Co"}, cookie=second)
        self.assertEqual((res.status, out["error"]), (403, "Only the Company Admin can delete the company."))
        # Handing the role over needs the Admin's password and an owner to hand it to.
        res, out = self.request("POST", "/api/team/admin", {"user_id": second_user["id"], "password": "wrong password"}, cookie=owner)
        self.assertEqual(res.status, 409)
        res, out = self.request("POST", "/api/team/admin", {"user_id": second_user["id"], "password": PASSWORD}, cookie=second)
        self.assertEqual(res.status, 409, "only the Admin hands it on")
        res, out = self.request("POST", "/api/team/admin", {"user_id": second_user["id"], "password": PASSWORD}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual([u["id"] for u in out["users"] if u["company_admin"]], [second_user["id"]])
        res, me_now = self.request("GET", "/api/auth/me", cookie=owner)
        self.assertFalse(me_now["user"]["company_admin"])
        res, out = self.request("POST", "/api/account/delete", {"password": PASSWORD, "confirm": "Admin Co"}, cookie=owner)
        self.assertEqual(res.status, 403)
        # The first owner can now become a member; the company still has its owner and Admin.
        res, out = self.request("POST", f"/api/team/{me['user']['id']}/role", {"profile_id": "member"}, cookie=second)
        self.assertEqual(res.status, 200, out)
        res, out = self.request("POST", "/api/account/delete", {"password": "member password 1", "confirm": "Admin Co"}, cookie=second)
        self.assertEqual(res.status, 200, out)

    def test_appearance_is_kept_per_person(self):
        cookie, _ = self.signup("look@pine.example")
        res, out = self.request("POST", "/api/me/appearance", {"theme": "dark", "accent": "slate"}, cookie=cookie)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(out["user"]["appearance"], {"theme": "dark", "accent": "slate"})
        res, me = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertEqual(me["user"]["appearance"], {"theme": "dark", "accent": "slate"})
        res, out = self.request("POST", "/api/me/appearance", {"theme": "purple", "accent": "slate"}, cookie=cookie)
        self.assertEqual((res.status, list(out["fields"])), (409, ["theme"]))
        other, _ = self.signup("look2@pine.example")
        res, me = self.request("GET", "/api/auth/me", cookie=other)
        self.assertEqual(me["user"]["appearance"], {"theme": "system", "accent": "copper"}, "someone else keeps their own")
        res, demo = self.request("POST", "/api/auth/demo", {})
        demo_cookie = res.getheader("Set-Cookie").split(";")[0]
        res, _ = self.request("POST", "/api/me/appearance", {"theme": "dark", "accent": "blue"}, cookie=demo_cookie)
        self.assertEqual(res.status, 409, "the shared sample account keeps its look in each browser")
        stored = json.loads(auth.AUTH_PATH.read_text())
        self.assertEqual(next(u for u in stored["users"] if u["email"] == "look@pine.example")["appearance"], {"theme": "dark", "accent": "slate"})


if __name__ == "__main__":
    unittest.main()
