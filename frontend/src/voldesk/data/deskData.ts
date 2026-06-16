/**
 * Desk-data context + hook + types (R11 PR F). Split from `provider.tsx` so the
 * provider module only exports a component (react-refresh constraint). Views call
 * `useDeskData()` to read each domain as `Fresh<T>` (live or mock).
 */
import { createContext, useContext } from "react";
import type { AccountState, Cash, Greeks, Limits, MacroEvent, Pc, PcaModelMeta, Position, TermPoint } from "./core";
import type { StackItem } from "./extended";
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
  /** IV surface grid + z field. ivSurface live (PR 1) ; ivZ still mock (gap). */
  surface: Fresh<SurfaceData>;
  /** PCA mode cards + model meta. Live (PR 1.2) ; display-config statics on mock. */
  pca: Fresh<PcaData>;
  /** Container stack + engine heartbeats. Live (PR 2r.1, polled). */
  system: Fresh<SystemData>;
  /** Versioned trading-config : history + current. Live read (PR 2r.2). */
  config: Fresh<ConfigData>;
  /** Trade read part : positions + book greeks + caps + events. Live (PR 6r.1, polled). */
  trade: Fresh<TradeData>;
}

export const DeskDataContext = createContext<DeskData | null>(null);

export function useDeskData(): DeskData {
  const ctx = useContext(DeskDataContext);
  if (ctx === null) {
    throw new Error("useDeskData must be used within <DataProvider>");
  }
  return ctx;
}
