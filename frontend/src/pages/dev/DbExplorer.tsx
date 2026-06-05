/**
 * DB Explorer — interactive, type-aware row reader.
 *
 * Source : ``GET /api/v1/dev/tables`` for the table picker (auto-
 * discovered from ``Base.metadata``) + ``GET /api/v1/dev/tables/{name}``
 * for paged / sorted / filtered rows with column metadata.
 *
 * Features (parity target = DB Schema dev tab) :
 *   - Table picker with type-as-you-search filter ; PK columns +
 *     column count shown inline.
 *   - Column metadata drives type-aware cell rendering :
 *       INTEGER / NUMERIC / FLOAT  → right-aligned, locale-formatted
 *       TIMESTAMP*                 → locale date/time
 *       BOOLEAN                    → green / red pill
 *       JSON / JSONB               → italic, click-to-expand modal
 *       null                       → dim "—"
 *   - Sortable headers (click toggles asc / desc, server-side sort).
 *   - Per-column filter row (toggle, exact match or ``%substr%`` ILIKE).
 *   - Pagination prev / next driven by offset+limit ; total row count
 *     surfaced so the navigator knows where it is.
 *   - Column visibility toggle (a checkbox grid in the toolbar).
 *   - CSV / JSON export of the currently-loaded page.
 *   - Refresh button.
 *
 * Anti-injection : the table name and every column reference go
 * through the backend's validation against ``Base.metadata`` ; filter
 * values are bound as SQL parameters server-side.
 */
import { useEffect, useMemo, useState } from "react";

interface TableMeta {
  name: string;
  n_columns: number;
  pk: string[];
}
interface ColMeta {
  name: string;
  type: string;
  nullable: boolean;
  pk: boolean;
  fk: boolean;
}
interface TableData {
  table: string;
  total: number;
  limit: number;
  offset: number;
  order_by: string;
  order_dir: "asc" | "desc";
  filters: string;
  columns: string[];
  columns_meta: ColMeta[];
  rows: Record<string, unknown>[];
}

const DEFAULT_LIMIT = 50;


export function DbExplorer(): JSX.Element {
  // ── Picker state ──
  const [tables, setTables] = useState<TableMeta[]>([]);
  const [pickerQuery, setPickerQuery] = useState("");
  const [selected, setSelected] = useState<string>("");

  // ── Query state ──
  const [limit, setLimit] = useState<number>(DEFAULT_LIMIT);
  const [offset, setOffset] = useState<number>(0);
  const [orderBy, setOrderBy] = useState<string | null>(null);
  const [orderDir, setOrderDir] = useState<"asc" | "desc">("desc");
  const [filters, setFilters] = useState<Map<string, string>>(new Map());
  const [showFilterRow, setShowFilterRow] = useState(false);

  // ── Render state ──
  const [data, setData] = useState<TableData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [hiddenCols, setHiddenCols] = useState<Set<string>>(new Set());
  const [showColPanel, setShowColPanel] = useState(false);
  const [expandedCell, setExpandedCell] = useState<{
    col: string; value: unknown;
  } | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 2000);
    return () => clearTimeout(id);
  }, [toast]);

  // ── Load table list ──
  useEffect(() => {
    fetch("/api/v1/dev/tables")
      .then((r) => r.json())
      .then((j: { tables: TableMeta[] }) => {
        setTables(j.tables);
        if (j.tables[0]) setSelected(j.tables[0].name);
      })
      .catch((e) => setError(String(e)));
  }, []);

  // ── Filter table picker ──
  const visibleTables = useMemo(() => {
    const q = pickerQuery.trim().toLowerCase();
    if (!q) return tables;
    return tables.filter((t) => t.name.toLowerCase().includes(q));
  }, [tables, pickerQuery]);

  // ── Reset paging / sort / filters when the table changes ──
  useEffect(() => {
    setOffset(0);
    setOrderBy(null);
    setOrderDir("desc");
    setFilters(new Map());
    setHiddenCols(new Set());
    setData(null);
  }, [selected]);

  // ── Fetch rows whenever any query input changes ──
  useEffect(() => {
    if (!selected) return;
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    params.set("offset", String(offset));
    if (orderBy) params.set("order_by", orderBy);
    params.set("order_dir", orderDir);
    const filterStr = Array.from(filters.entries())
      .filter(([_, v]) => v.trim() !== "")
      .map(([k, v]) => `${k}:${v}`)
      .join(",");
    if (filterStr) params.set("filters", filterStr);

    setLoading(true);
    setError(null);
    fetch(`/api/v1/dev/tables/${selected}?${params.toString()}`)
      .then(async (r) => {
        if (!r.ok) {
          const txt = await r.text();
          throw new Error(`HTTP ${r.status} : ${txt.slice(0, 200)}`);
        }
        return r.json() as Promise<TableData>;
      })
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [selected, limit, offset, orderBy, orderDir, filters]);

  const onSortClick = (col: string): void => {
    if (orderBy === col) {
      setOrderDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setOrderBy(col);
      setOrderDir("desc");
    }
    setOffset(0);
  };

  const onFilterChange = (col: string, val: string): void => {
    const next = new Map(filters);
    if (val) next.set(col, val);
    else next.delete(col);
    setFilters(next);
    setOffset(0);
  };

  const toggleColumnVisibility = (col: string): void => {
    const next = new Set(hiddenCols);
    if (next.has(col)) next.delete(col);
    else next.add(col);
    setHiddenCols(next);
  };

  const refresh = (): void => {
    // Force-bump filters Map identity to re-trigger the effect.
    setFilters(new Map(filters));
  };

  // ── Exports of the currently-loaded page ──
  const exportCsv = (): void => {
    if (!data) return;
    const visibleCols = data.columns.filter((c) => !hiddenCols.has(c));
    const escape = (s: string) =>
      `"${s.replace(/"/g, '""').replace(/\r?\n/g, " ")}"`;
    const header = visibleCols.join(",");
    const lines = data.rows.map((r) =>
      visibleCols.map((c) => {
        const v = r[c];
        if (v === null || v === undefined) return "";
        if (typeof v === "object") return escape(JSON.stringify(v));
        return escape(String(v));
      }).join(","),
    );
    const blob = new Blob([[header, ...lines].join("\n")], {
      type: "text/csv;charset=utf-8",
    });
    triggerDownload(blob, `${data.table}.csv`);
    setToast(`✓ Exported ${data.rows.length} rows to CSV`);
  };
  const exportJson = (): void => {
    if (!data) return;
    const visibleCols = data.columns.filter((c) => !hiddenCols.has(c));
    const subset = data.rows.map((r) => {
      const out: Record<string, unknown> = {};
      for (const c of visibleCols) out[c] = r[c];
      return out;
    });
    const blob = new Blob([JSON.stringify(subset, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    triggerDownload(blob, `${data.table}.json`);
    setToast(`✓ Exported ${data.rows.length} rows to JSON`);
  };

  const selectedMeta = tables.find((t) => t.name === selected);
  const total = data?.total ?? 0;
  const start = total === 0 ? 0 : offset + 1;
  const end = data ? Math.min(offset + data.rows.length, total) : 0;
  const canPrev = offset > 0;
  const canNext = data ? offset + data.rows.length < total : false;

  return (
    <div style={{ padding: 12 }}>
      {/* ── Toolbar ── */}
      <div style={{
        display: "flex", gap: 10, alignItems: "center",
        marginBottom: 8, flexWrap: "wrap",
        color: "#aaa", fontSize: 12,
      }}>
        <input
          type="text"
          value={pickerQuery}
          onChange={(e) => setPickerQuery(e.target.value)}
          placeholder="search tables…"
          style={inputStyle("180px")}
        />
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          style={inputStyle("auto")}
        >
          {visibleTables.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name}  ({t.n_columns} cols
              {t.pk.length > 0 ? `, pk: ${t.pk.join(",")}` : ""})
            </option>
          ))}
        </select>
        <span style={{ color: "#666" }}>·</span>
        <label>limit
          <input
            type="number" min={1} max={10000}
            value={limit}
            onChange={(e) => {
              setLimit(Math.max(1, Math.min(10000,
                Number(e.target.value) || 50)));
              setOffset(0);
            }}
            style={{ ...inputStyle("70px"), marginLeft: 4 }}
          />
        </label>
        <button type="button" onClick={refresh} style={btnStyle("ghost")}
                disabled={!selected}>
          {loading ? "…" : "↻ refresh"}
        </button>
        <button type="button" onClick={() => setShowFilterRow((b) => !b)}
                style={btnStyle(showFilterRow ? "active" : "ghost")}>
          ⌕ filter
        </button>
        <button type="button" onClick={() => setShowColPanel((b) => !b)}
                style={btnStyle(showColPanel ? "active" : "ghost")}>
          ⊞ columns ({(data?.columns.length ?? 0) - hiddenCols.size}/{data?.columns.length ?? 0})
        </button>
        <button type="button" onClick={exportCsv} style={btnStyle("ghost")}
                disabled={!data || data.rows.length === 0}>↓ CSV</button>
        <button type="button" onClick={exportJson} style={btnStyle("ghost")}
                disabled={!data || data.rows.length === 0}>↓ JSON</button>
        <label style={{ marginLeft: "auto" }}>
          <input
            type="checkbox"
            checked={showJson}
            onChange={(e) => setShowJson(e.target.checked)}
            style={{ marginRight: 4 }}
          />
          raw JSON view
        </label>
      </div>

      {/* ── Column visibility panel ── */}
      {showColPanel && data && (
        <div style={{
          background: "#1a1a1a", border: "1px solid #333",
          padding: "6px 10px", marginBottom: 8, borderRadius: 3,
          display: "flex", flexWrap: "wrap", gap: 6, fontSize: 11,
        }}>
          {data.columns.map((c) => (
            <label key={c} style={{
              padding: "2px 6px", background: "#0f0f0f",
              border: "1px solid #2a2a2a", borderRadius: 2,
              cursor: "pointer", color: hiddenCols.has(c) ? "#555" : "#ddd",
              fontFamily: "Consolas, monospace",
            }}>
              <input type="checkbox"
                     checked={!hiddenCols.has(c)}
                     onChange={() => toggleColumnVisibility(c)}
                     style={{ marginRight: 4, verticalAlign: "middle" }} />
              {c}
            </label>
          ))}
        </div>
      )}

      {/* ── Status / nav strip ── */}
      <div style={{
        display: "flex", gap: 12, alignItems: "center",
        marginBottom: 6, color: "#999", fontSize: 12,
        fontFamily: "Consolas, monospace",
      }}>
        <span>
          <code style={{ color: "#7af" }}>{selected || "—"}</code>
          {selectedMeta && (
            <span style={{ color: "#666" }}>
              {" · "}{selectedMeta.n_columns} cols
              {selectedMeta.pk.length > 0
                ? ` · PK ${selectedMeta.pk.join(",")}` : ""}
            </span>
          )}
        </span>
        <span style={{ color: "#666" }}>·</span>
        <span>
          rows <b style={{ color: "#ddd" }}>{start}</b>–<b style={{ color: "#ddd" }}>{end}</b>
          {" of "}<b style={{ color: "#7af" }}>{total.toLocaleString()}</b>
          {filters.size > 0 && (
            <span style={{ color: "#ec6" }}>
              {" "}(filtered by {Array.from(filters.entries())
                .map(([k, v]) => `${k}=${v}`).join(", ")})
            </span>
          )}
        </span>
        <span style={{ marginLeft: "auto" }}>
          <button type="button"
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                  disabled={!canPrev || loading}
                  style={btnStyle(canPrev ? "ghost" : "disabled")}>
            ← prev
          </button>
          <button type="button"
                  onClick={() => setOffset(offset + limit)}
                  disabled={!canNext || loading}
                  style={{ ...btnStyle(canNext ? "ghost" : "disabled"), marginLeft: 4 }}>
            next →
          </button>
        </span>
      </div>

      {error && (
        <div style={{
          color: "#fbb", padding: "8px 12px", marginBottom: 8,
          background: "#3a1a1a", border: "1px solid #5a2a2a",
          borderRadius: 3, fontFamily: "Consolas, monospace", fontSize: 12,
        }}>{error}</div>
      )}

      {/* ── Data view ── */}
      {data && (
        showJson ? (
          <pre style={preStyle}>{JSON.stringify(data.rows, null, 2)}</pre>
        ) : (
          <DataTable
            data={data}
            hiddenCols={hiddenCols}
            orderBy={orderBy ?? data.order_by}
            orderDir={orderDir}
            onSortClick={onSortClick}
            showFilterRow={showFilterRow}
            filters={filters}
            onFilterChange={onFilterChange}
            onCellExpand={(col, value) => setExpandedCell({ col, value })}
          />
        )
      )}

      {/* ── Cell-expand modal ── */}
      {expandedCell && (
        <div
          onClick={() => setExpandedCell(null)}
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)",
            display: "flex", alignItems: "center", justifyContent: "center",
            zIndex: 999,
          }}
        >
          <div onClick={(e) => e.stopPropagation()}
               style={{
                 maxWidth: "80vw", maxHeight: "80vh", overflow: "auto",
                 background: "#0e0e0e", border: "1px solid #444",
                 borderRadius: 4, padding: 16, color: "#ddd",
               }}>
            <div style={{ color: "#7af", fontSize: 12, marginBottom: 6,
                          fontFamily: "Consolas, monospace" }}>
              {selected}.{expandedCell.col}
              <button type="button"
                      onClick={() => setExpandedCell(null)}
                      style={{
                        float: "right", padding: "2px 8px", fontSize: 11,
                        background: "transparent", color: "#888",
                        border: "1px solid #333", borderRadius: 3, cursor: "pointer",
                      }}>close ×</button>
            </div>
            <pre style={{
              margin: 0, padding: 12, background: "#000", color: "#cdc",
              fontSize: 12, fontFamily: "Consolas, monospace",
              whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}>
              {typeof expandedCell.value === "object"
                ? JSON.stringify(expandedCell.value, null, 2)
                : String(expandedCell.value)}
            </pre>
          </div>
        </div>
      )}

      {/* ── Toast ── */}
      {toast && (
        <div style={{
          position: "fixed", bottom: 24, right: 24,
          padding: "10px 16px", borderRadius: 4,
          background: toast.startsWith("✓") ? "#1a3a1a" : "#3a1a1a",
          color: toast.startsWith("✓") ? "#bfb" : "#fbb",
          border: `1px solid ${toast.startsWith("✓") ? "#2a5a2a" : "#5a2a2a"}`,
          fontSize: 12, fontFamily: "Consolas, monospace",
          boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
          zIndex: 1000, pointerEvents: "none",
        }}>
          {toast}
        </div>
      )}
    </div>
  );
}


// ── Type families used for cell rendering / alignment ──
type TypeFamily = "int" | "decimal" | "float" | "text" | "bool" | "json"
                 | "timestamp" | "date" | "uuid" | "other";

function typeFamily(sqlType: string): TypeFamily {
  const t = sqlType.toUpperCase();
  if (/\b(INTEGER|BIGINT|SMALLINT|INT4|INT8|INT2|SERIAL|BIGSERIAL)\b/.test(t)) return "int";
  if (/\bNUMERIC\b|\bDECIMAL\b/.test(t)) return "decimal";
  if (/\b(FLOAT|REAL|DOUBLE)\b/.test(t)) return "float";
  if (/\bBOOL/.test(t)) return "bool";
  if (/\bJSON/.test(t)) return "json";
  if (/\bTIMESTAMP\b|\bTIMESTAMPTZ\b/.test(t)) return "timestamp";
  if (/\bDATE\b/.test(t) && !/TIME/.test(t)) return "date";
  if (/\bUUID\b/.test(t)) return "uuid";
  if (/\bVARCHAR\b|\bTEXT\b|\bCHAR\b/.test(t)) return "text";
  return "other";
}

function isNumeric(fam: TypeFamily): boolean {
  return fam === "int" || fam === "decimal" || fam === "float";
}


function DataTable({
  data, hiddenCols, orderBy, orderDir, onSortClick,
  showFilterRow, filters, onFilterChange, onCellExpand,
}: {
  data: TableData;
  hiddenCols: Set<string>;
  orderBy: string;
  orderDir: "asc" | "desc";
  onSortClick: (col: string) => void;
  showFilterRow: boolean;
  filters: Map<string, string>;
  onFilterChange: (col: string, val: string) => void;
  onCellExpand: (col: string, value: unknown) => void;
}): JSX.Element {
  const visibleCols = data.columns.filter((c) => !hiddenCols.has(c));
  const metaByName = new Map(data.columns_meta.map((m) => [m.name, m]));

  return (
    <div style={{
      overflow: "auto",
      maxHeight: "calc(100vh - 250px)",
      border: "1px solid #333", borderRadius: 3,
    }}>
      <table style={{
        borderCollapse: "collapse", width: "100%", fontSize: 12,
        fontFamily: "Consolas, monospace",
      }}>
        <thead>
          <tr style={{ background: "#1a1f2a", color: "#9bf",
                       position: "sticky", top: 0, zIndex: 2 }}>
            {visibleCols.map((c) => {
              const m = metaByName.get(c);
              const isSorted = orderBy === c;
              const fam = m ? typeFamily(m.type) : "other";
              return (
                <th key={c}
                    onClick={() => onSortClick(c)}
                    title={m
                      ? `${c} : ${m.type}${m.nullable ? " (nullable)" : " NOT NULL"}`
                      + (m.pk ? " · PK" : "") + (m.fk ? " · FK" : "")
                      : c}
                    style={{
                      padding: "6px 10px",
                      textAlign: isNumeric(fam) ? "right" : "left",
                      borderRight: "1px solid #2a3040",
                      cursor: "pointer", userSelect: "none",
                      whiteSpace: "nowrap",
                    }}>
                  {m?.pk && <span style={{ color: "#fc6", marginRight: 4 }}>★</span>}
                  {m?.fk && <span style={{ color: "#7af", marginRight: 4 }}>▸</span>}
                  {c}
                  <span style={{ color: "#888", fontWeight: 400,
                                 marginLeft: 4, fontSize: 10 }}>
                    {m?.type}
                  </span>
                  {isSorted && (
                    <span style={{ marginLeft: 6, color: "#ec6" }}>
                      {orderDir === "asc" ? "▲" : "▼"}
                    </span>
                  )}
                </th>
              );
            })}
          </tr>
          {showFilterRow && (
            <tr style={{ background: "#101418", position: "sticky", top: 32, zIndex: 1 }}>
              {visibleCols.map((c) => (
                <th key={c} style={{ padding: "3px 6px",
                                     borderRight: "1px solid #222" }}>
                  <input
                    type="text"
                    value={filters.get(c) ?? ""}
                    onChange={(e) => onFilterChange(c, e.target.value)}
                    placeholder="search…"
                    title={
                      "Substring match (case-insensitive).\n"
                      + "• 'foo'     → contains 'foo' anywhere\n"
                      + "• 'foo%bar' → wildcards passed through\n"
                      + "• '=foo'    → exact case-sensitive match"
                    }
                    style={{
                      width: "100%", padding: "2px 6px", fontSize: 11,
                      background: "#0a0a0a", color: "#ddd",
                      border: "1px solid #333", borderRadius: 2,
                      fontFamily: "Consolas, monospace",
                    }}
                  />
                </th>
              ))}
            </tr>
          )}
        </thead>
        <tbody>
          {data.rows.map((row, i) => (
            <tr key={i}
                style={{ borderTop: "1px solid #1a1a1a",
                         background: i % 2 === 0 ? "transparent" : "#0d0d0d" }}>
              {visibleCols.map((c) => {
                const m = metaByName.get(c);
                const fam = m ? typeFamily(m.type) : "other";
                return (
                  <td key={c}
                      style={{
                        padding: "4px 10px",
                        borderRight: "1px solid #1a1a1a",
                        textAlign: isNumeric(fam) ? "right" : "left",
                        verticalAlign: "top",
                        whiteSpace: "nowrap",
                        maxWidth: 400, overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                      onClick={() => {
                        const v = row[c];
                        if (typeof v === "object" && v !== null) {
                          onCellExpand(c, v);
                        } else if (typeof v === "string" && v.length > 80) {
                          onCellExpand(c, v);
                        }
                      }}>
                    <Cell value={row[c]} fam={fam} />
                  </td>
                );
              })}
            </tr>
          ))}
          {data.rows.length === 0 && (
            <tr>
              <td colSpan={visibleCols.length}
                  style={{ padding: 16, color: "#666", textAlign: "center" }}>
                no rows
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}


function Cell({ value, fam }: {
  value: unknown; fam: TypeFamily;
}): JSX.Element {
  if (value === null || value === undefined) {
    return <span style={{ color: "#555", fontStyle: "italic" }}>—</span>;
  }
  if (fam === "bool") {
    const b = Boolean(value);
    return (
      <span style={{
        padding: "1px 7px", borderRadius: 2, fontSize: 10, fontWeight: 700,
        background: b ? "#1a3a1a" : "#3a1a1a",
        color: b ? "#bfb" : "#fbb",
      }}>{b ? "TRUE" : "FALSE"}</span>
    );
  }
  if (fam === "int") {
    return <span style={{ color: "#dde" }}>{Number(value).toLocaleString()}</span>;
  }
  if (fam === "decimal" || fam === "float") {
    const n = Number(value);
    if (Number.isNaN(n)) return <span>{String(value)}</span>;
    return <span style={{ color: "#dde" }}>
      {n.toLocaleString(undefined, { maximumFractionDigits: 8 })}
    </span>;
  }
  if (fam === "timestamp") {
    try {
      const d = new Date(String(value));
      if (!Number.isNaN(d.getTime())) {
        return (
          <span style={{ color: "#ad7" }}>
            {d.toISOString().replace("T", " ").slice(0, 19)}
            <span style={{ color: "#666" }}> UTC</span>
          </span>
        );
      }
    } catch { /* fallthrough */ }
    return <span>{String(value)}</span>;
  }
  if (fam === "date") {
    return <span style={{ color: "#ad7" }}>{String(value).slice(0, 10)}</span>;
  }
  if (fam === "json" || (typeof value === "object" && value !== null)) {
    return (
      <span style={{ color: "#9af", fontStyle: "italic", cursor: "pointer" }}
            title="click to expand">
        {`{${Object.keys(value as Record<string, unknown>).length} keys}`}
      </span>
    );
  }
  if (fam === "uuid") {
    const s = String(value);
    return (
      <span style={{ color: "#aaa" }} title={s}>
        {s.length > 12 ? s.slice(0, 8) + "…" : s}
      </span>
    );
  }
  const s = String(value);
  return <span>{s.length > 80 ? s.slice(0, 80) + "…" : s}</span>;
}


function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}


// ── Style helpers ──
function inputStyle(width: string): React.CSSProperties {
  return {
    background: "#1a1a1a", color: "#ddd",
    border: "1px solid #333", borderRadius: 3,
    padding: "3px 8px", fontSize: 12, width,
    fontFamily: "Consolas, monospace",
  };
}

function btnStyle(variant: "ghost" | "active" | "disabled"): React.CSSProperties {
  const base: React.CSSProperties = {
    padding: "3px 10px", fontSize: 11, borderRadius: 3,
    border: "1px solid #333", cursor: "pointer",
    fontFamily: "Consolas, monospace",
  };
  if (variant === "active") {
    return { ...base, background: "#2a4a6a", color: "#fff", borderColor: "#3a5a7a" };
  }
  if (variant === "disabled") {
    return { ...base, background: "transparent", color: "#444",
             cursor: "not-allowed", borderColor: "#222" };
  }
  return { ...base, background: "#1a2a3a", color: "#9bf" };
}

const preStyle: React.CSSProperties = {
  margin: 0, padding: 12, background: "#000", color: "#cdc",
  fontSize: 11, overflow: "auto", maxHeight: "70vh",
  fontFamily: "Consolas, monospace",
};
