"""Every route in server.ROUTES walked three ways (Nov 27 hunt): signed out, signed in without the
route's permission, and signed in as another company. Each must be refused, or answer with the
signed-in company's own data only, and leave the other company's data as it was.

EXAMPLES names a path for every route; a new route without one fails this test, so it is walked
on purpose rather than left out by accident."""

import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import compliance
import demo_seed
import domain
import gcforms
import importer
import server
from domain import profiles
import gcform_pdfs as forms
from test_accounts import ServerCase, PASSWORD

MARK = "ZQXSECRETA"   # in company A's payroll file, a job name and a payer name

# (method, pattern) -> example paths. {job} {inv} {co} {payer} {file} {doc} {req} {gcf} {task} {email} {inbox} {user} {invite}
EXAMPLES = {
    ("GET", r"/api/inbox"): ["/api/inbox"],
    ("GET", r"/api/payapps/sent"): ["/api/payapps/sent"],
    ("GET", r"/api/export/(jobs|payapps|payments|change-orders)\.csv"): ["/api/export/jobs.csv", "/api/export/payments.csv"],
    ("POST", r"/api/tasks/assign"): ["/api/tasks/assign"],
    ("POST", r"/api/tasks/(task-[0-9a-f]{8})/done"): ["/api/tasks/{task}/done"],
    ("POST", r"/api/tasks/(task-[0-9a-f]{8})"): ["/api/tasks/{task}"],
    ("POST", r"/api/tasks"): ["/api/tasks"],
    ("GET", r"/api/payers/([^/]+)/jobs"): ["/api/payers/{payer}/jobs"],
    ("GET", r"/api/payers/([^/]+)/client-forms"): ["/api/payers/{payer}/client-forms"],
    ("GET", r"/api/payers/([^/]+)/client-forms/(gcf-[0-9a-f]{10})/(blank|sample)\.pdf"): ["/api/payers/{payer}/client-forms/{gcf}/blank.pdf", "/api/payers/{payer}/client-forms/{gcf}/sample.pdf"],
    ("POST", r"/api/payers/([^/]+)/client-forms/upload/([a-z_]+)"): ["/api/payers/{payer}/client-forms/upload/payapp"],
    ("POST", r"/api/payers/([^/]+)/client-forms/(gcf-[0-9a-f]{10})/match"): ["/api/payers/{payer}/client-forms/{gcf}/match"],
    ("POST", r"/api/payers/([^/]+)/client-forms/(gcf-[0-9a-f]{10})/note"): ["/api/payers/{payer}/client-forms/{gcf}/note"],
    ("POST", r"/api/payers/([^/]+)/client-forms/(gcf-[0-9a-f]{10})/retire"): ["/api/payers/{payer}/client-forms/{gcf}/retire"],
    ("POST", r"/api/payers/([^/]+)/client-forms/send"): ["/api/payers/{payer}/client-forms/send"],
    ("GET", r"/api/jobs/([^/]+)/payapp/([^/]+)/client-forms"): ["/api/jobs/{job}/payapp/{inv}/client-forms"],
    ("GET", r"/api/jobs/([^/]+)/payapp/([^/]+)/client-form/([a-z_]+)\.pdf"): ["/api/jobs/{job}/payapp/{inv}/client-form/payapp.pdf"],
    ("GET", r"/api/jobs/([^/]+)/archive/job-file\.zip"): ["/api/jobs/{job}/archive/job-file.zip"],
    ("POST", r"/api/contract/read"): ["/api/contract/read"],
    ("GET", r"/api/docusign/status"): ["/api/docusign/status"],
    ("GET", r"/api/docusign/callback"): ["/api/docusign/callback?code=x&state=y"],
    ("GET", r"/api/docusign/return"): ["/api/docusign/return"],
    ("POST", r"/api/docusign/connect"): ["/api/docusign/connect"],
    ("POST", r"/api/docusign/disconnect"): ["/api/docusign/disconnect"],
    ("POST", r"/api/docusign/settings"): ["/api/docusign/settings"],
    ("POST", r"/api/docusign/sign"): ["/api/docusign/sign"],
    ("POST", r"/api/docusign/check"): ["/api/docusign/check"],
    ("POST", r"/api/docusign/webhook"): ["/api/docusign/webhook"],
    ("GET", r"/api/waiver-forms"): ["/api/waiver-forms"],
    ("POST", r"/api/jobs/([^/]+)/payapp/([^/]+)/attachments"): ["/api/jobs/{job}/payapp/{inv}/attachments"],
    ("POST", r"/api/auth/ping"): ["/api/auth/ping"],
    ("GET", r"/api/auth/sessions"): ["/api/auth/sessions"],
    ("POST", r"/api/auth/sessions/end"): ["/api/auth/sessions/end"],
    ("POST", r"/api/auth/password/code"): ["/api/auth/password/code"],
    ("POST", r"/api/me/appearance"): ["/api/me/appearance"],
    ("POST", r"/api/team/profiles"): ["/api/team/profiles"],
    ("POST", r"/api/team/profiles/([^/]+)"): ["/api/team/profiles/member"],
    ("POST", r"/api/team/profiles/([^/]+)/delete"): ["/api/team/profiles/member/delete"],
    ("POST", r"/api/team/admin"): ["/api/team/admin"],
    ("GET", r"/api/compliance"): ["/api/compliance"],
    ("GET", r"/api/compliance/docs/(" + compliance.DOC_ID + r")/file"): ["/api/compliance/docs/{doc}/file"],
    ("POST", r"/api/compliance/docs"): ["/api/compliance/docs"],
    ("POST", r"/api/compliance/docs/(" + compliance.DOC_ID + r")/file"): ["/api/compliance/docs/{doc}/file"],
    ("POST", r"/api/compliance/docs/(" + compliance.DOC_ID + r")/delete"): ["/api/compliance/docs/{doc}/delete"],
    ("POST", r"/api/compliance/docs/(" + compliance.DOC_ID + r")"): ["/api/compliance/docs/{doc}"],
    ("POST", r"/api/compliance/requirements"): ["/api/compliance/requirements"],
    ("POST", r"/api/compliance/requirements/(" + compliance.REQ_ID + r")/delete"): ["/api/compliance/requirements/{req}/delete"],
    ("POST", r"/api/compliance/settings"): ["/api/compliance/settings"],
    ("POST", r"/api/compliance/liens/([^/]+)"): ["/api/compliance/liens/{job}"],
    ("GET", r"/healthz"): ["/healthz"],
    ("GET", r"/(terms|privacy)"): ["/terms", "/privacy"],
    ("GET", r"/favicon\.ico"): ["/favicon.ico"],
    ("GET", r"/api/calendar/([^/]+?)\.ics"): ["/api/calendar/nope.ics"],
    ("GET", r"/p/" + server.TOKEN_LINK + r"(/pdf|/file/[A-Za-z0-9_-]{1,64})?"): ["/p/{company}.AAAAAAAAAAAAAAAAAAAAAAAA", "/p/{company}.AAAAAAAAAAAAAAAAAAAAAAAA/pdf"],
    ("GET", r"/s/" + server.TOKEN_LINK): ["/s/{company}.AAAAAAAAAAAAAAAAAAAAAAAA"],
    ("GET", r"/o/" + server.TOKEN_LINK + r"\.gif"): ["/o/{company}.AAAAAAAAAAAAAAAAAAAAAAAA.gif"],
    ("GET", r"/api/auth/me"): ["/api/auth/me"],
    ("GET", r"/api/auth/demo-available"): ["/api/auth/demo-available"],
    ("GET", r"/api/state"): ["/api/state"],
    ("GET", r"/api/jobs"): ["/api/jobs"],
    ("GET", r"/api/jobs/([^/]+)/final-preview"): ["/api/jobs/{job}/final-preview"],
    ("GET", r"/api/jobs/([^/]+)"): ["/api/jobs/{job}"],
    ("GET", r"/api/(outbox|audit)"): ["/api/outbox", "/api/audit"],
    ("GET", r"/api/timeline"): ["/api/timeline"],
    ("GET", r"/api/export"): ["/api/export"],
    ("GET", r"/api/export/with-files\.zip"): ["/api/export/with-files.zip"],
    ("GET", r"/api/team"): ["/api/team"],
    ("GET", r"/api/metrics"): ["/api/metrics"],
    ("GET", r"/api/financials\.csv"): ["/api/financials.csv"],
    ("GET", r"/api/financials"): ["/api/financials"],
    ("GET", r"/api/qbo/status"): ["/api/qbo/status"],
    ("GET", r"/api/billing"): ["/api/billing"],
    ("GET", r"/api/qbo/reconcile"): ["/api/qbo/reconcile"],
    ("GET", r"/api/qbo/callback"): ["/api/qbo/callback?code=x&state=y&realmId=1"],
    ("GET", r"/api/qbo/items"): ["/api/qbo/items"],
    ("GET", r"/api/qbo/customers"): ["/api/qbo/customers"],
    ("GET", r"/api/import/settings"): ["/api/import/settings"],
    ("GET", r"/api/storage"): ["/api/storage"],
    ("GET", r"/api/files/([^/]+)"): ["/api/files/{file}"],
    ("GET", r"/api/jobs/([^/]+)/payapp/([^/]+)\.pdf"): ["/api/jobs/{job}/payapp/{inv}.pdf"],
    ("GET", r"/api/jobs/([^/]+)/waiver-form/(interim|final)\.pdf"): ["/api/jobs/{job}/waiver-form/interim.pdf"],
    ("GET", r"/api/jobs/([^/]+)/payapp/([^/]+)\.csv"): ["/api/jobs/{job}/payapp/{inv}.csv"],
    ("GET", r"/api/jobs/([^/]+)/payapp/([^/]+)/page"): ["/api/jobs/{job}/payapp/{inv}/page"],
    ("GET", r"/api/jobs/([^/]+)/co/([^/]+)/page"): ["/api/jobs/{cojob}/co/{co}/page"],
    ("GET", r"/api/helper/review"): ["/api/helper/review"],
    ("GET", r"/api/admin/companies"): ["/api/admin/companies"],
    ("POST", r"/api/auth/login"): ["/api/auth/login"],
    ("POST", r"/api/auth/signup"): ["/api/auth/signup"],
    ("POST", r"/api/auth/demo"): ["/api/auth/demo"],
    ("POST", r"/api/auth/logout"): ["/api/auth/logout"],
    ("POST", r"/api/auth/forgot"): ["/api/auth/forgot"],
    ("POST", r"/api/auth/reset"): ["/api/auth/reset"],
    ("POST", r"/api/auth/verify"): ["/api/auth/verify"],
    ("POST", r"/api/auth/invite-info"): ["/api/auth/invite-info"],
    ("POST", r"/api/auth/accept-invite"): ["/api/auth/accept-invite"],
    ("POST", r"/api/inbound/email/([^/]+)"): ["/api/inbound/email/nope"],
    ("POST", r"/api/inbound/sms/([^/]+)"): ["/api/inbound/sms/nope"],
    ("POST", r"/api/mail-events/([^/]+)"): ["/api/mail-events/nope"],
    ("POST", r"/p/" + server.TOKEN_LINK + r"/received"): ["/p/{company}.AAAAAAAAAAAAAAAAAAAAAAAA/received"],
    ("POST", r"/p/" + server.TOKEN_LINK + r"/approve"): ["/p/{company}.AAAAAAAAAAAAAAAAAAAAAAAA/approve"],
    ("POST", r"/p/" + server.TOKEN_LINK + r"/question"): ["/p/{company}.AAAAAAAAAAAAAAAAAAAAAAAA/question"],
    ("POST", r"/s/" + server.TOKEN_LINK + r"/sign"): ["/s/{company}.AAAAAAAAAAAAAAAAAAAAAAAA/sign"],
    ("POST", r"/share"): ["/share"],
    ("POST", r"/api/stripe/webhook"): ["/api/stripe/webhook"],
    ("POST", r"/api/import/file"): ["/api/import/file?job={job}&file=1&kind=payroll"],
    ("POST", r"/api/helper/ask"): ["/api/helper/ask"],
    ("POST", r"/api/helper/feedback"): ["/api/helper/feedback"],
    ("POST", r"/api/helper/(answers|resolve)"): ["/api/helper/answers", "/api/helper/resolve"],
    ("POST", r"/api/auth/resend-verify"): ["/api/auth/resend-verify"],
    ("POST", r"/api/auth/password"): ["/api/auth/password"],
    ("POST", r"/api/auth/2fa/(begin|enable|disable)"): ["/api/auth/2fa/begin", "/api/auth/2fa/enable", "/api/auth/2fa/disable"],
    ("POST", r"/api/import/payments-csv"): ["/api/import/payments-csv"],
    ("POST", r"/api/import/quantities/preview"): ["/api/import/quantities/preview"],
    ("POST", r"/api/import/rotate"): ["/api/import/rotate"],
    ("POST", r"/api/qbo/connect"): ["/api/qbo/connect"],
    ("POST", r"/api/billing/checkout"): ["/api/billing/checkout"],
    ("POST", r"/api/billing/portal"): ["/api/billing/portal"],
    ("POST", r"/api/qbo/disconnect"): ["/api/qbo/disconnect"],
    ("POST", r"/api/qbo/payments"): ["/api/qbo/payments"],
    ("POST", r"/api/qbo/push"): ["/api/qbo/push"],
    ("POST", r"/api/qbo/settings"): ["/api/qbo/settings"],
    ("POST", r"/api/qbo/link"): ["/api/qbo/link"],
    ("POST", r"/api/qbo/sync"): ["/api/qbo/sync"],
    ("POST", r"/api/onboarding/dismiss"): ["/api/onboarding/dismiss"],
    ("POST", r"/api/team/invite"): ["/api/team/invite"],
    ("POST", r"/api/team/invites/([^/]+)/revoke"): ["/api/team/invites/{invite}/revoke"],
    ("POST", r"/api/team/reminders"): ["/api/team/reminders"],
    ("POST", r"/api/team/([^/]+)/role"): ["/api/team/{user}/role"],
    ("POST", r"/api/team/([^/]+)/remove"): ["/api/team/{user}/remove"],
    ("POST", r"/api/account/delete"): ["/api/account/delete"],
    ("POST", r"/api/sov/parse"): ["/api/sov/parse"],
    ("POST", r"/api/outbox/([^/]+)/retry"): ["/api/outbox/{email}/retry"],
    ("POST", r"/api/outbox/([^/]+)/resend"): ["/api/outbox/{email}/resend"],
    ("POST", r"/api/share/claim"): ["/api/share/claim"],
    ("POST", r"/api/reset"): ["/api/reset"],
    ("POST", r"/api/jobs"): ["/api/jobs"],
    ("POST", r"/api/jobs/([^/]+)/([a-z-]+)(?:/([^/]+))?"): ["/api/jobs/{job}/edit", "/api/jobs/{job}/delete", "/api/jobs/{job}/void-sent/{inv}"],
    ("POST", r"/api/payers/([^/]+)/suggestion/([^/]+)/(add|dismiss)"): ["/api/payers/{payer}/suggestion/x/dismiss"],
    ("POST", r"/api/payers"): ["/api/payers"],
    ("POST", r"/api/payers/([^/]+)/update"): ["/api/payers/{payer}/update"],
    ("POST", r"/api/payers/([^/]+)/missing"): ["/api/payers/{payer}/missing"],
    ("POST", r"/api/payers/([^/]+)/waiver-needed"): ["/api/payers/{payer}/waiver-needed"],
    ("POST", r"/api/payers/([^/]+)/merge"): ["/api/payers/{payer}/merge"],
    ("POST", r"/api/payers/([^/]+)/delete"): ["/api/payers/{payer}/delete"],
    ("POST", r"/api/import/payments-apply"): ["/api/import/payments-apply"],
    ("POST", r"/api/import/quantities/apply"): ["/api/import/quantities/apply"],
    ("POST", r"/api/import/text"): ["/api/import/text"],
    ("POST", r"/api/inbox/([^/]+)/([a-z-]+)"): ["/api/inbox/{inbox}/file", "/api/inbox/{inbox}/dismiss"],
    ("POST", r"/api/company"): ["/api/company"],
    ("POST", r"/api/approvers"): ["/api/approvers"],
    ("POST", r"/api/payers/([^/]+)/approvers"): ["/api/payers/{payer}/approvers"],
    ("POST", r"/api/auth/name"): ["/api/auth/name"],
    ("POST", r"/api/auth/sms"): ["/api/auth/sms"],
    ("POST", r"/api/auth/sms/confirm"): ["/api/auth/sms/confirm"],
    ("GET", r"/api/reminders"): ["/api/reminders"],
    ("POST", r"/api/reminders/prefs"): ["/api/reminders/prefs"],
    ("POST", r"/api/reminders/read"): ["/api/reminders/read"],
}

# Bodies that reach past the first "that's missing" check, so a cross-company request is tested
# against the other company's ids, not refused for an empty form.
BODIES = {
    "/api/tasks/assign": {"key": "job:{job}", "assignee_id": "{user}"},
    "/api/tasks/{task}": {"title": "Renamed by B"},
    "/api/tasks/{task}/done": {"done": True},
    "/api/payers/{payer}/client-forms/{gcf}/match": {"mapping": []},
    "/api/payers/{payer}/client-forms/send": {"send": "ours"},
    "/api/jobs/{job}/payapp/{inv}/attachments": {"action": "add_record", "record_id": "x"},
    "/api/compliance/docs/{doc}": {"type": "coi", "note": "B was here"},
    "/api/compliance/liens/{job}": {"notice_sent_on": "2026-11-01"},
    "/api/payers/{payer}/update": {"name": "Taken by B"},
    "/api/payers/{payer}/missing": {"field": "phone"},
    "/api/payers/{payer}/merge": {"into": "{payer}"},
    "/api/payers/{payer}/approvers": {"approver_ids": []},
    "/api/inbox/{inbox}/file": {"job_id": "{job}", "kind": "payroll"},
    "/api/team/{user}/role": {"profile_id": "owner"},
    "/api/team/invites/{invite}/revoke": {},
    "/api/team/reminders": {"user_ids": ["{user}"], "prefs": {"in_app": True}},
    "/api/jobs/{job}/edit": {"name": "Taken by B"},
    "/api/qbo/link": {"payer_id": "{payer}", "customer_id": "1"},
    "/api/qbo/push": {"job_id": "{job}", "invoice_id": "{inv}"},
    "/api/docusign/sign": {"job_id": "{job}", "invoice_id": "{inv}"},
    "/api/docusign/check": {"job_id": "{job}", "invoice_id": "{inv}"},
    "/api/outbox/{email}/resend": {"to": "b@example.com"},
}

# Routes a test's own session must not take while walking (they would sign it out, or end the
# company): walked separately or by their own tests.
SKIP_SIGNED_IN = {"/api/auth/logout", "/api/auth/sessions/end", "/api/account/delete", "/api/reset", "/api/team/admin",
                  "/api/auth/2fa/begin", "/api/auth/2fa/enable", "/api/auth/2fa/disable"}


def fill(text, ids):
    for key, value in ids.items():
        text = text.replace("{" + key + "}", value)
    return text


class RouteWalkTests(ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Company A: the electrical demo's data under a real company, with a payroll file, a
        # compliance document with a W-9, a requirement, a client's form, a task and an Inbox item.
        a = cls._signup_raw("walk-a@example.com", "Company A")
        cls.a_owner, cls.a = a
        auth.create_user("walk-a-field@example.com", PASSWORD, "Fay Field", cls.a, role="member")
        state, files = demo_seed.build(company_id=cls.a)
        state["company"]["name"] = "Company A " + MARK
        state["jobs"][0]["name"] = "Job " + MARK
        state["payers"][0]["name"] = "Payer " + MARK
        demo_seed.write_files(cls.a, files, state.get("files", []))
        # The Member profile holds nothing: what a route needs, the field crew doesn't have.
        for p in state["company"].get("profiles") or []:
            if p.get("id") == "member":
                p["perms"] = []
        state["company"].setdefault("profiles", profiles.default_profiles())
        for p in state["company"]["profiles"]:
            if p.get("id") == "member":
                p["perms"] = []
        job = next(j for j in state["jobs"] if any(i["status"] == "sent" for i in j["invoices"]))
        inv = next(i for i in job["invoices"] if i["status"] == "sent")
        co_job = next(j for j in state["jobs"] if j.get("change_orders"))
        meta = importer.store_bytes(cls.a, "wh-347.pdf", b"%PDF-1.4 " + MARK.encode(), "Ann")
        importer.register_file(state, meta)
        rec = domain.add_job_record(state, job["id"], "Payroll " + MARK, "payroll", "", "Ann")["records"][-1]
        rec["file_ids"] = [meta["id"]]
        w9 = importer.store_bytes(cls.a, "w9.pdf", b"%PDF-1.4 W9 " + MARK.encode(), "Ann")
        importer.register_file(state, w9)
        doc = compliance.add_doc(state, {"type": "w9", "payer_id": state["payers"][0]["id"]}, "Ann")
        doc["file_id"] = w9["id"]
        req = compliance.add_requirement(state, {"type": "coi", "payer_id": state["payers"][0]["id"]}, "Ann")
        gmeta = importer.store_bytes(cls.a, "form.pdf", forms.fillable_stream(), "Ann")
        gcforms.add_template(state, state["payers"][0]["id"], "payapp", gmeta, gcforms.read_upload(forms.fillable_stream()), "Ann")
        gcf = gcforms.templates(state, state["payers"][0]["id"])[0]["id"]
        task = domain.add_task(state, {"title": "Task " + MARK}, None, "Ann")
        item = importer.add_item(state, "paste", text="Inbox " + MARK, subject="s")
        with server.company_lock(cls.a):
            server.save_company(cls.a, state)
        member = auth.user_by_email("walk-a-field@example.com")
        cls.a_member = "duracash_session=" + auth.create_session(member)
        cls.ids = {"company": cls.a, "job": job["id"], "inv": inv["id"], "cojob": co_job["id"], "co": co_job["change_orders"][0]["id"],
                   "payer": state["payers"][0]["id"], "file": meta["id"], "doc": doc["id"], "req": req["id"], "gcf": gcf,
                   "task": task["id"], "email": state["email_log"][0]["id"], "inbox": item["id"], "user": member["id"],
                   "invite": "inv-nope"}
        cls.b_owner, cls.b = cls._signup_raw("walk-b@example.com", "Company B")

    @classmethod
    def _signup_raw(cls, email, company):
        user = auth.create_user(email, PASSWORD, "Ann Owner", server.new_company_id(), role="owner")
        with server.company_lock(user["company_id"]):
            server.save_company(user["company_id"], server.empty_state(company))
        return "duracash_session=" + auth.create_session(user), user["company_id"]

    def a_digest(self):
        with server.company_lock(self.a):
            state = server.load_company(self.a)
        return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()

    def walk(self):
        """(route, method, path, body) for every route's examples, with company A's ids."""
        missing = [(r.method, r.pattern) for r in server.ROUTES if (r.method, r.pattern) not in EXAMPLES]
        self.assertEqual(missing, [], "every route needs an example path in EXAMPLES")
        for route in server.ROUTES:
            for template in EXAMPLES[(route.method, route.pattern)]:
                path = fill(template, self.ids)
                self.assertTrue(route.regex.fullmatch(path.split("?")[0]), (template, route.pattern))
                body = json.loads(fill(json.dumps(BODIES.get(template, {})), self.ids)) if route.method == "POST" else None
                yield route, template, path, body

    def leaks(self, payload):
        text = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode()
        return MARK.encode() in text

    def test_signed_out_every_signed_in_route_is_401_and_nothing_leaks(self):
        before = self.a_digest()
        for route, template, path, body in self.walk():
            res, data = self.request(route.method, path, body)
            with self.subTest(path=path):
                if route.access != "public":
                    self.assertEqual(res.status, 401, (path, data))
                self.assertFalse(self.leaks(data), path)
        self.assertEqual(self.a_digest(), before, "nothing signed out changed company A")

    def test_another_company_gets_nothing_of_company_a(self):
        before = self.a_digest()
        for route, template, path, body in self.walk():
            if template.split("?")[0] in SKIP_SIGNED_IN:
                continue
            res, data = self.request(route.method, path, body, cookie=self.b_owner)
            with self.subTest(path=path):
                self.assertLess(res.status, 500, (path, data))
                self.assertFalse(self.leaks(data), (path, res.status))
                if "{" in template and route.method == "GET" and route.access != "public":
                    # A's ids name nothing in B: a download or a page of A's is never served.
                    self.assertNotEqual(res.status, 200, (path, data)) if any(k in template for k in ("{file}", "{doc}", "{gcf}", "{inv}", "{co}")) else None
        self.assertEqual(self.a_digest(), before, "company B changed nothing of company A")
        res, _ = self.request("GET", "/api/state", cookie=self.b_owner)
        self.assertEqual(res.status, 200, "B is still signed in after the walk")

    def test_without_the_permission_every_gated_route_is_403(self):
        before = self.a_digest()
        for route, template, path, body in self.walk():
            if not (route.perm or route.owner) or route.access == "public" or template.split("?")[0] in SKIP_SIGNED_IN:
                continue
            headers = {"Content-Type": "application/pdf", "X-Filename": quote("x.pdf")} if route.access == "user" and route.method == "POST" else None
            res, data = self.request(route.method, path, body, cookie=self.a_member, headers=headers,
                                     raw=b"%PDF-1.4 x" if headers else None)
            with self.subTest(path=path):
                self.assertEqual(res.status, 403, (path, data))
        # Job actions: each one that takes a permission is refused to the member.
        for action, perm in profiles.JOB_ACTION_PERMS.items():
            if not perm:
                continue
            tail = f"/{self.ids['inv']}" if server.JOB_ACTIONS[action][0] else ""
            res, data = self.request("POST", f"/api/jobs/{self.ids['job']}/{action}{tail}", {}, cookie=self.a_member)
            with self.subTest(action=action):
                self.assertEqual(res.status, 403, (action, data))
        self.assertEqual(self.a_digest(), before, "the member changed nothing they may not")

    def test_the_read_only_demo_changes_nothing_by_any_route(self):
        demo = auth.DEMO_COMPANY_ID
        with server.company_lock(demo):
            state = server.load_company(demo)
            # A sent pay app's customer link and a change order's signing link, as an email would carry.
            job = next(j for j in state["jobs"] if any(i["status"] == "sent" for i in j["invoices"]))
            inv = next(i for i in job["invoices"] if i["status"] == "sent")
            inv["payer_token"] = inv.get("payer_token") or "P" * 32
            inv["payer_link_at"] = co_link_at = server.datetime.now(server.timezone.utc).isoformat(timespec="seconds")
            co_job = next(j for j in state["jobs"] if j.get("change_orders"))
            co = co_job["change_orders"][0]
            co["sign_token"] = co.get("sign_token") or "S" * 32
            co["sign_link_at"] = co_link_at
            server.save_company(demo, state)
        ids = {**self.ids, "company": demo, "job": job["id"], "inv": inv["id"], "cojob": co_job["id"], "co": co["id"],
               "payer": state["payers"][0]["id"], "file": (state.get("files") or [{"id": "file-x"}])[0]["id"],
               "email": (state.get("email_log") or [{"id": "em-x"}])[0]["id"], "user": "user-x"}
        sign = co["sign_token"]
        cookie = "duracash_session=" + auth.create_session(auth.user_by_email(auth.DEMO_EMAIL))

        def digest():
            with server.company_lock(demo):
                st = server.load_company(demo)
            return hashlib.sha256(json.dumps(st, sort_keys=True, default=str).encode()).hexdigest()

        with mock.patch.dict(os.environ, {"DURACASH_DEMO": "readonly"}):
            before = digest()
            saved, self.ids = self.ids, ids
            try:
                for route, template, path, body in self.walk():
                    if template.split("?")[0] in {"/api/auth/logout", "/api/auth/sessions/end"}:
                        continue
                    path = path.replace(".AAAAAAAAAAAAAAAAAAAAAAAA", "." + (sign if path.startswith("/s/") else inv["payer_token"]))
                    form = route.access == "public" and route.method == "POST" and path.startswith(("/p/", "/s/"))
                    res, data = self.request(route.method, path, None if form else body, cookie=cookie,
                                             raw=b"name=Visitor&agree=yes&expected_on=2026-12-01&question=hi" if form else None,
                                             headers={"Content-Type": "application/x-www-form-urlencoded"} if form else None)
                    with self.subTest(path=path):
                        self.assertLess(res.status, 500, (path, data))
                        if route.method == "POST" and route.access != "public" and route.handler not in server.DEMO_READ_ONLY_OK:
                            self.assertEqual(res.status, 403, (path, data))
            finally:
                self.ids = saved
            self.assertEqual(digest(), before, "nothing in the read-only demo changed")

    def test_a_client_page_that_carries_a_waiver_isnt_a_way_round_the_permission(self):
        """The client's page (/p/) serves the files sent with the pay app, signed waivers and
        certified payroll among them; someone who may not open those can't reach it either."""
        import re
        from datetime import datetime, timezone
        import payer_page
        with server.company_lock(self.a):
            state = server.load_company(self.a)
            for job in state["jobs"]:
                for inv in job["invoices"]:
                    if inv.get("payer_token"):
                        inv["payer_link_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            server.save_company(self.a, state)
        sensitive = profiles.sensitive_file_ids(state)
        carrying = [(j, i) for j in state["jobs"] for i in j["invoices"]
                    if i.get("payer_token") and set(payer_page.file_ids(state, j, i)) & sensitive]
        self.assertTrue(carrying, "the demo's pay apps carry signed waivers")
        job, inv = carrying[0]
        for path in ("/api/outbox?limit=200", "/api/state"):
            res, out = self.request("GET", path, cookie=self.a_member)
            self.assertEqual(res.status, 200)
            self.assertEqual(re.findall(r"/p/co-[0-9a-f]+\.[A-Za-z0-9_-]{20,}", json.dumps(out)), [], path)
        res, out = self.request("GET", f"/api/jobs/{job['id']}/payapp/{inv['id']}/page", cookie=self.a_member)
        self.assertEqual(res.status, 403, out)
        # The owner still opens it, and still reads the emails as they went.
        res, _ = self.request("GET", f"/api/jobs/{job['id']}/payapp/{inv['id']}/page", cookie=self.a_owner)
        self.assertEqual(res.status, 303)
        res, out = self.request("GET", "/api/outbox?limit=200", cookie=self.a_owner)
        self.assertTrue(re.findall(r"/p/co-[0-9a-f]+\.[A-Za-z0-9_-]{20,}", json.dumps(out)))

    def test_without_the_permission_downloads_of_sensitive_files_are_403(self):
        for path in (f"/api/files/{self.ids['file']}", f"/api/compliance/docs/{self.ids['doc']}/file",
                     f"/api/jobs/{self.ids['job']}/archive/job-file.zip"):
            res, data = self.request("GET", path, cookie=self.a_member)
            self.assertEqual(res.status, 403, path)
            self.assertFalse(self.leaks(data), path)
            res, data = self.request("GET", path, cookie=self.a_owner)
            self.assertEqual(res.status, 200, path)
        res, data = self.request("GET", "/api/export/with-files.zip", cookie=self.a_member)
        self.assertIn(res.status, (200, 403))
        self.assertFalse(self.leaks(data), "the export with files leaves payroll out for the member")


if __name__ == "__main__":
    unittest.main()
