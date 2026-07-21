/**
 * Live adapter (R11 PR 6r.1): backend trade/portfolio reads → the voldesk Trade
 * view (read-only part).
 *
 * Sources:
 *   - GET /positions/open  rich per-leg rows (greeks/pnl/iv/nominal)
 *   - GET /trade/limits    risk caps (keyed dict)
 *   - GET /regime/events   macro calendar
 *   - GET /trade/book      capital / margin state
 *
 * Net book greeks are DERIVED by summing the live per-leg greeks — the same
 * "one engine · = Risk" invariant the mock encodes (no separate aggregate call,
 * which would risk the book not footing). Per-unit greeks (var1d99, beta, 24h
 * deltas) have no live source here → kept from the mock for the Trade context;
 * Risk view (PR 5) wires VaR properly via G-risk.
 *
 * ⚠️ Cash-by-currency (holdings donut) needs /account/cash (G-trade gap) — stays
 * mock. Vanna/volga units (backend $ vs mock $k) are flagged in 09.
 */
import {
  type AccountState,
  type Cash,
  type Greeks,
  type Limits,
  limits as defaultLimits,
  type MacroEvent,
  type Position,
} from "../core";
import { EMPTY_ACCOUNT, EMPTY_GREEKS } from "../neutral";

interface BackendPosition {
  id?: number;
  package_id?: string | null;
  trade_id?: string | null;
  contract_id?: number | null;
  product_label?: string | null;
  structure?: string | null;
  side?: string;
  quantity?: number | null;
  tenor?: string | null;
  expiry?: string | null;
  current_pnl_usd?: number | null;
  market_price?: number | null;
  contract_price_entry?: number | null;
  nominal_eur?: number | null;
  delta_usd?: number | null;
  gamma_usd?: number | null;
  vega_usd?: number | null;
  theta_usd?: number | null;
  iv?: number | null;
  vanna_usd?: number | null;
  volga_usd?: number | null;
  timestamp?: string | null;
  entry_timestamp?: string | null;
}

const n = (v: number | null | undefined): number => (typeof v === "number" ? v : 0);

/** days-to-expiry from an ISO date (≥ 0), or 0 when unknown. */
function dteFrom(expiry: string | null | undefined, now: number): number {
  if (!expiry) return 0;
  const t = Date.parse(expiry);
  if (Number.isNaN(t)) return 0;
  return Math.max(0, Math.round((t - now) / 86_400_000));
}

export function adaptPositions(raw: unknown, now: number): Position[] {
  const rows = Array.isArray(raw) ? (raw as BackendPosition[]) : [];
  return rows.map((r) => {
    const nominal = n(r.nominal_eur);
    const pnl = n(r.current_pnl_usd);
    return {
      id: String(r.id ?? ""),
      packageId: r.package_id ?? "",
      tradeId: r.trade_id ?? "",
      conId: r.contract_id ?? 0,
      product: r.product_label ?? "",
      structure: r.structure ?? r.product_label ?? "—",
      side: r.side ?? "BUY",
      qty: n(r.quantity),
      tenor: r.tenor ?? "",
      expiry: r.expiry ?? "",
      strike: 0, // backend row carries no strike → table omits the "K" hint
      entry: n(r.contract_price_entry),
      mark: n(r.market_price),
      iv: n(r.iv) * 100, // backend stores IV as a fraction (0.054) → show percent (5.4)
      pnl,
      nominal,
      // δ/Γ/vega/θ stay in $ (the view auto-scales via gkc/gk$). vanna/volga
      // are displayed with a hardcoded "k" suffix → convert $ → $k (engine
      // writes them in $: qty·bs_vanna·mult·0.01). See engines/risk/engine.py.
      delta: n(r.delta_usd),
      gamma: n(r.gamma_usd),
      vega: n(r.vega_usd),
      theta: n(r.theta_usd),
      vanna: n(r.vanna_usd) / 1000,
      volga: n(r.volga_usd) / 1000,
      updated: r.timestamp ?? "",
      opened: r.entry_timestamp ?? "",
      pnlPct: nominal ? (pnl / nominal) * 100 : 0,
      dte: dteFrom(r.expiry, now),
    };
  });
}

/** Net book greeks = Σ per-leg live greeks; non-net fields (per-unit greeks,
 * VaR, 24h deltas) have no live source here → neutral zeros, never fabricated. */
export function deriveNetGreeks(positions: Position[]): Greeks {
  const sum = (f: (p: Position) => number): number => positions.reduce((s, p) => s + f(p), 0);
  return {
    ...EMPTY_GREEKS,
    netDelta: sum((p) => p.delta),
    netGamma: sum((p) => p.gamma),
    netVega: sum((p) => p.vega),
    netTheta: sum((p) => p.theta),
    netVanna: sum((p) => p.vanna),
    netVolga: sum((p) => p.volga),
    netNominal: sum((p) => p.nominal),
    netUnreal: sum((p) => p.pnl),
  };
}

interface TradeBook {
  capital_total_usd?: number | null;
  margin_used_usd?: number | null;
}

/** Margin / excess-liquidity from /trade/book; fields the endpoint doesn't
 * carry stay neutral zeros (the views render "—" for absent headline money). */
export function adaptAccount(raw: unknown): AccountState {
  const b = (raw ?? {}) as TradeBook;
  const cap = n(b.capital_total_usd);
  const used = n(b.margin_used_usd);
  return {
    ...EMPTY_ACCOUNT,
    netLiq: cap,
    marginInit: used,
    marginInitPct: cap > 0 ? (used / cap) * 100 : 0,
    excessLiq: cap > 0 ? cap - used : 0,
  };
}

interface BackendLimit {
  value?: number | null;
  unit?: string | null;
}

/** /trade/limits keyed dict → the limits struct (cap+unit per greek); keys the
 * backend doesn't configure fall back to the static default caps. */
export function adaptLimits(raw: unknown): Limits {
  const d = (raw ?? {}) as Record<string, BackendLimit>;
  const cap = (key: string, fallback: { cap: number; unit: string }) => {
    const l = d[key];
    return l ? { cap: n(l.value) || fallback.cap, unit: l.unit ?? fallback.unit } : fallback;
  };
  const scalar = (key: string, fallback: number) => {
    const l = d[key];
    return l && typeof l.value === "number" ? l.value : fallback;
  };
  return {
    gamma: cap("gamma", defaultLimits.gamma),
    vega: cap("vega", defaultLimits.vega),
    vanna: cap("vanna", defaultLimits.vanna),
    var99: cap("var99", defaultLimits.var99),
    deltaBandUsd: scalar("deltaBandUsd", defaultLimits.deltaBandUsd),
    skewVarPct: scalar("skewVarPct", defaultLimits.skewVarPct),
    // Live vega budget — the desk's configured max book vega (no mock fallback).
    vegaCapUsd: scalar("max_book_vega_usd", 0),
  };
}

interface BackendCashRow {
  ccy?: string;
  settled?: number | null;
  unsettled?: number | null;
  rate?: number | null;
  usd_value?: number | null;
}

/** /portfolio/cash → the mock Cash[] shape (usd_value → usd). Unvalued
 * currencies (no rate) are dropped — the donut only plots USD-valued legs. */
export function adaptCash(raw: unknown): Cash[] {
  const rows = ((raw ?? {}) as { currencies?: BackendCashRow[] }).currencies ?? [];
  return rows
    .filter((c) => typeof c.usd_value === "number")
    .map((c) => ({
      ccy: c.ccy ?? "",
      settled: n(c.settled),
      unsettled: n(c.unsettled),
      rate: n(c.rate),
      usd: n(c.usd_value),
    }));
}

const IMPACT = new Set(["high", "medium", "low"]);

interface BackendEvent {
  event_type?: string;
  impact?: string;
  region?: string;
  scheduled_at?: string;
  description?: string | null;
  source?: string;
}

/** Relative "in 3h" / "in 2d 4h" / "now" string for an upcoming event. */
function inWords(at: number, now: number): string {
  const ms = at - now;
  if (ms <= 0) return "now";
  const h = ms / 3.6e6;
  if (h < 24) return `${Math.round(h)}h`;
  const d = Math.floor(h / 24);
  return `${d}d ${Math.round(h - d * 24)}h`;
}

/** /regime/events → MacroEvent[]. `date` keeps the ISO string (the view's
 * parseEvt Date.parse's it) ; `in` is the relative countdown at fetch time. */
export function adaptEvents(raw: unknown, now: number): MacroEvent[] {
  const rows = Array.isArray(raw) ? (raw as BackendEvent[]) : [];
  return rows.map((e) => {
    const at = e.scheduled_at ? Date.parse(e.scheduled_at) : NaN;
    return {
      date: e.scheduled_at ?? "", // ISO — parseEvt() in the view Date.parse's it
      country: e.region ?? "",
      impact: e.impact && IMPACT.has(e.impact) ? e.impact : "low",
      in: Number.isNaN(at) ? "" : inWords(at, now),
      code: e.event_type ?? "",
      content: e.description ?? e.event_type ?? "",
      src: e.source ?? "",
    };
  });
}
