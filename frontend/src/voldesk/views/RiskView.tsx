/**
 * VOLDESK — Risk (matrix, stress, ladder, scenarios). Ported 1:1 from the
 * prototype's `js/views_risk.jsx` (global-window JSX) into typed ES modules.
 * Exports only RiskView; all sub-components stay local (lint).
 */
import { useEffect, useRef, useState } from "react";
import { fetchGreeksLadder, fetchMarginalVar, fetchPinRisk, fetchStressGrid } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { Panel, Tag } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { gk$, pnlCls } from "../components/format";
import type { Tone } from "../components/format";
import { fmt } from "../data";
import type { Position } from "../data";
import { type HistBin, useDeskData } from "../data/deskData";
import type { Fresh } from "../data/freshness";
import { adaptGreeksLadder, adaptMarginalVar, adaptPinRisk, adaptStressGrid, type LiveLadder, type MarginalVarData, type PinRiskRow, type StressGridData } from "../data/live/portfolio";

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

// neutral "risk-only" badge — marks surfaces that carry exposure but NO signal (skew). Never the signal palette.
function RiskOnly({ text = "risk-only" }: { text?: string }): JSX.Element {
  return <span className="risk-only-badge mono">{text}</span>;
}

// per-panel data-source indicator: live (fresh) / stale / no-data / mock.
// Reuses the .status-dot/.pulse styles from the VaR card's live pill.
function PanelLive({ status }: { status: Fresh<unknown>["status"] | "mock" }): JSX.Element {
  const cfg = {
    live: { c: "var(--pos)", t: "live", pulse: true },
    stale: { c: "var(--warn)", t: "stale", pulse: false },
    missing: { c: "var(--muted)", t: "no data", pulse: false },
    mock: { c: "var(--muted)", t: "mock", pulse: false },
  }[status];
  return (
    <span
      className="panel-live dim mono small"
      title={status === "mock" ? "placeholder data — no live endpoint yet" : "data feed status"}
    >
      <span className={"status-dot" + (cfg.pulse ? " pulse" : "")} style={{ background: cfg.c }} /> {cfg.t}
    </span>
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

function EmpiricalHist({ hist, var95, var99, es99, retk, letter, h = 88 }: { hist: HistBin[]; var95: number; var99: number; es99: number; retk: number; letter: string; h?: number }): JSX.Element {
  const w = 340;
  if (!hist.length) {
    return (
      <svg className="var-curve-svg" width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
        <text x={w / 2} y={h / 2} fill="var(--text-faint)" fontSize="10" fontFamily="var(--mono)" textAnchor="middle">distribution accumulating…</text>
      </svg>
    );
  }
  // Empirical P&L histogram in $k space. VaR/ES are losses by definition →
  // pin them to the negative (left) side as −|value|, whatever sign the backend
  // sends, so they always sit left of the mean (a +95% return is fine; it's the
  // −95% loss tail we plot here).
  const l95 = -Math.abs(var95), l99 = -Math.abs(var99), les = -Math.abs(es99);
  const marks = [0, l95, l99, les, retk];
  // Symmetric range about 0 so the mean (µ) line is centered; the widest of the
  // bins / VaR marks sets the half-width.
  const span = Math.max(...hist.flatMap((b) => [Math.abs(b.lo), Math.abs(b.hi)]), ...marks.map((m) => Math.abs(m)), 1);
  const lo = -span, hi = span;
  const rng = hi - lo || 1;
  const padL = 12, padR = 8;
  const px = (v: number): number => padL + ((v - lo) / rng) * (w - padL - padR);
  const baseY = h - 24, topY = 14, maxH = baseY - topY;
  const maxC = Math.max(...hist.map((b) => b.count)) || 1;
  return (
    <svg className="var-curve-svg" width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <rect x={px(lo)} y={topY} width={Math.max(0, px(l95) - px(lo))} height={maxH} fill="var(--neg)" fillOpacity="0.08" />
      {hist.map((b, i) => {
        const x = px(b.lo), bw = Math.max(1, px(b.hi) - px(b.lo)), hh = (b.count / maxC) * maxH;
        const loss = b.hi <= l95;
        return <rect key={i} x={x + bw * 0.06} y={baseY - hh} width={bw * 0.88} height={hh} fill={loss ? "var(--neg)" : "var(--muted)"} fillOpacity={loss ? 0.55 : 0.5} />;
      })}
      <line x1={px(lo)} x2={px(hi)} y1={baseY} y2={baseY} stroke="var(--border)" strokeWidth="1" />
      <line x1={px(0)} x2={px(0)} y1={topY} y2={baseY} stroke="var(--fg)" strokeWidth="1.1" strokeOpacity="0.35" strokeDasharray="2 2" />
      <text x={px(0)} y={baseY + 11} fill="var(--fg)" fontSize="8" fontFamily="var(--mono)" textAnchor="middle" opacity="0.55">µ</text>
      <line x1={px(retk)} x2={px(retk)} y1={topY} y2={baseY} stroke="var(--fg)" strokeWidth="1.6" />
      <text x={px(retk)} y={topY - 3} fill="var(--fg)" fontSize="8" fontWeight="700" fontFamily="var(--mono)" textAnchor="middle">{letter}</text>
      <line x1={px(l95)} x2={px(l95)} y1={topY + 8} y2={baseY} stroke="var(--warn)" strokeWidth="1.3" strokeDasharray="4 3" />
      <text x={px(l95)} y={baseY + 11} fill="var(--warn)" fontSize="7.5" fontFamily="var(--mono)" textAnchor="middle">95%</text>
      <line x1={px(l99)} x2={px(l99)} y1={topY + 8} y2={baseY} stroke="var(--neg)" strokeWidth="1.3" strokeDasharray="4 3" />
      <text x={px(l99)} y={baseY + 11} fill="var(--neg)" fontSize="7.5" fontFamily="var(--mono)" textAnchor="middle">99%</text>
      <line x1={px(les)} x2={px(les)} y1={baseY - 14} y2={baseY} stroke="#b3402f" strokeWidth="1.4" strokeDasharray="2 2" />
      <circle cx={px(les)} cy={baseY - 14} r="3.5" fill="#b3402f" stroke="var(--bg)" strokeWidth="1" />
      <text x={px(les)} y={baseY + 11} fill="#c4584a" fontSize="7.5" fontFamily="var(--mono)" textAnchor="middle">ES</text>
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

function VarCard({ var95, var99, es99, netLiq, hist, fresh }: { var95: number; var99: number; es99: number; netLiq: number; hist: HistBin[]; fresh: Fresh<unknown> }): JSX.Element {
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
  // empirical 1d distribution scaled to the selected horizon (same √t the table uses).
  const histScaled = hist.map((b) => ({ lo: b.lo * sel.m, hi: b.hi * sel.m, count: b.count }));
  const letter = ({ "1d": "D", "1w": "W", "1M": "M", "1Y": "Y" } as Record<string, string>)[sel.id] ?? "D";
  return (
    <Panel title="Value at Risk" dataPp="var" right={<PanelLive status={fresh.status} />} className="stress-panel">
      <div className="var-1x3">
        <Panel title="VaR table" dataPp="var-table" right={<FreshBadge fresh={fresh} label="historical 1d" />} className="trade-block" pad={false}>
          <div className="var-tf-group">
            {rows.map((r) => (
              <button
                key={r.id}
                className={"chip " + (r.id === tf ? "on" : "")}
                onClick={() => setTf(r.id)}
              >
                {r.id}
              </button>
            ))}
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
        </Panel>
        <Panel title="P&L distribution" dataPp="var-chart" right={<FreshBadge fresh={fresh} label="empirical" />} className="trade-block">
          <div className="ret-chart">
            <div className="ret-title">P&L distribution <span className="dim">· {sel.lbl} · empirical</span></div>
            <EmpiricalHist hist={histScaled} var95={c.v95} var99={c.v99} es99={c.es} retk={c.retk} letter={letter} />
            <div className="hist-leg dim mono">
              <span><i className="lg-line mu" />return (D/W/M/Y)</span>
              <span><i className="lg-line w" />VaR 95%</span>
              <span><i className="lg-line n" />VaR 99%</span>
              <span><i className="lg-dot" />ES (tail mean)</span>
            </div>
          </div>
        </Panel>
        <MarginalVarPanel />
      </div>
    </Panel>
  );
}

type GreekKey = "delta" | "gamma" | "vega" | "theta" | "vanna" | "volga";

// Per-axis 2nd-dimension label for the live stress grids.
const STRESS_AXIS_LABEL: Record<string, string> = {
  "spot-vol": "ΔVol ∥ ATM",
  "spot-time": "Time",
  "spot-skew": "ΔRR · skew",
  "spot-fly": "ΔBF · fly",
};

const stressCell = (v: number, max: number): string => {
  const t = Math.max(-1, Math.min(1, v / max));
  return t >= 0
    ? `oklch(0.62 ${0.02 + 0.13 * t} 150 / ${0.12 + 0.6 * t})`
    : `oklch(0.58 ${0.02 + 0.15 * -t} 25 / ${0.12 + 0.6 * -t})`;
};
const stressKg = (v: number): string => {
  const s = v >= 0 ? "+" : "-";
  const a = Math.abs(v);
  return a >= 1000 ? s + (a / 1000).toFixed(1) + "k" : s + a.toFixed(0);
};

// One live (axis, output) matrix. Rows = the 2nd-axis bins, cols = spot bp bins.
function LiveStressGrid({ d, status }: { d: StressGridData | null; status: Fresh<unknown>["status"] }): JSX.Element {
  if (!d || !d.grid.length) {
    return <div className="heat-empty dim mono small">{status === "missing" ? "no book / no spot" : "loading…"}</div>;
  }
  const max = Math.max(...d.grid.flat().map(Math.abs)) || 1;
  const rowLbl = (v: number): string => (d.rowUnit === "d" ? v + "d" : (v > 0 ? "+" : "") + v + "vp");
  // Column display order = secondary axis ascending, so the smallest bucket
  // (e.g. 0d for the time grid) is leftmost; cell lookups follow the same ri.
  const cols = d.rowBins.map((r, ri) => ({ r, ri })).sort((x, y) => x.r - y.r);
  return (
    // Transposed: ΔSpot on the ROWS, the secondary axis (vol/time/skew/fly) on
    // the columns. cell(si, ri) = grid[ri][si].
    <table className="heatmap stress">
      <thead>
        <tr>
          <th className="corner">ΔSpot \ Δ{STRESS_AXIS_LABEL[d.axis] ?? d.axis}</th>
          {cols.map(({ r, ri }) => <th key={ri}>{rowLbl(r)}<span className="th-sub">&nbsp;</span></th>)}
        </tr>
      </thead>
      <tbody>
        {d.spotBins.map((s, si) => (
          <tr key={si}>
            <th>{s > 0 ? "+" : ""}{s}bp</th>
            {cols.map(({ r, ri }) => {
              const v = d.grid[ri]?.[si] ?? NaN;
              const center = (r ?? NaN) === 0 && (s ?? NaN) === 0;
              return <td key={ri} className={center ? "center-cell" : ""} style={{ background: center ? "var(--bg-3)" : stressCell(v, max) }}>{stressKg(v)}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const STRESS_AXES = ["spot-time", "spot-vol", "spot-skew", "spot-fly"] as const;

// Stress engine: one output toggle drives P&L or any greek across the four
// spot-x grids. Owns the fetch so it can show a live indicator; refetches on
// output change (the four axes in parallel).
function PositionBreakdown({ positions }: { positions: Position[] }): JSX.Element {
  const rows = positions;
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
              <th className="l grp-fix">Trade</th><th className="l grp-fix">Contract</th><th className="l grp-fix">Product</th><th className="l grp-fix">Structure</th><th className="l grp-fix">Side</th>
              <th className="r grp-fix">Tenor</th><th className="r grp-fix">IV</th><th className="r grp-fix">Nominal €</th>
              <th className="r grp-grk col-grp">Δ$</th><th className="r grp-grk">Γ</th><th className="r grp-grk">Vega</th><th className="r grp-grk">Θ</th><th className="r grp-grk">Vanna</th><th className="r grp-grk col-grp-end">Volga</th>
              <th className="r grp-pnl col-grp">P&L 1d</th><th className="r grp-pnl">P&L 1w</th><th className="r grp-pnl col-grp-end">P&L 1M</th>
              <th className="r grp-att col-grp">Δ contrib</th><th className="r grp-att">Θ contrib</th><th className="r grp-att">Vega contrib</th><th className="r grp-att col-grp-end">Residual</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={22} className="l dim small mono" style={{ padding: "16px 10px" }}>no open positions</td></tr>
            )}
            {rows.map((p) => (
              <tr key={p.id}>
                <td className="l grp-fix mono dim">{p.tradeId || p.packageId || "—"}</td>
                <td className="l grp-fix mono dim">{p.conId || "—"}</td>
                <td className="l grp-fix">{p.product || "—"}</td>
                <td className="l grp-fix"><span className="sym">{p.structure}</span></td>
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

function StressEngine(): JSX.Element {
  const [out, setOut] = useState<"pnl" | GreekKey>("pnl");
  const labels: Record<"pnl" | GreekKey, string> = { pnl: "P&L", delta: "Δ Delta", gamma: "Γ Gamma", vega: "Vega", theta: "Θ Theta", vanna: "Vanna", volga: "Volga" };
  const opts: ("pnl" | GreekKey)[] = ["pnl", "delta", "gamma", "vega", "theta", "vanna", "volga"];
  const live = useFetch<(StressGridData | null)[]>(
    () => Promise.all(STRESS_AXES.map((a) => fetchStressGrid(a, out).then(adaptStressGrid))),
    120_000,
  );
  const reload = live.reload;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    reload();
  }, [out, reload]);
  const g = live.data ?? [null, null, null, null];
  return (
    <Panel title="Stress test" dataPp="stress" right={<PanelLive status={live.status} />} className="stress-panel">
      <div className="greek-btns">
        {opts.map((o) => (
          <button key={o} className={"chip " + (out === o ? "on" : "")} onClick={() => setOut(o)}>{labels[o]}</button>
        ))}
      </div>
      <div className="stress-2x2">
        <Panel title="Spot × Time" right={<span className="dim mono small">decay</span>} className="trade-block"><LiveStressGrid d={g[0] ?? null} status={live.status} /></Panel>
        <Panel title="Spot × ΔVol ∥ ATM" right={<span className="dim mono small">level only</span>} className="trade-block"><LiveStressGrid d={g[1] ?? null} status={live.status} /></Panel>
        <Panel title="Spot × Skew (ΔRR)" className="trade-block"><LiveStressGrid d={g[2] ?? null} status={live.status} /></Panel>
        <Panel title="Spot × Fly (ΔBF)" className="trade-block"><LiveStressGrid d={g[3] ?? null} status={live.status} /></Panel>
      </div>
    </Panel>
  );
}

// ---- Expiries & roll-off (pin risk) — own panel for the Greeks 2×2 grid ----
function PinRiskTable(): JSX.Element {
  const kk = (v: number): string => (v >= 0 ? "+" : "-") + "$" + (Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(1) + "k" : Math.round(Math.abs(v)));
  const pin = useFetch<PinRiskRow[]>(() => fetchPinRisk().then(adaptPinRisk), 120_000);
  const pinRows = pin.data ?? [];
  return (
    <Panel title="Expiries & roll-off" dataPp="pin-risk" right={<PanelLive status={pin.status} />} className="trade-block" pad={false}>
      <div className="table-scroll">
        <table className="dt">
          <thead><tr><th className="l">Option</th><th className="r">Strike</th><th className="r">DTE</th><th className="r">Dist pip</th><th className="r">P&L now</th><th className="r">if pin</th></tr></thead>
          <tbody>
            {pinRows.length === 0 ? (
              <tr><td className="l dim mono small" colSpan={6}>{pin.status === "missing" ? "no open options" : "loading…"}</td></tr>
            ) : pinRows.map((p, i) => (
              <tr key={i} className={p.distPips <= 10 ? "row-now" : ""}>
                <td className="l mono">{p.product}</td>
                <td className="r mono dim">{p.strike.toFixed(4)}</td>
                <td className="r mono dim">{p.dte}d</td>
                <td className={"r mono " + (p.distPips <= 10 ? "warn" : "dim")}>{p.distPips}</td>
                <td className={"r mono " + pnlCls(p.pnlNow)}>{kk(p.pnlNow)}</td>
                <td className={"r mono " + pnlCls(p.pnlAtPin)}>{kk(p.pnlAtPin)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

// ---- macro events calendar ----
function CalendarPanel(): JSX.Element {
  const impactTone: Record<string, Tone> = { high: "danger", medium: "warn", low: "neutral" };
  const { trade } = useDeskData();
  const events = trade.data?.events ?? [];
  return (
    <Panel title="Macro events" dataPp="risk-macro" right={<PanelLive status={trade.status} />} className="risk-macro-panel" scroll>
      <div className="evt-list">
        {events.length === 0 ? (
          <div className="dim mono small">no scheduled events</div>
        ) : events.map((e, i) => (
          <div key={i} className="evt-item">
            <div className="evt-when mono"><span className="evt-in accent">{e.in}</span><span className="dim small">{e.date.split(",")[0]}</span></div>
            <div className="evt-body"><span className="evt-code mono">{e.code}</span><span className="evt-name">{e.content}</span><span className="dim mono small"> · {e.country}</span></div>
            <Tag tone={impactTone[e.impact] ?? "neutral"}>{e.impact}</Tag>
          </div>
        ))}
      </div>
    </Panel>
  );
}

// Marginal contribution to VaR — live (/portfolio/marginal-var); empty state
// until a book + ≥5d history exist.
function MarginalVarPanel(): JSX.Element {
  const live = useFetch<MarginalVarData>(() => fetchMarginalVar().then(adaptMarginalVar), 120_000);
  const d = live.data;
  const rows = d?.rows ?? [];
  const money = (v: number): string => { const a = Math.abs(v); const m = a >= 1000 ? (a / 1000).toFixed(1) + "k" : Math.round(a).toString(); return (v >= 0 ? "-$" : "+$") + m; };
  return (
    <Panel title="Marginal contribution to VaR" dataPp="marginal-var" right={<PanelLive status={live.status} />} className="trade-block" pad={false}>
      <div className="table-scroll">
        <table className="dt">
          <thead><tr><th className="l">Trade</th><th className="l">Product</th><th className="r">standalone</th><th className="r">component</th><th className="r">% VaR</th></tr></thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td className="l dim mono small" colSpan={5}>{live.status === "missing" ? "no open book" : d && d.nDays < 5 ? "accumulating history (≈5d)…" : "loading…"}</td></tr>
            ) : rows.map((m, i) => (
              <tr key={i}>
                <td className="l mono dim">{m.trade}</td>
                <td className="l mono">{m.label}</td>
                <td className="r mono dim">{money(m.standalone)}</td>
                <td className={"r mono " + (m.component >= 0 ? "neg" : "pos")}>{money(m.component)}</td>
                <td className="r mono">{m.pct.toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
          {rows.length > 0 && d ? (
            <tfoot><tr>
              <td className="l mono" />
              <td className="l mono">Total</td>
              <td className="r mono dim">—</td>
              <td className="r mono neg">{money(d.portfolioVar)}</td>
              <td className="r mono">100%</td>
            </tr></tfoot>
          ) : null}
        </table>
      </div>
    </Panel>
  );
}

// ---- greeks ladder table (P1: includes vanna/volga; reference row frozen as read anchor) ----
function LiveLadderTable({ title, right, axisLbl, d, status }: {
  title: string;
  right?: JSX.Element;
  axisLbl: string;
  d: LiveLadder | null;
  status: Fresh<unknown>["status"];
}): JSX.Element {
  const kg = (v: number): string => { const s = v >= 0 ? "+" : "-"; const a = Math.abs(v); return a >= 1000 ? s + (a / 1000).toFixed(1) + "k" : s + Math.round(a); };
  const heads = ["P&L", "Δ", "Γ", "Vega"];
  const rows = d?.rows ?? [];
  return (
    <Panel title={title} right={right} className="trade-block" pad={false}>
      <div className="table-scroll">
        <table className="dt ladder-dt">
          <thead><tr><th className="l">{axisLbl}</th>{heads.map((h) => <th key={h} className="r">{h}</th>)}</tr></thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td className="l dim mono small" colSpan={5}>{status === "missing" ? "no book / no spot" : "loading…"}</td></tr>
            ) : rows.map((l, i) => (
              <tr key={i} className={l.isNow ? "row-now ladder-anchor" : ""}>
                <td className="l mono">{l.label}{l.spot != null ? <span className="dim"> {l.spot.toFixed(4)}</span> : null}</td>
                <td className={"r mono " + pnlCls(l.pnl)}>{kg(l.pnl)}</td>
                <td className={"r mono " + pnlCls(l.delta)}>{kg(l.delta)}</td>
                <td className={"r mono " + pnlCls(l.gamma)}>{kg(l.gamma)}</td>
                <td className={"r mono " + pnlCls(l.vega)}>{kg(l.vega)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

const LADDER_AXES = ["spot", "vol", "time", "skew", "fly"] as const;

// All five greek ladders, each a full-BS reval along one axis (live).
function LiveLadders(): JSX.Element {
  const live = useFetch<LiveLadder[]>(
    () => Promise.all(LADDER_AXES.map((a) => fetchGreeksLadder(a).then(adaptGreeksLadder))),
    120_000,
  );
  const L = live.data ?? [];
  const at = (i: number): LiveLadder | null => L[i] ?? null;
  return (
    <Panel title="Greeks ladder" dataPp="greeks-ladder" right={<><span className="dim mono small">5 axes · full BS reval</span> <PanelLive status={live.status} /></>} pad className="ladder-panel">
      <div className="ladder-grid">
        <LiveLadderTable title="vs Spot" axisLbl="Spot" d={at(0)} status={live.status} />
        <LiveLadderTable title="vs Vol ∥ ATM" right={<span className="dim mono small">level</span>} axisLbl="Vol" d={at(1)} status={live.status} />
        <LiveLadderTable title="vs Time" axisLbl="Time" d={at(2)} status={live.status} />
        <LiveLadderTable title="vs Skew (ΔRR)" right={<RiskOnly />} axisLbl="RR" d={at(3)} status={live.status} />
        <LiveLadderTable title="vs Fly (ΔBF)" axisLbl="BF" d={at(4)} status={live.status} />
      </div>
    </Panel>
  );
}

export function RiskView(): JSX.Element {
  const { risk, portfolio, trade } = useDeskData();
  // All live. Net greeks + caps from the trade domain; account from portfolio.
  // No DATA mock fallback — absent data reads as 0 / "—", never fabricated.
  const ng = trade.data?.greeks;
  const lim = trade.data?.limits;
  const a = portfolio.data?.account;
  const nd = ng?.netDelta ?? 0, ngm = ng?.netGamma ?? 0, nv = ng?.netVega ?? 0, nt = ng?.netTheta ?? 0;
  // No live VaR yet (history < min window → backend returns null): show honest
  // zeros + "building window…", NOT a fabricated mock VaR scaled across horizons.
  const vd = risk.data ?? { var95: 0, var99: 0, es99: 0, nDays: 0, hist: [], perTenor: [] };
  const pt = vd.perTenor; // vega/vanna/volga by tenor ($k) — live (PR 5)
  // net 2nd-order greeks = Σ of their tenor buckets (live), by construction
  const netVanna = pt.reduce((s, r) => s + r.vanna, 0);
  const netVolga = pt.reduce((s, r) => s + r.volga, 0);
  const utilColor = (p: number): string => (p > 80 ? "var(--neg)" : p > 60 ? "var(--warn)" : "var(--pos)");
  const pctOf = (used: number, cap: number | undefined): number => (cap && cap > 0 ? Math.min(100, (Math.abs(used) / cap) * 100) : 0);
  // Greek caps — desk risk policy, all anchored to LIVE inputs (no mock):
  //   • Δ band  = DELTA_BAND_PCT of net-liq (NLV). A vol book is delta-hedged;
  //               the band is the rehedge/alert trigger, sized to capital.
  //   • Vega    = the configured max book vega (risk config max_book_vega_usd).
  //   • Γ cap   = Δ band / GAMMA_STRESS_PIPS, i.e. a 1-big-figure spot move must
  //               not drift delta (Γ·ΔS) beyond the Δ band.
  const NLV = a?.netLiq ?? 0;
  const DELTA_BAND_PCT = 0.10;     // 10% of capital
  const GAMMA_STRESS_PIPS = 100;   // 1 big figure
  const deltaCap = DELTA_BAND_PCT * NLV;
  const vegaCap = lim?.vegaCapUsd ?? 0;
  const gammaCap = deltaCap / GAMMA_STRESS_PIPS;
  const capk = (c: number): string => (c > 0 ? fmt.usdk(c) : "—");
  // Risk-utilization rows : name · used / cap · % (used vs the cap). All live.
  const utilRows = [
    { label: "Init margin", used: a ? fmt.usd(a.marginInit) : "—", limit: a ? fmt.usd(a.netLiq) : "—", pct: a?.marginInitPct ?? 0 },
    { label: "Maint margin", used: a ? fmt.usd(a.marginMaint) : "—", limit: a ? fmt.usd(a.netLiq) : "—", pct: a?.marginMaintPct ?? 0 },
    { label: "Δ exposure", used: fmt.usdk(Math.abs(nd)), limit: capk(deltaCap), pct: pctOf(nd, deltaCap) },
    { label: "Vega", used: fmt.usdk(Math.abs(nv)), limit: capk(vegaCap), pct: pctOf(nv, vegaCap) },
    { label: "Γ exposure", used: fmt.usdk(Math.abs(ngm)), limit: capk(gammaCap), pct: pctOf(ngm, gammaCap) },
  ];
  // these sub-components are defined in the prototype but not rendered in this
  // view; referenced here to keep the 1:1 port without tripping noUnusedLocals.
  void VarCurve;
  void ReturnsBars;
  return (
    <div className="risk-grid">
      <div className="risk-row1">
        <div className="risk-left-col">
        <Panel title="Greeks" dataPp="greeks-wrap" right={<PanelLive status={risk.status} />} className="stress-panel">
          <div className="greeks-2x2">
            <Panel title="Portfolio greeks" dataPp="greeks-net" right={<PanelLive status={trade.status} />} className="trade-block">
              <table className="dt greeks-table">
                <thead><tr><th className="l">Greek</th><th className="r">Net value</th></tr></thead>
                <tbody>
                  <tr><td className="l">Δ <em className="unit">USD</em></td><td className={"r mono " + pnlCls(nd)}>{gk$(nd)}</td></tr>
                  <tr><td className="l">Γ <em className="unit">USD/pip</em></td><td className={"r mono " + pnlCls(ngm)}>{gk$(ngm)}</td></tr>
                  <tr><td className="l">Vega <em className="unit">$/vp</em></td><td className={"r mono " + pnlCls(nv)}>{gk$(nv)}</td></tr>
                  <tr><td className="l">Θ <em className="unit">$/day</em></td><td className={"r mono " + pnlCls(nt)}>{gk$(nt)}</td></tr>
                  <tr><td className="l">Vanna <em className="unit">$k/vp·fig</em></td><td className={"r mono " + pnlCls(netVanna)}>{fmt.sgn(netVanna, 0)}k</td></tr>
                  <tr><td className="l">Volga <em className="unit">$k/vp</em></td><td className={"r mono " + pnlCls(netVolga)}>{fmt.sgn(netVolga, 0)}k</td></tr>
                </tbody>
              </table>
            </Panel>
            <Panel title="Vega / Vanna / Volga" dataPp="vvv-tenor" right={<PanelLive status={risk.status} />} className="trade-block">
              <table className="dt greeks-table">
                <thead><tr><th className="l">Tenor</th><th className="r">Vega</th><th className="r">Vanna</th><th className="r">Volga</th></tr></thead>
                <tbody>
                  {pt.length === 0 && <tr><td colSpan={4} className="l dim small mono">no book</td></tr>}
                  {pt.map((r) => (
                    <tr key={r.tenor}>
                      <td className="l">{r.tenor}</td>
                      <td className={"r mono " + pnlCls(r.vega)}>{fmt.sgn(r.vega, 1)}k</td>
                      <td className={"r mono " + pnlCls(r.vanna)}>{fmt.sgn(r.vanna, 1)}k</td>
                      <td className={"r mono " + pnlCls(r.volga)}>{fmt.sgn(r.volga, 2)}k</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
            <Panel title="Risk utilization" dataPp="risk-util" right={<PanelLive status={portfolio.status} />} className="trade-block">
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
            </Panel>
            <PinRiskTable />
          </div>
        </Panel>
          <CalendarPanel />
        </div>
        <VarCard var95={vd.var95} var99={vd.var99} es99={vd.es99} netLiq={a?.netLiq ?? 0} hist={vd.hist} fresh={risk} />
      </div>
      <StressEngine />
      <LiveLadders />
      <Panel title="Position breakdown" dataPp="position-breakdown" right={<PanelLive status={portfolio.status} />} pad={false} className="ladder-panel">
        <PositionBreakdown positions={portfolio.data?.positions ?? []} />
      </Panel>
    </div>
  );
}
