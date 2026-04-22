import { useEffect, useState } from "react";
import { fetchRegime, type RegimeResponse } from "../../api/cockpit";

const REGIME_COLORS: Record<string, string> = {
  calm: "#22c55e",
  stressed: "#f59e0b",
  pre_event: "#ef4444",
};

export function RegimeDetectorPanel(): JSX.Element {
  const [data, setData] = useState<RegimeResponse | null>(null);

  useEffect(() => {
    const load = () => fetchRegime().then(setData).catch(() => setData(null));
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  if (!data) {
    return (
      <section className="panel regime-panel" data-testid="regime-panel">
        <header className="panel-header"><h2>Regime Detector</h2></header>
        <div className="panel-body">loading…</div>
      </section>
    );
  }
  const color = REGIME_COLORS[data.regime] ?? "#94a3b8";
  return (
    <section className="panel regime-panel" data-testid="regime-panel">
      <header className="panel-header">
        <h2>Regime</h2>
        <span style={{
          background: color, color: "white", padding: "2px 8px",
          borderRadius: 3, fontSize: 11, fontWeight: 600, textTransform: "uppercase",
        }}>{data.regime.replace("_", " ")}</span>
      </header>
      <div className="panel-body">
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>
          features
        </div>
        <table className="smile-table" style={{ width: "100%" }}>
          <tbody>
            <tr><td>vol_level</td><td>{fmt(data.features.vol_level ?? null, "%")}</td></tr>
            <tr><td>term_slope</td><td>{fmt(data.features.term_slope ?? null, "%")}</td></tr>
            <tr><td>event_dampener</td><td>{data.event_dampener ? "ON" : "OFF"}</td></tr>
          </tbody>
        </table>
        <div style={{ fontSize: 11, color: "var(--muted)", margin: "8px 0 4px" }}>
          expected VRP (vol pts)
        </div>
        <table className="smile-table" style={{ width: "100%" }}>
          <tbody>
            {Object.entries(data.vrp_by_tenor).map(([tenor, vrp]) => (
              <tr key={tenor}><td>{tenor}</td><td>+{vrp.toFixed(2)}</td></tr>
            ))}
          </tbody>
        </table>
        {data.bootstrap && (
          <div style={{ marginTop: 6, fontSize: 10, color: "var(--muted)", fontStyle: "italic" }}>
            regime model in bootstrap — GMM calibration accumulating
          </div>
        )}
      </div>
    </section>
  );
}

function fmt(v: number | null, unit = ""): string {
  if (v == null) return "—";
  return `${v.toFixed(2)}${unit}`;
}
