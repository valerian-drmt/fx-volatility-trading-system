/**
 * Step 5 — Active positions monitor.
 *
 * Phase 1 (current) : single table of open positions with live P&L + greeks.
 *  - REST /api/v1/positions/active polled every 5s.
 *  - WS /ws/positions pushes per-cycle updates between polls.
 *  - When markets are closed, P&L / greeks columns stay on their last
 *    persisted MTM snapshot (or "—" if none yet).
 *
 * Aggregate strip, detail drawer, exit alerts, MTM sparkline have been
 * deferred — they will come back in phase 2 once the table is stable.
 */
import { useEffect, useState } from "react";

interface ActivePosition {
  id: number;
  source: "booked" | "ib_live";
  structure_type: string | null;
  reference_tenor: string | null;
  expiry_date: string | null;
  triggering_pc: number | null;
  armed_z_score: number | null;
  armed_signal_label: string | null;
  opened_at: string | null;
  state: string;
  entry_premium_usd: number;
  current_pnl_gross_usd: number | null;
  current_pnl_net_usd: number | null;
  vega_pnl_usd: number | null;
  gamma_pnl_usd: number | null;
  theta_pnl_usd: number | null;
  current_vega_usd_per_volpt: number | null;
  current_delta_unhedged: number | null;
  last_mtm_at: string | null;
  ib_reconciled_at: string | null;
  ib_qty_total: number | null;
  ib_qty_diff: number | null;
  ib_sync_status: "fresh" | "stale" | "missing";
}

const IB_BADGE: Record<ActivePosition["ib_sync_status"], { bg: string; fg: string; label: string }> = {
  fresh:   { bg: "#1f7a3a", fg: "#fff", label: "fresh" },
  stale:   { bg: "#c08a1a", fg: "#fff", label: "stale" },
  missing: { bg: "#a8332a", fg: "#fff", label: "missing" },
};

const fmtUsd = (n: number | null | undefined): string =>
  n === null || n === undefined ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(0)}$`;
const fmtNum = (n: number | null | undefined, d = 2): string =>
  n === null || n === undefined ? "—" : n.toFixed(d);

export function Step5Positions(): JSX.Element {
  const [positions, setPositions] = useState<ActivePosition[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await fetch("/api/v1/positions/active");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const list = (await r.json()) as ActivePosition[];
      setPositions(Array.isArray(list) ? list : []);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 5_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`${proto}://${window.location.host}/ws/positions`);
      ws.onmessage = () => void load();
    } catch { /* poll fallback */ }
    return () => { try { ws?.close(); } catch { /* nop */ } };
  }, []);

  return (
    <div style={{ padding: 16, fontFamily: "system-ui, sans-serif" }}>
      <h2>Step 5 — Active positions monitor</h2>
      {error && <div style={{ color: "crimson", marginBottom: 8 }}>Error: {error}</div>}

      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead style={{ background: "#e8e8ec" }}>
          <tr>
            <th style={th}>Source</th>
            <th style={th}>ID</th>
            <th style={th}>Structure</th>
            <th style={th}>Tenor</th>
            <th style={th}>Expiry</th>
            <th style={th}>Entry signal</th>
            <th style={th}>P&L gross</th>
            <th style={th}>vega P&L</th>
            <th style={th}>gamma P&L</th>
            <th style={th}>theta P&L</th>
            <th style={th}>Vega ($/volpt)</th>
            <th style={th}>Δ unhedged</th>
            <th style={th}>State</th>
            <th style={th}>IB qty</th>
            <th style={th}>IB sync</th>
            <th style={th}>Last MTM</th>
          </tr>
        </thead>
        <tbody>
          {positions.length === 0 && (
            <tr>
              <td colSpan={16} style={{ padding: 12, textAlign: "center", color: "#999" }}>
                No open positions. Submit a trade in Step 3 to see one here.
              </td>
            </tr>
          )}
          {positions.map((p) => {
            const pnlColor =
              p.current_pnl_gross_usd === null
                ? "#666"
                : p.current_pnl_gross_usd >= 0
                ? "#0a7a0a"
                : "#c83232";
            return (
              <tr key={`${p.source}-${p.id}`} style={{ borderTop: "1px solid #ddd" }}>
                <td style={td}>
                  <span
                    style={{
                      background: p.source === "booked" ? "#3a5a8a" : "#5a3a8a",
                      color: "#fff",
                      padding: "1px 6px",
                      borderRadius: 3,
                      fontSize: 11,
                      fontWeight: 600,
                    }}
                  >
                    {p.source === "booked" ? "Step3" : "IB"}
                  </span>
                </td>
                <td style={td}>{p.id}</td>
                <td style={td}>{p.structure_type ?? "—"}</td>
                <td style={td}>{p.reference_tenor ?? "—"}</td>
                <td style={td}>{p.expiry_date ?? "—"}</td>
                <td style={td}>
                  {p.triggering_pc !== null
                    ? `PC${p.triggering_pc} ${p.armed_signal_label ?? ""} z=${fmtNum(
                        p.armed_z_score,
                        2,
                      )}`
                    : "manual"}
                </td>
                <td style={{ ...td, color: pnlColor, fontWeight: 600 }}>
                  {fmtUsd(p.current_pnl_gross_usd)}
                </td>
                <td style={td}>{fmtUsd(p.vega_pnl_usd)}</td>
                <td style={td}>{fmtUsd(p.gamma_pnl_usd)}</td>
                <td style={td}>{fmtUsd(p.theta_pnl_usd)}</td>
                <td style={td}>{fmtNum(p.current_vega_usd_per_volpt, 0)}</td>
                <td style={td}>{fmtNum(p.current_delta_unhedged, 3)}</td>
                <td style={td}>{p.state}</td>
                <td style={td}>
                  {p.ib_qty_total ?? "—"}
                  {p.ib_qty_diff != null && p.ib_qty_diff !== 0 && (
                    <span style={{ color: "#c83232", marginLeft: 4 }}>
                      (Δ{p.ib_qty_diff > 0 ? "+" : ""}{p.ib_qty_diff})
                    </span>
                  )}
                </td>
                <td style={td}>
                  <span
                    style={{
                      background: IB_BADGE[p.ib_sync_status].bg,
                      color: IB_BADGE[p.ib_sync_status].fg,
                      padding: "1px 6px",
                      borderRadius: 3,
                      fontSize: 11,
                      fontWeight: 600,
                    }}
                    title={
                      p.ib_reconciled_at
                        ? `last reconcile: ${new Date(p.ib_reconciled_at).toLocaleString()}`
                        : "never reconciled — execution-engine offline?"
                    }
                  >
                    {IB_BADGE[p.ib_sync_status].label}
                  </span>
                </td>
                <td style={td}>
                  {p.last_mtm_at ? new Date(p.last_mtm_at).toLocaleTimeString() : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const th: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid #aaa",
  fontWeight: 600,
};

const td: React.CSSProperties = {
  padding: "4px 8px",
  fontFamily: "ui-monospace, monospace",
};
