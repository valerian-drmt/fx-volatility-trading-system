/**
 * Vol Signals — distribution + table des signaux dans le contexte vol :
 * full fields incluant sigma_fair_p (P-measure) et vrp_vol_pts. Cf.
 * docs/VOL_ENGINE_REFERENCE.md §4.7. Lit /api/v1/signals.
 */
import { useEffect, useState } from "react";

interface SignalRow {
  id: number;
  timestamp: string;
  underlying: string;
  tenor: string;
  dte: number;
  sigma_mid: number;
  sigma_fair: number;        // Q-measure
  sigma_fair_p: number | null;  // P-measure (HAR/GARCH) — colonne ajoutée migration 009
  vrp_vol_pts: number | null;
  ecart: number;
  signal_type: string;
  rv: number | null;
}

const SIGNAL_TYPES = ["CHEAP", "FAIR", "EXPENSIVE"] as const;

export function VolSignals({ symbol = "EURUSD" }: { symbol?: string }): JSX.Element {
  const [rows, setRows] = useState<SignalRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const params = new URLSearchParams({
          underlying: symbol,
          limit: "50",
          latest_per_tenor: "true",
        });
        const r = await fetch(`/api/v1/signals?${params.toString()}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setRows(await r.json());
      } catch (e) {
        setError(String(e));
      }
    };
    void fetchData();
    const id = window.setInterval(fetchData, 30_000);
    return () => window.clearInterval(id);
  }, [symbol]);

  if (error) return <div style={{ color: "#e66", padding: 12 }}>{error}</div>;

  // Distribution count
  const dist = rows.reduce<Record<string, number>>((acc, r) => {
    acc[r.signal_type] = (acc[r.signal_type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ padding: 12 }}>
      <div style={{ display: "flex", gap: 16, marginBottom: 8, fontSize: 12 }}>
        {SIGNAL_TYPES.map((s) => (
          <span key={s} style={{ color: signalColor(s) }}>
            {s} : <strong>{dist[s] ?? 0}</strong>
          </span>
        ))}
        <span style={{ color: "#666", marginLeft: "auto" }}>{rows.length} rows · latest_per_tenor</span>
      </div>

      {rows.length === 0 ? (
        <div style={{ color: "#666", fontSize: 12 }}>(no signals — vol-engine fallback ou cycle skip)</div>
      ) : (
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={th}>ts</th><th style={th}>tenor</th><th style={th}>DTE</th>
              <th style={th}>σ mid (Q)</th><th style={th}>σ fair (Q)</th>
              <th style={th}>σ fair (P)</th><th style={th}>VRP</th>
              <th style={th}>RV</th><th style={th}>écart</th><th style={th}>signal</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} style={{ borderTop: "1px solid #222" }}>
                <td style={td}>{new Date(r.timestamp).toLocaleTimeString()}</td>
                <td style={td}>{r.tenor}</td>
                <td style={td}>{r.dte}</td>
                <td style={td}>{Number(r.sigma_mid).toFixed(3)}</td>
                <td style={td}>{Number(r.sigma_fair).toFixed(3)}</td>
                <td style={td}>{r.sigma_fair_p !== null ? Number(r.sigma_fair_p).toFixed(3) : "—"}</td>
                <td style={{ ...td, color: r.vrp_vol_pts !== null ? "#7af" : "#888" }}>
                  {r.vrp_vol_pts !== null ? `+${Number(r.vrp_vol_pts).toFixed(3)}` : "—"}
                </td>
                <td style={td}>{r.rv !== null ? Number(r.rv).toFixed(3) : "—"}</td>
                <td style={{ ...td, color: Number(r.ecart) > 0 ? "#cc6" : "#6c6" }}>
                  {Number(r.ecart).toFixed(3)}
                </td>
                <td style={{ ...td, color: signalColor(r.signal_type), fontWeight: 600 }}>
                  {r.signal_type}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function signalColor(s: string): string {
  if (s === "CHEAP") return "#6c6";
  if (s === "EXPENSIVE") return "#e66";
  return "#aaa";
}

const tableStyle = { borderCollapse: "collapse" as const, fontSize: 12, fontFamily: "Consolas, monospace", width: "100%" };
const th = { padding: "4px 8px", textAlign: "left" as const, color: "#888", borderBottom: "1px solid #333" };
const td = { padding: "3px 8px", whiteSpace: "nowrap" as const };
