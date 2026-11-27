"""Where Duracash keeps its data, and how it writes it.

The data directory is set by DURACASH_DATA_DIR. It defaults to data/ beside
the app, which is outside static/ and never served. On Render, point it at a
mounted disk; without a disk Render's filesystem does not survive a deploy.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
except ImportError:  # not POSIX: the thread lock alone, as before
    fcntl = None

ROOT = Path(__file__).resolve().parent
# The product was called Billdable: a server still set up with the old BILLDABLE_* names keeps working.
for _name, _value in list(os.environ.items()):
    if _name.startswith("BILLDABLE_"):
        os.environ.setdefault("DURACASH_" + _name[len("BILLDABLE_"):], _value)

DATA_DIR = Path(os.environ.get("DURACASH_DATA_DIR") or ROOT / "data").resolve()


def set_aside(path: Path) -> Path | None:
    """Copy a file that can't be read to <name>.bad-<when it was last written>, for a person to
    look at. Once per version of the file. The file stays where it is. Never raises."""
    try:
        target = path.with_name(f"{path.name}.bad-{path.stat().st_mtime_ns}")
        if not target.exists():
            shutil.copy2(path, target)
        return target
    except OSError:
        return None


def write_json(path: Path, data: Any) -> None:
    """Write to a temp file, then rename over the target, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ---------- one writer at a time (security review M7) ----------

class DataDirBusy(RuntimeError):
    """Another program already holds this data directory."""


_CLAIMS: dict[str, Any] = {}


def claim_data_dir(role: str = "server") -> None:
    """Hold an exclusive lock on <data dir>/.duracash-<role>.lock for as long as this process
    runs, so a second server started on the same data directory is refused instead of writing
    beside the first. The lock goes with the process, however it ends. Raises DataDirBusy."""
    if fcntl is None or role in _CLAIMS:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f".duracash-{role}.lock"
    fh = open(path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        holder = fh.read().strip()[:80]
        fh.close()
        raise DataDirBusy(f"Another Duracash {role} is already using {DATA_DIR}"
                          + (f" ({holder})" if holder else "") + ". Stop it first, or point DURACASH_DATA_DIR elsewhere.")
    fh.seek(0)
    fh.truncate()
    fh.write(f"process {os.getpid()}\n")
    fh.flush()
    _CLAIMS[role] = fh


def release_data_dir(role: str = "server") -> None:
    fh = _CLAIMS.pop(role, None)
    if fh is not None:
        fh.close()


class ProcessLock:
    """A lock that holds across threads and across processes on this machine: a thread lock,
    then flock() on a small file beside the data. The server and admin.py take the same ones,
    so a command run from the Shell while the server is up waits its turn instead of saving
    over a change made at the same moment. `path` is a Path, or a function returning one (the
    tests move the data directory). reentrant=True lets the thread holding it take it again."""

    def __init__(self, path: "Path | Callable[[], Path]", reentrant: bool = False) -> None:
        self._path = path
        self._lock = threading.RLock() if reentrant else threading.Lock()
        self._depth = 0
        self._fh = None

    def _file(self) -> Path:
        return Path(self._path() if callable(self._path) else self._path)

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if not self._lock.acquire(blocking, timeout):
            return False
        if self._depth == 0 and fcntl is not None:
            try:
                path = self._file()
                path.parent.mkdir(parents=True, exist_ok=True)
                fh = open(path, "a+b")
            except OSError:
                fh = None  # a read-only disk: the thread lock alone
            if fh is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                except OSError:
                    fh.close()
                    fh = None
            self._fh = fh
        self._depth += 1
        return True

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0 and self._fh is not None:
            fh, self._fh = self._fh, None
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()
        self._lock.release()

    def _is_owned(self) -> bool:
        owned = getattr(self._lock, "_is_owned", None)
        return owned() if owned else self._lock.locked()

    def locked(self) -> bool:
        return self._depth > 0

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()

