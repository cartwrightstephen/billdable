"""The demo company: Redbud Electric Co., a small electrical subcontractor in Atlanta (Stephen,
10 Nov, decision 11: "Modify the demo as needed"; build: an electrical trade sub with a real
schedule of values, job records, tickets, 10% retainage and a GC client).

The demo is made the way a customer's data is: every job, job record, pay app, payment, waiver
and change order goes through the same domain functions the pages call, so its figures add up
by the app's own arithmetic and nothing here can drift from what the app would store. Dates are
counted back from the day it is built, so a demo reset today shows a pay app sent 18 days ago,
not one from a fixed autumn that ages out. Field paperwork (delivery tickets, time sheets,
insurance certificate, lien waivers) is real one-page PDFs, returned beside the state; the
server writes them to the demo company's uploads (write_files).

The jobs, one for each stage a demo should show:
  Westside Lofts, Building B    two pay apps paid, the third sent and unpaid; a change order
                                waiting for the GC's signature
  Edgewood Retail, Suite 140    paid three days ago, its unconditional waiver not sent yet
  Summit Ridge Medical Office   the first pay app ten days past due, the second drafted and
                                ready to send
  Kirkwood Library lighting     work finished and billed in full, 10% retainage still held
  Cascade Road Warehouse        work recorded on job records since the last pay app, not billed
  three finished jobs           paid in full and closed, for the year's billing history

seed.py's seed_state() (Hartline Remodeling) stays as the test fixture: hundreds of tests and the
browser suite are written against its jobs. DURACASH_DEMO_SEED=hartline makes the demo company
that one (the browser suite sets it).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import threading
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

import doc_check
import domain
import migrations
import payapp_pdf

# Bumped when the demo's data changes, so a demo company made from an older seed is made again
# once on the next start (server.ensure_demo).
SEED_VERSION = "electrical-1"
COMPANY_NAME = "Redbud Electric Co."
OWNER_NAME = "Dana Whitfield"
OWNER_EMAIL = "owner@redbudelectric.example"
# The demo account before decision 11: renamed in place (migrations.AUTH_STEPS) so its sessions,
# id and company stay.
OLD_OWNER_EMAIL = "owner@hartline.example"
OLD_OWNER_NAME = "Alex Hartline"
LEGACY = "hartline"

ACTOR = OWNER_NAME
FOREMAN = "Luis Ortega"
CREW = ["Luis Ortega", "Ty Brooks", "Sam Kerr", "Andre Mills", "Jo Park"]

COMPANY = {
    "name": COMPANY_NAME,
    "legal_name": "Redbud Electric Company, LLC",
    "street": "77 Foundry Row NW",
    "city": "Atlanta",
    "state": "GA",
    "zip": "30318",
    "phone": "404-555-0160",
    "license": "EN212000",
}
# Rest of the company card, as the Hartline seed has it (Overview reads crew and revenue band).
COMPANY_EXTRA = {"crew": 8, "revenue_band": "$750K", "books": "QuickBooks Online"}

CLIENTS = {
    "Granite Row Builders": {
        "client_type": "gc", "approver": "Marcus Bell", "ap_email": "ap@graniterowbuilders.example", "phone": "404-555-0131",
        "playbook": {"packet": "Conditional waiver, Daily tickets and time sheets, Insurance certificate",
                     "payment_terms_days": 45, "usual_approval_days": 35, "follow_up_days": 7,
                     "rejects_when": "Quantities on the pay app that the tickets don't back up. Insurance certificate older than a year.",
                     "copy": "Project manager on every pay app"},
    },
    "Halcyon Construction Group": {
        "client_type": "gc", "approver": "Renee Ortiz", "ap_email": "payables@halcyoncg.example", "phone": "678-555-0144",
        "playbook": {"packet": "Conditional waiver, Daily tickets and time sheets",
                     "payment_terms_days": 30, "usual_approval_days": 28, "follow_up_days": 7,
                     "rejects_when": "Pay apps that arrive after the 25th go to next month's draw."},
    },
    "Piedmont Pediatric Partners": {
        "client_type": "owner", "approver": "Lena Price", "ap_email": "billing@piedmontpediatric.example", "phone": "770-555-0119",
        "playbook": {"packet": "Conditional waiver", "payment_terms_days": 30, "usual_approval_days": 21, "follow_up_days": 7},
    },
}

# Schedules of values: (description, unit, quantity, unit rate in dollars). Each job's contract is
# its schedule's total; the tests check every one adds up.
SCHEDULES: dict[str, list[tuple[str, str, Any, Any]]] = {
    "westside": [
        ("Temporary power", "LS", 1, 6800),
        ("Service entrance and switchgear", "LS", 1, 28500),
        ("Feeders, 4/0 copper in EMT", "LF", 1450, 38),
        ("Panelboards and unit load centers", "ea", 26, 780),
        ("Branch rough-in, apartments", "unit", 24, 2150),
        ("Devices, receptacles and switches", "ea", 1100, "14.50"),
        ("Light fixtures, install", "ea", 420, 42),
        ("Fire alarm rough-in", "unit", 24, 310),
    ],
    "edgewood": [
        ("Demolition and make-safe", "LS", 1, 2400),
        ("Panel L1, 225A, and feeder", "LS", 1, 7850),
        ("Branch rough-in, MC cable", "LF", 3600, "4.10"),
        ("Devices, receptacles and switches", "ea", 140, 16),
        ("LED troffers, install", "ea", 96, 85),
        ("Lighting controls", "LS", 1, 6300),
        ("Storefront sign circuit and exterior lighting", "LS", 1, 3850),
    ],
    "summit": [
        ("Temporary power and lighting", "LS", 1, 4900),
        ("800A service and distribution", "LS", 1, 31400),
        ("Feeders", "LF", 1050, 41),
        ("Branch rough-in", "LF", 9800, "3.25"),
        ("Devices, receptacles and switches", "ea", 520, 15),
        ("Light fixtures, install", "ea", 310, 48),
        ("Nurse call and low-voltage rough-in", "LS", 1, 8720),
    ],
    "kirkwood": [
        ("Remove existing fixtures", "ea", 210, 18),
        ("LED fixtures, install", "ea", 210, 96),
        ("Occupancy sensors", "ea", 46, 120),
        ("Lighting control panel", "LS", 1, 6480),
        ("Exit and emergency lights", "ea", 18, 150),
    ],
    "cascade": [
        ("Utility coordination and temporary service", "LS", 1, 3500),
        ("1200A service and switchboard", "LS", 1, 24800),
        ("Feeders, 600 kcmil aluminum", "LF", 600, 52),
        ("Dock receptacles and disconnects", "ea", 14, 345),
    ],
    "grantpark": [
        ("Temporary power", "LS", 1, 4200),
        ("Meter centers and services", "ea", 3, 9400),
        ("Branch rough-in, townhomes", "unit", 12, 3650),
        ("Devices and fixtures, trim-out", "unit", 12, 1480),
    ],
    "lakewood": [
        ("Service and distribution", "LS", 1, 18600),
        ("Branch circuits to units", "LF", 5200, "4.75"),
        ("Exterior and corridor lighting", "ea", 64, 210),
        ("Gate and access control power", "LS", 1, 4560),
    ],
    "piedmont": [
        ("Exam room circuits", "room", 8, 1150),
        ("Panel replacement, 200A", "LS", 1, 5400),
        ("LED fixtures, install", "ea", 48, 92),
    ],
}


def schedule_total_cents(rows: list[tuple[str, str, Any, Any]]) -> int:
    return int(sum(Decimal(str(q)) * Decimal(str(r)) for _, _, q, r in rows) * 100)


# ---------- the clock the domain reads while the demo is built ----------
# The domain stamps everything with the time now. The demo is built a day at a time in the past,
# so for the thread doing it, domain.utcnow and datetime.now (in the modules that call it) read
# the day being built. Every other thread reads the real time, so a reset of the demo on a
# running server can't stamp another company's changes with a demo day.

_LOCAL = threading.local()
_BUILD_LOCK = threading.Lock()


def _now() -> datetime | None:
    return getattr(_LOCAL, "now", None)


def _clocked_datetime(real: type) -> type:
    class Clocked(real):  # type: ignore[misc, valid-type]
        @classmethod
        def now(cls, tz=None):
            at = _now()
            if at is None:
                return real.now(tz)
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)
    return Clocked


@contextlib.contextmanager
def _demo_clock() -> Iterator[None]:
    import domain.daily
    import domain.jobinfo
    import domain.tasks
    import trial
    modules = [m for m in (domain, domain.daily, domain.jobinfo, domain.tasks, trial) if isinstance(getattr(m, "datetime", None), type)]
    real_utcnow = domain.utcnow
    saved = [(m, m.datetime) for m in modules]

    def utcnow() -> str:
        at = _now()
        return at.replace(microsecond=0).isoformat() if at else real_utcnow()

    with _BUILD_LOCK:
        domain.utcnow = utcnow
        for m, real in saved:
            m.datetime = _clocked_datetime(real)
        try:
            yield
        finally:
            _LOCAL.now = None
            domain.utcnow = real_utcnow
            for m, real in saved:
                m.datetime = real


# ---------- documents ----------

def _pdf(title: str, lines: list[str]) -> bytes:
    page = payapp_pdf._Page()
    y = payapp_pdf.PAGE_H - 72
    page.text(54, y, COMPANY["legal_name"], "F2", 10)
    page.text(54, y - 13, f"{COMPANY['street']}, {COMPANY['city']}, {COMPANY['state']} {COMPANY['zip']}  ·  {COMPANY['phone']}", "F1", 9)
    y -= 48
    page.text(54, y, title, "F2", 15)
    y -= 26
    for line in lines:
        if line:
            page.text(54, y, line, "F1", 10.5)
        y -= 16
    return payapp_pdf._build([page])


# ---------- the build ----------

class _Builder:
    def __init__(self, today: date, link_base: str, company_id: str) -> None:
        self.today = today
        self.link_base = link_base
        self.company_id = company_id
        self.files: dict[str, bytes] = {}
        self.state: dict[str, Any] = {"company": {"name": COMPANY_NAME}, "payers": [], "jobs": [], "audit": [], "email_log": [], "schema_version": 1}
        migrations.migrate(self.state)
        self.ticket_no = 55100
        self.check_no = 20410

    # time
    def at(self, days_ago: int, hour: int = 15) -> str:
        _LOCAL.now = datetime.combine(self.today - timedelta(days=days_ago), time(hour, 0), tzinfo=timezone.utc)
        return (self.today - timedelta(days=days_ago)).isoformat()

    def day(self) -> str:
        return _now().date().isoformat()

    # lookups
    def job(self, name: str) -> dict[str, Any]:
        return next(j for j in self.state["jobs"] if j["name"] == name)

    def line_id(self, job: dict[str, Any], description: str) -> str:
        return next(line["id"] for line in job["sov"]["lines"] if line["description"] == description)

    def read(self, meta: dict[str, Any]) -> Any:
        return doc_check.prepare(str(meta.get("ext") or "").lower(), self.files[meta["id"]])

    # files on records
    def _file(self, rec: dict[str, Any], name: str, data: bytes) -> None:
        meta = {"id": "file-" + secrets.token_hex(8), "name": name, "ext": ".pdf", "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "added_at": domain.utcnow(), "added_by": ACTOR}
        self.state.setdefault("files", []).append(meta)
        self.files[meta["id"]] = data
        rec["file_ids"] = [meta["id"]]

    def document(self, job: dict[str, Any], label: str, kind: str, name: str, data: bytes, note: str = "") -> dict[str, Any]:
        domain.add_job_record(self.state, job["id"], label, kind, note, ACTOR)
        rec = job["records"][-1]
        rec["on_date"] = self.day()
        self._file(rec, name, data)
        return rec

    # the parts of a job's life
    def new_job(self, key: str, name: str, client: str, address: dict[str, str], started: int, number: str = "") -> dict[str, Any]:
        self.at(started, 14)
        details = CLIENTS[client]
        rows = SCHEDULES[key]
        domain.create_job(self.state, name, client, schedule_total_cents(rows), address_parts=address, approver=details["approver"],
                          ap_email=details["ap_email"], phone=details["phone"], client_type=details["client_type"], retainage_pct=10,
                          contract_number=number, actor=ACTOR, started_on=self.day(), auto_schedule=True, approver_confirmed=True,
                          schedule_lines=[{"description": d, "unit": u, "quantity": str(q), "rate": str(r)} for d, u, q, r in rows])
        job = self.job(name)
        if not job.get("sov"):   # a create_job told not to make one (tests' @dollar_jobs): the schedule is the demo's point
            domain.save_sov(self.state, job["id"], [{"description": d, "unit": u, "quantity": str(q), "rate": str(r)} for d, u, q, r in rows], 0, actor=ACTOR)
        payer = next(p for p in self.state["payers"] if p["id"] == job["payer_id"])
        if not payer.get("demo_playbook"):
            domain.update_payer(self.state, payer["id"], dict(details["playbook"]), ACTOR)
            payer["demo_playbook"] = True
        if "Insurance certificate" in payer.get("packet", ""):
            self.document(job, f"Certificate of liability insurance, {client}", "insurance", "COI-Redbud-Electric.pdf", _pdf(
                "CERTIFICATE OF LIABILITY INSURANCE", [
                    f"Insured: {COMPANY['legal_name']}", "Insurer: Fictional Mutual Casualty Co. (demo)",
                    "General liability: $1,000,000 each occurrence / $2,000,000 aggregate",
                    "Workers' compensation: statutory limits", f"Certificate holder: {client}",
                    f"Project: {name}", f"Issued: {self.day()}",
                ]))
        return job

    def field_day(self, job: dict[str, Any], days_ago: int, notes: str, qty: dict[str, Any] | None = None, crew: int = 3,
                  delivery: str = "", supplier: str = "Redclay Electric Supply", photo: str = "") -> None:
        """One New job record: the diary, crew, quantities put in place, and the day's delivery
        ticket and time sheet (each a PDF on its own job record) and a photo noted."""
        self.at(days_ago, 19)   # 3 pm in Atlanta: the same day wherever the page is read in the US or Europe
        names = CREW[:crew]
        hours = 8 if days_ago % 2 else 9
        body: dict[str, Any] = {"crew_count": len(names), "crew_names": names, "weather": "Clear" if days_ago % 3 else "Cloudy",
                                "sections": {}, "entries": [], "today": self.day()}
        if qty:
            body["sections"]["quantities"] = [{"line_id": self.line_id(job, d), "qty": str(q)} for d, q in qty.items()]
        ticket = ""
        if delivery:
            self.ticket_no += 37
            ticket = f"DT-{self.ticket_no}"
            body["entries"].append({"section": "delivery", "supplier": supplier, "number": ticket, "what": delivery})
        body["entries"].append({"section": "time_sheets", "who": f"Crew of {len(names)}, {FOREMAN} foreman", "hours": str(hours * len(names))})
        if photo:
            body["entries"].append({"section": "photos", "note": photo})
        domain.save_job_record(self.state, job["id"], notes, ACTOR, body)
        daily_id = self.state["daily_records"][-1]["id"]
        for rec in job["records"]:
            if rec.get("daily_record_id") != daily_id or rec.get("file_ids"):
                continue
            if rec.get("ticket_type") == "delivery":
                self._file(rec, f"{ticket}.pdf", _pdf("DELIVERY TICKET", [
                    f"Supplier: {supplier}", f"Ticket: {ticket}", f"Delivered: {self.day()}", f"Ship to: {job['name']}, {job['address']}",
                    f"Material: {delivery}", "Received by: " + FOREMAN]))
            elif rec.get("ticket_type") == "time_sheet":
                self._file(rec, f"Time sheet {self.day()}.pdf", _pdf("DAILY TIME SHEET", [
                    f"Job: {job['name']}", f"Date: {self.day()}", *[f"{n}: {hours} hours" for n in names],
                    f"Total: {hours * len(names)} hours", f"Foreman: {FOREMAN}"]))

    def bill(self, job: dict[str, Any], days_ago: int, final: bool = False) -> dict[str, Any]:
        """Progress Pay App (or Final pay app) from the quantities the job records put in place."""
        self.at(days_ago, 16)
        if final:
            domain.mark_done(self.state, job["id"], ACTOR, "Work complete, punch list walked with the GC.")
        else:
            domain.record_sov_progress(self.state, job["id"], {}, actor=ACTOR, period_end=self.day())
        return next(inv for inv in job["invoices"] if inv["status"] in domain.DRAFT_STATUSES)

    def paperwork(self, job: dict[str, Any], inv: dict[str, Any]) -> None:
        """Fill the draft's supporting documents: the conditional waiver for its amount, the
        tickets and time sheets since the pay app before, and the insurance certificate."""
        before = [i["created_at"][:10] for i in job["invoices"] if i is not inv and i["status"] not in ("void",) and i.get("sent_at")]
        since = max(before) if before else ""
        for item in list(inv.get("packet") or []):
            if item.get("present"):
                continue
            if domain.is_waiver_item(item):
                kind = item.get("waiver_kind") or "conditional_progress"
                final = kind.endswith("final")
                rec = self.document(job, f"{'Conditional waiver on final payment' if final else 'Conditional waiver on progress payment'}, {inv['number']}",
                                    "waiver", f"Conditional waiver {inv['number']}.pdf", self.waiver_pdf(job, inv, "conditional", final))
                domain.attach_record_to_packet(self.state, job["id"], item["id"], rec["id"], ACTOR, read_file=self.read, looked=True, answer="conditional")
            elif "ticket" in item["label"].lower():
                for rec in job["records"]:
                    if rec.get("kind") == "ticket" and rec.get("file_ids") and rec.get("on_date", "") > since:
                        domain.attach_record_to_packet(self.state, job["id"], item["id"], rec["id"], ACTOR, read_file=self.read, looked=True)
            elif "insurance" in item["label"].lower():
                rec = next(r for r in job["records"] if r.get("kind") == "insurance")
                domain.attach_record_to_packet(self.state, job["id"], item["id"], rec["id"], ACTOR, read_file=self.read, looked=True)

    def closeout(self, job: dict[str, Any], days_ago: int, leave_out: tuple[str, ...] = ()) -> None:
        """The closeout documents a retainage request needs, each a PDF on the job record; an item
        named in leave_out stays missing (what the GC hasn't given back yet)."""
        self.at(days_ago, 15)
        held = domain.job_money(job)["retainage_open_cents"]
        docs = {
            "punch": ("punch", "Punch list sign-off", ["All punch list items complete and accepted.", "Accepted by: Marcus Bell"]),
            "completion": ("completion", "Certificate of substantial completion", ["The electrical work is substantially complete."]),
            "as-built": ("asbuilt", "As-built drawings, electrical", ["Sheets E-001 to E-401, marked up to field conditions."]),
            "warrant": ("warranty", "Electrical warranty, one year", ["Workmanship warranted for one year from substantial completion."]),
        }
        for item in domain.closeout_missing(job):
            label = item["label"].lower()
            if any(word in label for word in leave_out):
                continue
            if domain.is_waiver_item(item):
                kind = item.get("waiver_kind") or "conditional_final"
                which = kind.split("_")[0]
                rec = self.document(job, f"{which.capitalize()} waiver on final payment, retainage", "waiver",
                                    "Final waiver, retainage.pdf", self.waiver_pdf(job, {"number": "Retainage", "amount_cents": held}, which, True))
                domain.attach_record_to_packet(self.state, job["id"], item["id"], rec["id"], ACTOR, read_file=self.read, looked=True, answer=which)
                continue
            kind, title, lines = next(v for k, v in docs.items() if k in label)
            rec = self.document(job, f"{title}, {job['name']}", kind, f"{title}.pdf",
                                _pdf(title.upper(), [f"Project: {job['name']}", f"Address: {job['address']}", *lines, f"Date: {self.day()}"]))
            domain.attach_record_to_packet(self.state, job["id"], item["id"], rec["id"], ACTOR, read_file=self.read, looked=True)

    def waiver_pdf(self, job: dict[str, Any], inv: dict[str, Any], which: str, final: bool, cents: int | None = None) -> bytes:
        payer = next(p for p in self.state["payers"] if p["id"] == job["payer_id"])
        title = ("WAIVER AND RELEASE OF LIEN AND PAYMENT BOND RIGHTS UPON FINAL PAYMENT" if final
                 else "INTERIM WAIVER AND RELEASE UPON PAYMENT")
        amount = domain.money(inv["amount_cents"] if cents is None else cents)
        return _pdf(f"{which.upper()} {title}"[:90], [
            f"{which.capitalize()} waiver, State of Georgia, O.C.G.A. 44-14-366",
            f"Project: {job['name']}", f"Address: {job['address']}", f"Contractor: {payer['name']}",
            f"Pay application: {inv.get('number', 'Retainage')}", f"Amount: {amount}",
            ("Upon receipt of the sum above, the undersigned waives and releases any and all liens"
             if which == "conditional" else "The undersigned has been paid the sum above and waives and releases any and all liens"),
            "and claims of lien for labor, services or materials furnished to the project through the date of this waiver.",
            f"Signed: {OWNER_NAME}, {COMPANY['legal_name']}", f"Date: {self.day()}",
        ])

    def send(self, job: dict[str, Any], days_ago: int) -> dict[str, Any]:
        inv = next(inv for inv in job["invoices"] if inv["status"] in domain.DRAFT_STATUSES)
        self.at(days_ago, 17)
        self.paperwork(job, inv)
        domain.approve_and_send(self.state, job["id"], ACTOR, link_base=self.link_base, company_id=self.company_id)
        return inv

    def paid(self, job: dict[str, Any], inv: dict[str, Any], days_ago: int, waiver_days_ago: int | None = None) -> None:
        self.at(days_ago, 15)
        self.check_no += 13
        domain.match_payment(self.state, job["id"], inv["amount_cents"], f"Check {self.check_no}", ACTOR,
                             invoice_id=inv["id"], received_on=self.day())
        if waiver_days_ago is not None:
            self.unconditional(job, inv, waiver_days_ago)

    def unconditional(self, job: dict[str, Any], inv: dict[str, Any], days_ago: int, release: dict[str, Any] | None = None) -> None:
        self.at(days_ago, 16)
        final = bool(release) or bool(inv.get("final")) and not domain.job_money(job)["retainage_open_cents"]
        kind = "unconditional_final" if final else "unconditional_progress"
        target = release or inv
        rec = self.document(job, f"Unconditional waiver, {inv.get('number') or 'retainage'}", "waiver",
                            f"Unconditional waiver {inv.get('number') or 'retainage'}.pdf",
                            self.waiver_pdf(job, inv, "unconditional", final, target.get("amount_cents")))
        domain.set_waiver_type(self.state, job["id"], rec["id"], kind, ACTOR, for_id=target["id"])
        domain.send_waiver(self.state, job["id"], ACTOR, invoice_id=None if release else inv["id"], release_id=release["id"] if release else None,
                           kind=kind, read_file=self.read, looked_at=rec["id"], confirm_final=final)


def build(today: date | None = None, link_base: str = "", company_id: str = "") -> tuple[dict[str, Any], dict[str, bytes]]:
    """The demo company as of `today`, and the bytes of each of its files by file id."""
    import auth
    today = today or datetime.now(timezone.utc).date()
    company_id = company_id or auth.DEMO_COMPANY_ID
    link_base = link_base or "https://duracash.example"
    with _demo_clock():
        b = _Builder(today, link_base, company_id)
        b.at(400, 14)
        domain.update_company(b.state, dict(COMPANY), ACTOR)
        b.state["company"].update(COMPANY_EXTRA)
        _history(b)
        _westside(b)
        _edgewood(b)
        _summit(b)
        _kirkwood(b)
        _cascade(b)
    state = b.state
    # Nothing the demo "sent" went anywhere: the Outbox says so, and Today doesn't list them as
    # emails that didn't go out (as seed.py marks Hartline's).
    for email in state.get("email_log", []):
        email["status"] = "not sent: demo account"
        email["sample"] = True
    state["company"]["demo_seed"] = SEED_VERSION
    return state, b.files


def _addr(street: str, city: str, zip_code: str) -> dict[str, str]:
    return {"street": street, "city": city, "state": "GA", "zip": zip_code}


def _history(b: _Builder) -> None:
    """Three jobs finished last year and this spring: paid, retainage released, closed."""
    for key, name, client, addr, start, steps in (
        ("grantpark", "Grant Park Townhomes, Phase 1", "Granite Row Builders", _addr("540 Cherokee Avenue SE", "Atlanta", "30312"), 330,
         [(310, {"Temporary power": 100, "Meter centers and services": 1, "Branch rough-in, townhomes": 6}),
          (275, {"Meter centers and services": 3, "Branch rough-in, townhomes": 12, "Devices and fixtures, trim-out": 12})]),
        ("lakewood", "Lakewood Self Storage, Phase 1", "Halcyon Construction Group", _addr("2190 Lakewood Way SW", "Atlanta", "30315"), 265,
         [(240, {"Service and distribution": 60, "Branch circuits to units": 2400}),
          (205, {"Service and distribution": 100, "Branch circuits to units": 5200, "Exterior and corridor lighting": 64,
                 "Gate and access control power": 100})]),
        ("piedmont", "Piedmont Pediatrics, Suite 300", "Piedmont Pediatric Partners", _addr("1200 Piedmont Way NE", "Atlanta", "30309"), 215,
         [(190, {"Exam room circuits": 8, "Panel replacement, 200A": 100, "LED fixtures, install": 48})]),
    ):
        job = b.new_job(key, name, client, addr, start)
        for n, (day, qty) in enumerate(steps):
            b.field_day(job, day + 4, f"{name}: progress on site.", qty, delivery="Wire, conduit and boxes" if n == 0 else "Fixtures and devices",
                        photo="Work in place")
            last = n == len(steps) - 1
            inv = b.bill(job, day, final=last)
            b.send(job, day)
            b.paid(job, inv, day - 34, waiver_days_ago=day - 35)
        # Retainage: asked for at the end, paid, its final waiver sent, and the job finished.
        b.closeout(job, day - 36)
        domain.chase_retainage(b.state, job["id"], ACTOR, read_file=b.read)
        b.at(day - 60, 15)
        held = domain.job_money(job)["retainage_open_cents"]
        b.check_no += 13
        domain.record_retainage_payment(b.state, job["id"], held, f"Check {b.check_no}", ACTOR)
        release = next(r for r in job.get("retainage_waivers", []) if r.get("waiver_due") and not r.get("waiver_sent"))
        b.unconditional(job, job["invoices"][-1], day - 61, release=release)
        b.at(day - 62, 15)
        domain.close_job(b.state, job["id"], ACTOR)


def _westside(b: _Builder) -> None:
    job = b.new_job("westside", "Westside Lofts, Building B", "Granite Row Builders", _addr("1180 Huff Road NW", "Atlanta", "30318"), 150, "GRB-2026-031")
    b.field_day(job, 140, "Temp pole set, temp panels on each floor.", {"Temporary power": 100}, crew=2,
                delivery="Temp pole, two 100A temp panels, cord sets", photo="Temp power, Building B")
    b.field_day(job, 118, "Gear set in the main electrical room. Feeders pulled to floors 1 and 2.",
                {"Service entrance and switchgear": 40, "Feeders, 4/0 copper in EMT": 600}, crew=4,
                delivery="4/0 copper THHN, 2,000 ft; 2 in. EMT, 600 ft", photo="Switchgear set")
    inv1 = b.bill(job, 112)
    b.send(job, 112)
    b.paid(job, inv1, 70, waiver_days_ago=69)
    b.field_day(job, 95, "Feeders to floors 3 and 4. Unit load centers hung, floors 1 and 2.",
                {"Feeders, 4/0 copper in EMT": 850, "Panelboards and unit load centers": 12}, crew=4,
                delivery="Load centers, 14 ea; 4/0 copper THHN, 1,000 ft", photo="Load centers, floor 2")
    b.field_day(job, 60, "Rough-in, units 101 to 212. Rough inspection passed, floors 1 and 2.",
                {"Branch rough-in, apartments": 12, "Panelboards and unit load centers": 8}, crew=5,
                delivery="12/2 and 10/2 MC, 9,000 ft; boxes and supports", photo="Rough-in, unit 204")
    inv2 = b.bill(job, 55)
    b.send(job, 55)
    b.paid(job, inv2, 21, waiver_days_ago=20)
    b.field_day(job, 32, "Rough-in, units 301 to 412. Service energized.",
                {"Service entrance and switchgear": 100, "Branch rough-in, apartments": 12, "Panelboards and unit load centers": 6,
                 "Fire alarm rough-in": 24}, crew=5, delivery="Fire alarm boxes and cable; load centers, 12 ea", photo="Service energized")
    b.at(18)
    inv3 = b.bill(job, 18)
    b.send(job, 18)
    # Since the third pay app: devices going in, floors 1 and 2 (recorded, not billed yet).
    b.field_day(job, 9, "Devices, floors 1 and 2.", {"Devices, receptacles and switches": 380}, crew=4,
                delivery="Receptacles and switches, 400 ea", photo="Trim-out, unit 108")
    # The GC asked for EV charger circuits in the garage: priced, sent for signature, not signed yet.
    b.at(8, 15)
    domain.add_change_order(b.state, job["id"], "EV charger circuits, garage level", 1485000, ACTOR,
                                 description="Six 40A, 240V circuits from house panel HP to the garage, with conduit and disconnects.",
                                 schedule_days=3, reason="owner_request")
    co = job["change_orders"][-1]
    b.at(6, 15)
    domain.request_co_signature(b.state, job["id"], co["id"], ACTOR, link_base=b.link_base, company_id=b.company_id)


def _edgewood(b: _Builder) -> None:
    job = b.new_job("edgewood", "Edgewood Retail, Suite 140", "Granite Row Builders", _addr("1380 Edgewood Avenue NE", "Atlanta", "30307"), 82, "GRB-2026-044")
    b.field_day(job, 70, "Demo done, panel L1 set and fed.", {"Demolition and make-safe": 100, "Panel L1, 225A, and feeder": 100}, crew=3,
                delivery="225A panel, feeder wire", photo="Panel L1")
    b.field_day(job, 55, "Rough-in complete, devices and troffers started.",
                {"Branch rough-in, MC cable": 3600, "Devices, receptacles and switches": 60, "LED troffers, install": 40}, crew=4,
                delivery="MC cable 3,600 ft; LED troffers, 96 ea", photo="Ceiling grid with troffers")
    inv = b.bill(job, 50)
    b.send(job, 50)
    # Paid three days ago: the unconditional waiver for it is due and not sent.
    b.paid(job, inv, 3)
    b.field_day(job, 2, "Troffers and devices continuing. Waiting on the sign vendor.",
                {"LED troffers, install": 30, "Devices, receptacles and switches": 50}, crew=3, photo="Sales floor lighting")


def _summit(b: _Builder) -> None:
    job = b.new_job("summit", "Summit Ridge Medical Office", "Halcyon Construction Group", _addr("3350 Summit Ridge Parkway", "Duluth", "30096"), 72, "HCG-1187")
    b.field_day(job, 66, "Temp power and lighting up.", {"Temporary power and lighting": 100}, crew=2,
                delivery="Temp lighting strings, temp panel", photo="Temp lighting")
    b.field_day(job, 45, "Service gear set, feeders pulled to the first-floor panels.",
                {"800A service and distribution": 60, "Feeders": 520}, crew=5,
                delivery="800A switchboard; 500 kcmil copper, 1,200 ft", photo="Switchboard set")
    b.bill(job, 40)
    b.send(job, 40)
    # Halcyon pays in 30 days; this one is ten days late, and has been chased once.
    b.at(5, 15)
    domain.record_follow_up(b.state, job["id"], ACTOR)
    b.field_day(job, 24, "Feeders finished. Branch rough-in, first floor.", {"Feeders": 530, "Branch rough-in": 4200}, crew=5,
                delivery="MC cable 5,000 ft; boxes and supports", photo="Rough-in, exam rooms")
    b.field_day(job, 6, "Branch rough-in, second floor. Nurse call rough-in started.",
                {"Branch rough-in": 3100, "Nurse call and low-voltage rough-in": 40}, crew=5,
                delivery="Nurse call back boxes and cable", photo="Nurse call rough-in")
    # This month's pay app, drafted and ready for Approve & send.
    b.bill(job, 1)
    draft = next(inv for inv in job["invoices"] if inv["status"] in domain.DRAFT_STATUSES)
    b.at(1, 17)
    b.paperwork(job, draft)


def _kirkwood(b: _Builder) -> None:
    job = b.new_job("kirkwood", "Kirkwood Library lighting retrofit", "Granite Row Builders", _addr("11 Kirkwood Road NE", "Atlanta", "30317"), 96, "GRB-2026-038")
    b.field_day(job, 85, "Old fixtures out, first floor. New fixtures going in.",
                {"Remove existing fixtures": 120, "LED fixtures, install": 90}, crew=3,
                delivery="LED fixtures, 210 ea", photo="Reading room before")
    inv1 = b.bill(job, 75)
    b.send(job, 75)
    b.field_day(job, 46, "Second floor done. Sensors, control panel and exit lights in. Punch list walked.",
                {"Remove existing fixtures": 90, "LED fixtures, install": 120, "Occupancy sensors": 46, "Lighting control panel": 100,
                 "Exit and emergency lights": 18}, crew=3, delivery="Occupancy sensors, 46 ea; exit lights, 18 ea", photo="Reading room after")
    # The first pay app is paid before the final one goes out (Stephen, 10 Nov, decision 6: a final
    # pay app over an unpaid one needs an override, which this story doesn't have).
    b.paid(job, inv1, 44, waiver_days_ago=43)
    inv2 = b.bill(job, 42, final=True)
    b.send(job, 42)
    b.paid(job, inv2, 12, waiver_days_ago=11)
    # The work is done and billed in full. The 10% retainage is held until closeout, and the
    # GC hasn't sent the certificate of substantial completion yet, so it can't be asked for.
    b.closeout(job, 10, leave_out=("completion",))


def _cascade(b: _Builder) -> None:
    job = b.new_job("cascade", "Cascade Road Warehouse service upgrade", "Halcyon Construction Group", _addr("4100 Cascade Road SW", "Atlanta", "30331"), 58, "HCG-1192")
    b.field_day(job, 50, "Utility meeting held; temporary service set.", {"Utility coordination and temporary service": 100}, crew=2,
                delivery="Temporary service equipment", photo="Temp service")
    b.field_day(job, 40, "Switchboard set. Feeders started.", {"1200A service and switchboard": 70, "Feeders, 600 kcmil aluminum": 220}, crew=4,
                delivery="1200A switchboard; 600 kcmil aluminum, 700 ft", photo="Switchboard")
    inv = b.bill(job, 35)
    b.send(job, 35)
    b.paid(job, inv, 7, waiver_days_ago=6)
    # Since then, on the job records and not billed yet.
    b.field_day(job, 20, "Feeders finished. Switchboard terminated.", {"Feeders, 600 kcmil aluminum": 380, "1200A service and switchboard": 20}, crew=4,
                delivery="Lugs and terminations kit", photo="Feeder terminations")
    b.field_day(job, 4, "Dock receptacles and disconnects, doors 1 to 10.", {"Dock receptacles and disconnects": 10}, crew=3,
                delivery="Dock receptacles and disconnects, 14 ea", photo="Dock door 4")


# ---------- what the server uses ----------

def legacy() -> bool:
    """DURACASH_DEMO_SEED=hartline: the demo company is seed.py's Hartline Remodeling, the data the
    tests and the browser suite are written against."""
    return (os.environ.get("DURACASH_DEMO_SEED") or "").strip().lower() == LEGACY


def write_files(company_id: str, files: dict[str, bytes], metas: list[dict[str, Any]]) -> None:
    """Put the demo's files where its records look for them, after taking away the demo's old
    uploads (what anyone added while looking around goes with a reset)."""
    import shutil
    import importer
    folder = importer.UPLOADS / company_id
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    for meta in metas:
        if meta["id"] in files:
            importer.file_path(company_id, meta).write_bytes(files[meta["id"]])


if __name__ == "__main__":
    import json
    import sys
    s, f = build()
    json.dump(s, sys.stdout, indent=1, default=str)
