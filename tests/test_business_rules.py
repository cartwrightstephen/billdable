"""The business plan's billing rules in code: the 45-day trial for draws, charges on the 5th,
the design-partner price, activation; the playbook's measured payer timing; and the server's
monthly jobs (restore drill, summary emails)."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import admin
import billing
import domain
import metrics
import monthly
import server
import storage
import store
import trial
from seed import seed_state

UTC = timezone.utc


def started(days_ago, style=None):
    state = {"company": {"trial": {"started_at": (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(timespec="seconds")}}, "jobs": []}
    if style:
        state["company"]["billing_style"] = style
    return state


class DrawTrialTests(unittest.TestCase):
    def test_45_days_for_draws_90_otherwise(self):
        t = trial.status(started(10, "draws"))
        self.assertEqual((t["state"], t["draws"]), ("active", True))
        self.assertEqual((datetime.fromisoformat(t["ends_by"]) - datetime.fromisoformat(t["started_at"])).days, 45)
        self.assertEqual(trial.status(started(50, "draws"))["state"], "ended")
        self.assertEqual(trial.status(started(50))["state"], "active", "pay apps: still inside 90 days")

    def test_billing_style_on_the_company(self):
        state = seed_state()
        domain.update_company(state, {"billing_style": "draws"}, "Alex")
        self.assertEqual(state["company"]["billing_style"], "draws")
        self.assertIn("billing style (draws)", state["audit"][-1]["detail"])
        with self.assertRaises(domain.GateError):
            domain.update_company(state, {"billing_style": "monthly"}, "Alex")
        rows = len(state["audit"])
        domain.update_company(seed_state(), {"billing_style": "pay_apps"}, "Alex")  # the default: no change
        self.assertEqual(len(state["audit"]), rows)


class PayerTimingTests(unittest.TestCase):
    def test_matches_results_and_reaches_the_playbook(self):
        state = seed_state()
        timing = domain.payer_timing(state)
        names = {p["id"]: p["name"] for p in state["payers"]}
        by_name = {r["payer"]: (r["average_days"], r["paid"]) for r in metrics.compute(state, since=None)["by_payer"]}
        self.assertEqual({names[k]: v for k, v in timing.items()}, by_name, "the same rule as the Results tab")
        snap = domain.light_snapshot(state)
        ellison = next(p for p in snap["payers"] if p["name"] == "Ellison Residential")
        self.assertEqual((ellison["paid_days_avg"], ellison["paid_count"]), by_name["Ellison Residential"])


class BillingDayTests(unittest.TestCase):
    def test_noon_on_the_next_billing_day(self):
        m = lambda *a: billing.billing_moment(datetime(*a, tzinfo=UTC))
        with mock.patch.dict("os.environ", {"BILLING_DAY": ""}):
            self.assertEqual(m(2026, 10, 13), datetime(2026, 11, 5, 12, tzinfo=UTC))
            self.assertEqual(m(2026, 10, 3), datetime(2026, 10, 5, 12, tzinfo=UTC))
            self.assertEqual(m(2026, 10, 5, 8), datetime(2026, 10, 5, 12, tzinfo=UTC))
            self.assertEqual(m(2026, 10, 5, 13), datetime(2026, 11, 5, 12, tzinfo=UTC))
            self.assertEqual(m(2026, 12, 20), datetime(2027, 1, 5, 12, tzinfo=UTC))
        with mock.patch.dict("os.environ", {"BILLING_DAY": "31"}):
            self.assertEqual(m(2026, 2, 1).day, 28, "capped at the 28th, which every month has")

    def test_checkout_during_and_after_the_trial(self):
        env = {"STRIPE_SECRET_KEY": "sk_test", "STRIPE_PRICE_ID": "price_reg", "STRIPE_PARTNER_PRICE_ID": "price_99", "BILLING_DAY": ""}
        sent = []
        with mock.patch.dict("os.environ", env), \
                mock.patch.object(billing, "_post", side_effect=lambda path, data: sent.append(data) or {"url": "https://checkout.stripe.com/x"}):
            billing.checkout_url(started(10), "co-1", "a@example.com", "https://b.example")
            ends = datetime.fromtimestamp(sent[-1]["subscription_data"]["trial_end"], UTC)
            self.assertEqual((ends.day, ends.hour), (5, 12))
            self.assertGreaterEqual(ends, datetime.now(UTC) + timedelta(days=79), "after the trial's last day")
            billing.checkout_url(started(200), "co-1", "a@example.com", "https://b.example")
            sub = sent[-1]["subscription_data"]
            self.assertNotIn("trial_end", sub)
            self.assertEqual(datetime.fromtimestamp(sub["billing_cycle_anchor"], UTC).day, 5)
            self.assertEqual(sub["proration_behavior"], "create_prorations")
            self.assertEqual(sent[-1]["line_items"][0]["price"], "price_reg")
            partner = started(10)
            partner["company"]["billing"] = {"partner": True}
            billing.checkout_url(partner, "co-1", "a@example.com", "https://b.example")
            self.assertEqual(sent[-1]["line_items"][0]["price"], "price_99")


class ActivationTests(unittest.TestCase):
    def test_days_from_signup_to_first_paid(self):
        self.assertIsNone(server.activation_days(started(5)))
        state = seed_state()
        state["company"]["trial"] = {"started_at": "2026-07-01T00:00:00+00:00"}
        t = trial.status(state)
        self.assertEqual(t["reason"], "paid")
        self.assertEqual(server.activation_days(state),
                         (datetime.fromisoformat(t["ended_at"]).date() - datetime(2026, 7, 1).date()).days)


class MonthlyJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [mock.patch.object(storage, "DATA_DIR", self.root), mock.patch.object(server, "COMPANIES", self.root / "companies")]
        for p in self.patches:
            p.start()
        (self.root / "auth.json").write_text(json.dumps({"users": [], "sessions": {}}))
        store.SqliteStore(self.root / "companies").save("co-1", seed_state())

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_the_drill_runs_once_a_month_and_alerts_on_failure(self):
        now = datetime(2026, 11, 2, 9, tzinfo=UTC)
        self.assertEqual(monthly.run_monthly_jobs(server.SITE, now), [], "no backup yet: nothing to check")
        with mock.patch.dict("os.environ", {"BACKUP_UPLOADS": "0"}):
            backup = server.backup.make_backup()
            did = monthly.run_monthly_jobs(server.SITE, now)
        self.assertTrue(did and "PASS" in did[0], did)
        self.assertEqual(monthly.run_monthly_jobs(server.SITE, now), [], "once a month")
        with mock.patch.object(server.backup, "drill", return_value=([], ["co-1: the audit chain is broken at aud-2."])), \
                mock.patch.object(server.alerts, "notify") as notify:
            did = monthly.run_monthly_jobs(server.SITE, datetime(2026, 12, 1, 9, tzinfo=UTC))
        self.assertIn("FAIL", did[0])
        notify.assert_called_once()
        self.assertTrue(backup.exists())

    def test_summaries_only_when_switched_on_and_early_in_the_month(self):
        with mock.patch.object(server.backup, "backups", return_value=[]), \
                mock.patch.object(server.mailer, "configured", return_value=True), \
                mock.patch.object(monthly, "send_monthly_summaries", return_value=["co-1: 1 of 1 sent"]) as send:
            self.assertEqual(monthly.run_monthly_jobs(server.SITE, datetime(2026, 11, 1, 9, tzinfo=UTC)), [], "off unless MONTHLY_SUMMARY_EMAILS=1")
            with mock.patch.dict("os.environ", {"MONTHLY_SUMMARY_EMAILS": "1"}):
                self.assertEqual(monthly.run_monthly_jobs(server.SITE, datetime(2026, 11, 9, tzinfo=UTC)), [], "only on the 1st to the 3rd")
                did = monthly.run_monthly_jobs(server.SITE, datetime(2026, 11, 2, tzinfo=UTC))
                self.assertEqual(monthly.run_monthly_jobs(server.SITE, datetime(2026, 11, 3, tzinfo=UTC)), [], "once")
        send.assert_called_once_with(server.SITE, 2026, 10)
        self.assertIn("2026-10", did[0])


class FailingCompanySite:
    """A site whose company b can't be read (a schema newer than the code, after a rollback)."""

    def __init__(self):
        self.sent, self.logged = [], []

    def company_ids(self):
        return ["a", "b", "c"]

    def read(self, company_id):
        if company_id == "b":
            raise ValueError("Company file is schema 99; this code only knows up to 4. Deploy the newer code.")
        return seed_state()

    def deleted(self):
        return set()

    def send(self, to, subject, body, company_id):
        self.sent.append(company_id)
        return "sent"

    def app_url(self):
        return "https://duracash.example"

    def log(self, level, **fields):
        self.logged.append((level, fields.get("msg", "")))


class OneFailingCompanyTests(unittest.TestCase):
    """One company that fails to load doesn't stop the others or make the hourly loop repeat emails."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(storage, "DATA_DIR", Path(self.tmp.name)),
                        mock.patch.object(monthly.mailer, "configured", return_value=True),
                        mock.patch.object(monthly.auth, "owner_emails", side_effect=lambda cid: [f"owner@{cid}.example"]),
                        mock.patch.object(monthly.domain, "daily_digest", return_value=("Due today", "body")),
                        mock.patch.object(monthly.metrics, "month_summary", return_value=("Summary", "body")),
                        mock.patch.object(monthly.backup, "backups", return_value=[]),
                        mock.patch.object(monthly.alerts, "notify")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_daily_email_once_per_company_and_the_rest_still_get_theirs(self):
        site = FailingCompanySite()
        for hour in (12, 13, 14):
            monthly.run_once(site, datetime(2026, 11, 4, hour, tzinfo=UTC))
        self.assertEqual(site.sent, ["a", "c"])
        self.assertTrue(any(level == "error" and "b" in msg for level, msg in site.logged), site.logged)

    def test_a_drill_that_raises_does_not_stop_the_daily_email_or_run_every_hour(self):
        site = FailingCompanySite()
        newest = Path(self.tmp.name) / "backup-bad.tar.gz"
        newest.write_bytes(b"not a gzip file")
        with mock.patch.object(monthly.backup, "backups", return_value=[newest]), \
                mock.patch.object(monthly.backup, "drill", side_effect=OSError("No space left on device")) as drill:
            for hour in (12, 13, 14):
                monthly.run_once(site, datetime(2026, 11, 4, hour, tzinfo=UTC))
        self.assertEqual(site.sent, ["a", "c"], "the daily email still goes")
        self.assertEqual(drill.call_count, 1, "a failed drill is not tried again every hour")
        self.assertTrue(any("restore drill" in msg and "FAIL" in msg for _, msg in site.logged), site.logged)
        self.assertTrue(any(c.args[0] == "drill" for c in monthly.alerts.notify.call_args_list))

    def test_daily_email_still_goes_when_the_monthly_jobs_fail(self):
        site = FailingCompanySite()
        with mock.patch.object(monthly, "run_monthly_jobs", side_effect=KeyError("name")):
            monthly.run_once(site, datetime(2026, 11, 4, 12, tzinfo=UTC))
        self.assertEqual(site.sent, ["a", "c"])
        self.assertTrue(any(level == "error" and "monthly jobs failed" in msg for level, msg in site.logged), site.logged)

    def test_monthly_summary_once_per_company(self):
        site = FailingCompanySite()
        with mock.patch.dict("os.environ", {"MONTHLY_SUMMARY_EMAILS": "1"}), mock.patch.object(monthly, "send_daily_digests", return_value=[]):
            for hour in (9, 10, 11):
                monthly.run_once(site, datetime(2026, 11, 2, hour, tzinfo=UTC))
        self.assertEqual(site.sent, ["a", "c"])


class PartnerCommandTests(unittest.TestCase):
    def test_on_and_off(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(server, "COMPANIES", Path(tmp) / "companies"):
            server.save_company("co-1", seed_state())
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(admin.main(["admin.py", "partner", "co-1", "on"]), 0)
            self.assertTrue(server.load_company("co-1")["company"]["billing"]["partner"])
            self.assertIn("Design partners now: 1", out.getvalue())
            with redirect_stdout(io.StringIO()):
                admin.main(["admin.py", "partner", "co-1", "off"])
            self.assertNotIn("partner", server.load_company("co-1")["company"]["billing"])
            self.assertEqual(server.load_company("co-1")["audit"][-1]["action"], "billing")


class FirstPayAppPaperworkTests(unittest.TestCase):
    """R1-08: the first pay app is not blocked by paperwork Duracash makes or already has."""

    SOV_LINES = [{"item": "1", "description": "Pavers", "quantity": "100", "unit": "sf", "rate": "100"}]

    def gc_job(self, state, packet=None):
        job = domain.create_job(state, "Mill Creek patio", "Tallow Creek Builders", 1_000_000, address="1 Mill Rd",
                                ap_email="ap@tallow.example", role="GC")
        if packet is not None:
            domain.update_payer(state, job["payer_id"], {"packet": packet})
        return job

    def test_gc_job_with_a_schedule_drafts_ready_to_send(self):
        state = {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = self.gc_job(state, "AIA-style pay app, schedule of values, continuation sheet, pay-app backup")
        domain.save_sov(state, job["id"], self.SOV_LINES, "0")
        line_id = job["sov"]["lines"][0]["id"]
        domain.record_sov_progress(state, job["id"], {line_id: "40"})
        draft = job["invoices"][-1]
        self.assertEqual(draft["status"], "awaiting_approval")
        self.assertEqual(domain.blocker_lines(job), [])
        self.assertEqual({p["present_reason"] for p in draft["packet"]}, {"Produced by Duracash"})

    def test_gc_default_list_leaves_out_the_pay_app_and_schedule(self):
        state = {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = self.gc_job(state)
        self.assertEqual(state["payers"][0]["packet"], "Lien waiver, insurance certificate, daily tickets.")
        domain.record_progress(state, job["id"], 400_000)
        labels = [p["label"] for p in domain.missing_packet(job)]
        self.assertEqual(labels, ["Lien waiver", "Insurance certificate", "Daily tickets"])
        self.assertFalse([b for b in domain.blocker_lines(job) if "pay app" in b.lower() or "schedule" in b.lower()])

    def test_owner_backup_is_needed_only_without_a_schedule(self):
        state = {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        plain = domain.create_job(state, "Porch", "Dana Homeowner", 1_000_000, address="2 Elm St")
        domain.record_progress(state, plain["id"], 400_000)
        self.assertIn("Pay-app backup", [p["label"] for p in domain.missing_packet(plain)])
        scheduled = domain.create_job(state, "Deck", "Dana Homeowner", 1_000_000, address="3 Elm St")
        domain.save_sov(state, scheduled["id"], self.SOV_LINES, "0")
        domain.record_sov_progress(state, scheduled["id"], {scheduled["sov"]["lines"][0]["id"]: "40"})
        self.assertNotIn("Pay-app backup", [p["label"] for p in domain.missing_packet(scheduled)])

    def test_new_record_kinds_fill_their_paperwork(self):
        # An insurance certificate, a supplier invoice (for a ticket item) and a stored-materials
        # document on the job record fill the items that name them (J5, M6).
        state = {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = domain.create_job(state, "Porch", "Dana Homeowner", 1_000_000, address="2 Elm St")
        domain.update_payer(state, job["payer_id"], {"packet": "Insurance certificate, delivery tickets, stored materials invoice"})
        domain.add_job_record(state, job["id"], "ACME COI 2026", "insurance")
        domain.add_job_record(state, job["id"], "ABC Supply invoice 4471", "supplier_invoice")
        domain.record_progress(state, job["id"], 400_000)
        packet = {p["label"]: p for p in job["invoices"][-1]["packet"]}
        self.assertEqual(packet["Insurance certificate"]["attachments"][0]["name"], "ACME COI 2026")
        self.assertEqual(packet["Delivery tickets"]["attachments"][0]["name"], "ABC Supply invoice 4471")

    def test_records_on_the_job_fill_the_paperwork(self):
        state = {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = domain.create_job(state, "Porch", "Dana Homeowner", 1_000_000, address="2 Elm St", contract_number="C-7")
        domain.add_job_record(state, job["id"], "Signed contract PDF", "contract")
        domain.record_progress(state, job["id"], 400_000)
        contract = next(p for p in job["invoices"][-1]["packet"] if p["label"] == "Signed contract")
        self.assertTrue(contract["present"])
        self.assertEqual([(a["name"], a["auto"]) for a in contract["attachments"]], [("Signed contract PDF", True)])
        self.assertTrue(any("attached automatically from the job record" in a["detail"] for a in state["audit"]))
        # A record added after drafting fills its item too.
        domain.add_job_record(state, job["id"], "Kitchen photos", "photo")
        photos = next(p for p in job["invoices"][-1]["packet"] if p["label"] == "Completion photos")
        self.assertTrue(photos["present"])
        # Detach takes it off, and the same record is not attached again.
        domain.detach_packet_item(state, job["id"], photos["id"])
        self.assertFalse(photos["present"])
        domain.add_job_record(state, job["id"], "A note", "note")
        self.assertFalse(photos["present"])

    def test_contract_number_alone_is_not_the_signed_contract(self):
        state = {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = domain.create_job(state, "Porch", "Dana Homeowner", 1_000_000, address="2 Elm St", contract_number="C-7")
        domain.record_progress(state, job["id"], 400_000)
        self.assertIn("Signed contract", [p["label"] for p in domain.missing_packet(job)])

    def test_this_periods_waiver_is_not_reused(self):
        state = {"company": {"name": "T"}, "payers": [], "jobs": [], "audit": [], "email_log": []}
        job = domain.create_job(state, "Porch", "Dana Homeowner", 1_000_000, address="2 Elm St", ap_email="d@example.com")
        domain.add_job_record(state, job["id"], "Signed contract", "contract")
        domain.add_job_record(state, job["id"], "Waiver for PA-01", "waiver")
        domain.record_progress(state, job["id"], 400_000)
        for item in domain.missing_packet(job):
            domain.resolve_packet_item(state, job["id"], item["id"], reason="test")
        domain.approve_and_send(state, job["id"], "Owner")
        domain.record_progress(state, job["id"], 600_000)
        packet = {p["label"]: p for p in job["invoices"][-1]["packet"]}
        self.assertTrue(packet["Signed contract"]["present"], "the contract carries over")
        self.assertFalse(packet["Conditional waiver"]["present"], "last period's waiver does not")


if __name__ == "__main__":
    unittest.main()


class FollowUpEscalationTests(unittest.TestCase):
    """R4-06: one follow-up names every unpaid pay app, counts per pay app, gets firmer, copies
    the escalation email from the third; "Spoke to them" holds the next one until its date."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_money import new_state, new_job, send_open
        self.state = new_state()
        self.job = new_job(self.state, contract=1_000_000)
        domain.record_progress(self.state, self.job["id"], 300_000)
        self.first = send_open(self.state, self.job)
        domain.record_progress(self.state, self.job["id"], 500_000)
        self.second = send_open(self.state, self.job)
        long_ago = (datetime.now(UTC) - timedelta(days=40)).replace(microsecond=0).isoformat()
        self.first["sent_at"] = long_ago
        self.second["sent_at"] = (datetime.now(UTC) - timedelta(days=10)).replace(microsecond=0).isoformat()

    def follow_up(self):
        for inv in (self.first, self.second):
            if inv.get("last_follow_up_at"):
                inv["last_follow_up_at"] = (datetime.now(UTC) - timedelta(days=8)).replace(microsecond=0).isoformat()
        domain.record_follow_up(self.state, self.job["id"], "Owner")
        return self.state["email_log"][-1]

    def test_two_open_pay_apps_one_email_naming_both(self):
        mail = self.follow_up()
        self.assertEqual(sum(1 for e in self.state["email_log"] if e["kind"] == "follow_up"), 1)
        self.assertIn(f"{self.first['number']}, {self.second['number']}", mail["subject"])
        self.assertTrue(mail["subject"].startswith("Following up:"))
        self.assertIn(f"{self.first['number']} $3,000.00 sent", mail["body"])
        self.assertIn("total $5,000.00", mail["body"])
        self.assertIn("past the 14 days you usually take to pay", mail["body"], "the oldest is 40 days out")
        self.assertEqual((self.first["follow_up_count"], self.second["follow_up_count"]), (1, 1))
        self.assertEqual(self.first["last_follow_up_at"], self.second["last_follow_up_at"])

    def test_counts_and_escalates_from_the_third(self):
        domain.update_payer(self.state, self.job["payer_id"], {"escalation_email": "owner@jones.example"})
        with self.assertRaises(domain.GateError):
            domain.update_payer(self.state, self.job["payer_id"], {"escalation_email": "not an email"})
        self.assertEqual(self.follow_up()["cc"], [])
        second = self.follow_up()
        self.assertTrue(second["subject"].startswith("Second follow-up:"))
        self.assertEqual(second["cc"], [])
        third = self.follow_up()
        self.assertTrue(third["subject"].startswith("Third follow-up:"))
        self.assertEqual(third["cc"], ["owner@jones.example"])

    def test_note_contact_holds_the_follow_up_until_the_date(self):
        payer = domain._payer(self.state, self.job["payer_id"])
        self.assertLess(domain.follow_up_due(self.job, payer), datetime.now(UTC), "due before the call")
        with self.assertRaises(domain.GateError) as raised:
            domain.note_contact(self.state, self.job["id"], "", "")
        self.assertIn("text", raised.exception.fields)
        next_on = (datetime.now(UTC) + timedelta(days=3)).date()
        domain.note_contact(self.state, self.job["id"], "Check goes out Friday", next_on.isoformat(), "Owner")
        self.assertEqual(domain.follow_up_due(self.job, payer).date(), next_on)
        self.assertEqual(self.job["expected_payment_on"], next_on.isoformat())
        self.assertEqual(self.job["records"][-1]["label"], "Spoke to Sam Jones")
        self.assertIn("Spoke to Sam Jones: Check goes out Friday", self.state["audit"][-1]["detail"])
        with self.assertRaises(domain.GateError) as raised:
            domain.record_follow_up(self.state, self.job["id"], "Owner")
        self.assertIn(f"not due until {next_on.isoformat()}", str(raised.exception))
        self.assertIn("you spoke to them", str(raised.exception))   # the call holds it, not the days between contacts
        # A follow-up sent anyway (Follow up now) goes back to the usual days.
        domain.record_follow_up(self.state, self.job["id"], "Owner", early=True)
        self.assertGreater(domain.follow_up_due(self.job, payer), datetime.now(UTC) + timedelta(days=6))
