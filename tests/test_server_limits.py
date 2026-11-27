"""Security review M8: open connections are capped, headers have a deadline, and the share path
counts a signed-out sender before reading what it sends."""

import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import helper
import importer
import server
import storage


class ServerLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        patches = [mock.patch.object(auth, "AUTH_PATH", root / "auth.json"), mock.patch.object(server, "COMPANIES", root / "companies"),
                   mock.patch.object(importer, "UPLOADS", root / "uploads"), mock.patch.object(storage, "DATA_DIR", root),
                   mock.patch.object(helper, "LOG_PATH", root / "helper_log.json"),
                   mock.patch.dict("os.environ", {"DURACASH_MAX_CONNECTIONS": "4", "DURACASH_DEMO": "off"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def connect(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        self.addCleanup(s.close)
        return s

    def read_all(self, s):
        chunks = []
        while True:
            try:
                chunk = s.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def test_connections_past_the_cap_get_a_quick_busy_answer(self):
        with mock.patch.object(server, "HEADER_SECONDS", 3):
            held = [self.connect() for _ in range(4)]
            for s in held:
                s.sendall(b"GET /healthz HTTP/1.1\r\n")   # started, never finished
            time.sleep(0.3)
            started = time.monotonic()
            extra = self.connect()
            answer = self.read_all(extra)
            self.assertTrue(answer.startswith(b"HTTP/1.1 503"), answer)
            self.assertIn(b"busy", answer)
            self.assertLess(time.monotonic() - started, 2)
            # Once the stalled ones are dropped at the header deadline, there is room again.
            for s in held:
                self.read_all(s)
            time.sleep(0.3)
            ok = self.connect()
            ok.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            self.assertIn(b" 200 ", self.read_all(ok).split(b"\r\n")[0] + b" ")

    def test_headers_sent_a_byte_at_a_time_are_cut_off(self):
        with mock.patch.object(server, "HEADER_SECONDS", 1):
            s = self.connect()
            started = time.monotonic()
            s.sendall(b"GET /healthz HTTP/1.1\r\n")
            closed = False
            for _ in range(40):
                try:
                    s.sendall(b"X")
                except OSError:
                    closed = True
                    break
                s.settimeout(0.1)
                try:
                    if s.recv(1024) == b"":
                        closed = True
                        break
                except socket.timeout:
                    pass
            self.assertTrue(closed, "the connection was still open")
            self.assertLess(time.monotonic() - started, 3)

    def test_a_signed_out_share_over_the_hourly_limit_is_refused_before_its_body_is_read(self):
        key = "share-hold:127.0.0.1"
        with server._LIMITS_LOCK:
            server._LIMITS[key] = [time.monotonic()] * server.SHARE_HOLDS_PER_HOUR
        self.addCleanup(server._LIMITS.pop, key, None)
        with mock.patch.dict("os.environ", {"TRUSTED_PROXY_HOPS": "0"}):
            s = self.connect()
            s.sendall(b"POST /share HTTP/1.1\r\nHost: x\r\nContent-Type: multipart/form-data; boundary=b\r\n"
                      b"Content-Length: 20000000\r\n\r\n")   # announces 20 MB, sends none of it
            s.settimeout(3)
            started = time.monotonic()
            head = s.recv(4096)
            self.assertIn(b" 303 ", head.split(b"\r\n")[0], head)
            self.assertLess(time.monotonic() - started, 2)

    def test_a_signed_out_share_is_held_to_one_files_size(self):
        with mock.patch.dict("os.environ", {"TRUSTED_PROXY_HOPS": "0"}), mock.patch.object(server, "BODY_SECONDS", 1):
            s = self.connect()
            s.sendall(b"POST /share HTTP/1.1\r\nHost: x\r\nContent-Type: multipart/form-data; boundary=b\r\n"
                      + f"Content-Length: {importer.MAX_FILE_BYTES + 1}\r\n\r\n".encode())
            s.settimeout(5)
            self.assertIn(b" 413 ", s.recv(4096).split(b"\r\n")[0])


if __name__ == "__main__":
    unittest.main()
