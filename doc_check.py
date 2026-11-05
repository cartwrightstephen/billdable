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


def _type_problems(text: str, waiver_kind: str) -> list[str]:
    """The wording of the waiver's type, against the item. Only what is plainly another type warns."""
    plain = re.sub(r"unconditional", " ", text, flags=re.I)
    out = []
    if waiver_kind.startswith("conditional"):
        if plain != text and not _CONDITIONAL.search(plain):
            out.append("It says unconditional, not conditional.")
        elif not _CONDITIONAL.search(plain) and not _FINAL.search(plain):
            out.append("Conditional wording not found.")
    elif waiver_kind.startswith("unconditional"):
        if plain == text and not _CONDITIONAL.search(plain) and not _FINAL.search(plain):
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
            problems += _type_problems(text, waiver_kind)
    if problems:
        return {"status": "flagged", "problems": problems + unchecked}
    if unchecked and not texts:
        return {"status": "unchecked", "problems": unchecked}
    return {"status": "matched", "problems": unchecked}
