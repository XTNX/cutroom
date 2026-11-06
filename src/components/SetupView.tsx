import { useEffect, useState } from "react";
import { Icon } from "../icons";
import { cls } from "../util";

export type SetupStep = { id: string; title: string; detail: string; status: "wait" | "run" | "done" | "error"; progress: number; current?: string };
export type SetupState = { state: "none" | "starting" | "running" | "done" | "error"; gpu?: boolean; steps?: SetupStep[]; log?: string[]; error?: string | null };

/** Subscribes to the first-run setup of an installed Cutroom (Electron main downloads the runtime). */
export function useSetup(): SetupState | null {
  const [st, setSt] = useState<SetupState | null>(null);
  useEffect(() => {
    const c = window.cutroom;
    if (!c?.setupStatus) return;
    c.setupStatus().then((s) => setSt(s));
    return c.onSetup((s) => setSt(s));
  }, []);
  return st;
}

export function SetupView({ st }: { st: SetupState }) {
  const [showLog, setShowLog] = useState(false);
  const steps = st.steps ?? [];
  return (
    <div className="flex-1 min-h-0 overflow-y-auto grid place-items-center p-4">
      <div className="w-full max-w-[620px] flex flex-col gap-5">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight">Первый запуск: Cutroom скачивает нужные модули</h1>
          <p className="text-dim mt-1">
            Это делается один раз на этом компьютере. {st.gpu === undefined ? "" : st.gpu ? "Найдена видеокарта NVIDIA — ставлю версии, которые считают на ней." : "Видеокарты NVIDIA нет — ставлю облегчённые версии для процессора."} Можно свернуть окно и заняться другим.
          </p>
        </div>
        {st.state === "starting" && <p className="text-dim pulse">Проверяю компьютер…</p>}
        <ol className="flex flex-col gap-2.5">
          {steps.map((s, k) => (
            <li key={s.id} className={cls("rounded-[8px] border p-3 flex flex-col gap-2", s.status === "run" ? "border-accent/60 bg-accent-soft" : s.status === "error" ? "border-err/60" : "border-line")}>
              <div className="flex items-center gap-2.5">
                <span className={cls("w-6 h-6 rounded-full grid place-items-center text-[12px] font-semibold shrink-0",
                  s.status === "done" ? "bg-ok text-bg" : s.status === "run" ? "bg-accent text-accent-fg" : s.status === "error" ? "bg-err text-bg" : "bg-panel-3 text-dim")}>
                  {s.status === "done" ? <Icon name="check" size={13} /> : k + 1}
                </span>
                <span className="font-medium flex-1">{s.title}</span>
                <span className="text-faint text-[12px]">{s.detail}</span>
              </div>
              {s.status === "run" && (
                <div className="flex items-center gap-2 pl-8">
                  <div className="flex-1 h-1.5 rounded-full bg-panel-3 overflow-hidden">
                    <div className={cls("h-full bg-accent", s.progress <= 0.01 && "pulse")} style={{ width: `${Math.max(3, s.progress * 100)}%` }} />
                  </div>
                  <span className="tnum text-[12px] text-dim w-10 text-right">{Math.round(s.progress * 100)}%</span>
                </div>
              )}
              {s.status === "run" && s.current && <span className="pl-8 text-[12px] text-faint truncate">{s.current}</span>}
            </li>
          ))}
        </ol>
        {st.state === "error" && (
          <div className="rounded-[8px] border border-err/60 p-3 flex flex-col gap-2">
            <p className="text-err whitespace-pre-wrap text-[12.5px]">{st.error}</p>
            <button className="btn btn-primary self-start" onClick={() => window.cutroom?.setupRetry()}>Повторить</button>
          </div>
        )}
        {st.state === "done" && <p className="text-ok">Готово. Запускаю Cutroom…</p>}
        <button className="btn btn-ghost btn-sm self-start" onClick={() => setShowLog(!showLog)}>{showLog ? "Скрыть подробности" : "Подробности"}</button>
        {showLog && <pre className="rounded-[6px] bg-bg border border-line p-2 mono text-[11px] text-dim max-h-[220px] overflow-auto whitespace-pre-wrap">{(st.log ?? []).join("\n") || "пока пусто"}</pre>}
        <p className="text-[11.5px] text-faint">Всё скачивается в папку Cutroom с официальных источников по зафиксированным версиям, файлы сверяются по контрольным суммам. Ваши видео при этом никуда не отправляются.</p>
      </div>
    </div>
  );
}
