#!/usr/bin/env python3
"""Measure what reading scans costs on this machine (ocr.py), with the real tesseract and pdftoppm.

    python3 scripts/ocr_measure.py make-docs FOLDER            draw the documents (needs Pillow)
    python3 scripts/ocr_measure.py run FOLDER [--label TEXT]   read them and print the numbers (stdlib only)

`make-docs` draws, with made-up words: a one-page letter scan at 200 dpi, a ten-page contract
scan, and a 12-megapixel phone photo stored sideways with its orientation tag. Pillow draws them
and is needed for nothing else; the app never imports it. `run` reads each one through ocr.py in
a process of its own and prints seconds and the peak memory of the programs it ran
(resource.getrusage(RUSAGE_CHILDREN)): the letter, the ten pages, and the photo three ways (handed
to tesseract as it is, through ocr.py with the pixel cap lifted, and with the cap). Then the
smallest OCR_MEMORY_MB at which a page still reads (where the platform enforces it), and a real
server: its memory when idle, a scan uploaded to it and read by its worker, and its memory after.

Exit code 0 when every document was read, 1 otherwise. The CI job `ocr` (.github/workflows/tests.yml)
runs it on the runner and again in the production image held to 512 MB and half a CPU.
"""

from __future__ import annotations

import io
import json
import os
import platform
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WAIVER = ["CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT", "", "Project: Elm addition", "Owner: Park GC", "",
          "Upon receipt of a check in the sum of $4,500.00 payable to Pine Co,", "this document becomes effective to release any lien rights",
          "the undersigned has on the job of Park GC located at 9 Elm Street.", "", "Date: October 12, 2026", "Signature: ______________________"]
CONTRACT = ["SUBCONTRACT AGREEMENT", "", "Project: Elm addition", "Contractor: Park GC", "",
            "The Contract Sum is $125,000.00.", "Retainage of 10% shall be withheld from each payment.", ""]
FILLER = "The Subcontractor shall perform all work described in the documents."


# ---------- the documents (Pillow; only here and in the tests that skip without it) ----------

def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)   # Pillow's own scalable font: no system font needed
    except TypeError:
        return ImageFont.truetype("DejaVuSans.ttf", size)


def page_image(lines: list, width: int = 1700, height: int = 2200, size: int = 36):
    """Black words on a white page, as a scanner would give it: a gray image."""
    from PIL import Image, ImageDraw
    image = Image.new("L", (width, height), 255)
    draw, font, y = ImageDraw.Draw(image), _font(size), int(height * 0.07)
    for line in lines:
        draw.text((int(width * 0.09), y), line, fill=0, font=font)
        y += int(size * 1.6)
    return image


def scan_pdf(pages: list, dpi: int = 200) -> bytes:
    """A scanned PDF: each page is a picture, with no text in the file."""
    images = [page.convert("RGB") for page in pages]
    out = io.BytesIO()
    images[0].save(out, "PDF", resolution=dpi, save_all=True, append_images=images[1:])
    return out.getvalue()


def contract_pages(count: int) -> list:
    return [page_image(CONTRACT + [f"Page {n + 1} of {count}.", ""] + [FILLER] * 30) for n in range(count)]


def phone_photo(lines: list, sideways: bool = True) -> bytes:
    """A 12-megapixel JPEG of a page, stored on its side with the orientation tag a phone writes
    (6: turn it a quarter clockwise to see it upright)."""
    from PIL import Image
    upright = page_image(lines, 3024, 4032, 64).convert("RGB")
    out = io.BytesIO()
    if not sideways:
        upright.save(out, "JPEG", quality=88)
        return out.getvalue()
    exif = Image.Exif()
    exif[0x0112] = 6
    upright.rotate(90, expand=True).save(out, "JPEG", quality=88, exif=exif)
    return out.getvalue()


def make_docs(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "letter.pdf").write_bytes(scan_pdf([page_image(WAIVER + [""] + [FILLER] * 20)]))
    (folder / "contract10.pdf").write_bytes(scan_pdf(contract_pages(10)))
    (folder / "photo12.jpg").write_bytes(phone_photo(WAIVER))
    for path in sorted(folder.iterdir()):
        print(f"{path.name}: {path.stat().st_size / 1024:.0f} KB")


# ---------- one reading, in a process of its own ----------

def _child_peak_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)   # macOS counts bytes, Linux kilobytes


def one(path: Path, direct: bool) -> dict:
    """Read one document and say what it took. direct: hand it to tesseract as it is, outside ocr.py."""
    os.environ["DURACASH_DATA_DIR"] = tempfile.mkdtemp(prefix="ocr-measure-")
    os.environ.setdefault("OCR_ENABLED", "1")
    sys.path.insert(0, str(ROOT))
    import importer
    import ocr
    try:
        started = time.monotonic()
        if direct:
            done = subprocess.run([ocr.programs()[0], str(path), "stdout"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env={"PATH": "/usr/bin:/bin", "OMP_THREAD_LIMIT": "1", **({"TESSDATA_PREFIX": os.environ["TESSDATA_PREFIX"]} if os.environ.get("TESSDATA_PREFIX") else {})})
            got = {"state": "read" if done.returncode == 0 else "unread", "reason": "", "pages_read": 1, "chars": len(done.stdout.decode("utf-8", "replace").strip())}
        else:
            meta = importer.store_bytes("co-measure", path.name, path.read_bytes(), "measure")
            if not ocr.enqueue("co-measure", meta):
                return {"state": "not queued", "reason": f"OCR is {ocr.status()}", "pages_read": 0, "chars": 0, "seconds": 0, "peak_mb": 0}
            ocr.run_waiting()
            entry = ocr.queue("co-measure")[0]
            got = {"state": entry["state"], "reason": entry["reason"], "pages_read": entry["pages_read"], "chars": len(ocr.text_for("co-measure", meta["id"]))}
        return {**got, "seconds": round(time.monotonic() - started, 2), "peak_mb": _child_peak_mb()}
    finally:
        shutil.rmtree(os.environ["DURACASH_DATA_DIR"], ignore_errors=True)


def measure(path: Path, direct: bool = False, **settings: str) -> dict:
    """one() in a new Python, so the peak is this document's alone."""
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("OCR_") or k in ("OCR_TESSERACT", "OCR_PDFTOPPM")}, "OCR_ENABLED": "1", **settings}
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "one", str(path), "direct" if direct else "ocr"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(ROOT))
    try:
        return json.loads(done.stdout.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"state": "failed", "reason": done.stderr.decode("utf-8", "replace")[-400:], "pages_read": 0, "chars": 0, "seconds": 0, "peak_mb": 0}


# ---------- a real server ----------

def _rss_mb(pid: int) -> tuple:
    """(memory now, the most it has been) of a process, in MB; the second is 0 where it can't be read."""
    try:
        fields = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
        return round(int(fields["VmRSS"].split()[0]) / 1024, 1), round(int(fields["VmHWM"].split()[0]) / 1024, 1)
    except (OSError, KeyError, ValueError):
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], stdout=subprocess.PIPE).stdout.decode().strip()
        return (round(int(out) / 1024, 1) if out.isdigit() else 0.0), 0.0


def _call(url: str, data: bytes | None = None, headers: dict | None = None) -> tuple:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read() or b"{}"), res.headers.get("Set-Cookie", "")


def server_run(scan: Path) -> dict:
    """Start server.py on a fresh data directory with OCR on, note its memory, upload one scan to
    its Inbox, wait for its worker to read it, and note its memory again."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    data_dir = tempfile.mkdtemp(prefix="ocr-measure-server-")
    env = {**os.environ, "DURACASH_DATA_DIR": data_dir, "HOST": "127.0.0.1", "PORT": str(port), "BACKUP_HOURS": "0", "OCR_ENABLED": "1"}
    env.pop("DURACASH_ENV", None)
    log = open(Path(data_dir) / "server.log", "wb")
    server = subprocess.Popen([sys.executable, "server.py"], cwd=str(ROOT), env=env, stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    out: dict = {"state": "the server didn't start"}
    try:
        for _ in range(60):
            try:
                health, _ = _call(base + "/healthz")
                break
            except OSError:
                time.sleep(0.5)
        else:
            return out
        out["ocr"] = health.get("ocr")
        for _ in range(5):
            _call(base + "/healthz")
            urllib.request.urlopen(base + "/", timeout=30).read()
        time.sleep(1)
        out["idle_mb"] = _rss_mb(server.pid)[0]
        _, cookie = _call(base + "/api/auth/signup", json.dumps({"accept_terms": True, "email": "measure@example.com", "password": "correct horse battery",
                                                                  "name": "Measure", "company_name": "Measure Co"}).encode(), {"Content-Type": "application/json"})
        cookie = cookie.split(";")[0]
        started = time.monotonic()
        _call(base + "/api/import/file", scan.read_bytes(), {"Cookie": cookie, "X-Filename": scan.name, "Content-Type": "application/octet-stream"})
        out["state"] = "still waiting after 300 seconds"
        while time.monotonic() - started < 300:
            if _call(base + "/healthz")[0].get("ocr_queue") == 0:
                break
            time.sleep(0.25)
        out["seconds"] = round(time.monotonic() - started, 1)
        queues = list(Path(data_dir).glob("uploads/*/ocr-queue.json"))
        entries = json.loads(queues[0].read_text())["jobs"] if queues else []
        out["state"] = entries[0]["state"] if entries else "not queued"
        out["reason"] = entries[0]["reason"] if entries else f"OCR is {out['ocr']}"
        out["after_mb"], out["server_peak_mb"] = _rss_mb(server.pid)
        return out
    except Exception as exc:   # a refused request, a server that went away: said plainly, with its log below
        out["state"] = f"failed: {exc!r}"
        out.pop("after_mb", None)
        return out
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
        if out.get("state") != "read":
            print(Path(data_dir, "server.log").read_text(errors="replace")[-3000:])
        shutil.rmtree(data_dir, ignore_errors=True)


# ---------- the report ----------

def _version(program: str, flag: str) -> str:
    try:
        done = subprocess.run([program, flag], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        return done.stdout.decode("utf-8", "replace").strip().splitlines()[0]
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return "not found"


def _container_peak_mb() -> str:
    for name in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        try:
            return f"{int(Path(name).read_text()) / 1024 / 1024:.0f} MB"
        except (OSError, ValueError):
            continue
    return ""


def run(folder: Path, label: str) -> int:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("OCR_ENABLED", "1")
    import ocr
    tesseract, pdftoppm = ocr.programs()
    line = "=" * 100
    print(line)
    print(f"OCR MEASUREMENTS: {label}")
    print(line)
    print(f"machine     {platform.platform()}, {os.cpu_count()} CPUs seen, Python {platform.python_version()}")
    print(f"tesseract   {_version(tesseract or 'tesseract', '--version')}")
    print(f"pdftoppm    {_version(pdftoppm or 'pdftoppm', '-v')}")
    cfg = ocr.settings()
    print(f"settings    OCR_DPI={cfg['dpi']} OCR_MAX_PIXELS={cfg['max_pixels']} OCR_MAX_PAGES={cfg['max_pages']} OCR_PAGE_SECONDS={cfg['page_seconds']} OCR_MEMORY_MB={cfg['memory_mb']}")
    if ocr.status() != "ready":
        print(f"OCR is {ocr.status()}: nothing to measure.")
        return 1
    letter, contract, photo = folder / "letter.pdf", folder / "contract10.pdf", folder / "photo12.jpg"
    cases = [
        ("1-page letter scan, 200 dpi", measure(letter)),
        ("10-page contract scan, 200 dpi", measure(contract)),
        ("12 MP photo, tesseract alone, no cap", measure(photo, direct=True)),
        ("12 MP photo, ocr.py, cap lifted (13 MP)", measure(photo, OCR_MAX_PIXELS="13000000")),
        ("12 MP photo, ocr.py, cap 8 MP (default)", measure(photo)),
    ]
    print()
    print(f"{'document':<42}{'state':<10}{'pages':>6}{'chars':>8}{'seconds':>9}{'s/page':>8}{'peak MB of the programs':>26}")
    for name, got in cases:
        per_page = f"{got['seconds'] / got['pages_read']:.2f}" if got["pages_read"] else "-"
        print(f"{name:<42}{got['state']:<10}{got['pages_read']:>6}{got['chars']:>8}{got['seconds']:>9.2f}{per_page:>8}{got['peak_mb']:>26.1f}")
        if got["reason"]:
            print(f"    {got['reason']}")
    print()
    print("The smallest OCR_MEMORY_MB (address-space ceiling) at which it still reads, tried from 800 down:")
    for name, path in (("1-page letter scan", letter), ("12 MP photo, cap 8 MP", photo)):
        tried = []
        for mb in (800, 600, 400, 300, 250, 200, 150, 100, 50):
            tried.append((mb, measure(path, OCR_MEMORY_MB=str(mb))["state"] == "read"))
            if not tried[-1][1]:
                break   # a lower ceiling won't read either
        reads = [mb for mb, ok in tried if ok]
        if reads and min(reads) == 50:
            print(f"    {name:<26} read at every ceiling down to 50: the ceiling is not enforced on this platform")
        else:
            print(f"    {name:<26} {min(reads) if reads else 'not even at 800'}   (" + ", ".join(f"{mb} {'read' if ok else 'unread'}" for mb, ok in tried) + ")")
    print()
    served = server_run(letter)
    print("A real server (server.py, fresh data directory, OCR on):")
    if "idle_mb" in served:
        print(f"    idle, after a few page loads        {served['idle_mb']:.1f} MB")
    if "after_mb" in served:
        print(f"    the letter scan, uploaded to it     {served['state']} in {served['seconds']} s (upload to text stored){' - ' + served['reason'] if served.get('reason') else ''}")
        print(f"    after that read                     {served['after_mb']:.1f} MB" + (f" (the most it reached: {served['server_peak_mb']:.1f} MB)" if served["server_peak_mb"] else ""))
    else:
        print(f"    {served['state']}")
    peak = _container_peak_mb() if Path("/.dockerenv").exists() else ""
    if peak:
        print(f"    the most memory this whole run used at once (this script, the server and the programs): {peak}")
    worst = max(got["peak_mb"] for name, got in cases if "ocr.py, cap 8" in name or "scan" in name)
    if "idle_mb" in served:
        print()
        print(f"Server idle + the largest program peak with the default cap: {served['idle_mb']:.0f} + {worst:.0f} = {served['idle_mb'] + worst:.0f} MB")
    print(line)
    ok = all(got["state"] == "read" for _, got in cases) and served.get("state") == "read"
    print("RESULT: every document was read." if ok else "RESULT: something was not read; see above.")
    print(line)
    return 0 if ok else 1


def main(argv: list) -> int:
    if len(argv) == 3 and argv[1] == "make-docs":
        make_docs(Path(argv[2]))
        return 0
    if len(argv) == 4 and argv[1] == "one":
        print(json.dumps(one(Path(argv[2]), argv[3] == "direct")))
        return 0
    if len(argv) >= 3 and argv[1] == "run":
        label = argv[4] if len(argv) == 5 and argv[3] == "--label" else platform.node()
        return run(Path(argv[2]), label)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
