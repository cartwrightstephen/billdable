"""Reading a document attached to a pay app's supporting documents (Stephen's Nov 1 notes, Decision
6): is it about this job, and for a lien waiver, this amount, and which waiver it reads as? What
doesn't match is a warning naming it, for a person to look at; it never blocks on its own. Which
waiver a file is, and whether a file is a lien waiver at all, is the person's answer (reading()). Text
comes from contract_import's readers, which stay small whatever the file inflates to. A scan or a
photo has none, so it says it couldn't be checked, unless the background reader (ocr.py, off unless
OCR_ENABLED=1) has stored its text: with_stored_text() then puts that text on what prepare() got,
and everything after it treats the file like any other with text.

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
    couldn't be opened, whether a PDF has pictures in it, and whether the text is what the
    background reader (ocr.py) got from a scan or a photo."""
    text: str | None = None
    failed: bool = False
    has_image: bool = False
    ocr: bool = False


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


def with_stored_text(read: ReadFile, ext: str, ocr_text: Any) -> ReadFile:
    """What prepare() got, with the text the background reader stored for the file (ocr.py) when
    the file had none of its own: a photo, or a PDF with no text in it. ocr_text() gives that
    text, "" when there is none; it is asked only then. Never raises."""
    if read.failed or not (ext in PICTURES or ext == ".pdf") or not no_text(read):
        return read
    try:
        stored = str(ocr_text() or "")[:contract_import.MAX_TEXT]
    except Exception:
        stored = ""
    if _letters(stored) >= BLANK_LETTERS:
        read.text, read.ocr = stored, True
    return read


def no_text(read: ReadFile) -> bool:
    """Nothing to compare came out of the file: a photo, a scan, a blank page."""
    return _letters(read.text or "") < BLANK_LETTERS


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


# The type of a waiver is the person's answer (Nov 6 redesign): "Which waiver is this?" on a waiver
# item, "Is this a lien waiver?" on any other file that reads as one. The text only sets the answer
# that is picked first and the warning beside it. reading() says, for a file's text:
#   guess: which waiver it reads as. The first heading-like line that says only one of conditional
#     or unconditional; else the one ticked ([X] Unconditional, Conditional [X]); else the one said
#     more often; else blank, and the caller goes by the record's type, then the item's.
#   form: it reads as a lien waiver form, anywhere in the text: a heading-like line names a waiver
#     (one: "Lien Waivers" is a contract's section about them) or a release of lien, says it waives or
#     releases lien rights, or gives the waiver's type with release or lien or on its own; a
#     statutory form's title; the type (conditional or unconditional) within a few words of waiver,
#     release or lien; or operative release language ("waives and releases", "releases all lien
#     rights", "hereby ... releases") with a lien reference. Only a question hangs on it, never a
#     gate, so it is wide: a contract's lien waiver clause is asked about once, and No clears it.
#     A waiver of subrogation or of the right to recover (insurance) is no lien waiver, but a waiver
#     or release of claims with the type or a payment beside it is one (_claim_waiver).
# A heading-like line is a sentence of one of the first HEAD_LINES lines, of at most TITLE_WORDS words.
HEAD_LINES = 10
TITLE_WORDS = 12
_UN = r"un[\s\-\u00ad\u2010-\u2015]*"   # UN-CONDITIONAL, un conditional, UN and CONDITIONAL on two lines
_UNCONDITIONAL = re.compile(rf"\b{_UN}conditional(?:ly)?\b", re.I)
_CONDITIONAL = re.compile(r"\bconditional\b", re.I)
# "not an unconditional release", "does not constitute an unconditional waiver": a few words at most between.
_NOT_THE_TYPE = re.compile(rf"\b(?:not|isn['\u2019]?t|never)(?:\s+[a-z]+){{0,5}}?\s+(?:{_UN})?conditional(?:ly)?\b", re.I)
_NAMES_A_WAIVER = re.compile(r"\bwaiver\b|\breleases?\s+of\s+(?:\w+\s+)?liens?\b|\blien\s+releases?\b|\b(?:waives?|releases?)\b[^.;]{0,40}\blien\b", re.I)
_RELEASE_OR_LIEN = re.compile(r"\b(?:release|lien)\b", re.I)
_TICK = r"(?:\[\s*[xX\u2713\u2714]\s*\]|\(\s*[xX\u2713\u2714]\s*\)|[\u2611\u2612\u2713\u2714\u25a0])"
_TICKED = {word: re.compile(rf"(?:{_TICK}|(?:^|(?<=\s))[xX])[ \t]{{0,2}}{pattern}(?![a-z])|(?<![a-z]){pattern}[ \t]?{_TICK}", re.I | re.M)
           for word, pattern in (("unconditional", r"un-?conditional"), ("conditional", r"(?<![a-z])(?<!un-)conditional"))}
_STATUTORY_TITLE = re.compile(r"\b(?:un)?conditional\s+waiver\s+and\s+release\s+(?:on|upon)\s+(?:progress|final)\s+payment"
                              r"|\binterim\s+waiver\s+and\s+release\s+upon\s+payment"
                              r"|\bwaiver\s+and\s+release\s+of\s+lien\s+(?:and\s+payment\s+bond\s+rights\s+)?upon\s+(?:progress|final)\s+payment", re.I)
# The type within three words of waiver, release or lien, in either order: "Acme - Unconditional
# Waiver and Release of Lien" (a title too long to be a heading), "waiver (unconditional)".
_TYPE = rf"(?:{_UN})?conditional(?:ly)?"
_RIGHTS = r"(?:waiv\w*|releas\w*|liens?)"
_TYPE_NEAR_RIGHTS = re.compile(rf"\b{_TYPE}\b(?:\W+\w+){{0,3}}?\W+{_RIGHTS}\b|\b{_RIGHTS}\b(?:\W+\w+){{0,3}}?\W+{_TYPE}\b", re.I)
# Operative release language: it gives the rights up. A lien reference has to be in the text too.
_OPERATIVE = re.compile(r"\bwaiv\w*\s+(?:and|or)\s+releas\w*|\breleas\w*\s+(?:and|or)\s+waiv\w*"
                        r"|\breleases?\s+(?:\w+\s+){0,3}?liens?\b|\bwaives?\b[^.;]{0,50}\bliens?\b"
                        r"|\bliens?\b[^.;]{0,60}\b(?:are|is|hereby)\s+(?:\w+\s+)?(?:waived|released)\b"
                        r"|\bhereby\s+(?:\w+\s+){0,6}?(?:waives?|releases?)\b", re.I)
_LIEN = re.compile(r"\bliens?\b", re.I)
# Insurance: "Waiver of subrogation", "waiver of our right to recover", "waiver of transfer of rights of recovery".
_INSURANCE_WAIVER = re.compile(r"\bwaivers?\s+of\s+(?:(?!liens?\b)\w+\s+){0,6}?(?:subrogation|recover\w*)\b|\bwaives?\s+(?:(?!liens?\b)\w+\s+){0,3}?subrogation\b", re.I)
# A waiver or release of claims is a lien waiver when the type or a payment is beside it: "Unconditional
# Waiver of Claim and Right to Recover on Progress Payment" never says lien, and the strip above would
# take its "right to recover" away. Insurance says neither (an endorsement's waiver of claims and
# subrogation has no payment or type near it).
_CLAIM_WAIVER = re.compile(r"\b(?:waivers?|releases?)\s+of\s+(?:\w+\s+){0,2}?claims?\b", re.I)
_TYPE_OR_PAYMENT = re.compile(rf"\b{_TYPE}\b|\bpayments?\b", re.I)
CLAIM_REACH = 80   # characters either side of the words


def _claim_waiver(text: str) -> bool:
    return any(_TYPE_OR_PAYMENT.search(text[max(0, m.start() - CLAIM_REACH):m.end() + CLAIM_REACH]) for m in _CLAIM_WAIVER.finditer(text))


_SPACED_LETTERS = re.compile(r"(?<![A-Za-z])(?:[A-Za-z] ){3,}[A-Za-z](?![A-Za-z])")   # U N C O N D I T I O N A L


def _plain(text: str) -> str:
    """Spaced-out letters and words broken across lines put back together."""
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=[A-Za-z])-[ \t]*\n[ \t]*(?=[A-Za-z])|(?<=\bun)[ \t]*\n[ \t]*(?=conditional)", "", text, flags=re.I)
    return _SPACED_LETTERS.sub(lambda m: m.group(0).replace(" ", ""), text)


def _says(text: str) -> set[str]:
    """Which of the two words a piece of text says, leaving out "not a conditional"."""
    text = _NOT_THE_TYPE.sub(" ", text)
    said = {"unconditional"} if _UNCONDITIONAL.search(text) else set()
    return said | ({"conditional"} if _CONDITIONAL.search(_UNCONDITIONAL.sub(" ", text)) else set())


def _names_a_waiver(line: str) -> bool:
    """A waiver (one), a release of lien, waiving or releasing lien rights, or the type with
    release or lien, or on its own ("UNCONDITIONAL RELEASE", "UNCONDITIONAL")."""
    if _NAMES_A_WAIVER.search(line):
        return True
    return bool(_says(line)) and bool(_RELEASE_OR_LIEN.search(line) or re.fullmatch(rf"\W*(?:{_UN})?conditional\W*", line, re.I))


def _heading_lines(text: str) -> list[str]:
    """The heading-like lines: each sentence of the first HEAD_LINES lines with words in them, at
    most TITLE_WORDS words long. Only the first lines are split, however long the file."""
    lines = [line.strip() for line in text.split("\n", HEAD_LINES * 4)[:HEAD_LINES * 4] if line.strip()][:HEAD_LINES]
    return [s for line in lines for s in re.split(r"(?<=[.;!?])\s+", line) if s and len(s.split()) <= TITLE_WORDS]


def reading(text: str) -> dict[str, Any]:
    """{guess, form} for one file's text, as above."""
    plain = _plain(text or "")
    text = _INSURANCE_WAIVER.sub(" ", plain)
    heads = _heading_lines(text)
    guess = ""
    for line in heads:
        said = _says(line)
        if len(said) == 1 and re.search(r"waiver|release|lien", line, re.I):
            guess = said.pop()
            break
    if not guess:
        ticked = {word for word, tick in _TICKED.items() if tick.search(text)}
        if len(ticked) == 1:
            guess = ticked.pop()
    if not guess:
        kept = _NOT_THE_TYPE.sub(" ", text)
        many = len(_UNCONDITIONAL.findall(kept)), len(_CONDITIONAL.findall(_UNCONDITIONAL.sub(" ", kept)))
        guess = "unconditional" if many[0] > many[1] else "conditional" if many[1] > many[0] else ""
    form = (any(_names_a_waiver(line) for line in heads) or bool(_STATUTORY_TITLE.search(text)) or bool(_TYPE_NEAR_RIGHTS.search(text))
            or bool(_OPERATIVE.search(text) and _LIEN.search(text)) or _claim_waiver(plain))
    return {"guess": guess, "form": form}


FORM_LINES = 600   # lines looked at for a form of each kind (separate_forms)


def separate_forms(text: str) -> bool:
    """Both a conditional and an unconditional waiver are in the text as forms of their own: a line
    (not a sentence of a longer one) of at most TITLE_WORDS words that names a waiver and says one
    of the words, for each word, in the first FORM_LINES lines. "If you have not been paid, use a
    conditional release form." in a notice on an unconditional form is a sentence, not a title."""
    text = _INSURANCE_WAIVER.sub(" ", _plain(text or ""))
    lines = [line.strip() for line in text.split("\n", FORM_LINES * 4)[:FORM_LINES * 4] if line.strip()][:FORM_LINES]
    titled = set()
    for line in lines:
        if len(line.split()) <= TITLE_WORDS and not line.endswith((".", ":")) and _names_a_waiver(line):
            said = _says(line)
            if len(said) == 1:
                titled |= said
    return len(titled) == 2


def waiver_reading(files: list[tuple[str, str, Any]]) -> dict[str, Any]:
    """reading() for a record's files together: any that reads as a form makes it one. The guess is
    unconditional when any file reads so (the answer to check first), else conditional when any
    does, else blank. "both": the files read as different waivers, or a file has a conditional and
    an unconditional form in it; then there is no guess, since the app can't tell which one the
    person means. A photo, a scan or a file that couldn't be read says nothing. Never raises."""
    out = {"guess": "", "form": False, "both": False}
    try:
        guesses = set()
        for _name, ext, data in files:
            if data is None:
                continue
            read = data if isinstance(data, ReadFile) else prepare(ext, data)
            if not read.text:   # a photo or a scan, unless its text was read in the background
                continue
            got = reading(read.text)
            out["form"] = out["form"] or got["form"]
            out["both"] = out["both"] or separate_forms(read.text)
            guesses.add(got["guess"])
        out["both"] = out["both"] or {"conditional", "unconditional"} <= guesses
        out["guess"] = "" if out["both"] else "unconditional" if "unconditional" in guesses else "conditional" if "conditional" in guesses else ""
    except Exception:
        sys.stderr.write(traceback.format_exc())
    return out


_FINAL = re.compile(r"\bfinal\b", re.I)
_PROGRESS = re.compile(r"\binterim\b|\bprogress\b|\bpartial\b", re.I)


def _without_job_name(text: str, job_name: str) -> str:
    """The text with the job's own name taken out: a job called Final Grade says nothing about the
    waiver's type."""
    words = re.findall(r"[a-z0-9]+", job_name.lower())
    return re.sub(r"[^a-z0-9]+".join(words), " ", text, flags=re.I) if words else text


def _type_problems(text: str, waiver_kind: str, job_name: str = "") -> list[str]:
    """What the text reads as, against the item: a warning beside the person's answer, never the
    answer. Final or progress wording that is plainly the other one warns too."""
    text = _without_job_name(text, job_name)
    out = []
    want = waiver_kind.split("_")[0]
    guess = reading(text)["guess"]
    if want in ("conditional", "unconditional") and guess and guess != want:
        out.append(f"This reads as {guess}.")
    if waiver_kind.endswith("_final") and not _FINAL.search(text):
        out.append("Final wording not found.")
    elif waiver_kind.endswith("_progress") and _FINAL.search(text) and not _PROGRESS.search(text):
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
        read = data if isinstance(data, ReadFile) else prepare(ext, data)
        text = read.text
        if ext in PICTURES and not text:
            unchecked.append(f"{name} is a photo or a scan, so it couldn't be checked.")
            continue
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
