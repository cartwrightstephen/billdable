"""Round 2, the Waivers stream (Stephen, 10 Nov, decisions 1, 5 and 6): Georgia's statutory waiver
filled in (held back by a server setting until an attorney approves it), a waiver for each part
payment (a Client playbook setting, on by default), no final waiver while money is still to come,
and a reason to draft the final pay app while an earlier one is unpaid."""

import http.client
import json
import os
import re
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import doc_check
import domain
import importer
import server
import storage
import waiver_form
from test_money import new_job, new_state, send_open
from waiver_help import signed_waiver


def sent(state, job, done_cents):
    domain.record_progress(state, job["id"], done_cents)
    return send_open(state, job)


def owed_waivers(job):
    return [inv["number"] for inv in job["invoices"] if inv.get("waiver_due") and not inv.get("waiver_sent")]



_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def pdf_fields(pdf: bytes) -> dict[str, str]:
    """Each form field's name and value as a viewer reads it: a literal string unescaped, then
    UTF-16BE after its byte order mark (or PDFDocEncoding, which is Latin-1 for these)."""
    out = {}
    for name, value in re.findall(rb"/FT /Tx /T \(([a-z_]+)\) /TU \((?:\\.|[^\\)])*\) /V \(((?:\\.|[^\\)])*)\)", pdf, re.S):
        raw = re.sub(rb"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), value, flags=re.S)
        out[name.decode()] = raw[2:].decode("utf-16-be") if raw.startswith(b"\xfe\xff") else raw.decode("latin-1")
    return out


class PartPaymentWaiverTests(unittest.TestCase):  # decision 5
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        self.inv = sent(self.state, self.job, 400_000)   # PA-01, $4,000.00

    def pay(self, cents, ref):
        domain.match_payment(self.state, self.job["id"], cents, ref, invoice_id=self.inv["id"])

    def test_a_part_payment_owes_a_waiver_for_what_came_in(self):
        self.pay(150_000, "chk-1")
        self.assertEqual(self.inv["status"], "partial")
        self.assertEqual(owed_waivers(self.job), [self.inv["number"]])
        self.assertTrue(self.job["waiver_due"] and not self.job["waiver_sent"], "the job-level flags the Dashboard filter reads")
        self.assertEqual(domain.suggested_waiver_kind(self.job, self.inv), "unconditional_progress")
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 150_000)
        self.assertEqual(self.job["external_status"], domain.PARTIAL_WAIVER_STATUS)
        view = domain.job_view(self.state, self.job)
        self.assertEqual(view["invoices"][0]["waiver_amount_cents"], 150_000)
        self.assertEqual(view["invoices"][0]["waiver_stages"]["unconditional"], {"kind": "unconditional_progress", "status": "Needed"})

    def test_the_dashboard_and_close_out_see_it(self):
        self.pay(150_000, "chk-1")
        row = next(r for r in domain.today_rows(self.state) if r["job_id"] == self.job["id"])
        self.assertEqual((row["kind"], row["button"]), ("waiver", "Choose waiver and send"))
        self.assertTrue(row["text"].startswith("Part payment received:"), row["text"])
        self.assertIn("waiver", [b["kind"] for b in domain.close_blocker_items(self.job)])

    def test_the_waiver_covers_the_part_then_the_rest_gets_its_own(self):
        self.pay(150_000, "chk-1")
        signed_waiver(self.state, self.job["id"], "unconditional_progress", "Part waiver")
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"])
        mail = self.state["email_log"][-1]
        self.assertIn("Amount covered: $1,500.00.", mail["body"])
        self.assertIn("(part payment)", mail["subject"])
        self.assertEqual(owed_waivers(self.job), [])
        self.assertEqual([(w["cents"], w["kind"]) for w in self.inv["partial_waivers"]], [(150_000, "unconditional_progress")])
        self.pay(250_000, "chk-2")
        self.assertEqual(self.inv["status"], "paid")
        self.assertEqual(owed_waivers(self.job), [self.inv["number"]], "the rest owes its own waiver")
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 250_000)
        self.assertNotIn("waiver_sent_at", self.inv, "the part's waiver is kept in partial_waivers, not as this one")
        signed_waiver(self.state, self.job["id"], "unconditional_progress", "Rest waiver")
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"])
        self.assertIn("Amount covered: $2,500.00.", self.state["email_log"][-1]["body"])
        self.assertEqual(len(self.inv["partial_waivers"]), 1)

    def test_two_part_payments_before_a_waiver_are_one_waiver(self):
        self.pay(100_000, "chk-1")
        self.pay(100_000, "chk-2")
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 200_000)

    def test_not_needed_on_a_part_covers_it_and_the_rest_is_still_owed(self):
        self.pay(150_000, "chk-1")
        domain.waiver_not_needed(self.state, self.job["id"], "Handed over on site", invoice_id=self.inv["id"], actor="Sam")
        self.assertEqual(owed_waivers(self.job), [])
        self.assertTrue(self.inv["partial_waivers"][0]["not_needed"])
        self.pay(250_000, "chk-2")
        self.assertEqual(owed_waivers(self.job), [self.inv["number"]])
        self.assertNotIn("waiver_not_needed", self.inv)
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 250_000)

    def test_a_short_pay_after_a_part_waiver_owes_nothing_more_than_came_in(self):
        self.pay(150_000, "chk-1")
        signed_waiver(self.state, self.job["id"], "unconditional_progress")
        domain.send_waiver(self.state, self.job["id"], "Sam", invoice_id=self.inv["id"])
        domain.settle_short_pay(self.state, self.job["id"], self.inv["id"], "Backcharge", "Sam")
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 0)

    def test_a_client_set_to_no_owes_one_only_when_paid_in_full(self):
        payer = self.state["payers"][0]
        domain.update_payer(self.state, payer["id"], {"partial_waivers": "no"})
        self.assertIs(payer["partial_waivers"], False)
        self.pay(150_000, "chk-1")
        self.assertEqual(owed_waivers(self.job), [])
        self.assertEqual(self.job["external_status"], "Partial payment received")
        self.pay(250_000, "chk-2")
        self.assertEqual(domain.waiver_cents(self.job, self.inv), 400_000)

    def test_the_setting_is_on_for_a_client_saved_before_it(self):
        payer = self.state["payers"][0]
        self.assertNotIn("partial_waivers", payer)
        self.assertTrue(domain.partial_waivers_on(payer))
        snap = domain.light_snapshot(self.state)
        self.assertIs(snap["payers"][0]["partial_waivers"], True)
        domain.update_payer(self.state, payer["id"], {"partial_waivers": "no"})
        self.assertIs(domain.light_snapshot(self.state)["payers"][0]["partial_waivers"], False)
        domain.update_payer(self.state, payer["id"], {"partial_waivers": "yes"})
        self.assertNotIn("partial_waivers", payer, "on is the default, so it isn't stored")
        with self.assertRaises(domain.GateError) as refused:
            domain.update_payer(self.state, payer["id"], {"partial_waivers": "maybe"})
        self.assertIn("partial_waivers", refused.exception.fields)


class FinalWaiverGateTests(unittest.TestCase):  # decision 6, first half
    def test_no_final_waiver_while_retainage_is_held_and_no_override(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk", invoice_id=inv["id"])
        signed_waiver(state, job["id"], "unconditional_final")
        before = len(state["email_log"])
        for confirm in (False, True):
            with self.assertRaises(domain.GateError) as refused:
                domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"], kind="unconditional_final", confirm_final=confirm)
            self.assertEqual(refused.exception.extra["final_waiver_held"], ["$1,000.00 of retainage is still held"])
        self.assertEqual(len(state["email_log"]), before)
        self.assertEqual(domain.job_view(state, job)["final_waiver_holds"], ["$1,000.00 of retainage is still held"])
        # The progress one for this payment goes; the final one once the retainage is in.
        signed_waiver(state, job["id"], "unconditional_progress")
        domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"], kind="unconditional_progress")
        domain.record_retainage_payment(state, job["id"], 100_000, "ret")
        release = job["retainage_waivers"][-1]
        self.assertEqual(domain.final_waiver_holds(job), [])
        domain.send_waiver(state, job["id"], "Sam", release_id=release["id"], kind="unconditional_final")
        self.assertEqual(release["kind"], "unconditional_final")

    def test_a_part_paid_last_pay_app_holds_the_final_waiver(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], 600_000, "chk", invoice_id=inv["id"])
        signed_waiver(state, job["id"], "unconditional_final")
        with self.assertRaises(domain.GateError) as refused:
            domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"], kind="unconditional_final")
        self.assertIn("$4,000.00 is still owed", refused.exception.extra["final_waiver_held"])

    def test_the_final_waiver_goes_once_everything_is_paid(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "chk", invoice_id=inv["id"])
        signed_waiver(state, job["id"], "unconditional_final")
        domain.send_waiver(state, job["id"], "Sam", invoice_id=inv["id"], kind="unconditional_final")
        self.assertEqual(inv["waiver_kind"], "unconditional_final")


class FinalPayAppOverrideTests(unittest.TestCase):  # decision 6, second half
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        self.first = sent(self.state, self.job, 400_000)

    def test_refused_without_a_reason_while_an_earlier_pay_app_is_unpaid(self):
        with self.assertRaises(domain.GateError) as refused:
            domain.mark_done(self.state, self.job["id"], "Sam")
        self.assertIn("unpaid_reason", refused.exception.fields)
        self.assertEqual(str(refused.exception), f"{self.first['number']} is not paid yet. Enter why the final pay app goes now.")
        self.assertEqual(self.job["work_status"], "in_progress")
        domain.match_payment(self.state, self.job["id"], 100_000, "part", invoice_id=self.first["id"])
        with self.assertRaises(domain.GateError):
            domain.mark_done(self.state, self.job["id"], "Sam")   # partly paid is still unpaid

    def test_the_override_is_kept_audited_and_on_the_final_pay_app(self):
        domain.mark_done(self.state, self.job["id"], "Sam", unpaid_reason="  Owner pays both at closeout ")
        override = self.job["final_override"]
        self.assertEqual((override["by"], override["reason"], override["unpaid"]), ("Sam", "Owner pays both at closeout", [self.first["number"]]))
        self.assertTrue(override["at"])
        final = self.job["invoices"][-1]
        self.assertEqual(final["final_override"], override)
        entry = next(a for a in self.state["audit"] if a["action"] == "final_override")
        self.assertEqual((entry["actor"], entry["detail"]), ("Sam", f"Sam overrode the unpaid {self.first['number']} to draft the final pay app: Owner pays both at closeout."))
        view = domain.job_view(self.state, self.job)
        self.assertEqual(view["invoices"][-1]["final_override"]["reason"], "Owner pays both at closeout")

    def test_the_preview_says_what_is_unpaid_and_changes_nothing(self):
        preview = domain.final_pay_app_preview(self.state, self.job["id"])
        self.assertEqual(preview["unpaid"], [self.first["number"]])
        self.assertTrue(preview["drafted"])
        self.assertEqual(self.job["work_status"], "in_progress")

    def test_no_reason_needed_once_it_is_paid_and_undo_takes_the_override_off(self):
        domain.match_payment(self.state, self.job["id"], self.first["amount_cents"], "chk", invoice_id=self.first["id"])
        domain.mark_done(self.state, self.job["id"], "Sam")
        self.assertNotIn("final_override", self.job)
        state = new_state()
        job = new_job(state, contract=1_000_000)
        sent(state, job, 400_000)
        domain.mark_done(state, job["id"], "Sam", unpaid_reason="Agreed with the owner")
        domain.undo_mark_done(state, job["id"], "Sam")
        self.assertNotIn("final_override", job)

    def test_the_done_action_passes_the_reason(self):
        ctx = types.SimpleNamespace(actor="Sam", body={"notes": "", "period_to": "", "unpaid_reason": "Owner pays at closeout"})
        server.JOB_ACTIONS["done"][1](None, self.state, self.job["id"], None, ctx)
        self.assertEqual(self.job["final_override"]["reason"], "Owner pays at closeout")


GA_ADDRESS = "12 Peach St, Atlanta, GA 30303"


class GeorgiaFormTests(unittest.TestCase):  # decision 1
    def setUp(self):
        self.state = new_state()
        self.state["company"]["legal_name"] = "Volt Electric LLC"
        self.job = new_job(self.state, contract=1_000_000, name="Peachtree clinic", customer="Jones GC", role="GC")
        self.job["address"] = GA_ADDRESS
        self.inv = sent(self.state, self.job, 400_000)

    def forms(self):
        return domain.waiver_forms()["states"]["GA"]["statutory_forms"]

    def test_off_unless_the_server_names_the_state(self):
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": ""}):
            self.assertEqual(waiver_form.states_on(), set())
            page = waiver_form.forms_for_page()
            self.assertFalse(page["states"]["GA"]["fillable"])
            self.assertNotIn("statutory_forms", page["states"]["GA"], "the wording stays on the server")
            pdf, _, refused = waiver_form.for_waiver(self.state, self.job["id"], self.inv["id"])
            self.assertIsNone(pdf)
            self.assertTrue(refused)
        for value in ("GA", "ga, tx", "on"):
            with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": value}):
                self.assertEqual(waiver_form.states_on(), {"GA"}, value)
                self.assertTrue(waiver_form.forms_for_page()["states"]["GA"]["fillable"])

    def test_the_statute_titles_and_every_blank(self):
        forms = self.forms()
        self.assertEqual(forms["interim"]["title"], "Waiver and Release of Lien and Payment Bond Rights Upon Interim Payment")
        self.assertEqual(forms["final"]["title"], "Waiver and Release of Lien and Payment Bond Rights Upon Final Payment")
        self.assertEqual((forms["interim"]["subsection"], forms["final"]["subsection"]), ("(d)", "(e)"))
        for which in ("interim", "final"):
            blanks = re.findall(r"\{([a-z_]+)\}", " ".join(forms[which]["paragraphs"]))
            self.assertEqual(len(blanks), len(set(blanks)), which)
            self.assertEqual(set(blanks) - set(forms["blanks"]), set(), which)
            self.assertEqual(set(blanks) - set(waiver_form.BLANK_W) - {"property"}, set(), which)
            for needed in ("county", "contractor", "furnish", "project", "city", "project_county", "owner", "property", "amount",
                           "day", "month", "year", "claimant", "by", "its"):
                self.assertIn(needed, blanks, which)
            text = " ".join(forms[which]["paragraphs"])
            self.assertIn("NOTICE: WHEN YOU EXECUTE AND SUBMIT THIS DOCUMENT", text)
            self.assertIn("O.C.G.A. SECTION 44-14-366", text)
        self.assertIn("through", re.findall(r"\{([a-z_]+)\}", " ".join(forms["interim"]["paragraphs"])))
        self.assertNotIn("{through}", " ".join(forms["final"]["paragraphs"]))
        self.assertIn("RETAINED AMOUNTS", " ".join(forms["interim"]["paragraphs"]))

    def test_the_titles_read_as_a_statutory_waiver(self):
        for which in ("interim", "final"):
            self.assertTrue(doc_check._STATUTORY_TITLE.search(self.forms()[which]["title"]), which)

    def test_filled_in_twelve_point_fillable_one_page(self):
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": "GA"}):
            pdf, name, refused = waiver_form.for_waiver(self.state, self.job["id"], self.inv["id"], "interim")
        self.assertEqual(refused, "")
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(name.endswith("-GA-interim-waiver.pdf"))
        self.assertEqual(pdf.count(b"/Type /Page "), 1, "the notice is on the face of the form")
        self.assertEqual(set(re.findall(rb"/F[12] (\d+(?:\.\d+)?) Tf", pdf)), {b"12"})
        self.assertEqual(set(re.findall(rb"/Helv (\d+(?:\.\d+)?) Tf", pdf)), {b"12"})
        self.assertIn(b"/AcroForm", pdf)
        fields = pdf_fields(pdf)
        self.assertEqual(fields["amount"], "4,000.00")
        self.assertEqual(fields["project"], "Peachtree clinic")
        self.assertEqual(fields["contractor"], "Jones GC")
        self.assertEqual(fields["claimant"], "Volt Electric LLC")
        self.assertEqual(fields["city"], "Atlanta")
        self.assertEqual(fields["property"], GA_ADDRESS)
        self.assertEqual(fields["through"], self.inv["period_to"])
        self.assertEqual(fields["by"], "")
        self.assertIn(b"(WAIVER AND RELEASE OF LIEN AND PAYMENT BOND RIGHTS UPON INTERIM) Tj", pdf)

    def test_a_part_payment_form_names_what_came_in(self):
        domain.match_payment(self.state, self.job["id"], 150_000, "chk", invoice_id=self.inv["id"])
        values = waiver_form.prefill(self.state, self.job, self.inv["id"], "interim")
        self.assertEqual(values["amount"], "1,500.00")

    def test_no_final_form_while_money_is_to_come(self):
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": "GA"}):
            pdf, _, refused = waiver_form.for_waiver(self.state, self.job["id"], self.inv["id"], "final")
            self.assertIsNone(pdf)
            self.assertIn("A final waiver waits until all the money", refused)
            domain.match_payment(self.state, self.job["id"], self.inv["amount_cents"], "chk", invoice_id=self.inv["id"])
            domain.mark_done(self.state, self.job["id"])
            last = send_open(self.state, self.job)
            domain.match_payment(self.state, self.job["id"], last["amount_cents"], "chk2", invoice_id=last["id"])
            pdf, _, refused = waiver_form.for_waiver(self.state, self.job["id"], last["id"], "final")
        self.assertEqual(refused, "")
        self.assertIn(b"UPON FINAL", pdf)
        self.assertNotIn(b"(through)", pdf)

    def test_no_form_for_a_job_outside_georgia(self):
        self.job["address"] = "1 Main St, Rochester, NY 14604"
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": "on"}):
            pdf, _, refused = waiver_form.for_waiver(self.state, self.job["id"], self.inv["id"])
        self.assertIsNone(pdf)
        self.assertEqual(refused, "No state form is set up for this job's state.")


PASSWORD = "correct horse battery"


class WaiverFormRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._orig = (auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS)
        root = Path(cls.tmp.name)
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = root / "auth.json", root / "companies", root, root / "uploads"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR, importer.UPLOADS = cls._orig
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=""):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})}
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def test_the_form_is_found_only_once_switched_on(self):
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "ga@volt.example", "password": PASSWORD,
                                                           "name": "Pat", "company_name": "Volt"})
        cookie = res.getheader("Set-Cookie").split(";")[0]
        res, data = self.request("POST", "/api/jobs", {"name": "Peach clinic", "address": GA_ADDRESS, "customer": "Jones GC", "approver": "Mara",
                                                       "ap_email": "ap@jones.example", "contract_cents": 1_000_000, "phone": "(404) 555-0100", "role": "GC"}, cookie)
        self.assertEqual(res.status, 200, data)
        job = json.loads(data)["jobs"][-1]
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": ""}):
            res, data = self.request("GET", f"/api/jobs/{job['id']}/waiver-form/interim.pdf", cookie=cookie)
            self.assertEqual(res.status, 404)
            res, data = self.request("GET", "/api/waiver-forms", cookie=cookie)
            self.assertFalse(json.loads(data)["states"]["GA"]["fillable"])
            self.assertNotIn(b"NOTICE: WHEN YOU EXECUTE", data)
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": "GA"}):
            res, data = self.request("GET", f"/api/jobs/{job['id']}/waiver-form/interim.pdf", cookie=cookie)
            self.assertEqual((res.status, res.getheader("Content-Type")), (200, "application/pdf"))
            self.assertTrue(data.startswith(b"%PDF"))
            res, data = self.request("GET", f"/api/jobs/{job['id']}/waiver-form/final.pdf", cookie=cookie)
            self.assertEqual(res.status, 409, "not finished, so no final waiver yet")
            res, data = self.request("GET", "/api/waiver-forms", cookie=cookie)
            self.assertTrue(json.loads(data)["states"]["GA"]["fillable"])
        res, _ = self.request("GET", f"/api/jobs/{job['id']}/waiver-form/interim.pdf")
        self.assertEqual(res.status, 401)


if __name__ == "__main__":
    unittest.main()
