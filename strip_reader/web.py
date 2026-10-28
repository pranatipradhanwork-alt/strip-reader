"""Pharmacist review page.

Run:  python -m strip_reader.web out/real/all      (the folder with result.json)
Open: http://127.0.0.1:5050

The pharmacist sees every item the app was unsure about, with the picture of
the label, fixes what's wrong, and confirms it. The card is rebuilt each time.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from flask import Flask, Response, redirect, request

from . import confirm
from .card import LANGS, _img_tag, build_schedule, card_html


def create_app(run_dir: str | Path) -> Flask:
    run_dir = Path(run_dir)
    app = Flask(__name__)
    conf_path = run_dir / "confirmations.json"

    def result() -> dict:
        return json.loads((run_dir / "result.json").read_text(encoding="utf-8"))

    def items():
        for img in result()["images"]:
            for n, i in enumerate(img["items"], 1):
                yield confirm.item_id(img["image"], n), i

    def ask_ids() -> set:
        """Items that are not on the card yet: the ones the pharmacist should see."""
        sched = build_schedule(result(), confirm.load(conf_path))
        return {e["id"] for e in sched["ask"]}

    def rebuild() -> None:
        sched = build_schedule(result(), confirm.load(conf_path))
        (run_dir / "schedule.json").write_text(json.dumps(sched, indent=2, ensure_ascii=False))

    @app.get("/")
    def review():
        return _page(list(items()), confirm.load(conf_path), errors={}, form={}, ask=ask_ids())

    @app.post("/")
    def save():
        confs = confirm.load(conf_path)
        all_items = dict(items())
        errors, forms = {}, {}
        for iid, item in all_items.items():
            form = {k: request.form.get(f"{iid}|{k}", "") for k in ("action", "name", "strength", "timing_text")}
            form["by"] = request.form.get("by", "")
            forms[iid] = form
            if form["action"] in ("", "leave"):
                continue
            rec, errs = confirm.validate(item, form)
            if errs:
                errors[iid] = errs
            elif rec:
                confs[iid] = rec
        if errors:
            # Save nothing until every entry is valid, so nothing half-checked reaches the card.
            return _page(list(all_items.items()), confirm.load(conf_path), errors, forms, ask_ids()), 400
        confirm.save(conf_path, confs)
        rebuild()
        return redirect("/card")

    @app.post("/undo/<path:iid>")
    def undo(iid):
        confs = confirm.load(conf_path)
        confs.pop(iid, None)
        confirm.save(conf_path, confs)
        rebuild()
        return redirect("/")

    @app.get("/card")
    def card():
        lang = request.args.get("lang", "or")
        lang = lang if lang in LANGS else "en"
        sched = build_schedule(result(), confirm.load(conf_path))
        page = card_html(sched, lang)
        switch = " · ".join(f'<a href="/card?lang={l}">{l}</a>' for l in LANGS)
        bar = f'<nav style="font:16px system-ui;padding:8px 16px">Language: {switch} · <a href="/">← pharmacist review</a></nav>'
        return Response(page.replace("<body>", "<body>" + bar, 1), mimetype="text/html")

    return app


def _page(items: list, confs: dict, errors: dict, form: dict, ask: set) -> str:
    esc = html.escape
    rows = []
    for iid, i in items:
        c = confs.get(iid)
        if iid not in ask and not c:
            continue  # already on the card; nothing for the pharmacist to do
        f = form.get(iid, {})
        val = lambda k, default: esc(f.get(k) or default or "")
        name_default = i.get("matched_name") or i.get("name_as_read")
        status = ""
        if c:
            label = "confirmed" if c["action"] == "confirm" else "marked not a medicine"
            status = (f"<div class='done'>✓ {label} by {esc(c['by'])}"
                      f"<button formaction='/undo/{esc(iid)}' formmethod='post' formnovalidate>undo</button></div>")
        err = "".join(f"<div class='err'>{esc(e)}</div>" for e in errors.get(iid, []))
        why = "; ".join(i.get("reasons") or [])
        opts = "".join(
            f"<label><input type='radio' name='{esc(iid)}|action' value='{a}'"
            f"{' checked' if (f.get('action') or 'leave') == a else ''}> {t}</label>"
            for a, t in (("leave", "leave for doctor"), ("confirm", "confirm"), ("not_medicine", "not a medicine")))
        rows.append(f"""
<fieldset class='{"is-done" if c else ""}'>
  <legend>{esc(iid)}: read as <b>{esc(i.get('name_as_read') or '?')}</b> ({esc(i.get('source', ''))}, confidence {i.get('confidence', 0):.2f})</legend>
  {_img_tag(i.get('crop_path'))}
  <div class='why'>{esc(why)}</div>
  {status}{err}
  <div class='grid'>
    <label>Name <input name='{esc(iid)}|name' value='{val("name", c.get("name") if c else name_default)}'></label>
    <label>Strength <input name='{esc(iid)}|strength' placeholder='e.g. 500 mg' value='{val("strength", c.get("strength") if c else i.get("strength"))}'></label>
    <label>Timing <input name='{esc(iid)}|timing_text' placeholder='e.g. 1-0-1 after food x 5 days' value='{val("timing_text", c.get("timing_text") if c else i.get("timing_text"))}'></label>
  </div>
  <div class='actions'>{opts}</div>
</fieldset>""")
    body = "".join(rows) or "<p>Nothing to check. Every item was read clearly.</p>"
    by = esc(next((v.get("by") for v in form.values() if v.get("by")), ""))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pharmacist Review</title>
<style>
:root {{ --bg:#fafafa; --fg:#111; --muted:#666; --line:#ddd; --ok:#067647; --err:#b42318; --card:#fff; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111; --fg:#eee; --muted:#aaa; --line:#333; --ok:#47cd89; --err:#f97066; --card:#1b1b1b; }} }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--fg); font:16px/1.45 system-ui, sans-serif; }}
main {{ max-width:760px; margin:0 auto; }}
fieldset {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin:0 0 14px; padding:12px 14px; }}
fieldset.is-done {{ border-color:var(--ok); }}
legend {{ padding:0 6px; }}
.crop {{ display:block; max-width:100%; max-height:120px; border:1px solid var(--line); border-radius:6px; margin:4px 0; }}
.why {{ color:var(--muted); font-size:14px; }}
.done {{ color:var(--ok); font-weight:600; margin:6px 0; }}
.done button {{ margin-left:8px; }}
.err {{ color:var(--err); font-weight:600; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:8px; margin:8px 0; }}
.grid input, .top input {{ width:100%; box-sizing:border-box; padding:8px; font:inherit; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); }}
.actions label {{ margin-right:14px; white-space:nowrap; }}
.top {{ position:sticky; top:0; background:var(--bg); padding:8px 0 12px; border-bottom:1px solid var(--line); margin-bottom:12px; }}
button {{ font:inherit; padding:8px 14px; border-radius:6px; }}
</style></head><body><main>
<form method="post">
<div class="top">
  <h1 style="margin:0 0 6px;font-size:22px">Pharmacist review</h1>
  <p style="margin:0 0 8px;color:var(--muted)">Check each label picture. Fix anything wrong, then pick "confirm". Only confirmed items go on the card.</p>
  <label>Your name <input name="by" value="{by}" required></label>
  <p style="margin:8px 0 0"><button type="submit">Save and show card</button> <a href="/card">see card</a></p>
</div>
{body}
</form></main></body></html>"""


if __name__ == "__main__":
    run = sys.argv[1] if len(sys.argv) > 1 else "out/real/all"
    create_app(run).run(host="127.0.0.1", port=5050, debug=False)
