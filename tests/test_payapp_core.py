"""Pay-app core (Nov 27, Siteline comparison items 2-4): G702/G703 math with stored materials,
retainage by line and for one pay app, the retainage-only pay app (part and final), and a job
started mid-project with its earlier pay apps."""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain  # noqa: E402
import payapp_pdf  # noqa: E402
import qbo  # noqa: E402
import sov  # noqa: E402
import approve_help  # noqa: E402
from test_packet import new_state, new_job, draft  # noqa: E402
from waiver_help import signed_waiver  # noqa: E402

DAY = (date.today() - timedelta(days=60)).isoformat()
DAY2 = (date.today() - timedelta(days=30)).isoformat()


def schedule_job(retainage=10, packet="Invoice."):
    """$15,000: Labor, a lump sum of $10,000, and Materials, 100 ea at $50."""
    state = new_state()
    job = new_job(state, contract=1_500_000, retainage=retainage, packet=packet)
    domain.save_sov(state, job["id"], [{"item": "1", "description": "Labor", "quantity": "1", "unit": "LS", "rate": "10000"},
                                       {"item": "2", "description": "Materials", "quantity": "100", "unit": "ea", "rate": "50"}], "")
    labor, materials = (line["id"] for line in sov.lines(job))
    return state, job, labor, materials


def send(state, job):
    inv = draft(job)
    for item in inv["packet"]:
        if item["required"] and not item["present"]:
            domain.resolve_packet_item(state, job["id"], item["id"], reason="In hand")
    approve_help.approve_and_send(state, job["id"], "Owner")
    return inv


def columns(inv, name):
    entry = next(e for e in inv["sov_sheet"] if e["description"] == name)
    return sov.sheet_columns(entry, inv["retainage_pct"])


class StoredMaterialsMathTests(unittest.TestCase):
    def test_g703_columns_and_g702_lines_with_stored_materials(self):
        state, job, labor, materials = schedule_job()
        domain.record_sov_progress(state, job["id"], {labor: "0.5", materials: "20"}, stored={materials: "1500"})
        first = draft(job)
        m = columns(first, "Materials")
        # D + E + F = G: nothing before, $1,000 installed, $1,500 stored.
        self.assertEqual((m["previous"], m["this_period"], m["stored"], m["to_date"], m["balance"]), (0, 100_000, 150_000, 250_000, 250_000))
        self.assertEqual(m["previous"] + m["this_period"] + m["stored"], m["to_date"])
        self.assertEqual((first["gross_cents"], first["retainage_cents"], first["amount_cents"]), (750_000, 75_000, 675_000))
        send(state, job)
        f = payapp_pdf.summary(job, first)
        self.assertEqual((f["completed"], f["retainage"], f["earned"], f["due"]), (750_000, 75_000, 675_000, 675_000))
        # 5a + 5b = 5: 10% of $1,500 stored, the rest on work completed.
        self.assertEqual((f["retainage_stored"], f["retainage_work"], f["stored"]), (15_000, 60_000, 150_000))

        # Installed: the stored materials move into work done; billed only once.
        domain.record_sov_progress(state, job["id"], {labor: "0.7", materials: "50"}, stored={materials: "0"})
        second = draft(job)
        m = columns(second, "Materials")
        self.assertEqual((m["previous"], m["this_period"], m["stored"], m["to_date"]), (100_000, 150_000, 0, 250_000))
        line = next(e for e in second["sov_sheet"] if e["description"] == "Materials")
        self.assertEqual(line["amount_cents"], 0)
        self.assertEqual(second["gross_cents"], 200_000)   # labor only
        send(state, job)
        f = payapp_pdf.summary(job, second)
        self.assertEqual((f["completed"], f["retainage"], f["retainage_stored"], f["previous"], f["due"]), (950_000, 95_000, 0, 675_000, 180_000))
        self.assertEqual(f["contract_to_date"] - f["earned"], f["balance"])

    def test_stored_records_where_the_work_stood(self):
        state, job, labor, materials = schedule_job()
        domain.record_sov_progress(state, job["id"], {materials: "20"}, stored={materials: "500"})
        line = next(l for l in sov.lines(job) if l["id"] == materials)
        self.assertEqual(line["stored_qty_milli"], 20_000)

    def test_pdf_prints_5a_and_5b(self):
        state, job, labor, materials = schedule_job()
        domain.record_sov_progress(state, job["id"], {materials: "20"}, stored={materials: "1500"})
        pdf = payapp_pdf.render({"name": "Co"}, job, {}, draft(job))
        self.assertIn(b"a. On completed work", pdf)
        self.assertIn(b"b. On stored materials", pdf)


class StoredBackupTests(unittest.TestCase):
    def test_new_stored_materials_ask_for_backup_line_by_line(self):
        state, job, labor, materials = schedule_job()
        domain.record_sov_progress(state, job["id"], {labor: "0.5", materials: "20"}, stored={materials: "1500"})
        inv = draft(job)
        item = next(i for i in inv["packet"] if i["kind"] == "stored")
        self.assertEqual(item["covers"], ["2. Materials"])
        self.assertEqual(domain.item_gate(job, item), "soft")   # a check by default
        self.assertNotIn(item, domain.missing_packet(job))
        self.assertIn("Stored materials billed on 2. Materials", domain.soft_question(item))
        self.assertIn(item, domain.soft_checks_waiting(job, inv))
        # Required on this job: it stops the send until the line has its backup.
        domain.set_job_paperwork(state, job["id"], {"gates": {"stored": "hard"}})
        self.assertIn(item, domain.missing_packet(job))
        rec = _stored_record(state, job, "Supplier invoice 1182")
        domain.link_record_to_work(state, job["id"], rec["id"], sov_line_id=materials)
        domain.attach_record_to_packet(state, job["id"], item["id"], rec["id"])
        domain._refresh_draft_gate(job, state)
        self.assertTrue(item["present"], item)
        self.assertNotIn(item, domain.missing_packet(job))
        rows = domain.stored_backup_rows(job, inv)
        self.assertEqual([(r["name"], r["new_cents"], r["ok"]) for r in rows], [("2. Materials", 150_000, True)])

    def test_backup_tied_to_another_line_doesnt_count(self):
        state, job, labor, materials = schedule_job()
        domain.save_sov(state, job["id"], [{"item": "1", "description": "Pipe", "quantity": "100", "unit": "lf", "rate": "50"},
                                           {"item": "2", "description": "Fixtures", "quantity": "10", "unit": "ea", "rate": "1000"}], "")
        pipe, fixtures = (line["id"] for line in sov.lines(job))
        domain.set_job_paperwork(state, job["id"], {"gates": {"stored": "hard"}})
        domain.record_sov_progress(state, job["id"], {pipe: "10"}, stored={pipe: "1000", fixtures: "2000"})
        inv = draft(job)
        item = next(i for i in inv["packet"] if i["kind"] == "stored")
        rec = _stored_record(state, job, "Pipe receipt")
        domain.link_record_to_work(state, job["id"], rec["id"], sov_line_id=pipe)
        domain.attach_record_to_packet(state, job["id"], item["id"], rec["id"])
        domain._refresh_draft_gate(job, state)
        self.assertFalse(item["present"])
        self.assertEqual(item["stored_short"], ["2. Fixtures"])
        self.assertIn("2. Fixtures", domain._blocker_text(job, item))

    def test_no_new_stored_materials_no_item(self):
        state, job, labor, materials = schedule_job()
        domain.record_sov_progress(state, job["id"], {labor: "0.5"})
        self.assertFalse([i for i in draft(job)["packet"] if i["kind"] == "stored"])


def _stored_record(state, job, label):
    fid = f"file-{len(state['files']) + 1}"
    state["files"].append({"id": fid, "name": f"{label}.pdf", "ext": ".pdf", "size": 10})
    domain.add_job_record(state, job["id"], label, "stored")
    rec = job["records"][-1]
    rec["file_ids"] = [fid]
    return rec


class LineRetainageTests(unittest.TestCase):
    def test_one_percent_holds_what_it_always_did(self):
        state, job, labor, materials = schedule_job(retainage=10)
        domain.record_sov_progress(state, job["id"], {labor: "0.333", materials: "7"})
        inv = draft(job)
        self.assertEqual(inv["retainage_cents"], (inv["gross_cents"] * 10 + 50) // 100)
        self.assertEqual(inv["lines"][-1]["label"], "Less retainage 10%")

    def test_a_line_at_its_own_percent(self):
        state, job, labor, materials = schedule_job(retainage=10)
        domain.set_line_retainage(state, job["id"], {materials: "0"})
        domain.record_sov_progress(state, job["id"], {labor: "0.5", materials: "40"})
        inv = draft(job)
        # $5,000 labor at 10%, $2,000 materials at 0%.
        self.assertEqual((inv["gross_cents"], inv["retainage_cents"]), (700_000, 50_000))
        self.assertEqual(inv["lines"][-1]["label"], "Less retainage 10%")
        self.assertEqual(columns(inv, "Materials")["retainage"], 0)
        self.assertEqual(columns(inv, "Labor")["retainage"], 50_000)
        domain.set_line_retainage(state, job["id"], {materials: "5"})
        inv = draft(job)   # the open draft follows
        self.assertEqual(inv["retainage_cents"], 50_000 + 10_000)
        self.assertEqual(inv["lines"][-1]["label"], "Less retainage (10% and 5% by line)")
        send(state, job)
        self.assertEqual(payapp_pdf.summary(job, inv)["retainage"], 60_000)
        self.assertEqual(domain.job_money(job)["retainage_open_cents"], 60_000)
        # Back to the job's percent from the next pay app; this one keeps what it held.
        domain.set_line_retainage(state, job["id"], {materials: ""})
        self.assertNotIn("retainage_pct", next(l for l in sov.lines(job) if l["id"] == materials))
        self.assertEqual(inv["retainage_cents"], 60_000)

    def test_refuses_a_bad_percent(self):
        state, job, labor, materials = schedule_job()
        with self.assertRaises(domain.GateError) as raised:
            domain.set_line_retainage(state, job["id"], {materials: "45"})
        self.assertIn(f"pct_{materials}", raised.exception.fields)

    def test_this_period_override(self):
        state, job, labor, materials = schedule_job(retainage=10)
        domain.set_line_retainage(state, job["id"], {materials: "0"})
        domain.record_sov_progress(state, job["id"], {labor: "0.5", materials: "40"})
        number = draft(job)["number"]
        domain.set_payapp_retainage(state, job["id"], "5")
        inv = draft(job)
        self.assertEqual((inv["number"], inv["retainage_cents"], inv["retainage_pct"]), (number, 35_000, 5))
        # New quantities keep the override on this draft.
        domain.record_sov_progress(state, job["id"], {labor: "0.6"})
        inv = draft(job)
        self.assertEqual((inv["retainage_pct_override"], inv["retainage_cents"]), (5, 40_000))
        send(state, job)
        # The next pay app holds the usual percents again.
        domain.record_sov_progress(state, job["id"], {labor: "0.7"})
        self.assertEqual(draft(job)["retainage_cents"], 10_000)
        domain.set_payapp_retainage(state, job["id"], "0")
        self.assertEqual(draft(job)["retainage_cents"], 0)
        domain.set_payapp_retainage(state, job["id"], "")
        self.assertEqual(draft(job)["retainage_cents"], 10_000)


class RetainageReleaseTests(unittest.TestCase):
    def _billed(self):
        state, job, labor, materials = schedule_job(retainage=10)
        domain.record_sov_progress(state, job["id"], {labor: "1", materials: "50"})
        inv = send(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        return state, job, labor, materials

    def test_partial_release_at_substantial_completion(self):
        state, job, labor, materials = self._billed()
        self.assertEqual(domain.job_money(job)["retainage_open_cents"], 125_000)
        domain.draft_retainage_release(state, job["id"], 50_000, "substantial")
        rel = draft(job)
        self.assertEqual((rel["number"], rel["amount_cents"], rel["gross_cents"], rel["retainage_cents"], rel["final"]),
                         ("PA-PINE-02", 50_000, 0, -50_000, False))
        self.assertTrue(rel["lines"][0]["retainage_release"])
        self.assertIn("$500.00 of the $1,250.00 held", rel["lines"][0]["label"])
        # Not asked for or recorded twice while it is a draft.
        with self.assertRaises(domain.GateError):
            domain.record_retainage_payment(state, job["id"], 100_000)
        # Work can't be drafted over it.
        with self.assertRaises(domain.GateError):
            domain.record_sov_progress(state, job["id"], {materials: "60"})
        send(state, job)
        f = payapp_pdf.summary(job, rel)
        self.assertEqual((f["completed"], f["retainage"], f["earned"], f["previous"], f["due"]), (1_250_000, 75_000, 1_175_000, 1_125_000, 50_000))
        m = domain.job_money(job)
        self.assertEqual((m["retainage_open_cents"], m["outstanding_cents"]), (75_000, 50_000))
        # No new work on the continuation sheet.
        self.assertTrue(all(e["amount_cents"] == 0 and e["work_this_cents"] == 0 for e in rel["sov_sheet"]))
        self.assertEqual(sum(e["to_date_cents"] for e in rel["sov_sheet"]), 1_250_000)
        # Its payment brings its waiver due: progress, since retainage is still held.
        domain.match_payment(state, job["id"], 50_000, "CHK-2", invoice_id=rel["id"])
        self.assertEqual(rel["status"], "paid")
        self.assertTrue(rel["waiver_due"])
        self.assertEqual(domain.suggested_waiver_kind(job, rel), "unconditional_progress")
        # QuickBooks: the release is a Retainage line.
        self.assertEqual([r["Item"] for r in qbo.invoice_rows(state, job, rel)], ["Retainage"])
        self.assertIn(b"releases $500.00 of the retainage held", payapp_pdf.render({"name": "Co"}, job, {}, rel))

    def test_final_release_is_the_final_payment(self):
        state, job, labor, materials = self._billed()
        with self.assertRaises(domain.GateError) as early:
            domain.draft_retainage_release(state, job["id"], None, "final")
        self.assertIn("once the work is finished", str(early.exception))
        domain.mark_done(state, job["id"])
        last = send(state, job)   # the rest of the work, on the final pay app
        domain.match_payment(state, job["id"], last["amount_cents"], "CHK-2", invoice_id=last["id"])
        for inv in job["invoices"]:
            if inv.get("waiver_due") and not inv.get("waiver_sent"):
                rec = signed_waiver(state, job["id"], label=f"Waiver {inv['number']}")
                domain.set_waiver_type(state, job["id"], rec["id"], domain.suggested_waiver_kind(job, inv), for_id=inv["id"])
                domain.send_waiver(state, job["id"], "Owner", invoice_id=inv["id"])
        for item in job["closeout_packet"]:
            domain.resolve_packet_item(state, job["id"], item["id"], reason="In the binder")
        domain.draft_retainage_release(state, job["id"], None, "final")
        rel = draft(job)
        self.assertEqual((rel["amount_cents"], rel["final"]), (150_000, True))
        cond = next(i for i in rel["packet"] if str(i.get("waiver_kind", "")).startswith("conditional"))
        self.assertEqual(cond["waiver_kind"], "conditional_final")
        send(state, job)
        self.assertTrue(rel["final"])
        self.assertEqual(domain.job_money(job)["retainage_open_cents"], 0)
        self.assertEqual(payapp_pdf.summary(job, rel)["retainage"], 0)
        domain.match_payment(state, job["id"], 150_000, "CHK-3", invoice_id=rel["id"])
        self.assertEqual(domain.suggested_waiver_kind(job, rel), "unconditional_final")
        self.assertTrue(rel["waiver_due"])
        signed_waiver(state, job["id"], "unconditional_final")
        domain.send_waiver(state, job["id"], "Owner", invoice_id=rel["id"])
        domain.close_job(state, job["id"])
        self.assertTrue(job["closed"])

    def test_refusals(self):
        state, job, labor, materials = schedule_job(retainage=0)
        with self.assertRaises(domain.GateError):
            domain.draft_retainage_release(state, job["id"], None, "substantial")
        state, job, labor, materials = self._billed()
        with self.assertRaises(domain.GateError) as too_much:
            domain.draft_retainage_release(state, job["id"], 200_000, "substantial")
        self.assertIn("amount_cents", too_much.exception.fields)
        with self.assertRaises(domain.GateError):
            domain.draft_retainage_release(state, job["id"], None, "sometime")


class EarlierPayAppsTests(unittest.TestCase):
    def test_pay_app_by_pay_app(self):
        state, job, labor, materials = schedule_job(retainage=10)
        domain.import_earlier_payapps(state, job["id"], {"payapps": [
            {"number": "1", "period_to": DAY, "lines": {labor: {"pct": "20"}, materials: {"amount": "500"}}, "paid": "2250", "paid_on": DAY2},
            {"number": "2", "period_to": DAY2, "lines": {labor: {"pct": "40"}}, "retainage": "200", "paid": "0"},
        ]})
        one, two = domain.imported_invoices(job)
        self.assertEqual((one["number"], one["gross_cents"], one["retainage_cents"], one["amount_cents"], one["status"]),
                         ("PA-PINE-01", 250_000, 25_000, 225_000, "paid"))
        self.assertEqual((two["number"], two["gross_cents"], two["retainage_cents"], two["amount_cents"], two["status"]),
                         ("PA-PINE-02", 200_000, 20_000, 180_000, "sent"))
        m = domain.job_money(job)
        self.assertEqual((m["billed_cents"], m["payments_cents"], m["outstanding_cents"], m["retainage_open_cents"], m["unbilled_completed_cents"]),
                         (405_000, 225_000, 180_000, 45_000, 0))
        # The next pay app follows them, and its previous column counts them.
        domain.record_sov_progress(state, job["id"], {labor: "0.5", materials: "20"})
        nxt = draft(job)
        self.assertEqual(nxt["number"], "PA-PINE-03")
        lab = columns(nxt, "Labor")
        self.assertEqual((lab["previous"], lab["this_period"], lab["to_date"]), (400_000, 100_000, 500_000))
        mat = columns(nxt, "Materials")
        self.assertEqual((mat["previous"], mat["this_period"], mat["to_date"]), (50_000, 50_000, 100_000))
        f = payapp_pdf.summary(job, nxt)
        self.assertEqual((f["completed"], f["retainage"], f["previous"], f["due"]), (600_000, 60_000, 405_000, 135_000))
        # Not in QuickBooks, not pulled back.
        self.assertFalse(domain.qbo_pushable(one))
        with self.assertRaises(domain.GateError):
            qbo.invoice_rows(state, job, one)
        with self.assertRaises(domain.GateError):
            domain.void_sent_pay_app(state, job["id"], two["id"], "wrong")
        # What is still owed on an earlier pay app is recorded like any other's; only new money owes a waiver.
        domain.match_payment(state, job["id"], 180_000, "CHK-9", invoice_id=two["id"])
        self.assertEqual(two["status"], "paid")
        self.assertEqual(domain.waiver_cents(job, two), 180_000)

    def test_summary_to_date(self):
        state, job, labor, materials = schedule_job(retainage=10)
        domain.import_earlier_payapps(state, job["id"], {"summary": {
            "number": "4", "period_to": DAY, "lines": {labor: {"to_date": "6000"}, materials: {"pct": "50"}},
            "retainage": "850", "paid": "7650", "paid_on": DAY}})
        (inv,) = domain.imported_invoices(job)
        self.assertEqual((inv["number"], inv["gross_cents"], inv["retainage_cents"], inv["amount_cents"], inv["status"], inv["imported_as"]),
                         ("PA-PINE-04", 850_000, 85_000, 765_000, "paid", "summary"))
        self.assertEqual(domain._next_number(job), "PA-PINE-05")
        self.assertEqual(domain.job_money(job)["retainage_open_cents"], 85_000)
        self.assertEqual(next(l for l in sov.lines(job) if l["id"] == materials)["done_milli"], 50_000)
        # The schedule is fixed now, like after any pay app sent.
        with self.assertRaises(domain.GateError):
            domain.save_sov(state, job["id"], [{"description": "X", "quantity": "1", "rate": "15000"}], "")

    def test_refusals_and_taking_them_off(self):
        state, job, labor, materials = schedule_job(retainage=10)
        with self.assertRaises(domain.GateError):   # more than the line
            domain.import_earlier_payapps(state, job["id"], {"payapps": [{"number": "1", "period_to": DAY, "lines": {materials: {"amount": "9000"}}}]})
        with self.assertRaises(domain.GateError):   # paid more than billed
            domain.import_earlier_payapps(state, job["id"], {"payapps": [{"number": "1", "period_to": DAY, "lines": {labor: {"amount": "1000"}}, "paid": "5000"}]})
        with self.assertRaises(domain.GateError):   # a date ahead
            domain.import_earlier_payapps(state, job["id"], {"payapps": [{"number": "1", "period_to": "2099-01-01", "lines": {labor: {"amount": "1000"}}}]})
        domain.import_earlier_payapps(state, job["id"], {"payapps": [{"number": "1", "period_to": DAY, "lines": {labor: {"amount": "1000"}}, "paid": "900"}]})
        with self.assertRaises(domain.GateError):   # the same number again
            domain.import_earlier_payapps(state, job["id"], {"payapps": [{"number": "1", "period_to": DAY, "lines": {labor: {"amount": "1000"}}}]})
        domain.remove_earlier_payapps(state, job["id"])
        self.assertEqual(domain.imported_invoices(job), [])
        self.assertEqual(job["payments"], [])
        self.assertEqual(domain._next_number(job), "PA-PINE-01")
        self.assertEqual(next(l for l in sov.lines(job) if l["id"] == labor)["done_milli"], 0)
        # Once a pay app is made here, earlier ones can't be entered.
        domain.record_sov_progress(state, job["id"], {labor: "0.1"})
        with self.assertRaises(domain.GateError):
            domain.import_earlier_payapps(state, job["id"], {"payapps": [{"number": "1", "period_to": DAY, "lines": {labor: {"amount": "1000"}}}]})

    def test_old_pdf_kept_with_it(self):
        state, job, labor, materials = schedule_job()
        domain.import_earlier_payapps(state, job["id"], {"payapps": [{"number": "1", "period_to": DAY, "lines": {labor: {"amount": "1000"}}}]})
        (inv,) = domain.imported_invoices(job)
        domain.add_job_record(state, job["id"], "PA 1.pdf", "pay_app")
        rec = job["records"][-1]
        domain.attach_history_file(state, job["id"], inv["id"], rec["id"])
        self.assertEqual(inv["history_record_ids"], [rec["id"]])
        view = domain.job_view(state, job)
        self.assertEqual(view["earlier_payapps"]["count"], 1)
        self.assertEqual(view["earlier_payapps"]["next_number"], "PA-PINE-02")


if __name__ == "__main__":
    unittest.main()


from test_hunt_nov5_http import Base as HttpBase  # noqa: E402


class ThroughTheServer(HttpBase):
    def test_routes_and_who_may_take_them(self):
        job = self.job()
        line = job["sov"]["lines"][0]["id"]
        body = {"payapps": [{"number": "3", "period_to": DAY, "lines": {line: {"pct": "25"}}, "paid": "5000", "paid_on": DAY2}]}
        self.assertEqual(self.post(f"/api/jobs/{self.job_id}/earlier-payapps", body, cookie=self.bob)[0], 403)
        status, data = self.post(f"/api/jobs/{self.job_id}/earlier-payapps", body)
        self.assertEqual(status, 200, data)
        job = self.job()
        earlier = next(i for i in job["invoices"] if i.get("imported"))
        self.assertEqual((earlier["number"], earlier["gross_cents"], job["earlier_payapps"]["next_number"]), (f"PA-{job['code']}-03", 500_000, f"PA-{job['code']}-04"))
        # The old PDF goes on the job records, tied to it.
        res, data = self.upload("PA3.pdf", b"%PDF-1.4 old pay app", kind="pay_app", history=earlier["id"])
        self.assertEqual(res.status, 200, data)
        earlier = next(i for i in self.job()["invoices"] if i.get("imported"))
        self.assertEqual(len(earlier["history_files"]), 1)
        # Retainage by line takes manage_jobs; a release, ask_retainage.
        self.assertEqual(self.post(f"/api/jobs/{self.job_id}/line-retainage", {"pcts": {line: "5"}}, cookie=self.bob)[0], 403)
        self.assertEqual(self.post(f"/api/jobs/{self.job_id}/line-retainage", {"pcts": {line: "5"}})[0], 200)
        self.assertEqual(self.post(f"/api/jobs/{self.job_id}/retainage-release", {"stage": "substantial"}, cookie=self.bob)[0], 403)
        status, data = self.post(f"/api/jobs/{self.job_id}/retainage-release", {"stage": "substantial"})
        self.assertEqual(status, 409, data)   # this job holds no retainage
        self.assertIn("No retainage", data["error"])
        # Stored materials and their backup, tied to the line by the upload.
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line: "0.4"}, "stored": {line: "1000"}})
        self.assertEqual(status, 200, data)
        item = next(p for p in self.draft()["packet"] if p["kind"] == "stored")
        res, data = self.upload("receipt.pdf", b"%PDF-1.4 supplier receipt", kind="stored", attach=item["id"], sov_line=line)
        self.assertEqual(res.status, 200, data)
        rec = next(r for r in self.job()["records"] if r["kind"] == "stored")
        self.assertEqual(rec["sov_line_id"], line)
        rows = self.draft()["stored_backup"]
        self.assertEqual([(r["new_cents"], r["records"][0]["id"]) for r in rows], [(100_000, rec["id"])])
        # This period's own retainage, on the draft.
        status, data = self.post(f"/api/jobs/{self.job_id}/payapp-retainage", {"pct": "10"}, cookie=self.bob)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.draft()["retainage_pct_override"], 10)
