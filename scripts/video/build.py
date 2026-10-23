"""Builds the Duracash promo video from the recorded clips. Adapted from the YuuP video tools.

    swiftc -O scripts/video/record.swift -o <tmp>/duracash-record
    swiftc -O scripts/video/edit.swift   -o <tmp>/duracash-edit
    <tmp>/duracash-record scripts/video/shots.json <out>     # the clips, against a local server
    python3 scripts/video/build.py                             # narration, captions, the video

Needs <out>/raw/*.mp4 (record.swift against a local server on the demo company), the macOS
`say` voice and the compiled editor. <out> is DURACASH_VIDEO_OUT, by default the work folder's
10-Promo-video; the editor is DURACASH_EDIT. Writes <out>/duracash-promo.mp4 (about 45 s).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(os.environ.get("DURACASH_VIDEO_OUT",
                          Path.home() / "Claude/Billdable/Billdable-work-2026-10-12/10-Promo-video"))
EDIT = Path(os.environ.get("DURACASH_EDIT", "/private/tmp/claude-501/bd-video/duracash-edit"))
RATE = "176"
PREFERRED = "Zoe (Premium)"                  # the YuuP promo's voice, used whenever it's installed
SAY_AS: dict[str, str] = {}                  # spoken spellings, if a word comes out wrong


def best_voice() -> str:
    """PREFERRED if installed, else the best English macOS voice (Premium, Enhanced), else Samantha."""
    listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    if re.search(rf"^{re.escape(PREFERRED)}\s{{2,}}", listing, re.M):
        return PREFERRED
    found = []
    for line in listing.splitlines():
        m = re.match(r"(.+?)\s{2,}(en_[A-Z]+)\s+#", line)
        if m:
            name, locale = m.group(1).strip(), m.group(2)
            quality = 2 if "(Premium)" in name else 1 if "(Enhanced)" in name else 0
            found.append((-quality, locale != "en_US", name))
    best = min(found, default=(0, False, "Samantha"))
    return best[2] if best[0] < 0 else "Samantha"


VOICE = os.environ.get("DURACASH_VOICE") or best_voice()

PROMO = [
    {"title": "Duracash", "subtitle": "Finished work, billed and paid", "min": 2.5, "say": ["Meet Duracash."]},
    {"src": "01_today", "title": "Today",
     "say": ["Every morning, see what's holding up your money."]},
    {"src": "02_packet", "title": "Packet check",
     "say": ["Nothing goes out with a missing waiver or photo."]},
    {"src": "03_payapp", "title": "Pay app",
     "say": ["Draft the pay app from finished work.", "Approve it, and it goes out with its packet."]},
    {"src": "04_followup", "title": "Follow-up",
     "say": ["If the payer goes quiet, one tap sends the follow-up."]},
    {"src": "05_paid", "title": "Paid",
     "say": ["Payments from QuickBooks are matched to the right pay app."]},
    {"src": "06_waiver", "title": "Lien waiver",
     "say": ["Send the waiver, and close the job for the record."]},
    {"src": "07_playbook", "title": "Payer playbook",
     "say": ["Every payer's rules, cutoff and real payment time, in one place."]},
    {"title": "Duracash", "subtitle": "You finish the work. Duracash gets it billed and collected.", "min": 3.5,
     "say": ["You finish the work. Duracash gets it billed and collected."]},
]


def narrate(name: str, items: list[dict]) -> list[dict]:
    vo = OUT / "vo" / name
    vo.mkdir(parents=True, exist_ok=True)
    spec = []
    for i, item in enumerate(items):
        it = {k: v for k, v in item.items() if k != "say"}
        if "src" in it:
            it["src"] = f"raw/{it['src']}.mp4"
        lines = []
        for j, text in enumerate(item.get("say", [])):
            spoken = text                                # the caption keeps the spelling
            for word, sound in SAY_AS.items():
                spoken = spoken.replace(word, sound)
            path = vo / f"{i:02d}_{j}.aiff"
            subprocess.run(["say", "-v", VOICE, "-r", RATE, "-o", str(path), spoken], check=True)
            lines.append({"text": text, "audio": str(path.relative_to(OUT))})
        it["lines"] = lines
        spec.append(it)
    return spec


def build(name: str, items: list[dict]) -> None:
    spec = {"out": f"duracash-{name}.mp4", "voice_gap": 0.3, "hold": 1.0, "items": narrate(name, items)}
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(spec, indent=1))
    subprocess.run([str(EDIT), "compose", str(path)], check=True)


if __name__ == "__main__":
    print(f"voice: {VOICE}")
    for name in sys.argv[1:] or ["promo"]:
        build(name, {"promo": PROMO}[name])
