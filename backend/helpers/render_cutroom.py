"""Cutroom render: video-use's pipeline + picture/sound from different sources + speech clean-up + music bed.

A/V sync (measured, see evals): every segment is a whole number of frames, cut on the
cumulative EDL clock, with its audio trimmed to exactly that length; intermediates carry
PCM audio (.mov) and AAC is encoded once, at the very end. AAC-in-MP4 intermediates add
21.3 ms of priming per re-encode, and frame-rounded video next to unrounded audio makes
the sound slip ~1 frame per cut — both measured, both gone this way.

Order (video-use Hard Rules kept):
  1. per-segment extract: picture from the range's angle, sound from the take's speech mic
     (lavalier / recorder) at the sync-mapped time, 30 ms fades at both edges (Rule 3)
  2. lossless -c copy concat (Rule 2) — video-use concat_segments
  3. audio pass on the whole voice track (video copied, not re-encoded): clean-up chain,
     then the music bed ducked under speech with sidechaincompress
  4. overlays → subtitles LAST (Rule 1) — video-use build_final_composite
  5. two-pass loudnorm −14 LUFS / −1 dBTP — video-use apply_loudnorm_two_pass

Input: edl_render.json written by cutroom.edl.resolve_for_render.
Progress: prints `SEG k/n` per extracted segment and `STEP <name>` for the later passes.

Usage: python render_cutroom.py <edl_render.json> -o out.mp4 [--preview|--draft] [--no-loudnorm]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "vendor" / "video-use" / "helpers"))
import render as vu  # noqa: E402  (video-use helpers, pinned commit)

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ff(cmd: list[str], what: str) -> None:
    """Run ffmpeg; on failure raise with ffmpeg's own last lines instead of a Python traceback."""
    p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NOWIN)
    if p.returncode != 0:
        tail = [l for l in p.stderr.decode("utf-8", "replace").splitlines() if l.strip()][-4:]
        raise SystemExit(f"ОШИБКА ffmpeg ({what}): " + " | ".join(tail))


def has_audio(path: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, creationflags=NOWIN).stdout
    return bool(out.strip())


def composite(base: Path, overlays: list[dict], out: Path) -> None:
    """video-use build_final_composite, plus: each card is fitted to the base frame (draft is 1280 wide,
    portrait sources are tall; cards are drawn 1920x1080). PTS shift to the window start, alpha kept."""
    if not overlays:
        vu.build_final_composite(base, [], None, out, base.parent)
        return
    wh = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                         str(base)], capture_output=True, text=True, creationflags=NOWIN).stdout.strip().split(",")
    w, h = int(wh[0]), int(wh[1])
    inputs, parts, cur = ["-i", str(base)], [], "[0:v]"
    for i, ov in enumerate(overlays, start=1):
        t, end = float(ov["start_in_output"]), float(ov["start_in_output"]) + float(ov["duration"])
        if ov.get("image"):   # a still (slide from quick mode): looped for the window it's shown
            inputs += ["-loop", "1", "-t", f"{end + 0.1:.3f}", "-i", str(ov["image"])]
        else:
            inputs += ["-i", str(ov["file"])]
        parts.append(f"[{i}:v]format=rgba,scale={w}:{h}:force_original_aspect_ratio=decrease,setpts=PTS-STARTPTS+{t}/TB[a{i}]")
        parts.append(f"{cur}[a{i}]overlay=x=(W-w)/2:y=(H-h)/2:eof_action=pass:enable='between(t,{t:.3f},{end:.3f})'[v{i}]")
        cur = f"[v{i}]"
    ff(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts), "-map", cur, "-map", "0:a", "-c:v", "libx264", "-preset", "fast",
        "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)], "инфографика поверх кадра")


def media_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, creationflags=NOWIN).stdout
    return float(out.strip() or 0)


def extract_av(video: Path, v_start: float, frames: int, fps: float, audio: Path | None, a_start: float, grade: str,
               out: Path, rate: str, preview: bool, draft: bool, fade_in: bool = True, fade_out: bool = True) -> None:
    """video-use extract_segment, but: the sound may come from another file (lav) at its own
    time, the length is exactly `frames` frames for both streams, and the audio stays PCM."""
    dur = frames / fps
    portrait = vu.is_portrait_source(video)
    if draft:
        scale = "scale=-2:1280" if portrait else "scale=1280:-2"
    else:
        scale = "scale=-2:1920" if portrait else "scale=1920:-2"
    # exact length inside the graph: resample to the output rate, then keep exactly `frames` frames
    # (an output-side -frames:v stops the muxer early and drops queued audio)
    vf = ",".join([p for p in ([vu.TONEMAP_CHAIN] if vu.is_hdr_source(video) else []) + [scale, grade] if p]
                  + [f"fps={rate}", f"trim=end_frame={frames}"])
    # Rule 3 fades only where the sound really jumps; an angle change inside continuous speech keeps the waveform whole
    af = ",".join(([f"afade=t=in:st=0:d=0.03"] if fade_in else []) + ([f"afade=t=out:st={max(0.0, dur - 0.03):.3f}:d=0.03"] if fade_out else [])) or "anull"
    preset, crf = ("ultrafast", "28") if draft else (("medium", "22") if preview else ("fast", "20"))
    cmd = ["ffmpeg", "-y", "-ss", f"{v_start:.3f}", "-i", str(video)]
    amap = "0:a:0"
    if audio is not None:
        lead = max(0.0, -a_start)   # recorder started after the camera: pad the gap with silence
        cmd += ["-ss", f"{max(0.0, a_start):.4f}", "-i", str(audio)]
        amap = "1:a:0"
        if lead > 0:
            af = f"adelay={int(lead * 1000)}:all=1," + af
    af = af + f",apad,atrim=end={dur:.6f}"
    cmd += ["-map", "0:v:0", "-map", amap, "-vf", vf, "-af", af,
            "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out)]
    ff(cmd, f"фрагмент из {video.name}")


def _pcm(path: Path, af: str | None, seconds: float = 8.0, sr: int = 16000):
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-t", f"{seconds}", "-i", str(path), "-vn"]
    if af:
        cmd += ["-af", af]
    cmd += ["-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, creationflags=NOWIN).stdout, np.float32)


def chain_latency(base: Path, fx: str) -> float:
    """Some filters delay the signal (afftdn: 25 ms, measured). Measure the chain on the first
    seconds of this very track and return the delay to cut, so speech stays on the lips."""
    if not fx:
        return 0.0
    sys.path.insert(0, str(HERE.parent))
    from cutroom.sync import gcc_phat
    a, b = _pcm(base, fx), _pcm(base, None)
    n = min(len(a), len(b))
    if n < 16000:
        return 0.0
    lag, sharp = gcc_phat(a[:n], b[:n], 0.2)
    return lag if sharp > 8 and lag > 0 else 0.0


def audio_pass(base: Path, out: Path, fx: str, music: dict | None) -> None:
    """Voice clean-up + ducked music on the concatenated track; picture is stream-copied."""
    total = media_duration(base)
    inputs = ["-i", str(base)]
    lat = chain_latency(base, fx)
    if lat:
        print(f"STEP audio-latency {lat * 1000:.1f} ms compensated", flush=True)
    comp = f",atrim=start={lat:.4f},asetpts=PTS-STARTPTS,apad,atrim=end={total:.4f}" if lat else ""
    parts = [f"[0:a]{fx or 'anull'}{comp}[voice]"]
    label = "[voice]"
    if music and music.get("path") and Path(music["path"]).exists():
        inputs += ["-stream_loop", "-1" if music.get("loop") else "0", "-i", music["path"]]
        start = float(music.get("start_in_output", 0))
        off = float(music.get("offset", 0))
        gain = float(music.get("gain_db", -18))
        duck = float(music.get("duck_db", -10))
        fin, fout = float(music.get("fade_in", 1.0)), float(music.get("fade_out", 2.0))
        # deeper duck = higher ratio; threshold low enough that normal speech triggers it
        ratio = max(1.5, min(20.0, 1 + abs(duck) / 2))
        parts += [
            "[voice]asplit=2[v1][sc]",
            f"[1:a]atrim=start={off:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
            f"volume={gain:.1f}dB,afade=t=in:st=0:d={fin:.2f},adelay={int(start * 1000)}:all=1,"
            f"atrim=end={total:.3f},afade=t=out:st={max(0.0, total - fout):.3f}:d={fout:.2f}[m0]",
            f"[m0][sc]sidechaincompress=threshold=0.02:ratio={ratio:.1f}:attack=40:release=600[md]",
            "[v1][md]amix=inputs=2:duration=first:normalize=0[mix]",
        ]
        label = "[mix]"
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts), "-map", "0:v", "-map", label,
           "-c:v", "copy", "-c:a", "pcm_s16le", "-ar", "48000", str(out)]
    ff(cmd, "обработка звука и музыка")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("edl", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--no-loudnorm", action="store_true")
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    edit_dir = edl_path.parent
    sources = {k: Path(v) for k, v in edl["sources"].items()}
    ranges = edl["ranges"]
    grade = vu.resolve_grade_filter(edl.get("grade"))
    grade = "" if grade == "__AUTO__" else grade
    rate = vu.probe_source_fps(sources[ranges[0]["source"]]) or "24"
    from fractions import Fraction
    fps = float(Fraction(rate))
    clips = edit_dir / ("clips_draft" if args.draft else ("clips_preview" if args.preview else "clips_graded"))
    clips.mkdir(parents=True, exist_ok=True)

    segs = []
    clock = 0.0       # cumulative EDL time; frame boundaries come from it, so error never accumulates
    done_frames = 0
    def a_span(r):
        a = r.get("audio") or {}
        t0 = float(a.get("start", r["start"]))
        return a.get("source", r["source"]), t0, t0 + float(r["end"]) - float(r["start"])
    joined = [False] + [a_span(p)[0] == a_span(q)[0] and abs(a_span(q)[1] - a_span(p)[2]) < 0.005 for p, q in zip(ranges, ranges[1:])]
    for i, r in enumerate(ranges):
        video = sources[r["source"]]
        dur = float(r["end"]) - float(r["start"])
        clock += dur
        frames = max(1, round(clock * fps) - done_frames)
        done_frames += frames
        a = r.get("audio")
        a_path = sources.get(a["source"]) if a else None
        if a_path is not None and not has_audio(a_path):
            a_path = None
        out = clips / f"seg_{i:02d}_{r['source']}.mov"
        print(f"SEG {i + 1}/{len(ranges)} {r['source']} {float(r['start']):.2f}+{dur:.2f}"
              + (f" звук: {a['source']} @ {a['start']:.3f}" if a_path else ""), flush=True)
        seg_grade = r.get("grade_filter") if r.get("grade_filter") is not None else grade   # per-episode colour
        extract_av(video, float(r["start"]), frames, fps, a_path, float(a["start"]) if a_path else 0.0, seg_grade, out, rate,
                   args.preview, args.draft, fade_in=not joined[i], fade_out=not (i + 1 < len(joined) and joined[i + 1]))
        segs.append(out)

    print("STEP concat", flush=True)
    base = edit_dir / ("base_draft.mov" if args.draft else ("base_preview.mov" if args.preview else "base.mov"))
    vu.concat_segments(segs, base, edit_dir)

    fx, music = edl.get("audio_fx") or "", edl.get("music")
    if fx or (music and music.get("path")):
        print("STEP audio " + (fx or "-") + (" + music" if music else ""), flush=True)
        processed = base.with_name(base.stem + "_audio.mov")
        audio_pass(base, processed, fx, music)
        base = processed

    out_path = args.output.resolve()
    print("STEP composite", flush=True)
    tmp = out_path.with_suffix(".prenorm.mov")
    composite(base, edl.get("overlays") or [], tmp)
    if args.no_loudnorm:   # the one and only AAC encode
        subprocess.run(["ffmpeg", "-y", "-i", str(tmp), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                        "-movflags", "+faststart", str(out_path)], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, creationflags=NOWIN)
    else:
        print("STEP loudnorm", flush=True)
        vu.apply_loudnorm_two_pass(tmp, out_path, preview=args.draft)   # encodes AAC once
    tmp.unlink(missing_ok=True)
    print(f"DONE {out_path}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:   # video-use steps: show ffmpeg's words, not a traceback
        err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        tail = [l for l in err.splitlines() if l.strip()][-4:]
        raise SystemExit("ОШИБКА ffmpeg: " + (" | ".join(tail) or str(e)))
