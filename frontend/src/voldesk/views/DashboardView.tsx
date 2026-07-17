/**
 * VOLDESK — Dashboard: one live summary card per tab (Portfolio / Risk / Trade /
 * Signal). Each card compresses its tab's headline numbers and routes there via
 * the header link — detail lives in the tabs, never here. The Trade card shows
 * the EUR/USD candlestick (session bands + macro-event dots) with the upcoming
 * macro events listed beneath it.
 */
import type { ReactNode } from "react";
import { fetchEquityCurve, fetchRegimeEvents } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { Panel, Tag } from "../components/common";
import { gk$, pnlCls, type Tone } from "../components/format";
import type { Status } from "../components/format";
import { PERF_WINS, recapDd, recapMoney, recapRow, type RecapRow } from "../components/perfRecap";
import { TickerChart } from "../components/TickerChart";
import { DATA, fmt } from "../data";
import type { Cash } from "../data";
import { useDeskData, useTicks } from "../data/deskData";
import type { FreshStatus } from "../data/freshness";
import { adaptEquityCurve, type EquityPoint, type GreekSeries } from "../data/live/portfolio";
import { adaptEvents } from "../data/live/trade";
import { ModeCard } from "./SignalsView";

// per-panel data-source indicator (live / stale / no-data) — same look as the
// Risk tab's PanelLive, driven by a desk-domain freshness status.
const IMPACT_TONE: Record<string, Tone> = { high: "danger", medium: "warn", low: "neutral" };
function LiveBadge({ status }: { status: FreshStatus }): JSX.Element {
  const cfg = {
    live: { c: "var(--pos)", t: "live", pulse: true },
    stale: { c: "var(--warn)", t: "stale", pulse: false },
    missing: { c: "var(--muted)", t: "no data", pulse: false },
  }[status];
  return (
    <span className="panel-live dim mono small" title="data feed status">
      <span className={"status-dot" + (cfg.pulse ? " pulse" : "")} style={{ background: cfg.c }} /> {cfg.t}
    </span>
  );
}

// ▲/▼ change pill + parenthetical note — same helpers as the Portfolio tab's
// Cash & margin table (classes .acct-delta / .acct-sub are global).
function deltaPill(d: number | null | undefined): JSX.Element | null {
  if (d == null || !Number.isFinite(d)) return null;
  const neg = d < 0;
  return (
    <span className={"acct-delta " + (neg ? "neg" : "pos")}>
      {neg ? "▼" : "▲"} {Math.abs(d).toFixed(2)}%
    </span>
  );
}
function acctNote(note: ReactNode): JSX.Element | null {
  return note ? <span className="acct-sub"> ({note})</span> : null;
}

// Holdings valuation as a donut — same decomposition as the Portfolio tab's
// Holdings table (USD cash / EUR cash / contracts as the residual net liq −
// cash, so the parts always foot exactly to net liq in the centre).
const DONUT_PARTS: { key: string; label: string; color: string }[] = [
  { key: "usd", label: "USD cash", color: "var(--accent)" },
  { key: "eur", label: "EUR cash", color: "#a78bfa" },
  { key: "contracts", label: "Contracts", color: "#2dd4bf" },
];

function HoldingsDonut({ netLiq, cash }: { netLiq: number; cash: Cash[] }): JSX.Element {
  const usd = cash.find((c) => c.ccy === "USD")?.usd ?? 0;
  const eur = cash.find((c) => c.ccy === "EUR")?.usd ?? 0;
  const contracts = netLiq - cash.reduce((s, c) => s + c.usd, 0);
  const vals: Record<string, number> = { usd, eur, contracts };
  const totAbs = DONUT_PARTS.reduce((s, p) => s + Math.abs(vals[p.key]!), 0) || 1;
  const R = 40;
  const C = 2 * Math.PI * R;
  let acc = 0;
  return (
    <div className="hold-donut">
      <svg width="118" height="118" viewBox="0 0 118 118">
        {DONUT_PARTS.map((p) => {
          const frac = Math.abs(vals[p.key]!) / totAbs;
          const off = -acc * C;
          acc += frac;
          return (
            <circle
              key={p.key}
              cx="59"
              cy="59"
              r={R}
              fill="none"
              stroke={p.color}
              strokeWidth="14"
              strokeDasharray={`${frac * C} ${C - frac * C}`}
              strokeDashoffset={off}
              transform="rotate(-90 59 59)"
            />
          );
        })}
        <text x="59" y="54" textAnchor="middle" fontSize="9" fill="var(--text-dim)">
          Total
        </text>
        <text x="59" y="67" textAnchor="middle" fontSize="10.5" fontWeight={700} fontFamily="var(--mono)" fill="var(--fg)">
          {fmt.usd(netLiq)}
        </text>
      </svg>
      <div className="hold-donut-leg">
        {DONUT_PARTS.map((p) => (
          <div key={p.key} className="hdl-row">
            <span className="val-dot" style={{ background: p.color }} />
            <span className="hdl-lbl dim">{p.label}</span>
            <span className={"mono " + pnlCls(vals[p.key]!)}>{fmt.usd(vals[p.key]!)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

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
    g = portfolio.data?.greeks ?? DATA.greeks;
  const events = trade.data?.events ?? DATA.events;
  const spot = ticks.data?.mid ?? DATA.SPOT;

  // surface staleness — degrades the Signal card when the vol surface is down.
  const toTone = (s: FreshStatus): Status => (s === "live" ? "up" : s === "stale" ? "warn" : "down");
  const surfStale = toTone(termStructure.status) === "down";

  // ── Portfolio card: live per-currency cash for the holdings donut.
  const liveCash = trade.data?.cash;
  const cashRows = liveCash && liveCash.length > 0 ? liveCash : DATA.cash;
  // Leverage from the live book — same math as the Portfolio tab's "Leverage &
  // buying power" table: gross = Σ|notional| (€), net = |Σ signed| (€), ratios
  // vs net liq converted $→€ at live spot.
  const positions = trade.data?.positions ?? DATA.positions;
  const grossLevM = positions.reduce((s, p) => s + Math.abs(p.nominal), 0) / 1e6;
  const netLevM = Math.abs(positions.reduce((s, p) => s + (p.side === "BUY" ? p.nominal : -p.nominal), 0)) / 1e6;
  const netLiqEurM = a.netLiq / spot / 1e6;
  const grossX = netLiqEurM ? (grossLevM / netLiqEurM).toFixed(2) : "—";
  const netX = netLiqEurM ? (netLevM / netLiqEurM).toFixed(2) : "—";
  // per-window performance recap — one equity sweep over the 5 windows (the
  // dashboard table has no greek columns, so no greek-P&L fetch here).
  const NO_GREEKS: GreekSeries = { delta: [], gamma: [], vega: [], theta: [] };
  const recapRows =
    useFetch<RecapRow[]>(
      () =>
        Promise.all(
          PERF_WINS.map(async (wn) => {
            const pts = await fetchEquityCurve(wn.v.toLowerCase())
              .then(adaptEquityCurve)
              .catch((): EquityPoint[] => []);
            return recapRow(wn.v, pts, NO_GREEKS);
          }),
        ),
      300_000,
    ).data ?? [];

  // ── Risk card numbers
  const v = risk.data;

  // chart events: same calendar but with a 35d past window (covers the 1M
  // range), so past releases show as filled dots. The list below the chart
  // stays upcoming-only via the trade slice's future-only fetch.
  const chartEvents =
    useFetch(() => fetchRegimeEvents(100, 35).then((raw) => adaptEvents(raw, Date.now())), 300_000).data ?? events;

  // ── Trade card: market open/closed badge — same icon + mapping as the topbar.
  const mktDot = ticks.status === "live" ? "var(--pos)" : ticks.status === "stale" ? "var(--warn)" : "var(--neg)";
  const mktLabel = ticks.status === "live" ? "Market open" : ticks.status === "stale" ? "feed stale" : "no feed";

  // ── Signal card — the 3 live PC mode cards (PC1/PC2/PC3, 3M z-history view).
  const pcsLive = pca.data?.pcs ?? [];

  return (
    <div className="dash-grid">
      {/* one summary card per tab */}
      <div className="dash-cards">
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
          <TickerChart spot={spot} events={chartEvents} height={232} />
          <Panel
            title="Macro events"
            dataPp="dash-macro"
            right={<LiveBadge status={trade.status} />}
            className="risk-macro-panel"
            scroll
          >
            <div className="evt-list">
              {events.length === 0 ? (
                <div className="dim mono small">no scheduled events</div>
              ) : (
                events.map((e, i) => (
                  <div key={i} className="evt-item">
                    <div className="evt-when mono">
                      <span className="evt-in accent">{e.in}</span>
                      <span className="dim small">{e.date.split(",")[0]}</span>
                    </div>
                    <div className="evt-body">
                      <span className="evt-code mono">{e.code}</span>
                      <span className="evt-name">{e.content}</span>
                      <span className="dim mono small"> · {e.country}</span>
                    </div>
                    <Tag tone={IMPACT_TONE[e.impact] ?? "neutral"}>{e.impact}</Tag>
                  </div>
                ))
              )}
            </div>
          </Panel>
        </Panel>

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
          <div className="dash-pf-2col">
            <div className="ind-fam">
              <div className="ind-fam-head">Cash &amp; margin</div>
              <table className="dt greeks-table">
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
                    <td className="l">Gross leverage</td>
                    <td className="r mono">
                      {grossLevM.toFixed(1)}M €{acctNote(`${grossX}× net liq · €${netLiqEurM.toFixed(2)}M`)}
                    </td>
                  </tr>
                  <tr>
                    <td className="l">Net leverage</td>
                    <td className="r mono">
                      {netLevM.toFixed(1)}M €{acctNote(`${netX}× net liq`)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="ind-fam">
              <div className="ind-fam-head">Holdings</div>
              <HoldingsDonut netLiq={a.netLiq} cash={cashRows} />
            </div>
          </div>
          <div className="ind-fam dash-perf-block">
            <div className="ind-fam-head ifh-split">
              <span>Performance</span>
              <span className="ifh-right mono">
                <span className="dim">Unrealized </span>
                {recapMoney(g.netUnreal)}
              </span>
            </div>
            <div className="table-scroll">
              <table className="dt var-table">
                <thead>
                  <tr>
                    <th className="l">Window</th>
                    <th className="r">Realized</th>
                    <th className="r">Current DD</th>
                  </tr>
                </thead>
                <tbody>
                  {PERF_WINS.map((wn) => {
                    const r = recapRows.find((x) => x.w === wn.v);
                    return (
                      <tr key={wn.v}>
                        <td className="l mono">
                          {wn.l} <span className="dim">{wn.v === "all" ? "since start" : ""}</span>
                        </td>
                        <td className="r mono">{recapMoney(r?.pnl ?? null)}</td>
                        <td className="r mono">{recapDd(r?.curDd ?? null)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
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
              <div className="ind-fam-head">VaR table</div>
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
          {pcsLive.length ? (
            <div className="dash-sig-3col">
              {pcsLive.slice(0, 3).map((pc) => (
                <ModeCard key={pc.id} pc={pc} view="3M" showLoadings={false} />
              ))}
            </div>
          ) : (
            <div className="dim small mono">PCA model unavailable (no fit / market closed)</div>
          )}
        </Panel>
      </div>
    </div>
  );
}
