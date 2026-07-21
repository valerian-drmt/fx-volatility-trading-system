/**
 * VOLDESK shared portfolio/system type exports (former mock data module).
 * The synthetic risk/stress/attribution/system datasets that used to live here
 * were deleted in the fabricated-fallback purge (remediation 05 WI-2 part D) —
 * every consumer is wired to live adapters (`data/live/*`). Only the type
 * shapes shared between the adapters and the views remain.
 */

export interface VegaTenor {
  tenor: string;
  vega: number;
  n: number;
  pct: number;
}

export interface StackItem {
  name: string;
  status: "up" | "warn" | "down";
  meta: string;
}

/** Performance stats card shape (filled by `adaptPerfStats`). */
export interface PerfStats {
  cumRealized: number;
  cumUnrealized: number;
  maxDd: number;
  currentDd: number;
  sharpe: number;
  hitRate: number;
  nClosed: number; // genuine trade closes
  nReconciledFlat: number; // netting/reconciliation adjustments (not trades)
  netLiqChange: number; // ground-truth Δ net-liq over the window ($k)
  hitRateNull: boolean; // true when there are no genuine closes → show "—"
}

export interface WaterfallStep {
  label: string;
  sub?: string;
  v: number;
  type: string;
  color?: string;
}

export interface BookStructure {
  name: string;
  nominal: number;
  legs: number;
  color: string;
  pct: number;
}
export interface BookComposition {
  byStructure: BookStructure[];
  legs: number;
  totalNominal: number;
}

export interface VarFactor {
  key: string;
  label: string;
  v: number;
  color: string;
  incident?: boolean;
}
