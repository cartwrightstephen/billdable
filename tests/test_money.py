"""Money rules fixed in Build 2. Each test names the audit finding it covers."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import server
from seed import seed_state


def new_state():
    return {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": [], "email_log": []}


def new_job(state, contract=10_000_000, retainage=0, customer="Jones Family", **kw):
    return domain.create_job(
        state, kw.pop("name", "Pine kitchen"), customer, contract,
        address="12 Pine St", approver=kw.pop("approver", "Sam Jones"),
        ap_email=kw.pop("ap_email", "ap@jones.example"), retainage_pct=retainage, **kw,
    )


def clear_packet(state, job):
    inv = [i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES][-1]
    for item in inv["packet"]:
        domain.resolve_packet_item(state, job["id"], item["id"], reason="test")
    return inv


def send_open(state, job):
    clear_packet(state, job)
    domain.approve_and_send(state, job["id"], "Owner")
    return job["invoices"][-1]


class RetainageTests(unittest.TestCase):
    def test_retainage_withheld_on_progress(self):  # M1
        state = new_state()
        job = new_job(state, contract=10_000_000, retainage=10)
        domain.record_progress(state, job["id"], 4_000_000)
        inv = job["invoices"][-1]
        self.assertEqual(inv["amount_cents"], 3_600_000)
        self.assertEqual(inv["retainage_cents"], 400_000)
        send_open(state, job)
        m = domain.job_money(job)
        self.assertEqual(m["retainage_held_cents"], 400_000)
        self.assertEqual(m["unbilled_completed_cents"], 0)

    def test_first_packet_has_no_closeout_item(self):  # M3
        state = new_state()
        job = new_job(state, retainage=10)
        domain.record_progress(state, job["id"], 4_000_000)
        labels = " ".join(i["label"].lower() for i in job["invoices"][-1]["packet"])
        self.assertNotIn("closeout", labels)

    def test_retainage_not_unbilled_on_seed(self):  # M2
        job = domain._job(seed_state(), "job-turner")
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)

    def test_closeout_gates_release_and_release_closes(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        domain.send_waiver(state, job["id"], "Owner")
        with self.assertRaises(domain.GateError):
            domain.chase_retainage(state, job["id"])
        item = job["closeout_packet"][0]
        domain.resolve_packet_item(state, job["id"], item["id"], reason="Punch list signed")
        domain.chase_retainage(state, job["id"])
        with self.assertRaises(domain.GateError):
            domain.close_job(state, job["id"])
        domain.record_retainage_payment(state, job["id"], 100_000, "CHK-2")
        domain.close_job(state, job["id"])
        self.assertTrue(job["closed"])


class DraftTests(unittest.TestCase):
    def test_strict_draft_with_nothing_to_bill(self):
        state = new_state()
        job = new_job(state)
        with self.assertRaises(domain.GateError) as raised:
            domain.draft_invoice(state, job["id"], strict=True)
        self.assertIn("Nothing to draft", str(raised.exception))

    def test_mark_done_with_open_draft_names_it(self):
        state = new_state()
        job = new_job(state)
        domain.record_progress(state, job["id"], 4_000_000)
        with self.assertRaises(domain.GateError) as raised:
            domain.mark_done(state, job["id"])
        self.assertIn(job["invoices"][-1]["number"], str(raised.exception))
        self.assertEqual(job["work_status"], "in_progress")

    def test_packet_built_from_playbook(self):  # M12
        state = new_state()
        job = new_job(state)
        domain.update_payer(state, job["payer_id"], {"packet": "W-9, notarized waiver"})
        domain.record_progress(state, job["id"], 1_000_000)
        labels = [i["label"] for i in job["invoices"][-1]["packet"]]
        self.assertEqual(labels, ["W-9", "Notarized waiver"])

    def test_job_notes_stay_notes(self):  # R1-04: notes never become the paperwork list
        state = new_state()
        plain = new_job(state, customer="Lee Family")
        job = new_job(state, name="Oak deck", customer="Ray Family", note="Gate code 1234, call before 8am")
        self.assertEqual(state["payers"][-1]["packet"], state["payers"][-2]["packet"])
        [rec] = job["records"]
        self.assertEqual((rec["kind"], rec["label"], rec["note"]), ("note", "Job notes", "Gate code 1234, call before 8am"))
        self.assertEqual(plain["records"], [])

    def test_approver_and_email_optional_until_send(self):  # R1-05
        state = new_state()
        job = new_job(state, approver="", ap_email="", customer="Dana Homeowner")
        payer = state["payers"][-1]
        self.assertEqual((payer["approver"], payer["ap_email"]), ("Dana Homeowner", ""))
        domain.mark_done(state, job["id"])
        clear_packet(state, job)
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(state, job["id"])
        self.assertIn("Add the email pay apps go to", str(raised.exception))
        self.assertIn("ap_email", raised.exception.fields)
        self.assertIn(job["invoices"][-1]["status"], domain.DRAFT_STATUSES, "nothing changed")
        self.assertFalse(state.get("email_log"))
        domain.update_payer(state, payer["id"], {"ap_email": "dana@example.com"})
        domain.approve_and_send(state, job["id"])
        self.assertEqual(state["email_log"][-1]["to"], "dana@example.com")

    def test_repeat_customer_keeps_saved_contact(self):  # R1-05
        state = new_state()
        new_job(state, customer="Park GC", approver="Kim", ap_email="ap@park.example")
        new_job(state, name="Second", customer="park gc", approver="Someone", ap_email="other@park.example")
        [payer] = state["payers"]
        self.assertEqual((payer["approver"], payer["ap_email"]), ("Kim", "ap@park.example"))
        self.assertTrue(any(a["action"] == "payer_kept" for a in state["audit"]))
        # A blank email on the saved customer is filled in by the next job that has one.
        new_job(state, name="Third", customer="Ada Owner", ap_email="")
        new_job(state, name="Fourth", customer="Ada Owner", ap_email="ada@example.com")
        self.assertEqual(state["payers"][-1]["ap_email"], "ada@example.com")

    def test_attach_a_record(self):
        state = new_state()
        job = new_job(state)
        domain.record_progress(state, job["id"], 1_000_000)
        item = job["invoices"][-1]["packet"][0]
        domain.add_job_record(state, job["id"], "Signed contract PDF", "contract")
        domain.attach_record_to_packet(state, job["id"], item["id"], job["records"][-1]["id"])
        self.assertTrue(item["present"])


class ProgressCorrectionTests(unittest.TestCase):  # R1-09
    def test_progress_with_open_draft_redrafts_same_number(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        first = job["invoices"][-1]
        domain.resolve_packet_item(state, job["id"], first["packet"][0]["id"], reason="On site")
        domain.record_progress(state, job["id"], 350_000)
        live = [i for i in job["invoices"] if i["status"] != "void"]
        self.assertEqual(len(live), 1)
        self.assertEqual((live[0]["number"], live[0]["gross_cents"]), (first["number"], 350_000))
        self.assertEqual(first["status"], "void")
        self.assertTrue(live[0]["packet"][0]["present"], "the paperwork carries over")
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)

    def test_progress_refused_while_the_customer_reviews_the_draft(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        domain.send_pencil(state, job["id"])
        with self.assertRaises(domain.GateError) as raised:
            domain.record_progress(state, job["id"], 350_000)
        self.assertIn("is with the customer as a draft", str(raised.exception))
        self.assertEqual(job["invoices"][-1]["gross_cents"], 400_000)

    def test_void_then_lower_progress_redrafts(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        domain.void_draft(state, job["id"])
        self.assertEqual(job["completed_value_cents"], 0)
        domain.record_progress(state, job["id"], 300_000)
        self.assertEqual(job["invoices"][-1]["gross_cents"], 300_000)

    def test_floor_is_what_is_already_billed(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        send_open(state, job)
        with self.assertRaises(domain.GateError) as raised:
            domain.record_progress(state, job["id"], 400_000)
        self.assertIn("more than the $4,000.00 already billed", raised.exception.fields["completed_cents"])

    def test_pay_app_numbers_do_not_skip_after_void(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        number = job["invoices"][-1]["number"]
        self.assertTrue(number.endswith("-01"))
        domain.void_draft(state, job["id"])
        domain.record_progress(state, job["id"], 400_000)
        self.assertEqual(job["invoices"][-1]["number"], number)
        send_open(state, job)
        domain.record_progress(state, job["id"], 600_000)
        self.assertTrue(job["invoices"][-1]["number"].endswith("-02"))

    def test_a_number_the_customer_saw_is_not_reused(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        domain.send_pencil(state, job["id"])
        domain.void_draft(state, job["id"])
        domain.record_progress(state, job["id"], 300_000)
        self.assertTrue(job["invoices"][-1]["number"].endswith("-02"))

    def test_void_draft_releases_change_orders(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.add_change_order(state, job["id"], "Extra outlets", 50_000)
        co = job["change_orders"][0]
        domain.include_change_order(state, job["id"], co["id"])
        domain.record_progress(state, job["id"], 400_000)
        domain.void_draft(state, job["id"])
        self.assertFalse(co["included_in_billing"])
        self.assertTrue(any(a["detail"] == f"CO {co['number']} taken off the voided draft." for a in state["audit"]))
        domain.record_progress(state, job["id"], 400_000)
        self.assertFalse([l for l in job["invoices"][-1]["lines"] if l.get("co_id")])


class ChangeOrderTests(unittest.TestCase):
    def test_co_approved_after_done_is_billable(self):  # M4
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], inv["amount_cents"], "CHK-1")
        domain.add_change_order(state, job["id"], "Extra outlet", 250_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][0]["id"])
        domain.draft_invoice(state, job["id"], strict=True)
        self.assertEqual(job["invoices"][-1]["amount_cents"], 250_000)

    def test_co_validation(self):  # M5
        state = new_state()
        job = new_job(state)
        # A negative amount is a credit (R3-06), refused only when larger than the contract.
        for title, amount in [("", 1000), ("Tile", 0), ("Tile", -job["contract_cents"] - 1)]:
            with self.assertRaises(domain.GateError):
                domain.add_change_order(state, job["id"], title, amount)
        with self.assertRaises(domain.GateError):
            domain.add_change_order(state, job["id"], "Tile", 1000, schedule_days=400)
        domain.add_change_order(state, job["id"], "Tile", -5, schedule_days=-2)
        self.assertEqual((job["change_orders"][-1]["amount_cents"], job["change_orders"][-1]["schedule_days"]), (-5, -2))

    def test_unsigned_included_co_blocks_send(self):
        state = new_state()
        job = new_job(state)
        domain.add_change_order(state, job["id"], "Tile upgrade", 120_000)
        domain.include_change_order(state, job["id"], job["change_orders"][0]["id"])
        domain.record_progress(state, job["id"], 1_000_000)
        inv = clear_packet(state, job)
        self.assertEqual(inv["amount_cents"], 1_120_000)
        with self.assertRaises(domain.GateError) as raised:
            domain.approve_and_send(state, job["id"])
        self.assertTrue(any("CO 1" in b or "Change Order 1" in b for b in raised.exception.blockers))


class PaymentTests(unittest.TestCase):
    def _sent_job(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        send_open(state, job)
        return state, job

    def test_negative_and_oversized_payment_rejected(self):  # M6
        state, job = self._sent_job()
        for amount in [-100, 0, 400_001]:
            with self.assertRaises(domain.GateError):
                domain.match_payment(state, job["id"], amount, "")
        self.assertEqual(job["payments"], [])

    def test_duplicate_reference_rejected(self):
        state, job = self._sent_job()
        domain.match_payment(state, job["id"], 100_000, "CHK-9")
        with self.assertRaises(domain.GateError):
            domain.match_payment(state, job["id"], 100_000, "CHK-9")

    def test_payment_requires_choice_when_two_open(self):
        state, job = self._sent_job()
        domain.record_progress(state, job["id"], 700_000)
        send_open(state, job)
        with self.assertRaises(domain.GateError):
            domain.match_payment(state, job["id"], 100_000, "CHK-1")
        first = job["invoices"][0]
        domain.match_payment(state, job["id"], 100_000, "CHK-1", invoice_id=first["id"])
        self.assertEqual(job["payments"][-1]["invoice_id"], first["id"])

    def test_waiver_per_invoice(self):  # M8
        state, job = self._sent_job()
        domain.record_progress(state, job["id"], 700_000)
        send_open(state, job)
        a, b = job["invoices"]
        domain.match_payment(state, job["id"], a["amount_cents"], "CHK-1", invoice_id=a["id"])
        domain.send_waiver(state, job["id"], "Owner")
        self.assertTrue(job["waiver_sent"])
        domain.match_payment(state, job["id"], b["amount_cents"], "CHK-2", invoice_id=b["id"])
        self.assertFalse(job["waiver_sent"])
        domain.send_waiver(state, job["id"], "Owner")
        self.assertTrue(a["waiver_sent"] and b["waiver_sent"])
        self.assertEqual(len([e for e in state["email_log"] if e["kind"] == "waiver"]), 2)


class CloseAndFollowUpTests(unittest.TestCase):
    def test_close_refuses_fresh_job(self):  # M9
        state = new_state()
        job = new_job(state)
        with self.assertRaises(domain.GateError):
            domain.close_job(state, job["id"])

    def test_closed_job_rejects_mutations(self):
        state = seed_state()
        with self.assertRaises(domain.GateError):
            domain.add_change_order(state, "job-hist-maple", "Late extra", 1000)
        with self.assertRaises(domain.GateError):
            domain.draft_invoice(state, "job-hist-maple", strict=True)

    def test_follow_up_respects_window_and_writes_outbox(self):  # M13
        state = new_state()
        job = new_job(state)
        domain.record_progress(state, job["id"], 400_000)
        inv = send_open(state, job)
        with self.assertRaises(domain.GateError) as raised:
            domain.record_follow_up(state, job["id"], "Owner")
        self.assertIn("not due until", str(raised.exception))
        inv["sent_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
        domain.record_follow_up(state, job["id"], "Owner")
        self.assertEqual(state["email_log"][-1]["kind"], "follow_up")

    def test_second_job_keeps_payer_contact(self):  # M11
        state = new_state()
        new_job(state)
        new_job(state, name="Pine bath", approver="Someone Else", ap_email="other@jones.example")
        payer = state["payers"][0]
        self.assertEqual(payer["ap_email"], "ap@jones.example")
        self.assertEqual(payer["approver"], "Sam Jones")
        self.assertTrue(any(a["action"] == "payer_kept" for a in state["audit"]))

    def test_playbook_days_bounded(self):
        state = seed_state()
        for bad in [0, 366, 10 ** 12, "abc"]:
            with self.assertRaises(domain.GateError):
                domain.update_payer(state, "payer-ellison", {"follow_up_days": bad})



class ReviewFindingTests(unittest.TestCase):
    """Regressions for issues found in the independent review of the first fix."""

    def test_retainage_cannot_be_chased_mid_job(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.record_progress(state, job["id"], 500_000)
        send_open(state, job)
        with self.assertRaises(domain.GateError) as raised:
            domain.chase_retainage(state, job["id"])
        self.assertIn("closeout", str(raised.exception))
        self.assertFalse([e for e in state["email_log"] if e["kind"] == "retainage"])

    def test_co_after_done_billable_on_stale_legacy_record(self):
        state = seed_state()
        job = domain._job(state, "job-decatur")
        domain.add_change_order(state, "job-decatur", "Extra outlet", 1_000_000)
        domain.approve_change_order(state, "job-decatur", job["change_orders"][-1]["id"])
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 1_000_000)
        self.assertTrue(domain.close_blockers(job))

    def test_legacy_waiver_not_marked_sent_by_new_one(self):
        state = seed_state()
        job = domain._job(state, "job-ponce")
        self.assertTrue(job["waiver_due"] and not job["waiver_sent"])
        domain.add_change_order(state, "job-ponce", "Tile upgrade", 120_000)
        domain.approve_change_order(state, "job-ponce", job["change_orders"][-1]["id"])
        domain.draft_invoice(state, "job-ponce", strict=True)
        inv = clear_packet(state, job)
        domain.approve_and_send(state, "job-ponce", "Owner")
        domain.match_payment(state, "job-ponce", inv["amount_cents"], "CHK-P2", invoice_id=inv["id"])
        domain.send_waiver(state, "job-ponce", "Owner")
        self.assertFalse(job["waiver_sent"], "the older waiver is still unsent")
        domain.send_waiver(state, "job-ponce", "Owner")
        self.assertTrue(job["waiver_sent"])
        self.assertEqual(len([e for e in state["email_log"] if e["kind"] == "waiver"]), 2)

    def test_unsigned_co_job_can_be_unstuck(self):
        state = new_state()
        job = new_job(state)
        domain.add_change_order(state, job["id"], "Tile upgrade", 120_000)
        co_id = job["change_orders"][0]["id"]
        domain.include_change_order(state, job["id"], co_id)
        domain.record_progress(state, job["id"], 1_000_000)
        with self.assertRaises(domain.GateError):
            domain.withdraw_change_order(state, job["id"], co_id)
        domain.void_draft(state, job["id"])
        domain.withdraw_change_order(state, job["id"], co_id)
        # Voiding takes the progress back to what is billed (R1-09); enter it again.
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)
        domain.record_progress(state, job["id"], 1_000_000)
        self.assertEqual(job["invoices"][-1]["gross_cents"], 1_000_000)
        domain.void_draft(state, job["id"])
        domain.mark_done(state, job["id"])
        self.assertEqual(job["invoices"][-1]["amount_cents"], 10_000_000)

    def test_generated_reference_skips_typed_one(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        send_open(state, job)
        domain.match_payment(state, job["id"], 100_000, "manual-2")
        domain.match_payment(state, job["id"], 100_000, "")
        refs = [p["reference"] for p in job["payments"]]
        self.assertEqual(len(refs), len(set(refs)))


def job_route(state, job, action, body):
    """A job action the way the page calls it: the route's own handler, with its body."""
    ctx = server.Ctx(user={}, body=body, company_id="co-test", actor="Owner")
    return server.JOB_ACTIONS[action][1](None, state, job["id"], None, ctx)


class DrawTests(unittest.TestCase):  # R3-01: "This draw $" goes to the server as period_cents
    def test_period_cents_adds_to_what_is_billed(self):
        state = new_state()
        job = new_job(state, contract=10_000_000, retainage=10)
        job_route(state, job, "progress", {"period_cents": 3_000_000})
        send_open(state, job)
        job_route(state, job, "progress", {"period_cents": 2_000_000, "notes": "Second draw"})
        self.assertEqual(job["completed_value_cents"], 5_000_000)
        second = job["invoices"][-1]
        self.assertEqual((second["gross_cents"], second["retainage_cents"], second["amount_cents"]), (2_000_000, 200_000, 1_800_000))

    def test_period_cents_replaces_an_open_draft(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        send_open(state, job)
        job_route(state, job, "progress", {"period_cents": 100_000})
        job_route(state, job, "progress", {"period_cents": 150_000})
        live = [i for i in job["invoices"] if i["status"] != "void"]
        self.assertEqual([i["gross_cents"] for i in live], [400_000, 150_000])
        self.assertEqual(job["completed_value_cents"], 550_000)

    def test_period_cents_refusals_name_the_draw(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        send_open(state, job)
        for body, says in [({"period_cents": 0}, "more than $0"), ({"period_cents": 700_000}, "$6,000.00 left on the contract"),
                           ({"period_cents": "lots"}, "as a number")]:
            with self.assertRaises(domain.GateError) as raised:
                job_route(state, job, "progress", body)
            self.assertIn(says, raised.exception.fields["period_cents"])
        self.assertEqual(job["completed_value_cents"], 400_000)

    def test_total_done_so_far_still_works(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        job_route(state, job, "progress", {"completed_cents": 250_000})
        self.assertEqual(job["invoices"][-1]["gross_cents"], 250_000)


class RecordPaymentTests(unittest.TestCase):  # R3-02
    def _sent(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        send_open(state, job)
        return state, job

    def test_received_on_from_the_job_route_is_stored(self):
        state, job = self._sent()
        job_route(state, job, "match-payment", {"amount_cents": 100_000, "reference": "CHK 1", "received_on": "2026-10-02"})
        self.assertEqual(job["payments"][-1]["matched_at"], "2026-10-02T12:00:00+00:00")
        job_route(state, job, "match-payment", {"amount_cents": 100_000, "reference": "CHK 2", "received_on": ""})
        self.assertTrue(job["payments"][-1]["matched_at"].startswith(datetime.now(timezone.utc).date().isoformat()[:4]))

    def test_bad_or_future_date_is_refused_under_the_field(self):
        state, job = self._sent()
        later = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
        for day in ["10/02/2026", later]:
            with self.assertRaises(domain.GateError) as raised:
                job_route(state, job, "match-payment", {"amount_cents": 100_000, "received_on": day})
            self.assertIn("received_on", raised.exception.fields)
        self.assertEqual(job["payments"], [])

    def test_job_view_shows_what_is_still_owed(self):
        state, job = self._sent()
        domain.match_payment(state, job["id"], 150_000, "CHK 3")
        inv = domain.job_view(state, job)["invoices"][-1]
        self.assertEqual((inv["status"], inv["remaining_cents"]), ("partial", 250_000))


class ShortPayTests(unittest.TestCase):  # R4-01
    def _partial(self, retainage=0):
        state = new_state()
        job = new_job(state, contract=3_000_000, retainage=retainage, name="Ironleaf deck")
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], 2_875_000, "CHK 3101", invoice_id=inv["id"])
        return state, job, inv

    def test_partial_then_short_pay_settles_and_opens_waiver(self):
        state, job, inv = self._partial()
        job_route_item(state, job, "accept-short-pay", inv["id"], {"reason": "Backcharge for dumpster"})
        self.assertEqual(inv["status"], "paid")
        self.assertEqual(inv["amount_cents"], 3_000_000)   # the pay app stays as it was sent
        self.assertEqual((inv["short_pay_cents"], inv["short_pay_reason"]), (125_000, "Backcharge for dumpster"))
        self.assertTrue(inv["waiver_due"])
        self.assertFalse(inv["waiver_sent"])
        m = domain.job_money(job)
        self.assertEqual((m["outstanding_cents"], m["short_paid_cents"], m["unbilled_completed_cents"]), (0, 125_000, 0))
        self.assertEqual(state["audit"][-1]["action"], "short_pay")
        self.assertEqual([b["kind"] for b in domain.close_blocker_items(job)], ["waiver"])
        domain.send_waiver(state, job["id"], "Owner", invoice_id=inv["id"])
        domain.close_job(state, job["id"])
        self.assertTrue(job["closed"])

    def test_short_pay_needs_a_payment_and_a_reason(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        inv = send_open(state, job)
        with self.assertRaises(domain.GateError) as raised:
            domain.settle_short_pay(state, job["id"], inv["id"], "Backcharge")
        self.assertIn("Record what came in", str(raised.exception))
        domain.match_payment(state, job["id"], 300_000, "CHK 1")
        with self.assertRaises(domain.GateError) as raised:
            job_route_item(state, job, "accept-short-pay", inv["id"], {"reason": "  "})
        self.assertIn("reason", raised.exception.fields)
        self.assertEqual(inv["status"], "partial")

    def test_next_pay_app_does_not_bill_the_short_pay_again(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.record_progress(state, job["id"], 400_000)
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], 300_000, "CHK 1")
        domain.settle_short_pay(state, job["id"], inv["id"], "Disputed line 2")
        domain.record_progress(state, job["id"], 600_000)
        self.assertEqual(job["invoices"][-1]["gross_cents"], 200_000)

    def test_rest_paid_after_all_comes_off_the_short_pay(self):
        state, job, inv = self._partial()
        domain.settle_short_pay(state, job["id"], inv["id"], "Backcharge")
        domain.match_payment(state, job["id"], 100_000, "CHK 3200")
        self.assertEqual((inv["status"], inv["short_pay_cents"]), ("paid", 25_000))
        self.assertEqual(domain.job_money(job)["collected_cents"], 2_975_000)


class ReopenAndUndoTests(unittest.TestCase):  # R4-02
    def test_reopen_job_allows_late_payment(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.mark_done(state, job["id"])
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], 900_000, "CHK 1")
        domain.settle_short_pay(state, job["id"], inv["id"], "Customer held $1,000 for touch-ups")
        domain.send_waiver(state, job["id"], "Owner", invoice_id=inv["id"])
        domain.close_job(state, job["id"])
        with self.assertRaises(domain.GateError):
            domain.match_payment(state, job["id"], 100_000, "CHK 2")
        with self.assertRaises(domain.GateError) as raised:
            job_route(state, job, "reopen", {"reason": ""})
        self.assertIn("reason", raised.exception.fields)
        job_route(state, job, "reopen", {"reason": "Touch-up money came in"})
        self.assertFalse(job["closed"])
        self.assertNotIn("closed_at", job)
        self.assertEqual(state["audit"][-1]["action"], "reopen_job")
        domain.match_payment(state, job["id"], 100_000, "CHK 2")
        self.assertEqual((inv["short_pay_cents"], domain.job_money(job)["collected_cents"]), (0, 1_000_000))
        domain.close_job(state, job["id"])
        self.assertTrue(job["closed"])
        with self.assertRaises(domain.GateError):
            domain.reopen_job(state, "job-none", "x")

    def test_undo_done_restores_progress(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.record_progress(state, job["id"], 400_000)
        send_open(state, job)
        domain.mark_done(state, job["id"])
        final = job["invoices"][-1]
        self.assertTrue(domain.can_undo_done(job))
        job_route(state, job, "undo-done", {})
        self.assertEqual((final["status"], job["work_status"], job["completed_value_cents"]), ("void", "in_progress", 400_000))
        self.assertNotIn("completed_at", job)
        self.assertEqual(domain.job_money(job)["unbilled_completed_cents"], 0)
        # Progress goes on from what is billed; the final pay app's number is used again.
        domain.record_progress(state, job["id"], 700_000)
        self.assertEqual((job["invoices"][-1]["gross_cents"], job["invoices"][-1]["number"]), (300_000, final["number"]))

    def test_undo_done_refused_once_the_final_pay_app_went_out(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.mark_done(state, job["id"])
        send_open(state, job)
        self.assertFalse(domain.can_undo_done(job))
        with self.assertRaises(domain.GateError) as raised:
            domain.undo_mark_done(state, job["id"])
        self.assertIn("Pull it back first", str(raised.exception))
        self.assertEqual(job["work_status"], "complete")


class PullBackTests(unittest.TestCase):  # R4-03
    def test_void_sent_then_redraft_lower(self):
        state = new_state()
        job = new_job(state, contract=1_000_000, retainage=10)
        domain.record_progress(state, job["id"], 600_000)
        inv = send_open(state, job)
        number = inv["number"]
        job_route_item(state, job, "void-sent", inv["id"], {"reason": "Billed line 3 twice"})
        self.assertEqual((inv["status"], inv["voided_reason"]), ("void", "Billed line 3 twice"))
        self.assertEqual(job["completed_value_cents"], 0)
        m = domain.job_money(job)
        self.assertEqual((m["billed_cents"], m["retainage_held_cents"], m["outstanding_cents"]), (0, 0, 0))
        mail = state["email_log"][-1]
        self.assertEqual((mail["kind"], mail["to"]), ("pay_app_withdrawn", "ap@jones.example"))
        self.assertEqual(mail["subject"], f"Pay application {number} withdrawn — Pine kitchen")
        self.assertIn("Billed line 3 twice", mail["body"])
        domain.record_progress(state, job["id"], 450_000)
        again = job["invoices"][-1]
        self.assertEqual(again["gross_cents"], 450_000)
        self.assertNotEqual(again["number"], number)   # the customer saw that number
        import payer_page
        self.assertIn("withdrew this pay app", payer_page.render(state, job, inv, "/p/x"))

    def test_void_sent_refused_after_a_payment(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        inv = send_open(state, job)
        domain.match_payment(state, job["id"], 100_000, "CHK 1")
        with self.assertRaises(domain.GateError) as raised:
            domain.void_sent_pay_app(state, job["id"], inv["id"], "Wrong amount")
        self.assertIn("A payment is recorded", str(raised.exception))
        self.assertEqual(inv["status"], "partial")

    def test_void_sent_needs_a_reason_and_the_newest_pay_app(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        domain.record_progress(state, job["id"], 400_000)
        first = send_open(state, job)
        domain.record_progress(state, job["id"], 600_000)
        with self.assertRaises(domain.GateError) as raised:
            domain.void_sent_pay_app(state, job["id"], first["id"], "Wrong")
        self.assertIn("Delete the draft", str(raised.exception))
        send_open(state, job)
        with self.assertRaises(domain.GateError) as raised:
            domain.void_sent_pay_app(state, job["id"], first["id"], "Wrong")
        self.assertIn("Pull that one back first", str(raised.exception))
        with self.assertRaises(domain.GateError) as raised:
            domain.void_sent_pay_app(state, job["id"], job["invoices"][-1]["id"], "")
        self.assertIn("reason", raised.exception.fields)


def job_route_item(state, job, action, item, body):
    ctx = server.Ctx(user={"email_verified": True}, body=body, company_id="co-test", actor="Owner")
    return server.JOB_ACTIONS[action][1](None, state, job["id"], item, ctx)


if __name__ == "__main__":
    unittest.main()
