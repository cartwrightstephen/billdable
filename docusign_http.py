"""The DocuSign routes (docusign.py does the work). Kept out of server.py so the other work
streams' changes there don't meet these: server.py lists the routes in its "docusign" block and
calls install(Handler, its module) once, which adds the handlers below to the request handler.

Routes:
- GET  /api/docusign/status       what Settings and the Sign step show (no tokens)
- POST /api/docusign/connect      an owner starts the OAuth sign-in (perm company)
- GET  /api/docusign/callback     DocuSign sends the owner back here
- POST /api/docusign/disconnect   (perm company)
- POST /api/docusign/settings     {co_client_signs} (perm company)
- POST /api/docusign/sign         {kind: payapp|waiver|co, job_id, ...}: the envelope and the signing page's address
- GET  /api/docusign/return       the signer comes back; one status read, then their page
- POST /api/docusign/webhook      DocuSign Connect, HMAC checked
- POST /api/docusign/check        {envelope_id}: one status read, at most every 15 minutes
"""

from __future__ import annotations

import re
import time
from html import escape as html_escape
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import docusign
import domain
import importer

EVENTS = {"signing_complete", "cancel", "decline", "exception", "session_timeout", "ttl_expired", "viewing_complete",
          "access_code_failed", "id_check_failed", "fax_pending"}
ENVELOPE_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
MAX_WEBHOOK_BYTES = 2 * 1024 * 1024   # a JSON SIM message with its recipients, no documents
S: ModuleType   # the server module, set by install


def install(handler_cls: type, server_module: ModuleType) -> None:
    global S
    S = server_module
    for name, fn in list(globals().items()):
        if name.startswith(("_g_docusign", "_p_docusign")):
            setattr(handler_cls, name, fn)


def _redirect_uri() -> str:
    return f"{S.app_url() or ''}/api/docusign/callback"


def _page(title: str, text: str, back: str, back_words: str) -> bytes:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html_escape(title)} - Duracash</title></head><body><main><h1>{html_escape(title)}</h1><p>{html_escape(text)}</p>'
            f'<p><a href="{back}">{html_escape(back_words)}</a></p></main></body></html>').encode("utf-8")


def _load(company_id: str) -> dict:
    with S.company_lock(company_id):
        return S.load_company(company_id)


def where(env: dict[str, Any]) -> str:
    """The page the signer goes back to."""
    job = quote(str(env.get("job_id") or ""), safe="")
    if env.get("kind") == "payapp":
        return f"/money/pay-app/{job}/{quote(str(env.get('invoice_id') or ''), safe='')}"
    if env.get("kind") == "co":
        return f"/money/change-order/{job}/{quote(str(env.get('co_id') or ''), safe='')}"
    return f"/jobs/{job}"


# ----- connecting -----

def _g_docusign_status(self, m, user) -> None:
    self._json(200, docusign.status(user["company_id"]))


def _p_docusign_connect(self, m, ctx) -> None:
    if ctx.company_id == S.auth.DEMO_COMPANY_ID:
        self._json(409, {"error": "The demo account can't connect DocuSign."})
        return
    try:
        if docusign.configured() and not docusign.redirect_ok(_redirect_uri()):
            S.log_json("warn", msg="Connect DocuSign needs DURACASH_URL set to the site's https address", company=ctx.company_id)
        self._json(200, {"url": docusign.authorize_url(ctx.company_id, ctx.actor, _redirect_uri(), ctx.user["id"])})
    except docusign.DocusignError as exc:
        self._json(409, {"error": str(exc)})


def _g_docusign_callback(self, m) -> None:
    q = parse_qs(urlparse(self.path).query)
    arg = lambda k: (q.get(k) or [""])[0][:2000]
    try:
        if arg("error"):
            raise docusign.DocusignError("DocuSign wasn't connected." + (" You cancelled it." if arg("error") == "access_denied" else ""))
        user = self._session_user()
        company_id = docusign.finish(arg("state"), arg("code"), user["id"] if user else "")
    except docusign.DocusignError as exc:
        self._send(400, _page("DocuSign", str(exc), "/settings/company", "Back to Settings"), "text/html; charset=utf-8")
        return
    name = (docusign.connection(company_id) or {}).get("account_name") or "the DocuSign account"
    self._mutate(company_id, lambda st: domain.audit(st, "", "docusign_connected", f"DocuSign was connected ({name}).", "DocuSign"))
    self._send(303, b"", "text/plain", {"Location": "/settings/company"})


def _p_docusign_disconnect(self, m, ctx) -> None:
    docusign.disconnect(ctx.company_id)
    self._mutate(ctx.company_id, lambda st: domain.audit(st, "", "docusign_disconnected", f"{ctx.actor} disconnected DocuSign.", ctx.actor))
    self._json(200, docusign.status(ctx.company_id))


def _p_docusign_settings(self, m, ctx) -> None:
    on = ctx.body.get("co_client_signs") is True
    try:
        reply = docusign.set_co_client_signs(ctx.company_id, on)
    except docusign.DocusignError as exc:
        self._json(409, {"error": str(exc)})
        return
    words = "now sign change orders in DocuSign after the company" if on else "no longer sign change orders in DocuSign"
    self._mutate(ctx.company_id, lambda st: domain.audit(st, "", "docusign_settings", f"{ctx.actor}: clients {words}.", ctx.actor))
    self._json(200, reply)


# ----- sending -----

def _envelope_for(state: dict, ctx, body: dict) -> dict[str, Any]:
    """What is signed: its PDF, a label, the second signer if any, and what the envelope record
    keeps to mark it signed later. Refused (GateError) as the typed signature would be."""
    user = ctx.user
    kind = S._text(body, "kind")
    job_id = S._text(body, "job_id")
    job = docusign._job(state, job_id)
    out: dict[str, Any] = {"kind": kind, "job_id": job_id, "second": None, "dates": (True, True)}
    if kind == "payapp":
        if not S.may_take(user, "payapp-approve", state):
            raise S.OwnerOnly()
        invoice = docusign.check_payapp(state, job_id, user, S._text(body, "invoice_id"), S._text(body, "stamp"))
        out["pdf"], out["name"] = docusign.payapp_pdf(state, job_id, invoice["id"])
        out.update(label=invoice["number"], subject=f"Please sign {invoice['number']} - {job['name']}", invoice_id=invoice["id"],
                   stamp=S._text(body, "stamp"), title=S._text(body, "title", limit=80))
    elif kind == "co":
        if not S.may_take(user, "request-co-signature", state):
            raise S.OwnerOnly()
        job, co = docusign.check_co(state, job_id, S._text(body, "co_id"))
        conn = docusign.connection(ctx.company_id) or {}
        if conn.get("co_client_signs"):
            payer = domain._payer(state, job["payer_id"])
            if not payer.get("ap_email"):
                raise domain.GateError(f"Add {payer.get('name') or 'the client'}'s email first: DocuSign emails them to sign after you.")
            out["second"] = {"name": payer.get("approver") or payer.get("name") or "Client", "email": payer["ap_email"]}
        out["pdf"] = docusign.co_pdf(domain.co_snapshot(state, job, co), bool(out["second"]))
        out.update(name=f"CO-{co['number']}.pdf", label=f"CO {co['number']} - {co['title']}"[:120],
                   subject=f"Please sign change order {co['number']} - {job['name']}", co_id=co["id"])
    elif kind == "waiver":
        if not S.may_take(user, "waiver-type", state):
            raise S.OwnerOnly()
        which = "final" if S._text(body, "which") == "final" else "interim"
        waiver_kind = S._text(body, "waiver_kind")
        if waiver_kind and waiver_kind not in domain.WAIVER_KINDS:
            raise domain.GateError("Pick the waiver type: conditional or unconditional, progress or final.")
        for_id = S._text(body, "for_id")
        out["pdf"], out["name"] = docusign.waiver_pdf(state, job_id, for_id, which)
        words = domain.WAIVER_KINDS.get(waiver_kind) or ("Final lien waiver" if which == "final" else "Lien waiver")
        out.update(label=f"{words} - {job['name']}"[:120], subject=f"Please sign: {words} - {job['name']}"[:100],
                   for_id=for_id, which=which, waiver_kind=waiver_kind, dates=(False, False))
    else:
        raise S.BadRequest(S.NOT_UNDERSTOOD)
    return out


def _p_docusign_sign(self, m, ctx) -> None:
    if ctx.company_id == S.auth.DEMO_COMPANY_ID or not docusign.connected(ctx.company_id):
        self._json(409, {"error": "DocuSign isn't connected for this company. Sign with your typed name, or ask an owner to connect it in Settings."})
        return
    email = str(ctx.user.get("email") or "")
    if "@" not in email:
        self._json(409, {"error": "Your account needs an email address to sign in DocuSign."})
        return
    state = _load(ctx.company_id)
    try:
        what = _envelope_for(state, ctx, ctx.body)
    except S.OwnerOnly:
        self._json(403, {"error": S.NO_PERMISSION})
        return
    except domain.GateError as exc:
        self._json(409, S._gate(exc))
        return
    signer = {"name": ctx.user.get("name") or email, "email": email, "client_user_id": ctx.user["id"]}
    base = S.app_url() or ""
    notify = f"{base}/api/docusign/webhook" if base.startswith("https://") and docusign.settings()["connect_keys"] else ""
    definition = docusign.envelope_definition(what["pdf"], what["name"], what["subject"], signer, what["second"], notify, what["dates"])
    try:
        envelope_id = docusign.create_envelope(ctx.company_id, definition)
    except docusign.DocusignError as exc:
        self._json(409, {"error": str(exc)})
        return
    env = {"id": envelope_id, "company_id": ctx.company_id, "status": "sent", "created_at": domain.utcnow(),
           "user_id": ctx.user["id"], "user_name": ctx.actor, "signer": {"name": signer["name"], "email": email},
           "client_signer": what["second"], "webhook": bool(notify),
           **{k: what.get(k) for k in ("kind", "job_id", "label", "invoice_id", "stamp", "title", "co_id", "for_id", "which", "waiver_kind") if what.get(k) is not None}}
    docusign.save_envelope(env)
    status, payload = self._mutate(ctx.company_id, lambda st: domain.audit(
        st, what["job_id"], "docusign_sent", f"{ctx.actor} opened {what['label']} for signing in DocuSign (envelope {envelope_id})"
        + (f"; DocuSign emails {what['second']['name']} to sign after." if what["second"] else "."), ctx.actor))
    try:
        url = docusign.recipient_view(ctx.company_id, envelope_id, signer, f"{self._base_url()}/api/docusign/return?e={envelope_id}")
    except docusign.DocusignError as exc:
        self._json(409, {"error": str(exc)})
        return
    self._json(200, {"url": url, "envelope_id": envelope_id})


# ----- completion -----

def complete(self, envelope_id: str, info: dict[str, Any]) -> bool:
    """File a completed envelope once (docusign.claim). False when it failed and should be tried
    again (the webhook answers 500, so DocuSign sends it again)."""
    env = docusign.claim(envelope_id)
    if not env:
        return True   # filed already, or being filed now
    company_id = env["company_id"]
    if not info.get("signers"):
        try:   # a message without its recipients: they are read once
            info = docusign.read_envelope(company_id, envelope_id)
            docusign.update_envelope(envelope_id, polled_at=time.time())
        except docusign.DocusignError as exc:
            docusign.release(envelope_id, str(exc))
            return False
    try:
        combined, certificate = docusign.signed_files(company_id, envelope_id)
        base = importer.safe_name(str(env.get("label") or "signed"))[:80]
        metas = [importer.store_bytes(company_id, f"{base} signed.pdf", combined, "DocuSign"),
                 importer.store_bytes(company_id, f"{base} certificate.pdf", certificate, "DocuSign")]
    except (docusign.DocusignError, domain.GateError) as exc:
        docusign.release(envelope_id, str(exc))
        S.log_json("warn", msg="DocuSign envelope not filed", company=company_id, envelope=envelope_id, error=S.redact(str(exc))[:200])
        return False
    result: dict[str, Any] = {}
    status, payload = self._mutate(company_id, lambda st: result.update(docusign.file_signed(st, env, info, metas)))
    if status != 200:
        importer.discard(company_id, metas)
        docusign.release(envelope_id, str(payload.get("error") or status))
        S.log_json("warn", msg="DocuSign envelope not filed", company=company_id, envelope=envelope_id, error=str(payload.get("error"))[:200])
        return False
    docusign.update_envelope(envelope_id, status="completed", completed_at=info.get("completed_at") or domain.utcnow(),
                             record_ids=result.get("record_ids", []), note=result.get("note", ""))
    return True


def ended(self, envelope_id: str, status: str) -> None:
    env = docusign.envelope(envelope_id)
    if not env or env.get("status") in docusign.DONE | docusign.ENDED:
        return
    docusign.update_envelope(envelope_id, status=status)
    words = "was declined" if status == "declined" else "was voided"
    self._mutate(env["company_id"], lambda st: domain.audit(
        st, env.get("job_id", ""), "docusign_ended", f"{env.get('label') or 'A document'} {words} in DocuSign (envelope {envelope_id}). Nothing was marked signed.", "DocuSign"))


def _read_now(self, env: dict[str, Any]) -> str:
    """One status read for this envelope, and what follows from it. Returns the status."""
    docusign.update_envelope(env["id"], polled_at=time.time())
    info = docusign.read_envelope(env["company_id"], env["id"])
    if info["status"] in docusign.DONE:
        complete(self, env["id"], info)
    elif info["status"] in docusign.ENDED:
        ended(self, env["id"], info["status"])
    return info["status"]


def _g_docusign_return(self, m) -> None:
    """The signer is back from DocuSign. The event on the address is only a hint (DocuSign: don't
    rely on the return URL alone); with signing_complete, one status read files it if done."""
    q = parse_qs(urlparse(self.path).query)
    envelope_id = (q.get("e") or [""])[0][:64]
    event = (q.get("event") or [""])[0]
    event = event if event in EVENTS else "unknown"
    env = docusign.envelope(envelope_id) if ENVELOPE_ID.fullmatch(envelope_id) else None
    if not env:
        self._send(303, b"", "text/plain", {"Location": "/dashboard"})
        return
    user = self._session_user()
    if user and user["company_id"] == env["company_id"] and event == "signing_complete" \
            and env.get("status") not in docusign.DONE | docusign.ENDED and docusign.poll_due(env):
        try:
            _read_now(self, env)
        except docusign.DocusignError as exc:
            S.log_json("warn", msg="DocuSign status read failed", envelope=envelope_id, error=S.redact(str(exc))[:200])
    self._send(303, b"", "text/plain", {"Location": f"{where(env)}?docusign={event}"})


def _p_docusign_webhook(self, m) -> None:
    body = self._read_raw(MAX_WEBHOOK_BYTES)
    if not docusign.hmac_ok(body, self.headers):
        self._json(401, {"error": "Signature doesn't match."})
        return
    envelope_id, account_id, info = docusign.parse_connect(body)
    env = docusign.envelope(envelope_id) if envelope_id else None
    if not env or S.demo_locked(env["company_id"]):
        self._json(200, {"received": True})   # not ours: acknowledged, so DocuSign stops sending it
        return
    conn = docusign.connection(env["company_id"]) or {}
    if account_id and conn.get("account_id") and account_id != conn["account_id"]:
        S.log_json("warn", msg="DocuSign message for another account", envelope=envelope_id)
        self._json(200, {"received": True})
        return
    if info["status"] in docusign.DONE:
        if not complete(self, envelope_id, info):
            self._json(500, {"error": "Not filed yet; send it again."})
            return
    elif info["status"] in docusign.ENDED:
        ended(self, envelope_id, info["status"])
    self._json(200, {"received": True})


def _p_docusign_check(self, m, ctx) -> None:
    envelope_id = S._text(ctx.body, "envelope_id")[:64]
    env = docusign.envelope(envelope_id)
    if not env or env.get("company_id") != ctx.company_id:
        self._json(404, {"error": "That DocuSign envelope isn't this company's."})
        return
    if env.get("status") in docusign.DONE | docusign.ENDED:
        self._json(200, {"status": env["status"], **docusign.status(ctx.company_id)})
        return
    if not docusign.poll_due(env):
        wait = int((docusign.POLL_SECONDS - (time.time() - float(env.get("polled_at") or 0))) // 60) + 1
        self._json(409, {"error": f"DocuSign was asked about this one a moment ago. Try again in {wait} minute{'s' if wait != 1 else ''}."})
        return
    try:
        now = _read_now(self, env)
    except docusign.DocusignError as exc:
        self._json(409, {"error": str(exc)})
        return
    self._json(200, {"status": now, **docusign.status(ctx.company_id)})
