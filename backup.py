"""Backups of the data directory: automatic, rotated, and restorable.

The server makes one on start-up if the newest is older than BACKUP_HOURS,
then every BACKUP_HOURS (default 24; 0 turns it off). Each backup is a
.tar.gz in <data dir>/backups holding auth.json, every company file, the
inbound tokens, the helper files, the mail counter, the list of deleted
companies, the QuickBooks connections, the monthly and daily job records
(R6-06) and (unless BACKUP_UPLOADS=0) the uploaded files.

Each local backup is encrypted with the server key (H3, vault.py: DURACASH_SECRET_KEY) and
named .tar.gz.enc; restore and drill decrypt it, and still read the plain .tar.gz backups made
before. Without the key a local backup can't be read: keep a copy of DURACASH_SECRET_KEY in the
password manager.

Retention (M5): a local backup is removed once it is BACKUP_KEEP_DAYS old (default 14), and
beyond the newest BACKUP_KEEP (default 14) whatever its age. The same days apply to what a
restore moved aside (pre-restore-<time>/) and to the copies kept of files that couldn't be read
(<name>.bad-<time>). The hourly sweep runs even when BACKUP_HOURS=0.

These backups sit on the same disk as the data. They protect against a bad
change or a bug, not against losing the disk. When the BACKUP_S3_* settings
are filled in, each new backup is also encrypted and uploaded off the server
(offsite.py). Every backup holds every upload, so on a disk with many photos
set BACKUP_UPLOADS=0 once off-site is set up: local backups then leave the
uploads out and the off-site copy still has them (R6-06).

Command line (run with the server stopped for restore):

    python3 backup.py now              make one now
    python3 backup.py list             list backups, newest first
    python3 backup.py restore FILE     restore one; the current data is moved aside first
    python3 backup.py upload [FILE]    encrypt and upload one off-site (default: the newest)
    python3 backup.py drill [FILE]     restore one into a temporary folder and check it reads
                                       (default: the newest); the live data is not touched
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tarfile
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import alerts
import offsite
import storage
import store
import vault

# Files at the top of the data directory that a backup holds and a restore puts back. Each is
# small and guarded by its own lock, if any (the key in make_backup's `locks`).
TOP_FILES = (("auth.json", "auth"), ("inbound_tokens.json", "tokens"), ("helper_answers.json", None), ("helper_log.json", None),
             ("mail_sends.json", None), ("deleted_companies.json", "deleted"), ("qbo_connections.json", "qbo"),
             ("monthly_jobs.json", None), ("daily_jobs.json", None))
TOP_NAMES = tuple(name for name, _ in TOP_FILES)

PREFIX = "duracash-"
OLD_PREFIX = "billdable-"  # backups made before the rename to Duracash
SUFFIX = ".tar.gz"
ENC_SUFFIX = ".tar.gz.enc"   # encrypted with the server key (H3)
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
        "keep_days": max(1, num("BACKUP_KEEP_DAYS", 14)),
        "offsite_days": max(1, num("BACKUP_OFFSITE_DAYS", 35)),
        "uploads": 0 if os.environ.get("BACKUP_UPLOADS") == "0" else 1,
    }


def folder() -> Path:
    return Path(storage.DATA_DIR) / "backups"


def backups() -> list[Path]:
    if not folder().exists():
        return []
    found = [p for prefix in (PREFIX, OLD_PREFIX) for suffix in (SUFFIX, ENC_SUFFIX) for p in folder().glob(f"{prefix}*{suffix}")]
    return sorted(found, key=lambda p: p.name.split("-", 1)[1], reverse=True)


def newest_time() -> str | None:
    found = backups()
    if not found:
        return None
    return datetime.fromtimestamp(found[0].stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def make_backup(locks: dict[str, Any] | None = None, uploads: bool | None = None, into: Path | None = None) -> Path:
    """Write one backup. JSON files are read under the lock that guards them, one lock at a time
    (never nested, so a backup can't deadlock with a request); company files are replaced whole
    by a rename and the SQLite database is copied with its own backup API, so neither needs a
    lock; uploads never change once written. `uploads` overrides BACKUP_UPLOADS; `into` writes
    it to another folder, outside the rotation and not encrypted with the server key (the off-site
    copy with uploads, which offsite.py encrypts with age)."""
    locks = locks or {}
    with_uploads = bool(settings()["uploads"]) if uploads is None else uploads
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
        if companies.exists():
            # Company files (the JSON layout, and the snapshots taken before a reset) and the database.
            for path in sorted(companies.rglob("*.json")):
                members.append((str(path.relative_to(data_dir)), path.read_bytes()))
            if store.db_file(companies).exists():
                members.extend(store.SqliteStore(companies).backup_members())
        for rel, key in TOP_FILES[1:]:
            grab(rel, locks.get(key) if key else None)

        target = into or folder()
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        encrypt = into is None
        final = target / f"{PREFIX}{stamp}{ENC_SUFFIX if encrypt else SUFFIX}"
        tmp = target / f".{PREFIX}{stamp}{SUFFIX}.tmp"
        with tarfile.open(tmp, "w:gz") as tar:
            for rel, data in members:
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(data))
            folder_of_uploads = data_dir / "uploads"
            if with_uploads and folder_of_uploads.exists():
                for path in sorted(folder_of_uploads.rglob("*")):
                    if path.is_file() and not path.name.startswith("."):
                        tar.add(path, arcname=str(path.relative_to(data_dir)), recursive=False)
            elif not with_uploads:
                # Says so inside the archive, so the drill doesn't look for them (offsite_copy has them).
                note = b"Uploads are left out of this backup (BACKUP_UPLOADS=0); the off-site copy holds them.\n"
                info = tarfile.TarInfo(UPLOADS_LEFT_OUT)
                info.size, info.mtime = len(note), int(time.time())
                tar.addfile(info, io.BytesIO(note))
        if encrypt:
            sealed = target / f".{final.name}.tmp"
            try:
                vault.encrypt_file(tmp, sealed)
            finally:
                tmp.unlink(missing_ok=True)
            os.replace(sealed, final)
        else:
            os.replace(tmp, final)
        if into is None:
            rotate()
        return final


def _plain(path: Path, into: Path) -> tuple[Path, bool]:
    """The backup as a plain .tar.gz: an encrypted one is decrypted into the folder `into`
    (the caller removes it; the bool says so), an old plain one is used as it is."""
    path = Path(path)
    if not vault.is_encrypted_file(path):
        return path, False
    into.mkdir(parents=True, exist_ok=True)
    name = path.name[:-len(".enc")] if path.name.endswith(".enc") else path.name + SUFFIX
    out = into / name
    vault.decrypt_file(path, out)
    return out, True


UPLOADS_LEFT_OUT = "uploads-left-out.txt"


@contextmanager
def open_archive(path: str | Path):
    """A backup opened for reading as a tarfile, encrypted or not (for tests and checks)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        plain, _ = _plain(Path(path), Path(tmp))
        with tarfile.open(plain, "r:gz") as tar:
            yield tar


def offsite_copy(made: Path, locks: dict[str, Any] | None = None) -> str:
    """Upload a backup off-site. A local backup without the uploads (BACKUP_UPLOADS=0) goes
    with them: a second archive is made for the upload and removed after (R6-06)."""
    staging = folder() / "offsite-staging"
    if settings()["uploads"] or not (Path(storage.DATA_DIR) / "uploads").exists():
        # Off-site copies are encrypted with age instead, so a laptop with the age key alone reads them.
        plain, made_here = _plain(made, staging)
        try:
            return offsite.upload(plain)
        finally:
            if made_here:
                plain.unlink(missing_ok=True)
    full = make_backup(locks, uploads=True, into=staging)
    try:
        return offsite.upload(full)
    finally:
        full.unlink(missing_ok=True)


def _stamp_time(name: str) -> float | None:
    """The time in a backup's or a restore's name (duracash-20261127T101500123456Z..., pre-restore-20261127T101500Z)."""
    import re
    m = re.search(r"(\d{8}T\d{6})(\d*)Z", name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _age_days(path: Path, now: float) -> float:
    try:
        st = path.stat()
        # A copy of a bad file keeps the original's time (copy2); its ctime is when it was copied.
        made = _stamp_time(path.name) or max(st.st_mtime, st.st_ctime)
    except OSError:
        return 0.0
    return (now - made) / 86400


def rotate(now: float | None = None) -> list[Path]:
    """Remove the backups beyond the newest BACKUP_KEEP, and any BACKUP_KEEP_DAYS old or more (M5)."""
    cfg, now = settings(), time.time() if now is None else now
    removed = []
    for i, old in enumerate(backups()):
        if i >= cfg["keep"] or _age_days(old, now) >= cfg["keep_days"]:
            try:
                old.unlink()
                removed.append(old)
            except OSError:
                pass
    return removed


def purge(now: float | None = None) -> list[Path]:
    """The retention sweep (M5), hourly from the scheduler: old backups (rotate), what restores
    moved aside (pre-restore-<time>/) and copies of files that couldn't be read (*.bad-*) once
    they are BACKUP_KEEP_DAYS old, and leftovers of an interrupted restore or upload after a day.
    So a deleted company's data is gone from this disk within BACKUP_KEEP_DAYS."""
    cfg, now = settings(), time.time() if now is None else now
    data_dir = Path(storage.DATA_DIR)
    removed = rotate(now)
    candidates = [(p, cfg["keep_days"]) for p in data_dir.glob("pre-restore-*") if p.is_dir()]
    candidates += [(p, cfg["keep_days"]) for p in [*data_dir.glob("*.bad-*"), *(data_dir / "companies").rglob("*.bad-*")] if p.is_file()]
    candidates += [(p, 1) for p in data_dir.glob(".restore-staging-*")]
    candidates += [(p, 1) for p in (folder() / "offsite-staging").glob("*")] if (folder() / "offsite-staging").is_dir() else []
    for path, days in candidates:
        if _age_days(path, now) < days:
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(path)
        except OSError:
            pass
    return removed


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Only plain files at relative paths inside the data directory."""
    allowed_top = {*TOP_NAMES, UPLOADS_LEFT_OUT, "companies", "uploads"}
    out = []
    for m in tar.getmembers():
        parts = Path(m.name).parts
        if not m.isfile() or not parts or m.name.startswith("/") or ".." in parts or parts[0] not in allowed_top:
            raise ValueError(f"Refusing to restore {m.name!r}: not a Duracash backup member.")
        out.append(m)
    return out


def restore(path: str | Path) -> Path:
    """Restore a backup into the data directory. Run with the server stopped.

    The archive is checked and unpacked into a staging folder first; only a complete, valid
    backup (with auth.json and the companies folder) replaces anything. The current data is
    moved to <data dir>/pre-restore-<time>/, never deleted."""
    data_dir = Path(storage.DATA_DIR)
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    staging = data_dir / f".restore-staging-{stamp}"
    archive_dir = data_dir / f".restore-staging-{stamp}-archive"  # an encrypted backup, decrypted
    try:
        plain, _ = _plain(path, archive_dir)
        with tarfile.open(plain, "r:gz") as tar:
            members = _safe_members(tar)
            names = {m.name for m in members}
            if "auth.json" not in names or not any(n.startswith("companies/") for n in names):
                raise ValueError("Not a complete Duracash backup: auth.json or the companies folder is missing.")
            staging.mkdir(parents=True)
            for m in members:
                target = staging / m.name
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(m)
                if source is None:
                    raise ValueError(f"Could not read {m.name} from the backup.")
                target.write_bytes(source.read())
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(archive_dir, ignore_errors=True)
    aside = data_dir / f"pre-restore-{stamp}"
    aside.mkdir(parents=True)
    # Everything a backup holds is moved aside, even what this (perhaps older) backup lacks:
    # a list of deleted companies or QuickBooks connections newer than the backup must not
    # stay to contradict the data put back (R6-06).
    # A backup made without the uploads leaves the ones on the disk in place: files never change
    # once written, so they are what the restored records point to (the off-site copy has them too).
    left_out = (staging / UPLOADS_LEFT_OUT).exists()
    for name in (*TOP_NAMES, UPLOADS_LEFT_OUT, "companies", *(() if left_out else ("uploads",))):
        if (data_dir / name).exists():
            shutil.move(str(data_dir / name), str(aside / name))
    (staging / UPLOADS_LEFT_OUT).unlink(missing_ok=True)
    for item in staging.iterdir():
        shutil.move(str(item), str(data_dir / item.name))
    staging.rmdir()
    return aside


def start_scheduler(locks: dict[str, Any], log: Callable[[str], None] = print) -> threading.Thread | None:
    """Back up now if the newest backup is too old, then every BACKUP_HOURS; and every hour,
    even with BACKUP_HOURS=0, the retention sweep (purge). Daemon thread."""
    hours = settings()["hours"]

    def due() -> bool:
        found = backups()
        return bool(hours) and (not found or time.time() - found[0].stat().st_mtime >= hours * 3600)

    def loop() -> None:
        while True:
            try:
                for gone in purge():
                    log(f"retention: removed {gone.name}")
            except Exception as exc:
                log(f"retention sweep failed: {exc.__class__.__name__}: {exc}")
                alerts.notify(f"retention:{exc.__class__.__name__}", f"the backup retention sweep failed ({exc.__class__.__name__}); see the server log")
            try:
                if due():
                    made = make_backup(locks)
                    log(f"backup written: {made.name}")
                    if offsite.configured():
                        log(f"backup uploaded off-site: {offsite_copy(made, locks)}")
            except Exception as exc:  # a failed backup must never stop the backups that follow
                log(f"backup failed: {exc.__class__.__name__}: {exc}")
                alerts.notify(f"backup:{exc.__class__.__name__}", f"backup failed ({exc.__class__.__name__}); see the server log")
            time.sleep(min(3600, hours * 3600) if hours else 3600)

    thread = threading.Thread(target=loop, name="backups", daemon=True)
    thread.start()
    return thread


def drill(path: str | Path) -> tuple[list[str], list[str]]:
    """The restore drill (runbook 2): unpack a backup into a temporary folder, never the live
    data, and check that it would work. Returns (report lines, problems); no problems = pass."""
    import json
    import tempfile

    import domain
    import migrations
    import store

    report: list[str] = []
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "data"
        plain, _ = _plain(Path(path), Path(tmp) / "archive")
        with tarfile.open(plain, "r:gz") as tar:
            for m in _safe_members(tar):
                target = root / m.name
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(m)
                if source is None:
                    problems.append(f"{m.name} couldn't be read from the backup.")
                    continue
                target.write_bytes(source.read())
        try:
            accounts = json.loads((root / "auth.json").read_text())
        except (OSError, ValueError) as exc:
            return report, [f"auth.json doesn't read: {exc}"]
        users = accounts.get("users", [])
        report.append(f"accounts: {len(users)} users, {len(accounts.get('sessions', {}))} sessions")
        folder = root / "companies"
        companies = store.SqliteStore(folder) if store.db_file(folder).exists() else store.JsonStore(folder)
        ids = companies.ids() if folder.exists() else []
        try:
            deleted = set(json.loads((root / "deleted_companies.json").read_text()))
        except (OSError, ValueError):
            deleted = set()
        for company_id in sorted({u["company_id"] for u in users} - set(ids) - deleted):
            problems.append(f"users of {company_id} have no company data.")
        # Check the uploads unless this backup says it left them out.
        uploads = bool(settings()["uploads"]) and not (root / UPLOADS_LEFT_OUT).exists()
        for company_id in ids:
            try:
                state = companies.load(company_id)
                migrations.migrate(state)
            except Exception as exc:  # one broken company must not hide the others
                problems.append(f"{company_id} doesn't load: {exc.__class__.__name__}: {exc}")
                continue
            chain = domain.check_audit(state)
            if not chain["ok"]:
                problems.append(f"{company_id}: the audit chain is broken at {chain['broken_at']}.")
            missing = [f["name"] for f in state.get("files", [])
                       if uploads and not (root / "uploads" / company_id / f"{f['id']}{f['ext']}").is_file()]
            if missing:
                problems.append(f"{company_id}: {len(missing)} uploaded files aren't in the backup, e.g. {missing[0]}.")
            report.append(f"{company_id}: {state.get('company', {}).get('name', '')!r}, {len(state.get('jobs', []))} jobs, "
                          f"{len(state.get('files', []))} files, audit {'ok' if chain['ok'] else 'BROKEN'} ({chain['rows']} rows)")
        report.append(f"storage: {companies.kind}, {len(ids)} companies")
    return report, problems


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"now", "list", "restore", "upload", "drill"}:
        print(__doc__)
        return 2
    if argv[1] == "now":
        print(make_backup())
    elif argv[1] == "upload":
        target = Path(argv[2]) if len(argv) > 2 else (backups() or [None])[0]
        if target is None:
            print("No backup to upload. Make one with: python3 backup.py now")
            return 1
        print(f"Uploaded {target.name} as {offsite_copy(target)}")
    elif argv[1] == "drill":
        target = Path(argv[2]) if len(argv) > 2 else (backups() or [None])[0]
        if target is None:
            print("No backup to check. Make one with: python3 backup.py now")
            return 1
        report, problems = drill(target)
        print(f"Restore drill of {target.name}")
        for line in report:
            print("  " + line)
        for line in problems:
            print("  PROBLEM: " + line)
        print("PASS" if not problems else f"FAIL: {len(problems)} problems")
        return 0 if not problems else 1
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
