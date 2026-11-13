/**
 * VOLDESK — multi-leg structure builder. Ported 1:1 from the prototype's
 * `js/order_builder.jsx` (global-window pattern) into a typed ES module.
 *
 * Inputs are product-driven (§6); the preview tells the RISK TRUTH of the
 * structure (§0 max-loss safety, §1 structure-relevant greeks, §2 pre-trade
 * book impact, §3 skew flag, §4 bundled hedge, §5 premium reconciliation).
 * Same JSX / classNames / logic as the prototype — only types + ES modules added.
 */
import { explainGateFailure, fmtBudget, isRecordedRefusal, refusalText } from "./refusal";
import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtCcy, fmtCcySigned, gk$, pnlCls } from "./format";
import { DATA, EMPTY_GREEKS, fmt } from "../data";
import { useDeskData, useTicks } from "../data/deskData";
import { createTradePreview, submitTrade, type TradePreview } from "../../api/endpoints";
import { WRITE_ENABLED } from "../data/writeEnabled";
import { useAuthStore } from "../../store/authStore";
import { buildLegs, gridStrike, ivAt, ladderColumn, livePillarStrike, pillarStrike, tenorIdx, type Leg, type StrikeResolver } from "./orderPillars";
import { builderToLegs } from "./orderLegs";

const GATE_TITLE = "write disabled — auth required (Phase 2)";
// Alphabetical order for the product dropdown; the first entry is the default.
// "Straddle/Strangle" is one product — a Δ selector (10Δ/25Δ/50Δ) picks between a
// strangle (10Δ/25Δ) and a straddle (50Δ = ATM).
const PRODUCTS = ["Butterfly", "Calendar", "Call/Put Spread", "Future", "Risk Reversal", "Straddle/Strangle", "Vanilla Call", "Vanilla Put"];
const TENORS = DATA.tenors;
const PILLARS = DATA.deltas;
const CONTRACTS: Record<string, number> = { "6E (€125k)": 125000, "M6E (€12.5k)": 12500 };
// cash $-delta of 1 full 6E contract (notional × spot), at the LIVE spot (#347)
const deltaPer6E = (spot: number): number => 125_000 * spot;


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
  // Combined vertical spread — a Call/Put toggle + two wings on that side
  // (near = long / far = short for BUY ; Side swaps roles). mode "vspread".
  "Call/Put Spread": { mode: "vspread", order: ["d", "v", "g", "t"], naked: () => false },
  "Calendar": { mode: "cal", order: ["v", "t", "vn", "g"], naked: () => false },
  "Future": { mode: "future", order: ["d"], naked: () => false },
};

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



// Surface a backend error as one readable line. Handles the plain {detail:"…"},
// the {detail:{message}} submit shape, AND the execution-engine 502 envelopes
// ({error, exception} unreachable / {error, status, body} failed) so the operator
// sees the REAL cause ("IB Gateway not connected", "Connection refused") instead
// of an opaque "502".
// The keys the gate's details use for each budget row.
const VALUE_KEY: Record<string, string> = {
  delta: "delta_usd", vega: "vega_usd_per_volpt", gamma: "gamma_usd_per_pip", notional: "notional_eur",
};
const CAP_KEY: Record<string, string> = { ...VALUE_KEY, notional: "max_notional_eur" };

// Every reason the server gave, in words. This used to keep the error name
// only: a gate refusal with three limits and their numbers was printed as
// "pretrade gate failed" (components/refusal, #478).
const errMsg = (e: unknown): string => refusalText(e);

// Client-side ESTIMATE of a structure's net greeks (a real BS greek needs the
// write-gated /trade/preview). Toy sensitivity model, but every greek scales with
// the contract LOT so switching 6E↔M6E moves them all together.
//
// Unit convention (matches the backend delta_usd + book greeks, so before+after
// foots): Δ = CASH dollar-delta = delta% × qty × notional × spot (in $, the
// standard "$ long/short"). Γ/Vega/Θ in $ (·/pip, ·/vp, ·/day) — gk$. Vanna/Volga
// in $k — "±N.Nk".
const FULL_LOT_EUR = 125_000; // 6E notional; a full lot = the €125k contract
function previewGreeks(legs: Leg[], mult: number, spot: number): NetGreeks {
  const lot = mult / FULL_LOT_EUR;    // 1.0 for 6E, 0.1 for M6E — the notional scale
  const dollar = mult * 0.0001 * 100; // premium/2nd-order: $ per figure (= 1250 × lot)
  const cashDelta = mult * spot;      // Δ: cash dollar-delta = notional × spot (backend delta_usd)
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
    const otm = Math.abs(l.strike - spot) / spot;
    // Directional delta grows mildly with tenor so a calendar (same strike, near
    // vs far) isn't perfectly delta-flat → its delta hedge is non-zero + usable.
    const tf = 1 + 0.03 * tenorIdx(l.tenor);
    const lg = { d: (l.type === "Call" ? 0.5 : -0.5) * tf + (spot - l.strike) * 4, g: 1.8 / (l.iv / 5 || 1), v: l.prem * 6, t: -l.prem * 9 };
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
type OrderLog = (rec: { action: "open" | "close"; label: string; side: string; qty: number; state: "sent" | "rejected"; note?: string; recorded?: boolean }) => void;

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
  const [farTenor, setFarTenor] = useState("6M"); // far enough from the 3M near to land in a different contract month (adjacent tenors collapse to one CME monthly)
  const [strike, setStrike] = useState(1.0842);
  // true once the trader hand-picks a vanilla strike → stop auto-anchoring it to
  // the live ATM. Reset on product change.
  const userStrikeRef = useRef(false);
  const [wing, setWing] = useState("ATM"); // default strike Δ ; one of the 5 delta pillars
  // Call/Put Spread : which wing (call/put) + the two legs by |Δ| level. near =
  // near-money (higher |Δ|, long for BUY) ; far = OTM wing (lower |Δ|, short).
  const [spreadSide, setSpreadSide] = useState("call");
  const [spreadNear, setSpreadNear] = useState("ATM");
  const [spreadFar, setSpreadFar] = useState("25Δ");
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
      // eslint-disable-next-line react-hooks/set-state-in-effect -- a new prefill request from another panel resets the form (prop-driven reset)
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
  // Live mid once a tick has arrived (a stale stream keeps its last value);
  // only a desk that never received one anchors on the preview constant, and
  // says so next to every spot it prints (#369).
  const ticksSeen = ticks.data != null;
  const spotMid = ticksSeen ? ((ticks.data!.bid ?? 0) + (ticks.data!.ask ?? 0)) / 2 || DATA.SPOT : DATA.SPOT;
  const spotNote = !ticksSeen ? "no live quote" : ticks.status !== "live" ? "stale" : "";
  const nominalEur = qty * mult;
  const nominalUsd = nominalEur * spotMid;
  // The side flips the currency legs : BUY EUR/USD = long EUR (+€) / short USD (−$) ;
  // SELL = short EUR (−€) / long USD (+$) — i.e. selling EUR means BUYING dollars.
  const eurSigned = (side === "BUY" ? 1 : -1) * nominalEur;
  const usdSigned = (side === "BUY" ? -1 : 1) * nominalUsd;
  // The wing buttons expose all 5 delta pillars; the strike engine works off the
  // symmetric level ("25Δc"/"25Δp" → "25Δ"), with ATM as its own degenerate wing.
  const wingLevel = wing === "ATM" ? "ATM" : wing.replace(/[pc]$/, "");
  // For the vertical spread, IV/labels track the near leg's wing (not `wing`).
  const activeWing = meta.mode === "vspread"
    ? (spreadNear === "ATM" ? "ATM" : spreadNear + (spreadSide === "call" ? "c" : "p"))
    : wing;
  const liveAtm = gridStrike(spotMid);
  // ONE strike per pillar for the buttons, the legs AND the ladder (#379): the
  // mock smile only lends its shape (offset from its own ATM); the level is live.
  const livePillar = useCallback(
    (t: string, p: string): number => livePillarStrike(liveAtm, t, p, pillarStrike),
    [liveAtm],
  );
  // Preview IV for a live strike: the pillar whose LIVE strike is nearest, read
  // off the mock smile by pillar (not by raw strike, which sat 400 pips away).
  const liveIvAt = useCallback(
    (t: string, k: number): number =>
      ivAt(t, pillarStrike(t, PILLARS[ladderColumn(k, t, PILLARS, livePillar)]!)),
    [livePillar],
  );
  const live = useMemo<StrikeResolver>(
    () => ({ strike: livePillar, iv: liveIvAt, spot: spotMid }),
    [livePillar, liveIvAt, spotMid],
  );
  const liveStrikeFor = (w: string): number => livePillar(tenor, w);
  // MARKET IV tracks the strike Δ chosen in contract details (not fixed to ATM) :
  // single-strike vanillas read the hand-typed strike, everything else the wing.
  const mktIv = meta.mode === "single" ? liveIvAt(tenor, strike) : ivAt(tenor, pillarStrike(tenor, activeWing));
  const mktIvLabel = meta.mode === "single" ? `IV ${tenor}` : `${activeWing} IV ${tenor}`;
  // Calendar has two expiries → also surface the far-tenor IV in MARKET.
  const farIv = ivAt(farTenor, pillarStrike(farTenor, activeWing));
  const farIvLabel = `${activeWing} IV ${farTenor}`;
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
  useEffect(() => {
    // keep the vanilla strike on the live ATM until the trader picks one
    if (meta.mode === "single" && !userStrikeRef.current) setStrike(liveAtm);
  }, [liveAtm, meta.mode, tenor]);

  const legs = useMemo(() => buildLegs(product, side, tenor, farTenor, strike, qty, wing, spreadSide, spreadNear, spreadFar, live), [product, side, tenor, farTenor, strike, qty, wing, spreadSide, spreadNear, spreadFar, live]);
  // EUR/USD call = right to buy EUR vs USD → "EUR Call / USD Put" (and the inverse for a put)
  const callPutLabel = [
    legs.some((l) => l.type === "Call") ? "EUR Call / USD Put" : null,
    legs.some((l) => l.type === "Put") ? "USD Call / EUR Put" : null,
  ].filter(Boolean).join(" · ") || "—";
  const net = useMemo(() => previewGreeks(legs, mult, spotMid), [legs, mult, spotMid]);

  // Once a preview exists the ticket shows what the SERVER priced: premium,
  // commission, and below the greeks the gate judges (#479). The browser's
  // model is only the sketch before a preview, and for the two greeks the
  // server does not send (vanna, volga), which are then labelled estimates.
  const srvPremium = server?.pricing?.premium_paid_usd;
  const srvCommission = server?.costs?.commission_usd;
  const commission = srvCommission != null ? Math.round(srvCommission) : Math.round(legs.length * qty * 2.1);
  const isCredit = srvPremium != null ? srvPremium < 0 : net.cost < 0; // SELL-heavy structures take in premium
  const premiumAbs = srvPremium != null ? Math.abs(srvPremium) : Math.abs(net.cost);
  const naked = meta.naked(side); // a sold leg with no long cover → unbounded tail
  // A future is already delta-one — you don't delta-hedge it with another future,
  // so the bundle is disabled for futures (also hidden in the UI).
  const hedgeOn = bundleHedge && !isFut;
  // The hedge is part of the package (#482): sized by the server with the
  // preview, judged with the structure, sent by the engine on each fill. The
  // browser's figure is only the sketch shown before a preview exists.
  const serverHedge = server?.hedge ?? null;
  const estHedgeQty = Math.round(Math.abs(net.d) / deltaPer6E(spotMid));
  const hedgeQty = serverHedge ? serverHedge.qty : estHedgeQty;
  const hedgeSide = serverHedge ? serverHedge.side : net.d > 0 ? "SELL" : "BUY";
  const hedgedDelta = serverHedge
    ? Math.round(serverHedge.residual_delta_usd)
    : Math.round(net.d - Math.sign(net.d) * estHedgeQty * deltaPer6E(spotMid)); // delta once the bundle is applied
  // When the delta-hedge is bundled, the 6E futures leg neutralises delta and
  // adds its own commission — the ticket must re-price as the HEDGED structure
  // (futures are delta-only : Γ/Vega/Θ unchanged).
  const effDelta = hedgeOn ? hedgedDelta : net.d;
  const hedgeComm = hedgeOn ? Math.round(hedgeQty * 2.1) : 0;
  const totalCommission = commission + hedgeComm;

  // structure value + book before/after, one row per greek (and the cash legs below).
  // Same live book greeks the Trade "Portfolio greeks" panel shows (zeros until live).
  const g = trade.data?.greeks ?? EMPTY_GREEKS;
  const kfmt = (x: number): string => fmt.sgn(x, 1) + "k"; // $k greeks — 1 dp so small values don't collapse to "+0k"
  const netCash = (isCredit ? premiumAbs : -premiumAbs) - totalCommission;
  const srv = (key: string): { val: number; before: number; after: number } | null => {
    const r = server?.risk_budget?.rows.find((x) => x.key === key);
    return r && r.trade != null && r.after != null ? { val: r.trade, before: r.before ?? 0, after: r.after } : null;
  };
  const srvTheta = typeof server?.greeks_net?.theta_usd_per_day === "number" ? server.greeks_net.theta_usd_per_day : null;
  const est = server ? " (est.)" : "";
  const dS = srv("delta"), gS = srv("gamma"), vS = srv("vega");
  const impactRows = [
    { name: "Delta", unit: "USD", ...(dS ?? { val: effDelta, before: g.netDelta, after: g.netDelta + effDelta }), f: gk$ },
    { name: "Gamma", unit: "USD/pip", ...(gS ?? { val: net.g, before: g.netGamma, after: g.netGamma + net.g }), f: gk$ },
    { name: "Vega", unit: "$/vp", ...(vS ?? { val: net.v, before: g.netVega, after: g.netVega + net.v }), f: gk$ },
    { name: "Theta", unit: "$/day", val: srvTheta ?? net.t, before: g.netTheta, after: g.netTheta + (srvTheta ?? net.t), f: gk$ },
    { name: "Vanna" + est, unit: "$k/vp·fig", val: net.vn, before: g.netVanna, after: g.netVanna + net.vn, f: kfmt },
    { name: "Volga" + est, unit: "$k/vp", val: net.vg, before: g.netVolga, after: g.netVolga + net.vg, f: kfmt },
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
      const resp = await createTradePreview(
        builderToLegs(product, side, tenor, farTenor, strike, wing, csize, spreadSide, spreadNear, spreadFar),
        qty, "EURUSD", hedgeOn,
      );
      setServer(resp);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
      setStage("preview");
    }
  };
  // Place = submit the server-validated preview to the IB paper account. A
  // hedged trade is ONE order here: the server sized the hedge with the
  // preview and the engine sends the futures as the options fill. The second
  // order the browser used to fire is gone.
  const onPlace = async (): Promise<void> => {
    if (!canWrite || !server || server.state !== "valid_for_submit" || busy) return;
    setBusy(true);
    setErr(null);
    const label = `${product}${isFut ? "" : " " + tenor}${isCal ? "/" + farTenor : ""}${!isFut && STRUCT[product]!.mode.includes("wing") ? " " + wingLevel : ""}`;
    try {
      const result = await submitTrade(server.preview_id, "live", needsConfirm);
      setPlaced(result);
      setBooked({ side, product, qty, tenor, bundleHedge: hedgeOn, hedgeQty, hedgeSide });
      onOrder?.({ action: "open", label, side, qty, state: "sent" });
      setStage("booked");
    } catch (e) {
      const m = errMsg(e);
      setErr(m);
      // A refusal by our own checks is recorded by the server; the blotter
      // reads it back from there ("refused"), so it is not added from memory.
      onOrder?.({ action: "open", label, side, qty, state: "rejected", note: m, recorded: isRecordedRefusal(e) });
    } finally {
      setBusy(false);
    }
  };
  // What the submit would refuse right now, known from the preview: a limit
  // the trade breaks, a surface too old. Place is offered only when the
  // submit can pass; the reasons are listed under the server's verdict.
  const blockers = server?.submit_blockers ?? [];
  const budget = server?.risk_budget ?? null;
  const canPlace = canWrite && server?.state === "valid_for_submit" && blockers.length === 0 && !busy;
  // Inside the limits, above their warning share: placing is a confirmation.
  const needsConfirm = server?.needs_confirmation === true;
  const maxQty = budget?.max_qty ?? null;
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
            if (p === "Call/Put Spread") { setSpreadSide("call"); setSpreadNear("ATM"); setSpreadFar("25Δ"); }
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

        {/* Wing Δ selector — 10Δ / 25Δ only — for the symmetric-wing structures:
            Risk Reversal (call/put at the level) and Butterfly (both wings at the
            level around the ATM body). No ATM: it degenerates the width. */}
        {(meta.skew || meta.mode === "flywing") && (
          <div className="field tenor-field"><span>Wings</span>
            <div className="tenor-btns">
              {[["10Δ", "10Δc"], ["25Δ", "25Δc"]].map(([lbl, w]) => (
                <button key={w} type="button" className={"tenor-btn " + (wing === w ? "on" : "")}
                  onClick={() => { setWing(w!); reset(); }}>{lbl}</button>
              ))}
            </div>
          </div>
        )}

        {/* Call/Put Spread : Call/Put toggle + two wings on that side. Near = long
            (higher |Δ|, closer to F) ; Far = short (lower |Δ|). Side swaps roles;
            the guard keeps near |Δ| > far |Δ| so the width is never degenerate. */}
        {meta.mode === "vspread" && (
          <>
            <div className="field tenor-field"><span>Wing</span>
              <div className="tenor-btns">
                {[["Call", "call"], ["Put", "put"]].map(([lbl, w]) => (
                  <button key={w} type="button" className={"tenor-btn " + (spreadSide === w ? "on" : "")}
                    onClick={() => { setSpreadSide(w!); setSpreadNear("ATM"); setSpreadFar("25Δ"); reset(); }}>{lbl}</button>
                ))}
              </div>
            </div>
            <div className="field tenor-field"><span>Near ▸ long</span>
              <div className="tenor-btns">
                {["ATM", "25Δ"].map((w) => (
                  <button key={w} type="button" className={"tenor-btn " + (spreadNear === w ? "on" : "")}
                    onClick={() => { setSpreadNear(w); if (w === "25Δ" && spreadFar === "25Δ") setSpreadFar("10Δ"); reset(); }}>{w}</button>
                ))}
              </div>
            </div>
            <div className="field tenor-field"><span>Far ▸ short</span>
              <div className="tenor-btns">
                {["25Δ", "10Δ"].map((w) => (
                  <button key={w} type="button" className={"tenor-btn " + (spreadFar === w ? "on" : "")}
                    onClick={() => { setSpreadFar(w); if (w === "25Δ" && spreadNear === "25Δ") setSpreadNear("ATM"); reset(); }}>{w}</button>
                ))}
              </div>
            </div>
          </>
        )}

        {/* wings / strike Δ — every other option product (all 5 delta pillars) */}
        {!isFut && meta.mode !== "ss" && !meta.skew && meta.mode !== "vspread" && meta.mode !== "flywing" && (
          <div className="field tenor-field"><span>{meta.mode === "wing" ? "Wings" : "Strike Δ"}</span>
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
        <div className="ob-info-row"><span>Nominal <em className="unit">EUR / USD</em></span><b className="mono"><span className={eurSigned >= 0 ? "pos" : "neg"}>{fmtCcySigned(eurSigned, "€")}</span> <span className="dim">/</span> {ticksSeen ? <span className={usdSigned >= 0 ? "pos" : "neg"}>{fmtCcySigned(usdSigned, "$")}</span> : <span className="dim" title="no live quote: the USD leg needs a spot">—</span>}</b></div>
        {/* strike ladder — where each leg sits across the 5 delta pillars (spot
            line + a tick per wing; legs stacked under their strike). Bottom of
            the INPUTS block so it updates live as the structure is configured. */}
        {!isFut && legs.length > 0 && (() => {
          const cols: Leg[][] = PILLARS.map(() => []);
          for (const l of legs) {
            if (l.type === "Future") continue;
            // Same resolver as the Strike Δ buttons: the column IS the pillar (#379).
            cols[ladderColumn(l.strike, l.tenor, PILLARS, livePillar)]!.push(l);
          }
          // multiplier relative to the base (smallest) leg qty → a doubled leg
          // (e.g. a butterfly's 2× ATM body) reads "2× Call 3M Short".
          const baseQty = Math.min(...legs.map((l) => l.qty));
          return (
            <div className="strike-ladder">
              <div className="sl-title"><span>Legs on the smile</span><em className="unit mono">spot {ticksSeen ? spotMid.toFixed(4) : "\u2014"}{spotNote ? ` (${spotNote})` : ""}</em></div>
              <div className="sl-axis">
                {PILLARS.map((p, i) => (
                  <div key={p} className="sl-col">
                    <span className="sl-label">{p}</span>
                    <div className="sl-mark"><span className="sl-tick" /></div>
                    <div className="sl-legs">
                      {cols[i]!.map((l, j) => (
                        <span key={j} className={"sl-leg " + (l.side === "BUY" ? "long" : "short")}>
                          {l.qty > baseQty ? `${Math.round(l.qty / baseQty)}× ` : ""}{l.type} {l.tenor} {l.side === "BUY" ? "Long" : "Short"}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })()}
      </div>

      {/* MARKET (yellow) */}
      <div className="builder-block block-mkt">
        <div className="block-tag">MARKET</div>
        <div className="mkt-rows">
          <div><span>Spot bid/ask</span><b className="mono">{ticksSeen ? `${(ticks.data!.bid ?? 0).toFixed(5)}/${(ticks.data!.ask ?? 0).toFixed(5)}` : "\u2014 / \u2014"}{spotNote && <span className="dim small"> {spotNote}</span>}</b></div>
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

            {/* The server's verdict on this trade: the limits, what the book
                uses before and after, as the pre-trade gate will judge it.
                These are the numbers the system books and limits; the table
                above is the browser's estimate. */}
            {budget && budget.rows.length > 0 && (
              <table className="dt bi-table impact-table" data-testid="risk-budget">
                <thead>
                  <tr>
                    <th className="l">Limit check <em className="unit mono">server</em></th>
                    <th className="r">This trade</th><th className="r">Book before</th>
                    <th className="r after-col">Book after</th><th className="r">Limit</th><th className="r">Used</th>
                  </tr>
                </thead>
                <tbody>
                  {budget.rows.map((r) => (
                    <tr key={r.key} data-breached={r.breached || undefined}>
                      <td className="l">{r.label} <em className="unit mono">{r.unit}</em></td>
                      <td className="r mono">{fmtBudget(r, r.trade)}</td>
                      <td className="r mono">{fmtBudget(r, r.before)}</td>
                      <td className={"r mono after-col " + (r.breached ? "neg" : "")}>{fmtBudget(r, r.after)}</td>
                      <td className="r mono dim">{fmtBudget(r, r.cap)}</td>
                      <td className={"r mono " + (r.breached ? "neg" : r.used != null && r.used > 0.8 ? "warn" : "pos")}>
                        {r.used == null ? "—" : Math.round(r.used * 100) + "%"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {blockers.length > 0 && (
              <div className="ob-blocking mono small" data-testid="submit-blockers">
                {maxQty != null && maxQty < qty && (
                  <div data-testid="max-qty" style={{ marginBottom: 4 }}>
                    {maxQty > 0
                      ? <>Largest size inside the limits: <b>{maxQty}</b> contract{maxQty > 1 ? "s" : ""}{" "}
                          <button className="row-close" onClick={() => { setQty(maxQty); reset(); }}>use {maxQty}</button></>
                      : "Not even one contract of this trade fits the limits."}
                  </div>
                )}
                ⛔ This order would be refused:
                <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                  {blockers.map((c) => (
                    <li key={c}>
                      {explainGateFailure(c, {
                        ...(budget?.details ?? {}),
                        caps: Object.fromEntries((budget?.rows ?? []).map((r) => [CAP_KEY[r.key] ?? r.key, r.cap])),
                        post_trade: Object.fromEntries((budget?.rows ?? []).map((r) => [VALUE_KEY[r.key] ?? r.key, r.after])),
                        surface_age_s: server?.surface_age_seconds,
                        max_surface_age_s: server?.max_surface_age_seconds,
                      })}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {needsConfirm && blockers.length === 0 && (
              <div className="ob-blocking mono small" data-testid="needs-confirmation">
                ⚠ This trade uses more than 80 % of a limit ({(budget?.rows ?? []).filter((r) => r.warning).map((r) => r.label).join(", ")}).
                Placing it is recorded as your confirmation.
              </div>
            )}

            {/* delta-hedge bundle (no "Δ unhedged" row) */}
            {!isFut && (
              <div className="hedge-bundle">
                <label className="hb-toggle">
                  {/* the hedge is part of what was previewed: changing it asks for a new preview */}
                  <input type="checkbox" checked={bundleHedge} onChange={(e) => { setBundleHedge(e.target.checked); reset(); }} />
                  <span>Delta hedge: <b className="mono">{hedgeSide} {hedgeQty}× 6E</b> → Delta {gk$(hedgedDelta)}</span>
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
                  {busy ? "Placing…" : needsConfirm ? "Confirm and place" : "Place order"}
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
