/**
 * Generic HTTP snapshot hook (R11 PR F) → `Fresh<T>` + `reload()`.
 * Fetches on mount; `reload()` (or a WS stream invalidation) re-fetches.
 * `pollMs > 0` also re-fetches on an interval (for sources with no WS push,
 * e.g. engine heartbeats). On error → status "missing" (view shows last-known).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { type Fresh, makeFresh } from "../voldesk/data/freshness";

export interface FetchResult<T> extends Fresh<T> {
  reload: () => void;
}

export function useFetch<T>(
  fetcher: () => Promise<T>,
  warnMs: number,
  enabled = true,
  pollMs = 0,
): FetchResult<T> {
  const [state, setState] = useState<Fresh<T>>({
    data: null,
    status: "missing",
    asOf: null,
    ageMs: null,
  });
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void fetcherRef
      .current()
      .then((d) => {
        if (!cancelled) setState(makeFresh<T>(d, Date.now(), warnMs));
      })
      .catch(() => {
        if (!cancelled) {
          setState({ data: null, status: "missing", asOf: null, ageMs: null });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [tick, warnMs, enabled]);

  useEffect(() => {
    if (!enabled || pollMs <= 0) return;
    const id = setInterval(() => setTick((t) => t + 1), pollMs);
    return () => clearInterval(id);
  }, [enabled, pollMs]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}
