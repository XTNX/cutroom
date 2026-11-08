// Quick mode: pure timeline operations on the block document (no React, no I/O).
// Positions are integer frames; media blocks carry their source in-point in seconds.
// A video block and its linked sound (audio.link === video.id) move, split and trim together.

export type TrackName = "video" | "audio" | "slides" | "overlays";
export type Grade = { preset?: string; cdl?: { slope?: number[]; offset?: number[]; power?: number[]; sat?: number }; temperature?: number; raw?: string };
export type VBlock = { id: string; start: number; len: number; src: string; in: number; color?: Grade | null };
export type ABlock = { id: string; start: number; len: number; src: string; in: number; style?: string; link?: string };
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
  presentation: { file: string; name: string; pages: { n: number; text: string }[] } | null;
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

/** The blocks an operation on `refs` really touches: selected ones + the sound linked to selected pictures. */
export function withLinked(d: QDoc, refs: Ref[]): Ref[] {
  const out = [...refs];
  const vids = new Set(refs.filter((r) => r.track === "video").map((r) => r.id));
  for (const a of d.tracks.audio) {
    if (a.link && vids.has(a.link) && !out.some((r) => r.track === "audio" && r.id === a.id)) out.push({ track: "audio", id: a.id });
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
    const a = find(d, r)!;   // the linked sound follows only when its edge sits where the picture's does
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

/** The picture block whose edge touches `ref`'s `edge` (the other side of an angle switch / a cut). */
export function rollPartner(d: QDoc, ref: Ref, edge: "start" | "end"): Ref | null {
  if (ref.track !== "video") return null;
  const b = find(d, ref);
  if (!b) return null;
  const o = d.tracks.video.find((x) => x.id !== b.id && (edge === "end" ? x.start === end(b) : end(x) === b.start));
  return o ? { track: "video", id: o.id } : null;
}

/** Roll the cut between two touching picture blocks: one angle gets longer, the other shorter by
 *  the same frames; nothing else moves and the sound is not touched (the speech stays where it is).
 *  `left` ends where `right` starts. The right block keeps sync: its in-point moves with its start. */
export function roll(doc: QDoc, left: Ref, right: Ref, delta: number, srcDur: (id: string) => number): QDoc {
  const d = clone(doc);
  const fps = fpsOf(d);
  const a = find(d, left) as VBlock | undefined, b = find(d, right) as VBlock | undefined;
  if (!a || !b || end(a) !== b.start) return doc;
  let dd = delta;
  dd = Math.max(dd, MIN_LEN - a.len);                                           // left keeps at least MIN_LEN
  dd = Math.min(dd, b.len - MIN_LEN);                                           // right too
  dd = Math.min(dd, Math.floor((srcDur(a.src) - a.in) * fps + 1e-6) - a.len);   // left can't run past its source
  dd = Math.max(dd, -Math.floor(b.in * fps + 1e-6));                            // right can't start before its source
  if (!dd) return doc;
  a.len += dd;
  b.start += dd;
  b.len -= dd;
  b.in = +(b.in + dd / fps).toFixed(4);
  return d;
}

/** Open `n` frames of empty time at frame `f` on every track: media blocks crossing `f` are cut there,
 *  everything from `f` on moves right (except `keep`). Slides and cards crossing `f` stay as they are. */
function openTime(d: QDoc, f: number, n: number, keep: Set<string>): void {
  for (const t of ["video", "audio"] as TrackName[]) {
    const cross = list(d, t).filter((b) => !keep.has(b.id) && b.start < f && f < end(b));
    for (const b of cross) {
      const right = splitBlock(d, t, b, f);
      // a cut sound keeps its link to the picture piece it lies under
      if (right && t === "audio" && (b as ABlock).link) {
        const v = d.tracks.video.find((x) => x.start <= right.start && right.start < end(x));
        if (v) (right as ABlock).link = v.id;
      }
    }
  }
  for (const t of TRACKS) for (const b of list(d, t)) if (!keep.has(b.id) && b.start >= f) b.start += n;
}

/** Change a block's length WITHOUT touching its neighbours: the rest of the film moves instead
 *  (ripple). The linked sound changes with the picture when its edge sits where the picture's does.
 *  Shorter: the frames leave the film; longer: the block takes more of its source and everything
 *  after it moves right. */
export function rippleTrim(doc: QDoc, ref: Ref, edge: "start" | "end", delta: number, srcDur: (id: string) => number): QDoc {
  const d0 = clone(doc);
  const fps = fpsOf(d0);
  const b0 = find(d0, ref);
  if (!b0) return doc;
  const group = [ref, ...withLinked(d0, [ref]).slice(1).filter((r) => {
    const a = find(d0, r)!;
    return edge === "start" ? a.start === b0.start : end(a) === end(b0);
  })];
  // grow > 0: the block gets longer by `grow` frames
  let grow = edge === "end" ? delta : -delta;
  for (const r of group) {
    const b = find(d0, r)!;
    grow = Math.max(grow, MIN_LEN - b.len);
    if (MEDIA(r.track) && grow > 0) {
      const v = b as VBlock;
      grow = Math.min(grow, edge === "end" ? Math.floor((srcDur(v.src) - v.in) * fps + 1e-6) - v.len : Math.floor(v.in * fps + 1e-6));
    }
  }
  if (!grow) return doc;
  if (grow < 0) {   // shorter: cut those frames out of the whole film
    const n = -grow;
    return edge === "end" ? rippleDelete(d0, end(b0) - n, end(b0)) : rippleDelete(d0, b0.start, b0.start + n);
  }
  const keep = new Set(group.map((r) => r.id));
  const at = edge === "end" ? end(b0) : b0.start;
  openTime(d0, at, grow, keep);
  for (const r of group) {
    const b = find(d0, r)!;
    if (edge === "end") b.len += grow;
    else {
      b.len += grow;
      if (MEDIA(r.track)) (b as VBlock).in = +((b as VBlock).in - grow / fps).toFixed(4);
    }
  }
  sort(d0);
  return d0;
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
