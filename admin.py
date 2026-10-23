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

Every command acts on DURACASH_DATA_DIR, like the server. See docs/runbooks.md.
"""

from __future__ import annotations

import json
import sys

import auth
import domain
import server


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
        for line in server.send_monthly_summaries(year, month):
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
