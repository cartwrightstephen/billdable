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


class SendCheckTests(unittest.TestCase):
    def test_check_only_runs_the_checks_and_sends_nothing(self):
        from test_round2_approvals import ready_state, sign, OWNER
        state, job, inv = ready_state()
        with self.assertRaises(domain.GateError) as refused:
            domain.approve_and_send(state, job["id"], OWNER["name"], check_only=True)
        self.assertEqual(str(refused.exception), f"{inv['number']} needs a signature from Dana Whitfield before it goes.")
        self.assertEqual(refused.exception.blockers, [str(refused.exception)], "one sentence names who, no line per name under it")
        sign(state, job)
        domain.approve_and_send(state, job["id"], OWNER["name"], check_only=True)
        self.assertIn(inv["status"], domain.DRAFT_STATUSES, "a check sends nothing")
        self.assertFalse(inv.get("sent_at"))
        domain.approve_and_send(state, job["id"], OWNER["name"])
        self.assertEqual(inv["status"], "sent")


class EmailPeriodTests(unittest.TestCase):
    def test_the_email_body_gives_period_to_in_words_and_the_subject_keeps_the_date(self):
        state, job, labor, materials = schedule_job()
        domain.record_sov_progress(state, job["id"], {labor: "0.5"})
        inv = draft(job)
        inv["period_to"] = "2026-12-20"
        self.assertIn("Period to December 20, 2026", domain.pay_app_text(state, job, inv))
        self.assertNotIn("2026-12-20", domain.pay_app_text(state, job, inv).split("Schedule of values")[0])
        self.assertTrue(domain.pay_app_subject(state, job, inv).endswith("period to 2026-12-20"))


class ReleaseChecksTests(unittest.TestCase):
    def test_a_retainage_release_asks_no_work_checks_and_keeps_its_hard_stops(self):
        from test_payapp_core import send
        import approve_help
        state, job, labor, materials = schedule_job(retainage=10, packet="Invoice, Daily tickets, Time sheets, Progress photos")
        domain.record_sov_progress(state, job["id"], {labor: "1", materials: "50"})
        work = draft(job)
        self.assertTrue(domain.soft_checks_waiting(job, work), "a work pay app asks them")
        for item in work["packet"]:
            if domain.item_gate(job, item) == "soft":
                domain.soft_check_packet_item(state, job["id"], item["id"], "checked")
        send(state, job)
        domain.match_payment(state, job["id"], work["amount_cents"], "CHK-1")
        domain.draft_retainage_release(state, job["id"], 50_000, "substantial")
        rel = draft(job)
        self.assertEqual(domain.soft_checks_waiting(job, rel), [])
        self.assertFalse([i for i in rel["packet"] if domain.item_gate(job, i) == "soft" and i.get("required")])
        self.assertTrue(any(str(i.get("waiver_kind", "")).startswith("conditional") for i in rel["packet"]), "the conditional waiver stays")


class TemplateTests(unittest.TestCase):
    def test_the_project_manager_template_matches_the_project_manager_profile(self):
        from domain import profiles
        import case_company
        pm = next(t for t in profiles.TEMPLATES if t["id"] == "pm")
        case_pm = next(p for p in case_company.PROFILES if p["name"] == "Project manager")
        self.assertEqual(set(pm["perms"]), set(case_pm["perms"]))


if __name__ == "__main__":
    unittest.main()
