/**
 * Voldesk live WS streams (R11 PR F) — thin typed wrappers over `useWebSocket`,
 * each returning a `Fresh<T>` (last-known value + staleness). Pass `enabled=false`
 * (or null id) to stay disconnected (mock mode / view not mounted).
 *
 * Staleness thresholds reflect each engine's cadence: ticks fast, vol ~3 min.
 */
import { useWebSocket } from "./useWebSocket";
import { type Fresh, makeFresh } from "../voldesk/data/freshness";

const WARN_MS = {
  ticks: 10_000,
  vol: 240_000,
  risk: 90_000,
  positions: 90_000,
  orders: 60_000,
  alerts: 300_000,
} as const;

function useStream<T>(path: string | null, warnMs: number): Fresh<T> {
  const { last, asOf } = useWebSocket<T>(path);
  return makeFresh<T>(last, asOf, warnMs);
}

export interface TickMsg {
  symbol?: string;
  mid?: number;
  bid?: number;
  ask?: number;
}

export const useTicks = (enabled = true): Fresh<TickMsg> =>
  useStream<TickMsg>(enabled ? "/ws/ticks" : null, WARN_MS.ticks);

export const useVolStream = (enabled = true): Fresh<unknown> =>
  useStream<unknown>(enabled ? "/ws/vol" : null, WARN_MS.vol);

export const useRiskStream = (enabled = true): Fresh<unknown> =>
  useStream<unknown>(enabled ? "/ws/risk" : null, WARN_MS.risk);

export const usePositionsStream = (enabled = true): Fresh<unknown> =>
  useStream<unknown>(enabled ? "/ws/positions" : null, WARN_MS.positions);

export const useOrdersStream = (structureId: number | null): Fresh<unknown> =>
  useStream<unknown>(
    structureId !== null ? `/ws/orders/${structureId}` : null,
    WARN_MS.orders,
  );

export const useExitAlerts = (enabled = true): Fresh<unknown> =>
  useStream<unknown>(enabled ? "/ws/exit_alerts" : null, WARN_MS.alerts);
