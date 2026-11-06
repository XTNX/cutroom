"""Job queue: lanes with their own concurrency, DAG dependencies, progress, ETA, cancel.

Jobs run in worker threads (subprocess.Popen + line reader) — that works under any
asyncio loop policy on Windows. The server polls `version` to push changes over WS.
"""

from __future__ import annotations

import itertools
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .config import subprocess_env

LANES = {
    "io": 4,      # thumbnails, small ffmpeg jobs
    "audio": 3,   # 16 kHz extraction + peaks
    "proxy": 2,   # NVENC proxies (consumer cards allow several sessions; 2 keeps decode fed)
    "asr": 1,     # GPU transcription — one file at a time
    "pack": 1,
    "render": 1,  # heavy renders never run in parallel with each other
    "agent": 2,   # Claude calls (strategy, editor, fragment)
    "slots": 4,   # infographic slot agents — all in parallel (video-use Hard Rule 10)
}

_ids = itertools.count(1)
_TERMINAL = {"done", "cached", "error", "cancelled", "blocked"}


@dataclass
class Job:
    kind: str
    label: str
    lane: str
    cmd: list[str] | None = None
    fn: Callable[["Job"], object] | None = None
    source: str | None = None
    deps: list[int] = field(default_factory=list)
    priority: int = 50            # lower runs first
    duration: float | None = None  # media seconds, for ffmpeg -progress ratio
    expected: float | None = None  # expected wall seconds (ETA while queued)
    on_done: Callable[["Job"], None] | None = None
    heavy: bool = False            # long/expensive: UI marks it, never starts silently
    not_before: float = 0.0        # debounce: don't start before this time.time()
    id: int = field(default_factory=lambda: next(_ids))
    status: str = "queued"
    progress: float = 0.0
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    note: str | None = None
    log: deque = field(default_factory=lambda: deque(maxlen=200))
    _proc: subprocess.Popen | None = None
    _cancel: bool = False

    def eta(self) -> float | None:
        if self.status == "running" and self.started:
            el = time.time() - self.started
            if self.progress > 0.02:
                return max(0.0, el / self.progress - el)
            if self.expected:
                return max(0.0, self.expected - el)
        if self.status == "queued":
            return self.expected
        return None

    def public(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "label": self.label, "lane": self.lane,
            "source": self.source, "deps": self.deps, "status": self.status,
            "progress": round(self.progress, 4), "eta": self.eta(), "heavy": self.heavy,
            "created": self.created, "started": self.started, "finished": self.finished,
            "elapsed": (self.finished or time.time()) - self.started if self.started else None,
            "error": self.error, "note": self.note, "log": list(self.log)[-40:],
        }


_OUT_TIME = re.compile(r"out_time_(?:us|ms)=(\d+)")
_PROGRESS = re.compile(r"PROGRESS ([0-9.]+)")


class Queue:
    def __init__(self) -> None:
        self.jobs: dict[int, Job] = {}
        self.lock = threading.RLock()
        self.version = 0
        self._wake = threading.Event()
        self._stop = False
        self.on_finish: Callable[[Job], None] | None = None
        self._t = threading.Thread(target=self._loop, daemon=True, name="cutroom-scheduler")
        self._t.start()

    # ---------------------------------------------------------------- public

    def add(self, job: Job) -> Job:
        with self.lock:
            self.jobs[job.id] = job
            self._bump()
        return job

    def cancel(self, job_id: int) -> bool:
        with self.lock:
            j = self.jobs.get(job_id)
            if not j or j.status in _TERMINAL:
                return False
            j._cancel = True
            if j.status == "queued":
                self._finish(j, "cancelled")
            elif j._proc:
                _kill_tree(j._proc)
            self._bump()
            return True

    def cancel_all(self) -> None:
        with self.lock:
            ids = [j.id for j in self.jobs.values() if j.status not in _TERMINAL]
        for i in ids:
            self.cancel(i)

    def retry(self, job_id: int) -> Job | None:
        with self.lock:
            j = self.jobs.get(job_id)
            if not j or j.status not in {"error", "cancelled", "blocked"}:
                return None
            j.status, j.progress, j.error, j._cancel = "queued", 0.0, None, False
            j.started = j.finished = None
            # unblock dependents that were blocked by this one
            for d in self.jobs.values():
                if d.status == "blocked" and job_id in d.deps:
                    d.status, d.error = "queued", None
            self._bump()
            return j

    def active(self) -> list[Job]:
        with self.lock:
            return [j for j in self.jobs.values() if j.status not in _TERMINAL]

    def find(self, kind: str, source: str | None) -> Job | None:
        with self.lock:
            for j in sorted(self.jobs.values(), key=lambda x: -x.id):
                if j.kind == kind and j.source == source:
                    return j
        return None

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [j.public() for j in sorted(self.jobs.values(), key=lambda x: x.id)]

    def clear_finished(self) -> None:
        with self.lock:
            for k in [k for k, j in self.jobs.items() if j.status in {"done", "cached", "cancelled"}]:
                del self.jobs[k]
            self._bump()

    def shutdown(self) -> None:
        self._stop = True
        self.cancel_all()
        self._wake.set()

    # ---------------------------------------------------------------- internals

    def _bump(self) -> None:
        self.version += 1
        self._wake.set()

    def _finish(self, j: Job, status: str, error: str | None = None) -> None:
        j.status = status
        j.error = error
        j.finished = time.time()
        if status in {"done", "cached"}:
            j.progress = 1.0

    def _loop(self) -> None:
        while not self._stop:
            self._wake.wait(0.5)
            self._wake.clear()
            with self.lock:
                running = {lane: 0 for lane in LANES}
                for j in self.jobs.values():
                    if j.status == "running":
                        running[j.lane] = running.get(j.lane, 0) + 1
                queued = sorted((j for j in self.jobs.values() if j.status == "queued"),
                                key=lambda x: (x.priority, x.id))
                for j in queued:
                    dep_states = [self.jobs[d].status for d in j.deps if d in self.jobs]
                    if any(s in {"error", "cancelled", "blocked"} for s in dep_states):
                        self._finish(j, "blocked", "предыдущий шаг не выполнен")
                        self._bump()
                        continue
                    if any(s not in {"done", "cached"} for s in dep_states):
                        continue
                    if running.get(j.lane, 0) >= LANES.get(j.lane, 1):
                        continue
                    if j.not_before > time.time():
                        continue
                    running[j.lane] = running.get(j.lane, 0) + 1
                    j.status = "running"
                    j.started = time.time()
                    self._bump()
                    threading.Thread(target=self._run, args=(j,), daemon=True,
                                     name=f"job-{j.id}-{j.kind}").start()

    def _run(self, j: Job) -> None:
        try:
            if j.fn is not None:
                res = j.fn(j)
                if j._cancel:
                    raise _Cancelled()
                status = "cached" if res == "cached" else "done"
            else:
                status = self._run_cmd(j)
            # on_done runs while still "running", so dependents never see a half-renamed output
            j.note = j.note or ("из кэша" if status == "cached" else None)
            if j.on_done:
                try:
                    j.on_done(j)
                except Exception as e:  # noqa: BLE001
                    j.log.append(f"on_done: {e}")
            with self.lock:
                self._finish(j, status)
                self._bump()
            if self.on_finish:
                self.on_finish(j)
        except _Cancelled:
            with self.lock:
                self._finish(j, "cancelled")
                self._bump()
            if self.on_finish:
                self.on_finish(j)
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self._finish(j, "error", str(e)[-600:])
                self._bump()
            if self.on_finish:
                self.on_finish(j)

    def _run_cmd(self, j: Job) -> str:
        assert j.cmd
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0
        j.log.append("$ " + " ".join(_short(c) for c in j.cmd[:8]) + (" …" if len(j.cmd) > 8 else ""))
        proc = subprocess.Popen(j.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=subprocess_env(),
                                creationflags=flags, text=True, encoding="utf-8", errors="replace")
        j._proc = proc
        cached = False
        assert proc.stdout
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            m = _OUT_TIME.match(line)
            if m and j.duration:
                us = int(m.group(1))
                j.progress = min(0.999, us / 1e6 / j.duration)
                self.version += 1
                continue
            m = _PROGRESS.match(line)
            if m:
                j.progress = min(0.999, float(m.group(1)))
                self.version += 1
                continue
            if line.startswith(("frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time=",
                                "dup_frames=", "drop_frames=", "speed=", "progress=")):
                continue
            if line.startswith("CACHED"):
                cached = True
            j.log.append(line[:400])
            self.version += 1
        rc = proc.wait()
        j._proc = None
        if j._cancel:
            raise _Cancelled()
        if rc != 0:
            tail = " | ".join(list(j.log)[-4:])
            raise RuntimeError(f"код выхода {rc}: {tail}")
        return "cached" if cached else "done"


class _Cancelled(Exception):
    pass


def _short(s: str) -> str:
    return s if len(s) < 60 else "…" + s[-50:]


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
