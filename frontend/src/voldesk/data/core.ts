/**
 * VOLDESK mock data — core market/vol/pca/regime/positions/account (EURUSD).
 * Ported from the prototype's `js/data.jsx`. Synthetic only — this whole layer
 * is replaced by the typed OpenAPI client / WS hooks when each view is wired to
 * the backend (see frontend/Option Trading System/IMPLEMENTATION.md §5).
 */

export function mulberry32(a: number): () => number {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

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

export interface Candle {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export function genCandles(n: number, start: number, vol: number, seed: number, drift = 0): Candle[] {
  const rnd = mulberry32(seed);
  const out: Candle[] = [];
  let price = start;
  const t = Date.now() - n * 60000;
  for (let i = 0; i < n; i++) {
    const o = price;
    const shock = (rnd() - 0.5) * vol + drift;
    const c = Math.max(0.0001, o + shock);
    const hi = Math.max(o, c) + rnd() * vol * 0.7;
    const lo = Math.min(o, c) - rnd() * vol * 0.7;
    const v = (0.4 + rnd()) * (1 + Math.abs(shock) / vol);
    out.push({ t: t + i * 60000, o, h: hi, l: lo, c, v });
    price = c;
  }
  return out;
}

export const SPOT = 1.0842;
export const tenors = ["1M", "2M", "3M", "6M", "9M", "1Y"];
export const deltas = ["10Δp", "25Δp", "ATM", "25Δc", "10Δc"];

// vol surface IV [tenor][delta]
export const ivSurface: number[][] = [
  [7.9, 6.4, 5.3, 6.2, 7.6],
  [7.6, 6.2, 5.4, 6.1, 7.3],
  [7.4, 6.1, 5.6, 6.0, 7.1],
  [7.3, 6.0, 5.8, 6.0, 7.0],
  [7.2, 6.0, 6.0, 6.1, 7.0],
  [7.1, 6.0, 6.1, 6.1, 6.9],
];

// per-cell rich/cheap z-score (standardized vs rolling history) — the PCA read, by tenor × delta.
export const ivZ: number[][] = [
  [-2.2, -0.7, 0.1, -0.5, -1.9],
  [-1.5, -0.4, 0.3, -0.3, -1.1],
  [-0.6, 0.0, 0.5, 0.1, -0.3],
  [0.4, 0.3, 0.4, 0.4, 0.5],
  [1.0, 0.6, 0.2, 0.7, 1.2],
  [1.7, 1.0, 0.1, 1.1, 2.1],
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

// term structure: atm (mid), fair (GARCH forecast), rv (Yang-Zhang).
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

// PCA loadings
const pc1Load: number[][] = tenors.map((_, i) => deltas.map(() => (i === 5 ? 0.17 : 0.18)));
const pc2Load: number[][] = [
  [-0.28, -0.2, -0.21, -0.25, -0.33],
  [-0.13, -0.08, -0.09, -0.13, -0.21],
  [-0.03, 0.0, -0.02, -0.06, -0.14],
  [0.13, 0.08, 0.07, 0.02, -0.09],
  [0.32, 0.2, 0.16, 0.12, 0.04],
  [0.44, 0.23, 0.19, 0.15, 0.13],
];
const pc3Load: number[][] = [
  [0.08, -0.14, -0.16, -0.08, 0.07],
  [0.06, -0.09, -0.11, -0.04, 0.14],
  [0.07, -0.1, -0.09, -0.0, 0.23],
  [-0.08, -0.18, -0.16, -0.05, 0.24],
  [-0.02, -0.25, -0.24, -0.11, 0.33],
  [0.28, -0.13, -0.09, 0.08, 0.58],
];

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

export const pcs: Pc[] = [
  { id: "PC1", name: "level", desc: "surface up/down", z: -1.09, pctile: 13.8, label: "FAIR", variance: 97.2, stable: true, tier: 1, dataQuality: "clean", thr: 1.5, load: pc1Load, extra: null },
  { id: "PC2", name: "slope", desc: "front vs back (tenor)", z: 0.83, pctile: 79.8, label: "FAIR", variance: 1.2, stable: true, tier: 2, dataQuality: "clean", thr: 1.8, load: pc2Load, extra: null },
  { id: "PC3", name: "curvature", desc: "wings vs ATM (delta)", z: -2.15, pctile: 3.2, label: "CHEAP", variance: 0.8, stable: false, tier: 3, dataQuality: "noisy", thr: 2.0, load: pc3Load, extra: { convex_z: -2.15 } },
];

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

export interface RegimeFeature {
  name: string;
  value: number;
  z: number;
  pctile: number;
  bucket: string;
  dz: number;
  signal: string;
  ctx: string;
}

export const regime = {
  state: "calm",
  probs: { calm: 0.71, stressed: 0.06, pre_event: 0.23 },
  features: [
    { name: "vol_level", value: 5.28, z: -1.55, pctile: 0, bucket: "--", dz: -0.02, signal: "tail", ctx: "-1.55 ± 0.04 (NFP J0 london_open, n=31)" },
    { name: "vol_of_vol", value: 0.2, z: -0.14, pctile: 38, bucket: "0", dz: 0.01, signal: "noise", ctx: "-0.15 ± 0.00 (NFP J0 london_open, n=36)" },
    { name: "term_slope", value: 0.57, z: 1.12, pctile: 84, bucket: "0", dz: 0.26, signal: "weak", ctx: "+1.40 ± 0.19 (NFP J0 london_open, n=31)" },
  ] as RegimeFeature[],
  joint: { joint: "(--,0,0)", regime: "calm", dominant: "vol_level", vs_expected: "+0.00σ aligned" },
  vrp: tenors.map((t, i) => ({ tenor: t, vrp: [1.42, 1.18, 0.96, 0.81, 0.69, 0.58][i] })),
  gate: { allowed: true, reason: "regime calm · vol_level tail-cheap", size_mult: 1.0, dampener: 0.85 },
};

export interface MacroEvent {
  date: string;
  country: string;
  impact: string;
  in: string;
  code: string;
  content: string;
  src: string;
}

export const events: MacroEvent[] = [
  { date: "06/06/2026, 14:30", country: "US", impact: "high", in: "1d 2h", code: "NFP", content: "Non-Farm Payrolls", src: "FRED" },
  { date: "11/06/2026, 14:30", country: "US", impact: "high", in: "6d 2h", code: "CPI_US", content: "US CPI YoY", src: "FRED" },
  { date: "12/06/2026, 08:00", country: "GB", impact: "medium", in: "6d 20h", code: "GDP_GB", content: "UK GDP estimate", src: "ONS" },
  { date: "18/06/2026, 18:00", country: "US", impact: "high", in: "13d", code: "FOMC", content: "FOMC rate decision", src: "FOMC" },
  { date: "19/06/2026, 11:45", country: "EU", impact: "medium", in: "14d", code: "ECB_PR", content: "ECB press conference", src: "ECB" },
];

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
}

interface LegGreeks {
  d: number;
  g: number;
  v: number;
  t: number;
  vn: number;
  vg: number;
}
type LegSpec = [side: string, qty: number, tenor: string, expiry: string, strike: number, entry: number, mark: number, iv: number, g: LegGreeks];

let _tid = 88010;
let _cid = 712400;

function leg(pkgId: number, struct: string, spec: LegSpec): Position {
  const [side, qty, tenor, expiry, strike, entry, mark, iv, g] = spec;
  const conId = ++_cid;
  const mult = 125000; // 6E
  const dir = side === "BUY" ? 1 : -1;
  const pnl = (mark - entry) * dir * qty * mult * 0.0001 * 100;
  return {
    id: "L" + conId,
    packageId: "PKG-" + pkgId,
    tradeId: "T-" + _tid,
    conId,
    product: "EURUSD",
    structure: struct,
    side,
    qty,
    tenor,
    expiry,
    strike,
    entry,
    mark,
    iv,
    pnl,
    nominal: qty * 125000,
    delta: g.d,
    gamma: g.g,
    vega: g.v,
    theta: g.t,
    vanna: g.vn,
    volga: g.vg,
    updated: "12:04:31",
    opened: "03 Jun 09:12",
    pnlPct: 0,
    dte: 0,
  };
}

export const positions: Position[] = [];
let _pid = 2040;
function pkg(struct: string, legs: LegSpec[]): void {
  _pid++;
  _tid++;
  legs.forEach((l) => positions.push(leg(_pid, struct, l)));
}

pkg("Straddle ATM 1M", [
  ["BUY", 25, "1M", "04 Jul", 1.085, 0.00512, 0.00548, 5.3, { d: 4200, g: 1850, v: 920, t: -1180, vn: 42, vg: 18 }],
  ["BUY", 25, "1M", "04 Jul", 1.085, 0.00498, 0.00531, 5.3, { d: -4050, g: 1820, v: 910, t: -1160, vn: -38, vg: 17 }],
]);
pkg("Risk Reversal 25Δ 2M", [
  ["BUY", 30, "2M", "01 Aug", 1.101, 0.00231, 0.00268, 6.1, { d: 6100, g: 980, v: 1120, t: -640, vn: 88, vg: 9 }],
  ["SELL", 30, "2M", "01 Aug", 1.068, 0.00198, 0.00171, 6.2, { d: 3800, g: -720, v: -880, t: 510, vn: 64, vg: -7 }],
]);
pkg("Butterfly 25Δ 3M", [
  ["BUY", 20, "3M", "29 Aug", 1.108, 0.00142, 0.00159, 6.0, { d: 1900, g: 540, v: 610, t: -390, vn: 22, vg: 12 }],
  ["SELL", 40, "3M", "29 Aug", 1.0842, 0.00386, 0.00362, 5.6, { d: -200, g: -1180, v: -1240, t: 820, vn: -4, vg: -26 }],
  ["BUY", 20, "3M", "29 Aug", 1.06, 0.00121, 0.00138, 6.1, { d: -1500, g: 520, v: 590, t: -370, vn: -18, vg: 11 }],
]);
pkg("Calendar 1M/4M", [
  ["SELL", 15, "1M", "04 Jul", 1.085, 0.00301, 0.00279, 5.3, { d: -120, g: -940, v: -520, t: 690, vn: -3, vg: -8 }],
  ["BUY", 15, "4M", "26 Sep", 1.085, 0.00488, 0.00521, 5.8, { d: 140, g: 610, v: 1180, t: -410, vn: 5, vg: 14 }],
]);
positions.push({
  id: "F-6E-U6", packageId: "PKG-2045", tradeId: "T-88016", conId: 712499, product: "EURUSD",
  structure: "Future 6E Sep26", side: "SELL", qty: 8, tenor: "—", expiry: "15 Sep", strike: 0,
  entry: 1.0871, mark: 1.0842, iv: 0, pnl: 2900, nominal: 8 * 125000,
  delta: -10000, gamma: 0, vega: 0, theta: 0, vanna: 0, volga: 0, updated: "12:04:33", opened: "04 Jun 10:48",
  pnlPct: 0, dte: 0,
});
positions.forEach((p) => {
  p.pnlPct = p.entry ? ((p.mark - p.entry) / p.entry) * (p.side === "BUY" ? 1 : -1) * 100 : 0;
});

// days-to-expiry per leg (as-of 13 Jun 2026)
const DTE_BY_TENOR: Record<string, number> = { "1M": 21, "2M": 49, "3M": 77, "4M": 105, "5M": 133, "6M": 161 };
positions.forEach((p) => {
  p.dte = DTE_BY_TENOR[p.tenor] ?? 94;
});

export interface Cash {
  ccy: string;
  settled: number;
  unsettled: number;
  rate: number;
  usd: number;
}
export const cash: Cash[] = [
  { ccy: "USD", settled: 1284500, unsettled: -42000, rate: 1.0, usd: 0 },
  { ccy: "EUR", settled: 318200, unsettled: 0, rate: 1.0842, usd: 0 },
  { ccy: "GBP", settled: 96400, unsettled: 12000, rate: 1.2774, usd: 0 },
  { ccy: "JPY", settled: -8500000, unsettled: 0, rate: 0.00639, usd: 0 },
];
cash.forEach((c) => {
  c.usd = (c.settled + c.unsettled) * c.rate;
});

export const account = {
  netLiq: 4218640, dNetLiq: 0.92, cash: 1554900, dCash: -0.3,
  unrealized: 38420, dayPnl: 38420, dayPnlPct: 0.92, realized: 12180,
  marginInit: 1842000, marginMaint: 1284000, marginInitPct: 43.6, marginMaintPct: 30.4,
  excessLiq: 2934640, cushion: 0.696, nPositions: positions.length, dPositions: 2,
};

export interface Greeks {
  delta: number; gamma: number; theta: number; vega: number; vanna: number; volga: number;
  charm: number; var1d99: number; var1d95: number; beta: number;
  dDelta24h: number; dVega24h: number; dVanna24h: number; dVolga24h: number;
  netDelta: number; netGamma: number; netVega: number; netTheta: number;
  netVanna: number; netVolga: number; netNominal: number; netUnreal: number;
}

export const greeks: Greeks = {
  delta: 6.2, gamma: 14.5, theta: -9.1, vega: 32.0, vanna: 1.53, volga: 0.42, charm: -0.84,
  var1d99: -312, var1d95: -184, beta: 0.34, dDelta24h: 1.1, dVega24h: -3.4, dVanna24h: 0.31, dVolga24h: -0.12,
  netDelta: 0, netGamma: 0, netVega: 0, netTheta: 0, netVanna: 0, netVolga: 0, netNominal: 0, netUnreal: 0,
};

export const limits = {
  gamma: { cap: 20400, unit: "$/pip" },
  vega: { cap: 48000, unit: "$/vp" },
  vanna: { cap: 260, unit: "$k/vp·fig" },
  var99: { cap: 420, unit: "$k" },
  deltaBandUsd: 5000,
  skewVarPct: 20,
};
/** Inline-const shapes reused by the live Trade adapter (R11 PR 6r). */
export type AccountState = typeof account;
export type Limits = typeof limits;

export const feed = { feedS: 2, surfaceS: 38, feedWarn: 5, feedStale: 15, surfWarn: 45, surfStale: 60 };

export interface WorkingOrder {
  id: string;
  side: string;
  product: string;
  qty: number;
  level: string;
}
export const workingOrders: WorkingOrder[] = [
  { id: "wo-7741", side: "BUY", product: "Straddle 1M", qty: 15, level: "@ 5.1 vol limit" },
  { id: "wo-7742", side: "SELL", product: "6E Sep26", qty: 5, level: "@ 1.0905 stop" },
];

// ---- SINGLE GREEKS ENGINE: reconcile per-leg greeks so the book foots to the canonical net.
const GREEKS_NET = {
  delta: greeks.delta * 1000, gamma: greeks.gamma * 1000, vega: greeks.vega * 1000,
  theta: greeks.theta * 1000, vanna: 177, volga: 42,
};
(function reconcileBookGreeks(): void {
  const optLegs = positions.filter((p) => p.iv);
  const fut = positions.find((p) => !p.iv);
  const sum = (arr: Position[], k: keyof Position): number =>
    arr.reduce((s, p) => s + ((p[k] as number) || 0), 0);
  (["gamma", "vega", "theta", "vanna", "volga"] as const).forEach((k) => {
    const cur = sum(optLegs, k);
    if (!cur) return;
    const f = GREEKS_NET[k] / cur;
    optLegs.forEach((p) => {
      p[k] = +(p[k] * f).toFixed(k === "vanna" || k === "volga" ? 1 : 0);
    });
  });
  if (fut) fut.delta = Math.round(GREEKS_NET.delta - sum(optLegs, "delta"));
  greeks.netDelta = sum(positions, "delta");
  greeks.netGamma = sum(positions, "gamma");
  greeks.netVega = sum(positions, "vega");
  greeks.netTheta = sum(positions, "theta");
  greeks.netVanna = sum(positions, "vanna");
  greeks.netVolga = sum(positions, "volga");
  greeks.netNominal = sum(positions, "nominal");
  greeks.netUnreal = sum(positions, "pnl");
})();

export const equityCurve = (window_: string): number[] => {
  const map: Record<string, number> = { "1D": 48, "7D": 90, "30D": 120, "1Y": 160, all: 200 };
  const n = map[window_] || 90;
  const rnd = mulberry32(7 + n);
  let v = account.netLiq * 0.92;
  const a: number[] = [];
  for (let i = 0; i < n; i++) {
    v += (rnd() - 0.45) * account.netLiq * 0.004;
    a.push(v);
  }
  a[a.length - 1] = account.netLiq;
  return a;
};

export const watch = [{ sym: "EURUSD", last: SPOT, chg: 0.31 }];

export const DATA = {
  SPOT, tenors, deltas, ivSurface, ivZ, termStructure, smileFor, pcs, pcaModel, regime,
  events, positions, cash, account, greeks, limits, feed, workingOrders, equityCurve, watch,
};
