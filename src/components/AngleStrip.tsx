import { useEffect, useRef } from "react";
import { mediaUrl } from "../api";
import { cameraIndex, groupOf, mapTime, useStore } from "../store";
import { cls } from "../util";
import { targetSpan } from "../timelineOps";

/** Camera change applies to the selected episode(s) — not to whatever is under the playhead.
 *  fromHere (Shift): split the episode at the playhead (nearest word gap) and switch from there. */
export function switchAngle(member: string, fromHere = false) {
  const st = useStore.getState();
  const e = st.edl?.edl;
  if (!e) return;
  if (!st.strategy?.gate_open) return st.say("info", "Менять монтаж можно после утверждения стратегии.");
  const span = targetSpan();
  if (!span) return;
  const [a, b] = span;
  const ranges = e.ranges.map((x) => ({ ...x }));
  if (fromHere && a === b) {
    const r = ranges[a];
    const inside = st.time > r.out_start + 0.4 && st.time < r.out_start + (r.end - r.start) - 0.4;
    if (!inside) return st.say("info", "«С этого места» работает, когда бегунок внутри выбранного эпизода.");
    const g = groupOf(r.source);
    if (!g?.members.some((m) => m.id === member)) return;
    const ts = r.start + (st.time - r.out_start);
    ranges.splice(a, 1, { ...r, end: ts }, { ...r, start: ts, angle: member === r.source ? undefined : member, reason: `камера ${member} с ${ts.toFixed(2)} с` });
    st.putRanges(ranges, `камера ${member} с середины эпизода ${a + 1}`);
    st.set({ selRange: a + 1, selEnd: null });
    return;
  }
  let changed = 0;
  for (let i = a; i <= b; i++) {
    const r = ranges[i];
    const g = groupOf(r.source);
    if (!g?.members.some((m) => m.id === member)) continue;   // that camera didn't film this episode
    ranges[i] = { ...r, angle: member === r.source ? undefined : member };
    changed++;
  }
  if (!changed) return st.say("info", `Камера ${member} не снимала выбранный эпизод.`);
  st.putRanges(ranges, a === b ? `камера ${member} для эпизода ${a + 1}` : `камера ${member} для эпизодов ${a + 1}–${b + 1}`, false);
}

/** The episode the strip shows: first of the selection, else the one under the playhead. */
function stripEpisode() {
  const st = useStore.getState();
  const e = st.edl?.edl;
  const span = targetSpan();
  if (!e || !span) return null;
  const r = e.ranges[span[0]];
  const inside = st.time >= r.out_start && st.time < r.out_start + (r.end - r.start);
  // the moment to preview: under the playhead if it is inside, else a second into the episode
  const tSrc = inside ? r.start + (st.time - r.out_start) : r.start + Math.min(1, (r.end - r.start) / 2);
  return { r, tSrc, span };
}

export function cycleAngle() {
  const st = useStore.getState();
  const ep = stripEpisode();
  if (!ep) return;
  const g = groupOf(ep.r.source);
  if (!g) return;
  const cams = g.members.map((m) => m.id).filter((id) => {
    const src = st.sources.find((s) => s.id === id);
    const t = mapTime(g, ep.r.source, id, ep.tSrc);
    return src?.kind === "video" && t >= 0 && t <= (src.probe?.duration ?? 0);
  });
  if (cams.length < 2) return;
  const cur = cams.indexOf(ep.r.angle ?? ep.r.source);
  switchAngle(cams[(cur + 1) % cams.length]);
}

export function AngleStrip() {
  useStore((s) => s.edl?.edl);
  useStore((s) => s.time);
  useStore((s) => s.selRange);
  useStore((s) => s.selEnd);
  const playing = useStore((s) => s.playing);
  const sources = useStore((s) => s.sources);
  useStore((s) => s.sync);
  const ep = stripEpisode();
  const g = ep ? groupOf(ep.r.source) : undefined;
  if (!ep || !g) return null;
  const { r, tSrc, span } = ep;
  const activeCam = r.angle ?? r.source;
  const hasFrame = (id: string) => {
    const t = mapTime(g, r.source, id, tSrc);
    return t >= 0 && t <= (sources.find((s) => s.id === id)?.probe?.duration ?? 0);
  };
  const cams = g.members.filter((m) => sources.find((s) => s.id === m.id)?.kind === "video")
    .sort((a, b) => Number(b.id === activeCam) - Number(a.id === activeCam) || Number(hasFrame(b.id)) - Number(hasFrame(a.id)))
    .slice(0, 4);
  if (cams.length < 2) return null;
  const label = span[0] === span[1] ? `эпизод ${span[0] + 1}` : `эпизоды ${span[0] + 1}–${span[1] + 1}`;

  return (
    <div className="shrink-0 border-t border-line bg-panel px-2 py-1.5 flex items-center gap-2" role="group" aria-label="Камеры этого дубля">
      <div className="flex flex-col text-[11px] leading-tight text-faint w-[92px] shrink-0">
        <span className="label">Камеры</span>
        <span className="text-accent">{label}</span>
        <span>клик — весь эпизод</span>
        <span>Shift — с бегунка</span>
      </div>
      <div className="flex-1 min-w-0 flex gap-2 overflow-x-auto">
        {cams.map((m, k) => {
          const t = mapTime(g, r.source, m.id, tSrc);
          const dur = sources.find((s) => s.id === m.id)?.probe?.duration ?? 0;
          const has = t >= 0 && t <= dur;
          const on = activeCam === m.id;
          const col = `var(--cam${cameraIndex(m.id) + 1})`;
          return (
            <button key={m.id} disabled={!has} data-cam={m.id}
              onClick={(e) => switchAngle(m.id, e.shiftKey)}
              title={has ? `Камера ${cameraIndex(m.id) + 1}: ${m.id} — для ${label}. Клавиши Alt+${k + 1}` : "У этой камеры нет кадра в этот момент"}
              className={cls("relative shrink-0 w-[148px] aspect-video rounded-[6px] overflow-hidden bg-bg border-2 text-left disabled:opacity-35 disabled:cursor-not-allowed",
                on ? "shadow-[0_0_0_2px_var(--panel)]" : "opacity-80 hover:opacity-100")}
              style={{ borderColor: on ? col : "transparent" }}>
              {has && <Thumb id={m.id} t={t} playing={playing} />}
              <span className="absolute left-0 right-0 bottom-0 h-1" style={{ background: col }} />
              <span className="absolute left-1 top-1 badge" style={{ color: col }}>{cameraIndex(m.id) + 1} · {m.id}</span>
              {on && <span className="absolute right-1 top-1 badge badge-accent">в кадре</span>}
              <span className="absolute right-1 bottom-2 mono text-[10px] text-faint">Alt+{k + 1}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Thumb({ id, t, playing }: { id: string; t: number; playing: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el && Math.abs(el.currentTime - t) > (playing ? 0.3 : 0.04)) el.currentTime = Math.max(0, t);
  }, [t, playing]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (playing) el.play().catch(() => {}); else el.pause();
  }, [playing]);
  return <video ref={ref} src={mediaUrl("proxy", id)} muted preload="auto" className="w-full h-full object-cover pointer-events-none" />;
}
