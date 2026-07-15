/**
 * VOLDESK — Position breakdown table (rich pb-table): per-open-position greeks,
 * P&L, and live 24h Taylor attribution. Shared by the Risk tab (Greeks) and the
 * Portfolio attribution bridge.
 */
import { fetchPnlAttribution } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { fmt } from "../data";
import type { Position } from "../data";
import { adaptPnlAttributionByPosition, type PositionAttrib } from "../data/live/portfolio";
import { pnlCls } from "./format";

export function PositionBreakdown({ positions }: { positions: Position[] }): JSX.Element {
  const rows = positions;
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
            <th className="r grp-grk col-grp">Delta $</th>
            <th className="r grp-grk">Gamma</th>
            <th className="r grp-grk">Vega</th>
            <th className="r grp-grk">Theta</th>
            <th className="r grp-grk">Vanna</th>
            <th className="r grp-grk col-grp-end">Volga</th>
            <th className="r grp-pnl col-grp col-grp-end">P&L 1d</th>
            <th className="r grp-att col-grp">Delta contrib</th>
            <th className="r grp-att">Theta contrib</th>
            <th className="r grp-att">Vega contrib</th>
            <th className="r grp-att col-grp-end">
              Residual <span className="th-sub">24h</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={19} className="l dim small mono" style={{ padding: "16px 10px" }}>
                no open positions
              </td>
            </tr>
          )}
          {rows.map((p) => {
            const at = attrib[p.id];
            return (
              <tr key={p.id}>
                <td className="l grp-fix mono dim">{p.tradeId || p.packageId || "—"}</td>
                <td className="l grp-fix mono dim">{p.conId || "—"}</td>
                <td className="l grp-fix">{p.product || "—"}</td>
                <td className="l grp-fix">
                  <span className="sym">{p.structure}</span>
                </td>
                <td className="l grp-fix">
                  <span className={"side-pill " + (p.side === "BUY" ? "long" : "short")}>{p.side}</span>
                </td>
                <td className="r mono dim grp-fix">{p.tenor || "—"}</td>
                <td className="r mono dim grp-fix">{p.iv ? p.iv.toFixed(1) : "—"}</td>
                <td className="r mono dim grp-fix">{(p.nominal / 1e6).toFixed(2)}M</td>
                <td className={col(p.delta) + " grp-grk col-grp"}>{k(p.delta)}</td>
                <td className={(p.iv ? col(p.gamma) : "r mono dim") + " grp-grk"}>{p.iv ? (p.gamma / 1000).toFixed(1) + "k" : "—"}</td>
                <td className={(p.iv ? col(p.vega) : "r mono dim") + " grp-grk"}>{p.iv ? k(p.vega) : "—"}</td>
                <td className={(p.iv ? col(p.theta) : "r mono dim") + " grp-grk"}>{p.iv ? k(p.theta) : "—"}</td>
                <td className={(p.iv ? col(p.vanna) : "r mono dim") + " grp-grk"}>{p.iv ? k(p.vanna) : "—"}</td>
                <td className={(p.iv ? col(p.volga) : "r mono dim") + " grp-grk col-grp-end"}>{p.iv ? k(p.volga) : "—"}</td>
                <td className={col(p.pnl) + " grp-pnl col-grp col-grp-end"}>{fmt.usdk(p.pnl)}</td>
                <td className={col(at?.deltaPnl ?? null) + " grp-att col-grp"}>{k(at?.deltaPnl ?? null)}</td>
                <td className={col(at?.thetaPnl ?? null) + " grp-att"}>{k(at?.thetaPnl ?? null)}</td>
                <td className={col(at?.vegaPnl ?? null) + " grp-att"}>{k(at?.vegaPnl ?? null)}</td>
                <td className={col(at?.residual ?? null) + " grp-att col-grp-end"}>{k(at?.residual ?? null)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}