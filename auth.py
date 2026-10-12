"""File-backed auth for Billdable.

One user belongs to one company. Sessions are opaque tokens stored in a cookie.
Passwords are hashed with PBKDF2. No external services.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
AUTH_PATH = ROOT / "data" / "auth.json"
LOCK = threading.Lock()

DEMO_EMAIL = "owner@hartline.example"
DEMO_PASSWORD = "demo"
DEMO_COMPANY_ID = "co-hartline"
SESSION_DAYS = 14


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _hash(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return salt, digest.hex()


def _load() -> dict[str, Any]:
    if AUTH_PATH.exists():
        return json.loads(AUTH_PATH.read_text())
    return {"users": [], "sessions": {}}


def _save(data: dict[str, Any]) -> None:
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_PATH.write_text(json.dumps(data, indent=2))


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
    }


def create_user(email: str, password: str, name: str, company_id: str, role: str = "owner") -> dict[str, Any]:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("A real email is required.")
    if len(password) < 4:
        raise ValueError("Password must be at least 4 characters.")
    if not name.strip():
        raise ValueError("Name is required.")
    salt, digest = _hash(password)
    user = {
        "id": "user-" + secrets.token_hex(4),
        "email": email,
        "name": name.strip(),
        "company_id": company_id,
        "role": role,
        "salt": salt,
        "password_hash": digest,
        "created_at": _iso(_utcnow()),
    }
    with LOCK:
        data = _load()
        if any(u["email"] == email for u in data["users"]):
            raise ValueError("An account with that email already exists.")
        data["users"].append(user)
        _save(data)
    return _public(user)


def verify_login(email: str, password: str) -> dict[str, Any] | None:
    email = email.strip().lower()
    with LOCK:
        data = _load()
        user = next((u for u in data["users"] if u["email"] == email), None)
        if not user:
            return None
        _, digest = _hash(password, user["salt"])
        if not secrets.compare_digest(digest, user["password_hash"]):
            return None
        return _public(user)


def create_session(user: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(32)
    expires = _utcnow() + timedelta(days=SESSION_DAYS)
    with LOCK:
        data = _load()
        data["sessions"][token] = {
            "user_id": user["id"],
            "company_id": user["company_id"],
            "expires_at": _iso(expires),
        }
        # Drop expired sessions opportunistically.
        now = _iso(_utcnow())
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v["expires_at"] > now}
        data["sessions"][token] = {
            "user_id": user["id"],
            "company_id": user["company_id"],
            "expires_at": _iso(expires),
        }
        _save(data)
    return token


def session_user(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with LOCK:
        data = _load()
        entry = data["sessions"].get(token)
        if not entry:
            return None
        if entry["expires_at"] <= _iso(_utcnow()):
            data["sessions"].pop(token, None)
            _save(data)
            return None
        user = next((u for u in data["users"] if u["id"] == entry["user_id"]), None)
        if not user:
            return None
        return _public(user)


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with LOCK:
        data = _load()
        data["sessions"].pop(token, None)
        _save(data)


def demo_credentials() -> dict[str, str]:
    return {"email": DEMO_EMAIL, "password": DEMO_PASSWORD, "company_id": DEMO_COMPANY_ID}
