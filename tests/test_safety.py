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


def test_brand_and_generic_on_same_strip_merge():
    from strip_reader.safety import merge_same_medicine
    raws = [
        {"name_as_printed": "Dolo-650", "strength": None, "source": "printed_strip", "confidence": 0.98},
        {"name_as_printed": "Paracetamol Tablets IP", "strength": "650 mg", "source": "printed_strip", "confidence": 0.95},
    ]
    merged = merge_same_medicine(raws, MEDS)
    assert len(merged) == 1
    i = check_item(merged[0], MEDS)
    assert i.status == "ok" and i.strength == "650 mg" and i.confidence == 0.95


def test_handwritten_never_merged():
    from strip_reader.safety import merge_same_medicine
    raws = [
        {"name_as_printed": "Paracetamol", "strength": "650 mg", "source": "handwritten", "confidence": 0.9},
        {"name_as_printed": "Dolo 650", "strength": "650 mg", "source": "printed_strip", "confidence": 0.9},
    ]
    assert len(merge_same_medicine(raws, MEDS)) == 2


def test_short_brand_does_not_match_inside_longer_name():
    i = item(name_as_printed="Tab Amoxyclav", strength="625 mg")
    assert i.generic == "Amoxicillin + Clavulanic Acid"


def test_combination_beats_single_part():
    i = item(name_as_printed="Rabeprazole & Domperidone (SR) Capsules", strength="20 mg")
    assert i.generic == "Rabeprazole + Domperidone"


def test_timing_on_strip_is_flagged():
    i = item(timing_text="1-0-1")
    assert i.timing_note and "pharmacist" in i.timing_note


def test_timing_on_printed_prescription_not_flagged():
    i = item(timing_text="1-0-1", source="printed_prescription")
    assert i.timing_note is None


def test_suffix_means_confirm():
    i = item(name_as_printed="Montair Plus", strength="10 mg")
    assert i.status == "confirm"
