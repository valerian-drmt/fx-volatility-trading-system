import { useEffect, useState } from "react";
import { fetchPcaSignals, type PcaSignalsResponse } from "../../api/cockpit";

const Z_THRESHOLDS = { strong: 2.0, moderate: 1.5 };

export function PCASignalPanel(): JSX.Element {
  const [data, setData] = useState<PcaSignalsResponse | null>(null);

  useEffect(() => {
    const load = () => fetchPcaSignals().then(setData).catch(() => setData(null));
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  if (!data) {
    return (
      <section className="panel pca-panel" data-testid="pca-panel">
        <header className="panel-header"><h2>PCA Signals</h2></header>
        <div className="panel-body">loading…</div>
      </section>
    );
  }
  return (
    <section className="panel pca-panel" data-testid="pca-panel">
      <header className="panel-header">
        <h2>PCA Signals</h2>
        <span className="panel-count">{data.n_samples_trained} snapshots</span>
      </header>
      <div className="panel-body">
        {data.bootstrap && (
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8, fontStyle: "italic" }}>
            model in bootstrap — signals unreliable below 50 samples
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
          {data.signals.slice(0, 3).map((sig) => (
            <SignalCard key={sig.pc} sig={sig} explained={data.explained_variance[sig.pc - 1] ?? 0} />
          ))}
        </div>
      </div>
    </section>
  );
}

function SignalCard({
  sig, explained,
}: { sig: PcaSignalsResponse["signals"][number]; explained: number }) {
  const absZ = Math.abs(sig.z_score);
  const status =
    sig.bootstrap ? "BOOT" :
    absZ >= Z_THRESHOLDS.strong ? (sig.z_score > 0 ? "EXPENSIVE" : "CHEAP") :
    absZ >= Z_THRESHOLDS.moderate ? "WEAK" : "FAIR";
  const color =
    status === "CHEAP" ? "#22c55e" :
    status === "EXPENSIVE" ? "#ef4444" :
    "#94a3b8";
  return (
    <div style={{
      padding: 8, border: "1px solid var(--border)", borderRadius: 4,
      fontSize: 11,
    }}>
      <div style={{ fontWeight: 600, textTransform: "capitalize" }}>
        PC{sig.pc} · {sig.label.replace("_", " ")}
      </div>
      <div style={{ color: "var(--muted)", fontSize: 10 }}>
        {(explained * 100).toFixed(1)}% variance explained
      </div>
      <div style={{ fontSize: 16, fontWeight: 600, margin: "6px 0", color }}>
        z = {sig.z_score >= 0 ? "+" : ""}{sig.z_score.toFixed(2)}
      </div>
      <div style={{ color, fontWeight: 600, fontSize: 11 }}>{status}</div>
      {sig.recommended_structure && (
        <div style={{ marginTop: 6, fontSize: 10, color: "var(--muted)" }}>
          → {sig.recommended_structure} {sig.recommended_tenor ?? ""}
        </div>
      )}
    </div>
  );
}
