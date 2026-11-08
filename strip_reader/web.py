"""Web pages: the medicine card, the pharmacist review page, and the shared top bar.

Two ways to run:
  python -m strip_reader.app                 the full app (upload a photo -> card)   see app.py
  python -m strip_reader.web out/run1        review one finished run (CLI output)

Both use `Run` below, so the card and the review page behave the same.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from flask import Flask, Response, redirect, request

from . import confirm
from .card import LANGS, _img_tag, build_schedule, card_html, labels
from .card import head
from .icons import icon
from .leaflets import what_its_for
from .voice import speak

LANG_LABELS = {"en": "English", "hi": "हिन्दी", "or": "ଓଡ଼ିଆ"}


class Run:
    """One reading: a folder with result.json (+ confirmations.json, audio)."""

    def __init__(self, run_dir: str | Path):
        self.dir = Path(run_dir)
        self.conf_path = self.dir / "confirmations.json"

    def result(self) -> dict:
        return json.loads((self.dir / "result.json").read_text(encoding="utf-8"))

    def items(self):
        for img in self.result()["images"]:
            for n, i in enumerate(img["items"], 1):
                yield confirm.item_id(img["image"], n), i

    def confirmations(self) -> dict:
        return confirm.load(self.conf_path)

    def schedule(self, lang: str = "en", uses_fn=None) -> dict:
        return build_schedule(self.result(), self.confirmations(),
                              (lambda g: uses_fn(g, lang)) if uses_fn else None)

    def ask_ids(self) -> set:
        """Items not on the card yet: the ones the pharmacist should see."""
        return {e["id"] for e in self.schedule()["ask"]}

    def save_review(self, form) -> tuple[dict, dict]:
        """Validate every pharmacist entry; save only if all are valid. Returns (errors, forms)."""
        confs = self.confirmations()
        errors, forms = {}, {}
        for iid, item in self.items():
            f = {k: form.get(f"{iid}|{k}", "") for k in ("action", "name", "strength", "timing_text")}
            f["by"] = form.get("by", "")
            forms[iid] = f
            if f["action"] in ("", "leave"):
                continue
            rec, errs = confirm.validate(item, f)
            if errs:
                errors[iid] = errs
            elif rec:
                confs[iid] = rec
        if not errors:   # nothing half-checked ever reaches the card
            confirm.save(self.conf_path, confs)
        return errors, forms

    def undo(self, iid: str) -> None:
        confs = self.confirmations()
        confs.pop(iid, None)
        confirm.save(self.conf_path, confs)

    def audio_path(self, name: str) -> Path | None:
        f = self.dir / Path(name).name          # never a path outside the run folder
        return f if f.suffix == ".mp3" and f.exists() else None


# ---------- shared page pieces ----------

def topbar(lang: str, lang_href, right: str = "") -> str:
    """Logo + Read/History (left); language menu + theme toggle (right).

    lang_href(code) gives the link for each language. `right` holds the Read/History links.
    """
    esc = html.escape
    options = "".join(
        f"<a href='{esc(lang_href(code))}' role='menuitem'{' aria-current=\'true\'' if code == lang else ''}>"
        f"<span>{LANG_LABELS.get(code, code)}</span>{icon('check', 16) if code == lang else ''}</a>"
        for code in LANGS)
    menu = (f"<details class='lang-menu'><summary aria-label='Language'>{icon('globe', 18)}"
            f"<span class='lang-name'>{LANG_LABELS.get(lang, lang)}</span>{icon('chevron', 16)}</summary>"
            f"<div class='lang-list' role='menu'>{options}</div></details>")
    toggle = (f"<button class='theme-toggle' type='button' aria-label='Light or dark theme'>"
              f"<span class='sun'>{icon('sun', 20)}</span><span class='moon'>{icon('moon', 20)}</span></button>")
    return (f"<header class='topbar'><div class='wrap'><a class='brand' href='/'><span class='brand-mark'>"
            f"{icon('logo', 20)}</span><span class='brand-text'>Strip Reader</span></a>{right}"
            f"<span class='spacer'></span>{menu}{toggle}</div></header>")


def tabbar(lang: str, current: str = "read") -> str:
    """Bottom tab bar on phones (Read / History), like a native app."""
    L = labels(lang)
    esc = html.escape
    tab = lambda key, href, ico, text: (f"<a href='{href}'{' aria-current=\'page\'' if current == key else ''}>"
                                        f"{icon(ico, 22)}{esc(text)}</a>")
    return (f"<nav class='tabbar' aria-label='Main'>{tab('read', f'/?lang={lang}', 'camera', L['read_tab'])}"
            f"{tab('history', f'/history?lang={lang}', 'clock', L['history'])}</nav>")


def page(title: str, body: str, lang: str = "en", bar: str = "", tabs: str = "") -> str:
    return (f"<!doctype html><html lang='{lang}'><head>{head(title)}</head>"
            f"<body class='has-tabs'>{bar}<main class='wrap'>{body}</main>{tabs}</body></html>")


def card_page(run: Run, lang: str, base: str, voice: bool, uses_fn, speak_fn, can_retry: bool = False) -> str:
    """The card with the app's top bar and buttons. `base` is the URL prefix of this run."""
    L = labels(lang)
    esc = html.escape
    sched = run.schedule(lang, uses_fn)
    audio = None
    if voice and speak_fn and speak_fn(sched, lang, run.dir / f"card-{lang}.mp3"):
        audio = f"{base}/audio/card-{lang}.mp3"
    retry = ""
    if can_retry and sched.get("queued"):
        retry = (f"<form method='post' action='{esc(base)}/retry?lang={lang}' style='margin:0 0 12px'>"
                 f"<button class='btn btn-primary btn-block' type='submit'>{icon('refresh')}"
                 f"{esc(L['try_again'])}</button></form>")
    actions = retry + (f"<div class='btn-row'>"
               f"<a class='btn btn-primary' href='{esc(base)}/card?lang={lang}&voice=1'>{icon('speaker')}{esc(L['read_aloud'])}</a>"
               f"<a class='btn btn-outline' href='/?lang={lang}'>{icon('camera')}{esc(L['new_photo'])}</a>"
               f"<a class='btn btn-quiet' href='{esc(base)}/review?lang={lang}'>{icon('check_user')}{esc(L['pharmacist'])}</a></div>")
    bar = topbar(lang, lambda c: f"{base}/card?lang={c}", right=history_link(lang) if can_retry else "")
    if can_retry:   # the full app (not the single-run review tool) has the bottom tabs
        bar += tabbar(lang, "")
    return card_html(sched, lang, audio=audio, nav=bar, actions=actions)


def history_link(lang: str, current: str = "") -> str:
    """Top-bar links (laptops): Read and History, current page highlighted. Phones use the bottom tab bar."""
    L = labels(lang)
    link = lambda key, href, text: (f"<a class='navlink' href='{href}'"
                                    f"{' aria-current=\'page\'' if key == current else ''}>{html.escape(text)}</a>")
    return (f"<nav class='navlinks'>{link('read', f'/?lang={lang}', L['read_tab'])}"
            f"{link('history', f'/history?lang={lang}', L['history'])}</nav>")


def review_page(run: Run, base: str, lang: str = "en", errors=None, form=None) -> str:
    esc = html.escape
    errors, form = errors or {}, form or {}
    confs, ask = run.confirmations(), run.ask_ids()
    rows = []
    for iid, i in run.items():
        c = confs.get(iid)
        if iid not in ask and not c:
            continue  # already on the card; nothing for the pharmacist to do
        f = form.get(iid, {})
        val = lambda k, default: esc(f.get(k) or default or "")
        name_default = i.get("matched_name") or i.get("name_as_read")
        status = ""
        if c:
            label = "confirmed" if c["action"] == "confirm" else "marked not a medicine"
            status = (f"<div class='done'>{icon('check', 18)}{label} by {esc(c['by'])}<button formaction='{esc(base)}/undo/{esc(iid)}' "
                      f"formmethod='post' formnovalidate>undo</button></div>")
        err = "".join(f"<div class='err'>{esc(e)}</div>" for e in errors.get(iid, []))
        why = "; ".join(i.get("reasons") or [])
        opts = "".join(
            f"<label><input type='radio' name='{esc(iid)}|action' value='{a}'"
            f"{' checked' if (f.get('action') or 'leave') == a else ''}> {t}</label>"
            for a, t in (("leave", "leave for doctor"), ("confirm", "confirm"), ("not_medicine", "not a medicine")))
        rows.append(f"""
<section class='item {"is-done" if c else ""}'>
  <div class='item-head'>Read as <b>{esc(i.get('name_as_read') or '?')}</b>
    <span class='why'>· {esc(i.get('source', ''))} · confidence {i.get('confidence', 0):.2f}</span></div>
  {_img_tag(i.get('crop_path'))}
  <div class='why'>{esc(why)}</div>
  {status}{err}
  <div class='grid'>
    <label>Name <input name='{esc(iid)}|name' value='{val("name", c.get("name") if c else name_default)}'></label>
    <label>Strength <input name='{esc(iid)}|strength' placeholder='e.g. 500 mg' value='{val("strength", c.get("strength") if c else i.get("strength"))}'></label>
    <label>Timing <input name='{esc(iid)}|timing_text' placeholder='e.g. 1-0-1 after food x 5 days' value='{val("timing_text", c.get("timing_text") if c else i.get("timing_text"))}'></label>
  </div>
  <div class='choices'>{opts}</div>
</section>""")
    body_rows = "".join(rows) or "<p class='note'>Nothing to check. Every item was read clearly.</p>"
    by = esc(next((v.get("by") for v in form.values() if v.get("by")), ""))
    body = f"""<form method="post" action="{esc(base)}/review">
<div class="review-top">
  <h1>Pharmacist check</h1>
  <p>Look at each label picture. Fix anything wrong, then choose "confirm". Only confirmed items go on the card.</p>
  <div class="field"><label>Your name <input name="by" value="{by}" required></label></div>
  <div class="btn-row" style="margin-top:16px"><button class="btn btn-primary" type="submit">Save and show card</button>
  <a class="btn btn-outline" href="{esc(base)}/card?lang={lang}">See card</a></div>
</div>
{body_rows}
</form>"""
    return page("Pharmacist check", body, "en", topbar(lang, lambda c: f"{base}/review?lang={c}"))


# ---------- single-run app (python -m strip_reader.web out/run1) ----------

def create_app(run_dir: str | Path, uses_fn=what_its_for, speak_fn=speak) -> Flask:
    """Review one finished run. uses_fn / speak_fn = None keeps tests offline."""
    run = Run(run_dir)
    app = Flask(__name__)

    def lang_arg(default="or"):
        lang = request.args.get("lang", default)
        return lang if lang in LANGS else "en"

    @app.get("/")
    @app.get("/review")
    def review():
        return review_page(run, "", lang_arg())

    @app.post("/")
    @app.post("/review")
    def save():
        errors, forms = run.save_review(request.form)
        if errors:
            return review_page(run, "", lang_arg(), errors, forms), 400
        return redirect("/card")

    @app.post("/undo/<path:iid>")
    def undo(iid):
        run.undo(iid)
        return redirect("/")

    @app.get("/card")
    def card():
        return card_page(run, lang_arg(), "", bool(request.args.get("voice")), uses_fn, speak_fn)

    @app.get("/audio/<name>")
    def audio_file(name):
        f = run.audio_path(name)
        return Response(f.read_bytes(), mimetype="audio/mpeg") if f else Response(status=404)

    return app


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "out/real/all"
    create_app(folder).run(host="127.0.0.1", port=5050, debug=False)
