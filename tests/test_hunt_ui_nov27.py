"""Nov 27 UI hunt: what the pay app and its email say about a line billed for materials stored."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import domain  # noqa: E402
import sov  # noqa: E402
from test_payapp_core import schedule_job  # noqa: E402
from test_packet import draft  # noqa: E402


class StoredLineLabelTests(unittest.TestCase):
    def test_a_line_billed_only_for_stored_materials_says_so(self):
        state, job, labor, materials = schedule_job()
        domain.record_sov_progress(state, job["id"], {labor: "0.5"}, stored={materials: "1500"})
        inv = draft(job)
        label = next(l["label"] for l in inv["lines"] if l.get("sov_line_id") == materials)
        self.assertIn("materials stored $1,500.00", label)
        self.assertIn("0 of 100", label)

    def test_work_and_stored_on_one_line_name_only_the_stored_part(self):
        state, job, labor, materials = schedule_job()
        domain.record_sov_progress(state, job["id"], {materials: "20"}, stored={materials: "500"})
        label = next(l["label"] for l in draft(job)["lines"] if l.get("sov_line_id") == materials)
        self.assertTrue(label.endswith(", materials stored $500.00"), label)

    def test_a_line_without_stored_materials_reads_as_before(self):
        state, job, labor, materials = schedule_job()
        domain.record_sov_progress(state, job["id"], {labor: "0.5"})
        label = next(l["label"] for l in draft(job)["lines"] if l.get("sov_line_id") == labor)
        self.assertEqual(label, "1. Labor (50% complete to date)")


class RevisedEmailTests(unittest.TestCase):
    def job(self, status="sent"):
        inv = {"id": "i1", "number": "PA-2", "status": status, "revised_at": "2026-11-27T12:17:00+00:00"}
        return {"invoices": [inv], "change_orders": []}

    def test_an_email_of_the_version_before_a_revision_is_not_sent_again(self):
        old = {"kind": "pay_app", "payapp_invoice_id": "i1", "at": "2026-11-27T12:16:00+00:00"}
        self.assertIn("was revised after this email", domain.email_moot(self.job(), old))

    def test_the_revised_versions_own_email_can_go_again(self):
        new = {"kind": "pay_app", "payapp_invoice_id": "i1", "at": "2026-11-27T12:19:00+00:00"}
        self.assertEqual(domain.email_moot(self.job(), new), "")

    def test_while_back_to_a_draft_it_says_so_first(self):
        old = {"kind": "pay_app", "payapp_invoice_id": "i1", "at": "2026-11-27T12:16:00+00:00"}
        self.assertIn("back to a draft", domain.email_moot(self.job("draft"), old))


if __name__ == "__main__":
    unittest.main()
