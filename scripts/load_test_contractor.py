#!/usr/bin/env python3
"""Load the Brightwater Tile & Stone test contractor into a running Duracash server.

Stdlib only. Everything goes through the same HTTP API the browser uses:

    python3 scripts/load_test_contractor.py                      # http://127.0.0.1:8765
    python3 scripts/load_test_contractor.py --base http://127.0.0.1:8791
    python3 scripts/load_test_contractor.py --only-setup         # stop before the imports

What it does:
  1. Signs up a new company (POST /api/auth/signup) with a fresh test email and a generated
     password. Both are printed and written to testdata/test-contractor/.login (git-ignored).
  2. Enters the company profile (POST /api/company), 9 jobs (POST /api/jobs, which also creates
     the 6 payers), the payer playbooks (POST /api/payers/<id>/update), three schedules of values
     (pasted through POST /api/sov/parse, saved with POST /api/jobs/<id>/sov), progress, pay apps,
     sends, payments, a waiver and a closed job, so every stage in company.md exists.
  3. Unless --only-setup: runs the imports in testdata/test-contractor/import (files, pasted
     email, text thread, field notes, inbound email webhook, QuickBooks payments CSV) and the
     deliberately bad files in testdata/test-contractor/bad, and checks each result against
     what the parsers should do.
  4. Prints a summary of what loaded, what failed, and any result that differs from expected.

Exit code 0 when everything matched, 1 otherwise. Fictional data only (see company.md).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
TD = ROOT / "testdata" / "test-contractor"
IMPORT = TD / "import"
BAD = TD / "bad"

COMPANY = {
    "name": "Brightwater Tile & Stone LLC",
    "address": "2480 Cobb Industrial Ct, Smyrna, GA 30080",
    "phone": "404-555-0110",
    "license": "Cobb County BL-2026-04417",
    "billing_style": "pay_apps",
}
OWNER_NAME = "Nadia Brooks"

# Jobs in creation order. The first job for a customer creates that payer.
# key = the job code Duracash makes from the name (first 4 letters, upper case).
JOBS = [
    {"key": "PEAC", "name": "Peachtree Hills Condo Baths", "address": "418 Lindbergh Way NE, Atlanta, GA 30324",
     "customer": "Tallow Creek Builders", "approver": "Renee Calloway", "ap_email": "ap@tallowcreekbuilders.example",
     "phone": "404-555-0141", "role": "GC", "contract_number": "TCB-1092", "contract_cents": 3_850_000, "retainage_pct": 0},
    {"key": "MILL", "name": "Mill Creek Clubhouse", "address": "2150 Mill Creek Pkwy, Marietta, GA 30062",
     "customer": "Tallow Creek Builders", "approver": "Renee Calloway", "ap_email": "ap@tallowcreekbuilders.example",
     "phone": "404-555-0141", "role": "GC", "contract_number": "TCB-1147", "contract_cents": 8_030_000, "retainage_pct": 5},
    {"key": "VEST", "name": "Vesta Senior Living Phase 2", "address": "3300 Chamblee Dunwoody Rd, Chamblee, GA 30341",
     "customer": "Harmon & Vale Construction", "approver": "Dwight Easley", "ap_email": "payables@harmonvale.example",
     "phone": "404-555-0152", "role": "GC", "contract_number": "HV-24-318", "contract_cents": 12_860_000, "retainage_pct": 10},
    {"key": "BROO", "name": "Brookhaven Townhomes Bldg C", "address": "1475 Dresden Dr NE, Brookhaven, GA 30319",
     "customer": "Harmon & Vale Construction", "approver": "Dwight Easley", "ap_email": "payables@harmonvale.example",
     "phone": "404-555-0152", "role": "GC", "contract_number": "HV-24-355", "contract_cents": 4_606_800, "retainage_pct": 10},
    {"key": "ASHB", "name": "Ashby Lofts Amenity Deck", "address": "620 Ashby Lofts Way NW, Atlanta, GA 30314",
     "customer": "Corbel Street Properties", "approver": "Alana Grant", "ap_email": "invoices@corbelstreet.example",
     "phone": "404-555-0163", "role": "Owner", "contract_number": "CSP-PO-7781", "contract_cents": 7_485_000, "retainage_pct": 0},
    {"key": "GLEN", "name": "Glenwood Retail Shell", "address": "1020 Glenwood Ave SE, Atlanta, GA 30316",
     "customer": "Ironleaf General Contractors", "approver": "Kevin Strand", "ap_email": "ap@ironleafgc.example",
     "phone": "404-555-0174", "role": "GC", "contract_number": "IGC-5530", "contract_cents": 5_230_000, "retainage_pct": 0},
    {"key": "DECA", "name": "Decatur Library Annex", "address": "215 Sycamore St, Decatur, GA 30030",
     "customer": "Wrenfield Construction Group", "approver": "Joan Pettis", "ap_email": "ap@wrenfieldcg.example",
     "phone": "404-555-0185", "role": "GC", "contract_number": "WCG-SUB-2240", "contract_cents": 6_400_000, "retainage_pct": 10},
    {"key": "KENN", "name": "Kennesaw Ortho Clinic", "address": "3895 Cherokee St NW, Kennesaw, GA 30144",
     "customer": "Wrenfield Construction Group", "approver": "Joan Pettis", "ap_email": "ap@wrenfieldcg.example",
     "phone": "404-555-0185", "role": "GC", "contract_number": "WCG-SUB-2291", "contract_cents": 9_240_000, "retainage_pct": 10},
    {"key": "WHIT", "name": "Whitfield Kitchen and Baths", "address": "77 Willow Bend Ct, Tucker, GA 30084",
     "customer": "Tom & Priya Whitfield", "approver": "Priya Whitfield", "ap_email": "priya.whitfield@mailbox.example",
     "phone": "404-555-0196", "role": "Owner", "contract_number": "", "contract_cents": 1_682_500, "retainage_pct": 0},
]

PLAYBOOKS = {
    "Tallow Creek Builders": {
        "follow_up_days": 5, "usual_approval_days": 18, "payapp_due_day": 25,
        "packet": "AIA-style pay app, schedule of values, conditional lien waiver, insurance certificate",
        "rejects_when": "Missing insurance certificate", "copy": "pm@tallowcreekbuilders.example"},
    "Harmon & Vale Construction": {
        "follow_up_days": 7, "usual_approval_days": 45, "payapp_due_day": 20,
        "packet": "AIA-style pay app, schedule of values, conditional lien waiver, certified payroll",
        "rejects_when": "Missing certified payroll; pay app not notarized", "copy": ""},
    "Corbel Street Properties": {
        "follow_up_days": 10, "usual_approval_days": 30,
        "packet": "Pay app, conditional lien waiver, vendor portal upload confirmation",
        "rejects_when": "Not uploaded to the vendor portal", "copy": "alana.grant@corbelstreet.example"},
    "Ironleaf General Contractors": {
        "follow_up_days": 7, "usual_approval_days": 30,
        "packet": "AIA-style pay app, conditional lien waiver, cleanup log",
        "rejects_when": "Cleanup not documented (they backcharge cleanup)", "copy": ""},
    "Wrenfield Construction Group": {
        "follow_up_days": 10, "usual_approval_days": 30, "payapp_due_day": 25,
        "packet": "AIA-style pay app, conditional lien waiver, insurance certificate, punch-list signoff",
        "rejects_when": "No insurance certificate on file", "copy": ""},
    "Tom & Priya Whitfield": {"follow_up_days": 3, "usual_approval_days": 7},
}

SOV_FILES = {
    "MILL": "02_sov_MILL_excel-tab-with-header.txt",
    "ASHB": "03_sov_ASHB_pdf-copy-spaces.txt",
    "BROO": "04_sov_BROO_typed-commas-headerless.txt",
}

ON_FILE = "On file in the office (test data loader)"

# What each import should produce: (job code the Inbox should suggest or None, kind).
EXPECTED_UPLOADS = {
    "05_email_co-request-from-gc-super.eml": ("BROO", "change_order"),
    "06_email_signed-waiver-pdf-attached.eml": ("DECA", "waiver"),
    "07_email_payer-says-backup-missing.eml": ("VEST", "pay_app"),
    "08_email_photo-attachment-from-foreman.eml": ("MILL", "note"),
    "09_email_matches-no-job.eml": (None, "note"),
    "10_email_portal-notice-html-only.eml": ("ASHB", "pay_app"),
    "13 Subcontract KENN Kennesaw Ortho Clinic.pdf": ("KENN", "contract"),
    "14 photo MILL lobby floor IMG_2231.jpg": ("MILL", "photo"),
}
EXPECTED_NOTES = [  # (note_date, job code)
    (None, None), ("2026-10-05", "MILL"), ("2026-10-06", "KENN"), ("2026-10-08", "GLEN"),
    ("2026-10-09", "ASHB"), ("2026-10-12", "ASHB"), ("2026-10-14", "BROO"), ("2026-10-19", "VEST"),
]
EXPECTED_CSV = {  # reference -> (status, job code)
    "ACH-30211": ("match", "ASHB"),
    "4471": ("match", "GLEN"),
    "9902": ("too much", None),
    "CHK 40871": ("already recorded", None),
    "1187": ("match", "WHIT"),
    "2210": ("no match", None),
}


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cookie = ""

    def call(self, method: str, path: str, body: Any = None, raw: Optional[bytes] = None,
             headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
        h = dict(headers or {})
        data = None
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        elif method == "POST":
            data = b""
        if self.cookie:
            h["Cookie"] = self.cookie
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, payload, set_cookie = resp.status, resp.read(), resp.headers.get("Set-Cookie")
        except urllib.error.HTTPError as exc:
            status, payload, set_cookie = exc.code, exc.read(), exc.headers.get("Set-Cookie")
        except urllib.error.URLError as exc:
            sys.exit(f"Can't reach {self.base}: {exc.reason}. Start the server first (python3 server.py).")
        if set_cookie and "duracash_session=" in set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        try:
            parsed = json.loads(payload.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {"raw": payload[:300].decode("utf-8", "replace")}
        return status, parsed if isinstance(parsed, dict) else {"value": parsed}


class Run:
    def __init__(self, api: Api):
        self.api = api
        self.results: List[Tuple[str, bool, str]] = []
        self.jobs: Dict[str, str] = {}  # code -> job id

    def record(self, step: str, ok: bool, detail: str = "") -> bool:
        self.results.append((step, ok, detail))
        print(f"  [{'ok' if ok else 'FAIL'}] {step}{': ' + detail if detail else ''}")
        return ok

    def post(self, step: str, path: str, body: Any = None, expect: int = 200, quiet: bool = False) -> Dict[str, Any]:
        status, payload = self.api.call("POST", path, body if body is not None else {})
        ok = status == expect
        if not ok or not quiet:
            detail = "" if ok and expect == 200 else f"HTTP {status} {payload.get('error', '')} {payload.get('blockers', '') if status != 200 else ''}".strip()
            self.record(step, ok, detail)
        return payload

    def get(self, path: str) -> Dict[str, Any]:
        status, payload = self.api.call("GET", path)
        if status != 200:
            self.record(f"GET {path}", False, f"HTTP {status} {payload.get('error', '')}")
        return payload

    def job(self, code: str) -> Dict[str, Any]:
        return self.get(f"/api/jobs/{self.jobs[code]}").get("job", {})

    def state(self) -> Dict[str, Any]:
        return self.get("/api/state")

    def code_of(self, job_id: Optional[str]) -> Optional[str]:
        return next((c for c, j in self.jobs.items() if j == job_id), None) if job_id else None

    # ----- money steps -----

    def clear_packet(self, code: str) -> None:
        job = self.job(code)
        open_inv = next((i for i in reversed(job.get("invoices", [])) if i["status"] in ("draft", "awaiting_approval")), None)
        packet = (open_inv or {}).get("packet") or job.get("packet") or []
        for item in packet:
            if item.get("required") and not item.get("present"):
                self.post(f"{code}: mark '{item['label']}' present", f"/api/jobs/{self.jobs[code]}/resolve/{item['id']}",
                          {"reason": ON_FILE}, quiet=True)

    def sov_lines_by_desc(self, code: str) -> Dict[str, str]:
        return {l["description"]: l["id"] for l in (self.job(code).get("sov") or {}).get("lines", [])}


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def signup(run: Run, email: Optional[str]) -> Tuple[str, str]:
    email = email or f"nadia.brooks+{secrets.token_hex(3)}@brightwatertile.example"
    password = secrets.token_urlsafe(12)  # 16 characters
    status, payload = run.api.call("POST", "/api/auth/signup", {
        "name": OWNER_NAME, "company_name": COMPANY["name"], "email": email, "password": password, "accept_terms": True})
    if status != 200:
        sys.exit(f"Sign-up failed: HTTP {status} {payload.get('error')}")
    company_id = payload.get("user", {}).get("company_id", "")
    login = TD / ".login"
    login.write_text(
        f"# Brightwater Tile & Stone test login (generated by scripts/load_test_contractor.py; git-ignored)\n"
        f"base={run.api.base}\nemail={email}\npassword={password}\ncompany_id={company_id}\n")
    print(f"\nSigned up {COMPANY['name']} ({company_id})\n  email:    {email}\n  password: {password}\n  saved to: {login}\n")
    run.record("Sign up", True, f"{email} / company {company_id}")
    return email, password


def setup(run: Run) -> None:
    print("Company profile, jobs and payers")
    run.post("Company profile (POST /api/company)", "/api/company", COMPANY)
    for spec in JOBS:
        body = {k: v for k, v in spec.items() if k != "key"}
        body["note"] = ""
        payload = run.post(f"Create job {spec['key']} {spec['name']}", "/api/jobs", body)
        made = next((j for j in payload.get("jobs", []) if j["name"] == spec["name"]), None)
        if made:
            run.jobs[spec["key"]] = made["id"]
            if made["code"] != spec["key"]:
                run.record(f"Job code for {spec['name']}", False, f"got {made['code']}, expected {spec['key']}")
    if len(run.jobs) != len(JOBS):
        sys.exit("Not every job was created; stopping.")
    payers = {p["name"]: p["id"] for p in run.state().get("payers", [])}
    for name, playbook in PLAYBOOKS.items():
        if name not in payers:
            run.record(f"Payer {name}", False, "not created by its first job")
            continue
        run.post(f"Playbook: {name}", f"/api/payers/{payers[name]}/update", playbook)

    print("\nSchedules of values (pasted rows read by /api/sov/parse, then saved)")
    for code, filename in SOV_FILES.items():
        parsed = run.post(f"{code}: read pasted rows from {filename}", "/api/sov/parse", {"text": (IMPORT / filename).read_text()}, quiet=True)
        lines = parsed.get("lines", [])
        run.record(f"{code}: SOV paste parsed", bool(lines), f"{len(lines)} lines, O&P {parsed.get('op_pct') or 0}%, {len(parsed.get('warnings', []))} warnings")
        run.post(f"{code}: save schedule of values", f"/api/jobs/{run.jobs[code]}/sov",
                 {"lines": lines, "op_pct": parsed.get("op_pct", ""), "update_contract": False})

    print("\nStages: progress, pay apps, sends, payments, waiver, close")
    j = run.jobs
    # PEAC: finished, paid, waiver sent, closed.
    run.post("PEAC: mark done (drafts the final pay app)", f"/api/jobs/{j['PEAC']}/done", {"notes": "All four baths set, grouted and sealed."})
    run.clear_packet("PEAC")
    run.post("PEAC: approve & send PA-PEAC-01", f"/api/jobs/{j['PEAC']}/send")
    run.post("PEAC: match payment ACH 7781", f"/api/jobs/{j['PEAC']}/match-payment", {"amount_cents": 3_850_000, "reference": "ACH 7781"})
    run.post("PEAC: send waiver", f"/api/jobs/{j['PEAC']}/waiver")
    run.post("PEAC: close for record", f"/api/jobs/{j['PEAC']}/close")
    # DECA: finished, paid less retainage, waiver sent, retainage held.
    run.post("DECA: mark done", f"/api/jobs/{j['DECA']}/done", {"notes": "Substantially complete; punch walk pending."})
    run.clear_packet("DECA")
    run.post("DECA: approve & send PA-DECA-01", f"/api/jobs/{j['DECA']}/send")
    run.post("DECA: match payment CHK 40871", f"/api/jobs/{j['DECA']}/match-payment", {"amount_cents": 5_760_000, "reference": "CHK 40871"})
    run.post("DECA: send waiver", f"/api/jobs/{j['DECA']}/waiver", {"kind": "unconditional_progress"})
    # VEST, GLEN, WHIT: progress in dollars, pay app sent.
    for code, cents, note in (("VEST", 4_200_000, "Corridors and rooms 101-118 set."),
                              ("GLEN", 3_000_000, "Storefront entry and restrooms set."),
                              ("WHIT", 841_250, "Kitchen backsplash done; baths half done.")):
        run.post(f"{code}: progress {money(cents)} to date (drafts a pay app)", f"/api/jobs/{j[code]}/progress", {"completed_cents": cents, "notes": note})
        run.clear_packet(code)
        run.post(f"{code}: approve & send", f"/api/jobs/{j[code]}/send")
    # ASHB: SOV progress, sent.
    ids = run.sov_lines_by_desc("ASHB")
    run.post("ASHB: SOV progress (pavers 1,400 sf, coping 340 lf)", f"/api/jobs/{j['ASHB']}/sov-progress",
             {"done": {ids.get("Travertine pool deck pavers", "?"): "1400", ids.get("Coping stone", "?"): "340"}, "notes": "Pavers half down; coping complete."})
    run.clear_packet("ASHB")
    run.post("ASHB: approve & send PA-ASHB-01", f"/api/jobs/{j['ASHB']}/send")
    # MILL: SOV progress with stored materials; draft left blocked (pay app due on the 25th).
    ids = run.sov_lines_by_desc("MILL")
    run.post("MILL: SOV progress with $4,000 stored wall tile (drafts PA-MILL-01)", f"/api/jobs/{j['MILL']}/sov-progress", {
        "done": {ids.get("Floor prep and self-leveler", "?"): "4200", ids.get("Porcelain floor tile 24x24", "?"): "1800",
                 ids.get("Shower pans and waterproofing", "?"): "2"},
        "stored": {ids.get("Restroom wall tile", "?"): "4000.00"}, "notes": "Lobby floor half set; wall tile delivered."})
    # BROO: change order pending.
    run.post("BROO: add CO 1 (pending)", f"/api/jobs/{j['BROO']}/add-co",
             {"title": "Upgrade to large-format porcelain, units C4-C6", "amount_cents": 685_000,
              "description": "Owner upgrade from 12x24 to 24x48 in three primary baths."})


def check_setup(run: Run) -> None:
    print("\nChecking the stages through GET /api/jobs/<id>")

    def inv(code: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        job = run.job(code)
        live = [i for i in job.get("invoices", []) if i["status"] != "void"]
        return job, (live[-1] if live else {})

    expect = {
        "PEAC": ("paid", 3_850_000), "DECA": ("paid", 5_760_000), "VEST": ("sent", 3_780_000),
        "GLEN": ("sent", 3_000_000), "WHIT": ("sent", 841_250), "ASHB": ("sent", 3_602_000), "MILL": ("draft", 3_304_290),
    }
    for code, (status, amount) in expect.items():
        job, i = inv(code)
        ok = i.get("status") == status and i.get("amount_cents") == amount
        run.record(f"{code}: {i.get('number', 'no pay app')} is {status} for {money(amount)}", ok,
                   "" if ok else f"got {i.get('status')} {money(i.get('amount_cents', 0))}")
    # Notes typed with the progress go on that pay app (R4-12), and a sent one downloads for QuickBooks (R4-11).
    vest, vest_inv = inv("VEST")
    run.record("VEST: the progress notes are on the pay app", vest_inv.get("progress_notes") == "Corridors and rooms 101-118 set.",
               repr(vest_inv.get("progress_notes")))
    req = urllib.request.Request(f"{run.api.base}/api/jobs/{vest.get('id')}/payapp/{vest_inv.get('id')}.csv", headers={"Cookie": run.api.cookie})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, text = resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status, text = exc.code, ""
    rows = list(csv.DictReader(io.StringIO(text))) if status == 200 else []
    total = sum(round(float(r.get("ItemAmount") or 0) * 100) for r in rows)
    run.record("VEST: Download for QuickBooks adds up to the pay app", status == 200 and total == vest_inv.get("amount_cents")
               and any(r.get("Item") == "Retainage" for r in rows), f"HTTP {status}, {len(rows)} lines, {money(total)}")
    peac = run.job("PEAC")
    run.record("PEAC: closed for the record", bool(peac.get("closed")))
    deca = run.job("DECA")
    run.record("DECA: retainage $6,400.00 held and open", deca.get("money", {}).get("retainage_open_cents") == 640_000,
               money(deca.get("money", {}).get("retainage_open_cents", 0)))
    mill = run.job("MILL")
    # The pay app and schedule of values are made by Duracash, so only the waiver and insurance wait (R1-08).
    run.record("MILL: draft waits on the waiver and insurance certificate only",
               mill.get("missing_paperwork") == ["Conditional lien waiver", "Insurance certificate"] and len(mill.get("blockers", [])) == 2,
               f"{len(mill.get('blockers', []))} blockers: {'; '.join(mill.get('blockers', []))}")
    broo = run.job("BROO")
    run.record("BROO: SOV $46,068.00 and CO 1 pending", broo.get("contract_cents") == 4_606_800
               and any(c["status"] == "pending" for c in broo.get("change_orders", [])))
    kenn = run.job("KENN")
    run.record("KENN: active, nothing billed yet", not kenn.get("invoices") and kenn.get("work_status") == "in_progress")
    st = run.state()
    run.record("6 payers in the playbook", len(st.get("payers", [])) == 6, ", ".join(p["name"] for p in st.get("payers", [])))


def upload(run: Run, path: Path, query: str = "") -> Tuple[int, Dict[str, Any]]:
    data = path.read_bytes()
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return run.api.call("POST", "/api/import/file" + query, raw=data,
                        headers={"Content-Type": ctype, "X-Filename": urllib.parse.quote(path.name)})


def new_items(before: set, state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [i for i in state.get("inbox", []) if i["id"] not in before]


def imports(run: Run) -> None:
    print("\nImports: files into the Inbox (POST /api/import/file)")
    for filename, (code, kind) in EXPECTED_UPLOADS.items():
        before = {i["id"] for i in run.state().get("inbox", [])}
        status, payload = upload(run, IMPORT / filename)
        items = new_items(before, payload) if status == 200 else []
        if not items:
            run.record(f"Upload {filename}", False, f"HTTP {status} {payload.get('error', '')}")
            continue
        it = items[0]
        got = (run.code_of(it.get("suggested_job_id")), it.get("kind"))
        run.record(f"Upload {filename}", got == (code, kind),
                   f"suggested {got[0] or 'no job'} ({it.get('match_reason')}), kind {got[1]}"
                   + ("" if got == (code, kind) else f"; expected {code or 'no job'}, {kind}"))

    print("\nImports: straight onto a job record (POST /api/import/file?job=...&file=1)")
    pdf = IMPORT / "13 Subcontract KENN Kennesaw Ortho Clinic.pdf"
    status, payload = upload(run, pdf, f"?job={run.jobs['KENN']}&file=1")
    recs = run.job("KENN").get("records", []) if status == 200 else []
    run.record("KENN job page: contract PDF filed as a Contract record", status == 200 and any(r.get("kind") == "contract" and r.get("file_ids") for r in recs),
               f"HTTP {status}, {len(recs)} records")

    print("\nImports: pasted text (POST /api/import/text)")
    before = {i["id"] for i in run.state().get("inbox", [])}
    payload = run.post("Paste text thread as Text messages", "/api/import/text",
                       {"text": (IMPORT / "11_text-thread_whitfield-done.txt").read_text(), "source": "sms", "job_id": ""}, quiet=True)
    items = new_items(before, payload)
    it = items[0] if items else {}
    run.record("Text thread -> WHIT, offers 'Mark done and draft'",
               run.code_of(it.get("suggested_job_id")) == "WHIT" and run.code_of(it.get("says_done_job_id")) == "WHIT",
               f"suggested {run.code_of(it.get('suggested_job_id'))}, says done {run.code_of(it.get('says_done_job_id'))}")

    before = {i["id"] for i in payload.get("inbox", [])}
    payload = run.post("Paste field notes as Notes", "/api/import/text",
                       {"text": (IMPORT / "12_field-notes_split-into-daily-records.txt").read_text(), "source": "notes"}, quiet=True)
    items = new_items(before, payload)
    got = [(i.get("note_date"), run.code_of(i.get("suggested_job_id"))) for i in items]
    run.record(f"Field notes split into {len(items)} daily-record items", got == EXPECTED_NOTES and all(i["kind"] == "daily" for i in items),
               "; ".join(f"{d or 'no date'}->{c or 'no job'}" for d, c in got))

    before = {i["id"] for i in payload.get("inbox", [])}
    payload = run.post("Paste an email (From:/Subject: headers)", "/api/import/text",
                       {"text": (IMPORT / "15_pasted-email_wrenfield-punch-list.txt").read_text(), "source": "email"}, quiet=True)
    items = new_items(before, payload)
    it = items[0] if items else {}
    run.record("Pasted email -> DECA, sender and subject read",
               run.code_of(it.get("suggested_job_id")) == "DECA" and "wrenfieldcg" in it.get("from", "") and it.get("subject", "").startswith("Decatur"),
               f"suggested {run.code_of(it.get('suggested_job_id'))}, from {it.get('from')!r}")

    print("\nImports: inbound email webhook (POST /api/inbound/email/<token>)")
    settings = run.get("/api/import/settings")
    path = urllib.parse.urlparse(settings.get("email_webhook", "")).path
    before = {i["id"] for i in run.state().get("inbox", [])}
    saved_cookie, run.api.cookie = run.api.cookie, ""  # the provider has no session
    status, _ = run.api.call("POST", path, raw=(IMPORT / "16_inbound-webhook_crew-says-glenwood-finished.eml").read_bytes(),
                             headers={"Content-Type": "message/rfc822"})
    run.api.cookie = saved_cookie
    items = new_items(before, run.state())
    it = items[0] if items else {}
    run.record("Inbound email -> GLEN, source email, offers 'Mark done and draft'",
               status == 200 and it.get("source") == "email" and run.code_of(it.get("suggested_job_id")) == "GLEN"
               and run.code_of(it.get("says_done_job_id")) == "GLEN",
               f"HTTP {status}, source {it.get('source')}, suggested {run.code_of(it.get('suggested_job_id'))}")

    print("\nImports: QuickBooks payments CSV (POST /api/import/payments-csv, then payments-apply)")
    csv_text = (IMPORT / "01_qbo-payments_partial-nomatch-odd-formats.csv").read_text()
    proposal = run.post("Read payments CSV", "/api/import/payments-csv", {"csv": csv_text}, quiet=True)
    rows = proposal.get("rows", [])
    for ref, (status_word, code) in EXPECTED_CSV.items():
        row = next((r for r in rows if r.get("ref") == ref), {})
        got = (row.get("status"), run.code_of(row.get("job_id")))
        run.record(f"CSV row {ref}: {status_word}{' ' + code if code else ''}", got == (status_word, code),
                   f"{row.get('status')} - {row.get('reason')}")
    warns = proposal.get("warnings", [])
    run.record("CSV warnings for the refund, the non-number row and the invoice", len(warns) == 3
               and any("an Invoice is not money received" in w for w in warns), " | ".join(warns))
    run.record("CSV invoice row is not proposed", not any(r.get("ref") == "1043" for r in rows), "")
    picks = [{"job_id": r["job_id"], "invoice_id": r["invoice_id"], "amount_cents": r["amount_cents"],
              "reference": r["ref"], "date": r.get("date") or ""} for r in rows if r.get("status") == "match"]
    applied = run.post("Record the ticked (matched) payments", "/api/import/payments-apply", {"payments": picks}, quiet=True)
    results = applied.get("import_results", [])
    run.record(f"{sum(1 for r in results if r.get('ok'))} of {len(picks)} payments recorded", bool(results) and all(r.get("ok") for r in results),
               "; ".join(f"{r['reference']}: {'ok' if r.get('ok') else r.get('error')}" for r in results))
    for code, status_word, collected in (("ASHB", "paid", 3_602_000), ("GLEN", "partial", 2_875_000), ("WHIT", "paid", 841_250)):
        job = run.job(code)
        i = next((x for x in job.get("invoices", []) if x["status"] != "void"), {})
        ok = i.get("status") == status_word and job.get("money", {}).get("collected_cents") == collected
        run.record(f"{code}: {i.get('number')} now {status_word}, collected {money(collected)}", ok,
                   f"{i.get('status')}, collected {money(job.get('money', {}).get('collected_cents', 0))}, outstanding {money(job.get('money', {}).get('outstanding_cents', 0))}")
    again = run.post("Read the same CSV again", "/api/import/payments-csv", {"csv": csv_text}, quiet=True)
    dup = [r["ref"] for r in again.get("rows", []) if r.get("status") == "already recorded"]
    run.record("Second read: recorded payments show as already recorded", {"ACH-30211", "4471", "1187", "CHK 40871"} <= set(dup), ", ".join(dup))


def bad_files(run: Run) -> None:
    print("\nDeliberately bad files (each should be refused or warned about)")
    status, payload = upload(run, BAD / "bad_01_page.html")
    run.record("bad_01_page.html refused", status == 409 and "can't be imported" in payload.get("error", ""), f"HTTP {status} {payload.get('error', '')}")
    proposal = run.post("bad_02 wrong-header CSV read", "/api/import/payments-csv", {"csv": (BAD / "bad_02_payments-wrong-headers.csv").read_text()}, quiet=True)
    run.record("bad_02_payments-wrong-headers.csv: no rows, header warning",
               not proposal.get("rows") and any("No header row" in w for w in proposal.get("warnings", [])), " | ".join(proposal.get("warnings", [])))
    status, payload = upload(run, BAD / "bad_03_empty-file.txt")
    run.record("bad_03_empty-file.txt refused", status == 409 and "is empty" in payload.get("error", ""), f"HTTP {status} {payload.get('error', '')}")
    parsed = run.post("bad_04 SOV read", "/api/sov/parse", {"text": (BAD / "bad_04_sov-quantity-without-rate.txt").read_text()}, quiet=True)
    run.record("bad_04_sov-quantity-without-rate.txt: no lines, a warning per row",
               not parsed.get("lines") and len(parsed.get("warnings", [])) == 3, " | ".join(parsed.get("warnings", [])))
    before = {i["id"] for i in run.state().get("inbox", [])}
    status, payload = upload(run, BAD / "bad_05_email-with-zip-attachment.eml")
    items = new_items(before, payload) if status == 200 else []
    it = items[0] if items else {}
    run.record("bad_05 email: imported, .zip attachment listed as skipped, PDF kept, ASHB suggested",
               bool(it.get("skipped_attachments")) and len(it.get("file_ids", [])) == 2 and run.code_of(it.get("suggested_job_id")) == "ASHB",
               "; ".join(it.get("skipped_attachments", [])) or f"HTTP {status} {payload.get('error', '')}")


def summary(run: Run) -> int:
    st = run.state()
    print("\n" + "=" * 72)
    print(f"{COMPANY['name']}: what is in Duracash now")
    for j in sorted(st.get("jobs", []), key=lambda x: x["code"]):
        m = j.get("money", {})
        invs = ", ".join(f"{i['number']} {i['status']} {money(i['amount_cents'])}" for i in j.get("invoices", []))
        print(f"  {j['code']:<5} {j['name']:<30} {j.get('status_label', ''):<38} {invs}")
        print(f"        contract {money(m.get('contract_total_cents', 0))}  billed {money(m.get('billed_cents', 0))}  "
              f"collected {money(m.get('collected_cents', 0))}  outstanding {money(m.get('outstanding_cents', 0))}  "
              f"retainage open {money(m.get('retainage_open_cents', 0))}")
    inbox = [i for i in st.get("inbox", []) if i.get("status") == "new"]
    print(f"  Payers: {len(st.get('payers', []))}   Inbox items waiting: {len(inbox)}   "
          f"Closed jobs: {st.get('closed_jobs_total', 0)}   Emails logged: {st.get('email_log_total', 0)}")
    failed = [r for r in run.results if not r[1]]
    print(f"\n{len(run.results) - len(failed)} of {len(run.results)} checks passed.")
    for step, _, detail in failed:
        print(f"  FAILED: {step}: {detail}")
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8765", help="Duracash server (default http://127.0.0.1:8765)")
    ap.add_argument("--only-setup", action="store_true", help="enter the company, payers and jobs, then stop before the imports")
    ap.add_argument("--email", help="sign-up email to use (default: a fresh nadia.brooks+xxxxxx@brightwatertile.example)")
    args = ap.parse_args()
    run = Run(Api(args.base))
    signup(run, args.email)
    setup(run)
    check_setup(run)
    if not args.only_setup:
        imports(run)
        bad_files(run)
    else:
        print("\n--only-setup: stopping before the imports. Import the files in testdata/test-contractor/import by hand (MANUAL-TEST.md).")
    sys.exit(summary(run))


if __name__ == "__main__":
    main()
