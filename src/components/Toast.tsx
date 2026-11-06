import { Icon } from "../icons";
import { useStore } from "../store";
import { cls } from "../util";

export function Toast() {
  const toast = useStore((s) => s.toast);
  if (!toast) return null;
  return (
    <div role="status" aria-live="polite"
      className={cls("fixed z-50 right-4 bottom-4 max-w-[min(440px,calc(100vw-32px))] rounded-[8px] bg-panel-2 shadow-[var(--shadow-pop)] px-3.5 py-2.5 flex gap-2.5 items-start text-[12.5px]",
        toast.kind === "err" && "border-l-2 border-err")}>
      <Icon name={toast.kind === "err" ? "alert" : toast.kind === "ok" ? "check" : "agent"} size={15}
        className={cls("mt-0.5 shrink-0", toast.kind === "err" ? "text-err" : toast.kind === "ok" ? "text-ok" : "text-accent")} />
      <span className="flex-1">{toast.text}</span>
      <button className="text-faint hover:text-fg" aria-label="Закрыть" onClick={() => useStore.getState().set({ toast: null })}><Icon name="x" size={14} /></button>
    </div>
  );
}
