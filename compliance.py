"""Compliance documents and lien deadlines (Siteline comparison, items 7 and 8): the Jobs,
Compliance tab.

Kept in state["compliance"], apart from the jobs, so nothing else's shape changes:

- docs: the company's documents: certificates of insurance, W-9s, contractor licenses, bonds, DBE
  certifications, certified payroll and anything else. Each: {id, type, holder, issuer, number,
  effective_on, expires_on, payer_id, job_id, additional_insured, note, file_id, added_at,
  added_by}. payer_id names the client a document is for (a COI naming them as additional
  insured); blank is the company's own, good for every client. No Social Security or tax ID is
  kept as a field: a W-9 is its file only, and a number shaped like either is refused.
- requirements: what a client (or one of its jobs) needs on file: {id, payer_id, job_id, type,
  additional_insured, note}.
- remind_days: when reminders go before a document expires (30, 14 and 3 days unless changed).
- liens: {job id: the lien inputs}: state, how the company stands to the owner (tier), first
  and last furnishing (the last read from the latest job record when not typed), the Notice of
  Commencement's filing date, a notice of contest received, a waiver signed outside Duracash,
  what was done when (done) and what the company isn't doing (skipped). liens.py works out the
  dates from these.

Files go through the importer's store (importer.store_bytes), registered on state["files"] with
the importer's kind (insurance, payroll, other) and, for a W-9 or certified payroll, sensitive:
only people with "See payroll and sensitive documents" (sensitive_docs) open those, here and by
/api/files/<id>, and the export with files leaves them out for others (profiles.sensitive_file_ids).

Reminders: reminders.py's "compliance" kind reads reminder_items(): a document expiring within a
reminder day or expired, a client's requirement with nothing current on file, and a lien
deadline coming up on an open job. In the app they are rows on the Dashboard's To Do.

The soft gate: payapp_warnings() says when a job's client needs a certificate of insurance and
the one on file is expired or missing. The pay app page shows it above Approve & send; it never
stops the send.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timezone
from typing import Any

import liens

DOC_TYPES: dict[str, dict[str, Any]] = {
    "coi": {"label": "Certificate of insurance (COI)", "file_kind": "insurance", "sensitive": False, "expires": True},
    "w9": {"label": "W-9", "file_kind": "other", "sensitive": True, "expires": False, "no_number": True},
    "license": {"label": "Contractor license", "file_kind": "other", "sensitive": False, "expires": True},
    "bond": {"label": "Bond", "file_kind": "other", "sensitive": False, "expires": True},
    "dbe": {"label": "DBE certification", "file_kind": "other", "sensitive": False, "expires": True},
    "payroll": {"label": "Certified payroll", "file_kind": "payroll", "sensitive": True, "expires": False, "no_number": True},
    "other": {"label": "Other", "file_kind": "other", "sensitive": False, "expires": True},
}
SENSITIVE_TYPES = {k for k, v in DOC_TYPES.items() if v["sensitive"]}
DEFAULT_REMIND_DAYS = [30, 14, 3]
MAX_REMIND_DAYS = 120
MAX_DOCS = 500
MAX_REQUIREMENTS = 200
TEXT_MAX = 120
NOTE_MAX = 500
# A Social Security number (123-45-6789) or an employer ID (12-3456789), with or without spaces.
_TAX_ID = re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b|\b\d{2}[- ]\d{7}\b")
_TAX_WORDS = re.compile(r"\b(?:ssn|ein|tin|fein|itin|social security|tax id|taxpayer)\b", re.I)
DOC_ID = r"cdoc-[0-9a-f]{8}"
REQ_ID = r"creq-[0-9a-f]{8}"


def _gate(message: str, fields: dict[str, str] | None = None):
    import domain
    return domain.GateError(message, fields=fields)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def section(state: dict[str, Any]) -> dict[str, Any]:
    """state["compliance"], with its parts, made on first use."""
    c = state.setdefault("compliance", {})
    c.setdefault("docs", [])
    c.setdefault("requirements", [])
    c.setdefault("liens", {})
    return c


def remind_days(state: dict[str, Any]) -> list[int]:
    saved = (state.get("compliance") or {}).get("remind_days")
    if isinstance(saved, list) and saved and all(isinstance(d, int) and not isinstance(d, bool) for d in saved):
        return sorted({d for d in saved if 0 < d <= MAX_REMIND_DAYS}, reverse=True) or list(DEFAULT_REMIND_DAYS)
    return list(DEFAULT_REMIND_DAYS)


# ---------- checking what a form sends ----------

def _text(body: dict[str, Any], key: str, limit: int = TEXT_MAX) -> str:
    value = body.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _gate("That wasn't understood.", {key: "Enter text here."})
    value = " ".join(value.split()) if limit <= TEXT_MAX else value.strip()
    if len(value) > limit:
        raise _gate(f"Keep it under {limit} characters.", {key: f"Keep it under {limit} characters."})
    return value


def _date(body: dict[str, Any], key: str, label: str) -> str:
    raw = _text(body, key, 10)
    if not raw:
        return ""
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        raise _gate(f"Enter {label} as a date.", {key: f"Enter {label} as a date."})
    if not 1990 <= day.year <= 2100:
        raise _gate(f"Enter {label} between 1990 and 2100.", {key: f"Enter {label} between 1990 and 2100."})
    return day.isoformat()


def no_tax_ids(value: str, key: str) -> None:
    """Refuse a Social Security or employer ID number, or a field that says it is one."""
    if _TAX_ID.search(value) or _TAX_WORDS.search(value):
        raise _gate("Don't enter Social Security or tax ID numbers. Upload the W-9 file instead; Duracash keeps no tax IDs.",
                    {key: "Don't enter Social Security or tax ID numbers here."})


def _payer_id(state: dict[str, Any], body: dict[str, Any], required: bool = False) -> str:
    pid = _text(body, "payer_id")
    if not pid:
        if required:
            raise _gate("Pick the client.", {"payer_id": "Pick the client."})
        return ""
    if not any(p["id"] == pid for p in state.get("payers", [])):
        raise _gate("Client not found.", {"payer_id": "Client not found."})
    return pid


def _job_id(state: dict[str, Any], body: dict[str, Any], payer_id: str = "") -> str:
    jid = _text(body, "job_id")
    if not jid:
        return ""
    job = next((j for j in state.get("jobs", []) if j["id"] == jid), None)
    if not job:
        raise _gate("Job not found.", {"job_id": "Job not found."})
    if payer_id and job.get("payer_id") != payer_id:
        raise _gate("That job is for another client.", {"job_id": "That job is for another client."})
    return jid


def _type(body: dict[str, Any]) -> str:
    kind = _text(body, "type")
    if kind not in DOC_TYPES:
        raise _gate("Pick the kind of document.", {"type": "Pick the kind of document."})
    return kind


def _audit(state: dict[str, Any], job_id: str, action: str, detail: str, actor: str) -> None:
    import domain
    domain.audit(state, job_id, action, detail, actor)


# ---------- documents ----------

def _doc_fields(state: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    kind = _type(body)
    spec = DOC_TYPES[kind]
    out = {"type": kind, "holder": _text(body, "holder"), "issuer": _text(body, "issuer"), "number": _text(body, "number", 60),
           "effective_on": _date(body, "effective_on", "the effective date"), "expires_on": _date(body, "expires_on", "the expiry date"),
           "note": _text(body, "note", NOTE_MAX)}
    for key in ("holder", "issuer", "number", "note"):
        no_tax_ids(out[key], key)
    if spec.get("no_number") and out["number"]:
        raise _gate(f"A {spec['label']} keeps no number here. Upload its file.", {"number": "Leave this blank; upload the file."})
    if out["effective_on"] and out["expires_on"] and out["expires_on"] < out["effective_on"]:
        raise _gate("The expiry date is before the effective date.", {"expires_on": "Enter an expiry date on or after the effective date."})
    out["payer_id"] = _payer_id(state, body)
    out["job_id"] = _job_id(state, body, out["payer_id"])
    if out["job_id"] and not out["payer_id"]:
        out["payer_id"] = next(j.get("payer_id", "") for j in state["jobs"] if j["id"] == out["job_id"])
    out["additional_insured"] = body.get("additional_insured") is True and kind == "coi"
    if out["additional_insured"] and not out["payer_id"]:
        raise _gate("Pick the client named as additional insured.", {"payer_id": "Pick the client named as additional insured."})
    return out


def find_doc(state: dict[str, Any], doc_id: str) -> dict[str, Any]:
    doc = next((d for d in section(state)["docs"] if d["id"] == doc_id), None)
    if not doc:
        raise _gate("Document not found.")
    return doc


def add_doc(state: dict[str, Any], body: dict[str, Any], actor: str) -> dict[str, Any]:
    c = section(state)
    if len(c["docs"]) >= MAX_DOCS:
        raise _gate(f"Up to {MAX_DOCS} documents. Delete old ones first.")
    doc = {"id": "cdoc-" + secrets.token_hex(4), **_doc_fields(state, body), "file_id": "", "added_at": _now(), "added_by": actor}
    c["docs"].append(doc)
    _audit(state, doc["job_id"], "compliance_doc_add", f"{actor} added {doc_words(state, doc)}.", actor)
    return doc


def update_doc(state: dict[str, Any], doc_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
    doc = find_doc(state, doc_id)
    fields = _doc_fields(state, {**{k: doc.get(k) for k in ("type",)}, **body})
    if fields["type"] != doc["type"] and doc.get("file_id") and DOC_TYPES[doc["type"]]["sensitive"] != DOC_TYPES[fields["type"]]["sensitive"]:
        raise _gate("Delete this document and add it again to change it to that kind.", {"type": "Delete it and add it again as that kind."})
    doc.update(fields)
    doc["updated_at"], doc["updated_by"] = _now(), actor
    _audit(state, doc["job_id"], "compliance_doc_update", f"{actor} updated {doc_words(state, doc)}.", actor)
    return doc


def delete_doc(state: dict[str, Any], doc_id: str, actor: str) -> list[dict[str, Any]]:
    """Take the document off, and its file off the company's files. Returns the file metas to
    delete from disk once the change is saved."""
    doc = find_doc(state, doc_id)
    c = section(state)
    c["docs"] = [d for d in c["docs"] if d["id"] != doc_id]
    gone = [m for m in state.get("files", []) if m["id"] == doc.get("file_id")]
    if gone:
        state["files"] = [m for m in state["files"] if m["id"] != doc["file_id"]]
    _audit(state, doc.get("job_id", ""), "compliance_doc_delete", f"{actor} deleted {doc_words(state, doc)}{' and its file' if gone else ''}.", actor)
    return gone


def attach_file(state: dict[str, Any], doc_id: str, meta: dict[str, Any], actor: str) -> list[dict[str, Any]]:
    """Register an uploaded file (importer.store_bytes) as the document's file, replacing the
    one it had. Returns the replaced file metas, to delete from disk once saved."""
    import importer
    doc = find_doc(state, doc_id)
    spec = DOC_TYPES[doc["type"]]
    meta = {**meta, "kind": spec["file_kind"], "compliance_doc_id": doc_id}
    if spec["sensitive"]:
        meta["sensitive"] = True
    old = [m for m in state.get("files", []) if m["id"] == doc.get("file_id")]
    if old:
        state["files"] = [m for m in state["files"] if m["id"] != doc["file_id"]]
    importer.register_file(state, meta)
    doc["file_id"] = meta["id"]
    _audit(state, doc.get("job_id", ""), "compliance_doc_file", f"{actor} uploaded the file for {doc_words(state, doc)}.", actor)
    return old


def is_sensitive_file(state: dict[str, Any], file_id: str) -> bool:
    """A W-9 or certified payroll's file: it takes "See payroll and sensitive documents" to open,
    like payroll on a job record (domain.profiles.sensitive_file_ids, which reads SENSITIVE_TYPES)."""
    from domain import profiles
    meta = next((m for m in state.get("files", []) if m["id"] == file_id), None)
    return bool(meta and (meta.get("sensitive") or file_id in profiles.sensitive_file_ids(state)))


def doc_words(state: dict[str, Any], doc: dict[str, Any]) -> str:
    label = DOC_TYPES.get(doc.get("type"), DOC_TYPES["other"])["label"]
    payer = next((p.get("name", "") for p in state.get("payers", []) if p["id"] == doc.get("payer_id")), "")
    return f"the {label}{' for ' + payer if payer else ''}"


def doc_status(doc: dict[str, Any], today: date, warn_days: int) -> str:
    """current, expiring (within the first reminder day), expired, or no_expiry."""
    exp = liens._day(doc.get("expires_on"))
    if not exp:
        return "no_expiry"
    if exp < today:
        return "expired"
    return "expiring" if (exp - today).days <= warn_days else "current"


def _replaced(docs: list[dict[str, Any]]) -> set[str]:
    """Documents a later one of the same kind, client and job takes over from (a renewed COI):
    no reminders for the old one."""
    latest: dict[tuple, dict[str, Any]] = {}
    for d in docs:
        key = (d.get("type"), d.get("payer_id", ""), d.get("job_id", ""), d.get("additional_insured", False))
        best = latest.get(key)
        if not best or (d.get("expires_on") or "9999", d.get("added_at", "")) > (best.get("expires_on") or "9999", best.get("added_at", "")):
            latest[key] = d
    return {d["id"] for d in docs if d["type"] not in {"payroll", "other"}} - {d["id"] for d in latest.values()}


# ---------- requirements ----------

def add_requirement(state: dict[str, Any], body: dict[str, Any], actor: str) -> dict[str, Any]:
    c = section(state)
    if len(c["requirements"]) >= MAX_REQUIREMENTS:
        raise _gate(f"Up to {MAX_REQUIREMENTS} requirements.")
    kind = _type(body)
    payer_id = _payer_id(state, body, required=True)
    req = {"id": "creq-" + secrets.token_hex(4), "type": kind, "payer_id": payer_id, "job_id": _job_id(state, body, payer_id),
           "additional_insured": body.get("additional_insured") is True and kind == "coi", "note": _text(body, "note", NOTE_MAX),
           "added_at": _now(), "added_by": actor}
    no_tax_ids(req["note"], "note")
    c["requirements"].append(req)
    _audit(state, req["job_id"], "compliance_requirement_add", f"{actor} noted that {_payer_name(state, payer_id)} needs a {DOC_TYPES[kind]['label']}"
           f"{' naming them as additional insured' if req['additional_insured'] else ''}.", actor)
    return req


def delete_requirement(state: dict[str, Any], req_id: str, actor: str) -> None:
    c = section(state)
    req = next((r for r in c["requirements"] if r["id"] == req_id), None)
    if not req:
        raise _gate("Requirement not found.")
    c["requirements"] = [r for r in c["requirements"] if r["id"] != req_id]
    _audit(state, req.get("job_id", ""), "compliance_requirement_delete",
           f"{actor} took off {_payer_name(state, req['payer_id'])}'s need for a {DOC_TYPES[req['type']]['label']}.", actor)


def _payer_name(state: dict[str, Any], payer_id: str) -> str:
    return next((p.get("name", "") for p in state.get("payers", []) if p["id"] == payer_id), "") or "The client"


def meets(req: dict[str, Any], doc: dict[str, Any]) -> bool:
    """Whether a document is the kind a requirement asks for, for that client and job. A COI
    naming the client as additional insured must be that client's own."""
    if doc.get("type") != req["type"]:
        return False
    if doc.get("job_id") and doc["job_id"] != req.get("job_id") and req.get("job_id"):
        return False
    if req.get("additional_insured"):
        return doc.get("payer_id") == req["payer_id"] and bool(doc.get("additional_insured"))
    return doc.get("payer_id", "") in {"", req["payer_id"]}


def requirement_status(state: dict[str, Any], req: dict[str, Any], today: date) -> tuple[str, dict[str, Any] | None]:
    """(met | expired | missing, the best document): met by a document not expired; expired
    when only expired ones fit."""
    fits = [d for d in section(state)["docs"] if meets(req, d)]
    live = [d for d in fits if not liens._day(d.get("expires_on")) or liens._day(d["expires_on"]) >= today]
    if live:
        return "met", max(live, key=lambda d: d.get("expires_on") or "9999")
    if fits:
        return "expired", max(fits, key=lambda d: d.get("expires_on") or "")
    return "missing", None


def _reqs_for_job(state: dict[str, Any], job: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in section(state)["requirements"] if r["payer_id"] == job.get("payer_id") and (not r.get("job_id") or r["job_id"] == job["id"])]


def payapp_warnings(state: dict[str, Any], job: dict[str, Any], today: date) -> list[str]:
    """The soft gate before a pay app goes: the client needs a certificate of insurance and the
    one on file has expired, or there is none. Warnings only; nothing is refused."""
    out = []
    for req in _reqs_for_job(state, job):
        if req["type"] != "coi":
            continue
        status, doc = requirement_status(state, req, today)
        who = _payer_name(state, req["payer_id"])
        named = " naming them as additional insured" if req.get("additional_insured") else ""
        if status == "expired":
            out.append(f"{who} needs a certificate of insurance{named}. The one on file expired {liens._day(doc['expires_on']):%b} {liens._day(doc['expires_on']).day}, {liens._day(doc['expires_on']).year}.")
        elif status == "missing":
            out.append(f"{who} needs a certificate of insurance{named}, and none is on file.")
    return out


# ---------- lien inputs ----------

def set_job_liens(state: dict[str, Any], job_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
    """Save a job's lien inputs. Blank dates clear; tier blank is "from the client's type"."""
    job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
    if not job:
        raise _gate("Job not found.")
    saved = section(state)["liens"].get(job_id) or {}
    out: dict[str, Any] = {}
    code = _text(body, "state").upper()
    if code and not re.fullmatch(r"[A-Z]{2}", code):
        raise _gate("Enter the two-letter state.", {"state": "Enter the two-letter state, like GA."})
    out["state"] = code
    tier = _text(body, "tier")
    if tier and tier not in liens.TIERS:
        raise _gate("Pick who hired you.", {"tier": "Pick who hired you."})
    out["tier"] = tier
    for key in liens.DATE_INPUTS:
        out[key] = _date(body, key, "the date")
    if out["first_furnished_on"] and out["last_furnished_on"] and out["last_furnished_on"] < out["first_furnished_on"]:
        raise _gate("The last day of work is before the first.", {"last_furnished_on": "Enter a day on or after the first day of work."})
    keys = {d["key"] for rules in liens.STATE_RULES.values() for d in rules["deadlines"]}
    done_in = body.get("done") if isinstance(body.get("done"), dict) else saved.get("done") or {}
    done = {}
    for key, value in done_in.items():
        if key in keys and value:
            done[key] = _date({key: value}, key, "the date it was done")
    out["done"] = done
    skipped_in = body.get("skipped") if isinstance(body.get("skipped"), list) else saved.get("skipped") or []
    out["skipped"] = sorted({k for k in skipped_in if isinstance(k, str) and k in keys})
    out["updated_at"], out["updated_by"] = _now(), actor
    section(state)["liens"][job_id] = out
    _audit(state, job_id, "lien_dates_update", f"{actor} updated the lien dates.", actor)
    return out


def _tier(state: dict[str, Any], job: dict[str, Any], saved: dict[str, Any]) -> tuple[str, str]:
    """(tier, where it came from: "saved" or "client")."""
    if saved.get("tier") in liens.TIERS:
        return saved["tier"], "saved"
    from domain import client_type_of
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    return liens.TIER_FROM_CLIENT.get(client_type_of(payer), "owner"), "client"


def _furnishing(state: dict[str, Any], job: dict[str, Any]) -> tuple[str, str, str]:
    """(first, last, last_from): the first and last days of work from the job's records. The
    last is the latest job record's day, else the day the work was marked done."""
    from domain import job_started_on, record_day
    # The day each record is for where the crew is (record_day): a US evening's record has a UTC
    # time on the next day, which would put the lien deadlines a day late.
    days = sorted(record_day(r) for r in state.get("daily_records", [])
                  if isinstance(r, dict) and r.get("job_id") == job["id"] and liens._day(record_day(r)))
    first = job_started_on(state, job) or (days[0] if days else "")
    if days:
        return first, days[-1], "job_record"
    if job.get("work_status") == "complete" and liens._day(job.get("completed_at")):
        return first, str(job["completed_at"])[:10], "work_done"
    return first, "", ""


def job_liens(state: dict[str, Any], job: dict[str, Any], today: date) -> dict[str, Any]:
    """One job's lien picture: its inputs (saved, else worked out), the state's rules and each
    deadline, and the Georgia waivers Duracash sent that are still on their 90 days."""
    from domain import job_money, job_state
    saved = (state.get("compliance") or {}).get("liens", {}).get(job["id"]) or {}
    code = saved.get("state") or job_state(job)
    rules = liens.rules_for(code)
    tier, tier_from = _tier(state, job, saved)
    first, last, last_from = _furnishing(state, job)
    inputs = {**{k: saved.get(k, "") for k in liens.DATE_INPUTS}, "tier": tier, "done": saved.get("done") or {}, "skipped": saved.get("skipped") or []}
    inputs["first_furnished_on"] = saved.get("first_furnished_on") or first
    inputs["last_furnished_on"] = saved.get("last_furnished_on") or last
    m = job_money(job)
    owed = (m["outstanding_cents"] + m["retainage_open_cents"] + m["unbilled_completed_cents"]) > 0
    out = {"job_id": job["id"], "job": job.get("name", ""), "payer_id": job.get("payer_id", ""), "state": code, "state_name": rules["name"] if rules else "",
           "supported": bool(rules), "statute": rules["statute"] if rules else "", "counting": rules["counting"] if rules else "",
           "tier": tier, "tier_from": tier_from, "saved": saved, "inputs": inputs,
           "first_from": "saved" if saved.get("first_furnished_on") else ("job" if first else ""),
           "last_from": "saved" if saved.get("last_furnished_on") else last_from,
           "owed": owed, "work_done": job.get("work_status") == "complete", "deadlines": [], "waiver_clocks": [],
           "waiver_note": rules.get("waiver_note", "") if rules else ""}
    if not rules:
        return out
    out["deadlines"] = liens.deadlines(rules, inputs, today, remind_days(state)[0], owed=owed, work_done=out["work_done"])
    if code == "GA":
        from domain import ga_waiver_clocks
        out["waiver_clocks"] = [{"what": c["what"], "sent_on": c["sent_on"].isoformat(), "takes_effect": c["takes_effect"].isoformat()}
                                for c in ga_waiver_clocks(state, job) if c.get("takes_effect")]
    return out


# ---------- the page ----------

def view(state: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """GET /api/compliance: every document with its status, every requirement with whether it is
    met, the lien picture of each open job, and the pay app warnings by job."""
    today = today or datetime.now(timezone.utc).date()
    c = state.get("compliance") or {}
    days = remind_days(state)
    replaced = _replaced(c.get("docs") or [])
    docs = []
    for d in c.get("docs") or []:
        exp = liens._day(d.get("expires_on"))
        docs.append({**d, "type_label": DOC_TYPES.get(d.get("type"), DOC_TYPES["other"])["label"],
                     "status": "replaced" if d["id"] in replaced else doc_status(d, today, days[0]),
                     "days_left": (exp - today).days if exp else None, "sensitive": d.get("type") in SENSITIVE_TYPES,
                     "payer": _payer_name(state, d["payer_id"]) if d.get("payer_id") else ""})
    reqs = []
    for r in c.get("requirements") or []:
        status, doc = requirement_status(state, r, today)
        reqs.append({**r, "type_label": DOC_TYPES[r["type"]]["label"], "status": status, "doc_id": doc["id"] if doc else "",
                     "payer": _payer_name(state, r["payer_id"])})
    open_jobs = [j for j in state.get("jobs", []) if not j.get("closed")]
    return {"types": [{"key": k, "label": v["label"], "sensitive": v["sensitive"], "expires": v["expires"], "no_number": bool(v.get("no_number"))} for k, v in DOC_TYPES.items()],
            "tiers": [[k, v] for k, v in liens.TIERS.items()], "states": sorted(liens.STATE_RULES), "remind_days": days,
            "docs": docs, "requirements": reqs, "jobs": [job_liens(state, j, today) for j in open_jobs],
            "warnings": {j["id"]: w for j in open_jobs for w in [payapp_warnings(state, j, today)] if w},
            "not_legal_advice": liens.NOT_LEGAL_ADVICE, "today": today.isoformat()}


def set_remind_days(state: dict[str, Any], body: dict[str, Any], actor: str) -> list[int]:
    raw = body.get("remind_days")
    if not isinstance(raw, list) or not raw or len(raw) > 5:
        raise _gate("Enter one to five numbers of days.", {"remind_days": "Enter one to five numbers of days, like 30, 14, 3."})
    days = set()
    for v in raw:
        if isinstance(v, bool) or not (isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit())):
            raise _gate("Enter whole numbers of days.", {"remind_days": "Enter whole numbers of days, like 30, 14, 3."})
        n = int(v)
        if not 1 <= n <= MAX_REMIND_DAYS:
            raise _gate(f"Enter days from 1 to {MAX_REMIND_DAYS}.", {"remind_days": f"Enter days from 1 to {MAX_REMIND_DAYS}."})
        days.add(n)
    section(state)["remind_days"] = sorted(days, reverse=True)
    _audit(state, "", "compliance_settings", f"{actor} set compliance reminders to {', '.join(map(str, sorted(days, reverse=True)))} days before.", actor)
    return section(state)["remind_days"]


# ---------- reminders (reminders.py, kind "compliance") ----------

PATH = "/jobs/compliance"


def reminder_items(state: dict[str, Any], today: date, jobs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """What is worth a reminder today: {kind, key, job_id, job, text, path, assignee_id}. A
    key changes at each reminder day, so 30, 14 and 3 days before each come round once more."""
    c = state.get("compliance") or {}
    days = remind_days(state)
    replaced = _replaced(c.get("docs") or [])
    out = []

    def item(key: str, text: str, job: dict[str, Any] | None = None) -> None:
        out.append({"kind": "compliance", "key": f"compliance:{key}", "job_id": job["id"] if job else "", "job": job.get("name", "") if job else "Company documents",
                    "text": text, "path": PATH, "assignee_id": (job or {}).get("assignee_id") or ""})

    for d in c.get("docs") or []:
        exp = liens._day(d.get("expires_on"))
        if not exp or d["id"] in replaced or not DOC_TYPES.get(d.get("type"), {}).get("expires", True):
            continue
        left = (exp - today).days
        what = doc_words(state, d)
        what = what[0].upper() + what[1:]
        if left < 0:
            item(f"doc:{d['id']}:expired", f"{what} expired {exp:%b} {exp.day}. Upload the new one.")
        else:
            step = min((n for n in days if left <= n), default=None)
            if step is not None:
                item(f"doc:{d['id']}:{step}", f"{what} expires {exp:%b} {exp.day} ({left} day{'' if left == 1 else 's'}). Ask for the renewal.")
    open_payers = {j.get("payer_id") for j in jobs.values()}
    for r in c.get("requirements") or []:
        if r["payer_id"] not in open_payers or (r.get("job_id") and r["job_id"] not in jobs):
            continue
        status, _ = requirement_status(state, r, today)
        if status != "met":
            job = jobs.get(r.get("job_id")) if r.get("job_id") else None
            named = " naming them as additional insured" if r.get("additional_insured") else ""
            item(f"req:{r['id']}:{status}", f"{_payer_name(state, r['payer_id'])} needs a {DOC_TYPES[r['type']]['label']}{named}; "
                 f"{'the one on file has expired' if status == 'expired' else 'none is on file'}.", job)
    for job in jobs.values():
        picture = job_liens(state, job, today)
        for row in picture["deadlines"]:
            # An optional step (the preliminary notice) shows on the page but doesn't nag.
            if row["status"] != "due_soon" or row["optional"] or (row["key"] == "lien_claim" and not picture["owed"]):
                continue
            step = min((n for n in days if row["days_left"] <= n), default=days[-1])
            due = liens._day(row["file_by"])
            item(f"lien:{job['id']}:{row['key']}:{step}", f"{row['label']}: file by {due:%b} {due.day} ({row['cite']}). Not legal advice; confirm with your attorney.", job)
    return out
