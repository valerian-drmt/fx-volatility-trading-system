/**
 * Logs — search-as-you-type tail for the Loki-backed log store.
 *
 * Backend  : GET /api/v1/dev/logs/containers (label values)
 *            GET /api/v1/dev/logs/query    (query_range proxy)
 *
 * Why this exists vs Grafana Explore : Grafana is 5-10 s of friction
 * (open, pick datasource, write LogQL, set time range, run, adjust)
 * for any one-off check. This panel answers "is there an error in
 * vol-engine right now?" with 2 clicks and zero LogQL knowledge.
 *
 * Features :
 *   - Container dropdown (auto-populated from Loki's ``container``
 *     label values).
 *   - Level dropdown (ERROR / WARNING / INFO / DEBUG / any), matched
 *     against structlog's JSON ``"level"`` field.
 *   - Free-text regex search (case-insensitive by default, prepend
 *     ``(?-i)`` to make it case-sensitive).
 *   - Time range : last 5 min / 15 min / 1h / 6h / 24h.
 *   - Tail mode : auto-refresh every 3 s while on, so you can watch a
 *     log live without a manual refresh loop.
 *   - Per-line pretty-print : if the message is JSON (structlog),
 *     expand to a tree on click. Level field gets a coloured badge.
 *   - Export the current result set as .log.
 */
import { useCallback, useEffect, useRef, useState } from "react";

interface LogEntry {
  ts: string;                    // ISO
  container: string;
  message: string;
  labels: Record<string, string>;
}
interface LogsResponse {
  entries: LogEntry[];
  total: number;
  query: string;
  minutes: number;
}

const LEVELS = ["any", "ERROR", "WARNING", "INFO", "DEBUG"] as const;
type LevelFilter = typeof LEVELS[number];

const RANGES: Array<{ label: string; minutes: number }> = [
  { label: "5 min",  minutes: 5 },
  { label: "15 min", minutes: 15 },
  { label: "1 h",    minutes: 60 },
  { label: "6 h",    minutes: 360 },
  { label: "24 h",   minutes: 1440 },
];

const LEVEL_COLOR: Record<string, string> = {
  ERROR:   "#fbb",
  WARNING: "#fc6",
  INFO:    "#9bf",
  DEBUG:   "#888",
};


export function Logs(): JSX.Element {
  const [containers, setContainers] = useState<string[]>([]);
  const [container, setContainer] = useState<string>("");
  const [level, setLevel] = useState<LevelFilter>("any");
  const [pattern, setPattern] = useState<string>("");
  const [minutes, setMinutes] = useState<number>(15);
  const [limit, setLimit] = useState<number>(200);
  const [tail, setTail] = useState<boolean>(false);

  const [data, setData] = useState<LogsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [toast, setToast] = useState<string | null>(null);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 1800);
    return () => clearTimeout(id);
  }, [toast]);

  // ── Load container list once ──
  useEffect(() => {
    fetch("/api/v1/dev/logs/containers")
      .then((r) => r.json())
      .then((j: { containers: string[] }) => setContainers(j.containers))
      .catch((e) => setError(String(e)));
  }, []);

  // ── Fetch logs ──
  const runQuery = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const p = new URLSearchParams();
      if (container) p.set("container", container);
      if (level !== "any") p.set("level", level);
      if (pattern.trim()) p.set("pattern", pattern.trim());
      p.set("minutes", String(minutes));
      p.set("limit", String(limit));
      const r = await fetch(`/api/v1/dev/logs/query?${p.toString()}`);
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status} : ${txt.slice(0, 200)}`);
      }
      setData(await r.json());
    } catch (e) { setError(String(e)); }
    finally { setLoading(false); }
  }, [container, level, pattern, minutes, limit]);

  // Run on input change (debounced for pattern).
  const debounceRef = useRef<number | null>(null);
  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => { void runQuery(); }, 250);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [runQuery]);

  // Tail mode : poll every 3 s while ON. Reuse the same query.
  useEffect(() => {
    if (!tail) return;
    const id = window.setInterval(() => { void runQuery(); }, 3000);
    return () => window.clearInterval(id);
  }, [tail, runQuery]);

  const toggleExpand = (i: number): void => {
    const next = new Set(expanded);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    setExpanded(next);
  };

  const exportLog = (): void => {
    if (!data) return;
    const text = data.entries
      .map((e) => `${e.ts}  ${e.container}  ${e.message}`)
      .join("\n");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `logs-${container || "all"}-${Date.now()}.log`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setToast(`✓ Exported ${data.entries.length} lines`);
  };

  return (
    <div style={{ padding: 12 }}>
      {/* ── Toolbar ── */}
      <div style={{
        display: "flex", gap: 8, alignItems: "center",
        marginBottom: 8, flexWrap: "wrap",
        color: "#aaa", fontSize: 12,
        fontFamily: "Consolas, monospace",
      }}>
        <select value={container}
                onChange={(e) => setContainer(e.target.value)}
                style={inputStyle("auto")}
                title="Loki container label">
          <option value="">all containers ({containers.length})</option>
          {containers.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select value={level}
                onChange={(e) => setLevel(e.target.value as LevelFilter)}
                style={inputStyle("auto")}
                title="structlog level field">
          {LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
        <input
          type="text"
          value={pattern}
          onChange={(e) => setPattern(e.target.value)}
          placeholder="regex pattern…"
          title={
            "Free-text regex applied as LogQL |~.\n"
            + "Case-insensitive by default ; embed (?-i) for case-sensitive.\n"
            + "Examples : 'connection', 'error.*timeout', '(?i)\\\\bIB_USERID\\\\b'"
          }
          style={inputStyle("280px")}
        />
        <select value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
                style={inputStyle("auto")}>
          {RANGES.map((r) => (
            <option key={r.minutes} value={r.minutes}>last {r.label}</option>
          ))}
        </select>
        <label>limit
          <input type="number" min={1} max={5000} value={limit}
                 onChange={(e) => setLimit(Math.max(1, Math.min(5000,
                   Number(e.target.value) || 200)))}
                 style={{ ...inputStyle("70px"), marginLeft: 4 }} />
        </label>
        <button type="button" onClick={runQuery}
                disabled={loading}
                style={btnStyle("ghost")}>
          {loading ? "…" : "↻ refresh"}
        </button>
        <label style={{
          padding: "3px 10px", borderRadius: 3,
          border: "1px solid #333",
          background: tail ? "#3a1a1a" : "#1a2a3a",
          color: tail ? "#fbb" : "#9bf",
          cursor: "pointer", userSelect: "none",
        }}>
          <input type="checkbox" checked={tail}
                 onChange={(e) => setTail(e.target.checked)}
                 style={{ marginRight: 4, verticalAlign: "middle" }} />
          {tail ? "● tail" : "tail"}
        </label>
        <button type="button" onClick={exportLog}
                disabled={!data || data.entries.length === 0}
                style={btnStyle("ghost")}>↓ .log</button>
      </div>

      {/* ── Status / query echo ── */}
      <div style={{
        color: "#666", fontSize: 11,
        fontFamily: "Consolas, monospace",
        marginBottom: 6,
      }}>
        {data && (
          <>
            <b style={{ color: "#7af" }}>{data.entries.length}</b> lines
            {" "}· last <b>{data.minutes}</b> min
            {" "}· query <code style={{
              background: "#1a1a1a", padding: "1px 6px", borderRadius: 2,
              color: "#cdd",
            }}>{data.query}</code>
          </>
        )}
      </div>

      {error && (
        <div style={{
          color: "#fbb", padding: "8px 12px", marginBottom: 8,
          background: "#3a1a1a", border: "1px solid #5a2a2a",
          borderRadius: 3, fontFamily: "Consolas, monospace", fontSize: 12,
        }}>
          {error}
        </div>
      )}

      {/* ── Log lines ── */}
      <div style={{
        height: "calc(100vh - 220px)", minHeight: 500,
        overflow: "auto",
        background: "#000", border: "1px solid #222", borderRadius: 3,
        fontFamily: "Consolas, monospace", fontSize: 11,
      }}>
        {data && data.entries.length === 0 && (
          <div style={{ padding: 16, color: "#666", textAlign: "center" }}>
            no matching lines in the last {data.minutes} min
          </div>
        )}
        {data?.entries.map((entry, i) => (
          <LogLine key={i}
                   entry={entry}
                   expanded={expanded.has(i)}
                   onToggle={() => toggleExpand(i)} />
        ))}
      </div>

      {toast && (
        <div style={{
          position: "fixed", bottom: 24, right: 24,
          padding: "10px 16px", borderRadius: 4,
          background: "#1a3a1a", color: "#bfb",
          border: "1px solid #2a5a2a",
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


function LogLine({
  entry, expanded, onToggle,
}: {
  entry: LogEntry;
  expanded: boolean;
  onToggle: () => void;
}): JSX.Element {
  // Try to parse the message as structlog JSON for level highlighting.
  let parsed: Record<string, unknown> | null = null;
  let level: string | null = null;
  try {
    const j = JSON.parse(entry.message);
    if (j && typeof j === "object") {
      parsed = j as Record<string, unknown>;
      const lvl = parsed["level"];
      if (typeof lvl === "string") level = lvl.toUpperCase();
    }
  } catch { /* not JSON */ }

  const lvlColor = level ? (LEVEL_COLOR[level] ?? "#ddd") : "#ccc";
  const tsShort = entry.ts.replace("T", " ").slice(0, 19);

  // Build a compact single-line preview from the parsed JSON :
  // ``LEVEL  event  key=val key=val`` — much more readable than the raw JSON.
  const preview = parsed
    ? buildStructlogPreview(parsed)
    : entry.message;

  return (
    <div style={{
      padding: "3px 10px",
      borderBottom: "1px solid #0a0a0a",
      cursor: "pointer",
      whiteSpace: "pre",
      overflow: "hidden",
      textOverflow: "ellipsis",
    }}
         onClick={onToggle}>
      <span style={{ color: "#666" }}>{tsShort}</span>
      {"  "}
      <span style={{ color: "#7af", display: "inline-block",
                     minWidth: 110 }}>{entry.container}</span>
      {"  "}
      {level && (
        <span style={{
          color: lvlColor, fontWeight: 700, display: "inline-block",
          minWidth: 56,
        }}>{level}</span>
      )}
      <span style={{ color: "#ddd" }}>{preview}</span>
      {expanded && parsed && (
        <pre style={{
          margin: "6px 0 0 130px",
          padding: 8,
          background: "#0a0a0a",
          border: "1px solid #1a1a1a",
          color: "#cdc", fontSize: 11,
          whiteSpace: "pre-wrap", wordBreak: "break-word",
          fontFamily: "Consolas, monospace",
        }}>
{JSON.stringify(parsed, null, 2)}
        </pre>
      )}
    </div>
  );
}


/** Compact one-line preview of a structlog JSON record :
 *      <event>  key=val  key=val
 *  (skips ``level`` since we already render it as a badge, and the
 *  noisy infra keys like ``timestamp`` / ``logger`` which add clutter
 *  for an at-a-glance scan). */
function buildStructlogPreview(p: Record<string, unknown>): string {
  const skip = new Set(["level", "logger", "timestamp", "ts"]);
  const event = String(p["event"] ?? p["msg"] ?? p["message"] ?? "");
  const kv: string[] = [];
  for (const [k, v] of Object.entries(p)) {
    if (skip.has(k)) continue;
    if (k === "event" || k === "msg" || k === "message") continue;
    if (v === null || v === undefined) continue;
    if (typeof v === "object") {
      kv.push(`${k}=${JSON.stringify(v)}`);
    } else {
      kv.push(`${k}=${String(v)}`);
    }
  }
  return event + (kv.length ? "  " + kv.join("  ") : "");
}


function inputStyle(width: string): React.CSSProperties {
  return {
    background: "#1a1a1a", color: "#ddd",
    border: "1px solid #333", borderRadius: 3,
    padding: "3px 8px", fontSize: 12, width,
    fontFamily: "Consolas, monospace",
  };
}

function btnStyle(variant: "ghost" | "active"): React.CSSProperties {
  const base: React.CSSProperties = {
    padding: "3px 10px", fontSize: 11, borderRadius: 3,
    border: "1px solid #333", cursor: "pointer",
    fontFamily: "Consolas, monospace",
  };
  if (variant === "active") {
    return { ...base, background: "#2a4a6a", color: "#fff" };
  }
  return { ...base, background: "#1a2a3a", color: "#9bf" };
}
