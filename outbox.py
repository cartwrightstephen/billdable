"""Delivering the Outbox: approved emails go out after the change that queued them is saved.

Moved out of server.py (R6-08). The server hands in `site`, the few things this needs from it,
looked up when called: lock(company_id), load(company_id), save(company_id, state),
inbound(company_id) for the Reply-To inbound address, and log(level, **fields).

An email moves queued -> sending -> sent, or to "failed: ..." / "not sent: ..." with the reason.
The company lock is held only to read and write the log; the mail server is talked to outside it,
so a slow provider never holds up the company's other requests.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timedelta, timezone

import auth
import domain
import importer
import mailer
import payapp_pdf

STUCK_MINUTES = 30  # an email still "sending" after this long died mid-send and can be retried


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
            return domain.light_snapshot(state)
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
        site.save(company_id, state)
        if not work:
            return domain.light_snapshot(state)
    # Outside the lock: read attachments and talk to the mail server.
    outcome: dict = {}
    try:
        messages = []
        for entry, attach in work:
            try:
                msg, left_off = mailer.build_message(entry, company_name, entry.get("reply_to", ""), attach, cfg, inbound=inbound)
                messages.append((entry["id"], msg, left_off))
            except Exception as exc:  # a bad address or file fails this email only
                outcome[entry["id"]] = (f"failed: {exc}"[:300], [], [], "")
        results = mailer.send([m for _, m, _ in messages], cfg)
        for (entry_id, msg, left_off), error in zip(messages, results):
            outcome[entry_id] = (f"failed: {error}" if error else "sent", left_off, getattr(msg, "refused", None) or [], str(msg["Message-ID"] or ""))
    except Exception as exc:
        # One JSON line like every other server error (R6-09), never a bare traceback on stderr.
        site.log("error", msg="email delivery failed", rid=rid, company=company_id, trace=traceback.format_exc())
        for entry, _ in work:
            outcome.setdefault(entry["id"], (f"failed: unexpected error ({exc.__class__.__name__}); see the server log", [], [], ""))
    sent = sum(1 for status, *_ in outcome.values() if status == "sent")
    mailer.record_sent(company_id, sent)
    done_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with site.lock(company_id):
        state = site.load(company_id)
        for e in state.get("email_log", []):
            if e["id"] in outcome and e.get("status") == "sending":
                status, left_off, refused, message_id = outcome[e["id"]]
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
                else:
                    e["failed_at"] = done_at
        site.save(company_id, state)
        return domain.light_snapshot(state)


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
        entry["status"] = "queued"
        entry["retried"] = entry.get("retried", 0) + 1
        site.save(company_id, state)
        payload = domain.light_snapshot(state)
    # Delivery happens after the lock is released.
    return 200, deliver_queued(site, company_id, user, {email_id}, rid) or payload
