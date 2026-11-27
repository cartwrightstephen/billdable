"""QuickBooks Online, both ways: a sent pay app goes to QuickBooks as an invoice, and payments
recorded in QuickBooks against those invoices come back as payments here (Nov 27). qbo.py keeps
the connection (OAuth, tokens) and the read-only payment matcher; this module adds the calls that
write, and the automatic match by QuickBooks invoice id.

Sending a pay app (push):

- The client becomes a QuickBooks Customer: the one already linked to it (its id is kept on the
  client), else one with the same display name, else one with the same email, else a new one.
- The pay app becomes one Invoice. Each line of the pay app (schedule-of-values progress this
  period, overhead and profit, unsigned change orders billed with it) is one invoice line, all on
  the one billing Item chosen in settings, so the income account is the Item's. The lines are the
  ones the pay app was sent with, which already add up to what it asks for; a credit change order
  has already lowered them. Retainage withheld is one negative line on the Retainage Item chosen
  in settings, which in QuickBooks should point at an Other Current Asset account (Retainage
  Receivable): the invoice total is what is due now, income is the whole of the work billed, and
  what is held back waits as an asset until it is billed on release (docs/setup-integrations.md).
- The invoice's QuickBooks id and SyncToken are kept on the pay app, so sending it again updates
  that invoice rather than making a second one. An invoice changed in QuickBooks since (its
  total is no longer what Duracash sent) is not overwritten until the person says so.

Reading back (sync): every QuickBooks Payment whose lines are linked (LinkedTxn, TxnType
Invoice) to an invoice sent from here is recorded on that pay app through domain.match_payment,
the same path and audit trail as Record payment, once per payment and pay app. Sync now on the
QuickBooks card, and once a day from the hourly loop (monthly.py).

Every call carries minorversion (qbo.MINOR_VERSION), retries 429 and 5xx with backoff (Retry-After
when Intuit sends one), refreshes the access token once on a 401, and sends a requestid on writes
so a retried create can't make two. Intuit's validation errors are passed on in plain words.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib import error, parse, request

import billing
import domain
import qbo
import storage

TRIES = 4             # one call and three retries
BACKOFF = 2.0         # seconds before the first retry, doubled each time (tests set 0)
MAX_WAIT = 60         # Intuit's advice after a 429 is to wait a minute
PAGE = 1000
MAX_PAGES = 20
NAME_MAX = 100        # a QuickBooks display name
DOC_NUMBER_MAX = 21   # QuickBooks' invoice number
SYNC_PATH_NAME = "qbo_sync_days.json"

_PUSH_LOCKS: dict[str, threading.Lock] = {}
_PUSH_LOCKS_LOCK = threading.Lock()

# Intuit's error codes worth saying in our own words (Intuit: handling common errors).
STALE, NOT_FOUND, DUPLICATE_NAME, INVALID_REF = "5010", "610", "6240", "2500"


class QboApiError(qbo.QboError):
    """QuickBooks answered with an error; `code` is Intuit's (5010, 6240, ...) when it gave one."""

    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code, self.status = code, status


# ----- talking to QuickBooks -----

def _wait(attempt: int, retry_after: str | None) -> float:
    try:
        seconds = float(retry_after) if retry_after else BACKOFF * (2 ** attempt)
    except ValueError:
        seconds = BACKOFF * (2 ** attempt)
    return max(0.0, min(MAX_WAIT, seconds))


def _plain(reply: Any, status: int, what: str) -> QboApiError:
    """Intuit's Fault ({"Fault": {"Error": [{"Message", "Detail", "code"}], "type"}}) in plain words."""
    fault = reply.get("Fault") if isinstance(reply, dict) else None
    errors = (fault or {}).get("Error") or []
    first = errors[0] if errors and isinstance(errors[0], dict) else {}
    code = str(first.get("code") or "")
    detail = " ".join(str(first.get("Detail") or first.get("Message") or "").split())[:300]
    words = {
        DUPLICATE_NAME: "QuickBooks already uses that name for a client, vendor or employee (it may be inactive). "
                        "Link this client to its record in QuickBooks on the QuickBooks card, or rename one of them.",
        STALE: "Someone changed this in QuickBooks while Duracash was sending it. Press it again.",
        NOT_FOUND: "QuickBooks can't find it; it may have been deleted there.",
        INVALID_REF: "QuickBooks doesn't know an item or client Duracash named; it may be inactive or deleted. "
                     "Choose the items again on the QuickBooks card.",
    }
    text = words.get(code) or f"QuickBooks refused {what}."
    if detail and code not in words:
        text += f" QuickBooks says: {detail}"
    return QboApiError(text, code, status)


def _refreshed(company_id: str, used: str) -> str:
    """A new access token after QuickBooks answered 401 to `used`, unless another request
    refreshed it meanwhile (Intuit retires a refresh token once used, so one refresh at a time)."""
    with qbo._REFRESH_LOCK:
        conn = qbo.connection(company_id)
        if not conn:
            raise qbo.QboError("QuickBooks isn't connected for this company.")
        if conn.get("access_token") != used:
            return conn["access_token"]
        conn = qbo._tokens(qbo._token_request({"grant_type": "refresh_token", "refresh_token": conn["refresh_token"]}), conn)
        qbo._store(company_id, conn)
        return conn["access_token"]


def call(company_id: str, method: str, path: str, body: dict | None = None, params: dict | None = None,
         what: str = "the request", request_id: str = "") -> dict[str, Any]:
    """One QuickBooks API call: /v3/company/<realm>/<path>, JSON both ways."""
    token, realm = qbo._access_token(company_id)
    query = {**(params or {}), "minorversion": qbo.MINOR_VERSION, **({"requestid": request_id[:50]} if request_id else {})}
    url = f"{qbo.settings()['api_base']}/v3/company/{parse.quote(realm)}/{path}?{parse.urlencode(query)}"
    data = json.dumps(body).encode() if body is not None else None
    # A write is retried only with a requestid: Intuit answers a repeated one with the first answer.
    may_retry = method == "GET" or bool(request_id)
    refreshed = False
    attempt = 0
    while True:
        req = request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json", **({"Content-Type": "application/json"} if data else {})})
        try:
            with request.urlopen(req, timeout=qbo.TIMEOUT) as res:
                reply = json.loads(res.read() or b"{}")
            if isinstance(reply, dict) and reply.get("Fault"):
                raise _plain(reply, 200, what)
            return reply
        except error.HTTPError as exc:
            try:
                reply = json.loads(exc.read() or b"{}")
            except (ValueError, OSError):
                reply = {}
            if exc.code == 401 and not refreshed:
                token, refreshed = _refreshed(company_id, token), True
                continue
            if exc.code == 401:
                raise QboApiError("QuickBooks no longer accepts this connection. An owner needs to connect it again.", status=401) from None
            if exc.code == 403:
                raise QboApiError("QuickBooks refused access (403). Check that QBO_ENVIRONMENT matches the keys and the company connected.",
                                  status=403) from None
            if (exc.code == 429 or exc.code >= 500) and may_retry and attempt + 1 < TRIES:
                time.sleep(_wait(attempt, exc.headers.get("Retry-After") if exc.headers else None))
                attempt += 1
                continue
            if exc.code == 429:
                raise QboApiError("QuickBooks is busy (too many requests). Try again in a minute.", status=429) from None
            if exc.code >= 500:
                raise QboApiError(f"QuickBooks had a problem ({exc.code}). Try again in a few minutes.", status=exc.code) from None
            raise _plain(reply, exc.code, what) from None
        except (error.URLError, TimeoutError) as exc:
            if may_retry and attempt + 1 < TRIES:
                time.sleep(_wait(attempt, None))
                attempt += 1
                continue
            raise QboApiError(f"Couldn't reach QuickBooks: {exc}") from None
        except ValueError:
            raise QboApiError("QuickBooks sent back something Duracash couldn't read.") from None


def quote(text: str) -> str:
    """A value inside a QuickBooks query's quotes: backslash before an apostrophe (Intuit: data queries)."""
    return str(text).replace("\\", "\\\\").replace("'", "\\'")


def select(company_id: str, entity: str, where: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        sql = f"SELECT * FROM {entity}{' WHERE ' + where if where else ''} STARTPOSITION {page * PAGE + 1} MAXRESULTS {PAGE}"
        reply = call(company_id, "GET", "query", params={"query": sql}, what=f"the {entity.lower()} list")
        batch = (reply.get("QueryResponse") or {}).get(entity) or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
    return rows


def _cents(amount: Any) -> int:
    try:
        return int(round(float(amount) * 100))
    except (TypeError, ValueError):
        return 0


def _dollars(cents: int) -> float:
    return round(cents / 100, 2)


def _norm(text: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9@.]+", " ", str(text or "").lower()).split())


# ----- what QuickBooks has: items and customers, for settings and linking -----

ITEM_TYPES = {"Service", "NonInventory"}


def items(company_id: str) -> list[dict[str, Any]]:
    out = []
    for it in select(company_id, "Item", "Active = true"):
        if it.get("Type") not in ITEM_TYPES:
            continue
        out.append({"id": str(it.get("Id") or ""), "name": str(it.get("FullyQualifiedName") or it.get("Name") or "")[:200],
                    "type": it.get("Type"), "account": str((it.get("IncomeAccountRef") or {}).get("name") or "")[:200]})
    return sorted(out, key=lambda i: i["name"].lower())


def _customer_row(c: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(c.get("Id") or ""), "name": str(c.get("DisplayName") or "")[:200],
            "email": str((c.get("PrimaryEmailAddr") or {}).get("Address") or "")[:200]}


def customers(company_id: str) -> list[dict[str, Any]]:
    return sorted((_customer_row(c) for c in select(company_id, "Customer", "Active = true")), key=lambda c: c["name"].lower())


def suggest_links(state: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, str]:
    """Client id -> QuickBooks customer id for clients not linked yet: same name, else same email, when only one fits."""
    out = {}
    for payer in state.get("payers", []):
        if payer.get("qbo_customer_id"):
            continue
        same = [c for c in rows if _norm(c["name"]) and _norm(c["name"]) == _norm(payer.get("name"))]
        if not same and payer.get("ap_email"):
            same = [c for c in rows if c["email"] and c["email"].strip().lower() == str(payer["ap_email"]).strip().lower()]
        if len(same) == 1:
            out[payer["id"]] = same[0]["id"]
    return out


# ----- settings: the Items pay apps go in on -----

def mapping(state: dict[str, Any]) -> dict[str, Any]:
    return dict((state.get("company") or {}).get("qbo") or {})


def save_mapping(state: dict[str, Any], billing: dict[str, str] | None, retainage: dict[str, str] | None, actor: str) -> dict[str, Any]:
    if not billing or not billing.get("id"):
        raise domain.GateError("Choose the QuickBooks item pay apps are billed on.")
    if retainage and retainage.get("id") == billing["id"]:
        raise domain.GateError("Choose a different item for retainage: it should point at a Retainage Receivable account, not income.")
    company = state.setdefault("company", {})
    old = company.get("qbo") or {}
    company["qbo"] = {**old, "billing_item": {"id": billing["id"], "name": billing.get("name", "")[:200]},
                      "retainage_item": {"id": retainage["id"], "name": retainage.get("name", "")[:200]} if retainage and retainage.get("id") else None,
                      "updated_by": actor, "updated_at": domain.utcnow()}
    ret = f" and retainage on {retainage.get('name')}" if retainage and retainage.get("id") else ""
    domain.audit(state, "", "qbo_settings", f"{actor} set QuickBooks billing to the item {billing.get('name')}{ret}.", actor)
    return company["qbo"]


def link_client(state: dict[str, Any], payer_id: str, customer: dict[str, str] | None, actor: str) -> None:
    payer = next((p for p in state.get("payers", []) if p["id"] == payer_id), None)
    if not payer:
        raise domain.GateError("That client isn't here any more.")
    if customer:
        payer["qbo_customer_id"], payer["qbo_customer_name"] = customer["id"], customer.get("name", "")[:200]
        domain.audit(state, "", "qbo_linked", f"{actor} linked {payer['name']} to {customer.get('name')} in QuickBooks.", actor)
    else:
        payer.pop("qbo_customer_id", None)
        payer.pop("qbo_customer_name", None)
        domain.audit(state, "", "qbo_linked", f"{actor} unlinked {payer['name']} from QuickBooks.", actor)


# ----- sending a pay app -----

PUSHABLE = qbo.EXPORTABLE   # sent, approved, partial, paid


def _find(state: dict[str, Any], job_id: str, invoice_id: str) -> tuple[dict, dict, dict]:
    job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
    invoice = next((i for i in (job or {}).get("invoices", []) if i["id"] == invoice_id), None)
    if not job or not invoice:
        raise domain.GateError("That pay app isn't here any more.")
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), None) or {}
    return job, invoice, payer


def invoice_lines(invoice: dict[str, Any], job: dict[str, Any], billing_id: str, retainage_id: str | None) -> list[dict[str, Any]]:
    """The pay app's lines as QuickBooks SalesItemLineDetail lines. They add up to the pay app's
    amount (refused otherwise, so QuickBooks never shows another figure)."""
    lines = list(invoice.get("lines") or [])
    if not lines:   # a pay app from before lines were kept: the work, then retainage
        lines = [{"label": f"Pay app {invoice['number']}", "amount_cents": invoice.get("gross_cents") or invoice["amount_cents"]}]
        if invoice.get("retainage_cents"):
            lines.append({"label": "Less retainage", "amount_cents": -int(invoice["retainage_cents"])})
    out = []
    for line in lines:
        cents = int(line["amount_cents"])
        if not cents:
            continue
        held = cents < 0 and str(line.get("label", "")).startswith("Less retainage")
        if held and not retainage_id:
            raise domain.GateError(f"{invoice['number']} holds back retainage. Choose the QuickBooks retainage item on the QuickBooks card first.")
        item = retainage_id if held else billing_id
        out.append({"DetailType": "SalesItemLineDetail", "Amount": _dollars(cents),
                    "Description": " ".join(f"{job['name']}: {line.get('label', '')}".split())[:4000],
                    "SalesItemLineDetail": {"ItemRef": {"value": item}, "Qty": 1, "UnitPrice": _dollars(cents)}})
    if sum(_cents(l["Amount"]) for l in out) != invoice["amount_cents"]:
        raise domain.GateError(f"{invoice['number']}'s lines don't add up to its amount, so it wasn't sent to QuickBooks. Contact Duracash support.")
    return out


def invoice_body(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any], customer_id: str) -> dict[str, Any]:
    m = mapping(state)
    billing = (m.get("billing_item") or {}).get("id")
    if not billing:
        raise domain.GateError("Choose the QuickBooks item pay apps are billed on, on the QuickBooks card, first.")
    retainage = (m.get("retainage_item") or {}).get("id")
    issued = str(invoice.get("sent_at") or invoice.get("created_at") or "")[:10]
    body = {
        "CustomerRef": {"value": customer_id},
        "DocNumber": invoice["number"][:DOC_NUMBER_MAX],
        "Line": invoice_lines(invoice, job, billing, retainage),
        "PrivateNote": f"From Duracash: pay app {invoice['number']}, {job['name']}. Change it in Duracash and send it again."[:4000],
        "CustomerMemo": {"value": f"{job['name']}, pay app {invoice['number']}"
                                  + (f", period to {invoice['period_to']}" if invoice.get("period_to") else "")},
    }
    if issued:
        body["TxnDate"] = issued
    if invoice.get("due_on"):
        body["DueDate"] = invoice["due_on"]
    return body


def _customer(company_id: str, payer: dict[str, Any], request_id: str) -> tuple[str, str, str]:
    """(QuickBooks customer id, its name, how: linked | matched | created)."""
    linked = str(payer.get("qbo_customer_id") or "")
    if linked:
        try:
            found = call(company_id, "GET", f"customer/{parse.quote(linked)}", what="the client").get("Customer") or {}
            if found.get("Active", True):
                return linked, str(found.get("DisplayName") or payer.get("qbo_customer_name") or ""), "linked"
        except QboApiError as exc:
            if exc.code != NOT_FOUND and exc.status not in (400, 404):
                raise
    name = " ".join(str(payer.get("name") or "").split())[:NAME_MAX]
    if not name:
        raise domain.GateError("This client has no name, so it can't be added to QuickBooks.")
    rows = customers(company_id)
    hit = [c for c in rows if _norm(c["name"]) == _norm(name)]
    email = str(payer.get("ap_email") or "").strip().lower()
    if not hit and email:
        hit = [c for c in rows if c["email"].strip().lower() == email]
    if len(hit) == 1:
        return hit[0]["id"], hit[0]["name"], "matched"
    if len(hit) > 1:
        raise domain.GateError(f"QuickBooks has more than one client that could be {name}. Link the client to one on the QuickBooks card.")
    body: dict[str, Any] = {"DisplayName": name, "CompanyName": name}
    if email:
        body["PrimaryEmailAddr"] = {"Address": email}
    made = call(company_id, "POST", "customer", body, what=f"the new client {name}", request_id=request_id + "c").get("Customer") or {}
    if not made.get("Id"):
        raise QboApiError("QuickBooks didn't say which client record it made. Try again.")
    return str(made["Id"]), str(made.get("DisplayName") or name), "created"


def _push_lock(company_id: str) -> threading.Lock:
    with _PUSH_LOCKS_LOCK:
        return _PUSH_LOCKS.setdefault(company_id, threading.Lock())


def push(company_id: str, state: dict[str, Any], job_id: str, invoice_id: str, overwrite: bool = False) -> dict[str, Any]:
    """Send one pay app to QuickBooks. `state` is a copy read before; nothing here changes it.
    Returns what apply_push keeps. The caller holds push_lock(company_id) through apply_push."""
    job, invoice, payer = _find(state, job_id, invoice_id)
    if invoice.get("status") == "void":
        raise domain.GateError(f"{invoice['number']} was deleted or pulled back, so there is nothing to put in QuickBooks.")
    if invoice.get("status") not in PUSHABLE:
        raise domain.GateError(f"{invoice['number']} hasn't gone to the client, so it isn't ready for QuickBooks. Send it first.")
    invoice_body(state, job, invoice, "check")   # settings and lines, before anything is sent
    request_id = uuid.uuid4().hex[:40]
    customer_id, customer_name, how = _customer(company_id, payer, request_id)
    body = invoice_body(state, job, invoice, customer_id)
    kept = invoice.get("qbo") or {}
    current = None
    if kept.get("invoice_id"):
        try:
            current = call(company_id, "GET", f"invoice/{parse.quote(kept['invoice_id'])}", what="the invoice").get("Invoice")
        except QboApiError as exc:
            if exc.code != NOT_FOUND and exc.status not in (400, 404):
                raise
            current = None   # deleted in QuickBooks: a new one is made
    if not current:
        # Sent before from here, or imported from the CSV: the same number for the same customer is that invoice.
        same = select(company_id, "Invoice", f"DocNumber = '{quote(body['DocNumber'])}'")
        current = next((i for i in same if str((i.get("CustomerRef") or {}).get("value")) == customer_id), None)
        if current and not kept.get("invoice_id"):
            kept = {**kept, "total_cents": _cents(current.get("TotalAmt"))}
    if current and not overwrite:
        total = _cents(current.get("TotalAmt"))
        if kept.get("total_cents") is not None and total != kept["total_cents"] and total != invoice["amount_cents"]:
            raise QboApiError(f"QuickBooks invoice {current.get('DocNumber') or current.get('Id')} was changed in QuickBooks: it is now "
                              f"{domain.money(total)}. Send again to replace it with {invoice['number']} ({domain.money(invoice['amount_cents'])}).",
                              code="changed")
    if current:
        body.update(Id=str(current["Id"]), SyncToken=str(current.get("SyncToken") or "0"), sparse=True)
        saved = call(company_id, "POST", "invoice", body, what=f"invoice {invoice['number']}", request_id=request_id + "u").get("Invoice") or {}
        action = "updated"
    else:
        saved = call(company_id, "POST", "invoice", body, what=f"invoice {invoice['number']}", request_id=request_id + "i").get("Invoice") or {}
        action = "created"
    if not saved.get("Id"):
        raise QboApiError("QuickBooks didn't say which invoice it saved. Check QuickBooks before sending again.")
    return {"job_id": job_id, "invoice_id": invoice_id, "payer_id": payer.get("id"), "customer": {"id": customer_id, "name": customer_name, "how": how},
            "qbo": {"invoice_id": str(saved["Id"]), "sync_token": str(saved.get("SyncToken") or ""), "doc_number": str(saved.get("DocNumber") or body["DocNumber"]),
                    "total_cents": _cents(saved.get("TotalAmt")) if saved.get("TotalAmt") is not None else invoice["amount_cents"],
                    "customer_id": customer_id},
            "action": action}


def apply_push(state: dict[str, Any], result: dict[str, Any], actor: str) -> dict[str, Any]:
    """Keep what QuickBooks said: the invoice's id and SyncToken on the pay app, the customer on the client."""
    job, invoice, payer = _find(state, result["job_id"], result["invoice_id"])
    if payer:
        payer["qbo_customer_id"], payer["qbo_customer_name"] = result["customer"]["id"], result["customer"]["name"][:200]
    invoice["qbo"] = {**result["qbo"], "pushed_at": domain.utcnow(), "pushed_by": actor}
    customer = {"created": f", for {result['customer']['name']}, new in QuickBooks",
                "matched": f", for {result['customer']['name']} in QuickBooks"}.get(result["customer"]["how"], "")
    domain.audit(state, job["id"], "qbo_pushed",
                 f"{actor} sent {invoice['number']} ({domain.money(invoice['amount_cents'])}) to QuickBooks: invoice "
                 f"{invoice['qbo']['doc_number']} {result['action']}{customer}.", actor)
    return invoice["qbo"]


# ----- reading payments back -----

def pushed(state: dict[str, Any]) -> dict[str, tuple[dict, dict]]:
    """QuickBooks invoice id -> (job, pay app) for every pay app sent to QuickBooks."""
    return {inv["qbo"]["invoice_id"]: (job, inv) for job in state.get("jobs", []) for inv in job.get("invoices", [])
            if (inv.get("qbo") or {}).get("invoice_id")}


def open_pushed(state: dict[str, Any]) -> list[dict]:
    return [inv for job, inv in pushed(state).values() if inv.get("status") in domain.OPEN_FOR_PAYMENT and not job.get("closed")]


def fetch_payments(company_id: str, state: dict[str, Any]) -> list[dict[str, Any]]:
    """QuickBooks payments that could be for an open pay app sent from here (none to ask about: no call)."""
    waiting = open_pushed(state)
    if not waiting:
        return []
    first = min(str(inv.get("sent_at") or inv.get("created_at") or date.today().isoformat())[:10] for inv in waiting)
    try:
        since = (date.fromisoformat(first) - timedelta(days=31)).isoformat()
    except ValueError:
        since = (date.today() - timedelta(days=730)).isoformat()
    return select(company_id, "Payment", f"TxnDate >= '{since}'")


def apply_payments(state: dict[str, Any], payments: list[dict[str, Any]], actor: str = "QuickBooks") -> dict[str, Any]:
    """Record each payment line linked to a pay app sent from here, once per payment and pay app,
    through domain.match_payment (its checks and audit). Returns what was recorded and what wasn't."""
    ours = pushed(state)
    recorded, problems = [], []
    for p in payments:
        pid = str(p.get("Id") or "")
        links = [(_cents(line.get("Amount")), str(t.get("TxnId")))
                 for line in p.get("Line") or [] for t in line.get("LinkedTxn") or []
                 if t.get("TxnType") == "Invoice" and str(t.get("TxnId")) in ours]
        if not pid or not links:
            continue
        total = _cents(p.get("TotalAmt"))
        for cents, txn in links:
            job, inv = ours[txn]
            if any(x.get("qbo_payment_id") == pid and x.get("invoice_id") == inv["id"] for x in job.get("payments", [])):
                continue   # already here
            if cents <= 0 or inv.get("status") not in domain.OPEN_FOR_PAYMENT or job.get("closed"):
                continue
            ref = str(p.get("PaymentRefNum") or f"QBO {pid}")
            if len(links) > 1:
                ref = f"{ref} {inv['number']}"
            try:
                domain.match_payment(state, job["id"], cents, ref[:60], actor, invoice_id=inv["id"],
                                     received_on=str(p.get("TxnDate") or "")[:10] or None, qbo_payment_id=pid, qbo_total_cents=total or None)
                recorded.append({"job_id": job["id"], "number": inv["number"], "amount_cents": cents, "reference": ref[:60]})
            except domain.GateError as exc:
                problems.append({"job_id": job["id"], "number": inv["number"], "amount_cents": cents, "error": str(exc)})
    if recorded or problems:
        said = "; ".join(f"{r['number']} {domain.money(r['amount_cents'])}" for r in recorded) or "nothing"
        left = (" Not recorded: " + "; ".join(f"{x['number']} ({x['error']})" for x in problems)) if problems else ""
        domain.audit(state, "", "qbo_synced", f"QuickBooks sync by {actor} recorded {said}.{left}"[:1000], actor)
    return {"recorded": recorded, "problems": problems, "checked": len(payments)}


# ----- once a day, from the hourly loop -----

def run_daily(site, now: datetime | None = None) -> list[str]:
    """Each connected company with an open pay app sent to QuickBooks: read payments and record
    those linked to it, once a day (<data dir>/qbo_sync_days.json). A failure is retried next hour;
    a read-only company (billing.read_only_why) is skipped."""
    if not qbo.configured() or not hasattr(site, "mutate"):
        return []
    day = (now or datetime.now(timezone.utc)).date().isoformat()
    path = storage.DATA_DIR / SYNC_PATH_NAME
    try:
        done = json.loads(path.read_text())
        done = done if isinstance(done, dict) else {}
    except (OSError, ValueError):
        done = {}
    lines = []
    deleted = site.deleted()
    for company_id in site.company_ids():
        # status, not connection: it decrypts nothing, so tokens this server can't open don't stop the loop
        if done.get(company_id) == day or company_id in deleted or not qbo.status(company_id)["connected"]:
            continue
        try:
            state = site.read(company_id)
            if state is None or billing.read_only_why(state):   # a read-only company changes nothing
                continue
            payments = fetch_payments(company_id, state)
            if payments:
                report = site.mutate(company_id, lambda st: apply_payments(st, payments, "QuickBooks"))
                if report and (report["recorded"] or report["problems"]):
                    lines.append(f"{company_id}  QuickBooks sync: {len(report['recorded'])} recorded, {len(report['problems'])} not")
        except qbo.QboError as exc:
            site.log("warn", msg=f"{company_id}: QuickBooks sync: {exc}")
            continue
        except Exception as exc:  # one company's failure never stops the others
            site.log("error", msg=f"{company_id}: QuickBooks sync failed: {exc.__class__.__name__}: {exc}")
            continue
        done[company_id] = day
        storage.write_json(path, done)
    return lines
