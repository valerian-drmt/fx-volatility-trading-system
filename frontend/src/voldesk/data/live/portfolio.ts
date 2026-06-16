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
import type { BookComposition, PerfStats, VegaTenor, WaterfallStep } from "../extended";

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
