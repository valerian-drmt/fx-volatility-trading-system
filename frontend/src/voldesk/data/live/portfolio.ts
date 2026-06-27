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
  account as mockAccount,
  type Position,
} from "../core";
import type { HistBin, TenorRisk, VarData } from "../deskData";
import type { BookComposition, PerfStats, VarFactor, VegaTenor, WaterfallStep } from "../extended";

const n = (v: unknown): number => (typeof v === "number" ? v : 0);
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

/** /portfolio/account → the mock `account` shape (deltas from prev_24h). */
export function adaptAccount(raw: unknown): AccountState {
  const o = (raw ?? {}) as { latest?: AccountSnap | null; prev_24h?: AccountSnap | null };
  const L = o.latest ?? {};
  const P = o.prev_24h ?? null;
  const netLiq = n(L.net_liq_usd);
  const cash = n(L.cash_usd);
  const initM = n(L.init_margin_req);
  const maintM = n(L.maint_margin_req);
  const dPct = (now: number, prev: number): number => (prev ? ((now - prev) / prev) * 100 : 0);
  return {
    ...mockAccount,
    netLiq,
    cash,
    marginInit: initM,
    marginMaint: maintM,
    excessLiq: n(L.excess_liquidity),
    cushion: n(L.cushion),
    nPositions: L.open_positions_count ?? mockAccount.nPositions,
    marginInitPct: netLiq > 0 ? r1((initM / netLiq) * 100) : mockAccount.marginInitPct,
    marginMaintPct: netLiq > 0 ? r1((maintM / netLiq) * 100) : mockAccount.marginMaintPct,
    dNetLiq: P ? r2(dPct(netLiq, n(P.net_liq_usd))) : mockAccount.dNetLiq,
    dCash: P ? r2(dPct(cash, n(P.cash_usd))) : mockAccount.dCash,
    dayPnl: P ? r2(netLiq - n(P.net_liq_usd)) : mockAccount.dayPnl,
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
  };
}

/** /portfolio/daily-pnl → number[] of realized P&L per day, in $k. */
export function adaptDailyPnl(raw: unknown): number[] {
  const series = ((raw ?? {}) as { series?: { realized_usd?: number | null }[] }).series ?? [];
  return series.map((d) => r1(n(d.realized_usd) / 1000));
}

interface AttribTotals {
  actual_pnl?: number | null;
  delta_pnl?: number | null;
  gamma_pnl?: number | null;
  vega_pnl?: number | null;
  theta_pnl?: number | null;
  residual?: number | null;
}

/** /portfolio/pnl-attribution totals → the greek-pivot waterfall ($ → $k). */
export function adaptWaterfallGreek(raw: unknown): WaterfallStep[] {
  const t = ((raw ?? {}) as { totals?: AttribTotals }).totals ?? {};
  const k = (v: number | null | undefined): number => r1(n(v) / 1000);
  return [
    { label: "Start", v: 0, type: "start" },
    { label: "+Γ", sub: "½Γ(dS)²", v: k(t.gamma_pnl), type: "pos" },
    { label: "+V", sub: "V·dσ", v: k(t.vega_pnl), type: "pos" },
    { label: "−Θ", sub: "Θ·dt", v: k(t.theta_pnl), type: "neg" },
    { label: "±Δ", sub: "Δ·dS", v: k(t.delta_pnl), type: "neg" },
    { label: "residual", sub: "unexplained", v: k(t.residual), type: "resid" },
    { label: "Net", v: k(t.actual_pnl), type: "net" },
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
    n_days?: number | null;
    hist?: { lo?: number; hi?: number; count?: number }[];
  };
  const hist: HistBin[] = (v.hist ?? []).map((b) => ({
    lo: n(b.lo) / 1000,
    hi: n(b.hi) / 1000,
    count: n(b.count),
  }));
  return {
    var95: n(v.var_95_usd) / 1000,
    var99: n(v.var_99_usd) / 1000,
    es99: n(v.es_99_usd) / 1000,
    nDays: v.n_days ?? 0,
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

/** /portfolio/equity-curve → net-liq series for the equity chart. */
export function adaptEquityCurve(raw: unknown): number[] {
  const rows = Array.isArray(raw) ? (raw as { net_liq_usd?: number | null }[]) : [];
  return rows.map((p) => n(p.net_liq_usd)).filter((v) => v > 0);
}

export type StressAxis = "spot-vol" | "spot-time" | "spot-skew" | "spot-fly";

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

export interface ScenarioPoint {
  x: number; pnl: number; delta: number; gamma: number; vega: number; theta: number;
}

/** /portfolio/scenarios by_spot rows → ScenarioPoint[] (spot-shock full reval). */
export function adaptScenarios(raw: unknown): ScenarioPoint[] {
  const r = raw as { by_spot?: Array<Record<string, number>> } | null;
  return (r?.by_spot ?? []).map((d) => ({
    x: Number(d.step_pct ?? 0),
    pnl: Number(d.pnl_usd ?? 0),
    delta: Number(d.delta_usd ?? 0),
    gamma: Number(d.gamma_usd_per_pip ?? 0),
    vega: Number(d.vega_usd_per_volpt ?? 0),
    theta: Number(d.theta_usd_per_day ?? 0),
  }));
}

export interface LiveCoverage {
  convexity: number; carry: number; ratio: number;
  gammaPnl: number; vegaPnl: number; thetaPaid: number;
  posture: string; windowLabel: string;
}

/** /portfolio/pnl-attribution totals → realized survival ratio (convexity ÷ carry).
 *  $ → $k. Empty book → zeros (ratio 0). Perf trio (RoM/RoVaR/Sharpe) + the history
 *  sparkline need realized trading history → deferred (R12+, like backtest). */
export function adaptCoverage(raw: unknown): LiveCoverage {
  const t = (raw as { totals?: Record<string, number>; lookback_hours?: number } | null) ?? {};
  const tot = t.totals ?? {};
  const gammaPnl = +(Number(tot["gamma_pnl_usd"] ?? 0) / 1000).toFixed(1);
  const vegaPnl = +(Number(tot["vega_pnl_usd"] ?? 0) / 1000).toFixed(1);
  const thetaPaid = +(Math.abs(Number(tot["theta_pnl_usd"] ?? 0)) / 1000).toFixed(1);
  const convexity = +(gammaPnl + vegaPnl).toFixed(1);
  const ratio = thetaPaid > 0 ? +(convexity / thetaPaid).toFixed(2) : 0;
  const hrs = Number(t.lookback_hours ?? 24);
  return {
    convexity, carry: thetaPaid, ratio, gammaPnl, vegaPnl, thetaPaid,
    posture: gammaPnl >= 0 ? "long gamma · Θ−" : "short gamma · Θ+",
    windowLabel: hrs >= 24 ? `${Math.round(hrs / 24)}j` : `${hrs}h`,
  };
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

export type LadderAxis = "spot" | "vol" | "time" | "skew" | "fly";

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
  strike: number;
  distPips: number;
  dte: number;
  pnlNow: number;
  pnlAtPin: number;
}

interface PinRowResp {
  product_label?: string | null;
  structure?: string | null;
  strike?: number | null;
  distance_pips?: number | null;
  dte_days?: number | null;
  pnl_now_usd?: number | null;
  pnl_at_pin_usd?: number | null;
}

export interface VegaPcaRow {
  mode: string;
  name: string;
  vega: number; // $k per unit-PC move
  var: number; // variance explained %
}

interface VegaPcaResp {
  pcs?: { pc?: number; name?: string; variance_pct?: number | null; vega_usd?: number | null }[];
}

/** /portfolio/vega-pca → book vega projected on PC1/2/3 ($ → $k). */
export function adaptVegaPca(raw: unknown): VegaPcaRow[] {
  const pcs = ((raw ?? {}) as VegaPcaResp).pcs ?? [];
  return pcs.map((p) => ({
    mode: "PC" + (p.pc ?? 0),
    name: p.name ?? "",
    vega: r1(n(p.vega_usd) / 1000),
    var: n(p.variance_pct),
  }));
}

export interface GreekLimits {
  deltaCapUsd: number;
  vegaCapUsd: number;
  gammaCapPip: number;
  crossBudgetUsd: number;
  lossBudgetUsd: number;
  navBaseUsd: number;
  navLiveUsd: number;
  regimeMult: number;
}

/** /portfolio/greek-limits → derived stress-loss caps (greek-limits-spec §2). */
export function adaptGreekLimits(raw: unknown): GreekLimits {
  const o = (raw ?? {}) as {
    delta_cap_usd?: number | null;
    vega_cap_usd?: number | null;
    gamma_cap_pip?: number | null;
    cross_budget_usd?: number | null;
    loss_budget_usd?: number | null;
    nav_base_usd?: number | null;
    nav_live_usd?: number | null;
    regime_mult?: number | null;
  };
  return {
    deltaCapUsd: n(o.delta_cap_usd),
    vegaCapUsd: n(o.vega_cap_usd),
    gammaCapPip: n(o.gamma_cap_pip),
    crossBudgetUsd: n(o.cross_budget_usd),
    lossBudgetUsd: n(o.loss_budget_usd),
    navBaseUsd: n(o.nav_base_usd),
    navLiveUsd: n(o.nav_live_usd),
    regimeMult: o.regime_mult ?? 1,
  };
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

const _FACTOR_COLOR: Record<string, string> = {
  spot: "var(--warn)",
  level: "var(--accent)",
  skew: "#a78bfa",
  curv: "var(--pos)",
};

/** /portfolio/var-factors → scenario VaR by factor ($ → $k) for the FactorStack. */
export function adaptVarFactors(raw: unknown): VarFactor[] {
  const factors = ((raw ?? {}) as {
    factors?: { key?: string; label?: string; var_usd?: number | null }[];
  }).factors ?? [];
  return factors.map((f) => ({
    key: f.key ?? "",
    label: f.label ?? "",
    v: r1(n(f.var_usd) / 1000),
    color: _FACTOR_COLOR[f.key ?? ""] ?? "var(--muted)",
  }));
}

/** /portfolio/pin-risk → per-option pin exposure (P&L now vs at the strike). */
export function adaptPinRisk(raw: unknown): PinRiskRow[] {
  const rows = ((raw ?? {}) as { rows?: PinRowResp[] }).rows ?? [];
  return rows.map((r) => ({
    product: r.product_label || r.structure || "—",
    strike: n(r.strike),
    distPips: Math.round(n(r.distance_pips)),
    dte: n(r.dte_days),
    pnlNow: n(r.pnl_now_usd),
    pnlAtPin: n(r.pnl_at_pin_usd),
  }));
}
