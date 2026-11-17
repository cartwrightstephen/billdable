"""The live demo company, Redbud Electric (demo_seed.py; Stephen, 10 Nov, decision 11): an
electrical sub with a GC client, real schedules of values, job records with tickets and time
sheets, 10% retainage, and a job at each stage a demo should show. Its money adds up by the app's
own arithmetic, and the server makes it, replaces an older demo once, and resets to it."""

import json
import re
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import demo_seed
import domain
import financials
import importer
import migrations
import server
import sov
from seed import seed_state

TODAY = date(2026, 11, 17)
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")


def built(today=TODAY):
    if today not in _BUILT:
        _BUILT[today] = demo_seed.build(today)
    return _BUILT[today]


_BUILT: dict = {}


def live(state):
    return [j for j in state["jobs"] if not j.get("closed")]


def billed(job):
    return [inv for inv in job["invoices"] if inv["status"] in domain.BILLED_STATUSES]


class ScheduleTests(unittest.TestCase):
    def test_each_schedule_of_values_totals_its_contract(self):
        state, _ = built()
        names = {"westside": "Westside Lofts, Building B", "edgewood": "Edgewood Retail, Suite 140",
                 "summit": "Summit Ridge Medical Office", "kirkwood": "Kirkwood Library lighting retrofit",
                 "cascade": "Cascade Road Warehouse service upgrade", "grantpark": "Grant Park Townhomes, Phase 1",
                 "lakewood": "Lakewood Self Storage, Phase 1", "piedmont": "Piedmont Pediatrics, Suite 300"}
        self.assertEqual(sorted(j["name"] for j in state["jobs"]), sorted(names.values()))
        for key, name in names.items():
            job = next(j for j in state["jobs"] if j["name"] == name)
            rows = demo_seed.SCHEDULES[key]
            with self.subTest(job=name):
                self.assertEqual(job["contract_cents"], demo_seed.schedule_total_cents(rows))
                self.assertEqual(sov.totals(job)["contract_cents"], job["contract_cents"])
                self.assertEqual([l["description"] for l in job["sov"]["lines"] if not l.get("co_id")], [r[0] for r in rows])
                for line in job["sov"]["lines"]:
                    self.assertEqual(line["scheduled_cents"], sov.extend(line["qty_milli"], line["rate_cents"]))
                    self.assertLessEqual(line["done_milli"], line["qty_milli"])
                    self.assertGreater(line["rate_cents"], 0)

    def test_electrical_lines_with_quantities_and_unit_rates(self):
        words = " ".join(r[0].lower() for rows in demo_seed.SCHEDULES.values() for r in rows)
        for word in ("rough-in", "feeders", "panel", "devices", "fixtures"):
            self.assertIn(word, words)
        westside = demo_seed.SCHEDULES["westside"]
        self.assertTrue(any(q != 1 and u != "LS" for _, u, q, _ in westside), "unit-rate lines, not only lump sums")


class MoneyTests(unittest.TestCase):
    def test_every_job_adds_up(self):
        state, _ = built()
        for job in state["jobs"]:
            m = domain.job_money(job)
            with self.subTest(job=job["name"]):
                self.assertEqual(job["retainage_pct"], 10)
                # Done to date is what the job records put in place (each line at its field total);
                # the pay apps bill up to it.
                field = sov.totals({"sov": {"op_bp": sov.op_bp(job), "lines": [sov.anticipated(job, l) for l in sov.lines(job)]}})
                self.assertEqual(job["completed_value_cents"], field["to_date_cents"])
                self.assertGreaterEqual(job["completed_value_cents"], sov.totals(job)["to_date_cents"])
                for inv in billed(job):
                    self.assertEqual(inv["gross_cents"] - inv["retainage_cents"], inv["amount_cents"])
                    self.assertEqual(inv["retainage_cents"], sov.retainage_on(inv["gross_cents"], 10))
                    self.assertEqual(sum(l["amount_cents"] for l in inv["lines"]), inv["amount_cents"])
                    self.assertEqual(sum(e["amount_cents"] for e in inv["sov_sheet"]), inv["gross_cents"])
                    paid = sum(p["amount_cents"] for p in job["payments"] if p.get("invoice_id") == inv["id"])
                    self.assertEqual(paid, inv["amount_cents"] if inv["status"] == "paid" else 0)
                gross = sum(inv["gross_cents"] for inv in billed(job))
                self.assertLessEqual(gross, job["completed_value_cents"])
                self.assertEqual(m["billed_cents"], sum(inv["amount_cents"] for inv in billed(job)))
                self.assertEqual(m["outstanding_cents"], sum(inv["amount_cents"] for inv in billed(job) if inv["status"] != "paid"))
                held = sum(inv["retainage_cents"] for inv in billed(job))
                self.assertEqual(m["retainage_open_cents"], held - job.get("retainage_released_cents", 0))
                self.assertEqual(m["overbilled_cents"], 0)
                if job.get("closed"):
                    self.assertEqual((m["outstanding_cents"], m["retainage_open_cents"], m["unbilled_completed_cents"]), (0, 0, 0))
                    self.assertEqual(job["completed_value_cents"], job["contract_cents"])

    def test_the_company_figures_look_like_a_small_electrical_sub(self):
        state, _ = built()
        f = financials.compute(state, today=TODAY)
        self.assertEqual(f["company"], "Redbud Electric Co.")
        self.assertEqual(sum(b["cents"] for b in f["aging"]), f["receivable_cents"])
        self.assertTrue(50_000_00 < f["receivable_cents"] < 150_000_00, f["receivable_cents"])
        self.assertTrue(10_000_00 < f["retainage_cents"] < 60_000_00, f["retainage_cents"])
        self.assertTrue(50_000_00 < f["backlog_cents"] < 300_000_00, f["backlog_cents"])
        self.assertTrue(10_000_00 < f["unbilled_cents"] < 80_000_00, f["unbilled_cents"])
        self.assertTrue(300_000_00 < f["billed_12m_cents"] < 1_000_000_00, f["billed_12m_cents"])
        self.assertGreater(f["paid_count"], 5)
        self.assertEqual(f["pending_co_cents"], 1_485_000)
        self.assertGreater(sum(1 for b in f["aging"] if b["cents"]), 1, "owed money of more than one age")
        self.assertEqual(f["overbilled_cents"], 0)


class StageTests(unittest.TestCase):
    def stages(self, state):
        out = {}
        for job in live(state):
            m = domain.job_money(job)
            draft = any(inv["status"] in domain.DRAFT_STATUSES for inv in job["invoices"])
            if draft:
                out.setdefault("draft", job["name"])
            if any(inv["status"] == "sent" and inv.get("sent_at") for inv in job["invoices"]):
                out.setdefault("sent_unpaid", job["name"])
            if any(inv["status"] == "paid" and inv.get("waiver_due") and not inv.get("waiver_sent") for inv in job["invoices"]):
                out.setdefault("paid_waiver_due", job["name"])
            if any(co["status"] == "pending" and co.get("signature_requested_at") for co in job["change_orders"]):
                out.setdefault("co_awaiting_signature", job["name"])
            if job["work_status"] == "complete" and m["retainage_open_cents"] > 0 and not m["outstanding_cents"]:
                out.setdefault("finished_retainage_held", job["name"])
            if m["unbilled_completed_cents"] > 0 and not draft:
                out.setdefault("done_not_billed", job["name"])
        return out

    def test_a_job_at_every_stage(self):
        state, _ = built()
        self.assertEqual(set(self.stages(state)), {"draft", "sent_unpaid", "paid_waiver_due", "co_awaiting_signature",
                                                   "finished_retainage_held", "done_not_billed"})
        self.assertGreaterEqual(sum(1 for j in state["jobs"] if j.get("closed")), 2, "a history of finished jobs")

    def test_the_stages_hold_on_any_day_it_is_built(self):
        for day in (date(2027, 3, 1), date(2026, 12, 31)):
            state, _ = built(day)
            with self.subTest(day=day):
                self.assertEqual(len(self.stages(state)), 6)
                self.assertTrue(domain.check_audit(state)["ok"])

    def test_the_draft_is_ready_to_send(self):
        state, _ = built()
        job = next(j for j in state["jobs"] if j["name"] == "Summit Ridge Medical Office")
        draft = next(inv for inv in job["invoices"] if inv["status"] in domain.DRAFT_STATUSES)
        self.assertEqual(draft["status"], "awaiting_approval")
        self.assertEqual(domain.blocker_lines(job), [])

    def test_the_finished_job_waits_on_one_closeout_document(self):
        state, _ = built()
        job = next(j for j in state["jobs"] if j["name"] == "Kirkwood Library lighting retrofit")
        self.assertEqual([i["label"] for i in domain.closeout_missing(job)], ["Completion certificate"])
        with self.assertRaises(domain.GateError):
            domain.chase_retainage(json.loads(json.dumps(state)), job["id"], "test")


class GcAndRecordsTests(unittest.TestCase):
    def test_a_general_contractor_is_the_main_client(self):
        state, _ = built()
        by_client = {}
        for job in state["jobs"]:
            by_client[job["payer_id"]] = by_client.get(job["payer_id"], 0) + job["contract_cents"]
        main = max(by_client, key=by_client.get)
        payer = next(p for p in state["payers"] if p["id"] == main)
        self.assertEqual((payer["name"], payer["client_type"], payer["role"]), ("Granite Row Builders", "gc", "GC"))
        for p in state["payers"]:
            self.assertTrue(p["ap_email"].endswith(".example"), p["ap_email"])
            self.assertTrue(re.match(r"^\(\d{3}\) 555-01\d\d$", p["phone"]), p["phone"])

    def test_job_records_have_delivery_tickets_time_sheets_and_photos(self):
        state, files = built()
        for job in live(state):
            recs = job["records"]
            with self.subTest(job=job["name"]):
                self.assertTrue(any(r.get("ticket_type") == "delivery" and r.get("file_ids") for r in recs))
                self.assertTrue(any(r.get("ticket_type") == "time_sheet" and r.get("file_ids") for r in recs))
                self.assertTrue(any(r["kind"] == "photo" for r in recs))
                self.assertTrue(job["daily_records"])
                self.assertTrue(any(l.get("field_milli") for l in job["sov"]["lines"]), "quantities from the field")

    def test_every_file_is_a_pdf_on_a_record(self):
        state, files = built()
        metas = {m["id"]: m for m in state["files"]}
        self.assertEqual(set(metas), set(files))
        on_records = {fid for j in state["jobs"] for r in j.get("records", []) for fid in r.get("file_ids") or []}
        self.assertEqual(on_records, set(files))
        for fid, data in files.items():
            self.assertTrue(data.startswith(b"%PDF-1.4"))
            self.assertEqual(metas[fid]["size"], len(data))

    def test_dates_are_in_the_past_and_the_audit_chain_holds(self):
        state, _ = built()
        late = []

        def walk(o, key=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, k)
            elif isinstance(o, list):
                for v in o:
                    walk(v, key)
            elif isinstance(o, str) and ISO_DAY.match(o) and o[:10] > TODAY.isoformat() and key != "due_on":
                late.append((key, o))
        walk(state)
        self.assertEqual(late, [])
        self.assertTrue(domain.check_audit(state)["ok"])
        self.assertEqual(state["schema_version"], migrations.SCHEMA_VERSION)
        self.assertTrue(all(e["status"] == "not sent: demo account" and e["sample"] for e in state["email_log"]))
        self.assertTrue(all(e["to"].endswith(".example") for e in state["email_log"]))

    def test_names_are_fictional(self):
        state, _ = built()
        text = json.dumps(state)
        self.assertNotIn("Hartline", text)
        for email in set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)):
            self.assertTrue(email.endswith(".example"), email)
        self.assertEqual(auth.DEMO_EMAIL, demo_seed.OWNER_EMAIL)


class PatchedCreateJobTests(unittest.TestCase):
    def test_the_schedules_are_made_even_when_new_jobs_come_without_one(self):
        create = domain.create_job
        with mock.patch.object(domain, "create_job", lambda *a, **k: create(*a, **{**k, "auto_schedule": False})):
            state, _ = demo_seed.build(TODAY)
        self.assertTrue(all(sov.has_sov(j) for j in state["jobs"]))


class ClockTests(unittest.TestCase):
    def test_other_threads_read_the_real_time_while_the_demo_is_built(self):
        seen = []
        real = domain.utcnow

        def watch():
            seen.append((domain.utcnow(), datetime.now(timezone.utc).date()))

        def build_and_watch():
            with demo_seed._demo_clock():
                demo_seed._LOCAL.now = datetime(2020, 1, 1, tzinfo=timezone.utc)
                self.assertTrue(domain.utcnow().startswith("2020-01-01"))
                t = threading.Thread(target=watch)
                t.start()
                t.join()
        build_and_watch()
        self.assertFalse(seen[0][0].startswith("2020"))
        self.assertIs(domain.utcnow, real)
        self.assertIs(domain.datetime, datetime)


class ServerTests(unittest.TestCase):
    """ensure_demo and Reset the demo on a temporary data directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        patches = [mock.patch.object(auth, "AUTH_PATH", root / "auth.json"), mock.patch.object(server, "COMPANIES", root / "companies"),
                   mock.patch.object(importer, "UPLOADS", root / "uploads"), mock.patch.dict("os.environ", {"DURACASH_DEMO_SEED": ""})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def demo(self):
        return server.load_company(auth.DEMO_COMPANY_ID)

    def test_a_new_server_makes_the_electrical_demo_with_its_files(self):
        server.ensure_demo()
        state = self.demo()
        self.assertEqual(state["company"]["name"], "Redbud Electric Co.")
        self.assertEqual(state["company"]["demo_seed"], demo_seed.SEED_VERSION)
        self.assertEqual(auth.user_by_email(auth.DEMO_EMAIL)["name"], "Dana Whitfield")
        for meta in state["files"]:
            self.assertTrue(importer.file_path(auth.DEMO_COMPANY_ID, meta).read_bytes().startswith(b"%PDF"))

    def test_an_older_demo_is_made_again_once(self):
        server.save_company(auth.DEMO_COMPANY_ID, seed_state())   # the Hartline demo on a server today
        server.ensure_demo()
        state = self.demo()
        self.assertEqual(state["company"]["name"], "Redbud Electric Co.")
        # What someone does in the new demo stays until a reset.
        state["company"]["name"] = "Changed while looking around"
        server.save_company(auth.DEMO_COMPANY_ID, state)
        server.ensure_demo()
        self.assertEqual(self.demo()["company"]["name"], "Changed while looking around")

    def test_reset_the_demo_brings_back_the_electrical_demo(self):
        server.ensure_demo()
        stray = importer.UPLOADS / auth.DEMO_COMPANY_ID / "file-added-while-looking.pdf"
        stray.write_bytes(b"%PDF-1.4 x")
        ctx = mock.Mock(company_id=auth.DEMO_COMPANY_ID, body={})
        fresh = server.Handler._m_reset(None, {"company": {"name": "x"}, "jobs": []}, None, ctx)
        self.assertEqual(fresh["company"]["name"], "Redbud Electric Co.")
        self.assertFalse(stray.exists())
        for meta in fresh["files"]:
            self.assertTrue(importer.file_path(auth.DEMO_COMPANY_ID, meta).exists())

    def test_the_hartline_seed_for_the_browser_suite(self):
        with mock.patch.dict("os.environ", {"DURACASH_DEMO_SEED": "hartline"}):
            server.ensure_demo()
            self.assertEqual(self.demo()["company"]["name"], "Hartline Remodeling")
            server.ensure_demo()   # not replaced by the electrical demo
            self.assertEqual(self.demo()["company"]["name"], "Hartline Remodeling")
            ctx = mock.Mock(company_id=auth.DEMO_COMPANY_ID, body={})
            self.assertEqual(server.Handler._m_reset(None, {}, None, ctx)["company"]["name"], "Hartline Remodeling")
        self.assertFalse((importer.UPLOADS / auth.DEMO_COMPANY_ID).exists())


class DemoAccountTests(unittest.TestCase):
    def test_the_old_demo_account_is_renamed_in_place(self):
        data = {"users": [{"id": "u-1", "email": "owner@hartline.example", "name": "Alex Hartline", "company_id": "co-hartline"},
                          {"id": "u-2", "email": "pat@example.com", "name": "Pat", "company_id": "co-x"}]}
        self.assertTrue(migrations._demo_electrical_owner(data))
        self.assertEqual((data["users"][0]["id"], data["users"][0]["email"], data["users"][0]["name"]),
                         ("u-1", "owner@redbudelectric.example", "Dana Whitfield"))
        self.assertEqual(data["users"][1]["email"], "pat@example.com")
        self.assertFalse(migrations._demo_electrical_owner(data))

    def test_a_name_given_since_is_kept_and_no_second_account_is_made(self):
        data = {"users": [{"id": "u-1", "email": "owner@hartline.example", "name": "Sam", "company_id": "co-hartline"}]}
        migrations._demo_electrical_owner(data)
        self.assertEqual(data["users"][0]["name"], "Sam")
        both = {"users": [{"id": "u-1", "email": "owner@hartline.example", "name": "Alex Hartline"},
                          {"id": "u-2", "email": "owner@redbudelectric.example", "name": "Dana Whitfield"}]}
        self.assertFalse(migrations._demo_electrical_owner(both))


if __name__ == "__main__":
    unittest.main()
