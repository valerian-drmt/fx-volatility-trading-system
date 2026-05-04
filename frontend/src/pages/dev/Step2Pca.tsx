/**
 * Step 2 — PCA signal detection panel.
 *
 * Reads from /api/v1/signals/pca/{state,model} + /api/v1/regime/state. Refit
 * is performed automatically every hour by the api scheduler ; the panel is
 * read-only.
 *
 * Scenario dropdown : add ``?scenario=<name>`` to the state fetch to render
 * the seeded fixture instead of the live model. Useful in sandbox to exercise
 * each branch of the panel without waiting for live data.
 */
import { useEffect, useMemo, useState } from "react";
import { fetchVolSurface } from "../../api/endpoints";
import { Plot3DSurface } from "../../components/charts/Plot3DSurface";
import { FeaturesLivePanel, type FeaturesPayload } from "../../components/panels/FeaturesLivePanel";

interface PcSig {
  z_score: number;
  raw_score: number;
  label: "CHEAP" | "FAIR" | "EXPENSIVE";
  actionable: boolean;
  actionable_reason: string | null;
  reason_category?: string | null;
  recommended_structure: string | null;
  sub_signals?: { skew_z: number; convex_z: number } | null;
}
interface PcaState {
  state: "bootstrap" | "stable" | "unstable";
  model_version: string | null;
  timestamp?: string;
  n_obs_in_fit?: number;
  fit_timestamp?: string;
  fit_window_start?: string;
  fit_window_end?: string;
  variance_explained?: { pc1: number; pc2: number; pc3: number; cumulative: number };
  loadings_stable?: { pc1: boolean; pc2: boolean; pc3: boolean };
  loadings_grid?: number[][][];          // (3, 6, 5)
  signals: { pc1?: PcSig; pc2?: PcSig; pc3?: PcSig };
  coherence?: { all_coherent: boolean; contradictions: [string, string][] };
}
const PC_NAMES: Record<string, string> = {
  pc1: "PC1 — level (vol overall)",
  pc2: "PC2 — slope (term structure)",
  pc3: "PC3 — smile (skew/convexity)",
};

const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"];
const DELTAS = ["10dp", "25dp", "atm", "25dc", "10dc"];

// Shape returned by /api/v1/vol/surface : { tenors: { [tenor]: { [pillar]: { iv, strike } } } }
// We only consume the iv values here (decimal — convert to % for display).
type SurfaceCell = { iv?: number | null };
type SurfaceTenorMap = Partial<Record<string, SurfaceCell>>;

interface UpcomingEvent {
  id: number;
  event_type: string;
  impact: string;
  region: string;
  scheduled_at: string;
  description: string | null;
  source: string | null;
}

export function Step2Pca(): JSX.Element {
  const [state, setState] = useState<PcaState | null>(null);
  const [surfaceGrid, setSurfaceGrid] = useState<Record<string, SurfaceTenorMap>>({});
  const [featuresPayload, setFeaturesPayload] = useState<FeaturesPayload | null>(null);
  const [upcomingEvents, setUpcomingEvents] = useState<UpcomingEvent[]>([]);
  const [eventsHorizonDays, setEventsHorizonDays] = useState<number>(7);

  useEffect(() => {
    const load = async () => {
      try {
        const s = await fetch("/api/v1/signals/pca/state").then((r) => r.json());
        setState(s);
      } catch { /* keep last good */ }
    };
    void load();
    const id = window.setInterval(load, 5_000);
    return () => window.clearInterval(id);
  }, []);

  // Live full surface (tenor × delta grid). Polled every 10 s ; powers the
  // 3D plot below. Markets-closed sandbox returns 404 → we keep an empty grid.
  useEffect(() => {
    const load = () => {
      fetchVolSurface("EURUSD")
        .then((r) => {
          // Server response : { symbol, timestamp, surface: {tenor: {pillar:{iv,strike}}} }
          // Engine aggregates (keys starting with "_") are stripped.
          const raw = (r as unknown as { surface?: Record<string, SurfaceTenorMap> }).surface ?? {};
          const tenors: Record<string, SurfaceTenorMap> = {};
          for (const [k, v] of Object.entries(raw)) {
            if (!k.startsWith("_") && v && typeof v === "object") tenors[k] = v;
          }
          setSurfaceGrid(tenors);
        })
        .catch(() => setSurfaceGrid({}));
    };
    load();
    const id = window.setInterval(load, 10_000);
    return () => window.clearInterval(id);
  }, []);

  // Live regime features (Step 1 endpoint) — feeds the Diagnostics table.
  useEffect(() => {
    const load = () => {
      fetch("/api/v1/regime/features")
        .then((r) => r.ok ? r.json() : Promise.reject(r.status))
        .then((j: FeaturesPayload) => setFeaturesPayload(j))
        .catch(() => setFeaturesPayload(null));
      fetch("/api/v1/regime/events?n=50")
        .then((r) => r.ok ? r.json() : Promise.reject(r.status))
        .then((j: UpcomingEvent[]) => setUpcomingEvents(Array.isArray(j) ? j : []))
        .catch(() => setUpcomingEvents([]));
    };
    load();
    const id = window.setInterval(load, 5_000);
    return () => window.clearInterval(id);
  }, []);

  // Build z[deltaIdx][tenorIdx] = iv % from the surface grid. Cells absent
  // from the API response stay at NaN (rendered as a hole in plotly).
  const surfaceZ = useMemo(() => {
    return DELTAS.map((d) =>
      TENORS.map((t) => {
        const cell = surfaceGrid[t]?.[d];
        const iv = cell?.iv;
        return iv != null ? iv * 100 : Number.NaN;
      }),
    );
  }, [surfaceGrid]);
  const hasSurface = surfaceZ.some((row) => row.some((v) => Number.isFinite(v)));

  if (!state) return <div style={{ color: "#666", padding: 12 }}>(loading PCA signals…)</div>;

  const ve = state.variance_explained;
  const cumLow = ve ? ve.cumulative < 0.85 : false;

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
      {/* ── top row : Diagnostics + Prochains events ───────────────────── */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) auto minmax(320px, 1fr)", gap: 12, alignItems: "stretch" }}>
        <FeaturesLivePanel payload={featuresPayload} />

        {/* Panel 2 : PC variance + loadings stability */}
        {ve && (
          <section style={panelStyle}>
            <Header>PCA — variance &amp; stability</Header>
            <div style={{ padding: 12, fontFamily: "Consolas, monospace", display: "flex", flexDirection: "column", gap: 8 }}>
              {cumLow && (
                <div style={{ color: "#fc6", fontSize: 11 }}>
                  ✗ below 85% noise floor — all PCs blocked (low_total_variance)
                </div>
              )}
              <table style={{ borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr>
                    <th style={{ padding: "4px 12px", color: "#888", textAlign: "left", fontWeight: 500 }}></th>
                    {(["pc1", "pc2", "pc3"] as const).map((p) => (
                      <th key={p} style={{ padding: "4px 18px", color: "#aaa", fontWeight: 600, textAlign: "center", borderBottom: "1px solid #333", fontSize: 11, textTransform: "uppercase" }}>
                        {p}
                      </th>
                    ))}
                    <th style={{ padding: "4px 18px", color: "#aaa", fontWeight: 600, textAlign: "center", borderBottom: "1px solid #333", borderLeft: "1px solid #333", fontSize: 11, textTransform: "uppercase" }}>
                      total
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td style={{ padding: "6px 12px", color: "#888", fontWeight: 500 }}>variance</td>
                    {(["pc1", "pc2", "pc3"] as const).map((p) => (
                      <td key={p} style={{ padding: "6px 18px", color: "#ddd", textAlign: "center" }}>
                        {((ve[p] ?? 0) * 100).toFixed(1)}%
                      </td>
                    ))}
                    <td style={{ padding: "6px 18px", textAlign: "center", color: cumLow ? "#fc6" : "#6c6", fontWeight: 700, borderLeft: "1px solid #333" }}>
                      {(ve.cumulative * 100).toFixed(1)}%
                    </td>
                  </tr>
                  <tr>
                    <td style={{ padding: "6px 12px", color: "#888", fontWeight: 500 }}>loadings stable</td>
                    {(["pc1", "pc2", "pc3"] as const).map((p) => {
                      const ok = state.loadings_stable?.[p];
                      return (
                        <td key={p} style={{ padding: "6px 18px", textAlign: "center", color: ok ? "#6c6" : "#e66", fontWeight: 600 }}>
                          {ok ? "yes" : "no"}
                        </td>
                      );
                    })}
                    <td style={{ padding: "6px 18px", textAlign: "center", color: "#888", borderLeft: "1px solid #333" }}>—</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Panel 3 : Prochains events */}
        <section style={panelStyle}>
          <div style={{
            padding: "5px 12px", background: "#1a1a1a", borderBottom: "1px solid #333",
            color: "#7af", fontSize: 11, fontWeight: 600, letterSpacing: 1,
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <span>Prochains events</span>
            <select
              value={eventsHorizonDays}
              onChange={(e) => setEventsHorizonDays(Number(e.target.value))}
              style={{ background: "#0a0a0a", color: "#ddd", border: "1px solid #333", padding: "1px 4px", fontSize: 11 }}
            >
              <option value={7}>1 week</option>
              <option value={14}>2 weeks</option>
              <option value={21}>3 weeks</option>
              <option value={30}>1 month</option>
              <option value={90}>3 months</option>
            </select>
          </div>
          <div style={{ padding: 12, overflowX: "auto" }}>
            <UpcomingEventsTable events={upcomingEvents} horizonDays={eventsHorizonDays} />
          </div>
        </section>
      </div>

      {/* ── 2 cols : 3D surface | (3 rows × 2 cols of PC cells) ──────── */}
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12,
        alignItems: "stretch",
      }}>
        {/* Col 1 — 3D vol surface */}
        <section style={panelStyle}>
          <Header>Live vol surface — tenor × delta × iv (drag to rotate)</Header>
          <div style={{ padding: 8, minHeight: 640 }}>
            {hasSurface ? (
              <Plot3DSurface
                xLabels={TENORS}
                yLabels={DELTAS}
                z={surfaceZ}
                height={640}
                refine={6}
              />
            ) : (
              <div style={{ color: "#666", fontSize: 12, padding: 12 }}>
                no surface in cache (vol-engine down or markets closed)
              </div>
            )}
          </div>
        </section>

        {/* Col 2 — 3 rows × 2 cols : per-PC z-score | loadings heatmap */}
        <div style={{
          display: "grid", gridTemplateColumns: "1fr 1fr",
          gridAutoRows: "1fr", gap: 12,
        }}>
          {(["pc1", "pc2", "pc3"] as const).flatMap((pc, idx) => [
            <PcZCell
              key={`${pc}-z`} pcKey={pc}
              sig={state.signals[pc]}
            />,
            <PcHeatCell
              key={`${pc}-heat`} pcKey={pc}
              grid={state.loadings_grid?.[idx] ?? null}
            />,
          ])}
        </div>
      </div>

    </div>
  );
}


function PcZCell({
  pcKey, sig,
}: {
  pcKey: "pc1" | "pc2" | "pc3";
  sig: PcSig | undefined;
}): JSX.Element {
  if (!sig) {
    return (
      <section style={panelStyle}>
        <Header>{PC_NAMES[pcKey]} — z-score</Header>
        <div style={{ padding: 16, color: "#666", fontSize: 12 }}>(no signal yet)</div>
      </section>
    );
  }
  const labelColor = sig.label === "CHEAP" ? "#6c6" : sig.label === "EXPENSIVE" ? "#e66" : "#aaa";
  const z = sig.z_score;
  const barWidth = Math.min(Math.abs(z) / 3.0, 1.0) * 100;
  return (
    <section style={panelStyle}>
      <Header>{PC_NAMES[pcKey]} — z-score</Header>
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontSize: 40, fontWeight: 700, color: labelColor }}>
            {z >= 0 ? "+" : ""}{z.toFixed(2)}
          </span>
          <span style={{ fontSize: 13, color: "#888" }}>z-score</span>
        </div>
        <div style={{ position: "relative", height: 6, background: "#222", borderRadius: 3 }}>
          <div style={{
            position: "absolute", top: 0, height: 6,
            left: z >= 0 ? "50%" : `${50 - barWidth/2}%`,
            width: `${barWidth/2}%`,
            background: labelColor, borderRadius: 3,
          }} />
          <div style={{ position: "absolute", top: -2, left: "50%", width: 1, height: 10, background: "#666" }} />
        </div>

        {/* Gaussian + z marker — visualises where the current z sits in N(0,1) */}
        <GaussianMarker z={z} color={labelColor} />
        <div style={{
          padding: "4px 10px", background: labelColor, color: "#000",
          fontWeight: 700, borderRadius: 3, fontSize: 14, alignSelf: "flex-start",
        }}>{sig.label}</div>

        {/* Recommended structure — always shown, greyed if not actionable */}
        {sig.recommended_structure && (
          <div style={{
            fontSize: 12,
            color: sig.actionable ? "#7af" : "#555",
            textDecoration: sig.actionable ? "none" : "line-through",
          }}>
            structure : <code>{sig.recommended_structure}</code>
          </div>
        )}

        {sig.sub_signals && (
          <div style={{
            borderTop: "1px solid #222", marginTop: 4, paddingTop: 6,
            fontSize: 10, color: "#aaa", display: "flex", gap: 12,
          }}>
            <span>skew_z: <strong style={{ color: "#ddd" }}>{sig.sub_signals.skew_z >= 0 ? "+" : ""}{sig.sub_signals.skew_z.toFixed(2)}</strong></span>
            <span>convex_z: <strong style={{ color: "#ddd" }}>{sig.sub_signals.convex_z >= 0 ? "+" : ""}{sig.sub_signals.convex_z.toFixed(2)}</strong></span>
          </div>
        )}
      </div>
    </section>
  );
}

function PcHeatCell({
  pcKey, grid,
}: {
  pcKey: "pc1" | "pc2" | "pc3";
  grid: number[][] | null;
}): JSX.Element {
  return (
    <section style={panelStyle}>
      <Header>{PC_NAMES[pcKey]} — loadings (tenor × delta)</Header>
      <div style={{ padding: 12, display: "flex", justifyContent: "center" }}>
        {grid && grid.length > 0
          ? <LoadingsHeatmap title="" grid={grid} />
          : <div style={{ color: "#666", fontSize: 12 }}>(no loadings grid yet)</div>}
      </div>
    </section>
  );
}


function GaussianMarker({ z, color }: { z: number; color: string }): JSX.Element {
  // Standard normal density φ(x) = (1/√(2π)) exp(-x²/2). We sample on
  // [-3.5, +3.5] which covers 99.95% of the mass.
  const W = 360, H = 130;
  const xMin = -3.5, xMax = 3.5;
  const N = 60;
  const xScale = (x: number) => ((x - xMin) / (xMax - xMin)) * W;

  const phi = (x: number) => Math.exp(-(x * x) / 2) / Math.sqrt(2 * Math.PI);
  // sample once at component scope ; constants → fine to recompute each render
  const points = useMemo(() => {
    const arr: { x: number; y: number }[] = [];
    for (let i = 0; i <= N; i++) {
      const x = xMin + (i / N) * (xMax - xMin);
      arr.push({ x, y: phi(x) });
    }
    return arr;
  }, []);
  const peak = phi(0);                                    // ≈ 0.3989
  const yScale = (y: number) => H - 4 - (y / peak) * (H - 8);

  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(p.x).toFixed(1)},${yScale(p.y).toFixed(1)}`)
    .join(" ")
    + ` L ${xScale(xMax)},${H - 4} L ${xScale(xMin)},${H - 4} Z`;

  // Tail shading beyond ±1.5
  const tailLeft = points.filter((p) => p.x <= -1.5);
  const tailRight = points.filter((p) => p.x >= 1.5);
  const tailPath = (tail: typeof points, edge: number) => {
    if (tail.length === 0) return "";
    const head = tail
      .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(p.x).toFixed(1)},${yScale(p.y).toFixed(1)}`)
      .join(" ");
    return head + ` L ${xScale(edge)},${H - 4} L ${xScale(tail[0]!.x)},${H - 4} Z`;
  };

  const zClamped = Math.max(xMin, Math.min(xMax, z));
  const zX = xScale(zClamped);
  const zPdf = phi(zClamped);
  const zY = yScale(zPdf);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: 130, display: "block" }}>
      {/* Bell curve fill */}
      <path d={path} fill="rgba(122,170,255,0.10)" stroke="none" />
      {/* Tails (|z| > 1.5) shaded for emphasis */}
      <path d={tailPath(tailLeft, xMax)} fill="rgba(122,170,255,0.25)" stroke="none" />
      <path d={tailPath(tailRight, xMin)} fill="rgba(122,170,255,0.25)" stroke="none" />
      {/* Bell curve outline */}
      <polyline
        points={points.map((p) => `${xScale(p.x).toFixed(1)},${yScale(p.y).toFixed(1)}`).join(" ")}
        fill="none" stroke="#7af" strokeWidth={1.4}
      />
      {/* x-axis baseline */}
      <line x1={0} y1={H - 8} x2={W} y2={H - 8} stroke="#444" strokeWidth={0.8} />
      {/* x ticks at -3, -1.5, 0, 1.5, 3 */}
      {[-3, -1.5, 0, 1.5, 3].map((t) => (
        <g key={t}>
          <line x1={xScale(t)} y1={H - 8} x2={xScale(t)} y2={H - 3} stroke="#888" strokeWidth={0.8} />
          <text x={xScale(t)} y={H - 1} fontSize={11} fill="#888" textAnchor="middle">{t}</text>
        </g>
      ))}
      {/* Vertical line at z */}
      <line x1={zX} y1={zY} x2={zX} y2={H - 8} stroke={color} strokeWidth={1.8} />
      {/* Marker dot */}
      <circle cx={zX} cy={zY} r={4.5} fill={color} />
      {/* z label */}
      <text
        x={zX} y={Math.max(zY - 8, 16)} fontSize={14} fill={color} textAnchor="middle" fontWeight={700}
      >
        z={z >= 0 ? "+" : ""}{z.toFixed(2)}
      </text>
    </svg>
  );
}

function LoadingsHeatmap({ title, grid }: { title: string; grid: number[][] }): JSX.Element {
  // grid is (6 tenors, 5 deltas)
  const flat = grid.flat();
  const maxAbs = Math.max(...flat.map((v) => Math.abs(v)), 1e-6);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontSize: 11, color: "#aaa", fontWeight: 600 }}>{title}</div>
      <table style={{ borderCollapse: "collapse", fontSize: 14, width: "100%" }}>
        <thead>
          <tr>
            <th style={{ padding: 4, color: "#888" }}></th>
            {DELTAS.map((d) => <th key={d} style={{ padding: "6px 10px", color: "#aaa", fontWeight: 500, fontSize: 13 }}>{d}</th>)}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, ti) => (
            <tr key={ti}>
              <td style={{ padding: "6px 10px", color: "#aaa", fontWeight: 500 }}>{TENORS[ti] ?? ""}</td>
              {row.map((v, di) => {
                const intensity = Math.abs(v) / maxAbs;
                const bg = v >= 0
                  ? `rgba(34,197,94,${0.15 + 0.7 * intensity})`
                  : `rgba(239,68,68,${0.15 + 0.7 * intensity})`;
                return (
                  <td key={di} title={v.toFixed(3)} style={{
                    padding: "10px 12px", background: bg, color: "#fff",
                    textAlign: "center", minWidth: 56,
                    border: "1px solid #1a1a1a", fontWeight: 600,
                  }}>
                    {v >= 0 ? "+" : ""}{v.toFixed(2)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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

const IMPACT_COLOR: Record<string, string> = {
  high: "#e66", medium: "#fc6", low: "#7af",
};

function _formatDelta(scheduledAt: string): string {
  const ms = new Date(scheduledAt).getTime() - Date.now();
  if (!Number.isFinite(ms)) return "—";
  if (ms <= 0) return "now";
  const days = ms / 86_400_000;
  if (days >= 1) {
    const d = Math.floor(days);
    const h = Math.floor((days - d) * 24);
    return h > 0 ? `${d}j ${h}h` : `${d}j`;
  }
  const hours = ms / 3_600_000;
  if (hours >= 1) {
    const h = Math.floor(hours);
    const m = Math.floor((hours - h) * 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  return `${Math.max(1, Math.floor(ms / 60_000))}m`;
}

function UpcomingEventsTable({ events, horizonDays }: { events: UpcomingEvent[]; horizonDays: number }): JSX.Element {
  const cutoff = Date.now() + horizonDays * 86_400_000;
  const filtered = events.filter((e) => new Date(e.scheduled_at).getTime() <= cutoff);
  if (!filtered.length) {
    return (
      <div style={{ color: "#666", fontSize: 12 }}>
        (aucun event dans les {horizonDays} prochains jours)
      </div>
    );
  }
  const th: React.CSSProperties = {
    padding: "6px 12px", color: "#aaa", fontWeight: 600,
    textAlign: "left", borderBottom: "1px solid #333", fontSize: 11,
    textTransform: "uppercase", letterSpacing: 0.5,
  };
  const td: React.CSSProperties = {
    padding: "6px 12px", color: "#ddd", borderBottom: "1px solid #1a1a1a",
    fontSize: 12, verticalAlign: "top",
  };
  return (
    <table style={{ borderCollapse: "collapse", width: "100%", fontFamily: "Consolas, monospace" }}>
      <thead>
        <tr>
          <th style={th}>date / heure</th>
          <th style={th}>pays</th>
          <th style={th}>impact</th>
          <th style={th}>dans</th>
          <th style={th}>contenu</th>
        </tr>
      </thead>
      <tbody>
        {filtered.map((e) => {
          const impactColor = IMPACT_COLOR[e.impact?.toLowerCase()] ?? "#888";
          const delta = _formatDelta(e.scheduled_at);
          const isImminent = (new Date(e.scheduled_at).getTime() - Date.now()) < 5 * 86_400_000;
          return (
            <tr key={e.id}>
              <td style={td}>{new Date(e.scheduled_at).toLocaleString()}</td>
              <td style={{ ...td, color: "#aaa" }}>{e.region}</td>
              <td style={{ ...td, color: impactColor, fontWeight: 600 }}>{e.impact}</td>
              <td style={{
                ...td, fontWeight: 600,
                color: isImminent && e.impact?.toLowerCase() === "high" ? "#fc6" : "#ddd",
              }}>{delta}</td>
              <td style={td}>
                <strong style={{ color: "#cde" }}>{e.event_type}</strong>
                {e.description && (
                  <span style={{ color: "#888", marginLeft: 6 }}>· {e.description}</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
