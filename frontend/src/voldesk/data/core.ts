/**
 * VOLDESK static desk constants + domain types (EURUSD).
 *
 * What remains of the original mock corpus after the live wiring (R11) and the
 * fabricated-fallback purge (remediation 05 WI-2):
 *   - `fmt` display formatters,
 *   - axis/pillar constants (`tenors`, `deltas`) and the reference `SPOT` used
 *     ONLY by the OrderBuilder's client-side pricing preview (labeled as such),
 *   - the smile-preview generator (`smileFor`) + `strikeToWing` bucketing,
 *   - the domain type exports shared with the live adapters.
 * All synthetic *book* data (positions / account / greeks / cash / events…) is
 * gone: views render honest empty states when the backend has no data.
 */

export const fmt = {
  px: (v: number, d = 4): string =>
    v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }),
  usd: (v: number, d = 0): string =>
    (v < 0 ? "-$" : "$") +
    Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }),
  usdk: (v: number): string => {
    const a = Math.abs(v);
    const s = v < 0 ? "-" : "+";
    if (a >= 1e6) return s + "$" + (a / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return s + "$" + (a / 1e3).toFixed(1) + "k";
    return s + "$" + a.toFixed(0);
  },
  sgn: (v: number, d = 2): string => (v >= 0 ? "+" : "") + v.toFixed(d),
  pct: (v: number, d = 2): string => (v >= 0 ? "+" : "") + v.toFixed(d) + "%",
  num: (v: number, d = 2): string =>
    v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }),
};

/** Reference spot for the OrderBuilder preview maths (NOT live market data). */
export const SPOT = 1.0842;
// Cash dollar-delta scale (notional × spot), matching the backend `delta_usd`.
const DELTA_CASH = SPOT * 100;
export const tenors = ["1M", "2M", "3M", "4M", "5M", "6M"];
export const deltas = ["10Δp", "25Δp", "ATM", "25Δc", "10Δc"];

// Reference IV grid [tenor][delta] — drives the OrderBuilder smile preview only.
export const ivSurface: number[][] = [
  [7.9, 6.4, 5.3, 6.2, 7.6],
  [7.6, 6.2, 5.4, 6.1, 7.3],
  [7.4, 6.1, 5.6, 6.0, 7.1],
  [7.3, 6.0, 5.8, 6.0, 7.0],
  [7.2, 6.0, 6.0, 6.1, 7.0],
  [7.1, 6.0, 6.1, 6.1, 6.9],
];

export interface TermPoint {
  tenor: string;
  atm: number;
  fair: number;
  rv: number;
  bf25: number;
  bf10: number;
  rr25: number;
  rr10: number;
}

// Reference term structure backing the smile preview (fair/rv columns).
// Non-null assertions: tenors / ivSurface are fixed-length, indices always valid.
export const termStructure: TermPoint[] = tenors.map((t, i) => {
  const atm = ivSurface[i]![2]!;
  return {
    tenor: t,
    atm,
    fair: atm + [0.5, 0.35, 0.2, 0.1, 0.0, -0.05][i]!,
    rv: atm - [0.9, 0.7, 0.5, 0.4, 0.35, 0.3][i]!,
    bf25: [0.125, 0.15, 0.15, 0.18, 0.2, 0.225][i]!,
    bf10: [0.35, 0.45, 0.5, 0.6, 0.68, 0.75][i]!,
    rr25: [-0.4, -0.5, -0.6, -0.65, -0.7, -0.75][i]!,
    rr10: [-0.6, -0.85, -0.95, -1.1, -1.22, -1.35][i]!,
  };
});

export interface SmilePoint {
  strike: number;
  iv: number;
  label: string;
  skew: number;
}
export interface Smile {
  pts: SmilePoint[];
  fit: { k: number; iv: number }[];
  fair: number;
  rv: number;
  fwd: number;
}

// smile points per tenor (strike, iv, delta_label) + svi fair + rv
export function smileFor(tenorIdx: number): Smile {
  const row = ivSurface[tenorIdx]!;
  const atm = row[2]!;
  const fwd = SPOT * (1 + 0.0008 * (tenorIdx + 1));
  const strikes = [fwd * 0.972, fwd * 0.986, fwd, fwd * 1.014, fwd * 1.028];
  const pts: SmilePoint[] = row.map((iv, j) => ({ strike: strikes[j]!, iv, label: deltas[j]!, skew: iv - atm }));
  const fit: { k: number; iv: number }[] = [];
  for (let k = 0; k <= 40; k++) {
    const x = -0.06 + (0.12 * k) / 40; // log-moneyness
    const a = atm / 100,
      b = 0.9,
      rho = -0.18 - tenorIdx * 0.02,
      m = 0.002,
      sig = 0.045;
    const w = a + b * (rho * (x - m) + Math.sqrt((x - m) * (x - m) + sig * sig));
    fit.push({ k: fwd * Math.exp(x), iv: w * 100 });
  }
  const tp = termStructure[tenorIdx]!;
  return { pts, fit, fair: tp.fair, rv: tp.rv, fwd };
}

// Bucket a raw strike into its nearest smile pillar for a tenor, returning the
// wing tag ("ATM" / "25Δ" / "10Δ") — the trader-facing name for a leg instead of
// a raw calibrated strike like 1.15068…. null when there's no usable strike.
export function strikeToWing(strike: number | null | undefined, tenor: string): string | null {
  if (!strike || strike <= 0) return null;
  const s = smileFor(Math.max(0, tenors.indexOf(tenor)));
  let best = 0, bestD = Infinity;
  for (let j = 0; j < deltas.length; j++) {
    const k = s.pts[j] ? s.pts[j]!.strike : SPOT;
    const d = Math.abs(k - strike);
    if (d < bestD) { bestD = d; best = j; }
  }
  const pillar = deltas[best]!;
  return pillar === "ATM" ? "ATM" : pillar.replace(/[pc]$/, ""); // "25Δc" → "25Δ"
}

/** PCA mode card shape (filled by the live adapter, `live/pca.ts`). */
export interface Pc {
  id: string;
  name: string;
  desc: string;
  z: number;
  pctile: number;
  label: string;
  variance: number;
  stable: boolean;
  tier: number;
  dataQuality: string;
  thr: number;
  load: number[][];
  extra: { convex_z: number } | null;
}

// Display-config statics for the PCA model meta (window sizes, refit cadence…).
// The live adapter overlays the model-derived fields (variance, eigen, obs).
export const pcaModel = {
  variance: { pc1: 97.2, pc2: 1.2, pc3: 0.8, cumul: 99.3 },
  coherence: "aligned",
  coherenceNote: "no contradictions across PCs",
  pcaWindow: "3M hourly",
  pcaObs: 1500,
  refit: "daily",
  zWindow: "1M hourly",
  zObs: 700,
  display: "3M daily",
  displayPts: 65,
  zoomPts: 120,
  stable: true,
  dims: 30,
  shrinkage: 0.35,
  eigen: { lambda: [97.2, 1.2, 0.8], gap23: 0.4, ratio23: 1.5, state: "narrow", note: "PC2/PC3 identities may rotate on refit" },
};
/** Active-model meta shape — reused by the live adapter (R11). */
export type PcaModelMeta = typeof pcaModel;

export interface MacroEvent {
  date: string;
  country: string;
  impact: string;
  in: string;
  code: string;
  content: string;
  src: string;
}

export interface Position {
  id: string;
  packageId: string;
  tradeId: string;
  conId: number;
  product: string;
  structure: string;
  side: string;
  qty: number;
  tenor: string;
  expiry: string;
  strike: number;
  entry: number;
  mark: number;
  iv: number;
  pnl: number;
  nominal: number;
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  vanna: number;
  volga: number;
  updated: string;
  opened: string;
  pnlPct: number;
  dte: number;
  // Open per the book but netted-away at IB (no mirror row) → can't be closed by
  // the per-contract path; the individual Close is disabled (use the trade close).
  netted?: boolean;
}

export interface Cash {
  ccy: string;
  settled: number;
  unsettled: number;
  rate: number;
  usd: number;
}

/** IB account snapshot shape (filled by the live adapters, zeros when absent). */
export interface AccountState {
  netLiq: number;
  dNetLiq: number;
  cash: number;
  dCash: number;
  unrealized: number;
  dayPnl: number;
  dayPnlPct: number;
  realized: number;
  marginInit: number;
  marginMaint: number;
  marginInitPct: number;
  marginMaintPct: number;
  excessLiq: number;
  cushion: number;
  nPositions: number;
  dPositions: number;
  buyingPower: number;
  availableFunds: number;
}

export interface Greeks {
  delta: number; gamma: number; theta: number; vega: number; vanna: number; volga: number;
  charm: number; var1d99: number; var1d95: number; beta: number;
  dDelta24h: number; dVega24h: number; dVanna24h: number; dVolga24h: number;
  netDelta: number; netGamma: number; netVega: number; netTheta: number;
  netVanna: number; netVolga: number; netNominal: number; netUnreal: number;
}

// Default risk caps — overridden per-key by /trade/limits when configured.
export const limits = {
  gamma: { cap: 20400, unit: "$/pip" },
  vega: { cap: 48000, unit: "$/vp" },
  vanna: { cap: 260, unit: "$k/vp·fig" },
  var99: { cap: 420, unit: "$k" },
  deltaBandUsd: Math.round(5000 * DELTA_CASH), // cash dollar-delta band
  skewVarPct: 20,
  // Live vega budget from risk config (config_scalar 'max_book_vega_usd'); 0
  // until the config row resolves — never a mock fallback.
  vegaCapUsd: 0,
};
export type Limits = typeof limits;

/** Preview/axis constants consumed as `DATA.*` (OrderBuilder, wing tags, axes). */
export const DATA = {
  SPOT, tenors, deltas, ivSurface, termStructure, smileFor, strikeToWing,
};
