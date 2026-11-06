import { useEffect, useRef, useState } from "react";
import { api, gfxUrl, type Brand, type EdlSlot, type EdlState, type GfxSpec } from "../api";
import { Icon } from "../icons";
import { seek, useStore } from "../store";
import { cls, fmtTime } from "../util";

const TYPES: Record<string, string> = {
  number: "число", bars: "столбцы", donut: "доли", list: "список", compare: "было → стало", line: "линия по годам", title: "титр",
};
const STATUS: Record<string, [string, string]> = {
  placeholder: ["задумано", "badge"], working: ["агент работает", "badge badge-accent"], ready: ["готово", "badge badge-ok"], error: ["ошибка", "badge badge-warn"],
};
// Windows fonts with Cyrillic: regular + bold file
const FONTS: Record<string, [string, string]> = {
  "Segoe UI": ["segoeui.ttf", "seguisb.ttf"], Arial: ["arial.ttf", "arialbd.ttf"], Calibri: ["calibri.ttf", "calibrib.ttf"],
  Bahnschrift: ["bahnschrift.ttf", "bahnschrift.ttf"], Verdana: ["verdana.ttf", "verdanab.ttf"], Georgia: ["georgia.ttf", "georgiab.ttf"],
};
const DEFAULT_BRAND: Brand = { bg: "#0b0c10", panel: "#14161c", fg: "#f3f2ee", muted: "#9aa1ad", accent: "#e8a33d", accent2: "#5b8def", font: "segoeui.ttf", font_bold: "seguisb.ttf" };

const say = (k: "ok" | "err" | "info", t: string) => useStore.getState().say(k, t);
async function call(method: "post" | "put" | "del", path: string, body?: unknown) {
  try {
    const r = method === "del" ? await api.del<EdlState>(path) : await (api[method] as (p: string, b?: unknown) => Promise<unknown>)(path, body);
    if (r && typeof r === "object" && "edl" in (r as object)) useStore.setState({ edl: r as EdlState });
    return r;
  } catch (e) { say("err", (e as Error).message); return null; }
}

export function GfxPanel() {
  const edl = useStore((s) => s.edl?.edl);
  const gate = useStore((s) => s.strategy?.gate_open);
  const sel = useStore((s) => s.selSlot);
  const time = useStore((s) => s.time);
  const [type, setType] = useState("list");
  if (!edl) return <p className="p-3 text-dim text-[12.5px]">Инфографика живёт на монтаже: соберите черновик (стратегия или перетаскивание файлов на таймлайн).</p>;
  const slots = [...(edl.slots ?? [])].sort((a, b) => a.start_in_output - b.start_in_output);
  const guard = () => { if (!gate) say("info", "Менять монтаж можно после утверждения стратегии."); return !!gate; };

  return (
    <div className="flex flex-col text-[12.5px]">
      <BrandSection brand={{ ...DEFAULT_BRAND, ...(edl.brand ?? {}) }} />
      <section className="p-3 border-b border-line flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="label">Слоты</span>
          <span className="badge">{slots.length}</span>
          <span className="flex-1" />
          <button className="btn btn-primary btn-sm" disabled={!slots.length} onClick={() => guard() && call("post", "/api/gfx/build", {})}
            title="Каждый слот строит отдельный агент, все одновременно">
            <Icon name="agent" size={13} /> Построить все
          </button>
        </div>
        <div className="flex items-center gap-1.5">
          <select className="input h-7 flex-1" value={type} onChange={(e) => setType(e.target.value)} aria-label="Тип новой карточки">
            {Object.entries(TYPES).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <button className="btn btn-sm" onClick={() => guard() && call("post", "/api/gfx/add", { at: time, type })}>
            <Icon name="plus" size={13} /> В бегунок {fmtTime(time).replace(/\.\d+$/, "")}
          </button>
        </div>
        <p className="text-[11.5px] text-faint">Цифры агент берёт только из слов спикера. Слот можно двигать и растягивать на дорожке I таймлайна, двойной клик по дорожке — новый слот.</p>
      </section>
      {slots.length === 0 && <p className="p-3 text-dim">Слотов пока нет. Агент-редактор предлагает их при сборке, или добавьте слот сами.</p>}
      {slots.map((s) => <SlotCard key={s.id} s={s} active={s.id === sel} guard={guard} />)}
    </div>
  );
}

function SlotCard({ s, active, guard }: { s: EdlSlot; active: boolean; guard: () => boolean }) {
  const jobs = useStore((st) => st.jobs);
  const spec = typeof s.spec === "object" ? (s.spec as GfxSpec) : null;
  const job = jobs.filter((j) => (j.kind === "slot" || j.kind === "slot-render") && j.source === s.id && (j.status === "running" || j.status === "queued"))[0];
  const [st, cl] = job ? [job.kind === "slot" ? "агент думает…" : `рисую ${Math.round(job.progress * 100)}%`, "badge badge-accent"]
    : s.status === "working" ? ["прервано — постройте заново", "badge badge-warn"]   // no live job: the app was closed mid-way
    : (STATUS[s.status] ?? [s.status, "badge"]);
  const [hint, setHint] = useState(s.spec_hint ?? "");
  useEffect(() => setHint(s.spec_hint ?? ""), [s.spec_hint]);
  const put = (body: object) => guard() && call("put", `/api/gfx/${s.id}`, body);
  const build = (request?: string) => guard() && call("post", "/api/gfx/build", { ids: [s.id], request });
  const pick = () => { useStore.getState().set({ selSlot: s.id }); seek(s.start_in_output + 0.01); };
  const ref = useRef<HTMLElement>(null);
  useEffect(() => { if (active) ref.current?.scrollIntoView({ block: "nearest" }); }, [active]);   // picked on the I track

  return (
    <section ref={ref} className={cls("p-3 border-b border-line flex flex-col gap-2", active && "bg-panel-2")} onClick={pick}>
      <div className="flex items-center gap-2">
        <span className="badge">{TYPES[s.type] ?? s.type}</span>
        <span className="tnum text-faint">{fmtTime(s.start_in_output).replace(/\.\d+$/, "")} · {s.duration.toFixed(1)} с</span>
        <span className="flex-1" />
        <span className={cl}>{st}</span>
      </div>
      {s.status === "ready" && s.files && (
        <img src={gfxUrl(s.id, "poster", s.files.v)} alt={spec?.title ?? "карточка"} className="w-full rounded-[6px] border border-line bg-[#111] aspect-video object-contain" />
      )}
      {s.error && <p className="text-[11.5px] text-err">{s.error}</p>}
      {spec ? <SpecEditor spec={spec} onSave={(next) => put({ spec: next })} /> : (
        <label className="flex flex-col gap-1" onClick={(e) => e.stopPropagation()}>
          <span className="text-faint">Что показать (подсказка агенту)</span>
          <textarea className="input h-auto py-1.5" rows={2} value={hint} placeholder="например: рост рынка с 2015 по 2025, мы — в точке 2025"
            onChange={(e) => setHint(e.target.value)} onBlur={() => hint !== (s.spec_hint ?? "") && put({ spec_hint: hint })} />
        </label>
      )}
      <div className="flex flex-wrap gap-1" onClick={(e) => e.stopPropagation()}>
        <button className="btn btn-sm" disabled={!!job} onClick={() => build()}><Icon name="agent" size={13} /> {spec ? "Перестроить" : "Построить"}</button>
        {spec && <button className="btn btn-ghost btn-sm" disabled={!!job} onClick={() => build("упрости: меньше элементов и короче подписи")}>Проще</button>}
        <button className="btn btn-ghost btn-sm" onClick={() => put({ duration: s.duration - 1 })}>Короче</button>
        <button className="btn btn-ghost btn-sm" onClick={() => put({ duration: s.duration + 1 })}>Длиннее</button>
        <span className="flex-1" />
        <button className="btn btn-ghost btn-sm" title="Удалить слот" onClick={() => guard() && call("del", `/api/gfx/${s.id}`)}><Icon name="x" size={13} /></button>
      </div>
    </section>
  );
}

// Light-touch edit of what the card says; the drawing follows the brand and type.
function SpecEditor({ spec, onSave }: { spec: GfxSpec; onSave: (s: GfxSpec) => void }) {
  const [d, setD] = useState(spec);
  useEffect(() => setD(spec), [JSON.stringify(spec)]);
  const commit = (n: GfxSpec) => { if (JSON.stringify(n) !== JSON.stringify(spec)) onSave(n); };
  const line = (i: number) => {
    const it = d.items[i];
    return [it.label, it.value ?? "", it.text].map((x) => String(x ?? "")).join(" | ").replace(/( \| )+$/, "");
  };
  return (
    <details className="text-[12px]" onClick={(e) => e.stopPropagation()}>
      <summary className="cursor-pointer text-dim truncate">«{spec.title || "без заголовка"}» — текст карточки</summary>
      <div className="flex flex-col gap-1.5 mt-2">
        <input className="input h-7" value={d.title} placeholder="Заголовок" onChange={(e) => setD({ ...d, title: e.target.value })} onBlur={() => commit(d)} />
        <input className="input h-7" value={d.subtitle ?? ""} placeholder="Подзаголовок" onChange={(e) => setD({ ...d, subtitle: e.target.value })} onBlur={() => commit(d)} />
        {d.items.map((_, i) => (
          <input key={i} className="input h-7 mono text-[11.5px]" defaultValue={line(i)} title="подпись | число | текст"
            onBlur={(e) => {
              const [label = "", value = "", text = ""] = e.target.value.split("|").map((x) => x.trim());
              const v = value === "" ? null : Number(value.replace(",", "."));
              const items = d.items.map((it, k) => (k === i ? { label, value: v != null && Number.isFinite(v) ? v : null, text } : it));
              const n = { ...d, items };
              setD(n); commit(n);
            }} />
        ))}
        <input className="input h-7" value={d.note ?? ""} placeholder="Сноска внизу (источник)" onChange={(e) => setD({ ...d, note: e.target.value })} onBlur={() => commit(d)} />
        <label className="flex items-center gap-2 text-faint">
          <input type="checkbox" checked={d.layout === "full"} onChange={(e) => { const n = { ...d, layout: e.target.checked ? "full" as const : "side" as const }; setD(n); commit(n); }} />
          на весь кадр (иначе сбоку, спикер виден)
        </label>
      </div>
    </details>
  );
}

function BrandSection({ brand }: { brand: Brand }) {
  const [b, setB] = useState(brand);
  useEffect(() => setB(brand), [JSON.stringify(brand)]);
  const font = Object.entries(FONTS).find(([, f]) => f[0] === b.font)?.[0] ?? "Segoe UI";
  const save = (n: Brand) => call("put", "/api/brand", { brand: { ...n, confirmed: true } });
  const swatches: [keyof Brand, string][] = [["accent", "акцент"], ["accent2", "второй"], ["panel", "плашка"], ["fg", "текст"]];
  return (
    <section className="p-3 border-b border-line flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="label">Стиль карточек</span>
        {!brand.confirmed && <span className="badge badge-warn" title="Задайте фирменные цвета и шрифт — или оставьте нейтральную палитру">[NEED: цвета бренда]</span>}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {swatches.map(([k, l]) => (
          <label key={k} className="flex items-center gap-1 text-faint" title={l}>
            <input type="color" value={b[k] as string} className="w-7 h-7 rounded border border-line bg-transparent p-0"
              onChange={(e) => setB({ ...b, [k]: e.target.value })} onBlur={() => save(b)} />
            {l}
          </label>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <span className="text-faint w-14">Шрифт</span>
        <select className="input h-7 flex-1" value={font} onChange={(e) => { const [r, bold] = FONTS[e.target.value]; const n = { ...b, font: r, font_bold: bold }; setB(n); save(n); }}>
          {Object.keys(FONTS).map((f) => <option key={f}>{f}</option>)}
        </select>
        {!brand.confirmed && <button className="btn btn-ghost btn-sm" onClick={() => save(b)}>Оставить так</button>}
      </div>
    </section>
  );
}
