import * as Dialog from "@radix-ui/react-dialog";
import { useMemo, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { targetSpan } from "../timelineOps";
import { cls } from "../util";

type Cmd = { id: string; title: string; hint?: string; run: () => void };

const s = () => useStore.getState();

// Commands that belong to later stages answer honestly instead of silently doing nothing.
export function runCommand(id: string) {
  const st = s();
  const gate = "Сначала стратегия: резать можно только после «Утвердить стратегию» (Hard Rule 11).";
  switch (id) {
    case "draft": {
      const live = st.sources.filter((x) => !x.duplicate_of && !x.excluded && x.probe?.audio.length);
      const missing = live.filter((x) => !x.has.transcript).length;
      if (!st.project?.open) return st.say("info", "Откройте папку с исходниками.");
      if (missing) return st.say("info", `Ещё не расшифровано ${missing} из ${live.length}. Стратегию агент строит по полному takes_packed.md.`);
      st.set({ rightTab: "strategy", layout: { ...st.layout, rightOpen: true } });
      api.post("/api/strategy", { prompt: st.prompt, chips: st.chips, feedback: null })
        .then(() => st.loadStrategy()).catch((e) => st.say("err", e.message));
      return;
    }
    case "cut": case "delete-range": {
      const e = st.edl?.edl;
      if (!st.strategy?.gate_open) return st.say("info", gate);
      if (!e || st.monitor !== "program") return st.say("info", "Резать можно в «Итоге»: переключите плеер на программу.");
      const T = st.time;
      const span = targetSpan();   // the selected episode(s); the playhead only gives the cut point
      if (!span) return;
      const ranges = e.ranges.map((r) => ({ ...r }));
      if (id === "cut") {
        const i = span[0];
        const r = ranges[i];
        if (span[0] !== span[1]) return st.say("info", "Разрезать можно один эпизод: выберите его кликом.");
        const ts = r.start + (T - r.out_start);
        if (T < r.out_start || T > r.out_start + (r.end - r.start)) return st.say("info", `Поставьте бегунок внутрь эпизода ${i + 1} — там пройдёт разрез.`);
        if (ts - r.start < 0.3 || r.end - ts < 0.3) return st.say("info", "Слишком близко к краю эпизода.");
        ranges.splice(i, 1, { ...r, end: ts }, { ...r, start: ts, reason: "разрезано вручную" });
        st.putRanges(ranges, `разрез эпизода ${i + 1}`);
      } else {
        const gone = ranges.splice(span[0], span[1] - span[0] + 1);
        if (!ranges.length) return st.say("info", "Нельзя удалить все эпизоды.");
        st.set({ selRange: null, selEnd: null });
        st.putRanges(ranges, gone.length === 1 ? `удалён эпизод ${span[0] + 1}` : `удалены эпизоды ${span[0] + 1}–${span[1] + 1}`);
      }
      return;
    }
    case "undo": case "redo": {
      if (!st.edl?.edl) return st.say("info", gate);
      api.post(`/api/edl/${id}`).then((r) => st.set({ edl: r as never })).catch((e) => st.say("info", e.message));
      return;
    }
    case "prev-problem": case "next-problem":
      st.set({ leftTab: "problems" });
      return;
    case "ingest":
      api.post("/api/project/ingest", {}).then(() => st.set({ queueOpen: true })).catch((e) => st.say("err", e.message));
      return;
    case "system": st.set({ view: "system" }); return;
    case "palette": st.set({ paletteOpen: true }); return;
    case "reveal-edit": if (st.project?.edit) window.cutroom?.revealInFolder(st.project.edit); return;
    case "edit": st.set({ view: "edit" }); return;
    case "queue": st.set({ queueOpen: !st.queueOpen }); return;
    case "close-project": st.set({ project: { open: false, recent: st.project?.recent }, sources: [], selected: null, view: "edit" }); return;
    case "theme": {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      api.patch("/api/settings", { theme: next }).catch(() => {});
      return;
    }
    case "open-folder":
      window.cutroom?.pickFolder().then((p) => { if (p) st.openProject(p); });
      if (!window.cutroom) st.set({ project: { open: false, recent: st.project?.recent }, view: "edit" });
      return;
  }
}

export function CommandPalette() {
  const open = useStore((st) => st.paletteOpen);
  const set = useStore((st) => st.set);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);

  const cmds: Cmd[] = useMemo(() => [
    { id: "open-folder", title: "Открыть папку проекта", hint: "Ctrl+O" },
    { id: "ingest", title: "Подготовить все файлы: прокси и расшифровка" },
    { id: "draft", title: "Предложить стратегию", hint: "Ctrl+Enter" },
    { id: "queue", title: "Очередь задач", hint: "`" },
    { id: "system", title: "Система: железо и модели" },
    { id: "edit", title: "Вернуться к монтажу" },
    { id: "theme", title: "Переключить тему" },
    { id: "close-project", title: "Закрыть проект" },
  ].map((c) => ({ ...c, run: () => runCommand(c.id) })), []);
  const list = cmds.filter((c) => c.title.toLowerCase().includes(q.toLowerCase()));

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { set({ paletteOpen: o }); setQ(""); setIdx(0); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-[var(--scrim)]" />
        <Dialog.Content className="fixed z-50 left-1/2 top-[14vh] -translate-x-1/2 w-[min(560px,calc(100vw-32px))] rounded-[8px] bg-panel-2 shadow-[var(--shadow-pop)] overflow-hidden"
          aria-describedby={undefined}>
          <Dialog.Title className="sr-only">Команды</Dialog.Title>
          <input autoFocus className="w-full h-11 px-4 bg-transparent border-b border-line outline-none text-[14px]" placeholder="Команда…"
            value={q} onChange={(e) => { setQ(e.target.value); setIdx(0); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(list.length - 1, i + 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
              if (e.key === "Enter" && list[idx]) { set({ paletteOpen: false }); list[idx].run(); }
            }} />
          <ul className="max-h-[50vh] overflow-y-auto py-1" role="listbox">
            {list.map((c, i) => (
              <li key={c.id} role="option" aria-selected={i === idx}>
                <button className={cls("w-full h-9 px-4 flex items-center text-left text-[13px]", i === idx ? "bg-accent-soft text-fg" : "text-dim")}
                  onMouseEnter={() => setIdx(i)} onClick={() => { set({ paletteOpen: false }); c.run(); }}>
                  <span className="flex-1">{c.title}</span>
                  {c.hint && <kbd className="mono text-[11px] text-faint">{c.hint}</kbd>}
                </button>
              </li>
            ))}
            {!list.length && <li className="px-4 py-3 text-dim text-[12.5px]">Ничего не найдено</li>}
          </ul>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
