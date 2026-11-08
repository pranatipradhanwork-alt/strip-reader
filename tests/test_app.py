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
    assert 'capture="environment"' in r.data.decode()


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
