"""An iCalendar (.ics) feed of a company's billing dates.

Google Calendar, Apple Calendar and Outlook can subscribe to the feed URL, so
follow-up and approval dates show up on the owner's phone. The URL carries a
secret token instead of a login, because calendar apps cannot sign in.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any


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
    out = []

    def add(when: str | date | None, title: str, uid: str, detail: str = "") -> None:
        d = when if isinstance(when, date) else _day(when) if when else None
        if d:
            out.append({"date": d, "title": title, "uid": uid, "detail": detail})

    for j in state.get("jobs", []):
        payer = payers.get(j.get("payer_id"), {})
        add(j.get("completed_at"), f"{j['name']}: work complete", f"{j['id']}-done")
        for inv in j.get("invoices", []):
            if inv.get("status") == "void":
                continue
            add(inv.get("sent_at"), f"{inv['number']} sent ({j['name']})", f"{inv['id']}-sent")
            if inv.get("status") in {"sent", "partial"} and inv.get("sent_at"):
                sent = _day(inv["sent_at"])
                follow = int(payer.get("follow_up_days") or 7) if str(payer.get("follow_up_days") or "7").isdigit() else 7
                approve = int(payer.get("usual_approval_days") or 14) if str(payer.get("usual_approval_days") or "14").isdigit() else 14
                last = _day(inv.get("last_follow_up_at") or "") or sent
                add(last + timedelta(days=follow), f"Follow up on {inv['number']} ({j['name']})", f"{inv['id']}-follow",
                    f"Contact {payer.get('approver', '')} {payer.get('ap_email', '')}")
                add(sent + timedelta(days=approve), f"{inv['number']} expected approval ({j['name']})", f"{inv['id']}-approve")
            if inv.get("waiver_sent_at"):
                add(inv["waiver_sent_at"], f"Waiver sent for {inv['number']} ({j['name']})", f"{inv['id']}-waiver")
        for p in j.get("payments", []):
            add(p.get("matched_at"), f"Payment matched on {j['name']}", f"{j['id']}-{p['id']}")
        if j.get("closed_at"):
            add(j["closed_at"], f"{j['name']} closed", f"{j['id']}-closed")
    return out


def render(state: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = state.get("company", {}).get("name", "Billdable")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Billdable//Billing calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold(f"X-WR-CALNAME:{_esc(name + ' billing')}"),
    ]
    for e in events(state):
        lines += [
            "BEGIN:VEVENT",
            _fold(f"UID:{e['uid']}@billdable"),
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{e['date'].strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(e['date'] + timedelta(days=1)).strftime('%Y%m%d')}",
            _fold(f"SUMMARY:{_esc(e['title'])}"),
        ]
        if e["detail"]:
            lines.append(_fold(f"DESCRIPTION:{_esc(e['detail'])}"))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
