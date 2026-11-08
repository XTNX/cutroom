"""Live infographic smoke test (the brand reference's single layer) — synthetic project, a fake agent.

Checks: the layer agent's spec is anchored to spoken words; after a re-cut every element stays on its
word (a cut-out word moves to the next kept one); the band really appears in the assembled video at
its word and not before; editing one card re-renders only the segments it is on; the intro shifts the
film; the 4K master segment is HEVC Main10; the flat music bed.

Usage:  backend/.venv/Scripts/python evals/live_smoke.py <empty scratch dir>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(ROOT / "appdata")
os.environ["HOME"] = os.environ["USERPROFILE"] = str(ROOT / "home")
(ROOT / "appdata" / "Cutroom").mkdir(parents=True, exist_ok=True)
PROJ = ROOT / "Проект"
PROJ.mkdir(exist_ok=True)

SPEECH = ("Итак сегодня план запуска на три месяца . В ноябре мы готовим площадку до конца месяца . "
          "В декабре рассылка с четвёртого по пятнадцатое . А в январе главный запуск Делай движ . "
          "Первый шаг прислать тренера на тренинг . Второй шаг техподготовка экран и звук").split()

SPEC = {
    "title": "Дорожная карта проекта ДД на 3 месяца", "start_word": "план", "start_at": 1.0, "subtitles": True, "note": "собрано",
    "months": [{"label": "Ноябрь", "word": "ноябре", "at": 3.0}, {"label": "Декабрь", "word": "декабре", "at": 6.5}, {"label": "Январь", "word": "январе", "at": 10.0}],
    "marks": [{"kind": "date", "text": "конец 11.26", "caption": "площадка", "month": 0, "pos": 0.7, "word": "конца", "at": 5.0},
              {"kind": "launch", "text": "ДД", "caption": "запуск", "month": 2, "pos": 0.5, "word": "запуск", "at": 11.0,
               "highlight_word": "движ", "highlight_at": 12.0}],
    "cards": [{"group": "steps", "n": 1, "title": "Прислать тренера", "text": "На тренинг для тренеров", "date": "1–12.11.26", "word": "Первый", "at": 13.0},
              {"group": "steps", "n": 2, "title": "Техподготовка", "text": "Экран и звук", "tag": "экран", "word": "Второй", "at": 16.0}],
    "windows": [], "intro": None, "cta": None,
}


def media() -> None:
    for name, src in (("front.mp4", "testsrc2=size=1280x720:rate=25"), ("top.mp4", "smptebars=size=1280x720:rate=25")):
        f = PROJ / name
        if not f.exists():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
                            "-t", "22", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f)], check=True)
    music = ROOT / "music.wav"
    if not music.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-t", "30", str(music)], check=True)


FAKE = ROOT / "fake_claude.py"
FAKE.write_text("#!" + sys.executable + "\nimport sys, json\nsys.stdin.read()\n"
                f"print(json.dumps({{'is_error': False, 'structured_output': json.loads({json.dumps(json.dumps(SPEC, ensure_ascii=False))})}}))\n",
                encoding="utf-8")
FAKE.chmod(0o755)
(ROOT / "appdata" / "Cutroom" / "config.json").write_text(json.dumps({"claude_cli": str(FAKE), "prerender_final": False}), encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from cutroom import edl  # noqa: E402
from cutroom.jobs import Job, Queue  # noqa: E402
from cutroom.project import Project  # noqa: E402
from cutroom.quick import build, live, model  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    fails += 0 if cond else 1


def pixel(f: Path, t: float, x: int, y: int) -> tuple:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(f), "-frames:v", "1", "-vf", f"crop=2:2:{x}:{y}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return tuple(raw[:3])


def main() -> None:
    media()
    q = Queue()
    p = Project(PROJ, q)
    p.scan()
    ids = {Path(s["path"]).name: sid for sid, s in p.sources.items()}
    front, top = ids["front.mp4"], ids["top.mp4"]
    # a transcript for the front camera: one word every 0.45 s from 0.5 s
    words = [{"type": "word", "text": w, "start": round(0.5 + i * 0.45, 3), "end": round(0.5 + i * 0.45 + 0.35, 3)} for i, w in enumerate(SPEECH) if w != "."]
    p.paths(front)["transcript"].parent.mkdir(parents=True, exist_ok=True)
    p.paths(front)["transcript"].write_text(json.dumps({"language": "ru", "words": words}, ensure_ascii=False), encoding="utf-8")
    at = {w["text"]: w["start"] for w in words}

    d = model.empty("25/1")
    d["roles"] = {front: "front", top: "top"}
    d["tracks"]["video"] = [{"id": "v1", "start": 0, "len": 250, "src": front, "in": 0.0},
                            {"id": "v2", "start": 250, "len": 100, "src": top, "in": 10.0},
                            {"id": "v3", "start": 350, "len": 150, "src": front, "in": 14.0}]
    d["tracks"]["audio"] = [{"id": "a1", "start": 0, "len": 500, "src": front, "in": 0.0, "style": "natural"}]
    text = "Тест живого слоя"
    edl.save_strategy(p, {"status": "approved", "text": text, "approved_hash": edl.text_hash(text), "quick": True})
    d["meta"]["strategy_hash"] = edl.text_hash(text)
    model.save(p, d, "eval", "монтаж")

    # the layer agent (fake CLI) → spec anchored to words
    from cutroom.quick import routes
    routes.live_agent(p, Job(kind="qlive", label="t", lane="agent"), None, None)
    d = model.current(p)
    an = d["live"]["anchors"]
    check(an["months.0"]["word"] == "ноябре" and abs(an["months.0"]["t"] - at["ноябре"]) < 1e-6, f"месяц «Ноябрь» привязан к слову «ноябре» ({at['ноябре']} с исходника)")
    check(an["marks.1.hl"]["word"] == "движ", "подсветка запуска — на слове «движ»")
    lay, warn = live.layer(p, d)
    check(abs(lay["months"][1]["t"] - at["декабре"]) < 1e-3 and not warn, f"время в ролике = время слова: декабрь {lay['months'][1]['t']} с")
    check([a[2] for a in lay["angles"]] == ["front", "top", "front"], "ракурсы по времени: фронт, верх, фронт")
    check(len(lay["subs"]) >= 4 and all(len(s[2].split()) <= 6 for s in lay["subs"]), f"субтитры из расшифровки: {len(lay['subs'])} фраз")

    # re-cut: 2 s out of the start (everything moves left) → elements stay on their words
    d2 = json.loads(json.dumps(d))
    from cutroom.quick import ops
    ops.ripple_delete(d2, 0, 50)
    model.save(p, d2, "eval", "минус 2 с в начале")
    lay2, warn2 = live.layer(p, model.current(p))
    check(abs(lay2["months"][1]["t"] - (lay["months"][1]["t"] - 2)) < 1e-3, f"после перенарезки декабрь сдвинулся с речью: {lay['months'][1]['t']} → {lay2['months'][1]['t']} с")
    check(lay2["start"] is not None and lay2["start"] >= 0, f"слово «план» вырезано — лента появляется на следующем слове ({lay2['start']} с)")
    model.move_head(p, -1)
    d = model.current(p)

    # assembled draft: the band is there after its word, not before
    st = build.run_sync(p, "draft", log=lambda m: None)
    f = Path(st["file"])
    t_band = lay["start"] + 1.0
    before, after = pixel(f, max(0.1, lay["start"] - 0.4), 640, 40), pixel(f, t_band, 640, 40)
    cream = (255, 249, 235)
    close = lambda a, b: all(abs(x - y) < 24 for x, y in zip(a, b))  # noqa: E731
    check(not close(before, cream) and close(after, cream), f"лента в собранном видео: до слова {before}, после {after} (кремовый)")
    n_live = sum(1 for s in build.segments(p, d, "draft") if any(o.get("live") for o in s["job"]["overlays"]))
    check(n_live == 3, f"слой нарезан по сегментам: {n_live} из 3 с кусочком слоя")

    # edit one card: only the segment(s) it is on are rebuilt
    spec = json.loads(json.dumps(d["live"]["spec"]))
    spec["cards"][1]["title"] = "Техподготовка зала"
    d, _ = live.set_spec(p, d, spec)
    model.save(p, d, "eval", "правка карточки")
    st2 = build.run_sync(p, "draft", log=lambda m: None)
    check(st2["rebuilt"] == ["v3"], f"правка шага 2 пересобрала только его блок: {st2['rebuilt']}")

    # intro: the whole film moves right by its seconds, anchors still on their words
    spec["intro"] = {"title": "План запуска", "subtitle": "три месяца", "page": None, "seconds": 3.0}
    d, _ = live.set_spec(p, model.current(p), spec)
    model.save(p, d, "eval", "заставка")
    d = model.current(p)
    lay3, _ = live.layer(p, d)
    check(d["tracks"]["video"][0]["start"] == 75 and abs(lay3["months"][0]["t"] - (lay["months"][0]["t"] + 3)) < 1e-3,
          "заставка 3 с: ролик сдвинулся на 75 кадров, месяцы остались на словах")
    spec["intro"] = None
    d, _ = live.set_spec(p, d, spec)
    check(d["tracks"]["video"][0]["start"] == 0, "заставка убрана: ролик вернулся к 0")

    # the 4K master segment: HEVC Main10
    seg = build.segments(p, d, "master")[1]
    job = dict(seg["job"])
    job["frames"] = 10
    job["overlays"] = []
    job["out"] = str(ROOT / "m4k.mp4")
    jf = ROOT / "m4k.json"
    jf.write_text(json.dumps(job), encoding="utf-8")
    r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "backend" / "helpers" / "render_block.py"), str(jf)], capture_output=True, text=True)
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,profile,pix_fmt,width", "-of", "csv=p=0",
                         str(ROOT / "m4k.mp4")], capture_output=True, text=True).stdout.strip()
    check(r.returncode == 0 and pr.startswith("hevc,Main 10") and "3840" in pr, f"мастер 4K: {pr}")

    # flat music bed: one even level, no ducking
    d = model.current(p)
    d["music"] = {"track": "t", "path": str(ROOT / "music.wav"), "title": "тест", "flat": True}
    model.save(p, d, "eval", "музыка ровно")
    st3 = build.run_sync(p, "final", log=lambda m: None)
    check(Path(st3["file"]).exists(), "экспорт 1080p с ровной подложкой собран")
    q.shutdown()
    print(f"\n{'все проверки пройдены' if not fails else f'{fails} проверок не прошло'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
