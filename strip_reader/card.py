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
import html
import io
from pathlib import Path

from .timing import parse_timing

LABELS = {
    "en": {"title": "Your medicines today", "morning": "Morning", "afternoon": "Afternoon",
           "evening": "Evening", "night": "Night", "as_needed": "Only when needed",
           "ask": "Ask your pharmacist first", "none": "Nothing here",
           "after food": "after food", "before food": "before food", "with food": "with food",
           "days": "for {n} days", "footer": "This card is a reading aid, not medical advice."},
    "hi": {"title": "आज की दवाइयाँ", "morning": "सुबह", "afternoon": "दोपहर", "evening": "शाम",
           "night": "रात", "as_needed": "ज़रूरत होने पर ही", "ask": "पहले फार्मासिस्ट से पूछें",
           "none": "कुछ नहीं", "after food": "खाने के बाद", "before food": "खाने से पहले",
           "with food": "खाने के साथ", "days": "{n} दिन तक",
           "footer": "यह कार्ड पढ़ने में मदद के लिए है, डॉक्टर की सलाह नहीं।"},
    "or": {"title": "ଆଜିର ଔଷଧ", "morning": "ସକାଳ", "afternoon": "ମଧ୍ୟାହ୍ନ", "evening": "ସନ୍ଧ୍ୟା",
           "night": "ରାତି", "as_needed": "ଆବଶ୍ୟକ ହେଲେ ହିଁ", "ask": "ପ୍ରଥମେ ଫାର୍ମାସିଷ୍ଟଙ୍କୁ ପଚାରନ୍ତୁ",
           "none": "କିଛି ନାହିଁ", "after food": "ଖାଇବା ପରେ", "before food": "ଖାଇବା ପୂର୍ବରୁ",
           "with food": "ଖାଇବା ସହିତ", "days": "{n} ଦିନ ପାଇଁ",
           "footer": "ଏହି କାର୍ଡ ପଢ଼ିବାରେ ସାହାଯ୍ୟ ପାଇଁ, ଡାକ୍ତରୀ ପରାମର୍ଶ ନୁହେଁ।"},
}
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
            why = []
            if i["status"] != "ok":
                why += i["reasons"] or [i["message"]]
            if t is None:
                why.append("no timing written")
            elif i.get("timing_note"):
                why.append("timing not from a printed prescription")
            elif not t.sure:
                why.append("timing not clear: " + ", ".join(t.unclear) if t.unclear
                           else "times of day not written")
            if why:
                entry["read_timing"] = t.summary() if t else None
                entry["why"] = why
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


def card_text(sched: dict, lang: str = "en") -> str:
    L = LABELS[lang]
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
        if e.get("read_timing"):
            lines.append(f"      read: {e['read_timing']}")
        lines.append(f"      why : {'; '.join(e['why'])}")
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
    L = LABELS[lang]
    icons = {"morning": "☀️", "afternoon": "🌤️", "evening": "🌇", "night": "🌙", "as_needed": "⏱️"}
    esc = html.escape
    sections = []
    for key in (*SLOT_ORDER, "as_needed"):
        if key in ("evening", "as_needed") and not sched[key]:
            continue
        rows = "".join(f"<li>{esc(_line(e, L))}{_img_tag(e['crop'])}</li>" for e in sched[key]) \
            or f'<li class="none">{esc(L["none"])}</li>'
        sections.append(f'<section><h2>{icons[key]} {esc(L[key])}</h2><ul>{rows}</ul></section>')
    ask_rows = "".join(
        f"<li><b>{esc(_with_strength(e['name'], e['strength']))}</b>"
        + (f"<div class='read'>read: {esc(e['read_timing'])}</div>" if e.get("read_timing") else "")
        + f"<div class='why'>{esc('; '.join(e['why']))}</div>{_img_tag(e['crop'])}</li>"
        for e in sched["ask"]) or f'<li class="none">{esc(L["none"])}</li>'
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
footer {{ margin-top:24px; font-size:16px; color:var(--muted); }}
</style></head>
<body><main><h1>{esc(L['title'])}</h1>{''.join(sections)}
<footer>{esc(L['footer'])}</footer></main></body></html>"""
