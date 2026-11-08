"""The single «Запросы к ИИ» window.

Every request becomes a PROPOSAL: the operations, a one-line summary, warnings, and the document
as it would be — nothing changes until the person presses «Применить» (and a stale proposal is
refused if the timeline moved on). Context = the selection on the timeline, else the whole video.

Fast path without the model: «с 01:12 до 01:40 только верх», «удали 0:30–0:42», «сделай фронт» on
a selection — parsed directly (seconds instead of a minute-long agent call). Anything else goes
to the router agent (prompts/quick.md), the first cut goes to the cut agent (prompts/quickcut.md).
"""

from __future__ import annotations

import json
import re
import time

from .. import agent, edl
from ..jobs import Job
from . import model, ops
from .convert import blocks_from_ranges
from .model import ROLE_NAMES, fps_of, qdir

TS = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2}(?:[.,]\d+)?)(?![\d:])")
ANGLE_WORDS = [("top", r"верх|сверху|стол\b|на стол|над стол"), ("side", r"сбоку|боков"), ("front", r"фронт|спереди|анфас|лицо|говорящ")]
DELETE_WORDS = r"\b(удали|удалить|вырежи|вырезать|убери|убрать)\b"


def proposal_path(project):
    return qdir(project) / "proposal.json"


def load_proposal(project) -> dict | None:
    try:
        return json.loads(proposal_path(project).read_text(encoding="utf-8"))
    except Exception:
        return None


def drop_proposal(project) -> None:
    proposal_path(project).unlink(missing_ok=True)


# ---------------------------------------------------------------- context


def selection_frames(doc: dict, sel: dict | None) -> tuple[int, int] | None:
    if not sel:
        return None
    if sel.get("start") is not None and sel.get("end") is not None and sel["end"] > sel["start"]:
        return int(sel["start"]), int(sel["end"])
    ids = set(sel.get("ids") or [])
    bs = [b for t in model.TRACKS for b in doc["tracks"][t] if b["id"] in ids]
    if bs:
        return min(b["start"] for b in bs), max(b["start"] + b["len"] for b in bs)
    return None


def context(project, doc: dict, sel: tuple[int, int] | None) -> list[str]:
    from .cards import words_at
    fps = fps_of(doc)
    lines = []
    for i, v in enumerate(doc["tracks"]["video"], start=1):
        cov = model.coverage(project, doc, v)
        avail = ", ".join(ROLE_NAMES[k] for k, c in cov.items() if c) or "только этот клип"
        said = words_at(project, doc, v["start"], v["start"] + v["len"], pad=0).replace("\n", " ")[:160]
        mark = "  ← ВЫДЕЛЕНО" if sel and v["start"] < sel[1] and v["start"] + v["len"] > sel[0] else ""
        lines.append(f"{i}. {model.fmt(v['start'] / fps)}–{model.fmt((v['start'] + v['len']) / fps)} "
                     f"[{ROLE_NAMES.get(model.role(doc, project, v['src']), '?')} {v['src']}; есть: {avail}] {said}{mark}")
    out = [agent.wrap_data("монтаж (блоки картинки)", "\n".join(lines) or "(пусто)")]
    if sel:
        out.append(agent.wrap_data("выделение", f"{model.fmt(sel[0] / fps)}–{model.fmt(sel[1] / fps)} "
                                                f"({sel[0] / fps:.2f}–{sel[1] / fps:.2f} с)"))
    pres = doc.get("presentation") or {}
    if pres.get("pages"):
        out.append(agent.wrap_data("слайды", "\n".join(f"{p['n']}: {(p.get('text') or '(картинка без текста)')[:140]}" for p in pres["pages"])))
    on = [f"{model.fmt(s['start'] / fps)}–{model.fmt((s['start'] + s['len']) / fps)} слайд {s['page']}" for s in doc["tracks"]["slides"]]
    on += [f"{model.fmt(o['start'] / fps)}–{model.fmt((o['start'] + o['len']) / fps)} карточка «{(o.get('spec') or {}).get('title') or o.get('hint', '')}» ({o.get('style', 'cream')})"
           for o in doc["tracks"]["overlays"]]
    if on:
        out.append(agent.wrap_data("слайды и карточки на таймлайне", "\n".join(on)))
    from ..brand import presets
    out.append(agent.wrap_data("пресеты цвета", ", ".join(f"{p['id']} ({p['name']})" for p in presets())))
    return out


# ---------------------------------------------------------------- fast path


def _secs(m) -> float:
    return int(m.group(1)) * 60 + float(m.group(2).replace(",", "."))


COLOR_WORDS = [  # phrase → (temperature step, saturation factor); relative to what the block has now
    (r"тепл(ее|ей|о)|потепле|желтее", 20, None), (r"холодн(ее|ей)|прохладн|синее|похолод", -20, None),
    (r"насыщенн(ее|ей)|сочн(ее|ей)|ярче цвет|ярче краски|поярче", None, 1.15),
    (r"бледн(ее|ей)|менее насыщ|приглуш(и|ить) цвет|спокойн(ее|ей) цвет|потуск", None, 0.87),
]
SOUND_WORDS = [("clean", r"чист(ая|ую) речь|без подложк|без музык"), ("light", r"подложк\w* (потише|тише|лёгк|легк)|лёгк\w* подложк|легк\w* подложк"),
               ("bed", r"(на|под) музык|подложк\w* (громче|погромче)"), ("natural", r"как записано|без обработк")]
CARD_WORDS = [("cream", r"крем"), ("orange", r"оранж"), ("khaki", r"хаки"), ("carton", r"картон")]


def fast_parse(prompt: str, has_sel: bool) -> agent.QuickPlan | None:
    """Plain requests become operations by script — no model call, no tokens, instant."""
    p = prompt.lower().strip()
    if len(p) > 140:
        return None
    ts = [_secs(m) for m in TS.finditer(p)]
    if len(ts) not in (0, 2):
        return None
    start, end = (min(ts), max(ts)) if ts else (None, None)
    where = ("на выделенном отрезке" if has_sel else "во всём ролике") if start is None else f"с {model.fmt(start)} до {model.fmt(end)}"
    rest = TS.sub(" ", p)
    rest = re.sub(r"\b(с|до|по|от|на|и|только|оставь|оставить|поставь|сделай|переключи|ракурс|кадр|камера|камеру|вид|пожалуйста)\b|[–—\-,.!]", " ", rest)
    # colour, sound, card style: fine without a selection (then the whole film)
    if re.search(r"без цвета|сбрось цвет|убери цвет|исходн\w* цвет", p):
        return agent.QuickPlan(summary=f"{where.capitalize()} — без цветокора.", ops=[agent.QuickOp(op="color", start=start, end=end, preset="none")])
    from ..brand import presets
    for pr in presets():
        nm = pr["name"].lower()
        if pr["id"] != "none" and (re.search(rf"(пресет|цвет)\w*\s+«?{re.escape(nm)}", p) or (nm == "бренд" and re.search(r"(цвет|пресет)\w* бренд|по бренд", p))):
            return agent.QuickPlan(summary=f"{where.capitalize()} — цвет «{pr['name']}».", ops=[agent.QuickOp(op="color", start=start, end=end, preset=pr["id"])])
    col = [(t, k) for rx, t, k in COLOR_WORDS if re.search(rx, p)]
    if col and not re.search(r"карточк|слайд|звук|музык", p):
        t = sum(x for x, _ in col if x) or None
        k = next((y for _, y in col if y), None)
        what = ", ".join(w for w, on in (("теплее", (t or 0) > 0), ("холоднее", (t or 0) < 0), ("насыщеннее", (k or 1) > 1), ("спокойнее по цвету", (k or 1) < 1)) if on)
        return agent.QuickPlan(summary=f"{where.capitalize()} — {what}.",
                               ops=[agent.QuickOp(op="color", start=start, end=end, temperature=t, saturation=k, text="relative")])
    for style, rx in SOUND_WORDS:
        if re.search(rx, p):
            from .build import STYLES
            return agent.QuickPlan(summary=f"{where.capitalize()} — звук «{STYLES[style]['label']}».",
                                   ops=[agent.QuickOp(op="audio", start=start, end=end, style=style)])
    if re.search(r"карточк", p) and not re.search(r"нов|добав|сделай карточк|по слайду", p):
        st = next((k for k, rx in CARD_WORDS if re.search(rx, p)), None)
        if st:
            from ..brand import STYLES as CARD_STYLES
            return agent.QuickPlan(summary=f"{where.capitalize()} — карточки в стиле «{CARD_STYLES[st]}».", ops=[agent.QuickOp(op="overlay_style", start=start, end=end, style=st)])
    if not ts and not has_sel:
        return None
    m = re.search(r"слайд\w*\s*(?:№\s*)?(\d{1,3})", p)
    if m and not re.search(r"карточк|инфограф", p):
        full = bool(re.search(r"весь кадр|во весь|на весь|полн\w* экран|закро", p))
        return agent.QuickPlan(summary=f"{where.capitalize()} — слайд {m.group(1)}{' во весь кадр' if full else ' панелью справа'}.",
                               ops=[agent.QuickOp(op="slide", start=start, end=end, page=int(m.group(1)), style="full" if full else "")])
    if re.search(DELETE_WORDS, p) and not re.search(r"карточк|слайд|музык|цвет", p):
        left = re.sub(DELETE_WORDS, " ", rest).split()
        if not left or all(w in ("это", "кусок", "фрагмент", "отрезок", "участок") for w in left):
            return agent.QuickPlan(summary=f"Вырежу {'выделенный отрезок' if start is None else f'{model.fmt(start)}–{model.fmt(end)}'} — всё после него сдвинется.",
                                   ops=[agent.QuickOp(op="delete", start=start, end=end)])
    hits = [k for k, rx in ANGLE_WORDS if re.search(rx, rest)]
    if len(hits) == 1:
        left = re.sub("|".join(rx for _, rx in ANGLE_WORDS), " ", rest)
        left = left.split()
        if len(left) <= 2:
            return agent.QuickPlan(summary=f"{where.capitalize()} — ракурс «{ROLE_NAMES[hits[0]]}».",
                                   ops=[agent.QuickOp(op="angle", start=start, end=end, angle=hits[0])])
    return None


# ---------------------------------------------------------------- applying operations


def apply_ops(project, doc: dict, plan_ops: list, sel: tuple[int, int] | None, run_recut=None) -> tuple[dict, list[str], dict]:
    """Operations → the new document (a copy). Returns (doc, warnings, follow-ups for after «Применить»)."""
    from ..brand import grade_of
    doc = json.loads(json.dumps(doc))
    fps = fps_of(doc)
    total = model.total_frames(doc)
    warn: list[str] = []
    follow = {"spec": [], "draw": [], "requests": {}}
    for op in plan_ops:
        o = op if isinstance(op, dict) else op.model_dump()
        if o.get("start") is not None:
            f0 = max(0, round(float(o["start"]) * fps))
            f1 = round(float(o["end"]) * fps) if o.get("end") is not None else None
        else:
            f0, f1 = sel if sel else (0, total)
        kind = o["op"]
        if kind in ("angle", "color", "audio", "delete") and (f1 is None or f1 <= f0):
            f1 = min(total, f0 + round(5 * fps)) if f1 is None else f1
        if kind == "angle":
            want = o.get("angle") or "front"
            for v in ops.within(doc, "video", f0, f1):
                err = model.switch_angle(project, doc, v, want)
                if err:
                    warn.append(err)
        elif kind == "color":
            g = grade_of(o["preset"]) if o.get("preset") else None
            for v in ops.within(doc, "video", f0, f1):
                if o.get("preset") == "none":   # explicitly untouched (not «fall back to the film's grade»)
                    v["color"] = {"preset": "none"}
                    continue
                base = json.loads(json.dumps((g or {}) if o.get("preset") else (v.get("color") or doc.get("grade") or {})))
                rel = o.get("text") == "relative"   # fast path: «теплее» adds to what the block has
                if o.get("temperature") is not None:
                    t = float(o["temperature"]) + (float(base.get("temperature") or 0) if rel else 0)
                    base["temperature"] = max(-100.0, min(100.0, t))
                if o.get("saturation") is not None:
                    k = float(o["saturation"]) * (float((base.get("cdl") or {}).get("sat") or 1) if rel else 1)
                    base.setdefault("cdl", {})["sat"] = max(0.3, min(1.8, k))
                v["color"] = base or None
        elif kind == "audio":
            style = o.get("style") if o.get("style") in ("clean", "light", "bed", "natural") else "clean"
            for a in ops.within(doc, "audio", f0, f1):
                a["style"] = style
            if style in ("light", "bed") and not doc.get("music"):
                warn.append("Для подложки выберите трек на музыкальной полке (окно вариантов звука) — пока звучит только речь.")
        elif kind == "delete":
            ops.ripple_delete(doc, f0, f1)
        elif kind == "slide":
            page = o.get("page") or 1
            if not (doc.get("presentation") or {}).get("pages"):
                warn.append("Презентация не загружена — слайд поставить нельзя.")
                continue
            n = (f1 - f0) if f1 and f1 > f0 else round(8 * fps)
            ops.add_block(doc, "slides", {"id": model.new_id("s"), "start": f0, "len": n, "page": int(page),
                                          "layout": "full" if o.get("style") == "full" else "side"})
        elif kind == "overlay":
            n = (f1 - f0) if f1 and f1 > f0 else round(6 * fps)
            n = max(round(2.5 * fps), min(n, round(20 * fps)))
            sl = next((s for s in doc["tracks"]["slides"] if o.get("page") and s["page"] == o["page"]), None)
            card = {"id": model.new_id("o"), "start": f0, "len": n, "hint": o.get("text", ""), "style": o.get("style") or "cream",
                    "status": "placeholder", "page": o.get("page"), "slide": sl["id"] if sl else None}
            ops.add_block(doc, "overlays", card)
            follow["spec"].append(card["id"])
        elif kind == "overlay_style":
            hit = [c for c in doc["tracks"]["overlays"] if c["start"] < (f1 or total) and c["start"] + c["len"] > f0]
            if not hit:
                warn.append("На этом отрезке нет карточек.")
            for c in hit:
                if o.get("style") in ("cream", "orange", "khaki", "carton"):
                    c["style"] = o["style"]
                if o.get("text"):
                    follow["spec"].append(c["id"])
                    follow["requests"][c["id"]] = o["text"]
                elif isinstance(c.get("spec"), dict):
                    follow["draw"].append(c["id"])
        elif kind == "recut":
            if run_recut is None:
                warn.append("Пересборка фраз доступна из окна запросов.")
                continue
            new_v, new_a, w = run_recut(doc, f0, f1, o.get("text", ""))
            warn += w
            ops.ripple_delete(doc, f0, f1)
            insert(doc, f0, new_v, new_a)
    return doc, warn, follow


def insert(doc: dict, f0: int, video: list[dict], audio: list[dict]) -> None:
    """Insert laid-out blocks (starting at 0) at frame f0; everything after moves right."""
    n = max([b["start"] + b["len"] for b in video + audio] or [0])
    ops.split(doc, "video", f0)
    ops.split(doc, "audio", f0)
    for t in model.TRACKS:
        for b in doc["tracks"][t]:
            if b["start"] >= f0:
                b["start"] += n
    for b in video:
        doc["tracks"]["video"].append({**b, "start": b["start"] + f0})
    for b in audio:
        doc["tracks"]["audio"].append({**b, "start": b["start"] + f0})
    for t in ("video", "audio"):
        doc["tracks"][t].sort(key=lambda b: b["start"])


def changed(old: dict | None, new: dict) -> dict:
    before = {(t, b["id"]): json.dumps(b, sort_keys=True) for t in model.TRACKS for b in (old or {"tracks": {t: [] for t in model.TRACKS}})["tracks"][t]}
    out = {t: [b["id"] for b in new["tracks"][t] if before.get((t, b["id"])) != json.dumps(b, sort_keys=True)] for t in model.TRACKS}
    out["removed"] = [i for (t, i) in before if not any(b["id"] == i for b in new["tracks"][t])]
    return out


# ---------------------------------------------------------------- agents


def _roles_block(project, doc: dict) -> str:
    from ..sync import load_sync
    lines = []
    sync = load_sync(project)
    for g in sync.get("groups", []):
        ms = ", ".join(f"{m['id']} ({ROLE_NAMES.get(model.role(doc, project, m['id']), '?')}, {model.duration(project, m['id']):.0f} с, смещение {m['offset']:+.2f})"
                       for m in g["members"])
        lines.append(f"{g['id']}: {ms}")
    singles = [s for s in sync.get("singles", []) if s in project.sources]
    tops = [f"{s} ({model.duration(project, s):.0f} с)" for s in singles if model.role(doc, project, s) == "top"]
    if tops:
        lines.append("несинхронные клипы «верх» (для перебивок): " + ", ".join(tops))
    others = [s for s in singles if model.role(doc, project, s) == "front"]
    if others:
        lines.append("отдельные дубли (фронт, без других камер): " + ", ".join(others))
    return "\n".join(lines) or "Синхронизации нет."


def _packed(project, sids: set[str] | None = None) -> str:
    from ..stages import _packed, _packed_sections
    return _packed_sections(project, sids) if sids else _packed(project)


def cut_ranges(project, doc: dict, prompt: str, sids: set[str] | None = None, note: str = "") -> tuple[list[dict], str]:
    """The cut agent → ranges with a picture role (or a cutaway) each, snapped to word boundaries."""
    usable = {sid for sid in project.sources if model.role(doc, project, sid) in ("front", "top", "side", "other")}
    prompt_parts = [
        "Собери монтаж по промпту." + (f" {note}" if note else ""),
        agent.wrap_data("промпт человека", prompt),
        agent.wrap_data("дубли и роли камер", _roles_block(project, doc)),
        agent.wrap_data("takes_packed.md", _packed(project, sids)),
    ]
    out: agent.QuickCut = agent.run("quickcut", "\n\n".join(prompt_parts), timeout=900, log_dir=project.edit)  # type: ignore[assignment]
    raw = []
    eff = model.offsets(project)
    for r in out.ranges:
        sid, start, end = r.source, r.start, r.end
        if sid in project.sources and model.role(doc, project, sid) == "lav":
            # cut on the lav transcript → the same moment on the synced camera
            take, e = eff[sid]
            cam = next((c for w in ("front", "side", "top", "other") if (c := model.covering(project, doc, take, start + e, end + e, w))), None)
            if not cam:
                continue
            shift = e - eff[cam][1]
            sid, start, end = cam, start + shift, end + shift
        if sid not in project.sources or sid not in usable:
            continue
        item = {"source": sid, "start": start, "end": end, "role": r.angle, "reason": r.reason}
        if r.broll_source in project.sources and r.broll_start is not None and model.role(doc, project, r.broll_source) == "top":
            item["picture"] = {"source": r.broll_source, "start": float(r.broll_start)}
        raw.append(item)
    if not raw:
        raise RuntimeError("Агент не вернул ни одного годного отрезка.")
    # word-boundary snapping (Hard Rules 6/7) through the full-mode normaliser, extra keys ride along
    e = edl.normalize(project, raw, [])
    return e["ranges"], out.summary


def run_cut(project, job: Job, prompt: str) -> dict:
    """Step 2: the first cut by prompt → a proposal (applying it approves the strategy)."""
    doc = model.current(project)
    if not doc:
        raise RuntimeError("Сначала загрузите исходники (шаг 1).")
    job.log.append("агент нарезки думает…")
    ranges, summary = cut_ranges(project, doc, prompt)
    new = json.loads(json.dumps(doc))
    video, audio, warn = blocks_from_ranges(project, new, ranges)
    new["tracks"]["video"], new["tracks"]["audio"] = video, audio
    new["tracks"]["slides"] = [s for s in new["tracks"]["slides"]]   # slides and cards stay where they were
    fps = fps_of(new)
    total = sum(b["len"] for b in video) / fps
    tops = sum(1 for v in video if model.role(new, project, v["src"]) == "top")
    prop = {"id": model.new_id("p"), "kind": "cut", "prompt": prompt, "created": time.strftime("%H:%M:%S"),
            "summary": f"{summary} Итог: {model.fmt(total)}, {len(video)} блоков, из них сверху {tops}.",
            "ops": [{"op": "cut"}], "warnings": warn, "base": doc["meta"].get("version_id"),
            "doc": model.normalize(project, new), "follow": {"spec": [], "draw": [], "requests": {}}}
    prop["changed"] = changed(doc, prop["doc"])
    proposal_path(project).write_text(json.dumps(prop, ensure_ascii=False), encoding="utf-8")
    job.note = prop["summary"][:120]
    return prop


def run_request(project, job: Job | None, prompt: str, sel: dict | None) -> dict:
    doc = model.current(project)
    if not doc or not doc["tracks"]["video"]:
        raise RuntimeError("Сначала соберите монтаж (шаг 2).")
    sf = selection_frames(doc, sel)
    plan = fast_parse(prompt, sf is not None)
    fast = plan is not None
    if plan is None:
        if job:
            job.log.append("агент разбирает просьбу…")
        parts = ["Разбери просьбу человека в операции.", agent.wrap_data("просьба", prompt)] + context(project, doc, sf)
        plan = agent.run("quick", "\n\n".join(parts), timeout=300, log_dir=project.edit)  # type: ignore[assignment]

    def recut(d: dict, f0: int, f1: int, text: str):
        fps = fps_of(d)
        sids = {a["src"] for a in d["tracks"]["audio"] if a["start"] < f1 and a["start"] + a["len"] > f0}
        sids |= {v["src"] for v in d["tracks"]["video"] if v["start"] < f1 and v["start"] + v["len"] > f0}
        eff = model.offsets(project)
        takes = {eff[s][0] for s in sids}
        sids |= {s for s, (g, _) in eff.items() if g in takes}
        ranges, _ = cut_ranges(project, d, text or prompt, sids, note=f"Только для отрезка {model.fmt(f0 / fps)}–{model.fmt(f1 / fps)}.")
        return blocks_from_ranges(project, d, ranges)

    new, warn, follow = apply_ops(project, doc, plan.ops, sf, run_recut=recut)
    new = model.normalize(project, new)
    prop = {"id": model.new_id("p"), "kind": "request", "prompt": prompt, "created": time.strftime("%H:%M:%S"), "fast": fast,
            "summary": plan.summary, "ops": [o.model_dump() for o in plan.ops], "warnings": list(plan.warnings) + warn,
            "base": doc["meta"].get("version_id"), "selection": sf, "doc": new, "follow": follow}
    prop["changed"] = changed(doc, new)
    proposal_path(project).write_text(json.dumps(prop, ensure_ascii=False), encoding="utf-8")
    if job:
        job.note = plan.summary[:120]
    return prop


def apply(project) -> tuple[dict, dict]:
    """«Применить»: the proposal becomes a new version (if the timeline didn't move on meanwhile)."""
    prop = load_proposal(project)
    if not prop:
        raise RuntimeError("Нет предложения для применения.")
    if prop.get("base") != model.head(project):
        drop_proposal(project)
        raise RuntimeError("Монтаж изменился, пока ИИ думал. Повторите запрос — он учтёт новые правки.")
    doc = prop["doc"]
    if prop["kind"] == "cut":
        s = edl.load_strategy(project) or {}
        text = f"Быстрый режим: {prop['prompt']}\n{prop['summary']}"
        edl.save_strategy(project, {**s, "status": "approved", "text": text, "quick": True, "revision": s.get("revision", 0) + 1,
                                    "data": {"sentences": [text], "beats": [], "takes": "", "slips": [], "graphics": [],
                                             "target_seconds": 0, "aspect": "16:9", "questions": [], "needs": []},
                                    "approved_hash": edl.text_hash(text), "approved_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        doc.setdefault("meta", {})["strategy_hash"] = edl.text_hash(text)
    vid = model.save(project, doc, "ai", ("ИИ: " + prop["prompt"])[:120])
    drop_proposal(project)
    return model.current(project), {**prop["follow"], "version": vid}


def summary_of(prop: dict | None) -> dict | None:
    """The proposal for the UI (without the full document)."""
    if not prop:
        return None
    return {k: prop.get(k) for k in ("id", "kind", "prompt", "summary", "ops", "warnings", "changed", "created", "fast", "selection")}


def preview_doc(project) -> dict | None:
    prop = load_proposal(project)
    return prop.get("doc") if prop else None
