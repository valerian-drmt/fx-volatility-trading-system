/**
 * Generic HTTP snapshot hook (R11 PR F) → `Fresh<T>` + `reload()`.
 * Fetches on mount; `reload()` (or a WS stream invalidation) re-fetches.
 * `pollMs > 0` also re-fetches on an interval (for sources with no WS push,
 * e.g. engine heartbeats). On error the hook keeps the last-known data and
 * flags it "stale"; only when nothing ever loaded does it report "missing".
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { type Fresh, makeFresh } from "../voldesk/data/freshness";

export interface FetchResult<T> extends Fresh<T> {
  reload: () => void;
}

export function useFetch<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
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
    // Abort the in-flight request on cleanup / re-fetch so a superseded fetch
    // can't land late (and fetchers that thread `signal` actually cancel the
    // HTTP call). `cancelled` still guards setState in case the fetcher ignores
    // the signal.
    const controller = new AbortController();
    let cancelled = false;
    void fetcherRef
      .current(controller.signal)
      .then((d) => {
        if (!cancelled) setState(makeFresh<T>(d, Date.now(), warnMs));
      })
      .catch((err) => {
        // Aborted fetch: caller moved on, keep last-known state untouched.
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        // Transient error: keep the last-known data, flag it stale. Never blank
        // a slice that already has data — that is what used to flip views onto
        // fabricated fallbacks. No data yet → honest "missing".
        setState((prev) =>
          prev.data === null
            ? { data: null, status: "missing", asOf: null, ageMs: null }
            : { ...prev, status: "stale" },
        );
      });
    return () => {
      cancelled = true;
      controller.abort();
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
