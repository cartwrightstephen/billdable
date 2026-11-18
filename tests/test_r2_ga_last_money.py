"""Georgia waivers, Nov 18 (Stephen: "Build as needed by your read of the law"): the filled-in final
form for the retainage request when that is the last money, by the same rule as the request's send
gate; and the 90-day affidavit-of-nonpayment reminder for a Georgia waiver sent before its money."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain
import waiver_form
from test_money import clear_packet, new_job, new_state, send_open
from test_round2_waivers import pdf_fields
from waiver_help import signed_waiver

GA = "12 Peach St, Atlanta, GA 30303"
FORMS_ON = {"DURACASH_WAIVER_FORMS": "GA"}


def retainage_job(pay=True, ga=True):
    """A finished $10,000 job at 10% retainage, billed in full; with pay, paid but the $1,000
    retainage. Its closeout documents are in, the conditional final waiver among them."""
    state = new_state()
    job = new_job(state, contract=1_000_000, retainage=10)
    if ga:
        job["address"] = GA
    domain.record_progress(state, job["id"], 1_000_000)
    domain.mark_done(state, job["id"])
    inv = send_open(state, job)
    if pay:
        domain.match_payment(state, job["id"], inv["amount_cents"], "c", invoice_id=inv["id"])
    item = None
    for it in job["closeout_packet"]:
        if domain.is_waiver_item(it):
            item = it
        else:
            domain.resolve_packet_item(state, job["id"], it["id"], "Sam", reason="x")
    rec = signed_waiver(state, job["id"], "conditional_final", "Conditional final waiver")
    domain.attach_record_to_packet(state, job["id"], item["id"], rec["id"], "Sam")
    domain.confirm_packet_item(state, job["id"], item["id"], "Sam", answer="conditional")
    return state, job


def form(state, job, for_id=domain.RETAINAGE_REQUEST, which="final"):
    with mock.patch.dict(os.environ, FORMS_ON):
        return waiver_form.for_waiver(state, job["id"], for_id, which)


def request_goes(state, job, amount=None):
    """Whether the retainage request with the conditional final waiver gets past its waiver gate."""
    try:
        domain.chase_retainage(state, job["id"], "Owner", amount_cents=amount)
    except domain.GateError:
        return False
    return True


class FinalFormWithTheRetainageRequest(unittest.TestCase):
    def test_filled_in_for_the_request_naming_the_retainage(self):
        state, job = retainage_job()
        pdf, name, refused = form(state, job)
        self.assertEqual(refused, "")
        self.assertTrue(name.endswith("-GA-final-waiver.pdf"))
        self.assertIn(b"UPON FINAL", pdf)
        fields = pdf_fields(pdf)
        self.assertEqual(fields["amount"], "1,000.00", "the whole amount still owed: the retainage held")
        self.assertNotIn("through", fields, "the final form has no through-date")
        self.assertEqual(domain.job_view(state, job)["retainage_final_holds"], [])

    def test_still_refused_without_the_request(self):
        state, job = retainage_job()
        inv = job["invoices"][-1]
        for for_id in ("", inv["id"]):
            pdf, _, refused = form(state, job, for_id)
            self.assertIsNone(pdf, for_id)
            self.assertIn("A final waiver waits until all the money", refused)
            self.assertIn("$1,000.00 of retainage is still held", refused)

    def test_off_unless_the_server_names_the_state(self):
        state, job = retainage_job()
        with mock.patch.dict(os.environ, {"DURACASH_WAIVER_FORMS": ""}):
            pdf, _, refused = waiver_form.for_waiver(state, job["id"], domain.RETAINAGE_REQUEST, "final")
        self.assertIsNone(pdf)
        self.assertEqual(refused, "No state form is set up for this job's state.")

    def test_the_interim_form_is_unchanged(self):
        state, job = retainage_job()
        pdf, _, refused = form(state, job, which="interim")
        self.assertEqual(refused, "")
        self.assertEqual(pdf_fields(pdf)["amount"], "", "the interim form keeps retainage out; nothing to prefill for the request")

    def test_refused_while_a_pay_app_is_unpaid(self):
        state, job = retainage_job(pay=False)
        pdf, _, refused = form(state, job)
        self.assertIsNone(pdf)
        self.assertEqual(refused, f"A final waiver goes only with the request for all the money left on {job['name']}: $9,000.00 is still owed.")

    def test_refused_while_a_change_order_is_unsigned(self):
        state, job = retainage_job()
        domain.add_change_order(state, job["id"], "Extra outlet", 10_000)
        pdf, _, refused = form(state, job)
        self.assertIsNone(pdf)
        self.assertIn("CO 1 is not signed", refused)

    def test_refused_with_work_not_billed_or_on_a_draft(self):
        state, job = retainage_job()
        domain.add_change_order(state, job["id"], "Extra outlet", 10_000)
        domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
        self.assertIn("$100.00 is not billed", form(state, job)[2])
        domain.draft_invoice(state, job["id"])
        self.assertIn("is on a draft", form(state, job)[2])

    def test_refused_when_the_work_is_not_finished(self):
        state, job = retainage_job()
        job["work_status"] = "in_progress"   # as on a job reopened since (undo done needs the final pay app pulled back)
        pdf, _, refused = form(state, job)
        self.assertIsNone(pdf)
        self.assertIn("the job isn't finished", refused)


class OneRuleForTheFormAndTheSend(unittest.TestCase):
    """Decision 6's gate on the retainage request and the filled-in final form agree in every case
    (domain.retainage_final_holds)."""

    def scenarios(self):
        def base():
            return retainage_job()

        def unpaid():
            return retainage_job(pay=False)

        def unsigned_co():
            state, job = retainage_job()
            domain.add_change_order(state, job["id"], "Extra outlet", 10_000)
            return state, job

        def unbilled():
            state, job = retainage_job()
            domain.add_change_order(state, job["id"], "Extra outlet", 10_000)
            domain.approve_change_order(state, job["id"], job["change_orders"][-1]["id"])
            return state, job

        def on_a_draft():
            state, job = unbilled()
            domain.draft_invoice(state, job["id"])
            return state, job

        def not_finished():
            state, job = retainage_job()
            job["work_status"] = "in_progress"
            return state, job

        return {"base": base, "unpaid": unpaid, "unsigned_co": unsigned_co, "unbilled": unbilled, "on_a_draft": on_a_draft,
                "not_finished": not_finished}

    def test_served_exactly_when_the_request_with_it_goes(self):
        served = {}
        for name, make in self.scenarios().items():
            state, job = make()
            pdf, _, refused = form(state, job)
            holds = domain.retainage_final_holds(job)
            served[name] = pdf is not None
            self.assertEqual(served[name], not holds, name)
            self.assertEqual(served[name], request_goes(state, job), name)
            if holds and job.get("work_status") == "complete":
                with self.assertRaises(domain.GateError) as gate:
                    domain.chase_retainage(state, job["id"], "Owner")
                self.assertEqual(gate.exception.extra.get("final_waiver_held"), holds, name)
                self.assertTrue(refused.endswith(f"{', '.join(holds)}."), name)
        self.assertEqual([k for k, v in served.items() if v], ["base"])

    def test_part_of_the_retainage_is_not_the_last_money(self):
        state, job = retainage_job()
        self.assertEqual(domain.retainage_final_holds(job, 50_000), ["$500.00 of retainage is not asked for"])
        self.assertFalse(request_goes(state, job, 50_000))
        self.assertTrue(request_goes(state, job))

    def test_no_retainage_held_is_not_a_retainage_request(self):
        state = new_state()
        job = new_job(state, contract=1_000_000)
        self.assertEqual(domain.retainage_final_holds(job), ["no retainage is held"])


# ---------- the 90-day affidavit of nonpayment ----------

def at(days):
    return datetime.now(timezone.utc) + timedelta(days=days)


def rows(state, days, perms=None, kinds=("affidavit", "waiver_in_effect")):
    return [r for r in domain.rows_with_perms(domain.today_rows(state, at(days)), perms) if r["kind"] in kinds]


def day_words(d):
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def pay_app_with_waiver(ga=True, waiver=True):
    """PA-01 for $4,000 sent with its conditional progress waiver, today."""
    state = new_state()
    job = new_job(state, contract=1_000_000)
    if ga:
        job["address"] = GA
    domain.record_progress(state, job["id"], 400_000)
    inv = clear_packet(state, job)
    if waiver:
        rec = signed_waiver(state, job["id"], "conditional_progress", "Conditional waiver")
        domain.change_payapp_attachments(state, job["id"], inv["id"], "add_record", rec["id"])
        domain.answer_waiver_question(state, job["id"], rec["id"], "conditional", "Owner")
    domain.approve_and_send(state, job["id"], "Owner")
    return state, job, inv


class AffidavitReminder(unittest.TestCase):
    def setUp(self):
        self.state, self.job, self.inv = pay_app_with_waiver()
        self.sent = datetime.now(timezone.utc).date()
        self.effect = self.sent + timedelta(days=90)

    def test_from_day_60_urgent_from_day_80(self):
        self.assertEqual(rows(self.state, 59), [])
        row = rows(self.state, 60)[0]
        self.assertEqual((row["kind"], row["label"], row["tone"], row["perm"], row["invoice_id"]),
                         ("affidavit", "Affidavit of nonpayment", "amber", "send_waivers", self.inv["id"]))
        self.assertEqual(row["text"], f"{self.inv['number']}: the lien waiver sent {day_words(self.sent)} takes effect "
                                      f"{day_words(self.effect)} unless you file an affidavit of nonpayment. Payment hasn't come in.")
        self.assertEqual(row["note"], "File it in the property's county; copy to the owner within 7 days")
        self.assertEqual(row["due"], "", "due today on the To Do, so This week shows it")
        self.assertEqual(rows(self.state, 79)[0]["tone"], "amber")
        urgent = rows(self.state, 80)[0]
        self.assertEqual(urgent["tone"], "red")
        self.assertLess(urgent["rank"], 1, "above a pay app ready to send")
        self.assertEqual(rows(self.state, 89)[0]["kind"], "affidavit")

    def test_then_a_note_that_it_is_in_effect_for_a_week(self):
        note = rows(self.state, 90)
        self.assertEqual([r["kind"] for r in note], ["waiver_in_effect"])
        self.assertEqual(note[0]["text"], f"{self.inv['number']}: the lien waiver sent {day_words(self.sent)} is now in effect "
                                          f"({day_words(self.effect)}). Payment hasn't come in.")
        self.assertEqual(rows(self.state, 96)[0]["kind"], "waiver_in_effect")
        self.assertEqual(rows(self.state, 97), [])

    def test_the_daily_email_lists_it_and_the_note_once(self):
        subject, body = domain.daily_digest(self.state, "https://app.example", at(60))
        self.assertIn("1 lien waiver near its 90 days, unpaid", subject)
        self.assertIn("Affidavit of nonpayment. " + rows(self.state, 60)[0]["text"], body)
        self.assertIn(f"https://app.example/jobs/{self.job['id']}", body)
        self.assertIn("1 lien waiver now in effect, unpaid", domain.daily_digest(self.state, "", at(90))[0])
        for later in (91, 95):
            digest = domain.daily_digest(self.state, "", at(later))
            self.assertNotIn("now in effect", digest[0] if digest else "", later)

    def test_only_for_those_who_send_waivers(self):
        self.assertEqual(rows(self.state, 60, ["record_payments", "send_payapps"]), [])
        self.assertEqual(len(rows(self.state, 60, ["send_waivers"])), 1)
        digest = domain.daily_digest(self.state, "", at(60), perms=["record_payments"])
        self.assertNotIn("90 days", digest[0] if digest else "")

    def test_a_part_payment_keeps_it_payment_in_full_clears_it(self):
        domain.match_payment(self.state, self.job["id"], 100_000, "part", invoice_id=self.inv["id"])
        self.assertEqual(self.inv["status"], "partial")
        self.assertEqual(len(rows(self.state, 70)), 1)
        domain.match_payment(self.state, self.job["id"], self.inv["amount_cents"] - 100_000, "rest", invoice_id=self.inv["id"])
        self.assertEqual(self.inv["status"], "paid")
        self.assertEqual(rows(self.state, 70), [])
        self.assertEqual(rows(self.state, 92), [], "paid: no note either")

    def test_georgia_only(self):
        self.job["address"] = "1 Main St, Rochester, NY 14604"
        self.job.pop("address_parts", None)
        self.assertEqual(rows(self.state, 60), [])

    def test_only_a_waiver_duracash_sent(self):
        state, _job, _inv = pay_app_with_waiver(waiver=False)
        self.assertEqual(rows(state, 60), [], "no waiver went with the pay app")
        self.state["email_log"][-1]["status"] = "failed: mailbox full"
        self.assertEqual(rows(self.state, 60), [], "the email didn't go out")
        self.state["email_log"][-1]["status"] = "sent"
        self.assertEqual(len(rows(self.state, 60)), 1)

    def test_a_retainage_request_with_the_final_waiver(self):
        state, job = retainage_job()
        domain.chase_retainage(state, job["id"], "Owner")
        self.assertEqual(state["email_log"][-1]["retainage_asked_cents"], 100_000)
        row = rows(state, 60)[0]
        self.assertEqual((row["item_id"], row["key"]), ("retainage", f"affidavit:{job['id']}:retainage"))
        self.assertTrue(row["text"].startswith("Retainage request: the lien waiver sent "), row["text"])
        domain.record_retainage_payment(state, job["id"], 40_000, "part")
        self.assertEqual(len(rows(state, 60)), 1, "part of what it asked for")
        domain.record_retainage_payment(state, job["id"], 60_000, "rest")
        self.assertEqual(rows(state, 60), [])

    def test_a_retainage_request_outside_georgia(self):
        state, job = retainage_job(ga=False)
        domain.chase_retainage(state, job["id"], "Owner")
        self.assertEqual(rows(state, 60), [])


if __name__ == "__main__":
    unittest.main()
