"""Quick-mode smoke test (no agent calls): incremental build, side ≠ top, AI fast path, Quick ⇄ Full.

Needs a quick-mode project that already has a cut (blocks on the timeline), e.g. the test copy
D:/Video/cutroom-evals/bf-quick. Works on a copy of its current version and restores it at the end.

Usage: backend/.venv/Scripts/python evals/quick_smoke.py D:/Video/cutroom-evals/bf-quick
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from cutroom.config import subprocess_env  # noqa: E402
from cutroom.jobs import Job, Queue  # noqa: E402
from cutroom.project import Project  # noqa: E402
from cutroom.quick import ai, build, model, ops  # noqa: E402
from cutroom.quick.convert import from_full, to_full  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    fails += 0 if cond else 1


def build_now(p, profile: str = "draft") -> dict:
    """What the job queue does, synchronously: render the missing segments, then assemble."""
    doc = model.current(p)
    segs = build.segments(p, doc, profile)
    need = build.missing(segs)
    for s in need:
        r = subprocess.run(build.block_cmd(s), capture_output=True, text=True, env=subprocess_env())
        assert r.returncode == 0, r.stdout[-300:]
    return build.assemble(p, Job(kind="t", label="t", lane="io"), profile, doc["meta"]["version_id"],
                          [s["block"] or f"пауза@{s['start']}" for s in need])


def main() -> None:
    q = Queue()
    p = Project(Path(sys.argv[1]), q)
    start = model.current(p)
    assert start and start["tracks"]["video"], "в проекте нет монтажа быстрого режима"
    head0 = model.head(p)
    try:
        run(p)
    finally:
        model.save(p, start, "eval", "восстановлено после теста")
        print(f"\nдокумент восстановлен (был v{head0})")
        q.shutdown()
    print(f"\n{'все проверки пройдены' if not fails else f'{fails} проверок не прошло'}")
    sys.exit(1 if fails else 0)


def run(p) -> None:
    st = build_now(p)
    check(st is not None and st["segments"] == len(build.segments(p, model.current(p), "draft")), f"черновая сборка: {st['segments']} сегм., {st['duration']} с")

    # 1. one block changes angle → exactly one segment is rebuilt, the rest come from the cache
    d = model.current(p)
    target, want = None, None
    for v in d["tracks"]["video"]:
        cov = model.coverage(p, d, v)
        own = model.role(d, p, v["src"])
        alt = next((k for k in ("front", "top", "side") if k != own and cov.get(k)), None)
        if alt:
            target, want = v["id"], alt
            break
    check(target is not None, f"есть блок, которому доступен другой ракурс ({want})")
    v = next(x for x in d["tracks"]["video"] if x["id"] == target)
    old = (v["src"], v["in"])
    check(model.switch_angle(p, d, v, want) is None, f"смена ракурса блока {target} на «{model.ROLE_NAMES[want]}»")
    model.save(p, d, "eval", "eval: ракурс")
    t0 = time.time()
    st = build_now(p)
    check(st["rebuilt"] == [target], f"пересобран только изменённый блок: {st['rebuilt']} ({time.time() - t0:.1f} с, из кэша {len(st['cached'])})")

    # 2. back to the old angle: that segment is still in the cache → nothing is rebuilt
    d = model.current(p)
    v = next(x for x in d["tracks"]["video"] if x["id"] == target)
    v["src"], v["in"] = old
    model.save(p, d, "eval", "eval: ракурс обратно")
    st = build_now(p)
    check(st["rebuilt"] == [], f"возврат ракурса: пересобрано 0 (сегмент взят из кэша), звук из кэша: {not st['audio_rebuilt']}")

    # 3. side is never top: a block without synced top footage refuses the switch and stays as it was
    d = model.current(p)
    nontop = next((x for x in d["tracks"]["video"] if not model.coverage(p, d, x).get("top")), None)
    if nontop:
        before = dict(nontop)
        err = model.switch_angle(p, d, nontop, "top")
        check(bool(err) and nontop == before, f"нет синхронного верха → отказ без подмены: «{err}»")
    sides = [s for s in p.sources if model.role(d, p, s) == "side"]
    check(all(model.role(d, p, s) != "top" for s in sides), f"клипы «сбоку» ({len(sides)}) не считаются верхом")

    # 4. AI window fast path (no agent): timestamps + angle, delete, selection
    f = ai.fast_parse("с 01:12 до 01:40 только верх", False)
    check(bool(f) and f.ops[0].op == "angle" and f.ops[0].angle == "top" and (f.ops[0].start, f.ops[0].end) == (72.0, 100.0), "«с 01:12 до 01:40 только верх» → angle top 72–100 с")
    f = ai.fast_parse("удали 0:30–0:42", False)
    check(bool(f) and f.ops[0].op == "delete" and (f.ops[0].start, f.ops[0].end) == (30.0, 42.0), "«удали 0:30–0:42» → delete 30–42 с")
    f = ai.fast_parse("сделай фронт", True)
    check(bool(f) and f.ops[0].op == "angle" and f.ops[0].start is None, "«сделай фронт» с выделением → angle front на выделении")
    check(ai.fast_parse("сделай теплее и добавь карточку с ценами", True) is None, "сложная просьба уходит агенту (быстрый путь не угадывает)")
    check(ai.fast_parse("сбоку", False) is None, "без таймкода и без выделения быстрый путь не срабатывает")

    # 5. proposal: ops on a copy, the saved document is untouched until «Применить»
    d = model.current(p)
    fps = model.fps_of(d)
    new, warn, _ = ai.apply_ops(p, d, [{"op": "angle", "start": d["tracks"]["video"][0]["start"] / fps + 1, "end": d["tracks"]["video"][0]["start"] / fps + 3,
                                        "angle": "top"}], None)
    check(model.current(p)["meta"]["version_id"] == d["meta"]["version_id"], f"предложение не меняет сохранённый монтаж (предупреждений: {len(warn)})")
    ch = ai.changed(d, new)
    check(len(ch["video"]) >= 1, f"предложение знает, какие блоки меняются: {ch['video'][:4]}")

    # 6. ripple delete keeps every track consistent
    d2 = json.loads(json.dumps(d))
    n0 = model.total_frames(d2)
    ops.ripple_delete(d2, round(5 * fps), round(8 * fps))
    d2 = model.normalize(p, d2)
    check(model.total_frames(d2) == n0 - (round(8 * fps) - round(5 * fps)), "вырезка 00:05–00:08 укорачивает ролик ровно на 3 с")
    links = {a.get("link") for a in d2["tracks"]["audio"] if a.get("link")}
    check(links <= {v["id"] for v in d2["tracks"]["video"]}, "после вырезки звук связан только с существующими блоками")

    # 7. Quick → Full → Quick without edits restores the document exactly; full mode sees the same cut
    d = model.current(p)
    e = to_full(p, d)
    check(len(e["ranges"]) == len(d["tracks"]["video"]) or any(b["start"] for b in d["tracks"]["video"]), f"полный режим: {len(e['ranges'])} фрагментов, {len(e['slots'])} карточек, {len(e['overlays'])} слайдов")
    back, warn = from_full(p, json.loads(json.dumps(e)), d)
    same = json.dumps(back["tracks"], sort_keys=True) == json.dumps(d["tracks"], sort_keys=True)
    check(same, "быстрый → полный → быстрый: документ совпадает полностью")
    e2 = json.loads(json.dumps(e))
    e2["ranges"][0]["end"] = round(e2["ranges"][0]["end"] - 1.0, 3)   # an edit in full mode: the first fragment 1 s shorter
    back2, _ = from_full(p, e2, d)
    check(back2["tracks"]["slides"] == d["tracks"]["slides"] and [o["style"] for o in back2["tracks"]["overlays"]] == [o["style"] for o in d["tracks"]["overlays"]],
          "правка в полном режиме: блоки пересобраны из фрагментов, слайды и стили карточек сохранены")
    check(abs(back2["tracks"]["video"][0]["len"] - (d["tracks"]["video"][0]["len"] - round(fps))) <= 1, "правка в полном режиме видна в быстром (первый блок короче на 1 с)")


if __name__ == "__main__":
    main()
