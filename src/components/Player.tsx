import { useEffect, useRef, useState } from "react";
import { gfxUrl, mediaUrl, previewUrl, type Source } from "../api";
import { Icon } from "../icons";
import { currentDuration, groupOf, mapTime, player, seek, stepFrames, togglePlay, useStore } from "../store";
import { programPause, programRefresh, registerProgramEls } from "../program";
import { AngleStrip } from "./AngleStrip";
import { CompareView } from "./CompareView";
import { cssFilter } from "./ColorPanel";
import { cls, fmtTimecode } from "../util";

type Mode = "source" | "angles" | "compare";

export function Player() {
  const sel = useStore((s) => s.selected);
  const source = useStore((s) => s.sources.find((x) => x.id === s.selected));
  const playing = useStore((s) => s.playing);
  const monitor = useStore((s) => s.monitor);
  const edlState = useStore((s) => s.edl);
  const hasEdl = !!edlState?.edl;
  const [mode, setMode] = useState<Mode>("source");
  const video = useRef<HTMLVideoElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const program = monitor === "program" && hasEdl;

  useEffect(() => {
    player.el = video.current;
    return () => { if (player.el === video.current) player.el = null; };
  });

  // publish currentTime to the store at ~30 Hz while the source monitor plays
  useEffect(() => {
    let raf = 0, last = 0;
    const tick = (ts: number) => {
      const el = video.current;
      if (el && ts - last > 33) {
        last = ts;
        const s = useStore.getState();
        if (Math.abs(s.time - el.currentTime) > 0.01) s.set({ time: el.currentTime });
      }
      raf = requestAnimationFrame(tick);
    };
    if (playing && !program) raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, program]);

  useEffect(() => setErr(null), [sel]);

  const hasProxy = source?.has.proxy;
  const fps = source?.probe?.video?.fps || 25;
  const tabs: [string, string][] = [["program", "Итог"], ["source", "Исходник"], ["angles", "Ракурсы"], ["compare", "До / после"]];
  const compare = useStore((s) => s.compare);
  const activeTab = program ? (compare ? "compare" : "program") : mode;
  const pick = (m: string) => {
    if (m === "compare") {
      if (!hasEdl) return useStore.getState().say("info", "«До / после» сравнивает цвет на монтаже: сначала соберите черновик.");
      player.el?.pause();
      useStore.getState().set({ monitor: "program", compare: true });
      return;
    }
    if (m === "program") {
      if (!hasEdl) return useStore.getState().say("info", "Итог появится после утверждения стратегии и черновой сборки.");
      player.el?.pause();
      useStore.getState().set({ monitor: "program", compare: false, ...(program ? {} : { time: 0 }) });
    } else {
      programPause();
      useStore.getState().set({ monitor: "source", compare: false, time: video.current?.currentTime ?? 0 });
      setMode(m as Mode);
    }
  };

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-bg">
      <div className="h-9 shrink-0 flex items-center gap-2 px-3 border-b border-line bg-panel">
        <div className="flex rounded-[6px] border border-line-strong overflow-hidden" role="tablist" aria-label="Режим плеера">
          {tabs.map(([m, l]) => (
            <button key={m} role="tab" aria-selected={activeTab === m} onClick={() => pick(m)}
              className={cls("h-6 px-2.5 text-[12px]", activeTab === m ? "bg-panel-3 text-fg" : "text-dim hover:text-fg", m === "program" && !hasEdl && "opacity-50")}>{l}</button>
          ))}
        </div>
        <span className="text-[12px] text-dim truncate">
          {program ? <>Живое превью EDL v{edlState!.edl!.cutroom.version_id} · {edlState!.edl!.ranges.length} фрагментов · без рендера</>
            : source ? <>Источник <span className="text-fg">{source.name}</span></> : "Нет выбранного источника"}
        </span>
      </div>

      <div className="flex-1 min-h-0 relative grid place-items-center overflow-hidden">
        {program && <ProgramMonitor />}
        {program && compare && <CompareView />}
        {!program && mode === "angles" ? <AnglesGrid main={source} /> : !program && mode === "compare" ? (
          <p className="text-dim text-[12.5px] max-w-[360px] text-center">Сравнение до/после цветокора появится на этапе 7: штора по кадру из выбранного сегмента.</p>
        ) : null}
        <video
          ref={video}
          key={sel ?? "none"}
          src={hasProxy && sel ? mediaUrl("proxy", sel) : undefined}
          className={cls("max-w-full max-h-full", (program || mode !== "source") && "hidden")}
          preload="auto"
          onPlay={() => useStore.getState().set({ playing: true })}
          onPause={() => { if (useStore.getState().monitor === "source") useStore.getState().set({ playing: false, time: video.current?.currentTime ?? 0 }); }}
          onSeeked={() => { if (useStore.getState().monitor === "source") useStore.getState().set({ time: video.current?.currentTime ?? 0 }); }}
          onError={() => setErr("Прокси не воспроизводится. Пересоздайте его: очередь → «Повторить».")}
          onClick={togglePlay}
        />
        {!program && mode === "source" && source && !hasProxy && (
          <div className="absolute inset-0 grid place-items-center text-dim text-[12.5px]">
            {source.kind === "audio" ? "Аудиофайл: смотрите волну на таймлайне." : "Прокси ещё готовится — он нужен для мгновенной перемотки."}
          </div>
        )}
        {err && !program && <div className="absolute bottom-3 left-3 right-3 text-[12px] text-err bg-panel border border-line rounded-[6px] px-3 py-2">{err}</div>}
      </div>

      {program && <AngleStrip />}
      <div className="h-10 shrink-0 flex items-center gap-1 px-2 border-t border-line bg-panel">
        <button className="btn btn-ghost btn-sm" onClick={() => stepFrames(-1)} aria-label="Кадр назад" title="Кадр назад (←)">‹</button>
        <button className="btn btn-sm w-9 justify-center" onClick={togglePlay} aria-label={playing ? "Пауза" : "Играть"} title="Пробел">
          <Icon name={playing ? "pause" : "play"} size={14} />
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => stepFrames(1)} aria-label="Кадр вперёд" title="Кадр вперёд (→)">›</button>
        <Readout fps={fps} />
        <span className="flex-1" />
        <span className="text-[11px] text-faint hidden md:inline">J K L — шаттл · ←/→ кадр · Shift — 10 кадров</span>
      </div>
    </div>
  );
}

// Live program: two leapfrogging proxy players driven by program.ts; optional verified render.
function ProgramMonitor() {
  const a = useRef<HTMLVideoElement>(null);
  const b = useRef<HTMLVideoElement>(null);
  const edlState = useStore((s) => s.edl);
  const jobs = useStore((s) => s.jobs);
  const [rendered, setRendered] = useState(false);
  const version = edlState?.edl?.cutroom.version_id;
  const pv = edlState?.preview;
  const fresh = pv?.exists && pv.version === version;
  const job = jobs.filter((j) => j.kind === "preview").sort((x, y) => y.id - x.id)[0];
  const building = job && (job.status === "running" || job.status === "queued");

  useEffect(() => {
    registerProgramEls(a.current, b.current);
    programRefresh();
    return () => { programPause(); registerProgramEls(null, null); };
  }, []);
  // EDL changed (trim, split, undo): keep the playhead and reload what's under it
  useEffect(() => { if (!rendered) programRefresh(); }, [version, rendered]);
  // approximate colour of the episode under the playhead (exact grade happens in the render)
  useEffect(() => useStore.subscribe((st) => {
    const e = st.edl?.edl;
    if (!e) return;
    const r = e.ranges.find((x) => st.time >= x.out_start && st.time < x.out_start + (x.end - x.start));
    const f = cssFilter(r?.color ?? e.color ?? null);
    for (const el of [a.current, b.current]) if (el && el.style.filter !== f) el.style.filter = f;
  }), []);

  return (
    <>
      <video ref={a} className={cls("absolute inset-0 w-full h-full object-contain", rendered && "hidden")} preload="auto" onClick={togglePlay} />
      <video ref={b} className={cls("absolute inset-0 w-full h-full object-contain", rendered && "hidden")} style={{ visibility: "hidden" }} preload="auto" onClick={togglePlay} />
      {!rendered && <GfxOverlay />}
      {rendered && pv?.exists && <video src={previewUrl(pv.version ?? 0)} controls className="absolute inset-0 w-full h-full object-contain bg-bg" />}
      <div className="absolute right-2 top-2 flex items-center gap-1.5">
        {building && <span className="badge badge-accent tnum" title={job!.label}>рендер {Math.round(job!.progress * 100)}%</span>}
        {pv?.exists && (
          <button className={cls("btn btn-sm", rendered && "border-accent text-accent")} onClick={() => { programPause(); setRendered(!rendered); }}
            title={fresh ? "render.py --preview по текущей версии EDL" : `Рендер по версии ${pv.version}, EDL уже v${version}`}>
            {rendered ? "Живое превью" : `Проверенный рендер${fresh ? "" : " (устарел)"}`}
          </button>
        )}
      </div>
    </>
  );
}

// Ready infographic card over the live program (VP9 alpha preview; the render overlays the exact PNG-alpha file).
function GfxOverlay() {
  const slots = useStore((s) => s.edl?.edl?.slots);
  const time = useStore((s) => s.time);
  const playing = useStore((s) => s.playing);
  const ref = useRef<HTMLVideoElement>(null);
  const s = slots?.find((x) => x.status === "ready" && x.files && time >= x.start_in_output && time < x.start_in_output + x.duration);
  const t = s ? time - s.start_in_output : 0;
  useEffect(() => {
    const el = ref.current;
    if (!el || !s) return;
    if (Math.abs(el.currentTime - t) > (playing ? 0.2 : 0.02)) el.currentTime = Math.max(0, t);
  }, [t, playing, s?.id]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (playing && s) el.play().catch(() => {}); else el.pause();
  }, [playing, s?.id]);
  if (!s) return null;
  return <video key={`${s.id}:${s.files!.v}`} ref={ref} src={gfxUrl(s.id, "preview", s.files!.v)} muted preload="auto"
    className="absolute inset-0 w-full h-full object-contain pointer-events-none" />;
}

function Readout({ fps }: { fps: number }) {
  const t = useStore((s) => s.time);
  useStore((s) => s.monitor);
  useStore((s) => s.edl);
  const dur = currentDuration();
  return (
    <span className="mono tnum text-[12.5px] ml-2">
      <span className="text-fg">{fmtTimecode(t, fps)}</span>
      <span className="text-faint"> / {fmtTimecode(dur, fps)}</span>
      <span className="text-faint ml-2 text-[11px]">{Math.round(fps * 100) / 100} к/с</span>
    </span>
  );
}

// Angles of the selected take, each showing the frame of the same moment (audio-sync offsets).
function AnglesGrid({ main }: { main?: Source }) {
  const sources = useStore((s) => s.sources);
  const time = useStore((s) => s.time);
  const playing = useStore((s) => s.playing);
  useStore((s) => s.sync);
  const g = groupOf(main?.id ?? null);
  if (!main) return null;
  if (!g) {
    return <p className="absolute inset-0 grid place-items-center text-dim text-[12.5px] px-6 text-center">
      У этого файла нет найденных ракурсов. Запустите «Синхронизировать ракурсы» в панели источников — пары находятся по звуку.
    </p>;
  }
  const peers = g.members.map((m) => sources.find((s) => s.id === m.id)).filter((s): s is Source => !!s && s.has.proxy);
  const show = peers.slice(0, 4);
  const cols = show.length <= 1 ? 1 : 2;
  return (
    <div className="absolute inset-0 p-2 grid gap-2" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)`, gridAutoRows: "1fr" }}>
      {show.map((p) => {
        const m = g.members.find((x) => x.id === p.id)!;
        const t = mapTime(g, main.id, p.id, time);
        const dur = p.probe?.duration ?? 0;
        const inRange = t >= 0 && t <= dur;
        return (
          <div key={p.id} className={cls("relative rounded-[6px] overflow-hidden bg-panel-2 border min-h-0", p.id === main.id ? "border-accent" : "border-line")}>
            {p.id === main.id ? <MirrorMain /> : <FollowVideo id={p.id} t={t} playing={playing && inRange} />}
            {!inRange && <div className="absolute inset-0 grid place-items-center text-faint text-[12px] bg-panel-2/80">нет кадра в этот момент</div>}
            <span className="absolute left-1.5 top-1.5 badge">{p.name}</span>
            <span className={cls("absolute right-1.5 top-1.5 badge tnum", m.status === "ref" ? "badge-accent" : m.status === "check" ? "badge-warn" : "badge-ok")}>
              {m.status === "ref" ? "опорный" : `${m.offset >= 0 ? "+" : ""}${m.offset.toFixed(2)} с`}
            </span>
            {p.id !== main.id && (
              <button className="absolute inset-0" aria-label={`Переключиться на ${p.name}`} title="Сделать основным, сохранив момент"
                onClick={() => { useStore.getState().select(p.id); setTimeout(() => seek(Math.max(0, t)), 150); }} />
            )}
          </div>
        );
      })}
      {peers.length > 4 && <span className="absolute right-3 bottom-3 badge">ещё {peers.length - 4} ракурса в дубле</span>}
    </div>
  );
}

// The main tile is a muted follower of the hidden main <video>, which keeps the audio and drives the clock.
function MirrorMain() {
  const sel = useStore((s) => s.selected);
  const time = useStore((s) => s.time);
  const playing = useStore((s) => s.playing);
  return sel ? <FollowVideo id={sel} t={time} playing={playing} /> : null;
}

function FollowVideo({ id, t, playing }: { id: string; t: number; playing: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // while playing let it run free and only correct drift; when paused, land exactly
    const tol = playing ? 0.25 : 0.02;
    if (Math.abs(el.currentTime - t) > tol) el.currentTime = Math.max(0, Math.min(t, el.duration || t));
  }, [t, playing]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (playing) el.play().catch(() => {}); else el.pause();
  }, [playing]);
  return <video ref={ref} src={mediaUrl("proxy", id)} muted preload="auto" className="w-full h-full object-contain" />;
}
