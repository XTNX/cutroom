import { useEffect, useState } from "react";
import { api, type EdlState, type Grade } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";
import { targetSpan } from "../timelineOps";
import { cls } from "../util";

const PRESETS: Record<string, string> = { none: "без цвета", subtle: "лёгкая чистка", neutral_punch: "нейтрально, плотнее", warm_cinematic: "тёплое кино" };
const ID: Required<Grade> = { preset: "none", cdl: { slope: [1, 1, 1], offset: [0, 0, 0], power: [1, 1, 1], sat: 1 }, temperature: 0, raw: "" };
const norm = (g?: Grade | null): Required<Grade> => ({ ...ID, ...(g ?? {}), cdl: { ...ID.cdl, ...(g?.cdl ?? {}) } });

/** Approximate CSS look of a grade for the live preview (the render uses the exact ffmpeg chain). */
export function cssFilter(g?: Grade | null): string {
  if (!g) return "";
  const n = norm(g);
  const slope = (n.cdl.slope[0] + n.cdl.slope[1] + n.cdl.slope[2]) / 3;
  const off = (n.cdl.offset[0] + n.cdl.offset[1] + n.cdl.offset[2]) / 3;
  const contrast = { neutral_punch: 1.06, warm_cinematic: 1.12, subtle: 1.03 }[n.preset ?? ""] ?? 1;
  const sat = n.cdl.sat * ({ warm_cinematic: 0.88, subtle: 0.98 }[n.preset ?? ""] ?? 1);
  const warm = Math.max(0, n.temperature) / 400 + (n.preset === "warm_cinematic" ? 0.08 : 0);
  const cool = Math.max(0, -n.temperature) / 100;
  return `brightness(${(slope + off * 2).toFixed(3)}) contrast(${contrast}) saturate(${sat.toFixed(3)}) sepia(${warm.toFixed(3)})${cool ? ` hue-rotate(${(cool * -12).toFixed(1)}deg)` : ""}`;
}

export function ColorPanel() {
  const edl = useStore((s) => s.edl?.edl);
  const gate = useStore((s) => s.strategy?.gate_open);
  useStore((s) => s.selRange);
  useStore((s) => s.selEnd);
  useStore((s) => s.time);
  const [whole, setWhole] = useState(false);
  const span = targetSpan();
  const scopeAll = whole || !span;
  const current = edl ? (scopeAll ? edl.color : (edl.ranges[span![0]]?.color ?? null)) : null;
  const [g, setG] = useState<Required<Grade>>(norm(current));
  const key = `${edl?.cutroom.version_id}:${scopeAll ? "all" : span?.join("-")}`;
  useEffect(() => setG(norm(current)), [key]);

  if (!edl) return <p className="p-3 text-dim text-[12.5px]">Цвет настраивается на монтаже: соберите черновик (стратегия или перетаскивание файлов на таймлайн).</p>;

  const save = async (next: Required<Grade> | null) => {
    if (!gate) return useStore.getState().say("info", "Менять монтаж можно после утверждения стратегии.");
    const body = scopeAll ? { grade: next } : { grade: next, start: span![0], end: span![1] };
    try { useStore.setState({ edl: await api.put<EdlState>("/api/color", body) }); }
    catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const set = (patch: Partial<Required<Grade>>, commit = false) => { const n = { ...g, ...patch }; setG(n); if (commit) save(n); };
  const setCdl = (patch: Partial<Required<Grade>["cdl"]>, commit = false) => set({ cdl: { ...g.cdl, ...patch } }, commit);
  const allCh = (k: "slope" | "offset" | "power", v: number) => [v, v, v] as [number, number, number];
  const avg = (a: number[]) => (a[0] + a[1] + a[2]) / 3;
  const scopeLabel = scopeAll ? "весь ролик" : span![0] === span![1] ? `эпизод ${span![0] + 1}` : `эпизоды ${span![0] + 1}–${span![1] + 1}`;
  const episodeHasOwn = !scopeAll && edl.ranges.slice(span![0], span![1] + 1).some((r) => r.color);

  return (
    <div className="flex flex-col text-[12.5px]">
      <section className="p-3 border-b border-line flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="label">Цвет</span>
          <span className="badge badge-accent">{scopeLabel}</span>
          <span className="flex-1" />
          {span && <button className="btn btn-ghost btn-sm" onClick={() => setWhole(!whole)}>{whole ? "К выбранному эпизоду" : "Ко всему ролику"}</button>}
        </div>
        {!scopeAll && !episodeHasOwn && <p className="text-[11.5px] text-faint">У эпизода нет своего цвета — сейчас действует цвет всего ролика. Изменения здесь зададут цвет только этому эпизоду.</p>}
        <div className="flex flex-wrap gap-1">
          {Object.entries(PRESETS).map(([k, l]) => (
            <button key={k} className="chip" aria-pressed={g.preset === k} onClick={() => set({ preset: k }, true)}>{l}</button>
          ))}
        </div>
      </section>
      <section className="p-3 border-b border-line flex flex-col gap-2">
        <Slider label="Экспозиция" min={0.7} max={1.3} step={0.01} value={avg(g.cdl.slope)} fmt={(v) => `${v >= 1 ? "+" : ""}${Math.round((v - 1) * 100)}%`}
          onChange={(v, c) => setCdl({ slope: allCh("slope", v) }, c)} />
        <Slider label="Тени" min={-0.08} max={0.08} step={0.005} value={avg(g.cdl.offset)} fmt={(v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}`}
          onChange={(v, c) => setCdl({ offset: allCh("offset", v) }, c)} />
        <Slider label="Средние тона" min={0.7} max={1.3} step={0.01} value={2 - avg(g.cdl.power)} fmt={(v) => `${v >= 1 ? "+" : ""}${Math.round((v - 1) * 100)}%`}
          onChange={(v, c) => setCdl({ power: allCh("power", 2 - v) }, c)} />
        <Slider label="Насыщенность" min={0} max={1.6} step={0.01} value={g.cdl.sat} fmt={(v) => `${Math.round(v * 100)}%`}
          onChange={(v, c) => setCdl({ sat: v }, c)} />
        <Slider label="Температура" min={-100} max={100} step={1} value={g.temperature} fmt={(v) => (v > 0 ? `теплее ${v}` : v < 0 ? `холоднее ${-v}` : "0")}
          onChange={(v, c) => set({ temperature: v }, c)} />
        <div className="flex gap-1.5 mt-1">
          <button className="btn btn-sm" onClick={() => useStore.getState().set({ compare: true, monitor: "program" })}><Icon name="split" size={13} /> До / после</button>
          <button className="btn btn-ghost btn-sm" onClick={() => { setG(norm(null)); save(null); }}>Сбросить</button>
        </div>
        <p className="text-[11.5px] text-faint">В живом превью цвет показан приблизительно. Точный кадр — в режиме «До / после», в рендере цвет применяется к каждому фрагменту при извлечении.</p>
      </section>
      <details className="p-3 border-b border-line">
        <summary className="cursor-pointer text-dim">Точная настройка: ASC CDL по каналам</summary>
        <div className="grid grid-cols-[70px_repeat(3,1fr)] gap-1.5 mt-2 items-center text-[12px]">
          <span />{["R", "G", "B"].map((c) => <span key={c} className={cls("text-center font-medium", c === "R" ? "text-err" : c === "G" ? "text-ok" : "text-info")}>{c}</span>)}
          {(["slope", "offset", "power"] as const).map((k) => (
            <Row key={k} label={{ slope: "Slope", offset: "Offset", power: "Power" }[k]} values={g.cdl[k]} step={k === "offset" ? 0.005 : 0.01}
              onChange={(i, v) => { const arr = [...g.cdl[k]] as [number, number, number]; arr[i] = v; setCdl({ [k]: arr } as never, true); }} />
          ))}
        </div>
        <label className="flex flex-col gap-1 mt-3">
          <span className="text-faint">Свой фильтр ffmpeg (добавится в конец)</span>
          <textarea className="input h-auto py-1.5 mono text-[11.5px]" rows={2} value={g.raw} placeholder="например: curves=preset=lighter"
            onChange={(e) => set({ raw: e.target.value })} onBlur={() => save(g)} />
        </label>
      </details>
    </div>
  );
}

function Slider({ label, value, min, max, step, fmt, onChange }: { label: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string; onChange: (v: number, commit: boolean) => void }) {
  return (
    <label className="grid grid-cols-[96px_minmax(0,1fr)_72px] items-center gap-2">
      <span className="text-faint truncate">{label}</span>
      <input type="range" className="min-w-0 w-full" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value), false)}
        onPointerUp={(e) => onChange(Number((e.target as HTMLInputElement).value), true)} onKeyUp={(e) => onChange(Number((e.target as HTMLInputElement).value), true)} />
      <span className="tnum text-right">{fmt(value)}</span>
    </label>
  );
}

function Row({ label, values, step, onChange }: { label: string; values: number[]; step: number; onChange: (i: number, v: number) => void }) {
  return (
    <>
      <span className="text-faint">{label}</span>
      {values.map((v, i) => (
        <input key={i} type="number" step={step} className="input h-7 tnum text-center" value={Number(v.toFixed(3))} onChange={(e) => onChange(i, Number(e.target.value))} />
      ))}
    </>
  );
}
