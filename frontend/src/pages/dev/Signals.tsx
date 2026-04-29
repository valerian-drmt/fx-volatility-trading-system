/**
 * Signals — explore la table `signals` via /api/v1/signals avec filtres
 * réalistes (tenor, signal_type, underlying, since, limit, latest_per_tenor).
 *
 * Endpoint backend déjà mergé en R4 PR #28 (analytics router).
 *
 * Layout 2-col : filtres à gauche, table à droite.
 */
import { useEffect, useState } from "react";

const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"] as const;
const SIGNAL_TYPES = ["CHEAP", "FAIR", "EXPENSIVE"] as const;

interface SignalRow {
  id: number;
  timestamp: string;
  underlying: string;
  tenor: string;
  dte: number;
  sigma_mid: number;
  sigma_fair: number;
  ecart: number;
  signal_type: string;
  rv: number | null;
}

interface Filters {
  underlying: string;
  tenor: string;
  signal_type: string;
  limit: number;
  latest_per_tenor: boolean;
}

const DEFAULT_FILTERS: Filters = {
  underlying: "EURUSD",
  tenor: "",
  signal_type: "",
  limit: 100,
  latest_per_tenor: false,
};

export function Signals(): JSX.Element {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [rows, setRows] = useState<SignalRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const set = <K extends keyof Filters>(k: K, v: Filters[K]) =>
    setFilters((f) => ({ ...f, [k]: v }));

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    setRows([]);
    const params = new URLSearchParams();
    if (filters.underlying) params.set("underlying", filters.underlying);
    if (filters.tenor) params.set("tenor", filters.tenor);
    if (filters.signal_type) params.set("signal_type", filters.signal_type);
    params.set("limit", String(filters.limit));
    if (filters.latest_per_tenor) params.set("latest_per_tenor", "true");
    try {
      const r = await fetch(`/api/v1/signals?${params.toString()}`);
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status}: ${txt.slice(0, 200)}`);
      }
      setRows(await r.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Agrégat rapide pour le récap
  const counts = rows.reduce<Record<string, number>>((acc, r) => {
    acc[r.signal_type] = (acc[r.signal_type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 250px) minmax(0, 1fr)", gap: 16 }}>
        {/* Left : filters */}
        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>Filters</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            <Row label="Underlying">
              <input value={filters.underlying} onChange={(e) => set("underlying", e.target.value.toUpperCase())} style={inputStyle} />
            </Row>
            <Row label="Tenor">
              <select value={filters.tenor} onChange={(e) => set("tenor", e.target.value)} style={inputStyle}>
                <option value="">(any)</option>
                {TENORS.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Row>
            <Row label="Signal type">
              <select value={filters.signal_type} onChange={(e) => set("signal_type", e.target.value)} style={inputStyle}>
                <option value="">(any)</option>
                {SIGNAL_TYPES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </Row>
            <Row label="Limit">
              <input
                type="number"
                min={1}
                max={2000}
                value={filters.limit}
                onChange={(e) => set("limit", Math.max(1, Math.min(2000, parseInt(e.target.value || "100", 10))))}
                style={inputStyle}
              />
            </Row>
            <Row label="Latest per tenor">
              <input
                type="checkbox"
                checked={filters.latest_per_tenor}
                onChange={(e) => set("latest_per_tenor", e.target.checked)}
              />
            </Row>
            <button onClick={fetchData} disabled={loading} style={{ ...btnStyle, marginTop: 12, width: "100%" }}>
              {loading ? "…" : "Run ▶"}
            </button>

            {/* Récap counts par signal_type */}
            {rows.length > 0 && (
              <div style={{ marginTop: 16, fontSize: 12, color: "#aaa" }}>
                <div style={{ marginBottom: 4, color: "#7af" }}>Distribution</div>
                {SIGNAL_TYPES.map((s) => (
                  <div key={s} style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>{s}</span>
                    <span style={{ color: counts[s] ? "#fff" : "#666" }}>{counts[s] ?? 0}</span>
                  </div>
                ))}
                <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px solid #333", display: "flex", justifyContent: "space-between" }}>
                  <strong>Total</strong>
                  <strong style={{ color: "#fff" }}>{rows.length}</strong>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Right : table */}
        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>GET /api/v1/signals — {rows.length} rows</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            {error && <div style={{ color: "#e66", marginBottom: 12 }}>{error}</div>}
            {!error && rows.length === 0 && <div style={{ color: "#666", fontSize: 12 }}>(no rows — adjust filters above)</div>}
            {rows.length > 0 && (
              <div style={{ overflow: "auto", maxHeight: "70vh", border: "1px solid #333" }}>
                <table style={tableStyle}>
                  <thead>
                    <tr style={{ background: "#1a1a1a", color: "#888", textAlign: "left" }}>
                      <th style={th}>Timestamp</th>
                      <th style={th}>Symbol</th>
                      <th style={th}>Tenor</th>
                      <th style={th}>DTE</th>
                      <th style={th}>σ mid</th>
                      <th style={th}>σ fair</th>
                      <th style={th}>écart</th>
                      <th style={th}>RV</th>
                      <th style={th}>Signal</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id} style={{ borderTop: "1px solid #222" }}>
                        <td style={td}>{new Date(r.timestamp).toLocaleString()}</td>
                        <td style={td}>{r.underlying}</td>
                        <td style={td}>{r.tenor}</td>
                        <td style={td}>{r.dte}</td>
                        <td style={td}>{Number(r.sigma_mid).toFixed(3)}</td>
                        <td style={td}>{Number(r.sigma_fair).toFixed(3)}</td>
                        <td style={{ ...td, color: Number(r.ecart) > 0 ? "#cc6" : "#6c6" }}>
                          {Number(r.ecart).toFixed(3)}
                        </td>
                        <td style={td}>{r.rv != null ? Number(r.rv).toFixed(3) : "—"}</td>
                        <td style={{ ...td, color: signalColor(r.signal_type), fontWeight: 600 }}>
                          {r.signal_type}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function signalColor(s: string): string {
  if (s === "CHEAP") return "#6c6";
  if (s === "EXPENSIVE") return "#e66";
  return "#aaa";
}

function Row({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0", gap: 8 }}>
      <span style={{ color: "#aaa", fontSize: 13 }}>{label}</span>
      <div style={{ flex: 1, maxWidth: 130 }}>{children}</div>
    </div>
  );
}

const inputStyle = {
  background: "#1a1a1a",
  color: "#ddd",
  border: "1px solid #333",
  borderRadius: 3,
  padding: "4px 8px",
  fontSize: 13,
  width: "100%",
  boxSizing: "border-box" as const,
};
const btnStyle = {
  padding: "6px 12px",
  background: "#2a4a6a",
  color: "#fff",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 13,
};
const tableStyle = { borderCollapse: "collapse" as const, fontSize: 12, fontFamily: "Consolas, monospace", width: "100%" };
const th = { padding: "4px 12px", textAlign: "left" as const, color: "#888", borderBottom: "1px solid #333" };
const td = { padding: "3px 12px", borderBottom: "1px solid #222", whiteSpace: "nowrap" as const };
