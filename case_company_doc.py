"""The case company packet (docs/case-company.md), written from case_company.CASE and EXPECTED so
the steps a tester follows are the ones tests/test_case_company.py enters and checks. The words
in quotes are the app's own labels. Run `python3 case_company.py` to write it again."""

from __future__ import annotations

from datetime import date

import case_company as cc

LS = "LS"


def _money(dollars: int | float) -> str:
    return f"${dollars:,.2f}"


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return d.strftime("%A, %B ") + str(d.day)


def _short(iso: str) -> str:
    d = date.fromisoformat(iso)
    return d.strftime("%b ") + str(d.day)


def _job(key: str) -> dict:
    return next(j for j in cc.JOBS if j["key"] == key)


def _client(key: str) -> dict:
    return next(c for c in cc.CLIENTS if c["key"] == key)


def _line(job: dict, n) -> tuple:
    if isinstance(n, str):
        return (f"{n} (the signed change order's line)", 1, LS, 0)
    return job["lines"][n - 1]


def _cos_so_far(upto_day: str) -> dict:
    """Change order number -> title, per job, as the days before have made them."""
    made: dict[str, list[str]] = {}
    for day, steps in cc.DAYS:
        for s in steps:
            if s["do"] == "co":
                made.setdefault(s["job"], []).append(s["title"])
        if day == upto_day:
            break
    return made


def _record(s: dict, day: str) -> list[str]:
    j = _job(s["job"])
    out = [f"**New job record: {j['name']}** (Jobs > \"New job record\")", "",
           "| Box | Type this |", "|---|---|",
           f"| \"Job\" | {j['name']} |",
           f"| \"Date\" | {_short(day)}, 2026 |",
           f"| \"Diary\" | {s['diary']} |",
           f"| \"Crew members\" | {s['crew']} |"]
    if s.get("names"):
        out.append(f"| \"Names (optional)\" | {s['names']} |")
    out.append(f"| \"Weather\" | {s.get('weather', 'Not noted')} |")
    if s.get("weather_note"):
        out.append(f"| \"Weather note (optional)\" | {s['weather_note']} |")
    if s.get("materials"):
        out.append(f"| \"Materials used or installed\" | {s['materials']} |")
    out.append("")
    if s.get("qty"):
        out += ["Quantities (the table under the diary):", "", "| Line | Type this |", "|---|---|"]
        titles = _cos_so_far(day).get(s["job"], [])
        for n, q in s["qty"].items():
            if isinstance(n, str):
                no = int(n[2:])
                out.append(f"| CO{no}: {titles[no - 1]} | {q} in \"% done to date\" |")
            else:
                desc, qty, unit, _ = _line(j, n)
                if unit == LS and qty == 1:
                    out.append(f"| {n}. {desc} | {q} in \"% done to date\" |")
                else:
                    out.append(f"| {n}. {desc} | {q} {unit} in \"Today\" |")
        out.append("")
    if s.get("co_work"):
        titles = _cos_so_far(day).get(s["job"], [])
        out.append("Tick \"Change order work\":")
        out.append("")
        for no, hours, what in s["co_work"]:
            out.append(f"- \"Change order\": CO {no} {titles[no - 1]} (no price yet); \"Hours\": {hours}; \"Work done\": {what}")
        out.append("")
    for section, boxes in s.get("docs", []):
        chip = {"Delivery tickets": "+ Delivery ticket", "Progress photos": "+ Photos", "Time sheets": "+ Time sheet",
                "Invoices": "+ Invoice", "Inspections": "+ Inspection"}[section]
        parts = "; ".join(f"\"{k}\": {v}" for k, v in boxes.items())
        extra = " Add any photo with \"Take a photo\" or \"Choose files\"." if section in ("Progress photos", "Time sheets") else ""
        out.append(f"- Tap \"{chip}\". {parts}.{extra}")
    if s.get("docs"):
        out.append("")
    out.append("Tap \"Save job record\".")
    return out


def _co(s: dict) -> list[str]:
    j = _job(s["job"])
    out = [f"**New change order on {j['name']}** (job page > \"New Change Order\")", "",
           "| Box | Type this |", "|---|---|",
           f"| \"Title\" | {s['title']} |",
           f"| \"Reason\" | {s['reason']} |",
           f"| \"Description\" | {s.get('description', '')} |",
           f"| \"Type\" | {s['kind']} |",
           f"| \"Pricing\" | {s.get('pricing', 'Lump sum')} |",
           f"| \"Amount ($)\" | {_money(s['amount']) if s['amount'] else 'leave it blank: no price yet'} |", "",
           "Tap \"Create change order\"."]
    if s.get("sign"):
        signer, on = s["sign"]
        out += ["", f"It came back signed on paper. Tap \"Signed on paper…\": \"Signed by (name)\" {signer}, \"Date signed\" "
                    f"{_short(on)}, 2026. Add the signed copy with \"Take a photo of the signed copy\" (any photo will do), "
                    "pick it under \"The signed copy\", and tap \"Save as signed\"."]
    return out


def _price(s: dict) -> list[str]:
    j = _job(s["job"])
    return [f"**Price CO {s['co']} on {j['name']}.** On the change order, \"Type\" Adds to the contract, \"Price ($)\" "
            f"{_money(s['amount'])}, then \"Save price\". ({s['why']}.) Don't get it signed yet: it is still waiting "
            "for Ironbridge at the end of the case."]


def _payapp(s: dict, final: bool) -> list[str]:
    j = _job(s["job"])
    c = _client(j["client"])
    first = ([f"**Final pay app on {j['name']}.** Job page > \"Final Pay App\". \"Period to\" {_short(s['period_to'])}, 2026. "
              "Tap \"Draft final pay app\"."] if final else
             [f"**Progress pay app on {j['name']}.** Job page > \"Progress Pay App\". \"Period to\" {_short(s['period_to'])}, 2026. "
              "Leave every \"Done to date\" box as it is: the job records filled them in. Tap \"Draft progress pay app\"."])
    return first + ["",
                    "Supporting documents: for each item under \"Missing\", upload any file with \"Choose file\" or \"Take a photo\", "
                    "or tap \"I have this…\" and type a reason. The conditional waiver is always asked for; mark it \"Conditional\" "
                    "when asked \"Which waiver is this?\".", "",
                    f"Approvals: \"Your full name\" {cc.COMPANY['owner']}, \"Title\" Owner, then \"Sign\" with the pay app's number. "
                    f"Then \"Review & send\" > \"Approve & send to\" `you+{c['plus']}@example.com` > \"Send\". It is sent today, "
                    "whatever today is."]


def _payment(s: dict) -> list[str]:
    j = _job(s["job"])
    return [f"**Payment on {j['name']}.** Job page > \"Record a Payment\": \"Amount received ($)\" {_money(s['amount'])}, "
            f"\"Date received\" today, \"Check or reference no.\" {s['reference']}. Tap \"Record\"."]


def _waiver(s: dict) -> list[str]:
    j = _job(s["job"])
    return [f"**Lien waiver on {j['name']}.** Tap \"Choose waiver and send\". Add a photo of the signed form (any photo), "
            f"answer \"Which waiver is this?\" with {s['kind']}, tap \"Approve & send\", then \"I opened it and it's right\" "
            "and \"Send the waiver\"."]


def _finish(s: dict) -> list[str]:
    j = _job(s["job"])
    return [f"**Finish {j['name']}.** Job page > \"Finish job\". If it asks \"Why it can finish without them\", type "
            "Homeowner job: no closeout documents."]


def _check(name: str) -> list[str]:
    want = cc.EXPECTED[name]
    out = [f"> **Checkpoint: {name}.** If a figure is different, stop and report it.", ">"]
    for number, f in want.get("payapps", {}).items():
        out.append(f"> - {number}: this period {_money(f['gross'])}, retainage {_money(f['retainage'])}, amount due {_money(f['due'])}")
    for key, f in want.get("jobs", {}).items():
        j = _job(key)
        bits = []
        if "contract_total" in f:
            bits.append(f"contract total {_money(f['contract_total'])}")
        if "billed" in f:
            bits.append(f"billed {_money(f['billed'])}")
        if "paid" in f:
            bits.append(f"paid {_money(f['paid'])}")
        if "outstanding" in f:
            bits.append(f"outstanding {_money(f['outstanding'])}")
        if "retainage" in f:
            bits.append(f"retainage held {_money(f['retainage'])}")
        if f.get("pending_cos"):
            bits.append(f"{f['pending_cos']} change order waiting for signature")
        if f.get("finished"):
            bits.append("under Finished jobs")
        out.append(f"> - {j['name']}: " + ", ".join(bits))
    return out


def render() -> str:
    co = cc.COMPANY
    out = [
        "# Case company: Sweetgum Ridge Mechanical",
        "",
        "A made-up mechanical subcontractor in Decatur, Georgia, with five people, three clients, three jobs and three "
        "weeks of daily job history, Monday November 2 to Friday November 20, 2026. Testers type it into Duracash in "
        "order, day by day, and check the figures at each **Checkpoint**.",
        "",
        "Everything is made up: the companies, people, addresses, phone numbers (555-01xx) and jobs. Only your own email "
        "address is real.",
        "",
        "This guide is written from the same data that `tests/test_case_company.py` types into the app on every test "
        "run, checking every figure below. If the app changes and this guide stops matching, that test fails.",
        "",
        "## How to work through it",
        "",
        "- Go in order. Quantities count when they are saved, not by their date, so a pay app picks up whatever has been "
        "entered before it.",
        "- Type the **dates** given for job records, change order signatures and the job start dates. Sending a pay app, "
        "signing it, recording a payment and sending a waiver are dated the day you do them, which is fine: the figures "
        "to check are dollars, and they don't depend on the day.",
        "- Use your own inbox with plus-addressing: add `+dana`, `+rosa` and so on before the `@`. Below, `you@example.com` "
        "stands for your address. On staging, each address you use has to be on the email allow-list; send them to "
        "Yusuf first.",
        "- Have two or three photos on hand. Any photos will do: they stand in for signed change orders, signed waivers "
        "and time sheets.",
        "- About three hours in all: the setup takes 20 minutes, each week about an hour.",
        "",
        "## The company",
        "",
        "Sign up with \"Your name\" Dana Whitfield, \"Company name\" Sweetgum Ridge Mechanical and your `+dana` address. "
        "Then Settings > Company > \"Company details\":",
        "",
        "| Box | Type this |",
        "|---|---|",
        f"| \"Company name\" | {co['name']} |",
        f"| \"Phone\" | {co['phone']} |",
        f"| \"Street address\" | {co['street']} |",
        f"| \"City\" / \"State\" / \"ZIP code\" | {co['city']} / {co['state']} / {co['zip']} |",
        f"| \"Contractor licence number\" | {co['license']} |",
        f"| \"Legal name (on contracts and lien waivers)\" | {co['legal_name']} |",
        "",
        "## The team",
        "",
        "Settings > Team. First add three permission profiles (only an Owner can), then invite four people.",
        "",
        "| Profile | \"It can\" boxes to tick | Why |",
        "|---|---|---|",
    ]
    words = {"send_payapps": "Send pay apps", "approve_payapps": "Approve pay apps before they go", "send_waivers": "Send lien waivers",
             "ask_retainage": "Ask for retainage", "record_payments": "Record payments", "manage_jobs": "Change a job's contract, change orders and mark work done",
             "close_jobs": "Finish and reopen jobs", "company": "Edit company details, client settings and connections",
             "financials": "See company financials", "sensitive_docs": "See payroll and sensitive documents"}
    for p in cc.PROFILES:
        out.append(f"| {p['name']} | {', '.join(words[k] for k in p['perms']) or 'none'} | {p['why']} |")
    out += ["", "| Person | Job | \"Their email\" | \"Profile\" |", "|---|---|---|---|"]
    for t in cc.TEAM:
        out.append(f"| {t['name']} | {t['title']} | `you+{t['plus']}@example.com` | {t['profile']}{' (you)' if t['profile'] == 'Owner' else ''} |")
    out += ["", "Accepting the invites is optional. To test permissions, accept Terrell's in another browser and enter one "
                "week's job records for Linden Hollow as him: he can save job records but can't send a pay app or change "
                "the contract.", "",
            "## The clients", "",
            "Clients > \"Add client\" for each. Tick the supporting documents listed.", "",
            "| Box | " + " | ".join(c["name"] for c in cc.CLIENTS) + " |",
            "|---|" + "---|" * len(cc.CLIENTS),
            "| \"Client type\" | " + " | ".join(c["type_text"] for c in cc.CLIENTS) + " |",
            "| \"Who signs off on your bills\" | " + " | ".join(c["approver"] for c in cc.CLIENTS) + " |",
            "| \"Email for pay apps\" | " + " | ".join(f"`you+{c['plus']}@example.com`" for c in cc.CLIENTS) + " |",
            "| \"Phone\" | " + " | ".join(c["phone"] for c in cc.CLIENTS) + " |",
            "| \"Pay apps due by (day of the month)\" | " + " | ".join(str(c["payapp_due_day"] or "blank") for c in cc.CLIENTS) + " |",
            "| \"Payment terms, days\" | " + " | ".join(str(c["payment_terms_days"]) for c in cc.CLIENTS) + " |",
            "| Supporting documents | " + " | ".join(", ".join(c["packet"]) if c["packet"] else "leave unticked" for c in cc.CLIENTS) + " |",
            "| What they are like | " + " | ".join(c["about"] for c in cc.CLIENTS) + " |",
            "",
            "Lena Ortiz signs her own bills, so when it asks whether the signer is the client herself, say yes. On "
            "Thornwell's client card, set \"Usual retainage\" to 5 and tap \"Save client\".", "",
            "## The jobs", "",
            "Open jobs > \"New job\" for each, with the client picked under \"Existing client\". Open \"Schedule of values\" "
            "and type the lines; they add up to the contract amount exactly.", ""]
    for j in cc.JOBS:
        c = _client(j["client"])
        out += [f"### {j['name']}", "", j["about"], "",
                "| Box | Type this |", "|---|---|",
                f"| \"Job name\" | {j['name']} |",
                f"| \"Street address\" / \"City\" / \"State\" / \"ZIP\" | {j['street']} / {j['city']} / {j['state']} / {j['zip']} |",
                f"| \"Client\" | {c['name']} |",
                f"| \"Contract amount ($)\" | {_money(j['contract'])} |",
                f"| \"Retainage\" % | {j['retainage']} |",
                f"| \"Start date\" | {_short(j['started_on'])}, 2026 |",
                f"| \"Anticipated end date\" | {date.fromisoformat(j['anticipated_end_on']).strftime('%b')} {date.fromisoformat(j['anticipated_end_on']).day}, {j['anticipated_end_on'][:4]} |"]
        if j["contract_number"]:
            out.append(f"| \"Contract number\" | {j['contract_number']} |")
        out.append(f"| \"Assigned to\" (once the team has joined) | {j['assignee']} |")
        out += ["", "| # | \"Description\" | \"Qty\" | \"Unit\" | \"Unit rate $\" | Line total |", "|---|---|---|---|---|---|"]
        for n, (d, q, u, r) in enumerate(j["lines"], 1):
            out.append(f"| {n} | {d} | {q:,} | {u} | {r:,.2f} | {_money(q * r)} |")
        out += [f"| | **Total** | | | | **{_money(sum(q * r for _, q, _, r in j['lines']))}** |", ""]

    week = 0
    for day, steps in cc.DAYS:
        d = date.fromisoformat(day)
        if d.weekday() == 0:
            week += 1
            out += [f"## Week {week}", ""]
        out += [f"### {_day(day)}", ""]
        for s in steps:
            kind = s["do"]
            if kind == "record":
                block = _record(s, day)
            elif kind == "co":
                block = _co(s)
            elif kind == "price":
                block = _price(s)
            elif kind == "payapp":
                block = _payapp(s, False)
            elif kind == "final":
                block = _payapp(s, True)
            elif kind == "payment":
                block = _payment(s)
            elif kind == "waiver":
                block = _waiver(s)
            elif kind == "finish":
                block = _finish(s)
            else:
                block = _check(s["name"])
            out += block + [""]

    out += ["## Where the case ends", "",
            "- Ortiz Repipe is finished: billed, paid in full, final waiver sent.",
            "- Linden Hollow has two pay apps out. Ironbridge paid $90,000.00 of the first, so $6,624.00 of it is still "
            "owed, and CO 2 (the beam reroute, $2,160.00) is waiting for their signature.",
            "- Sorrel Creek Elementary has its first pay app out, nothing paid yet.",
            "",
            "Things to try from here, with no figures to check: get CO 2 signed with \"Request signature\"; record the "
            "rest of Ironbridge's payment or accept the short pay; look at Money > Financials and the money strip; open the "
            "Dashboard's To Do as Rosa or Terrell.",
            "",
            "## What to report",
            "",
            "For anything that is different from this guide or doesn't work: the day and step, what you typed, what you "
            "expected, what you saw, and a screenshot. Send it to Yusuf.",
            ""]
    return "\n".join(out)
