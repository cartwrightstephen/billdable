"""Today's rows (R4-10): domain.today_rows is the one rule set for the Today page (sent in
/api/state as today_rows; static/js/today.js only draws it) and the daily email. One test per
kind of row, with the ids its button needs, plus the job flags the job page reads."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import server

NOW = datetime(2026, 10, 28, 13, 0, tzinfo=timezone.utc)


def iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


class TodayRowsTests(unittest.TestCase):
    def setUp(self):
        self.state = server.empty_state("Pine Builders")
        self.job = domain.create_job(self.state, "Porch rebuild", "Dana Lee", 1_000_000, address="1 Elm St",
                                     approver="Dana", ap_email="dana@example.com")
        self.payer = self.state["payers"][0]

    def rows(self, now=NOW):
        return domain.today_rows(self.state, now)

    def only(self, now=NOW):
        rows = self.rows(now)
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def draft(self):
        domain.record_progress(self.state, self.job["id"], 400_000)
        return self.job["invoices"][-1]

    def clear_packet(self, inv):
        for item in inv["packet"]:
            item["present"] = True

    def sent(self, days_ago=10, **over):
        inv = self.draft()
        self.clear_packet(inv)
        inv.update({"status": "sent", "sent_at": iso(NOW - timedelta(days=days_ago)), **over})
        return inv

    # ---------- one row per kind ----------

    def test_new_job(self):
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("new_job", 5, "Not billed yet", "copper", "Bill progress"))
        self.assertEqual(r["text"], "Porch rebuild has no pay app yet. Bill the work done so far, or mark it done.")
        self.assertEqual((r["job_id"], r["job"], r["payer"], r["path"]), (self.job["id"], "Porch rebuild", "Dana Lee", f"/jobs/{self.job['id']}"))

    def test_blocked_by_paperwork(self):
        inv = self.draft()
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"]), ("blocked", 3, "Needs paperwork", "amber"))
        self.assertEqual(r["text"], "Signed contract is missing. And 3 more.")
        self.assertEqual(r["button"], "Add 4 missing documents")
        self.assertEqual((r["invoice_id"], r["co_id"]), (inv["id"], ""))
        self.assertEqual(r["path"], f"/money/packet/{self.job['id']}/{inv['id']}")

    def test_blocked_by_an_unsigned_change_order(self):
        inv = self.draft()
        self.clear_packet(inv)
        self.job["change_orders"].append({"id": "co-2", "number": 2, "status": "pending", "included_in_billing": True,
                                          "amount_cents": 50_000, "description": "Extra rail"})
        r = self.only()
        self.assertEqual((r["kind"], r["label"], r["tone"], r["button"], r["co_id"]), ("blocked", "Blocked", "red", "Get CO 2 signed", "co-2"))

    def test_ready_to_send(self):
        inv = self.draft()
        self.clear_packet(inv)
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("ready", 1, "Ready to send", "green", "Review & send"))
        self.assertEqual(r["text"], f"{inv['number']} for $4,000.00 to Dana. Check what goes out, then approve.")
        self.assertEqual(r["invoice_id"], inv["id"])

    def test_waiver_due(self):
        self.job["waiver_due"] = True
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("waiver", 2, "Waiver due", "amber", "Review waiver"))
        self.assertEqual(r["text"], "Paid in full: the client is waiting for the lien waiver.")

    def test_finished_work_not_billed(self):
        self.draft()["status"] = "void"
        self.job["work_status"] = "complete"
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["button"]), ("not_billed", 5, "Not billed", "Draft pay app"))
        self.assertEqual(r["text"], "$4,000.00 of finished work has no pay app yet. Drafting sends nothing.")

    def test_cutoff_due_soon(self):
        self.payer["payapp_due_day"] = 31
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("due_soon", 1.5, "Due soon", "amber", "Bill progress"))
        self.assertEqual(r["text"], "Record this month's progress and draft the pay app. Pay app due to Dana Lee by Oct 31, 2026.")
        # A cutoff also lifts a blocked job's row, and says so.
        self.draft()
        r = self.only()
        self.assertEqual((r["kind"], r["rank"]), ("blocked", 1.5))
        self.assertTrue(r["text"].endswith("Pay app due to Dana Lee by Oct 31, 2026."))

    def test_follow_up_names_every_unpaid_pay_app(self):
        inv = self.sent()
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("follow_up", 4, "Follow up", "copper", "Send follow-up"))
        self.assertEqual(r["text"], f"{inv['number']} was sent Oct 18, 2026. Emails dana@example.com.")
        self.assertEqual(r["invoice_id"], inv["id"])
        self.job["invoices"].append({**inv, "id": "inv-2", "number": "PA-PORC-02", "status": "partial", "sent_at": iso(NOW - timedelta(days=2))})
        self.assertTrue(self.only()["text"].startswith(f"{inv['number']} and PA-PORC-02 are unpaid; the oldest was sent Oct 18, 2026"))
        # Not before the customer's days between follow-ups.
        self.assertEqual(self.rows(NOW - timedelta(days=4)), [])

    def test_a_noted_call_waits_for_its_date(self):  # R4-06
        self.sent(last_follow_up_at=iso(NOW - timedelta(days=1)))
        self.job.update({"contact_noted_at": iso(NOW - timedelta(days=1)), "expected_payment_on": "2026-10-30"})
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.only(datetime(2026, 10, 30, 9, tzinfo=timezone.utc))["kind"], "follow_up")

    def test_approved_on_the_customer_page(self):  # R4-08
        inv = self.sent(status="approved", approved_by_payer="Kim Park", expected_payment_on="2026-11-05")
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("approved", 4.5, "Approved", "green", ""))
        self.assertEqual(r["text"], f"Approved by Kim Park, expect payment Nov 5, 2026 ({inv['number']}).")
        self.assertEqual(self.only(datetime(2026, 11, 6, 9, tzinfo=timezone.utc))["kind"], "follow_up")

    def test_email_that_did_not_go_out(self):
        inv = self.sent(days_ago=1)
        self.state["email_log"].append({"id": "eml-1", "job_id": self.job["id"], "kind": "pay_app", "payapp_invoice_id": inv["id"],
                                        "subject": f"Pay application {inv['number']}", "to": "dana@example.com",
                                        "status": "failed: recipient refused", "at": iso(NOW)})
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("email_failed", 0.5, "Didn't go out", "red", "Send again"))
        self.assertEqual(r["text"], f"Pay application {inv['number']} to dana@example.com: failed: recipient refused")
        self.assertEqual((r["email_id"], r["invoice_id"], r["path"]), ("eml-1", inv["id"], "/dashboard"))

    def test_inbox_says_the_job_is_done(self):
        self.sent(days_ago=1)
        self.state["inbox"] = [{"id": "in-1", "status": "new", "source": "sms", "from": "+15555550123",
                                "text": "Porch is done\nall cleaned up", "says_done_job_id": self.job["id"]}]
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["button"], r["item_id"]), ("says_done", 0, "Job's done?", "Mark done and draft", "in-1"))
        self.assertEqual(r["text"], "Text message from +15555550123: Porch is done")

    def test_inbox_item_for_a_job(self):
        self.sent(days_ago=1)
        self.state["inbox"] = [{"id": "in-2", "status": "new", "source": "email", "subject": "Signed CO 3", "suggested_job_id": self.job["id"],
                                "match_reason": "names the job"},
                               {"id": "in-3", "status": "filed", "subject": "Old", "suggested_job_id": self.job["id"]}]
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("inbox", 6, "Inbox", "", "Add to this job"))
        self.assertEqual((r["text"], r["note"], r["item_id"], r["path"]), ("Signed CO 3", "names the job", "in-2", "/communications"))
        # The kind it will be filed as goes with the row, to show and change there (Oct 23 note #46).
        self.assertEqual(r["item_kind"], "note")
        self.state["inbox"][0]["kind"] = "change_order"
        self.assertEqual(self.only()["item_kind"], "change_order")

    def test_closed_jobs_are_left_out(self):
        self.job["closed"] = True
        self.assertEqual(self.rows(), [])

    # ---------- one rule set: the page, the email and the job ----------

    def test_most_urgent_first(self):
        other = domain.create_job(self.state, "Bath", "Dana Lee", 500_000, address="2 Elm St")
        domain.record_progress(self.state, other["id"], 100_000)
        self.clear_packet(other["invoices"][-1])
        self.assertEqual([r["kind"] for r in self.rows()], ["ready", "new_job"])

    def test_snapshot_sends_the_rows_the_email_lists(self):
        inv = self.sent()
        other = domain.create_job(self.state, "Bath", "Dana Lee", 500_000, address="2 Elm St")
        domain.record_progress(self.state, other["id"], 100_000)
        self.state["inbox"] = [{"id": "in-2", "status": "new", "source": "email", "subject": "Signed CO 3", "suggested_job_id": self.job["id"]}]
        rows = self.rows()
        self.assertEqual(domain.light_snapshot(self.state, NOW)["today_rows"], rows)
        self.assertEqual([r["kind"] for r in rows], ["blocked", "follow_up", "inbox"])
        subject, body = domain.daily_digest(self.state, "https://app.example", NOW)
        self.assertEqual(subject, "Due today in Duracash: 1 pay app missing paperwork, 1 follow-up, 1 Inbox item")
        for r in rows:
            self.assertIn(f"- {r['job']}: {r['label']}. {r['text']}\n  https://app.example{r['path']}", body)
        self.assertIn(inv["number"], body)

    def test_the_job_carries_blocked_and_its_button(self):
        view = domain.job_view(self.state, self.job)
        self.assertEqual((view["blocked"], view["needs_paperwork"], view["blocker_button"]), (False, False, ""))
        inv = self.draft()
        view = domain.job_view(self.state, self.job)
        self.assertEqual((view["blocked"], view["needs_paperwork"], view["blocker_button"]), (True, True, "Add 4 missing documents"))
        self.clear_packet(inv)
        self.job["change_orders"].append({"id": "co-2", "number": 2, "status": "pending", "included_in_billing": True,
                                          "amount_cents": 50_000, "description": "Extra rail"})
        self.job["change_orders"].append({**self.job["change_orders"][0], "id": "co-3", "number": 3})
        view = domain.job_view(self.state, self.job)
        self.assertEqual((view["blocked"], view["needs_paperwork"], view["blocker_button"]), (True, False, "Get 2 change orders signed"))
        snap = domain.light_snapshot(self.state, NOW)
        self.assertEqual(snap["totals"]["blocked_jobs"], 1)
        self.job["invoices"][-1]["packet"][0]["present"] = False
        self.assertEqual(domain.blocker_button(self.job), "Fix 3 things before sending")
        # A closed job's summary is never blocked.
        self.job["closed"] = True
        summary = domain.job_summary(self.state, self.job)
        self.assertEqual((summary["blocked"], summary["needs_paperwork"], summary["blocker_button"]), (False, False, ""))


    # ---------- draws (R6-01) ----------

    def test_draws_read_draw_on_today_and_in_the_email(self):
        self.state["company"]["billing_style"] = "draws"
        self.payer["payapp_due_day"] = 31
        r = self.only()
        self.assertEqual(r["text"], "Record this month's progress and draft the draw. Draw due to Dana Lee by Oct 31, 2026.")
        self.draft()["status"] = "void"
        self.job["work_status"] = "complete"
        self.payer["payapp_due_day"] = None
        r = self.only()
        self.assertEqual((r["button"], r["text"]), ("Draft draw", "$4,000.00 of finished work has no draw yet. Drafting sends nothing."))
        subject, _ = domain.daily_digest(self.state, "", NOW)
        self.assertEqual(subject, "Due today in Duracash: 1 job to bill")

    def test_draws_leave_names_as_typed(self):
        self.state["company"]["billing_style"] = "draws"
        self.job["name"] = "Pay app test house"
        self.assertEqual(self.only()["text"], "Pay app test house has no draw yet. Bill the work done so far, or mark it done.")
        self.assertEqual(domain.term_text("Pay apps and a pay app", "draws"), "Draws and a draw")
        self.assertEqual(domain.term_text("Pay apps and a pay app", "pay_apps"), "Pay apps and a pay app")
        self.assertEqual(domain.term_text("Send pay app to Pay app Co", "draws", ("Pay app Co",)), "Send draw to Pay app Co")


if __name__ == "__main__":
    unittest.main()
