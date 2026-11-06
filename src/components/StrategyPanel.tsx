import { useEffect, useState } from "react";
import { api, type Strategy } from "../api";
import { seek, useStore } from "../store";
import { cls, fmtTime } from "../util";

export function StrategyPanel() {
  const project = useStore((s) => s.project);
  const strategy = useStore((s) => s.strategy);
  const edlState = useStore((s) => s.edl);
  const jobs = useStore((s) => s.jobs);
  const prompt = useStore((s) => s.prompt);
  const chips = useStore((s) => s.chips);
  const [text, setText] = useState("");
  const [feedback, setFeedback] = useState("");
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<"none" | "rewrite" | "ask">("none");
  const latest = (k: string) => jobs.filter((j) => j.kind === k).sort((a, b) => b.id - a.id)[0];
  const sJob = latest("strategy"), eJob = latest("editor"), aJob = latest("ask");
  const busy = (j?: { status: string }) => !!j && (j.status === "queued" || j.status === "running");
  const mem = project?.memory;

  useEffect(() => { setText(strategy?.text ?? ""); }, [strategy?.text, strategy?.revision]);

  const call = async (fn: () => Promise<unknown>) => {
    try { await fn(); await useStore.getState().loadStrategy(); } catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const saveText = () => text !== strategy?.text && call(() => api.patch<Strategy>("/api/strategy", { text }));
  const requestStrategy = (fb?: string) => call(() => api.post("/api/strategy", { prompt, chips, feedback: fb || null }));

  const st = strategy?.status ?? "none";
  const d = strategy?.data;
  const edl = edlState?.edl;

  return (
    <div className="p-3 flex flex-col gap-3 text-[12.5px]">
      {mem?.exists && mem.summary && st === "none" && (
        <div className="rounded-[8px] border border-line bg-panel-2 p-3">
          <div className="label mb-1.5">Прошлая сессия · {mem.last_title}</div>
          <p className="text-dim">{mem.summary}</p>
          <p className="text-faint mt-1.5 text-[11.5px]">Продолжить с этого места? Опишите задачу в поле внизу и нажмите «Предложить стратегию».</p>
        </div>
      )}

      {busy(sJob) && <AgentCard title="Агент читает материал" job={sJob!} />}
      {sJob?.status === "error" && !busy(sJob) && <ErrorCard text={sJob.error ?? ""} onRetry={() => api.post(`/api/jobs/${sJob.id}/retry`)} />}

      {st === "none" && !busy(sJob) && (
        <div className="rounded-[8px] border border-dashed border-line-strong p-3 flex flex-col gap-2">
          <div className="flex items-center gap-2"><span className="label">Ворота стратегии</span><span className="badge badge-warn">закрыто</span></div>
          <p className="text-dim">До утверждения стратегии ни один рез и ни один render.py не запускается (Hard Rule 11).
            Агент прочитает takes_packed.md и предложит 4–8 предложений: структура, дубли, направление резки, инфографика, цвет, субтитры, длина.</p>
          <button className="btn btn-primary self-start" onClick={() => requestStrategy()}>Предложить стратегию</button>
        </div>
      )}

      {st !== "none" && (
        <div className={cls("rounded-[8px] border p-3 flex flex-col gap-2.5", st === "approved" && strategy?.gate_open ? "border-ok/50" : "border-line-strong")}>
          <div className="flex items-center gap-2">
            <span className="label">Стратегия · версия {strategy?.revision}</span>
            {strategy?.gate_open ? <span className="badge badge-ok">утверждено {strategy.approved_at?.slice(11, 16)}</span>
              : strategy?.dirty ? <span className="badge badge-warn">текст изменён — ворота снова закрыты</span>
              : <span className="badge badge-warn">черновик · ворота закрыты</span>}
          </div>
          <textarea className="input h-auto min-h-[150px] py-2 leading-relaxed resize-y" value={text}
            onChange={(e) => setText(e.target.value)} onBlur={saveText} aria-label="Текст стратегии" />
          {d && (
            <div className="flex flex-col gap-1.5 text-[12px]">
              <Row k="Длина" v={`${Math.round(d.target_seconds)} с · ${d.aspect}`} />
              {d.beats.length > 0 && <Row k="Биты" v={d.beats.map((b) => b.name).join(" → ")} />}
              {d.takes && <Row k="Дубли" v={d.takes} />}
              {d.graphics.length > 0 && <Row k="Инфографика" v={d.graphics.map((g) => `${g.type}: ${g.content}`).join("; ")} />}
              {d.slips.length > 0 && <Row k="Оговорки" v={`${d.slips.length} — редактор их обойдёт`} />}
              {d.needs.length > 0 && (
                <div>
                  <div className="text-faint mb-1">Нужно от вас ({d.needs.length})</div>
                  <ul className="flex flex-col gap-1 list-disc pl-4 text-warn">
                    {d.needs.map((n, i) => <li key={i}>{n.replace(/^\[NEED:\s*/, "").replace(/\]$/, "")}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
          {d && d.questions.length > 0 && !strategy?.gate_open && (
            <div className="flex flex-col gap-2 border-t border-line pt-2">
              {d.questions.map((q, i) => (
                <div key={i}>
                  <p className="mb-1">{q.question}</p>
                  <div className="flex flex-wrap gap-1">
                    {q.options.map((o) => (
                      <button key={o} className="chip" onClick={() => { setMode("rewrite"); setFeedback((f) => (f ? f + "\n" : "") + `${q.question} — ${o}`); }}>{o}</button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          {!strategy?.gate_open && (
            <div className="flex flex-wrap gap-1.5">
              <button className="btn btn-primary" disabled={busy(sJob) || !text.trim()}
                onClick={() => call(async () => { await saveText(); await api.post("/api/strategy/approve"); })}>Утвердить стратегию</button>
              <button className="btn" disabled={busy(sJob)} onClick={() => setMode(mode === "rewrite" ? "none" : "rewrite")}>Переписать с учётом правок</button>
              <button className="btn btn-ghost" disabled={busy(aJob)} onClick={() => setMode(mode === "ask" ? "none" : "ask")}>Задать вопрос агенту</button>
            </div>
          )}
          {mode === "rewrite" && (
            <div className="flex flex-col gap-1.5">
              <textarea className="input h-auto min-h-[64px] py-2" placeholder="Что поменять: «короче, начни с шутки про кофе, без инфографики»"
                value={feedback} onChange={(e) => setFeedback(e.target.value)} />
              <button className="btn self-start" disabled={!feedback.trim() || busy(sJob)}
                onClick={() => { requestStrategy(feedback); setMode("none"); setFeedback(""); }}>Переписать</button>
            </div>
          )}
          {mode === "ask" && (
            <form className="flex gap-1.5" onSubmit={(e) => { e.preventDefault(); call(() => api.post("/api/strategy/ask", { question })); setQuestion(""); }}>
              <input className="input flex-1" placeholder="Вопрос о материале или стратегии" value={question} onChange={(e) => setQuestion(e.target.value)} />
              <button className="btn" disabled={!question.trim()}>Спросить</button>
            </form>
          )}
          {busy(aJob) && <p className="text-dim pulse">Агент отвечает…</p>}
          {aJob?.status === "error" && <ErrorCard text={aJob.error ?? ""} onRetry={() => api.post(`/api/jobs/${aJob.id}/retry`)} />}
          {strategy?.qa?.slice(-3).map((x, i) => (
            <div key={i} className="rounded-[6px] bg-panel-2 p-2">
              <p className="text-faint text-[11.5px]">{x.when} · {x.q}</p>
              <p>{x.answer}</p>
              {x.suggested_edit && <button className="btn btn-sm mt-1" onClick={() => setText((t) => t + "\n" + x.suggested_edit)}>Добавить в стратегию: «{x.suggested_edit.slice(0, 60)}…»</button>}
            </div>
          ))}
        </div>
      )}

      {strategy?.gate_open && (
        <>
          {busy(eJob) && <AgentCard title="Агент-редактор выбирает дубли" job={eJob!} />}
          {eJob?.status === "error" && <ErrorCard text={eJob.error ?? ""} onRetry={() => api.post(`/api/jobs/${eJob.id}/retry`)} />}
          {!edl && !busy(eJob) && eJob?.status !== "error" && (
            <button className="btn btn-primary self-start" onClick={() => api.post("/api/edl/build").catch((e) => useStore.getState().say("err", e.message))}>Собрать черновик</button>
          )}
          {edl && <AgentReasons />}
        </>
      )}
    </div>
  );
}

function AgentReasons() {
  const edl = useStore((s) => s.edl?.edl);
  const [open, setOpen] = useState(true);
  if (!edl) return null;
  return (
    <section className="rounded-[8px] border border-line bg-panel-2">
      <button className="w-full flex items-center gap-2 p-3 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="label">Что думает агент</span>
        <span className="text-faint tnum">· {edl.ranges.length} фрагм. · {edl.total_duration_s.toFixed(1)} с</span>
      </button>
      {open && (
        <div className="px-3 pb-3 flex flex-col gap-2">
          {edl.cutroom.agent_check && <p className="text-dim text-[12px]">{edl.cutroom.agent_check}</p>}
          {edl.ranges.map((r, i) => (
            <button key={i} className="text-left rounded-[6px] p-2 hover:bg-panel-3 border border-line"
              onClick={() => { useStore.getState().set({ monitor: "program", selRange: i }); seek(r.out_start); }}>
              <div className="flex items-center gap-2 text-[11.5px]">
                <span className="mono tnum text-faint">{fmtTime(r.out_start)}</span>
                {r.beat && <span className="badge badge-accent">{r.beat}</span>}
                <span className="text-faint truncate">{r.angle ?? r.source} · {(r.end - r.start).toFixed(1)} с</span>
              </div>
              {r.quote && <p className="mt-1 line-clamp-2">«{r.quote}»</p>}
              {r.reason && <p className="text-dim text-[11.5px] mt-0.5">{r.reason}</p>}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function AgentCard({ title, job }: { title: string; job: { label: string; elapsed: number | null; log: string[] } }) {
  return (
    <div className="rounded-[8px] border border-accent/40 bg-accent-soft p-3">
      <div className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-accent pulse" /><span className="font-medium">{title}</span>
        <span className="text-faint tnum ml-auto">{job.elapsed ? `${Math.round(job.elapsed)} с` : "в очереди"}</span></div>
      <p className="text-dim text-[11.5px] mt-1">Тяжёлая операция: вызов Claude. Отменить можно в очереди.</p>
    </div>
  );
}

function ErrorCard({ text, onRetry }: { text: string; onRetry: () => void }) {
  const [msg, fix] = text.split(" Как исправить: ");
  return (
    <div className="rounded-[8px] border border-err/50 p-3 flex flex-col gap-1.5">
      <p className="text-err">{msg}</p>
      {fix && <p>Как исправить: <span className="mono text-[12px]">{fix}</span></p>}
      <button className="btn btn-sm self-start" onClick={onRetry}>Повторить</button>
    </div>
  );
}

const Row = ({ k, v }: { k: string; v: string }) => (
  <div className="flex gap-2"><span className="text-faint w-[84px] shrink-0">{k}</span><span className="text-dim">{v}</span></div>
);
