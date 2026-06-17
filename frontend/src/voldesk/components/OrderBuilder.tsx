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
import { DATA, DATA2, fmt } from "../data";
import { useDeskData } from "../data/deskData";

const PRODUCTS = ["Vanilla Call", "Vanilla Put", "Straddle", "Strangle", "Butterfly", "Risk Reversal", "Calendar", "Future"];
const TENORS = DATA.tenors;
const PILLARS = DATA.deltas;
const CONTRACTS: Record<string, number> = { "6E (€125k)": 125000, "M6E (€12.5k)": 12500 };
const DELTA_PER_6E = 1250; // $ delta flattened per 1 contract of 6E (1 big-fig)

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

interface BookRow {
  k: string;
  unit: string;
  b: number;
  a: number;
  f: (v: number) => string;
  lead?: boolean;
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
  const atm = +pillarStrike(tenor, "ATM").toFixed(4);
  const wc = +pillarStrike(tenor, wing + "c").toFixed(4);
  const wp = +pillarStrike(tenor, wing + "p").toFixed(4);
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
      return [mk("Call", side, atm, tenor), mk("Put", side, atm, tenor)];
    case "Strangle":
      return [mk("Put", side, wp, tenor), mk("Call", side, wc, tenor)];
    case "Butterfly":
      return [mk("Call", side, wc, tenor), mk("Call", opp, atm, tenor, qty * 2), mk("Put", side, wp, tenor)];
    case "Risk Reversal":
      return [mk("Call", side, wc, tenor), mk("Put", opp, wp, tenor)];
    case "Calendar":
      return [mk("Call", opp, atm, tenor), mk("Call", side, atm, farTenor)];
    case "Future":
      return [{ instrument: "EURUSD", type: "Future", side, qty, strike: DATA.SPOT, tenor: "Sep26", iv: 0, prem: 0 }];
    default:
      return [];
  }
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
  const { ticks } = useDeskData();
  const [product, setProduct] = useState("Risk Reversal");
  const [side, setSide] = useState("BUY");
  const [tenor, setTenor] = useState("2M");
  const [farTenor, setFarTenor] = useState("4M");
  const [strike, setStrike] = useState(1.0842);
  const [wing, setWing] = useState("25Δ");
  const [qty, setQty] = useState(25);
  const [csize, setCsize] = useState("6E (€125k)");
  const [bundleHedge, setBundleHedge] = useState(false);
  const [stage, setStage] = useState("build"); // build | preview | booked
  const [booked, setBooked] = useState<Booked | null>(null);
  const reset = (): void => setStage("build");

  useEffect(() => {
    if (prefill) {
      setProduct(prefill.product);
      setSide(prefill.side || "BUY");
      if (prefill.tenor) setTenor(prefill.tenor);
      if (prefill.farTenor) setFarTenor(prefill.farTenor);
      reset();
    }
  }, [prefill]);

  const mult = CONTRACTS[csize]!;
  const meta = STRUCT[product]!;
  const isFut = product === "Future";
  const isCal = product === "Calendar";

  const legs = useMemo(() => buildLegs(product, side, tenor, farTenor, strike, qty, wing), [product, side, tenor, farTenor, strike, qty, wing]);
  const net = useMemo(() => previewGreeks(legs, mult), [legs, mult]);

  const commission = Math.round(legs.length * qty * 2.1);
  const isCredit = net.cost < 0; // SELL-heavy structures take in premium
  const premiumAbs = Math.abs(net.cost);
  const naked = meta.naked(side); // a sold leg with no long cover → unbounded tail
  const hedgeQty = Math.round(Math.abs(net.d) / DELTA_PER_6E);
  const hedgeSide = net.d > 0 ? "SELL" : "BUY";
  const hedgedDelta = Math.round(net.d - Math.sign(net.d) * hedgeQty * DELTA_PER_6E); // delta once the bundle is applied
  const afterDelta = bundleHedge ? hedgedDelta : net.d;

  // ---- pre-trade book impact (§2): single-engine net + structure → after ----
  const g = DATA.greeks;
  const VAR_TOTAL = Math.abs(g.var1d99); // $312k 1d/99
  const skewBefore = Math.abs((DATA2.varFactors.find((f) => f.key === "skew") || { v: 0 }).v || 0); // 106k
  const addedSkewVar = Math.round(Math.abs(net.vn) * 0.6);
  const marginalVar = Math.round(addedSkewVar + (Math.abs(net.v) / 1000) * 1.1 + (Math.abs(afterDelta) / 1000) * 0.35);
  const varAfter = VAR_TOTAL + marginalVar;
  const skewPctBefore = (skewBefore / VAR_TOTAL) * 100;
  const skewPctAfter = ((skewBefore + addedSkewVar) / varAfter) * 100;
  const book: BookRow[] = [
    { k: "Δ", unit: "$", b: g.netDelta, a: g.netDelta + afterDelta, f: gk$ },
    { k: "Γ", unit: "$/pip", b: g.netGamma, a: g.netGamma + net.g, f: gk$ },
    { k: "Vega", unit: "$/vp", b: g.netVega, a: g.netVega + net.v, f: gk$ },
    { k: "Vanna", unit: "$k/vp·fig", b: g.netVanna, a: Math.round(g.netVanna + net.vn), f: (x) => fmt.sgn(x, 0) + "k", ...(meta.skew ? { lead: true } : {}) },
    { k: "Volga", unit: "$k/vp", b: g.netVolga, a: Math.round(g.netVolga + net.vg), f: (x) => fmt.sgn(x, 0) + "k" },
  ];

  // structure-relevant greek read order (§1)
  const greekVal = (key: GreekKey): string =>
    ({ d: gk$(net.d), g: gk$(net.g), v: gk$(net.v), t: gk$(net.t), vn: fmt.sgn(net.vn, 1) + "k", vg: fmt.sgn(net.vg, 1) + "k" })[key];
  const greekRaw = (key: GreekKey): number => ({ d: net.d, g: net.g, v: net.v, t: net.t, vn: net.vn, vg: net.vg })[key];

  // stress-truth tail figure for naked structures — read this, not a false finite max-loss
  const shortNotionalEur = qty * mult;
  const stressLoss = -Math.round(shortNotionalEur * 400 * 0.0001 * 0.78 + Math.abs(net.v) * 4);

  const wc = +pillarStrike(tenor, wing + "c").toFixed(4);
  const wp = +pillarStrike(tenor, wing + "p").toFixed(4);
  const atmK = +pillarStrike(tenor, "ATM").toFixed(4);

  // report the live structure up so the Indicators pre-trade check reads the SAME engine (state, not signal)
  useEffect(() => {
    if (onState) onState({ active: stage === "preview", product, side, tenor, farTenor, qty, isCal, isFut, net, naked });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, product, side, tenor, farTenor, qty, isCal, isFut, net, naked]);

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

      {/* INPUTS (green) — product-driven (§6) */}
      <div className="builder-block block-in">
        <div className="block-tag">INPUTS</div>
        <div className="ob-side">
          <button className={"side-btn buy " + (side === "BUY" ? "on" : "")} onClick={() => { setSide("BUY"); reset(); }}>BUY</button>
          <button className={"side-btn sell " + (side === "SELL" ? "on" : "")} onClick={() => { setSide("SELL"); reset(); }}>SELL</button>
        </div>
        <label className="field"><span>Product</span>
          <select value={product} onChange={(e) => { setProduct(e.target.value); reset(); }}>
            {PRODUCTS.map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>

        {/* tenor(s) — calendar exposes two expiries */}
        <div className="field-row">
          <label className="field"><span>{isCal ? "Near tenor" : "Tenor"}</span>
            <select value={tenor} onChange={(e) => { setTenor(e.target.value); reset(); }}>{TENORS.map((t) => <option key={t}>{t}</option>)}</select>
          </label>
          {isCal && <label className="field"><span>Far tenor</span>
            <select value={farTenor} onChange={(e) => { setFarTenor(e.target.value); reset(); }}>{TENORS.map((t) => <option key={t}>{t}</option>)}</select>
          </label>}
        </div>

        {/* strike control — driven by the structure, not a single ATM input (§6) */}
        {meta.mode === "single" && (
          <>
            <label className="field"><span>Strike <em className="unit">USD</em></span>
              <div className="field-input"><input type="number" step="0.0001" value={strike} onChange={(e) => { setStrike(+e.target.value); reset(); }} /><em>USD</em></div>
            </label>
            <div className="pillars-wrap">
              <div className="pillars-lbl mono">snap to pillar · discretionary override</div>
              <div className="pillars">
                {PILLARS.map((p) => <button key={p} className="pillar" onClick={() => { setStrike(+pillarStrike(tenor, p).toFixed(4)); reset(); }}>{p}</button>)}
              </div>
            </div>
          </>
        )}
        {meta.mode === "atm" && (
          <div className="strike-driven">
            <div className="sd-head"><span>Strikes</span><span className="dim mono small">straddle → ATM pillar</span></div>
            <div className="leg-strikes"><span className="lstrike">Call <b className="mono">{atmK.toFixed(4)}</b></span><span className="lstrike">Put <b className="mono">{atmK.toFixed(4)}</b></span><span className="pillar-lock mono">ATM</span></div>
          </div>
        )}
        {(meta.mode === "wing" || meta.mode === "flywing") && (
          <div className="strike-driven">
            <div className="sd-head"><span>Wings</span><span className="dim mono small">{product === "Risk Reversal" ? "RR → Δ-paired call / put" : meta.mode === "flywing" ? "fly → wings + ATM body" : "strangle → Δ-paired"}</span></div>
            <div className="ob-side small wing-sel">
              {["25Δ", "10Δ"].map((w) => <button key={w} className={"side-btn " + (wing === w ? "on" : "")} onClick={() => { setWing(w); reset(); }}>{w}</button>)}
            </div>
            <div className="leg-strikes">
              <span className="lstrike">Call <b className="mono">{wc.toFixed(4)}</b><em>{wing}c</em></span>
              {meta.mode === "flywing" && <span className="lstrike">Body <b className="mono">{atmK.toFixed(4)}</b><em>ATM ×2</em></span>}
              <span className="lstrike">Put <b className="mono">{wp.toFixed(4)}</b><em>{wing}p</em></span>
            </div>
          </div>
        )}
        {meta.mode === "cal" && (
          <div className="strike-driven">
            <div className="sd-head"><span>Strikes</span><span className="dim mono small">calendar → shared ATM, 2 expiries</span></div>
            <div className="leg-strikes"><span className="lstrike">{tenor} <b className="mono">{atmK.toFixed(4)}</b></span><span className="lstrike">{farTenor} <b className="mono">{atmK.toFixed(4)}</b></span></div>
          </div>
        )}

        <div className="field-row">
          <label className="field"><span>Size <em className="unit">contracts</em></span>
            <div className="field-input"><input type="number" value={qty} onChange={(e) => { setQty(+e.target.value); reset(); }} /><em>ct</em></div>
          </label>
          <label className="field"><span>Contract</span>
            <select value={csize} onChange={(e) => { setCsize(e.target.value); reset(); }}>{Object.keys(CONTRACTS).map((c) => <option key={c}>{c}</option>)}</select>
          </label>
        </div>
      </div>

      {/* EXPOSURE REFERENCE — what the closed set expresses (reference, not a recommendation) */}
      <div className="exposure-ref">
        <div className="exp-ref-head"><span>Exposure reference</span><span className="dim mono small">closed set</span></div>
        <div className="exp-ref-list">
          <div className="exp-ref-row"><b>Straddle</b><span className="dim">vega · level (PC1)</span></div>
          <div className="exp-ref-row"><b>Calendar</b><span className="dim">vega-slope · theta (PC2)</span></div>
          <div className="exp-ref-row"><b>Butterfly</b><span className="dim">volga · curvature (PC3)</span></div>
          <div className="exp-ref-row"><b>Risk Reversal</b><span className="dim warn">vanna · skew — off by default</span></div>
        </div>
        <div className="dim small exp-ref-note">RR is risk-only: the desk does not signal-trade skew.</div>
      </div>

      {/* MARKET (yellow) */}
      <div className="builder-block block-mkt">
        <div className="block-tag">MARKET</div>
        <div className="mkt-rows">
          <div><span>Spot bid/ask</span><b className="mono">{(ticks.data?.bid ?? DATA.SPOT - 0.0001).toFixed(5)}/{(ticks.data?.ask ?? DATA.SPOT + 0.0001).toFixed(5)}</b></div>
          <div><span>ATM curve age</span><b className="mono warn">38s</b></div>
          <div><span>ATM IV {tenor}</span><b className="mono">{DATA.ivSurface[tenorIdx(tenor)]![2]!.toFixed(1)}%</b></div>
          <div><span>Fwd {tenor}</span><b className="mono">{DATA.smileFor(tenorIdx(tenor)).fwd.toFixed(4)}</b></div>
        </div>
      </div>

      {/* OUTPUTS (red) */}
      <div className={"builder-block block-out " + (stage === "build" ? "muted" : "")}>
        <div className="block-tag">OUTPUTS</div>
        {stage === "build" ? (
          <div className="out-empty">Run preview to price the structure & see book impact</div>
        ) : (
          <>
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

            {/* §1 — greeks ordered by relevance to the structure; the lead greek is the one that matters */}
            {!isFut && <>
              <div className="greek-read-lbl mono">structure greeks <span className="dim">· ordered by relevance · {GREEK_INFO[meta.order[0]!].name} leads</span></div>
              <div className="greek-read">
                {meta.order.map((key, i) => (
                  <div key={key} className={"greek-cell " + (i === 0 ? "lead" : "") + " " + pnlCls(greekRaw(key))}>
                    <span className="gc-lbl">{GREEK_INFO[key].label}</span>
                    <b className="gc-val mono">{greekVal(key)}</b>
                    <span className="gc-unit mono">{GREEK_INFO[key].unit}</span>
                  </div>
                ))}
              </div>
            </>}

            {/* §3 — skew flag for RR (risk-only) */}
            {meta.skew && (
              <div className="skew-flag">
                <span className="flag-dot" />
                <div><b>Adds skew · no signal</b><span className="dim"> — tracked as incident risk, not a traded mode. Vanna added <b className="mono warn">{fmt.sgn(net.vn, 0)}k</b> → skew share {skewPctBefore.toFixed(0)}% → <b className="mono">{skewPctAfter.toFixed(0)}%</b> of VaR.</span></div>
              </div>
            )}

            {/* §5 — premium, credit/debit explicit, reconciles to Σ leg prem */}
            <div className="cost-rows">
              <div><span>Net premium <em className="unit">Σ legs</em></span><b className={"mono " + (isCredit ? "pos" : "neg")}>{isCredit ? "+" : "−"}{fmt.usd(premiumAbs)} <em className="paytag">{isCredit ? "credit" : "debit"}</em></b></div>
              <div><span>Commission</span><b className="mono neg">−{fmt.usd(commission)} <em className="paytag">debit</em></b></div>
              <div className="cost-total"><span>Net cash</span><b className={"mono " + ((isCredit ? premiumAbs : -premiumAbs) - commission >= 0 ? "pos" : "neg")}>{gk$((isCredit ? premiumAbs : -premiumAbs) - commission)}</b></div>
            </div>

            {/* §0 — MAX LOSS SAFETY: never a finite max-loss when a sold leg is uncovered */}
            <div className={"maxloss " + (naked ? "unbounded" : "bounded")}>
              {naked ? (
                <>
                  <div className="ml-head"><span className="ml-badge mono">⚠ TAIL RISK · UNBOUNDED</span><span className="dim small">sold leg uncovered</span></div>
                  <div className="ml-row"><span>Max loss</span><b className="neg">unbounded · see stress</b></div>
                  <div className="ml-row"><span>Stress −400bp / +6vp</span><b className="mono neg">{gk$(stressLoss)}</b></div>
                  <div className="ml-row"><span>Premium at risk</span><b className="mono dim">{fmt.usd(premiumAbs)} <em className="paytag">{isCredit ? "credit" : "debit"} — not the max loss</em></b></div>
                </>
              ) : (
                <>
                  <div className="ml-row"><span>Max loss <em className="unit">debit structure</em></span><b className="mono">{fmt.usd(premiumAbs + commission)}</b></div>
                  <div className="ml-row dim small">bounded — long premium, no naked sold leg</div>
                </>
              )}
            </div>

            {/* §4 — delta hedge, bundleable */}
            {!isFut && (
              <div className="hedge-bundle">
                <div className="hb-row"><span>Δ unhedged</span><b className={"mono " + pnlCls(net.d)}>{gk$(net.d)}</b></div>
                <label className="hb-toggle">
                  <input type="checkbox" checked={bundleHedge} onChange={(e) => setBundleHedge(e.target.checked)} />
                  <span>Bundle hedge: <b className="mono">{hedgeSide} {hedgeQty}× 6E</b> → Δ {gk$(hedgedDelta)}</span>
                </label>
              </div>
            )}

            {/* §2 — pre-trade book impact (symmetric with Close), from the single greeks engine */}
            <div className="book-impact">
              <div className="bi-head"><span>Book impact <em className="unit">before → after</em></span><span className="dim mono small">PC1/2/3 + skew-incident base</span></div>
              <table className="dt bi-table">
                <thead><tr><th className="l">Net greek</th><th className="r">Before</th><th className="r after-col">After</th></tr></thead>
                <tbody>
                  {book.map((r) => (
                    <tr key={r.k} className={r.lead ? "bi-lead" : ""}>
                      <td className="l">{r.k} <em className="unit mono">{r.unit}</em></td>
                      <td className={"r mono " + pnlCls(r.b)}>{r.f(r.b)}</td>
                      <td className={"r mono after-col " + pnlCls(r.a)}>{r.f(r.a)}</td>
                    </tr>
                  ))}
                  <tr className="bi-var">
                    <td className="l">VaR 1d/99 <em className="unit mono">$k</em></td>
                    <td className="r mono neg">-${VAR_TOTAL}k</td>
                    <td className="r mono neg after-col">-${varAfter}k <span className="bi-marg mono">(marg {gk$(-marginalVar * 1000)})</span></td>
                  </tr>
                </tbody>
              </table>
              <div className="bi-skew dim small mono">skew factor {skewPctBefore.toFixed(0)}% → <b className={skewPctAfter > skewPctBefore + 1 ? "warn" : ""}>{skewPctAfter.toFixed(0)}%</b> of VaR{meta.skew ? " · RR is the book's #1 factor" : ""}</div>
            </div>
          </>
        )}
      </div>

      {/* ACTIONS */}
      <div className="builder-actions">
        {stage === "build" && <button className="btn-preview" onClick={() => setStage("preview")}>Preview pricing & impact<span className="arr">→</span></button>}
        {stage === "preview" && (
          <div className="book-panel draft">
            <div className="book-head"><span className="draft-title"><span className="draft-doc" />Order draft{bundleHedge ? " + hedge" : ""}</span><span className="badge-paper">PAPER</span></div>
            <div className="book-kv">
              <div><span>Structure</span><b>{side} {qty}× {product} {tenor}{isCal ? "/" + farTenor : ""}{!isFut && STRUCT[product]!.mode.includes("wing") ? " " + wing : ""}</b></div>
              <div><span>Preview id</span><b className="mono dim">tp_9425a798e686</b></div>
              <div><span>State</span><b className="mono pos">valid_for_submit</b></div>
              <div><span>Net cash</span><b className={"mono " + (isCredit ? "pos" : "neg")}>{isCredit ? "+" : "−"}{fmt.usd(premiumAbs)} {isCredit ? "credit" : "debit"}</b></div>
              <div><span>Max loss</span>{naked ? <b className="mono neg">unbounded · stress {gk$(stressLoss)}</b> : <b className="mono">{fmt.usd(premiumAbs + commission)}</b>}</div>
              <div><span>{meta.order[0] === "vn" ? "Vanna (lead)" : "Lead greek"}</span><b className="mono">{GREEK_INFO[meta.order[0]!].label} {greekVal(meta.order[0]!)}</b></div>
              <div><span>Δ after hedge</span><b className="mono">{gk$(afterDelta)}</b></div>
            </div>
            <div className="book-btns">
              <button className="btn-draft-send" onClick={() => { setBooked({ side, product, qty, tenor, bundleHedge, hedgeQty, hedgeSide }); setStage("booked"); }}>Send{bundleHedge ? " bundle" : ""}</button>
              <button className="btn-draft-cancel" onClick={reset}>Cancel</button>
            </div>
          </div>
        )}
        {stage === "booked" && booked && (
          <div className="book-result">
            <div className="result-icon">✓</div>
            <div><b>Order sent</b><span className="mono">{booked.side} {booked.qty}× {booked.product} {booked.tenor}{booked.bundleHedge ? " + " + booked.hedgeSide + " " + booked.hedgeQty + "× 6E" : ""} · PAPER · filled @ market</span></div>
            <button className="btn-ghost" onClick={reset}>New order</button>
          </div>
        )}
      </div>
    </div>
  );
}
