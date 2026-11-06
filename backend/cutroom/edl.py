"""Strategy gate, EDL versions, word-boundary snapping, render resolution.

Hard Rule 11: nothing that cuts or renders runs unless edit/strategy.json is "approved" AND
the approved text hash still matches the current text (editing the text re-closes the gate).
Hard Rules 6/7: every range edge is snapped to a word boundary of its source transcript and
padded (30–200 ms, default from settings), never into the neighbouring word.
edl.json (video-use format + Cutroom extensions) is the source of truth; every change is a
new row in edl_versions (undo/redo = moving the head pointer).
"""

from __future__ import annotations

import hashlib
import json
import time
from bisect import bisect_left
from pathlib import Path

from .config import load_settings

PAD_MIN, PAD_MAX = 0.03, 0.2

SCHEMA = """
create table if not exists edl_versions (
  id integer primary key autoincrement, created real, author text, note text, data text
);
"""


# ---------------------------------------------------------------- strategy gate


def strategy_path(project) -> Path:
    return project.edit / "strategy.json"


def load_strategy(project) -> dict | None:
    p = strategy_path(project)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_strategy(project, data: dict) -> dict:
    strategy_path(project).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def strategy_approved(project) -> bool:
    s = load_strategy(project)
    return bool(s and s.get("status") == "approved" and s.get("approved_hash") == text_hash(s.get("text", "")))


class GateClosed(Exception):
    pass


def require_gate(project) -> dict:
    if not strategy_approved(project):
        raise GateClosed("Стратегия не утверждена: резать и рендерить нельзя до «Утвердить стратегию» (Hard Rule 11).")
    return load_strategy(project)


# ---------------------------------------------------------------- transcripts


class Words:
    """Word starts/ends of one source, for snapping."""

    def __init__(self, path: Path):
        d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"words": []}
        ws = [w for w in d.get("words", []) if w.get("type") in ("word", "audio_event")]
        self.words = ws
        self.starts = [w["start"] for w in ws]
        self.ends = [w["end"] for w in ws]

    def snap_start(self, t: float, pad: float) -> float:
        if not self.words:
            return max(0.0, t)
        i = self._nearest(self.starts, t)
        s = self.starts[i]
        prev_end = self.ends[i - 1] if i > 0 else 0.0
        # pad, but never back into the previous word: at most half the gap
        room = max(0.0, s - prev_end)
        return round(max(0.0, s - min(pad, room / 2 if room < 2 * pad else pad)), 3)

    def snap_end(self, t: float, pad: float, limit: float) -> float:
        if not self.words:
            return min(limit, t)
        i = self._nearest(self.ends, t)
        e = self.ends[i]
        next_start = self.starts[i + 1] if i + 1 < len(self.starts) else limit
        room = max(0.0, next_start - e)
        return round(min(limit, e + min(pad, room / 2 if room < 2 * pad else pad)), 3)

    @staticmethod
    def _nearest(arr: list[float], t: float) -> int:
        i = bisect_left(arr, t)
        if i <= 0:
            return 0
        if i >= len(arr):
            return len(arr) - 1
        return i if abs(arr[i] - t) < abs(arr[i - 1] - t) else i - 1

    def snap_split(self, t: float, pad: float) -> tuple[float, float]:
        """A cut inside one take removes nothing: both halves meet at one point inside the
        inter-word gap nearest to t (t itself if it already sits in a gap, else the gap middle),
        so no audio is repeated or dropped and no word is split. Returns (end_left, start_right)."""
        if len(self.words) < 2:
            return t, t
        best, bd = 0, float("inf")
        for k in range(len(self.words) - 1):
            e, s = self.ends[k], self.starts[k + 1]
            d = 0.0 if e <= t <= s else min(abs(t - e), abs(t - s))
            if d < bd:
                best, bd = k, d
        e, s = self.ends[best], self.starts[best + 1]
        point = t if e <= t <= s else (e + s) / 2
        return round(point, 3), round(point, 3)

    def text_between(self, a: float, b: float) -> str:
        return " ".join(w["text"] for w in self.words if w["start"] >= a - 0.01 and w["end"] <= b + 0.01)


# ---------------------------------------------------------------- EDL build / versions


def ensure_schema(project) -> None:
    # once per project and under the lock: executescript issues its own COMMIT, and racing a write
    # from another job thread on the shared connection fails with "no transaction is active"
    if getattr(project, "_edl_schema", False):
        return
    with project._db_lock:
        project.db.executescript(SCHEMA)
        project.db.commit()
    project._edl_schema = True


def pad_seconds() -> float:
    return max(PAD_MIN, min(PAD_MAX, load_settings().get("cut_padding_ms", 100) / 1000))


def normalize(project, ranges: list[dict], slots: list[dict], snap: bool = True) -> dict:
    """Validate + snap ranges, place slots on the output timeline, build video-use edl.json."""
    pad = pad_seconds()
    words: dict[str, Words] = {}
    out_ranges = []
    offset = 0.0
    # consecutive ranges of one source meeting at one point = a split: snap them to one gap
    split_at: dict[int, tuple[float, float]] = {}
    for i in range(len(ranges) - 1):
        a, b = ranges[i], ranges[i + 1]
        if snap and a["source"] == b["source"] and abs(float(a["end"]) - float(b["start"])) < 0.05:
            w = words.setdefault(a["source"], Words(project.paths(a["source"])["transcript"]))
            split_at[i] = w.snap_split(float(a["end"]), pad)
    for i, r in enumerate(ranges):
        sid = r["source"]
        if sid not in project.sources:
            continue
        dur = (project.sources[sid]["probe"] or {}).get("duration", 0)
        w = words.setdefault(sid, Words(project.paths(sid)["transcript"]))
        start, end = float(r["start"]), float(r["end"])
        if snap and not r.get("locked"):
            start = split_at[i - 1][1] if i - 1 in split_at else w.snap_start(start, pad)
            end = split_at[i][0] if i in split_at else w.snap_end(end, pad, dur)
        start, end = max(0.0, start), min(dur, end)
        if end - start < 0.2:
            continue
        rr = {k: v for k, v in r.items() if k not in ("start", "end", "out_start")}
        if not rr.get("angle") or rr.get("angle") == sid:
            rr.pop("angle", None)
        rr.update({"source": sid, "start": start, "end": end, "out_start": round(offset, 3)})
        # the quote always reflects what the range really contains after snapping/splitting
        rr["quote"] = w.text_between(start, end)[:200] or rr.get("quote", "")
        out_ranges.append(rr)
        offset += end - start
    out_slots = []
    for k, s in enumerate(slots):
        s = dict(s)
        s.setdefault("id", f"s{k + 1}")
        if isinstance(s.get("spec"), str):   # editor's one-line idea → hint for the slot agent; spec becomes the card
            s["spec_hint"] = s.pop("spec") or s.get("spec_hint", "")
        if "start_in_output" not in s:
            # payoff word lands at the end of the reveal: start ≈ 0.8 s earlier
            t = None
            for r in out_ranges:
                if r["source"] == s.get("source") and r["start"] <= s.get("payoff_time", -1) <= r["end"]:
                    t = r["out_start"] + s["payoff_time"] - r["start"]
                    break
            if t is None:
                continue
            s["start_in_output"] = round(max(0.0, t - 0.8), 3)
        s.setdefault("duration", 5.0)
        s.setdefault("status", "placeholder")
        out_slots.append(s)
    return {
        "version": 1,
        "sources": {sid: project.sources[sid]["path"] for sid in sorted({r["source"] for r in out_ranges})},
        "ranges": out_ranges,
        "grade": "none",
        "overlays": [],
        "total_duration_s": round(offset, 3),
        "slots": out_slots,
        "cutroom": {"pad_s": pad, "updated": time.strftime("%Y-%m-%d %H:%M:%S")},
    }


CARRY = ("audio", "music", "color", "brand")


def carry_over(new: dict, old: dict | None) -> dict:
    """Settings that live beside the cut (speech mic per take, clean-up, music bed) survive edits."""
    for k in CARRY:
        if old and k in old:
            new[k] = old[k]
    return new


def head(project) -> int | None:
    v = project.stats().get("edl_head")
    return int(v) if v is not None else None


def save_version(project, edl: dict, author: str, note: str) -> int:
    ensure_schema(project)
    h = head(project)
    with project._db_lock:
        if h is not None:  # a new edit after undo drops the redo branch
            project.db.execute("delete from edl_versions where id > ?", (h,))
        cur = project.db.execute("insert into edl_versions (created, author, note, data) values (?,?,?,?)",
                                 (time.time(), author, note, json.dumps(edl, ensure_ascii=False)))
        vid = cur.lastrowid
        project.db.commit()
    project.stat_set("edl_head", vid)
    edl["cutroom"]["version_id"] = vid
    write_edl(project, edl)
    return vid


def write_edl(project, edl: dict) -> None:
    f = project.edit / "edl.json"
    if f.exists():
        # an edl.json written before Cutroom (by hand or another tool) is backed up once, never lost
        try:
            foreign = "cutroom" not in json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            foreign = True
        keep = project.edit / "edl.before-cutroom.json"
        if foreign and not keep.exists():
            f.replace(keep)
    f.write_text(json.dumps(edl, ensure_ascii=False, indent=1), encoding="utf-8")


def current(project) -> dict | None:
    ensure_schema(project)
    h = head(project)
    if h is None:
        return None
    row = project._q("select data from edl_versions where id = ?", (h,))
    if not row:
        return None
    edl = json.loads(row[0][0])
    edl["cutroom"]["version_id"] = h
    return edl


def history(project) -> dict:
    ensure_schema(project)
    rows = project._q("select id, created, author, note from edl_versions order by id")
    h = head(project)
    ids = [r[0] for r in rows]
    return {"head": h, "can_undo": bool(h and ids and h > ids[0]), "can_redo": bool(h and ids and h < ids[-1]),
            "versions": [{"id": r[0], "created": r[1], "author": r[2], "note": r[3]} for r in rows[-50:]]}


def move_head(project, step: int) -> dict | None:
    ensure_schema(project)
    ids = [r[0] for r in project._q("select id from edl_versions order by id")]
    h = head(project)
    if h not in ids:
        return None
    i = ids.index(h) + step
    if not 0 <= i < len(ids):
        return None
    project.stat_set("edl_head", ids[i])
    edl = current(project)
    write_edl(project, edl)
    return edl


# ---------------------------------------------------------------- render resolution


def _offsets(project) -> dict[str, tuple[str, float]]:
    from .sync import load_sync
    eff: dict[str, tuple[str, float]] = {}
    for g in load_sync(project).get("groups", []):
        for m in g["members"]:
            fps = ((project.sources.get(m["id"], {}).get("probe") or {}).get("video") or {}).get("fps") or 25
            eff[m["id"]] = (g["id"], m["offset"] + m.get("nudge_frames", 0) / fps)
    return eff


def resolve_ranges(project, edl: dict) -> list[dict]:
    """Per range: picture from the chosen angle, sound from the take's speech mic.
    All times map through the sync offsets (t_ref = t_member + eff)."""
    from .audio import effective_sources
    from .color import build_filter
    eff = _offsets(project)
    mics = effective_sources(project, edl)
    out = []
    for r in edl["ranges"]:
        src, a, b = r["source"], r["start"], r["end"]
        vsrc, va = src, a
        ang = r.get("angle")
        if ang and ang != src and ang in eff and src in eff and eff[ang][0] == eff[src][0]:
            vsrc, va = ang, a + eff[src][1] - eff[ang][1]
        item = {"source": vsrc, "start": round(va, 3), "end": round(va + (b - a), 3), "out_start": r.get("out_start", 0),
                "beat": r.get("beat", ""), "quote": r.get("quote", ""), "reason": r.get("reason", "")}
        # colour: the episode's own grade wins over the whole-cut grade; applied per segment at extract
        item["grade_filter"] = build_filter(r.get("color") or edl.get("color"))
        mic = mics.get(eff[src][0]) if src in eff else None
        if mic and mic != vsrc and mic in eff:
            item["audio"] = {"source": mic, "start": round(a + eff[src][1] - eff[mic][1], 4)}
        out.append(item)
    return out


def resolve_for_render(project, edl: dict) -> Path:
    """Plain render EDL for backend/helpers/render_cutroom.py (video-use format + per-range
    "audio" + "audio_fx" + "music"). Written next to edl.json because the video-use render
    functions work in the EDL's directory; ASCII-escaped for locale-encoding readers."""
    from .audio import fx_chain
    ranges = resolve_ranges(project, edl)
    ids = {r["source"] for r in ranges} | {r["audio"]["source"] for r in ranges if r.get("audio")}
    out = {"version": 1, "sources": {s: project.sources[s]["path"] for s in sorted(ids)},
           "ranges": ranges, "grade": edl.get("grade", "none"), "overlays": edl.get("overlays", []),
           "total_duration_s": edl.get("total_duration_s"),
           "audio_fx": fx_chain((edl.get("audio") or {}).get("fx")), "music": edl.get("music")}
    from .gfx import overlays
    out["overlays"] = (edl.get("overlays") or []) + overlays(edl)   # ready infographic slots
    p = project.edit / "edl_render.json"
    p.write_text(json.dumps(out, ensure_ascii=True, indent=1), encoding="utf-8")
    return p
