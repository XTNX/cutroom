"""Angle sync (stage 3): group clips of one take and find each angle's offset by audio.

1. Candidates — Pearson correlation of 100 Hz log-energy envelopes over the overlap, for
   every lag (local normalisation via cumulative sums, so short clips are not flattered),
   with enough overlap and a distinct peak.
2. Proof — GCC-PHAT on the raw 16 kHz audio around the candidate lag. Two mics recording
   the same moment give one sharp phase peak; a retake of the same script (same words,
   similar rhythm, different moment) gives noise. Only proven edges join a take.
3. Takes = maximum spanning forest over proven edges; reference = longest member.

Convention: t_ref = t_member + offset (+ nudge_frames / fps).
Result: <edit>/sync.json (source of truth; manual nudges and reference choice persist).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

SR = 16000
ENV_RATE = 100
MIN_OVERLAP = 4.0
CAND_SCORE = 0.45      # envelope Pearson to be considered at all
CAND_DISTINCT = 0.08   # peak must stand out from the best lag ≥ 1 s away
SHARP_OK = 14.0        # GCC-PHAT peak / noise floor: proven (unrelated pairs sit at ≈ 6.3)
SHARP_CHECK = 9.0      # plausible, needs a human look


def load_audio(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def envelope(a: np.ndarray) -> np.ndarray:
    hop = SR // ENV_RATE
    n = len(a) // hop
    if n == 0:
        return np.zeros(0, np.float64)
    return np.log10((a[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1) + 1e-8)


def coarse(ea: np.ndarray, eb: np.ndarray) -> tuple[float, float, float, float]:
    """Returns (lag_s, pearson, overlap_s, distinctiveness) with t_a = t_b + lag."""
    na, nb = len(ea), len(eb)
    n = 1 << int(np.ceil(np.log2(na + nb)))
    sab = np.fft.irfft(np.fft.rfft(ea, n) * np.conj(np.fft.rfft(eb, n)), n)
    # lag k: a[i + k] ~ b[i]; positive k = b starts k frames into a
    lags = np.concatenate([np.arange(0, na), np.arange(-(nb - 1), 0)])
    sab = np.concatenate([sab[:na], sab[n - (nb - 1):]])
    a0 = np.maximum(0, lags)
    a1 = np.minimum(na, lags + nb)
    b0, b1 = a0 - lags, a1 - lags
    cnt = a1 - a0
    short = min(na, nb)
    need = max(MIN_OVERLAP * ENV_RATE, min(0.8 * short, 8 * ENV_RATE))
    ok = cnt >= need
    if not ok.any():
        return 0.0, 0.0, 0.0, 0.0
    ca, ca2 = np.concatenate([[0], np.cumsum(ea)]), np.concatenate([[0], np.cumsum(ea ** 2)])
    cb, cb2 = np.concatenate([[0], np.cumsum(eb)]), np.concatenate([[0], np.cumsum(eb ** 2)])
    c = np.maximum(cnt, 1)
    sa, sb = ca[a1] - ca[a0], cb[b1] - cb[b0]
    va = ca2[a1] - ca2[a0] - sa * sa / c
    vb = cb2[b1] - cb2[b0] - sb * sb / c
    r = (sab - sa * sb / c) / np.sqrt(np.maximum(va * vb, 1e-9))
    r = np.where(ok, r, -np.inf)
    i = int(np.argmax(r))
    away = ok & (np.abs(lags - lags[i]) > ENV_RATE)
    second = float(np.max(np.where(away, r, -np.inf))) if away.any() else -1.0
    return lags[i] / ENV_RATE, float(r[i]), cnt[i] / ENV_RATE, float(r[i] - max(second, -1.0))


def gcc_phat(a: np.ndarray, b: np.ndarray, max_shift: float) -> tuple[float, float]:
    """(residual lag seconds, sharpness = peak / robust noise floor of the PHAT correlation)."""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    R = np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n))
    R /= np.abs(R) + 1e-12
    cc = np.abs(np.fft.irfft(R, n))
    m = int(max_shift * SR)
    win = np.concatenate([cc[-m:], cc[: m + 1]])
    k = int(np.argmax(win))
    floor = np.median(win) + 1e-12
    return (k - m) / SR, float(win[k] / floor)


def prove(a: np.ndarray, b: np.ndarray, lag: float, overlap: float) -> tuple[float, float]:
    """Refine lag (t_a = t_b + lag) over ≤ 30 s from the middle of the overlap → (lag, sharpness)."""
    mid = max(0.0, lag) + overlap / 2
    win = min(30.0, overlap * 0.8)
    a0 = int((mid - win / 2) * SR)
    b0 = a0 - int(round(lag * SR))
    if a0 < 0 or b0 < 0:
        return lag, 0.0
    L = int(win * SR)
    seg_a, seg_b = a[a0: a0 + L], b[b0: b0 + L]
    L = min(len(seg_a), len(seg_b))
    if L < SR * 2:
        return lag, 0.0
    res, sharp = gcc_phat(seg_a[:L], seg_b[:L], 0.08)
    return (a0 - b0) / SR + res, sharp


def run_sync(project, job) -> dict:
    srcs = [s for s in project.sources.values()
            if not s["duplicate_of"] and not s["excluded"] and project.paths(s["id"])["audio"].exists()
            and (s["probe"] or {}).get("duration", 0) >= MIN_OVERLAP]
    srcs.sort(key=lambda s: s["id"])
    envs = {}
    for k, s in enumerate(srcs):
        envs[s["id"]] = envelope(load_audio(project.paths(s["id"])["audio"]))
        job.progress = 0.15 * (k + 1) / len(srcs)
    cands = []
    pairs = [(i, j) for i in range(len(srcs)) for j in range(i + 1, len(srcs))]
    for n, (i, j) in enumerate(pairs):
        a, b = srcs[i]["id"], srcs[j]["id"]
        lag, score, ov, dist = coarse(envs[a], envs[b])
        if score >= CAND_SCORE and dist >= CAND_DISTINCT:
            cands.append({"a": a, "b": b, "lag": lag, "score": score, "overlap": ov, "distinct": dist})
        if n % 100 == 0:
            job.progress = 0.15 + 0.35 * n / max(1, len(pairs))
            if job._cancel:
                return {}
    job.log.append(f"кандидатов по огибающей: {len(cands)} из {len(pairs)} пар")
    cache: dict[str, np.ndarray] = {}

    def aud(sid):
        if sid not in cache:
            if len(cache) > 8:
                cache.pop(next(iter(cache)))
            cache[sid] = load_audio(project.paths(sid)["audio"])
        return cache[sid]

    edges = []
    for k, e in enumerate(sorted(cands, key=lambda e: (e["a"], e["b"]))):
        e["lag_fine"], e["sharp"] = prove(aud(e["a"]), aud(e["b"]), e["lag"], e["overlap"])
        if e["sharp"] >= SHARP_CHECK:
            edges.append(e)
        job.progress = 0.5 + 0.48 * (k + 1) / max(1, len(cands))
        if job._cancel:
            return {}
    job.log.append(f"подтверждено GCC-PHAT: {len(edges)}")
    edges.sort(key=lambda e: -e["sharp"])
    parent = {s["id"]: s["id"] for s in srcs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    tree = []
    for e in edges:
        ra, rb = find(e["a"]), find(e["b"])
        if ra != rb:
            parent[ra] = rb
            tree.append(e)
    comps: dict[str, list[str]] = {}
    for s in srcs:
        comps.setdefault(find(s["id"]), []).append(s["id"])
    dur = {s["id"]: (s["probe"] or {}).get("duration", 0) for s in srcs}
    old = load_sync(project)
    nudges = {m["id"]: m.get("nudge_frames", 0) for g in old.get("groups", []) for m in g["members"]}
    old_refs = {g["ref"] for g in old.get("groups", [])}
    groups = []
    for members in comps.values():
        if len(members) < 2:
            continue
        ref = next((m for m in members if m in old_refs), None) or max(members, key=lambda m: dur[m])
        adj: dict[str, list[tuple[str, float, float]]] = {m: [] for m in members}
        for e in tree:
            if e["a"] in adj:
                adj[e["a"]].append((e["b"], e["lag_fine"], e["sharp"]))   # t_a = t_b + lag → off_b = off_a + lag
                adj[e["b"]].append((e["a"], -e["lag_fine"], e["sharp"]))
        off = {ref: (0.0, 99.0)}
        stack = [ref]
        while stack:
            u = stack.pop()
            for v, lag, sh in adj[u]:
                if v not in off:
                    off[v] = (off[u][0] + lag, min(off[u][1], sh))
                    stack.append(v)
        groups.append({
            "id": "",
            "ref": ref,
            "members": sorted([{
                "id": m, "offset": round(off[m][0], 4),
                # 0 at the noise floor (≈ 6.3), 1 at the "proven" threshold
                "confidence": 1.0 if m == ref else round(max(0.0, min(1.0, (off[m][1] - 6.3) / (SHARP_OK - 6.3))), 3),
                "sharpness": None if m == ref else round(off[m][1], 1),
                "status": "ref" if m == ref else ("ok" if off[m][1] >= SHARP_OK else "check"),
                "nudge_frames": nudges.get(m, 0),
            } for m in members], key=lambda x: x["offset"]),
        })
    groups.sort(key=lambda g: g["ref"])
    for i, g in enumerate(groups, 1):
        g["id"] = f"take_{i}"
    result = {"version": 1, "convention": "t_ref = t_member + offset + nudge_frames / fps",
              "groups": groups, "singles": sorted(m for c in comps.values() if len(c) == 1 for m in c),
              "stats": {"pairs": len(pairs), "candidates": len(cands), "proven": len(edges)}}
    save_sync(project, result)
    return result


def load_sync(project) -> dict:
    p = project.edit / "sync.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_sync(project, data: dict) -> None:
    (project.edit / "sync.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
