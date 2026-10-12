"""Where Billdable keeps its data, and how it writes it.

The data directory is set by BILLDABLE_DATA_DIR. It defaults to data/ beside
the app, which is outside static/ and never served. On Render, point it at a
mounted disk; without a disk Render's filesystem does not survive a deploy.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BILLDABLE_DATA_DIR") or ROOT / "data").resolve()


def write_json(path: Path, data: Any) -> None:
    """Write to a temp file, then rename over the target, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
