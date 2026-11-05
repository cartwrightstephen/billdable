"""What Review & send sends with Approve & send: the stamp of the pay app the page showed. Not a test
module; the tests import it."""

import json


def reviewed(case, job_id, cookie=None):
    """A POST /send body, as the page makes it from the job's open draft."""
    res = case.request("GET", f"/api/jobs/{job_id}", cookie=cookie)
    raw = res[1]
    data = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
    for inv in (data.get("job") or {}).get("invoices", []) if isinstance(data, dict) else []:
        if inv.get("status") in ("draft", "awaiting_approval"):
            return {"reviewed": (inv.get("email_preview") or {}).get("stamp", "")}
    return {}
