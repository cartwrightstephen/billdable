"""A state's statutory lien waiver, filled in from the job and printed as a PDF whose blanks can
still be typed in (Stephen, 10 Nov, decision 1: "we should at least have a fillable form for one
suggested waiver and allow the user to upload their own in states where permitted").

Georgia only for now: O.C.G.A. § 44-14-366(d) (interim payment) and (e) (final payment), its
words kept in helper/waiver_forms.json. Every word prints in 12-point type, the blanks are PDF
form fields with what Duracash knows already in them, and the person prints it, signs it and adds
the signed copy to the job records as before: Duracash never sends an unsigned form.

Held back until an attorney approves the wording: the server's DURACASH_WAIVER_FORMS names the
states whose form is shown ("GA", or "on" for every state that has one); unset, none is.

Standard library only, like payapp_pdf: Helvetica (not embedded), portrait US Letter.
"""

from __future__ import annotations

import os
import re
from typing import Any

import domain

PAGE_W, PAGE_H = 612, 792
MARGIN = 42
SIZE = 12          # the statute's type size, for every word and every blank
LEAD = 15.5        # one line: 12-point type with room for a blank's box
FIELD_H = 14
BLANK_W = {        # each blank's width in points; property takes the whole line, two lines high
    "county": 160, "contractor": 230, "furnish": 260, "project": 260, "city": 150, "project_county": 140,
    "owner": 220, "amount": 110, "through": 130, "day": 36, "month": 110, "year": 50,
    "claimant": 330, "by": 260, "its": 200,
}
PROPERTY_LINES = 2

# Helvetica's widths (thousandths of an em) for printable ASCII, from the font's metrics.
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
      556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
      1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
      667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
      333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
      556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_BOLD_EXTRA = 1.08   # Helvetica-Bold runs about this much wider; the title is wrapped with it


def _width(text: str, size: float = SIZE, bold: bool = False) -> float:
    em = sum(_W[ord(c) - 32] if 32 <= ord(c) < 127 else 556 for c in text)
    return em * size / 1000 * (_BOLD_EXTRA if bold else 1)


def _pdf_str(text: str) -> bytes:
    raw = str(text).encode("cp1252", "replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b" ").replace(b"\n", b" ") + b")"


def _pdf_text(text: str) -> bytes:
    """A field's value or tooltip as a PDF text string: UTF-16BE with its byte order mark, so
    curly quotes, dashes and accents read as typed in any viewer (the PDF spec's text strings;
    cp1252 stays for what is drawn, whose font is WinAnsi). Escaped byte by byte: a backslash or
    parenthesis, and a CR or LF byte, which a reader would otherwise take as a line end."""
    raw = b"\xfe\xff" + str(text).replace("\r", " ").replace("\n", " ").encode("utf-16-be")
    out = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b"\\r").replace(b"\n", b"\\n")
    return b"(" + out + b")"


def states_on() -> set[str]:
    """The states whose form customers see: DURACASH_WAIVER_FORMS, a list like "GA", or "on" for
    all. Off unless set (decision 1: held back until an attorney approves the wording)."""
    raw = (os.environ.get("DURACASH_WAIVER_FORMS") or "").strip().upper()
    if raw in {"", "OFF", "0", "NO", "FALSE"}:
        return set()
    built = {st for st, entry in domain.waiver_forms()["states"].items() if entry.get("statutory_forms")}
    if raw in {"ON", "1", "YES", "TRUE", "ALL"}:
        return built
    return {st for st in re.split(r"[\s,]+", raw) if st} & built


def forms_for_page() -> dict[str, Any]:
    """helper/waiver_forms.json as the waiver page reads it: each state's titles and notes, and
    `fillable` on a state whose filled-in form is switched on. The form's wording stays on the server."""
    data = domain.waiver_forms()
    on = states_on()
    states = {}
    for st, entry in data["states"].items():
        entry = {k: v for k, v in entry.items() if k != "statutory_forms"}
        entry["fillable"] = st in on
        states[st] = entry
    return {**data, "states": states}


def _money(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def prefill(state: dict[str, Any], job: dict[str, Any], for_id: str, which: str) -> dict[str, str]:
    """What Duracash already knows for each blank. for_id: the pay app or retainage release the
    waiver is for (blank: none). The sum is the waiver's own money: what a payment due its waiver
    brought in since the last one, else what the pay app still has to bring in."""
    from domain.validate import parse_legacy_address
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    company = state.get("company", {})
    parts = job.get("address_parts") or parse_legacy_address(str(job.get("address") or ""))
    invoice = next((i for i in job.get("invoices", []) if i["id"] == for_id and i.get("status") != "void"), None)
    release = next((r for r in job.get("retainage_waivers", []) if r["id"] == for_id), None)
    amount = ""
    if invoice:
        due = invoice.get("waiver_due") and not invoice.get("waiver_sent")
        cents = domain.waiver_cents(job, invoice) if due else \
            invoice["amount_cents"] - int(invoice.get("short_pay_cents") or 0) - int(invoice.get("waiver_covered_cents") or 0)
        if not due and cents <= 0:
            # Its waivers are all sent: the form shows the sum of the last one that went, not $0.00.
            sent_parts = invoice.get("partial_waivers") or []
            cents = int(invoice.get("waiver_cents") or (sent_parts[-1]["cents"] if sent_parts else 0))
        amount = _money(max(0, cents))
    elif release:
        amount = _money(release["amount_cents"])
    owner_client = domain.client_type_role(payer.get("client_type")) == "Owner" if payer.get("client_type") else payer.get("role") == "Owner"
    return {
        "county": "", "project_county": "",
        # Whoever hired this company; for a contractor hired by the owner that is the owner too.
        "contractor": payer.get("name", ""),
        "furnish": str(company.get("trade") or "Labor and materials"),
        "project": job.get("name", ""),
        "city": str(parts.get("city") or ""),
        "owner": payer.get("name", "") if owner_client else str(job.get("owner_name") or ""),
        "property": str(job.get("address") or ""),
        "amount": amount,
        "through": str((invoice or {}).get("period_to") or "") if which == "interim" else "",
        "day": "", "month": "", "year": "",
        "claimant": str(company.get("legal_name") or company.get("name") or ""),
        "by": "", "its": "",
    }


def _layout(paragraphs: list[str], title: str) -> list[list[tuple]]:
    """Pages of drawing steps: ("text", x, y, words, bold), ("field", name, x, y, w, h, multiline)."""
    right = PAGE_W - MARGIN
    pages: list[list[tuple]] = [[]]
    y = PAGE_H - MARGIN - SIZE

    def newline(step: float = LEAD) -> None:
        nonlocal y
        y -= step
        if y < MARGIN:
            pages.append([])
            y = PAGE_H - MARGIN - SIZE

    # The title, bold and centred, wrapped to the width.
    line = ""
    for word in title.upper().split():
        test = f"{line} {word}".strip()
        if _width(test, bold=True) > right - MARGIN and line:
            pages[-1].append(("text", (PAGE_W - _width(line, bold=True)) / 2, y, line, True))
            newline()
            line = word
        else:
            line = test
    pages[-1].append(("text", (PAGE_W - _width(line, bold=True)) / 2, y, line, True))
    newline(LEAD * 1.6)
    for para in paragraphs:
        if para.strip() == "{property}":
            # The property's description: a box the width of the page, several lines high.
            height = LEAD * PROPERTY_LINES
            if y - height < MARGIN:
                newline(y)   # the next page
            pages[-1].append(("field", "property", MARGIN, y - height + LEAD - 4, right - MARGIN, height, True))
            newline(height + LEAD * 0.5)
            continue
        x = MARGIN
        run = ""
        run_x = x
        for token in re.split(r"(\{[a-z_]+\}|\s+)", para):
            if not token:
                continue
            blank = re.fullmatch(r"\{([a-z_]+)\}", token)
            if token.isspace():
                if run:
                    run += " "
                    x += _width(" ")
                continue
            width = BLANK_W.get(blank.group(1), 150) if blank else _width(token)
            if x + width > right and x > MARGIN:
                if run.strip():
                    pages[-1].append(("text", run_x, y, run.rstrip(), False))
                newline()
                x, run = MARGIN, ""
            if not run:
                run_x = x
            if blank:
                if run.strip():
                    pages[-1].append(("text", run_x, y, run.rstrip(), False))
                pages[-1].append(("field", blank.group(1), x, y - 4, width, FIELD_H, False))
                x += width
                run, run_x = "", x
            else:
                run += token
                x += width
        if run.strip():
            pages[-1].append(("text", run_x, y, run.rstrip(), False))
        newline(LEAD * 1.25)
    return [page for page in pages if page]   # the last paragraph's spacing can start a page with nothing on it


def render(paragraphs: list[str], title: str, values: dict[str, str]) -> bytes:
    """The form as a PDF with one text field per blank (an AcroForm), each filled with its value
    and drawn with it, so it prints filled in and can still be changed before signing."""
    pages = _layout(paragraphs, title)
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    helv = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    fields: list[int] = []
    # Ids are handed out in order: for each page its widgets (and their looks), then its content.
    pending: list[tuple[int, list[int], int]] = []   # (page index, widget ids, content id)
    for n, steps in enumerate(pages):
        ops = []
        widgets = []
        for step in steps:
            if step[0] == "text":
                _, x, y, words, is_bold = step
                ops.append(b"BT /%s %d Tf %.2f %.2f Td %s Tj ET" % (b"F2" if is_bold else b"F1", SIZE, x, y, _pdf_str(words)))
                continue
            _, name, x, y, w, h, multi = step
            value = str(values.get(name, "") or "")
            ops.append(b"0.4 w %.2f %.2f m %.2f %.2f l S" % (x, y, x + w, y))   # the blank's line, as printed
            look = _appearance(value, w, h, multi)
            ap = add(b"<< /Type /XObject /Subtype /Form /BBox [0 0 %.2f %.2f] /Resources << /Font << /Helv %d 0 R >> >> /Length %d >>\nstream\n"
                     % (w, h, helv, len(look)) + look + b"\nendstream")
            widget = add(b"<< /Type /Annot /Subtype /Widget /FT /Tx /T %s /TU %s /V %s /DA (/Helv %d Tf 0 g) /Rect [%.2f %.2f %.2f %.2f] /F 4%s /AP << /N %d 0 R >> /P PAGEREF%d >>"
                         % (_pdf_str(name), _pdf_text(name.replace("_", " ")), _pdf_text(value), SIZE, x, y, x + w, y + h,
                            b" /Ff 4096" if multi else b"", ap, n))
            widgets.append(widget)
            fields.append(widget)
        data = b"\n".join(ops)
        content = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        pending.append((n, widgets, content))
    first_page = len(objects) + 1
    pages_id = first_page + len(pages)
    resources = b"<< /Font << /F1 %d 0 R /F2 %d 0 R >> >>" % (helv, bold)
    for n, widgets, content in pending:
        annots = b" /Annots [%s]" % b" ".join(b"%d 0 R" % w for w in widgets) if widgets else b""
        add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Resources %s /Contents %d 0 R%s >>"
            % (pages_id, PAGE_W, PAGE_H, resources, content, annots))
    add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % (first_page + n) for n in range(len(pages))), len(pages)))
    acro = add(b"<< /Fields [%s] /DA (/Helv %d Tf 0 g) /DR << /Font << /Helv %d 0 R >> >> >>"
               % (b" ".join(b"%d 0 R" % f for f in fields), SIZE, helv))
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R /AcroForm %d 0 R >>" % (pages_id, acro))
    # Each widget names its page. Only the widgets, and only their /P: the looks hold what people
    # typed, which may read "PAGEREF9" too and must print as typed (B35).
    for wid in fields:
        objects[wid - 1] = re.sub(rb"/P PAGEREF(\d+) >>$", lambda m: b"/P %d 0 R >>" % (first_page + int(m.group(1))), objects[wid - 1])

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref)
    return bytes(out)


def _appearance(value: str, w: float, h: float, multi: bool) -> bytes:
    """How a filled blank looks before anyone types in it: its value in 12-point Helvetica."""
    if not value:
        return b"/Tx BMC EMC"
    lines = [value]
    if multi:
        lines, line = [], ""
        for word in value.split():
            test = f"{line} {word}".strip()
            if _width(test) > w - 4 and line:
                lines.append(line)
                line = word
            else:
                line = test
        lines.append(line)
    ops = [b"/Tx BMC q BT /Helv %d Tf 0 g" % SIZE]
    top = h - SIZE if multi else 3
    for i, text in enumerate(lines):
        ops.append(b"1 0 0 1 2 %.2f Tm %s Tj" % (top - i * (SIZE + 2), _pdf_str(text)))
    ops.append(b"ET Q EMC")
    return b" ".join(ops)


def for_waiver(state: dict[str, Any], job_id: str, for_id: str = "", which: str = "interim") -> tuple[bytes | None, str, str]:
    """(PDF, file name, refusal) for a job's waiver in its state's form. Refused: no form for the
    job's state (or not switched on), or a final waiver while money on the job is still to come
    (decision 6)."""
    job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
    if not job:
        return None, "", "Job not found."
    st = domain.job_state(job)
    entry = domain.waiver_forms()["states"].get(st) or {}
    forms = entry.get("statutory_forms")
    if not forms or st not in states_on():
        return None, "", "No state form is set up for this job's state."
    if which not in ("interim", "final"):
        which = "interim"
    if which == "final" and not job.get("closed"):
        left = domain.final_waiver_holds(job)
        if left:
            return None, "", f"A final waiver waits until all the money on {job['name']} is in: {', '.join(left)}."
    form = forms[which]
    values = prefill(state, job, for_id, which)
    name = re.sub(r"[^A-Za-z0-9._-]", "-", f"{job.get('code') or job['id']}-{st}-{which}-waiver") + ".pdf"
    return render(form["paragraphs"], form["title"], values), name, ""
