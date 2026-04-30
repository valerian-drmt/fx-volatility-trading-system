/**
 * Step 2 — PCA signal detection panel.
 *
 * Layout (cf. STEP2 §3) :
 *   - Top : 3 colonnes PC (1=level, 2=slope, 3=smile) avec z-score, label,
 *           actionable, recommended structure
 *   - Diagnostics : variance explained, loadings_stable, coherence
 *   - Admin : bouton "Refit PCA" (manuel MVP, demande ≥ 30 snapshots horaires)
 *
 * Auto-refresh 3s. Affiche bootstrap progress si pas de modèle actif.
 */
import { useEffect, useState } from "react";

interface PcSig {
  z_score: number;
  raw_score: number;
  label: "CHEAP" | "FAIR" | "EXPENSIVE";
  actionable: boolean;
  actionable_reason: string | null;
  recommended_structure: string | null;
}
interface PcaState {
  state: "bootstrap" | "stable" | "unstable";
  model_version: string | null;
  timestamp?: string;
  n_obs_in_fit?: number;
  variance_explained?: { pc1: number; pc2: number; pc3: number; cumulative: number };
  loadings_stable?: { pc1: boolean; pc2: boolean; pc3: boolean };
  signals: { pc1?: PcSig; pc2?: PcSig; pc3?: PcSig };
  diagnostics?: { reason?: string };
}
interface ModelInfo {
  active: boolean;
  version: string | null;
  available_hourly_snapshots: number;
  min_obs_for_refit: number;
  ready_to_refit: boolean;
  variance_explained: number[] | null;
}

const PC_NAMES: Record<string, string> = {
  pc1: "PC1 — level (vol overall)",
  pc2: "PC2 — slope (term structure)",
  pc3: "PC3 — smile (skew/convexity)",
};

export function Step2Pca(): JSX.Element {
  const [state, setState] = useState<PcaState | null>(null);
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refitting, setRefitting] = useState(false);
  const [refitResult, setRefitResult] = useState<string | null>(null);

  const fetchAll = async () => {
    try {
      const [s, m] = await Promise.all([
        fetch("/api/v1/signals/pca/state").then((r) => r.json()),
        fetch("/api/v1/signals/pca/model").then((r) => r.json()),
      ]);
      setState(s); setModel(m); setError(null);
    } catch (e) { setError(String(e)); }
  };

  useEffect(() => {
    void fetchAll();
    const id = window.setInterval(fetchAll, 3_000);
    return () => window.clearInterval(id);
  }, []);

  const triggerRefit = async () => {
    if (!confirm(`Refit PCA on ${model?.available_hourly_snapshots ?? 0} hourly snapshots ?`)) return;
    setRefitting(true); setRefitResult(null);
    try {
      const r = await fetch("/api/v1/admin/pca/refit", { method: "POST" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
      setRefitResult(`✓ ${j.version} — ${j.n_obs_used} obs · var=${(j.variance_explained_ratio || []).map((v: number) => (v*100).toFixed(0)+'%').join('/')}`);
      void fetchAll();
    } catch (e) {
      setRefitResult(`✗ ${String(e)}`);
    } finally {
      setRefitting(false);
    }
  };

  if (error && !state) return <div style={{ color: "#e66", padding: 12 }}>{error}</div>;
  if (!state) return <div style={{ color: "#666", padding: 12 }}>(loading PCA signals…)</div>;

  const isBootstrap = state.state === "bootstrap" || !state.model_version;

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
      {/* ── Top : status + admin refit ── */}
      <section style={panelStyle}>
        <div style={{ padding: 10, display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          <div>
            <div style={{ color: "#aaa", fontSize: 11 }}>STATE</div>
            <div style={{
              fontWeight: 700, fontSize: 16,
              color: isBootstrap ? "#fc6" : (state.state === "stable" ? "#6c6" : "#e66"),
            }}>
              {state.state.toUpperCase()}
            </div>
          </div>
          <div>
            <div style={{ color: "#aaa", fontSize: 11 }}>ACTIVE MODEL</div>
            <code style={{ fontSize: 12 }}>{state.model_version ?? "—"}</code>
          </div>
          {state.n_obs_in_fit && (
            <div>
              <div style={{ color: "#aaa", fontSize: 11 }}>N_OBS IN FIT</div>
              <div style={{ fontSize: 13 }}>{state.n_obs_in_fit}</div>
            </div>
          )}
          <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
            {model && (
              <div style={{ fontSize: 11, color: "#888" }}>
                hourly snapshots : <strong style={{ color: "#ddd" }}>{model.available_hourly_snapshots}</strong> / {model.min_obs_for_refit} min
              </div>
            )}
            <button
              onClick={triggerRefit}
              disabled={refitting || !model?.ready_to_refit}
              style={{
                ...btnStyle,
                opacity: model?.ready_to_refit ? 1 : 0.4,
                cursor: model?.ready_to_refit ? "pointer" : "not-allowed",
              }}
            >
              {refitting ? "…" : "Refit PCA"}
            </button>
          </div>
        </div>
        {refitResult && (
          <div style={{ padding: "0 12px 10px", fontSize: 11, color: refitResult.startsWith("✓") ? "#6c6" : "#e66" }}>
            {refitResult}
          </div>
        )}
      </section>

      {/* ── Bootstrap : pas encore de model ── */}
      {isBootstrap && (
        <section style={panelStyle}>
          <div style={{ padding: 16, color: "#888", fontSize: 13, lineHeight: 1.6 }}>
            Pas de modèle PCA actif.
            <br />
            Le système collecte un snapshot 30-dim toutes les heures
            (table <code>surface_snapshots_hourly</code>).
            Une fois <strong>≥ {model?.min_obs_for_refit ?? 30}</strong> snapshots accumulés,
            cliquer <strong>Refit PCA</strong> pour fitter le 1er modèle.
            <div style={{ marginTop: 8, color: "#aaa", fontSize: 11 }}>
              Cf. docs/vol_trading_pca/specs/STEP2_SIGNAL_DETECTION.md §9 bootstrap.
            </div>
          </div>
        </section>
      )}

      {/* ── 3 colonnes PC ── */}
      {!isBootstrap && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
          {(["pc1", "pc2", "pc3"] as const).map((pc) => (
            <PcCard
              key={pc} pcKey={pc}
              sig={state.signals[pc]}
              variance={state.variance_explained?.[pc] ?? 0}
              stable={state.loadings_stable?.[pc] ?? false}
            />
          ))}
        </div>
      )}

      {/* ── Diagnostics ── */}
      {!isBootstrap && state.variance_explained && (
        <section style={panelStyle}>
          <Header>Diagnostics — variance explained, stability, coherence</Header>
          <div style={{ padding: 12, fontSize: 12, fontFamily: "Consolas, monospace" }}>
            <div>
              cumul (PC1+2+3) : <strong>{(state.variance_explained.cumulative * 100).toFixed(1)}%</strong>
              {state.variance_explained.cumulative < 0.85 && (
                <span style={{ color: "#fc6", marginLeft: 8 }}>(low — PC4-6 carry meaningful var)</span>
              )}
            </div>
            <div style={{ marginTop: 4 }}>
              loadings stable :
              {(["pc1", "pc2", "pc3"] as const).map((p) => (
                <span key={p} style={{
                  marginLeft: 8,
                  color: state.loadings_stable?.[p] ? "#6c6" : "#e66",
                }}>
                  {p}={state.loadings_stable?.[p] ? "yes" : "no"}
                </span>
              ))}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

function PcCard({
  pcKey, sig, variance, stable,
}: {
  pcKey: "pc1" | "pc2" | "pc3";
  sig: PcSig | undefined;
  variance: number;
  stable: boolean;
}): JSX.Element {
  if (!sig) {
    return (
      <section style={panelStyle}>
        <Header>{PC_NAMES[pcKey]}</Header>
        <div style={{ padding: 16, color: "#666", fontSize: 12 }}>(no signal yet)</div>
      </section>
    );
  }
  const labelColor = sig.label === "CHEAP" ? "#6c6" : sig.label === "EXPENSIVE" ? "#e66" : "#aaa";
  const z = sig.z_score;
  const barWidth = Math.min(Math.abs(z) / 3.0, 1.0) * 100;  // cap at z=3
  return (
    <section style={panelStyle}>
      <Header>{PC_NAMES[pcKey]}</Header>
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontSize: 28, fontWeight: 700, color: labelColor }}>
            {z >= 0 ? "+" : ""}{z.toFixed(2)}
          </span>
          <span style={{ fontSize: 11, color: "#888" }}>z-score</span>
        </div>
        <div style={{ position: "relative", height: 6, background: "#222", borderRadius: 3 }}>
          <div style={{
            position: "absolute", top: 0, height: 6,
            left: z >= 0 ? "50%" : `${50 - barWidth/2}%`,
            width: `${barWidth/2}%`,
            background: labelColor, borderRadius: 3,
          }} />
          <div style={{
            position: "absolute", top: -2, left: "50%", width: 1, height: 10,
            background: "#666",
          }} />
        </div>
        <div style={{
          padding: "4px 10px", background: labelColor, color: "#000",
          fontWeight: 700, borderRadius: 3, fontSize: 14, alignSelf: "flex-start",
        }}>
          {sig.label}
        </div>
        <div style={{
          padding: "4px 8px",
          background: sig.actionable ? "#363" : "#444",
          color: sig.actionable ? "#cfc" : "#aaa",
          borderRadius: 3, fontSize: 11, fontFamily: "Consolas, monospace",
        }}>
          {sig.actionable ? "✓ actionable" : `✗ ${sig.actionable_reason ?? "not actionable"}`}
        </div>
        {sig.recommended_structure && (
          <div style={{ fontSize: 12 }}>
            structure : <code style={{ color: "#7af" }}>{sig.recommended_structure}</code>
          </div>
        )}
        <div style={{ borderTop: "1px solid #222", marginTop: 4, paddingTop: 6, color: "#888", fontSize: 10 }}>
          variance: {(variance * 100).toFixed(1)}% · loadings: {stable ? "stable" : "UNSTABLE"} · raw: {sig.raw_score.toFixed(3)}
        </div>
      </div>
    </section>
  );
}

function Header({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <div style={{
      padding: "5px 12px", background: "#1a1a1a", borderBottom: "1px solid #333",
      color: "#7af", fontSize: 11, fontWeight: 600, letterSpacing: 1,
    }}>{children}</div>
  );
}

const panelStyle = {
  background: "#0a0a0a", border: "1px solid #222", borderRadius: 4, overflow: "hidden",
};
const btnStyle = {
  padding: "6px 14px", background: "#2a4a6a", color: "#fff",
  border: "none", borderRadius: 3, fontSize: 12, fontWeight: 600,
};
