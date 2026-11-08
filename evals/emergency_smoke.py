"""Smoke test for the «tokens ran out» path — no real agent calls, a synthetic project.

Checks: model routing; a usage-limit answer → one fallback step → AgentQuotaError + status flag;
colour / sound requests by script (no model); the preset stills do not overwrite each other when
asked for at once; the emergency export (1080p from blocks, unfinished card listed as skipped);
an unfinished project stays out of the knowledge base; «Завершить проект» on a current final
export writes the notes and makes the reference; the KB re-read at project start.

Usage:  backend/.venv/Scripts/python evals/emergency_smoke.py <empty scratch dir>
Needs ffmpeg/ffprobe on PATH. Sets APPDATA/HOME inside the scratch dir (settings, vault, skills).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(ROOT / "appdata")
os.environ["HOME"] = os.environ["USERPROFILE"] = str(ROOT / "home")
(ROOT / "appdata" / "Cutroom").mkdir(parents=True, exist_ok=True)
VAULT, PROJ = ROOT / "vault", ROOT / "Проект"
for d in (VAULT, PROJ, ROOT / "home" / ".claude" / "skills" / "obsidian-markdown"):
    d.mkdir(parents=True, exist_ok=True)
(ROOT / "home" / ".claude" / "skills" / "obsidian-markdown" / "SKILL.md").write_text(
    "---\nname: obsidian-markdown\ndescription: Obsidian flavored markdown\n---\nUse [[wikilinks]].\n", encoding="utf-8")
(VAULT / "Брендбук — визуал.md").write_text("# Бренд\n```yaml cutroom-brand\ncolors: {orange: '#E8A33D'}\ngrade: {name: Бренд, sat: 1.1, temperature: 10}\n```\n", encoding="utf-8")
(VAULT / "Правило крупности.md").write_text("---\ntags: [cutroom-rule]\ncutroom-skills: [obsidian-markdown]\n---\nНачинать с фронта.\n", encoding="utf-8")

# a fake Claude Code CLI that always answers «usage limit reached»
FAKE = ROOT / "fake_claude.py"
FAKE.write_text("#!" + sys.executable + "\nimport sys, json\nargs = sys.argv[1:]\n"
                "m = args[args.index('--model') + 1] if '--model' in args else '?'\n"
                "open(__file__ + '.log', 'a').write(m + '\\n')\n"
                "print(json.dumps({'is_error': True, 'result': 'Claude AI usage limit reached|1731000000'}))\n", encoding="utf-8")
FAKE.chmod(0o755)
(ROOT / "appdata" / "Cutroom" / "config.json").write_text(json.dumps({"vault_path": str(VAULT), "claude_cli": str(FAKE),
                                                                     "kb_agent_notes": False}), encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from cutroom import agent, edl, emergency, kb  # noqa: E402
from cutroom.jobs import Queue  # noqa: E402
from cutroom.project import Project  # noqa: E402
from cutroom.quick import ai, model  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    fails += 0 if cond else 1


def media() -> None:
    for name, src in (("front.mp4", "testsrc2=size=1280x720:rate=25"), ("top.mp4", "smptebars=size=1280x720:rate=25")):
        f = PROJ / name
        if not f.exists():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                            "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f)], check=True)


def main() -> None:
    media()
    q = Queue()
    p = Project(PROJ, q)
    p.scan()
    ids = {Path(s["path"]).name: sid for sid, s in p.sources.items()}
    front, top = ids["front.mp4"], ids["top.mp4"]

    # routing
    check((agent.model_for("quickcut"), agent.model_for("strategy"), agent.model_for("ask")) == (agent.OPUS, agent.SONNET, agent.HAIKU),
          "модели по ролям: нарезка Opus, анализ Sonnet, текст Haiku")

    # usage limit → one step down → AgentQuotaError, status flag
    try:
        agent.run("quickcut", "test")
        check(False, "лимит: ожидалась ошибка")
    except agent.AgentQuotaError as e:
        tried = (FAKE.parent / (FAKE.name + ".log")).read_text().split()
        check(tried == [agent.OPUS, agent.SONNET], f"лимит: Opus → Sonnet, дальше не падает до Haiku ({tried})")
        check("Экстренный экспорт" in (e.fix or ""), "лимит: подсказка про экстренный экспорт")
    check(agent.status()["quota"], "статус агента: токены закончились")

    # a quick-mode document by hand (what «Применить» of the first cut produces)
    d = model.empty("25/1")
    d["roles"] = {front: "front", top: "top"}
    d["tracks"]["video"] = [{"id": "v1", "start": 0, "len": 100, "src": front, "in": 0.0},
                            {"id": "v2", "start": 100, "len": 75, "src": top, "in": 2.0}]
    d["tracks"]["audio"] = [{"id": "a1", "start": 0, "len": 175, "src": front, "in": 0.0, "style": "natural"}]
    d["tracks"]["overlays"] = [{"id": "o1", "start": 25, "len": 75, "hint": "цифры", "style": "cream", "status": "placeholder"}]
    text = "Тест: экстренный экспорт"
    edl.save_strategy(p, {"status": "approved", "text": text, "approved_hash": edl.text_hash(text), "quick": True})
    d["meta"]["strategy_hash"] = edl.text_hash(text)
    d["tracks"]["video"][1]["color"] = {"temperature": 15}
    model.save(p, d, "eval", "тестовый монтаж")

    # colour / sound by script: no model call (the fake CLI would log one)
    before = len((FAKE.parent / (FAKE.name + ".log")).read_text().split())
    prop = ai.run_request(p, None, "сделай теплее", None)
    prop2 = ai.run_request(p, None, "чистая речь", None)
    after = len((FAKE.parent / (FAKE.name + ".log")).read_text().split())
    temps = [(v.get("color") or {}).get("temperature") for v in prop["doc"]["tracks"]["video"]]
    check(after == before and prop.get("fast") and temps == [20.0, 35.0], f"«сделай теплее» без ИИ, к текущему цвету: {temps}")
    check(prop2.get("fast") and all(a["style"] == "clean" for a in prop2["doc"]["tracks"]["audio"]), "«чистая речь» без ИИ")
    ai.drop_proposal(p)

    # preset stills asked for at once: each is its own file (the old code shared frame_after.jpg)
    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "cutroom.server", "--port", str(port), "--token", "t", "--project", str(PROJ)],
                           cwd=str(Path(__file__).resolve().parent.parent / "backend"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, env={**os.environ})
    try:
        _wait(port)
        grades = [None, {"preset": "warm_cinematic"}, {"preset": "neutral_punch"}, {"temperature": 60}, {"cdl": {"sat": 0.4}}, {"temperature": -60}]
        out: dict[int, bytes] = {}

        def get(i, g):
            url = (f"http://127.0.0.1:{port}/media/frame?t=t&source={front}&time=1.0&w=320&graded={1 if g else 0}"
                   f"&g={urllib.request.quote(json.dumps(g) if g else '')}")
            out[i] = urllib.request.urlopen(url, timeout=60).read()

        ts = [threading.Thread(target=get, args=(i, g)) for i, g in enumerate(grades)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        ok = len(out) == len(grades) and all(b[:2] == b"\xff\xd8" and b[-2:] == b"\xff\xd9" for b in out.values())
        check(ok, f"кадры пресетов одновременно: {len(out)} целых JPEG")
        check(len({hash(b) for b in out.values()}) == len(grades), "у каждого пресета свой кадр (нет перезаписи)")
        t0 = time.time()
        get(99, grades[1])
        check(time.time() - t0 < 0.5 and out[99] == out[1], f"повтор того же кадра из кэша: {time.time() - t0:.2f} с")
        r = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/claude?t=t", timeout=30).read())
        check("routing" in (r.get("agent") or {}), "/api/claude отдаёт маршрутизацию моделей")
    finally:
        srv.terminate()
        srv.wait(10)

    # emergency export: 1080p from blocks, the unfinished card listed
    st = emergency.export(p, log=lambda m: None)
    f = Path(st["file"])
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                               capture_output=True, text=True).stdout or 0)
    h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", str(f)],
                       capture_output=True, text=True).stdout.strip()
    check(f.exists() and abs(dur - 7.0) < 0.15 and h == "1080", f"экстренный экспорт: {f.name}, {dur:.2f} с, {h}p")
    check(any("цифры" in s for s in st["skipped"]) and f.with_suffix(".txt").exists(), "неготовая карточка в списке «не вошло»")

    # knowledge base: an unfinished project reaches neither the vault nor the references
    kbd = VAULT / "Cutroom"
    rnotes = lambda: list((kbd / "Рендеры").glob("*.md")) if (kbd / "Рендеры").exists() else []  # noqa: E731
    kb.index_graphic(p, "o1", {"type": "number", "title": "цифры"}, "cream", {"poster": "x.png"})
    check(not rnotes() and not kb.items("graphic"), "незавершённый проект: экстренный экспорт и графика не в базе знаний")
    info = kb.project_start(p)
    check(info["renders"] == 0 and info["ref_grade"] is None and not info["finished"], "незавершённый проект: нет референсов")
    try:
        kb.finish(p)
        check(False, "завершение без финального экспорта должно отказать")
    except RuntimeError as e:
        check("экстренный" in str(e), f"после экстренного экспорта «Завершить проект» недоступно: {str(e)[:60]}")
    from cutroom.quick import build
    build.run_sync(p, "final", log=lambda m: None)
    check(not rnotes(), "обычный финальный экспорт сам по себе в базу знаний не пишет")
    d2 = model.current(p)
    d2["tracks"]["video"][0]["color"] = {"temperature": 5}
    model.save(p, d2, "eval", "правка после экспорта")
    try:
        kb.finish(p)
        check(False, "устаревший экспорт должен отказать")
    except RuntimeError as e:
        check("менялся" in str(e), "после правок нужен новый экспорт: устаревший не становится итогом")
    build.run_sync(p, "final", log=lambda m: None)
    res = kb.finish(p)
    check(len(rnotes()) == 1 and (kbd / "Проекты" / "Проект.md").exists() and res["assets"] == 1 and len(kb.items("graphic")) == 1,
          "«Завершить проект»: заметка рендера, проекта, графика перенесена в индекс")
    check(not kb.project_state(p)["can_finish"], "повторно тот же итог не записывается")
    info = kb.project_start(p)
    check(info["renders"] == 1 and info["finished"] and info["rules"] == 1 and info["skills"] == ["obsidian-markdown"],
          f"старт проекта: {info['renders']} финальный рендер, правило, скилл")
    check(isinstance(info["ref_grade"], dict), "цвет финального рендера — пресет-референс")
    check("Начинать с фронта" in kb.agent_context(), "правило из базы знаний попадает агентам")
    q.shutdown()
    print(f"\n{'все проверки пройдены' if not fails else f'{fails} проверок не прошло'}")
    sys.exit(1 if fails else 0)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait(port: int) -> None:
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("сервер не поднялся")


if __name__ == "__main__":
    main()
