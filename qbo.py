"""QuickBooks Online, read-only (P2-1, the API half; qbo_csv.py is the CSV fallback).

An owner connects the company's QuickBooks once (OAuth 2). After that Duracash reads, and
never writes:

- payments received, proposed against open pay apps with the same matcher as the CSV import;
  a recorded payment keeps its QuickBooks payment id, so the same payment is never recorded
  twice, however often it is read;
- invoices, for a reconciliation report: pay apps missing from QuickBooks, amounts that differ,
  and pay apps paid on one side but open on the other.

Settings: QBO_CLIENT_ID and QBO_CLIENT_SECRET from the Intuit Developer app, QBO_ENV
(sandbox, the default, or production) and DURACASH_URL, whose /api/qbo/callback must be
listed as a redirect URI in the Intuit app. QBO_TOKEN_URL, QBO_REVOKE_URL and QBO_API_BASE
override Intuit's addresses (tests use a local stand-in).

Intuit's endpoints, scope, field names and minor version below are from memory of their
documentation, not checked against a live sandbox yet. The first sandbox connection is the
test; the stand-in in tests/test_qbo.py shows the shapes this code expects.

Tokens are kept in <data dir>/qbo_connections.json, outside the backups on purpose: after a
restore onto a new disk an owner connects again.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import domain
import qbo_csv
import storage

SCOPE = "com.intuit.quickbooks.accounting"
AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"
API_BASES = {"sandbox": "https://sandbox-quickbooks.api.intuit.com", "production": "https://quickbooks.api.intuit.com"}
MINOR_VERSION = "75"
PAGE = 1000          # QuickBooks' largest page
MAX_PAGES = 20       # 20,000 rows per read is far beyond a small contractor's 90 days
TIMEOUT = 20
STATE_MINUTES = 10

LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()   # one refresh at a time: Intuit retires a refresh token once used
_PENDING: dict[str, dict[str, Any]] = {}   # OAuth state -> {company_id, user, user_id, until}


class QboError(Exception):
    """Something the person should read: not connected, Intuit refused, the network failed."""


def settings() -> dict[str, str]:
    env = "production" if (os.environ.get("QBO_ENV") or "").strip().lower() == "production" else "sandbox"
    return {
        "client_id": (os.environ.get("QBO_CLIENT_ID") or "").strip(),
        "client_secret": (os.environ.get("QBO_CLIENT_SECRET") or "").strip(),
        "env": env,
        "token_url": (os.environ.get("QBO_TOKEN_URL") or TOKEN_URL).strip(),
        "revoke_url": (os.environ.get("QBO_REVOKE_URL") or REVOKE_URL).strip(),
        "api_base": (os.environ.get("QBO_API_BASE") or API_BASES[env]).strip().rstrip("/"),
    }


def configured() -> bool:
    s = settings()
    return bool(s["client_id"] and s["client_secret"])


def _path() -> Path:
    return Path(storage.DATA_DIR) / "qbo_connections.json"


def _all() -> dict[str, Any]:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def connection(company_id: str) -> dict[str, Any] | None:
    with LOCK:
        return _all().get(company_id)


def _store(company_id: str, conn: dict[str, Any] | None) -> None:
    with LOCK:
        data = _all()
        if conn is None:
            data.pop(company_id, None)
        else:
            data[company_id] = conn
        storage.write_json(_path(), data)


def status(company_id: str) -> dict[str, Any]:
    conn = connection(company_id)
    return {
        "configured": configured(),
        "environment": settings()["env"],
        "connected": bool(conn),
        "connected_at": conn.get("connected_at") if conn else None,
        "connected_by": conn.get("connected_by") if conn else None,
    }


# ----- connecting -----

def redirect_ok(redirect_uri: str) -> bool:
    """Intuit sends people back to an https address (or this machine, for trying it out)."""
    return redirect_uri.startswith(("https://", "http://localhost", "http://127.0.0.1"))


def authorize_url(company_id: str, user_name: str, redirect_uri: str, user_id: str = "") -> str:
    if not configured():
        raise QboError("The QuickBooks connection isn't set up on this server (QBO_CLIENT_ID and QBO_CLIENT_SECRET).")
    if not redirect_ok(redirect_uri):
        # The owner reads plain words; the server logs that DURACASH_URL is missing (Oct 23 note 17).
        raise QboError("Connecting QuickBooks isn't available on this server yet. Contact Duracash support. "
                       "Until then, use the CSV import on Payments.")
    state = secrets.token_urlsafe(24)
    now = time.time()
    with LOCK:
        for key in [k for k, v in _PENDING.items() if v["until"] < now]:
            _PENDING.pop(key, None)
        _PENDING[state] = {"company_id": company_id, "user": user_name, "user_id": user_id, "until": now + STATE_MINUTES * 60}
    query = parse.urlencode({"client_id": settings()["client_id"], "response_type": "code", "scope": SCOPE,
                             "redirect_uri": redirect_uri, "state": state})
    return f"{AUTHORIZE_URL}?{query}"


def _token_request(form: dict[str, str]) -> dict[str, Any]:
    s = settings()
    basic = base64.b64encode(f"{s['client_id']}:{s['client_secret']}".encode()).decode()
    req = request.Request(s["token_url"], data=parse.urlencode(form).encode(), method="POST", headers={
        "Authorization": f"Basic {basic}", "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with request.urlopen(req, timeout=TIMEOUT) as res:
            return json.loads(res.read() or b"{}")
    except error.HTTPError as exc:
        raise QboError(f"QuickBooks refused the sign-in ({exc.code}). Connect again.") from None
    except (error.URLError, TimeoutError, ValueError) as exc:
        raise QboError(f"Couldn't reach QuickBooks: {exc}") from None


def _tokens(reply: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if not reply.get("access_token") or not reply.get("refresh_token"):
        raise QboError("QuickBooks didn't send the tokens back. Connect again.")
    conn = dict(old or {})
    conn.update(
        access_token=reply["access_token"],
        access_expires_at=(now + timedelta(seconds=int(reply.get("expires_in") or 3600))).isoformat(timespec="seconds"),
        # Intuit sends a new refresh token on refresh; the old one stops working.
        refresh_token=reply["refresh_token"],
        refresh_expires_at=(now + timedelta(seconds=int(reply.get("x_refresh_token_expires_in") or 8_640_000))).isoformat(timespec="seconds"),
    )
    return conn


def finish(state: str, code: str, realm_id: str, redirect_uri: str, user_id: str = "") -> str:
    """The callback: check the state, and that the browser finishing it is signed in as the person
    who pressed Connect (`user_id`), so someone else's Connect link can't link your QuickBooks to
    their company; then swap the code for tokens and save them. Returns the company id."""
    with LOCK:
        pending = _PENDING.pop(state or "", None)
    if not pending or pending["until"] < time.time():
        raise QboError("That QuickBooks link has expired. Press Connect QuickBooks again.")
    if not user_id or pending.get("user_id") != user_id:
        raise QboError("Sign in to Duracash as the owner who pressed Connect QuickBooks, then press it again.")
    if not code or not realm_id or not realm_id.isdigit():
        raise QboError("QuickBooks didn't say which company was connected. Connect again.")
    reply = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})
    conn = _tokens(reply)
    conn.update(realm_id=realm_id, connected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), connected_by=pending["user"])
    _store(pending["company_id"], conn)
    return pending["company_id"]


def disconnect(company_id: str) -> None:
    # After any refresh in flight, so it can't put the connection back; its newer token is the one revoked.
    with _REFRESH_LOCK:
        conn = connection(company_id)
        _store(company_id, None)
    if conn and configured():
        try:  # best effort: the tokens are gone from here either way
            s = settings()
            basic = base64.b64encode(f"{s['client_id']}:{s['client_secret']}".encode()).decode()
            req = request.Request(s["revoke_url"], data=json.dumps({"token": conn["refresh_token"]}).encode(), method="POST",
                                  headers={"Authorization": f"Basic {basic}", "Accept": "application/json", "Content-Type": "application/json"})
            request.urlopen(req, timeout=TIMEOUT).close()
        except (error.URLError, TimeoutError, OSError):
            pass


def _access_token(company_id: str) -> tuple[str, str]:
    with _REFRESH_LOCK:
        conn = connection(company_id)
        if not conn:
            raise QboError("QuickBooks isn't connected for this company.")
        soon = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(timespec="seconds")
        if conn["access_expires_at"] <= soon:
            if conn.get("refresh_expires_at", "") <= soon:
                _store(company_id, None)
                raise QboError("The QuickBooks connection expired. An owner needs to connect it again.")
            conn = _tokens(_token_request({"grant_type": "refresh_token", "refresh_token": conn["refresh_token"]}), conn)
            _store(company_id, conn)
        return conn["access_token"], conn["realm_id"]


# ----- reading -----

def query(company_id: str, entity: str, where: str = "") -> list[dict[str, Any]]:
    """Every row of one entity (Payment, Invoice), page by page."""
    token, realm = _access_token(company_id)
    rows: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        sql = f"SELECT * FROM {entity}{' WHERE ' + where if where else ''} STARTPOSITION {page * PAGE + 1} MAXRESULTS {PAGE}"
        url = f"{settings()['api_base']}/v3/company/{parse.quote(realm)}/query?" + parse.urlencode({"query": sql, "minorversion": MINOR_VERSION})
        req = request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        try:
            with request.urlopen(req, timeout=TIMEOUT) as res:
                reply = json.loads(res.read() or b"{}")
        except error.HTTPError as exc:
            if exc.code == 401:
                raise QboError("QuickBooks no longer accepts this connection. An owner needs to connect it again.") from None
            raise QboError(f"QuickBooks answered {exc.code} when reading {entity.lower()}s.") from None
        except (error.URLError, TimeoutError, ValueError) as exc:
            raise QboError(f"Couldn't reach QuickBooks: {exc}") from None
        batch = (reply.get("QueryResponse") or {}).get(entity) or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
    return rows


def _cents(amount: Any) -> int | None:
    try:
        return int(round(float(amount) * 100))
    except (TypeError, ValueError):
        return None


def payment_rows(payments: list[dict[str, Any]], invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """QuickBooks payments as rows for qbo_csv.propose, each carrying its QuickBooks id. The
    numbers of the invoices a payment is applied to go into the memo, so a QuickBooks invoice
    numbered like the pay app (PA-OAK-02) matches by number."""
    numbers = {str(inv.get("Id")): str(inv.get("DocNumber") or "") for inv in invoices}
    rows = []
    for p in payments:
        amount = _cents(p.get("TotalAmt"))
        if not amount or amount <= 0:
            continue
        linked = [numbers.get(str(t.get("TxnId")), "") for line in p.get("Line") or [] for t in line.get("LinkedTxn") or []
                  if t.get("TxnType") == "Invoice"]
        rows.append({
            "line": None,
            "date": str(p.get("TxnDate") or "")[:10] or None,
            "ref": str(p.get("PaymentRefNum") or f"QBO {p.get('Id')}")[:60],
            # No number in QuickBooks: "QBO <id>" is made up, so a payment recorded here with no
            # number is found by its customer, amount and day instead (qbo_csv.propose).
            "no_number": not p.get("PaymentRefNum"),
            "customer": str((p.get("CustomerRef") or {}).get("name") or "")[:120],
            "amount_cents": amount,
            "memo": " ".join([str(p.get("PrivateNote") or ""), *[n for n in linked if n]])[:200],
            "qbo_id": str(p.get("Id") or ""),
            "qbo_total_cents": amount,   # the whole payment, kept when it is split over pay apps
        })
    return rows


def recorded_parts(state: dict[str, Any]) -> dict[str, list[int]]:
    """QuickBooks payment id -> the amounts recorded under it (more than one when it was split)."""
    parts: dict[str, list[int]] = {}
    for j in state.get("jobs", []):
        for p in j.get("payments", []):
            if p.get("qbo_payment_id"):
                parts.setdefault(p["qbo_payment_id"], []).append(p["amount_cents"])
    return parts


def propose(state: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The CSV matcher, plus the QuickBooks id check that makes reading again harmless. A payment
    recorded in part (split over pay apps) comes back as its rest, with "(rest)" after the
    reference as the split gave it."""
    parts = recorded_parts(state)
    left = []   # (row as proposed, cents of it already recorded, whether that is all of it)
    for row in rows:
        got = parts.get(row.get("qbo_id") or "", [])
        whole = bool(got) and sum(got) >= row["amount_cents"]
        if got and not whole:
            row = {**row, "amount_cents": row["amount_cents"] - sum(got), "ref": (row["ref"] + " (rest)" * len(got))[:60]}
        left.append((row, sum(got), whole))
    out = []
    for (row, recorded, whole), result in zip(left, qbo_csv.propose(state, [r for r, _, _ in left])):
        if whole:
            result.update(status="already recorded", reason="This QuickBooks payment is already in Duracash.",
                          job_id=None, invoice_id=None, number=None)
        elif recorded and result["status"] != "already recorded":
            result["reason"] = f"The rest of this payment: {domain.money(recorded)} of it is already recorded. {result['reason']}"
        out.append(result)
    return out


def read_payments(company_id: str, state: dict[str, Any], days: int = 90) -> dict[str, Any]:
    since = (date.today() - timedelta(days=max(1, min(days, 730)))).isoformat()
    payments = query(company_id, "Payment", f"TxnDate >= '{since}'")
    invoices = query(company_id, "Invoice", f"TxnDate >= '{(date.today() - timedelta(days=730)).isoformat()}'")
    rows = payment_rows(payments, invoices)
    return {"rows": propose(state, rows), "warnings": [] if rows else [f"No payments in QuickBooks since {since}."]}


def reconcile(state: dict[str, Any], invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Where Duracash and QuickBooks disagree about sent pay apps. Changes nothing."""
    payers = {p["id"]: p for p in state.get("payers", [])}
    by_number = {str(inv.get("DocNumber") or ""): inv for inv in invoices if inv.get("DocNumber")}
    unused = list(invoices)
    out = []
    for job in state.get("jobs", []):
        payer = qbo_csv._norm(payers.get(job.get("payer_id"), {}).get("name", ""))
        for inv in job.get("invoices", []):
            if inv.get("status") not in {"sent", "approved", "partial", "paid"}:
                continue
            row = {"job_id": job["id"], "job": job["name"], "number": inv["number"], "invoice_id": inv["id"]}
            hit = by_number.get(inv["number"])
            if not hit:  # no number in common: the same customer and amount, once
                same = [q for q in unused if _cents(q.get("TotalAmt")) == inv["amount_cents"]
                        and payer and f" {payer} " in f" {qbo_csv._norm(str((q.get('CustomerRef') or {}).get('name') or ''))} "]
                hit = same[0] if len(same) == 1 else None
            if not hit:
                out.append({**row, "kind": "missing", "detail": f"{inv['number']} ({domain.money(inv['amount_cents'])}) isn't in QuickBooks."})
                continue
            if hit in unused:
                unused.remove(hit)
            total, balance = _cents(hit.get("TotalAmt")), _cents(hit.get("Balance"))
            label = f"QuickBooks invoice {hit.get('DocNumber') or hit.get('Id')}"
            if total is not None and total != inv["amount_cents"]:
                out.append({**row, "kind": "amount", "detail": f"{inv['number']} is {domain.money(inv['amount_cents'])}; {label} is {domain.money(total)}."})
            if balance == 0 and inv["status"] != "paid":
                out.append({**row, "kind": "paid_in_qbo", "detail": f"{label} is paid; {inv['number']} is still open here. Read payments from QuickBooks."})
            elif balance and balance > 0 and inv["status"] == "paid":
                out.append({**row, "kind": "open_in_qbo", "detail": f"{inv['number']} is paid here; {label} still shows {domain.money(balance)} owed."})
    return out


# ----- the pay app as a QuickBooks invoice (R4-11) -----

# QuickBooks Online's invoice import (Sales > Invoices > Import): one row per line, the invoice's
# own fields repeated on each. Dates as MM/DD/YYYY, the US company default. Writing to QuickBooks
# stays with the person: Duracash only hands over the file.
INVOICE_COLUMNS = ["InvoiceNo", "Customer", "InvoiceDate", "DueDate", "Item", "ItemDescription", "ItemAmount"]
EXPORTABLE = {"sent", "approved", "partial", "paid"}


def _qb_date(iso: Any) -> str:
    try:
        return date.fromisoformat(str(iso or "")[:10]).strftime("%m/%d/%Y")
    except ValueError:
        return ""


def _cell(text: Any) -> str:
    """A text cell a spreadsheet won't run as a formula."""
    text = " ".join(str(text or "").split())
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _item(line: dict[str, Any]) -> str:
    if line.get("co_id"):
        return "Change order"
    if line["amount_cents"] < 0 and line["label"].startswith("Less retainage"):
        return "Retainage"
    if line["label"].startswith("Overhead and profit"):
        return "Overhead and profit"
    return "Contract work"


def invoice_rows(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> list[dict[str, str]]:
    """The pay app's lines in INVOICE_COLUMNS: retainage is its own negative line, so the
    amounts add up to what the customer owes on the pay app."""
    if invoice.get("status") == "void":
        raise domain.GateError(f"{invoice['number']} was deleted or pulled back, so there is nothing to put in QuickBooks.")
    if invoice.get("status") not in EXPORTABLE:
        raise domain.GateError(f"{invoice['number']} hasn't gone to the customer, so it isn't ready for QuickBooks. Send it first.")
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    issued = _qb_date(invoice.get("sent_at") or invoice.get("created_at"))
    head = {"InvoiceNo": _cell(invoice["number"]), "Customer": _cell(payer.get("name", "")),
            "InvoiceDate": issued, "DueDate": _qb_date(invoice.get("due_on")) or issued}
    lines = invoice.get("lines") or [{"label": "Pay app " + invoice["number"], "amount_cents": invoice["amount_cents"]}]
    return [{**head, "Item": _item(line), "ItemDescription": _cell(f"{job['name']}: {line['label']}"),
             "ItemAmount": f"{line['amount_cents'] / 100:.2f}"} for line in lines]


def invoice_csv(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=INVOICE_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(invoice_rows(state, job, invoice))
    return out.getvalue()
