/**
 * VOLDESK — Settings (versioned, append-only trading config + edit panel).
 * Faithful 1:1 port of the prototype's `js/views_misc.jsx` SettingsView.
 * Mock data for now; wires to the backend in a later lot.
 */
import { useState } from "react";
import { Panel } from "../components/common";
import { DATA2 } from "../data";

export function SettingsView(): JSX.Element {
  const [sel, setSel] = useState<number | null>(null);
  return (
    <div className="settings-grid">
      <Panel
        title="Trading configuration"
        right={<span className="dim mono">versioned · append-only · hot-reload</span>}
        pad={false}
        className="config-panel"
      >
        <div className="table-scroll">
          <table className="dt config-table">
            <thead>
              <tr>
                <th className="l">Key</th>
                <th className="r">Value</th>
                <th className="r">Ver</th>
                <th className="l">Changed by</th>
                <th className="l">Note</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {DATA2.config.map((c, i) => (
                <tr key={i} className={sel === i ? "row-now" : ""} onClick={() => setSel(i)}>
                  <td className="l mono">{c.key}</td>
                  <td className="r mono accent">{c.value}</td>
                  <td className="r mono dim">v{c.v}</td>
                  <td className="l mono dim">{c.by}</td>
                  <td className="l dim small">{c.note || "—"}</td>
                  <td className="r">
                    <button className="row-close">revert</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Edit value" className="config-edit">
        {sel == null ? (
          <div className="close-empty">Select a config key to edit.</div>
        ) : (
          <>
            <label className="field">
              <span>{DATA2.config[sel]!.key}</span>
              <div className="field-input">
                <input defaultValue={DATA2.config[sel]!.value} />
              </div>
            </label>
            <label className="field">
              <span>Comment</span>
              <div className="field-input">
                <input placeholder="reason for change…" />
              </div>
            </label>
            <div className="book-btns">
              <button className="btn-ghost" onClick={() => setSel(null)}>
                Cancel
              </button>
              <button className="btn-primary">Commit v{DATA2.config[sel]!.v + 1}</button>
            </div>
            <div className="dim small" style={{ marginTop: 10 }}>
              Appends a new version · triggers hot-reload of affected engine · previous versions remain revertable.
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
