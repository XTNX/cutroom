import type { SVGProps } from "react";

// Minimal stroke icon set (24-grid, 1.75 stroke). Inline so the app works offline.
const P: Record<string, string> = {
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  plus: "M12 5v14M5 12h14",
  play: "M7 5l12 7-12 7z",
  pause: "M8 5v14M16 5v14",
  scissors: "M6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.1 7.9L20 20M8.1 16.1L20 4",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-4.3-4.3",
  queue: "M4 6h16M4 12h10M4 18h7M17 15l3 3-3 3",
  cpu: "M7 7h10v10H7zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3",
  x: "M6 6l12 12M18 6L6 18",
  check: "M5 12l5 5 9-10",
  alert: "M12 3l10 18H2zM12 10v5M12 18h.01",
  film: "M4 4h16v16H4zM8 4v16M16 4v16M4 8h4M4 12h4M4 16h4M16 8h4M16 12h4M16 16h4",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
  wave: "M3 12h2M7 8v8M11 5v14M15 9v6M19 7v10M21 12h0",
  agent: "M12 3v3M12 18v3M3 12h3M18 12h3M6.3 6.3l2.1 2.1M15.6 15.6l2.1 2.1M6.3 17.7l2.1-2.1M15.6 8.4l2.1-2.1",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  panelLeft: "M3 4h18v16H3zM9 4v16",
  panelRight: "M3 4h18v16H3zM15 4v16",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  chevronDown: "M6 9l6 6 6-6",
  chevronUp: "M6 15l6-6 6 6",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  split: "M12 3v18M4 5h16v14H4z",
  send: "M4 12l16-8-6 16-2-7z",
  stop: "M6 6h12v12H6z",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  eyeOff: "M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.1A10 10 0 0 1 12 5c5 0 9 4.5 10 7a13 13 0 0 1-3 4.2M6.6 6.6C4.4 8 2.8 10 2 12c1 2.5 5 7 10 7a9.7 9.7 0 0 0 4.4-1",
  download: "M12 3v12M7 10l5 5 5-5M5 21h14",
  copy: "M8 8h12v12H8zM4 16V4h12",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
};

export function Icon({ name, size = 16, ...rest }: { name: keyof typeof P | string; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>
      <path d={P[name] ?? ""} />
    </svg>
  );
}
