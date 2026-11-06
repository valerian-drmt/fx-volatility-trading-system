/**
 * A 1 s clock for freshness (#369, hot/cold T5 — problem P7).
 *
 * `Fresh.status` used to be computed once, at fetch time: a panel whose source
 * went quiet kept its green "live" badge for as long as nothing re-rendered it.
 * Hooks that hold an `asOf` subscribe to this clock while they have data and
 * re-derive `status` / `ageMs` every second, so a badge turns stale on its own.
 */
import { useEffect, useState } from "react";

export function useNow(active: boolean, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [active, intervalMs]);
  return now;
}
