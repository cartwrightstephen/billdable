"""Where each company's data lives (P2-5).

server.load_company() and save_company() go through here. Two layouts:

- sqlite (the default): one database file, companies/duracash.db, in WAL mode. A company is
  a head row plus one row per job, audit row, email, inbox item, daily record and file, so a
  save writes only the rows that changed, and readers never wait for a writer.
- json (DURACASH_STORAGE=json): the original one file per company, rewritten on every save.

On its first start the SQLite store imports any company JSON files it finds and moves them to
companies/imported-json-<time>/ (never deletes them; deleting a company removes its copy). Export stays JSON (/api/export).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import storage

DB_NAME = "duracash.db"
OLD_DB_NAME = "billdable.db"  # its name before the product was renamed Duracash
# Lists that grow with use; each entry is its own row. Everything else is in the head row.
ROW_KINDS = ("jobs", "audit", "email_log", "inbox", "daily_records", "files")

# Schema migrations, in order. PRAGMA user_version records how many have run. Append only.
MIGRATIONS = [
    """CREATE TABLE companies (
        id TEXT PRIMARY KEY,
        head TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE company_rows (
        company_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        pos INTEGER NOT NULL,
        row_id TEXT NOT NULL DEFAULT '',
        doc TEXT NOT NULL,
        PRIMARY KEY (company_id, kind, pos)
    ) WITHOUT ROWID""",
    "CREATE INDEX company_rows_by_id ON company_rows (company_id, kind, row_id)",
]


def backend() -> str:
    return "json" if (os.environ.get("DURACASH_STORAGE") or "").strip().lower() == "json" else "sqlite"


def _dump(doc: Any) -> str:
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False)


def _digest(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JsonStore:
    """One file per company: companies/<id>.json."""

    kind = "json"

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.last_rows_written = 0

    def path(self, company_id: str) -> Path:
        return self.folder / f"{company_id}.json"

    def ids(self) -> list[str]:
        return sorted(p.stem for p in self.folder.glob("*.json")) if self.folder.exists() else []

    def exists(self, company_id: str) -> bool:
        return self.path(company_id).exists()

    def load(self, company_id: str) -> dict | None:
        path = self.path(company_id)
        return json.loads(path.read_text()) if path.exists() else None

    def save(self, company_id: str, state: dict) -> None:
        storage.write_json(self.path(company_id), state)
        self.last_rows_written = 1

    def delete(self, company_id: str) -> None:
        try:
            self.path(company_id).unlink()
        except OSError:
            pass

    def backup_members(self) -> list[tuple[str, bytes]]:
        return []  # the company files are picked up with the rest of companies/


def db_file(folder: Path) -> Path:
    """The database in folder: duracash.db, or billdable.db when only that one exists (made before the rename)."""
    new, old = Path(folder) / DB_NAME, Path(folder) / OLD_DB_NAME
    return old if old.exists() and not new.exists() else new


class SqliteStore:
    """Companies in one SQLite database; see the module docstring."""

    kind = "sqlite"

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.db_path = db_file(self.folder)
        self._local = threading.local()
        # company id -> (version, head digest, {kind: [row digests]}) as last read or written here
        self._seen: dict[str, tuple] = {}
        self._seen_lock = threading.Lock()
        self.last_rows_written = 0
        self._ready = False
        self._ready_lock = threading.Lock()

    # ----- connection and schema -----

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _conn(self) -> sqlite3.Connection:
        if not self._ready:
            # First use in this process: WAL mode, schema and the JSON import, once, before any
            # other thread connects (switching the journal mode needs the database to itself).
            with self._ready_lock:
                if not self._ready:
                    self.folder.mkdir(parents=True, exist_ok=True)
                    conn = self._connect()
                    conn.execute("PRAGMA journal_mode=WAL")
                    self._migrate(conn)
                    self._import_json(conn)
                    self._local.conn = conn
                    self._ready = True
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = self._connect()
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            done = conn.execute("PRAGMA user_version").fetchone()[0]
            for n, sql in enumerate(MIGRATIONS[done:], start=done + 1):
                conn.execute(sql)
                conn.execute(f"PRAGMA user_version={n}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _import_json(self, conn: sqlite3.Connection) -> None:
        """Bring company JSON files (the old layout) into the database once, then move them aside."""
        files = sorted(self.folder.glob("*.json"))
        if not files:
            return
        moved = self.folder / f"imported-json-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        conn.execute("BEGIN IMMEDIATE")
        try:
            for path in files:
                if not conn.execute("SELECT 1 FROM companies WHERE id = ?", (path.stem,)).fetchone():
                    self._write_all(conn, path.stem, json.loads(path.read_text()), 0)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        moved.mkdir(parents=True, exist_ok=True)
        for path in files:
            os.replace(path, moved / path.name)

    # ----- reading -----

    def ids(self) -> list[str]:
        return [r[0] for r in self._conn().execute("SELECT id FROM companies ORDER BY id")]

    def exists(self, company_id: str) -> bool:
        return self._conn().execute("SELECT 1 FROM companies WHERE id = ?", (company_id,)).fetchone() is not None

    def load(self, company_id: str) -> dict | None:
        conn = self._conn()
        conn.execute("BEGIN")  # one consistent read
        try:
            head = conn.execute("SELECT head, version FROM companies WHERE id = ?", (company_id,)).fetchone()
            if not head:
                return None
            rows = conn.execute("SELECT kind, doc FROM company_rows WHERE company_id = ? ORDER BY kind, pos", (company_id,)).fetchall()
        finally:
            conn.execute("COMMIT")
        state = json.loads(head[0])
        lists: dict[str, list] = {}
        digests: dict[str, list] = {}
        for kind, doc in rows:
            lists.setdefault(kind, []).append(json.loads(doc))
            digests.setdefault(kind, []).append(_digest(doc))
        for kind in ROW_KINDS:
            if kind in lists or kind in state.get("_row_present", []):
                state[kind] = lists.get(kind, [])
        state.pop("_row_present", None)
        with self._seen_lock:
            self._seen[company_id] = (head[1], _digest(head[0]), digests)
        return state

    # ----- writing -----

    def _split(self, state: dict) -> tuple[str, dict[str, list[str]]]:
        head = {k: v for k, v in state.items() if k not in ROW_KINDS or not isinstance(v, list)}
        present = [k for k in ROW_KINDS if isinstance(state.get(k), list)]
        head["_row_present"] = present
        rows = {k: [_dump(item) for item in state[k]] for k in present}
        return _dump(head), rows

    def _write_all(self, conn: sqlite3.Connection, company_id: str, state: dict, version: int) -> int:
        head, rows = self._split(state)
        conn.execute("INSERT OR REPLACE INTO companies (id, head, version, updated_at) VALUES (?, ?, ?, ?)",
                     (company_id, head, version + 1, _now()))
        conn.execute("DELETE FROM company_rows WHERE company_id = ?", (company_id,))
        conn.executemany(
            "INSERT INTO company_rows (company_id, kind, pos, row_id, doc) VALUES (?, ?, ?, ?, ?)",
            [(company_id, kind, pos, _row_id(doc), doc) for kind, docs in rows.items() for pos, doc in enumerate(docs)],
        )
        return 1 + sum(len(d) for d in rows.values())

    def save(self, company_id: str, state: dict) -> None:
        conn = self._conn()
        head, rows = self._split(state)
        conn.execute("BEGIN IMMEDIATE")
        try:
            found = conn.execute("SELECT version FROM companies WHERE id = ?", (company_id,)).fetchone()
            with self._seen_lock:
                seen = self._seen.get(company_id)
            if not found or not seen or seen[0] != found[0]:
                # New company, or one this process hasn't read at its current version: write it all.
                written = self._write_all(conn, company_id, state, found[0] if found else 0)
                version = (found[0] if found else 0) + 1
            else:
                version = found[0] + 1
                written = 0
                head_digest = _digest(head)
                if head_digest != seen[1]:
                    written += 1
                conn.execute("UPDATE companies SET head = ?, version = ?, updated_at = ? WHERE id = ?",
                             (head, version, _now(), company_id))
                for kind in set(rows) | set(seen[2]):
                    new, old = rows.get(kind, []), seen[2].get(kind, [])
                    changed = [(company_id, kind, pos, _row_id(doc), doc) for pos, doc in enumerate(new)
                               if pos >= len(old) or old[pos] != _digest(doc)]
                    if changed:
                        conn.executemany("INSERT OR REPLACE INTO company_rows (company_id, kind, pos, row_id, doc) VALUES (?, ?, ?, ?, ?)", changed)
                        written += len(changed)
                    if len(old) > len(new):
                        conn.execute("DELETE FROM company_rows WHERE company_id = ? AND kind = ? AND pos >= ?", (company_id, kind, len(new)))
                        written += len(old) - len(new)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            with self._seen_lock:
                self._seen.pop(company_id, None)
            raise
        with self._seen_lock:
            self._seen[company_id] = (version, _digest(head), {k: [_digest(d) for d in docs] for k, docs in rows.items()})
        self.last_rows_written = written

    def delete(self, company_id: str) -> None:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM company_rows WHERE company_id = ?", (company_id,))
        conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
        conn.execute("COMMIT")
        with self._seen_lock:
            self._seen.pop(company_id, None)

    def backup_members(self) -> list[tuple[str, bytes]]:
        """A consistent copy of the database (SQLite's backup API), for backup.make_backup."""
        if not self.db_path.exists():
            return []
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / self.db_path.name
            source, dest = sqlite3.connect(self.db_path, timeout=10), sqlite3.connect(copy)
            try:
                source.backup(dest)
            finally:
                source.close()
                dest.close()
            return [(f"{self.folder.name}/{self.db_path.name}", copy.read_bytes())]


def _row_id(doc: str) -> str:
    try:
        value = json.loads(doc)
    except ValueError:
        return ""
    return str(value.get("id", "")) if isinstance(value, dict) else ""


_STORES: dict[tuple[str, Path], Any] = {}
_STORES_LOCK = threading.Lock()


def open_store(folder: Path) -> JsonStore | SqliteStore:
    """The store for a companies folder (one per folder and layout, shared by all threads)."""
    key = (backend(), Path(folder).resolve())
    with _STORES_LOCK:
        if key not in _STORES:
            _STORES[key] = JsonStore(folder) if key[0] == "json" else SqliteStore(folder)
        return _STORES[key]
