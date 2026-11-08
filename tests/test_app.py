import io
import json

import pytest
from PIL import Image

from strip_reader.app import FIX, create_app


def fake_reader(images, run_dir, mock=None):
    reply = json.loads((FIX / "printed_rx_reply.json").read_text())
    from strip_reader.cli import run
    return run(images, run_dir, mock=str(FIX / "printed_rx_reply.json")) if reply else {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from strip_reader import cli
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    app = create_app(tmp_path / "runs", reader=fake_reader, uses_fn=None, speak_fn=None)
    return app.test_client()


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize("lang,word", [("en", "Read my medicines"), ("or", "ମୋ ଔଷଧ ପଢ଼ନ୍ତୁ"), ("hi", "मेरी दवाइयाँ पढ़ें")])
def test_home_in_each_language(client, lang, word):
    r = client.get(f"/?lang={lang}")
    assert r.status_code == 200 and word in r.data.decode()
    assert 'capture="environment"' in r.data.decode() and 'getUserMedia' in r.data.decode()


def test_sample_goes_to_card(client):
    r = client.post("/sample", data={"lang": "or"})
    assert r.status_code == 302 and "/card?lang=or" in r.headers["Location"]
    card = client.get(r.headers["Location"]).data.decode()
    assert "Metformin" in card and "ସକାଳ" in card and "slot-morning" in card


def test_upload_photo_goes_to_card(client):
    r = client.post("/read", data={"lang": "en", "photos": (io.BytesIO(png_bytes()), "strip.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    assert "Your medicines today" in client.get(r.headers["Location"]).data.decode()


def test_no_photo_or_bad_file_is_refused(client):
    assert client.post("/read", data={"lang": "en"}).status_code == 400
    r = client.post("/read", data={"lang": "en", "photos": (io.BytesIO(b"not an image"), "x.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "not a photo" in r.data.decode()


def test_review_and_card_links_work(client):
    loc = client.post("/sample", data={"lang": "en"}).headers["Location"]
    base = loc.split("/card")[0]
    review = client.get(f"{base}/review").data.decode()
    assert "Pharmacist check" in review and "Glimpride" in review   # the unsure handwritten item
    bad = client.post(f"{base}/review", data={"by": "P", f"sample_prescription.png-item6|action": "confirm",
                                              f"sample_prescription.png-item6|name": "Glimepiride",
                                              f"sample_prescription.png-item6|strength": "1",
                                              f"sample_prescription.png-item6|timing_text": "1-0-0"})
    assert bad.status_code == 400


def test_unknown_run_is_404(client):
    assert client.get("/r/../etc/card").status_code == 404
    assert client.get("/r/20260101-deadbeef/card").status_code == 404


def test_not_a_medicine_photo_gets_fixed_reply_fast(tmp_path, monkeypatch):
    """Guardrail: the quick check says 'not medicine' -> no full read, fixed message."""
    from strip_reader import cli
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(cli, "quick_check", lambda img: False)

    def no_full_read(*a, **k):
        raise AssertionError("full read must not run for a non-medicine photo")

    monkeypatch.setattr(cli, "read_image", no_full_read)

    def reader(images, run_dir, mock=None):
        return cli.run(images, run_dir)

    c = create_app(tmp_path / "runs", reader=reader, uses_fn=None, speak_fn=None).test_client()
    r = c.post("/read", data={"lang": "en", "photos": (io.BytesIO(png_bytes()), "screenshot.png")},
               content_type="multipart/form-data")
    page = c.get(r.headers["Location"]).data.decode()
    assert "don&#x27;t look like a medicine" in page or "don't look like a medicine" in page
    assert "slot-morning" in page and "Ask your pharmacist first" not in page


def test_full_read_says_not_medicine_too(tmp_path, monkeypatch):
    """Even if the quick check can't decide, the full read's answer is guarded."""
    from strip_reader import cli
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(cli, "quick_check", lambda img: None)
    monkeypatch.setattr(cli, "read_image", lambda *a, **k: {
        "is_medicine_photo": False, "image_type": "other", "image_quality": "good",
        "items": [{"name_as_printed": "Python", "strength": "3 mg", "source": "printed_box", "confidence": 0.99}]})
    res = cli.run([str(FIX / "fake_strip.png")], tmp_path / "out")
    assert res["images"][0]["not_medicine"] and res["images"][0]["items"] == []


def test_try_again_rereads_saved_photos(tmp_path, monkeypatch):
    from strip_reader import cli
    from strip_reader.vision import OfflineError
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(cli, "QUEUE", tmp_path / "queue")
    monkeypatch.setattr(cli, "quick_check", lambda img: None)
    state = {"busy": True}

    def read(*a, **k):
        if state["busy"]:
            raise OfflineError("busy")
        return json.loads((FIX / "printed_rx_reply.json").read_text())

    monkeypatch.setattr(cli, "read_image", read)
    c = create_app(tmp_path / "runs", reader=lambda imgs, d, mock=None: cli.run(imgs, d),
                   uses_fn=None, speak_fn=None).test_client()
    loc = c.post("/read", data={"lang": "en", "photos": (io.BytesIO(png_bytes()), "rx.png")},
                 content_type="multipart/form-data").headers["Location"]
    page = c.get(loc).data.decode()
    assert "Couldn" in page and "/retry" in page
    state["busy"] = False
    loc2 = c.post(loc.split("/card")[0] + "/retry?lang=en").headers["Location"]
    assert "Metformin" in c.get(loc2).data.decode()


def test_history_lists_only_this_browsers_readings_and_can_delete(tmp_path, monkeypatch):
    from strip_reader import cli
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    app = create_app(tmp_path / "runs", reader=fake_reader, uses_fn=None, speak_fn=None)
    me, other = app.test_client(), app.test_client()
    assert "No readings yet" in me.get("/history?lang=en").data.decode()
    loc = me.post("/sample", data={"lang": "en"}).headers["Location"]
    rid = loc.split("/")[2]
    page = me.get("/history?lang=en").data.decode()
    assert f"/r/{rid}/card" in page and "1 to check" in page
    assert f"/r/{rid}/card" not in other.get("/history?lang=en").data.decode()   # another phone sees nothing
    assert other.post(f"/r/{rid}/delete").status_code == 404                     # and can't delete it
    me.post(f"/r/{rid}/delete?lang=en")
    assert me.get(loc).status_code == 404 and "No readings yet" in me.get("/history?lang=en").data.decode()
