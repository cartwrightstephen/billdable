"""The background reader of scans and photos (ocr.py), with stand-ins for tesseract and pdftoppm
(ocr_help.py), so it runs where neither is installed: what is queued and what isn't, what a read
keeps, and what a crafted file, a hung or runaway program, a damaged queue, two uploads at once,
a restart and a deleted company come to."""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import alerts
import auth
import contract_import
import doc_check
import importer
import ocr
import storage
from ocr_help import SCAN, WAIVER_TEXT, StandIns, clean_env, jpeg_header, png_header, text_pdf


class OcrCase(unittest.TestCase):
    """A data directory of its own, OCR on, and the stand-ins as its two programs."""

    settings: dict = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stand = StandIns(self.root / "bin")
        for patch in (mock.patch.object(importer, "UPLOADS", self.root / "uploads"), mock.patch.object(storage, "DATA_DIR", self.root),
                      mock.patch.dict(os.environ, {**clean_env(), **self.stand.env(**self.settings)}, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)
        hook = ocr._AFTER
        ocr.after_read(None)
        ocr._PENDING.clear()
        self.addCleanup(ocr.after_read, hook)
        self.addCleanup(ocr._PENDING.clear)

    def store(self, name="scan.pdf", data=SCAN, company="co-a"):
        return importer.store_bytes(company, name, data, "test")

    def queued(self, name="scan.pdf", data=SCAN, company="co-a"):
        meta = self.store(name, data, company)
        self.assertTrue(ocr.enqueue(company, meta), name)
        return meta

    def entry(self, meta, company="co-a"):
        return next(j for j in ocr.queue(company) if j["file_id"] == meta["id"])

    def read(self, name="scan.pdf", data=SCAN, company="co-a"):
        meta = self.queued(name, data, company)
        ocr.run_waiting()
        return meta, self.entry(meta, company)


class OffByDefault(OcrCase):
    def test_without_the_setting_nothing_is_queued_or_used(self):
        meta, _ = self.read()
        self.assertEqual(ocr.text_for("co-a", meta["id"]), WAIVER_TEXT.strip())
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            self.assertEqual((ocr.enabled(), ocr.status(), ocr.queue_length()), (False, "off", 0))
            other = self.store("another.pdf")
            self.assertFalse(ocr.enqueue("co-a", other))
            self.assertEqual(ocr.run_waiting(), 0)
            self.assertEqual(ocr.text_for("co-a", meta["id"]), "", "text read while it was on is not used once it is off")
            self.assertIsNone(ocr.start())
        self.assertEqual([j["file_id"] for j in ocr.queue("co-a")], [meta["id"]])

    def test_on_without_the_programs_it_says_not_installed_and_queues_nothing(self):
        gone = str(self.root / "nowhere" / "tesseract")
        with mock.patch.dict(os.environ, {"OCR_TESSERACT": gone}):
            self.assertEqual((ocr.enabled(), ocr.available(), ocr.status()), (True, False, "not installed"))
            self.assertFalse(ocr.enqueue("co-a", self.store()))
            self.assertIsNone(ocr.start())
        self.assertFalse((self.root / "uploads" / "co-a" / ocr.QUEUE_NAME).exists())

    def test_ready_with_both(self):
        self.assertEqual((ocr.available(), ocr.status()), (True, "ready"))

    def test_settings_have_safe_defaults_and_bounds(self):
        self.assertEqual(ocr.settings(), {"max_pages": 10, "page_seconds": 60, "dpi": 200, "max_pixels": 8_000_000, "memory_mb": 400, "lang": "eng"})
        with mock.patch.dict(os.environ, {"OCR_MAX_PAGES": "0", "OCR_PAGE_SECONDS": "x", "OCR_DPI": "99999", "OCR_MAX_PIXELS": "-5", "OCR_LANG": "eng; rm -rf /"}):
            self.assertEqual(ocr.settings(), {"max_pages": 1, "page_seconds": 60, "dpi": 600, "max_pixels": 10_000, "memory_mb": 400, "lang": "eng"})
        with mock.patch.dict(os.environ, {"OCR_LANG": "eng+spa"}):
            self.assertEqual(ocr.settings()["lang"], "eng+spa")


class WhatIsQueued(OcrCase):
    def test_a_scan_and_a_photo_are_and_a_file_with_text_is_not(self):
        scan, photo = self.queued("scan.pdf"), self.queued("IMG_0001.png", png_header(800, 600))
        for name, data in (("typed.pdf", text_pdf()), ("notes.txt", b"plain words"), ("sheet.csv", b"a,b\n1,2\n"), ("letter.docx", b"PK not really")):
            self.assertFalse(ocr.enqueue("co-a", self.store(name, data)), name)
        self.assertEqual([(j["file_id"], j["company_id"], j["state"], j["pages_read"]) for j in ocr.queue("co-a")],
                         [(scan["id"], "co-a", "waiting", 0), (photo["id"], "co-a", "waiting", 0)])
        self.assertEqual(ocr.queue_length(), 2)

    def test_the_same_file_is_queued_once(self):
        meta = self.queued()
        self.assertFalse(ocr.enqueue("co-a", meta))
        self.assertEqual(len(ocr.queue("co-a")), 1)

    def test_the_demo_companys_files_never_are(self):
        meta = self.store(company=auth.DEMO_COMPANY_ID)
        self.assertFalse(ocr.enqueue(auth.DEMO_COMPANY_ID, meta))
        self.assertFalse((self.root / "uploads" / auth.DEMO_COMPANY_ID / ocr.QUEUE_NAME).exists())

    def test_heic_and_gif_are_recorded_as_unread_without_running_anything(self):
        heic, gif = self.queued("IMG_2.heic", b"\x00\x00\x00\x18ftypheic"), self.queued("anim.gif", b"GIF89a" + b"\x00" * 20)
        self.assertEqual(ocr.run_waiting(), 0)
        self.assertEqual([(self.entry(m)["state"], self.entry(m)["reason"]) for m in (heic, gif)],
                         [("unread", "HEIC photos aren't read."), ("unread", "GIF images aren't read.")])
        self.assertEqual((self.stand.calls("tesseract"), self.stand.calls("pdftoppm"), ocr.queue_length()), ([], [], 0))

    def test_a_file_that_is_not_there_or_has_a_made_up_id_is_refused(self):
        for meta in ({"id": "file-" + "0" * 16, "ext": ".pdf"}, {"id": "../../etc/passwd", "ext": ".pdf"}, {"id": "file-abc123", "ext": ".exe"}, {}):
            self.assertFalse(ocr.enqueue("co-a", meta), meta)
        self.assertFalse(ocr.enqueue("../co-a", self.store()))


class ARead(OcrCase):
    def test_the_text_is_kept_beside_the_file_and_the_queue_says_read(self):
        meta, entry = self.read()
        self.assertEqual({k: entry[k] for k in ("state", "reason", "pages", "pages_read", "attempts")},
                         {"state": "read", "reason": "", "pages": 1, "pages_read": 1, "attempts": 1})
        self.assertGreater(entry["seconds"], 0)
        self.assertTrue(entry["finished_at"] >= entry["started_at"] >= entry["queued_at"][:19])
        sidecar = json.loads((self.root / "uploads" / "co-a" / f"{meta['id']}.ocr.json").read_text())
        self.assertEqual((sidecar["ocr_text"], sidecar["state"], sidecar["pages_read"]), (WAIVER_TEXT.strip(), "read", 1))
        self.assertEqual(ocr.text_for("co-a", meta["id"]), WAIVER_TEXT.strip())
        self.assertEqual(ocr.queue_length(), 0)

    def test_each_page_is_rendered_alone_at_the_set_resolution_from_the_stored_path(self):
        self.stand.set("pages", 3)
        meta, entry = self.read()
        stored = str(self.root / "uploads" / "co-a" / f"{meta['id']}.pdf")
        calls = self.stand.calls("pdftoppm")
        self.assertEqual([c.split(" -gray")[0] for c in calls], ["-f 1 -l 1", "-f 2 -l 2", "-f 3 -l 3", "-f 4 -l 4"])
        for call in calls[:3]:
            self.assertIn("-singlefile -r 200 -x 0 -y 0 -W 2828 -H 2828 " + stored, call)
        self.assertEqual((entry["pages"], entry["pages_read"], len(self.stand.calls("tesseract"))), (3, 3, 3))
        self.assertEqual(ocr.text_for("co-a", meta["id"]), "\n\n".join([WAIVER_TEXT.strip()] * 3))
        self.assertIn("-l eng --dpi 200", self.stand.calls("tesseract")[0])

    def test_the_programs_get_a_minimal_environment(self):
        with mock.patch.dict(os.environ, {"SMTP_PASSWORD": "hunter2", "DURACASH_URL": "https://example.test"}):
            self.read()
        seen = self.stand.seen_env()
        self.assertEqual({k: seen[k] for k in ("PATH", "LANG", "LC_ALL", "OMP_THREAD_LIMIT")},
                         {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "OMP_THREAD_LIMIT": "1"})
        self.assertLessEqual(set(seen), {"PATH", "LANG", "LC_ALL", "OMP_THREAD_LIMIT", "HOME", "TMPDIR", "PWD", "OLDPWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING"})
        self.assertTrue(seen["HOME"] == seen["TMPDIR"] and "duracash-ocr-" in seen["HOME"])
        self.assertFalse(Path(seen["HOME"]).exists(), "the private folder is gone after the read")

    def test_a_blank_page_is_read_and_has_no_text(self):
        self.stand.say("")
        meta, entry = self.read()
        self.assertEqual((entry["state"], entry["pages_read"], ocr.text_for("co-a", meta["id"])), ("read", 1, ""))

    def test_the_text_kept_is_capped(self):
        self.stand.say("word " * 60_000)   # 300,000 characters a page
        self.stand.set("pages", 4)
        meta, entry = self.read()
        self.assertEqual(len(ocr.text_for("co-a", meta["id"])), ocr.MAX_TEXT)
        self.assertEqual(entry["pages_read"], 2, "it stops once the cap is reached")

    def test_who_listens_is_told_once_the_text_is_stored(self):
        told = []
        ocr.after_read(lambda company_id, file_id: told.append((company_id, file_id, ocr.text_for(company_id, file_id) != "", self.entry_state(file_id))))
        meta, _ = self.read()
        self.assertEqual(told, [("co-a", meta["id"], True, "reading")])

    def entry_state(self, file_id):
        return next(j["state"] for j in ocr.queue("co-a") if j["file_id"] == file_id)

    def test_a_listener_that_fails_doesnt_stop_the_read(self):
        ocr.after_read(lambda company_id, file_id: 1 / 0)
        _, entry = self.read()
        self.assertEqual(entry["state"], "read")

    def test_finished_entries_are_trimmed(self):
        with mock.patch.object(ocr, "KEEP_DONE", 3):
            metas = [self.read(f"s{n}.pdf")[0] for n in range(5)]
        self.assertEqual([j["file_id"] for j in ocr.queue("co-a")], [m["id"] for m in metas[2:]])
        self.assertTrue(all(ocr.text_for("co-a", m["id"]) for m in metas), "each file's text stays beside it")


class CraftedFiles(OcrCase):
    settings = {"OCR_MAX_PAGES": "3", "OCR_PAGE_SECONDS": "2"}

    def test_a_pdf_with_thousands_of_pages_is_read_to_the_page_cap(self):
        self.stand.set("pages", 5000)
        meta, entry = self.read()
        self.assertEqual((entry["state"], entry["pages"], entry["pages_read"], entry["reason"]), ("read", 3, 3, "Only the first 3 pages are read."))
        self.assertEqual((len(self.stand.calls("pdftoppm")), len(self.stand.calls("tesseract"))), (4, 3), "three pages and one look for a fourth")

    def test_a_pdf_of_exactly_the_cap_says_nothing_about_more(self):
        self.stand.set("pages", 3)
        self.assertEqual(self.read()[1]["reason"], "")

    def test_a_name_with_shell_characters_never_reaches_a_program(self):
        name = "$(touch pwned);`touch pwned2` && rm -rf ~ | x > y.pdf"
        here = Path.cwd()
        meta, entry = self.read(name)
        self.assertEqual(entry["state"], "read")
        said = "\n".join(self.stand.calls("pdftoppm") + self.stand.calls("tesseract"))
        self.assertIn(f"{meta['id']}.pdf", said)
        for piece in ("pwned", "touch", "rm -rf", meta["name"]):
            self.assertNotIn(piece, said)
        for folder in (here, self.root, self.root / "uploads" / "co-a", self.stand.folder):
            self.assertEqual([p.name for p in folder.glob("pwned*")], [])

    def test_a_huge_picture_is_refused_from_its_header_before_anything_decodes_it(self):
        for name, data, why in (("bomb.png", png_header(30_000, 30_000), "The picture is 900 megapixels, more than is read."),
                                ("bomb.jpg", jpeg_header(65_535, 65_535), "The photo is 4294.8 megapixels, more than is read."),
                                ("over.png", png_header(4000, 3000), "The picture is 12 megapixels, more than is read."),
                                ("not-a.png", jpeg_header(10, 10), "The picture couldn't be read."[:0] + "Not a picture this server reads."),
                                ("not-a.jpg", png_header(10, 10), "Not a photo this server reads."),
                                ("cmyk.jpg", jpeg_header(100, 100, components=4), "Not a photo this server reads."),
                                ("moving.webp", b"RIFF\x00\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00\x02\x00\x00\x00" + b"\x63\x00\x00\x63\x00\x00", "Not a picture this server reads.")):
            meta, entry = self.read(name, data)
            self.assertEqual((entry["state"], entry["reason"], entry["pages_read"]), ("unread", why, 0), name)
            self.assertEqual(ocr.text_for("co-a", meta["id"]), "")
        self.assertEqual((self.stand.calls("tesseract"), self.stand.calls("pdftoppm")), ([], []))

    def test_a_page_rendered_bigger_than_the_cap_is_not_handed_on(self):
        self.stand.set("render", "huge")
        _, entry = self.read()
        self.assertEqual((entry["state"], entry["reason"]), ("unread", "Page 1 is too large."))
        self.assertEqual(self.stand.calls("tesseract"), [])

    def test_a_program_that_hangs_is_killed_with_everything_it_started(self):
        self.stand.set("mode", "hang")
        self.stand.set("pages", 1)
        started = time.monotonic()
        meta, entry = self.read()
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual((entry["state"], entry["reason"]), ("unread", "Page 1 took longer than 2 seconds."))
        child = int((self.stand.folder / "child.pid").read_text())
        for _ in range(40):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("the child the program started is still running")

    def test_a_renderer_that_hangs_leaves_the_file_unread(self):
        self.stand.set("render", "hang")
        self.stand.set("pages", 2)
        with mock.patch.dict(os.environ, {"OCR_MAX_PAGES": "2", "OCR_PAGE_SECONDS": "1"}):
            _, entry = self.read()
        self.assertEqual((entry["state"], entry["pages_read"]), ("unread", 0))
        self.assertIn("Page 1 took longer than 1 seconds", entry["reason"])
        self.assertEqual(self.stand.calls("tesseract"), [])

    def test_a_program_that_prints_megabytes_is_stopped_at_the_bound(self):
        self.stand.set("mode", "flood")
        started = time.monotonic()
        meta, entry = self.read()
        self.assertLess(time.monotonic() - started, 2, "stopped by the bound, not by the clock")
        self.assertEqual((entry["state"], entry["reason"]), ("unread", "Page 1 printed more than a page of text can be."))
        self.assertEqual(ocr.text_for("co-a", meta["id"]), "")

    def test_a_program_that_fails_leaves_the_file_unread_and_raises_nothing(self):
        self.stand.set("mode", "fail")
        meta, entry = self.read("IMG_7.png", png_header(640, 480))
        self.assertEqual((entry["state"], entry["reason"]), ("unread", "The picture couldn't be read."))

    def test_one_failed_page_is_named_and_the_rest_are_read(self):
        self.stand.set("mode", "fail2")
        self.stand.set("pages", 3)
        meta, entry = self.read()
        self.assertEqual((entry["state"], entry["pages"], entry["pages_read"], entry["reason"]), ("read", 3, 2, "Page 2 couldn't be read."))

    def test_a_renderer_full_of_warnings_still_gives_its_page(self):
        self.stand.set("render", "chatty")
        _, entry = self.read()
        self.assertEqual((entry["state"], entry["pages_read"], entry["reason"]), ("read", 1, ""))

    def test_a_pdf_the_renderer_cant_open_is_unread(self):
        self.stand.set("render", "fail")
        _, entry = self.read()
        self.assertEqual((entry["state"], entry["reason"]), ("unread", "Page 1 couldn't be read."))
        self.assertEqual(len(self.stand.calls("pdftoppm")), 2, "two pages in a row with nothing is the end")

    def test_a_big_photo_is_shrunk_by_the_renderer_and_turned_upright(self):
        meta, entry = self.read("IMG_12MP.jpg", jpeg_header(4032, 3024, orientation=6))
        self.assertEqual((entry["state"], entry["pages_read"]), ("read", 1))
        call = self.stand.calls("pdftoppm")[0]
        self.assertIn("-r 72 -scale-to 3265 ", call)
        self.assertIn("photo.pdf", call)
        self.assertNotIn("uploads", call, "the renderer gets the one-page PDF made for it, in the private folder")
        self.assertTrue(self.stand.calls("tesseract")[0].split(" ")[0].endswith("page.pgm"))

    def test_a_photos_size_and_turn_come_from_its_headers(self):
        self.assertEqual(ocr._jpeg_info(jpeg_header(4032, 3024, orientation=6)), (4032, 3024, 3, 90))
        self.assertEqual(ocr._jpeg_info(jpeg_header(640, 480, components=1, orientation=3)), (640, 480, 1, 180))
        self.assertEqual(ocr._jpeg_info(jpeg_header(640, 480)), (640, 480, 3, 0))
        for junk in (b"", b"\xff\xd8", b"\xff\xd8\xff", b"\xff\xd8\xff\xc0\x00", b"\xff\xd8\xff\xe1\xff\xff", b"\xff\xd8" + b"\x00" * 50, png_header(5, 5)):
            self.assertIsNone(ocr._jpeg_info(junk))
        pdf = ocr._jpeg_as_pdf(b"JPEGBYTES", 4032, 3024, 3, 90)
        self.assertTrue(pdf.startswith(b"%PDF-1.4") and b"/MediaBox [0 0 4032 3024] /Rotate 90" in pdf and b"/DCTDecode /Length 9 >>\nstream\nJPEGBYTES\nendstream" in pdf)
        self.assertEqual((ocr._png_size(png_header(7, 9)), ocr._png_size(b"\x89PNG\r\n\x1a\n")), ((7, 9), None))
        lossy = b"RIFF\x00\x00\x00\x00WEBPVP8 \x00\x00\x00\x00\x00\x00\x00\x9d\x01\x2a\x80\x02\xe0\x01"
        self.assertEqual(ocr._webp_size(lossy), (640, 480))


class ADamagedQueue(OcrCase):
    def test_it_is_set_aside_with_an_alert_and_nothing_else_is_lost(self):
        first, _ = self.read("first.pdf")
        path = self.root / "uploads" / "co-a" / ocr.QUEUE_NAME
        for damage in ("{not json", json.dumps({"jobs": [{"file_id": "../../x", "ext": ".pdf", "state": "waiting"}]}), json.dumps([1, 2])):
            path.write_text(damage)
            with mock.patch.object(alerts, "notify") as notify:
                second = self.queued(f"after-{len(damage)}.pdf")
            self.assertEqual(notify.call_count, 1)
            self.assertIn("ocr-queue:co-a", notify.call_args[0][0])
            self.assertIn("backfill --company co-a", notify.call_args[0][1])
            aside = [p for p in path.parent.glob(f"{ocr.QUEUE_NAME}.bad-*") if p.read_text() == damage]
            self.assertEqual(len(aside), 1, "the damaged file is kept for a person to look at")
            self.assertEqual([j["file_id"] for j in ocr.queue("co-a")], [second["id"]])
            self.assertEqual(ocr.text_for("co-a", first["id"]), WAIVER_TEXT.strip(), "text already read is still there")
            self.assertTrue((path.parent / f"{first['id']}.pdf").is_file())
            ocr.run_waiting()
            self.assertEqual(self.entry(second)["state"], "read")

    def test_reading_the_queue_for_healthz_never_raises(self):
        self.queued()
        (self.root / "uploads" / "co-a" / ocr.QUEUE_NAME).write_bytes(b"\xff\xfe\x00garbage")
        with mock.patch.object(alerts, "notify"):
            self.assertEqual(ocr.queue_length(), 0)


class TwoAtOnce(OcrCase):
    def test_uploads_at_the_same_moment_are_all_queued_and_read_one_at_a_time(self):
        self.stand.set("mode", "slow")
        metas = [self.store(f"s{n}.pdf", company=f"co-{'ab'[n % 2]}") for n in range(8)]
        threads = [threading.Thread(target=ocr.enqueue, args=(f"co-{'ab'[n % 2]}", meta)) for n, meta in enumerate(metas)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(j["file_id"] for c in ("co-a", "co-b") for j in ocr.queue(c)), sorted(m["id"] for m in metas), "no entry lost")
        self.assertEqual(ocr.queue_length(), 8)
        readers = [threading.Thread(target=ocr.run_waiting) for _ in range(3)]
        for t in readers:
            t.start()
        for t in readers:
            t.join()
        self.assertFalse((self.stand.folder / "overlap").exists(), "two files were being read at once")
        self.assertEqual({j["state"] for c in ("co-a", "co-b") for j in ocr.queue(c)}, {"read"})
        self.assertEqual(len(self.stand.calls("tesseract")), 8, "each file is read once")


class ARestart(OcrCase):
    def as_if_restarted(self):
        ocr._PENDING.clear()
        ocr._rescan()

    def test_what_was_waiting_is_read_after_a_restart(self):
        meta = self.queued()
        ocr._PENDING.clear()   # a new process knows nothing but the disk
        self.assertEqual(ocr.run_waiting(), 0)
        self.as_if_restarted()
        self.assertEqual((ocr.queue_length(), ocr.run_waiting(), self.entry(meta)["state"]), (1, 1, "read"))

    def test_a_file_being_read_when_the_server_stopped_is_read_again(self):
        meta = self.queued()
        job = ocr._take()
        self.assertEqual((job["file_id"], self.entry(meta)["state"], self.entry(meta)["attempts"]), (meta["id"], "reading", 1))
        ocr._rescan()
        self.assertEqual(self.entry(meta)["state"], "reading", "the one in hand is left alone by the periodic look at the disk")
        ocr._NOW = None   # the server stopped mid-read
        self.as_if_restarted()
        self.assertEqual(self.entry(meta)["state"], "waiting")
        self.assertEqual(ocr.run_waiting(), 1)
        self.assertEqual((self.entry(meta)["state"], self.entry(meta)["attempts"], ocr.text_for("co-a", meta["id"])), ("read", 2, WAIVER_TEXT.strip()))

    def test_a_file_the_server_keeps_stopping_on_is_given_up(self):
        meta = self.queued()
        for _ in range(ocr.MAX_ATTEMPTS):
            self.assertIsNotNone(ocr._take())
            ocr._NOW = None
            self.as_if_restarted()
        self.assertEqual(ocr.run_waiting(), 0)
        entry = self.entry(meta)
        self.assertEqual((entry["state"], entry["reason"]), ("unread", "The server stopped 3 times while reading it."))
        self.assertEqual(self.stand.calls("tesseract"), [])

    def test_the_worker_thread_reads_what_is_queued(self):
        thread = ocr.start()
        self.addCleanup(ocr.stop)
        self.assertIs(ocr.start(), thread, "one worker, however often it is started")
        meta = self.queued()
        for _ in range(100):
            if self.entry(meta)["state"] == "read":
                break
            time.sleep(0.05)
        self.assertEqual(self.entry(meta)["state"], "read")


class OneCompanyAtATime(OcrCase):
    def test_text_is_reachable_only_with_its_own_companys_id(self):
        meta, _ = self.read(company="co-a")
        self.store(company="co-b")
        self.assertEqual(ocr.text_for("co-a", meta["id"]), WAIVER_TEXT.strip())
        for company in ("co-b", "co-b/../co-a", "../uploads/co-a", "", "co-a/", ".", "co-a\x00"):
            self.assertEqual(ocr.text_for(company, meta["id"]), "", company)
        for file_id in ("../co-a/" + meta["id"], meta["id"] + "/..", "ocr-queue", "", None, meta["id"] + ".ocr"):
            self.assertEqual(ocr.text_for("co-b", file_id), "", file_id)
        self.assertEqual(ocr.text_for_bytes("co-b", [meta], meta["sha256"]), "", "another company's list of files doesn't open this one's text")
        self.assertEqual(ocr.text_for_bytes("co-a", [meta], meta["sha256"]), WAIVER_TEXT.strip())
        self.assertEqual(ocr.text_for_bytes("co-a", [meta], "0" * 64), "")
        self.assertEqual([j["company_id"] for j in ocr.queue("co-a")], ["co-a"])
        self.assertEqual(ocr.queue("co-b"), [])

    def test_a_company_deleted_while_its_file_waits_gets_nothing_written_back(self):
        self.queued()
        folder = self.root / "uploads" / "co-a"
        shutil.rmtree(folder)
        self.assertEqual(ocr.run_waiting(), 0)
        self.assertFalse(folder.exists())

    def test_a_company_deleted_while_its_file_is_read_gets_nothing_written_back(self):
        meta = self.queued()
        folder = self.root / "uploads" / "co-a"
        real = ocr._read_pdf

        def deleted_meanwhile(path, cfg, tmp):
            got = real(path, cfg, tmp)
            shutil.rmtree(folder)
            return got

        with mock.patch.object(ocr, "_read_pdf", deleted_meanwhile):
            ocr.run_waiting()
        self.assertFalse(folder.exists())

        # The folder is still there (the deletion was slower than the write): the text goes with the file.
        meta = self.queued()

        def file_went(path, cfg, tmp):
            got = real(path, cfg, tmp)
            for p in folder.iterdir():
                if p.name != "keep.txt":
                    p.unlink()
            return got

        (folder / "keep.txt").write_text("another file")
        with mock.patch.object(ocr, "_read_pdf", file_went):
            ocr.run_waiting()
        self.assertEqual([p.name for p in folder.iterdir()], ["keep.txt"])


class TheReadersUseIt(unittest.TestCase):
    """doc_check, contract_import and the Inbox's kind guess take stored text only when their own reading finds none."""

    def never(self):
        self.fail("the stored text was asked for though the file has its own")

    def prepare(self, ext, data, stored):
        return doc_check.with_stored_text(doc_check.prepare(ext, data), ext, stored)

    def test_a_photo_with_stored_text_is_checked_like_any_file_with_text(self):
        read = self.prepare(".jpg", b"", lambda: WAIVER_TEXT)
        self.assertEqual((read.text, read.ocr, read.failed), (WAIVER_TEXT, True, False))
        self.assertEqual(doc_check.check([("w.jpg", ".jpg", read)], "Elm addition"), {"status": "matched", "problems": []})
        self.assertEqual(doc_check.check([("w.jpg", ".jpg", read)], "Oak Street")["problems"], ["Job name not found: Oak Street."])
        self.assertEqual(doc_check.waiver_reading([("w.jpg", ".jpg", read)]), {"guess": "conditional", "form": True, "both": False})

    def test_without_stored_text_a_photo_and_a_scan_are_as_before(self):
        for stored in (lambda: "", lambda: None, lambda: "x1 . ,", lambda: 1 / 0):
            photo, scan = self.prepare(".png", b"", stored), self.prepare(".pdf", SCAN, stored)
            self.assertEqual((photo.text, photo.ocr, scan.ocr), (None, False, False))
            self.assertEqual(doc_check.check([("p.png", ".png", photo)], "Elm"), {"status": "unchecked", "problems": ["p.png is a photo or a scan, so it couldn't be checked."]})
            self.assertEqual(doc_check.check([("s.pdf", ".pdf", scan)], "Elm"), {"status": "unchecked", "problems": ["s.pdf is a scan, so it couldn't be checked."]})
            self.assertEqual(doc_check.waiver_reading([("p.png", ".png", photo), ("s.pdf", ".pdf", scan)]), {"guess": "", "form": False, "both": False})
        self.assertEqual(doc_check.check([("p.png", ".png", b"\x89PNG")], "Elm")["status"], "unchecked")

    def test_a_scan_with_stored_text_reads_as_that_text(self):
        read = self.prepare(".pdf", SCAN, lambda: WAIVER_TEXT)
        self.assertEqual((read.text, read.ocr, read.has_image), (WAIVER_TEXT, True, True))

    def test_a_file_with_its_own_text_never_asks_for_the_stored_text(self):
        self.assertFalse(self.prepare(".pdf", text_pdf(), self.never).ocr)
        self.assertFalse(self.prepare(".txt", b"Plain words in a text file.", self.never).ocr)
        self.assertEqual(self.prepare(".txt", b"", self.never).text, "")
        self.assertEqual(self.prepare(".docx", b"not a zip", self.never).failed, True)
        self.assertTrue(self.prepare(".pdf", b"not a pdf", self.never).failed)

    def test_a_scanned_contract_fills_the_form_from_the_stored_text(self):
        contract = "SUBCONTRACT AGREEMENT\nProject: Elm addition\nThe Contract Sum is $125,000.00. Retainage of 10% shall be withheld from each payment.\n"
        self.assertEqual(contract_import.read("c.pdf", SCAN), {"ok": False, "error": contract_import.NO_TEXT})
        self.assertEqual(contract_import.read("c.pdf", SCAN, ocr_text=lambda: ""), {"ok": False, "error": contract_import.NO_TEXT})
        got = contract_import.read("c.pdf", SCAN, ocr_text=lambda: contract)
        self.assertEqual((got["ok"], got["fields"]["contract_cents"], got["fields"]["retainage_pct"], got["fields"]["name"]), (True, 12_500_000, 10, "Elm addition"))
        self.assertTrue(contract_import.read("c.pdf", text_pdf("The Contract Sum is $9,000.00 for the work described in these documents."), ocr_text=self.never)["ok"])
        self.assertEqual(contract_import.read("c.docx", b"not a zip", ocr_text=self.never), {"ok": False, "error": contract_import.NOT_OPENED})
        self.assertEqual(contract_import.read("c.jpg", b"\xff\xd8", ocr_text=self.never), {"ok": False, "error": contract_import.WRONG_TYPE})

    def test_an_inbox_item_with_no_kind_from_its_name_is_guessed_again_with_the_text(self):
        def state(name, kind, status="new"):
            return {"files": [{"id": "file-aa11", "name": name, "ext": ".pdf"}],
                    "inbox": [{"id": "inb-1", "status": status, "file_ids": ["file-aa11"], "kind": kind, "subject": name, "text": ""}]}
        coi = "CERTIFICATE OF LIABILITY INSURANCE\nProducer: Acme Insurance"
        st = state("scan-0042.pdf", "note")
        self.assertTrue(importer.text_arrived(st, "file-aa11", coi))
        self.assertEqual(st["inbox"][0]["kind"], "insurance")
        for name, kind, status, text in (("contract.pdf", "contract", "new", "Any change order shall be in writing."),   # the name's kind stands
                                         ("scan-0042.pdf", "note", "filed", coi), ("scan-0042.pdf", "note", "dismissed", coi),
                                         ("scan-0042.pdf", "note", "new", "nothing that names a kind")):
            st = state(name, kind, status)
            self.assertFalse(importer.text_arrived(st, "file-aa11", text), (name, status))
            self.assertEqual(st["inbox"][0]["kind"], kind)
        self.assertFalse(importer.text_arrived(state("scan-0042.pdf", "note"), "file-other", coi))


if __name__ == "__main__":
    unittest.main()
