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
# The plain word, the same way, with any unconditional taken out first.
_PLAIN_CONDITIONAL = re.compile(r"(?<![a-z])" + r"[\s\-\u00ad\u2010-\u2015]*".join("conditional") + r"(?![a-z])", re.I)


# "not an unconditional release", "does not constitute an unconditional waiver": the word taken back
# at once. Only words that can sit between the two: "not conditional, and is an unconditional
# waiver" says it is one, so it stays.
_NEGATED_UNCONDITIONAL = re.compile(r"(?<![a-z])(?:not|isn['\u2019]?t|never|no)(?:\s+(?:to|be|an?|any|as|is|this|constitutes?|deemed|considered|construed|intended|meant|represents?|serves?))*\s+"
                                    + _UNCONDITIONAL.pattern, re.I)
_WAIVER_WORDS = re.compile(r"waiver|release|lien", re.I)
_NEAR_WORDS = 4


def _near_a_waiver_word(text: str, found: "re.Match[str]") -> bool:
    around = text[:found.start()].split()[-_NEAR_WORDS:] + text[found.end():].split()[:_NEAR_WORDS]
    return any(_WAIVER_WORDS.search(word) for word in around)


def says_unconditional(files: list[tuple[str, str, Any]], near_only: bool = False) -> bool:
    """Does one of these files read as an unconditional waiver, for the warning at send (M14)? The
    word taken back at once doesn't count here; it stays a prompt to look at confirm (_type_problems).
    near_only: for a file not filed as a waiver, it counts only within a few words of waiver,
    release or lien (an addendum can use the word for something else). Never raises."""
    try:
        for _name, ext, data in files:
            if data is None or ext in PICTURES:
                continue
            read = data if isinstance(data, ReadFile) else prepare(ext, data)
            text = read.text or ""
            kept = _NEGATED_UNCONDITIONAL.sub(" ", text)
            if any(not near_only or _near_a_waiver_word(kept, found) for found in _UNCONDITIONAL.finditer(kept)):
                return True
    except Exception:
        sys.stderr.write(traceback.format_exc())
    return False


def _without_job_name(text: str, job_name: str) -> str:
    """The text with the job's own name taken out: a job called Final Grade says nothing about the
    waiver's type."""
    words = re.findall(r"[a-z0-9]+", job_name.lower())
    return re.sub(r"[^a-z0-9]+".join(words), " ", text, flags=re.I) if words else text


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
            out.append("It says unconditional, not conditional.")
        elif not _CONDITIONAL.search(plain) and not _FINAL.search(plain):
            out.append("Conditional wording not found.")
    elif waiver_kind.startswith("unconditional"):
        if _PLAIN_CONDITIONAL.search(plain):
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
