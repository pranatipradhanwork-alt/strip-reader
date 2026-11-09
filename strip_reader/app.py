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
import shutil
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, abort, redirect, request
from PIL import Image

from .card import LANGS, labels
from .leaflets import what_its_for
from .voice import speak
from .icons import icon
from .web import Run, card_page, history_link, page, review_page, tabbar, topbar

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
MAX_PHOTOS = 6
MAX_HISTORY = 50
COOKIE = "sr_runs"
RID_RE = re.compile(r"^[0-9]{8}-[0-9a-f]{8}$")


def default_reader(images: list[str], run_dir: Path, mock: str | None = None) -> dict:
    from .cli import run
    # The web app gives up after a minute (photo saved, "try again") instead of spinning for minutes.
    # The app uses the fast lite model (seconds, not minutes); the safety rules still catch bad reads.
    import os
    model = os.getenv("GEMINI_APP_MODEL", "gemini-3.5-flash-lite")
    return run(images, run_dir, mock=mock, model=model, budget_s=60)


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

    def my_runs() -> list[str]:
        """This browser's readings (newest first). Kept in a cookie: no accounts, no shared list."""
        ids = (request.cookies.get(COOKIE) or "").split(",")
        return [r for r in ids if RID_RE.match(r) and (base_dir / r / "result.json").exists()]

    def with_runs(resp, runs: list[str]):
        resp.set_cookie(COOKIE, ",".join(runs[:MAX_HISTORY]), max_age=365 * 24 * 3600, samesite="Lax", httponly=True)
        return resp

    def finish(rid: str, d: Path, images: list[str], mock: str | None = None):
        result = reader(images, d, mock)
        (d / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        runs = [rid] + [r for r in my_runs() if r != rid]
        return with_runs(redirect(f"/r/{rid}/card?lang={lang_arg()}"), runs)

    @app.get("/history")
    def history():
        summaries = []
        for rid in my_runs():
            run = Run(base_dir / rid)
            sched = run.schedule()
            on_card = len({e["id"] for k in ("morning", "afternoon", "evening", "night", "as_needed") for e in sched[k]})
            when = time.strftime("%d %b %Y, %H:%M", time.localtime((run.dir / "result.json").stat().st_mtime))
            photos = len([f for f in (run.dir / "photos").iterdir() if f.is_file()]) if (run.dir / "photos").exists() else 0
            summaries.append({"rid": rid, "when": when, "photos": photos, "on_card": on_card, "ask": len(sched["ask"])})
        return history_page(lang_arg(), summaries)

    @app.post("/r/<rid>/delete")
    def delete(rid):
        runs = my_runs()
        if rid not in runs:          # only the browser that made a reading can delete it
            abort(404)
        shutil.rmtree(base_dir / rid, ignore_errors=True)
        return with_runs(redirect(f"/history?lang={lang_arg()}"), [r for r in runs if r != rid])

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
        return card_page(get_run(rid), lang_arg(), f"/r/{rid}", bool(request.args.get("voice")), uses_fn, speak_fn,
                         can_retry=True)

    @app.post("/r/<rid>/retry")
    def retry(rid):
        """Gemini was busy or offline: read the saved photos again."""
        run = get_run(rid)
        photos = sorted(str(f) for f in (run.dir / "photos").iterdir() if f.is_file())
        return finish(rid, run.dir, photos)

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
    err = (f"<div class='notice' role='alert'>{icon('alert')}<div>{esc(error)}</div></div>") if error else ""
    body = f"""
<section class="hero">
  <div>
    <p class="kicker">{esc(L['kicker'])}</p>
    <h1>{esc(L['hero_title'])}</h1>
    <p class="lede">{esc(L['app_tagline'])}</p>
    <ul class="points">
      <li>{icon('check')}<span>{esc(L['point1'])}</span></li>
      <li>{icon('check')}<span>{esc(L['point2'])}</span></li>
      <li>{icon('check')}<span>{esc(L['point3'])}</span></li>
    </ul>
  </div>
  <div class="scan">
    <h2>{esc(L['scan_title'])}</h2>
    <p class="sub">{esc(L['scan_sub'])}</p>
    {err}
    <form class="upload stack" method="post" action="/read" enctype="multipart/form-data" id="upload">
      <input type="hidden" name="lang" value="{lang}">
      <input type="file" id="photos" name="photos" accept="image/*" multiple>
      <input type="file" id="phonecam" accept="image/*" capture="environment">
      <button type="button" class="btn btn-dark btn-block" id="camera">{icon('camera')}{esc(L['take_photo'])}</button>
      <label for="photos" class="btn btn-outline btn-block">{icon('image')}{esc(L['choose_photo'])}</label>
      <div class="previews" id="previews" aria-live="polite"></div>
      <p class="count" id="count" hidden></p>
      <button class="btn btn-primary btn-block" type="submit" id="go" hidden>{esc(L['read_btn'])}{icon('arrow')}</button>
    </form>
    <div class="or-line">{esc(L['or_word'])}</div>
    <form method="post" action="/sample" class="sample">
      <input type="hidden" name="lang" value="{lang}">
      <button class="btn btn-quiet btn-block" type="submit">{icon('sample')}{esc(L['try_sample'])}</button>
    </form>
    <p class="privacy">{esc(L['privacy'])}</p>
  </div>
</section>
<section class="steps">
  <h2>{esc(L['how_title'])}</h2>
  <ol><li>{esc(L['step1'])}</li><li>{esc(L['step2'])}</li><li>{esc(L['step3'])}</li></ol>
  <p class="disclaimer">{esc(L['footer'])}</p>
</section>
<div class="cam" id="cam" hidden>
  <video id="video" playsinline autoplay muted></video>
  <p class="cam-hint">{esc(L['cam_hint'])}</p>
  <div class="btn-row">
    <button type="button" class="btn btn-primary" id="shoot">{icon('camera')}{esc(L['capture'])}</button>
    <button type="button" class="btn btn-quiet" id="camdone">{esc(L['done'])}</button>
  </div>
</div>
<div class="loading" id="loading" role="status" aria-live="polite">
  <div class="reading">
    <div class="scan-box" id="scanbox" hidden><img id="scanimg" alt=""><span class="scan-line"></span></div>
    <div class="strip-anim" id="pillanim" aria-hidden="true">
      <svg viewBox="0 0 240 150" width="240" height="150">
        <defs>
          <linearGradient id="foil" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" class="foil-a"/><stop offset=".5" class="foil-b"/><stop offset="1" class="foil-a"/>
          </linearGradient>
          <clipPath id="stripclip"><rect x="8" y="8" width="224" height="134" rx="14"/></clipPath>
        </defs>
        <rect class="foil" x="8" y="8" width="224" height="134" rx="14" fill="url(#foil)"/>
        <rect class="tab" x="28" y="26" width="26" height="30" rx="13" style="animation-delay:0.00s"/><rect class="tab" x="66" y="26" width="26" height="30" rx="13" style="animation-delay:0.36s"/><rect class="tab" x="104" y="26" width="26" height="30" rx="13" style="animation-delay:0.72s"/><rect class="tab" x="142" y="26" width="26" height="30" rx="13" style="animation-delay:1.08s"/><rect class="tab" x="180" y="26" width="26" height="30" rx="13" style="animation-delay:1.44s"/><rect class="tab" x="28" y="68" width="26" height="30" rx="13" style="animation-delay:0.18s"/><rect class="tab" x="66" y="68" width="26" height="30" rx="13" style="animation-delay:0.54s"/><rect class="tab" x="104" y="68" width="26" height="30" rx="13" style="animation-delay:0.90s"/><rect class="tab" x="142" y="68" width="26" height="30" rx="13" style="animation-delay:1.26s"/><rect class="tab" x="180" y="68" width="26" height="30" rx="13" style="animation-delay:1.62s"/>
        <rect class="label" x="22" y="118" width="120" height="8" rx="4"/>
        <rect class="label" x="150" y="118" width="64" height="8" rx="4"/>
        <g clip-path="url(#stripclip)"><rect class="beam" x="8" y="0" width="224" height="4" rx="2"/></g>
      </svg>
    </div>
    <h2 id="stagetext">{esc(L['step_upload'])}…</h2>
    <ol class="stages" id="stages">
      <li>{esc(L['step_upload'])}</li><li>{esc(L['step_scan'])}</li><li>{esc(L['step_read'])}</li>
      <li>{esc(L['step_check'])}</li><li>{esc(L['step_card'])}</li>
    </ol>
    <p class="wait-note">{esc(L['reading_wait'])}</p>
  </div>
</div>
{SCRIPT}"""
    script = SCRIPT.replace("__CHOSEN__", json.dumps(L["photos_chosen"])).replace(
        "__BLOCKED__", json.dumps(L["camera_blocked"])).replace("__MAX__", str(MAX_PHOTOS))
    return page(L["app_name"], body.replace("{SCRIPT}", script), lang,
                topbar(lang, lambda c: f"/?lang={c}", right=history_link(lang, "read")), tabbar(lang, "read"))


def history_page(lang: str, runs: list[dict]) -> str:
    L = labels(lang)
    esc = html.escape
    rows = []
    for r in runs:
        rows.append(
            f"<li class='med' style='display:flex;gap:16px;align-items:center;flex-wrap:wrap'>"
            f"<div style='flex:1;min-width:200px'><div class='name'>{esc(r['when'])}</div>"
            f"<div class='why'>{esc(L['photos_n'].format(n=r['photos']))} · "
            f"{esc(L['meta'].format(a=r['on_card'], b=r['ask']))}</div></div>"
            f"<a class='btn btn-primary' href='/r/{r['rid']}/card?lang={lang}'>{esc(L['open'])}</a>"
            f"<form method='post' action='/r/{r['rid']}/delete?lang={lang}' style='margin:0'>"
            f"<button class='btn btn-quiet' type='submit'>{esc(L['delete'])}</button></form></li>")
    listing = (f"<ul class='meds' style='margin-top:20px'>{''.join(rows)}</ul>" if rows
               else f"<p class='lede' style='margin-top:20px'>{esc(L['history_empty'])}</p>")
    body = (f"<div class='sheet' style='padding:0'><header class='page-head'><p class='kicker'>{esc(L['history'])}</p>"
            f"<h1>{esc(L['history_title'])}</h1><p class='meta'>{esc(L['history_note'])}</p></header>{listing}"
            f"<p style='margin:28px 0 48px'><a class='btn btn-outline' href='/?lang={lang}'>{icon('camera')}"
            f"{esc(L['new_photo'])}</a></p></div>")
    return page(L["history"], body, lang, topbar(lang, lambda c: f"/history?lang={c}", right=history_link(lang, "history")),
                tabbar(lang, "history"))


SCRIPT = """<script>
(() => {
  const input = document.getElementById('photos'), phonecam = document.getElementById('phonecam'),
        previews = document.getElementById('previews'), count = document.getElementById('count'),
        loading = document.getElementById('loading'), cam = document.getElementById('cam'),
        video = document.getElementById('video');
  const chosen = __CHOSEN__, blocked = __BLOCKED__, MAX = __MAX__;
  let files = [], stream = null;

  // One list of photos from the gallery and the camera, put back into the form's file input.
  function sync() {
    const dt = new DataTransfer();
    files.slice(0, MAX).forEach(f => dt.items.add(f));
    input.files = dt.files;
    previews.innerHTML = '';
    files.slice(0, MAX).forEach(f => {
      const img = document.createElement('img'); img.alt = ''; img.src = URL.createObjectURL(f); previews.appendChild(img);
    });
    count.hidden = !files.length; count.textContent = chosen.replace('{n}', Math.min(files.length, MAX));
    document.getElementById('go').hidden = !files.length;   // one clear next step at a time
  }
  input.addEventListener('change', () => { files = files.concat([...input.files]); sync(); });
  phonecam.addEventListener('change', () => { files = files.concat([...phonecam.files]); sync(); });

  // Live camera in the page (laptops and phones); if not allowed, the phone's own camera app.
  document.getElementById('camera').addEventListener('click', async () => {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1920 } } });
      video.srcObject = stream; cam.hidden = false;
    } catch (e) {
      if (/Android|iPhone|iPad/i.test(navigator.userAgent)) phonecam.click(); else alert(blocked);
    }
  });
  document.getElementById('shoot').addEventListener('click', () => {
    const c = document.createElement('canvas');
    c.width = video.videoWidth; c.height = video.videoHeight;
    c.getContext('2d').drawImage(video, 0, 0);
    c.toBlob(b => {
      files.push(new File([b], 'camera-' + Date.now() + '.jpg', { type: 'image/jpeg' })); sync();
      cam.classList.add('flash'); setTimeout(() => cam.classList.remove('flash'), 150);
    }, 'image/jpeg', 0.9);
  });
  function stopCam() { if (stream) stream.getTracks().forEach(t => t.stop()); stream = null; cam.hidden = true; }
  document.getElementById('camdone').addEventListener('click', stopCam);

  document.querySelectorAll('form.upload, form.sample').forEach(f =>
    f.addEventListener('submit', e => {
      if (f.id === 'upload' && !files.length) { e.preventDefault(); input.click(); return; }
      stopCam(); startReading(f.id === 'upload' ? files[0] : null);
    }));
  // Reading screen: the person's own photo with a moving scan line (or a pill animation for
  // the sample), and the stages ticked off one by one while the server works.
  function startReading(photo) {
    const box = document.getElementById('scanbox'), pill = document.getElementById('pillanim'),
          items = [...document.querySelectorAll('#stages li')], text = document.getElementById('stagetext');
    if (photo) { document.getElementById('scanimg').src = URL.createObjectURL(photo); box.hidden = false; pill.hidden = true; }
    let i = 0;
    const show = () => {
      items.forEach((li, k) => { li.className = k < i ? 'st-done' : (k === i ? 'st-now' : ''); });
      text.textContent = items[i].textContent + '…';
    };
    show();
    loading.classList.add('on');
    const t = setInterval(() => { if (i < items.length - 1) { i++; show(); } else clearInterval(t); }, 2600);
  }
  window.addEventListener('pageshow', () => loading.classList.remove('on'));
})();
</script>"""


def main() -> None:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="Run the Strip Reader web app.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5060)
    a = p.parse_args()
    create_app().run(host=a.host, port=a.port, debug=False)


if __name__ == "__main__":
    main()
