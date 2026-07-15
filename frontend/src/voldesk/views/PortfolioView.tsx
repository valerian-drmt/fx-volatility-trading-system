/**
 * VOLDESK — Portfolio (capital, performance, survival metric, realized
 * attribution bridge, book composition). Ported from the prototype's
 * `js/views_portfolio.jsx` (global-window pattern) into typed ES modules.
 * 1:1 port — same JSX, same classNames, same logic. Mock data for now.
 */
import { useEffect, useRef, useState } from "react";
import { fetchEquityCurve, fetchPnlAttributionPivot, fetchTradeMarkers } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { useTicks } from "../../hooks/streams";
import { Panel } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { pnlCls } from "../components/format";
import { CashHoldings } from "../components/PositionsTable";
import { TickerChart } from "../components/TickerChart";
import { DATA, DATA2, fmt } from "../data";
import type { PerfStats, WaterfallStep } from "../data";
import { useDeskData } from "../data/deskData";
import {
  adaptEquityCurve,
  adaptTenorRows,
  adaptTradeMarkers,
  adaptWaterfallPivot,
  type EquityPoint,
  type TenorRow,
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
}

function buildEquityGrid(points: EquityPoint[], windowDays: number | null, now: number): EqGrid {
  if (points.length === 0) return { series: [], ticks: [] };
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
  return { series, ticks };
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
            <line x1={x} x2={x} y1={pt} y2={h - pb} stroke="var(--line)" opacity="0.3" />
            <text
              x={x}
              y={h - 4}
              textAnchor={t.f === 0 ? "start" : t.f === 1 ? "end" : "middle"}
              fill="var(--text-faint)"
              fontSize="9"
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

// Equity curve (net liq) — top graph, plotted on the fixed time axis.
function EquityLineSvg({ grid, status }: { grid: EqGrid; status: string }): JSX.Element {
  const w = 760,
    h = 172,
    pl = 52,
    pr = 12,
    pt = 14,
    pb = 26;
  const vals = grid.series.filter((v): v is number => v != null);
  if (vals.length < 2) return emptyChart(w, h, status);
  const lo = Math.min(...vals),
    hi = Math.max(...vals),
    rng = hi - lo || 1;
  const X = (i: number): number => pl + (i / (GRID_N - 1)) * (w - pl - pr);
  const Y = (v: number): number => pt + (1 - (v - lo) / rng) * (h - pt - pb);
  const col = "var(--accent)"; // neutral — slope shouldn't imply good/bad
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <defs>
        <linearGradient id="eqg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={col} stopOpacity="0.20" />
          <stop offset="100%" stopColor={col} stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
        const v = lo + rng * (1 - f);
        return (
          <g key={i}>
            <line
              x1={pl}
              x2={w - pr}
              y1={pt + f * (h - pt - pb)}
              y2={pt + f * (h - pt - pb)}
              stroke="var(--line)"
              opacity="0.5"
            />
            <text
              x={4}
              y={pt + f * (h - pt - pb) + 3}
              fill="var(--text-faint)"
              fontSize="9"
              fontFamily="var(--mono)"
            >
              {(v / 1e6).toFixed(2)}M
            </text>
          </g>
        );
      })}
      <XAxis ticks={grid.ticks} pl={pl} pr={pr} pt={pt} pb={pb} w={w} h={h} />
      <path d={gappedArea(grid.series, X, Y, h - pb)} fill="url(#eqg)" />
      <path
        d={gappedLine(grid.series, X, Y)}
        fill="none"
        stroke={col}
        strokeWidth="2.2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
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
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
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
            <text x={4} y={yy + 3} fill="var(--text-faint)" fontSize="9" fontFamily="var(--mono)">
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

// Performance charts — two stacked rows (P&L / Drawdown), each with its two stats
// on the left. Both share ONE windowed equity fetch; remount via key on the window.
function PerfCharts({
  window: win,
  ps,
  unreal,
}: {
  window: string;
  ps: PerfStats;
  unreal: number;
}): JSX.Element {
  const live = useFetch<EquityPoint[]>(
    () => fetchEquityCurve(win.toLowerCase()).then(adaptEquityCurve),
    120_000,
  );
  // useFetch only refires on its own tick/poll, so a window change alone wouldn't
  // refetch — reload explicitly when the window switches (skip the initial mount,
  // which useFetch already fetched).
  const reload = live.reload;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    reload();
  }, [win, reload]);
  // Resample onto the fixed time domain so every timeframe shows the same number of
  // samples on a 0→N-day axis, with empty zones where the window has no data.
  const grid = buildEquityGrid(live.data ?? [], WINDOW_DAYS[win] ?? null, Date.now());
  return (
    <div className="perf-v">
      <div className="perf-row">
        <div className="perf-side">
          <div className="pstat">
            <span className="pstat-lbl mono dim">Realized</span>
            <b className={"pstat-val mono " + pnlCls(ps.cumRealized)}>
              {fmt.sgn(ps.cumRealized, 1)}k
            </b>
          </div>
          <div className="pstat">
            <span className="pstat-lbl mono dim">Unrealized</span>
            <b className={"pstat-val mono " + pnlCls(unreal)}>{fmt.usdk(unreal)}</b>
          </div>
        </div>
        <div className="perf-chart">
          <div className="perf-sub mono dim">
            P&L <em className="unit">equity curve</em>
          </div>
          <EquityLineSvg grid={grid} status={live.status} />
        </div>
      </div>
      <div className="perf-row">
        <div className="perf-side">
          <div className="pstat">
            <span className="pstat-lbl mono dim">Max drawdown</span>
            <b className="pstat-val mono neg">{ps.maxDd}%</b>
          </div>
          <div className="pstat">
            <span className="pstat-lbl mono dim">Current DD</span>
            <b className="pstat-val mono neg">{ps.currentDd}%</b>
          </div>
        </div>
        <div className="perf-chart">
          <div className="perf-sub mono dim">
            Drawdown <em className="unit">% from peak</em>
          </div>
          <DrawdownSvg grid={grid} status={live.status} />
        </div>
      </div>
    </div>
  );
}

// Attribution as a 2-column table (name | P&L | % gain/loss). Reused for both the
// by-trade and by-greek axes (same WaterfallStep shape). The % is the row's share of
// the total GAINS if it's a winner, or of the total LOSSES if it's a loser.
function TradeTable({
  steps,
  col = "Trade",
}: {
  steps: WaterfallStep[];
  col?: string;
}): JSX.Element {
  const rows = steps.filter((s) => s.type !== "start" && s.type !== "net");
  if (rows.length === 0) return <div className="hbar-empty dim small mono">no P&L yet</div>;
  const gains = rows.filter((r) => r.v > 0).reduce((s, r) => s + r.v, 0);
  const losses = rows.filter((r) => r.v < 0).reduce((s, r) => s + Math.abs(r.v), 0);
  const fmtk = (v: number): string => (v >= 0 ? "+" : "−") + "$" + Math.abs(v).toFixed(1) + "k";
  const pct = (v: number): number => {
    const base = v >= 0 ? gains : losses;
    return base ? Math.round((Math.abs(v) / base) * 100) : 0;
  };
  return (
    <table className="dt greeks-table acct-cap">
      <thead>
        <tr>
          <th className="l">{col}</th>
          <th className="r">P&L</th>
          <th className="r">
            % <em className="unit">gain/loss</em>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((s, i) => (
          <tr key={i}>
            <td className="l">
              {s.label}
              {s.sub && <em className="unit">{s.sub}</em>}
            </td>
            <td className={"r mono " + (s.v >= 0 ? "pos" : "neg")}>{fmtk(s.v)}</td>
            <td className={"r mono " + (s.v >= 0 ? "pos" : "neg")}>
              {(s.v >= 0 ? "+" : "−") + pct(s.v) + "%"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// By-tenor breakdown — Position-breakdown-styled table, with
// the vega-by-tenor data folded in: P&L (%), vega (%), and the 2nd-order greeks
// (vanna / volga) per reference tenor.
function TenorTable({ rows }: { rows: TenorRow[] }): JSX.Element {
  if (rows.length === 0) return <div className="hbar-empty dim small mono">no positions</div>;
  const gains = rows.filter((r) => r.pnl > 0).reduce((s, r) => s + r.pnl, 0);
  const losses = rows.filter((r) => r.pnl < 0).reduce((s, r) => s + Math.abs(r.pnl), 0);
  const totVega = rows.reduce((s, r) => s + Math.abs(r.vega), 0) || 1;
  const pnlPct = (v: number): string => {
    const base = v >= 0 ? gains : losses;
    return (v >= 0 ? "+" : "−") + (base ? Math.round((Math.abs(v) / base) * 100) : 0) + "%";
  };
  return (
    <div className="table-scroll">
      <table className="dt pb-table wf-structure">
        <thead>
          <tr>
            <th className="l grp-fix">Tenor</th>
            <th className="r grp-pnl col-grp">P&L</th>
            <th className="r grp-pnl col-grp-end">%</th>
            <th className="r grp-fix col-grp">Vega</th>
            <th className="r grp-fix col-grp-end">%</th>
            <th className="r grp-grk col-grp">Vanna</th>
            <th className="r grp-grk col-grp-end">Volga</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="l grp-fix">
                <span className="sym">{r.label}</span>
              </td>
              <td className={"r mono grp-pnl col-grp " + pnlCls(r.pnl)}>{fmt.usdk(r.pnl)}</td>
              <td className={"r mono grp-pnl col-grp-end " + pnlCls(r.pnl)}>{pnlPct(r.pnl)}</td>
              <td className={"r mono grp-fix col-grp " + pnlCls(r.vega)}>
                {fmt.sgn(r.vega / 1000, 1)}k
              </td>
              <td className="r mono dim grp-fix col-grp-end">
                {Math.round((Math.abs(r.vega) / totVega) * 100)}%
              </td>
              <td className={"r mono grp-grk col-grp " + pnlCls(r.vanna)}>
                {fmt.sgn(r.vanna / 1000, 0)}k
              </td>
              <td className={"r mono grp-grk col-grp-end " + pnlCls(r.volga)}>
                {fmt.sgn(r.volga / 1000, 0)}k
              </td>
            </tr>
          ))}
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

export function PortfolioView(): JSX.Element {
  const [win, setWin] = useState<string>("7D");
  const { portfolio, trade } = useDeskData();
  const pd = portfolio.data;
  const a = pd?.account ?? DATA.account,
    ps = pd?.perfStats ?? DATA2.perfStats,
    g = pd?.greeks ?? DATA.greeks;
  // Non-greek attribution pivots (by structure / by tenor) — realized P&L bridged
  // from closed booked positions. Polled so the bridge stays live; "by mode" (PCA)
  // stays deferred.
  const pivotLive = useFetch(
    async () => {
      const [tenor, trade] = await Promise.all([
        fetchPnlAttributionPivot("tenor").then(adaptTenorRows),
        fetchPnlAttributionPivot("trade").then(adaptWaterfallPivot),
      ]);
      return { tenor, trade };
    },
    120_000,
    true,
    60_000,
  ).data;
  // Live EURUSD spot (WS ticks) for the $→€ conversions; mock only until a tick lands.
  const spot = useTicks().data?.mid ?? DATA.SPOT;
  // Trade open/close markers overlaid on the Performance ticker (covers the 1M preset).
  const tradeMarkers =
    useFetch(() => fetchTradeMarkers(31).then(adaptTradeMarkers), 120_000, true, 60_000).data ?? [];
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
        <div className="acct-tables">
          <table className="dt greeks-table acct-cap">
            <thead>
              <tr>
                <th className="l">Capital</th>
                <th className="r">Value</th>
                <th className="r">Note</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="l">Net liquidation</td>
                <td className="r mono">{fmt.usd(a.netLiq)}</td>
                <td className="r">{deltaPill(a.dNetLiq)}</td>
              </tr>
              <tr>
                <td className="l">Cash</td>
                <td className="r mono">{fmt.usd(a.cash)}</td>
                <td className="r">{deltaPill(a.dCash)}</td>
              </tr>
              <tr>
                <td className="l">Init margin</td>
                <td className="r mono">{fmt.usd(a.marginInit)}</td>
                <td className="r acct-note">{a.marginInitPct}% used</td>
              </tr>
              <tr>
                <td className="l">Maint margin</td>
                <td className="r mono">{fmt.usd(a.marginMaint)}</td>
                <td className="r acct-note">{a.marginMaintPct}% used</td>
              </tr>
              <tr>
                <td className="l">Excess liquidity</td>
                <td className="r mono pos">{fmt.usd(a.excessLiq)}</td>
                <td className="r acct-note">—</td>
              </tr>
              <tr>
                <td className="l">Cushion</td>
                <td className="r mono">{(a.cushion * 100).toFixed(1)}%</td>
                <td className="r acct-note">{a.nPositions} positions</td>
              </tr>
              <tr className="acct-sep">
                <td className="l">Gross leverage</td>
                <td className="r mono">{lev.gross.toFixed(1)}M €</td>
                <td className="r acct-note">
                  {grossX}× net liq · €{(netLiqEur / 1e6).toFixed(2)}M
                </td>
              </tr>
              <tr>
                <td className="l">Net leverage</td>
                <td className="r mono">{lev.net.toFixed(1)}M €</td>
                <td className="r acct-note">{netX}× net liq</td>
              </tr>
              <tr>
                <td className="l">Buying power</td>
                <td className="r mono pos">${lev.buyingPower.toFixed(2)}M</td>
                <td className="r acct-note">available</td>
              </tr>
            </tbody>
          </table>
          <CashHoldings cash={cashRows} />
        </div>
      </Panel>

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
        <div className="perf-ticker">
          <div className="perf-sub mono dim">
            EUR/USD <em className="unit">your trades — ▲ open · ● close</em>
          </div>
          <TickerChart spot={spot} markers={tradeMarkers} />
        </div>
        <PerfCharts window={win} ps={ps} unreal={unreal} />
      </Panel>

      <Panel
        title="Realized P&L attribution — bridge"
        dataPp="pnl-attribution"
        className="wf-panel"
      >
        <div className="wf-cell wf-structure-cell">
          <div className="perf-sub mono dim">
            by tenor <em className="unit">P&L · vega · 2nd-order</em>
          </div>
          <TenorTable rows={pivotLive?.tenor ?? []} />
        </div>
        <div className="wf-2col">
          <div className="wf-cell wf-trade">
            <div className="perf-sub mono dim">by trade</div>
            <TradeTable steps={pivotLive?.trade ?? []} />
          </div>
          <div className="wf-cell">
            <div className="perf-sub mono dim">by greek</div>
            <TradeTable steps={pd?.waterfallGreek ?? []} col="Greek" />
          </div>
        </div>
      </Panel>
    </div>
  );
}
