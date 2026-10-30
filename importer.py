"""Bring outside paperwork into the job record.

Sources: uploaded files (photos, PDFs, spreadsheets, saved emails), pasted
email or text-message threads, pasted notes (split into one daily record per
date), and inbound email or SMS forwarded by a provider to a per-company
webhook. Everything lands in the company's Inbox with a suggested job, kind,
amounts and dates; one click files it to the job record. Nothing here uses AI:
matching and extraction are plain rules, and nothing is filed without a person.
"""

from __future__ import annotations

import base64
import email
import email.policy
import email.utils
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import domain
import storage

UPLOADS = storage.DATA_DIR / "uploads"
MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
MAX_NEW_INBOX = 1000          # an Inbox this full refuses more until items are filed
TOKENS_LOCK = threading.Lock()
# Uppercase words that look like job codes but aren't: months, days, common words.
NOT_CODES = {
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "SEPT", "OCT", "NOV", "DEC",
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN", "THE", "AND", "FOR", "INC", "LLC", "USA", "NEW",
    "OLD", "ASAP", "FYI", "PDF", "JPG", "PNG", "TOTAL", "DUE", "PAID", "CO", "PO", "GC", "AP", "SOV",
    # What phones and scanners put in file names.
    "IMG", "SCAN", "DOC", "JPEG", "HEIC", "EML", "CSV", "TXT", "XLS", "XLSX", "DOCX", "PHOTO", "FILE",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
UNIT_WORDS = {"inch", "inches", "in", "ft", "foot", "feet", "yd", "yard", "lb", "lbs", "bath", "hp", "ton", "pipe", "sheet", "sheets", "mile"}

# Extension -> (content type served back, shown inline in the browser?)
# HTML, SVG and scripts are refused: they could run code when opened.
ALLOWED_FILES = {
    ".pdf": ("application/pdf", True),
    ".jpg": ("image/jpeg", True),
    ".jpeg": ("image/jpeg", True),
    ".png": ("image/png", True),
    ".gif": ("image/gif", True),
    ".webp": ("image/webp", True),
    ".heic": ("image/heic", False),
    ".txt": ("text/plain; charset=utf-8", False),
    ".csv": ("text/csv; charset=utf-8", False),
    ".eml": ("message/rfc822", False),
    ".doc": ("application/msword", False),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", False),
    ".xls": ("application/vnd.ms-excel", False),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False),
}

KINDS = {
    "change_order": "Change order",
    "waiver": "Lien waiver",
    "pay_app": "Backup for the pay app",
    "ticket": "Daily ticket or time sheet",
    "supplier_invoice": "Supplier invoice or receipt",
    "insurance": "Insurance certificate",
    "inspection": "Inspection",
    "stored": "Stored materials document",
    "punch": "Punch list sign-off",
    "completion": "Completion certificate",
    "asbuilt": "As-builts",
    "warranty": "Warranty",
    "photo": "Photo",
    "contract": "Contract",
    "daily": "Daily record",
    "note": "Note",
    "other": "Other",
}

_KIND_RULES = [
    ("change_order", r"\bchange order\b|\bc\.?o\.?\s*#?\s*\d+|\bextra work\b|\bfield (?:order|directive)\b"),
    ("waiver", r"\bwaiver\b|\blien release\b"),
    ("pay_app", r"\bpay app\b|\bpay application\b|\bapplication for payment\b|\bg70[23]\b|\brequisition\b|\bpencil\b"),
    ("insurance", r"\bcertificate of (?:liability )?insurance\b|\binsurance certificate\b|\bcoi\b"),
    ("punch", r"\bpunch ?list\b"),
    ("completion", r"\bcertificate of (?:substantial )?completion\b|\bcompletion certificate\b"),
    ("asbuilt", r"\bas[- ]?builts?\b"),
    ("warranty", r"\bwarrant(?:y|ies)\b"),
    ("stored", r"\bstored materials?\b"),
    ("inspection", r"\binspection (?:report|passed|failed|result)s?\b"),
    # An invoice is a supplier's bill unless it names one of our pay apps (R5-07).
    ("invoice", r"\binvoice\b|\breceipt\b"),
    ("ticket", r"\bdelivery ticket\b|\bticket\b|\bpacking slip\b|\btime sheet\b|\btimesheet\b"),
    ("contract", r"\bcontract\b|\bagreement\b|\bsubcontract\b|\bpurchase order\b"),
    ("daily", r"\bdaily (?:log|report|record)\b|\bcrew\b|\bon site today\b"),
]

# $1,250.00, $40; without the $ only with a thousands comma or cents (1,250 or 412.50), so
# "4 crew" and "10/12" are not money (R5-05).
_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?"
                       r"|(?<![\d.,/$-])(?:(\d{1,3}(?:,\d{3})+)(?:\.(\d{2}))?|(\d+)\.(\d{2}))(?![\d,]|\.\d)")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE_PATTERNS = [
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),  # 2026-10-12
    re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b"),  # 10/12 or 10/12/2026
    re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.I),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------- files ----------

def safe_name(name: str) -> str:
    name = (name or "file").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .") or "file"
    return name[:120]


def save_file(state: dict[str, Any], company_id: str, filename: str, data: bytes, actor: str) -> dict[str, Any]:
    """Store a file and register it on the company's state (for callers already holding the data)."""
    return register_file(state, store_bytes(company_id, filename, data, actor))


def register_file(state: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    state.setdefault("files", []).append(meta)
    return meta


def discard(company_id: str, metas: list[dict[str, Any]]) -> None:
    """Delete stored files whose import was refused, so nothing is left orphaned on disk."""
    for meta in metas:
        try:
            file_path(company_id, meta).unlink()
        except OSError:
            pass


def store_bytes(company_id: str, filename: str, data: bytes, actor: str) -> dict[str, Any]:
    """Check and write a file to disk; no state is touched, so it can run outside the state lock."""
    name = safe_name(filename if isinstance(filename, str) else "file")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_FILES:
        allowed = ", ".join(sorted(e.lstrip(".") for e in ALLOWED_FILES))
        raise domain.GateError(f"{name}: this file type can't be imported. Allowed: {allowed}.")
    if not data:
        raise domain.GateError(f"{name} is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise domain.GateError(f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    file_id = "file-" + secrets.token_hex(8)
    folder = UPLOADS / company_id
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{file_id}.tmp"
    # On the disk before the rename, as the JSON files are (storage.write_json): a crash or a
    # lost disk cache never leaves a filed record pointing at an empty file (R6-06).
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(folder / f"{file_id}{ext}")
    meta = {
        "id": file_id,
        "name": name,
        "ext": ext,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "added_at": utcnow(),
        "added_by": actor,
    }
    return meta


def file_path(company_id: str, meta: dict[str, Any]) -> Path:
    return UPLOADS / company_id / f"{meta['id']}{meta['ext']}"


# ---------- reading text ----------

def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(raw)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def parse_email(data: bytes) -> dict[str, Any]:
    """Read a saved email (.eml / raw MIME) into sender, subject, date, body and attachments."""
    msg = email.message_from_bytes(data, policy=email.policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        try:
            content = body.get_content()
        except (LookupError, UnicodeDecodeError):
            content = body.get_payload(decode=True).decode("utf-8", "replace") if body.get_payload(decode=True) else ""
        text = html_to_text(content) if body.get_content_type() == "text/html" else content
    attachments = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True)
        if payload:
            attachments.append({"filename": part.get_filename() or "attachment", "data": payload})
    sent = ""
    try:  # Python 3.9 raises on reading a Date header it can't parse ("Mon, Oct 12, 2026 at 9:00 AM")
        if msg["date"]:
            sent = email.utils.parsedate_to_datetime(str(msg["date"])).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, IndexError):
        sent = ""
    return {
        "from": str(msg["from"] or ""),
        "to": str(msg["to"] or ""),
        "subject": str(msg["subject"] or ""),
        "sent_at": sent,
        "text": text.strip(),
        "attachments": attachments,
    }


# What Gmail, Outlook and Apple Mail put above a forwarded or quoted email (R5-06).
FORWARD_BANNER_RE = re.compile(r"^\s*(?:-{2,}\s*(?:forwarded message|original message)\s*-{2,}|begin forwarded message:?|_{8,})\s*$", re.I)
PASTED_HEADER_RE = re.compile(r"^\s*(from|subject|to|date|sent|cc):\s*\S", re.I)


def pasted_headers(text: str, strict: bool = False) -> tuple[str, str] | None:
    """Find the From:/Subject: block of a pasted email in its first 10 lines, past any forward
    banner and a line or two the person wrote above it. Returns (what is above it, the email
    from its headers on) for parse_email, or None. `strict` (pasted as "Something else") needs
    two header lines, one of them From: or Subject:, so a note that starts "To: Mike" stays a note."""
    lines = (text or "").splitlines()
    start = next((n for n, line in enumerate(lines[:10]) if PASTED_HEADER_RE.match(line)), None)
    if start is None:
        return None
    end = start
    while end < len(lines) and PASTED_HEADER_RE.match(lines[end]):
        end += 1
    names = {PASTED_HEADER_RE.match(line).group(1).lower() for line in lines[start:end]}
    if strict and (end - start < 2 or not names & {"from", "subject"}):
        return None
    above = "\n".join(line for line in lines[:start] if not FORWARD_BANNER_RE.match(line)).strip()
    # Outlook writes the address as [mailto:ap@x.com]; an email header wants <ap@x.com>.
    header = [re.sub(r"\s*\[mailto:([^\]\s]+)\]", r" <\1>", line.strip()) for line in lines[start:end]]
    body = "\n".join(lines[end:]).strip("\n")
    return above, "\n".join(header) + "\n\n" + body


def amounts(text: str) -> list[int]:
    """Dollar amounts, in cents, largest first, at most 5."""
    out = []
    for m in _MONEY_RE.finditer(text or ""):
        g = m.groups()
        whole, cents = (g[0], g[1]) if g[0] else (g[2], g[3]) if g[2] else (g[4], g[5])
        value = int(whole.replace(",", "")) * 100 + int(cents or 0)
        if value and value not in out:
            out.append(value)
    return sorted(out, reverse=True)[:5]


def _to_date(groups: tuple, kind: int, default_year: int) -> date | None:
    d = _to_date_raw(groups, kind, default_year)
    if d is None or not 2000 <= d.year <= 2100:
        return None
    # No year written and the date would be in the future: it was last year's note.
    explicit_year = (kind == 0) or (kind in (1, 2) and groups[2])
    if not explicit_year and d > datetime.now(timezone.utc).date() + timedelta(days=7):
        try:
            d = d.replace(year=d.year - 1)
        except ValueError:
            return None
    return d


def _to_date_raw(groups: tuple, kind: int, default_year: int) -> date | None:
    try:
        if kind == 0:
            y, m, d = int(groups[0]), int(groups[1]), int(groups[2])
        elif kind == 1:
            m, d = int(groups[0]), int(groups[1])
            y = int(groups[2]) if groups[2] else default_year
            if y < 100:
                y += 2000
        else:
            m = _MONTHS[groups[0][:3].lower()]
            d = int(groups[1])
            y = int(groups[2]) if groups[2] else default_year
        return date(y, m, d)
    except (ValueError, KeyError):
        return None


def dates(text: str, default_year: int | None = None) -> list[str]:
    """Calendar dates written in the text (ISO strings), first five, in order found."""
    default_year = default_year or datetime.now(timezone.utc).year
    found = []
    for kind, pattern in enumerate(_DATE_PATTERNS):
        for m in pattern.finditer(text or ""):
            if _quantity(kind, m, (text or "")[m.end():m.end() + 40]):
                continue
            d = _to_date(m.groups(), kind, default_year)
            if d and d.isoformat() not in found:
                found.append(d.isoformat())
    return found[:5]


# "May 2 guys out sick": May as a word, not the month.
_MAY_WORDS = {"guys", "men", "crew", "days", "hours", "people", "more"}


def _quantity(kind: int, m: re.Match, rest: str) -> bool:
    """A size or a count that looks like a date in running text: '3/4 inch backer screws',
    '5/8" drywall', 'May 2 guys'. Found lines and notes both skip these."""
    next_word = (re.findall(r"[a-z\"']+", rest.lower()) or [""])[0]
    if kind == 1 and not m.group(3):
        return next_word in UNIT_WORDS or rest.lstrip().startswith('"')
    if kind == 2 and not m.group(3) and m.group(1).lower() == "may":
        return next_word in _MAY_WORDS
    return False


def leading_date(line: str, default_year: int) -> date | None:
    """A date at the very start of a line, e.g. '10/12', 'Oct 12, 2026:', '2026-10-12 -'.

    Not a date: quantities with a unit ('3/4 inch pipe', '1/2 bath', '5/8" drywall'), and 'May' used as a
    word ('May 2 guys out sick') unless a separator or year follows.
    """
    stripped = line.strip().lstrip("#*-• ").strip()
    stripped = re.sub(r"^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+", "", stripped, count=1, flags=re.I)
    for kind, pattern in enumerate(_DATE_PATTERNS):
        m = pattern.match(stripped)
        if not m:
            continue
        rest = stripped[m.end():]
        separated = rest == "" or bool(re.match(r"\s*[:\-–—,.)]", rest))
        next_word = (re.findall(r"[a-z\"']+", rest.lower()) or [""])[0]
        if kind == 1 and not m.group(3) and not separated:
            # "10/16 VEST corridor" and "1/2 MILL tile" are dates; "3/4 inch pipe" and "1/2 bath" are
            # not. Only a unit, an inch mark or a first number that can't be a month makes it a fraction.
            if next_word in UNIT_WORDS or next_word.startswith('"') or int(m.group(1)) > 12:
                return None
        if kind == 2 and not m.group(3) and not separated and m.group(1).lower() == "may":
            return None
        return _to_date(m.groups(), kind, default_year)
    return None


def split_notes(text: str, default_year: int | None = None) -> list[dict[str, Any]]:
    """Split pasted notes (e.g. 30 days exported from a notes app) at lines that start with a date."""
    default_year = default_year or datetime.now(timezone.utc).year
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in (text or "").splitlines():
        d = leading_date(line, default_year)
        if d:
            current = {"date": d.isoformat(), "lines": [line.strip()]}
            chunks.append(current)
        elif current is not None:
            current["lines"].append(line)
        elif line.strip():
            current = {"date": None, "lines": [line]}
            chunks.append(current)
    out = []
    for c in chunks:
        body = "\n".join(c["lines"]).strip()
        if body:
            out.append({"date": c["date"], "text": body})
    return out


def spaced(name: str) -> str:
    """A file name as words: 'Scan_KENN_sub-contract.pdf' -> 'Scan KENN sub contract pdf'.
    Phones and scanners join words with _ - and ., which regex word boundaries don't split."""
    return re.sub(r"[_\-.]+", " ", name or "")


def guess_kind(name: str, text: str, exts: str | list[str] | tuple = ()) -> str:
    """The kind from words in the name or text; else a photo when the only files besides a
    saved email are images (an email with a picture attached is the picture)."""
    hay = f"{spaced(name)}\n{text[:4000]}".lower()
    for kind, pattern in _KIND_RULES:
        if re.search(pattern, hay):
            if kind == "invoice":
                return "pay_app" if PAYAPP_NUMBER_RE.search(f"{name}\n{text[:4000]}") else "supplier_invoice"
            return kind
    exts = [exts] if isinstance(exts, str) else list(exts)
    files = [e for e in exts if e and e != ".eml"]
    if files and all(e in IMAGE_EXTS for e in files):
        return "photo"
    return "note"


# ---------- matching to a job ----------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _street(address: str) -> str:
    """Street number and street name, '3895 Cherokee St NW, Kennesaw' -> '3895 cherokee'."""
    m = re.match(r"(\d+[a-z]?) ([a-z]{3,})\b", _norm(address))
    return f"{m.group(1)} {m.group(2)}" if m else ""


# Words that say what kind of job or place it is, not which one: "kitchen" or "Street" alone
# names no job, so a part of a job name counts only with a word that is not one of these.
GENERIC_NAME_WORDS = {
    "the", "and", "of", "at", "on", "in", "for", "to", "a", "an", "job", "project", "phase", "new", "old", "main",
    "street", "st", "avenue", "ave", "road", "rd", "drive", "dr", "lane", "ln", "way", "court", "ct", "place", "pl",
    "boulevard", "blvd", "parkway", "pkwy", "highway", "hwy", "circle", "cir", "trail", "north", "south", "east", "west",
    "n", "s", "e", "w", "nw", "ne", "sw", "se", "kitchen", "bath", "bathroom", "remodel", "renovation", "addition",
    "house", "home", "residence", "building", "bldg", "office", "offices", "clinic", "school", "church", "center",
    "centre", "clubhouse", "store", "shop", "plaza", "tower", "apartments", "apts", "unit", "suite", "floor", "level",
    "garage", "basement", "deck", "porch", "roof", "repair", "repairs", "interior", "exterior", "tenant", "ti",
    "finish", "buildout", "build", "out", "closed", "inc", "llc", "co", "company", "group", "park", "creek", "hill",
    "lake", "river", "lobby", "hall", "room", "rooms", "work", "site",
    # Kinds of building or tenant ("Vesta Senior Living", "Glenwood Retail Shell", "Kennesaw Ortho Clinic").
    "living", "senior", "retail", "shell", "ortho", "dental", "medical", "suite", "lofts", "loft", "amenity",
    "townhomes", "townhouses", "condo", "condos", "baths", "library", "annex", "hills", "apartment", "hotel",
    "restaurant", "warehouse", "hospital", "campus", "academy", "bank", "space", "studio", "gym", "pool",
    # Trades and phases (PHASE_RE below): "Peachtree drywall" is not named by "drywall delivery friday".
    "framing", "drywall", "rough", "demo", "demolition", "tile", "tiling", "paint", "painting", "inspection",
    "punch", "trim", "cabinet", "cabinets", "plumbing", "electrical", "hvac", "roofing", "foundation", "footing",
    "footings", "slab", "insulation", "siding", "flooring", "floors", "concrete", "masonry", "stone", "carpentry",
    "millwork", "glazing", "windows", "doors", "landscaping", "paving", "pavers", "waterproofing", "stucco",
}


def _phrase_in(words: list[str], hay_padded: str) -> bool:
    return f" {' '.join(words)} " in hay_padded


def _name_parts(name_words: list[str]) -> list[list[str]]:
    """The runs of a job name's words that could name it on their own, longest first, the full
    name left out: two or more words with one that is not generic ('oak street' of 'Oak Street
    kitchen'), or one word of five letters or more that is not generic ('vesta')."""
    parts = []
    n = len(name_words)
    for size in range(n - 1, 0, -1):
        for start in range(0, n - size + 1):
            run = name_words[start:start + size]
            telling = [w for w in run if w not in GENERIC_NAME_WORDS and not w.isdigit()]
            if not telling:
                continue
            if size == 1 and (len(run[0]) < 5 or not run[0].isalpha()):
                continue
            parts.append(run)
    return parts


def _shown_part(name: str, part: list[str]) -> str:
    """The part as the job name writes it: ['oak', 'street'] of 'Oak Street kitchen' -> 'Oak Street'."""
    m = re.search(r"(?<![A-Za-z0-9])" + r"[^A-Za-z0-9]+".join(map(re.escape, part)) + r"(?![A-Za-z0-9])", name, re.IGNORECASE)
    return m.group(0) if m else " ".join(part)


def match_job(state: dict[str, Any], text: str, sender: str = "", names: str = "") -> dict[str, Any]:
    """Suggest the job this item belongs to, with the reason. Never files anything.

    A job code in capitals counts (70), and in a file name in any case. In small letters it is
    a weak sign (55, under the sender's AP email at 60): it must stand alone ('kenn', not
    'Kennesaw'), and not be a word of any open job's name or address, since 'the mill shop' is
    not job MILL of 'Mill Creek Clubhouse'. A job name counts in full (80), or by a part only
    that job's name and address have: two words or more ('Oak Street' for 'Oak Street kitchen',
    75), or one telling word written with a capital ('Vesta', 55; never 'Street' or 'drywall').
    So an everyday word in an email from a customer's AP email never pulls the item to another
    customer's job. When two jobs fit equally, it does not guess."""
    jobs = [j for j in state.get("jobs", []) if not j.get("closed")]
    text = f"{text}\n{spaced(names)}" if names else text
    hay = _norm(text)
    hay_padded = f" {hay} "
    scores: dict[str, tuple[int, str]] = {}

    def bump(job: dict[str, Any], score: int, reason: str) -> None:
        if score > scores.get(job["id"], (0, ""))[0]:
            scores[job["id"]] = (score, reason)

    upper_tokens = set(re.findall(r"\b[A-Z0-9]{3,8}\b", text or ""))
    upper_tokens |= {t.upper() for t in re.findall(r"\b[A-Za-z0-9]{3,8}\b", spaced(names))}
    upper_tokens -= NOT_CODES
    # Each job's name and address words, to tell a part of a name only it has.
    words_of = {j["id"]: f" {_norm(j.get('name', ''))} {_norm(j.get('address', ''))} " for j in jobs}
    name_words = {w.upper() for ws in words_of.values() for w in ws.split()}
    any_tokens = {t.upper() for t in re.findall(r"[A-Za-z0-9]+", text or "") if 3 <= len(t) <= 8} - NOT_CODES - name_words
    # Words written with a capital, and every word of a file name: a single word of a job name counts only there.
    capitalized = {w.lower() for w in re.findall(r"\b[A-Z][A-Za-z]*", text or "")} | set(_norm(spaced(names)).split())
    sender_addr = (email.utils.parseaddr(sender)[1] or sender).lower().strip()
    payers = {p["id"]: p for p in state.get("payers", [])}
    for job in jobs:
        code = str(job.get("code") or "")
        if code and f"PA-{code}-" in (text or ""):
            bump(job, 100, f"mentions pay app PA-{code}")
        elif code and code in upper_tokens:
            # A bare code is weaker than the job's name or address, which are harder to hit by accident.
            bump(job, 70, f"mentions job code {code}")
        elif code and code.upper() in any_tokens:
            bump(job, 55, f"mentions job code {code}")
        addr = _norm(job.get("address", ""))
        street = _street(job.get("address", ""))
        if addr and len(addr) >= 6 and f" {addr} " in hay_padded:
            bump(job, 90, f"mentions the address {job.get('address')}")
        elif street and f" {street} " in hay_padded:
            bump(job, 85, f"mentions the street {street.title()}")
        name = _norm(job.get("name", ""))
        if name and len(name) >= 4 and f" {name} " in hay_padded:
            bump(job, 80, f"mentions the job name {job.get('name')}")
        elif name:
            others = [w for jid, w in words_of.items() if jid != job["id"]]
            for part in _name_parts(name.split()):
                phrase = f" {' '.join(part)} "
                if len(part) == 1 and part[0] not in capitalized:
                    continue
                if phrase in hay_padded and not any(phrase in o for o in others):
                    bump(job, 75 if len(part) > 1 else 55, f"mentions {_shown_part(job['name'], part)}, part of the job name {job['name']}")
                    break
        payer = payers.get(job.get("payer_id"), {})
        if sender_addr and sender_addr == str(payer.get("ap_email", "")).lower():
            bump(job, 60, f"sent from {payer.get('name')}'s AP email")
        pname = _norm(payer.get("name", ""))
        if pname and len(pname) >= 4 and f" {pname} " in hay_padded:
            bump(job, 50, f"mentions the client {payer.get('name')}")
    ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
    if not ranked:
        return {"job_id": None, "reason": "No job name, code, address or client found.", "candidates": []}
    top_score = ranked[0][1][0]
    tied = [jid for jid, (s, _) in ranked if s == top_score]
    if len(tied) > 1:
        # The tied jobs come first; the Inbox card offers them as one-tap choices (R5-05).
        return {"job_id": None, "reason": "More than one job fits. Pick one.", "candidates": [jid for jid, _ in ranked[:5]],
                "tied": min(len(tied), 5)}
    return {"job_id": ranked[0][0], "reason": ranked[0][1][1], "candidates": [jid for jid, _ in ranked[:5]]}


# ---------- inbox ----------

DONE_WORD_RE = re.compile(r"\b(done|finished|complete|completed|wrapped up)\b", re.IGNORECASE)
NOT_DONE_RE = re.compile(r"\b(not|almost|nearly|until|when|once|if|before|after|will|soon|tomorrow|by)\b|n't\b", re.IGNORECASE)
# One phase finishing ("framing done") is not the job finishing.
PHASE_RE = re.compile(r"\b(framing|drywall|rough|rough-in|demo|demolition|tile|tiling|paint|painting|inspection|phase|"
                      r"punch|trim|cabinets?|plumbing|electrical|hvac|roof|roofing|foundation|footings?|slab|insulation|siding)\b", re.IGNORECASE)


def says_done(text: str) -> bool:
    """A message saying the work is finished ("Grant Park done", "Job's done on Oak St").
    A line with a negation or a future ("not done", "done by Friday") doesn't count; the
    Inbox only offers a button, so a misread costs one click, never a pay app."""
    for line in (text or "").splitlines()[:20]:
        if DONE_WORD_RE.search(line) and not NOT_DONE_RE.search(line) and not PHASE_RE.search(line):
            return True
    return False


def add_item(
    state: dict[str, Any],
    source: str,
    text: str = "",
    sender: str = "",
    subject: str = "",
    file_ids: list[str] | None = None,
    job_id: str | None = None,
    sent_at: str = "",
    note_date: str | None = None,
    extra: dict[str, Any] | None = None,
    job_reason: str = "Added from the job page.",
) -> dict[str, Any]:
    text = (text or "")[:MAX_TEXT_CHARS]
    if len([i for i in state.get("inbox", []) if i.get("status") == "new"]) >= MAX_NEW_INBOX:
        raise domain.GateError(f"The Inbox has {MAX_NEW_INBOX} items waiting. File or dismiss some first.")
    files = [f for f in state.get("files", []) if f["id"] in (file_ids or [])]
    names = " ".join(f["name"] for f in files)
    if job_id and not any(j["id"] == job_id and not j.get("closed") for j in state.get("jobs", [])):
        job_id = None
    match = {"job_id": job_id, "reason": job_reason, "candidates": [job_id]} if job_id else match_job(
        state, f"{subject}\n{text}", sender, names=names
    )
    item = {
        "id": "inb-" + secrets.token_hex(5),
        "source": source,
        "received_at": utcnow(),
        "sent_at": sent_at,
        "from": sender[:200],
        "subject": subject[:300],
        "text": text,
        "file_ids": [f["id"] for f in files],
        "kind": "daily" if source == "notes" else guess_kind(f"{subject} {names}", text, [f["ext"] for f in files]),
        "amounts": amounts(f"{subject}\n{text}"),
        "dates": dates(f"{subject}\n{text}"),
        "note_date": note_date,
        "suggested_job_id": match["job_id"],
        "match_reason": match["reason"],
        "candidates": match["candidates"],
        "tied": match.get("tied", 0),
        "status": "new",
    }
    if extra:
        item.update(extra)
    # "Job's done" by text or email (P3): offer to mark the suggested job done in one click.
    if source in {"sms", "email"} and match["job_id"] and says_done(f"{subject}\n{text}"):
        job = next((j for j in state.get("jobs", []) if j["id"] == match["job_id"]), {})
        if job.get("work_status") != "complete" and not job.get("closed"):
            item["says_done_job_id"] = job["id"]
    state.setdefault("inbox", []).append(item)
    return item


def file_and_mark_done(state: dict[str, Any], item_id: str, actor: str) -> dict[str, Any]:
    """File a "job's done" message to its job, mark the job done and draft the final pay app.
    Sending still needs a person to approve it."""
    item = _item(state, item_id)
    job_id = item.get("says_done_job_id")
    if not job_id:
        raise domain.GateError("This item doesn't say a job is done.")
    file_item(state, item_id, job_id, item["kind"] if item["kind"] != "daily" else "note", actor)
    note = (item.get("text") or "").strip().splitlines()[0][:300] if item.get("text") else ""
    return domain.mark_done(state, job_id, actor, notes=f"From {item.get('from') or 'a message'}: {note}".strip(),
                            note_record=False)


def _item(state: dict[str, Any], item_id: str) -> dict[str, Any]:
    item = next((i for i in state.get("inbox", []) if i["id"] == item_id), None)
    if not item:
        raise domain.GateError("Inbox item not found.")
    return item


def file_item(state: dict[str, Any], item_id: str, job_id: str, kind: str, actor: str) -> dict[str, Any]:
    """Put an inbox item on a job's record. Daily notes become daily records with their own date."""
    item = _item(state, item_id)
    if item["status"] != "new":
        raise domain.GateError("This item was already filed or dismissed.")
    job = domain._open_job(state, job_id) if job_id else None
    if not job:
        raise domain.GateError("Pick a job to file this to.")
    kind = kind if kind in KINDS else item["kind"]
    files = [f for f in state.get("files", []) if f["id"] in item["file_ids"]]
    label = item["subject"] or (files[0]["name"] if files else "") or (item["text"].strip().splitlines() or ["Imported note"])[0]
    label = label.strip()[:120] or "Imported note"
    source_line = {"email": "Email", "sms": "Text message", "paste": "Pasted", "notes": "Notes import", "upload": "Upload", "payer_page": "Client page"}.get(item["source"], item["source"])
    if item["from"]:
        source_line += f" from {item['from']}"
    record_ids = []
    if kind == "daily":
        at = f"{item['note_date']}T12:00:00+00:00" if item.get("note_date") else None
        domain.add_daily_record(state, job_id, item["text"], actor, at=at)
        state["daily_records"][-1]["import_inbox_id"] = item["id"]
        record_ids.append(job["records"][-1]["id"])
    else:
        note = item["text"][:5000]
        domain.add_job_record(state, job_id, label, kind, note, actor)
        record_ids.append(job["records"][-1]["id"])
    for rid in record_ids:
        rec = next(r for r in job["records"] if r["id"] == rid)
        rec["source"] = source_line
        rec["file_ids"] = list(item["file_ids"])
        rec["inbox_id"] = item["id"]
    item.update({"status": "filed", "filed_job_id": job_id, "filed_kind": kind, "record_ids": record_ids, "filed_at": utcnow(), "filed_by": actor})
    domain.audit(state, job_id, "import_filed", f"{actor} filed {KINDS.get(kind, kind).lower()} '{label}' ({source_line}) to the job record.", actor)
    return item


PAYAPP_NUMBER_RE = re.compile(r"\bPA-[A-Z0-9]{1,12}-\d{1,4}\b")
QUOTE_START_RE = re.compile(r"^(On .{0,200} wrote:|-{2,} ?Original Message ?-{2,}|From: )", re.IGNORECASE)


def _reply_summary(text: str) -> str:
    """The first line the payer wrote, above the quoted original."""
    for line in (text or "").splitlines():
        line = line.strip()
        if QUOTE_START_RE.match(line):
            break
        if line and not line.startswith(">"):
            return line[:140]
    return "(no text above the quoted message)"


def capture_reply(state: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    """File an inbound email to its job when it answers a pay app this company emailed (P1-3).

    Only when the subject names a pay app (PA-XXX-N) whose email went out, and the sender is the
    address it went to, someone copied on it, or someone at the same domain. Anything else stays
    in the Inbox for a person to file. The job's status line then says who replied, when and the
    first line of what they wrote. Undo in the Inbox takes it back off, status line included."""
    if item.get("source") != "email" or item.get("status") != "new":
        return None
    numbers = set(PAYAPP_NUMBER_RE.findall(item.get("subject") or ""))
    if not numbers:
        return None
    sender = (email.utils.parseaddr(item.get("from") or "")[1] or "").lower()
    if "@" not in sender:
        return None
    for entry in reversed(state.get("email_log", [])):
        if entry.get("status") != "sent" or not numbers & set(PAYAPP_NUMBER_RE.findall(entry.get("subject") or "")):
            continue
        sent_to = [str(entry.get("to") or "").lower()] + [str(c).lower() for c in entry.get("cc") or []]
        domains = {a.rsplit("@", 1)[-1] for a in sent_to if "@" in a}
        if sender not in sent_to and sender.rsplit("@", 1)[-1] not in domains:
            continue
        job = next((j for j in state.get("jobs", []) if j["id"] == entry.get("job_id") and not j.get("closed")), None)
        if not job:
            return None
        payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
        file_item(state, item["id"], job["id"], "note", "duracash")
        when = datetime.now(timezone.utc)
        summary = _reply_summary(item.get("text", ""))
        item["prev_external_status"] = job.get("external_status")
        item["prev_last_reply"] = job.get("last_reply")
        item["auto_filed"] = True
        job["external_status"] = f"{payer.get('name') or sender} replied {when.strftime('%b')} {when.day}: {summary}"
        job["last_reply"] = {"from": sender, "at": utcnow(), "subject": item.get("subject", ""), "summary": summary, "inbox_id": item["id"]}
        if re.search(r"\b(need|needs|missing|required|require|attach|send us|please send)\b", summary, re.IGNORECASE):
            domain.suggest_packet_item(state, job.get("payer_id", ""), summary, "a reply", job["name"])
        domain.audit(state, job["id"], "reply_captured",
                     f"A reply from {sender} to {', '.join(sorted(numbers))} was filed to the job record: {summary}", "duracash")
        return job
    return None


def dismiss_item(state: dict[str, Any], item_id: str, actor: str) -> dict[str, Any]:
    item = _item(state, item_id)
    if item["status"] != "new":
        raise domain.GateError("Only a new item can be dismissed.")
    item.update({"status": "dismissed", "dismissed_at": utcnow(), "dismissed_by": actor})
    return item


def _paperwork_refusal(job: dict[str, Any], rids: set[str], can_pull_back: bool = True) -> "domain.GateError | None":
    """Why Undo can't take these records off, with a link to where that is settled (Oct 23 note #13).
    A sent pay app comes first: what went with it stays as it was sent, however it was attached,
    so its page is the way, not Remove. Else the paperwork page, where Remove is, for a record
    attached by hand (one attached automatically comes off with the record).
    Only an owner can pull a pay app back (void-sent), so a member is told to ask one."""
    def holds(packet: list[dict[str, Any]] | None, auto_too: bool = False) -> bool:
        return any(a.get("record_id") in rids and (auto_too or not a.get("auto"))
                   for p in packet or [] for a in p.get("attachments", []))

    def link(label: str, view: str, detail: str = "") -> dict[str, str]:
        return {"label": label, "view": view, "job_id": job["id"], "detail": detail}

    # A deleted draft is void too, but it never went out (no sent_at): nothing it held went to anyone.
    went = [inv for inv in job.get("invoices", []) if inv.get("status") not in domain.DRAFT_STATUSES
            and (inv.get("status") != "void" or inv.get("sent_at")) and holds(inv.get("packet"), auto_too=True)]
    if went:
        inv = went[-1]
        number = inv.get("number") or "a pay app"
        open_it = link(f"Open {number}", "payapp-detail", inv["id"])
        paid = inv.get("status") in {"partial", "paid"} or any(p.get("invoice_id") == inv["id"] for p in job.get("payments", []))
        if inv.get("status") == "void":
            text = (f"This record went to the client with {number}. {number} was pulled back, but what it sent "
                    "stays as it was, so the record stays on the job.")
        elif paid:
            text = f"This record went to the client with {number}, and a payment is recorded on it, so the record stays on the job."
        elif can_pull_back:
            text = (f"This record went to the client with {number}, so it stays on the job. To change what went out, "
                    f"open {number} and pull it back, then send a new one.")
        else:
            text = (f"This record went to the client with {number}, so it stays on the job. "
                    f"Ask an owner to pull {number} back if it needs to change.")
        return domain.GateError(text, action=open_it)
    drafts = [inv for inv in job.get("invoices", []) if inv.get("status") in domain.DRAFT_STATUSES and holds(inv.get("packet"))]
    if drafts:
        number = drafts[-1].get("number") or "the pay app"
        return domain.GateError(f"The paperwork for {number} uses this record. Tap Remove on it there first.",
                                action=link(f"Open the paperwork for {number}", "packet-detail", drafts[-1]["id"]))
    if holds(job.get("packet")):
        return domain.GateError("The job's paperwork uses this record. Tap Remove on it there first.",
                                action=link("Open the paperwork", "packet-detail"))
    if holds(job.get("closeout_packet")):
        return domain.GateError("The closeout paperwork uses this record. Tap Remove on it there first.",
                                action=link("Open the closeout paperwork", "packet-detail", "closeout"))
    return None


def undo_item(state: dict[str, Any], item_id: str, actor: str, can_pull_back: bool = True) -> dict[str, Any]:
    """Take a filed item back off the job record, unless paperwork uses it (the refusal links there).
    can_pull_back: whether this person may pull a sent pay app back, which words the refusal."""
    item = _item(state, item_id)
    if item["status"] == "dismissed":
        item["status"] = "new"
        return item
    if item["status"] != "filed":
        raise domain.GateError("Nothing to undo.")
    job = domain._open_job(state, item["filed_job_id"])
    rids = set(item.get("record_ids", []))
    packets = [p for inv in job["invoices"] for p in (inv.get("packet") or [])] + (job.get("packet") or []) + (job.get("closeout_packet") or [])
    refusal = _paperwork_refusal(job, rids, can_pull_back)
    if refusal:
        raise refusal
    for p in packets:  # attached automatically from the job record: it comes off with the record
        if any(a.get("record_id") in rids for a in p.get("attachments", [])):
            p["attachments"] = [a for a in p["attachments"] if a.get("record_id") not in rids]
            if not p["attachments"] and not p.get("present_reason") and not p.get("marked_in_hand"):
                p["present"] = False
    domain._refresh_draft_gate(job)
    job["records"] = [r for r in job.get("records", []) if r["id"] not in rids]
    dr_ids = {d["id"] for d in state.get("daily_records", []) if d.get("import_inbox_id") == item_id}
    state["daily_records"] = [d for d in state.get("daily_records", []) if d["id"] not in dr_ids]
    job["daily_records"] = [d for d in job.get("daily_records", []) if d not in dr_ids]
    if item.get("auto_filed"):
        # A captured reply also changed the status line; put it back, unless something newer
        # (the customer approving it, say) has replaced the reply's line since.
        prev = item.pop("prev_external_status", None)
        prev_last = item.pop("prev_last_reply", None)
        last = job.get("last_reply") or {}
        if last.get("inbox_id") == item_id:
            if str(job.get("external_status") or "").endswith(f": {last.get('summary', '')}"):
                job["external_status"] = prev
            if prev_last:  # the reply before this one is the latest again
                job["last_reply"] = prev_last
            else:
                job.pop("last_reply", None)
        else:
            # A newer reply replaced this one's line: undoing that one later puts back what this one replaced.
            newer = next((i for i in state.get("inbox", []) if i.get("auto_filed")
                          and (i.get("prev_last_reply") or {}).get("inbox_id") == item_id), None)
            if newer:
                if str(newer.get("prev_external_status") or "").endswith(f": {newer['prev_last_reply'].get('summary', '')}"):
                    newer["prev_external_status"] = prev
                newer["prev_last_reply"] = prev_last
        item["auto_filed"] = False
    item.update({"status": "new", "record_ids": [], "filed_job_id": None})
    domain.audit(state, job["id"], "import_undone", f"{actor} took an imported item back off the job record.", actor)
    return item


# ---------- inbound tokens ----------

def tokens_path() -> Path:
    return storage.DATA_DIR / "inbound_tokens.json"


def load_tokens() -> dict[str, Any]:
    import json
    p = tokens_path()
    return json.loads(p.read_text()) if p.exists() else {}


def company_tokens(company_id: str, create: bool = True) -> dict[str, str]:
    """The secret tokens in this company's inbound email, SMS and calendar URLs."""
    with TOKENS_LOCK:
        data = load_tokens()
        mine = {kind: tok for tok, v in data.items() if v["company_id"] == company_id for kind in [v["kind"]]}
        changed = False
        for kind in ("email", "sms", "calendar"):
            if kind not in mine and create:
                tok = secrets.token_urlsafe(18)
                data[tok] = {"company_id": company_id, "kind": kind, "created_at": utcnow()}
                mine[kind] = tok
                changed = True
        if changed:
            storage.write_json(tokens_path(), data)
        return mine


def rotate_token(company_id: str, kind: str) -> str:
    with TOKENS_LOCK:
        data = {t: v for t, v in load_tokens().items() if not (v["company_id"] == company_id and v["kind"] == kind)}
        tok = secrets.token_urlsafe(18)
        data[tok] = {"company_id": company_id, "kind": kind, "created_at": utcnow()}
        storage.write_json(tokens_path(), data)
        return tok


def company_for_token(token: str, kind: str) -> str | None:
    entry = load_tokens().get(token or "")
    if entry and entry["kind"] == kind:
        return entry["company_id"]
    return None


def forget_company(company_id: str) -> None:
    """Drop a deleted company's inbound and calendar tokens, so its addresses stop working."""
    with TOKENS_LOCK:
        data = {t: v for t, v in load_tokens().items() if v["company_id"] != company_id}
        storage.write_json(tokens_path(), data)

# ---------- what providers and the phone post (moved from server.py, R6-08) ----------

class BadPayload(ValueError):
    """A post the server can't read at all; the route answers 400 with this message."""


def parse_share(data: bytes, raw_ctype: str) -> dict:
    """The multipart form a phone's share menu posts to /share: title, text, url and files."""
    out = {"title": "", "text": "", "url": "", "files": []}
    if "multipart/form-data" not in raw_ctype.lower():
        return out
    msg = email.message_from_bytes(b"Content-Type: " + raw_ctype.encode("latin-1", "replace") + b"\r\n\r\n" + data, policy=email.policy.default)
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename or name == "files":
            if payload:
                out["files"].append({"filename": filename or "shared file", "data": payload})
        elif name in {"title", "text", "url"}:
            out[name] = payload.decode("utf-8", "replace")[:5000]
    out["files"] = out["files"][:20]
    return out


def provider_fields(data: bytes, raw_ctype: str) -> dict:
    """The plain fields of a form or JSON post (not the attachments), lower-cased names."""
    ctype = raw_ctype.lower()
    if "application/json" in ctype:
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return {}
        if not isinstance(raw, dict):
            return {}
        # Mailgun's JSON webhooks put the three in a "signature" object.
        nested = raw.get("signature") if isinstance(raw.get("signature"), dict) else {}
        return {str(k).lower(): v for k, v in {**raw, **nested}.items()}
    if "application/x-www-form-urlencoded" in ctype:
        return {k.lower(): v for k, v in parse_qsl(data.decode("utf-8", "replace"), keep_blank_values=True)}
    if "multipart/form-data" in ctype:
        msg = email.message_from_bytes(b"Content-Type: " + raw_ctype.encode("latin-1", "replace") + b"\r\n\r\n" + data, policy=email.policy.default)
        return {(part.get_param("name", header="content-disposition") or "").lower(): (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
                for part in msg.iter_parts() if not part.get_filename()}
    return {}


def parse_inbound_email(data: bytes, raw_ctype: str) -> dict:
    """Accept raw MIME, a JSON payload, or a multipart form post from an email provider.

    Field names below are the common ones for SendGrid Inbound Parse, Mailgun
    routes and Postmark inbound, from their documentation as I remember it;
    they have not been tested against the live services.
    """
    ctype = raw_ctype.lower()
    if "application/json" in ctype:
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise BadPayload("Request body must be valid JSON.")
        if not isinstance(raw, dict):
            raise BadPayload("Request body must be a JSON object.")
        low = {k.lower(): v for k, v in raw.items()}
        if isinstance(low.get("email"), str):
            return parse_email(low["email"].encode("utf-8"))
        text = low.get("text") or low.get("textbody") or low.get("body-plain") or low.get("stripped-text") or ""
        if not text and (low.get("html") or low.get("htmlbody")):
            text = html_to_text(str(low.get("html") or low.get("htmlbody")))
        attachments = []
        raw_atts = low.get("attachments")
        for att in raw_atts if isinstance(raw_atts, list) else []:
            if not isinstance(att, dict):
                continue
            a = {str(k).lower(): v for k, v in att.items()}
            content = a.get("content") or a.get("content_base64") or ""
            if not isinstance(content, str):
                continue
            try:
                blob = base64.b64decode(content, validate=False)
            except (ValueError, TypeError):
                continue
            name = a.get("name") or a.get("filename")
            attachments.append({"filename": name if isinstance(name, str) else "attachment", "data": blob})
        return {"from": str(low.get("from") or low.get("sender") or "")[:300], "subject": str(low.get("subject") or "")[:300],
                "text": str(text), "sent_at": "", "attachments": attachments}
    if "multipart/form-data" in ctype:
        msg = email.message_from_bytes(b"Content-Type: " + raw_ctype.encode("latin-1", "replace") + b"\r\n\r\n" + data, policy=email.policy.default)
        fields, attachments = {}, []
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition") or ""
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                attachments.append({"filename": filename, "data": payload})
            else:
                fields[name.lower()] = payload.decode("utf-8", "replace")
        return _email_from_fields(fields, attachments)
    if "application/x-www-form-urlencoded" in ctype:
        # Mailgun posts a message without attachments as a plain form.
        return _email_from_fields(provider_fields(data, raw_ctype), [])
    return parse_email(data)


def _email_from_fields(fields: dict, attachments: list) -> dict:
    if fields.get("email"):
        parsed = parse_email(fields["email"].encode("utf-8"))
        parsed["attachments"] += attachments
        return parsed
    text = fields.get("text") or fields.get("body-plain") or fields.get("stripped-text") or ""
    if not text and fields.get("html"):
        text = html_to_text(fields["html"])
    return {"from": fields.get("from") or fields.get("sender") or "", "subject": fields.get("subject", ""),
            "text": text, "sent_at": "", "attachments": attachments}


# ---------- shared while signed out (R6-10) ----------
# A phone can share to the app before anyone has signed in on it. What it shared waits here, in
# a folder named by a random key that only that browser holds (a cookie), for HOLD_MINUTES;
# after sign-in the app offers "Finish adding N shared items". Until then it is in no company.
HOLD_MINUTES = 30
HOLD_MAX_BYTES = 200 * 1024 * 1024  # every held share on the server together
HOLD_KEY_RE = re.compile(r"[A-Za-z0-9_-]{20,64}")
HOLD_LOCK = threading.Lock()


def _holds() -> Path:
    return storage.DATA_DIR / "held_shares"


def _expire_holds(now: float) -> int:
    """Remove holds past HOLD_MINUTES; returns the bytes the rest take."""
    kept = 0
    for folder in _holds().glob("*") if _holds().exists() else []:
        try:
            if now - folder.stat().st_mtime > HOLD_MINUTES * 60:
                shutil.rmtree(folder, ignore_errors=True)
            else:
                kept += sum(f.stat().st_size for f in folder.iterdir())
        except OSError:
            continue
    return kept


def hold_share(parsed: dict[str, Any], now: float | None = None) -> str | None:
    """Keep what a phone shared (parse_share's dict); returns its key, or None when nothing
    came or the server's room for held shares is used up."""
    files = [f for f in parsed.get("files", []) if f.get("data")]
    if not files and not (parsed.get("text") or parsed.get("url")):
        return None
    with HOLD_LOCK:
        used = _expire_holds(now or time.time())
        if used + sum(len(f["data"]) for f in files) > HOLD_MAX_BYTES:
            return None
        key = secrets.token_urlsafe(24)
        folder = _holds() / key
        folder.mkdir(parents=True)
        for n, f in enumerate(files):
            (folder / f"{n}.bin").write_bytes(f["data"])
        storage.write_json(folder / "share.json", {"title": parsed.get("title", ""), "text": parsed.get("text", ""), "url": parsed.get("url", ""),
                                                   "files": [f.get("filename") or "shared file" for f in files]})
    return key


def held_share(key: str | None, now: float | None = None) -> dict[str, Any] | None:
    """What is held under key, in parse_share's shape (file data read back), or None when the
    key is unknown or the hold has expired."""
    if not key or not HOLD_KEY_RE.fullmatch(key):
        return None
    folder = _holds() / key
    try:
        if (now or time.time()) - folder.stat().st_mtime > HOLD_MINUTES * 60:
            drop_share(key)
            return None
        meta = json.loads((folder / "share.json").read_text())
        files = [{"filename": name, "data": (folder / f"{n}.bin").read_bytes()} for n, name in enumerate(meta.get("files", []))]
    except (OSError, ValueError):
        return None
    return {"title": meta.get("title", ""), "text": meta.get("text", ""), "url": meta.get("url", ""), "files": files}


def held_count(key: str | None, now: float | None = None) -> int:
    """How many Inbox items the share held under key becomes (one per file, or one for text
    alone); 0 when there is none. Reads only the list, not the files."""
    if not key or not HOLD_KEY_RE.fullmatch(key):
        return 0
    folder = _holds() / key
    try:
        if (now or time.time()) - folder.stat().st_mtime > HOLD_MINUTES * 60:
            return 0
        meta = json.loads((folder / "share.json").read_text())
    except (OSError, ValueError):
        return 0
    return len(meta.get("files", [])) or (1 if meta.get("text") or meta.get("url") else 0)


def drop_share(key: str) -> None:
    if key and HOLD_KEY_RE.fullmatch(key):
        shutil.rmtree(_holds() / key, ignore_errors=True)
