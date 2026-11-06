import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { cls } from "../util";

export type ClaudeStatus = {
  checked: number | null; found: boolean; path: string | null; version: string | null; logged_in: boolean;
  account: string | null; plan: string | null; error: string | null; login_opened: number | null;
};

let cache: ClaudeStatus | null = null;
const listeners = new Set<(s: ClaudeStatus) => void>();
export async function refreshClaude(force = false): Promise<ClaudeStatus | null> {
  try {
    cache = force ? await api.post<ClaudeStatus>("/api/claude/check") : await api.get<ClaudeStatus>("/api/claude");
    listeners.forEach((l) => l(cache!));
  } catch { /* backend not up yet */ }
  return cache;
}
export async function claudeLogin() {
  await api.post("/api/claude/login");
  useStore.getState().say("info", "Открылось окно входа: войдите в аккаунт Claude в браузере. Индикатор в шапке станет зелёным сам.");
}
export function useClaude(): ClaudeStatus | null {
  const [s, setS] = useState(cache);
  useEffect(() => { listeners.add(setS); return () => { listeners.delete(setS); }; }, []);
  return s;
}

// Header chip: the link to Claude Code comes up by itself at launch; this shows where it stands.
export function ClaudeBadge() {
  const s = useClaude();
  useEffect(() => {
    refreshClaude();
    const t = window.setInterval(() => refreshClaude(!cache?.logged_in), cache?.logged_in ? 60000 : 4000);
    return () => clearInterval(t);
  }, [s?.logged_in]);

  const state = !s?.checked ? "wait" : !s.found ? "missing" : s.logged_in ? "ok" : "login";
  const dot = { wait: "bg-faint pulse", missing: "bg-err", login: "bg-warn pulse", ok: "bg-ok" }[state];
  const label = { wait: "Claude…", missing: "Claude не найден", login: "Войти в Claude", ok: "Claude" }[state];
  const title = state === "ok"
    ? `Claude Code ${s!.version ?? ""} подключён${s!.plan ? ` · план ${s!.plan}` : ""}${s!.account ? ` · ${s!.account}` : ""}. Агенты готовы.`
    : state === "login" ? "Агентам нужен вход в Claude: нажмите, откроется браузер"
    : state === "missing" ? (s?.error ?? "Claude Code не найден") : "Проверяю связь с Claude Code";
  return (
    <button className={cls("btn btn-sm", state === "login" ? "border-warn/70" : "btn-ghost")} title={title}
      onClick={() => (state === "login" ? claudeLogin() : useStore.getState().set({ view: "system" }))}>
      <span className={cls("w-2 h-2 rounded-full", dot)} />
      <span className="max-[1100px]:hidden">{label}</span>
    </button>
  );
}
