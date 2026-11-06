import * as Tabs from "@radix-ui/react-tabs";
import { useMemo, useState } from "react";
import { api, mediaUrl, type Job, type Source, type SyncGroup } from "../api";
import { Icon } from "../icons";
import { useStore, player } from "../store";
import { cls, fmtDur, fmtEta } from "../util";
import { TranscriptPanel } from "./TranscriptPanel";
import { ProblemsPanel } from "./ProblemsPanel";
import { MarkersPanel } from "./MarkersPanel";

export function LeftPanel() {
  const tab = useStore((s) => s.leftTab);
  const set = useStore((s) => s.set);
  const markerCount = useStore((s) => s.markers.filter((m) => !m.done).length);
  return (
    <Tabs.Root value={tab} onValueChange={(v) => set({ leftTab: v as never })} className="flex-1 min-h-0 flex flex-col">
      <Tabs.List className="flex px-2 border-b border-line shrink-0" aria-label="Левая панель">
        <Tabs.Trigger value="sources" className="tab-trigger px-2">Источники</Tabs.Trigger>
        <Tabs.Trigger value="transcript" className="tab-trigger px-2">Текст</Tabs.Trigger>
        <Tabs.Trigger value="problems" className="tab-trigger px-2">Проблемы</Tabs.Trigger>
        <Tabs.Trigger value="markers" className="tab-trigger px-2">Маркеры{markerCount ? <span className="ml-1 badge">{markerCount}</span> : null}</Tabs.Trigger>
      </Tabs.List>
      <Tabs.Content value="sources" className="flex-1 min-h-0 flex flex-col outline-none"><SourcesPanel /></Tabs.Content>
      <Tabs.Content value="transcript" className="flex-1 min-h-0 flex flex-col outline-none"><TranscriptPanel /></Tabs.Content>
      <Tabs.Content value="problems" className="flex-1 min-h-0 flex flex-col outline-none"><ProblemsPanel /></Tabs.Content>
      <Tabs.Content value="markers" className="flex-1 min-h-0 flex flex-col outline-none"><MarkersPanel /></Tabs.Content>
    </Tabs.Root>
  );
}

// Camera families until stage 3 groups by take: filename prefix + picture format.
function groupKey(s: Source): string {
  const prefix = s.name.match(/^[A-Za-z_]+/)?.[0]?.replace(/_?E$/, "") ?? "Прочее";
  const v = s.probe?.video;
  if (!v) return `${prefix} · аудио`;
  return `${prefix} · ${v.display_width}×${v.display_height}${v.hdr ? " HDR" : ""}`;
}

function SourcesPanel() {
  const sources = useStore((s) => s.sources);
  const jobs = useStore((s) => s.jobs);
  const project = useStore((s) => s.project);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const sync = useStore((s) => s.sync);
  const [mode, setMode] = useState<"takes" | "cams">("takes");
  const syncJob = jobs.filter((j) => j.kind === "sync").sort((a, b) => b.id - a.id)[0];
  const syncing = syncJob && (syncJob.status === "queued" || syncJob.status === "running");
  const byTakes = mode === "takes" && !!sync?.groups.length;

  const groups = useMemo(() => {
    const m = new Map<string, Source[]>();
    for (const s of sources) {
      if (filter && !s.name.toLowerCase().includes(filter.toLowerCase())) continue;
      const k = groupKey(s);
      m.set(k, [...(m.get(k) ?? []), s]);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [sources, filter]);

  const live = sources.filter((s) => !s.duplicate_of && !s.excluded);
  const total = live.reduce((a, s) => a + (s.probe?.duration ?? 0), 0);
  const ready = live.filter((s) => s.has.transcript || !s.probe?.audio.length).length;
  const pending = live.filter((s) => (s.kind === "video" && !s.has.proxy) || (s.probe?.audio.length && !s.has.transcript)).length;

  const ingest = async () => {
    setBusy(true);
    try { await api.post("/api/project/ingest", {}); useStore.getState().set({ queueOpen: true }); }
    catch (e) { useStore.getState().say("err", (e as Error).message); }
    finally { setBusy(false); }
  };
  const addFiles = async () => {
    const files = await window.cutroom?.pickFiles();
    if (files?.length) {
      await api.post("/api/project/add", { path: project?.dir, files });
      await useStore.getState().refreshSources();
    }
  };

  return (
    <div className="flex-1 min-h-0 flex flex-col"
      onDragOver={(e) => e.preventDefault()}
      onDrop={async (e) => {
        e.preventDefault();
        const paths = [...e.dataTransfer.files].map((f) => window.cutroom?.pathForFile(f)).filter(Boolean) as string[];
        if (!paths.length) return useStore.getState().say("info", "Перетаскивание файлов работает в окне Cutroom; в браузере добавьте файлы в папку проекта.");
        await api.post("/api/project/add", { path: project?.dir, files: paths });
        await useStore.getState().refreshSources();
      }}>
      <div className="p-2 flex flex-col gap-2 border-b border-line shrink-0">
        <div className="flex items-center gap-2 text-[12px] text-dim tnum">
          <span>{live.length} файлов · {fmtDur(total)}</span>
          <span className="flex-1" />
          <span title="Расшифровано">{ready}/{live.length} <Icon name="mic" size={12} className="inline -mt-0.5" /></span>
        </div>
        <div className="flex gap-1.5">
          <input className="input flex-1" placeholder="Фильтр по имени" value={filter} onChange={(e) => setFilter(e.target.value)} />
          {window.cutroom && <button className="btn btn-sm" onClick={addFiles} title="Добавить файлы"><Icon name="plus" size={14} /></button>}
        </div>
        <div className="flex items-center gap-1.5">
          <div className="flex rounded-[6px] border border-line-strong overflow-hidden">
            {(["takes", "cams"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)} aria-pressed={mode === m}
                className={cls("h-6 px-2 text-[11.5px]", mode === m ? "bg-panel-3 text-fg" : "text-dim")}>{m === "takes" ? "Дубли" : "Камеры"}</button>
            ))}
          </div>
          <span className="flex-1" />
          <button className="btn btn-sm" disabled={syncing || ready === 0} onClick={() => api.post("/api/sync").catch((e) => useStore.getState().say("err", e.message))}
            title="Найти ракурсы одного дубля и смещения по звуку (огибающая + GCC-PHAT)">
            {syncing ? `Синхронизирую ${Math.round((syncJob!.progress || 0) * 100)}%` : sync?.groups.length ? "Пересинхронизировать" : "Синхронизировать ракурсы"}
          </button>
        </div>
        {pending > 0 && (
          <button className="btn btn-primary w-full justify-center" disabled={busy} onClick={ingest}
            title="Прокси 540p, аудио 16 кГц, волна и локальная расшифровка для всех файлов. Кэш не пересчитывается.">
            Подготовить {pending} {pending === 1 ? "файл" : "файлов"}: прокси и расшифровка
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-2 flex flex-col gap-3">
        {byTakes && sync && <TakesView groups={sync.groups} sources={sources} jobs={jobs} filter={filter} />}
        {!byTakes && groups.length === 0 && <p className="text-dim text-[12.5px] p-3">В папке нет видео или аудио. Перетащите файлы сюда или добавьте их в папку проекта.</p>}
        {!byTakes && groups.map(([g, items]) => (
          <section key={g}>
            <h3 className="label px-1 mb-1.5">{g} <span className="text-faint normal-case tracking-normal font-normal">· {items.length}</span></h3>
            <div className="flex flex-col gap-1">
              {items.map((s) => <SourceRow key={s.id} s={s} jobs={jobs} />)}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

const STEP_LABEL: Record<string, string> = { proxy: "прокси", audio: "аудио", peaks: "волна", asr: "текст", thumb: "кадр" };

function SourceRow({ s, jobs }: { s: Source; jobs: Job[] }) {
  const selected = useStore((st) => st.selected === s.id);
  const multi = useStore((st) => st.selSources.includes(s.id));
  const select = useStore((st) => st.select);
  const mine = jobs.filter((j) => j.source === s.id);
  const latest = (k: string) => mine.filter((j) => j.kind === k).sort((a, b) => b.id - a.id)[0];
  const steps = (s.kind === "video" ? ["proxy", "audio", "asr"] : ["audio", "asr"]).filter((k) => k !== "audio" || s.probe?.audio.length)
    .filter((k) => k !== "asr" || s.probe?.audio.length);
  const v = s.probe?.video;
  const running = mine.find((j) => j.status === "running");

  return (
    <button
      draggable={!s.duplicate_of && s.kind === "video"}
      onDragStart={(e) => {
        const st = useStore.getState();
        const ids = st.selSources.includes(s.id) ? st.selSources : [s.id];
        e.dataTransfer.setData("application/x-cutroom-sources", JSON.stringify(ids));
        e.dataTransfer.effectAllowed = "copy";
      }}
      onClick={(e) => {
        const st = useStore.getState();
        if (e.ctrlKey || e.metaKey) {
          const cur = st.selSources.length ? st.selSources : st.selected ? [st.selected] : [];
          st.set({ selSources: cur.includes(s.id) ? cur.filter((x) => x !== s.id) : [...cur, s.id] });
          return;
        }
        if (e.shiftKey && st.selected) {
          const order = st.sources.filter((x) => !x.duplicate_of).map((x) => x.id);
          const [a, b] = [order.indexOf(st.selected), order.indexOf(s.id)].sort((x, y) => x - y);
          st.set({ selSources: order.slice(a, b + 1) });
          return;
        }
        st.set({ selSources: [] });
        select(s.id);
      }}
      title={s.missing ? "Файл не найден на диске: верните его в папку проекта" : "Перетащите на таймлайн, чтобы вставить в монтаж. Ctrl/Shift+клик — несколько файлов"}
      className={cls("group text-left flex gap-2 p-1.5 rounded-[8px] border",
        selected || multi ? "border-accent/70 bg-accent-soft" : "border-transparent hover:bg-panel-3",
        (s.duplicate_of || s.excluded) && "opacity-55")}
      aria-current={selected}
    >
      <div className="w-[76px] h-[43px] shrink-0 rounded-[4px] bg-panel-3 overflow-hidden grid place-items-center">
        {s.has.thumb ? <img src={mediaUrl("thumb", s.id)} alt="" className="w-full h-full object-cover" loading="lazy" />
          : <Icon name={s.kind === "audio" ? "wave" : "film"} size={16} className="text-faint" />}
      </div>
      <div className="min-w-0 flex-1 flex flex-col gap-0.5">
        <div className="flex items-center gap-1.5">
          <span className="truncate font-medium text-[12.5px]">{s.name}</span>
          <span className="flex-1" />
          <span className="tnum text-[11.5px] text-dim">{fmtDur(s.probe?.duration ?? 0)}</span>
        </div>
        <div className="flex items-center gap-1 flex-wrap">
          {v && <span className="badge tnum">{v.fps ? `${Math.round(v.fps * 100) / 100}p` : ""} {v.codec}</span>}
          {v?.hdr && <span className="badge badge-warn" title="HLG/PQ: прокси тонмаплен в Rec.709">HDR</span>}
          {s.duplicate_of && <span className="badge" title={`Байт-в-байт совпадает с ${s.duplicate_of}: не обрабатывается повторно`}>копия {s.duplicate_of}</span>}
          {s.probe?.error && <span className="badge badge-err" title={s.probe.error}>не читается</span>}
          {s.missing && <span className="badge badge-err">файл не найден</span>}
          {!s.duplicate_of && steps.map((k) => {
            const j = latest(k === "audio" ? "audio" : k);
            const done = k === "proxy" ? s.has.proxy : k === "audio" ? s.has.audio : s.has.transcript;
            const st = done ? "done" : j?.status;
            return (
              <span key={k} className={cls("badge", st === "done" || st === "cached" ? "badge-ok" : st === "error" || st === "blocked" ? "badge-err" : st === "running" ? "badge-accent" : "")}
                title={j?.error ?? undefined}>
                {STEP_LABEL[k]}{st === "running" && j ? ` ${Math.round(j.progress * 100)}%` : ""}
              </span>
            );
          })}
          {running?.eta != null && <span className="text-[11px] text-faint tnum">~{fmtEta(running.eta)}</span>}
        </div>
      </div>
    </button>
  );
}

function TakesView({ groups, sources, jobs, filter }: { groups: SyncGroup[]; sources: Source[]; jobs: Job[]; filter: string }) {
  const byId = new Map(sources.map((s) => [s.id, s]));
  const match = (id: string) => !filter || (byId.get(id)?.name ?? id).toLowerCase().includes(filter.toLowerCase());
  const nudge = async (member: string, body: Record<string, unknown>) => {
    const sel = useStore.getState().selected;
    const before = player.el?.currentTime ?? 0;
    try {
      useStore.setState({ sync: await api.post("/api/sync/nudge", { member, ...body }) });
      if (sel) useStore.getState().set({ time: before });
    } catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const loose = sources.filter((s) => !s.duplicate_of && !groups.some((g) => g.members.some((m) => m.id === s.id)) && match(s.id));
  return (
    <>
      {groups.map((g) => {
        const ms = g.members.filter((m) => match(m.id));
        if (!ms.length) return null;
        const checks = g.members.filter((m) => m.status === "check").length;
        return (
          <section key={g.id}>
            <h3 className="label px-1 mb-1.5 flex items-center gap-2">
              Дубль {g.id.replace("take_", "")}
              <span className="normal-case tracking-normal font-normal text-faint">· {g.members.length} ракурса · опорный {g.ref}</span>
              {checks > 0 && <span className="badge badge-warn normal-case tracking-normal">{checks} проверить</span>}
            </h3>
            <div className="flex flex-col gap-1">
              {ms.map((m) => {
                const s = byId.get(m.id);
                if (!s) return null;
                return (
                  <div key={m.id}>
                    <SourceRow s={s} jobs={jobs} />
                    <div className="flex items-center gap-1 pl-[86px] pb-1 text-[11.5px] tnum whitespace-nowrap">
                      {m.status === "ref" ? <span className="badge badge-accent">опорный</span> : <>
                        <span className={cls("badge", m.status === "ok" ? "badge-ok" : m.status === "manual" ? "" : "badge-warn")}
                          title={m.sharpness ? `Острота пика GCC-PHAT ${m.sharpness} (шум ≈ 6)` : undefined}>
                          {m.status === "ok" ? "синхр." : m.status === "manual" ? "вручную" : "проверить"}{m.status === "check" ? ` ${Math.round(m.confidence * 100)}%` : ""}
                        </span>
                        <span className="text-dim" title="Смещение относительно опорного ракурса">{m.offset >= 0 ? "+" : ""}{m.offset.toFixed(2)}{m.nudge_frames ? ` ${m.nudge_frames > 0 ? "+" : ""}${m.nudge_frames} к` : ""}</span>
                        <span className="flex-1" />
                        <button className="btn btn-ghost btn-sm h-5 px-1.5" title="Сдвинуть на кадр раньше" aria-label="Кадр раньше" onClick={() => nudge(m.id, { delta: -1 })}>−1</button>
                        <button className="btn btn-ghost btn-sm h-5 px-1.5" title="Сдвинуть на кадр позже" aria-label="Кадр позже" onClick={() => nudge(m.id, { delta: 1 })}>+1</button>
                        <button className="btn btn-ghost btn-sm h-5 px-1.5" title="Сделать опорным ракурсом дубля" onClick={() => nudge(m.id, { make_ref: true })}>опорн.</button>
                      </>}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
      {loose.length > 0 && (
        <section>
          <h3 className="label px-1 mb-1.5">Без пары <span className="normal-case tracking-normal font-normal text-faint">· {loose.length} · одиночные кадры или звук не совпал</span></h3>
          <div className="flex flex-col gap-1">{loose.map((s) => <SourceRow key={s.id} s={s} jobs={jobs} />)}</div>
        </section>
      )}
    </>
  );
}

