"""The server's background jobs: the hourly loop, the daily "due today" email, and once a month
the restore drill and last month's summary to each owner.

Moved out of server.py (R6-08). The server hands in `site`, looked up when called:
company_ids(), read(company_id) (the company's state under its lock, or None when it is missing),
deleted() (the deleted company ids), send(to, subject, body, company_id) (a system email; returns
"sent" or why not), app_url(), and log(level, **fields).

What these return goes to the server log, so it carries counts and company ids, never an
email address (R6-09).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import alerts
import auth
import backup
import domain
import mailer
import metrics
import storage

DAILY_PATH_NAME = "daily_jobs.json"
MONTHLY_PATH_NAME = "monthly_jobs.json"


def _sent(results: list) -> str:
    """"2 of 3 sent" for a list of send results."""
    return f"{sum(1 for r in results if r == 'sent')} of {len(results)} sent"


def _failed(site, company_id: str, what: str, exc: Exception) -> None:
    """One company's job failed (a file this code can't read, say): logged and alerted, and the
    other companies still get theirs."""
    site.log("error", msg=f"{company_id}: {what} failed: {exc.__class__.__name__}: {exc}")
    alerts.notify(f"monthly-jobs:{company_id}", f"the {what} for {company_id} failed ({exc.__class__.__name__}); see the server log")


def send_monthly_summaries(site, year: int, month: int) -> list:
    """Email each company's owners (confirmed addresses) their summary of that month. Returns
    one line per company that has an owner to send to: "company: N of M sent"."""
    lines = []
    for company_id in site.company_ids():
        if company_id == auth.DEMO_COMPANY_ID:
            continue
        try:
            state = site.read(company_id)
            if state is None:
                continue  # alerted; the other companies still get theirs
            to = auth.owner_emails(company_id)
            if not to:
                continue
            subject, body = metrics.month_summary(state, year, month)
            lines.append(f"{company_id}: {_sent([site.send(addr, subject, body, company_id) for addr in to])}")
        except Exception as exc:
            _failed(site, company_id, "monthly summary", exc)
    return lines


def daily_email_hour() -> int:
    """The hour (UTC) from which the daily "due today" email goes out: DAILY_EMAIL_HOUR_UTC,
    default 11 (7 in the morning on the US east coast in summer, 6 in winter)."""
    try:
        return min(23, max(0, int(os.environ.get("DAILY_EMAIL_HOUR_UTC", "11"))))
    except ValueError:
        return 11


def _digests(state: dict, company_id: str, base: str, now: datetime) -> list:
    """(address, digest or None) for each person the daily email goes to (G5): each owner gets
    what is assigned to them and what nobody has; a member with a confirmed email gets only what
    is assigned to them, and nothing on a day with none."""
    out = []
    for addr in auth.owner_emails(company_id):
        user = auth.user_by_email(addr)
        out.append((addr, domain.daily_digest(state, base, now, user_id=user["id"] if user else None)))
    for user in auth.all_users():
        if user.get("company_id") == company_id and user.get("role") != "owner" and user.get("email_verified"):
            out.append((user["email"], domain.daily_digest(state, base, now, user_id=user["id"], owner=False)))
    return out


def send_daily_digests(site, now: datetime | None = None) -> list:
    """Once a day per company, from the hourly loop (R4-10): what's due today to each owner with
    a confirmed email, and to each member with something assigned to them (G5), unless the company turned "Daily email of what's due" off. Nothing on a
    day with nothing due. The day each company got it is kept in <data dir>/daily_jobs.json so a
    restart doesn't repeat it. Returns one line per company emailed, for the log."""
    now = now or datetime.now(timezone.utc)
    if not mailer.configured() or now.hour < daily_email_hour():
        return []
    day = now.date().isoformat()
    path = storage.DATA_DIR / DAILY_PATH_NAME
    try:
        done = json.loads(path.read_text())
    except (OSError, ValueError):
        done = {}
    lines, base, deleted = [], site.app_url() or "", site.deleted()
    for company_id in site.company_ids():
        if company_id == auth.DEMO_COMPANY_ID or done.get(company_id) == day or company_id in deleted:
            continue
        done[company_id] = day
        try:
            state = site.read(company_id)
            if state is None or state.get("company", {}).get("daily_email") is False:
                continue
            results = [site.send(addr, d[0], d[1], company_id) for addr, d in _digests(state, company_id, base, now) if d]
            if results:
                lines.append(f"{company_id}  daily email: {_sent(results)}")
        except Exception as exc:
            _failed(site, company_id, "daily email", exc)
    storage.write_json(path, done)
    return lines


def run_monthly_jobs(site, now: datetime | None = None) -> list:
    """Once a month, recorded in <data dir>/monthly_jobs.json so a restart doesn't repeat them:
    the restore drill on the newest backup (an alert when it fails), and, with
    MONTHLY_SUMMARY_EMAILS=1 and email set up, last month's summary to each owner (on the 1st
    to the 3rd). Returns what it did, for the log."""
    now = now or datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    path = storage.DATA_DIR / MONTHLY_PATH_NAME
    try:
        done = json.loads(path.read_text())
    except (OSError, ValueError):
        done = {}
    did = []
    newest = (backup.backups() or [None])[0]
    if done.get("drill") != month and newest is not None:
        try:
            report, problems = backup.drill(newest)
        except Exception as exc:
            # A backup that can't be unpacked (corrupt, or the disk full) is a failed drill, not a
            # stop to the hourly loop: alerted once this month, and the daily emails still go.
            problems = [f"the drill could not run: {exc.__class__.__name__}: {exc}"]
        did.append(f"restore drill of {newest.name}: " + ("PASS" if not problems else f"FAIL ({len(problems)} problems)"))
        if problems:
            alerts.notify("drill", f"the monthly restore drill of {newest.name} failed: {problems[0]}")
        done["drill"] = month
        storage.write_json(path, done)  # kept now, so a failure below doesn't run the drill again next hour
    if (os.environ.get("MONTHLY_SUMMARY_EMAILS") == "1" and mailer.configured() and now.day <= 3
            and done.get("summaries") != month):
        last = now.date().replace(day=1) - timedelta(days=1)
        lines = send_monthly_summaries(site, last.year, last.month)
        did.append(f"monthly summaries for {last:%Y-%m}: {len(lines)} companies")
        did.extend(lines)
        done["summaries"] = month
    if did:
        storage.write_json(path, done)
    return did


def run_once(site, now: datetime | None = None) -> None:
    """One turn of the hourly loop: the monthly jobs, then the daily emails, each line logged.
    A failure is logged and alerted; the loop keeps going, and a failure in the monthly jobs
    doesn't stop the daily emails."""
    for key, what, job in (("monthly-jobs", "monthly jobs", run_monthly_jobs), ("daily-emails", "daily emails", send_daily_digests)):
        try:
            for line in job(site, now):
                site.log("info", msg=line)
        except Exception as exc:
            site.log("error", msg=f"{what} failed: {exc.__class__.__name__}: {exc}")
            alerts.notify(key, f"{what} failed ({exc.__class__.__name__}); see the server log")


def start(site) -> threading.Thread:
    def loop() -> None:
        while True:
            run_once(site)
            time.sleep(3600)

    thread = threading.Thread(target=loop, name="monthly-jobs", daemon=True)
    thread.start()
    return thread
