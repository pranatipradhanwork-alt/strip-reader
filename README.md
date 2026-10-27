# Strip Reader

A medicine strip, box and prescription reader for elders. It is built to work on poor networks.

Take a photo. The app reads each medicine with a vision LLM (Gemini). It checks every name against a real medicine list. Then it gives a simple answer. Safety comes first. Every item gets a confidence score and a crop of the spot it was read from. When the app is unsure, it says **"Please confirm with a pharmacist or doctor."** It never makes up a dose.

## Setup

```bash
cd ~/strip-reader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then paste your Gemini key into .env
```

## Run

```bash
python -m strip_reader.cli samples/my_strip.jpg
```

You can pass more than one photo, for example a prescription and its strip, so they are cross-checked together. Crops and `result.json` go to `out/`.

Try it with no API key (uses a saved model reply):

```bash
python -m strip_reader.cli tests/fixtures/fake_strip.png --mock tests/fixtures/fake_strip_reply.json
```

Tests: `pytest -q`

## How it works

| Step | File | What it does |
|---|---|---|
| 1. Read | `strip_reader/vision.py` | Shrinks the photo to 1600 px and sends it to Gemini with a strict JSON schema. For each medicine it gets the name, strength and timing text, all copied exactly as written. It also gets printed vs handwritten, a confidence score and a bounding box. |
| 2. Match | `strip_reader/safety.py` | Fuzzy-matches each name against `data/medicines.csv` (Indian generics plus common brands, each mapped to its generic). |
| 3. Safety | `strip_reader/safety.py` | An item is **OK** only if all of these are true: confidence ≥ 0.80, the text is printed (not handwritten), the name matches the list at ≥ 90%, the strength looks like a real printed strength, and the photo is not blurry or cut off. Anything else is marked **CONFIRM**. A strength or timing that is missing stays empty and is never filled in. |
| 4. Cross-check | `strip_reader/safety.py` | A handwritten item is checked against the printed strips and boxes in the same run. When they agree, a note is added, but the item still needs a pharmacist to confirm. |
| 5. Show | `strip_reader/cli.py` | Prints a simple card per medicine and saves the crop it was read from. |

## Day 1 (27 Oct 2026): done

- Project set up (git, venv, README).
- Core read path: photo → Gemini → structured items (name, strength, timing text, source, confidence, box).
- A crop is saved for every item.
- Names are matched against a real medicine list, and brands are mapped to generics.
- Abstain rules: low confidence, handwriting, unknown name, missing strength or a bad photo all lead to "Please confirm with a pharmacist or doctor".
- The app never invents a dose. Strength and timing are copied text only.
- 9 safety tests pass. The mock demo runs without a key.

## Next

**Week 1**
1. Timing parse: turn "1-0-1", "BD", "after food" and similar into morning/noon/night slots, only from text that is printed.
2. Schedule card: one simple picture card per day (morning / afternoon / night) with large text.
3. Low-network mode: compress photos hard, retry on failure, cache the medicine list and past results, and fall back to a text-only answer.
4. Voice: read the card aloud in the person's language (Hindi, Odia, English first).
5. RAG over medicine leaflets: short, plain answers to "what is this for?" with sources.
6. A bigger medicine list (the full Indian brand list and Jan Aushadhi).

**Week 2**
1. Test set: real photos (strips, boxes, printed and handwritten prescriptions) with names blurred and labelled by hand.
2. Eval: accuracy per field, **safe-failure rate** (how often a wrong read is caught as CONFIRM) and unsafe-pass rate. Run it in real medical stores.
3. WhatsApp bot: send a photo, get the card plus a voice note.
4. README, demo video and slides.

**Stretch:** cheaper generics and Jan Aushadhi suggestions, and a caregiver mode (a family member gets the schedule).

## Safety note

This is a reading aid, not medical advice. Always check with a pharmacist or doctor before taking any medicine.
