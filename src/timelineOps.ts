// Manual timeline operations: drop files onto the program timeline, and "which episode does an
// edit apply to" — the selected episode(s); the playhead only matters where a point is needed.
import { api, type EdlRange, type EdlState, type ProjectInfo } from "./api";
import { rangeAtPlayhead } from "./program";
import { selection, useStore } from "./store";

/** Episodes an edit applies to: the selection, else the episode under the playhead. */
export function targetSpan(): [number, number] | null {
  const sel = selection();
  if (sel) return sel;
  const i = rangeAtPlayhead();
  return i >= 0 ? [i, i] : null;
}

/** Index to insert before, for a drop at program time t (between episodes, nearest boundary). */
function insertIndex(ranges: EdlRange[], t: number): number {
  for (let i = 0; i < ranges.length; i++) {
    const r = ranges[i];
    if (t < r.out_start + (r.end - r.start) / 2) return i;
  }
  return ranges.length;
}

/** Open the gate for a manual cut when there is no approved strategy (the person decides here). */
async function ensureManualGate(): Promise<boolean> {
  const st = useStore.getState();
  if (st.strategy?.gate_open) return true;
  if (st.strategy && st.strategy.status !== "none" && !st.strategy.manual) {
    st.say("info", "Сначала утвердите стратегию во вкладке «Стратегия» — или удалите её, чтобы собирать вручную.");
    return false;
  }
  await api.post("/api/strategy/manual");
  await st.loadStrategy();
  st.say("info", "Начат ручной монтаж: фрагменты раскладываете вы. Агенты подключатся, когда попросите.");
  return true;
}

/** Put whole clips onto the program timeline at time t (several at once, in the given order). */
export async function insertSources(ids: string[], t: number) {
  const st = useStore.getState();
  const clips = ids.map((id) => st.sources.find((s) => s.id === id)).filter((s) => s && s.kind === "video" && !s.missing && (s.probe?.duration ?? 0) > 0.3);
  if (!clips.length) return st.say("info", "Перетащите видеофайлы: звук без картинки подключается во вкладке «Звук».");
  if (!(await ensureManualGate())) return;
  const e = useStore.getState().edl?.edl;
  const ranges: EdlRange[] = e ? e.ranges.map((r) => ({ ...r })) : [];
  const at = insertIndex(ranges, t);
  const add = clips.map((s) => ({ source: s!.id, start: 0, end: s!.probe!.duration, out_start: 0, beat: "вручную", reason: "перетащено на таймлайн" }));
  ranges.splice(at, 0, ...add);
  const note = `вставлено ${add.length === 1 ? add[0].source : `${add.length} файла`} на позицию ${at + 1}`;
  try {
    const res = await api.put<EdlState>("/api/edl", { ranges, slots: e?.slots ?? [], note, snap: false });
    useStore.setState({ edl: res, monitor: "program", selRange: at, selEnd: at + add.length - 1 });
    // clips without proxies or text yet get prepared in the background
    const raw = clips.filter((s) => !s!.has.proxy || !s!.has.transcript).map((s) => s!.id);
    if (raw.length) api.post("/api/project/ingest", { sources: raw }).catch(() => {});
  } catch (err) { st.say("err", (err as Error).message); }
}

/** Files dropped from Explorer: add them to the project, then put them on the timeline. */
export async function insertPaths(paths: string[], t: number) {
  const st = useStore.getState();
  if (!st.project?.dir) return;
  const before = new Set(st.sources.map((s) => s.path.toLowerCase()));
  const res = await api.post<ProjectInfo>("/api/project/add", { path: st.project.dir, files: paths });
  useStore.setState({ sources: res.sources ?? st.sources });
  const want = paths.map((p) => p.toLowerCase());
  const ids = (res.sources ?? []).filter((s) => want.includes(s.path.toLowerCase()) || (!before.has(s.path.toLowerCase()) && want.some((w) => s.path.toLowerCase().startsWith(w)))).map((s) => s.id);
  await insertSources(ids, t);
}
