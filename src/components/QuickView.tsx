import { useEffect, useRef, useState } from "react";
import { api, backendUrl, mediaUrl } from "../api";
import { useStore } from "../store";
import { copy, end, find, fpsOf, link, paste, patch, remove, spliceFades, split, unlink, type Block, type Clip, type VBlock } from "../quickOps";
import { qPause, qSeek, qToggle, roleOf, ROLE_NAMES, selRange, shownCover, shownDoc, useQuick, fmtT, type QuickPayload } from "../quickStore";
import { QuickTimeline } from "./QuickTimeline";
import { QuickSteps } from "./QuickSteps";
import { QuickDialogs } from "./QuickDialogs";
import { cls } from "../util";
import { cssFilter } from "./ColorPanel";

export const quickUrl = (path: string) => backendUrl(path);

let clipboard: Clip | null = null;   // Ctrl+C / Ctrl+X → Ctrl+V (within this window)
export const SPLICE_FADE = 0.12;     // seconds per side: «Фейды на склейках»

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
      } else if ((cmd === "copy" || cmd === "cut") && s.sel.length) {
        clipboard = copy(d, s.sel);
        if (cmd === "cut") { await s.commit(remove(d, s.sel, false), "вырезать"); s.set({ sel: [] }); }
      } else if (cmd === "paste" && clipboard) {
        const r = paste(d, clipboard, Math.round(s.time * fps));
        await s.commit(r.doc, `вставка на ${fmtT(s.time)}`);
        s.set({ sel: r.refs.filter((x) => x.track !== "audio" || !r.refs.some((y) => y.track === "video")) });
      } else if (cmd === "duplicate" && s.sel.length) {
        const c = copy(d, s.sel);
        if (c) {
          const after = Math.max(...c.blocks.map((x) => end(x.b)));
          const r = paste(d, c, after);
          await s.commit(r.doc, "дублировать");
          s.set({ sel: r.refs.filter((x) => x.track !== "audio" || !r.refs.some((y) => y.track === "video")) });
        }
      } else if (cmd.startsWith("rotate") || cmd.startsWith("flip") || cmd === "reset-transform") {
        const vids = s.sel.filter((r) => r.track === "video");
        if (!vids.length) return;
        const label = { "rotate-cw": "повернуть на 90° вправо", "rotate-ccw": "повернуть на 90° влево", "rotate-180": "повернуть на 180°",
          "flip-h": "отразить по горизонтали", "flip-v": "отразить по вертикали", "reset-transform": "сбросить поворот и отражение" }[cmd] ?? cmd;
        await s.commit(patch(d, vids, (b) => {
          const v = b as VBlock;
          if (cmd === "rotate-cw") v.rotate = ((v.rotate ?? 0) + 90) % 360;
          if (cmd === "rotate-ccw") v.rotate = ((v.rotate ?? 0) + 270) % 360;
          if (cmd === "rotate-180") v.rotate = ((v.rotate ?? 0) + 180) % 360;
          if (cmd === "flip-h") v.flip_h = !v.flip_h;
          if (cmd === "flip-v") v.flip_v = !v.flip_v;
          if (cmd === "reset-transform") { delete v.rotate; delete v.flip_h; delete v.flip_v; }
          if (!v.rotate) delete v.rotate;
        }), `${label} (${vids.length} бл.)`);
      } else if (cmd === "fades" || cmd === "fades-off") {
        const auds = s.sel.flatMap((r) => r.track === "audio" ? [r] : d.tracks.audio.filter((a) => a.link === r.id).map((a) => ({ track: "audio" as const, id: a.id })));
        const nd = spliceFades(d, auds, cmd === "fades" ? SPLICE_FADE : 0);
        if (JSON.stringify(nd.tracks.audio) !== JSON.stringify(d.tracks.audio))
          await s.commit(nd, cmd === "fades" ? (auds.length ? `фейды (${auds.length} бл.)` : "фейды на всех склейках") : "убрать фейды");
      } else if (cmd === "unlink" && s.sel.length) {
        await s.commit(unlink(d, s.sel), "отвязать звук от картинки");
      } else if (cmd === "link") {
        const v = s.sel.find((r) => r.track === "video"), a = s.sel.find((r) => r.track === "audio");
        if (v && a && find(d, v) && find(d, a)) await s.commit(link(d, [v, a]), "связать звук с картинкой");
        else useStore.getState().say("info", "Выделите один блок картинки и один звуковой (Ctrl+клик), потом «Связать».");
      }
    };
    const onCmd = (e: Event) => run((e as CustomEvent).detail);
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      const s = useQuick.getState();
      const d = shownDoc(s);
      const fps = d ? fpsOf(d) : 25;
      if (e.ctrlKey && e.code === "KeyZ") { e.preventDefault(); run(e.shiftKey ? "redo" : "undo"); return; }
      // clip commands, by key position so they work in the Russian layout too
      const ctrl = { KeyC: "copy", KeyX: "cut", KeyV: "paste", KeyD: "duplicate" }[e.code];
      if (e.ctrlKey && ctrl) { e.preventDefault(); run(ctrl); return; }
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

const ROLE_VAR: Record<string, string> = { front: "var(--cam1)", top: "var(--cam2)", side: "var(--cam3)" };
const BUILD_KINDS = ["qblock", "qassemble", "qplan"];

function useBuilding() {
  const jobs = useStore((s) => s.jobs);
  const busy = jobs.filter((j) => BUILD_KINDS.includes(j.kind) && (j.status === "running" || j.status === "queued"));
  const blocks = busy.filter((j) => j.kind === "qblock");
  const done = blocks.length ? blocks.reduce((a, j) => a + (j.status === "running" ? j.progress : 0), 0) / blocks.length : 0;
  return { busy: busy.length > 0, blocks: blocks.length, progress: done };
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
  // a camera switch under the playhead: flash the new monitor with a big «→ Верх»
  const prevActive = useRef(active);
  const [flash, setFlash] = useState<{ to: string; from: string; n: number } | null>(null);
  useEffect(() => {
    if (prevActive.current !== active && v) {
      const from = prevActive.current;
      setFlash((x) => ({ to: active, from, n: (x?.n ?? 0) + 1 }));
      const t = window.setTimeout(() => setFlash(null), 1100);
      prevActive.current = active;
      return () => clearTimeout(t);
    }
    prevActive.current = active;
  }, [active]);
  const switchMode = async () => {
    try {
      await api.post("/api/quick/switch", { to: "full" });
      qPause();
      useStore.getState().set({ appMode: "full" });
      await useStore.getState().refreshProject();
    } catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const draft = q.data?.build.draft;
  // the next camera switch after the playhead
  const next = (() => {
    if (!d || !v) return null;
    for (const b of d.tracks.video) {
      if (b.start <= f) continue;
      const r = roleOf(d, b.src);
      const k = r === "top" || r === "side" ? r : "front";
      if (k !== active) return { role: k, in: (b.start - f) / fps };
    }
    return null;
  })();

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-bg">
      <div className="flex-1 min-h-0 flex items-stretch gap-2 p-2">
        {q.assembled && draft ? (
          <Assembled url={quickUrl(`/media/quick/preview?v=${draft.version}`)} />
        ) : panes.map((role, i) => (
          <div key={role} className="contents">
            <Pane role={role} active={active === role} v={v} f={f} fps={fps} flash={flash && flash.to === role ? flash : null} />
            {i === 0 && (
              <div className="w-6 shrink-0 flex flex-col items-center justify-center gap-2">
                <button onClick={switchMode} title="Перейти в полный режим (проект сохранится)" aria-label="Переключить режим"
                  className="w-4 h-4 rounded-full border-2 border-accent bg-accent/30 hover:bg-accent transition-colors" />
              </div>
            )}
          </div>
        ))}
      </div>
      {d && <ProgramStrip />}
      <div className="h-9 shrink-0 flex items-center gap-2 px-3 border-t border-line bg-panel text-[12px]">
        <button className="btn btn-sm w-16 justify-center" onClick={qToggle}>{q.playing ? "Пауза" : "Играть"}</button>
        <span className="tabular-nums text-dim">{fmtT(q.time)}</span>
        {q.propDoc ? <span className="text-dim truncate">Показываю предложение ИИ — ещё не применено</span> : v ? (
          <span className="flex items-center gap-1.5 min-w-0">
            <span className="px-1.5 rounded text-[11.5px] font-medium text-white" style={{ background: ROLE_VAR[active] }}>{ROLE_NAMES[active]}</span>
            {next && <span className="text-dim truncate">дальше <b style={{ color: ROLE_VAR[next.role] }}>{ROLE_NAMES[next.role]}</b> через {next.in.toFixed(1)} с</span>}
          </span>
        ) : null}
        <span className="flex-1" />
        <BuildBadge />
        <button className="chip" aria-pressed={q.assembled} disabled={!draft} onClick={() => q.set({ assembled: !q.assembled })}
          title="Собранный из блоков файл (обновляется после каждой правки, только изменённые блоки)">Собранное превью</button>
      </div>
    </div>
  );
}

/** The whole film as camera-coloured stripes: where each angle is, where it switches, where the playhead is. */
function ProgramStrip() {
  const q = useQuick();
  const d = shownDoc(q)!;
  const fps = fpsOf(d);
  const n = Math.max(1, ...d.tracks.video.map(end), ...d.tracks.audio.map(end));
  const ref = useRef<HTMLDivElement>(null);
  const seek = (e: React.MouseEvent) => {
    const r = ref.current!.getBoundingClientRect();
    qSeek(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * n / fps);
  };
  return (
    <div className="shrink-0 px-2 pb-1.5">
      <div ref={ref} onMouseDown={seek} title="Ракурсы по всему ролику: клик — перейти" aria-label="Ракурсы по всему ролику"
        className="relative h-3 rounded-sm bg-panel-3 overflow-hidden cursor-pointer">
        {d.tracks.video.map((b, i) => {
          const r = roleOf(d, b.src);
          const k = r === "top" || r === "side" ? r : "front";
          const pr = i ? roleOf(d, d.tracks.video[i - 1].src) : null;
          const pk = pr === "top" || pr === "side" ? pr : pr ? "front" : null;
          return (
            <div key={b.id} className="absolute top-0 bottom-0" title={`${ROLE_NAMES[k]} ${fmtT(b.start / fps)}–${fmtT(end(b) / fps)}`}
              style={{ left: `${(b.start / n) * 100}%`, width: `${(b.len / n) * 100}%`, background: ROLE_VAR[k], opacity: 0.85,
                borderLeft: pk && pk !== k ? "2px solid var(--fg)" : undefined }} />
          );
        })}
        <div className="absolute top-[-2px] bottom-[-2px] w-[2px] bg-fg" style={{ left: `${(q.time * fps / n) * 100}%` }} />
      </div>
    </div>
  );
}

function Pane({ role, active, v, f, fps, flash }: { role: "front" | "top" | "side"; active: boolean; v?: VBlock; f: number; fps: number;
  flash: { from: string; n: number } | null }) {
  const q = useQuick();
  const d = shownDoc(q);
  const cov = shownCover(q);
  const vid = useRef<HTMLVideoElement>(null);
  const building = useBuilding();
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
  // the grade shows at once as a CSS approximation; the exact ffmpeg grade lands in the assembled preview
  const grade = v ? (v.color !== undefined && v.color !== null ? v.color : d?.grade ?? null) : null;
  const filter = cssFilter(grade as never);
  // turn / mirror from the clip menu — on the block's own picture (a turned 16:9 is scaled to fit, like the render)
  const own = !!v && src === v.src;
  const rot = own ? (v!.rotate ?? 0) % 360 : 0;
  const transform = own && (rot || v!.flip_h || v!.flip_v)
    ? `rotate(${rot}deg) scale(${rot % 180 ? 9 / 16 : 1}) scale(${v!.flip_h ? -1 : 1}, ${v!.flip_v ? -1 : 1})` : undefined;
  const slide = d ? blockAt(d.tracks.slides, f) : undefined;
  const card = d ? blockAt(d.tracks.overlays, f) : undefined;
  return (
    <div className={cls("min-w-0 self-center aspect-video max-h-full relative rounded-[8px] overflow-hidden bg-black border-[3px] transition-[flex-grow,opacity] duration-300",
      active ? "" : "border-transparent opacity-70")}
      style={{ flexGrow: active ? 1.7 : 1, flexBasis: 0, borderColor: active ? ROLE_VAR[role] : undefined }}>
      <div className="absolute top-1.5 left-2 z-10 text-[11px] px-1.5 rounded font-medium"
        style={active ? { background: ROLE_VAR[role], color: "#fff" } : { background: "rgb(0 0 0 / 0.6)", color: "rgb(255 255 255 / 0.8)" }}>
        {active ? "● " : ""}{ROLE_NAMES[role]}{active ? " · в кадре" : ""}
      </div>
      {src ? (
        <video ref={vid} key={src} src={mediaUrl("proxy", src)} muted preload="auto" className="w-full h-full object-contain" style={{ filter, transform }} />
      ) : (
        <div className="absolute inset-0 grid place-items-center text-white/40 text-[12px]">{v ? "нет синхронного кадра" : "—"}</div>
      )}
      {active && slide && !(card?.status === "ready" && card.files) && (
        <img src={quickUrl(`/media/quick/slide/${slide.page}`)} alt=""
          className={cls("absolute pointer-events-none", slide.layout === "full" ? "inset-0 w-full h-full object-contain bg-[#FFF9EB]" : "right-[3.6%] top-1/2 -translate-y-1/2 w-[46%] rounded-[6px] shadow-lg")} />
      )}
      {active && card?.status === "ready" && card.files && <CardOverlay id={card.id} v={card.files.v} />}
      {active && <Sound f={f} fps={fps} />}
      {active && <Music f={f} fps={fps} />}
      {flash && (
        <div key={flash.n} className="angle-flash absolute inset-0 z-20 grid place-items-center pointer-events-none">
          <span className="px-3 py-1 rounded-[6px] text-white text-[20px] font-semibold shadow-lg" style={{ background: ROLE_VAR[role] }}>
            {ROLE_NAMES[flash.from] ?? ""} → {ROLE_NAMES[role]}
          </span>
        </div>
      )}
      {active && src && building.busy && (
        <div className="absolute bottom-1.5 right-2 z-10 text-[10.5px] px-1.5 py-0.5 rounded bg-black/70 text-white/90 flex items-center gap-1.5"
          title="Цвет и правки показаны сразу (приближение). Точный рендер блоков идёт в фоне и попадёт в «Собранное превью».">
          <span className="w-2.5 h-2.5 rounded-full border-2 border-white/30 border-t-white animate-spin" />
          {filter ? "цвет: приближение · " : ""}точный рендер{building.blocks ? ` ${building.blocks} бл.` : "…"}
        </div>
      )}
    </div>
  );
}

/** The card in the live monitor is its still poster: a second video decoding on top of the camera froze
 *  playback. The animated card is in «Собранное превью» and the export. */
function CardOverlay({ id, v }: { id: string; v: number }) {
  return <img key={`${id}${v}`} src={quickUrl(`/media/quick/card/${id}/poster?v=${v}`)} alt="" className="absolute inset-0 w-full h-full object-contain pointer-events-none" />;
}

/** The speech under the playhead: the sound block's source (lav proxy or camera) at its own time. */
function Sound({ f, fps }: { f: number; fps: number }) {
  const q = useQuick();
  const d = shownDoc(q);
  const a = d ? blockAt(d.tracks.audio, f) : undefined;
  const el = useRef<HTMLVideoElement>(null);
  const at = a ? a.in + (f - a.start) / fps : 0;
  // the block's fades (and a short ramp at every cut, like the build's anti-click) as volume
  const gain = a ? Math.max(0, Math.min(1, (f - a.start) / fps / Math.max(0.03, a.fade_in ?? 0), (a.start + a.len - f) / fps / Math.max(0.03, a.fade_out ?? 0))) : 1;
  useEffect(() => {
    const x = el.current;
    if (!x || !a) return;
    x.volume = gain;
    if (Math.abs(x.currentTime - at) > (q.playing ? 0.2 : 0.04)) x.currentTime = Math.max(0, at);
    if (q.playing && x.paused) x.play().catch(() => {});
    if (!q.playing && !x.paused) x.pause();
  });
  if (!a) return null;
  return <video ref={el} key={a.src} src={mediaUrl("proxy", a.src)} preload="auto" className="hidden" />;
}

/** The music bed in the live monitor: the chosen track at program time (looped like the build), at the
 *  sound block's bed level. An approximation — the exact ducked mix is in «Собранное превью». */
function Music({ f, fps }: { f: number; fps: number }) {
  const q = useQuick();
  const d = shownDoc(q);
  const el = useRef<HTMLAudioElement>(null);
  const tid = d?.music?.track;
  const a = d ? blockAt(d.tracks.audio, f) : undefined;
  const bed = a ? q.data?.beds?.[a.style ?? "clean"] : null;
  const t = f / fps;
  useEffect(() => {
    const x = el.current;
    if (!x || !tid) return;
    // speech is around -14 LUFS and an unmastered track near -10..-14: the style's dB plus a little more duck
    x.volume = bed == null ? 0 : Math.min(1, 10 ** ((bed - 6) / 20));
    const at = x.duration ? t % x.duration : t;
    if (Math.abs(x.currentTime - at) > (q.playing ? 0.4 : 0.05)) x.currentTime = Math.max(0, at);
    if (q.playing && x.paused && bed != null) x.play().catch(() => {});
    if ((!q.playing || bed == null) && !x.paused) x.pause();
  });
  if (!tid) return null;
  return <audio ref={el} key={tid} src={backendUrl(`/media/music/${tid}`)} preload="auto" className="hidden" />;
}

function Assembled({ url }: { url: string }) {
  const q = useQuick();
  const el = useRef<HTMLVideoElement>(null);
  const building = useBuilding();
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
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
      <video ref={el} key={url} src={url} className={cls("w-full h-full object-contain transition-opacity", building.busy && "opacity-60")}
        onError={() => setFailed(true)} onLoadedData={() => setFailed(false)} />
      {building.busy && (
        <div className="absolute inset-x-0 bottom-0 z-10 bg-black/70 text-white text-[12px] px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full border-2 border-white/30 border-t-white animate-spin" />
            {building.blocks ? `Применяю правки и цвет: пересобираю блоков ${building.blocks}` : "Склеиваю из кэша…"}
            <span className="flex-1" />
            <button className="underline text-white/80 pointer-events-auto" onClick={() => q.set({ assembled: false })}>живой монитор (сразу)</button>
          </div>
          <div className="h-1 mt-1.5 rounded bg-white/20 overflow-hidden">
            <div className="h-full bg-accent transition-[width] duration-300" style={{ width: `${Math.max(6, building.progress * 100)}%` }} />
          </div>
        </div>
      )}
      {failed && !building.busy && (
        <div className="absolute inset-0 z-10 grid place-items-center text-center text-white/80 text-[12px] p-4">
          <div>
            Собранное превью не загрузилось.
            <div className="mt-2 flex gap-2 justify-center">
              <button className="btn btn-sm" onClick={() => api.post("/api/quick/build").catch((e) => useStore.getState().say("err", e.message))}>Пересобрать</button>
              <button className="btn btn-sm" onClick={() => q.set({ assembled: false })}>Живой монитор</button>
            </div>
          </div>
        </div>
      )}
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
              <span className="text-accent font-medium">{prop.kind === "cut" ? "Нарезка по промпту" : prop.kind === "review" ? "Раунд правок заказчика" : prop.fast ? "Предложение (понято по таймкодам, мгновенно)" : "ИИ предлагает"}:</span> {prop.summary}
            </div>
            {prop.warnings?.length > 0 && <div className="text-warn mt-0.5">{prop.warnings.join(" · ")}</div>}
            {prop.kind === "review" && !!prop.answers?.length && (
              <div className="mt-1 space-y-0.5 max-h-[120px] overflow-y-auto">
                {prop.answers.map((a) => (
                  <div key={a.n} className="text-[12px]"><span className={a.done ? "text-ok" : "text-warn"}>{a.done ? "✓" : "?"} {a.n}.</span> <span className="text-dim">{a.text}</span> → {a.how}</div>
                ))}
              </div>
            )}
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
