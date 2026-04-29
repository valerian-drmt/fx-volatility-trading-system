/**
 * Redis Inspector — affiche la whitelist des clés Redis avec TTL + age,
 * click sur une row → JSON brut de la valeur. Pas de polling auto, refresh
 * manuel. Backend : GET /api/v1/dev/redis/keys + /api/v1/dev/redis/value.
 */
import { useEffect, useState } from "react";

interface KeyInfo {
  key: string;
  exists: boolean;
  ttl: number | null;
  age_s: number | null;
}

interface ValueResp {
  key: string;
  value: unknown;
  raw: string;
  is_json: boolean;
}

export function RedisInspector(): JSX.Element {
  const [keys, setKeys] = useState<KeyInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [value, setValue] = useState<ValueResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchKeys = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/v1/dev/redis/keys");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setKeys(j.keys);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const fetchValue = async (key: string) => {
    setSelected(key);
    setValue(null);
    setError(null);
    try {
      const r = await fetch(`/api/v1/dev/redis/value?key=${encodeURIComponent(key)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setValue(await r.json());
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    fetchKeys();
  }, []);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, padding: 16 }}>
      <section className="panel">
        <header className="panel-header">
          <h2>Redis keys</h2>
          <button onClick={fetchKeys} disabled={loading} style={btnStyle}>
            {loading ? "..." : "Refresh"}
          </button>
        </header>
        <div className="panel-body" style={{ padding: 0 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "#999", borderBottom: "1px solid #333" }}>
                <th style={cellStyle}>Key</th>
                <th style={cellStyle}>TTL (s)</th>
                <th style={cellStyle}>Age (s)</th>
                <th style={cellStyle}>Status</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => {
                const isActive = k.key === selected;
                const status = !k.exists ? "✗ MISSING" : k.age_s === null ? "—" : "✓";
                const statusColor = !k.exists ? "#e66" : "#6c6";
                return (
                  <tr
                    key={k.key}
                    onClick={() => fetchValue(k.key)}
                    style={{
                      cursor: "pointer",
                      background: isActive ? "#2a4a6a" : "transparent",
                      borderBottom: "1px solid #222",
                    }}
                  >
                    <td style={cellStyle}>{k.key}</td>
                    <td style={cellStyle}>{k.ttl ?? "—"}</td>
                    <td style={cellStyle}>{k.age_s ?? "—"}</td>
                    <td style={{ ...cellStyle, color: statusColor }}>{status}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>Value: {selected ?? "(pick a key)"}</h2>
        </header>
        <div className="panel-body" style={{ padding: 12 }}>
          {error && <div style={{ color: "#e66", marginBottom: 8 }}>{error}</div>}
          {value === null && !error && <div style={{ color: "#888" }}>Click une row à gauche pour voir la valeur.</div>}
          {value && (
            <pre
              style={{
                margin: 0,
                padding: 12,
                background: "#000",
                color: "#cdc",
                fontSize: 12,
                overflow: "auto",
                maxHeight: "70vh",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {value.is_json ? JSON.stringify(value.value, null, 2) : value.raw}
            </pre>
          )}
        </div>
      </section>
    </div>
  );
}

const cellStyle = { padding: "6px 12px", verticalAlign: "top" as const };
const btnStyle = {
  padding: "4px 12px",
  background: "#2a4a6a",
  color: "#fff",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 13,
};
