import { useEffect, useMemo, useRef, useState } from "react";
import { api, musicUrl, sampleUrl, type AudioInfo, type Track } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";
import { cls, fmtDur } from "../util";

const DENOISE: Record<string, string> = { off: "выкл", soft: "мягко", medium: "средне", strong: "сильно" };
const COMPRESS: Record<string, string> = { off: "выкл", soft: "мягко", speech: "плотно (речь)" };

export function AudioPanel() {
  const audio = useStore((s) => s.audio);
  const edl = useStore((s) => s.edl?.edl);
  const gate = useStore((s) => s.strategy?.gate_open);
  if (!edl) {
    return <p className="p-3 text-dim text-[12.5px]">Звук настраивается на черновике: утвердите стратегию, и после сборки здесь появятся микрофоны дублей, обработка речи и музыка.</p>;
  }
  const save = async (body: Record<string, unknown>, note: string) => {
    if (!gate) return useStore.getState().say("info", "Менять монтаж можно после утверждения стратегии.");
    try { useStore.setState({ audio: await api.put<AudioInfo>("/api/audio", { ...body, note }) }); await useStore.getState().loadEdl(); }
    catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  return (
    <div className="flex flex-col">
      <Mics audio={audio} save={save} />
      <Cleanup audio={audio} save={save} />
      <Music audio={audio} save={save} />
    </div>
  );
}

type Save = (body: Record<string, unknown>, note: string) => Promise<void>;

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="p-3 border-b border-line flex flex-col gap-2 text-[12.5px]">
      <div className="flex items-baseline gap-2"><span className="label">{title}</span>{hint && <span className="text-faint text-[11.5px]">{hint}</span>}</div>
      {children}
    </section>
  );
}

function Mics({ audio, save }: { audio: AudioInfo | null; save: Save }) {
  if (!audio?.groups.length) {
    return <Section title="Микрофон"><p className="text-dim">Дубли не сведены по звуку — каждая камера звучит своим микрофоном. Сведите ракурсы во вкладке «Источники», и сюда подтянется петличка или рекордер, если они есть в папке.</p></Section>;
  }
  return (
    <Section title="Микрофон по дублям" hint="петличка подставляется сама">
      {audio.groups.map((g) => (
        <div key={g.id} className="flex flex-col gap-1">
          <span className="text-faint text-[11.5px]">Дубль {g.id.replace("take_", "")}</span>
          <div className="flex flex-wrap gap-1">
            <button className="chip" aria-pressed={!g.effective} onClick={() => save({ sources: { [g.id]: "" } }, "звук: свои микрофоны камер")}>
              как у камеры
            </button>
            {g.members.map((m) => (
              <button key={m.id} className="chip" aria-pressed={g.effective === m.id}
                title={`${m.name} · сдвиг ${m.offset >= 0 ? "+" : ""}${m.offset.toFixed(3)} с · ${m.status === "ok" || m.status === "ref" ? "сведено по звуку" : "проверьте сведение"}`}
                onClick={() => save({ sources: { [g.id]: m.id } }, `звук: ${m.id} для дубля ${g.id.replace("take_", "")}`)}>
                <Icon name={m.audio_only ? "mic" : "film"} size={12} /> {m.id}
                {m.audio_only && <span className="text-faint">петличка</span>}
              </button>
            ))}
          </div>
          {g.effective && !g.chosen && <span className="text-[11.5px] text-ok">Выбрано автоматически: {g.effective} — отдельная запись звука. Смещение посчитано по звуку, расхождение с губами меньше кадра.</span>}
        </div>
      ))}
    </Section>
  );
}

function Seg({ value, options, onChange }: { value: string; options: Record<string, string>; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-wrap rounded-[6px] border border-line-strong overflow-hidden w-fit">
      {Object.entries(options).map(([v, l]) => (
        <button key={v} onClick={() => onChange(v)} aria-pressed={value === v}
          className={cls("h-6 px-2 text-[12px] border-r border-line-strong last:border-r-0", value === v ? "bg-accent-soft text-accent" : "text-dim hover:text-fg")}>{l}</button>
      ))}
    </div>
  );
}

function Cleanup({ audio, save }: { audio: AudioInfo | null; save: Save }) {
  const jobs = useStore((s) => s.jobs);
  const time = useStore((s) => s.time);
  const [sample, setSample] = useState<{ before: string; after: string; chain: string; v: number } | null>(null);
  const [which, setWhich] = useState<"before" | "after">("after");
  const job = jobs.filter((j) => j.kind === "sample").sort((a, b) => b.id - a.id)[0];
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    if (job?.status === "done" && job.note) {
      try { const r = JSON.parse(job.note); setSample({ ...r, v: job.id }); } catch { /* not json */ }
    }
  }, [job?.status, job?.id]);
  const fx = audio?.fx ?? { highpass: true, denoise: "off", deess: false, compress: "off" };
  const set = (patch: Partial<typeof fx>, note: string) => save({ fx: patch }, note);
  const busy = job && (job.status === "queued" || job.status === "running");
  return (
    <Section title="Обработка речи" hint="применяется ко всей дорожке в рендере">
      <div className="grid grid-cols-[110px_1fr] gap-x-2 gap-y-2 items-center">
        <span className="text-faint">Шумоподавление</span>
        <Seg value={fx.denoise} options={DENOISE} onChange={(v) => set({ denoise: v }, `шумоподавление: ${DENOISE[v]}`)} />
        <span className="text-faint">Компрессия</span>
        <Seg value={fx.compress} options={COMPRESS} onChange={(v) => set({ compress: v }, `компрессия: ${COMPRESS[v]}`)} />
        <span className="text-faint">Свист «с», «ш»</span>
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={fx.deess} onChange={(e) => set({ deess: e.target.checked }, `де-эссер: ${e.target.checked ? "вкл" : "выкл"}`)} /> смягчить</label>
        <span className="text-faint">Гул и ветер</span>
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={fx.highpass} onChange={(e) => set({ highpass: e.target.checked }, `срез низа: ${e.target.checked ? "вкл" : "выкл"}`)} /> срезать ниже 80 Гц</label>
      </div>
      <div className="flex flex-wrap items-center gap-2 mt-1">
        <button className="btn btn-sm" disabled={busy} onClick={() => api.post("/api/audio/sample", { time, seconds: 10 }).catch((e) => useStore.getState().say("err", e.message))}
          title="10 секунд от точки плеера: как записано и как будет">
          <Icon name="wave" size={13} /> {busy ? "Готовлю пробник…" : "Прослушать 10 с: до / после"}
        </button>
        {sample && (
          <>
            <div className="flex rounded-[6px] border border-line-strong overflow-hidden">
              {(["before", "after"] as const).map((w) => (
                <button key={w} aria-pressed={which === w} onClick={() => { const t = ref.current?.currentTime ?? 0; setWhich(w); setTimeout(() => { if (ref.current) { ref.current.currentTime = t; ref.current.play().catch(() => {}); } }, 50); }}
                  className={cls("h-6 px-2 text-[12px]", which === w ? "bg-accent-soft text-accent" : "text-dim")}>{w === "before" ? "до" : "после"}</button>
              ))}
            </div>
            <audio ref={ref} controls src={sampleUrl(which === "before" ? sample.before : sample.after, sample.v)} className="h-7 max-w-full" />
          </>
        )}
      </div>
      <p className="text-[11.5px] text-faint">В живом превью обработка не слышна — она делается при рендере. Пробник «до/после» собирается из реального звука с теми же настройками.</p>
    </Section>
  );
}

function Music({ audio, save }: { audio: AudioInfo | null; save: Save }) {
  const edl = useStore((s) => s.edl?.edl);
  const chips = useStore((s) => s.chips);
  const jobs = useStore((s) => s.jobs);
  const [lib, setLib] = useState<{ dir: string; libraries: Record<string, { name: string; url: string; licence: string }>; tracks: Track[] } | null>(null);
  const [ranked, setRanked] = useState<Track[]>([]);
  const [playing, setPlaying] = useState<string | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const scanJob = jobs.filter((j) => j.kind === "music").sort((a, b) => b.id - a.id)[0];
  const target = edl?.total_duration_s ?? 60;
  const pace = typeof chips.pace === "string" ? chips.pace : "";
  useEffect(() => {
    api.get<typeof lib>("/api/music").then(setLib).catch(() => {});
    api.get<Track[]>(`/api/music/rank?target=${target.toFixed(1)}&pace=${encodeURIComponent(pace)}`).then(setRanked).catch(() => {});
  }, [scanJob?.status, target, pace]);
  useEffect(() => () => player.current?.pause(), []);
  const bed = audio?.music;
  const play = (t: Track) => {
    if (!player.current) player.current = new Audio();
    if (playing === t.id) { player.current.pause(); setPlaying(null); return; }
    player.current.src = musicUrl(t.id);
    player.current.currentTime = 15;   // skip the intro: judge the body of the track
    player.current.play().catch(() => {});
    setPlaying(t.id);
  };
  const top = useMemo(() => ranked.slice(0, 6), [ranked]);
  return (
    <Section title="Музыка" hint="только из разрешённых бесплатных библиотек">
      {bed && (
        <div className="rounded-[8px] border border-accent/50 bg-accent-soft p-2.5 flex flex-col gap-2">
          <div className="flex items-center gap-2"><Icon name="wave" size={13} /><span className="font-medium truncate">{bed.title}</span>
            <span className="flex-1" /><button className="btn btn-ghost btn-sm h-6" onClick={() => save({ clear_music: true }, "музыка убрана")}>Убрать</button></div>
          <Slider label="Громкость" value={bed.gain_db} min={-30} max={-6} unit="дБ" onChange={(v) => save({ music: { gain_db: v } }, `музыка: громкость ${v} дБ`)} />
          <Slider label="Тише под речью" value={bed.duck_db} min={-24} max={0} unit="дБ" onChange={(v) => save({ music: { duck_db: v } }, `музыка: приглушение ${v} дБ`)} />
          <span className="text-[11.5px] text-faint">В живом превью музыка уже звучит и приглушается под речью; в рендере — так же, компрессором по голосу.</span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <button className="btn btn-sm" disabled={scanJob?.status === "running"} onClick={() => api.post("/api/music/scan")}>
          <Icon name="refresh" size={13} /> {scanJob?.status === "running" ? "Слушаю треки…" : "Обновить полку"}
        </button>
        {lib && window.cutroom && <button className="btn btn-ghost btn-sm" onClick={() => window.cutroom!.revealInFolder(lib.dir)}>Открыть папку</button>}
        {lib && <span className="text-faint text-[11px] truncate" title={lib.dir}>{lib.tracks.length} треков</span>}
      </div>
      {lib && !lib.tracks.length && (
        <p className="text-dim">Скачайте треки с сайтов ниже и положите в папку <span className="mono text-[11.5px] break-all">{lib.dir}</span>, затем «Обновить полку». Cutroom послушает каждый трек и подберёт подходящие к монтажу.</p>
      )}
      {top.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-faint text-[11.5px]">Подходят к ролику {fmtDur(target)}{pace ? `, темп «${pace}»` : ""}:</span>
          {top.map((t, k) => (
            <div key={t.id} className={cls("rounded-[6px] border p-2 flex flex-col gap-1", bed?.track === t.id ? "border-accent/60" : "border-line")}>
              <div className="flex items-center gap-2">
                <button className="btn btn-ghost btn-sm h-6 w-6 justify-center p-0" onClick={() => play(t)} aria-label={playing === t.id ? "Стоп" : "Слушать"}>
                  <Icon name={playing === t.id ? "pause" : "play"} size={12} />
                </button>
                <span className="font-medium truncate flex-1" title={t.file}>{k + 1}. {t.file.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ")}</span>
                <span className="tnum text-faint text-[11px]">{Math.round(t.analysis?.bpm ?? 0)} уд/мин · {fmtDur(t.analysis?.duration ?? 0)}</span>
              </div>
              <div className="flex items-center gap-2 text-[11px]">
                <span className="w-16 h-1.5 rounded-full bg-panel-3 overflow-hidden" title="энергия"><span className="block h-full bg-accent" style={{ width: `${(t.analysis?.energy ?? 0) * 100}%` }} /></span>
                <span className={cls("badge", t.library ? "" : "badge-warn")} title={t.licence ?? "Укажите, откуда трек"}>{t.library_name ?? "источник не указан"}</span>
                <span className="flex-1" />
                {bed?.track !== t.id && (
                  <button className="btn btn-sm h-6" onClick={() => save({ music: { track: t.id, path: t.path, title: t.file, start_in_output: 0, offset: 15, gain_db: -18, duck_db: -12, fade_in: 1.5, fade_out: 2.5, loop: (t.analysis?.duration ?? 0) < target } }, `музыка: ${t.file}`)}>
                    В монтаж
                  </button>
                )}
              </div>
              {t.why && <p className="text-[11px] text-dim">{t.why.join(" · ")}</p>}
            </div>
          ))}
        </div>
      )}
      {lib && (
        <details className="text-[12px]">
          <summary className="cursor-pointer text-dim">Где брать музыку (разрешённые библиотеки)</summary>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {Object.entries(lib.libraries).map(([k, l]) => (
              <li key={k} className="flex flex-col">
                <button className="text-left text-accent underline-offset-2 hover:underline w-fit" onClick={() => window.cutroom ? window.cutroom.openExternal(l.url) : window.open(l.url, "_blank")}>{l.name}</button>
                <span className="text-faint text-[11.5px]">{l.licence}</span>
              </li>
            ))}
          </ul>
          <p className="text-faint text-[11.5px] mt-1.5">Cutroom не скачивает музыку сам: трек выбирает человек, Cutroom только подбирает из того, что лежит в папке.</p>
        </details>
      )}
    </Section>
  );
}

function Slider({ label, value, min, max, unit, onChange }: { label: string; value: number; min: number; max: number; unit: string; onChange: (v: number) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <label className="grid grid-cols-[110px_1fr_48px] items-center gap-2 text-[12px]">
      <span className="text-faint">{label}</span>
      <input type="range" min={min} max={max} step={1} value={v} onChange={(e) => setV(Number(e.target.value))} onPointerUp={() => v !== value && onChange(v)} onKeyUp={() => v !== value && onChange(v)} />
      <span className="tnum text-right">{v} {unit}</span>
    </label>
  );
}
