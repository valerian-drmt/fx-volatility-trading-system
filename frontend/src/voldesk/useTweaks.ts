/**
 * Minimal tweak store for the VOLDESK shell — drives the accent colour,
 * density and rail-labels. Ported from the prototype's `useTweaks`, but
 * stripped of the design-host `postMessage`/EDITMODE protocol (that machinery
 * only made sense inside the prototyping host, not in the production app).
 *
 * Values persist to localStorage so a reload keeps the user's choices.
 */
import { useCallback, useState } from "react";

export interface Tweaks {
  accent: string;
  density: "compact" | "regular";
  showGreeks: boolean;
  railLabels: boolean;
}

export const TWEAK_DEFAULTS: Tweaks = {
  accent: "#e0b341",
  density: "regular",
  showGreeks: true,
  railLabels: true,
};

const STORAGE_KEY = "voldesk.tweaks";

function load(): Tweaks {
  if (typeof localStorage === "undefined") return TWEAK_DEFAULTS;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? { ...TWEAK_DEFAULTS, ...(JSON.parse(raw) as Partial<Tweaks>) } : TWEAK_DEFAULTS;
  } catch {
    return TWEAK_DEFAULTS;
  }
}

export function useTweaks(): [Tweaks, <K extends keyof Tweaks>(key: K, val: Tweaks[K]) => void] {
  const [values, setValues] = useState<Tweaks>(load);

  const setTweak = useCallback(<K extends keyof Tweaks>(key: K, val: Tweaks[K]) => {
    setValues((prev) => {
      const next = { ...prev, [key]: val };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        /* localStorage unavailable (private mode) — keep in-memory only */
      }
      return next;
    });
  }, []);

  return [values, setTweak];
}
