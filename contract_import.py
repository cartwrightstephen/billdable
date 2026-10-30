"""Read a contract into the New job and Add client forms (Stephen's Oct 29 review, G4 and S12).

A contract comes as a Word file (.docx), a PDF with text in it, a .txt, or pasted text. The text
is read with the standard library only: zipfile and xml.etree for .docx; zlib to inflate a PDF's
compressed streams, then the strings its text operators (Tj, TJ, ' and ") draw. Plain rules, like
the rest of importer.py, then look for the contract sum, retainage, the contract or PO number,
the project's name and address, the client, the start and completion dates, the day pay apps
are due, the payment terms and a schedule of values (through sov.parse_rows).

Nothing is saved here: the answer is what the form fills in, for the person to check before
Save. A scanned PDF has no text to read, so it says so; a broken file never raises.
"""

from __future__ import annotations

import io
import re
import zipfile
import time
import zlib
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree

import sov
from domain.validate import check_field, normalize_phone, split_address

NO_TEXT = "Couldn't read text from this file; fill the fields in by hand."
NOT_OPENED = "Couldn't open this file. Save it again as a Word file or a PDF, or paste the text."
WRONG_TYPE = "Choose a Word file (.docx), a PDF or a .txt file, or paste the text."
MAX_TEXT = 400_000          # characters kept of a contract's text
MAX_PART = 30 * 1024 * 1024  # the most a .docx part or a PDF stream may inflate to
MIN_LETTERS = 40            # fewer letters than this is no text (a scan's stray marks)


def read(name: str, data: bytes, company_name: str = "") -> dict[str, Any]:
    """{ok, fields, found, sov, notes} from a contract file, or {ok: False, error}."""
    ext = ("." + str(name or "").rsplit(".", 1)[-1].lower()) if "." in str(name or "") else ".txt"
    try:
        if ext == ".docx":
            text = docx_text(data)
        elif ext == ".pdf":
            text = pdf_text(data)
        elif ext in {".txt", ".text", ".md"}:
            text = data.decode("utf-8", "replace")
        else:
            return {"ok": False, "error": WRONG_TYPE}
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, ValueError):
        return {"ok": False, "error": NOT_OPENED if ext == ".docx" else NO_TEXT}
    except Exception:  # a file this reader doesn't understand is never a crash
        return {"ok": False, "error": NO_TEXT}
    text = (text or "")[:MAX_TEXT]
    if len(re.findall(r"[A-Za-z]", text)) < MIN_LETTERS:
        return {"ok": False, "error": NO_TEXT}
    return {"ok": True, **suggest(text, company_name)}


# ---------- Word ----------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(data: bytes) -> str:
    """The document's paragraphs, one per line; a table's rows as tab-separated cells."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > MAX_PART:
            raise ValueError("too big")
        xml = z.read(info)
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise ValueError("entities")  # a Word file never has them; refusing them keeps the parser safe
    body = ElementTree.fromstring(xml).find(W + "body")
    lines: list[str] = []
    for el in list(body) if body is not None else []:
        if el.tag == W + "p":
            lines.append(_para(el))
        elif el.tag == W + "tbl":
            for tr in el.iter(W + "tr"):
                lines.append("\t".join(" ".join(_para(p) for p in tc.iter(W + "p")).strip() for tc in tr.findall(W + "tc")))
    return "\n".join(lines)


def _para(p: ElementTree.Element) -> str:
    out = []
    for el in p.iter():
        if el.tag == W + "t" and el.text:
            out.append(el.text)
        elif el.tag == W + "tab":
            out.append("\t")
        elif el.tag in (W + "br", W + "cr"):
            out.append("\n")
    return "".join(out)


# ---------- PDF ----------

_STREAM = re.compile(rb"stream\r?\n")
_SKIP = re.compile(rb"/Subtype\s*/Image|/Type\s*/(ObjStm|XRef|XObject)|/Length1")
MAX_INFLATED = 30 * 1024 * 1024  # the most all of a PDF's streams may inflate to, together
MAX_HEAD = 4096                  # how far back a stream's dictionary is looked for
READ_SECONDS = 1.5               # a PDF is read for this long at most; what was read by then is kept


def pdf_text(data: bytes, seconds: float = READ_SECONDS) -> str:
    """The text a PDF draws, from its content streams (inflated when FlateDecode). Fonts with
    their own encodings come out as nonsense, which then reads as no text. Every step moves
    forward through the file, and the reading stops at MAX_TEXT characters, MAX_INFLATED bytes
    inflated or `seconds`, so a small crafted file can't keep the server busy."""
    if not data.startswith(b"%PDF"):
        raise ValueError("not a PDF")
    deadline = time.monotonic() + seconds
    parts: list[str] = []
    kept = 0
    budget = MAX_INFLATED
    pos = 0
    last_obj, obj_from = -1, 0
    while kept < MAX_TEXT and budget > 0 and time.monotonic() < deadline:
        m = _STREAM.search(data, pos)
        if not m:
            break
        end = data.find(b"endstream", m.end())
        if end < 0:
            break
        pos = end + 9
        while True:  # the last "obj" before this stream, found moving forward only
            k = data.find(b"obj", obj_from, m.start())
            if k < 0:
                break
            last_obj, obj_from = k, k + 3
        obj_from = max(obj_from, m.start())
        head = data[max(0, last_obj, m.start() - MAX_HEAD):m.start()]
        if _SKIP.search(head):
            continue  # pictures, packed objects and embedded fonts hold no page text
        raw = data[m.end():end].rstrip(b"\r\n")
        if b"/FlateDecode" in head or b"/Fl " in head or b"/Fl]" in head:
            raw = _inflate(raw, min(MAX_PART, budget))
            if raw is None:
                continue
            budget -= len(raw)
        elif b"/Filter" in head:
            continue  # other filters (pictures, mostly) are not read
        if b"T" in raw:
            text = _content_text(raw, deadline, MAX_TEXT - kept)
            if text.strip():
                parts.append(text)
                kept += len(text)
    return "\n".join(parts)


def _inflate(raw: bytes, limit: int = MAX_PART) -> bytes | None:
    try:
        d = zlib.decompressobj()
        out = d.decompress(raw, max(1, limit))
        return out
    except zlib.error:
        return None


_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


_OCTAL = re.compile(rb"[0-7]{1,3}")
_NAME = re.compile(rb"/[^\s/\[\]()<>{}%]*")
_NUMBER = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)")
_WORD = re.compile(rb"[^\s/\[\]()<>{}%]+")
_NOT_HEX = re.compile(rb"[^0-9A-Fa-f]")
_PLAIN = re.compile(rb"[^()\\]+")
_LINE_END = re.compile(rb"[\r\n]")


def _tokens(src: bytes, deadline: float | None = None):
    """The tokens of a content stream: ("s", bytes) strings, ("n", float) numbers, "[" and "]",
    and ("o", operator). Each pattern is matched in place (pattern.match(src, i)), never on a
    copy of the rest of the stream, so reading is linear in the stream's length; past
    `deadline` the tokens simply end."""
    i, n, steps = 0, len(src), 0
    while i < n:
        steps += 1
        if deadline is not None and not steps % 1024 and time.monotonic() >= deadline:
            return
        c = src[i:i + 1]
        if c in b" \t\r\n\f\x00":
            i += 1
        elif c == b"%":
            m = _LINE_END.search(src, i)
            i = m.start() if m else n
        elif c == b"(":
            depth, i, buf = 1, i + 1, bytearray()
            while i < n and depth:
                steps += 1
                if deadline is not None and not steps % 1024 and time.monotonic() >= deadline:
                    return
                run = _PLAIN.match(src, i)
                if run:  # letters with no brackets or escapes, taken in one step
                    if len(buf) < MAX_TEXT:
                        buf += run.group(0)
                    i = run.end()
                    continue
                ch = src[i:i + 1]
                if ch == b"\\":
                    nxt = src[i + 1:i + 2]
                    if nxt in _ESCAPES:
                        buf += _ESCAPES[nxt]
                        i += 2
                    elif nxt and nxt in b"01234567":
                        octal = _OCTAL.match(src, i + 1, i + 4).group(0)
                        buf.append(int(octal, 8) & 0xFF)
                        i += 1 + len(octal)
                    else:
                        i += 2  # a line continuation, or an escape that means the character
                    continue
                if ch == b"(":
                    depth += 1
                elif ch == b")":
                    depth -= 1
                    if not depth:
                        i += 1
                        break
                buf += ch
                i += 1
            yield ("s", bytes(buf))
        elif c == b"<" and src[i + 1:i + 2] != b"<":
            end = src.find(b">", i)
            end = n if end < 0 else end
            hexed = _NOT_HEX.sub(b"", src[i + 1:end])
            yield ("s", bytes.fromhex((hexed + b"0" * (len(hexed) % 2)).decode()))
            i = end + 1
        elif c in b"[]":
            yield (c.decode(), None)
            i += 1
        elif c == b"/":
            m = _NAME.match(src, i)
            yield ("name", m.group(0))
            i += len(m.group(0))
        elif c in b"<>{}":
            i += 2 if src[i:i + 2] in (b"<<", b">>") else 1
        else:
            m = _NUMBER.match(src, i)
            if m:
                yield ("n", float(m.group(0)))
                i += len(m.group(0))
                continue
            m = _WORD.match(src, i)
            word = m.group(0) if m else src[i:i + 1]
            yield ("o", word)
            i += len(word)


def _decode(b: bytes) -> str:
    if len(b) >= 2 and b.count(0) * 3 >= len(b):
        try:
            return b.decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    return b.decode("latin-1")


def _content_text(src: bytes, deadline: float | None = None, limit: int = MAX_TEXT) -> str:
    out: list[str] = []
    operands: list[Any] = []
    array: list[Any] | None = None
    for kind, value in _tokens(src, deadline):
        if len(out) >= limit:
            break
        if kind == "[":
            array = []
        elif kind == "]":
            operands.append(("a", array or []))
            array = None
        elif kind in ("s", "n", "name"):
            (array if array is not None else operands).append((kind, value))
        else:
            op = value
            if op == b"Tj" and operands and operands[-1][0] == "s":
                out.append(_decode(operands[-1][1]))
            elif op in (b"'", b'"') and operands and operands[-1][0] == "s":
                out.append("\n" + _decode(operands[-1][1]))
            elif op == b"TJ" and operands and operands[-1][0] == "a":
                for k, v in operands[-1][1]:
                    if k == "s":
                        out.append(_decode(v))
                    elif k == "n" and v < -180:
                        out.append(" ")  # a wide gap between letters is a space
            elif op in (b"Td", b"TD"):
                ty = operands[-1][1] if operands and operands[-1][0] == "n" else 0
                out.append("\n" if ty else " ")
            elif op in (b"T*", b"Tm", b"ET"):
                out.append("\n")
            operands = []
    text = "".join(out)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", text))


# ---------- what the contract says ----------

MONTHS = {m: n for n, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
_DATE = (r"(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
         r"|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})")
_MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)"


def _to_date(text: str) -> str:
    t = text.strip().lower().replace(",", " ")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
            return date.fromisoformat(t).isoformat()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t)
        if m:
            year = int(m.group(3))
            return date(year + 2000 if year < 100 else year, int(m.group(1)), int(m.group(2))).isoformat()
        m = re.fullmatch(r"([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})", re.sub(r"\s+", " ", t))
        if m:
            month = next((n for name, n in MONTHS.items() if name.startswith(m.group(1)[:3])), 0)
            return date(int(m.group(3)), month, int(m.group(2))).isoformat()
    except ValueError:
        return ""
    return ""


def _cents(text: str) -> int:
    return int(round(float(text.replace(",", "")) * 100))


def _first(pattern: str, text: str, flags: int = re.I) -> re.Match | None:
    return re.search(pattern, text, flags)


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" ,.;:\"'“”()")
    # "This Agreement is made between ABC Builders, LLC" is ABC Builders, LLC; "and XYZ" is XYZ.
    name = re.split(r"(?i)\bbetween\s+", name)[-1]
    name = re.sub(r"(?i)^(?:and|by)\s+", "", name).strip(" ,.;:")
    name = re.sub(r"(?i)^(the|a|an)\s+", "", name) if re.match(r"(?i)^the\s+(contractor|owner|subcontractor)\b", name) else name
    return name[:120]


def _same_company(a: str, b: str) -> bool:
    from domain import customer_key
    return bool(a and b) and customer_key(a) == customer_key(b)


# A party's role word as contracts write it -> the client type it means (J2).
ROLE_TYPES = [("general contractor", "gc"), ("contractor", "gc"), ("subcontractor", "sub"), ("owner", "owner"),
              ("developer", "owner"), ("homeowner", "homeowner"), ("county", "public"), ("city of", "public"),
              ("authority", "public"), ("department", "public"), ("school district", "public")]


def _parties(text: str) -> list[tuple[str, str]]:
    """(name, role word) for each party the contract names: "Contractor: ABC Builders",
    "ABC Builders (the "Contractor")", "between ABC Builders ... and Hartline Tile"."""
    found: list[tuple[str, str]] = []
    for m in re.finditer(r"(?im)^\s*(?:the\s+)?(general contractor|contractor|subcontractor|owner|developer|client|customer)\s*(?:name)?\s*[:\-]\s*(?:the\s+)?([^\n\t]{2,120})$", text):
        found.append((_clean_name(m.group(2)), m.group(1).lower()))
    for m in re.finditer(r"([A-Z][A-Za-z0-9&.,' -]{2,80}?)\s*,?\s*\(\s*(?:hereinafter\s+(?:called\s+|referred to as\s+)?)?(?:the\s+)?[\"“']?(General Contractor|Contractor|Subcontractor|Owner|Developer)[\"”']?\s*\)", text):
        found.append((_clean_name(m.group(1)), m.group(2).lower()))
    return [(n, r) for n, r in found if n and re.search(r"[A-Za-z]", n)]


def _client(text: str, company_name: str) -> tuple[str, str]:
    """The client's name and type: the party opposite the user's own company."""
    parties = [(n, r) for n, r in _parties(text) if not _same_company(n, company_name)]
    # The user is the subcontractor on most of these: the client is the contractor, else the owner.
    order = ["general contractor", "contractor", "owner", "developer", "client", "customer", "subcontractor"]
    parties.sort(key=lambda p: order.index(p[1]) if p[1] in order else len(order))
    if not parties:
        return "", ""
    name, role = parties[0]
    lower = (role + " " + name).lower()
    kind = next((t for word, t in ROLE_TYPES if word in lower), "")
    if role in ("owner", "client", "customer") and re.search(r"(?i)\b(county|city of|state of|authority|department|district)\b", name):
        kind = "public"
    return name, kind


def _address(text: str) -> dict[str, str] | None:
    m = _first(r"(?im)^\s*(?:project|job|site|property|work)?\s*(?:address|location)\s*[:\-]\s*(.+)$", text)
    if m:
        line = m.group(1).strip()
        tail = text[m.end():m.end() + 120].strip().split("\n")[0].strip()
        for candidate in (line, f"{line}, {tail}" if tail else ""):
            parts = split_address(candidate) if candidate else None
            if parts and parts.get("zip"):
                return parts
    m = _first(r"\b\d{1,6}\s+[A-Za-z0-9 .'#-]{2,60},\s*[A-Za-z .'-]{2,40},?\s+[A-Z]{2}\.?\s+\d{5}(?:-\d{4})?\b", text, 0)
    return split_address(m.group(0)) if m else None


def _sov(text: str) -> dict[str, Any] | None:
    """A schedule of values under its heading, read with the SOV paste reader."""
    m = _first(r"(?im)^.*schedule of values.*$", text)
    if not m:
        return None
    block = []
    for line in text[m.end():].split("\n")[:80]:
        if block and not line.strip():
            break
        if line.strip():
            block.append(line)
    if not block:
        return None
    parsed = sov.parse_rows("\n".join(block))
    lines = [l for l in parsed.get("lines", []) if not l.get("needs_price")]
    if not lines:
        return None
    total = sum(int(round(float(l["quantity"]) * float(l["rate"]) * 100)) for l in lines)
    return {"lines": lines, "total_cents": total}


def suggest(text: str, company_name: str = "") -> dict[str, Any]:
    """What the contract says, as the forms' fields: {fields, found, sov, notes}."""
    flat = re.sub(r"[ \t]+", " ", text)
    fields: dict[str, Any] = {}

    m = _first(r"(?:contract\s+sum|contract\s+price|contract\s+amount|subcontract\s+(?:sum|amount|price)|total\s+contract(?:\s+amount|\s+price)?)[^$\n]{0,160}?" + _MONEY, flat)
    if m:
        fields["contract_cents"] = _cents(m.group(1))

    m = _first(r"\b(?:retainage|retention|retained)\b[^%\n]{0,120}?(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)", flat)
    if m and float(m.group(1)).is_integer() and 0 <= float(m.group(1)) <= 20:
        fields["retainage_pct"] = int(float(m.group(1)))

    m = _first(r"\b(?:sub)?(?:contract|agreement|purchase\s+order|P\.?O\.?)\s*(?:no\.?|number|#)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9./-]{1,39})", flat)
    if m and re.search(r"\d", m.group(1)) and not check_field("contract_number", m.group(1).rstrip(".")):
        fields["contract_number"] = m.group(1).rstrip(".")

    m = _first(r"(?im)^\s*(?:project|job)(?:\s+name)?\s*[:\-]\s*(.+)$", text)
    if m:
        name = re.split(r"\s{2,}|\t", m.group(1).strip())[0].strip(" .,;")
        if name and not split_address(name) and not check_field("name", name):
            fields["name"] = name[:120]

    parts = _address(text)
    if parts:
        fields.update({k: parts[k] for k in ("street", "city", "state", "zip")})

    client, kind = _client(text, company_name)
    if client:
        fields["customer"] = client
        if kind:
            fields["client_type"] = kind

    m = _first(r"(?:commencement|start(?:ing)?|begin(?:ning)?)(?:\s+of\s+(?:the\s+)?work)?(?:\s+date)?[^.\n]{0,60}?(" + _DATE + ")", flat)
    if m and _to_date(m.group(1)):
        fields["started_on"] = _to_date(m.group(1))
    m = _first(r"(?:substantial\s+completion|completion\s+date|complete(?:d)?\s+(?:the\s+work\s+)?(?:by|no later than|on or before))[^.\n]{0,60}?(" + _DATE + ")", flat)
    if m and _to_date(m.group(1)):
        fields["completion_on"] = _to_date(m.group(1))

    m = (_first(r"(?:applications?\s+for\s+payment|pay(?:ment)?\s+app(?:lication)?s?|invoices?|requisitions?)[^.\n]{0,120}?(?:by|before|on or before|no later than|on)\s+the\s+(\d{1,2})(?:st|nd|rd|th)", flat)
         or _first(r"(?:applications?\s+for\s+payment|pay(?:ment)?\s+app(?:lication)?s?|invoices?|requisitions?)[^.\n]{0,120}?(\d{1,2})(?:st|nd|rd|th)\s+(?:day\s+)?of\s+(?:each|the|every)\s+month", flat))
    if m and 1 <= int(m.group(1)) <= 31:
        fields["payapp_due_day"] = int(m.group(1))

    m = _first(r"\bnet\s*(\d{1,3})\b", flat) or _first(r"(?:pa(?:y|id)|payment)[^.\n]{0,80}?within\s+(\d{1,3})\s+(?:calendar\s+)?days", flat)
    if m and 1 <= int(m.group(1)) <= 365:
        fields["payment_terms_days"] = int(m.group(1))

    m = _first(r"(?:invoices?|pay(?:ment)?\s+app(?:lication)?s?|billing|accounts\s+payable|a/?p)[^@\n]{0,80}?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", flat)
    if m:
        fields["ap_email"] = m.group(1)
    m = _first(r"(?:phone|tel(?:ephone)?)\s*[:.]?\s*(\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4})", flat)
    if m and normalize_phone(m.group(1)):
        fields["phone"] = normalize_phone(m.group(1))

    schedule = _sov(text)
    notes = []
    if fields.get("completion_on"):
        notes.append(f"Completion date in the contract: {_words_date(fields['completion_on'])}.")
    if schedule:
        n = len(schedule["lines"])
        notes.append(f"A schedule of values with {n} line{'s' if n != 1 else ''}, {_dollars(schedule['total_cents'])} in all.")
    return {"fields": fields, "found": sorted(fields), "sov": schedule, "notes": notes}


def _words_date(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"
