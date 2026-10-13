"""The pay app as a PDF: an application-for-payment summary and a continuation sheet.

The layout follows the familiar G702/G703 pattern (summary lines 1-9, then a
line-by-line sheet), but it is Billdable's own document, not an AIA form, and
it says so on every page. Waiver documents are not generated: lien waiver
forms are set by state law, so the contractor supplies their own.

Written with the standard library only: a small PDF writer using the built-in
Helvetica and Courier fonts (no embedding), landscape US Letter.
"""

from __future__ import annotations

from typing import Any

import sov

PAGE_W, PAGE_H = 792, 612
MARGIN = 36
LIVE = sov.LIVE_STATUSES


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def _pdf_text(text: str) -> bytes:
    raw = str(text).encode("cp1252", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b" ").replace(b"\n", b" ")


def _fit(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "\u2026"


class _Page:
    def __init__(self) -> None:
        self.ops: list[bytes] = []

    def text(self, x: float, y: float, s: str, font: str = "F1", size: float = 10) -> None:
        self.ops.append(b"BT /%s %g Tf %g %g Td (%s) Tj ET" % (font.encode(), size, x, y, _pdf_text(s)))

    def right(self, x_right: float, y: float, s: str, size: float = 10) -> None:
        # Courier is 0.6 em per character, so right-aligning is exact.
        self.text(x_right - len(s) * 0.6 * size, y, s, "F3", size)

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5) -> None:
        self.ops.append(b"%g w %g %g m %g %g l S" % (width, x1, y1, x2, y2))

    def stream(self) -> bytes:
        return b"\n".join(self.ops)


def _build(pages: list[_Page]) -> bytes:
    objects: list[bytes] = []
    fonts = [b"Helvetica", b"Helvetica-Bold", b"Courier"]
    font_ids = []
    for name in fonts:
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /" + name + b" /Encoding /WinAnsiEncoding >>")
        font_ids.append(len(objects))
    page_ids = []
    content_objects = []
    for page in pages:
        data = page.stream()
        content_objects.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
    # Layout: fonts, contents, pages (kids), pages tree, catalog.
    first_content = len(objects) + 1
    objects.extend(content_objects)
    first_page = len(objects) + 1
    pages_id = first_page + len(pages)
    resources = b"<< /Font << /F1 %d 0 R /F2 %d 0 R /F3 %d 0 R >> >>" % tuple(font_ids)
    for n in range(len(pages)):
        objects.append(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Resources %s /Contents %d 0 R >>"
            % (pages_id, PAGE_W, PAGE_H, resources, first_content + n)
        )
        page_ids.append(first_page + n)
    objects.append(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % i for i in page_ids), len(pages)))
    objects.append(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    catalog_id = len(objects)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog_id, xref)
    return bytes(out)


def summary(job: dict[str, Any], invoice: dict[str, Any]) -> dict[str, int]:
    """Lines 1-9 of the application, from the pay apps up to and including this one, in the order
    they were made. A voided pay app keeps its own position, so its figures read as they were."""
    order = job.get("invoices", [])
    position = next((i for i, inv in enumerate(order) if inv is invoice or inv.get("id") == invoice.get("id")), len(order))
    up_to = [inv for inv in order[:position] if inv.get("status") in LIVE] + [invoice]
    earlier = up_to[:-1]
    original = job["contract_cents"]
    net_co = sum(co["amount_cents"] for co in job.get("change_orders", []) if co.get("status") == "approved")
    # Change orders billed while still unsigned are in the work completed, so they are shown in the
    # contract sum too, as their own line, rather than leaving the balance short.
    pending_ids = {co["id"] for co in job.get("change_orders", []) if co.get("status") == "pending"}
    unsigned = {}
    for inv in up_to:
        for line in inv.get("lines", []):
            if line.get("co_id") in pending_ids:
                unsigned[line["co_id"]] = line["amount_cents"]
    unsigned_total = sum(unsigned.values())
    gross = lambda inv: inv.get("gross_cents", inv["amount_cents"] + inv.get("retainage_cents", 0))
    # Older records hold retainage on the job rather than on each pay app.
    legacy = int(job.get("retainage_held_cents") or 0)
    completed = sum(gross(inv) for inv in up_to) + legacy
    retained = sum(inv.get("retainage_cents", 0) for inv in up_to) + legacy
    earned = completed - retained
    previous = sum(inv["amount_cents"] for inv in earlier)
    contract_to_date = original + net_co + unsigned_total
    return {
        "original": original,
        "net_co": net_co,
        "unsigned_co": unsigned_total,
        "contract_to_date": contract_to_date,
        "completed": completed,
        "retainage": retained,
        "released": int(job.get("retainage_released_cents") or 0),
        "earned": earned,
        "previous": previous,
        "due": invoice["amount_cents"],
        "balance": contract_to_date - earned,
    }


def render(company_name: str, job: dict[str, Any], payer: dict[str, Any], invoice: dict[str, Any]) -> bytes:
    figures = summary(job, invoice)
    status = invoice.get("status", "")
    mark = {"void": "VOID", "draft": "DRAFT - not submitted", "awaiting_approval": "DRAFT - awaiting approval"}.get(status, "")
    if status != "void" and mark and invoice.get("pencil_status") == "sent":
        mark = "PENCIL COPY - for review, not for payment"
    date = (invoice.get("sent_at") or invoice.get("created_at") or "")[:10]
    pages: list[_Page] = []

    def footer(page: _Page, n: int, total: int) -> None:
        page.line(MARGIN, 40, PAGE_W - MARGIN, 40)
        page.text(MARGIN, 28, "Prepared with Billdable from the contractor's records. This is not an AIA document.", "F1", 7.5)
        page.right(PAGE_W - MARGIN, 28, f"Page {n} of {total}", 7.5)

    # Page 1: the application summary.
    p = _Page()
    y = PAGE_H - MARGIN - 14
    p.text(MARGIN, y, "APPLICATION FOR PAYMENT", "F2", 16)
    p.right(PAGE_W - MARGIN, y, f"Application {invoice['number']}", 11)
    y -= 16
    if mark:
        p.text(MARGIN, y, mark, "F2", 11)
    p.right(PAGE_W - MARGIN, y, f"Date {date}", 10)
    y -= 26
    blocks = [
        ("TO", [payer.get("name", ""), payer.get("approver", ""), payer.get("ap_email", "")]),
        ("FROM", [company_name, f"Approved by {invoice['approved_by']}" if invoice.get("approved_by") else ""]),
        ("PROJECT", [job.get("name", ""), job.get("address", ""), f"Contract {job['contract_number']}" if job.get("contract_number") else ""]),
    ]
    col = (PAGE_W - 2 * MARGIN) / 3
    for i, (label, lines) in enumerate(blocks):
        x = MARGIN + i * col
        p.text(x, y, label, "F2", 8)
        for k, line in enumerate([l for l in lines if l][:3]):
            p.text(x, y - 13 * (k + 1), _fit(line, 42), "F1", 10)
    y -= 70
    p.line(MARGIN, y, PAGE_W - MARGIN, y, 1)
    y -= 22
    rows = [
        ("1. Original contract sum", figures["original"]),
        ("2. Net change by approved change orders", figures["net_co"]),
    ]
    if figures["unsigned_co"]:
        rows.append(("2a. Change orders billed here, not yet signed", figures["unsigned_co"]))
    rows += [
        ("3. Contract sum to date (1 + 2" + (" + 2a)" if figures["unsigned_co"] else ")"), figures["contract_to_date"]),
        ("4. Total completed to date", figures["completed"]),
        ("5. Retainage held to date", figures["retainage"]),
        ("6. Total earned less retainage (4 - 5)", figures["earned"]),
        ("7. Less previous pay apps", figures["previous"]),
        ("8. CURRENT PAYMENT DUE (6 - 7)", figures["due"]),
        ("9. Balance to finish, including retainage (3 - 6)", figures["balance"]),
    ]
    for label, value in rows:
        bold = label.startswith("8.")
        p.text(MARGIN + 20, y, label, "F2" if bold else "F1", 11 if bold else 10.5)
        p.right(MARGIN + 520, y, _money(value), 11 if bold else 10.5)
        y -= 20 if not label.startswith(("3.", "6.")) else 26
    y -= 6
    pct = invoice.get("retainage_cents", 0)
    p.text(MARGIN + 20, y, f"This pay app: {_money(invoice.get('gross_cents', invoice['amount_cents']))} of work"
           + (f", less {_money(pct)} retainage" if pct else "") + f" = {_money(invoice['amount_cents'])}.", "F1", 9)
    if figures["released"]:
        y -= 14
        p.text(MARGIN + 20, y, f"Retainage released to date, recorded separately: {_money(figures['released'])}.", "F1", 9)
    y -= 30
    p.text(MARGIN + 20, y, "Contractor signature: ____________________________________     Date: ______________", "F1", 10)
    pages.append(p)

    # Continuation sheet: every schedule line, or the pay app's lines when the job has no schedule.
    sheet = invoice.get("sov_sheet") or invoice.get("sov_lines") or []
    header_y = PAGE_H - MARGIN - 14

    def new_sheet_page(title: str) -> tuple[_Page, float]:
        page = _Page()
        page.text(MARGIN, header_y, title, "F2", 13)
        page.right(PAGE_W - MARGIN, header_y, f"{invoice['number']} - {_fit(job.get('name', ''), 40)}", 9)
        return page, header_y - 28

    if sheet:
        # G703 columns: C scheduled, D previous work, E work this period, F materials stored,
        # G completed and stored to date, G/C, H balance, I retainage at the job's percent.
        widths = [5, 24, 13, 13, 13, 12, 13, 4, 13, 12]
        heads = ["Item", "Description", "Scheduled", "Previous", "This period", "Stored", "To date", "%", "Balance", "Retainage"]
        pct_ret = int(job.get("retainage_pct") or 0)
        size = 8

        def row_text(cells: list[str]) -> str:
            out = []
            for i, (cell, w) in enumerate(zip(cells, widths)):
                out.append(cell.ljust(w) if i < 2 else cell.rjust(w))
            return " ".join(out)

        page, y = new_sheet_page("CONTINUATION SHEET")
        page.text(MARGIN, y, row_text(heads), "F3", size)
        page.line(MARGIN, y - 4, PAGE_W - MARGIN, y - 4)
        y -= 16
        totals = [0, 0, 0, 0, 0, 0]  # scheduled, previous, this period, stored, to date, retainage
        for e in sheet:
            if y < 70:
                pages.append(page)
                page, y = new_sheet_page("CONTINUATION SHEET (continued)")
                page.text(MARGIN, y, row_text(heads), "F3", size)
                y -= 16
            c = sov.sheet_columns(e, pct_ret)
            page.text(MARGIN, y, row_text([
                _fit(e.get("item", ""), 5), _fit(e["description"], 24),
                *(f"{c[k] / 100:,.2f}" for k in ("scheduled", "previous", "this_period", "stored", "to_date")),
                f"{c['pct']}%", f"{c['balance'] / 100:,.2f}", f"{c['retainage'] / 100:,.2f}",
            ]), "F3", size)
            for i, k in enumerate(("scheduled", "previous", "this_period", "stored", "to_date", "retainage")):
                totals[i] += c[k]
            y -= 13
        bp = sov.op_bp(job)
        if bp:
            # Overhead and profit as its own row, as of this pay app, so the totals match line 4.
            live = [inv for inv in job.get("invoices", []) if inv.get("status") in LIVE]
            earlier = live[: live.index(invoice)] if invoice in live else live
            op_prev = sum(inv.get("op_cents", 0) for inv in earlier)
            op_this = invoice.get("op_cents", 0)
            op_sched = sov.markup(sum(l["scheduled_cents"] for l in sov.lines(job) if not l.get("co_id")), bp)
            op_to = op_prev + op_this
            op_ret = sov.retainage_on(op_to, pct_ret)
            page.text(MARGIN, y, row_text(["", _fit(f"Overhead and profit {bp / 100:g}%", 24), f"{op_sched / 100:,.2f}", f"{op_prev / 100:,.2f}",
                                           f"{op_this / 100:,.2f}", "0.00", f"{op_to / 100:,.2f}",
                                           f"{(100 * op_to) // op_sched if op_sched else 0}%", f"{(op_sched - op_to) / 100:,.2f}",
                                           f"{op_ret / 100:,.2f}"]), "F3", size)
            for i, v in enumerate((op_sched, op_prev, op_this, 0, op_to, op_ret)):
                totals[i] += v
            y -= 13
        page.line(MARGIN, y + 8, PAGE_W - MARGIN, y + 8)
        page.text(MARGIN, y - 4, row_text(["", "Totals", *(f"{t / 100:,.2f}" for t in totals[:5]), "",
                                           f"{(totals[0] - totals[4]) / 100:,.2f}", f"{totals[5] / 100:,.2f}"]), "F3", size)
        if pct_ret:
            page.text(MARGIN, y - 18, f"Retainage column: {pct_ret}% of the total to date on each line, for reference. Line 5 on page 1 is the retainage actually held to date.", "F1", 8)
        pages.append(page)
    else:
        page, y = new_sheet_page("PAY APP LINES")
        for line in invoice.get("lines", []):
            if y < 70:
                pages.append(page)
                page, y = new_sheet_page("PAY APP LINES (continued)")
            page.text(MARGIN, y, _fit(line["label"], 100), "F1", 10)
            page.right(PAGE_W - MARGIN, y, _money(line["amount_cents"]), 10)
            y -= 16
        page.line(MARGIN, y + 8, PAGE_W - MARGIN, y + 8)
        page.text(MARGIN, y - 6, "Amount due this pay app", "F2", 10)
        page.right(PAGE_W - MARGIN, y - 6, _money(invoice["amount_cents"]), 10)
        pages.append(page)

    packet = [item for item in invoice.get("packet") or [] if item.get("required")]
    if packet:
        # Packet checklist on its own page so it never collides with the sheet.
        page, y = new_sheet_page("PACKET CHECKLIST")
        for item in packet:
            page.text(MARGIN, y, ("[x] " if item.get("present") else "[  ] ") + _fit(item["label"], 110), "F1", 10)
            y -= 15
            if y < 60:
                break
        pages.append(page)

    for n, page in enumerate(pages, start=1):
        footer(page, n, len(pages))
    return _build(pages)
