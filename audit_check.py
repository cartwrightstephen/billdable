"""Check every company's audit hash chain (P1-11). Run it nightly; exit code 1 means a
company's audit trail was changed after it was written, and the output names the row.

    python3 audit_check.py
"""

from __future__ import annotations

import json
import sys

import domain
import migrations
import storage


def main() -> int:
    folder = storage.DATA_DIR / "companies"
    broken = 0
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        state = json.loads(path.read_text())
        migrations.migrate(state)
        result = domain.check_audit(state)
        if result["ok"]:
            print(f"ok      {path.stem}  {result['rows']} rows")
        else:
            broken += 1
            print(f"BROKEN  {path.stem}  first bad row: {result['broken_at']}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
