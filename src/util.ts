export const pad = (n: number, w = 2) => String(Math.floor(n)).padStart(w, "0");

/** 01:23.45 under an hour, 1:02:03.4 above. */
export function fmtTime(t: number): string {
  if (!isFinite(t)) return "--:--";
  const sign = t < 0 ? "-" : "";
  t = Math.abs(t);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  const ss = s.toFixed(2).padStart(5, "0");
  return h ? `${sign}${h}:${pad(m)}:${ss}` : `${sign}${pad(m)}:${ss}`;
}

/** SMPTE-ish HH:MM:SS:FF for the player readout. */
export function fmtTimecode(t: number, fps: number): string {
  const f = Math.round(fps) || 25;
  const total = Math.floor(t * f + 1e-6);
  const fr = total % f;
  const s = Math.floor(total / f);
  return `${pad(s / 3600)}:${pad((s % 3600) / 60)}:${pad(s % 60)}:${pad(fr)}`;
}

export function fmtDur(t: number): string {
  if (!t) return "0 с";
  if (t < 60) return `${t.toFixed(t < 10 ? 1 : 0)} с`;
  const m = Math.floor(t / 60);
  const s = Math.round(t % 60);
  if (m < 60) return `${m} мин ${pad(s)} с`;
  return `${Math.floor(m / 60)} ч ${pad(m % 60)} мин`;
}

export function fmtEta(t: number | null): string {
  if (t == null) return "";
  if (t < 1) return "<1 с";
  if (t < 60) return `${Math.round(t)} с`;
  if (t < 3600) return `${Math.round(t / 60)} мин`;
  return `${(t / 3600).toFixed(1)} ч`;
}

export function fmtBytes(b: number): string {
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} ГБ`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(0)} МБ`;
  return `${Math.round(b / 1e3)} КБ`;
}

// Russian + English hesitation markers. Whisper sometimes writes them with hyphens or ellipses.
const FILLER = /^(э+-?э*|эм+|мм+|м-м|хм+|а-а+|ну|вот|типа|короче|как бы|это самое|значит|uh+|um+|umm+|erm|hmm+)[.,!?…]*$/i;
export const isFiller = (w: string) => FILLER.test(w.trim().toLowerCase());

export const cls = (...xs: (string | false | null | undefined)[]) => xs.filter(Boolean).join(" ");

export const plural = (n: number, one: string, few: string, many: string) => {
  const n10 = n % 10, n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return one;
  if (n10 >= 2 && n10 <= 4 && (n100 < 10 || n100 >= 20)) return few;
  return many;
};
