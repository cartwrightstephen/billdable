"""The case company (case_company.py) typed into Duracash from start to finish through the same
routes the pages use, with every figure in its answer key checked. If the app changes so that the
case no longer works or a figure moves, this fails before a tester finds it."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import case_company
import server


class CaseCompanyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._orig = (auth.AUTH_PATH, server.COMPANIES)
        auth.AUTH_PATH = Path(cls.tmp.name) / "auth.json"
        server.COMPANIES = Path(cls.tmp.name) / "companies"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, want_cookie=False, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            headers.setdefault("Content-Type", "application/json")
            headers["Content-Length"] = str(len(data))
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        payload = json.loads(res.read() or b"{}")
        if want_cookie and res.getheader("Set-Cookie"):
            payload["_cookie"] = res.getheader("Set-Cookie").split(";")[0]
        conn.close()
        return res.status, payload

    def test_the_whole_case_enters_and_every_figure_matches(self):
        figures = case_company.enter(self.request)
        self.assertEqual(set(figures), set(case_company.EXPECTED))
        for name, want in case_company.EXPECTED.items():
            got = figures[name]
            for number, f in want.get("payapps", {}).items():
                self.assertEqual(got["payapps"][number], f, f"{name}: {number}")
            for key, f in want.get("jobs", {}).items():
                self.assertEqual({k: got["jobs"][key][k] for k in f}, f, f"{name}: {key}")

    def test_the_packet_is_written_from_this_data(self):
        import case_company_doc
        packet = (Path(__file__).resolve().parents[1] / "docs" / "case-company.md").read_text(encoding="utf-8")
        self.assertEqual(packet, case_company_doc.render(), "run python3 case_company.py to write docs/case-company.md again")


if __name__ == "__main__":
    unittest.main()
