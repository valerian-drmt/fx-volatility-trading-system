/**
 * VOLDESK mock data — risk, scenarios, attribution, system, config.
 * Ported from the prototype's `js/data2.jsx`. Synthetic only (see core.ts header).
 */
import { DATA } from "./core";

// stress grid: ΔIV (-6..+6 vp) rows × ΔSpot (+200..-200 bp) cols → portfolio P&L USD
export const dSpot = [200, 100, 50, 0, -50, -100, -200];
export const dVol = [-6, -4, -2, 0, 2, 4, 6];
function stressPnl(spotBp: number, volVp: number): number {
  const delta = 6.2,
    gamma = 14.5,
    vega = 32.0;
  const sp = spotBp / 100;
  return Math.round(delta * spotBp * 0.9 + 0.5 * gamma * sp * sp * 1800 + vega * volVp * 1000 - Math.abs(spotBp) * 1.5);
}
export const stressGrid = dVol.map((v) => dSpot.map((s) => stressPnl(s, v)));

export const timeCols = ["M1", "M2", "M3", "M4", "M5", "M6"];
export const timeDays = [21, 42, 63, 84, 105, 126];
export const stressTimeCols = ["Now", "M1", "M2", "M3", "M4", "M5", "M6"];
export const stressTimeDays = [0, 21, 42, 63, 84, 105, 126];
function timePnl(spotBp: number, days: number): number {
  const delta = 6.2,
    gamma = 14.5,
    theta = -9.1,
    vega = 32.0;
  const sp = spotBp / 100;
  const spotPart = delta * spotBp * 0.9 + 0.5 * gamma * sp * sp * 1800;
  const thetaPart = theta * 1000 * (days / 21);
  const decayedVega = vega * Math.exp(-days / 160);
  const gammaScale = 1 - days / 320;
  return Math.round(spotPart * gammaScale + thetaPart + (decayedVega - vega) * 120);
}
export const timeGrid = dSpot.map((s) => stressTimeDays.map((d) => timePnl(s, d)));

// skew & fly stress axes
export const dRR = [-3, -2, -1, 0, 1, 2, 3];
export const dBF = [-1.5, -1, -0.5, 0, 0.5, 1, 1.5];
const NET_SKEW_VEGA = 18.4;
const NET_FLY_VEGA = 11.6;
function skewPnl(spotBp: number, rrVp: number): number {
  return Math.round(NET_SKEW_VEGA * rrVp * 1000 + DATA.greeks.vanna * spotBp * rrVp * 7 + 6.2 * spotBp * 0.4 - Math.abs(spotBp) * 1.2);
}
function flyPnl(spotBp: number, bfVp: number): number {
  return Math.round(NET_FLY_VEGA * bfVp * 1000 + DATA.greeks.volga * spotBp * spotBp * bfVp * 0.018 + 6.2 * spotBp * 0.3 - Math.abs(spotBp) * 1.0);
}
export const skewGrid = dRR.map((r) => dSpot.map((s) => skewPnl(s, r)));
export const flyGrid = dBF.map((b) => dSpot.map((s) => flyPnl(s, b)));

// greek stress surfaces
export const GREEKS = ["delta", "gamma", "vega", "theta", "vanna", "volga"];
interface GreekOpt {
  vol?: number;
  days?: number;
  rr?: number;
  bf?: number;
}
function greekVal(gk: string, spotBp: number, opt: GreekOpt): number {
  const atm = Math.exp(-Math.pow(spotBp / 190, 2));
  const skewMod = 1 + (opt.rr || 0) * 0.06;
  const flyMod = 1 + (opt.bf || 0) * 0.05;
  if (gk === "delta") {
    let v = 270 + (spotBp / 50) * 900;
    if (opt.vol != null) v += opt.vol * 18;
    if (opt.days != null) v += (-(opt.days - 21) / 21) * 28;
    if (opt.rr != null) v += opt.rr * 120;
    if (opt.bf != null) v += opt.bf * 20;
    return Math.round(v);
  }
  if (gk === "gamma") {
    let v = 3500 * atm;
    if (opt.vol != null) v *= 1 - opt.vol * 0.03;
    if (opt.days != null) v *= Math.pow(21 / opt.days, 0.45);
    if (opt.bf != null) v *= flyMod;
    return Math.round(v);
  }
  if (gk === "vega") {
    let v = 2690 * Math.exp(-Math.pow(spotBp / 260, 2));
    if (opt.vol != null) v *= 1 + opt.vol * 0.012;
    if (opt.days != null) v *= Math.pow(opt.days / 21, 0.5);
    if (opt.rr != null) v *= skewMod;
    if (opt.bf != null) v *= flyMod;
    return Math.round(v);
  }
  if (gk === "vanna") {
    let v = 150 + 1530 * (spotBp / 150) + 880 * (opt.rr || 0) + 40 * (opt.vol || 0) + 30 * (opt.bf || 0);
    if (opt.days != null) v *= Math.pow(21 / opt.days, 0.35);
    return Math.round(v);
  }
  if (gk === "volga") {
    let v = 420 * atm + 760 * (opt.bf || 0) + 90 * (opt.vol || 0) + 60 * (opt.rr || 0);
    if (opt.days != null) v *= Math.pow(opt.days / 21, 0.4);
    return Math.round(v);
  }
  let v = -2130 * atm;
  if (opt.vol != null) v *= 1 + opt.vol * 0.02;
  if (opt.days != null) v *= Math.pow(21 / opt.days, 0.5);
  if (opt.bf != null) v *= 1 + opt.bf * 0.045;
  return Math.round(v);
}

type GreekGrids = Record<string, number[][]>;
export const greekVolGrids: GreekGrids = {};
export const greekTimeGrids: GreekGrids = {};
export const greekSkewGrids: GreekGrids = {};
export const greekFlyGrids: GreekGrids = {};
GREEKS.forEach((gk) => {
  greekVolGrids[gk] = dSpot.map((s) => dVol.map((v) => greekVal(gk, s, { vol: v })));
  greekTimeGrids[gk] = dSpot.map((s) => stressTimeDays.map((d) => greekVal(gk, s, d === 0 ? {} : { days: d })));
  greekSkewGrids[gk] = dSpot.map((s) => dRR.map((r) => greekVal(gk, s, { rr: r })));
  greekFlyGrids[gk] = dSpot.map((s) => dBF.map((b) => greekVal(gk, s, { bf: b })));
});

export interface GreekVec {
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  vanna: number;
  volga: number;
}
export interface LadderRow extends GreekVec {
  axis: string;
  pnl: number;
  spot?: number;
  days?: number;
}
const ladGreeks = (s: number, opt: GreekOpt): GreekVec => ({
  delta: greekVal("delta", s, opt),
  gamma: greekVal("gamma", s, opt),
  vega: greekVal("vega", s, opt),
  theta: greekVal("theta", s, opt),
  vanna: greekVal("vanna", s, opt),
  volga: greekVal("volga", s, opt),
});
export const spotLadder: LadderRow[] = dSpot.map((s) => ({
  axis: (s > 0 ? "+" : "") + s + "bp",
  spot: 1.15692 * (1 + s / 10000),
  pnl: stressPnl(s, 0),
  ...ladGreeks(s, {}),
}));
const volLadderLevels = [-6, -4, -2, 0, 2, 4, 6];
export const volLadder: LadderRow[] = volLadderLevels.map((v) => ({
  axis: (v > 0 ? "+" : "") + v + "vp",
  pnl: stressPnl(0, v),
  ...ladGreeks(0, { vol: v }),
}));
export const timeLadder: LadderRow[] = [
  { axis: "Today", days: 0, pnl: 0, ...ladGreeks(0, {}) },
  ...timeCols.map((c, i) => {
    const d = timeDays[i]!;
    return { axis: c, days: d, pnl: timePnl(0, d), ...ladGreeks(0, { days: d }) };
  }),
];
export const skewLadder: LadderRow[] = dRR.map((r) => ({
  axis: (r > 0 ? "+" : "") + r + "vp",
  pnl: skewPnl(0, r),
  ...ladGreeks(0, { rr: r }),
}));
export const flyLadder: LadderRow[] = dBF.map((b) => ({
  axis: (b > 0 ? "+" : "") + b + "vp",
  pnl: flyPnl(0, b),
  ...ladGreeks(0, { bf: b }),
}));

export const ladderLevels = [-200, -150, -100, -50, 0, 50, 100, 150, 200];
export const greeksLadder = ladderLevels.map((bp) => {
  const sp = bp / 100;
  return {
    bp,
    spot: 1.0842 * (1 + bp / 10000),
    pnl: Math.round(6.2 * bp * 0.9 + 0.5 * 14.5 * sp * sp * 1800),
    delta: +(6.2 + 14.5 * sp * 0.6).toFixed(1),
    gamma: +(14.5 - Math.abs(sp) * 1.1).toFixed(1),
    vega: +(32 - Math.abs(sp) * 2.2).toFixed(1),
    hedge: Math.round(-(6.2 + 14.5 * sp * 0.6) * 1.2),
  };
});

export interface VegaTenor {
  tenor: string;
  vega: number;
  n: number;
  pct: number;
}
export const vegaPerTenor: VegaTenor[] = [
  { tenor: "1M", vega: 6.4, n: 4, pct: 0 },
  { tenor: "2M", vega: 9.1, n: 2, pct: 0 },
  { tenor: "3M", vega: 7.8, n: 3, pct: 0 },
  { tenor: "4M", vega: 5.2, n: 2, pct: 0 },
  { tenor: "5M", vega: 2.1, n: 0, pct: 0 },
  { tenor: "6M", vega: 1.4, n: 1, pct: 0 },
];
{
  const tot = vegaPerTenor.reduce((s, v) => s + v.vega, 0);
  vegaPerTenor.forEach((v) => (v.pct = (v.vega / tot) * 100));
}

export const vannaPerTenor = [
  { tenor: "1M", v: 18 }, { tenor: "2M", v: 152 }, { tenor: "3M", v: 24 },
  { tenor: "4M", v: 6 }, { tenor: "5M", v: -9 }, { tenor: "6M", v: -14 },
];
export const volgaPerTenor = [
  { tenor: "1M", v: 35 }, { tenor: "2M", v: 4 }, { tenor: "3M", v: -28 },
  { tenor: "4M", v: 16 }, { tenor: "5M", v: 6 }, { tenor: "6M", v: 9 },
];

export const vegaPCA = [
  { mode: "PC1", name: "level", vega: 27.6, var: 97 },
  { mode: "PC2", name: "slope", vega: -9.4, var: 1.2 },
  { mode: "PC3", name: "curvature", vega: 6.8, var: 0.8 },
];

export const pinRisk = [
  { product: "Straddle 1M C", strike: 1.085, dte: 29, dist: 8, now: 1480, ifPin: -2100, breach: 920 },
  { product: "Straddle 1M P", strike: 1.085, dte: 29, dist: 8, now: 1320, ifPin: -1980, breach: 880 },
  { product: "Calendar 1M C", strike: 1.085, dte: 29, dist: 8, now: -640, ifPin: 1450, breach: -380 },
  { product: "Butterfly 3M body", strike: 1.0842, dte: 85, dist: 0, now: 820, ifPin: 2240, breach: -1120 },
  { product: "RR 2M call", strike: 1.101, dte: 57, dist: 168, now: 2680, ifPin: 240, breach: 3100 },
];

export const shocks = Array.from({ length: 13 }, (_, i) => -3 + i * 0.5);
export interface ScenarioPoint {
  x: number;
  pnl: number;
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
}
export function scenarioSeries(kind: string): ScenarioPoint[] {
  return shocks.map((x) => {
    if (kind === "spot") {
      return {
        x,
        pnl: Math.round(6.2 * x * 90 + 0.5 * 14.5 * x * x * 220),
        delta: +(6.2 + 14.5 * x * 0.6).toFixed(1),
        gamma: +(14.5 - Math.abs(x) * 1.2).toFixed(1),
        vega: +(32 - Math.abs(x) * 1.5).toFixed(1),
        theta: +(-9.1 - Math.abs(x) * 0.3).toFixed(1),
      };
    }
    return {
      x,
      pnl: Math.round(32 * x * 100),
      delta: +(6.2 + x * 0.4).toFixed(1),
      gamma: +(14.5).toFixed(1),
      vega: +(32 + x * 0.8).toFixed(1),
      theta: +(-9.1 + x * 0.2).toFixed(1),
    };
  });
}

export const attribution = [
  { pos: "Vanilla Call", side: "BUY", delta: -1420, gamma: 0.01, vega: 2.59, theta: -86.91, resid: -1990, actual: -3500 },
  { pos: "Vanilla Put", side: "BUY", delta: -3320, gamma: 0.02, vega: 1.78, theta: -86.43, resid: 9000, actual: 5600 },
  { pos: "Future · 6E", side: "BUY", delta: -916.48, gamma: 0, vega: null, theta: 0, resid: null, actual: -916.48 },
  { pos: "Vanilla Call", side: "SELL", delta: 1010, gamma: 0, vega: -2.28, theta: 82.71, resid: 1820, actual: 2910 },
  { pos: "Vanilla Call", side: "BUY", delta: -1200, gamma: 0, vega: 19.84, theta: -66.2, resid: -2000, actual: -3250 },
];
export const attributionTotal = { delta: -5850, gamma: 0.03, vega: 21.93, theta: -156.83, resid: 6830, actual: 838.52 };

export interface StackItem {
  name: string;
  status: "up" | "warn" | "down";
  meta: string;
}
export const stack: { layer: string; items: StackItem[] }[] = [
  { layer: "EDGE", items: [{ name: "nginx", status: "up", meta: "reverse proxy · TLS" }] },
  { layer: "APP", items: [{ name: "api (FastAPI)", status: "up", meta: "REST + WS · 142 req/s" }, { name: "web (Vite)", status: "up", meta: "React 18 · served" }] },
  { layer: "ENGINES", items: [
    { name: "market-data", status: "up", meta: "ticks · OHLC bars" },
    { name: "vol-engine", status: "up", meta: "surface · SVI calib" },
    { name: "signal-engine", status: "up", meta: "PCA · regime GMM" },
    { name: "risk-engine", status: "up", meta: "greeks · stress" },
    { name: "exec-engine", status: "warn", meta: "IB Gateway · PAPER" },
  ] },
  { layer: "DATA", items: [{ name: "postgres", status: "up", meta: "18 tables · 2.4M rows" }, { name: "redis", status: "up", meta: "pub/sub · cache" }] },
  { layer: "OBS", items: [{ name: "AWS SSM/KMS", status: "up", meta: "secrets · encrypted" }] },
];
export const engines: { name: string; hb: number; stale: number; status: "up" | "warn" | "down" }[] = [
  { name: "market-data", hb: 1.2, stale: 5, status: "up" },
  { name: "vol-engine", hb: 42, stale: 200, status: "up" },
  { name: "signal-engine", hb: 58, stale: 200, status: "up" },
  { name: "risk-engine", hb: 0.8, stale: 3, status: "up" },
  { name: "exec-engine", hb: 4.1, stale: 10, status: "up" },
  { name: "IB Gateway", hb: 2.0, stale: 15, status: "warn" },
];
export const cycle = {
  total: 180,
  elapsed: 112,
  steps: [
    { name: "Vol Surface", done: true },
    { name: "Regime (GMM)", done: true },
    { name: "PCA decomp", done: true },
    { name: "Publish /ws", done: false },
  ],
};

export const config = [
  { key: "signal.pca.z_threshold", value: "1.50", v: 12, by: "quant@desk", note: "tightened after May regime shift" },
  { key: "regime.forecast_model", value: "HAR-RV", v: 8, by: "quant@desk", note: "switched GARCH→HAR" },
  { key: "sizing.base_contracts", value: "25", v: 5, by: "quant@desk", note: "" },
  { key: "sizing.regime_mult.calm", value: "1.00", v: 5, by: "quant@desk", note: "" },
  { key: "exit_rules.tp_vega_pts", value: "+2.0", v: 3, by: "quant@desk", note: "" },
  { key: "exit_rules.sl_pct", value: "-35%", v: 3, by: "quant@desk", note: "" },
  { key: "surface.calibration", value: "SSVI", v: 7, by: "quant@desk", note: "no-arb SSVI" },
  { key: "delta_hedge.band_bp", value: "±50", v: 4, by: "quant@desk", note: "" },
];

export interface StressPreset {
  id: string;
  name: string;
  sub: string;
  spot: number;
  vol: number;
  pnl: number;
}
export const stressPresets: StressPreset[] = [
  { id: "lehman", name: "Lehman", sub: "Sep '08 · credit seize", spot: -180, vol: 6, pnl: 0 },
  { id: "snb", name: "SNB floor", sub: "Jan '15 · CHF unpeg", spot: -200, vol: 6, pnl: 0 },
  { id: "brexit", name: "Brexit", sub: "Jun '16 · GBP gap", spot: -120, vol: 4, pnl: 0 },
  { id: "covid", name: "COVID", sub: "Mar '20 · liquidity crunch", spot: -160, vol: 6, pnl: 0 },
  { id: "taper", name: "Taper", sub: "May '13 · rates shock", spot: -80, vol: 2, pnl: 0 },
  { id: "meltup", name: "Melt-up", sub: "carry grind · vol crush", spot: 60, vol: -4, pnl: 0 },
];
stressPresets.forEach((p) => (p.pnl = stressPnl(p.spot, p.vol)));

export const marginalVar = [
  { pos: "Risk Reversal 25Δ 2M", standalone: -132, marginal: -96, comp: -108, pct: 34.6, f: { spot: -8, level: -6, skew: -90, curv: -4 } },
  { pos: "Straddle ATM 1M", standalone: -98, marginal: -71, comp: -84, pct: 26.9, f: { spot: -8, level: -52, skew: -8, curv: -16 } },
  { pos: "Calendar 1M/4M", standalone: -47, marginal: -39, comp: -44, pct: 14.1, f: { spot: -4, level: -20, skew: -4, curv: -16 } },
  { pos: "Butterfly 25Δ 3M", standalone: -54, marginal: -28, comp: -41, pct: 13.1, f: { spot: -4, level: -5, skew: -4, curv: -28 } },
  { pos: "Future 6E Sep26", standalone: -38, marginal: -22, comp: -35, pct: 11.2, f: { spot: -33, level: -2, skew: 0, curv: 0 } },
];
export const marginalVarTotal = { standalone: -369, comp: -312, pct: 100, diversification: 57 };

export interface VarFactor {
  key: string;
  label: string;
  v: number;
  color: string;
  incident?: boolean;
}
export const varFactors: VarFactor[] = [
  { key: "skew", label: "Skew · RR", v: -106, color: "#a78bfa", incident: true },
  { key: "level", label: "Vol level · PC1", v: -85, color: "var(--accent)" },
  { key: "curv", label: "Curvature · PC3", v: -64, color: "var(--pos)" },
  { key: "spot", label: "Spot · Delta", v: -57, color: "var(--warn)" },
];

export const dailyPnl = [-4, 6, -3, -5, 48, -4, 3, -5, -3, 38, -4, 5, -6, -3, 52, -4, -2, 7, -5, 34, -3, 5];
export const perfStats = {
  cumRealized: 312,
  cumUnrealized: 38.4,
  maxDd: -8.2,
  currentDd: -1.4,
  sharpe: 1.84,
  hitRate: (dailyPnl.filter((v) => v > 0).length / dailyPnl.length) * 100,
  nClosed: 0, // genuine trade closes
  nReconciledFlat: 0, // netting/reconciliation adjustments (not trades)
  netLiqChange: 0, // ground-truth Δ net-liq over the window ($k)
  hitRateNull: false, // true when there are no genuine closes → show "—"
};
/** Shape reused by the live Portfolio adapter (R11 PR 3). */
export type PerfStats = typeof perfStats;

export interface Coverage {
  gammaPnl: number;
  vegaPnl: number;
  thetaPaid: number;
  threshold: number;
  posture: string;
  returnOnMargin: number;
  returnOnVar: number;
  sharpe: number;
  history: number[];
  convexity: number;
  carry: number;
  ratio: number;
  windowDays: number;
  windowLabel: string;
}
export const coverage: Coverage = {
  gammaPnl: 88.2,
  vegaPnl: 54.1,
  thetaPaid: 118.4,
  threshold: 1.0,
  posture: "long gamma · Theta−",
  returnOnMargin: 4.2,
  returnOnVar: 0.34,
  sharpe: 1.84,
  history: [0.84, 0.91, 0.88, 0.97, 1.05, 0.99, 1.08, 1.02, 0.95, 1.06, 1.12, 1.04, 1.18, 1.09, 1.21, 1.14, 1.07, 1.16, 1.1, 1.23, 1.17, 1.09, 1.2],
  convexity: 0,
  carry: 0,
  ratio: 0,
  windowDays: 13,
  windowLabel: "13 sessions",
};
coverage.convexity = +(coverage.gammaPnl + coverage.vegaPnl).toFixed(1);
coverage.carry = coverage.thetaPaid;
coverage.ratio = +(coverage.convexity / coverage.carry).toFixed(2);

export interface WaterfallStep {
  label: string;
  sub?: string;
  v: number;
  type: string;
  color?: string;
}
const _net = 24.9;
export const waterfall: Record<string, WaterfallStep[]> = {
  greek: [
    { label: "Start", v: 0, type: "start" },
    { label: "+Gamma", sub: "½Gamma(dS)²", v: 88.2, type: "pos" },
    { label: "+Vega", sub: "Vega·dσ", v: 54.1, type: "pos" },
    { label: "−Theta", sub: "Theta·dt", v: -118.4, type: "neg" },
    { label: "±Delta", sub: "Delta·dS", v: -5.9, type: "neg" },
    { label: "Vanna", sub: "skew · incident", v: -7.8, type: "neg" },
    { label: "Volga", sub: "vol convexity", v: 13.4, type: "pos" },
    { label: "residual", sub: "unexplained", v: 1.3, type: "resid" },
    { label: "Net", v: _net, type: "net" },
  ],
  mode: [
    { label: "Start", v: 0, type: "start" },
    { label: "PC1", sub: "level · straddle", v: 31.4, type: "pos", color: "#4f9dff" },
    { label: "PC2", sub: "slope · calendar", v: -7.8, type: "neg", color: "#26c6da" },
    { label: "PC3", sub: "curvature · fly", v: 14.6, type: "pos", color: "#e0b341" },
    { label: "skew", sub: "incident · RR", v: -18.2, type: "neg", color: "#a78bfa" },
    { label: "Delta hedge", sub: "6E future", v: 4.0, type: "pos", color: "var(--muted)" },
    { label: "residual", sub: "unexplained", v: 0.9, type: "resid" },
    { label: "Net", v: _net, type: "net" },
  ],
  structure: [
    { label: "Start", v: 0, type: "start" },
    { label: "Straddle", sub: "1M ATM", v: 31.4, type: "pos" },
    { label: "Risk Rev", sub: "25Δ 2M", v: -18.2, type: "neg" },
    { label: "Butterfly", sub: "25Δ 3M", v: 14.6, type: "pos" },
    { label: "Calendar", sub: "1M/4M", v: -7.8, type: "neg" },
    { label: "Future", sub: "6E", v: 4.0, type: "pos" },
    { label: "residual", sub: "unexplained", v: 0.9, type: "resid" },
    { label: "Net", v: _net, type: "net" },
  ],
  tenor: [
    { label: "Start", v: 0, type: "start" },
    { label: "1M", sub: "0-30D", v: 22.8, type: "pos" },
    { label: "2M", sub: "31-60D", v: -12.4, type: "neg" },
    { label: "3M", sub: "61-90D", v: 16.1, type: "pos" },
    { label: "4M", sub: "91-120D", v: -3.6, type: "neg" },
    { label: "6M", sub: "121-180D", v: 1.1, type: "pos" },
    { label: "residual", sub: "unexplained", v: 0.9, type: "resid" },
    { label: "Net", v: _net, type: "net" },
  ],
};

export interface BookStructure {
  name: string;
  nominal: number;
  legs: number;
  color: string;
  pct: number;
}
export interface BookComposition {
  byStructure: BookStructure[];
  legs: number;
  totalNominal: number;
}
export const bookComposition: BookComposition = {
  byStructure: [
    { name: "Straddle", nominal: 6.25, legs: 2, color: "var(--accent)", pct: 0 },
    { name: "Risk Reversal", nominal: 7.5, legs: 2, color: "#a78bfa", pct: 0 },
    { name: "Butterfly", nominal: 10.0, legs: 3, color: "var(--pos)", pct: 0 },
    { name: "Calendar", nominal: 3.75, legs: 2, color: "var(--warn)", pct: 0 },
    { name: "Future 6E", nominal: 1.0, legs: 1, color: "var(--muted)", pct: 0 },
  ],
  legs: 10,
  totalNominal: 0,
};
{
  const t = bookComposition.byStructure.reduce((s, x) => s + x.nominal, 0);
  bookComposition.totalNominal = t;
  bookComposition.byStructure.forEach((x) => (x.pct = (x.nominal / t) * 100));
}

export const DATA2 = {
  dSpot, dVol, stressGrid, timeCols, timeDays, stressTimeCols, stressTimeDays, timeGrid, GREEKS,
  greekVolGrids, greekTimeGrids, spotLadder, volLadder, timeLadder, ladderLevels, greeksLadder,
  vegaPerTenor, pinRisk, stressPresets, marginalVar, marginalVarTotal,
  dRR, dBF, skewGrid, flyGrid, greekSkewGrids, greekFlyGrids, skewLadder, flyLadder,
  vannaPerTenor, volgaPerTenor, vegaPCA, varFactors,
  dailyPnl, perfStats, coverage, waterfall, bookComposition,
  shocks, scenarioSeries, attribution, attributionTotal, stack, engines, cycle, config,
  // ---- Risk reference tables (F / I / H) ----
  riskSpot: 1.15692,
  svCols: [-200, -100, -50, 0, 50, 100, 200],
  svRows: [3, 1, 0, -1, -3],
  svGrid: [
    [24820, 21570, 20740, 20470, 20760, 21610, 24940],
    [13390, 8750, 7420, 6790, 6880, 7690, 11420],
    [8210, 2580, 886.71, 0, -48.66, 746.75, 4790],
    [3600, -3290, -5490, -6730, -6960, -6170, -1670],
    [-2570, -13190, -17160, -19750, -20690, -19860, -13520],
  ],
  svPositions: 5,
  vegaBuckets: [
    { tenor: "1M", range: "0-30D", vega: 0, pct: 0, pos: 0 },
    { tenor: "2M", range: "31-60D", vega: 0, pct: 0, pos: 0 },
    { tenor: "3M", range: "61-90D", vega: 2920, pct: 43, pos: 3 },
    { tenor: "4M", range: "91-120D", vega: 0, pct: 0, pos: 0 },
    { tenor: "6M", range: "121-180D", vega: 0, pct: 0, pos: 0 },
    { tenor: ">6M", range: "181-10000D", vega: 3850, pct: 57, pos: 1 },
  ],
  vegaBucketTotal: { vega: 6760, pct: 100, pos: 4 },
  ladderH: [
    { spot: 1.11064, bp: -400, pnl: 26310, delta: -924890, gamma: 929.0, vega: 2560, hedge: 924890 },
    { spot: 1.13378, bp: -200, pnl: 8210, delta: -603180, gamma: 1870, vega: 4930, hedge: 603180 },
    { spot: 1.15692, bp: 0, pnl: 0, delta: -81330, gamma: 2510, vega: 6760, hedge: 81330 },
    { spot: 1.18006, bp: 200, pnl: 4790, delta: 484250, gamma: 2230, vega: 6560, hedge: -484250 },
    { spot: 1.20319, bp: 400, pnl: 21270, delta: 908550, gamma: 1420, vega: 4830, hedge: -908550 },
  ],
  ladderHPositions: 5,
};
