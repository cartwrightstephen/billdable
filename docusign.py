"""DocuSign eSignature: real signatures on pay apps, lien waivers and change orders.

An owner connects the company's own DocuSign account once (OAuth Authorization Code Grant,
Settings, Company, Integrations), the way QuickBooks is connected (qbo.py). After that the Sign
step offers "Sign with DocuSign" beside the typed-name signature:

- Duracash makes the PDF it already makes (the pay app, the state's waiver form, or a change
  order page drawn here), with invisible anchor words where the signature and date go;
- it creates an envelope with that PDF and the signed-in person as an embedded signer, and sends
  them to DocuSign's signing page (a recipient view), which sends them back to Duracash;
- for a change order, with the company's "client signs too" switch on, the client's approver is
  the second signer, emailed by DocuSign after the company has signed;
- when the envelope is completed (DocuSign Connect's webhook, checked by HMAC, or one status read
  when the signer comes back), the signed PDF and the certificate of completion are filed on the
  job, and the document counts as signed: the pay app approval, the waiver on file, or the change
  order approved.

Without DOCUSIGN_CLIENT_ID and DOCUSIGN_CLIENT_SECRET, or with no account connected, nothing
here shows and the typed-name signature works as before.

Settings: DOCUSIGN_CLIENT_ID (the integration key), DOCUSIGN_CLIENT_SECRET, DOCUSIGN_ENV (demo,
the default, or production), DOCUSIGN_CONNECT_KEY (the Connect HMAC secret; several, comma
separated, while one is being replaced) and DURACASH_URL, whose /api/docusign/callback must be a
redirect URI of the integration key. DOCUSIGN_AUTH_BASE overrides the account server (tests).

DocuSign's addresses, fields and rules below were checked on Nov 27 2026 against DocuSign's
developer pages (sources in docs/setup-integrations.md). No demo account has been connected yet;
the first one is the last test (the checklist in that doc).

Tokens are kept in <data dir>/docusign_connections.json, encrypted with the server key (H3,
vault.py: DURACASH_SECRET_KEY) and left out of the backups (H4), like qbo_connections.json: after a
restore onto a new disk an owner connects again. Deleting a company drops its tokens, any Connect
in progress and its envelopes in flight (forget_company). The envelopes in flight are in
<data dir>/docusign_envelopes.json, which the backups hold.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import secrets
import textwrap
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

import storage
import vault

# "signature" for the eSignature REST API; "extended" so each refresh brings a refresh token with
# a full lifetime (DocuSign: Authorization Code Grant, token lifetimes).
SCOPE = "signature extended"
AUTH_BASES = {"demo": "https://account-d.docusign.com", "production": "https://account.docusign.com"}
API_PATH = "/restapi/v2.1"
TIMEOUT = 20
STATE_MINUTES = 10
# "Apps are limited to one GET status request per unique envelope per 15 minutes" (DocuSign's
# API rules); going over is flagged at go-live review.
POLL_SECONDS = 15 * 60
SIGNATURE_HEADERS = 10   # X-DocuSign-Signature-1 .. -10 are looked at

# The words in our PDFs that DocuSign finds and puts the tabs on, drawn in white (pay app:
# payapp_pdf.render; waiver: waiver_form.render; change order: co_pdf here).
ANCHOR_SIGN = ("/ds_sign1/", "/ds_sign2/")
ANCHOR_DATE = ("/ds_date1/", "/ds_date2/")

KINDS = ("payapp", "waiver", "co")
DONE = {"completed"}
ENDED = {"declined", "voided"}

LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_PENDING: dict[str, dict[str, Any]] = {}   # OAuth state -> {company_id, user, user_id, until}


class DocusignError(Exception):
    """Something the person should read: not connected, DocuSign refused, the network failed."""


def settings() -> dict[str, Any]:
    env = "production" if (os.environ.get("DOCUSIGN_ENV") or "").strip().lower() == "production" else "demo"
    keys = [k.strip().strip('"') for k in (os.environ.get("DOCUSIGN_CONNECT_KEY") or "").split(",") if k.strip()]
    return {
        "client_id": (os.environ.get("DOCUSIGN_CLIENT_ID") or "").strip(),
        "client_secret": (os.environ.get("DOCUSIGN_CLIENT_SECRET") or "").strip(),
        "env": env,
        "auth_base": (os.environ.get("DOCUSIGN_AUTH_BASE") or AUTH_BASES[env]).strip().rstrip("/"),
        "connect_keys": keys,
    }


def configured() -> bool:
    s = settings()
    return bool(s["client_id"] and s["client_secret"])


# Before the server has its DocuSign keys (founders' decision, Nov 27): DocuSign still shows, and
# pressing Connect or Sign with DocuSign says this, never an error, and nothing is signed.
SWITCHING_ON = ("DocuSign is being switched on for your account. We'll email you when it's ready. "
           "Until then, sign with your typed name \u2014 it's legally binding.")
SWITCHING_ON_DEMO = "DocuSign is being switched on. Until then, sign with your typed name \u2014 it's legally binding."


def pending_message(demo: bool = False) -> str:
    return SWITCHING_ON_DEMO if demo else SWITCHING_ON


# ----- the HTTP layer (tests replace HTTP with a stand-in; nothing else here opens a socket) -----

def _http(method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> tuple[int, dict[str, str], bytes]:
    req = request.Request(url, data=body, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=TIMEOUT) as res:
            return res.status, dict(res.headers.items()), res.read()
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read() or b""
    except (error.URLError, TimeoutError, OSError) as exc:
        raise DocusignError(f"Couldn't reach DocuSign: {exc}") from None


HTTP: Callable[..., tuple[int, dict[str, str], bytes]] = _http


def _json_of(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# ----- stored connections and envelopes -----

def _path() -> Path:
    return Path(storage.DATA_DIR) / "docusign_connections.json"


def _envelopes_path() -> Path:
    return Path(storage.DATA_DIR) / "docusign_envelopes.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


SECRET_FIELDS = ("access_token", "refresh_token")


def _context(company_id: str) -> str:
    return f"docusign:{company_id}"


def _opened(company_id: str, conn: dict[str, Any] | None) -> dict[str, Any] | None:
    """The stored connection with its tokens decrypted. Tokens written before encryption are read as
    they are and encrypted the next time the connection is saved."""
    if not conn:
        return conn
    out = dict(conn)
    try:
        for field in SECRET_FIELDS:
            if out.get(field):
                out[field] = vault.open_sealed(out[field], _context(company_id))
    except vault.VaultError:
        raise DocusignError("The DocuSign connection can't be read on this server. Connect again.") from None
    return out


def _stored(company_id: str) -> dict[str, Any] | None:
    """The stored connection as it is on disk: for what needs no token (status, settings)."""
    with LOCK:
        return _read(_path()).get(company_id)


def connection(company_id: str) -> dict[str, Any] | None:
    return _opened(company_id, _stored(company_id))


def _store(company_id: str, conn: dict[str, Any] | None) -> None:
    with LOCK:
        data = _read(_path())
        if conn is None:
            data.pop(company_id, None)
        else:
            data[company_id] = {k: vault.seal(v, _context(company_id)) if k in SECRET_FIELDS and v and not vault.sealed(v) else v
                                for k, v in conn.items()}
        storage.write_json(_path(), data)


def connected(company_id: str) -> bool:
    return configured() and bool(_stored(company_id))


def status(company_id: str) -> dict[str, Any]:
    """What Settings and the Sign step show. Never a token."""
    conn = _stored(company_id) if configured() else None   # no tokens needed, so nothing to decrypt
    waiting = [{k: e.get(k) for k in ("id", "kind", "job_id", "label", "status", "created_at", "user_name")}
               for e in envelopes(company_id) if e.get("status") not in DONE | ENDED]
    return {
        "configured": configured(),
        "environment": settings()["env"],
        "connected": bool(conn),
        "connected_at": conn.get("connected_at") if conn else None,
        "connected_by": conn.get("connected_by") if conn else None,
        "account_name": conn.get("account_name") if conn else None,
        "co_client_signs": bool(conn and conn.get("co_client_signs")),
        "webhook": bool(settings()["connect_keys"]),
        "waiting": waiting if conn else [],
    }


def set_co_client_signs(company_id: str, on: bool) -> dict[str, Any]:
    with _REFRESH_LOCK:
        conn = connection(company_id)
        if not conn:
            raise DocusignError("DocuSign isn't connected for this company.")
        conn["co_client_signs"] = bool(on)
        _store(company_id, conn)
    return status(company_id)


def envelope(envelope_id: str) -> dict[str, Any] | None:
    with LOCK:
        return _read(_envelopes_path()).get(envelope_id)


def envelopes(company_id: str) -> list[dict[str, Any]]:
    with LOCK:
        rows = [e for e in _read(_envelopes_path()).values() if e.get("company_id") == company_id]
    return sorted(rows, key=lambda e: e.get("created_at", ""))


def save_envelope(env: dict[str, Any]) -> None:
    with LOCK:
        data = _read(_envelopes_path())
        data[env["id"]] = env
        storage.write_json(_envelopes_path(), data)


def update_envelope(envelope_id: str, **changes: Any) -> dict[str, Any] | None:
    with LOCK:
        data = _read(_envelopes_path())
        env = data.get(envelope_id)
        if env is None:
            return None
        env.update(changes)
        storage.write_json(_envelopes_path(), data)
        return env


def claim(envelope_id: str) -> dict[str, Any] | None:
    """Take an envelope to file it, once: the webhook and the signer coming back can arrive
    together. None when it is gone, filed already or being filed (a claim older than ten
    minutes is taken as failed and can be taken again)."""
    with LOCK:
        data = _read(_envelopes_path())
        env = data.get(envelope_id)
        now = time.time()
        if not env or env.get("status") in DONE or (env.get("status") == "completing" and now - float(env.get("claimed_at") or 0) < 600):
            return None
        env["status_before"] = env.get("status")
        env.update(status="completing", claimed_at=now)
        storage.write_json(_envelopes_path(), data)
        return dict(env)


def release(envelope_id: str, why: str) -> None:
    """Filing failed: the envelope can be filed again later (the next webhook or status read)."""
    env = envelope(envelope_id)
    if env and env.get("status") == "completing":
        update_envelope(envelope_id, status=env.get("status_before") or "sent", last_error=why[:300])


def poll_due(env: dict[str, Any], now: float | None = None) -> bool:
    return (now or time.time()) - float(env.get("polled_at") or 0) >= POLL_SECONDS


# ----- connecting -----

def redirect_ok(redirect_uri: str) -> bool:
    return redirect_uri.startswith(("https://", "http://localhost", "http://127.0.0.1"))


def authorize_url(company_id: str, user_name: str, redirect_uri: str, user_id: str = "") -> str:
    if not configured():
        raise DocusignError("DocuSign isn't set up on this server (DOCUSIGN_CLIENT_ID and DOCUSIGN_CLIENT_SECRET).")
    if not redirect_ok(redirect_uri):
        raise DocusignError("Connecting DocuSign isn't available on this server yet. Contact Duracash support. "
                            "Until then, sign with your typed name.")
    state = secrets.token_urlsafe(24)
    now = time.time()
    with LOCK:
        for key in [k for k, v in _PENDING.items() if v["until"] < now]:
            _PENDING.pop(key, None)
        _PENDING[state] = {"company_id": company_id, "user": user_name, "user_id": user_id, "until": now + STATE_MINUTES * 60}
    query = parse.urlencode({"response_type": "code", "scope": SCOPE, "client_id": settings()["client_id"],
                             "redirect_uri": redirect_uri, "state": state})
    return f"{settings()['auth_base']}/oauth/auth?{query}"


def _basic() -> str:
    s = settings()
    return "Basic " + base64.b64encode(f"{s['client_id']}:{s['client_secret']}".encode()).decode()


def _token_request(form: dict[str, str]) -> dict[str, Any]:
    code, _, raw = HTTP("POST", settings()["auth_base"] + "/oauth/token",
                        {"Authorization": _basic(), "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                        parse.urlencode(form).encode())
    if code != 200:
        raise DocusignError(f"DocuSign refused the sign-in ({code}). Connect again.")
    return _json_of(raw)


def _tokens(reply: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    if not reply.get("access_token") or not reply.get("refresh_token"):
        raise DocusignError("DocuSign didn't send the tokens back. Connect again.")
    now = datetime.now(timezone.utc)
    conn = dict(old or {})
    conn.update(
        access_token=reply["access_token"],
        access_expires_at=(now + timedelta(seconds=int(reply.get("expires_in") or 28800))).isoformat(timespec="seconds"),
        refresh_token=reply["refresh_token"],
        # About 30 days, which "can change at any time"; a refused refresh asks for Connect again.
        refreshed_at=now.isoformat(timespec="seconds"),
    )
    return conn


def _userinfo(access_token: str) -> dict[str, Any]:
    code, _, raw = HTTP("GET", settings()["auth_base"] + "/oauth/userinfo",
                        {"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    if code != 200:
        raise DocusignError(f"DocuSign didn't say which account was connected ({code}). Connect again.")
    return _json_of(raw)


def pick_account(info: dict[str, Any]) -> dict[str, Any]:
    """The user's default account (is_default), else the first; its base_uri is where API calls go."""
    accounts = [a for a in info.get("accounts") or [] if isinstance(a, dict) and a.get("account_id") and a.get("base_uri")]
    if not accounts:
        raise DocusignError("That DocuSign sign-in has no eSignature account. Connect with an account that can send envelopes.")
    chosen = next((a for a in accounts if a.get("is_default") in (True, "true")), accounts[0])
    base = str(chosen["base_uri"]).rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://127.0.0.1"):
        raise DocusignError("DocuSign gave an account address Duracash can't use. Connect again.")
    return {"account_id": str(chosen["account_id"]), "base_uri": base, "account_name": str(chosen.get("account_name") or "")[:120]}


def finish(state: str, code: str, user_id: str = "") -> str:
    """The callback: the state is one we made, for the person signed in now (someone else's
    Connect link can't tie your DocuSign to their company); then the code for tokens, and
    /oauth/userinfo for the account and its base URI. Returns the company id."""
    with LOCK:
        pending = _PENDING.pop(state or "", None)
    if not pending or pending["until"] < time.time():
        raise DocusignError("That DocuSign link has expired. Press Connect DocuSign again.")
    if not user_id or pending.get("user_id") != user_id:
        raise DocusignError("Sign in to Duracash as the owner who pressed Connect DocuSign, then press it again.")
    if not code:
        raise DocusignError("DocuSign didn't send a sign-in code. Connect again.")
    conn = _tokens(_token_request({"grant_type": "authorization_code", "code": code}))
    info = _userinfo(conn["access_token"])
    conn.update(pick_account(info))
    old = _stored(pending["company_id"]) or {}
    conn.update(user_email=str(info.get("email") or "")[:200], user_name=str(info.get("name") or "")[:120],
                connected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), connected_by=pending["user"],
                co_client_signs=bool(old.get("co_client_signs")))
    _store(pending["company_id"], conn)
    return pending["company_id"]


def disconnect(company_id: str) -> None:
    """The tokens are forgotten here. DocuSign has no revoke call for this grant; the owner can
    also remove Duracash from their DocuSign profile's connected apps."""
    with _REFRESH_LOCK:
        _store(company_id, None)


def forget_company(company_id: str) -> bool:
    """A deleted company (H4, as qbo.forget_company): its tokens go from the file, even ones this
    server's key can't open, a Connect still in progress can no longer finish, and its envelopes in
    flight (signers' names and emails) go too. There is nothing to revoke at DocuSign (disconnect).
    Returns whether the company was connected."""
    with LOCK:
        for key in [k for k, v in _PENDING.items() if v.get("company_id") == company_id]:
            _PENDING.pop(key, None)
    with _REFRESH_LOCK:   # after any refresh in flight, so it can't put the connection back
        was = _stored(company_id) is not None
        if was:
            _store(company_id, None)
    with LOCK:
        data = _read(_envelopes_path())
        kept = {k: v for k, v in data.items() if v.get("company_id") != company_id}
        if len(kept) != len(data):
            storage.write_json(_envelopes_path(), kept)
    return was


def _access(company_id: str) -> tuple[str, str, str]:
    """(access token, API base, account id), refreshing a token that is close to expiry."""
    if not configured():
        raise DocusignError("DocuSign isn't set up on this server.")
    with _REFRESH_LOCK:
        conn = connection(company_id)
        if not conn:
            raise DocusignError("DocuSign isn't connected for this company.")
        # DocuSign suggests refreshing well before the end; half an hour ahead here.
        soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
        if conn["access_expires_at"] <= soon:
            try:
                conn = _tokens(_token_request({"grant_type": "refresh_token", "refresh_token": conn["refresh_token"]}), conn)
            except DocusignError:
                _store(company_id, None)
                raise DocusignError("The DocuSign connection expired. An owner needs to connect it again.") from None
            _store(company_id, conn)
        return conn["access_token"], conn["base_uri"] + API_PATH, conn["account_id"]


def _api(company_id: str, method: str, path: str, payload: dict[str, Any] | None = None, raw: bool = False) -> Any:
    token, base, account = _access(company_id)
    url = f"{base}/accounts/{parse.quote(account)}{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/pdf" if raw else "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    code, _, data = HTTP(method, url, headers, body)
    if code == 401:
        raise DocusignError("DocuSign no longer accepts this connection. An owner needs to connect it again.")
    if code >= 300:
        why = _json_of(data)
        detail = str(why.get("message") or why.get("errorCode") or "")[:200]
        raise DocusignError(f"DocuSign answered {code}" + (f": {detail}" if detail else "") + ".")
    return data if raw else _json_of(data)


# ----- envelopes -----

def tabs(n: int) -> dict[str, Any]:
    """Signer n's (1 or 2) Sign Here and Date Signed, on our anchor words. Not found is an error
    (anchorIgnoreIfNotPresent false): a PDF without its anchors is never sent unsigned-looking."""
    anchor = lambda word: {"anchorString": word, "anchorUnits": "pixels", "anchorXOffset": "0", "anchorYOffset": "0",
                           "anchorIgnoreIfNotPresent": "false"}
    out: dict[str, Any] = {"signHereTabs": [anchor(ANCHOR_SIGN[n - 1])]}
    out["dateSignedTabs"] = [anchor(ANCHOR_DATE[n - 1])]
    return out


def envelope_definition(pdf: bytes, doc_name: str, subject: str, signer: dict[str, str],
                        second: dict[str, str] | None = None, notify_url: str = "", dates: tuple[bool, bool] = (True, True)) -> dict[str, Any]:
    """The envelope: our PDF, the company signer embedded (clientUserId: they sign in a recipient
    view, DocuSign emails them nothing), and optionally a second signer DocuSign emails once the
    first has signed. With notify_url, an envelope-level Connect webhook with HMAC, using the
    integration key's own HMAC keys (integratorManaged), so no customer account needs Connect set up."""
    first_tabs = tabs(1)
    if not dates[0]:
        first_tabs.pop("dateSignedTabs")
    signers = [{"email": signer["email"], "name": signer["name"], "recipientId": "1", "routingOrder": "1",
                "clientUserId": signer["client_user_id"], "tabs": first_tabs}]
    if second:
        second_tabs = tabs(2)
        if not dates[1]:
            second_tabs.pop("dateSignedTabs")
        signers.append({"email": second["email"], "name": second["name"], "recipientId": "2", "routingOrder": "2", "tabs": second_tabs})
    definition: dict[str, Any] = {
        "emailSubject": subject[:100],
        "documents": [{"documentBase64": base64.b64encode(pdf).decode(), "name": doc_name[:100], "fileExtension": "pdf", "documentId": "1"}],
        "recipients": {"signers": signers},
        "status": "sent",
    }
    if notify_url:
        definition["eventNotification"] = {
            "url": notify_url, "requireAcknowledgment": "true", "loggingEnabled": "true", "deliveryMode": "SIM",
            "includeHMAC": "true", "integratorManaged": "true",
            "events": ["envelope-completed", "envelope-declined", "envelope-voided", "recipient-completed"],
            "eventData": {"version": "restv2.1", "includeData": ["recipients"]},
        }
    return definition


def create_envelope(company_id: str, definition: dict[str, Any]) -> str:
    reply = _api(company_id, "POST", "/envelopes", definition)
    envelope_id = str(reply.get("envelopeId") or "")
    if not envelope_id or len(envelope_id) > 64:
        raise DocusignError("DocuSign didn't say which envelope it made. Try again.")
    return envelope_id


def recipient_view(company_id: str, envelope_id: str, signer: dict[str, str], return_url: str) -> str:
    """The signing page's address: used once, within 5 minutes, never stored or emailed."""
    reply = _api(company_id, "POST", f"/envelopes/{parse.quote(envelope_id)}/views/recipient", {
        "returnUrl": return_url, "authenticationMethod": "none",
        "email": signer["email"], "userName": signer["name"], "clientUserId": signer["client_user_id"]})
    url = str(reply.get("url") or "")
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise DocusignError("DocuSign didn't give a signing page. Try again.")
    return url


def summary(raw: dict[str, Any]) -> dict[str, Any]:
    """An envelope (GET envelope with recipients, or a Connect message's envelopeSummary) in the
    few fields used here."""
    signers = ((raw.get("recipients") or {}).get("signers")) or []
    return {
        "status": str(raw.get("status") or "").lower(),
        "completed_at": str(raw.get("completedDateTime") or ""),
        "signers": [{"recipient_id": str(s.get("recipientId") or ""), "name": str(s.get("name") or "")[:120],
                     "email": str(s.get("email") or "")[:200], "status": str(s.get("status") or "").lower(),
                     "signed_at": str(s.get("signedDateTime") or "")} for s in signers if isinstance(s, dict)],
    }


def read_envelope(company_id: str, envelope_id: str) -> dict[str, Any]:
    """One status read (the polling fallback); callers keep to POLL_SECONDS per envelope."""
    return summary(_api(company_id, "GET", f"/envelopes/{parse.quote(envelope_id)}?include=recipients"))


def signed_files(company_id: str, envelope_id: str) -> tuple[bytes, bytes]:
    """The signed document as one PDF (without the certificate) and the certificate of completion."""
    quoted = parse.quote(envelope_id)
    combined = _api(company_id, "GET", f"/envelopes/{quoted}/documents/combined?certificate=false", raw=True)
    certificate = _api(company_id, "GET", f"/envelopes/{quoted}/documents/certificate", raw=True)
    for blob in (combined, certificate):
        if not blob.startswith(b"%PDF"):
            raise DocusignError("DocuSign didn't send the signed PDF. It is tried again on the next update.")
    return combined, certificate


# ----- Connect (the webhook) -----

def hmac_ok(body: bytes, headers: Any, keys: list[str] | None = None) -> bool:
    """DocuSign's check: base64 of HMAC-SHA256 over the raw body, one X-DocuSign-Signature-N
    header per key on the account. One match with any of our keys is enough."""
    keys = settings()["connect_keys"] if keys is None else keys
    if not keys or not body:
        return False
    given = []
    for n in range(1, SIGNATURE_HEADERS + 1):
        value = headers.get(f"X-DocuSign-Signature-{n}") if hasattr(headers, "get") else None
        if value:
            given.append(str(value).strip())
    for key in keys:
        expected = base64.b64encode(hmac.new(key.encode("utf-8"), body, hashlib.sha256).digest()).decode()
        if any(hmac.compare_digest(expected.encode(), g.encode()) for g in given):
            return True
    return False


def parse_connect(body: bytes) -> tuple[str, str, dict[str, Any]]:
    """(envelope id, account id, summary) of a JSON SIM message."""
    msg = _json_of(body)
    data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
    envelope_id = str(data.get("envelopeId") or "")
    raw = data.get("envelopeSummary") if isinstance(data.get("envelopeSummary"), dict) else {}
    info = summary(raw)
    if not info["status"]:
        event = str(msg.get("event") or "")
        info["status"] = {"envelope-completed": "completed", "envelope-declined": "declined", "envelope-voided": "voided"}.get(event, "")
    return envelope_id[:64], str(data.get("accountId") or "")[:64], info


# ----- what is signed (the PDFs and the checks before an envelope is made) -----

def _gate():
    import domain
    return domain.GateError


def _job(state: dict[str, Any], job_id: str) -> dict[str, Any]:
    job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
    if not job:
        raise _gate()("Job not found.")
    if job.get("closed"):
        raise _gate()(f"{job['name']} is closed.")
    return job


def _draft(job: dict[str, Any]) -> dict[str, Any] | None:
    return next((i for i in reversed(job.get("invoices", [])) if i["status"] in {"draft", "awaiting_approval"}), None)


def check_payapp(state: dict[str, Any], job_id: str, user: dict[str, Any], invoice_id: str, stamp: str) -> dict[str, Any]:
    """Every check the typed signature makes (domain.approve_payapp), run on a copy, so DocuSign is
    offered only for what the person could sign here. Returns the draft."""
    import domain
    trial = copy.deepcopy(state)
    domain.approve_payapp(trial, job_id, user.get("name") or "owner", user.get("id", ""), invoice_id=invoice_id, seen=stamp,
                          signature=user.get("name") or user.get("email") or "Signer")
    return _draft(_job(state, job_id))


def payapp_pdf(state: dict[str, Any], job_id: str, invoice_id: str) -> tuple[bytes, str]:
    import payapp_pdf as pdf
    data, name = pdf.for_invoice(state, job_id, invoice_id, anchors=True)
    if not data:
        raise _gate()("That pay app is gone.")
    return data, name


def check_co(state: dict[str, Any], job_id: str, co_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    import domain
    return domain._pending_co(state, job_id, co_id)


# What the change order page says about signing, in place of the typed-name consent.
CO_DOCUSIGN_CONSENT = "Signed electronically through DocuSign. DocuSign's certificate of completion is kept with this change order."


def co_pdf(snapshot: dict[str, Any], client_signs: bool) -> bytes:
    """The change order as the client's signing page shows it (domain.co_snapshot), on one or two
    landscape pages, with the contractor's signature line and, when the client signs too, theirs."""
    import payapp_pdf as pdf
    pages = []
    page = pdf._Page()
    left, top = pdf.MARGIN, pdf.PAGE_H - pdf.MARGIN - 14
    y = top

    def line(text: str, font: str = "F1", size: float = 10.5, step: float = 15) -> None:
        nonlocal page, y
        if y < 70:
            pages.append(page)
            page, y = pdf._Page(), top
        page.text(left, y, text, font, size)
        y -= step

    line(f"CHANGE ORDER {snapshot.get('change_order', '')}", "F2", 16, 22)
    for label, value in (("Contractor", snapshot.get("contractor")), ("Job", snapshot.get("job")), ("Address", snapshot.get("address")),
                         ("Change", snapshot.get("title")), ("Reason", snapshot.get("reason")),
                         ("Contract reference", snapshot.get("contract_ref")), ("Pricing", snapshot.get("pricing"))):
        if value:
            line(pdf._fit(f"{label}: {value}", 130))
    if snapshot.get("description"):
        y -= 4
        for para in str(snapshot["description"]).splitlines():
            for chunk in textwrap.wrap(para, 130) or [""]:
                line(chunk, size=10)
    if snapshot.get("items"):
        y -= 4
        for item in snapshot["items"]:
            page.right(left + 600, y, pdf._money(int(item.get("amount_cents") or 0)), 10)
            line(pdf._fit(str(item.get("label") or ""), 90), size=10)
    y -= 6
    page.right(left + 600, y, pdf._money(int(snapshot.get("amount_cents") or 0)), 11)
    line("Amount of this change order", "F2", 11, 18)
    days = int(snapshot.get("schedule_days") or 0)
    line(f"Schedule: {days:+d} day{'' if abs(days) == 1 else 's'}" if days else "Schedule: no change", step=22)
    line(CO_DOCUSIGN_CONSENT, size=9, step=34)
    blocks = [("Contractor", 0)] + ([("Client", 1)] if client_signs else [])
    for who, n in blocks:
        if y < 110:
            pages.append(page)
            page, y = pdf._Page(), top
        page.text(left, y, f"{who} signature: ____________________________________     Date: ______________", "F1", 10)
        anchors(page, left, y, n)
        y -= 46
    pages.append(page)
    return pdf._build(pages)


def anchors(page: Any, x: float, y: float, n: int = 0) -> None:
    """The white anchor words on a "... signature: ____ Date: ____" line drawn at (x, y) in
    10-point Helvetica: the Sign Here tab on the first underline, Date Signed on the second."""
    page.ops.append(b"q 1 1 1 rg BT /F1 10 Tf %g %g Td (%s) Tj ET Q" % (x + 120, y, ANCHOR_SIGN[n].encode()))
    page.ops.append(b"q 1 1 1 rg BT /F1 10 Tf %g %g Td (%s) Tj ET Q" % (x + 340, y, ANCHOR_DATE[n].encode()))


def waiver_pdf(state: dict[str, Any], job_id: str, for_id: str, which: str) -> tuple[bytes, str]:
    import waiver_form
    data, name, refused = waiver_form.for_waiver(state, job_id, for_id, which, for_docusign=True)
    if refused or not data:
        raise _gate()(refused or "No state form for this waiver.")
    return data, name


# ----- filing a completed envelope -----

def _day(at: str) -> str:
    try:
        return datetime.fromisoformat(at.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return date.today().isoformat()


def file_signed(state: dict[str, Any], env: dict[str, Any], info: dict[str, Any], metas: list[dict[str, Any]]) -> dict[str, Any]:
    """The completed envelope on the job: the signed PDF and the certificate as job records, and
    the document marked signed in our own records. A change since the signing (a pay app edited
    while it was being signed, a change order withdrawn) keeps the files and says so in the audit
    trail; nothing is marked signed for a version nobody signed. Returns {record_ids, note}."""
    import domain
    import importer
    job_id, kind = env["job_id"], env["kind"]
    actor = env.get("user_name") or "owner"
    signed, cert = metas
    for meta in metas:
        importer.register_file(state, meta)
    record_kind = {"waiver": "waiver", "co": "change_order"}.get(kind, "other")
    label = env.get("label") or "Signed in DocuSign"

    def file_one(meta: dict[str, Any], subject: str, as_kind: str) -> str:
        item = importer.add_item(state, "upload", text="", subject=subject, file_ids=[meta["id"]], job_id=job_id,
                                 job_reason="was signed in DocuSign")
        return importer.file_item(state, item["id"], job_id, as_kind, actor)["record_ids"][0]

    first = next((s for s in info.get("signers", []) if s.get("recipient_id") == "1"), {}) or {}
    second = next((s for s in info.get("signers", []) if s.get("recipient_id") == "2"), {}) or {}
    signer_name = first.get("name") or (env.get("signer") or {}).get("name") or actor
    note = ""
    record_ids: list[str] = []
    if kind == "payapp":
        # Signed first, then filed: the copy on the record doesn't go with the email, so it leaves
        # the signature's stamp (what Review & send shows) as it is.
        try:
            domain.approve_payapp(state, job_id, actor, env.get("user_id", ""), invoice_id=env.get("invoice_id"), seen=env.get("stamp"),
                                  signature=signer_name, title=env.get("title") or "", user_agent="DocuSign")
            invoice = _draft(_job(state, job_id))
            approval = next((a for a in invoice.get("approvals") or [] if a.get("id") == env.get("user_id")), None)
            if approval is not None:
                approval.update(method="docusign", envelope_id=env["id"], signed_at=first.get("signed_at") or info.get("completed_at") or "")
        except domain.GateError as exc:
            note = f"Signed in DocuSign, but it doesn't count as the approval: {exc}"
        record_ids.append(file_one(signed, f"{label} (signed in DocuSign)", record_kind))
        record_ids.append(file_one(cert, f"{label}: DocuSign certificate of completion", "other"))
        if approval_of(state, env) is not None:
            approval_of(state, env)["record_ids"] = record_ids
    elif kind == "waiver":
        rid = file_one(signed, f"{label} (signed in DocuSign)", "waiver")
        record_ids += [rid, file_one(cert, f"{label}: DocuSign certificate of completion", "other")]
        try:
            if env.get("waiver_kind") in domain.WAIVER_KINDS:
                domain.set_waiver_type(state, job_id, rid, env["waiver_kind"], actor, for_id=env.get("for_id") or "", said=False)
        except domain.GateError as exc:
            note = f"Filed as a lien waiver; say which waiver it is on the job's records: {exc}"
    else:
        rid = file_one(signed, f"{label} (signed in DocuSign)", "change_order")
        record_ids += [rid, file_one(cert, f"{label}: DocuSign certificate of completion", "other")]
        client = second if env.get("client_signer") else {}
        try:
            job, co = domain._pending_co(state, job_id, env["co_id"])
            co["docusign"] = {"envelope_id": env["id"], "record_ids": record_ids, "company_signer": signer_name,
                              "client_signer": client.get("name", ""), "completed_at": info.get("completed_at") or ""}
            if client and client.get("status") == "completed":
                domain.mark_co_signed(state, job_id, env["co_id"], rid, actor, signer_name=client.get("name") or env["client_signer"]["name"],
                                      signed_on=_day(client.get("signed_at") or info.get("completed_at") or ""))
                co["history"][-1]["note"] = f"signed in DocuSign by {signer_name} and {client.get('name')}"
            else:
                co.setdefault("support_record_ids", []).append(rid)
                note = f"{signer_name} signed CO {co['number']} in DocuSign. It still needs the client's signature."
        except domain.GateError as exc:
            note = f"Signed in DocuSign, but not marked signed: {exc}"
    domain.audit(state, job_id, "docusign_completed",
                 f"{label} was signed in DocuSign by {', '.join(s['name'] for s in info.get('signers', []) if s.get('status') == 'completed') or signer_name}"
                 f" (envelope {env['id']}). The signed copy and the certificate of completion are on the job's records."
                 + (f" {note}" if note else ""), actor)
    return {"record_ids": record_ids, "note": note}


def approval_of(state: dict[str, Any], env: dict[str, Any]) -> dict[str, Any] | None:
    job = next((j for j in state.get("jobs", []) if j["id"] == env.get("job_id")), None)
    invoice = next((i for i in (job or {}).get("invoices", []) if i["id"] == env.get("invoice_id")), None)
    return next((a for a in (invoice or {}).get("approvals") or [] if a.get("envelope_id") == env.get("id")), None)
