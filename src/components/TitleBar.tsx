import { api } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";
import { cls } from "../util";
import { ClaudeBadge } from "./ClaudeBadge";

const WAVES = [
  { n: 1 as const, name: "Черновик", hint: "Грубая нарезка, прокси 540p, плейсхолдеры инфографики" },
  { n: 2 as const, name: "Доработка", hint: "Инфографика, цвет, огрехи — параллельно по слотам" },
  { n: 3 as const, name: "Финал", hint: "Исходное разрешение, loudnorm, self-eval" },
];

export function TitleBar() {
  const project = useStore((s) => s.project);
  const wave = useStore((s) => s.wave);
  const view = useStore((s) => s.view);
  const jobs = useStore((s) => s.jobs);
  const layout = useStore((s) => s.layout);
  const set = useStore((s) => s.set);
  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const running = active.filter((j) => j.status === "running").length;
  const failed = jobs.filter((j) => j.status === "error").length;
  const isElectron = !!window.cutroom;

  const toggleTheme = () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    api.patch("/api/settings", { theme: next }).catch(() => {});
  };

  return (
    <header className={cls("drag h-10 shrink-0 flex items-center gap-3 pl-3 bg-panel border-b border-line", isElectron ? "pr-[140px]" : "pr-3")}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-semibold tracking-tight text-[13px]">Cutroom</span>
        <span className="text-faint">/</span>
        <span className="truncate text-dim max-w-[260px]" title={project?.dir}>{project?.open ? project.name : "без проекта"}</span>
      </div>

      <nav className="nodrag flex items-center rounded-[6px] border border-line-strong overflow-hidden ml-2" aria-label="Волна конвейера">
        {WAVES.map((w) => (
          <button
            key={w.n}
            title={`${w.hint} (клавиша ${w.n})`}
            onClick={() => set({ wave: w.n })}
            className={cls("h-7 px-3 text-[12px] font-medium border-r border-line-strong last:border-r-0",
              wave === w.n ? "bg-accent-soft text-accent" : "text-dim hover:text-fg hover:bg-panel-3")}
          >
            <span className="mono mr-1.5 opacity-70">{w.n}</span>{w.name}
          </button>
        ))}
      </nav>

      <div className="flex-1" />

      <div className="nodrag flex items-center gap-1">
        <button className="btn btn-ghost btn-sm" onClick={() => set({ paletteOpen: true })} title="Команды (Ctrl+K)">
          <Icon name="search" size={14} /> <span className="max-[1000px]:hidden">Команды</span>
          <kbd className="mono text-[10.5px] text-faint ml-1 max-[1000px]:hidden">Ctrl K</kbd>
        </button>
        <button
          className={cls("btn btn-sm", running ? "border-accent/60" : "btn-ghost")}
          onClick={() => set({ queueOpen: !useStore.getState().queueOpen })}
          title="Очередь задач (`)"
        >
          <Icon name="queue" size={14} />
          <span className="tnum">{active.length ? `${running} / ${active.length}` : "Очередь"}</span>
          {running > 0 && <span className="w-1.5 h-1.5 rounded-full bg-accent pulse" />}
          {failed > 0 && <span className="badge badge-err">{failed} ошиб.</span>}
        </button>
        <ClaudeBadge />
        <button className={cls("btn btn-sm", view === "system" ? "" : "btn-ghost")} onClick={() => set({ view: view === "system" ? "edit" : "system" })}
          title="Железо, модели, ffmpeg">
          <Icon name="cpu" size={14} /> Система
        </button>
        <span className="w-px h-5 bg-line mx-1" />
        <button className="btn btn-ghost btn-sm" aria-label="Левая панель" title="Левая панель"
          onClick={() => useStore.getState().setLayout({ leftOpen: !layout.leftOpen })}><Icon name="panelLeft" size={14} /></button>
        <button className="btn btn-ghost btn-sm" aria-label="Инспектор" title="Инспектор"
          onClick={() => useStore.getState().setLayout({ rightOpen: !layout.rightOpen })}><Icon name="panelRight" size={14} /></button>
        <button className="btn btn-ghost btn-sm" aria-label="Тема" title="Светлая / тёмная тема" onClick={toggleTheme}>
          <Icon name="sun" size={14} />
        </button>
      </div>
    </header>
  );
}
