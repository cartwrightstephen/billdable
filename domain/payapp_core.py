"""Pay-app core (Nov 27, Siteline comparison items 2-4): starting a job mid-project with the pay
apps it already had, retainage per schedule line and per pay app, the retainage-only pay app, and
the backup each stored-materials line needs.

Earlier pay apps (before Duracash). Each is kept as an ordinary pay app with imported: True, in
the order of its number, ahead of any made here: its continuation sheet (sov_sheet) holds the
total billed to date on every line, so "work completed from previous application" on the next
pay app, the G702 summary, the job's money and the next number all read it like any other. It
is never emailed, pulled back or put in QuickBooks (qbo_pushable): it is already on the books.
What was paid on it before is a payment with imported: True, and its lien waivers are taken as
handled outside Duracash (waiver_not_needed for one paid in full; waiver_covered_cents for
money already in on one paid in part, so only later money owes a waiver). The old PDFs are job
records (kind pay_app) tied to it (history_invoice_id). Two ways in: pay app by pay app (each
line as dollars this period, or % complete to date), or one summary of "previously billed to
date" per line, retainage held to date and total paid, kept as one pay app numbered as the last
one billed. They can be entered while no pay app has been made in Duracash, and taken off again
(remove_earlier_payapps) until then.

Retainage per line. A schedule line may carry retainage_pct, which overrides the job's percent
on that line (materials at 0% or 5%, labor at 10%), and a draft may carry
retainage_pct_override, which holds one percent on everything this period. retainage_for groups
the period's amounts by percent and rounds once per group, so a job with one percent holds
exactly what it always did. Each continuation-sheet entry whose percent differs from the pay
app's own carries its retainage_pct, which the retainage column reads (sov.sheet_columns).

Retainage release. A pay app with no new work (gross 0) whose one line bills retainage held:
retainage_cents is minus the amount, so line 5 of the summary comes down and line 8 is the
release. Full or part, at substantial completion or final; final needs the work finished, all
of it billed and the closeout documents in, as the retainage request does, and carries the
closeout files. While it is a draft nothing else is drafted; once sent, the retainage it bills
is no longer held (job_money), and its payment is recorded like any pay app's, which brings its
lien waiver due: on final payment when nothing else is open.

Stored materials backup. A draft that bills new materials stored on a line (G703 column F up
from the last pay app) carries a Stored materials documents item covering those lines: a check
(soft) unless the job makes it required. It counts as in only when each line has an invoice,
receipt or photo tied to it (a stored record with sov_line_id), or, with one line, any stored
record attached.

Imported at the bottom of domain/__init__.py (from .payapp_core import *); domain's own helpers
are read at call time (import domain), so neither module imports the other while loading.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import payapp_pdf
import sov

__all__ = [
    "MAX_LINE_RETAINAGE", "RELEASE_STAGES", "line_retainage_pct", "retainage_for", "set_line_retainage", "set_payapp_retainage",
    "import_earlier_payapps", "remove_earlier_payapps", "attach_history_file", "imported_invoices", "qbo_pushable",
    "draft_retainage_release", "release_drafted_cents", "release_is_final", "final_for", "release_view",
    "stored_new_lines", "add_stored_item", "stored_backup_follows", "stored_backup_rows", "payapp_core_view",
    "not_on_release_draft", "not_imported",
]

MAX_LINE_RETAINAGE = 20   # as the job's own percent (the Edit job form: a whole percent from 0 to 20)
RELEASE_STAGES = {"substantial": "at substantial completion", "final": "final"}
HISTORY_ACTOR = "Before Duracash"


def _d():
    import domain
    return domain


def _today() -> date:
    return datetime.now(timezone.utc).date()


# ---------- retainage per line and per pay app ----------

def _pct_value(raw: Any, what: str) -> int:
    """A whole percent from 0 to MAX_LINE_RETAINAGE, from a form."""
    text = str(raw).strip().rstrip("%").strip() if raw is not None else ""
    if not re.fullmatch(r"\d{1,3}", text) or int(text) > MAX_LINE_RETAINAGE:
        raise ValueError(f"{what}: enter a whole percent from 0 to {MAX_LINE_RETAINAGE}.")
    return int(text)


def line_retainage_pct(job: dict[str, Any], line: dict[str, Any] | None, override: Any = None) -> int:
    """The percent held on a line this period: the pay app's override, else the line's own, else the job's."""
    if override is not None:
        return int(override)
    if line is not None and isinstance(line.get("retainage_pct"), int):
        return int(line["retainage_pct"])
    return int(job.get("retainage_pct") or 0)


def retainage_for(job: dict[str, Any], period: dict[str, Any] | None, pending_cents: int = 0, override: Any = None,
                  work_cents: int = 0) -> dict[str, Any]:
    """The retainage a pay app holds: {cents, pct (the pay app's own percent), pcts (each percent
    used), label (the "Less retainage" line), by_line ({line id: percent} where it differs)}.
    period: the schedule's period (sov.this_period / with_amounts); without one, work_cents is the
    work billed in dollars. Overhead and profit and unsigned change orders are held at the pay
    app's own percent. Grouped by percent, rounded half-up once per group."""
    own = int(override) if override is not None else int(job.get("retainage_pct") or 0)
    groups: dict[int, int] = {}

    def add(pct: int, cents: int) -> None:
        if cents:
            groups[pct] = groups.get(pct, 0) + cents

    by_line: dict[str, int] = {}
    if period:
        lines = {line["id"]: line for line in sov.lines(job)}
        for entry in period["sheet"]:
            pct = line_retainage_pct(job, lines.get(entry["line_id"]), override)
            if pct != own:
                by_line[entry["line_id"]] = pct
        for entry in period["lines"]:
            add(line_retainage_pct(job, lines.get(entry["line_id"]), override), entry["amount_cents"])
        add(own, period["op_cents"])
    else:
        add(own, work_cents)
    add(own, pending_cents)
    gross = sum(groups.values())
    held = sum((cents * pct + 50) // 100 for pct, cents in groups.items() if pct)
    held = max(0, min(held, gross)) if gross > 0 else 0
    used = sorted({pct for pct, cents in groups.items() if pct and cents > 0}, reverse=True)
    if len(used) <= 1:
        label = f"Less retainage {used[0] if used else own}%"
    else:
        label = f"Less retainage ({' and '.join(f'{p}%' for p in used)} by line)"
    return {"cents": held, "pct": own, "pcts": used, "label": label, "by_line": by_line}


def set_line_retainage(state: dict[str, Any], job_id: str, pcts: Any, actor: str = "owner") -> dict[str, Any]:
    """Retainage per schedule line: {line id: percent, or "" for the job's percent}. Applies from
    the next pay app, and to the open draft while it is only here (drafted again); pay apps sent
    keep what they held. Audited."""
    D = _d()
    job = D._open_job(state, job_id)
    if not sov.has_sov(job):
        raise D.GateError("This job has no schedule of values. Its retainage is the job's percent (Edit job).")
    if not isinstance(pcts, dict) or not pcts or len(pcts) > sov.MAX_LINES:
        D._refuse("", {"pcts": "Enter a retainage percent on at least one line, or clear it for the job's percent."})
    by_id = {line["id"]: line for line in sov.lines(job)}
    errors: dict[str, str] = {}
    changes: list[tuple[dict[str, Any], int | None]] = []
    for line_id, raw in pcts.items():
        line = by_id.get(line_id) if isinstance(line_id, str) else None
        if not line:
            errors[f"pct_{str(line_id)[:20]}"] = "That line is not on this job's schedule."
            continue
        name = D.sov_line_name(line)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            new = None
        else:
            try:
                new = _pct_value(raw, name)
            except ValueError as exc:
                errors[f"pct_{line_id}"] = str(exc)
                continue
        if new != line.get("retainage_pct"):
            changes.append((line, new))
    D._refuse("Retainage not saved.", errors)
    if not changes:
        D._refuse("", {"pcts": "Nothing changed: those lines already hold those percents."})
    job_pct = int(job.get("retainage_pct") or 0)
    words = []
    for line, new in changes:
        was = line.get("retainage_pct")
        if new is None:
            line.pop("retainage_pct", None)
        else:
            line["retainage_pct"] = new
        words.append(f"{D.sov_line_name(line)} {f'{was}%' if was is not None else f'job {job_pct}%'} → {f'{new}%' if new is not None else f'job {job_pct}%'}")
    D.audit(state, job_id, "line_retainage", f"{actor} set retainage by line on {job['name']}: {'; '.join(words)}. It applies from the next pay app.", actor)
    draft = _work_draft(job)
    if draft and not D._with_client(draft):
        D._redraft(state, job, draft, "with retainage by line")
    return job


def set_payapp_retainage(state: dict[str, Any], job_id: str, pct: Any, actor: str = "owner") -> dict[str, Any]:
    """Retainage on the open draft for this period only: one percent on everything it bills, in
    place of the lines' and the job's ("" goes back to them). The draft is drafted again, keeping
    its number and paperwork; the next pay app holds the usual percents again."""
    D = _d()
    job = D._open_job(state, job_id)
    draft = next((inv for inv in job["invoices"] if inv["status"] in D.DRAFT_STATUSES), None)
    if not draft:
        raise D.GateError("There is no draft pay app to change. Draft one first.")
    if draft.get("retainage_release"):
        raise D.GateError(f"{draft['number']} releases retainage; it holds none.")
    if D._with_client(draft):
        raise D.GateError(f"{draft['number']} is with the client as a draft. Void it or record their answer first.")
    if pct is None or (isinstance(pct, str) and not pct.strip()):
        new = None
    else:
        try:
            new = _pct_value(pct, "Retainage this period")
        except ValueError as exc:
            D._refuse("", {"pct": str(exc)})
    was = draft.get("retainage_pct_override")
    if new == was:
        D._refuse("", {"pct": "That is already the retainage on this pay app."})
    if new is None:
        draft.pop("retainage_pct_override", None)
    else:
        draft["retainage_pct_override"] = new
    D.audit(state, job_id, "payapp_retainage",
            f"{actor} set the retainage on {draft['number']} for this period to " + (f"{new}%." if new is not None else "the lines' and the job's percents again."), actor)
    return D._redraft(state, job, draft, "with the retainage changed for this period")


def _work_draft(job: dict[str, Any]) -> dict[str, Any] | None:
    D = _d()
    return next((inv for inv in job["invoices"] if inv["status"] in D.DRAFT_STATUSES and not inv.get("retainage_release")), None)


# ---------- earlier pay apps (start a job mid-project) ----------

def imported_invoices(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [inv for inv in job.get("invoices", []) if inv.get("imported") and inv.get("status") != "void"]


def qbo_pushable(invoice: dict[str, Any]) -> bool:
    """Whether a pay app goes to QuickBooks: sent, and made in Duracash (an earlier one is on the books already)."""
    return not invoice.get("imported") and invoice.get("status") in {"sent", "approved", "partial", "paid"}


def not_imported(invoice: dict[str, Any], what: str) -> None:
    """Refuse an action that only fits a pay app made here."""
    if invoice.get("imported"):
        D = _d()
        raise D.GateError(f"{invoice['number']} was billed before Duracash, so it can't be {what}. Change it under Earlier pay apps on the job.")


def not_on_release_draft(job: dict[str, Any]) -> None:
    """Progress and amounts wait while the open draft is a retainage release."""
    D = _d()
    draft = next((inv for inv in job["invoices"] if inv["status"] in D.DRAFT_STATUSES), None)
    if draft and draft.get("retainage_release"):
        raise D.GateError(f"{draft['number']} is a retainage release draft. Send or delete it, then record progress.")


def _number_of(job: dict[str, Any], raw: Any, field: str) -> int:
    D = _d()
    text = str(raw if raw is not None else "").strip()
    m = re.fullmatch(rf"(?:PA-{re.escape(str(job.get('code', '')))}-)?#?0*(\d{{1,3}})", text, re.I)
    if not m or not 1 <= int(m.group(1)) <= 999:
        D._refuse("", {field: "Enter the pay app's number, like 3."})
    return int(m.group(1))


def _iso_day(raw: Any, field: str, label: str, required: bool = True) -> str:
    D = _d()
    text = str(raw or "").strip()
    if not text:
        if required:
            D._refuse("", {field: f"Enter {label}, like 2026-09-30."})
        return ""
    try:
        day = date.fromisoformat(text)
    except ValueError:
        D._refuse("", {field: f"Enter {label} as a date, like 2026-09-30."})
    if day > _today():
        D._refuse("", {field: f"{label[0].upper()}{label[1:]} can't be after today."})
    return day.isoformat()


def _cents(raw: Any, field: str, label: str, blank: int | None = 0) -> int:
    D = _d()
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if blank is None:
            D._refuse("", {field: f"Enter {label}."})
        return int(blank)
    if isinstance(raw, int) and not isinstance(raw, bool):
        cents = raw * 100
    else:
        try:
            cents = sov.to_cents(raw, label)
        except sov.SovError as exc:
            D._refuse("", {field: str(exc)})
    if cents < 0:
        D._refuse("", {field: f"{label[0].upper()}{label[1:]} can't be negative."})
    return cents


def _to_date_by_line(job: dict[str, Any], raw: Any, before: dict[str, int], where: str, field: str) -> dict[str, int]:
    """{line id: billed to date} from {line id: {"amount": this period $} | {"pct": % complete to
    date} | {"to_date": $ to date}}; a line left out stays where it was."""
    D = _d()
    if not isinstance(raw, dict):
        D._refuse("", {field: f"{where}: enter what was billed on each line."})
    by_id = {line["id"]: line for line in sov.lines(job)}
    out = dict(before)
    errors = []
    for line_id, spec in raw.items():
        line = by_id.get(line_id) if isinstance(line_id, str) else None
        if not line:
            errors.append(f"{where}: line {str(line_id)[:20]} is not on this job's schedule.")
            continue
        name = D.sov_line_name(line)
        spec = spec if isinstance(spec, dict) else {"amount": spec}
        prev = before.get(line_id, 0)
        try:
            if str(spec.get("pct") if spec.get("pct") is not None else "").strip():
                pct = Decimal(str(spec["pct"]).strip().rstrip("%"))
                if pct < 0 or pct > 100 or pct != pct.quantize(Decimal("0.01")):
                    raise sov.SovError(f"{name}: % complete is a number from 0 to 100, with at most two decimals.")
                to_date = int((Decimal(line["scheduled_cents"]) * pct / 100).quantize(Decimal("1"), rounding="ROUND_HALF_UP"))
            elif str(spec.get("to_date") if spec.get("to_date") is not None else "").strip():
                to_date = sov.to_cents(spec["to_date"], f"{name}: billed to date")
            elif str(spec.get("amount") if spec.get("amount") is not None else "").strip():
                to_date = prev + sov.to_cents(spec["amount"], f"{name}: billed this period")
            else:
                continue
        except (sov.SovError, ArithmeticError, ValueError) as exc:
            errors.append(f"{where}: {exc}" if isinstance(exc, sov.SovError) else f"{where}: {name}: enter a number.")
            continue
        if line["scheduled_cents"] >= 0 and to_date < prev:
            errors.append(f"{where}: {name} is {D.money(to_date)} to date, less than the {D.money(prev)} billed before it.")
        elif line["scheduled_cents"] >= 0 and to_date > line["scheduled_cents"]:
            errors.append(f"{where}: {name} is {D.money(to_date)} to date, more than the line's {D.money(line['scheduled_cents'])}.")
        else:
            out[line_id] = to_date
    if errors:
        raise D.GateError("Earlier pay apps not saved.", errors[:10], fields={field: errors[0]})
    return out


def _qty_for(line: dict[str, Any], to_date: int) -> int:
    if line["scheduled_cents"] <= 0 or to_date >= line["scheduled_cents"]:
        return line["qty_milli"] if to_date else 0
    return to_date * 1000 // line["rate_cents"] if line["rate_cents"] else 0


def _sheet_for(job: dict[str, Any], before: dict[str, int], after: dict[str, int], qty_before: dict[str, int]) -> list[dict[str, Any]]:
    """The continuation sheet of an earlier pay app: every line, from billed to date before it to after it."""
    sheet = []
    for line in sov.lines(job):
        prev, to_date = before.get(line["id"], 0), after.get(line["id"], 0)
        qty = max(_qty_for(line, to_date), qty_before.get(line["id"], 0)) if to_date else 0
        sheet.append({
            "line_id": line["id"], "item": line.get("item", ""), "description": line["description"], "unit": line["unit"],
            "rate_cents": line["rate_cents"], "scheduled_cents": line["scheduled_cents"],
            "qty_milli_this": max(0, qty - qty_before.get(line["id"], 0)), "qty_milli_to_date": qty, "qty_milli": line["qty_milli"],
            "amount_cents": to_date - prev, "to_date_cents": to_date, "work_to_date_cents": to_date,
            "prev_work_cents": prev, "work_this_cents": to_date - prev, "stored_cents": 0,
        })
    return sheet


def _clean_app(job: dict[str, Any], raw: Any, n: int) -> dict[str, Any]:
    D = _d()
    if not isinstance(raw, dict):
        D._refuse("", {"payapps": f"Pay app {n} is not a pay app."})
    number = _number_of(job, raw.get("number"), f"number_{n}")
    where = f"Pay app {number}"
    period = _iso_day(raw.get("period_to"), f"period_to_{n}", f"the date {where} ran to")
    paid_on = _iso_day(raw.get("paid_on"), f"paid_on_{n}", f"the date {where} was paid", required=False)
    return {"number": number, "where": where, "period_to": period, "paid_on": paid_on, "lines": raw.get("lines"),
            "retainage": raw.get("retainage"), "paid": raw.get("paid"), "n": n}


def import_earlier_payapps(state: dict[str, Any], job_id: str, body: Any, actor: str = "owner") -> dict[str, Any]:
    """Enter the pay apps a job had before Duracash (see the top of this file). body:
    {"payapps": [{number, period_to, lines: {line id: {amount | pct | to_date}}, retainage ($ held
    on it; blank holds the lines' percents), paid ($), paid_on}]} or {"summary": {number (the last
    pay app billed), period_to, lines: {line id: {to_date | pct}}, retainage (held to date), paid
    (total paid), paid_on}}. Refused while a pay app made in Duracash exists."""
    D = _d()
    job = D._open_job(state, job_id)
    made_here = [inv for inv in job["invoices"] if inv["status"] != "void" and not inv.get("imported")]
    if made_here:
        raise D.GateError(f"{made_here[0]['number']} was made in Duracash. Earlier pay apps are entered before the first one here"
                          + (": delete the draft first." if made_here[0]["status"] in D.DRAFT_STATUSES else "."))
    body = body if isinstance(body, dict) else {}
    if not sov.has_sov(job):
        # A lump-sum job from before every job had a schedule (Nov 27 audit): one line for the
        # contract amount, as a new job gets, and the page's "contract" line is that line.
        line = sov.auto_line(int(job.get("contract_cents") or 0), "sov-" + secrets.token_hex(8))
        job["sov"] = {"op_bp": 0, "lines": [line] + [l for l in sov.lines(job) if l.get("co_id")]}
        for raw in ([body.get("summary")] if isinstance(body.get("summary"), dict) else body.get("payapps") or []):
            if isinstance(raw, dict) and isinstance(raw.get("lines"), dict) and "contract" in raw["lines"]:
                raw["lines"][line["id"]] = raw["lines"].pop("contract")
    summary = isinstance(body.get("summary"), dict)
    raw_apps = [body["summary"]] if summary else body.get("payapps")
    if not isinstance(raw_apps, list) or not raw_apps:
        D._refuse("", {"payapps": "Enter at least one earlier pay app."})
    if len(raw_apps) > 60:
        D._refuse("", {"payapps": "Enter at most 60 earlier pay apps at a time."})
    apps = sorted((_clean_app(job, raw, n) for n, raw in enumerate(raw_apps, start=1)), key=lambda a: a["number"])
    existing = imported_invoices(job)
    have = {_number_of(job, inv["number"], "number") for inv in existing}
    numbers = [a["number"] for a in apps]
    clash = sorted(set(numbers) & have) or sorted(n for n in set(numbers) if numbers.count(n) > 1)
    if clash:
        D._refuse("", {"payapps": f"Pay app {clash[0]} is entered twice."})
    if existing and min(numbers) < max(have):
        D._refuse("", {"payapps": f"Enter pay apps after {existing[-1]['number']}, the last one entered, or take the earlier ones off and enter them all again."})
    last_day = max((inv.get("period_to") or "" for inv in existing), default="")
    for a in apps:
        if a["period_to"] < last_day:
            D._refuse("", {f"period_to_{a['n']}": f"{a['where']} runs to {a['period_to']}, before {last_day}, the pay app before it."})
        last_day = a["period_to"]
    # Billed to date per line so far, from what is already entered.
    already = sov.billed(job)
    before = dict(already["by_line"])
    qty_before = dict(already["qty_by_line"])
    op_before = already["op_cents"]
    made = []
    if not job.get("history_before"):
        job["history_before"] = {line["id"]: {"done_milli": line["done_milli"], **({"field_milli": line["field_milli"]} if "field_milli" in line else {})}
                                 for line in sov.lines(job)}
    pending_payments = []
    for a in apps:
        after = _to_date_by_line(job, a["lines"], before, a["where"], f"lines_{a['n']}")
        sheet = _sheet_for(job, before, after, qty_before)
        co_ids = {line["id"] for line in sov.lines(job) if line.get("co_id")}
        op = max(0, sov.markup(sum(e["to_date_cents"] for e in sheet if e["line_id"] not in co_ids), sov.op_bp(job)) - op_before)
        billed_lines = [e for e in sheet if e["amount_cents"]]
        gross = sum(e["amount_cents"] for e in sheet) + op
        if gross <= 0 and not summary:
            D._refuse("", {f"lines_{a['n']}": f"{a['where']}: enter what it billed on at least one line."})
        period = {"lines": billed_lines, "sheet": sheet, "op_cents": op}
        if str(a["retainage"] if a["retainage"] is not None else "").strip():
            held = {"cents": _cents(a["retainage"], f"retainage_{a['n']}", "the retainage held"), "pct": int(job.get("retainage_pct") or 0), "by_line": {}}
            held["label"] = "Less retainage" + (" held to date" if summary else "")
            if held["cents"] > max(0, gross):
                D._refuse("", {f"retainage_{a['n']}": f"{a['where']}: the retainage held is more than the {D.money(gross)} it billed."})
        else:
            held = retainage_for(job, period)
        for entry in sheet:
            if entry["line_id"] in held["by_line"]:
                entry["retainage_pct"] = held["by_line"][entry["line_id"]]
        amount = gross - held["cents"]
        paid = _cents(a["paid"], f"paid_{a['n']}", "the amount paid")
        if paid > amount:
            D._refuse("", {f"paid_{a['n']}": f"{a['where']}: {D.money(paid)} paid is more than the {D.money(amount)} it came to after retainage."})
        number = f"PA-{job['code']}-{a['number']:02d}"
        lines = [{"label": sov.line_label(e), "amount_cents": e["amount_cents"], "sov_line_id": e["line_id"]} for e in billed_lines]
        if op:
            lines.append({"label": f"Overhead and profit {sov.op_bp(job) / 100:g}% on this period's base work", "amount_cents": op})
        if held["cents"]:
            lines.append({"label": held["label"], "amount_cents": -held["cents"]})
        at = f"{a['period_to']}T12:00:00+00:00"
        invoice = {
            "id": f"inv-{job['id']}-h{secrets.token_hex(4)}", "number": number, "amount_cents": amount, "gross_cents": gross,
            "retainage_cents": held["cents"], "status": "paid" if amount and paid >= amount else "partial" if paid else "sent",
            "lines": lines, "created_at": at, "sent_at": at, "approved_by": HISTORY_ACTOR, "packet": [], "packet_id": "",
            "sov_lines": billed_lines, "sov_sheet": sheet, "op_cents": op, "retainage_pct": held["pct"], "period_to": a["period_to"],
            "progress_notes": "", "imported": True, "imported_at": D.utcnow(), "imported_by": actor, "imported_as": "summary" if summary else "payapp",
            "final": False, "history_record_ids": [],
        }
        if not amount:
            invoice["status"] = "paid"
        if paid:
            invoice["waiver_covered_cents"] = paid
            pending_payments.append((invoice, paid, a["paid_on"] or a["period_to"]))
        if invoice["status"] == "paid":
            invoice["waiver_not_needed"] = {"reason": "Billed and paid before Duracash; its waivers were handled then.", "at": D.utcnow(), "by": actor}
        invoice["contract_at_send"] = payapp_pdf.contract_terms(job)
        made.append(invoice)
        before, op_before = after, op_before + op
        qty_before = {e["line_id"]: e["qty_milli_to_date"] for e in sheet}
    job["invoices"].extend(made)
    for invoice, paid, day in pending_payments:
        refs = {p.get("reference") for p in job["payments"]}
        ref = f"before-{invoice['number']}"
        while ref in refs:
            ref += "-x"
        job["payments"].append({"id": f"pay-{len(job['payments']) + 1}-h{secrets.token_hex(2)}", "amount_cents": paid, "reference": ref,
                                "invoice_id": invoice["id"], "matched_at": f"{day}T12:00:00+00:00", "imported": True})
    # The lines stand at what is billed: nothing earlier shows as work still to bill.
    for line in sov.lines(job):
        qty = qty_before.get(line["id"], 0)
        if qty > line["done_milli"]:
            line["done_milli"] = qty
        if "field_milli" in line and qty > line["field_milli"]:
            line["field_milli"] = qty
    job["completed_value_cents"] = sov.totals(job)["to_date_cents"]
    total = sum(inv["amount_cents"] for inv in made)
    D.audit(state, job_id, "earlier_payapps",
            f"{actor} entered {'the totals billed before Duracash, through ' + made[0]['number'] if summary else D._numbers(made) + ', billed before Duracash'}: "
            f"{D.money(sum(inv['gross_cents'] for inv in made))} of work, {D.money(sum(inv['retainage_cents'] for inv in made))} retainage held, "
            f"{D.money(total)} billed and {D.money(sum(p for _i, p, _d in pending_payments))} paid. The next pay app is {D._next_number(job)}.", actor)
    return job


def remove_earlier_payapps(state: dict[str, Any], job_id: str, actor: str = "owner") -> dict[str, Any]:
    """Take the earlier pay apps off again (entered wrong), while nothing was made in Duracash and
    no payment was recorded on them here. They stay on the job as void, and the lines go back to
    where they stood before."""
    D = _d()
    job = D._open_job(state, job_id)
    earlier = imported_invoices(job)
    if not earlier:
        raise D.GateError("No earlier pay apps are entered on this job.")
    made_here = [inv for inv in job["invoices"] if inv["status"] != "void" and not inv.get("imported")]
    if made_here:
        raise D.GateError(f"{made_here[0]['number']} was made in Duracash after them, so the earlier pay apps stay.")
    ids = {inv["id"] for inv in earlier}
    if any(p.get("invoice_id") in ids and not p.get("imported") for p in job["payments"]):
        raise D.GateError("A payment was recorded here on an earlier pay app, so they stay.")
    if job.get("retainage_payments") or job.get("retainage_credits"):
        raise D.GateError("Retainage is recorded as paid or credited on this job, so the earlier pay apps stay.")
    now = D.utcnow()
    for inv in earlier:
        inv.update(status="void", voided_at=now, voided_by=actor, voided_reason="Earlier pay app taken off", replaced=True)
    job["payments"] = [p for p in job["payments"] if not (p.get("imported") and p.get("invoice_id") in ids)]
    for line in sov.lines(job):
        was = (job.get("history_before") or {}).get(line["id"])
        if was is None:
            continue
        line["done_milli"] = min(int(was["done_milli"]), line["qty_milli"])
        if "field_milli" in was:
            line["field_milli"] = min(int(was["field_milli"]), line["qty_milli"])
        elif "field_milli" in line:
            line.pop("field_milli")
    job.pop("history_before", None)
    job["completed_value_cents"] = sov.totals(job)["to_date_cents"]
    D.audit(state, job_id, "earlier_payapps_removed", f"{actor} took off the earlier pay apps {D._numbers(earlier)}.", actor)
    return job


def attach_history_file(state: dict[str, Any], job_id: str, invoice_id: str, record_id: str, actor: str = "owner") -> dict[str, Any]:
    """The old PDF of an earlier pay app: a job record (pay app) tied to it."""
    D = _d()
    job = D._job(state, job_id)
    invoice = next((inv for inv in imported_invoices(job) if inv["id"] == invoice_id), None)
    rec = next((r for r in job.get("records", []) if r["id"] == record_id), None)
    if not invoice or not rec:
        raise D.GateError("Pick an earlier pay app on this job.")
    rec["history_invoice_id"] = invoice["id"]
    if record_id not in invoice.setdefault("history_record_ids", []):
        invoice["history_record_ids"].append(record_id)
    D.audit(state, job_id, "earlier_payapp_file", f"{actor} kept {rec['label']} as the copy of {invoice['number']} from before Duracash.", actor)
    return job


# ---------- the retainage-only pay app ----------

def release_drafted_cents(job: dict[str, Any]) -> int:
    """Retainage on a release that is still a draft: not released yet, and not free to ask for again."""
    D = _d()
    return sum(inv["amount_cents"] for inv in job.get("invoices", []) if inv.get("retainage_release") and inv["status"] in D.DRAFT_STATUSES)


def release_is_final(job: dict[str, Any], invoice: dict[str, Any]) -> bool:
    """A final release that bills all that is held, with nothing else open: the last money."""
    D = _d()
    return (invoice.get("retainage_release") or {}).get("stage") == "final" and D.is_final(job) and not D.still_open(job, invoice)


def final_for(job: dict[str, Any], invoice: dict[str, Any]) -> bool:
    """Whether a pay app is the final one: a release by release_is_final, any other by domain.is_final."""
    D = _d()
    return release_is_final(job, invoice) if invoice.get("retainage_release") else D.is_final(job)


def _standing_sheet(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The continuation sheet as the last pay app left it, with nothing this period."""
    last: dict[str, dict[str, Any]] = {}
    for inv in sov.live_invoices(job):
        for entry in inv.get("sov_sheet") or inv.get("sov_lines") or []:
            last[entry["line_id"]] = entry
    sheet = []
    for line in sov.lines(job):
        was = last.get(line["id"])
        to_date = was["to_date_cents"] if was else 0
        stored = int((was or {}).get("stored_cents") or 0)
        entry = {
            "line_id": line["id"], "item": line.get("item", ""), "description": line["description"], "unit": line["unit"],
            "rate_cents": line["rate_cents"], "scheduled_cents": line["scheduled_cents"], "qty_milli_this": 0,
            "qty_milli_to_date": (was or {}).get("qty_milli_to_date", 0), "qty_milli": line["qty_milli"],
            "amount_cents": 0, "to_date_cents": to_date, "work_to_date_cents": to_date - stored, "prev_work_cents": to_date - stored,
            "work_this_cents": 0, "stored_cents": stored,
        }
        if was and "retainage_pct" in was:
            entry["retainage_pct"] = was["retainage_pct"]
        sheet.append(entry)
    return sheet


def draft_retainage_release(state: dict[str, Any], job_id: str, amount_cents: Any = None, stage: str = "final", actor: str = "owner",
                            period_end: Any = "", notes: Any = "") -> dict[str, Any]:
    """Bill retainage held as its own pay app (see the top of this file). amount_cents blank is all
    of it. stage: "substantial" (substantial completion, the work may go on) or "final" (the work
    finished and billed, the closeout documents in)."""
    D = _d()
    job = D._open_job(state, job_id)
    if stage not in RELEASE_STAGES:
        D._refuse("", {"stage": "Pick substantial completion or final."})
    draft = next((inv for inv in job["invoices"] if inv["status"] in D.DRAFT_STATUSES), None)
    if draft:
        raise D.GateError(f"{draft['number']} is an open draft. Send or delete it, then bill the retainage.")
    m = D.job_money(job)
    held = m["retainage_open_cents"]
    if held <= 0:
        raise D.GateError("No retainage is held on this job.")
    if amount_cents in (None, ""):
        amount = held
    else:
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
            D._refuse("", {"amount_cents": "Enter the retainage to bill, more than $0."})
        if amount_cents > held:
            D._refuse("", {"amount_cents": f"Enter no more than the {D.money(held)} held."})
        amount = amount_cents
    if stage == "final":
        if job.get("work_status") != "complete":
            raise D.GateError("The final retainage release is billed once the work is finished. Use Final pay app first, or bill it at substantial completion.")
        if m["unbilled_completed_cents"] > 0:
            raise D.GateError(f"{D.money(m['unbilled_completed_cents'])} of finished work is not billed. Bill it on the final pay app first.")
        missing = D.closeout_missing(job)
        if missing:
            raise D.GateError("The closeout documents aren't complete.", [D._blocker_text(job, item) for item in missing])
    D._need_playbook(state, job)
    runs_to = D.period_to(job, period_end)
    number = D._next_number(job)
    invoice_id = f"inv-{job['id']}-{len(job['invoices']) + 1}"
    packet = D._packet_for_pay_app(state, job, invoice_id, number)
    partial = amount < held
    label = (f"Retainage release {RELEASE_STAGES[stage]}: {D.money(amount)} of the {D.money(held)} held" if partial
             else f"Retainage release {RELEASE_STAGES[stage]}: all retainage held")
    sheet = _standing_sheet(job)
    if not partial:
        for entry in sheet:   # nothing is held on any line after it
            entry["retainage_pct"] = 0
    invoice = {
        "id": invoice_id, "number": number, "amount_cents": amount, "gross_cents": 0, "retainage_cents": -amount,
        "status": "draft", "lines": [{"label": label, "amount_cents": amount, "retainage_release": True}],
        "created_at": D.utcnow(), "sent_at": None, "approved_by": None, "packet": packet,
        "packet_id": invoice_id if packet is not job.get("packet") else "job-packet",
        "sov_lines": [], "sov_sheet": sheet, "op_cents": 0, "retainage_pct": int(job.get("retainage_pct") or 0),
        "period_to": runs_to, "progress_notes": str(notes or "").strip()[:2000],
        "retainage_release": {"stage": stage, "amount_cents": amount, "held_cents": held},
    }
    if stage == "final":
        # The closeout documents go with it, as with the retainage request.
        invoice["extra_record_ids"] = list(dict.fromkeys(
            a["record_id"] for item in D._retainage_packet(job) for a in D._going(item) if a.get("record_id")))
    job["invoices"].append(invoice)
    invoice["final"] = release_is_final(job, invoice)
    if invoice["packet_id"] != "job-packet":
        D._name_conditional_waiver(job, invoice)
    blockers = D.blocker_lines(job)
    invoice["status"] = "draft" if blockers else "awaiting_approval"
    D.audit(state, job_id, "draft_retainage_release",
            f"{actor} drafted {number}, a retainage release {RELEASE_STAGES[stage]}, for {D.money(amount)}"
            + (f" of the {D.money(held)} held" if partial else "") + f". {len(blockers)} to fix before it can go.", actor)
    return job


def release_view(job: dict[str, Any]) -> dict[str, Any]:
    """What the job page's Bill retainage release card reads."""
    D = _d()
    m = D.job_money(job)
    drafted = release_drafted_cents(job)
    return {
        "held_cents": m["retainage_open_cents"], "drafted_cents": drafted,
        "open_draft": bool(next((inv for inv in job["invoices"] if inv["status"] in D.DRAFT_STATUSES), None)),
        "final_ready": job.get("work_status") == "complete" and m["unbilled_completed_cents"] == 0 and not D.closeout_missing(job),
        "work_done": job.get("work_status") == "complete",
    }


# ---------- stored materials backup ----------

def _earlier_stored(job: dict[str, Any], invoice: dict[str, Any]) -> dict[str, int]:
    """Materials stored per line on the last pay app before this one."""
    out: dict[str, int] = {}
    for inv in job.get("invoices", []):
        if inv is invoice or inv.get("id") == invoice.get("id"):
            break
        if inv.get("status") in sov.LIVE_STATUSES:
            for entry in inv.get("sov_sheet") or []:
                out[entry["line_id"]] = int(entry.get("stored_cents") or 0)
    return out


def stored_new_lines(job: dict[str, Any], invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """The lines on which this pay app bills new stored materials: [{line_id, name, new_cents, stored_cents}]."""
    D = _d()
    before = _earlier_stored(job, invoice)
    out = []
    for entry in invoice.get("sov_sheet") or []:
        stored = int(entry.get("stored_cents") or 0)
        new = stored - before.get(entry["line_id"], 0)
        if new > 0:
            out.append({"line_id": entry["line_id"], "name": D.sov_line_name(entry), "new_cents": new, "stored_cents": stored})
    return out


def _stored_item(invoice: dict[str, Any]) -> dict[str, Any] | None:
    D = _d()
    return next((item for item in invoice.get("packet") or [] if (D._record_kinds_for(item) or ("",))[0] == "stored"), None)


def add_stored_item(job: dict[str, Any], invoice: dict[str, Any]) -> bool:
    """A draft billing new stored materials asks for their backup (one item, covering those lines);
    returns whether a required one is now waiting. One added here comes off when no line has new
    materials any more; one from the job's list stays, asking whether any came in."""
    D = _d()
    if invoice.get("packet_id") == "job-packet":
        return False
    rows = stored_new_lines(job, invoice)
    packet = invoice.setdefault("packet", [])
    item = _stored_item(invoice)
    if not rows:
        if item and item.get("auto_stored"):
            packet.remove(item)
        elif item:
            for key in ("covers", "stored_lines", "stored_short"):
                item.pop(key, None)
        return False
    if not item:
        item = D._packet_item(invoice["id"], "stored", "Stored materials documents", "Stored materials documents are missing.")
        item["auto_stored"] = True
        packet.append(item)
    item["covers"] = [r["name"] for r in rows]
    item["stored_lines"] = [r["line_id"] for r in rows]
    return bool(item.get("required")) and not item.get("present")


def _backed_lines(job: dict[str, Any], item: dict[str, Any]) -> set[str]:
    D = _d()
    records = {r["id"]: r for r in job.get("records", [])}
    lines = list(item.get("stored_lines") or [])
    out: set[str] = set()
    for a in D._going(item):
        rec = records.get(a.get("record_id")) or {}
        if not rec.get("file_ids"):
            continue
        if rec.get("sov_line_id") in lines:
            out.add(rec["sov_line_id"])
        elif not rec.get("sov_line_id") and len(lines) == 1:
            out.add(lines[0])
    return out


def stored_backup_follows(job: dict[str, Any], invoice: dict[str, Any] | None) -> None:
    """The stored materials item is in only when each line it covers has its backup (an override
    or "I have this" stands on its own)."""
    if not invoice:
        return
    item = _stored_item(invoice)
    if not item or not item.get("stored_lines") or item.get("override") or item.get("marked_in_hand"):
        if item:
            item.pop("stored_short", None)
        return
    names = dict(zip(item["stored_lines"], item.get("covers") or []))
    short = [names.get(lid, lid) for lid in item["stored_lines"] if lid not in _backed_lines(job, item)]
    if short:
        item["stored_short"] = short
        item["blocker"] = f"Stored materials documents: add the supplier invoice, receipt or photo for {', '.join(short)}."
        item["present"] = False
    else:
        item.pop("stored_short", None)
        item["blocker"] = "Stored materials documents are missing."


def stored_backup_rows(job: dict[str, Any], invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """Each line with new stored materials on a pay app, with the backup tied to it, for its page."""
    item = _stored_item(invoice)
    rows = stored_new_lines(job, invoice)
    if not rows:
        return []
    records = [r for r in job.get("records", []) if r.get("kind") == "stored" and r.get("file_ids")]
    backed = _backed_lines(job, item) if item else set()
    out = []
    for row in rows:
        tied = [{"id": r["id"], "label": r["label"]} for r in records if r.get("sov_line_id") == row["line_id"]]
        out.append({**row, "records": tied, "ok": row["line_id"] in backed, "item_id": (item or {}).get("id", "")})
    return out


# ---------- what the app reads ----------

def payapp_core_view(state: dict[str, Any], job: dict[str, Any], out: dict[str, Any]) -> None:
    """Added to job_view: each pay app's stored materials backup and figures for an earlier one,
    the earlier pay apps, and the retainage release card."""
    D = _d()
    by_id = {inv["id"]: inv for inv in job["invoices"]}
    files = {f["id"]: f for f in state.get("files", [])}
    records = {r["id"]: r for r in job.get("records", [])}
    for inv in out.get("invoices", []):
        real = by_id.get(inv["id"])
        if not real or real.get("status") == "void":
            continue
        rows = stored_backup_rows(job, real)
        if rows:
            inv["stored_backup"] = rows
        if real.get("imported"):
            inv["history_files"] = [{"record_id": rid, "label": records[rid]["label"], "file_ids": [f for f in records[rid].get("file_ids") or [] if f in files]}
                                    for rid in real.get("history_record_ids") or [] if rid in records]
    earlier = imported_invoices(job)
    out["earlier_payapps"] = {
        "count": len(earlier),
        "can_enter": not job.get("closed") and not any(inv["status"] != "void" and not inv.get("imported") for inv in job["invoices"]),
        "next_number": D._next_number(job) if not job.get("closed") else "",
    }
    if not job.get("closed"):
        out["retainage_release"] = release_view(job)
