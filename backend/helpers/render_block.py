"""Quick mode: render ONE timeline segment (a video block or a gap) to a cached file.

Picture only — the sound is assembled separately as PCM, so a segment never depends on its
neighbours. Every segment of one profile is encoded with identical parameters (size, rate,
encoder, GOP, timescale), which is what lets the final assembly be a lossless `-c copy` concat
(video-use Hard Rule 2). Frame-exact: the source is resampled to the project rate inside the
graph and cut to exactly `frames` frames. Overlays (infographic cards, slides) are burned into
the segments they touch, each shifted to its own offset inside the segment (Hard Rule 1: overlays
go on top of the graded picture).

Usage: python render_block.py <job.json>
job = {out, frames, rate, w, h, encoder, quality: draft|final, src|null, in, hdr, grade,
       overlays: [{file, image, offset, skip, dur}]}
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "vendor" / "video-use" / "helpers"))
import render as vu  # noqa: E402  (video-use helpers, pinned commit)
from render_cutroom import ff  # noqa: E402

PAD = "0x454335"   # letterbox colour for portrait clips: the brand's khaki instead of black


def encode_args(j: dict) -> list[str]:
    from fractions import Fraction
    gop = str(max(1, round(float(Fraction(j["rate"])) * 2)))
    if j["encoder"] == "h264_nvenc":
        q = ["-preset", "p2", "-cq", "30"] if j["quality"] == "draft" else ["-preset", "p5", "-cq", "21"]
        return ["-c:v", "h264_nvenc", *q, "-rc", "vbr", "-b:v", "0", "-profile:v", "high", "-bf", "0", "-g", gop]
    q = ["-preset", "ultrafast", "-crf", "28"] if j["quality"] == "draft" else ["-preset", "fast", "-crf", "20"]
    return ["-c:v", "libx264", *q, "-profile:v", "high", "-bf", "0", "-g", gop, "-x264-params", "open-gop=0"]


def build(j: dict, hw_decode: bool) -> list[str]:
    w, h, rate, n = j["w"], j["h"], j["rate"], int(j["frames"])
    dur = n / float(__import__("fractions").Fraction(rate))
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if j.get("src"):
        if hw_decode:
            cmd += ["-hwaccel", "cuda"]
        cmd += ["-ss", f"{float(j['in']):.4f}", "-i", j["src"]]
        chain = ([vu.TONEMAP_CHAIN] if j.get("hdr") else []) + [
            f"scale={w}:{h}:force_original_aspect_ratio=decrease", f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={PAD}", "setsar=1"]
        if j.get("grade"):
            chain.append(j["grade"])
    else:   # a gap on the video track: black, same format as every other segment
        cmd += ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={rate}:d={dur + 1:.3f}"]
        chain = ["setsar=1"]
    chain += [f"fps={rate}", f"trim=end_frame={n}", "setpts=PTS-STARTPTS"]
    parts = [f"[0:v]{','.join(chain)}[b0]"]
    cur = "[b0]"
    for k, ov in enumerate(j.get("overlays") or [], start=1):
        off, odur = float(ov["offset"]), float(ov["dur"])
        if ov.get("image"):
            cmd += ["-loop", "1", "-framerate", rate, "-t", f"{odur + 0.1:.3f}", "-i", ov["file"]]
        else:
            cmd += ["-ss", f"{float(ov.get('skip', 0)):.4f}", "-i", ov["file"]]
        parts.append(f"[{k}:v]format=rgba,scale={w}:{h}:force_original_aspect_ratio=decrease,setpts=PTS-STARTPTS+{off:.4f}/TB[o{k}]")
        parts.append(f"{cur}[o{k}]overlay=x=(W-w)/2:y=(H-h)/2:eof_action=pass:enable='between(t,{off:.4f},{off + odur:.4f})'[v{k}]")
        cur = f"[v{k}]"
    parts.append(f"{cur}format=yuv420p,trim=end_frame={n}[out]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-an", "-r", rate, "-fps_mode", "cfr",
            *encode_args(j), "-pix_fmt", "yuv420p", "-video_track_timescale", "90000", j["out"]]
    return cmd


def main() -> None:
    j = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(j["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".part" + out.suffix)
    j = {**j, "out": str(part)}
    hw = bool(j.get("src")) and j["encoder"] == "h264_nvenc" and not j.get("hdr")
    try:
        ff(build(j, hw), f"блок {out.stem[:8]}")
    except SystemExit:
        if not hw:
            raise
        ff(build(j, False), f"блок {out.stem[:8]}")   # some sources refuse CUDA decode: CPU decode instead
    part.replace(out)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
