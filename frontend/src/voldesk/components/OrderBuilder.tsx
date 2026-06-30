/**
 * VOLDESK — multi-leg structure builder. Ported 1:1 from the prototype's
 * `js/order_builder.jsx` (global-window pattern) into a typed ES module.
 *
 * Inputs are product-driven (§6); the preview tells the RISK TRUTH of the
 * structure (§0 max-loss safety, §1 structure-relevant greeks, §2 pre-trade
 * book impact, §3 skew flag, §4 bundled hedge, §5 premium reconciliation).
 * Same JSX / classNames / logic as the prototype — only types + ES modules added.
 */
import { useEffect, useMemo, useState } from "react";
import { gk$, pnlCls } from "./format";
import { DATA, fmt } from "../data";
import { useDeskData, useTicks } from "../data/deskData";
import { ApiError } from "../../api/client";
import { createTradePreview, submitTrade, type TradePreview } from "../../api/endpoints";
import { WRITE_ENABLED } from "../data/writeEnabled";
import { builderToLegs } from "./orderLegs";

const GATE_TITLE = "write disabled — auth required (Phase 2)";
const PRODUCTS = ["Vanilla Call", "Vanilla Put", "Straddle", "Strangle", "Butterfly", "Risk Reversal", "Calendar", "Future"];
const TENORS = DATA.tenors;
const PILLARS = DATA.deltas;
const CONTRACTS: Record<string, number> = { "6E (€125k)": 125000, "M6E (€12.5k)": 12500 };
const DELTA_PER_6E = 1250; // $ delta flattened per 1 contract of 6E (1 big-fig)

// notional → compact <sym>k / <sym>M label (sym = € or $)
const fmtCcy = (v: number, sym: string): string => (Math.abs(v) >= 1e6 ? sym + (v / 1e6).toFixed(2) + "M" : sym + Math.round(v / 1e3) + "k");

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
  "Butterfly": { mode: "flywing", order: ["vg", "v", "g", "t"], naked: () => false },
  "Risk Reversal": { mode: "wing", order: ["vn", "vg", "d", "v"], naked: () => true, skew: true },
  "Calendar": { mode: "cal", order: ["v", "t", "vn", "g"], naked: () => false },
  "Future": { mode: "future", order: ["d"], naked: () => false },
};
const GREEK_INFO: Record<GreekKey, { label: string; unit: string; name: string }> = {
  d: { label: "Δ", unit: "$", name: "delta" },
  g: { label: "Γ", unit: "$/pip", name: "gamma" },
  v: { label: "Vega", unit: "$/vp", name: "vega" },
  t: { label: "Θ", unit: "$/day", name: "theta" },
  vn: { label: "Vanna", unit: "$k/vp·fig", name: "vanna · skew" },
  vg: { label: "Volga", unit: "$k/vp", name: "volga · convexity" },
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
      return [mk("Put", side, wp, tenor), mk("Call", side, wc, tenor)];
    case "Butterfly":
      return [mk("Call", side, wc, tenor), mk("Call", opp, atm, tenor, qty * 2), mk("Put", side, wp, tenor)];
    case "Risk Reversal":
      return [mk("Call", side, wc, tenor), mk("Put", opp, wp, tenor)];
    case "Calendar":
      return [mk("Call", opp, sharedK, tenor), mk("Call", side, sharedK, farTenor)];
    case "Future":
      return [{ instrument: "EURUSD", type: "Future", side, qty, strike: DATA.SPOT, tenor: "Sep26", iv: 0, prem: 0 }];
    default:
      return [];
  }
}

// Surface a backend error as one readable line (handles the structured
// {detail:{message}} the submit endpoint returns, plain {detail}, and network).
function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = (e.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const msg = (detail as { message?: unknown }).message;
      if (typeof msg === "string") return msg;
    }
    return `broker/API error ${e.status}`;
  }
  return e instanceof Error ? e.message : "unknown error";
}

// per-leg greeks → structure net. Vanna/volga are first-class (§1): a long-call/short-put RR ADDS
// vanna while its vega ≈ 0. cost is signed by cash convention (BUY pays = +debit, SELL receives = −credit).
function previewGreeks(legs: Leg[], mult: number): NetGreeks {
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
      const dd = sd * l.qty * mult * 0.0001 * 100;
      d += dd;
      return { prem: 0 };
    }
    const otm = Math.abs(l.strike - DATA.SPOT) / DATA.SPOT;
    const lg = { d: (l.type === "Call" ? 0.5 : -0.5) + (DATA.SPOT - l.strike) * 4, g: 1.8 / (l.iv / 5 || 1), v: l.prem * 6, t: -l.prem * 9 };
    d += sd * lg.d * l.qty * mult * 0.0001 * 100;
    g += sd * lg.g * l.qty * 1000;
    v += sd * lg.v * l.qty * 100;
    th += sd * lg.t * l.qty * 100;
    vn += sd * (l.type === "Call" ? 1 : -1) * (0.9 + otm * 70) * l.qty * 0.45; // skew, $k
    vg += sd * (0.5 + otm * 55) * l.qty * 0.2; // convexity, $k
    const prem = sd * l.prem * l.qty * mult * 0.0001 * 100;
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

interface OrderBuilderProps {
  prefill?: Prefill | null;
  onClearPrefill?: () => void;
  onState?: (state: BuilderState) => void;
}

export function OrderBuilder({ prefill, onClearPrefill, onState }: OrderBuilderProps): JSX.Element {
  // Live spot for the MARKET display block (RT.1). The structure pricing/preview
  // still uses the mock spot until the live preview lands (6r.2).
  const { surface } = useDeskData();
  const ticks = useTicks();
  // Tenors with no listed contract (server-interpolated). Selecting one is allowed
  // but warns: the order routes to the nearest listed expiry (snap, backend).
  const interpTenors = new Set(
    (surface.data?.tenors ?? []).filter((_, i) => surface.data?.sources?.[i] === "interp"),
  );
  const [product, setProduct] = useState("Risk Reversal");
  const [side, setSide] = useState("BUY");
  const [tenor, setTenor] = useState("2M");
  const [farTenor, setFarTenor] = useState("4M");
  const [strike, setStrike] = useState(1.0842);
  const [wing, setWing] = useState("25Δc"); // one of the 5 delta pillars
  const [qty, setQty] = useState(25);
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
  // The wing buttons expose all 5 delta pillars; the strike engine works off the
  // symmetric level ("25Δc"/"25Δp" → "25Δ"), with ATM as its own degenerate wing.
  const wingLevel = wing === "ATM" ? "ATM" : wing.replace(/[pc]$/, "");

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
  const hedgeQty = Math.round(Math.abs(net.d) / DELTA_PER_6E);
  const hedgeSide = net.d > 0 ? "SELL" : "BUY";
  const hedgedDelta = Math.round(net.d - Math.sign(net.d) * hedgeQty * DELTA_PER_6E); // delta once the bundle is applied
  const afterDelta = bundleHedge ? hedgedDelta : net.d;

  // greek value formatter — still used by the order ticket's lead-greek line
  const greekVal = (key: GreekKey): string =>
    ({ d: gk$(net.d), g: gk$(net.g), v: gk$(net.v), t: gk$(net.t), vn: fmt.sgn(net.vn, 1) + "k", vg: fmt.sgn(net.vg, 1) + "k" })[key];

  // structure value + book before/after, one row per greek (and the cash legs below)
  const g = DATA.greeks;
  const kfmt = (x: number): string => fmt.sgn(x, 0) + "k";
  const netCash = (isCredit ? premiumAbs : -premiumAbs) - commission;
  const impactRows = [
    { name: "Δ", unit: "USD", val: net.d, before: g.netDelta, after: g.netDelta + afterDelta, f: gk$ },
    { name: "Γ", unit: "USD/pip", val: net.g, before: g.netGamma, after: g.netGamma + net.g, f: gk$ },
    { name: "Vega", unit: "$/vp", val: net.v, before: g.netVega, after: g.netVega + net.v, f: gk$ },
    { name: "Θ", unit: "$/day", val: net.t, before: g.netTheta, after: g.netTheta + net.t, f: gk$ },
    { name: "Vanna", unit: "$k/vp·fig", val: net.vn, before: g.netVanna, after: g.netVanna + net.vn, f: kfmt },
    { name: "Volga", unit: "$k/vp", val: net.vg, before: g.netVolga, after: g.netVolga + net.vg, f: kfmt },
  ];

  // stress-truth tail figure for naked structures — read this, not a false finite max-loss
  const shortNotionalEur = qty * mult;
  const stressLoss = -Math.round(shortNotionalEur * 400 * 0.0001 * 0.78 + Math.abs(net.v) * 4);

  // report the live structure up so the Indicators pre-trade check reads the SAME engine (state, not signal)
  useEffect(() => {
    if (onState) onState({ active: stage === "preview", product, side, tenor, farTenor, qty, isCal, isFut, net, naked });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, product, side, tenor, farTenor, qty, isCal, isFut, net, naked]);

  // Preview = server-priced validation. Read-only desk (no auth) still shows the
  // client-side risk truth, just without a server preview_id / submit.
  const onPreview = async (): Promise<void> => {
    if (!WRITE_ENABLED) { setStage("preview"); return; }
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
  // Place = submit the server-validated preview to the IB paper account.
  const onPlace = async (): Promise<void> => {
    if (!WRITE_ENABLED || !server || server.state !== "valid_for_submit" || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const result = await submitTrade(server.preview_id, "live");
      setPlaced(result);
      setBooked({ side, product, qty, tenor, bundleHedge, hedgeQty, hedgeSide });
      setStage("booked");
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  const canPlace = WRITE_ENABLED && server?.state === "valid_for_submit" && !busy;
  const stateText = !WRITE_ENABLED ? "auth required" : busy && !server ? "pricing…" : (server?.state ?? "—");
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
            // wing structures default to a 25Δ wing; everything else defaults to ATM
            if (m) setWing(m.mode === "wing" || m.mode === "flywing" ? "25Δc" : "ATM");
            reset();
          }}>
            {PRODUCTS.map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>
        {!isFut && <div className="ob-info-row"><span>Call / Put</span><b className="mono">{callPutLabel}</b></div>}

        {/* tenor(s) — all 6 always visible as buttons; calendar exposes two expiries */}
        <div className="field tenor-field"><span>{isCal ? "Near tenor" : "Tenor"}</span>
          <div className="tenor-btns">
            {TENORS.map((t) => <button key={t} type="button" className={"tenor-btn " + (tenor === t ? "on" : "")} onClick={() => { setTenor(t); reset(); }}>{t}</button>)}
          </div>
        </div>
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
            <div className="field-input"><input type="number" step="0.0001" value={strike} onChange={(e) => { setStrike(+e.target.value); reset(); }} /><em>USD</em></div>
          </label>
        )}

        {/* wings / strike Δ — available on every product (all 5 delta pillars) */}
        {!isFut && (
          <div className="field tenor-field"><span>{meta.mode === "wing" || meta.mode === "flywing" ? "Wings" : "Strike Δ"}</span>
            <div className="tenor-btns">
              {PILLARS.map((w) => (
                <button key={w} type="button" className={"tenor-btn " + (wing === w ? "on" : "")}
                  onClick={() => { setWing(w); if (meta.mode === "single") setStrike(+pillarStrike(tenor, w).toFixed(4)); reset(); }}>{w}</button>
              ))}
            </div>
          </div>
        )}

        {isFut ? (
          <div className="field-row">
            <label className="field"><span>Size <em className="unit">contracts</em></span>
              <div className="field-input"><input type="number" value={qty} onChange={(e) => { setQty(+e.target.value); reset(); }} /><em>ct</em></div>
            </label>
            <label className="field"><span>Contract</span>
              <select value={csize} onChange={(e) => { setCsize(e.target.value); reset(); }}>{Object.keys(CONTRACTS).map((c) => <option key={c}>{c}</option>)}</select>
            </label>
          </div>
        ) : (
          <label className="field"><span>Size <em className="unit">contracts · {fmtCcy(mult, "€")} / ct</em></span>
            <div className="field-input"><input type="number" value={qty} onChange={(e) => { setQty(+e.target.value); reset(); }} /><em>ct</em></div>
          </label>
        )}
        {/* nominal traded = size × contract notional — shown in both legs (EUR base / USD) */}
        <div className="ob-info-row"><span>Nominal <em className="unit">EUR / USD</em></span><b className="mono">{fmtCcy(nominalEur, "€")} <span className="dim">/</span> {fmtCcy(nominalUsd, "$")}</b></div>
      </div>

      {/* MARKET (yellow) */}
      <div className="builder-block block-mkt">
        <div className="block-tag">MARKET</div>
        <div className="mkt-rows">
          <div><span>Spot bid/ask</span><b className="mono">{(ticks.data?.bid ?? DATA.SPOT - 0.0001).toFixed(5)}/{(ticks.data?.ask ?? DATA.SPOT + 0.0001).toFixed(5)}</b></div>
          <div><span>ATM IV {tenor}</span><b className="mono">{DATA.ivSurface[tenorIdx(tenor)]![2]!.toFixed(1)}%</b></div>
          <div><span>Fwd {tenor}</span><b className="mono">{DATA.smileFor(tenorIdx(tenor)).fwd.toFixed(4)}</b></div>
        </div>
      </div>

      {/* OUTPUTS (red) — always priced client-side; preview only adds the server validation */}
      <div className="builder-block block-out">
        <div className="block-tag">OUTPUTS</div>
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
                <tr className="impact-sep">
                  <td className="l">Net premium <em className="unit mono">Σ legs</em></td>
                  <td className={"r mono " + (isCredit ? "pos" : "neg")}>{isCredit ? "+" : "−"}{fmt.usd(premiumAbs)}</td>
                  <td className="r mono dim">—</td>
                  <td className="r mono dim after-col">—</td>
                </tr>
                <tr>
                  <td className="l">Commission</td>
                  <td className="r mono neg">−{fmt.usd(commission)}</td>
                  <td className="r mono dim">—</td>
                  <td className="r mono dim after-col">—</td>
                </tr>
                <tr className="impact-total">
                  <td className="l">Net cash</td>
                  <td className={"r mono " + (netCash >= 0 ? "pos" : "neg")}>{gk$(netCash)}</td>
                  <td className="r mono dim">—</td>
                  <td className="r mono dim after-col">—</td>
                </tr>
              </tbody>
            </table>

            {/* delta-hedge bundle (no "Δ unhedged" row) */}
            {!isFut && (
              <div className="hedge-bundle">
                <label className="hb-toggle">
                  <input type="checkbox" checked={bundleHedge} onChange={(e) => setBundleHedge(e.target.checked)} />
                  <span>Bundle hedge: <b className="mono">{hedgeSide} {hedgeQty}× 6E</b> → Δ {gk$(hedgedDelta)}</span>
                </label>
              </div>
            )}
      </div>

      {/* ACTIONS */}
      <div className="builder-actions">
        {stage === "build" && (
          <button className="btn-preview" disabled={busy} onClick={onPreview}>
            {busy ? "Pricing…" : <>Preview pricing &amp; impact<span className="arr">→</span></>}
          </button>
        )}
        {stage === "preview" && (
          <div className="book-panel draft">
            <div className="book-head">
              <span className="draft-title"><span className="draft-doc" />Order ticket{bundleHedge ? " + hedge" : ""}</span>
              <span className="badge-paper" title="orders route to the IB paper account">PAPER ACCOUNT</span>
            </div>
            <div className="book-kv">
              <div><span>Structure</span><b>{side} {qty}× {product} {tenor}{isCal ? "/" + farTenor : ""}{!isFut && STRUCT[product]!.mode.includes("wing") ? " " + wingLevel : ""}</b></div>
              <div><span>Preview id</span><b className="mono dim">{server?.preview_id ?? "—"}</b></div>
              <div><span>State</span><b className={"mono " + stateCls}>{stateText}</b></div>
              <div><span>Net cash <em className="unit">indicative</em></span><b className={"mono " + (isCredit ? "pos" : "neg")}>{isCredit ? "+" : "−"}{fmt.usd(premiumAbs)} {isCredit ? "credit" : "debit"}</b></div>
              <div><span>Max loss</span>{naked ? <b className="mono neg">unbounded · stress {gk$(stressLoss)}</b> : <b className="mono">{fmt.usd(premiumAbs + commission)}</b>}</div>
              <div><span>{meta.order[0] === "vn" ? "Vanna (lead)" : "Lead greek"}</span><b className="mono">{GREEK_INFO[meta.order[0]!].label} {greekVal(meta.order[0]!)}</b></div>
              <div><span>Δ after hedge</span><b className="mono">{gk$(afterDelta)}</b></div>
            </div>
            {server?.blocking_reasons && server.blocking_reasons.length > 0 && (
              <div className="ob-blocking mono small">⛔ {server.blocking_reasons.join(" · ")}</div>
            )}
            {err && <div className="ob-error mono small">⚠ {err}</div>}
            <div className="book-btns">
              <button
                className="btn-draft-send"
                disabled={!canPlace}
                title={WRITE_ENABLED ? (canPlace ? "submit to IB paper account" : "preview must be valid_for_submit") : GATE_TITLE}
                onClick={onPlace}
              >
                {busy ? "Placing…" : "Place order"}
              </button>
              <button className="btn-draft-cancel" disabled={busy} onClick={reset}>Cancel</button>
            </div>
            {!WRITE_ENABLED && <div className="dim small ob-readonly-note">Read-only desk · placing orders requires auth (Phase 2).</div>}
          </div>
        )}
        {stage === "booked" && booked && (
          <div className="book-result">
            <div className="result-icon">✓</div>
            <div>
              <b>Order submitted</b>
              <span className="mono">
                {booked.side} {booked.qty}× {booked.product} {booked.tenor}
                {booked.bundleHedge ? " + " + booked.hedgeSide + " " + booked.hedgeQty + "× 6E" : ""} · IB paper account
                {typeof placed?.["structure_id"] === "number" ? " · #" + String(placed["structure_id"]) : ""}
              </span>
            </div>
            <button className="btn-ghost" onClick={reset}>New order</button>
          </div>
        )}
      </div>
    </div>
  );
}
