/**
 * VOLDESK pure formatting / classification helpers (no JSX).
 * Split out of common.tsx so that file only exports components
 * (react-refresh/only-export-components, enforced with --max-warnings 0).
 */
export type Tone = "good" | "warn" | "danger" | "neutral";
export type Status = "up" | "warn" | "down";

export function pnlCls(v: number): "pos" | "neg" | "flat" {
  return v > 0 ? "pos" : v < 0 ? "neg" : "flat";
}

// shared signed-$ greek formatter (±N · ±$N.Nk · ±$N.NNM) — one definition for all tabs
export function gk$(v: number | null | undefined): string {
  if (v == null) return "—";
  const s = v < 0 ? "-" : "+";
  const a = Math.abs(v);
  if (a >= 1e6) return s + "$" + (a / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return s + "$" + (a / 1e3).toFixed(1) + "k";
  return s + "$" + Math.round(a);
}

export const signalTone = (s: string): Tone =>
  (({ tail: "danger", weak: "warn", noise: "neutral", strong: "good", aligned: "good" } as Record<string, Tone>)[s] ||
    "neutral");
