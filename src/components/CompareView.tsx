import { useEffect, useRef, useState } from "react";
import { frameUrl } from "../api";
import { groupOf, mapTime, useStore } from "../store";
import { targetSpan } from "../timelineOps";

// Before/after wipe on a real frame of the source (exact ffmpeg grade, not the CSS approximation).
export function CompareView() {
  const edl = useStore((s) => s.edl?.edl);
  const time = useStore((s) => s.time);
  const playing = useStore((s) => s.playing);
  useStore((s) => s.selRange);
  const [split, setSplit] = useState(50);
  const [shot, setShot] = useState<{ before: string; after: string; label: string } | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!edl || playing) return;
    const t = window.setTimeout(() => {
      const span = targetSpan();
      if (!span) return;
      const r = edl.ranges[span[0]];
      const inside = time >= r.out_start && time < r.out_start + (r.end - r.start);
      let src = r.source, ts = inside ? r.start + (time - r.out_start) : r.start + Math.min(1, (r.end - r.start) / 2);
      const g = r.angle ? groupOf(r.source) : undefined;
      if (r.angle && g) { ts = mapTime(g, r.source, r.angle, ts); src = r.angle; }
      const grade = r.color ?? edl.color ?? null;
      const v = `${edl.cutroom.version_id}-${ts.toFixed(2)}`;
      setShot({ before: frameUrl(src, ts, null, v), after: frameUrl(src, ts, grade, v), label: `эпизод ${span[0] + 1} · ${src}${grade ? "" : " · цвет не задан"}` });
    }, 250);
    return () => clearTimeout(t);
  }, [edl?.cutroom.version_id, time, playing, edl]);

  const drag = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    setSplit(Math.max(0, Math.min(100, ((clientX - r.left) / r.width) * 100)));
  };
  if (!shot) return <div className="absolute inset-0 grid place-items-center text-dim text-[12.5px] bg-bg">Остановите воспроизведение — покажу кадр до и после цветокора.</div>;
  return (
    <div className="absolute inset-0 grid place-items-center bg-bg">
      <div ref={box} className="relative max-w-full max-h-full aspect-video w-full select-none cursor-ew-resize"
        onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); drag(e.clientX); }}
        onPointerMove={(e) => { if (e.buttons & 1) drag(e.clientX); }}>
        <img src={shot.before} alt="До цветокора" className="absolute inset-0 w-full h-full object-contain" draggable={false} />
        <img src={shot.after} alt="После цветокора" className="absolute inset-0 w-full h-full object-contain" draggable={false}
          style={{ clipPath: `inset(0 0 0 ${split}%)` }} />
        <div className="absolute top-0 bottom-0 w-0.5 bg-accent" style={{ left: `${split}%` }} />
        <span className="absolute left-2 top-2 badge">до</span>
        <span className="absolute right-2 top-2 badge badge-accent">после</span>
        <span className="absolute left-2 bottom-2 badge">{shot.label}</span>
      </div>
    </div>
  );
}
