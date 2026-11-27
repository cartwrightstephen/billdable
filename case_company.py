"""The case company (Stephen, Nov 24): a made-up mechanical subcontractor with a team, three
clients, three jobs and three weeks of daily job history (Mon 2 Nov to Fri 20 Nov 2026), for
testers to type into Duracash by hand, in order, and check against the figures.

CASE is the one copy of the data. tests/test_case_company.py types all of it in through the same
routes the pages use and checks every figure in EXPECTED, so the packet (docs/case-company.md,
written from CASE and EXPECTED by `python3 case_company.py`) can't describe something the app
doesn't do. Everything here is made up: names, addresses, 555-01xx phone numbers, example.com.

Dates: a job record, a change order's "Date signed" and a job's start date take the date typed.
Sending a pay app, signing it, recording a payment and sending a waiver happen on the day they are
entered, so the figures to check are dollars, which don't depend on the day."""

from __future__ import annotations

import sys
from pathlib import Path

# ---------- the company, the team, the clients ----------

COMPANY = {
    "name": "Sweetgum Ridge Mechanical",
    "legal_name": "Sweetgum Ridge Mechanical, LLC",
    "phone": "(404) 555-0163",
    "street": "2207 Lawrenceville Hwy, Unit 4",
    "city": "Decatur", "state": "GA", "zip": "30033",
    "license": "MP-210468",
    "owner": "Dana Whitfield",
}

# Permission keys as the Team page shows them (domain/profiles.py).
PROFILES = [
    {"name": "Office", "perms": ["send_payapps", "send_waivers", "ask_retainage", "record_payments", "financials", "company", "sensitive_docs"],
     "why": "Rosa runs billing: sends pay apps and waivers, records payments, keeps client settings, opens payroll and waivers."},
    {"name": "Project manager", "perms": ["manage_jobs", "approve_payapps", "close_jobs"],
     "why": "Marcus runs the jobs: change orders, schedule changes, signs off pay apps, finishes jobs."},
    {"name": "Field", "perms": [],
     "why": "The crew enter job records only. Anyone on the team can do that."},
]

TEAM = [
    {"name": "Dana Whitfield", "title": "Owner", "profile": "Owner", "plus": "dana", "note": "You. Sign up as Dana."},
    {"name": "Rosa Delgado", "title": "Office manager and bookkeeper", "profile": "Office", "plus": "rosa"},
    {"name": "Marcus Bell", "title": "Project manager", "profile": "Project manager", "plus": "marcus"},
    {"name": "Terrell Hughes", "title": "Field foreman (HVAC)", "profile": "Field", "plus": "terrell"},
    {"name": "Kenji Park", "title": "Plumber", "profile": "Field", "plus": "kenji"},
]

CLIENTS = [
    {"key": "ironbridge", "name": "Ironbridge Builders", "client_type": "gc", "type_text": "General contractor",
     "approver": "Paul Kessler", "plus": "ironbridge", "phone": "(404) 555-0171",
     "payapp_due_day": 25, "payment_terms_days": 30,
     "packet": ["Insurance certificate", "Daily tickets and time sheets", "Progress photos"],
     "about": "Commercial general contractor. Monthly pay apps by the 25th, 10% retainage, pays in 30 days."},
    {"key": "thornwell", "name": "Thornwell Construction", "client_type": "gc", "type_text": "General contractor",
     "approver": "Derrick Owens", "plus": "thornwell", "phone": "(678) 555-0182",
     "payapp_due_day": 20, "payment_terms_days": 45, "retainage_pct": 5,
     "packet": ["Insurance certificate", "Certified payroll", "Daily tickets and time sheets"],
     "about": "General contractor on a public school job. Certified payroll with every pay app, 5% retainage, pays in 45 days."},
    {"key": "ortiz", "name": "Lena Ortiz", "client_type": "homeowner", "type_text": "Homeowner",
     "approver": "Lena Ortiz", "plus": "ortiz", "phone": "(770) 555-0194",
     "payapp_due_day": None, "payment_terms_days": 10,
     "packet": None,
     "about": "Homeowner. No retainage; pays on completion, within 10 days."},
]

# ---------- the jobs and their schedules of values ----------
# Each line: (description, qty, unit, rate in dollars). LS with qty 1 is a lump sum, entered as a % done.

JOBS = [
    {"key": "lind", "client": "ironbridge", "name": "Linden Hollow Office Suite 210",
     "street": "410 Linden Hollow Rd, Suite 210", "city": "Atlanta", "state": "GA", "zip": "30309",
     "contract": 148_600, "retainage": 10, "started_on": "2026-11-02", "anticipated_end_on": "2026-12-18",
     "contract_number": "IB-2026-114", "assignee": "Marcus Bell",
     "about": "HVAC for a second-floor office fit-out: duct, three rooftop units, VAV boxes, grilles, controls, balancing.",
     "lines": [("Mobilization and submittals", 1, "LS", 6_400), ("Galvanized ductwork", 1200, "LF", 42),
               ("Rooftop units, set and connect", 3, "EA", 14_800), ("VAV boxes", 12, "EA", 1_650),
               ("Grilles, registers and diffusers", 60, "EA", 185), ("Controls and thermostats", 1, "LS", 9_800),
               ("Test, adjust and balance", 1, "LS", 6_700)]},
    {"key": "sorr", "client": "thornwell", "name": "Sorrel Creek Elementary Restrooms",
     "street": "3900 Sorrel Creek Rd", "city": "Decatur", "state": "GA", "zip": "30033",
     "contract": 96_450, "retainage": 5, "started_on": "2026-11-09", "anticipated_end_on": "2027-01-15",
     "contract_number": "TC-0457-P", "assignee": "Marcus Bell",
     "about": "Plumbing for two student restrooms: demolition, underground waste, water piping, 24 fixtures, a water heater.",
     "lines": [("Mobilization", 1, "LS", 3_500), ("Demolish existing fixtures", 24, "EA", 120),
               ("Underground waste and vent", 260, "LF", 68), ("Domestic water piping", 420, "LF", 38),
               ("Set fixtures (toilets, lavatories, urinals)", 24, "EA", 1_450), ("Water heater", 1, "LS", 8_900),
               ("Inspections and closeout", 1, "LS", 12_730)]},
    {"key": "orti", "client": "ortiz", "name": "Ortiz Repipe",
     "street": "1186 Clairmont Way", "city": "Decatur", "state": "GA", "zip": "30030",
     "contract": 14_200, "retainage": 0, "started_on": "2026-11-02", "anticipated_end_on": "2026-11-06",
     "contract_number": "", "assignee": "Dana Whitfield",
     "about": "Whole-house repipe in PEX and a tankless water heater for a homeowner. Starts and finishes in week 1.",
     "lines": [("Repipe in PEX", 1, "LS", 11_400), ("Tankless water heater", 1, "LS", 2_800)]},
]

# ---------- three weeks, day by day, in the order to enter them ----------
# "do" is what happens; kinds:
#   record   New job record. qty: {line no.: today's quantity} for unit lines, {line no.: "% to date"} for lump sums
#            ("CO1" for a signed change order's line). co_work: [(CO no. or None, hours, what)].
#            docs: [(section, {boxes})] for the document chips. Line numbers count from 1.
#   co       New change order. sign: (signer, date) signs it on paper with the record "Signed CO n" filed that day.
#   price    Price an unpriced change order. sign_co  Sign one on paper later.
#   payapp   Progress Pay App with Period to, then supporting documents, Sign, Approve & send.
#   final    Final Pay App, then the same.
#   payment  Record a Payment.   waiver  Choose waiver and send.   finish  Finish job.
#   check    A checkpoint: the figures in EXPECTED under that name.

DAYS = [
    # ----- Week 1 -----
    ("2026-11-02", [
        {"do": "record", "job": "lind", "crew": 3, "names": "Terrell, Andre, Luis", "weather": "Clear",
         "diary": "Mobilized: job box and lift on site. Submittals approved by Ironbridge on Friday.", "qty": {1: "100"}},
        {"do": "record", "job": "orti", "crew": 2, "names": "Kenji, Sam", "weather": "Clear",
         "diary": "Water off at 8. Cut in the new manifold in the basement and started the first-floor runs.", "qty": {1: "30"},
         "materials": "PEX-A 3/4 in and 1/2 in, manifold, brass fittings"},
    ]),
    ("2026-11-03", [
        {"do": "record", "job": "lind", "crew": 4, "names": "Terrell, Andre, Luis, Mike", "weather": "Clear",
         "diary": "Hung duct mains on the north side of the second floor.", "qty": {2: 140}},
        {"do": "record", "job": "orti", "crew": 2, "names": "Kenji, Sam", "weather": "Clear",
         "diary": "Second-floor runs done; pressure test held at 100 psi for 30 minutes.", "qty": {1: "70"}},
    ]),
    ("2026-11-04", [
        {"do": "record", "job": "lind", "crew": 4, "names": "Terrell, Andre, Luis, Mike", "weather": "Cloudy",
         "diary": "Duct mains on the south side.", "qty": {2: 160},
         "docs": [("Delivery tickets", {"Supplier": "Southern Sheet Metal Supply", "Ticket number": "88213", "What arrived": "Duct, fittings and hangers"})]},
        {"do": "record", "job": "orti", "crew": 2, "names": "Kenji, Sam", "weather": "Cloudy",
         "diary": "Repipe finished and patched. Water back on at 3.", "qty": {1: "100"},
         "docs": [("Inspections", {"Who inspected": "City of Decatur plumbing inspector", "Result": "Passed"})]},
    ]),
    ("2026-11-05", [
        {"do": "record", "job": "lind", "crew": 2, "names": "Terrell, Andre", "weather": "Rain", "weather_note": "Rain until 1 pm",
         "diary": "Rain: the crane pick for the rooftop units moved to Monday. Two men hung branch duct inside.", "qty": {2: 60}},
        {"do": "co", "job": "orti", "title": "Owner supplies the tankless water heater", "reason": "Owner request", "kind": "Credit (deducts)",
         "amount": 1_650, "description": "Mrs. Ortiz bought her own heater. Our price keeps the installation and takes off the heater.",
         "sign": ("Lena Ortiz", "2026-11-05")},
    ]),
    ("2026-11-06", [
        {"do": "record", "job": "lind", "crew": 4, "names": "Terrell, Andre, Luis, Mike", "weather": "Clear",
         "diary": "Branch duct on the north side; first three VAV boxes hung.", "qty": {2: 150, 4: 3}},
        {"do": "co", "job": "lind", "title": "Exhaust fan for the server room", "reason": "Owner request", "kind": "Adds to the contract",
         "amount": 4_850, "description": "Tenant added a server room; one inline exhaust fan with its duct and controls.",
         "sign": ("Paul Kessler", "2026-11-06")},
        {"do": "record", "job": "orti", "crew": 1, "names": "Kenji", "weather": "Clear",
         "diary": "Installed the owner's tankless heater, vented and tested. Walked Mrs. Ortiz through it. Job complete.", "qty": {2: "100"}},
        {"do": "final", "job": "orti", "period_to": "2026-11-06"},
        {"do": "check", "name": "Ortiz final pay app"},
        {"do": "payment", "job": "orti", "amount": 12_550, "reference": "Check 1187"},
        {"do": "waiver", "job": "orti", "kind": "Unconditional, final payment"},
        {"do": "finish", "job": "orti"},
        {"do": "check", "name": "End of week 1"},
    ]),
    # ----- Week 2 -----
    ("2026-11-09", [
        {"do": "record", "job": "lind", "crew": 6, "names": "Terrell, Andre, Luis, Mike, Dee, Ray", "weather": "Clear",
         "diary": "Crane day: all three rooftop units set on their curbs and connected.", "qty": {3: 3},
         "docs": [("Progress photos", {"What they show": "Rooftop units on their curbs"})]},
        {"do": "record", "job": "sorr", "crew": 3, "names": "Kenji, Sam, Jo", "weather": "Clear",
         "diary": "Mobilized. Demolished the fixtures in the boys' restroom.", "qty": {1: "100", 2: 8}},
    ]),
    ("2026-11-10", [
        {"do": "co", "job": "lind", "title": "Reroute duct around the beam at grid C-4", "reason": "Unforeseen condition",
         "kind": "Adds to the contract", "pricing": "Time and materials", "amount": None,
         "description": "A steel beam not on the drawings blocks the main at C-4. Rerouting on time and materials; price to follow."},
        {"do": "record", "job": "lind", "crew": 4, "names": "Terrell, Andre, Luis, Mike", "weather": "Clear",
         "diary": "Found the beam conflict at C-4; started the reroute. Mains on the east side.", "qty": {2: 100},
         "co_work": [(2, 6, "Reroute at C-4: new offsets and hangers")]},
        {"do": "record", "job": "sorr", "crew": 3, "names": "Kenji, Sam, Jo", "weather": "Clear",
         "diary": "Demolished the girls' restroom fixtures. Saw-cut the slab for the new waste lines.", "qty": {2: 16}},
    ]),
    ("2026-11-11", [
        {"do": "record", "job": "lind", "crew": 4, "names": "Terrell, Andre, Luis, Mike", "weather": "Clear",
         "diary": "Finished the C-4 reroute. Started the server room exhaust fan.", "qty": {2: 120, "CO1": "40"},
         "co_work": [(2, 8, "Reroute at C-4 finished")]},
    ]),
    ("2026-11-12", [
        {"do": "record", "job": "lind", "crew": 4, "names": "Terrell, Andre, Luis, Mike", "weather": "Cloudy",
         "diary": "Five more VAV boxes. First grilles in the open office. Exhaust fan running.", "qty": {4: 5, 5: 12, "CO1": "100"}},
        {"do": "record", "job": "sorr", "crew": 3, "names": "Kenji, Sam, Jo", "weather": "Cloudy",
         "diary": "Underground waste and vent in the boys' restroom.", "qty": {3: 120}},
    ]),
    ("2026-11-13", [
        {"do": "record", "job": "lind", "crew": 3, "names": "Terrell, Andre, Luis", "weather": "Clear",
         "diary": "Duct on the west side. Ten more grilles. Rough-in inspection passed.", "qty": {2: 90, 5: 10},
         "docs": [("Time sheets", {"Who or which crew": "Terrell's crew, Nov 9-13", "Hours": "152"}),
                  ("Inspections", {"Who inspected": "City of Atlanta mechanical inspector", "Result": "Passed rough-in, second floor"})]},
        {"do": "record", "job": "sorr", "crew": 3, "names": "Kenji, Sam, Jo", "weather": "Clear",
         "diary": "Underground in the girls' restroom. Inspected and backfilled.", "qty": {3: 90},
         "docs": [("Inspections", {"Who inspected": "DeKalb County plumbing inspector", "Result": "Underground passed"})]},
        {"do": "payapp", "job": "lind", "period_to": "2026-11-13"},
        {"do": "check", "name": "Linden Hollow pay app 1"},
        {"do": "payment", "job": "lind", "amount": 90_000, "reference": "ACH 55102"},
        {"do": "waiver", "job": "lind", "kind": "Unconditional, progress payment"},
        {"do": "check", "name": "End of week 2"},
    ]),
    # ----- Week 3 -----
    ("2026-11-16", [
        {"do": "record", "job": "lind", "crew": 4, "names": "Terrell, Andre, Luis, Mike", "weather": "Clear",
         "diary": "Duct on the south side; four more VAV boxes, all twelve in.", "qty": {2: 110, 4: 4}},
        {"do": "record", "job": "sorr", "crew": 3, "names": "Kenji, Sam, Jo", "weather": "Clear",
         "diary": "Last of the underground. Started the water piping.", "qty": {3: 50, 4: 140}},
    ]),
    ("2026-11-17", [
        {"do": "record", "job": "lind", "crew": 4, "names": "Terrell, Andre, Luis, Mike", "weather": "Cold",
         "diary": "Grilles and diffusers in the conference rooms.", "qty": {5: 18}},
        {"do": "price", "job": "lind", "co": 2, "amount": 2_160, "why": "14 hours of labor ($1,540) and material ($620)"},
        {"do": "record", "job": "sorr", "crew": 3, "names": "Kenji, Sam, Jo", "weather": "Cold",
         "diary": "Water piping above the ceiling.", "qty": {4: 160}},
    ]),
    ("2026-11-18", [
        {"do": "record", "job": "lind", "crew": 3, "names": "Terrell, Andre, Mike", "weather": "Clear",
         "diary": "Thermostats and controls wiring, west half. More grilles.", "qty": {5: 10, 6: "40"}},
        {"do": "co", "job": "sorr", "title": "Hose bibb in the custodian's closet", "reason": "Owner request", "kind": "Adds to the contract",
         "amount": 780, "description": "School asked for a hose bibb in the custodian's closet.", "sign": ("Derrick Owens", "2026-11-18")},
        {"do": "record", "job": "sorr", "crew": 3, "names": "Kenji, Sam, Jo", "weather": "Clear",
         "diary": "Water piping to the fixture walls. Hose bibb installed.", "qty": {4: 80, "CO1": "100"}},
    ]),
    ("2026-11-19", [
        {"do": "record", "job": "lind", "crew": 2, "names": "Terrell, Andre", "weather": "Rain",
         "diary": "Fire damper inspection failed: two dampers have no access door. Controls wiring, east half.", "qty": {6: "60"},
         "docs": [("Inspections", {"Who inspected": "City of Atlanta mechanical inspector", "Result": "Failed: two fire dampers need access doors"})]},
        {"do": "record", "job": "sorr", "crew": 2, "names": "Kenji, Sam", "weather": "Rain",
         "diary": "Water heater set; piping it in.", "qty": {6: "50"}},
    ]),
    ("2026-11-20", [
        {"do": "record", "job": "lind", "crew": 4, "names": "Terrell, Andre, Luis, Mike", "weather": "Clear",
         "diary": "Access doors in; damper reinspection passed. Duct on the east side.", "qty": {2: 70},
         "docs": [("Inspections", {"Who inspected": "City of Atlanta mechanical inspector", "Result": "Passed fire damper reinspection"})]},
        {"do": "record", "job": "sorr", "crew": 3, "names": "Kenji, Sam, Jo", "weather": "Clear",
         "diary": "Water heater fired and tested. Top-out inspection passed.", "qty": {6: "100"},
         "docs": [("Inspections", {"Who inspected": "DeKalb County plumbing inspector", "Result": "Top-out passed"}),
                  ("Time sheets", {"Who or which crew": "Kenji's crew, Nov 9-20", "Hours": "236"})]},
        {"do": "payapp", "job": "lind", "period_to": "2026-11-20"},
        {"do": "payapp", "job": "sorr", "period_to": "2026-11-20"},
        {"do": "check", "name": "End of week 3"},
    ]),
]

# The figures to check, in dollars. Each pay app: gross this period (before retainage), retainage, amount due.
# Each job: contract total (with signed change orders), billed to date (after retainage), paid,
# outstanding, retainage held. The test enters CASE and checks every one of these.
EXPECTED: dict[str, dict] = {}   # filled below, kept in one place with the figures


def _expected() -> dict[str, dict]:
    return {
        "Ortiz final pay app": {
            "payapps": {"PA-ORTI-01": {"gross": 12_550, "retainage": 0, "due": 12_550}},
            "jobs": {"orti": {"contract_total": 12_550, "billed": 12_550, "paid": 0, "outstanding": 12_550, "retainage": 0}},
        },
        "End of week 1": {
            "jobs": {"orti": {"contract_total": 12_550, "billed": 12_550, "paid": 12_550, "outstanding": 0, "retainage": 0, "finished": True},
                     "lind": {"contract_total": 153_450, "billed": 0, "paid": 0, "outstanding": 0, "retainage": 0}},
        },
        "Linden Hollow pay app 1": {
            "payapps": {"PA-LIND-01": {"gross": 107_360, "retainage": 10_736, "due": 96_624}},
        },
        "End of week 2": {
            "jobs": {"lind": {"contract_total": 153_450, "billed": 96_624, "paid": 90_000, "outstanding": 6_624, "retainage": 10_736},
                     "sorr": {"contract_total": 96_450, "billed": 0, "paid": 0, "outstanding": 0, "retainage": 0}},
        },
        "End of week 3": {
            "payapps": {"PA-LIND-02": {"gross": 25_220, "retainage": 2_522, "due": 22_698},
                        "PA-SORR-01": {"gross": 48_180, "retainage": 2_409, "due": 45_771}},
            "jobs": {"lind": {"contract_total": 153_450, "billed": 119_322, "paid": 90_000, "outstanding": 29_322, "retainage": 13_258,
                              "pending_cos": 1},
                     "sorr": {"contract_total": 97_230, "billed": 45_771, "paid": 0, "outstanding": 45_771, "retainage": 2_409},
                     "orti": {"finished": True}},
        },
    }


EXPECTED = _expected()

EMAIL_BASE = "you@example.com"


def plus(addr: str, tag: str) -> str:
    user, _, domain_part = addr.partition("@")
    return f"{user}+{tag}@{domain_part}"


# ---------- entering it all, the way the pages do ----------

class Refused(AssertionError):
    pass


def enter(request, email_base: str = EMAIL_BASE, password: str = "case company password", on_check=None) -> dict:
    """Type the whole case in through the HTTP API, as the pages send it. `request(method, path,
    body=None, cookie=None)` returns (status, data). Returns {checkpoint name: figures read back}.
    on_check(name, figures) is called at each checkpoint."""
    status, data = request("POST", "/api/auth/signup", {"accept_terms": True, "email": plus(email_base, "dana"), "password": password,
                                                        "name": COMPANY["owner"], "company_name": COMPANY["name"]}, None, want_cookie=True)
    if status != 200:
        raise Refused(f"sign-up: {data}")
    cookie = data["_cookie"]

    def call(method, path, body=None):
        st, out = request(method, path, body, cookie)
        if st != 200:
            raise Refused(f"{method} {path} {body!r}: {st} {out}")
        return out

    call("POST", "/api/company", {k: COMPANY[k] for k in ("name", "legal_name", "phone", "street", "city", "state", "zip", "license")})
    for p in PROFILES:
        call("POST", "/api/team/profiles", {"name": p["name"], "perms": p["perms"]})
    profiles = {p["name"]: p["id"] for p in call("GET", "/api/team").get("profiles", [])}
    for person in TEAM[1:]:
        call("POST", "/api/team/invite", {"email": plus(email_base, person["plus"]), "profile_id": profiles[person["profile"]]})

    payer_ids = {}
    for c in CLIENTS:
        body = {"name": c["name"], "client_type": c["client_type"], "approver": c["approver"], "ap_email": plus(email_base, c["plus"]),
                "phone": c["phone"], "payment_terms_days": c["payment_terms_days"], "new_customer": True}
        if c["payapp_due_day"]:
            body["payapp_due_day"] = c["payapp_due_day"]
        if c["packet"]:
            body["packet"] = ", ".join(c["packet"])
        if c["approver"] == c["name"]:
            body["approver_confirmed"] = True
        out = call("POST", "/api/payers", body)
        payer = next(p for p in out["payers"] if p["name"] == c["name"])
        payer_ids[c["key"]] = payer["id"]
        if c.get("retainage_pct") is not None:
            call("POST", f"/api/payers/{payer['id']}/update", {"retainage_pct": c["retainage_pct"]})

    jobs = {}
    for j in JOBS:
        client = next(c for c in CLIENTS if c["key"] == j["client"])
        body = {"name": j["name"], "street": j["street"], "city": j["city"], "state": j["state"], "zip": j["zip"],
                "customer": client["name"], "contract_cents": j["contract"] * 100, "retainage_pct": j["retainage"],
                "started_on": j["started_on"], "anticipated_end_on": j["anticipated_end_on"],
                "contract_number": j["contract_number"], "new_job": True,
                "sov_lines": [{"description": d, "quantity": str(q), "unit": u, "rate": str(r)} for d, q, u, r in j["lines"]]}
        out = call("POST", "/api/jobs", body)
        made = next(x for x in out["jobs"] if x["name"] == j["name"])
        jobs[j["key"]] = made["id"]

    def job(key):
        return call("GET", f"/api/jobs/{jobs[key]}")["job"]

    def line_id(key, n):
        lines = job(key)["sov"]["lines"]
        if isinstance(n, str) and n.startswith("CO"):
            co_no = int(n[2:])
            co = next(c for c in job(key)["change_orders"] if c["number"] == co_no)
            return next(l["id"] for l in lines if l.get("co_id") == co["id"] or l.get("item") == f"CO{co_no}")
        base = [l for l in lines if not l.get("co_id")]
        return base[n - 1]["id"]

    def co_id(key, n):
        return next(c["id"] for c in job(key)["change_orders"] if c["number"] == n)

    def clear_packet(key):
        """A tester uploads the files or taps "I have this..."; here each missing item gets a reason."""
        j = job(key)
        draft = next(i for i in reversed(j["invoices"]) if i["status"] in ("draft", "awaiting_approval"))
        for item in draft.get("packet", []):
            if item.get("required") and not item.get("present"):
                call("POST", f"/api/jobs/{jobs[key]}/resolve/{item['id']}", {"reason": "Handed over with the pay app"})

    def sign_and_send(key):
        j = job(key)
        draft = next(i for i in reversed(j["invoices"]) if i["status"] in ("draft", "awaiting_approval"))
        approval = draft.get("approval") or {}
        if approval.get("waiting"):
            call("POST", f"/api/jobs/{jobs[key]}/payapp-approve", {"invoice_id": draft["id"], "stamp": approval.get("stamp", ""),
                                                                 "signature": COMPANY["owner"], "title": "Owner"})
            draft = next(i for i in job(key)["invoices"] if i["id"] == draft["id"])
        call("POST", f"/api/jobs/{jobs[key]}/send", {"reviewed": (draft.get("email_preview") or {}).get("stamp", "")})

    def read_figures(name):
        want = EXPECTED.get(name, {})
        got = {"payapps": {}, "jobs": {}}
        for key in want.get("jobs", {}):
            j = job(key)
            m = j.get("money") or {}
            got["jobs"][key] = {
                "contract_total": m.get("contract_total_cents", 0) // 100,
                "billed": m.get("billed_cents", 0) // 100,
                "paid": m.get("payments_cents", m.get("paid_cents", 0)) // 100,
                "outstanding": m.get("outstanding_cents", 0) // 100,
                "retainage": m.get("retainage_held_cents", m.get("retainage_cents", 0)) // 100,
                "finished": bool(j.get("closed")),
                "pending_cos": sum(1 for c in j.get("change_orders", []) if c["status"] == "pending"),
            }
        for number in want.get("payapps", {}):
            key = number.split("-")[1].lower()
            inv = next(i for i in job(key)["invoices"] if i["number"] == number)
            got["payapps"][number] = {"gross": inv.get("gross_cents", inv["amount_cents"] + inv.get("retainage_cents", 0)) // 100,
                                      "retainage": inv.get("retainage_cents", 0) // 100, "due": inv["amount_cents"] // 100}
        return got

    figures = {}
    for day, steps in DAYS:
        for s in steps:
            kind, key = s["do"], s.get("job")
            if kind == "record":
                body = {"date": day, "notes": s["diary"], "crew_count": s["crew"], "crew_names": s.get("names", ""),
                        "weather": s.get("weather", ""), "weather_note": s.get("weather_note", ""), "materials": s.get("materials", "")}
                sections = {}
                if s.get("qty"):
                    rows = []
                    for n, q in s["qty"].items():
                        lump = isinstance(q, str)
                        rows.append({"line_id": line_id(key, n), "qty": str(q), **({"to_date": True} if lump else {})})
                    sections["quantities"] = rows
                if s.get("co_work"):
                    sections["co_work"] = [{"co_id": co_id(key, n) if n else "", "hours": str(h), "description": what} for n, h, what in s["co_work"]]
                entries = []
                for section, boxes in s.get("docs", []):
                    entries.append(_doc_entry(section, boxes, len([e for e in entries if e["section"] == _SECTION_KEYS[section]])))
                if entries:
                    body["entries"] = entries
                body["sections"] = sections
                call("POST", f"/api/jobs/{jobs[key]}/daily-record", body)
            elif kind == "co":
                body = {"title": s["title"], "reason": _REASONS[s["reason"]], "description": s.get("description", ""),
                        "pricing": "tm" if s.get("pricing") == "Time and materials" else "lump_sum",
                        "amount_cents": (-1 if s["kind"].startswith("Credit") else 1) * s["amount"] * 100 if s["amount"] else 0, "items": []}
                call("POST", f"/api/jobs/{jobs[key]}/add-co", body)
                if s.get("sign"):
                    _sign_on_paper(call, jobs[key], job, key, max(c["number"] for c in job(key)["change_orders"]), *s["sign"])
            elif kind == "price":
                call("POST", f"/api/jobs/{jobs[key]}/co-price/{co_id(key, s['co'])}",
                     {"amount_cents": s["amount"] * 100})
            elif kind == "payapp":
                done = {}
                call("POST", f"/api/jobs/{jobs[key]}/sov-progress", {"done": done, "period_to": s["period_to"], "notes": ""})
                clear_packet(key)
                sign_and_send(key)
            elif kind == "final":
                call("POST", f"/api/jobs/{jobs[key]}/done", {"period_to": s["period_to"], "notes": ""})
                clear_packet(key)
                sign_and_send(key)
            elif kind == "payment":
                j = job(key)
                inv = next(i for i in j["invoices"] if i["status"] in ("sent", "approved", "partial"))
                call("POST", f"/api/jobs/{jobs[key]}/match-payment", {"amount_cents": s["amount"] * 100, "reference": s["reference"],
                                                                       "invoice_id": inv["id"]})
            elif kind == "waiver":
                _send_waiver(call, request, cookie, jobs[key], job, key, s["kind"])
            elif kind == "finish":
                call("POST", f"/api/jobs/{jobs[key]}/close", {"closeout_override": "Homeowner job: no closeout documents"})
            elif kind == "check":
                figures[s["name"]] = read_figures(s["name"])
                if on_check:
                    on_check(s["name"], figures[s["name"]])
    return figures


_REASONS = {"Owner request": "owner_request", "Unforeseen condition": "unforeseen_condition", "Design issue": "design_issue",
            "Value engineering": "value_engineering", "Other": "other"}
_SECTION_KEYS = {"Delivery tickets": "delivery", "Progress photos": "photos", "Time sheets": "time_sheets",
                 "Invoices": "invoices", "Inspections": "inspections"}


def _doc_entry(section: str, boxes: dict, row: int) -> dict:
    s = _SECTION_KEYS[section]
    if s == "delivery":
        return {"section": s, "row": row, "supplier": boxes["Supplier"], "number": boxes["Ticket number"], "what": boxes["What arrived"]}
    if s == "photos":
        return {"section": s, "row": row, "note": boxes["What they show"]}
    if s == "time_sheets":
        return {"section": s, "row": row, "who": boxes["Who or which crew"], "hours": boxes["Hours"]}
    if s == "inspections":
        return {"section": s, "row": row, "who": boxes["Who inspected"], "outcome": boxes["Result"]}
    return {"section": s, "row": row, **boxes}


def _sign_on_paper(call, job_id, job, key, co_no, signer, signed_on):
    """Signed on paper: the signed copy is filed on the job (a tester takes a photo of it), then Save as signed."""
    call("POST", f"/api/jobs/{job_id}/add-record", {"label": f"Signed CO {co_no}", "kind": "change_order", "note": f"Signed by {signer}"})
    rec = next(r for r in reversed(job(key)["records"]) if r["label"] == f"Signed CO {co_no}")
    co = next(c for c in job(key)["change_orders"] if c["number"] == co_no)
    call("POST", f"/api/jobs/{job_id}/approve-co/{co['id']}", {"record_id": rec["id"], "signer_name": signer, "signed_on": signed_on})


_WAIVER_KINDS = {"Conditional, progress payment": "conditional_progress", "Unconditional, progress payment": "unconditional_progress",
                 "Conditional, final payment": "conditional_final", "Unconditional, final payment": "unconditional_final"}


def _send_waiver(call, request, cookie, job_id, job, key, kind_text):
    """Choose waiver and send: the signed waiver goes on the job records, its type is picked
    ("Which waiver is this?"), then Approve & send once the person has opened it."""
    kind = _WAIVER_KINDS[kind_text]
    # A tester takes a photo of the signed form; here a stand-in photo is uploaded the same way.
    st, out = request("POST", f"/api/import/file?job={job_id}&file=1&kind=waiver", None, cookie,
                      raw=b"\xff\xd8\xff\xe0 signed waiver", headers={"Content-Type": "image/jpeg", "X-Filename": "signed-waiver.jpg"})
    if st != 200:
        raise Refused(f"waiver upload: {st} {out}")
    j = job(key)
    rec = next(r for r in reversed(j["records"]) if r["kind"] == "waiver")
    inv = next(i for i in reversed(j["invoices"]) if i["status"] in ("paid", "partial", "sent", "approved"))
    call("POST", f"/api/jobs/{job_id}/waiver-type/{rec['id']}", {"kind": kind, "for_id": inv["id"]})
    body = {"invoice_id": inv["id"], "kind": kind, "looked_at": rec["id"]}
    if "final" in kind:
        body["confirm_final"] = True
    call("POST", f"/api/jobs/{job_id}/waiver", body)


# ---------- the packet: docs/case-company.md ----------

def _http(base: str):
    """A request function for enter() against a running Duracash server at base."""
    import json
    import urllib.error
    import urllib.request

    def request(method, path, body=None, cookie=None, want_cookie=False, raw=None, headers=None):
        hdrs = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        if cookie:
            hdrs["Cookie"] = cookie
        req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                status, payload, setc = res.status, res.read(), res.headers.get("Set-Cookie")
        except urllib.error.HTTPError as exc:
            status, payload, setc = exc.code, exc.read(), None
        out = json.loads(payload or b"{}")
        if want_cookie and setc:
            out["_cookie"] = setc.split(";")[0]
        return status, out
    return request


def main(argv: list[str]) -> int:
    """python3 case_company.py                 writes docs/case-company.md
    python3 case_company.py --enter URL EMAIL  types the whole case into the server at URL as a new
                                               company, signed up as EMAIL with +dana (and +rosa,
                                               +ironbridge... for the team and clients), so the
                                               finished case can be opened and compared."""
    if len(argv) >= 3 and argv[0] == "--enter":
        import getpass
        import os
        password = os.environ.get("CASE_PASSWORD") or getpass.getpass("Password for the new account (at least 10 characters): ")
        figures = enter(_http(argv[1]), email_base=argv[2], password=password,
                        on_check=lambda name, _: print(f"checkpoint: {name}"))
        bad = [name for name, want in EXPECTED.items() for key, f in want.get("jobs", {}).items()
               if {k: figures[name]["jobs"][key][k] for k in f} != f]
        print("every figure matches" if not bad else f"figures differ at: {', '.join(bad)}")
        print(f"sign in as {plus(argv[2], 'dana')}")
        return 1 if bad else 0
    import case_company_doc
    out = Path(__file__).resolve().parent / "docs" / "case-company.md"
    out.write_text(case_company_doc.render(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
