"""Ask a pharmacist: a simple message thread attached to one reading.

The patient asks from their card; the reading (label pictures and what the app
read) is already attached, so they don't have to explain. Only verified,
signed-in pharmacists can see the questions and reply; each reply carries the
pharmacist's name and registration number. Messages are kept in the reading's
folder (chat.json) and the page checks for new ones every few seconds, so it
works like a chat without any patient account.

The app never promises how fast a reply comes, and every chat keeps the
"call 108" link and a reminder that a pharmacist doesn't replace the doctor.
"""

from __future__ import annotations

import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .card import _img_tag, labels
from .icons import icon

MAX_TEXT = 1000
MAX_PATIENT_MESSAGES = 30


def _path(run_dir: Path) -> Path:
    return Path(run_dir) / "chat.json"


def load(run_dir: Path) -> list[dict]:
    p = _path(run_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def add(run_dir: Path, sender: str, text: str, pharmacist: dict | None = None) -> str | None:
    """Add one message. Returns a problem in plain words, or None if saved."""
    text = (text or "").strip()
    if not text:
        return "empty"
    if len(text) > MAX_TEXT:
        return "too_long"
    msgs = load(run_dir)
    if sender == "patient" and sum(m["from"] == "patient" for m in msgs) >= MAX_PATIENT_MESSAGES:
        return "too_many"
    msg = {"from": sender, "text": text, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if sender == "pharmacist":
        msg.update(by=pharmacist["name"], reg_no=pharmacist["reg_no"])
    msgs.append(msg)
    tmp = _path(run_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps(msgs, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_path(run_dir))
    return None


def status(msgs: list[dict]) -> str:
    """"" (no chat), "waiting" (patient spoke last) or "answered"."""
    if not msgs:
        return ""
    return "waiting" if msgs[-1]["from"] == "patient" else "answered"


def inbox(base_dir: Path) -> list[dict]:
    """All readings with a question: waiting ones first (oldest first), then answered (newest first)."""
    rows = []
    for f in Path(base_dir).glob("*/chat.json"):
        msgs = json.loads(f.read_text(encoding="utf-8"))
        if msgs:
            rows.append({"rid": f.parent.name, "status": status(msgs), "last": msgs[-1],
                         "first": next((m for m in msgs if m["from"] == "patient"), msgs[0]), "count": len(msgs)})
    waiting = sorted((r for r in rows if r["status"] == "waiting"), key=lambda r: r["last"]["at"])
    answered = sorted((r for r in rows if r["status"] != "waiting"), key=lambda r: r["last"]["at"], reverse=True)
    return waiting + answered


def public(msgs: list[dict], lang: str, viewer: str) -> list[dict]:
    """Messages as shown on screen (also sent to the page when it checks for new ones)."""
    L = labels(lang)
    out = []
    for m in msgs:
        if m["from"] == "pharmacist":
            who = L["pharmacist_named"].format(name=m["by"], reg=m["reg_no"])
        else:
            who = L["you"] if viewer == "patient" else "Patient"
        out.append({"mine": m["from"] == viewer, "who": who, "text": m["text"],
                    "time": time.strftime("%d %b, %H:%M", time.localtime(datetime.fromisoformat(m["at"]).timestamp()))})
    return out


def _bubbles(items: list[dict]) -> str:
    esc = html.escape
    return "".join(f"<div class='msg {'mine' if m['mine'] else 'theirs'}'><div class='who'>{esc(m['who'])}"
                   f"<span>{esc(m['time'])}</span></div><div class='text'>{esc(m['text'])}</div></div>" for m in items)


def chat_body(rid: str, run, lang: str, viewer: str, error: str = "") -> str:
    """The chat for one reading. `viewer` is "patient" or "pharmacist"."""
    L = labels(lang)
    esc = html.escape
    msgs = load(run.dir)
    sched = run.schedule(lang)
    ask = sched["ask"]
    # What the pharmacist needs to see: each unsure item with its label picture.
    attached = "".join(
        f"<li><b>{esc(e['name'] or '?')}</b>{(' · ' + esc(e['strength'])) if e.get('strength') else ''}"
        f"{_img_tag(e.get('crop'))}</li>" for e in ask)
    on_card = [e for k in ("morning", "afternoon", "evening", "night", "as_needed") for e in sched[k]]
    names = sorted({e["name"] for e in on_card})
    reading = (f"<details class='attached' {'open' if viewer == 'pharmacist' else ''}><summary>{icon('rx', 18)}"
               f"{esc(L['chat_attached'])}</summary>"
               + (f"<p class='why'>{esc(L['ask'])}:</p><ul class='att-list'>{attached}</ul>" if ask else "")
               + (f"<p class='why'>{esc(L['title'])}: {esc(', '.join(names))}</p>" if names else "")
               + "</details>")
    quick = ""
    if viewer == "patient":
        quick = "<div class='quick'>" + "".join(
            f"<button type='button' class='chip-btn' data-q='{esc(L[k])}'>{esc(L[k])}</button>"
            for k in ("quick_right", "quick_when", "quick_together")) + "</div>"
    review = (f"<a class='btn btn-outline' href='/r/{rid}/review?lang=en'>{icon('check_user')}Check &amp; confirm medicines</a>"
              if viewer == "pharmacist" else "")
    err = f"<div class='err'>{esc(L.get('chat_' + error, error))}</div>" if error else ""
    placeholder = L["chat_placeholder"] if viewer == "patient" else "Write your reply in plain words"
    title = L["chat_title"] if viewer == "patient" else "Patient question"
    intro = L["chat_intro"] if viewer == "patient" else (
        "The reading is attached below. Reply in plain words; your name and registration number are shown "
        "with every reply. To put a medicine on the card, use Check & confirm.")
    waiting = (f"<p class='chat-wait' id='chatwait' {'hidden' if status(msgs) != 'waiting' or viewer != 'patient' else ''}>"
               f"{esc(L['chat_waiting'])}</p>")
    return f"""<div class='chat-page'>
<header class='page-head'><p class='kicker'>{esc(L['ask_pharmacist'])}</p><h1>{esc(title)}</h1>
<p class='meta'>{esc(intro)}</p></header>
{reading}
<div class='thread' id='thread' aria-live='polite'>{_bubbles(public(msgs, lang, viewer))}</div>
{waiting}
<form method='post' action='/r/{rid}/chat?lang={lang}' class='chat-form' id='chatform'>
  {quick}{err}
  <textarea name='text' id='chattext' rows='3' maxlength='{MAX_TEXT}' required placeholder='{esc(placeholder)}'></textarea>
  <div class='btn-row'><button class='btn btn-primary' type='submit'>{icon('arrow')}{esc(L['send'])}</button>{review}
  <a class='btn btn-quiet' href='/r/{rid}/card?lang={lang}'>{esc(L['back_to_card'])}</a></div>
</form>
<p class='disclaimer'>{esc(L['chat_not_doctor'])}</p>
<a class='urgent' href='tel:108'>{icon('phone', 20)}<span>{esc(L['urgent'])}</span></a>
</div>
<script>
(() => {{
  const thread = document.getElementById('thread'), wait = document.getElementById('chatwait');
  let count = {len(msgs)};
  document.querySelectorAll('.chip-btn').forEach(b => b.addEventListener('click', () => {{
    const t = document.getElementById('chattext'); t.value = b.dataset.q; t.focus(); }}));
  thread.scrollTop = thread.scrollHeight;
  // Check for new messages every 8 seconds (no page reload, so typing is never lost).
  setInterval(async () => {{
    try {{
      const r = await fetch('/r/{rid}/chat/messages?lang={lang}');
      if (!r.ok) return;
      const d = await r.json();
      if (d.messages.length === count) return;
      count = d.messages.length;
      thread.innerHTML = '';
      d.messages.forEach(m => {{
        const div = document.createElement('div'); div.className = 'msg ' + (m.mine ? 'mine' : 'theirs');
        const who = document.createElement('div'); who.className = 'who'; who.textContent = m.who;
        const t = document.createElement('span'); t.textContent = m.time; who.appendChild(t);
        const text = document.createElement('div'); text.className = 'text'; text.textContent = m.text;
        div.append(who, text); thread.appendChild(div);
      }});
      if (wait) wait.hidden = !(d.status === 'waiting' && '{viewer}' === 'patient');
      thread.scrollTop = thread.scrollHeight;
    }} catch (e) {{}}
  }}, 8000);
}})();
</script>"""


def inbox_body(rows: list[dict], pharmacist: dict) -> str:
    esc = html.escape
    items = []
    for r in rows:
        badge = ("<span class='badge wait'>Waiting</span>" if r["status"] == "waiting"
                 else "<span class='badge ok'>Answered</span>")
        when = time.strftime("%d %b, %H:%M", time.localtime(datetime.fromisoformat(r["last"]["at"]).timestamp()))
        items.append(f"<li class='med inbox-row'><div class='inbox-main'>{badge}<div class='name'>"
                     f"{esc(r['first']['text'][:120])}</div><div class='why'>{r['count']} message(s) · last {esc(when)}</div></div>"
                     f"<a class='btn btn-primary' href='/r/{r['rid']}/chat?lang=en'>Open</a></li>")
    listing = f"<ul class='meds'>{''.join(items)}</ul>" if items else "<p class='lede'>No questions yet.</p>"
    return (f"<div class='chat-page'><header class='page-head'><p class='kicker'>Pharmacist</p><h1>Questions</h1>"
            f"<p class='meta'>Signed in as {esc(pharmacist['name'])} · Reg. {esc(pharmacist['reg_no'])}. "
            f"Waiting questions are listed first, oldest first.</p></header>{listing}"
            f"<form method='post' action='/pharmacist/logout' style='margin-top:24px'>"
            f"<button class='btn btn-quiet' type='submit'>Sign out</button></form></div>")
