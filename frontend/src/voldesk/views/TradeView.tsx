/**
 * VOLDESK — Trade page. Ported from the prototype's `js/views_trade.jsx`.
 * Inline sub-components (IndicatorsPanel, HedgeStrip, HoldingsStrip, ClosePanel,
 * BudgetBar) stay local. The prototype's MarketDataBlock was exported but never
 * rendered by TradeView — dropped. Order entry is the WRITE path: it stays mock
 * until the auth boundary + backend wiring lands (IMPLEMENTATION.md §3bis/§5).
 */
import { useEffect, useState } from "react";
import { Panel } from "../components/common";
import { gk$, pnlCls } from "../components/format";
import { Donut } from "../components/charts";
import { FreshBadge } from "../components/FreshBadge";
import { OpenPositionsTable } from "../components/PositionsTable";
import { OrderBuilder, type BuilderState } from "../components/OrderBuilder";
import { DATA, fmt } from "../data";
import type { AccountState, Cash, Greeks, Limits, MacroEvent, Position } from "../data";
import { useDeskData, useTicks } from "../data/deskData";
import { WRITE_ENABLED } from "../data/writeEnabled";
import { ApiError } from "../../api/client";
import { closeContract, closeTrade } from "../../api/endpoints";

const GATE_TITLE = "write disabled — auth required (Phase 2)";

function closeErr(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = (e.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    return `broker/API error ${e.status}`;
  }
  return e instanceof Error ? e.message : "unknown error";
}

interface TradeTweaks {
  density: string;
  showGreeks: boolean;
}

// ---------------- ClosePanel ----------------
function ClosePanel({
  pos,
  onDone,
  positions,
  greeks,
  account,
}: {
  pos: Position | null;
  onDone: () => void;
  positions: Position[];
  greeks: Greeks;
  account: AccountState;
}): JSX.Element {
  const [type, setType] = useState<"contract" | "trade">("contract");
  const [contractId, setContractId] = useState("");
  const [tradeId, setTradeId] = useState("");
  const [qty, setQty] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (pos) {
      setType("contract");
      setContractId(pos.id);
      setQty(pos.qty);
    }
  }, [pos]);

  const packages = [...new Set(positions.map((p) => p.packageId))].map((id) => ({
    id,
    struct: positions.find((p) => p.packageId === id)?.structure ?? "",
  }));
  const g = greeks;
  const before: Record<string, number> = {
    pnl24: account.dayPnl,
    unrl: g.netUnreal,
    delta: g.netDelta,
    gamma: g.netGamma,
    vega: g.netVega,
    vanna: g.netVanna,
    theta: g.netTheta,
    var99: g.var1d99 * 1000,
  };
  let sel: Position | { trade: true } | null = null;
  const c = { pnl: 0, d: 0, g: 0, v: 0, vn: 0, t: 0, frac: 0 };
  if (type === "contract" && contractId) {
    const p = positions.find((x) => x.id === contractId);
    if (p) {
      const f = Math.min(1, (qty || 0) / p.qty);
      sel = p;
      c.pnl = p.pnl * f;
      c.d = p.delta * f;
      c.g = p.gamma * f;
      c.v = p.vega * f;
      c.vn = p.vanna * f;
      c.t = p.theta * f;
      c.frac = f;
    }
  } else if (type === "trade" && tradeId) {
    const legs = positions.filter((x) => x.packageId === tradeId);
    if (legs.length) {
      sel = { trade: true };
      legs.forEach((p) => {
        c.pnl += p.pnl;
        c.d += p.delta;
        c.g += p.gamma;
        c.v += p.vega;
        c.vn += p.vanna;
        c.t += p.theta;
      });
      c.frac = 1;
    }
  }
  const after: Record<string, number> | null = sel
    ? {
        pnl24: before.pnl24!,
        unrl: before.unrl! - c.pnl,
        delta: before.delta! - c.d,
        gamma: before.gamma! - c.g,
        vega: before.vega! - c.v,
        vanna: Math.round(before.vanna! - c.vn),
        theta: before.theta! - c.t,
        var99: Math.round(before.var99! * (1 - 0.14 * c.frac)),
      }
    : null;

  // Real close → IB paper account. Contract = one leg (OpenPosition.id, partial
  // via qty); trade = every leg sharing the trade_id. Parent refreshes on poll.
  const onExec = async (): Promise<void> => {
    if (!sel || !WRITE_ENABLED || busy) return;
    setBusy(true);
    setErr(null);
    try {
      if (type === "contract") {
        await closeContract(Number(contractId), qty);
      } else {
        const leg = positions.find((p) => p.packageId === tradeId);
        if (!leg?.tradeId) throw new Error("no backend trade id for this package");
        await closeTrade(Number(leg.tradeId));
      }
      onDone();
    } catch (e) {
      setErr(closeErr(e));
    } finally {
      setBusy(false);
    }
  };

  const m = (v: number): string => (v < 0 ? "-" : "") + "$" + Math.abs(Math.round(v)).toLocaleString("en-US");
  const rows: [string, string, string][] = [
    ["Total P&L (24h)", "pnl24", ""],
    ["Open unrealized", "unrl", ""],
    ["Δ net", "delta", "$"],
    ["Γ net", "gamma", "$/pip"],
    ["Vega net", "vega", "$/vp"],
    ["Vanna net", "vanna", "$k"],
    ["Θ net", "theta", "$/day"],
  ];
  const fmtCell = (key: string, v: number): string => (key === "vanna" ? fmt.sgn(v, 0) + "k" : m(v));

  return (
    <div className="close-draft">
      <div className="close-fields">
        <label className="field">
          <span>Type</span>
          <select value={type} onChange={(e) => setType(e.target.value as "contract" | "trade")}>
            <option value="contract">Contract (1 leg)</option>
            <option value="trade">Trade (all legs)</option>
          </select>
        </label>
        <label className="field">
          <span>Contract number</span>
          <select
            value={contractId}
            disabled={type === "trade"}
            onChange={(e) => {
              setContractId(e.target.value);
              const p = positions.find((x) => x.id === e.target.value);
              if (p) setQty(p.qty);
            }}
          >
            <option value="">— pick a contract —</option>
            {positions.map((p) => (
              <option key={p.id} value={p.id}>
                {p.conId} · {p.structure}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Trade number</span>
          <select value={tradeId} disabled={type === "contract"} onChange={(e) => setTradeId(e.target.value)}>
            <option value="">— pick a trade —</option>
            {packages.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id} · {p.struct}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>
            Qty to close <em className="unit">contracts</em>
          </span>
          <div className="field-input">
            <input type="number" value={qty} disabled={type === "trade"} onChange={(e) => setQty(+e.target.value)} />
            <em>ct</em>
          </div>
        </label>
      </div>
      <table className="dt close-risk-tbl">
        <thead>
          <tr>
            <th className="l">Net book · before → after</th>
            <th className="r">Before</th>
            <th className="r after-col">After</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, key, unit]) => (
            <tr key={key}>
              <td className="l">
                {label}
                {unit ? <em className="unit mono"> {unit}</em> : null}
              </td>
              <td className={"r mono " + pnlCls(before[key]!)}>{fmtCell(key, before[key]!)}</td>
              <td className={"r mono " + (after ? pnlCls(after[key]!) : "dim")}>{after ? fmtCell(key, after[key]!) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {err && <div className="ob-error mono small">⚠ {err}</div>}
      <button
        className="btn-close-exec"
        disabled={!sel || !WRITE_ENABLED || busy}
        title={WRITE_ENABLED ? "submit close to IB paper account" : GATE_TITLE}
        onClick={onExec}
      >
        {busy ? "Closing…" : sel ? (type === "trade" ? "Close trade" : `Close ${qty} ct`) : "Close"}
      </button>
      {!WRITE_ENABLED && <div className="dim small ob-readonly-note">Read-only desk · closing requires auth (Phase 2).</div>}
    </div>
  );
}

// ---------------- HoldingsStrip ----------------
function HoldingsStrip({ cash, spotBid, spotAsk }: { cash: Cash[]; spotBid: number; spotAsk: number }): JSX.Element {
  const eur = cash.find((c) => c.ccy === "EUR");
  const usd = cash.find((c) => c.ccy === "USD");
  const eurUsd = eur ? eur.usd : 0;
  const usdUsd = usd ? usd.usd : 0;
  const total = eurUsd + usdUsd;
  const k = (v: number): string => (v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "M" : "$" + (v / 1e3).toFixed(0) + "k");
  const pct = (v: number): string => ((v / total) * 100).toFixed(1) + "%";
  const bid = spotBid.toFixed(5);
  const ask = spotAsk.toFixed(5);
  return (
    <div className="hold-strip">
      <div className="hold-legend">
        <span className="hold-lbl">Cash holdings</span>
        <div className="hl-row">
          <i style={{ background: "var(--pos)" }} />
          <span className="hl-ccy">EUR</span>
          <b className="hl-pct mono">{pct(eurUsd)}</b>
          <span className="hl-val mono">{fmt.num(eur ? eur.settled + eur.unsettled : 0, 0)}</span>
        </div>
        <div className="hl-row">
          <i style={{ background: "var(--accent)" }} />
          <span className="hl-ccy">USD</span>
          <b className="hl-pct mono">{pct(usdUsd)}</b>
          <span className="hl-val mono">{fmt.num(usd ? usd.settled + usd.unsettled : 0, 0)}</span>
        </div>
        <div className="hl-fx">
          <span className="hold-lbl">EUR/USD</span>
          <span className="fx-pair">
            <b className="mono">{bid}</b>
            <span className="dim">/</span>
            <b className="mono">{ask}</b>
          </span>
          <span className="dim small">bid / ask</span>
        </div>
      </div>
      <div className="hold-donut">
        <Donut
          segments={[
            { label: "EUR", value: eurUsd, color: "var(--pos)" },
            { label: "USD", value: usdUsd, color: "var(--accent)" },
          ]}
          center={k(total)}
        />
      </div>
    </div>
  );
}

// ---------------- Indicators ----------------
function parseEvt(d: string): Date | null {
  // Live events carry an ISO date; the mock carries a locale string.
  const iso = Date.parse(d);
  if (!Number.isNaN(iso)) return new Date(iso);
  const m = d.match(/(\d+)\/(\d+)\/(\d+),\s*(\d+):(\d+)/);
  return m ? new Date(+m[3]!, +m[2]! - 1, +m[1]!, +m[4]!, +m[5]!) : null;
}
function nextHighImpact(events: MacroEvent[]): { e: MacroEvent; dt: Date } | null {
  const now = new Date();
  const cand = events
    .filter((e) => e.impact === "high")
    .map((e) => ({ e, dt: parseEvt(e.date) }))
    .filter((x): x is { e: MacroEvent; dt: Date } => x.dt != null && x.dt > now)
    .sort((a, b) => a.dt.getTime() - b.dt.getTime());
  return cand[0] ?? null;
}
function inWords(dt: Date): string {
  const ms = dt.getTime() - Date.now();
  const h = ms / 3.6e6;
  if (h < 24) return Math.round(h) + "h";
  const d = Math.floor(h / 24);
  return d + "d " + Math.round(h - d * 24) + "h";
}

interface BudgetBarProps {
  label: string;
  used: number;
  cap: number;
  unit: string;
  fmtv: (v: number) => string;
  add?: number;
}
function BudgetBar({ label, used, cap, unit, fmtv, add = 0 }: BudgetBarProps): JSX.Element {
  const after = used + add;
  const usedPct = Math.max(0, Math.min(100, (used / cap) * 100));
  const afterPct = Math.max(0, Math.min(100, (after / cap) * 100));
  const tone = afterPct > 90 ? "var(--neg)" : afterPct > 75 ? "var(--warn)" : "var(--pos)";
  const adding = Math.abs(add) > 1e-6;
  return (
    <div className="bud-row">
      <div className="bud-head">
        <span className="bud-lbl">
          {label} <em className="unit mono">{unit}</em>
        </span>
        <span className="bud-val mono">
          {fmtv(used)}
          {adding ? <span className="bud-after"> → {fmtv(after)}</span> : null}
          <span className="dim"> / {fmtv(cap)}</span>
        </span>
      </div>
      <div className="bud-track">
        <div className="bud-fill" style={{ width: Math.min(usedPct, afterPct) + "%", background: tone }} />
        {adding && (
          <div
            className="bud-add"
            style={{ left: Math.min(usedPct, afterPct) + "%", width: Math.abs(afterPct - usedPct) + "%", background: tone }}
          />
        )}
        <div className="bud-cap-mark" />
      </div>
    </div>
  );
}

// FX session windows (UTC). Bubble: green = open, orange = pre/post (±1h edge),
// red = closed. Derived from the wall clock — no backend feed needed.
const SESSIONS: { code: string; open: number; close: number }[] = [
  { code: "London", open: 7, close: 16 },
  { code: "New York", open: 12, close: 21 },
  { code: "Hong Kong", open: 1, close: 9 },
];
function sessionState(hUtc: number, open: number, close: number): "open" | "edge" | "closed" {
  if (hUtc >= open && hUtc < close) return "open";
  if ((hUtc >= open - 1 && hUtc < open) || (hUtc >= close && hUtc < close + 1)) return "edge";
  return "closed";
}
function MarketSessions(): JSX.Element {
  const now = new Date();
  const h = now.getUTCHours() + now.getUTCMinutes() / 60;
  const tone: Record<string, string> = { open: "var(--pos)", edge: "var(--warn)", closed: "var(--neg)" };
  const word: Record<string, string> = { open: "open", edge: "pre/post", closed: "closed" };
  return (
    <div className="mkt-sessions">
      {SESSIONS.map((s) => {
        const st = sessionState(h, s.open, s.close);
        return (
          <div key={s.code} className="mkt-sess" title={`${s.code} · ${word[st]}`}>
            <span className="sess-dot" style={{ background: tone[st] }} />
            <span className="sess-code">{s.code}</span>
            <span className="sess-state mono dim">{word[st]}</span>
          </div>
        );
      })}
    </div>
  );
}

function IndicatorsPanel({
  builder,
  greeks,
  account,
  limits,
  events,
  cash,
  positions,
}: {
  builder: BuilderState | null;
  greeks: Greeks;
  account: AccountState;
  limits: Limits;
  events: MacroEvent[];
  cash: Cash[];
  positions: Position[];
}): JSX.Element {
  const g = greeks,
    a = account,
    L = limits;
  const eur = cash.find((c) => c.ccy === "EUR"),
    usd = cash.find((c) => c.ccy === "USD");
  const evt = nextHighImpact(events);
  const isActive = !!(builder && builder.active && !builder.isFut);
  const add = isActive && builder ? builder.net : null;
  const tradedTenor = builder ? builder.tenor : null;
  const tenIx = tradedTenor ? Math.max(0, DATA.tenors.indexOf(tradedTenor)) : 1;
  const tradedDte = tradedTenor ? 21 + tenIx * 28 : null;
  const evtInWindow = !!(isActive && evt && tradedDte != null && (evt.dt.getTime() - Date.now()) / 8.64e7 <= tradedDte);

  const kM = (v: number): string => (v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "M" : "$" + Math.round(v / 1e3) + "k");
  const usedMarginPct = a.marginInitPct;
  const pct = (added: number, used: number, cap: number): number => {
    const rem = cap - used;
    return rem > 0 ? Math.max(0, (added / rem) * 100) : 100;
  };

  return (
    <div className="ind-grid">
      {/* 1 — market microstructure */}
      <div className="ind-fam">
        <div className="ind-fam-head">Market microstructure</div>
        <div className="ind-rows">
          <div className="ind-row">
            <span>Fwd {tradedTenor || "2M"}</span>
            <b className="mono">{DATA.smileFor(tenIx).fwd.toFixed(4)}</b>
          </div>
        </div>
        <MarketSessions />
      </div>

      {/* 2 — book net (= Risk) : Δ-band monitor + net greeks + book totals.
          Moved out of the Open-positions panel so "open positions" (the legs)
          and "book net" (the aggregate) read as distinct blocks. */}
      <div className="ind-fam">
        <div className="ind-fam-head">
          Book net <span className="dim">· {positions.length} legs · one engine · = Risk</span>
        </div>
        <HedgeStrip greeks={greeks} limits={limits} />
        <div className="ind-greeks">
          <div className="indg">
            <span className="indg-l">Δ net <em className="unit">$</em></span>
            <b className={"mono " + pnlCls(g.netDelta)}>{gk$(g.netDelta)}</b>
          </div>
          <div className="indg">
            <span className="indg-l">Γ net <em className="unit">$/pip</em></span>
            <b className={"mono " + pnlCls(g.netGamma)}>{gk$(g.netGamma)}</b>
          </div>
          <div className="indg">
            <span className="indg-l">Vega net <em className="unit">$/vp</em></span>
            <b className={"mono " + pnlCls(g.netVega)}>{gk$(g.netVega)}</b>
          </div>
          <div className="indg">
            <span className="indg-l">Vanna net <em className="unit">$k/vp·fig</em></span>
            <b className={"mono " + pnlCls(g.netVanna)}>{fmt.sgn(g.netVanna, 0)}k</b>
          </div>
          <div className="indg">
            <span className="indg-l">Volga net <em className="unit">$k/vp</em></span>
            <b className={"mono " + pnlCls(g.netVolga)}>{fmt.sgn(g.netVolga, 0)}k</b>
          </div>
          <div className="indg">
            <span className="indg-l">Θ net <em className="unit">$/day</em></span>
            <b className={"mono " + pnlCls(g.netTheta)}>{gk$(g.netTheta)}</b>
          </div>
        </div>
        <div className="ind-rows">
          <div className="ind-row">
            <span>Nominal <em className="unit">€</em></span>
            <b className="mono">{(g.netNominal / 1e6).toFixed(1)}M</b>
          </div>
          <div className="ind-row">
            <span>Unrealized P&L</span>
            <b className={"mono " + pnlCls(g.netUnreal)}>{fmt.usdk(g.netUnreal)}</b>
          </div>
        </div>
      </div>

      {/* 3 — capacity & budget */}
      <div className="ind-fam">
        <div className="ind-fam-head">Capacity & budget</div>
        <div className="ind-rows tight">
          <div className="ind-row">
            <span>Margin used</span>
            <b className="mono">
              <span className={"state-chip " + (usedMarginPct > 75 ? "hot" : usedMarginPct > 55 ? "warm" : "cool")}>
                {usedMarginPct.toFixed(1)}%
              </span>{" "}
              <span className="dim">excess {kM(a.excessLiq)}</span>
            </b>
          </div>
          <div className="ind-row">
            <span>Cash EUR / USD</span>
            <b className="mono">
              {kM(eur?.usd ?? 0)} / {kM(usd?.usd ?? 0)}
            </b>
          </div>
        </div>
        <div className="bud-bars">
          <BudgetBar label="Γ budget" used={g.netGamma} cap={L.gamma.cap} unit={L.gamma.unit} fmtv={gk$} add={add ? add.g : 0} />
          <BudgetBar label="Vega budget" used={g.netVega} cap={L.vega.cap} unit={L.vega.unit} fmtv={gk$} add={add ? add.v : 0} />
          <BudgetBar
            label="Vanna budget"
            used={g.netVanna}
            cap={L.vanna.cap}
            unit={L.vanna.unit}
            fmtv={(v) => fmt.sgn(v, 0) + "k"}
            add={add ? add.vn : 0}
          />
        </div>
        <div className={"ind-row evt " + (evtInWindow ? "spans" : "")}>
          <span>Next high-impact</span>
          <b className="mono">
            {evt ? (
              <>
                <span className="event-code">{evt.e.code}</span> in {inWords(evt.dt)}
                {evtInWindow ? <span className="warn"> · in {tradedTenor} window</span> : null}
              </>
            ) : (
              "none scheduled"
            )}
          </b>
        </div>

        {/* pre-trade check */}
        {isActive && builder && add && (
          <div className="pretrade">
            <div className="pretrade-head mono">
              Pre-trade · {builder.side} {builder.qty}× {builder.product} {builder.tenor}{" "}
              <span className="dim">vs budget</span>
            </div>
            <div className="pretrade-lines">
              <div>
                <span>Vega</span>
                <b className="mono">
                  {gk$(add.v)} · {pct(add.v, g.netVega, L.vega.cap).toFixed(0)}% of headroom
                </b>
              </div>
              <div>
                <span>Vanna</span>
                <b className={"mono " + (g.netVanna + add.vn > L.vanna.cap ? "neg" : "")}>
                  {fmt.sgn(add.vn, 0)}k · {pct(add.vn, g.netVanna, L.vanna.cap).toFixed(0)}% of headroom
                </b>
              </div>
              <div>
                <span>Γ</span>
                <b className="mono">
                  {gk$(add.g)} · {pct(add.g, g.netGamma, L.gamma.cap).toFixed(0)}% of headroom
                </b>
              </div>
              {builder.naked && (
                <div className="pt-flag">
                  <span className="flag-dot" />
                  sold leg · unbounded tail — read stress in preview
                </div>
              )}
              {evtInWindow && evt && (
                <div className="pt-flag">
                  <span className="flag-dot" />
                  {builder.tenor} tenor spans {evt.e.code} ({inWords(evt.dt)})
                </div>
              )}
            </div>
            <div className="pretrade-note dim small">
              state only · mirrors the preview Before/After · the tool does not say trade / don't trade
            </div>
          </div>
        )}
        {!isActive && (
          <div className="ind-rest dim small">
            Headroom standing. Build a structure → this shows its draw on the budget (same engine as the preview).
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------- HedgeStrip ----------------
function HedgeStrip({ greeks, limits }: { greeks: Greeks; limits: Limits }): JSX.Element {
  // Read-only Δ-band monitor. One-click "hedge to flat" execution belongs to the
  // auto-hedge phase (R12+, docs/strategy.md §1) — no backend endpoint yet, so we
  // surface the residual honestly rather than fake a re-center.
  const resid = greeks.netDelta;
  const band = limits.deltaBandUsd;
  const drift = Math.abs(resid) > band;
  const over = Math.round((Math.abs(resid) / band - 1) * 100);
  const bandTxt = "$" + (band / 1000).toFixed(1) + "k";
  return (
    <div className={"hedge-strip " + (drift ? "drift" : "ok")}>
      <div className="hs-item">
        <span className="gs-lbl">
          Δ residual <em className="unit">$</em>
        </span>
        <b className={"mono " + pnlCls(resid)}>{gk$(resid)}</b>
      </div>
      <div className="hs-item">
        <span className="gs-lbl">Band ±{bandTxt}</span>
        <b className={"mono " + (drift ? "warn" : "pos")}>{drift ? "+" + over + "% beyond" : "within band"}</b>
      </div>
      <div className="hs-item">
        <span className="gs-lbl">Hedge</span>
        <b className="mono dim">manual · via order builder</b>
      </div>
      <button className="btn-hedge" disabled title="auto delta-hedge lands in R12 (docs/strategy.md §1)">
        hedge to flat
      </button>
    </div>
  );
}

export function TradeView({ tweaks }: { tweaks: TradeTweaks }): JSX.Element {
  const [closing, setClosing] = useState<Position | null>(null);
  const [builder, setBuilder] = useState<BuilderState | null>(null);
  const { trade } = useDeskData();
  const ticks = useTicks();
  const td = trade.data;
  const positions = td?.positions ?? DATA.positions;
  const greeks = td?.greeks ?? DATA.greeks;
  const account = td?.account ?? DATA.account;
  const limits = td?.limits ?? DATA.limits;
  const events = td?.events ?? DATA.events;
  const cash = td?.cash ?? DATA.cash;
  // Live EURUSD bid/ask (RT.1) ; fallback to a synthetic spread around the mock spot.
  const spotBid = ticks.data?.bid ?? DATA.SPOT - 0.0001;
  const spotAsk = ticks.data?.ask ?? DATA.SPOT + 0.0001;

  return (
    <div className={"trade-grid " + (tweaks.density || "regular")}>
      <div className="trade-main">
        <Panel title="Indicators" dataPp="trade-indicators" right={<FreshBadge fresh={trade} label="state for execution · not a signal" />} className="trade-block">
          <IndicatorsPanel builder={builder} greeks={greeks} account={account} limits={limits} events={events} cash={cash} positions={positions} />
        </Panel>
        <Panel title="Open positions" dataPp="trade-open" pad={false} className="trade-block open-pos-panel">
          <OpenPositionsTable
            showGreeks={tweaks.showGreeks}
            extended={tweaks.showGreeks}
            onClose={setClosing}
            dense={tweaks.density === "compact"}
            positions={positions}
            greeks={greeks}
            showNet={false}
          />
        </Panel>
      </div>
      <div className="trade-side">
        <Panel title="Order builder" dataPp="trade-builder" className="trade-block">
          <HoldingsStrip cash={cash} spotBid={spotBid} spotAsk={spotAsk} />
          <OrderBuilder onState={setBuilder} />
        </Panel>
        <Panel title="Close position" dataPp="trade-close" className="trade-block close-block">
          <ClosePanel pos={closing} onDone={() => setClosing(null)} positions={positions} greeks={greeks} account={account} />
        </Panel>
      </div>
    </div>
  );
}
