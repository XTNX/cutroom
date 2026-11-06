"""Local word-level ASR → ElevenLabs-Scribe-shaped JSON. No network, no API keys.

Drop-in for video-use's transcribe.py: writes <edit_dir>/transcripts/<stem>.json in the
schema Scribe returns, so pack_transcripts.py, render.py --build-subtitles and the editor
brief work unchanged:

    {"language_code": "ru", "language_probability": 0.99, "text": "...",
     "words": [{"text": "Итак,", "start": 6.66, "end": 7.54, "type": "word",
                "speaker_id": "speaker_0", "logprob": -0.001, "probability": 0.999},
               {"text": " ", "start": 7.54, "end": 7.54, "type": "spacing", "speaker_id": "speaker_0", "logprob": 0.0},
               {"text": "(смех)", "start": .., "end": .., "type": "audio_event", ...}]}

`probability` is a Cutroom extension (Scribe has only `logprob`); the UI uses it to flag
low-confidence words around cuts. Everything else matches Scribe field for field.

Settings that keep the editorial signal (video-use Hard Rule 8):
    word_timestamps=True, condition_on_previous_text=False, temperature=0,
    vad_filter=False (VAD eats short pauses and fillers),
    initial_prompt with fillers and repeats, so Whisper doesn't clean them up.

Cache (Hard Rule 9): key = fast hash of the source + engine + model + params. The active
transcript sits at transcripts/<stem>.json; every keyed result is kept under
cache/transcripts/<stem>.<key>.json, so switching model and back costs nothing.

Usage:
    python helpers/transcribe_whisper.py <media> --edit-dir <edit> [--audio <16k.wav>]
        [--engine faster-whisper|gigaam] [--model large-v3] [--language ru] [--device auto]
        [--start 0 --duration 600 --out <path>]   # benchmark slices, bypasses the cache
Progress is printed as `PROGRESS <0..1>` lines on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FILLER_PROMPT = {
    "ru": "Э-э, ну, это, ммм, то есть, как бы… Ну вот, э-э, смотрите. Мм, да, да. Значит, э-э, мы-мы это…",
    "en": "Umm, uh, so, like, you know… I mean, uh, we-we did, hmm, yeah.",
}
EVENT_RE = re.compile(r"^[\(\[\*].*[\)\]\*]$")
PARAMS_VERSION = "1"  # bump when decoding params change → new cache key


def _add_cuda_dlls() -> None:
    try:
        import nvidia  # type: ignore
    except ImportError:
        return
    for root in nvidia.__path__:
        for b in Path(root).glob("*/bin"):
            os.add_dll_directory(str(b))
            os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def fast_hash(path: Path, chunk: int = 4 << 20) -> str:
    st = path.stat()
    h = hashlib.blake2b(digest_size=12)
    h.update(str(st.st_size).encode())
    with open(path, "rb") as f:
        for off in (0, max(0, st.st_size // 2 - chunk // 2), max(0, st.st_size - chunk)):
            f.seek(off)
            h.update(f.read(chunk))
    return h.hexdigest()


def cache_key(media: Path, engine: str, model: str, language: str, audio_track: int) -> str:
    raw = f"{fast_hash(media)}|{engine}|{model}|{language or 'auto'}|t{audio_track}|p{PARAMS_VERSION}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def extract_audio(src: Path, dest: Path, track: int, start: float | None, duration: float | None) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-map", f"0:a:{track}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)]
    subprocess.run(cmd, check=True)


def wav_duration(path: Path) -> float:
    import wave
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def load_wav(path: Path):
    import wave

    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"ожидается 16 кГц mono s16: {path}")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def progress(x: float) -> None:
    print(f"PROGRESS {max(0.0, min(1.0, x)):.4f}", flush=True)


# ---------------------------------------------------------------- engines


def run_faster_whisper(wav: Path, model_name: str, language: str | None, device: str) -> tuple[list[dict], str, float, dict]:
    _add_cuda_dlls()
    from faster_whisper import WhisperModel

    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    compute = "float16" if device == "cuda" else "int8"
    threads = 0 if device == "cuda" else max(1, (os.cpu_count() or 4) - 1)
    if device == "cpu":
        print("WARN CPU-режим: расшифровка будет медленной", flush=True)
    model = WhisperModel(model_name, device=device, compute_type=compute, cpu_threads=threads)
    total = wav_duration(wav)
    lang = language or None
    prompt = FILLER_PROMPT.get(lang or "ru", FILLER_PROMPT["ru"])
    segments, info = model.transcribe(
        load_wav(wav),  # numpy, not a path: skips PyAV (its API drifted from faster-whisper's)
        language=lang,
        word_timestamps=True,
        condition_on_previous_text=False,
        temperature=0.0,
        vad_filter=False,
        beam_size=5,
        initial_prompt=prompt,
    )
    if not lang:
        prompt = FILLER_PROMPT.get(info.language, FILLER_PROMPT["en"])
    raw: list[dict] = []
    for seg in segments:
        for w in seg.words or []:
            raw.append({"text": w.word.strip(), "start": float(w.start), "end": float(w.end),
                        "probability": float(w.probability)})
        progress(seg.end / total if total else 0)
    return raw, info.language, float(info.language_probability), {"device": device, "compute": compute}


GIGAAM_HF = {"v3_e2e_rnnt": "e2e_rnnt", "v3_rnnt": "rnnt", "v3_ctc": "ctc", "v3_e2e_ctc": "e2e_ctc"}


def load_gigaam(name: str, device: str):
    """Sber's CDN (cdn.chatwm.opensmodel.sberdevices.ru) currently serves an expired TLS
    certificate. We never disable TLS checks; instead we try the CDN first and fall back to the
    official Hugging Face mirror ai-sage/GigaAM-v3 (branch per variant), building the model from
    the gigaam package's own classes — the mirror's modeling_gigaam.py is never executed."""
    import gigaam  # type: ignore
    from huggingface_hub import hf_hub_download
    local = False
    if name in GIGAAM_HF:
        try:  # already mirrored → don't touch the network at all
            hf_hub_download("ai-sage/GigaAM-v3", "pytorch_model.bin", revision=GIGAAM_HF[name], local_files_only=True)
            local = True
        except Exception:
            pass
    if not local:
        try:
            return gigaam.load_model(name, device=device)
        except Exception as e:  # noqa: BLE001
            if name not in GIGAAM_HF:
                raise
            print(f"WARN CDN GigaAM недоступен ({type(e).__name__}); беру веса с Hugging Face ai-sage/GigaAM-v3", flush=True)
    import torch  # type: ignore
    from omegaconf import OmegaConf  # type: ignore
    rev = GIGAAM_HF[name]
    if local:
        import os as _os
        _os.environ["HF_HUB_OFFLINE"] = "1"
    cfg_raw = json.loads(Path(hf_hub_download("ai-sage/GigaAM-v3", "config.json", revision=rev)).read_text(encoding="utf-8"))
    targets = {
        "modeling_gigaam.FeatureExtractor": "gigaam.preprocess.FeatureExtractor",
        "modeling_gigaam.ConformerEncoder": "gigaam.encoder.ConformerEncoder",
        "modeling_gigaam.RNNTHead": "gigaam.decoder.RNNTHead",
        "modeling_gigaam.CTCHead": "gigaam.decoder.CTCHead",
        "modeling_gigaam.RNNTGreedyDecoding": "gigaam.decoding.RNNTGreedyDecoding",
        "modeling_gigaam.CTCGreedyDecoding": "gigaam.decoding.CTCGreedyDecoding",
    }
    text = json.dumps(cfg_raw["cfg"]["model"]["cfg"])
    for a, b in targets.items():
        text = text.replace(a, b)
    if "modeling_gigaam." in text:
        raise SystemExit("неизвестный класс в конфиге GigaAM: " + text[text.index("modeling_gigaam."):][:60])
    cfg = OmegaConf.create(json.loads(text))
    if cfg.decoding.get("model_path"):  # e2e variants: sentencepiece; plain ones: char vocabulary in cfg
        cfg.decoding.model_path = hf_hub_download("ai-sage/GigaAM-v3", "tokenizer.model", revision=rev)
    weights = hf_hub_download("ai-sage/GigaAM-v3", "pytorch_model.bin", revision=rev)
    sd = torch.load(weights, map_location="cpu", weights_only=True)
    sd = {k.removeprefix("model."): v for k, v in sd.items()}
    model = gigaam.GigaAMASR(cfg)
    model.load_state_dict(sd)
    model = model.to(device).eval()
    if device == "cuda":
        model.encoder = model.encoder.half()
    return model


def run_gigaam(wav: Path, model_name: str, language: str | None, device: str) -> tuple[list[dict], str, float, dict]:
    """GigaAM (Sber, MIT) adapter. Needs the optional `gigaam` package (torch).
    GigaAM v3 gives word timestamps via its CTC/RNNT alignment."""
    try:
        import gigaam  # type: ignore
        import torch  # type: ignore
    except ImportError:
        sys.exit("GigaAM не установлен: нужны torch + gigaam (см. SECURITY_AUDIT.md)")
    import wave

    import numpy as np

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_gigaam(model_name if model_name.startswith(("v", "multi")) else "v3_e2e_rnnt", device)
    audio = (load_wav(wav) * 32768).astype(np.int16)
    sr = 16000
    total = len(audio) / sr
    # GigaAM takes ≤25 s per call; its long-form mode needs pyannote + an HF token, so we cut
    # ourselves: windows ≤22 s, each ending at the quietest 10 ms frame of its last 4 s.
    frame = sr // 100
    env = np.abs(audio[: len(audio) // frame * frame].reshape(-1, frame).astype(np.int32)).max(axis=1)
    cuts, pos = [0], 0
    while pos + 22 * sr < len(audio):
        lo, hi = (pos + 18 * sr) // frame, (pos + 22 * sr) // frame
        q = lo + int(np.argmin(env[lo:hi]))
        pos = q * frame
        cuts.append(pos)
    cuts.append(len(audio))
    raw: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        for a, b in zip(cuts, cuts[1:]):
            if b - a < sr // 5:
                continue
            chunk = Path(td) / "c.wav"
            with wave.open(str(chunk), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
                w.writeframes(audio[a:b].tobytes())
            res = model.transcribe(str(chunk), word_timestamps=True)
            off = a / sr
            for w in res.words or []:
                raw.append({"text": w.text, "start": off + float(w.start), "end": off + float(w.end), "probability": 1.0})
            progress(b / sr / total if total else 0)
    return raw, "ru", 1.0, {"device": device, "compute": "torch-fp16"}


def _norm(t: str) -> str:
    return re.sub(r"[^\w-]", "", t.lower().replace("ё", "е"))


def cross_check(raw: list[dict], edit_dir: Path, name: str) -> str | None:
    """GigaAM has no per-word confidence. Borrow it from a cached Whisper transcript of the
    same source: words both engines agree on take Whisper's probability, words where they
    disagree get 0.35 — engine disagreement is exactly what deserves a human look at a cut.
    Mutates raw[*]["probability"]; returns e.g. "whisper:large-v3" or None."""
    import difflib
    best = None
    for f in sorted((edit_dir / "cache" / "transcripts").glob(f"{name}.*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta = d.get("cutroom") or {}
        if meta.get("engine") != "faster-whisper":
            continue
        rank = {"large-v3": 0, "large-v3-turbo": 1}.get(meta.get("model"), 2)
        if best is None or rank < best[0]:
            best = (rank, meta.get("model"), [w for w in d["words"] if w.get("type") == "word"])
    if not best:
        return None
    ww = best[2]
    a = [_norm(w["text"]) for w in raw]
    b = [_norm(w["text"]) for w in ww]
    for w in raw:
        w["probability"] = 0.35
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            g, h = raw[blk.a + k], ww[blk.b + k]
            # same word but placed >1 s apart is a coincidence, not agreement
            if abs(g["start"] - h["start"]) <= 1.0:
                g["probability"] = min(0.99, float(h.get("probability", 0.9)))
    return f"whisper:{best[1]}"


# ---------------------------------------------------------------- Scribe shaping


def to_scribe(raw: list[dict], language: str, lang_prob: float) -> dict:
    words: list[dict] = []
    prev_end: float | None = None
    for w in raw:
        text = w["text"]
        if not text:
            continue
        start, end = round(w["start"], 3), round(max(w["end"], w["start"]), 3)
        if prev_end is not None:
            # Scribe puts a spacing token between every pair of tokens; its span is the gap.
            words.append({"text": " ", "start": prev_end, "end": max(prev_end, start),
                          "type": "spacing", "speaker_id": "speaker_0", "logprob": 0.0})
        p = max(1e-6, min(1.0, w.get("probability", 1.0)))
        words.append({
            "text": text, "start": start, "end": end,
            "type": "audio_event" if EVENT_RE.match(text) else "word",
            "speaker_id": "speaker_0",
            "logprob": round(math.log(p), 4),
            "probability": round(p, 4),
        })
        prev_end = end
    text = " ".join(w["text"] for w in words if w["type"] != "spacing")
    return {"language_code": language, "language_probability": round(lang_prob, 4), "text": text, "words": words}


def main() -> None:
    ap = argparse.ArgumentParser(description="Local ASR → Scribe-shaped word-level JSON")
    ap.add_argument("media", type=Path)
    ap.add_argument("--edit-dir", type=Path, default=None)
    ap.add_argument("--audio", type=Path, default=None, help="Pre-extracted 16 kHz mono wav")
    ap.add_argument("--audio-track", type=int, default=0)
    ap.add_argument("--engine", default="faster-whisper", choices=["faster-whisper", "gigaam"])
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--language", default="ru", help="'' or 'auto' to detect from the first 30 s")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--name", default=None, help="Transcript name (default: media stem)")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--out", type=Path, default=None, help="Explicit output; bypasses the cache")
    args = ap.parse_args()

    media = args.media.resolve()
    if not media.exists():
        sys.exit(f"нет файла: {media}")
    language = "" if args.language in ("", "auto") else args.language
    edit_dir = (args.edit_dir or media.parent / "edit").resolve()
    name = args.name or media.stem

    out_path = args.out
    key = None
    if out_path is None:
        key = cache_key(media, args.engine, args.model, language, args.audio_track)
        active = edit_dir / "transcripts" / f"{name}.json"
        cache_dir = edit_dir / "cache" / "transcripts"
        cached = cache_dir / f"{name}.{key}.json"
        if cached.exists():
            active.parent.mkdir(parents=True, exist_ok=True)
            if not active.exists() or active.read_bytes() != cached.read_bytes():
                active.write_bytes(cached.read_bytes())
            print(f"CACHED {key}", flush=True)
            progress(1.0)
            return
        out_path = cached

    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        wav = args.audio
        if wav is None or args.start is not None or args.duration is not None:
            wav = Path(td) / "a.wav"
            extract_audio(media, wav, args.audio_track, args.start, args.duration)
        audio_dur = wav_duration(wav)
        engine = run_faster_whisper if args.engine == "faster-whisper" else run_gigaam
        raw, lang, lang_prob, runtime = engine(wav, args.model, language or None, args.device)

    conf_src = None
    if args.engine == "gigaam" and key is not None:
        conf_src = cross_check(raw, edit_dir, name)
    payload = to_scribe(raw, lang, lang_prob)
    elapsed = time.time() - t0
    payload["cutroom"] = {
        "engine": args.engine, "model": args.model, "cache_key": key, "source": str(media),
        "audio_duration": round(audio_dur, 3), "elapsed": round(elapsed, 2),
        "rtf": round(elapsed / audio_dur, 4) if audio_dur else None, **runtime,
        "confidence_source": conf_src,
        "params": {"word_timestamps": True, "condition_on_previous_text": False, "temperature": 0,
                   "vad_filter": False, "beam_size": 5, "initial_prompt": True},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii: pack_transcripts.py reads with the locale encoding (cp1251 on Windows)
    out_path.write_text(json.dumps(payload, ensure_ascii=True, indent=1), encoding="utf-8")
    if key is not None:
        active = edit_dir / "transcripts" / f"{name}.json"
        active.parent.mkdir(parents=True, exist_ok=True)
        active.write_bytes(out_path.read_bytes())
    n = sum(1 for w in payload["words"] if w["type"] == "word")
    progress(1.0)
    print(f"DONE words={n} rtf={payload['cutroom']['rtf']} lang={lang}", flush=True)


if __name__ == "__main__":
    main()
