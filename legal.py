"""The Terms of Service and Privacy Policy pages, and the version people agree to at sign-up.

The text lives in legal/terms.html and legal/privacy.html. Company details come from
settings so the same text works before and after the company exists:

    LEGAL_ENTITY   the company's legal name, e.g. "Billdable LLC"
    LEGAL_ADDRESS  its postal address
    LEGAL_EMAIL    the contact address for privacy and legal questions
    LEGAL_STATE    the state whose law governs the Terms
    DATA_REGION    where the hosting stores data, e.g. "the United States (Oregon)"
    LEGAL_REVIEWED set to 1 once a lawyer has reviewed the text and the details are filled in

Until LEGAL_REVIEWED=1, both pages carry a notice that they are a draft.
Change TERMS_VERSION whenever the text changes; each user records the version they accepted.
"""

from __future__ import annotations

import html
import os
from pathlib import Path

import backup

TERMS_VERSION = "2026-10-12"
FOLDER = Path(__file__).resolve().parent / "legal"
MISSING = "[to be completed]"


def details() -> dict[str, str]:
    cfg = backup.settings()
    days = max(1, (cfg["keep"] * cfg["hours"] + 23) // 24) if cfg["hours"] else 1
    return {
        "ENTITY": os.environ.get("LEGAL_ENTITY") or f"Billdable {MISSING}",
        "ADDRESS": os.environ.get("LEGAL_ADDRESS") or f"address {MISSING}",
        "EMAIL": os.environ.get("LEGAL_EMAIL") or f"contact email {MISSING}",
        "STATE": os.environ.get("LEGAL_STATE") or f"state {MISSING}",
        "REGION": os.environ.get("DATA_REGION") or f"the United States {MISSING}",
        "SITE": (os.environ.get("BILLDABLE_URL") or "the Billdable website").rstrip("/"),
        "EFFECTIVE": os.environ.get("LEGAL_EFFECTIVE") or TERMS_VERSION,
        "VERSION": TERMS_VERSION,
        "BACKUP_DAYS": str(days),
    }


def is_draft() -> bool:
    filled = all(os.environ.get(k) for k in ("LEGAL_ENTITY", "LEGAL_ADDRESS", "LEGAL_EMAIL", "LEGAL_STATE", "DATA_REGION"))
    return not (filled and os.environ.get("LEGAL_REVIEWED") == "1")


def page(kind: str) -> str:
    """The full HTML page. Every setting is HTML-escaped before it goes into the text."""
    body = (FOLDER / f"{kind}.html").read_text(encoding="utf-8")
    for key, value in details().items():
        body = body.replace("{{" + key + "}}", html.escape(value))
    title = "Terms of Service" if kind == "terms" else "Privacy Policy"
    notice = (
        '<p class="draft">Draft: this text has not yet been reviewed by a lawyer'
        + (", and the company details are not filled in." if not all(os.environ.get(k) for k in ("LEGAL_ENTITY", "LEGAL_ADDRESS", "LEGAL_EMAIL", "LEGAL_STATE")) else ".")
        + "</p>"
    ) if is_draft() else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Billdable</title>
<style>
body {{ font: 16px/1.6 -apple-system, "Segoe UI", system-ui, sans-serif; color: #1c1915; background: #f6f3ee; margin: 0; }}
main {{ max-width: 760px; margin: 0 auto; padding: 32px 18px 64px; }}
h1 {{ font-family: Georgia, serif; font-weight: 500; font-size: 32px; margin: 8px 0 4px; }}
h2 {{ font-size: 19px; margin: 28px 0 8px; }} h3 {{ font-size: 16px; margin: 18px 0 6px; }}
.meta {{ color: #6b645c; margin: 0 0 20px; }} a {{ color: #8c3f1d; }}
.draft {{ background: #f8efd8; border-radius: 8px; padding: 10px 12px; }}
nav {{ display: flex; gap: 16px; font-size: 14px; }} ul {{ padding-left: 22px; }}
</style></head><body><main>
<nav><a href="/">Billdable</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a></nav>
{notice}
{body}
</main></body></html>"""
