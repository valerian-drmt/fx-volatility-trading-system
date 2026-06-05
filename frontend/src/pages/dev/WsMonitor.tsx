/**
 * WS Monitor — per-channel real-time tap with search, rate, JSON path
 * watch, diff highlighting, and export.
 *
 * Beats the browser's DevTools → Network → WS for this stack because :
 *   - 3 channels side by side, each with its own buffer + filter
 *   - Domain-aware pretty-printing (structlog JSON, vol surface, …)
 *   - Regex filter so a noisy ticks stream stays scannable
 *   - JSON path watch : pick ``spot.bid`` and chart its live trajectory
 *     in a tiny sparkline — the cheapest "is the engine emitting sane
 *     values?" check
 *   - Diff highlight : on a slow channel (vol every 3 min), see which
 *     keys changed between consecutive emissions
 *   - Export the current buffer to CSV / NDJSON for offline inspection
 *
 * If a panel stays empty > 5 s on ticks or risk, the pipeline is
 * usually broken — cf. smoke notebooks under ``scripts/smoke/``.
 */
import { useMemo, useState } from "react";

import { useWsLog, type WsStatus } from "../../hooks/useWsLog";

const WS_BASE = (import.meta.env["VITE_WS_BASE_URL"] as string | undefined) ?? "";

const CHANNELS = [
  { key: "ticks", path: "/ws/ticks", label: "📡 ticks", expected: "~1 msg/s" },
  { key: "vol",   path: "/ws/vol",   label: "🌊 vol",   expected: "1 msg/180s" },
  { key: "risk",  path: "/ws/risk",  label: "📊 risk",  expected: "~1 msg/2s" },
] as const;


export function WsMonitor(): JSX.Element {
  return (
    <div style={{
      padding: 12, display: "grid",
      gridTemplateColumns: "1fr 1fr 1fr", gap: 12,
    }}>
      {CHANNELS.map((c) => (
        <ChannelPanel key={c.key} path={c.path}
                      label={c.label} expected={c.expected} />
      ))}
    </div>
  );
}


function ChannelPanel({
  path, label, expected,
}: { path: string; label: string; expected: string }): JSX.Element {
  const url = `${WS_BASE}${path}`;
  const { status, count, messages, paused, rate,
          pause, resume, clear } = useWsLog(url, 200);

  // ── Per-panel filter state ──
  const [search, setSearch] = useState("");
  const [jsonPath, setJsonPath] = useState("");

  // ── Apply regex filter to messages. ──
  const { filtered, regexError } = useMemo(() => {
    if (!search.trim()) return { filtered: messages, regexError: null };
    let re: RegExp | null = null;
    try { re = new RegExp(search, "i"); }
    catch (e) { return { filtered: [] as typeof messages,
                          regexError: String(e) }; }
    return {
      filtered: messages.filter((m) => re!.test(m.raw)),
      regexError: null,
    };
  }, [messages, search]);

  // ── Extract numeric values at jsonPath from each message (newest first)
  //    so we can render a sparkline of the value over time. ──
  const trace = useMemo(() => {
    if (!jsonPath.trim()) return [];
    const out: number[] = [];
    for (const m of messages) {
      try {
        const obj = JSON.parse(m.raw);
        const v = pickPath(obj, jsonPath);
        if (typeof v === "number" && Number.isFinite(v)) out.push(v);
      } catch { /* skip non-JSON */ }
    }
    // Reverse so the sparkline reads left-to-right = oldest-to-newest.
    return out.slice().reverse();
  }, [messages, jsonPath]);

  // ── Build a key-level diff between consecutive JSON messages so we
  //    can highlight what changed (useful on slow channels). ──
  const diffKeys = useMemo(() => {
    const out: Set<string>[] = [];
    for (let i = 0; i < messages.length; i++) {
      const cur = tryParse(messages[i]!.raw);
      const prev = i + 1 < messages.length ? tryParse(messages[i + 1]!.raw) : null;
      out.push(diffKeySet(cur, prev));
    }
    return out;
  }, [messages]);

  const exportNdjson = (): void => {
    const text = messages
      .map((m) => `{"ts":"${m.ts}","raw":${JSON.stringify(m.raw)}}`)
      .join("\n");
    downloadBlob(text, `${path.slice(1).replace("/", "-")}.ndjson`,
                 "application/x-ndjson");
  };
  const exportCsv = (): void => {
    const head = "ts,raw";
    const rows = messages.map((m) => {
      const escaped = `"${m.raw.replace(/"/g, '""').replace(/\r?\n/g, " ")}"`;
      return `${m.ts},${escaped}`;
    });
    downloadBlob([head, ...rows].join("\n"),
                 `${path.slice(1).replace("/", "-")}.csv`, "text/csv");
  };

  return (
    <section style={{
      background: "#0a0a0a",
      border: "1px solid #222",
      borderRadius: 4,
      overflow: "hidden",
      display: "flex", flexDirection: "column",
    }}>
      {/* Header */}
      <header style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "5px 10px",
        background: "#1a1a1a", borderBottom: "1px solid #2a2a2a",
        fontSize: 12,
      }}>
        <span style={{ color: "#9bf", fontWeight: 700 }}>{label}</span>
        <span style={{ color: "#666", fontSize: 10 }}>{expected}</span>
        <StatusDot status={status} />
        <span style={{ color: "#aaa", marginLeft: "auto",
                       fontFamily: "Consolas, monospace" }}>
          <b style={{ color: "#7af" }}>{rate.toFixed(1)}</b><span style={{ color: "#666" }}>/s</span>
          {" · "}{count}
        </span>
      </header>

      {/* Controls */}
      <div style={{ padding: "6px 10px", borderBottom: "1px solid #1a1a1a",
                    display: "flex", gap: 6, flexWrap: "wrap" }}>
        {paused
          ? <button onClick={resume} style={btn("active")}>▶ resume</button>
          : <button onClick={pause}  style={btn("ghost")}>⏸ pause</button>}
        <button onClick={clear} style={btn("ghost")}>clear</button>
        <button onClick={exportCsv} disabled={messages.length === 0}
                style={btn("ghost")}>↓ CSV</button>
        <button onClick={exportNdjson} disabled={messages.length === 0}
                style={btn("ghost")}>↓ NDJSON</button>
        <span style={{ color: "#666", fontSize: 10, alignSelf: "center",
                       marginLeft: "auto" }}>
          {filtered.length}/{messages.length}
        </span>
      </div>

      {/* Filters row */}
      <div style={{ padding: "6px 10px", borderBottom: "1px solid #1a1a1a",
                    display: "flex", gap: 6, flexWrap: "wrap" }}>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="regex filter…"
          title="Case-insensitive regex applied to raw payload"
          style={{ ...input(), flex: 1, minWidth: 100 }}
        />
        <input
          type="text"
          value={jsonPath}
          onChange={(e) => setJsonPath(e.target.value)}
          placeholder="JSON path (e.g. spot.bid)"
          title={
            "Dot-path into the parsed JSON, charted as a sparkline\n"
            + "Examples : spot.bid, surface.atm, greeks.delta_usd"
          }
          style={{ ...input(), flex: 1, minWidth: 100 }}
        />
      </div>

      {/* Sparkline when a JSON path is being watched */}
      {jsonPath.trim() && (
        <div style={{
          padding: "4px 10px", borderBottom: "1px solid #1a1a1a",
          background: "#0d0d0d", fontSize: 10,
          color: "#666", fontFamily: "Consolas, monospace",
        }}>
          {trace.length === 0
            ? <span>no numeric values at <code>{jsonPath}</code> yet</span>
            : <Sparkline values={trace} path={jsonPath} />}
        </div>
      )}

      {/* Body */}
      {regexError && (
        <div style={{ padding: "4px 10px", color: "#fbb", fontSize: 10,
                      background: "#3a1a1a" }}>
          regex : {regexError}
        </div>
      )}
      <div style={{
        background: "#000", color: "#cdc",
        fontSize: 11, fontFamily: "Consolas, monospace",
        padding: 6, flex: 1, overflow: "auto",
        height: "60vh",
      }}>
        {filtered.length === 0 ? (
          <div style={{ color: "#666" }}>
            {messages.length === 0 ? "(no messages yet)" : "(no matches)"}
          </div>
        ) : (
          filtered.map((m, i) => {
            // Find the index of this message in the unfiltered messages
            // list so the diff highlight stays consistent.
            const origIdx = messages.indexOf(m);
            const changed = origIdx >= 0 ? diffKeys[origIdx] : undefined;
            return (
              <MessageRow key={`${m.ts}-${i}`} ts={m.ts} raw={m.raw}
                          changedKeys={changed} />
            );
          })
        )}
      </div>
    </section>
  );
}


function MessageRow({
  ts, raw, changedKeys,
}: {
  ts: string;
  raw: string;
  changedKeys: Set<string> | undefined;
}): JSX.Element {
  const parsed = tryParse(raw);
  return (
    <div style={{ marginBottom: 6, paddingBottom: 4,
                  borderBottom: "1px solid #0e0e0e" }}>
      <div style={{ color: "#666", fontSize: 10 }}>
        {ts.replace("T", " ").slice(11, 19)}
      </div>
      {parsed ? (
        <PrettyJson value={parsed} changedKeys={changedKeys} />
      ) : (
        <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all",
                      color: "#cdc" }}>{raw}</pre>
      )}
    </div>
  );
}


/** Pretty-print a JSON object inline. Keys present in ``changedKeys``
 *  are highlighted (orange dot prefix) so the eye finds them on a
 *  slow channel. */
function PrettyJson({
  value, changedKeys,
}: {
  value: unknown; changedKeys: Set<string> | undefined;
}): JSX.Element {
  if (value === null || typeof value !== "object") {
    return <span>{JSON.stringify(value)}</span>;
  }
  const obj = value as Record<string, unknown>;
  return (
    <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
      {"{\n"}
      {Object.entries(obj).map(([k, v], i, arr) => (
        <span key={k}>
          {"  "}
          {changedKeys?.has(k) && (
            <span style={{ color: "#fc6" }} title="changed since previous message">●</span>
          )}
          <span style={{ color: changedKeys?.has(k) ? "#fc6" : "#9bf" }}>
            "{k}"
          </span>
          {": "}
          <span style={{ color: "#cdc" }}>
            {typeof v === "object" ? JSON.stringify(v) : JSON.stringify(v)}
          </span>
          {i < arr.length - 1 ? "," : ""}
          {"\n"}
        </span>
      ))}
      {"}"}
    </pre>
  );
}


/** Inline SVG sparkline rendering up to 80 points. Auto-rescales. */
function Sparkline({ values, path }: { values: number[]; path: string }): JSX.Element {
  const W = 220, H = 28;
  const pts = values.slice(-80);
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const range = max - min || 1;
  const dx = pts.length > 1 ? W / (pts.length - 1) : W;
  const d = pts
    .map((v, i) => {
      const x = i * dx;
      const y = H - ((v - min) / range) * (H - 4) - 2;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  const last = pts[pts.length - 1]!;
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
      <span style={{ color: "#888" }}>{path}</span>
      <svg width={W} height={H} style={{ background: "#000", borderRadius: 2 }}>
        <path d={d} stroke="#7af" strokeWidth={1.2} fill="none" />
      </svg>
      <span style={{ color: "#9bf" }}>
        {last.toLocaleString(undefined, { maximumFractionDigits: 6 })}
      </span>
      <span style={{ color: "#666" }}>
        n={pts.length} · min {min.toFixed(4)} · max {max.toFixed(4)}
      </span>
    </span>
  );
}


/** Extract a dot-path from a parsed object. Returns ``undefined`` if
 *  any segment doesn't exist or isn't an object. */
function pickPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj;
  for (const seg of path.split(".")) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[seg];
  }
  return cur;
}

function tryParse(raw: string): Record<string, unknown> | null {
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" ? v as Record<string, unknown> : null;
  } catch { return null; }
}

/** Returns the set of top-level keys whose JSON-serialized value
 *  differs between ``cur`` and ``prev``. Empty set if ``prev`` is
 *  null (first message — nothing to compare against). */
function diffKeySet(
  cur: Record<string, unknown> | null,
  prev: Record<string, unknown> | null,
): Set<string> {
  const out = new Set<string>();
  if (!cur || !prev) return out;
  for (const k of Object.keys(cur)) {
    if (JSON.stringify(cur[k]) !== JSON.stringify(prev[k])) out.add(k);
  }
  return out;
}

function downloadBlob(content: string, filename: string, mime: string): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
}


function StatusDot({ status }: { status: WsStatus }): JSX.Element {
  const color = status === "open" ? "#6c6"
              : status === "connecting" ? "#cc6" : "#e66";
  return (
    <span style={{ display: "inline-flex", alignItems: "center",
                   gap: 4, fontSize: 10, color }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%",
                     background: color }} />
      {status}
    </span>
  );
}


function input(): React.CSSProperties {
  return {
    background: "#0a0a0a", color: "#ddd",
    border: "1px solid #2a2a2a", borderRadius: 2,
    padding: "2px 6px", fontSize: 11,
    fontFamily: "Consolas, monospace",
  };
}
function btn(variant: "ghost" | "active"): React.CSSProperties {
  const base: React.CSSProperties = {
    padding: "2px 8px", fontSize: 10, borderRadius: 2,
    border: "1px solid #2a3040", cursor: "pointer",
    fontFamily: "Consolas, monospace",
  };
  if (variant === "active") return { ...base, background: "#2a4a6a", color: "#fff" };
  return { ...base, background: "#1a2a3a", color: "#9bf" };
}
