"""Every pay app is signed before it goes (Stephen, 18 Nov 2026: "Unapproved pay applications
should never send"). Tests of other rules send the way a person does: each approver still
waiting signs, then Approve & send. Not a test module; the tests import it."""

import json

import domain

TEST_OWNER = {"id": "owner", "name": "owner"}


def sign_all(state, job_id):
    """Each approver of the open draft who hasn't signed signs it. A state built without the
    server knows no owners; the test owner stands in, as the server's would. A draft that can't
    be signed yet (missing paperwork, nobody to sign) is left for the send to refuse."""
    if not state.get("team_owners"):
        domain.set_team_owners(state, [TEST_OWNER])
    job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
    draft = next((i for i in reversed((job or {}).get("invoices", [])) if i["status"] in domain.DRAFT_STATUSES), None)
    if not draft or (draft["status"] != "awaiting_approval" and domain.blocker_lines(job)):
        return
    for person in domain.payapp_approvers(state, job):
        if person["name"] in domain.approvals_missing(state, job, draft):
            domain.approve_payapp(state, job_id, person["name"], person["id"], signature=person["name"] or "Test Owner")


def approve_and_send(state, job_id, *args, **kwargs):
    """domain.approve_and_send, after every approver has signed."""
    sign_all(state, job_id)
    return domain.approve_and_send(state, job_id, *args, **kwargs)


def sign_http(case, job_id, cookie=None):
    """The signed-in person signs the job's open draft on Review & send, when they are one of
    those it waits on. Returns the reply, or None when there was nothing for them to sign."""
    res = case.request("GET", f"/api/jobs/{job_id}", cookie=cookie)
    raw = res[1]
    data = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
    for inv in (data.get("job") or {}).get("invoices", []) if isinstance(data, dict) else []:
        approval = inv.get("approval") or {}
        if inv.get("status") == "awaiting_approval" and approval.get("waiting"):
            body = {"invoice_id": inv["id"], "stamp": approval.get("stamp", ""), "signature": "Test Signer"}
            return case.request("POST", f"/api/jobs/{job_id}/payapp-approve", body, cookie=cookie)
    return None
