import { useEffect, useRef, useState } from "react";
import { api, type Marker } from "../api";
import { Icon } from "../icons";
import { anchorAtPlayhead, markerOut, seek, useStore } from "../store";
import { cls, fmtTime } from "../util";

export async function addMarkerAtPlayhead(note = "") {
  const st = useStore.getState();
  const at = anchorAtPlayhead();
  if (!at) return st.say("info", "Поставьте плеер на материал: маркер привязывается к записи.");
  try {
    const m = await api.post<Marker>("/api/markers", { ...at, note });
    await st.loadMarkers();
    st.set({ selMarker: m.id, leftTab: "markers", layout: { ...st.layout, leftOpen: true } });
  } catch (e) { st.say("err", (e as Error).message); }
}

export function MarkersPanel() {
  const markers = useStore((s) => s.markers);
  const sel = useStore((s) => s.selMarker);
  const monitor = useStore((s) => s.monitor);
  const edl = useStore((s) => s.edl?.edl);
  const gate = useStore((s) => s.strategy?.gate_open);
  const program = monitor === "program" && !!edl;
  const list = [...markers].sort((a, b) => (markerOut(a) ?? 1e9 + a.time) - (markerOut(b) ?? 1e9 + b.time));
  const open = list.filter((m) => !m.done).length;

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 text-[12px]">
        <span className="text-dim">{markers.length ? `${open} открыто · ${markers.length - open} сделано` : "Маркеров нет"}</span>
        <span className="flex-1" />
        <button className="btn btn-sm" onClick={() => addMarkerAtPlayhead()} title="Маркер в точке плеера (M)"><Icon name="plus" size={13} /> Маркер <kbd className="mono text-[10px] text-faint">M</kbd></button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {!markers.length && (
          <p className="p-4 text-dim text-[12.5px]">Нажмите <b>M</b> во время просмотра, чтобы отметить место и написать, что с ним сделать. Маркер привязан к записи и не потеряется при перемонтаже; заметки внутри выделенного фрагмента уйдут агенту вместе с запросом.</p>
        )}
        {list.map((m) => <MarkerRow key={m.id} m={m} selected={sel === m.id} program={program} gate={!!gate} />)}
      </div>
    </div>
  );
}

function MarkerRow({ m, selected, program, gate }: { m: Marker; selected: boolean; program: boolean; gate: boolean }) {
  const [note, setNote] = useState(m.note);
  const ref = useRef<HTMLTextAreaElement>(null);
  const out = markerOut(m);
  useEffect(() => setNote(m.note), [m.note]);
  useEffect(() => { if (selected && !m.note) ref.current?.focus(); }, [selected]);
  const save = (patch: Partial<Marker>) => api.patch(`/api/markers/${m.id}`, patch).then(() => useStore.getState().loadMarkers());
  const goto = () => {
    const st = useStore.getState();
    st.set({ selMarker: m.id });
    if (program && out != null) seek(out);
    else { if (st.selected !== m.source) st.select(m.source); setTimeout(() => seek(m.time), 150); }
  };
  const ask = async () => {
    const st = useStore.getState();
    const e = st.edl?.edl;
    if (!e || out == null || !note.trim()) return;
    const i = e.ranges.findIndex((r) => out >= r.out_start && out <= r.out_start + (r.end - r.start));
    try {
      await api.post("/api/edl/fragment", { start: i, end: i, request: note, notes: [] });
      st.say("info", `Агент правит фрагмент ${i + 1} по заметке.`);
      save({ done: true });
    } catch (err) { st.say("err", (err as Error).message); }
  };
  return (
    <div className={cls("px-3 py-2 border-b border-line flex flex-col gap-1.5", selected && "bg-accent-soft", m.done && "opacity-60")}>
      <div className="flex items-center gap-2 text-[11.5px]">
        <span className="w-2.5 h-2.5 shrink-0" style={{ background: "var(--marker)", clipPath: "polygon(0 0,100% 0,75% 50%,100% 100%,0 100%)" }} />
        <button className="mono tnum text-faint hover:text-fg" onClick={goto} title="Перейти">
          {out != null ? fmtTime(out) : `${m.source} ${fmtTime(m.time)}`}
        </button>
        {out == null && program && <span className="badge" title="Это место сейчас не входит в монтаж">вне монтажа</span>}
        <span className="text-faint truncate">{m.source}</span>
        <span className="flex-1" />
        <button className="btn btn-ghost btn-sm h-5 px-1" title={m.done ? "Вернуть в работу" : "Сделано"} onClick={() => save({ done: !m.done })}><Icon name="check" size={12} /></button>
        <button className="btn btn-ghost btn-sm h-5 px-1" title="Удалить" onClick={() => api.del(`/api/markers/${m.id}`).then(() => useStore.getState().loadMarkers())}><Icon name="x" size={12} /></button>
      </div>
      <textarea ref={ref} rows={2} className="input h-auto py-1.5 text-[12.5px] resize-none" placeholder="Что здесь сделать?"
        value={note} onChange={(e) => setNote(e.target.value)} onBlur={() => note !== m.note && save({ note })}
        onFocus={() => useStore.getState().set({ selMarker: m.id })} />
      {program && gate && out != null && note.trim() && !m.done && (
        <button className="btn btn-sm self-start" onClick={ask} title="Агент переделает фрагмент под этим маркером по вашей заметке">
          <Icon name="send" size={12} /> Попросить агента исправить здесь
        </button>
      )}
    </div>
  );
}
