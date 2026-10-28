"""Bring outside paperwork into the job record.

Sources: uploaded files (photos, PDFs, spreadsheets, saved emails), pasted
email or text-message threads, pasted notes (split into one daily record per
date), and inbound email or SMS forwarded by a provider to a per-company
webhook. Everything lands in the company's Inbox with a suggested job, kind,
amounts and dates; one click files it to the job record. Nothing here uses AI:
matching and extraction are plain rules, and nothing is filed without a person.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import hashlib
import html
import re
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    "pay_app": "Pay app / invoice backup",
    "ticket": "Delivery or time ticket",
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
    # An invoice is a supplier's bill (a ticket for the backup) unless it names one of our pay apps (R5-07).
    ("invoice", r"\binvoice\b"),
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
    tmp.write_bytes(data)
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
                return "pay_app" if PAYAPP_NUMBER_RE.search(f"{name}\n{text[:4000]}") else "ticket"
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


def match_job(state: dict[str, Any], text: str, sender: str = "", names: str = "") -> dict[str, Any]:
    """Suggest the job this item belongs to, with the reason. Never files anything.

    `names` are the item's file names: a job code standing alone there counts in any case
    ('kenn_subcontract.pdf'); in the text it has to be written in capitals, so 'the mill' is
    not job MILL."""
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
    sender_addr = (email.utils.parseaddr(sender)[1] or sender).lower().strip()
    payers = {p["id"]: p for p in state.get("payers", [])}
    for job in jobs:
        if job.get("code") and f"PA-{job['code']}-" in (text or ""):
            bump(job, 100, f"mentions pay app PA-{job['code']}")
        elif job.get("code") and job["code"] in upper_tokens:
            # A bare code is weaker than the job's name or address, which are harder to hit by accident.
            bump(job, 70, f"mentions job code {job['code']}")
        addr = _norm(job.get("address", ""))
        street = _street(job.get("address", ""))
        if addr and len(addr) >= 6 and f" {addr} " in hay_padded:
            bump(job, 90, f"mentions the address {job.get('address')}")
        elif street and f" {street} " in hay_padded:
            bump(job, 85, f"mentions the street {street.title()}")
        name = _norm(job.get("name", ""))
        if name and len(name) >= 4 and f" {name} " in hay_padded:
            bump(job, 80, f"mentions the job name {job.get('name')}")
        payer = payers.get(job.get("payer_id"), {})
        if sender_addr and sender_addr == str(payer.get("ap_email", "")).lower():
            bump(job, 60, f"sent from {payer.get('name')}'s AP email")
        pname = _norm(payer.get("name", ""))
        if pname and len(pname) >= 4 and f" {pname} " in hay_padded:
            bump(job, 50, f"mentions the customer {payer.get('name')}")
    ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
    if not ranked:
        return {"job_id": None, "reason": "No job name, code, address or customer found.", "candidates": []}
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
    source_line = {"email": "Email", "sms": "Text message", "paste": "Pasted", "notes": "Notes import", "upload": "Upload", "payer_page": "Customer page"}.get(item["source"], item["source"])
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


def undo_item(state: dict[str, Any], item_id: str, actor: str) -> dict[str, Any]:
    """Take a filed item back off the job record, unless a packet already uses it."""
    item = _item(state, item_id)
    if item["status"] == "dismissed":
        item["status"] = "new"
        return item
    if item["status"] != "filed":
        raise domain.GateError("Nothing to undo.")
    job = domain._open_job(state, item["filed_job_id"])
    rids = set(item.get("record_ids", []))
    packets = [p for inv in job["invoices"] for p in (inv.get("packet") or [])] + (job.get("packet") or []) + (job.get("closeout_packet") or [])
    if any(a.get("record_id") in rids and not a.get("auto") for p in packets for a in p.get("attachments", [])):
        raise domain.GateError("A pay app's paperwork uses this record. Tap Remove on it there first.")
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
        # A captured reply also changed the status line; put it back.
        job["external_status"] = item.pop("prev_external_status", None)
        if (job.get("last_reply") or {}).get("inbox_id") == item_id:
            job.pop("last_reply", None)
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
