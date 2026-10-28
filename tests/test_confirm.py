import json

import pytest

from strip_reader import confirm
from strip_reader.card import build_schedule, card_text
from strip_reader.web import create_app

ITEM = {"matched_name": None, "name_as_read": "Ciplar-LA", "strength": None, "status": "confirm",
        "reasons": ["handwritten text"], "codes": ["handwritten"], "message": "", "source": "handwritten",
        "confidence": 0.95, "timing_text": "1 x od", "timing_note": "x", "crop_path": None}
RESULT = {"images": [{"image": "samples/raw/img06.jpeg", "items": [ITEM]}]}
IID = "img06.jpeg-item1"


def ok_form(**kw):
    f = {"action": "confirm", "by": "Pharmacist A", "name": "Ciplar-LA",
         "strength": "40 mg", "timing_text": "1-0-0 after food"}
    f.update(kw)
    return f


def test_confirm_needs_name_of_pharmacist():
    rec, errs = confirm.validate(ITEM, ok_form(by=""))
    assert rec is None and errs


def test_confirm_needs_unit_on_strength():
    rec, errs = confirm.validate(ITEM, ok_form(strength="40"))
    assert rec is None and any("unit" in e for e in errs)


def test_confirm_needs_times_not_just_od():
    rec, errs = confirm.validate(ITEM, ok_form(timing_text="1 x od"))
    assert rec is None and any("timing" in e for e in errs)


def test_valid_confirm_records_changes():
    rec, errs = confirm.validate(ITEM, ok_form())
    assert not errs and rec["by"] == "Pharmacist A"
    assert set(rec["changed"]) == {"strength", "timing_text"}


def test_confirmed_item_goes_on_card_with_tick():
    rec, _ = confirm.validate(ITEM, ok_form())
    s = build_schedule(RESULT, {IID: rec})
    assert len(s["morning"]) == 1 and not s["ask"] and s["morning"][0]["confirmed_by"] == "Pharmacist A"
    assert "✓" in card_text(s, "en")


def test_not_medicine_is_removed():
    rec, _ = confirm.validate(ITEM, {"action": "not_medicine", "by": "Pharmacist A"})
    s = build_schedule(RESULT, {IID: rec})
    assert not s["ask"] and not s["morning"]


def test_unconfirmed_stays_in_ask():
    s = build_schedule(RESULT, {})
    assert len(s["ask"]) == 1 and not s["morning"]


@pytest.fixture
def client(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps(RESULT))
    return create_app(tmp_path).test_client(), tmp_path


def test_page_lists_item(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and b"Ciplar-LA" in r.data


def test_bad_entry_saves_nothing(client):
    c, d = client
    r = c.post("/", data={"by": "A", f"{IID}|action": "confirm", f"{IID}|name": "Ciplar-LA",
                          f"{IID}|strength": "40", f"{IID}|timing_text": "1-0-0"})
    assert r.status_code == 400 and not (d / "confirmations.json").exists()


def test_good_entry_saves_and_card_shows_it(client):
    c, d = client
    r = c.post("/", data={"by": "A", f"{IID}|action": "confirm", f"{IID}|name": "Ciplar-LA",
                          f"{IID}|strength": "40 mg", f"{IID}|timing_text": "1-0-0"})
    assert r.status_code == 302
    assert IID in json.loads((d / "confirmations.json").read_text())
    card = c.get("/card?lang=or").data.decode()
    assert "Ciplar-LA" in card and "ସକାଳ" in card


def test_undo(client):
    c, d = client
    c.post("/", data={"by": "A", f"{IID}|action": "not_medicine"})
    c.post(f"/undo/{IID}")
    assert IID not in json.loads((d / "confirmations.json").read_text())


def test_clear_strip_without_timing_is_shown_for_review(tmp_path):
    strip = {**ITEM, "name_as_read": "Dolo-650", "matched_name": "Dolo 650", "strength": "650 mg",
             "status": "ok", "reasons": [], "codes": [], "source": "printed_strip",
             "timing_text": None, "timing_note": None}
    (tmp_path / "result.json").write_text(json.dumps({"images": [{"image": "img11.jpeg", "items": [strip]}]}))
    r = create_app(tmp_path).test_client().get("/")
    assert b"Dolo-650" in r.data
