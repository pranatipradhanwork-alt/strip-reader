import json
import re
from pathlib import Path

from strip_reader.card import I18N_DIR, LANGS, build_schedule, card_text

ROOT = Path(__file__).resolve().parent.parent
EN = json.loads((I18N_DIR / "en.json").read_text())


def test_every_language_has_every_phrase():
    for lang in LANGS:
        tr = json.loads((I18N_DIR / f"{lang}.json").read_text())
        missing = [k for k in EN if not k.startswith("_") and k not in tr]
        missing += [f"reasons.{k}" for k in EN["reasons"] if k not in tr.get("reasons", {})]
        assert not missing, f"{lang} is missing {missing}"


def test_every_code_in_the_code_has_a_phrase():
    src = "".join(p.read_text() for p in (ROOT / "strip_reader").glob("*.py"))
    codes = set(re.findall(r'\.add\("(\w+)"', src)) | set(re.findall(r'codes\.append\("(\w+)"\)', src))
    codes |= {"photo_blurry", "photo_dark", "photo_cut_off"}  # built as f"photo_{quality}"
    assert codes <= set(EN["reasons"]), codes - set(EN["reasons"])


def test_placeholders_kept():
    for lang in LANGS:
        tr = json.loads((I18N_DIR / f"{lang}.json").read_text())
        for k in ("days", "times_a_day"):
            assert "{n}" in tr[k], (lang, k)


def test_odia_card_shows_odia_reason():
    item = {"matched_name": "Paracetamol", "name_as_read": "Paracetamol", "strength": "650 mg",
            "status": "confirm", "reasons": ["handwritten text"], "codes": ["handwritten"],
            "message": "", "timing_text": "1-0-1", "timing_note": "x", "crop_path": None}
    text = card_text(build_schedule({"images": [{"items": [item]}]}), "or")
    assert "ହାତରେ ଲେଖା" in text and "handwritten" not in text
