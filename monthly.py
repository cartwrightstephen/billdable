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


def send_monthly_summaries(site, year: int, month: int) -> list:
    """Email each company's owners (confirmed addresses) their summary of that month. Returns
    one line per company that has an owner to send to: "company: N of M sent"."""
    lines = []
    for company_id in site.company_ids():
        if company_id == auth.DEMO_COMPANY_ID:
            continue
        state = site.read(company_id)
        if state is None:
            continue  # alerted; the other companies still get theirs
        to = auth.owner_emails(company_id)
        if not to:
            continue
        subject, body = metrics.month_summary(state, year, month)
        lines.append(f"{company_id}: {_sent([site.send(addr, subject, body, company_id) for addr in to])}")
    return lines


def daily_email_hour() -> int:
    """The hour (UTC) from which the daily "due today" email goes out: DAILY_EMAIL_HOUR_UTC,
    default 11 (7 in the morning on the US east coast in summer, 6 in winter)."""
    try:
        return min(23, max(0, int(os.environ.get("DAILY_EMAIL_HOUR_UTC", "11"))))
    except ValueError:
        return 11


def send_daily_digests(site, now: datetime | None = None) -> list:
    """Once a day per company, from the hourly loop (R4-10): what's due today to each owner with
    a confirmed email, unless the company turned "Daily email of what's due" off. Nothing on a
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
        state = site.read(company_id)
        if state is None or state.get("company", {}).get("daily_email") is False:
            continue
        digest = domain.daily_digest(state, base, now)
        to = auth.owner_emails(company_id)
        if digest and to:
            lines.append(f"{company_id}  daily email: {_sent([site.send(addr, digest[0], digest[1], company_id) for addr in to])}")
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
        report, problems = backup.drill(newest)
        did.append(f"restore drill of {newest.name}: " + ("PASS" if not problems else f"FAIL ({len(problems)} problems)"))
        if problems:
            alerts.notify("drill", f"the monthly restore drill of {newest.name} failed: {problems[0]}")
        done["drill"] = month
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
    A failure is logged and alerted; the loop keeps going."""
    try:
        for line in run_monthly_jobs(site, now) + send_daily_digests(site, now):
            site.log("info", msg=line)
    except Exception as exc:
        site.log("error", msg=f"monthly jobs failed: {exc.__class__.__name__}: {exc}")
        alerts.notify("monthly-jobs", f"monthly jobs failed ({exc.__class__.__name__}); see the server log")


def start(site) -> threading.Thread:
    def loop() -> None:
        while True:
            run_once(site)
            time.sleep(3600)

    thread = threading.Thread(target=loop, name="monthly-jobs", daemon=True)
    thread.start()
    return thread
