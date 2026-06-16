/**
 * Desk-data context + hook + types (R11 PR F). Split from `provider.tsx` so the
 * provider module only exports a component (react-refresh constraint). Views call
 * `useDeskData()` to read each domain as `Fresh<T>` (live or mock).
 */
import { createContext, useContext } from "react";
import type { Pc, PcaModelMeta, TermPoint } from "./core";
import type { Fresh } from "./freshness";

export type { PcaModelMeta };

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
}

export const DeskDataContext = createContext<DeskData | null>(null);

export function useDeskData(): DeskData {
  const ctx = useContext(DeskDataContext);
  if (ctx === null) {
    throw new Error("useDeskData must be used within <DataProvider>");
  }
  return ctx;
}
