"""Smoke test: walk a patient's journey against a running Strip Reader and report pass/fail.

    python scripts/smoke_test.py http://localhost:5050
    python scripts/smoke_test.py https://<your-space>.hf.space
    python scripts/smoke_test.py http://localhost:5050 --photo strip.jpg     # also one real reading (uses AI quota)

Checks: health, welcome screen, choosing "Read my medicines", the sample prescription card,
history, pharmacist sign-in page (and that the check page is locked), and optionally one
real photo. Uses no medical data of its own: the sample is the built-in printed test prescription.
Exit code 0 = all passed, 1 = something failed.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

HEADERS = {"ngrok-skip-browser-warning": "1", "User-Agent": "strip-reader-smoke-test"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", help="address of the app, e.g. http://localhost:5050")
    ap.add_argument("--photo", help="optional photo to read for real (uses one AI request)")
    ap.add_argument("--timeout", type=float, default=90)
    a = ap.parse_args()
    base = a.base.rstrip("/")
    c = httpx.Client(base_url=base, headers=HEADERS, timeout=a.timeout, follow_redirects=False)
    results = []

    def step(name, fn):
        t0 = time.time()
        try:
            detail = fn() or ""
            ok = True
        except AssertionError as e:
            ok, detail = False, str(e) or "check failed"
        except httpx.HTTPError as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name:<38} {time.time() - t0:6.2f}s  {detail}")
        return ok

    def get(path, code=200):
        r = c.get(path)
        assert r.status_code == code, f"{path} returned {r.status_code}, expected {code}"
        return r

    def check_health():
        h = get("/healthz").json()
        assert h["status"] in ("ok", "degraded"), f"status {h['status']}"
        off = [k for k, v in h["checks"].items() if not v]
        return h["status"] + (f" (not ready: {', '.join(off)})" if off else "")

    def check_welcome():
        assert "/start/patient" in get("/?lang=en").text, "welcome choices missing"

    def choose_patient():
        r = get("/start/patient?lang=en", 302)
        assert "Read my medicines" in get(r.headers["location"]).text, "reader page missing"

    card_url = {}

    def sample_card():
        r = c.post("/sample", data={"lang": "en"})
        assert r.status_code == 302, f"sample returned {r.status_code}"
        card_url["u"] = r.headers["location"]
        page = get(card_url["u"]).text
        assert "Your medicines today" in page and "tel:108" in page, "card content missing"
        return card_url["u"].split("/r/")[1].split("/")[0]

    def history():
        assert card_url.get("u") and card_url["u"].split("/r/")[1].split("/")[0] in get("/history?lang=en").text, \
            "reading not in history"

    def pharmacist_locked():
        assert "Pharmacist sign in" in get("/pharmacist/login").text, "sign-in page missing"
        if card_url.get("u"):
            review = card_url["u"].split("/card")[0] + "/review"
            r = get(review, 302)
            assert "/pharmacist/login" in r.headers["location"], "check page is not locked"

    def real_photo():
        with open(a.photo, "rb") as f:
            r = c.post("/read", data={"lang": "en"}, files={"photos": (a.photo.split("/")[-1], f)})
        assert r.status_code == 302, f"read returned {r.status_code}"
        page = get(r.headers["location"]).text
        assert "Your medicines today" in page, "no card after reading"

    print(f"Smoke test: {base}")
    step("health check", check_health)
    step("welcome screen", check_welcome)
    step("choose Read my medicines", choose_patient)
    step("sample prescription -> card", sample_card)
    step("reading appears in history", history)
    step("pharmacist pages locked", pharmacist_locked)
    if a.photo:
        step("read a real photo", real_photo)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
