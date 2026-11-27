"""The client's own forms ("use the GC's own forms", Siteline comparison, item 1): many general
contractors send back a pay app that isn't on their own form. A client's blank form is uploaded
once per form type (pay app cover, continuation sheet, each lien waiver), each of its fields is
matched to a Duracash value, and that match is kept as the client's form template. Every month the
pay app is written onto the form; the email carries the client's form instead of, or with,
Duracash's PDF (a setting on the client).

Two kinds of form:
- Fillable (an AcroForm): its fields are read (pdf_forms.read) and each is matched to a value.
  Filling writes the values as an incremental update with plain appearance streams.
- Not fillable: values are placed by position. Rendering a PDF page in the browser would need a
  PDF library, so the page isn't drawn there. Instead the person types where each value goes, in
  inches from the page's top left corner, and opens a preview: the form with sample values stamped
  on it and a half-inch grid numbered in inches, to read the next position off. The values are
  drawn by a content stream added as an incremental update, so the form itself is untouched.

A template is versioned: a new blank of the same type is a new version, the old one is kept
(retired), and matches for fields that still exist carry over. Duracash's team can set a client's
forms up for them; a note on the template says so (set_note, only the Duracash team).

Filling checks first (check): every matched field gets a value unless it is marked "may be blank",
money and dates are written the way the template says, and a pay app with more lines than the
form has rows is refused rather than cut short. The send is refused on a problem
(send_problems), so a pay app never goes out on a half-filled form.

Waivers on the client's form are filled for printing and signing, never emailed: Duracash never
sends an unsigned waiver (waiver_form.py).

The state lives under state["gc_forms"]: {"templates": [...], "settings": {payer id: {...}}}. It
isn't in the page load (domain.light_snapshot); the client card and the pay app page ask for it.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, timedelta
from typing import Any

import domain
import importer
import payapp_pdf
import pdf_forms
import sov

FORM_TYPES = {
    "payapp": "Pay app (cover page)",
    "continuation": "Continuation sheet",
    **{k: v for k, v in domain.WAIVER_KINDS.items()},
}
PAYAPP_TYPES = ("payapp", "continuation")
WAIVER_TYPES = tuple(domain.WAIVER_KINDS)

SEND_MODES = {
    "ours": "Duracash's pay app PDF only",
    "theirs": "The client's own form only",
    "both": "Both: the client's form and Duracash's PDF",
}
DEFAULT_SEND = "both"
# The emails that carry the client's form: the pay app itself. Its draft sent for review (a pencil
# copy) goes on Duracash's PDF, so a form not finished yet never fails that email (Nov 27).
EMAIL_KINDS = ("pay_app",)
MONEY_FORMATS = {"dollars": "$1,234.56", "commas": "1,234.56", "plain": "1234.56"}
DATE_FORMATS = {"long": "November 27, 2026", "us": "11/27/2026", "iso": "2026-11-27"}

MAX_MAPPED = 3000
MAX_PLACED = 400
MAX_ROW = 500
MAX_TEXT = 300
MAX_NOTE = 2000
MAX_VERSIONS = 50   # kept per client and type; older ones are dropped with their files

# (key, label, kind, group). kind: money, date, text, pct, number, flag.
SOURCES: list[tuple[str, str, str, str]] = [
    ("project_name", "Project name", "text", "Project"),
    ("project_address", "Project address", "text", "Project"),
    ("job_code", "Job number (yours)", "text", "Project"),
    ("contract_number", "Contract number", "text", "Project"),
    ("owner", "Owner", "text", "Project"),
    ("client", "Client (who you bill)", "text", "Project"),
    ("client_contact", "Who signs off at the client", "text", "Project"),
    ("contractor", "Your company name", "text", "Your company"),
    ("contractor_address", "Your company address", "text", "Your company"),
    ("contractor_phone", "Your company phone", "text", "Your company"),
    ("contractor_license", "Your license number", "text", "Your company"),
    ("application_number", "Pay app number (as Duracash numbers it)", "text", "Pay app"),
    ("application_count", "Pay app number (1, 2, 3...)", "number", "Pay app"),
    ("application_date", "Pay app date", "date", "Pay app"),
    ("period_from", "Period from", "date", "Pay app"),
    ("period_to", "Period to", "date", "Pay app"),
    ("payment_due", "Payment due date", "date", "Pay app"),
    ("final", "Final pay app (tick)", "flag", "Pay app"),
    ("original_contract", "1. Original contract sum", "money", "Summary"),
    ("net_change_orders", "2. Net change by change orders", "money", "Summary"),
    ("contract_to_date", "3. Contract sum to date", "money", "Summary"),
    ("completed_stored", "4. Total completed and stored to date", "money", "Summary"),
    ("retainage_pct", "Retainage percent", "pct", "Summary"),
    ("retainage_total", "5. Retainage to date", "money", "Summary"),
    ("retainage_work", "5a. Retainage on completed work", "money", "Summary"),
    ("retainage_stored", "5b. Retainage on stored materials", "money", "Summary"),
    ("earned_less_retainage", "6. Total earned less retainage", "money", "Summary"),
    ("previous_certificates", "7. Less previous certificates for payment", "money", "Summary"),
    ("current_due", "8. Current payment due", "money", "Summary"),
    ("balance_to_finish", "9. Balance to finish, including retainage", "money", "Summary"),
    ("stored_total", "Materials presently stored", "money", "Summary"),
    ("co_additions", "Change orders: additions", "money", "Summary"),
    ("co_deductions", "Change orders: deductions", "money", "Summary"),
    ("line.item", "Line: item number", "text", "Continuation line (pick the row)"),
    ("line.description", "Line: description of work", "text", "Continuation line (pick the row)"),
    ("line.scheduled", "Line: scheduled value", "money", "Continuation line (pick the row)"),
    ("line.previous", "Line: from previous applications", "money", "Continuation line (pick the row)"),
    ("line.this_period", "Line: work this period", "money", "Continuation line (pick the row)"),
    ("line.stored", "Line: materials presently stored", "money", "Continuation line (pick the row)"),
    ("line.to_date", "Line: completed and stored to date", "money", "Continuation line (pick the row)"),
    ("line.pct", "Line: percent complete", "pct", "Continuation line (pick the row)"),
    ("line.balance", "Line: balance to finish", "money", "Continuation line (pick the row)"),
    ("line.retainage", "Line: retainage", "money", "Continuation line (pick the row)"),
    ("total.scheduled", "Totals: scheduled value", "money", "Continuation totals"),
    ("total.previous", "Totals: from previous applications", "money", "Continuation totals"),
    ("total.this_period", "Totals: work this period", "money", "Continuation totals"),
    ("total.stored", "Totals: materials stored", "money", "Continuation totals"),
    ("total.to_date", "Totals: completed and stored to date", "money", "Continuation totals"),
    ("total.balance", "Totals: balance to finish", "money", "Continuation totals"),
    ("total.retainage", "Totals: retainage", "money", "Continuation totals"),
    ("waiver_amount", "Waiver: amount", "money", "Lien waiver"),
    ("waiver_through", "Waiver: work through (date)", "date", "Lien waiver"),
    ("today", "Today's date", "date", "Other"),
    ("text", "Fixed text (type it)", "text", "Other"),
    ("checked", "Always ticked", "flag", "Other"),
]
SOURCE = {k: (label, kind, group) for k, label, kind, group in SOURCES}
LINE_COLUMNS = ("item", "description", "scheduled", "previous", "this_period", "stored", "to_date", "pct", "balance", "retainage")


class Forbidden(Exception):
    """Only the Duracash team may do this: answered with 403."""


# ---------- state ----------

def _store(state: dict[str, Any]) -> dict[str, Any]:
    gc = state.setdefault("gc_forms", {})
    gc.setdefault("templates", [])
    gc.setdefault("settings", {})
    return gc


def templates(state: dict[str, Any], payer_id: str, form_type: str | None = None, active_only: bool = True) -> list[dict]:
    out = [t for t in (state.get("gc_forms") or {}).get("templates", []) if t.get("payer_id") == payer_id]
    if form_type:
        out = [t for t in out if t.get("form_type") == form_type]
    if active_only:
        out = [t for t in out if not t.get("retired_at")]
    return out


def active(state: dict[str, Any], payer_id: str, form_type: str) -> dict | None:
    live = templates(state, payer_id, form_type)
    return max(live, key=lambda t: t.get("version", 0)) if live else None


def send_mode(state: dict[str, Any], payer_id: str) -> str:
    mode = ((state.get("gc_forms") or {}).get("settings", {}).get(payer_id) or {}).get("send")
    return mode if mode in SEND_MODES else DEFAULT_SEND


def _template(state: dict[str, Any], payer_id: str, tpl_id: str) -> dict:
    t = next((t for t in templates(state, payer_id, active_only=False) if t.get("id") == tpl_id), None)
    if not t:
        raise domain.GateError("That form isn't set up for this client.")
    return t


def file_metas(state: dict[str, Any]) -> list[dict]:
    """Every client blank form kept, as file metas (importer.file_path, exports): every version,
    retired ones too, as the history keeps them. They count toward the company's storage like any
    upload (importer.store_bytes) and go in the export with files; none is sensitive."""
    return [{"id": t["file_id"], "name": t.get("file_name") or "form.pdf", "ext": ".pdf", "size": t.get("size", 0), "client_form": True}
            for t in (state.get("gc_forms") or {}).get("templates", []) if t.get("file_id")]


def blank_bytes(company_id: str, tpl: dict) -> bytes:
    """The client's blank form as uploaded. One place reads it, so a change to how uploads are
    kept on disk changes only this."""
    return importer.file_path(company_id, {"id": tpl["file_id"], "ext": ".pdf"}).read_bytes()


# ---------- reading an upload ----------

def read_upload(data: bytes) -> pdf_forms.FormInfo:
    """The blank form's pages and fields; a GateError with the reason when it can't be used.
    Called before the company's lock: a big form keeps nobody else waiting."""
    try:
        return pdf_forms.read(data)
    except pdf_forms.PdfError as exc:
        raise domain.GateError(str(exc))
    except (RecursionError, MemoryError, OverflowError, IndexError, KeyError, TypeError, ValueError):
        raise domain.GateError(pdf_forms.DAMAGED)


def _norm(name: str) -> str:
    return " ".join(re.sub(r"([a-z])([A-Z])", r"\1 \2", re.sub(r"[^A-Za-z0-9%]+", " ", name)).lower().split())


# Field names, read as words, and the value each usually is. The first that fits is taken.
_GUESSES: list[tuple[str, str]] = [
    (r"original contract|contract sum original|^original|^contract (sum|amount)$", "original_contract"),
    (r"net change", "net_change_orders"),
    (r"contract sum to date|contract to date|adjusted contract", "contract_to_date"),
    (r"less retainage|earned less|total earned", "earned_less_retainage"),
    (r"total completed|completed (and|&)? ?stored|completed to date", "completed_stored"),
    (r"previous (certificates|payments|applications|billings)|less previous", "previous_certificates"),
    (r"current payment|payment due|amount due|current due|this payment", "current_due"),
    (r"balance to finish", "balance_to_finish"),
    (r"retainage (%|percent|pct)|retention (%|percent|pct)", "retainage_pct"),
    (r"total retainage|retainage|retention", "retainage_total"),
    (r"stored", "stored_total"),
    (r"application (no|number|#)|app (no|number|#)|^application$|pay app (no|number)", "application_count"),
    (r"period (to|ending|end)|to period|through", "period_to"),
    (r"period from|from period", "period_from"),
    (r"application date|app date|^date$", "application_date"),
    (r"project (name|title)|^project$|job name", "project_name"),
    (r"project (address|location)|job (address|location)|location", "project_address"),
    (r"contract (no|number|#)", "contract_number"),
    (r"^owner|owner name", "owner"),
    (r"subcontractor|^contractor|vendor|claimant|from", "contractor"),
    (r"waiver amount|amount of|sum of|^amount$", "waiver_amount"),
]
_COLUMN_GUESSES: list[tuple[str, str]] = [
    (r"desc", "description"), (r"\bitem\b|item no", "item"), (r"scheduled|sched|value", "scheduled"),
    (r"previous|prev|from prior", "previous"), (r"this period|this per|current|this app", "this_period"),
    (r"stored|material", "stored"), (r"%|percent|pct", "pct"), (r"completed|to date|total", "to_date"),
    (r"balance|finish", "balance"), (r"retain|retention", "retainage"),
]


def guess(name: str, kind: str) -> dict | None:
    """A first match for a field from its name, marked as a guess for a person to check."""
    if kind in ("signature", "button"):
        return None
    words = _norm(name)
    if kind in ("checkbox", "radio"):
        return {"source": "final", "guessed": True} if "final" in words else None
    numbers = re.findall(r"\d+", words)
    # A continuation line: a column's words and a row number, said as a row, line or item, or
    # ending the name ("Description3", "Scheduled_12"), never a summary line's "5a".
    if numbers and (re.search(r"\b(row|line|item)\b", words) or re.search(r"\d+$", words)):
        row = int(numbers[-1])
        for pattern, column in _COLUMN_GUESSES:
            if 1 <= row <= MAX_ROW and re.search(pattern, words):
                return {"source": f"line.{column}", "row": row, "guessed": True}
    for pattern, source in _GUESSES:
        if re.search(pattern, words):
            return {"source": source, "guessed": True}
    return None


# ---------- templates ----------

def _clean_fields(info: pdf_forms.FormInfo) -> list[dict]:
    return [{k: f[k] for k in ("name", "kind", "page", "rect", "max_len", "on")} | ({"options": f["options"]} if f.get("options") else {})
            for f in info.fields]


def add_template(state: dict[str, Any], payer_id: str, form_type: str, meta: dict, info: pdf_forms.FormInfo, actor: str,
                 duracash: bool = False) -> dict:
    """A client's blank form, as a new version of its type. Matches for fields that are still on
    the new version carry over; the rest are guessed from the fields' names."""
    payer = domain._payer(state, payer_id)
    if form_type not in FORM_TYPES:
        raise domain.GateError("Pick which form this is.")
    gc = _store(state)
    before = active(state, payer_id, form_type)
    if before and before.get("sha256") == meta.get("sha256"):
        raise domain.GateError(f"That is the same file as version {before['version']} of {payer['name']}'s {FORM_TYPES[form_type].lower()}.")
    fields = _clean_fields(info)
    names = {f["name"] for f in fields}
    kinds = {f["name"]: f["kind"] for f in fields}
    carried = [m for m in (before or {}).get("mapping", []) if m.get("field") in names]
    have = {m["field"] for m in carried}
    guessed = [{"field": f["name"], **g} for f in fields if f["name"] not in have for g in [guess(f["name"], kinds[f["name"]])] if g]
    pages = info.pages
    placed = [p for p in (before or {}).get("placed", []) if p.get("page", 1) <= len(pages)]
    version = 1 + max((t.get("version", 0) for t in templates(state, payer_id, form_type, active_only=False)), default=0)
    now = domain.utcnow()
    tpl = {
        "id": "gcf-" + secrets.token_hex(5), "payer_id": payer_id, "form_type": form_type, "version": version,
        "file_id": meta["id"], "file_name": meta["name"], "size": meta.get("size", 0), "sha256": meta.get("sha256", ""),
        "pages": pages, "fields": fields, "fillable": info.fillable, "notes": info.notes,
        "mapping": carried + guessed, "placed": placed,
        "money_format": (before or {}).get("money_format", "dollars"), "date_format": (before or {}).get("date_format", "us"),
        "added_at": now, "added_by": actor,
    }
    if duracash:
        tpl["duracash_note"], tpl["duracash_by"], tpl["duracash_at"] = "Set up by Duracash.", actor, now
    if before:
        before["retired_at"], before["retired_by"] = now, actor
    gc["templates"].append(tpl)
    _prune(state, payer_id, form_type)
    words = f"version {version}" + (f", replacing version {before['version']}" if before else "")
    domain.audit(state, "", "gc_form_added", f"{actor} added {payer['name']}'s {FORM_TYPES[form_type].lower()} ({meta['name']}, {words}).", actor)
    return tpl


def _prune(state: dict[str, Any], payer_id: str, form_type: str) -> None:
    every = sorted(templates(state, payer_id, form_type, active_only=False), key=lambda t: t.get("version", 0))
    for old in every[:-MAX_VERSIONS]:
        state["gc_forms"]["templates"].remove(old)


def _text(v: Any, limit: int = MAX_TEXT) -> str:
    return str(v).strip()[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def _entry(raw: Any, where: str) -> dict:
    """One match from the page: its value's source, the row for a continuation line, the fixed text."""
    if not isinstance(raw, dict):
        raise domain.GateError("That wasn't understood. Reload the page and try again.")
    source = _text(raw.get("source"), 40)
    if source not in SOURCE:
        raise domain.GateError(f"Pick a Duracash value for {where}.")
    out: dict[str, Any] = {"source": source}
    if source.startswith("line."):
        row = raw.get("row")
        try:
            row = int(str(row).strip())
        except (TypeError, ValueError):
            row = 0
        if not 1 <= row <= MAX_ROW:
            raise domain.GateError(f"Enter which line {where} is for, from 1 to {MAX_ROW}.")
        out["row"] = row
    if source == "text":
        text = _text(raw.get("text"))
        if not text:
            raise domain.GateError(f"Type the fixed text for {where}.")
        out["text"] = text
    if raw.get("optional") is True:
        out["optional"] = True
    return out


def save_mapping(state: dict[str, Any], payer_id: str, tpl_id: str, body: dict, actor: str) -> dict:
    """The matches a person checked: field name -> value, values placed by position, and how
    money and dates are written. Replaces what the template had."""
    payer = domain._payer(state, payer_id)
    tpl = _template(state, payer_id, tpl_id)
    if tpl.get("retired_at"):
        raise domain.GateError("That is an older version of the form. Set up the current one.")
    names = {f["name"]: f for f in tpl.get("fields", [])}
    mapping_in = body.get("mapping") or []
    placed_in = body.get("placed") or []
    if not isinstance(mapping_in, list) or not isinstance(placed_in, list) or len(mapping_in) > MAX_MAPPED or len(placed_in) > MAX_PLACED:
        raise domain.GateError("That wasn't understood. Reload the page and try again.")
    mapping, seen = [], set()
    for raw in mapping_in:
        if not isinstance(raw, dict):
            raise domain.GateError("That wasn't understood. Reload the page and try again.")
        name = raw.get("field")
        if not isinstance(name, str) or name not in names:
            raise domain.GateError("A field that isn't on this form was sent. Reload the page and try again.")
        if name in seen or not raw.get("source"):
            continue
        seen.add(name)
        entry = _entry(raw, f"the field {name}")
        if names[name]["kind"] in ("signature", "button"):
            raise domain.GateError(f"{name} is a {names[name]['kind']} field; Duracash leaves it for the person signing.")
        mapping.append({"field": name, **entry})
    placed = []
    for n, raw in enumerate(placed_in, start=1):
        where = f"placed value {n}"
        entry = _entry(raw, where)
        try:
            page = int(raw.get("page"))
            x, y = float(raw.get("x")), float(raw.get("y"))
            size = float(raw.get("size") or 10)
        except (TypeError, ValueError):
            raise domain.GateError(f"Enter the page and where on it for {where}, in inches from the top left.")
        if not 1 <= page <= len(tpl.get("pages", [])):
            raise domain.GateError(f"The form has {len(tpl.get('pages', []))} page(s); pick one of them for {where}.")
        dims = tpl["pages"][page - 1]
        if not (0 <= x <= dims["width"] and 0 <= y <= dims["height"]):
            raise domain.GateError(f"{where.capitalize()} is off the page: the page is {dims['width'] / 72:g} by {dims['height'] / 72:g} inches.")
        align = raw.get("align") if raw.get("align") in ("left", "right", "center") else "left"
        placed.append({**entry, "page": page, "x": round(x, 2), "y": round(y, 2), "size": max(4.0, min(36.0, size)), "align": align})
    money_format = body.get("money_format") if body.get("money_format") in MONEY_FORMATS else tpl.get("money_format", "dollars")
    date_format = body.get("date_format") if body.get("date_format") in DATE_FORMATS else tpl.get("date_format", "us")
    tpl.update(mapping=mapping, placed=placed, money_format=money_format, date_format=date_format,
               updated_at=domain.utcnow(), updated_by=actor)
    domain.audit(state, "", "gc_form_matched", f"{actor} matched {len(mapping) + len(placed)} value(s) on {payer['name']}'s "
                 f"{FORM_TYPES[tpl['form_type']].lower()} (version {tpl['version']}).", actor)
    return tpl


def set_note(state: dict[str, Any], payer_id: str, tpl_id: str, note: str, actor: str) -> dict:
    """Duracash's team: who set the form up, and anything to know about it."""
    payer = domain._payer(state, payer_id)
    tpl = _template(state, payer_id, tpl_id)
    note = _text(note, MAX_NOTE)
    if note:
        tpl.update(duracash_note=note, duracash_by=actor, duracash_at=domain.utcnow())
    else:
        for k in ("duracash_note", "duracash_by", "duracash_at"):
            tpl.pop(k, None)
    domain.audit(state, "", "gc_form_note", f"Duracash ({actor}) {'noted' if note else 'cleared the note on'} {payer['name']}'s {FORM_TYPES[tpl['form_type']].lower()}.", actor)
    return tpl


def set_send(state: dict[str, Any], payer_id: str, mode: str, actor: str) -> None:
    payer = domain._payer(state, payer_id)
    if mode not in SEND_MODES:
        raise domain.GateError("Pick what the pay app email carries.")
    _store(state)["settings"].setdefault(payer_id, {})["send"] = mode
    domain.audit(state, "", "gc_form_send", f"{actor} set {payer['name']}'s pay app emails to carry {SEND_MODES[mode][0].lower() + SEND_MODES[mode][1:]}.", actor)


def retire(state: dict[str, Any], payer_id: str, tpl_id: str, actor: str) -> None:
    """Stop using a form (kept in the history, never deleted here)."""
    payer = domain._payer(state, payer_id)
    tpl = _template(state, payer_id, tpl_id)
    if tpl.get("retired_at"):
        return
    tpl["retired_at"], tpl["retired_by"] = domain.utcnow(), actor
    domain.audit(state, "", "gc_form_retired", f"{actor} stopped using {payer['name']}'s {FORM_TYPES[tpl['form_type']].lower()} (version {tpl['version']}).", actor)


# ---------- the values ----------

def _day(iso: Any) -> date | None:
    try:
        return date.fromisoformat(str(iso or "")[:10])
    except ValueError:
        return None


def sheet_rows(job: dict[str, Any], invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """The continuation sheet's rows as payapp_pdf prints them: each schedule line, then overhead
    and profit, then change orders billed unsigned; or the pay app's own lines without a schedule."""
    sheet = invoice.get("sov_sheet") or invoice.get("sov_lines") or []
    pct_ret = int(invoice.get("retainage_pct", job.get("retainage_pct")) or 0)
    rows: list[dict[str, Any]] = []
    if not sheet:
        for line in invoice.get("lines", []):
            rows.append({"item": "", "description": line.get("label", ""), "scheduled": None, "previous": None,
                         "this_period": line.get("amount_cents", 0), "stored": None, "to_date": None, "pct": None,
                         "balance": None, "retainage": None})
        return rows
    for e in sheet:
        c = sov.sheet_columns(e, pct_ret)
        rows.append({"item": str(e.get("item", "")), "description": e.get("description", ""), **c})
    terms = payapp_pdf.terms_for(job, invoice)
    bp = terms["op_bp"]
    if bp:
        live = [inv for inv in job.get("invoices", []) if inv.get("status") in payapp_pdf.LIVE]
        earlier = live[: live.index(invoice)] if invoice in live else live
        prev = sum(inv.get("op_cents", 0) for inv in earlier)
        this = invoice.get("op_cents", 0)
        sched = terms["op_scheduled_cents"]
        to = prev + this
        rows.append({"item": "", "description": f"Overhead and profit {bp / 100:g}%", "scheduled": sched, "previous": prev,
                     "this_period": this, "stored": 0, "to_date": to, "pct": (100 * to) // sched if sched else 0,
                     "balance": sched - to, "retainage": sov.retainage_on(to, pct_ret)})
    unsigned = {co["id"] for co in terms["pending_cos"]}
    for row in sov.co_rows(job, invoice):
        sched, to = row["scheduled_cents"], row["to_date_cents"]
        rows.append({"item": "", "description": f"CO {row['number']}" + (" (unsigned)" if row["co_id"] in unsigned else ""),
                     "scheduled": sched, "previous": row["previous_cents"], "this_period": row["this_cents"], "stored": 0,
                     "to_date": to, "pct": (100 * to) // sched if sched else 0, "balance": sched - to,
                     "retainage": sov.retainage_on(to, pct_ret)})
    return rows


def _application_no(invoice: dict[str, Any], fallback: int) -> int:
    """The pay app's own number as a plain number (PA-PINE-05 is 5): a number pulled back isn't used
    again, and earlier pay apps entered as one summary stand for all those before it, so counting
    the pay apps on the job would put another number on the client's form than on the pay app."""
    m = re.search(r"(\d+)\s*$", str(invoice.get("number") or ""))
    return int(m.group(1)) if m else fallback


def _change_orders_on(job: dict[str, Any], invoice: dict[str, Any]) -> list[int]:
    """The change orders in line 2 of this pay app, as payapp_pdf.summary counts them: those signed
    by the time it went (a later one is on the next pay app), and those billed on it or before
    while still unsigned. Their additions less their deductions is the net change on the form."""
    at = invoice.get("sent_at") or (invoice.get("voided_at") if invoice.get("status") == "void" else None)
    terms = payapp_pdf.terms_for(job, invoice)
    pending = {co["id"] for co in terms["pending_cos"]}
    approved = [co["amount_cents"] for co in job.get("change_orders", []) if co.get("status") == "approved"
                and co["id"] not in pending and (at is None or not co.get("approved_at") or str(co["approved_at"]) <= at)]
    order = job.get("invoices", [])
    position = next((i for i, inv in enumerate(order) if inv is invoice or inv.get("id") == invoice.get("id")), len(order))
    unsigned: dict[str, int] = {}
    for inv in [inv for inv in order[:position] if inv.get("status") in payapp_pdf.LIVE] + [invoice]:
        for line in inv.get("lines", []):
            if line.get("co_id") in pending:
                unsigned[line["co_id"]] = line["amount_cents"]
    return approved + list(unsigned.values())


def payapp_values(state: dict[str, Any], job: dict[str, Any], invoice: dict[str, Any], today: date | None = None) -> tuple[dict, list[dict]]:
    """Every Duracash value for one pay app: (values by source, continuation rows)."""
    today = today or date.today()
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    company = state.get("company", {})
    figures = payapp_pdf.summary(job, invoice)
    rows = sheet_rows(job, invoice)
    live = [inv for inv in job.get("invoices", []) if inv.get("status") in payapp_pdf.LIVE or inv is invoice]
    position = next((i for i, inv in enumerate(live) if inv.get("id") == invoice.get("id")), len(live) - 1)
    earlier = [inv for inv in live[:position] if inv.get("period_to")]
    start = _day(earlier[-1]["period_to"]) + timedelta(days=1) if earlier and _day(earlier[-1]["period_to"]) else _day(job.get("started_on"))
    owner_client = domain.client_type_role(payer.get("client_type")) == "Owner" if payer.get("client_type") else payer.get("role") == "Owner"
    approved = _change_orders_on(job, invoice)
    pct_ret = int(invoice.get("retainage_pct", job.get("retainage_pct")) or 0)
    v: dict[str, Any] = {
        "project_name": job.get("name", ""), "project_address": job.get("address", ""), "job_code": job.get("code", ""),
        "contract_number": job.get("contract_number", ""),
        "owner": payer.get("name", "") if owner_client else str(job.get("owner_name") or ""),
        "client": payer.get("name", ""), "client_contact": payer.get("approver", ""),
        "contractor": company.get("name", ""), "contractor_address": company.get("address", ""),
        "contractor_phone": company.get("phone", ""), "contractor_license": company.get("license", ""),
        "application_number": invoice.get("number", ""), "application_count": _application_no(invoice, position + 1),
        "application_date": _day(invoice.get("sent_at")) or today,
        "period_from": start, "period_to": _day(invoice.get("period_to")), "payment_due": _day(invoice.get("due_on")),
        "final": bool(invoice.get("final")),
        "original_contract": figures["original"], "net_change_orders": figures["contract_to_date"] - figures["original"],
        "contract_to_date": figures["contract_to_date"], "completed_stored": figures["completed"],
        "retainage_pct": pct_ret, "retainage_total": figures["retainage"], "earned_less_retainage": figures["earned"],
        "previous_certificates": figures["previous"], "current_due": figures["due"], "balance_to_finish": figures["balance"],
        # G702 5a and 5b, and column F's total, as the PDF prints them (payapp_pdf.summary, payapp-core).
        "retainage_work": figures.get("retainage_work", figures["retainage"]), "retainage_stored": figures.get("retainage_stored", 0),
        "stored_total": figures.get("stored", sum(r["stored"] or 0 for r in rows)),
        "co_additions": sum(a for a in approved if a > 0), "co_deductions": -sum(a for a in approved if a < 0),
        "today": today, "checked": True,
    }
    for col in ("scheduled", "previous", "this_period", "stored", "to_date", "balance", "retainage"):
        v[f"total.{col}"] = sum(r[col] or 0 for r in rows) if any(r[col] is not None for r in rows) else None
    # A lien waiver for this pay app, as the state's form would fill it in (waiver_form.prefill).
    import waiver_form
    try:
        amount = waiver_form.prefill(state, job, invoice.get("id", ""), "interim").get("amount", "")
        v["waiver_amount"] = round(float(amount.replace(",", "")) * 100) if amount else None
    except (ValueError, KeyError):
        v["waiver_amount"] = None
    v["waiver_through"] = _day(invoice.get("period_to"))
    return v, rows


SAMPLE_ROWS = [
    {"item": "1", "description": "Mobilization", "scheduled": 1_500_000, "previous": 1_500_000, "this_period": 0, "stored": 0,
     "to_date": 1_500_000, "pct": 100, "balance": 0, "retainage": 150_000},
    {"item": "2", "description": "Rough-in", "scheduled": 4_800_000, "previous": 1_200_000, "this_period": 1_800_000, "stored": 250_000,
     "to_date": 3_250_000, "pct": 67, "balance": 1_550_000, "retainage": 325_000},
    {"item": "3", "description": "Trim and finish", "scheduled": 3_700_000, "previous": 0, "this_period": 0, "stored": 0,
     "to_date": 0, "pct": 0, "balance": 3_700_000, "retainage": 0},
]


def sample_values(state: dict[str, Any], payer_id: str) -> tuple[dict, list[dict]]:
    """Made-up values for a preview before any pay app: what each kind of value looks like."""
    payer = next((p for p in state.get("payers", []) if p["id"] == payer_id), {})
    company = state.get("company", {})
    today = date.today()
    v: dict[str, Any] = {
        "project_name": "Sample Project", "project_address": "100 Main Street, Atlanta, GA 30303", "job_code": "J-100",
        "contract_number": "C-2041", "owner": "Sample Owner LLC", "client": payer.get("name", "Client"),
        "client_contact": payer.get("approver", "Sam Jones"), "contractor": company.get("name", "Your Company"),
        "contractor_address": company.get("address", "1 Shop Road"), "contractor_phone": company.get("phone", "(404) 555-0100"),
        "contractor_license": company.get("license", "LIC-1234"), "application_number": "PA-004", "application_count": 4,
        "application_date": today, "period_from": today.replace(day=1), "period_to": today, "payment_due": today + timedelta(days=30),
        "final": False, "original_contract": 10_000_000, "net_change_orders": 0, "contract_to_date": 10_000_000,
        "completed_stored": 4_750_000, "retainage_pct": 10, "retainage_total": 475_000, "earned_less_retainage": 4_275_000,
        "previous_certificates": 2_430_000, "current_due": 1_845_000, "balance_to_finish": 5_725_000, "stored_total": 250_000,
        "co_additions": 0, "co_deductions": 0, "waiver_amount": 1_845_000, "waiver_through": today, "today": today, "checked": True,
    }
    rows = SAMPLE_ROWS
    for col in ("scheduled", "previous", "this_period", "stored", "to_date", "balance", "retainage"):
        v[f"total.{col}"] = sum(r[col] for r in rows)
    return v, rows


def _money(cents: int, style: str) -> str:
    sign = "-" if cents < 0 else ""
    a = abs(int(cents))
    if style == "plain":
        return f"{sign}{a // 100}.{a % 100:02d}"
    text = f"{a // 100:,}.{a % 100:02d}"
    return f"{sign}${text}" if style == "dollars" else f"{sign}{text}"


def _date(d: date, style: str) -> str:
    if style == "iso":
        return d.isoformat()
    if style == "long":
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def _formatted(entry: dict, values: dict, rows: list[dict], tpl: dict) -> str | None:
    """The text for one match, or None when Duracash has no value for it."""
    source = entry["source"]
    if source == "text":
        return entry.get("text", "")
    kind = SOURCE[source][1]
    if source.startswith("line."):
        n = entry.get("row", 0)
        if n > len(rows):
            return ""   # a row of the form below the last line: left blank on purpose
        raw = rows[n - 1].get(source[5:])
    else:
        raw = values.get(source)
    if raw is None or raw == "":
        return None
    if kind == "money":
        return _money(raw, tpl.get("money_format", "dollars"))
    if kind == "date":
        return _date(raw, tpl.get("date_format", "us")) if isinstance(raw, date) else str(raw)
    if kind == "pct":
        return f"{raw}%" if tpl.get("money_format", "dollars") != "plain" else str(raw)
    if kind == "flag":
        return "Yes" if raw else "Off"
    return str(raw)


def check(tpl: dict, values: dict, rows: list[dict]) -> tuple[dict[str, str], list[pdf_forms.Placed], list[str]]:
    """What goes on the form: (field values, placed values, problems). A problem is a matched
    field with no value (and not marked may be blank), a value longer than its field takes, or a
    pay app with more lines than the form has rows."""
    problems: list[str] = []
    fields = {f["name"]: f for f in tpl.get("fields", [])}
    out: dict[str, str] = {}
    for entry in tpl.get("mapping", []):
        name = entry["field"]
        if name not in fields:
            continue
        text = _formatted(entry, values, rows, tpl)
        label = SOURCE[entry["source"]][0] + (f", line {entry['row']}" if entry.get("row") else "")
        if text is None:
            if not entry.get("optional"):
                problems.append(f"{name} ({label}) has no value from Duracash. Fill it in on the job, or mark the field May be blank.")
            continue
        if SOURCE[entry["source"]][1] == "flag":
            # A tick: the box's own on-state, or an X in a text field.
            out[name] = text if fields[name].get("kind") in ("checkbox", "radio") else ("X" if text == "Yes" else "")
            continue
        limit = fields[name].get("max_len") or 0
        if limit and len(text) > limit:
            problems.append(f"{name} takes at most {limit} characters; {label} is {text}. Pick a shorter way to write it.")
        out[name] = text
    placed: list[pdf_forms.Placed] = []
    for n, entry in enumerate(tpl.get("placed", []), start=1):
        text = _formatted(entry, values, rows, tpl)
        label = SOURCE[entry["source"]][0] + (f", line {entry['row']}" if entry.get("row") else "")
        if text is None:
            if not entry.get("optional"):
                problems.append(f"Placed value {n} ({label}) has no value from Duracash. Fill it in on the job, or mark it May be blank.")
            continue
        if SOURCE[entry["source"]][1] == "flag":
            text = "X" if text == "Yes" else ""
        placed.append(pdf_forms.Placed(entry["page"] - 1, entry["x"], entry["y"], text, entry.get("size", 10), entry.get("align", "left")))
    rows_on_form = max([e.get("row", 0) for e in tpl.get("mapping", []) + tpl.get("placed", []) if e["source"].startswith("line.")], default=0)
    if rows_on_form and len(rows) > rows_on_form:
        problems.append(f"This pay app has {len(rows)} continuation lines; the form is set up for {rows_on_form}. "
                        "Match more rows, or send Duracash's PDF instead.")
    return out, placed, problems


def _file_name(job: dict, invoice: dict | None, tpl: dict) -> str:
    base = f"{(invoice or {}).get('number') or job.get('code') or job.get('id')}-{tpl['form_type']}-client-form"
    return re.sub(r"[^A-Za-z0-9._-]", "-", base) + ".pdf"


def fill_template(company_id: str, tpl: dict, values: dict, rows: list[dict], grid: bool = False, boxes: bool = False) -> tuple[bytes, list[str]]:
    """(the filled form, problems). The form is filled even with problems, for a preview."""
    fields, placed, problems = check(tpl, values, rows)
    try:
        data = pdf_forms.fill(blank_bytes(company_id, tpl), fields, placed, grid=grid, sample_boxes=boxes)
    except pdf_forms.PdfError as exc:
        raise domain.GateError(str(exc))
    except OSError:
        raise domain.GateError("The client's blank form is missing from storage. Upload it again.")
    return data, problems


def _find(state: dict, job_id: str, invoice_id: str) -> tuple[dict | None, dict | None]:
    job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
    inv = next((i for i in (job or {}).get("invoices", []) if i["id"] == invoice_id), None)
    return job, inv


def for_invoice(state: dict[str, Any], company_id: str, job_id: str, invoice_id: str, form_type: str,
                today: date | None = None) -> tuple[bytes | None, str, list[str]]:
    """(PDF, file name, problems) of one pay app, or a lien waiver for it, on the client's form.
    None when the client has no such form."""
    job, inv = _find(state, job_id, invoice_id)
    if not inv:
        return None, "", ["Pay app not found."]
    if inv.get("imported"):   # an earlier pay app (payapp-core): billed before Duracash, on whatever form it went on then
        raise domain.GateError(f"{inv['number']} was billed before Duracash, so it isn't filled in on the client's form.")
    tpl = active(state, job.get("payer_id", ""), form_type)
    if not tpl:
        return None, "", ["This client has no form of that type."]
    values, rows = payapp_values(state, job, inv, today)
    data, problems = fill_template(company_id, tpl, values, rows)
    return data, _file_name(job, inv, tpl), problems


def problems_for(state: dict[str, Any], job: dict, invoice: dict) -> dict[str, list[str]]:
    """Each pay app form type the client has -> what stops it being filled right. Reads no file."""
    values, rows = payapp_values(state, job, invoice)
    out = {}
    for form_type in PAYAPP_TYPES:
        tpl = active(state, job.get("payer_id", ""), form_type)
        if tpl:
            _, _, problems = check(tpl, values, rows)
            if not tpl.get("mapping") and not tpl.get("placed"):
                problems = ["No values are matched to this form yet. Set it up on the client's card."] + problems
            out[form_type] = problems
    return out


def send_problems(state: dict[str, Any], job_id: str) -> list[str]:
    """Why the job's draft pay app can't go on its client's form, when the client's emails carry it."""
    job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
    if not job:
        return []
    draft = next((i for i in reversed(job.get("invoices", [])) if i.get("status") in ("draft", "awaiting_approval")), None)
    if not draft or send_mode(state, job.get("payer_id", "")) == "ours":
        return []
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    out = []
    for form_type, problems in problems_for(state, job, draft).items():
        out += [f"{payer.get('name', 'The client')}'s {FORM_TYPES[form_type].lower()}: {p}" for p in problems]
    return out


def for_email(state: dict[str, Any], company_id: str, job_id: str, invoice_id: str) -> tuple[list[tuple[str, bytes]], bool, str]:
    """What a pay app email carries from the client's forms: (attachments, whether Duracash's PDF
    goes too, why it can't go). With no pay app form for the client, Duracash's PDF alone."""
    job, inv = _find(state, job_id, invoice_id)
    if not inv:
        return [], True, ""
    mode = send_mode(state, job.get("payer_id", ""))
    if mode == "ours" or not any(active(state, job.get("payer_id", ""), t) for t in PAYAPP_TYPES):
        return [], True, ""
    payer = next((p for p in state.get("payers", []) if p["id"] == job.get("payer_id")), {})
    out = []
    for form_type in PAYAPP_TYPES:
        if not active(state, job.get("payer_id", ""), form_type):
            continue
        try:
            data, name, problems = for_invoice(state, company_id, job_id, invoice_id, form_type)
        except domain.GateError as exc:
            return [], True, f"{payer.get('name', 'the client')}'s {FORM_TYPES[form_type].lower()} couldn't be filled: {exc}"
        if problems:
            return [], True, f"{payer.get('name', 'the client')}'s {FORM_TYPES[form_type].lower()} isn't complete: {problems[0]}"
        out.append((name, data))
    return out, mode == "both", ""


def payer_pdf(state: dict[str, Any], company_id: str, job: dict, invoice: dict) -> tuple[bytes | None, str]:
    """The pay app the client's page offers when their emails carry only their own form: the
    cover page on their form, filled. None to offer Duracash's PDF."""
    if send_mode(state, job.get("payer_id", "")) != "theirs":
        return None, ""
    for form_type in PAYAPP_TYPES:
        if active(state, job.get("payer_id", ""), form_type):
            try:
                data, name, problems = for_invoice(state, company_id, job["id"], invoice["id"], form_type)
            except domain.GateError:
                return None, ""
            return (data, name) if data and not problems else (None, "")
    return None, ""


# ---------- what the pages show ----------

def _tpl_view(tpl: dict, admin: bool) -> dict:
    out = {k: tpl.get(k) for k in ("id", "form_type", "version", "file_name", "size", "pages", "fields", "fillable", "notes",
                                   "mapping", "placed", "money_format", "date_format", "added_at", "added_by", "updated_at",
                                   "updated_by", "retired_at", "retired_by", "duracash_note", "duracash_by", "duracash_at")}
    out["label"] = FORM_TYPES.get(tpl.get("form_type"), "")
    return out


def client_view(state: dict[str, Any], payer_id: str, admin: bool = False) -> dict:
    """The client card's section: each type's current form and its older versions, the send
    setting, and the lists the matching boxes offer."""
    domain._payer(state, payer_id)
    every = templates(state, payer_id, active_only=False)
    return {
        "payer_id": payer_id,
        "send": send_mode(state, payer_id),
        "templates": [_tpl_view(t, admin) for t in every if not t.get("retired_at")],
        "history": [_tpl_view(t, admin) | {"fields": [], "mapping": [], "placed": []} for t in every if t.get("retired_at")],
        "form_types": FORM_TYPES, "send_modes": SEND_MODES, "money_formats": MONEY_FORMATS, "date_formats": DATE_FORMATS,
        "sources": [{"key": k, "label": label, "kind": kind, "group": group} for k, label, kind, group in SOURCES],
        "admin": admin,
    }


def payapp_view(state: dict[str, Any], job_id: str, invoice_id: str) -> dict:
    """The pay app page's line about the client's forms: which go, and what stops them."""
    job, inv = _find(state, job_id, invoice_id)
    if not inv:
        raise domain.GateError("Pay app not found.")
    payer_id = job.get("payer_id", "")
    mode = send_mode(state, payer_id)
    problems = problems_for(state, job, inv)
    forms = [{"form_type": t, "label": FORM_TYPES[t], "version": active(state, payer_id, t)["version"], "problems": p,
              "url": f"/api/jobs/{job_id}/payapp/{invoice_id}/client-form/{t}.pdf"} for t, p in problems.items()]
    waivers = [{"form_type": t, "label": FORM_TYPES[t], "version": active(state, payer_id, t)["version"],
                "url": f"/api/jobs/{job_id}/payapp/{invoice_id}/client-form/{t}.pdf"} for t in WAIVER_TYPES if active(state, payer_id, t)]
    return {"send": mode, "send_label": SEND_MODES[mode], "forms": forms, "waivers": waivers,
            "goes": bool(forms) and mode != "ours", "docusign_note": docusign_note(state, job)}


def docusign_note(state: dict[str, Any], job: dict) -> str:
    """What Sign with DocuSign signs when the client's emails carry only their own form: Duracash's
    PDF of the pay app, as the approval; their form goes filled in, without DocuSign's signature
    on it (its fields have no place DocuSign is told to sign). "" when Duracash's PDF goes too."""
    payer_id = job.get("payer_id", "")
    if send_mode(state, payer_id) != "theirs" or not any(active(state, payer_id, t) for t in PAYAPP_TYPES):
        return ""
    payer = next((p for p in state.get("payers", []) if p["id"] == payer_id), {})
    return (f"Sign with DocuSign signs Duracash's PDF of this pay app, as your approval. "
            f"{payer.get('name') or 'The client'}'s emails carry only their own form, filled in, and DocuSign doesn't sign that form.")
