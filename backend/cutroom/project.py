"""A Cutroom project = one <videos_dir>. Every artifact lives in <videos_dir>/edit/ (Hard Rule 12).

edit/
  cutroom.db            sources, job history, (later) EDL versions
  proxies/<id>.mp4      540p short-GOP proxies
  audio16k/<id>.wav     16 kHz mono for ASR / waveform / sync
  waveforms/<id>.peaks  uint8 envelope, 100 per second
  thumbs/<id>.jpg
  transcripts/<id>.json Scribe-shaped, word-level verbatim
  cache/transcripts/    keyed transcript cache
  takes_packed.md       from video-use pack_transcripts.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import media, system
from .config import (AUDIO_EXTS, CUTROOM_HELPERS, MEDIA_EXTS, PYTHON, VIDEO_USE_HELPERS,
                     load_settings, save_settings)
from .jobs import Job, Queue

SCHEMA = """
create table if not exists sources (
  id text primary key, path text not null, hash text, probe text, added real,
  duplicate_of text, audio_track integer default 0, excluded integer default 0
);
create table if not exists job_history (
  id integer, kind text, source text, status text, started real, finished real,
  elapsed real, error text, note text
);
create table if not exists stats (key text primary key, value real);
"""


class Project:
    def __init__(self, videos_dir: Path, queue: Queue) -> None:
        self.dir = videos_dir.resolve()
        if not self.dir.is_dir():
            raise FileNotFoundError(f"папка не найдена: {self.dir}")
        self.edit = self.dir / "edit"
        for sub in ("proxies", "audio16k", "waveforms", "thumbs", "transcripts", "cache/transcripts", "verify"):
            (self.edit / sub).mkdir(parents=True, exist_ok=True)
        self.queue = queue
        self._db_lock = threading.Lock()
        self.db = sqlite3.connect(self.edit / "cutroom.db", check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.sources: dict[str, dict] = {}
        self._load_sources()

    # ---------------------------------------------------------------- persistence

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._db_lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
            self.db.commit()
            return rows

    def _load_sources(self) -> None:
        for row in self._q("select id, path, hash, probe, added, duplicate_of, audio_track, excluded from sources"):
            sid, path, h, probe, added, dup, track, excl = row
            self.sources[sid] = {"id": sid, "path": path, "hash": h, "probe": json.loads(probe) if probe else None,
                                 "added": added, "duplicate_of": dup, "audio_track": track or 0,
                                 "excluded": bool(excl)}

    def _save_source(self, s: dict) -> None:
        self._q("insert or replace into sources values (?,?,?,?,?,?,?,?)",
                (s["id"], s["path"], s["hash"], json.dumps(s["probe"], ensure_ascii=False) if s["probe"] else None,
                 s["added"], s["duplicate_of"], s["audio_track"], int(s["excluded"])))

    def record_job(self, j: Job) -> None:
        self._q("insert into job_history values (?,?,?,?,?,?,?,?,?)",
                (j.id, j.kind, j.source, j.status, j.started, j.finished,
                 (j.finished - j.started) if j.started and j.finished else None, j.error, j.note))

    def stat_set(self, key: str, value: float) -> None:
        self._q("insert or replace into stats values (?,?)", (key, value))

    def stats(self) -> dict:
        return {k: v for k, v in self._q("select key, value from stats")}

    # ---------------------------------------------------------------- sources

    def paths(self, sid: str) -> dict[str, Path]:
        s = self.sources.get(sid) or {}
        audio_only = bool(s.get("probe")) and not (s.get("probe") or {}).get("video")
        return {
            # recorders / lavaliers get an AAC proxy so the live preview can play the lav track
            "proxy": self.edit / "proxies" / (f"{sid}.m4a" if audio_only else f"{sid}.mp4"),
            "audio": self.edit / "audio16k" / f"{sid}.wav",
            "peaks": self.edit / "waveforms" / f"{sid}.peaks",
            "thumb": self.edit / "thumbs" / f"{sid}.jpg",
            "transcript": self.edit / "transcripts" / f"{sid}.json",
        }

    def _new_id(self, p: Path) -> str:
        base = re.sub(r"[^\w\-().]+", "_", p.stem, flags=re.UNICODE) or "src"
        sid, n = base, 2
        while sid in self.sources and self.sources[sid]["path"] != str(p):
            sid = f"{base}_{p.suffix.lstrip('.').lower()}" if n == 2 else f"{base}_{n}"
            n += 1
        return sid

    def relink(self) -> dict[str, str]:
        """Sources whose file was moved (e.g. into a subfolder of the project) are found again by
        name + size, so the cut and the render keep working. Returns {source_id: new_path}."""
        missing = {sid: x for sid, x in self.sources.items() if not Path(x["path"]).exists()}
        if not missing:
            return {}
        want = {}
        for sid, x in missing.items():
            want.setdefault(Path(x["path"]).name.lower(), []).append((sid, (x.get("probe") or {}).get("size")))
        moved: dict[str, str] = {}
        for f in self.dir.rglob("*"):
            if len(f.relative_to(self.dir).parts) > 4 or self.edit in f.parents or not f.is_file():
                continue
            for sid, size in want.get(f.name.lower(), []):
                if sid in moved:
                    continue
                if size and f.stat().st_size != size:
                    continue
                moved[sid] = str(f)
                self.sources[sid]["path"] = str(f)
                self._save_source(self.sources[sid])
        return moved

    def missing_sources(self, ids=None) -> list[str]:
        ids = ids if ids is not None else self.sources.keys()
        return [sid for sid in ids if sid in self.sources and not Path(self.sources[sid]["path"]).exists()]

    def scan(self, extra_files: list[str] | None = None) -> list[dict]:
        """Find media in videos_dir (top level) + explicitly added files. ffprobe in parallel."""
        self.relink()
        found: list[Path] = []
        for p in sorted(self.dir.iterdir()):
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                found.append(p)
        for f in extra_files or []:
            p = Path(f).resolve()
            if p.is_dir():
                found += [q for q in sorted(p.iterdir()) if q.is_file() and q.suffix.lower() in MEDIA_EXTS]
            elif p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                found.append(p)
        known = {s["path"]: s for s in self.sources.values()}
        todo = []
        # sources that live in subfolders (moved or added there) stay in the project
        found += [Path(x["path"]) for x in self.sources.values() if Path(x["path"]).exists() and Path(x["path"]) not in found]
        for p in found:
            if self.edit in p.parents:
                continue
            s = known.get(str(p))
            if s is None:
                sid = self._new_id(p)
                s = {"id": sid, "path": str(p), "hash": None, "probe": None, "added": time.time(),
                     "duplicate_of": None, "audio_track": 0, "excluded": False}
                self.sources[sid] = s
            todo.append(s)

        def probe(s: dict) -> None:
            p = Path(s["path"])
            st = p.stat()
            stamp = [st.st_size, int(st.st_mtime)]
            # unchanged size+mtime → trust the stored content hash (re-hashing 68 camera files costs ~10 s on HDD)
            if s["probe"] and s["hash"] and s["probe"].get("stamp") == stamp:
                return
            h = media.fast_hash(p)
            if s["hash"] != h or not s["probe"]:
                s["hash"] = h
                try:
                    s["probe"] = media.summarize_probe(media.ffprobe(p))
                    s["probe"]["error"] = None
                except Exception as e:  # noqa: BLE001
                    s["probe"] = {"error": str(e), "duration": 0, "video": None, "audio": []}
            s["probe"]["stamp"] = stamp

        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(probe, todo))
        by_hash: dict[str, str] = {}
        for s in sorted(self.sources.values(), key=lambda x: x["id"]):
            if s["hash"] in by_hash:
                s["duplicate_of"] = by_hash[s["hash"]]
            else:
                by_hash[s["hash"]] = s["id"]
                s["duplicate_of"] = None
            self._save_source(s)
        return self.list_sources()

    def source_state(self, s: dict) -> dict:
        ps = self.paths(s["id"])
        tr = None
        if ps["transcript"].exists():
            try:
                meta = json.loads(ps["transcript"].read_text(encoding="utf-8")).get("cutroom", {})
                tr = {"engine": meta.get("engine"), "model": meta.get("model"), "rtf": meta.get("rtf")}
            except Exception:
                tr = {"engine": "?", "model": "?"}
        return {
            **s,
            "has": {k: ps[k].exists() for k in ("proxy", "audio", "peaks", "thumb", "transcript")},
            "transcript_meta": tr,
            "kind": "audio" if Path(s["path"]).suffix.lower() in AUDIO_EXTS or not (s["probe"] or {}).get("video") else "video",
            "name": Path(s["path"]).name,
            "missing": not Path(s["path"]).exists(),
        }

    def list_sources(self) -> list[dict]:
        return [self.source_state(s) for s in sorted(self.sources.values(), key=lambda x: x["id"])]

    def set_excluded(self, sid: str, excluded: bool) -> None:
        s = self.sources[sid]
        s["excluded"] = excluded
        self._save_source(s)

    # ---------------------------------------------------------------- ingest DAG

    def ingest(self, sids: list[str] | None = None, transcribe: bool = True) -> list[int]:
        settings = load_settings()
        enc = system.proxy_encoder(settings["proxy_encoder"])
        hw_decode = enc == "h264_nvenc"
        height = int(settings["proxy_height"])
        rtf = self.measured_rtf().get(f"{'cuda' if system.cuda_usable() else 'cpu'}:{settings['asr_model']}") \
            or system.RTF.get(("cuda" if system.cuda_usable() else "cpu", settings["asr_model"]), 0.1)
        created: list[int] = []
        asr_jobs: list[int] = []
        for s in sorted(self.sources.values(), key=lambda x: (x["probe"] or {}).get("duration", 0)):
            if sids and s["id"] not in sids:
                continue
            if s["excluded"] or s["duplicate_of"] or not s["probe"] or s["probe"].get("error"):
                continue
            sid, src, info = s["id"], Path(s["path"]), s["probe"]
            ps = self.paths(sid)
            dur = info.get("duration") or 0

            def existing(kind: str) -> Job | None:
                j = self.queue.find(kind, sid)
                return j if j and j.status in {"queued", "running"} else None

            proxy_job = None
            if info.get("video") and not ps["proxy"].exists() and not existing("proxy"):
                tmp = ps["proxy"].with_suffix(".part.mp4")
                proxy_job = Job(kind="proxy", label=f"Прокси {height}p · {sid}", lane="proxy", source=sid,
                                cmd=media.proxy_cmd(src, tmp, info, height, enc, hw_decode),
                                duration=dur, expected=dur * 0.15, priority=40,
                                on_done=_rename(tmp, ps["proxy"]))
                self.queue.add(proxy_job)
                created.append(proxy_job.id)
            if not info.get("video") and info.get("audio") and not ps["proxy"].exists() and not existing("proxy"):
                tmp = ps["proxy"].with_suffix(".part.m4a")
                aj = Job(kind="proxy", label=f"Аудиопрокси · {sid}", lane="audio", source=sid, duration=dur,
                         cmd=["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(src),
                              "-map", f"0:a:{s['audio_track']}", "-vn", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                              "-ac", "2", "-movflags", "+faststart", str(tmp)],
                         expected=max(1, dur * 0.03), priority=15, on_done=_rename(tmp, ps["proxy"]))
                self.queue.add(aj)
                created.append(aj.id)
            if info.get("video") and not ps["thumb"].exists() and not existing("thumb"):
                at = min(max(dur * 0.3, 0), max(dur - 0.1, 0))
                # thumbnail from the proxy when one is coming: decoding 4K HEVC for one frame is slow
                use_proxy = proxy_job is not None or ps["proxy"].exists()
                tj = Job(kind="thumb", label=f"Кадр · {sid}", lane="io", source=sid,
                         cmd=media.thumb_cmd(ps["proxy"] if use_proxy else src, ps["thumb"], at),
                         deps=[proxy_job.id] if proxy_job else [], priority=45, expected=1)
                self.queue.add(tj)
                created.append(tj.id)
            audio_job = None
            if info.get("audio"):
                if not ps["audio"].exists() and not existing("audio"):
                    tmp = ps["audio"].with_suffix(".part.wav")
                    audio_job = Job(kind="audio", label=f"Аудио 16 кГц · {sid}", lane="audio", source=sid,
                                    cmd=media.audio_cmd(src, tmp, s["audio_track"]), duration=dur,
                                    expected=max(1, dur * 0.02), priority=10, on_done=_rename(tmp, ps["audio"]))
                    self.queue.add(audio_job)
                    created.append(audio_job.id)
                deps = [audio_job.id] if audio_job else []
                if not ps["peaks"].exists() and not existing("peaks"):
                    pj = Job(kind="peaks", label=f"Волна · {sid}", lane="audio", source=sid, deps=deps,
                             fn=lambda j, a=ps["audio"], o=ps["peaks"]: media.compute_peaks(a, o),
                             priority=12, expected=1)
                    self.queue.add(pj)
                    created.append(pj.id)
                if transcribe and not existing("asr"):
                    aj = Job(kind="asr", label=f"Расшифровка {settings['asr_model']} · {sid}", lane="asr",
                             source=sid, deps=deps, cmd=self.asr_cmd(s, settings), priority=20,
                             expected=max(5.0, dur * rtf + 8), heavy=True, on_done=self._after_asr)
                    self.queue.add(aj)
                    created.append(aj.id)
                    asr_jobs.append(aj.id)
        return created

    def asr_cmd(self, s: dict, settings: dict) -> list[str]:
        ps = self.paths(s["id"])
        return [str(PYTHON), str(CUTROOM_HELPERS / "transcribe_whisper.py"), s["path"],
                "--edit-dir", str(self.edit), "--audio", str(ps["audio"]), "--name", s["id"],
                "--audio-track", str(s["audio_track"]), "--engine", settings["asr_engine"],
                "--model", settings["asr_model"], "--language", settings["asr_language"] or "auto",
                "--device", settings["asr_device"]]

    def _after_asr(self, j: Job) -> None:
        # measured speed feeds the ETA on the «Система» screen
        try:
            meta = json.loads(self.paths(j.source)["transcript"].read_text(encoding="utf-8")).get("cutroom", {})
            if meta.get("rtf") and not any(l.startswith("CACHED") for l in j.log) and meta.get("audio_duration", 0) > 20:
                key = f"rtf:{meta.get('device')}:{meta.get('model')}"
                old = self.stats().get(key)
                self.stat_set(key, meta["rtf"] if old is None else old * 0.7 + meta["rtf"] * 0.3)
        except Exception:
            pass
        self.schedule_pack()

    def measured_rtf(self) -> dict:
        return {k[4:]: v for k, v in self.stats().items() if k.startswith("rtf:")}

    def schedule_pack(self) -> Job | None:
        pending = self.queue.find("pack", None)
        if pending and pending.status == "queued":
            return pending
        j = Job(kind="pack", label="Сборка takes_packed.md", lane="pack", priority=30, expected=2,
                cmd=[str(PYTHON), str(VIDEO_USE_HELPERS / "pack_transcripts.py"), "--edit-dir", str(self.edit)])
        return self.queue.add(j)

    # ---------------------------------------------------------------- memory

    def project_memory(self) -> dict:
        """project.md: one sentence about the last session for the «продолжить?» prompt."""
        pm = self.edit / "project.md"
        if not pm.exists():
            return {"exists": False}
        text = pm.read_text(encoding="utf-8", errors="replace")
        heads = list(re.finditer(r"^#{1,3}\s*(Session|Сессия)\s*(\d+)[^\n]*$", text, flags=re.M | re.I))
        if not heads:
            return {"exists": True, "sessions": 0, "summary": None}
        last = heads[-1]
        body = text[last.end():].strip()
        body = re.sub(r"\*\*[^*]+:\*\*\s*", "", body)
        first = re.split(r"(?<=[.!?])\s", body.replace("\n", " "), maxsplit=1)[0][:280]
        return {"exists": True, "sessions": len(heads), "last_title": last.group(0).lstrip("# ").strip(),
                "summary": first}


def _rename(tmp: Path, final: Path):
    def f(j: Job) -> None:
        if tmp.exists():  # on_done only runs on success
            tmp.replace(final)
    return f


def remember_recent(path: Path) -> None:
    s = load_settings()
    rec = [str(path)] + [p for p in s.get("recent_projects", []) if p != str(path)]
    save_settings({"recent_projects": rec[:8]})
