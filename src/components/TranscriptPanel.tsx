import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Word } from "../api";
import { seek, useStore, togglePlay } from "../store";
import { cls, fmtTime, isFiller } from "../util";

const LOW = 0.6;

type Phrase = { start: number; end: number; words: { w: Word; i: number; gapBefore: number }[] };

function toPhrases(words: Word[]): Phrase[] {
  const out: Phrase[] = [];
  let cur: Phrase | null = null;
  words.forEach((w, i) => {
    const gap = i ? w[1] - words[i - 1][2] : 0;
    if (!cur || gap >= 0.5) {
      cur = { start: w[1], end: w[2], words: [] };
      out.push(cur);
    }
    cur.words.push({ w, i, gapBefore: gap });
    cur.end = w[2];
  });
  return out;
}

export function TranscriptPanel() {
  const sel = useStore((s) => s.selected);
  const t = useStore((s) => (sel ? s.transcripts[sel] : undefined));
  const source = useStore((s) => s.sources.find((x) => x.id === sel));
  const jobs = useStore((s) => s.jobs);
  const [mode, setMode] = useState<"words" | "packed">("words");
  const [packed, setPacked] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const words = typeof t === "object" ? t.words : [];
  const phrases = useMemo(() => toPhrases(words), [words]);
  const stats = useMemo(() => ({
    words: words.filter((w) => !w[4]).length,
    fillers: words.filter((w) => isFiller(w[0])).length,
    low: words.filter((w) => w[3] < LOW).length,
  }), [words]);

  // current-word highlight without re-rendering thousands of spans
  useEffect(() => {
    let last = -1;
    return useStore.subscribe((s) => {
      if (!box.current || !words.length) return;
      const time = s.time;
      let lo = 0, hi = words.length - 1, idx = -1;
      while (lo <= hi) {
        const m = (lo + hi) >> 1;
        if (words[m][1] <= time) { idx = m; lo = m + 1; } else hi = m - 1;
      }
      if (idx >= 0 && time > words[idx][2] + 0.25) idx = -1;
      if (idx === last) return;
      box.current.querySelector(".word.active")?.classList.remove("active");
      if (idx >= 0) {
        const el = box.current.querySelector(`[data-i="${idx}"]`);
        el?.classList.add("active");
        if (s.playing) el?.scrollIntoView({ block: "nearest" });
      }
      last = idx;
    });
  }, [words]);

  useEffect(() => {
    if (mode !== "packed") return;
    api.get<string>("/api/takes_packed").then(setPacked).catch(() => setPacked(null));
  }, [mode, jobs.filter((j) => j.kind === "pack" && j.status === "done").length]);

  const asr = jobs.filter((j) => j.kind === "asr" && j.source === sel).sort((a, b) => b.id - a.id)[0];

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 shrink-0 text-[12px]">
        <div className="flex rounded-[6px] border border-line-strong overflow-hidden">
          {(["words", "packed"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className={cls("h-6 px-2 text-[11.5px]", mode === m ? "bg-panel-3 text-fg" : "text-dim")}>
              {m === "words" ? "Слова" : "takes_packed.md"}
            </button>
          ))}
        </div>
        <span className="flex-1" />
        {mode === "words" && typeof t === "object" && (
          <span className="text-dim tnum" title="слов · филлеров · низкая уверенность">
            {stats.words} сл · <span className="text-accent">{stats.fillers} фил.</span> · <span className="text-warn">{stats.low} ?</span>
          </span>
        )}
      </div>

      {mode === "packed" ? (
        <pre className="flex-1 min-h-0 overflow-auto p-3 mono text-[11.5px] leading-[1.55] whitespace-pre-wrap text-dim">
          {packed ?? "takes_packed.md появится после первой расшифровки."}
        </pre>
      ) : !sel ? (
        <Empty>Выберите источник слева.</Empty>
      ) : t === "loading" ? (
        <Empty><span className="pulse">Загружаю расшифровку…</span></Empty>
      ) : t === "missing" || t === undefined ? (
        <Empty>
          {asr?.status === "running" ? <>Расшифровка идёт: {Math.round(asr.progress * 100)}%</>
            : asr?.status === "queued" ? <>Расшифровка в очереди.</>
            : asr?.status === "error" ? <span className="text-err">Расшифровка не удалась: {asr.error}. Откройте очередь (`) и нажмите «Повторить».</span>
            : !source?.probe?.audio.length ? <>В файле нет звуковой дорожки — расшифровывать нечего.</>
            : <>Расшифровки ещё нет. Нажмите «Подготовить» во вкладке «Источники».</>}
        </Empty>
      ) : (
        <div ref={box} className="flex-1 min-h-0 overflow-y-auto px-3 py-2 text-[13.5px] leading-[1.75]">
          <p className="text-[11.5px] text-faint mb-2">
            {String(t.meta?.model ?? "")} · {String(t.meta?.device ?? "")} · клик — перейти, двойной клик — играть с этого слова.
            Волнистое подчёркивание — уверенность ниже {LOW * 100}%: проверьте на стыке.
          </p>
          {phrases.map((p, k) => (
            <p key={k} className="mb-2">
              <span className="mono tnum text-[11px] text-faint mr-2 select-none cursor-pointer" onClick={() => seek(p.start)}>{fmtTime(p.start)}</span>
              {p.words.map(({ w, i, gapBefore }) => (
                <span key={i}>
                  {gapBefore >= 0.4 && gapBefore < 0.5 && <span className="text-faint text-[11px] mono select-none"> ·{gapBefore.toFixed(1)} </span>}
                  <span
                    data-i={i}
                    className={cls("word", w[3] < LOW && "low", isFiller(w[0]) && "filler", w[4] === 1 && "event")}
                    title={`${fmtTime(w[1])} – ${fmtTime(w[2])} · ${(w[3] * 100).toFixed(0)}%`}
                    onClick={() => seek(w[1])}
                    onDoubleClick={() => { seek(w[1]); togglePlay(); }}
                  >{w[0]}</span>{" "}
                </span>
              ))}
              {k < phrases.length - 1 && (
                <span className="text-faint text-[11px] mono select-none">·{(phrases[k + 1].start - p.end).toFixed(1)} с</span>
              )}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="p-4 text-dim text-[12.5px]">{children}</div>;
}
