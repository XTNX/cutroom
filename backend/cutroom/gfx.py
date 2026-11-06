"""Infographic slots (stage 6): one agent per slot builds a spec, all slots in parallel
(video-use Hard Rule 10); a deterministic renderer draws it (helpers/gfx_render.py).

Slot (in edl["slots"]): {id, type, spec_hint|spec, source, payoff_time, start_in_output, duration,
status: placeholder|working|ready|error, files: {render, preview, poster}, error}
Files live in <edit>/animations/slot_<id>/ (Hard Rule 12).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import agent, edl
from .config import CUTROOM_HELPERS, PYTHON, subprocess_env
from .jobs import Job

DEFAULT_BRAND = {"bg": "#0b0c10", "panel": "#14161c", "fg": "#f3f2ee", "muted": "#9aa1ad", "accent": "#e8a33d",
                 "accent2": "#5b8def", "font": "segoeui.ttf", "font_bold": "seguisb.ttf", "confirmed": False}
_lock = threading.Lock()   # slot agents finish concurrently: serialise read-modify-write of the EDL


def slot_dir(project, sid: str) -> Path:
    d = project.edit / "animations" / f"slot_{sid}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def brand_of(e: dict) -> dict:
    return {**DEFAULT_BRAND, **(e.get("brand") or {})}


def update_slot(project, sid: str, patch: dict, note: str | None, author: str = "gfx") -> dict:
    with _lock:
        cur = edl.current(project)
        if not cur:
            raise RuntimeError("EDL ещё нет")
        e = json.loads(json.dumps(cur))
        for s in e.get("slots", []):
            if s.get("id") == sid:
                s.update(patch)
                break
        else:
            raise KeyError(sid)
        if note:
            edl.save_version(project, e, author, note)
        else:   # status-only change: write in place, no new undo step
            project._q("update edl_versions set data = ? where id = ?", (json.dumps(e, ensure_ascii=False), cur["cutroom"]["version_id"]))
            edl.write_edl(project, e)
        return next(s for s in e["slots"] if s.get("id") == sid)


def _context_words(project, e: dict, slot: dict, window: float = 12.0) -> str:
    """What the speaker says around the slot's moment (output time → source time)."""
    t = slot.get("start_in_output", 0) + 0.8
    for r in e["ranges"]:
        if r["out_start"] <= t < r["out_start"] + (r["end"] - r["start"]):
            ts = r["start"] + (t - r["out_start"])
            tr = project.paths(r["source"])["transcript"]
            if not tr.exists():
                return "(нет расшифровки)"
            words = [w for w in json.loads(tr.read_text(encoding="utf-8"))["words"] if w.get("type") == "word"]
            near = [w for w in words if ts - window <= w["start"] <= ts + window]
            return " ".join(f"[{w['start']:.1f}] {w['text']}" if i % 12 == 0 else w["text"] for i, w in enumerate(near))
    return "(момент вне монтажа)"


def build_spec(project, job: Job, sid: str, request: str | None = None) -> dict:
    e = edl.current(project)
    slot = next((s for s in e.get("slots", []) if s.get("id") == sid), None)
    if slot is None:
        raise RuntimeError(f"нет слота {sid}")
    st = edl.load_strategy(project) or {}
    update_slot(project, sid, {"status": "working", "error": None}, None)
    parts = [
        f"Спецификация карточки для слота {sid}, длительность {slot.get('duration', 5):.1f} с.",
        agent.wrap_data("задумано для слота", f"тип: {slot.get('type', '')}; {slot.get('spec_hint') or slot.get('spec_text') or slot.get('spec', '')}"),
        agent.wrap_data("стратегия", st.get("text", "")),
        agent.wrap_data("речь вокруг момента появления", _context_words(project, e, slot)),
    ]
    if slot.get("spec") and isinstance(slot["spec"], dict):
        parts.append(agent.wrap_data("прошлая версия карточки", json.dumps(slot["spec"], ensure_ascii=False)))
    if request:
        parts.append(agent.wrap_data("пожелание человека", request))
    job.log.append(f"агент слота {sid} думает…")
    try:
        spec: agent.SlotSpec = agent.run("slot", "\n\n".join(parts), timeout=300, log_dir=project.edit)  # type: ignore[assignment]
    except Exception as ex:
        update_slot(project, sid, {"status": "error", "error": str(ex)[:300]}, None)
        raise
    return update_slot(project, sid, {"spec": spec.model_dump(), "type": spec.type, "status": "working"},
                       f"инфографика {sid}: {spec.type} «{spec.title[:30]}»" + (f" ({request[:30]})" if request else ""))


def render(project, job: Job, sid: str) -> dict:
    e = edl.current(project)
    slot = next((s for s in e.get("slots", []) if s.get("id") == sid), None)
    if not slot or not isinstance(slot.get("spec"), dict):
        raise RuntimeError(f"у слота {sid} нет спецификации")
    # every drawing gets its own folder: the verified render or the monitor may still be reading the
    # previous files (Windows locks open files), and the browser never shows a cached old card
    base = slot_dir(project, sid)
    d = base / f"v{int(time.time() * 1000)}"
    d.mkdir()
    (d / "spec.json").write_text(json.dumps(slot["spec"], ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "brand.json").write_text(json.dumps(brand_of(e), ensure_ascii=False, indent=1), encoding="utf-8")
    first = next(iter(e["ranges"]), None)
    fps = (((project.sources.get(first["source"], {}) if first else {}).get("probe") or {}).get("video") or {}).get("fps") or 25
    cmd = [str(PYTHON), str(CUTROOM_HELPERS / "gfx_render.py"), str(d), "--fps", f"{fps:.4f}", "--duration", f"{slot.get('duration', 5):.2f}"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         env=subprocess_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    job._proc = p
    tail = []
    for line in p.stdout:
        line = line.strip()
        if line.startswith("PROGRESS "):
            job.progress = float(line.split()[1])
        elif line:
            tail.append(line)
    if p.wait() != 0:
        msg = (tail[-1] if tail else "ошибка рендера")[:300]
        update_slot(project, sid, {"status": "error", "error": msg}, None)
        raise RuntimeError(msg)
    files = {"render": str(d / "render.mov"), "preview": str(d / "preview.webm"), "poster": str(d / "poster.png"), "v": int(time.time() * 1000)}
    out = update_slot(project, sid, {"status": "ready", "files": files, "error": None}, None)
    for old in base.glob("v*"):   # older drawings: best effort (one may still be open)
        if old != d and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    return out


def overlays(e: dict) -> list[dict]:
    """Ready slots → video-use overlays (render.py shifts PTS to the window start; subtitles stay last)."""
    out = []
    for s in e.get("slots", []):
        f = (s.get("files") or {}).get("render")
        if s.get("status") == "ready" and f and Path(f).exists():
            out.append({"file": f, "start_in_output": float(s["start_in_output"]), "duration": float(s.get("duration", 5))})
    return out
