import io
import json
import wave

from strip_reader import voice
from strip_reader.card import build_schedule, card_html
from strip_reader.web import create_app


def _sched(**kw):
    item = {"matched_name": "Metformin", "name_as_read": "Metformin", "generic": "Metformin",
            "strength": "500 mg", "status": "ok", "reasons": [], "codes": [], "message": "",
            "source": "printed_prescription", "timing_text": "1-0-1 after food x 5 days",
            "timing_note": None, "crop_path": None}
    item.update(kw)
    return build_schedule({"images": [{"image": "a.jpg", "items": [item]}]})


def test_script_says_only_what_is_on_the_card():
    s = voice.speech_script(_sched(), "en")
    assert "Morning: Metformin 500 mg, take 1, after food, for 5 days." in s
    assert "Night: Metformin 500 mg" in s
    assert "Afternoon" not in s                      # empty slots are not spoken


def test_unsure_item_is_spoken_only_as_ask_pharmacist():
    s = voice.speech_script(_sched(status="confirm", reasons=["handwritten text"], codes=["handwritten"]), "en")
    assert "Ask your pharmacist first: Metformin 500 mg." in s
    assert "take 1" not in s and "Please confirm with a pharmacist or doctor." in s


def test_script_in_odia_uses_reviewed_phrases():
    s = voice.speech_script(_sched(), "or")
    assert "ସକାଳ" in s and "ଖାଇବା ପରେ" in s and "1ଟି ନିଅନ୍ତୁ" in s


def test_half_tablet_spoken():
    s = voice.speech_script(_sched(timing_text="½-0-½"), "en")
    assert "take half" in s


def _fake_wav(*a):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000)            # 1 second of silence
    return buf.getvalue()


def test_speak_writes_small_mp3_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE", tmp_path / "cache")
    calls = []

    def tts(script, lang):
        calls.append(script)
        return _fake_wav()

    out = voice.speak(_sched(), "en", tmp_path / "card.mp3", tts_fn=tts)
    assert out and out.read_bytes()[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or out.read_bytes()[0] == 0xFF
    assert out.stat().st_size < 8000                 # ~4 KB per second at 32 kbps
    voice.speak(_sched(), "en", tmp_path / "again.mp3", tts_fn=tts)
    assert len(calls) == 1


def test_speak_failure_never_breaks(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE", tmp_path / "cache")

    def offline(*a):
        raise ConnectionError("no network")

    assert voice.speak(_sched(), "en", tmp_path / "card.mp3", tts_fn=offline) is None


def test_card_has_player_only_with_audio():
    assert "<audio" in card_html(_sched(), "en", audio="card.mp3")
    assert "<audio" not in card_html(_sched(), "en")


def test_web_read_aloud(tmp_path):
    res = {"images": [{"image": "a.jpg", "items": [{
        "matched_name": "Metformin", "name_as_read": "Metformin", "generic": "Metformin", "strength": "500 mg",
        "status": "ok", "reasons": [], "codes": [], "message": "", "source": "printed_prescription",
        "timing_text": "1-0-1", "timing_note": None, "crop_path": None}]}]}
    (tmp_path / "result.json").write_text(json.dumps(res))

    def fake_speak(sched, lang, path):
        path.write_bytes(b"\xff\xfbfake")
        return path

    c = create_app(tmp_path, uses_fn=None, speak_fn=fake_speak).test_client()
    page = c.get("/card?lang=or&voice=1").data.decode()
    assert "/audio/card-or.mp3" in page
    assert c.get("/audio/card-or.mp3").status_code == 200
    assert c.get("/audio/..%2Fresult.json").status_code == 404


def test_used_for_spoken_once_per_medicine():
    s = _sched()
    for slot in ("morning", "night"):
        for e in s[slot]:
            e["uses"] = {"text": "Controls blood sugar.", "source_url": "x", "source": "FDA"}
    script = voice.speech_script(s, "en")
    assert script.count("Controls blood sugar.") == 1
