import { useState } from "react";
import { api } from "../api";
import { Icon } from "../icons";
import { markerOut, selection, useStore } from "../store";
import { cls } from "../util";

const QUICK = ["Сделай короче", "Возьми другой дубль", "Убери паузы и «э-э»", "Добавь реакцию после фразы", "Закончи мысль целиком"];

// Ask the agent to rework only the selected fragments; the rest of the cut stays untouched.
export function FragmentBar() {
  const edl = useStore((s) => s.edl?.edl);
  const selRange = useStore((s) => s.selRange);
  const selEnd = useStore((s) => s.selEnd);
  const markers = useStore((s) => s.markers);
  const gate = useStore((s) => s.strategy?.gate_open);
  const jobs = useStore((s) => s.jobs);
  const [text, setText] = useState("");
  const sel = selection();
  void selRange; void selEnd;
  if (!edl || !sel) return null;
  const [a, b] = sel;
  const span = edl.ranges.slice(a, b + 1);
  const dur = span.reduce((x, r) => x + r.end - r.start, 0);
  const t0 = edl.ranges[a].out_start, t1 = t0 + dur;
  // markers inside the span travel with the request as notes
  const notes = markers.filter((m) => !m.done && m.note.trim()).filter((m) => { const t = markerOut(m); return t != null && t >= t0 && t <= t1; });
  const job = jobs.filter((j) => j.kind === "fragment").sort((x, y) => y.id - x.id)[0];
  const busy = job && (job.status === "queued" || job.status === "running");

  const send = async (req: string) => {
    if (!req.trim()) return;
    try {
      await api.post("/api/edl/fragment", { start: a, end: b, request: req, notes: notes.map((m) => m.note) });
      setText("");
      useStore.getState().say("info", "Агент переделывает выделенное. Остальной монтаж не тронут; отменить — Ctrl+Z.");
    } catch (e) { useStore.getState().say("err", (e as Error).message); }
  };

  return (
    <div className="shrink-0 border-b border-line bg-accent-soft px-3 py-1.5 flex flex-wrap items-center gap-2 text-[12px]">
      <span className="font-medium whitespace-nowrap">
        {a === b ? `Фрагмент ${a + 1}` : `Фрагменты ${a + 1}–${b + 1}`} · {dur.toFixed(1)} с
      </span>
      {notes.length > 0 && <span className="badge" title={notes.map((m) => m.note).join("\n")}>маркеров: {notes.length}</span>}
      {!gate ? <span className="text-dim">Править можно после утверждения стратегии.</span> : busy ? (
        <span className="text-dim pulse">Агент переделывает фрагмент…</span>
      ) : (
        <>
          <form className="flex-1 min-w-[220px] flex gap-1.5" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <input className="input flex-1 h-7" value={text} onChange={(e) => setText(e.target.value)}
              placeholder="Что изменить только здесь? Например: «короче, без истории про Рим»" aria-label="Запрос на правку фрагмента" />
            <button className="btn btn-primary btn-sm h-7" disabled={!text.trim()}><Icon name="send" size={13} /> Агенту</button>
          </form>
          <div className="flex flex-wrap gap-1">
            {QUICK.map((q) => <button key={q} className="chip h-6" onClick={() => send(q)}>{q}</button>)}
          </div>
        </>
      )}
      <button className={cls("btn btn-ghost btn-sm h-6 ml-auto")} onClick={() => useStore.getState().set({ selRange: null, selEnd: null })} aria-label="Снять выделение">
        <Icon name="x" size={13} />
      </button>
    </div>
  );
}
