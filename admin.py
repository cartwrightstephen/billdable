"""Founder tasks on the server, run from a shell on the host (Render: the service's Shell tab).

    python3 admin.py companies                     list companies: id, name, users, jobs
    python3 admin.py find EMAIL                    which company an email belongs to
    python3 admin.py delete-company COMPANY_ID     delete a company on request (asks to confirm)
    python3 admin.py end-sessions COMPANY_ID       sign every user of a company out
    python3 admin.py monthly-summaries [YYYY-MM]   email each company's owners last month's summary
    python3 admin.py read-only COMPANY_ID REASON   stop changes (for example "payment is past due");
                                                   the data stays, sign-in and export still work
    python3 admin.py writable COMPANY_ID           undo read-only
    python3 admin.py partner COMPANY_ID on|off     a design partner pays STRIPE_PARTNER_PRICE_ID
                                                   (the business plan: up to 3, $99 a month)
    python3 admin.py migrate-all                   bring every company file to the current schema now
                                                   (each is also brought up to date the first time it is read;
                                                   a backup is made first when the schema is new, H7)
    python3 admin.py two-step-off EMAIL            turn the authenticator app off for someone who lost the
                                                   phone and the recovery codes (asks to confirm; runbook 3a)
    python3 admin.py health                        what /healthz checks, with the details it doesn't show
                                                   the public: disk, email, backups, alerts
    python3 admin.py log [N]                       the last N (default 20) lines of the admin log

Every command acts on DURACASH_DATA_DIR, like the server. See docs/runbooks.md.

Every command run is written to <data dir>/admin_log.jsonl (M11): when, which command, on which
company, who ran it (the shell's user name) and how it ended. The file is only ever appended to;
an email is kept as a short hash, never the address. A command that changes something also posts
an alert to ALERT_WEBHOOK_URL, so the other founder hears of it.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import sys
from datetime import datetime, timezone

import alerts
import auth
import domain
import migrations
import monthly
import server
import storage

# The commands that change something: each is logged and alerted. The others are only logged.
CHANGES = {"delete-company", "end-sessions", "monthly-summaries", "read-only", "writable", "partner", "migrate-all", "two-step-off"}
EMAIL_ARGS = {"find", "two-step-off"}


def log_path():
    return storage.DATA_DIR / "admin_log.jsonl"


def _who() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no user name in the container's environment
        return "unknown"


def _target(cmd: str, args: list[str]) -> str:
    if not args:
        return ""
    if cmd in EMAIL_ARGS:
        return "email:" + hashlib.sha256(args[0].strip().lower().encode()).hexdigest()[:12]
    return args[0][:80]


def record(cmd: str, args: list[str], result: int) -> dict:
    """Append one line to the admin log; alert for a change (M11). Never raises: a log that can't be
    written is said on the screen, and the command has already run."""
    entry = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "command": cmd, "target": _target(cmd, args),
             "by": _who(), "result": "ok" if result == 0 else f"exit {result}"}
    try:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as exc:
        print(f"Note: the admin log couldn't be written ({exc}).")
    if cmd in CHANGES and result == 0:
        alerts.notify(f"admin:{cmd}:{entry['target']}", f"admin.py {cmd} {entry['target']} run by {entry['by']}".strip(), send=alerts._post)
    return entry


def _companies() -> list[dict]:
    users = auth.all_users()
    out = []
    for company_id in server.company_ids():
        state = server.load_company(company_id)
        out.append({"id": company_id, "name": state.get("company", {}).get("name", ""),
                    "users": sum(1 for u in users if u["company_id"] == company_id), "jobs": len(state.get("jobs", []))})
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, args = argv[1], argv[2:]
    result = 2
    try:
        result = _run(cmd, args)
    finally:
        if cmd != "log":
            record(cmd, args, result)
    return result


def _run(cmd: str, args: list[str]) -> int:
    if cmd == "companies":
        for c in _companies():
            print(f"{c['id']}  {c['name']}  users={c['users']}  jobs={c['jobs']}")
    elif cmd == "find" and args:
        user = auth.user_by_email(args[0])
        print(f"{user['email']} is {user['role']} of {user['company_id']}" if user else "No account with that email.")
    elif cmd == "delete-company" and args:
        match = next((c for c in _companies() if c["id"] == args[0]), None)
        if not match:
            print("No company with that id. Run: python3 admin.py companies")
            return 1
        typed = input(f"Delete {match['name']} ({match['id']}): {match['users']} users, {match['jobs']} jobs? Type the company id to confirm: ")
        if typed.strip() != match["id"]:
            print("Nothing deleted.")
            return 1
        removed = server.delete_company_data(match["id"])
        print(f"Deleted {match['name']} and {removed} users. Backups that hold it expire after BACKUP_KEEP days; off-site copies after the bucket's retention.")
    elif cmd == "monthly-summaries":
        # Run on the 1st of each month (a Render cron job, or by hand). Default: last month.
        # The server also sends these by itself on the 1st with MONTHLY_SUMMARY_EMAILS=1.
        import datetime as dt
        if args:
            year, month = (int(x) for x in args[0].split("-"))
        else:
            last = dt.date.today().replace(day=1) - dt.timedelta(days=1)
            year, month = last.year, last.month
        for line in monthly.send_monthly_summaries(server.SITE, year, month):
            print(line)
    elif cmd == "partner" and len(args) == 2 and args[1] in {"on", "off"}:
        if args[0] not in server.company_ids():
            print("No company with that id. Run: python3 admin.py companies")
            return 1
        on = args[1] == "on"
        with server.company_lock(args[0]):
            state = server.load_company(args[0])
            record = state.setdefault("company", {}).setdefault("billing", {})
            if on:
                record["partner"] = True
            else:
                record.pop("partner", None)
            domain.audit(state, "", "billing", "Duracash made the company a design partner." if on else "Duracash ended the design-partner price.", "Duracash")
            server.save_company(args[0], state)
        partners = sum(1 for c in server.company_ids() if (server.load_company(c).get("company", {}).get("billing") or {}).get("partner"))
        print(f"{args[0]} is {'a design partner' if on else 'on the regular price'}. Design partners now: {partners}.")
        if partners > 3:
            print("Note: the business plan caps design partners at 3.")
        print("It applies to the next Subscribe; an existing subscription changes in Stripe.")
    elif cmd in {"read-only", "writable"} and args and (cmd == "writable" or len(args) > 1):
        if args[0] not in server.company_ids():
            print("No company with that id. Run: python3 admin.py companies")
            return 1
        reason = " ".join(args[1:]).strip()[:200] if cmd == "read-only" else ""
        with server.company_lock(args[0]):
            state = server.load_company(args[0])
            company = state.setdefault("company", {})
            if reason:
                company["read_only"] = reason
            else:
                company.pop("read_only", None)
            domain.audit(state, "", "read_only" if reason else "writable",
                         f"Duracash set the company read-only: {reason}." if reason else "Duracash made the company writable again.", "Duracash")
            server.save_company(args[0], state)
        print(f"{args[0]} is now {'read-only: ' + reason if reason else 'writable'}.")
    elif cmd == "health":
        details = server.health_details()
        for key, value in details.items():
            print(f"{key}: {value}")
        for problem in server.recovery_problems() if server.production() else []:
            print(f"PROBLEM: {problem}")
        return 0 if details["ok"] else 1
    elif cmd == "log":
        try:
            lines = log_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines[-(int(args[0]) if args and args[0].isdigit() else 20):]:
            print(line)
    elif cmd == "two-step-off" and args:
        user = auth.user_by_email(args[0])
        if not user:
            print("No account with that email.")
            return 1
        typed = input(f"Turn two-step sign-in off for {user['email']} and sign them out everywhere? "
                      "Check who is asking first (runbook 3a). Type the email to confirm: ")
        if typed.strip().lower() != user["email"]:
            print("Nothing changed.")
            return 1
        auth.totp_reset(user["email"])
        with server.company_lock(user["company_id"]):
            state = server.load_company(user["company_id"])
            domain.audit(state, "", "team", f"Duracash turned the authenticator app off for {user.get('name') or user['email']} at their request.", "Duracash")
            server.save_company(user["company_id"], state)
        print(f"Two-step sign-in with the app is off for {user['email']}, and every session ended. "
              "They sign in with the password (and an emailed code where mail is set up) and can set the app up again.")
    elif cmd == "migrate-all":
        made = server.backup_before_upgrade(log=print)
        if made:
            print(f"Backed up first: {made}")
        ids = server.company_ids()
        updated, refused = 0, []
        for company_id in ids:
            raw = server.company_store().load(company_id)
            if raw is None or int(raw.get("schema_version") or 1) == migrations.SCHEMA_VERSION:
                continue
            try:
                server.load_company(company_id)  # a read saves it in the current shape (R6-08)
                updated += 1
            except ValueError as exc:  # a file from newer code
                refused.append(f"{company_id}: {exc}")
        print(f"{updated} of {len(ids)} company files brought to schema {migrations.SCHEMA_VERSION}; the others were already on it.")
        for line in refused:
            print(line)
        if refused:
            return 1
    elif cmd == "end-sessions" and args:
        with auth.LOCK:
            data = auth._load()
            ids = {u["id"] for u in data["users"] if u["company_id"] == args[0]}
            before = len(data["sessions"])
            data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("user_id") not in ids}
            auth._save(data)
        print(f"Ended {before - len(data['sessions'])} sessions.")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
