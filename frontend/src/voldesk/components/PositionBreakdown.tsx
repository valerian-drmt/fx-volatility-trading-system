/**
 * VOLDESK — Position breakdown table (rich pb-table): per-open-position greeks
 * and P&L. Grouped by trade like Open positions — a collapsible summary line
 * per multi-leg trade (caret ▸, aggregated greeks / P&L) with its legs
 * indented. Shared by Risk + Portfolio.
 */
import type { JSX } from "react";
import { Fragment, useState } from "react";
import { fmt } from "../data";
import type { Position } from "../data";
import { pnlCls } from "./format";
import { GroupIdentityCells, IdentityCells, IdentityHead } from "./LineIdentity";
import { groupLines, identFrom } from "./lineIdent";

interface NumVals {
  delta: number | null;   // null = nothing priced the line: shown "—", not 0
  gamma: number;
  vega: number;
  theta: number;
  vanna: number;
  volga: number;
  pnl: number | null;
}

export function PositionBreakdown({ positions: all }: { positions: Position[] }): JSX.Element {
  // A spot FX balance is cash, not a contract: it is not a position row. Its
  // delta stays in the net greeks, which are summed from the full feed.
  const positions = all.filter((p) => p.lineStatus !== "cash");
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
      <td className={col(v.pnl) + " grp-pnl col-grp col-grp-end"}>{v.pnl == null ? "—" : fmt.usdk(v.pnl)}</td>
    </>
  );

  const legVals = (p: Position): NumVals => ({
    delta: p.delta, gamma: p.gamma, vega: p.vega, theta: p.theta, vanna: p.vanna, volga: p.volga, pnl: p.pnl,
  });

  const legRow = (p: Position, main: boolean): JSX.Element => (
    <tr key={p.lineId ?? p.id} className={main ? undefined : "pos-leg"} data-line-id={p.lineId}>
      <IdentityCells ident={p.ident ?? identFrom(p)} main={main} status={p.lineStatus} cls="grp-fix" />
      <td className="r mono dim grp-fix">{p.iv ? p.iv.toFixed(1) : "—"}</td>
      <td className="r mono dim grp-fix">{p.unpriced ? "—" : (p.nominal / 1e6).toFixed(2) + "M"}</td>
      {numCells(p.unpriced ? { ...legVals(p), delta: null, pnl: null } : legVals(p), !!p.iv)}
    </tr>
  );

  return (
    <div className="table-scroll">
      <table className="dt pb-table">
        <thead>
          <tr>
            <IdentityHead cls="grp-fix" />
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
              <td colSpan={16} className="l dim small mono" style={{ padding: "16px 10px" }}>
                no open positions
              </td>
            </tr>
          )}
          {groupLines(positions).map((grp) => {
            if (grp.legs.length === 1) return legRow(grp.legs[0]!, true);
            const isOpen = expanded.has(grp.key);
            const sum = (f: (p: Position) => number): number => grp.legs.reduce((s, p) => s + f(p), 0);
            const agg: NumVals = {
              delta: sum((p) => p.delta), gamma: sum((p) => p.gamma), vega: sum((p) => p.vega),
              theta: sum((p) => p.theta), vanna: sum((p) => p.vanna), volga: sum((p) => p.volga), pnl: sum((p) => p.pnl),
            };
            const hasGreeks = grp.legs.some((l) => !!l.iv);
            return (
              <Fragment key={grp.key}>
                <tr className={"pos-main" + (isOpen ? " open" : "")} onClick={() => toggle(grp.key)}>
                  <GroupIdentityCells
                    idents={grp.legs.map((p) => p.ident ?? identFrom(p))}
                    isOpen={isOpen}
                    onToggle={() => toggle(grp.key)}
                    cls="grp-fix"
                  />
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
