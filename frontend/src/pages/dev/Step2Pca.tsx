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
import { fetchSmile, fetchTermStructure } from "../../api/endpoints";
import { SmileChart, type SmilePoint } from "../../components/charts/SmileChart";
import { TermStructureChart, type TermPoint } from "../../components/charts/TermStructureChart";

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
interface ModelInfo {
  active: boolean;
  version: string | null;
  available_hourly_snapshots: number;
  min_obs_for_refit: number;
}
interface RegimeState {
  regime?: string;
  gate_open?: boolean;
}

const PC_NAMES: Record<string, string> = {
  pc1: "PC1 — level (vol overall)",
  pc2: "PC2 — slope (term structure)",
  pc3: "PC3 — smile (skew/convexity)",
};

const REASON_COLOR: Record<string, string> = {
  variance: "#fc6",          // yellow — model coverage issue
  stability: "#f80",         // orange — loadings drift
  magnitude: "#7af",         // blue — signal too weak (informational)
  persistence: "#a7f",       // purple — signal not stable in time
  n_obs: "#e66",             // red — fit not enough data
  other: "#888",
};

const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"];
const DELTAS = ["10dp", "25dp", "atm", "25dc", "10dc"];

const SCENARIOS = [
  { value: "", label: "live (active model)" },
  { value: "actionable_pc1_cheap", label: "actionable_pc1_cheap" },
  { value: "actionable_pc2_expensive", label: "actionable_pc2_expensive" },
  { value: "blocked_low_variance", label: "blocked_low_variance" },
  { value: "blocked_n_obs", label: "blocked_n_obs" },
  { value: "stale_data", label: "stale_data" },
];

function formatAge(ts: string | undefined): string {
  if (!ts) return "—";
  const ms = Date.now() - new Date(ts).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h${m % 60 > 0 ? ` ${m % 60}m` : ""} ago`;
  return `${Math.floor(h / 24)}d ago`;
}

type SmileApiPoint = { strike: number; iv_pct: number; delta_label: string };
interface SmileLive {
  points: SmilePoint[];
  sviCurve: SmilePoint[] | null;
  fairVol: number | null;
  rv: number | null;
}

export function Step2Pca(): JSX.Element {
  const [state, setState] = useState<PcaState | null>(null);
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [regime, setRegime] = useState<RegimeState | null>(null);
  const [scenario, setScenario] = useState<string>("");
  const [smileTenor, setSmileTenor] = useState<string>("3M");
  const [termPoints, setTermPoints] = useState<TermPoint[]>([]);
  const [smile, setSmile] = useState<SmileLive | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const stateUrl = scenario
          ? `/api/v1/signals/pca/state?scenario=${encodeURIComponent(scenario)}`
          : "/api/v1/signals/pca/state";
        const [s, m, r] = await Promise.all([
          fetch(stateUrl).then((r) => r.json()),
          fetch("/api/v1/signals/pca/model").then((r) => r.json()),
          fetch("/api/v1/regime/state").then((r) => r.ok ? r.json() : null).catch(() => null),
        ]);
        setState(s); setModel(m); setRegime(r);
      } catch { /* keep last good */ }
    };
    void load();
    const id = window.setInterval(load, 5_000);
    return () => window.clearInterval(id);
  }, [scenario]);

  // Live term-structure (global, depends only on symbol).
  useEffect(() => {
    const load = () => {
      fetchTermStructure("EURUSD")
        .then((r) =>
          setTermPoints(
            r.pillars
              .filter((p) => p.sigma_atm_pct !== null)
              .map((p) => ({
                tenor: p.tenor,
                atmVol: p.sigma_atm_pct as number,
                fairVol: p.sigma_fair_pct ?? null,
                rv: p.rv_pct ?? null,
              })),
          ),
        )
        .catch(() => setTermPoints([]));
    };
    load();
    const id = window.setInterval(load, 10_000);
    return () => window.clearInterval(id);
  }, []);

  // Live smile (depends on selected tenor).
  useEffect(() => {
    const load = () => {
      fetchSmile(smileTenor, "EURUSD")
        .then((r) => {
          const apiPoints: SmileApiPoint[] = r.points;
          setSmile({
            points: apiPoints.map((p) => ({ strike: p.strike, vol: p.iv_pct })),
            sviCurve: r.svi_curve
              ? r.svi_curve.map((p) => ({ strike: p.strike, vol: p.iv_pct }))
              : null,
            fairVol: r.sigma_fair_pct ?? null,
            rv: r.rv_pct ?? null,
          });
        })
        .catch(() => setSmile(null));
    };
    load();
    const id = window.setInterval(load, 10_000);
    return () => window.clearInterval(id);
  }, [smileTenor]);

  const stale = useMemo(() => {
    if (!state?.timestamp) return false;
    const ageS = (Date.now() - new Date(state.timestamp).getTime()) / 1000;
    return Number.isFinite(ageS) && ageS > 360;
  }, [state?.timestamp]);

  if (!state) return <div style={{ color: "#666", padding: 12 }}>(loading PCA signals…)</div>;

  const ve = state.variance_explained;
  const cumLow = ve ? ve.cumulative < 0.85 : false;

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
      {/* ── scenario picker ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "#888" }}>
        <span>fixture scenario :</span>
        <select
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          style={{ background: "#0a0a0a", color: "#ddd", border: "1px solid #333", padding: "2px 6px", fontSize: 11 }}
          data-testid="pca-scenario-picker"
        >
          {SCENARIOS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <span style={{ marginLeft: 8, color: "#555" }}>
          (use <code>seed_pca_scenarios.py</code> to populate)
        </span>
      </div>

      {/* ── status header ── */}
      <section style={panelStyle}>
        <div style={{ padding: 10, display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          <div>
            <div style={{ color: "#aaa", fontSize: 11 }}>ACTIVE MODEL</div>
            <code style={{ fontSize: 12 }}>{state.model_version ?? "—"}</code>
          </div>
          <div>
            <div style={{ color: "#aaa", fontSize: 11 }}>N_OBS IN FIT</div>
            <div style={{ fontSize: 13 }}>{state.n_obs_in_fit ?? "—"}</div>
          </div>
          <div>
            <div style={{ color: "#aaa", fontSize: 11 }}>FIT AGE</div>
            <div style={{ fontSize: 13 }}>{formatAge(state.fit_timestamp)}</div>
          </div>
          <div>
            <div style={{ color: "#aaa", fontSize: 11 }}>LAST CYCLE</div>
            <div style={{ fontSize: 13, color: stale ? "#fc6" : "#ddd" }}>
              {formatAge(state.timestamp)}
            </div>
          </div>
          {regime && (
            <div>
              <div style={{ color: "#aaa", fontSize: 11 }}>REGIME</div>
              <span style={{
                padding: "2px 6px", borderRadius: 3, fontSize: 11, fontWeight: 600,
                background: regime.gate_open ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)",
                color: regime.gate_open ? "#6c6" : "#e66",
              }}>
                {regime.regime ?? "?"} {regime.gate_open ? "✓ open" : "✗ closed"}
              </span>
            </div>
          )}
          {model && !scenario && (
            <div style={{ marginLeft: "auto", fontSize: 11, color: "#888" }}>
              hourly snapshots : <strong style={{ color: "#ddd" }}>{model.available_hourly_snapshots}</strong>
              <span style={{ marginLeft: 8, color: "#666" }}>(refit auto every hour)</span>
            </div>
          )}
        </div>
        {state.coherence && (
          <div style={{ padding: "0 10px 8px", fontSize: 11 }}>
            <span style={{
              padding: "2px 8px", borderRadius: 3, fontWeight: 600,
              background: state.coherence.all_coherent ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)",
              color: state.coherence.all_coherent ? "#6c6" : "#e66",
            }} data-testid="pca-coherence">
              {state.coherence.all_coherent
                ? "SIGNALS COHERENT"
                : `CONTRADICTIONS (${state.coherence.contradictions.map(([a, b]) => `${a}↔${b}`).join(", ")})`}
            </span>
          </div>
        )}
      </section>

      {/* ── stale banner ── */}
      {stale && state.timestamp && (
        <div style={{
          padding: 8, background: "#3a2a00", border: "1px solid #b88500",
          color: "#fc6", fontSize: 11, borderRadius: 3,
        }}>
          ⚠ data stale — last signal {formatAge(state.timestamp)}. Vol-engine may be down or markets closed.
        </div>
      )}

      {/* ── 3 PC cards ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, opacity: stale ? 0.5 : 1 }}>
        {(["pc1", "pc2", "pc3"] as const).map((pc) => (
          <PcCard
            key={pc} pcKey={pc}
            sig={state.signals[pc]}
            variance={ve?.[pc] ?? 0}
            stable={state.loadings_stable?.[pc] ?? true}
          />
        ))}
      </div>

      {/* ── Live term structure + smile (read from Redis-cached surface) ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <section style={panelStyle}>
          <Header>Live term structure (ATM σ per tenor)</Header>
          <div style={{ padding: 8, minHeight: 220 }}>
            <TermStructureChart points={termPoints} />
          </div>
        </section>
        <section style={panelStyle}>
          <div style={{
            padding: "5px 12px", background: "#1a1a1a", borderBottom: "1px solid #333",
            color: "#7af", fontSize: 11, fontWeight: 600, letterSpacing: 1,
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <span>Live smile — tenor</span>
            <select
              value={smileTenor}
              onChange={(e) => setSmileTenor(e.target.value)}
              style={{ background: "#0a0a0a", color: "#ddd", border: "1px solid #333", padding: "1px 4px", fontSize: 11 }}
            >
              {TENORS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div style={{ padding: 8, minHeight: 220 }}>
            {smile && smile.points.length > 0 ? (
              <SmileChart
                points={smile.points} tenor={smileTenor}
                fairVol={smile.fairVol} rv={smile.rv} sviCurve={smile.sviCurve}
              />
            ) : (
              <div style={{ color: "#666", fontSize: 12, padding: 12 }}>
                no smile data for {smileTenor} (vol-engine down or markets closed)
              </div>
            )}
          </div>
        </section>
      </div>

      {/* ── diagnostics ── */}
      {ve && (
        <section style={panelStyle}>
          <Header>Diagnostics — variance + stability + cumulative gate</Header>
          <div style={{ padding: 12, fontSize: 12, fontFamily: "Consolas, monospace" }}>
            <div>
              cumul (PC1+2+3) : <strong style={{ color: cumLow ? "#fc6" : "#6c6" }}>{(ve.cumulative * 100).toFixed(1)}%</strong>
              {cumLow && (
                <span style={{ color: "#fc6", marginLeft: 8 }}>
                  ✗ below 85% noise floor — all PCs blocked (low_total_variance)
                </span>
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

      {/* ── vega loadings heatmap ── */}
      {state.loadings_grid && state.loadings_grid.length >= 3 && (
        <section style={panelStyle}>
          <Header>Loadings heatmap — (tenor × delta) per PC</Header>
          <div style={{ padding: 12, display: "flex", gap: 16, flexWrap: "wrap" }}>
            {(["pc1", "pc2", "pc3"] as const).map((pc, idx) => {
              const grid = state.loadings_grid?.[idx];
              if (!grid) return null;
              return (
                <LoadingsHeatmap key={pc} title={PC_NAMES[pc] ?? pc} grid={grid} />
              );
            })}
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
  const reasonColor = sig.reason_category ? (REASON_COLOR[sig.reason_category] ?? "#888") : "#888";
  const z = sig.z_score;
  const barWidth = Math.min(Math.abs(z) / 3.0, 1.0) * 100;
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
          <div style={{ position: "absolute", top: -2, left: "50%", width: 1, height: 10, background: "#666" }} />
        </div>

        {/* Gaussian + z marker — visualises where the current z sits in N(0,1) */}
        <GaussianMarker z={z} color={labelColor} />
        <div style={{
          padding: "4px 10px", background: labelColor, color: "#000",
          fontWeight: 700, borderRadius: 3, fontSize: 14, alignSelf: "flex-start",
        }}>{sig.label}</div>

        {/* Actionable / reason — colour-coded by category */}
        <div style={{
          padding: "4px 8px",
          background: sig.actionable ? "#363" : "#222",
          borderLeft: sig.actionable ? "3px solid #6c6" : `3px solid ${reasonColor}`,
          color: sig.actionable ? "#cfc" : reasonColor,
          borderRadius: 3, fontSize: 11, fontFamily: "Consolas, monospace",
        }} data-testid={`pca-actionability-${pcKey}`}>
          {sig.actionable
            ? "✓ actionable"
            : <>✗ {sig.actionable_reason ?? "not actionable"}
                {sig.reason_category && <span style={{ marginLeft: 6, opacity: 0.7 }}>[{sig.reason_category}]</span>}
              </>}
        </div>

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
        <div style={{ borderTop: "1px solid #222", marginTop: 4, paddingTop: 6, color: "#888", fontSize: 10 }}>
          variance: {(variance * 100).toFixed(1)}% · loadings: {stable ? "stable" : "UNSTABLE"} · raw: {sig.raw_score.toFixed(3)}
        </div>
      </div>
    </section>
  );
}

function GaussianMarker({ z, color }: { z: number; color: string }): JSX.Element {
  // Standard normal density φ(x) = (1/√(2π)) exp(-x²/2). We sample on
  // [-3.5, +3.5] which covers 99.95% of the mass.
  const W = 200, H = 60;
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
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: 60, display: "block" }}>
      {/* Bell curve fill */}
      <path d={path} fill="rgba(122,170,255,0.10)" stroke="none" />
      {/* Tails (|z| > 1.5) shaded for emphasis */}
      <path d={tailPath(tailLeft, xMax)} fill="rgba(122,170,255,0.25)" stroke="none" />
      <path d={tailPath(tailRight, xMin)} fill="rgba(122,170,255,0.25)" stroke="none" />
      {/* Bell curve outline */}
      <polyline
        points={points.map((p) => `${xScale(p.x).toFixed(1)},${yScale(p.y).toFixed(1)}`).join(" ")}
        fill="none" stroke="#7af" strokeWidth={0.8}
      />
      {/* x-axis baseline */}
      <line x1={0} y1={H - 4} x2={W} y2={H - 4} stroke="#333" strokeWidth={0.5} />
      {/* x ticks at -3, -1.5, 0, 1.5, 3 */}
      {[-3, -1.5, 0, 1.5, 3].map((t) => (
        <g key={t}>
          <line x1={xScale(t)} y1={H - 4} x2={xScale(t)} y2={H - 1} stroke="#666" strokeWidth={0.5} />
          <text x={xScale(t)} y={H} fontSize={6} fill="#666" textAnchor="middle">{t}</text>
        </g>
      ))}
      {/* Vertical line at z */}
      <line x1={zX} y1={zY} x2={zX} y2={H - 4} stroke={color} strokeWidth={1} />
      {/* Marker dot */}
      <circle cx={zX} cy={zY} r={2.4} fill={color} />
      {/* z label */}
      <text
        x={zX} y={Math.max(zY - 4, 8)} fontSize={7} fill={color} textAnchor="middle" fontWeight={600}
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
      <table style={{ borderCollapse: "collapse", fontSize: 10 }}>
        <thead>
          <tr>
            <th style={{ padding: 2, color: "#666" }}></th>
            {DELTAS.map((d) => <th key={d} style={{ padding: "2px 4px", color: "#666", fontWeight: 400 }}>{d}</th>)}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, ti) => (
            <tr key={ti}>
              <td style={{ padding: "2px 4px", color: "#666" }}>{TENORS[ti] ?? ""}</td>
              {row.map((v, di) => {
                const intensity = Math.abs(v) / maxAbs;
                const bg = v >= 0
                  ? `rgba(34,197,94,${0.15 + 0.7 * intensity})`
                  : `rgba(239,68,68,${0.15 + 0.7 * intensity})`;
                return (
                  <td key={di} title={v.toFixed(3)} style={{
                    padding: "4px 6px", background: bg, color: "#fff",
                    textAlign: "center", minWidth: 36,
                    border: "1px solid #1a1a1a",
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
