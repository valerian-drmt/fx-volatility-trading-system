/**
 * DB Explorer — pick a table, fetch N rows, display in a table.
 * Backend : GET /api/v1/dev/tables (list) + /api/v1/dev/tables/{name}.
 *
 * Pas de filtres dans cette itération — juste table + limit + Run.
 * Toggle "Show JSON" pour voir le payload brut au lieu du tableau formaté.
 */
import { useEffect, useState } from "react";

interface TableData {
  table: string;
  total: number;
  limit: number;
  offset: number;
  columns: string[];
  rows: Record<string, unknown>[];
}

const DEFAULT_LIMIT = 50;

export function DbExplorer(): JSX.Element {
  const [tables, setTables] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [limit, setLimit] = useState<number>(DEFAULT_LIMIT);
  const [data, setData] = useState<TableData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);

  // Au mount : récupère la liste des tables disponibles
  useEffect(() => {
    fetch("/api/v1/dev/tables")
      .then((r) => r.json())
      .then((j) => {
        setTables(j.tables);
        setSelected(j.tables[0] ?? "");
      })
      .catch((e) => setError(String(e)));
  }, []);

  const runQuery = async () => {
    if (!selected) return;
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const r = await fetch(`/api/v1/dev/tables/${selected}?limit=${limit}`);
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status} : ${txt.slice(0, 200)}`);
      }
      setData(await r.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <label style={{ color: "#aaa", fontSize: 13 }}>
          Table:{" "}
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            style={inputStyle}
          >
            {tables.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label style={{ color: "#aaa", fontSize: 13 }}>
          Limit:{" "}
          <input
            type="number"
            min={1}
            max={1000}
            value={limit}
            onChange={(e) => setLimit(Math.max(1, Math.min(1000, Number(e.target.value) || 50)))}
            style={{ ...inputStyle, width: 80 }}
          />
        </label>
        <button onClick={runQuery} disabled={loading || !selected} style={btnStyle}>
          {loading ? "…" : "Run ▶"}
        </button>
        <label style={{ color: "#aaa", fontSize: 13, marginLeft: "auto" }}>
          <input
            type="checkbox"
            checked={showJson}
            onChange={(e) => setShowJson(e.target.checked)}
            style={{ marginRight: 6 }}
          />
          Show JSON
        </label>
      </div>

      {error && <div style={{ color: "#e66", marginBottom: 12 }}>{error}</div>}

      {data && (
        <div>
          <div style={{ color: "#aaa", fontSize: 12, marginBottom: 6 }}>
            {data.rows.length} rows / {data.total} total — table <code>{data.table}</code> — {data.columns.length} cols
          </div>

          {showJson ? (
            <pre style={preStyle}>{JSON.stringify(data.rows, null, 2)}</pre>
          ) : (
            <div style={{ overflow: "auto", maxHeight: "70vh", border: "1px solid #333" }}>
              <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
                <thead>
                  <tr style={{ background: "#1a1a1a", color: "#888", textAlign: "left" }}>
                    {data.columns.map((c) => (
                      <th key={c} style={cellStyle}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row, i) => (
                    <tr key={i} style={{ borderTop: "1px solid #222" }}>
                      {data.columns.map((c) => (
                        <td key={c} style={cellStyle} title={String(formatCell(row[c]))}>
                          {truncate(formatCell(row[c]), 80)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

const inputStyle = {
  background: "#1a1a1a",
  color: "#ddd",
  border: "1px solid #333",
  borderRadius: 3,
  padding: "3px 8px",
  fontSize: 13,
  marginLeft: 4,
};

const btnStyle = {
  padding: "4px 12px",
  background: "#2a4a6a",
  color: "#fff",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 13,
};

const cellStyle = {
  padding: "4px 8px",
  verticalAlign: "top" as const,
  borderRight: "1px solid #222",
  whiteSpace: "nowrap" as const,
  fontFamily: "Consolas, monospace",
};

const preStyle = {
  margin: 0,
  padding: 12,
  background: "#000",
  color: "#cdc",
  fontSize: 11,
  overflow: "auto",
  maxHeight: "70vh",
};
