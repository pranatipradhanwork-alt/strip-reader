import json
from pathlib import Path

from strip_reader import cli
from strip_reader.card import build_schedule, card_html, card_text
from strip_reader.vision import OfflineError, PROFILES, load_image, prepare

FIX = Path(__file__).parent / "fixtures"


def test_profiles_get_smaller():
    img = load_image(FIX / "printed_rx.png")
    sizes = [len(prepare(img, p)) for p in ("normal", "low", "tiny")]
    assert sizes[0] > sizes[1] > sizes[2]


def test_offline_photo_is_queued_not_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(cli, "QUEUE", tmp_path / "queue")
    calls = []

    def no_network(*a, **k):
        calls.append(1)
        raise OfflineError("no network")

    monkeypatch.setattr(cli, "read_image", no_network)
    photos = [str(FIX / "printed_rx.png"), str(FIX / "fake_strip.png")]
    result = cli.run(photos, tmp_path / "out")
    assert all(i.get("queued") for i in result["images"])
    assert len(calls) == 1                                   # stops trying after the first failure
    assert sorted(p.name for p in (tmp_path / "queue").iterdir()) == ["fake_strip.png", "printed_rx.png"]
    sched = build_schedule(result)
    assert sched["queued"] and "No network" in card_text(sched, "en")
    assert "ନେଟୱାର୍କ" in card_text(sched, "or")


def test_same_photo_is_never_sent_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    reply = json.loads((FIX / "printed_rx_reply.json").read_text())
    calls = []

    def fake_read(*a, **k):
        calls.append(1)
        return reply

    monkeypatch.setattr(cli, "read_image", fake_read)
    cli.run([str(FIX / "printed_rx.png")], tmp_path / "a")
    cli.run([str(FIX / "printed_rx.png")], tmp_path / "b")    # different run folder, same photo
    assert len(calls) == 1


def test_card_without_crops_is_tiny():
    reply = {"images": [{"image": "x.png", "items": [{
        "matched_name": "Metformin", "name_as_read": "Metformin", "strength": "500 mg", "status": "ok",
        "reasons": [], "codes": [], "message": "", "timing_text": "1-0-1", "timing_note": None,
        "crop_path": str(FIX / "fake_strip.png")}]}]}
    s = build_schedule(reply)
    assert len(card_html(s, "or", crops=False)) < len(card_html(s, "or", crops=True))
    assert len(card_text(s, "or").encode()) < 2000            # fits in a couple of SMS/one WhatsApp text


def test_upload_steps_down_to_smaller_photo(monkeypatch):
    """A dropped upload is retried with a smaller photo before giving up."""
    import httpx
    from google import genai
    from strip_reader import vision

    sizes = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            sizes.append(len(contents[0].inline_data.data))
            if len(sizes) < 3:
                raise httpx.ReadTimeout("slow network")
            class R: text = '{"image_type": "strip", "image_quality": "good", "items": []}'
            return R()

    class FakeClient:
        def __init__(self, **k): self.models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setattr(vision.time, "sleep", lambda s: None)
    out = vision.read_image(load_image(FIX / "printed_rx.png"), "m", "normal")
    assert out["_profile"] == "tiny" and sizes[0] > sizes[1] > sizes[2]


def test_gives_up_to_queue_after_smallest(monkeypatch):
    import httpx
    import pytest
    from google import genai
    from strip_reader import vision

    class FakeClient:
        def __init__(self, **k):
            self.models = type("M", (), {"generate_content": lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down"))})()

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setattr(vision.time, "sleep", lambda s: None)
    with pytest.raises(OfflineError):
        vision.read_image(load_image(FIX / "printed_rx.png"), "m", "normal")
