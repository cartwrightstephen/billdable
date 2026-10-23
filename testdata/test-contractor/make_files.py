"""Regenerate the binary and .eml test files for the Brightwater Tile & Stone test contractor.

Stdlib only. Run from anywhere:

    python3 testdata/test-contractor/make_files.py

Writes the PDFs, the photo (PNG, converted to JPEG with macOS `sips` when available; the
committed JPEG is kept otherwise) and the saved emails under import/ and bad/. The plain-text
files (CSV, SOV pastes, notes, text thread) are written by hand and are not touched here.
Everything is fictional: names, companies, addresses, *.example email domains, 404-555-01xx phones.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
import zlib
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMPORT = HERE / "import"
BAD = HERE / "bad"


# ---------- tiny PDF writer ----------

def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines: list[str]) -> bytes:
    """A valid one-page PDF (Helvetica text). Byte offsets in the xref table are exact."""
    text = ["BT", "/F1 11 Tf", "14 TL", "54 760 Td"]
    for line in lines:
        text.append(f"({_pdf_escape(line)}) Tj T*")
    text.append("ET")
    stream = "\n".join(text).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---------- tiny PNG writer ----------

def make_png(width: int = 160, height: int = 120) -> bytes:
    """A tile-grid picture: grey grout lines on warm porcelain squares."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            if x % 40 < 2 or y % 40 < 2:
                raw += bytes((110, 110, 115))
            else:
                shade = 200 + ((x // 40 + y // 40) % 2) * 25
                raw += bytes((shade, shade - 18, shade - 40))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def make_jpeg(dest: Path) -> bytes | None:
    """Convert the PNG to JPEG with sips (macOS). Returns None when sips is missing."""
    if not shutil.which("sips"):
        return dest.read_bytes() if dest.exists() else None
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "in.png"
        jpg = Path(tmp) / "out.jpg"
        png.write_bytes(make_png())
        subprocess.run(["sips", "-s", "format", "jpeg", str(png), "--out", str(jpg)], check=True, capture_output=True)
        return jpg.read_bytes()


# ---------- emails ----------

def eml(sender: str, to: str, subject: str, date: str, body: str, html: bool = False,
        attachments: list[tuple[str, bytes, str, str]] | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = date
    msg["Message-ID"] = make_msgid(domain="mail.brightwatertile.example")
    if html:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    for filename, data, maintype, subtype in attachments or []:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return bytes(msg)


OFFICE = "Carla Jennings <office@brightwatertile.example>"


def main() -> None:
    IMPORT.mkdir(exist_ok=True)
    BAD.mkdir(exist_ok=True)

    contract_pdf = make_pdf([
        "SUBCONTRACT AGREEMENT  No. CCG-SUB-2291",
        "",
        "Contractor: Wrenfield Construction Group, 900 Founders Row, Suite 300, Marietta, GA 30060",
        "Subcontractor: Brightwater Tile & Stone LLC, 2480 Cobb Industrial Ct, Smyrna, GA 30080",
        "Project: Kennesaw Ortho Clinic, 3895 Cherokee St NW, Kennesaw, GA 30144",
        "",
        "Scope: furnish and install porcelain floor tile, restroom wall tile, and stone sills",
        "per drawings A-401 to A-412 and spec section 09 30 00.",
        "",
        "Subcontract sum: $92,400.00    Retainage: 10% until closeout.",
        "Pay applications: AIA-style, due the 25th, with conditional lien waiver and",
        "insurance certificate. Punch-list signoff required before retainage release.",
        "",
        "Signed for Wrenfield: Joan Pettis, Project Accountant     Date: 09/29/2026",
        "Signed for Brightwater: Nadia Brooks, Owner               Date: 09/30/2026",
        "",
        "FICTIONAL TEST DOCUMENT - Duracash test data. Not a real agreement.",
    ])
    (IMPORT / "13 Subcontract KENN Kennesaw Ortho Clinic.pdf").write_bytes(contract_pdf)

    waiver_pdf = make_pdf([
        "CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT",
        "",
        "Project: Decatur Library Annex, 215 Sycamore St, Decatur, GA 30030",
        "Pay application: PA-DECA-01",
        "Claimant: Brightwater Tile & Stone LLC",
        "Customer: Wrenfield Construction Group",
        "Amount of payment: $57,600.00",
        "",
        "Signed: Nadia Brooks, Owner     Date: 10/15/2026",
        "",
        "FICTIONAL TEST DOCUMENT - Duracash test data. Not a legal form.",
    ])

    jpg_path = IMPORT / "14 photo MILL lobby floor IMG_2231.jpg"
    jpg = make_jpeg(jpg_path)
    if jpg:
        jpg_path.write_bytes(jpg)

    (IMPORT / "05_email_co-request-from-gc-super.eml").write_bytes(eml(
        "Travis Mendez <tmendez@harmonvale.example>", OFFICE,
        "Brookhaven Townhomes Bldg C - change order request, shower niches",
        "Mon, 19 Oct 2026 07:42:00 -0400",
        "Carla, Nadia,\n\n"
        "Owner walked units C4 through C6 Friday and wants a recessed shower niche added in each\n"
        "primary bath, 12x24, same porcelain as the walls with a stone shelf.\n\n"
        "Please price it as a change order: 3 niches. Our estimate is $2,340.00 total but send\n"
        "your number. Need it back by Thursday so it rides with our CO log to the owner.\n\n"
        "Travis Mendez\nSuperintendent, Harmon & Vale Construction\n404-555-0158\n",
    ))

    (IMPORT / "06_email_signed-waiver-pdf-attached.eml").write_bytes(eml(
        "Nadia Brooks <nadia@brightwatertile.example>", OFFICE,
        "Signed conditional waiver for PA-DECA-01",
        "Thu, 15 Oct 2026 16:05:00 -0400",
        "Carla,\n\nSigned and scanned. File it on the library job so it goes with the next send.\n\nNadia\n",
        attachments=[("Conditional-Waiver-PA-DECA-01-signed.pdf", waiver_pdf, "application", "pdf")],
    ))

    (IMPORT / "07_email_payer-says-backup-missing.eml").write_bytes(eml(
        "Harmon & Vale Payables <payables@harmonvale.example>", OFFICE,
        "RE: Pay application PA-VEST-01 — Vesta Senior Living Phase 2",
        "Tue, 20 Oct 2026 10:12:00 -0400",
        "Hi Carla,\n\n"
        "We can't process PA-VEST-01 yet. It is missing the certified payroll backup for the\n"
        "September period. Please send it and we will put the pay app in the next check run.\n\n"
        "Thanks,\nHarmon & Vale Payables\n\n"
        "On Thu, Oct 15, 2026 at 9:02 AM Brightwater Tile & Stone wrote:\n"
        "> Please find pay application PA-VEST-01 attached.\n",
    ))

    photo = jpg or make_png()
    photo_name, photo_sub = ("IMG_2240.jpg", "jpeg") if jpg else ("IMG_2240.png", "png")
    (IMPORT / "08_email_photo-attachment-from-foreman.eml").write_bytes(eml(
        "Luis Ortega <luis@brightwatertile.example>", OFFICE,
        "MILL lobby - floor layout photo",
        "Wed, 21 Oct 2026 13:27:00 -0400",
        "Layout is snapped for the lobby. Picture attached so Tallow Creek can see the pattern\n"
        "before we start setting tomorrow.\n\nLuis\n",
        attachments=[(photo_name, photo, "image", photo_sub)],
    ))

    (IMPORT / "09_email_matches-no-job.eml").write_bytes(eml(
        "Peach State Tile Supply <orders@peachstatetilesupply.example>", OFFICE,
        "Fall pricing on thinset, grout and LFT mortar",
        "Fri, 16 Oct 2026 08:00:00 -0400",
        "Hello from Peach State Tile Supply,\n\n"
        "Fall pricing is in. Polymer thinset is $18.75 a bag through November and\n"
        "large-format mortar is $24.10. Sanded grout in 25 colors is in stock at both yards.\n\n"
        "Reply to reserve pallets.\n",
    ))

    (IMPORT / "10_email_portal-notice-html-only.eml").write_bytes(eml(
        "Corbel Street Vendor Portal <noreply@portal.corbelstreet.example>", OFFICE,
        "Invoice PA-ASHB-01 approved for payment",
        "Wed, 21 Oct 2026 18:30:00 -0400",
        "<html><body><p>Hello Brightwater Tile &amp; Stone LLC,</p>"
        "<p>Your invoice <b>PA-ASHB-01</b> for <b>$36,020.00</b> was approved in the vendor portal "
        "and is scheduled for the next ACH run.</p>"
        "<table><tr><td>Property</td><td>Ashby Lofts</td></tr><tr><td>Status</td><td>Approved</td></tr></table>"
        "<p>This is an automated message from Corbel Street Properties.</p></body></html>",
        html=True,
    ))

    (IMPORT / "16_inbound-webhook_crew-says-glenwood-finished.eml").write_bytes(eml(
        "Luis Ortega <luis@brightwatertile.example>", "jobs@brightwatertile.example",
        "Glenwood wrapped",
        "Wed, 21 Oct 2026 17:48:00 -0400",
        "Glenwood Retail Shell is finished.\n"
        "Ironleaf walked it with us this afternoon and signed off. Tools are out.\n",
    ))

    zip_bytes = b"PK\x05\x06" + b"\x00" * 18  # an empty zip archive
    (BAD / "bad_05_email-with-zip-attachment.eml").write_bytes(eml(
        "Alana Grant <alana.grant@corbelstreet.example>", OFFICE,
        "Ashby Lofts Amenity Deck - revised paver drawings",
        "Mon, 19 Oct 2026 11:15:00 -0400",
        "Revised paver layout drawings attached as a zip, and the color schedule as a PDF.\n\nAlana\n",
        attachments=[
            ("Ashby-paver-drawings-rev3.zip", zip_bytes, "application", "zip"),
            ("Ashby-color-schedule.pdf", make_pdf(["Ashby Lofts Amenity Deck - paver color schedule", "Field: Travertine Ivory", "Border: Noce"]), "application", "pdf"),
        ],
    ))
    print("Wrote PDFs, photo and emails under", HERE)


if __name__ == "__main__":
    main()
