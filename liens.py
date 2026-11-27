"""Statutory lien deadlines for each job, by the state the job is in (Siteline comparison, item 8).

NOT LEGAL ADVICE. Duracash works these dates out from the statute's words and the dates the
contractor enters. Lien law turns on facts the app can't see (who is in privity with whom, what
counts as the last furnishing, whether a Notice of Commencement was filed and posted), so the
page says to confirm every date with an attorney.

The rules are data: STATE_RULES holds one entry per state, and only Georgia ("GA") is filled.
Adding a state is a new entry (its deadlines, how it counts days, its holidays), not new code,
unless the state counts time in a way `count` doesn't know yet.

Georgia, O.C.G.A. Title 44, Chapter 14, Article 8, Part 3 (§ 44-14-360 et seq.), read Nov 27,
2026 (FindLaw's Georgia Code, current as of March 28, 2024, and law-firm summaries of SB 315 of
2020, effective Jan 1, 2021; nothing enacted since was found):

- Preliminary notice of lien rights, § 44-14-361.3: optional. Within 30 days after first
  furnishing labor, services or materials; a copy to the owner or contractor within 7 days.
- Notice to contractor, § 44-14-361.5(a): from someone not in privity with the contractor (a
  lower-tier sub or a supplier to a sub), within 30 days from the filing of the Notice of
  Commencement or 30 days after first furnishing, whichever is later. When no Notice of
  Commencement is filed, the section doesn't apply (§ 44-14-361.5(d)).
- Claim of lien, § 44-14-361.1(a)(2): filed within 90 days after the completion of the work or
  the last furnishing of materials (practitioners read this as the claimant's own last
  furnishing). A copy to the owner no later than 2 business days after filing.
- Action to enforce, § 44-14-361.1(a)(3): commenced within 365 days from filing the claim; a
  notice of commencement of lien action filed with the clerk within 30 days after commencing
  the action. § 44-14-367: a lien with no such notice within 395 days of filing may be
  disregarded.
- Notice of contest of lien, § 44-14-368: when the owner or contractor files one, the claimant's
  time to sue drops to 60 days from receiving it.
- Lien waivers, § 44-14-366 (SB 315): a waiver is conclusively deemed effective 90 days after it
  was signed unless an affidavit of nonpayment is filed before then (60 days before 2021). The
  waivers Duracash sent are already clocked in domain/gawaivers.py and its To Do rows; here a
  waiver signed outside Duracash can be entered so it is counted too. Georgia's statutory waiver
  forms have no notary block; the affidavit of nonpayment is sworn before a notary.
- Counting time, § 44-14-369 and § 1-3-1(d)(3): the first day isn't counted and the last is; a
  last day on a Saturday or Sunday runs through Monday, on a public and legal holiday (§ 1-4-1)
  through the next business day. § 1-4-1(a)(1) adopts the federal holidays as of Jan 1, 2022;
  days the Governor adds each year aren't known here. So the page shows the statute's last day
  and, when that was moved off a weekend or holiday, a safer day before it to file by.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

NOT_LEGAL_ADVICE = ("Not legal advice. These dates are worked out from the statute and the dates you entered; "
                    "confirm every deadline with your attorney before you rely on it.")

# How a job's contractor stands to the owner (who hired whom), from the client's type when the
# job doesn't say (domain/jobinfo.py CLIENT_TYPES).
TIERS = {
    "owner": "Hired by the owner (you are the prime contractor)",
    "contractor": "Hired by the general contractor",
    "lower": "Hired by a subcontractor or supplier (lower tier)",
    "public": "Public project",
}
TIER_FROM_CLIENT = {"gc": "contractor", "owner": "owner", "homeowner": "owner", "other": "owner", "sub": "lower", "public": "public"}

# One deadline: `start` names the date it counts from (an input, or "later:a,b"); `days` are
# calendar days, `business_days` business days (§ 1-3-1(d)(3) skips weekends and holidays in
# periods under 7 days); `tiers` who it applies to (all when absent); `needs` an input without
# which it doesn't apply, with `needs_note`; `done` the input that says it was done.
GA_DEADLINES: list[dict[str, Any]] = [
    {"key": "preliminary_notice", "label": "Preliminary notice of lien rights (optional)", "cite": "O.C.G.A. § 44-14-361.3",
     "start": "first_furnished_on", "days": 30, "optional": True,
     "help": "Optional. File with the clerk of superior court in the property's county; send a copy to the owner or contractor within 7 days."},
    {"key": "notice_to_contractor", "label": "Notice to contractor", "cite": "O.C.G.A. § 44-14-361.5(a)",
     "start": "later:noc_filed_on,first_furnished_on", "days": 30, "tiers": ("lower",),
     "needs": "noc_filed_on", "needs_note": "Needed only when a Notice of Commencement was filed (§ 44-14-361.5(d)). Enter its filing date when you get it.",
     "help": "By registered or certified mail or statutory overnight delivery to the owner and the contractor at the addresses on the Notice of Commencement."},
    {"key": "lien_claim", "label": "File the claim of lien", "cite": "O.C.G.A. § 44-14-361.1(a)(2)",
     "start": "last_furnished_on", "days": 90, "money": True,
     "help": "Within 90 days after your last work or materials on the job. The date moves while work goes on."},
    {"key": "lien_copy_owner", "label": "Send the owner a copy of the filed lien", "cite": "O.C.G.A. § 44-14-361.1(a)(2)",
     "start": "done:lien_claim", "business_days": 2,
     "help": "No later than 2 business days after filing; to the contractor too when a Notice of Commencement was filed."},
    {"key": "lien_action", "label": "Start the lawsuit to enforce the lien", "cite": "O.C.G.A. § 44-14-361.1(a)(3)",
     "start": "done:lien_claim", "days": 365,
     "help": "Within 365 days from filing the claim of lien."},
    {"key": "contest_action", "label": "Sue after a notice of contest of lien", "cite": "O.C.G.A. § 44-14-368",
     "start": "contest_received_on", "days": 60, "needs": "contest_received_on", "needs_note": "Only when the owner or contractor files a notice of contest.",
     "help": "Within 60 days from receiving the notice of contest; the lien is extinguished 90 days after the contest is filed without a notice of commencement of lien action."},
    {"key": "action_notice", "label": "File the notice of commencement of lien action", "cite": "O.C.G.A. §§ 44-14-361.1(a)(3), 44-14-367",
     "start": "done:lien_action", "days": 30,
     "help": "With the clerk of superior court within 30 days after starting the lawsuit."},
    {"key": "affidavit_nonpayment", "label": "Affidavit of nonpayment (waiver signed, not paid)", "cite": "O.C.G.A. § 44-14-366(g)",
     "start": "waiver_signed_on", "days": 90, "needs": "waiver_signed_on", "needs_note": "Only for a waiver signed and not paid. Waivers Duracash sent are tracked on the Dashboard.",
     "help": "File before 90 days after the waiver was signed, or it takes effect as if paid. Sworn before a notary; copy to the owner within 7 days."},
]

STATE_RULES: dict[str, dict[str, Any]] = {
    "GA": {
        "name": "Georgia",
        "statute": "O.C.G.A. § 44-14-360 et seq.",
        "counting": "O.C.G.A. §§ 44-14-369, 1-3-1(d)(3)",
        "holidays": "us_federal",   # § 1-4-1(a)(1); the Governor's added days aren't known here
        "public_note": "Liens don't attach to public property in Georgia; payment bond claims apply instead (not worked out here).",
        "waiver_note": ("Georgia lien waivers must use the statutory forms (§ 44-14-366) and don't need a notary: the forms end with "
                        "signature, seal and witness lines. The affidavit of nonpayment is sworn before a notary."),
        "deadlines": GA_DEADLINES,
    },
}

# Inputs a job's lien settings keep (compliance.py checks and stores them).
DATE_INPUTS = ("first_furnished_on", "last_furnished_on", "noc_filed_on", "contest_received_on", "waiver_signed_on")


# ---------- holidays and counting ----------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def us_federal_holidays(year: int) -> set[date]:
    """The federal holidays (5 U.S.C. § 6103), each on its day and, when that is a Saturday or a
    Sunday, on the Friday or Monday it is observed. Juneteenth from 2021."""
    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    if year >= 2021:
        fixed.append(date(year, 6, 19))
    days = set(fixed)
    for d in fixed:
        if d.weekday() == 5:
            days.add(d - timedelta(days=1))
        elif d.weekday() == 6:
            days.add(d + timedelta(days=1))
    days |= {_nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _last_weekday(year, 5, 0),
             _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 10, 0, 2), _nth_weekday(year, 11, 3, 4)}
    # New Year's Day on a Saturday is observed on Dec 31 of the year before.
    if date(year + 1, 1, 1).weekday() == 5:
        days.add(date(year, 12, 31))
    return days


HOLIDAYS = {"us_federal": us_federal_holidays}


def is_business_day(d: date, holidays: str = "us_federal") -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS[holidays](d.year)


def count(start: date, rule: dict[str, Any], holidays: str = "us_federal") -> dict[str, Any]:
    """The deadline `rule` sets from `start`, as § 1-3-1(d)(3) counts: {counted, last_day,
    file_by, moved}. counted is start plus the days (the first day not counted, the last
    counted); last_day is that, moved off a weekend or holiday to the next business day;
    file_by is the safer day to act by: counted when it is a business day, else the business
    day before it. Business days (a period under 7 days) skip weekends and holidays as they go."""
    if rule.get("business_days"):
        d, left = start, int(rule["business_days"])
        while left:
            d += timedelta(days=1)
            if is_business_day(d, holidays):
                left -= 1
        return {"counted": d, "last_day": d, "file_by": d, "moved": False}
    counted = start + timedelta(days=int(rule["days"]))
    last = counted
    while not is_business_day(last, holidays):
        last += timedelta(days=1)
    safe = counted
    while not is_business_day(safe, holidays):
        safe -= timedelta(days=1)
    return {"counted": counted, "last_day": last, "file_by": safe, "moved": last != counted}


# ---------- one job ----------

def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _start(rule: dict[str, Any], inputs: dict[str, Any]) -> tuple[date | None, str]:
    """The date the rule counts from, and the input it came from."""
    spec = rule["start"]
    if spec.startswith("later:"):
        found = [(d, k) for k in spec[6:].split(",") for d in [_day(inputs.get(k))] if d]
        return max(found) if found else (None, "")
    if spec.startswith("done:"):
        return _day((inputs.get("done") or {}).get(spec[5:])), spec
    return _day(inputs.get(spec)), spec


START_WORDS = {
    "first_furnished_on": "first work or materials", "last_furnished_on": "last work or materials", "noc_filed_on": "Notice of Commencement filed",
    "contest_received_on": "notice of contest received", "waiver_signed_on": "waiver signed",
    "done:lien_claim": "lien filed", "done:lien_action": "lawsuit started",
}


def deadlines(rules: dict[str, Any], inputs: dict[str, Any], today: date, remind_days: int = 30, owed: bool = True,
              work_done: bool = True) -> list[dict[str, Any]]:
    """Every deadline of the state's rules for one job: {key, label, cite, help, optional, status,
    note, from, from_on, counted, last_day, file_by, moved, days_left, done_on}. status is one of
    done, skipped, not_needed, waiting (a date it counts from isn't known yet), passed,
    due_soon (file_by within remind_days), upcoming. Dates are ISO strings."""
    tier = inputs.get("tier") or "owner"
    done = inputs.get("done") or {}
    skipped = set(inputs.get("skipped") or [])
    out = []
    for rule in rules["deadlines"]:
        row = {"key": rule["key"], "label": rule["label"], "cite": rule["cite"], "help": rule.get("help", ""), "optional": bool(rule.get("optional")),
               "status": "", "note": "", "from": "", "from_on": "", "counted": "", "last_day": "", "file_by": "", "moved": False,
               "days_left": None, "done_on": str(done.get(rule["key"]) or "")}
        out.append(row)
        if tier == "public":
            row["status"], row["note"] = "not_needed", rules.get("public_note", "")
            continue
        if rule.get("tiers") and tier not in rule["tiers"]:
            row["status"], row["note"] = "not_needed", "Not needed: you are in privity with the contractor or the owner."
            continue
        if rule.get("needs") and not _day(inputs.get(rule["needs"])):
            row["status"], row["note"] = "not_needed", rule.get("needs_note", "")
            continue
        start, source = _start(rule, inputs)
        if start:
            c = count(start, rule, rules.get("holidays", "us_federal"))
            row.update({"from": START_WORDS.get(source, source), "from_on": start.isoformat(), "counted": c["counted"].isoformat(),
                        "last_day": c["last_day"].isoformat(), "file_by": c["file_by"].isoformat(), "moved": c["moved"],
                        "days_left": (c["file_by"] - today).days})
        if row["done_on"]:
            row["status"] = "done"
        elif rule["key"] in skipped:
            row["status"] = "skipped"
        elif not start:
            row["status"] = "waiting"
            row["note"] = f"Counts from {START_WORDS.get(rule['start'], 'a date')}; enter it to see the deadline." if not rule["start"].startswith("done:") else ""
        elif _day(row["last_day"]) < today:
            row["status"] = "passed"
        elif row["days_left"] <= remind_days:
            row["status"] = "due_soon"
        else:
            row["status"] = "upcoming"
        if rule.get("money") and row["status"] in {"due_soon", "upcoming"} and not owed:
            row["note"] = "Nothing is owed on this job now; a lien is only for unpaid work."
        if rule["key"] == "lien_claim" and row["status"] in {"due_soon", "upcoming", "waiting"} and not work_done:
            row["note"] = (row["note"] + " " if row["note"] else "") + "Work is still going on, so this date moves with each new job record."
    return out


def rules_for(state_code: str) -> dict[str, Any] | None:
    return STATE_RULES.get((state_code or "").upper())
