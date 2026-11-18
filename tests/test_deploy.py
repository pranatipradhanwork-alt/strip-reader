import io
import os
import time

from PIL import Image

from strip_reader.app import create_app, remove_older_than
from strip_reader.pharmacists import Registry
from tests.test_app import fake_reader


def png():
    b = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(b, format="PNG")
    return b.getvalue()


def test_daily_limit_per_visitor(tmp_path, monkeypatch):
    from strip_reader import cli
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    monkeypatch.setenv("DAILY_LIMIT", "2")
    c = create_app(tmp_path / "runs", reader=fake_reader, uses_fn=None, speak_fn=None).test_client()
    c.set_cookie("sr_role", "patient")
    send = lambda: c.post("/read", data={"lang": "en", "photos": (io.BytesIO(png()), "s.png")},
                          content_type="multipart/form-data")
    assert send().status_code == 302 and send().status_code == 302
    r = send()
    assert r.status_code == 429 and "limit of 2 photo readings" in r.data.decode()
    assert c.post("/sample", data={"lang": "en"}).status_code == 302        # the sample needs no AI: never limited


def test_old_readings_and_cache_are_removed(tmp_path):
    runs, cache = tmp_path / "runs", tmp_path / "cache"
    (runs / "old").mkdir(parents=True); (runs / "new").mkdir()
    cache.mkdir(); (cache / "old.json").write_text("{}"); (cache / "new.json").write_text("{}")
    past = time.time() - 30 * 3600
    for p in (runs / "old", cache / "old.json"):
        os.utime(p, (past, past))
    assert remove_older_than(24, [runs, cache, tmp_path / "missing"]) == 2
    assert sorted(x.name for x in runs.iterdir()) == ["new"] and [x.name for x in cache.iterdir()] == ["new.json"]


def test_demo_pharmacist_from_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_PHARMACIST", "DEMO-001|Demo Pharmacist|Demo Council|demo-pass-1")
    reg = Registry(tmp_path / "inst")
    c = create_app(tmp_path / "runs", uses_fn=None, speak_fn=None, registry=reg).test_client()
    rec = reg.get("demo-001")
    assert rec and rec["verified_by"] == "demo account" and rec["ai_check"]["verdict"] == "demo"
    r = c.post("/pharmacist/login", data={"reg_no": "DEMO-001", "password": "demo-pass-1"})
    assert r.headers["Location"] == "/pharmacist/inbox"
    create_app(tmp_path / "runs", uses_fn=None, speak_fn=None, registry=reg)    # restart: not duplicated
    assert len(reg.load()) == 1


def test_wsgi_entry_point(monkeypatch, tmp_path):
    monkeypatch.setenv("STRIP_READER_RUNS", str(tmp_path / "runs"))
    monkeypatch.setattr("strip_reader.app.load_dotenv", lambda *a, **k: None)   # never read the real .env in tests
    from strip_reader.app import wsgi
    assert wsgi().test_client().get("/healthz").status_code in (200, 503)
