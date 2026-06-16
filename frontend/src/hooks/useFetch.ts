/**
 * Generic HTTP snapshot hook (R11 PR F) → `Fresh<T>` + `reload()`.
 * Fetches on mount; `reload()` (or a WS stream invalidation) re-fetches.
 * On error → status "missing" (the view shows last-known / placeholder).
 */
import { useEffect, useRef, useState } from "react";
import { type Fresh, makeFresh } from "../voldesk/data/freshness";

export interface FetchResult<T> extends Fresh<T> {
  reload: () => void;
}

export function useFetch<T>(
  fetcher: () => Promise<T>,
  warnMs: number,
  enabled = true,
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

  return { ...state, reload: () => setTick((t) => t + 1) };
}
