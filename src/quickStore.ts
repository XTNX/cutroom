// Quick mode state: the block document from the server, the selection, the AI proposal, the play clock.
import { create } from "zustand";
import { api } from "./api";
import { fpsOf, total, type QDoc, type Ref } from "./quickOps";
import { useStore } from "./store";

export type Cover = Record<string, Record<"front" | "top" | "side", { src: string; in: number } | null>>;
export type BuildStats = { profile: string; version: number; file: string; segments: number; duration: number; rebuilt: string[]; cached: string[];
  audio_rebuilt: string[]; audio_cached: number; t_concat: number; t_audio: number; t_total: number; when: string };
export type Proposal = { id: string; kind: "cut" | "request"; prompt: string; summary: string; warnings: string[]; created: string; fast?: boolean;
  changed: Record<string, string[]>; selection?: [number, number] | null };
export type QuickPayload = {
  doc: QDoc | null; mode: "quick" | "full"; gate_open: boolean; proposal: Proposal | null; coverage: Cover;
  history: { head: number | null; can_undo: boolean; can_redo: boolean; versions: { id: number; author: string; note: string }[] };
  build: { draft: BuildStats | null; final: BuildStats | null };
  brand: { found: boolean; need: string | null; colors: Record<string, string>; note: string | null };
  styles: Record<string, string>;
  beds?: Record<string, number | null>;   // music bed level (dB under the speech) per sound style
  warnings?: string[];
  live?: { on: boolean; preview?: string | null };
};
export type Step = 1 | 2 | 3 | 4;
export type Dialog = { kind: "card"; id: string } | { kind: "sound"; id: string } | { kind: "slides"; at: number; replace?: string } | null;

export type LiveT = { label?: string; title?: string; text?: string; kind?: string; group?: string; n?: number | null; t: number | null; t1?: number | null; ht?: number | null };
export type LiveStatus = { on: boolean; spec?: Record<string, unknown>; warnings?: string[]; preview?: string | null;
  layer?: { start: number | null; months: LiveT[]; marks: LiveT[]; cards: LiveT[]; windows: { kind: string; group: string; t: number; t1: number }[];
    intro: { t0: number; t1: number; title: string } | null; cta: { t0: number; t1: number; title: string } | null } | null };

type QState = {
  live: LiveStatus | null;         // the live infographic layer: spec + resolved times (for the panel, the timeline, the monitor)
  data: QuickPayload | null;
  draft: QDoc | null;              // a drag in progress (shown instead of data.doc until committed)
  propDoc: QDoc | null;            // the AI proposal's document (shown while a proposal is open)
  propCover: Cover;
  sel: Ref[];
  step: Step;
  time: number;                    // seconds on the finished-video clock
  playing: boolean;
  assembled: boolean;              // monitor shows the assembled preview file instead of the live proxies
  dialog: Dialog;
  overwrite: boolean;
  ripple: boolean;                 // edge drags change the length and move the rest of the film (neighbours untouched)
  snap: boolean;
  set: (p: Partial<QState>) => void;
  load: () => Promise<void>;
  loadProposal: () => Promise<void>;
  commit: (doc: QDoc, note: string) => Promise<void>;
};

export const useQuick = create<QState>((set, get) => ({
  live: null, data: null, draft: null, propDoc: null, propCover: {}, sel: [], step: 1, time: 0, playing: false, assembled: false,
  dialog: null, overwrite: false, ripple: false, snap: true,
  set: (p) => set(p),
  load: async () => {
    const d = await api.get<QuickPayload>("/api/quick");
    set({ data: d });
    if (d.proposal) await get().loadProposal(); else set({ propDoc: null, propCover: {} });
    if (d.live?.on) api.get<LiveStatus>("/api/quick/live").then((l) => set({ live: l })).catch(() => {});
    else set({ live: null });
  },
  loadProposal: async () => {
    const r = await api.get<{ proposal: Proposal | null; doc: QDoc | null; coverage: Cover }>("/api/quick/proposal");
    set({ propDoc: r.doc, propCover: r.coverage || {} });
  },
  commit: async (doc, note) => {
    set({ draft: doc });
    try {
      const d = await api.put<QuickPayload>("/api/quick/doc", { doc, note });
      set({ data: d, draft: null });
    } catch (e) {
      set({ draft: null });
      useStore.getState().say("err", (e as Error).message);
    }
  },
}));

/** The document on screen: a drag in progress, else the open proposal, else the saved one. */
export function shownDoc(s: QState = useQuick.getState()): QDoc | null {
  return s.draft ?? s.propDoc ?? s.data?.doc ?? null;
}
export function shownCover(s: QState = useQuick.getState()): Cover {
  return s.propDoc && !s.draft ? s.propCover : s.data?.coverage ?? {};
}

export const roleOf = (d: QDoc | null, src: string) => (d?.roles?.[src] as "front" | "top" | "side" | "other" | "lav" | undefined) ?? "other";
export const ROLE_NAMES: Record<string, string> = { front: "Фронт", top: "Верх", side: "Сбоку", other: "Другое", lav: "Петличка" };

export function fmtT(t: number): string {
  t = Math.max(0, t);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

// ---------------------------------------------------------------- play clock (rAF, the monitors follow it)

let raf = 0, t0 = 0, base = 0;
export function qPlay() {
  const s = useQuick.getState();
  const d = shownDoc();
  if (!d) return;
  const dur = total(d) / fpsOf(d);
  if (s.time >= dur - 0.05) s.set({ time: 0 });
  base = useQuick.getState().time;
  t0 = performance.now();
  useQuick.setState({ playing: true });
  const tick = () => {
    const t = base + (performance.now() - t0) / 1000;
    if (t >= dur) { useQuick.setState({ time: dur, playing: false }); return; }
    useQuick.setState({ time: t });
    raf = requestAnimationFrame(tick);
  };
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(tick);
}
export function qPause() {
  cancelAnimationFrame(raf);
  useQuick.setState({ playing: false });
}
export function qToggle() {
  if (useQuick.getState().playing) qPause(); else qPlay();
}
export function qSeek(t: number) {
  const playing = useQuick.getState().playing;
  if (playing) qPause();
  useQuick.setState({ time: Math.max(0, t) });
  if (playing) qPlay();
}

/** The selection as a frame range (for AI context and the colour/sound panels). */
export function selRange(): [number, number] | null {
  const s = useQuick.getState();
  const d = shownDoc(s);
  if (!d || !s.sel.length) return null;
  const bs = s.sel.map((r) => (d.tracks[r.track] as { id: string; start: number; len: number }[]).find((b) => b.id === r.id)).filter(Boolean) as { start: number; len: number }[];
  if (!bs.length) return null;
  return [Math.min(...bs.map((b) => b.start)), Math.max(...bs.map((b) => b.start + b.len))];
}
