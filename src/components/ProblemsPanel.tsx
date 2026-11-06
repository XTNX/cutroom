import { useMemo } from "react";
import { seek, useStore } from "../store";
import { fmtTime, isFiller } from "../util";

// Stage 5 adds detectors on the EDL (cut-off phrases, black/freeze frames, clicks).
// Until there is an EDL, this panel lists what the transcript already tells us about the source.
export function ProblemsPanel() {
  const sel = useStore((s) => s.selected);
  const t = useStore((s) => (sel ? s.transcripts[sel] : undefined));
  const items = useMemo(() => {
    if (typeof t !== "object") return [];
    const out: { kind: string; at: number; text: string }[] = [];
    t.words.forEach((w, i) => {
      if (isFiller(w[0])) out.push({ kind: "Филлер", at: w[1], text: w[0] });
      if (w[3] < 0.45) out.push({ kind: "Низкая уверенность", at: w[1], text: `${w[0]} · ${(w[3] * 100).toFixed(0)}%` });
      const next = t.words[i + 1];
      if (next && next[1] - w[2] >= 1.2) out.push({ kind: "Тишина", at: w[2], text: `${(next[1] - w[2]).toFixed(1)} с` });
      if (next && next[0].toLowerCase().replace(/[^\p{L}]/gu, "") === w[0].toLowerCase().replace(/[^\p{L}]/gu, "") && w[0].length > 1)
        out.push({ kind: "Повтор", at: w[1], text: `${w[0]} ${next[0]}` });
    });
    return out.sort((a, b) => a.at - b.at);
  }, [t]);

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <p className="px-3 py-2 text-[11.5px] text-faint border-b border-line">
        Кандидаты по расшифровке выбранного файла. Детекторы по склейке (обрезанные фразы, пустые кадры, щелчки) и пакетная автоправка
        включатся, когда появится EDL (этап 5).
      </p>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {items.length === 0 && <p className="p-4 text-dim text-[12.5px]">Пока ничего не найдено.</p>}
        {items.map((p, k) => (
          <button key={k} onClick={() => seek(Math.max(0, p.at - 0.5))}
            className="w-full text-left px-3 py-1.5 flex items-center gap-2 border-b border-line hover:bg-panel-3 text-[12.5px]">
            <span className="mono tnum text-faint text-[11px] w-[62px]">{fmtTime(p.at)}</span>
            <span className={p.kind === "Тишина" ? "text-info" : p.kind === "Филлер" ? "text-accent" : "text-warn"}>{p.kind}</span>
            <span className="truncate text-dim">{p.text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
