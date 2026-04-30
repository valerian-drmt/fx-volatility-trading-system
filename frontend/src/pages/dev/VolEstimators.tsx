/**
 * Vol Estimators — comparaison RV (Yang-Zhang) / HAR / GARCH / σ_fair_q
 * par tenor. Permet de voir la divergence entre les 3 modèles P-measure
 * (signal régime change si HAR ≠ GARCH significatif) et d'auditer la
 * conversion VRP P → Q.
 *
 * Source : `latest_vol_surface:EURUSD` (Redis) → champs `_rv_full_pct`,
 * `_har`, `_garch`, `_fair_q`. Cf. docs/VOL_ENGINE_REFERENCE.md §4.
 */
import { useEffect, useState } from "react";

const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"];

interface SurfacePayload {
  surface: Record<string, unknown>;
  timestamp?: string;
}

export function VolEstimators({ symbol = "EURUSD" }: { symbol?: string }): JSX.Element {
  const [surface, setSurface] = useState<SurfacePayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const r = await fetch(`/api/v1/vol/surface?symbol=${symbol}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setSurface(await r.json());
      } catch (e) {
        setError(String(e));
      }
    };
    void fetchData();
    const id = window.setInterval(fetchData, 30_000); // refresh 30s, suffit pour cycle 180s
    return () => window.clearInterval(id);
  }, [symbol]);

  if (error) return <div style={{ color: "#e66", padding: 12 }}>{error}</div>;
  if (!surface) return <div style={{ color: "#666", padding: 12, fontSize: 12 }}>(loading…)</div>;

  const s = surface.surface;
  const rvFull = (typeof s["_rv_full_pct"] === "number" ? s["_rv_full_pct"] : null) as number | null;
  const har = (s["_har"] as Record<string, { sigma_har_pct: number }>) || {};
  const garch = (s["_garch"] as Record<string, { sigma_model_pct: number }>) || {};
  const fairQ = (s["_fair_q"] as Record<string, {
    sigma_fair_p_pct: number; vrp_vol_pts: number; sigma_fair_q_pct: number; regime: string;
  }>) || {};

  return (
    <div style={{ padding: 12 }}>
      <div style={{ marginBottom: 8, fontSize: 12, color: "#aaa" }}>
        <strong style={{ color: "#7af" }}>RV Yang-Zhang (P, full window)</strong> :{" "}
        {rvFull !== null ? `${rvFull.toFixed(3)}%` : <span style={{ color: "#e66" }}>absent</span>}
        {" — "}<span style={{ color: "#888" }}>anchor pour les autres estimateurs</span>
      </div>

      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={th}>Tenor</th>
            <th style={th}>σ HAR (P)</th>
            <th style={th}>σ GARCH (P)</th>
            <th style={th}>σ fair P</th>
            <th style={th}>VRP</th>
            <th style={th}>σ fair Q</th>
            <th style={th}>Régime</th>
          </tr>
        </thead>
        <tbody>
          {TENORS.map((t) => {
            const harVal = har[t]?.sigma_har_pct;
            const garchVal = garch[t]?.sigma_model_pct;
            const fq = fairQ[t];
            const divergence = harVal !== undefined && garchVal !== undefined
              ? Math.abs(harVal - garchVal)
              : null;
            const divergenceColor = divergence === null ? "#888"
              : divergence < 0.5 ? "#6c6"
              : divergence < 1.5 ? "#cc6"
              : "#e66";
            return (
              <tr key={t} style={{ borderTop: "1px solid #222" }}>
                <td style={td}>{t}</td>
                <td style={{ ...td, color: divergenceColor }}>
                  {harVal !== undefined ? `${harVal.toFixed(3)}%` : "—"}
                </td>
                <td style={{ ...td, color: divergenceColor }}>
                  {garchVal !== undefined ? `${garchVal.toFixed(3)}%` : "—"}
                </td>
                <td style={td}>{fq?.sigma_fair_p_pct?.toFixed(3) ?? "—"}%</td>
                <td style={{ ...td, color: fq?.vrp_vol_pts !== undefined ? "#7af" : "#888" }}>
                  {fq?.vrp_vol_pts !== undefined ? `+${fq.vrp_vol_pts.toFixed(3)}` : "—"}
                </td>
                <td style={td}>{fq?.sigma_fair_q_pct?.toFixed(3) ?? "—"}%</td>
                <td style={td}>{fq?.regime ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ marginTop: 8, fontSize: 11, color: "#666" }}>
        Couleur HAR/GARCH : <span style={{ color: "#6c6" }}>vert</span> = convergent (Δ&lt;0.5%),{" "}
        <span style={{ color: "#cc6" }}>jaune</span> = écart modéré,{" "}
        <span style={{ color: "#e66" }}>rouge</span> = divergence régime change probable.
      </div>
    </div>
  );
}

const tableStyle = { borderCollapse: "collapse" as const, fontSize: 12, fontFamily: "Consolas, monospace", width: "100%" };
const th = { padding: "4px 12px", textAlign: "left" as const, color: "#888", borderBottom: "1px solid #333" };
const td = { padding: "3px 12px" };
