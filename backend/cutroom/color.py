"""Colour (stage 7): a grade = preset + ASC CDL + temperature + raw ffmpeg filter.

Applied per segment during extract (video-use rule: never after concat). The whole cut has one
grade (edl["color"]); an episode may override it (range["color"]).

ASC CDL per channel:  out = clamp(in · slope + offset) ^ power, then global saturation —
expressed as ffmpeg `lutrgb` (exact per channel) + `eq=saturation`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .config import VIDEO_USE_HELPERS

sys.path.insert(0, str(VIDEO_USE_HELPERS))
from grade import PRESETS  # noqa: E402  (video-use presets, pinned commit)

PRESET_NAMES = {"none": "без цвета", "subtle": "лёгкая чистка", "neutral_punch": "нейтрально, плотнее", "warm_cinematic": "тёплое кино"}
IDENTITY = {"slope": [1.0, 1.0, 1.0], "offset": [0.0, 0.0, 0.0], "power": [1.0, 1.0, 1.0], "sat": 1.0}


def _is_identity(cdl: dict) -> bool:
    return all(abs(a - b) < 1e-4 for k in ("slope", "offset", "power") for a, b in zip(cdl.get(k, IDENTITY[k]), IDENTITY[k]))


def build_filter(g: dict | None) -> str:
    """Grade dict → ffmpeg filter chain (empty string = untouched)."""
    if not g:
        return ""
    parts = []
    pre = PRESETS.get(g.get("preset") or "none", "")
    if pre:
        parts.append(pre)
    cdl = {**IDENTITY, **(g.get("cdl") or {})}
    if not _is_identity(cdl):
        ch = []
        for i, c in enumerate("rgb"):
            s, o, pw = cdl["slope"][i], cdl["offset"][i], cdl["power"][i]
            ch.append(f"{c}='clip(pow(clip(val/maxval*{s:.4f}+{o:.4f}\\,0\\,1)\\,{pw:.4f})*maxval\\,0\\,maxval)'")
        parts.append("lutrgb=" + ":".join(ch))
    if abs(cdl.get("sat", 1.0) - 1.0) > 1e-3:
        parts.append(f"eq=saturation={cdl['sat']:.3f}")
    t = float(g.get("temperature") or 0)
    if abs(t) > 0.5:   # −100 (cooler) … +100 (warmer) → 4000 K … 9000 K around 6500 K
        k = 6500 - t * 25   # colortemperature: lower kelvin = warmer picture
        parts.append(f"colortemperature=temperature={k:.0f}:mix=1:pl=1")
    raw = (g.get("raw") or "").strip()
    if raw:
        parts.append(raw)
    return ",".join(parts)


def frame(src: Path, t: float, g: dict | None, hdr: bool, out: Path, width: int = 960) -> Path:
    """One still of `src` at `t`, graded or not — for the before/after wipe."""
    vf = []
    if hdr:
        vf.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    vf.append(f"scale={width}:-2")
    f = build_filter(g)
    if f:
        vf.append(f)
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1", "-vf", ",".join(vf), "-q:v", "3", str(out)]
    p = subprocess.run(cmd, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["ffmpeg"])
    return out
