"""Reading a document attached to a pay app's supporting documents (Stephen's Nov 1 notes, Decision
6): is it about this job, and for a lien waiver, this amount and the conditional wording? What
doesn't match is a warning naming it, for a person to look at; it never blocks on its own. Text
comes from contract_import's readers, which stay small whatever the file inflates to. A scan or a
photo has none, so it says it couldn't be checked; there is no OCR here.

prepare() does the reading, so a caller can do it before taking a company's lock and hand what it
got to check() in place of the file's bytes (server._read_ahead). Whatever goes wrong in here is a
warning a person can clear, never a refusal."""

from __future__ import annotations

import re
import sys
import traceback
from dataclasses import dataclass
from itertools import islice
from typing import Any

import contract_import

PICTURES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
BLANK_LETTERS = 8   # fewer letters than this is a blank page's stray marks


@dataclass
class ReadFile:
    """What prepare() got from a file: its text (None for a type that isn't read), whether it
    couldn't be opened, and whether a PDF has pictures in it."""
    text: str | None = None
    failed: bool = False
    has_image: bool = False


def _text(ext: str, data: bytes) -> str | None:
    """The file's text, or None for a type that isn't read."""
    if ext == ".pdf":
        return contract_import.pdf_text(data)[:contract_import.MAX_TEXT]
    if ext == ".docx":
        return contract_import.docx_text(data)
    if ext == ".txt":
        return contract_import.plain_text(data)
    return None


def prepare(ext: str, data: bytes) -> ReadFile:
    """Read one file. Never raises: a damaged Word file or PDF is for the person to look at."""
    if ext in PICTURES:
        return ReadFile()
    try:
        return ReadFile(text=_text(ext, data), has_image=ext == ".pdf" and b"/Image" in data)
    except Exception:
        return ReadFile(failed=True)


def _letters(text: str) -> int:
    return sum(c.isalpha() for c in text)


def _has_job_name(text: str, job_name: str) -> bool:
    """Every word of the job's name is in the text, whatever the order or capitals."""
    have = set(re.findall(r"[a-z0-9]+", text.lower()))
    return all(w in have for w in re.findall(r"[a-z0-9]+", job_name.lower()))


def _has_amount(text: str, cents: int) -> bool:
    for m in re.finditer(r"\d[\d,]*(?:\.\d{1,2})?", text):
        try:
            if round(float(m.group(0).replace(",", "")) * 100) == cents:
                return True
        except (ValueError, OverflowError):  # hundreds of digits are no amount
            continue
    return False


# What a waiver's type looks like in the common forms and the state ones: Georgia's statutory forms
# may say "Interim" and "Upon Final Payment" rather than conditional or unconditional.
_CONDITIONAL = re.compile(r"\bconditional\b|\bconditioned\s+(?:up)?on\b|\bupon\s+(?:final\s+)?(?:payment|receipt)\b|\bsubject\s+to\s+(?:receipt|payment)\b|\binterim\b", re.I)
_FINAL = re.compile(r"\bfinal\b", re.I)
_PROGRESS = re.compile(r"\binterim\b|\bprogress\b|\bpartial\b", re.I)
# The word however it was typed or broke across lines: UN-CONDITIONAL, un conditional, U N C O N...
_UNCONDITIONAL = re.compile(r"(?<![a-z])" + r"[\s\-\u00ad\u2010-\u2015]*".join("unconditional") + r"(?![a-z])", re.I)
_UNCONDITIONAL_OR_LY = re.compile(_UNCONDITIONAL.pattern[:-len(r"(?![a-z])")] + r"(?:ly)?(?![a-z])", re.I)
# The plain word, the same way, with any unconditional taken out first.
_PLAIN_CONDITIONAL = re.compile(r"(?<![a-z])" + r"[\s\-\u00ad\u2010-\u2015]*".join("conditional") + r"(?![a-z])", re.I)


# "not an unconditional release", "does not constitute an unconditional waiver": the word taken back
# at once. Only words that can sit between the two: "not conditional, and is an unconditional
# waiver" says it is one, so it stays.
_NEGATED_UNCONDITIONAL = re.compile(r"(?<![a-z])(?:not|isn['\u2019]?t|never|no)(?:\s+(?:to|be|an?|any|as|is|this|constitutes?|deemed|considered|construed|intended|meant|represents?|serves?))*\s+"
                                    + _UNCONDITIONAL.pattern, re.I)
_WAIVER_WORDS = re.compile(r"waiver|release|lien", re.I)
_NEAR_WORDS = 4
_NEAR_CHARS = 200     # as far as a few words reach: only this much is split, however long the file
MAX_MATCHES = 200     # a file that says it more often than this has said what it has to say


def _near_a_waiver_word(text: str, found: "re.Match[str]") -> bool:
    around = text[max(0, found.start() - _NEAR_CHARS):found.start()].split()[-_NEAR_WORDS:] + text[found.end():found.end() + _NEAR_CHARS].split()[:_NEAR_WORDS]
    return any(_WAIVER_WORDS.search(word) for word in around)


_WINDOW_CHARS = 400   # how much of a line or sentence is looked at around the word, however long the file
_FOUND_CHARS = 60
_SPACED_LETTERS = re.compile(r"(?<![A-Za-z])(?:[A-Za-z] ){3,}[A-Za-z](?![A-Za-z])")   # U N C O N D I T I O N A L
_SHORT_LINE_WORDS = 6
_TITLE_WORDS = 12
_WAIVER_WORD = {"waiver", "waivers", "release", "releases", "lien", "liens"}
_WORD_EDGE = ".,;:()[]-\u2013\u2014\"'\u2019"
# Words before the type that make it a sentence about someone's waiver: "Sub's unconditional waiver",
# "an unconditional waiver". A state or a form's name in front is still a heading.
_NOT_A_HEADING = {"a", "an", "the", "this", "that", "these", "those", "our", "their", "his", "her", "its", "my", "your", "sub", "subs", "subcontractor", "supplier", "vendor", "copy",
                  "received", "got", "get", "need", "needs", "needed", "send", "sent", "provide", "deliver", "attach", "attached", "with", "for", "from", "and", "or", "no", "any", "will",
                  "must", "shall", "see", "per", "by", "if", "upon", "when", "after", "before", "on", "in", "to", "owe", "owed"}
# What can sit between the word and waiver or release in a phrase.
_BETWEEN = {"lien", "final", "progress", "partial", "interim", "statutory", "and", "of", "on", "payment"}
# A word right after "unconditional waiver" that makes it a sentence about one.
_ABOUT_ONE = {"received", "will", "was", "were", "is", "are", "attached", "to", "from", "due", "needed", "required", "pending", "sent", "follows", "shall", "must", "becomes", "may", "can",
              "has", "have", "when", "once", "after", "owed", "for"}
_DECLARED = re.compile(r"\b(?:this|the)\b[^.;\n]{0,60}?\b(?:is|constitutes)\s+(?:an?\s+)?$", re.I)
_NOT_CONDITIONAL = re.compile(r"\bnot\s+" + _PLAIN_CONDITIONAL.pattern, re.I)
# The only sentences that talk about an unconditional waiver without being one: it comes later.
_WILL_DELIVER = re.compile(r"\b(?:will|shall|agrees?\s+to|promises?\s+to)\s+(?:\w+\s+){0,2}?(?:deliver|provide|furnish|issue|exchange|give|send|sign|execute|submit)\s+(?:[\w'\u2019]+\s+){0,3}?$", re.I)
_WILL_BE_DELIVERED = re.compile(r"^\s+(?:lien\s+)?(?:waiver|release)(?:\s+and\s+release)?\s+(?:will|shall)\s+(?:be\s+)?(?:delivered|provided|furnished|issued|exchanged|given|sent|signed|executed|submitted)\b", re.I)
_BECOMES = re.compile(r"\b(?:becomes?|(?:will|shall)\s+become)\s+$", re.I)
_ON_PAYMENT = re.compile(r"^\s+(?:on|upon|once|when|after)\b", re.I)
_USE_CONDITIONAL = re.compile(r"\bif\s+you\s+have\s+not\s+been\s+paid,?\s+use\s+a\s+conditional\b", re.I)


def _around(text: str, found: "re.Match[str]") -> tuple[str, str]:
    """(before, after) the word on its own line, as far as the window goes."""
    start, end = found.start(), found.end()
    low, high = max(0, start - _WINDOW_CHARS), min(len(text), end + _WINDOW_CHARS)
    line_start = text.rfind("\n", low, start) + 1 or low
    line_end = text.find("\n", end, high)
    return text[line_start:start], text[end:line_end if line_end >= 0 else high]


def _sentence(before: str, after: str) -> tuple[str, str]:
    """The same, as far as the sentence goes."""
    return re.split(r"[.;!?]\s", before)[-1], re.split(r"[.;!?](?:\s|$)", after, maxsplit=1)[0]


def _comes_later(before: str, after: str, word: str) -> bool:
    """One of the sentences that says the unconditional waiver comes later, not that this is it."""
    before, after = _sentence(before, after)
    if word.lower().endswith("ly"):
        return False
    if _WILL_DELIVER.search(before) or _WILL_BE_DELIVERED.search(after) or (_BECOMES.search(before) and _ON_PAYMENT.search(after)):
        return True
    return bool(_USE_CONDITIONAL.search(before) or _USE_CONDITIONAL.search(after))


def _heading_like(before: str, after: str) -> bool:
    """A short line, one that starts or ends with a waiver or release word, or one in title case."""
    raw = [w for w in (w.strip(_WORD_EDGE) for w in (before + " Unconditional " + after).split()) if w]
    words = [w.lower() for w in raw]
    if len(words) <= _SHORT_LINE_WORDS or words[0] in _WAIVER_WORD or words[-1] in _WAIVER_WORD:
        return True
    return len(words) <= _TITLE_WORDS and sum(w[0].isupper() for w in raw) * 4 >= len(raw) * 3


def _titled_a_waiver(before: str, after: str) -> bool:
    """A title with the word further along: "Waiver and release on progress payment - unconditional"."""
    words = [w.strip(_WORD_EDGE).lower() for w in before.split()]
    return bool(words) and words[0] in _WAIVER_WORD and len(words) + len(after.split()) <= _TITLE_WORDS


def _a_waiver_phrase(before: str, after: str) -> bool:
    """"Unconditional [lien] waiver" that isn't a sentence about somebody's waiver."""
    before, _ = _sentence(before, after)
    if _DECLARED.search(before):
        return True
    in_front = before.split()[-1:]
    if in_front and (in_front[0].strip(_WORD_EDGE).lower() in _NOT_A_HEADING or "'" in in_front[0] or "\u2019" in in_front[0]):
        return False
    following = [w.strip(_WORD_EDGE).lower() for w in after.split()]
    for n, word in enumerate(following[:5]):
        if word in ("waiver", "release", "waivers", "releases"):
            return not (n + 1 < len(following) and following[n + 1] in _ABOUT_ONE)
        if word not in _BETWEEN:
            return False
    return False


def _reads_as_unconditional(kept: str) -> bool:
    """Its title doesn't say conditional, and somewhere a heading or a phrase says it is one, not a
    sentence about one. For the warning on an unconditional item; the send's gate is above."""
    title = _NOT_CONDITIONAL.sub(" ", re.split(r"\n|\.\s", kept.strip(), maxsplit=1)[0])
    if _PLAIN_CONDITIONAL.search(_UNCONDITIONAL.sub(" ", title)):
        return False
    for found in islice(_UNCONDITIONAL.finditer(kept), MAX_MATCHES):
        before, after = _around(kept, found)
        if not _comes_later(before, after, found.group(0)) and (_heading_like(before, after) or _a_waiver_phrase(before, after)):
            return True
    return False


# A clause number in front of a heading inside a body ("4. Unconditional Waiver.", "9.5 Unconditional
# Waivers"): a contract's or a GC form's paragraph about one, not the document's title.
_CLAUSE_NUMBER = re.compile(r"^\s*(?:§\s*)?(?:\d+(?:\.\d+)*\.?|[A-Za-z][.)]|\([A-Za-z0-9]+\))\s+")
# The type given as a field ("Type of waiver: Unconditional", "Waiver type: Unconditional") or a box
# ticked beside it ("[X] Unconditional"): the document says what it is. A box left empty says the
# opposite, so its word is taken out first, as a negation is.
_TYPE_FIELD = re.compile(r"\b(?:type\s+of\s+(?:lien\s+)?(?:waiver|release)|(?:lien\s+)?(?:waiver|release)\s+type)\s*:\s*$", re.I)
_TICKED = re.compile(r"(?:\[\s*[xX\u2713\u2714]\s*\]|[\u2611\u2612])\s*$")
_FIELD_ENDS = re.compile(r"^\s*(?:$|[|\t;,.]|\s{2,})")
_UNTICKED = re.compile(r"(?:\[\s?\]|\u2610)\s*" + _UNCONDITIONAL.pattern, re.I)
# Letterhead above a title: a phone, a web or email address, a street address, a city line, a company name.
_LETTERHEAD = re.compile(r"\(?\d{3}\)?[\s.-]*\d{3}-\d{4}|www\.|https?://|@|^\d+\s+\w+.*\b(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|suite|ste)\b"
                         r"|,\s*[A-Z]{2}\s+\d{5}|\b(?:llc|l\.l\.c|inc|corp|co|company|ltd)\.?$", re.I)
TITLE_LINES = 2
# Waiving now, in the present tense: "does hereby unconditionally waive and release", "the undersigned
# unconditionally releases", "this document waives ... rights unconditionally".
_WAIVES_NOW_AFTER = re.compile(r"^\s+(?:waives?|releases?)\b", re.I)
_WAIVES_BEFORE = re.compile(r"\b(?:waives|releases)\b[^.;\n]{0,100}$", re.I)
_MODAL_BEFORE = re.compile(r"\b(?:shall|will|would|may|might|must|should|can|could|to|be|been|being|becomes?|deemed|construed\s+as|considered)\s+(?:\w+\s+){0,2}$", re.I)
_DECLARED_NOW = re.compile(r"\b(?:this|the)\b[^.;\n]{0,60}?\b(?:is|constitutes)\s+(?:hereby\s+)?(?:made\s+)?(?:an?\s+)?$", re.I)
_THIS_ONE = re.compile(r"\bthis\s+$", re.I)
_WAIVER_NEXT = re.compile(r"^\s+(?:(?:lien|final|progress|partial|interim)\s+)*(?:waiver|release)", re.I)


def _title_spans(text: str) -> list[tuple[int, int]]:
    """Where the document's title is: its first lines that aren't letterhead."""
    spans, at = [], 0
    for line in text.split("\n"):
        start, at = at, at + len(line) + 1
        if not line.strip() or (_LETTERHEAD.search(line.strip()) and not _WAIVER_WORDS.search(line)):
            continue
        spans.append((start, at))
        if len(spans) == TITLE_LINES:
            break
    return spans


def _waives_now(before: str, after: str, word: str) -> bool:
    """Operative words that waive now, without a condition: not shall, will, deemed or to become."""
    before, after = _sentence(before, after)
    if word.lower().endswith("ly"):
        if _WAIVES_NOW_AFTER.search(after) and not _MODAL_BEFORE.search(before):
            return True
        return bool(_WAIVES_BEFORE.search(before)) and not _MODAL_BEFORE.search(before[:_WAIVES_BEFORE.search(before).start()])
    if _DECLARED_NOW.search(before):
        return True
    return bool(_THIS_ONE.search(before) and _WAIVER_NEXT.search(after))


_TITLE_WORDS_ONLY = {"unconditional", "waiver", "waivers", "release", "releases", "lien", "liens", "and", "of", "on", "upon", "final",
                     "progress", "partial", "interim", "payment", "payments", "statutory", "affidavit", "contractor's", "subcontractor's",
                     "contractor", "subcontractor", "form", "claim", "claims", "bond", "rights", "&"}


def _only_a_title(before: str, after: str) -> bool:
    """The whole line is a waiver's title and nothing else ("UNCONDITIONAL WAIVER AND RELEASE ON
    FINAL PAYMENT"): a form's own title wherever it starts in the file. A note in brackets after it
    doesn't count against it."""
    line = re.sub(r"\([^()]*\)\s*$", " ", after)
    words = [w.strip(_WORD_EDGE).lower() for w in (before + " unconditional " + line).split()]
    return all(w in _TITLE_WORDS_ONLY for w in words if w)


def _numbered_title(before: str, after: str) -> bool:
    """A numbered or lettered line that is all a waiver form's title, for one waiver ("1. UNCONDITIONAL
    WAIVER AND RELEASE ON PROGRESS PAYMENT", "(1) Unconditional Waiver and Release on Final Payment",
    "§ 2 UNCONDITIONAL WAIVER"): a form numbered in a packet. Plural ("9.5 Unconditional Waivers") or
    with a sentence after it, it is a contract's clause about waivers."""
    number = _CLAUSE_NUMBER.match(before)
    if not number or before[number.end():].strip():
        return False
    words = [w.strip(_WORD_EDGE).lower() for w in after.split()]
    words = [w for w in words if w]
    if any(w in ("waivers", "releases") for w in words) or not any(w in ("waiver", "release", "lien") for w in words):
        return False
    return _only_a_title("", after)


def _type_field(before: str, after: str) -> bool:
    """"Type of waiver: Unconditional" or a ticked box beside the word, with nothing after it but
    the end of the field."""
    return bool((_TYPE_FIELD.search(before) or _TICKED.search(before)) and _FIELD_ENDS.match(after))


def _about_one(before: str, after: str) -> bool:
    """A line that reports on somebody's waiver ("the plumber's signed unconditional lien waiver",
    "Unconditional waiver sent to GC") rather than naming this document."""
    in_front = before.split()[-1:]
    if in_front and (in_front[0].strip(_WORD_EDGE).lower() in _NOT_A_HEADING | {"signed", "received", "sent"} or "'" in in_front[0] or "\u2019" in in_front[0]):
        return True
    following = [w.strip(_WORD_EDGE).lower() for w in after.split()]
    for n, word in enumerate(following[:5]):
        if word in ("waiver", "release", "waivers", "releases"):
            return n + 1 < len(following) and following[n + 1] in _ABOUT_ONE
        if word not in _BETWEEN:
            return False
    return False


def unconditional_reading(files: list[tuple[str, str, Any]], near_only: bool = False) -> dict[str, Any]:
    """What these files say about an unconditional waiver: {waiver: the send's gate (M14), mention:
    they use the words, and found: the words of a look-and-clear, for the person who looks}.
    The gate is for a document that is one (Nov 6 review, A): its title says so (its first lines
    after any letterhead), or it waives now in so many words ("hereby unconditionally waives",
    "this release is unconditional"). Filed as a waiver (near_only False), a line anywhere that
    stands as a heading naming one counts too (a second form after the first); a clause number in
    front makes it a paragraph of a contract or form, not a title, unless the numbered line is all a
    form's title for one waiver (a packet's second form, "1. UNCONDITIONAL WAIVER AND RELEASE"). The
    type given as a field or a ticked box ("Type of waiver: Unconditional") gates too, filed as
    anything. Everything else that uses the
    word (one comes later, is deemed so on payment, is attached as an exhibit, was sent or received)
    is a look: any use when filed as a waiver, a use near waiver, release or lien when filed as
    anything else (an addendum can use the word for something else). The word taken back at once
    doesn't count here; it stays a prompt to look at confirm (_type_problems). Never raises."""
    out = {"waiver": False, "mention": False, "found": ""}
    try:
        for _name, ext, data in files:
            if data is None or ext in PICTURES:
                continue
            read = data if isinstance(data, ReadFile) else prepare(ext, data)
            spaced_out = _SPACED_LETTERS.sub(lambda m: m.group(0).replace(" ", ""), read.text or "")
            kept = _UNTICKED.sub(" ", _NEGATED_UNCONDITIONAL.sub(" ", spaced_out))
            titles = _title_spans(kept)
            seen = 0
            for found in islice(_UNCONDITIONAL_OR_LY.finditer(kept), MAX_MATCHES + 1):
                seen += 1
                if seen > MAX_MATCHES:   # more than is read: it has said what it has to say
                    out["waiver"] = out["waiver"] or not near_only
                    break
                before, after = _around(kept, found)
                word = found.group(0)
                later = _comes_later(before, after, word)
                numbered = bool(_CLAUSE_NUMBER.match(before))
                if numbered and _numbered_title(before, after):
                    # A form's own title with a number in front: read as the title it is.
                    numbered, before = False, before[_CLAUSE_NUMBER.match(before).end():]
                in_title = any(a <= found.start() < b for a, b in titles)
                heading = not later and not numbered and _heading_like(before, after) and len((before + " x " + after).split()) <= _TITLE_WORDS
                near = _near_a_waiver_word(kept, found) or _titled_a_waiver(before, after)
                if not later and (_waives_now(before, after, word) or _type_field(before, after)
                                  or (in_title and not numbered and (near or not near_only) and (heading or _a_waiver_phrase(before, after)))
                                  or (heading and near and _only_a_title(before, after))
                                  or (not near_only and heading and not _about_one(before, after))):
                    out["waiver"] = True
                elif near_only and not near:
                    continue
                if not out["found"]:
                    out["mention"] = True
                    out["found"] = " ".join(kept[max(0, found.start() - _FOUND_CHARS):found.end() + _FOUND_CHARS].split())
    except Exception:
        sys.stderr.write(traceback.format_exc())
    return out


def says_unconditional(files: list[tuple[str, str, Any]], near_only: bool = False) -> bool:
    """Does one of these files use the words at all (unconditional_reading)?"""
    return unconditional_reading(files, near_only)["mention"]


def _without_job_name(text: str, job_name: str) -> str:
    """The text with the job's own name taken out: a job called Final Grade says nothing about the
    waiver's type."""
    words = re.findall(r"[a-z0-9]+", job_name.lower())
    return re.sub(r"[^a-z0-9]+".join(words), " ", text, flags=re.I) if words else text


MENTIONS_UNCONDITIONAL = "It mentions an unconditional waiver. Check this is the conditional one."


def _type_problems(text: str, waiver_kind: str, job_name: str = "") -> list[str]:
    """The wording of the waiver's type, against the item. Only what is plainly another type warns;
    the word unconditional on a conditional item always does, whatever else it says (a form that
    says "not an unconditional release" gets a look, and a person clears it), and the plain word
    conditional on an unconditional item does too. "Upon payment" alone isn't that word: Georgia's
    unconditional forms say it."""
    text = _without_job_name(text, job_name)
    plain = _UNCONDITIONAL.sub(" ", text)
    out = []
    if waiver_kind.startswith("conditional"):
        if plain != text:
            # One that is an unconditional waiver, or a conditional form that talks about one (Nov 6 review, A).
            gate = unconditional_reading([("", ".txt", ReadFile(text=text))])["waiver"]
            out.append("It says unconditional, not conditional." if gate else MENTIONS_UNCONDITIONAL)
        elif not _CONDITIONAL.search(plain) and not _FINAL.search(plain):
            out.append("Conditional wording not found.")
    elif waiver_kind.startswith("unconditional"):
        if _PLAIN_CONDITIONAL.search(plain) and not _reads_as_unconditional(_NEGATED_UNCONDITIONAL.sub(" ", text)):
            out.append("It says conditional, not unconditional.")
        elif plain == text and not _CONDITIONAL.search(plain) and not _FINAL.search(plain):
            out.append("Unconditional wording not found.")
    if waiver_kind.endswith("_final") and not _FINAL.search(plain):
        out.append("Final wording not found.")
    elif waiver_kind.endswith("_progress") and _FINAL.search(plain) and not _PROGRESS.search(plain):
        out.append("It says final, not interim or progress.")
    return out


def check(files: list[tuple[str, str, Any]], job_name: str, money_text: str = "", cents: int | None = None,
          waiver_kind: str = "", is_waiver: bool = False) -> dict[str, Any]:
    """files is (name, extension, then the bytes, what prepare() got from them, or None when it
    couldn't be read). Returns {status, problems}: status "matched", "flagged" (something didn't
    match, or the check itself failed) or "unchecked" (no text to compare). Never raises."""
    try:
        return _check(files, job_name, money_text, cents, waiver_kind, is_waiver)
    except Exception:
        sys.stderr.write(traceback.format_exc())
        names = ", ".join(str(f[0]) for f in files)
        return {"status": "flagged", "problems": [f"{names} couldn't be checked. Open it and confirm it is right."]}


def _check(files: list[tuple[str, str, Any]], job_name: str, money_text: str, cents: int | None,
           waiver_kind: str, is_waiver: bool) -> dict[str, Any]:
    texts, unchecked, problems = [], [], []
    for name, ext, data in files:
        if data is None:
            problems.append(f"{name} couldn't be opened.")
            continue
        if ext in PICTURES:
            unchecked.append(f"{name} is a photo or a scan, so it couldn't be checked.")
            continue
        read = data if isinstance(data, ReadFile) else prepare(ext, data)
        text = read.text
        if read.failed:
            problems.append(f"{name} couldn't be opened.")
        elif text is None:
            unchecked.append(f"{name} couldn't be checked.")
        elif _letters(text) >= BLANK_LETTERS:
            texts.append(text)
        elif read.has_image:
            unchecked.append(f"{name} is a scan, so it couldn't be checked.")
        else:
            problems.append(f"{name} looks blank: no text was found in it.")
    if texts:
        text = "\n".join(texts)
        if job_name and not _has_job_name(text, job_name):
            problems.append(f"Job name not found: {job_name}.")
        if is_waiver:
            if cents is not None and not _has_amount(text, cents):
                problems.append(f"Amount not found: {money_text}.")
            problems += _type_problems(text, waiver_kind, job_name)
    if problems:
        return {"status": "flagged", "problems": problems + unchecked}
    if unchecked and not texts:
        return {"status": "unchecked", "problems": unchecked}
    return {"status": "matched", "problems": unchecked}
