"""Check every company's audit hash chain (P1-11, M6). The server runs it once a day from its
hourly loop (monthly.run_once) and alerts on a failure; run it by hand too:

    python3 audit_check.py

Exit code 1 means a company's audit trail was changed after it was written, and the output
names the row.

Besides each chain itself (domain.check_audit: keyed with the server key for rows written since
Nov 27 2026), the check keeps an anchor per company in <data dir>/audit_anchors.json: how many
rows the trail had and the newest row's hash, signed with the server key. The next check refuses
a trail that is shorter than its anchor or whose row at that place has another hash, so a trail
rebuilt from scratch, cut short, or put back into the old unkeyed form is caught too.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

import domain
import storage

ANCHORS_NAME = "audit_anchors.json"
# The anchors file is read, changed and written by the daily check and by a reset (reanchor),
# across processes too (audit_check.py run by hand while the server is up).
ANCHORS_LOCK = storage.ProcessLock(lambda: Path(storage.DATA_DIR) / f".{ANCHORS_NAME}.lock")


def anchors_path() -> Path:
    return Path(storage.DATA_DIR) / ANCHORS_NAME


def _anchor_mac(company_id: str, rows: int, head: str) -> str:
    import vault
    return hmac.new(vault.key("audit-anchor"), f"{company_id}\n{rows}\n{head}".encode(), hashlib.sha256).hexdigest()


def _read_anchors() -> dict[str, Any]:
    try:
        data = json.loads(anchors_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def check_company(company_id: str, state: dict[str, Any], anchors: dict[str, Any]) -> dict[str, Any]:
    """domain.check_audit, plus the anchor from the last check. Updates `anchors` when it passes."""
    result = dict(domain.check_audit(state))
    rows = state.get("audit", [])
    anchor = anchors.get(company_id)
    if result["ok"] and isinstance(anchor, dict):
        n, head = int(anchor.get("rows") or 0), str(anchor.get("hash") or "")
        if not hmac.compare_digest(str(anchor.get("mac") or ""), _anchor_mac(company_id, n, head)):
            result.update(ok=False, broken_at="the anchor from the last check (it was changed)")
        elif n and (len(rows) < n or rows[n - 1].get("hash") != head):
            result.update(ok=False, broken_at=rows[n - 1].get("id", "?") if len(rows) >= n else f"row {n} (the trail is shorter than at the last check)")
    if result["ok"]:
        head = rows[-1].get("hash", "") if rows else ""
        anchors[company_id] = {"rows": len(rows), "hash": head, "mac": _anchor_mac(company_id, len(rows), head)}
    return result


def check_all(companies: Iterable[tuple[str, dict[str, Any] | None]], save: bool = True) -> list[tuple[str, dict[str, Any]]]:
    """(company id, result) for each (company id, state) given; a None state is skipped. The
    anchors of the companies that passed are saved, unless a reset re-anchored one meanwhile."""
    seen = _read_anchors()
    anchors = json.loads(json.dumps(seen))
    out = []
    for company_id, state in companies:
        if state is None:
            continue
        out.append((company_id, check_company(company_id, state, anchors)))
    if save:
        with ANCHORS_LOCK:
            now = _read_anchors()
            for company_id, _ in out:
                if company_id in anchors and now.get(company_id) == seen.get(company_id):
                    now[company_id] = anchors[company_id]
            storage.write_json(anchors_path(), now)
    return out


def reanchor(company_id: str, state: dict[str, Any] | None) -> None:
    """A company whose trail started again on purpose (Reset, which replaces the company): its
    anchor is the new trail, so the next check doesn't take the shorter trail for a changed one.
    With state None (a deleted company) the anchor goes."""
    with ANCHORS_LOCK:
        anchors = _read_anchors()
        if state is None:
            if anchors.pop(company_id, None) is None:
                return
        else:
            rows = state.get("audit", [])
            head = rows[-1].get("hash", "") if rows else ""
            anchors[company_id] = {"rows": len(rows), "hash": head, "mac": _anchor_mac(company_id, len(rows), head)}
        storage.write_json(anchors_path(), anchors)


def run_daily(site, now=None, log: Callable[..., None] | None = None) -> list[str]:
    """From the server's hourly loop: once a day (kept in monthly_jobs.json), every company's
    trail is checked and each broken one alerted. Returns lines for the log."""
    from datetime import datetime, timezone

    import alerts
    import monthly

    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    path = Path(storage.DATA_DIR) / monthly.MONTHLY_PATH_NAME
    done = monthly._read_done(path, "monthly jobs")
    if done is None or done.get("audit_check") == day:
        return []
    deleted = site.deleted()

    def states():
        for company_id in site.company_ids():
            if company_id in deleted:
                continue
            try:
                yield company_id, site.read(company_id)
            except Exception as exc:  # one company that can't be read must not stop the others
                site.log("error", msg=f"{company_id}: audit check couldn't read it: {exc.__class__.__name__}: {exc}")

    results = check_all(states())
    broken = [(cid, r) for cid, r in results if not r["ok"]]
    for company_id, result in broken:
        alerts.notify(f"audit-chain:{company_id}", f"the audit trail of {company_id} was changed after it was written "
                                                   f"(first bad row: {result['broken_at']}). Don't change it; see runbook 5.")
    done["audit_check"] = day
    storage.write_json(path, done)
    return [f"audit check: {len(results)} companies, {len(broken)} broken"] + [f"{cid}  audit trail BROKEN at {r['broken_at']}" for cid, r in broken]


def main() -> int:
    import migrations
    import store

    folder = storage.DATA_DIR / "companies"
    companies = store.open_store(folder)

    def states():
        for company_id in companies.ids() if folder.exists() else []:
            state = companies.load(company_id)
            migrations.migrate(state)
            yield company_id, state

    broken = 0
    for company_id, result in check_all(states()):
        if result["ok"]:
            keyed = "" if result.get("keyed") else "  (rows from before the server key only)"
            print(f"ok      {company_id}  {result['rows']} rows{keyed}")
        else:
            broken += 1
            print(f"BROKEN  {company_id}  first bad row: {result['broken_at']}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
