/**
 * VOLDESK — Position breakdown table (rich pb-table): per-open-position greeks,
 * P&L, and live 24h Taylor attribution. Grouped by trade like Open positions —
 * a collapsible summary line per multi-leg trade (caret ▸, aggregated greeks /
 * P&L / contributions) with its legs indented. Shared by Risk + Portfolio.
 */
import { Fragment, useState } from "react";
import { fetchPnlAttribution } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { fmt } from "../data";
import type { Position } from "../data";
import { adaptPnlAttributionByPosition, type PositionAttrib } from "../data/live/portfolio";
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
  deltaPnl: number | null;
  thetaPnl: number | null;
  vegaPnl: number | null;
  residual: number | null;
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
  // Live per-position P&L attribution over the last 24h (Taylor decomposition
  // from /pnl-attribution) — replaces the old hardcoded 0.35/0.07/0.003 factors.
  const attrib =
    useFetch<Record<string, PositionAttrib>>(
      () => fetchPnlAttribution(24).then(adaptPnlAttributionByPosition),
      60_000,
    ).data ?? {};
  const k = (v: number | null, d = 2): string =>
    v == null
      ? "—"
      : (v >= 0 ? "+" : "-") + (Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(2) + "k" : Math.abs(v).toFixed(d));
  const col = (v: number | null): string => "r mono " + (v == null ? "dim" : pnlCls(v));

  // The 11 numeric cells (greeks · P&L · 24h contribs), shared by leg + summary rows.
  const numCells = (v: NumVals, hasGreeks: boolean): JSX.Element => (
    <>
      <td className={col(v.delta) + " grp-grk col-grp"}>{k(v.delta)}</td>
      <td className={(hasGreeks ? col(v.gamma) : "r mono dim") + " grp-grk"}>{hasGreeks ? (v.gamma / 1000).toFixed(1) + "k" : "—"}</td>
      <td className={(hasGreeks ? col(v.vega) : "r mono dim") + " grp-grk"}>{hasGreeks ? k(v.vega) : "—"}</td>
      <td className={(hasGreeks ? col(v.theta) : "r mono dim") + " grp-grk"}>{hasGreeks ? k(v.theta) : "—"}</td>
      <td className={(hasGreeks ? col(v.vanna) : "r mono dim") + " grp-grk"}>{hasGreeks ? k(v.vanna) : "—"}</td>
      <td className={(hasGreeks ? col(v.volga) : "r mono dim") + " grp-grk col-grp-end"}>{hasGreeks ? k(v.volga) : "—"}</td>
      <td className={col(v.pnl) + " grp-pnl col-grp col-grp-end"}>{fmt.usdk(v.pnl)}</td>
      <td className={col(v.deltaPnl) + " grp-att col-grp"}>{k(v.deltaPnl)}</td>
      <td className={col(v.thetaPnl) + " grp-att"}>{k(v.thetaPnl)}</td>
      <td className={col(v.vegaPnl) + " grp-att"}>{k(v.vegaPnl)}</td>
      <td className={col(v.residual) + " grp-att col-grp-end"}>{k(v.residual)}</td>
    </>
  );

  const legVals = (p: Position): NumVals => {
    const at = attrib[p.id];
    return {
      delta: p.delta, gamma: p.gamma, vega: p.vega, theta: p.theta, vanna: p.vanna, volga: p.volga, pnl: p.pnl,
      deltaPnl: at?.deltaPnl ?? null, thetaPnl: at?.thetaPnl ?? null, vegaPnl: at?.vegaPnl ?? null, residual: at?.residual ?? null,
    };
  };

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
            <th className="r grp-grk col-grp">Δ$</th>
            <th className="r grp-grk">Γ</th>
            <th className="r grp-grk">Vega</th>
            <th className="r grp-grk">Θ</th>
            <th className="r grp-grk">Vanna</th>
            <th className="r grp-grk col-grp-end">Volga</th>
            <th className="r grp-pnl col-grp col-grp-end">P&L 1d</th>
            <th className="r grp-att col-grp">Δ contrib</th>
            <th className="r grp-att">Θ contrib</th>
            <th className="r grp-att">Vega contrib</th>
            <th className="r grp-att col-grp-end">
              Residual <span className="th-sub">24h</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {positions.length === 0 && (
            <tr>
              <td colSpan={19} className="l dim small mono" style={{ padding: "16px 10px" }}>
                no open positions
              </td>
            </tr>
          )}
          {groupByTradeId(positions).map((grp) => {
            if (grp.legs.length === 1) return legRow(grp.legs[0]!, true);
            const isOpen = expanded.has(grp.key);
            const sum = (f: (p: Position) => number): number => grp.legs.reduce((s, p) => s + f(p), 0);
            const sumC = (f: (a: PositionAttrib) => number | null): number | null => {
              const vals = grp.legs.map((l) => attrib[l.id]).filter((a): a is PositionAttrib => a != null).map(f).filter((x): x is number => x != null);
              return vals.length ? vals.reduce((a, b) => a + b, 0) : null;
            };
            const agg: NumVals = {
              delta: sum((p) => p.delta), gamma: sum((p) => p.gamma), vega: sum((p) => p.vega),
              theta: sum((p) => p.theta), vanna: sum((p) => p.vanna), volga: sum((p) => p.volga), pnl: sum((p) => p.pnl),
              deltaPnl: sumC((a) => a.deltaPnl), thetaPnl: sumC((a) => a.thetaPnl), vegaPnl: sumC((a) => a.vegaPnl), residual: sumC((a) => a.residual),
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
