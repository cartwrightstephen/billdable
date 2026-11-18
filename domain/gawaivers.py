"""Georgia's 90-day fuse on a lien waiver that went before its money (Georgia waivers, Nov 18).

Since 1 January 2021 a Georgia waiver is "conclusively deemed effective" on the earliest of the
money it names arriving, a signed acknowledgment of payment, or 90 days after it was signed unless
the claimant files an affidavit of nonpayment before then (O.C.G.A. § 44-14-366(g)(2)), recorded
in the county where the property is, with a copy to the owner within 7 days. Paid or not, on day
90 it works as if the money came. Filing a lien no longer stops it. So a waiver sent with a pay app
or a retainage request that is still unpaid after 60 days is a To Do row and in the daily email,
urgent from day 80; once the day passes, a note says the waiver is now in effect (Stephen, 18 Nov:
"Build as needed by your read of the law. Make notes of your questions").

Only what Duracash knows went: a waiver record's file in an email of the pay app or retainage
request that didn't fail. A waiver sent after payment (send_waiver) names money already in, so it
has no fuse. Georgia only, by the job's own state (domain.job_state).

attorney: the statute counts the 90 days from the date stated on the waiver (when it was signed).
Duracash knows only when it was sent, which is that day or later, so the date shown can be late.
Should the clock run from the date on the form, from signing, or from handing it over? And is the
last day to file day 89 or day 90 ("prior to the expiration of said 90 day period")? Until then the
row shows until the day it takes effect, and says that date.

Imported at the bottom of domain/__init__.py (from .gawaivers import *). Helpers from domain are
imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

__all__ = ["AFFIDAVIT_DAYS", "AFFIDAVIT_WARN_DAY", "AFFIDAVIT_URGENT_DAY", "IN_EFFECT_NOTE_DAYS", "AFFIDAVIT_PERM",
           "ga_waiver_clocks", "affidavit_rows"]

AFFIDAVIT_DAYS = 90         # § 44-14-366(g)(2)(C), from 1 January 2021 (60 before)
AFFIDAVIT_WARN_DAY = 60     # the row shows from here
AFFIDAVIT_URGENT_DAY = 80   # and is urgent from here
IN_EFFECT_NOTE_DAYS = 7     # the note that it took effect stays on the To Do this long; the email says it once
# Lien rights are the business of whoever sends lien waivers: the row is theirs (rows_with_perms).
AFFIDAVIT_PERM = "send_waivers"


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _waiver_files(job: dict[str, Any], packet: list[dict[str, Any]], extra_ids: Any = (), excluded: Any = ()) -> set[str]:
    """The files of the lien waivers going with a pay app or the retainage request: those on its
    waiver items, and records added to the send that are, or were said to be, waivers."""
    from domain import _going, _was_waiver, is_waiver_item, waiver_answer
    records = {r["id"]: r for r in job.get("records", [])}
    recs = [records.get(a.get("record_id")) for item in packet or [] if is_waiver_item(item) for a in _going(item)]
    for rid in extra_ids or []:
        rec = records.get(rid)
        if rec and (_was_waiver(rec) or waiver_answer(rec) in {"conditional", "unconditional"}):
            recs.append(rec)
    return {f for rec in recs if rec for f in rec.get("file_ids", [])} - set(excluded or [])


def _went(e: dict[str, Any], files: set[str]) -> bool:
    """The email carried one of the waiver's files and didn't fail (queued counts: it is going)."""
    from domain import _email_failed
    return bool(files & set(e.get("attachments") or [])) and not _email_failed(e) and not e.get("sample")


def ga_waiver_clocks(state: dict[str, Any], job: dict[str, Any]) -> list[dict[str, Any]]:
    """Each Georgia waiver Duracash sent whose money hasn't all come in: {what, invoice_id, item_id,
    sent_on, takes_effect} (dates). what is the pay app's number, or "Retainage request"."""
    from domain import OPEN_FOR_PAYMENT, _retainage_packet, job_money, job_state
    if job_state(job) != "GA":
        return []
    emails = [e for e in state.get("email_log", []) if e.get("job_id") == job["id"]]
    out = []
    for inv in job.get("invoices", []):
        # Paid (in full, or a short pay accepted) is the money it named; void or replaced is left out.
        # attorney: a short pay accepted pays less than the sum on the waiver, and a pay app pulled
        # back after its waiver went is never paid: does the 90-day fuse still need an affidavit?
        if inv.get("status") not in OPEN_FOR_PAYMENT:
            continue
        files = _waiver_files(job, inv.get("packet") or [], inv.get("extra_record_ids"), inv.get("excluded_file_ids"))
        first = next((e for e in emails if e.get("kind") == "pay_app" and e.get("payapp_invoice_id") == inv["id"] and _went(e, files)), None)
        if first:
            out.append({"what": inv["number"], "invoice_id": inv["id"], "item_id": "", "sent_at": first["at"]})
    held = job_money(job)["retainage_open_cents"]
    files = _waiver_files(job, _retainage_packet(job)) if held > 0 else set()
    released = [(p.get("at") or "", int(p.get("amount_cents") or 0)) for p in job.get("retainage_payments", [])]
    for e in emails:
        if e.get("kind") != "retainage" or not _went(e, files):
            continue
        # The first request with the waiver whose sum hasn't come in since: the clock runs from it.
        asked = int(e.get("retainage_asked_cents") or job.get("retainage_asked_cents") or held)
        if sum(cents for at, cents in released if at >= e["at"]) < asked:
            out.append({"what": "Retainage request", "invoice_id": "", "item_id": "retainage", "sent_at": e["at"]})
            break
    for c in out:
        c["sent_on"] = _day(c.pop("sent_at"))
        c["takes_effect"] = c["sent_on"] + timedelta(days=AFFIDAVIT_DAYS) if c["sent_on"] else None
    return [c for c in out if c["sent_on"]]


def affidavit_rows(state: dict[str, Any], job: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """The To Do rows for the job's Georgia waivers: from day 60 until the day it takes effect, a
    row to file an affidavit of nonpayment (urgent from day 80); then, for a
    week, a note that it is in effect, in the daily email once, on that day (digest_on).
    Each: {rank, kind, label, tone, text, note, due, invoice_id, item_id, digest_on}."""
    from domain import _day_words
    out = []
    for c in ga_waiver_clocks(state, job):
        days = (today - c["sent_on"]).days
        sent, effect = _day_words(c["sent_on"].isoformat()), _day_words(c["takes_effect"].isoformat())
        ids = {"invoice_id": c["invoice_id"], "item_id": c["item_id"]}
        if AFFIDAVIT_WARN_DAY <= days < AFFIDAVIT_DAYS:
            urgent = days >= AFFIDAVIT_URGENT_DAY
            out.append({"rank": 0.8 if urgent else 3.4, "kind": "affidavit", "label": "Affidavit of nonpayment",
                        "tone": "red" if urgent else "amber",
                        "text": f"{c['what']}: the lien waiver sent {sent} takes effect {effect} unless you file an affidavit of nonpayment. "
                                "Payment hasn't come in.",
                        "note": "File it in the property's county; copy to the owner within 7 days",
                        # No due date (today, to the To Do), so This week shows it from day 60: the date is in its words.
                        "due": "", "digest_on": "", **ids})
        elif AFFIDAVIT_DAYS <= days < AFFIDAVIT_DAYS + IN_EFFECT_NOTE_DAYS:
            out.append({"rank": 6.8, "kind": "waiver_in_effect", "label": "Waiver in effect", "tone": "",
                        "text": f"{c['what']}: the lien waiver sent {sent} is now in effect ({effect}). Payment hasn't come in.",
                        "note": "", "due": "", "digest_on": c["takes_effect"].isoformat(), **ids})
    return out
