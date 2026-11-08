import { useEffect, useRef, useState } from "react";
import { api, mediaUrl } from "../api";
import { useStore } from "../store";
import { closeGaps, edges, end, fpsOf, move, snapFrame, total, trim, type Block, type QDoc, type Ref, type TrackName, type VBlock, type ABlock, type OBlock, type SBlock } from "../quickOps";
import { qSeek, roleOf, shownCover, shownDoc, useQuick, fmtT, type Cover } from "../quickStore";
import { quickUrl } from "./QuickView";

const RULER = 22, LABEL_W = 78, GAP = 4, EDGE = 6;
type RowKey = "front" | "top" | "side" | "slides" | "overlays" | "audio";
type Row = { key: RowKey; label: string; y: number; h: number };
const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || "#888";
const ROLE_COLOR: Record<string, string> = { front: "--cam1", top: "--cam2", side: "--cam3", other: "--fg-faint" };

function rowsFor(d: QDoc | null, cov: Cover): Row[] {
  const hasSide = !!d && (d.tracks.video.some((v) => roleOf(d, v.src) === "side") || Object.values(cov).some((c) => c.side));
  const spec: [RowKey, string, number][] = [["front", "Фронт", 30], ["top", "Верх", 30]];
  if (hasSide) spec.push(["side", "Сбоку", 24]);
  spec.push(["slides", "Презентация", 26], ["overlays", "Оверлеи", 36], ["audio", "Звук", 30]);
  let y = RULER + 4;
  return spec.map(([key, label, h]) => { const r = { key, label, y, h }; y += h + GAP; return r; });
}
const pictureRow = (d: QDoc, v: VBlock): RowKey => { const r = roleOf(d, v.src); return r === "top" || r === "side" ? r : "front"; };

type Drag =
  | { kind: "scrub" }
  | { kind: "move"; refs: Ref[]; x0: number; base: QDoc; moved: boolean }
  | { kind: "trim"; ref: Ref; edge: "start" | "end"; x0: number; base: QDoc }
  | { kind: "band"; x0: number; y0: number; x1: number; y1: number; add: boolean }
  | { kind: "angle"; ids: string[]; role: string; x0: number }
  | null;

const imgs = new Map<string, HTMLImageElement>();
function img(url: string, onload: () => void): HTMLImageElement | null {
  let i = imgs.get(url);
  if (!i) { i = new Image(); i.onload = onload; i.src = url; imgs.set(url, i); }
  return i.complete && i.naturalWidth ? i : null;
}

export function QuickTimeline() {
  const wrap = useRef<HTMLDivElement>(null);
  const cv = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 900, h: 220 });
  const view = useRef({ pps: 10, x0: 0, fitted: false });
  const drag = useRef<Drag>(null);
  const [, force] = useState(0);
  const redraw = () => force((n) => n + 1);
  const q = useQuick();
  const jobs = useStore((s) => s.jobs);
  const peaks = useStore((s) => s.peaks);
  const d = shownDoc(q);
  const cov = shownCover(q);
  const fps = d ? fpsOf(d) : 25;
  const rows = rowsFor(d, cov);
  const locked = !!q.propDoc || !q.data?.gate_open;
  const changed = new Set(q.propDoc ? Object.values(q.data?.proposal?.changed ?? {}).flat() : []);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    if (wrap.current) ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);
  // fit the whole film once it first appears
  useEffect(() => {
    if (d && !view.current.fitted && total(d) > 0 && size.w > 200) {
      view.current = { pps: (size.w - LABEL_W - 20) / (total(d) / fps), x0: 0, fitted: true };
      redraw();
    }
  }, [d, size.w]);
  // waveforms of the sound sources
  useEffect(() => {
    for (const a of d?.tracks.audio ?? []) if (!peaks[a.src]) useStore.getState().loadPeaks(a.src);
  }, [d?.tracks.audio.map((a) => a.src).join()]);

  const X = (f: number) => LABEL_W + (f / fps - view.current.x0) * view.current.pps;
  const F = (x: number) => Math.round(((x - LABEL_W) / view.current.pps + view.current.x0) * fps);

  // ---------------------------------------------------------------- drawing
  useEffect(() => {
    const c = cv.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr; c.height = size.h * dpr;
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    g.font = "11px 'IBM Plex Sans', 'Segoe UI', sans-serif";
    const { pps, x0 } = view.current;
    // ruler
    g.fillStyle = css("--panel-2"); g.fillRect(0, 0, size.w, RULER);
    const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120].find((s) => s * pps >= 70) ?? 300;
    g.fillStyle = css("--fg-faint"); g.strokeStyle = css("--line");
    for (let t = Math.floor(x0 / step) * step; X(t * fps) < size.w; t += step) {
      const x = X(t * fps);
      if (x < LABEL_W) continue;
      g.beginPath(); g.moveTo(x + 0.5, RULER - 6); g.lineTo(x + 0.5, RULER); g.stroke();
      g.fillText(fmtT(t).replace(/\.0$/, ""), x + 3, 13);
    }
    // row labels + lanes
    for (const r of rows) {
      g.fillStyle = css("--panel-2"); g.fillRect(LABEL_W, r.y, size.w - LABEL_W, r.h);
      g.fillStyle = css("--fg-dim"); g.fillText(r.label, 8, r.y + r.h / 2 + 4);
    }
    if (!d) return;
    const row = (k: RowKey) => rows.find((r) => r.key === k);
    const selIds = new Set(q.sel.map((s) => s.id));
    const running = new Map(jobs.filter((j) => (j.status === "running" || j.status === "queued") && j.source).map((j) => [j.source!, j]));
    const frame = (b: Block, r: Row, color: string, label: string, opts: { ghost?: boolean; hatch?: boolean } = {}) => {
      const x = X(b.start), w = Math.max(2, X(end(b)) - x);
      if (x > size.w || x + w < LABEL_W) return null;
      g.save();
      g.beginPath(); g.rect(LABEL_W, r.y, size.w - LABEL_W, r.h); g.clip();
      if (opts.hatch) {
        g.strokeStyle = css("--line-strong");
        for (let k = -r.h; k < w; k += 7) { g.beginPath(); g.moveTo(x + k, r.y + r.h); g.lineTo(x + k + r.h, r.y); g.stroke(); }
      } else {
        g.globalAlpha = opts.ghost ? 0.22 : 0.9;
        g.fillStyle = color;
        g.beginPath(); g.roundRect(x + 0.5, r.y + 1, w - 1, r.h - 2, 4); g.fill();
        g.globalAlpha = 1;
      }
      if (label && w > 24) {
        g.fillStyle = opts.ghost || opts.hatch ? css("--fg-faint") : "#fff";
        g.fillText(label, x + 5, r.y + r.h / 2 + 4, w - 8);
      }
      g.restore();
      return { x, w };
    };
    const outline = (x: number, w: number, r: Row, id: string, err?: boolean) => {
      if (selIds.has(id) || changed.has(id) || err) {
        g.save();
        g.strokeStyle = err ? css("--err") : selIds.has(id) ? css("--accent") : css("--warn");
        g.lineWidth = 2;
        if (changed.has(id) && !selIds.has(id)) g.setLineDash([5, 3]);
        g.beginPath(); g.roundRect(x + 1, r.y + 1.5, w - 2, r.h - 3, 4); g.stroke();
        g.restore();
      }
      const j = running.get(id);
      if (j) {   // per-block progress: the block is being (re)rendered or drawn
        g.fillStyle = css("--accent");
        g.globalAlpha = j.status === "running" ? 0.9 : 0.4;
        g.fillRect(x + 2, r.y + r.h - 4, Math.max(4, (w - 4) * (j.status === "running" ? Math.max(0.15, j.progress) : 0.08)), 2.5);
        g.globalAlpha = 1;
      }
    };
    // picture: each block in its camera's row; the other camera rows show what's available there
    for (const v of d.tracks.video) {
      const own = pictureRow(d, v);
      const c = cov[v.id] || {};
      for (const k of ["front", "top", "side"] as const) {
        const r = row(k);
        if (!r || k === own) continue;
        frame(v, r, css(ROLE_COLOR[k]), c[k] ? "показать" : "нет кадра", c[k] ? { ghost: true } : { hatch: true });
      }
      const r = row(own)!;
      const name = useStore.getState().sources.find((s) => s.id === v.src)?.name ?? v.src;
      const box = frame(v, r, css(ROLE_COLOR[roleOf(d, v.src)] ?? "--fg-faint"), (v.color ? "● " : "") + name);
      if (box) {
        const th = img(mediaUrl("thumb", v.src), redraw);
        if (th && box.w > 70) { g.save(); g.globalAlpha = 0.85; g.drawImage(th, box.x + box.w - 48, r.y + 3, 44, r.h - 6); g.restore(); }
        outline(box.x, box.w, r, v.id);
      }
    }
    // camera switches: a dashed line through the camera rows + a flag on the ruler in the new camera's colour
    {
      const cams = (["front", "top", "side"] as const).map((k) => row(k)).filter(Boolean) as Row[];
      const y0 = Math.min(...cams.map((r) => r.y)), y1 = Math.max(...cams.map((r) => r.y + r.h));
      let prev: RowKey | null = null;
      for (const v of d.tracks.video) {
        const k = pictureRow(d, v);
        if (prev && k !== prev) {
          const x = Math.round(X(v.start)) + 0.5;
          if (x >= LABEL_W && x <= size.w) {
            const c = css(ROLE_COLOR[k]);
            g.save();
            g.strokeStyle = c; g.lineWidth = 2; g.setLineDash([4, 3]);
            g.beginPath(); g.moveTo(x, y0); g.lineTo(x, y1); g.stroke();
            g.setLineDash([]);
            g.fillStyle = c;
            g.beginPath(); g.moveTo(x - 5, RULER - 9); g.lineTo(x + 5, RULER - 9); g.lineTo(x, RULER - 2); g.closePath(); g.fill();
            if (X(v.start + v.len) - x > 46) { g.font = "600 10px sans-serif"; g.fillText("→ " + ({ front: "Фронт", top: "Верх", side: "Сбоку" } as Record<string, string>)[k], x + 6, RULER - 3); }
            g.restore();
          }
        }
        prev = k;
      }
    }
    const rs = row("slides")!;
    for (const s of d.tracks.slides) {
      const box = frame(s, rs, css("--track-s"), `Слайд ${s.page}${s.layout === "full" ? " · весь кадр" : ""}`);
      if (!box) continue;
      const th = img(quickUrl(`/media/quick/slide/${s.page}?thumb=1`), redraw);
      if (th && box.w > 60) g.drawImage(th, box.x + box.w - 40, rs.y + 3, 36, rs.h - 6);
      outline(box.x, box.w, rs, s.id);
    }
    const ro = row("overlays")!;
    for (const o of d.tracks.overlays) {
      const st = o.status ?? "placeholder";
      const label = (o.spec?.title as string) || o.hint || "карточка";
      const box = frame(o, ro, css("--track-i"), (st === "ready" ? "" : st === "error" ? "⚠ " : "… ") + label);
      if (!box) continue;
      if (o.files?.poster) {
        const th = img(quickUrl(`/media/quick/card/${o.id}/poster?v=${o.files.v}`), redraw);
        if (th && box.w > 50) { g.save(); g.beginPath(); g.rect(box.x, ro.y, box.w, ro.h); g.clip(); g.drawImage(th, box.x + 2, ro.y + 2, (ro.h - 4) * 16 / 9, ro.h - 4); g.restore(); }
      }
      outline(box.x, box.w, ro, o.id, st === "error");
    }
    const ra = row("audio")!;
    for (const a of d.tracks.audio) {
      const box = frame(a, ra, css("--track-a"), "");
      if (!box) continue;
      const pk = peaks[a.src];
      if (pk instanceof Uint8Array) {   // miniature waveform of the sound at this block's in-point
        g.fillStyle = css("--wave");
        const mid = ra.y + ra.h / 2;
        for (let x = Math.max(box.x, LABEL_W); x < Math.min(box.x + box.w, size.w); x += 2) {
          const t = a.in + ((x - box.x) / pps);
          const v = pk[Math.floor(t * 100)] ?? 0;
          const hh = (v / 255) * (ra.h / 2 - 3);
          g.fillRect(x, mid - hh, 1.5, hh * 2);
        }
      }
      const style = q.data?.styles?.[a.style ?? "clean"] ?? "";
      if (box.w > 60) { g.fillStyle = "#fff"; g.fillText(style, box.x + 5, ra.y + 12, box.w - 8); }
      outline(box.x, box.w, ra, a.id);
    }
    // band selection
    const dr = drag.current;
    if (dr?.kind === "band") {
      g.strokeStyle = css("--accent"); g.fillStyle = css("--accent-soft");
      const bx = Math.min(dr.x0, dr.x1), by = Math.min(dr.y0, dr.y1);
      g.fillRect(bx, by, Math.abs(dr.x1 - dr.x0), Math.abs(dr.y1 - dr.y0));
      g.strokeRect(bx + 0.5, by + 0.5, Math.abs(dr.x1 - dr.x0), Math.abs(dr.y1 - dr.y0));
    }
    // playhead
    const px = X(q.time * fps);
    if (px >= LABEL_W) {
      g.strokeStyle = css("--accent"); g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(px, 0); g.lineTo(px, size.h); g.stroke();
      g.fillStyle = css("--accent"); g.beginPath(); g.moveTo(px - 5, 0); g.lineTo(px + 5, 0); g.lineTo(px, 7); g.fill();
    }
  });

  // ---------------------------------------------------------------- hit testing
  type Hit = { ref: Ref; edge?: "start" | "end"; row: Row; ghost?: string } | { row: Row | null; frame: number };
  const hit = (x: number, y: number): Hit => {
    const r = rows.find((rr) => y >= rr.y && y < rr.y + rr.h) ?? null;
    if (!d || !r || x < LABEL_W) return { row: r, frame: F(x) };
    const f = F(x);
    const near = (b: Block): "start" | "end" | undefined => Math.abs(x - X(b.start)) <= EDGE ? "start" : Math.abs(x - X(end(b))) <= EDGE ? "end" : undefined;
    const at = (bs: Block[]) => bs.find((b) => (f >= b.start && f < end(b)) || near(b));
    if (r.key === "front" || r.key === "top" || r.key === "side") {
      const v = at(d.tracks.video) as VBlock | undefined;
      if (!v) return { row: r, frame: f };
      if (pictureRow(d, v) !== r.key) return (cov[v.id] || {})[r.key] ? { ref: { track: "video", id: v.id }, row: r, ghost: r.key } : { row: r, frame: f };
      return { ref: { track: "video", id: v.id }, edge: near(v), row: r };
    }
    const track: TrackName = r.key === "audio" ? "audio" : r.key === "slides" ? "slides" : "overlays";
    const b = at(d.tracks[track] as Block[]);
    return b ? { ref: { track, id: b.id }, edge: near(b), row: r } : { row: r, frame: f };
  };

  const snapDelta = (base: QDoc, refs: Ref[], delta: number): number => {
    if (!q.snap) return delta;
    const ids = new Set(refs.map((r) => r.id));
    const es = [...edges(base, ids), Math.round(q.time * fps)];
    const tol = Math.max(1, Math.round(8 / view.current.pps * fps));
    let best = delta, bd = Infinity;
    for (const r of refs) {
      const b = (base.tracks[r.track] as Block[]).find((x) => x.id === r.id)!;
      for (const p of [b.start + delta, end(b) + delta]) {
        const s = snapFrame(p, es, tol);
        if (s !== p && Math.abs(s - p) < bd) { bd = Math.abs(s - p); best = delta + (s - p); }
      }
    }
    return best;
  };

  // ---------------------------------------------------------------- pointer
  const pos = (e: React.PointerEvent | React.MouseEvent) => {
    const rc = cv.current!.getBoundingClientRect();
    return { x: e.clientX - rc.left, y: e.clientY - rc.top };
  };
  const srcDur = (id: string) => useStore.getState().sources.find((s) => s.id === id)?.probe?.duration ?? 1e9;

  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0 || !d) return;
    const { x, y } = pos(e);
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    if (y < RULER) { drag.current = { kind: "scrub" }; qSeek(Math.max(0, F(x) / fps)); return; }
    const h = hit(x, y);
    if (!("ref" in h)) {
      drag.current = { kind: "band", x0: x, y0: y, x1: x, y1: y, add: e.ctrlKey || e.shiftKey };
      return;
    }
    if (h.ghost) { drag.current = { kind: "angle", ids: [h.ref.id], role: h.ghost, x0: x }; return; }
    const isSel = q.sel.some((s) => s.id === h.ref.id);
    let sel = q.sel;
    if (e.ctrlKey || e.shiftKey) sel = isSel ? q.sel.filter((s) => s.id !== h.ref.id) : [...q.sel, h.ref];
    else if (!isSel) sel = [h.ref];
    useQuick.setState({ sel });
    if (locked) return;
    if (h.edge && sel.length <= 1) drag.current = { kind: "trim", ref: h.ref, edge: h.edge, x0: x, base: d };
    else drag.current = { kind: "move", refs: sel.length ? sel : [h.ref], x0: x, base: d, moved: false };
  };

  const onMove = (e: React.PointerEvent) => {
    const { x, y } = pos(e);
    const dr = drag.current;
    if (!dr) {
      if (!d || locked) return;
      const h = hit(x, y);
      cv.current!.style.cursor = y < RULER ? "col-resize" : "ref" in h ? (h.ghost ? "pointer" : h.edge ? "ew-resize" : "grab") : "default";
      return;
    }
    if (dr.kind === "scrub") { qSeek(Math.max(0, F(x) / fps)); return; }
    if (dr.kind === "band") { dr.x1 = x; dr.y1 = y; redraw(); return; }
    if (dr.kind === "angle") return;
    const overwrite = e.altKey || q.overwrite;
    const df = Math.round((x - dr.x0) / view.current.pps * fps);
    if (dr.kind === "move") {
      if (!dr.moved && Math.abs(x - dr.x0) < 3) return;
      dr.moved = true;
      useQuick.setState({ draft: move(dr.base, dr.refs, snapDelta(dr.base, dr.refs, df), overwrite) });
    } else if (dr.kind === "trim") {
      const b = (dr.base.tracks[dr.ref.track] as Block[]).find((bb) => bb.id === dr.ref.id)!;
      let delta = df;
      if (q.snap) {
        const p = (dr.edge === "start" ? b.start : end(b)) + df;
        const s = snapFrame(p, [...edges(dr.base, new Set([b.id])), Math.round(q.time * fps)], Math.max(1, Math.round(8 / view.current.pps * fps)));
        delta += s - p;
      }
      useQuick.setState({ draft: trim(dr.base, dr.ref, dr.edge, delta, srcDur, overwrite) });
    }
  };

  const onUp = async (e: React.PointerEvent) => {
    const dr = drag.current;
    drag.current = null;
    if (!dr || !d) return;
    const { x } = pos(e);
    if (dr.kind === "band") {
      const fx0 = F(Math.min(dr.x0, dr.x1)), fx1 = F(Math.max(dr.x0, dr.x1));
      const y0 = Math.min(dr.y0, dr.y1), y1 = Math.max(dr.y0, dr.y1);
      if (Math.abs(dr.x1 - dr.x0) < 3 && Math.abs(dr.y1 - dr.y0) < 3) {
        if (!dr.add) useQuick.setState({ sel: [] });
        qSeek(Math.max(0, F(x) / fps));
        redraw();
        return;
      }
      const got: Ref[] = [];
      for (const r of rows) {
        if (r.y + r.h < y0 || r.y > y1) continue;
        const track: TrackName = r.key === "audio" ? "audio" : r.key === "slides" ? "slides" : r.key === "overlays" ? "overlays" : "video";
        for (const b of d.tracks[track] as Block[]) {
          if (track === "video" && pictureRow(d, b as VBlock) !== r.key) continue;
          if (b.start < fx1 && end(b) > fx0 && !got.some((g) => g.id === b.id)) got.push({ track, id: b.id });
        }
      }
      useQuick.setState({ sel: dr.add ? [...q.sel, ...got.filter((g) => !q.sel.some((s) => s.id === g.id))] : got });
      redraw();
      return;
    }
    if (dr.kind === "angle") {
      if (Math.abs(x - dr.x0) > 4 || locked) return;
      const ids = q.sel.length > 1 && q.sel.some((s) => s.id === dr.ids[0]) ? q.sel.filter((s) => s.track === "video").map((s) => s.id) : dr.ids;
      try {
        const r = await api.post<import("../quickStore").QuickPayload>("/api/quick/angle", { ids, role: dr.role });
        useQuick.setState({ data: r });
        if (r.warnings?.length) useStore.getState().say("info", r.warnings.join("; "));
      } catch (err) { useStore.getState().say("err", (err as Error).message); }
      return;
    }
    const draft = useQuick.getState().draft;
    if ((dr.kind === "move" && !dr.moved) || !draft) { useQuick.setState({ draft: null }); return; }
    const overwrite = e.altKey || q.overwrite;
    await useQuick.getState().commit(draft, dr.kind === "move" ? (overwrite ? "перенос с заменой" : "перенос") : (overwrite ? "растяжка с заменой" : "подрезка"));
  };

  const onDouble = (e: React.MouseEvent) => {
    const { x, y } = pos(e);
    const h = hit(x, y);
    if (h.row?.key !== "slides" || locked) return;
    useQuick.setState({ dialog: { kind: "slides", at: "frame" in h ? h.frame : (d!.tracks.slides.find((s) => s.id === h.ref.id)!.start), replace: "ref" in h ? h.ref.id : undefined } });
  };
  const onContext = (e: React.MouseEvent) => {
    e.preventDefault();
    const { x, y } = pos(e);
    const h = hit(x, y);
    if (!("ref" in h) || locked) return;
    useQuick.setState({ sel: [h.ref] });
    if (h.ref.track === "overlays") useQuick.setState({ dialog: { kind: "card", id: h.ref.id } });
    if (h.ref.track === "audio") useQuick.setState({ dialog: { kind: "sound", id: h.ref.id } });
  };
  const onWheel = (e: React.WheelEvent) => {
    const v = view.current;
    if (e.ctrlKey) {
      const { x } = pos(e);
      const t = (x - LABEL_W) / v.pps + v.x0;
      v.pps = Math.max(0.5, Math.min(400, v.pps * (e.deltaY < 0 ? 1.18 : 1 / 1.18)));
      v.x0 = Math.max(0, t - (x - LABEL_W) / v.pps);
    } else v.x0 = Math.max(0, v.x0 + (e.deltaY + e.deltaX) / v.pps * 0.6);
    redraw();
  };

  const zoom = (k: number) => { const v = view.current; v.pps = Math.max(0.5, Math.min(400, v.pps * k)); redraw(); };
  const fit = () => { if (d) { view.current = { pps: (size.w - LABEL_W - 20) / Math.max(1, total(d) / fps), x0: 0, fitted: true }; redraw(); } };

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="h-8 shrink-0 flex items-center gap-1.5 px-2 border-b border-line text-[12px]">
        <button className="btn btn-ghost btn-sm" title="Разрезать под бегунком (S)" onClick={() => window.dispatchEvent(new CustomEvent("quick-cmd", { detail: "split" }))} disabled={locked}>Разрезать</button>
        <button className="btn btn-ghost btn-sm" title="Удалить выделенное (Delete), со сдвигом — Shift+Delete" onClick={() => window.dispatchEvent(new CustomEvent("quick-cmd", { detail: "delete" }))} disabled={locked || !q.sel.length}>Удалить</button>
        <button className="btn btn-ghost btn-sm" title="Удалить и сдвинуть всё после (Shift+Delete)" onClick={() => window.dispatchEvent(new CustomEvent("quick-cmd", { detail: "ripple" }))} disabled={locked || !q.sel.length}>Удалить со сдвигом</button>
        <button className="btn btn-ghost btn-sm" title="Убрать пустые места между блоками картинки (всё после сдвигается)" disabled={locked || !d}
          onClick={() => { if (d) { const nd = closeGaps(d); if (nd !== d) q.commit(nd, "закрыть паузы"); } }}>Закрыть паузы</button>
        <span className="w-px h-4 bg-line mx-1" />
        <button className="chip" aria-pressed={q.overwrite} onClick={() => q.set({ overwrite: !q.overwrite })} title="Перенос и растяжка заменяют то, на что ложится блок (или держите Alt)">Перезапись</button>
        <button className="chip" aria-pressed={q.snap} onClick={() => q.set({ snap: !q.snap })} title="Прилипание к краям блоков и бегунку">Прилипание</button>
        <span className="w-px h-4 bg-line mx-1" />
        <button className="btn btn-ghost btn-sm" onClick={() => window.dispatchEvent(new CustomEvent("quick-cmd", { detail: "undo" }))} disabled={!q.data?.history.can_undo || !!q.propDoc} title="Ctrl+Z">Отменить</button>
        <button className="btn btn-ghost btn-sm" onClick={() => window.dispatchEvent(new CustomEvent("quick-cmd", { detail: "redo" }))} disabled={!q.data?.history.can_redo || !!q.propDoc} title="Ctrl+Shift+Z">Повторить</button>
        <span className="flex-1" />
        <span className="text-dim tabular-nums">{fmtT(q.time)} / {fmtT(d ? total(d) / fps : 0)}</span>
        <button className="btn btn-ghost btn-sm" onClick={() => zoom(1 / 1.4)} aria-label="Мельче">−</button>
        <button className="btn btn-ghost btn-sm" onClick={fit} title="Весь ролик">Весь</button>
        <button className="btn btn-ghost btn-sm" onClick={() => zoom(1.4)} aria-label="Крупнее">+</button>
      </div>
      <div ref={wrap} className="flex-1 min-h-0 relative">
        <canvas ref={cv} style={{ width: size.w, height: size.h }} className="block"
          onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onDoubleClick={onDouble} onContextMenu={onContext} onWheel={onWheel}
          aria-label="Таймлайн быстрого режима" />
        {!d?.tracks.video.length && (
          <div className="absolute inset-0 grid place-items-center text-dim text-[12.5px] pointer-events-none">Таймлайн появится после нарезки (шаг 2).</div>
        )}
      </div>
    </div>
  );
}

export type { ABlock, OBlock, SBlock };
