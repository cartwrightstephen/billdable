"""The payer's page for one pay app (P2-4): a private link in the pay app email.

The GC or owner opens it without an account and sees the pay app, its packet checklist
and the documents attached, and can press "Mark as received". That answers "did you get
it?" with a timestamp, and the contractor's job page shows it. Every value is HTML-escaped
here: this page is rendered on the server from what contractors typed, with no app code.
"""

from __future__ import annotations

import html
import secrets
from datetime import datetime
from typing import Any

import domain

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
"""

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


def render(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any], base: str) -> str:
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
    return (head
            + f'<p class="sub">{_e(company)} · {_e(job.get("address", ""))}</p>'
            + f"<h1>{_e(invoice['number'])}: {_e(domain.money(invoice['amount_cents']))}</h1>"
            + f'<p class="sub">{_e(job["name"])} · {_e(STATUS_WORDS.get(invoice.get("status"), invoice.get("status", "")))}'
            + (f" · sent {_e(_date(invoice.get('sent_at')))}" if invoice.get("sent_at") and invoice.get("status") != "sent"
               else f" {_e(_date(invoice.get('sent_at')))}" if invoice.get("sent_at") else "") + "</p>"
            + received
            + f'<p><a class="btn secondary" href="{_e(base)}/pdf">Open the pay app PDF</a></p>'
            + f'<h2>Lines</h2><div class="card">{lines or "<p>No lines.</p>"}</div>'
            + (f'<h2>Packet</h2><div class="card">{"".join(packet)}</div>' if packet else "")
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
