import json

from strip_reader.app import create_app
from strip_reader.monitor import Monitor, summarise
from tests.test_app import approved_registry, fake_reader


def make(tmp_path, monkeypatch, token=None):
    from strip_reader import cli
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    if token:
        monkeypatch.setenv("ADMIN_TOKEN", token)
    else:
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    reg = approved_registry(tmp_path / "inst")
    return create_app(tmp_path / "runs", reader=fake_reader, uses_fn=None, speak_fn=None, registry=reg), reg


def test_health_check(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    app, _ = make(tmp_path, monkeypatch)
    h = app.test_client().get("/healthz").get_json()
    assert h["status"] == "ok" and all(h["checks"].values())
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    h = app.test_client().get("/healthz").get_json()
    assert h["status"] == "degraded" and h["checks"]["ai_key"] is False


def test_reading_is_logged_without_medical_details(tmp_path, monkeypatch):
    app, reg = make(tmp_path, monkeypatch)
    c = app.test_client()
    c.set_cookie("sr_role", "patient")
    c.post("/sample", data={"lang": "en"})
    line = (reg.dir / "metrics.jsonl").read_text().splitlines()[-1]
    rec = json.loads(line)
    assert rec["event"] == "reading" and rec["kind"] == "sample" and rec["photos"] == 1
    assert rec["paths"]["ai"] == 1 and rec["on_card"] + rec["to_pharmacist"] > 0
    for word in ("Glimepiride", "Metformin", "Telma", "sample_prescription", ".png"):   # no names, no file names
        assert word.lower() not in line.lower()


def test_summarise_counts_each_path():
    result = {"images": [{"queued": True, "items": []}, {"offline": True, "items": [{"status": "ok"}]},
                         {"image_type": "other", "items": []},
                         {"image_type": "strip", "items": [{"status": "ok"}, {"status": "confirm"}]}]}
    s = summarise(result)
    assert s["paths"] == {"ai": 1, "offline": 1, "saved": 1, "not_medicine": 1}
    assert s["on_card"] == 2 and s["to_pharmacist"] == 1


def test_stats_add_up_the_day(tmp_path):
    m = Monitor(tmp_path)
    for secs in (2, 4, 30):
        m.reading("upload", secs, {"images": [{"image_type": "strip", "items": [{"status": "ok"}]}]})
    m.reading("upload", 1, error="TimeoutError")
    m.event("chat_message", sender="patient")
    st = m.stats()
    assert st["readings"] == 4 and st["reading_errors"] == 1 and st["seconds_median"] == 4.0   # failed reading not timed
    assert st["on_card"] == 3 and st["events"] == {"chat_message": 1}
    assert m.stats("2000-01-01")["readings"] == 0


def test_admin_dashboard_is_off_without_password_and_locked_with_one(tmp_path, monkeypatch):
    app, _ = make(tmp_path, monkeypatch)
    assert app.test_client().get("/admin/stats").status_code == 404
    app, _ = make(tmp_path / "b", monkeypatch, token="admin-secret-1")
    c = app.test_client()
    assert "Admin password" in c.get("/admin/stats").data.decode()
    assert c.get("/admin/stats.json").status_code == 404
    assert c.post("/admin/stats", data={"token": "nope"}).status_code == 401
    assert c.post("/admin/stats", data={"token": "admin-secret-1"}).status_code == 302
    page = c.get("/admin/stats").data.decode()
    assert "Today at a glance" in page and "Readings" in page
    assert c.get("/admin/stats.json").get_json()["readings"] == 0


def test_timing_ignores_the_sample(tmp_path):
    m = Monitor(tmp_path)
    m.reading("sample", 0.01, {"images": []})
    m.reading("upload", 8.0, {"images": []})
    st = m.stats()
    assert st["samples"] == 1 and st["seconds_median"] == 8.0
