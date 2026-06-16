/**
 * VOLDESK — Risk (matrix, stress, ladder, scenarios). Ported 1:1 from the
 * prototype's `js/views_risk.jsx` (global-window JSX) into typed ES modules.
 * Exports only RiskView; all sub-components stay local (lint).
 */
import { useState } from "react";
import { Bar, Panel, Tag } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { pnlCls } from "../components/format";
import type { Tone } from "../components/format";
import { DATA, DATA2, fmt } from "../data";
import type { LadderRow, VarFactor } from "../data";
import { useDeskData } from "../data/deskData";
import type { Fresh } from "../data/freshness";

// standard-normal CDF (Abramowitz & Stegun 7.1.26) → percentile of a z-score
const normCdf = (z: number): number => {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989423 * Math.exp(-z * z / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return z > 0 ? 1 - p : p;
};
const ordinal = (n: number): string => {
  const s = ["th", "st", "nd", "rd"],
    v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]!);
};

interface DivBarRow {
  label: string;
  v: number;
  sub?: string;
  neutral?: boolean;
  flag?: string | null;
}

// ---- diverging bars centered on zero — for SIGNED exposures (vanna, volga, vega-by-mode) ----
// scale="sqrt" keeps small buckets legible when one tenor dominates; rows can be `neutral` (no rich/cheap color).
function DivBars({ rows, unit = "", scale = "linear" }: { rows: DivBarRow[]; unit?: string; scale?: "linear" | "sqrt" }): JSX.Element {
  const mag = (v: number): number => (scale === "sqrt" ? Math.sqrt(Math.abs(v)) : Math.abs(v));
  const max = Math.max(...rows.map((r) => mag(r.v))) || 1;
  return (
    <div className="divbars">
      {rows.map((r) => {
        const pos = r.v >= 0,
          w = (mag(r.v) / max) * 50;
        return (
          <div key={r.label} className="divbar-row">
            <span className="divbar-lbl"><span className="mono">{r.label}</span>{r.sub && <span className="divbar-sub dim mono">{r.sub}</span>}</span>
            <div className="divbar-track">
              <span className="divbar-mid" />
              <span className={"divbar-fill " + (r.neutral ? "neu" : pos ? "pos" : "neg")} style={{ left: pos ? "50%" : (50 - w) + "%", width: w + "%" }} />
            </div>
            <span className={"divbar-val mono " + (r.neutral ? "dim" : pos ? "pos" : "neg")}>{(pos ? "+" : "−") + Math.abs(r.v) + unit}{r.flag && <span className="divbar-flag" title={r.flag}>▲</span>}</span>
          </div>
        );
      })}
    </div>
  );
}

// neutral "risk-only" badge — marks surfaces that carry exposure but NO signal (skew). Never the signal palette.
function RiskOnly({ text = "risk-only" }: { text?: string }): JSX.Element {
  return <span className="risk-only-badge mono">{text}</span>;
}

// ---- stacked factor decomposition bar (VaR by factor: spot / vol / skew / curvature) ----
function FactorStack({ factors, compact }: { factors: VarFactor[]; compact?: boolean }): JSX.Element {
  const sorted = [...factors].sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  const tot = sorted.reduce((s, f) => s + Math.abs(f.v), 0) || 1;
  return (
    <div className={"facstack" + (compact ? " compact" : "")}>
      <div className="facstack-bar">
        {sorted.map((f) => <span key={f.key || f.label} className="facstack-seg" style={{ width: (Math.abs(f.v) / tot * 100) + "%", background: f.color }} title={f.label} />)}
      </div>
      <div className="facstack-leg">
        {sorted.map((f) => (
          <span key={f.key || f.label} className="facstack-item">
            <i style={{ background: f.color }} /><span className="dim">{f.label}</span>
            {f.incident && <span className="incident-tag">incident · à neutraliser</span>}
            <b className="mono neg">−${Math.abs(f.v)}k</b><span className="dim mono fs-pct">{(Math.abs(f.v) / tot * 100).toFixed(0)}%</span>
          </span>
        ))}
      </div>
    </div>
  );
}

interface ReturnPoint {
  z: number;
  label: string;
  breach?: boolean;
}

// ---- VaR distribution bell curve (loss tail shaded), PC-card visual family ----
function VarCurve({ var95, var99, w = 280, h = 104, points = [] }: { var95: number; var99: number; w?: number; h?: number; points?: ReturnPoint[] }): JSX.Element {
  const z95 = -1.645,
    z99 = -2.326;
  const xs: number[] = [];
  for (let i = 0; i <= 80; i++) xs.push(-3.4 + (6.8 * i) / 80);
  const pdf = (x: number): number => Math.exp(-0.5 * x * x);
  const px = (x: number): number => ((x + 3.4) / 6.8) * w;
  const py = (p: number): number => h - 16 - p * (h - 28);
  const d = xs.map((x, i) => (i ? "L" : "M") + px(x).toFixed(1) + " " + py(pdf(x)).toFixed(1)).join(" ");
  const areaPath = (pts: number[]): string => pts.length ? "M" + px(pts[0]!).toFixed(1) + " " + (h - 16) + " " + pts.map((x) => "L" + px(x).toFixed(1) + " " + py(pdf(x)).toFixed(1)).join(" ") + " L" + px(pts[pts.length - 1]!).toFixed(1) + " " + (h - 16) + " Z" : "";
  const tail95 = areaPath(xs.filter((x) => x <= z95));
  const tail99 = areaPath(xs.filter((x) => x <= z99));
  const kk = (v: number): string => "-$" + (Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(1) + "M" : Math.abs(v) + "k");
  return (
    <svg className="var-curve-svg" width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      {tail95 && <path d={tail95} fill="var(--neg)" fillOpacity="0.13" />}
      {tail99 && <path d={tail99} fill="var(--neg)" fillOpacity="0.3" />}
      {[-2, -1, 0, 1, 2].map((t) => (
        <line key={t} x1={px(t)} x2={px(t)} y1="6" y2={h - 16} stroke="var(--line)" strokeWidth="1" opacity={t === 0 ? 0.7 : 0.32} />
      ))}
      <path d={d} fill="none" stroke="var(--muted)" strokeWidth="1.5" />
      <line x1={px(z95)} x2={px(z95)} y1="14" y2={h - 16} stroke="var(--warn)" strokeWidth="1.5" strokeDasharray="3 2" />
      <line x1={px(z99)} x2={px(z99)} y1="14" y2={h - 16} stroke="var(--neg)" strokeWidth="1.5" strokeDasharray="3 2" />
      <text x={px(z95) + 3} y="12" fill="var(--warn)" fontSize="8.5" fontFamily="var(--mono)" textAnchor="start">95%</text>
      <text x={px(z99) - 3} y="12" fill="var(--neg)" fontSize="8.5" fontFamily="var(--mono)" textAnchor="end">99%</text>
      <text x={px(z95)} y={h - 3} fill="var(--warn)" fontSize="8.5" fontFamily="var(--mono)" textAnchor="middle">{kk(var95)}</text>
      <text x={px(z99)} y={h - 3} fill="var(--neg)" fontSize="8.5" fontFamily="var(--mono)" textAnchor="middle">{kk(var99)}</text>
      <text x={px(0)} y={h - 3} fill="var(--text-faint)" fontSize="8.5" fontFamily="var(--mono)" textAnchor="middle">mean</text>
      {/* realized return points */}
      {points.map((pt, i) => {
        const z = Math.max(-3.3, Math.min(3.3, pt.z));
        const cx = px(z),
          cy = py(pdf(z));
        const c = pt.breach ? "var(--neg)" : "var(--fg)";
        return (
          <g key={i}>
            <line x1={cx} x2={cx} y1={cy} y2={h - 16} stroke={c} strokeWidth="1" opacity="0.5" />
            <circle cx={cx} cy={cy} r="4" fill={c} stroke="var(--bg)" strokeWidth="1.4" />
            <text x={cx} y={cy - 8} fill={c} fontSize="8.5" fontWeight="700" fontFamily="var(--mono)" textAnchor="middle">{pt.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

interface RetDatum {
  ret: number;
  label: string;
}

function ReturnsBars({ data, h = 130 }: { data: RetDatum[]; h?: number }): JSX.Element {
  const w = 330,
    padB = 24,
    padT = 16;
  const max = Math.max(...data.map((d) => Math.abs(d.ret))) || 1;
  const midY = padT + (h - padT - padB) / 2;
  const bw = (w - 40) / data.length;
  const scale = (h - padT - padB) / 2 / max;
  return (
    <svg className="ret-svg" width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" style={{ display: "block" }}>
      <line x1="20" x2={w - 20} y1={midY} y2={midY} stroke="var(--line)" strokeWidth="1" />
      {data.map((d, i) => {
        const cx = 20 + bw * i + bw / 2;
        const hgt = Math.abs(d.ret) * scale;
        const up = d.ret >= 0;
        const y = up ? midY - hgt : midY;
        const col = up ? "var(--up)" : "var(--down)";
        return (
          <g key={i}>
            <rect x={cx - bw * 0.32} y={y} width={bw * 0.64} height={Math.max(1, hgt)} rx="2" fill={col} fillOpacity="0.82" />
            <text x={cx} y={up ? y - 4 : y + hgt + 12} fill={col} fontSize="11" fontWeight="700" fontFamily="var(--mono)" textAnchor="middle">{(d.ret >= 0 ? "+" : "") + d.ret.toFixed(1) + "%"}</text>
            <text x={cx} y={h - 8} fill="var(--text-faint)" fontSize="9.5" fontFamily="var(--mono)" textAnchor="middle">{d.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

function EmpiricalHist({ muZ, v95z, v99z, esZ, letter, h = 88 }: { muZ: number; v95z: number; v99z: number; esZ: number; letter: string; h?: number }): JSX.Element {
  const w = 340,
    x0 = -3.8,
    x1 = 2.8;
  const px = (z: number): number => 12 + ((z - x0) / (x1 - x0)) * (w - 20);
  const baseY = h - 24,
    topY = 14;
  const bins = [0.04, 0.06, 0.09, 0.07, 0.12, 0.10, 0.17, 0.15, 0.23, 0.33, 0.47, 0.63, 0.81, 0.97, 1.0, 0.83, 0.6, 0.4, 0.25, 0.15, 0.08, 0.04];
  const n = bins.length,
    bw = (w - 20) / n,
    maxH = baseY - topY;
  return (
    <svg className="var-curve-svg" width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      {/* left-tail shading behind bars */}
      <rect x={px(x0)} y={topY} width={px(v95z) - px(x0)} height={baseY - topY} fill="var(--neg)" fillOpacity="0.08" />
      {bins.map((b, i) => {
        const x = 12 + i * bw,
          hh = b * maxH;
        const zc = x0 + ((i + 0.5) / n) * (x1 - x0);
        return <rect key={i} x={x + bw * 0.08} y={baseY - hh} width={bw * 0.84} height={hh} fill={zc <= v95z ? "var(--neg)" : "var(--muted)"} fillOpacity={zc <= v95z ? 0.55 : 0.5} />;
      })}
      <line x1={px(x0)} x2={px(x1)} y1={baseY} y2={baseY} stroke="var(--border)" strokeWidth="1" />
      {/* mean μ of the distribution (subtle reference) */}
      <line x1={px(0)} x2={px(0)} y1={topY} y2={baseY} stroke="var(--fg)" strokeWidth="1.1" strokeOpacity="0.35" strokeDasharray="2 2" />
      <text x={px(0)} y={baseY + 11} fill="var(--fg)" fontSize="8" fontFamily="var(--mono)" textAnchor="middle" opacity="0.55">µ</text>
      {/* current realized return (white) */}
      <line x1={px(muZ)} x2={px(muZ)} y1={topY} y2={baseY} stroke="var(--fg)" strokeWidth="1.6" />
      <text x={px(muZ)} y={topY - 3} fill="var(--fg)" fontSize="8" fontWeight="700" fontFamily="var(--mono)" textAnchor="middle">{letter}</text>
      {/* VaR95 */}
      <line x1={px(v95z)} x2={px(v95z)} y1={topY + 8} y2={baseY} stroke="var(--warn)" strokeWidth="1.3" strokeDasharray="4 3" />
      <text x={px(v95z)} y={baseY + 11} fill="var(--warn)" fontSize="7.5" fontFamily="var(--mono)" textAnchor="middle">95%</text>
      {/* VaR99 */}
      <line x1={px(v99z)} x2={px(v99z)} y1={topY + 8} y2={baseY} stroke="var(--neg)" strokeWidth="1.3" strokeDasharray="4 3" />
      <text x={px(v99z)} y={baseY + 11} fill="var(--neg)" fontSize="7.5" fontFamily="var(--mono)" textAnchor="middle">99%</text>
      {/* ES point */}
      <line x1={px(esZ)} x2={px(esZ)} y1={baseY - 14} y2={baseY} stroke="#b3402f" strokeWidth="1.4" strokeDasharray="2 2" />
      <circle cx={px(esZ)} cy={baseY - 14} r="3.5" fill="#b3402f" stroke="var(--bg)" strokeWidth="1" />
      <text x={px(esZ)} y={baseY + 11} fill="#c4584a" fontSize="7.5" fontFamily="var(--mono)" textAnchor="middle">ES</text>
    </svg>
  );
}

interface VarRow {
  id: string;
  lbl: string;
  m: number;
  ret: number;
  ratio: number;
}
interface VarCalc {
  v95: number;
  v99: number;
  es: number;
  retk: number;
  muZ: number;
  esZ: number;
}

function VarCard({ var95, var99, es99, netLiq, fresh }: { var95: number; var99: number; es99: number; netLiq: number; fresh: Fresh<unknown> }): JSX.Element {
  const base95 = var95,
    base99 = var99;
  const NL = netLiq;
  const rows: VarRow[] = [
    { id: "1d", lbl: "Daily", m: 1, ret: 0.92, ratio: 1.16 },
    { id: "1w", lbl: "Weekly", m: 2.6, ret: 2.1, ratio: 1.19 },
    { id: "1M", lbl: "Monthly", m: 4.8, ret: 4.6, ratio: 1.25 },
    { id: "1Y", lbl: "Yearly", m: 15.9, ret: 18.3, ratio: 1.34 },
  ];
  const [tf, setTf] = useState("1d");
  const kc = (vk: number): string => { const s = vk < 0 ? "−" : "+"; const a = Math.abs(vk); return s + "$" + (a >= 1000 ? (a / 1000).toFixed(2) + "M" : Math.round(a) + "k"); };
  const calc = (r: VarRow): VarCalc => {
    const v95 = base95 * r.m,
      v99 = base99 * r.m,
      // live 1d ES from the endpoint; longer horizons use the √t-scaled ES/VaR ratio.
      es = r.id === "1d" ? es99 : v99 * r.ratio;
    const retk = (r.ret / 100) * NL / 1000;
    const sig = Math.abs(v95) / 1.645;
    return { v95, v99, es, retk, muZ: retk / sig, esZ: -2.326 * r.ratio };
  };
  const sel = rows.find((r) => r.id === tf) ?? rows[0]!,
    c = calc(sel);
  const letter = ({ "1d": "D", "1w": "W", "1M": "M", "1Y": "Y" } as Record<string, string>)[sel.id] ?? "D";
  return (
    <Panel title="Value at Risk" right={<FreshBadge fresh={fresh} label="historical 1d" />} pad={false} className="trade-block">
      <div className="var-meta">
        <span className="var-method">historical sim</span>
        <span>504 obs · 504d window</span>
        <span>scale √t · 252d</span>
        <span className="var-frozen" title="le scaling √t suppose une exposition gelée — faux pour un book non-linéaire (vanna 2M +152k, theta bleed, roll-down du gamma). Lire 1M/1Y avec prudence.">⚠ exposition gelée</span>
        <span className="var-live"><span className="status-dot pulse" style={{ background: "var(--pos)" }} />live</span>
      </div>
      <div className="table-scroll">
        <table className="dt var-table">
          <thead><tr>
            <th className="l">Horizon</th><th className="r">exp. return μt</th><th className="r">VaR 95%</th><th className="r">VaR 99%</th><th className="r">ES 97.5%</th><th className="r">ES/VaR</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => {
              const x = calc(r);
              return (
                <tr key={r.id} className={"var-row " + (r.id === tf ? "row-now" : "")} onClick={() => setTf(r.id)}>
                  <td className="l mono">{r.id} <span className="dim">{r.lbl}</span></td>
                  <td className={"r mono " + pnlCls(x.retk)}>{kc(x.retk)} <span className="dim">({ordinal(Math.round(normCdf(x.muZ) * 100))})</span></td>
                  <td className="r mono neg">{kc(x.v95)}</td>
                  <td className="r mono neg">{kc(x.v99)}</td>
                  <td className="r mono neg">{kc(x.es)}</td>
                  <td className={"r mono " + (r.ratio >= 1.25 ? "warn" : "dim")}>{r.ratio.toFixed(2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="var-factors">
        <div className="vf-lbl dim small mono">VaR by factor <span className="dim">· which factor carries the tail (ties to marginal-VaR panel)</span></div>
        <FactorStack factors={DATA2.varFactors} compact />
      </div>
      <div className="ret-chart">
        <div className="ret-title">P&L distribution <span className="dim">· {sel.lbl} · empirical</span></div>
        <EmpiricalHist muZ={c.muZ} v95z={-1.645} v99z={-2.326} esZ={c.esZ} letter={letter} />
        <div className="hist-leg dim mono">
          <span><i className="lg-line mu" />return (D/W/M/Y)</span>
          <span><i className="lg-line w" />VaR 95%</span>
          <span><i className="lg-line n" />VaR 99%</span>
          <span><i className="lg-dot" />ES (tail mean)</span>
        </div>
      </div>
      <div className="var-backtest">
        <span className="dim">backtest 99%</span>
        <span className="mono">3 breaches / 2.5 expected · 252d</span>
        <span className="bt-pill">in the green</span>
      </div>
    </Panel>
  );
}

function HeatLegend({ note }: { note: string }): JSX.Element {
  return (
    <div className="heat-legend">
      <span className="hl-cap mono neg">− loss</span>
      <div className="hl-bar" />
      <span className="hl-cap mono pos">gain +</span>
      <span className="hl-note dim mono">{note}</span>
    </div>
  );
}

function StressGrid(): JSX.Element {
  const { dSpot, dVol, stressGrid } = DATA2;
  const max = Math.max(...stressGrid.flat().map(Math.abs)) || 1;
  const cell = (v: number): string => {
    const t = Math.max(-1, Math.min(1, v / max));
    if (t >= 0) return `oklch(0.62 ${0.02 + 0.13 * t} 150 / ${0.12 + 0.6 * t})`;
    return `oklch(0.58 ${0.02 + 0.15 * -t} 25 / ${0.12 + 0.6 * -t})`;
  };
  // transpose: rows = spot (vertical), cols = vol (horizontal)
  return (
    <table className="heatmap stress">
      <thead><tr><th className="corner">ΔSpot \ ΔVol</th>{dVol.map((v) => <th key={v}>{v > 0 ? "+" : ""}{v}vp<span className="th-sub">&nbsp;</span></th>)}</tr></thead>
      <tbody>
        {dSpot.map((s, si) => (
          <tr key={si}>
            <th>{s > 0 ? "+" : ""}{s}bp</th>
            {dVol.map((v, vi) => {
              const val = stressGrid[vi]![si]!;
              const center = v === 0 && s === 0;
              return <td key={vi} className={center ? "center-cell" : ""} style={{ background: center ? "var(--bg-3)" : cell(val) }}>{fmt.usdk(val).replace("$", "")}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface ScenarioDatum {
  x: number;
  pnl: number;
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
}

function ScenarioMini({ data, keyName, color, label }: { data: ScenarioDatum[]; keyName: keyof ScenarioDatum; color: string; label: string }): JSX.Element {
  const w = 200,
    h = 84,
    pad = 6;
  const vals = data.map((d) => d[keyName]);
  const lo = Math.min(...vals),
    hi = Math.max(...vals),
    rng = hi - lo || 1;
  const X = (i: number): number => pad + (i / (data.length - 1)) * (w - 2 * pad);
  const Y = (v: number): number => pad + (1 - (v - lo) / rng) * (h - 2 * pad);
  const d = data.map((p, i) => (i === 0 ? "M" : "L") + X(i).toFixed(1) + " " + Y(p[keyName]).toFixed(1)).join(" ");
  const zeroY = lo < 0 && hi > 0 ? Y(0) : null;
  return (
    <div className="scen-mini">
      <div className="scen-label">{label}</div>
      <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`}>
        {zeroY != null && <line x1={pad} x2={w - pad} y1={zeroY} y2={zeroY} stroke="var(--line)" strokeDasharray="2 2" />}
        <path d={d} fill="none" stroke={color} strokeWidth="1.8" />
      </svg>
    </div>
  );
}

function TimeStressGrid(): JSX.Element {
  const { dSpot, stressTimeCols, stressTimeDays, timeGrid } = DATA2;
  const max = Math.max(...timeGrid.flat().map(Math.abs)) || 1;
  const cell = (v: number): string => {
    const t = Math.max(-1, Math.min(1, v / max));
    if (t >= 0) return `oklch(0.62 ${0.02 + 0.13 * t} 150 / ${0.12 + 0.6 * t})`;
    return `oklch(0.58 ${0.02 + 0.15 * -t} 25 / ${0.12 + 0.6 * -t})`;
  };
  return (
    <table className="heatmap stress">
      <thead><tr><th className="corner">ΔSpot \ Time</th>{stressTimeCols.map((c, i) => <th key={c}>{c}<span className="dim th-sub">{stressTimeDays[i] === 0 ? " " : stressTimeDays[i] + "d"}</span></th>)}</tr></thead>
      <tbody>
        {dSpot.map((s, si) => (
          <tr key={si}>
            <th>{s > 0 ? "+" : ""}{s}bp</th>
            {timeGrid[si]!.map((v, ci) => {
              const now = s === 0 && ci === 0;
              return <td key={ci} className={now ? "center-cell" : ""} style={{ background: now ? "var(--bg-3)" : cell(v) }}>{fmt.usdk(v).replace("$", "")}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type PnlAxis = "vol" | "skew" | "fly";

// generic P&L stress grid for the vol / skew / fly axes (time has its own decay grid)
function PnlGrid({ axis }: { axis: PnlAxis }): JSX.Element {
  const D = DATA2;
  const cfg = {
    vol: { grid: D.stressGrid, cols: D.dVol, lbl: "ΔVol ∥ ATM" },
    skew: { grid: D.skewGrid, cols: D.dRR, lbl: "ΔRR · skew" },
    fly: { grid: D.flyGrid, cols: D.dBF, lbl: "ΔBF · fly" },
  }[axis];
  const max = Math.max(...cfg.grid.flat().map(Math.abs)) || 1;
  const cell = (v: number): string => { const t = Math.max(-1, Math.min(1, v / max)); return t >= 0 ? `oklch(0.62 ${0.02 + 0.13 * t} 150 / ${0.12 + 0.6 * t})` : `oklch(0.58 ${0.02 + 0.15 * -t} 25 / ${0.12 + 0.6 * -t})`; };
  return (
    <table className="heatmap stress">
      <thead><tr><th className="corner">ΔSpot \ {cfg.lbl}</th>{cfg.cols.map((v) => <th key={v}>{v > 0 ? "+" : ""}{v}vp<span className="th-sub">&nbsp;</span></th>)}</tr></thead>
      <tbody>
        {D.dSpot.map((s, si) => (
          <tr key={si}>
            <th>{s > 0 ? "+" : ""}{s}bp</th>
            {cfg.cols.map((v, ci) => {
              const val = cfg.grid[ci]![si]!,
                center = v === 0 && s === 0;
              return <td key={ci} className={center ? "center-cell" : ""} style={{ background: center ? "var(--bg-3)" : cell(val) }}>{fmt.usdk(val).replace("$", "")}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type GreekKey = "delta" | "gamma" | "vega" | "theta" | "vanna" | "volga";
type GreekAxis = "time" | "vol" | "skew" | "fly";

interface GreekGridCfg {
  grid: Record<string, number[][]>;
  cols: (string | number)[];
  lbl: string;
  sub?: (i: number) => string;
  zero: (ci: number) => boolean;
}

function GreekStressGrid({ greek, axis }: { greek: GreekKey; axis: GreekAxis }): JSX.Element {
  const D = DATA2;
  const cfgs: Record<GreekAxis, GreekGridCfg> = {
    time: { grid: D.greekTimeGrids, cols: D.stressTimeCols, lbl: "Time", sub: (i: number) => (D.stressTimeDays[i] === 0 ? " " : D.stressTimeDays[i] + "d"), zero: (ci: number) => ci === 0 },
    vol: { grid: D.greekVolGrids, cols: D.dVol.map((v) => (v > 0 ? "+" : "") + v + "vp"), lbl: "ΔVol ∥ ATM", zero: (ci: number) => D.dVol[ci] === 0 },
    skew: { grid: D.greekSkewGrids, cols: D.dRR.map((v) => (v > 0 ? "+" : "") + v + "vp"), lbl: "ΔRR · skew", zero: (ci: number) => D.dRR[ci] === 0 },
    fly: { grid: D.greekFlyGrids, cols: D.dBF.map((v) => (v > 0 ? "+" : "") + v + "vp"), lbl: "ΔBF · fly", zero: (ci: number) => D.dBF[ci] === 0 },
  };
  const cfg = cfgs[axis];
  const grid = cfg.grid[greek]!;
  const max = Math.max(...grid.flat().map(Math.abs)) || 1;
  const cell = (v: number): string => { const t = Math.max(-1, Math.min(1, v / max)); return t >= 0 ? `oklch(0.62 ${0.02 + 0.13 * t} 150 / ${0.12 + 0.6 * t})` : `oklch(0.58 ${0.02 + 0.15 * -t} 25 / ${0.12 + 0.6 * -t})`; };
  const kg = (v: number): string => { const s = v >= 0 ? "+" : "-"; const a = Math.abs(v); return a >= 1000 ? s + (a / 1000).toFixed(1) + "k" : s + a; };
  return (
    <table className="heatmap stress">
      <thead><tr><th className="corner">ΔSpot \ {cfg.lbl}</th>{cfg.cols.map((c, i) => <th key={i}>{c}{cfg.sub ? <span className="dim th-sub">{cfg.sub(i)}</span> : <span className="th-sub">&nbsp;</span>}</th>)}</tr></thead>
      <tbody>
        {D.dSpot.map((s, si) => (
          <tr key={si}>
            <th>{s > 0 ? "+" : ""}{s}bp</th>
            {grid[si]!.map((v, ci) => {
              const now = s === 0 && cfg.zero(ci);
              return <td key={ci} className={now ? "center-cell" : ""} style={{ background: now ? "var(--bg-3)" : cell(v) }}>{kg(v)}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PositionBreakdown(): JSX.Element {
  const rows = DATA.positions;
  const k = (v: number | null, d = 2): string => v == null ? "—" : (v >= 0 ? "+" : "-") + (Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(2) + "k" : Math.abs(v).toFixed(d));
  const dC = (p: typeof rows[number]): number => Math.round(p.delta * 0.35);
  const tC = (p: typeof rows[number]): number | null => p.iv ? +(p.theta * 0.07).toFixed(2) : null;
  const vC = (p: typeof rows[number]): number | null => p.iv ? +(p.vega * 0.003).toFixed(2) : null;
  const resid = (p: typeof rows[number]): number => Math.round(p.pnl - dC(p) - (tC(p) || 0));
  const col = (v: number | null): string => "r mono " + (v == null ? "dim" : pnlCls(v));
  return (
    <>
      <div className="table-scroll">
        <table className="dt pb-table">
          <thead>
            <tr>
              <th className="l grp-fix">Position</th><th className="l grp-fix">Side</th>
              <th className="r grp-fix">Tenor</th><th className="r grp-fix">IV</th><th className="r grp-fix">Nominal €</th>
              <th className="r grp-grk col-grp">Δ$</th><th className="r grp-grk">Γ</th><th className="r grp-grk">Vega</th><th className="r grp-grk">Θ</th><th className="r grp-grk">Vanna</th><th className="r grp-grk col-grp-end">Volga</th>
              <th className="r grp-pnl col-grp">P&L 1d</th><th className="r grp-pnl">P&L 1w</th><th className="r grp-pnl col-grp-end">P&L 1M</th>
              <th className="r grp-att col-grp">Δ contrib</th><th className="r grp-att">Θ contrib</th><th className="r grp-att">Vega contrib</th><th className="r grp-att col-grp-end">Residual</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id}>
                <td className="l grp-fix"><span className="sym">{p.structure}</span><span className="substruct">{p.packageId} · {p.expiry}</span></td>
                <td className="l grp-fix"><span className={"side-pill " + (p.side === "BUY" ? "long" : "short")}>{p.side}</span></td>
                <td className="r mono dim grp-fix">{p.tenor || "—"}</td>
                <td className="r mono dim grp-fix">{p.iv ? p.iv.toFixed(1) : "—"}</td>
                <td className="r mono dim grp-fix">{(p.nominal / 1e6).toFixed(2)}M</td>
                <td className={col(p.delta) + " grp-grk col-grp"}>{k(p.delta)}</td>
                <td className={(p.iv ? col(p.gamma) : "r mono dim") + " grp-grk"}>{p.iv ? (p.gamma / 1000).toFixed(1) + "k" : "—"}</td>
                <td className={(p.iv ? col(p.vega) : "r mono dim") + " grp-grk"}>{p.iv ? k(p.vega) : "—"}</td>
                <td className={(p.iv ? col(p.theta) : "r mono dim") + " grp-grk"}>{p.iv ? k(p.theta) : "—"}</td>
                <td className={(p.iv ? col(p.vanna) : "r mono dim") + " grp-grk"}>{p.iv ? k(p.vanna) : "—"}</td>
                <td className={(p.iv ? col(p.volga) : "r mono dim") + " grp-grk col-grp-end"}>{p.iv ? k(p.volga) : "—"}</td>
                <td className={col(p.pnl) + " grp-pnl col-grp"}>{fmt.usdk(p.pnl)}</td>
                <td className={col(Math.round(p.pnl * 2.6)) + " grp-pnl"}>{fmt.usdk(Math.round(p.pnl * 2.6))}</td>
                <td className={col(Math.round(p.pnl * 4.8)) + " grp-pnl col-grp-end"}>{fmt.usdk(Math.round(p.pnl * 4.8))}</td>
                <td className={col(dC(p)) + " grp-att col-grp"}>{k(dC(p))}</td>
                <td className={col(tC(p)) + " grp-att"}>{k(tC(p))}</td>
                <td className={col(vC(p)) + " grp-att"}>{k(vC(p))}</td>
                <td className={col(resid(p)) + " grp-att col-grp-end"}>{k(resid(p))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ---- vega bucketed by tenor : the curve-risk ladder (a term-structure desk's #1 risk) ----
function VegaTenorLadder({ rows }: { rows: { tenor: string; vega: number; n: number }[] }): JSX.Element {
  const max = Math.max(1, ...rows.map((r) => r.vega));
  return (
    <div className="vtl">
      {rows.map((r) => (
        <div key={r.tenor} className="vtl-row">
          <span className="vtl-ten mono">{r.tenor}</span>
          <div className="vtl-track"><div className="vtl-fill" style={{ width: (r.vega / max) * 100 + "%" }} /></div>
          <span className="vtl-val mono">${r.vega.toFixed(1)}k</span>
          <span className="vtl-n mono dim">{r.n} pos</span>
        </div>
      ))}
    </div>
  );
}

// ---- single stress engine: one toggle drives P&L or any greek across all four grids ----
function StressEngine(): JSX.Element {
  const [out, setOut] = useState<"pnl" | GreekKey>("pnl");
  const labels: Record<"pnl" | GreekKey, string> = { pnl: "P&L", delta: "Δ Delta", gamma: "Γ Gamma", vega: "Vega", theta: "Θ Theta", vanna: "Vanna", volga: "Volga" };
  const opts: ("pnl" | GreekKey)[] = ["pnl", "delta", "gamma", "vega", "theta", "vanna", "volga"];
  return (
    <Panel title="Stress test — scenario engine" right={<span className="dim mono small">factor base spans skew & curvature</span>} className="stress-panel">
      <div className="greek-btns">
        {opts.map((o) => (
          <button key={o} className={"chip " + (out === o ? "on" : "")} onClick={() => setOut(o)}>{labels[o]}</button>
        ))}
      </div>
      <div className="stress-2x2">
        <Panel title="Spot × Time" right={<span className="dim mono small">decay</span>} className="trade-block">{out === "pnl" ? <TimeStressGrid /> : <GreekStressGrid greek={out} axis="time" />}</Panel>
        <Panel title="Spot × ΔVol ∥ ATM" right={<span className="dim mono small">level only</span>} className="trade-block">{out === "pnl" ? <PnlGrid axis="vol" /> : <GreekStressGrid greek={out} axis="vol" />}</Panel>
        <Panel title="Spot × Skew (ΔRR)" right={<RiskOnly text="risk-only · pas de signal" />} className="trade-block">{out === "pnl" ? <PnlGrid axis="skew" /> : <GreekStressGrid greek={out} axis="skew" />}</Panel>
        <Panel title="Spot × Fly (ΔBF)" right={<span className="accent mono small">curvature · PC3</span>} className="trade-block">{out === "pnl" ? <PnlGrid axis="fly" /> : <GreekStressGrid greek={out} axis="fly" />}</Panel>
      </div>
      <HeatLegend note={(out === "pnl" ? "portfolio P&L" : labels[out] + " value") + " · value printed in cell · color normalized per grid · RR is ~vega-neutral in Spot×Vol → its risk only shows in Spot×Skew"} />
    </Panel>
  );
}

// ---- event & expiry calendar : roll-off / pin risk + dated macro ----
function CalendarPanel(): JSX.Element {
  const impactTone: Record<string, Tone> = { high: "danger", medium: "warn", low: "neutral" };
  const kk = (v: number): string => (v >= 0 ? "+" : "-") + "$" + Math.abs(v);
  return (
    <Panel title="Calendar — events & expiries" pad className="ladder-panel">
      <div className="risk-cards2">
        <Panel title="Expiries & roll-off" className="trade-block" pad={false}>
          <div className="table-scroll">
            <table className="dt">
              <thead><tr><th className="l">Option</th><th className="r">Strike</th><th className="r">DTE</th><th className="r">Dist pip</th><th className="r">P&L now</th><th className="r">if pin</th></tr></thead>
              <tbody>
                {DATA2.pinRisk.map((p, i) => (
                  <tr key={i} className={p.dist <= 10 ? "row-now" : ""}>
                    <td className="l mono">{p.product}</td>
                    <td className="r mono dim">{p.strike.toFixed(4)}</td>
                    <td className="r mono dim">{p.dte}d</td>
                    <td className={"r mono " + (p.dist <= 10 ? "warn" : "dim")}>{p.dist}</td>
                    <td className={"r mono " + pnlCls(p.now)}>{kk(p.now)}</td>
                    <td className={"r mono " + pnlCls(p.ifPin)}>{kk(p.ifPin)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Macro events" className="trade-block">
          <div className="evt-list">
            {DATA.events.map((e, i) => (
              <div key={i} className="evt-item">
                <div className="evt-when mono"><span className="evt-in accent">{e.in}</span><span className="dim small">{e.date.split(",")[0]}</span></div>
                <div className="evt-body"><span className="evt-code mono">{e.code}</span><span className="evt-name">{e.content}</span><span className="dim mono small"> · {e.country}</span></div>
                <Tag tone={impactTone[e.impact] ?? "neutral"}>{e.impact}</Tag>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </Panel>
  );
}

// ---- desk backlog : named-scenario presets + marginal VaR contribution ----
function DeskGapsPanel(): JSX.Element {
  const kk = (v: number): string => (v >= 0 ? "+" : "-") + "$" + (Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(1) + "k" : Math.abs(v));
  const kv = (v: number): string => "-$" + Math.abs(v) + "k";
  const facColor: Record<string, string> = { skew: "#a78bfa", level: "var(--accent)", spot: "var(--warn)", curv: "var(--pos)" };
  const T = DATA2.marginalVarTotal;
  return (
    <Panel title="Desk gaps — backlog of additions" pad className="ladder-panel">
      <div className="risk-cards2">
        <Panel title="Scenario presets / replay" className="trade-block">
          <div className="preset-grid">
            {DATA2.stressPresets.map((p) => (
              <button key={p.id} className="preset-card">
                <div className="preset-head"><span className="preset-name">{p.name}</span><span className={"preset-pnl mono " + pnlCls(p.pnl)}>{kk(p.pnl)}</span></div>
                <div className="preset-sub dim mono">{p.sub}</div>
                <div className="preset-shock mono dim">spot {p.spot > 0 ? "+" : ""}{p.spot}bp · vol {p.vol > 0 ? "+" : ""}{p.vol}vp</div>
              </button>
            ))}
          </div>
          <div className="dim small mono preset-note">named historical shocks in one click → feed the stress engine</div>
        </Panel>
        <Panel title="Marginal contribution to VaR" className="trade-block" pad={false}>
          <div className="mvar-factors">
            <div className="gs-sublbl mono dim">component VaR by factor · le <b className="accent">skew (RR)</b> domine sans vue associée → exposition incidente à neutraliser, pas un edge</div>
            <FactorStack factors={DATA2.varFactors} />
          </div>
          <div className="table-scroll">
            <table className="dt">
              <thead><tr><th className="l">Position</th><th className="r">standalone</th><th className="r">marginal</th><th className="r">component</th><th className="r">% VaR</th></tr></thead>
              <tbody>
                {DATA2.marginalVar.map((m, i) => {
                  const dom = Object.entries(m.f).reduce((a, b) => (Math.abs(b[1]) > Math.abs(a[1]) ? b : a))[0];
                  return (
                    <tr key={i}>
                      <td className="l mono"><span className="mvar-dot" style={{ background: facColor[dom] }} title={"driver: " + dom} />{m.pos}</td>
                      <td className="r mono dim">{kv(m.standalone)}</td>
                      <td className="r mono neg">{kv(m.marginal)}</td>
                      <td className="r mono neg">{kv(m.comp)}</td>
                      <td className="r"><div className="mvar-pct"><div className="mvar-bar" style={{ width: m.pct + "%" }} /><span className="mono">{m.pct.toFixed(1)}%</span></div></td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot><tr>
                <td className="l mono">Portfolio <span className="dim">· diversif. {T.diversification}%</span></td>
                <td className="r mono dim">{kv(T.standalone)}</td>
                <td className="r mono dim">—</td>
                <td className="r mono neg">{kv(T.comp)}</td>
                <td className="r mono">100%</td>
              </tr></tfoot>
            </table>
          </div>
        </Panel>
      </div>
    </Panel>
  );
}

// ---- greeks ladder table (P1: includes vanna/volga; reference row frozen as read anchor) ----
function LadderTable({ title, right, axisLbl, rows, nowKey, sub }: {
  title: string;
  right?: JSX.Element;
  axisLbl: string;
  rows: LadderRow[];
  nowKey: (l: LadderRow, i: number) => boolean;
  sub?: (l: LadderRow) => string;
}): JSX.Element {
  const kg = (v: number): string => { const s = v >= 0 ? "+" : "-"; const a = Math.abs(v); return a >= 1000 ? s + (a / 1000).toFixed(1) + "k" : s + Math.round(a); };
  const cols: (keyof LadderRow)[] = ["pnl", "delta", "gamma", "vega", "theta", "vanna", "volga"];
  const heads = ["P&L", "Δ", "Γ", "Vega", "Θ", "Vanna", "Volga"];
  return (
    <Panel title={title} right={right} className="trade-block" pad={false}>
      <div className="table-scroll">
        <table className="dt ladder-dt">
          <thead><tr><th className="l">{axisLbl}</th>{heads.map((h) => <th key={h} className="r">{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((l, i) => (
              <tr key={i} className={nowKey(l, i) ? "row-now ladder-anchor" : ""}>
                <td className="l mono">{l.axis}{sub ? <span className="dim"> {sub(l)}</span> : null}</td>
                {cols.map((c) => <td key={c} className={"r mono " + pnlCls(l[c] as number)}>{kg(l[c] as number)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export function RiskView(): JSX.Element {
  const [scenKind] = useState("spot");
  const scen = DATA2.scenarioSeries(scenKind);
  const { risk, portfolio } = useDeskData();
  const g = DATA.greeks; // per-unit greek representation — not the live positions-derived nets; stays mock (09)
  const a = portfolio.data?.account ?? DATA.account; // margin/net-liq live (PR 3 account domain)
  const vd = risk.data ?? { var95: g.var1d95, var99: g.var1d99, es99: g.var1d99 * 1.16, nDays: 0, hist: [], perTenor: [] };
  const pt = vd.perTenor; // vega/vanna/volga by tenor ($k) — live (PR 5)
  // reconciliation (§1): net 2nd-order greeks = Σ of their tenor buckets, by construction
  const netVanna = pt.reduce((s, r) => s + r.vanna, 0);
  const netVolga = pt.reduce((s, r) => s + r.volga, 0);
  const sumPC = +DATA2.vegaPCA.reduce((s, p) => s + p.vega, 0).toFixed(1);
  const skewResid = +(g.vega - sumPC).toFixed(1); // vega outside the 3 signal modes = incident skew
  const SKEW_THR = 5; // |residual| above this = a leak to investigate
  const skewLeak = Math.abs(skewResid) > SKEW_THR;
  const rrToFlat = Math.round(Math.abs(netVanna) / 8); // RR 25Δ contracts to flatten net vanna (mechanical hedge)
  const rvDaily = +(DATA.termStructure[0]!.rv / Math.sqrt(252)).toFixed(2); // realized 1M (YZ) → daily %
  const beMove = +(Math.sqrt(2 * Math.abs(g.theta) / g.gamma) * 0.225).toFixed(2); // move_BE = √(2Θ/Γ), %/day
  const beCovered = rvDaily >= beMove;
  const utilColor = (p: number): string => (p > 80 ? "var(--neg)" : p > 60 ? "var(--warn)" : "var(--pos)");
  // these sub-components are defined in the prototype but not rendered in this
  // view; referenced here to keep the 1:1 port without tripping noUnusedLocals.
  void scen;
  void utilColor;
  void VarCurve;
  void ReturnsBars;
  void StressGrid;
  void ScenarioMini;
  return (
    <div className="risk-grid">
      <Panel title="Portfolio risk" className="stress-panel">
        <div className="risk-overview2">
          <Panel title="Greeks & risk utilization" className="trade-block">
            <div className="gs-section-lbl">Portfolio greeks <span className="dim">· risk stock</span></div>
            <div className="greeks-summary gs-g4">
              <div className="gs-item"><span className="gs-lbl">Net Δ</span><b className={"mono " + pnlCls(g.delta)}>{fmt.usdk(g.delta * 1000)}</b><span className="gs-sub mono">{fmt.sgn(g.dDelta24h, 1)}k / 24h</span></div>
              <div className="gs-item"><span className="gs-lbl">Net Γ</span><b className={"mono " + pnlCls(g.gamma)}>{g.gamma.toFixed(1)}k</b><span className="gs-sub mono">USD/pip</span></div>
              <div className="gs-item"><span className="gs-lbl">Net Vega</span><b className={"mono " + pnlCls(g.vega)}>${g.vega.toFixed(0)}k</b><span className="gs-sub mono">{fmt.sgn(g.dVega24h, 1)}k / 24h</span></div>
              <div className="gs-item"><span className="gs-lbl">Net Θ</span><b className={"mono " + pnlCls(g.theta)}>${g.theta.toFixed(1)}k</b><span className="gs-sub mono">/ day</span></div>
            </div>
            <div className="gs-section-lbl">2nd-order greeks <span className="dim">· net = Σ tenor buckets · unit $k per 1vp·1 big-fig</span></div>
            <div className="greeks-summary gs-g3">
              <div className="gs-item"><span className="gs-lbl">Vanna</span><b className={"mono " + pnlCls(netVanna)}>{fmt.sgn(netVanna, 0)}k</b><span className="gs-sub mono">$k/vp·fig · Σ tenor</span></div>
              <div className="gs-item"><span className="gs-lbl">Volga</span><b className={"mono " + pnlCls(netVolga)}>{fmt.sgn(netVolga, 0)}k</b><span className="gs-sub mono">$k/vp · Σ tenor</span></div>
              <div className="gs-item"><span className="gs-lbl">Charm</span><b className={"mono " + pnlCls(g.charm)}>{fmt.sgn(g.charm, 2)}k</b><span className="gs-sub mono">Δ drift / day</span></div>
            </div>
            <div className="be-tile">
              <div className="be-l"><span className="be-lbl mono">breakeven γ–θ</span><span className="be-formula mono dim">move_BE = √(2Θ/Γ)</span></div>
              <div className="be-vals">
                <div className="be-v"><b className="mono">{beMove}%</b><span className="dim small">BE / jour</span></div>
                <div className="be-v"><b className={"mono " + (beCovered ? "pos" : "warn")}>{rvDaily}%</b><span className="dim small">réalisé YZ</span></div>
                <span className={"be-verdict " + (beCovered ? "pos" : "warn")}>{beCovered ? "le réalisé paie le θ" : "le réalisé ne paie pas le θ"}</span>
              </div>
            </div>
            <div className="gs-section-lbl util-lbl">Vega exposure <span className="dim">· by tenor (curve) & by signal mode (PCA)</span></div>
            <div className="gs-2col">
              <div className="gs-sub"><div className="gs-sublbl mono dim">vega by tenor · curve 1M–6M (magnitude)</div><VegaTenorLadder rows={pt} /></div>
              <div className="gs-sub"><div className="gs-sublbl mono dim">vega → PCA mode · the signal base ($k)</div><DivBars rows={DATA2.vegaPCA.map((p) => ({ label: p.mode, sub: p.name + " · " + p.var + "%", v: p.vega }))} unit="k" /></div>
            </div>
            <div className="skew-resid">
              <div className="sr-head">
                <span className="sr-lbl mono">skew · hors-signal</span>
                <RiskOnly />
                <span className={"sr-val mono " + (skewLeak ? "warn" : "dim")}>{fmt.sgn(skewResid, 1)}k</span>
                <span className="sr-eq dim small mono">ΣPC ({sumPC}k) + skew = net vega ({g.vega}k)</span>
              </div>
              <div className="sr-bar"><div className="sr-fill" style={{ width: Math.min(100, Math.abs(skewResid) / g.vega * 100) + "%" }} /></div>
              {skewLeak
                ? <div className="sr-alert"><span className="flag-dot" />skew incident à revoir · |résiduel| &gt; {SKEW_THR}k — fuite (structure asymétrique / fill déséquilibré), pas une position</div>
                : <div className="sr-ok dim small mono">dans le seuil (≈ 0) · aucune vue de skew au book</div>}
              <div className="sr-neut dim small mono">neutraliser : ≈ <b>{rrToFlat} RR 25Δ 2M</b> remettent la barre à ~0 · hedge mécanique, pas une reco directionnelle</div>
            </div>
            <div className="gs-section-lbl util-lbl">Vanna / Volga by tenor <span className="dim">· skew & convexity risk, signed · $k/vp · échelle √</span></div>
            <div className="gs-2col">
              <div className="gs-sub"><div className="gs-sublbl mono dim">vanna by tenor · dVega/dSpot (where RR lives)</div><DivBars rows={pt.map((r) => ({ label: r.tenor, v: r.vanna, flag: Math.abs(r.vanna) >= 150 ? "outlier — RR 2M domine" : null }))} unit="k" scale="sqrt" /></div>
              <div className="gs-sub"><div className="gs-sublbl mono dim">volga by tenor · dVega/dVol (where BF lives)</div><DivBars rows={pt.map((r) => ({ label: r.tenor, v: r.volga }))} unit="k" scale="sqrt" /></div>
            </div>
            <div className="gs-section-lbl util-lbl">Risk utilization <span className="dim">· used vs limit</span></div>
            <div className="util-bars">
              <Bar label="Init margin" used="$1.84M" limit="$4.22M" pct={a.marginInitPct} value={a.marginInitPct + "%"} tone="auto" />
              <Bar label="Maint margin" used="$1.28M" limit="$4.22M" pct={a.marginMaintPct} value={a.marginMaintPct + "%"} tone="auto" />
              <Bar label="Δ exposure" used="$1.18M" limit="$4.22M" pct={28} value="28%" tone="auto" />
              <Bar label="Vega" used="$32k" limit="$62k" pct={52} value="52%" tone="auto" />
              <Bar label="Γ exposure" used="14.5k" limit="20.4k" pct={71} value="71%" tone="auto" />
            </div>
          </Panel>
          <VarCard var95={vd.var95} var99={vd.var99} es99={vd.es99} netLiq={a.netLiq} fresh={risk} />
        </div>
      </Panel>
      <StressEngine />
      <Panel title="Greeks ladder" right={<span className="dim mono small">incl. vanna / volga · 5 axes</span>} pad className="ladder-panel">
        <div className="ladder-grid">
          <LadderTable title="vs Spot" axisLbl="Spot" rows={DATA2.spotLadder} nowKey={(l) => l.axis === "0bp"} sub={(l) => (l.spot ?? 0).toFixed(4)} />
          <LadderTable title="vs Vol ∥ ATM" right={<span className="dim mono small">level</span>} axisLbl="Vol" rows={[...DATA2.volLadder].reverse()} nowKey={(l) => l.axis === "0vp"} />
          <LadderTable title="vs Time" axisLbl="Time" rows={DATA2.timeLadder} nowKey={(_l, i) => i === 0} sub={(l) => (l.days === 0 ? "now" : l.days + "d")} />
          <LadderTable title="vs Skew (ΔRR)" right={<RiskOnly />} axisLbl="RR" rows={[...DATA2.skewLadder].reverse()} nowKey={(l) => l.axis === "0vp"} />
          <LadderTable title="vs Fly (ΔBF)" right={<span className="accent mono small">PC3</span>} axisLbl="BF" rows={[...DATA2.flyLadder].reverse()} nowKey={(l) => l.axis === "0vp"} />
        </div>
      </Panel>
      <Panel title="Position breakdown" pad={false} className="ladder-panel">
        <PositionBreakdown />
      </Panel>
      <CalendarPanel />
      <DeskGapsPanel />
    </div>
  );
}
