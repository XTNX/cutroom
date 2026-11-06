"""Music shelf: tracks the user downloaded by hand from allow-listed free libraries.

Cutroom never downloads music by itself (knowledge base: «Музыка — только вручную из
разрешённых библиотек»). It scans a local folder, analyses each track (length, tempo,
energy, brightness) and ranks tracks for the current cut with reasons in plain words.
Tracks whose library/licence is unknown are flagged and ranked down.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import numpy as np

from .config import load_settings, save_settings

EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}

# Allow-list (what colleagues may download from) — licence notes in plain words.
LIBRARIES = {
    "mixkit": {"name": "Mixkit", "url": "https://mixkit.co/free-stock-music/",
               "licence": "Mixkit License: бесплатно, можно в коммерции, указывать автора не нужно"},
    "pixabay": {"name": "Pixabay Music", "url": "https://pixabay.com/music/",
                "licence": "Pixabay Content License: бесплатно, без указания автора"},
    "youtube": {"name": "YouTube Audio Library", "url": "https://studio.youtube.com/",
                "licence": "Бесплатно; у части треков обязательна подпись автора — смотрите пометку в библиотеке"},
    "fma": {"name": "Free Music Archive", "url": "https://freemusicarchive.org/",
            "licence": "Creative Commons — у каждого трека своя лицензия, проверьте CC BY / NC"},
    "uppbeat": {"name": "Uppbeat", "url": "https://uppbeat.io/",
                "licence": "Бесплатный план: нужен кредит автору в описании ролика"},
}
_PATTERNS = [("mixkit", r"mixkit"), ("pixabay", r"pixabay"), ("uppbeat", r"uppbeat"), ("fma", r"\bfma\b|freemusicarchive"),
             ("youtube", r"youtube|yt[-_ ]?audio")]


def music_dir() -> Path:
    d = load_settings().get("music_dir")
    p = Path(d) if d else Path.home() / "Music" / "Cutroom"
    p.mkdir(parents=True, exist_ok=True)
    if not d:
        save_settings({"music_dir": str(p)})
    return p


def _meta_path() -> Path:
    return music_dir() / ".cutroom_music.json"


def _load_meta() -> dict:
    p = _meta_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_meta(m: dict) -> None:
    _meta_path().write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def _tid(path: Path) -> str:
    st = path.stat()
    return hashlib.sha1(f"{path.name}|{st.st_size}".encode()).hexdigest()[:12]


def guess_library(name: str) -> str | None:
    low = name.lower()
    for key, pat in _PATTERNS:
        if re.search(pat, low):
            return key
    return None


def _decode(path: Path, sr: int = 22050, seconds: float = 90) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(path), "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return np.frombuffer(raw, np.float32).copy()


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def analyse(path: Path) -> dict:
    import librosa
    y = _decode(path)
    sr = 22050
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    rms = librosa.feature.rms(y=y)[0]
    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    return {
        "duration": round(_duration(path), 2),
        "bpm": round(float(np.atleast_1d(tempo)[0]), 1),
        "energy": round(float(np.clip(np.mean(rms) / 0.25, 0, 1)), 3),          # 0 quiet … 1 loud/dense
        "brightness": round(float(np.clip(np.mean(cent) / 4000, 0, 1)), 3),     # 0 dark … 1 bright
        "busyness": round(float(np.clip(np.mean(onset) / 4, 0, 1)), 3),         # how much happens per second
    }


def scan(job=None) -> list[dict]:
    meta = _load_meta()
    files = sorted(p for p in music_dir().iterdir() if p.is_file() and p.suffix.lower() in EXTS)
    seen = set()
    for k, p in enumerate(files):
        tid = _tid(p)
        seen.add(tid)
        m = meta.get(tid) or {"id": tid, "file": p.name, "library": guess_library(p.name), "licence_note": ""}
        m["file"] = p.name
        if "analysis" not in m:
            try:
                m["analysis"] = analyse(p)
            except Exception as e:  # noqa: BLE001
                m["analysis"] = {"error": str(e)[:200]}
        meta[tid] = m
        if job is not None:
            job.progress = (k + 1) / max(1, len(files))
    for tid in [t for t in meta if t not in seen]:
        del meta[tid]
    _save_meta(meta)
    return list_tracks()


def list_tracks() -> list[dict]:
    meta = _load_meta()
    out = []
    for m in meta.values():
        lib = LIBRARIES.get(m.get("library") or "")
        out.append({**m, "path": str(music_dir() / m["file"]), "library_name": lib["name"] if lib else None,
                    "licence": m.get("licence_note") or (lib["licence"] if lib else None)})
    return sorted(out, key=lambda x: x["file"].lower())


def update_track(tid: str, patch: dict) -> dict:
    meta = _load_meta()
    if tid not in meta:
        raise KeyError(tid)
    for k in ("library", "licence_note"):
        if k in patch:
            meta[tid][k] = patch[k]
    _save_meta(meta)
    return next(t for t in list_tracks() if t["id"] == tid)


PACE_BPM = {"спокойный": (60, 95), "живой": (90, 118), "динамичный": (112, 150)}


def rank(target_s: float, pace: str | None, tone: str | None = None) -> list[dict]:
    """Score tracks for a cut of `target_s` seconds. Higher is better; reasons in Russian."""
    lo, hi = PACE_BPM.get(pace or "", (85, 125))
    want_energy = {"спокойный": 0.25, "живой": 0.45, "динамичный": 0.7}.get(pace or "", 0.4)
    ranked = []
    for t in list_tracks():
        a = t.get("analysis") or {}
        if "bpm" not in a:
            continue
        score, why = 0.0, []
        bpm = a["bpm"]
        # half/double-time count as a match: a 140 bpm track can feel like 70
        b = min((bpm, bpm / 2, bpm * 2), key=lambda x: 0 if lo <= x <= hi else min(abs(x - lo), abs(x - hi)))
        if lo <= b <= hi:
            score += 3
            why.append(f"темп {round(bpm)} уд/мин подходит к темпу монтажа «{pace or 'обычный'}»")
        else:
            score += max(0, 3 - min(abs(b - lo), abs(b - hi)) / 10)
        dur = a.get("duration", 0)
        if dur >= target_s:
            score += 2
            why.append(f"длиннее ролика ({round(dur)} с ≥ {round(target_s)} с) — хватит без повтора")
        elif dur >= target_s * 0.6:
            score += 1
            why.append(f"короче ролика ({round(dur)} с) — понадобится повтор или затухание раньше")
        else:
            why.append(f"заметно короче ролика ({round(dur)} с)")
        e = a.get("energy", 0.4)
        score += 2 * (1 - min(1, abs(e - want_energy) / 0.5))
        if a.get("busyness", 0) < 0.35:
            score += 1
            why.append("ровный, не спорит с речью")
        if not t.get("library"):
            score -= 3
            why.append("источник не указан — укажите библиотеку перед финалом")
        ranked.append({**t, "score": round(score, 2), "why": why})
    return sorted(ranked, key=lambda x: -x["score"])
