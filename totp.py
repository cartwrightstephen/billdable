"""Time-based one-time codes (RFC 6238) for two-step sign-in (P2-9), standard library only.

Any authenticator app (Google Authenticator, 1Password, Authy, Microsoft Authenticator)
accepts the key: 6 digits, 30-second steps, HMAC-SHA1, which is what those apps assume
when a key is typed in by hand.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret() -> str:
    """A 160-bit key in base32, the form authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    clean = secret.replace(" ", "").upper()
    return base64.b32decode(clean + "=" * (-len(clean) % 8))


def code_at(secret: str, counter: int, digits: int = DIGITS) -> str:
    mac = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def verify(secret: str, code: str, last_counter: int = -1, now: float | None = None, window: int = 1) -> int | None:
    """The time step the code belongs to (within ±window steps), or None. A step at or before
    last_counter is refused, so a code can't be used twice."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    current = int((time.time() if now is None else now) // STEP)
    for counter in range(current - window, current + window + 1):
        if counter > last_counter and hmac.compare_digest(code_at(secret, counter), code):
            return counter
    return None


def uri(secret: str, account: str, issuer: str = "Duracash") -> str:
    """otpauth:// link; phones open it straight in an authenticator app."""
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"
