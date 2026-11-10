/**
 * Voldesk live WS streams (R11 PR F) — thin typed wrappers over `useWebSocket`,
 * each returning a `Fresh<T>` (last-known value + staleness). Pass `enabled=false`
 * (or null id) to stay disconnected (mock mode / view not mounted).
 *
 * Staleness thresholds reflect each engine's cadence: ticks fast, vol ~3 min.
 *
 * Hot/cold T3 (#367): the execution engine pushes position, account and spot
 * order changes (#366); `usePositionsStream`, `useAccountStream`,
 * `useSpotOrdersStream` and `useOrdersStream(structureIds)` subscribe to them
 * and expose the socket state so a panel can show whether it is being pushed
 * to or living on the 60 s reconciliation poll.
 */
import { useMemo } from "react";
import { useNow } from "./useNow";
import { type MultiWsState, useMultiWebSocket, useWebSocket, type WsStatus } from "./useWebSocket";
import { type Fresh, makeFresh } from "../voldesk/data/freshness";
import type { SpotOrderRow } from "../api/endpoints";

const WARN_MS = {
  ticks: 10_000,
  vol: 240_000,
  risk: 90_000,
  /** event streams: a quiet channel is normal, so the window is wide and the
   * socket status, not the age, is what the chip shows */
  events: 24 * 3_600_000,
} as const;

export type { WsStatus };

/** A `Fresh<T>` plus the socket's own state (the age of an event stream says
 * nothing about its health: no fill for an hour is a quiet book, not a dead
 * socket). */
export interface StreamState<T> extends Fresh<T> {
  wsStatus: WsStatus;
}

function useStreamState<T>(
  path: string | null,
  warnMs: number,
  parse?: (raw: string) => T | null,
  throttleMs = 0,
): StreamState<T> {
  const { last, asOf, status } = useWebSocket<T>(path, parse, throttleMs);
  // No ageing clock here: an event stream is judged by its socket state, not
  // by how long ago the last event came (a quiet book is not a dead feed), so
  // the provider is not re-rendered every second on its behalf.
  return useMemo(
    () => ({ ...makeFresh<T>(last, asOf, warnMs, asOf ?? Date.now()), wsStatus: status }),
    [last, asOf, warnMs, status],
  );
}

function useStream<T>(path: string | null, warnMs: number, throttleMs = 0): Fresh<T> {
  const { last, asOf } = useWebSocket<T>(path, undefined, throttleMs);
  const now = useNow(last !== null);
  return makeFresh<T>(last, asOf, warnMs, now);
}

/** One `ticks` message, as `bus.publisher.publish_tick` emits it. The shape is
 * pinned to tests/fixtures/payloads/ticks.json, the fixture the backend
 * publisher tests use (#348): src/hooks/__tests__/wsContract.test.ts. */
export interface TickMsg {
  symbol?: string;
  mid?: number;
  bid?: number;
  ask?: number;
  /** ISO-8601 UTC stamp of the quote */
  timestamp?: string;
}

// Ticks fire ~5/s ; coalesce to a steady 1s beat (display-only smoothing).
export const useTicks = (enabled = true): Fresh<TickMsg> =>
  useStream<TickMsg>(enabled ? "/ws/ticks" : null, WARN_MS.ticks, 1000);

// vol/risk beats are used ONLY as REST-invalidation triggers (provider.tsx) —
// the payloads themselves are never rendered. Throttle them so each open tab
// re-fetches its snapshot family at a bounded cadence instead of on every
// ~2s engine cycle (the audit's "refetch storm": ~5.5 req/s per tab, forever).
export const useVolStream = (enabled = true): Fresh<unknown> =>
  useStream<unknown>(enabled ? "/ws/vol" : null, WARN_MS.vol, 30_000);

export const useRiskStream = (enabled = true): Fresh<unknown> =>
  useStream<unknown>(enabled ? "/ws/risk" : null, WARN_MS.risk, 15_000);

// ── Execution-engine push channels (#366 / #367) ─────────────────────────────

/** One `positions_changed` message: the rows a position sync upserted (full
 * `/positions/open` rows) and the ids it closed. The same channel also carries
 * the position monitor's MTM snapshots (keyed by `position_id`), which the
 * parser drops. */
export interface PositionsChangedMsg {
  event: "positions_changed";
  source: string;
  rows: unknown[];
  closed: { id: number; structure?: string | null; contract_id?: number | null }[];
  n_open: number;
  timestamp: string;
}

/** One `account_snapshot` message: the `latest` of `/portfolio/account`. */
export interface AccountSnapshotMsg {
  event: "account_snapshot";
  source: string;
  latest: Record<string, unknown> & { timestamp?: string | null };
  timestamp: string;
}

/** One `spot_order_changed` message: a `/trade/spot-orders` row. */
export interface SpotOrderChangedMsg {
  event: "spot_order_changed";
  source: string;
  row: SpotOrderRow;
  timestamp: string;
}

/** One event on `orders:<structure_id>` (acknowledged / filled / rejected /
 * cancelled / unwind_created). Loose schema: the desk only uses it as a
 * targeted reload trigger for the blotter. */
export interface OrderEventMsg {
  event_type?: string;
  order_id?: number;
  structure_id?: number;
  state?: string;
  timestamp?: string;
}

function parseJson(raw: string): unknown {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

/** Exported for tests: only the execution engine's `positions_changed` event
 * passes; the position monitor's per-position MTM messages are ignored. */
export function parsePositionsChanged(raw: string): PositionsChangedMsg | null {
  const o = parseJson(raw) as Partial<PositionsChangedMsg> | null;
  if (!o || o.event !== "positions_changed" || !Array.isArray(o.rows) || !Array.isArray(o.closed)) return null;
  return o as PositionsChangedMsg;
}

export function parseAccountSnapshot(raw: string): AccountSnapshotMsg | null {
  const o = parseJson(raw) as Partial<AccountSnapshotMsg> | null;
  if (!o || o.event !== "account_snapshot" || !o.latest || typeof o.latest !== "object") return null;
  return o as AccountSnapshotMsg;
}

export function parseSpotOrderChanged(raw: string): SpotOrderChangedMsg | null {
  const o = parseJson(raw) as Partial<SpotOrderChangedMsg> | null;
  if (!o || o.event !== "spot_order_changed" || !o.row || typeof o.row !== "object") return null;
  if (typeof (o.row as SpotOrderRow).id !== "number") return null;
  return o as SpotOrderChangedMsg;
}

function parseOrderEvent(raw: string): OrderEventMsg | null {
  const o = parseJson(raw);
  return o && typeof o === "object" ? (o as OrderEventMsg) : null;
}

export const usePositionsStream = (enabled = true): StreamState<PositionsChangedMsg> =>
  useStreamState<PositionsChangedMsg>(enabled ? "/ws/positions" : null, WARN_MS.events, parsePositionsChanged);

export const useAccountStream = (enabled = true): StreamState<AccountSnapshotMsg> =>
  useStreamState<AccountSnapshotMsg>(enabled ? "/ws/account" : null, WARN_MS.events, parseAccountSnapshot);

export const useSpotOrdersStream = (enabled = true): StreamState<SpotOrderChangedMsg> =>
  useStreamState<SpotOrderChangedMsg>(enabled ? "/ws/spot_orders" : null, WARN_MS.events, parseSpotOrderChanged);

/** At most this many per-structure order sockets at once: the blotter only
 * needs the structures still working, never the whole history. */
export const MAX_ORDER_STREAMS = 20;

/** One socket per working structure on `orders:<id>`; the last event and
 * the aggregated socket state. Pass the ids in any order and with
 * duplicates; the hook sorts, dedupes and caps them. */
export function useOrdersStream(structureIds: readonly number[], enabled = true): MultiWsState<OrderEventMsg> {
  const paths = useMemo(() => {
    if (!enabled) return [] as string[];
    const ids = [...new Set(structureIds.filter((n) => Number.isFinite(n)))].sort((a, b) => b - a).slice(0, MAX_ORDER_STREAMS);
    return ids.map((id) => `/ws/orders/${id}`);
  }, [structureIds, enabled]);
  return useMultiWebSocket<OrderEventMsg>(paths, parseOrderEvent);
}
