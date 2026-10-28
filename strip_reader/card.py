"""Build a simple daily schedule card (text and HTML) for an elder.

A medicine goes into a time slot ONLY if:
  - its name and strength were read clearly (status "ok"),
  - the timing came from a printed prescription (no timing warning), and
  - the timing parser is sure (times written out, nothing unclear).
Everything else goes under "Ask your pharmacist first", with what was read
and why, so the pharmacist can check it quickly.
"""

from __future__ import annotations

import base64
import json
import html
import io
from pathlib import Path

from .timing import parse_timing

I18N_DIR = Path(__file__).resolve().parent.parent / "data" / "i18n"
LANGS = sorted(f.stem for f in I18N_DIR.glob("*.json"))


def labels(lang: str) -> dict:
    """Card text for one language. Any missing phrase falls back to English."""
    en = json.loads((I18N_DIR / "en.json").read_text(encoding="utf-8"))
    path = I18N_DIR / f"{lang}.json"
    tr = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    out = {**en, **tr}
    out["reasons"] = {**en["reasons"], **tr.get("reasons", {})}
    return out


SLOT_ORDER = ("morning", "afternoon", "evening", "night")


def build_schedule(result: dict) -> dict:
    """Sort every item into a slot, 'as needed', or 'ask'."""
    sched = {s: [] for s in SLOT_ORDER}
    sched["as_needed"], sched["ask"] = [], []
    for img in result["images"]:
        for i in img["items"]:
            t = parse_timing(i.get("timing_text"))
            entry = {"name": _name(i),
                     "strength": i["strength"], "crop": i.get("crop_path"),
                     "timing": t.to_dict() if t else None}
            why, codes = [], []
            if i["status"] != "ok":
                why += i["reasons"] or [i["message"]]
                codes += i.get("codes") or []
            if t is None:
                why.append("no timing written"); codes.append("no_timing")
            elif i.get("timing_note"):
                why.append("timing not from a printed prescription"); codes.append("timing_not_printed_rx")
            elif not t.sure:
                if t.unclear:
                    why.append("timing not clear: " + ", ".join(t.unclear)); codes.append("timing_unclear")
                else:
                    why.append("times of day not written"); codes.append("times_not_written")
            if why:
                entry["why"] = why            # English detail for the pharmacist
                entry["why_codes"] = codes    # translated on the card
                sched["ask"].append(entry)
                continue
            if t.as_needed and not t.slots:
                sched["as_needed"].append({**entry, "amount": None})
            for slot, amt in (t.slots or {}).items():
                if amt:
                    sched[slot].append({**entry, "amount": amt})
    return sched


def _name(i: dict) -> str:
    """Display name. Leave out the strength later if the name already has it ("Dolo 650")."""
    return i["matched_name"] or i["name_as_read"] or "(unreadable)"


def _with_strength(name: str, strength: str | None) -> str:
    if not strength:
        return name
    digits = "".join(ch for ch in strength if ch.isdigit() or ch == ".").strip(".")
    return name if digits and digits in name else f"{name} {strength}"


def timing_summary(t: dict | None, L: dict) -> str | None:
    """What was read, in the card's language."""
    if not t:
        return None
    amt = lambda v: {0.5: "½", 0.25: "¼"}.get(v, f"{v:g}")
    parts = []
    if t.get("as_needed"):
        parts.append(L["as_needed"] + (f" ({t['reason']})" if t.get("reason") else ""))
    if t.get("slots"):
        parts.append(", ".join(f"{amt(v)} {L[k]}" for k, v in t["slots"].items() if v))
    elif t.get("per_day"):
        parts.append(L["times_a_day"].format(n=amt(t["per_day"])))
    if t.get("food"):
        parts.append(L[t["food"]])
    if t.get("days"):
        parts.append(L["days"].format(n=t["days"]))
    return "; ".join(parts) or L["reasons"]["timing_unclear"]


def why_text(e: dict, L: dict) -> str:
    seen = []
    for c in e.get("why_codes", []):
        if c in L["reasons"] and L["reasons"][c] not in seen:
            seen.append(L["reasons"][c])
    return "; ".join(seen)


def card_text(sched: dict, lang: str = "en") -> str:
    L = labels(lang)
    lines = [f"===== {L['title']} ====="]
    for key in (*SLOT_ORDER, "as_needed"):
        if key == "evening" and not sched[key]:
            continue
        lines.append(f"\n{L[key]}:")
        if not sched[key]:
            lines.append(f"  - {L['none']}")
        for e in sched[key]:
            lines.append("  - " + _line(e, L))
    lines.append(f"\n{L['ask']}:")
    if not sched["ask"]:
        lines.append(f"  - {L['none']}")
    for e in sched["ask"]:
        lines.append(f"  - {_with_strength(e['name'], e['strength'])}")
        read = timing_summary(e["timing"], L)
        if read:
            lines.append(f"      {L['read']} {read}")
        lines.append(f"      → {why_text(e, L)}")
    if sched["ask"]:
        lines.append(f"\n{L['confirm']}")
    lines.append(f"\n{L['footer']}")
    return "\n".join(lines)


def _line(e: dict, L: dict) -> str:
    t = e["timing"] or {}
    bits = []
    if e.get("amount"):
        amt = {0.5: "½", 0.25: "¼"}.get(e["amount"], f"{e['amount']:g}")
        bits.append(f"{amt} ×")
    bits.append(_with_strength(e["name"], e["strength"]))
    if t.get("reason"):
        bits.append(f"({t['reason']})")
    if t.get("food"):
        bits.append(f"({L[t['food']]})")
    if t.get("days"):
        bits.append("— " + L["days"].format(n=t["days"]))
    return " ".join(bits)


def _img_tag(path: str | None) -> str:
    if not path or not Path(path).exists():
        return ""
    # Small JPEG thumbnail keeps the card light enough for a weak network.
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((360, 120))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=60)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'<img class="crop" alt="photo of the label" src="data:image/jpeg;base64,{b64}">'


def card_html(sched: dict, lang: str = "en") -> str:
    """One self-contained HTML file: big text, high contrast, crops inline."""
    L = labels(lang)
    icons = {"morning": "☀️", "afternoon": "🌤️", "evening": "🌇", "night": "🌙", "as_needed": "⏱️"}
    esc = html.escape
    sections = []
    for key in (*SLOT_ORDER, "as_needed"):
        if key in ("evening", "as_needed") and not sched[key]:
            continue
        rows = "".join(f"<li>{esc(_line(e, L))}{_img_tag(e['crop'])}</li>" for e in sched[key]) \
            or f'<li class="none">{esc(L["none"])}</li>'
        sections.append(f'<section><h2>{icons[key]} {esc(L[key])}</h2><ul>{rows}</ul></section>')
    def ask_row(e):
        read = timing_summary(e["timing"], L)
        return (f"<li><b>{esc(_with_strength(e['name'], e['strength']))}</b>"
                + (f"<div class='read'>{esc(L['read'])} {esc(read)}</div>" if read else "")
                + f"<div class='why'>{esc(why_text(e, L))}</div>{_img_tag(e['crop'])}"
                + f"<details><summary>details</summary>{esc('; '.join(e['why']))}</details></li>")
    ask_rows = "".join(ask_row(e) for e in sched["ask"]) or f'<li class="none">{esc(L["none"])}</li>'
    if sched["ask"]:
        ask_rows += f"<li class='confirm'>{esc(L['confirm'])}</li>"
    sections.append(f'<section class="ask"><h2>⚠️ {esc(L["ask"])}</h2><ul>{ask_rows}</ul></section>')
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Medicine Card</title>
<style>
:root {{ --bg:#fffdf7; --fg:#111; --muted:#555; --line:#ddd; --warn:#b54708; --warnbg:#fff4e5; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141414; --fg:#f2f2f2; --muted:#aaa; --line:#333; --warn:#ffb86b; --warnbg:#2a1d0e; }} }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--fg);
        font:22px/1.45 system-ui, -apple-system, "Noto Sans", "Noto Sans Oriya", sans-serif; }}
main {{ max-width:640px; margin:0 auto; }}
h1 {{ font-size:30px; margin:0 0 12px; }}
h2 {{ font-size:26px; margin:20px 0 8px; }}
section {{ border-top:2px solid var(--line); }}
ul {{ list-style:none; padding:0; margin:0; }}
li {{ padding:10px 0; border-bottom:1px solid var(--line); }}
li.none {{ color:var(--muted); }}
.ask {{ background:var(--warnbg); border-radius:12px; padding:4px 14px; margin-top:20px; border-top:none; }}
.ask h2 {{ color:var(--warn); }}
.why, .read {{ font-size:17px; color:var(--muted); }}
.crop {{ display:block; max-width:100%; max-height:90px; margin-top:6px; border:1px solid var(--line); border-radius:6px; }}
details {{ font-size:14px; color:var(--muted); margin-top:4px; }}
li.confirm {{ font-weight:600; color:var(--warn); border-bottom:none; }}
footer {{ margin-top:24px; font-size:16px; color:var(--muted); }}
</style></head>
<body><main><h1>{esc(L['title'])}</h1>{''.join(sections)}
<footer>{esc(L['footer'])}</footer></main></body></html>"""
