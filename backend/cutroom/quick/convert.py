"""Quick ⇄ Full mode: one project, two views of it.

to_full:   video blocks → EDL ranges (picture source as `angle`, or `picture` for a cutaway from an
           unsynced clip), cards → infographic slots, slides → image overlays; gaps are closed.
           The full quick document rides along in edl["quick"] with a signature of what it
           produced, so coming back without edits restores it exactly (styles, gaps, links).
from_full: when the full-mode edit changed the cut, blocks are rebuilt from the ranges, and the
           quick-only parts (slides, card styles, speech styles) are kept by id.
"""

from __future__ import annotations

import hashlib
import json

from . import model
from .model import fps_of


def _sig(e: dict) -> str:
    keep = {"r": [(r["source"], round(r["start"], 3), round(r["end"], 3), r.get("angle"), json.dumps(r.get("picture"), sort_keys=True),
                   json.dumps(r.get("color"), sort_keys=True)) for r in e.get("ranges", [])],
            "s": [(s.get("id"), round(float(s.get("start_in_output", 0)), 3), round(float(s.get("duration", 0)), 3),
                   json.dumps(s.get("spec"), sort_keys=True, ensure_ascii=True), s.get("status")) for s in e.get("slots", [])],
            "o": [(o.get("quick_slide"), round(float(o.get("start_in_output", 0)), 3), round(float(o.get("duration", 0)), 3))
                  for o in e.get("overlays", [])],
            "g": json.dumps(e.get("color"), sort_keys=True)}
    return hashlib.sha256(json.dumps(keep, ensure_ascii=True).encode()).hexdigest()[:16]


def speech_source(project, doc: dict, take: str, r0: float, r1: float, fallback: str) -> str:
    """A camera of the take that covers the moment and has words (full mode reads its transcript)."""
    for want in ("front", "top", "side", "other"):
        sid = model.covering(project, doc, take, r0, r1, want)
        if sid and not model.is_audio_only(project, sid):
            return sid
    return fallback


def blocks_from_ranges(project, doc: dict, ranges: list[dict], style_of=None) -> tuple[list, list, list[str]]:
    """Ranges (source time) → linked video + audio blocks laid end to end. Each range may name
    a picture `role` (front/top/side), an explicit `angle` source, or a `picture` cutaway."""
    fps = fps_of(doc)
    eff = model.offsets(project)
    video, audio, warn = [], [], []
    clock, done = 0.0, 0
    for r in ranges:
        src = r["source"]
        if src not in project.sources:
            continue
        dur = float(r["end"]) - float(r["start"])
        clock += dur
        n = max(1, round(clock * fps) - done)
        take, e = eff[src]
        r0 = float(r["start"]) + e
        r1 = r0 + n / fps
        if r.get("picture") and r["picture"].get("source") in project.sources:
            psrc, pin = r["picture"]["source"], float(r["picture"]["start"])
        elif r.get("angle") and r["angle"] in eff and eff[r["angle"]][0] == take:
            psrc, pin = r["angle"], r0 - eff[r["angle"]][1]
        elif r.get("role"):
            psrc = model.covering(project, doc, take, r0, r1, r["role"])
            if psrc is None:
                warn.append(f"{model.fmt(done / fps)}: {model.ROLE_NAMES.get(r['role'], r['role'])} — синхронного кадра нет, оставлен фронт")
                psrc = speech_source(project, doc, take, r0, r1, src)
            pin = r0 - eff[psrc][1]
        else:
            psrc, pin = src, float(r["start"])
        if model.is_audio_only(project, psrc):   # a lav can't be a picture
            psrc = speech_source(project, doc, take, r0, r1, src)
            pin = r0 - eff[psrc][1]
        lav = model.covering(project, doc, take, r0, r1, "lav")
        asrc = lav or src
        vid = model.new_id("v")
        v = {"id": vid, "start": done, "len": n, "src": psrc, "in": round(pin, 4)}
        if r.get("color"):
            v["color"] = r["color"]
        video.append(v)
        audio.append({"id": model.new_id("a"), "start": done, "len": n, "src": asrc, "in": round(r0 - eff[asrc][1], 4),
                      "style": (style_of(r) if style_of else None) or "clean", "link": vid})
        done += n
    return video, audio, warn


def _out_time(doc: dict, frame: int) -> float:
    """Quick frame → full-mode output seconds (full mode has no gaps on the picture track)."""
    fps = fps_of(doc)
    t = 0
    for v in doc["tracks"]["video"]:
        if frame <= v["start"]:
            break
        t += min(v["len"], frame - v["start"])
    return t / fps


def to_full(project, doc: dict) -> dict:
    from .. import edl
    from ..brand import gfx_brand
    from .build import STYLES
    from .slides import compose
    from ..brand import load as load_brand
    fps = fps_of(doc)
    eff = model.offsets(project)
    ranges = []
    for v in doc["tracks"]["video"]:
        take, r0 = model.take_time(project, doc, v)
        r1 = r0 + v["len"] / fps
        a = next((x for x in doc["tracks"]["audio"] if x.get("link") == v["id"]), None)
        sp = a["src"] if a and not model.is_audio_only(project, a["src"]) else speech_source(project, doc, take, r0, r1, v["src"])
        s0 = r0 - eff[sp][1]
        r = {"source": sp, "start": round(s0, 4), "end": round(s0 + v["len"] / fps, 4), "locked": True, "quick_block": v["id"]}
        if v["src"] != sp:
            if eff[v["src"]][0] == eff[sp][0]:
                r["angle"] = v["src"]
            else:   # cutaway from a clip that isn't synced with the take
                r["picture"] = {"source": v["src"], "start": v["in"]}
        if v.get("color"):
            r["color"] = v["color"]
        ranges.append(r)
    slots = []
    for o in doc["tracks"]["overlays"]:
        s = {"id": o["id"], "type": (o.get("spec") or {}).get("type", "list"), "start_in_output": round(_out_time(doc, o["start"]), 3),
             "duration": round(o["len"] / fps, 3), "status": o.get("status", "placeholder"), "spec_hint": o.get("hint", "")}
        if isinstance(o.get("spec"), dict):
            s["spec"] = o["spec"]
        if o.get("files"):
            s["files"] = o["files"]
        slots.append(s)
    e = edl.normalize(project, ranges, slots, snap=False)
    colors = load_brand()["colors"]
    e["overlays"] = []
    for sl in doc["tracks"]["slides"]:
        f = compose(doc, sl["page"], sl.get("layout", "side"), colors)
        if f:
            e["overlays"].append({"image": str(f), "start_in_output": round(_out_time(doc, sl["start"]), 3),
                                  "duration": round(sl["len"] / fps, 3), "quick_slide": sl["id"]})
    if doc.get("grade"):
        e["color"] = doc["grade"]
    styles = [a.get("style") or "clean" for a in doc["tracks"]["audio"]]
    main = max(set(styles), key=styles.count) if styles else "clean"
    e["audio"] = {"fx": STYLES.get(main, STYLES["clean"])["fx"]}
    if doc.get("music") and any(STYLES.get(s, {}).get("bed") is not None for s in styles):
        lvl = STYLES[next(s for s in styles if STYLES.get(s, {}).get("bed") is not None)]["bed"]
        e["music"] = {**doc["music"], "start_in_output": 0, "offset": 0, "gain_db": lvl, "duck_db": -10, "fade_in": 1.0, "fade_out": 2.0, "loop": True}
    e["brand"] = gfx_brand((doc["tracks"]["overlays"][0].get("style") if doc["tracks"]["overlays"] else None) or "cream")
    e["cutroom"]["strategy_hash"] = (doc.get("meta") or {}).get("strategy_hash")
    e["quick"] = {"doc": doc, "sig": _sig(e)}
    return e


def from_full(project, e: dict, prev: dict | None) -> tuple[dict, list[str]]:
    q = e.get("quick") or {}
    if q.get("doc") and q.get("sig") == _sig(e):
        return json.loads(json.dumps(q["doc"])), []
    base = prev or q.get("doc") or model.empty(project_rate(project, e))
    doc = json.loads(json.dumps(base))
    fps = fps_of(doc)
    old_style = {}
    for a in doc["tracks"]["audio"]:
        old_style[a.get("link")] = a.get("style")
    by_block = {r.get("quick_block"): r for r in e.get("ranges", []) if r.get("quick_block")}
    video, audio, warn = blocks_from_ranges(project, doc, e.get("ranges", []),
                                            style_of=lambda r: old_style.get(r.get("quick_block")))
    doc["tracks"]["video"], doc["tracks"]["audio"] = video, audio
    old_ov = {o["id"]: o for o in doc["tracks"]["overlays"]}
    ovs = []
    for s in e.get("slots", []):
        if "start_in_output" not in s:
            continue
        o = dict(old_ov.get(s["id"]) or {"id": s["id"], "style": "cream"})
        o.update({"start": round(float(s["start_in_output"]) * fps), "len": max(1, round(float(s.get("duration", 5)) * fps)),
                  "status": s.get("status", "placeholder"), "hint": s.get("spec_hint", o.get("hint", ""))})
        if isinstance(s.get("spec"), dict):
            o["spec"] = s["spec"]
        if s.get("files"):
            o["files"] = s["files"]
        ovs.append(o)
    doc["tracks"]["overlays"] = ovs
    old_sl = {x["id"]: x for x in doc["tracks"]["slides"]}
    doc["tracks"]["slides"] = [{**old_sl[o["quick_slide"]], "start": round(float(o["start_in_output"]) * fps),
                                "len": max(1, round(float(o["duration"]) * fps))}
                               for o in e.get("overlays", []) if o.get("quick_slide") in old_sl]
    doc["grade"] = e.get("color")
    del by_block
    return doc, warn


def project_rate(project, e: dict | None = None) -> str:
    import sys
    from ..config import VIDEO_USE_HELPERS
    sys.path.insert(0, str(VIDEO_USE_HELPERS))
    from render import probe_source_fps
    from pathlib import Path
    ids = [r["source"] for r in (e or {}).get("ranges", [])] or [s["id"] for s in project.sources.values()
                                                                 if not model.is_audio_only(project, s["id"]) and not s.get("duplicate_of")]
    for sid in ids:
        p = Path(project.sources[sid]["path"])
        if p.exists():
            r = probe_source_fps(p)
            if r:
                return r
    return "25/1"
