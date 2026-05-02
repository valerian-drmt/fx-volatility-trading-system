/**
 * Step 4 — submitted trades list (mock execution).
 *
 * Reads /api/v1/trade/submitted every 5s. No real IB integration ; structures
 * are created server-side by the mock Submit endpoint and listed here.
 */
import { useEffect, useState } from "react";

interface Submitted {
  id: number;
  created_at: string;
  structure_type: string;
  reference_tenor: string;
  base_qty: number;
  state: string;
  execution_mode: string;
  total_premium_paid_usd: number | null;
  total_commission_usd: number | null;
  total_entry_cost_usd: number | null;
  preview_id: string | null;
  position_id: number | null;
  position_state: string | null;
}

export function Step4Trades(): JSX.Element {
  const [rows, setRows] = useState<Submitted[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch("/api/v1/trade/submitted?limit=100");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        setRows(Array.isArray(j) ? j : []);
        setError(null);
      } catch (e) { setError(String(e)); }
    };
    void load();
    const id = window.setInterval(load, 5_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
      <section style={{
        background: "#0a0a0a", border: "1px solid #222", borderRadius: 4, overflow: "hidden",
      }}>
        <div style={{
          padding: "5px 12px", background: "#1a1a1a", borderBottom: "1px solid #333",
          color: "#7af", fontSize: 11, fontWeight: 600, letterSpacing: 1, textTransform: "uppercase",
        }}>
          Submitted trades (mock execution)
        </div>
        <div style={{ padding: 8, fontSize: 11 }}>
          {error && <div style={{ color: "#fcc", marginBottom: 6 }}>✗ {error}</div>}
          {rows.length === 0 ? (
            <div style={{ color: "#666", fontStyle: "italic", padding: 6 }}>
              no submitted trades yet — go to Step 3, click Review then Book.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "Consolas, monospace" }}>
              <thead>
                <tr style={{ color: "#aaa", fontSize: 10 }}>
                  <th style={thStyle}>id</th>
                  <th style={thStyle}>created_at</th>
                  <th style={thStyle}>structure</th>
                  <th style={thStyle}>tenor</th>
                  <th style={thStyle}>qty</th>
                  <th style={thStyle}>state</th>
                  <th style={thStyle}>mode</th>
                  <th style={thStyle}>premium</th>
                  <th style={thStyle}>commission</th>
                  <th style={thStyle}>entry cost</th>
                  <th style={thStyle}>position</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td style={tdStyle}>{r.id}</td>
                    <td style={tdStyle}>{r.created_at?.replace("T", " ").slice(0, 19) ?? "—"}</td>
                    <td style={tdStyle}>{r.structure_type}</td>
                    <td style={tdStyle}>{r.reference_tenor}</td>
                    <td style={tdStyle}>{r.base_qty}</td>
                    <td style={{ ...tdStyle, color: r.state === "fully_filled" ? "#6c6" : r.state === "submitted" ? "#fc6" : "#e66" }}>
                      {r.state}
                    </td>
                    <td style={tdStyle}>{r.execution_mode}</td>
                    <td style={tdStyle}>{fmt(r.total_premium_paid_usd, 2)}</td>
                    <td style={tdStyle}>{fmt(r.total_commission_usd, 2)}</td>
                    <td style={tdStyle}>{fmt(r.total_entry_cost_usd, 2)}</td>
                    <td style={tdStyle}>
                      {r.position_id ? `#${r.position_id} (${r.position_state})` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
      <div style={{ fontSize: 10, color: "#666" }}>
        Real IB integration is deferred. STEP4 spec phase 1 = mock fills (you are here).
        Phase 2 = paper trading vs IB Gateway. Phase 3 = live micro size.
      </div>
    </div>
  );
}

function fmt(v: number | null | undefined, digits: number): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

const thStyle: React.CSSProperties = {
  padding: "4px 8px", textAlign: "left", fontWeight: 600, fontSize: 10,
  borderBottom: "1px solid #222",
};
const tdStyle: React.CSSProperties = {
  padding: "3px 8px", borderBottom: "1px solid #1a1a1a", color: "#ddd", fontSize: 11,
};
