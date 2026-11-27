"""Passwords people pick first (security review L3).

problem() says why a new password is too easy to guess, or "" when it is fine. Besides the
length rule (auth.MIN_PASSWORD), it refuses:

- the common passwords in common_passwords.txt, as typed or with digits and symbols added at
  either end (Password123!) and with look-alike swaps (p@ssw0rd);
- one or two characters repeated (aaaaaaaaaa, abababab12);
- a run along the keyboard or the alphabet (1234567890, qwertyuiop, abcdefghij);
- the person's own email address, the part before the @, their name, or the company's name,
  with digits and symbols added.

Standard library only; the list is read once.
"""

from __future__ import annotations

import re
from pathlib import Path

LIST_PATH = Path(__file__).resolve().parent / "common_passwords.txt"
_COMMON: set[str] | None = None
_LOOKALIKE = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "|": "l", "+": "t"})
_ROWS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm", "abcdefghijklmnopqrstuvwxyz", "01234567890")
TOO_COMMON = "That password is one of the most common ones, so it is easy to guess. Choose something longer and less usual, such as three or four unrelated words."


def common() -> set[str]:
    global _COMMON
    if _COMMON is None:
        try:
            lines = LIST_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        _COMMON = {line.strip().lower() for line in lines if line.strip() and not line.lstrip().startswith("#")}
    return _COMMON


def _bases(text: str) -> set[str]:
    """The password reduced to its core: lower case, spaces dropped, digits and symbols trimmed
    from both ends, with and without look-alike letters swapped back."""
    low = re.sub(r"\s+", "", text.lower())
    out = {low}
    for form in (low, low.translate(_LOOKALIKE)):
        out.add(form)
        out.add(re.sub(r"^[\W\d_]+|[\W\d_]+$", "", form))
    trimmed = re.sub(r"^[\W\d_]+|[\W\d_]+$", "", low)
    out.add(trimmed.translate(_LOOKALIKE))
    out.discard("")
    return out


def _is_run(text: str) -> bool:
    low = re.sub(r"\s+", "", text.lower())
    for row in _ROWS:
        for line in (row, row[::-1]):
            if low and (low in line * 2):
                return True
    return False


def problem(password: str, email: str = "", name: str = "", company: str = "") -> str:
    """Why this password is too easy to guess, or "" when it is fine."""
    if not isinstance(password, str) or not password:
        return ""
    squashed = re.sub(r"\s+", "", password.lower())
    if len(set(squashed)) <= 2:
        return "That password repeats one or two characters. Choose something less predictable."
    if _is_run(password):
        return "That password is a run of keys or letters in order. Choose something less predictable."
    bases = _bases(password)
    if bases & common():
        return TOO_COMMON
    email = (email or "").strip().lower()
    own = {email, email.split("@")[0], re.sub(r"[\W_]+", "", email.split("@")[0])}
    own |= {re.sub(r"[\W_]+", "", (name or "").lower()), re.sub(r"[\W_]+", "", (company or "").lower())}
    own = {w for w in own if len(w) >= 4}
    plain = {re.sub(r"[\W_]+", "", b) for b in bases} | bases
    if own & plain or (email and email in squashed):
        return "That password is your email address or name. Choose something others can't guess from them."
    return ""
