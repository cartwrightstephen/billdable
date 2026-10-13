"""Off-site backup upload (P0-2): SigV4 signing, encryption required, upload to a local fake S3."""

import os
import stat
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backup
import offsite
import storage

EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
FAKE_AGE = """#!{python}
# Stand-in for age in tests: writes a marker line, then the input. Usage: age -r R -o OUT IN
import sys
args = sys.argv[1:]
out, src, rcpt = args[args.index("-o") + 1], args[-1], args[args.index("-r") + 1]
with open(out, "wb") as f:
    f.write(b"FAKE-AGE " + rcpt.encode() + b"\\n" + open(src, "rb").read())
"""


class SignTests(unittest.TestCase):
    def test_matches_aws_documented_example(self):
        # "GET Object" example from AWS's Signature Version 4 documentation for S3.
        headers = {
            "host": "examplebucket.s3.amazonaws.com",
            "range": "bytes=0-9",
            "x-amz-content-sha256": EMPTY_SHA,
            "x-amz-date": "20130524T000000Z",
        }
        auth = offsite.sign("GET", headers["host"], "/test.txt", headers, EMPTY_SHA, "AKIAIOSFODNN7EXAMPLE",
                            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
                            datetime(2013, 5, 24, tzinfo=timezone.utc))
        self.assertEqual(auth, "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
                               "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
                               "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


class FakeS3(BaseHTTPRequestHandler):
    received: list = []

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        headers = {k: self.headers[k] for k in ("host", "x-amz-date", "x-amz-content-sha256", "content-length", "content-type")}
        now = datetime.strptime(headers["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        expected = offsite.sign("PUT", headers["host"], self.path, headers, headers["x-amz-content-sha256"],
                                "KEYID", "SECRET", "us-west-004", now)
        ok = expected == self.headers["Authorization"] and offsite.hashlib.sha256(body).hexdigest() == headers["x-amz-content-sha256"]
        FakeS3.received.append((self.path, body, ok))
        self.send_response(200 if ok else 403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class UploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s3 = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
        threading.Thread(target=cls.s3.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.s3.shutdown()
        cls.s3.server_close()

    def setUp(self):
        FakeS3.received.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.age = root / "age"
        self.age.write_text(FAKE_AGE.format(python=sys.executable))
        self.age.chmod(self.age.stat().st_mode | stat.S_IEXEC)
        patcher = mock.patch.object(storage, "DATA_DIR", root / "data")
        patcher.start()
        self.addCleanup(patcher.stop)
        (root / "data").mkdir()
        storage.write_json(root / "data" / "auth.json", {"users": [], "sessions": {}})
        self.env = {
            "BACKUP_S3_ENDPOINT": f"http://127.0.0.1:{self.s3.server_address[1]}",
            "BACKUP_S3_BUCKET": "billdable-backups",
            "BACKUP_S3_REGION": "us-west-004",
            "BACKUP_S3_KEY_ID": "KEYID",
            "BACKUP_S3_SECRET": "SECRET",
            "BACKUP_AGE_RECIPIENT": "age1testrecipient",
            "BACKUP_AGE_BINARY": str(self.age),
        }

    def test_upload_encrypts_and_signs(self):
        with mock.patch.dict(os.environ, self.env):
            made = backup.make_backup()
            key = offsite.upload(made)
            self.assertEqual(key, f"billdable/{made.name}.age")
            self.assertEqual(len(FakeS3.received), 1)
            path, body, ok = FakeS3.received[0]
            self.assertTrue(ok, "signature and payload hash check out")
            self.assertEqual(path, f"/billdable-backups/{key}")
            self.assertTrue(body.startswith(b"FAKE-AGE age1testrecipient\n"), "the uploaded bytes went through age")
            self.assertEqual(body.split(b"\n", 1)[1], made.read_bytes())
            self.assertFalse(made.with_name(made.name + ".age").exists(), "the encrypted copy is cleaned up")
            self.assertIsNotNone(offsite.last_upload())
            self.assertEqual(backup.backups(), [made])

    def test_never_uploads_unencrypted(self):
        for missing in ({"BACKUP_AGE_RECIPIENT": ""}, {"BACKUP_AGE_BINARY": str(Path(self.tmp.name) / "no-such-age")}):
            with mock.patch.dict(os.environ, {**self.env, **missing}):
                made = backup.make_backup()
                with self.assertRaisesRegex(offsite.OffsiteError, "unencrypted"):
                    offsite.upload(made)
        self.assertEqual(FakeS3.received, [])
        self.assertIsNone(offsite.last_upload())

    def test_wrong_secret_is_reported(self):
        with mock.patch.dict(os.environ, {**self.env, "BACKUP_S3_SECRET": "WRONG"}):
            with self.assertRaisesRegex(offsite.OffsiteError, "HTTP 403"):
                offsite.upload(backup.make_backup())
        self.assertIsNone(offsite.last_upload())

    def test_plain_http_refused_off_localhost(self):
        with mock.patch.dict(os.environ, {**self.env, "BACKUP_S3_ENDPOINT": "http://s3.example.com"}):
            with self.assertRaisesRegex(offsite.OffsiteError, "https"):
                offsite.upload(backup.make_backup())

    def test_not_configured(self):
        with mock.patch.dict(os.environ, {**self.env, "BACKUP_S3_BUCKET": ""}):
            self.assertFalse(offsite.configured())
            with self.assertRaises(offsite.OffsiteError):
                offsite.upload(backup.make_backup())


if __name__ == "__main__":
    unittest.main()
