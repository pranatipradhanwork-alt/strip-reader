import json

import numpy as np
import pytest

from strip_reader import leaflets
from strip_reader.card import build_schedule, card_text


@pytest.fixture
def fake_index(tmp_path, monkeypatch):
    """Two medicines, 3-d vectors: axis 0 = 'uses', axis 1 = 'warnings'."""
    d = tmp_path / "leaflets"
    d.mkdir()
    meta = [
        {"generic": "Paracetamol", "section": "uses", "text": "relieves minor aches and pains and reduces fever"},
        {"generic": "Paracetamol", "section": "warnings", "text": "liver warning"},
        {"generic": "Amlodipine", "section": "uses", "text": "treats high blood pressure"},
    ]
    np.save(d / "index.npy", np.array([[1, 0, 0], [0, 1, 0], [1, 0, 0]], dtype=np.float16))
    (d / "index.json").write_text(json.dumps(meta))
    for g in ("Paracetamol", "Amlodipine"):
        (d / f"{leaflets.slug(g)}.json").write_text(json.dumps(
            {"generic": g, "source": "US FDA drug label (openFDA)", "source_url": "https://example", "sections": {}}))
    monkeypatch.setattr(leaflets, "LEAFLETS", d)
    monkeypatch.setattr(leaflets, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(leaflets, "_index", None)
    return d


def uses_query(texts, task):
    return np.array([[1.0, 0.0, 0.0]], dtype=np.float32)


def test_retrieve_only_from_that_medicine(fake_index):
    got = leaflets.retrieve("Paracetamol", "what for?", k=5, embed_fn=uses_query)
    assert {c["generic"] for c in got} == {"Paracetamol"}
    assert got[0]["section"] == "uses"           # most similar chunk first


def test_unknown_medicine_gets_nothing(fake_index):
    assert leaflets.retrieve("Drotaverine", "what for?", embed_fn=uses_query) == []
    assert leaflets.what_its_for("Drotaverine", embed_fn=uses_query, generate_fn=lambda p: 1 / 0) is None


@pytest.mark.parametrize("text", [
    "Take 500 mg twice a day.", "Use 2 tablets for pain.", "Do not exceed the maximum dose.",
    "Once a day for blood pressure.", "Every 6 hours for fever.", "ଦିନକୁ ୨ ଥର", "दिन में 2 बार",
])
def test_dose_guard_catches_doses(text):
    assert leaflets.has_dose(text)


@pytest.mark.parametrize("text", [
    "Relieves minor aches and pains and lowers fever.", "Used for high blood pressure.",
    "ଜ୍ୱର ଓ ଯନ୍ତ୍ରଣା ପାଇଁ ବ୍ୟବହୃତ ହୁଏ।",
])
def test_dose_guard_allows_plain_uses(text):
    assert not leaflets.has_dose(text)


def test_answer_uses_only_leaflet_and_is_cached(fake_index):
    prompts = []

    def gen(prompt):
        prompts.append(prompt)
        return {"found": True, "answer": "Relieves aches and pains and lowers fever."}

    r = leaflets.what_its_for("Paracetamol", "en", embed_fn=uses_query, generate_fn=gen)
    assert r["text"].startswith("Relieves") and r["source_url"] == "https://example"
    assert "minor aches" in prompts[0] and "high blood pressure" not in prompts[0]
    leaflets.what_its_for("Paracetamol", "en", embed_fn=uses_query, generate_fn=gen)
    assert len(prompts) == 1                      # second time from cache


def test_answer_with_dose_is_blocked(fake_index):
    r = leaflets.what_its_for("Paracetamol", "en", embed_fn=uses_query,
                              generate_fn=lambda p: {"found": True, "answer": "For fever; take 500 mg."})
    assert r is None


def test_failure_never_breaks(fake_index):
    def boom(*a):
        raise ConnectionError("offline")
    assert leaflets.what_its_for("Paracetamol", "en", embed_fn=boom) is None


# ---- card trust rules ----

def _item(**kw):
    i = {"matched_name": "Dolo 650", "name_as_read": "Dolo-650", "generic": "Paracetamol",
         "strength": "650 mg", "status": "ok", "reasons": [], "codes": [], "message": "",
         "source": "printed_strip", "timing_text": None, "timing_note": None, "crop_path": None}
    i.update(kw)
    return {"images": [{"image": "a.jpg", "items": [i]}]}


USES = {"text": "Relieves aches and pains.", "source_url": "https://example", "source": "FDA"}


def test_printed_clear_strip_shows_uses():
    s = build_schedule(_item(), uses_fn=lambda g: USES)
    assert s["ask"][0]["uses"] == USES
    assert "Used for: Relieves" in card_text(s, "en")


def test_handwritten_never_shows_uses():
    s = build_schedule(_item(source="handwritten", status="confirm", reasons=["handwritten text"]),
                       uses_fn=lambda g: USES)
    assert s["ask"][0]["uses"] is None


def test_unsure_printed_name_shows_no_uses():
    s = build_schedule(_item(status="confirm", reasons=["name only partly matches"]), uses_fn=lambda g: USES)
    assert s["ask"][0]["uses"] is None


def test_pharmacist_confirmed_handwritten_shows_uses():
    res = _item(source="handwritten", status="confirm", reasons=["handwritten text"])
    conf = {"a.jpg-item1": {"action": "confirm", "by": "P", "at": "now", "name": "Paracetamol",
                            "strength": "650 mg", "timing_text": "1-0-1"}}
    s = build_schedule(res, conf, uses_fn=lambda g: USES if g == "Paracetamol" else None)
    assert s["morning"][0]["uses"] == USES
