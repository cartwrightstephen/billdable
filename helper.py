"""The Duracash helper: prepared answers, matched by rules, with live data.

Answers live in a database the team keeps growing:
  helper/answers.json                 shipped with the code (reviewed in Git)
  <data dir>/helper_answers.json      added or edited by founders in the app,
                                      no deploy needed; same id overrides
Every question is logged to <data dir>/helper_log.json with what it matched,
so unanswered and unhelpful questions show up for the founders to answer.

There is no AI model here. Matching is word overlap with synonyms; live answers
read the company's own jobs. Since Round 2 (Stephen, 10 Nov, decision 12) it also
opens pages and forms on a plain command (helper_intents.py), and every button it
offers only opens something: the helper never changes data itself (SAFE_ACTIONS).
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
import financials
import helper_intents
import storage
from domain import profiles

ROOT = Path(__file__).resolve().parent
SHIPPED_KB = ROOT / "helper" / "answers.json"
LOCAL_KB = storage.DATA_DIR / "helper_answers.json"
LOG_PATH = storage.DATA_DIR / "helper_log.json"
LOG_LIMIT = 5000
LOCK = threading.Lock()

ANSWER_AT = 0.42      # confident match
SUGGEST_AT = 0.22     # close enough to offer "did you mean"
EXACT_BONUS = 0.5     # an answer's own example question, typed as it is written there (rank)
COVER_AT = 0.5        # share of the question's known words an answer must cover (_covers)
# Words a question picks up on the way that say nothing about what it asks ("... in Duracash"): an
# answer that doesn't use them isn't missing them (Round 1 hunt). Only the app's own name: "asking"
# and "today" can be what a question is about ("Why is the app asking me which waiver it is?",
# "Can I record yesterday's work today?"), so they count as any word does.
FILLER = {"duracash"}

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
# "not billed" keeps its "not" (a stop word): finished work not billed yet is the unbilled answer, not
# a pay app (Round 2 review, B10). Not when the question is about retainage or a pay app or draft
# ("retainage that hasn't been billed", "pay apps drafted but not billed"): there it reads as it did.
_NOT_BILLED = re.compile(r"\b(?:(?:is|are|has|have|was|were)(?:n't|nt|\s+not)\s+(?:been\s+|yet\s+)?|not\s+(?:yet\s+|been\s+)?"
                         r"|(?:haven't|havent|hasn't|hasnt)\s+(?:i|we|you)\s+)billed\b")
_NOT_BILLED_ABOUT = re.compile(r"\b(?:retainage|retention|holdback|held back|draft\w*|pay\s*apps?|pay applications?|invoices?|requisitions?)\b")


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


# "my client is asking for retainage": someone else asking is how the question is put, not what it
# is about, so the phrase is left out; the app or Duracash asking ("Why is the app asking me which
# waiver it is?") is what a question can be about.
_ASKERS = ("client", "customer", "gc", "owner", "contractor", "sub", "subcontractor", "supplier", "vendor", "architect", "engineer", "payer", "they", "he", "she", "someone", "everyone")
_REPORTED = re.compile(r"\b(?:" + "|".join(_ASKERS) + r")(?:\s+(?:is|are|was|were|keeps|keep|kept|has been|have been))?\s+asking\b")


def _synonyms(text: str) -> str:
    text = _REPORTED.sub(" ", (text or "").lower().replace("’", "'"))
    if not _NOT_BILLED_ABOUT.search(text):
        text = _NOT_BILLED.sub(" unbilled ", text)
    return _SYN_RE.sub(lambda m: " " + SYNONYMS[m.group(1)] + " ", text)


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", _synonyms(text))
    return [_stem(w) for w in words if w not in STOPWORDS and len(w) > 1]


def normalize(text: str) -> str:
    return " ".join(tokens(text))


def _phrase(text: str) -> str:
    """The text as tokens() reads it, stop words kept: how a keyword written in quotes is matched."""
    return " ".join(_stem(w) for w in re.findall(r"[a-z0-9]+", _synonyms(text)))


def _plain(text: str) -> str:
    """A question as typed, without case or punctuation: how an example question is recognised."""
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower().replace("’", "'").replace("'", "")))


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


def _score(q: list[str], entry: dict[str, Any], idf: dict[str, float], phrase: str = "") -> float:
    qs = set(q)
    if not qs:
        return 0.0
    w = lambda ts: sum(idf.get(t, math.log(1 + len(idf) or 2)) for t in ts)
    best, best_tokens = 0.0, set()
    for example in entry.get("questions", []):
        es = set(tokens(example))
        if not es:
            continue
        overlap = w(qs & es)
        if overlap and overlap / math.sqrt(w(qs) * w(es)) > best:
            best, best_tokens = overlap / math.sqrt(w(qs) * w(es)), es
    joined, phrase = " ".join(q), phrase or " ".join(q)
    hit = []
    for k in entry.get("keywords", []):
        # A keyword in quotes matches only as written, stop words and all: "which job" is not any
        # question with "job" in it (Round 2 review, B28). Others match on their telling words, as
        # they always did ("how work" is any question about how it works).
        quoted = len(k) > 2 and k[0] == k[-1] == '"'
        nk = normalize(k)
        if nk and nk not in hit and (f" {_phrase(k[1:-1])} " in f" {phrase} " if quoted else f" {nk} " in f" {joined} "):
            hit.append(nk)
    # Not capped at 1.0: a keyword hit breaks ties between equally good example matches.
    score = best + 0.12 * min(len(hit), 3)
    if not _covers(qs, best_tokens | {t for k in hit for t in k.split()}, idf):
        return min(score, SUGGEST_AT - 0.01)
    return score


def _covers(qs: set[str], matched: set[str], idf: dict[str, float]) -> bool:
    """Whether an answer covers the question (round 1, item 20). Of the question's words that some
    answer uses (a job's name is no answer's), it must cover at least COVER_AT of their weight, and
    the rarest of them, the one that says what the question is about: "my GC wants a notice to
    owner" is about the notice, which the Client type answer, matching GC and owner, doesn't cover.
    One that doesn't is neither the answer nor a "Did you mean": the helper says it doesn't know.
    A FILLER word the answer doesn't use is left out of both."""
    known = {t for t in qs if t in idf and (t not in FILLER or t in matched)}
    hit = known & matched
    if not known:
        return True
    if not hit or sum(idf[t] for t in hit) < COVER_AT * sum(idf[t] for t in known):
        return False
    missed = known - hit
    return not missed or max(idf[t] for t in missed) <= max(idf[t] for t in hit)


def _says_what_it_needs(question: str, entry: dict[str, Any]) -> bool:
    """An answer that lists words it needs ("needs") is only the answer to a question that uses one
    of them, as written, stop words and all: "Why is my GC asking for a waiver?" is not about
    which waiver to tap. An answer with none lists nothing."""
    needs = entry.get("needs") or []
    text = (question or "").lower().replace("’", "'")
    return not needs or any(re.search(rf"\b{re.escape(n.lower())}(?:s|es|ed|ing|ped|ping)?\b", text) for n in needs)


def rank(question: str, kb: list[dict[str, Any]] | None = None) -> list[tuple[float, dict[str, Any]]]:
    kb = kb if kb is not None else load_kb()
    idf = _idf(kb)
    q, phrase, plain = tokens(question), _phrase(question), _plain(question)

    def score(e: dict[str, Any]) -> float:
        s = _score(q, e, idf, phrase)
        # Short of what it needs, an answer is at most a "did you mean", never the answer.
        if not _says_what_it_needs(question, e):
            return min(s, SUGGEST_AT - 0.01)
        # One of its own example questions, as typed, gets that answer: "What did I send?" is the
        # Outbox's, though the send how-to's keyword "send" would outscore it (Round 2 review, B26).
        return s + EXACT_BONUS if plain and any(_plain(x) == plain for x in e.get("questions", [])) else s

    return sorted(((score(e), e) for e in kb), key=lambda se: -se[0])


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


def _drop_names(question: str, named: list[dict[str, Any]]) -> str:
    """The question without the words of the jobs' and clients' names it uses, and their codes. A
    short word of a name ("Co", "TI") only right after another of its words: "Ponce Property Co" is
    the client, while "how do I add a co" still asks about a change order."""
    names = {w for i in named for w in re.findall(r"[a-z0-9]+", i["name"].lower())} | {str(i.get("code") or "").lower() for i in named} - {""}
    out, prev = [], False
    for w in re.findall(r"[\w'’-]+", question):
        lw = w.lower()
        prev = lw in names and (len(lw) >= 3 or prev)
        if not prev:
            out.append(w)
    return " ".join(out)


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


# A Today row whose button is on Today or in the Inbox opens that page.
PAGE_LABELS = {"/dashboard": "Open Dashboard", "/communications": "Open Inbox"}


# Said instead of a company-wide money figure to someone without "See company financials" (decision 12).
NO_MONEY = "Company money figures need the \"See company financials\" permission. Ask an owner."
# The answers that are company-wide money figures (decision 12's rails): who owes, unbilled,
# retainage held, backlog, the totals. A job's own figures stay, as its page shows them to all.
MONEY_HANDLERS = {"money", "who_owes", "unbilled", "retainage_list", "backlog"}


def _row_action(r: dict[str, Any]) -> dict[str, Any] | None:
    """A Today row's page: its own when it has one, else its job's, where its button is. Never the
    button itself: the helper opens, the person acts (decision 12)."""
    if r["path"] in PAGE_LABELS:
        return {"type": "path", "path": r["path"], "label": PAGE_LABELS[r["path"]]}
    if r["path"]:
        return {"type": "path", "path": r["path"], "label": f"{r['button']}: {r['job']}" if r["button"] else f"Open {r['job']}"}
    if r.get("job_id"):
        return {"type": "job", "job_id": r["job_id"], "label": f"Open {r['job']}"}
    return None


def _my_rows(state: dict[str, Any] | None, perms: set[str]) -> list[dict[str, Any]]:
    """The Dashboard's To Do rows this person is shown: a row kept for a permission (a change order
    to price, decision 2) only for those who hold it, as on the Dashboard."""
    return domain.rows_with_perms(domain.today_rows(state), sorted(perms)) if state is not None else []


def _due_week(snap: dict[str, Any], state: dict[str, Any] | None, perms: set[str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """To Do rows and tasks due in the next seven days, and pay apps whose payment is due then
    (payment terms): the dates the Dashboard and the Calendar keep. No money in it."""
    today = datetime.now(timezone.utc).date()
    first, last = today.isoformat(), (today + timedelta(days=6)).isoformat()
    items: list[tuple[str, str, dict[str, Any] | None]] = []
    for r in _my_rows(state, set(profiles.PERM_KEYS) if perms is None else perms):
        due = r.get("due") or first
        if due <= last:
            items.append((max(due, first), f"{r['job']}: {r['label']}", _row_action(r)))
    jobs = {j["id"]: j for j in snap["jobs"]}
    for t in (state or {}).get("tasks", []):
        if t.get("status") != "done" and t.get("due") and t["due"] <= last:
            j = jobs.get(t.get("job_id") or "")
            items.append((max(t["due"], first), t.get("title", "") + (f" ({j['name']})" if j else ""), helper_intents.nav_action("dashboard")))
    for j in snap["jobs"]:
        for inv in j["invoices"]:
            if inv["status"] in domain.OPEN_FOR_PAYMENT and first <= str(inv.get("due_on") or "") <= last:
                items.append((inv["due_on"], f"{j['name']}: {inv['number']} payment due", helper_intents.job_action(j)))
    if not items:
        return "Nothing is due in the next seven days.", [helper_intents.nav_action("calendar")]
    items.sort(key=lambda i: i[0])
    day = lambda iso: "today" if iso == first else (lambda d: f"{d:%a %b} {d.day}")(datetime.fromisoformat(iso))
    lines = [f"- {day(d)}: {text}" for d, text, _ in items[:10]]
    actions, seen = [], set()
    for _, _, a in items:
        if a and a["label"] not in seen and len(actions) < 4:
            seen.add(a["label"])
            actions.append(a)
    return "Due this week:\n" + "\n".join(lines), actions + [helper_intents.nav_action("calendar")]


# "Is anything overdue?", "past-due", "late": due date before today, as Unpaid marks it (B18).
_OVERDUE_RE = re.compile(r"\b(?:over[\s-]*due|past[\s-]*due|late)\b", re.I)
_NOT_OVERDUE_RE = re.compile(r"\b(?:not|isn't|isnt|aren't|arent)\s+(?:yet\s+)?(?:over[\s-]*due|past[\s-]*due|late|due)\b", re.I)


# Late or overdue said of a follow-up or of sending, not of a payment (Round 2 review, B18).
_FOLLOWUP_LATE_RE = re.compile(r"\bfollow[\s-]*ups?\b|\bfollowups?\b|\blate\s+(?:to\s+|in\s+|on\s+)?send(?:ing)?\b"
                               r"|\bsend(?:ing)?\b[^.?!]{0,20}?\b(?:late|overdue)\b", re.I)


def _overdue_asked(question: str) -> bool:
    return bool(_OVERDUE_RE.search(question or "")) and not _not_overdue_asked(question)


def _not_overdue_asked(question: str) -> bool:
    return bool(_NOT_OVERDUE_RE.search(question or ""))


def _overdue(snap: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The aging rows (financials) whose pay app is past its due date."""
    today = datetime.now(timezone.utc).date().isoformat()
    due = {(j["id"], i["number"]): str(i.get("due_on") or "") for j in snap["jobs"] for i in j["invoices"]}
    return [r for r in rows if "" < due.get((r["job_id"], r["pay_app"]), "") < today]


def _undated(snap: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The aging rows whose pay app has no due date, so can't be called overdue or not."""
    due = {(j["id"], i["number"]): str(i.get("due_on") or "") for j in snap["jobs"] for i in j["invoices"]}
    return [r for r in rows if not due.get((r["job_id"], r["pay_app"]), "")]


def _named_scope(snap: dict[str, Any], question: str) -> tuple[str, set[str]] | None:
    """The client or job a question names, and the ids of the jobs that means; None for neither."""
    client = helper_intents.client_named(snap, question) if question else None
    if client:
        return client["name"], {j["id"] for j in snap["jobs"] if j["payer_id"] == client["id"]}
    job = find_job(snap, question) if question else None
    return (job["name"], {job["id"]}) if job else None


# A question asking how to do something wants the how-to first; any other ("Why can't I send?",
# "Approve and send") wants its live answer first, as at 198422a, with the how-to after it.
_HOW_RE = re.compile(r"^\s*(?:how\b|what\s+are\s+the\s+steps\b|walk\s+me\s+through\b|show\s+me\s+how\b)", re.I)


def _join(how_to: str, live: str, live_first: bool = False) -> str:
    """A prepared how-to, then the live lines, without a sentence the how-to already says (B26).
    live_first: the live lines lead and the how-to follows (Round 2 review, third pass)."""
    split = lambda t: [x for x in re.split(r"(?<=[.!?])\s+", t.strip()) if x]
    said = {_plain(x) for x in split(how_to)}
    lines = []
    for line in live.split("\n"):
        kept = [x for x in split(line) if _plain(x) not in said]
        if kept or not line.strip():
            lines.append(" ".join(kept))
    rest = "\n".join(lines).strip()
    if rest and live_first:
        return f"{rest}\n\n{how_to.strip()}"
    return f"{how_to.strip()}\n\n{rest}" if rest else how_to.strip()


def handle(name: str, snap: dict[str, Any], job: dict[str, Any] | None,
           state: dict[str, Any] | None = None, perms: set[str] | None = None, question: str = "",
           how_to: bool = False) -> tuple[str, list[dict[str, Any]]] | None:
    """A live answer from the company's own data. perms: what the person asking may do (None: all,
    for the tests and the founders' review); without "financials" no company-wide money figure.
    how_to: the lines follow a prepared how-to, which already says the rule, so only what is live."""
    perms = set(profiles.PERM_KEYS) if perms is None else perms
    if name in MONEY_HANDLERS and "financials" not in perms:
        return NO_MONEY, []
    money_ok = "financials" in perms
    jobs = [j for j in snap["jobs"] if not j.get("closed")]
    company = snap.get("company", {}).get("name", "your company")
    # The helper's own words in the company's word for a pay app (R6-01); names stay as typed.
    style = domain.billing_style(snap)
    pay_app = domain.term_text("pay app", style)
    if name == "today":
        # Today's own queue (domain.today_rows), so the helper, the Today page and the daily email
        # never disagree on what is due.
        rows = _my_rows(state, perms)
        # The Dashboard's To do is these rows and the open tasks added by hand, ranked as
        # static/js/tasks.js todoItems ranks them: a task due today or before just after the
        # paperwork rows (3.5), other tasks last (7); then soonest due (Round 2 review, B27).
        today = datetime.now(timezone.utc).date().isoformat()
        names = {j["id"]: j["name"] for j in snap["jobs"]}
        todo = [(r.get("rank", 5), r.get("due") or today, n, r, None) for n, r in enumerate(rows)]
        todo += [(3.5 if t.get("due") and t["due"] <= today else 7, t.get("due") or "9999", 1000 + n, None, t)
                 for n, t in enumerate(t for t in (state or {}).get("tasks", []) if t.get("status") != "done")]
        todo.sort(key=lambda i: i[:3])
        if not todo:
            return f"Nothing needs action right now at {company}. Nice.", [{"type": "nav", "view": "dashboard", "label": "Open Dashboard"}]
        # A row's text can carry amounts: without "See company financials" only its label (decision 12).
        task_line = lambda t: f"- {names[t['job_id']]}: {t.get('title', '')}" if t.get("job_id") in names else f"- {t.get('title', '')}"
        lines = [task_line(t) if t else (f"- {r['job']}: {r['label']}. {r['text']}" if money_ok else f"- {r['job']}: {r['label']}")
                 for *_, r, t in todo[:8]]
        actions, seen = [], set()
        for *_, r, t in todo:
            a = _row_action(r) if r else helper_intents.nav_action("dashboard")
            if a and a["label"] not in seen:
                seen.add(a["label"])
                actions.append(a)
        return "Here is your To do, most urgent first:\n" + "\n".join(lines), actions[:6]
    if name == "money":
        t = snap["totals"]
        # Billed this month as Financials counts it (financials.compute), so the two agree.
        this_month = datetime.now(timezone.utc).strftime("%Y-%m")
        billed_month = next((m["billed_cents"] for m in financials.compute(state if state is not None else snap)["monthly"]
                             if m["month"] == this_month), 0)
        return (f"Across open jobs:\n- Finished but not billed: {domain.money(t['unbilled_cents'])}\n"
                f"- Billed and not paid: {domain.money(t['outstanding_cents'])}\n- Retainage still held: {domain.money(t['retainage_cents'])}\n"
                f"- Backlog: {domain.money(t.get('backlog_cents', 0))}\n"
                f"- Billed this month: {domain.money(billed_month)}"), [{"type": "nav", "view": "financials", "label": "Open Financials"},
                                                                       {"type": "nav", "view": "collections", "label": "Open Unpaid"}]
    if name == "who_owes":
        # Money > Financials' own figures (financials.compute), so the helper, the page and the
        # money strip never disagree. A client named in the question gets just theirs.
        f = financials.compute(state if state is not None else snap)
        rows = [c for c in f["clients"] if c["owed_cents"] > 0]
        # Only a job the question names: on a job's page "who owes me?" still means everyone.
        job = job if job and question and find_job(snap, question) else None
        payer, _ = helper_intents.find_client(snap, question) if question and not job else (None, [])
        if job:
            # "How much does Turner owe?" names the job: what is owed on it, pay app by pay app.
            mine = [r for r in f["aging_rows"] if r["job_id"] == job["id"]]
            if not mine:
                return f"Nothing is owed on {job['name']} right now.", [_job_btn(job)]
            return (f"{job['name']} ({job['payer']['name']}) owes {domain.money(sum(r['cents'] for r in mine))}:\n"
                    + "\n".join(f"- {r['pay_app']}: {domain.money(r['cents'])}, {r['days']} days" for r in mine), [_job_btn(job), helper_intents.nav_action("collections")])
        if payer:
            rows = [c for c in rows if c["client"] == payer["name"]]
            if not rows:
                return f"{payer['name']} owes you nothing on a sent {pay_app} right now.", [helper_intents.client_action(payer)]
        if not rows:
            return f"Nobody owes you on a sent {pay_app} right now.", []
        total = sum(c["owed_cents"] for c in rows)
        lines = []
        for c in rows[:8]:
            oldest = max((r["days"] for r in f["aging_rows"] if r["client"] == c["client"]), default=None)
            lines.append(f"- {c['client']}: {domain.money(c['owed_cents'])}" + (f", oldest {oldest} days" if oldest else ""))
        head = f"{payer['name']} owes you {domain.money(total)}:" if payer else f"Owed to you: {domain.money(total)}, largest first:"
        owing = sorted({r["job_id"]: r for r in f["aging_rows"] if not payer or r["client"] == payer["name"]}.values(), key=lambda r: -r["cents"])
        return head + "\n" + "\n".join(lines), [helper_intents.nav_action("collections")] + [
            {"type": "job", "job_id": r["job_id"], "label": f"Open {r['job']}"} for r in owing[:3]]
    if name == "unpaid":
        # Each pay app still owed on, by days since it was sent (financials' aging): with amounts
        # for someone who may see the company's money, without for anyone else.
        f = financials.compute(state if state is not None else snap)
        rows = sorted(f["aging_rows"], key=lambda r: -r["days"])
        if not rows:
            return f"No sent {pay_app} is waiting for payment.", [helper_intents.nav_action("collections")]
        total = f["receivable_cents"]
        head = domain.term_text("Unpaid pay apps", style)
        # A job or client named: theirs only ("Is Westside overdue?"; Round 2 review, B18).
        scope = _named_scope(snap, question)
        if scope:
            rows = [r for r in rows if r["job_id"] in scope[1]]
            total, head = sum(r["cents"] for r in rows), f"{head} for {scope[0]}"
            if not rows:
                return f"Nothing is owed on a sent {pay_app} for {scope[0]} right now.", [helper_intents.nav_action("collections")]
        if _not_overdue_asked(question):
            # "What's not overdue yet?": the ones still inside their payment terms.
            late = {(r["job_id"], r["pay_app"]) for r in _overdue(snap, rows)}
            rows = [r for r in rows if (r["job_id"], r["pay_app"]) not in late]
            if not rows:
                return f"Every unpaid {pay_app}" + (f" for {scope[0]}" if scope else "") + " is past its due date.", [helper_intents.nav_action("collections")]
            total, head = sum(r["cents"] for r in rows), domain.term_text("Pay apps not due yet", style) + (f" for {scope[0]}" if scope else "")
        elif _overdue_asked(question):
            # Overdue as Unpaid marks it: due date before today (Round 2 review, B18).
            late = _overdue(snap, rows)
            undated = _undated(snap, rows)
            if not late and undated:
                # No due date set is not "nothing overdue": those are listed, oldest first, as they
                # were at 198422a, saying why (Round 2 review, third pass).
                rows = undated
                total = sum(r["cents"] for r in rows)
                head = (f"Nothing is past its due date" + (f" for {scope[0]}" if scope else "")
                        + f", but {'this' if len(rows) == 1 else 'these'} unpaid {pay_app}{'' if len(rows) == 1 else 's'} {'has' if len(rows) == 1 else 'have'} no due date set")
            elif not late:
                return f"Nothing is overdue" + (f" for {scope[0]}" if scope else "") + f": no unpaid {pay_app} is past its due date.", [helper_intents.nav_action("collections")]
            else:
                rows = late
                total, head = sum(r["cents"] for r in rows), domain.term_text("Overdue pay apps", style) + (f" for {scope[0]}" if scope else "")
        lines = [f"- {r['pay_app']}, {r['job']}: " + (f"{domain.money(r['cents'])}, " if money_ok else "") + f"{r['days']} days" for r in rows[:10]]
        head += ", oldest first" + (f" ({domain.money(total)} in all)" if money_ok else "") + ":"
        jobs_seen = list({r["job_id"]: r for r in rows}.values())[:3]
        return head + "\n" + "\n".join(lines), [helper_intents.nav_action("collections")] + [
            {"type": "job", "job_id": r["job_id"], "label": f"Open {r['job']}"} for r in jobs_seen]
    if name == "backlog":
        f = financials.compute(state if state is not None else snap)
        rows = [r for r in f["backlog_jobs"] if r["left_cents"] > 0]
        if not f["backlog_cents"]:
            return "No signed work is left to do on open jobs.", [helper_intents.nav_action("financials")]
        lines = [f"- {r['job']}: {domain.money(r['left_cents'])}" for r in rows[:6]]
        return (f"Backlog (signed work not done yet): {domain.money(f['backlog_cents'])}.\n" + "\n".join(lines),
                [helper_intents.nav_action("financials")] + [{"type": "job", "job_id": r["job_id"], "label": f"Open {r['job']}"} for r in rows[:2]])
    if name == "due_week":
        return _due_week(snap, state, perms)
    if name == "followups":
        # The same follow-up rule as Today, including a call noted with a date to check again.
        due = [r for r in _my_rows(state, perms) if r["kind"] == "follow_up"]
        if not due:
            return ("No follow-ups are due. Each client's wait comes from Days between follow-ups in the Client playbook.",
                    [{"type": "nav", "view": "collections", "label": "Open Unpaid"}])
        lines = [f"- {r['job']}: {r['text']}" for r in due]
        actions = [a for a in (_row_action(r) for r in due[:4]) if a]
        return "These follow-ups are due now:\n" + "\n".join(lines), actions
    if name == "draft_followup":
        if not job:
            return None
        unpaid = [i for i in job["invoices"] if i["status"] in domain.OPEN_FOR_PAYMENT]
        if not unpaid:
            return f"{job['name']} has no sent, unpaid {pay_app} to follow up on.", [_job_btn(job)]
        p = job["payer"]
        # What is still owed on every unpaid pay app, listed as the job page's follow-up lists them
        # (domain.record_follow_up), so a part payment isn't billed again (Round 2 review, B17).
        owed = [(i, domain._owed_on(job, i)) for i in unpaid]
        # The letter layout of 198422a: greeting, the pay app, sign-off (Round 2 review, third pass).
        if len(owed) == 1:
            i, c = owed[0]
            still = " still owed" if c != int(i.get("amount_cents") or 0) else ""
            body = f"Checking on {i['number']} for {job['name']} ({domain.money(c)}{still}), sent {(i.get('sent_at') or '')[:10]}."
        else:
            listing = "; ".join(f"{i['number']} {domain.money(c)} sent {(i.get('sent_at') or '')[:10]}" for i, c in owed)
            body = f"Checking on {job['name']}: {listing}; total {domain.money(sum(c for _, c in owed))}."
        note = (f"Hi {p.get('approver') or p['name']},\n\n{body} Is anything missing on your side?"
                f"\n\nThanks,\n{company}")
        # Sending it is the job page's Send follow-up: the helper only opens the job (decision 12).
        actions = [{"type": "copy", "text": note, "label": "Copy note"}]
        return f"Here is a follow-up for {job['name']}:\n\n{note}", actions + [_job_btn(job)]
    if name == "blockers":
        if not job:
            # A client named: their jobs only (Round 2 review, B7).
            payer = helper_intents.client_named(snap, question) if question else None
            blocked = [j for j in jobs if j["blocked"] and (not payer or j["payer_id"] == payer["id"])]
            if not blocked:
                return (f"No open job for {payer['name']} is blocked.", [helper_intents.client_action(payer)]) if payer else ("No open job is blocked.", [])
            return "Blocked jobs:\n" + "\n".join(f"- {j['name']}: {j['blockers'][0]}" for j in blocked[:8]), [_job_btn(j) for j in blocked[:4]]
        if not job["blockers"]:
            return f"{job['name']} has nothing blocking it. Status: {job['status_label']}.", [_job_btn(job)]
        if not job["blocked"]:
            # Missing paperwork before there is a draft is what the next pay app will need, not a block.
            return (f"{job['name']} isn't blocked yet. The next {pay_app} will need:\n" + "\n".join(f"- {b}" for b in job["blockers"]),
                    [_job_btn(job), {"type": "packet", "job_id": job["id"], "inv": "", "label": "Supporting documents"}])
        return f"{job['name']} is blocked by:\n" + "\n".join(f"- {b}" for b in job["blockers"]), [_job_btn(job), {"type": "packet", "job_id": job["id"], "inv": "", "label": "Supporting documents"}]
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
            return f"All finished work is on a {pay_app}.", []
        return "Finished work not billed yet:\n" + "\n".join(f"- {j['name']}: {domain.money(j['money']['unbilled_completed_cents'])}" for j in rows), \
            [_job_btn(j) for j in rows[:4]]
    if name == "retainage_list":
        rows = [j for j in jobs if j["money"]["retainage_open_cents"] > 0]
        if not rows:
            return "No retainage is being held on open jobs.", [{"type": "nav", "view": "retainage", "label": "Open Closeout"}]
        lines = []
        for j in rows:
            missing = j.get("closeout_missing") or []
            state_txt = "job still in progress" if j.get("work_status") != "complete" else (f"waiting on {', '.join(missing)}" if missing else "ready to ask for release")
            last = j.get("last_retainage_chase_at")
            if state_txt == "ready to ask for release" and last:
                # The wait domain.chase_retainage enforces: the customer's days between contacts.
                due = domain._aware(last) + timedelta(days=domain._int_or((j.get("payer") or {}).get("follow_up_days"), 7))
                if datetime.now(timezone.utc) < due:
                    state_txt = f"asked {last[:10]}, ask again from {due.date().isoformat()}"
            lines.append(f"- {j['name']}: {domain.money(j['money']['retainage_open_cents'])}, {state_txt}")
        total = sum(j["money"]["retainage_open_cents"] for j in rows)
        return f"Retainage held: {domain.money(total)}\n" + "\n".join(lines), [{"type": "nav", "view": "retainage", "label": "Open Closeout"}]
    if name == "waivers_due":
        rows = [j for j in jobs if j.get("waiver_due") and not j.get("waiver_sent")]
        if not rows:
            # Part payments bring a waiver due too, unless the client has them off (decision 5).
            return f"No waivers are due. A waiver comes due when a {pay_app} is paid, in full or, for clients with part-payment waivers on, in part.", []
        return "Waivers due:\n" + "\n".join(f"- {j['name']}" for j in rows), [_job_btn(j) for j in rows[:4]]
    if name == "inbox":
        new = [i for i in snap.get("inbox", []) if i["status"] == "new"]
        if not new:
            return "The Inbox is empty. Drop files on any page, paste an email or notes on Import, or forward email and texts once set up.", [{"type": "nav", "view": "inbox", "label": "Open Inbox"}]
        suggested = len([i for i in new if i.get("suggested_job_id")])
        return f"{len(new)} item(s) are waiting; {suggested} already have a suggested job.", [{"type": "nav", "view": "inbox", "label": "Open Inbox"}]
    if name == "pending_cos":
        rows = [(j, co) for j in jobs for co in j["change_orders"] if co["status"] == "pending"]
        # A job or client named: theirs only (Round 2 review, B7).
        scope = _named_scope(snap, question)
        if scope:
            rows = [(j, co) for j, co in rows if j["id"] in scope[1]]
            if not rows:
                return f"No change order for {scope[0]} is waiting on a signature.", []
        if not rows:
            return "No change orders are waiting on a signature.", []
        # Amounts only for someone who may see the company's money (decision 12).
        price = lambda co: f" ({'no price yet' if co.get('unpriced') else domain.money(co['amount_cents'])})" if money_ok else ""
        return "Change orders waiting on a signature:\n" + "\n".join(f"- {j['name']}: CO {co['number']} {co['title']}{price(co)}" for j, co in rows), \
            [{"type": "nav", "view": "cos", "label": "Open Change orders"}]
    if name == "closeable":
        if how_to and job:
            # After the how-to, on a job's page or with a job named: that job, not every job (B26).
            if job.get("closed"):
                return f"{job['name']} is finished.", [_job_btn(job)]
            if not job.get("close_blockers"):
                return f"{job['name']} is ready to finish.", [_job_btn(job)]
            return f"Before {job['name']} can be finished:\n" + "\n".join(f"- {b}" for b in job["close_blockers"]), [_job_btn(job)]
        rows = [j for j in jobs if not j.get("close_blockers")]
        # The gate is the permission, not the owner role (domain/profiles.py close_jobs; B29). After
        # the how-to, which says the rule and the permission, only what is live (B26).
        who = "" if "close_jobs" in perms or how_to else " Finishing a job takes the Finish and reopen jobs permission."
        if not rows and how_to:
            return "No job is ready to finish yet.", []
        if not rows:
            return ("No job is ready to finish yet. A job can be finished once the work is marked done, every change order is signed or withdrawn, "
                    "and everything is billed, paid, released and waived. Open a job to see what is left. Finishing a job takes the Finish and reopen jobs permission."), []
        return "Ready to finish:\n" + "\n".join(f"- {j['name']}" for j in rows) + (f"\n{who.strip()}" if who else ""), [_job_btn(j) for j in rows[:4]]
    return None


# ---------- answering ----------

def _log(entry: dict[str, Any]) -> None:
    with LOCK:
        data = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else []
        data.append(entry)
        storage.write_json(LOG_PATH, data[-LOG_LIMIT:])


def answer(question: str, state: dict[str, Any], company_id: str, user_id: str, view: str = "", context_job_id: str = "",
           user: dict[str, Any] | None = None) -> dict[str, Any]:
    """The helper's reply to one question, from this company's state only. user: who asks (the
    server always passes it), for their permissions; None means every permission."""
    question = (question or "").strip()[:500]
    if not question:
        raise domain.GateError("Type a question.")
    perms = set(profiles.perms_of(user, state)) if user is not None else set(profiles.PERM_KEYS)
    snap = domain.snapshot(state)
    # A client named more fully than any job ("What does Ponce Property owe") is what the question
    # is about, not the job sharing a word with it (Ponce bath), nor the open page's job (B7).
    client = helper_intents.client_named(snap, question)
    job = None if client else find_job(snap, question)
    if not job and context_job_id and not client:
        job = next((j for j in snap["jobs"] if j["id"] == context_job_id), None)
    # A job's or client's name says which, not what is asked ("how much does Turner office owe?",
    # "how much does Ponce Property Co owe?"): ranked without it, so a word of it that some answer
    # uses ("office", "Co" for a change order) doesn't lead the question astray (B8).
    asked = question
    named = [job] if job and find_job(snap, question) else []
    named += [p for p in [client or helper_intents.find_client(snap, question)[0]] if p]
    if named:
        asked = _drop_names(question, named) or question
    ranked = rank(asked)
    top_score, top = ranked[0] if ranked else (0.0, None)
    if top and top.get("handler") in ("unpaid", "who_owes") and _FOLLOWUP_LATE_RE.search(question):
        # A follow-up, or sending, being late ("is a follow up overdue", "is my pay app late to
        # send?") is the follow-ups answer, not who owes or the overdue list (Round 2 review, B18).
        top = next((e for _, e in ranked if e.get("handler") == "followups"), top)
        top_score = max(top_score, ANSWER_AT)
    reply: dict[str, Any] = {"actions": [], "suggestions": []}
    matched = None
    command = helper_intents.intent(question, snap, perms, context_job_id)
    if command:
        # A plain command (Round 2, decision 12): open a page or a form, and nothing else.
        kind, done = command
        matched = {"id": kind}
        reply.update(text=done["text"], actions=done["actions"])
        if "go" in done:
            reply["go"] = done["go"]
    elif top and top_score >= ANSWER_AT:
        matched = top
        text = top.get("answer", "")
        actions = list(top.get("actions", []))
        if top.get("handler"):
            tied: list[dict[str, Any]] = []
            none_text = ""
            if top.get("needs_job") and not job and client:
                # A client named: one of their jobs, never the job sharing a word with them (B7). For
                # a follow-up, those with a sent, unpaid pay app; the open page's job when it is one.
                tied = [j for j in snap["jobs"] if not j.get("closed") and j["payer_id"] == client["id"]]
                if top["handler"] == "draft_followup":
                    tied = [j for j in tied if any(i["status"] in domain.OPEN_FOR_PAYMENT for i in j["invoices"])]
                    none_text = f"{client['name']} has no sent, unpaid {domain.term_text('pay app', domain.billing_style(snap))} to follow up on."
                else:
                    none_text = f"{client['name']} has no open jobs."
                job = next((j for j in tied if j["id"] == context_job_id), None) or (tied[0] if len(tied) == 1 else None)
            elif top.get("needs_job") and not job:
                found, tied = helper_intents.find_job(snap, question)   # a name typed with a typo
                job = found
            choices = tied or ([] if client else [j for j in snap["jobs"] if not j.get("closed")])
            if top.get("needs_job") and not job and not choices and client:
                text, actions = none_text, [helper_intents.client_action(client)] + actions
            elif top.get("needs_job") and not job and not choices:
                # Nothing to choose from: say so, with somewhere to go (B31).
                text = "There are no open jobs."
                actions = [helper_intents.form_action("newjob", None, domain.billing_style(snap))] + (
                    [helper_intents.nav_action("history")] if snap["jobs"] else []) + actions
            elif top.get("needs_job") and not job:
                text = "Which job do you mean?"
                # Each choice asks again with that job's id, so a name that doesn't pick out one
                # job (two alike, or common words only) doesn't ask "Which job?" forever.
                actions = [{"type": "ask", "q": question, "job_id": j["id"], "label": j["name"]} for j in choices][:6]
            else:
                how_to = bool(top.get("how_to")) and bool(text.strip())
                live = handle(top["handler"], snap, job, state, perms, question, how_to=how_to)
                if live:
                    # A prepared how-to ("how_to" in answers.json) keeps its text, first, and the live
                    # lines follow it; a live answer's text is only a fallback for no live lines (B26).
                    live_text, extra = live
                    text = _join(text, live_text, live_first=not _HOW_RE.match(question)) if how_to else live_text
                    actions = extra + actions
        form = helper_intents.answer_form(top["id"], snap, job, perms)
        if form:
            actions = [form] + actions
        reply.update(text=text, actions=actions[:7], suggestions=top.get("follow_ups", [])[:3])
    else:
        close = [e for s, e in ranked[:3] if s >= SUGGEST_AT]
        if close:
            reply["text"] = "I'm not sure I understood. Did you mean one of these?"
            reply["suggestions"] = [e["questions"][0] for e in close]
            reply["actions"] = helper_intents.closest(question, snap, perms)
        else:
            # The closest pages, jobs and clients as buttons, the closest answers as questions.
            near = helper_intents.closest(question, snap, perms)
            guesses = [e["questions"][0] for s, e in ranked[:3] if s > 0]
            reply["text"] = ("I don't know that yet. I've saved the question so the Duracash team can add an answer. "
                             + ("These are the closest I have." if near or guesses else "Try asking what to do today, who owes you, or how to import an email."))
            reply["actions"] = near
            reply["suggestions"] = guesses or ["What should I do today?", "Who owes me money?", "How do I import emails and texts?"]
    # The rail itself: whatever an answer (shipped or added in the app) carries, only buttons that
    # open something go out (decision 12).
    # A page button only for someone who may open it ("Open Financials" without the permission
    # would only lead to a refusal).
    reply["actions"] = [a for a in reply["actions"] if isinstance(a, dict) and a.get("type") in helper_intents.SAFE_ACTIONS
                        and helper_intents.may_open(a, perms)]
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
                     "retainage_list", "waivers_due", "inbox", "pending_cos", "closeable", "unpaid", "backlog", "due_week"],
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
    # The words a shipped answer needs in a question stay with it when its text is edited here.
    shipped = next((e for e in _read(SHIPPED_KB) if e.get("id") == str(answer_id)[:60]), {})
    needs = shipped.get("needs")
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
        **({"needs": needs} if needs else {}),
        # A shipped how-to stays one (its text first, then the live lines) when edited here.
        **({"how_to": True} if shipped.get("how_to") else {}),
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
