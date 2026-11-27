"""The client's own forms over HTTP: upload a blank, match its fields, previews, the pay app page's
check, Duracash's note and the send refused while the form can't be filled right."""

import os
import sys
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gcform_pdfs as forms
import pdf_forms
from test_accounts import ServerCase


class ClientFormsHttp(ServerCase):
    def company(self, email):
        cookie, _ = self.signup(email)
        job = {"name": "Elm addition", "customer": "Park GC", "address": "9 Elm Street", "approver": "Kim", "ap_email": "ap@park.example",
               "contract_cents": 10_000_000, "retainage_pct": 10, "role": "GC", "phone": "(404) 555-0100"}
        res, state = self.request("POST", "/api/jobs", job, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        job = state["jobs"][0]
        return cookie, job, job["payer_id"]

    def upload(self, cookie, payer_id, data, form_type="payapp", name="Park pay app.pdf"):
        return self.request("POST", f"/api/payers/{payer_id}/client-forms/upload/{form_type}", raw=data, cookie=cookie,
                            headers={"Content-Type": "application/pdf", "X-Filename": quote(name)})

    def test_upload_match_preview_and_the_send_check(self):
        cookie, job, payer_id = self.company("gc-forms@example.com")
        res, out = self.upload(cookie, payer_id, forms.locked())
        self.assertEqual(res.status, 409)
        self.assertIn("locked (encrypted)", out["error"])
        res, out = self.upload(cookie, payer_id, forms.fillable_stream())
        self.assertEqual(res.status, 200, out)
        self.assertIn("jobs", out, "the answer carries the page's data, as every change does")
        self.assertNotIn("gc_forms", out, "the forms stay out of the page load")
        tpl = out["client_forms"]["templates"][0]
        self.assertEqual((tpl["form_type"], tpl["version"], tpl["fillable"], len(tpl["fields"])), ("payapp", 1, True, 6))
        res, blank = self.request("GET", f"/api/payers/{payer_id}/client-forms/{tpl['id']}/blank.pdf", cookie=cookie)
        self.assertEqual((res.status, blank), (200, forms.fillable_stream()))
        res, out = self.request("POST", f"/api/payers/{payer_id}/client-forms/{tpl['id']}/match", {"mapping": [
            {"field": "ContractSum", "source": "original_contract"}, {"field": "AppNo", "source": "contract_number"},
            {"field": "Row.1.Desc", "source": "line.description", "row": 1}]}, cookie=cookie)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(len(out["client_forms"]["templates"][0]["mapping"]), 3)
        res, sample = self.request("GET", f"/api/payers/{payer_id}/client-forms/{tpl['id']}/sample.pdf?grid=1", cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(pdf_forms.field_values(sample)["ContractSum"], "$100,000.00")
        self.assertIn(b"0.55 0.75 0.95 RG", sample, "the grid")
        # A pay app: its page says what stops it going on the client's form.
        res, state = self.request("POST", f"/api/jobs/{job['id']}/sov-progress", {"done": {job["sov"]["lines"][0]["id"]: "0.4"}}, cookie=cookie)
        self.assertEqual(res.status, 200, state)
        inv = state["jobs"][0]["invoices"][-1]
        res, view = self.request("GET", f"/api/jobs/{job['id']}/payapp/{inv['id']}/client-forms", cookie=cookie)
        self.assertEqual(res.status, 200, view)
        self.assertEqual(view["send"], "both")
        self.assertIn("AppNo (Contract number) has no value", view["forms"][0]["problems"][0])
        res, pdf = self.request("GET", view["forms"][0]["url"], cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertEqual(pdf_forms.field_values(pdf)["Row.1.Desc"], "Contract amount", "the job's one lump-sum line")
        res, out = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie=cookie)
        self.assertEqual(res.status, 409, out)
        self.assertIn("Park GC's pay app (cover page): AppNo (Contract number) has no value", out["error"])
        # Emails carrying only Duracash's PDF don't wait on the client's form.
        res, out = self.request("POST", f"/api/payers/{payer_id}/client-forms/send", {"send": "ours"}, cookie=cookie)
        self.assertEqual((res.status, out["client_forms"]["send"]), (200, "ours"))
        res, out = self.request("POST", f"/api/jobs/{job['id']}/send", {}, cookie=cookie)
        self.assertNotIn("Park GC's pay app (cover page)", str(out.get("error", "")))
        res, out = self.request("POST", f"/api/payers/{payer_id}/client-forms/send", {"send": "everything"}, cookie=cookie)
        self.assertEqual(res.status, 409)

    def test_a_new_version_and_duracash_note(self):
        cookie, job, payer_id = self.company("gc-forms-versions@example.com")
        _, out = self.upload(cookie, payer_id, forms.fillable_table())
        first = out["client_forms"]["templates"][0]
        res, out = self.upload(cookie, payer_id, forms.fillable_table())
        self.assertEqual(res.status, 409)
        self.assertIn("same file as version 1", out["error"])
        res, out = self.upload(cookie, payer_id, forms.saved_twice())
        self.assertEqual(res.status, 200, out)
        view = out["client_forms"]
        self.assertEqual(([t["version"] for t in view["templates"]], [t["version"] for t in view["history"]]), ([2], [1]))
        second = view["templates"][0]
        res, out = self.request("POST", f"/api/payers/{payer_id}/client-forms/{second['id']}/note", {"note": "Set up from Park's email"}, cookie=cookie)
        self.assertEqual(res.status, 403, "only Duracash's team writes the note")
        _, me = self.request("GET", "/api/auth/me", cookie=cookie)
        os.environ["HELPER_ADMIN_USER_IDS"] = me["user"]["id"]
        res, out = self.request("POST", f"/api/payers/{payer_id}/client-forms/{second['id']}/note", {"note": "Set up from Park's email"}, cookie=cookie)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(out["client_forms"]["templates"][0]["duracash_note"], "Set up from Park's email")
        self.assertTrue(out["client_forms"]["admin"])
        res, out = self.request("POST", f"/api/payers/{payer_id}/client-forms/{second['id']}/retire", {}, cookie=cookie)
        self.assertEqual((res.status, out["client_forms"]["templates"]), (200, []))
        res, _ = self.request("GET", f"/api/payers/{payer_id}/client-forms/{first['id']}/blank.pdf", cookie=cookie)
        self.assertEqual(res.status, 200, "older versions stay")

    def test_blank_forms_count_toward_storage_and_go_in_the_export(self):
        # Integration (Nov 27): a blank is an upload like any other (importer.store_bytes): it
        # counts toward the storage cap, is refused over it, goes in the export with files for
        # anyone with Export (it isn't sensitive), and a version dropped past MAX_VERSIONS leaves the disk.
        import io
        import zipfile
        from unittest import mock
        import gcforms
        import importer
        cookie, job, payer_id = self.company("gc-forms-store@example.com")
        _, me = self.request("GET", "/api/auth/me", cookie=cookie)
        company_id = me["user"]["company_id"]
        used = importer.storage_used_bytes(company_id)
        res, out = self.upload(cookie, payer_id, forms.fillable_table())
        self.assertEqual(res.status, 200, out)
        import server
        tpl = gcforms.templates(server.load_company(company_id), payer_id)[0]
        self.assertEqual(importer.storage_used_bytes(company_id), used + len(forms.fillable_table()))
        self.assertEqual(gcforms.blank_bytes(company_id, tpl), forms.fillable_table(), "read back as stored on disk")
        with mock.patch.object(importer, "CAP_FOR", lambda cid: importer.storage_used_bytes(cid) + 10):
            res, out = self.upload(cookie, payer_id, forms.saved_twice())
            self.assertEqual(res.status, 409, out)
            self.assertIn("storage", out["error"].lower())
        res, raw = self.request("GET", "/api/export/with-files.zip", cookie=cookie)
        self.assertEqual(res.status, 200)
        zf = zipfile.ZipFile(io.BytesIO(raw))
        name = next(n for n in zf.namelist() if n.startswith(f"files/{tpl['file_id']}-"))
        self.assertEqual(zf.read(name), forms.fillable_table())
        self.assertNotIn("left-out-files.txt", zf.namelist())
        with mock.patch.object(gcforms, "MAX_VERSIONS", 1):
            res, out = self.upload(cookie, payer_id, forms.saved_twice())
            self.assertEqual(res.status, 200, out)
        self.assertEqual(importer.storage_used_bytes(company_id), used + len(forms.saved_twice()), "the dropped version's file is gone")

    def test_another_company_sees_none_of_it(self):
        cookie, job, payer_id = self.company("gc-forms-a@example.com")
        _, out = self.upload(cookie, payer_id, forms.flat(), form_type="conditional_progress")
        tpl = out["client_forms"]["templates"][0]
        self.assertFalse(tpl["fillable"])
        other, _, _ = self.company("gc-forms-b@example.com")
        res, _ = self.request("GET", f"/api/payers/{payer_id}/client-forms/{tpl['id']}/blank.pdf", cookie=other)
        self.assertEqual(res.status, 404)
        res, _ = self.request("GET", f"/api/payers/{payer_id}/client-forms", cookie=other)
        self.assertEqual(res.status, 404)
        res, _ = self.upload(other, payer_id, forms.flat())
        self.assertEqual(res.status, 409)
        res, _ = self.upload(cookie, payer_id, forms.flat(), form_type="nonsense")
        self.assertEqual(res.status, 404)
        res, _ = self.request("GET", f"/api/payers/{payer_id}/client-forms/{tpl['id']}/blank.pdf")
        self.assertEqual(res.status, 401)


if __name__ == "__main__":
    unittest.main()
