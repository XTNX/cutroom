import { useEffect, useRef } from "react";
import { api, connectWs, initBackend, type Job } from "./api";
import { groupOf, seek, shuttle, stepFrames, togglePlay, useStore, player } from "./store";
import { addMarkerAtPlayhead } from "./components/MarkersPanel";
import { cycleAngle, switchAngle } from "./components/AngleStrip";
import { TitleBar } from "./components/TitleBar";
import { LeftPanel } from "./components/LeftPanel";
import { Player } from "./components/Player";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { Timeline } from "./components/Timeline";
import { QueueDrawer } from "./components/QueueDrawer";
import { SystemView } from "./components/SystemView";
import { Welcome } from "./components/Welcome";
import { CommandPalette, runCommand } from "./components/CommandPalette";
import { Toast } from "./components/Toast";
import { SetupView, useSetup } from "./components/SetupView";
import { QuickView } from "./components/QuickView";

export function App() {
  const ready = useStore((s) => s.ready);
  const online = useStore((s) => s.online);
  const project = useStore((s) => s.project);
  const view = useStore((s) => s.view);
  const layout = useStore((s) => s.layout);
  const appMode = useStore((s) => s.appMode);
  const setupState = useSetup();

  useEffect(() => {
    let stop = () => {};
    (async () => {
      await initBackend();
      try {
        const settings = await api.get<{ ui_layout: typeof layout | null; theme: string }>("/api/settings");
        if (settings.ui_layout) useStore.setState({ layout: { ...useStore.getState().layout, ...settings.ui_layout } });
        if (settings.theme) document.documentElement.dataset.theme = settings.theme;
        await useStore.getState().refreshProject();
      } catch (e) {
        useStore.getState().say("err", `Бэкенд не отвечает: ${(e as Error).message}`);
      }
      useStore.setState({ ready: true });
      let lastSrc = -1;
      stop = connectWs(
        (m) => {
          if (m.type === "jobs") {
            const prev = useStore.getState().jobs;
            useStore.setState({ jobs: m.jobs as Job[] });
            notifyFinished(prev, m.jobs as Job[]);
            // infographic slots and verified renders change the EDL/preview on the server: pull it once they land
            const was = new Map(prev.map((j) => [j.id, j.status]));
            if ((m.jobs as Job[]).some((j) => ["slot", "slot-render", "preview"].includes(j.kind) && was.get(j.id) !== j.status
              && ["running", "done", "error", "blocked"].includes(j.status))) useStore.getState().loadEdl();
            // quick mode: blocks, cards and builds change the document on the server
            if ((m.jobs as Job[]).some((j) => j.kind.startsWith("q") && was.get(j.id) !== j.status)) window.dispatchEvent(new Event("cutroom-dirty"));
          } else if (m.type === "sources_dirty" && m.v !== lastSrc) {
            lastSrc = m.v;
            useStore.getState().refreshSources().catch(() => {});
            window.dispatchEvent(new Event("cutroom-dirty"));
          }
        },
        (up) => useStore.setState({ online: up }),
      );
    })();
    return () => stop();
  }, []);

  // keep the OS awake while heavy work runs (Electron powerSaveBlocker)
  const jobs = useStore((s) => s.jobs);
  const busy = jobs.some((j) => j.status === "running" && (j.heavy || j.lane === "render"));
  useEffect(() => { window.cutroom?.setBusy(busy); }, [busy]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      const s = useStore.getState();
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); s.set({ paletteOpen: !s.paletteOpen }); return; }
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); runCommand("draft"); return; }
      if (typing || s.paletteOpen) return;
      if (e.ctrlKey && e.key.toLowerCase() === "z") { e.preventDefault(); runCommand(e.shiftKey ? "redo" : "undo"); return; }
      if (e.altKey && /^[1-4]$/.test(e.key) && s.monitor === "program") {
        e.preventDefault();
        const r = s.edl?.edl?.ranges.find((x) => s.time >= x.out_start && s.time < x.out_start + (x.end - x.start));
        const g = r ? groupOf(r.source) : undefined;
        const tiles = [...document.querySelectorAll<HTMLButtonElement>('[aria-label="Камеры этого дубля"] button')];
        const cams = tiles.map((b) => ({ id: b.dataset.cam! })).filter((c) => c.id && g);   // same order as the strip
        const m = cams[Number(e.key) - 1];
        if (m) switchAngle(m.id, e.shiftKey);
        return;
      }
      switch (e.key) {
        case "m": case "M": e.preventDefault(); addMarkerAtPlayhead(); break;
        case "a": case "A": if (s.monitor === "program") cycleAngle(); break;
        case "Escape": s.set({ selRange: null, selEnd: null, selMarker: null }); break;
        case " ": e.preventDefault(); togglePlay(); break;
        case "j": case "J": shuttle(-1); break;
        case "k": case "K": shuttle(0); break;
        case "l": case "L": shuttle(1); break;
        case "ArrowLeft": e.preventDefault(); stepFrames(e.shiftKey ? -10 : -1); break;
        case "ArrowRight": e.preventDefault(); stepFrames(e.shiftKey ? 10 : 1); break;
        case "Home": seek(0); break;
        case "End": seek(player.el?.duration ?? 0); break;
        case "s": case "S": runCommand("cut"); break;
        case "x": case "X": runCommand("delete-range"); break;
        case "[": runCommand("prev-problem"); break;
        case "]": runCommand("next-problem"); break;
        case "1": s.set({ wave: 1 }); break;
        case "2": s.set({ wave: 2 }); break;
        case "3": s.set({ wave: 3 }); break;
        case "`": s.set({ queueOpen: !s.queueOpen }); break;
      }
    };
    window.addEventListener("keydown", onKey);
    const offMenu = window.cutroom?.onMenu((cmd) => runCommand(cmd));
    return () => { window.removeEventListener("keydown", onKey); offMenu?.(); };
  }, []);

  if (!ready) {
    if (setupState && setupState.state !== "none") return <div className="h-full flex flex-col"><SetupView st={setupState} /></div>;
    return <div className="h-full grid place-items-center text-dim"><span className="pulse">Запускаю бэкенд…</span></div>;
  }

  return (
    <div className="h-full flex flex-col">
      <TitleBar />
      {!online && (
        <div className="px-4 py-1.5 text-[12px] bg-[color-mix(in_srgb,var(--err)_18%,transparent)] text-err border-b border-line">
          Нет связи с бэкендом Cutroom. Переподключаюсь… Если не проходит, перезапустите приложение: задачи в очереди сохранятся в истории проекта.
        </div>
      )}
      {view === "system" ? (
        <SystemView />
      ) : !project?.open ? (
        <Welcome />
      ) : appMode === "quick" ? (
        <QuickView />
      ) : (
        <div className="flex-1 min-h-0 flex flex-col">
          <div className="flex-1 min-h-0 flex">
            {layout.leftOpen && (
              <>
                <div style={{ width: layout.left }} className="shrink-0 min-w-0 bg-panel border-r border-line flex flex-col max-[900px]:hidden">
                  <LeftPanel />
                </div>
                <Splitter axis="x" onDrag={(d) => useStore.getState().setLayout({ left: clamp(layout.left + d, 220, 520) })} />
              </>
            )}
            <div className="flex-1 min-w-0 flex flex-col">
              <Player />
              <Composer />
            </div>
            {layout.rightOpen && (
              <>
                <Splitter axis="x" onDrag={(d) => useStore.getState().setLayout({ right: clamp(layout.right - d, 260, 520) })} />
                <div style={{ width: layout.right }} className="shrink-0 min-w-0 bg-panel border-l border-line flex flex-col max-[1100px]:hidden">
                  <Inspector />
                </div>
              </>
            )}
          </div>
          <Splitter axis="y" onDrag={(d) => useStore.getState().setLayout({ bottom: clamp(layout.bottom - d, 140, 520) })} />
          <div style={{ height: layout.bottom }} className="shrink-0 bg-panel border-t border-line min-h-0">
            <Timeline />
          </div>
        </div>
      )}
      <QueueDrawer />
      <CommandPalette />
      <Toast />
    </div>
  );
}

const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

function Splitter({ axis, onDrag }: { axis: "x" | "y"; onDrag: (delta: number) => void }) {
  const last = useRef(0);
  return (
    <div
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      className={axis === "x" ? "w-1 -mx-0.5 z-10 cursor-col-resize hover:bg-accent/40" : "h-1 -my-0.5 z-10 cursor-row-resize hover:bg-accent/40"}
      onPointerDown={(e) => {
        last.current = axis === "x" ? e.clientX : e.clientY;
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!(e.buttons & 1)) return;
        const cur = axis === "x" ? e.clientX : e.clientY;
        const d = cur - last.current;
        if (d) { last.current = cur; onDrag(d); }
      }}
    />
  );
}

function notifyFinished(prev: Job[], next: Job[]) {
  const was = new Map(prev.map((j) => [j.id, j.status]));
  for (const j of next) {
    const before = was.get(j.id);
    if (before === "running" && (j.status === "done" || j.status === "error") && (j.heavy || j.lane === "render")) {
      if (j.lane === "render" || j.kind === "model") {
        window.cutroom?.notify(j.status === "done" ? "Cutroom: готово" : "Cutroom: ошибка", j.label);
      }
    }
  }
}
