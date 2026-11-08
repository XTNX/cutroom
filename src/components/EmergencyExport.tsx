import { useEffect, useRef } from "react";
import { api, type Job } from "../api";
import { useStore } from "../store";
import { cls } from "../util";
import { refreshClaude, useClaude } from "./ClaudeBadge";

type Last = { file: string; source: string; skipped: string[]; version: number; when: string } | null;

const AGENT_KINDS = new Set(["strategy", "editor", "ask", "fragment", "qcut", "qai", "qcard", "qvariants", "gfx", "kb-notes"]);

/**
 * «Экстренный экспорт»: the film out without any model call — always available, and highlighted
 * when the model's tokens ran out (an agent job failed on the usage limit).
 */
export function EmergencyExport() {
  const jobs = useStore((s) => s.jobs);
  const claude = useClaude();
  const seen = useRef(new Map<number, string>());
  const quota = !!claude?.agent?.quota;
  const job = jobs.filter((j) => j.kind === "emergency").at(-1);
  const running = !!job && (job.status === "running" || job.status === "queued");

  useEffect(() => {
    for (const j of jobs) {
      const was = seen.current.get(j.id);
      if (was && was !== j.status) {
        if (j.kind === "emergency" && j.status === "done") announce();
        if (j.kind === "emergency" && j.status === "error") useStore.getState().say("err", `Экстренный экспорт: ${j.error ?? "ошибка"}`);
        // an agent job ended: the usage limit may have been hit (or lifted)
        if (AGENT_KINDS.has(j.kind) && (j.status === "error" || j.status === "done")) refreshClaude();
      }
      seen.current.set(j.id, j.status);
    }
  }, [jobs]);

  const announce = async () => {
    try {
      const r = await api.get<{ last: Last }>("/api/export/emergency");
      if (!r.last) return;
      useStore.getState().say("ok", `Экстренный экспорт готов: ${r.last.file.split(/[\\/]/).pop()} (${r.last.source})` +
        (r.last.skipped.length ? `. Не вошло: ${r.last.skipped.length} — список в .txt рядом с файлом.` : "."));
      window.cutroom?.revealInFolder(r.last.file);
    } catch { /* the toast is a nicety */ }
  };
  const go = async () => {
    try { await api.post<Job>("/api/export/emergency", {}); }
    catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  return (
    <button onClick={go} disabled={running}
      className={cls("btn btn-sm", quota ? "border-err text-err pulse" : "btn-ghost")}
      title={"Экспорт без ИИ: 1080p из готовых блоков (карточки, которые ИИ не успел, пропускаются); если не выйдет — последнее собранное превью. " +
        "Файл и список пропущенного — в edit/export." + (quota ? "\nТокены модели закончились — ИИ-запросы недоступны до сброса лимита." : "")}>
      <span className={cls("w-1.5 h-1.5 rounded-full", running ? "bg-accent pulse" : quota ? "bg-err" : "bg-faint")} />
      <span className="max-[1100px]:hidden">{running ? `Экспортирую… ${job?.progress ? Math.round(job.progress * 100) + "%" : ""}` : quota ? "Токены кончились — экстренный экспорт" : "Экстренный экспорт"}</span>
      <span className="min-[1101px]:hidden">{running ? "…" : "Экспорт"}</span>
    </button>
  );
}
