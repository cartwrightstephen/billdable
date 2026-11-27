"""Delivering the Outbox: approved emails go out after the change that queued them is saved.

Moved out of server.py (R6-08). The server hands in `site`, the few things this needs from it,
looked up when called: lock(company_id), load(company_id), save(company_id, state),
inbound(company_id) for the Reply-To inbound address, and log(level, **fields).

An email moves queued -> sending -> sent, or to "failed: ..." / "not sent: ..." with the reason.
The company lock is held only to read and write the log; the mail server is talked to outside it,
so a slow provider never holds up the company's other requests.
"""

from __future__ import annotations

import secrets
import traceback
from datetime import datetime, timedelta, timezone

import auth
import delivery
import domain
import importer
import mailer
import payapp_pdf
from domain import profiles

STUCK_MINUTES = 30  # an email still "sending" after this long died mid-send and can be retried


def _snapshot(state: dict, user: dict | None) -> dict:
    """The reply, with only the To Do rows this person's permissions cover, as /api/state sends
    them (Round 2 decision 2: a change order to price is for those who can manage jobs)."""
    return domain.light_snapshot(state, perms=profiles.perms_of(user, state) if user else None)


def deliver_queued(site, company_id: str, user: dict, only: set | None = None, rid: str = "") -> dict | None:
    """Send this company's queued emails. Returns the new snapshot, or None if nothing was queued."""
    cfg = mailer.settings()
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(minutes=STUCK_MINUTES)).isoformat(timespec="seconds")
    with site.lock(company_id):
        state = site.load(company_id)
        log = state.get("email_log", [])
        queued = [e for e in log if e.get("status") == "queued" and (only is None or e["id"] in only)]
        if not queued:
            return None
        if company_id == auth.DEMO_COMPANY_ID or not mailer.configured() or not user.get("email_verified"):
            if company_id == auth.DEMO_COMPANY_ID:
                reason = mailer.DEMO
            elif not mailer.configured():
                reason = mailer.NOT_SET_UP
            else:
                # Replies go to the approver's address, so it must be theirs before anything goes out.
                reason = mailer.UNVERIFIED
            for e in queued:
                e["status"] = reason
            site.save(company_id, state)
            return _snapshot(state, user)
        # Off the allow-list (Decision 8): stays here, marked, and takes no room in the daily limit.
        for e in queued:
            if not mailer.allowed(e.get("to", "")):
                e["status"] = mailer.NOT_ALLOWED
        queued = [e for e in queued if e["status"] == "queued"]
        # Only recent "sending" entries are in flight; an older one died mid-send and can be retried.
        in_flight = sum(1 for e in log if e.get("status") == "sending" and (e.get("sending_at") or "") >= stale)
        room = max(0, cfg["daily_limit"] - mailer.sent_in_last_day(company_id) - in_flight)
        batch, over = queued[:room], queued[room:]
        for e in over:
            e["status"] = f"not sent: daily limit of {cfg['daily_limit']} emails reached; retry tomorrow"
        files = {f["id"]: f for f in state.get("files", [])}
        work = []
        for e in batch:
            e["status"] = "sending"
            e["sending_at"] = now.isoformat(timespec="seconds")
            # Reply-To is the person who approved the send, kept even if someone else retries it.
            e["reply_to"] = e.get("reply_to") or user.get("email", "")
            attach = [
                (files[fid]["name"], importer.file_path(company_id, files[fid]))
                for fid in e.get("attachments", [])
                if fid in files
            ]
            job_now = next((j for j in state.get("jobs", []) if j["id"] == e.get("job_id")), {})
            inv_now = next((i for i in job_now.get("invoices", []) if i["id"] == e.get("payapp_invoice_id")), None)
            if e.get("payapp_invoice_id") and inv_now and inv_now.get("status") == "void":
                # A pay app voided after this email was approved is never sent.
                e["status"] = "failed: the pay app was voided after this email was approved"
                e["failed_at"] = now.isoformat(timespec="seconds")
                continue
            unapproved = domain.send_unapproved(job_now, e)
            if unapproved:
                # "Unapproved pay applications should never send" (Stephen, 18 Nov), whatever queued it.
                e["status"] = f"not sent: {unapproved}"
                e["failed_at"] = now.isoformat(timespec="seconds")
                continue
            if e.get("payapp_invoice_id"):
                try:
                    pdf, name = payapp_pdf.for_invoice(state, e["job_id"], e["payapp_invoice_id"])
                except Exception:
                    site.log("error", msg="pay app PDF failed", rid=rid, trace=traceback.format_exc())
                    pdf, name = None, None
                if pdf:
                    attach.insert(0, (name, pdf))
                else:
                    e["status"] = "failed: the pay app PDF could not be made; see the server log"
                    e["failed_at"] = now.isoformat(timespec="seconds")
                    continue
            work.append((dict(e), attach))
        company_name = state.get("company", {}).get("name", "")
        inbound = site.inbound(company_id)
        # Open tracking (delivery.py): off unless an owner turns it on, and only with the site's
        # public address for the image. Each address then gets its own copy and its own image.
        base = _app_url(site) if state.get("company", {}).get("open_tracking") is True else None
        site.save(company_id, state)
        if not work:
            return _snapshot(state, user)
    # Outside the lock: read attachments and talk to the mail server.
    outcome: dict = {}
    tokens: dict = {}
    try:
        messages = []   # (entry id, message, left off, the one address it goes to or "")
        for entry, attach in work:
            try:
                if base:
                    msg, left_off = mailer.build_message(entry, company_name, entry.get("reply_to", ""), attach, cfg, inbound=inbound)
                    keys, copies = {}, []
                    for address in getattr(msg, "recipients", None) or []:
                        keys[address.lower()] = token = delivery.new_token()
                        one, _ = mailer.build_message(entry, company_name, entry.get("reply_to", ""), attach, cfg, inbound=inbound,
                                                      only_to=address, pixel_url=f"{base}/o/{company_id}.{token}.gif")
                        one.held_copies = getattr(msg, "held_copies", [])  # type: ignore[attr-defined]
                        copies.append((entry["id"], one, left_off, address))
                    tokens[entry["id"]] = keys
                    messages.extend(copies)
                else:
                    msg, left_off = mailer.build_message(entry, company_name, entry.get("reply_to", ""), attach, cfg, inbound=inbound)
                    messages.append((entry["id"], msg, left_off, ""))
            except Exception as exc:  # a bad address or file fails this email only
                outcome[entry["id"]] = (f"failed: {exc}"[:300], [], [], "", {})
        results = mailer.send([m for _, m, _, _ in messages], cfg)
        for (entry_id, msg, left_off, address), error in zip(messages, results):
            first = entry_id not in outcome
            status, _, refused, message_id, ids = outcome.get(entry_id, ("sent", left_off, [], "", {}))
            if address:
                ids = {**ids, address.lower(): str(msg["Message-ID"] or "")}
                to_copy = address.lower() == str(msg["To"] or "").lower()
                if to_copy:
                    status, message_id = (f"failed: {error}" if error else "sent"), str(msg["Message-ID"] or "")
                elif error:
                    refused = [*refused, f"{address} ({error})"]
                if first or to_copy:
                    refused = [*refused, *[r for r in (getattr(msg, "refused", None) or []) if r not in refused]]
            else:
                status, message_id = (f"failed: {error}" if error else "sent"), str(msg["Message-ID"] or "")
                refused = getattr(msg, "refused", None) or []
            outcome[entry_id] = (status, left_off, refused, message_id, ids)
    except Exception as exc:
        # One JSON line like every other server error (R6-09), never a bare traceback on stderr.
        site.log("error", msg="email delivery failed", rid=rid, company=company_id, trace=traceback.format_exc())
        for entry, _ in work:
            outcome.setdefault(entry["id"], (f"failed: unexpected error ({exc.__class__.__name__}); see the server log", [], [], "", {}))
    sent = sum(1 for status, *_ in outcome.values() if status == "sent")
    mailer.record_sent(company_id, sent)
    done_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with site.lock(company_id):
        state = site.load(company_id)
        for e in state.get("email_log", []):
            if e["id"] in outcome and e.get("status") == "sending":
                status, left_off, refused, message_id, ids = outcome[e["id"]]
                e["status"] = status
                if status == "sent":
                    e["delivered_at"] = done_at
                    # Its Message-ID, so a bounce from the mail provider finds this email (mail_events.py).
                    e["message_id"] = message_id
                    if left_off:
                        e["not_attached"] = left_off
                    if refused:
                        # A copy address the mail server refused; the To address got it.
                        e["refused"] = refused
                    # A line per address: sent, then delivered, opened or bounced (delivery.py).
                    delivery.start(e, done_at, refused, tokens.get(e["id"]), ids)
                else:
                    e["failed_at"] = done_at
        site.save(company_id, state)
        return _snapshot(state, user)


def _app_url(site) -> str | None:
    getter = getattr(site, "app_url", None)
    return getter() if callable(getter) else None


def retry_email(site, company_id: str, user: dict, email_id: str, rid: str = "") -> tuple:
    """Queue a failed, unsent or stuck email again and deliver it. Returns (status, reply)."""
    stale = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_MINUTES)).isoformat(timespec="seconds")
    with site.lock(company_id):
        state = site.load(company_id)
        entry = next((e for e in state.get("email_log", []) if e["id"] == email_id), None)
        if not entry:
            return 404, {"error": "Not found"}
        status = entry.get("status", "")
        stuck = status in {"sending", "queued"} and (entry.get("sending_at") or entry.get("at") or "") < stale
        if not (status.startswith("failed") or status.startswith("not sent") or stuck):
            return 409, {"error": f"This email is {status}; only a failed or unsent email can be sent again."}
        job = next((j for j in state.get("jobs", []) if j["id"] == entry.get("job_id")), None)
        moot = domain.email_moot(job, entry) if job else ""
        if moot:
            return 409, {"error": f"{moot} This email doesn't need to go out any more."}
        unapproved = domain.send_unapproved(job, entry)
        if unapproved:
            return 409, {"error": f"{unapproved} Unapproved pay apps aren't sent."}
        entry["status"] = "queued"
        entry["retried"] = entry.get("retried", 0) + 1
        site.save(company_id, state)
        payload = _snapshot(state, user)
    # Delivery happens after the lock is released.
    return 200, deliver_queued(site, company_id, user, {email_id}, rid) or payload


# Fields of an Outbox row that describe one send, not the email itself.
SEND_FIELDS = {"id", "at", "status", "audit_seq", "sending_at", "delivered_at", "failed_at", "bounced_at", "message_id",
               "not_attached", "refused", "recipients", "retried", "reply_to", "moot", "resend_of", "resent_to"}


def resend_to(site, company_id: str, user: dict, email_id: str, address: str, actor: str, rid: str = "") -> tuple:
    """Send an email again to one of its addresses only (Siteline comparison, item 5): a new
    Outbox row, the same subject, text and attachments (the pay app PDF made fresh, as on any
    send), to that address alone. Returns (status, reply)."""
    address = str(address or "").strip()
    with site.lock(company_id):
        state = site.load(company_id)
        entry = next((e for e in state.get("email_log", []) if e["id"] == email_id), None)
        if not entry:
            return 404, {"error": "Not found"}
        if not profiles.may_send_again(user, entry, state):   # the server checks first; this is under the lock
            return 403, {"error": "Your permissions don't cover that. Someone who manages the team can change them."}
        status = str(entry.get("status") or "")
        if status in {"queued", "sending"}:
            return 409, {"error": "This email is still going out. Wait for it, then send it again."}
        match = next((a for a, _ in delivery.addresses(entry) if a.lower() == address.lower()), None)
        if not match:
            return 409, {"error": "That address isn't on this email.", "fields": {"email": "Pick an address this email went to."}}
        job = next((j for j in state.get("jobs", []) if j["id"] == entry.get("job_id")), None)
        moot = domain.email_moot(job, entry) if job else ""
        if moot:
            return 409, {"error": f"{moot} This email doesn't need to go out any more."}
        unapproved = domain.send_unapproved(job, entry)
        if unapproved:
            return 409, {"error": f"{unapproved} Unapproved pay apps aren't sent."}
        again = {k: v for k, v in entry.items() if k not in SEND_FIELDS}
        again.update({"id": "eml-" + secrets.token_hex(5), "at": domain.utcnow(), "audit_seq": len(state.get("audit", [])),
                      "to": match, "cc": [], "approved_by": actor, "status": "queued", "resend_of": entry["id"]})
        state["email_log"].append(again)
        entry.setdefault("resent_to", []).append({"email": match, "at": again["at"], "by": actor, "id": again["id"]})
        domain.audit(state, entry.get("job_id", ""), "email_resent", f"{actor} sent \"{entry.get('subject', '')}\" again to {match}.", actor)
        site.save(company_id, state)
        payload = _snapshot(state, user)
    return 200, deliver_queued(site, company_id, user, {again["id"]}, rid) or payload
