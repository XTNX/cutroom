"""Cutroom backend. Started by Electron main on 127.0.0.1:<free port> with a random token.

    python -m cutroom.server --port 8765 --token dev [--project D:/Video/бф]
Prints `CUTROOM_READY <port>` once it accepts connections.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import system
from .config import VIDEO_USE_DIR, load_settings, save_settings
from .jobs import Job, Queue
from .project import Project, remember_recent

TOKEN = os.environ.get("CUTROOM_TOKEN", "dev")
queue = Queue()
state: dict = {"project": None, "sources_version": 0}


def _on_finish(j: Job) -> None:
    state["sources_version"] += 1
    p: Project | None = state["project"]
    if p is not None:
        try:
            p.record_job(j)
        except Exception:
            pass


queue.on_finish = _on_finish
app = FastAPI(title="Cutroom", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/health":
        return await call_next(request)
    tok = request.headers.get("x-cutroom-token") or request.query_params.get("t")
    if tok != TOKEN:
        return JSONResponse({"detail": "нет токена"}, status_code=401)
    return await call_next(request)


def proj() -> Project:
    p = state["project"]
    if p is None:
        raise HTTPException(409, "Проект не открыт: выберите папку с исходниками")
    return p


def src(sid: str) -> dict:
    p = proj()
    if sid not in p.sources:
        raise HTTPException(404, f"нет источника {sid}")
    return p.sources[sid]


# ------------------------------------------------------------------ system


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/system")
def get_system():
    p = state["project"]
    snap = system.snapshot(p.measured_rtf() if p else None)
    snap["video_use"] = {"path": str(VIDEO_USE_DIR), "sha": _video_use_sha()}
    snap["gigaam"] = _module_available("gigaam")
    snap["whisperx"] = _module_available("whisperx")
    return snap


def _module_available(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _video_use_sha() -> str | None:
    ver = VIDEO_USE_DIR.parent.parent / "VERSION.txt"   # installed copy: no git history, the build records the commit
    if ver.exists():
        for line in ver.read_text(encoding="utf-8").splitlines():
            if line.startswith("video-use "):
                return line.split()[1]
    head = VIDEO_USE_DIR / ".git"
    try:
        if head.is_file():  # submodule: gitdir pointer
            gitdir = (VIDEO_USE_DIR / head.read_text().split(":", 1)[1].strip()).resolve()
        else:
            gitdir = head
        ref = (gitdir / "HEAD").read_text().strip()
        if ref.startswith("ref:"):
            return (gitdir / ref.split(" ", 1)[1]).read_text().strip()
        return ref
    except Exception:
        return None


class ModelReq(BaseModel):
    model: str


@app.post("/api/system/models/download")
def download_model(req: ModelReq):
    if req.model not in system.MODEL_REPOS:
        raise HTTPException(400, "неизвестная модель")
    repo, size = system.MODEL_REPOS[req.model]

    def run(j: Job):
        from huggingface_hub import snapshot_download
        done = threading.Event()
        err: list[Exception] = []

        def dl():
            try:
                # plain HTTP instead of xet: .incomplete grows in place, so progress is visible
                os.environ["HF_HUB_DISABLE_XET"] = "1"
                snapshot_download(repo)
            except Exception as e:  # noqa: BLE001
                err.append(e)
            done.set()

        threading.Thread(target=dl, daemon=True).start()
        while not done.wait(0.5):
            if j._cancel:
                return None
            st = next((m for m in system.model_status() if m["name"] == req.model), None)
            if st:
                j.progress = min(0.99, st["downloaded"] / size)
                queue.version += 1
        if err:
            raise err[0]
        return None

    j = queue.add(Job(kind="model", label=f"Загрузка модели {req.model} ({size / 1e9:.1f} ГБ)", lane="io",
                      fn=run, heavy=True, priority=5))
    return j.public()


@app.get("/api/settings")
def get_settings():
    return load_settings()


@app.patch("/api/settings")
def patch_settings(patch: dict):
    return save_settings(patch)


# ------------------------------------------------------------------ project


class OpenReq(BaseModel):
    path: str
    files: list[str] | None = None


@app.post("/api/project/open")
def open_project(req: OpenReq):
    path = Path(req.path)
    if path.is_file():
        path = path.parent
    try:
        p = Project(path, queue)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    state["project"] = p
    remember_recent(p.dir)
    p.scan(req.files)
    state["sources_version"] += 1
    return project_payload(p)


@app.post("/api/project/add")
def add_files(req: OpenReq):
    p = proj()
    files = req.files or [req.path]
    p.scan(files)
    state["sources_version"] += 1
    return project_payload(p)


def project_payload(p: Project) -> dict:
    return {
        "dir": str(p.dir), "edit": str(p.edit), "name": p.dir.name,
        "memory": p.project_memory(),
        "sources": p.list_sources(),
        "has_packed": (p.edit / "takes_packed.md").exists(),
    }


@app.get("/api/project")
def get_project():
    p = state["project"]
    if p is None:
        return {"open": False, "recent": load_settings().get("recent_projects", [])}
    return {"open": True, **project_payload(p)}


class IngestReq(BaseModel):
    sources: list[str] | None = None
    transcribe: bool = True


@app.post("/api/project/ingest")
def ingest(req: IngestReq):
    p = proj()
    ids = p.ingest(req.sources, req.transcribe)
    if not ids:
        p.schedule_pack()
    return {"jobs": ids}


@app.get("/api/sources")
def sources():
    return proj().list_sources()


class ExcludeReq(BaseModel):
    excluded: bool


@app.post("/api/sources/{sid}/exclude")
def exclude(sid: str, req: ExcludeReq):
    src(sid)
    proj().set_excluded(sid, req.excluded)
    state["sources_version"] += 1
    return proj().source_state(proj().sources[sid])


@app.get("/api/sources/{sid}/transcript")
def transcript(sid: str):
    src(sid)
    path = proj().paths(sid)["transcript"]
    if not path.exists():
        raise HTTPException(404, "расшифровки ещё нет")
    data = json.loads(path.read_text(encoding="utf-8"))
    words = []
    for w in data.get("words", []):
        if w.get("type") == "spacing":
            continue
        words.append([w.get("text", ""), w.get("start", 0), w.get("end", 0),
                      w.get("probability", 1.0), 1 if w.get("type") == "audio_event" else 0])
    return {"language": data.get("language_code"), "meta": data.get("cutroom"), "words": words}


@app.get("/api/sources/{sid}/peaks")
def peaks(sid: str):
    src(sid)
    path = proj().paths(sid)["peaks"]
    if not path.exists():
        raise HTTPException(404, "волна ещё не посчитана")
    return Response(path.read_bytes(), media_type="application/octet-stream",
                    headers={"X-Peaks-Per-Sec": "100", "Access-Control-Expose-Headers": "X-Peaks-Per-Sec"})


@app.get("/api/takes_packed")
def takes_packed():
    path = proj().edit / "takes_packed.md"
    if not path.exists():
        raise HTTPException(404, "takes_packed.md ещё не собран")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@app.get("/media/{kind}/{sid}")
def media_file(kind: str, sid: str):
    src(sid)
    if kind not in {"proxy", "thumb"}:
        raise HTTPException(404)
    path = proj().paths(sid)[kind]
    if not path.exists():
        raise HTTPException(404, "файл ещё не готов")
    mt = "image/jpeg" if kind == "thumb" else ("audio/mp4" if path.suffix == ".m4a" else "video/mp4")
    return FileResponse(path, media_type=mt,
                        headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ Claude Code link


@app.get("/api/claude")
def claude_status():
    from . import claude_link
    st = claude_link.status()
    return st if st.get("checked") else claude_link.check()


@app.post("/api/claude/check")
def claude_check():
    from . import claude_link
    return claude_link.check()


@app.post("/api/claude/login")
def claude_login():
    from . import claude_link
    return claude_link.open_login()


# ------------------------------------------------------------------ strategy gate + EDL (stage 4)


def _agent_job(kind: str, label: str, fn) -> Job:
    """Wrap agent calls so a missing login or a bad answer shows up as an actionable error."""
    from .agent import AgentError

    def run(job: Job):
        try:
            return fn(job)
        except AgentError as e:
            raise RuntimeError(f"{e}" + (f" Как исправить: {e.fix}" if e.fix else "")) from e

    return queue.add(Job(kind=kind, label=label, lane="agent", heavy=True, priority=6, expected=90, fn=run))


def _strategy_payload(p: Project) -> dict:
    from . import edl
    s = edl.load_strategy(p)
    if not s:
        return {"status": "none"}
    s = dict(s)
    s["gate_open"] = edl.strategy_approved(p)
    s["dirty"] = s.get("status") == "approved" and not s["gate_open"]
    return s


@app.get("/api/strategy")
def get_strategy():
    return _strategy_payload(proj())


class StrategyReq(BaseModel):
    prompt: str = ""
    chips: dict = {}
    feedback: str | None = None


@app.post("/api/strategy")
def make_strategy(req: StrategyReq):
    from .stages import run_strategy
    p = proj()
    if not (p.edit / "takes_packed.md").exists():
        raise HTTPException(409, "Нет takes_packed.md: сначала подготовьте файлы (расшифровка).")
    label = "Стратегия: переписать с учётом правок" if req.feedback else "Стратегия: агент читает материал"
    j = _agent_job("strategy", label, lambda job: run_strategy(p, job, req.prompt, req.chips, req.feedback))
    state["sources_version"] += 1
    return j.public()


class StrategyText(BaseModel):
    text: str


@app.patch("/api/strategy")
def edit_strategy(req: StrategyText):
    from . import edl
    p = proj()
    s = edl.load_strategy(p)
    if not s:
        raise HTTPException(404, "стратегии ещё нет")
    s["text"] = req.text.strip()
    if s.get("status") == "approved" and s.get("approved_hash") != edl.text_hash(s["text"]):
        s["status"] = "draft"   # editing the approved text closes the gate again
    edl.save_strategy(p, s)
    return _strategy_payload(p)


@app.post("/api/strategy/approve")
def approve_strategy():
    from . import edl
    from .stages import run_editor
    p = proj()
    s = edl.load_strategy(p)
    if not s or not s.get("text", "").strip():
        raise HTTPException(409, "Нечего утверждать: сначала получите стратегию.")
    s["status"] = "approved"
    s["approved_hash"] = edl.text_hash(s["text"])
    s["approved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    edl.save_strategy(p, s)
    # approval opens the gate → rough cut starts right away
    j = _agent_job("editor", "Черновая сборка: агент-редактор выбирает дубли", lambda job: _editor_then_preview(p, job))
    return {"strategy": _strategy_payload(p), "job": j.public()}


def _editor_then_preview(p: Project, job: Job):
    from .stages import run_editor
    e = run_editor(p, job)
    schedule_preview(p, delay=0)
    state["sources_version"] += 1
    return e


@app.post("/api/strategy/manual")
def manual_strategy():
    """A person laying clips on the timeline by hand is the decision: open the gate for a manual cut
    (the agents still never cut without an approved strategy)."""
    from . import edl
    p = proj()
    s = edl.load_strategy(p)
    if s and edl.strategy_approved(p):
        return _strategy_payload(p)
    text = "Ручной монтаж: фрагменты раскладывает человек на таймлайне."
    edl.save_strategy(p, {"status": "approved", "text": text, "manual": True, "revision": (s or {}).get("revision", 0) + 1,
                          "data": {"sentences": [text], "beats": [], "takes": "", "slips": [], "graphics": [], "target_seconds": 0,
                                   "aspect": "16:9", "questions": [], "needs": []},
                          "approved_hash": edl.text_hash(text), "approved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "history": ((s or {}).get("history") or [])[-5:] + ([{"text": s["text"], "status": s["status"]}] if s else [])})
    return _strategy_payload(p)


class AskReq(BaseModel):
    question: str


@app.post("/api/strategy/ask")
def ask_agent(req: AskReq):
    from . import edl
    from .stages import ask
    p = proj()

    def run(job: Job):
        a = ask(p, req.question)
        s = edl.load_strategy(p) or {}
        s.setdefault("qa", []).append({"q": req.question, **a, "when": time.strftime("%H:%M")})
        edl.save_strategy(p, s)
        state["sources_version"] += 1

    return _agent_job("ask", "Вопрос агенту", run).public()


@app.post("/api/edl/build")
def build_edl():
    from . import edl
    p = proj()
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Стратегия не утверждена (Hard Rule 11).")
    return _agent_job("editor", "Черновая сборка: агент-редактор выбирает дубли", lambda job: _editor_then_preview(p, job)).public()


def _edl_payload(p: Project) -> dict:
    from . import edl
    e = edl.current(p)
    st = p.stats()
    return {"edl": e, "history": edl.history(p), "gate_open": edl.strategy_approved(p),
            "preview": {"version": int(st["preview_version"]) if "preview_version" in st else None,
                        "exists": (p.edit / "preview.mp4").exists()}}


@app.get("/api/edl")
def get_edl():
    return _edl_payload(proj())


class EdlPut(BaseModel):
    ranges: list[dict]
    slots: list[dict] = []
    note: str = "правка"
    snap: bool = True


@app.put("/api/edl")
def put_edl(req: EdlPut):
    from . import edl
    p = proj()
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Стратегия не утверждена (Hard Rule 11).")
    from .gfx import _lock as gfx_lock
    with gfx_lock:
        cur = edl.current(p)
        # what slot agents/renders wrote is server-owned: a client holding an older copy must not undo it
        own = {s.get("id"): s for s in (cur or {}).get("slots", [])}
        slots = [{**s, **{k: own[s["id"]][k] for k in ("spec", "files", "status", "error", "type") if k in own[s["id"]]}}
                 if s.get("id") in own else s for s in req.slots]
        e = edl.normalize(p, req.ranges, slots, snap=req.snap)
        if cur:
            e["cutroom"]["strategy_hash"] = cur["cutroom"].get("strategy_hash")
            edl.carry_over(e, cur)
        edl.save_version(p, e, "user", req.note)
    schedule_preview(p)
    return _edl_payload(p)


@app.post("/api/edl/undo")
def undo():
    return undo_redo("undo")


@app.post("/api/edl/redo")
def redo():
    return undo_redo("redo")


def undo_redo(direction: str):
    from . import edl
    p = proj()
    if edl.move_head(p, -1 if direction == "undo" else 1) is None:
        raise HTTPException(409, "Дальше некуда")
    schedule_preview(p)
    return _edl_payload(p)


def schedule_preview(p: Project, delay: float = 4.0) -> Job | None:
    """One verified render per burst of edits: a queued job renders whatever EDL is current when it starts."""
    from . import edl
    from .stages import run_render_preview
    if not edl.strategy_approved(p):
        return None
    for j in queue.active():
        if j.kind == "preview" and j.status == "queued":
            j.not_before = time.time() + delay
            return j
    return queue.add(Job(kind="preview", label="Проверенный рендер превью (render.py --preview)", lane="render",
                         heavy=True, priority=25, expected=120, not_before=time.time() + delay,
                         fn=lambda job: run_render_preview(p, job)))


@app.post("/api/render/preview")
def render_preview():
    from . import edl
    p = proj()
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Стратегия не утверждена (Hard Rule 11).")
    return schedule_preview(p, delay=0).public()


@app.get("/media/preview")
def media_preview():
    path = proj().edit / "preview.mp4"
    if not path.exists():
        raise HTTPException(404, "превью ещё не отрендерено")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ markers


def _markers_path(p: Project) -> Path:
    return p.edit / "markers.json"


def _load_markers(p: Project) -> list[dict]:
    f = _markers_path(p)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def _save_markers(p: Project, ms: list[dict]) -> list[dict]:
    _markers_path(p).write_text(json.dumps(ms, ensure_ascii=False, indent=1), encoding="utf-8")
    return ms


class MarkerReq(BaseModel):
    source: str
    time: float
    note: str = ""
    color: str = "amber"


@app.get("/api/markers")
def markers():
    return _load_markers(proj())


@app.post("/api/markers")
def add_marker(req: MarkerReq):
    """Markers stick to the material (source + source time), so they survive any re-cut."""
    p = proj()
    src(req.source)
    ms = _load_markers(p)
    m = {"id": f"m{int(time.time() * 1000)}", "source": req.source, "time": round(req.time, 3), "note": req.note,
         "color": req.color, "done": False, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    ms.append(m)
    _save_markers(p, ms)
    return m


class MarkerPatch(BaseModel):
    note: str | None = None
    done: bool | None = None
    color: str | None = None


@app.patch("/api/markers/{mid}")
def patch_marker(mid: str, req: MarkerPatch):
    p = proj()
    ms = _load_markers(p)
    for m in ms:
        if m["id"] == mid:
            for k, v in req.model_dump(exclude_none=True).items():
                m[k] = v
            _save_markers(p, ms)
            return m
    raise HTTPException(404, "нет такого маркера")


@app.delete("/api/markers/{mid}")
def delete_marker(mid: str):
    p = proj()
    _save_markers(p, [m for m in _load_markers(p) if m["id"] != mid])
    return {"ok": True}


# ------------------------------------------------------------------ fragment edit by the agent


class FragmentReq(BaseModel):
    start: int
    end: int
    request: str
    notes: list[str] = []


@app.post("/api/edl/fragment")
def edit_fragment(req: FragmentReq):
    from . import edl
    from .stages import edit_fragment as run_fragment
    p = proj()
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Стратегия не утверждена (Hard Rule 11).")
    if not req.request.strip():
        raise HTTPException(400, "Опишите, что изменить в выделенном фрагменте.")

    def run(job: Job):
        e = run_fragment(p, job, req.start, req.end, req.request, req.notes or None)
        schedule_preview(p)
        state["sources_version"] += 1
        return e

    a, b = sorted((req.start, req.end))
    return _agent_job("fragment", f"Правка фрагментов {a + 1}–{b + 1}: {req.request[:40]}", run).public()


# ------------------------------------------------------------------ audio: speech mic per take, clean-up, music


@app.get("/api/audio")
def audio_info():
    from . import edl
    from .audio import COMPRESS, DEFAULT_FX, DENOISE, groups_in_use
    p = proj()
    e = edl.current(p)
    presets = {"denoise": list(DENOISE), "compress": list(COMPRESS)}
    if not e:
        return {"groups": [], "fx": DEFAULT_FX, "music": None, "presets": presets}
    a = e.get("audio") or {}
    return {"groups": groups_in_use(p, e), "fx": {**DEFAULT_FX, **(a.get("fx") or {})}, "music": e.get("music"),
            "presets": presets}


class AudioPut(BaseModel):
    sources: dict | None = None     # take id -> member id; empty string = each camera keeps its own sound
    fx: dict | None = None
    music: dict | None = None
    clear_music: bool = False
    note: str = "звук"


@app.put("/api/audio")
def put_audio(req: AudioPut):
    from . import edl
    p = proj()
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Стратегия не утверждена (Hard Rule 11).")
    cur = edl.current(p)
    if not cur:
        raise HTTPException(409, "Сначала нужен черновик монтажа.")
    e = json.loads(json.dumps(cur))
    a = e.setdefault("audio", {})
    if req.sources is not None:
        a["sources"] = {**(a.get("sources") or {}), **req.sources}
    if req.fx is not None:
        a["fx"] = {**(a.get("fx") or {}), **req.fx}
    if req.clear_music:
        e.pop("music", None)
    elif req.music is not None:
        e["music"] = {**(e.get("music") or {}), **req.music}
    edl.save_version(p, e, "user", req.note)
    schedule_preview(p)
    return audio_info()


class SampleReq(BaseModel):
    time: float = 0
    seconds: float = 10


@app.post("/api/audio/sample")
def audio_sample(req: SampleReq):
    from . import edl
    from .audio import make_sample
    p = proj()
    e = edl.current(p)
    if not e:
        raise HTTPException(409, "Сначала нужен черновик монтажа.")

    def run(job: Job):
        r = make_sample(p, e, req.time, req.seconds)
        job.note = json.dumps(r, ensure_ascii=False)
        return r

    return queue.add(Job(kind="sample", label="Пробник звука: до / после", lane="io", priority=4, expected=3, fn=run)).public()


@app.get("/media/sample/{name}")
def media_sample(name: str):
    if name not in ("sample_before.wav", "sample_after.wav"):
        raise HTTPException(404)
    path = proj().edit / "verify" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-cache"})


@app.get("/api/music")
def music_list():
    from . import music
    return {"dir": str(music.music_dir()), "libraries": music.LIBRARIES, "tracks": music.list_tracks()}


@app.post("/api/music/scan")
def music_scan():
    from . import music
    return queue.add(Job(kind="music", label="Музыка: анализ треков в папке", lane="io", priority=8, expected=20,
                         fn=lambda job: music.scan(job))).public()


@app.get("/api/music/rank")
def music_rank(target: float = 60, pace: str = ""):
    from . import music
    return music.rank(target, pace or None)


class TrackPatch(BaseModel):
    library: str | None = None
    licence_note: str | None = None


@app.patch("/api/music/{tid}")
def music_patch(tid: str, req: TrackPatch):
    from . import music
    try:
        return music.update_track(tid, req.model_dump(exclude_none=True))
    except KeyError as e:
        raise HTTPException(404, "нет такого трека") from e


@app.get("/media/music/{tid}")
def media_music(tid: str):
    from . import music
    t = next((x for x in music.list_tracks() if x["id"] == tid), None)
    if not t:
        raise HTTPException(404)
    return FileResponse(t["path"], headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ colour (stage 7)


@app.get("/api/color")
def color_info():
    from . import edl
    from .color import PRESET_NAMES
    e = edl.current(proj())
    return {"presets": PRESET_NAMES, "color": (e or {}).get("color"),
            "episodes": {i: r["color"] for i, r in enumerate((e or {}).get("ranges", [])) if r.get("color")}}


class ColorPut(BaseModel):
    grade: dict | None = None       # None = remove (episodes fall back to the whole-cut grade)
    start: int | None = None        # episode span; both None = whole cut
    end: int | None = None


@app.put("/api/color")
def put_color(req: ColorPut):
    from . import edl
    p = proj()
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Стратегия не утверждена (Hard Rule 11).")
    cur = edl.current(p)
    if not cur:
        raise HTTPException(409, "Сначала нужен монтаж.")
    e = json.loads(json.dumps(cur))
    if req.start is None:
        if req.grade:
            e["color"] = req.grade
        else:
            e.pop("color", None)
        note = "цвет: весь ролик"
    else:
        a, b = sorted((req.start, req.end if req.end is not None else req.start))
        for i in range(max(0, a), min(len(e["ranges"]) - 1, b) + 1):
            if req.grade:
                e["ranges"][i]["color"] = req.grade
            else:
                e["ranges"][i].pop("color", None)
        note = f"цвет: эпизод {a + 1}" if a == b else f"цвет: эпизоды {a + 1}–{b + 1}"
    edl.save_version(p, e, "user", note)
    schedule_preview(p)
    return _edl_payload(p)


@app.get("/media/frame")
def media_frame(source: str, time: float, g: str = "", graded: int = 1):
    """A still for the before/after wipe: `g` is the grade as JSON (empty = untouched)."""
    from .color import frame
    p = proj()
    s = src(source)
    if not Path(s["path"]).exists():
        raise HTTPException(404, "исходник не найден на диске")
    grade = json.loads(g) if (g and graded) else None
    out = p.edit / "verify" / f"frame_{'after' if grade else 'before'}.jpg"
    try:
        frame(Path(s["path"]), time, grade, bool(((s["probe"] or {}).get("video") or {}).get("hdr")), out)
    except RuntimeError as e:
        raise HTTPException(500, f"кадр не получился: {e}") from e
    return Response(out.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ infographics (stage 6)


def _gfx_jobs(p: Project, sid: str, request: str | None, with_agent: bool) -> list[int]:
    from . import gfx
    ids = []
    deps = []
    if with_agent:
        j = queue.add(Job(kind="slot", label=f"Инфографика {sid}: агент строит карточку", lane="slots", heavy=True, priority=7,
                          expected=40, source=sid, fn=lambda job: gfx.build_spec(p, job, sid, request)))
        ids.append(j.id)
        deps = [j.id]

    def rnd(job: Job):
        gfx.render(p, job, sid)
        state["sources_version"] += 1
        schedule_preview(p)

    # one drawing per slot at a time: a queued one already reads the latest spec when it starts
    live = [j for j in queue.active() if j.kind == "slot-render" and j.source == sid]
    if not with_agent and any(j.status == "queued" for j in live):
        return ids
    deps = deps + [j.id for j in live if j.status == "running"]
    r = queue.add(Job(kind="slot-render", label=f"Инфографика {sid}: рисую", lane="io", priority=8, expected=5, source=sid,
                      deps=deps, fn=rnd))
    ids.append(r.id)
    return ids


@app.get("/api/gfx")
def gfx_info():
    from . import edl, gfx
    e = edl.current(proj())
    return {"brand": gfx.brand_of(e or {}), "slots": (e or {}).get("slots", [])}


class GfxBuild(BaseModel):
    ids: list[str] | None = None
    request: str | None = None


@app.post("/api/gfx/build")
def gfx_build(req: GfxBuild):
    """All slots at once: one agent per slot, in parallel (Hard Rule 10)."""
    from . import edl
    p = proj()
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Стратегия не утверждена (Hard Rule 11).")
    e = edl.current(p)
    slots = [s for s in (e or {}).get("slots", []) if not req.ids or s.get("id") in req.ids]
    if not slots:
        raise HTTPException(409, "Слотов нет: добавьте слот на таймлайн или попросите агента-редактора.")
    jobs = []
    for s in slots:
        jobs += _gfx_jobs(p, s["id"], req.request, True)
    return {"jobs": jobs}


@app.post("/api/gfx/{sid}/render")
def gfx_render(sid: str):
    return {"jobs": _gfx_jobs(proj(), sid, None, False)}


class GfxPut(BaseModel):
    spec: dict | None = None
    start_in_output: float | None = None
    duration: float | None = None
    spec_hint: str | None = None


@app.put("/api/gfx/{sid}")
def gfx_put(sid: str, req: GfxPut):
    from . import edl, gfx
    p = proj()
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Стратегия не утверждена (Hard Rule 11).")
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if "duration" in patch:
        patch["duration"] = max(2.5, min(20.0, patch["duration"]))
    if "start_in_output" in patch:
        patch["start_in_output"] = max(0.0, patch["start_in_output"])
    try:
        slot = gfx.update_slot(p, sid, patch, f"инфографика {sid}: " + ", ".join(patch), author="user")
    except KeyError as ex:
        raise HTTPException(404, "нет такого слота") from ex
    if ("spec" in patch or "duration" in patch) and isinstance(slot.get("spec"), dict):
        _gfx_jobs(p, sid, None, False)          # redraw; placement-only changes need no redraw
    else:
        schedule_preview(p)
    return _edl_payload(p)


class GfxAdd(BaseModel):
    at: float
    type: str = "list"
    duration: float = 5.0
    spec_hint: str = ""


@app.post("/api/gfx/add")
def gfx_add(req: GfxAdd):
    from . import edl, gfx
    p = proj()
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Стратегия не утверждена (Hard Rule 11).")
    with gfx._lock:
        cur = edl.current(p)
        if not cur:
            raise HTTPException(409, "Сначала нужен монтаж.")
        e = json.loads(json.dumps(cur))
        sid = f"s{int(time.time() * 1000) % 10_000_000}"
        e.setdefault("slots", []).append({"id": sid, "type": req.type, "spec_hint": req.spec_hint, "start_in_output": round(max(0.0, req.at), 3),
                                          "duration": req.duration, "status": "placeholder"})
        edl.save_version(p, e, "user", f"инфографика: новый слот {sid}")
    return _edl_payload(p)


@app.delete("/api/gfx/{sid}")
def gfx_delete(sid: str):
    from . import edl, gfx
    p = proj()
    with gfx._lock:
        cur = edl.current(p)
        e = json.loads(json.dumps(cur))
        e["slots"] = [s for s in e.get("slots", []) if s.get("id") != sid]
        edl.save_version(p, e, "user", f"инфографика: слот {sid} удалён")
    schedule_preview(p)
    return _edl_payload(p)


class BrandPut(BaseModel):
    brand: dict


@app.put("/api/brand")
def brand_put(req: BrandPut):
    from . import edl, gfx
    p = proj()
    with gfx._lock:
        cur = edl.current(p)
        if not cur:
            raise HTTPException(409, "Сначала нужен монтаж.")
        e = json.loads(json.dumps(cur))
        e["brand"] = {**gfx.brand_of(e), **req.brand, "confirmed": True}   # a person chose it: no more [NEED]
        edl.save_version(p, e, "user", "инфографика: палитра и шрифт")
    for s in e.get("slots", []):          # every drawn card follows the new look
        if isinstance(s.get("spec"), dict):
            _gfx_jobs(p, s["id"], None, False)
    return _edl_payload(p)


@app.get("/media/gfx/{sid}/{kind}")
def media_gfx(sid: str, kind: str):
    from . import gfx
    if kind not in ("poster", "preview"):
        raise HTTPException(404)
    from . import edl
    slot = next((x for x in (edl.current(proj()) or {}).get("slots", []) if x.get("id") == sid), None)
    f = Path(((slot or {}).get("files") or {}).get(kind, ""))
    if not slot or not f.is_file():
        raise HTTPException(404, "ещё не нарисовано")
    return FileResponse(f, media_type="image/png" if kind == "poster" else "video/webm", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ angle sync (stage 3)


@app.post("/api/sync")
def start_sync():
    from .sync import run_sync
    p = proj()
    j = queue.add(Job(kind="sync", label="Синхронизация ракурсов по звуку", lane="audio", priority=8,
                      expected=20, fn=lambda job: run_sync(p, job)))
    return j.public()


@app.get("/api/sync")
def get_sync():
    from .sync import load_sync
    return load_sync(proj()) or {"groups": [], "singles": []}


class NudgeReq(BaseModel):
    member: str
    frames: int | None = None    # absolute nudge
    delta: int | None = None     # relative nudge
    make_ref: bool = False


@app.post("/api/sync/nudge")
def nudge(req: NudgeReq):
    from .sync import load_sync, save_sync
    p = proj()
    data = load_sync(p)
    for g in data.get("groups", []):
        ids = [m["id"] for m in g["members"]]
        if req.member not in ids:
            continue
        if req.make_ref:
            # re-base offsets so the chosen member becomes the reference (offset 0)
            base = next(m for m in g["members"] if m["id"] == req.member)["offset"]
            for m in g["members"]:
                m["offset"] = round(m["offset"] - base, 4)
                m["status"] = "ref" if m["id"] == req.member else ("ok" if m["confidence"] >= 0.55 else "check")
            g["ref"] = req.member
        else:
            m = next(m for m in g["members"] if m["id"] == req.member)
            m["nudge_frames"] = req.frames if req.frames is not None else m.get("nudge_frames", 0) + (req.delta or 0)
            if m["status"] == "check":
                m["status"] = "manual"
        save_sync(p, data)
        state["sources_version"] += 1
        return data
    raise HTTPException(404, "источник не входит ни в одну группу")


# ------------------------------------------------------------------ ASR benchmark


class BenchReq(BaseModel):
    sources: list[str] | None = None
    seconds: float = 600


@app.post("/api/bench")
def start_bench(req: BenchReq):
    from .bench import run_bench
    p = proj()
    ids = req.sources or [s["id"] for s in sorted(
        (s for s in p.sources.values() if not s["duplicate_of"] and not s["excluded"] and (s["probe"] or {}).get("audio")),
        key=lambda s: -(s["probe"] or {}).get("duration", 0))]
    if not ids:
        raise HTTPException(409, "нет источников со звуком")
    j = queue.add(Job(kind="bench", label=f"Замер ASR · {int(req.seconds // 60)} мин материала", lane="asr", heavy=True,
                      priority=15, expected=req.seconds * 0.25, fn=lambda job: run_bench(p, job, ids, req.seconds)))
    return j.public()


@app.get("/api/bench")
def get_bench():
    path = proj().edit / "bench" / "result.json"
    if not path.exists():
        raise HTTPException(404, "замера ещё не было")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ jobs


@app.get("/api/jobs")
def jobs():
    return queue.snapshot()


@app.get("/api/jobs/summary")
def jobs_summary():
    act = queue.active()
    return {"active": len(act), "running": sum(1 for j in act if j.status == "running"),
            "render": sum(1 for j in act if j.lane == "render" and j.status == "running"),
            "heavy": sum(1 for j in act if j.heavy)}


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: int):
    return {"ok": queue.cancel(jid)}


@app.post("/api/jobs/{jid}/retry")
def retry(jid: int):
    j = queue.retry(jid)
    if not j:
        raise HTTPException(409, "задачу нельзя перезапустить")
    return j.public()


@app.post("/api/jobs/cancel_all")
def cancel_all():
    queue.cancel_all()
    return {"ok": True}


@app.post("/api/jobs/clear")
def clear_jobs():
    queue.clear_finished()
    return {"ok": True}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    if sock.query_params.get("t") != TOKEN:
        await sock.close(code=4401)
        return
    await sock.accept()
    last_v, last_s = -1, -1
    try:
        while True:
            if queue.version != last_v:
                last_v = queue.version
                await sock.send_text(json.dumps({"type": "jobs", "jobs": queue.snapshot()}, ensure_ascii=False))
            if state["sources_version"] != last_s:
                last_s = state["sources_version"]
                await sock.send_text(json.dumps({"type": "sources_dirty", "v": last_s}))
            await asyncio.sleep(0.25)
    except (WebSocketDisconnect, RuntimeError):
        return


@app.post("/api/shutdown")
def shutdown():
    queue.shutdown()
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


# ------------------------------------------------------------------ entry


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--token", default=None)
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    if args.token:
        TOKEN = args.token
    port = args.port or free_port()
    if args.project:
        try:
            p = Project(Path(args.project), queue)
            p.scan()
            state["project"] = p
        except Exception as e:  # noqa: BLE001
            print(f"project open failed: {e}", file=sys.stderr)

    # parent watchdog: if Electron dies, don't leave ffmpeg/python behind
    ppid = os.getppid()

    def watchdog():
        import psutil
        while True:
            time.sleep(2)
            if not psutil.pid_exists(ppid):
                queue.shutdown()
                time.sleep(0.5)
                os._exit(0)

    if os.environ.get("CUTROOM_PARENT_WATCH") == "1":
        threading.Thread(target=watchdog, daemon=True).start()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="websockets")
    server = uvicorn.Server(config)

    def announce():
        while not server.started:
            time.sleep(0.05)
        print(f"CUTROOM_READY {port}", flush=True)

    threading.Thread(target=announce, daemon=True).start()
    from . import claude_link
    claude_link.start_background()   # link to Claude Code right at launch
    try:
        server.run()
    finally:
        queue.shutdown()


if __name__ == "__main__":
    main()
