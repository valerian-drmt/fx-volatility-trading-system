/**
 * VOLDESK live-data freshness contract (R11 PR F).
 *
 * Every live source (HTTP fetch or WS stream) is wrapped in `Fresh<T>` so views
 * can render the *last real value* + a staleness badge, and NEVER fabricate data
 * (decision 2026-06-16). `status`:
 *   - "live"    : data present, age ≤ warn threshold
 *   - "stale"   : data present but past the threshold (last-known, show badge)
 *   - "missing" : no data yet (e.g. market closed at boot, never received)
 */
export type FreshStatus = "live" | "stale" | "missing";

export interface Fresh<T> {
  data: T | null;
  status: FreshStatus;
  /** epoch ms of the data (server `asOf` if provided, else receive time) */
  asOf: number | null;
  /** now - asOf, or null when missing */
  ageMs: number | null;
}

export function statusFor(
  asOf: number | null,
  warnMs: number,
  now: number = Date.now(),
): FreshStatus {
  if (asOf === null) return "missing";
  return now - asOf > warnMs ? "stale" : "live";
}

export function makeFresh<T>(
  data: T | null,
  asOf: number | null,
  warnMs: number,
  now: number = Date.now(),
): Fresh<T> {
  if (data === null || asOf === null) {
    return { data, status: "missing", asOf: null, ageMs: null };
  }
  return { data, status: statusFor(asOf, warnMs, now), asOf, ageMs: now - asOf };
}
