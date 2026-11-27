"""Job rules from Stephen's Round 2 decisions (10 Nov 2026): a change order without a price
(decision 2), the override Finish job needs while closeout documents are missing (decision 4),
and named people who approve a pay app before it goes (decision 8).

Change orders: one saved with no price has co["unpriced"] = True and amount 0, so it adds
nothing to the contract, the pay apps or the money figures until someone who can manage jobs
gives it a price (price_change_order). It still collects hours from New job records and the
tickets and invoices tied to it (co_accrued).

Approvers: company["payapp_approvers"] = [{id, name}], and a client may have its own list
(payer["payapp_approvers"], which replaces the company's). Since Stephen's answers of 18 Nov no
pay app goes without approval: with no list named, every owner of the company approves
(state["team_owners"], which the server fills from the team each time it loads the company),
and a client list saved empty means its pay apps can't be approved or sent. Approving is
signing: each approval is kept on the draft as {id, name, signed_name, title, at, stamp,
consent, ip, user_agent}; stamp is approval_stamp of the pay app when it was signed, so a
signature given before the pay app changed no longer counts. Sending keeps the signatures that
counted on the pay app as it went (approvals_frozen_at), and its PDF prints them.

Imported at the bottom of domain/__init__.py (from .jobrules import *). Helpers from domain
are imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "co_needs", "co_accrued", "co_view_extras", "co_info_rows", "price_change_order", "need_price",
    "closeout_override_needed", "record_closeout_override",
    "APPROVE_PERM", "APPROVAL_CONSENT", "payapp_approvers", "set_payapp_approvers", "rename_approver", "approval_stamp", "approval_view",
    "approvals_missing", "approval_blocked", "approve_payapp", "clear_stale_approvals", "drop_approver", "drop_approvers_without",
    "set_team_owners", "freeze_approvals", "signed_approvals", "send_unapproved", "approval_way_out",
]


# ---------- change orders without a price (Stephen, 10 Nov, decision 2) ----------

def co_needs(co: dict[str, Any]) -> list[str]:
    """What a pending change order still needs before it can be signed or billed, in words."""
    if co.get("status") != "pending":
        return []
    return ["a price"] if co.get("unpriced") else []


def need_price(co: dict[str, Any]) -> None:
    """Refuse signing or billing a change order with no price: what the client agrees to and
    what is billed is a price, and a blank one would go out as $0 (decision 2)."""
    from domain import GateError
    if co.get("unpriced"):
        raise GateError(f"CO {co['number']} has no price yet. Add the price first.")


def co_accrued(state: dict[str, Any], job: dict[str, Any], co: dict[str, Any]) -> dict[str, Any]:
    """The hours and backup gathered for a change order from the job records: Change order work
    rows on New job records that name it, and records tied to it (tickets, time sheets, supplier
    invoices: link_record_to_work). Those picked as its backup are on co["support_record_ids"]."""
    daily = {d["id"]: d for d in state.get("daily_records", []) if d.get("job_id") == job["id"]}
    entries = []
    for rid in job.get("daily_records", []) or []:
        rec = daily.get(rid)
        if not rec:
            continue
        for row in (rec.get("sections") or {}).get("co_work", []):
            if row.get("co_id") == co["id"]:
                entries.append({"at": rec.get("at", ""), "by": rec.get("actor", ""), "description": row.get("description", ""),
                                "hours_milli": int(row.get("hours_milli") or 0)})
    linked = [r["id"] for r in job.get("records", []) if r.get("co_id") == co["id"]]
    return {"hours_milli": sum(e["hours_milli"] for e in entries), "entries": entries, "record_ids": linked}


def co_view_extras(state: dict[str, Any], job: dict[str, Any], out: dict[str, Any]) -> None:
    """job_view's change orders: what each still needs and what it has gathered."""
    stored = {co["id"]: co for co in job.get("change_orders", [])}
    for co in out.get("change_orders", []):
        co["needs"] = co_needs(co)
        if co["id"] in stored:
            co["accrued"] = co_accrued(state, job, stored[co["id"]])


def co_info_rows(job: dict[str, Any]) -> list[dict[str, Any]]:
    """A To Do row for each change order still missing something, for whoever can manage jobs
    (decision 2: "a To Do for the Owner and Office personnel"). today_rows adds them."""
    out = []
    for co in job.get("change_orders", []):
        needs = co_needs(co)
        if needs:
            out.append({"co_id": co["id"], "number": co["number"],
                        "button": f"Add the price to CO {co['number']}",   # jobs.js coPriceLabel, the job page's task
                        "text": f"CO {co['number']} {co['title']} needs {' and '.join(needs)} before it can be signed or billed."})
    return out


def price_change_order(state: dict[str, Any], job_id: str, co_id: str, amount_cents: Any, items: Any = None,
                       actor: str = "owner") -> dict[str, Any]:
    """Give a change order saved without a price its price: the amount, or cost lines that add
    up to it, checked as New change order checks them."""
    from domain import (GateError, MAX_AMOUNT_CENTS, _co_items, _open_job, _refuse, audit, co_event, money)
    job = _open_job(state, job_id)
    co = next((c for c in job["change_orders"] if c["id"] == co_id), None)
    if not co:
        raise GateError("Change order not found.")
    if co["status"] != "pending" or not co.get("unpriced"):
        raise GateError(f"CO {co['number']} already has its price.")
    errors: dict[str, str] = {}
    lines = _co_items(items, errors)
    amount = amount_cents if isinstance(amount_cents, int) and not isinstance(amount_cents, bool) else 0
    if lines and not errors:
        total = sum(line["amount_cents"] for line in lines)
        if amount not in (0, total):
            errors["amount_cents"] = f"The cost lines add up to {money(total)}. Make the amount match, or leave it blank."
        amount = total
    if "amount_cents" in errors or any(k.startswith("item_") or k == "items" for k in errors):
        pass
    elif amount == 0:
        errors["amount_cents"] = "Enter the change order amount, like 1,250, or -1,250 for a credit."
    elif abs(amount) > MAX_AMOUNT_CENTS:
        errors["amount_cents"] = f"Enter a change order amount up to {money(MAX_AMOUNT_CENTS)}."
    elif amount < 0 and job["contract_cents"] + sum(c["amount_cents"] for c in job["change_orders"] if c["status"] == "approved") + amount < 0:
        errors["amount_cents"] = "Enter a credit no larger than the contract total."
    _refuse("Price not saved.", errors)
    co["amount_cents"] = amount
    if lines:
        co["items"] = lines
    co.pop("unpriced", None)
    what = f"a credit of {money(-amount)}" if amount < 0 else money(amount)
    co["priced_at"] = co_event(co, "priced", actor, note=what)
    co["priced_by"] = actor
    audit(state, job_id, "price_co", f"{actor} priced CO {co['number']} — {co['title']} at {what}.", actor)
    return job


# ---------- Finish job with closeout documents missing (Stephen, 10 Nov, decision 4) ----------

def closeout_override_needed(job: dict[str, Any]) -> list[str]:
    """The closeout documents still missing, which Finish job now needs an override for."""
    from domain import closeout_missing
    return [item["label"] for item in closeout_missing(job)]


def record_closeout_override(state: dict[str, Any], job: dict[str, Any], reason: Any, actor: str) -> None:
    """"Missing closeout docs should require override, when user overrides a blocker like that it
    should also be recorded" (decision 4): like "I have this", who, when and why, on the job and
    in the audit log. Refused without a reason, naming what is missing."""
    from domain import GateError, _reason, audit, utcnow
    missing = closeout_override_needed(job)
    if not missing:
        # A clean finish: an override from an earlier finish (before a reopen) no longer speaks
        # for this one. The audit log keeps it.
        job.pop("closeout_override", None)
        return
    why = _reason(reason)
    if not why:
        raise GateError(f"Closeout documents are missing: {', '.join(missing)}. Add them, or give the reason to finish without them.",
                        missing, fields={"closeout_override": "Enter why the job can finish without them."},
                        extra={"closeout_override": True})
    job["closeout_override"] = {"by": actor, "at": utcnow(), "reason": why, "missing": missing}
    audit(state, job["id"], "closeout_override", f"{actor} finished {job['name']} without closeout documents ({', '.join(missing)}): {why}.", actor)


# ---------- pay app approvers (Stephen, 10 Nov, decision 8) ----------

# Approving is its own permission, not "Send pay apps": the people who check a pay app (an owner,
# a project manager) are often not the ones who send it, and sending shouldn't let someone count
# as having checked it. Only holders can be named, and only they can approve.
APPROVE_PERM = "approve_payapps"


def _people(raw: Any) -> list[dict[str, str]]:
    return [{"id": str(p.get("id") or ""), "name": str(p.get("name") or "")} for p in raw or [] if isinstance(p, dict) and p.get("id")]


# What an approver agrees to by signing (kept with each signature, as CO_CONSENT is with a client's).
APPROVAL_CONSENT = "I have checked this pay app and approve it to go to the client. My typed name is my signature."


def set_team_owners(state: dict[str, Any], owners: Any) -> None:
    """The company's owners, [{id, name}], from the team (auth). The server sets it each time it
    loads the company, so it is never older than the request: they approve when no list is named."""
    state["team_owners"] = _people(owners)


def _approver_source(state: dict[str, Any], job: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    if "payapp_approvers" in payer:
        return "client", _people(payer["payapp_approvers"])
    company = _people(state.get("company", {}).get("payapp_approvers"))
    if company:
        return "company", company
    # "Unapproved pay applications should never send" (Stephen, 18 Nov): with nobody named, every
    # owner approves (Yusuf's default, 18 Nov). A company with one owner signs, then sends.
    return "owners", _people(state.get("team_owners"))


def payapp_approvers(state: dict[str, Any], job: dict[str, Any]) -> list[dict[str, str]]:
    """Who must approve this job's pay apps: the client's own list when it has one, else the
    company's, else every owner of the company."""
    return _approver_source(state, job)[1]


def approval_blocked(state: dict[str, Any], job: dict[str, Any]) -> str:
    """Why nobody can approve this job's pay apps, or "". "If a client's approver list is empty,
    then the pay app cannot be approved and be sent out" (Stephen, 18 Nov, answer 2)."""
    source, people = _approver_source(state, job)
    if people:
        return ""
    if source == "client":
        payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
        return f"Nobody is named to approve {payer.get('name') or 'this client'}'s pay apps, so they can't be sent. Name approvers in Client playbook."
    return "Nobody can approve pay apps yet. Name approvers in Settings, Company."


def approval_way_out(state: dict[str, Any], job: dict[str, Any]) -> dict[str, str]:
    """The link a refusal for nobody to approve carries: where approvers are named."""
    if _approver_source(state, job)[0] == "client":
        return {"label": "Open Client playbook", "view": "playbook"}
    return {"label": "Open Settings, Company", "view": "company"}


def set_payapp_approvers(state: dict[str, Any], user_ids: Any, pool: dict[str, str], payer_id: str = "",
                         same_as_company: bool = False, actor: str = "owner") -> list[dict[str, str]]:
    """Name the people who approve pay apps: for the company, or for one client (which replaces
    the company's list; same_as_company takes the client's own list off). pool is {user id: name}
    of the team members who hold the approve permission; anyone else is refused."""
    from domain import GateError, _refuse, audit
    ids = user_ids if isinstance(user_ids, list) else []
    ids = list(dict.fromkeys(str(i) for i in ids if i))
    if any(i not in pool for i in ids):
        _refuse("", {"approvers": "Pick people whose permissions include Approve pay apps."})
    people = [{"id": i, "name": pool[i]} for i in ids]
    names = ", ".join(p["name"] for p in people) or "nobody"
    if payer_id:
        payer = next((p for p in state.get("payers", []) if p["id"] == payer_id), None)
        if not payer:
            raise GateError("Client not found.")
        if same_as_company:
            payer.pop("payapp_approvers", None)
            audit(state, "", "approvers_set", f"{actor} set {payer['name']}'s pay apps to the company's approvers.", actor)
            return payapp_approvers(state, {"payer_id": payer_id})
        payer["payapp_approvers"] = people
        audit(state, "", "approvers_set", f"{actor} set who approves {payer['name']}'s pay apps: {names}.", actor)
        return people
    state.setdefault("company", {})["payapp_approvers"] = people
    audit(state, "", "approvers_set", f"{actor} set who approves pay apps: {names}.", actor)
    return people


def drop_approver(state: dict[str, Any], user_id: str, why: str = "left the team") -> None:
    """Someone left the team, or no longer holds Approve pay apps: they come off every approvers
    list, so no pay app waits on them. A client's own list they emptied comes off too, so the
    company's applies again: an empty list there reads as "this client's pay apps can't be sent",
    which no one chose. A list an owner saved empty on purpose has no one to remove and stays as
    it is. The company's list emptied this way falls back to the owners."""
    from domain import audit
    company = state.get("company", {})
    for holder in [company, *state.get("payers", [])]:
        if "payapp_approvers" not in holder:
            continue
        before = _people(holder["payapp_approvers"])
        gone = [p for p in before if p["id"] == user_id]
        if not gone:
            continue
        kept = [p for p in before if p["id"] != user_id]
        whose = "pay apps" if holder is company else f"{holder['name']}'s pay apps"
        if holder is not company and not kept:
            holder.pop("payapp_approvers")
            audit(state, "", "approvers_set", f"{gone[0]['name']} {why} and no longer approves {whose}. "
                  "No one is left on that list, so the company's approvers apply.", "duracash")
            continue
        holder["payapp_approvers"] = kept
        audit(state, "", "approvers_set", f"{gone[0]['name']} {why} and no longer approves {whose}.", "duracash")


def rename_approver(state: dict[str, Any], user_id: str, name: str) -> bool:
    """Someone on an approvers list changed their name (My account): the lists say the new one,
    so a pay app doesn't wait on a name nobody on the team has (B25). True when one changed."""
    changed = False
    for holder in [state.get("company", {}), *state.get("payers", [])]:
        for p in holder.get("payapp_approvers") or []:
            if isinstance(p, dict) and p.get("id") == user_id and p.get("name") != name:
                p["name"] = name
                changed = True
    return changed


def drop_approvers_without(state: dict[str, Any], pool: dict[str, str]) -> None:
    """After a profile change or a permissions edit: anyone named who no longer holds Approve pay
    apps (pool is {user id: name} of those who do) comes off the lists (decision 8)."""
    company = state.get("company", {})
    named = {p["id"] for holder in [company, *state.get("payers", [])] for p in _people(holder.get("payapp_approvers"))}
    for user_id in sorted(named - set(pool)):
        drop_approver(state, user_id, why="no longer holds Approve pay apps")


def approval_stamp(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> str:
    """What an approval was given to: what Review & send shows (amount and files going), the
    lines and the period. A change to any of them clears the approval."""
    from domain import review_stamp
    what = [review_stamp(state, job, invoice), json.dumps(invoice.get("lines") or [], sort_keys=True, default=str),
            str(invoice.get("period_to") or ""), str(invoice.get("retainage_cents") or 0)]
    return hashlib.sha256("|".join(what).encode()).hexdigest()[:16]


def _valid(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stamp = approval_stamp(state, job, invoice)
    return {a["id"]: a for a in invoice.get("approvals") or [] if a.get("stamp") == stamp}


def _shown(sig: dict[str, Any] | None) -> dict[str, str]:
    """A signature as the pages show it: not the network address or browser kept with it."""
    sig = sig or {}
    return {"approved_at": sig.get("at", ""), "signed_name": sig.get("signed_name") or "", "title": sig.get("title") or ""}


def approval_view(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> dict[str, Any]:
    """For Review & send: each approver, whether they have signed this version, and why nobody
    can when nobody is named for the client (blocked)."""
    valid = _valid(state, job, invoice)
    source, people = _approver_source(state, job)
    required = [{"id": p["id"], "name": p["name"], **_shown(valid.get(p["id"]))} for p in people]
    # stamp: the version shown, which the Sign button sends back (approve_payapp's seen).
    return {"required": required, "waiting": [p["name"] for p in required if not p["approved_at"]],
            "stamp": approval_stamp(state, job, invoice), "source": source,
            "blocked": approval_blocked(state, job), "consent": APPROVAL_CONSENT}


def approvals_missing(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> list[str]:
    return approval_view(state, job, invoice)["waiting"]


def _clean_name(raw: Any, limit: int = 120) -> str:
    return " ".join(str(raw or "").split())[:limit]


def approve_payapp(state: dict[str, Any], job_id: str, actor: str = "owner", actor_id: str = "",
                   invoice_id: str | None = None, seen: str | None = None, signature: Any = None, title: Any = "",
                   ip: str = "", user_agent: str = "") -> dict[str, Any]:
    """One approver signs the open draft, as the version they looked at: invoice_id and seen
    (approval_view's stamp) are what the page showed, and a draft changed or replaced since is
    refused, as Review & send refuses a stale send (B13). None is for callers with no page.
    Only once it is ready to send (awaiting_approval): a draft still missing paperwork will change
    before it goes. "The approval chain ... involve[s] application of a signature to a document"
    (Stephen, 18 Nov, answer 1): the typed full name is the signature, as a client's on a change
    order (sign_change_order); title is optional and printed with it."""
    from domain import GateError, _open_job, audit, blocker_lines, utcnow, money
    job = _open_job(state, job_id)
    invoice = next((i for i in reversed(job["invoices"]) if i["status"] in {"draft", "awaiting_approval"}), None)
    if not invoice:
        raise GateError("No draft pay app to approve.")
    if (invoice_id is not None and invoice_id != invoice["id"]) or (seen is not None and seen != approval_stamp(state, job, invoice)):
        raise GateError(f"{invoice['number']} changed since you opened it. Look it over again, then sign.",
                        extra={"review_changed": True, "invoice_id": invoice["id"]})
    blocked = approval_blocked(state, job)
    if blocked:
        raise GateError(blocked, extra={"approvals_none": True}, action=approval_way_out(state, job))
    if not any(p["id"] == actor_id for p in payapp_approvers(state, job)):
        raise GateError(f"You aren't one of the people who approve {invoice['number']}.")
    # Ready to send is what Approve & send goes by: nothing on the job's blocker list.
    if invoice["status"] != "awaiting_approval" and blocker_lines(job):
        raise GateError(f"{invoice['number']} still needs its supporting documents. Approve it once it is ready to send.")
    if actor_id in _valid(state, job, invoice):
        raise GateError(f"You have already signed {invoice['number']}.")
    signed = _clean_name(signature)
    if len(signed) < 2:
        raise GateError("Type your full name to sign.", fields={"signature": "Type your full name to sign."})
    approvals = [a for a in invoice.get("approvals") or [] if a.get("id") != actor_id]
    approvals.append({"id": actor_id, "name": actor, "signed_name": signed, "title": _clean_name(title, 80), "at": utcnow(),
                      "stamp": approval_stamp(state, job, invoice), "consent": APPROVAL_CONSENT,
                      "ip": str(ip or "")[:64], "user_agent": str(user_agent or "")[:200]})
    invoice["approvals"] = approvals
    audit(state, job_id, "payapp_approved", f"{actor} signed {invoice['number']} for {money(invoice['amount_cents'])} to go (signed as {signed}).", actor,
          record=(f"approval:{job_id}:{invoice['id']}:{actor_id}", approvals[-1]))
    return job


def freeze_approvals(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> None:
    """Approve & send, at the moment it goes: the signatures that counted stay with the pay app as
    it was sent, in the order of the list, and nothing changes them after (signed_approvals)."""
    from domain import utcnow
    valid = _valid(state, job, invoice)
    invoice["approvals"] = [valid[p["id"]] for p in payapp_approvers(state, job) if p["id"] in valid]
    invoice["approvals_frozen_at"] = utcnow()


def signed_approvals(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> list[dict[str, str]]:
    """The signatures for the PDF's Approved by block and the pay app's page: those it went with
    once sent, or, on a draft, those that count for it now."""
    if invoice.get("approvals_frozen_at"):
        sigs = invoice.get("approvals") or []
    elif invoice.get("status") in {"draft", "awaiting_approval"}:
        valid = _valid(state, job, invoice)
        sigs = [valid[p["id"]] for p in payapp_approvers(state, job) if p["id"] in valid]
    else:
        sigs = []
    return [{"name": a.get("name", ""), **_shown(a)} for a in sigs]


def send_unapproved(job: dict[str, Any] | None, email: dict[str, Any]) -> str:
    """Why a pay app's email can't go (the Outbox, and Send again), or "": "Unapproved pay
    applications should never send" (Stephen, 18 Nov, answer 2). A pay app goes with the
    signatures Approve & send kept; one marked sent without them (before 18 Nov) doesn't go now."""
    if email.get("kind") != "pay_app" or not email.get("payapp_invoice_id"):
        return ""
    inv = next((i for i in (job or {}).get("invoices", []) if i["id"] == email["payapp_invoice_id"]), None)
    if inv and inv.get("approvals_frozen_at") and inv.get("approvals"):
        return ""
    number = (inv or {}).get("number", "The pay app")
    return f"{number} has no approval on record."


def clear_stale_approvals(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> None:
    """"An approval is cleared if the pay app changes after it was given": take off the ones whose
    pay app has changed since, and say so in the audit log."""
    from domain import audit
    stamp = approval_stamp(state, job, invoice)
    stale = [a for a in invoice.get("approvals") or [] if a.get("stamp") != stamp]
    if not stale:
        return
    invoice["approvals"] = [a for a in invoice["approvals"] if a.get("stamp") == stamp]
    audit(state, job["id"], "payapp_approval_cleared",
          f"{invoice['number']} changed after {', '.join(a.get('name', '') for a in stale)} approved it. It needs approving again.", "duracash")
