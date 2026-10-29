"""The payer's page for one pay app (P2-4): a private link in the pay app email.

The GC or owner opens it without an account and sees the pay app, its packet checklist
and the documents attached, and can press "Mark as received". That answers "did you get
it?" with a timestamp, and the contractor's job page shows it. They can also approve it for
payment with the date to expect the money, or send a question that lands in the contractor's
Inbox (R4-08). Every value is HTML-escaped here: this page is rendered on the server from what
contractors typed, with no app code.
"""

from __future__ import annotations

import html
import secrets
from datetime import datetime
from typing import Any

import domain
import importer
import payapp_pdf

PAGE_STYLE = """
.card.alert{border-color:#8d2d2d}.sign-form{flex-direction:column;align-items:stretch}
.agree{display:flex;gap:10px;align-items:flex-start}.agree input{flex:none;margin-top:4px;min-width:auto}.signer{flex:none}
body{font:16px/1.55 -apple-system,"Segoe UI",system-ui,sans-serif;color:#1c1915;background:#f6f3ee;margin:0}
main{max-width:760px;margin:0 auto;padding:28px 18px 56px}
h1{font:500 30px/1.2 Georgia,serif;margin:4px 0 6px}h2{font-size:18px;margin:26px 0 8px}
.sub{color:#5f5850;margin:0 0 4px}.card{background:#fffcf8;border:1px solid #d9d1c5;border-radius:12px;padding:16px;margin:14px 0}
.row{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid #e4ddd2}.row:last-child{border:0}
.ok{color:#1f6b45;font-weight:600}.no{color:#8d2d2d;font-weight:600}
.btn{display:inline-block;background:#8c3f1d;color:#fff;border:0;border-radius:8px;padding:12px 18px;font:inherit;font-weight:600;cursor:pointer;text-decoration:none}
.btn.secondary{background:transparent;color:#8c3f1d;border:1px solid #8a8075}
input{font:inherit;padding:10px 12px;border:1px solid #8a8075;border-radius:8px;min-width:0;flex:1 1 220px}
form{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}a{color:#8c3f1d}.foot{color:#5f5850;font-size:13px;margin-top:28px}
.received{background:#e5f2ea;border-color:#b7d8c4}
.stack{flex-direction:column;align-items:stretch}textarea{font:inherit;padding:10px 12px;border:1px solid #8a8075;border-radius:8px;min-height:96px}
.total{font-weight:600}
"""

MAX_QUESTIONS = 20   # per pay app: past that, the customer replies to the contractor's email

STATUS_WORDS = {"sent": "Sent", "approved": "Approved", "partial": "Partly paid", "paid": "Paid"}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _date(iso: str | None) -> str:
    try:
        d = datetime.fromisoformat(str(iso))
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    except (TypeError, ValueError):
        return ""


def find(state: dict[str, Any], token: str) -> tuple[dict | None, dict | None]:
    """The job and pay app this link belongs to, or (None, None)."""
    for job in state.get("jobs", []):
        for inv in job.get("invoices", []):
            stored = inv.get("payer_token")
            if stored and isinstance(token, str) and secrets.compare_digest(stored, token):
                return job, inv
    return None, None


def file_ids(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> list[str]:
    """Only the documents attached to this pay app's packet can be opened from its page."""
    return domain._packet_file_ids(state, job, invoice)


def _job_card(job: dict[str, Any], invoice: dict[str, Any]) -> str:
    """"This job": the application's own figures (lines 1-9 of the PDF), up to this pay app."""
    f = payapp_pdf.summary(job, invoice)
    rows = [("Contract", f["original"]), ("Change orders", f["net_co"] + f["unsigned_co"]), ("Contract to date", f["contract_to_date"]),
            ("Work done to date", f["completed"]), ("Retainage held", f["retainage"]),
            ("Paid or billed before this one", f["previous"]), ("This pay app", f["due"]), ("Balance to finish", f["balance"])]
    return "".join(f'<div class="row{" total" if label == "This pay app" else ""}"><span>{_e(label)}</span><span>{_e(domain.money(cents))}</span></div>'
                   for label, cents in rows)


def _previous(job: dict[str, Any], invoice: dict[str, Any]) -> str:
    """The pay apps that went out before this one, with where each stands."""
    rows = []
    for inv in job.get("invoices", []):
        if inv is invoice or inv.get("id") == invoice.get("id"):
            break
        if inv.get("status") not in domain.BILLED_STATUSES:
            continue
        rows.append(f'<div class="row"><span>{_e(inv["number"])} · sent {_e(_date(inv.get("sent_at")))}</span>'
                    f'<span>{_e(domain.money(inv["amount_cents"]))} · {_e(STATUS_WORDS.get(inv["status"], inv["status"]))}</span></div>')
    return "".join(rows)


def _approval(company: str, invoice: dict[str, Any], base: str, errors: dict[str, str]) -> str:
    if invoice.get("approved_at"):
        return (f'<div class="card received"><strong>Approved for payment</strong> by {_e(invoice.get("approved_by_payer") or "the customer")} '
                f'on {_e(_date(invoice["approved_at"]))}. Payment expected by {_e(_date(invoice.get("expected_payment_on")))}.</div>')
    if invoice.get("status") != "sent":
        return ""
    err = "".join(f'<p class="no" role="alert">{_e(v)}</p>' for v in errors.values())
    return (f'<div class="card{" alert" if err else ""}"><strong>Approve for payment</strong>'
            f'<p class="sub">Tell {_e(company)} this pay app is approved and when to expect the money.</p>{err}'
            f'<form class="stack" method="post" action="{_e(base)}/approve">'
            f'<label for="ap-name" class="sub">Your name</label><input id="ap-name" name="name" maxlength="80" autocomplete="name" required>'
            f'<label for="ap-date" class="sub">Payment expected by</label><input id="ap-date" name="expected_on" type="date" required>'
            f'<button class="btn" type="submit">Approve for payment</button></form></div>')


def _question(company: str, invoice: dict[str, Any], base: str, errors: dict[str, str], asked: bool) -> str:
    if asked:
        return f'<div class="card received"><strong>Question sent.</strong> {_e(company)} has it and will get back to you.</div>'
    if invoice.get("status") == "paid" or int(invoice.get("questions") or 0) >= MAX_QUESTIONS:
        return ""
    err = "".join(f'<p class="no" role="alert">{_e(v)}</p>' for v in errors.values())
    return (f'<div class="card{" alert" if err else ""}"><strong>Something not right?</strong>'
            f'<p class="sub">Ask {_e(company)} about this pay app.</p>{err}'
            f'<form class="stack" method="post" action="{_e(base)}/question">'
            f'<label for="q-text" class="sub">Your question</label><textarea id="q-text" name="question" maxlength="2000" required></textarea>'
            f'<label for="q-name" class="sub">Your name</label><input id="q-name" name="name" maxlength="80" autocomplete="name">'
            f'<label for="q-email" class="sub">Your email, for the answer (optional)</label><input id="q-email" name="email" type="email" maxlength="200" autocomplete="email">'
            f'<button class="btn secondary" type="submit">Send a question</button></form></div>')


def render(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any], base: str,
           errors: dict[str, str] | None = None, form: str = "", asked: bool = False) -> str:
    """`errors` and `form` ("approve" or "question"): a refused form, shown again with why.
    `asked`: a question just went through."""
    errors = errors or {}
    company = state.get("company", {}).get("name", "") or "Your contractor"
    title = f"Pay application {invoice['number']} — {job['name']}"
    head = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{_e(title)}</title><style>{PAGE_STYLE}</style></head><body><main>')
    if invoice.get("status") == "void":
        return head + f"<h1>{_e(invoice['number'])}</h1><p>{_e(company)} withdrew this pay app. Nothing is due on it.</p></main></body></html>"
    files = {f["id"]: f for f in state.get("files", [])}
    lines = "".join(f'<div class="row"><span>{_e(l.get("label"))}</span><span>{_e(domain.money(l.get("amount_cents", 0)))}</span></div>'
                    for l in invoice.get("lines", []))
    packet = []
    records = {r["id"]: r for r in job.get("records", [])}
    for item in invoice.get("packet") or []:
        mark = '<span class="ok">✓</span>' if item.get("present") else '<span class="no">✗</span>'
        docs = []
        for att in item.get("attachments", []):
            for fid in (records.get(att.get("record_id")) or {}).get("file_ids", []):
                if fid in files:
                    docs.append(f'<a href="{_e(base)}/file/{_e(fid)}">{_e(files[fid]["name"])}</a>')
        packet.append(f'<div class="row"><span>{mark} {_e(item.get("label"))}</span><span>{" · ".join(docs)}</span></div>')
    if invoice.get("received_at"):
        received = (f'<div class="card received"><strong>Received</strong> by {_e(invoice.get("received_by") or "the payer")} '
                    f'on {_e(_date(invoice["received_at"]))}. Thank you.</div>')
    else:
        received = (f'<div class="card"><strong>Got it?</strong> Let {_e(company)} know this pay app reached you.'
                    f'<form method="post" action="{_e(base)}/received"><label for="who" class="sub">Your name</label>'
                    f'<input id="who" name="name" maxlength="80" autocomplete="name" placeholder="Your name">'
                    f'<button class="btn" type="submit">Mark as received</button></form></div>')
    previous = _previous(job, invoice)
    return (head
            + f'<p class="sub">{_e(company)} · {_e(job.get("address", ""))}</p>'
            + f"<h1>{_e(invoice['number'])}: {_e(domain.money(invoice['amount_cents']))}</h1>"
            + f'<p class="sub">{_e(job["name"])} · {_e(STATUS_WORDS.get(invoice.get("status"), invoice.get("status", "")))}'
            + (f" · sent {_e(_date(invoice.get('sent_at')))}" if invoice.get("sent_at") and invoice.get("status") != "sent"
               else f" {_e(_date(invoice.get('sent_at')))}" if invoice.get("sent_at") else "") + "</p>"
            + received
            + _approval(company, invoice, base, errors if form == "approve" else {})
            + f'<p><a class="btn secondary" href="{_e(base)}/pdf">Open the pay app PDF</a></p>'
            + f'<h2>This job</h2><div class="card">{_job_card(job, invoice)}</div>'
            + (f'<h2>Previous applications</h2><div class="card">{previous}</div>' if previous else "")
            + f'<h2>Lines</h2><div class="card">{lines or "<p>No lines.</p>"}</div>'
            + (f'<h2>Paperwork</h2><div class="card">{"".join(packet)}</div>' if packet else "")
            + _question(company, invoice, base, errors if form == "question" else {}, asked)
            + f'<p class="foot">This page is for the people {_e(company)} sent this pay app to. Prepared with Duracash. '
            + "Questions about the amounts go to the contractor; reply to their email.</p></main></body></html>")


def mark_received(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any], name: str, now: str) -> bool:
    """Record that the payer has the pay app. Returns False when it was already recorded."""
    if invoice.get("received_at") or invoice.get("status") == "void":
        return False
    name = " ".join(str(name or "").split())[:80]
    invoice["received_at"] = now
    invoice["received_by"] = name
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    who = name or payer.get("name") or "The payer"
    if invoice.get("status") == "sent" and not job.get("closed"):
        job["external_status"] = f"{who} confirmed receipt of {invoice['number']} on {_date(now)}"
    domain.audit(state, job["id"], "payer_received", f"{who} marked {invoice['number']} as received on the payer page.", "payer")
    return True


def approve(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any], name: str, expected_on: str, now: str) -> None:
    """"Approve for payment" (R4-08): the customer approved this pay app and says when the money
    will come. Today then shows it, and no follow-up is due until after that date."""
    if invoice.get("status") != "sent" or job.get("closed"):
        raise domain.GateError("This pay app can't be approved here now.")
    name = " ".join(str(name or "").split())[:80]
    problems = {}
    if not name:
        problems["name"] = "Enter your name."
    try:
        day = domain._day(expected_on, "expected_on", "Enter the date payment is expected, like 2026-11-14.")
    except domain.GateError as exc:
        day, problems = None, {**problems, **exc.fields}
    today = datetime.fromisoformat(now).date()
    if day is None and "expected_on" not in problems:
        problems["expected_on"] = "Enter the date payment is expected."
    elif day and day < today:
        problems["expected_on"] = "Enter today or a later date."
    elif day and (day - today).days > 366:
        problems["expected_on"] = "Enter a date within a year."
    domain._refuse("Not approved yet.", problems)
    invoice["status"] = "approved"
    invoice["approved_by_payer"] = name
    invoice["approved_at"] = now
    invoice["expected_payment_on"] = day.isoformat()
    job["external_status"] = f"Approved by {name}, expect payment {_date(day.isoformat())}"
    domain.audit(state, job["id"], "payer_approved",
                 f"{name} approved {invoice['number']} for payment on the customer page; payment expected by {day.isoformat()}.", "payer")


def ask(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any], name: str, email: str, text: str) -> dict[str, Any]:
    """"Send a question" (R4-08): an Inbox item on the job, for the contractor to answer.
    Returns the item; the server emails the owners."""
    if invoice.get("status") == "void" or int(invoice.get("questions") or 0) >= MAX_QUESTIONS:
        raise domain.GateError("Reply to the contractor's email with your question.")
    text = str(text or "").strip()[:2000]
    name = " ".join(str(name or "").split())[:80]
    email = str(email or "").strip()[:200]
    problems = {}
    if not text:
        problems["question"] = "Enter your question."
    if email and not domain.EMAIL_RE.match(email):
        problems["email"] = "Enter an email like kim@example.com, or leave it blank."
    domain._refuse("Not sent.", problems)
    who = name or "The customer"
    item = importer.add_item(state, "payer_page", text=text, sender=f"{who} <{email}>" if email else who,
                             subject=f"Question on {invoice['number']} — {job['name']}", job_id=job["id"],
                             extra={"invoice_id": invoice["id"], "match_reason": f"Asked on the customer page for {invoice['number']}."})
    invoice["questions"] = int(invoice.get("questions") or 0) + 1
    domain.audit(state, job["id"], "payer_question", f"{who} asked a question about {invoice['number']} on the customer page.", "payer")
    return item


def find_co(state: dict[str, Any], token: str) -> tuple[dict | None, dict | None]:
    """The job and change order a signing link belongs to, or (None, None)."""
    for job in state.get("jobs", []):
        for co in job.get("change_orders", []):
            stored = co.get("sign_token")
            if stored and isinstance(token, str) and secrets.compare_digest(stored, token):
                return job, co
    return None, None


def render_co(state: dict[str, Any], job: dict[str, Any], co: dict[str, Any], base: str, error: str = "") -> str:
    """The signing page for one change order (P2-3)."""
    company = state.get("company", {}).get("name", "") or "Your contractor"
    title = f"Change order {co['number']} — {job['name']}"
    head = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{_e(title)}</title><style>{PAGE_STYLE}</style></head><body><main>')
    summary = (f'<p class="sub">{_e(company)} · {_e(job["name"])} · {_e(job.get("address", ""))}</p>'
               f"<h1>Change order {_e(co['number'])}: {_e(domain.money(co['amount_cents']))}</h1>"
               f'<div class="card"><strong>{_e(co["title"])}</strong>'
               + (f"<p>{_e(co['description'])}</p>" if co.get("description") else "") + "</div>")
    sig = co.get("signature")
    if sig:
        return (head + summary + f'<div class="card received"><strong>Signed</strong> by {_e(sig["name"])} on {_e(_date(sig["at"]))}. '
                f"A copy of what was signed is kept with {_e(company)}'s records.</div>"
                + '<p class="foot">Prepared with Duracash.</p></main></body></html>')
    if co.get("status") != "pending":
        return head + summary + f"<p>This change order is {_e(co.get('status'))} and can no longer be signed here.</p></main></body></html>"
    return (head + summary
            + (f'<div class="card alert" role="alert"><span class="no">{_e(error)}</span></div>' if error else "")
            + f'<div class="card"><form class="sign-form" method="post" action="{_e(base)}/sign">'
            + f'<label class="agree"><input type="checkbox" name="agree" value="yes"> '
            + f"<span>{_e(domain.CO_CONSENT)}</span></label>"
            + '<label for="signer" class="sub">Your full name</label><input id="signer" name="name" maxlength="120" autocomplete="name" required class="signer">'
            + '<button class="btn" type="submit">Sign change order</button></form></div>'
            + f'<p class="foot">Your name, the time, your network address and this change order as shown are kept as the record of your signature. '
            + f"Questions go to {_e(company)}; reply to their email.</p></main></body></html>")
