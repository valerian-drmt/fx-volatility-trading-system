/**
 * VOLDESK — Settings (versioned, append-only trading config).
 *
 * HYBRID read model (R11 PR 2r.2, decision 2026-06-16):
 *   - version-history table  ← /admin/config/history
 *   - current-config viewer  ← /admin/config (nested → flat section rows)
 * Read-only : revert/commit are gated behind WRITE_ENABLED (auth, Phase 2/2w).
 */
import { Panel } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { useDeskData } from "../data/deskData";
import { WRITE_ENABLED } from "../data/writeEnabled";

const GATE_TITLE = "écriture désactivée — auth requise (Phase 2)";

export function SettingsView(): JSX.Element {
  const { config } = useDeskData();
  const data = config.data;
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
                    <button className="row-close" disabled={!WRITE_ENABLED} title={WRITE_ENABLED ? "" : GATE_TITLE}>
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
                  {s.fields.map((f) => (
                    <div key={f.key} className="cfg-field">
                      <span className="cfg-key mono dim">{f.key}</span>
                      <span className="cfg-val mono accent">{f.value}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div className="dim small" style={{ marginTop: 10 }}>
              {WRITE_ENABLED
                ? "Édition validée par section (JSON-Schema) · commit append une nouvelle version + hot-reload."
                : "Lecture seule · l'édition de la config (commit/revert) arrive avec l'auth (Phase 2)."}
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
