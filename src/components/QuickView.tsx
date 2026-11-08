import { useEffect, useRef, useState } from "react";
import { api, backendUrl, mediaUrl } from "../api";
import { useStore } from "../store";
import { end, fpsOf, remove, split, type Block, type VBlock } from "../quickOps";
import { qPause, qSeek, qToggle, roleOf, ROLE_NAMES, selRange, shownCover, shownDoc, useQuick, fmtT, type QuickPayload } from "../quickStore";
import { QuickTimeline } from "./QuickTimeline";
import { QuickSteps } from "./QuickSteps";
import { QuickDialogs } from "./QuickDialogs";
import { cls } from "../util";

export const quickUrl = (path: string) => backendUrl(path);

/** Quick mode: wizard steps on the left, Front/Top monitors on top, the block timeline, the AI window. */
export function QuickView() {
  const q = useQuick();
  const jobs = useStore((s) => s.jobs);
  const prevJobs = useRef(new Map<number, string>());

  useEffect(() => {
    q.load().catch((e) => useStore.getState().say("err", (e as Error).message));
    let t: number | undefined;
    const onDirty = () => { clearTimeout(t); t = window.setTimeout(() => useQuick.getState().load().catch(() => {}), 150); };
    window.addEventListener("cutroom-dirty", onDirty);
    return () => window.removeEventListener("cutroom-dirty", onDirty);
  }, []);

  // AI jobs that end produce a proposal (or an error worth showing)
  useEffect(() => {
    for (const j of jobs) {
      const was = prevJobs.current.get(j.id);
      if (was && was !== j.status && (j.kind === "qcut" || j.kind === "qai")) {
        if (j.status === "done") useQuick.getState().load();
        if (j.status === "error") useStore.getState().say("err", `ИИ: ${j.error ?? "ошибка"}`);
      }
      prevJobs.current.set(j.id, j.status);
    }
  }, [jobs]);

  // keyboard: S split, Delete / Shift+Delete, Ctrl+Z / Ctrl+Shift+Z, Space, ←/→, Esc
  useEffect(() => {
    const run = async (cmd: string) => {
      const s = useQuick.getState();
      const d = shownDoc(s);
      if (!d || s.propDoc) return;
      const fps = fpsOf(d);
      if (cmd === "undo" || cmd === "redo") {
        try { s.set({ data: await api.post<QuickPayload>(`/api/quick/${cmd}`) }); } catch (e) { useStore.getState().say("info", (e as Error).message); }
        return;
      }
      if (!s.data?.gate_open) return;
      if (cmd === "split") {
        const f = Math.round(s.time * fps);
        const nd = split(d, s.sel, f);
        if (JSON.stringify(nd.tracks) !== JSON.stringify(d.tracks)) await s.commit(nd, `разрез на ${fmtT(s.time)}`);
      } else if ((cmd === "delete" || cmd === "ripple") && s.sel.length) {
        await s.commit(remove(d, s.sel, cmd === "ripple"), cmd === "ripple" ? "удаление со сдвигом" : "удаление");
        s.set({ sel: [] });
      }
    };
    const onCmd = (e: Event) => run((e as CustomEvent).detail);
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      const s = useQuick.getState();
      const d = shownDoc(s);
      const fps = d ? fpsOf(d) : 25;
      if (e.ctrlKey && e.key.toLowerCase() === "z") { e.preventDefault(); run(e.shiftKey ? "redo" : "undo"); return; }
      switch (e.key) {
        case " ": e.preventDefault(); qToggle(); break;
        case "s": case "S": case "ы": case "Ы": run("split"); break;
        case "Delete": case "Backspace": run(e.shiftKey ? "ripple" : "delete"); break;
        case "ArrowLeft": e.preventDefault(); qSeek(s.time - (e.shiftKey ? 10 : 1) / fps); break;
        case "ArrowRight": e.preventDefault(); qSeek(s.time + (e.shiftKey ? 10 : 1) / fps); break;
        case "Home": qSeek(0); break;
        case "Escape": s.set({ sel: [], dialog: null }); break;
      }
    };
    window.addEventListener("quick-cmd", onCmd);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("quick-cmd", onCmd); window.removeEventListener("keydown", onKey); qPause(); };
  }, []);

  return (
    <div className="flex-1 min-h-0 flex">
      <div className="w-[300px] shrink-0 bg-panel border-r border-line flex flex-col min-h-0 max-[900px]:hidden">
        <QuickSteps />
      </div>
      <div className="flex-1 min-w-0 flex flex-col min-h-0">
        <Monitors />
        <div className="h-[292px] shrink-0 bg-panel border-t border-line min-h-0">
          <QuickTimeline />
        </div>
        <AiBar />
      </div>
      <QuickDialogs />
    </div>
  );
}

// ---------------------------------------------------------------- monitors

function blockAt<T extends Block>(bs: T[], f: number): T | undefined {
  return bs.find((b) => f >= b.start && f < end(b));
}

function Monitors() {
  const q = useQuick();
  const d = shownDoc(q);
  const cov = shownCover(q);
  const fps = d ? fpsOf(d) : 25;
  const f = Math.round(q.time * fps);
  const v = d ? blockAt(d.tracks.video, f) : undefined;
  const active = d && v ? (roleOf(d, v.src) === "top" ? "top" : roleOf(d, v.src) === "side" ? "side" : "front") : "front";
  const hasSide = !!d && (d.tracks.video.some((x) => roleOf(d, x.src) === "side") || Object.values(cov).some((c) => c.side));
  const panes: ("front" | "top" | "side")[] = hasSide ? ["front", "top", "side"] : ["front", "top"];
  const switchMode = async () => {
    try {
      await api.post("/api/quick/switch", { to: "full" });
      qPause();
      useStore.getState().set({ appMode: "full" });
      await useStore.getState().refreshProject();
    } catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const draft = q.data?.build.draft;

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-bg">
      <div className="flex-1 min-h-0 flex items-stretch gap-2 p-2">
        {q.assembled && draft ? (
          <Assembled url={quickUrl(`/media/quick/preview?v=${draft.version}`)} />
        ) : panes.map((role, i) => (
          <div key={role} className="contents">
            <Pane role={role} active={active === role} v={v} f={f} fps={fps} />
            {i === 0 && (
              <div className="w-6 shrink-0 flex flex-col items-center justify-center gap-2">
                <button onClick={switchMode} title="Перейти в полный режим (проект сохранится)" aria-label="Переключить режим"
                  className="w-4 h-4 rounded-full border-2 border-accent bg-accent/30 hover:bg-accent transition-colors" />
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="h-9 shrink-0 flex items-center gap-2 px-3 border-t border-line bg-panel text-[12px]">
        <button className="btn btn-sm w-16 justify-center" onClick={qToggle}>{q.playing ? "Пауза" : "Играть"}</button>
        <span className="tabular-nums text-dim">{fmtT(q.time)}</span>
        <span className="text-dim truncate">
          {q.propDoc ? "Показываю предложение ИИ — ещё не применено" : v ? `Ракурс: ${ROLE_NAMES[active]}` : ""}
        </span>
        <span className="flex-1" />
        <BuildBadge />
        <button className="chip" aria-pressed={q.assembled} disabled={!draft} onClick={() => q.set({ assembled: !q.assembled })}
          title="Собранный из блоков файл (обновляется после каждой правки, только изменённые блоки)">Собранное превью</button>
      </div>
    </div>
  );
}

function Pane({ role, active, v, f, fps }: { role: "front" | "top" | "side"; active: boolean; v?: VBlock; f: number; fps: number }) {
  const q = useQuick();
  const d = shownDoc(q);
  const cov = shownCover(q);
  const vid = useRef<HTMLVideoElement>(null);
  // which file and second this pane shows right now
  let src: string | null = null, at = 0;
  if (d && v) {
    const own = roleOf(d, v.src);
    const c = (cov[v.id] || {})[role];   // includes the block's own picture for its role
    if (c) { src = c.src; at = c.in + (f - v.start) / fps; }
    else if (role === "front" && own !== "top" && own !== "side") { src = v.src; at = v.in + (f - v.start) / fps; }
  }
  useEffect(() => {
    const el = vid.current;
    if (!el || !src) return;
    if (Math.abs(el.currentTime - at) > (q.playing ? 0.25 : 0.04)) el.currentTime = Math.max(0, at);
    if (q.playing && el.paused) el.play().catch(() => {});
    if (!q.playing && !el.paused) el.pause();
  });
  const slide = d ? blockAt(d.tracks.slides, f) : undefined;
  const card = d ? blockAt(d.tracks.overlays, f) : undefined;
  return (
    <div className={cls("flex-1 min-w-0 self-center aspect-video max-h-full relative rounded-[8px] overflow-hidden bg-black border-2", active ? "border-accent" : "border-transparent opacity-80")}>
      <div className={cls("absolute top-1.5 left-2 z-10 text-[11px] px-1.5 rounded", active ? "bg-accent text-[var(--accent-fg)]" : "bg-black/60 text-white/80")}>
        {ROLE_NAMES[role]}{active ? " · в кадре" : ""}
      </div>
      {src ? (
        <video ref={vid} key={src} src={mediaUrl("proxy", src)} muted preload="auto" className="w-full h-full object-contain" />
      ) : (
        <div className="absolute inset-0 grid place-items-center text-white/40 text-[12px]">{v ? "нет синхронного кадра" : "—"}</div>
      )}
      {active && slide && (
        <img src={quickUrl(`/media/quick/slide/${slide.page}`)} alt=""
          className={cls("absolute pointer-events-none", slide.layout === "full" ? "inset-0 w-full h-full object-contain bg-[#FFF9EB]" : "right-[3.6%] top-1/2 -translate-y-1/2 w-[46%] rounded-[6px] shadow-lg")} />
      )}
      {active && card?.status === "ready" && card.files && <CardOverlay id={card.id} v={card.files.v} offset={(f - card.start) / fps} />}
      {active && <Sound f={f} fps={fps} />}
    </div>
  );
}

function CardOverlay({ id, v, offset }: { id: string; v: number; offset: number }) {
  const el = useRef<HTMLVideoElement>(null);
  const playing = useQuick((s) => s.playing);
  useEffect(() => {
    const x = el.current;
    if (!x) return;
    if (Math.abs(x.currentTime - offset) > (playing ? 0.3 : 0.04)) x.currentTime = Math.max(0, offset);
    if (playing && x.paused) x.play().catch(() => {});
    if (!playing && !x.paused) x.pause();
  });
  return <video ref={el} key={`${id}${v}`} src={quickUrl(`/media/quick/card/${id}/preview?v=${v}`)} muted className="absolute inset-0 w-full h-full object-contain pointer-events-none" />;
}

/** The speech under the playhead: the sound block's source (lav proxy or camera) at its own time. */
function Sound({ f, fps }: { f: number; fps: number }) {
  const q = useQuick();
  const d = shownDoc(q);
  const a = d ? blockAt(d.tracks.audio, f) : undefined;
  const el = useRef<HTMLVideoElement>(null);
  const at = a ? a.in + (f - a.start) / fps : 0;
  useEffect(() => {
    const x = el.current;
    if (!x || !a) return;
    if (Math.abs(x.currentTime - at) > (q.playing ? 0.2 : 0.04)) x.currentTime = Math.max(0, at);
    if (q.playing && x.paused) x.play().catch(() => {});
    if (!q.playing && !x.paused) x.pause();
  });
  if (!a) return null;
  return <video ref={el} key={a.src} src={mediaUrl("proxy", a.src)} preload="auto" className="hidden" />;
}

function Assembled({ url }: { url: string }) {
  const q = useQuick();
  const el = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const x = el.current;
    if (!x) return;
    if (Math.abs(x.currentTime - q.time) > (q.playing ? 0.3 : 0.04)) x.currentTime = q.time;
    if (q.playing && x.paused) x.play().catch(() => {});
    if (!q.playing && !x.paused) x.pause();
  });
  return (
    <div className="flex-1 self-center aspect-video max-h-full relative rounded-[8px] overflow-hidden bg-black border-2 border-accent">
      <div className="absolute top-1.5 left-2 z-10 text-[11px] px-1.5 rounded bg-accent text-[var(--accent-fg)]">Собранное превью</div>
      <video ref={el} key={url} src={url} className="w-full h-full object-contain" />
    </div>
  );
}

function BuildBadge() {
  const q = useQuick();
  const jobs = useStore((s) => s.jobs);
  const busy = jobs.filter((j) => ["qblock", "qassemble", "qplan"].includes(j.kind) && (j.status === "running" || j.status === "queued"));
  const b = q.data?.build.draft;
  if (busy.length) {
    const blocks = busy.filter((j) => j.kind === "qblock").length;
    return <span className="text-accent pulse">{blocks ? `Пересобираю блоков: ${blocks}` : "Склеиваю из кэша…"}</span>;
  }
  if (!b) return null;
  return (
    <span className="text-dim" title={`Пересобраны: ${b.rebuilt.join(", ") || "—"}\nИз кэша: ${b.cached.length}\nСклейка ${b.t_concat} с, звук ${b.t_audio} с`}>
      v{b.version}: пересобрано {b.rebuilt.length}, из кэша {b.cached.length} · {b.t_total} с
    </span>
  );
}

// ---------------------------------------------------------------- AI requests window

function AiBar() {
  const q = useQuick();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const jobs = useStore((s) => s.jobs);
  const thinking = jobs.some((j) => (j.kind === "qai" || j.kind === "qcut") && (j.status === "running" || j.status === "queued"));
  const d = q.data?.doc;
  const fps = d ? fpsOf(d) : 25;
  const r = selRange();
  const prop = q.data?.proposal;
  const send = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      const sel = r ? { ids: q.sel.map((s) => s.id), start: r[0], end: r[1] } : null;
      const res = await api.post<{ proposal: unknown; job: unknown }>("/api/quick/ai", { prompt: text, selection: sel });
      if (res.proposal) await useQuick.getState().load();
      setText("");
    } catch (e) { useStore.getState().say("err", (e as Error).message); }
    setBusy(false);
  };
  const apply = async () => {
    try { q.set({ data: await api.post<QuickPayload>("/api/quick/proposal/apply"), propDoc: null, sel: [] }); }
    catch (e) { useStore.getState().say("err", (e as Error).message); await q.load(); }
  };
  const cancel = async () => { q.set({ data: await api.post<QuickPayload>("/api/quick/proposal/cancel"), propDoc: null }); };
  return (
    <div className="shrink-0 border-t border-line bg-panel">
      {prop && (
        <div className="px-3 py-2 border-b border-line bg-[var(--accent-soft)] flex items-start gap-3">
          <div className="flex-1 min-w-0 text-[12.5px]">
            <div className="text-fg">
              <span className="text-accent font-medium">{prop.kind === "cut" ? "Нарезка по промпту" : prop.fast ? "Предложение (понято по таймкодам, мгновенно)" : "ИИ предлагает"}:</span> {prop.summary}
            </div>
            {prop.warnings?.length > 0 && <div className="text-warn mt-0.5">{prop.warnings.join(" · ")}</div>}
            <div className="text-dim mt-0.5">Изменённые блоки обведены пунктиром; мониторы показывают результат. {prop.kind === "cut" ? "«Применить» — это и утверждение стратегии." : ""}</div>
          </div>
          <button className="btn btn-primary btn-sm" onClick={apply}>Применить</button>
          <button className="btn btn-sm" onClick={cancel}>Отмена</button>
        </div>
      )}
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="text-[11.5px] text-dim shrink-0 w-[150px] truncate" title="Запрос применяется к выделенному на таймлайне; без выделения — ко всему ролику">
          {r ? `Выделение ${fmtT(r[0] / fps)}–${fmtT(r[1] / fps)}` : "Весь ролик"}
        </span>
        <input className="input flex-1" value={text} onChange={(e) => setText(e.target.value)} disabled={!q.data?.gate_open || busy}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder={q.data?.gate_open ? "Запрос к ИИ: «с 01:12 до 01:40 только верх», «сделай теплее», «карточка по слайду 2», «подложка потише»…" : "Окно запросов откроется после нарезки (шаг 2)"} />
        <button className="btn btn-primary btn-sm" onClick={send} disabled={!text.trim() || busy || !q.data?.gate_open}>
          {thinking ? "ИИ думает…" : "Отправить"}
        </button>
      </div>
    </div>
  );
}
