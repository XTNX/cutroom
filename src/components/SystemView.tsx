import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { cls, fmtBytes } from "../util";
import { BenchPanel } from "./BenchPanel";
import { claudeLogin, refreshClaude, useClaude } from "./ClaudeBadge";

type Sys = {
  cpu: { name: string | null; cores: number; threads: number };
  ram_gb: number;
  gpus: { name: string; vram_mb: number; driver: string }[];
  cuda: boolean;
  device: string;
  ffmpeg: { version: string | null; hw_encoders: string[]; has_zscale: boolean; ffmpeg: string | null };
  models: { name: string; engine: string; repo: string; size: number; downloaded: number; ready: boolean }[];
  eta_per_hour: Record<string, { rtf: number; minutes_per_hour: number; measured: boolean }>;
  recommend: { model: string; device: string; compute: string; why: string };
  video_use: { path: string; sha: string | null };
  gigaam: boolean;
  whisperx: boolean;
};
type Settings = Record<string, unknown> & { asr_engine: string; asr_model: string; asr_language: string; asr_device: string; cloud_asr_enabled: boolean };

export function SystemView() {
  const [sys, setSys] = useState<Sys | null>(null);
  const [st, setSt] = useState<Settings | null>(null);
  const jobs = useStore((s) => s.jobs);
  const modelJobs = jobs.filter((j) => j.kind === "model");
  const reload = () => {
    api.get<Sys>("/api/system").then(setSys).catch((e) => useStore.getState().say("err", e.message));
    api.get<Settings>("/api/settings").then(setSt);
  };
  useEffect(reload, [modelJobs.filter((j) => j.status === "done").length]);
  const patch = async (p: Partial<Settings>) => setSt(await api.patch<Settings>("/api/settings", p));

  if (!sys || !st) return <div className="flex-1 grid place-items-center text-dim"><span className="pulse">Опрашиваю железо…</span></div>;
  const gpu = sys.gpus[0];

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="max-w-[1080px] mx-auto px-4 py-5 flex flex-col gap-5">
        <header className="flex items-baseline gap-3">
          <h1 className="text-[18px] font-semibold">Система</h1>
          <span className="text-dim text-[12.5px]">Всё считается локально. Облако и ключи не нужны; исходники не покидают компьютер.</span>
        </header>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Card title="Видеокарта">
            {gpu ? <><Big>{gpu.name}</Big><Row k="Видеопамять" v={`${(gpu.vram_mb / 1024).toFixed(1)} ГБ`} /><Row k="Драйвер" v={gpu.driver} />
              <Row k="CUDA для Whisper" v={sys.cuda ? "работает" : "недоступна"} tone={sys.cuda ? "ok" : "err"} /></>
              : <><Big>Нет NVIDIA GPU</Big><p className="text-warn text-[12px]">Расшифровка пойдёт на CPU: медленно, в фоне.</p></>}
          </Card>
          <Card title="Процессор и память">
            <Big>{sys.cpu.name ?? "CPU"}</Big>
            <Row k="Ядра / потоки" v={`${sys.cpu.cores} / ${sys.cpu.threads}`} />
            <Row k="ОЗУ" v={`${sys.ram_gb} ГБ`} />
          </Card>
          <Card title="ffmpeg">
            <Big>{sys.ffmpeg.version?.match(/version (\S+)/)?.[1] ?? "не найден"}</Big>
            <Row k="Аппаратные кодеры" v={sys.ffmpeg.hw_encoders.join(", ") || "нет"} tone={sys.ffmpeg.hw_encoders.length ? "ok" : undefined} />
            <Row k="Прокси" v={sys.ffmpeg.hw_encoders.includes("h264_nvenc") ? "NVENC (быстро)" : "libx264 (CPU)"} />
            <Row k="Финал" v="CPU libx264/libx265 или ProRes" />
            <Row k="zscale (HDR→SDR)" v={sys.ffmpeg.has_zscale ? "есть" : "нет"} tone={sys.ffmpeg.has_zscale ? "ok" : "err"} />
          </Card>
        </div>

        <Card title="Расшифровка речи">
          <div className="grid md:grid-cols-[1fr_1fr] gap-5">
            <div className="flex flex-col gap-3">
              {st.asr_engine === "gigaam" ? (
                <p className="text-[12.5px]"><span className="text-accent font-medium">Основной движок: GigaAM {st.asr_model}</span> — выбран по замеру на ваших записях (сохраняет больше слов-паразитов). <span className="text-dim">Сомнительные слова подсвечиваются сверкой с Whisper.</span></p>
              ) : (
                <p className="text-[12.5px]"><span className="text-accent font-medium">Рекомендация: {sys.recommend.model}</span> на {sys.recommend.device.toUpperCase()} ({sys.recommend.compute}). <span className="text-dim">{sys.recommend.why}</span></p>
              )}
              <Field label="Движок">
                <Seg value={st.asr_engine} onChange={(v) => patch({ asr_engine: v, asr_model: v === "gigaam" ? "v3_e2e_rnnt" : "large-v3" })}
                  options={[["faster-whisper", "faster-whisper"], ["gigaam", `GigaAM${sys.gigaam ? "" : " (не установлен)"}`]]} />
              </Field>
              <Field label="Модель">
                <Seg value={st.asr_model} onChange={(v) => patch({ asr_model: v })}
                  options={sys.models.filter((m) => m.engine === st.asr_engine).map((m) => [m.name, m.name + (m.ready ? "" : " ↓")])} />
              </Field>
              <Field label="Язык">
                <Seg value={st.asr_language || "auto"} onChange={(v) => patch({ asr_language: v === "auto" ? "" : v })}
                  options={[["ru", "русский"], ["en", "английский"], ["auto", "по первым 30 с"]]} />
              </Field>
              <Field label="Устройство">
                <Seg value={st.asr_device} onChange={(v) => patch({ asr_device: v })} options={[["auto", "авто"], ["cuda", "GPU"], ["cpu", "CPU"]]} />
              </Field>
              <p className="text-[11.5px] text-faint">
                Параметры зашиты ради монтажа: пословные метки, без VAD (он съедает паузы и филлеры), temperature 0, без опоры на прошлый текст,
                подсказка с «э-э, ммм». Кэш по содержимому файла + модель + параметры: смена модели = новый ключ, старый результат сохраняется.
                Выравнивание WhisperX: {sys.whisperx ? "установлено" : <span className="text-warn">не установлено — паддинг стыков 100 мс вместо 30–50</span>}.
              </p>
            </div>
            <div className="flex flex-col gap-2">
              <div className="label">Модели</div>
              {sys.models.map((m) => {
                if (m.engine === "gigaam" && !m.ready) return (
                  <div key={m.name} className="flex items-center gap-2 text-[12.5px] border border-line rounded-[8px] px-3 py-2 text-dim">
                    <span className="font-medium w-[120px] text-fg">{m.name}</span>GigaAM: веса скачаются при первой расшифровке (зеркало ai-sage на Hugging Face)
                  </div>
                );
                const j = modelJobs.filter((x) => x.label.includes(m.name + " ")).sort((a, b) => b.id - a.id)[0];
                const running = j && (j.status === "running" || j.status === "queued");
                return (
                  <div key={m.name} className="flex items-center gap-2 text-[12.5px] border border-line rounded-[8px] px-3 py-2">
                    <span className="font-medium w-[120px]">{m.name}</span>
                    <span className="text-dim tnum w-[70px]">{fmtBytes(m.size)}</span>
                    {m.ready ? <span className="badge badge-ok">{m.engine === "gigaam" ? "GigaAM · на диске" : "на диске"}</span>
                      : running ? <div className="flex-1 flex items-center gap-2"><div className="flex-1 h-1.5 bg-panel-3 rounded-full overflow-hidden"><div className="h-full bg-accent" style={{ width: `${(j!.progress * 100).toFixed(1)}%` }} /></div><span className="tnum text-dim">{Math.round(j!.progress * 100)}%</span></div>
                      : <button className="btn btn-sm" onClick={() => api.post("/api/system/models/download", { model: m.name })}>Скачать</button>}
                    <span className="flex-1" />
                    {sys.eta_per_hour[m.name] && (
                      <span className="tnum text-dim text-[11.5px]" title={sys.eta_per_hour[m.name].measured ? "Замерено на этом компьютере" : "Оценка до первого замера"}>
                        час аудио ≈ {sys.eta_per_hour[m.name].minutes_per_hour} мин{sys.eta_per_hour[m.name].measured ? " · замер" : " · оценка"}
                      </span>
                    )}
                  </div>
                );
              })}
              <div className="mt-2 rounded-[8px] border border-line p-3 text-[12.5px]">
                <label className="flex items-start gap-2">
                  <input type="checkbox" className="mt-0.5" checked={st.cloud_asr_enabled}
                    onChange={(e) => {
                      if (e.target.checked && !confirm("Облачный ASR отправляет звук на внешний сервер. Голос — персональные данные: включайте только для записей без ПДн. Включить?")) return;
                      patch({ cloud_asr_enabled: e.target.checked });
                    }} />
                  <span>Облачный ASR (Yandex SpeechKit) — только для записей без ПДн.
                    <span className="block text-faint text-[11.5px]">Выключено по умолчанию (решения D28, D38). Адаптер ещё не подключён: переключатель пока ничего не отправляет.</span></span>
                </label>
              </div>
            </div>
          </div>
        </Card>

        <ClaudeCard />

        <BenchPanel />

        <Card title="video-use">
          <Row k="Путь" v={sys.video_use.path} />
          <Row k="Коммит (зафиксирован)" v={sys.video_use.sha ?? "не git-репозиторий"} tone={sys.video_use.sha ? "ok" : "err"} />
          <p className="text-[11.5px] text-faint mt-1">ElevenLabs Scribe отключён: transcribe.py и transcribe_batch.py не вызываются. Отчёт аудита — SECURITY_AUDIT.md в репозитории Cutroom.</p>
        </Card>
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="rounded-[8px] border border-line bg-panel p-4 flex flex-col gap-1.5"><div className="label mb-1">{title}</div>{children}</section>;
}
const Big = ({ children }: { children: React.ReactNode }) => <div className="text-[14px] font-medium mb-1">{children}</div>;
function Row({ k, v, tone }: { k: string; v: string; tone?: "ok" | "err" }) {
  return <div className="flex gap-3 text-[12.5px]"><span className="text-faint w-[150px] shrink-0">{k}</span><span className={cls("tnum break-all", tone === "ok" && "text-ok", tone === "err" && "text-err")}>{v}</span></div>;
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex items-center gap-3"><span className="text-faint text-[12px] w-[90px]">{label}</span>{children}</div>;
}
function Seg({ value, options, onChange }: { value: string; options: [string, string][]; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-wrap rounded-[6px] border border-line-strong overflow-hidden">
      {options.map(([v, l]) => (
        <button key={v} onClick={() => onChange(v)} aria-pressed={value === v}
          className={cls("h-7 px-2.5 text-[12px] border-r border-line-strong last:border-r-0", value === v ? "bg-accent-soft text-accent" : "text-dim hover:text-fg")}>{l}</button>
      ))}
    </div>
  );
}

function ClaudeCard() {
  const c = useClaude();
  return (
    <Card title="Claude Code — агенты стратегии и монтажа">
      {!c?.checked ? <p className="text-dim text-[12.5px] pulse">Проверяю связь…</p> : <>
        <Row k="Состояние" v={!c.found ? "не найден" : c.logged_in ? "подключён, агенты готовы" : "нужен вход"} tone={c.logged_in ? "ok" : "err"} />
        {c.version && <Row k="Версия" v={c.version} />}
        {c.plan && <Row k="План" v={c.plan} />}
        {c.account && <Row k="Аккаунт" v={c.account} />}
        {c.path && <Row k="Программа" v={c.path} />}
        {c.error && <p className="text-err text-[12px]">{c.error}</p>}
        <div className="flex gap-2 mt-1">
          {c.found && !c.logged_in && <button className="btn btn-primary btn-sm" onClick={claudeLogin}>Войти в Claude</button>}
          <button className="btn btn-sm" onClick={() => refreshClaude(true)}>Проверить снова</button>
        </div>
        <p className="text-[11.5px] text-faint">Связь поднимается сама при запуске Cutroom. Если входа нет, Cutroom один раз откроет окно входа: войдите в браузере, окно закроется само. Проверка входа не тратит запросы к модели.</p>
      </>}
    </Card>
  );
}
