// Program monitor: plays edl.json client-side from the proxies — no render, no waiting.
// Two <video> elements leapfrog: the active one plays segment i while the other is parked on
// segment i+1; at the cut they swap with a 30 ms volume crossfade (Hard Rule 3, by ear-equivalent).
// The swap is armed by a timer for the remaining segment time and re-checked on every presented
// frame (requestVideoFrameCallback), so it doesn't depend on rAF cadence.
// Sound: when the take has a speech mic (lavalier/recorder), each video gets a companion <audio>
// playing that mic at the sync-mapped time, and the video is muted. A music bed follows the
// program clock and ducks under speech (words of the transcript), like the render does.
import { mediaUrl, musicUrl, type EdlRange } from "./api";
import { groupOf, mapTime, useStore } from "./store";

type Seg = { source: string; start: number; end: number; out: number; idx: number; mic?: { url: string; start: number } };
type VEl = HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => number };

const els: [VEl | null, VEl | null] = [null, null];
const mics: [HTMLAudioElement, HTMLAudioElement] = [new Audio(), new Audio()];
const music = new Audio();
mics.forEach((a) => (a.preload = "auto"));
music.preload = "auto";
let active = 0;
let cur = -1;
let raf = 0;
let swapTimer: number | undefined;
let playing = false;
let frameLoop: VEl | null = null;

export function registerProgramEls(a: HTMLVideoElement | null, b: HTMLVideoElement | null) {
  els[0] = a;
  els[1] = b;
  active = 0;
  cur = -1;
}

/** Ranges → playable segments: picture from the chosen angle, sound from the take's speech mic. */
export function segments(): Seg[] {
  const st = useStore.getState();
  const e = st.edl?.edl;
  if (!e) return [];
  const micOf = new Map((st.audio?.groups ?? []).map((g) => [g.id, g.effective]));
  return e.ranges.map((r: EdlRange, idx) => {
    let source = r.source, start = r.start, end = r.end;
    const g = groupOf(r.source);
    if (r.angle && r.angle !== r.source && g && g.members.some((m) => m.id === r.angle)) {
      start = mapTime(g, r.source, r.angle, r.start);
      end = mapTime(g, r.source, r.angle, r.end);
      source = r.angle;
    }
    const mic = g ? micOf.get(g.id) : null;
    const seg: Seg = { source, start, end, out: r.out_start, idx };
    if (g && mic && mic !== source) seg.mic = { url: mediaUrl("proxy", mic), start: mapTime(g, r.source, mic, r.start) };
    return seg;
  });
}

export const programDuration = () => useStore.getState().edl?.edl?.total_duration_s ?? 0;

function segAt(segs: Seg[], t: number): number {
  for (let i = 0; i < segs.length; i++) if (t < segs[i].out + (segs[i].end - segs[i].start)) return i;
  return segs.length - 1;
}

function load(k: number, s: Seg, at: number) {
  const el = els[k]!;
  const url = mediaUrl("proxy", s.source);
  if (el.dataset.src !== url) { el.src = url; el.dataset.src = url; }
  el.currentTime = at;
  const m = mics[k];
  if (s.mic) {
    if (m.dataset.src !== s.mic.url) { m.src = s.mic.url; m.dataset.src = s.mic.url; }
    m.currentTime = Math.max(0, s.mic.start + (at - s.start));
    el.muted = true;
  } else {
    m.pause();
    el.muted = false;
  }
}

/** The element that carries the sound of slot k (mic companion or the video itself). */
const voice = (k: number, s: Seg | undefined): HTMLMediaElement => (s?.mic ? mics[k] : els[k]!);

function park(i: number, segs: Seg[]) {
  const k = 1 - active;
  if (els[k] && segs[i + 1]) {
    els[k]!.pause();
    mics[k].pause();
    load(k, segs[i + 1], segs[i + 1].start);
    voice(k, segs[i + 1]).volume = 0;
  }
}

function fade(el: HTMLMediaElement, from: number, to: number, ms = 30) {
  const t0 = performance.now();
  const step = () => {
    const k = Math.min(1, (performance.now() - t0) / ms);
    el.volume = Math.max(0, Math.min(1, from + (to - from) * k));
    if (k < 1) window.setTimeout(step, 5);
  };
  step();
}

function show(i: 0 | 1) {
  els[i]!.style.visibility = "visible";
  els[1 - i]!.style.visibility = "hidden";
}

function arm() {
  clearTimeout(swapTimer);
  const segs = segments();
  const el = els[active];
  if (!playing || !el || !segs[cur]) return;
  const left = (segs[cur].end - el.currentTime) / (el.playbackRate || 1);
  swapTimer = window.setTimeout(() => { swapTimer = undefined; check(); }, Math.max(0, left * 1000 - 20));
  if (frameLoop !== el && el.requestVideoFrameCallback) { frameLoop = el; el.requestVideoFrameCallback(() => onFrame(el)); }
}

function onFrame(el: VEl) {
  if (!playing || el !== els[active]) { if (frameLoop === el) frameLoop = null; return; }
  check();
  syncMic();
  if (els[active] === el) el.requestVideoFrameCallback!(() => onFrame(el));
  else if (frameLoop === el) frameLoop = null;
}

/** Keep the lav companion on the picture (proxy decode starts can drift by a few frames). */
function syncMic() {
  const s = segments()[cur];
  const el = els[active];
  if (!s?.mic || !el) return;
  const m = mics[active];
  const want = s.mic.start + (el.currentTime - s.start);
  if (Math.abs(m.currentTime - want) > 0.04) m.currentTime = want;
  if (m.paused && playing) m.play().catch(() => {});
}

/** Swap to the next segment if the active one reached its out-point. */
function check() {
  const segs = segments();
  const el = els[active];
  if (!playing || !el || !segs[cur]) return;
  const s = segs[cur];
  if (el.currentTime < s.end - 0.025) {
    if (!swapTimer) arm();  // woke early: re-arm for the remainder
    return;
  }
  if (cur + 1 >= segs.length) { programPause(); useStore.getState().set({ time: programDuration() }); return; }
  const k = 1 - active;
  const next = els[k]!;
  const prevVoice = voice(active, s);
  cur += 1;
  if (Math.abs(next.currentTime - segs[cur].start) > 0.05 || next.dataset.src !== mediaUrl("proxy", segs[cur].source)) load(k, segs[cur], segs[cur].start);
  const nv = voice(k, segs[cur]);
  nv.volume = 0;
  next.play().catch(() => {});
  if (segs[cur].mic) mics[k].play().catch(() => {});
  fade(nv, 0, 1);
  fade(prevVoice, prevVoice.volume, 0);
  const old = active;
  active = k;
  window.setTimeout(() => { el.pause(); mics[old].pause(); }, 35);
  show(active as 0 | 1);
  window.setTimeout(() => park(cur, segs), 60);
  swapTimer = undefined;
  arm();
}

// ---------------------------------------------------------------- music bed (ducks under speech)

const db = (x: number) => Math.pow(10, x / 20);
let speechCache: { key: string; spans: [number, number][] } = { key: "", spans: [] };

function speechSpans(): [number, number][] {
  const st = useStore.getState();
  const e = st.edl?.edl;
  if (!e) return [];
  const key = `${e.cutroom.version_id}:${Object.keys(st.transcripts).length}`;
  if (speechCache.key === key) return speechCache.spans;
  const spans: [number, number][] = [];
  for (const r of e.ranges) {
    const t = st.transcripts[r.source];
    if (typeof t !== "object") continue;
    for (const w of t.words) {
      if (w[2] < r.start || w[1] > r.end) continue;
      const a = r.out_start + Math.max(0, w[1] - r.start) - 0.15;
      const b = r.out_start + Math.min(r.end, w[2]) - r.start + 0.3;
      const last = spans[spans.length - 1];
      if (last && a <= last[1]) last[1] = Math.max(last[1], b); else spans.push([a, b]);
    }
  }
  speechCache = { key, spans };
  return spans;
}

let musicGain = 0;
function musicTick(T: number) {
  const bed = useStore.getState().audio?.music;
  if (!bed || !playing) { if (!music.paused) music.pause(); return; }
  const url = musicUrl(bed.track);
  if (music.dataset.src !== url) { music.src = url; music.dataset.src = url; }
  const pos = bed.offset + (T - bed.start_in_output);
  if (pos < 0) { music.pause(); return; }
  if (Math.abs(music.currentTime - pos) > 0.2) music.currentTime = pos;
  if (music.paused) music.play().catch(() => {});
  const dur = programDuration();
  const talking = speechSpans().some(([a, b]) => T >= a && T <= b);
  let target = db(bed.gain_db) * (talking ? db(bed.duck_db) : 1);
  target *= Math.min(1, (T - bed.start_in_output) / Math.max(0.01, bed.fade_in), (dur - T) / Math.max(0.01, bed.fade_out));
  musicGain += (Math.max(0, target) - musicGain) * 0.15;   // smooth, like a compressor release
  music.volume = Math.max(0, Math.min(1, musicGain));
}

function tick() {
  const segs = segments();
  const el = els[active];
  if (el && cur >= 0 && segs[cur]) {
    const T = segs[cur].out + (el.currentTime - segs[cur].start);
    const st = useStore.getState();
    if (Math.abs(st.time - T) > 0.02) st.set({ time: Math.max(0, Math.min(programDuration(), T)) });
    musicTick(T);
  }
  if (playing) raf = requestAnimationFrame(tick);
}

export function programSeek(t: number) {
  const segs = segments();
  if (!segs.length || !els[0] || !els[1]) return;
  const T = Math.max(0, Math.min(programDuration() - 0.001, t));
  const i = segAt(segs, T);
  cur = i;
  load(active, segs[i], segs[i].start + (T - segs[i].out));
  voice(active, segs[i]).volume = 1;
  show(active as 0 | 1);
  park(i, segs);
  useStore.getState().set({ time: T });
  if (playing) {
    els[active]!.play().catch(() => {});
    if (segs[i].mic) mics[active].play().catch(() => {});
    arm();
  }
}

export function programPlay() {
  const segs = segments();
  if (!segs.length || !els[active]) return;
  const st = useStore.getState();
  if (st.time >= programDuration() - 0.05) programSeek(0);
  else if (cur < 0) programSeek(st.time);
  playing = true;
  const s = segs[cur];
  voice(active, s).volume = 1;
  els[active]!.play().catch(() => {});
  if (s?.mic) mics[active].play().catch(() => {});
  st.set({ playing: true });
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(tick);
  arm();
}

export function programPause() {
  playing = false;
  clearTimeout(swapTimer);
  swapTimer = undefined;
  cancelAnimationFrame(raf);
  els.forEach((e) => e?.pause());
  mics.forEach((m) => m.pause());
  music.pause();
  if (useStore.getState().playing) useStore.getState().set({ playing: false });
}

export const programIsPlaying = () => playing;

/** Called when the EDL changes: keep the playhead, reload the segment under it. */
export function programRefresh() {
  cur = -1;
  programSeek(useStore.getState().time);
}

/** Index of the EDL range under the program playhead. */
export function rangeAtPlayhead(): number {
  const e = useStore.getState().edl?.edl;
  const t = useStore.getState().time;
  if (!e) return -1;
  return e.ranges.findIndex((r) => t >= r.out_start && t < r.out_start + (r.end - r.start));
}
