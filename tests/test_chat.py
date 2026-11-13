from strip_reader.app import create_app
from tests.test_app import approved_registry, fake_reader


def make(tmp_path, monkeypatch):
    from strip_reader import cli
    monkeypatch.setattr(cli, "CACHE", tmp_path / "cache")
    return create_app(tmp_path / "runs", reader=fake_reader, uses_fn=None, speak_fn=None,
                      registry=approved_registry(tmp_path / "inst"))


def test_patient_asks_pharmacist_replies_patient_sees_it(tmp_path, monkeypatch):
    app = make(tmp_path, monkeypatch)
    patient, pharmacist, stranger = app.test_client(), app.test_client(), app.test_client()
    base = patient.post("/sample", data={"lang": "en"}).headers["Location"].split("/card")[0]

    card = patient.get(f"{base}/card?lang=en").data.decode()
    assert "Ask a pharmacist" in card
    page = patient.get(f"{base}/chat?lang=en").data.decode()
    assert "What the pharmacist sees" in page and "Glimepiride" in page and "tel:108" in page

    assert patient.post(f"{base}/chat?lang=en", data={"text": "  "}).status_code == 400
    assert patient.post(f"{base}/chat?lang=en", data={"text": "Is this right?"}).status_code == 302
    assert stranger.get(f"{base}/chat").status_code == 404                   # not their reading
    assert stranger.post(f"{base}/chat", data={"text": "hi"}).status_code == 404
    assert "/pharmacist/login" in pharmacist.get("/pharmacist/inbox").headers["Location"]

    pharmacist.post("/pharmacist/login", data={"reg_no": "OR-12345", "password": "s3cret-pass"})
    inbox = pharmacist.get("/pharmacist/inbox").data.decode()
    assert "Waiting" in inbox and "Is this right?" in inbox
    assert pharmacist.post(f"{base}/chat", data={"text": "Yes, take it after breakfast. <b>ok</b>"}).status_code == 302

    msgs = patient.get(f"{base}/chat/messages?lang=en").get_json()
    assert msgs["status"] == "answered" and msgs["messages"][-1]["who"] == "Pharmacist Asha Sahoo (Reg. OR-12345)"
    assert msgs["messages"][0]["who"] == "You" and msgs["messages"][0]["mine"]
    page = patient.get(f"{base}/chat?lang=en").data.decode()
    assert "&lt;b&gt;ok&lt;/b&gt;" in page and "<b>ok</b>" not in page              # replies are shown as text
    assert "A pharmacist replied" in patient.get(f"{base}/card?lang=en").data.decode()
    assert "A pharmacist replied" in patient.get("/history?lang=en").data.decode()
    assert "Answered" in pharmacist.get("/pharmacist/inbox").data.decode()


def test_chat_text_rules(tmp_path):
    from strip_reader import chat
    assert chat.add(tmp_path, "patient", "") == "empty"
    assert chat.add(tmp_path, "patient", "x" * 1001) == "too_long"
    for _ in range(chat.MAX_PATIENT_MESSAGES):
        assert chat.add(tmp_path, "patient", "hello") is None
    assert chat.add(tmp_path, "patient", "one more") == "too_many"
    assert chat.status(chat.load(tmp_path)) == "waiting"
