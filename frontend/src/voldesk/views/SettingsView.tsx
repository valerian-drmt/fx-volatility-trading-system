/**
 * VOLDESK — Settings (versioned, append-only trading config).
 *
 * HYBRID read model (R11 PR 2r.2, decision 2026-06-16):
 *   - version-history table  ← /admin/config/history
 *   - current-config viewer  ← /admin/config (nested → flat section rows)
 * Read-only : revert/commit are gated behind WRITE_ENABLED (auth, Phase 2/2w).
 */
import { useState } from "react";
import { fetchDomainSettings, putConfig, putDomainSettings, revertConfig } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { Panel } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { useDeskData } from "../data/deskData";
import { WRITE_ENABLED } from "../data/writeEnabled";
import { useAuthStore } from "../../store/authStore";

interface SettingParam { name: string; value: number; default: number; unit: string; description: string; isDefault: boolean; }
interface DomainSettings { title: string; params: SettingParam[]; }

function adaptDomainSettings(raw: unknown): DomainSettings {
  const o = (raw ?? {}) as {
    title?: string;
    params?: { name?: string; value?: number; default?: number; unit?: string; description?: string; is_default?: boolean }[];
  };
  return {
    title: o.title ?? "Settings",
    params: (o.params ?? []).map((p) => ({
      name: p.name ?? "",
      value: Number(p.value ?? 0),
      default: Number(p.default ?? 0),
      unit: p.unit ?? "",
      description: p.description ?? "",
      isDefault: !!p.is_default,
    })),
  };
}

/** Editable policy knobs for one desk domain (config_scalar, hot-applied). */
function DomainSettingsPanel({ domain }: { domain: string }): JSX.Element {
  // Editable as soon as you're logged in (the PUT is cookie-gated server-side).
  const canEdit = useAuthStore((s) => s.authenticated);
  const live = useFetch<DomainSettings>(() => fetchDomainSettings(domain).then(adaptDomainSettings), 600_000);
  const params = live.data?.params ?? [];
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const dirty = Object.keys(edits).length;
  const onCommit = async (): Promise<void> => {
    if (busy || !canEdit || dirty === 0) return;
    setBusy(true);
    const updates: Record<string, number> = {};
    for (const [k, v] of Object.entries(edits)) {
      const num = Number(v);
      if (!Number.isNaN(num)) updates[k] = num;
    }
    try {
      await putDomainSettings(domain, updates);
      window.location.reload();
    } catch {
      setBusy(false);
    }
  };
  return (
    <Panel title={live.data?.title ?? domain} right={<FreshBadge fresh={live} label="hot-applied" />} className="config-edit settings-domain">
      <div className="cfg-sections">
        <div className="cfg-section">
          {params.map((p) => (
            <div key={p.name} className="cfg-field" title={p.description}>
              <span className="cfg-key mono dim">{p.name}{p.unit && <span className="dim"> · {p.unit}</span>}</span>
              {canEdit ? (
                <input
                  className="cfg-val-input mono"
                  type="number"
                  step="any"
                  value={edits[p.name] ?? String(p.value)}
                  disabled={busy}
                  onChange={(e) =>
                    setEdits((prev) => {
                      const next = { ...prev };
                      if (Number(e.target.value) === p.value) delete next[p.name];
                      else next[p.name] = e.target.value;
                      return next;
                    })
                  }
                />
              ) : (
                <span className="cfg-val mono accent">{p.value}{p.isDefault && <span className="dim small"> · default</span>}</span>
              )}
            </div>
          ))}
          {params.length === 0 && <div className="dim small">unavailable.</div>}
        </div>
      </div>
      {canEdit ? (
        <div className="cfg-commit-bar">
          <button className="btn-primary" disabled={busy || dirty === 0} onClick={onCommit}>
            {busy ? "Saving…" : dirty > 0 ? `Save ${dirty} change${dirty > 1 ? "s" : ""}` : "Save"}
          </button>
          {dirty > 0 && <button className="row-close" disabled={busy} onClick={() => setEdits({})}>reset</button>}
        </div>
      ) : null}
      <div className="dim small" style={{ marginTop: 10 }}>
        {canEdit
          ? "Per-field editing · Save upserts config_scalar, applied live by the consuming endpoints."
          : "Read-only · log in (top-right button) to edit these values."}
      </div>
    </Panel>
  );
}

const GATE_TITLE = "writes disabled — auth required (Phase 2)";
const SEP = "␟"; // edits-map key separator: `${section}${SEP}${dotted.field.key}`

/** "1.5"→1.5, "true/false"→bool, else string. Best-effort; Pydantic re-validates server-side. */
function coerce(s: string): unknown {
  if (s === "true") return true;
  if (s === "false") return false;
  if (s.trim() !== "" && !Number.isNaN(Number(s))) return Number(s);
  return s;
}

/** Set a (possibly nested, dotted) path on a plain object, creating objects en route. */
function setDeep(obj: Record<string, unknown>, path: string[], value: unknown): void {
  let cur = obj;
  path.forEach((key, i) => {
    if (i === path.length - 1) {
      cur[key] = value;
      return;
    }
    if (typeof cur[key] !== "object" || cur[key] === null) cur[key] = {};
    cur = cur[key] as Record<string, unknown>;
  });
}

export function SettingsView(): JSX.Element {
  const { config } = useDeskData();
  const data = config.data;
  const [busy, setBusy] = useState(false);
  // Revert = the first write (Phase 2 / 2w): duplicates a past version as the new
  // head + hot-reloads the engine. Gated by WRITE_ENABLED (auth in prod, free local).
  const onRevert = async (version: number): Promise<void> => {
    if (busy || !WRITE_ENABLED) return;
    setBusy(true);
    try {
      await revertConfig(version, `revert to v${version} (desk)`);
      window.location.reload();
    } catch {
      setBusy(false);
    }
  };
  // Per-field edits, keyed `${section}${SEP}${dotted.field.key}`. Committed as a
  // single deep-merged patch (PUT /admin/config) → new version + hot-reload.
  const [edits, setEdits] = useState<Record<string, string>>({});
  const dirty = Object.keys(edits).length;
  const onCommit = async (): Promise<void> => {
    if (busy || !WRITE_ENABLED || dirty === 0) return;
    setBusy(true);
    const patch: Record<string, unknown> = {};
    for (const [mapKey, raw] of Object.entries(edits)) {
      const sepAt = mapKey.indexOf(SEP);
      const section = mapKey.slice(0, sepAt);
      const fieldKey = mapKey.slice(sepAt + SEP.length);
      // "general" holds top-level scalars; other sections nest under their name.
      const path = section === "general" ? fieldKey.split(".") : [section, ...fieldKey.split(".")];
      setDeep(patch, path, coerce(raw));
    }
    try {
      await putConfig(patch, `edit ${dirty} field${dirty > 1 ? "s" : ""} (desk)`);
      window.location.reload();
    } catch {
      setBusy(false);
    }
  };
  return (
    <div className="settings-view">
    <div className="settings-grid">
      <Panel
        title="Configuration — version history"
        right={<FreshBadge fresh={config} label="versioned · append-only · hot-reload" />}
        pad={false}
        className="config-panel"
      >
        <div className="table-scroll">
          <table className="dt config-table">
            <thead>
              <tr>
                <th className="r">Ver</th>
                <th className="l">Changed by</th>
                <th className="l">Comment</th>
                <th className="l">When</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(data?.history ?? []).map((h) => (
                <tr key={h.version}>
                  <td className="r mono accent">v{h.version}</td>
                  <td className="l mono dim">{h.by}</td>
                  <td className="l dim small">{h.comment || "—"}</td>
                  <td className="l mono dim small">{h.at ? new Date(h.at).toLocaleString() : "—"}</td>
                  <td className="r">
                    <button
                      className="row-close"
                      disabled={!WRITE_ENABLED || busy}
                      title={WRITE_ENABLED ? `revert to v${h.version}` : GATE_TITLE}
                      onClick={() => onRevert(h.version)}
                    >
                      revert
                    </button>
                  </td>
                </tr>
              ))}
              {(!data || data.history.length === 0) && (
                <tr>
                  <td colSpan={5} className="dim small ivz-empty">
                    no version recorded (default config)
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title={"Current config" + (data ? ` · v${data.currentVersion}` : "")} className="config-edit">
        {!data ? (
          <div className="close-empty">config unavailable.</div>
        ) : (
          <>
            <div className="cfg-sections">
              {data.sections.map((s) => (
                <div key={s.name} className="cfg-section">
                  <div className="cfg-section-head mono">{s.name}</div>
                  {s.fields.map((f) => {
                    const mapKey = `${s.name}${SEP}${f.key}`;
                    return (
                      <div key={f.key} className="cfg-field">
                        <span className="cfg-key mono dim">{f.key}</span>
                        {WRITE_ENABLED ? (
                          <input
                            className="cfg-val-input mono"
                            value={edits[mapKey] ?? f.value}
                            disabled={busy}
                            onChange={(e) =>
                              setEdits((prev) => {
                                const next = { ...prev };
                                if (e.target.value === f.value) delete next[mapKey];
                                else next[mapKey] = e.target.value;
                                return next;
                              })
                            }
                          />
                        ) : (
                          <span className="cfg-val mono accent">{f.value}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
            {WRITE_ENABLED && (
              <div className="cfg-commit-bar">
                <button
                  className="btn-primary"
                  disabled={busy || dirty === 0}
                  title={dirty === 0 ? "no changes" : `commit ${dirty} field(s)`}
                  onClick={onCommit}
                >
                  {busy ? "…" : dirty > 0 ? `Commit ${dirty} change${dirty > 1 ? "s" : ""}` : "No changes"}
                </button>
                {dirty > 0 && (
                  <button className="row-close" disabled={busy} onClick={() => setEdits({})}>
                    reset
                  </button>
                )}
              </div>
            )}
            <div className="dim small" style={{ marginTop: 10 }}>
              {WRITE_ENABLED
                ? "Per-field editing · commit appends a new version + hot-reload (Pydantic validates types server-side)."
                : "Read-only · config editing (commit/revert) arrives with auth (Phase 2)."}
            </div>
          </>
        )}
      </Panel>
      </div>
      <div className="settings-domains">
        <DomainSettingsPanel domain="trade" />
        <DomainSettingsPanel domain="signal" />
        <DomainSettingsPanel domain="risk" />
        <DomainSettingsPanel domain="portfolio" />
      </div>
    </div>
  );
}
