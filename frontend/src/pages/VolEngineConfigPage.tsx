/**
 * Vol Engine Configs page — flat key/value editor over the live `vol_engine_config`
 * row. Reads from GET /api/v1/admin/config, PUTs a deep-merge patch back via
 * PUT /api/v1/admin/config so the api lifespan diff-detects the change and
 * publishes ``config:changed`` on Redis (engines hot-reload).
 *
 * Layout :
 *   - Header (top)
 *   - Title : "Vol Engine Configs"
 *   - 2-col table : flat "section.path" → editable value
 *   - Hover the label → tooltip with type + default + range from the JSON schema
 *   - Save button (bottom) → PUT
 */
import { useEffect, useMemo, useState } from "react";
import { Header } from "../components/layout/Header";

interface ConfigResponse {
  version: number;
  config: Record<string, unknown>;
  updated_at: string;
  updated_by: string | null;
  comment: string | null;
}

type Primitive = string | number | boolean | null;
type FlatValue = { path: string[]; value: Primitive; isArray: boolean };

// Walk the config tree and emit a flat list of leaf paths. Arrays are
// serialised as JSON for editing — the user gets a single string they can
// edit and the parser re-parses on save (see ``unflatten``).
function flatten(obj: unknown, prefix: string[] = []): FlatValue[] {
  if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
    const isArray = Array.isArray(obj);
    const value: Primitive = isArray
      ? JSON.stringify(obj)
      : (obj as Primitive);
    return [{ path: prefix, value, isArray }];
  }
  const out: FlatValue[] = [];
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    out.push(...flatten(v, [...prefix, k]));
  }
  return out;
}

// Inverse of flatten — rebuild a nested object from edited flat values.
// Array-typed leaves are re-parsed from their JSON-string form so Pydantic
// validators get the proper ``list``/``tuple`` shape they expect.
function unflatten(rows: FlatValue[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const { path, value, isArray } of rows) {
    let cursor: Record<string, unknown> = out;
    for (let i = 0; i < path.length - 1; i++) {
      const key = path[i] as string;
      const next = cursor[key];
      if (typeof next !== "object" || next === null) cursor[key] = {};
      cursor = cursor[key] as Record<string, unknown>;
    }
    const leaf = path[path.length - 1] as string;
    if (isArray && typeof value === "string") {
      try {
        cursor[leaf] = JSON.parse(value);
      } catch {
        // Leave the raw string in place ; the API will return a typed
        // 422 that the user can read in the error banner.
        cursor[leaf] = value;
      }
    } else {
      cursor[leaf] = value;
    }
  }
  return out;
}

// Pretty type label for the tooltip — looked up in the JSON schema by
// walking the same path. Falls back to typeof when the schema lacks a node.
function describePath(
  path: string[],
  schema: Record<string, unknown> | null,
  liveValue: Primitive,
): string {
  if (schema) {
    const node = walkSchema(path, schema);
    if (node) {
      const parts: string[] = [];
      if (node.type) parts.push(`type: ${node.type}`);
      if (node.enum) parts.push(`one of: ${(node.enum as unknown[]).join(", ")}`);
      if (node.minimum !== undefined) parts.push(`min: ${node.minimum}`);
      if (node.maximum !== undefined) parts.push(`max: ${node.maximum}`);
      if (node.default !== undefined) parts.push(`default: ${JSON.stringify(node.default)}`);
      if (node.description) parts.push(node.description);
      if (parts.length) return parts.join(" · ");
    }
  }
  return `current: ${JSON.stringify(liveValue)}`;
}

interface SchemaNode {
  type?: string;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  default?: unknown;
  description?: string;
}

function walkSchema(
  path: string[], schema: Record<string, unknown>,
): SchemaNode | null {
  let node: Record<string, unknown> = schema;
  for (const key of path) {
    const props = (node["properties"] as Record<string, unknown> | undefined) ?? null;
    if (!props || !(key in props)) return null;
    node = props[key] as Record<string, unknown>;
    // Resolve $ref — the api emits Pydantic schemas with $defs.
    const ref = node["$ref"] as string | undefined;
    if (ref && ref.startsWith("#/$defs/") && schema["$defs"]) {
      const defs = schema["$defs"] as Record<string, unknown>;
      const target = defs[ref.slice("#/$defs/".length)];
      if (target && typeof target === "object") node = target as Record<string, unknown>;
    }
  }
  return node as SchemaNode;
}

export function VolEngineConfigPage(): JSX.Element {
  const [data, setData] = useState<ConfigResponse | null>(null);
  const [schema, setSchema] = useState<Record<string, unknown> | null>(null);
  const [edits, setEdits] = useState<Record<string, Primitive>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<{ version: number } | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [r1, r2] = await Promise.all([
          fetch("/api/v1/admin/config"),
          fetch("/api/v1/admin/config/schema"),
        ]);
        if (!r1.ok) throw new Error(`config fetch ${r1.status}`);
        setData(await r1.json());
        if (r2.ok) setSchema(await r2.json());
      } catch (e) {
        setError(String(e));
      }
    })();
  }, []);

  const flat = useMemo<FlatValue[]>(() => {
    if (!data) return [];
    return flatten(data.config);
  }, [data]);

  const handleChange = (key: string, raw: string, original: Primitive) => {
    let v: Primitive = raw;
    if (typeof original === "number") {
      const n = Number(raw);
      v = Number.isFinite(n) ? n : raw;
    } else if (typeof original === "boolean") {
      v = raw === "true";
    } else if (original === null) {
      v = raw === "" ? null : raw;
    }
    setEdits((prev) => ({ ...prev, [key]: v }));
  };

  const save = async () => {
    if (!data) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      // Build the patched config from edits. We send the FULL config tree
      // — the api endpoint deep-merges. For flat dotted edits we have to
      // unflatten the modified rows back into nested shape.
      const merged: FlatValue[] = flat.map((row) => {
        const key = row.path.join(".");
        return key in edits
          ? { path: row.path, value: edits[key] as Primitive, isArray: row.isArray }
          : row;
      });
      const patch = unflatten(merged);
      const r = await fetch("/api/v1/admin/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patch, user: "ui", comment: "edited via /config" }),
      });
      if (!r.ok) throw new Error(`PUT ${r.status} : ${await r.text()}`);
      const body = (await r.json()) as ConfigResponse;
      setData(body);
      setEdits({});
      setSaved({ version: body.version });
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <Header />
      <main style={{ flex: 1, overflow: "auto", background: "#0e0e0e", color: "#ddd", padding: 24 }}>
        <h2 style={{ margin: 0, color: "#7af", letterSpacing: 1, fontSize: 18 }}>
          Vol Engine Configs
        </h2>

        {error && (
          <div style={{ color: "#e66", marginTop: 12, fontSize: 12, fontFamily: "Consolas, monospace" }}>
            {error}
          </div>
        )}
        {saved && (
          <div style={{ color: "#6c6", marginTop: 12, fontSize: 12, fontFamily: "Consolas, monospace" }}>
            ✓ saved version {saved.version}
          </div>
        )}

        {!data ? (
          <div style={{ color: "#666", marginTop: 24 }}>(loading config…)</div>
        ) : (
          <>
            <div style={{
              marginTop: 18,
              background: "#0a0a0a", border: "1px solid #222", borderRadius: 4,
              maxWidth: 900,
            }}>
              <table style={{
                width: "100%", borderCollapse: "collapse",
                fontFamily: "Consolas, monospace", fontSize: 12,
              }}>
                <thead>
                  <tr style={{ background: "#1a1a1a" }}>
                    <th style={th}>name</th>
                    <th style={th}>value</th>
                  </tr>
                </thead>
                <tbody>
                  {flat.map((row) => {
                    const key = row.path.join(".");
                    const tooltip = describePath(row.path, schema, row.value);
                    const edited = key in edits;
                    const display = edited ? edits[key] : row.value;
                    return (
                      <tr key={key} style={{ borderBottom: "1px solid #1a1a1a" }}>
                        <td title={tooltip} style={{
                          padding: "6px 12px",
                          color: edited ? "#fc6" : "#aaa",
                          cursor: "help",
                        }}>
                          {key}
                        </td>
                        <td style={{ padding: "4px 12px" }}>
                          <input
                            type={typeof row.value === "number" ? "number" : "text"}
                            step="any"
                            value={display === null ? "" : String(display)}
                            onChange={(e) => handleChange(key, e.target.value, row.value)}
                            style={{
                              width: "100%", maxWidth: 380, padding: "3px 6px",
                              background: "#0e0e0e", color: "#ddd",
                              border: edited ? "1px solid #fc6" : "1px solid #333",
                              borderRadius: 3, fontFamily: "inherit", fontSize: 12,
                            }}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div style={{ marginTop: 18, display: "flex", gap: 12, alignItems: "center" }}>
              <button
                type="button"
                onClick={save}
                disabled={saving || Object.keys(edits).length === 0}
                style={{
                  padding: "6px 18px",
                  background: Object.keys(edits).length === 0 ? "#333" : "#2a6a4a",
                  color: "#fff", border: "none", borderRadius: 3,
                  fontSize: 13, fontWeight: 600,
                  cursor: Object.keys(edits).length === 0 ? "default" : "pointer",
                }}
              >
                {saving ? "saving…" : "Save"}
              </button>
              <span style={{ color: "#666", fontSize: 11 }}>
                {Object.keys(edits).length} edited field{Object.keys(edits).length === 1 ? "" : "s"}
              </span>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

const th: React.CSSProperties = {
  padding: "8px 12px", textAlign: "left",
  color: "#7af", fontWeight: 600, fontSize: 11,
  letterSpacing: 1, textTransform: "uppercase",
  borderBottom: "1px solid #333",
};
