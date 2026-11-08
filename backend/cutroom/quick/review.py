"""Review rounds: notes on timecodes → a round of edits → the journal. The process of the brand
reference (versions v4…v9 of «План запуска»), not its graphics.

  1. «Отправить на согласование»: the current version becomes round N — its document is snapshotted
     (the reference kept work/v3_backup/edl.json for exactly this) together with the file that went out.
  2. Notes: the customer's list «0:18–0:25 полный слайд…», ALWAYS in the timecodes of the version
     they watched. Each note is anchored by script to the SOURCE time under it in that snapshot
     (the sound block, else the picture block) — the reference's old2cam.
  3. The current cut may have moved on: every note is shown and sent to the agents at its time in
     the CURRENT version (cam2new), so «0:31» lands where that moment is now, not 0:31.
  4. «Собрать правки»: all open notes of a round go to the request agent in one pass → one proposal
     (the usual «Применить»/«Отмена») with an answer per note: done and how, or why not.
  5. Journal: per version a table «Что просил | Как сделано» in edit/project.md (managed block) and
     the list «Решения, которые стоит помнить» — pinned decisions every agent of this project reads.

edit/quick/review.json  {rounds, notes, decisions, journal}; edit/quick/rounds/r<N>.json — the snapshots.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import model
from .model import fps_of, qdir

TC = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2}(?:[.,]\d+)?)(?![\d:])")
RANGE_SEP = r"\s*(?:–|—|-|до|по)\s*"
KINDS = [  # script classification: what kind of edit a note asks for (display + routing hints); first match wins
    ("graphics", r"карточк|инфограф|плашк|слайд|шаг\w*\b|лент|дат[аыу]|подпис|титр|cta|заставк|афиш|субтитр|граф|надпис|логотип"),
    ("sound", r"звук|музык|громк|тише|громче|голос|шум|съехал"),
    ("color", r"цвет|тепл|холод|темн|светл|ярк|насыщ"),
    ("cut", r"вырез|убер|убра|сократ|начал[оа] с|начать с|начни|начина|закончи|рез\b|склейк|съеда|перенес|оставь|длинн|короч|пауз"),
    ("angle", r"ракурс|верх|стол\b|фронт|крупн|(?:крупн|общ|средн|верхн|боков)\w* план|камер"),
]
KIND_NAMES = {"graphics": "графика", "cut": "монтаж", "angle": "ракурс", "sound": "звук", "color": "цвет", "other": "другое"}


# ---------------------------------------------------------------- storage


def path(project) -> Path:
    return qdir(project) / "review.json"


def load(project) -> dict:
    try:
        r = json.loads(path(project).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        r = {}
    for k in ("rounds", "notes", "decisions", "journal"):
        r.setdefault(k, [])
    return r


def save(project, r: dict) -> None:
    tmp = path(project).with_suffix(".tmp")
    tmp.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path(project))


def snapshot(project, n: int) -> dict | None:
    try:
        return json.loads((qdir(project) / "rounds" / f"r{n}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- old2cam / cam2new


def to_source(doc: dict, t: float) -> dict | None:
    """Film second t of `doc` → {src, t} in the source under it (the sound first: the speech is the clock)."""
    fps = fps_of(doc)
    f = t * fps
    for track in ("audio", "video"):
        for b in doc["tracks"][track]:
            if b["start"] <= f < b["start"] + b["len"]:
                return {"src": b["src"], "t": round(b["in"] + (f - b["start"]) / fps, 4), "track": track}
    return None


def to_film(doc: dict, an: dict | None) -> float | None:
    """{src, t} → film second in `doc`; a moment that was cut out goes to the next kept moment of the same file."""
    return to_film_ex(doc, an)[0]


def to_film_ex(doc: dict, an: dict | None) -> tuple[float | None, bool]:
    """(film second, exact): exact = False when the moment itself was cut out and the next kept one is given."""
    if not an:
        return None, False
    fps = fps_of(doc)
    order = ("audio", "video") if an.get("track") != "video" else ("video", "audio")
    for track in order:
        for b in doc["tracks"][track]:
            if b["src"] == an["src"] and b["in"] - 1e-4 <= an["t"] < b["in"] + b["len"] / fps:
                return round((b["start"] + (an["t"] - b["in"]) * fps) / fps, 3), True
    later = [(b["in"] - an["t"], b) for track in order for b in doc["tracks"][track] if b["src"] == an["src"] and b["in"] > an["t"]]
    if later:
        gap, b = min(later, key=lambda x: x[0])
        if gap <= 30:
            return round(b["start"] / fps, 3), False
    return None, False


# ---------------------------------------------------------------- rounds


def send(project, label: str = "") -> dict:
    """The current version goes out for review: round N = a snapshot of it + the file the customer gets."""
    from . import build
    doc = model.current(project)
    if not doc or not doc["tracks"]["video"]:
        raise RuntimeError("Нечего отправлять: в быстром режиме нет монтажа.")
    r = load(project)
    vid = doc["meta"]["version_id"]
    if r["rounds"] and r["rounds"][-1]["version"] == vid:
        return r["rounds"][-1]
    n = (r["rounds"][-1]["n"] + 1) if r["rounds"] else 1
    d = qdir(project) / "rounds"
    d.mkdir(exist_ok=True)
    (d / f"r{n}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    fin, drf = build.last(project, "final"), build.last(project, "draft")
    out = next((x for x in (fin, drf) if x and x.get("version") == vid and x.get("file")), None)
    rd = {"n": n, "version": vid, "sent": time.strftime("%Y-%m-%d %H:%M"), "file": (out or {}).get("file"),
          "duration": round(model.total_frames(doc) / fps_of(doc), 2), "label": label[:80]}
    r["rounds"].append(rd)
    save(project, r)
    return rd


def kind_of(text: str) -> str:
    low = text.lower()
    return next((k for k, rx in KINDS if re.search(rx, low)), "other")


def _secs(m) -> float:
    h, mm, ss = m.group(1), m.group(2), m.group(3).replace(",", ".")
    return (int(h) * 3600 if h else 0) + int(mm) * 60 + float(ss)


def parse(text: str) -> list[dict]:
    """The customer's list → notes. «0:18–0:25 …», «с 2:40 …», «на 1:45 …», «2:53 убрать кадр»; a line
    without a timecode is a note for the whole film; «1.», «-», «•» in front are dropped."""
    out = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*(?:\d{1,2}[.)]\s+|[-•*–]\s*)", "", raw).strip()
        if not line:
            continue
        ms = list(TC.finditer(line))
        t0 = t1 = None
        if ms:
            t0 = _secs(ms[0])
            if len(ms) > 1 and re.fullmatch(RANGE_SEP, line[ms[0].end():ms[1].start()] or "", flags=re.I):
                t1 = _secs(ms[1])
        out.append({"t0": t0, "t1": t1 if t1 is not None and t1 > (t0 or 0) else None, "text": line[:400], "kind": kind_of(line)})
    return out


def add_notes(project, text: str, round_n: int | None = None, at: float | None = None) -> list[dict]:
    """Notes in the timecodes of round `round_n` (default: the latest sent round; none sent → the
    current version). `at` = one note at that second of the CURRENT version (a click on the timeline)."""
    r = load(project)
    cur = model.current(project)
    if not cur:
        raise RuntimeError("Нет монтажа.")
    if at is not None:
        items, base, rn = [{"t0": float(at), "t1": None, "text": text.strip()[:400], "kind": kind_of(text)}], cur, 0
    else:
        rn = round_n if round_n is not None else (r["rounds"][-1]["n"] if r["rounds"] else 0)
        base = snapshot(project, rn) if rn else cur
        if base is None:
            raise RuntimeError(f"Нет снимка версии раунда {rn}.")
        items = parse(text)
    made = []
    for it in items:
        note = {"id": model.new_id("n"), "round": rn, "version": base["meta"].get("version_id"), **it,
                "a0": to_source(base, it["t0"]) if it["t0"] is not None else None,
                "a1": to_source(base, it["t1"]) if it["t1"] is not None else None,
                "status": "open", "how": "", "done_in": None, "created": time.strftime("%Y-%m-%d %H:%M")}
        r["notes"].append(note)
        made.append(note)
    save(project, r)
    return made


def with_now(project, notes: list[dict], doc: dict | None = None) -> list[dict]:
    """Each note + where it is in the CURRENT version (now0/now1), and whether that moment was cut out."""
    doc = doc or model.current(project)
    out = []
    for n in notes:
        (now0, ex0), (now1, ex1) = (to_film_ex(doc, n.get("a0")), to_film_ex(doc, n.get("a1"))) if doc else ((None, False), (None, False))
        same = n.get("round") == 0 and n.get("version") == (doc or {}).get("meta", {}).get("version_id")
        if same:
            now0, now1, ex0, ex1 = n.get("t0"), n.get("t1"), True, True
        # the whole moment is gone: its start was cut out and so was its end (or it had none)
        gone = n.get("t0") is not None and (now0 is None or (not ex0 and (n.get("a1") is None or not ex1)))
        out.append({**n, "now0": now0, "now1": now1, "gone": gone,
                    "moved": now0 is not None and n.get("t0") is not None and abs(now0 - n["t0"]) > 0.25})
    return out


def state(project) -> dict:
    r = load(project)
    doc = model.current(project)
    return {"rounds": r["rounds"], "notes": with_now(project, r["notes"], doc), "decisions": r["decisions"], "journal": r["journal"][-20:],
            "current_version": (doc or {}).get("meta", {}).get("version_id"), "kinds": KIND_NAMES}


def update_note(project, nid: str, patch: dict) -> dict:
    r = load(project)
    n = next((x for x in r["notes"] if x["id"] == nid), None)
    if not n:
        raise KeyError(nid)
    for k in ("status", "how", "text", "kind"):
        if k in patch and patch[k] is not None:
            n[k] = str(patch[k])[:400]
    if patch.get("status") in ("done", "rejected"):
        n["done_in"] = (model.current(project) or {}).get("meta", {}).get("version_id")
    save(project, r)
    return n


def delete_note(project, nid: str) -> None:
    r = load(project)
    r["notes"] = [x for x in r["notes"] if x["id"] != nid]
    save(project, r)


# ---------------------------------------------------------------- decisions (the reference's «Решения, которые стоит помнить»)


def add_decision(project, text: str, from_note: str | None = None) -> dict:
    r = load(project)
    d = {"id": model.new_id("d"), "text": text.strip()[:300], "from_note": from_note, "when": time.strftime("%Y-%m-%d %H:%M")}
    r["decisions"].append(d)
    save(project, r)
    write_journal(project)
    return d


def delete_decision(project, did: str) -> None:
    r = load(project)
    r["decisions"] = [x for x in r["decisions"] if x["id"] != did]
    save(project, r)
    write_journal(project)


def decisions_text(project) -> str:
    """For every agent of this project (prompt context)."""
    ds = load(project)["decisions"]
    return "\n".join(f"- {d['text']}" for d in ds)


# ---------------------------------------------------------------- the round of edits


def round_prompt(project, ids: list[str] | None = None) -> tuple[str, list[dict]]:
    """The open notes, numbered, in the CURRENT version's timecodes — for the request agent."""
    st = state(project)
    notes = [n for n in st["notes"] if n["status"] in ("open", "question") and (not ids or n["id"] in ids)]
    if not notes:
        raise RuntimeError("Открытых пометок нет.")
    lines = []
    for i, n in enumerate(notes, start=1):
        if n["t0"] is None:
            where = "весь ролик"
        elif n["gone"]:
            where = f"(было {model.fmt(n['t0'])} в v{n['version']}: этот момент уже вырезан)"
        else:
            where = model.fmt(n["now0"]) + (f"–{model.fmt(n['now1'])}" if n.get("now1") is not None else "")
        lines.append(f"{i}. [{where}] ({KIND_NAMES.get(n['kind'], n['kind'])}) {n['text']}")
    return "\n".join(lines), notes


def answers_to_notes(project, notes: list[dict], answers: list, version: int | None) -> list[dict]:
    """The agent's answer per note (n, done, how) → statuses; the journal row of this version."""
    r = load(project)
    by_id = {x["id"]: x for x in r["notes"]}
    rows = []
    for i, n in enumerate(notes, start=1):
        a = next((x for x in answers if int((x.get("n") if isinstance(x, dict) else x.n) or 0) == i), None)
        a = a if isinstance(a, dict) or a is None else a.model_dump()
        x = by_id.get(n["id"])
        if not x:
            continue
        if a:
            x["status"] = "done" if a.get("done") else "question"
            x["how"] = (a.get("how") or "")[:400]
            if a.get("done"):
                x["done_in"] = version
        rows.append({"asked": n["text"], "done": x.get("how") or "—", "status": x["status"], "at": n.get("t0"), "round": n.get("round")})
    if rows:
        r["journal"].append({"version": version, "when": time.strftime("%Y-%m-%d %H:%M"), "rows": rows})
    save(project, r)
    write_journal(project)
    return rows


# ---------------------------------------------------------------- edit/project.md (managed block)


BEGIN, END = "<!-- cutroom-review -->", "<!-- /cutroom-review -->"


def write_journal(project) -> Path:
    r = load(project)
    lines = [BEGIN, "## Журнал правок (Cutroom)", "",
             "Таймкоды в «Что просил» — по версии, которую смотрел заказчик; Cutroom переносит их на текущую версию по исходникам.", ""]
    for rd in r["rounds"]:
        lines.append(f"- Раунд {rd['n']}: v{rd['version']} отправлена {rd['sent']}" + (f" — {rd['label']}" if rd.get("label") else "")
                     + (f" (`{Path(rd['file']).name}`)" if rd.get("file") else ""))
    if r["rounds"]:
        lines.append("")
    for j in r["journal"]:
        lines += [f"### v{j['version']} — {j['when']}", "", "| Что просил | Как сделано |", "|---|---|"]
        for row in j["rows"]:
            tc = f"{model.fmt(row['at'])} " if row.get("at") is not None else ""
            mark = "" if row["status"] == "done" else " ⚠"
            lines.append(f"| {tc}{row['asked'].replace('|', '/')} | {row['done'].replace('|', '/')}{mark} |")
        lines.append("")
    if r["decisions"]:
        lines += ["### Решения, которые стоит помнить", ""] + [f"- {d['text']}" for d in r["decisions"]] + [""]
    lines.append(END)
    block = "\n".join(lines)
    pm = project.edit / "project.md"
    text = pm.read_text(encoding="utf-8", errors="replace") if pm.exists() else ""
    if BEGIN in text and END in text:
        text = text[:text.index(BEGIN)] + block + text[text.index(END) + len(END):]
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"
    pm.write_text(text, encoding="utf-8")
    return pm


# ---------------------------------------------------------------- graphics stay on their words through a re-cut


def anchor_graphics(doc: dict) -> dict:
    """Slides and cards → the source moment under their start (before a re-cut)."""
    out = {}
    for t in ("slides", "overlays"):
        for b in doc["tracks"][t]:
            a = to_source(doc, b["start"] / fps_of(doc))
            if a:
                out[b["id"]] = a
    return out


def replace_graphics(old_anchors: dict, new: dict) -> list[str]:
    """After a re-cut: each slide / card back on its moment of the speech (the reference: «при любой
    перенарезке графика остаётся на своих словах»). One whose moment was cut out keeps its frame."""
    fps = fps_of(new)
    warn = []
    for t in ("slides", "overlays"):
        for b in new["tracks"][t]:
            an = old_anchors.get(b["id"])
            if not an:
                continue
            s = to_film(new, an)
            if s is None:
                warn.append(f"{'слайд' if t == 'slides' else 'карточка'} {b.get('page') or b['id']}: её момент вырезан — осталась на месте")
                continue
            b["start"] = max(0, int(round(s * fps)))
    return warn
