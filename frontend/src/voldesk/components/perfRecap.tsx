/**
 * VOLDESK — per-window performance recap, shared by the Portfolio tab's
 * Performance panel and the Dashboard Portfolio card. One row per perf window:
 * realized = equity Δ over the window, drawdowns from the window's running
 * peak (same formula as the underwater plot), and the 4 cumulative greek-P&L
 * Taylor terms (Δ over window).
 */
import { fmt } from "../data";
import type { EquityPoint, GreekSeries } from "../data/live/portfolio";
import { pnlCls } from "./format";

// The 5 shared perf windows — chips on the Performance panel + recap-table rows.
export const PERF_WINS = [
  { v: "1D", l: "1D" },
  { v: "7D", l: "7D" },
  { v: "30D", l: "1M" },
  { v: "1Y", l: "1Y" },
  { v: "all", l: "all" },
];

export interface RecapRow {
  w: string;
  pnl: number | null;
  maxDd: number | null;
  curDd: number | null;
  delta: number | null;
  gamma: number | null;
  vega: number | null;
  theta: number | null;
}

export function recapRow(w: string, pts: EquityPoint[], gs: GreekSeries): RecapRow {
  let pnl: number | null = null,
    maxDd: number | null = null,
    curDd: number | null = null;
  if (pts.length >= 2) {
    pnl = pts[pts.length - 1]!.v - pts[0]!.v;
    let peak = -Infinity,
      worst = 0;
    for (const p of pts) {
      peak = Math.max(peak, p.v);
      worst = Math.min(worst, (p.v - peak) / peak);
    }
    maxDd = worst * 100;
    curDd = ((pts[pts.length - 1]!.v - peak) / peak) * 100;
  }
  const gval = (arr: EquityPoint[]): number | null =>
    arr.length >= 2 ? arr[arr.length - 1]!.v - arr[0]!.v : null;
  return { w, pnl, maxDd, curDd, delta: gval(gs.delta), gamma: gval(gs.gamma), vega: gval(gs.vega), theta: gval(gs.theta) };
}

// cell renderers — money green/red by sign, DD red when meaningfully negative.
export const recapMoney = (x: number | null): JSX.Element =>
  x == null ? <span className="dim">—</span> : <span className={pnlCls(x)}>{fmt.usdk(x)}</span>;

export const recapDd = (x: number | null): JSX.Element =>
  x == null ? <span className="dim">—</span> : <span className={x < -0.05 ? "neg" : "dim"}>{x.toFixed(1)}%</span>;
