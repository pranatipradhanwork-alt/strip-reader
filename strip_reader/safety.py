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

from rapidfuzz import fuzz, utils

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


# Words that describe the form, not the medicine. Removed before matching.
FORM_WORDS = {"tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules", "syp", "syrup",
              "inj", "injection", "ip", "bp", "usp", "sr", "er", "gel", "cream", "drops", "mouth", "wash",
              "orally", "disintegrating", "md", "dt", "hydrochloride", "hcl", "sodium"}


def _clean(text: str) -> str:
    text = re.sub(r"\b([ib])\.\s*p\.?", r"\1p", text, flags=re.IGNORECASE)  # "I.P." -> "ip"
    words = utils.default_process(text).split()
    kept = [w for w in words if w not in FORM_WORDS]
    return " ".join(kept) or " ".join(words)


def match_name(raw: str | None, medicines: list[Medicine]) -> Match:
    """Best list match for a name.

    Uses whole-string similarity plus word-set similarity. It does not use
    "partial" matching, which let a short brand like "Mox" match inside
    "Amoxyclav". On a tie the longer list name wins, so a combination
    ("Rabeprazole + Domperidone") beats one of its parts.
    """
    if not raw or not raw.strip():
        return Match(None, None, 0)
    q = _clean(raw)
    best, best_key = None, (-1, -1)
    for m in medicines:
        c = _clean(m.name)
        # Word-set score only if every word of the list name was read
        # (so "Paracetamol" never matches "Aceclofenac + Paracetamol").
        covered = all(any(fuzz.ratio(w, x) >= 85 for x in q.split()) for w in c.split())
        score = max(fuzz.ratio(q, c), fuzz.token_set_ratio(q, c) if covered and len(c) >= 5 else 0)
        # Extra words in what was read (e.g. "Plus", "LC", "D") often mean a
        # different combination, so cap the score: the item must be confirmed.
        extra = [w for w in q.split() if not w.isdigit()
                 and not any(fuzz.ratio(w, x) >= 85 for x in c.split())]
        if extra:
            score = min(score, MATCH_OK - 5)
        key = (round(score), len(c))
        if key > best_key:
            best, best_key = m, key
    if best is None:
        return Match(None, None, 0)
    return Match(best.name, best.generic, best_key[0])


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
    timing_note: str | None = None
    crop_path: str | None = None
    status: str = "confirm"          # "ok" or "confirm"
    reasons: list[str] = field(default_factory=list)   # English detail, for the pharmacist
    codes: list[str] = field(default_factory=list)     # same reasons as codes, translated on the card
    message: str = CONFIRM_MSG

    def add(self, code: str, text: str) -> None:
        self.reasons.append(text)
        if code not in self.codes:
            self.codes.append(code)

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

    if conf < MIN_CONFIDENCE:
        item.add("low_confidence", f"low reading confidence ({conf:.2f})")
    if source == "handwritten":
        item.add("handwritten", "handwritten text")
    if source == "unknown":
        item.add("source_unknown", "could not tell if text was printed or handwritten")
    if m.score < MATCH_MAYBE:
        item.add("name_not_found", "name not found in the medicine list")
    elif m.score < MATCH_OK:
        item.add("name_partial", f"name only a partial match to '{m.name}' ({m.score}%)")
    if strength_note:
        item.add("strength_missing", strength_note)
    if strength is None:
        item.add("strength_missing", "strength not clearly printed")

    if timing and source != "printed_prescription":
        # Strips and boxes don't carry a personal dose. Text here is a pharmacy
        # sticker, a pen mark, or handwriting, so it is shown but never trusted.
        item.timing_note = "Timing not from a printed prescription. " + CONFIRM_MSG

    if not item.reasons:
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
                i.add("agrees_printed", f"agrees with a printed strip/box ({i.generic})")
            else:
                i.add("no_printed_agrees", "no printed strip/box seen that agrees")


def merge_same_medicine(raws: list[dict], medicines: list[Medicine]) -> list[dict]:
    """Join printed items in one photo that are the same medicine.

    A strip often prints the brand ("Dolo-650") and the generic
    ("Paracetamol 650 mg") separately. They are one medicine, so we keep one
    item: the best-matching name, a strength or timing that is printed on
    either, and the lower of the two confidences (to stay cautious).
    Handwritten items are never merged.
    """
    groups: dict[str, list[dict]] = {}
    out: list[dict] = []
    for r in raws:
        m = match_name(r.get("name_as_printed"), medicines)
        if (r.get("source") or "") == "handwritten" or m.score < MATCH_OK:
            out.append(r)
            continue
        if m.generic not in groups:
            groups[m.generic] = []
            out.append({"_group": m.generic})
        groups[m.generic].append(r)

    merged = []
    for r in out:
        g = r.get("_group")
        if g is None:
            merged.append(r)
            continue
        rs = groups[g]
        if len(rs) == 1:
            merged.append(rs[0])
            continue
        best = max(rs, key=lambda x: match_name(x.get("name_as_printed"), medicines).score)
        new = dict(best)
        for key in ("strength", "timing_text"):
            vals = [x.get(key) for x in rs if x.get(key)]
            new[key] = vals[0] if vals and not new.get(key) else new.get(key)
        new["confidence"] = min(float(x.get("confidence", 0) or 0) for x in rs)
        new["also_printed_as"] = [x.get("name_as_printed") for x in rs if x is not best]
        merged.append(new)
    return merged
