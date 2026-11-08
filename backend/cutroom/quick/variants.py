"""Variants dialogs (right-click on a card / on the sound): options for ONE selected block.

Cards: the block's content drawn as a poster in each brand style (fast, poster-only), optionally
after the slot agent rewrote the content by a refinement prompt. Sound: a short audition of the
block in each speech style (the same chain and bed level the build uses). Choosing a variant
changes only that block."""

from __future__ import annotations

import json
from pathlib import Path

from .. import gfx
from ..brand import STYLES as CARD_STYLES, gfx_brand
from ..jobs import Job
from . import model
from .build import DUCK, MUSIC_PREP, SR, STYLES as SOUND_STYLES, ff
from .cards import card_dir, patch_card
from .model import fps_of, qdir


def card_variants(project, job: Job, oid: str, request: str | None) -> dict:
    from .cards import build_spec
    if request:
        build_spec(project, job, oid, request)
    doc = model.current(project)
    o = next((x for x in doc["tracks"]["overlays"] if x["id"] == oid), None)
    if not o or not isinstance(o.get("spec"), dict):
        raise RuntimeError("У карточки ещё нет содержания: сначала её постройте.")
    out = {}
    for i, style in enumerate(CARD_STYLES):
        f = gfx.draw(None, o["spec"], gfx_brand(style), o["len"] / fps_of(doc), fps_of(doc), card_dir(project, oid), poster_only=True)
        out[style] = f["poster"]
        job.progress = (i + 1) / len(CARD_STYLES)
    patch_card(project, oid, {"variants": out, "variants_spec": o["spec"]})
    return out


def sound_variants(project, job: Job, aid: str, seconds: float = 10.0) -> dict:
    """WAV auditions of the sound block in each style (first `seconds` of it)."""
    from ..audio import fx_chain
    doc = model.current(project)
    a = next((x for x in doc["tracks"]["audio"] if x["id"] == aid), None)
    if not a:
        raise RuntimeError("Звуковой блок не найден.")
    src = project.sources[a["src"]]
    dur = min(seconds, a["len"] / fps_of(doc))
    d = qdir(project) / "auditions"
    d.mkdir(exist_ok=True)
    music = (doc.get("music") or {}).get("path")
    out = {}
    for i, (style, st) in enumerate(SOUND_STYLES.items()):
        f = d / f"{aid}_{style}.wav"
        chain = fx_chain(st["fx"]) or "anull"
        pre = min(0.5, a["in"])
        inputs = ["-ss", f"{a['in'] - pre:.3f}", "-t", f"{dur + pre:.3f}", "-i", src["path"]]
        graph = f"[0:a:{src.get('audio_track', 0)}]{chain},atrim=start={pre:.3f},asetpts=PTS-STARTPTS,aresample={SR},aformat=channel_layouts=stereo[v]"
        label = "[v]"
        if st["bed"] is not None and music and Path(music).exists():
            inputs += ["-stream_loop", "-1", "-i", music]
            graph += (f";[1:a]{MUSIC_PREP},volume={st['bed']}dB,atrim=end={dur:.3f}[m];"
                      f"[v]asplit=2[v1][sc];[m][sc]{DUCK}[md];"
                      "[v1][md]amix=inputs=2:duration=first:normalize=0[mix]")
            label = "[mix]"
        graph += f";{label}loudnorm=I=-14:TP=-1:LRA=11[o]"
        ff(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]", "-ar", str(SR), "-c:a", "pcm_s16le", str(f)],
           f"вариант звука {st['label']}")
        out[style] = {"file": str(f), "label": st["label"], "bed": st["bed"] is not None, "no_music": st["bed"] is not None and not music}
        job.progress = (i + 1) / len(SOUND_STYLES)
    (d / f"{aid}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def load_sound_variants(project, aid: str) -> dict | None:
    try:
        return json.loads((qdir(project) / "auditions" / f"{aid}.json").read_text(encoding="utf-8"))
    except Exception:
        return None
