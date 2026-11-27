"""Revise: a sent pay app goes back to a draft to be corrected (Siteline comparison, item 5).

Pull back (domain.void_sent_pay_app) withdraws a pay app and bills again under a new number.
Revise keeps the pay app and its number: it goes back to a draft, is corrected, approved again
and sent again. Before it changes, what went out is kept on it as a revision, in
invoice["revisions"]: the PDF as the client got it (saved as a file, through save_pdf) and its
figures, lines, dates and approvals, so the earlier version stays viewable in its history.

Only one sent and not paid: no payment recorded on it, no retainage paid that it holds, nothing
sent or drafted after it on the job. The approvals given for the version that went are cleared,
since a changed pay app is signed again, and the client's link to it stops working until it goes
again (a new link is made then). The client is not emailed: the corrected pay app is.

Imported at the bottom of domain/__init__.py (from .revise import *). Helpers from domain are
imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

__all__ = ["revise_sent_pay_app", "REVISABLE"]

REVISABLE = {"sent", "approved"}
# What the client and the send set on a pay app; kept on the revision, then cleared.
SENT_FIELDS = ("sent_at", "approved_by", "due_on", "received_at", "received_by", "approved_at", "approved_by_payer",
               "expected_payment_on", "approvals", "approvals_frozen_at", "contract_at_send", "payer_token",
               "last_follow_up_at", "conditional_waiver_kind", "unconditional_override")


def revise_sent_pay_app(state: dict[str, Any], job_id: str, invoice_id: str, reason: str, actor: str = "owner",
                        save_pdf: Callable[[dict[str, Any], dict[str, Any]], str] | None = None) -> dict[str, Any]:
    """Put a sent, unpaid pay app back to a draft for revision, keeping a snapshot of what went.
    save_pdf(job, invoice) stores the PDF as it is now and returns the file id ("" if it can't)."""
    import domain
    job = domain._open_job(state, job_id)
    invoice = next((inv for inv in job["invoices"] if inv["id"] == invoice_id), None)
    if not invoice or invoice.get("status") not in REVISABLE or not invoice.get("sent_at"):
        if invoice and invoice.get("status") in domain.DRAFT_STATUSES:
            raise domain.GateError(f"{invoice['number']} is a draft already. Change it, then send it.")
        raise domain.GateError("Pick a pay app that has gone out and isn't paid. A paid one is corrected on the next pay app.")
    number = invoice["number"]
    if any(p.get("invoice_id") == invoice["id"] for p in job.get("payments", [])):
        raise domain.GateError(f"A payment is recorded on {number}, so it can't go back to a draft. Correct the next pay app instead.")
    if domain._holds_paid_retainage(job, invoice):
        raise domain.GateError(f"Retainage recorded as paid is held on {number}, so it can't go back to a draft. Correct the next pay app instead.")
    later = [inv for inv in job["invoices"][job["invoices"].index(invoice) + 1:] if inv["status"] != "void"]
    if later:
        nxt = later[0]
        if nxt["status"] in domain.DRAFT_STATUSES:
            raise domain.GateError(f"Delete the draft {nxt['number']} first, then revise {number}.")
        raise domain.GateError(f"{nxt['number']} went out after it. Only the latest pay app can be revised.")
    reason = domain._reason(reason)
    if not reason:
        domain._refuse("", {"reason": "Enter what needs correcting, like the quantity on line 3."})
    now = domain.utcnow()
    file_id = ""
    if save_pdf:
        try:
            file_id = save_pdf(job, invoice) or ""
        except domain.GateError:
            file_id = ""
    revisions = invoice.setdefault("revisions", [])
    revisions.append({
        "n": len(revisions) + 1,
        "at": now,
        "by": actor,
        "reason": reason,
        "status_was": invoice["status"],
        "amount_cents": invoice["amount_cents"],
        "gross_cents": invoice.get("gross_cents", invoice["amount_cents"]),
        "retainage_cents": invoice.get("retainage_cents", 0),
        "period_to": invoice.get("period_to", ""),
        "lines": deepcopy(invoice.get("lines") or []),
        "figures": domain.payapp_figures(job, invoice),
        "file_id": file_id,
        # Who signed the version that went, as its page shows them (network address and browser stay off).
        "signatures": domain.signed_approvals(state, job, invoice) if invoice.get("approvals_frozen_at") else [],
        **{k: deepcopy(invoice[k]) for k in SENT_FIELDS if invoice.get(k) and k not in {"payer_token", "contract_at_send", "approvals", "approvals_frozen_at"}},
    })
    for k in SENT_FIELDS:
        invoice.pop(k, None)
    invoice["sent_at"] = None
    invoice["approved_by"] = None
    invoice["revised_at"] = now
    invoice["status"] = "draft"
    domain._refresh_draft_gate(job, state)
    job["external_status"] = f"{number} back to a draft for revision"
    domain.audit(state, job_id, "payapp_revised",
                 f"{actor} put {number} ({domain.money(revisions[-1]['amount_cents'])}) back to a draft to revise it: {reason}. "
                 f"The version sent {revisions[-1].get('sent_at', '')[:10]} is kept as revision {revisions[-1]['n']}"
                 + ("." if file_id else ", without its PDF (it couldn't be made)."), actor)
    return job
