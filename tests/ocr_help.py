"""Stand-ins for tesseract and pdftoppm, so OCR's tests run where neither is installed (CI has
none). Each is a small shell script in a folder of its own; files beside it say what it does and
keep what it was called with. Not a test module; the tests import it."""

import os
import stat
from pathlib import Path

# Prints the folder's `text` file. `mode`: hang (starts a child, then sleeps), flood (prints
# without end), fail (exit 1), fail2 (the second call fails), slow (0.3 s, and notes a second one
# running at the same time).
TESSERACT = r"""#!/bin/sh
here=$(dirname "$0")
printf '%s\n' "$*" >> "$here/tesseract.calls"
env > "$here/tesseract.env"
mode=$(cat "$here/mode" 2>/dev/null)
calls=$(wc -l < "$here/tesseract.calls")
case "$mode" in
  hang) sleep 300 & echo $! > "$here/child.pid"; sleep 300 ;;
  flood) exec yes AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA ;;
  fail) echo "boom" >&2; exit 1 ;;
  fail2) if [ "$calls" -eq 2 ]; then exit 1; fi; cat "$here/text" ;;
  slow) if ! mkdir "$here/busy" 2>/dev/null; then echo overlap >> "$here/overlap"; fi; sleep 0.3; rmdir "$here/busy" 2>/dev/null; cat "$here/text" ;;
  *) cat "$here/text" ;;
esac
"""

# Writes a 2 x 2 gray image where pdftoppm would, for pages up to the number in `pages` (1 when
# there is no such file); past it, pdftoppm's own message and exit code. `render`: hang, huge (an
# image whose header claims 30,000 x 30,000), fail (exit 1, nothing written), chatty (20 KB of
# warnings, then the page).
PDFTOPPM = r"""#!/bin/sh
here=$(dirname "$0")
printf '%s\n' "$*" >> "$here/pdftoppm.calls"
page=1; prev=""; root=""
for a in "$@"; do
  if [ "$prev" = "-f" ]; then page=$a; fi
  prev=$a; root=$a
done
pages=$(cat "$here/pages" 2>/dev/null || echo 1)
if [ "$page" -gt "$pages" ]; then
  echo "Wrong page range given: the first page ($page) can not be after the last page ($pages)."
  exit 99
fi
mode=$(cat "$here/render" 2>/dev/null)
case "$mode" in
  hang) sleep 300 ;;
  fail) echo "Syntax Error: Couldn't read xref table"; exit 1 ;;
  chatty) i=0; while [ $i -lt 400 ]; do echo "Syntax Warning: Invalid Font Weight on this page of the scan" >&2; i=$((i+1)); done
          printf 'P5\n2 2\n255\n\000\000\000\000' > "$root.pgm" ;;
  huge) printf 'P5\n30000 30000\n255\n' > "$root.pgm" ;;
  *) printf 'P5\n2 2\n255\n\000\000\000\000' > "$root.pgm" ;;
esac
"""

# A scan: a PDF with a picture in it and no text. The stand-ins never open it.
SCAN = (b"%PDF-1.4\n1 0 obj\n<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 4 >>\n"
        b"stream\n\x00\x00\x00\x00\nendstream\nendobj\n%%EOF\n")
WAIVER_TEXT = ("CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT\n\nProject: Elm addition\n"
               "Upon receipt of payment the undersigned waives and releases its lien rights on the job.\nSigned ____\n")


def text_pdf(sentence: str = "The quick brown fox jumps over the lazy dog, again and again and again.") -> bytes:
    """A PDF today's reader gets text from."""
    stream = f"BT ({sentence}) Tj ET".encode()
    return b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream\nendobj\n%%EOF\n"


def png_header(width: int, height: int) -> bytes:
    """The start of a PNG that says it is this big (nothing behind it to decode)."""
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x00\x00\x00\x00" + b"\x00" * 4


def jpeg_header(width: int, height: int, components: int = 3, orientation: int = 0) -> bytes:
    """A JPEG's headers saying it is this big, with an Exif orientation when given (no picture behind them)."""
    out = b"\xff\xd8"
    if orientation:
        tiff = b"MM\x00\x2a\x00\x00\x00\x08" + (1).to_bytes(2, "big") + (0x0112).to_bytes(2, "big") + (3).to_bytes(2, "big") + (1).to_bytes(4, "big") \
            + orientation.to_bytes(2, "big") + b"\x00\x00" + b"\x00\x00\x00\x00"
        body = b"Exif\x00\x00" + tiff
        out += b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body
    frame = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + bytes([components]) + b"\x01\x11\x00" * components
    return out + b"\xff\xc0" + (len(frame) + 2).to_bytes(2, "big") + frame + b"\xff\xd9"


class StandIns:
    """The two programs in `folder`, and the settings that point OCR at them."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        for name, body in (("tesseract", TESSERACT), ("pdftoppm", PDFTOPPM)):
            path = self.folder / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.say(WAIVER_TEXT)

    def env(self, **more: str) -> dict:
        return {"OCR_ENABLED": "1", "OCR_TESSERACT": str(self.folder / "tesseract"), "OCR_PDFTOPPM": str(self.folder / "pdftoppm"), **more}

    def say(self, text: str) -> None:
        (self.folder / "text").write_text(text)

    def set(self, name: str, value) -> None:
        """mode (tesseract), render or pages (pdftoppm); a blank value takes it away."""
        path = self.folder / name
        if value in ("", None):
            if path.exists():
                path.unlink()
        else:
            path.write_text(str(value))

    def calls(self, program: str) -> list:
        path = self.folder / f"{program}.calls"
        return path.read_text().splitlines() if path.exists() else []

    def forget_calls(self) -> None:
        for program in ("tesseract", "pdftoppm"):
            path = self.folder / f"{program}.calls"
            if path.exists():
                path.unlink()

    def seen_env(self) -> dict:
        lines = (self.folder / "tesseract.env").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)


def clean_env() -> dict:
    """os.environ without any OCR setting, for mock.patch.dict(..., clear=True)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("OCR_")}
