"""The audit trail: one row per change, chained by hash (P1-11). Moved out of domain (R6-08);
`domain` re-exports every name here."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import domain  # utcnow is looked up there when a row is written, so a test can set the clock

# ---------- audit trail: a hash chain (P1-11) ----------
# Each row carries hash = sha256(previous row's hash + this row's fields). Changing, removing
# or reordering any row breaks every hash after it, which check_audit() reports. The chain
# shows the file was not edited after the fact; it is not a signature, so someone with the
# file and this code could rebuild it. The nightly check and off-site backups are what make
# a quiet rebuild visible.
AUDIT_FIELDS = ("id", "at", "job_id", "actor", "action", "detail")


def _audit_hash(prev: str, row: dict[str, Any]) -> str:
    body = json.dumps({k: row.get(k, "") for k in AUDIT_FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev + "\n" + body).encode("utf-8")).hexdigest()


def seal_audit(state: dict[str, Any]) -> None:
    """Chain rows that have no hash yet (rows written before the chain existed)."""
    prev = ""
    for row in state.get("audit", []):
        if not row.get("hash"):
            row["prev"] = prev
            row["hash"] = _audit_hash(prev, row)
        prev = row["hash"]


def check_audit(state: dict[str, Any]) -> dict[str, Any]:
    """{"ok", "rows", "broken_at"}: broken_at is the id of the first row that doesn't match."""
    prev = ""
    rows = state.get("audit", [])
    for row in rows:
        if row.get("prev") != prev or row.get("hash") != _audit_hash(prev, row):
            return {"ok": False, "rows": len(rows), "broken_at": row.get("id", "?")}
        prev = row["hash"]
    return {"ok": True, "rows": len(rows), "broken_at": None}


def audit(state: dict[str, Any], job_id: str, action: str, detail: str, actor: str = "duracash") -> None:
    rows = state.setdefault("audit", [])
    prev = rows[-1].get("hash", "") if rows else ""
    row = {
        "id": f"aud-{len(rows) + 1}",
        "at": domain.utcnow(),
        "job_id": job_id,
        "actor": actor,
        "action": action,
        "detail": detail,
        "prev": prev,
    }
    row["hash"] = _audit_hash(prev, row)
    rows.append(row)
