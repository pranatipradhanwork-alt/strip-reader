"""Turn timing text like "1-0-1 after food x 5 days" into a plain schedule.

Rule: only what is written. If the text says "BD" (twice a day) we know the
count but not the times, so we say so instead of picking morning and night.
Anything we don't understand is listed in `unclear`, and the result is not
"sure".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# Bengali, Devanagari and Odia digits -> ASCII, so "১-০-১" reads as "1-0-1".
_DIGITS = {}
for zero in ("০", "०", "୦"):
    for k in range(10):
        _DIGITS[chr(ord(zero) + k)] = str(k)

SLOTS3 = ("morning", "afternoon", "night")
SLOTS4 = ("morning", "afternoon", "evening", "night")

FREQ = {  # words that give a count per day but NOT which times
    "od": 1, "once": 1, "once daily": 1, "once a day": 1, "qd": 1,
    "bd": 2, "bid": 2, "twice": 2, "twice daily": 2, "twice a day": 2,
    "tds": 3, "tid": 3, "thrice": 3, "thrice daily": 3, "three times a day": 3,
    "qid": 4, "four times a day": 4,
}
NIGHT_WORDS = ("hs", "at bedtime", "bedtime", "at night")
AS_NEEDED = ("sos", "prn", "as needed", "when needed", "if needed")
FOOD = [
    (r"\bafter (food|meals?|breakfast|lunch|dinner)\b|\bp/?c\b|\bpc\b", "after food"),
    (r"\bbefore (food|meals?|breakfast|lunch|dinner)\b|\ba/?c\b|\bac\b|\bempty stomach\b", "before food"),
    (r"\bwith (food|meals?)\b", "with food"),
]
# Text that is fine to see but adds nothing to the schedule.
NOISE = ("as directed by the physician", "as directed", "tab", "tabs", "cap", "x", "for",
         "daily", "a day", "per day", "and", "then")


@dataclass
class Timing:
    text: str
    slots: dict[str, float] | None = None  # e.g. {"morning": 1, "afternoon": 0, "night": 1}
    per_day: float | None = None
    food: str | None = None
    days: int | None = None
    as_needed: bool = False
    reason: str | None = None   # e.g. "fever" in "SOS for fever"
    unclear: list[str] = field(default_factory=list)
    sure: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        parts = []
        if self.as_needed:
            parts.append("only when needed" + (f" ({self.reason})" if self.reason else ""))
        if self.slots:
            parts.append(", ".join(f"{_amt(v)} {k}" for k, v in self.slots.items() if v))
        elif self.per_day:
            parts.append(f"{_amt(self.per_day)} time(s) a day, times of day not written")
        if self.food:
            parts.append(self.food)
        if self.days:
            parts.append(f"for {self.days} days")
        return "; ".join(parts) or "timing not understood"


def _amt(v: float) -> str:
    return {0.5: "½", 0.25: "¼"}.get(v, str(int(v)) if float(v).is_integer() else str(v))


def _num(tok: str) -> float | None:
    tok = tok.strip().replace("½", "1/2").replace("¼", "1/4")
    try:
        if "/" in tok:
            a, b = tok.split("/")
            return float(a) / float(b)
        return float(tok)
    except (ValueError, ZeroDivisionError):
        return None


def parse_timing(text: str | None) -> Timing | None:
    if not text or not text.strip():
        return None
    t = Timing(text=text.strip())
    s = text.translate(str.maketrans(_DIGITS)).lower()
    s = re.sub(r"\s+", " ", s)

    # 1) Slot pattern: 1-0-1, 1-1-1-1, ½-0-½, 1/2-0-1/2
    num = r"(?:\d+(?:\.\d+)?|\d/\d|½|¼)"
    m = re.search(rf"(?<![\w/]){num}(?:\s*-\s*{num}){{2,3}}(?![\w/])", s)
    if m:
        vals = [_num(x) for x in re.split(r"\s*-\s*", m.group(0))]
        if None not in vals and all(v <= 4 for v in vals):
            names = SLOTS3 if len(vals) == 3 else SLOTS4
            t.slots = dict(zip(names, vals))
            t.per_day = sum(1 for v in vals if v)
        else:
            t.unclear.append(m.group(0))
        s = s[:m.start()] + " " + s[m.end():]

    # 2) Duration: "x 5 days", "for 1 week", "1 month"
    m = re.search(r"(\d+)\s*(day|days|d|week|weeks|wk|month|months|mon)\b", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        t.days = n * (7 if unit.startswith("w") else 30 if unit.startswith("mon") else 1)
        s = s[:m.start()] + " " + s[m.end():]

    # 3) Meal named -> that meal's time is written, so it counts as a slot
    meal = re.search(r"\b(before|after|with) (breakfast|lunch|dinner)\b", s)
    meal_slot = {"breakfast": "morning", "lunch": "afternoon", "dinner": "night"}[meal.group(2)] if meal else None

    # 3b) Food
    for pat, label in FOOD:
        if re.search(pat, s):
            t.food = label
            s = re.sub(pat, " ", s)
            break
    if re.search(r"\bb/?f\b", s):  # "BF" can mean "before food" OR "breakfast"
        t.unclear.append("'BF' (before food or breakfast?)")
        s = re.sub(r"\bb/?f\b", " ", s)

    # 4) As needed / night-only / frequency words
    for w in AS_NEEDED:
        if re.search(rf"\b{re.escape(w)}\b", s):
            t.as_needed = True
            s = re.sub(rf"\b{re.escape(w)}\b", " ", s)
    if t.as_needed:
        m = re.search(r"\bfor ([a-z ]+?)\s*$", s.strip())
        if m:
            t.reason = m.group(1).strip()
            s = s[:s.rfind("for " + m.group(1))]
    for w in NIGHT_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", s) and not t.slots:
            t.slots = {"morning": 0, "afternoon": 0, "night": 1}
            t.per_day = 1
            s = re.sub(rf"\b{re.escape(w)}\b", " ", s)
    for w in sorted(FREQ, key=len, reverse=True):
        if re.search(rf"\b{re.escape(w)}\b", s):
            if t.per_day is None:
                t.per_day = FREQ[w]
            elif t.per_day != FREQ[w]:
                t.unclear.append(f"'{w}' disagrees with {t.text!r}")
            s = re.sub(rf"\b{re.escape(w)}\b", " ", s)

    if meal_slot and not t.slots and t.per_day in (None, 1):
        t.slots = {k: (1 if k == meal_slot else 0) for k in SLOTS3}
        t.per_day = 1

    # 5) Whatever is left and isn't noise is unclear
    for w in NOISE:
        s = re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", " ", s)
    leftover = re.sub(r"[^\w½¼]+", " ", s).split()
    leftover = [w for w in leftover if not re.fullmatch(r"\d+", w) or len(w) > 1]
    if leftover:
        t.unclear.append(" ".join(leftover))

    # Sure = explicit slots (or as-needed only), nothing unclear.
    t.sure = not t.unclear and (t.slots is not None or (t.as_needed and t.per_day is None))
    return t
