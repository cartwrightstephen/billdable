"""Copy each backup off the server, encrypted (P0-2).

Local backups share a disk with the data, so they don't survive losing the disk
or the hosting account. After each backup, when the BACKUP_S3_* settings are
filled in, the archive is encrypted with age (https://age-encryption.org) to
BACKUP_AGE_RECIPIENT and PUT to S3-compatible storage: Backblaze B2, Cloudflare
R2 or Amazon S3. Use a key that can only write, and turn on Object Lock (or
B2's file lock) on the bucket so an upload can't be deleted or overwritten for
about 35 days. Nothing is ever uploaded unencrypted: without age or a
recipient the upload is refused and logged.

Requests are signed with AWS Signature Version 4, standard library only.

Restore on a laptop: download the .age file, then
    age -d -i key.txt billdable-....tar.gz.age > backup.tar.gz
    python3 backup.py restore backup.tar.gz
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import storage

CHUNK = 1024 * 1024


def settings() -> dict[str, str]:
    env = os.environ.get
    return {
        "endpoint": (env("BACKUP_S3_ENDPOINT") or "").strip().rstrip("/"),
        "bucket": (env("BACKUP_S3_BUCKET") or "").strip(),
        "region": (env("BACKUP_S3_REGION") or "").strip(),
        "key_id": (env("BACKUP_S3_KEY_ID") or "").strip(),
        "secret": env("BACKUP_S3_SECRET") or "",
        "prefix": (env("BACKUP_S3_PREFIX") or "billdable/").lstrip("/"),
        "recipient": (env("BACKUP_AGE_RECIPIENT") or "").strip(),
        "age": (env("BACKUP_AGE_BINARY") or "age").strip(),
    }


def configured() -> bool:
    s = settings()
    return all(s[k] for k in ("endpoint", "bucket", "region", "key_id", "secret"))


def status_path() -> Path:
    return Path(storage.DATA_DIR) / "backups" / "offsite.json"


def last_upload() -> str | None:
    try:
        return json.loads(status_path().read_text()).get("uploaded_at")
    except (OSError, ValueError):
        return None


class OffsiteError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign(method: str, host: str, path: str, headers: dict[str, str], payload_hash: str,
         key_id: str, secret: str, region: str, now: datetime, query: str = "") -> str:
    """The Authorization header for an S3 request (SigV4). `path` must already be URI-encoded;
    `headers` must include host, x-amz-date and x-amz-content-sha256."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    canon = {k.lower().strip(): " ".join(str(v).split()) for k, v in headers.items()}
    names = sorted(canon)
    canonical_request = "\n".join([
        method,
        path,
        query,
        "".join(f"{n}:{canon[n]}\n" for n in names),
        ";".join(names),
        payload_hash,
    ])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), day), region), "s3"), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"


def encrypt(path: Path) -> Path:
    """age-encrypt `path` to BACKUP_AGE_RECIPIENT; returns the .age file beside it."""
    s = settings()
    if not s["recipient"]:
        raise OffsiteError("BACKUP_AGE_RECIPIENT is not set; refusing to upload a backup unencrypted.")
    binary = shutil.which(s["age"])
    if not binary:
        raise OffsiteError(f"The age program ({s['age']}) was not found; refusing to upload a backup unencrypted.")
    out = path.with_name(path.name + ".age")
    tmp = path.with_name(f".{out.name}.tmp")
    try:
        subprocess.run([binary, "-r", s["recipient"], "-o", str(tmp), str(path)], check=True,
                       capture_output=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        tmp.unlink(missing_ok=True)
        raise OffsiteError(f"age failed: {exc.stderr.decode(errors='replace').strip()[:200]}") from None
    os.replace(tmp, out)
    return out


def put(path: Path, object_key: str, now: datetime | None = None) -> None:
    """Stream one file to <endpoint>/<bucket>/<object_key> (path-style)."""
    s = settings()
    url = urlparse(s["endpoint"])
    if url.scheme != "https" and url.hostname not in {"127.0.0.1", "localhost"}:
        raise OffsiteError("BACKUP_S3_ENDPOINT must be an https:// address.")
    now = now or datetime.now(timezone.utc)
    payload_hash = _sha256_file(path)
    size = path.stat().st_size
    request_path = "/" + quote(s["bucket"], safe="") + "/" + quote(object_key, safe="/-_.~")
    headers = {
        "host": url.netloc,
        "x-amz-date": now.strftime("%Y%m%dT%H%M%SZ"),
        "x-amz-content-sha256": payload_hash,
        "content-length": str(size),
        "content-type": "application/octet-stream",
    }
    headers["authorization"] = sign("PUT", url.netloc, request_path, {k: v for k, v in headers.items()},
                                    payload_hash, s["key_id"], s["secret"], s["region"], now)
    conn_cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(url.hostname, url.port, timeout=120)
    try:
        conn.putrequest("PUT", request_path, skip_host=True, skip_accept_encoding=True)
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                conn.send(block)
        res = conn.getresponse()
        body = res.read(500).decode(errors="replace")
    finally:
        conn.close()
    if res.status != 200:
        raise OffsiteError(f"Upload refused: HTTP {res.status} {body.strip()[:200]}")


def upload(backup: Path) -> str:
    """Encrypt and upload one backup; returns the object key. The .age file is removed afterwards."""
    if not configured():
        raise OffsiteError("Off-site backup is not set up (BACKUP_S3_* settings).")
    encrypted = encrypt(backup)
    try:
        key = settings()["prefix"] + encrypted.name
        put(encrypted, key)
    finally:
        encrypted.unlink(missing_ok=True)
    status: dict[str, Any] = {"uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "object": key}
    storage.write_json(status_path(), status)
    return key
