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
                                     approver="Dana", ap_email="dana@example.com", phone="(404) 555-0100", role="Owner")
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
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("new_job", 5, "Not billed yet", "copper", "Progress pay app"))
        self.assertEqual(r["text"], "Porch rebuild has no pay app yet. Bill the work done so far with Progress pay app, or use Final pay app.")
        self.assertEqual((r["job_id"], r["job"], r["payer"], r["path"]), (self.job["id"], "Porch rebuild", "Dana Lee", f"/jobs/{self.job['id']}"))

    def test_blocked_by_paperwork(self):
        inv = self.draft()
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"]), ("blocked", 3, "Needs supporting documents", "amber"))
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
        self.assertEqual(r["text"], f"{inv['number']} for $4,000.00 to Dana is ready. Review & send.")
        self.assertEqual(r["invoice_id"], inv["id"])

    def test_waiver_due(self):
        self.job["waiver_due"] = True
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("waiver", 2, "Waiver due", "amber", "Choose waiver and send"))
        self.assertEqual(r["text"], "Paid in full: the client is waiting for the lien waiver. Choose waiver and send.")

    def test_finished_work_not_billed(self):
        self.draft()["status"] = "void"
        self.job["work_status"] = "complete"
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["button"]), ("not_billed", 5, "Not billed", "Draft pay app"))
        self.assertEqual(r["text"], "$4,000.00 of finished work isn't billed. Draft pay app.")

    def test_cutoff_due_soon(self):
        self.payer["payapp_due_day"] = 31
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("due_soon", 1.5, "Due soon", "amber", "Progress pay app"))
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
        self.assertEqual((r["kind"], r["rank"], r["label"], r["button"], r["item_id"]), ("says_done", 0, "Job's done?", "Final pay app", "in-1"))
        self.assertEqual(r["text"], "Text message from +15555550123: Porch is done")

    def test_inbox_item_for_a_job(self):
        self.sent(days_ago=1)
        self.state["inbox"] = [{"id": "in-2", "status": "new", "source": "email", "subject": "Signed CO 3", "suggested_job_id": self.job["id"],
                                "match_reason": "names the job"},
                               {"id": "in-3", "status": "filed", "subject": "Old", "suggested_job_id": self.job["id"]}]
        r = self.only()
        self.assertEqual((r["kind"], r["rank"], r["label"], r["tone"], r["button"]), ("inbox", 6, "Inbox", "", "Add to This Job"))
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
        other = domain.create_job(self.state, "Bath", "Dana Lee", 500_000, address="2 Elm St", approver="Bath", ap_email="ap@client.example", phone="(404) 555-0100", role="Owner")
        domain.record_progress(self.state, other["id"], 100_000)
        self.clear_packet(other["invoices"][-1])
        self.assertEqual([r["kind"] for r in self.rows()], ["ready", "new_job"])

    def test_snapshot_sends_the_rows_the_email_lists(self):
        inv = self.sent()
        other = domain.create_job(self.state, "Bath", "Dana Lee", 500_000, address="2 Elm St", approver="Bath", ap_email="ap@client.example", phone="(404) 555-0100", role="Owner")
        domain.record_progress(self.state, other["id"], 100_000)
        self.state["inbox"] = [{"id": "in-2", "status": "new", "source": "email", "subject": "Signed CO 3", "suggested_job_id": self.job["id"]}]
        rows = self.rows()
        self.assertEqual(domain.light_snapshot(self.state, NOW)["today_rows"], rows)
        self.assertEqual([r["kind"] for r in rows], ["blocked", "follow_up", "inbox"])
        subject, body = domain.daily_digest(self.state, "https://app.example", NOW)
        self.assertEqual(subject, "Due today in Duracash: 1 pay app missing supporting documents, 1 follow-up, 1 Inbox item")
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

    # ---------- what comes after billing: the job page's words, in the daily email too (Nov 3) ----------

    def finished_and_paid(self, retainage=0):
        inv = self.sent()
        inv.update(status="paid", amount_cents=1_000_000 - retainage, gross_cents=1_000_000, retainage_cents=retainage)
        self.job["work_status"] = "complete"
        self.job["completed_value_cents"] = 1_000_000
        self.job.setdefault("payments", []).append({"id": "p1", "invoice_id": inv["id"], "amount_cents": 1_000_000 - retainage, "reference": "x", "matched_at": iso(NOW)})
        self.job["waiver_due"] = False
        self.job["closeout_packet"] = []
        return inv

    def test_a_draft_with_the_client_is_waiting_on_them_not_ready_to_send(self):
        inv = self.draft()
        self.clear_packet(inv)
        inv.update(pencil_status="sent")
        r = self.only()
        self.assertEqual((r["kind"], r["button"]), ("pencil_out", ""))
        self.assertIn("When they answer, tap Client agreed to the draft or Client wants changes.", r["text"])
        inv.update(pencil_status="agreed")
        r = self.only()
        self.assertEqual((r["kind"], r["button"]), ("ready", "Review & send"))
        self.assertIn("The client agreed to it. Review & send.", r["text"])

    def test_closeout_missing(self):
        self.finished_and_paid()
        self.job["closeout_packet"] = [{"id": "c1", "kind": "punch", "label": "Punch list sign-off", "required": True, "present": False, "blocker": "Punch list sign-off is missing."}]
        r = self.only()
        self.assertEqual((r["kind"], r["button"]), ("closeout", "Open closeout documents"))
        self.assertEqual(r["text"], "Closeout: missing Punch list sign-off. Open closeout documents.")
        subject, _ = domain.daily_digest(self.state, "", NOW)
        self.assertIn("1 job missing closeout documents", subject)

    def test_retainage_to_ask_for_and_the_wait_after_asking(self):
        self.finished_and_paid(retainage=100_000)
        self.job["closeout_packet"] = []
        held = domain.job_money(self.job)["retainage_open_cents"]
        self.assertGreater(held, 0)
        r = self.only()
        self.assertEqual((r["kind"], r["button"]), ("retainage", "Ask for retainage"))
        self.assertEqual(r["text"], f"{domain.money(held)} held back. Closeout documents are in. Ask for retainage.")
        self.assertIn("1 retainage to ask for", domain.daily_digest(self.state, "", NOW)[0])
        self.job["last_retainage_chase_at"] = iso(NOW - timedelta(days=1))
        r = self.only()
        self.assertEqual((r["kind"], r["button"], r["ball_in_court"], r["due"]), ("retainage_wait", "Record retainage paid", "client", "2026-11-03"))
        self.assertIn("Ask again from Nov 3, 2026, or Record retainage paid.", r["text"])
        self.assertIsNone(domain.daily_digest(self.state, "", NOW), "waiting on the client: not in the email")
        self.job["last_retainage_chase_at"] = iso(NOW - timedelta(days=30))
        self.assertEqual(self.only()["kind"], "retainage")

    def test_ready_to_finish(self):
        self.finished_and_paid()
        r = self.only()
        self.assertEqual((r["kind"], r["button"], r["text"]), ("finish", "Finish job", "Ready to finish the job. Finish job."))
        self.job["completed_at"] = iso(NOW - timedelta(days=7))
        self.assertIn("1 job ready to finish", domain.daily_digest(self.state, "", NOW)[0])

    def test_a_job_to_finish_is_in_the_email_once_a_week(self):
        self.finished_and_paid()
        self.job["completed_at"] = iso(NOW)
        days = [d for d in range(21) if domain.daily_digest(self.state, "", NOW + timedelta(days=d))]
        self.assertEqual(days, [0, 7, 14], "the row stays on the Dashboard each day")
        self.assertEqual(self.only(NOW + timedelta(days=1))["kind"], "finish")
        del self.job["completed_at"]
        monday = NOW + timedelta(days=(7 - NOW.weekday()) % 7)
        self.assertIsNotNone(domain.daily_digest(self.state, "", monday), "no date on an older job: Mondays")
        self.assertIsNone(domain.daily_digest(self.state, "", monday + timedelta(days=1)))

    def test_a_finished_job_held_up_by_an_unsigned_change_order_has_a_row(self):
        self.finished_and_paid()
        self.job["change_orders"].append({"id": "co-3", "number": 3, "status": "pending", "included_in_billing": False, "amount_cents": 50_000, "description": "Rail"})
        r = self.only()
        self.assertEqual((r["kind"], r["label"], r["tone"], r["button"]), ("finish_held", "Before you finish", "amber", "Open job"))
        self.assertEqual(r["text"], "Before you finish: CO 3 is not signed yet.")
        self.job["completed_at"] = iso(NOW)
        self.assertIn("1 job held up before finishing", domain.daily_digest(self.state, "", NOW)[0])
        self.assertIsNone(domain.daily_digest(self.state, "", NOW + timedelta(days=1)))

    def test_a_draft_to_a_client_with_no_email_names_the_email(self):
        inv = self.draft()
        self.clear_packet(inv)
        self.payer["ap_email"] = ""
        self.assertEqual(self.only()["text"], f"{inv['number']} for {domain.money(inv['amount_cents'])} to Dana is ready. Add the pay app email for Dana Lee, then Review & send.")

    def test_a_ticket_filed_from_the_inbox_says_which(self):  # criterion 83
        import importer
        for picked, text, expect in (("ticket:delivery", "Tile came", "delivery"), ("ticket:time_sheet", "Crew hours", "time_sheet"),
                                     ("ticket", "Time sheet week 3", "time_sheet"), ("ticket", "Tile came", "delivery")):
            item = {"id": f"in-{picked}-{text}", "status": "new", "kind": "note", "subject": "", "text": text, "file_ids": [], "source": "paste", "from": ""}
            self.state.setdefault("inbox", []).append(item)
            importer.file_item(self.state, item["id"], self.job["id"], picked, "owner")
            rec = self.job["records"][-1]
            self.assertEqual((rec["kind"], rec["ticket_type"]), ("ticket", expect))

    def test_the_pencil_preview_is_what_goes(self):  # criterion 78
        inv = self.draft()
        shown = next(i for i in domain.job_view(self.state, self.job)["invoices"] if i["id"] == inv["id"])["pencil_preview"]
        domain.send_pencil(self.state, self.job["id"], domain.PREVIEW_SENDER)
        sent = self.state["email_log"][-1]
        self.assertEqual((sent["subject"], sent["body"]), (shown["subject"], shown["body"]))

    def test_a_missing_document_row_carries_the_item_its_button_lands_on(self):
        inv = self.draft()
        r = self.only()
        self.assertEqual(r["packet_item_id"], inv["packet"][0]["id"])
        self.assertEqual(r["key"], f"blocked:{self.job['id']}:{inv['id']}", "the assignee stays with the row")

    def test_a_row_held_up_only_by_a_change_order_goes_to_it(self):
        inv = self.draft()
        self.clear_packet(inv)
        self.job["change_orders"].append({"id": "co-2", "number": 2, "status": "pending", "included_in_billing": True, "amount_cents": 50_000, "description": "Rail"})
        r = self.only()
        self.assertEqual((r["button"], r["co_id"]), ("Get CO 2 signed", "co-2"))
        self.assertEqual(r["path"], f"/money/change-order/{self.job['id']}/co-2")
        inv["packet"][0]["present"] = False
        r = self.only()
        self.assertEqual((r["co_id"], r["path"]), ("", f"/money/packet/{self.job['id']}/{inv['id']}"), "with documents missing too, the documents first")

    def test_a_file_waiting_for_a_look_changes_the_button(self):
        inv = self.draft()
        self.clear_packet(inv)
        self.job["records"].append({"id": "r1", "label": "scan.pdf", "file_ids": ["f1"]})
        inv["packet"][0].update(present=False, attachments=[{"record_id": "r1", "name": "scan.pdf", "unconfirmed": True}])
        r = self.only()
        self.assertEqual(r["button"], "Check 1 document")
        self.assertIn("look at scan.pdf and confirm it", r["text"])
        inv["packet"][1].update(present=False)
        self.assertEqual(self.only()["button"], "Add or check 2 documents")

    def test_draws_read_draw_on_today_and_in_the_email(self):
        self.state["company"]["billing_style"] = "draws"
        self.payer["payapp_due_day"] = 31
        r = self.only()
        self.assertEqual(r["text"], "Record this month's progress and draft the draw. Draw due to Dana Lee by Oct 31, 2026.")
        self.draft()["status"] = "void"
        self.job["work_status"] = "complete"
        self.payer["payapp_due_day"] = None
        r = self.only()
        self.assertEqual((r["button"], r["text"]), ("Draft draw", "$4,000.00 of finished work isn't billed. Draft draw."))
        subject, _ = domain.daily_digest(self.state, "", NOW)
        self.assertEqual(subject, "Due today in Duracash: 1 job to bill")

    def test_draws_leave_names_as_typed(self):
        self.state["company"]["billing_style"] = "draws"
        self.job["name"] = "Pay app test house"
        self.assertEqual(self.only()["text"], "Pay app test house has no draw yet. Bill the work done so far with Progress draw, or use Final draw.")
        self.assertEqual(domain.term_text("Final pay app", "draws"), "Final draw")
        self.assertEqual(domain.term_text("Pay apps and a pay app", "draws"), "Draws and a draw")
        self.assertEqual(domain.term_text("Pay apps and a pay app", "pay_apps"), "Pay apps and a pay app")
        self.assertEqual(domain.term_text("Send pay app to Pay app Co", "draws", ("Pay app Co",)), "Send draw to Pay app Co")


if __name__ == "__main__":
    unittest.main()
