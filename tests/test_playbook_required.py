"""A new job needs a complete Client playbook (Decisions 3 and 4 of the Nov 3 brief), an anticipated
end date on the job, and paperwork a job can change for itself."""

import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calendar_feed
import domain
import exports
import job_archive


def new_state():
    return {"company": {"name": "Test"}, "payers": [], "jobs": [], "audit": [], "email_log": [], "files": []}


FULL = {"approver": "Sam Jones", "ap_email": "ap@jones.example", "phone": "404-555-0100", "client_type": "homeowner"}


def filed(state, job, label, kind):
    """A record with a stored file, as an upload leaves it."""
    fid = f"file-{len(state.setdefault('files', [])) + 1}"
    state["files"].append({"id": fid, "name": f"{label}.pdf", "ext": ".pdf", "size": 10})
    domain.add_job_record(state, job["id"], label, kind)
    job["records"][-1]["file_ids"] = [fid]
    return job["records"][-1]


def new_job(state, name="Pine kitchen", client="Jones Family", contract=1_000_000, **over):
    kwargs = {"address": "12 Pine St, Atlanta, GA 30307", **FULL, **over}
    return domain.create_job(state, name, client, contract, **kwargs)


def older_client(state, **over):
    """A client saved before the rule: no phone, and maybe no email."""
    payer = {"id": "payer-old", "name": "Old Client", "role": "Owner", "client_type": "owner", "approver": "Olive", "ap_email": "", "phone": "",
             "packet": "Signed contract", **over}
    state["payers"].append(payer)
    return payer


def older_job(state, payer):
    """A job of that client, made before the rule (no playbook_required on it)."""
    job = new_job(state, "Scratch", "Scratch Client")
    state["jobs"].remove(job)
    state["payers"] = [p for p in state["payers"] if p["id"] != job["payer_id"]]
    job.pop("playbook_required", None)
    job["payer_id"] = payer["id"]
    state["jobs"].append(job)
    return job


def draft_of(job):
    return next(i for i in job["invoices"] if i["status"] in domain.DRAFT_STATUSES)


class NewClientTests(unittest.TestCase):
    def refused(self, **over):
        state = new_state()
        with self.assertRaises(domain.GateError) as ctx:
            new_job(state, **over)
        self.assertEqual(state["jobs"], [])
        self.assertEqual(state["payers"], [], "nothing is saved on a refusal")
        return ctx.exception.fields

    def test_refused_without_each_field_with_its_own_reason(self):
        for key, words in (("approver", "signs off"), ("ap_email", "email"), ("phone", "phone")):
            fields = self.refused(**{key: ""})
            self.assertEqual(list(fields), [key])
            self.assertIn(words, fields[key])

    def test_refused_without_a_client_type(self):
        fields = self.refused(client_type="")
        self.assertEqual(list(fields), ["client_type"])
        self.assertIn("client type", fields["client_type"])

    def test_refused_without_a_name(self):
        state = new_state()
        with self.assertRaises(domain.GateError) as ctx:
            new_job(state, client="")
        self.assertIn("customer", ctx.exception.fields)

    def test_all_at_once_are_said_at_once(self):
        fields = self.refused(approver="", ap_email="", phone="", client_type="")
        self.assertEqual(set(fields), {"approver", "ap_email", "phone", "client_type"})

    def test_a_complete_one_is_saved(self):
        state = new_state()
        job = new_job(state)
        payer = state["payers"][0]
        self.assertEqual((payer["approver"], payer["ap_email"], payer["phone"], payer["client_type"]),
                         ("Sam Jones", "ap@jones.example", "(404) 555-0100", "homeowner"))
        self.assertTrue(job["playbook_required"])

    def test_an_older_role_still_says_the_type(self):
        state = new_state()
        new_job(state, client_type="", role="GC")
        self.assertEqual(state["payers"][0]["client_type"], "gc")

    def test_near_match_is_asked_before_the_missing_fields(self):
        state = new_state()
        new_job(state, client="ABC Builders")
        with self.assertRaises(domain.GateError) as ctx:
            domain.create_job(state, "Porch", "ABC Builders Inc", 100_000, address="1 Elm St")
        self.assertEqual(ctx.exception.extra["same_customer"]["name"], "ABC Builders")

    def test_new_on_purpose_needs_the_fields_too(self):
        state = new_state()
        new_job(state, client="ABC Builders")
        with self.assertRaises(domain.GateError) as ctx:
            domain.create_job(state, "Porch", "ABC Builders Inc", 100_000, address="1 Elm St", new_customer=True)
        self.assertEqual(set(ctx.exception.fields), {"approver", "ap_email", "phone", "client_type"})


class SavedClientTests(unittest.TestCase):
    def test_a_complete_client_needs_nothing_more(self):
        state = new_state()
        new_job(state)
        job = domain.create_job(state, "Deck", "jones family", 100_000, address="1 Elm St")
        self.assertEqual(job["payer_id"], state["payers"][0]["id"])

    def test_an_incomplete_client_is_refused_per_field(self):
        state = new_state()
        older_client(state)
        with self.assertRaises(domain.GateError) as ctx:
            domain.create_job(state, "Deck", "Old Client", 100_000, address="1 Elm St")
        self.assertEqual(set(ctx.exception.fields), {"ap_email", "phone"})
        self.assertEqual(state["jobs"], [])

    def test_what_is_typed_fills_the_playbook_and_is_audited(self):
        state = new_state()
        payer = older_client(state)
        domain.create_job(state, "Deck", "Old Client", 100_000, address="1 Elm St", ap_email="ap@old.example", phone="4045550111")
        self.assertEqual((payer["ap_email"], payer["phone"]), ("ap@old.example", "(404) 555-0111"))
        self.assertEqual(payer["approver"], "Olive")
        said = " ".join(a["detail"] for a in state["audit"] if a["action"] == "playbook_update")
        self.assertIn("ap@old.example", said)
        self.assertIn("(404) 555-0111", said)

    def test_half_filled_saves_nothing(self):
        state = new_state()
        payer = older_client(state)
        with self.assertRaises(domain.GateError):
            domain.create_job(state, "Deck", "Old Client", 100_000, address="1 Elm St", ap_email="ap@old.example")
        self.assertEqual(payer["ap_email"], "")

    def test_a_saved_phone_or_email_is_kept(self):
        state = new_state()
        new_job(state)
        domain.create_job(state, "Deck", "Jones Family", 100_000, address="1 Elm St", phone="404-555-0199", ap_email="x@y.example")
        self.assertEqual((state["payers"][0]["phone"], state["payers"][0]["ap_email"]), ("(404) 555-0100", "ap@jones.example"))


class OlderClientTests(unittest.TestCase):
    """Any of the five missing counts, and what used to be defaulted isn't an answer (Decision 3 as tightened)."""

    def refused_fields(self, state, **typed):
        with self.assertRaises(domain.GateError) as ctx:
            domain.create_job(state, "Deck", "Old Client", 100_000, address="1 Elm St", **typed)
        self.assertEqual(state["jobs"], [])
        return ctx.exception.fields

    def test_a_blank_signer_is_refused_until_entered(self):
        state = new_state()
        payer = older_client(state, approver="", ap_email="o@old.example", phone="(404) 555-0111")
        self.assertEqual(list(self.refused_fields(state)), ["approver"])
        domain.create_job(state, "Deck", "Old Client", 100_000, address="1 Elm St", approver="Olive")
        self.assertEqual(payer["approver"], "Olive")

    def test_a_signer_that_defaulted_to_the_client_is_confirmed_once(self):
        state = new_state()
        payer = older_client(state, approver="Old Client", ap_email="o@old.example", phone="(404) 555-0111")
        self.assertEqual(domain.playbook_missing(payer), ["approver"])
        self.assertIn("Confirm", self.refused_fields(state)["approver"])
        domain.create_job(state, "Deck", "Old Client", 100_000, address="1 Elm St", approver="Old Client")
        self.assertEqual(domain.playbook_missing(payer), [])
        domain.create_job(state, "Porch", "Old Client", 100_000, address="1 Elm St")
        self.assertEqual(len(state["jobs"]), 2)

    def test_a_signer_can_be_changed_while_confirming(self):
        state = new_state()
        payer = older_client(state, approver="Old Client", ap_email="o@old.example", phone="(404) 555-0111")
        domain.create_job(state, "Deck", "Old Client", 100_000, address="1 Elm St", approver="Kim")
        self.assertEqual(payer["approver"], "Kim")
        self.assertFalse([a for a in state["audit"] if a["action"] == "payer_kept"])

    def test_saving_the_card_confirms_the_signer(self):
        state = new_state()
        payer = older_client(state, approver="Old Client")
        domain.update_payer(state, payer["id"], {"approver": "Old Client"})
        self.assertNotIn("approver", domain.playbook_missing(payer))

    def test_a_homeowner_who_typed_their_own_name_isnt_asked_again(self):
        state = new_state()
        new_job(state, client="Dana Lee", approver="Dana Lee")
        self.assertEqual(domain.playbook_missing(state["payers"][0]), [])

    def test_a_type_read_from_an_older_role_is_asked_for(self):
        state = new_state()
        payer = older_client(state, ap_email="o@old.example", phone="(404) 555-0111")
        del payer["client_type"]
        self.assertEqual(list(self.refused_fields(state)), ["client_type"])
        domain.create_job(state, "Deck", "Old Client", 100_000, address="1 Elm St", client_type="public")
        self.assertEqual((payer["client_type"], payer["role"]), ("public", "Owner"))

    def test_their_running_jobs_arent_held_up(self):
        state = new_state()
        payer = older_client(state, approver="Old Client", ap_email="o@old.example")
        del payer["client_type"]
        job = older_job(state, payer)
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        for item in draft_of(job)["packet"]:
            item.update(required=False)
        domain._refresh_draft_gate(job, state)
        domain.approve_and_send(state, job["id"])
        self.assertEqual(job["invoices"][-1]["status"], "sent")
        self.assertFalse(domain.job_view(state, job)["playbook_blocks"])

    def test_the_client_list_says_what_is_missing(self):
        state = new_state()
        payer = dict(older_client(state, approver="Old Client"))
        domain.payer_extras(state, payer)
        self.assertEqual(payer["playbook_missing"], ["approver", "ap_email", "phone"])

    def test_the_seed_is_complete(self):
        from seed import seed_state
        for payer in seed_state()["payers"]:
            self.assertEqual(domain.playbook_missing(payer), [], payer["name"])


class AddClientTests(unittest.TestCase):
    BODY = {"name": "Dana Lee", "client_type": "homeowner", "approver": "Dana", "ap_email": "dana@example.com", "phone": "4045550100"}

    def test_same_rules_per_field(self):
        for key in ("approver", "ap_email", "phone", "client_type", "name"):
            state = new_state()
            with self.assertRaises(domain.GateError) as ctx:
                domain.add_payer(state, {**self.BODY, key: ""})
            self.assertEqual(list(ctx.exception.fields), [key])
            self.assertEqual(state["payers"], [])

    def test_a_complete_one_is_added(self):
        state = new_state()
        payer = domain.add_payer(state, self.BODY)
        self.assertEqual(domain.playbook_missing(payer), [])

    def test_near_match_is_still_asked_first(self):
        state = new_state()
        domain.add_payer(state, {**self.BODY, "name": "ABC Builders"})
        with self.assertRaises(domain.GateError) as ctx:
            domain.add_payer(state, {"name": "ABC Builders Inc"})
        self.assertIn("same_customer", ctx.exception.extra)


class EditPlaybookTests(unittest.TestCase):
    def test_set_fields_cant_be_removed(self):
        for key in ("approver", "ap_email", "phone", "name"):
            state = new_state()
            job = new_job(state)
            before = dict(state["payers"][0])
            with self.assertRaises(domain.GateError) as ctx:
                domain.update_payer(state, job["payer_id"], {key: "  "})
            self.assertIn(key, ctx.exception.fields)
            self.assertEqual(state["payers"][0][key], before[key])

    def test_an_older_client_can_be_saved_without_them(self):
        state = new_state()
        payer = older_client(state)
        domain.update_payer(state, payer["id"], {"phone": "", "follow_up_days": 9})
        self.assertEqual(payer["follow_up_days"], 9)
        domain.update_payer(state, payer["id"], {"phone": "4045550100"})
        self.assertEqual(payer["phone"], "(404) 555-0100")


class PayAppGateTests(unittest.TestCase):
    def job_with_incomplete_client(self):
        """A job made under the rule whose client lost a field (merged into an older client)."""
        state = new_state()
        job = new_job(state)
        older_client(state, ap_email="old@example.com")
        domain.merge_payer(state, job["payer_id"], "payer-old")
        return state, job

    def assert_refused(self, call):
        with self.assertRaises(domain.GateError) as ctx:
            call()
        err = ctx.exception
        self.assertIn("Old Client", str(err))
        self.assertTrue(any("phone" in line for line in err.blockers), err.blockers)
        self.assertEqual(err.action["view"], "playbook")
        self.assertEqual(err.extra["playbook_missing"], ["phone"])

    def test_no_draft_starts_any_way(self):
        state, job = self.job_with_incomplete_client()
        self.assert_refused(lambda: domain.record_progress(state, job["id"], 0, period_cents=100_000))
        self.assert_refused(lambda: domain.mark_done(state, job["id"]))
        self.assert_refused(lambda: domain.draft_invoice(state, job["id"], strict=True))
        self.assertEqual(job["invoices"], [])
        self.assertEqual(job["work_status"], "in_progress")

    def test_quantities_are_refused_too(self):
        state, job = self.job_with_incomplete_client()
        domain.save_sov(state, job["id"], [{"description": "All", "unit": "LS", "qty": "1", "rate": "10000"}], op_pct=0)
        line = job["sov"]["lines"][0]
        self.assert_refused(lambda: domain.record_sov_progress(state, job["id"], {line["id"]: "1"}))

    def test_an_open_draft_doesnt_send(self):
        state = new_state()
        job = new_job(state)
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        older_client(state, ap_email="old@example.com")
        domain.merge_payer(state, job["payer_id"], "payer-old")
        for item in draft_of(job)["packet"]:
            item.update(present=True, required=False)
        self.assert_refused(lambda: domain.approve_and_send(state, job["id"]))
        self.assert_refused(lambda: domain.send_pencil(state, job["id"]))
        self.assertEqual(draft_of(job)["sent_at"], None)

    def test_an_existing_job_of_an_older_client_isnt_blocked(self):
        state = new_state()
        job = older_job(state, older_client(state, ap_email="old@example.com"))
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        self.assertEqual(len(job["invoices"]), 1)
        view = domain.job_view(state, job)
        self.assertEqual(view["playbook_missing"], ["phone"])
        self.assertFalse(view["playbook_blocks"])

    def test_the_job_says_what_is_missing(self):
        state, job = self.job_with_incomplete_client()
        view = domain.job_view(state, job)
        self.assertEqual(view["playbook_missing"], ["phone"])
        self.assertTrue(view["playbook_blocks"])

    def test_a_complete_job_says_nothing(self):
        state = new_state()
        view = domain.job_view(state, new_job(state))
        self.assertEqual(view["playbook_missing"], [])
        self.assertFalse(view["playbook_blocks"])


class EndDateTests(unittest.TestCase):
    def test_saved_on_a_new_job_and_listed(self):
        state = new_state()
        job = new_job(state, started_on="2026-11-02", anticipated_end_on="2027-03-31")
        self.assertEqual(job["anticipated_end_on"], "2027-03-31")
        view = domain.job_view(state, job)
        self.assertIn({"key": "anticipated_end", "label": "Anticipated end", "date": "2027-03-31"}, view["key_dates"])

    def test_optional(self):
        state = new_state()
        job = new_job(state)
        self.assertNotIn("anticipated_end_on", job)
        self.assertNotIn("anticipated_end", [d["key"] for d in domain.job_view(state, job)["key_dates"]])

    def test_not_before_the_start_nor_nonsense(self):
        for bad in ("2026-10-01", "not a date", "2026-02-31", "2099-01-01"):
            state = new_state()
            with self.assertRaises(domain.GateError) as ctx:
                new_job(state, started_on="2026-11-02", anticipated_end_on=bad)
            self.assertIn("anticipated_end_on", ctx.exception.fields, bad)
            self.assertEqual(state["jobs"], [])

    def test_the_same_day_as_the_start_is_fine(self):
        state = new_state()
        self.assertEqual(new_job(state, started_on="2026-11-02", anticipated_end_on="2026-11-02")["anticipated_end_on"], "2026-11-02")

    def test_edit_changes_clears_and_audits(self):
        state = new_state()
        job = new_job(state, started_on="2026-11-02")
        domain.edit_job(state, job["id"], {"anticipated_end_on": "2027-01-15"})
        self.assertEqual(job["anticipated_end_on"], "2027-01-15")
        self.assertIn("anticipated end date", state["audit"][-1]["detail"])
        domain.edit_job(state, job["id"], {"anticipated_end_on": ""})
        self.assertNotIn("anticipated_end_on", job)
        self.assertIn("anticipated end date", state["audit"][-1]["detail"])

    def test_edit_is_checked_against_the_start_either_way(self):
        state = new_state()
        job = new_job(state, started_on="2026-11-02", anticipated_end_on="2026-12-01")
        with self.assertRaises(domain.GateError) as ctx:
            domain.edit_job(state, job["id"], {"anticipated_end_on": "2026-11-01"})
        self.assertIn("anticipated_end_on", ctx.exception.fields)
        with self.assertRaises(domain.GateError) as ctx:
            domain.edit_job(state, job["id"], {"started_on": "2026-12-02"})
        self.assertIn("started_on", ctx.exception.fields)
        domain.edit_job(state, job["id"], {"started_on": "2026-12-02", "anticipated_end_on": "2026-12-20"})
        self.assertEqual(job["started_on"], "2026-12-02")

    def test_calendar_lists_it_while_the_job_is_open(self):
        state = new_state()
        job = new_job(state, anticipated_end_on="2027-03-31")
        hits = [e for e in calendar_feed.events(state) if e["uid"] == f"{job['id']}-end"]
        self.assertEqual([e["title"] for e in hits], ["Pine kitchen: anticipated end"])
        self.assertIn("anticipated end", calendar_feed.render(state))
        job["closed"] = True
        self.assertEqual([e for e in calendar_feed.events(state) if e["uid"] == f"{job['id']}-end"], [])

    def test_the_jobs_export_and_job_file_list_it(self):
        state = new_state()
        new_job(state, anticipated_end_on="2027-03-31")
        rows = list(csv.reader(io.StringIO(exports.csv_text(state, "jobs"))))
        self.assertEqual(rows[1][rows[0].index("Anticipated end")], "2027-03-31")
        self.assertIn("Anticipated end: 2027-03-31", job_archive.summary_text(state, state["jobs"][0]))


class JobPaperworkTests(unittest.TestCase):
    def labels(self, invoice):
        return [item["label"] for item in invoice["packet"]]

    def test_starts_from_the_clients_list(self):
        state = new_state()
        job = new_job(state)
        domain.update_payer(state, job["payer_id"], {"packet": "Signed contract, Progress photos"})
        view = domain.job_view(state, job)["paperwork_view"]
        self.assertEqual((view["own"], view["labels"], view["none"]), (False, ["Signed contract", "Progress photos"], False))

    def test_a_new_draft_uses_the_jobs_list(self):
        state = new_state()
        job = new_job(state)
        domain.update_payer(state, job["payer_id"], {"packet": "Signed contract, Progress photos"})
        domain.set_job_paperwork(state, job["id"], {"packet": "Signed contract, Certified payroll"}, "Pat")
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        self.assertEqual(self.labels(draft_of(job)), ["Conditional waiver on progress payment", "Signed contract", "Certified payroll"])
        self.assertEqual(state["payers"][0]["packet"], "Signed contract, Progress photos", "the client's list stays")
        view = domain.job_view(state, job)["paperwork_view"]
        self.assertEqual((view["own"], view["labels"]), (True, ["Signed contract", "Certified payroll"]))
        said = [a for a in state["audit"] if a["action"] == "job_paperwork"]
        self.assertIn("Certified payroll", said[-1]["detail"])
        self.assertIn("Progress photos", said[-1]["detail"])
        self.assertEqual(said[-1]["job_id"], job["id"])

    def test_an_open_draft_follows_and_keeps_what_it_has(self):
        state = new_state()
        job = new_job(state)
        domain.update_payer(state, job["payer_id"], {"packet": "Signed contract, Progress photos"})
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        contract = next(i for i in draft_of(job)["packet"] if i["label"] == "Signed contract")
        filed(state, job, "Signed contract", "contract")
        domain.attach_record_to_packet(state, job["id"], contract["id"], job["records"][-1]["id"])
        domain.set_job_paperwork(state, job["id"], {"packet": "Signed contract, Insurance certificate"}, "Pat")
        labels = self.labels(draft_of(job))
        self.assertEqual(labels, ["Conditional waiver on progress payment", "Signed contract", "Insurance certificate"])
        kept = next(i for i in draft_of(job)["packet"] if i["label"] == "Signed contract")
        self.assertTrue(kept["present"])
        self.assertTrue(kept["attachments"])

    def test_a_sent_pay_app_never_changes(self):
        state = new_state()
        job = new_job(state)
        domain.update_payer(state, job["payer_id"], {"packet": "Progress photos"})
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        sent = draft_of(job)
        for item in sent["packet"]:
            item.update(required=False)
        domain._refresh_draft_gate(job, state)
        domain.approve_and_send(state, job["id"])
        self.assertEqual(sent["status"], "sent")
        before = self.labels(sent)
        domain.set_job_paperwork(state, job["id"], {"packet": "Insurance certificate"}, "Pat")
        self.assertEqual(self.labels(sent), before)
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        self.assertEqual(self.labels(draft_of(job)), ["Conditional waiver on progress payment", "Insurance certificate"])

    def test_nothing_needed_keeps_the_conditional_waiver(self):
        state = new_state()
        job = new_job(state)
        domain.set_job_paperwork(state, job["id"], {"packet": "", "no_paperwork": True}, "Pat")
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        self.assertEqual(self.labels(draft_of(job)), ["Conditional waiver on progress payment"])
        self.assertTrue(domain.job_view(state, job)["paperwork_view"]["none"])

    def test_empty_without_nothing_needed_is_refused(self):
        state = new_state()
        job = new_job(state)
        with self.assertRaises(domain.GateError) as ctx:
            domain.set_job_paperwork(state, job["id"], {"packet": " "}, "Pat")
        self.assertIn("packet", ctx.exception.fields)
        self.assertNotIn("paperwork_labels", job)

    def test_taking_the_waiver_off_a_draft_leaves_it_there(self):
        state = new_state()
        job = new_job(state)
        domain.update_payer(state, job["payer_id"], {"packet": "Conditional waiver, Progress photos"})
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        domain.set_job_paperwork(state, job["id"], {"packet": "Progress photos"}, "Pat")
        waiver = next(i for i in draft_of(job)["packet"] if str(i.get("waiver_kind", "")).startswith("conditional"))
        self.assertTrue(waiver["required"])

    def test_a_client_that_takes_no_waiver_stays_skipped(self):
        state = new_state()
        job = new_job(state)
        state["payers"][0]["waiver_not_needed"] = {"by": "Pat", "at": "x", "reason": "They don't take one"}
        domain.set_job_paperwork(state, job["id"], {"packet": "Progress photos"}, "Pat")
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        waiver = next(i for i in draft_of(job)["packet"] if str(i.get("waiver_kind", "")).startswith("conditional"))
        self.assertFalse(waiver["required"])
        self.assertEqual(waiver["skipped_reason"], "They don't take one")

    def test_not_needed_comes_off_the_jobs_list_not_the_clients(self):
        state = new_state()
        job = new_job(state)
        domain.update_payer(state, job["payer_id"], {"packet": "Progress photos"})
        domain.set_job_paperwork(state, job["id"], {"packet": "Progress photos, Insurance certificate"}, "Pat")
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        item = next(i for i in draft_of(job)["packet"] if i["label"] == "Insurance certificate")
        domain.skip_packet_item(state, job["id"], item["id"], "Not on this job", "Pat")
        self.assertEqual(job["paperwork_labels"], ["Progress photos"])
        self.assertEqual(state["payers"][0]["packet"], "Progress photos")

    def test_use_the_clients_list_again(self):
        state = new_state()
        job = new_job(state)
        domain.update_payer(state, job["payer_id"], {"packet": "Progress photos"})
        domain.record_progress(state, job["id"], 0, period_cents=100_000)
        domain.set_job_paperwork(state, job["id"], {"packet": "Insurance certificate"}, "Pat")
        domain.set_job_paperwork(state, job["id"], {"use_client": True}, "Pat")
        self.assertNotIn("paperwork_labels", job)
        self.assertIn("Progress photos", self.labels(draft_of(job)))
        self.assertNotIn("Insurance certificate", self.labels(draft_of(job)))
        self.assertIn("client's list", state["audit"][-1]["detail"])

    def test_no_change_writes_no_audit(self):
        state = new_state()
        job = new_job(state)
        domain.set_job_paperwork(state, job["id"], {"packet": "Progress photos"}, "Pat")
        rows = len(state["audit"])
        domain.set_job_paperwork(state, job["id"], {"packet": "Progress photos."}, "Pat")
        self.assertEqual(len(state["audit"]), rows)

    def test_closeout_items_on_the_jobs_list_go_to_closeout(self):
        state = new_state()
        job = new_job(state)
        domain.set_job_paperwork(state, job["id"], {"packet": "Progress photos, As-builts"}, "Pat")
        domain.mark_done(state, job["id"])
        self.assertEqual([i["label"] for i in job["closeout_packet"]], ["As-builts"])
        self.assertNotIn("As-builts", self.labels(draft_of(job)))


class PublicJobItemTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.job = new_job(self.state)
        domain.set_job_paperwork(self.state, self.job["id"], {"packet": "Certified payroll, Notarized pay application"}, "Pat")

    def item(self, label):
        return next(i for i in draft_of(self.job)["packet"] if i["label"] == label)

    def test_in_the_catalog(self):
        labels = {e["label"]: e for e in domain.PAPERWORK_CATALOG}
        for label in ("Certified payroll", "Notarized pay application"):
            self.assertTrue(labels[label]["per_pay_app"])
            self.assertFalse(labels[label]["closeout"])

    def test_filled_from_their_own_records_only(self):
        domain.add_job_record(self.state, self.job["id"], "Week 12 time sheet", "ticket")
        domain.add_job_record(self.state, self.job["id"], "Backup", "pay_app")
        domain.record_progress(self.state, self.job["id"], 0, period_cents=100_000)
        self.assertFalse(self.item("Certified payroll")["present"], "a time sheet isn't certified payroll")
        self.assertFalse(self.item("Notarized pay application")["present"], "backup isn't a notarized application")
        domain.void_draft(self.state, self.job["id"])
        filed(self.state, self.job, "WH-347 week 12", "payroll")
        filed(self.state, self.job, "Notarized G702", "notarized")
        domain.record_progress(self.state, self.job["id"], 0, period_cents=50_000)
        for label in ("Certified payroll", "Notarized pay application"):
            self.assertFalse(self.item(label)["present"], "suggested, not cleared")
            self.assertTrue(self.item(label)["attachments"][0]["auto"])
            domain.confirm_packet_item(self.state, self.job["id"], self.item(label)["id"], "Pat")
            self.assertTrue(self.item(label)["present"])

    def test_notarized_isnt_taken_as_made_here(self):
        domain.record_progress(self.state, self.job["id"], 0, period_cents=100_000)
        item = self.item("Notarized pay application")
        self.assertFalse(item["present"])
        self.assertNotEqual(item.get("present_reason"), domain.MADE_HERE_REASON)
        for label in ("Notarized pay application", "notarized pay app", "Notarized application"):
            self.assertFalse(domain._made_here(label, self.job), label)
        self.assertTrue(domain._made_here("Pay application", self.job))

    def test_a_record_goes_with_one_pay_app(self):
        filed(self.state, self.job, "WH-347 week 12", "payroll")
        domain.record_progress(self.state, self.job["id"], 0, period_cents=100_000)
        first = draft_of(self.job)
        domain.confirm_packet_item(self.state, self.job["id"], self.item("Certified payroll")["id"], "Pat")
        self.assertTrue(self.item("Certified payroll")["present"])
        for item in first["packet"]:
            item.update(required=False)
        domain._refresh_draft_gate(self.job, self.state)
        domain.approve_and_send(self.state, self.job["id"])
        domain.record_progress(self.state, self.job["id"], 0, period_cents=100_000)
        self.assertFalse(self.item("Certified payroll")["present"])


if __name__ == "__main__":
    unittest.main()
