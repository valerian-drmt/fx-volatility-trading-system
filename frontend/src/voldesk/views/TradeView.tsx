/**
 * VOLDESK — Trade page. Ported from the prototype's `js/views_trade.jsx`.
 * Inline sub-components (IndicatorsPanel, HedgeStrip, HoldingsStrip, ClosePanel,
 * BudgetBar) stay local. The prototype's MarketDataBlock was exported but never
 * rendered by TradeView — dropped. Order entry is the WRITE path: it stays mock
 * until the auth boundary + backend wiring lands (IMPLEMENTATION.md §3bis/§5).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Panel } from "../components/common";
import { gk$, pnlCls } from "../components/format";
import { FreshBadge } from "../components/FreshBadge";
import { OpenPositionsTable, type StructureCtx } from "../components/PositionsTable";
import { OrderBuilder } from "../components/OrderBuilder";
import { TickerChart } from "../components/TickerChart";
import { DATA, fmt } from "../data";
import type { AccountState, Cash, Greeks, Position } from "../data";
import { useDeskData, useTicks } from "../data/deskData";
import { WRITE_ENABLED } from "../data/writeEnabled";
import { useAuthStore } from "../../store/authStore";
import { ApiError } from "../../api/client";
import { closeContract, closeTrade, fetchGreekLimits, fetchStructuredPositions, fetchSubmitted, type StructuredPositions, type SubmittedTrade } from "../../api/endpoints";
import { adaptGreekLimits, type GreekLimits } from "../data/live/portfolio";
import { useFetch } from "../../hooks/useFetch";

const GATE_TITLE = "write disabled — auth required (Phase 2)";

function closeErr(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = (e.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    return `broker/API error ${e.status}`;
  }
  return e instanceof Error ? e.message : "unknown error";
}

interface TradeTweaks {
  density: string;
  showGreeks: boolean;
}

// One row of the session order blotter — an order the trader sent (open or close).
export interface OrderRecord {
  action: "open" | "close";
  label: string;
  side: string;
  qty: number;
  state: "sent" | "rejected";
  note?: string;
}
interface BlotterRow {
  id: string;
  tradeNo?: number; // the assigned trade_structure id (DB rows only; session rejects have none)
  contract?: string; // IB localSymbol(s) of the legs ("EUUV6 C1130" / "… +N")
  ts: string;
  tsSort: number;
  action: "open" | "close";
  role: string; // desk order role : entry / closing / unwind / hedge
  label: string;
  qty: number;
  state: string; // "sent"/"rejected" (session) or the DB state ("active", "closed", …)
  note?: string;
}
// Order role → the desk's professional label + open/close tone for the "Type" cell.
const ROLE_LABEL: Record<string, string> = { entry: "Entry", closing: "Closing", unwind: "Unwind", hedge: "Hedge" };
function roleLabel(role: string): string { return ROLE_LABEL[role] ?? "Entry"; }
function roleTone(role: string): "open" | "close" { return role === "closing" || role === "unwind" ? "close" : "open"; }
// map any order/position state → a distinct badge tone (one colour per lifecycle stage)
// Three buckets, by outcome — matches the operator's mental model:
//   red    = rejected / failed / expired (never became a live position)
//   green  = accepted (a fill, or an open live position / settled order)
//   orange = in progress (submitted / pending / acknowledged / partially filled)
// `partial_fail` hits the red rule first (contains "fail"); `partial_fill` is
// still working, so it must be caught by the orange rule before the green one.
function stateTone(s: string): string {
  const t = s.toLowerCase();
  if (/reject|cancel|fail|error|block|expire/.test(t)) return "rejected";        // red
  if (/partial|submit|pending|presubmit|sent|acknowledg|working/.test(t)) return "pending"; // orange
  if (/fill|open|active|live|done|settled|closed/.test(t)) return "filled";      // green
  return "pending";
}
// A working order that hasn't filled or been rejected after this long is "stale"
// — the desk should review/cancel it. Matches the backend stuck_order_watcher
// (STUCK_AFTER_S = 600 s). We flag it in the UI; we NEVER auto-cancel (a resting
// limit can be deliberate — that call stays with the operator).
const STALE_MS = 10 * 60 * 1000;
function fmtAge(ms: number): string {
  const m = Math.floor(ms / 60000);
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60);
  return h + "h" + (m % 60 ? " " + (m % 60) + "m" : "");
}
// clean, consistent product name. Known structure_types map to a proper label;
// for "custom"/unknown types we fall back to the descriptive product_label so a
// freeform trade shows its real name instead of "Custom".
const PRODUCT_NAMES: Record<string, string> = {
  vanilla_call: "Vanilla Call", vanilla_put: "Vanilla Put",
  straddle_atm: "Straddle", straddle: "Straddle", strangle: "Strangle",
  butterfly: "Butterfly", risk_reversal: "Risk Reversal", calendar: "Calendar", future: "Future",
  "call spread": "Call Spread", "put spread": "Put Spread",
};
// Format a classifier label (the stored structure_type / product_label) into a
// clean product name. The backend stores classify_legs' verdict with a
// long/short prefix + Δ bucket — e.g. "long strangle 25d", "long straddle",
// "long future", "long call". This maps those to the dropdown's product names.
// Returns null only for empty / "custom" so callers can fall through.
function formatStructLabel(label: string | null | undefined): string | null {
  if (!label) return null;
  const l = label.toLowerCase().trim();
  if (l === "custom" || l === "") return null;
  const sm = /strangle\s*(\d+)\s*d/.exec(l);
  if (sm) return `Strangle ${sm[1]}Δ`;
  if (l.includes("strangle")) return "Strangle";
  if (l.includes("straddle")) return "Straddle";
  if (l.includes("risk reversal")) return "Risk Reversal";
  if (l.includes("butterfly")) return "Butterfly";
  if (l.includes("calendar")) return "Calendar";
  if (l.includes("call spread")) return "Call Spread";
  if (l.includes("put spread")) return "Put Spread";
  if (l.includes("vertical spread")) return "Vertical Spread";
  if (l.includes("future")) return "Future";
  const bare = l.replace(/^(long|short)\s+/, "");  // vanilla single-leg
  if (bare === "call") return "Vanilla Call";
  if (bare === "put") return "Vanilla Put";
  return label.replace(/_/g, " ").trim().replace(/\b\w/g, (c) => c.toUpperCase());
}

function prettyProduct(s: SubmittedTrade): string {
  const st = (s.structure_type ?? "").toLowerCase();
  if (PRODUCT_NAMES[st]) return PRODUCT_NAMES[st];
  // product_label is often empty for free-leg builds → format structure_type
  // (the classifier verdict) as the fallback, not "" (which would read Structure).
  return formatStructLabel(s.product_label) ?? formatStructLabel(s.structure_type) ?? "Structure";
}

// Single source of truth for Open positions : turn the server-joined
// /positions/structured payload into the panel's rows + per-structure context.
// Identity + terms come from the DB structure (trade_structure), the live values
// from the IB mirror it already joined — NO client-side inference of what a
// structure "is" (that would let the broker feed define our own booking). Only
// legs IB actually holds (`linked`) become rows ; unlinked IB holdings still
// show as their own singletons so nothing the broker reports disappears.
function structuredToRows(
  data: StructuredPositions | null,
): { positions: Position[]; ctx: Record<string, StructureCtx> } {
  const positions: Position[] = [];
  const ctx: Record<string, StructureCtx> = {};
  if (!data) return { positions, ctx };
  const now = Date.now();
  const dteOf = (e: string): number => {
    const t = Date.parse(e);
    return Number.isNaN(t) ? 0 : Math.max(0, Math.round((t - now) / 86_400_000));
  };
  const legProduct = (ct: string): string =>
    ct === "call" ? "Vanilla Call" : ct === "put" ? "Vanilla Put" : ct === "future" ? "Future" : ct;
  for (const s of data.structures) {
    const tid = String(s.structure_id);
    const filled = s.legs.filter((l) => l.linked).length;
    const naked =
      s.legs.some((l) => l.side === "SELL" && l.linked) &&
      s.legs.some((l) => l.side === "BUY" && !l.linked);
    ctx[tid] = {
      // Identity from the DB structure : the canonical type, else the stored
      // product_label, else the classifier's structure_type verdict formatted
      // (e.g. "long strangle 25d" → "Strangle 25Δ"). Never leg-inference.
      name: PRODUCT_NAMES[s.structure_type]
        ?? formatStructLabel(s.product_label)
        ?? formatStructLabel(s.structure_type)
        ?? "Structure",
      filled, total: s.legs.length, naked,
    };
    for (const l of s.legs) {
      if (!l.linked || l.position_id == null) continue; // render only what IB holds
      const nominal = l.nominal_eur ?? 0, pnl = l.pnl_usd ?? 0;
      positions.push({
        id: String(l.position_id), packageId: "", tradeId: tid, conId: l.con_id ?? 0,
        product: legProduct(l.contract_type), structure: l.ib_local_symbol ?? "—",
        side: l.held_side ?? l.side, qty: l.held_qty ?? l.qty,
        tenor: l.tenor ?? s.tenor ?? "", expiry: l.expiry ?? "", strike: l.strike ?? 0,
        entry: l.entry ?? 0, mark: l.mark ?? 0, iv: (l.iv ?? 0) * 100, pnl, nominal,
        delta: l.delta_usd ?? 0, gamma: l.gamma_usd ?? 0, vega: l.vega_usd ?? 0, theta: l.theta_usd ?? 0,
        vanna: (l.vanna_usd ?? 0) / 1000, volga: (l.volga_usd ?? 0) / 1000,
        updated: l.updated ?? "", opened: l.opened ?? "",
        pnlPct: nominal ? (pnl / nominal) * 100 : 0, dte: dteOf(l.expiry ?? ""),
      });
    }
  }
  for (const u of data.unlinked) {
    const pnl = u.pnl_usd ?? 0;
    positions.push({
      id: String(u.id), packageId: "", tradeId: "", conId: 0,
      product: u.product_label ?? "—", structure: u.symbol ?? "—",
      side: u.side ?? "BUY", qty: u.qty ?? 0, tenor: u.tenor ?? "", expiry: u.expiry ?? "",
      strike: 0, entry: 0, mark: u.mark ?? 0, iv: (u.iv ?? 0) * 100, pnl, nominal: 0,
      delta: u.delta_usd ?? 0, gamma: u.gamma_usd ?? 0, vega: u.vega_usd ?? 0, theta: u.theta_usd ?? 0,
      vanna: (u.vanna_usd ?? 0) / 1000, volga: (u.volga_usd ?? 0) / 1000,
      updated: "", opened: "", pnlPct: 0, dte: dteOf(u.expiry ?? ""),
    });
  }
  return { positions, ctx };
}

// ---------------- ClosePanel ----------------
// A close request : one leg (contract) or a whole multi-leg trade (all legs).
type CloseReq =
  | { kind: "contract"; pos: Position }
  | { kind: "trade"; legs: Position[] };

function ClosePanel({
  req,
  onDone,
  onOrder,
  onClosing,
  positions,
  greeks,
  ctx,
}: {
  req: CloseReq | null;
  onDone: () => void;
  onOrder: (rec: OrderRecord) => void;
  /** Signal that a close was accepted for this key so the panel/table can lock
   *  its Close button until the position clears. Key = position id, or
   *  "t:<tradeId>" for a whole-trade close. */
  onClosing: (key: string) => void;
  positions: Position[];
  greeks: Greeks;
  ctx: Record<string, StructureCtx>;
}): JSX.Element {
  const [type, setType] = useState<"contract" | "trade">("contract");
  const [contractId, setContractId] = useState("");
  const [tradeId, setTradeId] = useState("");
  const [qty, setQty] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null); // "Position closed" confirmation
  // Write gate = real login state (auth cookie) OR the local-dev build bypass.
  const canWrite = useAuthStore((s) => s.authenticated) || WRITE_ENABLED;
  useEffect(() => {
    if (!req) return;
    // A fresh close request (Close / Close all clicked in Open positions) always
    // returns to the form, clearing any leftover confirmation / error.
    setDone(null);
    setErr(null);
    if (req.kind === "contract") {
      setType("contract");
      setContractId(req.pos.id);
      setQty(req.pos.qty);
    } else {
      // whole trade → "all legs" mode, keyed by the group's real trade_id
      setType("trade");
      setTradeId(req.legs[0]?.tradeId ?? "");
    }
  }, [req]);

  // Trades are keyed by the real trade_id (what Open positions + Orders show as
  // "#135"), so a "Close all" selection matches the dropdown and the backend.
  const trades = [...new Set(positions.map((p) => p.tradeId).filter(Boolean))].map((id) => ({
    id,
    // the trade's PRODUCT name ("Risk Reversal 25Δ"), not a leg's contract symbol
    struct: ctx[id]?.name ?? positions.find((p) => p.tradeId === id)?.structure ?? "",
  }));
  const g = greeks;
  let sel: Position | { trade: true } | null = null;
  const c = { pnl: 0, d: 0, g: 0, v: 0, vn: 0, vg: 0, t: 0 };
  if (type === "contract" && contractId) {
    const p = positions.find((x) => x.id === contractId);
    if (p) {
      const f = Math.min(1, (qty || 0) / p.qty);
      sel = p;
      c.pnl = p.pnl * f; c.d = p.delta * f; c.g = p.gamma * f; c.v = p.vega * f;
      c.vn = p.vanna * f; c.vg = p.volga * f; c.t = p.theta * f;
    }
  } else if (type === "trade" && tradeId) {
    const legs = positions.filter((x) => x.tradeId === tradeId);
    if (legs.length) {
      sel = { trade: true };
      legs.forEach((p) => {
        c.pnl += p.pnl; c.d += p.delta; c.g += p.gamma; c.v += p.vega;
        c.vn += p.vanna; c.vg += p.volga; c.t += p.theta;
      });
    }
  }
  // Same table as the Order builder. Closing removes the position's greeks from the
  // book → the "Value" (book impact) is −c, and Book after = before − c.
  const kfmt = (x: number): string => fmt.sgn(x, 1) + "k";
  const closeContracts = type === "contract" ? (qty || 0) : positions.filter((x) => x.tradeId === tradeId).reduce((s, p) => s + p.qty, 0);
  const closeComm = sel ? Math.round(closeContracts * 2.1) : 0;
  const closeNetCash = c.pnl - closeComm;
  const impactRows = [
    { name: "Δ", unit: "USD", val: -c.d, before: g.netDelta, after: g.netDelta - c.d, f: gk$ },
    { name: "Γ", unit: "USD/pip", val: -c.g, before: g.netGamma, after: g.netGamma - c.g, f: gk$ },
    { name: "Vega", unit: "$/vp", val: -c.v, before: g.netVega, after: g.netVega - c.v, f: gk$ },
    { name: "Θ", unit: "$/day", val: -c.t, before: g.netTheta, after: g.netTheta - c.t, f: gk$ },
    { name: "Vanna", unit: "$k/vp·fig", val: -c.vn, before: g.netVanna, after: g.netVanna - c.vn, f: kfmt },
    { name: "Volga", unit: "$k/vp", val: -c.vg, before: g.netVolga, after: g.netVolga - c.vg, f: kfmt },
  ];

  // Real close → IB paper account. Contract = one leg (OpenPosition.id, partial
  // via qty); trade = every leg sharing the trade_id. Parent refreshes on poll.
  const onExec = async (): Promise<void> => {
    if (!sel || !canWrite || busy) return;
    setBusy(true);
    setErr(null);
    const label = type === "contract"
      ? (positions.find((x) => x.id === contractId)?.structure ?? `contract #${contractId}`)
      : `trade #${tradeId}`;
    const oqty = type === "contract" ? qty : positions.filter((x) => x.tradeId === tradeId).length;
    try {
      if (type === "contract") {
        await closeContract(Number(contractId), qty);
        onClosing(String(contractId));
      } else {
        if (!tradeId || Number.isNaN(Number(tradeId))) throw new Error("no backend trade id for this trade");
        await closeTrade(Number(tradeId));
        onClosing("t:" + String(tradeId));
      }
      onOrder({ action: "close", label, side: "—", qty: oqty, state: "sent" });
      setDone(`${type === "contract" ? qty + " ct" : "all legs"} · ${label} · realized ${gk$(c.pnl)} · IB paper account`);
      setContractId(""); setTradeId(""); setQty(0); // reset the form for the next close
      onDone();
    } catch (e) {
      const m = closeErr(e);
      setErr(m);
      onOrder({ action: "close", label, side: "—", qty: oqty, state: "rejected", note: m });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="close-draft">
      <div className="close-fields">
        <label className="field">
          <span>Type</span>
          <select value={type} onChange={(e) => setType(e.target.value as "contract" | "trade")}>
            <option value="contract">Contract (1 leg)</option>
            <option value="trade">Trade (all legs)</option>
          </select>
        </label>
        <label className="field">
          <span>Contract number</span>
          <select
            value={contractId}
            disabled={type === "trade"}
            onChange={(e) => {
              setContractId(e.target.value);
              const p = positions.find((x) => x.id === e.target.value);
              if (p) setQty(p.qty);
            }}
          >
            <option value="">— pick a contract —</option>
            {positions.map((p) => (
              <option key={p.id} value={p.id}>
                {p.conId} · {p.structure}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Trade number</span>
          <select value={tradeId} disabled={type === "contract"} onChange={(e) => setTradeId(e.target.value)}>
            <option value="">— pick a trade —</option>
            {trades.map((p) => (
              <option key={p.id} value={p.id}>
                #{p.id} · {p.struct}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>
            Qty to close <em className="unit">contracts</em>
          </span>
          <div className="field-input">
            <input type="number" value={qty} disabled={type === "trade"} onChange={(e) => setQty(+e.target.value)} />
            <em>ct</em>
          </div>
        </label>
      </div>
      <table className="dt bi-table impact-table">
        <thead><tr><th className="l">Item</th><th className="r">Value</th><th className="r">Book before</th><th className="r after-col">Book after</th></tr></thead>
        <tbody>
          {impactRows.map((r) => (
            <tr key={r.name}>
              <td className="l">{r.name} <em className="unit mono">{r.unit}</em></td>
              <td className={"r mono " + (sel ? pnlCls(r.val) : "dim")}>{sel ? r.f(r.val) : "—"}</td>
              <td className={"r mono " + pnlCls(r.before)}>{r.f(r.before)}</td>
              <td className={"r mono after-col " + (sel ? pnlCls(r.after) : "dim")}>{sel ? r.f(r.after) : "—"}</td>
            </tr>
          ))}
          <tr className="impact-sep">
            <td className="l">Realized P&amp;L</td>
            <td className={"r mono " + (sel ? pnlCls(c.pnl) : "dim")}>{sel ? gk$(c.pnl) : "—"}</td>
            <td className="r mono dim">—</td>
            <td className="r mono dim after-col">—</td>
          </tr>
          <tr>
            <td className="l">Commission</td>
            <td className={"r mono " + (sel ? "neg" : "dim")}>{sel ? "−" + fmt.usd(closeComm) : "—"}</td>
            <td className="r mono dim">—</td>
            <td className="r mono dim after-col">—</td>
          </tr>
          <tr className="impact-total">
            <td className="l">Net cash</td>
            <td className={"r mono " + (sel ? (closeNetCash >= 0 ? "pos" : "neg") : "dim")}>{sel ? gk$(closeNetCash) : "—"}</td>
            <td className="r mono dim">—</td>
            <td className="r mono dim after-col">—</td>
          </tr>
        </tbody>
      </table>
      {err && <div className="ob-error mono small">⚠ {err}</div>}
      {done ? (
        // Close accepted → the confirmation takes the Close button's place ; the
        // form stays visible, but the operator must Dismiss before closing again
        // (no accidental re-close on a stale selection).
        <div className="book-result close-result">
          <div>
            <b className="close-result-title">Close submitted ✓</b>
            <span className="mono">{done}</span>
          </div>
          <button className="btn-ghost" onClick={() => setDone(null)}>Dismiss</button>
        </div>
      ) : (
        <button
          className="btn-close-exec"
          disabled={!sel || !canWrite || busy}
          title={canWrite ? "submit close to IB paper account" : GATE_TITLE}
          onClick={onExec}
        >
          {busy ? "Closing…" : sel ? (type === "trade" ? "Close trade" : `Close ${qty} ct`) : "Close"}
        </button>
      )}
      {!canWrite && <div className="dim small ob-readonly-note">Read-only desk · log in to close positions.</div>}
    </div>
  );
}

// ---------------- HoldingsStrip ----------------
// Two lines, one per currency: "<CCY> <native amount> (<share of book>%)".
function HoldingsStrip({ cash }: { cash: Cash[] }): JSX.Element {
  const eur = cash.find((c) => c.ccy === "EUR");
  const usd = cash.find((c) => c.ccy === "USD");
  const total = (eur ? eur.usd : 0) + (usd ? usd.usd : 0) || 1;
  const rows = [
    { ccy: "EUR", amount: eur ? eur.settled + eur.unsettled : 0, share: eur ? (eur.usd / total) * 100 : 0 },
    { ccy: "USD", amount: usd ? usd.settled + usd.unsettled : 0, share: usd ? (usd.usd / total) * 100 : 0 },
  ];
  return (
    <div className="hold-lines">
      {rows.map((r) => (
        <div className="hold-line" key={r.ccy}>
          <span className="hl-dash dim">—</span>
          <span className="hl-ccy">{r.ccy}</span>
          <b className="mono">{fmt.num(r.amount, 0)}</b>
          <span className="dim mono hl-share">({r.share.toFixed(0)}%)</span>
        </div>
      ))}
    </div>
  );
}

// ---------------- Indicators ----------------
function IndicatorsPanel({
  greeks,
  account,
  cash,
  spotBid,
  spotAsk,
}: {
  greeks: Greeks;
  account: AccountState;
  cash: Cash[];
  spotBid: number;
  spotAsk: number;
}): JSX.Element {
  const g = greeks;
  // Risk utilization (same as the Risk tab): margins vs netLiq + greek exposures
  // vs the /portfolio/greek-limits caps. Live; caps read "—" until they resolve.
  const glim = useFetch<GreekLimits>(() => fetchGreekLimits().then(adaptGreekLimits), 60_000).data;
  const utilColor = (p: number): string => (p > 100 ? "var(--neg)" : p > 80 ? "var(--warn)" : "var(--pos)");
  const pctOf = (used: number, cap: number | undefined): number => (cap && cap > 0 ? (Math.abs(used) / cap) * 100 : 0);
  const capk = (c: number): string => (c > 0 ? fmt.usdk(c) : "—");
  const deltaCap = glim?.deltaCapUsd ?? 0, vegaCap = glim?.vegaCapUsd ?? 0, gammaCap = glim?.gammaCapPip ?? 0;
  const utilRows = [
    { label: "Init margin", used: fmt.usd(account.marginInit), limit: fmt.usd(account.netLiq), pct: account.marginInitPct },
    { label: "Maint margin", used: fmt.usd(account.marginMaint), limit: fmt.usd(account.netLiq), pct: account.marginMaintPct },
    { label: "Δ exposure", used: fmt.usdk(Math.abs(g.netDelta)), limit: capk(deltaCap), pct: pctOf(g.netDelta, deltaCap) },
    { label: "Vega", used: fmt.usdk(Math.abs(g.netVega)), limit: capk(vegaCap), pct: pctOf(g.netVega, vegaCap) },
    { label: "Γ exposure", used: fmt.usdk(Math.abs(g.netGamma)), limit: capk(gammaCap), pct: pctOf(g.netGamma, gammaCap) },
  ];

  return (
    <div className="ind-grid">
      {/* EUR/USD ticker with market-session overlay */}
      <div className="ind-fam">
        <div className="ind-fam-head">Ticker <span className="dim">· EUR/USD</span></div>
        <TickerChart spot={(spotBid + spotAsk) / 2} />
      </div>

      {/* cash holdings — below the ticker */}
      <div className="ind-fam">
        <div className="ind-fam-head">Cash holdings</div>
        <HoldingsStrip cash={cash} />
      </div>

      {/* portfolio greeks — same table as the Risk tab's "Portfolio greeks" */}
      <div className="ind-fam">
        <div className="ind-fam-head">Portfolio greeks</div>
        <table className="dt greeks-table">
          <thead><tr><th className="l">Greek</th><th className="r">Net value</th></tr></thead>
          <tbody>
            <tr><td className="l">Δ <em className="unit">USD</em></td><td className={"r mono " + pnlCls(g.netDelta)}>{gk$(g.netDelta)}</td></tr>
            <tr><td className="l">Γ <em className="unit">USD/pip</em></td><td className={"r mono " + pnlCls(g.netGamma)}>{gk$(g.netGamma)}</td></tr>
            <tr><td className="l">Vega <em className="unit">$/vp</em></td><td className={"r mono " + pnlCls(g.netVega)}>{gk$(g.netVega)}</td></tr>
            <tr><td className="l">Θ <em className="unit">$/day</em></td><td className={"r mono " + pnlCls(g.netTheta)}>{gk$(g.netTheta)}</td></tr>
            <tr><td className="l">Vanna <em className="unit">$k/vp·fig</em></td><td className={"r mono " + pnlCls(g.netVanna)}>{fmt.sgn(g.netVanna, 1)}k</td></tr>
            <tr><td className="l">Volga <em className="unit">$k/vp</em></td><td className={"r mono " + pnlCls(g.netVolga)}>{fmt.sgn(g.netVolga, 1)}k</td></tr>
          </tbody>
        </table>
      </div>

      {/* risk utilization — same table as the Risk tab, below Portfolio greeks */}
      <div className="ind-fam">
        <div className="ind-fam-head">Risk utilization</div>
        <table className="dt greeks-table">
          <thead><tr><th className="l">Limit</th><th className="r">Used / cap</th><th className="r">%</th></tr></thead>
          <tbody>
            {utilRows.map((r) => (
              <tr key={r.label}>
                <td className="l">{r.label}</td>
                <td className="r mono"><span style={{ color: utilColor(r.pct) }}>{r.used}</span> <span className="dim">/ {r.limit}</span></td>
                <td className="r mono" style={{ color: utilColor(r.pct) }}>{r.pct.toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function TradeView({ tweaks }: { tweaks: TradeTweaks }): JSX.Element {
  const [closeReq, setCloseReq] = useState<CloseReq | null>(null);
  // Keys with a close in flight (accepted by IB but the position hasn't cleared
  // yet). Key = position id, or "t:<tradeId>" for a whole-trade close → maps to
  // epoch ms fired. Locks the matching Close button so a slow fill can't be
  // re-clicked into a stack of duplicate orders (the backend also 409s, this is
  // the UX layer). Pruned when the position clears or after a TTL safety net.
  const [closingKeys, setClosingKeys] = useState<Record<string, number>>({});
  // On a close request (from Open positions), scroll the pre-filled Close panel
  // into view and flash it, so the operator just confirms with one click.
  useEffect(() => {
    if (!closeReq) return;
    const el = document.querySelector('[data-pp="trade-close"]');
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("panel-flash");
    const t = setTimeout(() => el.classList.remove("panel-flash"), 1300);
    return () => clearTimeout(t);
  }, [closeReq]);
  // Desk trade data (Open positions mirror + greeks/account) — polled by the
  // provider on TRADE_POLL_MS. Grabbed here (above addOrder) so a send can force
  // an immediate refetch of the positions panel.
  const { trade, reloadTrade } = useDeskData();
  // Order blotter: persisted submitted structures from the DB (survive refresh) +
  // ephemeral session rejects (failed sends aren't persisted server-side).
  const submitted = useFetch<SubmittedTrade[]>(() => fetchSubmitted(50), 120_000, true, 30_000);
  // Open positions = a SINGLE server-joined read (/positions/structured). It
  // carries the DB structure (identity + terms) already joined to the live IB
  // mirror, so the panel renders straight from it — no second fetch, no
  // client-side inference of what a structure is (the broker feed never defines
  // our own booking). `structuredToRows` yields both the leg rows and the
  // per-structure context (name / N-of-M / naked).
  const structured = useFetch<StructuredPositions>(() => fetchStructuredPositions(), 15_000, true, 30_000);
  const structRows = useMemo(() => structuredToRows(structured.data), [structured.data]);
  const structureCtx = structRows.ctx;
  const [rejects, setRejects] = useState<BlotterRow[]>([]);
  const seq = useRef(0);
  const addOrder = (rec: OrderRecord): void => {
    if (rec.state === "rejected") {
      const now = Date.now();
      setRejects((prev) => [{
        id: "r" + seq.current++, ts: new Date().toLocaleTimeString("en-GB", { hour12: false }), tsSort: now,
        action: rec.action, role: rec.action === "close" ? "closing" : "entry",
        label: rec.label, qty: rec.qty, state: "rejected", ...(rec.note ? { note: rec.note } : {}),
      }, ...prev].slice(0, 10));
    } else {
      // Success → refetch every source the Open positions panel reads, together,
      // so a new trade appears in one coherent step instead of staggering in as
      // each source's own timer fires (blotter now, then legs, then labels later).
      submitted.reload();     // DB order list (blotter)
      structured.reload();    // structure labels / naked flags
      reloadTrade();          // open_position mirror (leg rows)
    }
  };
  const dbRows: BlotterRow[] = (submitted.data ?? []).map((s) => ({
    id: "s" + s.id,
    // a close shows the trade it closes (#30), not this new closing structure (#31)
    tradeNo: s.closes_trade_id ?? s.id,
    ...(s.contract ? { contract: s.contract } : {}),
    ts: new Date(s.created_at).toLocaleTimeString("en-GB", { hour12: false }),
    tsSort: Date.parse(s.created_at) || 0,
    action: s.order_role === "closing" || s.order_role === "unwind" ? "close" : "open",
    role: s.order_role ?? "entry",
    label: `${prettyProduct(s)}${s.reference_tenor ? " " + s.reference_tenor : ""}`,
    qty: s.base_qty ?? 0,
    state: s.position_state ?? s.state ?? "—",
    ...(s.execution_mode === "mock" ? { note: "paper" } : {}),
  }));
  const orders = [...rejects, ...dbRows].sort((a, b) => b.tsSort - a.tsSort).slice(0, 60);
  const ticks = useTicks();
  const td = trade.data;
  // Open positions render from the structured read. Fall back to the raw IB
  // mirror only when there are no structures at all (and to the mock when there's
  // no live data), so a genuinely-flat live book shows empty, not mock rows.
  const positions = structRows.positions.length
    ? structRows.positions
    : (td?.positions ?? DATA.positions);
  const greeks = td?.greeks ?? DATA.greeks;
  // Drop a "closing" lock once its position has cleared from the panel (fill +
  // sync ~30 s) or after a 90 s TTL (safety net so a close that never fills
  // doesn't lock the button forever — the operator can retry / cancel). Runs on
  // each positions poll (~15 s), which also covers the TTL check.
  useEffect(() => {
    const now = Date.now();
    setClosingKeys((prev) => {
      const next: Record<string, number> = {};
      let changed = false;
      for (const [k, ts] of Object.entries(prev)) {
        const gone = k.startsWith("t:")
          ? !positions.some((p) => "t:" + p.tradeId === k)
          : !positions.some((p) => p.id === k);
        if (gone || now - ts > 90_000) { changed = true; continue; }
        next[k] = ts;
      }
      return changed ? next : prev;
    });
  }, [positions]);
  const closing = useMemo(() => new Set(Object.keys(closingKeys)), [closingKeys]);
  const account = td?.account ?? DATA.account;
  const cash = td?.cash ?? DATA.cash;
  // Live EURUSD bid/ask (RT.1) ; fallback to a synthetic spread around the mock spot.
  const spotBid = ticks.data?.bid ?? DATA.SPOT - 0.0001;
  const spotAsk = ticks.data?.ask ?? DATA.SPOT + 0.0001;

  return (
    <div className={"trade-grid " + (tweaks.density || "regular")}>
      <div className="trade-top">
        <Panel title="Indicators" dataPp="trade-indicators" right={<FreshBadge fresh={trade} label="" />} className="trade-block">
          <IndicatorsPanel greeks={greeks} account={account} cash={cash} spotBid={spotBid} spotAsk={spotAsk} />
        </Panel>
        <Panel title="Order" dataPp="trade-builder" className="trade-block">
          <OrderBuilder onOrder={addOrder} />
        </Panel>
        <Panel title="Close position" dataPp="trade-close" className="trade-block">
          <ClosePanel req={closeReq} onDone={() => setCloseReq(null)} onOrder={addOrder} onClosing={(k) => setClosingKeys((p) => ({ ...p, [k]: Date.now() }))} positions={positions} greeks={greeks} ctx={structureCtx} />
        </Panel>
      </div>
      <Panel title="Open positions" dataPp="trade-open" pad={false} className="trade-block open-pos-panel">
        <OpenPositionsTable
          showGreeks={tweaks.showGreeks}
          extended={tweaks.showGreeks}
          onClose={(p) => setCloseReq({ kind: "contract", pos: p })}
          onCloseTrade={(legs) => setCloseReq({ kind: "trade", legs })}
          structureContext={structureCtx}
          closing={closing}
          dense={tweaks.density === "compact"}
          positions={positions}
          greeks={greeks}
          showNet={false}
        />
      </Panel>
      <Panel title="Orders" dataPp="trade-orders" right={<FreshBadge fresh={submitted} label="from DB" />} className="trade-block" pad={false}>
        {orders.length === 0 ? (
          <div className="dim small mono orders-empty">No submitted orders — Place order to book one (persisted in the DB).</div>
        ) : (
          <div className="table-scroll orders-scroll">
            <table className="dt orders-table">
              <thead>
                <tr><th>Time</th><th>Trade</th><th>Contract</th><th>Product</th><th>Type</th><th>Contracts</th><th>State</th></tr>
              </thead>
              <tbody>
                {orders.map((o) => {
                  const tone = stateTone(o.state);
                  const ageMs = Date.now() - o.tsSort;
                  const stale = tone === "pending" && ageMs > STALE_MS;
                  return (
                    <tr key={o.id}>
                      <td className="mono dim">{o.ts}</td>
                      <td className="mono dim">{o.tradeNo != null ? "#" + o.tradeNo : "—"}</td>
                      <td className="mono dim">{o.contract ?? "—"}</td>
                      <td>
                        {o.label}
                        {o.note && <span className="dim small"> · {o.note}</span>}
                      </td>
                      <td><span className={"ord-dir " + roleTone(o.role)}>{roleLabel(o.role)}</span></td>
                      <td className="mono">{o.qty}</td>
                      <td>
                        <span className={"ord-state " + tone}>{o.state}</span>
                        {stale && (
                          <span className="ord-stale mono" title="working > 10 min with no fill — review or cancel">
                            ⏱ {fmtAge(ageMs)}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
