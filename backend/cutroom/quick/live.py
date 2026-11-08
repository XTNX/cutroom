"""Live infographic (quick mode): one brand layer over the whole film, like the reference «План запуска».

doc["live"] = {"spec": LiveSpec as dict, "anchors": {key: {src, t, word}}, "intro_frames": n}

Anchors. The agent names each moment by a spoken word and roughly when it sounds in the current
cut; the script finds that word in the transcript and stores it as SOURCE time (the file and the
second inside it). Rendering maps it back to the finished film through the sound blocks — so after
any re-cut (blocks moved, phrases cut out, angles switched) every element is still on its word.
That is the reference's old2cam → cam2new, done for every version automatically.

The layer is cut into slices along the picture segments (build.segments): each slice holds only
what is visible in it and is hashed, so an edit re-renders only the segments it touches.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import model
from .model import fps_of, qdir

LIVE_VERSION = 1
SPAN_PAD = 1.0   # seconds around a slice whose elements still matter (fades, morphs)


def norm(w: str) -> str:
    w = re.sub(r"[^\wё-]", "", (w or "").lower()).replace("ё", "е")
    return w


def stem(w: str) -> str:
    w = norm(w)
    return w[:max(4, len(w) - 2)] if len(w) > 5 else w


# ---------------------------------------------------------------- words on the finished-film clock


_WORDS: dict[str, tuple[float, list]] = {}


def _source_words(project, sid: str) -> list:
    from ..edl import Words
    p = project.paths(sid)["transcript"]
    try:
        m = p.stat().st_mtime
    except OSError:
        return []
    hit = _WORDS.get(str(p))
    if not hit or hit[0] != m:
        hit = (m, [w for w in Words(p).words if w.get("type") == "word"])
        _WORDS[str(p)] = hit
    return hit[1]


def words(project, doc: dict) -> list[dict]:
    """Every word under a sound block: {t0, t1, text, src, st} (t = film seconds, st = source seconds)."""
    fps = fps_of(doc)
    out = []
    cache: dict[str, list] = {}
    for a in doc["tracks"]["audio"]:
        if a["src"] not in cache:
            cache[a["src"]] = _source_words(project, a["src"])
        a0, a1 = a["in"], a["in"] + a["len"] / fps
        base = a["start"] / fps
        for w in cache[a["src"]]:
            if a0 <= w["start"] < a1:
                out.append({"t0": base + w["start"] - a0, "t1": base + min(w["end"], a1) - a0, "text": w["text"].strip(),
                            "src": a["src"], "st": w["start"]})
    out.sort(key=lambda w: w["t0"])
    return out


def speech(ws: list[dict], every: int = 10) -> str:
    """[мм:сс.с] word word … — the speech of the current cut for the agent."""
    lines, cur = [], []
    for i, w in enumerate(ws):
        if i % every == 0:
            if cur:
                lines.append(" ".join(cur))
            cur = [f"[{model.fmt(w['t0'])}]"]
        cur.append(w["text"])
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(lines) or "(речи нет: расшифровки ещё не готовы)"


def angles(project, doc: dict) -> list[list]:
    """[[t0, t1, role]] of the picture blocks (front / top / side)."""
    fps = fps_of(doc)
    out = []
    for v in doc["tracks"]["video"]:
        r = model.role(doc, project, v["src"])
        r = r if r in ("top", "side") else "front"
        t0, t1 = v["start"] / fps, (v["start"] + v["len"]) / fps
        if out and out[-1][2] == r and abs(out[-1][1] - t0) < 1e-6:
            out[-1][1] = t1
        else:
            out.append([round(t0, 4), round(t1, 4), r])
    return out


# ---------------------------------------------------------------- anchors


def find(ws: list[dict], word: str, at: float | None, window: float = 4.0) -> dict | None:
    """The spoken word nearest to `at` (film seconds) that matches `word`; else the word nearest to `at`."""
    if not ws:
        return None
    if at is None and not word:
        return None
    s = stem(word)
    cand = [w for w in ws if s and (norm(w["text"]).startswith(s) or s.startswith(norm(w["text"])[:max(4, len(s))]))]
    if at is not None:
        near = [w for w in cand if abs(w["t0"] - at) <= window]
        if near:
            return min(near, key=lambda w: abs(w["t0"] - at))
        return min(ws, key=lambda w: abs(w["t0"] - at))
    return cand[0] if cand else None


def to_film(project, doc: dict, an: dict | None, ws: list[dict] | None = None) -> float | None:
    """Source anchor → film seconds through the current sound blocks. A word that was cut out
    moves to the next kept word of the same file (within 30 s)."""
    if not an:
        return None
    fps = fps_of(doc)
    for a in doc["tracks"]["audio"]:
        if a["src"] == an["src"] and a["in"] - 1e-4 <= an["t"] < a["in"] + a["len"] / fps:
            return round(a["start"] / fps + an["t"] - a["in"], 4)
    ws = ws if ws is not None else words(project, doc)
    later = [w for w in ws if w["src"] == an["src"] and an["t"] < w["st"] <= an["t"] + 30]
    return round(min(later, key=lambda w: w["st"])["t0"], 4) if later else None


def element_keys(spec: dict) -> list[tuple[str, dict, str, str]]:
    """(key, element, word field, time field) for every anchored moment of the spec."""
    out = [("start", spec, "start_word", "start_at")]
    for i, m in enumerate(spec.get("months") or []):
        out.append((f"months.{i}", m, "word", "at"))
    for i, m in enumerate(spec.get("marks") or []):
        out.append((f"marks.{i}", m, "word", "at"))
        if m.get("highlight_word") or m.get("highlight_at") is not None:
            out.append((f"marks.{i}.hl", m, "highlight_word", "highlight_at"))
    for i, c in enumerate(spec.get("cards") or []):
        out.append((f"cards.{i}", c, "word", "at"))
        if c.get("until_word") or c.get("until_at") is not None:
            out.append((f"cards.{i}.until", c, "until_word", "until_at"))
    for i, w in enumerate(spec.get("windows") or []):
        out.append((f"windows.{i}", w, "word", "at"))
        out.append((f"windows.{i}.until", w, "until_word", "until_at"))
    if spec.get("cta"):
        out.append(("cta", spec["cta"], "word", "at"))
    return out


def set_spec(project, doc: dict, spec: dict, keep_anchors: dict | None = None) -> tuple[dict, list[str]]:
    """Store a spec in the document: anchor every moment to its word (source time). Moments whose
    word and time did not change keep their old anchor (an edit doesn't move what it didn't touch)."""
    ws = words(project, doc)
    old = (doc.get("live") or {})
    prev_spec = old.get("spec") or {}
    prev_anchors = keep_anchors if keep_anchors is not None else (old.get("anchors") or {})
    prev_vals = {k: (e.get(wf), e.get(tf)) for k, e, wf, tf in element_keys(prev_spec)}
    anchors, warn = {}, []
    for key, e, wf, tf in element_keys(spec):
        word, at = e.get(wf) or "", e.get(tf)
        if not word and at is None:
            continue
        if key in prev_anchors and prev_vals.get(key) == (word, at):
            anchors[key] = prev_anchors[key]
            continue
        w = find(ws, word, at)
        if w is None:
            warn.append(f"«{word or model.fmt(at or 0)}»: в речи не найдено — элемент не показан")
            continue
        if word and not norm(w["text"]).startswith(stem(word)[:4]):
            warn.append(f"«{word}» около {model.fmt(at or 0)} не нашлось — взято ближайшее слово «{w['text']}» ({model.fmt(w['t0'])})")
        anchors[key] = {"src": w["src"], "t": round(w["st"], 4), "word": w["text"]}
    live = {**old, "spec": spec, "anchors": anchors}
    doc["live"] = live
    _intro_gap(doc, spec)
    return doc, warn


def _intro_gap(doc: dict, spec: dict) -> None:
    """The intro gets its own seconds at the start: the whole film moves right by them (the reference's PRE)."""
    fps = fps_of(doc)
    want = round(float((spec.get("intro") or {}).get("seconds") or 0) * fps) if spec.get("intro") else 0
    have = int((doc.get("live") or {}).get("intro_frames") or 0)
    delta = want - have
    if delta < 0:   # never move anything before frame 0
        first = min([b["start"] for t in model.TRACKS for b in doc["tracks"][t]] or [0])
        delta = max(delta, -first)
    if delta:
        for t in model.TRACKS:
            for b in doc["tracks"][t]:
                b["start"] += delta
    doc["live"]["intro_frames"] = have + delta


def clear(doc: dict) -> dict:
    """Remove the layer (and the intro's seconds at the start)."""
    if doc.get("live"):
        _intro_gap(doc, {})
        doc.pop("live", None)
    return doc


# ---------------------------------------------------------------- the resolved layer


def subtitles(ws: list[dict], max_words: int = 6, max_s: float = 2.8, gap: float = 0.6) -> list[list]:
    out, cur = [], []
    for w in ws:
        if cur and (len(cur) >= max_words or w["t0"] - cur[-1]["t1"] > gap or w["t1"] - cur[0]["t0"] > max_s):
            out.append([round(cur[0]["t0"], 3), round(cur[-1]["t1"] + 0.15, 3), " ".join(x["text"] for x in cur)])
            cur = []
        cur.append(w)
    if cur:
        out.append([round(cur[0]["t0"], 3), round(cur[-1]["t1"] + 0.15, 3), " ".join(x["text"] for x in cur)])
    for i in range(len(out) - 1):   # a phrase never overlaps the next one
        out[i][1] = min(out[i][1], out[i + 1][0])
    return out


def brand() -> dict:
    """The brand system of the layer: the vault note's palette over the reference one; the
    reference fonts (Unbounded / Jura / Comfortaa) unless the note names `live_fonts`."""
    from ..brand import load
    b = load()
    # without the brand note: the reference palette (Cutroom's neutral greys are for the app, not the brand)
    c = (b.get("colors") or {}) if b.get("found") else {}
    lf = (b.get("fonts") or {}).get("live") or {}
    return {"cream": c.get("cream") or "#FFF9EB", "orange": c.get("orange") or "#FF7700", "khaki": c.get("khaki") or "#454335",
            "fonts": {k: v for k, v in lf.items() if k in ("head", "head5", "body", "bold", "caps")}}


def layer(project, doc: dict) -> tuple[dict | None, list[str]]:
    """Everything the renderer needs, in film seconds."""
    live = doc.get("live") or {}
    spec = live.get("spec")
    if not spec:
        return None, []
    fps = fps_of(doc)
    ws = words(project, doc)
    an = live.get("anchors") or {}
    warn = []

    def T(key: str):
        if key not in an:
            return None
        t = to_film(project, doc, an[key], ws)
        if t is None:
            warn.append(f"«{an[key].get('word')}» вырезано из ролика — элемент скрыт")
        return t

    total = model.total_frames(doc) / fps
    out = {"v": LIVE_VERSION, "title": spec.get("title") or "", "start": T("start"), "subtitles": bool(spec.get("subtitles", True)),
           "brand": brand(), "angles": angles(project, doc), "total": round(total, 3)}
    out["months"] = [{"label": m["label"], "t": T(f"months.{i}")} for i, m in enumerate(spec.get("months") or [])]
    out["marks"] = [{**{k: m.get(k) for k in ("kind", "text", "caption", "month", "pos")}, "t": T(f"marks.{i}"), "ht": T(f"marks.{i}.hl")}
                    for i, m in enumerate(spec.get("marks") or [])]
    out["cards"] = [{**{k: c.get(k) for k in ("group", "n", "title", "text", "tag", "note", "date", "month", "side")},
                     "t": T(f"cards.{i}"), "t1": T(f"cards.{i}.until")} for i, c in enumerate(spec.get("cards") or [])]
    wins = []
    for i, w in enumerate(spec.get("windows") or []):
        t0, t1 = T(f"windows.{i}"), T(f"windows.{i}.until")
        if t0 is not None and t1 is not None and t1 > t0 + 0.5:
            wins.append({"kind": w.get("kind") or "full", "group": w.get("group") or "stages", "t": t0, "t1": t1})
    wins.sort(key=lambda w: w["t"])
    for a, b in zip(wins, wins[1:]):   # windows never overlap: the earlier one ends where the next begins
        a["t1"] = min(a["t1"], b["t"])
    out["windows"] = wins
    if out["start"] is None:   # no anchor for the band: it comes with the first month or the first element
        firsts = [x["t"] for x in out["months"] + out["marks"] + out["cards"] if x.get("t") is not None]
        out["start"] = min(firsts) if firsts else None
    it = spec.get("intro")
    if it:
        n = int(live.get("intro_frames") or 0)
        pg = None
        if it.get("page"):
            from .slides import page_of
            p = page_of(doc, int(it["page"]))
            pg = p["image"] if p else None
        out["intro"] = {"title": it.get("title") or "", "subtitle": it.get("subtitle") or "", "poster": pg, "t0": 0.0, "t1": round(n / fps, 4)}
    cta = spec.get("cta")
    if cta:
        t0 = T("cta")
        if t0 is None:
            t0 = max(0.0, total - float(cta.get("seconds") or 6))
        out["cta"] = {"title": cta.get("title") or "", "lines": cta.get("lines") or [], "t0": t0, "t1": t0 + float(cta.get("seconds") or 6)}
    out["subs"] = subtitles(ws) if out["subtitles"] else []
    return out, warn


def end_frames(project, doc: dict) -> int:
    """The film is at least as long as the CTA page."""
    lay, _ = layer(project, doc)
    if not lay or not lay.get("cta"):
        return 0
    return int(round(lay["cta"]["t1"] * fps_of(doc)))


# ---------------------------------------------------------------- slices


def slice_of(lay: dict, t0: float, t1: float) -> dict | None:
    """What of the layer can be seen in [t0, t1): elements around it only (the band's own parts —
    title, months, marks — whenever the band is on). None = nothing to draw there."""
    lo, hi = t0 - SPAN_PAD, t1 + SPAN_PAD

    def on(a, b=None):
        return a is not None and a < hi and (b is None or b > lo)

    cta = lay.get("cta")
    band_on = lay.get("start") is not None and lay["start"] < hi and not (cta and cta["t0"] + 0.6 <= lo)
    intro = lay.get("intro") if lay.get("intro") and lay["intro"]["t1"] + 0.6 > lo else None
    cards = [c for c in lay["cards"] if on(c.get("t"), c.get("t1") + 0.5 if c.get("t1") is not None else None)]
    wins = [w for w in lay["windows"] if on(w["t"], w["t1"])]
    for w in wins:   # a full window shows the cards of its group that came before it
        cards += [c for c in lay["cards"] if c.get("group") == w["group"] and c.get("t") is not None and c["t"] <= w["t1"] and c not in cards]
    subs = [s for s in lay.get("subs") or [] if s[0] < hi and s[1] > lo]
    ctas = cta if cta and cta["t0"] < hi else None
    if not (band_on or intro or cards or wins or subs or ctas):
        return None
    return {"v": lay["v"], "title": lay["title"] if band_on else "", "start": lay["start"] if band_on else None,
            "subtitles": lay["subtitles"], "brand": lay["brand"],
            "angles": [a for a in lay["angles"] if a[0] < hi and a[1] > lo - 1.0],
            "months": lay["months"] if band_on else [], "marks": lay["marks"] if band_on else [],
            "cards": cards, "windows": wins, "intro": intro, "cta": ctas, "subs": subs}


def piece(project, doc: dict, lay: dict | None, s0: int, n: int, w: int, h: int, profile: str) -> dict | None:
    """The overlay piece for picture segment [s0, s0+n): a cached slice of the layer (rendered by
    render_block.py through gfx_live.py right before the segment, when missing)."""
    if not lay:
        return None
    fps = fps_of(doc)
    t0, t1 = s0 / fps, (s0 + n) / fps
    sl = slice_of(lay, t0, t1)
    if sl is None:
        return None
    job = {"layer": sl, "w": w, "h": h, "fps": fps, "rate": doc["rate"], "t0": round(t0, 6), "n": n, "codec": "qtrle"}
    key = hashlib.sha256(json.dumps(job, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:20]
    f = qdir(project) / "live" / profile / f"{key}.mov"
    job["out"] = str(f)
    return {"file": str(f), "image": False, "offset": 0.0, "skip": 0.0, "dur": round(n / fps, 4), "id": "live", "ident": key, "live": job}


def status(project, doc: dict) -> dict:
    lay, warn = layer(project, doc)
    spec = (doc.get("live") or {}).get("spec")
    if not spec:
        return {"on": False}
    return {"on": True, "spec": spec, "warnings": warn, "layer": {k: lay.get(k) for k in ("start", "months", "marks", "cards", "windows", "intro", "cta")} if lay else None,
            "preview": preview_file(project, doc)}


# ---------------------------------------------------------------- the live-monitor preview (whole film, small, VP9 alpha)


def preview_job(project, doc: dict) -> dict | None:
    """640×360 PNG stills at 4 per second (repeats dropped) + index.json: the live monitor swaps
    pictures by time — a second video decoding over the cameras froze playback."""
    lay, _ = layer(project, doc)
    if not lay:
        return None
    fps = 4.0
    n = max(1, int(round(max(lay["total"], (lay.get("cta") or {}).get("t1", 0)) * fps)) + 1)
    job = {"layer": lay, "w": 640, "h": 360, "fps": fps, "t0": 0.0, "n": n, "codec": "frames"}
    key = hashlib.sha256(json.dumps(job, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:16]
    job["out"] = str(qdir(project) / "live" / "preview" / key)
    return job


def preview_file(project, doc: dict) -> str | None:
    j = preview_job(project, doc)
    return j["out"] if j and (Path(j["out"]) / "index.json").exists() else None


def prune_previews(project, keep: str | None) -> None:
    import shutil
    d = qdir(project) / "live" / "preview"
    for x in d.glob("*") if d.exists() else []:
        if x.is_dir() and str(x) != keep and not x.name.endswith(".part"):
            shutil.rmtree(x, ignore_errors=True)
