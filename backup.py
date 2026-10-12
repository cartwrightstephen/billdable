"""Backups of the data directory: automatic, rotated, and restorable.

The server makes one on start-up if the newest is older than BACKUP_HOURS,
then every BACKUP_HOURS (default 24; 0 turns it off). Each backup is a
.tar.gz in <data dir>/backups holding auth.json, every company file, the
inbound tokens, the helper files, the mail counter and (unless
BACKUP_UPLOADS=0) the uploaded files. The newest BACKUP_KEEP (default 14)
are kept.

These backups sit on the same disk as the data. They protect against a bad
change or a bug, not against losing the disk; copy them off the server too.

Command line (run with the server stopped for restore):

    python3 backup.py now              make one now
    python3 backup.py list             list backups, newest first
    python3 backup.py restore FILE     restore one; the current data is moved aside first
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tarfile
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import storage

PREFIX = "billdable-"
SUFFIX = ".tar.gz"
_RUN_LOCK = threading.Lock()


def settings() -> dict[str, int]:
    def num(name: str, default: int) -> int:
        try:
            return max(0, int(os.environ.get(name) or default))
        except ValueError:
            return default

    return {
        "hours": num("BACKUP_HOURS", 24),
        "keep": max(1, num("BACKUP_KEEP", 14)),
        "uploads": 0 if os.environ.get("BACKUP_UPLOADS") == "0" else 1,
    }


def folder() -> Path:
    return Path(storage.DATA_DIR) / "backups"


def backups() -> list[Path]:
    if not folder().exists():
        return []
    return sorted(folder().glob(f"{PREFIX}*{SUFFIX}"), reverse=True)


def newest_time() -> str | None:
    found = backups()
    if not found:
        return None
    return datetime.fromtimestamp(found[0].stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def make_backup(locks: dict[str, Any] | None = None) -> Path:
    """Write one backup. JSON files are read under the lock that guards them, one lock at a time
    (never nested, so a backup can't deadlock with a request); uploads never change once written."""
    locks = locks or {}
    data_dir = Path(storage.DATA_DIR)
    with _RUN_LOCK:
        members: list[tuple[str, bytes]] = []

        def grab(rel: str, lock: Any) -> None:
            path = data_dir / rel
            with lock or nullcontext():
                if path.is_file():
                    members.append((rel, path.read_bytes()))

        grab("auth.json", locks.get("auth"))
        companies = data_dir / "companies"
        with locks.get("companies") or nullcontext():
            if companies.exists():
                for path in sorted(companies.glob("*.json")):
                    members.append((f"companies/{path.name}", path.read_bytes()))
        for rel, key in (("inbound_tokens.json", "tokens"), ("helper_answers.json", None), ("helper_log.json", None), ("mail_sends.json", None)):
            grab(rel, locks.get(key) if key else None)

        folder().mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        final = folder() / f"{PREFIX}{stamp}{SUFFIX}"
        tmp = folder() / f".{final.name}.tmp"
        with tarfile.open(tmp, "w:gz") as tar:
            for rel, data in members:
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(data))
            uploads = data_dir / "uploads"
            if settings()["uploads"] and uploads.exists():
                for path in sorted(uploads.rglob("*")):
                    if path.is_file() and not path.name.startswith("."):
                        tar.add(path, arcname=str(path.relative_to(data_dir)), recursive=False)
        os.replace(tmp, final)
        rotate()
        return final


def rotate() -> list[Path]:
    keep = settings()["keep"]
    removed = []
    for old in backups()[keep:]:
        try:
            old.unlink()
            removed.append(old)
        except OSError:
            pass
    return removed


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Only plain files at relative paths inside the data directory."""
    allowed_top = {"auth.json", "inbound_tokens.json", "helper_answers.json", "helper_log.json", "mail_sends.json", "companies", "uploads"}
    out = []
    for m in tar.getmembers():
        parts = Path(m.name).parts
        if not m.isfile() or not parts or m.name.startswith("/") or ".." in parts or parts[0] not in allowed_top:
            raise ValueError(f"Refusing to restore {m.name!r}: not a Billdable backup member.")
        out.append(m)
    return out


def restore(path: str | Path) -> Path:
    """Restore a backup into the data directory. Run with the server stopped.
    The current data is moved to <data dir>/pre-restore-<time>/ first, never deleted."""
    data_dir = Path(storage.DATA_DIR)
    path = Path(path)
    with tarfile.open(path, "r:gz") as tar:
        members = _safe_members(tar)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        aside = data_dir / f"pre-restore-{stamp}"
        aside.mkdir(parents=True)
        for name in ("auth.json", "inbound_tokens.json", "helper_answers.json", "helper_log.json", "mail_sends.json", "companies", "uploads"):
            if (data_dir / name).exists():
                shutil.move(str(data_dir / name), str(aside / name))
        for m in members:
            target = data_dir / m.name
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(m)
            if source is None:
                continue
            target.write_bytes(source.read())
    return aside


def start_scheduler(locks: dict[str, Any], log: Callable[[str], None] = print) -> threading.Thread | None:
    """Back up now if the newest backup is too old, then every BACKUP_HOURS. Daemon thread."""
    hours = settings()["hours"]
    if not hours:
        return None

    def due() -> bool:
        found = backups()
        return not found or time.time() - found[0].stat().st_mtime >= hours * 3600

    def loop() -> None:
        while True:
            if due():
                try:
                    made = make_backup(locks)
                    log(f"backup written: {made.name}")
                except Exception as exc:  # a failed backup must never stop the server
                    log(f"backup failed: {exc.__class__.__name__}: {exc}")
            time.sleep(min(3600, hours * 3600))

    thread = threading.Thread(target=loop, name="backups", daemon=True)
    thread.start()
    return thread


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"now", "list", "restore"}:
        print(__doc__)
        return 2
    if argv[1] == "now":
        print(make_backup())
    elif argv[1] == "list":
        for b in backups():
            print(f"{b.name}  {b.stat().st_size:,} bytes")
    else:
        if len(argv) < 3:
            print("Usage: python3 backup.py restore FILE")
            return 2
        aside = restore(argv[2])
        print(f"Restored {argv[2]}. The previous data is in {aside}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
