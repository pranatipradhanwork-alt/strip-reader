from pathlib import Path

from strip_reader.safety import CONFIRM_MSG, check_item, cross_check, load_medicines

MEDS = load_medicines(Path(__file__).resolve().parent.parent / "data" / "medicines.csv")


def item(**kw):
    base = {"name_as_printed": "Paracetamol", "strength": "500 mg", "timing_text": None,
            "source": "printed_strip", "confidence": 0.95, "box_2d": [0, 0, 100, 100]}
    base.update(kw)
    return check_item(base, MEDS)


def test_clear_printed_item_is_ok():
    i = item()
    assert i.status == "ok" and i.generic == "Paracetamol"


def test_brand_maps_to_generic():
    i = item(name_as_printed="DOLO 650", strength="650 mg")
    assert i.status == "ok" and i.generic == "Paracetamol"


def test_low_confidence_abstains():
    i = item(confidence=0.4)
    assert i.status == "confirm" and i.message == CONFIRM_MSG


def test_handwriting_always_abstains():
    i = item(source="handwritten", confidence=0.99)
    assert i.status == "confirm"


def test_unknown_name_abstains():
    i = item(name_as_printed="Zorblaxin")
    assert i.status == "confirm" and i.matched_name is None


def test_missing_strength_not_invented():
    i = item(strength=None)
    assert i.strength is None and i.status == "confirm"


def test_garbage_strength_dropped():
    i = item(strength="take two")
    assert i.strength is None and i.status == "confirm"


def test_timing_kept_verbatim():
    i = item(timing_text="1-0-1 after food")
    assert i.timing_text == "1-0-1 after food"


def test_cross_check_notes_agreement_but_stays_confirm():
    printed = item()
    hand = item(source="handwritten", name_as_printed="Paracetmol")
    cross_check([printed, hand])
    assert hand.status == "confirm"
    assert any("agrees with a printed" in r for r in hand.reasons)
