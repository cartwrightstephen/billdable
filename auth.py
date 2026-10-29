"""File-backed auth for Duracash.

A company has one or more users; each user belongs to one company and is an
owner or a member. Sessions are opaque tokens stored in a cookie; only their
SHA-256 is stored, so a copy of auth.json signs nobody in. Passwords are hashed
with PBKDF2. Password-reset, email-verification and invite links
carry single-use tokens; only their SHA-256 is stored. No external services.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import storage
import totp

AUTH_PATH = storage.DATA_DIR / "auth.json"
LOCK = threading.Lock()

MIN_PASSWORD = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Login throttling. Per email: LOGIN_FREE_FAILURES wrong passwords inside LOGIN_WINDOW_SECONDS
# cost nothing; each one after that doubles a wait (2, 4, 8 ... up to LOGIN_MAX_DELAY seconds)
# before the next try. A short, growing wait rather than a lockout, so nobody can lock
# someone else out of their account by typing wrong passwords. Per network address:
# IP_MAX_FAILURES wrong passwords inside the window and that address is refused until it passes.
LOGIN_FREE_FAILURES = 3
LOGIN_MAX_DELAY = 60
LOGIN_WINDOW_SECONDS = 15 * 60
IP_MAX_FAILURES = 20
_FAILURES: dict[str, list[float]] = {}
_IP_FAILURES: dict[str, list[float]] = {}
# PBKDF2-HMAC-SHA256 rounds: OWASP's 2023 figure (from training, not re-checked). Each user
# record keeps the count it was hashed with; older ones (120,000) are rehashed at sign-in.
KDF_ITERATIONS = 600_000
LEGACY_ITERATIONS = 120_000


class RateLimited(Exception):
    pass

ROLES = ("owner", "member")
TOKEN_HOURS = {"reset": 1, "verify": 72, "invite": 7 * 24}
RESET_MAX_PER_HOUR = 3
_RESETS: dict[str, list[float]] = {}


class AuthError(ValueError):
    pass

DEMO_EMAIL = "owner@hartline.example"
DEMO_PASSWORD = "demo"
DEMO_COMPANY_ID = "co-hartline"
SESSION_DAYS = 14


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _hash(password: str, salt: str | None = None, iterations: int = KDF_ITERATIONS) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return salt, digest.hex()


def _matches(user: dict[str, Any], password: str) -> bool:
    _, digest = _hash(password, user["salt"], int(user.get("kdf_iterations") or LEGACY_ITERATIONS))
    return secrets.compare_digest(digest, user["password_hash"])


def _set_password(user: dict[str, Any], salt: str, digest: str) -> None:
    user["salt"], user["password_hash"], user["kdf_iterations"] = salt, digest, KDF_ITERATIONS


def _prune(table: dict[str, list[float]], now: float) -> None:
    """Bound memory: forget keys with no failure inside the window."""
    if len(table) > 10_000:
        for key in [k for k, v in table.items() if not v or now - v[-1] >= LOGIN_WINDOW_SECONDS]:
            table.pop(key, None)


def _throttle(email: str, ip: str | None, now: float) -> None:
    """Raise RateLimited when this email must wait, or this address has failed too often. Call under LOCK."""
    _prune(_FAILURES, now)
    _prune(_IP_FAILURES, now)
    if ip:
        recent_ip = [t for t in _IP_FAILURES.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        _IP_FAILURES[ip] = recent_ip
        if len(recent_ip) >= IP_MAX_FAILURES:
            raise RateLimited("Too many wrong passwords from this network. Try again in 15 minutes.")
    recent = [t for t in _FAILURES.get(email, []) if now - t < LOGIN_WINDOW_SECONDS]
    _FAILURES[email] = recent
    if len(recent) >= LOGIN_FREE_FAILURES:
        wait = min(LOGIN_MAX_DELAY, 2 ** (len(recent) - LOGIN_FREE_FAILURES + 1))
        left = wait - (now - recent[-1])
        if left > 0:
            raise RateLimited(f"Too many wrong passwords. Try again in {math.ceil(left)} second{'' if math.ceil(left) == 1 else 's'}.")


def _failed(email: str, ip: str | None, now: float) -> None:
    _FAILURES.setdefault(email, []).append(now)
    if ip:
        _IP_FAILURES.setdefault(ip, []).append(now)


def _load() -> dict[str, Any]:
    if AUTH_PATH.exists():
        data = json.loads(AUTH_PATH.read_text())
    else:
        data = {"users": [], "sessions": {}, "sessions_hashed": True}
    data.setdefault("tokens", {})
    if not data.get("sessions_hashed"):
        # Files written before session hashing hold raw tokens as keys; hash them in place.
        data["sessions"] = {_digest(k): v for k, v in data.get("sessions", {}).items()}
        data["sessions_hashed"] = True
    return data


def _save(data: dict[str, Any]) -> None:
    storage.write_json(AUTH_PATH, data)


_READ_CACHE: dict[str, Any] = {}


def _read() -> dict[str, Any]:
    """The file as last parsed, for lookups that change nothing: every request checks its
    session here, so the file is parsed again only when it changes (a save renames a new file
    into place, which changes the inode). Callers must not modify what this returns."""
    try:
        st = AUTH_PATH.stat()
    except FileNotFoundError:
        return _load()
    key = (str(AUTH_PATH), st.st_ino, st.st_mtime_ns, st.st_size)
    if _READ_CACHE.get("key") != key:
        _READ_CACHE.update(key=key, data=_load())
    return _READ_CACHE["data"]


def upgrade_file() -> None:
    """Called at start-up: write back a file from before session hashing once, so _load()
    stops re-hashing its session keys on every request."""
    with LOCK:
        if AUTH_PATH.exists() and not json.loads(AUTH_PATH.read_text()).get("sessions_hashed"):
            _save(_load())


def has_users() -> bool:
    with LOCK:
        return bool(_load()["users"])


def user_by_email(email: str) -> dict[str, Any] | None:
    email = email.strip().lower()
    with LOCK:
        for user in _load()["users"]:
            if user["email"] == email:
                return _public(user)
    return None


def _public(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "company_id": user["company_id"],
        "role": user.get("role", "owner"),
        "email_verified": bool(user.get("email_verified")),
        "two_step": bool(user.get("totp_enabled")),
        "onboarding_dismissed": bool(user.get("onboarding_dismissed")),
    }


def create_user(
    email: str,
    password: str,
    name: str,
    company_id: str,
    role: str = "owner",
    enforce_policy: bool = True,
    terms_version: str | None = None,
) -> dict[str, Any]:
    """Create a user. enforce_policy=False is only for the built-in demo account."""
    if not isinstance(email, str) or not isinstance(password, str) or not isinstance(name, str):
        raise ValueError("Email, password and name must be text.")
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("A real email is required.")
    if enforce_policy and len(password) < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters.")
    if not password:
        raise ValueError("Password is required.")
    if not name.strip():
        raise ValueError("Name is required.")
    if role not in ROLES:
        raise ValueError("Role must be owner or member.")
    salt, digest = _hash(password)
    user = {
        "id": "user-" + secrets.token_hex(4),
        "email": email,
        "name": name.strip()[:120],
        "company_id": company_id,
        "role": role,
        "salt": salt,
        "password_hash": digest,
        "kdf_iterations": KDF_ITERATIONS,
        "created_at": _iso(_utcnow()),
    }
    if terms_version:
        user["terms_version"] = terms_version
        user["terms_accepted_at"] = _iso(_utcnow())
    with LOCK:
        data = _load()
        if any(u["email"] == email for u in data["users"]):
            raise ValueError("An account with that email already exists.")
        data["users"].append(user)
        _save(data)
    return _public(user)


def verify_login(email: str, password: str, ip: str | None = None) -> dict[str, Any] | None:
    """Return the user, None on a wrong email or password, or raise RateLimited."""
    if not isinstance(email, str) or not isinstance(password, str):
        return None
    email = email.strip().lower()
    with LOCK:
        now = time.monotonic()
        _throttle(email, ip, now)
        user = next((u for u in _load()["users"] if u["email"] == email), None)
    # Hash outside the lock so one slow login does not hold up every session check.
    if user:
        ok = _matches(user, password)
    else:
        ok = False
        _hash(password, "0" * 32)  # same work for an unknown email, so timing doesn't reveal which emails exist
    if not ok:
        with LOCK:
            _failed(email, ip, now)
        return None
    if int(user.get("kdf_iterations") or LEGACY_ITERATIONS) < KDF_ITERATIONS:
        salt, digest = _hash(password)
        with LOCK:
            data = _load()
            stored = next((u for u in data["users"] if u["id"] == user["id"]), None)
            # Only if the password didn't change while we were hashing.
            if stored and stored["password_hash"] == user["password_hash"]:
                _set_password(stored, salt, digest)
                _save(data)
    if not user.get("totp_enabled"):
        # With two-step sign-in, only a right code clears the count (totp_check), so a known
        # password can't be used to reset the throttle on guessing codes.
        with LOCK:
            _FAILURES.pop(email, None)
    return _public(user)


def create_session(user: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(32)
    expires = _utcnow() + timedelta(days=SESSION_DAYS)
    with LOCK:
        data = _load()
        # Drop expired sessions opportunistically.
        now = _iso(_utcnow())
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v["expires_at"] > now}
        data["sessions"][_digest(token)] = {
            "user_id": user["id"],
            "company_id": user["company_id"],
            "expires_at": _iso(expires),
        }
        _save(data)
    return token


def session_user(token: str | None) -> dict[str, Any] | None:
    if not token or not isinstance(token, str) or len(token) > 200:
        return None
    key = _digest(token)
    with LOCK:
        entry = _read()["sessions"].get(key)
        if not entry:
            return None
        if entry["expires_at"] <= _iso(_utcnow()):
            data = _load()
            data["sessions"].pop(key, None)
            _save(data)
            return None
        user = next((u for u in _read()["users"] if u["id"] == entry["user_id"]), None)
        if not user:
            return None
        return _public(user)


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with LOCK:
        data = _load()
        data["sessions"].pop(_digest(token), None)
        _save(data)


def demo_credentials() -> dict[str, str]:
    return {"email": DEMO_EMAIL, "password": DEMO_PASSWORD, "company_id": DEMO_COMPANY_ID}


# ---------- single-use tokens (reset, verify, invite) ----------

def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _prune_tokens(data: dict[str, Any]) -> None:
    now = _iso(_utcnow())
    data["tokens"] = {k: v for k, v in data["tokens"].items() if v.get("expires_at", "") > now}


def _new_token(data: dict[str, Any], kind: str, **fields: Any) -> str:
    raw = secrets.token_urlsafe(32)
    _prune_tokens(data)
    data["tokens"][_digest(raw)] = {
        "kind": kind,
        "id": f"{kind}-" + secrets.token_hex(4),
        "created_at": _iso(_utcnow()),
        "expires_at": _iso(_utcnow() + timedelta(hours=TOKEN_HOURS[kind])),
        **fields,
    }
    return raw


def _find_token(data: dict[str, Any], kind: str, raw: Any) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    if not isinstance(raw, str) or not raw or len(raw) > 200:
        return None, None
    key = _digest(raw)
    entry = data["tokens"].get(key)
    if not entry or entry.get("kind") != kind or entry.get("expires_at", "") <= _iso(_utcnow()):
        return None, None
    return key, entry


def _check_password(password: Any) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AuthError(f"Password must be at least {MIN_PASSWORD} characters.")
    if len(password) > 1000:
        raise AuthError("Password is too long.")
    return password


def _end_sessions(data: dict[str, Any], user_id: str) -> None:
    data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("user_id") != user_id}


def password_reset_token(email: Any) -> tuple[str, dict[str, Any]] | None:
    """A reset token for this email, or None when there is no such account (or too many requests).
    The caller answers the same way either way, so nobody learns which emails have accounts."""
    if not isinstance(email, str):
        return None
    email = email.strip().lower()
    now = time.monotonic()
    with LOCK:
        recent = [t for t in _RESETS.get(email, []) if now - t < 3600]
        if len(recent) >= RESET_MAX_PER_HOUR:
            _RESETS[email] = recent
            return None
        _RESETS[email] = recent + [now]
        if len(_RESETS) > 10_000:
            for key in [k for k, v in _RESETS.items() if not v or now - v[-1] >= 3600]:
                _RESETS.pop(key, None)
            if len(_RESETS) > 50_000:  # still too many fresh entries: drop the lot rather than grow
                _RESETS.clear()
        data = _load()
        user = next((u for u in data["users"] if u["email"] == email), None)
        if not user or user["company_id"] == DEMO_COMPANY_ID:
            return None
        # Older unexpired links stay valid (at most 3 exist, by the rate limit), so someone
        # requesting resets for another person's email can't cancel the link they're using.
        raw = _new_token(data, "reset", user_id=user["id"])
        _save(data)
        return raw, _public(user)


def reset_password(raw: Any, password: Any) -> dict[str, Any]:
    _check_password(password)
    with LOCK:
        data = _load()
        key, entry = _find_token(data, "reset", raw)
        if not key:
            raise AuthError("This reset link is invalid or has expired. Ask for a new one.")
        user = next((u for u in data["users"] if u["id"] == entry["user_id"]), None)
        if not user:
            raise AuthError("This account no longer exists.")
    salt, digest = _hash(password)  # outside the lock: hashing is slow on purpose
    with LOCK:
        data = _load()
        if _digest(raw) not in data["tokens"]:
            raise AuthError("This reset link has already been used.")
        user = next(u for u in data["users"] if u["id"] == entry["user_id"])
        # Every reset link for this account is used up once one of them works.
        data["tokens"] = {k: v for k, v in data["tokens"].items() if not (v["kind"] == "reset" and v.get("user_id") == user["id"])}
        _set_password(user, salt, digest)
        user["email_verified"] = True  # they received the link at this address
        _end_sessions(data, user["id"])  # everywhere else is signed out
        _save(data)
        _FAILURES.pop(user["email"], None)
        return _public(user)


def update_name(user_id: str, name: Any) -> dict[str, Any]:
    """A user changes the name the audit trail and the team list show."""
    if not isinstance(name, str) or not " ".join(name.split()):
        raise AuthError("Your name can't be empty.")
    name = " ".join(name.split())
    if len(name) > 80:
        raise AuthError("Your name is too long (limit 80 characters).")
    with LOCK:
        data = _load()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if not user:
            raise AuthError("Account not found.")
        user["name"] = name
        _save(data)
        return _public(user)


def set_onboarding_dismissed(user_id: str, dismissed: bool) -> dict[str, Any]:
    """Hide or show the setup checklist for this person only (R2-03): a teammate who joins
    later still sees it."""
    with LOCK:
        data = _load()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if not user:
            raise AuthError("Account not found.")
        if dismissed:
            user["onboarding_dismissed"] = True
        else:
            user.pop("onboarding_dismissed", None)
        _save(data)
        return _public(user)


def change_password(user_id: str, current: Any, new: Any, keep_session: str | None = None) -> None:
    """Change a password. Other sessions end; the one making the change can stay."""
    _check_password(new)
    with LOCK:
        user = next((u for u in _load()["users"] if u["id"] == user_id), None)
        if not user:
            raise AuthError("Current password is wrong.")
        if user["company_id"] == DEMO_COMPANY_ID:
            raise AuthError("The demo account's password can't be changed.")
        now = time.monotonic()
        _throttle(user["email"], None, now)
    ok = isinstance(current, str) and _matches(user, current)
    if not ok:
        with LOCK:
            _failed(user["email"], None, now)
        raise AuthError("Current password is wrong.")
    salt, new_digest = _hash(new)
    keep = _digest(keep_session) if keep_session else None
    with LOCK:
        data = _load()
        user = next(u for u in data["users"] if u["id"] == user_id)
        _set_password(user, salt, new_digest)
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("user_id") != user_id or k == keep}
        _save(data)


def verify_email_token(user_id: str) -> str:
    with LOCK:
        data = _load()
        data["tokens"] = {k: v for k, v in data["tokens"].items() if not (v["kind"] == "verify" and v.get("user_id") == user_id)}
        raw = _new_token(data, "verify", user_id=user_id)
        _save(data)
        return raw


def verify_email(raw: Any) -> dict[str, Any]:
    with LOCK:
        data = _load()
        key, entry = _find_token(data, "verify", raw)
        if not key:
            raise AuthError("This verification link is invalid or has expired. Send a new one from Settings, My account.")
        data["tokens"].pop(key, None)
        user = next((u for u in data["users"] if u["id"] == entry["user_id"]), None)
        if not user:
            raise AuthError("This account no longer exists.")
        user["email_verified"] = True
        user["verified_at"] = _iso(_utcnow())
        _save(data)
        return _public(user)


# ---------- team ----------

def _require_owner(data: dict[str, Any], company_id: str, actor_id: str) -> dict[str, Any]:
    """Checked under LOCK at the moment of the change, so a person removed or demoted a moment
    ago can't finish a request they started while they were still an owner."""
    actor = next((u for u in data["users"] if u["id"] == actor_id and u["company_id"] == company_id), None)
    if not actor or actor.get("role", "owner") != "owner":
        raise AuthError("Only an owner of this company can do that.")
    return actor


def require_owner(company_id: str, actor_id: str) -> None:
    with LOCK:
        _require_owner(_load(), company_id, actor_id)


def team(company_id: str) -> dict[str, Any]:
    with LOCK:
        data = _load()
        _prune_tokens(data)
        users = [
            {**_public(u), "created_at": u.get("created_at", "")}
            for u in data["users"] if u["company_id"] == company_id
        ]
        invites = [
            {"id": v["id"], "email": v["email"], "role": v["role"], "invited_by": v.get("invited_by", ""),
             "created_at": v["created_at"], "expires_at": v["expires_at"]}
            for v in data["tokens"].values() if v["kind"] == "invite" and v.get("company_id") == company_id
        ]
    return {"users": users, "invites": invites}


def create_invite(company_id: str, email: Any, role: Any, invited_by: str, actor_id: str = "") -> tuple[str, dict[str, Any]]:
    if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
        raise AuthError("Enter the person's email address.")
    if role not in ROLES:
        raise AuthError("Role must be owner or member.")
    email = email.strip().lower()
    with LOCK:
        data = _load()
        _require_owner(data, company_id, actor_id)
        if any(u["email"] == email for u in data["users"]):
            raise AuthError("That email already has a Duracash account. Each person belongs to one company.")
        pending = [v for v in data["tokens"].values() if v["kind"] == "invite" and v.get("company_id") == company_id]
        if len(pending) >= 20:
            raise AuthError("Too many open invites. Revoke some first.")
        # Inviting the same address again replaces the older invite.
        data["tokens"] = {k: v for k, v in data["tokens"].items()
                          if not (v["kind"] == "invite" and v.get("company_id") == company_id and v.get("email") == email)}
        raw = _new_token(data, "invite", company_id=company_id, email=email, role=role, invited_by=invited_by, invited_by_id=actor_id)
        entry = data["tokens"][_digest(raw)]
        _save(data)
        return raw, {"id": entry["id"], "email": email, "role": role}


def invite_info(raw: Any) -> dict[str, Any] | None:
    with LOCK:
        _, entry = _find_token(_load(), "invite", raw)
    return {"email": entry["email"], "company_id": entry["company_id"], "role": entry["role"],
            "invited_by": entry.get("invited_by", "")} if entry else None


def accept_invite(raw: Any, name: Any, password: Any, terms_version: str | None = None) -> dict[str, Any]:
    _check_password(password)
    if not isinstance(name, str) or not name.strip():
        raise AuthError("Your name is required.")
    with LOCK:
        key, entry = _find_token(_load(), "invite", raw)
    if not key:
        raise AuthError("This invite is invalid or has expired. Ask for a new one.")
    salt, digest = _hash(password)
    with LOCK:
        data = _load()
        if key not in data["tokens"]:
            raise AuthError("This invite has already been used.")
        if any(u["email"] == entry["email"] for u in data["users"]):
            raise AuthError("That email already has a Duracash account.")
        data["tokens"].pop(key, None)
        user = {
            "id": "user-" + secrets.token_hex(4),
            "email": entry["email"],
            "name": name.strip()[:120],
            "company_id": entry["company_id"],
            "role": entry["role"],
            "salt": salt,
            "password_hash": digest,
            "kdf_iterations": KDF_ITERATIONS,
            "created_at": _iso(_utcnow()),
            "invited_by": entry.get("invited_by", ""),
            # They received the invite link at this address, as with a reset link.
            "email_verified": True,
            "verified_at": _iso(_utcnow()),
        }
        if terms_version:
            user["terms_version"] = terms_version
            user["terms_accepted_at"] = _iso(_utcnow())
        data["users"].append(user)
        _save(data)
        return _public(user)


def revoke_invite(company_id: str, invite_id: str, actor_id: str = "") -> None:
    with LOCK:
        data = _load()
        _require_owner(data, company_id, actor_id)
        before = len(data["tokens"])
        data["tokens"] = {k: v for k, v in data["tokens"].items()
                          if not (v["kind"] == "invite" and v.get("company_id") == company_id and v["id"] == invite_id)}
        if len(data["tokens"]) == before:
            raise AuthError("Invite not found.")
        _save(data)


def _owners(data: dict[str, Any], company_id: str) -> list[dict[str, Any]]:
    return [u for u in data["users"] if u["company_id"] == company_id and u.get("role", "owner") == "owner"]


def _drop_invites_by(data: dict[str, Any], user_id: str) -> None:
    """Invites someone sent stop working when they lose owner rights."""
    data["tokens"] = {k: v for k, v in data["tokens"].items() if not (v["kind"] == "invite" and v.get("invited_by_id") == user_id)}


def set_role(company_id: str, user_id: str, role: Any, actor_id: str = "") -> dict[str, Any]:
    if role not in ROLES:
        raise AuthError("Role must be owner or member.")
    with LOCK:
        data = _load()
        _require_owner(data, company_id, actor_id)
        user = next((u for u in data["users"] if u["id"] == user_id and u["company_id"] == company_id), None)
        if not user:
            raise AuthError("That person is not on this team.")
        if role == "member" and user.get("role", "owner") == "owner" and len(_owners(data, company_id)) == 1:
            raise AuthError("A company needs at least one owner. Make someone else an owner first.")
        user["role"] = role
        if role == "member":
            _drop_invites_by(data, user_id)
        _save(data)
        return _public(user)


def remove_user(company_id: str, user_id: str, actor_id: str = "") -> dict[str, Any]:
    with LOCK:
        data = _load()
        _require_owner(data, company_id, actor_id)
        user = next((u for u in data["users"] if u["id"] == user_id and u["company_id"] == company_id), None)
        if not user:
            raise AuthError("That person is not on this team.")
        if user.get("role", "owner") == "owner" and len(_owners(data, company_id)) == 1:
            raise AuthError("A company needs at least one owner. To close the company, delete the account instead.")
        data["users"] = [u for u in data["users"] if u["id"] != user_id]
        data["tokens"] = {k: v for k, v in data["tokens"].items() if v.get("user_id") != user_id}
        _drop_invites_by(data, user_id)
        _end_sessions(data, user_id)
        _save(data)
        return _public(user)


def check_password(user_id: str, password: Any) -> bool:
    with LOCK:
        user = next((u for u in _load()["users"] if u["id"] == user_id), None)
    if not user or not isinstance(password, str):
        return False
    return _matches(user, password)


def delete_company_users(company_id: str) -> int:
    """Remove every user, session and token of a company. Returns how many users were removed."""
    with LOCK:
        data = _load()
        ids = {u["id"] for u in data["users"] if u["company_id"] == company_id}
        data["users"] = [u for u in data["users"] if u["company_id"] != company_id]
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("company_id") != company_id and v.get("user_id") not in ids}
        data["tokens"] = {k: v for k, v in data["tokens"].items() if v.get("company_id") != company_id and v.get("user_id") not in ids}
        _save(data)
        return len(ids)


def all_users() -> list[dict[str, Any]]:
    with LOCK:
        return [{**_public(u), "created_at": u.get("created_at", "")} for u in _load()["users"]]


def owner_emails(company_id: str) -> list[str]:
    """The confirmed email addresses of a company's owners."""
    return [u["email"] for u in all_users()
            if u["company_id"] == company_id and u.get("role") == "owner" and u.get("email_verified")]


# ---------- two-step sign-in (P2-9) ----------

RECOVERY_CODES = 8


def _user(data: dict[str, Any], user_id: str) -> dict[str, Any]:
    user = next((u for u in data["users"] if u["id"] == user_id), None)
    if not user:
        raise AuthError("Account not found.")
    return user


def totp_begin(user_id: str) -> dict[str, str]:
    """A new key, not yet in force: it starts working once a code from it is confirmed."""
    with LOCK:
        data = _load()
        user = _user(data, user_id)
        if user["company_id"] == DEMO_COMPANY_ID:
            raise AuthError("The demo account can't use two-step sign-in.")
        if user.get("totp_enabled"):
            raise AuthError("Two-step sign-in is already on.")
        user["totp_pending"] = totp.new_secret()
        _save(data)
        return {"secret": user["totp_pending"], "uri": totp.uri(user["totp_pending"], user["email"])}


def totp_enable(user_id: str, code: Any) -> list[str]:
    """Confirm the new key with a code from the app. Returns recovery codes, shown once."""
    with LOCK:
        data = _load()
        user = _user(data, user_id)
        pending = user.get("totp_pending")
        if not pending:
            raise AuthError("Start setup first.")
        counter = totp.verify(pending, code)
        if counter is None:
            raise AuthError("That code didn't match. Check the phone's clock and type the code showing now.")
        codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(RECOVERY_CODES)]
        user.update(totp_secret=pending, totp_enabled=True, totp_last=counter, totp_recovery=[_digest(c) for c in codes])
        user.pop("totp_pending", None)
        _save(data)
        return codes


def totp_check(user_id: str, code: Any, ip: str | None = None) -> bool:
    """At sign-in: a current code (each one once) or an unused recovery code. Wrong codes count
    toward the same throttle as wrong passwords."""
    now = time.monotonic()
    with LOCK:
        data = _load()
        user = _user(data, user_id)
        _throttle(user["email"], ip, now)
        if not user.get("totp_enabled"):
            return True
        raw = str(code or "").strip().lower()
        counter = totp.verify(user["totp_secret"], raw, int(user.get("totp_last", -1)))
        if counter is not None:
            user["totp_last"] = counter
            _save(data)
            _FAILURES.pop(user["email"], None)
            return True
        digest = _digest(raw)
        if digest in user.get("totp_recovery", []):
            user["totp_recovery"] = [d for d in user["totp_recovery"] if d != digest]
            _save(data)
            _FAILURES.pop(user["email"], None)
            return True
        _failed(user["email"], ip, now)
        return False


def totp_disable(user_id: str, password: Any, code: Any) -> None:
    """Turn two-step sign-in off: needs the password and a current code (or a recovery code)."""
    if not check_password(user_id, password):
        raise AuthError("Password is wrong.")
    if not totp_check(user_id, code):
        raise AuthError("That code didn't work.")
    with LOCK:
        data = _load()
        user = _user(data, user_id)
        for key in ("totp_secret", "totp_enabled", "totp_last", "totp_recovery", "totp_pending"):
            user.pop(key, None)
        _save(data)
