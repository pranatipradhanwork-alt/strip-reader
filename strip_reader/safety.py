"""Safety rules: match names to a real medicine list and decide when to abstain.

The model's raw reading is never trusted on its own. An item is marked "ok"
only when every check passes; otherwise it is marked "confirm" and the user is
told to check with a pharmacist or doctor.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from rapidfuzz import fuzz, process, utils

CONFIRM_MSG = "Please confirm with a pharmacist or doctor."

# Thresholds (kept in one place so the eval in week 2 can tune them).
MIN_CONFIDENCE = 0.80   # model's own confidence for this item
MATCH_OK = 90           # name match score (0-100) to count as a sure match
MATCH_MAYBE = 75        # below this the name is treated as unknown

# A strength must look like "500 mg", "0.5mg", "10 ml", "5 mg/5 ml", "1000 IU".
STRENGTH_RE = re.compile(
    r"^\s*\d+(\.\d+)?\s*(mg|mcg|µg|g|ml|iu|%|units?)"
    r"(\s*/\s*\d*(\.\d+)?\s*(mg|mcg|g|ml))?\s*$",
    re.IGNORECASE,
)


@dataclass
class Medicine:
    name: str
    generic: str
    kind: str


def load_medicines(path: str | Path) -> list[Medicine]:
    with open(path, newline="", encoding="utf-8") as f:
        return [Medicine(r["name"].strip(), r["generic"].strip(), r["kind"].strip())
                for r in csv.DictReader(f) if r.get("name")]


@dataclass
class Match:
    name: str | None
    generic: str | None
    score: int


def match_name(raw: str | None, medicines: list[Medicine]) -> Match:
    if not raw or not raw.strip():
        return Match(None, None, 0)
    names = [m.name for m in medicines]
    best = process.extractOne(raw, names, scorer=fuzz.WRatio, processor=utils.default_process)
    if not best:
        return Match(None, None, 0)
    name, score, idx = best
    return Match(name, medicines[idx].generic, int(round(score)))


@dataclass
class CheckedItem:
    name_as_read: str | None
    matched_name: str | None
    generic: str | None
    match_score: int
    strength: str | None
    timing_text: str | None
    source: str
    confidence: float
    box_2d: list[int] | None
    crop_path: str | None = None
    status: str = "confirm"          # "ok" or "confirm"
    reasons: list[str] = field(default_factory=list)
    message: str = CONFIRM_MSG

    def to_dict(self) -> dict:
        return asdict(self)


def clean_strength(raw: str | None) -> tuple[str | None, str | None]:
    """Keep the strength only if it looks like a real printed strength.

    Returns (strength, reason_if_dropped). We never fill in a default.
    """
    if raw is None or not str(raw).strip():
        return None, None
    s = str(raw).strip()
    if STRENGTH_RE.match(s):
        return s, None
    return None, f"strength '{s}' did not look like a printed strength, so it was dropped"


def check_item(raw: dict, medicines: list[Medicine]) -> CheckedItem:
    """Apply all safety rules to one item from the vision model."""
    m = match_name(raw.get("name_as_printed"), medicines)
    strength, strength_note = clean_strength(raw.get("strength"))
    timing = (raw.get("timing_text") or "").strip() or None
    source = (raw.get("source") or "unknown").strip().lower()
    try:
        conf = max(0.0, min(1.0, float(raw.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0

    item = CheckedItem(
        name_as_read=raw.get("name_as_printed"),
        matched_name=m.name if m.score >= MATCH_MAYBE else None,
        generic=m.generic if m.score >= MATCH_MAYBE else None,
        match_score=m.score,
        strength=strength,
        timing_text=timing,
        source=source,
        confidence=round(conf, 2),
        box_2d=raw.get("box_2d"),
    )

    reasons = []
    if conf < MIN_CONFIDENCE:
        reasons.append(f"low reading confidence ({conf:.2f})")
    if source == "handwritten":
        reasons.append("handwritten text")
    if source == "unknown":
        reasons.append("could not tell if text was printed or handwritten")
    if m.score < MATCH_MAYBE:
        reasons.append("name not found in the medicine list")
    elif m.score < MATCH_OK:
        reasons.append(f"name only a partial match to '{m.name}' ({m.score}%)")
    if strength_note:
        reasons.append(strength_note)
    if strength is None:
        reasons.append("strength not clearly printed")

    item.reasons = reasons
    if not reasons:
        item.status = "ok"
        item.message = "Read clearly and found in the medicine list."
    return item


def cross_check(items: list[CheckedItem]) -> None:
    """Handwritten items: note whether a printed item in the same photo(s) agrees.

    Agreement is a helpful hint for the pharmacist but never turns a
    handwritten item into "ok".
    """
    printed = {i.generic for i in items if i.source != "handwritten" and i.status == "ok" and i.generic}
    for i in items:
        if i.source == "handwritten" and i.generic:
            if i.generic in printed:
                i.reasons.append(f"agrees with a printed strip/box ({i.generic})")
            else:
                i.reasons.append("no printed strip/box seen that agrees")
