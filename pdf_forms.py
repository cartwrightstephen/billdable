"""Reading a client's own blank PDF form and writing values onto it, with the standard library only.

Used by gcforms.py ("use the GC's own forms"). Two jobs:

- read(): the form's pages (size and rotation) and, for a fillable form (an AcroForm), its fields:
  each one's full name, type, the page and box of each of its widgets, and for a checkbox the
  name of its "on" state.
- fill(): an incremental update appended to the original bytes. A fillable field gets its value
  (/V) and a plain appearance stream, so a viewer that ignores /NeedAppearances still shows it, and
  the form's /NeedAppearances is set so one that honours it draws its own. Values placed by
  position (for a form with no fields) are drawn by a content stream added to the page. The
  original bytes are never changed, so the client's form is byte for byte what they sent, with
  the values after it.

The reader handles classic cross-reference tables and cross-reference streams (with PNG
predictors), hybrid files, object streams, FlateDecode, and files saved several times
(incremental updates, /Prev chains). A file whose cross-reference is damaged is read by scanning
for its objects. Encrypted files are refused with words a person can act on; so are dynamic XFA
forms, whose pages are drawn by a program the reader doesn't run.

Uploads are untrusted, so every part is bounded, like contract_import's readers: the file's size,
the number of objects, how deep arrays and dictionaries nest, how far streams inflate in all, how
many fields and pages are read, how many cross-reference sections are followed, and a deadline.
Past any of them the file is refused (PdfError), never read on without end.
"""

from __future__ import annotations

import re
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

MAX_BYTES = 15 * 1024 * 1024       # as importer.MAX_FILE_BYTES
MAX_INFLATED = 30 * 1024 * 1024    # all the streams read from one file, inflated, together
MAX_STREAM = 12 * 1024 * 1024      # one stream, inflated
MAX_DEPTH = 64                     # arrays and dictionaries inside each other
MAX_OBJECTS = 200_000              # objects a file may number
MAX_SECTIONS = 128                 # cross-reference sections followed through /Prev
MAX_FIELDS = 3000                  # form fields read
MAX_NODES = 20_000                 # field-tree and page-tree nodes visited
MAX_PAGES = 500
MAX_ITEMS = 500_000                # items parsed in one object (an array of numbers can be long)
SECONDS = 8.0                      # the most one read may take

ENCRYPTED = ("This PDF is locked (encrypted), so its fields can't be read or filled. Ask the client for an "
             "unlocked copy, or open it and print it to a new PDF, and upload that.")
DYNAMIC_XFA = ("This form is a dynamic XFA form (made in Adobe LiveCycle Designer): its pages are drawn by a "
               "program only Adobe Reader runs, so Duracash can't fill it. Ask the client for a plain PDF of the form.")
NOT_PDF = "That file isn't a PDF. Upload the client's blank form as a PDF."
DAMAGED = "This PDF couldn't be read; it may be damaged. Open it, print it to a new PDF and upload that."


class PdfError(ValueError):
    """A form that can't be read or filled; the message is for the person who uploaded it."""


class Name(str):
    """A PDF name (/Foo), kept apart from text strings."""


@dataclass(frozen=True)
class Ref:
    num: int
    gen: int = 0


@dataclass
class Stream:
    dict: dict
    raw: bytes


# ---------- lexing and parsing ----------

WS = b" \t\n\r\x0c\x00"
DELIM = b"()<>[]{}/%"
_NUMBER = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
_REGULAR_END = re.compile(rb"[ \t\n\r\x0c\x00()<>\[\]{}/%]")
_REF_TAIL = re.compile(rb"\s+(\d+)\s+R(?=[\s()<>\[\]{}/%]|$)")


class _Budget:
    """What one read may still spend: inflated bytes, parsed items and time."""

    def __init__(self) -> None:
        self.inflated = MAX_INFLATED
        self.deadline = time.monotonic() + SECONDS

    def tick(self) -> None:
        if time.monotonic() > self.deadline:
            raise PdfError("This PDF took too long to read. Print it to a new PDF and upload that.")


def _skip_ws(data: bytes, pos: int) -> int:
    n = len(data)
    while pos < n:
        c = data[pos]
        if c in WS:
            pos += 1
        elif c == 0x25:  # % comment to the end of the line
            while pos < n and data[pos] not in b"\r\n":
                pos += 1
        else:
            break
    return pos


class _Parser:
    def __init__(self, data: bytes, budget: _Budget) -> None:
        self.data = data
        self.budget = budget
        self.items = 0

    def value(self, pos: int, depth: int = 0) -> tuple[Any, int]:
        """The object at pos and where it ends. n g R is read as a Ref."""
        if depth > MAX_DEPTH:
            raise PdfError(DAMAGED)
        self.items += 1
        if self.items > MAX_ITEMS:
            raise PdfError(DAMAGED)
        data = self.data
        pos = _skip_ws(data, pos)
        if pos >= len(data):
            raise PdfError(DAMAGED)
        c = data[pos:pos + 1]
        if c == b"/":
            return self._name(pos)
        if c == b"(":
            return self._literal(pos)
        if c == b"<":
            if data[pos:pos + 2] == b"<<":
                return self._dict(pos + 2, depth)
            return self._hex(pos)
        if c == b"[":
            out: list = []
            pos += 1
            while True:
                pos = _skip_ws(data, pos)
                if pos >= len(data):
                    raise PdfError(DAMAGED)
                if data[pos:pos + 1] == b"]":
                    return out, pos + 1
                item, pos = self.value(pos, depth + 1)
                out.append(item)
        m = _NUMBER.match(data, pos)
        if m:
            text = m.group(0)
            end = m.end()
            if b"." not in text:
                num = int(text)
                # n g R
                m2 = _REF_TAIL.match(data, end)
                if m2 and num >= 0:
                    return Ref(num, int(m2.group(1))), m2.end()
                return num, end
            try:
                return float(text), end
            except ValueError:
                raise PdfError(DAMAGED)
        m = _REGULAR_END.search(data, pos)
        end = m.start() if m else len(data)
        word = data[pos:end]
        if word == b"true":
            return True, end
        if word == b"false":
            return False, end
        if word == b"null":
            return None, end
        if not word:
            raise PdfError(DAMAGED)
        return _Keyword(word.decode("latin-1")), end

    def _name(self, pos: int) -> tuple[Name, int]:
        m = _REGULAR_END.search(self.data, pos + 1)
        end = m.start() if m else len(self.data)
        raw = self.data[pos + 1:end]
        raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda x: bytes([int(x.group(1), 16)]), raw)
        return Name(raw.decode("latin-1")), end

    def _dict(self, pos: int, depth: int) -> tuple[dict, int]:
        out: dict = {}
        data = self.data
        while True:
            pos = _skip_ws(data, pos)
            if pos >= len(data):
                raise PdfError(DAMAGED)
            if data[pos:pos + 2] == b">>":
                return out, pos + 2
            key, pos = self.value(pos, depth + 1)
            if not isinstance(key, Name):
                raise PdfError(DAMAGED)
            val, pos = self.value(pos, depth + 1)
            out[str(key)] = val

    def _literal(self, pos: int) -> tuple[bytes, int]:
        data, n = self.data, len(self.data)
        out = bytearray()
        level, i = 1, pos + 1
        while i < n:
            c = data[i]
            if c == 0x5C:  # backslash
                i += 1
                if i >= n:
                    break
                e = data[i]
                simple = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12, 0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}
                if e in simple:
                    out.append(simple[e])
                    i += 1
                elif 0x30 <= e <= 0x37:
                    digits = 1
                    while digits < 3 and i + digits < n and 0x30 <= data[i + digits] <= 0x37:
                        digits += 1
                    out.append(int(data[i:i + digits], 8) & 0xFF)
                    i += digits
                elif e == 0x0D:
                    i += 2 if data[i + 1:i + 2] == b"\n" else 1
                elif e == 0x0A:
                    i += 1
                else:
                    out.append(e)
                    i += 1
                continue
            if c == 0x28:
                level += 1
            elif c == 0x29:
                level -= 1
                if level == 0:
                    return bytes(out), i + 1
            out.append(c)
            i += 1
        raise PdfError(DAMAGED)

    def _hex(self, pos: int) -> tuple[bytes, int]:
        end = self.data.find(b">", pos)
        if end < 0:
            raise PdfError(DAMAGED)
        digits = re.sub(rb"[^0-9A-Fa-f]", b"", self.data[pos + 1:end])
        if len(digits) % 2:
            digits += b"0"
        return bytes.fromhex(digits.decode("ascii")), end + 1


class _Keyword(str):
    """A bare word (obj, endobj, stream, R...) met where an object was expected."""


# ---------- decoding streams ----------

def _inflate(raw: bytes, budget: _Budget) -> bytes:
    limit = min(MAX_STREAM, budget.inflated)
    d = zlib.decompressobj()
    try:
        out = d.decompress(raw, limit + 1)
    except zlib.error:
        # A stream cut short or with junk after it: what came out before the damage, if anything.
        try:
            d = zlib.decompressobj()
            out = b""
            for k in range(0, len(raw), 1024):
                out += d.decompress(raw[k:k + 1024], limit + 1 - len(out))
        except zlib.error:
            pass
        if not out:
            raise PdfError(DAMAGED)
    if len(out) > limit:
        raise PdfError("This PDF is too large to read once its parts are unpacked. Print it to a new PDF and upload that.")
    budget.inflated -= len(out)
    return out


def _unpredict(data: bytes, parms: dict) -> bytes:
    predictor = parms.get("Predictor", 1) if isinstance(parms, dict) else 1
    if not isinstance(predictor, int) or predictor < 10:
        if predictor in (1, None):
            return data
        raise PdfError(DAMAGED)
    colors = parms.get("Colors", 1)
    bpc = parms.get("BitsPerComponent", 8)
    columns = parms.get("Columns", 1)
    if not all(isinstance(v, int) and v > 0 for v in (colors, bpc, columns)) or columns > 100_000:
        raise PdfError(DAMAGED)
    bpp = max(1, colors * bpc // 8)
    width = (colors * bpc * columns + 7) // 8
    out = bytearray()
    prev = bytearray(width)
    i = 0
    while i + 1 + width <= len(data):
        kind = data[i]
        row = bytearray(data[i + 1:i + 1 + width])
        i += 1 + width
        for x in range(width):
            left = row[x - bpp] if x >= bpp else 0
            up = prev[x]
            if kind == 1:
                row[x] = (row[x] + left) & 0xFF
            elif kind == 2:
                row[x] = (row[x] + up) & 0xFF
            elif kind == 3:
                row[x] = (row[x] + ((left + up) >> 1)) & 0xFF
            elif kind == 4:
                ul = prev[x - bpp] if x >= bpp else 0
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                row[x] = (row[x] + (left if pa <= pb and pa <= pc else up if pb <= pc else ul)) & 0xFF
        out += row
        prev = row
    return bytes(out)


# ---------- the document ----------

@dataclass
class Widget:
    ref: Ref | None
    rect: list[float]
    page: int          # 0-based, -1 when no page lists it
    on_state: str = "" # a checkbox or radio button's "on" appearance name


@dataclass
class Field:
    name: str
    ftype: str          # Tx, Btn, Ch, Sig
    flags: int
    ref: Ref | None
    widgets: list[Widget] = field(default_factory=list)
    da: str = ""
    q: int = 0
    max_len: int = 0
    options: list[str] = field(default_factory=list)
    value: str = ""

    @property
    def kind(self) -> str:
        """text, checkbox, radio, button, choice or signature."""
        if self.ftype == "Tx":
            return "text"
        if self.ftype == "Ch":
            return "choice"
        if self.ftype == "Sig":
            return "signature"
        if self.flags & (1 << 16):
            return "button"
        if self.flags & (1 << 15):
            return "radio"
        return "checkbox"


@dataclass
class Page:
    ref: Ref
    dict: dict
    box: list[float]      # what shows: the crop box within the media box, x0 y0 x1 y1
    rotate: int
    resources: dict

    @property
    def size(self) -> tuple[float, float]:
        """Width and height as the page shows, in points."""
        w, h = self.box[2] - self.box[0], self.box[3] - self.box[1]
        return (h, w) if self.rotate in (90, 270) else (w, h)


class Pdf:
    """A PDF read for its pages and form fields. Objects are read when asked for."""

    def __init__(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise PdfError(NOT_PDF)
        if len(data) > MAX_BYTES:
            raise PdfError(f"That PDF is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        head = data.find(b"%PDF-", 0, 1024)
        if head < 0:
            raise PdfError(NOT_PDF)
        self.data = bytes(data)
        self.base = head    # offsets in a file with junk before %PDF- count from it
        self.budget = _Budget()
        self.parser = _Parser(self.data, self.budget)
        self.xref: dict[int, tuple] = {}
        self.trailer: dict = {}
        self.last_xref = -1
        self.xref_stream = False
        self.repaired = False
        self._cache: dict[int, Any] = {}
        self._objstm: dict[int, tuple[bytes, list[tuple[int, int]], int]] = {}
        try:
            self._read_xref()
            if self.trailer.get("Encrypt") is not None:
                raise PdfError(ENCRYPTED)
            if not isinstance(self._resolve(self.trailer.get("Root")), dict):
                raise PdfError(DAMAGED)
        except PdfError as exc:
            if str(exc) == ENCRYPTED or " too " in f" {exc} ":
                raise
            self._repair()
        if "Encrypt" in self.trailer and self.trailer["Encrypt"] is not None:
            raise PdfError(ENCRYPTED)
        self.root = self._resolve(self.trailer.get("Root"))
        if not isinstance(self.root, dict):
            raise PdfError(DAMAGED)

    # -- cross-reference --

    def _read_xref(self) -> None:
        tail = self.data[-4096:]
        k = tail.rfind(b"startxref")
        if k < 0:
            raise PdfError(DAMAGED)
        m = re.match(rb"startxref\s+(\d+)", tail[k:])
        if not m:
            raise PdfError(DAMAGED)
        offset = int(m.group(1))
        self.last_xref = offset
        seen: set[int] = set()
        first = True
        while offset is not None and offset >= 0:
            self.budget.tick()
            if offset in seen or len(seen) >= MAX_SECTIONS:
                break
            seen.add(offset)
            trailer, is_stream = self._xref_section(offset)
            if first:
                self.trailer = dict(trailer)
                self.xref_stream = is_stream
                first = False
            else:
                for k2, v in trailer.items():
                    self.trailer.setdefault(k2, v)
            prev = trailer.get("Prev")
            offset = prev if isinstance(prev, int) else None
        if len(self.xref) > MAX_OBJECTS:
            raise PdfError("This PDF has too many parts to read.")

    def _at(self, offset: int) -> int:
        """A recorded offset as a position in the data: from the start, else from %PDF-."""
        for pos in (offset, offset + self.base):
            if 0 <= pos < len(self.data) and (self.data.startswith(b"xref", pos) or re.match(rb"\d+\s+\d+\s+obj", self.data[pos:pos + 32])):
                return pos
        raise PdfError(DAMAGED)

    def _xref_section(self, offset: int) -> tuple[dict, bool]:
        pos = self._at(offset)
        data = self.data
        if data.startswith(b"xref", pos):
            pos += 4
            while True:
                pos = _skip_ws(data, pos)
                if data.startswith(b"trailer", pos):
                    trailer, _ = self.parser.value(pos + 7)
                    if not isinstance(trailer, dict):
                        raise PdfError(DAMAGED)
                    stm = trailer.get("XRefStm")
                    if isinstance(stm, int):
                        try:
                            self._xref_section(stm)   # a hybrid file's stream: objects the table doesn't list
                        except PdfError:
                            pass
                    return trailer, False
                m = re.compile(rb"(\d+)\s+(\d+)").match(data, pos)
                if not m:
                    raise PdfError(DAMAGED)
                start, count = int(m.group(1)), int(m.group(2))
                if count > MAX_OBJECTS or start > MAX_OBJECTS:
                    raise PdfError("This PDF has too many parts to read.")
                pos = m.end()
                for n in range(count):
                    pos = _skip_ws(data, pos)
                    e = re.compile(rb"(\d{1,10})\s+(\d{1,5})\s+([nf])").match(data, pos)
                    if not e:
                        raise PdfError(DAMAGED)
                    pos = e.end()
                    num = start + n
                    if num not in self.xref:
                        self.xref[num] = ("n", int(e.group(1)), int(e.group(2))) if e.group(3) == b"n" else ("f",)
                if count == 0:
                    continue
        obj, _ = self._object_at(pos)
        if not isinstance(obj, Stream) or obj.dict.get("Type") != "XRef":
            raise PdfError(DAMAGED)
        d = obj.dict
        body = self._decode(obj)
        widths = d.get("W")
        if not (isinstance(widths, list) and len(widths) == 3 and all(isinstance(w, int) and 0 <= w <= 8 for w in widths)):
            raise PdfError(DAMAGED)
        size = d.get("Size", 0)
        index = d.get("Index") or [0, size]
        if not isinstance(index, list) or len(index) % 2:
            raise PdfError(DAMAGED)
        row = sum(widths)
        if row == 0:
            raise PdfError(DAMAGED)
        k = 0
        for s in range(0, len(index), 2):
            start, count = index[s], index[s + 1]
            if not (isinstance(start, int) and isinstance(count, int)) or count > MAX_OBJECTS or start > MAX_OBJECTS:
                raise PdfError(DAMAGED)
            for n in range(count):
                if k + row > len(body):
                    break
                parts, at = [], k
                for w in widths:
                    parts.append(int.from_bytes(body[at:at + w], "big") if w else None)
                    at += w
                k += row
                kind = parts[0] if widths[0] else 1
                num = start + n
                if num in self.xref:
                    continue
                if kind == 1:
                    self.xref[num] = ("n", parts[1] or 0, parts[2] or 0)
                elif kind == 2:
                    self.xref[num] = ("c", parts[1] or 0, parts[2] or 0)
                elif kind == 0:
                    self.xref[num] = ("f",)
        return d, True

    def _repair(self) -> None:
        """Read a file whose cross-reference is damaged by finding each n g obj in it."""
        self.repaired = True
        self.xref, self._cache, self._objstm = {}, {}, {}
        count = 0
        for m in re.finditer(rb"(?<![0-9])(\d{1,7})\s+(\d{1,5})\s+obj\b", self.data):
            count += 1
            if count > MAX_OBJECTS:
                raise PdfError("This PDF has too many parts to read.")
            self.xref[int(m.group(1))] = ("n", m.start(), int(m.group(2)))
        trailer: dict = {}
        for m in re.finditer(rb"trailer\s*<<", self.data):
            try:
                t, _ = self.parser.value(m.start() + 7)
                if isinstance(t, dict):
                    trailer.update(t)
            except PdfError:
                continue
        # Objects in object streams, and the newest xref stream's trailer keys.
        for num, entry in list(self.xref.items()):
            self.budget.tick()
            try:
                obj = self.get(num)
            except PdfError:
                continue
            if isinstance(obj, Stream) and obj.dict.get("Type") == "ObjStm":
                try:
                    _, pairs, _ = self._object_stream(num)
                except PdfError:
                    continue
                for idx, (inner, _) in enumerate(pairs):
                    self.xref.setdefault(inner, ("c", num, idx))
            elif isinstance(obj, Stream) and obj.dict.get("Type") == "XRef":
                for k in ("Root", "Info", "ID", "Encrypt"):
                    if k in obj.dict:
                        trailer[k] = obj.dict[k]
        if not isinstance(self._resolve(trailer.get("Root")), dict):
            for num in list(self.xref):
                try:
                    obj = self.get(num)
                except PdfError:
                    continue
                if isinstance(obj, dict) and obj.get("Type") == "Catalog":
                    trailer["Root"] = Ref(num, self.xref[num][2] if self.xref[num][0] == "n" else 0)
                    break
        self.trailer = trailer
        self.last_xref = -1
        if not isinstance(self._resolve(trailer.get("Root")), dict):
            if "Encrypt" in trailer:
                raise PdfError(ENCRYPTED)
            raise PdfError(DAMAGED)

    # -- objects --

    def _object_at(self, pos: int) -> tuple[Any, int]:
        m = re.compile(rb"(\d+)\s+(\d+)\s+obj").match(self.data, pos)
        if not m:
            raise PdfError(DAMAGED)
        obj, end = self.parser.value(m.end())
        if isinstance(obj, dict):
            after = _skip_ws(self.data, end)
            if self.data.startswith(b"stream", after):
                start = after + 6
                if self.data[start:start + 2] == b"\r\n":
                    start += 2
                elif self.data[start:start + 1] in (b"\n", b"\r"):
                    start += 1
                length = obj.get("Length")
                if isinstance(length, Ref):
                    length = self._length(length)
                raw = None
                if isinstance(length, int) and 0 <= length <= len(self.data) - start:
                    tail = _skip_ws(self.data, start + length)
                    if self.data.startswith(b"endstream", tail):
                        raw = self.data[start:start + length]
                if raw is None:
                    stop = self.data.find(b"endstream", start)
                    if stop < 0:
                        raise PdfError(DAMAGED)
                    raw = self.data[start:stop].rstrip(b"\r\n")
                    if self.data[stop - 1:stop] == b"\n" and self.data[stop - 2:stop - 1] != b"\r":
                        pass
                return Stream(obj, raw), start + len(raw)
        return obj, end

    def _length(self, ref: Ref) -> int | None:
        entry = self.xref.get(ref.num)
        if not entry or entry[0] != "n":
            return None
        try:
            m = re.compile(rb"\d+\s+\d+\s+obj\s*(\d+)").match(self.data, self._at(entry[1]))
        except PdfError:
            return None
        return int(m.group(1)) if m else None

    def get(self, num: int) -> Any:
        if num in self._cache:
            return self._cache[num]
        self.budget.tick()
        entry = self.xref.get(num)
        obj = None
        if entry and entry[0] == "n":
            obj, _ = self._object_at(self._at(entry[1]))
        elif entry and entry[0] == "c":
            obj = self._from_object_stream(entry[1], entry[2])
        self._cache[num] = obj
        return obj

    def _object_stream(self, num: int) -> tuple[bytes, list[tuple[int, int]], int]:
        if num in self._objstm:
            return self._objstm[num]
        entry = self.xref.get(num)
        if not entry or entry[0] != "n":
            raise PdfError(DAMAGED)
        stm, _ = self._object_at(self._at(entry[1]))
        if not isinstance(stm, Stream):
            raise PdfError(DAMAGED)
        body = self._decode(stm)
        n, first = stm.dict.get("N"), stm.dict.get("First")
        if not (isinstance(n, int) and isinstance(first, int)) or n < 0 or n > MAX_OBJECTS or first > len(body):
            raise PdfError(DAMAGED)
        nums = re.findall(rb"\d+", body[:first])
        pairs = [(int(nums[2 * i]), int(nums[2 * i + 1])) for i in range(min(n, len(nums) // 2))]
        self._objstm[num] = (body, pairs, first)
        return self._objstm[num]

    def _from_object_stream(self, stm_num: int, index: int) -> Any:
        body, pairs, first = self._object_stream(stm_num)
        if not 0 <= index < len(pairs):
            return None
        parser = _Parser(body, self.budget)
        obj, _ = parser.value(first + pairs[index][1])
        return obj

    def _decode(self, stream: Stream) -> bytes:
        filters = stream.dict.get("Filter")
        parms = stream.dict.get("DecodeParms")
        filters = self._resolve(filters)
        parms = self._resolve(parms)
        if filters is None:
            return stream.raw
        if not isinstance(filters, list):
            filters, parms = [filters], [parms]
        if not isinstance(parms, list):
            parms = [parms] * len(filters)
        data = stream.raw
        for f, p in zip(filters, parms + [None] * (len(filters) - len(parms))):
            if f in ("FlateDecode", "Fl"):
                data = _inflate(data, self.budget)
                data = _unpredict(data, self._resolve(p) or {})
            else:
                raise PdfError(f"This PDF uses a compression ({f}) Duracash doesn't read. Print it to a new PDF and upload that.")
        return data

    def _resolve(self, obj: Any, depth: int = 0) -> Any:
        while isinstance(obj, Ref):
            if depth > MAX_DEPTH:
                raise PdfError(DAMAGED)
            obj = self.get(obj.num)
            depth += 1
        return obj

    resolve = _resolve

    @property
    def size(self) -> int:
        """The next object number free for a new object."""
        size = self.trailer.get("Size")
        top = max(self.xref, default=0) + 1
        return max(size if isinstance(size, int) else 0, top)

    # -- pages --

    def pages(self) -> list[Page]:
        out: list[Page] = []
        seen: set = set()

        def walk(ref: Any, inherited: dict, depth: int) -> None:
            if depth > MAX_DEPTH or len(seen) > MAX_NODES:
                raise PdfError(DAMAGED)
            node = self._resolve(ref)
            if not isinstance(node, dict):
                return
            key = ref if isinstance(ref, Ref) else id(node)
            if key in seen:
                return
            seen.add(key)
            here = dict(inherited)
            for k in ("MediaBox", "CropBox", "Resources", "Rotate"):
                if k in node:
                    here[k] = node[k]
            kids = self._resolve(node.get("Kids"))
            if node.get("Type") == "Pages" or (isinstance(kids, list) and node.get("Type") != "Page"):
                for kid in kids or []:
                    walk(kid, here, depth + 1)
                return
            if len(out) >= MAX_PAGES:
                raise PdfError(f"This PDF has more than {MAX_PAGES} pages; a client's form is a page or a few.")
            if not isinstance(ref, Ref):
                return
            media = self._box(here.get("MediaBox")) or [0, 0, 612, 792]
            crop = self._box(here.get("CropBox"))
            box = media
            if crop:
                box = [max(media[0], crop[0]), max(media[1], crop[1]), min(media[2], crop[2]), min(media[3], crop[3])]
                if box[2] <= box[0] or box[3] <= box[1]:
                    box = media
            rotate = self._resolve(here.get("Rotate")) or 0
            rotate = int(rotate) % 360 if isinstance(rotate, (int, float)) else 0
            if rotate not in (0, 90, 180, 270):
                rotate = 0
            res = self._resolve(here.get("Resources"))
            out.append(Page(ref, node, box, rotate, res if isinstance(res, dict) else {}))

        walk(self.root.get("Pages"), {}, 0)
        return out

    def _box(self, value: Any) -> list[float] | None:
        value = self._resolve(value)
        if isinstance(value, list) and len(value) == 4:
            nums = [self._resolve(v) for v in value]
            if all(isinstance(v, (int, float)) for v in nums):
                x0, y0, x1, y1 = (float(v) for v in nums)
                return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        return None

    # -- the form --

    def acroform(self) -> dict | None:
        form = self._resolve(self.root.get("AcroForm"))
        return form if isinstance(form, dict) else None

    def is_dynamic_xfa(self) -> bool:
        form = self.acroform()
        if not form or form.get("XFA") is None:
            return False
        fields = self._resolve(form.get("Fields"))
        return not fields or self.root.get("NeedsRendering") is True

    def fields(self, pages: list[Page] | None = None) -> list[Field]:
        form = self.acroform()
        if not form:
            return []
        pages = self.pages() if pages is None else pages
        page_of: dict[Ref, int] = {}
        for i, p in enumerate(pages):
            annots = self._resolve(p.dict.get("Annots"))
            for a in annots if isinstance(annots, list) else []:
                if isinstance(a, Ref):
                    page_of.setdefault(a, i)
        page_index = {p.ref: i for i, p in enumerate(pages)}
        default_da = _text(self._resolve(form.get("DA")))
        out: list[Field] = []
        seen: set = set()

        def widget(ref: Any, node: dict) -> Widget:
            rect = self._box(node.get("Rect")) or [0, 0, 0, 0]
            page = page_of.get(ref, -1) if isinstance(ref, Ref) else -1
            if page < 0 and isinstance(node.get("P"), Ref):
                page = page_index.get(node["P"], -1)
            on = ""
            ap = self._resolve(node.get("AP"))
            normal = self._resolve(ap.get("N")) if isinstance(ap, dict) else None
            if isinstance(normal, dict):
                on = next((k for k in normal if k != "Off"), "")
            return Widget(ref if isinstance(ref, Ref) else None, rect, page, on)

        def walk(ref: Any, parent_name: str, inherited: dict, depth: int) -> None:
            if depth > MAX_DEPTH or len(seen) > MAX_NODES:
                raise PdfError(DAMAGED)
            node = self._resolve(ref)
            if not isinstance(node, dict):
                return
            key = ref if isinstance(ref, Ref) else id(node)
            if key in seen:
                return
            seen.add(key)
            here = dict(inherited)
            for k in ("FT", "Ff", "DA", "Q", "MaxLen", "Opt"):
                if k in node:
                    here[k] = self._resolve(node[k])
            partial = _text(self._resolve(node.get("T"))) if "T" in node else None
            name = parent_name if partial is None else (f"{parent_name}.{partial}" if parent_name else partial)
            kids = self._resolve(node.get("Kids"))
            kids = kids if isinstance(kids, list) else []
            field_kids = [k for k in kids if isinstance(self._resolve(k), dict) and "T" in self._resolve(k)]
            if field_kids:
                for kid in kids:
                    walk(kid, name, here, depth + 1)
                return
            if len(out) >= MAX_FIELDS:
                raise PdfError(f"This form has more than {MAX_FIELDS} fields.")
            ftype = str(here.get("FT") or "")
            flags = here.get("Ff") if isinstance(here.get("Ff"), int) else 0
            widgets = [widget(k, self._resolve(k)) for k in kids if isinstance(self._resolve(k), dict)] if kids else [widget(ref, node)]
            opts = []
            for o in here.get("Opt") or [] if isinstance(here.get("Opt"), list) else []:
                o = self._resolve(o)
                if isinstance(o, list) and o:
                    o = self._resolve(o[-1])
                opts.append(_text(o) if isinstance(o, (bytes, str)) else "")
            value = self._resolve(node.get("V"))
            out.append(Field(
                name=name or f"(unnamed {len(out) + 1})", ftype=ftype, flags=flags,
                ref=ref if isinstance(ref, Ref) else None, widgets=widgets,
                da=_text(here.get("DA")) or default_da, q=here.get("Q") if isinstance(here.get("Q"), int) else 0,
                max_len=here.get("MaxLen") if isinstance(here.get("MaxLen"), int) else 0, options=opts,
                value=_text(value) if isinstance(value, (bytes, str)) else "",
            ))

        top = self._resolve(form.get("Fields"))
        for ref in top if isinstance(top, list) else []:
            walk(ref, "", {}, 0)
        return out


def _text(value: Any) -> str:
    """A PDF text string (UTF-16 with its byte order mark, or PDFDocEncoding read as Latin-1) as str."""
    if isinstance(value, Name):
        return str(value)
    if isinstance(value, str):
        return value
    if not isinstance(value, (bytes, bytearray)):
        return ""
    if value[:2] == b"\xfe\xff":
        return value[2:].decode("utf-16-be", "replace")
    if value[:2] == b"\xff\xfe":
        return value[2:].decode("utf-16-le", "replace")
    if value[:3] == b"\xef\xbb\xbf":
        return value[3:].decode("utf-8", "replace")
    return value.decode("latin-1")


# ---------- reading a form for the person ----------

@dataclass
class FormInfo:
    pages: list[dict]       # [{"width", "height"}] in points, as each page shows
    fields: list[dict]      # [{"name", "kind", "page", "rect", "max_len", "on", "options"}]
    fillable: bool
    notes: list[str]


def read(data: bytes) -> FormInfo:
    """What a blank form has: its pages and its fillable fields. Raises PdfError with the reason
    a person can act on."""
    pdf = Pdf(data)
    if pdf.is_dynamic_xfa():
        raise PdfError(DYNAMIC_XFA)
    pages = pdf.pages()
    if not pages:
        raise PdfError(DAMAGED)
    fields = pdf.fields(pages)
    notes = []
    if pdf.repaired:
        notes.append("The PDF's index was damaged; it was read by scanning the file.")
    form = pdf.acroform()
    if form and form.get("XFA") is not None:
        notes.append("The form also carries XFA data; Duracash fills its ordinary fields and leaves the XFA out of the filled copy.")
    if any(abs(w.rect[2] - w.rect[0]) < 1 for f in fields for w in f.widgets):
        notes.append("Some fields have no box on the page.")
    out_fields = []
    for f in fields:
        w = next((w for w in f.widgets if w.page >= 0), f.widgets[0] if f.widgets else None)
        out_fields.append({
            "name": f.name, "kind": f.kind, "page": (w.page + 1) if w and w.page >= 0 else 0,
            "rect": [round(v, 1) for v in w.rect] if w else [0, 0, 0, 0],
            "max_len": f.max_len, "on": w.on_state if w else "", "options": f.options[:50],
        })
    return FormInfo(
        pages=[{"width": round(p.size[0], 1), "height": round(p.size[1], 1), "rotate": p.rotate} for p in pages],
        fields=out_fields, fillable=bool(out_fields), notes=notes,
    )


# ---------- writing ----------

def _num(v: float) -> bytes:
    if isinstance(v, bool):
        return b"true" if v else b"false"
    if isinstance(v, int):
        return b"%d" % v
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return (text if text not in ("", "-0") else "0").encode()


def _name_bytes(name: str) -> bytes:
    out = bytearray(b"/")
    for b in name.encode("latin-1", "replace"):
        if b < 0x21 or b > 0x7E or b in b"()<>[]{}/%#":
            out += b"#%02X" % b
        else:
            out.append(b)
    return bytes(out)


def serialize(obj: Any) -> bytes:
    if obj is None:
        return b"null"
    if isinstance(obj, bool):
        return b"true" if obj else b"false"
    if isinstance(obj, (int, float)):
        return _num(obj)
    if isinstance(obj, Name):
        return _name_bytes(obj)
    if isinstance(obj, Ref):
        return b"%d %d R" % (obj.num, obj.gen)
    if isinstance(obj, (bytes, bytearray)):
        return b"<" + bytes(obj).hex().encode() + b">"
    if isinstance(obj, str):  # a keyword left as read, or text: written as a text string
        return serialize(text_string(obj))
    if isinstance(obj, list):
        return b"[" + b" ".join(serialize(v) for v in obj) + b"]"
    if isinstance(obj, dict):
        return b"<<" + b"".join(_name_bytes(k) + b" " + serialize(v) + b"\n" for k, v in obj.items()) + b">>"
    if isinstance(obj, Stream):
        d = dict(obj.dict)
        d["Length"] = len(obj.raw)
        return serialize(d) + b"\nstream\n" + obj.raw + b"\nendstream"
    raise TypeError(type(obj))


def text_string(text: str) -> bytes:
    """A text string's bytes: Latin-1 when every character is plain, else UTF-16BE with its mark."""
    try:
        raw = text.encode("latin-1")
        if all(0x20 <= b < 0x7F for b in raw):
            return raw
    except UnicodeEncodeError:
        pass
    return b"\xfe\xff" + text.encode("utf-16-be")


def _show(text: str) -> bytes:
    """Text drawn in a WinAnsi Helvetica: cp1252, escaped for a literal string."""
    raw = str(text).encode("cp1252", "replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b" ").replace(b"\n", b" ") + b")"


# Helvetica's widths (thousandths of an em) for printable ASCII, from the font's metrics.
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
      556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
      1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
      667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
      333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
      556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


def text_width(text: str, size: float) -> float:
    return sum(_W[ord(c) - 32] if 32 <= ord(c) < 127 else 556 for c in str(text)) * size / 1000


FONT = "DuraHelv"


class Update:
    """New and changed objects, written after the original bytes with their own cross-reference."""

    def __init__(self, pdf: Pdf) -> None:
        self.pdf = pdf
        self.objects: dict[int, tuple[int, Any]] = {}   # num -> (gen, object)
        self.next = pdf.size
        self._font: Ref | None = None

    def put(self, ref: Ref, obj: Any) -> None:
        entry = self.pdf.xref.get(ref.num)
        gen = entry[2] if entry and entry[0] == "n" else 0
        self.objects[ref.num] = (gen, obj)

    def add(self, obj: Any) -> Ref:
        ref = Ref(self.next, 0)
        self.next += 1
        self.objects[ref.num] = (0, obj)
        return ref

    def font(self) -> Ref:
        if self._font is None:
            self._font = self.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica"),
                                   "Encoding": Name("WinAnsiEncoding")})
        return self._font

    def current(self, ref: Ref) -> Any:
        """The object as this update has it, or as the file has it."""
        if ref.num in self.objects:
            return self.objects[ref.num][1]
        return self.pdf.get(ref.num)

    def write(self) -> bytes:
        pdf = self.pdf
        out = bytearray(pdf.data)
        if not out.endswith((b"\n", b"\r")):
            out += b"\n"
        offsets: dict[int, tuple[int, int]] = {}
        for num in sorted(self.objects):
            gen, obj = self.objects[num]
            offsets[num] = (len(out) - pdf.base, gen)
            out += b"%d %d obj\n" % (num, gen) + serialize(obj) + b"\nendobj\n"
        trailer: dict = {}
        for k in ("Root", "Info", "ID"):
            if k in pdf.trailer and pdf.trailer[k] is not None:
                trailer[k] = pdf.trailer[k]
        if pdf.last_xref >= 0:
            trailer["Prev"] = pdf.last_xref
        if pdf.xref_stream and pdf.last_xref >= 0:
            num = self.next
            self.next += 1
            offsets[num] = (len(out) - pdf.base, 0)
            nums = sorted(offsets)
            index, rows = [], bytearray()
            for n in nums:
                if index and index[-2] + index[-1] == n:
                    index[-1] += 1
                else:
                    index += [n, 1]
                off, gen = offsets[n]
                rows += bytes([1]) + off.to_bytes(4, "big") + gen.to_bytes(2, "big")
            d = {"Type": Name("XRef"), "Size": self.next, "W": [1, 4, 2], "Index": index, **trailer}
            xref_at = len(out) - pdf.base
            out += b"%d 0 obj\n" % num + serialize(Stream(d, bytes(rows))) + b"\nendobj\n"
            out += b"startxref\n%d\n%%%%EOF\n" % xref_at
            return bytes(out)
        xref_at = len(out) - pdf.base
        out += b"xref\n"
        nums = sorted(offsets)
        groups: list[list[int]] = []
        for n in nums:
            if groups and groups[-1][-1] + 1 == n:
                groups[-1].append(n)
            else:
                groups.append([n])
        if pdf.last_xref < 0:
            # A repaired file: a complete table, so a reader needs nothing before it.
            out = bytearray(out[:xref_at + pdf.base])
            return self._full_rewrite(out, offsets)
        for g in groups:
            out += b"%d %d\n" % (g[0], len(g))
            for n in g:
                off, gen = offsets[n]
                out += b"%010d %05d n\r\n" % (off, gen)
        trailer["Size"] = self.next
        out += b"trailer\n" + serialize(trailer) + b"\nstartxref\n%d\n%%%%EOF\n" % xref_at
        return bytes(out)

    def _full_rewrite(self, out: bytearray, offsets: dict[int, tuple[int, int]]) -> bytes:
        """For a file read by scanning: a table naming every object, the old ones where the scan found them."""
        pdf = self.pdf
        entries: dict[int, tuple[int, int]] = {}
        for num, entry in pdf.xref.items():
            if entry[0] == "n":
                entries[num] = (entry[1] - pdf.base, entry[2])
        compressed = {num: e for num, e in pdf.xref.items() if e[0] == "c"}
        # Objects only inside object streams can't be named by a plain table: copy them out.
        for num in compressed:
            if num not in offsets:
                obj = pdf.get(num)
                offsets[num] = (len(out) - pdf.base, 0)
                out += b"%d 0 obj\n" % num + serialize(obj) + b"\nendobj\n"
        entries.update(offsets)
        size = max(max(entries, default=0) + 1, self.next)
        xref_at = len(out) - pdf.base
        out += b"xref\n0 %d\n" % size
        for n in range(size):
            if n in entries and n != 0:
                out += b"%010d %05d n\r\n" % entries[n]
            else:
                out += b"0000000000 65535 f\r\n" if n == 0 else b"0000000000 00000 f\r\n"
        trailer = {k: pdf.trailer[k] for k in ("Root", "Info", "ID") if pdf.trailer.get(k) is not None}
        trailer["Size"] = size
        out += b"trailer\n" + serialize(trailer) + b"\nstartxref\n%d\n%%%%EOF\n" % xref_at
        return bytes(out)


_DA_SIZE = re.compile(r"/([^\s/]+)\s+([\d.]+)\s+Tf")
_DA_COLOR = re.compile(r"((?:[\d.]+\s+){1}g|(?:[\d.]+\s+){3}rg|(?:[\d.]+\s+){4}k)\b")


def _appearance(update: Update, value: str, widget: Widget, f: Field) -> Ref:
    """A plain appearance for a text field's value: Helvetica, its size from the field's /DA (or
    made to fit), aligned as the field says, one line (or wrapped for a multi-line field)."""
    x0, y0, x1, y1 = widget.rect
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    m = _DA_SIZE.search(f.da or "")
    size = float(m.group(2)) if m else 0.0
    multi = bool(f.flags & (1 << 12))
    if size <= 0:
        size = min(12.0, max(4.0, (h - 3) * 0.8)) if not multi else 10.0
    while size > 4 and not multi and text_width(value, size) > w - 4:
        size -= 0.5
    color = _DA_COLOR.search(f.da or "")
    ops = [b"/Tx BMC q 1 1 %s %s re W n BT /%s %s Tf %s" % (_num(w - 2), _num(h - 2), FONT.encode(), _num(size),
                                                              color.group(1).encode() if color else b"0 g")]
    lines = [value]
    if multi:
        lines, line = [], ""
        for word in value.split():
            test = f"{line} {word}".strip()
            if text_width(test, size) > w - 4 and line:
                lines.append(line)
                line = word
            else:
                line = test
        lines.append(line)
    for i, line in enumerate(lines):
        tw = text_width(line, size)
        x = (w - tw) / 2 if f.q == 1 else (w - 2 - tw) if f.q == 2 else 2
        y = (h - size * (i + 1) - 1) if multi else max(1.0, (h - size * 0.72) / 2)
        ops.append(b"1 0 0 1 %s %s Tm %s Tj" % (_num(max(1.0, x)), _num(y), _show(line)))
    ops.append(b"ET Q EMC")
    return update.add(Stream({"Type": Name("XObject"), "Subtype": Name("Form"), "BBox": [0, 0, _num_val(w), _num_val(h)],
                              "Resources": {"Font": {FONT: update.font()}}}, b"\n".join(ops)))


def _num_val(v: float) -> float | int:
    return int(v) if float(v).is_integer() else round(v, 4)


TRUE_WORDS = {"1", "x", "yes", "true", "on", "checked", "y"}


def _display_matrix(page: Page) -> list[float]:
    """The cm that turns coordinates on the page as it shows (origin bottom left) into its own."""
    x0, y0, x1, y1 = page.box
    return {0: [1, 0, 0, 1, x0, y0], 90: [0, 1, -1, 0, x1, y0],
            180: [-1, 0, 0, -1, x1, y1], 270: [0, -1, 1, 0, x0, y1]}[page.rotate]


@dataclass
class Placed:
    """A value drawn at a position on a page as it shows: x and y in points from the top left,
    y the line the text sits on."""
    page: int        # 0-based
    x: float
    y: float
    text: str
    size: float = 10
    align: str = "left"   # left, right (x is the right edge) or center


def _grid_ops(width: float, height: float) -> list[bytes]:
    """A light grid every half inch, numbered in inches from the top left, to read positions off."""
    ops = [b"q 0.55 0.75 0.95 RG 0.3 w"]
    step = 36
    x = step
    while x < width:
        ops.append(b"%s 0 m %s %s l S" % (_num(x), _num(x), _num(height)))
        x += step
    y = step
    while y < height:
        ops.append(b"0 %s m %s %s l S" % (_num(height - y), _num(width), _num(height - y)))
        y += step
    ops.append(b"Q q 0.1 0.35 0.75 rg BT")
    inch = 72
    k = 1
    while k * inch < width:
        ops.append(b"/%s 7 Tf 1 0 0 1 %s %s Tm (%d) Tj" % (FONT.encode(), _num(k * inch + 1), _num(height - 8), k))
        k += 1
    k = 1
    while k * inch < height:
        ops.append(b"/%s 7 Tf 1 0 0 1 2 %s Tm (%d) Tj" % (FONT.encode(), _num(height - k * inch + 1), k))
        k += 1
    ops.append(b"ET Q")
    return ops


def fill(data: bytes, values: dict[str, str] | None = None, placed: list[Placed] | None = None, grid: bool = False,
         sample_boxes: bool = False) -> bytes:
    """The form with values written in, as an incremental update. values: field name -> text (a
    checkbox is ticked by yes, x, on, 1 or its own on-state name). placed: text drawn at positions.
    grid: a half-inch grid on every page, for reading positions off a preview. sample_boxes: a thin
    outline around each field filled, for a preview. Field names not on the form are ignored here;
    gcforms checks them first."""
    pdf = Pdf(data)
    if pdf.is_dynamic_xfa():
        raise PdfError(DYNAMIC_XFA)
    values = values or {}
    placed = placed or []
    pages = pdf.pages()
    update = Update(pdf)
    by_name = {f.name: f for f in pdf.fields(pages)} if values else {}
    changed = False

    for name, value in values.items():
        f = by_name.get(name)
        if not f or f.kind in ("signature", "button"):
            continue
        value = "" if value is None else str(value)
        if f.kind in ("checkbox", "radio"):
            on = value.strip().lower() in TRUE_WORDS
            names = [w.on_state for w in f.widgets if w.on_state]
            # A radio group: the widget whose on-state is the value given.
            pick = value if value in names else (names[0] if on and names else "")
            new_v = Name(pick) if pick else Name("Off")
            if f.ref:
                node = dict(update.current(f.ref) or {})
                node["V"] = new_v
                update.put(f.ref, node)
            for w in f.widgets:
                if w.ref:
                    node = dict(update.current(w.ref) or {})
                    node["AS"] = Name(w.on_state) if pick and w.on_state == pick else Name("Off")
                    if w.ref == f.ref:
                        node["V"] = new_v
                    update.put(w.ref, node)
            changed = True
            continue
        if f.max_len and len(value) > f.max_len:
            value = value[:f.max_len]
        if f.ref:
            node = dict(update.current(f.ref) or {})
            node["V"] = text_string(value)
            update.put(f.ref, node)
        for w in f.widgets:
            if not w.ref:
                continue
            node = dict(update.current(w.ref) or {})
            if w.ref == f.ref:
                node["V"] = text_string(value)
            ap = _appearance(update, value, w, f) if value else update.add(Stream(
                {"Type": Name("XObject"), "Subtype": Name("Form"), "BBox": [0, 0, 1, 1]}, b"/Tx BMC EMC"))
            node["AP"] = {"N": ap}
            update.put(w.ref, node)
        changed = True

    if changed:
        form_ref = pdf.root.get("AcroForm")
        form = dict(pdf.acroform() or {})
        form["NeedAppearances"] = True
        form.pop("XFA", None)   # filled copies read their values from the fields, not stale XFA data
        if isinstance(form_ref, Ref):
            update.put(form_ref, form)
        else:
            root_ref = pdf.trailer["Root"]
            root = dict(pdf.root)
            root["AcroForm"] = form
            update.put(root_ref, root)

    boxes_on: dict[int, list[list[float]]] = {}
    if sample_boxes:
        for name in values:
            f = by_name.get(name)
            for w in f.widgets if f else []:
                if w.page >= 0:
                    boxes_on.setdefault(w.page, []).append(w.rect)

    on_pages: dict[int, list[Placed]] = {}
    for item in placed:
        if 0 <= item.page < len(pages) and str(item.text):
            on_pages.setdefault(item.page, []).append(item)
    for index, page in enumerate(pages):
        items = on_pages.get(index, [])
        rects = boxes_on.get(index, [])
        if not items and not grid and not rects:
            continue
        width, height = page.size
        ops = [b"Q q %s cm" % b" ".join(_num(v) for v in _display_matrix(page))]
        if grid:
            ops += _grid_ops(width, height)
        ops.append(b"0 g")
        for item in items:
            size = max(4.0, min(36.0, float(item.size or 10)))
            tw = text_width(item.text, size)
            x = item.x - tw if item.align == "right" else item.x - tw / 2 if item.align == "center" else item.x
            ops.append(b"BT /%s %s Tf 1 0 0 1 %s %s Tm %s Tj ET" % (FONT.encode(), _num(size), _num(x), _num(height - item.y), _show(item.text)))
        ops.append(b"Q")
        if rects:
            # In the page's own coordinates, where the fields' boxes are.
            ops.append(b"q 0.85 0.45 0.1 RG 0.6 w")
            for r in rects:
                ops.append(b"%s %s %s %s re S" % (_num(r[0]), _num(r[1]), _num(r[2] - r[0]), _num(r[3] - r[1])))
            ops.append(b"Q")
        ours = update.add(Stream({}, b"\n".join(ops)))
        opener = update.add(Stream({}, b"q"))
        node = dict(update.current(page.ref) or page.dict)
        contents = pdf.resolve(node.get("Contents"))
        old = node.get("Contents")
        if isinstance(old, Ref) and isinstance(contents, Stream):
            parts = [old]
        elif isinstance(contents, list):
            parts = [c for c in contents if isinstance(c, Ref)]
        else:
            parts = []
        node["Contents"] = [opener, *parts, ours]
        res = dict(page.resources)
        fonts = pdf.resolve(res.get("Font"))
        fonts = dict(fonts) if isinstance(fonts, dict) else {}
        fonts[FONT] = update.font()
        res["Font"] = fonts
        node["Resources"] = res
        update.put(page.ref, node)
    if not update.objects:
        return pdf.data
    return update.write()


def field_values(data: bytes) -> dict[str, str]:
    """Each field's value as the file now has it (the newest update's): for tests and checks."""
    pdf = Pdf(data)
    return {f.name: f.value for f in pdf.fields()}
