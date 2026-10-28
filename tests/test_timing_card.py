from strip_reader.card import build_schedule, card_text
from strip_reader.timing import parse_timing


def test_slots_written_out():
    t = parse_timing("1-0-1 after food x 5 days")
    assert t.sure and t.slots == {"morning": 1, "afternoon": 0, "night": 1}
    assert t.food == "after food" and t.days == 5


def test_bengali_digits():
    assert parse_timing("১-০-১").slots == {"morning": 1, "afternoon": 0, "night": 1}


def test_half_tablet():
    assert parse_timing("½-0-½").slots["morning"] == 0.5


def test_bd_gives_count_not_times():
    t = parse_timing("BD after meals")
    assert t.per_day == 2 and t.slots is None and not t.sure


def test_bf_is_ambiguous():
    t = parse_timing("BF 1-0-1")
    assert not t.sure and t.unclear


def test_meal_named_gives_slot():
    t = parse_timing("OD before breakfast")
    assert t.sure and t.slots["morning"] == 1


def test_sos_reason():
    t = parse_timing("SOS for fever")
    assert t.sure and t.as_needed and t.reason == "fever"


def test_unknown_words_not_sure():
    assert not parse_timing("apply").sure
    assert not parse_timing("As directed by the Physician").sure


def _result(**kw):
    item = {"matched_name": "Metformin", "name_as_read": "Metformin", "strength": "500 mg",
            "status": "ok", "reasons": [], "message": "", "timing_text": "1-0-1",
            "timing_note": None, "crop_path": None}
    item.update(kw)
    return {"images": [{"items": [item]}]}


def test_clear_item_goes_into_slots():
    s = build_schedule(_result())
    assert len(s["morning"]) == 1 and len(s["night"]) == 1 and not s["ask"]


def test_confirm_item_never_in_slots():
    s = build_schedule(_result(status="confirm", reasons=["handwritten text"]))
    assert not s["morning"] and not s["night"] and len(s["ask"]) == 1


def test_strip_timing_never_in_slots():
    s = build_schedule(_result(timing_note="Timing not from a printed prescription."))
    assert not s["morning"] and len(s["ask"]) == 1


def test_no_timing_goes_to_ask():
    s = build_schedule(_result(timing_text=None))
    assert len(s["ask"]) == 1


def test_card_languages_render():
    for lang in ("en", "hi", "or"):
        assert "Metformin" in card_text(build_schedule(_result()), lang)
