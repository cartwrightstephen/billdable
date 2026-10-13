"""Billing with Stripe (P2-2). Off until STRIPE_SECRET_KEY and STRIPE_PRICE_ID are set.

- Subscribe: a Stripe Checkout page for the one plan (STRIPE_PRICE_ID). While the free trial
  runs (trial.py), the subscription starts with Stripe's trial ending on the trial's last day.
- The business plan ends the trial when the first pay app sent through Billdable is paid, which
  is an event, not a date: when that happens, Billdable asks Stripe to end the trial now.
- Manage billing: Stripe's Customer Portal (card, invoices, cancel).
- Stripe tells Billdable what happened through a signed webhook (STRIPE_WEBHOOK_SECRET); the
  subscription's status is kept on the company.
- Enforcement is read-only, never deletion, and only with BILLING_ENFORCE=1: an unpaid or
  cancelled subscription, or a trial over BILLING_GRACE_DAYS (14) past its end with no
  subscription, makes the company read-only (server.read_only_reason). "Past due" (Stripe still
  retrying the card) only warns. Owners can always open Billing to fix it.

Stripe's parameter and field names below are from their documentation as remembered and are
checked only against a stand-in (tests/test_billing.py); the first test-mode checkout is the
real check. STRIPE_API_BASE overrides api.stripe.com for that stand-in.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import error, parse, request

import trial

API_BASE = "https://api.stripe.com"
TIMEOUT = 20
SIGNATURE_TOLERANCE = 300        # seconds; Stripe's own libraries use five minutes
MIN_TRIAL_SECONDS = 2 * 24 * 3600  # Checkout refuses a trial_end less than 48 hours away
HOLD_STATUSES = {"unpaid", "canceled", "incomplete_expired"}
WARN_STATUSES = {"past_due", "incomplete"}
GOOD_STATUSES = {"trialing", "active"}
EVENTS_KEPT = 50


class BillingError(Exception):
    """Something the person should read."""


def settings() -> dict[str, Any]:
    try:
        grace = max(0, int(os.environ.get("BILLING_GRACE_DAYS") or 14))
    except ValueError:
        grace = 14
    return {
        "key": (os.environ.get("STRIPE_SECRET_KEY") or "").strip(),
        "price": (os.environ.get("STRIPE_PRICE_ID") or "").strip(),
        "webhook_secret": (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip(),
        "api_base": (os.environ.get("STRIPE_API_BASE") or API_BASE).strip().rstrip("/"),
        "enforce": os.environ.get("BILLING_ENFORCE") == "1",
        "grace_days": grace,
    }


def configured() -> bool:
    s = settings()
    return bool(s["key"] and s["price"])


def _form(data: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's form encoding: nested keys as a[b][0][c]."""
    out: list[tuple[str, str]] = []
    for key, value in data.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out += _form(value, name)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                out += _form(item, f"{name}[{i}]") if isinstance(item, dict) else [(f"{name}[{i}]", str(item))]
        elif value is not None:
            out.append((name, str(value).lower() if isinstance(value, bool) else str(value)))
    return out


def _post(path: str, data: dict[str, Any]) -> dict[str, Any]:
    s = settings()
    if not s["key"]:
        raise BillingError("Billing isn't set up on this server.")
    req = request.Request(f"{s['api_base']}{path}", data=parse.urlencode(_form(data)).encode(), method="POST", headers={
        "Authorization": f"Bearer {s['key']}", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with request.urlopen(req, timeout=TIMEOUT) as res:
            return json.loads(res.read() or b"{}")
    except error.HTTPError as exc:
        try:
            message = json.loads(exc.read() or b"{}").get("error", {}).get("message", "")
        except ValueError:
            message = ""
        raise BillingError(f"Stripe refused it ({exc.code}){': ' + message if message else ''}.") from None
    except (error.URLError, TimeoutError, ValueError) as exc:
        raise BillingError(f"Couldn't reach Stripe: {exc}") from None


def record(state: dict[str, Any]) -> dict[str, Any]:
    return state.setdefault("company", {}).setdefault("billing", {})


# ----- what the owner can do -----

def checkout_url(state: dict[str, Any], company_id: str, email: str, base_url: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if not configured():
        raise BillingError("Billing isn't set up on this server yet.")
    b = record(state)
    if b.get("status") in GOOD_STATUSES | WARN_STATUSES:
        raise BillingError("This company already has a subscription. Use Manage billing.")
    data: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": settings()["price"], "quantity": 1}],
        "success_url": f"{base_url}/settings?billing=done",
        "cancel_url": f"{base_url}/settings",
        "client_reference_id": company_id,
        "subscription_data": {"metadata": {"company_id": company_id}},
    }
    if b.get("customer_id"):
        data["customer"] = b["customer_id"]
    elif email:
        data["customer_email"] = email
    t = trial.status(state, now)
    if t.get("state") == "active":
        ends = datetime.fromisoformat(t["ends_by"])
        if (ends - now).total_seconds() >= MIN_TRIAL_SECONDS:
            data["subscription_data"]["trial_end"] = int(ends.timestamp())
    session = _post("/v1/checkout/sessions", data)
    if not str(session.get("url", "")).startswith("https://"):
        raise BillingError("Stripe didn't return a checkout page.")
    return session["url"]


def portal_url(state: dict[str, Any], base_url: str) -> str:
    customer = record(state).get("customer_id")
    if not customer:
        raise BillingError("There is no subscription to manage yet. Subscribe first.")
    session = _post("/v1/billing_portal/sessions", {"customer": customer, "return_url": f"{base_url}/settings"})
    if not str(session.get("url", "")).startswith("https://"):
        raise BillingError("Stripe didn't return the billing page.")
    return session["url"]


def end_trial_now(subscription_id: str) -> None:
    _post(f"/v1/subscriptions/{parse.quote(subscription_id)}", {"trial_end": "now"})


def trial_end_due(state: dict[str, Any], now: datetime | None = None) -> str | None:
    """The subscription whose Stripe trial should end now, because the first pay app was paid.
    Marks it so the request is sent once; returns the subscription id, or None."""
    b = record(state)
    if b.get("status") != "trialing" or not b.get("subscription_id") or b.get("trial_end_requested_at"):
        return None
    t = trial.status(state, now)
    if t.get("state") != "ended" or t.get("reason") != "paid":
        return None
    b["trial_end_requested_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return b["subscription_id"]


# ----- what Stripe tells us -----

def verify(payload: bytes, header: str, now: float | None = None) -> dict[str, Any]:
    """The event, if the Stripe-Signature header is right and recent; else BillingError."""
    secret = settings()["webhook_secret"]
    if not secret:
        raise BillingError("STRIPE_WEBHOOK_SECRET isn't set.")
    parts: dict[str, list[str]] = {}
    for item in (header or "").split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            parts.setdefault(k.strip(), []).append(v.strip())
    try:
        stamp = int((parts.get("t") or [""])[0])
    except ValueError:
        raise BillingError("No timestamp in the signature.") from None
    if abs((now or time.time()) - stamp) > SIGNATURE_TOLERANCE:
        raise BillingError("The signature is too old.")
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", [])):
        raise BillingError("The signature doesn't match.")
    try:
        event = json.loads(payload)
    except ValueError:
        raise BillingError("The event isn't JSON.") from None
    if not isinstance(event, dict) or not isinstance((event.get("data") or {}).get("object"), dict):
        raise BillingError("Not a Stripe event.")
    return event


def company_hint(event: dict[str, Any]) -> tuple[str, str]:
    """(company id, customer id) the event names, either may be ""."""
    obj = event["data"]["object"]
    company = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("company_id") or ""
    customer = obj.get("customer") if isinstance(obj.get("customer"), str) else ""
    return str(company), str(customer or "")


def _period_end(sub: dict[str, Any]) -> int | None:
    end = sub.get("current_period_end")
    if not end:  # newer API versions keep it on the subscription's items
        items = ((sub.get("items") or {}).get("data") or [{}])
        end = items[0].get("current_period_end") if items else None
    return int(end) if isinstance(end, (int, float)) else None


def apply(state: dict[str, Any], event: dict[str, Any]) -> str:
    """Change the company's billing record for one event. Returns an audit line, or "" when the
    event changes nothing (a replay, an older event, a type we don't use)."""
    b = record(state)
    seen = b.setdefault("events", [])
    if event.get("id") in seen:
        return ""
    seen.append(event.get("id"))
    del seen[:-EVENTS_KEPT]
    kind, obj, created = event.get("type", ""), event["data"]["object"], int(event.get("created") or 0)
    if kind == "checkout.session.completed":
        b.update(customer_id=obj.get("customer") or b.get("customer_id"), subscription_id=obj.get("subscription") or b.get("subscription_id"))
        return "Subscribed through Stripe Checkout."
    if kind.startswith("customer.subscription."):
        if created < int(b.get("status_at") or 0):
            return ""  # Stripe doesn't promise order; an older status never overwrites a newer one
        status = "canceled" if kind == "customer.subscription.deleted" else str(obj.get("status") or "")
        before = b.get("status")
        b.update(customer_id=obj.get("customer") or b.get("customer_id"), subscription_id=obj.get("id") or b.get("subscription_id"),
                 status=status, status_at=created, period_end=_period_end(obj))
        return f"Subscription {before or 'none'} → {status}." if status != before else ""
    if kind == "invoice.payment_failed":
        b["last_failed_at"] = created
        return "Stripe couldn't charge the card for the subscription."
    if kind == "invoice.paid":
        b["last_paid_at"] = created
        return "Subscription invoice paid."
    return ""


# ----- where the company stands -----

def standing(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """{"configured", "enforced", "status", "hold", "warning"}: hold is the read-only reason."""
    now = now or datetime.now(timezone.utc)
    s = settings()
    b = (state.get("company") or {}).get("billing") or {}
    status = b.get("status") or ""
    out = {"configured": configured(), "enforced": bool(configured() and s["enforce"]), "status": status,
           "period_end": b.get("period_end"), "has_customer": bool(b.get("customer_id")), "hold": "", "warning": ""}
    if not out["configured"]:
        return out
    if status in WARN_STATUSES:
        out["warning"] = "Stripe couldn't charge the card. Update it in Manage billing to keep the account open."
    if status in HOLD_STATUSES:
        out["hold"] = "the subscription is unpaid or cancelled; an owner can fix it in Settings, Billing"
    elif status not in GOOD_STATUSES | WARN_STATUSES:
        t = trial.status(state, now)
        if t.get("state") == "ended":
            ended = datetime.fromisoformat(t["ended_at"])
            if now >= ended + timedelta(days=s["grace_days"]):
                out["hold"] = "the free trial ended and there is no subscription; an owner can subscribe in Settings, Billing"
            else:
                out["warning"] = f"The free trial has ended. Subscribe by {(ended + timedelta(days=s['grace_days'])).date().isoformat()} to keep making changes."
    if not out["enforced"]:
        out["hold"] = ""
    return out
