/**
 * Vol Config editor — affiche la version courante de VolTradingConfig +
 * édition des 2 fields hot-reloadables (signal.threshold_vol_pts +
 * signal.model_p). Le reste des sections est read-only (cf.
 * docs/VOL_ENGINE_REFERENCE.md §6).
 *
 * Backend :
 *   GET /api/v1/admin/config        → current
 *   PUT /api/v1/admin/config {patch} → deep-merge + validate + INSERT
 *                                      vN+1 + PUBLISH config:changed
 */
import { useEffect, useState } from "react";

interface ConfigResponse {
  version: number;
  config: Record<string, unknown>;
  updated_at: string;
  updated_by: string | null;
  comment: string | null;
}

export function VolConfigEditor(): JSX.Element {
  const [current, setCurrent] = useState<ConfigResponse | null>(null);
  const [threshold, setThreshold] = useState<number>(1.0);
  const [modelP, setModelP] = useState<"har" | "garch">("har");
  const [comment, setComment] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<ConfigResponse | null>(null);

  const fetchCurrent = async () => {
    setError(null);
    try {
      const r = await fetch("/api/v1/admin/config");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as ConfigResponse;
      setCurrent(j);
      const sig = (j.config["signal"] as Record<string, unknown> | undefined) ?? {};
      if (typeof sig["threshold_vol_pts"] === "number") setThreshold(sig["threshold_vol_pts"] as number);
      if (sig["model_p"] === "har" || sig["model_p"] === "garch") setModelP(sig["model_p"] as "har" | "garch");
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => { void fetchCurrent(); }, []);

  const save = async () => {
    if (!confirm(`Update signal config : threshold=${threshold}, model_p=${modelP} ?`)) return;
    setSaving(true);
    setError(null);
    try {
      const r = await fetch("/api/v1/admin/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          patch: { signal: { threshold_vol_pts: threshold, model_p: modelP } },
          user: "frontend-dev",
          comment: comment || `signal.threshold=${threshold}, model_p=${modelP}`,
        }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${JSON.stringify(j)}`);
      setLastResult(j);
      setComment("");
      void fetchCurrent();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (!current && !error) return <div style={{ color: "#666", padding: 12 }}>(loading current config…)</div>;
  if (error) return <div style={{ color: "#e66", padding: 12 }}>{error}</div>;
  if (!current) return <></>;

  // Sections au-delà de signal sont read-only ; affichées en JSON pour info.
  const sections = Object.entries(current.config).filter(([k]) => k !== "signal");

  return (
    <div style={{ padding: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
      {/* Left : editor */}
      <section className="panel">
        <header className="panel-header">
          <h2 style={{ fontSize: 13 }}>signal.* — hot-reloadable</h2>
          <span style={{ color: "#666", fontSize: 11, marginLeft: "auto" }}>
            v{current.version} · {new Date(current.updated_at).toLocaleString()}
          </span>
        </header>
        <div className="panel-body" style={{ padding: 12 }}>
          <Row label="threshold_vol_pts">
            <input
              type="number" step={0.1} min={0.1} max={10}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value) || 1.0)}
              style={inputStyle}
            />
            <div style={hint}>seuil |écart| pour générer CHEAP/EXPENSIVE (défaut 1.0)</div>
          </Row>
          <Row label="model_p">
            <select value={modelP} onChange={(e) => setModelP(e.target.value as "har" | "garch")} style={inputStyle}>
              <option value="har">HAR-RV (Corsi 2009 — préféré)</option>
              <option value="garch">GARCH(1,1) — fallback legacy</option>
            </select>
            <div style={hint}>estimateur P-measure pour fair vol (défaut HAR)</div>
          </Row>
          <Row label="comment">
            <input
              type="text" value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="optionnel — pour l'audit history"
              style={inputStyle}
            />
          </Row>
          <button onClick={save} disabled={saving} style={{ ...btnStyle, marginTop: 12, width: "100%" }}>
            {saving ? "…" : "Save → INSERT v" + (current.version + 1) + " + PUBLISH config:changed"}
          </button>
          {lastResult && (
            <div style={{ marginTop: 12, fontSize: 11, color: "#6c6" }}>
              ✓ saved v{lastResult.version} at {new Date(lastResult.updated_at).toLocaleTimeString()}
              {lastResult.comment && ` — "${lastResult.comment}"`}
            </div>
          )}
        </div>
      </section>

      {/* Right : read-only autres sections */}
      <section className="panel">
        <header className="panel-header">
          <h2 style={{ fontSize: 13 }}>Read-only — autres sections (pas wired runtime)</h2>
        </header>
        <div className="panel-body" style={{ padding: 12 }}>
          <div style={{ color: "#888", fontSize: 11, marginBottom: 8 }}>
            Ces sections sont définies dans <code>VolTradingConfig</code> mais
            le moteur ne les consume pas encore (cf. docs/VOL_ENGINE_REFERENCE.md
            §6). Modifs sans effet jusqu'à ce qu'elles soient wired.
          </div>
          {sections.map(([key, val]) => (
            <details key={key} style={{ marginBottom: 6 }}>
              <summary style={{ color: "#7af", fontSize: 12, cursor: "pointer" }}>{key}</summary>
              <pre style={preStyle}>{JSON.stringify(val, null, 2)}</pre>
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ color: "#aaa", fontSize: 12, marginBottom: 3 }}>{label}</div>
      {children}
    </div>
  );
}

const inputStyle = {
  background: "#1a1a1a", color: "#ddd", border: "1px solid #333",
  borderRadius: 3, padding: "5px 8px", fontSize: 13,
  width: "100%", boxSizing: "border-box" as const,
};
const btnStyle = {
  padding: "6px 12px", background: "#2a4a6a", color: "#fff",
  border: "none", borderRadius: 3, cursor: "pointer", fontSize: 12, fontWeight: 600,
};
const hint = { color: "#666", fontSize: 10, marginTop: 2 };
const preStyle = {
  margin: 0, padding: 8, background: "#000", color: "#cdc",
  fontSize: 11, fontFamily: "Consolas, monospace",
  overflow: "auto" as const, maxHeight: 200,
  whiteSpace: "pre-wrap" as const, wordBreak: "break-all" as const,
};
