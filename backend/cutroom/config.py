"""Paths and persistent user settings.

Settings live in %APPDATA%/Cutroom/config.json (never in the renderer's localStorage).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
VIDEO_USE_DIR = REPO_DIR / "vendor" / "video-use"
VIDEO_USE_HELPERS = VIDEO_USE_DIR / "helpers"
CUTROOM_HELPERS = BACKEND_DIR / "helpers"
PROMPTS_DIR = BACKEND_DIR / "prompts"
PYTHON = Path(sys.executable)

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".mkv", ".avi", ".m4v", ".mts", ".webm"}
AUDIO_EXTS = {".wav", ".mp3", ".aac", ".m4a", ".flac"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / "Cutroom"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_PATH = _config_dir() / "config.json"

DEFAULTS: dict = {
    "asr_engine": "gigaam",              # gigaam | faster-whisper  (team choice by the ASR benchmark)
    "asr_model": "v3_e2e_rnnt",          # gigaam: v3_e2e_rnnt | v3_rnnt; whisper: large-v3 | large-v3-turbo | medium
    "asr_language": "ru",                # "" = detect from the first 30 s
    "asr_device": "auto",                # auto | cuda | cpu
    "cloud_asr_enabled": False,          # Yandex SpeechKit; only for recordings without PII
    "proxy_height": 540,
    "proxy_encoder": "auto",             # auto | h264_nvenc | libx264
    "cut_padding_ms": 100,               # Whisper drifts more than Scribe: 80-120 ms
    "low_confidence": 0.6,
    "recent_projects": [],
    "ui_layout": None,
    "theme": "dark",
    "claude_cli": None,          # explicit path to claude.exe; otherwise the newest desktop-app CLI
    "claude_auto_login": True,   # open the sign-in window at launch when not signed in
}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            data.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return data


def save_settings(patch: dict) -> dict:
    data = load_settings()
    for k, v in patch.items():
        if k in DEFAULTS:
            data[k] = v
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def cuda_dll_dirs() -> list[str]:
    """cuBLAS/cuDNN wheels ship DLLs under site-packages/nvidia/*/bin (Windows)."""
    try:
        import nvidia  # type: ignore
    except ImportError:
        return []
    out = []
    for root in nvidia.__path__:
        for b in Path(root).glob("*/bin"):
            out.append(str(b))
    return out


def subprocess_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    dirs = cuda_dll_dirs()
    if dirs:
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    return env
