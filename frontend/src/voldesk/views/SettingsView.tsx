/**
 * VOLDESK — Settings (versioned, append-only trading config).
 *
 * HYBRID read model (R11 PR 2r.2, decision 2026-06-16):
 *   - version-history table  ← /admin/config/history
 *   - current-config viewer  ← /admin/config (nested → flat section rows)
 * Read-only : revert/commit are gated behind WRITE_ENABLED (auth, Phase 2/2w).
 */
import { useState } from "react";
import { putConfig, revertConfig } from "../../api/endpoints";
import { Panel } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { useDeskData } from "../data/deskData";
import { WRITE_ENABLED } from "../data/writeEnabled";

const GATE_TITLE = "écriture désactivée — auth requise (Phase 2)";
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
                    aucune version enregistrée (config par défaut)
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title={"Current config" + (data ? ` · v${data.currentVersion}` : "")} className="config-edit">
        {!data ? (
          <div className="close-empty">config indisponible.</div>
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
                  title={dirty === 0 ? "aucune modification" : `commit ${dirty} champ(s)`}
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
                ? "Édition par champ · commit append une nouvelle version + hot-reload (Pydantic valide les types côté serveur)."
                : "Lecture seule · l'édition de la config (commit/revert) arrive avec l'auth (Phase 2)."}
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
