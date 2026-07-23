/**
 * VOLDESK — Position breakdown table (rich pb-table): per-open-position greeks
 * and P&L. Grouped by trade like Open positions — a collapsible summary line
 * per multi-leg trade (caret ▸, aggregated greeks / P&L) with its legs
 * indented. Shared by Risk + Portfolio.
 */
import { Fragment, useState } from "react";
import { fmt } from "../data";
import type { Position } from "../data";
import { pnlCls } from "./format";
import { groupByTradeId, structureName, structureSide } from "./tradeGrouping";

interface NumVals {
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  vanna: number;
  volga: number;
  pnl: number;
}

export function PositionBreakdown({ positions }: { positions: Position[] }): JSX.Element {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (key: string): void =>
    setExpanded((prev) => {
      const n = new Set(prev);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  const k = (v: number | null, d = 2): string =>
    v == null
      ? "—"
      : (v >= 0 ? "+" : "-") + (Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(2) + "k" : Math.abs(v).toFixed(d));
  const col = (v: number | null): string => "r mono " + (v == null ? "dim" : pnlCls(v));

  // The 7 numeric cells (greeks · P&L), shared by leg + summary rows.
  const numCells = (v: NumVals, hasGreeks: boolean): JSX.Element => (
    <>
      <td className={col(v.delta) + " grp-grk col-grp"}>{k(v.delta)}</td>
      <td className={(hasGreeks ? col(v.gamma) : "r mono dim") + " grp-grk"}>{hasGreeks ? (v.gamma / 1000).toFixed(1) + "k" : "—"}</td>
      <td className={(hasGreeks ? col(v.vega) : "r mono dim") + " grp-grk"}>{hasGreeks ? k(v.vega) : "—"}</td>
      <td className={(hasGreeks ? col(v.theta) : "r mono dim") + " grp-grk"}>{hasGreeks ? k(v.theta) : "—"}</td>
      <td className={(hasGreeks ? col(v.vanna) : "r mono dim") + " grp-grk"}>{hasGreeks ? k(v.vanna) : "—"}</td>
      <td className={(hasGreeks ? col(v.volga) : "r mono dim") + " grp-grk col-grp-end"}>{hasGreeks ? k(v.volga) : "—"}</td>
      <td className={col(v.pnl) + " grp-pnl col-grp col-grp-end"}>{fmt.usdk(v.pnl)}</td>
    </>
  );

  const legVals = (p: Position): NumVals => ({
    delta: p.delta, gamma: p.gamma, vega: p.vega, theta: p.theta, vanna: p.vanna, volga: p.volga, pnl: p.pnl,
  });

  const legRow = (p: Position, main: boolean): JSX.Element => (
    <tr key={p.id} className={main ? undefined : "pos-leg"}>
      <td className="l grp-fix mono dim">{main ? p.tradeId || p.packageId || "—" : ""}</td>
      <td className="l grp-fix mono dim">{p.conId || "—"}</td>
      <td className="l grp-fix">
        <span className="sym">{main ? "" : "↳ "}{p.product || "—"}</span>
      </td>
      <td className="l grp-fix">
        <span className="sym">{p.structure}</span>
      </td>
      <td className="l grp-fix">
        <span className={"side-pill " + (p.side === "BUY" ? "long" : "short")}>{p.side}</span>
      </td>
      <td className="r mono dim grp-fix">{p.tenor || "—"}</td>
      <td className="r mono dim grp-fix">{p.iv ? p.iv.toFixed(1) : "—"}</td>
      <td className="r mono dim grp-fix">{(p.nominal / 1e6).toFixed(2)}M</td>
      {numCells(legVals(p), !!p.iv)}
    </tr>
  );

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
            <th className="r grp-fix">Nominal €</th>
            <th className="r grp-grk col-grp">Δ</th>
            <th className="r grp-grk">Γ</th>
            <th className="r grp-grk">Vega</th>
            <th className="r grp-grk">Θ</th>
            <th className="r grp-grk">Vanna</th>
            <th className="r grp-grk col-grp-end">Volga</th>
            <th className="r grp-pnl col-grp col-grp-end">P&L 1d</th>
          </tr>
        </thead>
        <tbody>
          {positions.length === 0 && (
            <tr>
              <td colSpan={15} className="l dim small mono" style={{ padding: "16px 10px" }}>
                no open positions
              </td>
            </tr>
          )}
          {groupByTradeId(positions).map((grp) => {
            if (grp.legs.length === 1) return legRow(grp.legs[0]!, true);
            const isOpen = expanded.has(grp.key);
            const sum = (f: (p: Position) => number): number => grp.legs.reduce((s, p) => s + f(p), 0);
            const agg: NumVals = {
              delta: sum((p) => p.delta), gamma: sum((p) => p.gamma), vega: sum((p) => p.vega),
              theta: sum((p) => p.theta), vanna: sum((p) => p.vanna), volga: sum((p) => p.volga), pnl: sum((p) => p.pnl),
            };
            const tenors = new Set(grp.legs.map((l) => l.tenor).filter(Boolean));
            const side = structureSide(grp.legs);
            const hasGreeks = grp.legs.some((l) => !!l.iv);
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
                    <span className="sym">{structureName(grp.legs)}</span>
                  </td>
                  <td className="l grp-fix mono dim">—</td>
                  <td className="l grp-fix">
                    <span className={"side-pill " + (side === "BUY" ? "long" : "short")}>{side}</span>
                  </td>
                  <td className="r mono dim grp-fix">{tenors.size === 1 ? [...tenors][0] : "—"}</td>
                  <td className="r mono dim grp-fix">—</td>
                  <td className="r mono dim grp-fix">{(sum((p) => p.nominal) / 1e6).toFixed(2)}M</td>
                  {numCells(agg, hasGreeks)}
                </tr>
                {isOpen && grp.legs.map((p) => legRow(p, false))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
