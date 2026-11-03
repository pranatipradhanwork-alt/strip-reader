"""The Strip Reader web app.

Run:   python -m strip_reader.app          then open http://127.0.0.1:5060
       (on a phone on the same Wi-Fi: python -m strip_reader.app --host 0.0.0.0)

Flow:  home (take/choose photos, pick language)  ->  reading  ->  medicine card
       (read aloud, new photo, pharmacist check). "Try a sample" works without an API key.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, abort, redirect, request
from PIL import Image

from .card import LANGS, labels
from .leaflets import what_its_for
from .voice import speak
from .web import Run, card_page, page, review_page, topbar

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
MAX_PHOTOS = 6
RID_RE = re.compile(r"^[0-9]{8}-[0-9a-f]{8}$")


def default_reader(images: list[str], run_dir: Path, mock: str | None = None) -> dict:
    from .cli import run
    return run(images, run_dir, mock=mock)


def create_app(base_dir: str | Path = ROOT / "out" / "app", reader=default_reader,
               uses_fn=what_its_for, speak_fn=speak) -> Flask:
    base_dir = Path(base_dir)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_PHOTOS * 15 * 1024 * 1024

    def lang_arg() -> str:
        lang = request.values.get("lang", "or")
        return lang if lang in LANGS else "en"

    def get_run(rid: str) -> Run:
        if not RID_RE.match(rid) or not (base_dir / rid / "result.json").exists():
            abort(404)
        return Run(base_dir / rid)

    def new_run() -> tuple[str, Path]:
        rid = time.strftime("%Y%m%d") + "-" + secrets.token_hex(4)
        d = base_dir / rid
        (d / "photos").mkdir(parents=True)
        return rid, d

    def finish(rid: str, d: Path, images: list[str], mock: str | None = None):
        result = reader(images, d, mock)
        (d / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        return redirect(f"/r/{rid}/card?lang={lang_arg()}")

    @app.get("/")
    def home():
        return home_page(lang_arg())

    @app.post("/read")
    def read():
        lang = lang_arg()
        files = [f for f in request.files.getlist("photos") if f and f.filename][:MAX_PHOTOS]
        if not files:
            return home_page(lang, labels(lang)["no_photo"]), 400
        rid, d = new_run()
        saved = []
        for n, f in enumerate(files, 1):
            ext = Path(f.filename).suffix.lower() or ".jpg"
            path = d / "photos" / f"photo{n}{ext if ext in ('.jpg', '.jpeg', '.png', '.webp') else '.jpg'}"
            f.save(path)
            try:
                with Image.open(path) as im:
                    im.verify()
            except Exception:
                return home_page(lang, labels(lang)["bad_photo"]), 400
            saved.append(str(path))
        return finish(rid, d, saved)

    @app.post("/sample")
    def sample():
        rid, d = new_run()
        photo = d / "photos" / "sample_prescription.png"
        photo.write_bytes((FIX / "printed_rx.png").read_bytes())
        return finish(rid, d, [str(photo)], mock=str(FIX / "printed_rx_reply.json"))

    @app.get("/r/<rid>/card")
    def card(rid):
        return card_page(get_run(rid), lang_arg(), f"/r/{rid}", bool(request.args.get("voice")), uses_fn, speak_fn)

    @app.get("/r/<rid>/review")
    def review(rid):
        return review_page(get_run(rid), f"/r/{rid}", lang_arg())

    @app.post("/r/<rid>/review")
    def save_review(rid):
        run = get_run(rid)
        errors, forms = run.save_review(request.form)
        if errors:
            return review_page(run, f"/r/{rid}", lang_arg(), errors, forms), 400
        return redirect(f"/r/{rid}/card?lang={lang_arg()}")

    @app.post("/r/<rid>/undo/<path:iid>")
    def undo(rid, iid):
        get_run(rid).undo(iid)
        return redirect(f"/r/{rid}/review")

    @app.get("/r/<rid>/audio/<name>")
    def audio(rid, name):
        f = get_run(rid).audio_path(name)
        return Response(f.read_bytes(), mimetype="audio/mpeg") if f else Response(status=404)

    return app


def home_page(lang: str, error: str = "") -> str:
    L = labels(lang)
    esc = html.escape
    err = f"<p class='banner banner-warn' role='alert'>{esc(error)}</p>" if error else ""
    body = f"""
<section class="hero">
  <h1>{esc(L['read_btn'])}</h1>
  <p>{esc(L['app_tagline'])}</p>
</section>
{err}
<form class="upload" method="post" action="/read" enctype="multipart/form-data" id="upload">
  <input type="hidden" name="lang" value="{lang}">
  <input type="file" id="photos" name="photos" accept="image/*" capture="environment" multiple>
  <label for="photos" class="btn btn-ghost btn-big btn-block"><span class='i'>📷</span>{esc(L['take_photo'])}</label>
  <div class="previews" id="previews" aria-live="polite"></div>
  <p class="note" id="count" hidden></p>
  <button class="btn btn-primary btn-big btn-block" type="submit" id="go" style="margin-top:12px">
    {esc(L['read_btn'])} →</button>
</form>
<p class="or">— {esc(L['or_word'])} —</p>
<form method="post" action="/sample" class="sample">
  <input type="hidden" name="lang" value="{lang}">
  <button class="btn btn-ghost btn-block" type="submit"><span class='i'>✨</span>{esc(L['try_sample'])}</button>
</form>
<ol class="steps">
  <li>{esc(L['step1'])}</li>
  <li>{esc(L['step2'])}</li>
  <li>{esc(L['step3'])}</li>
</ol>
<p class="note">{esc(L['footer'])}<br>{esc(L['privacy'])}</p>
<div class="loading" id="loading" role="status">
  <div><div class="spinner"></div><h2 style="margin:0 0 6px">{esc(L['reading'])}</h2>
  <p class="note" style="margin:0">{esc(L['reading_wait'])}</p></div>
</div>
<script>
(() => {{
  const input = document.getElementById('photos'), previews = document.getElementById('previews'),
        count = document.getElementById('count'), loading = document.getElementById('loading');
  const chosen = {json.dumps(L['photos_chosen'])};
  input.addEventListener('change', () => {{
    previews.innerHTML = '';
    [...input.files].slice(0, {MAX_PHOTOS}).forEach(f => {{
      const img = document.createElement('img'); img.alt = ''; img.src = URL.createObjectURL(f); previews.appendChild(img);
    }});
    count.hidden = !input.files.length; count.textContent = chosen.replace('{{n}}', input.files.length);
  }});
  document.querySelectorAll('form.upload, form.sample').forEach(f =>
    f.addEventListener('submit', e => {{
      if (f.id === 'upload' && !input.files.length) {{ e.preventDefault(); input.click(); return; }}
      loading.classList.add('on');
    }}));
  window.addEventListener('pageshow', () => loading.classList.remove('on'));
}})();
</script>"""
    return page(L["app_name"], body, lang, topbar(lang, lambda c: f"/?lang={c}"))


def main() -> None:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="Run the Strip Reader web app.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5060)
    a = p.parse_args()
    create_app().run(host=a.host, port=a.port, debug=False)


if __name__ == "__main__":
    main()
