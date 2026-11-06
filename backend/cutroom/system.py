"""Hardware and toolchain detection for the «Система» screen."""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import psutil

from .config import cuda_dll_dirs

# Conservative real-time factors (compute seconds per audio second) for
# faster-whisper, beam_size=5, no VAD. Replaced by measured values once a
# transcription has run on this machine.
RTF = {
    ("cuda", "large-v3"): 0.06,
    ("cuda", "large-v3-turbo"): 0.025,
    ("cuda", "medium"): 0.035,
    ("cpu", "large-v3"): 1.6,
    ("cpu", "large-v3-turbo"): 0.55,
    ("cpu", "medium"): 0.7,
    ("cuda", "v3_e2e_rnnt"): 0.09,   # GigaAM, measured on RTX 5050 (bench 2026-11-05)
    ("cuda", "v3_rnnt"): 0.1,
    ("cpu", "v3_e2e_rnnt"): 0.8,
    ("cpu", "v3_rnnt"): 0.9,
}

# GigaAM weights come from the HF mirror (Sber's CDN has an expired TLS certificate)
GIGAAM_MODELS = {"v3_e2e_rnnt": ("e2e_rnnt", 4.49e8), "v3_rnnt": ("rnnt", 4.49e8)}

MODEL_REPOS = {
    "large-v3": ("Systran/faster-whisper-large-v3", 3.09e9),
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", 1.62e9),
    "medium": ("Systran/faster-whisper-medium", 1.53e9),
}


def _run(cmd: list[str], timeout: float = 15) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace").stdout
    except Exception:
        return ""


@lru_cache(maxsize=1)
def gpu_info() -> tuple[dict, ...]:
    if not shutil.which("nvidia-smi"):
        return ()
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            gpus.append({"name": parts[0], "vram_mb": int(float(parts[1])), "driver": parts[2],
                         "vendor": "nvidia"})
    return tuple(gpus)


@lru_cache(maxsize=1)
def cuda_usable() -> bool:
    try:
        for d in cuda_dll_dirs():
            os.add_dll_directory(d)
        import ctranslate2  # type: ignore
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


@lru_cache(maxsize=1)
def cpu_name() -> str | None:
    if os.name != "nt":
        return None
    return _run(["powershell", "-NoProfile", "-c", "(Get-CimInstance Win32_Processor).Name"]).strip() or None


@lru_cache(maxsize=1)
def ffmpeg_info() -> dict:
    ver = _run(["ffmpeg", "-version"]).splitlines()[:1]
    working = []
    for enc in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "h264_amf"):
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                                "color=c=black:s=256x256:d=0.1", "-c:v", enc, "-f", "null", "-"],
                               capture_output=True, timeout=20)
            if r.returncode == 0:
                working.append(enc)
        except Exception:
            pass
    return {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "version": ver[0] if ver else None,
        "hw_encoders": working,
        "has_zscale": "zscale" in _run(["ffmpeg", "-hide_banner", "-filters"]),
    }


def proxy_encoder(setting: str = "auto") -> str:
    if setting != "auto":
        return setting
    return "h264_nvenc" if "h264_nvenc" in ffmpeg_info()["hw_encoders"] else "libx264"


def model_cache_dir() -> Path:
    hf = os.environ.get("HF_HOME")
    return Path(hf) / "hub" if hf else Path.home() / ".cache" / "huggingface" / "hub"


def model_status() -> list[dict]:
    out = []
    base = model_cache_dir()
    for name, (repo, size) in MODEL_REPOS.items():
        d = base / ("models--" + repo.replace("/", "--"))
        have = 0
        ready = False
        blobs = d / "blobs"
        if blobs.exists():
            have = sum(f.stat().st_size for f in blobs.glob("*") if f.is_file())
        snaps = d / "snapshots"
        if snaps.exists():
            # without symlink rights (Windows default) HF writes real files into snapshots/
            have += sum(f.stat().st_size for f in snaps.glob("*/*") if f.is_file() and not f.is_symlink())
        if snaps.exists():
            ready = any((s / "model.bin").exists() for s in snaps.glob("*"))
        if blobs.exists() and any(blobs.glob("*.incomplete")):
            ready = False
        out.append({"name": name, "engine": "faster-whisper", "repo": repo, "size": size, "downloaded": have, "ready": ready})
    for name, (rev, size) in GIGAAM_MODELS.items():
        ready = False
        try:
            from huggingface_hub import hf_hub_download
            hf_hub_download("ai-sage/GigaAM-v3", "pytorch_model.bin", revision=rev, local_files_only=True)
            ready = True
        except Exception:
            pass
        out.append({"name": name, "engine": "gigaam", "repo": f"ai-sage/GigaAM-v3@{rev}", "size": size,
                    "downloaded": size if ready else 0, "ready": ready})
    return out


def recommend(gpus: tuple[dict, ...], ram_gb: float, cuda: bool) -> dict:
    if cuda and gpus and gpus[0]["vram_mb"] >= 6000:
        return {"model": "large-v3", "device": "cuda", "compute": "float16",
                "why": f"{gpus[0]['name']} с {round(gpus[0]['vram_mb'] / 1024)} ГБ видеопамяти тянет large-v3 в float16."}
    if cuda and gpus:
        return {"model": "large-v3-turbo", "device": "cuda", "compute": "int8_float16",
                "why": "Видеопамяти меньше 6 ГБ: turbo в int8_float16 помещается и почти не теряет точности."}
    if ram_gb >= 12:
        return {"model": "large-v3-turbo", "device": "cpu", "compute": "int8",
                "why": "Видеокарты NVIDIA нет: на CPU разумен turbo в int8, расшифровка идёт фоном."}
    return {"model": "medium", "device": "cpu", "compute": "int8",
            "why": "Мало памяти и нет GPU: medium в int8, расшифровка идёт фоном и долго."}


def snapshot(measured_rtf: dict | None = None) -> dict:
    gpus = gpu_info()
    vm = psutil.virtual_memory()
    ram_gb = vm.total / 2**30
    cuda = cuda_usable()
    device = "cuda" if cuda else "cpu"
    measured_rtf = measured_rtf or {}
    eta = {}
    for (dev, model), rtf in RTF.items():
        if dev != device:
            continue
        key = f"{dev}:{model}"
        r = measured_rtf.get(key, rtf)
        eta[model] = {"rtf": round(r, 4), "minutes_per_hour": round(r * 60, 1), "measured": key in measured_rtf}
    return {
        "cpu": {"name": cpu_name(), "cores": psutil.cpu_count(logical=False), "threads": psutil.cpu_count()},
        "ram_gb": round(ram_gb, 1),
        "gpus": list(gpus),
        "cuda": cuda,
        "device": device,
        "ffmpeg": ffmpeg_info(),
        "models": model_status(),
        "eta_per_hour": eta,
        "recommend": recommend(gpus, ram_gb, cuda),
    }
