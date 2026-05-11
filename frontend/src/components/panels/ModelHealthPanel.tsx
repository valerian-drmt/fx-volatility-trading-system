import { useEffect, useState } from "react";
import { fetchModelHealth, type ModelHealthResponse } from "../../api/cockpit";

export function ModelHealthPanel(): JSX.Element {
  const [data, setData] = useState<ModelHealthResponse | null>(null);

  useEffect(() => {
    const load = () => fetchModelHealth().then(setData).catch(() => { /* keep last good */ });
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (!data) {
    return (
      <section className="panel health-panel" data-testid="model-health-panel">
        <header className="panel-header"><h2>Model Health</h2></header>
        <div className="panel-body">loading…</div>
      </section>
    );
  }

  const check = (ready: boolean, label: string, hint: string) => (
    <tr>
      <td>{label}</td>
      <td style={{ color: ready ? "#22c55e" : "#f59e0b", fontWeight: 600 }}>
        {ready ? "READY" : "ACCUMULATING"}
      </td>
      <td style={{ fontSize: 10, color: "var(--muted)" }}>{hint}</td>
    </tr>
  );

  return (
    <section className="panel health-panel" data-testid="model-health-panel">
      <header className="panel-header"><h2>Model Health</h2></header>
      <div className="panel-body">
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>
          accumulated observations
        </div>
        <table className="smile-table" style={{ width: "100%" }}>
          <tbody>
            <tr><td>vol_surface_history</td><td>{data.vol_surfaces_count}</td></tr>
            <tr><td>svi_params (in JSONB)</td><td>{data.svi_params_count}</td></tr>
          </tbody>
        </table>
        <div style={{ margin: "10px 0 6px", fontSize: 11, color: "var(--muted)" }}>
          readiness checks
        </div>
        <table className="smile-table" style={{ width: "100%" }}>
          <tbody>
            {check(data.pca_ready, "PCA loadings", "≥50 vol surfaces")}
          </tbody>
        </table>
        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 8 }}>
          last vol surface: {data.last_vol_surface_ts ? new Date(data.last_vol_surface_ts).toLocaleTimeString() : "—"}
        </div>
      </div>
    </section>
  );
}
