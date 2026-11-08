"""Review rounds smoke test — the reference's process (notes on timecodes → a round of edits → the journal).

Checks: a version goes out (snapshot); the customer's list is parsed (ranges, «с 2:40», lines without
timecodes, kinds); after a re-cut every note is at its moment in the CURRENT version and a cut-out
moment is flagged; one round of edits (fake agent) → one proposal with an answer per note → statuses +
the journal «Что просил | Как сделано» in edit/project.md; decisions reach the agents' context; a
slide follows its moment of the speech into a new cut; the KB render note carries the journal.

Usage:  backend/.venv/Scripts/python evals/review_smoke.py <empty scratch dir>
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
PROJ, VAULT = ROOT / "Проект", ROOT / "vault"
PROJ.mkdir(exist_ok=True)
VAULT.mkdir(exist_ok=True)

PLAN = {"summary": "Сделано", "ops": [{"op": "angle", "start": 10.0, "end": 12.0, "angle": "top"}], "warnings": [],
        "answers": [{"n": 1, "done": False, "how": "момент уже вырезан — уточнить у заказчика"},
                    {"n": 2, "done": True, "how": "10:00–12:00 верхний план"},
                    {"n": 3, "done": True, "how": "подложка на весь ролик ровная"}]}
CUT = {"ranges": [{"source": "FRONT", "start": 6.0, "end": 12.0, "angle": "front", "reason": "сначала декабрь"},
                  {"source": "FRONT", "start": 0.5, "end": 6.0, "angle": "front", "reason": "потом начало"}], "summary": "переставлено"}

FAKE = ROOT / "fake_claude.py"
FAKE.write_text("#!" + sys.executable + "\nimport sys, json\nsys.stdin.read()\nargs = ' '.join(sys.argv)\n"
                f"plan = json.loads({json.dumps(json.dumps(PLAN, ensure_ascii=False))})\n"
                f"cut = json.loads({json.dumps(json.dumps(CUT, ensure_ascii=False))})\n"
                "cut = json.loads(open(__file__ + '.cut').read()) if 'system-quickcut-' in args else None\n"
                "print(json.dumps({'is_error': False, 'structured_output': cut if cut else plan}))\n", encoding="utf-8")
FAKE.chmod(0o755)
(ROOT / "appdata" / "Cutroom" / "config.json").write_text(json.dumps({"claude_cli": str(FAKE), "prerender_final": False, "vault_path": str(VAULT),
                                                                     "kb_agent_notes": False}), encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from cutroom import edl  # noqa: E402
from cutroom.jobs import Job, Queue  # noqa: E402
from cutroom.project import Project  # noqa: E402
from cutroom.quick import ai, model, ops, review  # noqa: E402

fails = 0
WORDS = "итак сегодня план запуска на три месяца в ноябре мы готовим площадку в декабре рассылка а в январе запуск".split()


def check(cond: bool, what: str) -> None:
    global fails
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    fails += 0 if cond else 1


def main() -> None:
    f = PROJ / "front.mp4"
    if not f.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-f", "lavfi", "-i", "sine=frequency=330",
                        "-t", "20", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f)], check=True)
    q = Queue()
    p = Project(PROJ, q)
    p.scan()
    front = next(iter(p.sources))
    words = [{"type": "word", "text": w, "start": round(0.5 + i * 0.6, 3), "end": round(0.5 + i * 0.6 + 0.45, 3)} for i, w in enumerate(WORDS)]
    p.paths(front)["transcript"].parent.mkdir(parents=True, exist_ok=True)
    p.paths(front)["transcript"].write_text(json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8")
    (FAKE.parent / (FAKE.name + ".cut")).write_text(json.dumps(CUT).replace("FRONT", front), encoding="utf-8")

    d = model.empty("25/1")
    d["roles"] = {front: "front"}
    d["tracks"]["video"] = [{"id": "v1", "start": 0, "len": 500, "src": front, "in": 0.0}]
    d["tracks"]["audio"] = [{"id": "a1", "start": 0, "len": 500, "src": front, "in": 0.0, "style": "clean", "link": "v1"}]
    text = "тест правок"
    edl.save_strategy(p, {"status": "approved", "text": text, "approved_hash": edl.text_hash(text), "quick": True})
    model.save(p, d, "eval", "монтаж")

    rd = review.send(p, "первая версия")
    check(rd["n"] == 1 and review.snapshot(p, 1) is not None, f"отправка на согласование: раунд 1 = v{rd['version']}, снимок сохранён")

    made = review.add_notes(p, "1. 0:01–0:03 убрать оговорку в начале\n- 0:12 показать верх\nмузыка не должна расти в паузах")
    check([(n["t0"], n["t1"], n["kind"]) for n in made] == [(1.0, 3.0, "cut"), (12.0, None, "angle"), (None, None, "sound")],
          f"разбор списка: {[(n['t0'], n['t1'], n['kind']) for n in made]}")

    # the cut moves on after the customer watched v1: 4 s out of the start
    d2 = model.current(p)
    ops.ripple_delete(d2, 0, 100)
    model.save(p, d2, "eval", "минус 4 с")
    st = review.state(p)
    n1, n2 = st["notes"][0], st["notes"][1]
    check(n2["now0"] == 8.0 and n2["moved"], f"пометка «0:12» в текущей версии — на {n2['now0']} с (момент сдвинулся с речью)")
    check(n1["gone"], f"пометка «0:01» — момент вырезан: {n1['gone']}, now {n1['now0']}")
    txt, notes = review.round_prompt(p)
    check("[00:08.0]" in txt and "весь ролик" in txt, "в раунд пометки уходят с таймкодами текущей версии")

    review.add_decision(p, "Даты на ленте — по речи, на карточках — из презентации")
    ctx = "\n".join(ai.context(p, model.current(p), None))
    check("Даты на ленте — по речи" in ctx, "решения проекта — в контексте агентов")

    # one round: the fake agent answers every note
    prop = ai.run_request(p, Job(kind="qai", label="Раунд правок", lane="agent"), "Правки заказчика (3)", None, review={"text": txt, "notes": notes})
    check(prop["kind"] == "review" and len(prop["answers"]) == 3, "раунд правок → одно предложение с ответом по каждой пометке")
    ai.apply(p)
    st = review.state(p)
    check([n["status"] for n in st["notes"]] == ["question", "done", "done"], f"статусы после «Применить»: {[n['status'] for n in st['notes']]}")
    pm = (p.edit / "project.md").read_text(encoding="utf-8")
    check("| Что просил | Как сделано |" in pm and "показать верх | 10:00–12:00 верхний план" in pm and "Решения, которые стоит помнить" in pm,
          "журнал в edit/project.md: таблица «Что просил | Как сделано» и решения")

    # a slide follows its moment of the speech into a new cut
    d3 = model.current(p)
    fps = 25
    t_dec = next(w["start"] for w in words if w["text"] == "декабре") - 4.0   # in the current version (4 s cut)
    d3["tracks"]["slides"] = [{"id": "s1", "start": round(t_dec * fps), "len": 50, "page": 1, "layout": "side"}]
    model.save(p, d3, "eval", "слайд на «декабре»")
    ai.run_cut(p, Job(kind="qcut", label="t", lane="agent"), "переставь")
    pr = ai.load_proposal(p)
    s1 = pr["doc"]["tracks"]["slides"][0]
    a = review.to_source(pr["doc"], s1["start"] / fps)
    check(a and abs(a["t"] - (t_dec + 4.0)) < 0.05, f"слайд переехал за словом «декабре» в новую нарезку: кадр {s1['start']} (в исходнике {a and a['t']} с)")

    # the knowledge base gets the journal with the finished project's render note
    from cutroom import kb
    note = kb.render_note(p, {"version": 9, "duration": 16, "file": "x.mp4"}, "quick")
    body = note.read_text(encoding="utf-8")
    check("## Правки по версиям" in body and "верхний план" in body, "заметка рендера в базе знаний: журнал правок и решения")
    q.shutdown()
    print(f"\n{'все проверки пройдены' if not fails else f'{fails} проверок не прошло'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
