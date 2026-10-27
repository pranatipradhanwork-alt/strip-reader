"""Command line: python -m strip_reader.cli samples/photo1.jpg [photo2.jpg ...]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .safety import CONFIRM_MSG, check_item, cross_check, load_medicines
from .vision import crop, load_image, read_image

ROOT = Path(__file__).resolve().parent.parent


def run(images: list[str], out_dir: Path, mock: str | None = None, model: str | None = None) -> dict:
    medicines = load_medicines(ROOT / "data" / "medicines.csv")
    all_items, per_image = [], []
    for path in images:
        img = load_image(path)
        raw = json.loads(Path(mock).read_text()) if mock else read_image(img, model)
        items = []
        for n, r in enumerate(raw.get("items", []), 1):
            item = check_item(r, medicines)
            item.crop_path = crop(img, r.get("box_2d"), out_dir / f"{Path(path).stem}_item{n}.png")
            if raw.get("image_quality", "good") != "good":
                item.reasons.append(f"photo is {raw['image_quality']}")
                item.status, item.message = "confirm", CONFIRM_MSG
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
    p.add_argument("--model", help="Gemini model name (default from GEMINI_MODEL or gemini-2.5-flash)")
    a = p.parse_args()
    out = Path(a.out)
    result = run(a.images, out, a.mock, a.model)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    show(result)
    print(f"Full result saved to {out / 'result.json'}")


if __name__ == "__main__":
    main()
