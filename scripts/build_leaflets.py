"""Collect real medicine leaflet text for every generic in data/medicines.csv.

Source: openFDA drug labels (US FDA, public domain): https://open.fda.gov/apis/drug/label/
For each generic we keep only:
  uses (indications / purpose), warnings, contraindications, side effects.
The dosage section is NEVER stored, so a dose can't leak into an answer.

Indian generic names that differ in the US are mapped (Paracetamol -> acetaminophen).
Medicines not sold in the US (e.g. Drotaverine, Aceclofenac) get no leaflet;
the app then says "ask your pharmacist" instead of guessing.

Run: python scripts/build_leaflets.py   (about 1 minute, no API key needed)
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "leaflets"
API = "https://api.fda.gov/drug/label.json"

# India name -> US label name (lowercase). Combinations use "and" on US labels.
US_NAME = {
    "paracetamol": "acetaminophen",
    "salbutamol": "albuterol",
    "ferrous sulphate": "ferrous sulfate",
    "sodium valproate": "valproic acid",
    "ursodeoxycholic acid": "ursodiol",
    "amoxicillin + clavulanic acid": "amoxicillin and clavulanate potassium",
    "levodopa + carbidopa": "carbidopa and levodopa",
    "cefuroxime": "cefuroxime axetil",
    "cholecalciferol": "cholecalciferol",
}
KEEP = {  # FDA field -> our section name
    "indications_and_usage": "uses",
    "purpose": "uses",
    "uses": "uses",
    "contraindications": "do_not_take_if",
    "do_not_use": "do_not_take_if",
    "warnings": "warnings",
    "warnings_and_cautions": "warnings",
    "boxed_warning": "warnings",
    "stop_use": "warnings",
    "adverse_reactions": "side_effects",
}
MAX_CHARS = 2500  # per section; leaflets are long and the start is what matters


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fetch(us_name: str) -> dict | None:
    q = f'openfda.generic_name:"{us_name}"'
    for attempt in range(3):
        try:
            r = httpx.get(API, params={"search": q, "limit": 10}, timeout=30)
            break
        except httpx.TransportError:
            time.sleep(3)
    else:
        return None
    if r.status_code == 404:
        return None
    r.raise_for_status()
    # Prefer the label whose generic name is exactly this one (not a combination).
    best = None
    for lab in r.json().get("results", []):
        names = [n.lower() for n in lab.get("openfda", {}).get("generic_name", [])]
        if not any(k in lab for k in ("indications_and_usage", "purpose", "uses")):
            continue
        if us_name in names:
            return lab
        best = best or lab
    return best if best and us_name in " ".join(best.get("openfda", {}).get("generic_name", [])).lower() else None


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS] + ("…" if len(text) > MAX_CHARS else "")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / "data" / "medicines.csv", encoding="utf-8") as f:
        generics = sorted({r["generic"] for r in csv.DictReader(f)})
    found, missing = 0, []
    for g in generics:
        us = US_NAME.get(g.lower(), g.lower().replace(" + ", " and "))
        lab = fetch(us)
        if not lab:
            missing.append(g)
            continue
        sections: dict[str, str] = {}
        for field, name in KEEP.items():
            if field in lab and name not in sections:
                sections[name] = clean(" ".join(lab[field]))
        set_id = lab.get("set_id")
        doc = {
            "generic": g,
            "us_name": us,
            "label_title": (lab.get("openfda", {}).get("brand_name") or [us])[0],
            "source": "US FDA drug label (openFDA)",
            "source_url": f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={set_id}",
            "effective_time": lab.get("effective_time"),
            "sections": sections,
        }
        (OUT / f"{slug(g)}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        found += 1
        time.sleep(0.3)  # be polite to the free API
    (OUT / "_missing.json").write_text(json.dumps(missing, indent=2) + "\n")
    print(f"leaflets: {found} found, {len(missing)} not on US labels")
    print("missing:", ", ".join(missing))


if __name__ == "__main__":
    sys.exit(main())
