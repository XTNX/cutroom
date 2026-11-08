"""Infographic cards on the quick Overlays track: the slot agent writes the spec (from the speech
around the card and, when the card comes from a slide, the slide itself), gfx_render draws it
in the chosen brand style. Shared with full mode through gfx.ask_spec / gfx.draw."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .. import agent, gfx
from ..brand import gfx_brand
from ..jobs import Job
from . import model
from .model import fps_of, qdir
from .slides import page_of

lock = threading.Lock()   # cards finish concurrently: serialise read-modify-write of the document


def card_dir(project, oid: str) -> Path:
    d = qdir(project) / "cards" / oid
    d.mkdir(parents=True, exist_ok=True)
    return d


def patch_card(project, oid: str, patch: dict) -> dict | None:
    """Server-owned card fields, written into the current version (no undo step)."""
    with lock:
        doc = model.current(project)
        if not doc:
            return None
        o = next((x for x in doc["tracks"]["overlays"] if x["id"] == oid), None)
        if o is None:
            return None
        o.update(patch)
        model.patch_in_place(project, doc)
        return o


def words_at(project, doc: dict, f0: int, f1: int, pad: float = 8.0) -> str:
    """What is said around frames [f0, f1) of the finished video, from the sound blocks' transcripts."""
    from ..edl import Words
    fps = fps_of(doc)
    out = []
    for a in doc["tracks"]["audio"]:
        s, e = a["start"], a["start"] + a["len"]
        lo, hi = f0 - pad * fps, f1 + pad * fps
        if e <= lo or s >= hi:
            continue
        t0 = a["in"] + max(0, lo - s) / fps
        t1 = a["in"] + (min(e, hi) - s) / fps
        txt = Words(project.paths(a["src"])["transcript"]).text_between(t0, t1)
        if txt:
            out.append(f"[{model.fmt(max(s, lo) / fps)}] {txt}")
    return "\n".join(out) or "(речи рядом нет)"


def build_spec(project, job: Job, oid: str, request: str | None = None) -> dict:
    doc = model.current(project)
    o = next((x for x in doc["tracks"]["overlays"] if x["id"] == oid), None) if doc else None
    if o is None:
        raise RuntimeError(f"нет карточки {oid}")
    patch_card(project, oid, {"status": "working", "error": None})
    fps = fps_of(doc)
    parts = [f"Спецификация карточки {oid}, длительность {o['len'] / fps:.1f} с.",
             agent.wrap_data("задумано", o.get("hint") or "(по смыслу речи)")]
    speech = words_at(project, doc, o["start"], o["start"] + o["len"])
    images = None
    pg = page_of(doc, o.get("page")) if o.get("page") else None
    if pg:
        # a card «по слайду» conveys the slide: its figures and labels; the speech only says which of them matter now
        src = "из базы знаний (слайд — картинка)" if pg.get("text_src") == "kb" else "со слайда"
        parts.append(agent.wrap_data(f"слайд {pg['n']}: текст {src}", pg.get("text") or "(текстового слоя нет — смотри картинку слайда)"))
        if not pg.get("text"):
            images = [Path(pg["image"])]
        parts.append(agent.wrap_data("речь в этот момент (только чтобы выбрать, что со слайда важно сейчас)", speech))
        parts.append("Карточка строится по этому слайду: цифры, подписи и заголовок бери со слайда, не подменяй их "
                     "тем, что звучит в речи. Выбери с него главное (2–5 пунктов или одно сравнение), коротко, и оживи анимацией.")
    else:
        parts.append(agent.wrap_data("речь вокруг момента появления", speech))
    if isinstance(o.get("spec"), dict):
        parts.append(agent.wrap_data("прошлая версия карточки", json.dumps(o["spec"], ensure_ascii=False)))
    if request:
        parts.append(agent.wrap_data("пожелание человека", request))
    job.log.append(f"агент карточки {oid} думает…")
    try:
        spec = gfx.ask_spec(project, parts, images=images)
    except Exception as ex:
        patch_card(project, oid, {"status": "error", "error": str(ex)[:300]})
        raise
    return patch_card(project, oid, {"spec": spec.model_dump(), "status": "drawing"}) or {}


def draw(project, job: Job, oid: str) -> dict:
    doc = model.current(project)
    o = next((x for x in doc["tracks"]["overlays"] if x["id"] == oid), None) if doc else None
    if not o or not isinstance(o.get("spec"), dict):
        raise RuntimeError(f"у карточки {oid} нет содержания")
    base = card_dir(project, oid)
    try:
        files = gfx.draw(job, o["spec"], gfx_brand(o.get("style") or "cream"), o["len"] / fps_of(doc), fps_of(doc), base)
    except RuntimeError as ex:
        patch_card(project, oid, {"status": "error", "error": str(ex)})
        raise
    out = patch_card(project, oid, {"status": "ready", "files": files, "error": None, "drawn_len": o["len"], "drawn_style": o.get("style")})
    gfx.prune(base, {str(Path(files["render"]).parent)} | {str(Path(v).parent) for v in (o.get("variants") or {}).values()})
    try:
        from .. import kb
        kb.index_graphic(project, oid, o["spec"], o.get("style") or "cream", files, "quick")
    except Exception:  # noqa: BLE001 — the index is best effort
        pass
    return out or {}
