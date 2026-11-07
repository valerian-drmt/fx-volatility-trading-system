/**
 * One strike per delta pillar, anchored on the LIVE ATM (#379).
 *
 * The order builder mixed two strike scales: the Strike Δ buttons set a vanilla
 * strike relative to the live ATM, while the "Legs on the smile" ladder placed
 * each leg under the nearest MOCK pillar strike (the preview smile around
 * DATA.SPOT = 1.0842). With live spot ~1.12 every leg sat ~400 pips above the
 * highest mock pillar, so it always landed in the last column whatever button
 * was pressed. Everything now goes through `livePillarStrike`, and the ladder
 * column is the pillar whose live strike is nearest the leg.
 */
import { DATA } from "../data";

const TENORS = DATA.tenors;
const PILLARS = DATA.deltas;

/** CME EUR FOP strike grid. */
export const CME_STRIKE_STEP = 0.005;

export const gridStrike = (s: number): number =>
  +(Math.round(s / CME_STRIKE_STEP) * CME_STRIKE_STEP).toFixed(4);

/** Strike of `pillar` at `tenor` on the live market: the mock smile's OFFSET
 * from its own ATM (a shape, not a level) applied to the live ATM, snapped to
 * the CME grid. `mockStrike(tenor, pillar)` is the preview smile accessor. */
export function livePillarStrike(
  liveAtm: number,
  tenor: string,
  pillar: string,
  mockStrike: (tenor: string, pillar: string) => number,
): number {
  if (pillar === "ATM") return gridStrike(liveAtm);
  return gridStrike(liveAtm + (mockStrike(tenor, pillar) - mockStrike(tenor, "ATM")));
}

/** Index of the ladder column a leg at `strike` belongs to: the pillar whose
 * strike (from the SAME resolver the buttons use) is nearest. */
export function ladderColumn(
  strike: number,
  tenor: string,
  pillars: readonly string[],
  strikeAt: (tenor: string, pillar: string) => number,
): number {
  let best = 0;
  let bestD = Infinity;
  pillars.forEach((p, i) => {
    const d = Math.abs(strikeAt(tenor, p) - strike);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  });
  return best;
}

export interface Leg {
  instrument: string;
  type: string;
  side: string;
  qty: number;
  strike: number;
  tenor: string;
  iv: number;
  prem: number;
}

export function tenorIdx(t: string): number {
  return Math.max(0, TENORS.indexOf(t));
}
export function pillarStrike(t: string, p: string): number {
  const s = DATA.smileFor(tenorIdx(t));
  const j = PILLARS.indexOf(p);
  return s.pts[j] ? s.pts[j]!.strike : DATA.SPOT;
}
export function ivAt(t: string, strike: number): number {
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

/** Where a leg's strike and preview IV come from. The component passes the
 * LIVE-anchored resolver (#379); the mock one is the default for tests. */
export interface StrikeResolver {
  strike: (tenor: string, pillar: string) => number;
  iv: (tenor: string, strike: number) => number;
  spot: number;
}
export const MOCK_RESOLVER: StrikeResolver = { strike: pillarStrike, iv: ivAt, spot: DATA.SPOT };

export function buildLegs(
  product: string,
  side: string,
  tenor: string,
  farTenor: string,
  strike: number,
  qty: number,
  wing: string,
  spreadSide: string,
  spreadNear: string,
  spreadFar: string,
  r: StrikeResolver = MOCK_RESOLVER,
): Leg[] {
  // `wing` is a full delta pillar ("25Δc" / "ATM"); symmetric wings use its level
  // ("25Δ"), single-strike structures (straddle/calendar) use the pillar itself.
  const atm = +r.strike(tenor, "ATM").toFixed(4);
  const level = wing === "ATM" ? "ATM" : wing.replace(/[pc]$/, "");
  const wc = level === "ATM" ? atm : +r.strike(tenor, level + "c").toFixed(4);
  const wp = level === "ATM" ? atm : +r.strike(tenor, level + "p").toFixed(4);
  const sharedK = wing === "ATM" ? atm : +r.strike(tenor, wing).toFixed(4);
  // a vertical spread needs width : an ATM strike-Δ would collapse both legs onto
  // ATM (zero greeks) → fall back to the 25Δ wing so the spread is always real.
  const scStrike = level === "ATM" ? +r.strike(tenor, "25Δc").toFixed(4) : wc;
  const spStrike = level === "ATM" ? +r.strike(tenor, "25Δp").toFixed(4) : wp;
  const opp = side === "BUY" ? "SELL" : "BUY";
  const mk = (typ: string, sd: string, k: number, ten: string, q?: number): Leg => {
    const iv = r.iv(ten, k);
    const prem = r.spot * (iv / 100) * Math.sqrt((tenorIdx(ten) + 1) / 12) * 0.42;
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
    case "Call/Put Spread": {
      // 2 same-type legs on one wing : near = long (higher |Δ|, closer to F),
      // far = short (lower |Δ|). Side swaps long/short (debit ⇄ credit).
      const suf = spreadSide === "call" ? "c" : "p";
      const typ = spreadSide === "call" ? "Call" : "Put";
      const kFor = (lvl: string): number =>
        lvl === "ATM" ? atm : +r.strike(tenor, lvl + suf).toFixed(4);
      return [mk(typ, side, kFor(spreadNear), tenor), mk(typ, opp, kFor(spreadFar), tenor)];
    }
    case "Calendar":
      return [mk("Call", opp, sharedK, tenor), mk("Call", side, sharedK, farTenor)];
    case "Future":
      return [{ instrument: "EURUSD", type: "Future", side, qty, strike: r.spot, tenor: "Sep26", iv: 0, prem: 0 }];
    default:
      return [];
  }
}
