/**
 * VOLDESK — Portfolio (capital, performance, survival metric, realized
 * attribution bridge, book composition). Ported from the prototype's
 * `js/views_portfolio.jsx` (global-window pattern) into typed ES modules.
 * 1:1 port — same JSX, same classNames, same logic. Mock data for now.
 */
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import {
  fetchEquityCurve,
  fetchGreekPnlHistory,
  fetchPnlAttribution,
  fetchPnlAttributionMatrix,
  fetchTradeMarkers,
  fetchValuationHistory,
} from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { useTicks } from "../../hooks/streams";
import { Panel } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { gk$, pnlCls } from "../components/format";
import { groupByTradeId, structureName, structureSide } from "../components/tradeGrouping";
import { DATA, DATA2, fmt } from "../data";
import type { Cash, PerfStats } from "../data";
import { useDeskData } from "../data/deskData";
import {
  adaptAttributionMatrix,
  adaptEquityCurve,
  adaptGreekPnlHistory,
  adaptPositionAttribution,
  adaptTradeMarkers,
  adaptValuationHistory,
  type AttribMatrix,
  type AttribRow,
  type EquityPoint,
  type GreekKey,
  type GreekSeries,
  type PositionAttribMatrix,
  type PositionAttribRow,
  type TradeEvent,
  type ValuationKey,
  type ValuationSeries,
} from "../data/live/portfolio";

// ─── Performance charts: fixed time axis with gaps ───────────────────────────
// The net-liq series is resampled onto a FIXED grid of GRID_N samples spanning the
// window's whole domain [t0, t1] (e.g. "7 days ago → now"). Samples outside the
// data's real time range stay null, so the line simply stops and leaves an empty
// zone — no more stretching a handful of points across the full width. Every
// timeframe therefore renders the same number of samples on the same-shaped axis.
const GRID_N = 90;
const DAY_MS = 86_400_000;
const WINDOW_DAYS: Record<string, number | null> = {
  "1D": 1,
  "7D": 7,
  "30D": 30,
  "1Y": 365,
  all: null,
};

interface EqGrid {
  series: (number | null)[]; // GRID_N samples across the fixed domain (null = no data)
  ticks: { f: number; label: string }[]; // x-axis marks (fraction 0..1 + label)
  t0: number; // domain start (epoch ms) — for positioning trade markers
  t1: number; // domain end (epoch ms)
}

function buildEquityGrid(points: EquityPoint[], windowDays: number | null, now: number): EqGrid {
  if (points.length === 0) return { series: [], ticks: [], t0: now, t1: now };
  const firstT = points[0]!.t;
  const lastT = points[points.length - 1]!.t;
  const t1 = windowDays != null ? now : lastT;
  const t0 = windowDays != null ? now - windowDays * DAY_MS : firstT;
  const span = t1 - t0 || 1;
  // linear interpolation at absolute time t, or null outside the real data range
  const at = (t: number): number | null => {
    if (t < firstT || t > lastT) return null;
    let k = 1;
    while (k < points.length && points[k]!.t < t) k++;
    const a = points[k - 1]!;
    const b = points[k] ?? a;
    const f = b.t === a.t ? 0 : (t - a.t) / (b.t - a.t);
    return a.v + f * (b.v - a.v);
  };
  const series: (number | null)[] = [];
  for (let i = 0; i < GRID_N; i++) series.push(at(t0 + (i / (GRID_N - 1)) * span));
  const ticks: { f: number; label: string }[] = [];
  if (windowDays != null) {
    const marks = windowDays <= 1 ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1];
    for (const f of marks) {
      const ago = windowDays * (1 - f);
      ticks.push({
        f,
        label:
          f === 1 ? "now" : windowDays <= 1 ? `-${Math.round(ago * 24)}h` : `-${Math.round(ago)}d`,
      });
    }
  } else {
    ticks.push({ f: 0, label: "start" }, { f: 1, label: "now" });
  }
  return { series, ticks, t0, t1 };
}

const p2 = (n: number): string => String(n).padStart(2, "0");
const fmtTs = (t: number): string => {
  const d = new Date(t);
  return `${p2(d.getUTCDate())}/${p2(d.getUTCMonth() + 1)} ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
};

// Trade open/close markers overlaid on a time-series chart: ▲ open (entry, accent),
// ● close (coloured by realized P&L). Anchored to the series value at the event time,
// hover shows the tooltip. Reused by the P&L and greek charts (same time domain).
function ChartMarkers({
  markers,
  series,
  t0,
  t1,
  X,
  Y,
}: {
  markers: TradeEvent[];
  series: (number | null)[];
  t0: number;
  t1: number;
  X: (i: number) => number;
  Y: (v: number) => number;
}): JSX.Element {
  const span = t1 - t0 || 1;
  return (
    <g>
      {markers.map((m, idx) => {
        const fi = ((m.t - t0) / span) * (GRID_N - 1);
        if (fi < -0.5 || fi > GRID_N - 0.5) return null;
        const v = series[Math.max(0, Math.min(GRID_N - 1, Math.round(fi)))];
        if (v == null) return null;
        const x = X(fi),
          y = Y(v);
        const col =
          m.kind === "open" ? "var(--accent)" : m.pnl == null ? "var(--muted)" : m.pnl >= 0 ? "var(--pos)" : "var(--neg)";
        const tip =
          m.kind === "open"
            ? `Opened #${m.id} ${m.type}${m.spot != null ? " @ " + m.spot.toFixed(4) : ""} · ${fmtTs(m.t)}`
            : `Closed #${m.id} ${m.type}${m.pnl != null ? " " + (m.pnl >= 0 ? "+" : "−") + "$" + Math.abs(m.pnl / 1000).toFixed(1) + "k" : ""} · ${fmtTs(m.t)}`;
        return (
          <g key={idx} style={{ cursor: "pointer" }}>
            <title>{tip}</title>
            {m.kind === "open" ? (
              <path d={`M${x} ${y - 5.5} L${x + 4.5} ${y + 3} L${x - 4.5} ${y + 3} Z`} fill={col} stroke="var(--bg)" strokeWidth="0.8" />
            ) : (
              <circle cx={x} cy={y} r="3.7" fill={col} stroke="var(--bg)" strokeWidth="1" />
            )}
          </g>
        );
      })}
    </g>
  );
}

// SVG line path that breaks at nulls (missing spans render as empty gaps).
function gappedLine(
  series: (number | null)[],
  X: (i: number) => number,
  Y: (v: number) => number,
): string {
  let d = "";
  let pen = false;
  series.forEach((v, i) => {
    if (v == null) {
      pen = false;
      return;
    }
    d += (pen ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " ";
    pen = true;
  });
  return d.trim();
}

// Filled area under each contiguous (non-null) run, down to a baseline.
function gappedArea(
  series: (number | null)[],
  X: (i: number) => number,
  Y: (v: number) => number,
  baseY: number,
): string {
  let d = "";
  let run: number[] = [];
  const flush = (): void => {
    if (run.length === 0) return;
    d += "M" + X(run[0]!).toFixed(1) + " " + baseY.toFixed(1) + " ";
    for (const i of run) d += "L" + X(i).toFixed(1) + " " + Y(series[i]!).toFixed(1) + " ";
    d += "L" + X(run[run.length - 1]!).toFixed(1) + " " + baseY.toFixed(1) + " Z ";
    run = [];
  };
  series.forEach((v, i) => (v == null ? flush() : run.push(i)));
  flush();
  return d.trim();
}

// Fixed x-axis: faint vertical marks + day/hour labels along the time domain.
function XAxis({
  ticks,
  pl,
  pr,
  pt,
  pb,
  w,
  h,
}: {
  ticks: { f: number; label: string }[];
  pl: number;
  pr: number;
  pt: number;
  pb: number;
  w: number;
  h: number;
}): JSX.Element {
  const span = w - pl - pr;
  return (
    <g>
      {ticks.map((t, i) => {
        const x = pl + t.f * span;
        return (
          <g key={i}>
            <line x1={x} x2={x} y1={pt} y2={h - pb} stroke="var(--line)" opacity="0.5" />
            <text
              x={x}
              y={h - 4}
              textAnchor={t.f === 0 ? "start" : t.f === 1 ? "end" : "middle"}
              fill="var(--text-dim)"
              fontSize="11"
              fontWeight={600}
              fontFamily="var(--mono)"
            >
              {t.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function emptyChart(w: number, h: number, status: string): JSX.Element {
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <text
        x={w / 2}
        y={h / 2}
        textAnchor="middle"
        fill="var(--text-faint)"
        fontSize="11"
        fontFamily="var(--mono)"
      >
        {status === "missing" ? "no equity history" : "loading…"}
      </text>
    </svg>
  );
}

// Reusable y-axis $ label formatter (M / k / units).
const fmtAxis = (v: number): string =>
  Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + "M" : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(0) + "k" : v.toFixed(0);

// Y-axis gridlines + $ labels (5 ticks), brightened for legibility.
function YGrid({ lo, hi, w, pl, pr, pt, pb, h }: { lo: number; hi: number; w: number; pl: number; pr: number; pt: number; pb: number; h: number }): JSX.Element {
  const rng = hi - lo || 1;
  return (
    <g>
      {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
        const y = pt + f * (h - pt - pb);
        return (
          <g key={i}>
            <line x1={pl} x2={w - pr} y1={y} y2={y} stroke="var(--line)" opacity="0.55" />
            <text x={4} y={y + 3} fill="var(--text-dim)" fontSize="10.5" fontFamily="var(--mono)">
              {fmtAxis(lo + rng * (1 - f))}
            </text>
          </g>
        );
      })}
    </g>
  );
}

// Green-above-zero / red-below-zero line + area, clipped exactly at the zero line.
function SignedSeries({
  series,
  X,
  Y,
  zeroY,
  w,
  top,
  bottom,
  id,
}: {
  series: (number | null)[];
  X: (i: number) => number;
  Y: (v: number) => number;
  zeroY: number;
  w: number;
  top: number;
  bottom: number;
  id: string;
}): JSX.Element {
  const line = gappedLine(series, X, Y);
  const area = gappedArea(series, X, Y, zeroY);
  return (
    <g>
      <defs>
        <clipPath id={`${id}-pos`}>
          <rect x={0} y={top} width={w} height={Math.max(0, zeroY - top)} />
        </clipPath>
        <clipPath id={`${id}-neg`}>
          <rect x={0} y={zeroY} width={w} height={Math.max(0, bottom - zeroY)} />
        </clipPath>
      </defs>
      <path d={area} fill="var(--pos)" fillOpacity="0.16" clipPath={`url(#${id}-pos)`} />
      <path d={area} fill="var(--neg)" fillOpacity="0.16" clipPath={`url(#${id}-neg)`} />
      <path d={line} fill="none" stroke="var(--pos)" strokeWidth="2.1" strokeLinejoin="round" strokeLinecap="round" clipPath={`url(#${id}-pos)`} />
      <path d={line} fill="none" stroke="var(--neg)" strokeWidth="2.1" strokeLinejoin="round" strokeLinecap="round" clipPath={`url(#${id}-neg)`} />
    </g>
  );
}

// P&L curve — cumulative P&L since the window start (rebased to 0), coloured green
// above / red below the zero baseline, on the fixed time axis + trade markers.
function EquityLineSvg({ grid, status, markers = [] }: { grid: EqGrid; status: string; markers?: TradeEvent[] }): JSX.Element {
  const w = 760,
    h = 172,
    pl = 56,
    pr = 12,
    pt = 14,
    pb = 26;
  const firstIdx = grid.series.findIndex((v) => v != null);
  if (firstIdx < 0) return emptyChart(w, h, status);
  const base = grid.series[firstIdx]!;
  const pnl = grid.series.map((v) => (v == null ? null : v - base));
  const vals = pnl.filter((v): v is number => v != null);
  if (vals.length < 2) return emptyChart(w, h, status);
  const lo = Math.min(...vals, 0),
    hi = Math.max(...vals, 0),
    rng = hi - lo || 1;
  const X = (i: number): number => pl + (i / (GRID_N - 1)) * (w - pl - pr);
  const Y = (v: number): number => pt + (1 - (v - lo) / rng) * (h - pt - pb);
  const zeroY = Y(0);
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block", height: "auto" }}>
      <YGrid lo={lo} hi={hi} w={w} pl={pl} pr={pr} pt={pt} pb={pb} h={h} />
      <line x1={pl} x2={w - pr} y1={zeroY} y2={zeroY} stroke="var(--text-faint)" strokeWidth="1" opacity="0.85" />
      <XAxis ticks={grid.ticks} pl={pl} pr={pr} pt={pt} pb={pb} w={w} h={h} />
      <SignedSeries series={pnl} X={X} Y={Y} zeroY={zeroY} w={w} top={pt} bottom={h - pb} id="eqpnl" />
      <ChartMarkers markers={markers} series={pnl} t0={grid.t0} t1={grid.t1} X={X} Y={Y} />
    </svg>
  );
}

// Drawdown (% from running peak) — bottom graph, same fixed time axis + gaps.
function DrawdownSvg({ grid, status }: { grid: EqGrid; status: string }): JSX.Element {
  const w = 760,
    h = 152,
    pl = 52,
    pr = 12,
    pt = 14,
    pb = 26;
  const base = pt, // 0% at the top — the underwater surface
    floor = h - pb;
  let peak = -Infinity;
  const dd = grid.series.map((v) => {
    if (v == null) return null;
    peak = Math.max(peak, v);
    return (v - peak) / peak;
  });
  const ddVals = dd.filter((v): v is number => v != null);
  if (ddVals.length < 2) return emptyChart(w, h, status);
  const ddMin = Math.min(...ddVals, -0.0001);
  const X = (i: number): number => pl + (i / (GRID_N - 1)) * (w - pl - pr);
  const Y = (x: number): number => base + (x / ddMin) * (floor - base); // 0 → top, ddMin → bottom
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block", height: "auto" }}>
      {[0, 1].map((f, i) => {
        const yy = base + f * (floor - base);
        return (
          <g key={i}>
            <line
              x1={pl}
              x2={w - pr}
              y1={yy}
              y2={yy}
              stroke="var(--line)"
              opacity={f === 0 ? 0.9 : 0.5}
            />
            <text x={4} y={yy + 3} fill="var(--text-dim)" fontSize="10.5" fontFamily="var(--mono)">
              {f === 0 ? "0%" : (ddMin * 100).toFixed(1) + "%"}
            </text>
          </g>
        );
      })}
      <XAxis ticks={grid.ticks} pl={pl} pr={pr} pt={pt} pb={pb} w={w} h={h} />
      <path d={gappedArea(dd, X, Y, base)} fill="var(--neg)" fillOpacity="0.34" />
      <path
        d={gappedLine(dd, X, Y)}
        fill="none"
        stroke="var(--neg)"
        strokeWidth="1.2"
        opacity="0.75"
      />
    </svg>
  );
}

// Performance panel — fixed layout over a shared timeframe: the left half stacks
// the P&L curve over the drawdown underwater plot; the right half is a 2×2 grid of
// the four cumulative greek-P&L series (Taylor terms from /greek-pnl-history — the
// realized decomposition, NOT the book's greek sensitivities). Each left chart is
// twice the width of a right cell, so both halves foot to the same height.
function PerformancePanel({
  ps,
  unreal,
  markers,
}: {
  ps: PerfStats;
  unreal: number;
  markers: TradeEvent[];
}): JSX.Element {
  const [win, setWin] = useState<string>("7D");
  // One windowed equity fetch + one greek-P&L-history fetch feed both halves.
  const eq = useFetch<EquityPoint[]>(
    () => fetchEquityCurve(win.toLowerCase()).then(adaptEquityCurve),
    120_000,
  );
  const gk = useFetch<GreekSeries>(
    () => fetchGreekPnlHistory(win.toLowerCase()).then(adaptGreekPnlHistory),
    120_000,
  );
  // useFetch won't refire on a window change alone — reload both on switch (skip mount).
  const reloadEq = eq.reload;
  const reloadGk = gk.reload;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    reloadEq();
    reloadGk();
  }, [win, reloadEq, reloadGk]);
  const equity = buildEquityGrid(eq.data ?? [], WINDOW_DAYS[win] ?? null, Date.now());
  const hist: GreekSeries = gk.data ?? { delta: [], gamma: [], vega: [], theta: [] };
  return (
    <Panel
      title="Performance"
      dataPp="perf"
      right={
        <div className="tf-group">
          {[
            { v: "1D", l: "1D" },
            { v: "7D", l: "7D" },
            { v: "30D", l: "1M" },
            { v: "1Y", l: "1Y" },
            { v: "all", l: "all" },
          ].map((wn) => (
            <button
              key={wn.v}
              className={"chip " + (win === wn.v ? "on" : "")}
              onClick={() => setWin(wn.v)}
            >
              {wn.l}
            </button>
          ))}
        </div>
      }
      className="perf-panel"
    >
      <div className="perf-cols">
        <div className="perf-col-left">
          <div className="perf-slot-stats">
            <div className="pstat">
              <span className="pstat-lbl mono dim">Realized</span>
              <b className={"pstat-val mono " + pnlCls(ps.cumRealized)}>{fmt.sgn(ps.cumRealized, 1)}k</b>
            </div>
            <div className="pstat">
              <span className="pstat-lbl mono dim">Unrealized</span>
              <b className={"pstat-val mono " + pnlCls(unreal)}>{fmt.usdk(unreal)}</b>
            </div>
          </div>
          <div className="perf-sub mono dim">
            P&L <em className="unit">cumulative $ · ▲ open · ● close</em>
          </div>
          <EquityLineSvg grid={equity} status={eq.status} markers={markers} />
          <div className="perf-slot-stats">
            <div className="pstat">
              <span className="pstat-lbl mono dim">Max drawdown</span>
              <b className="pstat-val mono neg">{ps.maxDd}%</b>
            </div>
            <div className="pstat">
              <span className="pstat-lbl mono dim">Current DD</span>
              <b className="pstat-val mono neg">{ps.currentDd}%</b>
            </div>
          </div>
          <div className="perf-sub mono dim">
            Drawdown <em className="unit">% from peak</em>
          </div>
          <DrawdownSvg grid={equity} status={eq.status} />
        </div>
        <div className="perf-greek-grid">
          {GREEKS.map((g) => (
            <div key={g.key} className="perf-greek-cell">
              <div className="perf-sub mono dim">
                {g.label} P&L <em className="unit">Taylor · cumulative $</em>
              </div>
              <GreekPnlChart
                grid={buildEquityGrid(hist[g.key], WINDOW_DAYS[win] ?? null, Date.now())}
                status={gk.status}
                markers={markers}
                id={g.key}
              />
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}

// One cell of the 2×2 greek-P&L grid — a cumulative Taylor term ($, zero baseline)
// on the shared fixed time axis. Half-width viewBox so two cells span one left-column
// chart at the same rendered scale, with the trade markers overlaid.
function GreekPnlChart({
  grid,
  status,
  markers,
  id,
}: {
  grid: EqGrid;
  status: string;
  markers: TradeEvent[];
  id: string;
}): JSX.Element {
  const w = 380,
    h = 210,
    pl = 46,
    pr = 8,
    pt = 12,
    pb = 24;
  const vals = grid.series.filter((v): v is number => v != null);
  if (vals.length < 2) return emptyChart(w, h, status);
  const lo = Math.min(...vals, 0),
    hi = Math.max(...vals, 0);
  const X = (i: number): number => pl + (i / (GRID_N - 1)) * (w - pl - pr);
  const Y = (v: number): number => pt + (1 - (v - lo) / (hi - lo || 1)) * (h - pt - pb);
  const zeroY = Y(0);
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block", height: "auto" }}>
      <YGrid lo={lo} hi={hi} w={w} pl={pl} pr={pr} pt={pt} pb={pb} h={h} />
      <line x1={pl} x2={w - pr} y1={zeroY} y2={zeroY} stroke="var(--text-faint)" strokeWidth="1" opacity="0.85" />
      <XAxis ticks={grid.ticks} pl={pl} pr={pr} pt={pt} pb={pb} w={w} h={h} />
      <SignedSeries series={grid.series} X={X} Y={Y} zeroY={zeroY} w={w} top={pt} bottom={h - pb} id={`gkpnl-${id}`} />
      <ChartMarkers markers={markers} series={grid.series} t0={grid.t0} t1={grid.t1} X={X} Y={Y} />
    </svg>
  );
}

const GREEKS: { key: GreekKey; label: string }[] = [
  { key: "delta", label: "Delta" },
  { key: "gamma", label: "Gamma" },
  { key: "vega", label: "Vega" },
  { key: "theta", label: "Theta" },
];

// P&L attribution matrix — greek P&L (Taylor terms) × axis (tenor), all in $. Each
// cell shows the term value + its share of that row's realized P&L. Rows foot to
// P&L Σ (± residual); the Total row (Σ over rows) equals the by-greek bridge.
function AttributionMatrix({ m, axisLabel }: { m: AttribMatrix | null; axisLabel: string }): JSX.Element {
  if (!m || m.rows.length === 0)
    return <div className="hbar-empty dim small mono">no attribution yet</div>;
  // term cell: value $ bold + (% of the row's realized P&L) lighter, same colour.
  const cell = (v: number, rowActual: number, extra: string): JSX.Element => {
    // % of the row's realized P&L — undefined when the row P&L is ~0 (would divide
    // by ≈0 into a nonsense %), shown as "—" then.
    const p = Math.abs(rowActual) < 1 ? null : Math.round((v / Math.abs(rowActual)) * 100);
    return (
      <td className={"r mono " + extra + " " + pnlCls(v)}>
        <b>{gk$(v)}</b>{" "}
        <span className="pb-rel">({p == null ? "—" : (p >= 0 ? "+" : "−") + Math.abs(p) + "%"})</span>
      </td>
    );
  };
  const dataRow = (r: AttribRow, i: number): JSX.Element => (
    <tr key={i}>
      <td className="l grp-fix">
        <span className="sym">{r.label}</span>
      </td>
      <td className={"r mono grp-pnl col-grp col-grp-end " + pnlCls(r.actual)}>
        <b>{fmt.usdk(r.actual)}</b>
      </td>
      {cell(r.delta, r.actual, "grp-grk col-grp")}
      {cell(r.gamma, r.actual, "grp-grk")}
      {cell(r.vega, r.actual, "grp-grk")}
      {cell(r.theta, r.actual, "grp-grk")}
      {cell(r.residual, r.actual, "grp-grk col-grp-end")}
    </tr>
  );
  const t = m.totals;
  return (
    <div className="table-scroll">
      <table className="dt pb-table wf-structure">
        <thead>
          <tr>
            <th className="l grp-fix">{axisLabel}</th>
            <th className="r grp-pnl col-grp col-grp-end">
              P&L <em className="unit">Σ</em>
            </th>
            <th className="r grp-grk col-grp">Delta·dS</th>
            <th className="r grp-grk">½Γ·dS²</th>
            <th className="r grp-grk">Vega·dσ</th>
            <th className="r grp-grk">Theta·dt</th>
            <th className="r grp-grk col-grp-end">residual</th>
          </tr>
        </thead>
        <tbody>
          {m.rows.map(dataRow)}
          <tr className="wf-total">
            <td className="l grp-fix">
              <span className="sym">Total</span>
            </td>
            <td className={"r mono grp-pnl col-grp col-grp-end " + pnlCls(t.actual)}>
              <b>{fmt.usdk(t.actual)}</b>
            </td>
            <td className={"r mono grp-grk col-grp " + pnlCls(t.delta)}>
              <b>{gk$(t.delta)}</b>
            </td>
            <td className={"r mono grp-grk " + pnlCls(t.gamma)}>
              <b>{gk$(t.gamma)}</b>
            </td>
            <td className={"r mono grp-grk " + pnlCls(t.vega)}>
              <b>{gk$(t.vega)}</b>
            </td>
            <td className={"r mono grp-grk " + pnlCls(t.theta)}>
              <b>{gk$(t.theta)}</b>
            </td>
            <td className={"r mono grp-grk col-grp-end " + pnlCls(t.residual)}>
              <b>{gk$(t.residual)}</b>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// Per-trade P&L-attribution matrix — grouped by trade like Open positions: a
// collapsible summary line per multi-leg trade (caret ▸, aggregated greek-P&L)
// with its legs indented, single-leg trades as one row. Bottom Total foots.
// Booked structure name from the trade's classifier verdict (structure_type),
// mirroring Trade's Open positions so #90 reads "Straddle" not "Vanilla Put" even
// when the mirror only holds one un-netted leg. null for empty/custom → fall back.
function bookedName(type: string): string | null {
  const l = (type || "").toLowerCase().trim();
  if (!l || l === "custom") return null;
  const sm = /strangle\s*(\d+)\s*d/.exec(l);
  if (sm) return `Strangle ${sm[1]}Δ`;
  if (l.includes("strangle")) return "Strangle";
  if (l.includes("straddle")) return "Straddle";
  if (l.includes("risk reversal")) return "Risk Reversal";
  if (l.includes("butterfly")) return "Butterfly";
  if (l.includes("calendar")) return "Calendar";
  if (l.includes("call spread")) return "Call Spread";
  if (l.includes("put spread")) return "Put Spread";
  if (l.includes("vertical spread")) return "Vertical Spread";
  if (l.includes("future")) return "Future";
  const bare = l.replace(/^(long|short)\s+/, "");
  if (bare === "call") return "Vanilla Call";
  if (bare === "put") return "Vanilla Put";
  return null;
}

function PositionAttributionMatrix({ m }: { m: PositionAttribMatrix | null }): JSX.Element {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (k: string): void =>
    setExpanded((prev) => {
      const n = new Set(prev);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  if (!m || m.rows.length === 0)
    return <div className="hbar-empty dim small mono">no attribution yet</div>;
  // one greek-P&L cell: value $ bold + (% of the row's realized P&L).
  const cell = (v: number, rowActual: number, extra: string): JSX.Element => {
    // % of the row's realized P&L — undefined when the row P&L is ~0 (would divide
    // by ≈0 into a nonsense %), shown as "—" then.
    const p = Math.abs(rowActual) < 1 ? null : Math.round((v / Math.abs(rowActual)) * 100);
    return (
      <td className={"r mono " + extra + " " + pnlCls(v)}>
        <b>{gk$(v)}</b>{" "}
        <span className="pb-rel">({p == null ? "—" : (p >= 0 ? "+" : "−") + Math.abs(p) + "%"})</span>
      </td>
    );
  };
  // the 6 attribution cells (P&L Σ + 4 terms + residual), shared by leg & summary rows.
  const attribCells = (r: {
    actual: number; delta: number; gamma: number; vega: number; theta: number; residual: number;
  }): JSX.Element => (
    <>
      <td className={"r mono grp-pnl col-grp col-grp-end " + pnlCls(r.actual)}>
        <b>{fmt.usdk(r.actual)}</b>
      </td>
      {cell(r.delta, r.actual, "grp-grk col-grp")}
      {cell(r.gamma, r.actual, "grp-grk")}
      {cell(r.vega, r.actual, "grp-grk")}
      {cell(r.theta, r.actual, "grp-grk")}
      {cell(r.residual, r.actual, "grp-grk col-grp-end")}
    </>
  );
  const legRow = (r: PositionAttribRow, main: boolean): JSX.Element => (
    <tr key={r.id} className={main ? undefined : "pos-leg"}>
      <td className="l grp-fix mono dim">{main ? (r.tradeId != null ? "#" + r.tradeId : "—") : ""}</td>
      <td className="l grp-fix mono dim">{r.contractId ?? "—"}</td>
      <td className="l grp-fix">
        <span className="sym">{main ? bookedName(r.type) ?? r.product : "↳ " + r.product}</span>
      </td>
      <td className="l grp-fix">
        <span className="sym">{r.structure}</span>
      </td>
      <td className="l grp-fix">
        <span className={"side-pill " + (r.side === "BUY" ? "long" : "short")}>{r.side}</span>
      </td>
      <td className="r mono dim grp-fix">{r.tenor}</td>
      <td className="r mono dim grp-fix">{r.iv ? r.iv.toFixed(1) : "—"}</td>
      <td className="r mono dim grp-fix col-grp-end">{(r.nominal / 1e6).toFixed(2)}M</td>
      {attribCells(r)}
    </tr>
  );
  const t = m.totals;
  return (
    <div className="table-scroll">
      <table className="dt pb-table">
        <thead>
          <tr>
            <th className="l grp-fix">Trade</th>
            <th className="l grp-fix">Contract</th>
            <th className="l grp-fix">Product</th>
            <th className="l grp-fix">Structure</th>
            <th className="l grp-fix">Side</th>
            <th className="r grp-fix">Tenor</th>
            <th className="r grp-fix">IV</th>
            <th className="r grp-fix col-grp-end">Nominal €</th>
            <th className="r grp-pnl col-grp col-grp-end">
              P&L <em className="unit">Σ</em>
            </th>
            <th className="r grp-grk col-grp">Delta·dS</th>
            <th className="r grp-grk">½Γ·dS²</th>
            <th className="r grp-grk">Vega·dσ</th>
            <th className="r grp-grk">Theta·dt</th>
            <th className="r grp-grk col-grp-end">residual</th>
          </tr>
        </thead>
        <tbody>
          {groupByTradeId(m.rows).map((grp) => {
            if (grp.legs.length === 1) return legRow(grp.legs[0]!, true);
            const isOpen = expanded.has(grp.key);
            const sum = (sel: (r: PositionAttribRow) => number): number => grp.legs.reduce((a, r) => a + sel(r), 0);
            const agg = {
              actual: sum((r) => r.actual), delta: sum((r) => r.delta), gamma: sum((r) => r.gamma),
              vega: sum((r) => r.vega), theta: sum((r) => r.theta), residual: sum((r) => r.residual),
            };
            const tenors = new Set(grp.legs.map((l) => l.tenor));
            const side = structureSide(grp.legs);
            return (
              <Fragment key={grp.key}>
                <tr className={"pos-main" + (isOpen ? " open" : "")} onClick={() => toggle(grp.key)}>
                  <td className="l grp-fix mono dim">
                    <button
                      className="pos-caret"
                      onClick={(e) => { e.stopPropagation(); toggle(grp.key); }}
                      aria-expanded={isOpen}
                    >
                      {isOpen ? "▾" : "▸"}
                    </button>
                    {grp.tradeId ? "#" + grp.tradeId : "—"}
                  </td>
                  <td className="l grp-fix mono dim">{grp.legs.length} legs</td>
                  <td className="l grp-fix">
                    <span className="sym">{bookedName(grp.legs[0]!.type) ?? structureName(grp.legs)}</span>
                  </td>
                  <td className="l grp-fix mono dim">—</td>
                  <td className="l grp-fix">
                    <span className={"side-pill " + (side === "BUY" ? "long" : "short")}>{side}</span>
                  </td>
                  <td className="r mono dim grp-fix">{tenors.size === 1 ? [...tenors][0] : "—"}</td>
                  <td className="r mono dim grp-fix">—</td>
                  <td className="r mono dim grp-fix col-grp-end">{(sum((r) => r.nominal) / 1e6).toFixed(2)}M</td>
                  {attribCells(agg)}
                </tr>
                {isOpen && grp.legs.map((r) => legRow(r, false))}
              </Fragment>
            );
          })}
          <tr className="wf-total">
            <td className="l grp-fix" colSpan={8}>
              <span className="sym">Total</span>
            </td>
            <td className={"r mono grp-pnl col-grp col-grp-end " + pnlCls(t.actual)}>
              <b>{fmt.usdk(t.actual)}</b>
            </td>
            <td className={"r mono grp-grk col-grp " + pnlCls(t.delta)}>
              <b>{gk$(t.delta)}</b>
            </td>
            <td className={"r mono grp-grk " + pnlCls(t.gamma)}>
              <b>{gk$(t.gamma)}</b>
            </td>
            <td className={"r mono grp-grk " + pnlCls(t.vega)}>
              <b>{gk$(t.vega)}</b>
            </td>
            <td className={"r mono grp-grk " + pnlCls(t.theta)}>
              <b>{gk$(t.theta)}</b>
            </td>
            <td className={"r mono grp-grk col-grp-end " + pnlCls(t.residual)}>
              <b>{gk$(t.residual)}</b>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ▲/▼ change pill (percent), coloured like P&L — matches the account-tile deltas.
function deltaPill(d: number | null | undefined): JSX.Element | null {
  if (d == null || !Number.isFinite(d)) return null;
  const neg = d < 0;
  return (
    <span className={"acct-delta " + (neg ? "neg" : "pos")}>
      {neg ? "▼" : "▲"} {Math.abs(d).toFixed(2)}%
    </span>
  );
}

// Parenthetical note appended to the value in the merged Capital cell; nothing if empty.
function acctNote(note: ReactNode): JSX.Element | null {
  return note ? <span className="acct-sub"> ({note})</span> : null;
}

// ─── Account & capital: holdings valuation (right side) ─────────────────────
// Net liq decomposed into its three components — USD cash, EUR cash (both in $)
// and the contracts' market value as the residual net liq − cash, so the parts
// always foot exactly to net liq (the Total row).
const VAL_PARTS: { key: ValuationKey; label: string; color: string }[] = [
  { key: "usd", label: "USD cash", color: "var(--accent)" },
  { key: "eur", label: "EUR cash", color: "#a78bfa" },
  { key: "contracts", label: "Contracts", color: "#2dd4bf" },
];

function HoldingsValuation({ netLiq, cash }: { netLiq: number; cash: Cash[] }): JSX.Element {
  const usd = cash.find((c) => c.ccy === "USD")?.usd ?? 0;
  const eur = cash.find((c) => c.ccy === "EUR")?.usd ?? 0;
  const contracts = netLiq - cash.reduce((s, c) => s + c.usd, 0);
  const vals: Record<string, number> = { usd, eur, contracts };
  const base = Math.abs(netLiq) || 1;
  // signed share of |net liq| — same reading as the attribution tables.
  const pct = (v: number): string => {
    const p = Math.round((v / base) * 100);
    return (p >= 0 ? "+" : "−") + Math.abs(p) + "%";
  };
  return (
    <table className="dt greeks-table acct-cap">
      <thead>
        <tr>
          <th className="l">Holdings</th>
          <th className="r">
            USD value <em className="unit">(% of net liq)</em>
          </th>
        </tr>
      </thead>
      <tbody>
        {VAL_PARTS.map((p) => (
          <tr key={p.key}>
            <td className="l">
              <span className="val-dot" style={{ background: p.color }} />
              {p.label}
            </td>
            <td className={"r mono " + pnlCls(vals[p.key]!)}>
              <b>{fmt.usd(vals[p.key]!)}</b> <span className="pb-rel">({pct(vals[p.key]!)})</span>
            </td>
          </tr>
        ))}
        <tr className="wf-total">
          <td className="l">
            <span className="sym">Total</span>
          </td>
          <td className={"r mono " + pnlCls(netLiq)}>
            <b>{fmt.usd(netLiq)}</b>
          </td>
        </tr>
      </tbody>
    </table>
  );
}

// Filled band between two same-length gapped offset polylines (bottom → top).
function bandPath(
  bot: (number | null)[],
  top: (number | null)[],
  X: (i: number) => number,
  Y: (v: number) => number,
): string {
  let d = "";
  let run: number[] = [];
  const flush = (): void => {
    if (run.length === 0) return;
    d += "M" + X(run[0]!).toFixed(1) + " " + Y(top[run[0]!]!).toFixed(1) + " ";
    for (const i of run) d += "L" + X(i).toFixed(1) + " " + Y(top[i]!).toFixed(1) + " ";
    for (let k = run.length - 1; k >= 0; k--) {
      const i = run[k]!;
      d += "L" + X(i).toFixed(1) + " " + Y(bot[i]!).toFixed(1) + " ";
    }
    d += "Z ";
    run = [];
  };
  top.forEach((v, i) => (v == null || bot[i] == null ? flush() : run.push(i)));
  flush();
  return d.trim();
}

// Portfolio-valuation chart — the net-liq area split into the three holdings
// components as diverging stacked bands (positive parts pile up from 0, negative
// ones down), with the net-liq total drawn as a line on top. Fixed 30d axis.
function ValuationChart({ s, status }: { s: ValuationSeries | null; status: string }): JSX.Element {
  const w = 760,
    h = 250,
    pl = 56,
    pr = 12,
    pt = 14,
    pb = 26;
  const now = Date.now();
  const grids: Record<ValuationKey, EqGrid> = {
    usd: buildEquityGrid(s?.usd ?? [], 30, now),
    eur: buildEquityGrid(s?.eur ?? [], 30, now),
    contracts: buildEquityGrid(s?.contracts ?? [], 30, now),
    total: buildEquityGrid(s?.total ?? [], 30, now),
  };
  const total = grids.total.series;
  if (total.filter((v): v is number => v != null).length < 2) return emptyChart(w, h, status);
  const bands = VAL_PARTS.map((p) => ({ ...p, bot: [] as (number | null)[], top: [] as (number | null)[] }));
  for (let i = 0; i < GRID_N; i++) {
    let pos = 0,
      neg = 0;
    for (const b of bands) {
      const v = grids[b.key].series[i] ?? null;
      if (v == null) {
        b.bot.push(null);
        b.top.push(null);
        continue;
      }
      if (v >= 0) {
        b.bot.push(pos);
        b.top.push(pos + v);
        pos += v;
      } else {
        b.bot.push(neg);
        b.top.push(neg + v);
        neg += v;
      }
    }
  }
  const extents = [...total, ...bands.flatMap((b) => [...b.top, ...b.bot])].filter(
    (v): v is number => v != null,
  );
  const lo = Math.min(...extents, 0),
    hi = Math.max(...extents, 0);
  const X = (i: number): number => pl + (i / (GRID_N - 1)) * (w - pl - pr);
  const Y = (v: number): number => pt + (1 - (v - lo) / (hi - lo || 1)) * (h - pt - pb);
  const zeroY = Y(0);
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block", height: "auto" }}>
      <YGrid lo={lo} hi={hi} w={w} pl={pl} pr={pr} pt={pt} pb={pb} h={h} />
      <line x1={pl} x2={w - pr} y1={zeroY} y2={zeroY} stroke="var(--text-faint)" strokeWidth="1" opacity="0.85" />
      <XAxis ticks={grids.total.ticks} pl={pl} pr={pr} pt={pt} pb={pb} w={w} h={h} />
      {bands.map((b) => (
        <g key={b.key}>
          <path d={bandPath(b.bot, b.top, X, Y)} fill={b.color} fillOpacity="0.32" />
          <path d={gappedLine(b.top, X, Y)} fill="none" stroke={b.color} strokeWidth="1" opacity="0.75" />
        </g>
      ))}
      <path
        d={gappedLine(total, X, Y)}
        fill="none"
        stroke="var(--fg)"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity="0.9"
      />
    </svg>
  );
}

export function PortfolioView(): JSX.Element {
  const { portfolio, trade } = useDeskData();
  const pd = portfolio.data;
  const a = pd?.account ?? DATA.account,
    ps = pd?.perfStats ?? DATA2.perfStats,
    g = pd?.greeks ?? DATA.greeks;
  // Live EURUSD spot (WS ticks) for the $→€ conversions; mock only until a tick lands.
  const spot = useTicks().data?.mid ?? DATA.SPOT;
  // Trade open/close markers overlaid on the Performance P&L + greek charts (covers 1Y).
  const tradeMarkers =
    useFetch(() => fetchTradeMarkers(366).then(adaptTradeMarkers), 120_000, true, 60_000).data ?? [];
  // P&L-attribution matrices: greek P&L bucketed by tenor, and one per leg.
  const attribTenor =
    useFetch(() => fetchPnlAttributionMatrix("tenor").then(adaptAttributionMatrix), 120_000, true, 60_000).data ?? null;
  const attribByLeg =
    useFetch(() => fetchPnlAttribution().then(adaptPositionAttribution), 120_000, true, 60_000).data ?? null;
  // Net-liq valuation decomposition (USD cash / EUR cash / contracts) over 30d.
  const valuation = useFetch<ValuationSeries>(
    () => fetchValuationHistory("30d").then(adaptValuationHistory),
    120_000,
    true,
    60_000,
  );
  // Live per-currency cash balances (from /portfolio/cash via the trade slice).
  const liveCash = trade.data?.cash;
  const cashRows = liveCash && liveCash.length > 0 ? liveCash : DATA.cash;
  // Leverage from the live book: gross = Σ|notional|, net = |Σ signed notional| (€),
  // buying power from the IB heartbeat ($). Mock only until positions/account load.
  const posForLev = pd?.positions ?? DATA.positions;
  const grossNotional = posForLev.reduce((s, p) => s + Math.abs(p.nominal), 0);
  const netNotional = Math.abs(
    posForLev.reduce((s, p) => s + (p.side === "BUY" ? p.nominal : -p.nominal), 0),
  );
  const lev = {
    gross: grossNotional / 1e6,
    net: netNotional / 1e6,
    buyingPower: a.buyingPower / 1e6,
  };
  // §P1 leverage unit bug: notional is in €, net liq in $ — convert to one ccy before dividing
  const netLiqEur = a.netLiq / spot; // $ net liq → €
  const grossX = netLiqEur ? (lev.gross / (netLiqEur / 1e6)).toFixed(2) : "—";
  const netX = netLiqEur ? (lev.net / (netLiqEur / 1e6)).toFixed(2) : "—";
  // §P1 unrealized single source: read the one engine (= Open positions = Risk = Close)
  const unreal = g.netUnreal;
  return (
    <div className="portfolio-grid">
      <Panel
        title="Account & capital"
        dataPp="account"
        right={<FreshBadge fresh={portfolio} label="IB account" />}
        className="acct-panel"
      >
        <div className="acct-cols">
          <div className="acct-col">
            <table className="dt greeks-table acct-cap">
              <thead>
                <tr>
                  <th className="l">Cash &amp; margin</th>
                  <th className="r">Value</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="l">Net liquidation</td>
                  <td className="r mono">
                    {fmt.usd(a.netLiq)}
                    {acctNote(deltaPill(a.dNetLiq))}
                  </td>
                </tr>
                <tr>
                  <td className="l">Cash</td>
                  <td className="r mono">
                    {fmt.usd(a.cash)}
                    {acctNote(deltaPill(a.dCash))}
                  </td>
                </tr>
                <tr>
                  <td className="l">Init margin</td>
                  <td className="r mono">
                    {fmt.usd(a.marginInit)}
                    {acctNote(`${a.marginInitPct}% used`)}
                  </td>
                </tr>
                <tr>
                  <td className="l">Maint margin</td>
                  <td className="r mono">
                    {fmt.usd(a.marginMaint)}
                    {acctNote(`${a.marginMaintPct}% used`)}
                  </td>
                </tr>
                <tr>
                  <td className="l">Excess liquidity</td>
                  <td className="r mono pos">{fmt.usd(a.excessLiq)}</td>
                </tr>
                <tr>
                  <td className="l">Cushion</td>
                  <td className="r mono">
                    {(a.cushion * 100).toFixed(1)}%{acctNote(`${a.nPositions} positions`)}
                  </td>
                </tr>
              </tbody>
            </table>
            <table className="dt greeks-table acct-cap">
              <thead>
                <tr>
                  <th className="l">Leverage &amp; buying power</th>
                  <th className="r">Value</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="l">Gross leverage</td>
                  <td className="r mono">
                    {lev.gross.toFixed(1)}M €{acctNote(`${grossX}× net liq · €${(netLiqEur / 1e6).toFixed(2)}M`)}
                  </td>
                </tr>
                <tr>
                  <td className="l">Net leverage</td>
                  <td className="r mono">
                    {lev.net.toFixed(1)}M €{acctNote(`${netX}× net liq`)}
                  </td>
                </tr>
                <tr>
                  <td className="l">Buying power</td>
                  <td className="r mono pos">
                    ${lev.buyingPower.toFixed(2)}M{acctNote("available")}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="acct-col">
            <HoldingsValuation netLiq={a.netLiq} cash={cashRows} />
            <div>
              <div className="perf-sub mono dim">
                Portfolio valuation <em className="unit">net liq $ · stacked components · 30d</em>
              </div>
              <div className="val-legend mono dim">
                {VAL_PARTS.map((p) => (
                  <span key={p.key} className="val-key">
                    <i style={{ background: p.color }} /> {p.label}
                  </span>
                ))}
                <span className="val-key">
                  <i className="val-key-line" /> Net liq
                </span>
              </div>
              <ValuationChart s={valuation.data} status={valuation.status} />
            </div>
          </div>
        </div>
      </Panel>

      <PerformancePanel ps={ps} unreal={unreal} markers={tradeMarkers} />

      <Panel title="P&L attribution by tenor" dataPp="attrib-tenor" className="wf-panel">
        <AttributionMatrix m={attribTenor} axisLabel="Tenor" />
      </Panel>

      <Panel title="P&L attribution by trade" dataPp="attrib-leg" className="wf-panel">
        <PositionAttributionMatrix m={attribByLeg} />
      </Panel>
    </div>
  );
}
