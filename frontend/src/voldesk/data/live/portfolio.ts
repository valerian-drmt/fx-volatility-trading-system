/**
 * Live adapter (R11 PR 3): backend portfolio reads → the voldesk Portfolio view.
 *
 * Sources (all already on the portfolio-panel router; daily-pnl + stats added in
 * the R11 G backend PR):
 *   - /portfolio/account        capital snapshot (+ prev_24h for deltas)
 *   - /portfolio/vega-per-tenor  vega bucketed by DTE
 *   - /portfolio/stats           sharpe / drawdown / hit-rate / cum P&L
 *   - /portfolio/daily-pnl       realized P&L per day
 *   - /portfolio/pnl-attribution greek-pivot bridge (totals)
 *   - /portfolio/equity-curve    net-liq series (window-parameterised, fetched
 *                                in the view since it depends on view state)
 *   - /positions/open            → book composition (front-derived)
 *
 * Net book greeks reuse `deriveNetGreeks` (live/trade) so Portfolio foots with
 * Trade + Risk. Mock-kept gaps (flagged in 09): coverage (no /portfolio/coverage
 * yet), the structure/tenor/mode waterfall pivots (only greek is live), and the
 * leverage strip (no endpoint).
 */
import {
  type AccountState,
  type Position,
} from "../core";
import { EMPTY_ACCOUNT } from "../neutral";
import type { HistBin, TenorRisk, VarData } from "../deskData";
import type { BookComposition, PerfStats, VegaTenor, WaterfallStep } from "../extended";

const n = (v: unknown): number => (typeof v === "number" ? v : 0);
// Like `n` but keeps "absent" absent: a backend null is a value the desk could not
// measure, and must not be rendered as a real 0 (see AttribRow / VarData).
const nn = (v: unknown): number | null => (typeof v === "number" ? v : null);
const r1 = (v: number): number => Math.round(v * 10) / 10;
const r2 = (v: number): number => Math.round(v * 100) / 100;

interface AccountSnap {
  net_liq_usd?: number | null;
  cash_usd?: number | null;
  init_margin_req?: number | null;
  maint_margin_req?: number | null;
  excess_liquidity?: number | null;
  cushion?: number | null;
  open_positions_count?: number | null;
}

/** /portfolio/account → the `AccountState` shape (deltas from prev_24h);
 * fields the snapshot doesn't carry stay neutral zeros. */
export function adaptAccount(raw: unknown): AccountState {
  const o = (raw ?? {}) as {
    latest?: AccountSnap | null; prev_24h?: AccountSnap | null;
    buying_power_usd?: number | null; available_funds_usd?: number | null;
  };
  const L = o.latest ?? {};
  const P = o.prev_24h ?? null;
  const netLiq = n(L.net_liq_usd);
  const cash = n(L.cash_usd);
  const initM = n(L.init_margin_req);
  const maintM = n(L.maint_margin_req);
  const dPct = (now: number, prev: number): number => (prev ? ((now - prev) / prev) * 100 : 0);
  return {
    ...EMPTY_ACCOUNT,
    netLiq,
    cash,
    marginInit: initM,
    marginMaint: maintM,
    excessLiq: n(L.excess_liquidity),
    cushion: n(L.cushion),
    nPositions: L.open_positions_count ?? 0,
    buyingPower: o.buying_power_usd != null ? n(o.buying_power_usd) : 0,
    availableFunds: o.available_funds_usd != null ? n(o.available_funds_usd) : 0,
    marginInitPct: netLiq > 0 ? r1((initM / netLiq) * 100) : 0,
    marginMaintPct: netLiq > 0 ? r1((maintM / netLiq) * 100) : 0,
    dNetLiq: P ? r2(dPct(netLiq, n(P.net_liq_usd))) : 0,
    dCash: P ? r2(dPct(cash, n(P.cash_usd))) : 0,
    dayPnl: P ? r2(netLiq - n(P.net_liq_usd)) : 0,
  };
}

interface VegaBucket {
  bucket?: string;
  vega_usd?: number | null;
  n_positions?: number | null;
}

/** /portfolio/vega-per-tenor → mock VegaTenor[] (vega_usd $ → $k). */
export function adaptVegaPerTenor(raw: unknown): VegaTenor[] {
  const rows = Array.isArray(raw) ? (raw as VegaBucket[]) : [];
  return rows.map((b) => ({
    tenor: b.bucket ?? "",
    vega: r1(n(b.vega_usd) / 1000),
    n: b.n_positions ?? 0,
    pct: 0,
  }));
}

interface StatsResp {
  sharpe?: number | null;
  max_drawdown_pct?: number | null;
  current_drawdown_pct?: number | null;
  hit_rate?: number | null;
  cum_realized_usd?: number | null;
  cum_unrealized_usd?: number | null;
  n_closed?: number | null;
  n_reconciled_flat?: number | null;
  net_liq_change_usd?: number | null;
}

/** /portfolio/stats → mock perfStats ($ → $k ; hit_rate ratio → %). */
export function adaptPerfStats(raw: unknown): PerfStats {
  const s = (raw ?? {}) as StatsResp;
  return {
    cumRealized: r1(n(s.cum_realized_usd) / 1000),
    cumUnrealized: r1(n(s.cum_unrealized_usd) / 1000),
    maxDd: n(s.max_drawdown_pct),
    currentDd: n(s.current_drawdown_pct),
    sharpe: n(s.sharpe),
    hitRate: n(s.hit_rate) * 100,
    nClosed: n(s.n_closed),
    nReconciledFlat: n(s.n_reconciled_flat),
    netLiqChange: r1(n(s.net_liq_change_usd) / 1000),
    hitRateNull: s.hit_rate == null, // no genuine closes → hit-rate is undefined, not 0%
  };
}

/** /portfolio/daily-pnl → number[] of MARK-TO-MARKET P&L per day, in $k (Δ net-liq).
 * Realized-on-close reads flat while the book is open, so the bars are MTM. */
export function adaptDailyPnl(raw: unknown): number[] {
  const series = ((raw ?? {}) as { series?: { mtm_usd?: number | null }[] }).series ?? [];
  return series.map((d) => r1(n(d.mtm_usd) / 1000));
}

interface AttribTotals {
  actual_pnl_usd?: number | null;
  delta_pnl_usd?: number | null;
  gamma_pnl_usd?: number | null;
  vega_pnl_usd?: number | null;
  theta_pnl_usd?: number | null;
  residual_usd?: number | null;
}

/** /portfolio/pnl-attribution totals → the greek-pivot waterfall ($ → $k). */
export function adaptWaterfallGreek(raw: unknown): WaterfallStep[] {
  const t = ((raw ?? {}) as { totals?: AttribTotals }).totals ?? {};
  const k = (v: number | null | undefined): number => r1(n(v) / 1000);
  return [
    // Delta, Gamma, Vega, Theta — same order as the Risk tab's Portfolio greeks panel.
    { label: "Start", v: 0, type: "start" },
    { label: "Delta", sub: "Delta·dS", v: k(t.delta_pnl_usd), type: "neg" },
    { label: "Gamma", sub: "½Gamma(dS)²", v: k(t.gamma_pnl_usd), type: "pos" },
    { label: "Vega", sub: "Vega·dσ", v: k(t.vega_pnl_usd), type: "pos" },
    { label: "Theta", sub: "Theta·dt", v: k(t.theta_pnl_usd), type: "neg" },
    { label: "residual", sub: "unexplained", v: k(t.residual_usd), type: "resid" },
    { label: "Net", v: k(t.actual_pnl_usd), type: "net" },
  ];
}

// Stable colours per structure family (matches the mock palette).
const FAMILY_COLORS = ["var(--accent)", "#a78bfa", "var(--pos)", "var(--warn)", "var(--muted)"];

/** Book composition derived front-side from /positions/open : nominal + legs
 * grouped by structure family (the leading word of the structure label). */
export function deriveBookComposition(positions: Position[]): BookComposition {
  const byName = new Map<string, { nominal: number; legs: number }>();
  for (const p of positions) {
    const name = (p.structure || "—").trim();
    const cur = byName.get(name) ?? { nominal: 0, legs: 0 };
    cur.nominal += (p.nominal || 0) / 1e6; // € → M€
    cur.legs += 1;
    byName.set(name, cur);
  }
  const total = [...byName.values()].reduce((s, x) => s + x.nominal, 0) || 1;
  const byStructure = [...byName.entries()].map(([name, x], i) => ({
    name,
    nominal: r2(x.nominal),
    legs: x.legs,
    color: FAMILY_COLORS[i % FAMILY_COLORS.length]!,
    pct: (x.nominal / total) * 100,
  }));
  return {
    byStructure,
    legs: positions.length,
    totalNominal: r2([...byName.values()].reduce((s, x) => s + x.nominal, 0)),
  };
}

/** /portfolio/var → 1d VaR 95/99 + ES 99 ($k) + empirical histogram ($k bins).
 * `perTenor` is filled by the provider from /risk-per-tenor. */
export function adaptVar(raw: unknown): VarData {
  const v = (raw ?? {}) as {
    var_95_usd?: number | null;
    var_99_usd?: number | null;
    es_99_usd?: number | null;
    mean_daily_usd?: number | null;
    n_days?: number | null;
    method?: string | null;
    n_positions?: number | null;
    hist?: { lo?: number; hi?: number; count?: number }[];
  };
  const hist: HistBin[] = (v.hist ?? []).map((b) => ({
    lo: n(b.lo) / 1000,
    hi: n(b.hi) / 1000,
    count: n(b.count),
  }));
  // null stays null (short history) — see VarData: 0 would be read as a real
  // zero-risk VaR by every consumer.
  const k = (x: number | null | undefined): number | null => (typeof x === "number" ? x / 1000 : null);
  return {
    var95: k(v.var_95_usd),
    var99: k(v.var_99_usd),
    es99: k(v.es_99_usd),
    meanDaily: k(v.mean_daily_usd),
    nDays: v.n_days ?? 0,
    method: v.method ?? null,
    nPositions: v.n_positions ?? null,
    hist,
    perTenor: [],
  };
}

interface RiskTenorRow {
  bucket?: string;
  vega_usd?: number | null;
  vanna_usd?: number | null;
  volga_usd?: number | null;
  n_positions?: number | null;
}

/** /portfolio/risk-per-tenor → vega/vanna/volga by tenor, $ → $k (all displayed
 * with the "k" suffix). */
export function adaptRiskPerTenor(raw: unknown): TenorRisk[] {
  const rows = Array.isArray(raw) ? (raw as RiskTenorRow[]) : [];
  return rows.map((r) => ({
    tenor: r.bucket ?? "",
    vega: n(r.vega_usd) / 1000,
    vanna: n(r.vanna_usd) / 1000,
    volga: n(r.volga_usd) / 1000,
    n: r.n_positions ?? 0,
  }));
}

/** A net-liq sample: epoch-ms timestamp + value ($). */
export interface EquityPoint {
  t: number;
  v: number;
}

/** /portfolio/equity-curve → timestamped net-liq series for the equity chart.
 * The timestamp is kept so the chart can plot on a FIXED time axis (0→N days) with
 * empty zones where the window has no data, instead of stretching whatever points
 * exist across the full width. */
export function adaptEquityCurve(raw: unknown): EquityPoint[] {
  const rows = Array.isArray(raw)
    ? (raw as { timestamp?: string; net_liq_usd?: number | null }[])
    : [];
  return rows
    .map((p) => ({ t: p.timestamp ? Date.parse(p.timestamp) : NaN, v: n(p.net_liq_usd) }))
    .filter((p) => p.v > 0 && Number.isFinite(p.t));
}

/** Portfolio greek time-series — one timestamped ($) series per greek. */
export type GreekKey = "delta" | "gamma" | "vega" | "theta";
export type GreekSeries = Record<GreekKey, EquityPoint[]>;

/** /portfolio/greek-pnl-history → four cumulative Taylor-term series ($, start at 0)
 * for the Performance 2×2 greek-P&L grid. */
export function adaptGreekPnlHistory(raw: unknown): GreekSeries {
  const rows = Array.isArray(raw)
    ? (raw as {
        timestamp?: string;
        delta_pnl_usd?: number | null;
        gamma_pnl_usd?: number | null;
        vega_pnl_usd?: number | null;
        theta_pnl_usd?: number | null;
      }[])
    : [];
  const out: GreekSeries = { delta: [], gamma: [], vega: [], theta: [] };
  for (const r of rows) {
    const t = r.timestamp ? Date.parse(r.timestamp) : NaN;
    if (!Number.isFinite(t)) continue;
    out.delta.push({ t, v: n(r.delta_pnl_usd) });
    out.gamma.push({ t, v: n(r.gamma_pnl_usd) });
    out.vega.push({ t, v: n(r.vega_pnl_usd) });
    out.theta.push({ t, v: n(r.theta_pnl_usd) });
  }
  return out;
}

/** Net-liq valuation decomposition — one timestamped ($) series per component
 * (USD cash / EUR cash / contracts) plus the net-liq total they stack to. */
export type ValuationKey = "usd" | "eur" | "contracts" | "total";
export type ValuationSeries = Record<ValuationKey, EquityPoint[]>;

/** /portfolio/valuation-history → the Account panel's stacked valuation chart. */
export function adaptValuationHistory(raw: unknown): ValuationSeries {
  const rows = Array.isArray(raw)
    ? (raw as {
        timestamp?: string;
        net_liq_usd?: number | null;
        usd_cash_usd?: number | null;
        eur_cash_usd?: number | null;
        contracts_usd?: number | null;
      }[])
    : [];
  const out: ValuationSeries = { usd: [], eur: [], contracts: [], total: [] };
  for (const r of rows) {
    const t = r.timestamp ? Date.parse(r.timestamp) : NaN;
    if (!Number.isFinite(t)) continue;
    if (r.usd_cash_usd != null) out.usd.push({ t, v: r.usd_cash_usd });
    if (r.eur_cash_usd != null) out.eur.push({ t, v: r.eur_cash_usd });
    if (r.contracts_usd != null) out.contracts.push({ t, v: r.contracts_usd });
    if (r.net_liq_usd != null) out.total.push({ t, v: r.net_liq_usd });
  }
  return out;
}

/** One row of the greek-P&L attribution matrix (all $): the Taylor terms of a group's
 * P&L over the window, plus the group's actual P&L (Σ). Terms foot to actual (± residual).
 *
 * `null` = not measurable over this window (no t-1 snapshot for that leg, e.g. it was
 * opened inside the window). Kept null all the way to the cell, which renders "—":
 * an unmeasurable term is not a flat $0. */
export interface AttribRow {
  label: string;
  delta: number | null; // δ·dS
  gamma: number | null; // ½Γ·dS²
  vega: number | null; // V·dσ
  theta: number | null; // Θ·dt
  residual: number | null;
  actual: number | null; // realized P&L over the window
}
export interface AttribMatrix {
  rows: AttribRow[];
  totals: AttribRow; // Σ over rows per column (= the by-greek bridge)
}

/** /portfolio/pnl-attribution?group_by= → greek-P&L × axis matrix (all $). */
export function adaptAttributionMatrix(raw: unknown): AttribMatrix {
  const o = (raw ?? {}) as {
    groups?: Record<string, number | string | null>[];
    totals?: Record<string, number | null>;
  };
  const row = (g: Record<string, number | string | null>, label: string): AttribRow => ({
    label,
    delta: nn(g.delta_pnl_usd),
    gamma: nn(g.gamma_pnl_usd),
    vega: nn(g.vega_pnl_usd),
    theta: nn(g.theta_pnl_usd),
    residual: nn(g.residual_usd),
    actual: nn(g.actual_pnl_usd),
  });
  return {
    rows: (o.groups ?? []).map((g) => row(g, String(g.label ?? "—"))),
    totals: row(o.totals ?? {}, "Total"),
  };
}

/** Per-position attribution row: position metadata + the Taylor P&L terms ($). */
export interface PositionAttribRow {
  id: number;
  tradeId: number | null;
  contractId: number | null;
  product: string;
  type: string; // trade_structure.structure_type (the booked classifier verdict)
  structure: string;
  side: string;
  tenor: string;
  iv: number; // %
  nominal: number; // €
  // null = not measurable over the window (see AttribRow).
  actual: number | null;
  delta: number | null;
  gamma: number | null;
  vega: number | null;
  theta: number | null;
  residual: number | null;
}
export interface PositionAttribMatrix {
  rows: PositionAttribRow[];
  totals: AttribRow;
}

/** /portfolio/pnl-attribution (no group_by) → per-leg attribution matrix (IB legs). */
export function adaptPositionAttribution(raw: unknown): PositionAttribMatrix {
  const o = (raw ?? {}) as { per_position?: Record<string, unknown>[] };
  const rows: PositionAttribRow[] = (o.per_position ?? [])
    .filter((r) => r.source === "ib_live")
    .map((r) => ({
      id: n(r.id),
      tradeId: r.trade_id == null ? null : n(r.trade_id),
      contractId: r.contract_id == null ? null : n(r.contract_id),
      product: String(r.product_label ?? "—"),
      type: String(r.structure_type ?? ""),
      structure: String(r.structure ?? "—"),
      side: String(r.side ?? "—"),
      tenor: String(r.tenor ?? "—"),
      iv: n(r.iv) * 100,
      nominal: n(r.nominal_eur),
      actual: nn(r.actual_pnl_usd),
      delta: nn(r.delta_pnl_usd),
      gamma: nn(r.gamma_pnl_usd),
      vega: nn(r.vega_pnl_usd),
      theta: nn(r.theta_pnl_usd),
      residual: nn(r.residual_usd),
    }));
  // Total footed on the visible IB legs (not the endpoint totals, which include
  // booked). Sums only the legs that carry the term; null when none of them does,
  // so a column nobody could measure stays "—" instead of totalling to $0.
  const s = (sel: (r: PositionAttribRow) => number | null): number | null => {
    const vals = rows.map(sel).filter((v): v is number => v !== null);
    return vals.length ? vals.reduce((a, v) => a + v, 0) : null;
  };
  return {
    rows,
    totals: {
      label: "Total",
      actual: s((r) => r.actual),
      delta: s((r) => r.delta),
      gamma: s((r) => r.gamma),
      vega: s((r) => r.vega),
      theta: s((r) => r.theta),
      residual: s((r) => r.residual),
    },
  };
}

/** A trade open/close event for the EUR/USD ticker overlay (one entry per side). */
export interface TradeEvent {
  t: number; // epoch ms of the event
  kind: "open" | "close";
  id: number;
  type: string;
  spot: number | null; // entry_spot on opens (else the marker anchors to the candle)
  pnl: number | null; // realized net P&L (closes)
}

/** /portfolio/trade-markers → flat open/close events for the ticker overlay. */
export function adaptTradeMarkers(raw: unknown): TradeEvent[] {
  const rows = Array.isArray(raw)
    ? (raw as {
        id?: number;
        type?: string;
        opened_at?: string | null;
        entry_spot?: number | null;
        closed_at?: string | null;
        net_pnl_usd?: number | null;
      }[])
    : [];
  const out: TradeEvent[] = [];
  for (const r of rows) {
    const id = Number(r.id ?? 0);
    const type = String(r.type ?? "trade");
    if (r.opened_at) out.push({ t: Date.parse(r.opened_at), kind: "open", id, type, spot: r.entry_spot ?? null, pnl: null });
    if (r.closed_at) out.push({ t: Date.parse(r.closed_at), kind: "close", id, type, spot: null, pnl: r.net_pnl_usd ?? null });
  }
  return out.filter((e) => Number.isFinite(e.t));
}

export interface StressGridData {
  axis: string;
  output: string;
  currentSpot: number | null;
  spotBins: number[]; // bp columns
  rowBins: number[]; // 2nd-axis rows
  rowUnit: string; // "vp" | "d"
  nPositions: number;
  grid: number[][]; // [row][col], raw $ (full-BS reval)
}

/** /portfolio/stress-grid?axis=&output= → one (axis, output) matrix. Rows = the
 * 2nd-axis bins, cols = spot bp bins. `null` when the book is empty or no spot
 * could be resolved (backend returns `grid: []`). Values stay in raw $ — the grid
 * component formats with the desk's signed-k convention. */
export function adaptStressGrid(raw: unknown): StressGridData | null {
  const o = (raw ?? {}) as {
    axis?: string;
    output?: string;
    current_spot?: number | null;
    spot_bins_bps?: number[];
    row_bins?: number[];
    row_unit?: string;
    n_positions?: number;
    grid?: number[][];
  };
  const grid = Array.isArray(o.grid) ? o.grid : [];
  if (!grid.length) return null;
  return {
    axis: o.axis ?? "",
    output: o.output ?? "pnl",
    currentSpot: typeof o.current_spot === "number" ? o.current_spot : null,
    spotBins: Array.isArray(o.spot_bins_bps) ? o.spot_bins_bps : [],
    rowBins: Array.isArray(o.row_bins) ? o.row_bins : [],
    rowUnit: o.row_unit ?? "vp",
    nPositions: o.n_positions ?? 0,
    grid,
  };
}

export interface LiveLadderRow {
  label: string;
  pnl: number;
  delta: number;
  gamma: number;
  vega: number;
  hedge: number;
  isNow: boolean;
  spot: number | null;
}

export interface LiveLadder {
  axis: string;
  unit: string;
  rows: LiveLadderRow[];
}

interface LadderRowResp {
  axis_value?: number | null;
  pnl_usd?: number | null;
  delta_usd?: number | null;
  gamma_usd_per_pip?: number | null;
  vega_usd_per_volpt?: number | null;
  hedge_delta_usd?: number | null;
  spot?: number | null;
}

/** /portfolio/greeks-ladder?axis= → per-bin P&L + Δ/Γ/Vega + hedge-Δ along one
 * axis (full-BS reval). θ/vanna/volga aren't in the row payload (backend subset
 * — a trivial extension). Empty rows when the book/spot is missing. */
export function adaptGreeksLadder(raw: unknown): LiveLadder {
  const o = (raw ?? {}) as { axis?: string; unit?: string; rows?: LadderRowResp[] };
  const unit = o.unit ?? "";
  const fmtLbl = (v: number): string =>
    unit === "d" ? (v === 0 ? "now" : v + "d") : (v > 0 ? "+" : "") + v + unit;
  const rows = (Array.isArray(o.rows) ? o.rows : []).map((r) => ({
    label: fmtLbl(n(r.axis_value)),
    pnl: n(r.pnl_usd),
    delta: n(r.delta_usd),
    gamma: n(r.gamma_usd_per_pip),
    vega: n(r.vega_usd_per_volpt),
    hedge: n(r.hedge_delta_usd),
    isNow: n(r.axis_value) === 0,
    spot: typeof r.spot === "number" ? r.spot : null,
  }));
  return { axis: o.axis ?? "", unit, rows };
}

export interface PinRiskRow {
  product: string;
  tradeId: number | null;
  strike: number;
  distPips: number;
  dte: number;
  pnlNow: number;
  pnlAtPin: number;
}

interface PinRowResp {
  product_label?: string | null;
  trade_id?: number | null;
  structure?: string | null;
  strike?: number | null;
  distance_pips?: number | null;
  dte_days?: number | null;
  pnl_now_usd?: number | null;
  pnl_at_pin_usd?: number | null;
}

export interface MarginalVarRow {
  trade: string; // trade / package id (T-… / PKG-…) or "—"
  label: string; // product label
  factor: string; // dominant greek: spot | level | skew | curv
  standalone: number; // USD loss
  component: number; // USD contribution to portfolio VaR (signed)
  pct: number;
}

export interface MarginalVarData {
  rows: MarginalVarRow[];
  portfolioVar: number;
  diversification: number;
  nDays: number;
}

interface MVarResp {
  positions?: {
    label?: string;
    trade?: string | null;
    factor?: string;
    standalone_usd?: number | null;
    component_usd?: number | null;
    pct?: number | null;
  }[];
  total?: { portfolio_var_usd?: number | null; diversification_pct?: number | null } | null;
  n_days?: number;
}

/** /portfolio/marginal-var → per-position standalone + component VaR. */
export function adaptMarginalVar(raw: unknown): MarginalVarData {
  const o = (raw ?? {}) as MVarResp;
  const rows = (o.positions ?? []).map((r) => ({
    trade: r.trade ?? "—",
    label: r.label ?? "",
    factor: r.factor ?? "spot",
    standalone: n(r.standalone_usd),
    component: n(r.component_usd),
    pct: n(r.pct),
  }));
  return {
    rows,
    portfolioVar: n(o.total?.portfolio_var_usd),
    diversification: n(o.total?.diversification_pct),
    nDays: o.n_days ?? 0,
  };
}

/** /portfolio/pin-risk → per-option pin exposure (P&L now vs at the strike). */
export function adaptPinRisk(raw: unknown): PinRiskRow[] {
  const rows = ((raw ?? {}) as { rows?: PinRowResp[] }).rows ?? [];
  return rows.map((r) => ({
    product: r.product_label || r.structure || "—",
    tradeId: r.trade_id ?? null,
    strike: n(r.strike),
    distPips: Math.round(n(r.distance_pips)),
    dte: n(r.dte_days),
    pnlNow: n(r.pnl_now_usd),
    pnlAtPin: n(r.pnl_at_pin_usd),
  }));
}
