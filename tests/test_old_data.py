"""A company file written by the code before the Nov 3 overhaul (dev at cd935f5; made by running that
code): a dollar job with a sent pay app and an open draft, a name-only attachment, an in-hand item
with no reason, and a client with no email or phone. It loads and keeps working (acceptance 6, 18, 95, 96)."""

import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import migrations
import reminders

OLD = Path(__file__).parent / "fixtures" / "old_company_cd935f5.json"
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


class OldCompanyFile(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(OLD.read_text(encoding="utf-8"))
        self.state = copy.deepcopy(self.raw)
        migrations.migrate(self.state)
        migrations.migrate(self.state)
        self.job = self.state["jobs"][0]
        self.sent, self.draft = self.job["invoices"]

    def test_the_schema_is_current_and_every_step_is_counted(self):
        self.assertEqual(self.state["schema_version"], migrations.SCHEMA_VERSION)
        self.assertEqual(migrations.SCHEMA_VERSION, len(migrations.STEPS) + 1)
        self.assertFalse(migrations.migrate(self.state), "a second run has nothing to do")

    def test_no_job_gains_a_schedule_and_no_dollars_move(self):
        for job, was in zip(self.state["jobs"], self.raw["jobs"]):
            self.assertFalse(domain.sov.has_sov(job), job["name"])
            self.assertEqual(job.get("completed_value_cents"), was.get("completed_value_cents"))
        self.assertEqual(self.sent, self.raw["jobs"][0]["invoices"][0], "a sent pay app doesn't change")
        for key in ("amount_cents", "gross_cents", "retainage_cents", "status", "number"):
            self.assertEqual(self.draft[key], self.raw["jobs"][0]["invoices"][1][key], key)

    def test_it_draws_and_reminds_without_tripping(self):
        view = domain.job_view(self.state, self.job)
        self.assertFalse(view["sov_view"]["present"], "the dollar form")
        self.assertFalse(view["sov_view"].get("auto"))
        domain.snapshot(self.state)
        domain.light_snapshot(self.state, NOW)
        domain.today_rows(self.state, NOW)
        reminders.build(self.state, NOW)

    def test_the_dollar_form_still_bills_in_dollars(self):
        before = self.draft["gross_cents"]
        domain.record_progress(self.state, self.job["id"], 6_000_000)
        live = next(i for i in self.job["invoices"] if i["status"] in domain.DRAFT_STATUSES)
        self.assertEqual(live["gross_cents"], before + 1_000_000)
        self.assertFalse(domain.sov.has_sov(self.job))

    def test_an_old_name_only_attachment_and_an_in_hand_item_have_a_way_forward(self):
        view = domain.job_view(self.state, self.job)
        draft = next(i for i in view["invoices"] if i["id"] == self.draft["id"])
        by_label = {p["label"]: p for p in draft["packet"]}
        named = by_label["Conditional waiver"]
        self.assertEqual([a["name"] for a in named["attachments"]], ["Name only waiver"])
        self.assertFalse(named["attachments"][0].get("record_id"), "the page says no file was kept, with Remove")
        domain.detach_packet_item(self.state, self.job["id"], named["id"])
        self.assertIn("Conditional waiver", [p["label"] for p in domain.missing_packet(self.job)])
        in_hand = by_label["Insurance certificate"]
        self.assertTrue(in_hand["present"])
        for key in ("present_reason", "marked_by", "marked_at"):
            self.assertNotIn(str(in_hand.get(key)), ("undefined",), key)

    def test_a_client_with_nothing_on_file_keeps_its_running_job(self):  # Decision 3
        idle = self.state["jobs"][1]
        payer = next(p for p in self.state["payers"] if p["id"] == idle["payer_id"])
        self.assertFalse(payer.get("phone"))
        domain.record_progress(self.state, idle["id"], 100_000)
        self.assertEqual(idle["invoices"][-1]["status"] in domain.DRAFT_STATUSES, True)
        with self.assertRaises(domain.GateError):
            domain.create_job(self.state, "Next job", payer["name"], 100_000, address="3 Old St")


if __name__ == "__main__":
    unittest.main()
