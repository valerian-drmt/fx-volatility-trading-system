/**
 * Live adapter (R11 PR 2r.2): backend versioned trading-config → the voldesk
 * Settings view, per the HYBRID decision (2026-06-16):
 *   - version-history table  ← GET /admin/config/history (list of ConfigResponse)
 *   - current-config viewer  ← GET /admin/config (nested VolTradingConfig)
 *
 * The backend config is a nested, versioned object; the viewer flattens it into
 * section → "dotted.key = value" rows for read-only display. Editing/commit/
 * revert is write-gated (Phase 2 / PR 2w) — this adapter is read-only.
 */
import type { ConfigData, ConfigField, ConfigSection, ConfigVersionRow } from "../deskData";

interface ConfigRecord {
  version?: number;
  config?: Record<string, unknown>;
  updated_at?: string | null;
  updated_by?: string | null;
  comment?: string | null;
}

/** Recursively flatten a nested config object into dotted-key scalar fields. */
function flatten(obj: Record<string, unknown>, prefix = ""): ConfigField[] {
  const out: ConfigField[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flatten(v as Record<string, unknown>, key));
    } else {
      out.push({ key, value: Array.isArray(v) ? JSON.stringify(v) : String(v) });
    }
  }
  return out;
}

/** Nested config → sections (one per top-level key; scalars → "general"). */
export function adaptConfigCurrent(raw: unknown): { version: number; sections: ConfigSection[] } {
  const rec = (raw ?? {}) as ConfigRecord;
  const cfg = rec.config ?? {};
  const sections: ConfigSection[] = [];
  const general: ConfigField[] = [];
  for (const [name, val] of Object.entries(cfg)) {
    if (val !== null && typeof val === "object" && !Array.isArray(val)) {
      sections.push({ name, fields: flatten(val as Record<string, unknown>) });
    } else {
      general.push({ key: name, value: Array.isArray(val) ? JSON.stringify(val) : String(val) });
    }
  }
  if (general.length) sections.unshift({ name: "general", fields: general });
  return { version: rec.version ?? 0, sections };
}

/** History payload (newest-first list of ConfigResponse) → table rows. */
export function adaptConfigHistory(raw: unknown): ConfigVersionRow[] {
  const rows = Array.isArray(raw) ? (raw as ConfigRecord[]) : [];
  return rows.map((r) => ({
    version: r.version ?? 0,
    by: r.updated_by ?? "—",
    comment: r.comment ?? "",
    at: r.updated_at ?? null,
  }));
}

export function adaptConfig(currentRaw: unknown, historyRaw: unknown): ConfigData {
  const { version, sections } = adaptConfigCurrent(currentRaw);
  return { currentVersion: version, sections, history: adaptConfigHistory(historyRaw) };
}
