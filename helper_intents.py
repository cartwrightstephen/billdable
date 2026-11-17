"""What the helper does besides its prepared answers: open a page and start a task (Stephen, 10 Nov,
decision 12: "basic and easy functionality ... a loosely guard railed area for experimentation").

The rails:
  - Nothing here changes data. Every button it hands back only opens a page or a form
    (SAFE_ACTIONS); the person fills the form in and saves it themselves, through the same
    endpoints and gates as anywhere else in the app.
  - It reads only the signed-in company's own jobs and clients (the state the server loaded).
  - A form someone's permissions don't cover is not offered.
  - Deterministic: word rules and difflib for typos; no model, no outside call.

intent() runs before the prepared answers. It takes only a plain command ("take me to Unpaid",
"open the Oak Street job", "new change order for Oak Street"); a question ("how do I record a
payment?") goes on to the prepared answers, which add the form's button (FORM_FOR_ANSWER).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

import domain

# Every action type a helper reply may carry: each opens something; none posts (decision 12's rails).
SAFE_ACTIONS = {"nav", "job", "client", "form", "ask", "copy", "packet", "path"}

# The pages, as the menu names them (static/app.js SECTIONS), and what people call them. An alias
# is the whole of what follows "open" / "take me to": "show me the money" and "show my receivables"
# are prepared answers, so "money" and "receivables" are no page's alias.
PAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "dashboard": ("Dashboard", ("dashboard", "overview", "home", "to do", "todo", "to do list", "today")),
    "calendar": ("Calendar", ("calendar",)),
    "jobs": ("Open jobs", ("jobs", "open jobs", "job list", "all jobs", "my jobs")),
    "daily": ("New job record", ("job records", "new job record", "daily record", "daily records", "diary")),
    "history": ("Job history", ("job history", "finished jobs", "closed jobs", "old jobs")),
    "playbook": ("Client playbook", ("clients", "client playbook", "playbook", "client list", "customers", "customer list")),
    "inbox": ("Inbox", ("inbox", "communications", "messages")),
    "outbox": ("Outbox", ("outbox", "sent emails", "sent email")),
    "import": ("Import", ("import", "imports")),
    "export": ("Export", ("export", "exports")),
    "payapps": ("Pay apps", ("pay apps", "pay app list", "pay applications", "payapps", "invoices", "draws")),
    "cos": ("Change orders", ("change orders", "cos")),
    "collections": ("Unpaid", ("unpaid", "collections", "unpaid page")),
    "waivers": ("Closeout", ("closeout", "close out", "waivers", "lien waivers")),
    "retainage": ("Retainage", ("retainage",)),
    "financials": ("Financials", ("financials", "financial", "finances", "financial dashboard")),
    "company": ("Company settings", ("settings", "company settings", "company")),
    "team": ("Team", ("team", "users", "permissions", "team settings")),
    "account": ("My account", ("my account", "account", "notifications", "profile")),
    "billing": ("Billing", ("billing", "subscription")),
    "audit": ("Activity log", ("activity log", "audit", "activity", "audit log")),
    "packets": ("Supporting documents", ("supporting documents", "paperwork", "packets")),
}
# A page only some people may open: the helper says so instead of sending them to a refusal.
PAGE_PERM = {"financials": "financials", "team": "team", "billing": "billing"}

# The forms it opens, with the permission each save needs (domain/profiles.py JOB_ACTION_PERMS:
# New job and a job record are anyone's; a change order and a progress pay app too).
FORMS = {
    "newjob": ("New job", None),
    "newco": ("New Change Order", None),
    "match": ("Record a Payment", "record_payments"),
    "daily": ("New job record", None),
    "progress": ("Progress Pay App", None),
}
# A prepared answer about doing one of these also offers its form.
FORM_FOR_ANSWER = {"create-job": "newjob", "change-orders": "newco", "match-payment": "match", "daily-record": "daily", "progress": "progress"}

# Case-blind: the command keeps its capitals, so a job code typed in capitals (TRN) still reads as one.
_QUESTION = re.compile(r"^(?:how|what|whats|what's|why|when|where|which|who|whose|can|could|do|does|did|is|are|was|should|would|will|am)\b", re.I)
_NAV = re.compile(r"^(?:please\s+|can you\s+|could you\s+)?(?:take me to|bring me to|bring up|go to|goto|open up|open|show me|show|pull up|jump to|navigate to)\s+(.+)$", re.I)
# Words around a page's name that say nothing about which page; around a job's or client's, more.
_SOFT = {"the", "my", "our", "a", "an", "page", "tab", "screen", "please", "me", "up", "to", "now", "thanks"}
_FILLER = _SOFT | {"list", "for", "of", "job", "jobs", "project", "client", "clients", "customer", "s", "on", "in", "card"}
# Too common in job and client names to pick one out alone (helper.find_job keeps its own list).
_GENERIC = {"street", "st", "road", "avenue", "ave", "kitchen", "bath", "house", "job", "remodel", "addition", "renovation", "office",
            "the", "and", "residence", "residential", "property", "properties", "construction", "builders", "building", "group", "company",
            "inc", "llc", "corp", "homes", "home", "services", "contracting", "contractors", "gc", "co", "park", "closed"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("’", "'").replace("'s", ""))


def _close(a: str, b: str) -> bool:
    """Same word, or the same word mistyped: a letter left out or added ("stret", "turnr")."""
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.85


def _name_hits(name: str, code: str, words: list[str], codes: set[str]) -> int:
    """How many of a name's telling words the text has, typos allowed; a job's code typed in
    capitals (OAK, TRN: helper.find_job's rule) counts as all. A name of common words only
    ("Kitchen remodel") counts when the text has the whole of it, so it can be named at all."""
    if code and code in codes:
        return 99
    own = _words(name)
    telling = [w for w in own if len(w) >= 3 and w not in _GENERIC and not w.isdigit()]
    if not telling:
        return 1 if own and f" {' '.join(own)} " in f" {' '.join(words)} " else 0
    return sum(1 for t in telling if any(_close(t, w) for w in words))


def _best(items: list[dict[str, Any]], text: str, code_key: str = "") -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The one item (job or client) the text names, or None and the ones tied for it."""
    words, codes = _words(text), set(re.findall(r"\b[A-Z0-9]{3,8}\b", text or ""))

    def score(item: dict[str, Any]) -> tuple[int, int]:
        # Telling words first; the common ones only break a tie ("Oak Street kitchen" or "Oak Street office").
        telling = _name_hits(item.get("name", ""), str(item.get(code_key) or "") if code_key else "", words, codes)
        return (telling, sum(1 for x in _words(item.get("name", "")) if any(_close(x, w) for w in words))) if telling else (0, 0)

    scored = [(score(i), i) for i in items]
    top = max((s for s, _ in scored), default=(0, 0))
    if not top[0]:
        return None, []
    tied = [i for s, i in scored if s == top]
    return (tied[0], tied) if len(tied) == 1 else (None, tied)


def find_job(snap: dict[str, Any], text: str, open_only: bool = True) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The job a question names, typos allowed; this company's jobs only."""
    jobs = [j for j in snap.get("jobs", []) if not (open_only and j.get("closed"))]
    return _best(jobs, text, "code")


def find_client(snap: dict[str, Any], text: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    return _best(snap.get("payers", []), text)


def _covers(name: str, code: str, rest: list[str]) -> bool:
    """Whether every word left over is part of the name (so "open oak street" opens it, and
    "show oak street status" is a question about it instead)."""
    own = _words(name) + ([code.lower()] if code else [])
    return all(any(_close(w, o) for o in own) for w in rest)


def page_for(text: str) -> str | None:
    """The page a phrase names: an alias as written, or mistyped."""
    phrase = " ".join(w for w in _words(text) if w not in _SOFT) or " ".join(_words(text))
    if not phrase:
        return None
    for view, (_, aliases) in PAGES.items():
        if phrase in aliases:
            return view
    best, best_ratio = None, 0.0
    for view, (label, aliases) in PAGES.items():
        for alias in aliases + (label.lower(),):
            r = SequenceMatcher(None, phrase, alias).ratio()
            if r > best_ratio:
                best, best_ratio = view, r
    return best if best_ratio >= 0.85 else None


def nav_action(view: str) -> dict[str, Any]:
    return {"type": "nav", "view": view, "label": f"Open {PAGES[view][0]}" if view in PAGES else "Open"}


def job_action(job: dict[str, Any]) -> dict[str, Any]:
    return {"type": "job", "job_id": job["id"], "label": f"Open {job['name']}"}


def client_action(payer: dict[str, Any]) -> dict[str, Any]:
    return {"type": "client", "payer_id": payer["id"], "label": f"Open {payer['name']}"}


def form_action(form: str, job: dict[str, Any] | None = None, style: str = "", **prefill: str) -> dict[str, Any]:
    label = domain.term_text(FORMS[form][0], style) if style else FORMS[form][0]
    out = {"type": "form", "form": form, "label": f"{label}: {job['name']}" if job else label}
    if job:
        out["job_id"] = job["id"]
    out.update({k: v for k, v in prefill.items() if v})
    return out


def _may(perms: set[str], form: str) -> bool:
    need = FORMS[form][1]
    return need is None or need in perms


# ---------- the task a command names ----------

# The form a command makes is its verb's object: verb, then a/an/the/new, then the form's words
# ("new change order for Oak", "record a payment on Turner"). A form word further on is something
# else the sentence is about ("draft a follow-up for the Turner pay app").
_OBJECTS = (
    # Job record first: "new job record" is not New job.
    ("daily", r"(?:job|daily|site)\s+records?|diary|daily\s+log"),
    ("newco", r"change\s+orders?|co"),
    ("match", r"payments?"),
    ("progress", r"(?:progress\s+)?(?:pay\s*apps?|pay\s+applications?|draws?|invoices?|bill)|progress\s+billing"),
    ("newjob", r"job|project"),
)
_TASK = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|let me\s+|i want to\s+|i need to\s+|help me\s+)?"
    r"(?P<verb>(?:new|add|create|start|make|enter|log|record|write|draft|begin)\s+|open\s+(?=(?:a|an|new|the\s+new)\b))"
    r"(?:(?:a|an|the|new)\s+)*"
    r"(?:" + "|".join(f"(?P<{form}>{words})" for form, words in _OBJECTS) + r")\b", re.I)
# New job only as the thing made ("new job", "add a job"), not "log a job".
_MAKES = {"new", "add", "create", "start", "make", "begin", "open"}
# Words that say the command is about something no form here makes: a follow-up, a reminder, an
# email, a note, a photo; and the final pay app, which is a prepared answer (its own step).
_NOT_A_FORM = re.compile(r"\b(?:follow[\s-]*ups?|followups?|reminders?|e-?mails?|notes?|photos?|final)\b", re.I)


def _task_match(q: str) -> re.Match[str] | None:
    if _NOT_A_FORM.search(q):
        return None
    m = _TASK.search(q)
    if not m:
        return None
    form = next(f for f, _ in _OBJECTS if m.group(f))
    if form == "newjob" and m.group("verb").split()[0].lower() not in _MAKES:
        return None
    return m


def _task(q: str) -> str | None:
    m = _task_match(q)
    return next(f for f, _ in _OBJECTS if m.group(f)) if m else None


def _strip_task_words(q: str) -> str:
    """What follows the form's words: where the job (or client) is named."""
    m = _task_match(q)
    return q[m.end():] if m else q


def _which_job(form: str, jobs: list[dict[str, Any]], why: str, style: str) -> tuple[str, list[dict[str, Any]]]:
    """Each choice opens the form on that job, by its id: asking again by name went round in a
    circle for a name that doesn't pick out one job ("Kitchen remodel", or two jobs named alike)."""
    if not jobs:
        return why, []
    return "Which job?", [{**form_action(form, j, style), "label": j["name"]} for j in jobs[:6]]


def _task_reply(form: str, question: str, q: str, snap: dict[str, Any], perms: set[str], style: str,
                context_job_id: str = "") -> dict[str, Any] | None:
    open_jobs = [j for j in snap.get("jobs", []) if not j.get("closed")]
    pay_app = domain.term_text("pay app", style)
    rest = _strip_task_words(q)
    if form == "newjob":
        # The new job's own name isn't a client's ("new job called Granite Tower" is not Granite Row's).
        payer, _ = find_client(snap, re.sub(r"\b(?:called|named)\s+.+?(?=\s+for\s+|$)", " ", rest, flags=re.I))
        named = re.search(r"\b(?:called|named)\s+[\"“']?(.+?)[\"”']?(?:\s+for\s+.*)?\s*$", question, re.I)
        action = form_action("newjob", None, style, client=payer["name"] if payer else "", name=named.group(1).strip() if named else "")
        return {"text": "Here is New job" + (f" for {payer['name']}" if payer else "") + ". Nothing is saved until you save it.", "actions": [action], "go": 0}
    if not _may(perms, form):
        return {"text": f"Your permissions don't cover {FORMS[form][0]}. Someone who manages the team can change them.", "actions": []}
    job, tied = find_job(snap, rest, open_only=False)
    tied = [j for j in tied if not j.get("closed")]
    # No job named: the job whose page is open is the one meant.
    here = next((j for j in snap.get("jobs", []) if context_job_id and j["id"] == context_job_id), None)
    if not job and here and (not tied or here in tied):
        job, tied = here, []
    if form == "progress" and not job and not tied and not re.search(r"\bprogress\b", q, re.I):
        return None   # "new pay app" with no job named: the prepared answer about the Pay apps list
    eligible = {
        "match": lambda j: any(i["status"] in domain.OPEN_FOR_PAYMENT for i in j["invoices"]),
        "progress": lambda j: j.get("work_status") != "complete",
    }.get(form, lambda j: True)
    if not job:
        pool = [j for j in (tied or open_jobs) if eligible(j)]
        why = {"match": f"No {pay_app} is waiting for payment.", "progress": "Every open job's work is marked done."}.get(form, "There are no open jobs.")
        text, actions = _which_job(form, pool, why, style)
        return {"text": text, "actions": actions}
    if job.get("closed"):
        return {"text": f"{job['name']} is finished.", "actions": [job_action(job)]}
    if not eligible(job):
        if form == "match":
            return {"text": f"{job['name']} has no {pay_app} waiting for payment.", "actions": [job_action(job)]}
        return {"text": f"The work on {job['name']} is marked done, so its next {pay_app} is drafted from its page.", "actions": [job_action(job)]}
    return {"text": f"Here is {domain.term_text(FORMS[form][0], style)} for {job['name']}. Nothing is saved until you save it.",
            "actions": [form_action(form, job, style), job_action(job)], "go": 0}


def _page_reply(view: str, perms: set[str]) -> dict[str, Any]:
    need = PAGE_PERM.get(view)
    if need and need not in perms:
        return {"text": f"{PAGES[view][0]} needs a permission you don't have. Ask an owner.", "actions": []}
    return {"text": f"Opening {PAGES[view][0]}.", "actions": [nav_action(view)], "go": 0}


def _nav_reply(target: str, snap: dict[str, Any], perms: set[str]) -> dict[str, Any] | None:
    """What "open X" / "take me to X" opens: a page, a job or a client, when X is all of its name."""
    words = [w for w in _words(target) if w not in _FILLER]
    view = page_for(target)
    names = [x for i in snap.get("jobs", []) + snap.get("payers", []) for x in _words(i.get("name", "")) if x not in _GENERIC and len(x) >= 3]
    if view and not any(_close(w, x) for w in words for x in names):
        return _page_reply(view, perms)
    if words:
        job, tied = find_job(snap, target, open_only=False)
        if job and _covers(job["name"], str(job.get("code") or ""), words):
            return {"text": f"Opening {job['name']}.", "actions": [job_action(job)], "go": 0}
        if tied and all(_covers(j["name"], str(j.get("code") or ""), words) for j in tied):
            return {"text": "Which one?", "actions": [job_action(j) for j in tied[:6]]}
        payer, ptied = find_client(snap, target)
        if payer and _covers(payer["name"], "", words):
            return {"text": f"Opening {payer['name']} in the Client playbook.", "actions": [client_action(payer)], "go": 0}
        if ptied and all(_covers(p["name"], "", words) for p in ptied):
            return {"text": "Which one?", "actions": [client_action(p) for p in ptied[:6]]}
    return _page_reply(view, perms) if view else None


def intent(question: str, snap: dict[str, Any], perms: set[str], context_job_id: str = "") -> tuple[str, dict[str, Any]] | None:
    """A plain command the helper can carry out by opening something: (kind, reply) or None.
    kind is "open-page" or "start-task", logged as what the question matched. context_job_id: the
    job whose page is open, meant when the command names none."""
    q = " ".join((question or "").replace("’", "'").split()).strip(" ?.!")
    q = re.sub(r"^(?:please|can you|could you|would you)\s+", "", q, flags=re.I)
    if not q or _QUESTION.match(q):
        return None
    style = domain.billing_style(snap)
    form = _task(q)
    if form:
        reply = _task_reply(form, question, q, snap, perms, style, context_job_id)
        if reply:
            return "start-task", reply
    m = _NAV.match(q)
    if m:
        reply = _nav_reply(m.group(1), snap, perms)
        if reply:
            return "open-page", reply
    return None


def answer_form(entry_id: str, snap: dict[str, Any], job: dict[str, Any] | None, perms: set[str]) -> dict[str, Any] | None:
    """The form a prepared how-to answer offers: New job always, a job's form on a job named or open."""
    form = FORM_FOR_ANSWER.get(entry_id)
    if not form or not _may(perms, form):
        return None
    style = domain.billing_style(snap)
    if form == "newjob":
        return form_action(form, None, style)
    if not job or job.get("closed"):
        return None
    if form == "match" and not any(i["status"] in domain.OPEN_FOR_PAYMENT for i in job["invoices"]):
        return None
    if form == "progress" and job.get("work_status") == "complete":
        return None
    return form_action(form, job, style)


def may_open(action: dict[str, Any], perms: set[str] | None) -> bool:
    """A page button only for someone who may open the page (PAGE_PERM); None: every permission."""
    need = PAGE_PERM.get(action.get("view", "")) if action.get("type") == "nav" else None
    return not need or perms is None or need in perms


def closest(question: str, snap: dict[str, Any], perms: set[str] | None = None) -> list[dict[str, Any]]:
    """Pages, jobs and clients close to what was asked, for "I don't know that yet"."""
    words = [w for w in _words(question) if w not in _FILLER and len(w) >= 3]
    out: list[dict[str, Any]] = []
    for w in words:
        view = page_for(w)
        if view and nav_action(view) not in out and may_open(nav_action(view), perms):
            out.append(nav_action(view))
    job, tied = find_job(snap, question)
    for j in ([job] if job else tied)[:2]:
        out.append(job_action(j))
    payer, _ = find_client(snap, question)
    if payer:
        out.append(client_action(payer))
    return out[:4]
