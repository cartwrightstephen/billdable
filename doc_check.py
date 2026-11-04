"""Reading a document attached to a pay app's supporting documents (Stephen's Nov 1 notes, Decision
6): is it about this job, and for a lien waiver, this amount and the conditional wording? What
doesn't match is a warning naming it, for a person to look at; it never blocks on its own. Text
comes from contract_import's readers. A scan or a photo has none, so it says it couldn't be
checked; there is no OCR here."""

from __future__ import annotations

import re
from typing import Any

import contract_import

PICTURES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
BLANK_LETTERS = 8   # fewer letters than this is a blank page's stray marks


def _text(ext: str, data: bytes) -> str | None:
    """The file's text, or None for a type that isn't read."""
    if ext == ".pdf":
        return contract_import.pdf_text(data)
    if ext == ".docx":
        return contract_import.docx_text(data)
    if ext == ".txt":
        return data.decode("utf-8", "replace")
    return None


def _letters(text: str) -> int:
    return sum(c.isalpha() for c in text)


def _has_job_name(text: str, job_name: str) -> bool:
    """Every word of the job's name is in the text, whatever the order or capitals."""
    have = set(re.findall(r"[a-z0-9]+", text.lower()))
    return all(w in have for w in re.findall(r"[a-z0-9]+", job_name.lower()))


def _has_amount(text: str, cents: int) -> bool:
    for found in re.findall(r"\d[\d,]*(?:\.\d{1,2})?", text):
        try:
            if round(float(found.replace(",", "")) * 100) == cents:
                return True
        except ValueError:
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


def check(files: list[tuple[str, str, bytes | None]], job_name: str, money_text: str = "", cents: int | None = None,
          waiver_kind: str = "", is_waiver: bool = False) -> dict[str, Any]:
    """files is (name, extension, bytes or None when it couldn't be read). Returns {status, problems}:
    status "matched", "flagged" (something didn't match) or "unchecked" (no text to compare)."""
    texts, unchecked, problems = [], [], []
    for name, ext, data in files:
        if data is None:
            problems.append(f"{name} couldn't be opened.")
            continue
        if ext in PICTURES:
            unchecked.append(f"{name} is a photo or a scan, so it couldn't be checked.")
            continue
        try:
            text = _text(ext, data)
        except Exception:  # a damaged Word file or PDF: the person looks at it, it never blocks
            problems.append(f"{name} couldn't be opened.")
            continue
        if text is None:
            unchecked.append(f"{name} couldn't be checked.")
        elif _letters(text) >= BLANK_LETTERS:
            texts.append(text)
        elif ext == ".pdf" and b"/Image" in data:
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
