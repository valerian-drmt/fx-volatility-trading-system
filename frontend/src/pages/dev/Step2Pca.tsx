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
import { useEffect, useMemo, useRef, useState } from "react";
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
  // ── LIVE state ──────────────────────────────────────────────────────────
  // Updated by the polls every 2 s. These flow into refs so the parent can
  // commit them to the DISPLAY state at the next cycle-finished transition,
  // giving the UI atomic refresh at end-of-cycle instead of streaming
  // mid-cycle as each DB write lands.
  const [liveState, setLiveState] = useState<PcaState | null>(null);
  const [liveSurfaceGrid, setLiveSurfaceGrid] = useState<Record<string, SurfaceTenorMap>>({});
  const [liveSurfaceTimestamp, setLiveSurfaceTimestamp] = useState<string | null>(null);
  const [liveFeaturesPayload, setLiveFeaturesPayload] = useState<FeaturesPayload | null>(null);
  const [liveUpcomingEvents, setLiveUpcomingEvents] = useState<UpcomingEvent[]>([]);
  const [eventsHorizonDays, setEventsHorizonDays] = useState<number>(7);

  // ── DISPLAY state ───────────────────────────────────────────────────────
  // Frozen during the work phase ; swapped to the latest LIVE values at the
  // moment cycleFinished flips false → true. Initial render copies LIVE
  // through immediately so the first paint isn't blank.
  const [state, setState] = useState<PcaState | null>(null);
  const [surfaceGrid, setSurfaceGrid] = useState<Record<string, SurfaceTenorMap>>({});
  const [featuresPayload, setFeaturesPayload] = useState<FeaturesPayload | null>(null);
  const [upcomingEvents, setUpcomingEvents] = useState<UpcomingEvent[]>([]);

  // ── Cycle progress (lifted to parent so timer + freeze share it) ───────
  const [progress, setProgress] = useState<{
    cycleStartedAt: string | null;
    stage: string | null; task: string | null; completed: string[];
  }>({ cycleStartedAt: null, stage: null, task: null, completed: [] });
  useEffect(() => {
    const load = () => {
      fetch("/api/v1/dev/cycle-progress")
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((j) => setProgress({
          cycleStartedAt: j.cycle_started_at ?? null,
          stage: j.stage ?? null,
          task: j.task ?? null,
          completed: Array.isArray(j.completed) ? j.completed : [],
        }))
        .catch(() => { /* keep last good */ });
    };
    load();
    const id = window.setInterval(load, 1_000);
    return () => window.clearInterval(id);
  }, []);
  const cycleFinished = !progress.stage && progress.completed.length > 0;

  // Atomic refresh on the cycle BOUNDARY (timer hits 0 / 180 s, new cycle
  // starts). The trigger is ``cycle_started_at`` changing to a fresh value.
  // Each display slot ALSO has its own "is empty" check : on a page refresh
  // the cycle anchor lands instantly, but the live polls return staggered
  // (over 2 s). We can't rely on a single ``firstLoad`` flag — each slot
  // gets populated independently the moment its first live response is in.
  const lastCycleStartRef = useRef<string | null>(null);
  useEffect(() => {
    const newCycle =
      progress.cycleStartedAt != null &&
      progress.cycleStartedAt !== lastCycleStartRef.current;
    if (newCycle || (state === null && liveState !== null)) {
      if (liveState !== null) setState(liveState);
    }
    if (newCycle || (Object.keys(surfaceGrid).length === 0 && Object.keys(liveSurfaceGrid).length > 0)) {
      if (Object.keys(liveSurfaceGrid).length > 0) setSurfaceGrid(liveSurfaceGrid);
    }
    if (newCycle || (featuresPayload === null && liveFeaturesPayload !== null)) {
      if (liveFeaturesPayload !== null) setFeaturesPayload(liveFeaturesPayload);
    }
    if (newCycle || (upcomingEvents.length === 0 && liveUpcomingEvents.length > 0)) {
      if (liveUpcomingEvents.length > 0) setUpcomingEvents(liveUpcomingEvents);
    }
    if (newCycle) {
      lastCycleStartRef.current = progress.cycleStartedAt;
    }
  }, [
    progress.cycleStartedAt, liveState, liveSurfaceGrid,
    liveFeaturesPayload, liveUpcomingEvents,
    state, surfaceGrid, featuresPayload, upcomingEvents,
  ]);

  // All polling intervals : 2 s. The vol-engine cycle is 180 s ; with 2 s
  // polling we detect a fresh write within ≤ 2 s of it landing.
  // CRITICAL : every catch handler must keep the LAST GOOD STATE — never
  // clear to null/[]/{} on a transient fetch failure or 404. Showing data
  // from the previous cycle is always preferable to a blank panel.
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch("/api/v1/signals/pca/state");
        if (!r.ok) return;
        setLiveState(await r.json());
      } catch { /* keep last good */ }
    };
    void load();
    const id = window.setInterval(load, 2_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const load = () => {
      fetchVolSurface("EURUSD")
        .then((r) => {
          const r2 = r as unknown as { surface?: Record<string, SurfaceTenorMap>; timestamp?: string };
          const raw = r2.surface ?? {};
          const tenors: Record<string, SurfaceTenorMap> = {};
          for (const [k, v] of Object.entries(raw)) {
            if (!k.startsWith("_") && v && typeof v === "object") tenors[k] = v;
          }
          if (Object.keys(tenors).length > 0) setLiveSurfaceGrid(tenors);
          if (r2.timestamp) setLiveSurfaceTimestamp(r2.timestamp);
        })
        .catch(() => { /* keep last good */ });
    };
    load();
    const id = window.setInterval(load, 2_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const load = () => {
      fetch("/api/v1/regime/features")
        .then((r) => r.ok ? r.json() : Promise.reject(r.status))
        .then((j: FeaturesPayload) => setLiveFeaturesPayload(j))
        .catch(() => { /* keep last good */ });
      fetch("/api/v1/regime/events?n=50")
        .then((r) => r.ok ? r.json() : Promise.reject(r.status))
        .then((j: UpcomingEvent[]) => {
          if (Array.isArray(j) && j.length > 0) setLiveUpcomingEvents(j);
        })
        .catch(() => { /* keep last good */ });
    };
    load();
    const id = window.setInterval(load, 2_000);
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

  // Always render the panel ; missing data falls back to "—" or empty cells.
  // The PCA state may temporarily be null (initial fetch) or in bootstrap (no
  // active model yet) — in both cases we still want the surface, features
  // panel and timer to be visible with whatever the DB last persisted.
  const ve = state?.variance_explained;
  const cumLow = ve ? ve.cumulative < 0.85 : false;

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
      {/* ── L1 : 3 columns of equal height : Cycle | (Features Live ▸ PCA variance) | Upcoming events
            Cycle and the middle column take their natural minimum width ;
            Upcoming events fills whatever horizontal space is left. ── */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "auto 800px minmax(320px, 1fr)",
        gap: 12, alignItems: "stretch",
      }}>
        <CycleTimerPanel
          volSurfaceTs={liveSurfaceTimestamp}
          regimeSnapshotTs={liveFeaturesPayload?.timestamp ?? null}
          pcaSignalTs={liveState?.timestamp ?? null}
          progress={progress}
          cycleFinished={cycleFinished}
        />

        {/* C2 : Features Live (top) + PCA variance & stability (bottom) stacked */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <FeaturesLivePanel payload={featuresPayload} />
          <section style={{ ...panelStyle, minWidth: 800 }}>
            <Header>PCA — variance &amp; stability</Header>
            <div style={{ padding: 0, fontFamily: "Consolas, monospace", display: "flex", flexDirection: "column", gap: 8 }}>
              {cumLow && (
                <div style={{ color: "#fc6", fontSize: 11, padding: "8px 12px 0" }}>
                  ✗ below 85% noise floor — all PCs blocked (low_total_variance)
                </div>
              )}
              {ve ? (
                <table style={{
                  borderCollapse: "collapse", fontSize: 13,
                  // Match Features Live above : same minimum width, cells
                  // distributed evenly across the table area.
                  width: "100%", minWidth: 800, tableLayout: "fixed",
                }}>
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
                        const ok = state?.loadings_stable?.[p];
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
              ) : (
                <div style={{ color: "#666", fontSize: 12, padding: "8px 12px" }}>(no PCA model active yet)</div>
              )}
            </div>
          </section>
        </div>

        {/* C3 : Upcoming events */}
        <section style={{ ...panelStyle, display: "flex", flexDirection: "column" }}>
          <div style={{
            padding: "5px 12px", background: "#1a1a1a", borderBottom: "1px solid #333",
            color: "#7af", fontSize: 11, fontWeight: 600, letterSpacing: 1,
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <span>Upcoming events</span>
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
          <div style={{ padding: 12, overflowX: "auto", overflowY: "auto", flex: 1, minHeight: 0 }}>
            <UpcomingEventsTable events={upcomingEvents} horizonDays={eventsHorizonDays} />
          </div>
        </section>
      </div>

      {/* ── L2 : 3D surface | (3 rows × 2 cols of PC cells) — surface height = grid height ── */}
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12,
        alignItems: "stretch",
      }}>
        {/* Col 1 — 3D vol surface : auto-sizes to match the right-side 2x3 grid */}
        <section style={{ ...panelStyle, display: "flex", flexDirection: "column" }}>
          <Header>Live vol surface — tenor × delta × iv (drag to rotate)</Header>
          <div style={{ padding: 8, flex: 1, minHeight: 0, display: "flex" }}>
            {hasSurface ? (
              <div style={{ flex: 1, minHeight: 640 }}>
                <Plot3DSurface
                  xLabels={TENORS}
                  yLabels={DELTAS}
                  z={surfaceZ}
                  height={640}
                  refine={6}
                />
              </div>
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
              sig={state?.signals?.[pc]}
            />,
            <PcHeatCell
              key={`${pc}-heat`} pcKey={pc}
              grid={state?.loadings_grid?.[idx] ?? null}
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
  const pct = (_normalCdf(z) * 100).toFixed(1);
  return (
    <section style={panelStyle}>
      <Header>{PC_NAMES[pcKey]} — z-score</Header>
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontSize: 40, fontWeight: 700, color: labelColor }}>
            {z >= 0 ? "+" : ""}{z.toFixed(2)}
          </span>
          <span style={{ fontSize: 13, color: "#888" }}>z-score ({pct}%)</span>
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

        {/* Recommended structure — always shown, greyed if not actionable. Hidden for PC1. */}
        {sig.recommended_structure && pcKey !== "pc1" && (
          <div style={{
            fontSize: 12,
            color: sig.actionable ? "#7af" : "#555",
            textDecoration: sig.actionable ? "none" : "line-through",
          }}>
            structure: <code>{sig.recommended_structure}</code>
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


// Standard-normal CDF Φ(z) via Abramowitz & Stegun 7.1.26 erf approximation.
// Used to display the percentile next to a z-score (e.g. z=+1.42 → 92.2%).
function _normalCdf(z: number): number {
  const sign = z < 0 ? -1 : 1;
  const x = Math.abs(z) / Math.SQRT2;
  // erf approximation, max error ≈ 1.5e-7
  const t = 1 / (1 + 0.3275911 * x);
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429;
  const erf = 1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return 0.5 * (1 + sign * erf);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      <path d={tailPath(tailLeft, -1.5)} fill="rgba(122,170,255,0.25)" stroke="none" />
      <path d={tailPath(tailRight, 1.5)} fill="rgba(122,170,255,0.25)" stroke="none" />
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
      {/* Vertical line at z — full height from top to baseline */}
      <line x1={zX} y1={4} x2={zX} y2={H - 8} stroke={color} strokeWidth={1.8} shapeRendering="crispEdges" />
      {/* Marker dot at the curve point */}
      <circle cx={zX} cy={zY} r={4.5} fill={color} />
      {/* z label */}
      <text
        x={zX} y={Math.max(zY - 8, 16)} fontSize={14} fill={color} textAnchor="middle" fontWeight={700}
      >
        z={z >= 0 ? "+" : ""}{z.toFixed(2)} ({(_normalCdf(z) * 100).toFixed(1)}%)
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

// ─────────────────────────────────────────────────────────────────────────
// Cycle timer — filling-circle countdown synced to the real vol-engine cycle.
// The vol-engine has a hard 180 s cadence (work-then-sleep-to-deadline). It
// writes ``cycle_started_at`` to Redis at the start of every cycle ; we
// anchor the ring on that so it fills cleanly 0 → 180 s, regardless of when
// the actual DB write within the cycle landed.
// ─────────────────────────────────────────────────────────────────────────

const CYCLE_S = 180;

interface CycleProgress {
  cycleStartedAt: string | null;
  stage: string | null;
  task: string | null;
  completed: string[];
}

function CycleTimerPanel({
  volSurfaceTs, regimeSnapshotTs, pcaSignalTs, progress, cycleFinished,
}: {
  volSurfaceTs: string | null;
  regimeSnapshotTs: string | null;
  pcaSignalTs: string | null;
  progress: CycleProgress;
  cycleFinished: boolean;
}): JSX.Element {
  const [now, setNow] = useState<number>(() => Date.now());
  // Track the previously-seen DB write timestamp — used to flash the "Saved"
  // pulse when fresh data lands in the DB. (Independent of the ring anchor.)
  const lastWriteRef = useRef<number | null>(null);
  const [justSavedAt, setJustSavedAt] = useState<number | null>(null);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, []);

  const writeTsCandidates = [volSurfaceTs, regimeSnapshotTs, pcaSignalTs]
    .map((s) => (s ? new Date(s).getTime() : NaN))
    .filter((t) => Number.isFinite(t)) as number[];
  const lastWriteMs = writeTsCandidates.length ? Math.max(...writeTsCandidates) : null;

  useEffect(() => {
    if (lastWriteMs == null) return;
    if (lastWriteRef.current != null && lastWriteMs > lastWriteRef.current) {
      setJustSavedAt(Date.now());
    }
    lastWriteRef.current = lastWriteMs;
  }, [lastWriteMs]);
  const recentlySaved = justSavedAt != null && (now - justSavedAt) < 10_000;

  // 5 independent pipelines, each with named (stage, task) keys. The vol-engine
  // publishes its current (stage, task) + the list of completed pairs to the
  // Redis hash ``cycle_progress:vol_engine`` ; the dev panel polls
  // /api/v1/dev/cycle-progress and uses real progress to drive the bullets.
  type Pipeline = {
    label: string;
    stage: string;
    tasks: { key: string; label: string }[];
  };
  const pipelines: Pipeline[] = [
    { label: "Vol Surface", stage: "vol_surface", tasks: [
      { key: "ib_chain_fetch", label: "IB chain fetch" },
      { key: "svi_per_tenor",  label: "SVI per tenor" },
      { key: "pchip_smile",    label: "PCHIP smile" },
      { key: "ssvi_surface",   label: "SSVI surface" },
    ]},
    { label: "Regime Features", stage: "regime_features", tasks: [
      { key: "z_score",       label: "z-score (90d)" },
      { key: "bucket_signal", label: "bucket / signal" },
      { key: "joint_pattern", label: "joint pattern" },
      { key: "regime_lookup", label: "regime lookup" },
    ]},
    { label: "PCA Projection", stage: "pca_projection", tasks: [
      { key: "read_model",  label: "read active model" },
      { key: "project",     label: "project surface" },
      { key: "gen_z_label", label: "gen z + label" },
      { key: "coherence",   label: "coherence check" },
    ]},
    { label: "Publish & Persist", stage: "publish", tasks: [
      { key: "redis_set", label: "Redis SET surface" },
      { key: "pubsub",    label: "pub/sub channels" },
      { key: "db_events", label: "publish_db_event × N" },
      { key: "heartbeat", label: "heartbeat" },
    ]},
  ];
  const STAGE_ORDER = pipelines.map((p) => p.stage);

  // Ring anchor : real cycle start. The countdown is exact (engine's deadline
  // is fixed 180 s, frontend ring fills 0 → 100 % over that window).
  const cycleStartMs = progress.cycleStartedAt
    ? new Date(progress.cycleStartedAt).getTime()
    : null;
  const elapsedS = cycleStartMs ? Math.max(0, (now - cycleStartMs) / 1000) : 0;
  const phase = Math.min(elapsedS / CYCLE_S, 1);
  const remainingS = Math.max(0, CYCLE_S - elapsedS);

  const completedSet = new Set(progress.completed);
  const activeStageIdx = progress.stage ? STAGE_ORDER.indexOf(progress.stage) : -1;

  function stepColor(p: Pipeline, idx: number): { arrow: string; label: string; done: boolean } {
    if (cycleFinished) return { arrow: "#6c6", label: "#aaa", done: true };
    if (recentlySaved && p.stage === "publish") return { arrow: "#6c6", label: "#aaa", done: true };
    // Stage strictly before the active one : done.
    if (activeStageIdx > -1 && idx < activeStageIdx) return { arrow: "#6c6", label: "#aaa", done: true };
    if (activeStageIdx === idx) return { arrow: "#fc6", label: "#ddd", done: false };
    return { arrow: "#444", label: "#666", done: false };
  }

  function taskBullet(p: Pipeline, taskIdx: number): { mark: string; color: string } {
    const t = p.tasks[taskIdx];
    if (!t) return { mark: "○", color: "#444" };
    const key = `${p.stage}:${t.key}`;
    if (cycleFinished) return { mark: "●", color: "#6c6" };
    if (completedSet.has(key)) return { mark: "●", color: "#6c6" };          // done
    if (progress.stage === p.stage && progress.task === t.key) return { mark: "●", color: "#fc6" }; // active
    return { mark: "○", color: "#444" };                                   // pending
  }

  // SVG geometry — circle stroke offset based on phase (0 -> empty, 1 -> full).
  const SIZE = 132;
  const R = 54;
  const CIRC = 2 * Math.PI * R;
  const dashOffset = CIRC * (1 - phase);
  // Ring color follows task state, not elapsed time :
  //   green = cycle finished, all writes saved
  //   red   = cycle running long (> 20 % over budget) — IB stalled or compute slow
  //   amber = work in progress
  const phaseColor =
    cycleFinished       ? "#6c6"
    : elapsedS > CYCLE_S * 1.2 ? "#e66"
    : "#fc6";

  return (
    <section style={panelStyle}>
      <Header>Cycle · 3 min</Header>
      <div style={{
        padding: 12, display: "flex", flexDirection: "row", alignItems: "flex-start",
        gap: 14, fontFamily: "Consolas, monospace", minWidth: 380,
      }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flexShrink: 0 }}>
          <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
            {/* base ring */}
            <circle cx={SIZE / 2} cy={SIZE / 2} r={R}
              fill="none" stroke="#222" strokeWidth={8} />
            {/* progress arc — rotated -90° so 0 starts at the top */}
            <circle cx={SIZE / 2} cy={SIZE / 2} r={R}
              fill="none" stroke={phaseColor} strokeWidth={8}
              strokeDasharray={CIRC.toFixed(2)} strokeDashoffset={dashOffset.toFixed(2)}
              strokeLinecap="round"
              transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
              style={{ transition: "stroke-dashoffset 0.95s linear, stroke 0.4s linear" }}
            />
            <text x={SIZE / 2} y={SIZE / 2 - 2} textAnchor="middle"
              fill="#ddd" fontSize={26} fontWeight={700}>
              {Math.ceil(remainingS)}s
            </text>
            <text x={SIZE / 2} y={SIZE / 2 + 16} textAnchor="middle"
              fill="#666" fontSize={10}>
              next write
            </text>
          </svg>
        </div>

        {/* Pipelines : 5 steps shown as colored arrows, each with its tasks as bullets */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 11, flex: 1, minWidth: 200 }}>
          {pipelines.map((p, idx) => {
            const sc = stepColor(p, idx);
            return (
              <div key={p.label}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ color: sc.arrow, fontWeight: 700, fontSize: 13 }}>▶</span>
                  <span style={{ color: sc.label, fontWeight: 600 }}>{p.label}</span>
                </div>
                <ul style={{ listStyle: "none", padding: "2px 0 2px 22px", margin: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                  {p.tasks.map((task, i) => {
                    const tb = taskBullet(p, i);
                    return (
                      <li key={task.key} style={{ display: "flex", alignItems: "center", gap: 6, color: "#888", fontSize: 10 }}>
                        <span style={{ color: tb.color, width: 8, textAlign: "center" }}>{tb.mark}</span>
                        <span>{task.label}</span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
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
    return h > 0 ? `${d}d ${h}h` : `${d}d`;
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
        (no event in the next {horizonDays} days)
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
          <th style={th}>date / time</th>
          <th style={th}>country</th>
          <th style={th}>impact</th>
          <th style={th}>in</th>
          <th style={th}>content</th>
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
