"""Command line: python -m strip_reader.cli samples/photo1.jpg [photo2.jpg ...]"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from dotenv import load_dotenv

from .safety import CONFIRM_MSG, check_item, cross_check, load_medicines, merge_same_medicine
from . import confirm
from .card import LANGS, build_schedule, card_html, card_text
from .vision import PROFILES, OfflineError, crop, load_image, read_image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "out" / "cache"   # Gemini replies by photo content: a photo is never sent twice
QUEUE = ROOT / "out" / "queue"   # photos taken while offline, read later with --flush-queue


def photo_key(path: str | Path, profile: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16] + f"-{profile}"


def run(images: list[str], out_dir: Path, mock: str | None = None, model: str | None = None,
        profile: str = "low") -> dict:
    medicines = load_medicines(ROOT / "data" / "medicines.csv")
    all_items, per_image, offline = [], [], False
    for path in images:
        img = load_image(path)
        old_cache = out_dir / f"{Path(path).stem}_raw.json"   # Day 1 style, still read
        cache = CACHE / f"{photo_key(path, profile)}.json"
        if mock:
            raw = json.loads(Path(mock).read_text())
        elif cache.exists():
            raw = json.loads(cache.read_text())
        elif old_cache.exists():
            raw = json.loads(old_cache.read_text())
        else:
            try:
                if offline:
                    raise OfflineError("network already down")
                raw = read_image(img, model, profile)
            except OfflineError:
                # Keep the photo; never lose it. Stop trying the rest for now.
                offline = True
                QUEUE.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, QUEUE / Path(path).name)
                per_image.append({"image": path, "queued": True, "items": []})
                continue
            CACHE.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
        items = []
        for n, r in enumerate(merge_same_medicine(raw.get("items", []), medicines), 1):
            item = check_item(r, medicines)
            item.crop_path = crop(img, r.get("box_2d"), out_dir / f"{Path(path).stem}_item{n}.png")
            if raw.get("image_quality", "good") != "good":
                item.add(f"photo_{raw['image_quality']}", f"photo is {raw['image_quality']}")
                item.status, item.message = "confirm", CONFIRM_MSG
            if r.get("also_printed_as"):
                item.reasons.append("also printed as: " + ", ".join(map(str, r["also_printed_as"])))  # info only
            items.append(item)
        per_image.append({"image": path, "image_type": raw.get("image_type"),
                          "image_quality": raw.get("image_quality"), "items": items})
        all_items += items
    cross_check(all_items)
    for img in per_image:
        img["items"] = [i.to_dict() for i in img["items"]]
    return {"images": per_image}


def show(result: dict) -> None:
    for img in result["images"]:
        if img.get("queued"):
            print(f"\n=== {img['image']}: no network, saved to {QUEUE} to read later ===")
            continue
        print(f"\n=== {img['image']}  ({img['image_type']}, photo {img['image_quality']}) ===")
        if not img["items"]:
            print("  No medicine could be read. " + CONFIRM_MSG)
        for i in img["items"]:
            mark = "OK     " if i["status"] == "ok" else "CONFIRM"
            name = i["matched_name"] or i["name_as_read"] or "(unreadable)"
            print(f"\n  [{mark}] {name}  {i['strength'] or '(strength not read)'}")
            print(f"     read as   : {i['name_as_read']}   (source: {i['source']})")
            if i["generic"]:
                print(f"     generic   : {i['generic']}   (list match {i['match_score']}%)")
            print(f"     timing    : {i['timing_text'] or 'not printed'}")
            if i.get("timing_note"):
                print(f"     !! {i['timing_note']}")
            print(f"     confidence: {i['confidence']:.2f}")
            if i["crop_path"]:
                print(f"     crop      : {i['crop_path']}")
            for r in i["reasons"]:
                print(f"     - {r}")
            print(f"     -> {i['message']}")
    print()


def main() -> None:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="Read medicine strips, boxes and prescriptions.")
    p.add_argument("images", nargs="*")
    p.add_argument("--network", default="low", choices=list(PROFILES),
                   help="upload size: normal, low (default, ~80 KB) or tiny (~40 KB, grey)")
    p.add_argument("--flush-queue", action="store_true", help="read the photos saved while offline")
    p.add_argument("--no-crops", action="store_true", help="smallest card: no label pictures")
    p.add_argument("--out", default=str(ROOT / "out"), help="folder for crops and result.json")
    p.add_argument("--mock", help="use a saved model reply (JSON) instead of calling Gemini")
    p.add_argument("--model", help="Gemini model name (default from GEMINI_MODEL or gemini-3.8-flash)")
    p.add_argument("--lang", default="en", choices=LANGS, help="card language (one file per language in data/i18n)")
    p.add_argument("--quiet", action="store_true", help="print only the schedule card")
    a = p.parse_args()
    out = Path(a.out)
    images = list(a.images)
    if a.flush_queue:
        images += sorted(str(f) for f in QUEUE.glob("*") if f.is_file())
    if not images:
        p.error("give at least one photo (or --flush-queue)")
    result = run(images, out, a.mock, a.model, a.network)
    if a.flush_queue:
        for img in result["images"]:
            if not img.get("queued") and Path(img["image"]).parent == QUEUE:
                Path(img["image"]).unlink(missing_ok=True)   # read now; cached reply kept
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    sched = build_schedule(result, confirm.load(out / "confirmations.json"))
    (out / "schedule.json").write_text(json.dumps(sched, indent=2, ensure_ascii=False))
    html_card = card_html(sched, a.lang, crops=not a.no_crops)
    text_card = card_text(sched, a.lang)
    (out / "card.html").write_text(html_card, encoding="utf-8")
    (out / "card.txt").write_text(text_card, encoding="utf-8")
    if not a.quiet:
        show(result)
    print(text_card)
    kb = lambda t: len(t.encode("utf-8")) / 1024
    print(f"\nSaved: card.html ({kb(html_card):.0f} KB), card.txt ({kb(text_card):.1f} KB, "
          f"for SMS/WhatsApp text), result.json, schedule.json in {out}")


if __name__ == "__main__":
    main()
