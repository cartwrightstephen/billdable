"""Per-view endpoints (P2-6): /api/state is bounded by open work, history is paged."""

import json
import sys
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
from seed import seed_state


def big_company(closed_jobs=400):
    state = seed_state()
    template = next(j for j in state["jobs"] if j.get("closed"))
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    for n in range(closed_jobs):
        job = json.loads(json.dumps(template))
        job["id"], job["code"], job["name"], job["closed_at"] = f"job-old{n}", f"O{n}", f"Old job {n}", old
        job["daily_records"] = [f"rec-{n}-{d}" for d in range(30)]
        state["jobs"].insert(0, job)
    for n in range(500):
        state["email_log"].append({"id": f"eml-{n}", "job_id": "job-old1", "subject": f"Follow-up {n}", "body": "x" * 800, "at": old})
    return state


class LightSnapshotTests(unittest.TestCase):
    def test_page_load_does_not_grow_with_history(self):
        state = big_company()
        full, light = json.dumps(domain.snapshot(state)), json.dumps(domain.light_snapshot(state))
        small = json.dumps(domain.light_snapshot(seed_state()))
        self.assertGreater(len(full), 10 * len(light))
        self.assertLess(len(light), 3 * len(small), "the page load stays near a small company's")
        snap = domain.light_snapshot(state)
        self.assertEqual(snap["closed_jobs_total"], 403)
        self.assertEqual(snap["email_log_total"], len(state["email_log"]))
        self.assertEqual(len(snap["email_log"]), domain.RECENT_EMAILS)
        self.assertNotIn("audit", snap)
        self.assertFalse(any(j["id"].startswith("job-old") for j in snap["jobs"]), "old closed jobs are paged, not loaded")

    def test_open_jobs_match_the_full_view_without_the_payer_copy(self):
        state = seed_state()
        full = {j["id"]: j for j in domain.snapshot(state)["jobs"]}
        light = domain.light_snapshot(state)
        for job in light["jobs"]:
            if job.get("summary"):
                self.assertEqual(job["money"], full[job["id"]]["money"])
                self.assertNotIn("lines", json.dumps(job["invoices"]))
                continue
            expected = dict(full[job["id"]])
            expected.pop("payer")
            self.assertEqual(job, expected)
        self.assertEqual(light["totals"]["unbilled_cents"], domain.snapshot(state)["totals"]["unbilled_cents"])
        self.assertTrue(all("sent_before" in p for p in light["payers"]))

    def test_pages_are_newest_first_and_filter_by_job(self):
        state = big_company(5)
        page = domain.page(state["email_log"], 0, 50, "job-old1")
        self.assertEqual(page["total"], 500)
        self.assertEqual(page["entries"][0]["id"], "eml-499")
        self.assertEqual(domain.page(state["email_log"], 490, 50, "job-old1")["entries"][-1]["id"], "eml-0")
        jobs = domain.jobs_page(state, "closed", 0, 2)
        self.assertEqual(jobs["total"], 8)
        self.assertTrue(all(j["summary"] for j in jobs["jobs"]))
        self.assertEqual(domain.jobs_page(state, "closed", 0, 50, q="old job 3")["total"], 1)
        self.assertEqual(domain.jobs_page(state, "open", 0, 50)["total"], sum(1 for j in state["jobs"] if not j.get("closed")))



class TimelineTests(unittest.TestCase):
    def test_one_jobs_actions_and_emails_newest_first_without_bodies(self):
        state = seed_state()
        job = next(j for j in state["jobs"] if j["code"] == "TRN")
        domain.audit(state, job["id"], "note", "A later action.", "Pat")
        domain.audit(state, "job-other", "note", "Another job.", "Pat")
        page = domain.timeline_page(state, job["id"], 0, 50)
        kinds = [r["kind"] for r in page["entries"]]
        self.assertIn("email", kinds)
        self.assertIn("action", kinds)
        self.assertEqual([r["at"] for r in page["entries"]], sorted((r["at"] for r in page["entries"]), reverse=True))
        email = next(r for r in page["entries"] if r["kind"] == "email")
        self.assertEqual(email["email_kind"], "pay_app", "the email's own kind is kept under another name")
        self.assertNotIn("body", email)
        self.assertFalse(any("Another job" in r["text"] for r in page["entries"]))
        self.assertEqual(domain.timeline_page(state, job["id"], 1, 1)["entries"][0], page["entries"][1])
        with mock.patch.object(domain, "utcnow", return_value="2099-01-01T00:00:00+00:00"):
            domain.audit(state, job["id"], "note", "First in this second.", "Pat")
            domain.audit(state, job["id"], "note", "Second in this second.", "Pat")
        self.assertEqual(domain.timeline_page(state, job["id"], 0, 1)["entries"][0]["text"], "Second in this second.")


if __name__ == "__main__":
    unittest.main()
