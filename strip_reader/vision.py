"""Read a photo with Gemini and return raw items (not yet safety-checked)."""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

from PIL import Image, ImageOps

MAX_SIDE = 1600  # shrink big phone photos: faster upload, same reading quality

PROMPT = """You are reading a photo that should show a medicine strip, medicine box, or prescription.
First decide: is_medicine_photo = true only if the photo clearly shows a medicine strip, medicine
box/bottle label, or a doctor's prescription. Anything else (a screenshot, a document, a person,
food, a random object, a blank page) -> is_medicine_photo = false, image_type = "other", items = [].
If it is a medicine photo, list every medicine you can see. For each one return:
- name_as_printed: the medicine name exactly as written (brand or generic). Do not correct spelling.
- strength: the strength exactly as written next to that name (e.g. "500 mg"). null if not visible.
- timing_text: dosing/timing words exactly as written for that medicine (e.g. "1-0-1", "after food",
  "twice daily"). null if nothing is written. Never guess or add a usual dose.
- source: "printed_strip", "printed_box", "printed_prescription" or "handwritten".
- confidence: 0.0-1.0, how sure you are that name AND strength are read correctly.
  Use below 0.5 for blurry, cut-off or hard-to-read text.
- box_2d: [ymin, xmin, ymax, xmax] of the region you read the name from, scaled 0-1000.
Rules: copy text only, never invent. If you cannot read a field, use null and lower the confidence.
If the same medicine appears many times (e.g. repeated on a strip), list it once.
Also return image_type ("strip", "box", "prescription", "other") and image_quality ("good", "blurry", "dark", "cut_off")."""

SCHEMA = {
    "type": "object",
    "properties": {
        "is_medicine_photo": {"type": "boolean"},
        "image_type": {"type": "string", "enum": ["strip", "box", "prescription", "other"]},
        "image_quality": {"type": "string", "enum": ["good", "blurry", "dark", "cut_off"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name_as_printed": {"type": "string", "nullable": True},
                    "strength": {"type": "string", "nullable": True},
                    "timing_text": {"type": "string", "nullable": True},
                    "source": {"type": "string", "enum": [
                        "printed_strip", "printed_box", "printed_prescription", "handwritten"]},
                    "confidence": {"type": "number"},
                    "box_2d": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name_as_printed", "source", "confidence"],
            },
        },
    },
    "required": ["is_medicine_photo", "image_type", "image_quality", "items"],
}


# Upload profiles. Box coordinates are 0-1000, so crops are still cut from
# the full photo on the phone even when a small copy is sent.
PROFILES = {
    "normal": {"max_side": 1600, "quality": 85, "gray": False},
    "low":    {"max_side": 1024, "quality": 70, "gray": False},
    "tiny":   {"max_side": 768,  "quality": 60, "gray": True},
}
TIMEOUT_S = 180  # this model can take 2-3 minutes on a photo


class OfflineError(Exception):
    """No network (or it kept failing). The photo should be queued, not lost."""


def load_image(path: str | Path) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    return img


def prepare(img: Image.Image, profile: str = "normal") -> bytes:
    """The bytes actually uploaded for this network profile."""
    p = PROFILES[profile]
    small = img.copy()
    small.thumbnail((p["max_side"], p["max_side"]))
    if p["gray"]:
        small = small.convert("L")
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=p["quality"], optimize=True)
    return buf.getvalue()


def _is_network_error(e: Exception) -> bool:
    import httpx
    return isinstance(e, (httpx.TransportError, ConnectionError, TimeoutError, OSError))


CHECK_MODEL = os.getenv("GEMINI_CHECK_MODEL", "gemini-3.5-flash-lite")
CHECK_TIMEOUT_S = 12


def quick_check(img: Image.Image) -> bool | None:
    """Fast guardrail before the full read: is this a medicine strip, box or prescription?

    Sends a tiny greyscale copy (~15 KB) to the lightest model with a yes/no question.
    Returns False only when the model is sure it is NOT a medicine photo, so the app can
    answer in seconds. Any error or slowness returns None: the full read then decides.
    """
    from google import genai
    from google.genai import types

    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        return None
    small = img.copy()
    small.thumbnail((384, 384))
    buf = io.BytesIO()
    small.convert("L").save(buf, format="JPEG", quality=60)
    try:
        client = genai.Client(http_options=types.HttpOptions(timeout=CHECK_TIMEOUT_S * 1000))
        r = client.models.generate_content(
            model=CHECK_MODEL,
            contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"),
                      "Does this photo show a medicine strip, a medicine box/bottle label, or a doctor's "
                      "prescription? Answer false for screenshots, documents, people, food, objects or "
                      "blank pages."],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0,
                response_schema={"type": "object", "properties": {"is_medicine_photo": {"type": "boolean"}},
                                 "required": ["is_medicine_photo"]}))
        return bool(json.loads(r.text)["is_medicine_photo"])
    except Exception:
        return None


def read_image(img: Image.Image, model: str | None = None, profile: str = "normal",
               budget_s: float | None = None) -> dict:
    """Send the image to Gemini and return the parsed JSON.

    Busy errors (429/500/503/504) are retried with a growing wait. When the
    upload itself fails (slow or dropped network), the next try sends a
    smaller photo: normal -> low -> tiny. Small photos read printed strips
    just as well but handwriting worse, so we only shrink when we must.
    If it still fails, OfflineError is raised so the caller can queue the photo.
    """
    from google import genai
    from google.genai import errors, types

    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        raise SystemExit("GEMINI_API_KEY is not set. Add it to the .env file.")

    steps = list(PROFILES)[list(PROFILES).index(profile):]   # e.g. ["normal", "low", "tiny"]
    data = prepare(img, profile)
    start = time.monotonic()
    timeout = min(TIMEOUT_S, budget_s) if budget_s else TIMEOUT_S
    client = genai.Client(http_options=types.HttpOptions(timeout=int(timeout * 1000)))

    def over_budget(wait: float) -> bool:
        """The web app gives up after budget_s instead of making a person wait minutes."""
        return bool(budget_s) and time.monotonic() - start + wait > budget_s
    model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
    net_failures = 0
    for attempt in range(6):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"), PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=SCHEMA,
                    temperature=0,
                ),
            )
            out = json.loads(resp.text)
            out["_model"] = model
            out["_profile"] = profile
            out["_upload_bytes"] = len(data)
            return out
        except (errors.ServerError, errors.ClientError) as e:
            # Retry only "busy" errors (503 overloaded, 504 timeout, 429 rate limit); others are real bugs.
            daily_limit = "PerDay" in str(e)  # waiting won't help; quota resets tomorrow
            if daily_limit and model != fallback:
                print(f"  Daily free limit reached for {model}; switching to {fallback}.", flush=True)
                model = fallback
                continue
            if getattr(e, "code", None) not in (429, 500, 503, 504) or daily_limit:
                raise
            wait = min(60, 5 * 2 ** attempt)
            if attempt == 5 or over_budget(wait):
                raise OfflineError(f"Gemini still busy ({e.code})") from e
            print(f"  Gemini busy ({e.code}), retrying in {wait}s...", flush=True)
            time.sleep(wait)
        except Exception as e:  # dropped connection, timeout, no network
            if not _is_network_error(e):
                raise
            net_failures += 1
            if net_failures >= len(steps) or over_budget(5 * net_failures):
                raise OfflineError(str(e)) from e
            profile = steps[net_failures]
            data = prepare(img, profile)
            wait = 5 * net_failures
            print(f"  Network problem ({type(e).__name__}); retrying in {wait}s with a smaller photo "
                  f"({profile}, {len(data) // 1024} KB)...", flush=True)
            time.sleep(wait)
    raise OfflineError("gave up after retries")


def crop(img: Image.Image, box_2d, out_path: Path, pad: float = 0.03) -> str | None:
    """Save the region the name was read from, so a human can check it."""
    if not box_2d or len(box_2d) != 4:
        return None
    ymin, xmin, ymax, xmax = [max(0, min(1000, int(v))) for v in box_2d]
    if ymax <= ymin or xmax <= xmin:
        return None
    w, h = img.size
    px, py = pad * w, pad * h
    region = (max(0, xmin * w / 1000 - px), max(0, ymin * h / 1000 - py),
              min(w, xmax * w / 1000 + px), min(h, ymax * h / 1000 + py))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.crop(tuple(int(v) for v in region)).save(out_path)
    return str(out_path)
