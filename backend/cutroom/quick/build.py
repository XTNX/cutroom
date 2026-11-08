"""Incremental build: per-block render cache + stream-copy assembly.

Picture: the video track is cut into segments (each block, each gap). A segment's file name is
the hash of everything it shows — source file content, in-point, length, project rate, grade,
the slide/card pieces burned into it, the output profile and the renderer version. An edit
changes the hash of only the segments it touches; the rest are reused as they are.

Sound: each audio block becomes a cached PCM piece (its own speech style, 30 ms fades only where
the sound really jumps — video-use Rule 3), the pieces are summed into one voice track at their
exact sample positions, the music bed is mixed under the blocks that ask for it (ducked under
speech) and loudness is normalised on the sound alone (two-pass, −14 LUFS / −1 dBTP). None of
that re-encodes the picture.

Assembly: `-c copy` concat of the cached segments (Rule 2) + the mixed sound → mp4. The log
(edit/quick/logs/build.log) lists which segments were rebuilt and which came from the cache.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import wave
from pathlib import Path

from ..color import build_filter
from ..config import CUTROOM_HELPERS, PYTHON, subprocess_env
from ..jobs import Job
from . import model
from .model import fps_of, qdir

RENDER_VERSION = 3
SR = 48000
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROFILES = {"draft": {"w": 1280, "h": 720, "quality": "draft"}, "final": {"w": 1920, "h": 1080, "quality": "final"}}

# speech styles: the clean-up chain (cutroom.audio) + an optional music bed level under this block
STYLES = {
    "clean": {"label": "Чистая речь", "fx": {"highpass": True, "denoise": "medium", "deess": True, "compress": "speech"}, "bed": None},
    "light": {"label": "Речь и лёгкая подложка", "fx": {"highpass": True, "denoise": "medium", "deess": True, "compress": "speech"}, "bed": -28.0},
    "bed": {"label": "Речь на музыке", "fx": {"highpass": True, "denoise": "medium", "deess": True, "compress": "speech"}, "bed": -20.0},
    "natural": {"label": "Как записано", "fx": {"highpass": True, "denoise": "off", "deess": False, "compress": "soft"}, "bed": None},
}
DEFAULT_STYLE = "clean"


def ff(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, creationflags=NOWIN)
    if p.returncode != 0:
        tail = [x for x in p.stderr.decode("utf-8", "replace").splitlines() if x.strip()][-3:]
        raise RuntimeError(f"ffmpeg ({what}): " + " | ".join(tail))
    return p


def encoder() -> str:
    from ..system import proxy_encoder
    return proxy_encoder("auto")


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:20]


# ---------------------------------------------------------------- picture segments


def overlay_pieces(project, doc: dict, s0: int, n: int) -> list[dict]:
    """Slides (below) and ready infographic cards (on top) that intersect frames [s0, s0+n)."""
    from ..brand import load as load_brand
    from .slides import compose
    fps = fps_of(doc)
    out = []
    colors = load_brand()["colors"]
    for kind in ("slides", "overlays"):
        for o in doc["tracks"][kind]:
            a, b = max(s0, o["start"]), min(s0 + n, o["start"] + o["len"])
            if b <= a:
                continue
            if kind == "slides":
                f = compose(doc, o["page"], o.get("layout", "side"), colors)
                if not f:
                    continue
                ident, image = f"{f.name}", True
            else:
                f = Path(((o.get("files") or {}).get("render")) or "")
                if o.get("status") != "ready" or not f.is_file():
                    continue
                ident, image = f"{o['files'].get('v')}", False
            out.append({"file": str(f), "image": image, "offset": round((a - s0) / fps, 4), "skip": round((a - o["start"]) / fps, 4),
                        "dur": round((b - a) / fps, 4), "id": o["id"], "ident": ident})
    return out


def segments(project, doc: dict, profile: str) -> list[dict]:
    fps = fps_of(doc)
    vids = doc["tracks"]["video"]
    end = max([b["start"] + b["len"] for t in ("video", "audio") for b in doc["tracks"][t]] or [0])
    enc = encoder()
    prof = PROFILES[profile]
    out, cur = [], 0

    def add(s0: int, n: int, v: dict | None):
        pieces = overlay_pieces(project, doc, s0, n)
        src = project.sources.get(v["src"]) if v else None
        hdr = bool((((src or {}).get("probe") or {}).get("video") or {}).get("hdr"))
        grade = build_filter((v.get("color") if v and v.get("color") is not None else doc.get("grade"))) if v else ""
        key = {"rv": RENDER_VERSION, "p": profile, "enc": enc, "rate": doc["rate"], "n": n,
               "src": (src or {}).get("hash"), "in": round(v["in"], 4) if v else None, "hdr": hdr, "grade": grade,
               "ov": [(p["ident"], p["offset"], p["skip"], p["dur"]) for p in pieces]}
        h = _h(key)
        f = qdir(project) / "cache" / profile / f"{h}.mp4"
        out.append({"hash": h, "file": f, "start": s0, "frames": n, "block": v["id"] if v else None, "src": v["src"] if v else None,
                    "job": {"out": str(f), "frames": n, "rate": doc["rate"], **prof, "encoder": enc,
                            "src": (src or {}).get("path"), "in": v["in"] if v else 0, "hdr": hdr, "grade": grade,
                            "overlays": [{k: p[k] for k in ("file", "image", "offset", "skip", "dur")} for p in pieces]}})

    for v in vids:
        if v["start"] > cur:
            add(cur, v["start"] - cur, None)
        add(v["start"], v["len"], v)
        cur = v["start"] + v["len"]
    if end > cur:
        add(cur, end - cur, None)
    return out


def block_cmd(seg: dict) -> list[str]:
    jf = seg["file"].with_suffix(".json")
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps(seg["job"], ensure_ascii=False), encoding="utf-8")
    return [str(PYTHON), str(CUTROOM_HELPERS / "render_block.py"), str(jf)]


# ---------------------------------------------------------------- sound pieces


_latency: dict[str, float] = {}


def _continuous(p: dict | None, a: dict, fps: float) -> bool:
    return bool(p) and bool(a) and p["src"] == a["src"] and p["start"] + p["len"] == a["start"] and abs(p["in"] + p["len"] / fps - a["in"]) < 0.01


def audio_piece(project, doc: dict, a: dict, prev: dict | None, nxt: dict | None) -> tuple[Path, bool]:
    """(file, rebuilt) — one audio block as 48 kHz stereo PCM, exactly len frames long."""
    from ..audio import fx_chain
    fps = fps_of(doc)
    st = STYLES.get(a.get("style") or DEFAULT_STYLE, STYLES[DEFAULT_STYLE])
    chain = fx_chain(st["fx"])
    fin, fout = not _continuous(prev, a, fps), not _continuous(a, nxt, fps)
    src = project.sources[a["src"]]
    dur = a["len"] / fps
    key = {"rv": RENDER_VERSION, "src": src["hash"], "in": round(a["in"], 4), "n": a["len"], "rate": doc["rate"],
           "chain": chain, "fin": fin, "fout": fout, "track": src.get("audio_track", 0)}
    f = qdir(project) / "cache" / "audio" / f"{_h(key)}.wav"
    if f.exists():
        return f, False
    f.parent.mkdir(parents=True, exist_ok=True)
    # pre-roll: the denoiser settles before the block starts and its delay (afftdn ≈ 25 ms) is cut off
    pre = min(0.5, a["in"])
    lat = _chain_latency(chain, src["path"], a["in"]) if chain else 0.0
    af = [chain] if chain else []
    af += [f"atrim=start={pre + lat:.4f}", "asetpts=PTS-STARTPTS"]
    if fin:
        af.append("afade=t=in:st=0:d=0.03")
    if fout:
        af.append(f"afade=t=out:st={max(0.0, dur - 0.03):.4f}:d=0.03")
    af += ["apad", f"atrim=end_sample={round(dur * SR)}"]
    tmp = f.with_suffix(".part.wav")
    ff(["ffmpeg", "-y", "-v", "error", "-ss", f"{a['in'] - pre:.4f}", "-t", f"{dur + pre + lat + 0.3:.4f}", "-i", src["path"],
        "-map", f"0:a:{src.get('audio_track', 0)}", "-vn", "-ac", "2", "-ar", str(SR), "-af", ",".join(af), "-c:a", "pcm_s16le", str(tmp)],
       f"звук блока {a['id']}")
    tmp.replace(f)
    return f, True


def _chain_latency(chain: str, path: str, t: float) -> float:
    """Measured once per chain (render_cutroom.chain_latency on 8 s of this source)."""
    if chain in _latency:
        return _latency[chain]
    import sys
    sys.path.insert(0, str(CUTROOM_HELPERS))
    from render_cutroom import chain_latency
    tmp = Path(__import__("tempfile").gettempdir()) / f"cutroom_lat_{abs(hash(path)) % 10**8}.wav"
    try:
        ff(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-t", "8", "-i", path, "-vn", "-ac", "1", "-ar", "48000", str(tmp)], "замер задержки")
        _latency[chain] = chain_latency(tmp, chain)
    except Exception:
        _latency[chain] = 0.0
    finally:
        tmp.unlink(missing_ok=True)
    return _latency[chain]


def pieces(project, doc: dict) -> tuple[list[tuple[Path, int]], list[str], list[str]]:
    """Every audio block's cached piece and its sample position (building the missing ones)."""
    fps = fps_of(doc)
    au = doc["tracks"]["audio"]
    out, rebuilt, cached = [], [], []
    for i, a in enumerate(au):
        f, new = audio_piece(project, doc, a, au[i - 1] if i else None, au[i + 1] if i + 1 < len(au) else None)
        (rebuilt if new else cached).append(a["id"])
        out.append((f, round(a["start"] / fps * SR)))
    return out, rebuilt, cached


def mix_voice(placed: list[tuple[Path, int]], n: int, out: Path) -> None:
    """Sum the cached pieces at their exact sample positions (numpy) → one voice track."""
    import numpy as np
    buf = np.zeros((n, 2), np.int32)
    for f, pos in placed:
        with wave.open(str(f), "rb") as w:
            data = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2)
        m = max(0, min(len(data), n - pos))
        buf[pos:pos + m] += data[:m]
    _write(out, np.clip(buf, -32768, 32767).astype(np.int16))


def _write(path: Path, pcm) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def quick_level(src: Path, out: Path, target_db: float = -17.0, ceiling_db: float = -1.0) -> None:
    """Draft loudness in well under a second: gated speech RMS → gain to ≈ −14 LUFS for speech,
    soft ceiling at −1 dBFS. The export uses the real two-pass loudnorm instead."""
    import numpy as np
    with wave.open(str(src), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2).astype(np.float32) / 32768
    blk = SR // 2
    m = x[: len(x) // blk * blk].reshape(-1, blk, 2)
    rms = np.sqrt((m ** 2).mean(axis=(1, 2)) + 1e-12)
    speech = rms[rms > 10 ** (-50 / 20)]
    if len(speech):
        level = 20 * np.log10(np.sqrt((speech ** 2).mean()))
        x = x * 10 ** ((target_db - level) / 20)
    c = 10 ** (ceiling_db / 20)
    over = np.abs(x) > c * 0.8
    x[over] = np.sign(x[over]) * (c * 0.8 + (c * 0.2) * np.tanh((np.abs(x[over]) - c * 0.8) / (c * 0.2)))
    _write(out, (np.clip(x, -1, 1) * 32767).astype(np.int16))


def bed_regions(doc: dict) -> list[tuple[float, float, float]]:
    fps = fps_of(doc)
    out = []
    for a in doc["tracks"]["audio"]:
        lvl = STYLES.get(a.get("style") or DEFAULT_STYLE, STYLES[DEFAULT_STYLE])["bed"]
        if lvl is not None:
            t0, t1 = a["start"] / fps, (a["start"] + a["len"]) / fps
            if out and abs(out[-1][1] - t0) < 1e-3 and out[-1][2] == lvl:
                out[-1] = (out[-1][0], t1, lvl)
            else:
                out.append((t0, t1, lvl))
    return out


def master(voice: Path, doc: dict, out: Path, total_s: float, profile: str = "final") -> None:
    """Music bed under the blocks that ask for it (ducked under speech) + loudness, sound only
    (two-pass loudnorm for the export, the fast level for the draft preview)."""
    music = doc.get("music") or {}
    regions = bed_regions(doc)
    pre = voice
    if regions and music.get("path") and Path(music["path"]).exists():
        # gain envelope: each region ramps in/out over 0.6 s
        env = "+".join(f"{10 ** (lvl / 20):.5f}*clip(min((t-{a:.3f})/0.6\\,({b:.3f}-t)/0.6)\\,0\\,1)" for a, b, lvl in regions)
        pre = out.with_name("mix.wav")
        ff(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-stream_loop", "-1", "-i", music["path"], "-filter_complex",
            f"[1:a]aresample={SR},aformat=channel_layouts=stereo,volume='{env}':eval=frame,atrim=end={total_s:.4f}[m0];"
            "[0:a]asplit=2[v][sc];[m0][sc]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=600[md];"
            "[v][md]amix=inputs=2:duration=first:normalize=0[mix]",
            "-map", "[mix]", "-c:a", "pcm_s16le", "-ar", str(SR), str(pre)], "музыкальная подложка")
    if profile == "draft":
        quick_level(pre, out)
        return
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(pre), "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, creationflags=NOWIN)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr.decode("utf-8", "replace"))
    if m:
        d = json.loads(m.group(0))
        if d.get("input_i") in ("-inf", None) or float(d["input_i"]) < -70:   # silence: nothing to normalise
            ln = "anull"
        else:
            ln = (f"loudnorm=I=-14:TP=-1:LRA=11:measured_I={d['input_i']}:measured_TP={d['input_tp']}:measured_LRA={d['input_lra']}"
                  f":measured_thresh={d['input_thresh']}:offset={d['target_offset']}:linear=true")
    else:
        ln = "loudnorm=I=-14:TP=-1:LRA=11"
    ff(["ffmpeg", "-y", "-v", "error", "-i", str(pre), "-af", f"{ln},aresample={SR},atrim=end_sample={round(total_s * SR)}",
        "-c:a", "pcm_s16le", str(out)], "громкость")


# ---------------------------------------------------------------- scheduling and assembly


_active_hash: dict[str, int] = {}   # segment hash → job id (one render per segment at a time)


def missing(segs: list[dict]) -> list[dict]:
    return [s for s in segs if not s["file"].exists()]


def schedule(project, queue, profile: str = "draft", delay: float = 1.2, on_done=None) -> Job:
    """Debounced: a burst of edits makes one plan; the plan enqueues only the segments that are missing."""
    for j in queue.active():
        if j.kind == "qplan" and j.status == "queued" and j.source == profile:
            j.not_before = time.time() + delay
            return j
    return queue.add(Job(kind="qplan", label="Быстрый режим: что пересобрать", lane="io", priority=5, source=profile,
                         not_before=time.time() + delay, fn=lambda job: _plan(project, queue, job, profile, on_done)))


def _plan(project, queue, job: Job, profile: str, on_done) -> None:
    doc = model.current(project)
    if not doc or not doc["tracks"]["video"]:
        job.note = "нечего собирать"
        return
    if project.missing_sources({b["src"] for t in ("video", "audio") for b in doc["tracks"][t]}):
        project.relink()
    segs = segments(project, doc, profile)
    need = missing(segs)
    deps = []
    for s in need:
        jid = _active_hash.get(s["hash"])
        live = next((j for j in queue.active() if j.id == jid), None) if jid else None
        if live is None:
            name = s["src"] or "пауза"
            live = queue.add(Job(kind="qblock", label=f"Блок {s['block'] or 'пауза'} ({name}): рендер {s['frames']} кадр.", lane="blocks",
                                 priority=10 if profile == "draft" else 20, source=s["block"], cmd=block_cmd(s),
                                 expected=max(1.5, s["frames"] / fps_of(doc) * 0.4)))
            _active_hash[s["hash"]] = live.id
        deps.append(live.id)
    job.note = f"пересобрать {len(need)} из {len(segs)}"
    vid = doc["meta"]["version_id"]
    rebuilt = [s["block"] or f"пауза@{s['start']}" for s in need]
    queue.add(Job(kind="qassemble", label=("Сборка превью" if profile == "draft" else "Экспорт") + f" v{vid}: склейка из кэша",
                  lane="assemble", priority=4 if profile == "draft" else 6, deps=deps, source=profile,
                  fn=lambda j: assemble(project, j, profile, vid, rebuilt, on_done)))


def assemble(project, job: Job, profile: str, vid: int, rebuilt: list[str], on_done=None) -> dict | None:
    doc = model.current(project)
    if not doc or doc["meta"]["version_id"] != vid:
        job.note = "устарело: собирается более новая версия"
        return None
    t0 = time.time()
    fps = fps_of(doc)
    segs = segments(project, doc, profile)
    gone = missing(segs)
    if gone:
        raise RuntimeError(f"не готовы блоки: {', '.join(str(s['block'] or 'пауза') for s in gone[:5])}")
    total = sum(s["frames"] for s in segs)
    work = qdir(project) / "work"
    work.mkdir(exist_ok=True)
    lst = work / f"concat_{profile}.txt"
    lst.write_text("".join(f"file '{s['file'].as_posix()}'\n" for s in segs), encoding="utf-8")
    video = work / f"video_{profile}.mp4"
    ff(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video)], "склейка")
    t_concat = time.time() - t0
    job.progress = 0.4
    placed, a_rebuilt, a_cached = pieces(project, doc)
    a_stats = {"rebuilt": a_rebuilt, "cached": a_cached}
    music = doc.get("music") or {}
    skey = _h({"rv": RENDER_VERSION, "p": profile, "n": total, "rate": doc["rate"], "pieces": [(f.name, pos) for f, pos in placed],
               "music": music.get("path"), "bed": bed_regions(doc)})
    final_wav = work / f"sound_{profile}_{skey}.wav"
    sound_cached = final_wav.exists()
    if not sound_cached:   # the mixed sound only changes when a piece, its place or the music changes
        for old in work.glob(f"sound_{profile}_*.wav"):
            old.unlink(missing_ok=True)
        voice = work / f"voice_{profile}.wav"
        mix_voice(placed, round(total / fps * SR), voice)
        job.progress = 0.7
        master(voice, doc, final_wav, total / fps, profile)
    aac = final_wav.with_suffix(".m4a")   # the one and only AAC encode, cached with the sound
    if not aac.exists():
        for old in work.glob(f"sound_{profile}_*.m4a"):
            old.unlink(missing_ok=True)
        ff(["ffmpeg", "-y", "-v", "error", "-i", str(final_wav), "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), str(aac)], "AAC")
    t_audio = time.time() - t0 - t_concat
    if profile == "draft":
        out = qdir(project) / f"preview_v{vid}.mp4"
    else:
        exp = project.edit / "export"
        exp.mkdir(exist_ok=True)
        out = exp / f"{project.dir.name}_быстрый_v{vid}.mp4"
    ff(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(aac), "-map", "0:v", "-map", "1:a", "-c", "copy",
        "-t", f"{total / fps:.4f}", "-movflags", "+faststart", str(out)], "сведение")
    t_all = time.time() - t0
    blocks = {s["block"] or f"пауза@{s['start']}" for s in segs}
    stats = {"profile": profile, "version": vid, "file": str(out), "segments": len(segs), "duration": round(total / fps, 3),
             "rebuilt": rebuilt, "cached": sorted(blocks - set(rebuilt)), "audio_rebuilt": a_stats["rebuilt"],
             "audio_cached": len(a_stats["cached"]), "t_concat": round(t_concat, 2), "t_audio": round(t_audio, 2), "t_total": round(t_all, 2),
             "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    log = qdir(project) / "logs"
    log.mkdir(exist_ok=True)
    with (log / "build.log").open("a", encoding="utf-8") as fh:
        fh.write(f"[{stats['when']}] {'превью' if profile == 'draft' else 'ЭКСПОРТ'} v{vid}: {len(segs)} сегм., {stats['duration']} с\n"
                 f"  ПЕРЕСОБРАНЫ ({len(rebuilt)}): {', '.join(rebuilt) or '—'}\n"
                 f"  ИЗ КЭША ({len(stats['cached'])}): {', '.join(stats['cached']) or '—'}\n"
                 f"  звук: пересобрано {len(a_stats['rebuilt'])} кусков, из кэша {len(a_stats['cached'])}"
                 f"{'; дорожка целиком из кэша' if sound_cached else ''}\n"
                 f"  склейка {t_concat:.2f} с · звук {t_audio:.2f} с · всего после рендера блоков {t_all:.2f} с\n")
    (log / f"last_{profile}.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    if profile == "draft":   # older previews: best effort (the monitor may still hold one open)
        for old in qdir(project).glob("preview_v*.mp4"):
            if old != out:
                try:
                    old.unlink()
                except OSError:
                    pass
    job.note = f"{len(rebuilt)} пересобрано, {len(stats['cached'])} из кэша · {t_all:.1f} с"
    if on_done:
        on_done(stats)
    return stats


def prerender(project, queue, profile: str = "final") -> int:
    """Background pre-render: enqueue the missing segments of `profile` (no assembly, lowest priority).
    After a draft is built, the 1080p blocks follow, so «Экспорт» and the emergency export only glue."""
    doc = model.current(project)
    if not doc or not doc["tracks"]["video"]:
        return 0
    n = 0
    for s in missing(segments(project, doc, profile)):
        jid = _active_hash.get(s["hash"])
        if jid and any(j.id == jid for j in queue.active()):
            continue
        j = queue.add(Job(kind="qprerender", label=f"Пререндер {profile} · блок {s['block'] or 'пауза'}", lane="prerender", priority=35,
                          source=s["block"], cmd=block_cmd(s), expected=max(1.5, s["frames"] / fps_of(doc) * 0.6)))
        _active_hash[s["hash"]] = j.id
        n += 1
    return n


def newest_preview(project) -> str | None:
    fs = sorted(qdir(project).glob("preview_v*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
    return str(fs[0]) if fs else None


def run_sync(project, profile: str = "final", log=print, workers: int = 2) -> dict:
    """The whole build without the job queue and without any agent: render the missing segments
    (render_block.py, `workers` at a time), then assemble. Used by the CLI and the emergency export."""
    from concurrent.futures import ThreadPoolExecutor
    doc = model.current(project)
    if not doc or not doc["tracks"]["video"]:
        raise RuntimeError("В быстром режиме нет монтажа — собирать нечего.")
    if project.missing_sources({b["src"] for t in ("video", "audio") for b in doc["tracks"][t]}):
        project.relink()
    segs = segments(project, doc, profile)
    need = missing(segs)
    log(f"сегментов {len(segs)}, рендерить {len(need)}, из кэша {len(segs) - len(need)}")

    def one(s: dict) -> str:
        p = subprocess.run(block_cmd(s), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=subprocess_env(), creationflags=NOWIN)
        if not s["file"].exists():   # (a background pre-render of the same segment may have won the race)
            tail = [x for x in (p.stdout + p.stderr).splitlines() if x.strip()][-2:]
            raise RuntimeError(f"блок {s['block'] or 'пауза'}: {' | '.join(tail)}")
        return s["block"] or f"пауза@{s['start']}"

    with ThreadPoolExecutor(max(1, workers)) as ex:
        for i, name in enumerate(ex.map(one, need), start=1):
            log(f"  [{i}/{len(need)}] {name}")
    job = Job(kind="qassemble", label="сборка", lane="assemble")
    stats = assemble(project, job, profile, doc["meta"]["version_id"], [s["block"] or f"пауза@{s['start']}" for s in need])
    if not stats:
        raise RuntimeError(job.note or "сборка не удалась")
    log(job.note or "")
    return stats


def last(project, profile: str) -> dict | None:
    f = qdir(project) / "logs" / f"last_{profile}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
