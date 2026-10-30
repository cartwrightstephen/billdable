"""Schema versions for a company file, and the steps between them (P1-12).

Every company file carries schema_version. load_company() runs migrate() on what it
reads, so code only ever sees the current shape; the next save writes it back. To change
the shape of the data: add a step at the end of STEPS that turns version N into N + 1,
and a test that loads a version-N file. Steps must be safe to run on any file of their
version, including ones written by hand.
"""

from __future__ import annotations

from typing import Any, Callable

import domain
import payapp_pdf


def _v1_to_v2(state: dict[str, Any]) -> None:
    """Fill lists older files lack, and seal the existing audit rows into a hash chain."""
    for key in ("payers", "jobs", "audit", "email_log", "daily_records", "inbox", "files"):
        state.setdefault(key, [])
    state.setdefault("company", {})
    domain.seal_audit(state)


def _v2_to_v3(state: dict[str, Any]) -> None:
    """Paperwork, not packet (Oct 23 note 21): a customer still holding the old default for
    "They send pay apps back when" gets the new one. Anything the person typed is left alone."""
    for payer in state.get("payers", []):
        if payer.get("rejects_when") == OLD_REJECTS_WHEN:
            payer["rejects_when"] = NEW_REJECTS_WHEN


OLD_REJECTS_WHEN = "Missing required packet items."
NEW_REJECTS_WHEN = "Missing required paperwork."


def _v3_to_v4(state: dict[str, Any]) -> None:
    """A pay app keeps the contract it was sent against (contract_at_send), so a change order
    approved or retainage released later doesn't change its PDF. Pay apps sent before that was
    kept get it worked out from the dates of the change orders and retainage payments."""
    for job in state.get("jobs", []):
        for inv in job.get("invoices", []):
            if inv.get("sent_at") and not inv.get("contract_at_send") and "contract_cents" in job:
                inv["contract_at_send"] = payapp_pdf.contract_terms(job, inv["sent_at"])


def _tasks_daily_lists(state: dict[str, Any]) -> None:
    """tasks-daily stream (G5, T3): the To Do list's hand-added tasks and who each computed row is
    assigned to start empty. Daily records keep their shape; the new fields are optional."""
    if not isinstance(state.get("tasks"), list):
        state["tasks"] = []
    if not isinstance(state.get("task_assignments"), dict):
        state["task_assignments"] = {}


STEPS: list[Callable[[dict[str, Any]], None]] = [_v1_to_v2, _v2_to_v3, _v3_to_v4, _tasks_daily_lists]
SCHEMA_VERSION = len(STEPS) + 1


def migrate(state: dict[str, Any]) -> bool:
    """Bring a company file to SCHEMA_VERSION in place. Returns whether anything ran.
    A file from newer code is refused rather than guessed at."""
    version = int(state.get("schema_version") or 1)
    if version > SCHEMA_VERSION:
        raise ValueError(f"Company file is schema {version}; this code only knows up to {SCHEMA_VERSION}. Deploy the newer code.")
    for step in STEPS[version - 1:]:
        step(state)
    changed = version != SCHEMA_VERSION
    state["schema_version"] = SCHEMA_VERSION
    return changed
