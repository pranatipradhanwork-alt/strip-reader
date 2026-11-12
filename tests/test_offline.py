from PIL import Image

from strip_reader.offline import offline_read
from strip_reader.safety import check_item, load_medicines
from pathlib import Path

MEDS = load_medicines(Path(__file__).resolve().parent.parent / "data" / "medicines.csv")
IMG = Image.new("RGB", (1000, 1000), "white")
BOX = [[100, 100], [400, 100], [400, 150], [100, 150]]


def read(*lines):
    return offline_read(IMG, MEDS, lines=[(BOX, t, s) for t, s in lines])


def test_garbled_strength_is_not_used():
    """Real case: 'Paracetamol IP 650 mg' came out of OCR as '...C50mg'."""
    r = read(("Dolo-650", 0.85), ("PaactemolIPm.C50mg", 0.9))
    item = r["items"][0]
    assert item["name_as_printed"] == "Dolo 650" and item["strength"] is None
    assert check_item(item, MEDS).status == "confirm"


def test_strength_must_agree_with_number_in_name():
    r = read(("Dolo-650", 0.9), ("Each tablet 500 mg", 0.9))
    assert r["items"][0]["strength"] is None


def test_clear_name_and_strength_can_be_ok():
    r = read(("ELECTRAL", 0.9), ("Contents : 21.80 g", 0.9))
    item = r["items"][0]
    assert item["strength"] == "21.80 g" and check_item(item, MEDS).status == "ok"


def test_short_brand_never_matches_inside_a_word():
    r = read(("Amoxycillin capsules", 0.9),)
    assert "Mox" not in [i["name_as_printed"] for i in r["items"]]


def test_two_different_strengths_means_none():
    r = read(("Telma", 0.9), ("40 mg", 0.9), ("80 mg", 0.9))
    assert r["items"][0]["strength"] is None


def test_nothing_readable_returns_no_items():
    assert read(("scribble", 0.7),)["items"] == []
