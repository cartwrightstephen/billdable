"""The Duracash helper: prepared answers, matched by rules, with live data.

Answers live in a database the team keeps growing:
  helper/answers.json                 shipped with the code (reviewed in Git)
  <data dir>/helper_answers.json      added or edited by founders in the app,
                                      no deploy needed; same id overrides
Every question is logged to <data dir>/helper_log.json with what it matched,
so unanswered and unhelpful questions show up for the founders to answer.

There is no AI model here. Matching is word overlap with synonyms; "agentic"
answers read the company's own jobs and offer buttons that call the same
endpoints as the rest of the app, so every gate and confirmation still applies.
"""

from __future__ import annotations

import json
import math
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import domain
import storage

ROOT = Path(__file__).resolve().parent
SHIPPED_KB = ROOT / "helper" / "answers.json"
LOCAL_KB = storage.DATA_DIR / "helper_answers.json"
LOG_PATH = storage.DATA_DIR / "helper_log.json"
LOG_LIMIT = 5000
LOCK = threading.Lock()

ANSWER_AT = 0.42      # confident match
SUGGEST_AT = 0.22     # close enough to offer "did you mean"

STOPWORDS = set("""a an the i me my we our you your it its is are was were be been am do does did doing to of in on at for
from with by about as into than then so and or but if can could would should will shall may might must how what when
where which who whom why this that these those there here please hi hello hey just some any all much many get got have
has had need want like know tell show give let us up out over one also not no yes ok okay thanks thank""".split())

# Phrase -> canonical token. Longest phrases are replaced first.
SYNONYMS = {
    "pay application": "payapp", "pay apps": "payapp", "pay app": "payapp", "payapps": "payapp", "payapp": "payapp",
    "requisition": "payapp", "invoice": "payapp", "invoices": "payapp", "bill": "payapp", "billing": "payapp", "billed": "payapp",
    "change orders": "changeorder", "change order": "changeorder", "cos": "changeorder", "co": "changeorder", "extras": "changeorder",
    "lien waivers": "waiver", "lien waiver": "waiver", "lien release": "waiver", "waivers": "waiver",
    "retention": "retainage", "holdback": "retainage", "held back": "retainage", "retainer": "retainage",
    "punch list": "punch", "punchlist": "punch", "close out": "closeout", "close-out": "closeout",
    "quickbooks online": "qbo", "quickbooks": "qbo", "quick books": "qbo", "qb": "qbo",
    "text messages": "sms", "text message": "sms", "texts": "sms", "texting": "sms", "sms": "sms",
    "e-mail": "email", "emails": "email", "mail": "email",
    "log in": "login", "sign in": "login", "signin": "login", "log-in": "login", "logged in": "login",
    "hasn't paid": "owed", "haven't paid": "owed", "hasnt paid": "owed", "havent paid": "owed", "not paid": "owed",
    "owes me": "owed", "owe me": "owed", "owe": "owed", "owes": "owed", "unpaid": "owed", "outstanding": "owed", "receivable": "owed",
    "follow up": "followup", "follow-up": "followup", "chase": "followup", "remind": "followup", "reminder": "followup", "nudge": "followup",
    "upload": "import", "uploads": "import", "attach": "import", "drag": "import", "drop": "import", "importer": "import",
    "pencil draft": "pencil", "pencil copy": "pencil", "pencil requisition": "pencil",
    "to do": "todo", "to-do": "todo", "today": "todo", "priorities": "todo", "next": "todo",
    "phone": "mobile", "iphone": "mobile", "android": "mobile", "cell": "mobile",
    "price": "pricing", "cost": "pricing", "costs": "pricing", "subscription": "pricing", "trial": "pricing", "plan": "pricing",
    "blocked": "blocker", "blocking": "blocker", "stuck": "blocker", "holding up": "blocker", "missing": "blocker",
}
_SYN_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(SYNONYMS, key=len, reverse=True)) + r")\b")


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    text = (text or "").lower().replace("’", "'")
    text = _SYN_RE.sub(lambda m: " " + SYNONYMS[m.group(1)] + " ", text)
    words = re.findall(r"[a-z0-9]+", text)
    return [_stem(w) for w in words if w not in STOPWORDS and len(w) > 1]


def normalize(text: str) -> str:
    return " ".join(tokens(text))


# ---------- knowledge base ----------

def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("answers", []) if isinstance(data, dict) else data


def load_kb() -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for entry in _read(SHIPPED_KB):
        by_id[entry["id"]] = {**entry, "origin": "shipped"}
    for entry in _read(LOCAL_KB):
        if entry.get("deleted"):
            by_id.pop(entry["id"], None)
        else:
            by_id[entry["id"]] = {**entry, "origin": "added in app"}
    return list(by_id.values())


def _idf(kb: list[dict[str, Any]]) -> dict[str, float]:
    docs = [set(tokens(" ".join(e.get("questions", []) + e.get("keywords", [])))) for e in kb]
    n = len(docs) or 1
    df: dict[str, int] = {}
    for d in docs:
        for t in d:
            df[t] = df.get(t, 0) + 1
    return {t: math.log(1 + n / c) for t, c in df.items()}


def _score(q: list[str], entry: dict[str, Any], idf: dict[str, float]) -> float:
    qs = set(q)
    if not qs:
        return 0.0
    w = lambda ts: sum(idf.get(t, math.log(1 + len(idf) or 2)) for t in ts)
    best = 0.0
    for example in entry.get("questions", []):
        es = set(tokens(example))
        if not es:
            continue
        overlap = w(qs & es)
        if overlap:
            best = max(best, overlap / math.sqrt(w(qs) * w(es)))
    kws = {normalize(k) for k in entry.get("keywords", [])}
    joined = " ".join(q)
    hits = sum(1 for k in kws if k and (f" {k} " in f" {joined} "))
    # Not capped at 1.0: a keyword hit breaks ties between equally good example matches.
    return best + 0.12 * min(hits, 3)


def rank(question: str, kb: list[dict[str, Any]] | None = None) -> list[tuple[float, dict[str, Any]]]:
    kb = kb if kb is not None else load_kb()
    idf = _idf(kb)
    q = tokens(question)
    return sorted(((_score(q, e, idf), e) for e in kb), key=lambda se: -se[0])


# ---------- finding the job a question is about ----------

def find_job(state: dict[str, Any], question: str) -> dict[str, Any] | None:
    raw = question or ""
    upper = set(re.findall(r"\b[A-Z0-9]{3,8}\b", raw))
    jobs = state.get("jobs", [])
    for j in jobs:
        if j.get("code") in upper:
            return j
    q = set(re.findall(r"[a-z0-9]+", raw.lower()))
    best, best_hits, tie = None, 0, False
    for j in jobs:
        words = {w for w in re.findall(r"[a-z0-9]+", j.get("name", "").lower()) if w not in STOPWORDS and len(w) >= 3}
        words -= {"street", "kitchen", "bath", "house", "job", "remodel", "addition", "renovation"}  # too common to identify a job alone
        hits = len(words & q)
        if hits > best_hits:
            best, best_hits, tie = j, hits, False
        elif hits and hits == best_hits:
            tie = True
    return None if tie or not best else best


# ---------- live-data answers ----------

def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def _job_btn(j: dict[str, Any]) -> dict[str, Any]:
    return {"type": "job", "job_id": j["id"], "label": f"Open {j['name']}"}


def _followup_due(snap: dict[str, Any], j: dict[str, Any]) -> tuple[dict[str, Any], int] | None:
    inv = next((i for i in j["invoices"] if i["status"] in {"sent", "partial"}), None)
    if not inv:
        return None
    days = domain._int_or(j["payer"].get("follow_up_days"), 7)
    since = _days_since(inv.get("last_follow_up_at") or inv.get("sent_at"))
    if since is None or since < days:
        return None
    return inv, since


def _follow_up_btn(r: dict[str, Any]) -> dict[str, Any]:
    return {"type": "post", "path": f"/api/jobs/{r['job_id']}/follow-up", "label": f"Follow up: {r['job']}",
            "confirm": f"Send a follow-up on {r['job']}? It is emailed when email is set up, and listed in the Outbox either way."}


# A Today row whose button is on Today or in the Inbox opens that page.
PAGE_LABELS = {"/today": "Open Today", "/inbox": "Open Inbox"}


def handle(name: str, snap: dict[str, Any], job: dict[str, Any] | None,
           state: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]] | None:
    jobs = [j for j in snap["jobs"] if not j.get("closed")]
    company = snap.get("company", {}).get("name", "your company")
    if name == "today":
        # Today's own queue (domain.today_rows), so the helper, the Today page and the daily email
        # never disagree on what is due.
        rows = domain.today_rows(state) if state is not None else []
        if not rows:
            return f"Nothing needs action right now at {company}. Nice.", [{"type": "nav", "view": "dashboard", "label": "Open Today"}]
        lines = [f"- {r['job']}: {r['label']}. {r['text']}" for r in rows[:8]]
        actions, seen = [], set()
        for r in rows:
            label = PAGE_LABELS.get(r["path"]) or (f"{r['button']}: {r['job']}" if r["button"] else "")
            if label and label not in seen:
                seen.add(label)
                if r["kind"] == "follow_up":
                    actions.append(_follow_up_btn(r))
                else:
                    actions.append({"type": "path", "path": r["path"], "label": label})
        return "Here is what needs you, most urgent first:\n" + "\n".join(lines), actions[:6]
    if name == "money":
        t = snap["totals"]
        billed_month = sum(i["amount_cents"] for j in snap["jobs"] for i in j["invoices"]
                           if i.get("sent_at") and i["sent_at"][:7] == datetime.now(timezone.utc).strftime("%Y-%m"))
        return (f"Across open jobs:\n- Finished but not billed: {domain.money(t['unbilled_cents'])}\n"
                f"- Billed and not paid: {domain.money(t['outstanding_cents'])}\n- Retainage still held: {domain.money(t['retainage_cents'])}\n"
                f"- Billed this month: {domain.money(billed_month)}"), [{"type": "nav", "view": "dashboard", "label": "Open Today"},
                                                                       {"type": "nav", "view": "collections", "label": "Open Unpaid"}]
    if name == "who_owes":
        owing = sorted((j for j in jobs if j["money"]["outstanding_cents"] > 0), key=lambda j: -j["money"]["outstanding_cents"])
        if not owing:
            return "Nobody owes you on a sent pay app right now.", []
        lines = []
        for j in owing[:8]:
            inv = next((i for i in j["invoices"] if i["status"] in {"sent", "partial", "approved"}), None)
            since = _days_since(inv.get("sent_at")) if inv else None
            lines.append(f"- {j['payer']['name']} on {j['name']}: {domain.money(j['money']['outstanding_cents'])}"
                         + (f", sent {since} days ago" if since is not None else ""))
        return "Who owes you, largest first:\n" + "\n".join(lines), [{"type": "nav", "view": "collections", "label": "Open Unpaid"}] + [_job_btn(j) for j in owing[:3]]
    if name == "followups":
        # The same follow-up rule as Today, including a call noted with a date to check again.
        due = [r for r in domain.today_rows(state) if r["kind"] == "follow_up"] if state is not None else []
        if not due:
            return ("No follow-ups are due. Each customer's wait comes from Days between follow-ups in Customer settings.",
                    [{"type": "nav", "view": "collections", "label": "Open Unpaid"}])
        lines = [f"- {r['job']}: {r['text']}" for r in due]
        actions = [_follow_up_btn(r) for r in due[:4]]
        return "These follow-ups are due now:\n" + "\n".join(lines), actions
    if name == "draft_followup":
        if not job:
            return None
        inv = next((i for i in job["invoices"] if i["status"] in {"sent", "partial"}), None)
        if not inv:
            return f"{job['name']} has no sent, unpaid pay app to follow up on.", [_job_btn(job)]
        p = job["payer"]
        note = (f"Hi {p.get('approver') or p['name']},\n\nChecking on {inv['number']} for {job['name']} "
                f"({domain.money(inv['amount_cents'])}), sent {(inv.get('sent_at') or '')[:10]}. Is anything missing on your side?\n\nThanks,\n{company}")
        actions = [{"type": "copy", "text": note, "label": "Copy note"}]
        if _followup_due(snap, job):
            actions.append({"type": "post", "path": f"/api/jobs/{job['id']}/follow-up", "label": "Send the follow-up",
                            "confirm": f"Send a follow-up on {inv['number']} to {p.get('ap_email') or 'the customer'}? It is emailed when email is set up, and listed in the Outbox either way."})
        return f"Here is a follow-up for {job['name']}:\n\n{note}", actions + [_job_btn(job)]
    if name == "blockers":
        if not job:
            blocked = [j for j in jobs if j["blockers"]]
            if not blocked:
                return "No open job is blocked.", []
            return "Blocked jobs:\n" + "\n".join(f"- {j['name']}: {j['blockers'][0]}" for j in blocked[:8]), [_job_btn(j) for j in blocked[:4]]
        if not job["blockers"]:
            return f"{job['name']} has nothing blocking it. Status: {job['status_label']}.", [_job_btn(job)]
        return f"{job['name']} is blocked by:\n" + "\n".join(f"- {b}" for b in job["blockers"]), [_job_btn(job), {"type": "packet", "job_id": job["id"], "inv": "", "label": "Open paperwork"}]
    if name == "job_status":
        if not job:
            return None
        m = job["money"]
        return (f"{job['name']} ({job['payer']['name']}): {job['status_label']}.\n"
                f"- Contract total {domain.money(m['contract_total_cents'])}, billed {domain.money(m['billed_cents'])}, collected {domain.money(m['collected_cents'])}\n"
                f"- Not billed yet {domain.money(m['unbilled_completed_cents'])}, retainage held {domain.money(m['retainage_open_cents'])}"), [_job_btn(job)]
    if name == "unbilled":
        rows = [j for j in jobs if j["money"]["unbilled_completed_cents"] > 0]
        if not rows:
            return "All finished work is on a pay app.", []
        return "Finished work not billed yet:\n" + "\n".join(f"- {j['name']}: {domain.money(j['money']['unbilled_completed_cents'])}" for j in rows), \
            [{"type": "post", "path": f"/api/jobs/{j['id']}/draft", "label": f"Draft for {j['name']}", "confirm": f"Draft a pay app for {j['name']}?"} for j in rows[:4]]
    if name == "retainage_list":
        rows = [j for j in jobs if j["money"]["retainage_open_cents"] > 0]
        if not rows:
            return "No retainage is being held on open jobs.", [{"type": "nav", "view": "retainage", "label": "Open Retainage"}]
        lines = []
        for j in rows:
            items = (j.get("closeout_packet") or []) or [p for p in j.get("packet", []) if p.get("kind") == "closeout"]
            missing = [p["label"] for p in items if p.get("required") and not p.get("present")]
            state_txt = "job still in progress" if j.get("work_status") != "complete" else (f"waiting on {', '.join(missing)}" if missing else "ready to ask for release")
            lines.append(f"- {j['name']}: {domain.money(j['money']['retainage_open_cents'])}, {state_txt}")
        return "Retainage held:\n" + "\n".join(lines), [{"type": "nav", "view": "retainage", "label": "Open Retainage"}]
    if name == "waivers_due":
        rows = [j for j in jobs if j.get("waiver_due") and not j.get("waiver_sent")]
        if not rows:
            return "No waivers are due. A waiver comes due when a pay app is paid in full.", []
        return "Waivers due:\n" + "\n".join(f"- {j['name']}" for j in rows), [_job_btn(j) for j in rows[:4]]
    if name == "inbox":
        new = [i for i in snap.get("inbox", []) if i["status"] == "new"]
        if not new:
            return "The Inbox is empty. Drop files on any page, paste an email or notes in the Inbox, or forward email and texts once set up.", [{"type": "nav", "view": "inbox", "label": "Open Inbox"}]
        suggested = len([i for i in new if i.get("suggested_job_id")])
        return f"{len(new)} item(s) are waiting; {suggested} already have a suggested job.", [{"type": "nav", "view": "inbox", "label": "Open Inbox"}]
    if name == "pending_cos":
        rows = [(j, co) for j in jobs for co in j["change_orders"] if co["status"] == "pending"]
        if not rows:
            return "No change orders are waiting on a signature.", []
        return "Change orders waiting on a signature:\n" + "\n".join(f"- {j['name']}: CO {co['number']} {co['title']} ({domain.money(co['amount_cents'])})" for j, co in rows), \
            [{"type": "nav", "view": "cos", "label": "Open Change orders"}]
    if name == "closeable":
        rows = [j for j in jobs if not j.get("close_blockers")]
        if not rows:
            return "No job is ready to close yet. A job closes when everything is billed, paid, released and waived.", []
        return "Ready to finish:\n" + "\n".join(f"- {j['name']}" for j in rows), [_job_btn(j) for j in rows[:4]]
    return None


# ---------- answering ----------

def _log(entry: dict[str, Any]) -> None:
    with LOCK:
        data = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else []
        data.append(entry)
        storage.write_json(LOG_PATH, data[-LOG_LIMIT:])


def answer(question: str, state: dict[str, Any], company_id: str, user_id: str, view: str = "", context_job_id: str = "") -> dict[str, Any]:
    question = (question or "").strip()[:500]
    if not question:
        raise domain.GateError("Type a question.")
    snap = domain.snapshot(state)
    job = find_job(snap, question)
    if not job and context_job_id:
        job = next((j for j in snap["jobs"] if j["id"] == context_job_id), None)
    ranked = rank(question)
    top_score, top = ranked[0] if ranked else (0.0, None)
    reply: dict[str, Any] = {"actions": [], "suggestions": []}
    matched = None
    if top and top_score >= ANSWER_AT:
        matched = top
        text = top.get("answer", "")
        actions = list(top.get("actions", []))
        if top.get("handler"):
            if top.get("needs_job") and not job:
                text = "Which job do you mean?"
                actions = [{"type": "ask", "q": f"{question} {j['name']}", "label": j["name"]} for j in snap["jobs"] if not j.get("closed")][:6]
            else:
                live = handle(top["handler"], snap, job, state)
                if live:
                    text, extra = live
                    actions = extra + actions
        reply.update(text=text, actions=actions[:7], suggestions=top.get("follow_ups", [])[:3])
    else:
        close = [e for s, e in ranked[:3] if s >= SUGGEST_AT]
        if close:
            reply["text"] = "I'm not sure I understood. Did you mean one of these?"
            reply["suggestions"] = [e["questions"][0] for e in close]
        else:
            reply["text"] = ("I don't have an answer for that yet. I've saved the question so the Duracash team can add one. "
                             "Try asking what to do today, who owes you, or how to import an email.")
            reply["suggestions"] = ["What should I do today?", "Who owes me money?", "How do I import emails and texts?"]
    log_id = "q-" + secrets.token_hex(6)
    _log({
        "id": log_id, "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "company_id": company_id, "user_id": user_id,
        "question": question, "normalized": normalize(question), "view": view[:40],
        "matched": matched["id"] if matched else None, "score": round(top_score, 3), "top_guess": top["id"] if top else None,
        "helpful": None, "resolved": False,
    })
    reply.update(log_id=log_id, matched=matched["id"] if matched else None, score=round(top_score, 3))
    return reply


def feedback(log_id: str, helpful: bool, note: str, company_id: str) -> None:
    with LOCK:
        data = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else []
        for e in data:
            if e["id"] == log_id and e["company_id"] == company_id:
                e["helpful"] = bool(helpful)
                e["note"] = (note or "")[:500]
                storage.write_json(LOG_PATH, data)
                return
    raise domain.GateError("That question was not found.")


# ---------- founders' review ----------

def review() -> dict[str, Any]:
    data = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else []
    groups: dict[str, dict[str, Any]] = {}
    for e in data:
        if e.get("resolved"):
            continue
        if e.get("matched") and e.get("helpful") is not False:
            continue
        key = e.get("normalized") or e["question"].lower()
        g = groups.setdefault(key, {"normalized": key, "examples": [], "count": 0, "last_at": "", "unhelpful": 0, "top_guess": e.get("top_guess")})
        g["count"] += 1
        g["unhelpful"] += 1 if e.get("helpful") is False else 0
        g["last_at"] = max(g["last_at"], e["at"])
        if e["question"] not in g["examples"] and len(g["examples"]) < 3:
            g["examples"].append(e["question"])
    total = len(data)
    answered = len([e for e in data if e.get("matched")])
    return {
        "open": sorted(groups.values(), key=lambda g: (-g["count"], g["last_at"]), reverse=False)[:100],
        "stats": {"questions": total, "answered": answered, "helpful": len([e for e in data if e.get("helpful") is True]),
                  "unhelpful": len([e for e in data if e.get("helpful") is False])},
        "answers": [{k: e.get(k) for k in ("id", "category", "questions", "keywords", "answer", "handler", "needs_job", "actions", "follow_ups", "origin")} for e in load_kb()],
        "handlers": ["today", "money", "who_owes", "followups", "draft_followup", "blockers", "job_status", "unbilled",
                     "retainage_list", "waivers_due", "inbox", "pending_cos", "closeable"],
    }


def save_answer(entry: dict[str, Any], editor: str) -> dict[str, Any]:
    """Add or replace an answer in the in-app database. Same id overrides a shipped answer."""
    for key in ("questions", "keywords", "follow_ups", "actions"):
        if not isinstance(entry.get(key), list):
            entry[key] = []
    questions = [q.strip() for q in entry.get("questions", []) if isinstance(q, str) and q.strip()][:20]
    text = entry.get("answer", "")
    if not questions:
        raise domain.GateError("Give at least one example question.")
    if not isinstance(text, str) or not text.strip():
        raise domain.GateError("Write the answer.")
    handler = entry.get("handler") or None
    if handler and handler not in review()["handlers"]:
        raise domain.GateError("Unknown live-data handler.")
    actions = []
    for a in entry.get("actions", []) or []:
        if isinstance(a, dict) and a.get("type") == "nav" and isinstance(a.get("view"), str) and isinstance(a.get("label"), str):
            actions.append({"type": "nav", "view": a["view"][:30], "label": a["label"][:60]})
    answer_id = entry.get("id") or re.sub(r"[^a-z0-9]+", "-", questions[0].lower()).strip("-")[:60] or "answer-" + secrets.token_hex(3)
    clean = {
        "id": str(answer_id)[:60],
        "category": str(entry.get("category") or "General")[:40],
        "questions": questions,
        "keywords": [k.strip()[:40] for k in entry.get("keywords", []) if isinstance(k, str) and k.strip()][:12],
        "answer": text.strip()[:4000],
        "handler": handler,
        "needs_job": bool(entry.get("needs_job")),
        "actions": actions,
        "follow_ups": [f.strip()[:120] for f in entry.get("follow_ups", []) if isinstance(f, str) and f.strip()][:4],
        "edited_by": editor,
        "edited_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    with LOCK:
        local = _read(LOCAL_KB)
        local = [e for e in local if e.get("id") != clean["id"]] + [clean]
        storage.write_json(LOCAL_KB, {"answers": local})
        # Questions this answer now covers stop showing as open.
        data = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else []
        covered = {normalize(q) for q in questions}
        for e in data:
            if e.get("normalized") in covered:
                e["resolved"] = True
        storage.write_json(LOG_PATH, data)
    return clean


def resolve(normalized: str) -> None:
    with LOCK:
        data = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else []
        for e in data:
            if e.get("normalized") == normalized:
                e["resolved"] = True
        storage.write_json(LOG_PATH, data)


def forget_company(company_id: str) -> None:
    """Remove a deleted company's questions from the log."""
    with LOCK:
        if LOG_PATH.exists():
            data = json.loads(LOG_PATH.read_text())
            storage.write_json(LOG_PATH, [e for e in data if e.get("company_id") != company_id])
