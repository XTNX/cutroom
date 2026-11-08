import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { clone, end, fpsOf } from "../quickOps";
import { shownDoc, useQuick, type QuickPayload } from "../quickStore";
import { quickUrl } from "./QuickView";
import { cls } from "../util";

const CARD_STYLES: [string, string][] = [["cream", "Крем"], ["orange", "Оранжевый"], ["khaki", "Хаки"], ["carton", "Картон"]];

export function QuickDialogs() {
  const dialog = useQuick((s) => s.dialog);
  const set = useQuick((s) => s.set);
  return (
    <Dialog.Root open={!!dialog} onOpenChange={(o) => { if (!o) set({ dialog: null }); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-[var(--scrim)] z-40" />
        <Dialog.Content className="fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[min(860px,94vw)] max-h-[90vh] overflow-y-auto bg-panel rounded-[10px] shadow-[var(--shadow-pop)] p-4">
          {dialog?.kind === "card" && <CardVariants id={dialog.id} />}
          {dialog?.kind === "sound" && <SoundVariants id={dialog.id} />}
          {dialog?.kind === "slides" && <SlidePicker at={dialog.at} replace={dialog.replace} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function useJobDone(kind: string, source: string, onDone: () => void) {
  const jobs = useStore((s) => s.jobs);
  const last = useRef<string | undefined>(undefined);
  const j = jobs.filter((x) => x.kind === kind && x.source === source).at(-1);
  useEffect(() => {
    if (j && last.current && last.current !== j.status && j.status === "done") onDone();
    if (j?.status === "error" && last.current !== "error") useStore.getState().say("err", j.error ?? "ошибка");
    last.current = j?.status;
  }, [j?.status]);
  return j && (j.status === "running" || j.status === "queued") ? j : null;
}

function CardVariants({ id }: { id: string }) {
  const q = useQuick();
  const card = q.data?.doc?.tracks.overlays.find((o) => o.id === id);
  const [pick, setPick] = useState<string>(card?.style ?? "cream");
  const [req, setReq] = useState("");
  const running = useJobDone("qvariants", id, () => q.load());
  const ask = (request?: string) => api.post(`/api/quick/variants/card/${id}`, { request: request || null }).catch((e) => useStore.getState().say("err", e.message));
  useEffect(() => { if (card?.spec && !card.variants) ask(); }, [id]);
  if (!card) return <p className="text-dim">Карточка не найдена.</p>;
  const url = (style: string) => card.variants?.[style] ? quickUrl(`/media/quick/card/${id}/poster?style=${style}&v=${encodeURIComponent(card.variants[style])}`) : null;
  const apply = async () => {
    try { q.set({ data: await api.post<QuickPayload>(`/api/quick/variants/card/${id}/apply`, { style: pick }), dialog: null }); }
    catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  return (
    <>
      <Dialog.Title className="text-[15px] font-medium mb-1">Оформление карточки</Dialog.Title>
      <Dialog.Description className="text-dim text-[12px] mb-3">«{(card.spec?.title as string) || card.hint}» — выбор применится только к этой карточке.</Dialog.Description>
      {!card.spec ? (
        <p className="text-dim text-[12.5px]">Содержание карточки ещё строится. Откройте окно снова, когда она появится на дорожке.</p>
      ) : (
        <>
          <div className="aspect-video bg-[#2a2a2a] rounded-[8px] overflow-hidden grid place-items-center mb-3">
            {url(pick) ? <img src={url(pick)!} alt="" className="w-full h-full object-contain" /> : <span className="text-dim pulse">Рисую варианты…</span>}
          </div>
          <div className="grid grid-cols-4 gap-2 mb-3">
            {CARD_STYLES.map(([k, label]) => (
              <button key={k} onClick={() => setPick(k)} className={cls("rounded-[8px] overflow-hidden border-2 text-left", pick === k ? "border-accent" : "border-line")}>
                <div className="aspect-video bg-[#2a2a2a]">{url(k) && <img src={url(k)!} alt="" className="w-full h-full object-contain" />}</div>
                <div className="px-2 py-1 text-[12px]">{label}{card.style === k ? " · сейчас" : ""}</div>
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <input className="input flex-1" value={req} onChange={(e) => setReq(e.target.value)} placeholder="Уточнить содержание: «короче», «покажи только цифры», «добавь сравнение»…"
              onKeyDown={(e) => { if (e.key === "Enter" && req.trim()) { ask(req); setReq(""); } }} />
            <button className="btn" disabled={!req.trim() || !!running} onClick={() => { ask(req); setReq(""); }}>{running ? "Перестраиваю…" : "Перестроить"}</button>
          </div>
        </>
      )}
      <div className="flex justify-end gap-2 mt-4">
        <Dialog.Close className="btn">Отмена</Dialog.Close>
        <button className="btn btn-primary" onClick={apply} disabled={!card.spec || !url(pick)}>Применить к карточке</button>
      </div>
    </>
  );
}

type Variant = { file: string; label: string; bed: boolean; no_music: boolean };

function SoundVariants({ id }: { id: string }) {
  const q = useQuick();
  const a = q.data?.doc?.tracks.audio.find((x) => x.id === id);
  const [vars, setVars] = useState<Record<string, Variant> | null>(null);
  const [pick, setPick] = useState(a?.style ?? "clean");
  const [bust, setBust] = useState(0);
  const audio = useRef<HTMLAudioElement>(null);
  const fetchVars = () => api.get<{ variants: Record<string, Variant> | null }>(`/api/quick/variants/sound/${id}`).then((r) => { setVars(r.variants); setBust(Date.now()); });
  const running = useJobDone("qaudition", id, fetchVars);
  useEffect(() => { api.post(`/api/quick/variants/sound/${id}`, {}).catch((e) => useStore.getState().say("err", e.message)); }, [id]);
  if (!a) return <p className="text-dim">Звуковой блок не найден.</p>;
  const play = (k: string) => {
    setPick(k);
    const el = audio.current;
    if (!el || !vars?.[k]) return;
    el.src = quickUrl(`/media/quick/audition/${id}/${k}?v=${bust}`);
    el.play().catch(() => {});
  };
  const apply = async () => {
    try { q.set({ data: await api.post<QuickPayload>(`/api/quick/variants/sound/${id}/apply`, { style: pick }), dialog: null }); }
    catch (e) { useStore.getState().say("err", (e as Error).message); }
  };
  const fps = q.data?.doc ? fpsOf(q.data.doc) : 25;
  return (
    <>
      <Dialog.Title className="text-[15px] font-medium mb-1">Звук блока</Dialog.Title>
      <Dialog.Description className="text-dim text-[12px] mb-3">
        Блок {(a.len / fps).toFixed(1)} с. Послушайте первые 10 секунд в каждом стиле; выбор применится только к этому блоку.
      </Dialog.Description>
      <div className="rounded-[8px] bg-panel-2 border border-line p-3 mb-3 flex items-center gap-3">
        <audio ref={audio} controls className="flex-1" />
        <span className="text-[12px] text-dim w-[160px]">{vars?.[pick]?.label ?? (running ? "Готовлю варианты…" : "")}</span>
      </div>
      <div className="grid grid-cols-4 gap-2">
        {Object.entries(vars ?? {}).map(([k, v]) => (
          <button key={k} onClick={() => play(k)} className={cls("rounded-[8px] border-2 p-2 text-left", pick === k ? "border-accent" : "border-line")}>
            <div className="text-[12.5px]">{v.label}</div>
            <div className="text-[11px] text-dim mt-0.5">{a.style === k ? "сейчас · " : ""}{v.no_music ? "музыка не выбрана — слышно только речь" : "▶ послушать"}</div>
          </button>
        ))}
        {!vars && <p className="col-span-4 text-dim pulse">Готовлю варианты…</p>}
      </div>
      <div className="flex justify-end gap-2 mt-4">
        <Dialog.Close className="btn">Отмена</Dialog.Close>
        <button className="btn btn-primary" onClick={apply}>Применить к блоку</button>
      </div>
    </>
  );
}

function SlidePicker({ at, replace }: { at: number; replace?: string }) {
  const q = useQuick();
  const d = shownDoc(q);
  const pages = d?.presentation?.pages ?? [];
  const cur = replace ? d?.tracks.slides.find((s) => s.id === replace) : undefined;
  const [pick, setPick] = useState(cur?.page ?? pages[0]?.n ?? 1);
  if (!d) return null;
  const fps = fpsOf(d);
  const place = async () => {
    const nd = clone(d);
    if (cur) {
      nd.tracks.slides.find((s) => s.id === cur.id)!.page = pick;
      await q.commit(nd, `слайд → ${pick}`);
    } else {
      // up to 8 s, but never over the next slide
      const next = nd.tracks.slides.filter((s) => s.start > at).sort((x, y) => x.start - y.start)[0];
      const len = Math.max(Math.round(1 * fps), Math.min(Math.round(8 * fps), (next?.start ?? Infinity) - at));
      nd.tracks.slides = nd.tracks.slides.filter((s) => !(s.start < at + len && end(s) > at));
      nd.tracks.slides.push({ id: "s" + Math.random().toString(16).slice(2, 9), start: at, len, page: pick, layout: "side" });
      nd.tracks.slides.sort((x, y) => x.start - y.start);
      await q.commit(nd, `слайд ${pick}`);
    }
    q.set({ dialog: null });
  };
  return (
    <>
      <Dialog.Title className="text-[15px] font-medium mb-3">{cur ? `Заменить слайд ${cur.page}` : "Поставить слайд"}</Dialog.Title>
      <Dialog.Description className="sr-only">Выбор слайда презентации</Dialog.Description>
      {!pages.length ? <p className="text-dim">Презентация не загружена: добавьте PDF на шаге 1.</p> : (
        <>
          <div className="aspect-video bg-black rounded-[8px] overflow-hidden mb-3">
            <img src={quickUrl(`/media/quick/slide/${pick}`)} alt={`слайд ${pick}`} className="w-full h-full object-contain" />
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {pages.map((p) => (
              <button key={p.n} onClick={() => setPick(p.n)} onDoubleClick={() => { setPick(p.n); }} className={cls("shrink-0 w-[120px] rounded border-2 overflow-hidden", pick === p.n ? "border-accent" : "border-line")}>
                <img src={quickUrl(`/media/quick/slide/${p.n}?thumb=1`)} alt="" className="w-full aspect-video object-cover" />
                <div className="text-[11px] py-0.5">{p.n}</div>
              </button>
            ))}
          </div>
        </>
      )}
      <div className="flex justify-end gap-2 mt-4">
        <Dialog.Close className="btn">Отмена</Dialog.Close>
        <button className="btn btn-primary" onClick={place} disabled={!pages.length}>{cur ? "Заменить" : "Поставить"}</button>
      </div>
    </>
  );
}
