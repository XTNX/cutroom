import { useEffect, useRef, useState } from "react";
import { api, type EdlRange, type EdlState, type Word } from "../api";
import { cameraIndex, currentDuration, groupOf, mapTime, markerOut, seek, selection, useStore } from "../store";
import { FragmentBar } from "./FragmentBar";
import { cls, fmtTime, isFiller } from "../util";
import { insertPaths, insertSources } from "../timelineOps";
import { runCommand } from "./CommandPalette";

const RULER = 22;
const LABEL_W = 44;
const H_V = 34, H_A = 60, H_I = 24, H_S = 30, GAP = 6;

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";

type Drag = { kind: "trim"; i: number; edge: "start" | "end"; ranges: EdlRange[] } | { kind: "scrub" }
  | { kind: "slot"; id: string; mode: "move" | "start" | "end"; t0: number; s0: number; d0: number; moved: boolean } | null;
const I_Y = RULER + 4 + H_V + GAP + H_A + GAP;   // program mode: one V row
type Piece = { src: string; a: number; b: number; out: number; i: number; r?: EdlRange };

export function Timeline() {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 800, h: 200 });
  const view = useRef({ pps: 40, x0: 0 });
  const drag = useRef<Drag>(null);
  const [, force] = useState(0);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const st = useStore();
  const program = st.monitor === "program" && !!st.edl?.edl;
  const edl = st.edl?.edl;
  const source = st.sources.find((x) => x.id === st.selected);
  const dur = program ? edl!.total_duration_s : source?.probe?.duration ?? 0;
  const fps = source?.probe?.video?.fps || 25;
  const key = program ? `p${edl!.cutroom.version_id}` : `s${st.selected}`;

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    if (wrap.current) ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  // program mode needs transcripts + peaks of every source in the EDL
  useEffect(() => {
    if (!program) return;
    const ids = new Set<string>();
    edl!.ranges.forEach((r) => { ids.add(r.source); if (r.angle) ids.add(r.angle); });
    ids.forEach((id) => { st.loadTranscript(id); st.loadPeaks(id); });
  }, [program, edl?.cutroom.version_id]);

  const fit = () => { if (dur) view.current = { pps: Math.max(2, (size.w - LABEL_W - 16) / dur), x0: 0 }; };
  useEffect(() => { fit(); force((n) => n + 1); }, [program ? "program" : st.selected, size.w, program ? 0 : dur]);

  const X = (sec: number) => LABEL_W + (sec - view.current.x0) * view.current.pps;
  const T = (px: number) => view.current.x0 + (px - LABEL_W) / view.current.pps;

  const draw = () => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(size.w * dpr) || c.height !== Math.round(size.h * dpr)) {
      c.width = Math.round(size.w * dpr);
      c.height = Math.round(size.h * dpr);
    }
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const S = useStore.getState();
    const { pps, x0 } = view.current;
    const W = size.w, H = size.h;
    const line = css("--line"), dim = css("--fg-faint"), fgc = css("--fg");
    g.fillStyle = css("--panel");
    g.fillRect(0, 0, W, H);
    g.font = "10.5px 'IBM Plex Mono', Consolas, monospace";

    // ruler (seconds; frames when zoomed in)
    const steps = [1 / fps, 5 / fps, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const step = steps.find((s) => s * pps >= 70) ?? 600;
    g.fillStyle = dim;
    g.strokeStyle = line;
    for (let s = Math.floor(x0 / step) * step; X(s) < W; s += step) {
      const x = Math.round(X(s)) + 0.5;
      if (x < LABEL_W) continue;
      g.beginPath(); g.moveTo(x, RULER - 6); g.lineTo(x, H); g.stroke();
      g.fillText(step < 1 ? `${fmtTime(s)} ·${Math.round((s % 1) * fps)}к` : fmtTime(s).replace(/\.\d+$/, ""), x + 3, 13);
    }
    g.beginPath(); g.moveTo(0, RULER + 0.5); g.lineTo(W, RULER + 0.5); g.stroke();

    // V rows: program = 1 row of EDL ranges; source = the clip + other angles of its take
    const grp = !program && source ? groupOf(source.id) : undefined;
    const vIds = program || !source ? [] : grp ? [source.id, ...grp.members.map((m) => m.id).filter((i) => i !== source.id)].slice(0, 4) : [source.id];
    const vH = vIds.length > 1 ? H_V * 2 - 4 : H_V;
    const vRowH = vIds.length > 1 ? Math.floor(vH / vIds.length) : H_V;
    let y = RULER + 4;
    const rows: Record<string, number> = {};
    for (const [k, h] of [["V", vH], ["A", H_A], ["I", H_I], ["S", H_S]] as const) {
      rows[k] = y;
      g.fillStyle = dim;
      if (k === "V" && vIds.length > 1) vIds.forEach((_, r) => g.fillText(`V${r + 1}`, 10, y + r * vRowH + vRowH / 2 + 4));
      else g.fillText(k === "V" ? "V1" : k, 10, y + h / 2 + 4);
      y += h + GAP;
      g.strokeStyle = line;
      g.beginPath(); g.moveTo(LABEL_W, y - 3 + 0.5); g.lineTo(W, y - 3 + 0.5); g.stroke();
    }
    g.save();
    g.beginPath(); g.rect(LABEL_W, 0, W - LABEL_W, H); g.clip();

    // pieces: what plays where on this clock
    const pieces: Piece[] = program
      ? edl!.ranges.map((r, i) => {
          const g2 = r.angle ? groupOf(r.source) : undefined;
          const src = r.angle && g2 ? r.angle : r.source;
          const a = g2 && r.angle ? mapTime(g2, r.source, r.angle, r.start) : r.start;
          return { src, a, b: a + (r.end - r.start), out: r.out_start, i, r };
        })
      : source ? [{ src: source.id, a: 0, b: dur, out: 0, i: 0 }] : [];

    // V
    const vc = css("--track-v");
    const vy = rows.V;
    if (program) {
      pieces.forEach((p) => {
        const xa = X(p.out), xb = X(p.out + (p.b - p.a));
        if (xb < LABEL_W || xa > W) return;
        // each fragment wears its camera's colour: angle changes are visible at a glance
        const camCol = css(`--cam${cameraIndex(p.src) + 1}`);
        g.fillStyle = vc;
        roundRect(g, xa + 0.5, vy, Math.max(1, xb - xa - 1), H_V, 3); g.fill();
        g.fillStyle = camCol;
        g.fillRect(xa + 0.5, vy + H_V - 6, Math.max(1, xb - xa - 1), 6);
        g.fillRect(xa + 0.5, vy, 4, H_V - 6);
        const sel = selection();
        if (sel && p.i >= sel[0] && p.i <= sel[1]) {
          g.strokeStyle = css("--accent"); g.lineWidth = 2;
          roundRect(g, xa + 1, vy + 1, Math.max(1, xb - xa - 2), H_V - 2, 3); g.stroke(); g.lineWidth = 1;
        }
        g.save(); g.beginPath(); g.rect(xa, vy, xb - xa, H_V); g.clip();
        g.fillStyle = fgc;
        g.fillText(`${p.r?.beat ? p.r.beat + " · " : ""}${p.src}`, xa + 8, vy + 14);
        g.fillStyle = dim;
        g.fillText(`${(p.b - p.a).toFixed(1)} с`, xa + 8, vy + 25);
        g.restore();
      });
    } else if (source?.kind === "video") {
      vIds.forEach((id, r) => {
        const s2 = S.sources.find((x) => x.id === id);
        const start = grp && id !== source.id ? -mapTime(grp, source.id, id, 0) : 0;
        const a0 = X(start), b0 = X(start + (s2?.probe?.duration ?? 0));
        const yy = vy + r * vRowH;
        g.fillStyle = id === source.id ? vc : vc + "99";
        roundRect(g, a0, yy + 1, Math.max(1, b0 - a0), vRowH - 2, 3); g.fill();
        g.fillStyle = fgc;
        g.fillText(s2?.name ?? id, Math.max(a0, LABEL_W) + 6, yy + vRowH / 2 + 4);
      });
    }

    // A: waveform of each piece from its own source peaks
    const ay = rows.A;
    const mid = ay + H_A / 2;
    pieces.forEach((p) => {
      const pk = S.peaks[p.src];
      const xa = Math.max(LABEL_W, X(p.out)), xb = Math.min(W, X(p.out + (p.b - p.a)));
      if (xb <= xa) return;
      g.fillStyle = css("--track-a") + "55";
      g.fillRect(xa, ay, xb - xa, H_A);
      if (!(pk instanceof Uint8Array)) return;
      g.fillStyle = css("--wave");
      for (let px = Math.floor(xa); px < xb; px++) {
        const s0 = p.a + (T(px) - p.out), s1 = s0 + 1 / pps;
        const i0 = Math.max(0, Math.floor(s0 * 100)), i1 = Math.min(pk.length, Math.max(i0 + 1, Math.ceil(s1 * 100)));
        let m = 0;
        for (let i = i0; i < i1; i++) if (pk[i] > m) m = pk[i];
        const h = (m / 255) * (H_A / 2 - 2);
        g.fillRect(px, mid - h, 1, h * 2 || 1);
      }
    });

    // S: words on this clock (+ silence gaps ≥ 0.4 s tinted on A in source mode: cut candidates)
    const sy = rows.S;
    const lowC = css("--warn"), filC = css("--accent"), sc = css("--track-s"), info = css("--info") + "33";
    pieces.forEach((p) => {
      const t = S.transcripts[p.src];
      if (typeof t !== "object") return;
      const ws: Word[] = t.words;
      for (let k = 0; k < ws.length; k++) {
        const w = ws[k];
        if (w[2] < p.a || w[1] > p.b) continue;
        const wx = X(p.out + (w[1] - p.a)), ww = Math.max(1, (w[2] - w[1]) * pps);
        const n = ws[k + 1];
        if (!program && n && n[1] - w[2] >= 0.4) { g.fillStyle = info; g.fillRect(X(p.out + (w[2] - p.a)), ay, (n[1] - w[2]) * pps, H_A); }
        if (wx + ww < LABEL_W || wx > W) continue;
        g.fillStyle = isFiller(w[0]) ? filC + "66" : w[3] < 0.6 ? lowC + "55" : sc;
        g.fillRect(wx, sy + 2, Math.max(1, ww - 1), H_S - 4);
        if (ww > 24) {
          g.save(); g.beginPath(); g.rect(wx, sy, ww - 2, H_S); g.clip();
          g.fillStyle = fgc; g.fillText(w[0], wx + 3, sy + H_S / 2 + 4); g.restore();
        }
      }
    });

    // I: infographic slots — colour = state, drag to move, edges to stretch
    const iy = rows.I;
    if (program && edl!.slots.length) {
      edl!.slots.forEach((s) => {
        const xa = X(s.start_in_output), xb = X(s.start_in_output + s.duration);
        const col = s.status === "ready" ? css("--accent") : s.status === "error" ? css("--warn") : css("--track-i");
        g.fillStyle = col; g.globalAlpha = s.status === "ready" ? 0.85 : 1;
        roundRect(g, xa, iy, Math.max(2, xb - xa), H_I, 3); g.fill(); g.globalAlpha = 1;
        if (s.status === "working") { g.fillStyle = css("--accent"); g.fillRect(xa, iy + H_I - 3, Math.max(2, xb - xa), 3); }
        if (S.selSlot === s.id) { g.strokeStyle = fgc; g.lineWidth = 1.5; roundRect(g, xa + 0.5, iy + 0.5, Math.max(2, xb - xa) - 1, H_I - 1, 3); g.stroke(); g.lineWidth = 1; }
        const title = typeof s.spec === "object" && s.spec ? s.spec.title : (s.spec_hint || "");
        const label = ({ placeholder: "задумано", working: "агент…", ready: "", error: "ошибка" } as Record<string, string>)[s.status] ?? s.status;
        g.save(); g.beginPath(); g.rect(xa, iy, xb - xa, H_I); g.clip();
        g.fillStyle = s.status === "ready" ? css("--bg") : fgc;
        g.fillText([title || s.type, label].filter(Boolean).join(" · "), xa + 5, iy + 15); g.restore();
      });
    } else {
      g.fillStyle = dim;
      g.fillText(program ? "инфографики нет · двойной клик — добавить слот" : "слоты инфографики появятся с черновой EDL", LABEL_W + 6, iy + 15);
    }

    if (program) {  // cut markers
      g.strokeStyle = css("--fg-dim");
      pieces.forEach((p) => { const x = Math.round(X(p.out)) + 0.5; g.beginPath(); g.moveTo(x, RULER); g.lineTo(x, sy + H_S); g.stroke(); });
    }
    g.restore();

    // markers: flags on the ruler, anchored to the material so they follow every re-cut
    const mk = css("--marker");
    for (const m of S.markers) {
      const t = program ? markerOut(m) : (m.source === S.selected ? m.time : null);
      if (t == null) continue;
      const x = Math.round(X(t)) + 0.5;
      if (x < LABEL_W || x > W) continue;
      g.strokeStyle = mk; g.globalAlpha = m.done ? 0.35 : 0.8;
      g.setLineDash([3, 3]); g.beginPath(); g.moveTo(x, RULER); g.lineTo(x, H); g.stroke(); g.setLineDash([]);
      g.fillStyle = mk; g.globalAlpha = m.done ? 0.45 : 1;
      g.beginPath(); g.moveTo(x, 2); g.lineTo(x + 11, 2); g.lineTo(x + 8, 8); g.lineTo(x + 11, 14); g.lineTo(x, 14); g.closePath(); g.fill();
      if (S.selMarker === m.id) { g.strokeStyle = css("--fg"); g.lineWidth = 1.5; g.stroke(); g.lineWidth = 1; }
      g.globalAlpha = 1;
    }

    // playhead
    const px = X(S.time);
    if (px >= LABEL_W && px <= W) {
      g.strokeStyle = css("--accent"); g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(px, 0); g.lineTo(px, H); g.stroke(); g.lineWidth = 1;
      g.fillStyle = css("--accent");
      g.beginPath(); g.moveTo(px - 5, 0); g.lineTo(px + 5, 0); g.lineTo(px, 7); g.fill();
    }
  };

  useEffect(draw);
  useEffect(() => useStore.subscribe((s, p) => {
    if (s.time === p.time && s.selRange === p.selRange && s.peaks === p.peaks && s.transcripts === p.transcripts && s.sync === p.sync && s.markers === p.markers && s.selMarker === p.selMarker && s.selEnd === p.selEnd && s.selSlot === p.selSlot) return;
    const v = view.current;
    const right = v.x0 + (size.w - LABEL_W) / v.pps;
    if (s.playing && (s.time > right - 0.5 || s.time < v.x0)) v.x0 = Math.max(0, s.time - 1);
    draw();
  }), [size, key]);

  // ---------------------------------------------------------------- interactions

  const edgeAt = (clientX: number, clientY: number): { i: number; edge: "start" | "end" } | null => {
    if (!program) return null;
    const r = canvas.current!.getBoundingClientRect();
    const x = clientX - r.left, y = clientY - r.top;
    if (y < RULER || y > RULER + 4 + H_V + GAP + H_A) return null;
    const e = useStore.getState().edl!.edl!;
    for (let i = 0; i < e.ranges.length; i++) {
      const rr = e.ranges[i];
      const xa = X(rr.out_start), xb = X(rr.out_start + rr.end - rr.start);
      if (Math.abs(x - xb) <= 5) return { i, edge: "end" };
      if (Math.abs(x - xa) <= 5 && i > 0) return { i, edge: "start" };
    }
    return null;
  };

  const slotAt = (clientX: number, clientY: number): { id: string; mode: "move" | "start" | "end" } | null => {
    if (!program) return null;
    const r = canvas.current!.getBoundingClientRect();
    const x = clientX - r.left, y = clientY - r.top;
    if (y < I_Y || y > I_Y + H_I) return null;
    for (const s of useStore.getState().edl!.edl!.slots) {
      const xa = X(s.start_in_output), xb = X(s.start_in_output + s.duration);
      if (x < xa - 4 || x > xb + 4) continue;
      return { id: s.id, mode: Math.abs(x - xb) <= 5 ? "end" : Math.abs(x - xa) <= 5 ? "start" : "move" };
    }
    return null;
  };

  // snap to word boundaries of the range's source (Hard Rule 6); the backend adds padding
  const snapWord = (srcId: string, t: number, edge: "start" | "end"): number => {
    const tr = useStore.getState().transcripts[srcId];
    if (typeof tr !== "object" || !tr.words.length) return t;
    let best = t, bd = Infinity;
    for (const w of tr.words) {
      const c = edge === "start" ? w[1] : w[2];
      const d = Math.abs(c - t);
      if (d < bd) { bd = d; best = c; }
    }
    return best;
  };

  const onDown = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    const S = useStore.getState();
    const hit = edgeAt(e.clientX, e.clientY);
    if (hit && S.strategy?.gate_open) {
      drag.current = { kind: "trim", i: hit.i, edge: hit.edge, ranges: S.edl!.edl!.ranges.map((r) => ({ ...r })) };
      return;
    }
    const sh = slotAt(e.clientX, e.clientY);
    if (sh) {
      const sl = S.edl!.edl!.slots.find((x) => x.id === sh.id)!;
      const r0 = canvas.current!.getBoundingClientRect();
      S.set({ selSlot: sh.id, rightTab: "gfx" });
      drag.current = { kind: "slot", id: sh.id, mode: sh.mode, t0: T(e.clientX - r0.left), s0: sl.start_in_output, d0: sl.duration, moved: false };
      return;
    }
    drag.current = { kind: "scrub" };
    const r = canvas.current!.getBoundingClientRect();
    const t = Math.max(0, T(e.clientX - r.left));
    const y = e.clientY - r.top;
    if (y < 16) {
      const hitM = S.markers.find((m) => {
        const mt = program ? markerOut(m) : (m.source === S.selected ? m.time : null);
        return mt != null && Math.abs(X(mt) - (e.clientX - r.left) + 5) <= 9;
      });
      if (hitM) { drag.current = null; S.set({ selMarker: hitM.id, leftTab: "markers" }); seek(program ? markerOut(hitM)! : hitM.time); return; }
    }
    if (program) {
      const i = S.edl!.edl!.ranges.findIndex((rr) => t >= rr.out_start && t < rr.out_start + rr.end - rr.start);
      if (y >= RULER && y <= RULER + 4 + H_V) {
        if (e.shiftKey && S.selRange != null && i >= 0) S.set({ selEnd: i });
        else S.set({ selRange: i >= 0 ? i : null, selEnd: null });
      }
    }
    seek(t);
  };

  const onMove = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    if (!(e.buttons & 1)) {
      const sh = slotAt(e.clientX, e.clientY);
      canvas.current!.style.cursor = edgeAt(e.clientX, e.clientY) || (sh && sh.mode !== "move") ? "ew-resize" : sh ? "grab" : "text";
      return;
    }
    const d = drag.current;
    if (!d) return;
    const t = T(e.clientX - r.left);
    if (d.kind === "scrub") { seek(Math.max(0, t)); return; }
    if (d.kind === "slot") {
      const S = useStore.getState();
      const e2 = S.edl!.edl!;
      const dt = t - d.t0;
      if (Math.abs(dt * view.current.pps) > 2) d.moved = true;
      const slots = e2.slots.map((s) => {
        if (s.id !== d.id) return s;
        if (d.mode === "move") return { ...s, start_in_output: Math.max(0, Math.min(e2.total_duration_s - d.d0, d.s0 + dt)) };
        if (d.mode === "end") return { ...s, duration: Math.max(2.5, Math.min(20, d.d0 + dt)) };
        const st = Math.max(0, Math.min(d.s0 + d.d0 - 2.5, d.s0 + dt));
        return { ...s, start_in_output: st, duration: d.s0 + d.d0 - st };
      });
      S.set({ edl: { ...S.edl!, edl: { ...e2, slots } } });
      draw();
      return;
    }
    // ripple trim: the edge follows the pointer, everything after it slides
    const S = useStore.getState();
    const ranges = d.ranges.map((x) => ({ ...x }));
    const rr = ranges[d.i];
    if (d.edge === "end") {
      rr.end = Math.max(rr.start + 0.3, snapWord(rr.source, rr.start + (t - rr.out_start), "end"));
    } else {
      const prev = ranges[d.i - 1];
      const prevEnd = prev.out_start + prev.end - prev.start;
      rr.start = Math.min(rr.end - 0.3, Math.max(0, snapWord(rr.source, rr.start + (t - prevEnd), "start")));
    }
    let off = 0;
    ranges.forEach((x) => { x.out_start = off; off += x.end - x.start; });
    S.set({ edl: { ...S.edl!, edl: { ...S.edl!.edl!, ranges, total_duration_s: off } } });
    draw();
  };

  const onUp = async () => {
    const d = drag.current;
    drag.current = null;
    if (d?.kind === "slot") {
      const S = useStore.getState();
      const sl = S.edl!.edl!.slots.find((x) => x.id === d.id);
      if (!d.moved || !sl) { if (sl) seek(sl.start_in_output + 0.01); return; }
      if (!S.strategy?.gate_open) { S.say("info", "Менять монтаж можно после утверждения стратегии."); S.loadEdl(); return; }
      try { S.set({ edl: await api.put<EdlState>(`/api/gfx/${d.id}`, { start_in_output: sl.start_in_output, duration: sl.duration }) }); }
      catch (err) { S.say("err", (err as Error).message); S.loadEdl(); }
      return;
    }
    if (d?.kind === "trim") {
      const S = useStore.getState();
      const rr = S.edl!.edl!.ranges[d.i];
      S.putRanges(S.edl!.edl!.ranges, `подрезка ${d.edge === "end" ? "конца" : "начала"} фрагмента ${d.i + 1} (${rr.source})`);
    }
  };

  return (
    <div className="h-full flex flex-col">
      <div className="h-7 shrink-0 flex items-center gap-3 px-3 border-b border-line text-[11.5px] text-faint">
        <span className="label">Таймлайн</span>
        <span className="truncate">{program ? `EDL v${edl!.cutroom.version_id} · ${edl!.ranges.length} фрагм. · ${edl!.total_duration_s.toFixed(1)} с` : source ? `${source.name} · исходник` : "нет источника"}</span>
        {program && selection() && <span className="badge badge-accent whitespace-nowrap">правки → {selection()![0] === selection()![1] ? `эпизод ${selection()![0] + 1}` : `эпизоды ${selection()![0] + 1}–${selection()![1] + 1}`}</span>}
        <span className="flex-1" />
        <span className="hidden xl:inline">{program ? "край — подрезка · Shift+клик — диапазон · M — маркер · S/X — разрезать/удалить" : "колесо — зум · M — маркер"}</span>
        {program && st.edl && (
          <>
            <button className="btn btn-ghost btn-sm" disabled={!st.edl.history.can_undo} onClick={() => runCommand("undo")} title="Ctrl+Z">Отменить</button>
            <button className="btn btn-ghost btn-sm" disabled={!st.edl.history.can_redo} onClick={() => runCommand("redo")} title="Shift+Ctrl+Z">Вернуть</button>
          </>
        )}
        <button className="btn btn-ghost btn-sm" onClick={() => { fit(); draw(); }}>Вписать</button>
      </div>
      {program && <FragmentBar />}
      <div ref={wrap} className={cls("flex-1 min-h-0 relative", dropAt != null && "ring-2 ring-accent ring-inset")}
        onDragOver={(e) => {
          const t = e.dataTransfer.types;
          if (!t.includes("application/x-cutroom-sources") && !t.includes("Files")) return;
          e.preventDefault();
          const r = canvas.current!.getBoundingClientRect();
          setDropAt(Math.max(0, T(e.clientX - r.left)));
        }}
        onDragLeave={() => setDropAt(null)}
        onDrop={async (e) => {
          e.preventDefault();
          const r = canvas.current!.getBoundingClientRect();
          const t = program ? Math.max(0, T(e.clientX - r.left)) : (useStore.getState().edl?.edl?.total_duration_s ?? 0);
          setDropAt(null);
          const ids = e.dataTransfer.getData("application/x-cutroom-sources");
          if (ids) return insertSources(JSON.parse(ids), t);
          const paths = [...e.dataTransfer.files].map((f) => window.cutroom?.pathForFile(f)).filter(Boolean) as string[];
          if (paths.length) return insertPaths(paths, t);
        }}>
        {dropAt != null && (
          <div className="absolute top-1 left-1/2 -translate-x-1/2 z-10 badge badge-accent pointer-events-none">
            {program ? `Отпустите — вставлю в ${fmtTime(dropAt)}` : "Отпустите — добавлю в конец монтажа"}
          </div>
        )}
        <canvas
          ref={canvas}
          style={{ width: size.w, height: size.h }}
          className="absolute inset-0 cursor-text"
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onDoubleClick={async (e) => {
            const r = canvas.current!.getBoundingClientRect();
            const y = e.clientY - r.top;
            const S = useStore.getState();
            if (!program || y < I_Y || y > I_Y + H_I || slotAt(e.clientX, e.clientY)) return;
            if (!S.strategy?.gate_open) return S.say("info", "Менять монтаж можно после утверждения стратегии.");
            try {
              const res = await api.post<EdlState>("/api/gfx/add", { at: Math.max(0, T(e.clientX - r.left)) });
              const ids = new Set(S.edl?.edl?.slots.map((x) => x.id));
              S.set({ edl: res, rightTab: "gfx", selSlot: res.edl?.slots.find((x) => !ids.has(x.id))?.id ?? null });
            } catch (err) { S.say("err", (err as Error).message); }
          }}
          onWheel={(e) => {
            const v = view.current;
            const r = canvas.current!.getBoundingClientRect();
            if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
              v.x0 = Math.max(0, v.x0 + (e.deltaX || e.deltaY) / v.pps);
            } else {
              const at = T(e.clientX - r.left);
              v.pps = Math.max(1, Math.min(1200, v.pps * Math.exp(-e.deltaY * 0.0015)));
              v.x0 = Math.max(0, at - (e.clientX - r.left - LABEL_W) / v.pps);
            }
            draw();
          }}
          aria-label="Таймлайн: клик — перейти ко времени"
          role="slider"
          aria-valuemin={0}
          aria-valuemax={currentDuration()}
          aria-valuenow={st.time}
        />
      </div>
    </div>
  );
}

function roundRect(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}
