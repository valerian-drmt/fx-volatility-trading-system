/**
 * VOLDESK — Portfolio (capital, performance, survival metric, realized
 * attribution bridge, book composition). Ported from the prototype's
 * `js/views_portfolio.jsx` (global-window pattern) into typed ES modules.
 * 1:1 port — same JSX, same classNames, same logic. Mock data for now.
 */
import { useMemo, useState } from "react";
import { Panel, MetricTile, Tag } from "../components/common";
import { pnlCls, gk$ } from "../components/format";
import { CashHoldings } from "../components/PositionsTable";
import { DATA, DATA2, fmt } from "../data";
import type { WaterfallStep } from "../data";

// equity curve + drawdown band
function EquityChart({ window: win }: { window: string }): JSX.Element {
  const data = useMemo<number[]>(() => DATA.equityCurve(win), [win]);
  const w = 760,
    h = 230,
    pl = 52,
    pr = 12,
    pt = 14,
    pb = 40;
  const lo = Math.min(...data),
    hi = Math.max(...data),
    rng = hi - lo || 1;
  const X = (i: number): number => pl + (i / (data.length - 1)) * (w - pl - pr);
  const Y = (v: number): number => pt + (1 - (v - lo) / rng) * (h - pt - pb);
  const d = data.map((v, i) => (i === 0 ? "M" : "L") + X(i).toFixed(1) + " " + Y(v).toFixed(1)).join(" ");
  const up = data[data.length - 1]! >= data[0]!;
  const col = up ? "var(--pos)" : "var(--neg)";
  // drawdown (% from running peak) drawn as a band at the bottom
  let peak = data[0]!;
  const dd = data.map((v) => {
    peak = Math.max(peak, v);
    return (v - peak) / peak;
  });
  const ddMin = Math.min(...dd) || -1;
  const ddTop = h - pb + 6,
    ddH = pb - 14;
  const DY = (x: number): number => ddTop + (x / (ddMin || -1)) * ddH;
  const ddPath =
    "M" +
    X(0) +
    " " +
    ddTop +
    " " +
    dd.map((x, i) => "L" + X(i).toFixed(1) + " " + DY(x).toFixed(1)).join(" ") +
    " L" +
    X(data.length - 1) +
    " " +
    ddTop +
    " Z";
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <defs>
        <linearGradient id="eqg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={col} stopOpacity="0.22" />
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
            <text x={4} y={pt + f * (h - pt - pb) + 3} fill="var(--text-faint)" fontSize="9" fontFamily="var(--mono)">
              {(v / 1e6).toFixed(2)}M
            </text>
          </g>
        );
      })}
      <path d={d + ` L${X(data.length - 1)} ${h - pb} L${pl} ${h - pb} Z`} fill="url(#eqg)" />
      <path d={d} fill="none" stroke={col} strokeWidth="1.8" />
      {/* drawdown band */}
      <path d={ddPath} fill="var(--neg)" fillOpacity="0.16" />
      <line x1={pl} x2={w - pr} y1={ddTop} y2={ddTop} stroke="var(--line)" opacity="0.6" />
      <text x={4} y={ddTop + 3} fill="var(--text-faint)" fontSize="8" fontFamily="var(--mono)">
        DD 0%
      </text>
      <text x={4} y={ddTop + ddH + 2} fill="var(--neg)" fontSize="8" fontFamily="var(--mono)">
        {(ddMin * 100).toFixed(1)}%
      </text>
    </svg>
  );
}

// daily realized P&L bars
function DailyPnlBars(): JSX.Element {
  const data = DATA2.dailyPnl,
    w = 360,
    h = 150,
    pt = 14,
    pb = 18;
  const max = Math.max(...data.map(Math.abs)) || 1;
  const bw = (w - 8) / data.length,
    mid = pt + (h - pt - pb) / 2;
  const sc = (h - pt - pb) / 2 / max;
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: "block" }}>
      <line x1="2" x2={w - 2} y1={mid} y2={mid} stroke="var(--line)" />
      {data.map((v, i) => {
        const cx = 4 + bw * i + bw / 2,
          hh = Math.abs(v) * sc,
          up = v >= 0;
        return (
          <rect
            key={i}
            x={cx - bw * 0.34}
            y={up ? mid - hh : mid}
            width={bw * 0.68}
            height={Math.max(1, hh)}
            rx="1"
            fill={up ? "var(--pos)" : "var(--neg)"}
            fillOpacity="0.85"
          />
        );
      })}
    </svg>
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
        <line x1="0" x2={w} y1={Y(threshold)} y2={Y(threshold)} stroke="var(--text-faint)" strokeDasharray="3 2" />
      )}
      <path d={d} fill="none" stroke={last >= threshold ? "var(--pos)" : "var(--neg)"} strokeWidth="1.6" />
      <circle cx={X(data.length - 1)} cy={Y(last)} r="2.6" fill={last >= threshold ? "var(--pos)" : "var(--neg)"} />
    </svg>
  );
}

// carry vs convexity — survival hero
function CoverageHero(): JSX.Element {
  const c = DATA2.coverage;
  const ok = c.ratio >= c.threshold;
  const tot = c.convexity + c.carry;
  // forward breakeven (implied): move_BE = √(2Θ/Γ) vs current RV — complements the realized ratio
  const g = DATA.greeks;
  const beMove = Math.sqrt((2 * Math.abs(g.theta)) / g.gamma) * 0.225;
  const rvDaily = DATA.termStructure[0]!.rv / Math.sqrt(252);
  const beCovered = rvDaily >= beMove;
  return (
    <>
      <div className="cov-hero">
        <div className="cov-main">
          <span className="cov-lbl mono">
            coverage ratio <span className="dim">· convexity ÷ theta-carry · realized</span>
          </span>
          <div className="cov-num-row">
            <b className={"cov-num mono " + (ok ? "pos" : "neg")}>{c.ratio.toFixed(2)}×</b>
            <span className={"cov-verdict " + (ok ? "ok" : "bad")}>
              {ok ? "convexity paid the carry" : "carry not covered"}
            </span>
          </div>
          <span className="cov-formula mono dim">
            (Σ½Γ(dS)² + ΣV·dσ) ÷ ΣΘ·dt · {c.windowLabel}
          </span>
        </div>
        <div className="cov-spark">
          <CovSpark data={c.history} threshold={c.threshold} />
          <span className="dim small mono">threshold 1.0</span>
        </div>
      </div>
      <div className="c2">
        <div className="cov-bars">
          <div className="cov-bar-row">
            <span className="mono">convexity earned</span>
            <span className="mono pos">+${c.convexity.toFixed(0)}k</span>
          </div>
          <div className="cov-bar-track">
            <div className="cov-bar-fill pos" style={{ width: (c.convexity / tot) * 100 + "%" }} />
          </div>
          <div className="cov-bar-sub dim mono">
            Γ +${c.gammaPnl}k · Vega +${c.vegaPnl}k
          </div>
          <div className="cov-bar-row">
            <span className="mono">
              theta-carry paid <span className="dim">· theta-only</span>
            </span>
            <span className="mono neg">−${c.carry.toFixed(0)}k</span>
          </div>
          <div className="cov-bar-track">
            <div className="cov-bar-fill neg" style={{ width: (c.carry / tot) * 100 + "%" }} />
          </div>
          <div className="cov-bar-sub dim mono">
            Θ −${c.thetaPaid}k / {c.windowLabel} · excl. 6E/JPY funding
          </div>
        </div>
        <div className="cov-ror">
          <div className="ror-item">
            <span className="gs-lbl">P&L / margin</span>
            <b className="mono pos">{c.returnOnMargin.toFixed(1)}%</b>
          </div>
          <div className="ror-item">
            <span className="gs-lbl">P&L / VaR</span>
            <b className="mono pos">{c.returnOnVar.toFixed(2)}×</b>
          </div>
          <div className="ror-item">
            <span className="gs-lbl">
              Realized Sharpe <em className="unit">daily ann.</em>
            </span>
            <b className="mono">{c.sharpe.toFixed(2)}</b>
          </div>
        </div>
      </div>
      <div className="cov-fwd">
        <span className="dim mono">
          forward breakeven <span className="dim">· implied, complements realized</span>
        </span>
        <span className="cov-fwd-eq mono">
          move<sub>BE</sub> = √(2Θ/Γ) = <b>{beMove.toFixed(2)}%/day</b> <span className="dim">vs</span> RV{" "}
          <b>{rvDaily.toFixed(2)}%/day</b>
        </span>
        <Tag tone={beCovered ? "good" : "danger"}>{beCovered ? "convexity pays now" : "carry bleeds now"}</Tag>
      </div>
      <div className="cov-posture">
        <span className="dim mono">measured posture</span>
        <Tag tone="good">{c.posture}</Tag>
        <span className="dim small">
          realized = backward (it paid) · forward = breakeven (does it pay now) · full breakdown → bridge
        </span>
      </div>
    </>
  );
}

interface WaterfallBar extends WaterfallStep {
  base: number;
  top: number;
  isTotal?: boolean;
}

// realized P&L attribution bridge (waterfall)
function Waterfall({ steps }: { steps: WaterfallStep[] }): JSX.Element {
  const w = 660,
    h = 230,
    pt = 26,
    pb = 44,
    pl = 8,
    pr = 8;
  let run = 0;
  const bars: WaterfallBar[] = steps.map((s) => {
    if (s.type === "start") return { ...s, base: 0, top: 0 };
    if (s.type === "net") return { ...s, base: 0, top: s.v, isTotal: true };
    const base = run;
    run += s.v;
    return { ...s, base, top: run };
  });
  const vals = bars.flatMap((b) => [b.base, b.top]).concat(0);
  const lo = Math.min(...vals),
    hi = Math.max(...vals),
    rng = hi - lo || 1;
  const Y = (v: number): number => pt + (1 - (v - lo) / rng) * (h - pt - pb);
  const n = steps.length,
    slot = (w - pl - pr) / n,
    bw = slot * 0.56;
  const col = (s: WaterfallBar): string =>
    s.color
      ? s.color
      : s.type === "net"
        ? "var(--accent)"
        : s.type === "resid"
          ? "var(--muted)"
          : s.v >= 0
            ? "var(--pos)"
            : "var(--neg)";
  const k = (v: number): string => (v >= 0 ? "+" : "−") + "$" + Math.abs(v).toFixed(1) + "k";
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <line x1={pl} x2={w - pr} y1={Y(0)} y2={Y(0)} stroke="var(--line)" />
      {bars.map((s, i) => {
        const cx = pl + slot * i + slot / 2;
        const y0 = Y(s.base),
          y1 = Y(s.top);
        const top = Math.min(y0, y1),
          height = Math.max(2, Math.abs(y1 - y0));
        const isStart = s.type === "start";
        return (
          <g key={i}>
            {i > 0 && !isStart && (
              <line
                x1={pl + slot * (i - 1) + slot / 2 + bw / 2}
                x2={cx - bw / 2}
                y1={Y(bars[i - 1]!.type === "start" ? 0 : bars[i - 1]!.top)}
                y2={Y(s.type === "net" ? 0 : s.base)}
                stroke="var(--text-faint)"
                strokeDasharray="2 2"
                opacity="0.7"
              />
            )}
            {!isStart && (
              <rect
                x={cx - bw / 2}
                y={top}
                width={bw}
                height={height}
                rx="2"
                fill={col(s)}
                fillOpacity={s.type === "net" ? 0.9 : 0.78}
              />
            )}
            {!isStart && (
              <text
                x={cx}
                y={top - 5}
                fill={col(s)}
                fontSize="9.5"
                fontWeight="700"
                fontFamily="var(--mono)"
                textAnchor="middle"
              >
                {s.type === "net" ? k(s.v) : k(s.v)}
              </text>
            )}
            <text
              x={cx}
              y={h - pb + 16}
              fill="var(--fg)"
              fontSize="10.5"
              fontWeight="700"
              fontFamily="var(--mono)"
              textAnchor="middle"
            >
              {s.label}
            </text>
            {s.sub && (
              <text
                x={cx}
                y={h - pb + 28}
                fill="var(--text-faint)"
                fontSize="8"
                fontFamily="var(--mono)"
                textAnchor="middle"
              >
                {s.sub}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

interface StructureFam {
  name: string;
  color: string;
  vanna: number;
  volga: number;
}

function BookComposition(): JSX.Element {
  const vt = DATA2.vegaPerTenor,
    maxV = Math.max(...vt.map((r) => r.vega)) || 1;
  const bc = DATA2.bookComposition;
  // 2nd-order by structure — which structure carries the skew (vanna) and the vol-convexity (volga)
  const fam: StructureFam[] = bc.byStructure.map((s) => {
    const legs = DATA.positions.filter(
      (p) => p.structure.startsWith(s.name) || p.structure.split(" ")[0] === s.name.split(" ")[0]
    );
    return {
      name: s.name,
      color: s.color,
      vanna: legs.reduce((a, p) => a + (p.vanna || 0), 0),
      volga: legs.reduce((a, p) => a + (p.volga || 0), 0),
    };
  });
  return (
    <div className="bookcomp">
      <div className="gs-section-lbl">
        Vega by tenor <span className="dim">· 1M–6M</span>
      </div>
      <div className="vtl">
        {vt.map((r) => (
          <div key={r.tenor} className="vtl-row">
            <span className="vtl-ten mono">{r.tenor}</span>
            <div className="vtl-track">
              <div className="vtl-fill" style={{ width: (r.vega / maxV) * 100 + "%" }} />
            </div>
            <span className="vtl-val mono">${r.vega.toFixed(1)}k</span>
          </div>
        ))}
      </div>
      <div className="gs-section-lbl util-lbl">
        Nominal by structure{" "}
        <span className="dim">
          · {bc.totalNominal.toFixed(1)}M € · {bc.legs} legs
        </span>
      </div>
      <div className="comp-stack">
        {bc.byStructure.map((s) => (
          <div key={s.name} className="comp-seg" style={{ width: s.pct + "%", background: s.color }} title={s.name} />
        ))}
      </div>
      <div className="comp-legend">
        {bc.byStructure.map((s) => (
          <div key={s.name} className="comp-leg-item">
            <i style={{ background: s.color }} />
            <span className="comp-leg-name">{s.name}</span>
            <span className="mono dim">
              {s.nominal.toFixed(2)}M · {s.legs}L
            </span>
          </div>
        ))}
      </div>
      <div className="gs-section-lbl util-lbl">
        2nd-order by structure <span className="dim">· who carries skew / convexity</span>
      </div>
      <table className="dt dense so-table">
        <thead>
          <tr>
            <th className="l">Structure</th>
            <th className="r">
              Vanna <em className="unit">$k</em>
            </th>
            <th className="r">
              Volga <em className="unit">$k</em>
            </th>
          </tr>
        </thead>
        <tbody>
          {fam.map((f) => (
            <tr key={f.name}>
              <td className="l">
                <span className="so-dot" style={{ background: f.color }} />
                {f.name}
              </td>
              <td className={"r mono " + (Math.abs(f.vanna) >= 80 ? "warn" : "dim")}>
                {Math.abs(f.vanna) >= 0.5 ? fmt.sgn(f.vanna, 0) + "k" : "—"}
                {Math.abs(f.vanna) >= 80 ? " ·skew" : ""}
              </td>
              <td className={"r mono dim"}>{Math.abs(f.volga) >= 0.5 ? fmt.sgn(f.volga, 0) + "k" : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="dim small" style={{ marginTop: "6px" }}>
        RR carries the incident skew (net vanna) — the book's #1 VaR factor. Reconciles to Risk net vanna +
        {DATA.greeks.netVanna}k.
      </div>
    </div>
  );
}

export function PortfolioView(): JSX.Element {
  const [win, setWin] = useState<string>("7D");
  const [pivot, setPivot] = useState<string>("greek");
  const a = DATA.account,
    ps = DATA2.perfStats,
    g = DATA.greeks;
  const lev = { gross: 28.5, net: 18.2, buyingPower: 8.74 };
  // §P1 leverage unit bug: notional is in €, net liq in $ — convert to one ccy before dividing
  const netLiqEur = a.netLiq / DATA.SPOT; // $4.22M → €3.89M
  const grossX = (lev.gross / (netLiqEur / 1e6)).toFixed(2);
  const netX = (lev.net / (netLiqEur / 1e6)).toFixed(2);
  // §P1 unrealized single source: read the one engine (= Open positions = Risk = Close)
  const unreal = g.netUnreal;
  // §P3 cash FX residual — non-pair balances (GBP long, JPY short), separate from option Δ
  const gbp = DATA.cash.find((c) => c.ccy === "GBP"),
    jpy = DATA.cash.find((c) => c.ccy === "JPY");
  // §P3 P&L skew — a long-gamma book should show positive skew (many small theta losses, occasional gamma spikes)
  const dp = DATA2.dailyPnl,
    mean = dp.reduce((x, y) => x + y, 0) / dp.length;
  const sd = Math.sqrt(dp.reduce((x, y) => x + (y - mean) ** 2, 0) / dp.length) || 1;
  const pnlSkew = dp.reduce((x, y) => x + ((y - mean) / sd) ** 3, 0) / dp.length;
  return (
    <div className="portfolio-grid">
      <Panel title="Account & capital" className="acct-panel">
        <div className="acct-tiles">
          <MetricTile big label="Net liquidation" value={fmt.usd(a.netLiq)} delta={a.dNetLiq} />
          <MetricTile label="Cash" value={fmt.usd(a.cash)} delta={a.dCash} />
          <MetricTile label="Init margin" value={fmt.usd(a.marginInit)} sub={a.marginInitPct + "% used"} />
          <MetricTile label="Maint margin" value={fmt.usd(a.marginMaint)} sub={a.marginMaintPct + "% used"} />
          <MetricTile label="Excess liquidity" value={fmt.usd(a.excessLiq)} tone="pos" />
          <MetricTile label="Cushion" value={(a.cushion * 100).toFixed(1) + "%"} sub={a.nPositions + " positions"} />
        </div>
        <div className="lev-strip">
          <div className="lev-item">
            <span className="gs-lbl">Gross leverage</span>
            <b className="mono">{lev.gross.toFixed(1)}M €</b>
            <span className="gs-sub mono dim">
              {grossX}× net liq <em className="unit">€{(netLiqEur / 1e6).toFixed(2)}M</em>
            </span>
          </div>
          <div className="lev-item">
            <span className="gs-lbl">Net leverage</span>
            <b className="mono">{lev.net.toFixed(1)}M €</b>
            <span className="gs-sub mono dim">
              {netX}× net liq <em className="unit">€</em>
            </span>
          </div>
          <div className="lev-item">
            <span className="gs-lbl">Buying power</span>
            <b className="mono pos">${lev.buyingPower.toFixed(2)}M</b>
            <span className="gs-sub mono dim">available</span>
          </div>
          <div className="lev-item lev-fx">
            <span className="gs-lbl">
              FX residual <em className="unit">cash</em>
            </span>
            <b className="mono">
              GBP <span className="pos">{gk$(gbp?.usd)}</span> · JPY <span className="neg">{gk$(jpy?.usd)}</span>
            </b>
            <span className="gs-sub mono dim">settlement residue · not an option Δ</span>
          </div>
        </div>
        <CashHoldings />
      </Panel>

      <Panel
        title="Performance"
        right={
          <div className="tf-group">
            {["1D", "7D", "30D", "1Y", "all"].map((wn) => (
              <button key={wn} className={"chip " + (win === wn ? "on" : "")} onClick={() => setWin(wn)}>
                {wn}
              </button>
            ))}
          </div>
        }
        className="perf-panel"
      >
        <div className="perf-grid">
          <div className="perf-eq">
            <div className="perf-sub mono dim">equity curve · drawdown band</div>
            <EquityChart window={win} />
          </div>
          <div className="perf-daily">
            <div className="perf-sub mono dim">daily realized P&L · hit rate {ps.hitRate.toFixed(0)}%</div>
            <DailyPnlBars />
          </div>
        </div>
        <div className="perf-stats">
          <div className="ps-item">
            <span className="gs-lbl">Cumulative realized</span>
            <b className="mono pos">+${ps.cumRealized}k</b>
          </div>
          <div className="ps-item">
            <span className="gs-lbl">
              Unrealized <em className="unit">one engine</em>
            </span>
            <b className={"mono " + pnlCls(unreal)}>{fmt.usdk(unreal)}</b>
          </div>
          <div className="ps-item">
            <span className="gs-lbl">Max drawdown</span>
            <b className="mono neg">{ps.maxDd}%</b>
          </div>
          <div className="ps-item">
            <span className="gs-lbl">Current DD</span>
            <b className="mono neg">{ps.currentDd}%</b>
          </div>
          <div className="ps-item">
            <span className="gs-lbl">
              Realized Sharpe <em className="unit">daily ann. · 22 sess.</em>
            </span>
            <b className="mono">{ps.sharpe.toFixed(2)}</b>
          </div>
          <div className="ps-item">
            <span className="gs-lbl">P&L skew</span>
            <b className={"mono " + (pnlSkew >= 0 ? "pos" : "neg")}>{fmt.sgn(pnlSkew, 2)}</b>
            <span className="gs-sub mono dim">{pnlSkew >= 0 ? "long-γ signature ✓" : "⚠ vs long-γ"}</span>
          </div>
        </div>
      </Panel>

      <Panel title="Carry vs convexity — survival metric" className="cov-panel">
        <CoverageHero />
      </Panel>

      <div className="row2 pf-row2">
        <Panel
          title="Realized P&L attribution — bridge"
          right={
            <div className="tf-group">
              {["greek", "structure", "tenor", "mode"].map((p) => (
                <button key={p} className={"chip " + (pivot === p ? "on" : "")} onClick={() => setPivot(p)}>
                  by {p}
                </button>
              ))}
            </div>
          }
          className="wf-panel"
        >
          <Waterfall steps={DATA2.waterfall[pivot] ?? []} />
          {pivot === "mode" ? (
            <div className="attrib-note dim small">
              signal → structure → realized P&L, mapped to PC1/2/3 (the modes traded) · forward realized tracking, not a
              backtest · feeds conviction weighting in Signal · skew = incident
            </div>
          ) : (
            <div className="attrib-note dim small">
              residual = explained vs realized — greeks health check (large residual = model drift) · base matches Risk
              (Γ/V/Θ/Δ + vanna/volga) so residual is truly unexplained · lookback {DATA2.coverage.windowLabel}
            </div>
          )}
        </Panel>
        <Panel title="Book composition" className="bookcomp-panel">
          <BookComposition />
        </Panel>
      </div>
    </div>
  );
}
