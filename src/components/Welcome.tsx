import { useState } from "react";
import { Icon } from "../icons";
import { useStore } from "../store";
import { cls } from "../util";

export function Welcome() {
  const project = useStore((s) => s.project);
  const openProject = useStore((s) => s.openProject);
  const [path, setPath] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const isElectron = !!window.cutroom;

  const open = async (p: string, files?: string[]) => {
    if (!p) return;
    setBusy(true);
    await openProject(p, files);
    setBusy(false);
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto grid place-items-center p-4">
      <div className="w-full max-w-[640px] flex flex-col gap-5">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight">Откройте папку с исходниками</h1>
          <p className="text-dim mt-1">
            Все дубли и ракурсы одной съёмки — в одной папке. Cutroom ничего в ней не меняет: прокси, расшифровки и монтаж ложатся в <span className="mono">edit/</span> рядом.
          </p>
        </div>
        <div
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            const files = [...e.dataTransfer.files];
            const paths = files.map((f) => window.cutroom?.pathForFile(f)).filter(Boolean) as string[];
            if (!paths.length) return useStore.getState().say("info", "В браузере пути файлов недоступны — введите путь к папке ниже.");
            // one folder → that folder is the project; loose files → their folder is the project
            open(paths[0], paths.length > 1 ? paths : undefined);
          }}
          className={cls("rounded-[8px] border-2 border-dashed p-8 grid place-items-center gap-3 text-center",
            over ? "border-accent bg-accent-soft" : "border-line-strong")}
        >
          <Icon name="folder" size={28} className="text-faint" />
          <p className="text-dim">Перетащите папку или файлы сюда</p>
          {isElectron && (
            <button className="btn btn-primary" disabled={busy} onClick={async () => { const p = await window.cutroom!.pickFolder(); if (p) open(p); }}>
              Выбрать папку…
            </button>
          )}
        </div>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); open(path.trim()); }}>
          <input className="input flex-1" placeholder="D:\Video\съёмка" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Путь к папке" />
          <button className="btn" disabled={busy || !path.trim()}>{busy ? "Сканирую…" : "Открыть"}</button>
        </form>
        {!!project?.recent?.length && (
          <div>
            <div className="label mb-2">Недавние</div>
            <div className="flex flex-col gap-1">
              {project.recent.map((r) => (
                <button key={r} className="text-left px-3 py-2 rounded-[6px] hover:bg-panel-3 mono text-[12.5px] truncate" onClick={() => open(r)}>{r}</button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
