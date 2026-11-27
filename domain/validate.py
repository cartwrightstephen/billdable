"""Field rules shared by every form (G1): phone, ZIP, state, email, licence and contract numbers,
names and addresses. static/js/validate.js has the same rules, so a box is marked when it is left
and the server refuses the same values; tests/validate_cases.txt is the table both are tested on.

Each check returns {field: message}, the shape of a 409's "fields", so a refusal lands under the
box (refuseForm on the page). An empty value passes unless the field is required: a value
already saved is checked again only when someone edits it.

Imported at the bottom of domain/__init__.py (from .validate import *). Helpers from domain
are imported inside each function, so the two modules never import each other at load time.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

__all__ = [
    "US_STATES", "FIELD_RULES", "rule_message", "normalize_phone", "normalize_state", "check_field", "zip_states", "zip_state_question",
    "check_fields", "refuse_fields", "split_address", "compose_address", "parse_legacy_address", "check_address",
]

# USPS codes: the 50 states, DC, and the territories (PR, VI, GU, AS, MP).
US_STATES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
    "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "VI", "GU", "AS", "MP",
)

# [0-9], not \d: Python's \d also takes other scripts' digits ("٣٠٣٠٣"), which the page's rules refuse.
_PHONE_RE = re.compile(r"^\s*(?:\+?1[\s.-]*)?\(?([0-9]{3})\)?[\s.-]*([0-9]{3})[\s.-]*([0-9]{4})\s*(?:(?:x|ext\.?|extension)\s*([0-9]{1,6}))?\s*$", re.I)
_ZIP_RE = re.compile(r"^[0-9]{5}(?:-[0-9]{4})?$")
# Contract and licence numbers as GCs write them: "PO# 4500-12", "SC_2026_014", "2026-014 (Rev 1)".
_NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ./#_&()-]*$")
# A name, street or city has a letter (any alphabet) or a digit: "Café Ñandú", "北京路 12" pass.
_HAS_WORD_RE = re.compile(r"[^\W_]")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")   # the same as domain.EMAIL_RE

# rule -> (the message when it fails, the longest it may be)
FIELD_RULES: dict[str, tuple[str, int]] = {
    "phone": ("Enter a 10-digit phone number, like (404) 555-0123.", 40),
    "zip": ("Enter a 5-digit ZIP code, like 30303, or ZIP+4, like 30303-1234.", 10),
    "state": ("Enter the two-letter state, like GA.", 3),
    "email": ("Enter an email address, like name@example.com.", 254),
    "licence": ("Use letters, numbers, spaces and - / . # _ & ( ) only, up to 40.", 40),
    "contract_number": ("Use letters, numbers, spaces and - / . # _ & ( ) only, up to 40.", 40),
    "name": ("Enter a name with at least one letter or number, up to 120.", 120),
    "street": ("Enter the street address, up to 120 characters, like 120 Peachtree St NE.", 120),
    "city": ("Enter the city.", 60),
}


def rule_message(rule: str) -> str:
    return FIELD_RULES[rule][0]


def normalize_phone(value: str) -> str | None:
    """A US phone as (xxx) xxx-xxxx (with " x123" for an extension), or None if it isn't one."""
    m = _PHONE_RE.match(str(value or ""))
    if not m or len(set(m.group(1) + m.group(2) + m.group(3))) == 1:   # 0000000000 is nobody's number
        return None
    out = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"
    return out + (f" x{m.group(4)}" if m.group(4) else "")


def normalize_state(value: str) -> str | None:
    code = str(value or "").strip().rstrip(".").upper()
    return code if code in US_STATES else None


def _passes(rule: str, value: str) -> bool:
    if rule == "phone":
        return normalize_phone(value) is not None
    if len(value) > FIELD_RULES[rule][1]:
        return False
    if rule == "zip":
        return bool(_ZIP_RE.match(value))
    if rule == "state":
        return normalize_state(value) is not None
    if rule == "email":
        return bool(_EMAIL_RE.match(value))
    if rule in ("licence", "contract_number"):
        return bool(_NUMBER_RE.match(value))
    if rule in ("name", "street", "city"):
        return bool(_HAS_WORD_RE.search(value))
    raise KeyError(rule)


def check_field(rule: str, value: Any, field: str | None = None, required: bool = False) -> dict[str, str]:
    """{field: message} when value breaks the rule, else {}. Blank passes unless required."""
    text = str(value if value is not None else "").strip()
    key = field or rule
    if not text:
        return {key: rule_message(rule)} if required else {}
    return {} if _passes(rule, text) else {key: rule_message(rule)}


def check_fields(values: dict[str, Any], rules: dict[str, str], required: Iterable[str] = ()) -> dict[str, str]:
    """Check each field named in rules ({field: rule}) that values has (or that is required)."""
    need = set(required)
    errors: dict[str, str] = {}
    for field, rule in rules.items():
        if field in values or field in need:
            errors.update(check_field(rule, values.get(field), field, field in need))
    return errors


def refuse_fields(errors: dict[str, str], message: str = "Check the marked boxes.") -> None:
    """Raise the 409 that marks each box, when there is anything to mark."""
    if errors:
        from domain import GateError
        raise GateError(message, fields=errors)


# ---------- addresses: Street, City, ST 12345 ----------

_ADDRESS_RE = re.compile(
    r"^\s*(?P<street>.+?)\s*,\s*(?P<city>[^,]+?)\s*,?\s+(?P<state>[A-Za-z]{2})\.?(?:\s+(?P<zip>[0-9]{5}(?:-[0-9]{4})?))?\s*$")


def compose_address(parts: dict[str, Any]) -> str:
    """'Street, City, ST 12345' from its parts; the blank ones are left out."""
    street, city = (str(parts.get(k) or "").strip() for k in ("street", "city"))
    state = (normalize_state(parts.get("state") or "") or str(parts.get("state") or "").strip()).upper()
    zip_code = str(parts.get("zip") or "").strip()
    tail = " ".join(x for x in (state, zip_code) if x)
    return ", ".join(x for x in (street, city, tail) if x)


def split_address(text: str) -> dict[str, str] | None:
    """The parts of 'Street, City, ST 12345' (the ZIP may be missing), or None when it doesn't read
    that way or the state isn't a US one."""
    one = re.sub(r"\s*\n\s*", ", ", str(text or "").strip())
    m = _ADDRESS_RE.match(one)
    if not m or not normalize_state(m.group("state")):
        return None
    return {"street": m.group("street"), "city": m.group("city"), "state": m.group("state").upper(), "zip": m.group("zip") or ""}


def parse_legacy_address(text: str) -> dict[str, str]:
    """An address saved as one line, in parts for the four boxes: split when it reads as
    'Street, City, ST 12345', else all of it in the street box so nothing is lost."""
    return split_address(text) or {"street": str(text or "").strip(), "city": "", "state": "", "zip": ""}


def check_address(parts: dict[str, Any], prefix: str = "", required: bool = True) -> dict[str, str]:
    """Errors for an address's four boxes, keyed prefix + street / city / state / zip."""
    rules = {"street": "street", "city": "city", "state": "state", "zip": "zip"}
    errors = check_fields(parts, rules, required=rules if required else ())
    return {prefix + k: v for k, v in errors.items()}


# The state(s) a ZIP code belongs to, by its first three digits (USPS prefix ranges), as
# static/js/validate.js has them: the question asked when a ZIP doesn't match the state (Stephen,
# 10 Nov; on the server too since the Nov 27 audit, for saves and imports the page didn't ask).
_ZIP3 = (
    (5, 5, "NY"), (6, 7, "PR"), (8, 8, "VI"), (9, 9, "PR"), (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"),
    (39, 49, "ME"), (50, 54, "VT"), (55, 55, "MA"), (56, 59, "VT"), (60, 69, "CT"), (70, 89, "NJ"),
    (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"), (200, 200, "DC"), (201, 201, "VA"), (202, 205, "DC"),
    (206, 219, "MD"), (220, 246, "VA"), (247, 268, "WV"), (270, 289, "NC"), (290, 299, "SC"), (300, 319, "GA"),
    (320, 339, "FL"), (341, 349, "FL"), (350, 369, "AL"), (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"),
    (400, 427, "KY"), (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"), (500, 528, "IA"), (530, 549, "WI"),
    (550, 567, "MN"), (569, 569, "DC"), (570, 577, "SD"), (580, 588, "ND"), (590, 599, "MT"), (600, 629, "IL"),
    (630, 658, "MO"), (660, 679, "KS"), (680, 693, "NE"), (700, 714, "LA"), (716, 729, "AR"), (730, 732, "OK"),
    (733, 733, "TX"), (734, 749, "OK"), (750, 799, "TX"), (800, 816, "CO"), (820, 831, "WY"), (832, 838, "ID"),
    (840, 847, "UT"), (850, 865, "AZ"), (870, 884, "NM"), (885, 885, "TX"), (889, 898, "NV"), (900, 961, "CA"),
    (967, 968, "HI"), (969, 969, ("GU", "MP", "AS")), (970, 979, "OR"), (980, 994, "WA"), (995, 999, "AK"),
)
_STATE_NAMES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
                "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
                "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
                "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
                "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
                "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
                "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "DC",
                "PR": "Puerto Rico", "VI": "the Virgin Islands", "GU": "Guam", "AS": "American Samoa", "MP": "the Northern Mariana Islands"}


def zip_states(zip_code: Any) -> tuple[str, ...] | None:
    m = re.fullmatch(r"(\d{3})\d{2}(?:-\d{4})?", str(zip_code or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    hit = next((states for lo, hi, states in _ZIP3 if lo <= n <= hi), None)
    return None if hit is None else ((hit,) if isinstance(hit, str) else hit)


def zip_state_question(zip_code: Any, state: Any) -> str:
    """The question when a ZIP belongs to another state, in the page's words; "" when it fits, or
    either is blank or not valid. A warning, never a refusal: a few ZIPs cross state lines."""
    code = normalize_state(str(state or ""))
    states = zip_states(zip_code)
    if not code or not states or code in states:
        return ""
    return (f"ZIP {str(zip_code).strip()} is in {_STATE_NAMES.get(states[0], states[0])}, "
            f"not {_STATE_NAMES.get(code, code)}. Check the state or the ZIP.")
