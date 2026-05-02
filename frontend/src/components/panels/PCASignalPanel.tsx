import { useEffect, useState } from "react";
import {
  fetchPcaState,
  type PcaSignalNode,
  type PcaStateResponse,
} from "../../api/cockpit";

const PC_KEYS = ["pc1", "pc2", "pc3"] as const;
type PcKey = (typeof PC_KEYS)[number];

const LABEL_COLOR: Record<PcaSignalNode["label"], string> = {
  CHEAP: "#22c55e",
  EXPENSIVE: "#ef4444",
  FAIR: "#94a3b8",
};

export function PCASignalPanel(): JSX.Element {
  const [data, setData] = useState<PcaStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      fetchPcaState()
        .then((d) => { setData(d); setError(null); })
        .catch(() => setError("fetch failed"));
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  if (!data) {
    return (
      <section className="panel pca-panel" data-testid="pca-panel">
        <header className="panel-header"><h2>PCA Signals</h2></header>
        <div className="panel-body">{error ?? "loading…"}</div>
      </section>
    );
  }

  if (data.state === "bootstrap") {
    return (
      <section className="panel pca-panel" data-testid="pca-panel">
        <header className="panel-header">
          <h2>PCA Signals</h2>
          <span className="panel-count" style={{ color: "var(--muted)" }}>
            bootstrap
          </span>
        </header>
        <div className="panel-body" style={{ fontSize: 12 }}>
          <div style={{ fontStyle: "italic", color: "var(--muted)" }}>
            No active PCA model yet. Accumulating hourly snapshots before first fit.
          </div>
          {data.diagnostics?.reason && (
            <div style={{ marginTop: 6, fontSize: 11, color: "var(--muted)" }}>
              reason: <code>{data.diagnostics.reason}</code>
            </div>
          )}
        </div>
      </section>
    );
  }

  const ve = data.variance_explained;
  const stable = data.loadings_stable;

  return (
    <section className="panel pca-panel" data-testid="pca-panel">
      <header className="panel-header">
        <h2>PCA Signals</h2>
        <span className="panel-count">
          {data.model_version ?? "—"} · T={data.n_obs_in_fit ?? "—"}
        </span>
      </header>
      <div className="panel-body">
        <CoherenceBadge coherence={data.coherence} />
        <div
          style={{
            display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12,
            marginTop: 8,
          }}
        >
          {PC_KEYS.map((pc) => (
            <SignalCard
              key={pc}
              pc={pc}
              sig={data.signals[pc]}
              variance={ve?.[pc] ?? 0}
              stable={stable?.[pc] ?? true}
            />
          ))}
        </div>
        {ve && (
          <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 8 }}>
            cumulative variance explained (PC1+2+3) ={" "}
            {(ve.cumulative * 100).toFixed(1)}%
          </div>
        )}
      </div>
    </section>
  );
}

function CoherenceBadge({
  coherence,
}: { coherence: PcaStateResponse["coherence"] }): JSX.Element | null {
  if (!coherence) return null;
  const ok = coherence.all_coherent;
  return (
    <div
      style={{
        display: "inline-block", padding: "2px 8px", borderRadius: 3,
        fontSize: 10, fontWeight: 600, letterSpacing: 0.3,
        background: ok ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)",
        color: ok ? "#22c55e" : "#ef4444",
      }}
      data-testid="pca-coherence"
    >
      {ok
        ? "SIGNALS COHERENT"
        : `CONTRADICTIONS (${coherence.contradictions
            .map(([a, b]) => `${a}↔${b}`)
            .join(", ")})`}
    </div>
  );
}

function SignalCard({
  pc, sig, variance, stable,
}: {
  pc: PcKey;
  sig: PcaSignalNode | undefined;
  variance: number;
  stable: boolean;
}): JSX.Element {
  if (!sig) {
    return (
      <div
        style={{
          padding: 8, border: "1px solid var(--border)", borderRadius: 4,
          fontSize: 11, color: "var(--muted)",
        }}
      >
        <div style={{ fontWeight: 600, textTransform: "uppercase" }}>{pc}</div>
        <div style={{ marginTop: 6, fontStyle: "italic" }}>no signal yet</div>
      </div>
    );
  }
  const color = LABEL_COLOR[sig.label];
  return (
    <div
      style={{
        padding: 8, border: "1px solid var(--border)", borderRadius: 4,
        fontSize: 11,
        opacity: stable ? 1 : 0.55,
      }}
      data-testid={`pca-card-${pc}`}
    >
      <div
        style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "baseline", fontWeight: 600,
        }}
      >
        <span style={{ textTransform: "uppercase" }}>{pc}</span>
        <span style={{ fontSize: 10, color: "var(--muted)" }}>
          {(variance * 100).toFixed(1)}% var
        </span>
      </div>
      <div
        style={{
          fontSize: 18, fontWeight: 700, margin: "6px 0", color,
        }}
      >
        z = {sig.z_score >= 0 ? "+" : ""}{sig.z_score.toFixed(2)}
      </div>
      <div style={{ color, fontWeight: 600 }}>{sig.label}</div>

      {!stable && (
        <div
          style={{
            marginTop: 6, fontSize: 10, color: "#f59e0b", fontStyle: "italic",
          }}
        >
          ⚠ loadings unstable — signal grayed out
        </div>
      )}

      {sig.actionable ? (
        <div style={{ marginTop: 8 }}>
          {sig.recommended_structure && (
            <div style={{ fontSize: 11, color: "var(--muted)" }}>
              → {sig.recommended_structure}
            </div>
          )}
          <button
            type="button"
            data-testid={`arm-${pc}`}
            style={{
              marginTop: 4, fontSize: 10, padding: "2px 8px",
              background: color, color: "#0f172a", border: "none",
              borderRadius: 3, fontWeight: 600, cursor: "pointer",
            }}
          >
            Arm trade
          </button>
        </div>
      ) : (
        <div
          style={{
            marginTop: 8, fontSize: 10, color: "var(--muted)",
            fontStyle: "italic",
          }}
          data-testid={`pca-reason-${pc}`}
        >
          not actionable: <code>{sig.actionable_reason ?? "—"}</code>
        </div>
      )}
    </div>
  );
}
