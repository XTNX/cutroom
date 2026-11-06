"""Stage-4 smoke test on a small fixture take (no LLM needed).

Runs its own backend on a spare port against <fixture_dir>, then checks:
  1. ingest (proxy, audio, ASR with the configured engine) and angle sync
  2. the gate: PUT /api/edl and /api/render/preview are refused before approval (Hard Rule 11)
  3. an eval-authored strategy is approved; the editor agent job may fail without a Claude login —
     the test then supplies a hand-made EDL (as the agent would)
  4. every range edge sits on a word boundary ± padding (Hard Rules 6/7)
  5. undo/redo move between versions
  6. background render produces preview.mp4 whose duration matches the EDL (±0.25 s)
  7. a lavalier/recorder file (fixture ZOOM_0001.wav, started 7.5 s before the camera) is found by
     sync and becomes the take's speech mic automatically
  8. A/V sync of the render: per segment, rendered sound vs the camera sound under the picture,
     |offset| ≤ 5 ms (frame-exact segment starts)
  9. markers survive a re-cut; the fragment endpoint is gated like everything else
 10. colour per episode + exact graded frame; infographic slot drawn from a spec, agent-built card
     (when Claude is logged in), brand palette, and the card composited into the verified render

Usage: backend/.venv/Scripts/python evals/stage4_smoke.py D:/Video/cutroom-evals/take8
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8799
TOKEN = "eval"
BASE = f"http://127.0.0.1:{PORT}"


def call(method: str, path: str, body=None, ok=(200,)):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-Cutroom-Token": TOKEN, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return e.code, json.loads(e.read() or b"null")
        return e.code, json.loads(e.read() or b"null")


def wait_jobs(kinds: set[str] | None = None, timeout: float = 1800) -> list[dict]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, jobs = call("GET", "/api/jobs")
        act = [j for j in jobs if j["status"] in ("queued", "running") and (not kinds or j["kind"] in kinds)]
        if not act:
            return jobs
        time.sleep(2)
    raise TimeoutError(f"jobs {kinds} still running")


def av_offsets(edit: Path) -> list[float]:
    """Rendered sound vs camera sound under the picture, per segment, in ms (GCC-PHAT).
    Segment starts are frame-exact, as render_cutroom.py cuts them on the cumulative EDL clock."""
    from fractions import Fraction

    import numpy as np
    sys.path.insert(0, str(ROOT / "backend"))
    from cutroom.sync import gcc_phat

    def load(path, ss, t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ss:.4f}", "-t", f"{t:.3f}", "-i", str(path), "-ac", "1",
                              "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.float32)

    r = json.loads((edit / "edl_render.json").read_text(encoding="utf-8"))
    # the same clock as render_cutroom.py (video-use probe_source_fps → avg_frame_rate, e.g. 24.998 for a phone's "25")
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate",
                           "-of", "csv=p=0", r["sources"][r["ranges"][0]["source"]]], capture_output=True, text=True).stdout.strip()
    fps = float(Fraction(rate))
    out, clock, frames = [], 0.0, 0
    for x in r["ranges"]:
        start_out = frames / fps
        dur = x["end"] - x["start"]
        clock += dur
        frames += max(1, round(clock * fps) - frames)
        w = min(2.0, dur - 0.4)
        if w < 0.8:
            continue
        a = load(edit / "preview.mp4", start_out + 0.2, w)
        b = load(r["sources"][x["source"]], x["start"] + 0.2, w)
        n = min(len(a), len(b))
        res, sharp = gcc_phat(a[:n], b[:n], 0.3)
        out.append(round(res * 1000, 1))
    return out


def check(cond: bool, msg: str, results: list):
    results.append((bool(cond), msg))
    print(("PASS " if cond else "FAIL ") + msg, flush=True)


def main():
    fixture = Path(sys.argv[1]).resolve()
    results: list = []
    py = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    srv = subprocess.Popen([str(py), "-m", "cutroom.server", "--port", str(PORT), "--token", TOKEN],
                           cwd=str(ROOT / "backend"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace")
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(BASE + "/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        st, proj = call("POST", "/api/project/open", {"path": str(fixture)})
        check(st == 200 and len(proj["sources"]) >= 2, f"проект открыт: {len(proj.get('sources', []))} источника", results)

        call("POST", "/api/project/ingest", {})
        jobs = wait_jobs()
        errs = [j for j in jobs if j["status"] == "error"]
        check(not errs, "приём без ошибок" + (f": {errs[0]['error'][:200]}" if errs else ""), results)
        _, srcs = call("GET", "/api/sources")
        check(all(s["has"]["proxy"] and s["has"]["transcript"] for s in srcs), "у всех источников есть прокси и расшифровка", results)

        call("POST", "/api/sync")
        wait_jobs({"sync"})
        _, sync = call("GET", "/api/sync")
        check(len(sync["groups"]) >= 1, f"ракурсы найдены: {[len(g['members']) for g in sync['groups']]}", results)
        lav = next((m for g in sync["groups"] for m in g["members"] if m["id"] == "ZOOM_0001"), None)
        if (fixture / "ZOOM_0001.wav").exists():
            check(lav is not None and abs(lav["offset"] + 7.5) < 0.01,
                  f"петличка найдена по звуку: смещение {lav['offset'] if lav else '—'} с (ожидалось −7.5)", results)

        # --- gate closed (reset a strategy left by an earlier run)
        (fixture / "edit" / "strategy.json").unlink(missing_ok=True)
        st, _ = call("PUT", "/api/edl", {"ranges": [{"source": srcs[0]["id"], "start": 1, "end": 3}]})
        check(st == 423, f"PUT /api/edl до утверждения отклонён (HTTP {st})", results)
        st, _ = call("POST", "/api/render/preview")
        check(st == 423, f"рендер до утверждения отклонён (HTTP {st})", results)
        st, _ = call("POST", "/api/edl/fragment", {"start": 0, "end": 0, "request": "короче"})
        check(st == 423, f"правка фрагмента агентом до утверждения отклонена (HTTP {st})", results)

        # --- eval-authored strategy, approved through the real endpoint
        edit = fixture / "edit"
        (edit / "strategy.json").write_text(json.dumps({
            "status": "draft", "revision": 1, "author": "eval",
            "text": "Тестовая стратегия eval: три фразы из опорного ракурса, без инфографики, без цвета, без субтитров.",
            "data": {"sentences": ["eval"], "beats": [], "takes": "", "slips": [], "graphics": [], "target_seconds": 20,
                     "aspect": "16:9", "questions": [], "needs": []}}, ensure_ascii=False), encoding="utf-8")
        st, ap = call("POST", "/api/strategy/approve")
        check(st == 200 and ap["strategy"]["gate_open"], "ворота открываются только через «Утвердить»", results)
        jobs = wait_jobs({"editor"}, timeout=900)
        ed = [j for j in jobs if j["kind"] == "editor"][-1]
        agent_ok = ed["status"] == "done"
        print(f"INFO агент-редактор: {ed['status']} {('— ' + ed['error'][:160]) if ed.get('error') else ''}")

        # --- hand-made EDL from the reference angle's transcript: three phrases
        ref = sync["groups"][0]["ref"]
        tr = json.loads((edit / "transcripts" / f"{ref}.json").read_text(encoding="utf-8"))
        words = [w for w in tr["words"] if w["type"] == "word"]
        third = len(words) // 3
        picks = [(words[2]["start"] + 0.07, words[9]["end"] - 0.05),
                 (words[third]["start"], words[third + 8]["end"]),
                 (words[2 * third]["start"] - 0.04, words[2 * third + 10]["end"] + 0.02)]
        ranges = [{"source": ref, "start": a, "end": b, "beat": f"B{i + 1}", "reason": "eval"} for i, (a, b) in enumerate(picks)]
        st, e1 = call("PUT", "/api/edl", {"ranges": ranges, "note": "eval v1"})
        check(st == 200 and e1["edl"], f"EDL сохранён (v{e1['edl']['cutroom']['version_id'] if e1.get('edl') else '?'})", results)
        pad = e1["edl"]["cutroom"]["pad_s"]
        starts = [w["start"] for w in tr["words"] if w["type"] in ("word", "audio_event")]
        ends = [w["end"] for w in tr["words"] if w["type"] in ("word", "audio_event")]
        okb = True
        for r in e1["edl"]["ranges"]:
            ds = min(abs(r["start"] + pad - s) for s in starts)
            de = min(abs(r["end"] - pad - e) for e in ends)
            near_s = min(abs(r["start"] - s) for s in starts) <= pad + 1e-3
            near_e = min(abs(r["end"] - e) for e in ends) <= pad + 1e-3
            okb &= (ds < 0.002 or near_s) and (de < 0.002 or near_e)
        check(okb, f"края на границах слов ± паддинг {int(pad * 1000)} мс", results)

        # a marker anchored to material keeps its place through a re-cut
        mt = e1["edl"]["ranges"][1]["start"] + 0.5
        _, mk = call("POST", "/api/markers", {"source": ref, "time": mt, "note": "eval"})
        if (fixture / "ZOOM_0001.wav").exists():
            _, au = call("GET", "/api/audio")
            eff = [g["effective"] for g in au["groups"]]
            check("ZOOM_0001" in eff, f"звук дубля берётся с петлички автоматически ({eff})", results)

        # split + delete + undo/redo
        r0 = e1["edl"]["ranges"][0]
        mid = (r0["start"] + r0["end"]) / 2
        split = [dict(r0, end=mid), dict(r0, start=mid)] + e1["edl"]["ranges"][1:]
        _, e2 = call("PUT", "/api/edl", {"ranges": split, "note": "eval split"})
        check(len(e2["edl"]["ranges"]) == 4, "разрез даёт 4 фрагмента", results)
        a, b = e2["edl"]["ranges"][0], e2["edl"]["ranges"][1]
        check(abs(a["end"] - b["start"]) < 1e-6 and abs((a["end"] - a["start"]) + (b["end"] - b["start"]) - (r0["end"] - r0["start"])) < 1e-3,
              f"разрез ничего не теряет и не повторяет (стык {a['end']:.3f} = {b['start']:.3f})", results)
        _, e3 = call("POST", "/api/edl/undo")
        check(len(e3["edl"]["ranges"]) == 3, "undo возвращает 3 фрагмента", results)
        _, e4 = call("POST", "/api/edl/redo")
        check(len(e4["edl"]["ranges"]) == 4, "redo снова 4", results)

        # colour: an episode grade beats the whole-cut grade; the exact frame comes from ffmpeg
        st, ec = call("PUT", "/api/color", {"grade": {"preset": "warm_cinematic", "temperature": 30}, "start": 1, "end": 1})
        rs = ec["edl"]["ranges"] if st == 200 else []
        check(st == 200 and rs[1].get("color", {}).get("preset") == "warm_cinematic" and not rs[0].get("color"),
              "цвет задаётся выбранному эпизоду, соседние не тронуты", results)
        req = urllib.request.Request(f"{BASE}/media/frame?t={TOKEN}&source={ref}&time={rs[1]['start'] + 0.5:.3f}&graded=1&g="
                                     + urllib.parse.quote(json.dumps({"preset": "warm_cinematic"})), headers={"X-Cutroom-Token": TOKEN})
        with urllib.request.urlopen(req, timeout=60) as r:
            jpg = r.read()
        check(jpg[:2] == b"\xff\xd8" and len(jpg) > 5000, f"кадр «после» с цветом отдаётся ({len(jpg) // 1024} КБ)", results)

        # infographics: a slot gets a card (hand spec — no LLM), is drawn, and lands in the render as an overlay
        total = ec["edl"]["total_duration_s"]
        slot_t = round(min(total - 4.0, ec["edl"]["ranges"][1]["out_start"] + 0.5), 2)
        st, eg = call("POST", "/api/gfx/add", {"at": slot_t, "type": "list", "duration": 3.5})
        sid = eg["edl"]["slots"][-1]["id"]
        spec = {"type": "list", "title": "Три шага", "items": [{"text": "Снять"}, {"text": "Смонтировать"}, {"text": "Показать"}]}
        call("PUT", f"/api/gfx/{sid}", {"spec": spec})
        wait_jobs({"slot-render"}, timeout=300)
        sl = next(x for x in call("GET", "/api/gfx")[1]["slots"] if x["id"] == sid)
        okf = sl["status"] == "ready" and all(Path(sl["files"][k]).exists() for k in ("render", "preview", "poster"))
        check(okf, f"слот {sid} нарисован: {sl['status']} {sl.get('error') or ''}", results)
        if agent_ok:
            call("POST", "/api/gfx/add", {"at": 0.3, "type": "title", "duration": 3.0, "spec_hint": "титр: о чём ролик"})
            sid2 = call("GET", "/api/gfx")[1]["slots"][-1]["id"]
            call("POST", "/api/gfx/build", {"ids": [sid2]})
            wait_jobs({"slot", "slot-render"}, timeout=600)
            s2 = next(x for x in call("GET", "/api/gfx")[1]["slots"] if x["id"] == sid2)
            check(s2["status"] == "ready" and isinstance(s2.get("spec"), dict),
                  f"агент слота построил карточку: {(s2.get('spec') or {}).get('type')} «{(s2.get('spec') or {}).get('title', '')}» {s2.get('error') or ''}", results)
        st, eb = call("PUT", "/api/brand", {"brand": {"accent": "#3dbbe8"}})
        check(st == 200 and eb["edl"]["brand"]["accent"] == "#3dbbe8" and eb["edl"]["brand"]["confirmed"], "палитра бренда сохраняется в EDL", results)
        wait_jobs({"slot-render"}, timeout=300)
        e4 = call("GET", "/api/edl")[1]

        # verified preview render
        call("POST", "/api/render/preview")
        jobs = wait_jobs({"preview"}, timeout=1800)
        pj = [j for j in jobs if j["kind"] == "preview"][-1]
        check(pj["status"] == "done", f"render.py --preview: {pj['status']} {pj.get('error') or ''}"[:300], results)
        if pj["status"] == "done":
            d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                      str(edit / "preview.mp4")], capture_output=True, text=True).stdout.strip())
            want = e4["edl"]["total_duration_s"]
            check(abs(d - want) <= 0.25, f"длительность превью {d:.2f} с ≈ EDL {want:.2f} с", results)
            offs = av_offsets(edit)
            check(offs and max(abs(o) for o in offs) <= 5.0, f"звук совпадает с картинкой по фрагментам: {offs} мс", results)
            rnd = json.loads((edit / "edl_render.json").read_text(encoding="utf-8"))
            check(any(sid in o["file"] for o in rnd.get("overlays", [])), f"готовые карточки попадают в рендер: {len(rnd.get('overlays', []))} оверлея", results)

            def region_mean(t: float) -> float:   # right-hand card area of the rendered frame
                raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(edit / "preview.mp4"), "-frames:v", "1",
                                      "-vf", "crop=iw*0.38:ih*0.5:iw*0.58:ih*0.25,scale=32:16,format=gray", "-f", "rawvideo", "-"],
                                     capture_output=True).stdout
                return sum(raw) / max(1, len(raw))
            on, off = region_mean(slot_t + 2.2), region_mean(slot_t - 1.0)
            check(abs(on - off) > 6, f"карточка видна в кадре превью (яркость зоны {off:.0f} → {on:.0f})", results)
        ms = call("GET", "/api/markers")[1]
        mine = next((m for m in ms if m["id"] == mk["id"]), None)
        check(mine is not None and abs(mine["time"] - mt) < 1e-3, "маркер остался на месте после перемонтажа", results)
        call("DELETE", f"/api/markers/{mk['id']}")
        print(f"\n{sum(1 for ok, _ in results if ok)}/{len(results)} проверок пройдено; агент-редактор: {'работает' if agent_ok else 'не проверен (нет входа в Claude)'}")
        return 0 if all(ok for ok, _ in results) else 1
    finally:
        try:
            call("POST", "/api/shutdown")
        except Exception:
            pass
        time.sleep(1)
        if srv.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(srv.pid)], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
