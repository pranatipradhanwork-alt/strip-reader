import pytest


@pytest.fixture(autouse=True)
def private_instance_folder(tmp_path, monkeypatch):
    """Tests never touch the real instance/ folder (accounts, usage log, secret key)."""
    from strip_reader import pharmacists
    monkeypatch.setattr(pharmacists, "DEFAULT_DIR", tmp_path / "instance")
