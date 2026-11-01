"""A signed lien waiver on a job's record, as the waiver page's Choose file leaves it: sending a
waiver needs one (M21). Not a test module; the tests import it."""

import secrets


def signed_waiver(state, job_id, kind=None, label="Signed lien waiver"):
    """Add a waiver record with one stored file to the job; `kind` is its type, if said."""
    job = next(j for j in state["jobs"] if j["id"] == job_id)
    file_id = "file-" + secrets.token_hex(6)
    state.setdefault("files", []).append({"id": file_id, "name": f"{label}.pdf", "ext": ".pdf", "size": 10})
    rec = {"id": "rec-" + secrets.token_hex(3), "kind": "waiver", "label": label, "note": "", "file_ids": [file_id],
           "added_at": "2026-10-01T12:00:00+00:00", "added_by": "owner"}
    if kind:
        rec["waiver_kind"] = kind
    job.setdefault("records", []).append(rec)
    return rec
