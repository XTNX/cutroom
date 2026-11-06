"""Stage 4 orchestration: strategy → (human gate) → editor → EDL → background render.py --preview."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import agent, edl
from .config import CUTROOM_HELPERS, PYTHON, subprocess_env
from .jobs import Job
from .sync import load_sync


def _sources_block(project) -> str:
    lines = []
    for s in sorted(project.sources.values(), key=lambda x: x["id"]):
        if s["duplicate_of"] or s["excluded"]:
            continue
        pr = s["probe"] or {}
        v = pr.get("video") or {}
        has_tr = project.paths(s["id"])["transcript"].exists()
        lines.append(f"{s['id']}: {pr.get('duration', 0):.1f} s, "
                     f"{v.get('display_width', '?')}x{v.get('display_height', '?')}"
                     f"{' HDR' if v.get('hdr') else ''}{'' if has_tr else ', без расшифровки'}")
    return "\n".join(lines)


def _takes_block(project) -> str:
    sync = load_sync(project)
    if not sync.get("groups"):
        return "Синхронизация ракурсов не выполнялась."
    out = []
    for g in sync["groups"]:
        ms = ", ".join(f"{m['id']} ({m['offset']:+.2f} с)" for m in g["members"])
        out.append(f"{g['id']}: опорный {g['ref']}; ракурсы: {ms}")
    return "\n".join(out)


def _packed(project) -> str:
    p = project.edit / "takes_packed.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def strategy_prompt(project, prompt: str, chips: dict, feedback: str | None, prev: dict | None) -> str:
    mem = project.project_memory()
    parts = [
        "Предложи стратегию монтажа.",
        agent.wrap_data("запрос человека", prompt or "(пусто — предложи разумный вариант по материалу)"),
        agent.wrap_data("подсказки-чипы", json.dumps(chips, ensure_ascii=False)),
        agent.wrap_data("источники", _sources_block(project)),
        agent.wrap_data("дубли и ракурсы", _takes_block(project)),
    ]
    if mem.get("summary"):
        parts.append(agent.wrap_data("память проекта", f"{mem.get('last_title')}: {mem['summary']}"))
    if prev and feedback:
        parts.append(agent.wrap_data("прошлая версия стратегии", prev.get("text", "")))
        parts.append(agent.wrap_data("правки человека", feedback))
        parts.append("Перепиши стратегию с учётом правок человека.")
    parts.append(agent.wrap_data("takes_packed.md", _packed(project)))
    return "\n\n".join(parts)


def run_strategy(project, job: Job, prompt: str, chips: dict, feedback: str | None = None) -> dict:
    prev = edl.load_strategy(project)
    job.log.append("агент стратегии думает…")
    s: agent.Strategy = agent.run("strategy", strategy_prompt(project, prompt, chips, feedback, prev),
                                  log_dir=project.edit)  # type: ignore[assignment]
    data = s.model_dump()
    text = "\n".join(s.sentences)
    out = {
        "status": "draft", "text": text, "data": data, "prompt": prompt, "chips": chips,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"), "revision": (prev or {}).get("revision", 0) + 1,
        "approved_hash": None, "approved_at": None, "history": ((prev or {}).get("history") or [])[-5:] +
        ([{"text": prev["text"], "status": prev["status"]}] if prev else []),
    }
    return edl.save_strategy(project, out)


def ask(project, question: str) -> dict:
    s = edl.load_strategy(project) or {}
    prompt = "\n\n".join([
        "Ответь на вопрос человека о стратегии монтажа. Если ответ подразумевает правку стратегии — "
        "предложи её одной-двумя фразами в suggested_edit.",
        agent.wrap_data("текущая стратегия", s.get("text", "(нет)")),
        agent.wrap_data("вопрос", question),
        agent.wrap_data("takes_packed.md", _packed(project)),
    ])
    a: agent.Answer = agent.run("ask", prompt, log_dir=project.edit)  # type: ignore[assignment]
    return a.model_dump()


def run_editor(project, job: Job) -> dict:
    s = edl.require_gate(project)
    d = s["data"]
    slips = "\n".join(f"{x['source']} @ {x['time']:.2f}: {x['text']}" for x in d.get("slips", [])) or "нет"
    prompt = "\n\n".join([
        "Собери EDL по утверждённой стратегии.",
        agent.wrap_data("утверждённая стратегия", s["text"]),
        agent.wrap_data("биты", json.dumps(d.get("beats", []), ensure_ascii=False)),
        agent.wrap_data("инфографика", json.dumps(d.get("graphics", []), ensure_ascii=False)),
        agent.wrap_data("целевая длина, с", str(d.get("target_seconds"))),
        agent.wrap_data("оговорки, которых избегать", slips),
        agent.wrap_data("дубли и ракурсы", _takes_block(project)),
        agent.wrap_data("takes_packed.md", _packed(project)),
    ])
    job.log.append("агент-редактор собирает EDL…")
    out: agent.EditorOut = agent.run("editor", prompt, timeout=900, log_dir=project.edit)  # type: ignore[assignment]
    e = edl.normalize(project, [r.model_dump() for r in out.ranges], [x.model_dump() for x in out.slots])
    e["cutroom"]["strategy_hash"] = s["approved_hash"]
    e["cutroom"]["agent_check"] = out.check
    edl.carry_over(e, edl.current(project))
    vid = edl.save_version(project, e, "agent", f"редактор: {len(e['ranges'])} диапазонов, {e['total_duration_s']:.1f} с")
    job.note = f"версия {vid}: {e['total_duration_s']:.1f} с"
    return e


def _packed_sections(project, sids: set[str]) -> str:
    """Only the takes_packed.md sections of the given sources (cheaper, sharper context)."""
    text = _packed(project)
    out, keep = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            keep = line[3:].split()[0] in sids
        if keep:
            out.append(line)
    return "\n".join(out) or text


def edit_fragment(project, job: Job, a: int, b: int, request: str, notes: list[str] | None = None) -> dict:
    """Agent rewrites ranges a..b (inclusive) of the current EDL; everything else stays."""
    s = edl.require_gate(project)
    cur = edl.current(project)
    if not cur:
        raise RuntimeError("EDL ещё нет")
    rs = cur["ranges"]
    a, b = max(0, min(a, b)), min(len(rs) - 1, max(a, b))
    sync = load_sync(project)
    sids = {r["source"] for r in rs[a:b + 1]}
    for g in sync.get("groups", []):
        ids = {m["id"] for m in g["members"]}
        if ids & sids:
            sids |= ids
    lines = [f"{i + 1}. [{r.get('beat', '')}] {r['source']} {r['start']:.2f}–{r['end']:.2f}"
             f"{' (ракурс ' + r['angle'] + ')' if r.get('angle') else ''}: «{r.get('quote', '')[:120]}»"
             + ("   ← ВЫДЕЛЕНО" if a <= i <= b else "") for i, r in enumerate(rs)]
    prompt = "\n\n".join([
        f"Перепиши только фрагменты {a + 1}–{b + 1}.",
        agent.wrap_data("утверждённая стратегия", s["text"]),
        agent.wrap_data("текущий монтаж", "\n".join(lines)),
        agent.wrap_data("просьба человека", request),
        *([agent.wrap_data("заметки-маркеры на этом участке", "\n".join(notes))] if notes else []),
        agent.wrap_data("дубли и ракурсы", _takes_block(project)),
        agent.wrap_data("takes_packed.md (источники этого участка)", _packed_sections(project, sids)),
    ])
    job.log.append(f"агент переделывает фрагменты {a + 1}–{b + 1}…")
    out: agent.FragmentOut = agent.run("fragment", prompt, timeout=600, log_dir=project.edit)  # type: ignore[assignment]
    before = sum(r["end"] - r["start"] for r in rs[a:b + 1])
    new = [r.model_dump() for r in out.ranges]
    ranges = [dict(r) for r in rs[:a]] + new + [dict(r) for r in rs[b + 1:]]
    e = edl.normalize(project, ranges, cur.get("slots", []))
    e["cutroom"]["strategy_hash"] = cur["cutroom"].get("strategy_hash")
    e["cutroom"]["agent_check"] = out.note
    edl.carry_over(e, cur)
    after = sum(r["end"] - r["start"] for r in e["ranges"][a:a + len(new)])
    note = f"агент, фрагменты {a + 1}–{b + 1}: {request[:60]} ({before:.1f} → {after:.1f} с)"
    vid = edl.save_version(project, e, "agent", note)
    job.note = f"версия {vid}: {out.note[:120]}"
    return e


def render_preview_cmd(project) -> list[str]:
    edl.require_gate(project)
    e = edl.current(project)
    if not e:
        raise RuntimeError("EDL ещё нет")
    moved = project.relink()
    used = {r["source"] for r in e["ranges"]} | {r["angle"] for r in e["ranges"] if r.get("angle")}
    gone = project.missing_sources(used)
    if gone:
        names = ", ".join(Path(project.sources[s]["path"]).name for s in gone)
        raise RuntimeError(f"Не найдены исходники: {names}. Их переместили или удалили? Верните файлы в папку проекта "
                           f"(можно в любую подпапку) и нажмите «Повторить» — Cutroom найдёт их сам.")
    if moved:
        print("relinked:", ", ".join(moved))
    p = edl.resolve_for_render(project, e)
    return [str(PYTHON), str(CUTROOM_HELPERS / "render_cutroom.py"), str(p),
            "-o", str(project.edit / "preview.part.mp4"), "--preview"]


def run_render_preview(project, job: Job) -> None:
    """render_cutroom.py as a cancellable child; progress from its SEG k/n and STEP lines."""
    import re
    cmd = render_preview_cmd(project)
    e = edl.current(project)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env=subprocess_env(), cwd=str(project.edit),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    job._proc = proc
    assert proc.stdout
    steps = {"concat": 0.86, "audio": 0.9, "composite": 0.93, "loudnorm": 0.96}
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        job.log.append(line[:300])
        m = re.match(r"SEG (\d+)/(\d+)", line)
        if m:
            job.progress = 0.85 * (int(m.group(1)) - 1) / int(m.group(2))
        elif line.startswith("STEP "):
            job.progress = steps.get(line.split()[1], job.progress)
    rc = proc.wait()
    job._proc = None
    if job._cancel:
        return None
    if rc != 0:
        raise RuntimeError(f"рендер завершился с кодом {rc}: {' | '.join(list(job.log)[-3:])}")
    part = project.edit / "preview.part.mp4"
    prev = project.edit / "preview.mp4"
    if prev.exists() and "preview_version" not in project.stats():
        # a preview made before Cutroom (by hand, by another tool) is kept, never overwritten
        keep = project.edit / "preview.before-cutroom.mp4"
        if not keep.exists():
            prev.replace(keep)
    part.replace(prev)
    project.stat_set("preview_version", e["cutroom"]["version_id"])
    job.note = f"версия {e['cutroom']['version_id']}"
    return None
