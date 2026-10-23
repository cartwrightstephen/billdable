"""Download photos and files texted to the Duracash number (P3: "Twilio media download").

Twilio posts each picture as a MediaUrl on api.twilio.com, which redirects to Twilio's
storage. Downloads happen only when TWILIO_AUTH_TOKEN is set (so the request was signed by
Twilio and nobody else can make the server fetch an address of their choosing), only from
https://api.twilio.com, following redirects only to Twilio's storage hosts. The account
login (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN) is sent only when api.twilio.com asks for
it, never to the storage host a redirect points at.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlparse

TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
         "image/heic": ".heic", "application/pdf": ".pdf"}
STORAGE_HOSTS = (".twilio.com", ".twiliocdn.com", ".amazonaws.com")
MAX_FILES = 10


def allowed_start(url: str) -> bool:
    u = urlparse(url)
    return u.scheme == "https" and u.hostname == "api.twilio.com"


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urlparse(newurl)
        if u.scheme != "https" or not (u.hostname or "").endswith(STORAGE_HOSTS):
            raise urllib.error.HTTPError(newurl, code, "redirect to an unexpected host", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, limit: int) -> bytes:
    """GET one media URL; raises on anything unexpected. Returns at most `limit` bytes or raises."""
    password = urllib.request.HTTPPasswordMgrWithPriorAuth()
    sid, token = os.environ.get("TWILIO_ACCOUNT_SID", ""), os.environ.get("TWILIO_AUTH_TOKEN", "")
    if sid and token:
        password.add_password(None, "https://api.twilio.com/", sid, token, is_authenticated=False)
    opener = urllib.request.build_opener(_Redirects(), urllib.request.HTTPBasicAuthHandler(password))
    with opener.open(url, timeout=15) as res:
        data = res.read(limit + 1)
    if len(data) > limit:
        raise ValueError("larger than the upload limit")
    return data


def download(media: list[dict[str, str]], limit: int, get: Callable[[str, int], bytes] | None = None) -> tuple[list[dict], list[str]]:
    """([{"filename", "data"}], [reasons for anything not downloaded]). Nothing at all without TWILIO_AUTH_TOKEN."""
    if not os.environ.get("TWILIO_AUTH_TOKEN"):
        return [], []
    get = get or fetch
    files, problems = [], []
    for n, m in enumerate(media[:MAX_FILES], start=1):
        ext = TYPES.get((m.get("type") or "").split(";")[0].strip().lower())
        if not ext:
            problems.append(f"Attachment {n} ({m.get('type') or 'unknown type'}) is not a photo or PDF; kept as a link.")
            continue
        if not allowed_start(m.get("url", "")):
            problems.append(f"Attachment {n} is not on api.twilio.com; kept as a link.")
            continue
        try:
            files.append({"filename": f"Text photo {n}{ext}", "data": get(m["url"], limit), "url": m["url"]})
        except Exception as exc:  # one bad download keeps the others and the text
            problems.append(f"Attachment {n} couldn't be downloaded ({exc.__class__.__name__}); kept as a link.")
    return files, problems
