"""Verified pharmacist accounts.

Only a verified pharmacist can confirm an unsure reading. Nobody is trusted
just because they type "I am a pharmacist":

1. A pharmacist applies on /pharmacist/apply with their name, State Pharmacy
   Council registration number, the council, and a photo/PDF of their
   Registration Certificate. They choose a password.
2. AI first check: Gemini reads the certificate (name, registration number,
   council, valid-till date) and compares it with what was typed. The result is
   saved with the application ("match", "mismatch" or "not_checked").
   AI can read a certificate but cannot tell a real one from an edited copy,
   so it never approves anyone by itself.
3. The account stays "pending". An admin checks the number against the
   council's register and approves it (refused if the AI check found a mismatch):
       python -m strip_reader.pharmacists list
       python -m strip_reader.pharmacists approve OR-12345 --by "Admin name"
   (India has no public API for these registers, so this check is done by a person.)
4. Only an approved, active pharmacist can log in. Every confirmation carries
   their name and registration number, and an admin can disable an account at once:
       python -m strip_reader.pharmacists disable OR-12345

Accounts and certificates live in instance/ (never committed to git).
Passwords are stored only as salted hashes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = Path(os.getenv("STRIP_READER_INSTANCE", ROOT / "instance"))
REG_RE = re.compile(r"^[A-Z0-9][A-Z0-9/\- ]{2,24}$")
MIN_PASSWORD = 8
CERT_EXTS = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic")
MAX_CERT_BYTES = 10 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm_reg(reg: str) -> str:
    """Registration numbers are compared without case or extra spaces."""
    return re.sub(r"\s+", " ", (reg or "").strip().upper())


class Registry:
    """The pharmacist accounts file (instance/pharmacists.json) and certificate folder.

    `reader` reads a certificate file into a dict (default: Gemini); tests pass a fake one.
    """

    def __init__(self, folder: str | Path = DEFAULT_DIR, reader=None):
        self.reader = reader or read_certificate
        self.dir = Path(folder)
        self.file = self.dir / "pharmacists.json"
        self.certs = self.dir / "pharmacist_certs"

    def load(self) -> dict:
        return json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else {}

    def _save(self, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.file)

    def apply(self, name: str, reg_no: str, council: str, password: str,
              cert_name: str, cert_bytes: bytes) -> list[str]:
        """Save an application (status "pending"). Returns a list of problems; empty means saved."""
        name, reg, council = (name or "").strip(), norm_reg(reg_no), (council or "").strip()
        ext = Path(cert_name or "").suffix.lower()
        errors = []
        if len(name) < 3:
            errors.append("Write your full name as on the certificate.")
        if not REG_RE.match(reg) or not re.search(r"\d", reg):
            errors.append("Write your registration number as on the certificate.")
        if len(council) < 3:
            errors.append("Write the Pharmacy Council that registered you.")
        if len(password or "") < MIN_PASSWORD:
            errors.append(f"Choose a password of at least {MIN_PASSWORD} characters.")
        if ext not in CERT_EXTS or not cert_bytes:
            errors.append("Upload your Registration Certificate (photo or PDF).")
        elif len(cert_bytes) > MAX_CERT_BYTES:
            errors.append("The certificate file is too large (10 MB at most).")
        data = self.load()
        if reg in data and data[reg]["status"] != "rejected":
            errors.append("This registration number has already applied.")
        if errors:
            return errors
        self.certs.mkdir(parents=True, exist_ok=True)
        cert_file = re.sub(r"[^A-Z0-9]", "_", reg) + ext
        (self.certs / cert_file).write_bytes(cert_bytes)
        data[reg] = {"name": name, "reg_no": reg, "council": council,
                     "pw_hash": generate_password_hash(password), "certificate": cert_file,
                     "status": "pending", "applied_at": _now()}
        data[reg]["ai_check"] = self._ai_check(data[reg])
        self._save(data)
        return []

    def _ai_check(self, rec: dict) -> dict:
        try:
            return compare_certificate(self.reader(self.certs / rec["certificate"]), rec)
        except Exception:   # service down: the admin can run `recheck` later
            return {"verdict": "not_checked", "problems": ["AI check could not run"], "at": _now()}

    def recheck(self, reg_no: str) -> dict:
        data = self.load()
        rec = data[norm_reg(reg_no)]
        rec["ai_check"] = self._ai_check(rec)
        self._save(data)
        return rec

    def set_status(self, reg_no: str, status: str, by: str = "") -> dict:
        data = self.load()
        rec = data.get(norm_reg(reg_no))
        if not rec:
            raise KeyError(f"no application for {reg_no}")
        if status == "approved" and rec.get("ai_check", {}).get("verdict") == "mismatch":
            raise ValueError("the AI check found a mismatch: " + "; ".join(rec["ai_check"]["problems"]))
        if status == "approved" and not by.strip():
            raise ValueError("say who checked the certificate against the council register")
        rec["status"] = status
        if status == "approved":
            rec["verified_by"], rec["verified_at"] = by, _now()
        else:
            rec[f"{status}_at"] = _now()
        self._save(data)
        return rec

    def get(self, reg_no: str) -> dict | None:
        """The account only if it is approved (a disabled account stops working at once)."""
        rec = self.load().get(norm_reg(reg_no))
        return rec if rec and rec["status"] == "approved" else None

    def check(self, reg_no: str, password: str) -> tuple[dict | None, str]:
        """Log-in check. Returns (account, problem). problem is "", "pending" or "wrong"."""
        rec = self.load().get(norm_reg(reg_no))
        if not rec or not check_password_hash(rec["pw_hash"], password or ""):
            return None, "wrong"
        if rec["status"] == "pending":
            return None, "pending"
        if rec["status"] != "approved":
            return None, "wrong"
        return rec, ""


COUNCIL_WORDS = {"state", "pharmacy", "pharmaceutical", "council", "of", "the", "board", "registration"}
CERT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_pharmacist_registration_certificate": {"type": "boolean"},
        "name": {"type": "string", "nullable": True},
        "registration_number": {"type": "string", "nullable": True},
        "council": {"type": "string", "nullable": True},
        "valid_until": {"type": "string", "nullable": True, "description": "YYYY-MM-DD if printed"},
    },
    "required": ["is_pharmacist_registration_certificate"],
}
CERT_PROMPT = ("Is this a pharmacist's registration certificate (or renewal card) issued by an Indian State "
               "Pharmacy Council or the Pharmacy Council of India? Copy, exactly as printed: the pharmacist's "
               "name, the registration number, the council's name, and the valid-until / renewal date "
               "(as YYYY-MM-DD). Use null for anything not printed. Never guess.")


def cert_image(path: Path):
    """First page of a certificate as an image (PDF, HEIC or photo)."""
    from PIL import Image, ImageOps
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        return pdfium.PdfDocument(str(path))[0].render(scale=200 / 72).to_pil().convert("RGB")
    if path.suffix.lower() == ".heic":
        from pillow_heif import register_heif_opener
        register_heif_opener()
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def read_certificate(path: Path) -> dict:
    """Gemini reads the certificate. Raises if the service can't be reached."""
    import io
    from google import genai
    from google.genai import types
    img = cert_image(path)
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    client = genai.Client(http_options=types.HttpOptions(timeout=30_000))
    r = client.models.generate_content(
        model=os.getenv("GEMINI_APP_MODEL", "gemini-3.5-flash-lite"),
        contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), CERT_PROMPT],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0,
                                           response_schema=CERT_SCHEMA))
    return json.loads(r.text)


def compare_certificate(read: dict, rec: dict, today: str | None = None) -> dict:
    """Compare what the AI read on the certificate with what the pharmacist typed."""
    from rapidfuzz import fuzz
    alnum = lambda x: re.sub(r"[^A-Z0-9]", "", (x or "").upper())
    problems = []
    if not read.get("is_pharmacist_registration_certificate"):
        problems.append("the file does not look like a pharmacist registration certificate")
    else:
        if fuzz.token_sort_ratio((read.get("name") or "").lower(), rec["name"].lower()) < 85:
            problems.append(f"name on certificate is '{read.get('name')}'")
        if alnum(read.get("registration_number")) != alnum(rec["reg_no"]):
            problems.append(f"registration number on certificate is '{read.get('registration_number')}'")
        # Compare the distinctive part ("Odisha"), not the shared words ("State Pharmacy Council").
        place = lambda x: " ".join(w for w in re.findall(r"[a-z]+", (x or "").lower()) if w not in COUNCIL_WORDS)
        if read.get("council") and fuzz.token_set_ratio(place(read["council"]), place(rec["council"])) < 80:
            problems.append(f"council on certificate is '{read.get('council')}'")
        until = read.get("valid_until") or ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", until) and until < (today or _now()[:10]):
            problems.append(f"certificate expired on {until}")
    return {"verdict": "mismatch" if problems else "match", "problems": problems, "read": read, "at": _now()}


def signature(rec: dict) -> str:
    """How a pharmacist appears on a confirmation: name and registration number."""
    return f"{rec['name']} (Reg. {rec['reg_no']})"


def main() -> None:
    p = argparse.ArgumentParser(description="Review pharmacist applications.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show all applications")
    a = sub.add_parser("approve", help="approve after checking the certificate against the council register")
    a.add_argument("reg_no")
    a.add_argument("--by", required=True, help="who checked the certificate")
    for cmd in ("reject", "disable", "recheck"):
        sub.add_parser(cmd).add_argument("reg_no")
    args = p.parse_args()
    reg = Registry()
    if args.cmd == "list":
        for r in reg.load().values():
            ai = r.get("ai_check", {})
            print(f"{r['status']:9} {r['reg_no']:15} {r['name']:25} {r['council']:30} "
                  f"AI: {ai.get('verdict', '-')}{' (' + '; '.join(ai['problems']) + ')' if ai.get('problems') else ''}")
            print(f"          certificate: {reg.certs / r['certificate']}")
        return
    if args.cmd == "recheck":
        print(reg.recheck(args.reg_no)["ai_check"])
        return
    status = {"approve": "approved", "reject": "rejected", "disable": "disabled"}[args.cmd]
    try:
        rec = reg.set_status(args.reg_no, status, getattr(args, "by", ""))
    except (KeyError, ValueError) as e:
        raise SystemExit(f"Not changed: {e.args[0]}")
    print(f"{rec['reg_no']} ({rec['name']}) is now {rec['status']}.")


if __name__ == "__main__":
    main()
