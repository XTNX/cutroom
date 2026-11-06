import { useMemo, useState } from "react";
import { api, type Job } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";
import { cls, fmtEta } from "../util";

const LANE_NAME: Record<string, string> = {
  audio: "Аудио и синхронизация", proxy: "Прокси", asr: "Расшифровка", io: "Кадры и загрузки", pack: "Сборка", render: "Рендер", agent: "Агенты (Claude)",
};
const STATUS: Record<Job["status"], string> = {
  queued: "в очереди", running: "идёт", done: "готово", cached: "из кэша", error: "ошибка", cancelled: "отменено", blocked: "заблокировано",
};

export function QueueDrawer() {
  const open = useStore((s) => s.queueOpen);
  const jobs = useStore((s) => s.jobs);
  const set = useStore((s) => s.set);
  const [view, setView] = useState<"lanes" | "dag">("lanes");
  const [expanded, setExpanded] = useState<number | null>(null);

  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const lanes = useMemo(() => {
    const m = new Map<string, Job[]>();
    for (const j of jobs) m.set(j.lane, [...(m.get(j.lane) ?? []), j]);
    return [...m.entries()];
  }, [jobs]);
  // total ETA ≈ per lane: running ETA + queued expectations / lane concurrency, max across lanes (they run in parallel)
  const eta = useMemo(() => {
    const conc: Record<string, number> = { io: 4, audio: 3, proxy: 2, asr: 1, pack: 1, render: 1, agent: 2 };
    let worst = 0;
    for (const [lane, js] of lanes) {
      const sum = js.filter((j) => j.status === "queued" || j.status === "running").reduce((a, j) => a + (j.eta ?? 0), 0);
      worst = Math.max(worst, sum / (conc[lane] ?? 1));
    }
    return worst;
  }, [lanes]);

  if (!open) return null;
  return (
    <div className="fixed left-0 right-0 bottom-0 z-30 h-[46vh] min-h-[260px] bg-panel border-t border-line-strong shadow-[var(--shadow-pop)] flex flex-col"
      role="dialog" aria-label="Очередь задач">
      <div className="h-10 shrink-0 flex items-center gap-2 px-3 border-b border-line">
        <span className="label">Очередь</span>
        <span className="text-[12px] text-dim tnum">
          {active.length ? <>{active.filter((j) => j.status === "running").length} идёт · {active.length} всего · осталось ~{fmtEta(eta)}</> : "Пусто: всё готово"}
        </span>
        <div className="flex rounded-[6px] border border-line-strong overflow-hidden ml-3">
          {(["lanes", "dag"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)} className={cls("h-6 px-2 text-[11.5px]", view === v ? "bg-panel-3 text-fg" : "text-dim")}>
              {v === "lanes" ? "По полосам" : "Граф по файлам"}
            </button>
          ))}
        </div>
        <span className="flex-1" />
        <button className="btn btn-sm btn-ghost" onClick={() => api.post("/api/jobs/clear")}>Убрать завершённые</button>
        {active.length > 0 && <button className="btn btn-sm" onClick={() => api.post("/api/jobs/cancel_all")}>Отменить всё</button>}
        <button className="btn btn-sm btn-ghost" aria-label="Закрыть" onClick={() => set({ queueOpen: false })}><Icon name="x" size={14} /></button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {jobs.length === 0 && <p className="p-4 text-dim text-[12.5px]">Задач нет. Тяжёлые операции здесь всегда видны заранее, с оценкой времени, и их можно отменить.</p>}
        {view === "lanes" ? lanes.map(([lane, js]) => (
          <section key={lane}>
            <h3 className="label px-3 pt-3 pb-1">{LANE_NAME[lane] ?? lane}
              <span className="normal-case tracking-normal font-normal text-faint"> · {js.filter((j) => j.status === "running").length} идёт, {js.filter((j) => j.status === "queued").length} ждёт</span>
            </h3>
            {js.slice().sort((a, b) => rank(a) - rank(b) || a.id - b.id).slice(0, 80).map((j) => (
              <JobRow key={j.id} j={j} expanded={expanded === j.id} onToggle={() => setExpanded(expanded === j.id ? null : j.id)} />
            ))}
          </section>
        )) : <Dag jobs={jobs} />}
      </div>
    </div>
  );
}

const rank = (j: Job) => ({ running: 0, error: 1, queued: 2, blocked: 3, cancelled: 4, done: 5, cached: 5 })[j.status];

function JobRow({ j, expanded, onToggle }: { j: Job; expanded: boolean; onToggle: () => void }) {
  return (
    <div className="border-b border-line">
      <div className="px-3 h-8 flex items-center gap-2 text-[12.5px]">
        <button className="flex-1 min-w-0 flex items-center gap-2 text-left" onClick={onToggle} aria-expanded={expanded}>
          <StatusDot s={j.status} />
          <span className="truncate">{j.label}</span>
          {j.heavy && <span className="badge" title="Долгая операция">долго</span>}
        </button>
        {j.status === "running" && (
          <div className="w-[140px] h-1.5 rounded-full bg-panel-3 overflow-hidden" aria-label={`${Math.round(j.progress * 100)}%`}>
            <div className="h-full bg-accent" style={{ width: `${Math.max(2, j.progress * 100)}%` }} />
          </div>
        )}
        <span className="w-[88px] text-right tnum text-[11.5px] text-dim">
          {j.status === "running" ? `${Math.round(j.progress * 100)}% · ${fmtEta(j.eta)}`
            : j.status === "queued" ? (j.eta ? `~${fmtEta(j.eta)}` : STATUS[j.status])
            : j.elapsed != null && (j.status === "done" || j.status === "cached") ? `${STATUS[j.status]} · ${fmtEta(j.elapsed)}` : STATUS[j.status]}
        </span>
        {(j.status === "queued" || j.status === "running") && (
          <button className="btn btn-ghost btn-sm" onClick={() => api.post(`/api/jobs/${j.id}/cancel`)}>Отменить</button>
        )}
        {(j.status === "error" || j.status === "cancelled" || j.status === "blocked") && (
          <button className="btn btn-sm" onClick={() => api.post(`/api/jobs/${j.id}/retry`)}>Повторить</button>
        )}
      </div>
      {j.error && <p className="px-8 pb-1.5 text-[11.5px] text-err break-words">{j.error}</p>}
      {expanded && (
        <pre className="mx-3 mb-2 p-2 rounded-[6px] bg-bg mono text-[11px] text-dim max-h-[180px] overflow-auto whitespace-pre-wrap">{j.log.join("\n") || "лог пуст"}</pre>
      )}
    </div>
  );
}

function StatusDot({ s }: { s: Job["status"] }) {
  const c = s === "running" ? "bg-accent pulse" : s === "done" || s === "cached" ? "bg-ok" : s === "error" || s === "blocked" ? "bg-err" : "bg-faint";
  return <span className={cls("w-2 h-2 rounded-full shrink-0", c)} title={STATUS[s]} />;
}

// One row per source: proxy ─ thumb, audio ─ peaks ─ asr ─ (pack). Shows where each file is in the DAG.
function Dag({ jobs }: { jobs: Job[] }) {
  const bySource = new Map<string, Job[]>();
  for (const j of jobs) if (j.source) bySource.set(j.source, [...(bySource.get(j.source) ?? []), j]);
  const kinds = ["audio", "peaks", "asr", "proxy", "thumb"];
  return (
    <table className="w-full text-[12px]">
      <thead>
        <tr className="text-faint">
          <th className="text-left font-medium px-3 py-1.5">Файл</th>
          {kinds.map((k) => <th key={k} className="text-left font-medium px-2">{({ audio: "аудио →", peaks: "волна", asr: "→ текст → pack", proxy: "прокси →", thumb: "кадр" } as Record<string, string>)[k]}</th>)}
        </tr>
      </thead>
      <tbody>
        {[...bySource.entries()].map(([sid, js]) => (
          <tr key={sid} className="border-t border-line">
            <td className="px-3 py-1 truncate max-w-[200px]">{sid}</td>
            {kinds.map((k) => {
              const j = js.filter((x) => x.kind === k).sort((a, b) => b.id - a.id)[0];
              return (
                <td key={k} className="px-2">
                  {j ? <span className="inline-flex items-center gap-1.5"><StatusDot s={j.status} />
                    <span className="tnum text-dim">{j.status === "running" ? `${Math.round(j.progress * 100)}%` : STATUS[j.status]}</span></span>
                    : <span className="text-faint">—</span>}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
