"""The audit trail: one row per change, chained by hash (P1-11), keyed with the server key (M6).
Moved out of domain (R6-08); `domain` re-exports every name here."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import domain  # utcnow is looked up there when a row is written, so a test can set the clock

# ---------- audit trail: a keyed hash chain (P1-11, M6) ----------
# Each row carries hash = H(previous row's hash + this row's fields). Changing, removing or
# reordering any row breaks every hash after it, which check_audit() reports.
#
# Version 1 rows (no "v"; written before Nov 27 2026): H is plain SHA-256, so someone with the
# file and this code could rebuild them. They still verify as they are.
# Version 2 rows ("v": 2): H is HMAC-SHA256 under a key derived from DURACASH_SECRET_KEY
# (vault.key("audit-chain")), so the chain can't be rebuilt without the server key. The first
# version 2 row's prev is the last version 1 hash, so it seals everything before it; a version
# 1 row after a version 2 row is refused (no going back). A row about a signature (a change
# order signed online, a pay app approval) also carries the SHA-256 of the signature record
# ("record_sha256") and where it is ("record"); check_audit() checks that the record on the job
# still has that hash.
AUDIT_FIELDS = ("id", "at", "job_id", "actor", "action", "detail")
KEYED_FIELDS = AUDIT_FIELDS + ("v", "record", "record_sha256")
VERSION = 2


def _audit_hash(prev: str, row: dict[str, Any]) -> str:
    """Version 1: plain SHA-256 (kept for rows written before the key)."""
    body = json.dumps({k: row.get(k, "") for k in AUDIT_FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev + "\n" + body).encode("utf-8")).hexdigest()


def _chain_key() -> bytes:
    import vault
    return vault.key("audit-chain")


def _keyed_hash(prev: str, row: dict[str, Any]) -> str:
    body = json.dumps({k: row.get(k, "") for k in KEYED_FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(_chain_key(), (prev + "\n" + body).encode("utf-8"), hashlib.sha256).hexdigest()


def _row_hash(prev: str, row: dict[str, Any]) -> str:
    return _keyed_hash(prev, row) if row.get("v") == VERSION else _audit_hash(prev, row)


def record_sha256(record: Any) -> str:
    body = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def signature_record(state: dict[str, Any], ref: str) -> Any:
    """The signature record a row names, or None when it is no longer on the job: a change
    order's online signature ("co-signature:JOB:CO") or one person's approval of a pay app
    ("approval:JOB:INVOICE:USER")."""
    parts = str(ref or "").split(":")
    job = next((j for j in state.get("jobs", []) if len(parts) > 1 and j.get("id") == parts[1]), None)
    if job is None:
        return None
    if parts[0] == "co-signature" and len(parts) == 3:
        co = next((c for c in job.get("change_orders", []) if c.get("id") == parts[2]), None)
        return (co or {}).get("signature")
    if parts[0] == "approval" and len(parts) == 4:
        inv = next((i for i in job.get("invoices", []) if i.get("id") == parts[2]), None)
        return next((a for a in (inv or {}).get("approvals") or [] if a.get("id") == parts[3]), None)
    return None


def seal_audit(state: dict[str, Any]) -> None:
    """Chain rows that have no hash yet (rows written before the chain existed)."""
    prev = ""
    for row in state.get("audit", []):
        if not row.get("hash"):
            row["prev"] = prev
            row["hash"] = _audit_hash(prev, row)
        prev = row["hash"]


def check_audit(state: dict[str, Any]) -> dict[str, Any]:
    """{"ok", "rows", "broken_at", "keyed"}: broken_at is the id of the first row that doesn't
    match, or of the row whose signature record was changed; keyed is whether the newest row
    is keyed with the server key (a trail with only rows from before the key is not)."""
    prev = ""
    rows = state.get("audit", [])
    seen_keyed = False
    latest: dict[str, tuple[str, str]] = {}
    for row in rows:
        keyed = row.get("v") == VERSION
        if (row.get("prev") != prev or (seen_keyed and not keyed)
                or not hmac.compare_digest(str(row.get("hash", "")), _row_hash(prev, row))):
            return {"ok": False, "rows": len(rows), "broken_at": row.get("id", "?"), "keyed": seen_keyed}
        seen_keyed = seen_keyed or keyed
        if keyed and row.get("record"):
            latest[row["record"]] = (row.get("record_sha256", ""), row.get("id", "?"))
        prev = row["hash"]
    for ref, (digest, row_id) in latest.items():
        record = signature_record(state, ref)
        if record is not None and record_sha256(record) != digest:
            return {"ok": False, "rows": len(rows), "broken_at": row_id, "keyed": seen_keyed}
    return {"ok": True, "rows": len(rows), "broken_at": None, "keyed": seen_keyed or not rows}


def audit(state: dict[str, Any], job_id: str, action: str, detail: str, actor: str = "duracash",
          record: tuple[str, Any] | None = None) -> None:
    """Add a row. `record` is (where, the signature record) for a row about a signature."""
    rows = state.setdefault("audit", [])
    prev = rows[-1].get("hash", "") if rows else ""
    row = {
        "id": f"aud-{len(rows) + 1}",
        "at": domain.utcnow(),
        "job_id": job_id,
        "actor": actor,
        "action": action,
        "detail": detail,
        "v": VERSION,
        "prev": prev,
    }
    if record is not None:
        row["record"], row["record_sha256"] = record[0], record_sha256(record[1])
    row["hash"] = _keyed_hash(prev, row)
    rows.append(row)
