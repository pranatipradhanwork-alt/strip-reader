"""Read a photo with Gemini and return raw items (not yet safety-checked)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

from PIL import Image, ImageOps

MAX_SIDE = 1600  # shrink big phone photos: faster upload, same reading quality

PROMPT = """You are reading a photo of a medicine strip, medicine box, or prescription.
List every medicine you can see. For each one return:
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
    "required": ["image_type", "image_quality", "items"],
}


def load_image(path: str | Path) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    return img


def read_image(img: Image.Image, model: str | None = None) -> dict:
    """Send the image to Gemini and return the parsed JSON."""
    from google import genai
    from google.genai import types

    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        raise SystemExit("GEMINI_API_KEY is not set. Add it to the .env file.")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    client = genai.Client()
    resp = client.models.generate_content(
        model=model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SCHEMA,
            temperature=0,
        ),
    )
    return json.loads(resp.text)


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
