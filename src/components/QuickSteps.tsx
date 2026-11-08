import { useEffect, useRef, useState } from "react";
import { api, frameUrl, mediaUrl, type Track } from "../api";
import { useStore } from "../store";
import { clone, end, fpsOf, split, type Grade, type QDoc } from "../quickOps";
import { ROLE_NAMES, selRange, shownDoc, useQuick, fmtT, type QuickPayload, type Step } from "../quickStore";
import { quickUrl } from "./QuickView";
import { cls } from "../util";

const STEPS: [Step, string][] = [[1, "Исходники"], [2, "Нарезка"], [3, "Цвет и звук"], [4, "Слайды и графика"]];

export function QuickSteps() {
  const step = useQuick((s) => s.step);
  const set = useQuick((s) => s.set);
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="grid grid-cols-4 border-b border-line" role="tablist" aria-label="Шаги">
        {STEPS.map(([n, label]) => (
          <button key={n} role="tab" aria-selected={step === n} onClick={() => set({ step: n })} title={label}
            className={cls("py-2 text-[11.5px] leading-tight border-b-2", step === n ? "border-accent text-fg" : "border-transparent text-dim hover:text-fg")}>
            <span className="block text-[13px] font-medium">{n}</span>{label}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4 text-[12.5px]">
        {step === 1 && <StepSources />}
        {step === 2 && <StepCut />}
        {step === 3 && <StepColorSound />}
        {step === 4 && <StepSlides />}
      </div>
    </div>
  );
}

const H = ({ children }: { children: React.ReactNode }) => <h3 className="text-[11px] uppercase tracking-wide text-dim mb-1.5">{children}</h3>;

// ---------------------------------------------------------------- 1. sources

const TRAYS: { role: string; label: string; hint: string }[] = [
  { role: "front", label: "Фронт", hint: "камера на говорящего" },
  { role: "top", label: "Верх", hint: "вид на стол сверху" },
  { role: "lav", label: "Петличка", hint: "звук: WAV, AAC, MP3" },
];

/** «Из базы» next to the presentation tray: the PDFs in the Obsidian knowledge base. */
function KbPresentations({ onPick }: { onPick: (path: string) => void }) {
  const [items, setItems] = useState<{ path: string; name: string; folder: string }[] | null>(null);
  const open = async () => {
    try { setItems((await api.get<{ items: { path: string; name: string; folder: string }[] }>("/api/quick/presentations/kb")).items); }
    catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  if (!items) return <button className="btn btn-sm" onClick={open} title="PDF из базы знаний (Obsidian)">Из базы</button>;
  if (!items.length) return <span className="text-dim text-[11.5px]">в базе нет PDF</span>;
  return (
    <select className="input h-7 text-[11.5px] w-[150px]" defaultValue="" aria-label="Презентация из базы знаний"
      onChange={(e) => { if (e.target.value) { onPick(e.target.value); setItems(null); } }}>
      <option value="" disabled>из базы знаний…</option>
      {items.map((x) => <option key={x.path} value={x.path} title={x.path}>{x.name} · {x.folder}</option>)}
    </select>
  );
}

function StepSources() {
  const sources = useStore((s) => s.sources);
  const jobs = useStore((s) => s.jobs);
  const q = useQuick();
  const d = q.data?.doc;
  const [over, setOver] = useState<string | null>(null);
  const busy = jobs.some((j) => ["proxy", "audio", "asr", "peaks", "pack", "sync"].includes(j.kind) && (j.status === "running" || j.status === "queued"));

  const ensure = async () => { if (!q.data?.doc) q.set({ data: await api.post<QuickPayload>("/api/quick/create", {}) }); };
  const addFiles = async (paths: string[], role: string) => {
    if (!paths.length) return;
    try {
      await ensure();
      if (role === "presentation") {
        q.set({ data: await api.post<QuickPayload>("/api/quick/presentation", { path: paths[0] }) });
        return;
      }
      const before = new Set(useStore.getState().sources.map((s) => s.path));
      await api.post("/api/project/add", { path: paths[0], files: paths });
      await useStore.getState().refreshSources();
      const added = useStore.getState().sources.filter((s) => !before.has(s.path) || paths.includes(s.path));
      const roles = Object.fromEntries(added.map((s) => [s.id, role]));
      q.set({ data: await api.put<QuickPayload>("/api/quick/roles", { roles }) });
      await prepare();
    } catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const prepare = async () => {
    // proxies + transcripts, then sync by sound (angles and the lav) once they're ready
    await api.post("/api/project/ingest", {});
    const wait = () => new Promise<void>((res) => {
      const t = window.setInterval(() => {
        const act = useStore.getState().jobs.some((j) => ["proxy", "audio", "asr", "peaks"].includes(j.kind) && (j.status === "running" || j.status === "queued"));
        if (!act) { clearInterval(t); res(); }
      }, 1500);
    });
    await wait();
    await api.post("/api/sync", {});
  };
  const drop = (role: string) => (e: React.DragEvent) => {
    e.preventDefault(); setOver(null);
    const files = [...e.dataTransfer.files].map((f) => window.cutroom?.pathForFile(f) ?? "").filter(Boolean);
    addFiles(files, role);
  };
  const pick = async (role: string) => addFiles((await window.cutroom?.pickFiles()) ?? [], role);
  const setRole = async (id: string, role: string) => {
    try { await ensure(); q.set({ data: await api.put<QuickPayload>("/api/quick/roles", { roles: { [id]: role } }) }); }
    catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const vids = sources.filter((s) => !s.duplicate_of && !s.excluded);
  const pres = d?.presentation;
  // a sound-only file without an explicit role is a lav (same default as the server)
  const roleFor = (s: (typeof vids)[number]) => d?.roles?.[s.id] ?? (s.kind === "audio" ? "lav" : "other");
  // a project opened from a folder already has its files: prepare them too (once per set of unprepared files)
  const unprepared = vids.filter((s) => !s.missing && (!s.has.transcript || (s.kind === "video" && !s.has.proxy))).map((s) => s.id).join(",");
  const prepared = useRef("");
  useEffect(() => {
    if (!unprepared || busy || prepared.current === unprepared) return;
    prepared.current = unprepared;
    prepare().catch((e) => useStore.getState().say("err", (e as Error).message));
  }, [unprepared, busy]);

  return (
    <>
      <div className="space-y-2">
        {[...TRAYS, { role: "presentation", label: "Презентация", hint: "PDF (PowerPoint → Экспорт → PDF)" }].map((t) => (
          <div key={t.role} onDragOver={(e) => { e.preventDefault(); setOver(t.role); }} onDragLeave={() => setOver(null)} onDrop={drop(t.role)}
            className={cls("rounded-[8px] border border-dashed px-3 py-2 flex items-center gap-2", over === t.role ? "border-accent bg-[var(--accent-soft)]" : "border-line-strong")}>
            <div className="flex-1 min-w-0">
              <div className="text-fg">{t.label}</div>
              <div className="text-dim text-[11.5px]">
                {t.role === "presentation" ? (pres ? `${pres.name}: ${pres.pages.length} слайд.${pres.text_note ? " · текст слайдов из базы знаний" : ""}` : t.hint)
                  : `${vids.filter((s) => roleFor(s) === t.role).length} файл. · ${t.hint}`}
              </div>
            </div>
            {t.role === "presentation" && <KbPresentations onPick={(path) => addFiles([path], "presentation")} />}
            <button className="btn btn-sm" onClick={() => pick(t.role)}>Добавить</button>
          </div>
        ))}
        <p className="text-dim text-[11.5px]">Перетащите файлы в нужный лоток. Вид сбоку — не «верх»: отметьте его ниже как «Сбоку».</p>
      </div>
      {busy && <p className="text-accent pulse">Готовлю файлы: прокси, расшифровка, синхронизация по звуку…</p>}
      <KbStatus />
      <div>
        <H>Клипы проекта и их роль</H>
        <div className="space-y-1">
          {vids.map((s) => (
            <div key={s.id} className="flex items-center gap-2">
              {s.kind === "video" ? <img src={mediaUrl("thumb", s.id)} alt="" className="w-12 h-7 object-cover rounded bg-black shrink-0" /> : <div className="w-12 h-7 rounded bg-panel-3 grid place-items-center text-[10px] text-dim shrink-0">звук</div>}
              <span className="flex-1 min-w-0 truncate" title={s.path}>{s.name}</span>
              <select className="input h-6 text-[11.5px] w-[96px]" value={roleFor(s)}
                onChange={(e) => setRole(s.id, e.target.value)} aria-label={`Роль ${s.name}`}>
                {(s.kind === "audio" ? ["lav", "other"] : ["front", "top", "side", "other"]).map((r) => <option key={r} value={r}>{ROLE_NAMES[r]}</option>)}
              </select>
            </div>
          ))}
          {!vids.length && <p className="text-dim">Пока нет файлов.</p>}
        </div>
      </div>
      <button className="btn btn-primary w-full justify-center" disabled={!vids.some((s) => roleFor(s) === "front")}
        onClick={async () => { await ensure(); q.set({ step: 2 }); }}>Дальше: нарезка</button>
    </>
  );
}

type KbInfo = { info: { vault: string | null; kb: string | null; notes: number; renders: number; rules: number; skills: string[]; skills_missing: string[];
  obsidian_skills: string[]; ref_render: string | null; when: string } | null; index: Record<string, number>;
  project: { finished: boolean; version?: number; when?: string; can_finish: boolean; why_not: string | null; pending_assets: number } | null };

/** What the knowledge base gave this project: re-read at every project start. */
function KbStatus() {
  const jobs = useStore((s) => s.jobs);
  const [kb, setKb] = useState<KbInfo | null>(null);
  const job = jobs.filter((j) => j.kind === "kb").at(-1);
  useEffect(() => { api.get<KbInfo>("/api/kb").then(setKb).catch(() => {}); }, [job?.status]);
  const i = kb?.info;
  const reading = job && (job.status === "running" || job.status === "queued");
  return (
    <div className="rounded-[8px] border border-line px-3 py-2 text-[11.5px]">
      <div className="flex items-center gap-2">
        <span className="text-fg flex-1">База знаний</span>
        <button className="text-accent underline disabled:opacity-50" disabled={!!reading}
          onClick={() => api.post("/api/kb/refresh", {}).catch((e) => useStore.getState().say("err", e.message))}>{reading ? "читаю…" : "перечитать"}</button>
      </div>
      {!i ? <p className="text-dim">{reading ? "Перечитываю заметки Obsidian…" : "Ещё не прочитана."}</p> : !i.vault ? (
        <p className="text-warn">Хранилище Obsidian не найдено — укажите vault_path в настройках.</p>
      ) : (
        <div className="text-dim space-y-0.5 mt-0.5">
          <p>Заметок {i.notes}, правил {i.rules}, финальных рендеров завершённых проектов {i.renders}{i.ref_render ? ` · цвет-референс: ${i.ref_render}` : ""}.</p>
          <p>Этот проект: {kb?.project?.finished ? `завершён (v${kb.project.version}) — в базе знаний` : "в работе — в базу знаний и референсы не попадает до «Завершить проект» (шаг 4)"}.</p>
          <p>Ассеты: брендбук {kb?.index.brandbook ?? 0}, презентаций {kb?.index.presentation ?? 0}, графики {kb?.index.graphic ?? 0}.</p>
          <p>Скиллы для агентов: {i.skills.length ? i.skills.join(", ") : "—"}{i.skills_missing.length ? <span className="text-warn"> · не установлены: {i.skills_missing.join(", ")}</span> : null}</p>
          <p>Заметки после рендера: {i.obsidian_skills.length ? `скиллы ${i.obsidian_skills.join(", ")}` : <span className="text-warn">скиллы Obsidian для Claude не найдены — заметки пишет только скрипт</span>}</p>
        </div>
      )}
    </div>
  );
}

/** «Завершить проект»: only now the final export becomes a knowledge-base note and a reference. */
function FinishProject() {
  const jobs = useStore((s) => s.jobs);
  const [kb, setKb] = useState<KbInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const last = jobs.filter((j) => j.kind === "qassemble").at(-1);
  const load = () => api.get<KbInfo>("/api/kb").then(setKb).catch(() => {});
  useEffect(() => { load(); }, [last?.status, last?.id]);
  const st = kb?.project;
  const go = async () => {
    setBusy(true);
    try {
      await api.post("/api/kb/finish", {});
      useStore.getState().say("ok", "Проект завершён: финальный рендер записан в базу знаний и станет референсом для следующих проектов.");
      await load();
    } catch (e) { useStore.getState().say("err", (e as Error).message); }
    setBusy(false);
  };
  if (!st) return null;
  return (
    <div className="mt-3 pt-3 border-t border-line">
      <button className="btn w-full justify-center" disabled={!st.can_finish || busy} onClick={go}
        title={st.why_not ?? "Финальный экспорт текущей версии → заметка в Obsidian, ассеты в индекс, референс для новых проектов"}>
        {st.finished && !st.can_finish ? `Проект завершён (v${st.version})` : st.finished ? "Обновить итог проекта в базе знаний" : "Завершить проект → в базу знаний"}
      </button>
      <p className="text-dim text-[11.5px] mt-1.5">
        {st.why_not && !st.finished ? st.why_not
          : st.finished ? `В базе знаний с ${st.when?.slice(0, 16)}.${st.can_finish ? " Есть более новый финальный экспорт — можно обновить итог." : ""}`
          : `Пока проект не завершён, его рендеры, презентации и графика (${st.pending_assets}) в базу знаний и референсы не попадают.`}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- 2. cut

function StepCut() {
  const q = useQuick();
  const jobs = useStore((s) => s.jobs);
  const [prompt, setPrompt] = useState("");
  const job = jobs.filter((j) => j.kind === "qcut").at(-1);
  const running = job && (job.status === "running" || job.status === "queued");
  const preparing = jobs.some((j) => ["proxy", "audio", "asr", "peaks", "pack", "sync"].includes(j.kind) && (j.status === "running" || j.status === "queued"));
  const go = async () => {
    try { await api.post("/api/quick/cut", { prompt }); } catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const d = q.data?.doc;
  return (
    <>
      <div>
        <H>Что за ролик</H>
        <textarea className="input w-full py-2 leading-snug resize-y" style={{ height: 140 }} value={prompt} onChange={(e) => setPrompt(e.target.value)}
          placeholder="Например: ролик на 2 минуты о том, как устроен рынок. Когда раскладывают предметы или пишут на столе — показывай верх, остальное время фронт. Без оговорок." />
        <button className="btn btn-primary w-full justify-center mt-2" onClick={go} disabled={!prompt.trim() || !!running || preparing}>
          {running ? `ИИ режет… ${job?.eta ? `~${Math.ceil(job.eta)} с` : ""}` : preparing ? "Файлы ещё готовятся…" : d?.tracks.video.length ? "Нарезать заново" : "Нарезать"}
        </button>
        {job?.status === "error" && <p className="text-err mt-1">{job.error}</p>}
      </div>
      <div className="text-dim text-[11.5px] space-y-1">
        <p>ИИ выберет фразы и ракурс для каждого отрезка. Результат появится на таймлайне как предложение — нажмите «Применить» внизу.</p>
        <p>Потом можно править руками (перетаскивать, резать, растягивать) или просить ИИ в окне внизу, например «с 01:12 до 01:40 только верх».</p>
      </div>
      {d && d.tracks.video.length > 0 && <button className="btn w-full justify-center" onClick={() => q.set({ step: 3 })}>Дальше: цвет и звук</button>}
    </>
  );
}

// ---------------------------------------------------------------- 3. colour + sound

type Preset = { id: string; name: string; grade: Grade | null; kind: "brand" | "ready" | "user" | "ref" };

/** A graded still for a preset: shimmer while ffmpeg renders it, a retry if it fails. */
function PresetThumb({ src }: { src: string }) {
  const [st, setSt] = useState<"load" | "ok" | "err">("load");
  const [n, setN] = useState(0);
  useEffect(() => { setSt("load"); setN(0); }, [src]);
  // the server turns stills away while its queue is long: ask again by itself a few times
  const failed = () => { if (n < 5) window.setTimeout(() => setN((x) => x + 1), 2000); else setSt("err"); };
  return (
    <div className="relative w-full aspect-video bg-black">
      {st === "load" && <div className="absolute inset-0 shimmer grid place-items-center text-[10.5px] text-dim">кадр с цветом…</div>}
      {st === "err" && (
        <span role="button" tabIndex={0} className="absolute inset-0 grid place-items-center text-[10.5px] text-warn"
          onClick={(e) => { e.stopPropagation(); setSt("load"); setN((x) => x + 1); }}>кадр не получился · повторить</span>
      )}
      <img key={n} src={n ? `${src}&r=${n}` : src} alt="" className={cls("w-full h-full object-cover", st !== "ok" && "opacity-0")}
        onLoad={() => setSt("ok")} onError={failed} />
    </div>
  );
}

const sameGrade = (a: Grade | null | undefined, b: Grade | null | undefined) =>
  JSON.stringify(a ?? null) === JSON.stringify(b ?? null) || (!a && b?.preset === "none") || (!b && a?.preset === "none");

/** Apply a change to the video (or sound) blocks of the selection, or to the whole film. */
function onSelection(d: QDoc, track: "video" | "audio", fn: (b: { id: string } & Record<string, unknown>) => void): [QDoc, string] {
  const r = selRange();
  let nd = clone(d);
  if (r) {
    for (const f of r) nd = split(nd, nd.tracks[track].filter((b) => b.start < f && f < end(b)).map((b) => ({ track, id: b.id })), f);
    for (const b of nd.tracks[track]) if (b.start >= r[0] && end(b) <= r[1]) fn(b as never);
    return [nd, `выделение ${fmtT(r[0] / fpsOf(d))}–${fmtT(r[1] / fpsOf(d))}`];
  }
  for (const b of nd.tracks[track]) fn(b as never);
  return [nd, "весь ролик"];
}

function StepColorSound() {
  const q = useQuick();
  const d = shownDoc(q);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [brand, setBrand] = useState<{ found: boolean; need: string | null; note: string | null } | null>(null);
  const [temp, setTemp] = useState(0);
  const [sat, setSat] = useState(1);
  const [name, setName] = useState("");
  const [tracks, setTracks] = useState<Track[]>([]);
  const load = () => api.get<{ presets: Preset[]; brand: typeof brand }>("/api/quick/presets").then((r) => { setPresets(r.presets); setBrand(r.brand); });
  useEffect(() => { load(); api.get<{ tracks: Track[] }>("/api/music").then((r) => setTracks(r.tracks)).catch(() => {}); }, []);
  // the frame the preset thumbnails show: it holds still during playback — following the playhead asked the
  // server for 5 new 4K stills every half second, and the queue starved the proxy stream (the picture froze)
  const still = useRef<{ src: string; at: number } | null>(null);
  if (!d) return <p className="text-dim">Сначала нарезка (шаг 2).</p>;
  const fps = fpsOf(d);
  const f = Math.round(q.time * fps);
  const v = d.tracks.video.find((b) => f >= b.start && f < end(b)) ?? d.tracks.video[0];
  const at = v ? v.in + Math.max(0, f - v.start) / fps : 0;
  if (v && (!q.playing || !still.current)) still.current = { src: v.src, at: Math.round(at * 2) / 2 };
  const r = selRange();
  const locked = !q.data?.gate_open || !!q.propDoc;

  const withAdjust = (g: Grade | null): Grade | null => {
    if (!temp && sat === 1) return g;
    const out: Grade = JSON.parse(JSON.stringify(g ?? {}));
    if (temp) out.temperature = (out.temperature ?? 0) + temp;
    if (sat !== 1) out.cdl = { ...(out.cdl ?? {}), sat: +(((out.cdl?.sat ?? 1) * sat)).toFixed(3) };
    return out;
  };
  const current = (v?.color ?? d.grade ?? null) as Grade | null;
  const applyGrade = async (p: Preset) => {
    const g = withAdjust(p.grade);
    if (r) {
      const [nd, where] = onSelection(d, "video", (b) => { b.color = g; });
      await q.commit(nd, `цвет «${p.name}»: ${where}`);
    } else {
      const nd = clone(d);
      nd.grade = g;
      nd.tracks.video.forEach((b) => { delete b.color; });
      await q.commit(nd, `цвет «${p.name}»: весь ролик`);
    }
  };
  const save = async () => {
    const cur = (v?.color ?? d.grade ?? {}) as Grade;
    try { await api.post("/api/quick/presets", { name, grade: withAdjust(cur) ?? {} }); setName(""); load(); }
    catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const setStyle = async (style: string, label: string) => {
    const [nd, where] = onSelection(d, "audio", (b) => { b.style = style; });
    await q.commit(nd, `звук «${label}»: ${where}`);
  };
  const setMusic = async (track: string) => {
    try { q.set({ data: await api.put<QuickPayload>("/api/quick/music", { track: track || null }) }); } catch (e) { useStore.getState().say("err", (e as Error).message); }
  };

  return (
    <>
      <div>
        <H>Цвет · {r ? "к выделенному" : "ко всему ролику"}</H>
        <p className={cls("text-[11.5px] mb-2", brand?.found ? "text-dim" : "text-warn")}>
          {brand?.found ? "Брендбук: заметка в Obsidian «Брендбук — визуал»." : (brand?.need ?? "Брендбук не найден.")}
        </p>
        <div className="grid grid-cols-2 gap-2">
          {presets.map((p) => (
            <button key={p.id} disabled={locked || !v} onClick={() => applyGrade(p)} title={p.kind === "ref" ? "Цвет последнего финального рендера из базы знаний" : p.name}
              className={cls("text-left rounded-[8px] border overflow-hidden bg-panel-2 disabled:opacity-50", sameGrade(current, withAdjust(p.grade)) ? "border-accent ring-1 ring-accent" : "border-line hover:border-accent")}>
              {v && still.current && <PresetThumb src={frameUrl(still.current.src, still.current.at, withAdjust(p.grade), "q") + "&w=480"} />}
              <div className="px-2 py-1 text-[11.5px] truncate">
                {sameGrade(current, withAdjust(p.grade)) ? "✓ " : p.kind === "brand" ? "★ " : p.kind === "ref" ? "↺ " : p.kind === "user" ? "• " : ""}{p.name}
              </div>
            </button>
          ))}
        </div>
        <details className="mt-2">
          <summary className="text-dim cursor-pointer">Тоньше и свой пресет</summary>
          <label className="flex items-center gap-2 mt-2">Тепло<input type="range" min={-50} max={50} value={temp} onChange={(e) => setTemp(+e.target.value)} className="flex-1 min-w-0" /><span className="w-8 text-right tabular-nums">{temp}</span></label>
          <label className="flex items-center gap-2">Насыщ.<input type="range" min={0.5} max={1.5} step={0.05} value={sat} onChange={(e) => setSat(+e.target.value)} className="flex-1 min-w-0" /><span className="w-8 text-right tabular-nums">{sat.toFixed(2)}</span></label>
          <p className="text-dim text-[11.5px]">Ползунки добавляются к пресету, который вы нажмёте. Мониторы показывают цвет сразу (приближение), точный рендер блоков идёт в фоне.</p>
          <div className="flex gap-1.5 mt-1">
            <input className="input flex-1 h-7" value={name} onChange={(e) => setName(e.target.value)} placeholder="Имя пресета" />
            <button className="btn btn-sm" disabled={!name.trim()} onClick={save} title="Сохранить цвет текущего блока как пресет">Сохранить</button>
          </div>
        </details>
      </div>
      <div>
        <H>Звук петлички · {r ? "к выделенному" : "ко всему ролику"}</H>
        <div className="grid grid-cols-2 gap-1.5">
          {Object.entries(q.data?.styles ?? {}).map(([k, label]) => (
            <button key={k} className="btn btn-sm justify-center" disabled={locked} onClick={() => setStyle(k, label)}>{label}</button>
          ))}
        </div>
        <p className="text-dim text-[11.5px] mt-1.5">Шумоподавление, выравнивание громкости и компрессия входят в «Чистую речь». Варианты для одного блока с прослушиванием — правый клик по дорожке «Звук».</p>
        <label className="block mt-2 text-dim">Музыка для подложки</label>
        <select className="input w-full h-7 mt-1" value={d.music?.track ?? ""} onChange={(e) => setMusic(e.target.value)} disabled={locked}>
          <option value="">без музыки</option>
          {tracks.map((t) => <option key={t.id} value={t.id}>{t.file}{t.library_name ? ` · ${t.library_name}` : ""}</option>)}
        </select>
        {!tracks.length && <p className="text-dim text-[11.5px] mt-1">Полка пуста: треки из разрешённых библиотек добавляются в полном режиме (вкладка «Звук»).</p>}
      </div>
      <button className="btn w-full justify-center" onClick={() => q.set({ step: 4 })}>Дальше: слайды и графика</button>
    </>
  );
}

// ---------------------------------------------------------------- 4. slides + cards

function StepSlides() {
  const q = useQuick();
  const d = shownDoc(q);
  const jobs = useStore((s) => s.jobs);
  if (!d) return <p className="text-dim">Сначала нарезка (шаг 2).</p>;
  const fps = fpsOf(d);
  const pres = d.presentation;
  const locked = !q.data?.gate_open || !!q.propDoc;
  const selSlide = d.tracks.slides.find((s) => q.sel.some((r) => r.id === s.id));
  const place = async (page: number) => {
    const nd = clone(d);
    const start = Math.round(q.time * fps), len = Math.round(8 * fps);
    nd.tracks.slides = nd.tracks.slides.flatMap((s) => {
      if (end(s) <= start || s.start >= start + len) return [s];
      const out = [];
      if (s.start < start) out.push({ ...s, len: start - s.start });
      if (end(s) > start + len) out.push({ ...s, id: s.id + "b", start: start + len, len: end(s) - start - len });
      return out;
    });
    nd.tracks.slides.push({ id: "s" + Math.random().toString(16).slice(2, 9), start, len, page, layout: "side" });
    nd.tracks.slides.sort((a, b) => a.start - b.start);
    await q.commit(nd, `слайд ${page} на ${fmtT(q.time)}`);
  };
  const layout = async (l: "side" | "full") => {
    if (!selSlide) return;
    const nd = clone(d);
    nd.tracks.slides.find((s) => s.id === selSlide.id)!.layout = l;
    await q.commit(nd, `слайд ${selSlide.page}: ${l === "full" ? "во весь кадр" : "панелью"}`);
  };
  const cardFromSlide = async () => {
    if (!selSlide) return;
    const nd = clone(d);
    const id = "o" + Math.random().toString(16).slice(2, 9);
    const len = Math.min(selSlide.len, Math.round(8 * fps));
    nd.tracks.overlays = nd.tracks.overlays.filter((o) => end(o) <= selSlide.start || o.start >= selSlide.start + len);
    nd.tracks.overlays.push({ id, start: selSlide.start, len, slide: selSlide.id, page: selSlide.page, hint: `по слайду ${selSlide.page}`, style: "cream", status: "placeholder" });
    nd.tracks.overlays.sort((a, b) => a.start - b.start);
    await q.commit(nd, `карточка по слайду ${selSlide.page}`);
    try { await api.post(`/api/quick/cards/${id}/build`, {}); } catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const exportJob = jobs.filter((j) => j.kind === "qassemble" && j.source === "final").at(-1);
  const exporting = jobs.some((j) => (j.kind === "qassemble" || j.kind === "qblock" || j.kind === "qplan") && j.status !== "done" && j.status !== "error" && j.status !== "cancelled" && j.status !== "cached")
    && exportJob && (exportJob.status === "running" || exportJob.status === "queued");
  const fin = q.data?.build.final;

  return (
    <>
      <div>
        <H>Слайды</H>
        {!pres ? (
          <p className="text-dim">Презентация не загружена — добавьте PDF на шаге 1.</p>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-1.5">
              {pres.pages.map((p) => (
                <button key={p.n} disabled={locked} onClick={() => place(p.n)} title={`Поставить слайд ${p.n} под бегунок (8 с)`}
                  className="rounded border border-line hover:border-accent overflow-hidden disabled:opacity-50">
                  <img src={quickUrl(`/media/quick/slide/${p.n}?thumb=1`)} alt={`слайд ${p.n}`} className="w-full aspect-video object-cover" loading="lazy" />
                </button>
              ))}
            </div>
            <p className="text-dim text-[11.5px] mt-1">Клик — слайд под бегунок. Двойной клик по дорожке «Презентация» — выбор слайда для места.</p>
          </>
        )}
      </div>
      {selSlide && (
        <div className="space-y-1.5">
          <H>Слайд {selSlide.page}</H>
          <div className="flex gap-1.5">
            <button className="chip" aria-pressed={selSlide.layout !== "full"} onClick={() => layout("side")}>Панелью справа</button>
            <button className="chip" aria-pressed={selSlide.layout === "full"} onClick={() => layout("full")}>Во весь кадр</button>
          </div>
          <button className="btn btn-sm w-full justify-center" onClick={cardFromSlide} disabled={locked}>Инфографика по этому слайду</button>
        </div>
      )}
      <div>
        <H>Карточки</H>
        {d.tracks.overlays.length ? d.tracks.overlays.map((o) => (
          <button key={o.id} onClick={() => q.set({ sel: [{ track: "overlays", id: o.id }], time: o.start / fps })} className="w-full flex items-center gap-2 py-1 text-left hover:text-fg">
            {o.files?.poster ? <img src={quickUrl(`/media/quick/card/${o.id}/poster?v=${o.files.v}`)} alt="" className="w-12 h-7 object-cover rounded bg-panel-3" /> : <span className="w-12 h-7 rounded bg-panel-3" />}
            <span className="flex-1 min-w-0 truncate">{(o.spec?.title as string) || o.hint || "карточка"}</span>
            <span className={cls("text-[11px]", o.status === "error" ? "text-err" : o.status === "ready" ? "text-dim" : "text-accent")}>
              {o.status === "ready" ? fmtT(o.start / fps) : o.status === "error" ? "ошибка" : "строится…"}
            </span>
          </button>
        )) : <p className="text-dim">Пока нет. Выделите слайд и нажмите «Инфографика по этому слайду» или попросите ИИ внизу.</p>}
        <p className="text-dim text-[11.5px] mt-1">Правый клик по карточке на таймлайне — варианты оформления.</p>
      </div>
      <div className="border-t border-line pt-3">
        <button className="btn btn-primary w-full justify-center" disabled={locked || !!exporting} onClick={() => api.post("/api/quick/export", {}).catch((e) => useStore.getState().say("err", e.message))}>
          {exporting ? "Экспортирую…" : "Экспорт 1080p"}
        </button>
        {fin && (
          <p className="text-dim text-[11.5px] mt-1.5">
            Готово v{fin.version}: {fmtT(fin.duration)} · пересобрано {fin.rebuilt.length}, из кэша {fin.cached.length}.{" "}
            <button className="text-accent underline" onClick={() => window.cutroom?.revealInFolder(fin.file)}>Показать файл</button>
          </p>
        )}
        <FinishProject />
      </div>
    </>
  );
}
