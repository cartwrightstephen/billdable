"""Tell a founder when something breaks (P0-5).

ALERT_WEBHOOK_URL is an incoming-webhook address: a Slack or Discord channel
webhook, or any service that accepts a JSON POST with a "text" field (Better
Stack, Zapier, ...). Each server crash and each failed backup posts one short
line: what failed, where, and the request id to look up in the logs. The
message never carries the error text or request data, which can hold customer
details; the full traceback stays in the server log.

Alerts are sent from a background thread with a short timeout, so a slow
webhook never slows a request, and are rate limited so a crash loop sends a
handful, not thousands. Point an uptime monitor at /healthz as well: it
catches the server being down, which no alert from inside the server can.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from typing import Callable

SAME_EVENT_SECONDS = 5 * 60  # one alert per kind of event per 5 minutes
HOURLY_LIMIT = 20

_LOCK = threading.Lock()
_sent: list[float] = []
_last: dict[str, float] = {}


def url() -> str:
    value = (os.environ.get("ALERT_WEBHOOK_URL") or "").strip()
    return value if value.startswith("https://") else ""


def _allowed(key: str, now: float) -> bool:
    with _LOCK:
        _sent[:] = [t for t in _sent if now - t < 3600]
        if len(_sent) >= HOURLY_LIMIT or now - _last.get(key, -1e9) < SAME_EVENT_SECONDS:
            return False
        _sent.append(now)
        _last[key] = now
        return True


def _post(target: str, text: str) -> None:
    try:
        req = urllib.request.Request(target, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as exc:  # an alert that can't be sent must never cause another error
        sys.stderr.write(json.dumps({"level": "warn", "msg": f"alert not sent: {exc.__class__.__name__}"}) + "\n")


def notify(key: str, text: str, send: Callable[[str, str], None] | None = None) -> bool:
    """Queue one alert. `key` groups repeats (e.g. "crash:/api/jobs"). Returns whether it was sent."""
    target = url()
    if not target or not _allowed(key, time.monotonic()):
        return False
    env = (os.environ.get("DURACASH_ENV") or "development").strip()
    message = f"Duracash ({env}): {text}"
    if send:
        send(target, message)
    else:
        threading.Thread(target=_post, args=(target, message), name="alert", daemon=True).start()
    return True
