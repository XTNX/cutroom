"""Quick-mode API (/api/quick/*). State lives on disk: edit/quick/ + the quick_versions table."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import edl
from ..jobs import Job
from . import ai, build, cards, model, variants
from .model import qdir

router = APIRouter()
SERVER_OWNED = ("spec", "files", "status", "error", "variants", "variants_spec", "drawn_len", "drawn_style")


_server = None


def bind(server_module) -> None:
    """The running server module (python -m cutroom.server runs it as __main__, so importing
    cutroom.server here would make a second, empty copy of its state)."""
    global _server
    _server = server_module


def S():
    if _server is None:
        from .. import server
        return server
    return _server


def proj():
    return S().proj()


def bump():
    S().state["sources_version"] += 1


def mode_path(p) -> Path:
    return qdir(p) / "state.json"


def get_mode(p) -> str:
    try:
        return json.loads(mode_path(p).read_text(encoding="utf-8")).get("mode", "full")
    except Exception:
        return "full"


def set_mode(p, m: str) -> None:
    mode_path(p).write_text(json.dumps({"mode": m}), encoding="utf-8")


def gate(p) -> None:
    if not edl.strategy_approved(p):
        raise HTTPException(423, "Сначала шаг 2: нарезка по промпту и «Применить» (это утверждение стратегии, Hard Rule 11).")


def doc_or_409(p) -> dict:
    d = model.current(p)
    if not d:
        raise HTTPException(409, "Проект быстрого режима ещё не создан.")
    return d


def payload(p) -> dict:
    from ..brand import load as load_brand
    d = model.current(p)
    cov = {}
    if d:
        for v in d["tracks"]["video"]:
            cov[v["id"]] = model.coverage(p, d, v)
    b = load_brand()
    return {"doc": d, "history": model.history(p), "mode": get_mode(p), "gate_open": edl.strategy_approved(p),
            "proposal": ai.summary_of(ai.load_proposal(p)), "coverage": cov,
            "build": {"draft": build.last(p, "draft"), "final": build.last(p, "final")},
            "brand": {"found": b["found"], "need": b["need"], "colors": b["colors"], "note": b["note"]},
            "styles": {k: v["label"] for k, v in build.STYLES.items()}}


def schedule_build(p, delay: float = 1.2):
    if model.current(p) and edl.strategy_approved(p):
        build.schedule(p, S().queue, "draft", delay, on_done=lambda st: bump())


def card_jobs(p, oid: str, request: str | None = None, with_spec: bool = True) -> list[int]:
    q = S().queue
    ids, deps = [], []
    if with_spec:
        j = q.add(Job(kind="qcard", label=f"Карточка {oid}: агент пишет содержание", lane="slots", heavy=True, priority=7, expected=40,
                      source=oid, fn=lambda job: (cards.build_spec(p, job, oid, request), bump())))
        ids.append(j.id)
        deps = [j.id]
    live = [j for j in q.active() if j.kind == "qcard-draw" and j.source == oid]
    if not with_spec and any(j.status == "queued" for j in live):
        return ids
    deps += [j.id for j in live if j.status == "running"]

    def draw(job: Job):
        cards.draw(p, job, oid)
        bump()
        schedule_build(p, 0.5)

    r = q.add(Job(kind="qcard-draw", label=f"Карточка {oid}: рисую", lane="io", priority=8, expected=6, source=oid, deps=deps, fn=draw))
    return ids + [r.id]


# ---------------------------------------------------------------- document


@router.get("/api/quick")
def get_quick():
    return payload(proj())


class CreateReq(BaseModel):
    roles: dict[str, str] | None = None


@router.post("/api/quick/create")
def create(req: CreateReq):
    from .convert import from_full, project_rate
    p = proj()
    d = model.current(p)
    if d is None:
        e = edl.current(p)
        if e and e.get("ranges"):
            d, _ = from_full(p, e, None)
            note = "из полного режима"
        else:
            d = model.empty(project_rate(p))
            note = "новый проект"
        if req.roles:
            d["roles"] = {k: v for k, v in req.roles.items() if v in model.ROLES}
        model.save(p, d, "user", note)
    set_mode(p, "quick")
    return payload(p)


class RolesReq(BaseModel):
    roles: dict[str, str]


@router.put("/api/quick/roles")
def put_roles(req: RolesReq):
    p = proj()
    d = doc_or_409(p)
    d["roles"] = {**d.get("roles", {}), **{k: v for k, v in req.roles.items() if v in model.ROLES}}
    model.save(p, d, "user", "роли камер")
    bump()
    return payload(p)


class DocPut(BaseModel):
    doc: dict
    note: str = "правка"


@router.put("/api/quick/doc")
def put_doc(req: DocPut):
    p = proj()
    gate(p)
    redraw = []
    with cards.lock:
        cur = doc_or_409(p)
        old = {o["id"]: o for o in cur["tracks"]["overlays"]}
        d = req.doc
        for k in ("roles", "presentation", "music", "rate"):
            d[k] = cur.get(k)
        for o in d["tracks"].get("overlays", []):
            if o["id"] in old:   # spec, drawing and status belong to the server
                for k in SERVER_OWNED:
                    if k in old[o["id"]]:
                        o[k] = old[o["id"]][k]
                    else:
                        o.pop(k, None)
            if isinstance(o.get("spec"), dict) and o.get("status") == "ready" and (o.get("drawn_len") != o["len"] or o.get("drawn_style") != o.get("style")):
                o["status"] = "drawing"
                redraw.append(o["id"])
        model.save(p, d, "user", req.note[:120])
    for oid in redraw:
        card_jobs(p, oid, with_spec=False)
    schedule_build(p)
    bump()
    return payload(p)


class AngleReq(BaseModel):
    ids: list[str]
    role: str


@router.post("/api/quick/angle")
def set_angle(req: AngleReq):
    """Click on the other camera row: those blocks show that angle (only where synced footage exists)."""
    p = proj()
    gate(p)
    d = doc_or_409(p)
    errs = []
    for v in d["tracks"]["video"]:
        if v["id"] in req.ids:
            e = model.switch_angle(p, d, v, req.role)
            if e:
                errs.append(e)
    if len(errs) == len(req.ids):
        raise HTTPException(409, errs[0] if errs else "блоки не найдены")
    model.save(p, d, "user", f"ракурс: {model.ROLE_NAMES.get(req.role, req.role)} ({len(req.ids) - len(errs)} бл.)")
    schedule_build(p, 0.5)
    bump()
    return {**payload(p), "warnings": errs}


@router.post("/api/quick/undo")
def undo():
    return _move(-1)


@router.post("/api/quick/redo")
def redo():
    return _move(1)


def _move(step: int):
    p = proj()
    if model.move_head(p, step) is None:
        raise HTTPException(409, "Дальше некуда.")
    schedule_build(p)
    bump()
    return payload(p)


# ---------------------------------------------------------------- AI


class CutReq(BaseModel):
    prompt: str


@router.post("/api/quick/cut")
def cut(req: CutReq):
    p = proj()
    doc_or_409(p)
    if not req.prompt.strip():
        raise HTTPException(400, "Опишите, какой ролик нужен.")
    if not any(model.role(model.current(p), p, s) in ("front", "top", "side") for s in p.sources):
        raise HTTPException(409, "Отметьте хотя бы одну камеру как «Фронт» (шаг 1).")

    def run(job: Job):
        ai.run_cut(p, job, req.prompt)
        bump()

    return S()._agent_job("qcut", "Нарезка по промпту: агент выбирает фразы и ракурсы", run).public()


class AiReq(BaseModel):
    prompt: str
    selection: dict | None = None


@router.post("/api/quick/ai")
def ai_request(req: AiReq):
    p = proj()
    gate(p)
    d = doc_or_409(p)
    if not req.prompt.strip():
        raise HTTPException(400, "Пустой запрос.")
    if ai.fast_parse(req.prompt, ai.selection_frames(d, req.selection) is not None):
        prop = ai.run_request(p, None, req.prompt, req.selection)   # instant: no agent call
        bump()
        return {"proposal": ai.summary_of(prop), "job": None}

    def run(job: Job):
        ai.run_request(p, job, req.prompt, req.selection)
        bump()

    return {"proposal": None, "job": S()._agent_job("qai", f"Запрос к ИИ: {req.prompt[:50]}", run).public()}


@router.get("/api/quick/proposal")
def get_proposal():
    p = proj()
    prop = ai.load_proposal(p)
    d = prop.get("doc") if prop else None
    cov = {v["id"]: model.coverage(p, d, v) for v in d["tracks"]["video"]} if d else {}
    return {"proposal": ai.summary_of(prop), "doc": d, "coverage": cov}


@router.post("/api/quick/proposal/apply")
def apply_proposal():
    p = proj()
    try:
        _, follow = ai.apply(p)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    for oid in follow.get("spec", []):
        card_jobs(p, oid, follow.get("requests", {}).get(oid))
    for oid in follow.get("draw", []):
        card_jobs(p, oid, with_spec=False)
    schedule_build(p, 0.3)
    bump()
    return payload(p)


@router.post("/api/quick/proposal/cancel")
def cancel_proposal():
    p = proj()
    ai.drop_proposal(p)
    bump()
    return payload(p)


# ---------------------------------------------------------------- presentation, cards, variants


class PathReq(BaseModel):
    path: str


@router.post("/api/quick/presentation")
def presentation(req: PathReq):
    from .slides import SlideError, import_presentation
    p = proj()
    d = doc_or_409(p)
    try:
        d["presentation"] = import_presentation(p, req.path)
    except SlideError as e:
        raise HTTPException(400, str(e)) from e
    model.save(p, d, "user", f"презентация: {d['presentation']['name']}")
    bump()
    return payload(p)


class CardReq(BaseModel):
    request: str | None = None


@router.post("/api/quick/cards/{oid}/build")
def card_build(oid: str, req: CardReq):
    p = proj()
    gate(p)
    return {"jobs": card_jobs(p, oid, req.request)}


@router.post("/api/quick/variants/card/{oid}")
def card_variants(oid: str, req: CardReq):
    p = proj()
    gate(p)

    def run(job: Job):
        variants.card_variants(p, job, oid, req.request)
        bump()

    q = S().queue
    j = q.add(Job(kind="qvariants", label=f"Варианты карточки {oid}", lane="slots" if req.request else "io", heavy=bool(req.request),
                  priority=6, expected=45 if req.request else 6, source=oid, fn=run))
    return j.public()


class StyleReq(BaseModel):
    style: str


@router.post("/api/quick/variants/card/{oid}/apply")
def card_apply(oid: str, req: StyleReq):
    from ..brand import STYLES
    p = proj()
    gate(p)
    if req.style not in STYLES:
        raise HTTPException(400, "нет такого стиля")
    with cards.lock:
        d = doc_or_409(p)
        o = next((x for x in d["tracks"]["overlays"] if x["id"] == oid), None)
        if not o:
            raise HTTPException(404, "карточка не найдена")
        o["style"] = req.style
        if isinstance(o.get("variants_spec"), dict):
            o["spec"] = o["variants_spec"]
        o["status"] = "drawing"
        model.save(p, d, "user", f"карточка {oid}: стиль «{STYLES[req.style]}»")
    card_jobs(p, oid, with_spec=False)
    bump()
    return payload(p)


@router.post("/api/quick/variants/sound/{aid}")
def sound_variants(aid: str):
    p = proj()
    gate(p)

    def run(job: Job):
        variants.sound_variants(p, job, aid)
        bump()

    return S().queue.add(Job(kind="qaudition", label=f"Варианты звука {aid}", lane="audio", priority=6, expected=8, source=aid, fn=run)).public()


@router.get("/api/quick/variants/sound/{aid}")
def sound_variants_get(aid: str):
    return {"variants": variants.load_sound_variants(proj(), aid)}


@router.post("/api/quick/variants/sound/{aid}/apply")
def sound_apply(aid: str, req: StyleReq):
    p = proj()
    gate(p)
    if req.style not in build.STYLES:
        raise HTTPException(400, "нет такого стиля")
    d = doc_or_409(p)
    a = next((x for x in d["tracks"]["audio"] if x["id"] == aid), None)
    if not a:
        raise HTTPException(404, "звуковой блок не найден")
    a["style"] = req.style
    model.save(p, d, "user", f"звук {aid}: «{build.STYLES[req.style]['label']}»")
    schedule_build(p, 0.3)
    bump()
    return payload(p)


class MusicReq(BaseModel):
    track: str | None = None


@router.put("/api/quick/music")
def put_music(req: MusicReq):
    from .. import music
    p = proj()
    d = doc_or_409(p)
    if req.track:
        t = next((x for x in music.list_tracks() if x["id"] == req.track), None)
        if not t:
            raise HTTPException(404, "трек не найден на полке")
        d["music"] = {"track": t["id"], "path": t["path"], "title": t.get("title") or Path(t["path"]).stem}
    else:
        d["music"] = None
    model.save(p, d, "user", "музыка: " + ((d["music"] or {}).get("title") or "без музыки"))
    schedule_build(p, 0.3)
    bump()
    return payload(p)


# ---------------------------------------------------------------- colour presets


@router.get("/api/quick/presets")
def presets():
    from ..brand import load, presets as all_presets
    b = load(force=True)
    return {"presets": all_presets(), "brand": {"found": b["found"], "need": b["need"], "note": b["note"]}}


class PresetReq(BaseModel):
    name: str
    grade: dict


@router.post("/api/quick/presets")
def save_preset(req: PresetReq):
    from ..brand import save_preset as sp
    if not req.name.strip():
        raise HTTPException(400, "Дайте пресету имя.")
    return sp(req.name.strip(), req.grade)


@router.delete("/api/quick/presets/{pid}")
def delete_preset(pid: str):
    from ..brand import delete_preset as dp
    dp(pid)
    return {"ok": True}


# ---------------------------------------------------------------- build, export, mode


@router.post("/api/quick/build")
def build_now():
    p = proj()
    gate(p)
    return build.schedule(p, S().queue, "draft", 0, on_done=lambda st: bump()).public()


@router.post("/api/quick/export")
def export():
    p = proj()
    gate(p)
    return build.schedule(p, S().queue, "final", 0, on_done=lambda st: bump()).public()


@router.get("/api/quick/log")
def build_log():
    f = qdir(proj()) / "logs" / "build.log"
    return {"log": f.read_text(encoding="utf-8")[-6000:] if f.exists() else ""}


class SwitchReq(BaseModel):
    to: str


@router.post("/api/quick/switch")
def switch(req: SwitchReq):
    """Quick ⇄ Full: the project is carried over both ways; nothing is lost (see convert.py)."""
    from .convert import from_full, to_full
    p = proj()
    if req.to == "full":
        d = model.current(p)
        if d and d["tracks"]["video"]:
            gate(p)
            e = to_full(p, d)
            edl.save_version(p, e, "quick", f"из быстрого режима v{d['meta']['version_id']}")
            S().schedule_preview(p)
        set_mode(p, "full")
    else:
        e = edl.current(p)
        d = model.current(p)
        if e and e.get("ranges"):
            nd, warn = from_full(p, e, d)
            if d is None or json.dumps(nd["tracks"], sort_keys=True) != json.dumps(d["tracks"], sort_keys=True):
                model.save(p, nd, "full", f"из полного режима v{e['cutroom']['version_id']}")
        elif d is None:
            from .convert import project_rate
            model.save(p, model.empty(project_rate(p)), "user", "новый проект")
        set_mode(p, "quick")
    bump()
    return payload(p)


# ---------------------------------------------------------------- media


def _file(path: str | Path | None, mt: str):
    f = Path(path or "")
    if not path or not f.is_file():
        raise HTTPException(404, "файла нет")
    return FileResponse(f, media_type=mt, headers={"Cache-Control": "no-cache"})


@router.get("/media/quick/preview")
def media_preview():
    st = build.last(proj(), "draft")
    return _file((st or {}).get("file"), "video/mp4")


@router.get("/media/quick/export")
def media_export():
    st = build.last(proj(), "final")
    return _file((st or {}).get("file"), "video/mp4")


@router.get("/media/quick/card/{oid}/{kind}")
def media_card(oid: str, kind: str, style: str = ""):
    d = model.current(proj()) or {"tracks": {"overlays": []}}
    o = next((x for x in d["tracks"]["overlays"] if x["id"] == oid), None) or {}
    if style:
        return _file((o.get("variants") or {}).get(style), "image/png")
    if kind not in ("poster", "preview"):
        raise HTTPException(404)
    return _file((o.get("files") or {}).get(kind), "image/png" if kind == "poster" else "video/webm")


@router.get("/media/quick/slide/{n}")
def media_slide(n: int, thumb: int = 0):
    from .slides import page_of
    d = model.current(proj()) or {}
    pg = page_of(d, n)
    if not pg:
        raise HTTPException(404, "нет слайда")
    return _file(pg["thumb"] if thumb else pg["image"], "image/jpeg" if thumb else "image/png")


@router.get("/media/quick/audition/{aid}/{style}")
def media_audition(aid: str, style: str):
    v = variants.load_sound_variants(proj(), aid) or {}
    return _file((v.get(style) or {}).get("file"), "audio/wav")
