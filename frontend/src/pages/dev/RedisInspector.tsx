/**
 * Redis Inspector — shows the whitelist of Redis keys with TTL + age;
 * clicking a row → raw JSON of the value. No auto polling, manual
 * refresh. Backend: GET /api/v1/dev/redis/keys + /api/v1/dev/redis/value.
 *
 * Exports 3 surfaces:
 *   - useRedisInspector()  : hook with shared state (keys, selected, value)
 *   - RedisKeysPanel       : just the table (uses the hook or an instance)
 *   - RedisValuePanel      : just the selected value
 *   - RedisInspector       : all-in-one wrapper (table on top, value below)
 *
 * The 2 panels can be placed in separate grid cells (cf. StackCombined.tsx)
 * by sharing the same hook instance at the parent level.
 */
import { useEffect, useState } from "react";
import { apiFetch } from "../../api/client";

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

export interface RedisInspectorState {
  keys: KeyInfo[];
  selected: string | null;
  value: ValueResp | null;
  error: string | null;
  loading: boolean;
  fetchKeys: () => void;
  fetchValue: (k: string) => void;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useRedisInspector(): RedisInspectorState {
  const [keys, setKeys] = useState<KeyInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [value, setValue] = useState<ValueResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchKeys = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch("/api/v1/dev/redis/keys");
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
      const r = await apiFetch(`/api/v1/dev/redis/value?key=${encodeURIComponent(key)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setValue(await r.json());
    } catch (e) {
      setError(String(e));
    }
  };

  // Auto-refresh 3s: re-fetch keys + re-fetch value (if one is selected).
  useEffect(() => {
    void fetchKeys();
    const id = window.setInterval(() => {
      void fetchKeys();
      if (selected) void fetchValue(selected);
    }, 3_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  return { keys, selected, value, error, loading, fetchKeys, fetchValue };
}


export function RedisKeysPanel({ state }: { state: RedisInspectorState }): JSX.Element {
  const { keys, selected, fetchValue } = state;
  return (
    <section className="panel" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <header className="panel-header">
        <h2>Redis keys</h2>
        <span style={{ color: "#666", fontSize: 11 }}>auto 3s</span>
      </header>
      <div className="panel-body" style={{ padding: 0, overflow: "auto", flex: 1 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
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
              const status = !k.exists ? "✗" : "✓";
              const statusColor = !k.exists ? "#e66" : "#6c6";
              return (
                <tr
                  key={k.key}
                  onClick={() => void fetchValue(k.key)}
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
  );
}


export function RedisValuePanel({ state }: { state: RedisInspectorState }): JSX.Element {
  const { selected, value, error } = state;
  return (
    <section className="panel" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <header className="panel-header">
        <h2>Value: {selected ?? "(pick a key)"}</h2>
      </header>
      <div className="panel-body" style={{ padding: 12, overflow: "auto", flex: 1 }}>
        {error && <div style={{ color: "#e66", marginBottom: 8 }}>{error}</div>}
        {value === null && !error && <div style={{ color: "#888" }}>Click a row to see its value.</div>}
        {value && (
          <pre style={preStyle}>{value.is_json ? JSON.stringify(value.value, null, 2) : value.raw}</pre>
        )}
      </div>
    </section>
  );
}


export function RedisInspector(): JSX.Element {
  const state = useRedisInspector();
  return (
    <div style={{ display: "grid", gridTemplateRows: "auto 1fr", gap: 12, padding: 12, height: "100%" }}>
      <RedisKeysPanel state={state} />
      <RedisValuePanel state={state} />
    </div>
  );
}

const cellStyle = { padding: "5px 10px", verticalAlign: "top" as const };
const preStyle = {
  margin: 0, padding: 10, background: "#000", color: "#cdc", fontSize: 12,
  overflow: "auto" as const, whiteSpace: "pre-wrap" as const,
  wordBreak: "break-all" as const,
};
