"""Stephen's answers of 18 Nov 2026 on pay app approvers (round2.md, last section):

1. "The approver and the sender can be two different people ... the approval chain is likely to be
   more than one person and involve application of a signature to a document."
2. "If a client's approver list is empty, then the pay app cannot be approved and be sent out.
   Unapproved pay applications should never send."

Built as: every send needs approval, from the client's list, else the company's, else every
owner (Yusuf's default); approving is signing a typed full name (with a title if given), kept
with who, when and the version stamp; the PDF prints an Approved by block; sending freezes the
signatures; the Outbox and Send again refuse a pay app with none on record."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_seed
import domain
import payapp_pdf
import server
from approve_help import sign_http
from review_help import reviewed
from test_accounts import ServerCase
from test_money import clear_packet, new_job, new_state
import test_team  # the module, so its tests aren't collected again here
from test_team import JOB

OWNER = {"id": "u-own", "name": "Dana Whitfield"}
POOL = {"u-ana": "Ana Ruiz", "u-ben": "Ben Ode"}


def ready_state():
    state = new_state()
    domain.set_team_owners(state, [OWNER])
    job = new_job(state, contract=1_000_000)
    domain.record_progress(state, job["id"], 300_000, "a")
    inv = clear_packet(state, job)
    return state, job, inv


def sign(state, job, who=OWNER, name=None, **kw):
    return domain.approve_payapp(state, job["id"], who["name"], who["id"], signature=name or who["name"], **kw)


class WhoApproves(unittest.TestCase):
    def test_with_nobody_named_every_owner_approves(self):
        state, job, inv = ready_state()
        domain.set_team_owners(state, [OWNER, {"id": "u-own2", "name": "Lee Whitfield"}])
        view = domain.approval_view(state, job, inv)
        self.assertEqual((view["source"], view["waiting"], view["blocked"]), ("owners", ["Dana Whitfield", "Lee Whitfield"], ""))
        with self.assertRaises(domain.GateError) as refused:
            domain.approve_and_send(state, job["id"], "Office Sam")
        self.assertTrue(refused.exception.extra["approvals_missing"])
        self.assertIn("Dana Whitfield, Lee Whitfield", str(refused.exception))

    def test_one_owner_signs_then_sends(self):
        state, job, inv = ready_state()
        with self.assertRaises(domain.GateError):
            domain.approve_and_send(state, job["id"], OWNER["name"])
        sign(state, job, title="Owner")
        domain.approve_and_send(state, job["id"], OWNER["name"])
        self.assertEqual(inv["status"], "sent")
        self.assertEqual([a["signed_name"] for a in inv["approvals"]], ["Dana Whitfield"])

    def test_the_companys_list_then_the_clients_own(self):
        state, job, inv = ready_state()
        domain.set_payapp_approvers(state, ["u-ana"], POOL)
        self.assertEqual(domain.approval_view(state, job, inv)["source"], "company")
        self.assertEqual(domain.approvals_missing(state, job, inv), ["Ana Ruiz"], "a named list replaces the owners")
        domain.set_payapp_approvers(state, ["u-ben"], POOL, payer_id=job["payer_id"])
        self.assertEqual((domain.approval_view(state, job, inv)["source"], domain.approvals_missing(state, job, inv)), ("client", ["Ben Ode"]))
        # The company's list emptied: the owners again.
        domain.set_payapp_approvers(state, [], POOL, payer_id=job["payer_id"], same_as_company=True)
        domain.set_payapp_approvers(state, [], POOL)
        self.assertEqual(domain.approvals_missing(state, job, inv), ["Dana Whitfield"])

    def test_a_client_list_saved_empty_means_it_cant_be_approved_or_sent(self):
        state, job, inv = ready_state()
        domain.set_payapp_approvers(state, [], POOL, payer_id=job["payer_id"])
        view = domain.approval_view(state, job, inv)
        self.assertEqual(view["required"], [])
        self.assertIn("can't be sent. Name approvers in Client playbook.", view["blocked"])
        with self.assertRaises(domain.GateError) as refused:
            sign(state, job)
        self.assertEqual(refused.exception.action["view"], "playbook")
        with self.assertRaises(domain.GateError) as refused:
            domain.approve_and_send(state, job["id"], OWNER["name"])
        self.assertTrue(refused.exception.extra["approvals_none"])
        self.assertEqual(refused.exception.action, {"label": "Open Client playbook", "view": "playbook"})
        self.assertEqual(inv["status"], "awaiting_approval")

    def test_nobody_known_at_all_is_refused_not_waved_through(self):
        state, job, inv = ready_state()
        state.pop("team_owners")
        self.assertIn("Nobody can approve", domain.approval_blocked(state, job))
        with self.assertRaises(domain.GateError) as refused:
            domain.approve_and_send(state, job["id"], "Sam")
        self.assertEqual(refused.exception.action["view"], "company")


class ApprovingIsSigning(unittest.TestCase):
    def test_a_typed_full_name_is_required_and_kept_with_who_when_and_the_version(self):
        state, job, inv = ready_state()
        for blank in (None, "", " ", "D"):
            with self.assertRaises(domain.GateError) as refused:
                domain.approve_payapp(state, job["id"], OWNER["name"], OWNER["id"], signature=blank)
            self.assertIn("signature", refused.exception.fields)
        sign(state, job, name="  Dana   M. Whitfield ", title="Owner", ip="203.0.113.9", user_agent="Test browser")
        sig = inv["approvals"][0]
        self.assertEqual((sig["id"], sig["name"], sig["signed_name"], sig["title"]), ("u-own", "Dana Whitfield", "Dana M. Whitfield", "Owner"))
        self.assertEqual(sig["stamp"], domain.approval_stamp(state, job, inv))
        self.assertEqual((sig["ip"], sig["user_agent"], sig["consent"]), ("203.0.113.9", "Test browser", domain.APPROVAL_CONSENT))
        self.assertTrue(sig["at"])
        self.assertIn("signed PA-", state["audit"][-1]["detail"])
        # The page sees the signature, not the network address kept with it.
        shown = domain.job_view(state, job)["invoices"][-1]
        self.assertNotIn("approvals", shown)
        self.assertEqual(shown["approval"]["required"][0]["signed_name"], "Dana M. Whitfield")
        self.assertNotIn("ip", shown["approval"]["required"][0])

    def test_a_change_after_signing_takes_the_signature_off_and_they_sign_again(self):
        state, job, inv = ready_state()
        sign(state, job)
        domain.record_progress(state, job["id"], 450_000, "b")
        draft = clear_packet(state, job)
        self.assertEqual(domain.approvals_missing(state, job, draft), ["Dana Whitfield"])
        with self.assertRaises(domain.GateError):
            domain.approve_and_send(state, job["id"], OWNER["name"])
        sign(state, job)
        domain.approve_and_send(state, job["id"], OWNER["name"])
        self.assertEqual(draft["status"], "sent")

    def test_sending_freezes_the_signatures_with_the_pay_app(self):
        state, job, inv = ready_state()
        domain.set_payapp_approvers(state, ["u-ana", "u-ben"], POOL)
        sign(state, job, {"id": "u-ben", "name": "Ben Ode"})
        sign(state, job, {"id": "u-ana", "name": "Ana Ruiz"}, title="Project manager")
        domain.approve_and_send(state, job["id"], "Office Sam")
        frozen = [dict(a) for a in inv["approvals"]]
        self.assertEqual([a["name"] for a in frozen], ["Ana Ruiz", "Ben Ode"], "in the order of the list")
        self.assertTrue(inv["approvals_frozen_at"])
        # Lists and the team change after; the signatures it went with don't.
        domain.set_payapp_approvers(state, [], POOL, payer_id=job["payer_id"])
        domain.drop_approver(state, "u-ana")
        self.assertEqual(inv["approvals"], frozen)
        sent = domain.job_view(state, job)["invoices"][-1]
        self.assertNotIn("approval", sent)
        self.assertEqual([(s["name"], s["title"]) for s in sent["signatures"]], [("Ana Ruiz", "Project manager"), ("Ben Ode", "")])
        self.assertEqual(domain.signed_approvals(state, job, inv)[0]["signed_name"], "Ana Ruiz")

    def test_the_pdf_prints_approved_by_with_each_signature_title_and_date(self):
        state, job, inv = ready_state()
        sign(state, job, name="Dana M. Whitfield", title="Owner")
        draft_pdf, _ = payapp_pdf.for_invoice(state, job["id"], inv["id"])
        self.assertIn(b"APPROVED BY", draft_pdf, "a draft shows who has signed so far")
        domain.approve_and_send(state, job["id"], OWNER["name"])
        pdf, _ = payapp_pdf.for_invoice(state, job["id"], inv["id"])
        self.assertIn(b"APPROVED BY", pdf)
        self.assertIn(b"/s/ Dana M. Whitfield", pdf)
        self.assertIn(b"Dana Whitfield, Owner", pdf)
        self.assertIn(f"Date {inv['approvals'][0]['at'][:10]}".encode(), pdf)
        # Unsigned: no block.
        state2, job2, inv2 = ready_state()
        self.assertNotIn(b"APPROVED BY", payapp_pdf.for_invoice(state2, job2["id"], inv2["id"])[0])

    def test_many_signatures_stay_on_the_first_page(self):
        state, job, inv = ready_state()
        # The page is portrait (Nov 26 walkthrough): more fit than in landscape, so more sign.
        people = {f"u-{n}": f"Approver {n}" for n in range(30)}
        domain.set_payapp_approvers(state, list(people), people)
        for uid, name in people.items():
            sign(state, job, {"id": uid, "name": name})
        domain.approve_and_send(state, job["id"], "Sam")
        pdf, _ = payapp_pdf.for_invoice(state, job["id"], inv["id"])
        self.assertIn(b"more, kept with the contractor", pdf)


class NothingElseSendsIt(unittest.TestCase):
    def test_a_pay_app_email_with_no_approval_on_record_is_held(self):
        state, job, inv = ready_state()
        sign(state, job)
        domain.approve_and_send(state, job["id"], OWNER["name"])
        email = next(e for e in state["email_log"] if e["kind"] == "pay_app")
        self.assertEqual(domain.send_unapproved(job, email), "")
        # One marked sent before 18 Nov, with nothing signed: never goes now.
        inv.pop("approvals_frozen_at")
        self.assertIn("no approval on record", domain.send_unapproved(job, email))
        inv["approvals_frozen_at"], inv["approvals"] = "2026-11-18T00:00:00+00:00", []
        self.assertIn("no approval on record", domain.send_unapproved(job, email))
        # Other emails are not pay apps.
        self.assertEqual(domain.send_unapproved(job, {"kind": "follow_up", "payapp_invoice_id": inv["id"]}), "")

    def test_a_pencil_copy_is_a_draft_for_review_not_the_pay_app(self):
        # Judgment call (Nov 18 build): the pencil copy is marked PENCIL COPY, for review, not for
        # payment, and comes before approval; it isn't held for signatures.
        state, job, inv = ready_state()
        domain.send_pencil(state, job["id"], "Sam")
        self.assertEqual(inv["pencil_status"], "sent")
        self.assertEqual(domain.send_unapproved(job, state["email_log"][-1]), "")

    def test_the_demo_sends_its_pay_apps_signed_by_its_owner(self):
        state, _ = demo_seed.build()
        sent = [i for j in state["jobs"] for i in j["invoices"] if i.get("sent_at") and i["status"] != "void"]
        self.assertTrue(sent)
        for inv in sent:
            self.assertEqual([a["signed_name"] for a in inv["approvals"]], [demo_seed.OWNER_NAME], inv["number"])
            self.assertTrue(inv["approvals_frozen_at"])
        self.assertEqual([o["name"] for o in state["team_owners"]], [demo_seed.OWNER_NAME])


class ApprovalsHttp(ServerCase):
    join = test_team.TeamHttpTests.join

    def setUp(self):
        super().setUp()
        tag = self._testMethodName.replace("_", "")[-18:]
        self.tag = tag
        self.owner, data = self.signup(f"owner-{tag}@sign.example", "Sign Co")
        self.owner_user = data["user"]
        _, state = self.request("POST", "/api/jobs", JOB, cookie=self.owner)
        self.job_id = state["jobs"][0]["id"]
        line = state["jobs"][0]["sov"]["lines"][0]
        _, out = self.request("POST", f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.3"}}, cookie=self.owner)
        for item in out["jobs"][0]["invoices"][-1]["packet"]:
            self.request("POST", f"/api/jobs/{self.job_id}/resolve/{item['id']}", {"reason": "In hand"}, cookie=self.owner)

    def draft(self):
        _, data = self.request("GET", f"/api/jobs/{self.job_id}", cookie=self.owner)
        return next(i for i in data["job"]["invoices"] if i["status"] in domain.DRAFT_STATUSES)

    def send(self, cookie=None):
        return self.request("POST", f"/api/jobs/{self.job_id}/send", {"reviewed": self.draft()["email_preview"]["stamp"]}, cookie=cookie or self.owner)

    def test_a_one_owner_company_signs_then_sends(self):
        inv = self.draft()
        self.assertEqual((inv["approval"]["source"], [p["id"] for p in inv["approval"]["required"]]), ("owners", [self.owner_user["id"]]))
        res, out = self.send()
        self.assertEqual((res.status, out.get("approvals_missing")), (409, True), out)
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/payapp-approve",
                                {"invoice_id": inv["id"], "stamp": inv["approval"]["stamp"], "signature": "Pat Pine", "title": "Owner"}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        res, out = self.send()
        self.assertEqual(res.status, 200, out)
        sent = out["jobs"][0]["invoices"][-1]
        self.assertEqual(sent["status"], "sent")
        self.assertEqual([(s["signed_name"], s["title"]) for s in sent["signatures"]], [("Pat Pine", "Owner")])
        self.assertNotIn("approvals", sent)
        with server.company_lock(self.owner_user["company_id"]):
            stored = server.load_company(self.owner_user["company_id"])
        sig = stored["jobs"][0]["invoices"][-1]["approvals"][0]
        self.assertEqual(sig["ip"], "127.0.0.1")
        res, pdf = self.request("GET", f"/api/jobs/{self.job_id}/payapp/{sent['id']}.pdf", cookie=self.owner)
        self.assertEqual(res.status, 200)
        self.assertIn(b"/s/ Pat Pine", pdf if isinstance(pdf, bytes) else pdf.encode())

    def test_the_sender_needs_no_approve_permission_and_a_member_cant_sign(self):
        member, _ = self.join(self.owner, f"fred-{self.tag}@sign.example")
        res, out = self.request("POST", "/api/team/profiles/member", {"perms": ["send_payapps"]}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(sign_http(self, self.job_id, member)[0].status, 403, "signing takes Approve pay apps")
        self.assertEqual(self.send(member)[0].status, 409)
        self.assertEqual(sign_http(self, self.job_id, self.owner)[0].status, 200)
        res, out = self.send(member)
        self.assertEqual(res.status, 200, out)

    def test_an_empty_client_list_is_said_and_refused(self):
        payer_id = self.request("GET", f"/api/jobs/{self.job_id}", cookie=self.owner)[1]["job"]["payer_id"]
        res, out = self.request("POST", f"/api/payers/{payer_id}/approvers", {"user_ids": []}, cookie=self.owner)
        self.assertEqual(res.status, 200, out)
        inv = self.draft()
        self.assertIn("Name approvers in Client playbook", inv["approval"]["blocked"])
        res, out = self.request("POST", f"/api/jobs/{self.job_id}/payapp-approve",
                                {"invoice_id": inv["id"], "stamp": inv["approval"]["stamp"], "signature": "Pat Pine"}, cookie=self.owner)
        self.assertEqual((res.status, out.get("approvals_none")), (409, True), out)
        res, out = self.send()
        self.assertEqual((res.status, out.get("approvals_none")), (409, True), out)

    def test_send_again_refuses_a_pay_app_with_no_approval_on_record(self):
        self.assertEqual(self.request("POST", f"/api/jobs/{self.job_id}/send", reviewed(self, self.job_id, self.owner), cookie=self.owner)[0].status, 200)
        company = self.owner_user["company_id"]
        with server.company_lock(company):
            state = server.load_company(company)
            inv = state["jobs"][0]["invoices"][-1]
            inv.pop("approvals_frozen_at")   # as one marked sent before 18 Nov
            email = next(e for e in state["email_log"] if e.get("kind") == "pay_app")
            email["status"] = "failed: test"
            server.save_company(company, state)
        res, out = self.request("POST", f"/api/outbox/{email['id']}/retry", {}, cookie=self.owner)
        self.assertEqual(res.status, 409, out)
        self.assertIn("no approval on record", out["error"])


if __name__ == "__main__":
    unittest.main()
