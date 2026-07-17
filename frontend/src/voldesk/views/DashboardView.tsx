/**
 * VOLDESK — Dashboard: one live summary card per tab (Portfolio / Risk / Trade /
 * Signal). Each card compresses its tab's headline numbers and routes there via
 * the header link — detail lives in the tabs, never here. The Trade card shows
 * the EUR/USD candlestick (session bands + macro-event dots) with the upcoming
 * macro events listed beneath it.
 */
import type { ReactNode } from "react";
import { fetchRegimeEvents, fetchRegimeState } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { Panel, Tag } from "../components/common";
import { gk$, pnlCls, type Tone } from "../components/format";
import type { Status } from "../components/format";
import { TickerChart } from "../components/TickerChart";
import { DATA, DATA2, fmt } from "../data";
import type { Cash, Pc, TermPoint } from "../data";
import { useDeskData, useTicks } from "../data/deskData";
import type { FreshStatus } from "../data/freshness";
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
  const base = Math.abs(netLiq) || 1;
  // signed share of |net liq| — same reading as the Portfolio tab's table.
  const pct = (v: number): string => {
    const p = Math.round((v / base) * 100);
    return (p >= 0 ? "+" : "−") + Math.abs(p) + "%";
  };
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
            <span className={"mono " + pnlCls(vals[p.key]!)}>
              {fmt.usd(vals[p.key]!)} <span className="dim">({pct(vals[p.key]!)})</span>
            </span>
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
    g = portfolio.data?.greeks ?? DATA.greeks,
    ps = portfolio.data?.perfStats ?? DATA2.perfStats;
  const events = trade.data?.events ?? DATA.events;
  const ts = termStructure.data ?? DATA.termStructure;
  const spot = ticks.data?.mid ?? DATA.SPOT;

  // regime gate: live decision (/regime/state → gate.authorized), mock fallback.
  const regimeLive = useFetch(() => fetchRegimeState(), 60_000);
  const gate = regimeLive.data?.gate;
  const gateOpen = gate?.authorized ?? DATA.regime.gate.allowed;

  // surface staleness — degrades the Signal card when the vol surface is down.
  const toTone = (s: FreshStatus): Status => (s === "live" ? "up" : s === "stale" ? "warn" : "down");
  const surfStale = toTone(termStructure.status) === "down";

  // ── Portfolio card: live per-currency cash for the holdings donut.
  const liveCash = trade.data?.cash;
  const cashRows = liveCash && liveCash.length > 0 ? liveCash : DATA.cash;

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
            </div>
            <div className="ind-fam">
              <div className="ind-fam-head">P&amp;L &amp; holdings</div>
              <div className="dash-pnl-pair">
                <div className="bs-item">
                  <span className="gs-lbl">Realized</span>
                  <b className={"mono " + pnlCls(ps.cumRealized)}>{fmt.sgn(ps.cumRealized, 1)}k</b>
                </div>
                <div className="bs-item">
                  <span className="gs-lbl">Unrealized</span>
                  <b className={"mono " + pnlCls(g.netUnreal)}>{fmt.usdk(g.netUnreal)}</b>
                </div>
              </div>
              <HoldingsDonut netLiq={a.netLiq} cash={cashRows} />
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
