// Quick mode: pure timeline operations on the block document (no React, no I/O).
// Positions are integer frames; media blocks carry their source in-point in seconds.
// A video block and its linked sound (audio.link === video.id) move, split and trim together.

export type TrackName = "video" | "audio" | "slides" | "overlays";
export type Grade = { preset?: string; cdl?: { slope?: number[]; offset?: number[]; power?: number[]; sat?: number }; temperature?: number; raw?: string };
export type VBlock = { id: string; start: number; len: number; src: string; in: number; color?: Grade | null;
  rotate?: number; flip_h?: boolean; flip_v?: boolean };   // clip settings: clockwise turn, mirror
export type ABlock = { id: string; start: number; len: number; src: string; in: number; style?: string; link?: string;
  fade_in?: number; fade_out?: number };   // fades in seconds (the timeline's corner handles)
export type SBlock = { id: string; start: number; len: number; page: number; layout?: "side" | "full" };
export type OBlock = {
  id: string; start: number; len: number; slide?: string | null; page?: number | null; hint?: string; style?: string;
  spec?: { type: string; title: string } & Record<string, unknown>; status?: string; error?: string | null;
  files?: { poster: string; preview: string; render: string; v: number }; variants?: Record<string, string>;
};
export type Block = VBlock | ABlock | SBlock | OBlock;
export type QDoc = {
  version: number; rate: string; roles: Record<string, string>; grade: Grade | null;
  music: { track: string; path: string; title: string } | null;
  presentation: { file: string; name: string; pages: { n: number; text: string; text_src?: string }[]; text_note?: string | null } | null;
  tracks: { video: VBlock[]; audio: ABlock[]; slides: SBlock[]; overlays: OBlock[] };
  meta: { version_id?: number; strategy_hash?: string };
};
export type Ref = { track: TrackName; id: string };

export const TRACKS: TrackName[] = ["video", "audio", "slides", "overlays"];
export const MEDIA = (t: TrackName) => t === "video" || t === "audio";
export const MIN_LEN = 3;

export const fpsOf = (d: QDoc) => {
  const [a, b] = (d.rate || "25").split("/").map(Number);
  return b ? a / b : a;
};
export const end = (b: Block) => b.start + b.len;
export const clone = (d: QDoc): QDoc => JSON.parse(JSON.stringify(d));
export const total = (d: QDoc) => Math.max(0, ...TRACKS.flatMap((t) => (d.tracks[t] as Block[]).map(end)));
const uid = (p: string) => p + Math.random().toString(16).slice(2, 9);
const list = (d: QDoc, t: TrackName) => d.tracks[t] as Block[];
const sort = (d: QDoc) => TRACKS.forEach((t) => list(d, t).sort((a, b) => a.start - b.start));

export function find(d: QDoc, r: Ref): Block | undefined {
  return list(d, r.track).find((b) => b.id === r.id);
}

/** The blocks an operation on `refs` really touches: the selected ones + their linked partners, both ways
 *  (a picture brings its sound, a sound brings its picture). */
export function withLinked(d: QDoc, refs: Ref[]): Ref[] {
  const out = [...refs];
  const has = (t: TrackName, id: string) => out.some((r) => r.track === t && r.id === id);
  const vids = new Set(refs.filter((r) => r.track === "video").map((r) => r.id));
  const auds = new Set(refs.filter((r) => r.track === "audio").map((r) => r.id));
  for (const a of d.tracks.audio) {
    if (a.link && vids.has(a.link) && !has("audio", a.id)) out.push({ track: "audio", id: a.id });
    if (a.link && auds.has(a.id) && !has("video", a.link) && d.tracks.video.some((v) => v.id === a.link)) out.push({ track: "video", id: a.link });
  }
  return out;
}

function splitBlock(d: QDoc, t: TrackName, b: Block, f: number): Block | null {
  if (!(b.start < f && f < end(b))) return null;
  const k = f - b.start;
  const right = { ...b, id: uid(b.id[0]), start: f, len: b.len - k } as Block;
  if (MEDIA(t)) (right as VBlock).in = +((b as VBlock).in + k / fpsOf(d)).toFixed(4);
  b.len = k;
  const l = list(d, t);
  l.splice(l.indexOf(b) + 1, 0, right);
  return right;
}

/** Split at frame f: the selected blocks, or every block under f when nothing is selected. */
export function split(doc: QDoc, refs: Ref[], f: number): QDoc {
  const d = clone(doc);
  const targets: Ref[] = refs.length ? withLinked(d, refs) : TRACKS.flatMap((t) => list(d, t).filter((b) => b.start < f && f < end(b)).map((b) => ({ track: t, id: b.id })));
  const rights = new Map<string, string>();
  for (const r of targets.filter((x) => x.track !== "audio").concat(targets.filter((x) => x.track === "audio"))) {
    const b = find(d, r);
    if (!b) continue;
    const right = splitBlock(d, r.track, b, f);
    if (right && r.track === "video") rights.set(b.id, right.id);
    if (right && r.track === "audio" && (b as ABlock).link && rights.has((b as ABlock).link!)) (right as ABlock).link = rights.get((b as ABlock).link!);
  }
  return d;
}

/** How far the selection may move by `delta` without running into blocks that stay put (free move). */
export function clampMove(d: QDoc, refs: Ref[], delta: number): number {
  const moving = new Set(refs.map((r) => r.track + ":" + r.id));
  let lo = -Infinity, hi = Infinity;
  for (const r of refs) {
    const b = find(d, r)!;
    lo = Math.max(lo, -b.start);
    for (const o of list(d, r.track)) {
      if (moving.has(r.track + ":" + o.id)) continue;
      if (end(o) <= b.start) lo = Math.max(lo, end(o) - b.start);
      else if (o.start >= end(b)) hi = Math.min(hi, o.start - end(b));
      else return 0;   // already overlapping (shouldn't happen): don't move
    }
  }
  return Math.max(lo, Math.min(hi, delta));
}

/** Overwrite: block `b` lands on its track and replaces whatever it covers there. */
function carve(d: QDoc, t: TrackName, b: Block, keep: Set<string>) {
  const fps = fpsOf(d);
  const out: Block[] = [];
  for (const o of list(d, t)) {
    if (o === b || keep.has(o.id) || end(o) <= b.start || o.start >= end(b)) { out.push(o); continue; }
    if (o.start < b.start) out.push({ ...o, len: b.start - o.start } as Block);
    if (end(o) > end(b)) {
      const cut = end(b) - o.start;
      // the remaining tail keeps its id (and its link) unless the block was cut in two
      const r = { ...o, id: o.start < b.start ? uid(o.id[0]) : o.id, start: end(b), len: end(o) - end(b) } as Block;
      if (MEDIA(t)) (r as VBlock).in = +((o as VBlock).in + cut / fps).toFixed(4);
      out.push(r);
    }
  }
  (d.tracks as Record<TrackName, Block[]>)[t] = out;
}

export function move(doc: QDoc, refs: Ref[], delta: number, overwrite: boolean): QDoc {
  const d = clone(doc);
  const all = withLinked(d, refs);
  const dd = overwrite ? Math.max(delta, -Math.min(...all.map((r) => find(d, r)!.start))) : clampMove(d, all, delta);
  if (!dd) return doc;
  const keep = new Set(all.map((r) => r.id));
  for (const r of all) find(d, r)!.start += dd;
  if (overwrite) for (const r of all) carve(d, r.track, find(d, r)!, keep);
  sort(d);
  return d;
}

/** Drag an edge. Media can't go past its source; nothing may overlap a neighbour (it stops there),
 *  unless `overwrite` — then the edge eats into the neighbour. `srcDur` = source length in seconds. */
export function trim(doc: QDoc, ref: Ref, edge: "start" | "end", delta: number, srcDur: (id: string) => number, overwrite = false): QDoc {
  const d = clone(doc);
  const fps = fpsOf(d);
  const b0 = find(d, ref);
  if (!b0) return doc;
  const group = [ref, ...withLinked(d, [ref]).slice(1).filter((r) => {
    const a = find(d, r)!;   // the linked partner (sound or picture) follows when its edge sits where this one's does
    return edge === "start" ? a.start === b0.start : end(a) === end(b0);
  })];
  let dd = delta;
  for (const r of group) {
    const b = find(d, r)!;
    const others = list(d, r.track).filter((o) => o.id !== b.id);
    if (edge === "start") {
      dd = Math.min(dd, b.len - MIN_LEN);
      dd = Math.max(dd, -b.start);
      if (MEDIA(r.track)) dd = Math.max(dd, -Math.floor((b as VBlock).in * fps + 1e-6));
      if (!overwrite) for (const o of others) if (end(o) <= b.start) dd = Math.max(dd, end(o) - b.start);
    } else {
      dd = Math.max(dd, MIN_LEN - b.len);
      if (MEDIA(r.track)) dd = Math.min(dd, Math.floor((srcDur((b as VBlock).src) - (b as VBlock).in) * fps + 1e-6) - b.len);
      if (!overwrite) for (const o of others) if (o.start >= end(b)) dd = Math.min(dd, o.start - end(b));
    }
  }
  if (!dd) return doc;
  const keep = new Set(group.map((r) => r.id));
  for (const r of group) {
    const b = find(d, r)!;
    if (edge === "start") {
      b.start += dd;
      b.len -= dd;
      if (MEDIA(r.track)) (b as VBlock).in = +((b as VBlock).in + dd / fps).toFixed(4);
    } else b.len += dd;
    if (overwrite) carve(d, r.track, b, keep);
  }
  sort(d);
  return d;
}

/** Remove the selected blocks. ripple: close the gap on every track (the film gets shorter). */
export function remove(doc: QDoc, refs: Ref[], ripple: boolean): QDoc {
  let d = clone(doc);
  const all = withLinked(d, refs);
  if (!ripple) {
    for (const r of all) (d.tracks as Record<TrackName, Block[]>)[r.track] = list(d, r.track).filter((b) => b.id !== r.id);
    return d;
  }
  const spans = all.filter((r) => MEDIA(r.track)).map((r) => find(d, r)!).map((b) => [b.start, end(b)] as [number, number]);
  for (const r of all.filter((x) => !MEDIA(x.track))) (d.tracks as Record<TrackName, Block[]>)[r.track] = list(d, r.track).filter((b) => b.id !== r.id);
  spans.sort((a, b) => b[0] - a[0]);   // from the end, so earlier spans keep their frames
  const merged: [number, number][] = [];
  for (const s of spans) {
    const last = merged[merged.length - 1];
    if (last && s[1] >= last[0]) last[0] = Math.min(last[0], s[0]);
    else merged.push([...s]);
  }
  for (const [f0, f1] of merged) d = rippleDelete(d, f0, f1);
  return d;
}

/** Cut frames [f0, f1) out of the whole film (same as the server's ops.ripple_delete). */
export function rippleDelete(doc: QDoc, f0: number, f1: number): QDoc {
  const n = f1 - f0;
  if (n <= 0) return doc;
  let d = doc;
  for (const t of ["video", "audio"] as TrackName[]) {
    d = split(d, list(d, t).filter((b) => b.start < f0 && f0 < end(b)).map((b) => ({ track: t, id: b.id })), f0);
    d = split(d, list(d, t).filter((b) => b.start < f1 && f1 < end(b)).map((b) => ({ track: t, id: b.id })), f1);
  }
  d = clone(d);
  for (const t of TRACKS) {
    const out: Block[] = [];
    for (const b of list(d, t)) {
      const s = b.start, e = end(b);
      if (MEDIA(t)) {
        if (f0 <= s && e <= f1) continue;
        if (s >= f1) b.start = s - n;
      } else {
        const keep = Math.max(0, Math.min(e, f0) - s) + Math.max(0, e - Math.max(s, f1));
        if (keep <= 0) continue;
        b.start = s < f0 ? s : Math.max(f0, s - n);
        b.len = keep;
      }
      out.push(b);
    }
    (d.tracks as Record<TrackName, Block[]>)[t] = out;
  }
  return d;
}

/** Close every gap on the picture track (and move the rest of the film with it). */
export function closeGaps(doc: QDoc): QDoc {
  let d = doc;
  for (;;) {
    const v = d.tracks.video;
    let gap: [number, number] | null = null;
    let cur = 0;
    for (const b of v) { if (b.start > cur) { gap = [cur, b.start]; break; } cur = end(b); }
    if (!gap) return d;
    const [g0, g1] = gap;
    const covered = d.tracks.audio.some((a) => a.start < g1 && end(a) > g0);
    if (covered) return d;   // sound under the gap: leave it, the person decides
    d = rippleDelete(d, g0, g1);
  }
}

/** Snap a frame to the nearest edge / playhead within `tol` frames. */
export function snapFrame(f: number, edges: number[], tol: number): number {
  let best = f, bd = tol + 1;
  for (const e of edges) {
    const dd = Math.abs(e - f);
    if (dd < bd) { bd = dd; best = e; }
  }
  return bd <= tol ? best : f;
}

export function edges(d: QDoc, exclude: Set<string>): number[] {
  const out: number[] = [0];
  for (const t of TRACKS) for (const b of list(d, t)) if (!exclude.has(b.id)) out.push(b.start, end(b));
  return out;
}

/** Change fields of the selected blocks (rotate, mirror, fades…) — the blocks themselves, not their partners. */
export function patch(doc: QDoc, refs: Ref[], fn: (b: Block, t: TrackName) => void): QDoc {
  const d = clone(doc);
  for (const r of refs) { const b = find(d, r); if (b) fn(b, r.track); }
  return d;
}

/** Short fades where two sound blocks meet (or around the selected ones): evens out the jump in level
 *  and the click at a cut. `sec` per side; 0 removes them. */
export function spliceFades(doc: QDoc, refs: Ref[], sec: number): QDoc {
  const d = clone(doc);
  const pick = new Set(refs.filter((r) => r.track === "audio").map((r) => r.id));
  const a = d.tracks.audio;
  const fps = fpsOf(d);
  for (let i = 0; i < a.length; i++) {
    const b = a[i];
    if (pick.size && !pick.has(b.id)) continue;
    const max = b.len / fps / 2;
    const prevTouch = i > 0 && end(a[i - 1]) >= b.start;
    const nextTouch = i < a.length - 1 && a[i + 1].start <= end(b);
    if (pick.size || prevTouch) b.fade_in = sec ? Math.min(sec, max) : undefined;
    if (pick.size || nextTouch) b.fade_out = sec ? Math.min(sec, max) : undefined;
    if (!pick.size) {   // the neighbour across the cut gets the matching fade
      if (prevTouch) a[i - 1].fade_out = sec ? Math.min(sec, a[i - 1].len / fps / 2) : undefined;
      if (nextTouch) a[i + 1].fade_in = sec ? Math.min(sec, a[i + 1].len / fps / 2) : undefined;
    }
  }
  return d;
}

export type Clip = { blocks: { track: TrackName; b: Block }[]; start: number };

/** Copy: the selection with its linked partners, positions relative to the earliest block. */
export function copy(d: QDoc, refs: Ref[]): Clip | null {
  const all = withLinked(d, refs).map((r) => ({ track: r.track, b: find(d, r) })).filter((x) => x.b) as { track: TrackName; b: Block }[];
  if (!all.length) return null;
  const start = Math.min(...all.map((x) => x.b.start));
  return { start, blocks: all.map((x) => ({ track: x.track, b: JSON.parse(JSON.stringify(x.b)) })) };
}

/** Paste a copied clip at frame `at`: new ids (links kept inside the clip), overwrites what is there. */
export function paste(doc: QDoc, clip: Clip, at: number): { doc: QDoc; refs: Ref[] } {
  const d = clone(doc);
  const ids = new Map<string, string>();
  for (const x of clip.blocks) ids.set(x.b.id, uid(x.b.id[0]));
  const refs: Ref[] = [];
  const placed: { track: TrackName; b: Block }[] = [];
  for (const x of clip.blocks) {
    const b = { ...JSON.parse(JSON.stringify(x.b)), id: ids.get(x.b.id)!, start: at + (x.b.start - clip.start) } as Block;
    if (x.track === "audio" && (b as ABlock).link) {
      const l = ids.get((b as ABlock).link!);
      if (l) (b as ABlock).link = l; else delete (b as ABlock).link;
    }
    if (x.track === "overlays") { const o = b as OBlock; if (o.slide && !ids.has(o.slide)) o.slide = null; else if (o.slide) o.slide = ids.get(o.slide)!; }
    list(d, x.track).push(b);
    placed.push({ track: x.track, b });
    refs.push({ track: x.track, id: b.id });
  }
  const keep = new Set(placed.map((x) => x.b.id));
  for (const x of placed) carve(d, x.track, x.b, keep);
  sort(d);
  return { doc: d, refs };
}

/** Unlink the selected pictures and sounds from their partners / link a picture with the sound under it. */
export function unlink(doc: QDoc, refs: Ref[]): QDoc {
  const d = clone(doc);
  const ids = new Set(withLinked(d, refs).map((r) => r.id));
  for (const a of d.tracks.audio) if (a.link && (ids.has(a.id) || ids.has(a.link))) delete a.link;
  return d;
}
export function link(doc: QDoc, refs: Ref[]): QDoc {
  const d = clone(doc);
  const v = refs.find((r) => r.track === "video"), a = refs.find((r) => r.track === "audio");
  const vb = v && (find(d, v) as VBlock | undefined), ab = a && (find(d, a) as ABlock | undefined);
  if (vb && ab) ab.link = vb.id;
  return d;
}
