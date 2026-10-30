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



def inbox_item(n, **over):
    item = {"id": f"inb-{n}", "source": "email", "status": "dismissed", "received_at": f"2026-10-{1 + n % 28:02d}T09:00:00+00:00",
            "sent_at": "", "from": "", "subject": f"Item {n}", "text": "", "file_ids": [], "kind": "note",
            "suggested_job_id": None, "match_reason": "No job name, code, address or client found.", "candidates": []}
    item.update(over)
    return item


class InboxPageTests(unittest.TestCase):  # C1
    def setUp(self):
        self.state = seed_state()
        self.oak = self.state["jobs"][0]
        self.state["files"].append({"id": "f-1", "name": "co2.pdf", "ext": "pdf"})
        self.state["inbox"] = [
            inbox_item(0, source="sms", **{"from": "+15555550123"}),
            inbox_item(1, status="filed", filed_job_id=self.oak["id"], filed_kind="waiver", suggested_job_id=self.oak["id"],
                       match_reason="mentions job code OAK", file_ids=["f-1", "f-gone"], **{"from": "billing@millwork.example"}),
            inbox_item(2, status="filed", auto_filed=True, filed_job_id=self.oak["id"], filed_kind="note", **{"from": "ap@park.example"}),
            inbox_item(3, status="new", suggested_job_id=self.oak["id"], match_reason="mentions job code OAK", source="upload"),
            inbox_item(4, status="new", source="paste", text="10/12 Framing done\nmore"),
        ]

    def test_newest_first_with_how_each_was_matched(self):
        page = domain.inbox_page(self.state, 0, 50)
        self.assertEqual([r["id"] for r in page["entries"]], ["inb-4", "inb-3", "inb-2", "inb-1", "inb-0"])
        rows = {r["id"]: r for r in page["entries"]}
        self.assertEqual(rows["inb-1"]["why"], "Suggested because it mentions job code OAK.")
        self.assertEqual((rows["inb-1"]["status"], rows["inb-1"]["job_name"], rows["inb-1"]["kind"]), ("filed", self.oak["name"], "waiver"))
        self.assertEqual(rows["inb-1"]["files"], [{"id": "f-1", "name": "co2.pdf"}])
        self.assertEqual(rows["inb-1"]["files_gone"], 1, "a file taken off since is counted, not linked")
        self.assertEqual(rows["inb-2"]["status"], "auto")
        self.assertIn("reply to a pay app", rows["inb-2"]["why"])
        self.assertEqual(rows["inb-0"]["why"], "No job name, code, address or client found.")
        self.assertEqual(rows["inb-4"]["title"], "Item 4")
        self.assertEqual(rows["inb-3"]["job_id"], self.oak["id"], "a waiting item belongs to its suggested job")

    def test_filters_sort_and_paging(self):
        ids = lambda **kw: [r["id"] for r in domain.inbox_page(self.state, 0, 50, **kw)["entries"]]
        self.assertEqual(ids(source="sms"), ["inb-0"])
        self.assertEqual(ids(status="filed"), ["inb-1"], "filed by a person only")
        self.assertEqual(ids(status="auto"), ["inb-2"])
        self.assertEqual(ids(status="new"), ["inb-4", "inb-3"])
        self.assertEqual(ids(job_id=self.oak["id"]), ["inb-3", "inb-2", "inb-1"])
        self.assertEqual(ids(sort="oldest"), ["inb-0", "inb-1", "inb-2", "inb-3", "inb-4"])
        self.assertEqual(ids(sort="from"), ["inb-0", "inb-2", "inb-1", "inb-4", "inb-3"], "A-Z by sender, none last")
        self.assertEqual(ids(sort="job")[:3], ["inb-3", "inb-2", "inb-1"], "with a job first")
        page = domain.inbox_page(self.state, 1, 2)
        self.assertEqual(([r["id"] for r in page["entries"]], page["total"]), (["inb-3", "inb-2"], 5))

    def test_the_page_load_carries_what_waits_and_the_last_twenty_done(self):
        self.state["inbox"] = [inbox_item(n) for n in range(30)] + [inbox_item(30, status="new")] + [inbox_item(n) for n in range(31, 40)]
        snap = domain.light_snapshot(self.state)
        self.assertEqual(snap["inbox_total"], 40)
        self.assertEqual(len(snap["inbox"]), 21)
        self.assertEqual([i["id"] for i in snap["inbox"]][:2], ["inb-19", "inb-20"], "the last twenty done, in the order they came")
        self.assertIn("inb-30", [i["id"] for i in snap["inbox"]])
        self.assertEqual(snap["inbox"][-1]["id"], "inb-39")
        self.state["inbox"] = [inbox_item(n, status="new") for n in range(60)]
        self.assertEqual(len(domain.light_snapshot(self.state)["inbox"]), 60, "every waiting item")


class SentPayAppsTests(unittest.TestCase):  # I1
    def test_every_sent_pay_app_newest_first_by_job(self):
        state = big_company(2)
        page = domain.sent_payapps_page(state, 0, 200)
        went = [(j["id"], i["id"]) for j in state["jobs"] for i in j["invoices"] if i["status"] in domain.WENT_OUT and i.get("sent_at")]
        self.assertEqual(page["total"], len(went))
        self.assertTrue(any(r["closed"] for r in page["entries"]), "closed jobs' too")
        stamps = [r["sent_at"] for r in page["entries"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        one = page["entries"][0]["job_id"]
        self.assertTrue(all(r["job_id"] == one for r in domain.sent_payapps_page(state, 0, 50, one)["entries"]))
        drafts = {i["id"] for j in state["jobs"] for i in j["invoices"] if i["status"] in ("draft", "void")}
        self.assertFalse(drafts & {r["invoice_id"] for r in page["entries"]})


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


    def test_emails_sit_beside_their_actions_within_the_same_second(self):
        state = seed_state()
        job = next(x for x in state["jobs"] if x["id"] == "job-inman")
        with mock.patch.object(domain, "utcnow", return_value="2099-01-01T00:00:00+00:00"):
            for item in job.get("packet", []):
                if item.get("required") and not item.get("present"):
                    domain.resolve_packet_item(state, job["id"], item["id"], "t", reason="Emailed to the payer")
            domain.draft_invoice(state, job["id"], "t")
            domain.approve_and_send(state, job["id"], "t")
            inv = next(i for i in job["invoices"] if i["status"] == "sent")
            domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1", "t", invoice_id=inv["id"])
            domain.send_waiver(state, job["id"], "t")
            domain.close_job(state, job["id"], "t")
        order = [r.get("action") or r.get("email_kind") for r in domain.timeline_page(state, job["id"], 0, 7)["entries"]]
        self.assertEqual(order[0], "close_job", "the last thing done is first, though everything shares one second")
        self.assertEqual(order[:6], ["close_job", "send_waiver", "waiver", "match_payment", "approve_send", "pay_app"])


if __name__ == "__main__":
    unittest.main()
