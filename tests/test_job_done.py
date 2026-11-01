"""'Job's done' by text or email (P3): one click marks the job done and drafts the pay app."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import importer
from seed import seed_state


class SaysDoneTests(unittest.TestCase):
    def test_reads_finished_work_but_not_futures_negations_or_phases(self):
        yes = ["Grant Park done", "Job's done on Oak St", "Grant Park addition finished today, 4 crew", "All done at 9 Elm", "Work complete"]
        no = ["not done yet", "almost finished", "done by Friday", "won't be done", "Framing done", "Drywall finished on Grant Park",
              "drywall delivered", "Will be finished once the inspector comes"]
        self.assertEqual([t for t in yes if not importer.says_done(t)], [])
        self.assertEqual([t for t in no if importer.says_done(t)], [])


class MarkDoneFromMessageTests(unittest.TestCase):
    def test_flag_then_one_click_marks_done_and_drafts(self):
        state = seed_state()
        grt = next(j for j in state["jobs"] if j["code"] == "GRT")
        item = importer.add_item(state, "sms", text="Grant Park addition done, punch walk Friday?", sender="+15555550123")
        self.assertIsNone(item.get("says_done_job_id"), "'punch' names a phase: not offered")
        item = importer.add_item(state, "sms", text="Grant Park addition finished today", sender="+15555550123")
        self.assertEqual(item["says_done_job_id"], grt["id"])
        importer.file_and_mark_done(state, item["id"], "owner")
        self.assertEqual(grt["work_status"], "complete")
        self.assertEqual(item["status"], "filed")
        self.assertTrue(any(i["status"] in domain.DRAFT_STATUSES for i in grt["invoices"]), "the final pay app is drafted, not sent")
        self.assertIn("+15555550123", grt["done_notes"])
        # The message is on the job record once, as filed; its first line isn't repeated as a note.
        self.assertEqual([r for r in grt["records"] if r.get("label") == "Notes when marked done"], [])

    def test_not_offered_for_finished_jobs_or_unmatched_messages(self):
        state = seed_state()
        self.assertIsNone(importer.add_item(state, "sms", text="Oak Street kitchen done").get("says_done_job_id"), "Oak is already done")
        self.assertIsNone(importer.add_item(state, "sms", text="All done for today").get("says_done_job_id"), "no job named")
        item = importer.add_item(state, "paste", text="Grant Park addition finished")
        self.assertIsNone(item.get("says_done_job_id"), "only texts and emails")
        with self.assertRaises(domain.GateError):
            importer.file_and_mark_done(state, item["id"], "owner")


if __name__ == "__main__":
    unittest.main()
