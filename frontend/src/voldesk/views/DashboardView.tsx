/**
 * VOLDESK — Dashboard (command center). Ported from the prototype's
 * `js/views_misc.jsx` (DashboardView + MiniTerm). Mock data for now; wires to
 * the backend in a later lot.
 */
import { fetchOrders, fetchPnlAttribution, fetchRegimeState } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { Bar, Panel, Tag } from "../components/common";
import { gk$, pnlCls, type Tone } from "../components/format";
import type { Status } from "../components/format";
import { DATA, DATA2, fmt } from "../data";
import type { Pc, TermPoint, WorkingOrder } from "../data";
import { useDeskData } from "../data/deskData";
import type { FreshStatus } from "../data/freshness";
import { adaptCoverage } from "../data/live/portfolio";

/** /api/v1/orders (live IB openTrades via execution-engine) → WorkingOrder[].
 *  Empty when execution-engine is down or there are no resident orders. */
function adaptWorkingOrders(raw: unknown): WorkingOrder[] {
  const rows = (raw as { orders?: Array<Record<string, unknown>> } | null)?.orders ?? [];
  return rows.map((o) => ({
    id: String(o["order_id"] ?? o["perm_id"] ?? ""),
    side: String(o["side"] ?? ""),
    product: String(o["local_symbol"] ?? o["symbol"] ?? "—"),
    qty: Number(o["qty"] ?? 0),
    level: o["limit_price"] != null ? `@ ${String(o["limit_price"])} limit` : String(o["status"] ?? ""),
  }));
}

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

export function DashboardView({ go }: { go: (r: string) => void }): JSX.Element | null {
  // Composition view: read the already-wired desk domains, fall back to mock.
  const { pca, portfolio, trade, termStructure, ticks } = useDeskData();
  const a = portfolio.data?.account ?? DATA.account,
    g = portfolio.data?.greeks ?? DATA.greeks,
    L = trade.data?.limits ?? DATA.limits,
    f = DATA.feed; // freshness thresholds mock; ages/tones derived from domains below
  // regime gate: live decision (/regime/state → gate.authorized), mock fallback.
  const regimeLive = useFetch(() => fetchRegimeState(), 60_000);
  const gateOpen = regimeLive.data?.gate?.authorized ?? DATA.regime.gate.allowed;
  // working orders: live resident IB orders (empty when none / execution down).
  const workingOrders = useFetch(() => fetchOrders().then(adaptWorkingOrders), 30_000).data ?? [];
  const live = ticks.data?.mid ?? DATA.SPOT; // live spot (RT.1) ; move/RV restent mock
  // coverage: ratio/convexity/carry live (from /pnl-attribution); perf trio deferred (mock).
  const covLive = useFetch(() => fetchPnlAttribution().then(adaptCoverage), 60_000).data;
  const cov = { ...DATA2.coverage, ...(covLive ?? {}) };
  const ts = termStructure.data ?? DATA.termStructure;
  const events = trade.data?.events ?? DATA.events;
  const totalNominal = portfolio.data?.bookComposition.totalNominal ?? DATA2.bookComposition.totalNominal;
  // signal: conviction-ranked by VARIANCE share (PC1 > PC2 > PC3)
  const ranked = [...(pca.data?.pcs ?? DATA.pcs)].sort((x, y) => (y.variance || 0) - (x.variance || 0));
  const lead = ranked[0];
  const ev0 = events[0];
  if (!lead || !ev0) return null;
  const convW = (pc: Pc): number => Math.sqrt(pc.variance || 0);
  const maxW = Math.max(...ranked.map(convW)) || 1;
  const leadTone: Tone =
    lead.label === "CHEAP" ? "good" : lead.label === "RICH" || lead.label === "EXPENSIVE" ? "danger" : "neutral";
  // leverage — € notional ÷ € net liq
  const netLiqEur = a.netLiq / DATA.SPOT;
  const grossX = (totalNominal / (netLiqEur / 1e6)).toFixed(2);
  const netX = (18.2 / (netLiqEur / 1e6)).toFixed(2);

  // freshness — derived from the live domains (honest staleness), not mock timers.
  const toTone = (s: FreshStatus): Status => (s === "live" ? "up" : s === "stale" ? "warn" : "down");
  const ageS = (ms: number | null, fallback: number): number => (ms != null ? Math.round(ms / 1000) : fallback);
  const feedTone = toTone(trade.status);
  const surfTone = toTone(termStructure.status);
  const feedS = ageS(trade.ageMs, f.feedS);
  const surfaceS = ageS(termStructure.ageMs, f.surfaceS);
  const surfStale = surfTone === "down";
  const worst: Status = [feedTone, surfTone].includes("down") ? "down" : [feedTone, surfTone].includes("warn") ? "warn" : "up";

  // skew incident
  const varTot = DATA2.varFactors.reduce((s, x) => s + Math.abs(x.v), 0);
  const skewF = DATA2.varFactors.find((x) => x.key === "skew");
  const skewPct = skewF ? (Math.abs(skewF.v) / varTot) * 100 : 0;
  const skewWatch = skewPct > L.skewVarPct;

  // EXCEPTIONS — risk only
  const band = L.deltaBandUsd,
    resid = g.netDelta,
    overPct = Math.round((Math.abs(resid) / band - 1) * 100);
  const bandTxt = "$" + (band / 1000).toFixed(1) + "k";
  const breach = {
    cat: "Hedge",
    msg: "Δ residual " + gk$(resid) + " vs ±" + bandTxt + " band",
    detail: "+" + overPct + "% beyond band · last hedge 11:48:02",
    action: "hedge in Trade",
    tab: "trade",
  };
  const watches = [
    { cat: "Limit · Γ", msg: "Gamma 71% of cap", detail: "14.5k / 20.4k · approaching", action: "Risk", tab: "risk" },
    ...(skewWatch
      ? [
          {
            cat: "Skew incident",
            msg: "Skew " + skewPct.toFixed(0) + "% of VaR · unintended",
            detail: "net vanna " + fmt.sgn(g.netVanna, 0) + "k · risk-only, not traded",
            action: "Risk",
            tab: "risk",
          },
        ]
      : []),
    { cat: "Stability", msg: "Eigengap λ2−λ3 narrow", detail: "PC2/PC3 may rotate · 1.50×", action: "Signal", tab: "signals" },
  ];
  const cleared = ["VaR within 99 limit · 3 / 2.5 exp", "Cushion 69.6% · margin 43.6%", "Coverage 1.20× · convexity pays"];

  return (
    <div className="dash-grid">
      {/* freshness */}
      <div className={"dash-fresh tone-" + worst}>
        <span className={"df-dot " + worst} />
        <b className="df-status">{worst === "down" ? "FEED STALE" : worst === "warn" ? "feed delayed" : "live"}</b>
        <span className="df-src mono">
          feed <em className={feedTone}>{feedS}s</em>
        </span>
        <span className="df-src mono">
          surface <em className={surfTone}>{surfaceS}s</em>
        </span>
        {surfStale && <span className="df-warn mono">surface &gt; {f.surfStale}s — greyed tiles are unreliable</span>}
        <span className="df-asof dim mono">as of {new Date().toLocaleTimeString("en-GB")} · UTC+1</span>
      </div>

      {/* LAYER 1 — exceptions */}
      <Panel
        title="Attention"
        dataPp="dash-attention"
        right={
          <span className="dim mono small">
            1 breach · {watches.length} watch · {cleared.length} clear
          </span>
        }
        className="dash-alerts-panel"
      >
        <div className="exc-layout">
          <button className="exc-hero" onClick={() => go(breach.tab)}>
            <div className="exc-hero-l">
              <span className="exc-flag mono">● BREACH</span>
              <span className="exc-cat mono">{breach.cat}</span>
            </div>
            <div className="exc-hero-msg">{breach.msg}</div>
            <div className="exc-hero-detail mono dim">{breach.detail}</div>
            <span className="exc-hero-act mono">{breach.action} →</span>
          </button>
          <div className="exc-side">
            <div className="exc-watch-row">
              {watches.map((w, i) => (
                <button key={i} className="exc-watch" onClick={() => go(w.tab)}>
                  <span className="exc-w-cat mono">▲ {w.cat}</span>
                  <span className="exc-w-msg">{w.msg}</span>
                  <span className="exc-w-detail mono dim">{w.detail}</span>
                  <span className="exc-w-act mono">{w.action} →</span>
                </button>
              ))}
            </div>
            <div className="exc-clear">
              <span className="exc-clear-tag mono">✓ within limits</span>
              {cleared.map((c, i) => (
                <span key={i} className="exc-clear-item mono dim">
                  {c}
                </span>
              ))}
            </div>
          </div>
        </div>
      </Panel>

      {/* LAYER 2 — routing state */}
      <div className="dash-r2">
        <Panel
          title="Market snapshot"
          dataPp="dash-market"
          right={
            <button className="link-btn" onClick={() => go("signals")}>
              Signal →
            </button>
          }
          className={"dash-mkt" + (surfStale ? " df-degraded" : "")}
        >
          <div className="mkt-top">
            <div className="mkt-spot">
              <span className="gs-lbl">EURUSD spot</span>
              <b className="mono">{live.toFixed(5)}</b>
              <span className="dim mono small">
                {(ticks.data?.bid ?? live - 0.00008).toFixed(5)} / {(ticks.data?.ask ?? live + 0.00008).toFixed(5)}
              </span>
            </div>
            <div className="mkt-stat">
              <span className="gs-lbl">move 24h</span>
              <b className="mono pos">+0.31%</b>
            </div>
            <div className="mkt-stat">
              <span className="gs-lbl">RV 1M</span>
              <b className="mono">4.4</b>
            </div>
            <div className="mkt-stat">
              <span className="gs-lbl">session</span>
              <b className="mono">London</b>
            </div>
          </div>
          <div className="mkt-term">
            <div className="mkt-term-head">
              <span className="gs-lbl">ATM term structure</span>
              <span className="mkt-term-leg mono dim">
                <i className="lg-atm" />
                IV <i className="lg-fair" />
                σ_fair
              </span>
            </div>
            <MiniTerm ts={ts} />
          </div>
        </Panel>
        <Panel
          title="Active signal"
          dataPp="dash-signal"
          right={
            <button className="link-btn" onClick={() => go("signals")}>
              Signal →
            </button>
          }
          className={"dash-signal" + (surfStale ? " df-degraded" : "")}
        >
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
            conviction-ranked · {lead.id} dominant ({lead.variance}% var) · gate {gateOpen ? "open" : "blocked"}{" "}
            <span className="dim">(PC1 only · info)</span>
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
        </Panel>
      </div>

      <div className="dash-r2 dash-r2b">
        <Panel
          title="Book health"
          dataPp="dash-book-health"
          right={
            <button className="link-btn" onClick={() => go("risk")}>
              Risk →
            </button>
          }
          className="dash-book"
        >
          <div className="greeks-summary gs-g5">
            <div className="gs-item">
              <span className="gs-lbl">
                Net Δ <em className="unit">$</em>
              </span>
              <b className={"mono " + pnlCls(g.netDelta)}>{gk$(g.netDelta)}</b>
            </div>
            <div className="gs-item">
              <span className="gs-lbl">
                Net Γ <em className="unit">$/pip</em>
              </span>
              <b className={"mono " + pnlCls(g.netGamma)}>{gk$(g.netGamma)}</b>
            </div>
            <div className="gs-item">
              <span className="gs-lbl">
                Net Vega <em className="unit">$/vp</em>
              </span>
              <b className={"mono " + pnlCls(g.netVega)}>{gk$(g.netVega)}</b>
            </div>
            <div className="gs-item">
              <span className="gs-lbl">
                Net Vanna <em className="unit">$k</em>
              </span>
              <b className={"mono " + pnlCls(g.netVanna)}>{fmt.sgn(g.netVanna, 0)}k</b>
            </div>
            <div className="gs-item">
              <span className="gs-lbl">
                Net Θ <em className="unit">$/day</em>
              </span>
              <b className={"mono " + pnlCls(g.netTheta)}>{gk$(g.netTheta)}</b>
            </div>
          </div>
          <div className="book-surv">
            <div className="bs-item">
              <span className="gs-lbl">Coverage</span>
              <b className={"mono " + (cov.ratio >= 1 ? "pos" : "neg")}>{cov.ratio.toFixed(2)}×</b>
              <span className="gs-sub dim">survival · {cov.windowLabel}</span>
            </div>
            <div className="bs-item">
              <span className="gs-lbl">Day P&L</span>
              <b className={"mono " + pnlCls(a.dayPnl)}>{fmt.usdk(a.dayPnl)}</b>
              <span className="gs-sub dim">session · {fmt.pct(a.dayPnlPct)}</span>
            </div>
            <div className="bs-item">
              <span className="gs-lbl">Unrealized</span>
              <b className={"mono " + pnlCls(g.netUnreal)}>{fmt.usdk(g.netUnreal)}</b>
              <span className="gs-sub dim">open MTM</span>
            </div>
            <div className="bs-item">
              <span className="gs-lbl">Γ util.</span>
              <b className="mono warn">71%</b>
              <span className="gs-sub dim">of cap</span>
            </div>
          </div>
        </Panel>
        <Panel
          title="Capital"
          dataPp="dash-capital"
          right={
            <button className="link-btn" onClick={() => go("portfolio")}>
              Portfolio →
            </button>
          }
          className="dash-capital"
        >
          <div className="cap-row">
            <span className="gs-lbl">Net liq</span>
            <b className="mono">{fmt.usd(a.netLiq)}</b>
          </div>
          <div className="cap-row">
            <span className="gs-lbl">Cushion</span>
            <b className="mono pos">{(a.cushion * 100).toFixed(1)}%</b>
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
              gross {grossX}× · net {netX}× net liq <em className="unit">€{(netLiqEur / 1e6).toFixed(2)}M</em>
            </span>
          </div>
        </Panel>
      </div>

      {/* LAYER 3 — temporal */}
      <Panel title="Today — events, expiries & working orders" dataPp="dash-today" className="dash-today">
        <div className="today-grid t3">
          <div className="today-col">
            <span className="gs-lbl">Today's macro events</span>
            <div className="today-evt">
              <span className="mono accent">{ev0.in}</span>
              <span className="evt-code mono">{ev0.code}</span>
              <span className="dim">
                {ev0.content} · {ev0.country}
              </span>
              <Tag tone="danger">{ev0.impact}</Tag>
            </div>
          </div>
          <div className="today-col">
            <span className="gs-lbl">Near expiries & roll-off</span>
            <div className="today-evt">
              <span className="mono warn">29 DTE</span>
              <span className="evt-code mono">Straddle 1M</span>
              <span className="dim">@1.0850 · pin 8 pip</span>
              <button className="link-btn" onClick={() => go("risk")}>
                Risk →
              </button>
            </div>
          </div>
          <div className="today-col">
            <span className="gs-lbl">
              Working orders <span className="dim">· {workingOrders.length} resident</span>
            </span>
            {workingOrders.length ? (
              workingOrders.map((o) => (
                <button key={o.id} className="today-evt wo-row" onClick={() => go("trade")}>
                  <span className={"side-pill " + (o.side === "BUY" ? "long" : "short")}>{o.side}</span>
                  <span className="evt-code mono">{o.product}</span>
                  <span className="dim mono">
                    {o.qty}× · {o.level}
                  </span>
                  <span className="wo-go mono">Trade →</span>
                </button>
              ))
            ) : (
              <div className="today-evt dim">no working orders</div>
            )}
          </div>
        </div>
      </Panel>
    </div>
  );
}
