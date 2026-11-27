"""Nov 26 walkthrough: the owner (or anyone who manages the team) sets anyone's notifications; a
person's own later change applies too, and the Team page shows what they changed. Also the
starting templates for a new permission profile."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import reminders
from domain import profiles
from test_accounts import ServerCase
from test_team import TeamHttpTests

ALL_EMAIL = {"dates": ["app", "email"], "missing": ["app", "email"], "co_records": ["app", "email"], "compliance": ["app", "email"]}


class TeamNotificationsTests(ServerCase):
    join = TeamHttpTests.join

    def team(self, cookie):
        res, out = self.request("GET", "/api/team", cookie=cookie)
        self.assertEqual(res.status, 200, out)
        return {u["email"]: u for u in out["users"]}, out

    def test_the_owner_sets_a_member_s_notifications_and_sees_what_they_change(self):
        owner, _ = self.signup("owner@notes.example", "Notes Co")
        member, pat = self.join(owner, "pat@notes.example")
        res, out = self.request("POST", "/api/team/reminders", {"user_ids": [pat["id"]], "prefs": ALL_EMAIL}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        row = {u["email"]: u for u in out["users"]}["pat@notes.example"]
        self.assertEqual(row["notifications"]["prefs"], ALL_EMAIL)
        self.assertEqual(row["notifications"]["set_by"], "Pat Pine")
        self.assertEqual(row["notifications"]["changed"], [])
        self.assertIn("reminders", out, "the kinds and channels, for the Team page's editor")
        # What Pat sees on My account: the owner's choices, and who set them.
        res, mine = self.request("GET", "/api/reminders", cookie=member)
        self.assertEqual(mine["prefs"], ALL_EMAIL)
        self.assertEqual(mine["set_by"]["by"], "Pat Pine")
        # Pat turns email off for important dates: that applies, and the owner sees it.
        res, out = self.request("POST", "/api/reminders/prefs", {"prefs": {**ALL_EMAIL, "dates": ["app"]}}, cookie=member)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(out["prefs"]["dates"], ["app"])
        users, _ = self.team(owner)
        changed = users["pat@notes.example"]["notifications"]["changed"]
        self.assertEqual([(c["kind"], c["channel"], c["on"]) for c in changed], [("dates", "email", False)])
        self.assertEqual(changed[0]["words"], "Email off for Important dates")
        # The owner sets them again: the owner's change applies now.
        self.request("POST", "/api/team/reminders", {"user_ids": [pat["id"]], "prefs": ALL_EMAIL}, cookie=owner)
        users, _ = self.team(owner)
        self.assertEqual(users["pat@notes.example"]["notifications"]["changed"], [])
        # The change is in the Activity log.
        res, log = self.request("GET", "/api/audit", cookie=owner)
        self.assertTrue(any("set the notifications of Pat" in e["detail"] for e in log["entries"]), log)

    def test_a_text_set_by_the_owner_waits_for_the_person_and_doesn_t_block_their_save(self):
        owner, _ = self.signup("owner@texts.example", "Texts Co")
        member, sam = self.join(owner, "sam@texts.example")
        prefs = {"dates": ["app", "text"], "missing": ["app"], "co_records": ["app"], "compliance": ["app"]}
        res, out = self.request("POST", "/api/team/reminders", {"user_ids": [sam["id"]], "prefs": prefs}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertFalse({u["email"]: u for u in out["users"]}["sam@texts.example"]["notifications"]["texts_on"])
        # Sam has not agreed to texts: saving the rest still works, the owner's Text stays waiting.
        res, out = self.request("POST", "/api/reminders/prefs", {"prefs": {**prefs, "missing": ["app", "email"]}}, cookie=member)
        self.assertEqual(res.status, 200, out)
        # A Text Sam ticks themselves, without a number, is still refused.
        res, out = self.request("POST", "/api/reminders/prefs", {"prefs": {**prefs, "missing": ["app", "text"]}}, cookie=member)
        self.assertEqual(res.status, 409, out)
        self.assertIn("missing", out["fields"])

    def test_who_may_set_them(self):
        owner, signed = self.signup("owner@who.example", "Who Co")
        me = signed["user"]
        member, kim = self.join(owner, "kim@who.example")
        _, lee = self.join(owner, "lee@who.example")
        # A member without "manage the team" can't, and doesn't see anyone's.
        res, out = self.request("POST", "/api/team/reminders", {"user_ids": [lee["id"]], "prefs": ALL_EMAIL}, cookie=member)
        self.assertEqual(res.status, 403, out)
        _, out = self.request("GET", "/api/team", cookie=member)
        self.assertTrue(all("notifications" not in u for u in out["users"]))
        # Not your own here (that is My account), nor someone on another team.
        res, out = self.request("POST", "/api/team/reminders", {"user_ids": [me["id"]], "prefs": ALL_EMAIL}, cookie=owner)
        self.assertEqual(res.status, 409, out)
        other, _ = self.signup("owner@else.example", "Else Co")
        res, out = self.request("POST", "/api/team/reminders", {"user_ids": [kim["id"]], "prefs": ALL_EMAIL}, cookie=other)
        self.assertEqual(res.status, 409, out)
        # Everyone at once; a kind or channel that doesn't exist is refused.
        res, out = self.request("POST", "/api/team/reminders", {"user_ids": [kim["id"], lee["id"]], "prefs": ALL_EMAIL}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        self.assertTrue(all(u["notifications"]["prefs"] == ALL_EMAIL for u in out["users"] if u["id"] != me["id"]))
        res, out = self.request("POST", "/api/team/reminders", {"user_ids": [kim["id"]], "prefs": {"dates": ["fax"]}}, cookie=owner)
        self.assertEqual(res.status, 400, out)
        res, out = self.request("POST", "/api/team/reminders", {"user_ids": [], "prefs": ALL_EMAIL}, cookie=owner)
        self.assertEqual(res.status, 400, out)

    def test_compliance_and_lien_dates_is_in_the_editor_and_the_defaults(self):
        # Integration (Nov 27): the compliance stream's reminder kind sits with the others on the
        # Team page's kinds x channels editor, in a new person's defaults, and for people whose
        # choices were saved before it existed it counts as in the app on both sides of the diff.
        import auth
        owner, _ = self.signup("owner@comp.example", "Comp Co")
        _, ana = self.join(owner, "ana@comp.example")
        users, out = self.team(owner)
        self.assertIn(("compliance", "Compliance and lien dates"), [(k["key"], k["label"]) for k in out["reminders"]["kinds"]])
        self.assertEqual(users["ana@comp.example"]["notifications"]["prefs"]["compliance"], ["app"])
        self.assertEqual(set(auth.NEW_USER_REMINDERS), set(reminders.KINDS), "a new person gets every kind")
        self.assertTrue(all(auth.NEW_USER_REMINDERS[k] == v for k, v in reminders.KIND_DEFAULTS.items()))
        # Set from the Team page with compliance email on, and then by the person: the diff names it.
        res, out = self.request("POST", "/api/team/reminders", {"user_ids": [ana["id"]], "prefs": {"compliance": ["app", "email"]}}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        row = {u["email"]: u for u in out["users"]}["ana@comp.example"]["notifications"]
        self.assertEqual((row["prefs"]["compliance"], row["changed"]), (["app", "email"], []))
        # An account saved before the kind existed (no "compliance" key), set by the team then too.
        auth_data = auth._load()
        user = next(u for u in auth_data["users"] if u["id"] == ana["id"])
        old = {"dates": ["app"], "missing": ["app"], "co_records": ["app"]}
        user["reminders"], user["reminders_team"] = dict(old), {"prefs": dict(old), "by": "Pat", "by_id": "x", "at": "2026-11-01T00:00:00+00:00"}
        auth._save(auth_data)
        users, _ = self.team(owner)
        notes = users["ana@comp.example"]["notifications"]
        self.assertEqual((notes["prefs"]["compliance"], notes["changed"]), (["app"], []), "in the app by default; not a change by them")


class ChangesTests(unittest.TestCase):
    def test_changes_lists_each_channel_turned_on_or_off(self):
        team = {"dates": ["app", "email"], "missing": ["app"], "co_records": []}
        mine = {"dates": ["app"], "missing": ["app", "text"], "co_records": []}
        self.assertEqual([c["words"] for c in reminders.changes(team, mine)],
                         ["Email off for Important dates", "Text on for Missing information"])
        self.assertEqual(reminders.changes(None, mine), [], "never set by the team: nothing to compare")
        # A kind added after the team set them counts as its default (compliance: in the app).
        self.assertEqual(reminders.changes(team, {**mine, "compliance": ["app"]})[2:], [])
        self.assertEqual([c["words"] for c in reminders.changes(team, {**mine, "compliance": []})][2:], ["In the app off for Compliance and lien dates"])


class TemplateTests(unittest.TestCase):
    def test_the_five_starting_templates(self):
        self.assertEqual([t["name"] for t in profiles.TEMPLATES],
                         ["Owner", "Accountant or office", "Project manager", "Superintendent", "Field"])
        for t in profiles.TEMPLATES:
            self.assertTrue(all(k in profiles.PERM_KEYS for k in t["perms"]), t["id"])
            self.assertNotEqual(t["profile_name"].lower(), "owner", "the built-in Owner keeps its name")
        by = {t["id"]: set(t["perms"]) for t in profiles.TEMPLATES}
        self.assertEqual(by["owner"], set(profiles.PERM_KEYS))
        self.assertEqual(by["field"], set())
        self.assertNotIn("team", by["office"] | by["pm"] | by["super"], "only an owner-like profile manages the team")


if __name__ == "__main__":
    unittest.main()
