// Settings page — admin UI for the versioned vol trading config.
//
// T1 scope : 2 editable fields (signal.threshold_vol_pts, signal.model_p)
// + history pane showing the last 20 versions with a revert button. Full
// RJSF-driven form lands in T2 when more params are wired to the engine.
import { useCallback, useEffect, useState } from "react";

import {
  ConfigResponse,
  fetchConfigHistory,
  fetchCurrentConfig,
  putConfig,
  revertConfig,
} from "../api/admin";
import { ApiError } from "../api/client";

type ModelP = "har" | "garch" | "ewma";

interface FormState {
  threshold_vol_pts: number;
  model_p: ModelP;
  comment: string;
}

function toForm(cfg: ConfigResponse): FormState {
  const sig = cfg.config.signal;
  return {
    threshold_vol_pts: Number(sig.threshold_vol_pts) || 1.0,
    model_p: (sig.model_p as ModelP) || "har",
    comment: "",
  };
}

export function Settings(): JSX.Element {
  const [current, setCurrent] = useState<ConfigResponse | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [history, setHistory] = useState<ConfigResponse[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const [cur, hist] = await Promise.all([
        fetchCurrentConfig(),
        fetchConfigHistory(20),
      ]);
      setCurrent(cur);
      setForm(toForm(cur));
      setHistory(hist);
    } catch (e) {
      setError(e instanceof ApiError ? `API ${e.status} on ${e.url}` : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function onSave() {
    if (!form) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const res = await putConfig({
        patch: {
          signal: {
            threshold_vol_pts: form.threshold_vol_pts,
            model_p: form.model_p,
          },
        },
        comment: form.comment || undefined,
      });
      setMessage(`Saved version ${res.version}.`);
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? JSON.stringify(e.body) : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function onRevert(version: number) {
    if (!confirm(`Revert to version ${version} ? A new version pointing here will be created.`)) return;
    setError(null);
    setMessage(null);
    try {
      const res = await revertConfig(version, undefined, `manual revert from UI to v${version}`);
      setMessage(`Reverted. New version = ${res.version}.`);
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? JSON.stringify(e.body) : String(e));
    }
  }

  if (!current || !form) {
    return (
      <section className="settings-page">
        <h1>Settings</h1>
        <p>{error ?? "Loading current config…"}</p>
      </section>
    );
  }

  return (
    <section className="settings-page">
      <h1>Settings — vol trading config</h1>
      <p>
        Current version : <strong>{current.version}</strong>
        {current.updated_at ? ` — last update ${new Date(current.updated_at).toLocaleString()}` : null}
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void onSave();
        }}
      >
        <label>
          <span>Signal threshold (vol pts)</span>
          <input
            type="number"
            step="0.01"
            min="0.1"
            max="10"
            value={form.threshold_vol_pts}
            onChange={(e) =>
              setForm({ ...form, threshold_vol_pts: Number(e.target.value) })
            }
          />
        </label>

        <label>
          <span>Forecast model</span>
          <select
            value={form.model_p}
            onChange={(e) => setForm({ ...form, model_p: e.target.value as ModelP })}
          >
            <option value="har">HAR-RV</option>
            <option value="garch">GARCH(1,1)</option>
            <option value="ewma">EWMA</option>
          </select>
        </label>

        <label>
          <span>Comment (audit log)</span>
          <input
            type="text"
            maxLength={500}
            placeholder="e.g. tighter threshold after may backtest"
            value={form.comment}
            onChange={(e) => setForm({ ...form, comment: e.target.value })}
          />
        </label>

        <button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </form>

      {error ? <p role="alert" style={{ color: "tomato" }}>{error}</p> : null}
      {message ? <p role="status" style={{ color: "teal" }}>{message}</p> : null}

      <h2>History</h2>
      <table>
        <thead>
          <tr>
            <th>Version</th>
            <th>Updated at</th>
            <th>By</th>
            <th>Comment</th>
            <th>threshold</th>
            <th>model</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {history.map((row) => (
            <tr key={row.version}>
              <td>{row.version}</td>
              <td>{new Date(row.updated_at).toLocaleString()}</td>
              <td>{row.updated_by ?? "—"}</td>
              <td>{row.comment ?? "—"}</td>
              <td>{String(row.config.signal?.threshold_vol_pts ?? "—")}</td>
              <td>{String(row.config.signal?.model_p ?? "—")}</td>
              <td>
                <button type="button" onClick={() => void onRevert(row.version)}>
                  Revert
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
