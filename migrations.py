"""Schema versions for a company file, and the steps between them (P1-12).

Every company file carries schema_version. load_company() runs migrate() on what it
reads, so code only ever sees the current shape; the next save writes it back. To change
the shape of the data: add a step at the end of STEPS that turns version N into N + 1,
and a test that loads a version-N file. Steps must be safe to run on any file of their
version, including ones written by hand.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import domain
import payapp_pdf
from domain import profiles


def _v1_to_v2(state: dict[str, Any]) -> None:
    """Fill lists older files lack, and seal the existing audit rows into a hash chain."""
    for key in ("payers", "jobs", "audit", "email_log", "daily_records", "inbox", "files"):
        state.setdefault(key, [])
    state.setdefault("company", {})
    domain.seal_audit(state)


def _v2_to_v3(state: dict[str, Any]) -> None:
    """Paperwork, not packet (Oct 23 note 21): a customer still holding the old default for
    "They send pay apps back when" gets the new one. Anything the person typed is left alone."""
    for payer in state.get("payers", []):
        if payer.get("rejects_when") == OLD_REJECTS_WHEN:
            payer["rejects_when"] = NEW_REJECTS_WHEN


OLD_REJECTS_WHEN = "Missing required packet items."
NEW_REJECTS_WHEN = "Missing required paperwork."


def _v3_to_v4(state: dict[str, Any]) -> None:
    """A pay app keeps the contract it was sent against (contract_at_send), so a change order
    approved or retainage released later doesn't change its PDF. Pay apps sent before that was
    kept get it worked out from the dates of the change orders and retainage payments."""
    for job in state.get("jobs", []):
        for inv in job.get("invoices", []):
            if inv.get("sent_at") and not inv.get("contract_at_send") and "contract_cents" in job:
                inv["contract_at_send"] = payapp_pdf.contract_terms(job, inv["sent_at"])


def _tasks_daily_lists(state: dict[str, Any]) -> None:
    """tasks-daily stream (G5, T3): the To Do list's hand-added tasks and who each computed row is
    assigned to start empty. Daily records keep their shape; the new fields are optional."""
    if not isinstance(state.get("tasks"), list):
        state["tasks"] = []
    if not isinstance(state.get("task_assignments"), dict):
        state["task_assignments"] = {}



# What each audit action means on a change order's own history (Stephen's Oct 29 review, M10).
CO_AUDIT_EVENTS = {"add_co": "created", "include_co": "included", "approve_co": "signed", "withdraw_co": "withdrawn",
                   "request_co_signature": "signature_requested", "change_order_off_draft": "off_draft"}


CO_NAMED = re.compile(r"\bCO (\d+)\b")


def _payapps_cos_co_history(state: dict[str, Any]) -> None:
    """payapps-cos: a change order keeps its own history (who created, put on the bill, signed or
    withdrew it, and when). Change orders made before that get it from the job's audit rows that
    name them ("CO 3"), with created_at/by, included_at/by, approved_by and withdrawn_by filled the
    same way. One without matching rows is left without a history."""
    rows_by_job: dict[str, list[dict[str, Any]]] = {}
    for row in state.get("audit", []):
        if row.get("action") in CO_AUDIT_EVENTS:
            rows_by_job.setdefault(row.get("job_id", ""), []).append(row)
    stamps = {"created": ("created_at", "created_by"), "included": ("included_at", "included_by"),
              "signed": ("approved_at", "approved_by"), "withdrawn": ("withdrawn_at", "withdrawn_by")}
    for job in state.get("jobs", []):
        for co in job.get("change_orders", []):
            if co.get("history") or "number" not in co:
                continue
            history = []
            for row in rows_by_job.get(job.get("id", ""), []):
                # The first "CO N" in the row is the one it is about; a title can name another.
                named = CO_NAMED.search(str(row.get("detail", "")))
                if named and int(named.group(1)) == int(co["number"]):
                    what = CO_AUDIT_EVENTS[row["action"]]
                    history.append({"at": row.get("at", ""), "what": what, "by": row.get("actor", "")})
                    at_key, by_key = stamps.get(what, (None, None))
                    if at_key:
                        co.setdefault(at_key, row.get("at", ""))
                        co.setdefault(by_key, row.get("actor", ""))
            if history:
                co["history"] = history


INSURANCE_LABEL_RE = re.compile(r"\b(?:insurance|coi|certificate of insurance)\b", re.I)


def _paperwork_insurance_records(state: dict[str, Any]) -> None:
    """Insurance certificates have their own record kind now (M6), which fills an Insurance
    certificate item. A record filed as Other whose label clearly says insurance becomes one;
    every other record keeps its kind."""
    for job in state.get("jobs", []):
        for rec in job.get("records", []):
            if rec.get("kind") == "other" and INSURANCE_LABEL_RE.search(str(rec.get("label") or "")):
                rec["kind"] = "insurance"


def _jobs_clients_address_parts(state: dict[str, Any]) -> None:
    """jobs-clients stream (Stephen's Oct 29 review, J1 and J2). A job's address is kept in four
    parts (street, city, state, ZIP) beside the one line the PDF, the client's page and Inbox
    matching read: an older job gets its parts from that line, split when it reads as
    "Street, City, ST 12345", else all of it as the street, so nothing is lost and job.address
    is untouched. A client saved before there were client types gets the one its role means
    (GC: General contractor; Owner: Owner or developer)."""
    for job in state.get("jobs", []):
        if job.get("address") and not isinstance(job.get("address_parts"), dict):
            job["address_parts"] = domain.parse_legacy_address(job["address"])
    for payer in state.get("payers", []):
        if payer.get("client_type") not in domain.CLIENT_TYPES:
            payer["client_type"] = domain.client_type_of(payer)


def _settings_auth_company_address_parts(state: dict[str, Any]) -> None:
    """settings-auth (S3): the company address in four boxes. A one-line address that reads as
    'Street, City, ST 12345' is split into street, city, state and zip; one that doesn't goes
    whole into street, so the owner sees it and fills in the rest. The line itself is kept."""
    company = state.get("company") or {}
    address = str(company.get("address") or "").strip()
    if not address or any(company.get(k) for k in domain.ADDRESS_PARTS):
        return
    for key, value in domain.parse_legacy_address(address).items():
        if value:
            company[key] = value


def _settings_auth_permission_profiles(state: dict[str, Any]) -> None:
    """settings-auth (S6): the company's permission profiles, Owner and Member. Where "Members
    can send" was on, Member gets the three send permissions; the switch itself is retired.
    Each person's profile comes from their role (auth.py reads a missing profile_id that way)."""
    profiles.migrate_company(state)


STEPS: list[Callable[[dict[str, Any]], None]] = [_v1_to_v2, _v2_to_v3, _v3_to_v4, _tasks_daily_lists, _payapps_cos_co_history,
                                                   _paperwork_insurance_records, _jobs_clients_address_parts,
                                                   _settings_auth_company_address_parts, _settings_auth_permission_profiles]


# ----- steps for auth.json (the accounts file), run once at start-up by auth.upgrade_file -----

def _settings_auth_company_admins(data: dict[str, Any]) -> bool:
    """settings-auth (S8): each company gets its one Company Admin, the only person who can delete
    the company: the earliest-created owner, where no one holds it yet. Returns whether anything
    changed. Safe to run again."""
    changed = False
    by_company: dict[str, list[dict[str, Any]]] = {}
    for user in data.get("users", []):
        by_company.setdefault(user.get("company_id", ""), []).append(user)
    for users in by_company.values():
        if any(u.get("company_admin") for u in users):
            continue
        owners = sorted((u for u in users if u.get("role", "owner") == "owner"), key=lambda u: u.get("created_at", ""))
        if owners:
            owners[0]["company_admin"] = True
            changed = True
    return changed


AUTH_STEPS: list[Callable[[dict[str, Any]], bool]] = [_settings_auth_company_admins]
SCHEMA_VERSION = len(STEPS) + 1


def migrate(state: dict[str, Any]) -> bool:
    """Bring a company file to SCHEMA_VERSION in place. Returns whether anything ran.
    A file from newer code is refused rather than guessed at."""
    version = int(state.get("schema_version") or 1)
    if version > SCHEMA_VERSION:
        raise ValueError(f"Company file is schema {version}; this code only knows up to {SCHEMA_VERSION}. Deploy the newer code.")
    for step in STEPS[version - 1:]:
        step(state)
    changed = version != SCHEMA_VERSION
    state["schema_version"] = SCHEMA_VERSION
    return changed
