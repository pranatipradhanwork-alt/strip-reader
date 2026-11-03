"""Read the medicine card aloud in the person's language.

Two separate steps, on purpose:
1. Script: the words to speak are built by code from the card and the
   reviewed language files. No AI writes or rewords them, so the voice can't
   say a dose or timing that isn't on the card.
2. Speech: a text-to-speech model only reads that script aloud. The audio is
   saved as a small MP3 (~4 KB per second) and cached, so the same card is
   never generated twice.

If speech fails (no network, quota), the card still works; there is just no audio.
"""

from __future__ import annotations

import hashlib
import io
import os
import wave
from pathlib import Path

from .card import SLOT_ORDER, _with_strength, labels

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "out" / "cache"
TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts")
LANG_NAMES = {"en": "English", "hi": "Hindi", "or": "Odia"}
MP3_KBPS = 32


# ---------- 1. script (deterministic) ----------

def _amount(v, L: dict) -> str:
    if v is None:
        return ""
    if v == 0.5:
        return L["half"]
    return L["take_n"].format(n=f"{v:g}")


def _say_item(e: dict, L: dict, said: set) -> str:
    t = e.get("timing") or {}
    bits = [_with_strength(e["name"], e["strength"])]
    if e.get("amount"):
        bits.append(_amount(e["amount"], L))
    if t.get("reason"):
        bits.append(t["reason"])
    if t.get("food"):
        bits.append(L[t["food"]])
    if t.get("days"):
        bits.append(L["days"].format(n=t["days"]))
    line = ", ".join(b for b in bits if b) + "."
    if e.get("uses") and e["name"] not in said:   # say "used for" once per medicine
        said.add(e["name"])
        line += f" {L['used_for']} {e['uses']['text']}"
    return line


def speech_script(sched: dict, lang: str = "en") -> str:
    """The exact words to speak, built only from the card."""
    L = labels(lang)
    parts, said = [L["title"] + "."], set()
    if sched.get("queued"):
        parts.append(L["queued"].format(n=len(sched["queued"])))
    for key in (*SLOT_ORDER, "as_needed"):
        items = sched.get(key) or []
        if not items:
            continue
        parts.append(f"{L[key]}: " + " ".join(_say_item(e, L, said) for e in items))
    if sched.get("ask"):
        names = ", ".join(_with_strength(e["name"], e["strength"]) for e in sched["ask"])
        parts.append(f"{L['ask']}: {names}. {L['confirm']}")
    parts.append(L["footer"])
    return "\n".join(parts)


# ---------- 2. speech ----------

def _pcm_from(data: bytes) -> tuple[bytes, int]:
    """Gemini returns either a WAV file or raw 16-bit PCM at 24 kHz."""
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data)) as w:
            return w.readframes(w.getnframes()), w.getframerate()
    return data, 24000


def to_mp3(pcm: bytes, rate: int) -> bytes:
    import lameenc

    enc = lameenc.Encoder()
    enc.set_bit_rate(MP3_KBPS)
    enc.set_in_sample_rate(rate)
    enc.set_channels(1)
    enc.set_quality(5)
    return enc.encode(pcm) + enc.flush()


def _tts(script: str, lang: str) -> bytes:
    from google import genai
    from google.genai import types

    client = genai.Client()
    prompt = (f"Read the following text aloud exactly as written, slowly and clearly, in "
              f"{LANG_NAMES.get(lang, 'English')}, for an elderly listener. Do not add, skip or "
              f"change any word or number.\n\n{script}")
    r = client.models.generate_content(
        model=TTS_MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_modalities=["AUDIO"]))
    return r.candidates[0].content.parts[0].inline_data.data


def speak(sched: dict, lang: str, out_path: Path, tts_fn=None) -> Path | None:
    """Write the card as MP3 to out_path. Returns the path, or None if speech failed."""
    script = speech_script(sched, lang)
    key = hashlib.sha256(f"{TTS_MODEL}|{lang}|{script}".encode()).hexdigest()[:16]
    cache = CACHE / f"voice-{key}.mp3"
    try:
        if not cache.exists():
            pcm, rate = _pcm_from((tts_fn or _tts)(script, lang))
            CACHE.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(to_mp3(pcm, rate))
        out_path.write_bytes(cache.read_bytes())
        return out_path
    except Exception as e:  # offline, quota: the card still works without audio
        print(f"  (no voice: {type(e).__name__})", flush=True)
        return None
