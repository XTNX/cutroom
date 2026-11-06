import * as Tabs from "@radix-ui/react-tabs";
import { Fragment } from "react";
import { useStore } from "../store";
import { StrategyPanel } from "./StrategyPanel";
import { AudioPanel } from "./AudioPanel";
import { ColorPanel } from "./ColorPanel";
import { GfxPanel } from "./GfxPanel";
import { fmtBytes, fmtDur } from "../util";

const TABS = [
  ["strategy", "Стратегия"],
  ["gfx", "Инфографика"],
  ["color", "Цвет"],
  ["subs", "Субтитры"],
  ["sound", "Звук"],
  ["export", "Экспорт"],
] as const;

export function Inspector() {
  const tab = useStore((s) => s.rightTab);
  const set = useStore((s) => s.set);
  return (
    <Tabs.Root value={tab} onValueChange={(v) => set({ rightTab: v as never })} className="flex-1 min-h-0 flex flex-col">
      <Tabs.List className="flex px-1 border-b border-line overflow-x-auto shrink-0" aria-label="Инспектор">
        {TABS.map(([v, l]) => <Tabs.Trigger key={v} value={v} className="tab-trigger px-2">{l}</Tabs.Trigger>)}
      </Tabs.List>
      <div className="flex-1 min-h-0 overflow-y-auto">
        <Tabs.Content value="strategy" className="outline-none"><StrategyPanel /></Tabs.Content>
        <Tabs.Content value="gfx" className="outline-none"><GfxPanel /></Tabs.Content>
        <Tabs.Content value="color" className="outline-none"><ColorPanel /></Tabs.Content>
        <Tabs.Content value="subs" className="outline-none">
          <Stub title="Субтитры" stage={4}>
            Стили bold-overlay и natural-sentence. Накладываются последними. Шрифт с кириллицей из C:\Windows\Fonts —
            <span className="text-warn"> [NEED: шрифт бренда]</span>.
          </Stub>
        </Tabs.Content>
        <Tabs.Content value="sound" className="outline-none"><AudioPanel /></Tabs.Content>
        <Tabs.Content value="export" className="outline-none">
          <Stub title="Финальный рендер" stage={8}>Исходное разрешение и fps, CPU libx264/libx265 или ProRes, опция «SRT отдельным файлом».</Stub>
        </Tabs.Content>
      </div>
      <SourceInfo />
    </Tabs.Root>
  );
}

function Stub({ title, stage, children }: { title: string; stage: number; children: React.ReactNode }) {
  return (
    <div className="p-3 text-[12.5px]">
      <div className="flex items-center gap-2 mb-1.5"><span className="label">{title}</span><span className="badge">этап {stage}</span></div>
      <p className="text-dim">{children}</p>
    </div>
  );
}

function SourceInfo() {
  const s = useStore((st) => st.sources.find((x) => x.id === st.selected));
  if (!s?.probe) return null;
  const v = s.probe.video;
  const rows: [string, string][] = [
    ["Файл", s.name],
    ["Длительность", fmtDur(s.probe.duration)],
    ["Размер", fmtBytes(s.probe.size)],
    ...(v ? [
      ["Кадр", `${v.width}×${v.height}${v.rotation ? ` · поворот ${v.rotation}°` : ""}`] as [string, string],
      ["Частота", `${v.fps ? Math.round(v.fps * 1000) / 1000 : "?"} к/с (${v.fps_raw})`] as [string, string],
      ["Кодек", `${v.codec} · ${v.pix_fmt}${v.color_transfer ? ` · ${v.color_transfer}` : ""}`] as [string, string],
    ] : []),
    ["Звук", s.probe.audio.map((a) => `${a.codec} ${a.channels}ch ${a.sample_rate / 1000}k`).join(", ") || "нет"],
    ["Снято", s.probe.creation_time ? new Date(s.probe.creation_time).toLocaleString("ru-RU") : "—"],
    ["Расшифровка", s.transcript_meta ? `${s.transcript_meta.engine} ${s.transcript_meta.model}` : "нет"],
  ];
  return (
    <div className="shrink-0 border-t border-line p-3">
      <div className="label mb-2">Источник</div>
      <dl className="grid grid-cols-[88px_1fr] gap-x-2 gap-y-1 text-[12px]">
        {rows.map(([k, val]) => (<Fragment key={k}><dt className="text-faint">{k}</dt><dd className="tnum truncate" title={val}>{val}</dd></Fragment>))}
      </dl>
      {window.cutroom && <button className="btn btn-sm btn-ghost mt-2 -ml-2" onClick={() => window.cutroom?.revealInFolder(s.path)}>Показать в папке</button>}
    </div>
  );
}
