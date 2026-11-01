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


# A client's paperwork saved before the Client playbook's boxes came from the catalog: the old
# defaults for a new client and the old box labels, each with the catalog label it now is.
OLD_PAPERWORK_LABELS = {
    "conditional lien waiver": "Conditional waiver",
    "lien waiver": "Conditional waiver",
    "completion photos": "Progress photos",
    "progress or completion photos": "Progress photos",
    "pay-app backup": "Pay app backup",
    "daily tickets": "Daily tickets and time sheets",
    "daily records / time sheets / tickets": "Daily tickets and time sheets",
    "supplier invoices or material receipts": "Supplier invoices and receipts",
    "punch list sign-off (closeout)": "Punch list sign-off",
    "completion certificate (closeout)": "Completion certificate",
    "final lien waivers (closeout)": "Final lien waivers",
    "as-builts (closeout)": "As-builts",
    "warranties (closeout)": "Warranties",
}


def _followups_paperwork_catalog_labels(state: dict[str, Any]) -> None:
    """Follow-ups after Stephen's Oct 29 review (S11 with M6): the Client playbook's paperwork
    boxes are the standard catalog (domain.PAPERWORK_ITEMS). A client's saved list that uses an
    old default or an old box label is saved again with the catalog label, so it shows as a
    ticked box instead of under Other. Anything else in the list stays as typed, in its place;
    the items on pay apps already drafted are not touched."""
    for payer in state.get("payers", []):
        labels = domain.parse_packet_text(payer.get("packet") or "")
        new = list(dict.fromkeys(OLD_PAPERWORK_LABELS.get(l.lower(), l) for l in labels))
        if new != labels:
            payer["packet"] = ", ".join(new)


def _review_oct30_money_waivers(state: dict[str, Any]) -> None:
    """Fixes from the Oct 30 review of the money side (section B), on what is already stored:
    - B2: a paid pay app or retainage release still owing its unconditional waiver, for a client
      marked as not taking lien waivers (payer waiver_not_needed), owes none: it is marked Not
      needed with the client's reason, as a new payment for that client is, and the job's flags follow.
    - B3: an open draft's conditional waiver item asks for one on final payment only when no
      retainage is held on it or still open on the job.
    - B8: on an open draft, an item from the client's list naming an unconditional waiver is
      that type, and the conditional waiver is asked for beside it.
    Sent pay apps and finished jobs are not touched. Safe to run again."""
    payers = {p.get("id"): p for p in state.get("payers", [])}
    for job in state.get("jobs", []):
        if job.get("closed"):
            continue
        skip = (payers.get(job.get("payer_id")) or {}).get("waiver_not_needed")
        if skip:
            for owed in job.get("invoices", []) + job.get("retainage_waivers", []):
                if owed.get("waiver_due") and not owed.get("waiver_sent"):
                    owed["waiver_due"] = False
                    owed["waiver_not_needed"] = {"by": "duracash", "at": domain.utcnow(),
                                                 "reason": skip.get("reason") or "Not needed for this client", "client": True}
            domain._sync_waiver(job)
        for inv in job.get("invoices", []):
            if inv.get("status") not in domain.DRAFT_STATUSES or inv.get("packet_id") == "job-packet":
                continue
            packet = inv.get("packet") or []
            waivers = _type_waiver_items(packet)
            if waivers and not any(str(i.get("waiver_kind", "")).startswith("conditional") for i in packet):
                kind = "conditional_progress"
                item = domain._packet_item(inv["id"], "waiver", domain.WAIVER_KINDS[kind], f"{domain.WAIVER_KINDS[kind]} is missing.")
                item.update(id=_unused_id(packet, item["id"]), waiver_kind=kind, auto_waiver=True)
                if skip:
                    item.update(required=False, skipped_reason=skip.get("reason") or "Not needed for this client", skipped_at=domain.utcnow())
                packet.insert(0, item)
                if item["required"] and inv.get("status") == "awaiting_approval":
                    inv["status"] = "draft"  # the new item is missing, so the draft waits for it
            domain._name_conditional_waiver(job, inv)


def _type_waiver_items(packet: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The packet's waiver items, each with its type, by domain._new_packet's rule: an item naming
    an unconditional waiver is that type; any other waiver item without a type (a draft saved by
    older code, "Conditional lien waiver") is the conditional waiver on progress payment."""
    waivers = [i for i in packet if (domain._record_kinds_for(i) or ("",))[0] == "waiver"]
    for item in waivers:
        label = str(item.get("label") or "").lower()
        if "unconditional" in label:
            if not str(item.get("waiver_kind", "")).startswith("unconditional"):
                item["waiver_kind"] = f"unconditional_{'final' if 'final' in label else 'progress'}"
        elif not item.get("waiver_kind"):
            item["waiver_kind"] = "conditional_progress"
    return waivers


def _unused_id(packet: list[dict[str, Any]], wanted: str) -> str:
    used = {i.get("id") for i in packet}
    n, candidate = 2, wanted
    while candidate in used:
        candidate, n = f"{wanted}-{n}", n + 1
    return candidate


def _review_oct30_waiver_repair(state: dict[str, Any]) -> None:
    """Repairs what the first version of _review_oct30_money_waivers did to a draft saved by older
    code: it didn't see that draft's untyped waiver item ("Conditional lien waiver") as the
    conditional one, so it put a second, empty conditional item in front of it, often with the
    same id, and sent a draft awaiting approval back to draft. That added item goes (its file,
    if one was attached to it, moves to the old item), the old item gets its type, and the draft
    is awaiting approval again when nothing else is missing. Safe to run again."""
    for job in state.get("jobs", []):
        if job.get("closed"):
            continue
        for inv in job.get("invoices", []):
            if inv.get("status") not in domain.DRAFT_STATUSES or inv.get("packet_id") == "job-packet":
                continue
            packet = inv.get("packet") or []
            olds = [i for i in packet if (domain._record_kinds_for(i) or ("",))[0] == "waiver" and not i.get("auto_waiver")
                    and "unconditional" not in str(i.get("label") or "").lower()
                    and (not i.get("waiver_kind") or str(i.get("waiver_kind")).startswith("conditional"))]
            added = [i for i in packet if i.get("auto_waiver") and str(i.get("waiver_kind", "")).startswith("conditional")]
            if not olds or not added:
                continue
            old = olds[0]
            for extra in added:
                files = {a.get("record_id") for a in old.get("attachments", [])}
                old.setdefault("attachments", []).extend(a for a in extra.get("attachments", []) if a.get("record_id") not in files)
                old["present"] = bool(old.get("present") or extra.get("present"))
                packet.remove(extra)
            _type_waiver_items(packet)
            domain._name_conditional_waiver(job, inv)
            inv["status"] = "draft" if domain.blocker_lines(job) else "awaiting_approval"


STEPS: list[Callable[[dict[str, Any]], None]] = [_v1_to_v2, _v2_to_v3, _v3_to_v4, _tasks_daily_lists, _payapps_cos_co_history,
                                                   _paperwork_insurance_records, _jobs_clients_address_parts,
                                                   _settings_auth_company_address_parts, _settings_auth_permission_profiles,
                                                   _followups_paperwork_catalog_labels, _review_oct30_money_waivers,
                                                   _review_oct30_waiver_repair]


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
