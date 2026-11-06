"""ffprobe, proxy/audio/thumbnail command builders, waveform peaks, fast file hashing.

Originals are never modified. Everything derived lands under <videos_dir>/edit/.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
PEAKS_PER_SEC = 100  # one peak per 10 ms


def fast_hash(path: Path, chunk: int = 4 << 20) -> str:
    """Content hash: size + three 4 MB samples (head/middle/tail). Full-hashing 2.7 GB
    camera files on every open would take minutes; this catches any re-export,
    trim or replacement, ignores a bare mtime touch, and finds byte-identical copies."""
    st = path.stat()
    h = hashlib.blake2b(digest_size=12)
    h.update(str(st.st_size).encode())
    with open(path, "rb") as f:
        for off in (0, max(0, st.st_size // 2 - chunk // 2), max(0, st.st_size - chunk)):
            f.seek(off)
            h.update(f.read(chunk))
    return h.hexdigest()


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-400:] or "ffprobe failed")
    return json.loads(out.stdout)


def _fps(s: str | None) -> float | None:
    try:
        f = Fraction(s)  # type: ignore[arg-type]
        return float(f) if f else None
    except Exception:
        return None


def summarize_probe(raw: dict) -> dict:
    """Flatten ffprobe JSON to what the UI and pipeline need."""
    fmt = raw.get("format", {})
    vs = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video"
               and not s.get("disposition", {}).get("attached_pic")), None)
    audio = [s for s in raw.get("streams", []) if s.get("codec_type") == "audio"]
    rotation = 0
    if vs:
        for sd in vs.get("side_data_list", []) or []:
            if "rotation" in sd:
                rotation = int(sd["rotation"])
        rotation = int(vs.get("tags", {}).get("rotate", rotation))
    w, h = (vs.get("width"), vs.get("height")) if vs else (None, None)
    disp_w, disp_h = (h, w) if rotation % 180 else (w, h)
    tags = fmt.get("tags", {}) or {}
    return {
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or 0),
        "container": fmt.get("format_name"),
        "creation_time": tags.get("creation_time") or (vs or {}).get("tags", {}).get("creation_time"),
        "video": None if not vs else {
            "codec": vs.get("codec_name"),
            "width": w, "height": h,
            "display_width": disp_w, "display_height": disp_h,
            "rotation": rotation,
            "fps": _fps(vs.get("avg_frame_rate")) or _fps(vs.get("r_frame_rate")),
            "fps_raw": vs.get("r_frame_rate"),
            "pix_fmt": vs.get("pix_fmt"),
            "color_transfer": vs.get("color_transfer"),
            "hdr": vs.get("color_transfer") in HDR_TRANSFERS,
            "bit_rate": int(vs.get("bit_rate") or 0) or None,
        },
        "audio": [{
            "index": i, "codec": a.get("codec_name"), "channels": a.get("channels"),
            "sample_rate": int(a.get("sample_rate") or 0), "layout": a.get("channel_layout"),
        } for i, a in enumerate(audio)],
    }


def proxy_cmd(src: Path, dst: Path, info: dict, height: int, encoder: str, hw_decode: bool) -> list[str]:
    """540p/720p short-GOP H.264 proxy for instant scrubbing.

    -g 12 / no B-frames: every 12th frame is a keyframe, so seeking lands fast.
    HDR (HLG/PQ) is tone-mapped to Rec.709 so the proxy looks like the final grade input.
    """
    v = info.get("video") or {}
    hdr = v.get("hdr")
    rotated = bool(v.get("rotation"))
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1"]
    use_cuda = hw_decode and encoder == "h264_nvenc" and not hdr and not rotated
    if use_cuda:
        cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    cmd += ["-i", str(src), "-map", "0:v:0"]
    if info.get("audio"):
        cmd += ["-map", "0:a:0"]
    # scale on the short side so portrait clips stay portrait
    portrait = (v.get("display_height") or 0) > (v.get("display_width") or 0)
    if use_cuda:
        sc = f"scale_cuda={height}:-2:format=nv12" if portrait else f"scale_cuda=-2:{height}:format=nv12"
        vf = sc
    else:
        sc = f"scale={height}:-2" if portrait else f"scale=-2:{height}"
        if hdr:
            vf = (sc + ":flags=bilinear,zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                  "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
        else:
            vf = sc + ":flags=bilinear,format=yuv420p"
    cmd += ["-vf", vf]
    if encoder == "h264_nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p3", "-tune", "ll", "-rc", "vbr", "-cq", "30",
                "-g", "12", "-bf", "0"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-g", "12", "-bf", "0"]
    if hdr:
        cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    if info.get("audio"):
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    cmd += ["-movflags", "+faststart", "-map_metadata", "-1", str(dst)]
    return cmd


def audio_cmd(src: Path, dst: Path, track: int = 0) -> list[str]:
    """16 kHz mono PCM: input for Whisper, the waveform and the angle sync."""
    return ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(src),
            "-map", f"0:a:{track}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]


def thumb_cmd(src: Path, dst: Path, at: float) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1",
            "-vf", "scale=-2:180", "-q:v", "4", str(dst)]


def compute_peaks(wav: Path, out: Path) -> dict:
    """Max-abs per 10 ms bucket, quantized to uint8. One hour = 360 KB."""
    import wave

    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        hop = sr // PEAKS_PER_SEC
        peaks = np.zeros((n + hop - 1) // hop, dtype=np.uint8)
        idx = 0
        block = hop * PEAKS_PER_SEC * 60  # one minute at a time
        while True:
            frames = w.readframes(block)
            if not frames:
                break
            a = np.abs(np.frombuffer(frames, dtype=np.int16).astype(np.int32))
            k = len(a) // hop
            if k:
                m = a[: k * hop].reshape(k, hop).max(axis=1)
                peaks[idx: idx + k] = np.minimum(255, (m / 32768.0) ** 0.5 * 255).astype(np.uint8)
                idx += k
            rest = len(a) - k * hop
            if rest:
                peaks[idx] = min(255, int((a[-rest:].max() / 32768.0) ** 0.5 * 255))
                idx += 1
    peaks = peaks[:idx]
    out.write_bytes(peaks.tobytes())
    return {"count": int(idx), "per_sec": PEAKS_PER_SEC}


def silence_spans(peaks_path: Path, min_len: float = 0.4, thresh: int = 28) -> list[list[float]]:
    """Quiet spans from the peak envelope (≈ -38 dBFS after the sqrt curve)."""
    p = np.frombuffer(peaks_path.read_bytes(), dtype=np.uint8)
    quiet = p < thresh
    spans, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) / PEAKS_PER_SEC >= min_len:
                spans.append([start / PEAKS_PER_SEC, i / PEAKS_PER_SEC])
            start = None
    if start is not None and (len(p) - start) / PEAKS_PER_SEC >= min_len:
        spans.append([start / PEAKS_PER_SEC, len(p) / PEAKS_PER_SEC])
    return spans
