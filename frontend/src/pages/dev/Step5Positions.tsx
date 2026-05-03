/**
 * Step 5 — Active positions monitoring panel.
 *
 * Reads /api/v1/positions/active + /aggregate every 5s. No live data when
 * markets are closed — values come from the api position_monitor scheduler
 * which fills in zeroes for delta/iv when Redis surface is empty.
 *
 * Layout (cf. STEP5 §5) :
 *  A. Aggregate greeks bar (top)
 *  B. Open structures table with current MTM + signal status
 *  C. Click on a row → detail strip : MTM history sparkline + alerts + hedges
 */
import { useEffect, useState } from "react";

interface ActivePosition {
  id: number;
  structure_id: number;
  structure_type: string | null;
  reference_tenor: string | null;
  expiry_date: string | null;
  triggering_pc: number | null;
  armed_z_score: number | null;
  armed_signal_label: string | null;
  opened_at: string | null;
  state: string;
  entry_premium_usd: number;
  entry_total_cost_usd: number;
  entry_vega_usd_per_volpt: number | null;
  entry_gamma_usd_per_pip2: number | null;
  entry_theta_usd_per_day: number | null;
  current_pnl_gross_usd: number | null;
  current_pnl_net_usd: number | null;
  vega_pnl_usd: number | null;
  gamma_pnl_usd: number | null;
  theta_pnl_usd: number | null;
  current_vega_usd_per_volpt: number | null;
  current_delta_unhedged: number | null;
  last_mtm_at: string | null;
}

interface Aggregate {
  n_open_positions: number;
  total_vega_usd_per_volpt: number;
  total_gamma_usd_per_pip2: number;
  total_theta_usd_per_day: number;
  total_delta_unhedged: number;
}

interface ExitAlert {
  id: number;
  timestamp: string;
  rule_triggered: string;
  action_recommended: string;
  priority: number;
  rule_detail: Record<string, unknown>;
  auto_executed: boolean;
  execution_status: string | null;
  closing_structure_id: number | null;
}

interface MtmRow {
  timestamp: string;
  spot: number;
  iv_avg_legs_pct: number | null;
  pnl_gross_usd: number;
  pnl_net_usd: number;
  vega_pnl_usd: number | null;
  gamma_pnl_usd: number | null;
  theta_pnl_usd: number | null;
  other_pnl_usd: number | null;
  vega_usd_per_volpt: number | null;
}

const fmtUsd = (n: number | null | undefined): string =>
  n === null || n === undefined ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(0)}$`;
const fmtNum = (n: number | null | undefined, d = 2): string =>
  n === null || n === undefined ? "—" : n.toFixed(d);

export function Step5Positions(): JSX.Element {
  const [positions, setPositions] = useState<ActivePosition[]>([]);
  const [agg, setAgg] = useState<Aggregate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [alerts, setAlerts] = useState<ExitAlert[]>([]);
  const [mtmHist, setMtmHist] = useState<MtmRow[]>([]);
  const [running, setRunning] = useState(false);

  const load = async () => {
    try {
      const [r1, r2] = await Promise.all([
        fetch("/api/v1/positions/active"),
        fetch("/api/v1/positions/aggregate"),
      ]);
      if (!r1.ok || !r2.ok) throw new Error(`HTTP ${r1.status}/${r2.status}`);
      const list = (await r1.json()) as ActivePosition[];
      const a = (await r2.json()) as Aggregate;
      setPositions(Array.isArray(list) ? list : []);
      setAgg(a);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  const loadDetail = async (id: number) => {
    try {
      const [r1, r2] = await Promise.all([
        fetch(`/api/v1/positions/${id}/alerts?limit=20`),
        fetch(`/api/v1/positions/${id}/mtm-history?hours=24&limit=200`),
      ]);
      setAlerts(r1.ok ? await r1.json() : []);
      setMtmHist(r2.ok ? await r2.json() : []);
    } catch {
      setAlerts([]);
      setMtmHist([]);
    }
  };

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 5_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (selectedId !== null) void loadDetail(selectedId);
  }, [selectedId]);

  const triggerCycle = async () => {
    setRunning(true);
    try {
      const r = await fetch("/api/v1/positions/monitor/run-once", { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await load();
      if (selectedId) await loadDetail(selectedId);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  };

  const closeManual = async (id: number) => {
    if (!window.confirm(`Mark position ${id} for manual close (mock)?`)) return;
    try {
      const r = await fetch(`/api/v1/positions/${id}/close-manual`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await load();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div style={{ padding: 16, fontFamily: "system-ui, sans-serif" }}>
      <h2>Step 5 — Active positions monitor</h2>
      {error && <div style={{ color: "crimson", marginBottom: 8 }}>{error}</div>}

      {/* Section A : aggregate greeks */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(5, 1fr)",
          gap: 12,
          padding: 12,
          background: "#f4f4f8",
          borderRadius: 8,
          marginBottom: 16,
        }}
      >
        <Stat label="Open positions" value={agg ? String(agg.n_open_positions) : "—"} />
        <Stat label="Σ Vega ($/volpt)" value={fmtNum(agg?.total_vega_usd_per_volpt, 0)} />
        <Stat label="Σ Gamma ($/pip²)" value={fmtNum(agg?.total_gamma_usd_per_pip2, 3)} />
        <Stat label="Σ Theta ($/day)" value={fmtNum(agg?.total_theta_usd_per_day, 0)} />
        <Stat label="Σ Delta (unhedged)" value={fmtNum(agg?.total_delta_unhedged, 3)} />
      </div>

      <div style={{ marginBottom: 12 }}>
        <button
          onClick={() => void triggerCycle()}
          disabled={running}
          style={{ padding: "6px 12px", marginRight: 8 }}
        >
          {running ? "Running…" : "Run monitor cycle now"}
        </button>
        <span style={{ color: "#666", fontSize: 12 }}>
          Background loop runs every 60s. Manual trigger for ad-hoc refresh.
        </span>
      </div>

      {/* Section B : positions table */}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead style={{ background: "#e8e8ec" }}>
          <tr>
            <th style={th}>ID</th>
            <th style={th}>Structure</th>
            <th style={th}>Tenor</th>
            <th style={th}>Expiry</th>
            <th style={th}>Entry signal</th>
            <th style={th}>P&L gross</th>
            <th style={th}>vega P&L</th>
            <th style={th}>gamma P&L</th>
            <th style={th}>theta P&L</th>
            <th style={th}>Δ unhedged</th>
            <th style={th}>State</th>
            <th style={th}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {positions.length === 0 && (
            <tr>
              <td colSpan={12} style={{ padding: 12, textAlign: "center", color: "#999" }}>
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
              <tr
                key={p.id}
                onClick={() => setSelectedId(p.id)}
                style={{
                  borderTop: "1px solid #ddd",
                  cursor: "pointer",
                  background: selectedId === p.id ? "#f9f5e0" : undefined,
                }}
              >
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
                <td style={td}>{fmtNum(p.current_delta_unhedged, 3)}</td>
                <td style={td}>{p.state}</td>
                <td style={td}>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      void closeManual(p.id);
                    }}
                    style={{ fontSize: 11, padding: "2px 6px" }}
                    disabled={p.state !== "open"}
                  >
                    Close
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Section C : detail panel for the selected position */}
      {selectedId !== null && (
        <div
          style={{
            marginTop: 24,
            padding: 12,
            background: "#fafafa",
            borderRadius: 8,
            border: "1px solid #ddd",
          }}
        >
          <h3 style={{ margin: "0 0 8px 0" }}>Detail — position #{selectedId}</h3>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <div>
              <h4 style={{ margin: "8px 0" }}>Exit alerts</h4>
              {alerts.length === 0 && <div style={{ color: "#999" }}>No alerts.</div>}
              {alerts.map((a) => (
                <div
                  key={a.id}
                  style={{
                    padding: "4px 6px",
                    marginBottom: 4,
                    background:
                      a.action_recommended === "EXIT" ? "#fde6e6" : "#fdf6d3",
                    fontSize: 12,
                  }}
                >
                  <b>{a.rule_triggered}</b> → {a.action_recommended} (p={a.priority}) ·{" "}
                  {new Date(a.timestamp).toLocaleString()} ·{" "}
                  {a.execution_status ?? "no action"}
                </div>
              ))}
            </div>
            <div>
              <h4 style={{ margin: "8px 0" }}>MTM history (last {mtmHist.length} pts)</h4>
              <Sparkline points={mtmHist.map((m) => m.pnl_gross_usd)} />
              <div style={{ fontSize: 11, color: "#666", marginTop: 4 }}>
                P&L gross min ={" "}
                {fmtUsd(mtmHist.length ? Math.min(...mtmHist.map((m) => m.pnl_gross_usd)) : null)}
                {" · max = "}
                {fmtUsd(mtmHist.length ? Math.max(...mtmHist.map((m) => m.pnl_gross_usd)) : null)}
              </div>
            </div>
          </div>
        </div>
      )}
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

function Stat({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div>
      <div style={{ fontSize: 11, color: "#666", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

function Sparkline({ points }: { points: number[] }): JSX.Element {
  if (points.length < 2) {
    return <div style={{ fontSize: 12, color: "#999" }}>Need ≥2 points to draw.</div>;
  }
  const w = 320;
  const h = 60;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const xs = (i: number) => (i / (points.length - 1)) * w;
  const ys = (v: number) => h - ((v - min) / range) * h;
  const path = points.map((v, i) => `${i === 0 ? "M" : "L"}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`).join(" ");
  // Zero line
  const zeroY = max < 0 || min > 0 ? null : ys(0);
  return (
    <svg width={w} height={h} style={{ background: "#fff", border: "1px solid #ddd" }}>
      {zeroY !== null && (
        <line x1={0} y1={zeroY} x2={w} y2={zeroY} stroke="#ccc" strokeDasharray="2 2" />
      )}
      <path d={path} stroke="#3060c0" strokeWidth={1.5} fill="none" />
    </svg>
  );
}
