"""Pharmacist confirm step.

The app never turns an unsure reading into a dose by itself. A pharmacist
looks at the label picture and either:
  - confirms it (and may correct the name, strength or timing),
  - marks it "not a medicine" (a toothbrush, a paste), or
  - leaves it for the doctor.
Only confirmed items move onto the card, marked as confirmed, with who
confirmed them and when.

The same safety rules still apply to what the pharmacist types: the strength
needs a unit, and the timing must say when (e.g. 1-0-1 or "before breakfast"),
not only "BD".
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .safety import STRENGTH_RE
from .timing import parse_timing

ACTIONS = ("confirm", "not_medicine", "leave")


def item_id(image_path: str, index: int) -> str:
    """Stable id for an item: photo file name + position (1-based)."""
    return f"{Path(image_path).name}-item{index}"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(path: Path, confirmations: dict) -> None:
    path.write_text(json.dumps(confirmations, indent=2, ensure_ascii=False), encoding="utf-8")


def validate(item: dict, form: dict) -> tuple[dict | None, list[str]]:
    """Check one pharmacist entry. Returns (record, errors).

    `item` is the item from result.json; `form` has action, name, strength,
    timing_text and by (the pharmacist's name).
    """
    action = (form.get("action") or "leave").strip()
    if action not in ACTIONS:
        return None, [f"unknown action '{action}'"]
    if action == "leave":
        return None, []
    by = (form.get("by") or "").strip()
    if not by:
        return None, ["write your name so we know who confirmed it"]
    rec = {"action": action, "by": by, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if action == "not_medicine":
        return rec, []

    errors = []
    name = (form.get("name") or "").strip()
    strength = (form.get("strength") or "").strip()
    timing = (form.get("timing_text") or "").strip()
    if not name:
        errors.append("name is empty")
    if not STRENGTH_RE.match(strength):
        errors.append("strength needs a number and a unit, e.g. 500 mg")
    t = parse_timing(timing)
    if t is None:
        errors.append("timing is empty: write it like 1-0-1, or 'before breakfast'")
    elif not t.sure:
        why = ", ".join(t.unclear) if t.unclear else "times of day not written"
        errors.append(f"timing not clear ({why}): write it like 1-0-1, or 'before breakfast'")
    if errors:
        return None, errors

    before = {"name": item.get("matched_name") or item.get("name_as_read") or "",
              "strength": item.get("strength") or "", "timing_text": item.get("timing_text") or ""}
    after = {"name": name, "strength": strength, "timing_text": timing}
    changed = [f for f in after if after[f] != before[f]]
    rec.update(name=name, strength=strength, timing_text=timing, changed=changed)
    return rec, []
