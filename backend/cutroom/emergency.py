"""Emergency export: get the film out when the model's tokens ran out (or the agent is down).

Nothing here calls a model. In order, the first that works:
  1. quick mode — the full 1080p build from the current document (render_block.py per missing
     segment + stream-copy assembly + loudnorm); cards that the agent never finished are left out;
  2. quick mode — a copy of the last assembled preview (720p) if the 1080p build fails;
  3. full mode — the verified render_cutroom.py preview of the current EDL (rendered if missing
     or stale), copied to export/.
A text file next to the video lists what was left out. Nothing goes to the knowledge base
(only «Завершить проект» on a real final export does that).
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .jobs import Job


def _out_dir(project) -> Path:
    d = project.edit / "export"
    d.mkdir(exist_ok=True)
    return d


def _skipped_quick(project) -> list[str]:
    from .quick import model
    d = model.current(project) or {"tracks": {"overlays": []}}
    fps = model.fps_of(d) if d.get("rate") else 25
    return [f"карточка «{(o.get('spec') or {}).get('title') or o.get('hint') or o['id']}» на {model.fmt(o['start'] / fps)}: "
            f"{'ошибка: ' + str(o.get('error'))[:120] if o.get('status') == 'error' else 'не готова (' + str(o.get('status')) + ')'}"
            for o in d["tracks"]["overlays"] if o.get("status") != "ready"]


def _finish(project, src: Path, label: str, version, stats: dict, notes: list[str], mode: str, queue=None) -> dict:
    stamp = time.strftime("%Y%m%d_%H%M")
    out = _out_dir(project) / f"{project.dir.name}_экстренный_v{version}_{stamp}{src.suffix}"
    if src.resolve() != out.resolve():
        shutil.copy2(src, out)
    txt = out.with_suffix(".txt")
    txt.write_text("\n".join([f"Экстренный экспорт без ИИ — {time.strftime('%Y-%m-%d %H:%M')}", f"Источник: {label}", f"Файл: {out.name}", "",
                              "Не вошло:" if notes else "Всё, что было на таймлайне, вошло.", *[f"- {n}" for n in notes]]), encoding="utf-8")
    res = {**stats, "file": str(out), "source": label, "skipped": notes, "version": version, "emergency": True,
           "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    (_out_dir(project) / "last_emergency.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    # an emergency export is never a reference: nothing goes to the knowledge base
    return res


def export(project, job: Job | None = None, queue=None, log=print) -> dict:
    from .quick import build, model
    from .quick.routes import get_mode

    def say(m: str):
        log(m)
        if job is not None:
            job.log.append(m)

    errors = []
    doc = model.current(project)
    if doc and doc["tracks"]["video"] and (get_mode(project) == "quick" or not _has_full(project)):
        try:
            say("1080p из блоков (без ИИ)…")
            stats = build.run_sync(project, "final", log=say)
            # this 1080p build is an emergency one: it never counts as the project's final render
            lf = project.edit / "quick" / "logs" / "last_final.json"
            lf.write_text(json.dumps({**stats, "emergency": True}, ensure_ascii=False, indent=1), encoding="utf-8")
            return _finish(project, Path(stats["file"]), "быстрый режим, сборка 1080p", stats["version"], stats, _skipped_quick(project), "quick", queue)
        except Exception as e:  # noqa: BLE001
            errors.append(f"сборка 1080p: {e}")
            say(f"не вышло: {e}")
        prev = (build.last(project, "draft") or {}).get("file")
        prev = prev if prev and Path(prev).is_file() else build.newest_preview(project)
        if prev:
            say("беру последнее собранное превью 720p")
            v = (build.last(project, "draft") or {}).get("version") or doc["meta"].get("version_id")
            return _finish(project, Path(prev), "быстрый режим, последнее собранное превью 720p", v,
                           build.last(project, "draft") or {}, _skipped_quick(project) + errors, "quick", queue)
    if _has_full(project):
        from . import edl
        from .stages import run_render_preview
        e = edl.current(project)
        vid = e["cutroom"]["version_id"]
        prev = project.edit / "preview.mp4"
        fresh = prev.exists() and project.stats().get("preview_version") == vid
        if not fresh:
            try:
                say("рендер превью полного режима (render_cutroom.py)…")
                run_render_preview(project, job or Job(kind="preview", label="превью", lane="render"))
            except Exception as ex:  # noqa: BLE001
                errors.append(f"рендер превью: {ex}")
                say(f"не вышло: {ex}")
        if prev.exists():
            skipped = [f"инфографика {s.get('id')}: не готова" for s in e.get("slots", []) if s.get("status") != "ready"]
            if not fresh and errors:
                skipped.append("превью от более ранней версии монтажа")
            return _finish(project, prev, "полный режим, превью render_cutroom.py", vid,
                           {"duration": e.get("total_duration_s")}, skipped + errors, "full", queue)
    raise RuntimeError("Экспортировать нечего: нет ни монтажа быстрого режима, ни превью полного. " + " · ".join(errors))


def _has_full(project) -> bool:
    from . import edl
    try:
        e = edl.current(project)
        return bool(e and e.get("ranges")) and edl.strategy_approved(project)
    except Exception:  # noqa: BLE001
        return False


def last(project) -> dict | None:
    try:
        return json.loads((_out_dir(project) / "last_emergency.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
