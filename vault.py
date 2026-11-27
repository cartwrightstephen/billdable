"""Encryption at rest with the server key (H3), standard library only.

What it protects: two-step sign-in keys and QuickBooks tokens in the data files, the secret
part of each inbound address, the local backups, and the audit trail's chain key (M6). Someone
who copies the disk, a backup or a data file without the key reads none of the secrets and
can't rebuild the audit trail.

The key. DURACASH_SECRET_KEY, at least 32 characters; make one with
    python3 -c "import secrets; print(secrets.token_urlsafe(32))"
and keep a copy in the password manager beside the age private key: without it the local
backups can't be read, two-step sign-in has to be set up again and QuickBooks connected again.
In production and on staging the server refuses to start without it (server.config_problems).
Elsewhere (a laptop) a key is made once in <data dir>/secret.key, readable by its owner only;
no backup holds that file.

Why this construction. The standard library has no AES, and calling the age program for
every field (offsite.py uses it for whole files) would mean a process per sign-in and an age
identity file on the disk anyway. So: encrypt-then-MAC from hmac/hashlib, which are
well-studied primitives used in a standard way.
- Keys: one per purpose, HMAC-SHA256(server key, "duracash-key:" + purpose); the encryption
  and MAC keys are never the same.
- Encryption: a keystream of HMAC-SHA512(enc key, nonce || 64-bit block counter) blocks, XORed
  with the text (HMAC as a PRF in counter mode). A fresh random 16-byte nonce each time.
- Authentication: HMAC-SHA256(mac key, version || context || nonce || ciphertext), checked in
  constant time before anything is decrypted; the context (say "totp") stops a value being
  moved to another field.
- Field format: "enc1:" + base64url(nonce || ciphertext || tag). A value without the prefix is
  a plaintext one from before this change; it is read as it is and encrypted on the next write.
- Files (backups): MAGIC || nonce || ciphertext || tag, the same scheme streamed in 1 MiB
  chunks; decrypt writes a temporary file and keeps it only if the tag matches.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import threading
from pathlib import Path

import storage

KEY_ENV = "DURACASH_SECRET_KEY"
KEY_FILE = "secret.key"
MIN_KEY_CHARS = 32
PREFIX = "enc1:"
LOOKUP_PREFIX = "h1:"
NONCE = 16
TAG = 32
BLOCK = 64                      # bytes of keystream per HMAC-SHA512 call
CHUNK = 1024 * 1024             # a multiple of BLOCK
MAGIC = b"DURACASH-ENC1\n"
GENERATE = 'python3 -c "import secrets; print(secrets.token_urlsafe(32))"'

_LOCK = threading.Lock()
_DEV_KEY: dict[str, bytes] = {}
_DERIVED: dict[tuple[bytes, str], bytes] = {}


class VaultError(Exception):
    """A value or file that doesn't decrypt: changed, cut short, or made with another key."""


class KeyMissing(VaultError):
    """No usable server key."""


def key_required() -> bool:
    """Production and staging never make a key of their own: it has to come from the environment."""
    return (os.environ.get("DURACASH_ENV") or "").strip().lower() in {"production", "staging"}


def problem() -> str | None:
    """Why the server key can't be used, for the start-up checks; None when it can."""
    raw = (os.environ.get(KEY_ENV) or "").strip()
    if raw and len(raw) < MIN_KEY_CHARS:
        return f"{KEY_ENV} is shorter than {MIN_KEY_CHARS} characters. Make one with: {GENERATE}"
    if not raw and key_required():
        return (f"{KEY_ENV} is not set. Make one with: {GENERATE} and keep a copy in the password "
                "manager: without it the backups can't be read.")
    return None


def _dev_key() -> bytes:
    """The laptop key in <data dir>/secret.key, made once. Kept for the life of the process, so a
    test that points the data directory elsewhere still reads what it wrote."""
    with _LOCK:
        if "key" in _DEV_KEY:
            return _DEV_KEY["key"]
        path = Path(storage.DATA_DIR) / KEY_FILE
        try:
            raw = path.read_text().strip()
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = secrets.token_urlsafe(32)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(raw + "\n")
            except FileExistsError:  # another process made it first
                raw = path.read_text().strip()
        if len(raw) < MIN_KEY_CHARS:
            raise KeyMissing(f"{path} holds a key shorter than {MIN_KEY_CHARS} characters.")
        _DEV_KEY["key"] = raw.encode()
        return _DEV_KEY["key"]


def _forget_dev_key() -> None:
    """For tests."""
    with _LOCK:
        _DEV_KEY.clear()


def master() -> bytes:
    """The server key. A production or staging process never gets this far without one: the
    server refuses to start (server.main), and so does any command that needs it. A process that
    already loaded the laptop key keeps it, so a test that switches DURACASH_ENV midway still
    reads what it wrote."""
    raw = (os.environ.get(KEY_ENV) or "").strip()
    if not raw and "key" in _DEV_KEY:
        return _DEV_KEY["key"]
    trouble = problem()
    if trouble:
        raise KeyMissing(trouble)
    return raw.encode() if raw else _dev_key()


def key(purpose: str) -> bytes:
    """A 32-byte key for one purpose, derived from the server key."""
    root = master()
    found = _DERIVED.get((root, purpose))
    if found is None:
        found = hmac.digest(root, b"duracash-key:" + purpose.encode(), "sha256")
        if len(_DERIVED) > 64:
            _DERIVED.clear()
        _DERIVED[(root, purpose)] = found
    return found


def _keystream(enc_key: bytes, nonce: bytes, length: int, first_block: int = 0) -> bytes:
    blocks = -(-length // BLOCK)
    stream = b"".join(hmac.digest(enc_key, nonce + struct.pack(">Q", first_block + i), "sha512") for i in range(blocks))
    return stream[:length]


def _xor(data: bytes, stream: bytes) -> bytes:
    if not data:
        return b""
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


def _header(context: str) -> bytes:
    name = context.encode()
    return PREFIX.encode() + struct.pack(">H", len(name)) + name


# ----- small values -----

def sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def seal(text: str, context: str) -> str:
    """Encrypt a short text for one field (`context` names it, e.g. "totp")."""
    if sealed(text):
        return text
    data = str(text).encode("utf-8")
    nonce = secrets.token_bytes(NONCE)
    body = _xor(data, _keystream(key(f"field-enc:{context}"), nonce, len(data)))
    tag = hmac.digest(key(f"field-mac:{context}"), _header(context) + nonce + body, "sha256")
    return PREFIX + base64.urlsafe_b64encode(nonce + body + tag).decode().rstrip("=")


def open_sealed(value: str, context: str) -> str:
    """The text inside a sealed value. A value without the "enc1:" prefix is plaintext from
    before encryption and comes back as it is. Raises VaultError when the value was changed."""
    if not sealed(value):
        return value
    raw = value[len(PREFIX):]
    try:
        blob = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError):
        raise VaultError("An encrypted value doesn't decode.") from None
    if len(blob) < NONCE + TAG:
        raise VaultError("An encrypted value is cut short.")
    nonce, body, tag = blob[:NONCE], blob[NONCE:-TAG], blob[-TAG:]
    want = hmac.digest(key(f"field-mac:{context}"), _header(context) + nonce + body, "sha256")
    if not hmac.compare_digest(want, tag):
        raise VaultError("An encrypted value was changed or made with another key.")
    return _xor(body, _keystream(key(f"field-enc:{context}"), nonce, len(body))).decode("utf-8")


def lookup(text: str, context: str) -> str:
    """A keyed hash for finding a secret without keeping it: "h1:" + HMAC-SHA256 in hex."""
    digest = hmac.new(key(f"lookup:{context}"), str(text).encode("utf-8"), hashlib.sha256).hexdigest()
    return LOOKUP_PREFIX + digest


# ----- files -----

def is_encrypted_file(path: Path | str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def encrypt_file(src: Path | str, dst: Path | str) -> None:
    enc, mac_key = key("file-enc"), key("file-mac")
    nonce = secrets.token_bytes(NONCE)
    mac = hmac.new(mac_key, MAGIC + nonce, hashlib.sha256)
    block = 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(MAGIC + nonce)
        for chunk in iter(lambda: fin.read(CHUNK), b""):
            out = _xor(chunk, _keystream(enc, nonce, len(chunk), block))
            block += len(chunk) // BLOCK
            mac.update(out)
            fout.write(out)
        fout.write(mac.digest())
        fout.flush()
        os.fsync(fout.fileno())


def decrypt_file(src: Path | str, dst: Path | str) -> None:
    """Decrypt into dst. dst is removed and VaultError raised unless the whole file checks out."""
    enc, mac_key = key("file-enc"), key("file-mac")
    size = os.path.getsize(src)
    head = len(MAGIC) + NONCE
    if size < head + TAG:
        raise VaultError("The encrypted file is cut short.")
    try:
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            if fin.read(len(MAGIC)) != MAGIC:
                raise VaultError("Not a Duracash encrypted file.")
            nonce = fin.read(NONCE)
            mac = hmac.new(mac_key, MAGIC + nonce, hashlib.sha256)
            left, block = size - head - TAG, 0
            while left:
                chunk = fin.read(min(CHUNK, left))
                if not chunk:
                    raise VaultError("The encrypted file is cut short.")
                left -= len(chunk)
                mac.update(chunk)
                fout.write(_xor(chunk, _keystream(enc, nonce, len(chunk), block)))
                block += len(chunk) // BLOCK
            if not hmac.compare_digest(mac.digest(), fin.read(TAG)):
                raise VaultError("The encrypted file was changed, is damaged, or was made with another key.")
    except BaseException:
        Path(dst).unlink(missing_ok=True)
        raise
