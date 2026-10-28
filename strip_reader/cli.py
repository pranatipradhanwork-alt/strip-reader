"""Command line: python -m strip_reader.cli samples/photo1.jpg [photo2.jpg ...]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .safety import CONFIRM_MSG, check_item, cross_check, load_medicines, merge_same_medicine
from . import confirm
from .card import LANGS, build_schedule, card_html, card_text
from .vision import crop, load_image, read_image

ROOT = Path(__file__).resolve().parent.parent


def run(images: list[str], out_dir: Path, mock: str | None = None, model: str | None = None) -> dict:
    medicines = load_medicines(ROOT / "data" / "medicines.csv")
    all_items, per_image = [], []
    for path in images:
        img = load_image(path)
        cache = out_dir / f"{Path(path).stem}_raw.json"
        if mock:
            raw = json.loads(Path(mock).read_text())
        elif cache.exists():
            raw = json.loads(cache.read_text())
        else:
            raw = read_image(img, model)
            cache.parent.mkdir(parents=True, exist_ok=True)
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
    p.add_argument("images", nargs="+")
    p.add_argument("--out", default=str(ROOT / "out"), help="folder for crops and result.json")
    p.add_argument("--mock", help="use a saved model reply (JSON) instead of calling Gemini")
    p.add_argument("--model", help="Gemini model name (default from GEMINI_MODEL or gemini-3.8-flash)")
    p.add_argument("--lang", default="en", choices=LANGS, help="card language (one file per language in data/i18n)")
    p.add_argument("--quiet", action="store_true", help="print only the schedule card")
    a = p.parse_args()
    out = Path(a.out)
    result = run(a.images, out, a.mock, a.model)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    sched = build_schedule(result, confirm.load(out / "confirmations.json"))
    (out / "schedule.json").write_text(json.dumps(sched, indent=2, ensure_ascii=False))
    (out / "card.html").write_text(card_html(sched, a.lang), encoding="utf-8")
    if not a.quiet:
        show(result)
    print(card_text(sched, a.lang))
    print(f"\nSaved: {out / 'result.json'}, {out / 'schedule.json'}, {out / 'card.html'}")


if __name__ == "__main__":
    main()
