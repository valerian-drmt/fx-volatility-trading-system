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
 * by tenor. factors/marginal/vega-PCA + the stress grids/ladders remain mock
 * (separate G-risk frontend wiring) → flagged in 09. */
export interface VarData {
  var95: number;
  var99: number;
  es99: number;
  nDays: number;
  hist: HistBin[];
  perTenor: TenorRisk[];
}

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
  /** Portfolio : capital, perf-stats, attribution, book composition. Live (PR 3). */
  portfolio: Fresh<PortfolioData>;
  /** Risk : 1-day VaR card values (PR 5). Stress grids/ladders/factors stay mock. */
  risk: Fresh<VarData>;
  /** Live EURUSD spot tick (bid/ask/mid) via /ws/ticks (RT.1). */
  ticks: Fresh<TickMsg>;
}

export const DeskDataContext = createContext<DeskData | null>(null);

export function useDeskData(): DeskData {
  const ctx = useContext(DeskDataContext);
  if (ctx === null) {
    throw new Error("useDeskData must be used within <DataProvider>");
  }
  return ctx;
}
