"""The job page's Next step (static/js/jobs.js nextStep, jobActions) and the Dashboard's row
(domain.today_rows) are written twice, in two languages. This runs the app's own code in Node on
job_view for the main job states and checks the two say the same thing: the same button, and the
same sentence where they share one, else the same words that matter."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import domain
import server

NOW = datetime(2026, 10, 28, 13, 0, tzinfo=timezone.utc)
DUMP = """
const { nextStep, jobActions } = await import(process.argv[1]);
const now = new Date(process.argv[2]);
let body = "";
for await (const chunk of process.stdin) body += chunk;
console.log(JSON.stringify(JSON.parse(body).map(j => {
  const { primary } = jobActions(j, { now });
  return { line: nextStep(j, now), primary: primary ? primary.label : "" };
})));
"""


def iso(when):
    return when.isoformat(timespec="seconds")


def clear(inv):
    for item in inv["packet"]:
        item["present"] = True


@unittest.skipUnless(shutil.which("node"), "needs Node")
class NextStepAgreesTests(unittest.TestCase):
    def build(self):
        state = server.empty_state("Pine Builders")
        job = domain.create_job(state, "Porch rebuild", "Dana Lee", 1_000_000, address="1 Elm St", approver="Dana",
                                ap_email="dana@example.com", phone="(404) 555-0100", role="Owner")
        return state, job

    def draft(self, state, job, packet=False):
        domain.record_progress(state, job["id"], 400_000)
        inv = job["invoices"][-1]
        if not packet:
            clear(inv)
        return inv

    def sent(self, state, job, days_ago=2):
        inv = self.draft(state, job)
        inv.update(status="sent", sent_at=iso(NOW - timedelta(days=days_ago)))
        return inv

    def finished(self, state, job, retainage=0):
        job["retainage_pct"] = 10 if retainage else 0
        domain.record_progress(state, job["id"], 1_000_000)
        inv = job["invoices"][-1]
        clear(inv)
        job["work_status"] = "complete"
        job["completed_at"] = iso(NOW - timedelta(days=3))
        inv.update(status="paid", sent_at=iso(NOW - timedelta(days=9)), remaining_cents=0, paid_cents=inv["amount_cents"])
        job["payments"].append({"id": "p1", "invoice_id": inv["id"], "amount_cents": inv["amount_cents"], "reference": "1", "matched_at": iso(NOW)})
        job["closeout_packet"] = []
        return inv

    def compare(self, state, job):
        """The row for the job (or None) and what the job page says."""
        rows = [r for r in domain.today_rows(state, NOW) if r["job_id"] == job["id"]]
        view = json.loads(json.dumps(domain.job_view(state, job)))
        jobs_js = (ROOT / "static" / "js" / "jobs.js").as_uri()
        run = subprocess.run(["node", "--input-type=module", "-e", DUMP, jobs_js, NOW.isoformat()], input=json.dumps([view]),
                             capture_output=True, text=True, env={**os.environ, "TZ": "UTC"})
        self.assertEqual(run.returncode, 0, run.stderr)
        page = json.loads(run.stdout)[0]
        return (rows[0] if rows else None), page

    def agree(self, state, job, kind, button, same=True, words=()):
        row, page = self.compare(state, job)
        self.assertIsNotNone(row, page)
        self.assertEqual(row["kind"], kind)
        self.assertEqual(row["button"].lower(), button.lower())
        self.assertEqual(page["primary"].lower(), button.lower(), page)
        if same:
            self.assertEqual(row["text"], page["line"])
        for word in words:
            self.assertIn(word, row["text"])
            self.assertIn(word, page["line"])

    def test_a_new_job(self):
        state, job = self.build()
        self.agree(state, job, "new_job", "Progress Pay App", same=False, words=("Progress Pay App", "Final Pay App"))

    def test_a_missing_document_is_named_on_both(self):
        state, job = self.build()
        inv = self.draft(state, job, packet=True)
        clear(inv)
        inv["packet"][0]["present"] = False
        self.agree(state, job, "blocked", "Add 1 missing document", same=False, words=(inv["packet"][0]["label"],))

    def test_a_file_waiting_for_a_look_says_to_look_and_confirm(self):
        state, job = self.build()
        inv = self.draft(state, job, packet=True)
        clear(inv)
        item = inv["packet"][0]
        item.update(present=False, attachments=[{"record_id": "r1", "name": "scan.pdf", "unconfirmed": True}])
        self.agree(state, job, "blocked", "Check 1 document", words=("look at scan.pdf and confirm it",))

    def test_an_unsigned_change_order_names_its_number(self):
        state, job = self.build()
        self.draft(state, job)
        job["change_orders"].append({"id": "co-2", "number": 2, "status": "pending", "included_in_billing": True, "amount_cents": 50_000, "description": "Rail"})
        self.agree(state, job, "blocked", "Get CO 2 signed", same=False, words=("Change Order 2",))

    def test_a_draft_ready_to_send(self):
        state, job = self.build()
        self.draft(state, job)
        self.agree(state, job, "ready", "Review & Send", words=("is ready",))

    def test_a_draft_to_a_client_with_no_email_names_the_email(self):
        state, job = self.build()
        self.draft(state, job)
        state["payers"][0]["ap_email"] = ""
        self.agree(state, job, "ready", "Review & Send", words=("Add the pay app email for Dana Lee, then Review & Send.",))

    def test_a_draft_the_client_answered(self):
        state, job = self.build()
        inv = self.draft(state, job)
        inv["pencil_status"] = "agreed"
        self.agree(state, job, "ready", "Review & Send", words=("The client agreed to it.",))
        inv.update(pencil_status="corrections", pencil_note="Fix the rail")
        self.agree(state, job, "ready", "Review & Send", words=("Fix the rail. Fix it, then Review & Send.",))

    def test_a_draft_with_the_client_for_review(self):
        state, job = self.build()
        inv = self.draft(state, job)
        inv.update(pencil_status="sent", pencil_sent_at=iso(NOW - timedelta(days=1)))
        row, page = self.compare(state, job)
        self.assertEqual(row["kind"], "pencil_out")
        self.assertEqual(row["text"], page["line"])

    def test_a_waiver_due(self):
        state, job = self.build()
        self.sent(state, job)["status"] = "paid"
        job.update(waiver_due=True, waiver_sent=False)
        self.agree(state, job, "waiver", "Choose Waiver and Send", same=False, words=("Choose Waiver and Send.",))

    def test_finished_work_not_billed(self):
        state, job = self.build()
        inv = self.sent(state, job)
        inv["status"] = "paid"
        job["payments"].append({"id": "p1", "invoice_id": inv["id"], "amount_cents": inv["amount_cents"], "reference": "1", "matched_at": iso(NOW)})
        job.update(work_status="complete", completed_at=iso(NOW), completed_value_cents=1_000_000)
        self.agree(state, job, "not_billed", "Draft Pay App")

    def test_a_follow_up_due(self):
        state, job = self.build()
        self.sent(state, job, days_ago=30)
        self.agree(state, job, "follow_up", "Send Follow-Up", same=False)

    def test_sent_and_not_due_is_quiet_on_the_dashboard_and_waiting_on_the_job(self):
        state, job = self.build()
        self.sent(state, job)
        row, page = self.compare(state, job)
        self.assertIsNone(row)
        self.assertIn("Record a Payment", page["line"])
        self.assertIn(job["id"], [j["id"] for j in state["jobs"] if any(i["status"] in domain.OPEN_FOR_PAYMENT for i in j["invoices"])],
                      "the Dashboard's Unpaid list is what finds it")

    def test_after_a_conversation_the_line_says_conversation(self):
        state, job = self.build()
        inv = self.sent(state, job)
        job.update(contact_noted_at=iso(NOW - timedelta(days=1)), expected_payment_on="2026-11-05")
        inv["last_follow_up_at"] = job["contact_noted_at"]
        row, page = self.compare(state, job)
        self.assertIsNone(row)
        self.assertIn("Conversation Oct 27, 2026; check again Nov 5, 2026.", page["line"])
        self.assertNotIn("Spoke", page["line"])

    def test_closeout_missing(self):
        state, job = self.build()
        self.finished(state, job)
        job["closeout_packet"] = [{"id": "c1", "kind": "punch", "label": "Punch list sign-off", "required": True, "present": False, "blocker": "Punch list sign-off is missing."}]
        self.agree(state, job, "closeout", "Open Closeout Documents", words=("Punch list sign-off",))

    def test_retainage_to_ask_for_and_waiting_after_asking(self):
        state, job = self.build()
        self.finished(state, job, retainage=1)
        self.agree(state, job, "retainage", "Ask for Retainage")
        job["last_retainage_chase_at"] = iso(NOW - timedelta(days=1))
        self.agree(state, job, "retainage_wait", "Record Retainage Paid", words=("Ask again from",))

    def test_finished_but_held_up_by_an_unsigned_change_order(self):
        state, job = self.build()
        self.finished(state, job)
        job["change_orders"].append({"id": "co-3", "number": 3, "status": "pending", "included_in_billing": False, "amount_cents": 50_000, "description": "Rail"})
        row, page = self.compare(state, job)
        self.assertEqual((row["kind"], row["button"]), ("finish_held", "Open Job"))
        self.assertEqual(row["text"], page["line"])
        self.assertIn("CO 3", row["text"])

    def test_ready_to_finish(self):
        state, job = self.build()
        self.finished(state, job)
        self.agree(state, job, "finish", "Finish Job")


if __name__ == "__main__":
    unittest.main()
