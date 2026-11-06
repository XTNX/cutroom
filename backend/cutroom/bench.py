"""ASR benchmark on our own material (stage 2): faster-whisper large-v3 / large-v3-turbo / GigaAM.

Metrics per engine:
  - speed: RTF on this machine (model load included — that is what a user waits for)
  - words, fillers, filler share
  - WER against a human reference  edit/bench/reference.txt   (if present)
  - otherwise "disagreement" against large-v3 (NOT a WER — no ground truth)
  - word-boundary error on manually marked words  edit/bench/boundaries.json  (if present):
        [{"word": "рынок", "start": 12.34, "end": 12.80}, ...]  times relative to the slice
Writes edit/asr_benchmark.md and edit/bench/result.json.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import time
from pathlib import Path

from .config import CUTROOM_HELPERS, PYTHON, subprocess_env
from .jobs import Job

ENGINES = [("faster-whisper", "large-v3"), ("faster-whisper", "large-v3-turbo"),
           ("gigaam", "v3_e2e_rnnt"), ("gigaam", "v3_rnnt")]
FILLERS = re.compile(r"^(э+|ээ+|эм+|мм+|м|хм+|а-а+|ну|вот|типа|короче|значит)$")


def norm(text: str) -> list[str]:
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[^\w\s-]", " ", t)
    t = re.sub(r"(?<!\w)-|-(?!\w)", " ", t)
    return [w for w in t.split() if w]


def wer(ref: list[str], hyp: list[str]) -> float:
    if not ref:
        return 0.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def filler_count(words: list[str]) -> int:
    return sum(1 for w in words if FILLERS.match(w.replace("-", "")) or re.fullmatch(r"э+-?э*|м+-?м*", w))


def boundary_error(marks: list[dict], words: list[dict]) -> float | None:
    """Mean |Δstart|+|Δend|/2 in ms for marked words, matched to the nearest same-text word."""
    if not marks:
        return None
    errs = []
    for m in marks:
        target = norm(m["word"])
        cands = [w for w in words if w["type"] == "word" and norm(w["text"]) == target]
        if not cands:
            continue
        w = min(cands, key=lambda c: abs(c["start"] - m["start"]))
        errs.append((abs(w["start"] - m["start"]) + abs(w["end"] - m["end"])) / 2 * 1000)
    return round(sum(errs) / len(errs), 1) if errs else None


def run_bench(project, j: Job, source_ids: list[str], seconds: float) -> dict:
    bench = project.edit / "bench"
    bench.mkdir(parents=True, exist_ok=True)
    # slice: first `seconds` of audio across the chosen sources (in order)
    slices, left = [], seconds
    for sid in source_ids:
        s = project.sources[sid]
        d = (s["probe"] or {}).get("duration", 0)
        if left <= 0:
            break
        take = min(d, left)
        slices.append((s, take))
        left -= take
    total = sum(t for _, t in slices)
    ref_path = bench / "reference.txt"
    ref = norm(ref_path.read_text(encoding="utf-8")) if ref_path.exists() else None
    marks_path = bench / "boundaries.json"
    marks = json.loads(marks_path.read_text(encoding="utf-8")) if marks_path.exists() else []

    rows = []
    steps = len(ENGINES) * len(slices)
    step = 0
    for engine, model in ENGINES:
        if engine == "gigaam" and importlib.util.find_spec("gigaam") is None:
            rows.append({"engine": engine, "model": model, "skipped": "не установлен (нужны torch + gigaam)"})
            step += len(slices)
            continue
        all_words: list[dict] = []
        elapsed = 0.0
        err = None
        for s, take in slices:
            out = bench / f"{engine}_{model}_{s['id']}.json"
            if not out.exists():
                t0 = time.time()
                cmd = [str(PYTHON), str(CUTROOM_HELPERS / "transcribe_whisper.py"), s["path"],
                       "--engine", engine, "--model", model, "--language", "ru",
                       "--start", "0", "--duration", f"{take:.3f}", "--out", str(out)]
                j.log.append(f"{engine} {model} · {s['id']} ({take:.0f} с)")
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env=subprocess_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if p.returncode != 0:
                    err = (p.stdout + p.stderr).strip().splitlines()[-1:] or ["ошибка"]
                    err = err[0][:300]
                    break
                elapsed += time.time() - t0
            else:
                elapsed += json.loads(out.read_text(encoding="utf-8")).get("cutroom", {}).get("elapsed", 0)
            all_words += json.loads(out.read_text(encoding="utf-8"))["words"]
            step += 1
            j.progress = step / steps
            if j._cancel:
                return {}
        if err:
            rows.append({"engine": engine, "model": model, "error": err})
            continue
        toks = norm(" ".join(w["text"] for w in all_words if w["type"] == "word"))
        f = filler_count(toks)
        rows.append({
            "engine": engine, "model": model, "words": len(toks), "fillers": f,
            "filler_share": round(f / len(toks) * 100, 2) if toks else 0,
            "rtf": round(elapsed / total, 4) if total else None,
            "wer": round(wer(ref, toks) * 100, 1) if ref else None,
            "boundary_ms": boundary_error(marks, all_words),
            "_tokens": toks,
        })
    base = next((r for r in rows if r.get("model") == "large-v3" and "_tokens" in r), None)
    for r in rows:
        if "_tokens" in r:
            r["disagree_vs_large_v3"] = round(wer(base["_tokens"], r["_tokens"]) * 100, 1) if base and r is not base else None
    max_f = max((r.get("fillers", 0) for r in rows), default=0)
    ref_f = filler_count(ref) if ref else None
    for r in rows:
        if "_tokens" in r:
            denom = ref_f if ref_f else max_f
            r["filler_retention"] = round(r["fillers"] / denom * 100, 1) if denom else None
            del r["_tokens"]
    result = {
        "when": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(total, 1),
        "sources": [{"id": s["id"], "seconds": round(t, 1)} for s, t in slices],
        "has_reference": ref is not None, "has_boundaries": bool(marks), "rows": rows,
    }
    (bench / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (project.edit / "asr_benchmark.md").write_text(to_markdown(result), encoding="utf-8")
    return result


def to_markdown(r: dict) -> str:
    L = [f"# Замер ASR — {r['when']}", "",
         f"Материал: {r['seconds']} с из " + ", ".join(f"{s['id']} ({s['seconds']} с)" for s in r["sources"]) + ".", ""]
    L.append("| Движок | Модель | Скорость (RTF) | Час аудио | Слов | Филлеров | Доля филлеров | Сохранено филлеров | WER | Расхождение с large-v3 | Ошибка границ |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for x in r["rows"]:
        if x.get("skipped") or x.get("error"):
            L.append(f"| {x['engine']} | {x['model']} | — | — | — | — | — | — | — | — | {x.get('skipped') or 'ошибка: ' + x['error']} |")
            continue
        L.append("| {engine} | {model} | {rtf} | {mph} мин | {words} | {fillers} | {fs} % | {fr} | {wer} | {dis} | {bnd} |".format(
            engine=x["engine"], model=x["model"], rtf=x["rtf"], mph=round((x["rtf"] or 0) * 60, 1), words=x["words"],
            fillers=x["fillers"], fs=x["filler_share"],
            fr=f"{x['filler_retention']} %" if x.get("filler_retention") is not None else "—",
            wer=f"{x['wer']} %" if x.get("wer") is not None else "[NEED: эталон]",
            dis=f"{x['disagree_vs_large_v3']} %" if x.get("disagree_vs_large_v3") is not None else "база",
            bnd=f"{x['boundary_ms']} мс" if x.get("boundary_ms") is not None else "[NEED: 20 слов]"))
    L += ["", "Примечания:",
          "- RTF включает загрузку модели — столько реально ждёт пользователь.",
          "- «Сохранено филлеров» без эталона считается от максимума среди движков, с эталоном — от числа филлеров в эталоне.",
          "- WER требует ручной эталонной расшифровки: положите текст в `edit/bench/reference.txt`."
          if not r["has_reference"] else "- WER посчитан по `edit/bench/reference.txt`.",
          "- Ошибка границ требует 20 размеченных вручную слов в `edit/bench/boundaries.json`."
          if not r["has_boundaries"] else "- Ошибка границ посчитана по `edit/bench/boundaries.json`.",
          "- Основной движок выбирает человек по этой таблице."]
    return "\n".join(L) + "\n"
