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
- The app never treats timing on a strip or box as a dose. Only a printed prescription counts; anything else (a pharmacy sticker, a pen mark) is shown with a "confirm" warning.
- Brand and generic printed on the same strip (e.g. "Dolo-650" and "Paracetamol 650 mg") are merged into one medicine.
- Gemini replies are saved per photo, so a photo is never sent twice. When Gemini is busy the app waits and retries; when the daily free limit runs out it switches to a lite model.
- 16 safety tests pass. The mock demo runs without a key.

### First real run (12 photos: 6 prescriptions, 6 strips/boxes)

Models: `gemini-3.8-flash` (photos 1–4), then `gemini-3.5-flash-lite` once the free daily limit (20 requests) ran out.

| | Count |
|---|---|
| Medicines read | 28 |
| Marked OK | 4 (Dolo 650, Ondem MD 4, Electral, Drotaverine 80 mg; all printed strips/boxes) |
| Marked CONFIRM | 24 (all handwritten items, plus strips with an unknown name or missing strength) |
| Wrongly marked OK | 0 found by eye (not yet a proper labelled eval) |

What the run taught us, and what was fixed:
- **Wrong match:** "Amoxyclav" matched "Mox" (plain amoxicillin). Fixed: no partial matching inside longer words.
- **Wrong match the other way:** "Paracetamol" matched the combo "Aceclofenac + Paracetamol". Fixed: a list name matches only if all its words were read.
- **Suffixes:** "Montair Plus" is not "Montair". Now any extra word means CONFIRM.
- **Sticker timing:** the Ondem strip had a Bengali pharmacy sticker with a pen tick ("১-০-১"). It is now shown with a warning, not as a dose.
- **Unit-less strengths** on prescriptions ("Nexpro (40)") are dropped, not guessed as mg. Correct, but week 2 should show them as "40 (unit not written)".
- **Medicine list:** brands seen in these photos were added (Daxid, Nexpro RD, Ciplar-LA, Rivotril, Eptoin and others). This makes these photos easier than new ones, so week 2 needs a full list and a fresh test set.
- Photo 1 (a prescription page with no medicines on it) correctly returned nothing.

## Day 2 (28 Oct 2026): timing parse and schedule card

```bash
python -m strip_reader.cli samples/my_rx.jpg --lang or      # en, hi or or (Odia)
python -m strip_reader.cli tests/fixtures/printed_rx.png --mock tests/fixtures/printed_rx_reply.json --lang hi   # demo, no key
```

Every run now also writes `schedule.json` and `card.html`: one light, phone-sized page with big text, an icon per time of day, and a small picture of each label. It is about 25–110 KB, small enough for a weak network.

**Timing parser** (`strip_reader/timing.py`) only uses what is written:

| Written | Understood as | Sure? |
|---|---|---|
| `1-0-1`, `½-0-½`, `1-1-1-1`, `১-০-১` (Bengali/Hindi/Odia digits) | exact morning / afternoon / (evening) / night amounts | yes |
| `after food`, `PC`, `A/C`, `before food` | food timing | yes |
| `x 5 days`, `for 1 week`, `1 month` | how many days | yes |
| `HS`, `at bedtime` | night | yes |
| `before breakfast` / `lunch` / `dinner` | morning / afternoon / night | yes |
| `SOS for fever` | only when needed (fever) | yes |
| `OD`, `BD`, `TDS` | count per day only; **the times are not written, so we don't pick them** | no |
| `BF` | could be "before food" or "breakfast" | no |
| anything else (`apply`, `as directed`) | listed as unclear | no |

**Card rule:** a medicine goes into a time slot only if its name and strength are OK, the timing comes from a printed prescription, and the parser is sure. Everything else goes under **"Ask your pharmacist first"** with what was read and why.

**On the 12 real photos** every prescription was handwritten, so the card correctly put every medicine under "Ask your pharmacist first". This shows the next step clearly: a **pharmacist confirm step**. The pharmacist ticks each item (looking at the crop), and only then does it move into a slot.

### Languages (multilingual by design)

All card text lives in one file per language in `data/i18n/` (`en.json`, `hi.json`, `or.json`). Every safety reason has a short code (`handwritten`, `name_not_found`, …), so the card shows it in the person's language. The English detail stays in `result.json` and in a small "details" fold for the pharmacist.

| Language | Status |
|---|---|
| English | source |
| Odia | checked by a native speaker (Pranati, 28 Oct); 4 newer phrases listed under `_unchecked` |
| Hindi | draft, not yet checked by a native speaker |

**Adding a language:** copy `en.json` to `<code>.json`, translate the values (Gemini can draft them), have a native speaker check them, and list anything unchecked under `_unchecked`. There's no code change, and the tests make sure no phrase or `{n}` placeholder is missing. The safety messages are never translated live by an LLM: fixed, checked text can't drift, and it works offline.

Medicine names stay as printed (usually English).

33 tests pass.

## Next

**Week 1**
1. ~~Timing parse~~ and 2. ~~Schedule card~~: done on Day 2.
2b. Pharmacist confirm step: tick an item to move it into the card. ("Why" notes are now translated.)
3. Low-network mode: compress photos hard, retry on failure, cache the medicine list and past results, and fall back to a text-only answer.
4. Voice: read the card aloud in the person's language (Hindi, Odia, English first).
5. RAG over medicine leaflets: short, plain answers to "what is this for?" with sources.
6. A bigger medicine list (the full Indian brand list and Jan Aushadhi), so new photos aren't matched against a list tuned on these ones.

**Week 2**
1. Test set: real photos (strips, boxes, printed and handwritten prescriptions) with names blurred and labelled by hand.
2. Eval: accuracy per field, **safe-failure rate** (how often a wrong read is caught as CONFIRM) and unsafe-pass rate. Run it in real medical stores.
3. WhatsApp bot: send a photo, get the card plus a voice note.
4. README, demo video and slides.

**Stretch:** cheaper generics and Jan Aushadhi suggestions, and a caregiver mode (a family member gets the schedule).

## Safety note

This is a reading aid, not medical advice. Always check with a pharmacist or doctor before taking any medicine.
