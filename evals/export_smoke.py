"""Export smoke test — the 4K master (HEVC Main10) and the flat music bed (−29 LUFS, no side-chain).

Usage:  backend/.venv/Scripts/python evals/export_smoke.py <empty scratch dir>
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(ROOT / "appdata")
os.environ["HOME"] = os.environ["USERPROFILE"] = str(ROOT / "home")
(ROOT / "appdata" / "Cutroom").mkdir(parents=True, exist_ok=True)
(ROOT / "appdata" / "Cutroom" / "config.json").write_text(json.dumps({"prerender_final": False}), encoding="utf-8")
PROJ = ROOT / "Проект"
PROJ.mkdir(exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from cutroom import edl  # noqa: E402
from cutroom.jobs import Queue  # noqa: E402
from cutroom.project import Project  # noqa: E402
from cutroom.quick import build, model  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    fails += 0 if cond else 1


def main() -> None:
    f, music = PROJ / "front.mp4", ROOT / "music.wav"
    if not f.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25", "-f", "lavfi", "-i", "sine=frequency=330",
                        "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f)], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-t", "10", str(music)], check=True)
    q = Queue()
    p = Project(PROJ, q)
    p.scan()
    sid = next(iter(p.sources))
    d = model.empty("25/1")
    d["roles"] = {sid: "front"}
    d["tracks"]["video"] = [{"id": "v1", "start": 0, "len": 100, "src": sid, "in": 0.0}]
    d["tracks"]["audio"] = [{"id": "a1", "start": 0, "len": 100, "src": sid, "in": 0.0, "style": "natural", "link": "v1"}]
    d["music"] = {"track": "t", "path": str(music), "title": "тест", "flat": True}
    text = "тест экспорта"
    edl.save_strategy(p, {"status": "approved", "text": text, "approved_hash": edl.text_hash(text), "quick": True})
    model.save(p, d, "eval", "монтаж")

    st = build.run_sync(p, "master", log=lambda m: None)
    out = Path(st["file"])
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,profile,pix_fmt,width,height", "-of", "csv=p=0",
                         str(out)], capture_output=True, text=True).stdout.strip()
    check(out.name.endswith("_4K_master.mp4") and pr.startswith("hevc,Main 10") and "3840,2160" in pr, f"мастер 4K: {out.name} — {pr}")

    # the bed alone: the voice track is a 330 Hz tone, the bed 220 Hz — measure the 220 Hz band of the mix
    work = PROJ / "edit" / "quick" / "work"
    mix = work / "mix.wav"
    check(mix.exists(), "ровная подложка смикширована")
    m = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(mix), "-af", "bandpass=f=220:w=40,ebur128", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    il = re.findall(r"I:\s+(-?[\d.]+) LUFS", m)
    lvl = float(il[-1]) if il else 0.0
    check(-36 < lvl < -26, f"подложка около −29 LUFS (полоса 220 Гц): {lvl:.1f}")
    q.shutdown()
    print(f"\n{'все проверки пройдены' if not fails else f'{fails} проверок не прошло'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
