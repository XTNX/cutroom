// Backend client. In Electron the preload bridge hands over {url, token};
// in a plain browser (dev self-check) they come from ?backend=&token= or the dev defaults.

export type Probe = {
  duration: number;
  size: number;
  container?: string;
  creation_time?: string | null;
  error?: string | null;
  video: null | {
    codec: string; width: number; height: number; display_width: number; display_height: number;
    rotation: number; fps: number | null; fps_raw: string; pix_fmt: string; color_transfer?: string; hdr: boolean;
  };
  audio: { index: number; codec: string; channels: number; sample_rate: number }[];
};

export type Source = {
  id: string; path: string; name: string; hash: string; probe: Probe | null; kind: "video" | "audio";
  duplicate_of: string | null; excluded: boolean; audio_track: number;
  has: { proxy: boolean; audio: boolean; peaks: boolean; thumb: boolean; transcript: boolean };
  transcript_meta: { engine: string; model: string; rtf?: number } | null;
  missing?: boolean;
};

export type Job = {
  id: number; kind: string; label: string; lane: string; source: string | null; deps: number[];
  status: "queued" | "running" | "done" | "cached" | "error" | "cancelled" | "blocked";
  progress: number; eta: number | null; heavy: boolean; created: number; started: number | null;
  finished: number | null; elapsed: number | null; error: string | null; note: string | null; log: string[];
};

export type ProjectInfo = {
  open: boolean; dir?: string; edit?: string; name?: string; recent?: string[];
  memory?: { exists: boolean; sessions?: number; last_title?: string; summary?: string | null };
  sources?: Source[]; has_packed?: boolean;
};

export type SyncMember = { id: string; offset: number; confidence: number; sharpness: number | null; status: "ref" | "ok" | "check" | "manual"; nudge_frames: number };
export type SyncGroup = { id: string; ref: string; members: SyncMember[] };
export type SyncData = { groups: SyncGroup[]; singles: string[]; stats?: { pairs: number; candidates: number; proven: number } };

export type StrategyData = {
  sentences: string[]; beats: { name: string; intent: string }[]; takes: string;
  slips: { source: string; time: number; text: string }[];
  graphics: { moment: string; type: string; content: string }[];
  target_seconds: number; aspect: string;
  questions: { question: string; options: string[] }[]; needs: string[];
};
export type Strategy = {
  status: "none" | "draft" | "approved"; text?: string; data?: StrategyData; prompt?: string; revision?: number;
  approved_at?: string | null; gate_open?: boolean; dirty?: boolean; manual?: boolean;
  qa?: { q: string; answer: string; suggested_edit: string; when: string }[];
};
export type Grade = { preset?: string; cdl?: { slope: [number, number, number]; offset: [number, number, number]; power: [number, number, number]; sat: number }; temperature?: number; raw?: string };
export type EdlRange = { source: string; start: number; end: number; out_start: number; beat?: string; quote?: string; reason?: string; angle?: string; locked?: boolean; color?: Grade | null };
export type GfxItem = { label?: string; value?: number | null; text?: string };
export type GfxSpec = { type: string; title: string; subtitle?: string; items: GfxItem[]; unit?: string; highlight?: number | null; note?: string; layout?: "side" | "full" };
export type Brand = { bg: string; panel: string; fg: string; muted: string; accent: string; accent2: string; font: string; font_bold: string; confirmed?: boolean };
export type EdlSlot = { id: string; type: string; source?: string; payoff_time?: number; start_in_output: number; duration: number; spec?: GfxSpec | string;
  spec_hint?: string; status: string; error?: string | null; files?: { render: string; preview: string; poster: string; v: number } };
export type Edl = { version: number; sources: Record<string, string>; ranges: EdlRange[]; slots: EdlSlot[]; total_duration_s: number; color?: Grade | null; brand?: Partial<Brand>;
  cutroom: { version_id: number; pad_s: number; agent_check?: string; strategy_hash?: string } };
export type EdlState = {
  edl: Edl | null; gate_open: boolean;
  history: { head: number | null; can_undo: boolean; can_redo: boolean; versions: { id: number; created: number; author: string; note: string }[] };
  preview: { version: number | null; exists: boolean };
};

export type Marker = { id: string; source: string; time: number; note: string; color: string; done: boolean; created: string };
export type AudioMember = { id: string; audio_only: boolean; offset: number; status: string; confidence: number; name: string };
export type AudioGroup = { id: string; ref: string; members: AudioMember[]; chosen: string | null; auto: string | null; effective: string | null };
export type AudioFx = { highpass: boolean; denoise: string; deess: boolean; compress: string };
export type MusicBed = { track: string; path: string; title: string; start_in_output: number; offset: number; gain_db: number; duck_db: number; fade_in: number; fade_out: number; loop?: boolean };
export type AudioInfo = { groups: AudioGroup[]; fx: AudioFx; music: MusicBed | null; presets: { denoise: string[]; compress: string[] } };
export type Track = { id: string; file: string; path: string; library: string | null; library_name: string | null; licence: string | null;
  analysis?: { duration: number; bpm: number; energy: number; brightness: number; busyness: number; error?: string }; score?: number; why?: string[] };

// [text, start, end, probability, isEvent]
export type Word = [string, number, number, number, 0 | 1];
export type Transcript = { language: string; meta: Record<string, unknown> | null; words: Word[] };

declare global {
  interface Window {
    cutroom?: {
      backend: () => Promise<{ url: string; token: string }>;
      pickFolder: () => Promise<string | null>;
      pickFiles: () => Promise<string[]>;
      pathForFile: (f: File) => string;
      revealInFolder: (p: string) => void;
      setProjectDir: (dir: string) => void;
      openExternal: (url: string) => void;
      setupStatus?: () => Promise<import("./components/SetupView").SetupState>;
      setupRetry: () => void;
      onSetup: (cb: (st: import("./components/SetupView").SetupState) => void) => () => void;
      setBusy: (busy: boolean) => void;
      notify: (title: string, body: string) => void;
      onMenu: (cb: (cmd: string) => void) => () => void;
      isElectron: true;
    };
  }
}

let base = "";
let token = "";

export async function initBackend(): Promise<void> {
  if (window.cutroom) {
    const b = await window.cutroom.backend();
    base = b.url;
    token = b.token;
    return;
  }
  const q = new URLSearchParams(location.search);
  base = q.get("backend") || "http://127.0.0.1:8765";
  token = q.get("token") || "dev";
}

export const previewUrl = (bust: string | number) => `${base}/media/preview?t=${token}&v=${bust}`;
export const frameUrl = (source: string, time: number, grade: unknown, bust: string) =>
  `${base}/media/frame?t=${token}&source=${encodeURIComponent(source)}&time=${time.toFixed(3)}&graded=${grade ? 1 : 0}&g=${encodeURIComponent(grade ? JSON.stringify(grade) : "")}&v=${bust}`;
export const gfxUrl = (id: string, kind: "poster" | "preview", v: string | number) => `${base}/media/gfx/${encodeURIComponent(id)}/${kind}?t=${token}&v=${v}`;
export const musicUrl = (id: string) => `${base}/media/music/${encodeURIComponent(id)}?t=${token}`;
export const sampleUrl = (name: string, bust: string | number) => `${base}/media/sample/${name}?t=${token}&v=${bust}`;

/** Any backend media path with the token appended (quick mode: previews, cards, slides, auditions). */
export const backendUrl = (path: string) => `${base}${path}${path.includes("?") ? "&" : "?"}t=${token}`;

export const mediaUrl = (kind: "proxy" | "thumb", id: string, bust = "") =>
  `${base}/media/${kind}/${encodeURIComponent(id)}?t=${token}${bust ? `&v=${bust}` : ""}`;

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(base + path, {
    method,
    headers: { "X-Cutroom-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not json */ }
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("json")) return r.json() as Promise<T>;
  if (ct.includes("octet-stream")) return r.arrayBuffer() as unknown as Promise<T>;
  return r.text() as unknown as Promise<T>;
}

export const api = {
  get: <T>(p: string) => req<T>("GET", p),
  post: <T>(p: string, b: unknown = {}) => req<T>("POST", p, b),
  patch: <T>(p: string, b: unknown) => req<T>("PATCH", p, b),
  put: <T>(p: string, b: unknown) => req<T>("PUT", p, b),
  del: <T>(p: string) => req<T>("DELETE", p),
};

export function connectWs(onMsg: (m: any) => void, onState: (up: boolean) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry: number | undefined;
  const open = () => {
    ws = new WebSocket(base.replace(/^http/, "ws") + `/ws?t=${token}`);
    ws.onopen = () => onState(true);
    ws.onmessage = (e) => { try { onMsg(JSON.parse(e.data)); } catch { /* ignore */ } };
    ws.onclose = () => {
      onState(false);
      if (!closed) retry = window.setTimeout(open, 1000);
    };
  };
  open();
  return () => { closed = true; clearTimeout(retry); ws?.close(); };
}
