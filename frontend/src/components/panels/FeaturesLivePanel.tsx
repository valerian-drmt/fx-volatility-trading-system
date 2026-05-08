/**
 * FeaturesLive panel — 8 cols + synthesis row.
 *
 * Consumes /api/v1/regime/features (E2 endpoint). Each feature row carries
 * value + z + bucket + Δz/1h + pct + signal + expected_z context. The
 * synthesis row at the bottom reads joint_pattern → regime → dominant →
 * vs_expected → action.
 *
 * Visual conventions :
 *   - bucket / signal badges : gray (calm), amber (1σ-ish), red (tail).
 *   - Δz/1h : red text when |Δz| > 0.15, neutral else.
 *   - expected_z status='insufficient' : "—" + tooltip with n_obs / threshold.
 */
import type { JSX } from "react";

export interface FeatureRow {
  name: string;
  value: number | null;
  z: number | null;
  bucket: "--" | "-" | "0" | "+" | "++" | null;
  delta_z_1h: number | null;
  pct: number | null;
  signal: "noise" | "weak" | "strong" | "tail" | null;
  expected_z: {
    mu: number | null;
    sigma: number | null;
    n_obs: number;
    status: "valid" | "approx" | "insufficient" | "stale";
    context: { event_type: string; days_bucket: number; tod_bucket: string };
    relaxation?: "table" | "exact" | "event_days" | "event" | "unconditional" | "cold_start";
  } | null;
  vs_expected: "underpriced" | "overpriced" | "aligned" | null;
}

export interface FeaturesPayload {
  timestamp?: string;
  features: FeatureRow[];
  synthesis: {
    joint_pattern: string | null;
    regime: { id: number; name: string; family: string; action_default: string;
              asymmetry_note: string | null; intensity_count: number } | null;
    dominant: string | null;
    vs_expected: { feature: string; delta_sigma: number; label: string } | null;
    action: string;
  };
}

const BUCKET_BG: Record<string, { bg: string; fg: string }> = {
  "0":  { bg: "#F1EFE8", fg: "#444441" },
  "-":  { bg: "#FAEEDA", fg: "#633806" },
  "+":  { bg: "#FAEEDA", fg: "#633806" },
  "--": { bg: "#FCEBEB", fg: "#791F1F" },
  "++": { bg: "#FCEBEB", fg: "#791F1F" },
};
const SIGNAL_BG: Record<string, { bg: string; fg: string }> = {
  noise:  { bg: "#F1EFE8", fg: "#444441" },
  weak:   { bg: "#FDF5E6", fg: "#7a5a18" },
  strong: { bg: "#FAEEDA", fg: "#633806" },
  tail:   { bg: "#FCEBEB", fg: "#791F1F" },
};

function Badge({ label, palette }: { label: string; palette: { bg: string; fg: string } }): JSX.Element {
  return (
    <span data-testid={`badge-${label}`} style={{
      background: palette.bg, color: palette.fg,
      fontFamily: "Consolas, monospace", fontSize: 11,
      fontWeight: 500, padding: "2px 6px", borderRadius: 3,
      letterSpacing: 0.5,
    }}>{label}</span>
  );
}

function _formatSigned(v: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}`;
}

function _deltaZStyle(v: number | null): React.CSSProperties {
  if (v == null) return { color: "#888" };
  return { color: Math.abs(v) > 0.15 ? "#993C1D" : "#ddd", fontWeight: 600 };
}

const HEADER_STYLE: React.CSSProperties = {
  padding: "5px 12px", background: "#1a1a1a", borderBottom: "1px solid #333",
  color: "#7af", fontSize: 11, fontWeight: 600, letterSpacing: 1,
};
const PANEL_STYLE: React.CSSProperties = {
  background: "#0a0a0a", border: "1px solid #222", borderRadius: 4, overflow: "hidden",
  // Floor matches the inner table's minWidth so the panel and PCA variance
  // panel beneath it share the same minimum footprint regardless of viewport.
  minWidth: 800,
};

export function FeaturesLivePanel({ payload }: { payload: FeaturesPayload | null }): JSX.Element {
  const features = payload?.features ?? [];
  const synth = payload?.synthesis;
  const th: React.CSSProperties = {
    padding: "5px 10px", color: "#aaa", fontWeight: 600,
    textAlign: "left", borderBottom: "1px solid #333",
    fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5,
  };
  const td: React.CSSProperties = {
    padding: "6px 10px", color: "#ddd", borderBottom: "1px solid #1a1a1a",
    fontSize: 12, fontFamily: "Consolas, monospace", verticalAlign: "middle",
  };
  return (
    <section style={PANEL_STYLE} data-testid="features-live-panel">
      <div style={HEADER_STYLE}>Features live</div>
      <div style={{ padding: 0, overflowX: "hidden" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 12, width: "100%", minWidth: 800 }}>
          <thead>
            <tr>
              <th style={th}>feature</th>
              <th style={th}>value</th>
              <th style={th}>z (90d)</th>
              <th style={th}>bucket</th>
              <th style={th}>Δz / 1h</th>
              <th style={th}>signal</th>
              <th style={th}>expected_z context</th>
            </tr>
          </thead>
          <tbody>
            {features.length === 0 && (
              <tr><td colSpan={7} style={{ ...td, color: "#666" }}>(no regime snapshot yet)</td></tr>
            )}
            {features.map((f) => {
              const zColor = f.z == null ? "#888" : Math.abs(f.z) >= 2 ? "#e66" : Math.abs(f.z) >= 1 ? "#fc6" : "#6c6";
              const exp = f.expected_z;
              const expHasNumber =
                (exp?.status === "valid" || exp?.status === "approx") &&
                exp?.mu != null && exp?.sigma != null;
              const relaxLabel: Record<string, string> = {
                table: "table",
                exact: `${exp?.context.event_type} J${exp?.context.days_bucket} ${exp?.context.tod_bucket}`,
                event_days: `${exp?.context.event_type} J${exp?.context.days_bucket}`,
                event: `${exp?.context.event_type}`,
                unconditional: "90d all",
                cold_start: "cold start",
              };
              const ctx = expHasNumber && exp != null
                ? (relaxLabel[exp.relaxation ?? "unconditional"] ?? "90d all")
                : "—";
              const expText = expHasNumber && exp != null
                ? `${_formatSigned(exp.mu)} ± ${(exp.sigma ?? 0).toFixed(2)} (${ctx}, n=${exp.n_obs})`
                : "—";
              const expColor = exp?.status === "valid" ? "#aaa"
                : exp?.status === "approx" ? "#888"
                : "#666";
              const expTitle = exp?.status === "valid"
                ? `n=${exp?.n_obs} on context ${ctx}`
                : exp?.status === "approx"
                ? `relaxed context (${ctx}), n=${exp?.n_obs} (< 20 needed for "valid")`
                : `no history (n=${exp?.n_obs ?? 0})`;
              return (
                <tr key={f.name} data-testid={`feature-row-${f.name}`}>
                  <td style={{ ...td, color: "#aaa", fontWeight: 500 }}>{f.name}</td>
                  <td style={td}>{f.value != null ? f.value.toFixed(2) : "—"}</td>
                  <td style={{ ...td, color: zColor, fontWeight: 600 }}>
                    {f.z != null
                      ? `${f.z.toFixed(2)}${f.pct != null ? ` (${f.pct}%)` : ""}`
                      : "—"}
                  </td>
                  <td style={td}>
                    {f.bucket
                      ? <Badge label={f.bucket} palette={BUCKET_BG[f.bucket] ?? BUCKET_BG["0"]!} />
                      : "—"}
                  </td>
                  <td style={{ ...td, ..._deltaZStyle(f.delta_z_1h) }}>
                    {_formatSigned(f.delta_z_1h)}
                  </td>
                  <td style={td}>
                    {f.signal
                      ? <Badge label={f.signal} palette={SIGNAL_BG[f.signal] ?? SIGNAL_BG.noise!} />
                      : "—"}
                  </td>
                  <td style={{ ...td, color: expColor }} title={expTitle}>{expText}</td>
                </tr>
              );
            })}
          </tbody>
          {synth && (
            <tfoot>
              <tr>
                <td colSpan={7} data-testid="synthesis-row" style={{
                  padding: "10px 10px 4px", borderTop: "1px solid #333",
                  fontSize: 14, fontFamily: "Consolas, monospace", color: "#aaa",
                }}>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center" }}>
                    <span style={{ color: "#666" }}>joint:</span>
                    <strong style={{ color: "#cde", fontWeight: 600 }}>{synth.joint_pattern ?? "—"}</strong>
                    <span style={{ color: "#666" }}>· regime:</span>
                    <strong style={{ color: "#cde" }}>{synth.regime?.name ?? "—"}</strong>
                    <span style={{ color: "#666" }}>· dominant:</span>
                    <strong style={{ color: "#cde" }}>{synth.dominant ?? "—"}</strong>
                    <span style={{ color: "#666" }}>· vs_expected:</span>
                    <strong style={{
                      color: synth.vs_expected?.label === "underpriced" ? "#6c6"
                           : synth.vs_expected?.label === "overpriced" ? "#e66" : "#aaa",
                    }}>
                      {synth.vs_expected
                        ? `${_formatSigned(synth.vs_expected.delta_sigma)}σ ${synth.vs_expected.label}`
                        : "—"}
                    </strong>
                  </div>
                </td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </section>
  );
}
