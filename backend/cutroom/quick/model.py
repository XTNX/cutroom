"""Quick-mode document: blocks, camera roles, versions (undo/redo), angle coverage.

doc = {
  version, rate: "25/1",
  roles: {source_id: front|top|side|other|lav},
  presentation: {file, dir, pages: [{n, image, thumb, text}]} | None,
  grade: <whole-video grade> | None, music: {track, path, title} | None,
  tracks: {
    video:    [{id, start, len, src, in, color?}],               start/len in frames, in in source seconds
    audio:    [{id, start, len, src, in, style, link?}],         link = id of the video block it moves with
    slides:   [{id, start, len, page, layout: side|full}],
    overlays: [{id, start, len, slide?, hint, spec?, style, status, files?, error?}],
  },
  meta: {version_id, updated, strategy_hash}
}
Source files are never touched; everything derived lives in <edit>/quick/.
"""

from __future__ import annotations

import json
import time
import uuid
from fractions import Fraction
from pathlib import Path

TRACKS = ("video", "audio", "slides", "overlays")
ROLES = ("front", "top", "side", "other", "lav")
ROLE_NAMES = {"front": "фронт", "top": "верх", "side": "сбоку", "other": "другое", "lav": "петличка"}

SCHEMA = """
create table if not exists quick_versions (
  id integer primary key autoincrement, created real, author text, note text, data text
);
"""


def qdir(project) -> Path:
    d = project.edit / "quick"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:7]


def fps_of(doc: dict) -> float:
    return float(Fraction(doc.get("rate") or "25"))


def empty(rate: str = "25/1") -> dict:
    return {"version": 1, "rate": rate, "roles": {}, "presentation": None, "grade": None, "music": None,
            "tracks": {t: [] for t in TRACKS}, "meta": {}}


# ---------------------------------------------------------------- versions


def _schema(project) -> None:
    if getattr(project, "_quick_schema", False):
        return
    with project._db_lock:
        project.db.executescript(SCHEMA)
        project.db.commit()
    project._quick_schema = True


def head(project) -> int | None:
    v = project.stats().get("quick_head")
    return int(v) if v is not None else None


def current(project) -> dict | None:
    _schema(project)
    h = head(project)
    if h is None:
        return None
    row = project._q("select data from quick_versions where id = ?", (h,))
    if not row:
        return None
    doc = json.loads(row[0][0])
    doc.setdefault("meta", {})["version_id"] = h
    return doc


def save(project, doc: dict, author: str, note: str) -> int:
    _schema(project)
    doc = normalize(project, doc)
    doc.setdefault("meta", {})["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    h = head(project)
    with project._db_lock:
        if h is not None:   # a new edit after undo drops the redo branch
            project.db.execute("delete from quick_versions where id > ?", (h,))
        cur = project.db.execute("insert into quick_versions (created, author, note, data) values (?,?,?,?)",
                                 (time.time(), author, note, json.dumps(doc, ensure_ascii=False)))
        vid = cur.lastrowid
        project.db.commit()
    project.stat_set("quick_head", vid)
    doc["meta"]["version_id"] = vid
    (qdir(project) / "quick.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return vid


def patch_in_place(project, doc: dict) -> None:
    """Server-owned status (overlay drawn, error) without a new undo step."""
    vid = doc["meta"]["version_id"]
    project._q("update quick_versions set data = ? where id = ?", (json.dumps(doc, ensure_ascii=False), vid))
    (qdir(project) / "quick.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def history(project) -> dict:
    _schema(project)
    rows = project._q("select id, created, author, note from quick_versions order by id")
    h = head(project)
    ids = [r[0] for r in rows]
    return {"head": h, "can_undo": bool(h and ids and h > ids[0]), "can_redo": bool(h and ids and h < ids[-1]),
            "versions": [{"id": r[0], "created": r[1], "author": r[2], "note": r[3]} for r in rows[-40:]]}


def move_head(project, step: int) -> dict | None:
    _schema(project)
    ids = [r[0] for r in project._q("select id from quick_versions order by id")]
    h = head(project)
    if h not in ids:
        return None
    i = ids.index(h) + step
    if not 0 <= i < len(ids):
        return None
    project.stat_set("quick_head", ids[i])
    return current(project)


# ---------------------------------------------------------------- sources, roles, sync


def duration(project, sid: str) -> float:
    return float(((project.sources.get(sid) or {}).get("probe") or {}).get("duration") or 0)


def is_audio_only(project, sid: str) -> bool:
    pr = (project.sources.get(sid) or {}).get("probe") or {}
    return bool(pr) and not pr.get("video")


def offsets(project) -> dict[str, tuple[str, float]]:
    """source → (take id, eff): take time t_ref = t_source + eff. Unsynced sources are their own take."""
    from ..edl import _offsets
    eff = _offsets(project)
    for sid in project.sources:
        eff.setdefault(sid, (sid, 0.0))
    return eff


def role(doc: dict, project, sid: str) -> str:
    r = (doc.get("roles") or {}).get(sid)
    if r:
        return r
    return "lav" if is_audio_only(project, sid) else "other"


def covering(project, doc: dict, take: str, r0: float, r1: float, want: str) -> str | None:
    """A source of role `want` in take `take` whose footage covers take time [r0, r1]."""
    eff = offsets(project)
    best = None
    for sid, (g, e) in eff.items():
        s = project.sources.get(sid) or {}
        if g != take or s.get("duplicate_of") or s.get("excluded") or role(doc, project, sid) != want:
            continue
        a, b = e, e + duration(project, sid)
        if a <= r0 + 1e-3 and r1 <= b + 1e-3:
            # prefer the plain file over the iPhone "edited" twin (IMG_E…)
            if best is None or "_E" in Path(project.sources[best]["path"]).stem.upper():
                best = sid
    return best


def take_time(project, doc: dict, v: dict) -> tuple[str, float]:
    """(take, take time at the block start) of a video block — from its linked sound when it has one,
    so a cutaway (picture from an unsynced clip) still knows where in the take it sits."""
    eff = offsets(project)
    fps = fps_of(doc)
    a = next((x for x in doc["tracks"]["audio"] if x.get("link") == v["id"]), None)
    if a:
        g, e = eff[a["src"]]
        return g, a["in"] + e + (v["start"] - a["start"]) / fps
    g, e = eff[v["src"]]
    return g, v["in"] + e


def coverage(project, doc: dict, v: dict) -> dict[str, dict | None]:
    """For each picture role: the source (and its in-point) that can show this block, None = no synced footage.
    The block's own picture always counts for its role (a cutaway from an unsynced clip too)."""
    g, r0 = take_time(project, doc, v)
    r1 = r0 + v["len"] / fps_of(doc)
    eff = offsets(project)
    out: dict[str, dict | None] = {}
    for k in ("front", "top", "side"):
        sid = covering(project, doc, g, r0, r1, k)
        out[k] = {"src": sid, "in": round(r0 - eff[sid][1], 4)} if sid else None
    own = role(doc, project, v["src"])
    if own in out:
        out[own] = {"src": v["src"], "in": v["in"]}
    return out


def switch_angle(project, doc: dict, v: dict, want: str) -> str | None:
    """Point the block at a synced source of role `want`; returns an error text when there is none."""
    g, r0 = take_time(project, doc, v)
    r1 = r0 + v["len"] / fps_of(doc)
    sid = covering(project, doc, g, r0, r1, want)
    if sid is None:
        return f"{ROLE_NAMES.get(want, want)}: на {fmt(v['start'] / fps_of(doc))}–{fmt((v['start'] + v['len']) / fps_of(doc))} синхронного кадра нет"
    v["src"], v["in"] = sid, round(r0 - offsets(project)[sid][1], 4)
    return None


def fmt(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


# ---------------------------------------------------------------- normalisation


def normalize(project, doc: dict) -> dict:
    """Integer frames, sorted tracks, no overlaps on a track, media inside its source."""
    fps = fps_of(doc)
    tr = doc.setdefault("tracks", {})
    for t in TRACKS:
        blocks = []
        for b in tr.get(t) or []:
            b = dict(b)
            b["start"] = max(0, int(round(b.get("start", 0))))
            b["len"] = max(1, int(round(b.get("len", 1))))
            if t in ("video", "audio"):
                if b.get("src") not in project.sources:
                    continue
                b["in"] = max(0.0, float(b.get("in", 0)))
                room = duration(project, b["src"]) - b["in"]
                if room > 0:
                    b["len"] = max(1, min(b["len"], int(room * fps + 1e-6)))
            blocks.append(b)
        blocks.sort(key=lambda b: (b["start"], b["id"]))
        out: list[dict] = []
        for b in blocks:   # a later block never hides under an earlier one: it is trimmed to start after it
            if out and b["start"] < out[-1]["start"] + out[-1]["len"]:
                cut = out[-1]["start"] + out[-1]["len"] - b["start"]
                if cut >= b["len"]:
                    continue
                b["start"] += cut
                b["len"] -= cut
                if t in ("video", "audio"):
                    b["in"] = round(b["in"] + cut / fps, 4)
            out.append(b)
        tr[t] = out
    ids = {b["id"] for b in tr["video"]}
    for a in tr["audio"]:
        if a.get("link") and a["link"] not in ids:
            a.pop("link")
    slides = {s["id"] for s in tr["slides"]}
    for o in tr["overlays"]:
        if o.get("slide") and o["slide"] not in slides:
            o["slide_gone"] = True
    return doc


def total_frames(doc: dict) -> int:
    return max([b["start"] + b["len"] for t in TRACKS for b in doc["tracks"].get(t, [])] or [0])
