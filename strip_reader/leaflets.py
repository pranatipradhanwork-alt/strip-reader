"""Leaflet RAG: "what is this medicine for?" from real leaflet text.

1. Index  (scripts/build_index.py, once): split each leaflet section into
   small chunks, embed them with Gemini, save vectors + metadata in data/leaflets/.
2. Retrieve: FIRST filter to the chunks of this one medicine (the name was
   already matched exactly against the medicine list), THEN rank those chunks
   by vector similarity to the question. So an answer can never come from
   another medicine's leaflet.
3. Answer: Gemini writes one or two plain sentences using ONLY those chunks.
4. Guard: if the answer mentions any dose, amount or how often to take it,
   it is thrown away. Doses only come from the prescription and the pharmacist.
   (The leaflet's dosage section is never even stored.)

At ~600 chunks a plain numpy cosine search is instant; a vector database
server (Chroma, FAISS) would add setup without adding speed at this size.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
LEAFLETS = ROOT / "data" / "leaflets"
CACHE = ROOT / "out" / "cache"
EMBED_MODEL = "gemini-embedding-001"
DIM = 768
CHUNK_CHARS = 600
USES_QUESTION = "What is this medicine used for? What conditions does it treat?"

LANG_NAMES = {"en": "English", "hi": "Hindi", "or": "Odia"}

# Anything that looks like a dose, amount or schedule. Checked on the answer.
_DIGITS = str.maketrans({chr(z + k): str(k) for z in (0x09E6, 0x0966, 0x0B66) for k in range(10)})
DOSE_RE = re.compile(
    r"\d\s*(mg|mcg|µg|g\b|ml|iu|units?|%|tablets?|tabs?|capsules?|puffs?|drops?)"
    r"|\b(once|twice|thrice|\d+ times?)\b.{0,15}\b(a|per|each) (day|daily|week)"
    r"|\bevery \d+\s*(hours?|hrs?)\b|\b(dose|dosage|maximum|max)\b"
    r"|\d+\s*(बार|ଥର)",
    re.IGNORECASE,
)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ---------- 1. index ----------

def chunk_leaflets() -> list[dict]:
    chunks = []
    for f in sorted(LEAFLETS.glob("*.json")):
        if f.name.startswith("_") or f.name == "index.json":
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        for section, text in doc["sections"].items():
            words, cur = text.split(), []
            for w in words:
                cur.append(w)
                if sum(len(x) + 1 for x in cur) >= CHUNK_CHARS:
                    chunks.append({"generic": doc["generic"], "section": section, "text": " ".join(cur)})
                    cur = cur[-15:]  # small overlap so a sentence isn't cut in half
            if len(cur) > 15 or not chunks or chunks[-1]["generic"] != doc["generic"]:
                chunks.append({"generic": doc["generic"], "section": section, "text": " ".join(cur)})
    # Keep every "uses" and "do not take" chunk, but only the first chunk of the
    # long warnings / side-effects sections: enough for "what is it for" and simple
    # questions, and it fits the free embedding limit.
    kept, seen = [], set()
    for c in chunks:
        key = (c["generic"], c["section"])
        if c["section"] in ("warnings", "side_effects") and key in seen:
            continue
        seen.add(key)
        kept.append(c)
    return kept


def embed(texts: list[str], task: str) -> np.ndarray:
    from google import genai
    from google.genai import errors, types

    client = genai.Client()
    out = []
    for i in range(0, len(texts), 90):  # free tier: 100 texts per minute
        if i:
            time.sleep(62)
        for attempt in range(5):
            try:
                r = client.models.embed_content(
                    model=EMBED_MODEL, contents=texts[i:i + 90],
                    config=types.EmbedContentConfig(task_type=task, output_dimensionality=DIM))
                break
            except errors.ClientError as e:
                # Per-minute limit: wait it out. Per-day limit: stop (resets tomorrow).
                if e.code != 429 or "PerDay" in str(e) or attempt == 4:
                    raise
                print(f"  embedding limit, waiting 65s ({i}/{len(texts)} done)...", flush=True)
                time.sleep(65)
        out += [e.values for e in r.embeddings]
    v = np.array(out, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def build_index() -> int:
    chunks = chunk_leaflets()
    vecs = embed([f"{c['generic']} ({c['section']}): {c['text']}" for c in chunks], "RETRIEVAL_DOCUMENT")
    np.save(LEAFLETS / "index.npy", vecs.astype(np.float16))
    (LEAFLETS / "index.json").write_text(json.dumps(chunks, ensure_ascii=False) + "\n")
    return len(chunks)


# ---------- 2. retrieve ----------

_index: tuple[np.ndarray, list[dict]] | None = None


def load_index() -> tuple[np.ndarray, list[dict]]:
    global _index
    if _index is None:
        vecs = np.load(LEAFLETS / "index.npy").astype(np.float32)
        meta = json.loads((LEAFLETS / "index.json").read_text(encoding="utf-8"))
        _index = (vecs, meta)
    return _index


def retrieve(generic: str, question: str, k: int = 3, embed_fn=None) -> list[dict]:
    vecs, meta = load_index()
    rows = [i for i, m in enumerate(meta) if m["generic"].lower() == generic.lower()]
    if not rows:
        return []
    q = (embed_fn or embed)([question], "RETRIEVAL_QUERY")[0]
    scores = vecs[rows] @ q
    best = np.argsort(-scores)[:k]
    return [{**meta[rows[j]], "score": float(scores[j])} for j in best]


def leaflet_info(generic: str) -> dict | None:
    f = LEAFLETS / f"{slug(generic)}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


# ---------- 3 + 4. answer with guard ----------

def has_dose(text: str) -> bool:
    return bool(DOSE_RE.search(text.translate(_DIGITS)))


def _generate(prompt: str) -> dict:
    from google import genai
    from google.genai import types

    model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash-lite")
    client = genai.Client()  # keep a reference: a temporary client is closed before the call ends
    r = client.models.generate_content(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0,
            response_schema={"type": "object", "properties": {
                "found": {"type": "boolean"}, "answer": {"type": "string"}},
                "required": ["found", "answer"]}))
    return json.loads(r.text)


def what_its_for(generic: str, lang: str = "en", embed_fn=None, generate_fn=None) -> dict | None:
    """One or two plain sentences on what the medicine is for, or None.

    Cached per medicine and language, so each is generated only once.
    Never breaks the card: with no network or no index it just returns None
    (and caches nothing, so it tries again next time).
    """
    if not generic:
        return None
    try:
        return _what_its_for(generic, lang, embed_fn, generate_fn)
    except Exception as e:  # offline, quota, missing index...
        print(f"  (no 'used for' for {generic}: {type(e).__name__})", flush=True)
        return None


def _what_its_for(generic, lang, embed_fn, generate_fn):
    cache = CACHE / f"uses-{slug(generic)}-{lang}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")) or None
    info = leaflet_info(generic)
    chunks = retrieve(generic, USES_QUESTION, embed_fn=embed_fn) if info else []
    result = None
    if chunks:
        context = "\n\n".join(f"[{c['section']}] {c['text']}" for c in chunks)
        prompt = (
            f"Leaflet text for {generic}:\n\"\"\"\n{context}\n\"\"\"\n\n"
            f"Using ONLY the leaflet text above, say in one or two short, plain sentences what "
            f"{generic} is used for, for an elderly reader, in {LANG_NAMES.get(lang, 'English')}. "
            "Do NOT mention any dose, amount, number of tablets, or how often to take it. "
            "Do not add anything that is not in the text. "
            "If the text does not say what it is used for, return found=false.")
        out = (generate_fn or _generate)(prompt)
        answer = (out.get("answer") or "").strip()
        if out.get("found") and answer and not has_dose(answer):
            result = {"text": answer, "source": info["source"], "source_url": info["source_url"],
                      "sections": sorted({c["section"] for c in chunks})}
        elif answer and has_dose(answer):
            result = None  # blocked by the dose guard; caller shows "ask your pharmacist"
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
