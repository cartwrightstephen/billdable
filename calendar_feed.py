"""An iCalendar (.ics) feed of a company's billing dates.

Google Calendar, Apple Calendar and Outlook can subscribe to the feed URL, so
follow-up and approval dates show up on the owner's phone. The URL carries a
secret token instead of a login, because calendar apps cannot sign in.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import domain

# Shown on the Calendar page and in every feed: Duracash computes no statutory dates (LC-2).
NOT_LEGAL = "These are your contract and follow-up dates, not legal deadlines. Check lien and prompt-pay deadlines with your attorney."


def _esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 line folding: at most 75 octets per line, continuation lines start with a space."""
    out, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > (75 if not out else 74):
            out.append(current.decode("utf-8"))
            current = b""
        current += b
    out.append(current.decode("utf-8"))
    return "\r\n ".join(out)


def _day(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        return None


def events(state: dict[str, Any]) -> list[dict[str, Any]]:
    payers = {p["id"]: p for p in state.get("payers", [])}
    style = domain.billing_style(state)   # "draw" for a company that bills in draws (R6-01)
    out = []

    def add(when: str | date | None, title: str, uid: str, detail: str = "") -> None:
        d = when if isinstance(when, date) else _day(when) if when else None
        if d:
            out.append({"date": d, "title": title, "uid": uid, "detail": detail})

    for j in state.get("jobs", []):
        payer = payers.get(j.get("payer_id"), {})
        add(j.get("completed_at"), f"{j['name']}: work complete", f"{j['id']}-done")
        # One follow-up per job, on the day Today raises it (domain.follow_up_due): the oldest unpaid
        # pay app, after its last follow-up, or the date noted after a call, or after a promised date.
        due = domain.follow_up_due(j, payer)
        if due:
            oldest = next(inv for inv in j["invoices"] if inv["status"] in domain.OPEN_FOR_PAYMENT)
            add(due.date(), f"Follow up on {oldest['number']} ({j['name']})", f"{oldest['id']}-follow",
                f"Contact {payer.get('approver', '')} {payer.get('ap_email', '')}")
        for inv in j.get("invoices", []):
            if inv.get("status") == "void":
                continue
            add(inv.get("sent_at"), f"{inv['number']} sent ({j['name']})", f"{inv['id']}-sent")
            if inv.get("status") in {"sent", "partial"} and inv.get("sent_at"):
                sent = _day(inv["sent_at"])
                approve = int(payer.get("usual_approval_days") or 14) if str(payer.get("usual_approval_days") or "14").isdigit() else 14
                add(sent + timedelta(days=approve), f"{inv['number']} expected approval ({j['name']})", f"{inv['id']}-approve")
            if inv.get("status") in domain.OPEN_FOR_PAYMENT and inv.get("due_on"):
                add(inv["due_on"], f"{inv['number']} payment due ({j['name']})", f"{inv['id']}-due")   # payment terms (R4-04)
            if inv.get("waiver_sent_at"):
                add(inv["waiver_sent_at"], f"Waiver sent for {inv['number']} ({j['name']})", f"{inv['id']}-waiver")
        for p in j.get("payments", []):
            add(p.get("matched_at"), f"Payment matched on {j['name']}", f"{j['id']}-{p['id']}")
        if j.get("closed_at"):
            add(j["closed_at"], f"{j['name']} closed", f"{j['id']}-closed")
        # The payer's monthly cutoff (playbook), for jobs that may still bill: this cycle and the next two.
        due = domain.payapp_due(payer)
        if due and not j.get("closed") and (j.get("work_status") != "complete" or domain.job_money(j)["unbilled_completed_cents"] > 0):
            when = due[0]
            for _ in range(3):
                add(when, f"{domain.term_text('Pay app due to', style)} {payer.get('name', 'the payer')} ({j['name']})", f"{j['id']}-due-{when:%Y%m}",
                    domain.term_text("The payer's cutoff for this month's pay app, from the payer playbook.", style))
                when = domain.payapp_due(payer, when + timedelta(days=1))[0]
    return out


def render(state: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = state.get("company", {}).get("name", "Duracash")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Duracash//Billing calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold(f"X-WR-CALNAME:{_esc(name + ' billing')}"),
        _fold(f"X-WR-CALDESC:{_esc(NOT_LEGAL)}"),
    ]
    for e in events(state):
        lines += [
            "BEGIN:VEVENT",
            _fold(f"UID:{e['uid']}@duracash"),
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{e['date'].strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(e['date'] + timedelta(days=1)).strftime('%Y%m%d')}",
            _fold(f"SUMMARY:{_esc(e['title'])}"),
        ]
        # Calendar apps show an event's description, rarely the calendar's, so each event carries the label.
        detail = f"{e['detail']}\n\n{NOT_LEGAL}" if e["detail"] else NOT_LEGAL
        lines.append(_fold(f"DESCRIPTION:{_esc(detail)}"))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
