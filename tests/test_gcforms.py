"""The client's own forms (gcforms.py, pdf_forms.py): reading a blank form's fields from a classic
cross-reference table, a cross-reference stream with object streams and a file saved twice;
refusing locked and dynamic XFA forms and files built to keep the reader busy; filling as an
incremental update that reads back; values placed by position; templates, versions and the checks
before filling; and the pay app email carrying the client's form."""

import re
import sys
import tempfile
import threading
import unittest
import zlib
from datetime import date
from email import message_from_bytes
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain
import gcforms
import gcform_pdfs as forms
import importer
import mailer
import outbox
import pdf_forms
from test_money import clear_packet, new_job, new_state
import approve_help

VALUES = {"ContractSum": "$1,234.56", "AppNo": "7", "Final": "yes", "Row.1.Desc": "Framing (rough)", "Owner": "Ann Ruiz"}


class Reading(unittest.TestCase):
    def names(self, data):
        return [(f["name"], f["kind"], f["page"]) for f in pdf_forms.read(data).fields]

    def test_a_classic_table_and_an_xref_stream_with_object_streams_read_the_same(self):
        want = [("ContractSum", "text", 1), ("AppNo", "text", 1), ("Final", "checkbox", 1), ("Row.1.Desc", "text", 1),
                ("Row.2.Desc", "text", 1), ("Owner", "text", 1)]
        self.assertEqual(self.names(forms.fillable_table()), want)
        stream = forms.fillable_stream()
        self.assertIn(b"/Type /ObjStm", stream)
        self.assertNotIn(b"/T (ContractSum)", stream, "the fields are inside the compressed object stream")
        self.assertEqual(self.names(stream), want)
        info = pdf_forms.read(stream)
        self.assertEqual(info.pages, [{"width": 612.0, "height": 792.0, "rotate": 0}])
        app_no = next(f for f in info.fields if f["name"] == "AppNo")
        self.assertEqual((app_no["max_len"], app_no["rect"]), (6, [450.0, 735.0, 540.0, 755.0]))
        self.assertEqual(next(f for f in info.fields if f["name"] == "Final")["on"], "Yes")

    def test_a_file_saved_twice_reads_its_newest_objects(self):
        names = [n for n, _, _ in self.names(forms.saved_twice())]
        self.assertEqual(names[-1], "Architect")
        self.assertFalse(pdf_forms.Pdf(forms.saved_twice()).repaired)

    def test_a_damaged_index_is_read_by_scanning(self):
        info = pdf_forms.read(forms.damaged_xref())
        self.assertEqual(len(info.fields), 6)
        self.assertIn("damaged", info.notes[0])

    def test_locked_xfa_and_non_pdf_files_are_refused_politely(self):
        for data, words in ((forms.locked(), "locked (encrypted)"), (forms.dynamic_xfa(), "dynamic XFA"),
                            (b"hello", "isn't a PDF"), (b"", "isn't a PDF")):
            with self.assertRaises(domain.GateError) as refused:
                gcforms.read_upload(data)
            self.assertIn(words, str(refused.exception))

    def test_a_form_with_no_fields_says_so(self):
        info = pdf_forms.read(forms.flat(rotate=90))
        self.assertFalse(info.fillable)
        self.assertEqual(info.pages[0], {"width": 792.0, "height": 612.0, "rotate": 90}, "as the page shows, turned")
        self.assertEqual(info.pages[1]["width"], 792.0)


class Bounded(unittest.TestCase):
    """Uploads are untrusted: each of these is refused, quickly, with a PdfError, never a crash."""

    def refused(self, data):
        with self.assertRaises(domain.GateError):
            gcforms.read_upload(data)

    def test_nesting_too_deep(self):
        objs = {1: b"<</Type /Catalog /Pages 2 0 R /X " + b"[" * 5000 + b"]" * 5000 + b">>",
                2: b"<</Type /Pages /Kids [] /Count 0>>"}
        self.refused(forms.with_table(objs))

    def test_a_stream_that_inflates_past_the_limit(self):
        # A field's widget packed in an object stream padded far past what one stream may inflate to.
        objs = {1: b"<</Type /Catalog /Pages 2 0 R /AcroForm <</Fields [5 0 R]>>>>", 2: b"<</Type /Pages /Kids [3 0 R] /Count 1>>",
                3: b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>",
                5: b"<</T (A) /Kids [6 0 R]>>", 6: b"<</FT /Tx /Rect [0 0 1 1]>>" + b" " * (pdf_forms.MAX_STREAM + 10)}
        data = forms.with_xref_stream(objs, packed={6})
        self.assertLess(len(data), 100_000)
        self.refused(data)
        with mock.patch.object(pdf_forms, "MAX_STREAM", 20 * 1024 * 1024):
            self.assertEqual([f["name"] for f in pdf_forms.read(data).fields], ["A"], "under the limit it reads")

    def test_a_prev_chain_that_loops_ends(self):
        data = forms.fillable_table()
        at = int(data[data.rindex(b"startxref"):].split()[1])
        looped = data.replace(b"trailer\n<</Size", b"trailer\n<</Prev %d /Size" % at)
        self.assertEqual(len(pdf_forms.read(looped).fields), 6)

    def test_a_field_tree_that_points_at_itself_ends(self):
        objs = {1: b"<</Type /Catalog /Pages 2 0 R /AcroForm <</Fields [4 0 R]>>>>", 2: b"<</Type /Pages /Kids [3 0 R] /Count 1>>",
                3: b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>", 4: b"<</T (Loop) /Kids [4 0 R 5 0 R]>>",
                5: b"<</T (Kid) /FT /Tx /Rect [0 0 10 10] /Parent 4 0 R>>"}
        self.assertEqual([f["name"] for f in pdf_forms.read(forms.with_table(objs)).fields], ["Loop.Kid"])

    def test_too_large(self):
        with mock.patch.object(pdf_forms, "MAX_BYTES", 100):
            self.refused(forms.fillable_table())


class Filling(unittest.TestCase):
    def check_update(self, original, filled):
        self.assertTrue(filled.startswith(original), "the client's form is kept byte for byte, the values after it")
        pdf = pdf_forms.Pdf(filled)
        self.assertFalse(pdf.repaired, "the update's own cross-reference reads")
        tail = filled[filled.rindex(b"startxref"):]
        at = int(tail.split()[1])
        self.assertGreater(at, len(original))
        self.assertEqual(pdf.trailer.get("Prev"), int(original[original.rindex(b"startxref"):].split()[1]))
        return pdf

    def test_values_read_back_from_a_table_form(self):
        original = forms.fillable_table()
        filled = pdf_forms.fill(original, VALUES)
        pdf = self.check_update(original, filled)
        self.assertTrue(filled[len(original):].lstrip().startswith(b"7 0 obj") or b"xref" in filled[len(original):])
        got = pdf_forms.field_values(filled)
        self.assertEqual({k: got[k] for k in VALUES}, {**VALUES, "Final": "Yes"})
        self.assertEqual(got["Row.2.Desc"], "")
        self.assertIs(pdf.acroform()["NeedAppearances"], True)
        final = next(f for f in pdf.fields() if f.name == "Final")
        self.assertEqual(pdf.get(final.widgets[0].ref.num)["AS"], "Yes")
        # Each text widget has its own appearance with the value drawn in it.
        owner = next(f for f in pdf.fields() if f.name == "Owner")
        widget = pdf.get(owner.widgets[0].ref.num)
        ap = pdf.resolve(widget["AP"]["N"])
        self.assertIn(b"(Ann Ruiz) Tj", ap.raw)
        self.assertEqual(ap.dict["BBox"], [0, 0, 228, 16])
        self.assertIn(b"Owner", b"".join(re.findall(rb"/T \((\w+)\)", filled)))

    def test_values_read_back_from_an_xref_stream_form(self):
        original = forms.fillable_stream()
        filled = pdf_forms.fill(original, VALUES)
        pdf = self.check_update(original, filled)
        self.assertTrue(pdf.xref_stream, "the update's index is a stream too, as the form's was")
        self.assertEqual({k: pdf_forms.field_values(filled)[k] for k in ("ContractSum", "Row.1.Desc")},
                         {"ContractSum": "$1,234.56", "Row.1.Desc": "Framing (rough)"})

    def test_filled_twice_the_newest_values_win(self):
        once = pdf_forms.fill(forms.saved_twice(), {"Architect": "Lee & Co"})
        twice = pdf_forms.fill(once, {"Architect": "Pat Lee", "AppNo": "8"})
        got = pdf_forms.field_values(twice)
        self.assertEqual((got["Architect"], got["AppNo"]), ("Pat Lee", "8"))
        self.check_update(once, twice)

    def test_a_value_longer_than_the_field_takes_is_cut_and_unicode_kept(self):
        got = pdf_forms.field_values(pdf_forms.fill(forms.fillable_table(), {"AppNo": "1234567890", "Owner": "Zoë – Ruiz"}))
        self.assertEqual(got["AppNo"], "123456")
        self.assertEqual(got["Owner"], "Zoë – Ruiz")

    def test_values_placed_on_a_form_without_fields(self):
        original = forms.flat(compressed=True)
        filled = pdf_forms.fill(original, {}, [pdf_forms.Placed(0, 72, 72, "$9,000.00"), pdf_forms.Placed(1, 500, 100, "Right", align="right")], grid=True)
        pdf = self.check_update(original, filled)
        pages = pdf.pages()
        self.assertEqual(len(pages), 2)
        contents = pdf.resolve(pages[0].dict["Contents"])
        self.assertIsInstance(contents, list)
        self.assertEqual(len(contents), 3, "q, the form's own drawing, then ours")
        ours = pdf.resolve(contents[-1]).raw
        self.assertTrue(ours.startswith(b"Q q 1 0 0 1 0 0 cm"))
        self.assertIn(b"1 0 0 1 72 720 Tm ($9,000.00) Tj", ours, "an inch in from the left and down from the top")
        self.assertIn(pdf_forms.FONT, pdf.resolve(pages[0].dict["Resources"])["Font"])
        self.assertIn("F1", pdf.resolve(pages[0].dict["Resources"])["Font"], "the page's own fonts are kept")

    def test_a_turned_page_is_placed_on_as_it_shows(self):
        filled = pdf_forms.fill(forms.flat(rotate=90), {}, [pdf_forms.Placed(0, 72, 72, "X")])
        pdf = pdf_forms.Pdf(filled)
        ours = pdf.resolve(pdf.resolve(pdf.pages()[0].dict["Contents"])[-1]).raw
        self.assertIn(b"0 1 -1 0 612 0 cm", ours)
        self.assertIn(b"1 0 0 1 72 540 Tm (X) Tj", ours, "612 tall as it shows, so 72 down is 540 up")


def stored_template(state, company_id, payer_id, data, form_type="payapp", name="gc-form.pdf"):
    info = gcforms.read_upload(data)
    meta = importer.store_bytes(company_id, name, data, "Pat")
    return gcforms.add_template(state, payer_id, form_type, meta, info, "Pat")


class Uploads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = importer.UPLOADS
        importer.UPLOADS = Path(self.tmp.name)
        self.state = new_state()
        self.job = new_job(self.state, contract=10_000_000, retainage=10, client_type="gc", customer="Peach GC")
        self.payer_id = self.job["payer_id"]

    def tearDown(self):
        importer.UPLOADS = self.orig
        self.tmp.cleanup()


class Templates(Uploads):
    def test_fields_are_guessed_from_their_names_and_versions_carry_matches(self):
        t1 = stored_template(self.state, "co-t", self.payer_id, forms.fillable_table())
        self.assertEqual(t1["version"], 1)
        by_field = {m["field"]: m for m in t1["mapping"]}
        self.assertEqual(by_field["ContractSum"]["source"], "original_contract")
        self.assertEqual((by_field["Row.1.Desc"]["source"], by_field["Row.1.Desc"]["row"]), ("line.description", 1))
        self.assertTrue(by_field["Owner"]["guessed"])
        gcforms.save_mapping(self.state, self.payer_id, t1["id"], {"mapping": [
            {"field": "ContractSum", "source": "contract_to_date"}, {"field": "AppNo", "source": "application_count"},
            {"field": "Owner", "source": "text", "text": "Peach Holdings"}], "money_format": "commas"}, "Pat")
        with self.assertRaises(domain.GateError):
            stored_template(self.state, "co-t", self.payer_id, forms.fillable_table())   # the same file again
        t2 = stored_template(self.state, "co-t", self.payer_id, forms.saved_twice())
        self.assertEqual(t2["version"], 2)
        self.assertTrue(t1["retired_at"])
        self.assertEqual(gcforms.active(self.state, self.payer_id, "payapp")["id"], t2["id"])
        kept = {m["field"]: m["source"] for m in t2["mapping"]}
        self.assertEqual((kept["ContractSum"], kept["AppNo"], kept["Owner"]), ("contract_to_date", "application_count", "text"))
        self.assertEqual(t2["money_format"], "commas")
        view = gcforms.client_view(self.state, self.payer_id)
        self.assertEqual(([t["version"] for t in view["templates"]], [t["version"] for t in view["history"]]), ([2], [1]))
        self.assertTrue(any(a["action"] == "gc_form_added" for a in self.state["audit"]))

    def test_matches_that_cant_be_used_are_refused(self):
        t = stored_template(self.state, "co-t", self.payer_id, forms.fillable_table())
        bad = [
            {"mapping": [{"field": "Nope", "source": "current_due"}]},
            {"mapping": [{"field": "AppNo", "source": "made_up"}]},
            {"mapping": [{"field": "Row.1.Desc", "source": "line.description"}]},
            {"mapping": [{"field": "Owner", "source": "text", "text": "  "}]},
            {"placed": [{"source": "current_due", "page": 2, "x": 10, "y": 10}]},
            {"placed": [{"source": "current_due", "page": 1, "x": 10, "y": 900}]},
        ]
        for body in bad:
            with self.assertRaises(domain.GateError, msg=body):
                gcforms.save_mapping(self.state, self.payer_id, t["id"], body, "Pat")

    def test_only_one_form_of_each_type_is_in_use(self):
        a = stored_template(self.state, "co-t", self.payer_id, forms.fillable_table())
        w = stored_template(self.state, "co-t", self.payer_id, forms.flat(), form_type="conditional_progress")
        self.assertEqual({t["form_type"] for t in gcforms.templates(self.state, self.payer_id)}, {"payapp", "conditional_progress"})
        gcforms.retire(self.state, self.payer_id, a["id"], "Pat")
        self.assertIsNone(gcforms.active(self.state, self.payer_id, "payapp"))
        self.assertEqual(gcforms.active(self.state, self.payer_id, "conditional_progress")["id"], w["id"])


class Checks(unittest.TestCase):
    TPL = {"fields": [{"name": "Due", "kind": "text"}, {"name": "Arch", "kind": "text"}, {"name": "Short", "kind": "text", "max_len": 4},
                      {"name": "D1", "kind": "text"}, {"name": "D2", "kind": "text"}, {"name": "Fin", "kind": "checkbox"}],
           "mapping": [{"field": "Due", "source": "current_due"}, {"field": "Arch", "source": "contract_number"},
                       {"field": "D1", "source": "line.description", "row": 1}, {"field": "D2", "source": "line.description", "row": 2},
                       {"field": "Fin", "source": "final"}],
           "placed": [], "money_format": "dollars", "date_format": "us"}

    def test_every_matched_field_gets_its_value_or_a_problem_says_which(self):
        values = {"current_due": 1_234_567, "contract_number": "", "final": False}
        out, _, problems = gcforms.check(self.TPL, values, [{"description": "Framing"}])
        self.assertEqual(out, {"Due": "$12,345.67", "D1": "Framing", "D2": "", "Fin": "Off"})
        self.assertEqual(len(problems), 1)
        self.assertIn("Arch (Contract number) has no value", problems[0])
        optional = {**self.TPL, "mapping": [{**m, "optional": True} if m["field"] == "Arch" else m for m in self.TPL["mapping"]]}
        self.assertEqual(gcforms.check(optional, values, [{"description": "Framing"}])[2], [])

    def test_more_lines_than_rows_is_a_problem(self):
        rows = [{"description": f"L{n}"} for n in range(3)]
        _, _, problems = gcforms.check(self.TPL, {"current_due": 1, "contract_number": "C", "final": True}, rows)
        self.assertEqual(len(problems), 1)
        self.assertIn("3 continuation lines; the form is set up for 2", problems[0])

    def test_money_and_dates_as_the_client_writes_them(self):
        for style, text in (("dollars", "-$1,234.50"), ("commas", "-1,234.50"), ("plain", "-1234.50")):
            self.assertEqual(gcforms._money(-123_450, style), text)
        d = date(2026, 11, 27)
        self.assertEqual([gcforms._date(d, s) for s in ("us", "long", "iso")], ["11/27/2026", "November 27, 2026", "2026-11-27"])

    def test_a_value_longer_than_the_field_is_a_problem(self):
        tpl = {**self.TPL, "mapping": [{"field": "Short", "source": "current_due"}]}
        self.assertIn("at most 4 characters", gcforms.check(tpl, {"current_due": 100_000}, [])[2][0])


class SendPath(Uploads):
    """The pay app email carries the client's form, filled in from the pay app."""

    def setUp(self):
        super().setUp()
        domain.record_progress(self.state, self.job["id"], 4_000_000)
        self.inv = clear_packet(self.state, self.job)
        self.tpl = stored_template(self.state, "co-t", self.payer_id, forms.fillable_table())
        gcforms.save_mapping(self.state, self.payer_id, self.tpl["id"], {"mapping": [
            {"field": "ContractSum", "source": "original_contract"}, {"field": "AppNo", "source": "application_count"},
            {"field": "Row.1.Desc", "source": "line.description", "row": 1}, {"field": "Row.2.Desc", "source": "line.description", "row": 2},
            {"field": "Owner", "source": "client"}], "money_format": "dollars"}, "Pat")

    def deliver(self):
        sent = []
        site = mock.Mock()
        site.lock.return_value = threading.Lock()
        site.load.return_value = self.state
        site.inbound.return_value = ""
        user = {"id": "u1", "role": "owner", "email": "pat@pine.example", "email_verified": True, "name": "Pat"}
        with mock.patch.object(mailer, "configured", return_value=True), mock.patch.object(mailer, "allowed", return_value=True), \
                mock.patch.object(mailer, "sent_in_last_day", return_value=0), mock.patch.object(mailer, "record_sent"), \
                mock.patch.object(mailer, "send", side_effect=lambda msgs, cfg: sent.extend(msgs) or [None] * len(msgs)):
            outbox.deliver_queued(site, "co-t", user)
        return sent

    def attachments(self, msg):
        return {part.get_filename(): part.get_payload(decode=True) for part in msg.walk() if part.get_filename()}

    def test_the_preview_and_the_email_carry_the_filled_form(self):
        data, name, problems = gcforms.for_invoice(self.state, "co-t", self.job["id"], self.inv["id"], "payapp")
        self.assertEqual(problems, [])
        self.assertTrue(name.endswith("-payapp-client-form.pdf"))
        got = pdf_forms.field_values(data)
        self.assertEqual((got["ContractSum"], got["AppNo"], got["Owner"]), ("$100,000.00", "1", "Peach GC"))
        self.assertEqual(gcforms.send_problems(self.state, self.job["id"]), [])
        approve_help.approve_and_send(self.state, self.job["id"], "Owner")
        sent = self.deliver()
        self.assertEqual(len(sent), 1)
        files = self.attachments(sent[0])
        self.assertEqual(len([n for n in files if n.endswith(".pdf")]), 2, "both: the client's form and Duracash's PDF (the default)")
        theirs = next(v for n, v in files.items() if n.endswith("client-form.pdf"))
        self.assertEqual(pdf_forms.field_values(theirs)["ContractSum"], "$100,000.00")
        self.assertEqual(self.state["email_log"][-1]["status"], "sent")

    def test_theirs_only_leaves_duracash_pdf_off(self):
        gcforms.set_send(self.state, self.payer_id, "theirs", "Pat")
        approve_help.approve_and_send(self.state, self.job["id"], "Owner")
        files = self.attachments(self.deliver()[0])
        self.assertEqual([n for n in files if n.endswith(".pdf")], [n for n in files if n.endswith("client-form.pdf")])
        self.assertEqual(len(files), 1)

    def test_ours_only_sends_no_client_form(self):
        gcforms.set_send(self.state, self.payer_id, "ours", "Pat")
        approve_help.approve_and_send(self.state, self.job["id"], "Owner")
        files = self.attachments(self.deliver()[0])
        self.assertFalse([n for n in files if "client-form" in n])

    def test_a_retainage_request_keeps_duracash_pdf(self):
        gcforms.set_send(self.state, self.payer_id, "theirs", "Pat")
        approve_help.approve_and_send(self.state, self.job["id"], "Owner")
        self.deliver()
        self.state["email_log"].append({"id": "eml-ret", "kind": "retainage", "job_id": self.job["id"], "to": "ap@jones.example",
                                        "subject": "Retainage", "body": "Please release it.", "status": "queued", "attachments": [],
                                        "payapp_invoice_id": self.inv["id"]})
        sent = self.deliver()
        self.assertEqual(len(sent), 1)
        self.assertFalse([n for n in self.attachments(sent[0]) if "client-form" in n])

    def test_a_form_missing_a_value_stops_the_send_and_fails_an_email_already_queued(self):
        gcforms.save_mapping(self.state, self.payer_id, self.tpl["id"], {"mapping": [
            {"field": "ContractSum", "source": "original_contract"}, {"field": "AppNo", "source": "contract_number"}]}, "Pat")
        problems = gcforms.send_problems(self.state, self.job["id"])
        self.assertEqual(len(problems), 1)
        self.assertIn("Peach GC's pay app (cover page): AppNo (Contract number) has no value", problems[0])
        approve_help.approve_and_send(self.state, self.job["id"], "Owner")   # the server refuses first; queued by hand here
        self.assertEqual(self.deliver(), [])
        self.assertTrue(self.state["email_log"][-1]["status"].startswith("failed: Peach GC's pay app (cover page) isn't complete"))

    def test_a_waiver_on_their_form_is_filled_for_printing(self):
        stored_template(self.state, "co-t", self.payer_id, forms.flat(), form_type="conditional_progress", name="waiver.pdf")
        tpl = gcforms.active(self.state, self.payer_id, "conditional_progress")
        gcforms.save_mapping(self.state, self.payer_id, tpl["id"], {"placed": [
            {"source": "waiver_amount", "page": 1, "x": 300, "y": 200}, {"source": "contractor", "page": 1, "x": 72, "y": 250}]}, "Pat")
        approve_help.approve_and_send(self.state, self.job["id"], "Owner")
        data, _, problems = gcforms.for_invoice(self.state, "co-t", self.job["id"], self.inv["id"], "conditional_progress")
        self.assertEqual(problems, [])
        self.assertIn(b"($36,000.00) Tj", data, "the pay app's amount less retainage")
        view = gcforms.payapp_view(self.state, self.job["id"], self.inv["id"])
        self.assertEqual([w["form_type"] for w in view["waivers"]], ["conditional_progress"])
        self.assertEqual([f["form_type"] for f in view["forms"]], ["payapp"])


if __name__ == "__main__":
    unittest.main()


class PayappCoreValues(Uploads):
    """Integration (Nov 27): the pay-app core's figures on the client's form."""

    def schedule(self):
        import test_payapp_core as core
        state, job, labor, materials = core.schedule_job(retainage=10)
        return core, state, job, labor, materials

    def test_g702_5a_5b_and_stored_materials_are_values_a_form_can_take(self):
        core, state, job, labor, materials = self.schedule()
        for key in ("retainage_work", "retainage_stored", "stored_total", "line.stored", "total.stored"):
            self.assertIn(key, gcforms.SOURCE)
        domain.record_sov_progress(state, job["id"], {labor: "0.5", materials: "20"}, stored={materials: "1500"})
        inv = core.draft(job)
        values, rows = gcforms.payapp_values(state, job, inv)
        # As the PDF prints them: $1,500 stored, 10% of it held (5b), the rest on work done (5a).
        self.assertEqual((values["stored_total"], values["retainage_stored"], values["retainage_work"], values["retainage_total"]),
                         (150_000, 15_000, 60_000, 75_000))
        self.assertEqual(values["retainage_work"] + values["retainage_stored"], values["retainage_total"])
        self.assertEqual(next(r for r in rows if r["description"] == "Materials")["stored"], 150_000)
        self.assertEqual(values["total.stored"], 150_000)

    def test_a_retainage_release_fills_in_and_an_earlier_pay_app_is_refused(self):
        core, state, job, labor, materials = self.schedule()
        domain.record_sov_progress(state, job["id"], {labor: "1", materials: "50"})
        first = core.send(state, job)
        domain.match_payment(state, job["id"], first["amount_cents"], "CHK-1")
        domain.draft_retainage_release(state, job["id"], 50_000, "substantial")
        rel = core.draft(job)
        values, rows = gcforms.payapp_values(state, job, rel)
        self.assertEqual((values["current_due"], values["total.this_period"]), (50_000, 0), "only the retainage released is due; no new work")
        self.assertEqual(values["retainage_total"], 75_000, "line 5: what is still held after it")
        # An earlier pay app (billed before Duracash) isn't filled on the client's form.
        core2, st2, job2, labor2, materials2 = self.schedule()
        domain.import_earlier_payapps(st2, job2["id"], {"payapps": [{"number": "1", "period_to": core.DAY, "lines": {labor2: {"pct": "20"}}}]})
        [earlier] = domain.imported_invoices(job2)
        stored_template(st2, "co-t", job2["payer_id"], forms.fillable_table())
        with self.assertRaises(domain.GateError) as caught:
            gcforms.for_invoice(st2, "co-t", job2["id"], earlier["id"], "payapp")
        self.assertIn("billed before Duracash", str(caught.exception))
