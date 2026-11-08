import { create } from "zustand";
import { programDuration, programIsPlaying, programPause, programPlay, programSeek } from "./program";
import { api, type AudioInfo, type EdlRange, type EdlState, type Job, type Marker, type ProjectInfo, type Source, type Strategy, type SyncData, type SyncGroup, type Transcript } from "./api";

export type LeftTab = "sources" | "transcript" | "problems" | "markers";
export type RightTab = "strategy" | "gfx" | "color" | "subs" | "sound" | "export";
export type View = "edit" | "system";
export type AppMode = "quick" | "full";

type Layout = { left: number; right: number; bottom: number; leftOpen: boolean; rightOpen: boolean };
const DEFAULT_LAYOUT: Layout = { left: 300, right: 320, bottom: 240, leftOpen: true, rightOpen: true };

type State = {
  ready: boolean;
  online: boolean;
  project: ProjectInfo | null;
  sources: Source[];
  jobs: Job[];
  selected: string | null;
  selSources: string[];   // Ctrl/Shift-click in «Источники»: several files to drag onto the timeline
  time: number;
  playing: boolean;
  view: View;
  appMode: AppMode;               // quick (block timeline, 4 steps) or full (the original studio)
  leftTab: LeftTab;
  rightTab: RightTab;
  queueOpen: boolean;
  paletteOpen: boolean;
  wave: 1 | 2 | 3;
  layout: Layout;
  transcripts: Record<string, Transcript | "loading" | "missing">;
  peaks: Record<string, Uint8Array | "loading" | "missing">;
  sync: SyncData | null;
  strategy: Strategy | null;
  edl: EdlState | null;
  monitor: "program" | "source";
  compare: boolean;   // program monitor shows the before/after colour wipe
  selRange: number | null;
  selEnd: number | null;          // shift-click extends the selection to a span of ranges
  markers: Marker[];
  selMarker: string | null;
  selSlot: string | null;         // infographic slot picked on the I track / in the panel
  audio: AudioInfo | null;
  prompt: string;
  chips: Record<string, string | boolean>;
  toast: { kind: "ok" | "err" | "info"; text: string } | null;
  set: (p: Partial<State>) => void;
  setLayout: (p: Partial<Layout>) => void;
  refreshProject: () => Promise<void>;
  refreshSources: () => Promise<void>;
  openProject: (path: string, files?: string[]) => Promise<void>;
  select: (id: string | null) => void;
  loadTranscript: (id: string, force?: boolean) => Promise<void>;
  loadPeaks: (id: string, force?: boolean) => Promise<void>;
  say: (kind: "ok" | "err" | "info", text: string) => void;
  loadSync: () => Promise<void>;
  loadStrategy: () => Promise<void>;
  loadEdl: () => Promise<void>;
  putRanges: (ranges: EdlRange[], note: string, snap?: boolean) => Promise<void>;
  loadMarkers: () => Promise<void>;
  loadAudio: () => Promise<void>;
};

let layoutTimer: number | undefined;
const saveLayout = (l: Layout) => {
  // window layout belongs to the user config (backend), not localStorage; debounced while dragging
  clearTimeout(layoutTimer);
  layoutTimer = window.setTimeout(() => api.patch("/api/settings", { ui_layout: l }).catch(() => {}), 600);
};

export const useStore = create<State>((set, get) => ({
  ready: false,
  online: false,
  project: null,
  sources: [],
  jobs: [],
  selected: null,
  selSources: [],
  time: 0,
  playing: false,
  view: "edit",
  appMode: "full",
  leftTab: "sources",
  rightTab: "strategy",
  queueOpen: false,
  paletteOpen: false,
  wave: 1,
  layout: DEFAULT_LAYOUT,
  transcripts: {},
  peaks: {},
  sync: null,
  strategy: null,
  edl: null,
  monitor: "source",
  compare: false,
  selRange: null,
  selEnd: null,
  markers: [],
  selMarker: null,
  selSlot: null,
  audio: null,
  prompt: "",
  chips: { format: "16:9", subs: false, gfx: true, grade: false },
  toast: null,
  set: (p) => set(p),
  setLayout: (p) => {
    const l = { ...get().layout, ...p };
    set({ layout: l });
    saveLayout(l);
  },
  refreshProject: async () => {
    const p = await api.get<ProjectInfo>("/api/project");
    set({ project: p, sources: p.sources ?? [] });
    if (p.dir) window.cutroom?.setProjectDir(p.dir);
    get().loadSync(); get().loadStrategy(); get().loadEdl(); get().loadMarkers(); get().loadAudio();
    if (p.open) api.get<{ mode: AppMode }>("/api/quick").then((q) => set({ appMode: q.mode })).catch(() => {});
    const sel = get().selected;
    if (p.sources?.length && (!sel || !p.sources.find((s) => s.id === sel))) {
      get().select(p.sources.find((s) => !s.duplicate_of)?.id ?? p.sources[0].id);
    }
  },
  refreshSources: async () => {
    if (!get().project?.open) return;
    const prev = get().sources;
    const sources = await api.get<Source[]>("/api/sources");
    set({ sources });
    get().loadSync(); get().loadStrategy(); get().loadEdl(); get().loadMarkers(); get().loadAudio();
    // reload derived data that just appeared
    const sel = get().selected;
    for (const s of sources) {
      const old = prev.find((o) => o.id === s.id);
      if (s.id === sel) {
        if (s.has.transcript && (!old?.has.transcript || get().transcripts[s.id] === "missing")) get().loadTranscript(s.id, true);
        if (s.has.peaks && (!old?.has.peaks || get().peaks[s.id] === "missing")) get().loadPeaks(s.id, true);
      }
    }
  },
  openProject: async (path, files) => {
    try {
      const p = await api.post<ProjectInfo>("/api/project/open", { path, files });
      if (p.dir) window.cutroom?.setProjectDir(p.dir);
      get().loadSync(); get().loadStrategy(); get().loadEdl(); get().loadMarkers(); get().loadAudio();
      set({ project: { ...p, open: true }, sources: p.sources ?? [], transcripts: {}, peaks: {}, selected: null });
      const first = p.sources?.find((s) => !s.duplicate_of);
      if (first) get().select(first.id);
    } catch (e) {
      get().say("err", (e as Error).message);
    }
  },
  select: (id) => {
    programPause();
    set({ selected: id, time: 0, playing: false, monitor: "source" });
    if (id) {
      get().loadTranscript(id);
      get().loadPeaks(id);
    }
  },
  loadTranscript: async (id, force) => {
    const cur = get().transcripts[id];
    if (cur && !force && cur !== "missing") return;
    set({ transcripts: { ...get().transcripts, [id]: "loading" } });
    try {
      const t = await api.get<Transcript>(`/api/sources/${encodeURIComponent(id)}/transcript`);
      set({ transcripts: { ...get().transcripts, [id]: t } });
    } catch {
      set({ transcripts: { ...get().transcripts, [id]: "missing" } });
    }
  },
  loadPeaks: async (id, force) => {
    const cur = get().peaks[id];
    if (cur && !force && cur !== "missing") return;
    set({ peaks: { ...get().peaks, [id]: "loading" } });
    try {
      const buf = await api.get<ArrayBuffer>(`/api/sources/${encodeURIComponent(id)}/peaks`);
      set({ peaks: { ...get().peaks, [id]: new Uint8Array(buf) } });
    } catch {
      set({ peaks: { ...get().peaks, [id]: "missing" } });
    }
  },
  loadStrategy: async () => {
    if (!get().project?.open) return;
    try { set({ strategy: await api.get<Strategy>("/api/strategy") }); } catch { /* not open */ }
  },
  loadEdl: async () => {
    if (!get().project?.open) return;
    try {
      const e = await api.get<EdlState>("/api/edl");
      const first = !get().edl?.edl && e.edl;
      set({ edl: e, ...(first ? { monitor: "program" as const, time: 0 } : {}) });
    } catch { /* not open */ }
  },
  putRanges: async (ranges, note, snap = true) => {
    const cur = get().edl;
    if (!cur?.edl) return;
    // optimistic: the program monitor and timeline update instantly, the backend snaps + versions
    let off = 0;
    const local = ranges.map((r) => { const o = { ...r, out_start: off }; off += r.end - r.start; return o; });
    set({ edl: { ...cur, edl: { ...cur.edl, ranges: local, total_duration_s: off } } });
    try {
      set({ edl: await api.put<EdlState>("/api/edl", { ranges, slots: cur.edl.slots, note, snap }) });
    } catch (e) {
      set({ edl: cur });
      get().say("err", (e as Error).message);
    }
  },
  loadMarkers: async () => {
    if (!get().project?.open) return;
    try { set({ markers: await api.get<Marker[]>("/api/markers") }); } catch { /* not open */ }
  },
  loadAudio: async () => {
    if (!get().project?.open) return;
    try { set({ audio: await api.get<AudioInfo>("/api/audio") }); } catch { /* not open */ }
  },
  loadSync: async () => {
    if (!get().project?.open) return;
    try { set({ sync: await api.get<SyncData>("/api/sync") }); } catch { set({ sync: null }); }
  },
  say: (kind, text) => {
    set({ toast: { kind, text } });
    window.setTimeout(() => { if (get().toast?.text === text) set({ toast: null }); }, 5000);
  },
}));

// ---------------------------------------------------------------- player bridge
// One <video> element; components call these instead of passing refs around.

type PlayerCtl = { el: HTMLVideoElement | null; rate: number };
export const player: PlayerCtl = { el: null, rate: 0 };

export function seek(t: number) {
  if (useStore.getState().monitor === "program") return programSeek(t);
  const el = player.el;
  const s = useStore.getState();
  const dur = currentDuration();
  const clamped = Math.max(0, Math.min(dur || t, t));
  if (el) el.currentTime = clamped;
  s.set({ time: clamped });
}

export function togglePlay() {
  if (useStore.getState().monitor === "program") return programIsPlaying() ? programPause() : programPlay();
  const el = player.el;
  if (!el) return;
  if (el.paused) { el.playbackRate = 1; player.rate = 1; el.play().catch(() => {}); }
  else { el.pause(); player.rate = 0; }
}

// J/K/L shuttle: L speeds up forward (1→2→4), J steps back (no native reverse; we nudge), K stops.
export function shuttle(dir: -1 | 0 | 1) {
  if (useStore.getState().monitor === "program") {
    if (dir === 0) return programPause();
    if (dir === 1) return programPlay();
    programPause();
    return programSeek(useStore.getState().time - 1);
  }
  const el = player.el;
  if (!el) return;
  if (dir === 0) { el.pause(); player.rate = 0; return; }
  if (dir === 1) {
    player.rate = player.rate <= 0 ? 1 : Math.min(4, player.rate * 2);
    el.playbackRate = player.rate;
    el.play().catch(() => {});
    return;
  }
  el.pause();
  player.rate = 0;
  seek(el.currentTime - 1);
}

export function stepFrames(n: number) {
  const fps = currentSource()?.probe?.video?.fps || 25;
  if (useStore.getState().monitor === "program") { programPause(); return programSeek(useStore.getState().time + n / fps); }
  const el = player.el;
  if (!el) return;
  el.pause();
  seek(el.currentTime + n / fps);
}

export function currentSource(): Source | undefined {
  const s = useStore.getState();
  return s.sources.find((x) => x.id === s.selected);
}

export function currentDuration(): number {
  if (useStore.getState().monitor === "program") return programDuration();
  return currentSource()?.probe?.duration ?? 0;
}

// ---------------------------------------------------------------- angle sync helpers
// t_ref = t_member + eff(member), eff = offset + nudge_frames / fps(member)

export function groupOf(id: string | null): SyncGroup | undefined {
  if (!id) return undefined;
  return useStore.getState().sync?.groups.find((g) => g.members.some((m) => m.id === id));
}

export function effOffset(g: SyncGroup, id: string): number {
  const m = g.members.find((x) => x.id === id);
  if (!m) return 0;
  const fps = useStore.getState().sources.find((s) => s.id === id)?.probe?.video?.fps || 25;
  return m.offset + m.nudge_frames / fps;
}

/** Time in `other`'s own timeline that plays together with time t of `from`. */
export function mapTime(g: SyncGroup, from: string, other: string, t: number): number {
  return t + effOffset(g, from) - effOffset(g, other);
}

// ---------------------------------------------------------------- selection, markers, cameras

/** Selected span of EDL ranges [a, b] (inclusive), or null. */
export function selection(): [number, number] | null {
  const s = useStore.getState();
  if (s.selRange == null) return null;
  const b = s.selEnd ?? s.selRange;
  return [Math.min(s.selRange, b), Math.max(s.selRange, b)];
}

/** Where a marker (anchored to source material) sits on the program clock, or null if cut out. */
export function markerOut(m: { source: string; time: number }): number | null {
  const e = useStore.getState().edl?.edl;
  if (!e) return null;
  for (const r of e.ranges) {
    if (r.source === m.source && m.time >= r.start && m.time <= r.end) return r.out_start + (m.time - r.start);
  }
  return null;
}

/** What is under the playhead, as material: {source, time}. */
export function anchorAtPlayhead(): { source: string; time: number } | null {
  const s = useStore.getState();
  if (s.monitor === "program" && s.edl?.edl) {
    const r = s.edl.edl.ranges.find((x) => s.time >= x.out_start && s.time < x.out_start + (x.end - x.start));
    return r ? { source: r.source, time: r.start + (s.time - r.out_start) } : null;
  }
  return s.selected ? { source: s.selected, time: s.time } : null;
}

/** Stable colour per camera inside its take (1 = reference). */
export function cameraIndex(id: string): number {
  const g = groupOf(id);
  if (!g) return 0;
  const vids = g.members.filter((m) => useStore.getState().sources.find((s) => s.id === m.id)?.kind !== "audio");
  const order = [g.ref, ...vids.map((m) => m.id).filter((x) => x !== g.ref)];
  return Math.max(0, order.indexOf(id)) % 4;
}
