"""Reading the text of scans and photos (OCR) on this server, in the background.

A scanned PDF or a photo has no text for contract_import's readers. With OCR_ENABLED=1 and two
programs installed, tesseract and pdftoppm (poppler-utils), such a file is queued when it is stored
and read here: one page at a time, one file at a time across the whole server. The text is kept
beside the upload (uploads/<company>/<file id>.ocr.json, under ocr_text) and the readers that
already read text use it when their own reading finds none (doc_check.with_stored_text through
server._read_stored, contract_import.read, the Inbox's kind guess). Nothing leaves the server: no
AI, no cloud call. Without the setting or the programs nothing here runs, and everything behaves
as it did before.

Each program runs without a shell, its arguments as a list, with a minimal environment, in its own
process group, with a time limit (the whole group is killed), a bound on what is read from it and,
where the platform enforces one (Linux; macOS refuses it), an address-space ceiling. Its input is
the app's own stored path (the file's id and extension), never a name a person gave. A page that
fails, times out or is too big is recorded as unread; nothing here raises into a request.

    python3 ocr.py status                      whether OCR is on, and what is waiting
    python3 ocr.py backfill --company <id>     queue a company's older scans and photos

Settings (README.md has the table): OCR_ENABLED, OCR_MAX_PAGES, OCR_PAGE_SECONDS, OCR_DPI,
OCR_MAX_PIXELS, OCR_LANG, OCR_MEMORY_MB, and OCR_TESSERACT / OCR_PDFTOPPM for the programs' paths.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    import fcntl
except ImportError:  # not on this platform: the queue is then guarded inside this process only
    fcntl = None  # type: ignore[assignment]

import alerts
import auth
import contract_import
import importer
import storage

QUEUE_NAME = "ocr-queue.json"
LOCK_NAME = ".ocr-queue.lock"
MAX_TEXT = contract_import.MAX_TEXT   # characters kept of a file's text, as a contract's
PAGE_BYTES = 1024 * 1024              # the most read from the reader for one page; more and it is stopped
NOTE_BYTES = 4096                     # the most read of what pdftoppm says
MAX_ATTEMPTS = 3                      # a file the server stopped on this many times is left unread
KEEP_DONE = 200                       # finished entries kept in a company's queue file
POLL_SECONDS = 30                     # how often the worker looks at the disk for files queued by the command
SHRINK_UP_TO = 8                      # a JPEG up to this many times OCR_MAX_PIXELS is shrunk; a bigger one is unread
PDF = ".pdf"
JPEGS = {".jpg", ".jpeg"}
AS_IS = {".png", ".webp"}             # read by tesseract itself, when the header says they are small enough
NOT_READ = {".heic": "HEIC photos aren't read.", ".gif": "GIF images aren't read."}
READS = {PDF} | JPEGS | AS_IS | set(NOT_READ)
STATES = {"waiting", "reading", "read", "unread"}
_COMPANY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_FILE = re.compile(r"file-[0-9a-f]{6,64}")
_LANG = re.compile(r"[A-Za-z_]{2,24}(?:\+[A-Za-z_]{2,24}){0,3}")

_FILES = threading.RLock()        # the queue files, inside this process
_ONE = threading.Lock()           # one file is read at a time, whoever asks
_WAKE = threading.Event()
_STOP = threading.Event()
_PENDING: set[str] = set()        # companies with a file waiting
_THREAD: threading.Thread | None = None
_NOW: tuple[str, str] | None = None   # the file being read
_AFTER: Callable[[str, str], None] | None = None


# ---------- settings ----------

def _number(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int((os.environ.get(name) or "").strip() or default)))
    except ValueError:
        return default


def settings() -> dict[str, Any]:
    lang = (os.environ.get("OCR_LANG") or "eng").strip()
    return {
        "max_pages": _number("OCR_MAX_PAGES", 10, 1, 500),
        "page_seconds": _number("OCR_PAGE_SECONDS", 60, 1, 900),
        "dpi": _number("OCR_DPI", 200, 72, 600),
        "max_pixels": _number("OCR_MAX_PIXELS", 8_000_000, 10_000, 100_000_000),
        # Each program's address-space ceiling. 0 sets none.
        "memory_mb": _number("OCR_MEMORY_MB", 400, 0, 65_536),
        "lang": lang if _LANG.fullmatch(lang) else "eng",
    }


def enabled() -> bool:
    return (os.environ.get("OCR_ENABLED") or "").strip() == "1"


def _program(setting: str, name: str) -> str:
    path = (os.environ.get(setting) or "").strip() or shutil.which(name) or ""
    path = os.path.abspath(path) if path else ""
    return path if path and os.path.isfile(path) and os.access(path, os.X_OK) else ""


def programs() -> tuple[str, str]:
    """Where tesseract and pdftoppm are, each "" when it isn't installed."""
    return _program("OCR_TESSERACT", "tesseract"), _program("OCR_PDFTOPPM", "pdftoppm")


def available() -> bool:
    return os.name == "posix" and all(programs())


def status() -> str:
    """off, not installed, or ready (for /healthz)."""
    return "off" if not enabled() else "ready" if available() else "not installed"


def _log(level: str, **fields: Any) -> None:
    record = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": level, **fields}
    sys.stderr.write(json.dumps(record, default=str) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- where things are kept ----------

def _folder(company_id: str) -> Path:
    if not _COMPANY.fullmatch(str(company_id)):
        raise ValueError("not a company id")
    return importer.UPLOADS / company_id


def _file_id(file_id: Any) -> str:
    if not _FILE.fullmatch(str(file_id)):
        raise ValueError("not a file id")
    return str(file_id)


def _stored(company_id: str, file_id: str, ext: str) -> Path:
    """The upload itself, as importer.file_path names it: the id and a known extension, nothing a person typed."""
    if ext not in READS:
        raise ValueError("not a type that is read")
    return _folder(company_id) / f"{_file_id(file_id)}{ext}"


def _sidecar(company_id: str, file_id: str) -> Path:
    return _folder(company_id) / f"{_file_id(file_id)}.ocr.json"


def _write(path: Path, doc: Any) -> bool:
    """storage.write_json without making the folder: a company deleted meanwhile gets nothing written back."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def text_for(company_id: str, file_id: Any) -> str:
    """The text read from one of this company's stored files, "" when there is none (not read yet,
    unread, never queued) or OCR is off. Never raises."""
    if not enabled():
        return ""
    try:
        doc = json.loads(_sidecar(company_id, str(file_id)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    text = doc.get("ocr_text") if isinstance(doc, dict) else ""
    return text[:MAX_TEXT] if isinstance(text, str) else ""


def text_for_bytes(company_id: str, files: list[dict[str, Any]], sha256: str) -> str:
    """The text read from a stored file of this company with these same bytes (a contract dropped
    on a form is not stored; the copy on a job's records is)."""
    for meta in files or []:
        if sha256 and meta.get("sha256") == sha256:
            text = text_for(company_id, meta.get("id"))
            if text:
                return text
    return ""


# ---------- the queue ----------

@contextmanager
def _locked(company_id: str, start: bool = False) -> Iterator[Path]:
    """A company's queue file, held by this process's lock and a lock file, so the server and the
    backfill command never write it at once. Only queueing a file (start) makes the lock file:
    nothing is made for a company whose folder, or whose queue, is gone."""
    folder = _folder(company_id)
    with _FILES:
        handle = None
        if fcntl is not None and folder.is_dir():
            try:
                handle = open(folder / LOCK_NAME, "a" if start else "r+")
                fcntl.flock(handle, fcntl.LOCK_EX)
            except OSError:
                handle = None
        try:
            yield folder / QUEUE_NAME
        finally:
            if handle is not None:
                handle.close()


def _valid(job: Any) -> bool:
    return (isinstance(job, dict) and isinstance(job.get("file_id"), str) and bool(_FILE.fullmatch(job["file_id"]))
            and job.get("ext") in READS and job.get("state") in STATES)


def _load(path: Path) -> list[dict[str, Any]]:
    """The queue's entries. A file that can't be understood is copied aside (storage.set_aside), an
    alert goes out and an empty queue takes its place: the copy, the uploads and every text already
    read stay, and `backfill` queues again what was waiting."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        jobs = json.loads(raw)["jobs"]
        if not isinstance(jobs, list) or not all(_valid(j) for j in jobs):
            raise ValueError("not a queue")
        return jobs
    except (ValueError, KeyError, TypeError):
        kept = storage.set_aside(path)
        company_id = path.parent.name
        _log("error", msg="OCR queue unreadable; set aside and started again", company=company_id, kept=kept.name if kept else "")
        alerts.notify(f"ocr-queue:{company_id}", f"the OCR queue of {company_id} couldn't be read; it was set aside"
                      + (f" as {kept.name}" if kept else "") + " and a new one started. Queue its files again with: python3 ocr.py backfill --company " + company_id)
        _write(path, {"version": 1, "jobs": []})
        return []


def _save(path: Path, jobs: list[dict[str, Any]]) -> bool:
    done = [j for j in jobs if j["state"] in ("read", "unread")]
    drop = {id(j) for j in done[:max(0, len(done) - KEEP_DONE)]}
    return path.parent.is_dir() and _write(path, {"version": 1, "jobs": [j for j in jobs if id(j) not in drop]})


def _has_text(path: Path) -> bool:
    """Today's reader gets text from this PDF (contract_import.pdf_text), so it needs no OCR."""
    try:
        text = contract_import.pdf_text(path.read_bytes())
    except Exception:
        return False
    return len(re.findall(r"[A-Za-z]", text[:MAX_TEXT])) >= contract_import.MIN_LETTERS


def enqueue(company_id: str, meta: dict[str, Any]) -> bool:
    """Queue a stored file to be read, when OCR is ready and the file is a scan or a photo: a PDF
    today's reader gets no text from, or an image. The demo company's files are never queued. A type
    that isn't read (HEIC, GIF) is recorded as unread straight away. Returns whether it was put in
    the queue. Never raises."""
    try:
        if status() != "ready" or company_id == auth.DEMO_COMPANY_ID:
            return False
        file_id, ext = str(meta.get("id") or ""), str(meta.get("ext") or "").lower()
        if ext not in READS or not _FILE.fullmatch(file_id):
            return False
        path = _stored(company_id, file_id, ext)
        if not path.is_file() or (ext == PDF and _has_text(path)):
            return False
        why = NOT_READ.get(ext, "")
        with _locked(company_id, start=True) as queue:
            jobs = _load(queue)
            if any(j["file_id"] == file_id for j in jobs):
                return False
            jobs.append({"file_id": file_id, "company_id": company_id, "ext": ext, "state": "unread" if why else "waiting", "reason": why,
                         "pages": 0, "pages_read": 0, "seconds": 0, "attempts": 0, "queued_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                         **({"finished_at": _now()} if why else {})})
            if not _save(queue, jobs):
                return False
        if not why:
            with _FILES:
                _PENDING.add(company_id)
            _WAKE.set()
        return True
    except Exception:
        _log("error", msg="OCR queueing failed", company=str(company_id)[:80], error=traceback.format_exc(limit=3)[-600:])
        return False


def queue(company_id: str) -> list[dict[str, Any]]:
    """A company's queue as it is on the disk: every entry, finished ones too."""
    with _locked(company_id) as path:
        return [dict(j) for j in _load(path)]


def _pending() -> list[str]:
    with _FILES:
        return sorted(_PENDING)


def queue_length() -> int:
    """Files waiting or being read, across companies. 0 when OCR isn't on."""
    if not enabled():
        return 0
    total = 0
    for company_id in _pending():
        try:
            total += sum(1 for j in queue(company_id) if j["state"] in ("waiting", "reading"))
        except (OSError, ValueError):
            continue
    return total


def _rescan() -> None:
    """Find the queues on the disk with work in them: at start (a restart resumes what was waiting,
    and what was being read when the server stopped waits again), and now and then for files the
    backfill command queued."""
    if not importer.UPLOADS.is_dir():
        return
    for path in importer.UPLOADS.glob(f"*/{QUEUE_NAME}"):
        company_id = path.parent.name
        if not _COMPANY.fullmatch(company_id):
            continue
        with _locked(company_id) as queue_path:
            jobs = _load(queue_path)
            stopped = [j for j in jobs if j["state"] == "reading" and (company_id, j["file_id"]) != _NOW]
            for job in stopped:
                job["state"] = "waiting"
            if stopped:
                _save(queue_path, jobs)
            if any(j["state"] == "waiting" for j in jobs):
                _PENDING.add(company_id)   # under _FILES: _locked holds it


def _take() -> dict[str, Any] | None:
    """The file that has waited longest, marked as being read. One the server already stopped on
    MAX_ATTEMPTS times is left unread instead, so a file that brings the server down can't do it for ever."""
    global _NOW
    while True:
        best: tuple[str, str] | None = None
        for company_id in _pending():
            with _locked(company_id) as path:
                waiting = [j for j in _load(path) if j["state"] == "waiting"]
                if not waiting:
                    _PENDING.discard(company_id)
                    continue
            oldest = min(str(j.get("queued_at") or "") for j in waiting)
            if best is None or oldest < best[0]:
                best = (oldest, company_id)
        if best is None:
            return None
        company_id = best[1]
        with _locked(company_id) as path:
            jobs = _load(path)
            job = min((j for j in jobs if j["state"] == "waiting"), key=lambda j: str(j.get("queued_at") or ""), default=None)
            if job is None:
                continue
            job["attempts"] = int(job.get("attempts") or 0) + 1
            if job["attempts"] > MAX_ATTEMPTS:
                job.update(state="unread", reason=f"The server stopped {MAX_ATTEMPTS} times while reading it.", finished_at=_now())
                _save(path, jobs)
                continue
            job.update(state="reading", started_at=_now())
            if not _save(path, jobs):
                _PENDING.discard(company_id)
                continue
            _NOW = (company_id, job["file_id"])   # set under the queue's lock: _rescan leaves it alone
            return dict(job)


def _finish(company_id: str, file_id: str, result: dict[str, Any], seconds: float) -> None:
    with _locked(company_id) as path:
        if not path.is_file():
            return   # the company was deleted meanwhile: no queue is started for it
        jobs = _load(path)
        for job in jobs:
            if job["file_id"] == file_id:
                job.update(state=result["state"], reason=result["reason"], pages=result["pages"], pages_read=result["pages_read"],
                           seconds=seconds, finished_at=_now())
        _save(path, jobs)


# ---------- running the programs ----------

# Run with python -I -S -c: sets the limits, then becomes the program. argv: the address-space
# ceiling in bytes (0: none), CPU seconds, the biggest file it may write, then the program and its
# arguments. A limit this platform refuses is skipped (macOS has no address-space limit to set).
_LIMITED = """
import os, sys
try:
    import resource
    for name, value in (("RLIMIT_AS", int(sys.argv[1])), ("RLIMIT_CPU", int(sys.argv[2])), ("RLIMIT_FSIZE", int(sys.argv[3])), ("RLIMIT_CORE", 0)):
        try:
            if value or name == "RLIMIT_CORE":
                resource.setrlimit(getattr(resource, name), (value, value))
        except (ValueError, OSError, AttributeError):
            pass
except ImportError:
    pass
os.environ.pop("LD_LIBRARY_PATH", None)
os.execv(sys.argv[4], sys.argv[4:])
"""


def _env(tmp: Path) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": str(tmp), "TMPDIR": str(tmp), "OMP_THREAD_LIMIT": "1"}
    if os.environ.get("TESSDATA_PREFIX"):
        env["TESSDATA_PREFIX"] = os.environ["TESSDATA_PREFIX"]
    return env


def _run(args: list[str], cfg: dict[str, Any], tmp: Path, limit: int, notes: bool = False) -> tuple[int | None, bytes, str]:
    """Run one program to its end: (exit code, what it printed, why it was stopped or ""). At most
    `limit` bytes are kept. A program that prints more is killed, and so is one still going after
    OCR_PAGE_SECONDS: its whole process group. notes: what is read is only what the program says
    about its work (pdftoppm, whose page goes to a file; its warnings can be many), stderr
    included: the first `limit` bytes are kept and the rest dropped, without stopping it."""
    seconds = cfg["page_seconds"]
    launcher = [sys.executable, "-I", "-S", "-c", _LIMITED, str(cfg["memory_mb"] * 1024 * 1024), str(seconds + 5),
                str(cfg["max_pixels"] * 4 + 1024 * 1024)]
    env = _env(tmp)
    if os.environ.get("LD_LIBRARY_PATH"):   # for this Python to start; taken out again before the program runs
        env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH"]
    proc = subprocess.Popen(launcher + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT if notes else subprocess.DEVNULL,
                            env=env, cwd=str(tmp), start_new_session=True, close_fds=True)
    out, why = bytearray(), ""
    deadline = time.monotonic() + seconds
    fd = proc.stdout.fileno()
    waiter = selectors.DefaultSelector()
    waiter.register(fd, selectors.EVENT_READ)
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                why = "timeout"
                break
            if not waiter.select(min(left, 1.0)):
                continue
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            if len(out) <= limit:
                out += chunk
            if len(out) > limit and not notes:
                why = "flood"
                break
        if not why:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                why = "timeout"
    finally:
        waiter.close()
        if proc.poll() is None:   # still going (so its process id is still its own): it and anything it started
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
        proc.stdout.close()
        proc.wait()
    return proc.returncode, bytes(out[:limit]), why


def _why(code: int | None, stopped: str, cfg: dict[str, Any]) -> str:
    if stopped == "timeout":
        return f"took longer than {cfg['page_seconds']} seconds"
    if stopped == "flood":
        return "printed more than a page of text can be"
    if code is not None and code < 0:
        return "was stopped (out of memory or time)"
    return "couldn't be read"


def _tesseract(image: Path, cfg: dict[str, Any], tmp: Path, dpi: int = 0) -> tuple[str | None, str]:
    """One image's text, or None and why not."""
    args = [programs()[0], str(image), "stdout", "-l", cfg["lang"]] + (["--dpi", str(dpi)] if dpi else [])
    code, out, stopped = _run(args, cfg, tmp, PAGE_BYTES)
    if stopped or code != 0:
        return None, _why(code, stopped, cfg)
    return out.decode("utf-8", "replace").replace("\x0c", "\n").strip(), ""


def _pgm_size(path: Path) -> tuple[int, int] | None:
    """Width and height from a PGM's header (P5, then the numbers)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return None
    m = re.match(rb"P5\s+(\d{1,7})\s+(\d{1,7})\s+\d{1,5}\s", head)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _render(pdf: Path, page: int, size: list[str], cfg: dict[str, Any], tmp: Path) -> tuple[Path | None, str, bool]:
    """One PDF page as a gray image in tmp: (the image, why not, whether the page is past the end).
    A PGM, not a PNG: compressing a noisy scan's PNG took a hundred times longer than the page."""
    image = tmp / "page.pgm"
    try:
        image.unlink()
    except OSError:
        pass
    args = [programs()[1], "-f", str(page), "-l", str(page), "-gray", "-singlefile", *size, str(pdf), str(tmp / "page")]
    code, out, stopped = _run(args, cfg, tmp, NOTE_BYTES, notes=True)
    if stopped or code != 0 or not image.is_file():
        return None, _why(code, stopped, cfg), not stopped and b"Wrong page range" in out
    made = _pgm_size(image)
    if not made or made[0] * made[1] > cfg["max_pixels"] * 1.02:   # a scaled side is rounded up a pixel
        return None, "is too large", False
    return image, "", False


def _read_pdf(path: Path, cfg: dict[str, Any], tmp: Path) -> dict[str, Any]:
    """Up to OCR_MAX_PAGES pages, each rendered alone at OCR_DPI and read, its image deleted before
    the next. A page is never rendered past OCR_MAX_PIXELS: the render is cut to a square that
    holds that many, and a page that reached the cut is rendered again scaled to fit it whole."""
    side = int(cfg["max_pixels"] ** 0.5)
    texts: list[str] = []
    unread: list[str] = []
    pages = kept = misses = 0
    more = False
    for n in range(1, cfg["max_pages"] + 2):
        if n > cfg["max_pages"]:
            # Is there a page past the last one read? Asked for a small corner of it, so it costs next to nothing.
            image, why, past_end = _render(path, n, ["-r", "20", "-x", "0", "-y", "0", "-W", "64", "-H", "64"], cfg, tmp)
            more = image is not None and not misses
            break
        image, why, past_end = _render(path, n, ["-r", str(cfg["dpi"]), "-x", "0", "-y", "0", "-W", str(side), "-H", str(side)], cfg, tmp)
        if past_end:
            break
        dpi = cfg["dpi"]
        if image is not None and max(_pgm_size(image) or (0, 0)) >= side:
            image, why, _ = _render(path, n, ["-scale-to", str(side)], cfg, tmp)
            dpi = 0
        if image is None:
            misses += 1
            unread.append(f"page {n} {why}")
            if misses >= 2 and not why.startswith("took longer"):
                # Two pages in a row pdftoppm gives nothing for: the end of what it can open.
                unread = unread[:-2] if pages else unread[:1]
                break
            continue
        misses = 0
        pages = n
        text, why = _tesseract(image, cfg, tmp, dpi)
        if text is None:
            unread.append(f"page {n} {why}")
            continue
        texts.append(text)
        kept += len(text) + 2
        if kept >= MAX_TEXT:
            break
    if more:
        unread.append(f"only the first {cfg['max_pages']} pages are read")
    return _result(texts, unread, pages)


def _result(texts: list[str], unread: list[str], pages: int) -> dict[str, Any]:
    """What a file came to: read when any page was, with what wasn't as the reason; else unread."""
    reason = "; ".join(unread)[:500]
    reason = reason[:1].upper() + reason[1:] + "." if reason else ""
    if not texts:
        return {"state": "unread", "reason": reason or "Couldn't be opened.", "pages": pages, "pages_read": 0, "text": ""}
    return {"state": "read", "reason": reason, "pages": pages, "pages_read": len(texts), "text": "\n\n".join(t for t in texts if t)[:MAX_TEXT]}


# ---------- images ----------

def _png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR" or len(data) < 24:
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _webp_size(data: bytes) -> tuple[int, int] | None:
    """A still WebP's size from its first chunk; None for an animation or anything else."""
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP" or len(data) < 30:
        return None
    kind = data[12:16]
    if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    if kind == b"VP8L" and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X" and not data[20] & 0x02:
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    return None


def _exif_rotation(tiff: bytes) -> int:
    """How far a photo is turned (the Exif orientation), in degrees clockwise to show it upright."""
    try:
        order = {b"II": "little", b"MM": "big"}[tiff[:2]]
        at = int.from_bytes(tiff[4:8], order)
        for k in range(min(int.from_bytes(tiff[at:at + 2], order), 64)):
            entry = tiff[at + 2 + 12 * k:at + 14 + 12 * k]
            if len(entry) == 12 and int.from_bytes(entry[:2], order) == 0x0112:
                return {3: 180, 6: 90, 8: 270}.get(int.from_bytes(entry[8:10], order), 0)
    except (KeyError, IndexError):
        pass
    return 0


_FRAME_HEADERS = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _jpeg_info(data: bytes) -> tuple[int, int, int, int] | None:
    """(width, height, colour components, rotation) from a JPEG's headers, without decoding it."""
    if data[:2] != b"\xff\xd8":
        return None
    i, rotation, end = 2, 0, len(data)
    while i + 4 <= end:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        size = int.from_bytes(data[i + 2:i + 4], "big")
        if size < 2 or marker in (0xD9, 0xDA):
            return None
        body = data[i + 4:i + 2 + size]
        if marker == 0xE1 and body[:6] == b"Exif\x00\x00":
            rotation = _exif_rotation(body[6:])
        elif marker in _FRAME_HEADERS:
            if len(body) < 6:
                return None
            height, width = int.from_bytes(body[1:3], "big"), int.from_bytes(body[3:5], "big")
            return (width, height, body[5], rotation) if width and height else None
        i += 2 + size
    return None


def _jpeg_as_pdf(data: bytes, width: int, height: int, components: int, rotation: int) -> bytes:
    """A one-page PDF that is the photo, so pdftoppm decodes it and scales it down as it goes
    (a PDF holds a JPEG as it is)."""
    space = "/DeviceGray" if components == 1 else "/DeviceRGB"
    draw = b"q %d 0 0 %d 0 0 cm /Im Do Q" % (width, height)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Rotate %d /Resources << /XObject << /Im 4 0 R >> >> /Contents 5 0 R >>" % (width, height, rotation),
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n"
        % (width, height, space.encode(), len(data)) + data + b"\nendstream",
        b"<< /Length %d >>\nstream\n" % len(draw) + draw + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _megapixels(pixels: int) -> str:
    return f"{pixels / 1_000_000:.1f}".rstrip("0").rstrip(".")


def _read_image(path: Path, ext: str, cfg: dict[str, Any], tmp: Path) -> dict[str, Any]:
    """A photo. Its size comes from the file's header before anything decodes it. A JPEG goes
    through pdftoppm (as a one-page PDF), which turns it upright and shrinks it to OCR_MAX_PIXELS
    while decoding, so tesseract never holds the full photo; one more than SHRINK_UP_TO times that
    is left unread. A PNG or a still WebP within OCR_MAX_PIXELS is read as it is, a bigger one is
    left unread."""
    cap = cfg["max_pixels"]
    data = path.read_bytes()
    if ext in JPEGS:
        info = _jpeg_info(data)
        if not info or info[2] not in (1, 3):
            return _result([], ["not a photo this server reads"], 0)
        width, height, components, rotation = info
        if width * height > cap * SHRINK_UP_TO:
            return _result([], [f"the photo is {_megapixels(width * height)} megapixels, more than is read"], 0)
        pdf = tmp / "photo.pdf"
        pdf.write_bytes(_jpeg_as_pdf(data, width, height, components, rotation))
        del data
        long_side = int(max(width, height) * min(1.0, (cap / (width * height)) ** 0.5))
        image, why, _ = _render(pdf, 1, ["-r", "72", "-scale-to", str(max(1, long_side))], cfg, tmp)
        pdf.unlink()
        if image is None:
            return _result([], [f"the photo {why}"], 0)
        text, why = _tesseract(image, cfg, tmp)
    else:
        size = _png_size(data) if ext == ".png" else _webp_size(data)
        del data
        if not size or not size[0] or not size[1]:
            return _result([], ["not a picture this server reads"], 0)
        if size[0] * size[1] > cap:
            return _result([], [f"the picture is {_megapixels(size[0] * size[1])} megapixels, more than is read"], 0)
        text, why = _tesseract(path, cfg, tmp)
    return _result([] if text is None else [text], [f"the picture {why}"] if text is None else [], 1)


# ---------- the worker ----------

def after_read(hook: Callable[[str, str], None] | None) -> None:
    """What to call, with the company and file ids, once a file's text is stored (the server has
    the Inbox's suggestion and the waiver question's first answer worked out again with it)."""
    global _AFTER
    _AFTER = hook


def _left_behind(sidecar: Path) -> None:
    """The upload went while it was being read. Its text goes too, and when the whole company was
    deleted (its queue is gone as well), so does the folder the write brought back."""
    folder = sidecar.parent
    try:
        sidecar.unlink()
    except OSError:
        pass
    if not (folder / QUEUE_NAME).exists():
        for path in (folder / LOCK_NAME,):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            folder.rmdir()   # only when nothing else is in it
        except OSError:
            pass


def _read(job: dict[str, Any]) -> None:
    """Read one queued file and keep what came of it: the text beside the upload, then whoever
    listens (after_read), then the queue's entry. Never raises."""
    global _NOW
    company_id, file_id, ext = str(job.get("company_id") or ""), job["file_id"], job["ext"]
    started = time.monotonic()
    result = {"state": "unread", "reason": "Couldn't be read.", "pages": 0, "pages_read": 0, "text": ""}
    try:
        cfg = settings()
        path = _stored(company_id, file_id, ext)
        if path.is_file():
            with tempfile.TemporaryDirectory(prefix="duracash-ocr-") as tmp:
                result = _read_pdf(path, cfg, Path(tmp)) if ext == PDF else _read_image(path, ext, cfg, Path(tmp))
        seconds = round(time.monotonic() - started, 2)
        sidecar = _sidecar(company_id, file_id)
        kept = path.is_file() and _write(sidecar, {
            "file_id": file_id, "state": result["state"], "reason": result["reason"], "pages": result["pages"], "pages_read": result["pages_read"],
            "seconds": seconds, "read_at": _now(), "lang": cfg["lang"], "ocr_text": result["text"]})
        if not path.is_file():
            _left_behind(sidecar)
            result = {**result, "state": "unread", "reason": "The file is gone.", "text": ""}
        elif not kept:
            result = {**result, "state": "unread", "reason": "The text couldn't be saved.", "text": ""}
        elif result["text"] and _AFTER is not None:
            try:
                _AFTER(company_id, file_id)
            except Exception:
                _log("error", msg="OCR text stored, but the records weren't brought up to date", company=company_id, file=file_id,
                     error=traceback.format_exc(limit=4)[-800:])
        _finish(company_id, file_id, result, seconds)
        _log("info", msg="OCR", company=company_id, file=file_id, state=result["state"], pages=result["pages"], pages_read=result["pages_read"],
             chars=len(result["text"]), seconds=seconds)
    except Exception:
        _log("error", msg="OCR failed", company=company_id[:80], file=str(file_id)[:80], error=traceback.format_exc(limit=4)[-800:])
        try:
            _finish(company_id, file_id, result, round(time.monotonic() - started, 2))
        except Exception:
            pass
    finally:
        _NOW = None


def run_waiting() -> int:
    """Read every file that is waiting, the one that waited longest first, one at a time, in the
    calling thread; returns how many. The worker's loop, and what the tests call."""
    done = 0
    while status() == "ready":
        with _ONE:
            job = _take()
            if job is None:
                break
            _read(job)
        done += 1
    return done


def _loop() -> None:
    while not _STOP.is_set():
        try:
            _rescan()
            run_waiting()
        except Exception:
            _log("error", msg="OCR worker", error=traceback.format_exc(limit=4)[-800:])
        _WAKE.wait(POLL_SECONDS)
        _WAKE.clear()


def start() -> threading.Thread | None:
    """Start the one worker thread, when OCR is ready; it picks up what the disk says is waiting."""
    global _THREAD
    if status() != "ready":
        return None
    if _THREAD is None or not _THREAD.is_alive():
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, name="ocr", daemon=True)
        _THREAD.start()
    return _THREAD


def stop() -> None:
    """End the worker thread once the file in hand is done (the tests; the server's goes with the process)."""
    global _THREAD
    thread, _THREAD = _THREAD, None
    _STOP.set()
    _WAKE.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=30)


# ---------- the command ----------

def main(argv: list[str]) -> int:
    import server   # here, not at the top: the server imports this module

    if argv[1:2] == ["status"]:
        print(f"OCR: {status()}")
        _rescan()
        for company_id in sorted(_PENDING):
            jobs = queue(company_id)
            print(f"{company_id}: {sum(1 for j in jobs if j['state'] in ('waiting', 'reading'))} waiting")
        return 0
    if argv[1:2] == ["backfill"] and argv[2:3] == ["--company"] and len(argv) == 4:
        company_id = argv[3]
        if status() != "ready":
            print(f"OCR is {status()}. Set OCR_ENABLED=1 and install tesseract and pdftoppm first.")
            return 1
        if company_id == auth.DEMO_COMPANY_ID or not _COMPANY.fullmatch(company_id) or not server.company_exists(company_id):
            print("No company with that id to read files for (the demo's files are never read). Run: python3 admin.py companies")
            return 1
        with server.company_lock(company_id):
            files = server.load_company(company_id).get("files", [])
        queued = sum(1 for meta in files if enqueue(company_id, meta))
        print(f"{queued} of {len(files)} files queued. The server reads them in the background, starting within {POLL_SECONDS} seconds.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    import ocr   # the module the server imports, not a second copy of it under __main__
    sys.exit(ocr.main(sys.argv))
