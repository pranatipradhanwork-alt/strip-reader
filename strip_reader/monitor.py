"""Monitoring: is the app healthy, and how is it doing?

- `health()` backs GET /healthz: the app is up, and whether the AI key, the offline
  reader and the medicine list are ready. Hosting platforms call it to restart a broken app.
- `Monitor.reading()` writes one line per reading (to the log and to a metrics file):
  time taken, number of photos, which path each photo took (full AI reading, offline
  reader, saved for later, or not a medicine) and how many items went on the card vs.
  to the pharmacist. Never medicine names, photos or anything typed by a person.
- `Monitor.stats()` sums one day's lines for the private /admin/stats page.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("strip_reader")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

PATHS = ("ai", "offline", "saved", "not_medicine")


def image_path(img: dict) -> str:
    """Which way one photo was read."""
    if img.get("queued"):
        return "saved"
    if img.get("offline"):
        return "offline"
    if img.get("image_type") == "other" and not img.get("items"):
        return "not_medicine"
    return "ai"


def summarise(result: dict) -> dict:
    """Counts only: nothing that could identify a person or their medicines."""
    paths = Counter(image_path(i) for i in result.get("images", []))
    items = [it for i in result.get("images", []) for it in i.get("items", [])]
    return {"photos": len(result.get("images", [])), "paths": {p: paths.get(p, 0) for p in PATHS},
            "on_card": sum(it.get("status") == "ok" for it in items),
            "to_pharmacist": sum(it.get("status") != "ok" for it in items)}


class Monitor:
    def __init__(self, folder: str | Path):
        self.file = Path(folder) / "metrics.jsonl"

    def _write(self, rec: dict) -> None:
        rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rec}
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            with self.file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass   # monitoring must never break a reading
        log.info(" ".join(f"{k}={json.dumps(v, separators=(',', ':'))}" for k, v in rec.items() if k != "at"))

    def reading(self, kind: str, seconds: float, result: dict | None = None, error: str = "") -> None:
        """kind: "upload", "sample" or "retry". `error` is an exception class name only."""
        rec = {"event": "reading", "kind": kind, "seconds": round(seconds, 2)}
        if result is not None:
            rec.update(summarise(result))
        if error:
            rec["error"] = error
        self._write(rec)

    def event(self, name: str, **counts) -> None:
        """Other things worth counting: chat messages, sign-ins, server errors (no content)."""
        self._write({"event": name, **counts})

    def stats(self, day: str | None = None) -> dict:
        """Totals for one UTC day (default today)."""
        day = day or datetime.now(timezone.utc).date().isoformat()
        rows = []
        if self.file.exists():
            for line in self.file.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("at", "").startswith(day):
                    rows.append(r)
        reads = [r for r in rows if r["event"] == "reading"]
        # Timing only for real photos: the built-in sample needs no AI and would hide slow readings.
        secs = sorted(r["seconds"] for r in reads if r.get("kind") != "sample" and not r.get("error"))
        paths = Counter()
        for r in reads:
            paths.update(r.get("paths", {}))
        events = Counter(r["event"] for r in rows if r["event"] != "reading")
        return {
            "day": day,
            "readings": len(reads),
            "samples": sum(r.get("kind") == "sample" for r in reads),
            "photos": sum(r.get("photos", 0) for r in reads),
            "seconds_median": round(statistics.median(secs), 1) if secs else None,
            "seconds_p90": round(secs[min(len(secs) - 1, int(len(secs) * 0.9))], 1) if secs else None,
            "paths": {p: paths.get(p, 0) for p in PATHS},
            "on_card": sum(r.get("on_card", 0) for r in reads),
            "to_pharmacist": sum(r.get("to_pharmacist", 0) for r in reads),
            "reading_errors": sum(1 for r in reads if r.get("error")),
            "events": dict(events),
        }


def health(medicines_csv: Path, data_dir: Path) -> dict:
    """Quick checks only (no AI call, no model loading), so it is safe to call every few seconds."""
    checks = {
        "ai_key": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        "offline_reader": importlib.util.find_spec("rapidocr_onnxruntime") is not None,
        "medicine_list": medicines_csv.exists(),
        "storage": _writable(data_dir),
    }
    status = "ok" if all(checks.values()) else ("down" if not checks["storage"] else "degraded")
    return {"status": status, "checks": checks, "time": int(time.time())}


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False
