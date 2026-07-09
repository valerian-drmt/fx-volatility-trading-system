/**
 * VOLDESK — OpenPositionsTable (rich net-strip + per-leg table) + CashHoldings.
 * Ported from the prototype's `js/positions_table.jsx`. Used by Trade and
 * Portfolio. Net greeks read the single reconciled store (DATA.greeks), so the
 * book foots identically to Risk.
 */
import { Fragment, useState } from "react";
import { pnlCls } from "./format";
import { DATA, fmt } from "../data";
import type { Greeks, Position } from "../data";

// compact signed formatter for per-leg / net greek cells (±N · ±N.Nk · ±N.NNM).
// NOTE: distinct from common's gk$ — this one omits the "$" prefix by design.
function gkc(v: number | null | undefined): string {
  if (v == null) return "—";
  const s = v < 0 ? "-" : "+";
  const a = Math.abs(v);
  if (a >= 1e6) return s + (a / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return s + (a / 1e3).toFixed(1) + "k";
  return s + Math.round(a);
}

// ── Trade grouping : one main line per trade + its legs as sub-rows ──
// The flat backend feed is per-leg (Vanilla Call / Vanilla Put …) all tagged
// with the same trade_id. We group by trade so a Risk Reversal reads as ONE
// summary line (net greeks / P&L) with the two legs indented under it.
interface PosGroup {
  key: string;
  tradeId: string;
  legs: Position[];
}

function groupByTrade(rows: Position[]): PosGroup[] {
  const groups: PosGroup[] = [];
  const idx: Record<string, number> = {};
  for (const p of rows) {
    // rows with no trade_id (unlinked IB positions) stay their own singleton
    const key = p.tradeId ? "T" + p.tradeId : "S" + p.id;
    if (idx[key] === undefined) {
      idx[key] = groups.length;
      groups.push({ key, tradeId: p.tradeId, legs: [] });
    }
    groups[idx[key]]!.legs.push(p);
  }
  return groups;
}

const _gcd = (a: number, b: number): number => (b === 0 ? a : _gcd(b, a % b));

// Wing derivation — bucket each leg's strike into a smile delta pillar so the
// vertical structures read like "Call Spread ATM/10Δ" / "Risk Reversal 25Δ". The
// backend product_label carries the wing for some structures (Strangle) but not
// all; we recover it from the strikes with the same nearest-pillar bucketing as
// the order-ticket strike ladder.
function _legStrikeNum(p: Position): number | null {
  if (p.strike && p.strike > 0) return p.strike;
  const m = /\s[CP](\d{3,5})$/.exec(p.structure || "");
  return m ? parseInt(m[1]!, 10) / 1000 : null;
}
function _legLevel(p: Position): string | null {
  return DATA.strikeToWing(_legStrikeNum(p), p.tenor);
}
const _rank = (lvl: string): number => (lvl === "ATM" ? 50 : parseInt(lvl, 10));
function deriveWing(legs: Position[], base: string): string | null {
  const levels = legs.map(_legLevel).filter((x): x is string => x != null);
  if (levels.length < 2) return null;
  if (/Strangle|Risk Reversal/.test(base)) return levels[0]!; // symmetric → one Δ
  // vertical spread : near (higher |Δ|, closer to F) / far (lower |Δ|)
  const uniq = [...new Set(levels)].sort((a, b) => _rank(b) - _rank(a));
  return uniq.length >= 2 ? `${uniq[0]}/${uniq[1]}` : (uniq[0] ?? null);
}

// Structure name for the main line. Prefer a shared explicit label (the mock and
// any backend that fills `structure` with a real name); otherwise infer it from
// the legs (real per-leg rows carry product_label + side + strike, not a name).
function structureName(legs: Position[]): string {
  const base = ((): string => {
    const s0 = legs[0]!.structure;
    if (s0 && s0 !== "—" && legs.every((l) => l.structure === s0)) return s0;
    if (legs.length === 1) return legs[0]!.product || s0 || "—";
    const calls = legs.filter((l) => /call/i.test(l.product));
    const puts = legs.filter((l) => /put/i.test(l.product));
    const n = legs.length;
    if (n === 2 && calls.length === 1 && puts.length === 1) {
      if (calls[0]!.side !== puts[0]!.side) return "Risk Reversal";
      return calls[0]!.strike && calls[0]!.strike === puts[0]!.strike ? "Straddle" : "Strangle";
    }
    if (n === 2 && calls.length === 2) return "Call Spread";
    if (n === 2 && puts.length === 2) return "Put Spread";
    if (n === 3) return "Butterfly";
    if (n === 4) return "Condor";
    return `Structure · ${n} legs`;
  })();
  // Append the wing pillars for the wing-bearing verticals when the label doesn't
  // already carry them (e.g. "Risk Reversal" → "Risk Reversal 25Δ", "Call Spread"
  // → "Call Spread ATM/10Δ" ; a backend "Strangle 10Δ" already has it → skip).
  if (!/\d+Δ|ATM/.test(base) && /^(Risk Reversal|Call Spread|Put Spread|Strangle)\b/.test(base)) {
    const wing = deriveWing(legs, base);
    if (wing) return `${base} ${wing}`;
  }
  return base;
}

// Structure-level side (BUY/SELL) — structures have a side too. All legs agree
// (strangle/straddle) → that side ; a count majority (butterfly: 2 long wings vs
// 1 short body) → the majority ; a 2-leg split (RR / spread) → the first (entry)
// leg's side.
function structureSide(legs: Position[]): string {
  const buys = legs.filter((l) => l.side === "BUY").length;
  const sells = legs.length - buys;
  if (buys > sells) return "BUY";
  if (sells > buys) return "SELL";
  return legs[0]?.side ?? "—";
}

interface GroupAgg {
  qty: number;
  tenor: string;
  dte: number;
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  vanna: number;
  volga: number;
  nominal: number;
  pnl: number;
}

function aggregate(legs: Position[]): GroupAgg {
  const sum = (f: (p: Position) => number): number => legs.reduce((s, p) => s + f(p), 0);
  const qtys = legs.map((l) => Math.abs(l.qty)).filter((q) => q > 0);
  const baseQty = qtys.length ? qtys.reduce((a, b) => _gcd(a, b)) : 0;
  const tenors = new Set(legs.map((l) => l.tenor).filter(Boolean));
  return {
    qty: baseQty,
    tenor: tenors.size === 1 ? [...tenors][0]! : "—",
    dte: legs[0]!.dte,
    delta: sum((p) => p.delta),
    gamma: sum((p) => p.gamma),
    vega: sum((p) => p.vega),
    theta: sum((p) => p.theta),
    vanna: sum((p) => p.vanna),
    volga: sum((p) => p.volga),
    nominal: sum((p) => p.nominal),
    pnl: sum((p) => p.pnl),
  };
}

// One leg row. `main=true` renders it as a standalone (single-leg trade) line;
// Strike for a leg : the live feed carries no strike column, but the IB
// localSymbol encodes it ("EUUQ6 C1145" → 1.1450). Falls back to p.strike (mock).
function legStrike(p: Position): string {
  if (p.strike && p.strike > 0) return p.strike.toFixed(4);
  const m = /\s[CP](\d{3,5})$/.exec(p.structure || "");
  if (m) return (parseInt(m[1]!, 10) / 1000).toFixed(4);
  return "—";
}

// The wing for a leg comes from the STRUCTURE's declared wings (parsed from its
// name, e.g. "Strangle 25Δ"), NOT from bucketing the live strike against the mock
// smile (spot 1.0842) — that snaps every live strike to the outermost pillar, so
// a 25Δ strangle would read 10Δ. One wing → all legs share it; two wings (a
// spread) → assign by strike rank (near→far), calls low-strike-first, puts high.
function legWingFor(p: Position, siblings: Position[], wings: string[]): string {
  if (wings.length <= 1) return wings[0] ?? "";
  const isCall = /call/i.test(p.product);
  const peers = siblings
    .filter((l) => /call/i.test(l.product) === isCall)
    .map((l) => ({ id: l.id, k: _legStrikeNum(l) }))
    .filter((x): x is { id: string; k: number } => x.k != null)
    .sort((a, b) => (isCall ? a.k - b.k : b.k - a.k));
  const idx = peers.findIndex((x) => x.id === p.id);
  return wings[Math.min(idx < 0 ? 0 : idx, wings.length - 1)]!;
}

// Leg product with its wing appended ("Vanilla Put" → "Vanilla Put 25Δ") so each
// leg row says which wing it is, consistent with the structure's main-row name.
// Skips if the product already carries a wing tag.
function legProductLabel(p: Position, wing: string): string {
  const prod = p.product || "—";
  return wing && !/\d+Δ|ATM/.test(prod) ? `${prod} ${wing}` : prod;
}

// Fill date in English format ("02 Jul 2026, 14:30"). Falls back to the raw value
// for the mock's pre-formatted strings, "" when absent.
function fmtFillDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })
    + ", " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
}

// Stable empty default for the `closing` prop (avoids a new Set each render).
const EMPTY_CLOSING: Set<string> = new Set();

// `main=false` renders it indented under its trade's summary line.
function legRow(
  p: Position,
  opts: { showGreeks: boolean; extended: boolean; onClose: ((p: Position) => void) | undefined; main: boolean; closing: Set<string>; legWing?: string },
): JSX.Element {
  const { showGreeks, extended, onClose, main, closing, legWing = "" } = opts;
  // Locked while this leg (or its whole trade) has a close in flight.
  const isClosing = closing.has(p.id) || (p.tradeId != null && closing.has("t:" + p.tradeId));
  return (
    <tr key={p.id} className={main ? "pkg-start" : "pos-leg"}>
      <td className="l mono dim">{main ? (p.tradeId ? "#" + p.tradeId : "—") : ""}</td>
      <td className="l mono dim">{p.structure || "—"}</td>
      <td className="l">
        <span className="sym">{main ? "" : "↳ "}{legProductLabel(p, legWing)}</span>
        {fmtFillDate(p.opened) && (
          <span className="substruct">filled {fmtFillDate(p.opened)}</span>
        )}
      </td>
      <td className="r mono dim">{legStrike(p)}</td>
      <td>
        <span className={"side-pill " + (p.side === "BUY" ? "long" : "short")}>{p.side}</span>
      </td>
      <td className="r mono">{p.qty}</td>
      <td className="r mono dim">{p.tenor}</td>
      <td className="r mono dim">{p.iv ? p.dte + "d" : "—"}</td>
      <td className="r mono">{p.entry ? p.entry.toFixed(p.entry > 1.5 ? 4 : 5) : "—"}</td>
      <td className="r mono">{p.mark ? p.mark.toFixed(p.mark > 1.5 ? 4 : 5) : "—"}</td>
      <td className="r mono dim">{p.iv ? p.iv.toFixed(1) : "—"}</td>
      {showGreeks && (
        <>
          <td className={"r mono " + pnlCls(p.delta)}>{gkc(p.delta)}</td>
          <td className="r mono dim">{p.iv ? gkc(p.gamma) : "—"}</td>
          <td className="r mono dim">{p.iv ? gkc(p.vega) : "—"}</td>
          <td className="r mono dim">{p.iv ? gkc(p.theta) : "—"}</td>
        </>
      )}
      {showGreeks && extended && (
        <>
          <td className="r mono dim">{p.iv ? fmt.sgn(p.vanna, 0) + "k" : "—"}</td>
          <td className="r mono dim">{p.iv ? fmt.sgn(p.volga, 0) + "k" : "—"}</td>
        </>
      )}
      <td className="r mono dim">{(p.nominal / 1e6).toFixed(2)}M</td>
      <td className={"r mono " + pnlCls(p.pnl)}>{fmt.usdk(p.pnl)}</td>
      <td className="r">
        <button
          className="row-close"
          disabled={isClosing}
          title={isClosing ? "a close for this position is already in flight" : undefined}
          onClick={() => onClose && onClose(p)}
        >
          {isClosing ? "Closing…" : "Close"}
        </button>
      </td>
    </tr>
  );
}

// Booking context per trade (from /positions/structured) : the real structure
// name + fill status, so a partially-filled multi-leg trade reads as e.g.
// "Risk Reversal · 1/2 filled ⚠ naked" instead of a bare filled leg.
export interface StructureCtx {
  name: string;
  filled: number;
  total: number;
  naked: boolean;
}

interface OpenPositionsTableProps {
  showGreeks?: boolean;
  extended?: boolean;
  onClose?: (p: Position) => void;
  /** Close a whole multi-leg trade at once (main summary line). */
  onCloseTrade?: (legs: Position[]) => void;
  /** trade_id → booking context (name + fill status), keyed as a string. */
  structureContext?: Record<string, StructureCtx>;
  /** Keys with a close in flight → lock the matching Close button. Key = a
   *  position id, or "t:<tradeId>" for a whole-trade close. */
  closing?: Set<string>;
  dense?: boolean;
  /** Live positions + book greeks (PR 6r). Default to the mock when omitted. */
  positions?: Position[];
  greeks?: Greeks;
  /** Render the "Book net" aggregate strip. Trade moves it into the Indicators
   * panel (showNet=false) so "open positions" vs "book state" read distinctly. */
  showNet?: boolean;
}

export function OpenPositionsTable({
  showGreeks = true,
  extended = false,
  onClose,
  onCloseTrade,
  structureContext,
  closing = EMPTY_CLOSING,
  dense = false,
  positions = DATA.positions,
  greeks = DATA.greeks,
  showNet = true,
}: OpenPositionsTableProps): JSX.Element {
  const rows = positions;
  const g = greeks;
  const total = g.netUnreal,
    tNom = g.netNominal;
  // Which multi-leg trades are EXPANDED (legs shown). Default = empty → every
  // trade lands collapsed (caret ▸, legs hidden) ; the operator opens the ones
  // they care about.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (key: string): void =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  return (
    <div className="positions-wrap">
      {showNet && (
      <div className="net-strip">
        <div className="net-id">
          <span className="dim small">Book net</span>
          <span className="net-id-val mono">{rows.length} legs</span>
          <span className="dim small mono">one engine · = Risk</span>
        </div>
        <div className="net-tiles">
          <div className="metric">
            <span className="metric-label">
              Δ net <em className="unit">$</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netDelta)}>{gkc(g.netDelta)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Γ net <em className="unit">$/pip</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netGamma)}>{gkc(g.netGamma)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Vega net <em className="unit">$/vp</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netVega)}>{gkc(g.netVega)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Vanna net <em className="unit">$k/vp·fig</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netVanna)}>{fmt.sgn(g.netVanna, 0)}k</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Volga net <em className="unit">$k/vp</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netVolga)}>{fmt.sgn(g.netVolga, 0)}k</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Θ net <em className="unit">$/day</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netTheta)}>{gkc(g.netTheta)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Nominal <em className="unit">€</em>
            </span>
            <span className="metric-value mono">{(tNom / 1e6).toFixed(1)}M</span>
          </div>
          <div className="metric">
            <span className="metric-label">Unrealized P&L</span>
            <span className={"metric-value mono " + pnlCls(total)}>{fmt.usdk(total)}</span>
          </div>
        </div>
      </div>
      )}
      <div className="table-scroll">
        <table className={"dt positions-table" + (dense ? " dense" : "")}>
          <thead>
            <tr>
              <th className="l">Trade</th>
              <th className="l">Contract</th>
              <th className="l">Product</th>
              <th className="r">Strike</th>
              <th>Side</th>
              <th className="r">Contracts</th>
              <th className="r">Tenor</th>
              <th className="r">DTE</th>
              <th className="r">Entry</th>
              <th className="r">Mark</th>
              <th className="r">IV</th>
              {showGreeks && (
                <>
                  <th className="r" title="USD">
                    Δ
                  </th>
                  <th className="r" title="USD/pip">
                    Γ
                  </th>
                  <th className="r" title="USD/vol pt">
                    Vega
                  </th>
                  <th className="r" title="USD/day">
                    Θ
                  </th>
                </>
              )}
              {showGreeks && extended && (
                <>
                  <th className="r" title="$k per 1vp·1 big-fig">
                    Vanna
                  </th>
                  <th className="r" title="$k/vp">
                    Volga
                  </th>
                </>
              )}
              <th className="r">Nominal €</th>
              <th className="r">P&L</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {groupByTrade(rows).map((grp) => {
              const ctx = grp.tradeId ? structureContext?.[grp.tradeId] : undefined;
              // A trade is "multi" if it has >1 filled leg OR the booking says it's
              // a multi-leg structure (so a partially-filled RR still reads as an RR,
              // not the one leg that happened to fill).
              const isMulti = grp.legs.length > 1 || (ctx != null && ctx.total > 1);
              if (!isMulti) {
                return legRow(grp.legs[0]!, { showGreeks, extended, onClose, main: true, closing });
              }
              const tradeClosing = grp.tradeId != null && closing.has("t:" + grp.tradeId);
              const name = ctx?.name ?? structureName(grp.legs);
              const wings = name.match(/\d+Δ|ATM/g) ?? []; // structure's declared wings
              const a = aggregate(grp.legs);
              const sSide = structureSide(grp.legs);
              const isOpen = expanded.has(grp.key);
              const legsLabel = ctx ? `${ctx.filled}/${ctx.total} legs` : `${grp.legs.length} legs`;
              return (
                <Fragment key={grp.key}>
                  <tr className={"pkg-start pos-main" + (isOpen ? " open" : "")} onClick={() => toggle(grp.key)}>
                    <td className="l mono dim">
                      <button
                        className="pos-caret"
                        onClick={(e) => { e.stopPropagation(); toggle(grp.key); }}
                        aria-label={isOpen ? "collapse legs" : "expand legs"}
                        aria-expanded={isOpen}
                      >
                        {isOpen ? "▾" : "▸"}
                      </button>
                      {grp.tradeId ? "#" + grp.tradeId : "—"}
                    </td>
                    <td className="l mono dim">{legsLabel}</td>
                    <td className="l">
                      <span className="sym">{name}</span>
                      {ctx?.naked && (
                        <span className="pos-naked" title="a sold leg filled but its long hedge leg hasn't — unbounded tail until it fills or is cancelled"> ⚠ naked</span>
                      )}
                    </td>
                    <td className="r mono dim">—</td>
                    <td><span className={"side-pill " + (sSide === "BUY" ? "long" : "short")}>{sSide}</span></td>
                    <td className="r mono">{a.qty || "—"}</td>
                    <td className="r mono dim">{a.tenor}</td>
                    <td className="r mono dim">{a.dte ? a.dte + "d" : "—"}</td>
                    <td className="r mono dim">—</td>
                    <td className="r mono dim">—</td>
                    <td className="r mono dim">—</td>
                    {showGreeks && (
                      <>
                        <td className={"r mono " + pnlCls(a.delta)}>{gkc(a.delta)}</td>
                        <td className={"r mono " + pnlCls(a.gamma)}>{gkc(a.gamma)}</td>
                        <td className={"r mono " + pnlCls(a.vega)}>{gkc(a.vega)}</td>
                        <td className={"r mono " + pnlCls(a.theta)}>{gkc(a.theta)}</td>
                      </>
                    )}
                    {showGreeks && extended && (
                      <>
                        <td className="r mono dim">{fmt.sgn(a.vanna, 0) + "k"}</td>
                        <td className="r mono dim">{fmt.sgn(a.volga, 0) + "k"}</td>
                      </>
                    )}
                    <td className="r mono dim">{(a.nominal / 1e6).toFixed(2)}M</td>
                    <td className={"r mono " + pnlCls(a.pnl)}>{fmt.usdk(a.pnl)}</td>
                    <td className="r">
                      <button
                        className="row-close"
                        disabled={tradeClosing}
                        title={tradeClosing ? "a close for this trade is already in flight" : `close all ${grp.legs.length} legs of this trade`}
                        onClick={(e) => { e.stopPropagation(); onCloseTrade && onCloseTrade(grp.legs); }}
                      >
                        {tradeClosing ? "Closing…" : "Close all"}
                      </button>
                    </td>
                  </tr>
                  {isOpen && grp.legs.map((p) => legRow(p, { showGreeks, extended, onClose, main: false, closing, legWing: legWingFor(p, grp.legs, wings) }))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function CashHoldings({ compact = false }: { compact?: boolean }): JSX.Element {
  const total = DATA.cash.reduce((s, c) => s + c.usd, 0);
  return (
    <div className="table-scroll">
      <table className="dt cash">
        <thead>
          <tr>
            <th className="l">Ccy</th>
            <th className="r">Settled</th>
            {!compact && <th className="r">Unsettled</th>}
            <th className="r">Rate</th>
            <th className="r">USD value</th>
          </tr>
        </thead>
        <tbody>
          {DATA.cash.map((c, i) => (
            <tr key={i}>
              <td className="l">
                <span className="ccy-dot" />
                {c.ccy}
              </td>
              <td className={"r mono " + pnlCls(c.settled)}>{fmt.num(c.settled, 0)}</td>
              {!compact && (
                <td className={"r mono " + (c.unsettled ? pnlCls(c.unsettled) : "dim")}>
                  {c.unsettled ? fmt.num(c.unsettled, 0) : "—"}
                </td>
              )}
              <td className="r mono dim">{c.rate.toFixed(4)}</td>
              <td className={"r mono " + pnlCls(c.usd)}>{fmt.usd(c.usd)}</td>
            </tr>
          ))}
          <tr className="total-row">
            <td className="l">Net cash (USD)</td>
            <td className="r mono" colSpan={compact ? 2 : 3}></td>
            <td className="r mono">{fmt.usd(total)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
