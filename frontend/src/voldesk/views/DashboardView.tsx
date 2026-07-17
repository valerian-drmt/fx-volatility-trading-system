/**
 * VOLDESK — Dashboard: one live summary card per tab (Portfolio / Risk / Trade /
 * Signal). Each card compresses its tab's headline numbers and routes there via
 * the header link — detail lives in the tabs, never here. The Trade card shows
 * the EUR/USD candlestick (session bands + macro-event dots) with the upcoming
 * macro events listed beneath it.
 */
import {
  fetchEquityCurve,
  fetchRegimeEvents,
  fetchRegimeState,
} from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { Bar, Panel, Tag } from "../components/common";
import { gk$, pnlCls, type Tone } from "../components/format";
import type { Status } from "../components/format";
import { TickerChart } from "../components/TickerChart";
import { DATA, DATA2, fmt } from "../data";
import type { Pc, TermPoint } from "../data";
import { useDeskData, useTicks } from "../data/deskData";
import type { FreshStatus } from "../data/freshness";
import { adaptEquityCurve, type EquityPoint } from "../data/live/portfolio";
import { adaptEvents } from "../data/live/trade";

// mini ATM term-structure with σ_fair overlay
function MiniTerm({ ts }: { ts: TermPoint[] }): JSX.Element {
  const w = 250,
    h = 74,
    pl = 6,
    pr = 6,
    pt = 8,
    pb = 16;
  const all = ts.flatMap((t) => [t.atm, t.fair]);
  const lo = Math.min(...all),
    hi = Math.max(...all),
    rng = hi - lo || 1;
  const X = (i: number): number => pl + (i / (ts.length - 1)) * (w - pl - pr);
  const Y = (v: number): number => pt + (1 - (v - lo) / rng) * (h - pt - pb);
  const line = (key: "atm" | "fair"): string =>
    ts.map((t, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(t[key]).toFixed(1)).join(" ");
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <path d={line("fair")} stroke="var(--text-faint)" strokeDasharray="3 2" fill="none" strokeWidth="1.4" />
      <path d={line("atm")} stroke="var(--accent)" fill="none" strokeWidth="1.9" />
      {ts.map((t, i) => (
        <circle key={i} cx={X(i)} cy={Y(t.atm)} r="2.2" fill="var(--accent)" />
      ))}
      {ts.map((t, i) => (
        <text key={"l" + i} x={X(i)} y={h - 4} fill="var(--text-faint)" fontSize="8" fontFamily="var(--mono)" textAnchor="middle">
          {t.tenor}
        </text>
      ))}
    </svg>
  );
}

// 7d net-liq sparkline — colour by first→last direction, light area fill.
function Spark({ pts }: { pts: EquityPoint[] }): JSX.Element {
  const w = 250,
    h = 46,
    pad = 4;
  if (pts.length < 2) return <div className="dim small mono spark-empty">no equity history</div>;
  const t0 = pts[0]!.t,
    t1 = pts[pts.length - 1]!.t;
  const vals = pts.map((p) => p.v);
  const lo = Math.min(...vals),
    hi = Math.max(...vals),
    rng = hi - lo || 1;
  const X = (t: number): number => pad + ((t - t0) / (t1 - t0 || 1)) * (w - 2 * pad);
  const Y = (v: number): number => pad + (1 - (v - lo) / rng) * (h - 2 * pad);
  const d = pts.map((p, i) => (i ? "L" : "M") + X(p.t).toFixed(1) + " " + Y(p.v).toFixed(1)).join(" ");
  const col = vals[vals.length - 1]! >= vals[0]! ? "var(--pos)" : "var(--neg)";
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <path d={`${d} L ${X(t1).toFixed(1)} ${h - pad} L ${X(t0).toFixed(1)} ${h - pad} Z`} fill={col} fillOpacity="0.12" />
      <path d={d} fill="none" stroke={col} strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

// $k loss formatter for VaR/ES values (VarData is in $k, losses by definition).
const lossK = (vk: number): string => {
  const a = Math.abs(vk);
  return "−$" + (a >= 1000 ? (a / 1000).toFixed(2) + "M" : Math.round(a) + "k");
};

// mini VaR table (Risk card) — same √t scaling + percentile math as the Risk
// tab's VarCard, compressed to Horizon / exp. return / VaR 95%.
const VAR_ROWS = [
  { id: "1d", lbl: "Daily", days: 1 },
  { id: "1w", lbl: "Weekly", days: 5 },
  { id: "1M", lbl: "Monthly", days: 21 },
  { id: "1Y", lbl: "Yearly", days: 252 },
];
const kc = (vk: number): string => {
  const s = vk < 0 ? "−" : "+";
  const a = Math.abs(vk);
  return s + "$" + (a >= 1000 ? (a / 1000).toFixed(2) + "M" : Math.round(a) + "k");
};
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

export function DashboardView({ go }: { go: (r: string) => void }): JSX.Element {
  const { pca, portfolio, trade, termStructure, risk } = useDeskData();
  const ticks = useTicks();
  const a = portfolio.data?.account ?? DATA.account,
    g = portfolio.data?.greeks ?? DATA.greeks,
    ps = portfolio.data?.perfStats ?? DATA2.perfStats,
    L = trade.data?.limits ?? DATA.limits;
  const positions = trade.data?.positions ?? DATA.positions;
  const events = trade.data?.events ?? DATA.events;
  const ts = termStructure.data ?? DATA.termStructure;
  const spot = ticks.data?.mid ?? DATA.SPOT;

  // regime gate: live decision (/regime/state → gate.authorized), mock fallback.
  const regimeLive = useFetch(() => fetchRegimeState(), 60_000);
  const gate = regimeLive.data?.gate;
  const gateOpen = gate?.authorized ?? DATA.regime.gate.allowed;
  // per-card fetches: 7d equity sparkline.
  const equity7 = useFetch(() => fetchEquityCurve("7d").then(adaptEquityCurve), 120_000, true, 60_000).data ?? [];

  // surface staleness — degrades the Signal card when the vol surface is down.
  const toTone = (s: FreshStatus): Status => (s === "live" ? "up" : s === "stale" ? "warn" : "down");
  const surfStale = toTone(termStructure.status) === "down";

  // ── Portfolio card numbers
  const grossNominal = positions.reduce((s, p) => s + Math.abs(p.nominal), 0);
  const netLiqEur = a.netLiq / spot;
  const grossX = netLiqEur > 0 ? (grossNominal / netLiqEur).toFixed(2) : "—";

  // ── Risk card numbers
  const v = risk.data;
  const varCapPct = v && L.var99.cap ? Math.round((Math.abs(v.var99) / L.var99.cap) * 100) : null;

  // chart events: same calendar but with a 35d past window (covers the 1M
  // range), so past releases show as filled dots. The list below the chart
  // stays upcoming-only via the trade slice's future-only fetch.
  const chartEvents =
    useFetch(() => fetchRegimeEvents(100, 35).then((raw) => adaptEvents(raw, Date.now())), 300_000).data ?? events;

  // ── Trade card: market open/closed badge — same icon + mapping as the topbar.
  const mktDot = ticks.status === "live" ? "var(--pos)" : ticks.status === "stale" ? "var(--warn)" : "var(--neg)";
  const mktLabel = ticks.status === "live" ? "Market open" : ticks.status === "stale" ? "feed stale" : "no feed";

  // ── Signal card numbers — conviction-ranked by VARIANCE share (PC1 > PC2 > PC3)
  const ranked = [...(pca.data?.pcs ?? DATA.pcs)].sort((x, y) => (y.variance || 0) - (x.variance || 0));
  const lead = ranked[0] ?? null;
  const convW = (pc: Pc): number => Math.sqrt(pc.variance || 0);
  const maxW = Math.max(...ranked.map(convW)) || 1;
  const leadTone: Tone =
    lead?.label === "CHEAP" ? "good" : lead?.label === "RICH" || lead?.label === "EXPENSIVE" ? "danger" : "neutral";
  // IV vs σ_fair per tenor (±0.1 vol-pt threshold) — the Fair-vol gate compressed.
  const nCheap = ts.filter((t) => t.atm - t.fair <= -0.1).length;
  const nRich = ts.filter((t) => t.atm - t.fair >= 0.1).length;

  return (
    <div className="dash-grid">
      {/* one summary card per tab */}
      <div className="dash-cards">
        <Panel
          title="Portfolio"
          dataPp="dash-portfolio"
          right={
            <button className="link-btn" onClick={() => go("portfolio")}>
              Portfolio →
            </button>
          }
          className="dash-card"
        >
          <div className="book-surv dash-kpis">
            <div className="bs-item">
              <span className="gs-lbl">Net liq</span>
              <b className="mono">{fmt.usd(a.netLiq)}</b>
              <span className={"gs-sub " + (a.dNetLiq >= 0 ? "pos" : "neg")}>
                {a.dNetLiq >= 0 ? "▲" : "▼"} {Math.abs(a.dNetLiq).toFixed(2)}% 24h
              </span>
            </div>
            <div className="bs-item">
              <span className="gs-lbl">Day P&L</span>
              <b className={"mono " + pnlCls(a.dayPnl)}>{fmt.usdk(a.dayPnl)}</b>
              <span className="gs-sub dim">session</span>
            </div>
            <div className="bs-item">
              <span className="gs-lbl">Unrealized</span>
              <b className={"mono " + pnlCls(g.netUnreal)}>{fmt.usdk(g.netUnreal)}</b>
              <span className="gs-sub dim">open MTM</span>
            </div>
            <div className="bs-item">
              <span className="gs-lbl">Realized</span>
              <b className={"mono " + pnlCls(ps.cumRealized)}>{fmt.sgn(ps.cumRealized, 1)}k</b>
              <span className="gs-sub dim">cumulative</span>
            </div>
          </div>
          <Bar
            label="Margin used"
            used={fmt.usd(a.marginInit)}
            limit={fmt.usd(a.netLiq)}
            pct={a.marginInitPct}
            value={a.marginInitPct + "%"}
            tone="auto"
          />
          <div className="cap-lev">
            <span className="dim mono small">
              cushion {(a.cushion * 100).toFixed(1)}% · gross {grossX}× net liq · {a.nPositions} positions
            </span>
          </div>
          <div className="mkt-term-head">
            <span className="gs-lbl">Net liq — 7d</span>
          </div>
          <Spark pts={equity7} />
        </Panel>

        <Panel
          title="Risk"
          dataPp="dash-risk"
          right={
            <button className="link-btn" onClick={() => go("risk")}>
              Risk →
            </button>
          }
          className="dash-card"
        >
          <div className="dash-risk-2col">
            <div className="ind-fam">
              <div className="ind-fam-head">Greeks</div>
              <table className="dt greeks-table">
                <thead>
                  <tr>
                    <th className="l">Greek</th>
                    <th className="r">Net value</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="l">Delta <em className="unit">USD</em></td>
                    <td className={"r mono " + pnlCls(g.netDelta)}>{gk$(g.netDelta)}</td>
                  </tr>
                  <tr>
                    <td className="l">Gamma <em className="unit">USD/pip</em></td>
                    <td className={"r mono " + pnlCls(g.netGamma)}>{gk$(g.netGamma)}</td>
                  </tr>
                  <tr>
                    <td className="l">Vega <em className="unit">$/vp</em></td>
                    <td className={"r mono " + pnlCls(g.netVega)}>{gk$(g.netVega)}</td>
                  </tr>
                  <tr>
                    <td className="l">Theta <em className="unit">$/day</em></td>
                    <td className={"r mono " + pnlCls(g.netTheta)}>{gk$(g.netTheta)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="ind-fam">
              <div className="ind-fam-head">
                VaR table <span className="dim">· historical 1d</span>
              </div>
              {v ? (
                <table className="dt var-table">
                  <thead>
                    <tr>
                      <th className="l">Horizon</th>
                      <th className="r">exp. return μt</th>
                      <th className="r">VaR 95%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {VAR_ROWS.map((r) => {
                      const m = Math.sqrt(r.days);
                      const v95 = v.var95 * m;
                      const retk = v.meanDaily * r.days;
                      const sig = Math.abs(v95) / 1.645;
                      const muZ = sig ? retk / sig : 0;
                      return (
                        <tr key={r.id}>
                          <td className="l mono">
                            {r.id} <span className="dim">{r.lbl}</span>
                          </td>
                          <td className={"r mono " + pnlCls(retk)}>
                            {kc(retk)} <span className="dim">({ordinal(Math.round(normCdf(muZ) * 100))})</span>
                          </td>
                          <td className="r mono neg">{kc(v95)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="dim small mono">VaR accumulating…</div>
              )}
            </div>
          </div>
          {varCapPct != null && v && (
            <Bar
              label="VaR 99 vs cap"
              used={lossK(v.var99)}
              limit={"$" + L.var99.cap + "k"}
              pct={varCapPct}
              value={varCapPct + "%"}
              tone="auto"
            />
          )}
        </Panel>

        <Panel
          title="Trade"
          dataPp="dash-trade"
          right={
            <>
              <span className={"tb-badge " + (ticks.status === "live" ? "open" : "")}>
                <span className="status-dot" style={{ background: mktDot }} />
                {mktLabel}
              </span>
              <button className="link-btn" onClick={() => go("trade")}>
                Trade →
              </button>
            </>
          }
          className="dash-card"
        >
          <div className="ind-fam">
            <div className="ind-fam-head">
              Ticker <span className="dim">· EUR/USD</span>
            </div>
            <TickerChart spot={spot} events={chartEvents} />
          </div>
          <div className="ind-fam">
            <div className="ind-fam-head">Macro events</div>
            {events.length ? (
              events.slice(0, 3).map((ev, i) => (
                <div key={i} className="today-evt">
                  <span className="mono accent">{ev.in}</span>
                  <span className="evt-code mono">{ev.code}</span>
                  <span className="dim">
                    {ev.content} · {ev.country}
                  </span>
                  <Tag tone={String(ev.impact).toUpperCase().includes("HIGH") ? "danger" : "neutral"}>{ev.impact}</Tag>
                </div>
              ))
            ) : (
              <div className="today-evt dim">no upcoming events</div>
            )}
          </div>
        </Panel>

        <Panel
          title="Signal"
          dataPp="dash-signal"
          right={
            <button className="link-btn" onClick={() => go("signals")}>
              Signal →
            </button>
          }
          className={"dash-card" + (surfStale ? " df-degraded" : "")}
        >
          {lead ? (
            <>
              <div className="sig-main">
                <div className="sig-pc">
                  <span className="sig-id mono">{lead.id}</span>
                  <span className="dim">{lead.name}</span>
                </div>
                <div className="sig-z mono">
                  <b className={pnlCls(lead.z)}>{fmt.sgn(lead.z, 2)}</b>
                  <span className="dim small">z-score</span>
                </div>
                <Tag tone={leadTone}>{lead.label}</Tag>
              </div>
              <div className="sig-conv dim small mono">
                gate {gateOpen ? "open" : "blocked"}
                {gate?.size_mult != null ? ` · size ×${gate.size_mult}` : ""}
                {gate?.reason ? ` · ${gate.reason}` : ""} · IV vs σ_fair: {nCheap} cheap / {nRich} rich
              </div>
              <div className="sig-rank">
                {ranked.map((pc) => (
                  <div key={pc.id} className="sig-rank-row">
                    <span className="srr-id mono">{pc.id}</span>
                    <span className="srr-name dim">{pc.name}</span>
                    <div className="srr-track">
                      <div className="srr-fill" style={{ width: Math.max(4, (convW(pc) / maxW) * 100) + "%" }} />
                    </div>
                    <span className={"srr-z mono " + pnlCls(pc.z)}>{fmt.sgn(pc.z, 2)}</span>
                    {pc.dataQuality === "noisy" ? (
                      <span className="srr-badge warn mono">low conv · wings noisy</span>
                    ) : (
                      <span className="srr-badge dim mono">{pc.variance}% var</span>
                    )}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="dim small mono">PCA model unavailable (no fit / market closed)</div>
          )}
          <div className="mkt-term-head">
            <span className="gs-lbl">ATM term structure</span>
            <span className="mkt-term-leg mono dim">
              <i className="lg-atm" />
              IV <i className="lg-fair" />
              σ_fair
            </span>
          </div>
          <MiniTerm ts={ts} />
        </Panel>
      </div>
    </div>
  );
}
