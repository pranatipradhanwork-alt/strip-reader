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

from .confirm import item_id
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


def build_schedule(result: dict, confirmations: dict | None = None, uses_fn=None) -> dict:
    """Sort every item into a slot, 'as needed', or 'ask'.

    `confirmations` (from the pharmacist page) can move an item onto the card
    or remove it as "not a medicine". See confirm.py.

    `uses_fn(generic)` returns "what it's for" from the leaflet (leaflets.py).
    It is only asked for medicines whose name we trust: printed and read
    clearly (status "ok"), or confirmed by a pharmacist. Never for an unsure
    or handwritten name, because the wrong "use" is worse than none.
    """
    confirmations = confirmations or {}
    uses = uses_fn or (lambda g: None)
    sched = {s: [] for s in SLOT_ORDER}
    sched["as_needed"], sched["ask"] = [], []
    sched["queued"] = [Path(img["image"]).name for img in result["images"] if img.get("queued")]
    sched["not_medicine"] = [Path(img["image"]).name for img in result["images"] if img.get("not_medicine")]
    sched["offline"] = [Path(img["image"]).name for img in result["images"] if img.get("offline")]
    # A medicine photo where nothing could be read must never look like "no medicines".
    sched["unread"] = [Path(img["image"]).name for img in result["images"]
                       if not img.get("queued") and not img.get("not_medicine") and not img.get("items")]
    for img in result["images"]:
        for n, i in enumerate(img["items"], 1):
            iid = item_id(img.get("image", "photo"), n)
            c = confirmations.get(iid)
            if c and c["action"] == "not_medicine":
                continue
            if c and c["action"] == "confirm":
                t = parse_timing(c["timing_text"])
                entry = {"id": iid, "name": c["name"], "strength": c["strength"],
                         "crop": i.get("crop_path"), "timing": t.to_dict(),
                         "confirmed_by": c["by"], "confirmed_at": c["at"], "confirmed_reg": c.get("reg_no"),
                         "uses": uses(_trusted_generic(c["name"]))}
                _place(sched, entry, t)
                continue
            t = parse_timing(i.get("timing_text"))
            trusted = i["status"] == "ok" and i.get("source") != "handwritten"
            entry = {"id": iid, "name": _name(i),
                     "strength": i["strength"], "crop": i.get("crop_path"),
                     "timing": t.to_dict() if t else None,
                     "uses": uses(i.get("generic")) if trusted else None}
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
            _place(sched, entry, t)
    return sched


def _trusted_generic(name: str) -> str | None:
    """Generic for a pharmacist-typed name, only if it is a sure list match."""
    from .safety import MATCH_OK, load_medicines, match_name
    m = match_name(name, load_medicines(Path(__file__).resolve().parent.parent / "data" / "medicines.csv"))
    return m.generic if m.score >= MATCH_OK else None


def _place(sched: dict, entry: dict, t) -> None:
    if t.as_needed and not t.slots:
        sched["as_needed"].append({**entry, "amount": None})
    for slot, amt in (t.slots or {}).items():
        if amt:
            sched[slot].append({**entry, "amount": amt})


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
    if sched.get("queued"):
        lines.append(f"\n!! {L['queued'].format(n=len(sched['queued']))}")
    if sched.get("not_medicine"):
        lines.append(f"\n!! {L['not_medicine'].format(n=len(sched['not_medicine']))}")
    if sched.get("unread"):
        lines.append(f"\n!! {L['nothing_read'].format(n=len(sched['unread']))}")
    if sched.get("offline"):
        lines.append(f"\n!! {L['offline_mode']}")
    for key in (*SLOT_ORDER, "as_needed"):
        if key == "evening" and not sched[key]:
            continue
        lines.append(f"\n{L[key]}:")
        if not sched[key]:
            lines.append(f"  - {L['none']}")
        for e in sched[key]:
            lines.append("  - " + _line(e, L))
            if e.get("uses"):
                lines.append(f"      {L['used_for']} {e['uses']['text']} {L['uses_note']}")
    lines.append(f"\n{L['ask']}:")
    if not sched["ask"]:
        lines.append(f"  - {L['none']}")
    for e in sched["ask"]:
        lines.append(f"  - {_with_strength(e['name'], e['strength'])}")
        read = timing_summary(e["timing"], L)
        if read:
            lines.append(f"      {L['read']} {read}")
        if e.get("uses"):
            lines.append(f"      {L['used_for']} {e['uses']['text']} {L['uses_note']}")
        lines.append(f"      → {why_text(e, L)}")
    if any(e.get("confirmed_by") for k in (*SLOT_ORDER, "as_needed") for e in sched[k]):
        lines.append(f"\n{L['confirmed_legend']}")
    if sched["ask"]:
        lines.append(f"\n{L['confirm']}")
    lines.append(f"\n{L['urgent']}")
    lines.append(f"\n{L['footer']}")
    return "\n".join(lines)


def _line(e: dict, L: dict) -> str:
    t = e["timing"] or {}
    bits = []
    if e.get("amount"):
        amt = {0.5: "½", 0.25: "¼"}.get(e["amount"], f"{e['amount']:g}")
        bits.append(f"{amt} ×")
    bits.append(_with_strength(e["name"], e["strength"]))
    if e.get("confirmed_by"):
        bits.append("✓")
    if t.get("reason"):
        bits.append(f"({t['reason']})")
    if t.get("food"):
        bits.append(f"({L[t['food']]})")
    if t.get("days"):
        bits.append("— " + L["days"].format(n=t["days"]))
    return " ".join(bits)


def _uses_html(e: dict, L: dict) -> str:
    u = e.get("uses")
    if not u:
        return ""
    esc = html.escape
    return (f"<div class='uses'><b>{esc(L['used_for'])}</b> {esc(u['text'])} "
            f"<a class='src' href='{esc(u['source_url'])}'>{esc(L['source_short'])}</a>"
            f"<p class='note'>{esc(L['uses_note'])}</p></div>")


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


STYLE = (Path(__file__).resolve().parent / "static" / "style.css").read_text(encoding="utf-8")
FONTS = ""  # system fonts (SF / Roboto / Noto): native look, nothing to download on a weak network
# Light or dark: the saved choice, else the phone's setting. Runs before the page draws (no flash).
THEME_JS = """<script>
(function () {
  var t = null; try { t = localStorage.getItem('sr-theme'); } catch (e) {}
  if (t !== 'light' && t !== 'dark') t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', t);
  document.addEventListener('click', function (e) {
    document.querySelectorAll('details.lang-menu[open]').forEach(function (d) {   // close menu on outside tap
      if (!d.contains(e.target)) d.removeAttribute('open');
    });
    var b = e.target.closest && e.target.closest('.theme-toggle'); if (!b) return;
    var n = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', n);
    try { localStorage.setItem('sr-theme', n); } catch (e) {}
  });
})();
</script>"""
SLOT_ICONS = {"morning": "sun", "afternoon": "sun_high", "evening": "sunset", "night": "moon", "as_needed": "clock"}


def head(title: str) -> str:
    """<head> shared by every page: fonts (fall back to the phone's own if offline) + inline styles."""
    return (f"<meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(title)}</title>{THEME_JS}{FONTS}<style>{STYLE}</style>")


def _med_html(e: dict, L: dict, img) -> str:
    """One medicine: amount, name, tags (food / days / reason), used-for, label picture."""
    from .icons import icon
    esc = html.escape
    t = e.get("timing") or {}
    amt = ""
    if e.get("amount"):
        a = {0.5: "½", 0.25: "¼"}.get(e["amount"], f"{e['amount']:g}")
        amt = f"<span class='amt' title='amount'>{a}</span>"
    tick = ""
    if e.get("confirmed_by"):
        who = (L["checked_by"].format(name=e["confirmed_by"], reg=e["confirmed_reg"]) if e.get("confirmed_reg")
               else L["pharmacist"])
        tick = f" <span class='tick'>{icon('check', 16)}{esc(who)}</span>"
    chips = []
    if t.get("reason"):
        chips.append(t["reason"])
    if t.get("food"):
        chips.append(L[t["food"]])
    if t.get("days"):
        chips.append(L["days"].format(n=t["days"]))
    chip_html = ("<div class='chips'>" + "".join(f"<span class='chip'>{esc(c)}</span>" for c in chips)
                 + "</div>") if chips else ""
    return (f"<li class='med'><div class='med-main'>{amt}<span class='name'>"
            f"{esc(_with_strength(e['name'], e['strength']))}</span>{tick}</div>"
            f"{chip_html}{_uses_html(e, L)}{img(e.get('crop'))}</li>")


def card_html(sched: dict, lang: str = "en", crops: bool = True, audio: str | None = None,
              nav: str = "", actions: str = "") -> str:
    """The medicine card as one self-contained page: a day timeline (morning -> night).

    `nav` and `actions` let the web app add its header and buttons around the same card.
    """
    from .icons import icon
    L = labels(lang)
    esc = html.escape
    img = _img_tag if crops else (lambda _p: "")
    on_card = len({e.get("id") or e["name"] for k in (*SLOT_ORDER, "as_needed") for e in sched[k]})
    parts = [f"<header class='page-head'><p class='kicker'>{esc(L['card_kicker'])}</p>"
             f"<h1>{esc(L['title'])}</h1>"
             f"<p class='meta'>{esc(L['meta'].format(a=on_card, b=len(sched['ask'])))}</p></header>"]
    if actions:
        parts.append(f"<div class='toolbar'>{actions}</div>")
    if audio:
        parts.append(f"<div class='listen'><div class='row'>{icon('speaker')}{esc(L['listen'])}</div>"
                     f"<audio controls preload='none' src='{esc(audio)}'></audio></div>")
    if sched.get("queued"):
        parts.append(f"<div class='notice' role='status'>{icon('wifi_off')}"
                     f"<div>{esc(L['queued'].format(n=len(sched['queued'])))}</div></div>")
    if sched.get("not_medicine"):
        parts.append(f"<div class='notice' role='alert'>{icon('block')}"
                     f"<div>{esc(L['not_medicine'].format(n=len(sched['not_medicine'])))}</div></div>")
    if sched.get("unread"):
        parts.append(f"<div class='notice' role='alert'>{icon('alert')}"
                     f"<div>{esc(L['nothing_read'].format(n=len(sched['unread'])))}</div></div>")
    if sched.get("offline"):
        parts.append(f"<div class='notice' role='status'>{icon('wifi_off')}<div>{esc(L['offline_mode'])}</div></div>")
    if any(e.get("confirmed_by") for k in (*SLOT_ORDER, "as_needed") for e in sched[k]):
        parts.append(f"<p class='legend'>{icon('check', 18)}{esc(L['confirmed_legend'])}</p>")
    slots = []
    for key in (*SLOT_ORDER, "as_needed"):
        if key in ("evening", "as_needed") and not sched[key]:
            continue
        rows = "".join(_med_html(e, L, img) for e in sched[key]) \
            or f"<li class='med none'>{esc(L['none'])}</li>"
        count = f"<span class='slot-count'>{esc(L['count_meds'].format(n=len(sched[key])))}</span>" if sched[key] else ""
        slots.append(f"<section class='slot slot-{key}'><div class='slot-head'><span class='slot-ico'>"
                     f"{icon(SLOT_ICONS[key], 28)}</span><h2>{esc(L[key])}{count}</h2></div>"
                     f"<ul class='meds'>{rows}</ul></section>")

    def ask_row(e):
        read = timing_summary(e["timing"], L)
        return (f"<li class='med'><div class='med-main'><span class='name'>"
                f"{esc(_with_strength(e['name'], e['strength']))}</span></div>"
                + (f"<div class='read'>{esc(L['read'])} {esc(read)}</div>" if read else "")
                + _uses_html(e, L)
                + f"<div class='why'>{esc(why_text(e, L))}</div>{img(e.get('crop'))}"
                + f"<details><summary>details</summary>{esc('; '.join(e['why']))}</details></li>")

    if sched["ask"]:
        rows = "".join(ask_row(e) for e in sched["ask"])
        slots.append(f"<section class='slot ask'><div class='slot-head'><span class='slot-ico'>{icon('alert', 28)}</span>"
                     f"<h2>{esc(L['ask'])}<span class='slot-count'>{esc(L['count_meds'].format(n=len(sched['ask'])))}"
                     f"</span></h2></div><div><ul class='meds'>{rows}</ul>"
                     f"<p class='confirm-line'>{esc(L['confirm'])}</p></div></section>")
    parts.append(f"<div class='timeline'>{''.join(slots)}</div>")
    parts.append(f"<a class='urgent' href='tel:108'>{icon('phone', 20)}<span>{esc(L['urgent'])}</span></a>")
    parts.append(f"<p class='card-foot'>{esc(L['footer'])}</p>")
    return (f"<!doctype html><html lang='{lang}'><head>{head('Medicine Card')}</head>"
            f"<body class='has-tabs'>{nav}<main class='sheet'>{''.join(parts)}</main></body></html>")
