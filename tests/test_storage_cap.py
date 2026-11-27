"""Security review H5: a company's uploaded files are capped, uploads are limited per hour, and
Settings, Company shows how much room is used."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importer
import server
from test_accounts import ServerCase


class StorageCapTests(ServerCase):
    def setUp(self):
        super().setUp()
        server._LIMITS.clear()

    def upload(self, cookie, data, name="photo.jpg"):
        return self.request("POST", "/api/import/file", raw=data, cookie=cookie,
                            headers={"Content-Type": "application/octet-stream", "X-Filename": name})

    def test_the_trial_cap_refuses_a_file_that_would_not_fit_and_says_so(self):
        cookie, data = self.signup("cap@pine.example")
        company_id = data["user"]["company_id"]
        with mock.patch.dict(os.environ, {"DURACASH_TRIAL_STORAGE_CAP_MB": "1"}):
            res, _ = self.upload(cookie, b"\xff\xd8" + b"x" * 600_000)
            self.assertEqual(res.status, 200)
            res, body = self.upload(cookie, b"\xff\xd8" + b"y" * 600_000, "second.jpg")
            self.assertEqual(res.status, 409)
            self.assertIn("file storage is full", body["error"])
            self.assertIn("of 1 MB used", body["error"])
            self.assertEqual(len(list((importer.UPLOADS / company_id).iterdir())), 1, "nothing of the refused file is kept")
            res, info = self.request("GET", "/api/storage", cookie=cookie)
            self.assertEqual(res.status, 200)
            self.assertEqual(info["cap_bytes"], 1024 * 1024)
            self.assertGreater(info["used_bytes"], 600_000)
            # A subscribed company gets the larger cap.
            with server.company_lock(company_id):
                st = server.load_company(company_id)
                st["company"].setdefault("billing", {})["status"] = "active"
                server.save_company(company_id, st)
            with mock.patch.dict(os.environ, {"DURACASH_STORAGE_CAP_MB": "3"}):
                self.assertEqual(self.request("GET", "/api/storage", cookie=cookie)[1]["cap_bytes"], 3 * 1024 * 1024)
                res, _ = self.upload(cookie, b"\xff\xd8" + b"y" * 600_000, "second.jpg")
                self.assertEqual(res.status, 200)

    def test_defaults_are_two_gb_subscribed_and_one_gb_before(self):
        self.assertEqual(importer.storage_cap_bytes({}), 1024 ** 3)
        self.assertEqual(importer.storage_cap_bytes({"company": {"billing": {"status": "trialing"}}}), 2 * 1024 ** 3)
        self.assertEqual(importer.storage_cap_bytes({"company": {"billing": {"partner": True}}}), 2 * 1024 ** 3)
        self.assertEqual(importer.storage_cap_bytes({"company": {"billing": {"status": "canceled"}}}), 1024 ** 3)

    def test_uploads_past_the_hourly_limit_are_refused_before_they_are_read(self):
        cookie, _ = self.signup("burst@pine.example")
        with mock.patch.dict(os.environ, {"DURACASH_UPLOADS_PER_HOUR_USER": "2"}):
            for n in range(2):
                self.assertEqual(self.upload(cookie, b"note %d" % n, f"n{n}.txt")[0].status, 200)
            res, body = self.upload(cookie, b"one more", "n3.txt")
            self.assertEqual(res.status, 429)
            self.assertIn("uploads in one hour", body["error"])

    def test_compliance_and_client_form_uploads_count_toward_the_hourly_limit(self):  # Nov 27 hunt
        cookie, _ = self.signup("burst2@pine.example")
        res, snap = self.request("POST", "/api/jobs", {"name": "Elm", "customer": "Park GC", "address": "9 Elm Street", "approver": "Kim",
                                                       "ap_email": "ap@park.example", "contract_cents": 100000, "role": "GC",
                                                       "phone": "(404) 555-0100"}, cookie=cookie)
        self.assertEqual(res.status, 200, snap)
        payer_id = snap["jobs"][0]["payer_id"]
        res, out = self.request("POST", "/api/compliance/docs", {"type": "coi", "payer_id": payer_id}, cookie=cookie)
        self.assertEqual(res.status, 200, out)
        doc_id = out["doc_id"]
        with mock.patch.dict(os.environ, {"DURACASH_UPLOADS_PER_HOUR_USER": "1"}):
            self.assertEqual(self.upload(cookie, b"note", "n.txt")[0].status, 200)
            res, body = self.request("POST", f"/api/compliance/docs/{doc_id}/file", raw=b"%PDF-1.4 coi", cookie=cookie,
                                     headers={"Content-Type": "application/pdf", "X-Filename": "coi.pdf"})
            self.assertEqual(res.status, 429, body)
            res, body = self.request("POST", f"/api/payers/{payer_id}/client-forms/upload/payapp", raw=b"%PDF-1.4 form", cookie=cookie,
                                     headers={"Content-Type": "application/pdf", "X-Filename": "form.pdf"})
            self.assertEqual(res.status, 429, body)

    def test_email_attachments_past_the_cap_are_listed_as_skipped(self):
        cookie, data = self.signup("capmail@pine.example")
        with mock.patch.dict(os.environ, {"DURACASH_TRIAL_STORAGE_CAP_MB": "1"}):
            stored, skipped = server.Handler._store_email_files(
                None, data["user"]["company_id"], {"attachments": [{"filename": "a.pdf", "data": b"%PDF" + b"z" * 700_000},
                                                                   {"filename": "b.pdf", "data": b"%PDF" + b"z" * 700_000}]}, "Pat")
        self.assertEqual(len(stored), 1)
        self.assertIn("file storage is full", skipped[0])


if __name__ == "__main__":
    unittest.main()
