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
from flask import Flask, Response, abort, jsonify, redirect, request, session
from PIL import Image

from .card import LANGS, labels
from .leaflets import what_its_for
from .voice import speak
from .icons import icon
from .pharmacists import Registry
from . import chat
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


LOGIN_TRIES, LOGIN_WINDOW_S = 5, 600   # 5 wrong passwords in 10 minutes -> wait


def secret_key(folder: Path) -> str:
    """Signs the pharmacist's login cookie. From SECRET_KEY, else made once and kept in instance/."""
    import os
    if os.getenv("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    f = folder / "secret_key"
    if not f.exists():
        folder.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return f.read_text().strip()


def create_app(base_dir: str | Path = ROOT / "out" / "app", reader=default_reader,
               uses_fn=what_its_for, speak_fn=speak, registry: Registry | None = None) -> Flask:
    base_dir = Path(base_dir)
    registry = registry or Registry()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_PHOTOS * 15 * 1024 * 1024
    app.config.update(SECRET_KEY=secret_key(registry.dir), SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=12 * 3600)
    failed: dict[str, list[float]] = {}

    def pharmacist() -> dict | None:
        """The logged-in pharmacist, re-read every time so a disabled account stops at once."""
        reg = session.get("pharmacist")
        return registry.get(reg) if reg else None

    def need_login(rid: str):
        return redirect(f"/pharmacist/login?next=/r/{rid}/review%3Flang={lang_arg()}")

    @app.get("/pharmacist/login")
    def login_form():
        return login_page(request.args.get("next", ""))

    @app.post("/pharmacist/login")
    def login():
        ip, now = request.remote_addr or "?", time.time()
        tries = [t for t in failed.get(ip, []) if now - t < LOGIN_WINDOW_S]
        nxt = request.form.get("next", "")
        if len(tries) >= LOGIN_TRIES:
            return login_page(nxt, "Too many wrong tries. Please wait 10 minutes."), 429
        rec, problem = registry.check(request.form.get("reg_no", ""), request.form.get("password", ""))
        if not rec:
            if problem == "wrong":
                failed[ip] = tries + [now]
            msg = ("Your application is still being verified. You can sign in once it is approved."
                   if problem == "pending" else "Registration number or password is wrong.")
            return login_page(nxt, msg), 401
        failed.pop(ip, None)
        session.clear()
        session.permanent = True
        session["pharmacist"] = rec["reg_no"]
        return redirect(nxt if re.match(r"^/r/[0-9]{8}-[0-9a-f]{8}/(review|chat)(\?lang=[a-z]{2})?$", nxt)
                        else "/pharmacist/inbox")

    @app.post("/pharmacist/logout")
    def logout():
        session.clear()
        return redirect("/")

    @app.get("/pharmacist/apply")
    def apply_form():
        return apply_page()

    @app.post("/pharmacist/apply")
    def apply():
        f = request.files.get("certificate")
        errors = registry.apply(request.form.get("name", ""), request.form.get("reg_no", ""),
                                request.form.get("council", ""), request.form.get("password", ""),
                                f.filename if f else "", f.read() if f else b"")
        if errors:
            return apply_page(request.form, errors), 400
        return apply_page(done=True)

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
            summaries.append({"rid": rid, "when": when, "photos": photos, "on_card": on_card, "ask": len(sched["ask"]),
                              "chat": chat.status(chat.load(run.dir))})
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
        for f in files:
            try:
                saved += save_upload(f, d / "photos", len(saved) + 1, MAX_PHOTOS - len(saved))
            except Exception:
                return home_page(lang, labels(lang)["bad_photo"]), 400
            if len(saved) >= MAX_PHOTOS:
                break
        return finish(rid, d, saved)

    @app.post("/sample")
    def sample():
        rid, d = new_run()
        photo = d / "photos" / "sample_prescription.png"
        photo.write_bytes((FIX / "printed_rx.png").read_bytes())
        return finish(rid, d, [str(photo)], mock=str(FIX / "printed_rx_reply.json"))

    @app.get("/r/<rid>/card")
    def card(rid):
        run = get_run(rid)
        msgs = chat.load(run.dir) if rid in my_runs() else None    # only the person who asked sees the chat link
        return card_page(run, lang_arg(), f"/r/{rid}", bool(request.args.get("voice")), uses_fn, speak_fn,
                         can_retry=True, chat_state=None if msgs is None else chat.status(msgs))

    # ---------- Ask a pharmacist ----------

    def chat_viewer(rid: str) -> tuple[str, dict | None]:
        """Who may see this chat: the browser that made the reading, or a verified pharmacist."""
        ph = pharmacist()
        if ph:
            return "pharmacist", ph
        if rid in my_runs():
            return "patient", None
        abort(404)

    def chat_page(rid: str, error: str = ""):
        run = get_run(rid)
        viewer, ph = chat_viewer(rid)
        lang = lang_arg() if viewer == "patient" else "en"
        L = labels(lang)
        bar = topbar(lang, lambda c: f"/r/{rid}/chat?lang={c}",
                     right=history_link(lang) if viewer == "patient" else pharmacist_links())
        return page(L["ask_pharmacist"], chat.chat_body(rid, run, lang, viewer, error), lang, bar,
                    tabbar(lang, "") if viewer == "patient" else "")

    @app.get("/r/<rid>/chat")
    def chat_get(rid):
        return chat_page(rid)

    @app.post("/r/<rid>/chat")
    def chat_post(rid):
        get_run(rid)
        viewer, ph = chat_viewer(rid)
        problem = chat.add(base_dir / rid, viewer, request.form.get("text", ""), ph)
        if problem:
            return chat_page(rid, problem), 400
        return redirect(f"/r/{rid}/chat?lang={lang_arg() if viewer == 'patient' else 'en'}")

    @app.get("/r/<rid>/chat/messages")
    def chat_messages(rid):
        get_run(rid)
        viewer, _ = chat_viewer(rid)
        msgs = chat.load(base_dir / rid)
        lang = lang_arg() if viewer == "patient" else "en"
        return jsonify(messages=chat.public(msgs, lang, viewer), status=chat.status(msgs))

    @app.get("/pharmacist/inbox")
    def inbox():
        ph = pharmacist()
        if not ph:
            return redirect("/pharmacist/login")
        return page("Questions", chat.inbox_body(chat.inbox(base_dir), ph), "en",
                    topbar("en", lambda c: "/pharmacist/inbox", right=pharmacist_links("inbox")))

    @app.post("/r/<rid>/retry")
    def retry(rid):
        """Gemini was busy or offline: read the saved photos again."""
        run = get_run(rid)
        photos = sorted(str(f) for f in (run.dir / "photos").iterdir() if f.is_file())
        return finish(rid, run.dir, photos)

    @app.get("/r/<rid>/review")
    def review(rid):
        run, ph = get_run(rid), pharmacist()
        if not ph:
            return need_login(rid)
        return review_page(run, f"/r/{rid}", lang_arg(), pharmacist=ph)

    @app.post("/r/<rid>/review")
    def save_review(rid):
        run, ph = get_run(rid), pharmacist()
        if not ph:            # only a verified pharmacist can put an unsure reading on the card
            return need_login(rid)
        errors, forms = run.save_review(request.form, pharmacist=ph)
        if errors:
            return review_page(run, f"/r/{rid}", lang_arg(), errors, forms, pharmacist=ph), 400
        return redirect(f"/r/{rid}/card?lang={lang_arg()}")

    @app.post("/r/<rid>/undo/<path:iid>")
    def undo(rid, iid):
        run, ph = get_run(rid), pharmacist()
        if not ph:
            return need_login(rid)
        run.undo(iid)
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
      <input type="file" id="photos" name="photos" accept="image/*,.heic,.heif,.pdf,application/pdf" multiple>
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
  <a class="urgent" href="tel:108">{icon('phone', 20)}<span>{esc(L['urgent'])}</span></a>
  <p class="disclaimer">{esc(L['footer'])}</p>
</section>
<div class="cam" id="cam" hidden>
  <video id="video" playsinline autoplay muted></video>
  <p class="cam-hint" id="camhint">{esc(L['cam_allow'])}</p>
  <div class="btn-row">
    <label for="photos" class="btn btn-outline" id="campick" hidden>{icon('image')}{esc(L['choose_photo'])}</label>
    <button type="button" class="btn btn-primary" id="shoot" disabled>{icon('camera')}{esc(L['capture'])}</button>
    <button type="button" class="btn btn-quiet" id="camdone">{esc(L['done'])}</button>
  </div>
</div>
<div class="loading" id="loading" role="status" aria-live="polite">
  <div class="reading">
    <div class="read-visual" aria-hidden="true">
      <div class="pop-stage"><span class="pop-tile emoji" style="animation-delay:0.0s"><span class="two-pills"><span>💊</span><span>💊</span></span></span><span class="pop-tile emoji" style="animation-delay:0.9s">🔍</span><span class="pop-tile line" style="animation-delay:1.8s">{icon('strip', 42)}</span><span class="pop-tile line" style="animation-delay:2.7s">{icon('rx', 42)}</span><span class="pop-tile emoji" style="animation-delay:3.6s">🩺</span></div>
      <span id="pillanim" hidden></span>
    </div>
    <h2 id="stagetext">{esc(L['step_upload'])}…</h2>
    <div class="progress" aria-hidden="true"><span id="progressbar"></span></div>
    <ol class="stages" id="stages">
      <li>{esc(L['step_upload'])}</li><li>{esc(L['step_scan'])}</li><li>{esc(L['step_read'])}</li>
      <li>{esc(L['step_check'])}</li><li>{esc(L['step_card'])}</li>
    </ol>
    <p class="wait-note">{esc(L['reading_wait'])}</p>
  </div>
</div>
{{SCRIPT}}"""
    script = SCRIPT.replace("__CHOSEN__", json.dumps(L["photos_chosen"])).replace(
        "__BLOCKED__", json.dumps(L["camera_blocked"])).replace("__MAX__", str(MAX_PHOTOS)).replace(
        "__ALLOW__", json.dumps(L["cam_allow"])).replace("__TIP__", json.dumps(L["cam_hint"]))
    return page(L["app_name"], body.replace("{SCRIPT}", script), lang,
                topbar(lang, lambda c: f"/?lang={c}", right=history_link(lang, "read")), tabbar(lang, "read"))


PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")
MAX_PDF_PAGES = 3   # a prescription is 1-2 pages; a long PDF is almost surely something else


def save_upload(f, folder: Path, n: int, room: int) -> list[str]:
    """Save one uploaded file as photo(s) the reader can use.

    Photos (JPG, PNG, WEBP) are kept as they are. iPhone photos (HEIC) become JPG.
    A PDF (prescriptions often arrive as PDF) becomes one PNG per page, up to `room` pages.
    Raises if the file is not something we can read.
    """
    ext = Path(f.filename or "").suffix.lower()
    data = f.read()
    if ext == ".pdf" or data[:5] == b"%PDF-":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        out = []
        for k in range(min(len(pdf), room, MAX_PDF_PAGES)):
            path = folder / f"photo{n + k}.png"
            pdf[k].render(scale=200 / 72).to_pil().convert("RGB").save(path)   # 200 dpi: small print stays readable
            out.append(str(path))
        if not out:
            raise ValueError("empty PDF")
        return out
    if ext in (".heic", ".heif"):
        import io
        from pillow_heif import register_heif_opener
        register_heif_opener()
        path = folder / f"photo{n}.jpg"
        Image.open(io.BytesIO(data)).convert("RGB").save(path, quality=92)
        return [str(path)]
    path = folder / f"photo{n}{ext if ext in PHOTO_EXTS else '.jpg'}"
    path.write_bytes(data)
    with Image.open(path) as im:
        im.verify()
    return [str(path)]


def pharmacist_links(current: str = "") -> str:
    """Top-bar links for a signed-in pharmacist."""
    cur = lambda k: " aria-current='page'" if k == current else ""
    return f"<nav class='navlinks'><a class='navlink' href='/pharmacist/inbox'{cur('inbox')}>Questions</a></nav>"


def _pharmacist_shell(title: str, inner: str) -> str:
    return page(title, f"<div class='auth'>{inner}</div>", "en", topbar("en", lambda c: f"/?lang={c}"))


def login_page(nxt: str = "", error: str = "") -> str:
    esc = html.escape
    err = f"<div class='notice' role='alert'>{icon('alert')}<div>{esc(error)}</div></div>" if error else ""
    return _pharmacist_shell("Pharmacist sign in", f"""
<h1>Pharmacist sign in</h1>
<p class="lede">Only verified pharmacists can confirm medicines. Sign in with your State Pharmacy Council
registration number.</p>{err}
<form method="post" action="/pharmacist/login" class="stack">
  <input type="hidden" name="next" value="{esc(nxt)}">
  <label class="field">Registration number <input name="reg_no" required autocomplete="username"></label>
  <label class="field">Password <input name="password" type="password" required autocomplete="current-password"></label>
  <button class="btn btn-primary btn-block" type="submit">Sign in</button>
</form>
<p class="small">New here? <a href="/pharmacist/apply">Apply with your registration certificate</a></p>""")


def apply_page(form=None, errors=(), done: bool = False) -> str:
    esc = html.escape
    form = form or {}
    if done:
        return _pharmacist_shell("Application received", """
<h1>Application received</h1>
<p class="lede">We are checking your registration certificate against the Pharmacy Council register.
You can sign in once it is approved.</p><a class="btn btn-outline" href="/">Back to Strip Reader</a>""")
    err = "".join(f"<div class='err'>{esc(e)}</div>" for e in errors)
    v = lambda k: esc(form.get(k, ""))
    return _pharmacist_shell("Pharmacist application", f"""
<h1>Apply as a pharmacist</h1>
<p class="lede">Upload your State Pharmacy Council Registration Certificate. It is read automatically, then
checked by a person against the council register before your account works.</p>{err}
<form method="post" action="/pharmacist/apply" enctype="multipart/form-data" class="stack">
  <label class="field">Full name (as on the certificate) <input name="name" value="{v('name')}" required></label>
  <label class="field">Registration number <input name="reg_no" value="{v('reg_no')}" required></label>
  <label class="field">Pharmacy Council <input name="council" value="{v('council')}" placeholder="e.g. Odisha State Pharmacy Council" required></label>
  <label class="field">Registration certificate (photo or PDF)
    <input name="certificate" type="file" accept="image/*,.heic,.pdf,application/pdf" required></label>
  <label class="field">Choose a password (8+ characters) <input name="password" type="password" minlength="8" required autocomplete="new-password"></label>
  <button class="btn btn-primary btn-block" type="submit">Send application</button>
</form>
<p class="small">Already approved? <a href="/pharmacist/login">Sign in</a></p>""")


def history_page(lang: str, runs: list[dict]) -> str:
    L = labels(lang)
    esc = html.escape
    rows = []
    for r in runs:
        rows.append(
            f"<li class='med' style='display:flex;gap:16px;align-items:center;flex-wrap:wrap'>"
            f"<div style='flex:1;min-width:200px'><div class='name'>{esc(r['when'])}</div>"
            f"<div class='why'>{esc(L['photos_n'].format(n=r['photos']))} · "
            f"{esc(L['meta'].format(a=r['on_card'], b=r['ask']))}</div>"
            + (f"<span class='badge ok'>{esc(L['pharmacist_replied'])}</span>" if r.get('chat') == 'answered' else "")
            + (f"<span class='badge wait'>{esc(L['chat_sent'])}</span>" if r.get('chat') == 'waiting' else "")
            + f"</div>"
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
  const chosen = __CHOSEN__, blocked = __BLOCKED__, allow = __ALLOW__, tip = __TIP__, MAX = __MAX__;
  let files = [], stream = null;

  // One list of photos from the gallery and the camera, put back into the form's file input.
  function sync() {
    const dt = new DataTransfer();
    files.slice(0, MAX).forEach(f => dt.items.add(f));
    input.files = dt.files;
    previews.innerHTML = '';
    files.slice(0, MAX).forEach(f => {
      if (/^image\\/(jpeg|png|webp|gif)$/.test(f.type)) {
        const img = document.createElement('img'); img.alt = ''; img.src = URL.createObjectURL(f); previews.appendChild(img);
      } else {   // PDF or iPhone HEIC: the browser can't show it, so show the file type
        const chip = document.createElement('span'); chip.className = 'file-chip';
        chip.textContent = (f.name.split('.').pop() || 'file').toUpperCase(); chip.title = f.name; previews.appendChild(chip);
      }
    });
    count.hidden = !files.length; count.textContent = chosen.replace('{n}', Math.min(files.length, MAX));
    const go = document.getElementById('go');
    go.hidden = !files.length;   // one clear next step at a time
    if (files.length) { go.scrollIntoView({ behavior: 'smooth', block: 'center' }); go.classList.add('pulse'); }
  }
  input.addEventListener('change', () => { files = files.concat([...input.files]); sync(); });
  phonecam.addEventListener('change', () => { files = files.concat([...phonecam.files]); sync(); });

  // Live camera in the page (laptops and phones); if not allowed, the phone's own camera app.
  // The camera screen opens at once, so the tap always does something. It asks the person to
  // allow the camera; if the browser refuses (or never answers), it offers "Choose photo".
  const hint = document.getElementById('camhint'), shoot = document.getElementById('shoot'),
        pick = document.getElementById('campick');
  function camFailed() {
    if (stream) return;
    if (/Android|iPhone|iPad/i.test(navigator.userAgent)) { stopCam(); phonecam.click(); return; }
    hint.textContent = blocked; pick.hidden = false; shoot.hidden = true;
  }
  document.getElementById('camera').addEventListener('click', async () => {
    hint.textContent = allow; pick.hidden = true; shoot.hidden = false; shoot.disabled = true; cam.hidden = false;
    const waited = setTimeout(camFailed, 12000);
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1920 } } });
      clearTimeout(waited);
      if (cam.hidden) { stopCam(); return; }          // closed while waiting
      video.srcObject = stream; hint.textContent = tip; shoot.disabled = false; pick.hidden = true; shoot.hidden = false;
    } catch (e) { clearTimeout(waited); camFailed(); }
  });
  pick.addEventListener('click', () => stopCam());
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
      stopCam(); startReading();
    }));
  // Reading screen: the popping medicine icons and the stages ticked off one by one
  // while the server works.
  function startReading() {
    const pill = document.getElementById('pillanim'),
          items = [...document.querySelectorAll('#stages li')], text = document.getElementById('stagetext');
    let i = 0;
    const show = () => {
      items.forEach((li, k) => { li.className = k < i ? 'st-done' : (k === i ? 'st-now' : ''); });
      text.textContent = items[i].textContent + '…';
      document.getElementById('progressbar').style.width = Math.round((i + 0.6) / items.length * 100) + '%';
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
