/**
 * Generic HTTP snapshot hook (R11 PR F) → `Fresh<T>` + `reload()`.
 * Fetches on mount; `reload()` (or a WS stream invalidation) re-fetches.
 * `pollMs > 0` also re-fetches on an interval (for sources with no WS push,
 * e.g. engine heartbeats). On error the hook keeps the last-known data and
 * flags it "stale"; only when nothing ever loaded does it report "missing".
 *
 * Freshness AGES (#369): `status` / `ageMs` are re-derived every second from
 * `asOf` (the server's own stamp when `asOfFrom` extracts one, else receive
 * time), so a source that goes quiet turns stale on its own instead of
 * keeping the badge it had at fetch time. A failed reload keeps the data and
 * forces "stale" until the next success.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Fresh, statusFor } from "../voldesk/data/freshness";
import { useNow } from "./useNow";

export interface FetchResult<T> extends Fresh<T> {
  reload: () => void;
}

interface Held<T> {
  data: T | null;
  asOf: number | null;
  /** last reload failed: show stale whatever the age */
  errored: boolean;
  /** message of the last failure; null after a success (#348) */
  error?: string | null;
}

export function useFetch<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  warnMs: number,
  enabled = true,
  pollMs = 0,
  asOfFrom?: (data: T) => number | null,
): FetchResult<T> {
  const [held, setHeld] = useState<Held<T>>({ data: null, asOf: null, errored: false, error: null });
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const asOfFromRef = useRef(asOfFrom);
  asOfFromRef.current = asOfFrom;

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
        if (cancelled) return;
        const serverAsOf = asOfFromRef.current?.(d) ?? null;
        setHeld({ data: d, asOf: Number.isFinite(serverAsOf ?? NaN) ? serverAsOf : Date.now(), errored: false, error: null });
      })
      .catch((err) => {
        // Aborted fetch: caller moved on, keep last-known state untouched.
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        // Transient error: keep the last-known data, flag it stale. Never blank
        // a slice that already has data — that is what used to flip views onto
        // fabricated fallbacks. No data yet → honest "missing".
        // The message rides along (#348) so a panel that never loaded can say
        // "error" instead of "no data", which reads like a closed market.
        const message = err instanceof Error ? err.message : String(err);
        setHeld((prev) =>
          prev.data === null
            ? { data: null, asOf: null, errored: false, error: message }
            : { ...prev, errored: true, error: message },
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

  // The ageing clock only runs while there is something to age.
  const now = useNow(held.data !== null);
  const fresh = deriveFresh(held, warnMs, now);
  // One object per actual change (#348, audit 09#6): the provider memoises on
  // these results, and a fresh literal every render defeated it.
  const { data, status, asOf, ageMs } = fresh;
  const error = fresh.error ?? null;
  return useMemo(() => ({ data, status, asOf, ageMs, error, reload }), [data, status, asOf, ageMs, error, reload]);
}

/** Pure: (held data, warn window, now) → Fresh. Exported for tests. */
export function deriveFresh<T>(held: Held<T>, warnMs: number, now: number): Fresh<T> {
  if (held.data === null || held.asOf === null) {
    return { data: null, status: "missing", asOf: null, ageMs: null, error: held.error ?? null };
  }
  const status = held.errored ? "stale" : statusFor(held.asOf, warnMs, now);
  return { data: held.data, status, asOf: held.asOf, ageMs: Math.max(0, now - held.asOf), error: held.error ?? null };
}
