"""Permission profiles (S6): what each person on a company's team may do.

A profile is a name and a set of permissions. Two are built in: Owner, which holds every
permission and can't be changed, and Member, whose permissions an owner can change (it starts
with what members could always do). Anyone holding "team" can add named profiles of their own.
The company keeps them in state["company"]["profiles"]; each user's profile_id is on their
account (auth.py), and the server checks has_perm() before every gated route and job action
(server.py), so hiding a button on the page is never the only gate.

Deleting the company is not a permission a profile can hold: it belongs to the one Company Admin
(S8, auth.company_admin), who must be an Owner.

Imported as `from domain import profiles`; domain helpers are imported inside functions so the
modules never import each other at load time.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

# (key, the words on the Team page), in the order the page lists them.
PERMISSIONS: list[tuple[str, str]] = [
    ("send_payapps", "Send pay apps"),
    ("send_waivers", "Send lien waivers"),
    ("ask_retainage", "Ask for retainage"),
    ("record_payments", "Record payments"),
    ("void", "Delete drafts, pull back sent pay apps and accept short pays"),
    ("close_jobs", "Finish and reopen jobs"),
    ("company", "Edit company details, client settings and connections"),
    ("team", "Manage the team and permissions"),
    ("billing", "Manage billing"),
    ("data", "Export and reset data"),
]
PERM_KEYS = [k for k, _ in PERMISSIONS]
PERM_WORDS = dict(PERMISSIONS)

OWNER = "owner"
MEMBER = "member"
BUILT_IN = (OWNER, MEMBER)
# What members could do before profiles: record payments; the three sends only where the
# company had "Members can send" on (migrated into the Member profile).
MEMBER_DEFAULT = ["record_payments"]
MEMBER_SENDS = ["send_payapps", "send_waivers", "ask_retainage"]
MAX_PROFILES = 20
NAME_MAX = 40

# Each job action (server.JOB_ACTIONS) and the permission it needs; None: anyone on the team.
# Every JOB_ACTIONS key must be listed here (tests/test_team.py checks), so a new action is
# given a permission on purpose rather than left open by accident.
JOB_ACTION_PERMS: dict[str, str | None] = {
    "send": "send_payapps",
    "waiver": "send_waivers",
    "retainage": "ask_retainage",
    "match-payment": "record_payments",
    "retainage-paid": "record_payments",
    "accept-short-pay": "void",
    "void-draft": "void",
    "void-sent": "void",
    "delete": "void",
    "close": "close_jobs",
    "reopen": "close_jobs",
    # Drafting, records, paperwork and follow-ups: everyone on the team.
    "done": None, "undo-done": None, "progress": None, "sov": None, "sov-progress": None, "draft": None,
    "resolve": None, "packet-skip": None, "follow-up": None, "note-contact": None, "edit": None,
    "add-co": None, "approve-co": None, "request-co-signature": None, "include-co": None, "withdraw-co": None,
    "attach": None, "detach": None, "add-evidence": None, "add-record": None, "attach-record": None,
    "edit-record": None, "daily-record": None, "pencil": None, "pencil-response": None,
    "learn-rejection": None, "import-qbo": None,
    # Added on dev by the other streams of the Oct 29 review (anyone, as they shipped).
    "waiver-type": None, "record-work": None, "co-logged": None, "co-work-started": None,
}


def _company(state: dict[str, Any]) -> dict[str, Any]:
    return state.setdefault("company", {})


def default_profiles(members_can_send: bool = False) -> list[dict[str, Any]]:
    """The two built-in profiles a company starts with (Owner's permissions are never stored)."""
    member = MEMBER_DEFAULT + (MEMBER_SENDS if members_can_send else [])
    return [{"id": OWNER, "name": "Owner", "built_in": True, "locked": True},
            {"id": MEMBER, "name": "Member", "built_in": True, "perms": [k for k in PERM_KEYS if k in member]}]


def profiles(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Every profile with its permissions filled in: Owner first, then Member, then custom ones.
    A company file from before profiles reads as the defaults."""
    stored = _company(state).get("profiles")
    if not isinstance(stored, list) or not stored:
        stored = default_profiles(bool(_company(state).get("members_can_send")))
    out = []
    for p in stored:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        perms = PERM_KEYS if p["id"] == OWNER else [k for k in PERM_KEYS if k in (p.get("perms") or [])]
        out.append({**p, "perms": list(perms)})
    if not any(p["id"] == OWNER for p in out):
        out.insert(0, default_profiles()[0] | {"perms": list(PERM_KEYS)})
    if not any(p["id"] == MEMBER for p in out):
        out.insert(1, default_profiles()[1])
    return out


def profile(state: dict[str, Any], profile_id: str) -> dict[str, Any] | None:
    return next((p for p in profiles(state) if p["id"] == profile_id), None)


def profile_ids(state: dict[str, Any]) -> list[str]:
    return [p["id"] for p in profiles(state)]


def profile_name(state: dict[str, Any], profile_id: str) -> str:
    p = profile(state, profile_id)
    return p["name"] if p else "Member"


def user_profile_id(user: dict[str, Any]) -> str:
    """A person's profile: profile_id on their account, or, for accounts from before profiles,
    their role (owner or member)."""
    return str(user.get("profile_id") or user.get("role") or MEMBER)


def perms_of(user: dict[str, Any], state: dict[str, Any]) -> list[str]:
    """What this person may do. A profile that no longer exists gives Member's permissions."""
    pid = user_profile_id(user)
    p = profile(state, pid) or profile(state, MEMBER)
    return list(p["perms"]) if p else []


def has_perm(user: dict[str, Any], perm: str, state: dict[str, Any]) -> bool:
    if perm not in PERM_WORDS:
        raise KeyError(f"unknown permission {perm}")
    return perm in perms_of(user, state)


def may_take(user: dict[str, Any], action: str, state: dict[str, Any]) -> bool:
    """Whether this person may take this job action (mirrored by mayTake in static/js/jobs.js)."""
    need = JOB_ACTION_PERMS.get(action)
    return need is None or has_perm(user, need, state)


def holders(state: dict[str, Any], perm: str) -> set[str]:
    """The ids of the profiles that hold this permission."""
    return {p["id"] for p in profiles(state) if perm in p["perms"]}


def save_profile(state: dict[str, Any], profile_id: str | None, name: Any, perms: Any, actor: str) -> dict[str, Any]:
    """Add a profile (profile_id None) or change one. Owner can't be changed; Member can be given
    other permissions but keeps its name. Names are unique on the team."""
    from domain import GateError, audit
    company = _company(state)
    current = profiles(state)
    existing = next((p for p in current if p["id"] == profile_id), None) if profile_id else None
    if profile_id and not existing:
        raise GateError("That profile no longer exists.")
    if existing and existing.get("locked"):
        raise GateError("The Owner profile holds every permission and can't be changed.")
    errors: dict[str, str] = {}
    if existing and existing["id"] == MEMBER:
        clean_name = existing["name"]
    else:
        clean_name = " ".join(str(name or "").split()) if isinstance(name, str) else ""
        if not clean_name or not re.search(r"[A-Za-z0-9]", clean_name):
            errors["name"] = "Name the profile, like Office manager."
        elif len(clean_name) > NAME_MAX:
            errors["name"] = f"Shorten the name to {NAME_MAX} characters or fewer."
        elif any(p["name"].lower() == clean_name.lower() and p["id"] != profile_id for p in current):
            errors["name"] = "Another profile has that name."
    if not isinstance(perms, list) or any(not isinstance(k, str) or k not in PERM_WORDS for k in perms):
        errors["perms"] = "Tick the permissions from the list."
    if errors:
        raise GateError("Profile not saved.", fields=errors)
    clean_perms = [k for k in PERM_KEYS if k in perms]
    if not existing and len(current) >= MAX_PROFILES:
        raise GateError(f"A company can have up to {MAX_PROFILES} profiles.")
    stored = [{k: v for k, v in p.items() if not (p["id"] == OWNER and k == "perms")} for p in current]
    if existing:
        before = existing["perms"]
        for p in stored:
            if p["id"] == existing["id"]:
                p["name"], p["perms"] = clean_name, clean_perms
        added = [PERM_WORDS[k] for k in clean_perms if k not in before]
        removed = [PERM_WORDS[k] for k in before if k not in clean_perms]
        renamed = clean_name != existing["name"]
        if not (added or removed or renamed):
            company["profiles"] = stored
            return next(p for p in profiles(state) if p["id"] == existing["id"])
        what = "; ".join(x for x in (
            f"renamed from {existing['name']}" if renamed else "",
            f"can now: {', '.join(added).lower()}" if added else "",
            f"can no longer: {', '.join(removed).lower()}" if removed else "") if x)
        audit(state, "", "team", f"{actor} changed the {clean_name} profile: {what}.", actor)
        saved_id = existing["id"]
    else:
        saved_id = "prof-" + secrets.token_hex(4)
        stored.append({"id": saved_id, "name": clean_name, "perms": clean_perms})
        audit(state, "", "team", f"{actor} added the {clean_name} profile: "
              f"{', '.join(PERM_WORDS[k] for k in clean_perms).lower() or 'no permissions'}.", actor)
    company["profiles"] = stored
    return next(p for p in profiles(state) if p["id"] == saved_id)


def delete_profile(state: dict[str, Any], profile_id: str, in_use: bool, actor: str) -> None:
    """Delete a custom profile no one holds (people and open invites move to another first)."""
    from domain import GateError, audit
    p = profile(state, profile_id)
    if not p:
        raise GateError("That profile no longer exists.")
    if p["id"] in BUILT_IN:
        raise GateError(f"The {p['name']} profile is built in and can't be deleted.")
    if in_use:
        raise GateError(f"Someone on the team or an open invite has the {p['name']} profile. Give them another profile first.")
    _company(state)["profiles"] = [{k: v for k, v in q.items() if not (q["id"] == OWNER and k == "perms")}
                                   for q in profiles(state) if q["id"] != profile_id]
    audit(state, "", "team", f"{actor} deleted the {p['name']} profile.", actor)


def migrate_company(state: dict[str, Any]) -> None:
    """settings-auth (S6): give a company file its profiles. "Members can send", the switch the
    profiles replace, becomes the Member profile's three send permissions, and the switch is
    retired. Safe to run again: a file that has profiles is left alone, but the switch is dropped."""
    company = _company(state)
    if not isinstance(company.get("profiles"), list) or not company["profiles"]:
        company["profiles"] = default_profiles(bool(company.get("members_can_send")))
    company.pop("members_can_send", None)
