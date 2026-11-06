import { useStore } from "../store";
import { runCommand } from "./CommandPalette";

const CHIPS: { key: string; label: string; options: (string | boolean)[] }[] = [
  { key: "format", label: "Формат", options: ["16:9", "9:16", "1:1", "4K"] },
  { key: "length", label: "Длина", options: ["30 с", "60 с", "3 мин", "10–15 мин"] },
  { key: "pace", label: "Темп", options: ["спокойный", "живой", "динамичный"] },
  { key: "gfx", label: "Инфографика", options: [true, false] },
  { key: "grade", label: "Цветокор", options: [true, false] },
  { key: "subs", label: "Субтитры", options: [false, "bold-overlay", "natural-sentence"] },
];

const show = (v: string | boolean | undefined) => (v === true ? "да" : v === false || v === undefined ? "нет" : v);

export function Composer() {
  const prompt = useStore((s) => s.prompt);
  const chips = useStore((s) => s.chips);
  const set = useStore((s) => s.set);
  const sources = useStore((s) => s.sources);
  const live = sources.filter((s) => !s.duplicate_of && !s.excluded && s.probe?.audio.length);
  const ready = live.filter((s) => s.has.transcript).length;

  const cycle = (key: string, options: (string | boolean)[]) => {
    const cur = chips[key];
    const i = options.indexOf(cur as never);
    set({ chips: { ...chips, [key]: options[(i + 1) % options.length] } });
  };

  return (
    <div className="shrink-0 border-t border-line bg-panel p-2.5 flex flex-col gap-2">
      <div className="flex gap-1.5 flex-wrap items-center">
        {CHIPS.map((c) => (
          <button key={c.key} className="chip" aria-pressed={chips[c.key] !== undefined && chips[c.key] !== false}
            onClick={() => cycle(c.key, c.options)} title="Чипы только подсказывают: главное — текст">
            <span className="text-faint">{c.label}:</span> {show(chips[c.key])}
          </button>
        ))}
      </div>
      <div className="flex gap-2 items-end">
        <textarea
          className="input flex-1 h-auto min-h-[44px] max-h-[120px] py-2 resize-none leading-snug"
          rows={2}
          placeholder="Опишите ролик обычным языком: «динамичный ролик 60 с, вертикаль, оставь шутку про кофе, инфографика на цифрах»"
          value={prompt}
          onChange={(e) => set({ prompt: e.target.value })}
          aria-label="Промпт монтажа"
        />
        <div className="flex flex-col items-end gap-1">
          <span className="text-[11px] text-faint tnum">{ready}/{live.length} расшифровано</span>
          <button className="btn btn-primary h-9" onClick={() => runCommand("draft")} title="Ctrl+Enter">
            Предложить стратегию
          </button>
        </div>
      </div>
    </div>
  );
}
