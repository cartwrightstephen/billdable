"""Check every company's audit hash chain (P1-11). Run it nightly; exit code 1 means a
company's audit trail was changed after it was written, and the output names the row.

    python3 audit_check.py
"""

from __future__ import annotations

import sys

import domain
import migrations
import storage
import store


def main() -> int:
    folder = storage.DATA_DIR / "companies"
    companies = store.open_store(folder)
    broken = 0
    for company_id in companies.ids() if folder.exists() else []:
        state = companies.load(company_id)
        migrations.migrate(state)
        result = domain.check_audit(state)
        if result["ok"]:
            print(f"ok      {company_id}  {result['rows']} rows")
        else:
            broken += 1
            print(f"BROKEN  {company_id}  first bad row: {result['broken_at']}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
