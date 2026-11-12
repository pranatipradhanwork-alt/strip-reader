"""Offline fallback: read printed strips without any AI service.

Used only when Gemini can't be reached or is overloaded. A small OCR model
(RapidOCR, runs on this server's CPU in under a second) reads the printed text,
then names are looked up in our own medicine list. Nothing is guessed:

- A medicine is found only if every word of a list name appears, in order, in an
  OCR line (so "Mox" can't match inside "Amoxyclav").
- A strength is used only if it is clearly printed (e.g. "200 mg") and it is the
  only strength on the pack; otherwise it stays empty.
- Timing is never read offline.
- Handwriting can't be read this way; such photos end up "nothing readable",
  which sends the person to a pharmacist.
"""

from __future__ import annotations

import re

from PIL import Image

from .safety import MATCH_OK, STRENGTH_RE, Medicine, _clean

_ocr = None
MIN_LINE_SCORE = 0.6
# A strength must stand on its own ("200 mg"), never glued to letters ("C50mg" is garbled OCR).
STRENGTH_IN_TEXT = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*(mg|mcg|ml|g|iu)\b", re.IGNORECASE)
CLEAN_LINE_SCORE = 0.8   # strengths only from lines OCR read confidently


def _engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def _words(text: str) -> list[str]:
    """Normalise OCR text: split letters from digits ("Cefizot200DT" -> cefizot 200 dt)."""
    text = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", text)
    return _clean(text).split()


def _contains(line_words: list[str], name_words: list[str]) -> bool:
    """All words of the medicine name appear consecutively in the line (OCR-tolerant)."""
    from rapidfuzz import fuzz
    n = len(name_words)
    for i in range(len(line_words) - n + 1):
        if all((w.isdigit() and w == lw) or (not w.isdigit() and len(w) >= 4 and fuzz.ratio(w, lw) >= MATCH_OK)
               or (not w.isdigit() and len(w) < 4 and w == lw)
               for w, lw in zip(name_words, line_words[i:i + n])):
            return True
    return False


def ocr_lines(img: Image.Image) -> list[tuple[list, str, float]]:
    import numpy as np
    result, _ = _engine()(np.array(img.convert("RGB")))
    return [(box, text, float(score)) for box, text, score in (result or []) if float(score) >= MIN_LINE_SCORE]


def offline_read(img: Image.Image, medicines: list[Medicine], lines=None) -> dict:
    """Return a reply in the same shape as the Gemini reader, built only from printed text."""
    lines = ocr_lines(img) if lines is None else lines
    w, h = img.size
    names = sorted({(m.name, m.generic, m.kind) for m in medicines}, key=lambda x: -len(x[0]))
    found: dict[str, dict] = {}
    for box, text, score in lines:
        lw = _words(text)
        for name, generic, kind in names:
            nw = _words(name)
            if not nw or not _contains(lw, nw):
                continue
            # Prefer the longest name that matched on this line ("Dolo 650" over "Dolo").
            if any(name.lower() in other.lower() and name != other for other in found):
                continue
            hit = found.setdefault(name, {"generic": generic, "kind": kind, "scores": [], "box": box})
            hit["scores"].append(score)
            break

    strengths = {f"{v} {u.lower()}" for _, text, score in lines if score >= CLEAN_LINE_SCORE
                 for v, u in STRENGTH_IN_TEXT.findall(text)}
    strength = next(iter(strengths)) if len(strengths) == 1 else None

    # A brand and its generic on the same pack are one medicine: keep the brand.
    items = []
    generics_with_brand = {v["generic"] for k, v in found.items() if v["kind"] == "brand"}
    for name, v in found.items():
        if v["kind"] == "generic" and name in generics_with_brand:
            continue
        xs = [p[0] for p in v["box"]]
        ys = [p[1] for p in v["box"]]
        conf = min(0.95, sum(v["scores"]) / len(v["scores"]) + 0.05 * (len(v["scores"]) - 1))
        item_strength = strength if strength and STRENGTH_RE.match(strength) else None
        name_numbers = re.findall(r"\d+(?:\.\d+)?", name)
        if item_strength and name_numbers and item_strength.split()[0] not in name_numbers:
            item_strength = None   # "Dolo 650" with "50 mg" read somewhere: they disagree, so trust neither
        items.append({
            "name_as_printed": name,
            "generic_as_printed": None,
            "strength": item_strength,
            "timing_text": None,
            "source": "printed_strip",
            "confidence": round(conf, 2),
            "box_2d": [int(min(ys) / h * 1000), int(min(xs) / w * 1000), int(max(ys) / h * 1000), int(max(xs) / w * 1000)],
        })
    return {"is_medicine_photo": True, "image_type": "strip" if items else "other",
            "image_quality": "good", "items": items, "_offline": True}
