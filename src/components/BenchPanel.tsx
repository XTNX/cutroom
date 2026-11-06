import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";

type Row = {
  engine: string; model: string; words?: number; fillers?: number; filler_share?: number; filler_retention?: number | null;
  rtf?: number | null; wer?: number | null; boundary_ms?: number | null; disagree_vs_large_v3?: number | null;
  skipped?: string; error?: string;
};
type Result = { when: string; seconds: number; sources: { id: string; seconds: number }[]; has_reference: boolean; has_boundaries: boolean; rows: Row[] };

export function BenchPanel() {
  const project = useStore((s) => s.project);
  const jobs = useStore((s) => s.jobs);
  const job = jobs.filter((j) => j.kind === "bench").sort((a, b) => b.id - a.id)[0];
  const [res, setRes] = useState<Result | null>(null);

  useEffect(() => {
    if (!project?.open) return;
    api.get<Result>("/api/bench").then(setRes).catch(() => setRes(null));
  }, [project?.open, job?.status]);

  const running = job && (job.status === "running" || job.status === "queued");
  return (
    <section className="rounded-[8px] border border-line bg-panel p-4 flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <div className="label">Замер ASR на нашем материале</div>
        <span className="flex-1" />
        {running ? <span className="text-[12px] text-dim tnum">идёт: {Math.round((job!.progress || 0) * 100)}%</span>
          : <button className="btn btn-sm" disabled={!project?.open} onClick={() => api.post("/api/bench", { seconds: 600 }).catch((e) => useStore.getState().say("err", e.message))}
            title="10 минут: самые длинные файлы проекта. Выбирайте записи без ПДн.">
            {res ? "Перезамерить" : "Запустить замер (10 мин материала)"}
          </button>}
      </div>
      {!project?.open && <p className="text-dim text-[12.5px]">Откройте проект: замер идёт на его записях.</p>}
      {res && (
        <>
          <p className="text-[12px] text-dim">{res.when} · {res.seconds} с из {res.sources.map((s) => s.id).join(", ")}. Таблица сохранена в <span className="mono">edit/asr_benchmark.md</span>.</p>
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px] tnum">
              <thead className="text-faint text-left">
                <tr>{["Движок", "Модель", "RTF", "Час аудио", "Слов", "Филлеров", "Доля", "Сохранено фил.", "WER", "Расхожд. с v3", "Границы"].map((h) => <th key={h} className="font-medium py-1 pr-3">{h}</th>)}</tr>
              </thead>
              <tbody>
                {res.rows.map((r) => (
                  <tr key={r.engine + r.model} className="border-t border-line">
                    <td className="py-1.5 pr-3">{r.engine}</td>
                    <td className="pr-3 font-medium">{r.model}</td>
                    {r.skipped || r.error ? <td colSpan={9} className="text-dim">{r.skipped ?? <span className="text-err">{r.error}</span>}</td> : <>
                      <td className="pr-3">{r.rtf}</td>
                      <td className="pr-3">{((r.rtf ?? 0) * 60).toFixed(1)} мин</td>
                      <td className="pr-3">{r.words}</td>
                      <td className="pr-3">{r.fillers}</td>
                      <td className="pr-3">{r.filler_share} %</td>
                      <td className="pr-3">{r.filler_retention != null ? `${r.filler_retention} %` : "—"}</td>
                      <td className="pr-3">{r.wer != null ? `${r.wer} %` : <span className="text-warn">[NEED: эталон]</span>}</td>
                      <td className="pr-3">{r.disagree_vs_large_v3 != null ? `${r.disagree_vs_large_v3} %` : "база"}</td>
                      <td className="pr-3">{r.boundary_ms != null ? `${r.boundary_ms} мс` : <span className="text-warn">[NEED: 20 слов]</span>}</td>
                    </>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11.5px] text-faint">
            «Расхождение» — не WER: эталона нет. Для честного WER положите ручную расшифровку этого же фрагмента в <span className="mono">edit/bench/reference.txt</span>,
            для ошибки границ — 20 слов с точными метками в <span className="mono">edit/bench/boundaries.json</span>. Основной движок выбираете вы.
          </p>
        </>
      )}
    </section>
  );
}
