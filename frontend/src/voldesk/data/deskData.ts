/**
 * Desk-data context + hook + types (R11 PR F). Split from `provider.tsx` so the
 * provider module only exports a component (react-refresh constraint). Views call
 * `useDeskData()` to read each domain as `Fresh<T>` (live or mock).
 */
import { createContext, useContext } from "react";
import type { TickMsg } from "../../hooks/streams";
import type { AccountState, Cash, Greeks, Limits, MacroEvent, Pc, PcaModelMeta, Position, TermPoint } from "./core";
import type { BookComposition, PerfStats, StackItem, VegaTenor, WaterfallStep } from "./extended";
import type { Fresh } from "./freshness";

export type { PcaModelMeta };

/** Trade view live read part : positions + derived book greeks + caps + calendar. */
export interface TradeData {
  positions: Position[];
  greeks: Greeks;
  account: AccountState;
  limits: Limits;
  events: MacroEvent[];
  cash: Cash[];
}

/** Portfolio view live part (PR 3). coverage/leverage/non-greek waterfall pivots
 * stay mock (backend gaps) — see 09. */
export interface HistBin {
  lo: number;
  hi: number;
  count: number;
}
/** Vega/vanna/volga by tenor bucket, in $k (PR 5). */
export interface TenorRisk {
  tenor: string;
  vega: number;
  vanna: number;
  volga: number;
  n: number;
}
/** Risk view live data (PR 5): VaR values ($k) + empirical histogram + 2nd-order
 * by tenor. Stress grids, greeks ladders, marginal-VaR and pin-risk are now all
 * wired to live endpoints (fetchStressGrid / fetchGreeksLadder / fetchMarginalVar
 * / fetchPinRisk) — no longer mock. */
export interface VarData {
  // null until the backend has enough daily observations (< VAR_MIN_DAYS ⇒ the
  // /portfolio/var stats come back null). Never coerced to 0 — a 0 VaR would
  // read as "no risk" and pile the 95/99/ES marks onto the µ line.
  var95: number | null;
  var99: number | null;
  es99: number | null;
  meanDaily: number | null; // live mean daily P&L ($k) — drives the expected-return column
  nDays: number;
  hist: HistBin[];
  perTenor: TenorRisk[];
}

/** Daily observations the historical VaR needs before it returns stats
 * (mirrors `_var_stats` in `src/api/routers/portfolio_panel.py`). */
export const VAR_MIN_DAYS = 5;

export interface PortfolioData {
  account: AccountState;
  greeks: Greeks;
  positions: Position[];
  vegaPerTenor: VegaTenor[];
  perfStats: PerfStats;
  dailyPnl: number[];
  waterfallGreek: WaterfallStep[];
  bookComposition: BookComposition;
}

/** Engine heartbeat row (same shape as the mock `engines[]`). */
export interface EngineRow {
  name: string;
  hb: number;
  stale: number;
  status: "up" | "warn" | "down";
}
export interface StackLayer {
  layer: string;
  items: StackItem[];
}
/** System view live part: container stack + engine heartbeats. */
export interface SystemData {
  engines: EngineRow[];
  stack: StackLayer[];
}

/** Versioned trading-config (Settings view, hybrid read model). */
export interface ConfigVersionRow {
  version: number;
  by: string;
  comment: string;
  at: string | null;
}
export interface ConfigField {
  key: string;
  value: string;
}
export interface ConfigSection {
  name: string;
  fields: ConfigField[];
}
export interface ConfigData {
  currentVersion: number;
  sections: ConfigSection[];
  history: ConfigVersionRow[];
}

export interface SurfaceData {
  /** 6×5 IV grid (%), [tenorIdx][deltaIdx]. Live from /vol/surface (PR 1). */
  ivSurface: number[][];
  /** 6×5 per-cell rich/cheap z. Backend gap → mock until a per-cell-z endpoint. */
  ivZ: number[][];
  tenors: string[];
  deltas: string[];
  /** Per-tenor source aligned with `tenors`: "listed" (real contract), "interp"
   * (no listed contract — IV interpolated server-side) or "missing". */
  sources?: ("listed" | "interp" | "missing")[];
}

/** A mode card = the mock `Pc` plus its real z trajectory (empty in mock mode). */
export interface PcaCard extends Pc {
  zHistory: number[];
}
export interface PcaData {
  pcs: PcaCard[];
  model: PcaModelMeta;
}

export interface DeskData {
  /** ATM term structure (+ fair/rv). Live (PR F). */
  termStructure: Fresh<TermPoint[]>;
  /** IV surface grid + per-cell rich/cheap z. Both live (ivSurface PR 1, ivZ via
   * backend surface `.z`) ; z is 0/neutral until the engine has surface history. */
  surface: Fresh<SurfaceData>;
  /** PCA mode cards + model meta. Live (PR 1.2) ; display-config statics on mock. */
  pca: Fresh<PcaData>;
  /** Container stack + engine heartbeats. Live (PR 2r.1, polled). */
  system: Fresh<SystemData>;
  /** Versioned trading-config : history + current. Live read (PR 2r.2). */
  config: Fresh<ConfigData>;
  /** Trade read part : positions + book greeks + caps + events. Live (PR 6r.1, polled). */
  trade: Fresh<TradeData>;
  /** Force an immediate refetch of the `trade` slice (positions mirror + book
   *  greeks). Called right after a send so Open positions updates in one step
   *  instead of waiting for the next poll. */
  reloadTrade: () => void;
  /** Portfolio : capital, perf-stats, attribution, book composition. Live (PR 3). */
  portfolio: Fresh<PortfolioData>;
  /** Risk : 1-day VaR card values (PR 5). Stress grids / ladders / marginal-VaR
   * / pin-risk are all live now (their own fetch hooks), not mock. */
  risk: Fresh<VarData>;
}

export const DeskDataContext = createContext<DeskData | null>(null);

export function useDeskData(): DeskData {
  const ctx = useContext(DeskDataContext);
  if (ctx === null) {
    throw new Error("useDeskData must be used within <DataProvider>");
  }
  return ctx;
}

/**
 * Live EURUSD spot tick (bid/ask/mid) via /ws/ticks (RT.1). Kept in its OWN
 * context, separate from `DeskDataContext`: the tick stream fires ~1 Hz, so
 * folding it into the desk-data value object would re-render every
 * `useDeskData()` consumer (the slow vol domains) on each spot tick. Only the
 * components that actually display spot subscribe via `useTicks()`.
 */
export const TicksContext = createContext<Fresh<TickMsg> | null>(null);

export function useTicks(): Fresh<TickMsg> {
  const ctx = useContext(TicksContext);
  if (ctx === null) {
    throw new Error("useTicks must be used within <DataProvider>");
  }
  return ctx;
}
