import pytest

from strip_reader.app import create_app
from strip_reader.pharmacists import Registry, compare_certificate

CERT = {"is_pharmacist_registration_certificate": True, "name": "Asha Sahoo", "registration_number": "OR 12345",
        "council": "Odisha State Pharmacy Council", "valid_until": "2099-12-31"}


def registry(tmp_path, read=CERT):
    return Registry(tmp_path, reader=lambda path: read)


def apply(reg, **kw):
    a = dict(name="Asha Sahoo", reg_no="OR-12345", council="Odisha State Pharmacy Council",
             password="s3cret-pass", cert_name="cert.jpg", cert_bytes=b"jpeg")
    a.update(kw)
    return reg.apply(**a)


def test_new_application_is_pending_and_cannot_sign_in(tmp_path):
    reg = registry(tmp_path)
    assert apply(reg) == []
    rec = reg.load()["OR-12345"]
    assert rec["status"] == "pending" and rec["ai_check"]["verdict"] == "match"
    assert "s3cret-pass" not in (tmp_path / "pharmacists.json").read_text()      # only a hash is stored
    assert reg.check("OR-12345", "s3cret-pass") == (None, "pending")


def test_approved_can_sign_in_wrong_password_cannot_disabled_stops(tmp_path):
    reg = registry(tmp_path)
    apply(reg)
    with pytest.raises(ValueError):
        reg.set_status("OR-12345", "approved", by="")                            # who checked it must be named
    reg.set_status("or-12345", "approved", by="Admin")
    assert reg.check("OR-12345", "s3cret-pass")[0]["name"] == "Asha Sahoo"
    assert reg.check("OR-12345", "wrong-pass") == (None, "wrong")
    reg.set_status("OR-12345", "disabled")
    assert reg.get("OR-12345") is None and reg.check("OR-12345", "s3cret-pass")[0] is None


def test_application_needs_certificate_and_good_fields(tmp_path):
    errs = apply(registry(tmp_path), name="A", reg_no="??", password="short", cert_name="", cert_bytes=b"")
    assert len(errs) == 4
    reg = registry(tmp_path)
    apply(reg)
    assert any("already applied" in e for e in apply(reg))


@pytest.mark.parametrize("change,problem", [
    ({"registration_number": "OR 99999"}, "registration number"),
    ({"name": "Ravi Kumar"}, "name"),
    ({"council": "Kerala State Pharmacy Council"}, "council"),
    ({"valid_until": "2020-01-01"}, "expired"),
    ({"is_pharmacist_registration_certificate": False}, "does not look like"),
])
def test_ai_check_catches_mismatch_and_blocks_approval(tmp_path, change, problem):
    reg = registry(tmp_path, {**CERT, **change})
    apply(reg)
    check = reg.load()["OR-12345"]["ai_check"]
    assert check["verdict"] == "mismatch" and any(problem in p for p in check["problems"])
    with pytest.raises(ValueError):
        reg.set_status("OR-12345", "approved", by="Admin")


def test_ai_service_down_leaves_it_for_a_person(tmp_path):
    def broken(path):
        raise RuntimeError("down")
    reg = Registry(tmp_path, reader=broken)
    apply(reg)
    assert reg.load()["OR-12345"]["ai_check"]["verdict"] == "not_checked"


def test_compare_ignores_spacing_and_case():
    rec = {"name": "asha  sahoo", "reg_no": "OR-12345", "council": "Odisha Pharmacy Council"}
    assert compare_certificate(CERT, rec, today="2026-11-13")["verdict"] == "match"


def test_sign_in_pages_and_lockout(tmp_path):
    reg = registry(tmp_path / "inst")
    apply(reg)
    reg.set_status("OR-12345", "approved", by="Admin")
    c = create_app(tmp_path / "runs", uses_fn=None, speak_fn=None, registry=reg).test_client()
    assert "Apply as a pharmacist" in c.get("/pharmacist/apply").data.decode()
    for _ in range(5):
        assert c.post("/pharmacist/login", data={"reg_no": "OR-12345", "password": "nope-nope"}).status_code == 401
    assert c.post("/pharmacist/login", data={"reg_no": "OR-12345", "password": "s3cret-pass"}).status_code == 429


def test_sign_in_never_redirects_off_site(tmp_path):
    reg = registry(tmp_path / "inst")
    apply(reg)
    reg.set_status("OR-12345", "approved", by="Admin")
    c = create_app(tmp_path / "runs", uses_fn=None, speak_fn=None, registry=reg).test_client()
    r = c.post("/pharmacist/login", data={"reg_no": "OR-12345", "password": "s3cret-pass", "next": "https://evil.example"})
    assert r.headers["Location"] == "/pharmacist/inbox"


def test_password_rules_match_the_checklist():
    from strip_reader.pharmacists import password_ok
    assert password_ok("pharma123")
    assert not password_ok("abc1")            # too short
    assert not password_ok("abcdefghij")      # no number
    assert not password_ok("1234567890")      # no letter
