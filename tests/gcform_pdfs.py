"""Small blank forms written here, byte by byte, for the GC forms tests: a fillable form with a
classic cross-reference table, the same with a cross-reference stream and its objects packed in an
object stream, one saved twice (an incremental update), one with no fields, a rotated page, a
locked one and a dynamic XFA one. Nothing here uses a PDF library."""

from __future__ import annotations

import zlib


def _obj(num: int, body: bytes) -> bytes:
    return b"%d 0 obj\n" % num + body + b"\nendobj\n"


def _stream(d: bytes, data: bytes) -> bytes:
    return b"<<" + d + b" /Length %d>>\nstream\n" % len(data) + data + b"\nendstream"


def _text_field(num: int, name: str, rect: tuple, page: int = 3, extra: bytes = b"") -> bytes:
    return (b"<</Type /Annot /Subtype /Widget /FT /Tx /T (%s) /Rect [%d %d %d %d] /P %d 0 R /DA (/Helv 0 Tf 0 g) /F 4%s>>"
            % (name.encode(), *rect, page, extra))


def _objects(fields_extra: bool = True, page_extra: bytes = b"") -> dict[int, bytes]:
    """1 catalog, 2 pages, 3 page, 4 content, 5 font, 6 AcroForm, 7.. fields."""
    content = b"BT /F1 14 Tf 72 740 Td (APPLICATION AND CERTIFICATE FOR PAYMENT) Tj ET\nBT /F1 10 Tf 72 700 Td (Original contract sum) Tj ET"
    objs = {
        1: b"<</Type /Catalog /Pages 2 0 R /AcroForm 6 0 R>>",
        2: b"<</Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792]>>",
        4: _stream(b"", content),
        5: b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>",
        7: _text_field(7, "ContractSum", (300, 690, 450, 710)),
        8: _text_field(8, "AppNo", (450, 735, 540, 755), extra=b" /Q 2 /MaxLen 6"),
        9: b"<</Type /Annot /Subtype /Widget /FT /Btn /T (Final) /Rect [72 650 86 664] /P 3 0 R /AS /Off"
           b" /AP <</N <</Yes 12 0 R /Off 12 0 R>>>>>>",
        # A parent with two kids: Row.1.Desc and Row.2.Desc, each a merged field and widget.
        10: b"<</T (Row) /Kids [13 0 R 14 0 R]>>",
        12: _stream(b"/Type /XObject /Subtype /Form /BBox [0 0 14 14]", b""),
        13: b"<</T (1) /Parent 10 0 R /Kids [15 0 R]>>",
        14: b"<</T (2) /Parent 10 0 R /Kids [16 0 R]>>",
        15: b"<</Type /Annot /Subtype /Widget /FT /Tx /T (Desc) /Parent 13 0 R /Rect [72 600 300 615] /P 3 0 R /DA (/Helv 9 Tf 0 g)>>",
        16: b"<</Type /Annot /Subtype /Widget /FT /Tx /T (Desc) /Parent 14 0 R /Rect [72 580 300 595] /P 3 0 R /DA (/Helv 9 Tf 0 g)>>",
        # A field whose one widget is its own kid (no /T on the widget).
        11: b"<</FT /Tx /T (Owner) /Kids [17 0 R] /DA (/Helv 10 Tf 0 g)>>",
        17: b"<</Type /Annot /Subtype /Widget /Parent 11 0 R /Rect [72 540 300 556] /P 3 0 R>>",
    }
    objs[3] = (b"<</Type /Page /Parent 2 0 R /Contents 4 0 R /Resources <</Font <</F1 5 0 R>>>>"
               b" /Annots [7 0 R 8 0 R 9 0 R 15 0 R 16 0 R 17 0 R]" + page_extra + b">>")
    objs[6] = b"<</Fields [7 0 R 8 0 R 9 0 R 10 0 R 11 0 R] /DA (/Helv 0 Tf 0 g) /DR <</Font <</Helv 5 0 R>>>>>>"
    return objs


def with_table(objs: dict[int, bytes], trailer_extra: bytes = b"", header: bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n") -> bytes:
    out = bytearray(header)
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += _obj(num, objs[num])
    size = max(objs) + 1
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f\r\n" % size
    for n in range(1, size):
        out += (b"%010d 00000 n\r\n" % offsets[n]) if n in offsets else b"0000000000 00000 f\r\n"
    out += b"trailer\n<</Size %d /Root 1 0 R%s>>\nstartxref\n%d\n%%%%EOF\n" % (size, trailer_extra, xref)
    return bytes(out)


def _png_up(rows: list[bytes]) -> bytes:
    """Rows encoded with the PNG Up predictor (filter type 2), as xref streams often are."""
    out, prev = bytearray(), bytes(len(rows[0]))
    for row in rows:
        out.append(2)
        out += bytes((row[i] - prev[i]) & 0xFF for i in range(len(row)))
        prev = row
    return bytes(out)


def with_xref_stream(objs: dict[int, bytes], packed: set[int]) -> bytes:
    """Objects in `packed` go into one object stream (compressed); the rest are plain. The
    cross-reference is a stream with a PNG predictor."""
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, tuple] = {}
    for num in sorted(objs):
        if num not in packed:
            offsets[num] = (1, len(out), 0)
            out += _obj(num, objs[num])
    stm_num = max(objs) + 1
    header, body = [], bytearray()
    for i, num in enumerate(sorted(packed)):
        header.append(b"%d %d" % (num, len(body)))
        body += objs[num] + b"\n"
        offsets[num] = (2, stm_num, i)
    head = b" ".join(header) + b"\n"
    packed_data = zlib.compress(head + bytes(body))
    offsets[stm_num] = (1, len(out), 0)
    out += _obj(stm_num, _stream(b"/Type /ObjStm /N %d /First %d /Filter /FlateDecode" % (len(packed), len(head)), packed_data))
    xref_num = stm_num + 1
    offsets[xref_num] = (1, len(out), 0)
    size = xref_num + 1
    rows = [bytes([0]) + (0).to_bytes(4, "big") + (65535).to_bytes(2, "big")]
    for n in range(1, size):
        kind, a, b = offsets.get(n, (0, 0, 0))
        rows.append(bytes([kind]) + a.to_bytes(4, "big") + b.to_bytes(2, "big"))
    data = zlib.compress(_png_up(rows))
    xref_at = len(out)
    out += _obj(xref_num, _stream(b"/Type /XRef /Size %d /W [1 4 2] /Root 1 0 R /Filter /FlateDecode"
                                  b" /DecodeParms <</Predictor 12 /Columns 7>>" % size, data))
    out += b"startxref\n%d\n%%%%EOF\n" % xref_at
    return bytes(out)


def fillable_table() -> bytes:
    return with_table(_objects())


def fillable_stream() -> bytes:
    """The same form, its fields and AcroForm packed in an object stream."""
    return with_xref_stream(_objects(), packed={6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17})


def saved_twice() -> bytes:
    """The table form, then an update that renames ContractSum's tooltip and adds a field
    (Architect) on the page: the newest objects must win."""
    base = fillable_table()
    prev = base.rindex(b"startxref")
    prev_at = int(base[prev:].split()[1])
    out = bytearray(base)
    updates = {
        18: _text_field(18, "Architect", (320, 540, 540, 556)),
        6: b"<</Fields [7 0 R 8 0 R 9 0 R 10 0 R 11 0 R 18 0 R] /DA (/Helv 0 Tf 0 g) /DR <</Font <</Helv 5 0 R>>>>>>",
        3: (b"<</Type /Page /Parent 2 0 R /Contents 4 0 R /Resources <</Font <</F1 5 0 R>>>>"
            b" /Annots [7 0 R 8 0 R 9 0 R 15 0 R 16 0 R 17 0 R 18 0 R]>>"),
    }
    offsets = {}
    for num in sorted(updates):
        offsets[num] = len(out)
        out += _obj(num, updates[num])
    xref = len(out)
    out += b"xref\n"
    for num in sorted(updates):
        out += b"%d 1\n%010d 00000 n\r\n" % (num, offsets[num])
    out += b"trailer\n<</Size 19 /Root 1 0 R /Prev %d>>\nstartxref\n%d\n%%%%EOF\n" % (prev_at, xref)
    return bytes(out)


def flat(rotate: int = 0, compressed: bool = False) -> bytes:
    """A form with no fields: lines and labels only. compressed: its content Flate-encoded."""
    content = b"BT /F1 12 Tf 72 740 Td (LIEN WAIVER - CONDITIONAL) Tj ET\n72 700 m 300 700 l S"
    stream = _stream(b" /Filter /FlateDecode", zlib.compress(content)) if compressed else _stream(b"", content)
    objs = {
        1: b"<</Type /Catalog /Pages 2 0 R>>",
        2: b"<</Type /Pages /Kids [3 0 R 6 0 R] /Count 2 /MediaBox [0 0 612 792] /Resources <</Font <</F1 5 0 R>>>>>>",
        3: b"<</Type /Page /Parent 2 0 R /Contents 4 0 R" + (b" /Rotate %d" % rotate if rotate else b"") + b">>",
        4: stream,
        5: b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>",
        6: b"<</Type /Page /Parent 2 0 R /Contents [4 0 R] /MediaBox [0 0 792 612]>>",
    }
    return with_table(objs)


def locked() -> bytes:
    objs = _objects()
    objs[19] = b"<</Filter /Standard /V 1 /R 2 /O <00> /U <00> /P -4>>"
    return with_table(objs, trailer_extra=b" /Encrypt 19 0 R /ID [<00112233> <00112233>]")


def dynamic_xfa() -> bytes:
    objs = {
        1: b"<</Type /Catalog /Pages 2 0 R /AcroForm 6 0 R /NeedsRendering true>>",
        2: b"<</Type /Pages /Kids [3 0 R] /Count 1>>",
        3: b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>",
        6: b"<</Fields [] /XFA 7 0 R>>",
        7: _stream(b"", b"<xdp:xdp/>"),
    }
    return with_table(objs)


def damaged_xref() -> bytes:
    """The table form with its startxref pointing at nothing: read by scanning."""
    data = fillable_table()
    k = data.rindex(b"startxref")
    return data[:k] + b"startxref\n999999\n%%EOF\n"
