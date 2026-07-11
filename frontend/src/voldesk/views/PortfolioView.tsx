/**
 * VOLDESK — Portfolio (capital, performance, survival metric, realized
 * attribution bridge, book composition). Ported from the prototype's
 * `js/views_portfolio.jsx` (global-window pattern) into typed ES modules.
 * 1:1 port — same JSX, same classNames, same logic. Mock data for now.
 */
import { useEffect, useRef, useState } from "react";
import {
  fetchEquityCurve,
  fetchPnlAttribution,
  fetchPnlAttributionPivot,
} from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { useTicks } from "../../hooks/streams";
import { Panel, Tag } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { pnlCls } from "../components/format";
import { CashHoldings } from "../components/PositionsTable";
import { DATA, DATA2, fmt } from "../data";
import type { PerfStats, WaterfallStep } from "../data";
import { useDeskData } from "../data/deskData";
import {
  adaptCoverage,
  adaptEquityCurve,
  adaptStructureRows,
  adaptTenorRows,
  adaptWaterfallPivot,
  type StructureRow,
  type TenorRow,
} from "../data/live/portfolio";

// Equity curve (cumulative P&L) — the top graph. Live-only: empty until data.
function EquityLineSvg({ data, status }: { data: number[]; status: string }): JSX.Element {
  const w = 760,
    h = 168,
    pl = 52,
    pr = 12,
    pt = 14,
    pb = 22;
  if (data.length < 2) {
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
  const lo = Math.min(...data),
    hi = Math.max(...data),
    rng = hi - lo || 1;
  const X = (i: number): number => pl + (i / (data.length - 1)) * (w - pl - pr);
  const Y = (v: number): number => pt + (1 - (v - lo) / rng) * (h - pt - pb);
  const d = data
    .map((v, i) => (i === 0 ? "M" : "L") + X(i).toFixed(1) + " " + Y(v).toFixed(1))
    .join(" ");
  // Neutral colour — the equity line shouldn't imply good/bad by its slope.
  const col = "var(--accent)";
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
      <path d={d + ` L${X(data.length - 1)} ${h - pb} L${pl} ${h - pb} Z`} fill="url(#eqg)" />
      <path
        d={d}
        fill="none"
        stroke={col}
        strokeWidth="2.2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

// Drawdown (% from running peak) — the bottom graph, from the same equity series.
function DrawdownSvg({ data, status }: { data: number[]; status: string }): JSX.Element {
  const w = 760,
    h = 148,
    pl = 52,
    pr = 12,
    pt = 14,
    pb = 22;
  if (data.length < 2) {
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
  let peak = data[0]!;
  const dd = data.map((v) => {
    peak = Math.max(peak, v);
    return (v - peak) / peak;
  });
  const ddMin = Math.min(...dd, -0.0001);
  const X = (i: number): number => pl + (i / (data.length - 1)) * (w - pl - pr);
  const base = pt; // 0% at the top — the underwater surface
  const floor = h - pb;
  const Y = (x: number): number => base + (x / ddMin) * (floor - base); // 0 → top, ddMin → bottom
  const line = dd
    .map((x, i) => (i === 0 ? "M" : "L") + X(i).toFixed(1) + " " + Y(x).toFixed(1))
    .join(" ");
  // Underwater area: a bold SOLID fill hanging DOWN from the 0% surface (fill-forward
  // style, distinct from the equity line-forward chart).
  const area =
    "M" +
    X(0).toFixed(1) +
    " " +
    base +
    " " +
    dd.map((x, i) => "L" + X(i).toFixed(1) + " " + Y(x).toFixed(1)).join(" ") +
    " L" +
    X(data.length - 1).toFixed(1) +
    " " +
    base +
    " Z";
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      {/* scale: 0% water surface (emphasised) + floor */}
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
      <path d={area} fill="var(--neg)" fillOpacity="0.34" />
      <path d={line} fill="none" stroke="var(--neg)" strokeWidth="1.2" opacity="0.75" />
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
  const live = useFetch<number[]>(
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
  const data = live.data ?? [];
  return (
    <div className="perf-v">
      <div className="perf-row">
        <div className="perf-side">
          <table className="dt greeks-table acct-cap">
            <tbody>
              <tr>
                <td className="l">Realized</td>
                <td className={"r mono " + pnlCls(ps.cumRealized)}>
                  {fmt.sgn(ps.cumRealized, 1)}k
                </td>
              </tr>
              <tr>
                <td className="l">Unrealized</td>
                <td className={"r mono " + pnlCls(unreal)}>{fmt.usdk(unreal)}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="perf-chart">
          <div className="perf-sub mono dim">
            P&L <em className="unit">equity curve</em>
          </div>
          <EquityLineSvg data={data} status={live.status} />
        </div>
      </div>
      <div className="perf-row">
        <div className="perf-side">
          <table className="dt greeks-table acct-cap">
            <tbody>
              <tr>
                <td className="l">Max drawdown</td>
                <td className="r mono neg">{ps.maxDd}%</td>
              </tr>
              <tr>
                <td className="l">Current DD</td>
                <td className="r mono neg">{ps.currentDd}%</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="perf-chart">
          <div className="perf-sub mono dim">
            Drawdown <em className="unit">% from peak</em>
          </div>
          <DrawdownSvg data={data} status={live.status} />
        </div>
      </div>
    </div>
  );
}

function CovSpark({
  data,
  threshold,
  w = 150,
  h = 34,
}: {
  data: number[];
  threshold: number;
  w?: number;
  h?: number;
}): JSX.Element {
  const lo = Math.min(...data, threshold),
    hi = Math.max(...data, threshold),
    rng = hi - lo || 1;
  const X = (i: number): number => (i / (data.length - 1)) * w;
  const Y = (v: number): number => 3 + (1 - (v - lo) / rng) * (h - 6);
  const d = data.map((v, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1)).join(" ");
  const last = data[data.length - 1]!;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      {threshold != null && (
        <line
          x1="0"
          x2={w}
          y1={Y(threshold)}
          y2={Y(threshold)}
          stroke="var(--text-faint)"
          strokeDasharray="3 2"
        />
      )}
      <path
        d={d}
        fill="none"
        stroke={last >= threshold ? "var(--pos)" : "var(--neg)"}
        strokeWidth="1.6"
      />
      <circle
        cx={X(data.length - 1)}
        cy={Y(last)}
        r="2.6"
        fill={last >= threshold ? "var(--pos)" : "var(--neg)"}
      />
    </svg>
  );
}

// carry vs convexity — survival hero
function CoverageHero(): JSX.Element {
  // convexity/carry/ratio/greek-PnL/posture live (from /pnl-attribution totals). The perf
  // trio (RoM/RoVaR/Sharpe) needs realized trading history → deferred (R12+, like backtest).
  const covLive = useFetch(() => fetchPnlAttribution().then(adaptCoverage), 60_000).data;
  const c = { ...DATA2.coverage, ...(covLive ?? {}) };
  const ok = c.ratio >= c.threshold;
  // forward breakeven (implied): move_BE = √(2Θ/Γ) vs current RV — from the LIVE book
  // greeks + live ATM RV (mock only until they load).
  const { portfolio: pf, termStructure: term } = useDeskData();
  const g = pf.data?.greeks ?? DATA.greeks;
  const rvNear = term.data?.[0]?.rv ?? DATA.termStructure[0]!.rv;
  const beMove = g.gamma ? Math.sqrt((2 * Math.abs(g.theta)) / g.gamma) * 0.225 : 0;
  const rvDaily = rvNear / Math.sqrt(252);
  const beCovered = rvDaily >= beMove;
  // REALIZED (backward) — earned vs paid on a shared scale, so the ratio is visible.
  const earned = c.convexity,
    paid = c.carry;
  const maxSide = Math.max(1, Math.abs(earned), Math.abs(paid));
  const untested = Math.abs(earned) < 0.05 && Math.abs(paid) < 0.05;
  // FORWARD (implied) — breakeven move vs realized daily move, on a shared scale.
  const maxMove = Math.max(1e-4, beMove, rvDaily);
  const fwdRatio = beMove > 0 ? rvDaily / beMove : null;
  return (
    <div className="cov2">
      <div className="cov2-head">
        <span className="cov2-title mono">is gamma paying its rent?</span>
        <Tag tone="good">{c.posture}</Tag>
      </div>
      <div className="cov2-grid">
        {/* REALIZED · backward — did it pay? */}
        <div className="cov2-cell">
          <div className="cov2-cell-hd mono dim">
            realized <em className="unit">did it pay?</em>
          </div>
          <div className="cov2-num-row">
            <b
              className={"cov2-num mono " + (ok ? "pos" : "neg")}
              title={`(Σ½Γ(dS)² + ΣV·dσ) ÷ ΣΘ·dt · ${c.windowLabel}`}
            >
              {c.ratio.toFixed(2)}×
            </b>
            <span className={"cov-verdict " + (ok ? "ok" : "bad")}>
              {ok ? "convexity paid the carry" : "carry not covered"}
            </span>
          </div>
          {untested ? (
            <div className="cov2-empty dim small mono">
              gamma untested · no move booked this {c.windowLabel}
            </div>
          ) : (
            <div className="cov2-tug">
              <div className="cov2-tug-row">
                <span className="cov2-tug-lbl mono">earned</span>
                <span className="cov2-tug-track">
                  <span
                    className="cov2-tug-fill pos"
                    style={{ width: (Math.abs(earned) / maxSide) * 100 + "%" }}
                  />
                </span>
                <span className="cov2-tug-val mono pos">+${earned.toFixed(0)}k</span>
              </div>
              <div className="cov2-tug-row">
                <span className="cov2-tug-lbl mono">paid</span>
                <span className="cov2-tug-track">
                  <span
                    className="cov2-tug-fill neg"
                    style={{ width: (Math.abs(paid) / maxSide) * 100 + "%" }}
                  />
                </span>
                <span className="cov2-tug-val mono neg">−${paid.toFixed(0)}k</span>
              </div>
            </div>
          )}
          <div className="cov2-foot dim mono">
            Γ +${c.gammaPnl}k · Vega +${c.vegaPnl}k · Θ −${c.thetaPaid}k
          </div>
        </div>
        {/* FORWARD · implied — does it pay now? */}
        <div className="cov2-cell">
          <div className="cov2-cell-hd mono dim">
            forward <em className="unit">does it pay now?</em>
          </div>
          <div className="cov2-num-row">
            <b className={"cov2-num mono " + (beCovered ? "pos" : "neg")}>
              {fwdRatio == null ? "—" : fwdRatio.toFixed(2) + "×"}
            </b>
            <span className={"cov-verdict " + (beCovered ? "ok" : "bad")}>
              {fwdRatio == null ? "no gamma" : beCovered ? "gamma pays now" : "carry bleeds now"}
            </span>
          </div>
          <div className="cov2-tug">
            <div className="cov2-tug-row">
              <span className="cov2-tug-lbl mono">
                need <em className="unit">BE</em>
              </span>
              <span className="cov2-tug-track">
                <span
                  className="cov2-tug-fill neg"
                  style={{ width: (beMove / maxMove) * 100 + "%" }}
                />
              </span>
              <span className="cov2-tug-val mono">{beMove.toFixed(2)}%</span>
            </div>
            <div className="cov2-tug-row">
              <span className="cov2-tug-lbl mono">
                have <em className="unit">RV</em>
              </span>
              <span className="cov2-tug-track">
                <span
                  className="cov2-tug-fill pos"
                  style={{ width: (rvDaily / maxMove) * 100 + "%" }}
                />
              </span>
              <span className="cov2-tug-val mono">{rvDaily.toFixed(2)}%</span>
            </div>
          </div>
          <div className="cov2-foot dim mono">
            move<sub>BE</sub> = √(2Θ/Γ) %/day · vs realized daily vol
          </div>
        </div>
      </div>
      <div className="cov2-spark">
        <CovSpark data={c.history} threshold={c.threshold} w={260} />
        <span className="dim small mono">coverage vs threshold 1.0 · {c.windowLabel}</span>
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

// By-structure breakdown — Position-breakdown-styled table: P&L (%), nominal (%),
// and the two 2nd-order greeks (vanna / volga) per structure type.
function StructureTable({ rows }: { rows: StructureRow[] }): JSX.Element {
  if (rows.length === 0) return <div className="hbar-empty dim small mono">no positions</div>;
  const gains = rows.filter((r) => r.pnl > 0).reduce((s, r) => s + r.pnl, 0);
  const losses = rows.filter((r) => r.pnl < 0).reduce((s, r) => s + Math.abs(r.pnl), 0);
  const totNom = rows.reduce((s, r) => s + Math.abs(r.nominal), 0) || 1;
  const pnlPct = (v: number): string => {
    const base = v >= 0 ? gains : losses;
    return (v >= 0 ? "+" : "−") + (base ? Math.round((Math.abs(v) / base) * 100) : 0) + "%";
  };
  return (
    <div className="table-scroll">
      <table className="dt pb-table wf-structure">
        <thead>
          <tr>
            <th className="l grp-fix">Structure</th>
            <th className="r grp-pnl col-grp">P&L</th>
            <th className="r grp-pnl col-grp-end">%</th>
            <th className="r grp-fix col-grp">Nominal €</th>
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
              <td className="r mono dim grp-fix col-grp">{(r.nominal / 1e6).toFixed(2)}M</td>
              <td className="r mono dim grp-fix col-grp-end">
                {Math.round((Math.abs(r.nominal) / totNom) * 100)}%
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

// By-tenor breakdown — same Position-breakdown-styled table as StructureTable, with
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
  const dailyPnlData = pd?.dailyPnl ?? DATA2.dailyPnl;
  // Non-greek attribution pivots (by structure / by tenor) — realized P&L bridged
  // from closed booked positions. Polled so the bridge stays live; "by mode" (PCA)
  // stays deferred.
  const pivotLive = useFetch(
    async () => {
      const [structure, tenor, trade] = await Promise.all([
        fetchPnlAttributionPivot("structure").then(adaptStructureRows),
        fetchPnlAttributionPivot("tenor").then(adaptTenorRows),
        fetchPnlAttributionPivot("trade").then(adaptWaterfallPivot),
      ]);
      return { structure, tenor, trade };
    },
    120_000,
    true,
    60_000,
  ).data;
  // Live EURUSD spot (WS ticks) for the $→€ conversions; mock only until a tick lands.
  const spot = useTicks().data?.mid ?? DATA.SPOT;
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
  // §P3 P&L skew — a long-gamma book should show positive skew (many small theta losses, occasional gamma spikes)
  const dp = dailyPnlData,
    mean = dp.length ? dp.reduce((x, y) => x + y, 0) / dp.length : 0;
  const sd = Math.sqrt(dp.reduce((x, y) => x + (y - mean) ** 2, 0) / dp.length) || 1;
  const pnlSkew = dp.reduce((x, y) => x + ((y - mean) / sd) ** 3, 0) / dp.length;
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

      <div className="pf-perf-row">
        <Panel
          title="Performance"
          dataPp="perf"
          right={
            <div className="tf-group">
              {["1D", "7D", "30D", "1Y", "all"].map((wn) => (
                <button
                  key={wn}
                  className={"chip " + (win === wn ? "on" : "")}
                  onClick={() => setWin(wn)}
                >
                  {wn}
                </button>
              ))}
            </div>
          }
          className="perf-panel"
        >
          <PerfCharts window={win} ps={ps} unreal={unreal} />
          <div className="perf-foot">
            <div className="ps-item">
              <span className="gs-lbl">
                Hit rate <em className="unit">realized Sharpe {ps.sharpe.toFixed(2)}</em>
              </span>
              <b className="mono">{ps.hitRateNull ? "—" : ps.hitRate.toFixed(0) + "%"}</b>
            </div>
            <div className="ps-item">
              <span className="gs-lbl">P&L skew</span>
              <b className={"mono " + (pnlSkew >= 0 ? "pos" : "neg")}>{fmt.sgn(pnlSkew, 2)}</b>
              <span className="gs-sub mono dim">
                {pnlSkew >= 0 ? "long-γ signature ✓" : "⚠ vs long-γ"}
              </span>
            </div>
          </div>
        </Panel>
        <Panel
          title="Carry vs convexity — survival metric"
          dataPp="carry-convex"
          className="cov-panel"
        >
          <CoverageHero />
        </Panel>
      </div>

      <Panel
        title="Realized P&L attribution — bridge"
        dataPp="pnl-attribution"
        className="wf-panel"
      >
        <div className="wf-cell wf-structure-cell">
          <div className="perf-sub mono dim">
            by structure <em className="unit">P&L · nominal · 2nd-order</em>
          </div>
          <StructureTable rows={pivotLive?.structure ?? []} />
        </div>
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
