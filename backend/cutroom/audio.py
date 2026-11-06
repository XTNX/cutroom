"""Speech audio: which mic each take uses, and the clean-up chain.

- Audio source per take: a take (sync group) may contain a lavalier / field recorder file
  (audio-only source). By default it wins over camera mics; the user can pick any member.
  Times map through the sync offsets, so the lav lines up with the picture to < 1 frame.
- Clean-up chain (ffmpeg filters), applied once to the whole concatenated voice track after
  the per-segment 30 ms fades — denoise doesn't restart at every cut:
      highpass → denoise (afftdn) → de-esser → compressor
"""

from __future__ import annotations

import subprocess
from pathlib import Path

DENOISE = {
    "off": "",
    "soft": "afftdn=nr=8:nf=-45:tn=1",
    "medium": "afftdn=nr=14:nf=-40:tn=1",
    "strong": "afftdn=nr=22:nf=-35:tn=1",
}
COMPRESS = {
    "off": "",
    "soft": "acompressor=threshold=-20dB:ratio=2.5:attack=15:release=200:makeup=2",
    "speech": "acompressor=threshold=-24dB:ratio=4:attack=8:release=150:makeup=4",
}
DEFAULT_FX = {"highpass": True, "denoise": "off", "deess": False, "compress": "off"}


def fx_chain(fx: dict | None) -> str:
    fx = {**DEFAULT_FX, **(fx or {})}
    parts = []
    if fx.get("highpass"):
        parts.append("highpass=f=80")
    if DENOISE.get(fx.get("denoise", "off")):
        parts.append(DENOISE[fx["denoise"]])
    if fx.get("deess"):
        parts.append("deesser=i=0.4:m=0.5:f=0.5")
    if COMPRESS.get(fx.get("compress", "off")):
        parts.append(COMPRESS[fx["compress"]])
    return ",".join(parts)


def is_audio_only(project, sid: str) -> bool:
    s = project.sources.get(sid) or {}
    return not (s.get("probe") or {}).get("video")


def groups_in_use(project, edl: dict) -> list[dict]:
    """Take groups touched by the EDL, with members and the chosen/auto audio source."""
    from .sync import load_sync
    sync = load_sync(project)
    chosen = ((edl.get("audio") or {}).get("sources") or {})
    used = {r["source"] for r in edl.get("ranges", [])}
    out = []
    for g in sync.get("groups", []):
        ids = [m["id"] for m in g["members"]]
        if not used & set(ids):
            continue
        lav = [i for i in ids if is_audio_only(project, i)]
        pick = chosen.get(g["id"])
        out.append({
            "id": g["id"], "ref": g["ref"],
            "members": [{"id": m["id"], "audio_only": is_audio_only(project, m["id"]), "offset": m["offset"],
                         "status": m["status"], "confidence": m["confidence"],
                         "name": Path((project.sources.get(m["id"]) or {}).get("path", m["id"])).name} for m in g["members"]],
            "chosen": pick, "auto": lav[0] if lav else None,
            "effective": pick or (lav[0] if lav else None),   # None → each range keeps its own camera audio
        })
    return out


def effective_sources(project, edl: dict) -> dict[str, str]:
    return {g["id"]: g["effective"] for g in groups_in_use(project, edl) if g["effective"]}


def make_sample(project, edl: dict, out_time: float, seconds: float = 10.0) -> dict:
    """Two short WAVs around `out_time` of the cut: as recorded, and with the clean-up chain."""
    from .edl import resolve_ranges
    ranges = resolve_ranges(project, edl)
    r = next((x for x in ranges if x["out_start"] <= out_time < x["out_start"] + (x["end"] - x["start"])), ranges[0])
    a = r.get("audio") or {"source": r["source"], "start": r["start"]}
    start = a["start"] + max(0.0, out_time - r["out_start"])
    src = project.sources[a["source"]]["path"]
    verify = project.edit / "verify"
    verify.mkdir(exist_ok=True)
    chain = fx_chain((edl.get("audio") or {}).get("fx"))
    out = {}
    for name, af in (("before", ""), ("after", chain)):
        p = verify / f"sample_{name}.wav"
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-i", src, "-t", f"{seconds:.2f}", "-vn",
               "-ac", "2", "-ar", "48000"]
        if af:
            cmd += ["-af", af]
        cmd += [str(p)]
        subprocess.run(cmd, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out[name] = p.name
    out["source"] = a["source"]
    out["chain"] = chain or "без обработки"
    return out
