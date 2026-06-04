/**
 * OpenPositionsTable — shared Panel E rendering.
 *
 * 1-to-1 mirror of the DB ``open_position`` table (migration 033). The
 * companion ``open_position_history`` table holds per-cycle snapshots
 * with the same shape minus the FK back to the live row.
 *
 * Backend cadence :
 *   - risk-engine UPDATEs ``open_position`` + INSERTs ``open_position_history``
 *     every 2 s (greeks / market_price / pnl).
 *   - position_sync_loop INSERTs / DELETEs rows every 30 s on IB diffs.
 *
 * Columns rendered = DB columns, minus the ``entry_timestamp`` field
 * (we keep ``timestamp`` as "Last update" which is the operational
 * freshness indicator).
 */
import type { CSSProperties } from "react";

export interface OpenPositionRow {
  id: number;
  structure: string;
  product_label: string | null;
  // Murex-aligned identity stack (migration 034) :
  //   contract_id = IB conId (atomic instrument).
  //   trade_id    = FK trade_structure.id (= "strategy" ; 2 legs of a
  //                 straddle share one trade_id).
  //   package_id  = FK package.id (operational grouping of trades).
  contract_id: number | null;
  trade_id: number | null;
  package_id: number | null;
  side: string;
  tenor: string | null;
  expiry: string | null;          // ISO date
  quantity: number;
  nominal_eur: number | null;
  contract_price_entry: number | null;
  market_price: number | null;
  current_pnl_usd: number | null;
  delta_usd: number | null;
  gamma_usd: number | null;
  vega_usd: number | null;
  theta_usd: number | null;
  iv: number | null;
  vanna_usd: number | null;
  volga_usd: number | null;
  entry_timestamp: string | null; // kept on the interface ; not rendered
  timestamp: string | null;       // ISO with TZ ; rendered as "Last update"
}

function fmtCompact(
  n: number | null | undefined, d = 2, withSign = true,
): string {
  if (n === null || n === undefined) return "—";
  const sign = withSign ? (n >= 0 ? "+" : "-") : (n < 0 ? "-" : "");
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(d)}B`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(d)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(d)}k`;
  return `${sign}${abs.toFixed(d)}`;
}
function fmtNum(n: number | null | undefined, d = 2): string {
  return n === null || n === undefined ? "—" : n.toFixed(d);
}
function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  // Trim to "YYYY-MM-DD HH:MM:SS" for compact display.
  return iso.replace("T", " ").slice(0, 19);
}

const tableStyle: CSSProperties = {
  width: "100%", borderCollapse: "collapse", fontFamily: "Consolas, monospace",
  fontSize: 11,
};
const th: CSSProperties = {
  padding: "4px 8px", textAlign: "right", color: "#7af",
  borderBottom: "1px solid #1f2937", whiteSpace: "nowrap",
};
const td: CSSProperties = {
  padding: "3px 8px", textAlign: "right", color: "#ddd",
  borderBottom: "1px solid #161616", whiteSpace: "nowrap",
};

export function OpenPositionsTable({
  positions,
}: {
  positions: OpenPositionRow[];
}): JSX.Element {
  if (positions.length === 0) {
    return (
      <div style={{ padding: 8, color: "#666", fontStyle: "italic", fontSize: 12 }}>
        (no open positions)
      </div>
    );
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={tableStyle}>
        <thead>
          <tr>
            {/* Identity & grouping */}
            <th style={th}>ID</th>
            <th style={th}>Package</th>
            <th style={th}>Trade</th>
            <th style={th}>Contract</th>
            <th style={th}>Product</th>
            <th style={th}>Structure (IB)</th>
            <th style={th}>Side</th>
            {/* Spec */}
            <th style={th}>Qty</th>
            <th style={th}>Tenor</th>
            <th style={th}>Expiry</th>
            {/* P&L & pricing */}
            <th style={th}>P&L (pending)</th>
            <th style={th}>Market price</th>
            <th style={th}>Entry price</th>
            <th style={th}>Nominal (EUR)</th>
            {/* Main greeks */}
            <th style={th}>Δ ($)</th>
            <th style={th}>Γ ($/pip)</th>
            <th style={th}>Vega ($/volpt)</th>
            <th style={th}>Θ ($/day)</th>
            <th style={th}>IV (%)</th>
            {/* Secondary greeks */}
            <th style={th}>Vanna ($/vp)</th>
            <th style={th}>Volga ($/vp²)</th>
            {/* Metadata */}
            <th style={th}>Last update</th>
            <th style={th}>Opened at</th>
          </tr>
        </thead>
        <tbody>
          {[...positions].sort((a, b) =>
            // Sort by package_id, then trade_id, then id : legs of the
            // same trade sit together, trades of the same package above
            // ungrouped (null) rows at the bottom.
            (a.package_id ?? Number.MAX_SAFE_INTEGER) - (b.package_id ?? Number.MAX_SAFE_INTEGER)
            || (a.trade_id ?? Number.MAX_SAFE_INTEGER) - (b.trade_id ?? Number.MAX_SAFE_INTEGER)
            || a.id - b.id
          ).map((p) => (
            <tr key={p.id}>
              {/* Identity & grouping */}
              <td style={td}>{p.id}</td>
              <td style={{ ...td, color: p.package_id != null ? "#fc6" : "#666" }}>
                {p.package_id != null ? `#${p.package_id}` : "—"}
              </td>
              <td style={{ ...td, color: p.trade_id != null ? "#7af" : "#666",
                          fontWeight: p.trade_id != null ? 600 : 400 }}>
                {p.trade_id != null ? `#${p.trade_id}` : "—"}
              </td>
              <td style={{ ...td, color: "#888" }}>
                {p.contract_id != null ? p.contract_id : "—"}
              </td>
              <td style={td}>{p.product_label ?? "—"}</td>
              <td style={td}>{p.structure}</td>
              <td style={{ ...td, fontWeight: 600,
                          color: p.side === "BUY" ? "#6c6"
                               : p.side === "SELL" ? "#e66" : "#888" }}>
                {p.side}
              </td>
              {/* Spec */}
              <td style={td}>{Math.abs(p.quantity)}</td>
              <td style={td}>{p.tenor ?? "—"}</td>
              <td style={td}>{p.expiry ?? "—"}</td>
              {/* P&L & pricing */}
              <td style={{ ...td, color: (p.current_pnl_usd ?? 0) >= 0 ? "#6c6" : "#e66" }}>
                {fmtCompact(p.current_pnl_usd)}$
              </td>
              <td style={td}>{fmtNum(p.market_price, 5)}</td>
              <td style={td}>{fmtNum(p.contract_price_entry, 5)}</td>
              <td style={td}>{fmtCompact(p.nominal_eur, 2, false)} €</td>
              {/* Main greeks */}
              <td style={td}>{fmtCompact(p.delta_usd)}</td>
              <td style={td}>{fmtCompact(p.gamma_usd)}</td>
              <td style={td}>{fmtCompact(p.vega_usd)}</td>
              <td style={td}>{fmtCompact(p.theta_usd)}</td>
              <td style={td}>{p.iv != null ? `${(p.iv * 100).toFixed(2)}%` : "—"}</td>
              {/* Secondary greeks */}
              <td style={td}>{fmtCompact(p.vanna_usd)}</td>
              <td style={td}>{fmtCompact(p.volga_usd)}</td>
              {/* Metadata */}
              <td style={{ ...td, color: "#888" }}>{fmtTs(p.timestamp)}</td>
              <td style={{ ...td, color: "#888" }}>{fmtTs(p.entry_timestamp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
