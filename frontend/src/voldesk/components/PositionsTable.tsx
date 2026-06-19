/**
 * VOLDESK — OpenPositionsTable (rich net-strip + per-leg table) + CashHoldings.
 * Ported from the prototype's `js/positions_table.jsx`. Used by Trade and
 * Portfolio. Net greeks read the single reconciled store (DATA.greeks), so the
 * book foots identically to Risk.
 */
import { pnlCls } from "./format";
import { DATA, fmt } from "../data";
import type { Greeks, Position } from "../data";

// compact signed formatter for per-leg / net greek cells (±N · ±N.Nk · ±N.NNM).
// NOTE: distinct from common's gk$ — this one omits the "$" prefix by design.
function gkc(v: number | null | undefined): string {
  if (v == null) return "—";
  const s = v < 0 ? "-" : "+";
  const a = Math.abs(v);
  if (a >= 1e6) return s + (a / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return s + (a / 1e3).toFixed(1) + "k";
  return s + Math.round(a);
}

interface OpenPositionsTableProps {
  showGreeks?: boolean;
  extended?: boolean;
  onClose?: (p: Position) => void;
  dense?: boolean;
  /** Live positions + book greeks (PR 6r). Default to the mock when omitted. */
  positions?: Position[];
  greeks?: Greeks;
}

export function OpenPositionsTable({
  showGreeks = true,
  extended = false,
  onClose,
  dense = false,
  positions = DATA.positions,
  greeks = DATA.greeks,
}: OpenPositionsTableProps): JSX.Element {
  const rows = positions;
  const g = greeks;
  const total = g.netUnreal,
    tNom = g.netNominal;
  return (
    <div className="positions-wrap">
      <div className="net-strip">
        <div className="net-id">
          <span className="dim small">Book net</span>
          <span className="net-id-val mono">{rows.length} legs</span>
          <span className="dim small mono">one engine · = Risk</span>
        </div>
        <div className="net-tiles">
          <div className="metric">
            <span className="metric-label">
              Δ net <em className="unit">$</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netDelta)}>{gkc(g.netDelta)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Γ net <em className="unit">$/pip</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netGamma)}>{gkc(g.netGamma)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Vega net <em className="unit">$/vp</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netVega)}>{gkc(g.netVega)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Vanna net <em className="unit">$k/vp·fig</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netVanna)}>{fmt.sgn(g.netVanna, 0)}k</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Volga net <em className="unit">$k/vp</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netVolga)}>{fmt.sgn(g.netVolga, 0)}k</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Θ net <em className="unit">$/day</em>
            </span>
            <span className={"metric-value mono " + pnlCls(g.netTheta)}>{gkc(g.netTheta)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">
              Nominal <em className="unit">€</em>
            </span>
            <span className="metric-value mono">{(tNom / 1e6).toFixed(1)}M</span>
          </div>
          <div className="metric">
            <span className="metric-label">Unrealized P&L</span>
            <span className={"metric-value mono " + pnlCls(total)}>{fmt.usdk(total)}</span>
          </div>
        </div>
      </div>
      <div className="table-scroll">
        <table className={"dt positions-table" + (dense ? " dense" : "")}>
          <thead>
            <tr>
              <th className="l">Trade</th>
              <th className="l">Contract</th>
              <th className="l">Product</th>
              <th className="l">Structure</th>
              <th>Side</th>
              <th className="r">Qty</th>
              <th className="r">Tenor</th>
              <th className="r">DTE</th>
              <th className="r">Entry</th>
              <th className="r">Mark</th>
              <th className="r">IV</th>
              {showGreeks && (
                <>
                  <th className="r" title="USD">
                    Δ$
                  </th>
                  <th className="r" title="USD/pip">
                    Γ
                  </th>
                  <th className="r" title="USD/vol pt">
                    Vega
                  </th>
                  <th className="r" title="USD/day">
                    Θ
                  </th>
                </>
              )}
              {showGreeks && extended && (
                <>
                  <th className="r" title="$k per 1vp·1 big-fig">
                    Vanna
                  </th>
                  <th className="r" title="$k/vp">
                    Volga
                  </th>
                </>
              )}
              <th className="r">Nominal €</th>
              <th className="r">P&L</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p, i) => {
              const newPkg = i === 0 || rows[i - 1]!.packageId !== p.packageId;
              return (
                <tr key={p.id} className={newPkg ? "pkg-start" : ""}>
                  <td className="l mono dim">{p.tradeId ? "#" + p.tradeId : "—"}</td>
                  <td className="l mono dim">{p.conId ? p.conId : "—"}</td>
                  <td className="l">
                    <span className="sym">{p.product || "—"}</span>
                    <span className="substruct">
                      {p.packageId ? p.packageId + " · " : ""}{p.expiry}
                      {p.strike ? " · K " + p.strike.toFixed(4) : ""}
                    </span>
                  </td>
                  <td className="l mono dim">{p.structure || "—"}</td>
                  <td>
                    <span className={"side-pill " + (p.side === "BUY" ? "long" : "short")}>{p.side}</span>
                  </td>
                  <td className="r mono">{p.qty}</td>
                  <td className="r mono dim">{p.tenor}</td>
                  <td className="r mono dim">{p.iv ? p.dte + "d" : "—"}</td>
                  <td className="r mono">{p.entry ? p.entry.toFixed(p.entry > 1.5 ? 4 : 5) : "—"}</td>
                  <td className="r mono">{p.mark ? p.mark.toFixed(p.mark > 1.5 ? 4 : 5) : "—"}</td>
                  <td className="r mono dim">{p.iv ? p.iv.toFixed(1) : "—"}</td>
                  {showGreeks && (
                    <>
                      <td className={"r mono " + pnlCls(p.delta)}>{gkc(p.delta)}</td>
                      <td className="r mono dim">{p.iv ? gkc(p.gamma) : "—"}</td>
                      <td className="r mono dim">{p.iv ? gkc(p.vega) : "—"}</td>
                      <td className="r mono dim">{p.iv ? gkc(p.theta) : "—"}</td>
                    </>
                  )}
                  {showGreeks && extended && (
                    <>
                      <td className="r mono dim">{p.iv ? fmt.sgn(p.vanna, 0) + "k" : "—"}</td>
                      <td className="r mono dim">{p.iv ? fmt.sgn(p.volga, 0) + "k" : "—"}</td>
                    </>
                  )}
                  <td className="r mono dim">{(p.nominal / 1e6).toFixed(2)}M</td>
                  <td className={"r mono " + pnlCls(p.pnl)}>{fmt.usdk(p.pnl)}</td>
                  <td className="r">
                    <button className="row-close" onClick={() => onClose && onClose(p)}>
                      Close
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function CashHoldings({ compact = false }: { compact?: boolean }): JSX.Element {
  const total = DATA.cash.reduce((s, c) => s + c.usd, 0);
  return (
    <div className="table-scroll">
      <table className="dt cash">
        <thead>
          <tr>
            <th className="l">Ccy</th>
            <th className="r">Settled</th>
            {!compact && <th className="r">Unsettled</th>}
            <th className="r">Rate</th>
            <th className="r">USD value</th>
          </tr>
        </thead>
        <tbody>
          {DATA.cash.map((c, i) => (
            <tr key={i}>
              <td className="l">
                <span className="ccy-dot" />
                {c.ccy}
              </td>
              <td className={"r mono " + pnlCls(c.settled)}>{fmt.num(c.settled, 0)}</td>
              {!compact && (
                <td className={"r mono " + (c.unsettled ? pnlCls(c.unsettled) : "dim")}>
                  {c.unsettled ? fmt.num(c.unsettled, 0) : "—"}
                </td>
              )}
              <td className="r mono dim">{c.rate.toFixed(4)}</td>
              <td className={"r mono " + pnlCls(c.usd)}>{fmt.usd(c.usd)}</td>
            </tr>
          ))}
          <tr className="total-row">
            <td className="l">Net cash (USD)</td>
            <td className="r mono" colSpan={compact ? 2 : 3}></td>
            <td className="r mono">{fmt.usd(total)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
