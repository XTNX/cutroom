"""Scripted work without the app and without any model: renders, pre-renders, the emergency export,
the knowledge base. Everything a render needs is already decided in the project files.

    python -m cutroom.cli prerender  D:/Video/бф [--profile draft|final]   render missing segments only
    python -m cutroom.cli build      D:/Video/бф                           draft preview (720p)
    python -m cutroom.cli export     D:/Video/бф                           final 1080p + KB note
    python -m cutroom.cli emergency  D:/Video/бф                           export with fallbacks (see emergency.py)
    python -m cutroom.cli kb-start   D:/Video/бф                           re-read the knowledge base for a project
    python -m cutroom.cli kb-index                                         index the brand book, rewrite the asset index note
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _project(path: str):
    from .jobs import Queue
    from .project import Project
    return Project(Path(path), Queue())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cutroom.cli", description="Cutroom: скрипты без ИИ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prerender", "build", "export", "emergency", "kb-start"):
        s = sub.add_parser(name)
        s.add_argument("project")
        if name == "prerender":
            s.add_argument("--profile", choices=("draft", "final"), default="final")
        if name in ("prerender", "build", "export"):
            s.add_argument("--workers", type=int, default=2)
    sub.add_parser("kb-index")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "kb-index":
            from . import kb
            n = kb.index_brandbook()
            print(f"брендбук: {len(n)} записей; индекс: {kb.write_index_note() or 'хранилище Obsidian не найдено'}")
            return 0
        p = _project(a.project)
        if a.cmd == "kb-start":
            from . import kb
            print(json.dumps(kb.project_start(p), ensure_ascii=False, indent=1))
        elif a.cmd == "prerender":
            from .quick import build, model
            doc = model.current(p)
            if not doc:
                raise RuntimeError("нет монтажа быстрого режима")
            segs = build.segments(p, doc, a.profile)
            need = build.missing(segs)
            print(f"{a.profile}: сегментов {len(segs)}, нужно отрендерить {len(need)}")
            import subprocess
            from concurrent.futures import ThreadPoolExecutor

            def one(s):
                r = subprocess.run(build.block_cmd(s), capture_output=True, text=True)
                return (s["block"] or "пауза", r.returncode)

            with ThreadPoolExecutor(max(1, a.workers)) as ex:
                for name, rc in ex.map(one, need):
                    print(f"  {name}: {'ok' if rc == 0 else f'ошибка {rc}'}")
        elif a.cmd in ("build", "export"):
            from .quick import build
            st = build.run_sync(p, "draft" if a.cmd == "build" else "final", workers=a.workers)
            print(st["file"])
            if a.cmd == "export":
                from . import kb
                print("заметка:", kb.after_render(p, st, "quick") or "хранилище Obsidian не найдено (запись в индексе)")
        elif a.cmd == "emergency":
            from . import emergency
            st = emergency.export(p)
            print(st["file"])
            for s in st.get("skipped") or []:
                print("  не вошло:", s)
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"ошибка: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
