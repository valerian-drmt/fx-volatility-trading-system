/**
 * VOLDESK — multi-leg structure builder. Ported 1:1 from the prototype's
 * `js/order_builder.jsx` (global-window pattern) into a typed ES module.
 *
 * Inputs are product-driven (§6); the preview tells the RISK TRUTH of the
 * structure (§0 max-loss safety, §1 structure-relevant greeks, §2 pre-trade
 * book impact, §3 skew flag, §4 bundled hedge, §5 premium reconciliation).
 * Same JSX / classNames / logic as the prototype — only types + ES modules added.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { gk$, pnlCls } from "./format";
import { DATA, fmt } from "../data";
import { useDeskData, useTicks } from "../data/deskData";
import { ApiError } from "../../api/client";
import { createTradePreview, submitTrade, type TradePreview } from "../../api/endpoints";
import { WRITE_ENABLED } from "../data/writeEnabled";
import { useAuthStore } from "../../store/authStore";
import { builderToLegs } from "./orderLegs";

const GATE_TITLE = "write disabled — auth required (Phase 2)";
// Alphabetical order for the product dropdown; the first entry is the default.
// "Straddle/Strangle" is one product — a Δ selector (10Δ/25Δ/50Δ) picks between a
// strangle (10Δ/25Δ) and a straddle (50Δ = ATM).
const PRODUCTS = ["Butterfly", "Calendar", "Call Spread", "Future", "Put Spread", "Risk Reversal", "Straddle/Strangle", "Vanilla Call", "Vanilla Put"];
const TENORS = DATA.tenors;
const PILLARS = DATA.deltas;
const CONTRACTS: Record<string, number> = { "6E (€125k)": 125000, "M6E (€12.5k)": 12500 };
const DELTA_PER_6E = 125_000 * DATA.SPOT; // cash $-delta of 1 full 6E contract (notional × spot)

// notional → compact <sym>k / <sym>M label (sym = € or $)
const fmtCcy = (v: number, sym: string): string => (Math.abs(v) >= 1e6 ? sym + (v / 1e6).toFixed(2) + "M" : sym + Math.round(v / 1e3) + "k");
// signed notional : sign BEFORE the symbol so a short leg reads "−$3.57M"
const fmtCcySigned = (v: number, sym: string): string => (v < 0 ? "−" : "+") + fmtCcy(Math.abs(v), sym);

type GreekKey = "d" | "g" | "v" | "t" | "vn" | "vg";

interface StructMeta {
  mode: string;
  order: GreekKey[];
  naked: (s: string) => boolean;
  skew?: boolean;
}

// structure metadata — drives inputs, the greek read order, and the tail-risk classification
const STRUCT: Record<string, StructMeta> = {
  "Vanilla Call": { mode: "single", order: ["d", "g", "v", "t"], naked: (s) => s === "SELL" },
  "Vanilla Put": { mode: "single", order: ["d", "g", "v", "t"], naked: (s) => s === "SELL" },
  "Straddle": { mode: "atm", order: ["v", "g", "t", "d"], naked: (s) => s === "SELL" },
  "Strangle": { mode: "wing", order: ["v", "g", "t", "vn"], naked: (s) => s === "SELL" },
  "Straddle/Strangle": { mode: "ss", order: ["v", "g", "t", "vn"], naked: (s) => s === "SELL" },
  "Butterfly": { mode: "flywing", order: ["vg", "v", "g", "t"], naked: () => false },
  "Risk Reversal": { mode: "wing", order: ["vn", "vg", "d", "v"], naked: () => true, skew: true },
  "Call Spread": { mode: "spread", order: ["d", "v", "g", "t"], naked: () => false },
  "Put Spread": { mode: "spread", order: ["d", "v", "g", "t"], naked: () => false },
  "Calendar": { mode: "cal", order: ["v", "t", "vn", "g"], naked: () => false },
  "Future": { mode: "future", order: ["d"], naked: () => false },
};
interface Leg {
  instrument: string;
  type: string;
  side: string;
  qty: number;
  strike: number;
  tenor: string;
  iv: number;
  prem: number;
}

interface LegDetail {
  prem: number;
}

interface NetGreeks {
  d: number;
  g: number;
  v: number;
  t: number;
  vn: number;
  vg: number;
  cost: number;
  detail: LegDetail[];
}

interface Booked {
  side: string;
  product: string;
  qty: number;
  tenor: string;
  bundleHedge: boolean;
  hedgeQty: number;
  hedgeSide: string;
}

export interface Prefill {
  product: string;
  side?: string;
  tenor?: string;
  farTenor?: string;
  pcId: string;
  label: string;
}

function tenorIdx(t: string): number {
  return Math.max(0, TENORS.indexOf(t));
}
function pillarStrike(t: string, p: string): number {
  const s = DATA.smileFor(tenorIdx(t));
  const j = PILLARS.indexOf(p);
  return s.pts[j] ? s.pts[j]!.strike : DATA.SPOT;
}
function ivAt(t: string, strike: number): number {
  const sm = DATA.smileFor(tenorIdx(t));
  let best = sm.pts[0]!,
    bd = 1e9;
  sm.pts.forEach((pt) => {
    const d = Math.abs(pt.strike - strike);
    if (d < bd) {
      bd = d;
      best = pt;
    }
  });
  return best.iv;
}

function buildLegs(
  product: string,
  side: string,
  tenor: string,
  farTenor: string,
  strike: number,
  qty: number,
  wing: string,
): Leg[] {
  // `wing` is a full delta pillar ("25Δc" / "ATM"); symmetric wings use its level
  // ("25Δ"), single-strike structures (straddle/calendar) use the pillar itself.
  const atm = +pillarStrike(tenor, "ATM").toFixed(4);
  const level = wing === "ATM" ? "ATM" : wing.replace(/[pc]$/, "");
  const wc = level === "ATM" ? atm : +pillarStrike(tenor, level + "c").toFixed(4);
  const wp = level === "ATM" ? atm : +pillarStrike(tenor, level + "p").toFixed(4);
  const sharedK = wing === "ATM" ? atm : +pillarStrike(tenor, wing).toFixed(4);
  // a vertical spread needs width : an ATM strike-Δ would collapse both legs onto
  // ATM (zero greeks) → fall back to the 25Δ wing so the spread is always real.
  const scStrike = level === "ATM" ? +pillarStrike(tenor, "25Δc").toFixed(4) : wc;
  const spStrike = level === "ATM" ? +pillarStrike(tenor, "25Δp").toFixed(4) : wp;
  const opp = side === "BUY" ? "SELL" : "BUY";
  const mk = (typ: string, sd: string, k: number, ten: string, q?: number): Leg => {
    const iv = ivAt(ten, k);
    const prem = DATA.SPOT * (iv / 100) * Math.sqrt((tenorIdx(ten) + 1) / 12) * 0.42;
    return { instrument: "EURUSD", type: typ, side: sd, qty: q == null ? qty : q, strike: k, tenor: ten, iv, prem };
  };
  switch (product) {
    case "Vanilla Call":
      return [mk("Call", side, strike, tenor)];
    case "Vanilla Put":
      return [mk("Put", side, strike, tenor)];
    case "Straddle":
      return [mk("Call", side, sharedK, tenor), mk("Put", side, sharedK, tenor)];
    case "Strangle":
      // put below ATM, call above ATM ; width-guarded so an ATM wing doesn't
      // collapse both onto ATM (that would be a straddle, not a strangle).
      return [mk("Put", side, spStrike, tenor), mk("Call", side, scStrike, tenor)];
    case "Straddle/Strangle":
      // 50Δ (ATM) → straddle (same strike) ; 10Δ/25Δ → strangle (put wp / call wc)
      return wing === "ATM"
        ? [mk("Call", side, atm, tenor), mk("Put", side, atm, tenor)]
        : [mk("Put", side, wp, tenor), mk("Call", side, wc, tenor)];
    case "Butterfly":
      // Textbook call butterfly : long low + short 2× ATM body + long high (all
      // calls, one type). Uses the width-guarded wings (scStrike/spStrike) so an
      // ATM wing selection doesn't collapse all 3 legs onto ATM (zero greeks).
      return [mk("Call", side, spStrike, tenor), mk("Call", opp, atm, tenor, qty * 2), mk("Call", side, scStrike, tenor)];
    case "Risk Reversal":
      return [mk("Call", side, wc, tenor), mk("Put", opp, wp, tenor)];
    case "Call Spread":
      // vertical : long ATM call, short higher-strike call (defined risk)
      return [mk("Call", side, atm, tenor), mk("Call", opp, scStrike, tenor)];
    case "Put Spread":
      // vertical : long ATM put, short lower-strike put (defined risk)
      return [mk("Put", side, atm, tenor), mk("Put", opp, spStrike, tenor)];
    case "Calendar":
      return [mk("Call", opp, sharedK, tenor), mk("Call", side, sharedK, farTenor)];
    case "Future":
      return [{ instrument: "EURUSD", type: "Future", side, qty, strike: DATA.SPOT, tenor: "Sep26", iv: 0, prem: 0 }];
    default:
      return [];
  }
}

// Surface a backend error as one readable line. Handles the plain {detail:"…"},
// the {detail:{message}} submit shape, AND the execution-engine 502 envelopes
// ({error, exception} unreachable / {error, status, body} failed) so the operator
// sees the REAL cause ("IB Gateway not connected", "Connection refused") instead
// of an opaque "502".
function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = (e.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const d = detail as { message?: unknown; error?: unknown; exception?: unknown; body?: unknown; status?: unknown };
      if (typeof d.message === "string") return d.message;
      const parts: string[] = [];
      if (typeof d.error === "string") parts.push(d.error.replace(/_/g, " "));
      if (typeof d.exception === "string") parts.push(d.exception);
      else if (typeof d.body === "string" && d.body) parts.push(d.body);
      else if (typeof d.status === "number") parts.push(`upstream ${d.status}`);
      if (parts.length) return parts.join(" — ");
    }
    return `broker/API error ${e.status}`;
  }
  return e instanceof Error ? e.message : "unknown error";
}

// Client-side ESTIMATE of a structure's net greeks (a real BS greek needs the
// write-gated /trade/preview). Toy sensitivity model, but every greek scales with
// the contract LOT so switching 6E↔M6E moves them all together.
//
// Unit convention (matches the backend delta_usd + book greeks, so before+after
// foots): Δ = CASH dollar-delta = delta% × qty × notional × spot (in $, the
// standard "$ long/short"). Γ/Vega/Θ in $ (·/pip, ·/vp, ·/day) — gk$. Vanna/Volga
// in $k — "±N.Nk".
const FULL_LOT_EUR = 125_000; // 6E notional; a full lot = the €125k contract
function previewGreeks(legs: Leg[], mult: number): NetGreeks {
  const lot = mult / FULL_LOT_EUR;    // 1.0 for 6E, 0.1 for M6E — the notional scale
  const dollar = mult * 0.0001 * 100; // premium/2nd-order: $ per figure (= 1250 × lot)
  const cashDelta = mult * DATA.SPOT; // Δ: cash dollar-delta = notional × spot (backend delta_usd)
  let d = 0,
    g = 0,
    v = 0,
    th = 0,
    vn = 0,
    vg = 0,
    cost = 0;
  const detail = legs.map((l): LegDetail => {
    const sd = l.side === "BUY" ? 1 : -1;
    if (l.type === "Future") {
      d += sd * l.qty * cashDelta; // future = 100% delta → full cash notional
      return { prem: 0 };
    }
    const otm = Math.abs(l.strike - DATA.SPOT) / DATA.SPOT;
    // Directional delta grows mildly with tenor so a calendar (same strike, near
    // vs far) isn't perfectly delta-flat → its delta hedge is non-zero + usable.
    const tf = 1 + 0.03 * tenorIdx(l.tenor);
    const lg = { d: (l.type === "Call" ? 0.5 : -0.5) * tf + (DATA.SPOT - l.strike) * 4, g: 1.8 / (l.iv / 5 || 1), v: l.prem * 6, t: -l.prem * 9 };
    d += sd * lg.d * l.qty * cashDelta;           // $ (delta% × notional × spot)
    g += sd * lg.g * l.qty * 1000 * lot;          // $/pip
    v += sd * lg.v * l.qty * 100 * lot;           // $/vp
    th += sd * lg.t * l.qty * 100 * lot;          // $/day
    vn += sd * (l.type === "Call" ? 1 : -1) * (0.9 + otm * 70) * l.qty * 0.45 * lot; // $k/vp·fig
    vg += sd * (0.5 + otm * 55) * l.qty * 0.2 * lot;                                  // $k/vp
    const prem = sd * l.prem * l.qty * dollar;
    cost += prem;
    return { prem };
  });
  return { d: Math.round(d), g: Math.round(g), v: Math.round(v), t: Math.round(th), vn: +vn.toFixed(1), vg: +vg.toFixed(1), cost: Math.round(cost), detail };
}

export interface BuilderState {
  active: boolean;
  product: string;
  side: string;
  tenor: string;
  farTenor: string;
  qty: number;
  isCal: boolean;
  isFut: boolean;
  net: NetGreeks;
  naked: boolean;
}

// Callback shape matches TradeView's OrderRecord (kept local to avoid a view↔component import cycle).
type OrderLog = (rec: { action: "open" | "close"; label: string; side: string; qty: number; state: "sent" | "rejected"; note?: string }) => void;

interface OrderBuilderProps {
  prefill?: Prefill | null;
  onClearPrefill?: () => void;
  onState?: (state: BuilderState) => void;
  onOrder?: OrderLog;
}

export function OrderBuilder({ prefill, onClearPrefill, onState, onOrder }: OrderBuilderProps): JSX.Element {
  // Live spot for the MARKET display block (RT.1). The structure pricing/preview
  // still uses the mock spot until the live preview lands (6r.2).
  const { surface, trade } = useDeskData();
  const ticks = useTicks();
  // Write gate = real login state (auth cookie) OR the local-dev build bypass.
  const canWrite = useAuthStore((s) => s.authenticated) || WRITE_ENABLED;
  // Tenors with no listed contract (server-interpolated). Selecting one is allowed
  // but warns: the order routes to the nearest listed expiry (snap, backend).
  const interpTenors = new Set(
    (surface.data?.tenors ?? []).filter((_, i) => surface.data?.sources?.[i] === "interp"),
  );
  const [product, setProduct] = useState(PRODUCTS[0]!); // alphabetical first = default
  const [side, setSide] = useState("BUY");
  const [tenor, setTenor] = useState("3M");
  const [farTenor, setFarTenor] = useState("4M");
  const [strike, setStrike] = useState(1.0842);
  // true once the trader hand-picks a vanilla strike → stop auto-anchoring it to
  // the live ATM. Reset on product change.
  const userStrikeRef = useRef(false);
  const [wing, setWing] = useState("ATM"); // default strike Δ ; one of the 5 delta pillars
  const [qty, setQty] = useState(10);
  const [csize, setCsize] = useState("6E (€125k)");
  const [bundleHedge, setBundleHedge] = useState(false);
  const [stage, setStage] = useState("build"); // build | preview | booked
  const [booked, setBooked] = useState<Booked | null>(null);
  // 6w — real submit path. The preview is server-validated (POST /trade/preview);
  // "Place order" routes to the IB paper account (execution_mode "live").
  const [server, setServer] = useState<TradePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [placed, setPlaced] = useState<Record<string, unknown> | null>(null);
  const reset = (): void => {
    setStage("build");
    setServer(null);
    setErr(null);
    setPlaced(null);
  };

  useEffect(() => {
    if (prefill) {
      setProduct(prefill.product);
      setSide(prefill.side || "BUY");
      if (prefill.tenor) setTenor(prefill.tenor);
      if (prefill.farTenor) setFarTenor(prefill.farTenor);
      reset();
    }
  }, [prefill]);

  const meta = STRUCT[product]!;
  const isFut = product === "Future";
  const isCal = product === "Calendar";
  // contract size is only a choice for futures; options always trade the full 6E lot
  const mult = isFut ? CONTRACTS[csize]! : CONTRACTS["6E (€125k)"]!;
  // notional is sized in EUR (the 6E/M6E contracts are EUR-denominated); show its
  // USD leg too so the panel stays coherent whichever side is the call.
  const spotMid = ((ticks.data?.bid ?? DATA.SPOT) + (ticks.data?.ask ?? DATA.SPOT)) / 2;
  const nominalEur = qty * mult;
  const nominalUsd = nominalEur * spotMid;
  // The side flips the currency legs : BUY EUR/USD = long EUR (+€) / short USD (−$) ;
  // SELL = short EUR (−€) / long USD (+$) — i.e. selling EUR means BUYING dollars.
  const eurSigned = (side === "BUY" ? 1 : -1) * nominalEur;
  const usdSigned = (side === "BUY" ? -1 : 1) * nominalUsd;
  // The wing buttons expose all 5 delta pillars; the strike engine works off the
  // symmetric level ("25Δc"/"25Δp" → "25Δ"), with ATM as its own degenerate wing.
  const wingLevel = wing === "ATM" ? "ATM" : wing.replace(/[pc]$/, "");
  // MARKET IV tracks the strike Δ chosen in contract details (not fixed to ATM) :
  // single-strike vanillas read the hand-typed strike, everything else the wing.
  const mktIvStrike = meta.mode === "single" ? strike : pillarStrike(tenor, wing);
  const mktIv = ivAt(tenor, mktIvStrike);
  const mktIvLabel = meta.mode === "single" ? `IV ${tenor}` : `${wing} IV ${tenor}`;
  // Calendar has two expiries → also surface the far-tenor IV in MARKET.
  const farIv = ivAt(farTenor, pillarStrike(farTenor, wing));
  const farIvLabel = `${wing} IV ${farTenor}`;
  // Products whose two legs sit on different-Δ strikes → different IVs, so the
  // skew IS the trade variable : show BOTH wing IVs (e.g. "25Δc IV 3M" + "25Δp
  // IV 3M"). Strangle + a non-ATM Straddle/Strangle, and the Risk Reversal
  // (its selection signal is exactly IV_put − IV_call). Straddle (ATM) = one IV.
  const showWingSkew = product === "Strangle" || product === "Risk Reversal"
    || (product === "Straddle/Strangle" && wing !== "ATM");
  const callWingIv = ivAt(tenor, pillarStrike(tenor, wingLevel + "c"));
  const putWingIv = ivAt(tenor, pillarStrike(tenor, wingLevel + "p"));
  const wingSkew = putWingIv - callWingIv;  // risk-reversal skew, vol points

  // A hand-typed vanilla strike is shipped RAW to IB (orderLegs.ts), so it must
  // sit on the LIVE market — not the mock spot (DATA.SPOT = 1.0842). Otherwise the
  // contract is deep ITM/OTM vs the real ~1.14 market and IB won't qualify it.
  // Anchor the single-strike default + the Δ-pillar shortcuts to the live ATM,
  // snapped to the 0.005 CME grid. (Structured products send delta_pillar and the
  // backend resolves strikes from the live surface, so they're unaffected.)
  const gridStrike = (s: number): number => +(Math.round(s / 0.005) * 0.005).toFixed(4);
  const liveAtm = gridStrike(spotMid);
  const mockAtm = pillarStrike(tenor, "ATM");
  const liveStrikeFor = (w: string): number =>
    w === "ATM" ? liveAtm : gridStrike(liveAtm + (pillarStrike(tenor, w) - mockAtm));
  useEffect(() => {
    // keep the vanilla strike on the live ATM until the trader picks one
    if (meta.mode === "single" && !userStrikeRef.current) setStrike(liveAtm);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveAtm, meta.mode, tenor]);

  const legs = useMemo(() => buildLegs(product, side, tenor, farTenor, strike, qty, wing), [product, side, tenor, farTenor, strike, qty, wing]);
  // EUR/USD call = right to buy EUR vs USD → "EUR Call / USD Put" (and the inverse for a put)
  const callPutLabel = [
    legs.some((l) => l.type === "Call") ? "EUR Call / USD Put" : null,
    legs.some((l) => l.type === "Put") ? "USD Call / EUR Put" : null,
  ].filter(Boolean).join(" · ") || "—";
  const net = useMemo(() => previewGreeks(legs, mult), [legs, mult]);

  const commission = Math.round(legs.length * qty * 2.1);
  const isCredit = net.cost < 0; // SELL-heavy structures take in premium
  const premiumAbs = Math.abs(net.cost);
  const naked = meta.naked(side); // a sold leg with no long cover → unbounded tail
  // A future is already delta-one — you don't delta-hedge it with another future,
  // so the bundle is disabled for futures (also hidden in the UI).
  const hedgeOn = bundleHedge && !isFut;
  const hedgeQty = Math.round(Math.abs(net.d) / DELTA_PER_6E);
  const hedgeSide = net.d > 0 ? "SELL" : "BUY";
  const hedgedDelta = Math.round(net.d - Math.sign(net.d) * hedgeQty * DELTA_PER_6E); // delta once the bundle is applied
  // When the delta-hedge is bundled, the 6E futures leg neutralises delta and
  // adds its own commission — the ticket must re-price as the HEDGED structure
  // (futures are delta-only : Γ/Vega/Θ unchanged).
  const effDelta = hedgeOn ? hedgedDelta : net.d;
  const hedgeComm = hedgeOn ? Math.round(hedgeQty * 2.1) : 0;
  const totalCommission = commission + hedgeComm;

  // structure value + book before/after, one row per greek (and the cash legs below).
  // Same live book greeks the Trade "Portfolio greeks" panel shows (mock fallback).
  const g = trade.data?.greeks ?? DATA.greeks;
  const kfmt = (x: number): string => fmt.sgn(x, 1) + "k"; // $k greeks — 1 dp so small values don't collapse to "+0k"
  const netCash = (isCredit ? premiumAbs : -premiumAbs) - totalCommission;
  const impactRows = [
    { name: "Δ", unit: "USD", val: effDelta, before: g.netDelta, after: g.netDelta + effDelta, f: gk$ },
    { name: "Γ", unit: "USD/pip", val: net.g, before: g.netGamma, after: g.netGamma + net.g, f: gk$ },
    { name: "Vega", unit: "$/vp", val: net.v, before: g.netVega, after: g.netVega + net.v, f: gk$ },
    { name: "Θ", unit: "$/day", val: net.t, before: g.netTheta, after: g.netTheta + net.t, f: gk$ },
    { name: "Vanna", unit: "$k/vp·fig", val: net.vn, before: g.netVanna, after: g.netVanna + net.vn, f: kfmt },
    { name: "Volga", unit: "$k/vp", val: net.vg, before: g.netVolga, after: g.netVolga + net.vg, f: kfmt },
  ];

  // report the live structure up so the Indicators pre-trade check reads the SAME engine (state, not signal)
  useEffect(() => {
    if (onState) onState({ active: stage === "preview", product, side, tenor, farTenor, qty, isCal, isFut, net, naked });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, product, side, tenor, farTenor, qty, isCal, isFut, net, naked]);

  // Preview = server-priced validation. Read-only desk (no auth) still shows the
  // client-side risk truth, just without a server preview_id / submit.
  const onPreview = async (): Promise<void> => {
    if (!canWrite) { setStage("preview"); return; }
    setBusy(true);
    setErr(null);
    setServer(null);
    try {
      const resp = await createTradePreview(builderToLegs(product, side, tenor, farTenor, strike, wing, csize), qty);
      setServer(resp);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
      setStage("preview");
    }
  };
  // Place = submit the server-validated preview to the IB paper account. When a
  // delta hedge is bundled, a SECOND order (the 6E futures hedge) is fired right
  // after the structure — the desk sends two linked orders. The hedge is best-
  // effort : if it fails, the structure stays live and we surface the hedge error.
  const submitHedge = async (): Promise<void> => {
    const hLabel = `${hedgeQty}× 6E hedge (${product} ${tenor})`;
    try {
      const hp = await createTradePreview(
        [{ contract_type: "future", side: hedgeSide as "BUY" | "SELL", tenor, future_contract_size: "full" }],
        hedgeQty,
      );
      if (hp.state !== "valid_for_submit") {
        throw new Error(hp.blocking_reasons?.join(" · ") || `hedge preview ${hp.state}`);
      }
      await submitTrade(hp.preview_id, "live");
      onOrder?.({ action: "open", label: hLabel, side: hedgeSide, qty: hedgeQty, state: "sent" });
    } catch (he) {
      const hm = errMsg(he);
      setErr(`structure placed — hedge failed: ${hm}`);
      onOrder?.({ action: "open", label: hLabel, side: hedgeSide, qty: hedgeQty, state: "rejected", note: hm });
    }
  };
  const onPlace = async (): Promise<void> => {
    if (!canWrite || !server || server.state !== "valid_for_submit" || busy) return;
    setBusy(true);
    setErr(null);
    const label = `${product}${isFut ? "" : " " + tenor}${isCal ? "/" + farTenor : ""}${!isFut && STRUCT[product]!.mode.includes("wing") ? " " + wingLevel : ""}`;
    try {
      const result = await submitTrade(server.preview_id, "live");
      setPlaced(result);
      setBooked({ side, product, qty, tenor, bundleHedge: hedgeOn, hedgeQty, hedgeSide });
      onOrder?.({ action: "open", label, side, qty, state: "sent" });
      // second, linked order : the bundled futures delta hedge
      if (hedgeOn && hedgeQty > 0) await submitHedge();
      setStage("booked");
    } catch (e) {
      const m = errMsg(e);
      setErr(m);
      onOrder?.({ action: "open", label, side, qty, state: "rejected", note: m });
    } finally {
      setBusy(false);
    }
  };
  const canPlace = canWrite && server?.state === "valid_for_submit" && !busy;
  const stateText = !canWrite ? "auth required" : busy && !server ? "pricing…" : (server?.state ?? "—");
  const stateCls = server?.state === "valid_for_submit" ? "pos" : server?.state ? "warn" : "dim";

  return (
    <div className="builder">
      {prefill && (
        <div className="ob-signal-link">
          <span className="obs-tag mono">↳ prefilled from {prefill.pcId}</span>
          <b className="obs-struct">{prefill.label}</b>
          <button className="obs-clear" onClick={() => onClearPrefill && onClearPrefill()}>
            ✕
          </button>
        </div>
      )}

      {/* CONTRACT DETAILS (green) — product-driven (§6) */}
      <div className="builder-block block-in">
        <div className="block-tag">CONTRACT DETAILS</div>
        <div className="ob-info-row"><span>Currency pair</span><b className="mono">EUR/USD</b></div>
        <div className="field tenor-field"><span>Side</span>
          <div className="tenor-btns">
            <button type="button" className={"tenor-btn buy " + (side === "BUY" ? "on" : "")} onClick={() => { setSide("BUY"); reset(); }}>BUY</button>
            <button type="button" className={"tenor-btn sell " + (side === "SELL" ? "on" : "")} onClick={() => { setSide("SELL"); reset(); }}>SELL</button>
          </div>
        </div>
        <label className="field"><span>Product</span>
          <select value={product} onChange={(e) => {
            const p = e.target.value;
            setProduct(p);
            const m = STRUCT[p];
            // strangle / butterfly / RR / spreads NEED non-ATM wings (ATM collapses
            // them) → default 25Δ ; everything else (Straddle/Strangle, vanilla,
            // calendar) defaults to ATM.
            if (m) setWing(["wing", "flywing", "spread"].includes(m.mode) ? "25Δc" : "ATM");
            userStrikeRef.current = false; // re-anchor the vanilla strike to live ATM
            reset();
          }}>
            {PRODUCTS.map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>
        {!isFut && <div className="ob-info-row"><span>Call / Put</span><b className="mono">{callPutLabel}</b></div>}

        {/* tenor(s) — options only ; a future trades a fixed CME quarterly, so no
            tenor choice. Calendar exposes two expiries. */}
        {!isFut && (
        <div className="field tenor-field"><span>{isCal ? "Near tenor" : "Tenor"}</span>
          <div className="tenor-btns">
            {TENORS.map((t) => <button key={t} type="button" className={"tenor-btn " + (tenor === t ? "on" : "")} onClick={() => { setTenor(t); reset(); }}>{t}</button>)}
          </div>
        </div>
        )}
        {isCal && <div className="field tenor-field"><span>Far tenor</span>
          <div className="tenor-btns">
            {TENORS.map((t) => <button key={t} type="button" className={"tenor-btn " + (farTenor === t ? "on" : "")} onClick={() => { setFarTenor(t); reset(); }}>{t}</button>)}
          </div>
        </div>}
        {!isFut && (interpTenors.has(tenor) || (isCal && interpTenors.has(farTenor))) && (
          <div className="ob-interp-warn mono small">
            ⚠ {[tenor, isCal ? farTenor : null].filter((t) => t && interpTenors.has(t)).join(" / ")} interpolated —
            no listed contract; the order routes to the nearest listed expiry.
          </div>
        )}

        {/* manual strike — only the single-strike vanillas expose a hand-typed strike */}
        {meta.mode === "single" && (
          <label className="field"><span>Strike <em className="unit">USD</em></span>
            <div className="field-input"><input type="number" step="0.0001" value={strike} onChange={(e) => { setStrike(+e.target.value); userStrikeRef.current = true; reset(); }} /><em>USD</em></div>
          </label>
        )}

        {/* Straddle/Strangle : one Δ selector — 50Δ = straddle (ATM), 10Δ/25Δ = strangle */}
        {meta.mode === "ss" && (
          <div className="field tenor-field"><span>Strike Δ</span>
            <div className="tenor-btns">
              {[["10Δ", "10Δc"], ["25Δ", "25Δc"], ["50Δ (ATM)", "ATM"]].map(([lbl, w]) => (
                <button key={w} type="button" className={"tenor-btn " + (wing === w ? "on" : "")}
                  onClick={() => { setWing(w!); reset(); }}>{lbl}</button>
              ))}
            </div>
          </div>
        )}

        {/* wings / strike Δ — every other option product (all 5 delta pillars) */}
        {!isFut && meta.mode !== "ss" && (
          <div className="field tenor-field"><span>{meta.mode === "wing" || meta.mode === "flywing" ? "Wings" : "Strike Δ"}</span>
            <div className="tenor-btns">
              {PILLARS.map((w) => (
                <button key={w} type="button" className={"tenor-btn " + (wing === w ? "on" : "")}
                  onClick={() => { setWing(w); if (meta.mode === "single") { setStrike(liveStrikeFor(w)); userStrikeRef.current = true; } reset(); }}>{w}</button>
              ))}
            </div>
          </div>
        )}

        {/* put & call strikes + width — for the products with one put + one call
            leg on distinct strikes (straddle = equal, width 0 ; strangle + risk
            reversal spread around ATM). */}
        {(product === "Straddle/Strangle" || product === "Strangle" || product === "Straddle" || product === "Risk Reversal") && (() => {
          const put = legs.find((l) => l.type === "Put");
          const call = legs.find((l) => l.type === "Call");
          if (!put || !call) return null;
          return (
            <div className="ob-info-row"><span>Strikes <em className="unit">put / call</em></span>
              <b className="mono">{put.strike.toFixed(4)} <span className="dim">/</span> {call.strike.toFixed(4)}
                <span className="dim small"> · width {(call.strike - put.strike).toFixed(4)}</span>
              </b>
            </div>
          );
        })()}

        {isFut ? (
          <div className="field-row">
            <label className="field"><span>Size <em className="unit">contracts</em></span>
              <div className="field-input"><input type="number" min={1} step={1} value={qty} onChange={(e) => { setQty(Math.max(1, Math.floor(Number(e.target.value)) || 0)); reset(); }} /><em>ct</em></div>
            </label>
            <label className="field"><span>Contract</span>
              <select value={csize} onChange={(e) => { setCsize(e.target.value); reset(); }}>{Object.keys(CONTRACTS).map((c) => <option key={c}>{c}</option>)}</select>
            </label>
          </div>
        ) : (
          <label className="field"><span>Size <em className="unit">contracts · {fmtCcy(mult, "€")} / ct</em></span>
            <div className="field-input"><input type="number" min={1} step={1} value={qty} onChange={(e) => { setQty(Math.max(1, Math.floor(Number(e.target.value)) || 0)); reset(); }} /><em>ct</em></div>
          </label>
        )}
        {/* nominal traded = size × contract notional ; signed by side so a SELL
            reads "short €" / "long $" (selling EUR = buying dollars) */}
        <div className="ob-info-row"><span>Nominal <em className="unit">EUR / USD</em></span><b className="mono"><span className={eurSigned >= 0 ? "pos" : "neg"}>{fmtCcySigned(eurSigned, "€")}</span> <span className="dim">/</span> <span className={usdSigned >= 0 ? "pos" : "neg"}>{fmtCcySigned(usdSigned, "$")}</span></b></div>
      </div>

      {/* MARKET (yellow) */}
      <div className="builder-block block-mkt">
        <div className="block-tag">MARKET</div>
        <div className="mkt-rows">
          <div><span>Spot bid/ask</span><b className="mono">{(ticks.data?.bid ?? DATA.SPOT - 0.0001).toFixed(5)}/{(ticks.data?.ask ?? DATA.SPOT + 0.0001).toFixed(5)}</b></div>
          {showWingSkew ? (
            <>
              <div><span>{wingLevel}c IV {tenor}</span><b className="mono">{callWingIv.toFixed(1)}%</b></div>
              <div><span>{wingLevel}p IV {tenor}</span><b className="mono">{putWingIv.toFixed(1)}%</b></div>
              {product === "Risk Reversal" && (
                <div><span>Skew <em className="unit">p−c</em></span><b className={"mono " + (wingSkew >= 0 ? "pos" : "neg")}>{wingSkew >= 0 ? "+" : ""}{wingSkew.toFixed(1)}vp</b></div>
              )}
            </>
          ) : (
            <div><span>{mktIvLabel}</span><b className="mono">{mktIv.toFixed(1)}%</b></div>
          )}
          {isCal && <div><span>{farIvLabel}</span><b className="mono">{farIv.toFixed(1)}%</b></div>}
          <div><span>Fwd {tenor}</span><b className="mono">{DATA.smileFor(tenorIdx(tenor)).fwd.toFixed(4)}</b></div>
        </div>
      </div>

      {/* OUTPUTS (red) — the order ticket: priced client-side; Preview adds server validation */}
      <div className="builder-block block-out">
        <div className="block-tag">OUTPUTS</div>
        <div className="book-head">
          <span className="draft-title"><span className="draft-doc" />Order ticket{hedgeOn ? " + hedge" : ""}</span>
          <span className="badge-paper" title="orders route to the IB paper account">PAPER ACCOUNT</span>
        </div>
        <div className="book-kv">
          <div><span>Structure</span><b>{side} {qty}× {product}{isFut ? "" : " " + tenor}{isCal ? "/" + farTenor : ""}{!isFut && STRUCT[product]!.mode.includes("wing") ? " " + wingLevel : ""}</b></div>
          <div><span>Premium <em className="unit">Σ legs</em></span><b className={"mono " + (isCredit ? "pos" : "neg")}>{isCredit ? "+" : "−"}{fmt.usd(premiumAbs)}</b></div>
          <div><span>Commission{hedgeOn && hedgeQty > 0 ? " + hedge" : ""}</span><b className="mono neg">−{fmt.usd(totalCommission)}</b></div>
          <div><span>Net cash</span><b className={"mono " + (netCash >= 0 ? "pos" : "neg")}>{gk$(netCash)}</b></div>
        </div>
            <table className="dt legs">
              <thead><tr><th className="l">Leg</th><th>Side</th><th className="r">K</th><th className="r">IV</th><th className="r">Prem $</th></tr></thead>
              <tbody>
                {legs.map((l, i) => (
                  <tr key={i}>
                    <td className="l">{l.qty}× {l.type} {l.tenor}</td>
                    <td><span className={"side-pill " + (l.side === "BUY" ? "long" : "short")}>{l.side}</span></td>
                    <td className="r mono">{l.type === "Future" ? "—" : l.strike.toFixed(4)}</td>
                    <td className="r mono dim">{l.iv ? l.iv.toFixed(1) : "—"}</td>
                    <td className={"r mono " + pnlCls(net.detail[i] ? -net.detail[i]!.prem : 0)}>{net.detail[i] ? gk$(net.detail[i]!.prem) : "—"}</td>
                  </tr>
                ))}
                {hedgeOn && hedgeQty > 0 && (
                  <tr className="hedge-leg-row">
                    <td className="l">{hedgeQty}× 6E hedge</td>
                    <td><span className={"side-pill " + (hedgeSide === "BUY" ? "long" : "short")}>{hedgeSide}</span></td>
                    <td className="r mono dim">—</td>
                    <td className="r mono dim">—</td>
                    <td className="r mono dim">—</td>
                  </tr>
                )}
              </tbody>
            </table>

            {/* structure values + book before → after, plus the cash legs */}
            <table className="dt bi-table impact-table">
              <thead><tr><th className="l">Item</th><th className="r">Value</th><th className="r">Book before</th><th className="r after-col">Book after</th></tr></thead>
              <tbody>
                {impactRows.map((r) => (
                  <tr key={r.name}>
                    <td className="l">{r.name} <em className="unit mono">{r.unit}</em></td>
                    <td className={"r mono " + pnlCls(r.val)}>{r.f(r.val)}</td>
                    <td className={"r mono " + pnlCls(r.before)}>{r.f(r.before)}</td>
                    <td className={"r mono after-col " + pnlCls(r.after)}>{r.f(r.after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* delta-hedge bundle (no "Δ unhedged" row) */}
            {!isFut && (
              <div className="hedge-bundle">
                <label className="hb-toggle">
                  <input type="checkbox" checked={bundleHedge} onChange={(e) => setBundleHedge(e.target.checked)} />
                  <span>Delta hedge: <b className="mono">{hedgeSide} {hedgeQty}× 6E</b> → Δ {gk$(hedgedDelta)}</span>
                </label>
              </div>
            )}

            {/* preview identity + validation state — below the hedge toggle */}
            <div className="book-kv ob-preview-meta">
              <div><span>Preview id</span><b className="mono dim">{server?.preview_id ?? "—"}</b></div>
              <div><span>State</span><b className={"mono " + stateCls}>{stateText}</b></div>
            </div>

            {/* status + actions — Preview fills the empty ticket fields, then Place / Cancel */}
            {server?.blocking_reasons && server.blocking_reasons.length > 0 && (
              <div className="ob-blocking mono small">⛔ {server.blocking_reasons.join(" · ")}</div>
            )}
            {err && <div className="ob-error mono small">⚠ {err}</div>}
            {stage === "booked" && booked ? (
              <div className="book-result">
                <div>
                  <b className="book-result-title">Order submitted</b>
                  <span className="mono">
                    {booked.side} {booked.qty}× {booked.product} {booked.tenor}
                    {booked.bundleHedge ? " + " + booked.hedgeSide + " " + booked.hedgeQty + "× 6E" : ""} · IB paper account
                    {typeof placed?.["structure_id"] === "number" ? " · #" + String(placed["structure_id"]) : ""}
                  </span>
                </div>
                <button className="btn-ghost" onClick={reset}>New order</button>
              </div>
            ) : stage === "preview" ? (
              <div className="book-btns">
                <button
                  className="btn-draft-send"
                  disabled={!canPlace}
                  title={canWrite ? (canPlace ? "submit to IB paper account" : "preview must be valid_for_submit") : GATE_TITLE}
                  onClick={onPlace}
                >
                  {busy ? "Placing…" : "Place order"}
                </button>
                <button className="btn-draft-cancel" disabled={busy} onClick={reset}>Cancel</button>
              </div>
            ) : (
              <button className="btn-preview" disabled={busy} onClick={onPreview}>
                {busy ? "Pricing…" : <>Preview pricing &amp; impact<span className="arr">→</span></>}
              </button>
            )}
            {!canWrite && <div className="dim small ob-readonly-note">Read-only desk · log in to place orders.</div>}
      </div>
    </div>
  );
}
